import Foundation

private var failureCount = 0

private func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() {
        failureCount += 1
        fputs("FAIL: \(message)\n", stderr)
    }
}

private func recommendationDecision(
    candidateID: String? = "candidate",
    comparisonCandidateID: String? = nil,
    decision: String = "recommend",
    reason: String = "material_time_gain",
    qualityDelta: Double? = 2.4,
    timeReduction: Double? = 24.6,
    costReduction: Double? = nil
) -> BridgeRecommendationDecisionV2 {
    BridgeRecommendationDecisionV2(
        currentModelConfigurationId: "current",
        candidateModelConfigurationId: candidateID,
        comparisonCandidateModelConfigurationId: comparisonCandidateID,
        comparisonCandidateReasons: nil,
        decision: decision,
        reason: reason,
        qualityTradeoff: false,
        qualityWarningQuestionIds: [],
        qualityGuard: nil,
        quality: BridgeRecommendationQualityV2(
            currentScore: 80,
            candidateScore: 82.4,
            scoreDelta: qualityDelta
        ),
        time: BridgeRecommendationTimeV2(
            currentSeconds: 100,
            candidateSeconds: 75.4,
            reductionPercent: timeReduction
        ),
        referenceCost: BridgeRecommendationCostV2(
            currentUsd: nil,
            candidateUsd: nil,
            reductionPercent: costReduction
        ),
        primaryBenefit: BridgeRecommendationPrimaryBenefitV2(
            kind: "time",
            reductionPercent: timeReduction,
            gainPoints: nil
        )
    )
}

private func portfolio(
    status: String,
    sourceResolutionReason: String? = nil
) -> BridgeRecommendationPortfolioV2 {
    let sourceReason = sourceResolutionReason.map { "\"\($0)\"" } ?? "null"
    let json = """
    {
      "schemaVersion": 2,
      "sourceModeByConfigurationId": {},
      "sourceResolutionReason": \(sourceReason),
      "preference": "smart",
      "status": "\(status)",
      "decisions": [],
      "testableCandidateIds": [],
      "unmappedActiveSessionCount": 0
    }
    """
    return try! JSONDecoder().decode(
        BridgeRecommendationPortfolioV2.self,
        from: Data(json.utf8)
    )
}

private func verifySourceAndPreferenceLabels() {
    let automatic = RadarPresenter.sourceLabels(
        selectedSourceMode: "auto",
        displaySource: "official_snapshot"
    )
    expect(automatic.control == "自动 · 官网榜单", "automatic source should stay compact")
    expect(
        automatic.accessibilityValue == "来源：官网榜单（自动）",
        "automatic source should retain full accessibility copy"
    )
    let explicit = RadarPresenter.sourceLabels(
        selectedSourceMode: "local_evaluation",
        displaySource: "official_snapshot"
    )
    expect(explicit.control == "本机实测", "explicit source should override resolved source")
    expect(
        RadarPresenter.preferenceLabel("cost") == "目标：费用优先",
        "preference should be display-only mapping"
    )
}

private func verifyDecisionPresentation() {
    let decision = recommendationDecision(
        candidateID: nil,
        comparisonCandidateID: "comparison"
    )
    let presentation = RadarPresenter.decision(
        evidenceUpdating: false,
        hasSnapshotRefreshIssue: false,
        hasResumableRun: false,
        isUnmappedCurrentModel: false,
        detectedCurrentModelIdentity: nil,
        selectedSourceMode: "auto",
        portfolio: portfolio(status: "recommend"),
        decision: decision,
        candidateLabel: "GPT-5.6 High",
        candidateCostCoverage: "partial"
    )
    expect(
        presentation.candidateConfigurationID == "comparison",
        "comparison candidate should remain the display fallback"
    )
    expect(presentation.comparisonLabel == "切换建议", "recommend should label the comparison")
    expect(presentation.title == "建议切换到 GPT-5.6 High", "decision title should use projected label")
    expect(
        presentation.reason == "总耗时缩短 25%，且质量仍在允许范围内。",
        "decision reason should preserve the existing threshold copy"
    )
    expect(presentation.qualityText == "+2 分", "quality metric should remain signed")
    expect(presentation.timeText == "快 25%", "time metric should remain directional")
    expect(presentation.referenceCostText == "部分未知", "partial cost should remain explicit")

    let gated = RadarPresenter.decision(
        evidenceUpdating: true,
        hasSnapshotRefreshIssue: false,
        hasResumableRun: false,
        isUnmappedCurrentModel: false,
        detectedCurrentModelIdentity: nil,
        selectedSourceMode: "auto",
        portfolio: portfolio(status: "recommend"),
        decision: decision,
        candidateLabel: "GPT-5.6 High",
        candidateCostCoverage: nil
    )
    expect(gated.title == nil && gated.reason == nil, "working state should defer to runtime copy")

    let unmapped = RadarPresenter.decision(
        evidenceUpdating: false,
        hasSnapshotRefreshIssue: false,
        hasResumableRun: false,
        isUnmappedCurrentModel: true,
        detectedCurrentModelIdentity: "GPT-5.6 Ultra",
        selectedSourceMode: "auto",
        portfolio: portfolio(status: "keep"),
        decision: nil,
        candidateLabel: nil,
        candidateCostCoverage: nil
    )
    expect(unmapped.title == "当前档位未纳入比较", "unmapped title should remain explicit")
    expect(
        unmapped.reason?.contains("榜单仍展示已启用档位的结果") == true,
        "unmapped reason should keep the valid leaderboard boundary"
    )
}

private func verifySessionSummary() {
    let sessions = [
        RadarPresenter.SessionInput(
            id: "a",
            source: "codex",
            model: "gpt-5.6",
            effort: "high",
            title: "重构任务",
            context: "Codex · MQD · GPT-5.6 High",
            isEvaluationSession: false
        ),
        RadarPresenter.SessionInput(
            id: "b",
            source: "claude",
            model: "claude-opus",
            effort: nil,
            title: "评审任务",
            context: "Claude Code · MQD · Claude Opus",
            isEvaluationSession: false
        ),
        RadarPresenter.SessionInput(
            id: "eval",
            source: "codex",
            model: "gpt-5.6",
            effort: "high",
            title: "评测",
            context: "Codex",
            isEvaluationSession: true
        ),
    ]
    let summary = RadarPresenter.sessionSummary(
        sessions: sessions,
        isCurrentModelAutomatic: true,
        currentModelDetectionStatus: "recent"
    )
    expect(summary.visibleSessionIDs == ["a", "b"], "evaluation sessions should be excluded")
    expect(summary.title == "2 个活动会话 · 2 个配置", "session/configuration counts should be stable")
    expect(
        summary.detail == "重构任务 · Codex · MQD · GPT-5.6 High",
        "first session detail should remain task-first"
    )
    expect(summary.accessibilityLabel.contains("重构任务"), "accessibility should include detail")

    let manual = RadarPresenter.sessionSummary(
        sessions: [],
        isCurrentModelAutomatic: false,
        currentModelDetectionStatus: nil
    )
    expect(manual.title == "当前模型已手动指定", "manual empty state should remain explicit")

    let activeUsage = RadarPresenter.activeUsage(
        sessions: [
            RadarPresenter.ActiveUsageSessionInput(
                id: "a",
                sourceDisplayName: "Codex",
                model: "gpt-5.6",
                effort: "high"
            ),
            RadarPresenter.ActiveUsageSessionInput(
                id: "b",
                sourceDisplayName: "Codex",
                model: "gpt-5.6",
                effort: "low"
            ),
        ]
    )
    expect(activeUsage.summary?.identity == "GPT-5.6", "same-model sessions should share one identity")
    expect(activeUsage.summary?.detail == "low × 1、high × 1", "effort counts should use stable order")
    expect(activeUsage.sessionIdentities["a"] == "GPT-5.6 High", "session identity should include effort")
    expect(activeUsage.sessionDetails["a"] == "Codex · GPT-5.6 High", "session detail should include source")

    let incompleteUsage = RadarPresenter.activeUsage(
        sessions: [
            RadarPresenter.ActiveUsageSessionInput(
                id: "unknown",
                sourceDisplayName: "Claude Code",
                model: nil,
                effort: nil
            ),
        ]
    )
    expect(incompleteUsage.summary?.identity == "多个会话", "missing model identity should stay explicit")
    expect(incompleteUsage.sessionDetails["unknown"] == "Claude Code", "source should be the row fallback")
}

private func verifyCanonicalLeaderboardProjection() {
    let item = RadarPresenter.LeaderboardItemInput(
        configurationID: "candidate",
        isCurrent: true,
        isRecommended: false
    )
    let official = RadarPresenter.CanonicalLeaderboardRow(
        configurationID: "candidate",
        alternateConfigurationID: nil,
        rank: 2,
        targetLabels: ["Highest score"],
        decisionTagKinds: ["recommended", "speed"]
    )
    let local = RadarPresenter.CanonicalLeaderboardRow(
        configurationID: "local-id",
        alternateConfigurationID: "candidate",
        rank: 7,
        targetLabels: ["本机稳定"],
        decisionTagKinds: []
    )
    let officialPresentation = RadarPresenter.leaderboardRow(
        item: item,
        displaySource: "official_snapshot",
        portfolioStatus: "keep",
        officialRows: [official],
        localRows: [local]
    )
    expect(officialPresentation.rank == 2, "official rank must come from official projection")
    expect(
        officialPresentation.tags == ["当前在用", "推荐", "速度优选"],
        "official tags must use publisher decision semantics instead of target labels"
    )

    let localPresentation = RadarPresenter.leaderboardRow(
        item: item,
        displaySource: "local_evaluation",
        portfolioStatus: "keep",
        officialRows: [official],
        localRows: [local]
    )
    expect(localPresentation.rank == 7, "local rank must come from local projection")
    expect(
        localPresentation.tags == ["当前在用", "本机稳定"],
        "local labels must not leak official projection"
    )

    let officialWithoutDecisionTag = RadarPresenter.leaderboardRow(
        item: RadarPresenter.LeaderboardItemInput(
            configurationID: "candidate",
            isCurrent: false,
            isRecommended: true
        ),
        displaySource: "official_snapshot",
        portfolioStatus: "recommend",
        officialRows: [RadarPresenter.CanonicalLeaderboardRow(
            configurationID: "candidate",
            alternateConfigurationID: nil,
            rank: 1,
            targetLabels: ["Highest score"],
            decisionTagKinds: []
        )],
        localRows: []
    )
    expect(
        !officialWithoutDecisionTag.tags.contains("推荐"),
        "highest-score targets and local recommendation state must not create a second official recommendation"
    )
}

private func verifySurfaceCopy() {
    let official = RadarPresenter.surface(
        displaySource: "official_snapshot",
        selectedSourceMode: "official_snapshot",
        portfolioStatus: "stale",
        evidenceUpdating: true,
        hasStableDashboard: true,
        completeQuestionSetLabel: "完整五题",
        officialQuestionPackVersion: "qpack-v2",
        localQuestionPackVersion: "qpack-local"
    )
    expect(official.rankingContext == "上次榜单 · 完整五题", "working context should identify stable evidence")
    expect(official.questionPackVersion == "qpack-v2", "official pack must come from official source")
    expect(official.emptyTitle == "扫描进行中", "working empty title should take priority")
    expect(
        official.emptyReason == "正在生成本轮榜单，完成后会自动显示完整结果。",
        "working empty reason should take priority"
    )

    let expired = RadarPresenter.surface(
        displaySource: "official_snapshot",
        selectedSourceMode: "official_snapshot",
        portfolioStatus: "stale",
        evidenceUpdating: false,
        hasStableDashboard: false,
        completeQuestionSetLabel: "完整五题",
        officialQuestionPackVersion: "qpack-v2",
        localQuestionPackVersion: "qpack-local"
    )
    expect(expired.emptyTitle == "结果已过期", "idle stale title should remain explicit")
}

private func verifyExportAndReferenceCostPresentation() {
    let recommended = RadarPresenter.leaderboardExportSemantics(
        decisionTagKinds: ["value", "recommended"]
    )
    expect(recommended.isRecommended, "backend recommendation tags should drive export emphasis")

    let rankOnly = RadarPresenter.leaderboardExportSemantics(decisionTagKinds: ["value"])
    expect(!rankOnly.isRecommended, "rank or value tags alone must not imply a recommendation")

    let partial = RadarPresenter.referenceCost(value: 1.23, coverage: "partial")
    expect(partial.text == "≥$1.23", "partial reference cost should expose a lower bound")
    expect(partial.helpText.contains("仍有调用费用未知"), "partial coverage should stay explicit")

    let complete = RadarPresenter.referenceCost(value: 1.23, coverage: "complete")
    expect(complete.text == "$1.23", "complete reference cost should use the exact estimate")
    expect(complete.helpText.contains("完整参考费用"), "complete coverage should stay explicit")

    let unavailable = RadarPresenter.referenceCost(value: nil, coverage: nil)
    expect(unavailable.text == "未知", "missing reference cost should remain unavailable")
}

private func verifyEvidenceAvailabilityProjection() {
    let contracts = (1...5).map {
        RadarPresenter.QuestionContractInput(id: "q\($0)", scoreMax: 20)
    }
    let completeResults = (1...5).map {
        RadarPresenter.QuestionResultContractInput(id: "q\($0)", semanticTotal: 20)
    }
    let current = RadarPresenter.evidenceAvailability(
        RadarPresenter.EvidenceAvailabilityInput(
            scoringMode: "semantic_q1_q5_equal_v2",
            questionCompleted: 5,
            hasQuestionResults: true,
            hasLatestValidAt: true,
            hasLatestAttemptStatus: true,
            isCurrentPackComparable: true,
            isInCurrentOperation: false,
            isCurrentRunEligible: true,
            hasLatestAttemptError: false,
            hasOverallScore: true,
            questionContracts: contracts,
            questionResults: completeResults
        )
    )
    expect(!current.requiresCurrentPackRescan, "current evidence should not require a rescan")
    expect(current.canDisplayCurrentQuestionScores, "current question scores should be visible")
    expect(current.canDisplayCurrentOverallScore, "a complete current pack should expose the total")
    expect(current.isLeaderboardExportable, "eligible complete evidence should be exportable")

    let legacy = RadarPresenter.evidenceAvailability(
        RadarPresenter.EvidenceAvailabilityInput(
            scoringMode: "legacy",
            questionCompleted: 5,
            hasQuestionResults: true,
            hasLatestValidAt: true,
            hasLatestAttemptStatus: false,
            isCurrentPackComparable: false,
            isInCurrentOperation: false,
            isCurrentRunEligible: true,
            hasLatestAttemptError: false,
            hasOverallScore: true,
            questionContracts: contracts,
            questionResults: completeResults
        )
    )
    expect(legacy.requiresCurrentPackRescan, "legacy evidence should require the current pack")
    expect(!legacy.canDisplayCurrentQuestionScores, "legacy question scores must stay hidden")
    expect(!legacy.isLeaderboardExportable, "legacy evidence must not enter the export")

    let inProgress = RadarPresenter.evidenceAvailability(
        RadarPresenter.EvidenceAvailabilityInput(
            scoringMode: "semantic_q1_q5_equal_v2",
            questionCompleted: 0,
            hasQuestionResults: false,
            hasLatestValidAt: false,
            hasLatestAttemptStatus: false,
            isCurrentPackComparable: true,
            isInCurrentOperation: true,
            isCurrentRunEligible: true,
            hasLatestAttemptError: false,
            hasOverallScore: false,
            questionContracts: contracts,
            questionResults: []
        )
    )
    expect(inProgress.canDisplayCurrentQuestionScores, "an active current-pack run should expose its columns")
    expect(!inProgress.canDisplayCurrentOverallScore, "an incomplete run must not expose a total")

    var incompleteResults = completeResults
    incompleteResults[4] = RadarPresenter.QuestionResultContractInput(
        id: "q5",
        semanticTotal: 10
    )
    let incomplete = RadarPresenter.evidenceAvailability(
        RadarPresenter.EvidenceAvailabilityInput(
            scoringMode: "semantic_q1_q5_equal_v2",
            questionCompleted: 5,
            hasQuestionResults: true,
            hasLatestValidAt: true,
            hasLatestAttemptStatus: true,
            isCurrentPackComparable: true,
            isInCurrentOperation: false,
            isCurrentRunEligible: true,
            hasLatestAttemptError: false,
            hasOverallScore: true,
            questionContracts: contracts,
            questionResults: incompleteResults
        )
    )
    expect(incomplete.canDisplayCurrentQuestionScores, "valid partial current scores should remain visible")
    expect(!incomplete.canDisplayCurrentOverallScore, "mismatched score totals must fail completeness")
    expect(!incomplete.isLeaderboardExportable, "incomplete totals must stay out of exports")

    let failed = RadarPresenter.evidenceAvailability(
        RadarPresenter.EvidenceAvailabilityInput(
            scoringMode: "semantic_q1_q5_equal_v2",
            questionCompleted: 5,
            hasQuestionResults: true,
            hasLatestValidAt: true,
            hasLatestAttemptStatus: true,
            isCurrentPackComparable: true,
            isInCurrentOperation: false,
            isCurrentRunEligible: true,
            hasLatestAttemptError: true,
            hasOverallScore: true,
            questionContracts: contracts,
            questionResults: completeResults
        )
    )
    expect(!failed.isLeaderboardExportable, "failed latest attempts must stay out of exports")
}

private func verifyQuestionAndDecisionProjection() {
    let available = [
        QuestionSemantic(
            questionNumber: 1,
            questionId: "q1",
            capabilityId: "reasoning",
            capabilityLabel: "推理",
            detailLabel: "推理细节",
            scoreMax: 20
        ),
        QuestionSemantic(
            questionNumber: 2,
            questionId: "q2",
            capabilityId: "coding",
            capabilityLabel: "编码",
            detailLabel: "编码细节",
            scoreMax: 20
        ),
    ]
    let selected = RadarPresenter.relevantQuestionSemantics(
        available: available,
        runQuestionIDs: ["q2"]
    )
    expect(selected.map(\.questionId) == ["q2"], "run question IDs should select the compatible semantics")
    expect(
        RadarPresenter.decisionEmphasis("recommend") == .recommended,
        "recommend decisions should project semantic emphasis"
    )
    expect(
        RadarPresenter.decisionEmphasis("keep") == .neutral,
        "non-recommend decisions should remain neutral"
    )
}

private func verifyRadarDashboardSelection() {
    expect(
        RadarPresenter.shouldUseCurrentDashboard(
            runID: "run-quick",
            evidenceSourceSnapshotID: "local:run-quick"
        ),
        "the dashboard selected by local evidence should replace the stable dashboard"
    )
    expect(
        !RadarPresenter.shouldUseCurrentDashboard(
            runID: "run-working",
            evidenceSourceSnapshotID: "local:run-stable"
        ),
        "a dashboard not selected by local evidence should retain the stable dashboard"
    )
    expect(
        !RadarPresenter.shouldUseCurrentDashboard(
            runID: "run-quick",
            evidenceSourceSnapshotID: nil
        ),
        "missing evidence identity should retain the stable dashboard"
    )
}

@main
private enum RadarPresenterTestMain {
    static func main() {
        verifySourceAndPreferenceLabels()
        verifyDecisionPresentation()
        verifySessionSummary()
        verifyCanonicalLeaderboardProjection()
        verifySurfaceCopy()
        verifyExportAndReferenceCostPresentation()
        verifyEvidenceAvailabilityProjection()
        verifyQuestionAndDecisionProjection()
        verifyRadarDashboardSelection()
        if failureCount > 0 {
            exit(1)
        }
        print("Radar presenter tests passed")
    }
}
