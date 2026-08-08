import Combine
import Foundation

enum AppLanguage: String, CaseIterable, Hashable {
    case system
    case zhHans = "zh-Hans"
    case en

    var menuLabel: String {
        switch self {
        case .system:
            return L10n.Language.followSystem
        case .zhHans:
            return "简体中文"
        case .en:
            return "English"
        }
    }
}

enum AppLanguageResolver {
    static let key = "modeldial.app.language"

    static func current(defaults: UserDefaults = .standard) -> AppLanguage {
        guard let rawValue = defaults.string(forKey: key) else { return .system }
        return AppLanguage(rawValue: rawValue) ?? .system
    }

    static func resolvedResourceName(
        for language: AppLanguage,
        preferredLanguages: [String] = Locale.preferredLanguages
    ) -> String {
        switch language {
        case .zhHans:
            return "zh-Hans"
        case .en:
            return "en"
        case .system:
            for preference in preferredLanguages {
                let normalized = preference.lowercased().replacingOccurrences(of: "_", with: "-")
                if normalized == "zh"
                    || normalized.hasPrefix("zh-hans")
                    || normalized.hasPrefix("zh-cn")
                    || normalized.hasPrefix("zh-sg") {
                    return "zh-Hans"
                }
                if normalized == "en" || normalized.hasPrefix("en-") {
                    return "en"
                }
            }
            return "en"
        }
    }

    static var locale: Locale {
        locale(for: current())
    }

    static func locale(for language: AppLanguage) -> Locale {
        Locale(identifier: resolvedResourceName(for: language))
    }

    static var bundle: Bundle {
        localizationBundle(for: current())
    }

    static func localizationBundle(for language: AppLanguage) -> Bundle {
        let resourceName = resolvedResourceName(for: language)
        guard let path = Bundle.main.path(forResource: resourceName, ofType: "lproj"),
              let bundle = Bundle(path: path) else {
            return .main
        }
        return bundle
    }
}

@MainActor
final class AppLanguageStore: ObservableObject {
    static let shared = AppLanguageStore()

    @Published private(set) var language: AppLanguage

    private let defaults: UserDefaults
    private var localeObserver: NSObjectProtocol?

    init(
        defaults: UserDefaults = .standard,
        observesLocaleChanges: Bool = true
    ) {
        self.defaults = defaults
        language = AppLanguageResolver.current(defaults: defaults)
        if observesLocaleChanges {
            localeObserver = NotificationCenter.default.addObserver(
                forName: NSLocale.currentLocaleDidChangeNotification,
                object: nil,
                queue: .main
            ) { [weak self] _ in
                Task { @MainActor in
                    guard self?.language == .system else { return }
                    self?.objectWillChange.send()
                }
            }
        }
    }

    var locale: Locale {
        AppLanguageResolver.locale(for: language)
    }

    @discardableResult
    func select(_ newLanguage: AppLanguage) -> Bool {
        guard language != newLanguage else { return false }
        language = newLanguage
        defaults.set(newLanguage.rawValue, forKey: AppLanguageResolver.key)
        return true
    }

    deinit {
        if let localeObserver {
            NotificationCenter.default.removeObserver(localeObserver)
        }
    }
}
