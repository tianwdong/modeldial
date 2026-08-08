import AppKit
import CoreGraphics

struct DisplayInfo: Identifiable {
    var id: String { stableID }

    let screen: NSScreen
    let displayID: CGDirectDisplayID
    let stableID: String
    let name: String
    let isBuiltin: Bool
    let notch: NotchInfo

    static func all() -> [DisplayInfo] {
        NSScreen.screens.compactMap { Self.make(from: $0) }
    }

    @MainActor
    static func currentTarget() -> DisplayInfo? {
        let choice = IslandTargetDisplayStore.shared.choice
        let all = Self.all()
        switch choice {
        case .auto:
            return Self.autoPick(from: all)
        case .stable(let id):
            return all.first(where: { $0.stableID == id }) ?? Self.autoPick(from: all)
        }
    }

    private static func autoPick(from all: [DisplayInfo]) -> DisplayInfo? {
        all.first(where: { $0.screen.frame.contains(NSEvent.mouseLocation) })
            ?? all.first(where: { $0.screen == NSScreen.main })
            ?? all.first(where: \.notch.hasNotch)
            ?? all.first
    }

    private static func make(from screen: NSScreen) -> DisplayInfo? {
        guard let displayID = screen.deviceDescription[
            NSDeviceDescriptionKey("NSScreenNumber")
        ] as? CGDirectDisplayID else {
            return nil
        }
        guard let unmanaged = CGDisplayCreateUUIDFromDisplayID(displayID) else {
            return nil
        }
        let cfuuid = unmanaged.takeRetainedValue()
        guard let stable = CFUUIDCreateString(nil, cfuuid) as String? else {
            return nil
        }
        return DisplayInfo(
            screen: screen,
            displayID: displayID,
            stableID: stable,
            name: screen.localizedName,
            isBuiltin: CGDisplayIsBuiltin(displayID) != 0,
            notch: NotchInfo.detect(from: screen)
        )
    }
}
