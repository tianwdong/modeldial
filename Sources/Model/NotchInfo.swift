import AppKit

struct NotchInfo {
    let width: CGFloat
    let height: CGFloat
    let hasNotch: Bool

    static func detect(from screen: NSScreen?) -> NotchInfo {
        guard let screen else {
            return NotchInfo(width: 200, height: fallbackMenuBarHeight(), hasNotch: false)
        }

        let safeTop = screen.safeAreaInsets.top
        let visibleHeight = menuBarHeight(of: screen)
        if safeTop > 0 {
            let leftWidth = screen.auxiliaryTopLeftArea?.width ?? 0
            let rightWidth = screen.auxiliaryTopRightArea?.width ?? 0
            let width: CGFloat = (leftWidth > 0 && rightWidth > 0)
                ? screen.frame.width - leftWidth - rightWidth
                : 200
            return NotchInfo(width: width, height: visibleHeight, hasNotch: true)
        }

        return NotchInfo(width: 200, height: visibleHeight, hasNotch: false)
    }

    private static func menuBarHeight(of screen: NSScreen) -> CGFloat {
        let fromVisibleFrame = screen.frame.maxY - screen.visibleFrame.maxY
        if fromVisibleFrame > 0 { return fromVisibleFrame }
        if screen.safeAreaInsets.top > 0 { return screen.safeAreaInsets.top }
        return fallbackMenuBarHeight()
    }

    private static func fallbackMenuBarHeight() -> CGFloat {
        let thickness = NSStatusBar.system.thickness
        return thickness > 0 ? thickness : 24
    }
}
