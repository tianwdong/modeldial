import AppKit
import SwiftUI

struct QuitButton: View {
    @State private var showsQuitConfirmation = false

    var body: some View {
        Button {
            showsQuitConfirmation = true
        } label: {
            Image(systemName: "power")
                .symbolRenderingMode(.hierarchical)
        }
        .buttonStyle(IslandIconButtonStyle())
        .help(L10n.tr("退出 modeldial"))
        .alert(L10n.tr("退出 modeldial？"), isPresented: $showsQuitConfirmation) {
            Button(L10n.tr("退出"), role: .destructive) {
                NSApp.terminate(nil)
            }
            Button(L10n.tr("取消"), role: .cancel) {}
        } message: {
            Text(L10n.tr("确定要退出吗？"))
        }
    }
}
