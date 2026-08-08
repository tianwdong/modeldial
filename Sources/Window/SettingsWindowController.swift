import AppKit
import Combine

enum SettingsDestination: Equatable {
    case modelIngress
}

@MainActor
final class SettingsWindowController: ObservableObject {
    static let shared = SettingsWindowController()
    @Published private(set) var destinationRequest: SettingsDestination?
    private var openWindowAction: (() -> Void)?

    private init() {}

    func registerOpenWindow(_ action: @escaping () -> Void) {
        openWindowAction = action
    }

    func show(destination: SettingsDestination? = nil) {
        DebugLog.write("SettingsWindowController.show begin")
        destinationRequest = destination
        NSApp.activate(ignoringOtherApps: true)
        openWindowAction?()
        DebugLog.write("SettingsWindowController.show dispatched openWindow")
        DispatchQueue.main.async {
            SelectionSettingsStore.shared.reload()
            DebugLog.write("SettingsWindowController.show after reload dispatch")
        }
    }

    func consumeDestination(_ destination: SettingsDestination) {
        guard destinationRequest == destination else { return }
        destinationRequest = nil
    }
}
