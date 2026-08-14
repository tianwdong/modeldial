import Foundation

enum ComparisonPresenter {
    enum Emphasis: Equatable {
        case primary
        case positive
        case warning
        case secondary
        case tertiary
    }

    struct Presentation: Equatable {
        let text: String
        let emphasis: Emphasis
    }

    struct CandidateInput: Equatable {
        let id: String
        let score: Double?
        let elapsedSeconds: Double?
        let referenceCostUsd: Double?
    }

    struct TokenValues: Equatable {
        let input: Int?
        let cachedInput: Int?
        let cacheWriteInput: Int?
        let output: Int?
        let reasoning: Int?
    }

    struct PairwiseInput: Equatable {
        let baselineCandidateId: String
        let candidateId: String
        let isComparable: Bool
        let baselineQualityScore: Double?
        let candidateQualityScore: Double?
        let qualityDeltaPoints: Double?
        let baselineElapsedSeconds: Double?
        let candidateElapsedSeconds: Double?
        let timeDeltaPercent: Double?
        let baselineCostUsd: Double?
        let candidateCostUsd: Double?
        let costDeltaPercent: Double?
        let baselineTokens: TokenValues
        let candidateTokens: TokenValues
        let warningQuestionIds: [String]
    }

    struct DecisionEvidenceInput: Equatable {
        let currentCandidateId: String
        let candidateCandidateId: String?
        let comparisonCandidateId: String?
        let currentScore: Double?
        let candidateScore: Double?
        let qualityDeltaPoints: Double?
        let currentSeconds: Double?
        let candidateSeconds: Double?
        let timeDeltaPercent: Double?
        let currentCostUsd: Double?
        let candidateCostUsd: Double?
        let costDeltaPercent: Double?
        let warningQuestionIds: [String]
    }

    struct LocalTrendSeriesInput: Equatable {
        let candidateId: String
        let runIndices: [Int]
        let scores: [Int]
    }

    struct OfficialTrendPointInput: Equatable {
        let batchId: String
        let publishedAt: String
        let score: Double
    }

    struct OfficialTrendSeriesInput: Equatable {
        let candidateId: String
        let points: [OfficialTrendPointInput]
    }

    struct OfficialTokenInput: Equatable {
        let candidateId: String
        let values: TokenValues
    }

    struct RealizedBenefitInput: Equatable {
        let status: String
        let observedWorkUnitCount: Int
        let referenceCostWorkUnitCount: Int?
        let modelWaitWorkUnitCount: Int?
        let referenceCostDeltaUsd: Double?
        let modelWaitDeltaMs: Int?
    }

    struct RealizedBenefitPresentation: Equatable {
        let title: String
        let statusIcon: String
        let emphasis: Emphasis
        let completedWorkText: String
        let modelWaitText: String
        let referenceCostText: String
        let noteText: String
        let helpText: String
    }

    struct UsageCandidateInput: Equatable {
        let id: String
        let displayName: String
        let modelName: String
        let providerId: String?
        let effort: String
    }

    struct UsageAggregateInput: Equatable {
        let modelConfigurationId: String
        let providerId: String
        let rawModelId: String
        let reasoningEffort: String
        let completedWorkUnits: Int
        let failureCount: Int
        let sampleDays: Int
        let attributionConfidence: Double
        let behaviorObservedWorkUnits: Int?
        let behaviorCoveragePercent: Double?
        let oneShotRatePercent: Double?
    }

    struct UsageWorkloadInput: Equatable {
        let coverageStartedAtText: String
        let coverageComplete: Bool
        let aggregates: [UsageAggregateInput]
    }

    struct UsageRowPresentation: Equatable, Identifiable {
        let id: String
        let displayName: String
        let summaryText: String?
        let confidenceText: String?
        let behaviorText: String?
        let emptyText: String?
    }

    struct UsagePresentation: Equatable {
        let rows: [UsageRowPresentation]
        let emptyText: String?
        let coverageText: String
    }

    struct ComparisonInput: Equatable {
        let current: CandidateInput
        let candidate: CandidateInput
        let isManualComparison: Bool
        let decision: DecisionEvidenceInput?
        let pairwiseComparisons: [PairwiseInput]
        let displaySource: String?
        let localTrendSeries: [LocalTrendSeriesInput]
        let officialTrendSeries: [OfficialTrendSeriesInput]
        let officialTokens: [OfficialTokenInput]
    }

    struct MetricEvidence: Equatable {
        let currentScore: Double?
        let candidateScore: Double?
        let qualityDeltaPoints: Double?
        let currentSeconds: Double?
        let candidateSeconds: Double?
        let timeDeltaPercent: Double?
        let currentCostUsd: Double?
        let candidateCostUsd: Double?
        let costDeltaPercent: Double?
        let pairwiseComparable: Bool?
        let warningQuestionIds: [String]
    }

    struct QuestionRisk: Equatable {
        let warningQuestionIds: [String]
        let warningCount: Int
    }

    struct TrendPoint: Identifiable, Equatable {
        let slot: Int
        let score: Int

        var id: Int { slot }
    }

    struct TrendScale: Equatable {
        let lower: Int
        let upper: Int

        var midpoint: Int { (lower + upper) / 2 }
    }

    struct TrendData: Equatable {
        let slots: [Int]
        let current: [TrendPoint]
        let candidate: [TrendPoint]
        let scale: TrendScale

        var hasValues: Bool { !current.isEmpty || !candidate.isEmpty }
        var hasGap: Bool {
            current.count < slots.count
                || (!candidate.isEmpty && candidate.count < slots.count)
        }
    }

    struct TokenComparison: Equatable {
        let current: TokenValues?
        let candidate: TokenValues?
        let evidenceNote: String
    }

    struct ComparisonOutput: Equatable {
        let evidence: MetricEvidence?
        let trend: TrendData
        let tokens: TokenComparison
        let questionRisk: QuestionRisk
    }

    struct DecisionPresentation: Equatable {
        let title: String
        let emphasis: Emphasis
        let automaticCandidatePrefix: String
    }

    struct QuestionInput: Equatable {
        let id: String
        let shortLabel: String
        let capabilityLabel: String
    }

    struct QuestionRowPresentation: Equatable, Identifiable {
        let id: String
        let shortLabel: String
        let capabilityLabel: String
        let currentScoreText: String?
        let candidateScoreText: String?
        let showsWarning: Bool
    }

    static func present(_ input: ComparisonInput) -> ComparisonOutput {
        let pair = input.pairwiseComparisons.first {
            $0.baselineCandidateId == input.current.id
                && $0.candidateId == input.candidate.id
        }
        let evidence = metricEvidence(input, pair: pair)
        let warningQuestionIds = evidence?.warningQuestionIds ?? []
        return ComparisonOutput(
            evidence: evidence,
            trend: trendData(input),
            tokens: tokenComparison(input, pair: pair),
            questionRisk: QuestionRisk(
                warningQuestionIds: warningQuestionIds,
                warningCount: warningQuestionIds.count
            )
        )
    }

    static func realizedBenefit(
        _ summary: RealizedBenefitInput?
    ) -> RealizedBenefitPresentation? {
        guard let summary,
              summary.observedWorkUnitCount > 0,
              summary.status == "estimated" || summary.status == "unavailable" else {
            return nil
        }
        let total = summary.observedWorkUnitCount
        let costCovered = summary.referenceCostWorkUnitCount
            ?? (summary.referenceCostDeltaUsd == nil ? 0 : total)
        let waitCovered = summary.modelWaitWorkUnitCount
            ?? (summary.modelWaitDeltaMs == nil ? 0 : total)
        return RealizedBenefitPresentation(
            title: L10n.tr("历史实际切换累计"),
            statusIcon: summary.status == "estimated"
                ? "checkmark.circle.fill"
                : "info.circle",
            emphasis: summary.status == "estimated" ? .positive : .tertiary,
            completedWorkText: L10n.tr("已完成 %d 次任务", total),
            modelWaitText: realizedModelWaitText(summary.modelWaitDeltaMs),
            referenceCostText: realizedReferenceCostText(summary.referenceCostDeltaUsd),
            noteText: summary.status == "estimated"
                ? L10n.tr("根据本机使用记录估算，不代表实际账单或工作总时长。")
                : L10n.tr("记录还不够，暂时无法估算等待时间或参考费用。"),
            helpText: L10n.tr(
                "汇总历史实际切换后记录到的 %d 次已完成任务。参考费用覆盖 %d/%d 次，等待时间覆盖 %d/%d 次；与当前选中的对比组合无关，不代表实际账单或工作总时长。",
                total,
                costCovered,
                total,
                waitCovered,
                total
            )
        )
    }

    static func decisionPresentation(
        decision: String?,
        isManualComparison: Bool
    ) -> DecisionPresentation {
        let automaticCandidatePrefix = decision == "recommend"
            ? L10n.tr("推荐")
            : L10n.tr("最接近")
        if isManualComparison {
            return DecisionPresentation(
                title: L10n.tr("手动对比"),
                emphasis: .secondary,
                automaticCandidatePrefix: automaticCandidatePrefix
            )
        }
        switch decision {
        case "recommend":
            return DecisionPresentation(
                title: L10n.tr("建议切换"),
                emphasis: .positive,
                automaticCandidatePrefix: automaticCandidatePrefix
            )
        case "keep":
            return DecisionPresentation(
                title: L10n.tr("保持当前"),
                emphasis: .primary,
                automaticCandidatePrefix: automaticCandidatePrefix
            )
        case "stale":
            return DecisionPresentation(
                title: L10n.tr("结果已过期"),
                emphasis: .primary,
                automaticCandidatePrefix: automaticCandidatePrefix
            )
        case "no_usage":
            return DecisionPresentation(
                title: L10n.tr("暂无使用记录"),
                emphasis: .primary,
                automaticCandidatePrefix: automaticCandidatePrefix
            )
        default:
            return DecisionPresentation(
                title: L10n.tr("暂时无法形成建议"),
                emphasis: .primary,
                automaticCandidatePrefix: automaticCandidatePrefix
            )
        }
    }

    static func questionRows(
        questions: [QuestionInput],
        currentScores: [String: Double],
        candidateScores: [String: Double],
        warningQuestionIDs: [String]
    ) -> [QuestionRowPresentation] {
        let warnings = Set(warningQuestionIDs)
        return questions.map { question in
            QuestionRowPresentation(
                id: question.id,
                shortLabel: question.shortLabel,
                capabilityLabel: question.capabilityLabel,
                currentScoreText: questionScoreText(currentScores, id: question.id),
                candidateScoreText: questionScoreText(candidateScores, id: question.id),
                showsWarning: warnings.contains(question.id)
            )
        }
    }

    static func realUsage(
        current: UsageCandidateInput,
        candidate: UsageCandidateInput,
        workload: UsageWorkloadInput?
    ) -> UsagePresentation {
        let currentAggregate = usageAggregate(for: current, workload: workload)
        let candidateAggregate = current.id == candidate.id
            ? currentAggregate
            : usageAggregate(for: candidate, workload: workload)
        let rows = [usageRow(current, aggregate: currentAggregate)]
            + (current.id == candidate.id
                ? []
                : [usageRow(candidate, aggregate: candidateAggregate)])
        let emptyText: String?
        if currentAggregate == nil && candidateAggregate == nil {
            emptyText = current.id == candidate.id
                ? L10n.tr("当前配置暂无可归属的本地使用记录")
                : L10n.tr("当前与候选均暂无可归属的本地使用记录")
        } else {
            emptyText = nil
        }
        let coverageText: String
        if let workload {
            coverageText = workload.coverageComplete
                ? L10n.tr(
                    "日志覆盖自 %@；它是行为信号，不证明任务成功。",
                    workload.coverageStartedAtText
                )
                : L10n.tr("日志仍在建立覆盖范围；当前样本可能不完整，也不证明任务成功。")
        } else {
            coverageText = L10n.tr("仅统计本机可读取的 Codex 日志；其他连接暂无真实工作观察。")
        }
        return UsagePresentation(
            rows: rows,
            emptyText: emptyText,
            coverageText: coverageText
        )
    }

    static func qualityChange(
        deltaPoints: Double?,
        sameConfiguration: Bool
    ) -> Presentation {
        guard let delta = deltaPoints else {
            return Presentation(
                text: sameConfiguration ? L10n.tr("相同") : L10n.tr("不可比较"),
                emphasis: .tertiary
            )
        }
        let points = Int(abs(delta).rounded())
        guard points > 0 else {
            return Presentation(text: L10n.tr("相同"), emphasis: .tertiary)
        }
        return delta > 0
            ? Presentation(text: L10n.tr("高 %d 分", points), emphasis: .positive)
            : Presentation(text: L10n.tr("少 %d 分", points), emphasis: .warning)
    }

    static func timeChange(
        deltaPercent: Double?,
        sameConfiguration: Bool
    ) -> Presentation {
        percentPresentation(
            deltaPercent,
            sameConfiguration: sameConfiguration,
            improvementFormat: "快 %d%%",
            regressionFormat: "慢 %d%%"
        )
    }

    static func costChange(
        deltaPercent: Double?,
        sameConfiguration: Bool
    ) -> Presentation {
        percentPresentation(
            deltaPercent,
            sameConfiguration: sameConfiguration,
            improvementFormat: "省 %d%%",
            regressionFormat: "多 %d%%"
        )
    }

    static func qualityGuard(
        sameConfiguration: Bool,
        isManualComparison: Bool,
        pairwiseComparable: Bool?,
        status: String?,
        rule: String?,
        thresholdPoints: Double?
    ) -> Presentation {
        let resolvedStatus: String?
        if sameConfiguration {
            resolvedStatus = "current_configuration"
        } else if isManualComparison {
            resolvedStatus = pairwiseComparable == true
                ? "manual_evidence"
                : "manual_unavailable"
        } else {
            resolvedStatus = status
        }
        return qualityGuard(
            status: resolvedStatus,
            rule: rule,
            thresholdPoints: thresholdPoints
        )
    }

    static func qualityGuard(
        status: String?,
        rule: String?,
        thresholdPoints: Double?
    ) -> Presentation {
        switch status {
        case "current_configuration":
            return Presentation(text: L10n.tr("当前配置"), emphasis: .tertiary)
        case "manual_unavailable":
            return Presentation(
                text: L10n.tr("手动候选 · 不可比较"),
                emphasis: .secondary
            )
        case "manual_evidence":
            return Presentation(
                text: L10n.tr("手动候选 · 仅作对比"),
                emphasis: .secondary
            )
        case "current_is_best":
            return Presentation(text: L10n.tr("当前已是最高分"), emphasis: .secondary)
        case "quality_improved":
            return Presentation(text: L10n.tr("质量不降"), emphasis: .positive)
        case "passed", "failed":
            guard let thresholdPoints else {
                return Presentation(
                    text: status == "passed"
                        ? L10n.tr("质量要求已通过")
                        : L10n.tr("质量要求未通过"),
                    emphasis: status == "passed" ? .positive : .secondary
                )
            }
            let label = rule == "minimum_gain" ? L10n.tr("质量门槛") : L10n.tr("质量护栏")
            let points = Int(thresholdPoints.rounded())
            return Presentation(
                text: L10n.tr(
                    "%@ %d 分 · %@",
                    label,
                    points,
                    status == "passed" ? L10n.tr("已通过") : L10n.tr("未通过")
                ),
                emphasis: status == "passed" ? .positive : .secondary
            )
        default:
            return Presentation(text: L10n.tr("总分暂不可比"), emphasis: .tertiary)
        }
    }

    static func routeEvidenceText(
        currentRoute: String?,
        candidateRoute: String?
    ) -> String {
        switch (currentRoute, candidateRoute) {
        case (nil, nil):
            return L10n.tr("未记录")
        case (.some(_), nil), (nil, .some(_)):
            return L10n.tr("部分未记录")
        case let (.some(lhs), .some(rhs)) where lhs == rhs:
            return shortIdentifier(lhs)
        default:
            return L10n.tr("不同，见配置证据")
        }
    }

    static func configurationDecisionText(
        decision: String,
        target: String,
        benefitKind: String?,
        reductionPercent: Double?,
        gainPoints: Double?
    ) -> String {
        switch decision {
        case "recommend":
            switch benefitKind {
            case "time":
                return L10n.tr("建议 %@ · 快 %d%%", target, Int((reductionPercent ?? 0).rounded()))
            case "reference_cost":
                return L10n.tr("建议 %@ · 省 %d%%", target, Int((reductionPercent ?? 0).rounded()))
            case "quality":
                return L10n.tr("建议 %@ · 质量 +%d", target, Int((gainPoints ?? 0).rounded()))
            default:
                return L10n.tr("建议 %@", target)
            }
        case "keep":
            return L10n.tr("保持当前配置")
        case "stale":
            return L10n.tr("结果过期 · 需要复测")
        case "no_usage":
            return L10n.tr("暂无使用记录")
        default:
            return L10n.tr("证据不足 · 需要快测")
        }
    }

    private static func questionScoreText(
        _ scores: [String: Double],
        id: String
    ) -> String? {
        scores.first { $0.key.caseInsensitiveCompare(id) == .orderedSame }
            .map { String(Int($0.value.rounded())) }
    }

    static func recommendationReasonText(
        status: String,
        reason: String?,
        sourceResolutionReason: String?,
        selectedSourceMode: String?,
        qualityTradeoff: Bool,
        scoreDelta: Double?,
        timeReductionPercent: Double?,
        costReductionPercent: Double?
    ) -> String {
        if qualityTradeoff {
            return L10n.tr("速度或费用收益明确，但存在质量下降，请确认取舍。")
        }
        switch status {
        case "recommend":
            switch reason {
            case "material_quality_gain", "quality_gain_with_tradeoff":
                return L10n.tr(
                    "总分提高 %d 分，达到质量优先的推荐门槛。",
                    Int((scoreDelta ?? 0).rounded())
                )
            case "material_time_gain":
                guard let timeReductionPercent else {
                    return L10n.tr("候选满足质量要求，并在当前数据中耗时更短。")
                }
                return L10n.tr(
                    "总耗时缩短 %d%%，且质量仍在允许范围内。",
                    Int(abs(timeReductionPercent).rounded())
                )
            case "material_reference_cost_gain":
                guard let costReductionPercent else {
                    return L10n.tr("候选满足质量要求，并在当前数据中参考费用更低。")
                }
                return L10n.tr(
                    "参考费用降低 %d%%，且质量仍在允许范围内。",
                    Int(abs(costReductionPercent).rounded())
                )
            default:
                return L10n.tr("质量满足当前策略要求，且速度或参考费用有明确改善。")
            }
        case "keep":
            return L10n.tr("未找到同时满足当前质量与收益要求的候选。")
        case "stale":
            return L10n.tr("当前结果超过有效期，不用于新的切换判断。")
        case "no_usage":
            return L10n.tr("识别到首次真实使用后，再为对应精确配置匹配数据。")
        default:
            if sourceResolutionReason == "mixed_source_resolution_blocked" {
                return L10n.tr("多个活动配置无法使用同一来源，当前不计算混合收益。")
            }
            if selectedSourceMode == "official_snapshot" {
                return L10n.tr("官网榜单尚未覆盖当前模型和思考档位，建议使用本机实测。")
            }
            return L10n.tr("当前来源缺少完整同轮对比，完成快测后再形成建议。")
        }
    }

    private static func percentPresentation(
        _ value: Double?,
        sameConfiguration: Bool,
        improvementFormat: String,
        regressionFormat: String
    ) -> Presentation {
        guard let value else {
            return Presentation(
                text: sameConfiguration ? L10n.tr("相同") : L10n.tr("不可比较"),
                emphasis: .tertiary
            )
        }
        let isImprovement = value >= 0
        let format = isImprovement ? improvementFormat : regressionFormat
        return Presentation(
            text: L10n.tr(format, Int(abs(value).rounded())),
            emphasis: isImprovement ? .positive : .warning
        )
    }

    private static func shortIdentifier(_ value: String) -> String {
        value.count > 18 ? "\(value.prefix(8))…\(value.suffix(6))" : value
    }

    private static func metricEvidence(
        _ input: ComparisonInput,
        pair: PairwiseInput?
    ) -> MetricEvidence? {
        if let pair {
            return MetricEvidence(
                currentScore: pair.baselineQualityScore,
                candidateScore: pair.candidateQualityScore,
                qualityDeltaPoints: pair.qualityDeltaPoints,
                currentSeconds: pair.baselineElapsedSeconds,
                candidateSeconds: pair.candidateElapsedSeconds,
                timeDeltaPercent: pair.timeDeltaPercent,
                currentCostUsd: pair.baselineCostUsd,
                candidateCostUsd: pair.candidateCostUsd,
                costDeltaPercent: pair.costDeltaPercent,
                pairwiseComparable: pair.isComparable,
                warningQuestionIds: pair.warningQuestionIds
            )
        }
        if input.current.id == input.candidate.id {
            return MetricEvidence(
                currentScore: input.current.score,
                candidateScore: input.candidate.score,
                qualityDeltaPoints: nil,
                currentSeconds: input.current.elapsedSeconds,
                candidateSeconds: input.candidate.elapsedSeconds,
                timeDeltaPercent: nil,
                currentCostUsd: input.current.referenceCostUsd,
                candidateCostUsd: input.candidate.referenceCostUsd,
                costDeltaPercent: nil,
                pairwiseComparable: nil,
                warningQuestionIds: []
            )
        }
        guard !input.isManualComparison,
              let decision = input.decision,
              decision.currentCandidateId == input.current.id else {
            return nil
        }
        let targetId = decision.candidateCandidateId
            ?? decision.comparisonCandidateId
            ?? decision.currentCandidateId
        guard targetId == input.candidate.id else { return nil }
        return MetricEvidence(
            currentScore: decision.currentScore,
            candidateScore: decision.candidateScore,
            qualityDeltaPoints: decision.qualityDeltaPoints,
            currentSeconds: decision.currentSeconds,
            candidateSeconds: decision.candidateSeconds,
            timeDeltaPercent: decision.timeDeltaPercent,
            currentCostUsd: decision.currentCostUsd,
            candidateCostUsd: decision.candidateCostUsd,
            costDeltaPercent: decision.costDeltaPercent,
            pairwiseComparable: nil,
            warningQuestionIds: decision.warningQuestionIds
        )
    }

    private static func trendData(_ input: ComparisonInput) -> TrendData {
        if input.displaySource == "official_snapshot" {
            return officialTrendData(input)
        }
        let currentPoints = localTrendPoints(
            candidateId: input.current.id,
            series: input.localTrendSeries
        )
        let candidatePoints = input.current.id == input.candidate.id
            ? []
            : localTrendPoints(
                candidateId: input.candidate.id,
                series: input.localTrendSeries
            )
        let slots = visibleTrendSlots(
            currentSlots: currentPoints.map(\.slot),
            candidateSlots: candidatePoints.map(\.slot)
        )
        let visibleCurrent = currentPoints.filter { slots.contains($0.slot) }
        let visibleCandidate = candidatePoints.filter { slots.contains($0.slot) }
        return TrendData(
            slots: slots,
            current: visibleCurrent,
            candidate: visibleCandidate,
            scale: trendScale(
                scores: (visibleCurrent + visibleCandidate).map(\.score)
            )
        )
    }

    private static func localTrendPoints(
        candidateId: String,
        series: [LocalTrendSeriesInput]
    ) -> [TrendPoint] {
        guard let trend = series.first(where: { $0.candidateId == candidateId }) else {
            return []
        }
        return zip(trend.runIndices, trend.scores)
            .map { TrendPoint(slot: $0.0, score: $0.1) }
            .sorted { $0.slot < $1.slot }
    }

    private static func officialTrendData(_ input: ComparisonInput) -> TrendData {
        let currentSource = input.officialTrendSeries.first {
            $0.candidateId == input.current.id
        }?.points ?? []
        let candidateSource = input.current.id == input.candidate.id
            ? []
            : (input.officialTrendSeries.first {
                $0.candidateId == input.candidate.id
            }?.points ?? [])
        let ordered = (currentSource + candidateSource).sorted {
            if $0.publishedAt != $1.publishedAt {
                return $0.publishedAt < $1.publishedAt
            }
            return $0.batchId < $1.batchId
        }
        var batchIds: [String] = []
        for point in ordered where !batchIds.contains(point.batchId) {
            batchIds.append(point.batchId)
        }
        batchIds = Array(batchIds.suffix(6))
        let slotByBatchId = Dictionary(
            uniqueKeysWithValues: batchIds.enumerated().map {
                ($0.element, $0.offset)
            }
        )
        let currentPoints = officialTrendPoints(
            currentSource,
            slotByBatchId: slotByBatchId
        )
        let candidatePoints = officialTrendPoints(
            candidateSource,
            slotByBatchId: slotByBatchId
        )
        return TrendData(
            slots: Array(batchIds.indices),
            current: currentPoints,
            candidate: candidatePoints,
            scale: trendScale(
                scores: (currentPoints + candidatePoints).map(\.score)
            )
        )
    }

    private static func officialTrendPoints(
        _ points: [OfficialTrendPointInput],
        slotByBatchId: [String: Int]
    ) -> [TrendPoint] {
        points.compactMap { point in
            guard let slot = slotByBatchId[point.batchId] else { return nil }
            return TrendPoint(slot: slot, score: Int(point.score.rounded()))
        }
        .sorted { $0.slot < $1.slot }
    }

    private static func visibleTrendSlots(
        currentSlots: [Int],
        candidateSlots: [Int]
    ) -> [Int] {
        guard let latestSlot = (currentSlots + candidateSlots).max(),
              latestSlot >= 0 else {
            return []
        }
        return Array(max(0, latestSlot - 5)...latestSlot)
    }

    private static func trendScale(scores: [Int]) -> TrendScale {
        let minimumSpan = 10
        guard let rawMinimum = scores.min(), let rawMaximum = scores.max() else {
            return TrendScale(lower: 0, upper: 100)
        }

        var lower = max(0, Int(floor(Double(rawMinimum) / 5.0)) * 5)
        var upper = min(100, Int(ceil(Double(rawMaximum) / 5.0)) * 5)
        while upper - lower < minimumSpan {
            if lower >= 5 {
                lower -= 5
            } else if upper <= 95 {
                upper += 5
            } else {
                break
            }
        }
        return TrendScale(lower: lower, upper: upper)
    }

    private static func tokenComparison(
        _ input: ComparisonInput,
        pair: PairwiseInput?
    ) -> TokenComparison {
        if input.displaySource == "official_snapshot" {
            return TokenComparison(
                current: input.officialTokens.first {
                    $0.candidateId == input.current.id
                }?.values,
                candidate: input.officialTokens.first {
                    $0.candidateId == input.candidate.id
                }?.values,
                evidenceNote: L10n.tr("来自当前官网快照；只展示该来源发布的同题包成功调用汇总。")
            )
        }
        return TokenComparison(
            current: pair?.baselineTokens,
            candidate: pair?.candidateTokens,
            evidenceNote: L10n.tr("来自后端权威对比投影；缺失字段保持不可用。")
        )
    }

    private static func realizedModelWaitText(_ delta: Int?) -> String {
        guard let delta else { return L10n.tr("暂不可用") }
        if delta == 0 { return L10n.tr("基本相同") }
        return delta < 0
            ? L10n.tr("累计约少 %@", benefitDurationText(abs(delta)))
            : L10n.tr("累计约多 %@", benefitDurationText(abs(delta)))
    }

    private static func realizedReferenceCostText(_ delta: Double?) -> String {
        guard let delta else { return L10n.tr("暂不可用") }
        if abs(delta) < 0.005 { return L10n.tr("基本相同") }
        let amount = String(format: "$%.2f", abs(delta))
        return delta < 0
            ? L10n.tr("累计约省 %@", amount)
            : L10n.tr("累计约多 %@", amount)
    }

    private static func benefitDurationText(_ milliseconds: Int) -> String {
        let seconds = max(1, Int((Double(milliseconds) / 1000).rounded()))
        if seconds < 60 { return L10n.tr("%d 秒", seconds) }
        let minutes = seconds / 60
        let remainder = seconds % 60
        if minutes < 60 {
            return remainder == 0
                ? L10n.tr("%d 分钟", minutes)
                : L10n.tr("%d 分 %d 秒", minutes, remainder)
        }
        let hours = minutes / 60
        let remainingMinutes = minutes % 60
        return remainingMinutes == 0
            ? L10n.tr("%d 小时", hours)
            : L10n.tr("%d 小时 %d 分", hours, remainingMinutes)
    }

    private static func usageAggregate(
        for candidate: UsageCandidateInput,
        workload: UsageWorkloadInput?
    ) -> UsageAggregateInput? {
        if let exact = workload?.aggregates.first(where: {
            $0.modelConfigurationId == candidate.id
        }) {
            return exact
        }
        let matches = workload?.aggregates.filter {
            $0.rawModelId.caseInsensitiveCompare(candidate.modelName) == .orderedSame
                && $0.reasoningEffort.caseInsensitiveCompare(candidate.effort) == .orderedSame
        } ?? []
        if let providerId = candidate.providerId {
            let providerMatches = matches.filter {
                $0.providerId.caseInsensitiveCompare(providerId) == .orderedSame
            }
            if providerMatches.count == 1 {
                return providerMatches[0]
            }
        }
        return matches.count == 1 ? matches[0] : nil
    }

    private static func usageRow(
        _ candidate: UsageCandidateInput,
        aggregate: UsageAggregateInput?
    ) -> UsageRowPresentation {
        guard let aggregate else {
            return UsageRowPresentation(
                id: candidate.id,
                displayName: candidate.displayName,
                summaryText: nil,
                confidenceText: nil,
                behaviorText: nil,
                emptyText: L10n.tr("尚无可归属的本地使用记录")
            )
        }
        let behaviorText: String?
        if let observed = aggregate.behaviorObservedWorkUnits, observed > 0 {
            let coverage = aggregate.behaviorCoveragePercent.map {
                L10n.tr("行为覆盖 %d%%", Int($0.rounded()))
            } ?? L10n.tr("行为覆盖未知")
            let oneShot = aggregate.oneShotRatePercent.map {
                L10n.tr("one-shot %d%%", Int($0.rounded()))
            } ?? L10n.tr("one-shot 证据积累中")
            behaviorText = L10n.tr("%@ · %@ · 仅看该配置自身", coverage, oneShot)
        } else {
            behaviorText = nil
        }
        return UsageRowPresentation(
            id: candidate.id,
            displayName: candidate.displayName,
            summaryText: L10n.tr(
                "完成 %d · 失败 %d · %d 天样本",
                aggregate.completedWorkUnits,
                aggregate.failureCount,
                aggregate.sampleDays
            ),
            confidenceText: L10n.tr(
                "日志身份置信度 %d%%",
                Int((aggregate.attributionConfidence * 100).rounded())
            ),
            behaviorText: behaviorText,
            emptyText: nil
        )
    }

}
