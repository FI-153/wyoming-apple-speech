import Foundation
import Testing

@testable import apple_stt

@Suite("languageCodes")
struct SupportedLanguagesTests {

    @Test("extracts and deduplicates language codes from locales")
    func deduplication() {
        let locales = [
            Locale(identifier: "en-US"),
            Locale(identifier: "en-GB"),
            Locale(identifier: "fr-FR"),
            Locale(identifier: "de-DE"),
        ]
        let codes = languageCodes(from: locales)
        #expect(codes == ["de", "en", "fr"])
    }

    @Test("returns sorted codes")
    func sorted() {
        let locales = [
            Locale(identifier: "zh-Hans-CN"),
            Locale(identifier: "ar-SA"),
            Locale(identifier: "en-US"),
        ]
        let codes = languageCodes(from: locales)
        #expect(codes == ["ar", "en", "zh"])
    }

    @Test("empty locales returns empty array")
    func emptyInput() {
        let codes = languageCodes(from: [])
        #expect(codes.isEmpty)
    }

    @Test("handles locales with script subtags")
    func scriptSubtags() {
        let locales = [
            Locale(identifier: "zh-Hans-CN"),
            Locale(identifier: "zh-Hant-TW"),
        ]
        let codes = languageCodes(from: locales)
        #expect(codes == ["zh"])
    }
}

@Suite("dictationReadyLocales")
struct DictationReadyLocalesTests {

    @Test("keeps only locales the predicate marks installed")
    func filtersToInstalled() {
        let locales = [
            Locale(identifier: "de-DE"),
            Locale(identifier: "fr-FR"),
            Locale(identifier: "en-US"),
        ]
        let installed: Set<String> = ["de-DE", "en-US"]
        let ready = dictationReadyLocales(from: locales) {
            installed.contains($0.identifier(.bcp47))
        }
        #expect(ready.map { $0.identifier(.bcp47) } == ["de-DE", "en-US"])
    }

    @Test("falls back to all locales when none are installed")
    func fallbackWhenNoneInstalled() {
        let locales = [
            Locale(identifier: "de-DE"),
            Locale(identifier: "fr-FR"),
        ]
        let ready = dictationReadyLocales(from: locales) { _ in false }
        #expect(ready == locales)
    }

    @Test("empty input returns empty array")
    func emptyInput() {
        let ready = dictationReadyLocales(from: []) { _ in true }
        #expect(ready.isEmpty)
    }
}
