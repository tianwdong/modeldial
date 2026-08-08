import SwiftUI

@MainActor
final class IslandModel: ObservableObject {
    private static let menuBarCompactWidth: CGFloat = 200
    private static let menuBarCompactHeight: CGFloat = 28
    private static let notchCompactSideSlotWidth: CGFloat = 112

    enum State {
        case compact
        case expanded
    }

    @Published var state: State = .compact
    @Published var notch: NotchInfo
    @Published private(set) var isCompactSessionPanelVisible = false
    @Published private(set) var compactSessionPanelHeight: CGFloat = 0
    @Published private(set) var isPointerInsideInteractionArea = false

    let pillSlotWidth: CGFloat = 78
    let expandedWidth: CGFloat = 1080
    let expandedBaseContentHeight: CGFloat = 460

    var compactSideSlotWidth: CGFloat {
        Self.notchCompactSideSlotWidth
    }

    var size: CGSize {
        switch state {
        case .compact:
            return CGSize(width: compactWidth, height: compactHeight)
        case .expanded:
            return expandedSize
        }
    }

    var expandedSize: CGSize {
        CGSize(
            width: expandedWidth,
            height: expandedBaseContentHeight + notch.height
        )
    }

    var compactSessionPanelWidth: CGFloat {
        notch.hasNotch ? max(compactWidth, 420) : 360
    }

    var interactionSize: CGSize {
        guard state == .compact, isCompactSessionPanelVisible else { return size }
        return CGSize(
            width: compactSessionPanelWidth,
            height: size.height + compactSessionPanelHeight
        )
    }

    init(notch: NotchInfo) {
        self.notch = notch
    }

    func setState(_ newState: State) {
        guard state != newState else { return }
        if newState == .expanded {
            setCompactSessionPanel(visible: false)
        }
        state = newState
    }

    func setCompactSessionPanel(visible: Bool, height: CGFloat = 0) {
        isCompactSessionPanelVisible = visible
        compactSessionPanelHeight = visible ? max(0, height) : 0
    }

    func setPointerInsideInteractionArea(_ inside: Bool) {
        guard isPointerInsideInteractionArea != inside else { return }
        isPointerInsideInteractionArea = inside
    }

    func updateNotch(_ newNotch: NotchInfo) {
        guard newNotch.width != notch.width || newNotch.height != notch.height || newNotch.hasNotch != notch.hasNotch else {
            return
        }
        notch = newNotch
    }

    private var compactWidth: CGFloat {
        notch.hasNotch ? notch.width + compactSideSlotWidth * 2 : Self.menuBarCompactWidth
    }

    var compactHeight: CGFloat {
        notch.hasNotch ? notch.height : Self.menuBarCompactHeight
    }

}
