import AVFoundation
import Speech

/// STT engine using SpeechAnalyzer (macOS 26+).
/// Faster and more accurate than SFSpeechRecognizer for on-device recognition.
@available(macOS 26, *)
class SpeechAnalyzerEngine: STTEngine {

    func transcribe(pcmData: Data, language: String) async throws -> String {
        let locale = Locale(identifier: language)

        guard SpeechTranscriber.isAvailable else {
            throw STTError.recognitionFailed("SpeechTranscriber is not available on this system.")
        }

        let inputFormat = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: 16000,
            channels: 1,
            interleaved: true
        )!

        let bytesPerFrame = inputFormat.streamDescription.pointee.mBytesPerFrame
        let frameCount = UInt32(pcmData.count) / bytesPerFrame
        guard frameCount > 0 else {
            return ""
        }

        guard let inputBuffer = AVAudioPCMBuffer(
            pcmFormat: inputFormat,
            frameCapacity: frameCount
        ) else {
            throw STTError.bufferCreationFailed
        }
        inputBuffer.frameLength = frameCount

        pcmData.withUnsafeBytes { rawBuffer in
            guard let src = rawBuffer.baseAddress else { return }
            memcpy(inputBuffer.int16ChannelData![0], src, pcmData.count)
        }

        // Set up transcriber and analyzer
        let transcriber = SpeechTranscriber(
            locale: locale,
            transcriptionOptions: [],
            reportingOptions: [],
            attributeOptions: []
        )

        let analyzer = SpeechAnalyzer(modules: [transcriber])

        // Get the format the analyzer expects and convert if needed
        guard let analyzerFormat = await SpeechAnalyzer.bestAvailableAudioFormat(
            compatibleWith: [transcriber]
        ) else {
            throw STTError.recognitionFailed("No compatible audio format available for SpeechAnalyzer.")
        }

        let convertedBuffer: AVAudioPCMBuffer
        if inputFormat == analyzerFormat {
            convertedBuffer = inputBuffer
        } else {
            guard let converter = AVAudioConverter(from: inputFormat, to: analyzerFormat) else {
                throw STTError.recognitionFailed("Cannot convert audio to analyzer format.")
            }
            let convertedCapacity = AVAudioFrameCount(
                Double(frameCount) * analyzerFormat.sampleRate / inputFormat.sampleRate
            )
            guard let buffer = AVAudioPCMBuffer(
                pcmFormat: analyzerFormat,
                frameCapacity: convertedCapacity
            ) else {
                throw STTError.bufferCreationFailed
            }
            try converter.convert(to: buffer, from: inputBuffer)
            convertedBuffer = buffer
        }

        // Create async stream to feed audio
        let (stream, continuation) = AsyncStream<AnalyzerInput>.makeStream()

        // Collect results
        var finalText = ""
        let resultTask = Task {
            for try await result in transcriber.results {
                if result.isFinal {
                    finalText = String(result.text.characters)
                }
            }
        }

        // Start analyzer, feed audio, finalize
        try await analyzer.start(inputSequence: stream)

        continuation.yield(AnalyzerInput(buffer: convertedBuffer))
        continuation.finish()

        try await analyzer.finalizeAndFinishThroughEndOfInput()

        // Wait for results to finish
        try await resultTask.value

        return finalText
    }
}
