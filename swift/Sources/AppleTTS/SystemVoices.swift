import Foundation

/// A Siri voice bundle that macOS manages itself under the MobileAsset stores.
///
/// These bundles are populated when a Siri voice is selected in System Settings and always
/// match the system's private TTS engine — unlike the frozen 2021 download catalog, whose
/// voices no longer load on current macOS.
struct SystemVoice: Codable {
    /// Lowercase voice name from the asset specifier (e.g. "helena").
    let name: String
    /// BCP-47 language tag (e.g. "de-DE").
    let language: String
    /// Voice model type from the asset specifier (e.g. "neural").
    let type: String
    /// Quality footprint from the asset specifier (e.g. "premium").
    let footprint: String
    /// Speaker gender as reported by the asset properties, or "unknown".
    let gender: String
    /// The asset's ttsContentVersion, or 0 when absent.
    let version: Int
    /// Absolute path of the `.asset` directory (usable as the engine's voice path).
    let path: String
}

/// MobileAsset stores where macOS keeps the Siri voices selected in System Settings.
let defaultVoiceAssetStores = [
    "/System/Library/AssetsV2/com_apple_MobileAsset_Trial_Siri_SiriTextToSpeech/purpose_auto",
    "/System/Library/AssetsV2/com_apple_MobileAsset_UAF_Siri_TextToSpeech/purpose_auto",
]

/// Parse a Siri TTS voice asset specifier into its voice fields.
///
/// Specifiers look like `com.apple.siri.tts.voice.de_DE.helena.neural.premium`. The same
/// stores also hold `com.apple.siri.tts.resource.*` assets, which are not voices and are
/// rejected here. Locale underscores are normalized to hyphens ("de_DE" → "de-DE").
///
/// - Parameter specifier: The `Factor` or `AssetSpecifier` value from an asset's Info.plist.
/// - Returns: The parsed fields, or nil when the specifier is not a voice.
func parseVoiceSpecifier(
    _ specifier: String
) -> (language: String, name: String, type: String, footprint: String)? {
    let voicePrefix = "com.apple.siri.tts.voice."
    guard specifier.hasPrefix(voicePrefix) else { return nil }

    let components = specifier.dropFirst(voicePrefix.count).components(separatedBy: ".")
    guard components.count >= 4 else { return nil }

    return (
        language: components[0].replacingOccurrences(of: "_", with: "-"),
        name: components[1],
        type: components[2],
        footprint: components[3]
    )
}

/// Whether a voice bundle can be loaded *and* synthesized by the system engine on current macOS.
///
/// Two bundle types are refused up front because both leave the native library in a broken
/// state rather than failing cleanly:
///
/// - Tacotron-only bundles (the 2021 catalog era): the engine loads neural voices via their
///   fastspeech2 model data, but tacotron-only init demands emotion resources that no longer
///   exist, fails, and poisons the process.
/// - Bundles that force the "hydra" text frontend (the en-US / en-GB / en-IN "nashville"
///   family): init and preheat succeed, but `synthesize` throws `map::at: key not found`
///   because that frontend needs the shared `com.apple.siri.tts.resource.<lang>` bundle, which
///   the in-process engine cannot load on macOS 26 — and the failed synthesis poisons the
///   autorelease pool. See `voiceForcesHydraFrontend(atAssetData:)`.
///
/// - Parameter voicePath: The voice bundle directory.
/// - Returns: True when the bundle is safe to hand to the engine.
func voiceIsCompatibleWithSystemEngine(_ voicePath: URL) -> Bool {
    let assetData = voicePath.appendingPathComponent("AssetData")
    let fileManager = FileManager.default
    let usesTacotron = fileManager.fileExists(atPath: assetData.appendingPathComponent("tacotron").path)
    let hasFastspeech2 = fileManager.fileExists(atPath: assetData.appendingPathComponent("fastspeech2").path)
    if usesTacotron && !hasFastspeech2 {
        return false
    }
    if voiceForcesHydraFrontend(atAssetData: assetData) {
        return false
    }
    return true
}

/// Whether a voice's frontend config forces the "hydra" text frontend.
///
/// These voices carry a `frontend.cfg` with `"force_hydra_fe": true` and depend on the shared
/// `com.apple.siri.tts.resource.<lang>` bundle for their text-normalization rules. The
/// in-process engine can't load that bundle on macOS 26, so synthesis throws
/// `map::at: key not found`. Voices without a `frontend.cfg`, or with the flag absent or false,
/// use a self-contained frontend and synthesize normally.
///
/// - Parameter assetData: The voice bundle's `AssetData` directory.
/// - Returns: True when the voice forces the hydra frontend.
func voiceForcesHydraFrontend(atAssetData assetData: URL) -> Bool {
    let configURL = assetData.appendingPathComponent("frontend.cfg")
    guard
        let data = try? Data(contentsOf: configURL),
        let config = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
        let forcesHydra = config["force_hydra_fe"] as? Bool
    else {
        return false
    }
    return forcesHydra
}

/// Read one system voice from a `.asset` directory, if it holds a compatible voice.
///
/// - Parameter assetDirectory: A `.asset` directory inside a MobileAsset store.
/// - Returns: The voice, or nil when the directory is not a usable, compatible voice bundle.
func systemVoice(atAssetDirectory assetDirectory: URL) -> SystemVoice? {
    let assetData = assetDirectory.appendingPathComponent("AssetData")
    guard FileManager.default.fileExists(atPath: assetData.path) else { return nil }
    guard voiceIsCompatibleWithSystemEngine(assetDirectory) else { return nil }

    let infoPlistURL = assetDirectory.appendingPathComponent("Info.plist")
    guard
        let data = try? Data(contentsOf: infoPlistURL),
        let plist = try? PropertyListSerialization.propertyList(from: data, format: nil)
            as? [String: Any],
        let properties = plist["MobileAssetProperties"] as? [String: Any],
        let specifier = (properties["Factor"] ?? properties["AssetSpecifier"]) as? String,
        let fields = parseVoiceSpecifier(specifier)
    else { return nil }

    return SystemVoice(
        name: fields.name,
        language: fields.language,
        type: fields.type,
        footprint: fields.footprint,
        gender: properties["gender"] as? String ?? "unknown",
        version: Int(properties["ttsContentVersion"] as? String ?? "") ?? 0,
        path: assetDirectory.path
    )
}

/// Discover all compatible system-managed Siri voices.
///
/// Home Assistant keys a voice by `name-language-footprint`, ignoring its model type and
/// content version. The same speaker therefore ships as several assets that collapse to one
/// HA voice (e.g. `damon` en-US premium exists as both `neural` and `natural`). Those variants
/// are reported once here, keeping the one with the highest content version so HA sees no
/// duplicates.
///
/// - Parameter stores: MobileAsset store directories to scan.
/// - Returns: The discovered voices, sorted by language, then name, then footprint.
func discoverSystemVoices(in stores: [String] = defaultVoiceAssetStores) -> [SystemVoice] {
    var bestVoiceByKey: [String: SystemVoice] = [:]

    for store in stores {
        let storeURL = URL(fileURLWithPath: store, isDirectory: true)
        let assetURLs = (try? FileManager.default.contentsOfDirectory(
            at: storeURL,
            includingPropertiesForKeys: [.isDirectoryKey],
            options: [.skipsHiddenFiles]
        )) ?? []

        for assetURL in assetURLs where assetURL.pathExtension == "asset" {
            guard let voice = systemVoice(atAssetDirectory: assetURL) else { continue }

            let voiceKey = "\(voice.language)|\(voice.name)|\(voice.footprint)"
            if let existing = bestVoiceByKey[voiceKey], existing.version >= voice.version {
                continue
            }
            bestVoiceByKey[voiceKey] = voice
        }
    }

    return bestVoiceByKey.values.sorted {
        ($0.language, $0.name, $0.footprint) < ($1.language, $1.name, $1.footprint)
    }
}
