import Foundation

enum GlanceState: Equatable {
    case backendUnavailableWithoutCache
    case backendUnavailableWithCache
    case activeScan
    case pausing
    case stopping
    case preparing
    case pausedOrRecoverable
    case finalizing
    case recommendationUnavailable
    case failedWithoutRecommendation
    case failedWithFallbackRecommendation
    case degradedRecommendation
    case freshRecommendation
    case staleRecommendation
    case expiredRecommendation
    case neverScanned
}

enum GlanceTone: Equatable {
    case neutral
    case active
    case success
    case warning
    case failure
}

enum GlanceCompactTextRole: Equatable {
    case identityPrimary
    case identitySecondary
    case status
}

enum GlanceActivity: Equatable {
    case none
    case running
    case finalizing
}

enum GlanceDestination: Equatable {
    case overview
    case runProgress
    case failureEvidence
    case recommendationIssue
    case rescan
    case connectionDiagnostics
}

enum GlancePhase: Equatable {
    case scan
    case repair
}

enum RuntimeLifecycleState: Equatable {
    case idle
    case preparing
    case activeScan
    case pausedRecoverable
    case finalizing
    case failed
    case recommendationUnavailable
}

struct RuntimeSnapshot: Equatable {
    let lifecycleState: RuntimeLifecycleState
    let phase: GlancePhase?
    let progressCompleted: Int
    let progressTotal: Int?
    let lastPhase: GlancePhase?
    let lastPhaseCompleted: Int
    let lastPhaseTotal: Int?
    let stateChangedAt: Date?
    let finalizingStartedAt: Date?
    let updatedAt: Date?
    let leaseExpiresAt: Date?
    let isRecoverable: Bool
    let failureCategory: String?
    let currentTargetShortName: String?
    let activeEvaluationCount: Int
    let oldestActiveEvaluationStartedAt: Date?
    let executionTimeoutSeconds: Int?

    init(
        lifecycleState: RuntimeLifecycleState,
        phase: GlancePhase?,
        progressCompleted: Int,
        progressTotal: Int?,
        lastPhase: GlancePhase?,
        lastPhaseCompleted: Int,
        lastPhaseTotal: Int?,
        stateChangedAt: Date?,
        finalizingStartedAt: Date?,
        updatedAt: Date?,
        leaseExpiresAt: Date?,
        isRecoverable: Bool,
        failureCategory: String?,
        currentTargetShortName: String?,
        activeEvaluationCount: Int = 0,
        oldestActiveEvaluationStartedAt: Date? = nil,
        executionTimeoutSeconds: Int? = nil
    ) {
        self.lifecycleState = lifecycleState
        self.phase = phase
        self.progressCompleted = progressCompleted
        self.progressTotal = progressTotal
        self.lastPhase = lastPhase
        self.lastPhaseCompleted = lastPhaseCompleted
        self.lastPhaseTotal = lastPhaseTotal
        self.stateChangedAt = stateChangedAt
        self.finalizingStartedAt = finalizingStartedAt
        self.updatedAt = updatedAt
        self.leaseExpiresAt = leaseExpiresAt
        self.isRecoverable = isRecoverable
        self.failureCategory = failureCategory
        self.currentTargetShortName = currentTargetShortName
        self.activeEvaluationCount = activeEvaluationCount
        self.oldestActiveEvaluationStartedAt = oldestActiveEvaluationStartedAt
        self.executionTimeoutSeconds = executionTimeoutSeconds
    }
}

enum RuntimeSnapshotState: Equatable {
    case available(RuntimeSnapshot)
    case unavailable(lastError: String?)
}

struct RecommendationSnapshot: Equatable {
    let fullDisplayName: String
    let shortDisplayName: String
    let effortLabel: String
    let recommendationOutcome: String
    let currentDefaultCandidateId: String?
    let recommendedCandidateId: String?
    let currentUsageStatus: String
    let activeSessionCount: Int
    let evidenceState: String
    let runStatus: String
    let scoreText: String
    let recommendationCreatedAt: Date?
    let runCompletedAt: Date?
    let staleAt: Date?
    let expiresAt: Date?

    init(
        fullDisplayName: String,
        shortDisplayName: String,
        effortLabel: String,
        recommendationOutcome: String,
        currentDefaultCandidateId: String?,
        recommendedCandidateId: String?,
        currentUsageStatus: String,
        activeSessionCount: Int,
        evidenceState: String,
        runStatus: String,
        scoreText: String,
        recommendationCreatedAt: Date?,
        runCompletedAt: Date?,
        staleAt: Date?,
        expiresAt: Date?
    ) {
        self.fullDisplayName = fullDisplayName
        self.shortDisplayName = shortDisplayName
        self.effortLabel = effortLabel
        self.recommendationOutcome = recommendationOutcome
        self.currentDefaultCandidateId = currentDefaultCandidateId
        self.recommendedCandidateId = recommendedCandidateId
        self.currentUsageStatus = currentUsageStatus
        self.activeSessionCount = activeSessionCount
        self.evidenceState = evidenceState
        self.runStatus = runStatus
        self.scoreText = scoreText
        self.recommendationCreatedAt = recommendationCreatedAt
        self.runCompletedAt = runCompletedAt
        self.staleAt = staleAt
        self.expiresAt = expiresAt
    }
}

struct GlancePresentation: Equatable {
    let state: GlanceState
    let compactLeft: String
    let compactRight: String
    let compactLeftTextRole: GlanceCompactTextRole
    let compactRightTextRole: GlanceCompactTextRole
    let compactLeadingSymbol: String?
    let compactLeadingSymbolTone: GlanceTone?
    let peekLeftPrimary: String
    let peekLeftSecondary: String?
    let peekRightLabel: String?
    let peekRightValue: String?
    let tone: GlanceTone
    let activity: GlanceActivity
    let accessibilityLabel: String
    let destination: GlanceDestination
}

struct GlanceStateResolver {
    private enum Freshness {
        case fresh
        case staleUsable
        case expired
    }

    static func resolve(
        runtime runtimeState: RuntimeSnapshotState,
        recommendation: RecommendationSnapshot?,
        recommendationStatus: String? = nil,
        hasOfficialReferenceResults: Bool = false,
        now: Date
    ) -> GlancePresentation {
        let recommendationFreshness: Freshness? = recommendation.map {
            freshness(of: $0, status: recommendationStatus, now: now)
        }

        switch runtimeState {
        case .unavailable(let lastError):
            if let recommendation, recommendationFreshness != .expired {
                return cachedBackendPresentation(recommendation, lastError: lastError)
            }
            return presentation(
                state: .backendUnavailableWithoutCache,
                compactLeft: L10n.Glance.dataError,
                compactRight: "—",
                peekLeftPrimary: L10n.Glance.localDataUnavailable,
                peekLeftSecondary: lastError,
                peekRightLabel: nil,
                peekRightValue: L10n.Common.view,
                tone: .failure,
                activity: .none,
                accessibilityLabel: joined(L10n.Glance.localDataUnavailable, lastError),
                destination: .connectionDiagnostics
            )

        case .available(let runtime):
            switch runtime.lifecycleState {
            case .activeScan:
                return activePresentation(
                    state: .activeScan,
                    compactLabel: L10n.Glance.scanning,
                    peekLabel: L10n.Glance.scanning,
                    progressLabel: L10n.Glance.scanProgress,
                    runtime: runtime,
                    recommendation: usable(recommendation, freshness: recommendationFreshness),
                    now: now
                )
            case .preparing:
                return presentation(
                    state: .preparing,
                    compactLeft: L10n.Glance.prepare,
                    compactRight: "—",
                    peekLeftPrimary: L10n.Glance.preparingScan,
                    peekLeftSecondary: recommendation.flatMap { usable($0, freshness: recommendationFreshness) }.map(previousRecommendationText),
                    peekRightLabel: L10n.Glance.runtimeStatus,
                    peekRightValue: L10n.Glance.preparing,
                    tone: .active,
                    activity: .running,
                    accessibilityLabel: L10n.Glance.preparingAccessibility,
                    destination: .runProgress
                )
            case .pausedRecoverable:
                return pausedPresentation(runtime)
            case .finalizing:
                return finalizingPresentation(runtime, recommendation: usable(recommendation, freshness: recommendationFreshness), now: now)
            case .recommendationUnavailable:
                return presentation(
                    state: .recommendationUnavailable,
                    compactLeft: L10n.Glance.undecided,
                    compactRight: "—",
                    peekLeftPrimary: L10n.Glance.resultUndecided,
                    peekLeftSecondary: runtime.failureCategory,
                    peekRightLabel: nil,
                    peekRightValue: L10n.Common.view,
                    tone: .warning,
                    activity: .none,
                    accessibilityLabel: joined(L10n.Glance.resultUndecided, runtime.failureCategory),
                    destination: .recommendationIssue
                )
            case .failed:
                if let fallback = usable(recommendation, freshness: recommendationFreshness) {
                    return fallbackRecommendationPresentation(fallback)
                }
                return presentation(
                    state: .failedWithoutRecommendation,
                    compactLeft: L10n.Glance.failed,
                    compactRight: "—",
                    peekLeftPrimary: L10n.Glance.scanFailed,
                    peekLeftSecondary: runtime.failureCategory,
                    peekRightLabel: nil,
                    peekRightValue: L10n.Common.view,
                    tone: .failure,
                    activity: .none,
                    accessibilityLabel: joined(L10n.Glance.scanFailed, runtime.failureCategory),
                    destination: .failureEvidence
                )
            case .idle:
                break
            }
        }

        if recommendationStatus == "stale" {
            return presentation(
                state: .expiredRecommendation,
                compactLeft: L10n.Glance.expired,
                compactRight: "—",
                peekLeftPrimary: L10n.Glance.recommendationExpired,
                peekLeftSecondary: recommendation?.runCompletedAt.map {
                    L10n.Glance.lastCompleted(timestamp($0))
                },
                peekRightLabel: nil,
                peekRightValue: L10n.Common.rescan,
                tone: .warning,
                activity: .none,
                accessibilityLabel: L10n.text(
                    "glance.expired_accessibility",
                    fallback: "推荐已过期，展开后可重新扫描"
                ),
                destination: .rescan
            )
        }

        if recommendationStatus == "no_usage" {
            return unavailableRecommendationPresentation(
                compactLeft: L10n.Glance.needsBaseline,
                primary: L10n.text(
                    "glance.current_usage_unavailable",
                    fallback: "尚未识别当前使用模型"
                ),
                secondary: L10n.text(
                    "glance.current_usage_unavailable_detail",
                    fallback: "产生一次真实会话后即可建立对比基线"
                )
            )
        }

        if recommendationStatus == "needs_test" {
            if hasOfficialReferenceResults {
                let title = L10n.tr("远端榜单可用")
                let detail = L10n.tr(
                    "当前档位暂无同口径远端结论；榜单仍可浏览，本机实测仅用于校准当前路线。"
                )
                return presentation(
                    state: .recommendationUnavailable,
                    compactLeft: L10n.tr("远端榜单"),
                    compactRight: "—",
                    peekLeftPrimary: title,
                    peekLeftSecondary: detail,
                    peekRightLabel: nil,
                    peekRightValue: L10n.Common.view,
                    tone: .neutral,
                    activity: .none,
                    accessibilityLabel: joined(title, detail),
                    destination: .overview
                )
            }
            return unavailableRecommendationPresentation(
                compactLeft: L10n.Glance.pendingComparison,
                primary: L10n.text(
                    "glance.comparison_evidence_needed",
                    fallback: "需要补充可比较实测"
                ),
                secondary: L10n.text(
                    "glance.comparison_evidence_needed_detail",
                    fallback: "当前模型或候选档位尚无同口径结果"
                )
            )
        }

        guard let recommendation else {
            return presentation(
                state: .neverScanned,
                compactLeft: L10n.Glance.pendingScan,
                compactRight: "—",
                peekLeftPrimary: L10n.Glance.neverScanned,
                peekLeftSecondary: nil,
                peekRightLabel: nil,
                peekRightValue: L10n.Common.rescan,
                tone: .neutral,
                activity: .none,
                accessibilityLabel: L10n.text(
                    "glance.never_scanned_accessibility",
                    fallback: "尚未扫描，展开后可开始扫描"
                ),
                destination: .rescan
            )
        }

        switch recommendationFreshness ?? .expired {
        case .fresh:
            if isFallbackRecommendation(recommendation) {
                return fallbackRecommendationPresentation(recommendation)
            }
            if recommendation.runStatus == "degraded" {
                return degradedRecommendationPresentation(recommendation)
            }
            return recommendationPresentation(recommendation, state: .freshRecommendation, tone: .neutral)
        case .staleUsable:
            if isFallbackRecommendation(recommendation) {
                return fallbackRecommendationPresentation(recommendation)
            }
            if recommendation.runStatus == "degraded" {
                return degradedRecommendationPresentation(recommendation)
            }
            return recommendationPresentation(recommendation, state: .staleRecommendation, tone: .warning)
        case .expired:
            return presentation(
                state: .expiredRecommendation,
                compactLeft: L10n.Glance.expired,
                compactRight: "—",
                peekLeftPrimary: L10n.Glance.recommendationExpired,
                peekLeftSecondary: recommendation.runCompletedAt.map {
                    L10n.Glance.lastCompleted(timestamp($0))
                },
                peekRightLabel: nil,
                peekRightValue: L10n.Common.rescan,
                tone: .warning,
                activity: .none,
                accessibilityLabel: L10n.text(
                    "glance.expired_accessibility",
                    fallback: "推荐已过期，展开后可重新扫描"
                ),
                destination: .rescan
            )
        }
    }

    private static func unavailableRecommendationPresentation(
        compactLeft: String,
        primary: String,
        secondary: String
    ) -> GlancePresentation {
        presentation(
            state: .recommendationUnavailable,
            compactLeft: compactLeft,
            compactRight: "—",
            peekLeftPrimary: primary,
            peekLeftSecondary: secondary,
            peekRightLabel: nil,
            peekRightValue: L10n.Common.view,
            tone: .neutral,
            activity: .none,
            accessibilityLabel: joined(primary, secondary),
            destination: .recommendationIssue
        )
    }

    static func stoppingPresentation(runtime runtimeState: RuntimeSnapshotState) -> GlancePresentation {
        let runtime: RuntimeSnapshot?
        switch runtimeState {
        case .available(let snapshot):
            runtime = snapshot
        case .unavailable:
            runtime = nil
        }
        let progressText = runtime.map { progress($0.progressCompleted, $0.progressTotal) } ?? "—"
        return presentation(
            state: .stopping,
            compactLeft: L10n.Glance.stopping,
            compactRight: progressText,
            peekLeftPrimary: L10n.Glance.stoppingScan,
            peekLeftSecondary: L10n.Glance.stoppingDetail,
            peekRightLabel: L10n.Glance.progressBeforeStop,
            peekRightValue: progressText,
            tone: .warning,
            activity: .finalizing,
            accessibilityLabel: L10n.format(
                "glance.stopping_accessibility",
                fallback: "正在停止扫描，停止前进度 %@",
                progressText
            ),
            destination: .runProgress
        )
    }

    static func pausingPresentation(runtime runtimeState: RuntimeSnapshotState) -> GlancePresentation {
        let runtime: RuntimeSnapshot?
        switch runtimeState {
        case .available(let snapshot):
            runtime = snapshot
        case .unavailable:
            runtime = nil
        }
        let progressText = runtime.map { progress($0.progressCompleted, $0.progressTotal) } ?? "—"
        return presentation(
            state: .pausing,
            compactLeft: L10n.Glance.pausing,
            compactRight: progressText,
            peekLeftPrimary: L10n.Glance.pausingScan,
            peekLeftSecondary: L10n.Glance.pausingDetail,
            peekRightLabel: L10n.Glance.progressBeforePause,
            peekRightValue: progressText,
            tone: .warning,
            activity: .finalizing,
            accessibilityLabel: L10n.format(
                "glance.pausing_accessibility",
                fallback: "正在暂停扫描，暂停前进度 %@",
                progressText
            ),
            destination: .runProgress
        )
    }

    private static func freshness(
        of recommendation: RecommendationSnapshot,
        status: String?,
        now: Date
    ) -> Freshness {
        if status == "stale" { return .expired }
        if status == "recommend" || status == "keep" { return .fresh }
        guard recommendation.recommendationCreatedAt != nil,
              let staleAt = recommendation.staleAt,
              let expiresAt = recommendation.expiresAt else {
            return .expired
        }
        if now >= expiresAt { return .expired }
        if now >= staleAt { return .staleUsable }
        return .fresh
    }

    private static func usable(
        _ recommendation: RecommendationSnapshot?,
        freshness: Freshness?
    ) -> RecommendationSnapshot? {
        freshness == .expired ? nil : recommendation
    }

    private static func progress(_ completed: Int, _ total: Int?) -> String {
        "\(max(0, completed))/\(total.map { String(max(0, $0)) } ?? "—")"
    }

    static func activeEvaluationTimingText(
        _ runtime: RuntimeSnapshot,
        now: Date
    ) -> String? {
        guard runtime.lifecycleState == .activeScan,
              let total = runtime.progressTotal,
              total > runtime.progressCompleted,
              runtime.activeEvaluationCount > 0,
              total - runtime.progressCompleted <= runtime.activeEvaluationCount,
              let startedAt = runtime.oldestActiveEvaluationStartedAt,
              let timeoutSeconds = runtime.executionTimeoutSeconds,
              timeoutSeconds > 0 else {
            return nil
        }

        let elapsedSeconds = max(0, Int(now.timeIntervalSince(startedAt)))
        guard elapsedSeconds >= 60 else { return nil }
        let remainingCount = total - runtime.progressCompleted
        let elapsedText = shortDuration(elapsedSeconds, roundingUp: false)
        let remainingSeconds = timeoutSeconds - elapsedSeconds
        if remainingSeconds > 0 {
            return "最后 \(remainingCount) 项仍在运行 · 最慢已 \(elapsedText) · 距超时 \(shortDuration(remainingSeconds, roundingUp: true))"
        }
        return "最后 \(remainingCount) 项仍在收尾 · 最慢已达到 \(shortDuration(timeoutSeconds, roundingUp: false)) 超时线"
    }

    private static func shortDuration(_ seconds: Int, roundingUp: Bool) -> String {
        let clamped = max(0, seconds)
        if clamped < 60 {
            return "\(clamped) 秒"
        }
        let minutes = roundingUp ? (clamped + 59) / 60 : clamped / 60
        return "\(minutes) 分钟"
    }

    private static func activePresentation(
        state: GlanceState,
        compactLabel: String,
        peekLabel: String,
        progressLabel: String,
        runtime: RuntimeSnapshot,
        recommendation: RecommendationSnapshot?,
        now: Date
    ) -> GlancePresentation {
        let progressText = progress(runtime.progressCompleted, runtime.progressTotal)
        let secondary = activeEvaluationTimingText(runtime, now: now)
            ?? recommendation.map(previousRecommendationText)
            ?? runtime.currentTargetShortName.map(L10n.Glance.testing)
        return presentation(
            state: state,
            compactLeft: compactLabel,
            compactRight: progressText,
            compactLeftTextRole: .status,
            compactRightTextRole: .status,
            peekLeftPrimary: peekLabel,
            peekLeftSecondary: secondary,
            peekRightLabel: progressLabel,
            peekRightValue: progressText,
            tone: .active,
            activity: .running,
            accessibilityLabel: "\(peekLabel)，\(progressLabel) \(progressText)" + (secondary.map { "，\($0)" } ?? ""),
            destination: .runProgress
        )
    }

    private static func pausedPresentation(_ runtime: RuntimeSnapshot) -> GlancePresentation {
        let progressText = progress(runtime.progressCompleted, runtime.progressTotal)
        return presentation(
            state: .pausedOrRecoverable,
            compactLeft: L10n.Glance.pendingResume,
            compactRight: progressText,
            peekLeftPrimary: L10n.Glance.scanPendingResume,
            peekLeftSecondary: runtime.currentTargetShortName,
            peekRightLabel: runtime.phase == .repair
                ? L10n.Glance.repairProgress
                : L10n.Glance.scanProgress,
            peekRightValue: progressText,
            tone: .warning,
            activity: .none,
            accessibilityLabel: L10n.format(
                "glance.pending_resume_accessibility",
                fallback: "扫描待继续，当前进度 %@",
                progressText
            ),
            destination: .runProgress
        )
    }

    private static func finalizingPresentation(
        _ runtime: RuntimeSnapshot,
        recommendation: RecommendationSnapshot?,
        now: Date
    ) -> GlancePresentation {
        let isWithinDelay = runtime.finalizingStartedAt.map { now < $0.addingTimeInterval(0.3) } ?? false
        let phase = runtime.lastPhase ?? runtime.phase ?? .scan
        let progressText = progress(runtime.lastPhaseCompleted, runtime.lastPhaseTotal)
        let compactLabel = phase == .repair ? L10n.Glance.repair : L10n.Glance.scan
        return presentation(
            state: .finalizing,
            compactLeft: isWithinDelay ? compactLabel : L10n.Glance.finalizing,
            compactRight: isWithinDelay ? progressText : "—",
            peekLeftPrimary: L10n.Glance.finalizingResults,
            peekLeftSecondary: recommendation.map(previousRecommendationText),
            peekRightLabel: isWithinDelay
                ? (phase == .repair ? L10n.Glance.repairProgress : L10n.Glance.scanProgress)
                : L10n.Glance.runtimeStatus,
            peekRightValue: isWithinDelay ? progressText : L10n.Glance.finalizingValue,
            tone: .active,
            activity: .finalizing,
            accessibilityLabel: isWithinDelay
                ? L10n.format(
                    "glance.finalizing_progress_accessibility",
                    fallback: "%@完成，进度 %@，正在整理结果",
                    compactLabel,
                    progressText
                )
                : L10n.text(
                    "glance.finalizing_accessibility",
                    fallback: "正在整理扫描结果"
                ),
            destination: .runProgress
        )
    }

    private static func recommendationPresentation(
        _ recommendation: RecommendationSnapshot,
        state: GlanceState,
        tone: GlanceTone
    ) -> GlancePresentation {
        let isMixedUsage = recommendation.currentUsageStatus == "active_mixed"
        let isUnmappedUsage = recommendation.currentUsageStatus == "unmapped"
        let isWaitOutcome = recommendation.recommendationOutcome == "wait"
        let isReoptimizeOutcome = recommendation.recommendationOutcome == "reoptimize"
        let currentMatchesRecommendation = recommendation.currentDefaultCandidateId != nil
            && recommendation.currentDefaultCandidateId == recommendation.recommendedCandidateId
        let evidence = L10n.format(
            "glance.evidence_score",
            fallback: "综合总分 %@",
            recommendation.scoreText
        )
        let secondary: String
        if state == .staleRecommendation, let runCompletedAt = recommendation.runCompletedAt {
            secondary = L10n.Glance.lastUpdated(timestamp(runCompletedAt))
        } else {
            secondary = evidence
        }
        let compactLeadingSymbol: String?
        let compactLeadingSymbolTone: GlanceTone?
        let accessibilityPrefix: String
        let decisionState = isMixedUsage
            ? "mixed"
            : (isUnmappedUsage ? "unmapped" : recommendation.recommendationOutcome)
        switch decisionState {
        case "mixed":
            compactLeadingSymbol = nil
            compactLeadingSymbolTone = nil
            accessibilityPrefix = L10n.text(
                "glance.accessibility.mixed_prefix",
                fallback: "检测到多个活动会话，当前推荐模型"
            )
        case "switch":
            compactLeadingSymbol = "arrow.right.circle.fill"
            compactLeadingSymbolTone = .warning
            accessibilityPrefix = L10n.text(
                "glance.accessibility.switch_prefix",
                fallback: "建议切换到推荐模型"
            )
        case "keep":
            compactLeadingSymbol = "checkmark.circle.fill"
            compactLeadingSymbolTone = .active
            accessibilityPrefix = L10n.text(
                "glance.accessibility.keep_prefix",
                fallback: "当前模型无需切换，推荐模型"
            )
        case "adopted":
            compactLeadingSymbol = "checkmark.circle.fill"
            compactLeadingSymbolTone = .active
            accessibilityPrefix = L10n.text(
                "glance.accessibility.adopted_prefix",
                fallback: "已采用建议，当前模型"
            )
        case "reoptimize":
            compactLeadingSymbol = "arrow.triangle.2.circlepath.circle.fill"
            compactLeadingSymbolTone = .warning
            accessibilityPrefix = L10n.text(
                "glance.accessibility.reoptimize_prefix",
                fallback: "当前档位需要重新评估，当前模型"
            )
        case "unmapped":
            compactLeadingSymbol = "exclamationmark.circle.fill"
            compactLeadingSymbolTone = .warning
            accessibilityPrefix = L10n.text(
                "glance.accessibility.unmapped_prefix",
                fallback: "当前在用档位尚未参与比较，当前推荐模型"
            )
        default:
            if currentMatchesRecommendation {
                compactLeadingSymbol = "checkmark.circle.fill"
                compactLeadingSymbolTone = .active
                accessibilityPrefix = L10n.text(
                    "glance.accessibility.current_matches_prefix",
                    fallback: "当前在用模型与当前榜首一致，推荐模型"
                )
            } else if recommendation.currentDefaultCandidateId != nil {
                compactLeadingSymbol = "arrow.right.circle.fill"
                compactLeadingSymbolTone = .warning
                accessibilityPrefix = L10n.text(
                    "glance.accessibility.current_differs_prefix",
                    fallback: "当前在用模型与当前榜首不同，推荐模型"
                )
            } else {
                compactLeadingSymbol = nil
                compactLeadingSymbolTone = nil
                accessibilityPrefix = L10n.text(
                    "glance.accessibility.recommended_model_prefix",
                    fallback: "推荐模型"
                )
            }
        }
        let resolvedSecondary: String
        if isMixedUsage {
            resolvedSecondary = L10n.Glance.mixedSessions(recommendation.activeSessionCount)
        } else if isUnmappedUsage {
            resolvedSecondary = L10n.Glance.currentEffortUncompared
        } else {
            resolvedSecondary = secondary
        }
        let resolvedTone: GlanceTone = isMixedUsage || isUnmappedUsage || isWaitOutcome || isReoptimizeOutcome ? .warning : tone
        return presentation(
            state: state,
            compactLeft: compactIdentity(recommendation),
            compactRight: isMixedUsage
                ? L10n.Glance.multipleSessions
                : recommendation.effortLabel,
            compactLeftTextRole: .identityPrimary,
            compactRightTextRole: isMixedUsage ? .status : .identitySecondary,
            compactLeadingSymbol: compactLeadingSymbol,
            compactLeadingSymbolTone: compactLeadingSymbolTone,
            peekLeftPrimary: recommendation.fullDisplayName,
            peekLeftSecondary: resolvedSecondary,
            peekRightLabel: isMixedUsage || isUnmappedUsage
                ? L10n.Glance.currentStatus
                : ((isWaitOutcome || isReoptimizeOutcome)
                    ? L10n.Glance.leadingEffort
                    : L10n.Glance.recommendedEffort),
            peekRightValue: isMixedUsage
                ? L10n.Glance.needsBaseline
                : (isUnmappedUsage ? L10n.Glance.pendingComparison : recommendation.effortLabel),
            tone: resolvedTone,
            activity: .none,
            accessibilityLabel: isMixedUsage
                ? L10n.format(
                    "glance.accessibility.mixed_recommendation",
                    fallback: "多个活动会话正在使用不同模型，推荐模型 %@，展开后可指定比较基准",
                    recommendation.fullDisplayName
                )
                : L10n.format(
                    "glance.accessibility.recommendation",
                    fallback: "%@ %@，建议档位 %@，%@",
                    accessibilityPrefix,
                    recommendation.fullDisplayName,
                    recommendation.effortLabel,
                    evidence
                ),
            destination: .overview
        )
    }

    private static func cachedBackendPresentation(
        _ recommendation: RecommendationSnapshot,
        lastError: String?
    ) -> GlancePresentation {
        presentation(
            state: .backendUnavailableWithCache,
            compactLeft: compactIdentity(recommendation),
            compactRight: recommendation.effortLabel,
            compactLeftTextRole: .identityPrimary,
            compactRightTextRole: .identitySecondary,
            compactLeadingSymbol: "exclamationmark.triangle.fill",
            compactLeadingSymbolTone: .warning,
            peekLeftPrimary: recommendation.fullDisplayName,
            peekLeftSecondary: L10n.Glance.cacheFallback,
            peekRightLabel: L10n.Glance.recommendedEffort,
            peekRightValue: recommendation.effortLabel,
            tone: .warning,
            activity: .none,
            accessibilityLabel: joined(
                L10n.format(
                    "glance.accessibility.cache_fallback",
                    fallback: "数据暂未更新，显示上次可用推荐 %@，档位 %@",
                    recommendation.fullDisplayName,
                    recommendation.effortLabel
                ),
                lastError
            ),
            destination: .connectionDiagnostics
        )
    }

    private static func fallbackRecommendationPresentation(
        _ recommendation: RecommendationSnapshot
    ) -> GlancePresentation {
        presentation(
            state: .failedWithFallbackRecommendation,
            compactLeft: compactIdentity(recommendation),
            compactRight: recommendation.effortLabel,
            compactLeftTextRole: .identityPrimary,
            compactRightTextRole: .identitySecondary,
            compactLeadingSymbol: "exclamationmark.triangle.fill",
            compactLeadingSymbolTone: .warning,
            peekLeftPrimary: recommendation.fullDisplayName,
            peekLeftSecondary: L10n.Glance.failureFallback,
            peekRightLabel: L10n.Glance.previousEffort,
            peekRightValue: recommendation.effortLabel,
            tone: .warning,
            activity: .none,
            accessibilityLabel: L10n.format(
                "glance.accessibility.failure_fallback",
                fallback: "本轮扫描失败，沿用上次推荐 %@，档位 %@",
                recommendation.fullDisplayName,
                recommendation.effortLabel
            ),
            destination: .overview
        )
    }

    private static func degradedRecommendationPresentation(
        _ recommendation: RecommendationSnapshot
    ) -> GlancePresentation {
        presentation(
            state: .degradedRecommendation,
            compactLeft: compactIdentity(recommendation),
            compactRight: recommendation.effortLabel,
            compactLeftTextRole: .identityPrimary,
            compactRightTextRole: .identitySecondary,
            compactLeadingSymbol: "exclamationmark.triangle.fill",
            compactLeadingSymbolTone: .warning,
            peekLeftPrimary: recommendation.fullDisplayName,
            peekLeftSecondary: L10n.Glance.degraded,
            peekRightLabel: L10n.Glance.currentLeader,
            peekRightValue: recommendation.effortLabel,
            tone: .warning,
            activity: .none,
            accessibilityLabel: L10n.format(
                "glance.accessibility.degraded",
                fallback: "本轮部分结果异常，当前榜首 %@，档位 %@",
                recommendation.fullDisplayName,
                recommendation.effortLabel
            ),
            destination: .overview
        )
    }

    private static func isFallbackRecommendation(_ recommendation: RecommendationSnapshot) -> Bool {
        recommendation.evidenceState == "retained_after_failure"
            || recommendation.recommendationOutcome == "retain_after_failure"
            || ["partial", "failed", "stopped"].contains(recommendation.runStatus)
    }

    private static func compactIdentity(_ recommendation: RecommendationSnapshot) -> String {
        recommendation.shortDisplayName
    }

    private static func previousRecommendationText(_ recommendation: RecommendationSnapshot) -> String {
        L10n.Glance.previousRecommendation(
            model: recommendation.shortDisplayName,
            effort: recommendation.effortLabel
        )
    }

    private static func timestamp(_ date: Date) -> String {
        LocalizedFormatters.shortDateTime(date)
    }

    private static func joined(_ primary: String, _ secondary: String?) -> String {
        secondary.map {
            L10n.format("common.joined_detail", fallback: "%@，%@", primary, $0)
        } ?? primary
    }

    private static func presentation(
        state: GlanceState,
        compactLeft: String,
        compactRight: String,
        compactLeftTextRole: GlanceCompactTextRole = .status,
        compactRightTextRole: GlanceCompactTextRole = .status,
        compactLeadingSymbol: String? = nil,
        compactLeadingSymbolTone: GlanceTone? = nil,
        peekLeftPrimary: String,
        peekLeftSecondary: String?,
        peekRightLabel: String?,
        peekRightValue: String?,
        tone: GlanceTone,
        activity: GlanceActivity,
        accessibilityLabel: String,
        destination: GlanceDestination
    ) -> GlancePresentation {
        GlancePresentation(
            state: state,
            compactLeft: compactLeft,
            compactRight: compactRight,
            compactLeftTextRole: compactLeftTextRole,
            compactRightTextRole: compactRightTextRole,
            compactLeadingSymbol: compactLeadingSymbol,
            compactLeadingSymbolTone: compactLeadingSymbolTone,
            peekLeftPrimary: peekLeftPrimary,
            peekLeftSecondary: peekLeftSecondary,
            peekRightLabel: peekRightLabel,
            peekRightValue: peekRightValue,
            tone: tone,
            activity: activity,
            accessibilityLabel: accessibilityLabel,
            destination: destination
        )
    }
}
