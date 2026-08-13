import Foundation

private var failureCount = 0

private func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() {
        failureCount += 1
        fputs("FAIL: \(message)\n", stderr)
    }
}

private func decision(
    qualityDelta: Double? = nil,
    timeReduction: Double? = nil,
    costReduction: Double? = nil
) -> BridgeRecommendationDecisionV2 {
    BridgeRecommendationDecisionV2(
        currentModelConfigurationId: "current",
        candidateModelConfigurationId: "candidate",
        comparisonCandidateModelConfigurationId: nil,
        comparisonCandidateReasons: nil,
        decision: "recommend",
        reason: "test",
        qualityTradeoff: false,
        qualityWarningQuestionIds: [],
        qualityGuard: nil,
        quality: BridgeRecommendationQualityV2(
            currentScore: nil,
            candidateScore: nil,
            scoreDelta: qualityDelta
        ),
        time: BridgeRecommendationTimeV2(
            currentSeconds: nil,
            candidateSeconds: nil,
            reductionPercent: timeReduction
        ),
        referenceCost: BridgeRecommendationCostV2(
            currentUsd: nil,
            candidateUsd: nil,
            reductionPercent: costReduction
        ),
        primaryBenefit: nil
    )
}

private func verifySettingsAdvisorReasons() {
    expect(
        SettingsAdvisorReasonPresenter.presentation(for: nil).text == "建议尚未形成",
        "missing advisor reason should stay unresolved"
    )
    expect(
        SettingsAdvisorReasonPresenter.presentation(
            for: "current_evaluation_incomplete"
        ).text == "当前配置缺少同版完整评测",
        "evaluation gate should preserve its user-facing copy"
    )
    expect(
        SettingsAdvisorReasonPresenter.presentation(
            for: "material_time_gain"
        ).text == "候选时间收益已达到建议门槛",
        "material time gain should preserve its user-facing copy"
    )
    expect(
        SettingsAdvisorReasonPresenter.presentation(for: "keep").text
            == "当前配置仍是更稳妥选择",
        "keep should preserve its user-facing copy"
    )
    expect(
        SettingsAdvisorReasonPresenter.presentation(for: "future_reason").text
            == "建议门禁未识别",
        "unknown advisor reasons should remain explicit"
    )
}

private func verifyDecisionMetrics() {
    expect(
        IslandDecisionMetricPresentation.quality(decision()) == "未知",
        "missing quality should remain unknown"
    )
    expect(
        IslandDecisionMetricPresentation.quality(decision(qualityDelta: 2.4)) == "+2 分",
        "quality gain should preserve signed rounded points"
    )
    expect(
        IslandDecisionMetricPresentation.quality(decision(qualityDelta: 0.01)) == "持平",
        "near-zero quality should remain flat"
    )
    expect(
        IslandDecisionMetricPresentation.time(decision(timeReduction: 24.6)) == "快 25%",
        "time gain should preserve improvement copy"
    )
    expect(
        IslandDecisionMetricPresentation.time(decision(timeReduction: -12.4)) == "慢 12%",
        "time regression should preserve regression copy"
    )
    expect(
        IslandDecisionMetricPresentation.referenceCost(decision(), isPartial: true)
            == "部分未知",
        "partial missing cost should remain explicit"
    )
    expect(
        IslandDecisionMetricPresentation.referenceCost(decision(), isPartial: false)
            == "不可比较",
        "fully missing cost should remain incomparable"
    )
    expect(
        IslandDecisionMetricPresentation.referenceCost(
            decision(costReduction: 33.6),
            isPartial: false
        ) == "省 34%",
        "cost gain should preserve improvement copy"
    )
}

private func verifyDurationFormattingGuard() {
    expect(
        checkedRoundedDurationSeconds(12.4) == 12,
        "normal duration should round to seconds"
    )
    expect(
        checkedRoundedDurationSeconds(Double.greatestFiniteMagnitude) == nil,
        "oversized duration should not trap during Int conversion"
    )
    expect(
        checkedRoundedDurationSeconds(Double.nan) == nil
            && checkedRoundedDurationSeconds(-1) == nil,
        "non-finite and negative durations should remain unavailable"
    )
}

private func verifyCompactRecommendationPresentation() throws {
    let payload: [String: Any] = [
        "schema_version": 2,
        "source_mode": "auto",
        "source_mode_by_configuration_id": ["current": "auto"],
        "resolved_data_source": "official_snapshot",
        "source_resolution_reason": "fixture",
        "preference": "smart",
        "representative_configuration_id": "current",
        "representative_reason": "fixture",
        "status": "recommend",
        "decisions": [[
            "current_model_configuration_id": "current",
            "candidate_model_configuration_id": "candidate",
            "comparison_candidate_model_configuration_id": "candidate",
            "decision": "recommend",
            "reason": "fixture",
            "quality_tradeoff": false,
            "quality_warning_question_ids": [],
            "quality": ["score_delta": 2.4],
            "time": ["reduction_percent": 24.6],
            "reference_cost": ["reduction_percent": 33.6],
        ]],
        "testable_candidate_ids": [],
        "unmapped_active_session_count": 0,
    ]
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase
    let portfolio = try decoder.decode(
        BridgeRecommendationPortfolioV2.self,
        from: JSONSerialization.data(withJSONObject: payload)
    )
    let presentation = CompactSessionPresenter.recommendation(
        snapshot: nil,
        dashboard: nil,
        portfolio: portfolio,
        displaySource: "official_snapshot",
        displayFreshness: "delayed",
        leaderboardItems: [
            RadarLeaderboardItem(
                id: "candidate",
                displayName: "GPT-5.6 Sol High",
                modelName: "gpt-5.6-sol",
                providerId: "openai",
                effort: "high",
                score: 90,
                maxScore: 100,
                elapsedSeconds: 10,
                referenceCostUsd: 0.1,
                costCoverage: "complete",
                questionScores: [:],
                isCurrent: false,
                isRecommended: true
            ),
        ]
    )

    expect(presentation.contextLabel == "切换建议", "compact context should consume the backend decision")
    expect(presentation.title == "GPT-5.6 Sol High", "compact title should consume the resolved target")
    expect(presentation.tone == .recommendation, "compact tone should consume the backend decision")
    expect(
        presentation.comparisonState == .metrics(
            CompactRecommendationMetrics(
                quality: "+2 分",
                time: "快 25%",
                referenceCost: "省 34%"
            )
        ),
        "recommendation should expose its three comparison metrics"
    )
    expect(presentation.metrics?.quality == "+2 分", "compact quality belongs to the presenter")
    expect(presentation.metrics?.time == "快 25%", "compact time belongs to the presenter")
    expect(presentation.metrics?.referenceCost == "省 34%", "compact cost belongs to the presenter")
    expect(presentation.basisText == "数据待补齐 · 同题包完整结果", "untrusted official source must fail closed in compact basis")
    expect(presentation.freshnessText.isEmpty, "untrusted official source must not expose freshness")

    var adoptedPayload = payload
    adoptedPayload["status"] = "keep"
    adoptedPayload["recommendation_lifecycle"] = [
        "schema_version": 1,
        "status": "adopted",
        "trigger": "recommendation_accepted",
        "anchor_configuration_id": "baseline",
        "adopted_configuration_id": "current",
    ]
    adoptedPayload["decisions"] = [[
        "current_model_configuration_id": "current",
        "candidate_model_configuration_id": NSNull(),
        "comparison_candidate_model_configuration_id": "candidate",
        "decision": "keep",
        "reason": "recommendation_adopted",
        "quality_tradeoff": false,
        "quality_warning_question_ids": [],
        "quality": ["score_delta": 0.0],
        "time": [:],
        "reference_cost": [:],
    ]]
    let adoptedPortfolio = try decoder.decode(
        BridgeRecommendationPortfolioV2.self,
        from: JSONSerialization.data(withJSONObject: adoptedPayload)
    )
    let adoptedPresentation = CompactSessionPresenter.recommendation(
        snapshot: nil,
        dashboard: nil,
        portfolio: adoptedPortfolio,
        displaySource: "official_snapshot",
        displayFreshness: nil,
        leaderboardItems: [
            RadarLeaderboardItem(
                id: "current",
                displayName: "GPT-5.6 Terra High",
                modelName: "gpt-5.6-terra",
                providerId: "openai",
                effort: "high",
                score: 90,
                maxScore: 100,
                elapsedSeconds: 10,
                referenceCostUsd: 0.1,
                costCoverage: "complete",
                questionScores: [:],
                isCurrent: true,
                isRecommended: true
            ),
            RadarLeaderboardItem(
                id: "candidate",
                displayName: "GPT-5.6 Sol High",
                modelName: "gpt-5.6-sol",
                providerId: "openai",
                effort: "high",
                score: 88,
                maxScore: 100,
                elapsedSeconds: 8,
                referenceCostUsd: 0.08,
                costCoverage: "complete",
                questionScores: [:],
                isCurrent: false,
                isRecommended: false
            ),
        ]
    )
    expect(adoptedPresentation.contextLabel == "已采用建议", "adopted recommendation should name its lifecycle")
    expect(adoptedPresentation.title == "GPT-5.6 Terra High", "adopted presentation should keep the adopted configuration, not a next candidate")
    expect(adoptedPresentation.tone == .recommendation, "adopted presentation should remain affirmative")
    expect(adoptedPresentation.comparisonState == .suppressed, "adopted recommendation should suppress comparison")
    expect(adoptedPresentation.metrics == nil, "adopted presentation should not imply a further comparison")

    var keepPayload = adoptedPayload
    keepPayload.removeValue(forKey: "recommendation_lifecycle")
    let keepPortfolio = try decoder.decode(
        BridgeRecommendationPortfolioV2.self,
        from: JSONSerialization.data(withJSONObject: keepPayload)
    )
    let keepPresentation = CompactSessionPresenter.recommendation(
        snapshot: nil,
        dashboard: nil,
        portfolio: keepPortfolio,
        displaySource: "official_snapshot",
        displayFreshness: nil,
        leaderboardItems: [
            RadarLeaderboardItem(
                id: "current",
                displayName: "GPT-5.6 Terra High",
                modelName: "gpt-5.6-terra",
                providerId: "openai",
                effort: "high",
                score: 90,
                maxScore: 100,
                elapsedSeconds: 10,
                referenceCostUsd: 0.1,
                costCoverage: "complete",
                questionScores: [:],
                isCurrent: true,
                isRecommended: true
            ),
        ]
    )
    expect(keepPresentation.contextLabel == "当前无需切换", "keep should describe the current configuration instead of a comparison candidate")
    expect(keepPresentation.title == "GPT-5.6 Terra High", "keep presentation should match the capsule identity")
    expect(keepPresentation.comparisonState == .suppressed, "keep should suppress comparison")
    expect(keepPresentation.metrics == nil, "keep presentation should not imply a hidden next-step recommendation")

    var needsTestPayload = keepPayload
    needsTestPayload["status"] = "needs_test"
    needsTestPayload["decisions"] = [[
        "current_model_configuration_id": "current",
        "candidate_model_configuration_id": NSNull(),
        "comparison_candidate_model_configuration_id": NSNull(),
        "decision": "needs_test",
        "reason": "current_needs_test",
        "quality_tradeoff": false,
        "quality_warning_question_ids": [],
        "quality": [:],
        "time": [:],
        "reference_cost": [:],
    ]]
    let needsTestPortfolio = try decoder.decode(
        BridgeRecommendationPortfolioV2.self,
        from: JSONSerialization.data(withJSONObject: needsTestPayload)
    )
    let needsTestPresentation = CompactSessionPresenter.recommendation(
        snapshot: nil,
        dashboard: nil,
        portfolio: needsTestPortfolio,
        displaySource: "local_evaluation",
        displayFreshness: nil,
        leaderboardItems: [
            RadarLeaderboardItem(
                id: "current",
                displayName: "GPT-5.6 Terra High",
                modelName: "gpt-5.6-terra",
                providerId: "openai",
                effort: "high",
                score: 90,
                maxScore: 100,
                elapsedSeconds: 10,
                referenceCostUsd: 0.1,
                costCoverage: "complete",
                questionScores: [:],
                isCurrent: true,
                isRecommended: false
            ),
        ]
    )
    expect(
        needsTestPresentation.contextLabel == "等待比较证据",
        "needs-test decisions must not claim that no switch is needed"
    )
    expect(
        needsTestPresentation.tone == .comparison,
        "needs-test decisions should remain pending instead of affirmative"
    )
    expect(
        needsTestPresentation.comparisonState == .pending,
        "needs-test decisions should wait for comparable evidence"
    )

    var stalePayload = needsTestPayload
    stalePayload["status"] = "stale"
    var staleDecisions = stalePayload["decisions"] as! [[String: Any]]
    staleDecisions[0]["decision"] = "stale"
    stalePayload["decisions"] = staleDecisions
    let stalePortfolio = try decoder.decode(
        BridgeRecommendationPortfolioV2.self,
        from: JSONSerialization.data(withJSONObject: stalePayload)
    )
    let stalePresentation = CompactSessionPresenter.recommendation(
        snapshot: nil,
        dashboard: nil,
        portfolio: stalePortfolio,
        displaySource: "local_evaluation",
        displayFreshness: nil,
        leaderboardItems: []
    )
    expect(stalePresentation.contextLabel == "结果已过期", "stale evidence should be explicit")
    expect(stalePresentation.tone == .unavailable, "stale evidence should not use recommendation tone")
    expect(stalePresentation.comparisonState == .pending, "stale evidence should wait for a rescan")

    var pendingPayload = payload
    pendingPayload["status"] = "needs_test"
    pendingPayload["decisions"] = []
    pendingPayload.removeValue(forKey: "representative_configuration_id")
    let pendingPortfolio = try decoder.decode(
        BridgeRecommendationPortfolioV2.self,
        from: JSONSerialization.data(withJSONObject: pendingPayload)
    )
    let pendingPresentation = CompactSessionPresenter.recommendation(
        snapshot: nil,
        dashboard: nil,
        portfolio: pendingPortfolio,
        displaySource: "official_snapshot",
        displayFreshness: nil,
        leaderboardItems: []
    )
    expect(pendingPresentation.comparisonState == .pending, "missing decision should wait for same-round comparable evidence")
    expect(pendingPresentation.metrics == nil, "pending comparison should not expose metrics")

    let remoteOnlyPresentation = CompactSessionPresenter.recommendation(
        snapshot: nil,
        dashboard: nil,
        portfolio: nil,
        displaySource: "official_snapshot",
        displayFreshness: nil,
        leaderboardItems: [],
        remoteOnlyDisplayName: "GPT-5.6 Sol High"
    )
    expect(
        remoteOnlyPresentation.contextLabel == "官网综合推荐"
            && remoteOnlyPresentation.title == "GPT-5.6 Sol High",
        "remote-only compact presentation should use the official recommendation identity"
    )
    expect(
        remoteOnlyPresentation.basisText == "官方榜单 · 暂无本地对比"
            && remoteOnlyPresentation.comparisonState == .pending,
        "remote-only compact presentation must avoid actionable local comparison copy"
    )
}

@main
private enum ViewPresenterTestMain {
    static func main() throws {
        verifySettingsAdvisorReasons()
        verifyDecisionMetrics()
        verifyDurationFormattingGuard()
        try verifyCompactRecommendationPresentation()
        if failureCount > 0 {
            exit(1)
        }
        print("View presenter tests passed")
    }
}
