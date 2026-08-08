import Foundation

enum CompactRecommendationTone: Equatable {
    case recommendation
    case comparison
    case unavailable
}

struct CompactRecommendationMetrics: Equatable {
    let quality: String
    let time: String
    let referenceCost: String
}

enum CompactRecommendationComparisonState: Equatable {
    case metrics(CompactRecommendationMetrics)
    case pending
    case suppressed
}

struct CompactRecommendationPresentation: Equatable {
    let contextLabel: String
    let title: String
    let tone: CompactRecommendationTone
    let comparisonState: CompactRecommendationComparisonState
    let basisText: String
    let freshnessText: String

    var metrics: CompactRecommendationMetrics? {
        guard case let .metrics(metrics) = comparisonState else { return nil }
        return metrics
    }
}

enum CompactSessionPresenter {
    static func recommendation(
        snapshot: BridgeSnapshot?,
        dashboard: BridgeDashboard?,
        portfolio: BridgeRecommendationPortfolioV2?,
        displaySource: String?,
        displayFreshness: String?,
        leaderboardItems: [RadarLeaderboardItem]
    ) -> CompactRecommendationPresentation {
        let decision = portfolio?.representativeDecision
        let lifecycle = portfolio?.recommendationLifecycle ?? .none
        let targetID: String?
        if decision?.decision == "recommend" {
            targetID = decision?.candidateModelConfigurationId
        } else {
            targetID = decision?.currentModelConfigurationId
        }
        let targetName = displayName(
            for: targetID,
            snapshot: snapshot,
            dashboard: dashboard,
            leaderboardItems: leaderboardItems
        )
        let costCoverage = leaderboardItems.first {
            $0.id == targetID
        }?.costCoverage
        let comparisonState: CompactRecommendationComparisonState
        if lifecycle.isAdopted {
            comparisonState = .suppressed
        } else if decision?.decision == "recommend", let decision {
            comparisonState = .metrics(
                CompactRecommendationMetrics(
                    quality: IslandDecisionMetricPresentation.quality(decision),
                    time: IslandDecisionMetricPresentation.compactTime(decision),
                    referenceCost: IslandDecisionMetricPresentation.compactReferenceCost(
                        decision,
                        isPartial: costCoverage == "partial"
                    )
                )
            )
        } else if decision == nil {
            comparisonState = .pending
        } else {
            comparisonState = .suppressed
        }
        let rawTimestamp = displaySource == "official_snapshot"
            ? snapshot?.referenceSnapshotFeed.latest?.publishedAt
            : dashboard?.runMetadata.completedAt
        let questionCount = dashboard?.runMetadata.questionCount
            ?? snapshot?.questionPack.questionCount
            ?? 0
        let sourceText: String
        switch displaySource {
        case "official_snapshot": sourceText = L10n.tr("官网实测")
        case "local_evaluation": sourceText = L10n.tr("本机实测")
        default: sourceText = L10n.tr("数据待补齐")
        }
        let completeness = questionCount > 0
            ? L10n.tr("同题包完整 %d 题", questionCount)
            : L10n.tr("同题包完整结果")
        let timestamp = timestampText(rawTimestamp).map { L10n.tr(" · %@ 更新", $0) } ?? ""
        let freshnessText: String
        switch displayFreshness {
        case "delayed": freshnessText = L10n.tr("更新延迟")
        case "expired": freshnessText = L10n.tr("结果过期")
        default: freshnessText = ""
        }
        let tone: CompactRecommendationTone
        if targetName == nil {
            tone = .unavailable
        } else if decision != nil {
            tone = .recommendation
        } else {
            tone = .comparison
        }
        return CompactRecommendationPresentation(
            contextLabel: contextLabel(
                decision: decision,
                lifecycle: lifecycle
            ),
            title: targetName ?? L10n.tr("暂无可比较候选"),
            tone: tone,
            comparisonState: comparisonState,
            basisText: L10n.tr("%@%@ · %@", sourceText, timestamp, completeness),
            freshnessText: freshnessText
        )
    }

    private static func contextLabel(
        decision: BridgeRecommendationDecisionV2?,
        lifecycle: BridgeRecommendationLifecycleV1
    ) -> String {
        if lifecycle.isAdopted {
            return L10n.tr("已采用建议")
        }
        if lifecycle.isNewProposal {
            return L10n.tr("有新建议")
        }
        if lifecycle.status == "reoptimize_required" {
            return L10n.tr("需要重新评估")
        }
        guard let decision else {
            return L10n.tr("即时建议")
        }
        return decision.decision == "recommend"
            ? L10n.tr("切换建议")
            : L10n.tr("当前无需切换")
    }

    private static func displayName(
        for configurationID: String?,
        snapshot: BridgeSnapshot?,
        dashboard: BridgeDashboard?,
        leaderboardItems: [RadarLeaderboardItem]
    ) -> String? {
        guard let configurationID else { return nil }
        if let item = leaderboardItems.first(where: { $0.id == configurationID }) {
            return item.displayName
        }
        if let candidate = snapshot?.config.modelIngress.connections
            .flatMap(\.modelCandidates)
            .first(where: { $0.id == configurationID }) {
            return ModelIdentityPresentation.displayLabel(
                model: candidate.modelId,
                effort: candidate.scanProfile
            )
        }
        if let entry = dashboard?.leaderboard.first(where: {
            $0.candidateId == configurationID
        }) {
            return ModelIdentityPresentation.displayLabel(
                model: entry.modelId,
                effort: entry.effort
            )
        }
        if let entry = snapshot?.referenceSnapshotFeed.latest?.entries.first(where: {
            $0.modelConfigurationId == configurationID
        }) {
            return ModelIdentityPresentation.displayLabel(
                model: entry.modelConfiguration.canonicalModelId,
                effort: entry.modelConfiguration.reasoningEffort
            )
        }
        return configurationID
    }

    private static func timestampText(_ value: String?) -> String? {
        guard let value else { return nil }
        let parser = ISO8601DateFormatter()
        parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let date = parser.date(from: value) ?? {
            parser.formatOptions = [.withInternetDateTime]
            return parser.date(from: value)
        }()
        guard let date else { return nil }
        let formatter = DateFormatter()
        formatter.locale = L10n.locale
        formatter.dateFormat = "HH:mm"
        return formatter.string(from: date)
    }
}
