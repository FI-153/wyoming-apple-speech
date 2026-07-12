import Foundation

/// Serializes stdout writes in worker mode: partial-transcript callbacks fire on
/// recognition threads while the main loop owns the command flow.
private let workerStdoutLock = NSLock()

/// Write one worker-protocol frame (a JSON header line) to stdout.
func writeWorkerFrame(_ header: [String: Any]) {
    var data = try! JSONSerialization.data(withJSONObject: header)
    data.append(0x0A)

    workerStdoutLock.lock()
    defer { workerStdoutLock.unlock() }
    FileHandle.standardOutput.write(data)
}

/// Buffered stdin reader for the worker protocol: JSON header lines followed by
/// binary audio payloads (which may contain newline bytes, so `readLine()` can't
/// be used). Reads run one at a time from the worker loop; the class is not
/// safe for concurrent readers.
final class StdinFrameReader: @unchecked Sendable {
    private var buffer = Data()
    private let chunkSize = 65536

    /// Read one newline-terminated header line. Returns nil on EOF.
    func readHeaderLine() -> Data? {
        while true {
            if let newlineIndex = buffer.firstIndex(of: 0x0A) {
                let line = buffer.subdata(in: buffer.startIndex..<newlineIndex)
                buffer.removeSubrange(buffer.startIndex...newlineIndex)
                return line
            }
            guard fill() else {
                return buffer.isEmpty ? nil : buffer
            }
        }
    }

    /// Read exactly `count` payload bytes. Returns nil on premature EOF.
    func readExactly(_ count: Int) -> Data? {
        while buffer.count < count {
            guard fill() else { return nil }
        }
        let payload = buffer.prefix(count)
        buffer.removeFirst(count)
        return Data(payload)
    }

    /// Pull more bytes from stdin into the buffer. Returns false on EOF.
    ///
    /// Uses read(2) directly because `FileHandle.readData(ofLength:)` loops
    /// until it accumulates the full requested length, which deadlocks the
    /// framed protocol whenever a frame boundary leaves fewer bytes pending.
    private func fill() -> Bool {
        var chunk = [UInt8](repeating: 0, count: chunkSize)
        var bytesRead = 0
        repeat {
            bytesRead = chunk.withUnsafeMutableBytes { read(0, $0.baseAddress, chunkSize) }
        } while bytesRead < 0 && errno == EINTR
        guard bytesRead > 0 else { return false }
        buffer.append(contentsOf: chunk[0..<bytesRead])
        return true
    }
}

/// A command frame received on stdin in worker mode.
private struct WorkerCommand: Decodable {
    let type: String
    let language: String?
    let length: Int?
}

/// Read the next command frame off stdin without blocking the cooperative pool.
private func readCommand(_ reader: StdinFrameReader) async -> WorkerCommand? {
    await withCheckedContinuation { continuation in
        DispatchQueue.global().async {
            guard let line = reader.readHeaderLine(), !line.isEmpty else {
                continuation.resume(returning: nil)
                return
            }
            continuation.resume(
                returning: try? JSONDecoder().decode(WorkerCommand.self, from: line))
        }
    }
}

/// Read an audio payload off stdin without blocking the cooperative pool.
private func readPayload(_ reader: StdinFrameReader, length: Int) async -> Data? {
    await withCheckedContinuation { continuation in
        DispatchQueue.global().async {
            continuation.resume(returning: reader.readExactly(length))
        }
    }
}

/// Open a streaming session on the best engine for this macOS version.
private func makeStreamingSession(
    language: String,
    onPartial: @escaping @Sendable (String) -> Void
) async throws -> StreamingSTTSession {
    if #available(macOS 26, *) {
        do {
            return try await AnalyzerStreamingSession(language: language, onPartial: onPartial)
        } catch {
            fputs(
                "[apple-stt] SpeechAnalyzer session failed, falling back to "
                    + "SFSpeechRecognizer: \(error.localizedDescription)\n",
                stderr)
        }
    }
    return try SFStreamingSession(language: language, onPartial: onPartial)
}

/// Run one transcription session: consume audio frames until `stop`, streaming
/// partial transcripts out as recognition progresses, then emit the final text.
private func runSession(reader: StdinFrameReader, language: String) async {
    fputs("[apple-stt] Session start (language=\(language))\n", stderr)
    let session: StreamingSTTSession
    do {
        session = try await makeStreamingSession(language: language) { partialText in
            writeWorkerFrame(["type": "partial", "text": partialText])
        }
    } catch {
        fputs("[apple-stt] Session init failed: \(error.localizedDescription)\n", stderr)
        // Drain this session's frames so the stream stays in sync for the next one.
        while let command = await readCommand(reader), command.type != "stop" {
            if command.type == "audio", let length = command.length {
                _ = await readPayload(reader, length: length)
            }
        }
        writeWorkerFrame(["type": "error", "message": error.localizedDescription])
        return
    }

    while let command = await readCommand(reader) {
        switch command.type {
        case "audio":
            guard let length = command.length, length > 0,
                let payload = await readPayload(reader, length: length)
            else { continue }
            do {
                try session.append(payload)
            } catch {
                fputs("[apple-stt] Dropping audio chunk: \(error.localizedDescription)\n", stderr)
            }
        case "stop":
            do {
                let text = try await session.finish()
                writeWorkerFrame(["type": "final", "text": text])
            } catch {
                writeWorkerFrame(["type": "error", "message": error.localizedDescription])
            }
            return
        default:
            writeWorkerFrame(["type": "error", "message": "unexpected frame: \(command.type)"])
            return
        }
    }
}

/// Force the recognition model for a language into memory with a short discarded
/// session, so the first real request doesn't pay the lazy-load cost.
private func warmUp(language: String) async {
    do {
        let session = try await makeStreamingSession(language: language) { _ in }
        try session.append(Data(count: 3200))  // 100 ms of silence
        _ = try await session.finish()
        fputs("[apple-stt] Warmed up recognition for '\(language)'\n", stderr)
    } catch {
        fputs("[apple-stt] Warmup for '\(language)' failed: \(error.localizedDescription)\n", stderr)
    }
}

/// Run the persistent worker loop: authorize, warm up, signal readiness, then
/// serve transcription sessions from stdin until EOF.
func runWorkerMode(language: String) async {
    do {
        try await requestAuthorization()
    } catch {
        fputs("Error: \(error.localizedDescription)\n", stderr)
        exit(1)
    }

    await warmUp(language: language)
    writeWorkerFrame(["type": "ready"])

    let reader = StdinFrameReader()
    while let command = await readCommand(reader) {
        guard command.type == "transcribe" else {
            writeWorkerFrame(["type": "error", "message": "expected transcribe frame"])
            continue
        }
        await runSession(reader: reader, language: command.language ?? language)
    }
}
