import AVFoundation
import Speech

/// One live streaming transcription: audio is appended as it arrives and partial
/// transcripts are reported while recognition runs concurrently.
protocol StreamingSTTSession {
    /// Append raw PCM audio (16 kHz, 16-bit signed integer, mono).
    func append(_ pcmData: Data) throws

    /// Signal end of audio and wait for the final transcript.
    func finish() async throws -> String
}

/// Streaming session backed by SFSpeechRecognizer (macOS 15+).
///
/// Recognition starts immediately and runs while audio is appended;
/// `onPartial` fires with the cumulative best transcription so far.
final class SFStreamingSession: StreamingSTTSession, @unchecked Sendable {
    private let request: SFSpeechAudioBufferRecognitionRequest
    private let recognizer: SFSpeechRecognizer
    private var task: SFSpeechRecognitionTask?

    private let stateLock = NSLock()
    private var completedResult: Result<String, Error>?
    private var finalContinuation: CheckedContinuation<String, Error>?
    private var lastPartialText = ""

    init(language: String, onPartial: @escaping (String) -> Void) throws {
        let (locale, recognizer) = try Self.resolveOnDeviceRecognizer(for: language)
        self.recognizer = recognizer
        fputs("[apple-stt] Using recognizer locale \(locale.identifier)\n", stderr)

        request = SFSpeechAudioBufferRecognitionRequest()
        request.requiresOnDeviceRecognition = true
        request.shouldReportPartialResults = true

        task = recognizer.recognitionTask(with: request) { [weak self] result, error in
            guard let self else { return }

            if let result {
                let text = result.bestTranscription.formattedString
                if result.isFinal {
                    self.complete(.success(text))
                } else {
                    self.stateLock.lock()
                    self.lastPartialText = text
                    self.stateLock.unlock()
                    onPartial(text)
                }
                return
            }

            if let error {
                // "No speech detected" after endAudio is a normal empty result,
                // not a failure; and if we already heard words, keep them rather
                // than discarding the utterance over a finalization hiccup.
                self.stateLock.lock()
                let heardText = self.lastPartialText
                self.stateLock.unlock()
                if !heardText.isEmpty {
                    self.complete(.success(heardText))
                } else if (error as NSError).code == 1110 {
                    self.complete(.success(""))
                } else {
                    self.complete(
                        .failure(STTError.recognitionFailed(error.localizedDescription)))
                }
            }
        }
    }

    /// Resolve a language to a locale whose on-device model is actually installed.
    ///
    /// `SFSpeechRecognizer.supportedLocales()` is an unordered set, so simply
    /// taking the first same-language locale can land on a regional variant with
    /// no downloaded model (e.g. "en" → "en-GB"). This prefers an exact BCP-47
    /// match, then any same-language locale that supports on-device recognition,
    /// scanned in deterministic (sorted) order.
    ///
    /// - Parameter language: BCP-47 language code (e.g. "en", "de-DE").
    /// - Returns: The chosen locale and its recognizer.
    /// - Throws: `STTError.languageNotSupported` if no locale matches the
    ///   language at all, or `STTError.onDeviceModelNotAvailable` if one matches
    ///   but no on-device model is installed for it.
    private static func resolveOnDeviceRecognizer(
        for language: String
    ) throws -> (Locale, SFSpeechRecognizer) {
        let supported = Array(SFSpeechRecognizer.supportedLocales())
        let candidate = Locale(identifier: language)
        let candidateBCP47 = candidate.identifier(.bcp47)

        let exact = supported.filter { $0.identifier(.bcp47) == candidateBCP47 }
        let sameLanguage = supported
            .filter {
                $0.identifier(.bcp47) != candidateBCP47
                    && $0.language.languageCode == candidate.language.languageCode
            }
            .sorted { $0.identifier(.bcp47) < $1.identifier(.bcp47) }
        let ordered = exact + sameLanguage

        guard !ordered.isEmpty else {
            throw STTError.languageNotSupported(language)
        }

        for locale in ordered {
            if let recognizer = SFSpeechRecognizer(locale: locale),
                recognizer.supportsOnDeviceRecognition
            {
                return (locale, recognizer)
            }
        }

        throw STTError.onDeviceModelNotAvailable
    }

    func append(_ pcmData: Data) throws {
        guard let buffer = try makePCMBuffer(from: pcmData) else { return }
        request.append(buffer)
    }

    func finish() async throws -> String {
        request.endAudio()

        // Safety net: some on-device recognizer configurations stop calling back
        // after endAudio() without ever delivering an isFinal result, which would
        // hang finish() forever. The streamed partials already hold the full text,
        // so if no final arrives shortly we finalize with the last partial.
        let fallback = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            guard !Task.isCancelled, let self else { return }
            self.complete(.success(self.snapshotLastPartial()))
        }

        let result = try await withCheckedThrowingContinuation { continuation in
            stateLock.lock()
            if let result = completedResult {
                stateLock.unlock()
                continuation.resume(with: result)
            } else {
                finalContinuation = continuation
                stateLock.unlock()
            }
        }
        fallback.cancel()
        return result
    }

    /// Thread-safe read of the most recent partial transcription.
    private func snapshotLastPartial() -> String {
        stateLock.lock()
        defer { stateLock.unlock() }
        return lastPartialText
    }

    /// Deliver the final result exactly once, whether finish() is already waiting or not.
    private func complete(_ result: Result<String, Error>) {
        stateLock.lock()
        guard completedResult == nil else {
            stateLock.unlock()
            return
        }
        completedResult = result
        let continuation = finalContinuation
        finalContinuation = nil
        stateLock.unlock()

        continuation?.resume(with: result)
    }
}

/// Streaming session backed by SpeechAnalyzer (macOS 26+).
///
/// Volatile results stream out as partials while finalized segments accumulate
/// into the final transcript.
@available(macOS 26, *)
final class AnalyzerStreamingSession: StreamingSTTSession {
    private let analyzer: SpeechAnalyzer
    private let analyzerFormat: AVAudioFormat
    private let converter: AVAudioConverter?
    private let inputContinuation: AsyncStream<AnalyzerInput>.Continuation
    private let resultTask: Task<String, Error>

    init(language: String, onPartial: @escaping @Sendable (String) -> Void) async throws {
        guard SpeechTranscriber.isAvailable else {
            throw STTError.recognitionFailed("SpeechTranscriber is not available on this system.")
        }

        let supported = await SpeechTranscriber.supportedLocales
        let installed = await SpeechTranscriber.installedLocales
        // Prefer a locale whose model is already installed, so a bare "en"
        // doesn't resolve to an uninstalled regional variant when another is ready.
        guard
            let locale = bestMatchingLocale(for: language, in: installed)
                ?? bestMatchingLocale(for: language, in: supported)
        else {
            throw STTError.languageNotSupported(language)
        }

        let transcriber = SpeechTranscriber(
            locale: locale,
            transcriptionOptions: [],
            reportingOptions: [.volatileResults],
            attributeOptions: []
        )

        let localeBCP47 = locale.identifier(.bcp47)
        if !installed.contains(where: { $0.identifier(.bcp47) == localeBCP47 }) {
            if let downloader = try await AssetInventory.assetInstallationRequest(
                supporting: [transcriber]
            ) {
                fputs("[apple-stt] Downloading model for \(locale)...\n", stderr)
                try await downloader.downloadAndInstall()
            }
        }

        guard
            let format = await SpeechAnalyzer.bestAvailableAudioFormat(
                compatibleWith: [transcriber]
            )
        else {
            throw STTError.recognitionFailed(
                "No compatible audio format available for SpeechAnalyzer.")
        }
        analyzerFormat = format
        converter = format == pcmInputFormat ? nil : AVAudioConverter(from: pcmInputFormat, to: format)

        resultTask = Task {
            var finalizedText = ""
            for try await result in transcriber.results {
                let text = String(result.text.characters)
                if result.isFinal {
                    finalizedText = finalizedText.isEmpty ? text : finalizedText + " " + text
                    onPartial(finalizedText)
                } else if !text.isEmpty {
                    let partial = finalizedText.isEmpty ? text : finalizedText + " " + text
                    onPartial(partial)
                }
            }
            return finalizedText
        }

        let (stream, continuation) = AsyncStream<AnalyzerInput>.makeStream()
        inputContinuation = continuation
        analyzer = SpeechAnalyzer(modules: [transcriber])
        try await analyzer.start(inputSequence: stream)
    }

    func append(_ pcmData: Data) throws {
        guard let inputBuffer = try makePCMBuffer(from: pcmData) else { return }

        guard let converter else {
            inputContinuation.yield(AnalyzerInput(buffer: inputBuffer))
            return
        }

        let outputCapacity = AVAudioFrameCount(
            (Double(inputBuffer.frameLength) * analyzerFormat.sampleRate
                / pcmInputFormat.sampleRate).rounded(.up) + 64
        )
        guard
            let outputBuffer = AVAudioPCMBuffer(
                pcmFormat: analyzerFormat, frameCapacity: outputCapacity)
        else {
            throw STTError.bufferCreationFailed
        }

        // Streaming conversion: hand over this chunk once, then report "ran dry"
        // so the converter returns what it has and keeps its internal state for
        // the next chunk (sample-rate converters carry filter history across calls).
        nonisolated(unsafe) var inputSupplied = false
        var conversionError: NSError?
        let status = converter.convert(to: outputBuffer, error: &conversionError) {
            _, outStatus in
            if inputSupplied {
                outStatus.pointee = .noDataNow
                return nil
            }
            inputSupplied = true
            outStatus.pointee = .haveData
            return inputBuffer
        }
        if status == .error {
            throw STTError.recognitionFailed(
                conversionError?.localizedDescription ?? "Audio conversion failed.")
        }
        if outputBuffer.frameLength > 0 {
            inputContinuation.yield(AnalyzerInput(buffer: outputBuffer))
        }
    }

    func finish() async throws -> String {
        inputContinuation.finish()
        try await analyzer.finalizeAndFinishThroughEndOfInput()
        return try await resultTask.value
    }
}
