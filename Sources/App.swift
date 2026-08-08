import AppKit
import SwiftUI

@main
struct ModeldialApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate

    var body: some Scene {
        Window("modeldial Settings", id: "settings") {
            SettingsWindowContent()
        }
        .defaultSize(width: 1160, height: 560)
        .windowStyle(.hiddenTitleBar)
        .windowResizability(.contentMinSize)
    }
}

private struct SettingsWindowContent: View {
    @Environment(\.openWindow) private var openWindow
    @ObservedObject private var appLanguage = AppLanguageStore.shared

    var body: some View {
        SettingsView()
            .environment(\.locale, appLanguage.locale)
            .onAppear {
                SettingsWindowController.shared.registerOpenWindow { [openWindow] in
                    openWindow(id: "settings")
                }
            }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    var controller: SelectionWindowController?
    private let appNotifications = NotificationCenter.default
    private let workspaceNotifications = NSWorkspace.shared.notificationCenter

    func applicationDidFinishLaunching(_ notification: Notification) {
        DebugLog.reset()
        DebugLog.write("applicationDidFinishLaunching")
        NSApp.appearance = NSAppearance(named: .darkAqua)
        _ = RecommendationNotificationEngine.shared
        UpdaterController.shared.startIfConfigured()
        NSApp.setActivationPolicy(.accessory)
        controller = SelectionWindowController()
        controller?.show()
        DispatchQueue.main.async {
            Self.hideInitialSettingsWindow()
        }
        workspaceNotifications.addObserver(
            self,
            selector: #selector(workspaceWillSleep),
            name: NSWorkspace.willSleepNotification,
            object: nil
        )
        workspaceNotifications.addObserver(
            self,
            selector: #selector(workspaceDidWake),
            name: NSWorkspace.didWakeNotification,
            object: nil
        )
        workspaceNotifications.addObserver(
            self,
            selector: #selector(workspaceSessionDidBecomeActive),
            name: NSWorkspace.sessionDidBecomeActiveNotification,
            object: nil
        )
        appNotifications.addObserver(
            self,
            selector: #selector(appDidBecomeActive),
            name: NSApplication.didBecomeActiveNotification,
            object: nil
        )
    }

    @objc private func workspaceWillSleep() {
        AppSessionStore.shared.setGlanceActuallyVisible(false)
        AppSessionStore.shared.suspendGlanceBoundaryRefresh()
    }

    @objc private func workspaceDidWake() {
        resumeAfterLifecycleChange()
    }

    @objc private func workspaceSessionDidBecomeActive() {
        resumeAfterLifecycleChange()
    }

    @objc private func appDidBecomeActive() {
        resumeAfterLifecycleChange()
    }

    private func resumeAfterLifecycleChange() {
        controller?.updateGlanceVisibility()
        AppSessionStore.shared.resumeGlanceBoundaryRefresh()
    }

    func applicationWillTerminate(_ notification: Notification) {
        appNotifications.removeObserver(self)
        workspaceNotifications.removeObserver(self)
        AppSessionStore.shared.suspendGlanceBoundaryRefresh()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    private static func hideInitialSettingsWindow() {
        for window in NSApp.windows where window.styleMask.contains(.titled) {
            window.orderOut(nil)
        }
    }
}
