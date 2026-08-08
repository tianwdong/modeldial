import SwiftUI

enum Typography {
    // Product typography uses explicit roles so native controls and custom surfaces
    // do not silently inherit different macOS text sizes.
    static let heroModel = Font.system(size: 24, weight: .bold)
    static let heroDecision = Font.system(size: 19, weight: .semibold)
    static let pageTitle = Font.system(size: 24, weight: .semibold)
    static let sectionTitle = Font.system(size: 16, weight: .semibold)
    static let metricValue = Font.system(size: 16, weight: .semibold, design: .monospaced)
    static let tableValue = Font.system(size: 16, weight: .semibold, design: .monospaced)

    static let bigNumber = Font.system(size: 40, weight: .semibold, design: .monospaced)
    static let scoreValue = Font.system(size: 34, weight: .bold, design: .rounded)
    static let chartValue = Font.system(size: 16, weight: .semibold, design: .monospaced)
    static let previewNumber = Font.system(size: 16, weight: .semibold, design: .monospaced)
    static let bodyNumber = Font.system(size: 12, weight: .semibold, design: .monospaced)
    static let rankingHeader = Font.system(size: 12, weight: .medium)
    static let rankingModel = Font.system(size: 14, weight: .medium)
    static let rankingModelEmphasis = Font.system(size: 14, weight: .semibold)
    static let rankingValue = Font.system(size: 13, weight: .regular, design: .monospaced)
    static let rankingValueEmphasis = Font.system(size: 13, weight: .semibold, design: .monospaced)
    static let brand = Font.system(size: 16, weight: .semibold)
    static let unit = Font.system(size: 16, weight: .medium)
    static let providerTitle = Font.system(size: 16, weight: .semibold)
    static let rowTitle = Font.system(size: 14, weight: .medium)
    static let icon = Font.system(size: 16, weight: .semibold)
    static let tabLabel = Font.system(size: 13, weight: .semibold)
    static let button = Font.system(size: 13, weight: .semibold)
    static let label = Font.system(size: 12, weight: .medium)
    static let micro = Font.system(size: 11, weight: .medium)
    static let sectionLabel = Font.system(size: 12, weight: .semibold)
    static let caption = Font.system(size: 11, design: .monospaced)
    static let chip = Font.system(size: 11, weight: .bold, design: .monospaced)
    static let settingsStatValue = Font.system(size: 24, weight: .semibold, design: .rounded)
    static let settingsStatLabel = Font.system(size: 11, weight: .medium)
    static let settingsCardTitle = Font.system(size: 16, weight: .semibold)
    static let settingsCardBody = Font.system(size: 12, weight: .medium)
}

enum LayoutRhythm {
    static let micro: CGFloat = 4
    static let compact: CGFloat = 8
    static let standard: CGFloat = 16
    static let section: CGFloat = 24
    static let large: CGFloat = 32
}

enum IslandRadius {
    static let control: CGFloat = 8
    static let card: CGFloat = 12
    static let modal: CGFloat = 16
    static let panel: CGFloat = 24
}
