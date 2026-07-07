import Foundation
import Speech

/// Read all data from stdin until EOF.
func readStdin() -> Data {
    var data = Data()
    let bufferSize = 65536
    let buf = UnsafeMutablePointer<UInt8>.allocate(capacity: bufferSize)
    defer { buf.deallocate() }

    while true {
        let bytesRead = fread(buf, 1, bufferSize, stdin)
        if bytesRead > 0 {
            data.append(buf, count: bytesRead)
        }
        if bytesRead < bufferSize {
            break
        }
    }

    return data
}

/// Write JSON transcript to stdout.
func writeTranscript(_ text: String) {
    let result: [String: String] = ["text": text]
    if let jsonData = try? JSONSerialization.data(withJSONObject: result),
        let jsonString = String(data: jsonData, encoding: .utf8)
    {
        print(jsonString)
    }
}

/// Parsed command-line arguments.
struct CLIArgs {
    /// BCP-47 language code for transcription.
    var language: String = "en"
    /// When true, print supported languages and exit.
    var listLanguages: Bool = false
    /// When true, download/ensure the language model and exit (no transcription).
    var preload: Bool = false
    /// When true, run as a persistent streaming worker (framed protocol on stdin/stdout).
    var worker: Bool = false
}

/// Parse command-line arguments.
func parseArgs() -> CLIArgs {
    var result = CLIArgs()
    let args = CommandLine.arguments

    var i = 1
    while i < args.count {
        if args[i] == "--language", i + 1 < args.count {
            result.language = args[i + 1]
            i += 2
        } else if args[i] == "--list-languages" {
            result.listLanguages = true
            i += 1
        } else if args[i] == "--preload" {
            result.preload = true
            i += 1
        } else if args[i] == "--worker" {
            result.worker = true
            i += 1
        } else {
            i += 1
        }
    }

    return result
}

/// Request speech recognition authorization and wait for the result.
func requestAuthorization() async throws {
    let status = await withCheckedContinuation { continuation in
        SFSpeechRecognizer.requestAuthorization { status in
            continuation.resume(returning: status)
        }
    }

    switch status {
    case .authorized:
        return
    case .denied:
        throw STTError.recognitionFailed(
            "Speech recognition permission denied. "
                + "Grant access in System Settings → Privacy & Security → Speech Recognition."
        )
    case .restricted:
        throw STTError.recognitionFailed("Speech recognition is restricted on this device.")
    case .notDetermined:
        throw STTError.recognitionFailed("Speech recognition authorization not determined.")
    @unknown default:
        throw STTError.recognitionFailed("Unknown authorization status.")
    }
}

/// Attempt transcription with fallback from SpeechAnalyzer to SFSpeechRecognizer.
func transcribeWithFallback(pcmData: Data, language: String) async throws -> String {
    if #available(macOS 26, *) {
        do {
            fputs("[apple-stt] Using SpeechAnalyzer engine (macOS 26+)\n", stderr)
            let analyzer = SpeechAnalyzerEngine()
            let text = try await analyzer.transcribe(pcmData: pcmData, language: language)
            fputs("[apple-stt] Transcription result: \"\(text)\"\n", stderr)
            return text
        } catch {
            fputs(
                "[apple-stt] SpeechAnalyzer failed, falling back to SFSpeechRecognizer: \(error.localizedDescription)\n",
                stderr)
        }
    }
    fputs("[apple-stt] Using SFSpeechRecognizer engine (pre-Tahoe)\n", stderr)
    let legacy = SFSpeechEngine()
    let text = try await legacy.transcribe(pcmData: pcmData, language: language)
    fputs("[apple-stt] Transcription result: \"\(text)\"\n", stderr)
    return text
}

// MARK: - Main

let cliArgs = parseArgs()

if cliArgs.listLanguages {
    var codes: [String]
    if #available(macOS 26, *) {
        let locales = await SpeechTranscriber.supportedLocales
        codes = languageCodes(from: locales)
    } else {
        let locales = Array(SFSpeechRecognizer.supportedLocales())
        codes = languageCodes(from: locales)
    }
    if let jsonData = try? JSONSerialization.data(withJSONObject: codes),
        let jsonString = String(data: jsonData, encoding: .utf8)
    {
        print(jsonString)
    }
    exit(0)
}

if cliArgs.preload {
    if #available(macOS 26, *) {
        do {
            let engine = SpeechAnalyzerEngine()
            try await engine.preloadModel(for: cliArgs.language)
            fputs("[apple-stt] Model ready for \(cliArgs.language)\n", stderr)
            exit(0)
        } catch {
            fputs("Error: \(error.localizedDescription)\n", stderr)
            exit(1)
        }
    }
    // SFSpeechRecognizer has no downloadable-asset API here; nothing to preload.
    fputs("[apple-stt] Preload is a no-op on macOS < 26\n", stderr)
    exit(0)
}

if cliArgs.worker {
    await runWorkerMode(language: cliArgs.language)
    exit(0)
}

let language = cliArgs.language
let pcmData = readStdin()

let durationSeconds = Double(pcmData.count) / (16000.0 * 2.0)
fputs(
    "[apple-stt] Received \(pcmData.count) bytes (\(String(format: "%.1f", durationSeconds))s) of PCM audio, language=\(language)\n",
    stderr)

if pcmData.isEmpty {
    fputs("[apple-stt] Empty audio, returning empty transcript\n", stderr)
    writeTranscript("")
    exit(0)
}

do {
    try await requestAuthorization()
    let text = try await transcribeWithFallback(pcmData: pcmData, language: language)
    writeTranscript(text)
} catch {
    fputs("Error: \(error.localizedDescription)\n", stderr)
    exit(1)
}
