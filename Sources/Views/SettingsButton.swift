import SwiftUI

struct SettingsButton: View {
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: "gearshape")
                .symbolRenderingMode(.hierarchical)
        }
        .buttonStyle(IslandIconButtonStyle())
        .help(L10n.tr("打开设置"))
    }
}
