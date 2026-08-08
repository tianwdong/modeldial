import SwiftUI

struct CompactSessionPanelView: View {
    private static let recommendationHeight: CGFloat = 66
    private static let sessionSummaryHeight: CGFloat = 46

    @ObservedObject var store: AppSessionStore
    @ObservedObject var model: IslandModel
    let transitionNamespace: Namespace.ID
    let isTransitionSource: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    static func height(forSessionCount count: Int) -> CGFloat {
        guard count > 0 else { return recommendationHeight }
        return recommendationHeight + sessionSummaryHeight
    }

    var body: some View {
        VStack(spacing: 0) {
            if showsRecommendationSummary {
                recommendationSummary
            } else {
                operationalSummary
            }

            if let session = sessions.first {
                sessionRow(session)
            }
        }
        .frame(
            width: model.compactSessionPanelWidth - IslandShape.hoverShoulderRadius * 2,
            height: Self.height(forSessionCount: sessions.count),
            alignment: .top
        )
    }

    private var sessions: [BridgeDetectedModelSession] {
        store.activeModelSessions
    }

    private var showsRecommendationSummary: Bool {
        switch store.glancePresentation.state {
        case .freshRecommendation,
             .staleRecommendation,
             .degradedRecommendation,
             .failedWithFallbackRecommendation:
            return true
        default:
            return false
        }
    }

    private var operationalSummary: some View {
        let presentation = store.glancePresentation
        return HStack(alignment: .center, spacing: 16) {
            VStack(alignment: .leading, spacing: 5) {
                Text(presentation.peekLeftPrimary)
                    .font(Typography.rowTitle)
                    .foregroundStyle(operationalToneColor)
                    .lineLimit(1)

                if let secondary = presentation.peekLeftSecondary {
                    Text(secondary)
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.tertiaryText)
                        .lineLimit(2)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            if presentation.peekRightLabel != nil || presentation.peekRightValue != nil {
                VStack(alignment: .trailing, spacing: 3) {
                    Text(presentation.peekRightValue ?? "—")
                        .font(Typography.bodyNumber)
                        .foregroundStyle(IslandVisual.secondaryText)
                        .monospacedDigit()
                        .lineLimit(1)
                    if let label = presentation.peekRightLabel {
                        Text(label)
                            .font(Typography.micro)
                            .foregroundStyle(IslandVisual.tertiaryText)
                            .lineLimit(1)
                    }
                }
            }
        }
        .padding(.horizontal, LayoutRhythm.section)
        .frame(height: Self.recommendationHeight, alignment: .center)
        .overlay(alignment: .bottom) {
            if !sessions.isEmpty {
                Rectangle()
                    .fill(IslandVisual.hairline.opacity(0.72))
                    .frame(height: 0.5)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(presentation.accessibilityLabel)
    }

    private var operationalToneColor: Color {
        switch store.glancePresentation.tone {
        case .active: return IslandColor.interaction
        case .success: return IslandColor.liveTeal
        case .warning: return IslandColor.alertAmber
        case .failure: return IslandColor.alertRed
        case .neutral: return IslandVisual.primaryText
        }
    }

    private var recommendationSummary: some View {
        let presentation = store.compactRecommendationPresentation
        return VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(presentation.contextLabel)
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.tertiaryText)
                    .lineLimit(1)

                Text(presentation.title)
                    .font(Typography.rowTitle)
                    .foregroundStyle(recommendationCandidateColor)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
                    .islandMatchedGeometry(
                        id: IslandTransitionElement.candidateIdentity.rawValue,
                        in: transitionNamespace,
                        isSource: isTransitionSource,
                        reduceMotion: reduceMotion
                    )

                Spacer(minLength: 8)

                if !presentation.freshnessText.isEmpty {
                    Text(presentation.freshnessText)
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.hintText)
                        .lineLimit(1)
                }
            }

            recommendationMetrics(presentation.comparisonState)
        }
        .padding(.horizontal, LayoutRhythm.standard)
        .frame(height: Self.recommendationHeight, alignment: .center)
        .overlay(alignment: .bottom) {
            if !sessions.isEmpty {
                Rectangle()
                    .fill(IslandVisual.hairline.opacity(0.72))
                    .frame(height: 0.5)
            }
        }
        .accessibilityElement(children: .combine)
    }

    private var recommendationCandidateColor: Color {
        switch store.compactRecommendationPresentation.tone {
        case .recommendation: return IslandColor.liveTeal
        case .comparison: return IslandColor.interaction
        case .unavailable: return IslandVisual.secondaryText
        }
    }

    @ViewBuilder
    private func recommendationMetrics(
        _ state: CompactRecommendationComparisonState
    ) -> some View {
        switch state {
        case .metrics(let metrics):
            HStack(spacing: 0) {
                recommendationMetric(
                    label: L10n.tr("质量"),
                    value: metrics.quality,
                    element: .qualityMetric
                )
                recommendationMetricDivider
                recommendationMetric(
                    label: L10n.tr("时间"),
                    value: metrics.time,
                    element: .timeMetric
                )
                recommendationMetricDivider
                recommendationMetric(
                    label: L10n.tr("成本"),
                    value: metrics.referenceCost,
                    element: .costMetric
                )
            }
            .frame(maxWidth: .infinity)
        case .pending:
            Text("完成同轮题目后显示差异")
                .font(Typography.micro)
                .foregroundStyle(IslandVisual.tertiaryText)
                .lineLimit(1)
                .frame(maxWidth: .infinity, alignment: .center)
        case .suppressed:
            EmptyView()
        }
    }

    private func recommendationMetric(
        label: String,
        value: String,
        element: IslandTransitionElement
    ) -> some View {
        HStack(spacing: LayoutRhythm.micro) {
            Text(label)
                .font(Typography.micro)
                .foregroundStyle(IslandVisual.tertiaryText)
                .lineLimit(1)

            Spacer(minLength: LayoutRhythm.micro)

            Text(value)
                .font(Typography.bodyNumber)
                .foregroundStyle(IslandVisual.secondaryText)
                .monospacedDigit()
                .lineLimit(1)
                .minimumScaleFactor(0.8)
                .islandMatchedGeometry(
                    id: element.rawValue,
                    in: transitionNamespace,
                    isSource: isTransitionSource,
                    reduceMotion: reduceMotion
                )
        }
        .padding(.horizontal, LayoutRhythm.micro / 2)
        .frame(maxWidth: .infinity)
    }

    private var recommendationMetricDivider: some View {
        Rectangle()
            .fill(IslandVisual.hairline.opacity(0.72))
            .frame(width: 0.5, height: 20)
            .padding(.horizontal, LayoutRhythm.micro)
    }

    private func sessionRow(_ session: BridgeDetectedModelSession) -> some View {
        let presentation = ActiveSessionPresenter.present(session)

        return HStack(spacing: 10) {
            Circle()
                .fill(IslandColor.interaction)
                .frame(width: 6, height: 6)
                .accessibilityHidden(true)

            Text(L10n.Sessions.count(sessions.count))
                .font(Typography.micro)
                .foregroundStyle(IslandVisual.tertiaryText)
                .monospacedDigit()
                .fixedSize(horizontal: true, vertical: false)

            Text(presentation.title)
                .font(Typography.label)
                .foregroundStyle(IslandVisual.primaryText)
                .lineLimit(1)
                .truncationMode(.tail)
                .frame(maxWidth: .infinity, alignment: .leading)

            Text(presentation.identity)
                .font(Typography.micro)
                .foregroundStyle(IslandVisual.secondaryText)
                .lineLimit(1)
                .minimumScaleFactor(0.82)
                .frame(maxWidth: 132, alignment: .trailing)
                .layoutPriority(1)
        }
        .padding(.horizontal, LayoutRhythm.section)
        .frame(height: Self.sessionSummaryHeight)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(
            L10n.Sessions.activeAccessibility(
                title: presentation.title,
                context: presentation.context
            )
        )
    }
}
