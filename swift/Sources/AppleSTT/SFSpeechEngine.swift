import AVFoundation
import Speech

/// STT engine using SFSpeechRecognizer (macOS 14+).
/// Uses on-device recognition only — no network calls.
class SFSpeechEngine: STTEngine {

    func transcribe(pcmData: Data, language: String) async throws -> String {
        let supportedLocales = Array(SFSpeechRecognizer.supportedLocales())

        // Check if the lanauge is supported among the locales
        guard let locale = bestMatchingLocale(for: language, in: supportedLocales) else {
            throw STTError.languageNotSupported(language)
        }

        // Check if the language is supported by SFSpeechRecognizer
        guard let recognizer = SFSpeechRecognizer(locale: locale) else {
            throw STTError.languageNotSupported(language)
        }

        // Check if the on-device stt model is downloaded
        guard recognizer.supportsOnDeviceRecognition else {
            throw STTError.onDeviceModelNotAvailable
        }

        // Create a local request that returns all the data at the same time (no chunks)
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.requiresOnDeviceRecognition = true
        request.shouldReportPartialResults = false

        // Create a buffer from the given audio and append it to the request
        guard let buffer = try makePCMBuffer(from: pcmData) else {
            return ""
        }
        request.append(buffer)
        request.endAudio()

        // Actual transcription
        return try await withCheckedThrowingContinuation { continuation in
            var hasResumed = false
            recognizer.recognitionTask(with: request) { result, error in
                guard !hasResumed else { return }

                if let error = error {
                    hasResumed = true
                    continuation.resume(
                        throwing: STTError.recognitionFailed(error.localizedDescription)
                    )
                    return
                }

                if let result = result, result.isFinal {
                    hasResumed = true
                    continuation.resume(
                        returning: result.bestTranscription.formattedString
                    )
                }
            }
        }
    }
}
