import AVFoundation
import Speech

/// STT engine using SFSpeechRecognizer (macOS 14+).
/// Uses on-device recognition only — no network calls.
class SFSpeechEngine: STTEngine {

    func transcribe(pcmData: Data, language: String) async throws -> String {
        let locale = Locale(identifier: language)
        guard let recognizer = SFSpeechRecognizer(locale: locale) else {
            throw STTError.languageNotSupported(language)
        }

        guard recognizer.supportsOnDeviceRecognition else {
            throw STTError.onDeviceModelNotAvailable
        }

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.requiresOnDeviceRecognition = true
        request.shouldReportPartialResults = false

        let audioFormat = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: 16000,
            channels: 1,
            interleaved: true
        )!

        let bytesPerFrame = audioFormat.streamDescription.pointee.mBytesPerFrame
        let frameCount = UInt32(pcmData.count) / bytesPerFrame
        guard frameCount > 0 else {
            return ""
        }

        guard let buffer = AVAudioPCMBuffer(
            pcmFormat: audioFormat,
            frameCapacity: frameCount
        ) else {
            throw STTError.bufferCreationFailed
        }
        buffer.frameLength = frameCount

        pcmData.withUnsafeBytes { rawBuffer in
            guard let src = rawBuffer.baseAddress else { return }
            memcpy(buffer.int16ChannelData![0], src, pcmData.count)
        }

        request.append(buffer)
        request.endAudio()

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
