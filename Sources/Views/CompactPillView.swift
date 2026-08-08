import SwiftUI

struct CompactPillView: View {
    let presentation: GlancePresentation
    let notch: NotchInfo
    let sideSlotWidth: CGFloat
    let transitionNamespace: Namespace.ID
    let primaryIdentityTransitionID: String
    let isTransitionSource: Bool
    let reduceMotion: Bool
    private let notchTextSafetyInset: CGFloat = 8

    var body: some View {
        Group {
            if notch.hasNotch {
                notchCompactLayout
            } else {
                menuBarCompactLayout
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityHidden(true)
    }

    private var notchCompactLayout: some View {
        HStack(spacing: 0) {
            compactLeftContent
                .padding(.leading, LayoutRhythm.section)
                .padding(.trailing, notchTextSafetyInset)
                .frame(width: sideSlotWidth, alignment: .leading)

            Color.clear
                .frame(width: notch.width)
                .allowsHitTesting(false)

            compactRightContent
                .padding(.leading, notchTextSafetyInset)
                .padding(.trailing, LayoutRhythm.section)
                .frame(width: sideSlotWidth, alignment: .trailing)
        }
    }

    private var menuBarCompactLayout: some View {
        HStack(spacing: LayoutRhythm.compact) {
            compactLeftContent
            Spacer(minLength: 8)
            compactRightContent
        }
        .padding(.horizontal, LayoutRhythm.standard)
    }

    private var compactLeftContent: some View {
        HStack(spacing: LayoutRhythm.micro) {
            if presentation.activity == .none,
               let symbol = presentation.compactLeadingSymbol {
                Image(systemName: symbol)
                    .font(Typography.label)
                    .foregroundStyle(
                        (presentation.compactLeadingSymbolTone ?? presentation.tone)
                            .foregroundColor
                    )
                    .accessibilityHidden(true)
            }
            Text(presentation.compactLeft)
                .font(Typography.label)
                .foregroundStyle(compactTextColor(for: presentation.compactLeftTextRole))
                .lineLimit(1)
                .truncationMode(.tail)
                .minimumScaleFactor(0.72)
        }
        .islandMatchedGeometry(
            id: primaryIdentityTransitionID,
            in: transitionNamespace,
            isSource: isTransitionSource,
            reduceMotion: reduceMotion
        )
    }

    private var compactRightContent: some View {
        Text(presentation.compactRight)
            .font(Typography.label)
            .foregroundStyle(compactTextColor(for: presentation.compactRightTextRole))
            .monospacedDigit()
            .lineLimit(1)
            .minimumScaleFactor(0.72)
            .islandMatchedGeometry(
                id: IslandTransitionElement.secondaryStatus.rawValue,
                in: transitionNamespace,
                isSource: isTransitionSource,
                reduceMotion: reduceMotion
            )
    }

    private func compactTextColor(for role: GlanceCompactTextRole) -> Color {
        switch role {
        case .identityPrimary:
            return IslandVisual.primaryText
        case .identitySecondary:
            return IslandVisual.secondaryText
        case .status:
            return presentation.tone.foregroundColor
        }
    }
}

extension GlanceTone {
    var foregroundColor: Color {
        switch self {
        case .neutral:
            return IslandVisual.secondaryText
        case .active:
            return IslandColor.interaction
        case .success:
            return IslandColor.liveTeal
        case .warning:
            return IslandColor.alertAmber
        case .failure:
            return IslandColor.alertRed
        }
    }
}
