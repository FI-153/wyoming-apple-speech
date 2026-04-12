import Foundation

/// Protocol for speech-to-text engines.
protocol STTEngine {
    /// Transcribe raw PCM audio data to text.
    /// - Parameters:
    ///   - pcmData: Raw PCM audio (16kHz, 16-bit signed integer, mono).
    ///   - language: BCP-47 language code (e.g. "en").
    /// - Returns: The transcribed text, or empty string if no speech detected.
    func transcribe(pcmData: Data, language: String) async throws -> String
}

/// Errors that can occur during speech recognition.
enum STTError: LocalizedError {
    case languageNotSupported(String)
    case onDeviceModelNotAvailable
    case bufferCreationFailed
    case recognitionFailed(String)

    var errorDescription: String? {
        switch self {
        case .languageNotSupported(let lang):
            return "Language '\(lang)' is not supported for on-device recognition."
        case .onDeviceModelNotAvailable:
            return "On-device speech model is not downloaded. "
                + "Download it via System Settings → Apple Intelligence & Siri → Language."
        case .bufferCreationFailed:
            return "Failed to create audio buffer from PCM data."
        case .recognitionFailed(let msg):
            return "Speech recognition failed: \(msg)"
        }
    }
}
