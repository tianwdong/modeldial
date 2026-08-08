import Foundation

private var failureCount = 0

private func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() {
        failureCount += 1
        fputs("FAIL: \(message)\n", stderr)
    }
}

private func verifyUpdateCheckPresentations() {
    let idle = UpdateCheckPresenter.presentation(for: .idle)
    expect(idle.text == "尚未检查更新", "idle should explain that no check has run")
    expect(idle.tone == .neutral, "idle should be neutral")

    let checking = UpdateCheckPresenter.presentation(for: .checking)
    expect(checking.text == "正在检查更新", "checking should provide immediate feedback")
    expect(checking.tone == .active, "checking should use the active tone")

    let current = UpdateCheckPresenter.presentation(for: .upToDate)
    expect(current.text == "当前已是最新版本", "confirmed latest version should be explicit")
    expect(current.symbolName == "checkmark.circle.fill", "latest version should use success icon")
    expect(current.tone == .success, "latest version should use success tone")

    let unavailable = UpdateCheckPresenter.presentation(for: .failed)
    expect(unavailable.text == "暂时无法检查更新", "feed failures must not be presented as current")
    expect(unavailable.tone == .failure, "feed failures should be visible")

    let unsupported = UpdateCheckPresenter.presentation(for: .unsupportedSystem)
    expect(unsupported.text == "当前系统无法安装可用更新", "unsupported systems need a distinct explanation")
    expect(unsupported.tone == .warning, "unsupported systems should be warnings")
}

@main
struct UpdateCheckPresenterTests {
    static func main() {
        verifyUpdateCheckPresentations()
        if failureCount > 0 {
            exit(1)
        }
        print("Update check presenter tests passed")
    }
}
