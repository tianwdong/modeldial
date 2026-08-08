import AppKit
import Combine
import SwiftUI

@MainActor
final class SelectionWindowController {
    private static let hostWindowSize = CGSize(width: 1120, height: 540)
    private static let compactHoverHitInset: CGFloat = 6
    private let window: BorderlessFloatingWindow
    private let model: IslandModel
    private let store: AppSessionStore
    private let host: IslandHostingView
    private var cancellables: Set<AnyCancellable> = []
    private var localMouseMonitor: Any?
    private var globalMouseMonitor: Any?
    private var screenChangeObserver: NSObjectProtocol?
    private var occlusionObserver: NSObjectProtocol?

    init() {
        DebugLog.write("SelectionWindowController.init start")
        store = AppSessionStore.shared
        model = IslandModel(notch: NotchInfo.detect(from: Self.targetScreen()))
        window = BorderlessFloatingWindow(
            contentRect: NSRect(origin: .zero, size: Self.hostWindowSize),
            styleMask: [.borderless, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        window.isOpaque = false
        window.backgroundColor = .clear
        window.hasShadow = false
        window.level = .popUpMenu
        window.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle]
        window.isMovable = false
        window.ignoresMouseEvents = true

        host = IslandHostingView(rootView: IslandRootView(store: store, model: model), model: model)
        host.frame = NSRect(origin: .zero, size: Self.hostWindowSize)
        host.autoresizingMask = [.width, .height]
        window.contentView = host

        bind()
        DebugLog.write("SelectionWindowController.init done")
    }

    func show() {
        DebugLog.write("SelectionWindowController.show")
        installMouseTracking()
        observeScreenChanges()
        reposition()
        window.level = .popUpMenu
        window.ignoresMouseEvents = true
        window.orderFrontRegardless()
        observeWindowVisibility()
        updateGlanceVisibility()
        NSApp.activate(ignoringOtherApps: true)
        DispatchQueue.main.async { [weak self] in
            DebugLog.write("SelectionWindowController.show refresh dispatch")
            self?.store.refresh()
        }
    }

    private func bind() {
        model.$state
            .sink { [weak self] _ in
                self?.window.displayIfNeeded()
            }
            .store(in: &cancellables)

        model.$isCompactSessionPanelVisible
            .sink { [weak self] _ in
                self?.window.displayIfNeeded()
                self?.updateMouseEventsBasedOnCursor()
            }
            .store(in: &cancellables)

        IslandTargetDisplayStore.shared.$choice
            .dropFirst()
            .sink { [weak self] _ in
                Task { @MainActor in self?.reposition() }
            }
            .store(in: &cancellables)
    }

    private func reposition() {
        guard let screen = Self.targetScreen() else { return }
        model.updateNotch(NotchInfo.detect(from: screen))
        let size = Self.hostWindowSize
        let frame = screen.frame
        let x = frame.midX - size.width / 2
        let y = frame.maxY - size.height
        DebugLog.write("reposition screen=\(screen.localizedName) frame=\(frame) target=(\(x),\(y),\(size.width),\(size.height))")
        window.setFrame(NSRect(x: x, y: y, width: size.width, height: size.height), display: true)
    }

    private static func targetScreen() -> NSScreen? {
        DisplayInfo.currentTarget()?.screen
            ?? NSScreen.main
            ?? NSScreen.screens.first
    }

    private func observeScreenChanges() {
        guard screenChangeObserver == nil else { return }
        screenChangeObserver = NotificationCenter.default.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                self?.reposition()
                self?.updateGlanceVisibility()
            }
        }
    }

    private func observeWindowVisibility() {
        guard occlusionObserver == nil else { return }
        occlusionObserver = NotificationCenter.default.addObserver(
            forName: NSWindow.didChangeOcclusionStateNotification,
            object: window,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in self?.updateGlanceVisibility() }
        }
    }

    func updateGlanceVisibility() {
        let hasTargetDisplay = Self.targetScreen() != nil
        let isOcclusionVisible = window.occlusionState.isEmpty
            || window.occlusionState.contains(.visible)
        store.setGlanceActuallyVisible(window.isVisible && hasTargetDisplay && isOcclusionVisible)
    }

    private func installMouseTracking() {
        guard globalMouseMonitor == nil, localMouseMonitor == nil else { return }
        let handler: (NSEvent) -> Void = { [weak self] _ in
            Task { @MainActor in
                self?.updateMouseEventsBasedOnCursor()
            }
        }
        globalMouseMonitor = NSEvent.addGlobalMonitorForEvents(matching: [.mouseMoved], handler: handler)
        localMouseMonitor = NSEvent.addLocalMonitorForEvents(matching: [.mouseMoved]) { event in
            handler(event)
            return event
        }
    }

    private func updateMouseEventsBasedOnCursor() {
        let cursor = NSEvent.mouseLocation
        let frame = window.frame
        let local = NSPoint(x: cursor.x - frame.minX, y: cursor.y - frame.minY)
        let interactionSize = model.interactionSize
        let islandRect = NSRect(
            x: frame.width / 2 - interactionSize.width / 2,
            y: frame.height - interactionSize.height,
            width: interactionSize.width,
            height: interactionSize.height
        )
        let hitInset = model.state == .compact ? Self.compactHoverHitInset : 0
        let interactiveIslandRect = islandRect.insetBy(dx: -hitInset, dy: -hitInset)
        let inside = interactiveIslandRect.contains(local)
        model.setPointerInsideInteractionArea(inside)
        let shouldIgnoreMouseEvents = !inside
        guard window.ignoresMouseEvents != shouldIgnoreMouseEvents else { return }
        window.ignoresMouseEvents = shouldIgnoreMouseEvents
    }

    deinit {
        if let globalMouseMonitor { NSEvent.removeMonitor(globalMouseMonitor) }
        if let localMouseMonitor { NSEvent.removeMonitor(localMouseMonitor) }
        if let screenChangeObserver { NotificationCenter.default.removeObserver(screenChangeObserver) }
        if let occlusionObserver { NotificationCenter.default.removeObserver(occlusionObserver) }
    }
}
