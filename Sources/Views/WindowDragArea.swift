import AppKit
import SwiftUI

struct WindowDragArea: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView {
        DragView()
    }

    func updateNSView(_ nsView: NSView, context: Context) {}

    static func dismantleNSView(_ nsView: NSView, coordinator: Void) {}
}

private final class DragView: NSView {
    override var mouseDownCanMoveWindow: Bool { true }
}
