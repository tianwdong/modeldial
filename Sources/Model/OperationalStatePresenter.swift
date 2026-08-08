import Foundation

enum OperationalStatePresenter {
    struct ScanActivityInput {
        let isRunning: Bool
        let currentCompletedAt: Date?
        let lastCompletedAt: Date?
        let now: Date
    }

    static func scanActivityText(_ input: ScanActivityInput) -> String {
        if input.isRunning {
            return L10n.ScanActivity.inProgress
        }
        guard let completedAt = input.currentCompletedAt ?? input.lastCompletedAt else {
            return L10n.ScanActivity.noHistory
        }
        let elapsed = max(0, input.now.timeIntervalSince(completedAt))
        if elapsed < 5 {
            return L10n.ScanActivity.justCompleted
        }
        return L10n.ScanActivity.lastCompleted(
            relativeTime: LocalizedFormatters.relativeDateTime(
                from: completedAt,
                relativeTo: input.now
            )
        )
    }

    struct AdvisorInput: Equatable {
        let decision: String
        let currentCandidateID: String?
        let candidateID: String?
        let confidenceLevel: String
        let nextAction: String
    }

    struct AvailabilityInput: Equatable {
        let state: GlanceState
        let hasEntries: Bool
        let canDisplayCurrentQuestionScores: [Bool]
        let hasResumableRun: Bool
        let requiresModelSetup: Bool
        let isProvisionalResult: Bool
        let hasBestCombination: Bool
        let bestEvidenceState: String?
        let runStatus: String
        let advisor: AdvisorInput?
    }

    struct Availability: Equatable {
        let isEvidenceUpdating: Bool
        let shouldHideLegacyOverviewScores: Bool
        let isAwaitingCurrentScoreScan: Bool
        let showsPersonalAdvisor: Bool
        let advisorTargetCandidateID: String?
    }

    struct CurrentModelInput: Equatable {
        let hasRecommendation: Bool
        let mode: String?
        let detectionStatus: String?
        let effectiveCandidateID: String?
        let defaultCandidateID: String?
        let fallbackCandidateID: String?
        let detectedIdentity: String?
        let detectedActiveSessionCount: Int
        let candidateLabels: [String: String]
        let requiresModelSetup: Bool
    }

    struct CurrentModelPresentation: Equatable {
        let currentCandidateID: String?
        let configuredCandidateID: String?
        let detectionStatus: String
        let modeLabel: String
        let isAutomatic: Bool
        let isUnmapped: Bool
        let displayText: String
        let automaticDescription: String
        let actionAccessibilityHint: String
    }

    struct ProgressQuestionInput: Equatable {
        let id: String
        let scoreMax: Int
    }

    struct ProgressResultInput: Equatable {
        let questionID: String
        let semanticTotal: Int?
    }

    struct ProgressInput: Equatable {
        let hasRunEntry: Bool
        let attemptsPerTarget: Int?
        let attemptsCompleted: Int?
        let questions: [ProgressQuestionInput]
        let results: [ProgressResultInput]
    }

    struct Progress: Equatable {
        let completed: Int
        let total: Int
    }

    struct IngressSourceInput: Equatable {
        let id: String
        let title: String
        let mode: String
        let isEnabled: Bool
    }

    struct IngressCandidateInput: Equatable {
        let id: String
        let modelID: String
        let familyID: String?
        let variantID: String?
        let scanProfile: String
        let isEnabled: Bool
    }

    struct IngressConnectionInput: Equatable {
        let id: String
        let sourceID: String
        let name: String
        let isEnabled: Bool
        let hasAPIFormat: Bool
        let candidates: [IngressCandidateInput]
    }

    struct IngressInput: Equatable {
        let isLoaded: Bool
        let sources: [IngressSourceInput]
        let connections: [IngressConnectionInput]
        let enabledCandidateCount: Int
        let runtimeIsRunning: Bool
        let hasResumableRun: Bool
    }

    struct IngressCandidatePresentation: Equatable, Identifiable {
        let id: String
        let connectionID: String
        let pickerLabel: String
        let currentModelLabel: String
        let currentModelDetail: String
        let isEnabled: Bool
    }

    struct IngressConnectionPresentation: Equatable, Identifiable {
        let id: String
        let title: String
        let candidates: [IngressCandidatePresentation]
        let selectedCandidateCount: Int
    }

    struct IngressPresentation: Equatable {
        let scanConnections: [IngressConnectionPresentation]
        let currentCandidates: [IngressCandidatePresentation]
        let candidateCount: Int
        let selectedCandidateCount: Int
        let selectionIsLocked: Bool
        let requiresModelSetup: Bool
        let hasConfiguredCandidates: Bool
        let setupHeaderText: String
    }

    struct RepairEntryInput: Equatable {
        let id: String
        let displayName: String
        let isRunning: Bool
        let progressText: String
        let isCurrentRunEligible: Bool
        let repairableQuestionIDs: [String]
        let canDisplayCurrentQuestionScores: Bool
        let questionStatuses: [String]
    }

    struct RepairInput: Equatable {
        let showsLocalRepairControls: Bool
        let runtimeIsRunning: Bool
        let hasResumableRun: Bool
        let lifecycleState: String
        let isScanOperationActive: Bool
        let pendingControlAction: String?
        let currentPhase: String?
        let currentTarget: String?
        let activeEvaluationCount: Int
        let queuedEvaluationCount: Int
        let runID: String?
        let configuredCandidateIDs: [String]
        let runCandidateIDs: [String]
        let entries: [RepairEntryInput]
    }

    struct RepairPresentation: Equatable {
        let runID: String?
        let activeTaskCount: Int
        let queuedTaskCount: Int
        let isBatchRunning: Bool
        let batchTitle: String
        let batchStatusText: String
        let repairableCandidateIDs: [String]
        let repairableQuestionCount: Int
        let timedOutCandidateIDs: [String]
        let timedOutQuestionCount: Int
        let canRetryFailedQuestions: Bool
        let canRetryTimedOutQuestions: Bool
        let failedNoticeTitle: String
        let noticeEntryID: String?
        let noticeRetryIsDisabled: Bool
        let canDismissNotice: Bool
        let showRestartButton: Bool
    }

    struct BestInput: Equatable {
        let displayLabel: String
        let evidenceState: String
        let recommendationOutcome: String
        let decisionReason: String
        let overallScore: Int?
        let overallScoreText: String?
        let scoreText: String
        let confidenceLabel: String
    }

    struct ProvisionalInput: Equatable {
        let displayLabel: String?
        let scoreText: String?
        let hasModeScore: Bool
        let confidenceLabel: String?
        let confidenceReason: String?
        let statusLabel: String?
        let evaluationProfileLabel: String?
        let completedQuestionCount: Int
        let totalQuestionCount: Int
    }

    struct PresentationInput: Equatable {
        let availability: Availability
        let state: GlanceState
        let glanceTone: GlanceTone
        let runStatus: String
        let hasSnapshotRefreshIssue: Bool
        let snapshotRefreshMessage: String?
        let snapshotRefreshDetail: String?
        let runtimeIsRunning: Bool
        let runtimeLastError: String?
        let runtimeProgressText: String
        let activeEvaluationTimingText: String?
        let hasResumableRun: Bool
        let entryDestination: GlanceDestination
        let glanceDestination: GlanceDestination
        let glancePeekLeftSecondary: String?
        let requiresModelSetup: Bool
        let hasConfiguredModelCandidates: Bool
        let radarDisplaySource: String?
        let radarReferenceFreshness: String?
        let radarReferenceAgeHours: Int?
        let referenceDeliveryRefreshStatus: String?
        let referenceDeliverySource: String?
        let referencePublishedAt: String?
        let localCompletedAt: String?
        let now: Date
        let hasRadarPortfolio: Bool
        let radarPortfolioStatus: String?
        let best: BestInput?
        let provisional: ProvisionalInput?
        let advisor: AdvisorInput?
        let advisorDisplayLabel: String?
        let advisorOverallScore: Int?
        let radarTitle: String?
        let radarReason: String?
        let fallbackTitle: String
        let fallbackReason: String
        let isUnmappedCurrentModel: Bool
        let detectedCurrentModelIdentity: String?
        let completeQuestionSetLabel: String
        let questionRoundLabel: String
    }

    enum Accent: Equatable {
        case interaction
        case warning
        case operational
    }

    enum TextEmphasis: Equatable {
        case primary
        case secondary
        case tertiary
        case positive
        case warning
        case accent
    }

    struct Presentation: Equatable {
        let operationalTone: GlanceTone
        let heroAccent: Accent
        let heroEyebrow: String
        let heroOverallScoreText: String
        let heroOverallScoreEmphasis: TextEmphasis
        let confidenceLabel: String
        let confidenceEmphasis: TextEmphasis
        let showsConfidenceChip: Bool
        let heroDecisionTitle: String
        let heroDecisionReason: String
        let heroDisplayLabel: String
        let heroIdentityLabel: String
        let headerDetailText: String
        let footerDataStatusText: String?
        let footerTone: GlanceTone
    }

    struct ReferenceRefreshPresentation: Equatable {
        let text: String
        let symbolName: String
        let tone: GlanceTone
    }

    static func referenceRefreshPresentation(
        status: String?
    ) -> ReferenceRefreshPresentation? {
        guard let status else { return nil }
        switch status {
        case "refreshed":
            return ReferenceRefreshPresentation(
                text: L10n.tr("榜单已更新"),
                symbolName: "checkmark.circle.fill",
                tone: .success
            )
        case "not_modified":
            return ReferenceRefreshPresentation(
                text: L10n.tr("当前已是最新结果"),
                symbolName: "checkmark.circle",
                tone: .neutral
            )
        case "cached":
            return ReferenceRefreshPresentation(
                text: L10n.tr("当前使用已缓存结果"),
                symbolName: "externaldrive.fill",
                tone: .warning
            )
        case "not_configured":
            return ReferenceRefreshPresentation(
                text: L10n.tr("榜单更新源未配置"),
                symbolName: "exclamationmark.triangle.fill",
                tone: .warning
            )
        case "failed":
            return ReferenceRefreshPresentation(
                text: L10n.tr("官网更新失败，正在使用缓存结果"),
                symbolName: "exclamationmark.triangle.fill",
                tone: .failure
            )
        default:
            return ReferenceRefreshPresentation(
                text: L10n.tr("榜单更新完成"),
                symbolName: "checkmark.circle",
                tone: .neutral
            )
        }
    }

    static func availability(_ input: AvailabilityInput) -> Availability {
        let isEvidenceUpdating: Bool
        switch input.state {
        case .preparing, .activeScan, .pausing, .stopping, .finalizing:
            isEvidenceUpdating = true
        default:
            isEvidenceUpdating = false
        }
        let shouldHideLegacyOverviewScores = input.hasEntries
            && !input.canDisplayCurrentQuestionScores.contains(true)
        let isAwaitingCurrentScoreScan = shouldHideLegacyOverviewScores
            && !isEvidenceUpdating
            && !input.hasResumableRun
        let rejectedRunStatuses = ["degraded", "partial", "failed", "stopped"]
        let showsPersonalAdvisor = !input.requiresModelSetup
            && !isAwaitingCurrentScoreScan
            && !input.isProvisionalResult
            && input.state == .freshRecommendation
            && input.hasBestCombination
            && input.bestEvidenceState != "retained_after_failure"
            && !rejectedRunStatuses.contains(input.runStatus)
            && input.advisor != nil
        let advisorTargetCandidateID: String?
        if showsPersonalAdvisor, let advisor = input.advisor {
            switch advisor.decision {
            case "trial_switch":
                advisorTargetCandidateID = advisor.candidateID
            case "compare_first":
                advisorTargetCandidateID = advisor.candidateID ?? advisor.currentCandidateID
            default:
                advisorTargetCandidateID = advisor.currentCandidateID
            }
        } else {
            advisorTargetCandidateID = nil
        }
        return Availability(
            isEvidenceUpdating: isEvidenceUpdating,
            shouldHideLegacyOverviewScores: shouldHideLegacyOverviewScores,
            isAwaitingCurrentScoreScan: isAwaitingCurrentScoreScan,
            showsPersonalAdvisor: showsPersonalAdvisor,
            advisorTargetCandidateID: advisorTargetCandidateID
        )
    }

    static func currentModel(
        _ input: CurrentModelInput
    ) -> CurrentModelPresentation {
        let isAutomatic = input.mode != "manual"
        let detectionStatus = input.detectionStatus ?? "unavailable"
        let currentCandidateID: String?
        if !input.hasRecommendation {
            currentCandidateID = input.fallbackCandidateID
        } else if isAutomatic {
            currentCandidateID = input.effectiveCandidateID
        } else {
            currentCandidateID = input.effectiveCandidateID ?? input.defaultCandidateID
        }
        let isUnmapped = isAutomatic
            && detectionStatus == "unmapped"
            && input.detectedIdentity != nil

        let modeLabel: String
        if input.requiresModelSetup {
            modeLabel = input.detectedIdentity == nil
                ? L10n.tr("先完成模型接入")
                : L10n.tr("已识别，尚未加入扫描")
        } else if !isAutomatic {
            modeLabel = L10n.tr("手动指定")
        } else {
            switch detectionStatus {
            case "active_mixed": modeLabel = L10n.tr("多会话")
            case "active_single": modeLabel = L10n.tr("活动识别")
            case "recent": modeLabel = L10n.tr("最近使用")
            case "unmapped": modeLabel = L10n.tr("未比较")
            default: modeLabel = L10n.tr("自动识别")
            }
        }

        let displayText: String
        if isAutomatic && detectionStatus == "active_mixed" {
            displayText = input.detectedActiveSessionCount > 0
                ? L10n.tr("多个模型（%lld 个会话）", input.detectedActiveSessionCount)
                : L10n.tr("多个模型")
        } else if isUnmapped, let detectedIdentity = input.detectedIdentity {
            displayText = detectedIdentity
        } else if input.requiresModelSetup && input.detectedIdentity == nil {
            displayText = L10n.tr("尚未设置")
        } else if let currentCandidateID {
            displayText = input.candidateLabels[currentCandidateID] ?? currentCandidateID
        } else {
            displayText = L10n.tr("尚未指定当前模型")
        }

        let automaticDescription: String
        switch detectionStatus {
        case "active_mixed":
            automaticDescription = L10n.tr("检测到多个活动会话正在使用不同模型")
        case "active_single":
            automaticDescription = L10n.tr("活动会话正在使用 %@", displayText)
        case "recent":
            automaticDescription = L10n.tr("最近使用 %@", displayText)
        case "unmapped":
            automaticDescription = input.detectedIdentity.map {
                L10n.tr("已识别 %@，但尚未参与比较", $0)
            } ?? L10n.tr("已识别当前模型，但尚未参与比较")
        default:
            automaticDescription = L10n.tr("尚未识别活动会话或最近使用模型")
        }

        return CurrentModelPresentation(
            currentCandidateID: currentCandidateID,
            configuredCandidateID: input.defaultCandidateID,
            detectionStatus: detectionStatus,
            modeLabel: modeLabel,
            isAutomatic: isAutomatic,
            isUnmapped: isUnmapped,
            displayText: displayText,
            automaticDescription: automaticDescription,
            actionAccessibilityHint: input.requiresModelSetup
                ? L10n.tr("点击前往模型接入，选择要比较的模型。")
                : isAutomatic
                    ? L10n.tr("当前使用自动识别；点击可手动指定模型。")
                    : L10n.tr("当前使用手动指定；点击可更换模型或恢复自动识别。")
        )
    }

    static func progress(_ input: ProgressInput) -> Progress {
        if input.hasRunEntry {
            let total = max(input.attemptsPerTarget ?? input.questions.count, 1)
            let completed = min(
                max(input.attemptsCompleted ?? input.results.count, 0),
                total
            )
            return Progress(completed: completed, total: total)
        }
        let completed = input.questions.filter { question in
            input.results.contains { result in
                question.id == result.questionID
                    && result.semanticTotal == question.scoreMax
            }
        }.count
        return Progress(completed: completed, total: max(input.questions.count, 1))
    }

    static func ingress(_ input: IngressInput) -> IngressPresentation {
        let sourcesByID = Dictionary(
            input.sources.map { ($0.id, $0) },
            uniquingKeysWith: { first, _ in first }
        )
        let scanConnections: [IngressConnectionPresentation] = input.connections.compactMap {
            connection in
            guard let source = sourcesByID[connection.sourceID],
                  source.isEnabled,
                  connection.isEnabled,
                  !connection.candidates.isEmpty else {
                return nil
            }
            let candidates = connection.candidates.map { candidate in
                ingressCandidate(
                    candidate,
                    connection: connection,
                    connectionName: connection.name
                )
            }
            return IngressConnectionPresentation(
                id: connection.id,
                title: source.mode == "api" ? connection.name : source.title,
                candidates: candidates,
                selectedCandidateCount: candidates.filter(\.isEnabled).count
            )
        }
        let currentCandidates = scanConnections.flatMap { connection in
            connection.candidates.filter(\.isEnabled)
        }
        let configuredCandidateCount = input.connections.reduce(0) {
            $0 + $1.candidates.count
        }
        let hasConfiguredCandidates = configuredCandidateCount > 0
        let requiresModelSetup = input.isLoaded
            && input.enabledCandidateCount == 0
            && !input.runtimeIsRunning
            && !input.hasResumableRun
        return IngressPresentation(
            scanConnections: scanConnections,
            currentCandidates: currentCandidates,
            candidateCount: scanConnections.reduce(0) { $0 + $1.candidates.count },
            selectedCandidateCount: scanConnections.reduce(0) {
                $0 + $1.selectedCandidateCount
            },
            selectionIsLocked: input.runtimeIsRunning || input.hasResumableRun,
            requiresModelSetup: requiresModelSetup,
            hasConfiguredCandidates: hasConfiguredCandidates,
            setupHeaderText: hasConfiguredCandidates
                ? L10n.tr("尚未选择扫描档位")
                : L10n.tr("尚未接入模型")
        )
    }

    static func repair(_ input: RepairInput) -> RepairPresentation {
        let activeTaskCount = max(input.activeEvaluationCount, 0)
        let queuedTaskCount = max(input.queuedEvaluationCount, 0)
        let isBatchRunning = input.runtimeIsRunning
            && input.currentPhase == "repair"
            && activeTaskCount + queuedTaskCount > 0
        let batchStatusText: String
        if activeTaskCount == 0 {
            batchStatusText = queuedTaskCount > 0
                ? L10n.tr("%lld 个任务等待开始", queuedTaskCount)
                : L10n.tr("正在整理重试结果")
        } else if queuedTaskCount > 0 {
            batchStatusText = L10n.tr(
                "%lld 个任务执行中 · %lld 个待执行",
                activeTaskCount,
                queuedTaskCount
            )
        } else {
            batchStatusText = L10n.tr("%lld 个任务执行中", activeTaskCount)
        }

        let eligibleCandidateIDs = Set(input.configuredCandidateIDs)
            .intersection(input.runCandidateIDs)
        let eligibleEntries = input.showsLocalRepairControls
            ? input.entries.filter { eligibleCandidateIDs.contains($0.id) }
            : []
        let repairableEntries = eligibleEntries.filter {
            !$0.isCurrentRunEligible && !$0.repairableQuestionIDs.isEmpty
        }
        let timedOutEntries = eligibleEntries.filter {
            $0.canDisplayCurrentQuestionScores && $0.questionStatuses.contains("timeout")
        }
        let repairableQuestionCount = repairableEntries.reduce(0) {
            $0 + $1.repairableQuestionIDs.count
        }
        let timedOutQuestionCount = timedOutEntries.reduce(0) { count, entry in
            count + entry.questionStatuses.filter { $0 == "timeout" }.count
        }
        let hasDismissibleBacklog = input.hasResumableRun
            || input.lifecycleState == "paused_recoverable"
        let noticeEntry = repairableEntries.first {
            $0.isRunning && $0.progressText.hasPrefix("重试 ")
        } ?? (!input.runtimeIsRunning && hasDismissibleBacklog
            ? repairableEntries.first
            : nil)
        let canRetryFailedQuestions = !input.runtimeIsRunning
            && !input.hasResumableRun
            && !input.isScanOperationActive
            && input.pendingControlAction == nil
            && repairableQuestionCount > 0
            && input.runID != nil
        let canRetryTimedOutQuestions = !input.runtimeIsRunning
            && !input.hasResumableRun
            && !input.isScanOperationActive
            && timedOutQuestionCount > 0
            && input.runID != nil

        return RepairPresentation(
            runID: input.runID,
            activeTaskCount: activeTaskCount,
            queuedTaskCount: queuedTaskCount,
            isBatchRunning: isBatchRunning,
            batchTitle: input.currentTarget == "重试超时题"
                ? L10n.tr("正在重试超时题")
                : L10n.tr("正在并行重试失败题"),
            batchStatusText: batchStatusText,
            repairableCandidateIDs: repairableEntries.map(\.id),
            repairableQuestionCount: repairableQuestionCount,
            timedOutCandidateIDs: timedOutEntries.map(\.id),
            timedOutQuestionCount: timedOutQuestionCount,
            canRetryFailedQuestions: canRetryFailedQuestions,
            canRetryTimedOutQuestions: canRetryTimedOutQuestions,
            failedNoticeTitle: repairableEntries.count == 1
                ? L10n.tr("%@ 有失败题", repairableEntries[0].displayName)
                : L10n.tr("%lld 个模型有失败题", repairableEntries.count),
            noticeEntryID: noticeEntry?.id,
            noticeRetryIsDisabled: noticeEntry?.isRunning == true || input.runtimeIsRunning,
            canDismissNotice: !input.runtimeIsRunning && hasDismissibleBacklog,
            showRestartButton: input.showsLocalRepairControls
                && input.hasResumableRun
                && !input.runtimeIsRunning
                && input.pendingControlAction == nil
        )
    }

    static func presentation(_ input: PresentationInput) -> Presentation {
        let operationalTone = operationalTone(input)
        let operationalTitle = operationalTitle(input)
        let operationalReason = operationalReason(input)
        let heroAccent: Accent
        if input.requiresModelSetup {
            heroAccent = .interaction
        } else if input.radarDisplaySource == "official_snapshot",
                  input.radarReferenceFreshness == "expired"
                    || input.radarReferenceFreshness == "delayed" {
            heroAccent = .warning
        } else {
            heroAccent = .operational
        }

        let heroOverallScoreText = overallScoreText(input)
        let confidenceValue = confidenceValue(input)
        let confidenceLabel: String
        switch confidenceValue {
        case "高": confidenceLabel = L10n.tr("高置信")
        case "中": confidenceLabel = L10n.tr("中置信")
        case "低": confidenceLabel = L10n.tr("低置信")
        default: confidenceLabel = L10n.tr(confidenceValue)
        }
        let confidenceEmphasis: TextEmphasis
        if ["待定", "已暂停", "旧结果", "需谨慎", "不可用"].contains(confidenceValue) {
            confidenceEmphasis = .accent
        } else {
            switch confidenceValue {
            case "高": confidenceEmphasis = .positive
            case "中": confidenceEmphasis = .secondary
            default: confidenceEmphasis = .warning
            }
        }
        let heroSummary = decisionSummary(input)
        let destinationReason = entryDestinationReason(input)
        let heroReason = decisionReason(
            input,
            destinationReason: destinationReason,
            operationalReason: operationalReason
        )
        let headerDetailText: String
        if input.availability.isEvidenceUpdating {
            headerDetailText = input.runtimeProgressText
        } else if input.hasResumableRun {
            headerDetailText = L10n.tr("中断于 %@", input.runtimeProgressText)
        } else {
            headerDetailText = heroSummary
        }
        let sourceStatus = sourceStatus(input)
        let footerDataStatusText = input.availability.isEvidenceUpdating
            ? L10n.tr("正在评测 · %@", input.runtimeProgressText)
            : operationalTitle ?? sourceStatus.text
        let footerTone: GlanceTone
        if !input.availability.isEvidenceUpdating,
           operationalTitle == nil,
           sourceStatus.isWarning {
            footerTone = .warning
        } else {
            footerTone = operationalTone
        }

        return Presentation(
            operationalTone: operationalTone,
            heroAccent: heroAccent,
            heroEyebrow: eyebrow(input),
            heroOverallScoreText: heroOverallScoreText,
            heroOverallScoreEmphasis: overallScoreEmphasis(input),
            confidenceLabel: confidenceLabel,
            confidenceEmphasis: confidenceEmphasis,
            showsConfidenceChip: false,
            heroDecisionTitle: decisionTitle(input, operationalTitle: operationalTitle),
            heroDecisionReason: heroReason,
            heroDisplayLabel: displayLabel(input),
            heroIdentityLabel: identityLabel(input),
            headerDetailText: headerDetailText,
            footerDataStatusText: footerDataStatusText,
            footerTone: footerTone
        )
    }

    private static func operationalTone(_ input: PresentationInput) -> GlanceTone {
        if input.hasSnapshotRefreshIssue || input.best?.evidenceState == "retained_after_failure" {
            return .warning
        }
        if ["degraded", "partial", "failed", "stopped"].contains(input.runStatus),
           input.glanceTone == .neutral || input.glanceTone == .success {
            return .warning
        }
        return input.glanceTone
    }

    private static func operationalTitle(_ input: PresentationInput) -> String? {
        if input.hasSnapshotRefreshIssue {
            return input.runtimeIsRunning
                ? L10n.tr("扫描进行中，结论待定")
                : L10n.tr("数据未更新，沿用上次结果")
        }
        if input.best?.evidenceState == "retained_after_failure" {
            return L10n.tr("扫描失败，沿用上次结果")
        }
        if ["partial", "failed", "stopped"].contains(input.runStatus), input.best != nil {
            return L10n.tr("本轮未完成，沿用有效结果")
        }
        if input.runStatus == "degraded", input.state == .degradedRecommendation {
            return L10n.tr("谨慎参考当前榜首")
        }
        switch input.state {
        case .backendUnavailableWithoutCache: return L10n.tr("本地数据暂不可用")
        case .backendUnavailableWithCache: return L10n.tr("数据未更新，沿用上次结果")
        case .activeScan: return L10n.tr("扫描进行中，结论待定")
        case .pausing: return L10n.tr("正在暂停扫描")
        case .stopping: return L10n.tr("正在停止扫描")
        case .preparing: return L10n.tr("正在准备扫描")
        case .pausedOrRecoverable: return L10n.tr("扫描已暂停，可继续")
        case .finalizing: return L10n.tr("正在整理扫描结果")
        case .recommendationUnavailable: return L10n.tr("本轮暂无法形成推荐")
        case .failedWithoutRecommendation: return L10n.tr("本轮扫描失败")
        case .failedWithFallbackRecommendation: return L10n.tr("扫描失败，沿用上次结果")
        case .degradedRecommendation: return L10n.tr("谨慎参考当前榜首")
        case .freshRecommendation: return nil
        case .staleRecommendation: return L10n.tr("结果需要更新")
        case .expiredRecommendation: return L10n.tr("结果已过期，请重扫")
        case .neverScanned: return L10n.tr("等待有效扫描结果")
        }
    }

    private static func operationalReason(_ input: PresentationInput) -> String? {
        if input.hasSnapshotRefreshIssue {
            return [input.snapshotRefreshMessage, input.snapshotRefreshDetail]
                .compactMap { $0 }
                .joined(separator: " ")
        }
        if input.best?.evidenceState == "retained_after_failure" {
            return L10n.tr("本次重扫失败，当前显示的是上一轮有效结果。")
        }
        switch input.runStatus {
        case "degraded":
            return L10n.tr("本轮有部分模型执行异常，榜首只基于有效样本。")
        case "partial", "failed", "stopped":
            if input.best != nil {
                return L10n.tr("本轮未完整结束，当前显示的是最近可用结果。")
            }
        default:
            break
        }
        switch input.state {
        case .backendUnavailableWithoutCache:
            return input.runtimeLastError ?? L10n.tr("暂时无法读取本地数据。")
        case .backendUnavailableWithCache:
            return L10n.tr("本地数据暂未更新，当前显示的是上次结果。")
        case .activeScan:
            return input.activeEvaluationTimingText ?? L10n.tr("本轮扫描还在进行。")
        case .pausing:
            return L10n.tr("正在终止在途模型请求并保存断点，请稍候。")
        case .stopping:
            return L10n.tr("正在终止在途模型请求，请稍候。")
        case .preparing:
            return L10n.tr("正在载入模型与题目，请稍候。")
        case .pausedOrRecoverable:
            return L10n.tr("进度已保存，可继续 %@。", input.runtimeProgressText)
        case .finalizing:
            return L10n.tr("测试已完成，正在汇总推荐与历史数据。")
        case .recommendationUnavailable:
            return input.runtimeLastError ?? L10n.tr("本轮证据不足或推荐计算失败。")
        case .failedWithoutRecommendation:
            return input.runtimeLastError ?? L10n.tr("本轮没有留下可用结果。")
        case .failedWithFallbackRecommendation:
            return L10n.tr("本轮执行失败，当前显示的是上一轮有效结果。")
        case .degradedRecommendation:
            return L10n.tr("本轮有部分模型执行异常，榜首只基于有效样本。")
        case .freshRecommendation:
            return nil
        case .staleRecommendation:
            return L10n.tr("结果不是最新一轮，请重扫后再决定是否切换。")
        case .expiredRecommendation:
            return L10n.tr("历史结果已超过有效期，不应再用于切换决策。")
        case .neverScanned:
            return L10n.tr("等待首次扫描。")
        }
    }

    private static func eyebrow(_ input: PresentationInput) -> String {
        if input.requiresModelSetup { return L10n.tr("开始设置") }
        if input.hasRadarPortfolio && !input.availability.isEvidenceUpdating {
            if input.radarDisplaySource == "official_snapshot"
                && input.radarReferenceFreshness == "expired"
                || input.radarPortfolioStatus == "stale" {
                return L10n.tr("结果已过期")
            }
            if input.radarDisplaySource == "official_snapshot",
               input.radarReferenceFreshness == "delayed" {
                return L10n.tr("快照更新延迟")
            }
            switch input.radarPortfolioStatus {
            case "recommend": return L10n.tr("即时建议")
            case "keep": return L10n.tr("当前最优")
            case "no_usage": return L10n.tr("等待使用记录")
            default: return L10n.tr("等待本机证据")
            }
        }
        switch input.state {
        case .backendUnavailableWithoutCache, .backendUnavailableWithCache: return L10n.tr("本地数据")
        case .preparing: return L10n.tr("准备扫描")
        case .activeScan: return L10n.tr("扫描中")
        case .pausing: return L10n.tr("暂停中")
        case .stopping: return L10n.tr("停止中")
        case .pausedOrRecoverable: return L10n.tr("扫描已暂停")
        case .finalizing: return L10n.tr("正在整理")
        case .recommendationUnavailable: return L10n.tr("推荐未决")
        case .failedWithoutRecommendation, .failedWithFallbackRecommendation: return L10n.tr("扫描失败")
        case .degradedRecommendation: return L10n.tr("部分结果异常")
        case .staleRecommendation: return L10n.tr("历史结果")
        case .expiredRecommendation: return L10n.tr("结果已过期")
        case .neverScanned: return L10n.tr("等待扫描")
        case .freshRecommendation:
            if input.best?.evidenceState == "retained_after_failure" {
                return L10n.tr("沿用上次结果")
            }
            if ["degraded", "partial", "failed", "stopped"].contains(input.runStatus) {
                return L10n.tr("结果需谨慎参考")
            }
            return input.advisor == nil ? L10n.tr("推荐结论") : L10n.tr("个人建议")
        }
    }

    private static func overallScoreText(_ input: PresentationInput) -> String {
        if input.requiresModelSetup { return "—" }
        if input.availability.shouldHideLegacyOverviewScores { return L10n.tr("未测") }
        if input.availability.isEvidenceUpdating { return "…" }
        switch input.state {
        case .backendUnavailableWithoutCache, .recommendationUnavailable,
             .failedWithoutRecommendation, .expiredRecommendation, .neverScanned:
            return "—"
        default:
            break
        }
        if let provisional = input.provisional {
            return provisional.scoreText ?? "—"
        }
        if input.advisor != nil {
            return input.advisorOverallScore.map(String.init) ?? "—"
        }
        return input.best?.overallScore.map(String.init) ?? "—"
    }

    private static func overallScoreEmphasis(_ input: PresentationInput) -> TextEmphasis {
        if input.availability.shouldHideLegacyOverviewScores { return .tertiary }
        if let provisional = input.provisional {
            return provisional.hasModeScore ? .primary : .tertiary
        }
        if input.advisor != nil {
            return input.advisorOverallScore == nil ? .tertiary : .primary
        }
        return input.best?.overallScore == nil ? .tertiary : .primary
    }

    private static func confidenceValue(_ input: PresentationInput) -> String {
        if input.requiresModelSetup { return "待设置" }
        if input.availability.shouldHideLegacyOverviewScores { return "待扫描" }
        if let provisional = input.provisional { return provisional.confidenceLabel ?? "低" }
        if let advisor = input.advisor {
            switch advisor.confidenceLevel {
            case "high": return "高"
            case "medium": return "中"
            default: return "低"
            }
        }
        switch input.state {
        case .preparing, .activeScan, .pausing, .stopping, .finalizing: return "待定"
        case .pausedOrRecoverable: return "已暂停"
        case .backendUnavailableWithCache, .failedWithFallbackRecommendation,
             .staleRecommendation: return "旧结果"
        case .degradedRecommendation: return "需谨慎"
        case .backendUnavailableWithoutCache, .recommendationUnavailable,
             .failedWithoutRecommendation, .expiredRecommendation: return "不可用"
        case .freshRecommendation, .neverScanned:
            return input.best?.confidenceLabel ?? "低"
        }
    }

    private static func decisionSummary(_ input: PresentationInput) -> String {
        if input.requiresModelSetup {
            return input.hasConfiguredModelCandidates
                ? L10n.tr("已发现模型，但尚未开启任何扫描档位。")
                : L10n.tr("连接本机登录态或 API，并开启至少一个模型档位。")
        }
        if input.availability.shouldHideLegacyOverviewScores {
            return L10n.tr("题包已更新，等待全量扫描")
        }
        if let provisional = input.provisional {
            return L10n.tr(
                "%@ · 覆盖 %lld/%lld 题 · 补全后生成正式推荐",
                provisional.evaluationProfileLabel ?? L10n.tr("初步结果"),
                provisional.completedQuestionCount,
                provisional.totalQuestionCount
            )
        }
        if let advisor = input.advisor { return advisor.nextAction }
        guard let best = input.best else { return L10n.tr("等待首次扫描") }
        if let score = best.overallScore {
            return L10n.tr("总分 %lld/100 · %@", score, input.completeQuestionSetLabel)
        }
        return L10n.tr(
            "题目总分 %@ · 置信度%@",
            best.overallScoreText ?? best.scoreText,
            best.confidenceLabel
        )
    }

    private static func decisionTitle(
        _ input: PresentationInput,
        operationalTitle: String?
    ) -> String {
        if input.requiresModelSetup {
            return input.hasConfiguredModelCandidates
                ? L10n.tr("先选择要比较的模型")
                : L10n.tr("先接入一个模型来源")
        }
        if input.availability.isEvidenceUpdating || input.hasSnapshotRefreshIssue,
           let operationalTitle {
            return operationalTitle
        }
        if input.hasResumableRun { return L10n.tr("扫描已暂停，可继续") }
        if let radarTitle = input.radarTitle { return radarTitle }
        if let operationalTitle { return operationalTitle }
        if let provisional = input.provisional {
            return provisional.statusLabel ?? L10n.tr("初步排序")
        }
        if input.isUnmappedCurrentModel { return L10n.tr("当前档位尚未参与比较") }
        return input.fallbackTitle
    }

    private static func decisionReason(
        _ input: PresentationInput,
        destinationReason: String?,
        operationalReason: String?
    ) -> String {
        if input.requiresModelSetup {
            return input.hasConfiguredModelCandidates
                ? L10n.tr("开启至少一个模型档位后即可开始首次扫描。")
                : L10n.tr("连接本机登录态或 API，再选择要比较的模型档位。")
        }
        if let radarReason = input.radarReason { return radarReason }
        if let destinationReason { return destinationReason }
        if let operationalReason { return operationalReason }
        if let provisional = input.provisional {
            return provisional.confidenceReason
                ?? L10n.tr("当前仅完成部分题目，补全评测后再做正式切换决策。")
        }
        if input.isUnmappedCurrentModel, let identity = input.detectedCurrentModelIdentity {
            return L10n.tr("已识别 %@。", identity)
        }
        return input.fallbackReason
    }

    private static func displayLabel(_ input: PresentationInput) -> String {
        if input.requiresModelSetup { return L10n.tr("等待设置") }
        if input.availability.shouldHideLegacyOverviewScores { return L10n.tr("等待扫描") }
        if let displayLabel = input.provisional?.displayLabel { return displayLabel }
        if let displayLabel = input.advisorDisplayLabel { return displayLabel }
        guard let best = input.best else {
            return input.availability.isEvidenceUpdating
                ? L10n.tr("结果待定")
                : L10n.tr("暂无结果")
        }
        return best.displayLabel
    }

    private static func identityLabel(_ input: PresentationInput) -> String {
        if input.requiresModelSetup { return L10n.tr("扫描档位") }
        if input.availability.shouldHideLegacyOverviewScores { return L10n.tr("当前题包") }
        if input.availability.isEvidenceUpdating {
            return input.best == nil ? L10n.tr("结果") : L10n.tr("上次结果")
        }
        if input.provisional != nil { return L10n.tr("初步领先") }
        if input.best?.evidenceState == "retained_after_failure" {
            return L10n.tr("上次结果")
        }
        if let advisor = input.advisor {
            switch advisor.decision {
            case "trial_switch": return L10n.tr("试用候选")
            case "compare_first" where advisor.candidateID != nil: return L10n.tr("待比较候选")
            default: return L10n.tr("当前使用")
            }
        }
        switch input.state {
        case .backendUnavailableWithCache, .failedWithFallbackRecommendation,
             .staleRecommendation, .expiredRecommendation:
            return L10n.tr("历史结果")
        case .degradedRecommendation:
            return L10n.tr("当前榜首")
        case .freshRecommendation:
            return input.best?.recommendationOutcome == "wait"
                ? L10n.tr("当前榜首")
                : L10n.tr("推荐")
        default:
            return L10n.tr("结果")
        }
    }

    private static func entryDestinationReason(_ input: PresentationInput) -> String? {
        guard input.entryDestination == input.glanceDestination else { return nil }
        switch input.entryDestination {
        case .overview:
            return nil
        case .runProgress:
            return input.glancePeekLeftSecondary
                ?? L10n.tr("当前进度为 %@。", input.runtimeProgressText)
        case .failureEvidence:
            return input.runtimeLastError
                ?? L10n.tr("本轮没有形成可用结果，请查看失败记录后重试。")
        case .recommendationIssue:
            return input.best?.decisionReason
                ?? L10n.tr("本轮证据不足，暂时不能形成推荐。")
        case .rescan:
            return L10n.tr("当前结果需要重新扫描后更新。")
        case .connectionDiagnostics:
            return input.runtimeLastError
                ?? L10n.tr("本地数据暂时无法刷新，正在自动重试。")
        }
    }

    private static func sourceStatus(
        _ input: PresentationInput
    ) -> (text: String?, isWarning: Bool) {
        switch input.radarDisplaySource {
        case "official_snapshot":
            let age = input.radarReferenceAgeHours.map {
                L10n.tr("（%lld 小时前）", $0)
            } ?? ""
            if input.radarReferenceFreshness == "expired" {
                return (L10n.tr("官网榜单已过期%@", age), true)
            }
            if input.radarReferenceFreshness == "delayed" {
                return (L10n.tr("官网榜单更新延迟%@", age), false)
            }
            if input.referenceDeliveryRefreshStatus == "failed" {
                return (L10n.tr("官网更新失败，正在使用缓存结果"), true)
            }
            switch input.referenceDeliverySource {
            case "http":
                return (
                    freshnessText(
                        source: L10n.tr("官网榜单"),
                        value: input.referencePublishedAt,
                        now: input.now
                    ),
                    false
                )
            case "cache":
                return (L10n.tr("官网榜单%@", age), false)
            default:
                return (L10n.tr("官网榜单%@", age), false)
            }
        case "local_evaluation":
            if let completedAt = input.localCompletedAt {
                return (
                    freshnessText(
                        source: L10n.tr("本机实测"),
                        value: completedAt,
                        now: input.now
                    ),
                    false
                )
            }
            return (L10n.tr("等待完成 %@测试", input.questionRoundLabel), false)
        default:
            if input.radarPortfolioStatus == "no_usage" {
                return (L10n.tr("尚无使用记录"), false)
            }
            return (L10n.tr("当前配置暂无可比较数据"), false)
        }
    }

    private static func freshnessText(source: String, value: String?, now: Date) -> String? {
        guard let value else { return nil }
        let parser = ISO8601DateFormatter()
        parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let date = parser.date(from: value) ?? {
            parser.formatOptions = [.withInternetDateTime]
            return parser.date(from: value)
        }()
        guard let date else {
            guard value.count >= 16 else { return nil }
            let timestamp = String(value.prefix(16)).replacingOccurrences(of: "T", with: " ")
            return L10n.tr("%@ · %@ 更新", source, String(timestamp.suffix(5)))
        }

        let calendar = Calendar.autoupdatingCurrent
        let timeFormatter = DateFormatter()
        timeFormatter.calendar = calendar
        timeFormatter.locale = L10n.locale
        timeFormatter.dateFormat = "HH:mm"
        let time = timeFormatter.string(from: date)

        let timestamp: String
        if calendar.isDate(date, inSameDayAs: now) {
            timestamp = time
        } else if let yesterday = calendar.date(byAdding: .day, value: -1, to: now),
                  calendar.isDate(date, inSameDayAs: yesterday) {
            timestamp = L10n.tr("昨天 %@", time)
        } else {
            let dateFormatter = DateFormatter()
            dateFormatter.calendar = calendar
            dateFormatter.locale = L10n.locale
            let template = calendar.component(.year, from: date)
                == calendar.component(.year, from: now) ? "MMMdjm" : "yMMMdjm"
            dateFormatter.setLocalizedDateFormatFromTemplate(template)
            timestamp = dateFormatter.string(from: date)
        }
        return L10n.tr("%@ · %@ 更新", source, timestamp)
    }

    private static func ingressCandidate(
        _ candidate: IngressCandidateInput,
        connection: IngressConnectionInput,
        connectionName: String
    ) -> IngressCandidatePresentation {
        let currentModelLabel = ModelIdentityPresentation.displayLabel(
            model: candidate.modelID,
            effort: candidate.scanProfile
        )
        return IngressCandidatePresentation(
            id: candidate.id,
            connectionID: connection.id,
            pickerLabel: ingressCandidateLabel(candidate, connection: connection),
            currentModelLabel: currentModelLabel,
            currentModelDetail: "\(connectionName) · "
                + ModelIdentityPresentation.canonicalEffortName(for: candidate.scanProfile),
            isEnabled: candidate.isEnabled
        )
    }

    private static func ingressCandidateLabel(
        _ candidate: IngressCandidateInput,
        connection: IngressConnectionInput
    ) -> String {
        let profile = candidate.scanProfile
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
        if !profile.isEmpty, profile != "default", profile != "codex_default" {
            return ModelIdentityPresentation.displayLabel(
                model: candidate.modelID,
                effort: profile
            )
        }

        let reasoningProfiles: Set<String> = [
            "minimal", "low", "medium", "high", "xhigh", "max",
        ]
        if let family = candidate.familyID?.trimmingCharacters(in: .whitespacesAndNewlines),
           family != candidate.modelID,
           let variant = candidate.variantID?
               .trimmingCharacters(in: .whitespacesAndNewlines)
               .lowercased(),
           reasoningProfiles.contains(variant) {
            return ModelIdentityPresentation.displayLabel(model: family, effort: variant)
        }

        if connection.hasAPIFormat {
            let parts = candidate.modelID.split(separator: "-").map(String.init)
            if parts.count > 1,
               let suffix = parts.last?.lowercased(),
               reasoningProfiles.contains(suffix) {
                let family = parts.dropLast().joined(separator: "-")
                let siblingCount = connection.candidates.filter { sibling in
                    let siblingParts = sibling.modelID.split(separator: "-").map(String.init)
                    guard siblingParts.count > 1,
                          let siblingSuffix = siblingParts.last?.lowercased(),
                          reasoningProfiles.contains(siblingSuffix) else {
                        return false
                    }
                    return siblingParts.dropLast().joined(separator: "-") == family
                }.count
                if siblingCount > 1 {
                    return ModelIdentityPresentation.displayLabel(
                        model: family,
                        effort: suffix
                    )
                }
            }
        }
        return candidate.modelID
    }
}
