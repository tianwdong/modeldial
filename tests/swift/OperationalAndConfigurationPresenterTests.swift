import Foundation

private var failureCount = 0

private func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() {
        failureCount += 1
        fputs("FAIL: \(message)\n", stderr)
    }
}

private let advisor = OperationalStatePresenter.AdvisorInput(
    decision: "compare_first",
    currentCandidateID: "current",
    candidateID: nil,
    confidenceLevel: "high",
    nextAction: "先补齐候选证据"
)

private func localDate(
    year: Int,
    month: Int,
    day: Int,
    hour: Int,
    minute: Int
) -> Date {
    var calendar = Calendar(identifier: .gregorian)
    calendar.timeZone = .autoupdatingCurrent
    return calendar.date(
        from: DateComponents(
            timeZone: .autoupdatingCurrent,
            year: year,
            month: month,
            day: day,
            hour: hour,
            minute: minute
        )
    )!
}

private func isoTimestamp(_ date: Date) -> String {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime]
    formatter.timeZone = .autoupdatingCurrent
    return formatter.string(from: date)
}

private func availability(
    state: GlanceState = .freshRecommendation,
    canDisplayScores: [Bool] = [true],
    hasResumableRun: Bool = false,
    runStatus: String = "completed",
    advisorInput: OperationalStatePresenter.AdvisorInput? = advisor
) -> OperationalStatePresenter.Availability {
    OperationalStatePresenter.availability(
        OperationalStatePresenter.AvailabilityInput(
            state: state,
            hasEntries: true,
            canDisplayCurrentQuestionScores: canDisplayScores,
            hasResumableRun: hasResumableRun,
            requiresModelSetup: false,
            isProvisionalResult: false,
            hasBestCombination: true,
            bestEvidenceState: "fresh",
            runStatus: runStatus,
            advisor: advisorInput
        )
    )
}

private func presentationInput(
    availability: OperationalStatePresenter.Availability = availability(),
    state: GlanceState = .freshRecommendation,
    glanceTone: GlanceTone = .success,
    runStatus: String = "completed",
    hasRefreshIssue: Bool = false,
    runtimeIsRunning: Bool = false,
    requiresModelSetup: Bool = false,
    advisorInput: OperationalStatePresenter.AdvisorInput? = advisor,
    advisorScore: Int? = 88,
    radarTitle: String? = nil,
    radarReason: String? = nil,
    radarDisplaySource: String = "local_evaluation",
    radarReferenceFreshness: String? = nil,
    radarReferenceAgeHours: Int? = nil,
    referenceDeliveryRefreshStatus: String? = nil,
    referenceDeliverySource: String? = nil,
    referencePublishedAt: String? = nil,
    now: Date = localDate(year: 2026, month: 7, day: 29, hour: 18, minute: 0),
    localCompletedAt: String? = isoTimestamp(
        localDate(year: 2026, month: 7, day: 29, hour: 12, minute: 30)
    )
) -> OperationalStatePresenter.PresentationInput {
    OperationalStatePresenter.PresentationInput(
        availability: availability,
        state: state,
        glanceTone: glanceTone,
        runStatus: runStatus,
        hasSnapshotRefreshIssue: hasRefreshIssue,
        snapshotRefreshMessage: hasRefreshIssue ? "刷新失败" : nil,
        snapshotRefreshDetail: hasRefreshIssue ? "连接中断" : nil,
        runtimeIsRunning: runtimeIsRunning,
        runtimeLastError: "运行失败",
        runtimeProgressText: "3/5",
        activeEvaluationTimingText: "已运行 10 秒",
        hasResumableRun: false,
        entryDestination: .overview,
        glanceDestination: .overview,
        glancePeekLeftSecondary: nil,
        requiresModelSetup: requiresModelSetup,
        hasConfiguredModelCandidates: true,
        radarDisplaySource: radarDisplaySource,
        radarReferenceFreshness: radarReferenceFreshness,
        radarReferenceAgeHours: radarReferenceAgeHours,
        referenceDeliveryRefreshStatus: referenceDeliveryRefreshStatus,
        referenceDeliverySource: referenceDeliverySource,
        referencePublishedAt: referencePublishedAt,
        localCompletedAt: localCompletedAt,
        now: now,
        hasRadarPortfolio: false,
        radarPortfolioStatus: nil,
        best: OperationalStatePresenter.BestInput(
            displayLabel: "GPT-5.6 High",
            evidenceState: "fresh",
            recommendationOutcome: "keep",
            decisionReason: "证据不足",
            overallScore: 86,
            overallScoreText: "86/100",
            scoreText: "86",
            confidenceLabel: "中"
        ),
        provisional: nil,
        advisor: advisorInput,
        advisorDisplayLabel: advisorInput == nil ? nil : "GPT-5.5 High",
        advisorOverallScore: advisorInput == nil ? nil : advisorScore,
        radarTitle: radarTitle,
        radarReason: radarReason,
        fallbackTitle: "保持当前模型",
        fallbackReason: "当前模型仍是本轮最优。",
        isUnmappedCurrentModel: false,
        detectedCurrentModelIdentity: nil,
        completeQuestionSetLabel: "完整 5 题",
        questionRoundLabel: "5 题"
    )
}

private func verifyOperationalAvailability() {
    let running = availability(state: .activeScan, canDisplayScores: [false])
    expect(running.isEvidenceUpdating, "active scan should be an updating state")
    expect(running.shouldHideLegacyOverviewScores, "missing current scores should hide legacy evidence")
    expect(!running.isAwaitingCurrentScoreScan, "active work is not an idle wait state")
    expect(!running.showsPersonalAdvisor, "advisor must be hidden while scanning")

    let awaiting = availability(canDisplayScores: [false])
    expect(awaiting.isAwaitingCurrentScoreScan, "idle legacy evidence should await a current scan")
    expect(!awaiting.showsPersonalAdvisor, "advisor must not use legacy pack evidence")

    let fresh = availability()
    expect(fresh.showsPersonalAdvisor, "fresh complete evidence should expose the advisor")
    expect(fresh.advisorTargetCandidateID == "current", "compare-first should fall back to current candidate")

    let degraded = availability(runStatus: "degraded")
    expect(!degraded.showsPersonalAdvisor, "degraded runs must suppress advisor copy")
}

private func verifyCurrentModelPresentation() {
    let manual = OperationalStatePresenter.currentModel(
        OperationalStatePresenter.CurrentModelInput(
            hasRecommendation: true,
            mode: "manual",
            detectionStatus: "recent",
            effectiveCandidateID: nil,
            defaultCandidateID: "manual",
            fallbackCandidateID: "fallback",
            detectedIdentity: "GPT-5.6 High",
            detectedActiveSessionCount: 0,
            candidateLabels: ["manual": "GPT-5.5 High"],
            requiresModelSetup: false
        )
    )
    expect(!manual.isAutomatic, "manual mode should be projected once")
    expect(manual.currentCandidateID == "manual", "manual mode should fall back to configured candidate")
    expect(manual.modeLabel == "手动指定", "manual mode label should be stable")
    expect(manual.displayText == "GPT-5.5 High", "candidate labels should be resolved by the presenter")

    let mixed = OperationalStatePresenter.currentModel(
        OperationalStatePresenter.CurrentModelInput(
            hasRecommendation: true,
            mode: "auto",
            detectionStatus: "active_mixed",
            effectiveCandidateID: nil,
            defaultCandidateID: nil,
            fallbackCandidateID: nil,
            detectedIdentity: nil,
            detectedActiveSessionCount: 3,
            candidateLabels: [:],
            requiresModelSetup: false
        )
    )
    expect(mixed.modeLabel == "多会话", "mixed active sessions should have a stable label")
    expect(mixed.displayText == "多个模型（3 个会话）", "mixed session count should be projected")
    expect(
        mixed.automaticDescription == "检测到多个活动会话正在使用不同模型",
        "mixed session explanation should stay outside the View"
    )
}

private func verifyProgressProjection() {
    let questions = [
        OperationalStatePresenter.ProgressQuestionInput(id: "q1", scoreMax: 20),
        OperationalStatePresenter.ProgressQuestionInput(id: "q2", scoreMax: 20),
    ]
    let results = [
        OperationalStatePresenter.ProgressResultInput(questionID: "q1", semanticTotal: 20),
        OperationalStatePresenter.ProgressResultInput(questionID: "q2", semanticTotal: 19),
    ]
    let evidenceProgress = OperationalStatePresenter.progress(
        OperationalStatePresenter.ProgressInput(
            hasRunEntry: false,
            attemptsPerTarget: nil,
            attemptsCompleted: nil,
            questions: questions,
            results: results
        )
    )
    expect(evidenceProgress.completed == 1, "only full semantic totals should count as complete")
    expect(evidenceProgress.total == 2, "question contracts should own the evidence total")

    let runProgress = OperationalStatePresenter.progress(
        OperationalStatePresenter.ProgressInput(
            hasRunEntry: true,
            attemptsPerTarget: 2,
            attemptsCompleted: 5,
            questions: questions,
            results: results
        )
    )
    expect(runProgress.completed == 2, "run progress should clamp completed attempts to its total")
}

private func verifyIngressProjection() {
    let ingress = OperationalStatePresenter.ingress(
        OperationalStatePresenter.IngressInput(
            isLoaded: true,
            sources: [
                OperationalStatePresenter.IngressSourceInput(
                    id: "api",
                    title: "API 来源",
                    mode: "api",
                    isEnabled: true
                ),
                OperationalStatePresenter.IngressSourceInput(
                    id: "disabled",
                    title: "关闭来源",
                    mode: "local",
                    isEnabled: false
                ),
            ],
            connections: [
                OperationalStatePresenter.IngressConnectionInput(
                    id: "api-main",
                    sourceID: "api",
                    name: "主连接",
                    isEnabled: true,
                    hasAPIFormat: true,
                    candidates: [
                        OperationalStatePresenter.IngressCandidateInput(
                            id: "high",
                            modelID: "gpt-5.6-high",
                            familyID: nil,
                            variantID: nil,
                            scanProfile: "default",
                            isEnabled: true
                        ),
                        OperationalStatePresenter.IngressCandidateInput(
                            id: "low",
                            modelID: "gpt-5.6-low",
                            familyID: nil,
                            variantID: nil,
                            scanProfile: "default",
                            isEnabled: false
                        ),
                    ]
                ),
                OperationalStatePresenter.IngressConnectionInput(
                    id: "hidden",
                    sourceID: "disabled",
                    name: "隐藏连接",
                    isEnabled: true,
                    hasAPIFormat: false,
                    candidates: [
                        OperationalStatePresenter.IngressCandidateInput(
                            id: "hidden-candidate",
                            modelID: "gpt-5.5",
                            familyID: nil,
                            variantID: nil,
                            scanProfile: "high",
                            isEnabled: true
                        ),
                    ]
                ),
            ],
            enabledCandidateCount: 1,
            runtimeIsRunning: false,
            hasResumableRun: false
        )
    )
    expect(ingress.scanConnections.map(\.id) == ["api-main"], "disabled sources should stay out of the picker")
    expect(ingress.scanConnections[0].title == "主连接", "API connections should use their connection name")
    expect(ingress.candidateCount == 2, "picker totals should include disabled candidates")
    expect(ingress.selectedCandidateCount == 1, "selected count should include enabled candidates only")
    expect(ingress.currentCandidates.map(\.id) == ["high"], "current-model options should use enabled candidates")
    expect(ingress.scanConnections[0].candidates[0].pickerLabel == "GPT-5.6 High", "API suffix profiles should be projected")
    expect(!ingress.requiresModelSetup, "an enabled candidate should satisfy setup")

    let setup = OperationalStatePresenter.ingress(
        OperationalStatePresenter.IngressInput(
            isLoaded: true,
            sources: [],
            connections: [],
            enabledCandidateCount: 0,
            runtimeIsRunning: false,
            hasResumableRun: false
        )
    )
    expect(setup.requiresModelSetup, "loaded empty ingress should require setup")
    expect(setup.setupHeaderText == "尚未接入模型", "empty ingress should expose setup copy")
}

private func verifyRepairProjection() {
    let entries = [
        OperationalStatePresenter.RepairEntryInput(
            id: "candidate-a",
            displayName: "GPT-5.6 High",
            isRunning: false,
            progressText: "失败 1/5",
            isCurrentRunEligible: false,
            repairableQuestionIDs: ["q1"],
            canDisplayCurrentQuestionScores: true,
            questionStatuses: ["timeout", "passed"]
        ),
        OperationalStatePresenter.RepairEntryInput(
            id: "removed-candidate",
            displayName: "Removed Model",
            isRunning: false,
            progressText: "失败 1/5",
            isCurrentRunEligible: false,
            repairableQuestionIDs: ["q2"],
            canDisplayCurrentQuestionScores: true,
            questionStatuses: ["timeout"]
        ),
    ]
    let idle = OperationalStatePresenter.repair(
        OperationalStatePresenter.RepairInput(
            showsLocalRepairControls: true,
            runtimeIsRunning: false,
            hasResumableRun: false,
            lifecycleState: "idle",
            isScanOperationActive: false,
            pendingControlAction: nil,
            currentPhase: nil,
            currentTarget: nil,
            activeEvaluationCount: 0,
            queuedEvaluationCount: 0,
            runID: "run-1",
            configuredCandidateIDs: ["candidate-a"],
            runCandidateIDs: ["candidate-a", "removed-candidate"],
            entries: entries
        )
    )
    expect(idle.canRetryFailedQuestions, "idle failed evidence should be retryable")
    expect(idle.canRetryTimedOutQuestions, "idle timeout evidence should be retryable")
    expect(idle.repairableQuestionCount == 1, "repairable question count should be projected")
    expect(idle.timedOutQuestionCount == 1, "timeout count should be projected")
    expect(idle.failedNoticeTitle == "GPT-5.6 High 有失败题", "single-candidate notice copy should be stable")
    expect(idle.repairableCandidateIDs == ["candidate-a"], "removed candidates must stay outside repair scope")

    let official = OperationalStatePresenter.repair(
        OperationalStatePresenter.RepairInput(
            showsLocalRepairControls: false,
            runtimeIsRunning: false,
            hasResumableRun: false,
            lifecycleState: "idle",
            isScanOperationActive: false,
            pendingControlAction: nil,
            currentPhase: nil,
            currentTarget: nil,
            activeEvaluationCount: 0,
            queuedEvaluationCount: 0,
            runID: "run-1",
            configuredCandidateIDs: ["candidate-a"],
            runCandidateIDs: ["candidate-a"],
            entries: entries
        )
    )
    expect(official.repairableQuestionCount == 0, "official snapshots must hide local failed questions")
    expect(!official.canRetryFailedQuestions, "official snapshots must not expose local repair actions")

    let paused = OperationalStatePresenter.repair(
        OperationalStatePresenter.RepairInput(
            showsLocalRepairControls: true,
            runtimeIsRunning: false,
            hasResumableRun: true,
            lifecycleState: "paused_recoverable",
            isScanOperationActive: false,
            pendingControlAction: nil,
            currentPhase: nil,
            currentTarget: nil,
            activeEvaluationCount: 0,
            queuedEvaluationCount: 0,
            runID: "run-1",
            configuredCandidateIDs: ["candidate-a"],
            runCandidateIDs: ["candidate-a"],
            entries: entries
        )
    )
    expect(paused.noticeEntryID == "candidate-a", "paused repair backlog should expose one notice entry")
    expect(paused.canDismissNotice, "paused repair notices should be dismissible")
    expect(paused.showRestartButton, "resumable idle work should expose restart")
    expect(!paused.canRetryFailedQuestions, "resumable work should block a competing repair")

    let batch = OperationalStatePresenter.repair(
        OperationalStatePresenter.RepairInput(
            showsLocalRepairControls: true,
            runtimeIsRunning: true,
            hasResumableRun: false,
            lifecycleState: "active_scan",
            isScanOperationActive: true,
            pendingControlAction: nil,
            currentPhase: "repair",
            currentTarget: "重试超时题",
            activeEvaluationCount: 2,
            queuedEvaluationCount: 3,
            runID: "run-1",
            configuredCandidateIDs: ["candidate-a"],
            runCandidateIDs: ["candidate-a"],
            entries: entries
        )
    )
    expect(batch.isBatchRunning, "active repair queues should be projected as a batch")
    expect(batch.batchTitle == "正在重试超时题", "timeout batch title should remain explicit")
    expect(batch.batchStatusText == "2 个任务执行中 · 3 个待执行", "batch counts should be summarized")
}

private func verifyOperationalPresentation() {
    let fresh = OperationalStatePresenter.presentation(presentationInput())
    expect(fresh.heroEyebrow == "个人建议", "visible advisor should own the eyebrow")
    expect(fresh.heroOverallScoreText == "88", "advisor target score should be presented")
    expect(fresh.confidenceLabel == "高置信", "advisor confidence should be localized")
    expect(!fresh.showsConfidenceChip, "the V2 surface should not render the legacy confidence chip")
    expect(fresh.headerDetailText == "先补齐候选证据", "advisor next action should own the summary")
    expect(fresh.heroDisplayLabel == "GPT-5.5 High", "advisor target label should be projected")
    expect(fresh.heroIdentityLabel == "当前使用", "compare-first fallback should identify current use")

    let refreshing = OperationalStatePresenter.presentation(
        presentationInput(
            availability: availability(state: .activeScan, advisorInput: nil),
            state: .activeScan,
            glanceTone: .active,
            hasRefreshIssue: true,
            runtimeIsRunning: true,
            advisorInput: nil
        )
    )
    expect(refreshing.operationalTone == .warning, "refresh issues should override the runtime tone")
    expect(refreshing.heroDecisionTitle == "扫描进行中，结论待定", "refreshing title priority must stay stable")
    expect(refreshing.heroDecisionReason == "刷新失败 连接中断", "refresh details should own the reason")
    expect(refreshing.confidenceLabel == "待定", "active runs should not expose stale confidence")
    expect(refreshing.footerDataStatusText == "正在评测 · 3/5", "footer should expose live progress")

    let hiddenAvailability = availability(canDisplayScores: [false], advisorInput: nil)
    let hidden = OperationalStatePresenter.presentation(
        presentationInput(
            availability: hiddenAvailability,
            advisorInput: nil,
            radarTitle: "先完成本机快测",
            radarReason: "完成本机评测后生成建议。"
        )
    )
    expect(hidden.heroDecisionTitle == "先完成本机快测", "V2 radar title should remain authoritative")
    expect(
        hidden.heroDecisionReason == "完成本机评测后生成建议。",
        "V2 radar reason should remain authoritative"
    )
    expect(hidden.heroOverallScoreText == "未测", "legacy totals should stay hidden")
    expect(hidden.confidenceLabel == "待扫描", "legacy confidence should stay hidden")

    let setup = OperationalStatePresenter.presentation(
        presentationInput(requiresModelSetup: true, advisorInput: nil)
    )
    expect(setup.heroDecisionTitle == "先选择要比较的模型", "setup title should preserve configured state")
    expect(setup.heroAccent == .interaction, "setup should use interaction emphasis")

    let localFooter = OperationalStatePresenter.presentation(
        presentationInput(advisorInput: nil)
    )
    expect(localFooter.footerDataStatusText == "本机实测 · 12:30 更新", "local freshness should be projected")

    let cachedOfficialFooter = OperationalStatePresenter.presentation(
        presentationInput(
            advisorInput: nil,
            radarDisplaySource: "official_snapshot",
            radarReferenceFreshness: "fresh",
            radarReferenceAgeHours: 2,
            referenceDeliveryRefreshStatus: "cached",
            referenceDeliverySource: "cache",
            localCompletedAt: nil
        )
    )
    expect(
        cachedOfficialFooter.footerDataStatusText == "官网榜单（2 小时前）",
        "cached transport should preserve the ModelDial leaderboard source"
    )

    let yesterdayFooter = OperationalStatePresenter.presentation(
        presentationInput(
            advisorInput: nil,
            localCompletedAt: isoTimestamp(
                localDate(year: 2026, month: 7, day: 28, hour: 8, minute: 19)
            )
        )
    )
    expect(
        yesterdayFooter.footerDataStatusText == "本机实测 · 昨天 08:19 更新",
        "yesterday freshness should disambiguate the date"
    )

    let earlierFooter = OperationalStatePresenter.presentation(
        presentationInput(
            advisorInput: nil,
            localCompletedAt: isoTimestamp(
                localDate(year: 2026, month: 7, day: 27, hour: 8, minute: 19)
            )
        )
    )
    expect(
        earlierFooter.footerDataStatusText == "本机实测 · 7月27日 08:19 更新",
        "earlier freshness should include month and day"
    )

    let previousYearFooter = OperationalStatePresenter.presentation(
        presentationInput(
            advisorInput: nil,
            now: localDate(year: 2026, month: 1, day: 2, hour: 18, minute: 0),
            localCompletedAt: isoTimestamp(
                localDate(year: 2025, month: 12, day: 31, hour: 8, minute: 19)
            )
        )
    )
    expect(
        previousYearFooter.footerDataStatusText == "本机实测 · 2025年12月31日 08:19 更新",
        "cross-year freshness should include the year"
    )
}

private func verifyOfficialPublicationTime() {
    // The remote runner starts at 23:00 UTC, while the public snapshot is
    // published at the next fixed slot, 00:00 UTC.  Official App copy must
    // use the latter timestamp; in the current Beijing locale that is 08:00.
    let officialFooter = OperationalStatePresenter.presentation(
        presentationInput(
            advisorInput: nil,
            radarDisplaySource: "official_snapshot",
            radarReferenceFreshness: "fresh",
            radarReferenceAgeHours: 0,
            referenceDeliveryRefreshStatus: "refreshed",
            referenceDeliverySource: "http",
            referencePublishedAt: "2026-08-06T00:00:00Z",
            now: localDate(year: 2026, month: 8, day: 6, hour: 8, minute: 30),
            localCompletedAt: nil
        )
    )
    expect(
        officialFooter.footerDataStatusText == "官网榜单 · 08:00 更新",
        "official freshness should display published_at (00:00 UTC / 08:00 Beijing), not started_at"
    )
}

private func verifyReferenceRefreshPresentation() {
    let refreshed = OperationalStatePresenter.referenceRefreshPresentation(
        status: "refreshed"
    )
    expect(refreshed?.text == "榜单已更新", "a changed remote batch should confirm the update")
    expect(
        refreshed?.symbolName == "checkmark.circle.fill",
        "a changed remote batch should use the success symbol"
    )
    expect(refreshed?.tone == .success, "a changed remote batch should use success tone")

    let current = OperationalStatePresenter.referenceRefreshPresentation(
        status: "not_modified"
    )
    expect(current?.text == "当前已是最新结果", "an unchanged batch should acknowledge freshness")
    expect(
        current?.symbolName == "checkmark.circle",
        "an unchanged batch should use the confirmation symbol"
    )
    expect(current?.tone == .neutral, "an unchanged batch should stay neutral")

    let failed = OperationalStatePresenter.referenceRefreshPresentation(status: "failed")
    expect(
        failed?.text == "官网更新失败，正在使用缓存结果",
        "a failed refresh should preserve the cache fallback explanation"
    )
    expect(failed?.tone == .failure, "a failed refresh should use failure tone")
}

private func verifyScanActivityPresentation() {
    let now = Date(timeIntervalSince1970: 100)
    let running = OperationalStatePresenter.scanActivityText(
        OperationalStatePresenter.ScanActivityInput(
            isRunning: true,
            currentCompletedAt: nil,
            lastCompletedAt: nil,
            now: now
        )
    )
    expect(running == L10n.ScanActivity.inProgress, "running state should own scan activity")

    let empty = OperationalStatePresenter.scanActivityText(
        OperationalStatePresenter.ScanActivityInput(
            isRunning: false,
            currentCompletedAt: nil,
            lastCompletedAt: nil,
            now: now
        )
    )
    expect(empty == L10n.ScanActivity.noHistory, "missing history should remain explicit")

    let recentFallback = OperationalStatePresenter.scanActivityText(
        OperationalStatePresenter.ScanActivityInput(
            isRunning: false,
            currentCompletedAt: nil,
            lastCompletedAt: now.addingTimeInterval(-1),
            now: now
        )
    )
    expect(
        recentFallback == L10n.ScanActivity.justCompleted,
        "the last accepted snapshot should provide the recent completion fallback"
    )

    let currentCompletion = now.addingTimeInterval(-10)
    let current = OperationalStatePresenter.scanActivityText(
        OperationalStatePresenter.ScanActivityInput(
            isRunning: false,
            currentCompletedAt: currentCompletion,
            lastCompletedAt: now.addingTimeInterval(-1),
            now: now
        )
    )
    expect(
        current == L10n.ScanActivity.lastCompleted(
            relativeTime: LocalizedFormatters.relativeDateTime(
                from: currentCompletion,
                relativeTo: now
            )
        ),
        "the authoritative dashboard completion should take precedence over the fallback"
    )
}

private func source(
    rawModelID: String? = nil,
    rawEffort: String? = nil,
    connectionParts: [String?] = [],
    route: String? = nil,
    completedAt: String? = nil
) -> ConfigurationEvidencePresenter.SourceInput {
    ConfigurationEvidencePresenter.SourceInput(
        rawModelID: rawModelID,
        rawEffort: rawEffort,
        connectionParts: connectionParts,
        routeFingerprint: route,
        completedAt: completedAt
    )
}

private func item(
    id: String,
    displayName: String,
    modelName: String = "gpt-5.6",
    effort: String = "high",
    official: ConfigurationEvidencePresenter.SourceInput,
    local: ConfigurationEvidencePresenter.SourceInput
) -> ConfigurationEvidencePresenter.ItemInput {
    ConfigurationEvidencePresenter.ItemInput(
        id: id,
        displayName: displayName,
        modelName: modelName,
        effort: effort,
        official: official,
        local: local
    )
}

private func verifyConfigurationEvidencePresentation() {
    let current = item(
        id: "current",
        displayName: "Current",
        official: source(
            rawModelID: "gpt-5.6-2026-07",
            rawEffort: "xhigh",
            connectionParts: ["openai", "priority"],
            route: "route-current-1234567890",
            completedAt: "2026-07-29T12:30:00Z"
        ),
        local: source(
            connectionParts: ["codex", "login"],
            route: "local-shared",
            completedAt: "2026-07-29T11:00:00Z"
        )
    )
    let candidate = item(
        id: "candidate",
        displayName: "Candidate",
        official: source(
            connectionParts: ["openai", "priority"],
            route: nil,
            completedAt: "2026-07-29T12:30:59Z"
        ),
        local: source(
            connectionParts: ["api", "candidate"],
            route: "local-shared",
            completedAt: "2026-07-29T10:00:00Z"
        )
    )

    let official = ConfigurationEvidencePresenter.presentation(
        displaySource: "official_snapshot",
        current: current,
        candidate: candidate
    )
    expect(official.sourceLabel == "官网实测", "official source label should be canonical")
    expect(official.sharedConnectionText == "openai · priority", "shared connection should be deduplicated")
    expect(official.sharedCompletionText == "2026-07-29 12:30", "completion comparison should use displayed precision")
    expect(official.rows.count == 2, "identity and route differences should expose both rows")
    expect(
        official.rows[0].identityDifferenceText == "原始 ID：gpt-5.6-2026-07 · 思考档位：xhigh",
        "official raw identity differences should be projected"
    )
    expect(official.rows[0].connectionText == nil, "shared connections should not repeat per row")
    expect(official.rows[0].routeText == "route-cu…567890", "long routes should be compacted")
    expect(official.rows[1].routeText == "未知", "missing side of a route difference should stay explicit")

    let local = ConfigurationEvidencePresenter.presentation(
        displaySource: "local_evaluation",
        current: current,
        candidate: candidate
    )
    expect(local.sourceLabel == "本机实测", "local source label should be canonical")
    expect(local.sharedConnectionText == nil, "different local connections should remain per row")
    expect(local.rows.allSatisfy { $0.connectionText != nil }, "local connection evidence should stay visible")
    expect(local.rows.allSatisfy { $0.routeText == nil }, "matching routes should be represented once in basis evidence")
    expect(local.routeEvidenceText == "local-shared", "matching routes should be rendered once")

    let graderVersion = ConfigurationEvidencePresenter.graderVersion(
        officialGraderVersion: nil,
        questionPackVersion: "pack-v2",
        localResults: [
            ConfigurationEvidencePresenter.GraderResultInput(
                questionPackVersion: "pack-v1",
                graderVersion: "grader-old"
            ),
            ConfigurationEvidencePresenter.GraderResultInput(
                questionPackVersion: "pack-v2",
                graderVersion: "grader-current"
            ),
        ]
    )
    expect(graderVersion == "grader-current", "grader evidence should match the selected question pack")

    let routing = ConfigurationEvidencePresenter.routing(
        ConfigurationEvidencePresenter.RoutingInput(
            displaySource: "official_snapshot",
            officialSnapshotIsTrusted: true,
            officialQuestionPackVersion: "official-pack",
            officialGraderVersion: "official-grader",
            officialSnapshotID: "official-batch",
            officialPricingSnapshotID: nil,
            localQuestionPackVersion: "local-pack",
            localGraderResults: [],
            localSnapshotID: "local-run",
            recommendationPricingSnapshotID: "recommendation-price",
            diagnosticPricingSnapshotID: "diagnostic-price"
        )
    )
    expect(routing.usesOfficialSnapshot, "official display source should select the reference snapshot")
    expect(!routing.usesLocalDataset, "official display source should suppress local datasets")
    expect(routing.questionPackVersion == "official-pack", "official question pack should win")
    expect(routing.evaluationSnapshotID == "official-batch", "official batch ID should win")
    expect(routing.pricingSnapshotID == "recommendation-price", "missing official pricing should use recommendation pricing")

    let untrustedRouting = ConfigurationEvidencePresenter.routing(
        ConfigurationEvidencePresenter.RoutingInput(
            displaySource: "official_snapshot",
            officialQuestionPackVersion: "seed-pack",
            officialGraderVersion: "seed-grader",
            officialSnapshotID: "seed-batch",
            officialPricingSnapshotID: "seed-pricing",
            localQuestionPackVersion: "local-pack",
            localGraderResults: [],
            localSnapshotID: "local-run",
            recommendationPricingSnapshotID: "recommendation-price",
            diagnosticPricingSnapshotID: "diagnostic-price"
        )
    )
    expect(!untrustedRouting.usesOfficialSnapshot, "missing official snapshot provenance must fail closed")
    expect(untrustedRouting.evaluationSnapshotID == "local-run", "untrusted official metadata must not enter evidence")
}

@main
private enum PresenterTestMain {
    static func main() {
        UserDefaults.standard.set(
            AppLanguage.zhHans.rawValue,
            forKey: AppLanguageResolver.key
        )
        verifyOperationalAvailability()
        verifyCurrentModelPresentation()
        verifyProgressProjection()
        verifyIngressProjection()
        verifyRepairProjection()
        verifyOperationalPresentation()
        verifyOfficialPublicationTime()
        verifyReferenceRefreshPresentation()
        verifyScanActivityPresentation()
        verifyConfigurationEvidencePresentation()
        if failureCount > 0 { exit(1) }
        print("Operational and configuration presenter tests passed")
    }
}
