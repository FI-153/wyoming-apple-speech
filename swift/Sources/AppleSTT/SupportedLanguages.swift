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

/// Filter locales to those whose dictation model is actually installed.
///
/// Pre-Tahoe systems have no API to download speech models on demand, so
/// only locales that already support on-device recognition can transcribe.
/// Falls back to the unfiltered list when nothing is installed, so callers
/// never advertise an empty language list.
///
/// - Parameters:
///   - locales: Candidate locales, typically `SFSpeechRecognizer.supportedLocales()`.
///   - isInstalled: Returns true when the locale's on-device model is available.
/// - Returns: The installed subset, or all locales if that subset is empty.
func dictationReadyLocales(
    from locales: [Locale],
    isInstalled: (Locale) -> Bool
) -> [Locale] {
    let installed = locales.filter(isInstalled)
    return installed.isEmpty ? locales : installed
}
