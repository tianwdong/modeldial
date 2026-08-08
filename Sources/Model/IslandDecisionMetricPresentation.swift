import Foundation

enum IslandDecisionMetricPresentation {
    static func quality(_ decision: BridgeRecommendationDecisionV2) -> String {
        guard let delta = decision.quality.scoreDelta else { return L10n.tr("未知") }
        if abs(delta) < 0.05 { return L10n.tr("持平") }
        return L10n.tr("%+.0f 分", delta)
    }

    static func time(_ decision: BridgeRecommendationDecisionV2) -> String {
        compactTime(decision)
    }

    static func compactTime(_ decision: BridgeRecommendationDecisionV2) -> String {
        guard let reduction = decision.time.reductionPercent else {
            return L10n.tr("不可比较")
        }
        if abs(reduction) < 0.05 { return L10n.tr("持平") }
        let percent = Int(abs(reduction).rounded())
        return reduction > 0
            ? L10n.tr("快 %d%%", percent)
            : L10n.tr("慢 %d%%", percent)
    }

    static func referenceCost(
        _ decision: BridgeRecommendationDecisionV2,
        isPartial: Bool
    ) -> String {
        guard decision.referenceCost.reductionPercent != nil else {
            return isPartial ? L10n.tr("部分未知") : L10n.tr("不可比较")
        }
        return compactReferenceCost(decision, isPartial: isPartial)
    }

    static func compactReferenceCost(
        _ decision: BridgeRecommendationDecisionV2,
        isPartial: Bool
    ) -> String {
        guard let reduction = decision.referenceCost.reductionPercent else {
            return isPartial ? L10n.tr("部分未知") : L10n.tr("不可比较")
        }
        if abs(reduction) < 0.05 { return L10n.tr("持平") }
        let percent = Int(abs(reduction).rounded())
        return reduction > 0
            ? L10n.tr("省 %d%%", percent)
            : L10n.tr("多 %d%%", percent)
    }
}
