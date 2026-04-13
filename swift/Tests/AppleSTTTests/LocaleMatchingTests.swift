import Foundation
import Testing

@testable import apple_stt

@Suite("bestMatchingLocale")
struct LocaleMatchingTests {

    let supportedLocales: [Locale] = [
        Locale(identifier: "en-US"),
        Locale(identifier: "en-GB"),
        Locale(identifier: "fr-FR"),
        Locale(identifier: "de-DE"),
        Locale(identifier: "es-ES"),
        Locale(identifier: "zh-Hans-CN"),
    ]

    @Test("exact BCP-47 match returns the matching locale")
    func exactMatch() {
        let result = bestMatchingLocale(for: "en-US", in: supportedLocales)
        #expect(result?.identifier(.bcp47) == "en-US")
    }

    @Test("underscore separator normalizes to exact match")
    func underscoreSeparator() {
        let result = bestMatchingLocale(for: "en_US", in: supportedLocales)
        #expect(result?.identifier(.bcp47) == "en-US")
    }

    @Test("bare language code matches first locale with same language")
    func bareLanguageCode() {
        let result = bestMatchingLocale(for: "en", in: supportedLocales)
        #expect(result != nil)
        #expect(result?.language.languageCode?.identifier == "en")
    }

    @Test("bare language code fr matches fr-FR")
    func bareLanguageCodeFrench() {
        let result = bestMatchingLocale(for: "fr", in: supportedLocales)
        #expect(result?.identifier(.bcp47) == "fr-FR")
    }

    @Test("unsupported language returns nil")
    func unsupportedLanguage() {
        let result = bestMatchingLocale(for: "xx", in: supportedLocales)
        #expect(result == nil)
    }

    @Test("empty language string returns nil")
    func emptyLanguage() {
        let result = bestMatchingLocale(for: "", in: supportedLocales)
        #expect(result == nil)
    }

    @Test("empty supported locales returns nil")
    func emptySupportedLocales() {
        let result = bestMatchingLocale(for: "en", in: [])
        #expect(result == nil)
    }

    @Test("language code match picks first from list")
    func languageCodeMatchOrder() {
        let result = bestMatchingLocale(for: "en", in: supportedLocales)
        // "en-US" comes before "en-GB" in the list, so it should be picked.
        #expect(result?.identifier(.bcp47) == "en-US")
    }

    @Test("script tag zh-Hans matches zh-Hans-CN")
    func scriptTag() {
        let result = bestMatchingLocale(for: "zh-Hans", in: supportedLocales)
        #expect(result != nil)
        #expect(result?.language.languageCode?.identifier == "zh")
    }
}
