import Foundation
import ServiceManagement

@MainActor
final class LaunchAtLoginStore: ObservableObject {
    static let shared = LaunchAtLoginStore()

    @Published private(set) var isEnabled = false
    @Published private(set) var errorMessage: String?

    private init() {
        refresh()
    }

    func refresh() {
        if #available(macOS 13.0, *) {
            isEnabled = SMAppService.mainApp.status == .enabled
        } else {
            isEnabled = false
        }
    }

    func setEnabled(_ enabled: Bool) {
        guard #available(macOS 13.0, *) else {
            errorMessage = "当前系统版本不支持登录时启动"
            return
        }
        do {
            if enabled {
                try SMAppService.mainApp.register()
            } else {
                try SMAppService.mainApp.unregister()
            }
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
        refresh()
    }
}
