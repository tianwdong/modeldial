import AppKit
import SwiftUI

final class IslandHostingView: NSHostingView<IslandRootView> {
    let model: IslandModel

    init(rootView: IslandRootView, model: IslandModel) {
        self.model = model
        super.init(rootView: rootView)
    }

    @MainActor required dynamic init(rootView: IslandRootView) {
        fatalError("Use init(rootView:model:)")
    }

    @MainActor required dynamic init?(coder: NSCoder) {
        fatalError("init(coder:) not used")
    }

    override func hitTest(_ point: NSPoint) -> NSView? {
        let bounds = bounds
        let size = model.interactionSize
        let rect = NSRect(
            x: bounds.midX - size.width / 2,
            y: bounds.maxY - size.height,
            width: size.width,
            height: size.height
        )
        return rect.contains(point) ? super.hitTest(point) : nil
    }

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}
