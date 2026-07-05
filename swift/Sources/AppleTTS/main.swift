import Foundation

/// Sample rate of the PCM audio the Siri engine produces for current neural voices.
let engineSampleRate = 48_000
/// Bytes per sample (16-bit).
let engineSampleWidth = 2
/// Channel count (mono).
let engineChannels = 1

/// Serializes stdout writes: audio callbacks fire on engine threads while the main thread
/// owns the command loop, and each audio frame (header line + binary payload) must reach
/// the pipe as one contiguous write.
let stdoutLock = NSLock()

/// Write one protocol frame to stdout: a JSON header line, then an optional binary payload.
func writeFrame(_ header: [String: Any], payload: Data? = nil) {
    var data = try! JSONSerialization.data(withJSONObject: header)
    data.append(0x0A)
    if let payload {
        data.append(payload)
    }

    stdoutLock.lock()
    defer { stdoutLock.unlock() }
    FileHandle.standardOutput.write(data)
}

/// Log a message to stderr (stdout is reserved for protocol frames).
func logError(_ message: String) {
    fputs("[apple-tts] \(message)\n", stderr)
}

/// A synthesize command received on stdin.
struct SynthesizeCommand: Decodable {
    let command: String
    let text: String
    let voicePath: String
    let rate: Double?
    let pitch: Double?
    let volume: Double?

    enum CodingKeys: String, CodingKey {
        case command
        case text
        case voicePath = "voice_path"
        case rate
        case pitch
        case volume
    }
}

/// Parsed command-line arguments.
struct CLIArgs {
    /// When true, print discovered system voices as JSON and exit.
    var listVoices: Bool = false
    /// Voice bundle to initialize and preheat before signaling readiness.
    var preloadVoicePath: String?
}

/// Parse command-line arguments.
func parseArgs() -> CLIArgs {
    var result = CLIArgs()
    let args = CommandLine.arguments

    var i = 1
    while i < args.count {
        if args[i] == "--list-voices" {
            result.listVoices = true
            i += 1
        } else if args[i] == "--preload-voice", i + 1 < args.count {
            result.preloadVoicePath = args[i + 1]
            i += 2
        } else {
            i += 1
        }
    }

    return result
}

/// Run one synthesize command, streaming audio frames to stdout.
///
/// Emits `audio` frames as the engine produces PCM, then a final `done` frame. On failure an
/// `error` frame is emitted instead; engine-init failures additionally terminate the process,
/// because a failed init leaves the native library in a state that crashes later (the pool
/// manager replaces the worker).
func handleSynthesize(_ command: SynthesizeCommand, engine: SiriEngine) {
    let audioHandler: SiriAudioHandler = { data in
        guard data.length > 0 else { return }
        writeFrame(
            [
                "type": "audio",
                "length": data.length,
                "rate": engineSampleRate,
                "width": engineSampleWidth,
                "channels": engineChannels,
            ],
            payload: data as Data
        )
    }

    do {
        try engine.synthesize(
            text: command.text,
            voicePath: command.voicePath,
            rate: command.rate ?? 1.0,
            pitch: command.pitch ?? 1.0,
            volume: command.volume ?? 1.0,
            audioHandler: audioHandler
        )
        writeFrame(["type": "done"])
    } catch SiriEngineError.engineInitFailed(let message) {
        writeFrame(["type": "error", "message": message])
        logError("Engine init failed, exiting (poisoned state): \(message)")
        exit(1)
    } catch {
        writeFrame(["type": "error", "message": error.localizedDescription])
    }
}

// MARK: - Main

let cliArgs = parseArgs()

if cliArgs.listVoices {
    let voices = discoverSystemVoices()
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    let jsonData = try! encoder.encode(voices)
    FileHandle.standardOutput.write(jsonData)
    FileHandle.standardOutput.write(Data([0x0A]))
    exit(0)
}

do {
    try SiriEngine.loadFramework()
} catch {
    logError("Error: \(error.localizedDescription)")
    exit(1)
}

let engine = SiriEngine()

if let preloadVoicePath = cliArgs.preloadVoicePath {
    do {
        _ = try engine.engine(forVoicePath: preloadVoicePath)
        // Preheating alone leaves lazy engine setup for the first synthesis, which then
        // pays a multi-second penalty. A tiny discarded warmup synthesis moves that cost
        // to worker startup, before the pool marks this worker ready.
        try engine.synthesize(
            text: ".",
            voicePath: preloadVoicePath,
            rate: 1.0,
            pitch: 1.0,
            volume: 1.0,
            audioHandler: { _ in }
        )
        logError("Preheated voice at \(preloadVoicePath)")
    } catch {
        logError("Error: preload failed: \(error.localizedDescription)")
        exit(1)
    }
}

writeFrame(["type": "ready"])

while let line = readLine(strippingNewline: true) {
    guard !line.isEmpty else { continue }
    guard
        let lineData = line.data(using: .utf8),
        let command = try? JSONDecoder().decode(SynthesizeCommand.self, from: lineData),
        command.command == "synthesize"
    else {
        writeFrame(["type": "error", "message": "unrecognized command: \(line.prefix(200))"])
        continue
    }

    handleSynthesize(command, engine: engine)
}
