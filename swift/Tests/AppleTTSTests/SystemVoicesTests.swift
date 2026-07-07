import Foundation
import Testing

@testable import apple_tts

@Suite("parseVoiceSpecifier")
struct ParseVoiceSpecifierTests {

    @Test("parses a neural voice specifier")
    func neuralVoice() {
        let fields = parseVoiceSpecifier("com.apple.siri.tts.voice.de_DE.helena.neural.premium")
        #expect(fields != nil)
        #expect(fields?.language == "de-DE")
        #expect(fields?.name == "helena")
        #expect(fields?.type == "neural")
        #expect(fields?.footprint == "premium")
    }

    @Test("normalizes locale underscores to hyphens")
    func localeNormalization() {
        let fields = parseVoiceSpecifier("com.apple.siri.tts.voice.en_US.aria.neural.premiumhigh")
        #expect(fields?.language == "en-US")
    }

    @Test("keeps already-hyphenated locales")
    func hyphenatedLocale() {
        let fields = parseVoiceSpecifier("com.apple.siri.tts.voice.de-DE.martin.gryphon.compact")
        #expect(fields?.language == "de-DE")
    }

    @Test("rejects resource assets")
    func resourceAsset() {
        let fields = parseVoiceSpecifier("com.apple.siri.tts.resource.de_DE.nashville")
        #expect(fields == nil)
    }

    @Test("rejects specifiers with too few components")
    func tooFewComponents() {
        let fields = parseVoiceSpecifier("com.apple.siri.tts.voice.de_DE.helena")
        #expect(fields == nil)
    }

    @Test("rejects unrelated specifiers")
    func unrelated() {
        let fields = parseVoiceSpecifier("com.apple.MobileAsset.SomethingElse")
        #expect(fields == nil)
    }
}

@Suite("systemVoice discovery")
struct SystemVoiceDiscoveryTests {

    /// Build a fake `.asset` bundle directory and return its URL.
    private func makeAssetBundle(
        specifier: String?,
        gender: String? = "female",
        contentVersion: String? = "1328",
        modelDirectories: [String] = ["fastspeech2"],
        frontendConfig: String? = nil,
        includeAssetData: Bool = true
    ) throws -> URL {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("apple-tts-tests-\(UUID().uuidString)", isDirectory: true)
        let asset = root.appendingPathComponent("voice.asset", isDirectory: true)
        try FileManager.default.createDirectory(at: asset, withIntermediateDirectories: true)

        if includeAssetData {
            let assetData = asset.appendingPathComponent("AssetData", isDirectory: true)
            try FileManager.default.createDirectory(at: assetData, withIntermediateDirectories: true)
            for model in modelDirectories {
                try FileManager.default.createDirectory(
                    at: assetData.appendingPathComponent(model, isDirectory: true),
                    withIntermediateDirectories: true
                )
            }
            if let frontendConfig {
                try frontendConfig.write(
                    to: assetData.appendingPathComponent("frontend.cfg"),
                    atomically: true,
                    encoding: .utf8
                )
            }
        }

        var properties: [String: Any] = [:]
        if let specifier {
            properties["Factor"] = specifier
        }
        if let gender {
            properties["gender"] = gender
        }
        if let contentVersion {
            properties["ttsContentVersion"] = contentVersion
        }

        let plist: [String: Any] = ["MobileAssetProperties": properties]
        let plistData = try PropertyListSerialization.data(
            fromPropertyList: plist, format: .xml, options: 0
        )
        try plistData.write(to: asset.appendingPathComponent("Info.plist"))

        return asset
    }

    @Test("reads a voice from a well-formed asset bundle")
    func wellFormedBundle() throws {
        let asset = try makeAssetBundle(
            specifier: "com.apple.siri.tts.voice.de_DE.helena.neural.premium"
        )
        defer { try? FileManager.default.removeItem(at: asset.deletingLastPathComponent()) }

        let voice = systemVoice(atAssetDirectory: asset)
        #expect(voice != nil)
        #expect(voice?.name == "helena")
        #expect(voice?.language == "de-DE")
        #expect(voice?.type == "neural")
        #expect(voice?.footprint == "premium")
        #expect(voice?.gender == "female")
        #expect(voice?.version == 1328)
        #expect(voice?.path == asset.path)
    }

    @Test("rejects tacotron-only bundles")
    func tacotronOnlyBundle() throws {
        let asset = try makeAssetBundle(
            specifier: "com.apple.siri.tts.voice.de_DE.helena.neural.premium",
            modelDirectories: ["tacotron"]
        )
        defer { try? FileManager.default.removeItem(at: asset.deletingLastPathComponent()) }

        #expect(systemVoice(atAssetDirectory: asset) == nil)
    }

    @Test("accepts bundles with both tacotron and fastspeech2")
    func mixedModelBundle() throws {
        let asset = try makeAssetBundle(
            specifier: "com.apple.siri.tts.voice.de_DE.helena.neural.premium",
            modelDirectories: ["tacotron", "fastspeech2"]
        )
        defer { try? FileManager.default.removeItem(at: asset.deletingLastPathComponent()) }

        #expect(systemVoice(atAssetDirectory: asset) != nil)
    }

    @Test("rejects voices that force the hydra frontend")
    func hydraFrontendBundle() throws {
        // Voices whose frontend.cfg sets force_hydra_fe need the shared
        // com.apple.siri.tts.resource.<lang> bundle, which the in-process engine can't load on
        // macOS 26 — synthesis fails with `map::at: key not found`. They must be refused up front.
        let asset = try makeAssetBundle(
            specifier: "com.apple.siri.tts.voice.en_US.damon.natural.premium",
            frontendConfig: #"{"locale": "en-US", "force_hydra_fe": true}"#
        )
        defer { try? FileManager.default.removeItem(at: asset.deletingLastPathComponent()) }

        #expect(systemVoice(atAssetDirectory: asset) == nil)
    }

    @Test("accepts voices whose frontend config does not force hydra")
    func nonHydraFrontendBundle() throws {
        let asset = try makeAssetBundle(
            specifier: "com.apple.siri.tts.voice.en_IE.aidan.natural.premium",
            frontendConfig: #"{"locale": "en-IE", "force_hydra_fe": false}"#
        )
        defer { try? FileManager.default.removeItem(at: asset.deletingLastPathComponent()) }

        #expect(systemVoice(atAssetDirectory: asset) != nil)
    }

    @Test("rejects bundles without AssetData")
    func missingAssetData() throws {
        let asset = try makeAssetBundle(
            specifier: "com.apple.siri.tts.voice.de_DE.helena.neural.premium",
            includeAssetData: false
        )
        defer { try? FileManager.default.removeItem(at: asset.deletingLastPathComponent()) }

        #expect(systemVoice(atAssetDirectory: asset) == nil)
    }

    @Test("rejects resource assets in the same store")
    func resourceAsset() throws {
        let asset = try makeAssetBundle(
            specifier: "com.apple.siri.tts.resource.de_DE.nashville"
        )
        defer { try? FileManager.default.removeItem(at: asset.deletingLastPathComponent()) }

        #expect(systemVoice(atAssetDirectory: asset) == nil)
    }

    @Test("defaults gender and version when absent")
    func missingOptionalProperties() throws {
        let asset = try makeAssetBundle(
            specifier: "com.apple.siri.tts.voice.en_US.aria.neural.premium",
            gender: nil,
            contentVersion: nil
        )
        defer { try? FileManager.default.removeItem(at: asset.deletingLastPathComponent()) }

        let voice = systemVoice(atAssetDirectory: asset)
        #expect(voice?.gender == "unknown")
        #expect(voice?.version == 0)
    }

    @Test("discovery deduplicates identical voices across stores")
    func deduplication() throws {
        let assetA = try makeAssetBundle(
            specifier: "com.apple.siri.tts.voice.de_DE.helena.neural.premium"
        )
        let assetB = try makeAssetBundle(
            specifier: "com.apple.siri.tts.voice.de_DE.helena.neural.premium"
        )
        defer {
            try? FileManager.default.removeItem(at: assetA.deletingLastPathComponent())
            try? FileManager.default.removeItem(at: assetB.deletingLastPathComponent())
        }

        let voices = discoverSystemVoices(in: [
            assetA.deletingLastPathComponent().path,
            assetB.deletingLastPathComponent().path,
        ])
        #expect(voices.count == 1)
    }

    @Test("collapses type variants that share language, name, and footprint")
    func collapsesTypeVariants() throws {
        // Same name/language/footprint but different type and version: these map to a single
        // Home Assistant voice_id (name-language-footprint), so discovery must report one entry.
        let neural = try makeAssetBundle(
            specifier: "com.apple.siri.tts.voice.en_US.damon.neural.premium",
            contentVersion: "1301"
        )
        let natural = try makeAssetBundle(
            specifier: "com.apple.siri.tts.voice.en_US.damon.natural.premium",
            contentVersion: "5030"
        )
        defer {
            try? FileManager.default.removeItem(at: neural.deletingLastPathComponent())
            try? FileManager.default.removeItem(at: natural.deletingLastPathComponent())
        }

        let voices = discoverSystemVoices(in: [
            neural.deletingLastPathComponent().path,
            natural.deletingLastPathComponent().path,
        ])

        #expect(voices.count == 1)
        // The higher-version variant wins.
        #expect(voices.first?.version == 5030)
        #expect(voices.first?.type == "natural")
    }

    @Test("keeps voices that differ only by footprint")
    func keepsDistinctFootprints() throws {
        let premium = try makeAssetBundle(
            specifier: "com.apple.siri.tts.voice.en_US.aria.neural.premium"
        )
        let compact = try makeAssetBundle(
            specifier: "com.apple.siri.tts.voice.en_US.aria.neural.compact"
        )
        defer {
            try? FileManager.default.removeItem(at: premium.deletingLastPathComponent())
            try? FileManager.default.removeItem(at: compact.deletingLastPathComponent())
        }

        let voices = discoverSystemVoices(in: [
            premium.deletingLastPathComponent().path,
            compact.deletingLastPathComponent().path,
        ])
        #expect(voices.count == 2)
    }
}
