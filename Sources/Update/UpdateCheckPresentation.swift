import Foundation

enum UpdateCheckState: Equatable {
    case idle
    case checking
    case upToDate
    case updateAvailable
    case notConfigured
    case unsupportedSystem
    case failed

    var isChecking: Bool {
        self == .checking
    }
}

enum UpdateCheckPresenter {
    enum Tone: Equatable {
        case neutral
        case active
        case success
        case warning
        case failure
    }

    struct Presentation: Equatable {
        let text: String
        let symbolName: String
        let tone: Tone
    }

    static func presentation(for state: UpdateCheckState) -> Presentation {
        switch state {
        case .idle:
            return Presentation(
                text: L10n.Update.notChecked,
                symbolName: "minus.circle",
                tone: .neutral
            )
        case .checking:
            return Presentation(
                text: L10n.Update.checking,
                symbolName: "arrow.triangle.2.circlepath",
                tone: .active
            )
        case .upToDate:
            return Presentation(
                text: L10n.Update.upToDate,
                symbolName: "checkmark.circle.fill",
                tone: .success
            )
        case .updateAvailable:
            return Presentation(
                text: L10n.Update.updateAvailable,
                symbolName: "arrow.down.circle.fill",
                tone: .success
            )
        case .notConfigured:
            return Presentation(
                text: L10n.Update.notConfigured,
                symbolName: "exclamationmark.triangle.fill",
                tone: .warning
            )
        case .unsupportedSystem:
            return Presentation(
                text: L10n.Update.unsupportedSystem,
                symbolName: "exclamationmark.triangle.fill",
                tone: .warning
            )
        case .failed:
            return Presentation(
                text: L10n.Update.checkFailed,
                symbolName: "exclamationmark.triangle.fill",
                tone: .failure
            )
        }
    }
}
