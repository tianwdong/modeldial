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
        leaderboardItems: [RadarLeaderboardItem],
        remoteOnlyDisplayName: String? = nil
    ) -> CompactRecommendationPresentation {
        if let remoteOnlyDisplayName {
            return remoteOnlyPresentation(displayName: remoteOnlyDisplayName)
        }
        let effectiveDisplaySource: String?
        if displaySource == "official_snapshot" {
            effectiveDisplaySource = snapshot?.referenceSnapshotFeed.trustedLatest == nil
                ? nil
                : "official_snapshot"
        } else {
            effectiveDisplaySource = displaySource
        }
        let decision = portfolio?.representativeDecision
        let portfolioStatus = portfolio?.status
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
        } else if decision?.decision == "recommend",
                  portfolioStatus == nil || portfolioStatus == "recommend",
                  let decision {
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
        } else if portfolioStatus == "keep" {
            comparisonState = .suppressed
        } else {
            comparisonState = .pending
        }
        let rawTimestamp = effectiveDisplaySource == "official_snapshot"
            ? snapshot?.referenceSnapshotFeed.trustedLatest?.publishedAt
            : dashboard?.runMetadata.completedAt
        let questionCount: Int
        if effectiveDisplaySource == "official_snapshot" {
            questionCount = snapshot?.referenceSnapshotFeed.trustedLatest?
                .leaderboardProjection?.questions.count
                ?? snapshot?.questionPack.questionCount
                ?? 0
        } else {
            questionCount = dashboard?.runMetadata.questionCount
                ?? snapshot?.questionPack.questionCount
                ?? 0
        }
        let sourceText: String
        switch effectiveDisplaySource {
        case "official_snapshot": sourceText = L10n.tr("官网实测")
        case "local_evaluation": sourceText = L10n.tr("本机实测")
        default: sourceText = L10n.tr("数据待补齐")
        }
        let completeness = questionCount > 0
            ? L10n.tr("同题包完整 %d 题", questionCount)
            : L10n.tr("同题包完整结果")
        let timestamp = timestampText(rawTimestamp).map { L10n.tr(" · %@ 更新", $0) } ?? ""
        let freshnessText: String
        switch effectiveDisplaySource == "official_snapshot" ? displayFreshness : nil {
        case "delayed": freshnessText = L10n.tr("更新延迟")
        case "expired": freshnessText = L10n.tr("结果过期")
        default: freshnessText = ""
        }
        let tone: CompactRecommendationTone
        if targetName == nil {
            tone = .unavailable
        } else {
            switch portfolioStatus {
            case "recommend", "keep":
                tone = .recommendation
            case "needs_test":
                tone = .comparison
            case "stale", "no_usage":
                tone = .unavailable
            default:
                tone = decision != nil ? .recommendation : .comparison
            }
        }
        return CompactRecommendationPresentation(
            contextLabel: contextLabel(
                decision: decision,
                portfolioStatus: portfolioStatus,
                lifecycle: lifecycle
            ),
            title: targetName ?? L10n.tr("暂无可比较候选"),
            tone: tone,
            comparisonState: comparisonState,
            basisText: L10n.tr("%@%@ · %@", sourceText, timestamp, completeness),
            freshnessText: freshnessText
        )
    }

    private static func remoteOnlyPresentation(
        displayName: String
    ) -> CompactRecommendationPresentation {
        CompactRecommendationPresentation(
            contextLabel: L10n.tr("官网综合推荐"),
            title: displayName,
            tone: .unavailable,
            comparisonState: .pending,
            basisText: L10n.tr("官方榜单 · 暂无本地对比"),
            freshnessText: ""
        )
    }

    private static func contextLabel(
        decision: BridgeRecommendationDecisionV2?,
        portfolioStatus: String?,
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
        switch portfolioStatus {
        case "recommend":
            return L10n.tr("切换建议")
        case "keep":
            return L10n.tr("当前无需切换")
        case "needs_test":
            return L10n.tr("等待比较证据")
        case "stale":
            return L10n.tr("结果已过期")
        case "no_usage":
            return L10n.tr("等待使用记录")
        default:
            guard let decision else {
                return L10n.tr("即时建议")
            }
            return decision.decision == "recommend"
                ? L10n.tr("切换建议")
                : L10n.tr("当前无需切换")
        }
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
        if let entry = snapshot?.referenceSnapshotFeed.trustedLatest?.entries.first(where: {
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
