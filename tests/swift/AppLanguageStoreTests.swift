import Foundation

private func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() {
        fputs("FAIL: \(message)\n", stderr)
        exit(1)
    }
}

@main
struct AppLanguageStoreTests {
    @MainActor
    static func main() {
        expect(
            AppLanguageResolver.resolvedResourceName(
                for: .system,
                preferredLanguages: ["zh-Hans-CN", "en"]
            ) == "zh-Hans",
            "Simplified Chinese preferences should resolve to zh-Hans"
        )
        expect(
            AppLanguageResolver.resolvedResourceName(
                for: .system,
                preferredLanguages: ["zh-Hant-TW", "fr-FR"]
            ) == "en",
            "Unsupported languages should fall back to English"
        )
        expect(
            AppLanguageResolver.resolvedResourceName(
                for: .zhHans,
                preferredLanguages: ["en-US"]
            ) == "zh-Hans",
            "An explicit language should override system preferences"
        )

        let suiteName = "modeldial.app-language-tests.\(UUID().uuidString)"
        guard let defaults = UserDefaults(suiteName: suiteName) else {
            fputs("FAIL: unable to create isolated defaults\n", stderr)
            exit(1)
        }
        defaults.removePersistentDomain(forName: suiteName)
        let store = AppLanguageStore(defaults: defaults, observesLocaleChanges: false)
        expect(store.language == .system, "the default preference should follow the system")
        expect(store.select(.en), "selecting a new language should report a change")
        expect(defaults.string(forKey: AppLanguageResolver.key) == "en", "selection should persist")
        expect(store.locale.identifier.hasPrefix("en"), "selected locale should update immediately")
        expect(!store.select(.en), "selecting the current language should be a no-op")
        defaults.removePersistentDomain(forName: suiteName)
        print("AppLanguageStore tests passed")
    }
}
