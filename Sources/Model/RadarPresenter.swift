import Foundation

enum RadarPresenter {
    struct SourceLabels: Equatable {
        let control: String
        let accessibilityValue: String
    }

    struct DecisionPresentation: Equatable {
        let candidateConfigurationID: String?
        let candidateLabel: String?
        let comparisonLabel: String
        let qualityText: String
        let timeText: String
        let referenceCostText: String
        let title: String?
        let reason: String?

        var titleOrFallback: String { title ?? L10n.tr("等待推荐结论") }
        var reasonOrFallback: String { reason ?? L10n.tr("等待后端推荐投影。") }
    }

    struct SessionInput: Equatable {
        let id: String
        let source: String
        let model: String?
        let effort: String?
        let title: String
        let context: String
        let isEvaluationSession: Bool
    }

    struct SessionSummary: Equatable {
        let visibleSessionIDs: [String]
        let title: String
        let detail: String?
        let accessibilityLabel: String
    }

    struct ActiveUsageSessionInput: Equatable {
        let id: String
        let sourceDisplayName: String
        let model: String?
        let effort: String?
    }

    struct ActiveUsageSummary: Equatable {
        let identity: String
        let detail: String
    }

    struct ActiveUsagePresentation: Equatable {
        let summary: ActiveUsageSummary?
        let sessionIdentities: [String: String]
        let sessionDetails: [String: String]
    }

    struct CanonicalLeaderboardRow: Equatable {
        let configurationID: String
        let alternateConfigurationID: String?
        let rank: Int?
        let targetLabels: [String]
        let decisionTagKinds: [String]
    }

    struct LeaderboardItemInput: Equatable {
        let configurationID: String
        let isCurrent: Bool
        let isRecommended: Bool
    }

    struct LeaderboardRowPresentation: Equatable {
        let rank: Int?
        let tags: [String]
    }

    struct LeaderboardExportSemantics: Equatable {
        let isRecommended: Bool
    }

    struct ReferenceCostPresentation: Equatable {
        let text: String
        let helpText: String
    }

    enum DecisionEmphasis: Equatable {
        case recommended
        case neutral
    }

    struct ConfigurationDecisionPresentation: Equatable {
        let text: String
        let emphasis: DecisionEmphasis
    }

    struct SurfacePresentation: Equatable {
        let rankingContext: String
        let questionPackVersion: String
        let emptyTitle: String
        let emptyReason: String
    }

    struct QuestionContractInput: Equatable {
        let id: String
        let scoreMax: Int
    }

    struct QuestionResultContractInput: Equatable {
        let id: String
        let semanticTotal: Int?
    }

    struct EvidenceAvailabilityInput: Equatable {
        let scoringMode: String
        let questionCompleted: Int
        let hasQuestionResults: Bool
        let hasLatestValidAt: Bool
        let hasLatestAttemptStatus: Bool
        let isCurrentPackComparable: Bool
        let isInCurrentOperation: Bool
        let isCurrentRunEligible: Bool
        let hasLatestAttemptError: Bool
        let hasOverallScore: Bool
        let questionContracts: [QuestionContractInput]
        let questionResults: [QuestionResultContractInput]
    }

    struct EvidenceAvailabilityPresentation: Equatable {
        let requiresCurrentPackRescan: Bool
        let canDisplayCurrentQuestionScores: Bool
        let canDisplayCurrentOverallScore: Bool
        let isLeaderboardExportable: Bool
    }

    static func shouldUseCurrentDashboard(
        runID: String,
        evidenceSourceSnapshotID: String?
    ) -> Bool {
        evidenceSourceSnapshotID == "local:\(runID)"
    }

    static func sourceLabels(
        selectedSourceMode: String?,
        displaySource: String?,
        displayFreshness: String? = nil
    ) -> SourceLabels {
        switch selectedSourceMode {
        case "official_snapshot":
            return SourceLabels(
                control: L10n.tr("官网榜单"),
                accessibilityValue: L10n.tr("来源：官网榜单")
            )
        case "local_evaluation":
            if displayFreshness == "expired" {
                return SourceLabels(
                    control: L10n.tr("本机实测 · 已过期"),
                    accessibilityValue: L10n.tr("来源：本机实测（结果已过期）")
                )
            }
            return SourceLabels(
                control: L10n.tr("本机实测"),
                accessibilityValue: L10n.tr("来源：本机实测")
            )
        default:
            switch displaySource {
            case "official_snapshot":
                return SourceLabels(
                    control: L10n.tr("自动 · 官网榜单"),
                    accessibilityValue: L10n.tr("来源：官网榜单（自动）")
                )
            case "local_evaluation":
                return SourceLabels(
                    control: L10n.tr("自动 · 本机实测"),
                    accessibilityValue: L10n.tr("来源：本机实测（自动）")
                )
            default:
                return SourceLabels(
                    control: L10n.tr("自动选择"),
                    accessibilityValue: L10n.tr("来源：自动选择")
                )
            }
        }
    }

    static func preferenceLabel(_ preference: String?) -> String {
        switch preference {
        case "quality":
            return L10n.tr("目标：质量优先")
        case "speed":
            return L10n.tr("目标：速度优先")
        case "cost":
            return L10n.tr("目标：费用优先")
        default:
            return L10n.tr("目标：综合平衡")
        }
    }

    static func candidateConfigurationID(
        for decision: BridgeRecommendationDecisionV2?
    ) -> String? {
        decision?.candidateModelConfigurationId
            ?? decision?.comparisonCandidateModelConfigurationId
    }

    static func decision(
        evidenceUpdating: Bool,
        hasSnapshotRefreshIssue: Bool,
        hasResumableRun: Bool,
        isUnmappedCurrentModel: Bool,
        detectedCurrentModelIdentity: String?,
        selectedSourceMode: String?,
        displaySource: String? = nil,
        portfolio: BridgeRecommendationPortfolioV2?,
        decision: BridgeRecommendationDecisionV2?,
        candidateLabel: String?,
        candidateCostCoverage: String?
    ) -> DecisionPresentation {
        let candidateID = candidateConfigurationID(for: decision)
        let comparisonLabel: String
        if candidateLabel == nil {
            comparisonLabel = L10n.tr("当前在用")
        } else if decision?.decision == "recommend" {
            comparisonLabel = L10n.tr("切换建议")
        } else {
            comparisonLabel = L10n.tr("最接近候选")
        }

        let title: String?
        let reason: String?
        if !evidenceUpdating,
           !hasSnapshotRefreshIssue,
           !hasResumableRun,
           let portfolio {
            if isUnmappedCurrentModel {
                title = L10n.tr("当前档位未纳入比较")
                if let identity = detectedCurrentModelIdentity {
                    reason = L10n.tr(
                        "已识别 %@，但该档位不在当前评测范围；榜单仍展示已启用档位的结果，暂不形成切换建议。",
                        identity
                    )
                } else {
                    reason = ComparisonPresenter.recommendationReasonText(
                        status: portfolio.status,
                        reason: decision?.reason,
                        sourceResolutionReason: portfolio.sourceResolutionReason,
                        selectedSourceMode: selectedSourceMode,
                        qualityTradeoff: decision?.qualityTradeoff == true,
                        scoreDelta: decision?.quality.scoreDelta,
                        timeReductionPercent: decision?.time.reductionPercent,
                        costReductionPercent: decision?.referenceCost.reductionPercent
                    )
                }
            } else {
                switch portfolio.status {
                case "recommend":
                    title = candidateLabel.map { L10n.tr("建议切换到 %@", $0) }
                        ?? L10n.tr("建议切换模型")
                case "keep":
                    title = L10n.tr("暂不建议切换")
                case "stale":
                    title = L10n.tr("结果已过期")
                case "no_usage":
                    title = L10n.tr("尚无使用记录")
                default:
                    title = displaySource == "official_snapshot"
                        ? L10n.tr("远端榜单可供参考")
                        : L10n.tr("先完成本机快测")
                }
                if portfolio.status == "needs_test",
                   displaySource == "official_snapshot" {
                    reason = L10n.tr(
                        "当前档位暂无同口径远端结论；榜单仍可浏览，本机实测仅用于校准当前路线。"
                    )
                } else {
                    reason = ComparisonPresenter.recommendationReasonText(
                        status: portfolio.status,
                        reason: decision?.reason,
                        sourceResolutionReason: portfolio.sourceResolutionReason,
                        selectedSourceMode: selectedSourceMode,
                        qualityTradeoff: decision?.qualityTradeoff == true,
                        scoreDelta: decision?.quality.scoreDelta,
                        timeReductionPercent: decision?.time.reductionPercent,
                        costReductionPercent: decision?.referenceCost.reductionPercent
                    )
                }
            }
        } else {
            title = nil
            reason = nil
        }

        return DecisionPresentation(
            candidateConfigurationID: candidateID,
            candidateLabel: candidateLabel,
            comparisonLabel: comparisonLabel,
            qualityText: decision.map(IslandDecisionMetricPresentation.quality) ?? L10n.tr("未知"),
            timeText: decision.map(IslandDecisionMetricPresentation.time) ?? L10n.tr("不可比较"),
            referenceCostText: decision.map {
                IslandDecisionMetricPresentation.referenceCost(
                    $0,
                    isPartial: candidateCostCoverage == "partial"
                )
            } ?? L10n.tr("不可比较"),
            title: title,
            reason: reason
        )
    }

    static func sessionSummary(
        sessions: [SessionInput],
        isCurrentModelAutomatic: Bool,
        currentModelDetectionStatus: String?
    ) -> SessionSummary {
        let visibleSessions = sessions.filter { !$0.isEvaluationSession }
        let configurationCount = Set(visibleSessions.compactMap(configurationKey)).count
        let title: String
        if !visibleSessions.isEmpty {
            title = configurationCount > 1
                ? L10n.tr("%d 个活动会话 · %d 个配置", visibleSessions.count, configurationCount)
                : L10n.tr("%d 个活动会话", visibleSessions.count)
        } else if !isCurrentModelAutomatic {
            title = L10n.tr("当前模型已手动指定")
        } else if currentModelDetectionStatus == "recent" {
            title = L10n.tr("根据最近使用识别")
        } else {
            title = L10n.tr("尚未识别活动会话")
        }

        let detail = visibleSessions.first.map { "\($0.title) · \($0.context)" }
        return SessionSummary(
            visibleSessionIDs: visibleSessions.map(\.id),
            title: title,
            detail: detail,
            accessibilityLabel: [title, detail]
                .compactMap { $0 }
                .joined(separator: "，")
        )
    }

    static func activeUsage(
        sessions: [ActiveUsageSessionInput]
    ) -> ActiveUsagePresentation {
        let sessionIdentities = Dictionary(
            sessions.map { session in
                let model = session.model?.trimmingCharacters(in: .whitespacesAndNewlines)
                let identity = model.flatMap { value in
                    value.isEmpty
                        ? nil
                        : ModelIdentityPresentation.displayLabel(
                            model: value,
                            effort: session.effort ?? ""
                        )
                } ?? session.sourceDisplayName
                return (session.id, identity)
            },
            uniquingKeysWith: { first, _ in first }
        )
        let sessionDetails = Dictionary(
            sessions.map { session in
                let identity = sessionIdentities[session.id] ?? session.sourceDisplayName
                let detail = identity == session.sourceDisplayName
                    ? identity
                    : "\(session.sourceDisplayName) · \(identity)"
                return (session.id, detail)
            },
            uniquingKeysWith: { first, _ in first }
        )
        guard !sessions.isEmpty else {
            return ActiveUsagePresentation(
                summary: nil,
                sessionIdentities: sessionIdentities,
                sessionDetails: sessionDetails
            )
        }

        let identities: [(model: String, modelKey: String, effort: String)] = sessions.compactMap {
            session in
            guard let model = session.model?.trimmingCharacters(in: .whitespacesAndNewlines),
                  !model.isEmpty else {
                return nil
            }
            return (
                model: model,
                modelKey: model.lowercased(),
                effort: ModelIdentityPresentation.canonicalEffortName(
                    for: session.effort ?? ""
                )
            )
        }
        let summary: ActiveUsageSummary
        if identities.count != sessions.count {
            summary = ActiveUsageSummary(
                identity: L10n.tr("多个会话"),
                detail: L10n.tr("%d 个会话，部分模型未识别", sessions.count)
            )
        } else {
            summary = activeUsageSummary(sessions: sessions, identities: identities)
        }
        return ActiveUsagePresentation(
            summary: summary,
            sessionIdentities: sessionIdentities,
            sessionDetails: sessionDetails
        )
    }

    static func leaderboardRow(
        item: LeaderboardItemInput,
        displaySource: String?,
        portfolioStatus: String?,
        officialRows: [CanonicalLeaderboardRow],
        localRows: [CanonicalLeaderboardRow]
    ) -> LeaderboardRowPresentation {
        let rows: [CanonicalLeaderboardRow]
        switch displaySource {
        case "official_snapshot":
            rows = officialRows
        case "local_evaluation":
            rows = localRows
        default:
            rows = []
        }
        let projectedRow = rows.first {
            $0.configurationID == item.configurationID
                || $0.alternateConfigurationID == item.configurationID
        }

        var tags: [String] = []
        if displaySource == "official_snapshot" {
            if item.isCurrent, portfolioStatus == "keep" {
                tags.append(L10n.tr("当前在用"))
            } else if item.isCurrent {
                tags.append(L10n.tr("当前"))
            }
            for kind in projectedRow?.decisionTagKinds ?? [] {
                guard let label = officialDecisionTagLabel(kind),
                      !tags.contains(label) else {
                    continue
                }
                tags.append(label)
            }
        } else {
            if item.isRecommended {
                tags.append(L10n.tr("建议切换"))
            } else if item.isCurrent, portfolioStatus == "keep" {
                tags.append(L10n.tr("当前在用"))
            } else if item.isCurrent {
                tags.append(L10n.tr("当前"))
            }
            for label in projectedRow?.targetLabels ?? [] where !tags.contains(label) {
                tags.append(label)
            }
        }
        return LeaderboardRowPresentation(
            rank: projectedRow?.rank,
            tags: Array(tags.prefix(3))
        )
    }

    private static func officialDecisionTagLabel(_ kind: String) -> String? {
        switch kind {
        case "recommended": return L10n.tr("推荐")
        case "value": return L10n.tr("性价比")
        case "speed": return L10n.tr("速度优选")
        case "lightweight": return L10n.tr("轻量优选")
        default: return nil
        }
    }

    static func leaderboardExportSemantics(
        decisionTagKinds: [String]
    ) -> LeaderboardExportSemantics {
        LeaderboardExportSemantics(
            isRecommended: decisionTagKinds.contains("recommended")
        )
    }

    static func referenceCost(
        value: Double?,
        coverage: String?
    ) -> ReferenceCostPresentation {
        guard let value else {
            return ReferenceCostPresentation(
                text: L10n.tr("未知"),
                helpText: L10n.tr("按 Token 和版本化公开单价折算，不等于实际账单。")
            )
        }
        switch coverage {
        case "partial":
            return ReferenceCostPresentation(
                text: String(format: "≥$%.2f", value),
                helpText: L10n.tr("只统计已取得 Token 用量且有单价的调用；仍有调用费用未知。")
            )
        case "complete":
            return ReferenceCostPresentation(
                text: String(format: "$%.2f", value),
                helpText: L10n.tr("按版本化公开单价折算的完整参考费用。")
            )
        default:
            return ReferenceCostPresentation(
                text: String(format: "$%.2f", value),
                helpText: L10n.tr("按 Token 和版本化公开单价折算，不等于实际账单。")
            )
        }
    }

    static func surface(
        displaySource: String?,
        selectedSourceMode: String?,
        portfolioStatus: String?,
        evidenceUpdating: Bool,
        hasStableDashboard: Bool,
        completeQuestionSetLabel: String,
        officialQuestionPackVersion: String?,
        localQuestionPackVersion: String?,
        requiresModelSetup: Bool = false
    ) -> SurfacePresentation {
        let rankingContext: String
        let questionPackVersion: String
        if displaySource == "official_snapshot" {
            rankingContext = evidenceUpdating && hasStableDashboard
                ? L10n.tr("上次榜单 · %@", completeQuestionSetLabel)
                : L10n.tr("榜单结果 · %@", completeQuestionSetLabel)
            questionPackVersion = officialQuestionPackVersion ?? L10n.tr("题包未知")
        } else if displaySource == "local_evaluation" {
            rankingContext = evidenceUpdating && hasStableDashboard
                ? L10n.tr("上轮结果 · %@", completeQuestionSetLabel)
                : L10n.tr("本轮结果 · %@", completeQuestionSetLabel)
            questionPackVersion = localQuestionPackVersion ?? L10n.tr("题包未知")
        } else {
            rankingContext = L10n.tr("排名结果 · 来源未就绪")
            questionPackVersion = localQuestionPackVersion ?? L10n.tr("题包未知")
        }

        let emptyTitle: String
        if evidenceUpdating {
            emptyTitle = L10n.tr("扫描进行中")
        } else if requiresModelSetup && displaySource != "official_snapshot" {
            emptyTitle = L10n.tr("官方 Radar 尚未载入")
        } else {
            switch portfolioStatus {
            case "no_usage":
                emptyTitle = L10n.tr("尚无使用记录")
            case "stale":
                emptyTitle = L10n.tr("结果已过期")
            default:
                emptyTitle = L10n.tr("当前来源暂无可比较结果")
            }
        }

        let emptyReason: String
        if evidenceUpdating {
            emptyReason = L10n.tr("正在生成本轮榜单，完成后会自动显示完整结果。")
        } else if requiresModelSetup && displaySource != "official_snapshot" {
            emptyReason = L10n.tr("可刷新官方榜单；本地模型接入与本机评测是可选项。")
        } else if displaySource == "official_snapshot" {
            emptyReason = L10n.tr("当前档位暂无同口径远端结论；榜单仍可浏览，本机实测仅用于校准当前路线。")
        } else if selectedSourceMode == "local_evaluation" {
            emptyReason = L10n.tr("完成当前配置与候选配置的同轮快测后，本页会生成本机榜单。")
        } else {
            emptyReason = L10n.tr("自动选择没有找到覆盖当前模型和思考档位的数据，建议先运行本机快测。")
        }

        return SurfacePresentation(
            rankingContext: rankingContext,
            questionPackVersion: questionPackVersion,
            emptyTitle: emptyTitle,
            emptyReason: emptyReason
        )
    }

    static func evidenceAvailability(
        _ input: EvidenceAvailabilityInput
    ) -> EvidenceAvailabilityPresentation {
        let usesCurrentQuestionScoreContract = input.scoringMode == "semantic_q1_q5_equal_v2"
        let hasRecordedEvidence = input.questionCompleted > 0
            || input.hasQuestionResults
            || input.hasLatestValidAt
            || input.hasLatestAttemptStatus
        let currentQuestionResultCount = input.questionContracts.reduce(into: 0) {
            count, contract in
            if input.questionResults.contains(where: {
                $0.id == contract.id && $0.semanticTotal == contract.scoreMax
            }) {
                count += 1
            }
        }
        let requiresCurrentPackRescan = hasRecordedEvidence
            && (!input.isCurrentPackComparable || !usesCurrentQuestionScoreContract)
        let canDisplayCurrentQuestionScores = !requiresCurrentPackRescan
            && usesCurrentQuestionScoreContract
            && (input.isInCurrentOperation || currentQuestionResultCount > 0)
        let canDisplayCurrentOverallScore = currentQuestionResultCount == input.questionContracts.count
            && input.hasOverallScore
        let isLeaderboardExportable = input.isCurrentRunEligible
            && input.isCurrentPackComparable
            && !input.hasLatestAttemptError
            && canDisplayCurrentOverallScore
        return EvidenceAvailabilityPresentation(
            requiresCurrentPackRescan: requiresCurrentPackRescan,
            canDisplayCurrentQuestionScores: canDisplayCurrentQuestionScores,
            canDisplayCurrentOverallScore: canDisplayCurrentOverallScore,
            isLeaderboardExportable: isLeaderboardExportable
        )
    }

    static func configurationDecisionText(
        decision: BridgeRecommendationDecisionV2,
        target: String
    ) -> String {
        ComparisonPresenter.configurationDecisionText(
            decision: decision.decision,
            target: target,
            benefitKind: decision.primaryBenefit?.kind,
            reductionPercent: decision.primaryBenefit?.reductionPercent,
            gainPoints: decision.primaryBenefit?.gainPoints
        )
    }

    static func configurationDecision(
        decision: BridgeRecommendationDecisionV2,
        target: String
    ) -> ConfigurationDecisionPresentation {
        ConfigurationDecisionPresentation(
            text: configurationDecisionText(decision: decision, target: target),
            emphasis: decision.decision == "recommend" ? .recommended : .neutral
        )
    }

    static func decisionEmphasis(_ decision: String?) -> DecisionEmphasis {
        decision == "recommend" ? .recommended : .neutral
    }

    static func relevantQuestionSemantics(
        available: [QuestionSemantic],
        runQuestionIDs: [String]
    ) -> [QuestionSemantic] {
        guard !runQuestionIDs.isEmpty else { return available }
        let semanticByQuestionID = Dictionary(
            uniqueKeysWithValues: available.map { ($0.questionId, $0) }
        )
        return runQuestionIDs.compactMap { semanticByQuestionID[$0] }
    }

    private static func activeUsageSummary(
        sessions: [ActiveUsageSessionInput],
        identities: [(model: String, modelKey: String, effort: String)]
    ) -> ActiveUsageSummary {
        let modelKeys = Set(identities.map(\.modelKey))
        let slotKeys = Set(identities.map { "\($0.modelKey)\u{1F}\($0.effort)" })
        let effortCounts = Dictionary(grouping: identities, by: { $0.effort })
            .mapValues(\.count)

        if modelKeys.count == 1, let first = identities.first {
            if effortCounts.count == 1, let onlyEffort = effortCounts.keys.first {
                let detail = sessions.count == 1
                    ? L10n.tr("1 个活动会话")
                    : L10n.tr("%d 个会话一致", sessions.count)
                if onlyEffort.isEmpty {
                    return ActiveUsageSummary(
                        identity: ModelIdentityPresentation.canonicalName(for: first.model),
                        detail: L10n.tr("%d 个会话，档位未识别", sessions.count)
                    )
                }
                return ActiveUsageSummary(
                    identity: ModelIdentityPresentation.displayLabel(
                        model: first.model,
                        effort: onlyEffort
                    ),
                    detail: detail
                )
            }

            let effortOrder = ["low", "medium", "high", "xhigh", "max", "ultra"]
            let effortBreakdown = effortCounts.keys.sorted { lhs, rhs in
                let lhsIndex = effortOrder.firstIndex(of: lhs) ?? Int.max
                let rhsIndex = effortOrder.firstIndex(of: rhs) ?? Int.max
                return lhsIndex == rhsIndex ? lhs < rhs : lhsIndex < rhsIndex
            }.map { effort in
                let label = effort.isEmpty ? L10n.tr("未识别") : effort
                return L10n.tr("%@ × %d", label, effortCounts[effort] ?? 0)
            }
            return ActiveUsageSummary(
                identity: ModelIdentityPresentation.canonicalName(for: first.model),
                detail: effortBreakdown.joined(separator: "、")
            )
        }

        let distinctEfforts = Set(identities.map(\.effort))
        if distinctEfforts.count == 1,
           let onlyEffort = distinctEfforts.first,
           !onlyEffort.isEmpty {
            return ActiveUsageSummary(
                identity: L10n.tr("多个模型 [%@]", onlyEffort),
                detail: L10n.tr("%d 个会话，%d 个模型", sessions.count, modelKeys.count)
            )
        }
        return ActiveUsageSummary(
            identity: L10n.tr("多个模型"),
            detail: L10n.tr("%d 个会话，%d 个模型档位", sessions.count, slotKeys.count)
        )
    }

    private static func configurationKey(_ session: SessionInput) -> String? {
        guard let model = session.model?.trimmingCharacters(in: .whitespacesAndNewlines),
              !model.isEmpty else {
            return nil
        }
        let source = session.source
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
        let effort = session.effort?
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased() ?? ""
        return "\(source)|\(model.lowercased())|\(effort)"
    }

}
