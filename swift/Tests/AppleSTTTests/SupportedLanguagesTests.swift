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
