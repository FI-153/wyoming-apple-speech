import AVFoundation
import Speech

/// STT engine using SpeechAnalyzer (macOS 26+).
/// Faster and more accurate than SFSpeechRecognizer for on-device recognition.
@available(macOS 26, *)
class SpeechAnalyzerEngine: STTEngine {

    func transcribe(pcmData: Data, language: String) async throws -> String {
        guard SpeechTranscriber.isAvailable else {
            throw STTError.recognitionFailed("SpeechTranscriber is not available on this system.")
        }

        let locale = try await resolveLocale(language)

        guard let inputBuffer = try makePCMBuffer(from: pcmData) else {
            return ""
        }

        // Use a SpeechTranscriber since it is better suited for commands
        let transcriber = SpeechTranscriber(
            locale: locale,
            transcriptionOptions: [],
            reportingOptions: [],
            attributeOptions: []
        )

        // If the language model is missing then install it
        try await ensureModelDownloaded(for: transcriber, locale: locale)

        let analyzer = SpeechAnalyzer(modules: [transcriber])

        guard
            let analyzerFormat = await SpeechAnalyzer.bestAvailableAudioFormat(
                compatibleWith: [transcriber]
            )
        else {
            throw STTError.recognitionFailed(
                "No compatible audio format available for SpeechAnalyzer.")
        }

        let convertedBuffer: AVAudioPCMBuffer
        if pcmInputFormat == analyzerFormat {
            convertedBuffer = inputBuffer
        } else {
            guard let converter = AVAudioConverter(from: pcmInputFormat, to: analyzerFormat) else {
                throw STTError.recognitionFailed("Cannot convert audio to analyzer format.")
            }
            let convertedCapacity = AVAudioFrameCount(
                Double(inputBuffer.frameLength) * analyzerFormat.sampleRate
                    / pcmInputFormat.sampleRate
            )
            guard
                let buffer = AVAudioPCMBuffer(
                    pcmFormat: analyzerFormat,
                    frameCapacity: convertedCapacity
                )
            else {
                throw STTError.bufferCreationFailed
            }
            try converter.convert(to: buffer, from: inputBuffer)
            convertedBuffer = buffer
        }

        let (stream, continuation) = AsyncStream<AnalyzerInput>.makeStream()

        // Transcribe
        let resultTask = Task {
            var text = ""
            for try await result in transcriber.results {
                if result.isFinal {
                    text = String(result.text.characters)
                }
            }
            return text
        }

        try await analyzer.start(inputSequence: stream)

        continuation.yield(AnalyzerInput(buffer: convertedBuffer))
        continuation.finish()

        try await analyzer.finalizeAndFinishThroughEndOfInput()

        return try await resultTask.value
    }

    // MARK: - Private Helpers

    /// Resolve a language string to a supported ``SpeechTranscriber`` locale.
    ///
    /// Delegates to ``bestMatchingLocale(for:in:)`` using the set of
    /// locales reported by ``SpeechTranscriber/supportedLocales``.
    ///
    /// - Parameter language: BCP-47 language code (e.g. "en", "en-US").
    /// - Returns: A supported locale matching the language.
    private func resolveLocale(_ language: String) async throws -> Locale {
        let supported = await SpeechTranscriber.supportedLocales
        guard let locale = bestMatchingLocale(for: language, in: supported) else {
            throw STTError.languageNotSupported(language)
        }
        return locale
    }

    /// Ensure the on-device model for the given locale is downloaded.
    ///
    /// Checks ``SpeechTranscriber/installedLocales`` first. If the model
    /// is missing, requests an asset download via ``AssetInventory``.
    ///
    /// - Parameters:
    ///   - transcriber: The transcriber whose model to check.
    ///   - locale: The locale whose model must be installed.
    private func ensureModelDownloaded(
        for transcriber: SpeechTranscriber,
        locale: Locale
    ) async throws {
        let installed = await SpeechTranscriber.installedLocales
        let localeBCP47 = locale.identifier(.bcp47)

        if installed.contains(where: { $0.identifier(.bcp47) == localeBCP47 }) {
            return
        }

        if let downloader = try await AssetInventory.assetInstallationRequest(
            supporting: [transcriber]
        ) {
            fputs("[apple-stt] Downloading model for \(locale)...", stderr)
            try await downloader.downloadAndInstall()
            fputs("[apple-stt] Download finised for \(locale)", stderr)
        } else {
            throw STTError.onDeviceModelNotAvailable
        }
    }
}
