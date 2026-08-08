import Foundation

private var failureCount = 0

private func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() {
        failureCount += 1
        fputs("FAIL: \(message)\n", stderr)
    }
}

private let now = Date(timeIntervalSince1970: 1_800_000_000)

private func recommendation(
    staleOffset: TimeInterval = 3_600,
    expiryOffset: TimeInterval = 7_200,
    missingFreshness: Bool = false,
    outcome: String = "recommend",
    currentDefaultCandidateId: String? = nil,
    recommendedCandidateId: String = "candidate-recommended",
    currentUsageStatus: String = "recent",
    activeSessionCount: Int = 0,
    evidenceState: String = "fresh",
    runStatus: String = "completed"
) -> RecommendationSnapshot {
    RecommendationSnapshot(
        fullDisplayName: "gpt-5.6-sol",
        shortDisplayName: "5.6 Sol",
        effortLabel: "high",
        recommendationOutcome: outcome,
        currentDefaultCandidateId: currentDefaultCandidateId,
        recommendedCandidateId: recommendedCandidateId,
        currentUsageStatus: currentUsageStatus,
        activeSessionCount: activeSessionCount,
        evidenceState: evidenceState,
        runStatus: runStatus,
        scoreText: "90/100",
        recommendationCreatedAt: missingFreshness ? nil : now.addingTimeInterval(-60),
        runCompletedAt: now.addingTimeInterval(-60),
        staleAt: missingFreshness ? nil : now.addingTimeInterval(staleOffset),
        expiresAt: missingFreshness ? nil : now.addingTimeInterval(expiryOffset)
    )
}

private func runtime(
    lifecycle: RuntimeLifecycleState,
    phase: GlancePhase? = nil,
    completed: Int = 0,
    total: Int? = nil,
    lastPhase: GlancePhase? = nil,
    lastCompleted: Int = 0,
    lastTotal: Int? = nil,
    finalizingOffset: TimeInterval? = nil,
    leaseOffset: TimeInterval? = 60,
    recoverable: Bool = false,
    failure: String? = nil,
    currentTarget: String? = nil,
    activeEvaluationCount: Int = 0,
    oldestActiveOffset: TimeInterval? = nil,
    executionTimeoutSeconds: Int? = nil
) -> RuntimeSnapshot {
    RuntimeSnapshot(
        lifecycleState: lifecycle,
        phase: phase,
        progressCompleted: completed,
        progressTotal: total,
        lastPhase: lastPhase,
        lastPhaseCompleted: lastCompleted,
        lastPhaseTotal: lastTotal,
        stateChangedAt: now.addingTimeInterval(-10),
        finalizingStartedAt: finalizingOffset.map { now.addingTimeInterval($0) },
        updatedAt: now.addingTimeInterval(-1),
        leaseExpiresAt: leaseOffset.map { now.addingTimeInterval($0) },
        isRecoverable: recoverable,
        failureCategory: failure,
        currentTargetShortName: currentTarget,
        activeEvaluationCount: activeEvaluationCount,
        oldestActiveEvaluationStartedAt: oldestActiveOffset.map { now.addingTimeInterval($0) },
        executionTimeoutSeconds: executionTimeoutSeconds
    )
}

private func resolve(
    _ runtime: RuntimeSnapshotState,
    recommendation: RecommendationSnapshot? = nil,
    recommendationStatus: String? = nil,
    hasOfficialReferenceResults: Bool = false
) -> GlancePresentation {
    GlanceStateResolver.resolve(
        runtime: runtime,
        recommendation: recommendation,
        recommendationStatus: recommendationStatus,
        hasOfficialReferenceResults: hasOfficialReferenceResults,
        now: now
    )
}

private func verifyStateTable() {
    let fresh = recommendation()
    let stale = recommendation(staleOffset: -1, expiryOffset: 3_600)
    let expired = recommendation(staleOffset: -3_600, expiryOffset: -1)

    let backendWithout = resolve(.unavailable(lastError: "offline"))
    expect(backendWithout.state == .backendUnavailableWithoutCache, "backend without cache")
    expect(backendWithout.compactLeft == "数据异常", "backend error compact text")
    expect(backendWithout.destination == .connectionDiagnostics, "backend destination")

    let backendWith = resolve(.unavailable(lastError: "offline"), recommendation: fresh)
    expect(backendWith.state == .backendUnavailableWithCache, "backend with usable cache")
    expect(backendWith.compactLeft == "5.6 Sol" && backendWith.compactRight == "high", "cached recommendation compact")
    expect(backendWith.tone == .warning, "cached recommendation warning")
    expect(backendWith.compactLeadingSymbol == "exclamationmark.triangle.fill", "cached recommendation has warning marker")
    expect(backendWith.compactLeftTextRole == .identityPrimary, "cached recommendation keeps model identity neutral")
    expect(backendWith.compactRightTextRole == .identitySecondary, "cached recommendation keeps effort identity neutral")

    let activeScan = resolve(.available(runtime(lifecycle: .activeScan, phase: .scan, completed: 54, total: 60, currentTarget: "5.6 Terra · high")))
    expect(activeScan.state == .activeScan, "active scan")
    expect(activeScan.compactLeft == "扫描中" && activeScan.compactRight == "54/60", "scan status and progress")
    expect(activeScan.compactLeftTextRole == .status, "scan label uses active status color")
    expect(activeScan.compactRightTextRole == .status, "scan progress uses active status color")
    expect(activeScan.peekLeftSecondary == "正在测试 5.6 Terra · high", "active target peek")

    let finalSlowEvaluation = resolve(.available(runtime(
        lifecycle: .activeScan,
        phase: .scan,
        completed: 74,
        total: 75,
        activeEvaluationCount: 1,
        oldestActiveOffset: -660,
        executionTimeoutSeconds: 1_200
    )))
    expect(
        finalSlowEvaluation.peekLeftSecondary == "最后 1 项仍在运行 · 最慢已 11 分钟 · 距超时 9 分钟",
        "final slow evaluation exposes elapsed and timeout timing"
    )

    let nonFinalSlowEvaluation = resolve(.available(runtime(
        lifecycle: .activeScan,
        phase: .scan,
        completed: 60,
        total: 75,
        currentTarget: "5.6 Sol · max",
        activeEvaluationCount: 1,
        oldestActiveOffset: -660,
        executionTimeoutSeconds: 1_200
    )))
    expect(
        nonFinalSlowEvaluation.peekLeftSecondary == "正在测试 5.6 Sol · max",
        "timing stays hidden before the final active wave"
    )

    let geminiScan = resolve(.available(runtime(
        lifecycle: .activeScan,
        phase: .scan,
        completed: 3,
        total: 5,
        currentTarget: "gemini-3.6-flash / high · 扫描 3/5"
    )))
    expect(geminiScan.compactLeft == "扫描中", "concurrent scan never promotes one model into compact status")
    expect(geminiScan.peekLeftSecondary == "正在测试 gemini-3.6-flash / high · 扫描 3/5", "gemini scan keeps normalized effort in peek")
    expect(!geminiScan.peekLeftSecondary!.contains("default"), "gemini scan never exposes raw default profile")

    let stopping = GlanceStateResolver.stoppingPresentation(
        runtime: .available(runtime(lifecycle: .activeScan, phase: .scan, completed: 17, total: 75))
    )
    expect(stopping.state == .stopping, "stopping state")
    expect(stopping.compactLeft == "停止中" && stopping.compactRight == "17/75", "stopping keeps pre-stop progress")
    expect(stopping.activity == .finalizing && stopping.tone == .warning, "stopping has distinct activity and tone")

    let pausing = GlanceStateResolver.pausingPresentation(
        runtime: .available(runtime(lifecycle: .activeScan, phase: .scan, completed: 18, total: 75))
    )
    expect(pausing.state == .pausing, "pausing state")
    expect(pausing.compactLeft == "暂停中" && pausing.compactRight == "18/75", "pausing keeps pre-pause progress")
    expect(pausing.activity == .finalizing && pausing.tone == .warning, "pausing has distinct activity and tone")

    let activeScanWithRecommendation = resolve(
        .available(runtime(lifecycle: .activeScan, phase: .scan, completed: 54, total: 60)),
        recommendation: fresh
    )
    expect(activeScanWithRecommendation.peekLeftSecondary == "上次推荐 5.6 Sol high", "active scan preserves recommendation in peek only")

    let preparing = resolve(.available(runtime(lifecycle: .preparing)))
    expect(preparing.state == .preparing && preparing.compactLeft == "准备", "preparing")

    let paused = resolve(.available(runtime(lifecycle: .pausedRecoverable, phase: .scan, completed: 12, total: 24, recoverable: true)))
    expect(paused.state == .pausedOrRecoverable, "paused recoverable")
    expect(paused.compactLeft == "待继续" && paused.compactRight == "12/24", "paused compact")

    let pausedRepair = resolve(.available(runtime(lifecycle: .pausedRecoverable, phase: .repair, completed: 2, total: 5, recoverable: true)))
    expect(pausedRepair.peekRightLabel == "重试进度", "paused repair keeps repair progress semantics")

    let finalizing299 = GlanceStateResolver.resolve(
        runtime: .available(runtime(lifecycle: .finalizing, lastPhase: .scan, lastCompleted: 60, lastTotal: 60, finalizingOffset: -0.299)),
        recommendation: fresh,
        now: now
    )
    expect(finalizing299.state == .finalizing, "finalizing 299 state")
    expect(finalizing299.compactLeft == "扫描" && finalizing299.compactRight == "60/60", "finalizing 299 terminal count")

    let finalizing300 = GlanceStateResolver.resolve(
        runtime: .available(runtime(lifecycle: .finalizing, lastPhase: .scan, lastCompleted: 5, lastTotal: 5, finalizingOffset: -0.300)),
        recommendation: fresh,
        now: now
    )
    expect(finalizing300.compactLeft == "整理" && finalizing300.compactRight == "—", "finalizing 300 threshold")

    let finalizingWithoutLease = resolve(
        .available(runtime(
            lifecycle: .finalizing,
            lastPhase: .scan,
            lastCompleted: 5,
            lastTotal: 5,
            finalizingOffset: -1,
            leaseOffset: nil
        )),
        recommendation: fresh
    )
    expect(finalizingWithoutLease.state == .finalizing, "finalizing without lease remains finalizing")

    let repairFinalizing = GlanceStateResolver.resolve(
        runtime: .available(runtime(
            lifecycle: .finalizing,
            lastPhase: .repair,
            lastCompleted: 2,
            lastTotal: 3,
            finalizingOffset: -0.1
        )),
        recommendation: fresh,
        now: now
    )
    expect(repairFinalizing.compactLeft == "重试" && repairFinalizing.compactRight == "2/3", "repair finalizing preserves phase and terminal progress")

    let unavailable = resolve(.available(runtime(lifecycle: .recommendationUnavailable, failure: "insufficient evidence")), recommendation: fresh)
    expect(unavailable.state == .recommendationUnavailable && unavailable.compactLeft == "未决", "recommendation unavailable")

    let failedWithout = resolve(.available(runtime(lifecycle: .failed, failure: "timeout")))
    expect(failedWithout.state == .failedWithoutRecommendation && failedWithout.tone == .failure, "failed without fallback")

    let failedWith = resolve(.available(runtime(lifecycle: .failed, failure: "timeout")), recommendation: stale)
    expect(failedWith.state == .failedWithFallbackRecommendation, "failed with fallback")
    expect(failedWith.compactLeft == "5.6 Sol" && failedWith.compactRight == "high" && failedWith.tone == .warning, "fallback compact")
    expect(failedWith.compactLeadingSymbol == "exclamationmark.triangle.fill", "fallback compact has warning marker")

    let retainedAfterFailure = resolve(
        .available(runtime(lifecycle: .idle)),
        recommendation: recommendation(
            outcome: "retain_after_failure",
            evidenceState: "retained_after_failure",
            runStatus: "failed"
        )
    )
    expect(retainedAfterFailure.state == .failedWithFallbackRecommendation, "retained result remains a failure fallback after reload")
    expect(retainedAfterFailure.tone == .warning, "retained result uses warning tone")
    expect(retainedAfterFailure.compactLeadingSymbol == "exclamationmark.triangle.fill", "retained result has warning marker")

    let degraded = resolve(
        .available(runtime(lifecycle: .idle)),
        recommendation: recommendation(runStatus: "degraded")
    )
    expect(degraded.state == .degradedRecommendation, "degraded run remains distinct after reload")
    expect(degraded.compactRight == "high", "degraded compact keeps the recommended effort")
    expect(degraded.tone == .warning, "degraded recommendation uses warning tone")
    expect(degraded.compactLeadingSymbol == "exclamationmark.triangle.fill", "degraded recommendation has warning marker")

    let freshResult = resolve(.available(runtime(lifecycle: .idle)), recommendation: fresh)
    expect(freshResult.state == .freshRecommendation && freshResult.tone == .neutral, "fresh recommendation has no ambient success tint")
    expect(freshResult.destination == .overview, "fresh destination")
    expect(freshResult.compactLeadingSymbol == nil, "recommend without current model has no compact decision symbol")
    expect(freshResult.compactLeftTextRole == .identityPrimary, "fresh recommendation model is primary identity")
    expect(freshResult.compactRightTextRole == .identitySecondary, "fresh recommendation effort is secondary identity")
    expect(freshResult.peekLeftPrimary == "gpt-5.6-sol", "hover uses the model name without repeating effort")
    expect(freshResult.peekLeftSecondary == "综合总分 90/100", "hover uses the current overall score")

    let waitButAlreadyUsingBest = resolve(
        .available(runtime(lifecycle: .idle)),
        recommendation: recommendation(
            outcome: "wait",
            currentDefaultCandidateId: "candidate-recommended",
            recommendedCandidateId: "candidate-recommended"
        )
    )
    expect(waitButAlreadyUsingBest.compactLeadingSymbol == "checkmark.circle.fill", "wait with same current model restores compact keep marker")
    expect(waitButAlreadyUsingBest.compactLeadingSymbolTone == .active, "wait with same current model uses active marker tone")
    expect(waitButAlreadyUsingBest.tone == .warning, "wait with same current model keeps warning ambient tone")
    expect(waitButAlreadyUsingBest.compactLeftTextRole == .identityPrimary, "wait keeps model identity neutral")
    expect(waitButAlreadyUsingBest.compactRightTextRole == .identitySecondary, "wait keeps effort identity neutral")
    expect(waitButAlreadyUsingBest.accessibilityLabel.contains("当前在用模型与当前榜首一致"), "wait keep fallback accessibility stays explicit")

    let waitWithDifferentCurrentModel = resolve(
        .available(runtime(lifecycle: .idle)),
        recommendation: recommendation(
            outcome: "wait",
            currentDefaultCandidateId: "candidate-current",
            recommendedCandidateId: "candidate-recommended"
        )
    )
    expect(waitWithDifferentCurrentModel.compactLeadingSymbol == "arrow.right.circle.fill", "wait with a different current model keeps compact switch marker")
    expect(waitWithDifferentCurrentModel.compactLeadingSymbolTone == .warning, "wait switch marker uses warning tone")
    expect(waitWithDifferentCurrentModel.tone == .warning, "wait with different current model keeps warning ambient tone")
    expect(waitWithDifferentCurrentModel.accessibilityLabel.contains("当前在用模型与当前榜首不同"), "wait switch fallback accessibility stays explicit")

    let switchResult = resolve(
        .available(runtime(lifecycle: .idle)),
        recommendation: recommendation(
            outcome: "switch",
            currentDefaultCandidateId: "candidate-current"
        )
    )
    expect(switchResult.compactLeft == "5.6 Sol" && switchResult.compactRight == "high", "switch keeps the recommended model and effort together")
    expect(switchResult.peekLeftPrimary == "gpt-5.6-sol", "switch hover matches the compact recommended model")
    expect(switchResult.compactLeadingSymbol == "arrow.right.circle.fill", "switch uses compact forward symbol")
    expect(switchResult.compactLeadingSymbolTone == .warning, "switch symbol uses warning tone")
    expect(switchResult.tone == .neutral, "switch keeps fresh recommendation ambient tone neutral")
    expect(switchResult.accessibilityLabel.contains("建议切换到推荐模型"), "switch accessibility describes action")

    let keepResult = resolve(
        .available(runtime(lifecycle: .idle)),
        recommendation: recommendation(outcome: "keep")
    )
    expect(keepResult.compactLeft == "5.6 Sol" && keepResult.compactRight == "high", "keep keeps current model and recommended effort visible")
    expect(keepResult.compactLeadingSymbol == "checkmark.circle.fill", "keep uses compact confirmation symbol")
    expect(keepResult.compactLeadingSymbolTone == .active, "keep symbol uses active tone")
    expect(keepResult.tone == .neutral, "fresh keep uses neutral ambient tone")
    expect(keepResult.accessibilityLabel.contains("当前模型无需切换"), "keep accessibility describes no action")

    let adoptedResult = resolve(
        .available(runtime(lifecycle: .idle)),
        recommendation: recommendation(
            outcome: "adopted",
            currentDefaultCandidateId: "candidate-recommended",
            recommendedCandidateId: "candidate-recommended"
        )
    )
    expect(adoptedResult.compactLeadingSymbol == "checkmark.circle.fill", "adopted recommendation uses compact confirmation symbol")
    expect(adoptedResult.compactLeadingSymbolTone == .active, "adopted recommendation uses active symbol tone")
    expect(adoptedResult.accessibilityLabel.contains("已采用建议"), "adopted recommendation describes its stable lifecycle")

    let staleResult = resolve(.available(runtime(lifecycle: .idle)), recommendation: stale)
    expect(staleResult.state == .staleRecommendation && staleResult.tone == .warning, "stale recommendation")

    let expiredResult = resolve(.available(runtime(lifecycle: .idle)), recommendation: expired)
    expect(expiredResult.state == .expiredRecommendation && expiredResult.compactLeft == "推荐过期", "expired recommendation")
    expect(expiredResult.destination == .rescan, "expired destination")

    let never = resolve(.available(runtime(lifecycle: .idle)))
    expect(never.state == .neverScanned && never.compactLeft == "待扫描", "never scanned")

    let v2Stale = resolve(
        .available(runtime(lifecycle: .idle)),
        recommendationStatus: "stale"
    )
    expect(v2Stale.state == .expiredRecommendation, "v2 stale remains expired without a displayable recommendation")

    let noUsage = resolve(
        .available(runtime(lifecycle: .idle)),
        recommendationStatus: "no_usage"
    )
    expect(noUsage.state == .recommendationUnavailable, "v2 no usage is not treated as never scanned")
    expect(noUsage.peekLeftPrimary == "尚未识别当前使用模型", "v2 no usage copy")

    let needsTest = resolve(
        .available(runtime(lifecycle: .idle)),
        recommendationStatus: "needs_test"
    )
    expect(needsTest.state == .recommendationUnavailable, "v2 needs test is not treated as never scanned")
    expect(needsTest.peekLeftPrimary == "需要补充可比较实测", "v2 needs test copy")

    let remoteOnlyNeedsTest = resolve(
        .available(runtime(lifecycle: .idle)),
        recommendationStatus: "needs_test",
        hasOfficialReferenceResults: true
    )
    expect(remoteOnlyNeedsTest.compactLeft == "远端榜单", "remote evidence should remain useful without a local run")
    expect(remoteOnlyNeedsTest.peekLeftPrimary == "远端榜单可用", "remote-only copy should not require a local scan")
    expect(remoteOnlyNeedsTest.destination == .overview, "remote-only evidence should open the leaderboard")

    let v2StaleOverridesFresh = resolve(
        .available(runtime(lifecycle: .idle)),
        recommendation: fresh,
        recommendationStatus: "stale"
    )
    expect(v2StaleOverridesFresh.state == .expiredRecommendation, "v2 stale status overrides a displayable recommendation")

    let noUsageOverridesFresh = resolve(
        .available(runtime(lifecycle: .idle)),
        recommendation: fresh,
        recommendationStatus: "no_usage"
    )
    expect(noUsageOverridesFresh.state == .recommendationUnavailable, "v2 no usage status overrides a displayable recommendation")

    let needsTestOverridesFresh = resolve(
        .available(runtime(lifecycle: .idle)),
        recommendation: fresh,
        recommendationStatus: "needs_test"
    )
    expect(needsTestOverridesFresh.state == .recommendationUnavailable, "v2 needs test status overrides a displayable recommendation")
}

private func verifyMixedCurrentUsage() {
    let mixed = recommendation(
        outcome: "keep",
        currentDefaultCandidateId: "candidate-recommended",
        currentUsageStatus: "active_mixed",
        activeSessionCount: 2
    )
    let result = resolve(.available(runtime(lifecycle: .idle)), recommendation: mixed)
    expect(result.compactLeft == "5.6 Sol", "mixed usage preserves recommended model")
    expect(result.compactRight == "多会话", "mixed usage replaces false effort decision")
    expect(result.compactLeadingSymbol == nil, "mixed usage has no keep or switch symbol")
    expect(result.tone == .warning, "mixed usage uses warning tone")
    expect(result.compactLeftTextRole == .identityPrimary, "mixed usage preserves neutral model identity")
    expect(result.compactRightTextRole == .status, "mixed usage keeps its warning status visible")
}

private func verifyUnmappedCurrentUsage() {
    let unmapped = recommendation(
        outcome: "recommend",
        currentUsageStatus: "unmapped"
    )
    let result = resolve(.available(runtime(lifecycle: .idle)), recommendation: unmapped)
    expect(result.compactLeft == "5.6 Sol", "unmapped usage preserves recommended model")
    expect(result.compactRight == "high", "unmapped usage preserves the recommended effort")
    expect(result.compactLeadingSymbol == "exclamationmark.circle.fill", "unmapped usage has warning marker")
    expect(result.compactLeadingSymbolTone == .warning, "unmapped marker uses warning tone")
    expect(result.tone == .warning, "unmapped usage uses warning tone")
    expect(result.compactLeftTextRole == .identityPrimary, "unmapped usage preserves neutral model identity")
    expect(result.compactRightTextRole == .identitySecondary, "unmapped usage preserves neutral effort identity")
    expect(result.peekRightValue == "待比较", "unmapped usage explains comparison state")
    expect(result.accessibilityLabel.contains("当前在用档位尚未参与比较"), "unmapped accessibility is explicit")
}

private func verifyPriorityAndBoundaries() {
    let fresh = recommendation()
    let expired = recommendation(staleOffset: -3_600, expiryOffset: -1)

    let backendWins = resolve(
        .unavailable(lastError: "offline"),
        recommendation: fresh
    )
    expect(backendWins.state == .backendUnavailableWithCache, "backend unavailability wins")

    let expiredCacheDoesNotMaskBackend = resolve(
        .unavailable(lastError: "offline"),
        recommendation: expired
    )
    expect(expiredCacheDoesNotMaskBackend.state == .backendUnavailableWithoutCache, "expired recommendation is not a usable backend fallback")

    let expiredLease = resolve(
        .available(runtime(lifecycle: .activeScan, phase: .scan, completed: 8, total: 24, leaseOffset: -1, recoverable: true)),
        recommendation: fresh
    )
    expect(expiredLease.state == .activeScan, "backend lifecycle stays authoritative after lease expiry")

    let missingLease = resolve(
        .available(runtime(lifecycle: .activeScan, phase: .scan, completed: 8, total: 24, leaseOffset: nil)),
        recommendation: fresh
    )
    expect(missingLease.state == .activeScan, "missing lease does not invent a recoverable lifecycle")

    let backendFreshness = resolve(
        .available(runtime(lifecycle: .idle)),
        recommendation: recommendation(missingFreshness: true),
        recommendationStatus: "recommend"
    )
    expect(backendFreshness.state == .freshRecommendation, "backend recommendation status owns v2 freshness")

    let missingTotal = resolve(.available(runtime(lifecycle: .activeScan, phase: .scan, completed: 4, total: nil)))
    expect(missingTotal.compactRight == "4/—", "missing total is explicit")

    let unknownFreshness = resolve(.available(runtime(lifecycle: .idle)), recommendation: recommendation(missingFreshness: true))
    expect(unknownFreshness.state == .expiredRecommendation, "missing freshness expires")

    let overdueFinalEvaluation = resolve(.available(runtime(
        lifecycle: .activeScan,
        phase: .scan,
        completed: 74,
        total: 75,
        activeEvaluationCount: 1,
        oldestActiveOffset: -1_300,
        executionTimeoutSeconds: 1_200
    )))
    expect(
        overdueFinalEvaluation.peekLeftSecondary == "最后 1 项仍在收尾 · 最慢已达到 20 分钟 超时线",
        "final active wave reports the crossed timeout boundary"
    )

    let youngFinalEvaluation = resolve(.available(runtime(
        lifecycle: .activeScan,
        phase: .scan,
        completed: 74,
        total: 75,
        activeEvaluationCount: 1,
        oldestActiveOffset: -59,
        executionTimeoutSeconds: 1_200
    )))
    expect(youngFinalEvaluation.peekLeftSecondary == nil, "final active timing stays hidden before one minute")

    expect(resolve(.available(runtime(lifecycle: .activeScan, phase: .scan, completed: 1, total: 5)), recommendation: fresh).destination == .runProgress, "run destination")
    expect(resolve(.available(runtime(lifecycle: .failed, failure: "timeout"))).destination == .failureEvidence, "failure destination")
    expect(resolve(.available(runtime(lifecycle: .recommendationUnavailable))).destination == .recommendationIssue, "issue destination")

    let label = resolve(.available(runtime(lifecycle: .activeScan, phase: .scan, completed: 1, total: 5)), recommendation: fresh).accessibilityLabel
    expect(label.contains("扫描") && label.contains("1/5"), "accessibility includes state and progress")
}

@main
private enum GlanceStateResolverTestMain {
    static func main() {
verifyStateTable()
verifyMixedCurrentUsage()
        verifyUnmappedCurrentUsage()
        verifyPriorityAndBoundaries()
        if failureCount > 0 {
            exit(1)
        }
        print("GlanceStateResolver tests passed")
    }
}
