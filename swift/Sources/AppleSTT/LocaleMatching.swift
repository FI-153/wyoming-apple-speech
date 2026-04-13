import Foundation

/// Find the best matching locale from a list of supported locales.
///
/// Attempts an exact BCP-47 match first, then falls back to matching
/// by language code alone. Returns `nil` if no supported locale matches.
///
/// - Parameters:
///   - language: BCP-47 language code (e.g. "en", "en-US").
///   - supportedLocales: Locales to match against.
/// - Returns: The best matching locale, or `nil` if none matches.
func bestMatchingLocale(
    for language: String,
    in supportedLocales: [Locale]
) -> Locale? {
    let candidate = Locale(identifier: language)
    let candidateBCP47 = candidate.identifier(.bcp47)

    // Exact match by BCP-47 identifier.
    if let exact = supportedLocales.first(where: {
        $0.identifier(.bcp47) == candidateBCP47
    }) {
        return exact
    }

    // Closest match: first supported locale sharing the language code.
    if let match = supportedLocales.first(where: {
        $0.language.languageCode == candidate.language.languageCode
    }) {
        return match
    }

    return nil
}
