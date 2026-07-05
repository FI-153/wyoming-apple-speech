import Foundation

/// Receives raw PCM chunks (48 kHz, 16-bit, mono) as the engine produces them.
typealias SiriAudioHandler = @convention(block) (NSData) -> Void
/// Receives word-timing objects; unused here but the engine expects a handler to be set.
typealias SiriWordTimingsHandler = @convention(block) ([NSObject]) -> Void

/// Errors raised while driving the private Siri synthesis engine.
enum SiriEngineError: Error, LocalizedError {
    case frameworkUnavailable
    case incompatibleVoice(String)
    case engineInitFailed(String)
    case synthesisFailed(String)

    var errorDescription: String? {
        switch self {
        case .frameworkUnavailable:
            return "SiriTTSService.framework could not be loaded"
        case .incompatibleVoice(let path):
            return "Voice bundle uses the 2021-era tacotron-only format and cannot be loaded: \(path)"
        case .engineInitFailed(let message):
            return "The Siri engine couldn't load this voice: \(message)"
        case .synthesisFailed(let message):
            return "Synthesis failed: \(message)"
        }
    }
}

/// Alloc an instance of an Objective-C class by name.
///
/// alloc's +1 is deliberately left unclaimed: a consuming ObjC init takes it over, including
/// releasing it when init fails and returns nil (claiming it retained causes a use-after-free
/// exactly there). The resulting +1 over-retain on success is intentional — Siri engine
/// objects must never deallocate, see `SiriEngine.engineCache`.
private func allocInstance(_ cls: String) throws -> NSObject {
    guard let cObject = NSClassFromString(cls) as? NSObject.Type else {
        throw SiriEngineError.frameworkUnavailable
    }
    return cObject.perform("alloc").takeUnretainedValue() as! NSObject
}

/// Owns the private Siri speech engine and hides the Objective-C bridging details.
///
/// The native library is hostile to teardown: deallocating an engine tears down
/// process-global state that later inits and deallocs then trip over, and a failed init
/// poisons the autorelease pool (the next drain crashes in objc_release). Engines are
/// therefore cached per voice for the process lifetime and never deallocated, and callers
/// must treat `engineInitFailed` as fatal for the process.
final class SiriEngine {
    private var engineCache: [String: NSObject] = [:]

    /// Load the private framework that hosts the engine classes.
    ///
    /// Must be called once before any other use.
    ///
    /// - Throws: `SiriEngineError.frameworkUnavailable` when dlopen fails.
    static func loadFramework() throws {
        let frameworkPath = "/System/Library/PrivateFrameworks/SiriTTSService.framework/SiriTTSService"
        guard dlopen(frameworkPath, RTLD_LAZY) != nil else {
            throw SiriEngineError.frameworkUnavailable
        }
    }

    /// Return a ready (initialized and preheated) engine for a voice bundle, caching it.
    ///
    /// - Parameter voicePath: Absolute path of the voice's `.asset` directory.
    /// - Returns: The cached or newly initialized engine.
    /// - Throws: `SiriEngineError` when the bundle is incompatible or the engine refuses it.
    ///   An `engineInitFailed` error leaves the process in a poisoned state; the caller must
    ///   report it and exit.
    func engine(forVoicePath voicePath: String) throws -> NSObject {
        if let cachedEngine = engineCache[voicePath] {
            return cachedEngine
        }

        let voiceURL = URL(fileURLWithPath: voicePath, isDirectory: true)
        guard voiceIsCompatibleWithSystemEngine(voiceURL) else {
            throw SiriEngineError.incompatibleVoice(voicePath)
        }

        let newEngine = try initEngine(voicePath: voiceURL)
        try preheat(newEngine)
        engineCache[voicePath] = newEngine
        return newEngine
    }

    private func initEngine(voicePath: URL) throws -> NSObject {
        let allocIns = try allocInstance("SiriTTSSynthesisEngine")

        let selector = NSSelectorFromString("initWithVoicePath:resourcePath:error:")
        let method = class_getInstanceMethod(type(of: allocIns), selector)
        let implementation = method_getImplementation(method!)

        // `Any` is an existential container (inline buffer + witness tables), not a single
        // pointer, so it does not match the `NSString*` (`id`) slot the real IMP expects.
        // Declaring these as `AnyObject` keeps the call ABI-compatible with a plain object
        // pointer.
        typealias Function = @convention(c) (
            AnyObject, Selector, AnyObject, AnyObject, UnsafeMutablePointer<NSObject?>?
        ) -> NSObject
        let function = unsafeBitCast(implementation, to: Function.self)

        var error: NSObject?
        let instance = function(
            allocIns,
            selector,
            voicePath.path as NSString,
            voicePath.appendingPathComponent("AssetData").path as NSString,
            &error
        )

        if let error = error as? NSError {
            throw SiriEngineError.engineInitFailed(error.localizedDescription)
        }

        return instance
    }

    private func preheat(_ engine: NSObject) throws {
        let selector = NSSelectorFromString("preheatWithError:")
        let method = class_getInstanceMethod(type(of: engine), selector)
        let implementation = method_getImplementation(method!)

        typealias Function = @convention(c) (
            AnyObject, Selector, UnsafeMutablePointer<AnyObject?>
        ) -> Bool
        let function = unsafeBitCast(implementation, to: Function.self)

        var outError: AnyObject?
        _ = function(engine, selector, &outError)

        if let error = outError as? NSError {
            throw SiriEngineError.engineInitFailed(error.localizedDescription)
        }
    }

    /// Synthesize text with a voice, delivering PCM chunks through the audio handler.
    ///
    /// Blocks until synthesis completes; `audioHandler` fires on engine-owned threads while
    /// this call is in progress.
    ///
    /// - Parameters:
    ///   - text: The text to speak.
    ///   - voicePath: Absolute path of the voice's `.asset` directory.
    ///   - rate: Speaking rate multiplier (1.0 = normal).
    ///   - pitch: Pitch multiplier (1.0 = normal).
    ///   - volume: Volume multiplier (1.0 = normal).
    ///   - audioHandler: Receives raw PCM chunks as they are produced.
    /// - Throws: `SiriEngineError` when the engine cannot load the voice or synthesis fails.
    func synthesize(
        text: String,
        voicePath: String,
        rate: Double,
        pitch: Double,
        volume: Double,
        audioHandler: @escaping SiriAudioHandler
    ) throws {
        let engine = try engine(forVoicePath: voicePath)

        let allocIns = try allocInstance("SiriTTSSynthesisEngineRequest")
        let request = allocIns.perform("init").takeUnretainedValue() as! NSObject

        request.setValuesForKeys([
            "text": text,
            "privacySensitive": false,
            "requestId": UUID().uuidString,
            "profile": 1,
            "rate": rate,
            "pitch": pitch,
            "volume": volume,
        ])

        let wordTimingsHandler: SiriWordTimingsHandler = { _ in }
        request.perform("setAudioHandler:", with: audioHandler)
        request.perform("setWordTimingsHandler:", with: wordTimingsHandler)

        let selector = NSSelectorFromString("synthesize:error:")
        let method = class_getInstanceMethod(type(of: engine), selector)
        let implementation = method_getImplementation(method!)

        typealias Function = @convention(c) (
            AnyObject, Selector, AnyObject, UnsafeMutablePointer<NSObject?>?
        ) -> Bool
        let function = unsafeBitCast(implementation, to: Function.self)

        var outError: NSObject?
        let success = function(engine, selector, request, &outError)

        if let error = outError as? NSError {
            throw SiriEngineError.synthesisFailed(error.localizedDescription)
        }
        if !success {
            throw SiriEngineError.synthesisFailed("engine returned failure without an error")
        }
    }
}
