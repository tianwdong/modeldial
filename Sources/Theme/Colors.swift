import SwiftUI

enum IslandColor {
    static let cobalt = Color(red: 0/255, green: 71/255, blue: 171/255)
    static let claude = Color(red: 204/255, green: 120/255, blue: 92/255)
    static let codex = Color(red: 90/255, green: 168/255, blue: 240/255)
    static let interaction = Color(red: 134/255, green: 168/255, blue: 196/255)
    static let liveTeal = Color(red: 67/255, green: 213/255, blue: 150/255)
    static let alertAmber = Color(red: 220/255, green: 166/255, blue: 74/255)
    static let alertRed = Color(red: 230/255, green: 98/255, blue: 104/255)
    static let canvas = Color(red: 6/255, green: 7/255, blue: 8/255)
    static let chrome = Color(red: 9/255, green: 11/255, blue: 14/255)
    static let panel = Color(red: 7/255, green: 9/255, blue: 11/255)
    static let panelRaised = Color(red: 15/255, green: 19/255, blue: 24/255)
    static let panelHover = Color(red: 17/255, green: 22/255, blue: 28/255)
    static let panelBorder = Color(red: 27/255, green: 32/255, blue: 38/255)
    static let openRouter = Color(red: 119/255, green: 204/255, blue: 138/255)
    static let endpoint = Color(red: 95/255, green: 151/255, blue: 255/255)
}

enum IslandVisual {
    static let primaryText = Color(red: 232/255, green: 235/255, blue: 238/255)
    static let secondaryText = Color(red: 173/255, green: 180/255, blue: 188/255)
    static let tertiaryText = Color(red: 118/255, green: 127/255, blue: 137/255)
    static let hintText = Color(red: 83/255, green: 92/255, blue: 102/255)
    static let interactionText = IslandColor.interaction
    static let primaryActionText = Color(red: 9/255, green: 16/255, blue: 22/255)

    static let hairline = IslandColor.panelBorder
    static let shellSurface = IslandColor.panel
    static let summarySurface = Color(red: 13/255, green: 17/255, blue: 22/255)
    static let evidenceSurface = Color(red: 9/255, green: 12/255, blue: 16/255)
    static let interactionSurface = IslandColor.panelHover
    static let contentTopHighlight = Color.white.opacity(0.035)
    static let surfaceSubtle = IslandColor.panelRaised
    static let surfaceRaised = IslandColor.panelHover
    static let surfaceStrong = Color(red: 24/255, green: 38/255, blue: 48/255)
    static let workspaceSurface = evidenceSurface
    static let workspaceBorder = hairline
    static let floatingSurface = Color.black.opacity(0.82)
    static let selectedSurface = Color(red: 24/255, green: 38/255, blue: 48/255)
    static let selectedBorder = IslandColor.interaction.opacity(0.42)
    static let controlFill = Color(red: 11/255, green: 14/255, blue: 18/255)

    static func panelBackground(reduceTransparency: Bool) -> Color {
        reduceTransparency ? .black : shellSurface
    }

    static func border(increasedContrast: Bool) -> Color {
        increasedContrast ? Color.white.opacity(0.22) : hairline
    }
}
