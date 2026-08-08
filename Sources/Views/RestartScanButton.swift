import SwiftUI

struct RestartScanButton: View {
    @State private var showsRestartConfirmation = false

    var body: some View {
        Button {
            showsRestartConfirmation = true
        } label: {
            Text("重新扫描")
        }
        .buttonStyle(IslandActionButtonStyle(.secondary))
        .help(L10n.tr("放弃当前进度并重新扫描"))
        .alert(L10n.tr("重新开始本轮扫描？"), isPresented: $showsRestartConfirmation) {
            Button(L10n.tr("重新扫描"), role: .destructive) {
                AppSessionStore.shared.restartManualScan()
            }
            Button(L10n.tr("取消"), role: .cancel) {}
        } message: {
            Text(L10n.tr("这会放弃当前未完成的进度，并从第一题重新开始。"))
        }
    }
}
