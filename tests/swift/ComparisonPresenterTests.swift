import Foundation

private var failureCount = 0

private func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() {
        failureCount += 1
        fputs("FAIL: \(message)\n", stderr)
    }
}

private let emptyTokens = ComparisonPresenter.TokenValues(
    input: nil,
    cachedInput: nil,
    cacheWriteInput: nil,
    output: nil,
    reasoning: nil
)

private func candidate(
    _ id: String,
    score: Double? = nil,
    elapsedSeconds: Double? = nil,
    referenceCostUsd: Double? = nil
) -> ComparisonPresenter.CandidateInput {
    ComparisonPresenter.CandidateInput(
        id: id,
        score: score,
        elapsedSeconds: elapsedSeconds,
        referenceCostUsd: referenceCostUsd
    )
}

private func comparisonInput(
    current: ComparisonPresenter.CandidateInput = candidate("current"),
    candidate candidateInput: ComparisonPresenter.CandidateInput = candidate("candidate"),
    isManualComparison: Bool = false,
    decision: ComparisonPresenter.DecisionEvidenceInput? = nil,
    pairs: [ComparisonPresenter.PairwiseInput] = [],
    displaySource: String? = nil,
    localTrendSeries: [ComparisonPresenter.LocalTrendSeriesInput] = [],
    officialTrendSeries: [ComparisonPresenter.OfficialTrendSeriesInput] = [],
    officialTokens: [ComparisonPresenter.OfficialTokenInput] = []
) -> ComparisonPresenter.ComparisonInput {
    ComparisonPresenter.ComparisonInput(
        current: current,
        candidate: candidateInput,
        isManualComparison: isManualComparison,
        decision: decision,
        pairwiseComparisons: pairs,
        displaySource: displaySource,
        localTrendSeries: localTrendSeries,
        officialTrendSeries: officialTrendSeries,
        officialTokens: officialTokens
    )
}

private func pairwiseInput(
    isComparable: Bool = true,
    warningQuestionIds: [String] = ["q2", "q4"]
) -> ComparisonPresenter.PairwiseInput {
    ComparisonPresenter.PairwiseInput(
        baselineCandidateId: "current",
        candidateId: "candidate",
        isComparable: isComparable,
        baselineQualityScore: 80,
        candidateQualityScore: 86,
        qualityDeltaPoints: 6,
        baselineElapsedSeconds: 20,
        candidateElapsedSeconds: 15,
        timeDeltaPercent: 25,
        baselineCostUsd: 0.4,
        candidateCostUsd: 0.2,
        costDeltaPercent: 50,
        baselineTokens: ComparisonPresenter.TokenValues(
            input: 100,
            cachedInput: 20,
            cacheWriteInput: nil,
            output: 30,
            reasoning: 40
        ),
        candidateTokens: ComparisonPresenter.TokenValues(
            input: 90,
            cachedInput: 25,
            cacheWriteInput: 5,
            output: 20,
            reasoning: 35
        ),
        warningQuestionIds: warningQuestionIds
    )
}

private func verifyMetricChanges() {
    let qualityGain = ComparisonPresenter.qualityChange(
        deltaPoints: 5,
        sameConfiguration: false
    )
    expect(qualityGain.text == "高 5 分", "quality gain should keep the existing copy")
    expect(qualityGain.emphasis == .positive, "quality gain should be positive")

    let qualityLoss = ComparisonPresenter.qualityChange(
        deltaPoints: -5,
        sameConfiguration: false
    )
    expect(qualityLoss.text == "少 5 分", "quality loss should keep the existing copy")
    expect(qualityLoss.emphasis == .warning, "quality loss should be a warning")

    let roundedTie = ComparisonPresenter.qualityChange(
        deltaPoints: 0.4,
        sameConfiguration: false
    )
    expect(roundedTie.text == "相同", "sub-point quality changes should keep the rounded tie copy")
    expect(roundedTie.emphasis == .tertiary, "rounded quality ties should stay neutral")

    let unavailableQuality = ComparisonPresenter.qualityChange(
        deltaPoints: nil,
        sameConfiguration: false
    )
    expect(unavailableQuality.text == "不可比较", "missing quality evidence should stay unavailable")
    expect(unavailableQuality.emphasis == .tertiary, "unavailable quality should stay muted")

    let sameMissingQuality = ComparisonPresenter.qualityChange(
        deltaPoints: nil,
        sameConfiguration: true
    )
    expect(sameMissingQuality.text == "相同", "same configuration should keep the existing missing-value copy")

    let faster = ComparisonPresenter.timeChange(
        deltaPercent: 25,
        sameConfiguration: false
    )
    expect(faster.text == "快 25%", "time reduction should keep the existing copy")
    expect(faster.emphasis == .positive, "time reduction should be positive")

    let slower = ComparisonPresenter.timeChange(
        deltaPercent: -25,
        sameConfiguration: false
    )
    expect(slower.text == "慢 25%", "time regression should keep the existing copy")
    expect(slower.emphasis == .warning, "time regression should be a warning")

    let cheaper = ComparisonPresenter.costChange(
        deltaPercent: 50,
        sameConfiguration: false
    )
    expect(cheaper.text == "省 50%", "cost reduction should keep the existing copy")
    expect(cheaper.emphasis == .positive, "cost reduction should be positive")

    let costlier = ComparisonPresenter.costChange(
        deltaPercent: -50,
        sameConfiguration: false
    )
    expect(costlier.text == "多 50%", "cost regression should keep the existing copy")
    expect(costlier.emphasis == .warning, "cost regression should be a warning")

    let equalTime = ComparisonPresenter.timeChange(
        deltaPercent: 0,
        sameConfiguration: true
    )
    expect(equalTime.text == "快 0%", "equal measured time should preserve the existing percentage copy")

    let sameMissingCost = ComparisonPresenter.costChange(
        deltaPercent: nil,
        sameConfiguration: true
    )
    expect(sameMissingCost.text == "相同", "same configuration without cost should keep the existing copy")
}

private func verifyQualityGuard() {
    let resolvedCurrent = ComparisonPresenter.qualityGuard(
        sameConfiguration: true,
        isManualComparison: false,
        pairwiseComparable: nil,
        status: "passed",
        rule: "minimum_gain",
        thresholdPoints: 2
    )
    expect(
        resolvedCurrent.text == "当前配置",
        "presenter should resolve same-configuration guard status"
    )

    let resolvedManualEvidence = ComparisonPresenter.qualityGuard(
        sameConfiguration: false,
        isManualComparison: true,
        pairwiseComparable: true,
        status: "passed",
        rule: nil,
        thresholdPoints: nil
    )
    expect(
        resolvedManualEvidence.text == "手动候选 · 仅作对比",
        "presenter should resolve comparable manual evidence"
    )

    let resolvedManualUnavailable = ComparisonPresenter.qualityGuard(
        sameConfiguration: false,
        isManualComparison: true,
        pairwiseComparable: false,
        status: "passed",
        rule: nil,
        thresholdPoints: nil
    )
    expect(
        resolvedManualUnavailable.text == "手动候选 · 不可比较",
        "presenter should resolve missing manual evidence"
    )

    let currentConfiguration = ComparisonPresenter.qualityGuard(
        status: "current_configuration",
        rule: nil,
        thresholdPoints: nil
    )
    expect(currentConfiguration.text == "当前配置", "same configuration without scores should be identified")
    expect(currentConfiguration.emphasis == .tertiary, "missing guard evidence should stay muted")

    let manual = ComparisonPresenter.qualityGuard(
        status: "manual_unavailable",
        rule: nil,
        thresholdPoints: nil
    )
    expect(manual.text == "手动候选 · 不可比较", "manual comparison should not invent missing evidence")
    expect(manual.emphasis == .secondary, "manual comparison should use secondary emphasis")

    let manualEvidence = ComparisonPresenter.qualityGuard(
        status: "manual_evidence",
        rule: nil,
        thresholdPoints: nil
    )
    expect(manualEvidence.text == "手动候选 · 仅作对比", "manual pairwise evidence should remain non-recommendational")

    let qualityPassed = ComparisonPresenter.qualityGuard(
        status: "passed",
        rule: "minimum_gain",
        thresholdPoints: 2
    )
    expect(qualityPassed.text == "质量门槛 2 分 · 已通过", "quality preference should keep its 2-point threshold")
    expect(qualityPassed.emphasis == .positive, "passed guard should be positive")

    let qualityFailed = ComparisonPresenter.qualityGuard(
        status: "failed",
        rule: "minimum_gain",
        thresholdPoints: 2
    )
    expect(qualityFailed.text == "质量门槛 2 分 · 未通过", "quality preference failure should keep its copy")

    let currentHighest = ComparisonPresenter.qualityGuard(
        status: "current_is_best",
        rule: "minimum_gain",
        thresholdPoints: 2
    )
    expect(currentHighest.text == "当前已是最高分", "non-improving quality candidate should keep the current-highest copy")

    let smartPassed = ComparisonPresenter.qualityGuard(
        status: "passed",
        rule: "maximum_loss",
        thresholdPoints: 5
    )
    expect(smartPassed.text == "质量护栏 5 分 · 已通过", "smart preference should keep its 5-point guard")

    let costPassed = ComparisonPresenter.qualityGuard(
        status: "passed",
        rule: "maximum_loss",
        thresholdPoints: 10
    )
    expect(costPassed.text == "质量护栏 10 分 · 已通过", "non-smart efficiency preferences should keep the 10-point guard")

    let unavailable = ComparisonPresenter.qualityGuard(
        status: "unavailable",
        rule: "maximum_loss",
        thresholdPoints: 5
    )
    expect(unavailable.text == "总分暂不可比", "missing guard evidence should keep the unavailable copy")
}

private func verifyRouteEvidenceText() {
    expect(
        ComparisonPresenter.routeEvidenceText(currentRoute: nil, candidateRoute: nil)
            == "未记录",
        "missing route evidence should stay explicit"
    )
    expect(
        ComparisonPresenter.routeEvidenceText(currentRoute: "route-a", candidateRoute: nil)
            == "部分未记录",
        "partial route evidence should stay explicit"
    )
    expect(
        ComparisonPresenter.routeEvidenceText(
            currentRoute: "route-1234567890-abcdefghijkl",
            candidateRoute: "route-1234567890-abcdefghijkl"
        ) == "route-12…ghijkl",
        "matching route evidence should keep the compact identifier"
    )
    expect(
        ComparisonPresenter.routeEvidenceText(currentRoute: "route-a", candidateRoute: "route-b")
            == "不同，见配置证据",
        "different route evidence should point to configuration details"
    )
}

private func verifyEvidenceAndTokenProjection() {
    let decision = ComparisonPresenter.DecisionEvidenceInput(
        currentCandidateId: "current",
        candidateCandidateId: nil,
        comparisonCandidateId: "candidate",
        currentScore: 10,
        candidateScore: 11,
        qualityDeltaPoints: 1,
        currentSeconds: 99,
        candidateSeconds: 98,
        timeDeltaPercent: 1,
        currentCostUsd: 9,
        candidateCostUsd: 8,
        costDeltaPercent: 1,
        warningQuestionIds: ["decision-warning"]
    )
    let pair = pairwiseInput()
    let paired = ComparisonPresenter.present(
        comparisonInput(decision: decision, pairs: [pair])
    )
    expect(paired.evidence?.currentScore == 80, "pairwise evidence should take precedence")
    expect(paired.evidence?.qualityDeltaPoints == 6, "pairwise quality delta should be preserved")
    expect(paired.evidence?.pairwiseComparable == true, "pair comparability should reach the view")
    expect(
        paired.questionRisk.warningQuestionIds == ["q2", "q4"],
        "pairwise warning ids should remain authoritative"
    )
    expect(paired.questionRisk.warningCount == 2, "warning count should be computed by the presenter")
    expect(paired.tokens.current?.input == 100, "local token values should use the matching pair")
    expect(paired.tokens.candidate?.cacheWriteInput == 5, "candidate token values should be preserved")
    expect(
        paired.tokens.evidenceNote == "来自后端权威对比投影；缺失字段保持不可用。",
        "local token provenance should be presenter-owned"
    )

    let decisionOnly = ComparisonPresenter.present(
        comparisonInput(decision: decision)
    )
    expect(decisionOnly.evidence?.currentScore == 10, "decision evidence should be the automatic fallback")
    expect(
        decisionOnly.questionRisk.warningQuestionIds == ["decision-warning"],
        "decision warning ids should survive the fallback"
    )
    expect(decisionOnly.tokens.current == nil, "local tokens should not fall back to decision metrics")

    let manualWithoutPair = ComparisonPresenter.present(
        comparisonInput(isManualComparison: true, decision: decision)
    )
    expect(manualWithoutPair.evidence == nil, "manual comparison should not borrow decision evidence")
    expect(manualWithoutPair.questionRisk.warningCount == 0, "missing evidence should have no warning count")

    let manualWithPair = ComparisonPresenter.present(
        comparisonInput(isManualComparison: true, pairs: [pair])
    )
    expect(manualWithPair.evidence?.candidateScore == 86, "manual comparison should use its matching pairwise evidence")
    expect(manualWithPair.evidence?.qualityDeltaPoints == 6, "manual comparison should preserve the pairwise quality delta")
    expect(manualWithPair.evidence?.pairwiseComparable == true, "manual comparison should preserve pairwise comparability")

    let same = candidate("current", score: 77, elapsedSeconds: 12, referenceCostUsd: 0.3)
    let sameConfiguration = ComparisonPresenter.present(
        comparisonInput(current: same, candidate: same, decision: decision)
    )
    expect(sameConfiguration.evidence?.currentScore == 77, "same configuration should use item evidence")
    expect(sameConfiguration.evidence?.qualityDeltaPoints == nil, "same configuration should not invent a delta")

    let officialCurrent = ComparisonPresenter.TokenValues(
        input: 1_000,
        cachedInput: 500,
        cacheWriteInput: nil,
        output: 100,
        reasoning: 200
    )
    let officialCandidate = ComparisonPresenter.TokenValues(
        input: 800,
        cachedInput: nil,
        cacheWriteInput: nil,
        output: 90,
        reasoning: 150
    )
    let official = ComparisonPresenter.present(
        comparisonInput(
            pairs: [pair],
            displaySource: "official_snapshot",
            officialTokens: [
                ComparisonPresenter.OfficialTokenInput(
                    candidateId: "current",
                    values: officialCurrent
                ),
                ComparisonPresenter.OfficialTokenInput(
                    candidateId: "candidate",
                    values: officialCandidate
                ),
            ]
        )
    )
    expect(official.tokens.current == officialCurrent, "official source should select publisher tokens")
    expect(official.tokens.candidate == officialCandidate, "official candidate tokens should stay source-isolated")
    expect(
        official.tokens.evidenceNote == "来自当前官网快照；只展示该来源发布的同题包成功调用汇总。",
        "official token provenance should be presenter-owned"
    )
}

private func verifyRealizedBenefitProjection() {
    let estimated = ComparisonPresenter.realizedBenefit(
        ComparisonPresenter.RealizedBenefitInput(
            status: "estimated",
            observedWorkUnitCount: 4,
            referenceCostWorkUnitCount: nil,
            modelWaitWorkUnitCount: 3,
            referenceCostDeltaUsd: -0.1234,
            modelWaitDeltaMs: -65_000
        ),
        isManualComparison: false
    )
    expect(estimated?.title == "调整模型后", "estimated benefit title should use plain language")
    expect(estimated?.statusIcon == "checkmark.circle.fill", "estimated benefit should use success status")
    expect(estimated?.emphasis == .positive, "estimated benefit should use positive emphasis")
    expect(estimated?.completedWorkText == "已完成 4 次任务", "work count should use plain language")
    expect(estimated?.modelWaitText == "累计约少 1 分 5 秒", "wait delta should use readable duration")
    expect(estimated?.referenceCostText == "累计约省 $0.12", "cost delta should avoid false precision")
    expect(estimated?.noteText == "根据本机使用记录估算，不代表实际账单或工作总时长。", "estimated benefit note should stay concise")
    expect(estimated?.helpText.contains("参考费用覆盖 4/4 次") == true, "delta fallback should retain cost coverage")
    expect(estimated?.helpText.contains("等待时间覆盖 3/4 次") == true, "explicit wait coverage should be retained")

    let unavailable = ComparisonPresenter.realizedBenefit(
        ComparisonPresenter.RealizedBenefitInput(
            status: "unavailable",
            observedWorkUnitCount: 2,
            referenceCostWorkUnitCount: nil,
            modelWaitWorkUnitCount: nil,
            referenceCostDeltaUsd: nil,
            modelWaitDeltaMs: nil
        ),
        isManualComparison: false
    )
    expect(unavailable?.title == "调整模型后", "unavailable estimates should keep the same plain-language context")
    expect(unavailable?.modelWaitText == "暂不可用", "missing wait delta should not be invented")
    expect(unavailable?.noteText.contains("记录还不够") == true, "unavailable benefit note should explain the missing conclusion")
    expect(unavailable?.helpText.contains("参考费用覆盖 0/2 次") == true, "missing deltas should report zero coverage")
    expect(
        ComparisonPresenter.realizedBenefit(
            ComparisonPresenter.RealizedBenefitInput(
                status: "estimated",
                observedWorkUnitCount: 1,
                referenceCostWorkUnitCount: 1,
                modelWaitWorkUnitCount: 1,
                referenceCostDeltaUsd: 0,
                modelWaitDeltaMs: 0
            ),
            isManualComparison: true
        ) == nil,
        "manual comparisons must not claim recommendation benefit"
    )
}

private func verifyRealUsageProjection() {
    let current = ComparisonPresenter.UsageCandidateInput(
        id: "current",
        displayName: "Current High",
        modelName: "gpt-current",
        effort: "high"
    )
    let candidate = ComparisonPresenter.UsageCandidateInput(
        id: "candidate",
        displayName: "Candidate High",
        modelName: "gpt-candidate",
        effort: "high"
    )
    let exact = ComparisonPresenter.UsageAggregateInput(
        modelConfigurationId: "current",
        rawModelId: "ignored-by-exact-id",
        reasoningEffort: "low",
        completedWorkUnits: 3,
        failureCount: 1,
        sampleDays: 2,
        attributionConfidence: 0.876,
        behaviorObservedWorkUnits: 2,
        behaviorCoveragePercent: 66.6,
        oneShotRatePercent: 50.4
    )
    let uniqueFallback = ComparisonPresenter.UsageAggregateInput(
        modelConfigurationId: "legacy-candidate-id",
        rawModelId: "GPT-CANDIDATE",
        reasoningEffort: "HIGH",
        completedWorkUnits: 5,
        failureCount: 0,
        sampleDays: 4,
        attributionConfidence: 0.91,
        behaviorObservedWorkUnits: nil,
        behaviorCoveragePercent: nil,
        oneShotRatePercent: nil
    )
    let presentation = ComparisonPresenter.realUsage(
        current: current,
        candidate: candidate,
        workload: ComparisonPresenter.UsageWorkloadInput(
            coverageStartedAtText: "2026-07-20 08:00",
            coverageComplete: true,
            aggregates: [exact, uniqueFallback]
        )
    )
    expect(presentation.emptyText == nil, "matched usage should not show an aggregate empty state")
    expect(presentation.rows.count == 2, "current and candidate usage should be projected")
    expect(presentation.rows[0].summaryText == "完成 3 · 失败 1 · 2 天样本", "usage summary should be formatted once")
    expect(presentation.rows[0].confidenceText == "日志身份置信度 88%", "attribution confidence should be rounded by the presenter")
    expect(
        presentation.rows[0].behaviorText == "行为覆盖 67% · one-shot 50% · 仅看该配置自身",
        "behavior evidence should be presenter-owned"
    )
    expect(presentation.rows[1].summaryText == "完成 5 · 失败 0 · 4 天样本", "unique model and effort fallback should match")
    expect(
        presentation.coverageText == "日志覆盖自 2026-07-20 08:00；它是行为信号，不证明任务成功。",
        "complete workload coverage should retain its boundary copy"
    )

    let ambiguous = ComparisonPresenter.realUsage(
        current: current,
        candidate: candidate,
        workload: ComparisonPresenter.UsageWorkloadInput(
            coverageStartedAtText: "未知",
            coverageComplete: false,
            aggregates: [uniqueFallback, uniqueFallback]
        )
    )
    expect(ambiguous.rows[1].emptyText == "尚无可归属的本地使用记录", "ambiguous fallback identities must fail closed")
    expect(
        ambiguous.coverageText == "日志仍在建立覆盖范围；当前样本可能不完整，也不证明任务成功。",
        "partial coverage should remain explicit"
    )

    let empty = ComparisonPresenter.realUsage(
        current: current,
        candidate: candidate,
        workload: nil
    )
    expect(empty.emptyText == "当前与候选均暂无可归属的本地使用记录", "missing workload should explain both rows")
    expect(
        empty.coverageText == "仅统计本机可读取的 Codex 日志；其他连接暂无真实工作观察。",
        "missing workload should preserve the local privacy boundary"
    )
}

private func verifyTrendFormatting() {
    let local = ComparisonPresenter.present(
        comparisonInput(
            localTrendSeries: [
                ComparisonPresenter.LocalTrendSeriesInput(
                    candidateId: "current",
                    runIndices: [7, 3, 0],
                    scores: [80, 75, 70]
                ),
                ComparisonPresenter.LocalTrendSeriesInput(
                    candidateId: "candidate",
                    runIndices: [7, 1],
                    scores: [78, 72]
                ),
            ]
        )
    ).trend
    expect(local.slots == [2, 3, 4, 5, 6, 7], "local trend should use the latest six slots")
    expect(
        local.current == [
            ComparisonPresenter.TrendPoint(slot: 3, score: 75),
            ComparisonPresenter.TrendPoint(slot: 7, score: 80),
        ],
        "local trend should map and order visible current points"
    )
    expect(
        local.candidate == [ComparisonPresenter.TrendPoint(slot: 7, score: 78)],
        "local trend should align candidate points to the shared slots"
    )
    expect(local.scale == ComparisonPresenter.TrendScale(lower: 70, upper: 80), "local scale should use visible scores")
    expect(local.hasGap, "missing local points should expose a discontinuity")

    let officialPoints = (1...7).map { index in
        ComparisonPresenter.OfficialTrendPointInput(
            batchId: "b\(index)",
            publishedAt: "2026-07-\(String(format: "%02d", index))T00:00:00Z",
            score: Double(68 + index * 2)
        )
    }.reversed()
    let official = ComparisonPresenter.present(
        comparisonInput(
            displaySource: "official_snapshot",
            officialTrendSeries: [
                ComparisonPresenter.OfficialTrendSeriesInput(
                    candidateId: "current",
                    points: Array(officialPoints)
                ),
                ComparisonPresenter.OfficialTrendSeriesInput(
                    candidateId: "candidate",
                    points: [
                        ComparisonPresenter.OfficialTrendPointInput(
                            batchId: "b3",
                            publishedAt: "2026-07-03T00:00:00Z",
                            score: 73.4
                        ),
                        ComparisonPresenter.OfficialTrendPointInput(
                            batchId: "b7",
                            publishedAt: "2026-07-07T00:00:00Z",
                            score: 83.6
                        ),
                    ]
                ),
            ]
        )
    ).trend
    expect(official.slots == [0, 1, 2, 3, 4, 5], "official trend should keep six sorted batches")
    expect(
        official.current.map(\.score) == [72, 74, 76, 78, 80, 82],
        "official trend should drop the oldest batch and preserve sorted scores"
    )
    expect(
        official.candidate == [
            ComparisonPresenter.TrendPoint(slot: 1, score: 73),
            ComparisonPresenter.TrendPoint(slot: 5, score: 84),
        ],
        "official trend should map publisher batches to shared slots"
    )
    expect(official.scale == ComparisonPresenter.TrendScale(lower: 70, upper: 85), "official scale should use mapped points")
    expect(official.hasGap, "missing official batches should expose a discontinuity")
}

private func verifyDecisionCopy() {
    let recommendation = ComparisonPresenter.decisionPresentation(
        decision: "recommend",
        isManualComparison: false
    )
    expect(recommendation.title == "建议切换", "recommend verdict copy should be projected")
    expect(recommendation.emphasis == .positive, "recommend verdict should use positive emphasis")
    expect(recommendation.automaticCandidatePrefix == "推荐", "recommended candidate prefix should be projected")

    let manual = ComparisonPresenter.decisionPresentation(
        decision: "recommend",
        isManualComparison: true
    )
    expect(manual.title == "手动对比", "manual comparison should override the backend verdict title")
    expect(manual.emphasis == .secondary, "manual comparison should use secondary emphasis")

    let questionRows = ComparisonPresenter.questionRows(
        questions: [
            ComparisonPresenter.QuestionInput(
                id: "Q1",
                shortLabel: "题1",
                capabilityLabel: "推理"
            ),
        ],
        currentScores: ["q1": 8.6],
        candidateScores: [:],
        warningQuestionIDs: ["Q1"]
    )
    expect(questionRows.first?.currentScoreText == "9", "question scores should match IDs case-insensitively")
    expect(questionRows.first?.candidateScoreText == nil, "missing question evidence should stay missing")
    expect(questionRows.first?.showsWarning == true, "question risk should be projected per row")

    expect(
        ComparisonPresenter.configurationDecisionText(
            decision: "recommend",
            target: "GPT-5.6 High",
            benefitKind: "time",
            reductionPercent: 25.4,
            gainPoints: nil
        ) == "建议 GPT-5.6 High · 快 25%",
        "configuration decision copy should format the backend primary benefit"
    )
    expect(
        ComparisonPresenter.configurationDecisionText(
            decision: "keep",
            target: "unused",
            benefitKind: nil,
            reductionPercent: nil,
            gainPoints: nil
        ) == "保持当前配置",
        "keep decisions should preserve the existing row copy"
    )

    expect(
        ComparisonPresenter.recommendationReasonText(
            status: "recommend",
            reason: "material_quality_gain",
            sourceResolutionReason: nil,
            selectedSourceMode: "auto",
            qualityTradeoff: false,
            scoreDelta: 3.6,
            timeReductionPercent: nil,
            costReductionPercent: nil
        ) == "总分提高 4 分，达到质量优先的推荐门槛。",
        "quality recommendation copy should consume the authoritative reason and delta"
    )
    expect(
        ComparisonPresenter.recommendationReasonText(
            status: "recommend",
            reason: "material_time_gain",
            sourceResolutionReason: nil,
            selectedSourceMode: "auto",
            qualityTradeoff: false,
            scoreDelta: nil,
            timeReductionPercent: 25.6,
            costReductionPercent: nil
        ) == "总耗时缩短 26%，且质量仍在允许范围内。",
        "time recommendation copy should consume the backend reduction"
    )
    expect(
        ComparisonPresenter.recommendationReasonText(
            status: "recommend",
            reason: "material_reference_cost_gain",
            sourceResolutionReason: nil,
            selectedSourceMode: "auto",
            qualityTradeoff: true,
            scoreDelta: -6,
            timeReductionPercent: nil,
            costReductionPercent: 30
        ) == "速度或费用收益明确，但存在质量下降，请确认取舍。",
        "quality tradeoff warning should take precedence over the benefit copy"
    )
    expect(
        ComparisonPresenter.recommendationReasonText(
            status: "needs_test",
            reason: "current_needs_test",
            sourceResolutionReason: "mixed_source_resolution_blocked",
            selectedSourceMode: "auto",
            qualityTradeoff: false,
            scoreDelta: nil,
            timeReductionPercent: nil,
            costReductionPercent: nil
        ) == "多个活动配置无法使用同一来源，当前不计算混合收益。",
        "source resolution failures should preserve the existing explanation"
    )
    expect(
        ComparisonPresenter.recommendationReasonText(
            status: "keep",
            reason: nil,
            sourceResolutionReason: nil,
            selectedSourceMode: "auto",
            qualityTradeoff: false,
            scoreDelta: nil,
            timeReductionPercent: nil,
            costReductionPercent: nil
        ) == "未找到同时满足当前质量与收益要求的候选。",
        "keep recommendations should preserve the existing explanation"
    )
}

@main
private enum ComparisonPresenterTestMain {
    static func main() {
        verifyMetricChanges()
        verifyQualityGuard()
        verifyRouteEvidenceText()
        verifyEvidenceAndTokenProjection()
        verifyRealizedBenefitProjection()
        verifyRealUsageProjection()
        verifyTrendFormatting()
        verifyDecisionCopy()
        if failureCount > 0 {
            exit(1)
        }
        print("ComparisonPresenter tests passed")
    }
}
