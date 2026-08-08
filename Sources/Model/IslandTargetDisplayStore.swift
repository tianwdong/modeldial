import Foundation
import SwiftUI

@MainActor
final class IslandTargetDisplayStore: ObservableObject {
    static let shared = IslandTargetDisplayStore()

    enum Choice: Equatable {
        case auto
        case stable(id: String)

        var rawValue: String {
            switch self {
            case .auto:
                return "auto"
            case .stable(let id):
                return id
            }
        }

        init(rawValue: String?) {
            switch rawValue {
            case nil, "auto", "":
                self = .auto
            case let id?:
                self = .stable(id: id)
            }
        }
    }

    private static let key = "modeldial.targetDisplay"
    private static let legacyKey = "ModelPilot.targetDisplay"
    private static let legacyBundleID = "dev.codexselectionisland.app"

    @Published var choice: Choice {
        didSet { UserDefaults.standard.set(choice.rawValue, forKey: Self.key) }
    }

    private init() {
        let defaults = UserDefaults.standard
        let legacyDomain = defaults.persistentDomain(forName: Self.legacyBundleID)
        let raw = defaults.string(forKey: Self.key)
            ?? defaults.string(forKey: Self.legacyKey)
            ?? legacyDomain?[Self.legacyKey] as? String
        if defaults.string(forKey: Self.key) == nil, let raw {
            defaults.set(raw, forKey: Self.key)
        }
        self.choice = Choice(rawValue: raw)
    }
}
