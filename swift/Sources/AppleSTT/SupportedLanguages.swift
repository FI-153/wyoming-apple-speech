import Foundation

/// Extract deduplicated, sorted language codes from a list of locales.
///
/// Strips region and script subtags, keeping only the base language code.
/// For example, ["en-US", "en-GB", "fr-FR"] becomes ["en", "fr"].
///
/// - Parameter locales: Locales to extract language codes from.
/// - Returns: Sorted array of unique language code strings.
func languageCodes(from locales: [Locale]) -> [String] {
    var seen = Set<String>()
    for locale in locales {
        if let code = locale.language.languageCode?.identifier, !code.isEmpty {
            seen.insert(code)
        }
    }
    return seen.sorted()
}
