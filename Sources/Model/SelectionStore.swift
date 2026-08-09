import Foundation
import AppKit

struct ScheduledRunStatus {
    let date: Date?
    let absoluteText: String
    let relativeText: String
    let candidateCount: Int
    let questionCount: Int
    let reason: String
}

struct SnapshotRefreshIssue {
    let message: String
    let detail: String
}

enum ScanConflictPresentation {
    case expanded
    case settings
    case background
}

@MainActor
final class AppSessionStore: ObservableObject {
    static let shared = AppSessionStore()
    private static let preferredManualEvaluationProfileKey =
        "preferred_manual_evaluation_profile_id"

    @Published private(set) var snapshot: BridgeSnapshot?
    @Published var isExpanded = false
    @Published var transientMessage: String?
    @Published private(set) var scanConflictMessage: String?
    @Published private(set) var scanConflictPresentation: ScanConflictPresentation?
    @Published private(set) var repairFailureMessage: String?
    @Published private(set) var pendingScanControlAction: String?
    @Published private(set) var snapshotRefreshIssue: SnapshotRefreshIssue?
    @Published private(set) var isReferenceSnapshotRefreshInFlight = false
    @Published private(set) var referenceSnapshotRefreshFeedbackStatus: String?
    @Published private(set) var glancePresentation: GlancePresentation
    private var expandedDestination: GlanceDestination?
    @Published private(set) var isGlanceActuallyVisible = true
    @Published private(set) var preferredManualEvaluationProfileID: String?

    private let bridge: NativeBridgeClient
    private let commandGateway: any AppSessionBridgeGatewayProtocol
    private let timersEnabled: Bool
    private let clientSessionID: String
    private lazy var notificationEngine = RecommendationNotificationEngine.shared
    private var scheduledScanTimer: Timer?
    private var scheduledScanFireDate: Date?
    private var scheduledScanFingerprint: String?
    private var glanceBoundaryTimer: Timer?
    private var currentModelRefreshTimer: Timer?
    private var startupMaintenanceRetryTask: Task<Void, Never>?
    private var startupMaintenanceRetryScheduled = false
    private var isSnapshotReloadInFlight = false
    private var isSnapshotReloadPending = false
    private var pendingSnapshotReloadAutoResumeOnInterruption = false
    private var pendingSnapshotReloadAllowsStartupMaintenance = false
    private var pendingSnapshotReloadAllowsReferenceRefresh = false
    private var pendingSnapshotReloadForcesReferenceRefresh = false
    private var snapshotGeneration: UInt = 0
    private let activeRuntimeRefreshInterval: TimeInterval = 3
    private let idleCurrentModelRefreshInterval: TimeInterval = 30
    private let snapshotRefreshWarningThreshold = 2
    private var consecutiveSnapshotRefreshFailures = 0
    private var lastSuccessfulSnapshotRefreshAt: Date?
    private var lastCompletedScanAt: Date?
    private var activeBridgeOperationID: UUID?
    private var pendingScanPlanPreviewID: UUID?
    private var pendingScanControlRequestID: UUID?
    private var pendingScanControlRequestedAt: Date?
    private var activeScanConflictPresentation: ScanConflictPresentation = .expanded
    private var startupLoadCoordinator = StartupLoadCoordinator()
    private var referenceSnapshotRefreshPolicy: ReferenceSnapshotRefreshPolicy
    private var referenceSnapshotRefreshFeedbackDismissTask: Task<Void, Never>?
    private var stableActiveModelSessionKeys: [String] = []
    private static let startupMaintenanceRetryDelayNanoseconds: UInt64 = 250_000_000

    init(
        bridge: NativeBridgeClient = NativeBridgeClient(),
        commandGateway: (any AppSessionBridgeGatewayProtocol)? = nil,
        timersEnabled: Bool = true,
        clientSessionID: String = UUID().uuidString,
        initialSnapshot: BridgeSnapshot? = nil,
        referenceSnapshotRefreshPolicy: ReferenceSnapshotRefreshPolicy =
            ReferenceSnapshotRefreshPolicy()
    ) {
        self.bridge = bridge
        self.commandGateway = commandGateway ?? AppSessionBridgeGateway()
        self.timersEnabled = timersEnabled
        self.clientSessionID = clientSessionID
        self.referenceSnapshotRefreshPolicy = referenceSnapshotRefreshPolicy
        preferredManualEvaluationProfileID = UserDefaults.standard.string(
            forKey: Self.preferredManualEvaluationProfileKey
        )
        glancePresentation = GlanceStateResolver.resolve(
            runtime: .available(Self.idleRuntimeSnapshot),
            recommendation: nil,
            now: Date()
        )
        DebugLog.write("AppSessionStore.init")
        if let initialSnapshot {
            applySnapshot(initialSnapshot)
        } else if timersEnabled {
            armCurrentModelRefreshTimer()
        }
    }

    deinit {
        scheduledScanTimer?.invalidate()
        glanceBoundaryTimer?.invalidate()
        currentModelRefreshTimer?.invalidate()
        startupMaintenanceRetryTask?.cancel()
    }

    var bestLabel: String {
        if snapshot?.runtime.isRunning == true {
            return "扫描中"
        }
        return snapshot?.dashboard.bestCombination?.label ?? "暂无推荐"
    }

    var recommendationReason: String {
        if let runtime = snapshot?.runtime, runtime.isRunning {
            return "当前扫描项 \(runtime.currentTarget ?? "-") · \(displayProgressText(for: runtime))"
        }
        guard let best = snapshot?.dashboard.bestCombination else { return "等待首次扫描。" }
        return "原因：\(best.confidenceReason)"
    }

    var progressText: String {
        guard let runtime = snapshot?.runtime else { return "未开始扫描" }
        if runtime.isRunning {
            return "正在扫描 \(displayProgressText(for: runtime)) · \(runtime.currentTarget ?? "-")"
        }
        if runtime.hasResumableRun {
            return "可继续 \(displayProgressText(for: runtime))"
        }
        return "上次扫描完成 \(runtime.lastRunCount) 个模型"
    }

    var scanActivityText: String {
        OperationalStatePresenter.scanActivityText(
            OperationalStatePresenter.ScanActivityInput(
                isRunning: snapshot?.runtime.isRunning == true,
                currentCompletedAt: snapshot?.dashboard.runMetadata.completedAt
                    .flatMap { bridgeDate(from: $0) },
                lastCompletedAt: lastCompletedScanAt,
                now: Date()
            )
        )
    }

    var runtimeProgressText: String {
        guard let runtime = snapshot?.runtime else { return "待扫描" }
        return displayProgressText(for: runtime)
    }

    var runtimeProgressCounterText: String {
        guard let runtime = snapshot?.runtime else { return "待扫" }
        return displayProgressCounter(for: runtime)
    }

    var activeEvaluationTimingText: String? {
        guard case .available(let runtime) = runtimeSnapshotState() else { return nil }
        return GlanceStateResolver.activeEvaluationTimingText(runtime, now: Date())
    }

    var isScanOperationActive: Bool {
        if activeBridgeOperationID != nil || pendingScanControlAction != nil {
            return true
        }
        switch glancePresentation.state {
        case .preparing, .activeScan, .pausing, .stopping, .finalizing:
            return true
        default:
            return false
        }
    }

    var canRequestScanPause: Bool {
        pendingScanControlAction == nil && glancePresentation.state == .activeScan
    }

    var canRequestScanStop: Bool {
        guard pendingScanControlAction == nil else { return false }
        switch glancePresentation.state {
        case .preparing, .activeScan, .pausing:
            return true
        default:
            return false
        }
    }

    var leaderboard: [BridgeLeaderboardEntry] {
        snapshot?.dashboard.leaderboard ?? []
    }

    var radarDashboard: BridgeDashboard? {
        guard let snapshot else { return nil }
        if RadarPresenter.shouldUseCurrentDashboard(
            runID: snapshot.dashboard.runMetadata.runId,
            evidenceSourceSnapshotID: radarEvidence?.sourceSnapshotId
        ) {
            return snapshot.dashboard
        }
        return snapshot.stableEvidenceDashboard
            ?? snapshot.stableDashboard
            ?? snapshot.dashboard
    }

    var radarPortfolio: BridgeRecommendationPortfolioV2? {
        snapshot?.recommendationPortfolioV2
    }

    var radarEvidence: BridgeAdvisorV2Evidence? {
        radarPortfolio?.representativeEvidence ?? snapshot?.advisorV2Evidence
    }

    var radarRepresentativeDecision: BridgeRecommendationDecisionV2? {
        radarPortfolio?.representativeDecision
    }

    var radarRepresentativeConfigurationID: String? {
        radarPortfolio?.representativeConfigurationId
            ?? radarEvidence?.currentModelConfigurationId
            ?? snapshot?.config.recommendation.effectiveCurrentCandidateId
    }

    var radarSelectedSourceMode: String {
        guard let configurationID = radarRepresentativeConfigurationID else {
            let sourceMode = radarPortfolio?.sourceMode ?? "auto"
            return sourceMode == "official_snapshot" && referenceSnapshotLeaderboardItems.isEmpty
                ? "auto"
                : sourceMode
        }
        let sourceMode = snapshot?.config.recommendation.sourceModeByConfigurationId[configurationID]
            ?? radarPortfolio?.sourceModeByConfigurationId[configurationID]
            ?? radarPortfolio?.sourceMode
            ?? "auto"
        return sourceMode == "official_snapshot" && referenceSnapshotLeaderboardItems.isEmpty
            ? "auto"
            : sourceMode
    }

    private var radarAuthoritativeDataSource: String? {
        switch radarSelectedSourceMode {
        case "official_snapshot":
            return radarHasResults(for: "official_snapshot")
                ? "official_snapshot"
                : nil
        case "local_evaluation":
            return "local_evaluation"
        default:
            if let resolved = radarPortfolio?.resolvedDataSource,
               radarHasResults(for: resolved) {
                return resolved
            }
            if let resolved = radarEvidence?.resolvedDataSource,
               radarHasResults(for: resolved) {
                return resolved
            }
            if !localEvaluationLeaderboardItems.isEmpty {
                return "local_evaluation"
            }
            if !referenceSnapshotLeaderboardItems.isEmpty {
                return "official_snapshot"
            }
            return nil
        }
    }

    private func radarHasResults(for source: String) -> Bool {
        switch source {
        case "local_evaluation":
            return !localEvaluationLeaderboardItems.isEmpty
        case "official_snapshot":
            return !referenceSnapshotLeaderboardItems.isEmpty
        default:
            return false
        }
    }

    var radarDisplaySource: String? {
        radarAuthoritativeDataSource
    }

    var radarLeaderboardItems: [RadarLeaderboardItem] {
        switch radarDisplaySource {
        case "official_snapshot":
            return referenceSnapshotLeaderboardItems
        case "local_evaluation":
            return localEvaluationLeaderboardItems
        default:
            return []
        }
    }

    var radarDisplayFreshness: String? {
        switch radarDisplaySource {
        case "official_snapshot":
            return radarReferenceFreshness
        case "local_evaluation":
            if radarPortfolio?.status == "stale" {
                return "expired"
            }
            return localEvaluationLeaderboardItems.isEmpty ? nil : "fresh"
        default:
            return nil
        }
    }

    var radarResultsUpdatedAt: String? {
        switch radarDisplaySource {
        case "official_snapshot":
            return snapshot?.referenceSnapshotFeed.trustedLatest?.publishedAt
        case "local_evaluation":
            return radarDashboard?.runMetadata.completedAt
        default:
            return nil
        }
    }

    var compactRecommendationPresentation: CompactRecommendationPresentation {
        CompactSessionPresenter.recommendation(
            snapshot: snapshot,
            dashboard: radarDashboard,
            portfolio: radarPortfolio,
            displaySource: radarDisplaySource,
            displayFreshness: radarDisplayFreshness,
            leaderboardItems: radarLeaderboardItems
        )
    }

    var radarReferenceFreshness: String? {
        snapshot?.referenceSnapshotFeed.trustedLatest == nil
            ? nil
            : snapshot?.referenceSnapshotFeed.freshness
    }

    var radarReferenceAgeHours: Int? {
        snapshot?.referenceSnapshotFeed.trustedLatest == nil
            ? nil
            : snapshot?.referenceSnapshotFeed.ageHours
    }

    func radarDisplayName(for configurationID: String?) -> String? {
        guard let configurationID else { return nil }
        if let candidate = snapshot?.config.modelIngress.connections
            .flatMap(\.modelCandidates)
            .first(where: { $0.id == configurationID }) {
            return ModelIdentityPresentation.displayLabel(
                model: candidate.modelId,
                effort: candidate.scanProfile
            )
        }
        if let entry = radarDashboard?.leaderboard.first(where: { $0.candidateId == configurationID }) {
            return ModelIdentityPresentation.displayLabel(model: entry.modelId, effort: entry.effort)
        }
        if let entry = referenceSnapshotEntry(for: configurationID) {
            return ModelIdentityPresentation.displayLabel(
                model: entry.modelConfiguration.canonicalModelId,
                effort: entry.modelConfiguration.reasoningEffort
            )
        }
        return configurationID
    }

    private var referenceSnapshotLeaderboardItems: [RadarLeaderboardItem] {
        guard let latest = snapshot?.referenceSnapshotFeed.trustedLatest else { return [] }
        let entriesByID = Dictionary(
            latest.entries.map { ($0.modelConfigurationId, $0) },
            uniquingKeysWith: { first, _ in first }
        )
        var seenIDs = Set<String>()
        var entries: [BridgeReferenceSnapshotEntry] = (
            latest.leaderboardProjection?.rows ?? []
        ).compactMap { row in
            guard let entry = entriesByID[row.modelConfigurationId] else { return nil }
            guard entry.advisorEligible else { return nil }
            seenIDs.insert(entry.modelConfigurationId)
            return entry
        }
        entries.append(contentsOf: latest.entries.filter {
            $0.advisorEligible && !seenIDs.contains($0.modelConfigurationId)
        })
        let currentID = radarRepresentativeConfigurationID
        let recommendedID = radarRepresentativeDecision?.candidateModelConfigurationId
        return entries.map { entry in
            RadarLeaderboardItem(
                id: entry.modelConfigurationId,
                displayName: ModelIdentityPresentation.displayLabel(
                    model: entry.modelConfiguration.canonicalModelId,
                    effort: entry.modelConfiguration.reasoningEffort
                ),
                modelName: entry.modelConfiguration.canonicalModelId,
                providerId: ModelIdentityPresentation.providerBrandID(
                    providerID: entry.modelConfiguration.providerId,
                    model: entry.modelConfiguration.canonicalModelId
                ),
                effort: entry.modelConfiguration.reasoningEffort,
                score: entry.score,
                maxScore: entry.maxScore,
                elapsedSeconds: entry.elapsedMs / 1_000,
                referenceCostUsd: entry.estimatedApiCostUsd,
                costCoverage: entry.costCoverage,
                questionScores: entry.questionScores,
                isCurrent: currentID.map {
                    referenceEntry(entry, matches: $0)
                } ?? false,
                isRecommended: recommendedID.map {
                    referenceEntry(entry, matches: $0)
                } ?? false
            )
        }
    }

    func radarLeaderboardItem(for configurationID: String) -> RadarLeaderboardItem? {
        radarLeaderboardItems.first { item in
            item.id == configurationID
                || (radarDisplaySource == "official_snapshot"
                    && referenceSnapshotEntry(for: configurationID)?.modelConfigurationId
                        == item.id)
        }
    }

    func radarDisplayRank(for configurationID: String) -> Int? {
        radarEvidence?.resolvedResultRows.first {
            $0.modelConfigurationId == configurationID
        }?.displayRank
    }

    private func referenceSnapshotEntry(
        for configurationID: String
    ) -> BridgeReferenceSnapshotEntry? {
        guard let latest = snapshot?.referenceSnapshotFeed.trustedLatest else {
            return nil
        }
        if let exact = latest.entries.first(where: {
            $0.modelConfigurationId == configurationID
        }) {
            return exact
        }
        let matches = referenceSnapshotIdentityMatches(
            in: latest.entries,
            configurationID: configurationID
        )
        return matches.count == 1 ? matches[0] : nil
    }

    private func referenceEntry(
        _ entry: BridgeReferenceSnapshotEntry,
        matches configurationID: String
    ) -> Bool {
        guard let latest = snapshot?.referenceSnapshotFeed.trustedLatest else {
            return false
        }
        if latest.entries.contains(where: {
            $0.modelConfigurationId == configurationID
        }) {
            return entry.modelConfigurationId == configurationID
        }
        let matches = referenceSnapshotIdentityMatches(
            in: latest.entries,
            configurationID: configurationID
        )
        return matches.count == 1 && matches[0].modelConfigurationId == entry.modelConfigurationId
    }

    private func referenceSnapshotIdentityMatches(
        in entries: [BridgeReferenceSnapshotEntry],
        configurationID: String
    ) -> [BridgeReferenceSnapshotEntry] {
        guard let localIdentity = configuredIdentity(for: configurationID) else {
            return []
        }
        return entries.filter { entry in
            canonicalProviderID(entry.modelConfiguration.providerId)
                    == canonicalProviderID(localIdentity.providerID)
                && entry.modelConfiguration.canonicalModelId.caseInsensitiveCompare(
                    localIdentity.modelID
                ) == .orderedSame
                && canonicalEffort(entry.modelConfiguration.reasoningEffort)
                    == canonicalEffort(localIdentity.effort)
        }
    }

    private func configuredIdentity(
        for configurationID: String
    ) -> (providerID: String, modelID: String, effort: String)? {
        guard let ingress = snapshot?.config.modelIngress else { return nil }
        for connection in ingress.connections {
            guard let candidate = connection.modelCandidates.first(
                where: { $0.id == configurationID }
            ) else {
                continue
            }
            let providerID = connection.providerId ?? {
                switch connection.sourceId {
                case "codex_local": return "codex"
                case "claude_local": return "claude-code"
                case "grok_local": return "grok-build"
                default: return connection.sourceId
                }
            }()
            return (providerID, candidate.modelId, candidate.scanProfile)
        }
        return nil
    }

    private func canonicalProviderID(_ value: String) -> String {
        switch value.lowercased() {
        case "codex", "openai": return "openai"
        case "claude", "claude-code", "anthropic": return "anthropic"
        case "grok", "grok-build", "xai": return "xai"
        default: return value.lowercased()
        }
    }

    private func canonicalEffort(_ value: String) -> String {
        value.lowercased()
            .replacingOccurrences(of: "-", with: "")
            .replacingOccurrences(of: "_", with: "")
    }

    private var localEvaluationLeaderboardItems: [RadarLeaderboardItem] {
        guard let evidence = radarEvidence,
              evidence.resolvedDataSource == "local_evaluation",
              evidence.currentStatus == "ready",
              let currentID = evidence.currentModelConfigurationId else {
            return []
        }
        let recommendedID = radarRepresentativeDecision?.candidateModelConfigurationId
        let decisionEligibleCandidateIDs = Set(
            evidence.candidateDecisions.compactMap { decision in
                decision.status == "eligible" ? decision.modelConfigurationId : nil
            }
        )
        var eligibleCandidateIDs = Set(evidence.eligibleCandidateIds)
            .intersection(decisionEligibleCandidateIDs)
        eligibleCandidateIDs.insert(currentID)
        return (radarDashboard?.leaderboard ?? [])
            .filter { eligibleCandidateIDs.contains($0.candidateId) }
            .map { entry in
                RadarLeaderboardItem(
                    id: entry.candidateId,
                    displayName: ModelIdentityPresentation.displayLabel(
                        model: entry.modelId,
                        effort: entry.effort
                    ),
                    modelName: entry.modelId,
                    providerId: localProviderID(for: entry),
                    effort: entry.effort,
                    score: entry.overallScore.map(Double.init) ?? Double(entry.modeScore),
                    maxScore: entry.overallScore == nil ? Double(entry.modeScoreMax) : 100,
                    elapsedSeconds: entry.elapsedSeconds,
                    referenceCostUsd: entry.estimatedCostUsd,
                    costCoverage: entry.costCoverage,
                    questionScores: entry.questionResults.reduce(into: [:]) { scores, result in
                        guard let score = result.semanticScore else { return }
                        scores[result.questionId] = Double(score)
                    },
                    isCurrent: entry.candidateId == currentID,
                    isRecommended: entry.candidateId == recommendedID
                )
            }
    }

    private func localProviderID(for entry: BridgeLeaderboardEntry) -> String? {
        let connections = snapshot?.config.modelIngress.connections ?? []
        if let connectionID = entry.connectionId,
           let connection = connections.first(where: { $0.id == connectionID }) {
            return ModelIdentityPresentation.providerBrandID(
                providerID: connection.providerId ?? connection.sourceId,
                familyID: entry.familyId,
                model: entry.modelId
            )
        }
        if let connection = connections.first(
            where: { connection in
                connection.modelCandidates.contains { $0.id == entry.candidateId }
            }
        ) {
            let candidate = connection.modelCandidates.first {
                $0.id == entry.candidateId
            }
            return ModelIdentityPresentation.providerBrandID(
                providerID: connection.providerId ?? connection.sourceId,
                familyID: candidate?.familyId ?? entry.familyId,
                model: entry.modelId
            )
        }
        return ModelIdentityPresentation.providerBrandID(
            providerID: entry.sourceId,
            familyID: entry.familyId,
            model: entry.modelId
        )
    }

    var runEntries: [BridgeRunEntry] {
        snapshot?.runtime.runEntries ?? []
    }

    var activeCodexSessions: [BridgeDetectedCodexSession] {
        snapshot?.config.recommendation.detectedActiveSessions ?? []
    }

    var activeModelSessions: [BridgeDetectedModelSession] {
        let sessions = snapshot?.config.recommendation.activeModelSessions ?? []
        let sessionsByKey = Dictionary(
            sessions.map { (activeModelSessionKey($0), $0) },
            uniquingKeysWith: { first, _ in first }
        )
        return stableActiveModelSessionKeys.compactMap { sessionsByKey[$0] }
    }

    var evidenceCards: [BridgeEvidenceCard] {
        guard let dashboard = snapshot?.dashboard else { return [] }
        let byCandidateID = Dictionary(uniqueKeysWithValues: dashboard.cards.map { ($0.candidateId, $0) })
        return leaderboard.compactMap { byCandidateID[$0.candidateId] }.filter { $0.recentCount > 0 }
    }

    var evaluationProfiles: [BridgeEvaluationProfile] {
        snapshot?.questionPack.evaluationProfiles ?? []
    }

    var selectedEvaluationProfile: BridgeEvaluationProfile? {
        let profileID = resolvedPreferredManualEvaluationProfileID
        return evaluationProfiles.first { $0.id == profileID }
    }

    var isEvaluationProfileSelectionLocked: Bool {
        pendingScanPlanPreviewID != nil
            || activeBridgeOperationID != nil
            || snapshot?.runtime.isRunning == true
            || snapshot?.runtime.hasResumableRun == true
    }

    var activeEvaluationProfile: BridgeEvaluationProfile? {
        guard let metadata = snapshot?.dashboard.runMetadata else { return nil }
        return evaluationProfiles.first { $0.id == metadata.evaluationProfileId }
    }

    var upgradeEvaluationProfile: BridgeEvaluationProfile? {
        guard snapshot?.dashboard.runMetadata.evaluationResultLevel == "provisional",
              let targetID = snapshot?.dashboard.runMetadata.upgradeTargetProfileId else {
            return nil
        }
        return evaluationProfiles.first { $0.id == targetID }
    }

    private var completeEvaluationProfile: BridgeEvaluationProfile? {
        evaluationProfiles.first { $0.id == "full" && $0.resultLevel == "complete" }
            ?? evaluationProfiles.first { $0.resultLevel == "complete" }
    }

    var scheduledEvaluationProfile: BridgeEvaluationProfile? {
        return completeEvaluationProfile
    }

    private var resolvedPreferredManualEvaluationProfileID: String? {
        if let preferredManualEvaluationProfileID,
           evaluationProfiles.contains(where: { $0.id == preferredManualEvaluationProfileID }) {
            return preferredManualEvaluationProfileID
        }
        if let defaultID = snapshot?.questionPack.defaultEvaluationProfileId,
           evaluationProfiles.contains(where: { $0.id == defaultID }) {
            return defaultID
        }
        return completeEvaluationProfile?.id ?? evaluationProfiles.first?.id
    }

    private var newScanEvaluationProfileID: String? {
        resolvedPreferredManualEvaluationProfileID
    }

    private var resumableEvaluationProfileID: String? {
        guard snapshot?.runtime.hasResumableRun == true else {
            return newScanEvaluationProfileID
        }
        let profileID = snapshot?.dashboard.runMetadata.evaluationProfileId
        return evaluationProfiles.contains(where: { $0.id == profileID }) ? profileID : nil
    }

    func selectEvaluationProfile(_ profileID: String) {
        guard !isEvaluationProfileSelectionLocked,
              evaluationProfiles.contains(where: { $0.id == profileID }) else {
            return
        }
        preferredManualEvaluationProfileID = profileID
        UserDefaults.standard.set(
            profileID,
            forKey: Self.preferredManualEvaluationProfileKey
        )
    }

    func toggleExpanded() {
        isExpanded.toggle()
    }

    func prepareExpandedDestination() {
        expandedDestination = glancePresentation.destination
    }

    func consumeExpandedDestination() -> GlanceDestination {
        defer { expandedDestination = nil }
        guard let captured = expandedDestination,
              captured == glancePresentation.destination else {
            return glancePresentation.destination
        }
        return captured
    }

    func setGlanceActuallyVisible(_ isVisible: Bool) {
        isGlanceActuallyVisible = isVisible
    }

    func dismissScanConflict() {
        scanConflictMessage = nil
        scanConflictPresentation = nil
    }

    func startManualScan() {
        startRegularScan()
    }

    func restartManualScan() {
        startScan(
            intent: BridgeScanIntent(
                forceRestart: true,
                evaluationProfileID: resumableEvaluationProfileID
            ),
            autoResumeOnInterruption: false,
            conflictPresentation: .expanded
        )
    }

    func startManualScan(forceRestart: Bool) {
        if forceRestart {
            restartManualScan()
        } else {
            startRegularScan()
        }
    }

    func startRegularScan(conflictPresentation: ScanConflictPresentation = .expanded) {
        if snapshot?.runtime.hasResumableRun == true {
            startScan(
                intent: BridgeScanIntent(),
                autoResumeOnInterruption: false,
                conflictPresentation: conflictPresentation
            )
            return
        }
        let evaluationProfileID = newScanEvaluationProfileID
        if evaluationProfileID == "quick" {
            previewAndStartScan(
                intent: BridgeScanIntent(evaluationProfileID: evaluationProfileID),
                autoResumeOnInterruption: false,
                conflictPresentation: conflictPresentation
            )
            return
        }
        startScan(
            intent: BridgeScanIntent(
                evaluationProfileID: evaluationProfileID
            ),
            autoResumeOnInterruption: false,
            conflictPresentation: conflictPresentation
        )
    }

    func startIncrementalFullScan(
        conflictPresentation: ScanConflictPresentation = .settings
    ) {
        previewAndStartScan(
            intent: BridgeScanIntent(selectionMode: .incrementalFull),
            autoResumeOnInterruption: false,
            conflictPresentation: conflictPresentation
        )
    }

    func startFreshFullScan(
        conflictPresentation: ScanConflictPresentation = .settings
    ) {
        guard let fullProfile = completeEvaluationProfile else {
            transientMessage = "完整评测当前不可用"
            return
        }
        startScan(
            intent: BridgeScanIntent(
                forceRestart: true,
                evaluationProfileID: fullProfile.id
            ),
            autoResumeOnInterruption: false,
            conflictPresentation: conflictPresentation
        )
    }

    func startCustomScan(
        preview: BridgeScanPlanPreview,
        conflictPresentation: ScanConflictPresentation = .expanded
    ) {
        startPreviewedScan(
            intent: BridgeScanIntent(
                candidateIDs: preview.requestedCandidateIds,
                selectionMode: .custom,
                customRoundMode: BridgeCustomRoundMode(
                    rawValue: preview.requestedCustomRoundMode
                ),
                evaluationProfileID: preview.profile.id
            ),
            preview: preview,
            autoResumeOnInterruption: false,
            conflictPresentation: conflictPresentation
        )
    }

    func startIngressCandidateScan(
        candidateIDs: [String],
        conflictPresentation: ScanConflictPresentation = .expanded
    ) {
        previewAndStartScan(
            intent: BridgeScanIntent(
                candidateIDs: candidateIDs,
                selectionMode: .single
            ),
            autoResumeOnInterruption: false,
            conflictPresentation: conflictPresentation
        )
    }

    func startSingleScan(
        candidateID: String,
        conflictPresentation: ScanConflictPresentation = .expanded
    ) {
        previewAndStartScan(
            intent: BridgeScanIntent(
                candidateIDs: [candidateID],
                selectionMode: .single,
                evaluationProfileID: newScanEvaluationProfileID
            ),
            autoResumeOnInterruption: false,
            conflictPresentation: conflictPresentation
        )
    }

    func upgradeCurrentEvaluationProfile() {
        guard !isEvaluationProfileSelectionLocked,
              let metadata = snapshot?.dashboard.runMetadata,
              metadata.runId != "legacy" else {
            transientMessage = "当前结果不能补全评测"
            return
        }
        previewAndStartScan(
            intent: BridgeScanIntent(
                upgradeFromRunID: metadata.runId
            ),
            autoResumeOnInterruption: false,
            conflictPresentation: .expanded
        )
    }

    func upgradeCurrentSelectionEvaluationProfile(
        profileID: String,
        candidateIDs: [String]
    ) {
        guard !isEvaluationProfileSelectionLocked,
              !candidateIDs.isEmpty,
              let metadata = snapshot?.dashboard.runMetadata,
              metadata.runId != "legacy",
              let profile = evaluationProfiles.first(where: { $0.id == profileID }) else {
            transientMessage = "所选评测模式不可用"
            return
        }
        selectEvaluationProfile(profile.id)
        previewAndStartScan(
            intent: BridgeScanIntent(
                candidateIDs: candidateIDs,
                evaluationProfileID: profile.id,
                upgradeFromRunID: metadata.runId
            ),
            autoResumeOnInterruption: false,
            conflictPresentation: .expanded
        )
    }

    func startCandidateRepair(
        runID: String,
        candidateID: String,
        questionID: String? = nil,
        autoResumeOnInterruption: Bool = false
    ) {
        if activeBridgeOperationID != nil || snapshot?.runtime.isRunning == true {
            reportScanConflict(
                "已有扫描任务正在进行，请等待完成，或先暂停／停止当前任务后再试。",
                presentation: .expanded
            )
            return
        }
        repairFailureMessage = nil
        transientMessage = "正在重试失败题..."
        let operationID = UUID()
        snapshotGeneration &+= 1
        activeBridgeOperationID = operationID
        armCurrentModelRefreshTimer()
        do {
            try bridge.startRepair(
                runID: runID,
                candidateID: candidateID,
                questionID: questionID,
                onEvent: { [weak self] event in
                    self?.consume(event: event, operationID: operationID)
                },
                onComplete: { [weak self] in
                    guard self?.completeBridgeOperation(operationID) == true else { return }
                    self?.reloadSnapshotAsync(
                        autoResumeOnInterruption: autoResumeOnInterruption
                    )
                }
            )
        } catch {
            _ = completeBridgeOperation(operationID)
            let failureMessage = error.localizedDescription
            repairFailureMessage = failureMessage
            transientMessage = failureMessage
            resolveGlance()
        }
    }

    func startFailedRepair(runID: String, candidateIDs: [String]) {
        startFailedRepair(
            runID: runID,
            candidateIDs: candidateIDs,
            autoResumeOnInterruption: false
        )
    }

    private func startFailedRepair(
        runID: String,
        candidateIDs: [String],
        autoResumeOnInterruption: Bool
    ) {
        guard !candidateIDs.isEmpty else {
            transientMessage = "当前没有可重试的失败题"
            return
        }
        if activeBridgeOperationID != nil || snapshot?.runtime.isRunning == true {
            reportScanConflict(
                "已有扫描任务正在进行，请等待完成，或先暂停／停止当前任务后再试。",
                presentation: .expanded
            )
            return
        }
        repairFailureMessage = nil
        transientMessage = "正在并行重试失败题..."
        let operationID = UUID()
        snapshotGeneration &+= 1
        activeBridgeOperationID = operationID
        armCurrentModelRefreshTimer()
        do {
            try bridge.startFailedRepair(
                runID: runID,
                candidateIDs: candidateIDs,
                onEvent: { [weak self] event in
                    self?.consume(event: event, operationID: operationID)
                },
                onComplete: { [weak self] in
                    guard self?.completeBridgeOperation(operationID) == true else { return }
                    self?.reloadSnapshotAsync(
                        autoResumeOnInterruption: autoResumeOnInterruption
                    )
                }
            )
        } catch {
            _ = completeBridgeOperation(operationID)
            let failureMessage = error.localizedDescription
            repairFailureMessage = failureMessage
            transientMessage = failureMessage
            resolveGlance()
        }
    }

    func startTimedOutRepair(runID: String, candidateIDs: [String]) {
        startTimedOutRepair(
            runID: runID,
            candidateIDs: candidateIDs,
            autoResumeOnInterruption: false
        )
    }

    private func startTimedOutRepair(
        runID: String,
        candidateIDs: [String],
        autoResumeOnInterruption: Bool
    ) {
        guard !candidateIDs.isEmpty else {
            transientMessage = "当前没有可重试的超时题"
            return
        }
        if activeBridgeOperationID != nil || snapshot?.runtime.isRunning == true {
            reportScanConflict(
                "已有扫描任务正在进行，请等待完成，或先暂停／停止当前任务后再试。",
                presentation: .expanded
            )
            return
        }
        repairFailureMessage = nil
        transientMessage = "正在重试全部超时题..."
        let operationID = UUID()
        snapshotGeneration &+= 1
        activeBridgeOperationID = operationID
        armCurrentModelRefreshTimer()
        do {
            try bridge.startTimedOutRepair(
                runID: runID,
                candidateIDs: candidateIDs,
                onEvent: { [weak self] event in
                    self?.consume(event: event, operationID: operationID)
                },
                onComplete: { [weak self] in
                    guard self?.completeBridgeOperation(operationID) == true else { return }
                    self?.reloadSnapshotAsync(
                        autoResumeOnInterruption: autoResumeOnInterruption
                    )
                }
            )
        } catch {
            _ = completeBridgeOperation(operationID)
            let failureMessage = error.localizedDescription
            repairFailureMessage = failureMessage
            transientMessage = failureMessage
            resolveGlance()
        }
    }

    func dismissResumableRun() {
        guard activeBridgeOperationID == nil, snapshot?.runtime.isRunning != true else {
            transientMessage = "扫描中"
            return
        }
        Task { [weak self] in
            guard let self else { return }
            do {
                let response = try await commandGateway.dismissResumableRun()
                transientMessage = response.message
                reloadSnapshotAsync()
            } catch {
                transientMessage = error.localizedDescription
            }
        }
    }

    func pauseScan() {
        requestScanControl("pause")
    }

    func stopScan() {
        requestScanControl("stop")
    }

    func resumeCurrentOperation(
        conflictPresentation: ScanConflictPresentation = .expanded,
        autoResumeOnInterruption: Bool = false
    ) {
        guard let runtime = snapshot?.runtime, runtime.hasResumableRun else {
            startRegularScan(conflictPresentation: conflictPresentation)
            return
        }
        let runID = runtime.resumableOperationRunId
            ?? runtime.resumableRunId
            ?? runtime.currentRunId
        switch runtime.resumableOperationKind {
        case "candidate_repair":
            guard let runID, let candidateID = runtime.resumableCandidateIds.first else {
                transientMessage = "修复断点信息不完整，无法继续"
                return
            }
            startCandidateRepair(
                runID: runID,
                candidateID: candidateID,
                questionID: runtime.resumableQuestionId,
                autoResumeOnInterruption: autoResumeOnInterruption
            )
        case "failed_repair":
            guard let runID, !runtime.resumableCandidateIds.isEmpty else {
                transientMessage = "修复断点信息不完整，无法继续"
                return
            }
            startFailedRepair(
                runID: runID,
                candidateIDs: runtime.resumableCandidateIds,
                autoResumeOnInterruption: autoResumeOnInterruption
            )
        case "timeout_repair":
            guard let runID, !runtime.resumableCandidateIds.isEmpty else {
                transientMessage = "修复断点信息不完整，无法继续"
                return
            }
            startTimedOutRepair(
                runID: runID,
                candidateIDs: runtime.resumableCandidateIds,
                autoResumeOnInterruption: autoResumeOnInterruption
            )
        default:
            startScan(
                intent: BridgeScanIntent(),
                autoResumeOnInterruption: autoResumeOnInterruption,
                conflictPresentation: conflictPresentation
            )
        }
    }

    private func requestScanControl(_ action: String) {
        guard activeBridgeOperationID != nil || snapshot?.runtime.isRunning == true else {
            transientMessage = "当前没有正在运行的扫描"
            return
        }
        guard pendingScanControlAction == nil else { return }
        let requestID = UUID()
        pendingScanControlAction = action
        pendingScanControlRequestID = requestID
        pendingScanControlRequestedAt = Date()
        transientMessage = action == "pause" ? "正在暂停" : "正在停止"
        resolveGlance()
        Task(priority: .userInitiated) { [weak self] in
            guard let self else { return }
            do {
                let response = try await commandGateway.requestScanControl(
                    action,
                    clientSessionID: clientSessionID
                )
                transientMessage = response.message
                if !response.ok {
                    clearPendingScanControl(requestID: requestID)
                    resolveGlance()
                }
            } catch {
                clearPendingScanControl(requestID: requestID)
                transientMessage = error.localizedDescription
                resolveGlance()
            }
        }
        Task { [weak self] in
            try? await Task.sleep(nanoseconds: 20_000_000_000)
            guard let self, self.pendingScanControlRequestID == requestID else { return }
            self.clearPendingScanControl(requestID: requestID)
            self.transientMessage = action == "pause"
                ? "暂停请求未确认，请重试"
                : "停止请求未确认，请重试"
            self.resolveGlance()
        }
    }

    private func startScheduledScan() {
        startScan(
            intent: BridgeScanIntent(
                forceRestart: true,
                evaluationProfileID: scheduledEvaluationProfile?.id
            ),
            autoResumeOnInterruption: true,
            conflictPresentation: .background
        )
    }

    private func previewAndStartScan(
        intent: BridgeScanIntent,
        autoResumeOnInterruption: Bool,
        conflictPresentation: ScanConflictPresentation
    ) {
        activeScanConflictPresentation = conflictPresentation
        if pendingScanPlanPreviewID != nil
            || activeBridgeOperationID != nil
            || snapshot?.runtime.isRunning == true {
            reportScanConflict(
                "已有扫描任务正在进行，请等待完成，或先暂停／停止当前任务后再试。",
                presentation: conflictPresentation
            )
            return
        }
        if intent.selectionMode != .regular, snapshot?.runtime.hasResumableRun == true {
            reportScanConflict(
                "存在尚未完成的扫描，请先继续、重新开始或放弃该断点。",
                presentation: conflictPresentation
            )
            return
        }
        let requestID = UUID()
        pendingScanPlanPreviewID = requestID
        transientMessage = "正在校验扫描计划..."
        Task(priority: .userInitiated) { [weak self] in
            guard let self else { return }
            do {
                let preview = try await previewScan(intent)
                guard pendingScanPlanPreviewID == requestID else { return }
                pendingScanPlanPreviewID = nil
                startPreviewedScan(
                    intent: intent,
                    preview: preview,
                    autoResumeOnInterruption: autoResumeOnInterruption,
                    conflictPresentation: conflictPresentation
                )
            } catch {
                guard pendingScanPlanPreviewID == requestID else { return }
                pendingScanPlanPreviewID = nil
                transientMessage = error.localizedDescription
            }
        }
    }

    private func startPreviewedScan(
        intent: BridgeScanIntent,
        preview: BridgeScanPlanPreview,
        autoResumeOnInterruption: Bool,
        conflictPresentation: ScanConflictPresentation
    ) {
        guard preview.valid else {
            transientMessage = preview.message
                ?? ScanPlanPreviewPresenter.failureText(reason: preview.reason)
            return
        }
        guard let validatedIntent = intent.applying(preview) else {
            transientMessage = "扫描计划响应与请求不一致，请重试"
            return
        }
        startScan(
            intent: validatedIntent,
            autoResumeOnInterruption: autoResumeOnInterruption,
            conflictPresentation: conflictPresentation
        )
    }

    private func startScan(
        intent: BridgeScanIntent,
        autoResumeOnInterruption: Bool,
        conflictPresentation: ScanConflictPresentation
    ) {
        DebugLog.write(
            "AppSessionStore.startScan mode=\(intent.selectionMode.rawValue) "
                + "customRoundMode=\(intent.customRoundMode?.rawValue ?? "none")"
        )
        activeScanConflictPresentation = conflictPresentation
        if pendingScanPlanPreviewID != nil
            || activeBridgeOperationID != nil
            || snapshot?.runtime.isRunning == true {
            reportScanConflict(
                "已有扫描任务正在进行，请等待完成，或先暂停／停止当前任务后再试。",
                presentation: conflictPresentation
            )
            return
        }
        if intent.selectionMode != .regular, snapshot?.runtime.hasResumableRun == true {
            reportScanConflict(
                "存在尚未完成的扫描，请先继续、重新开始或放弃该断点。",
                presentation: conflictPresentation
            )
            return
        }
        repairFailureMessage = nil
        transientMessage = intent.forceRestart
            ? "正在重新扫描..."
            : (intent.selectionMode == .custom && intent.customRoundMode == .append
                ? "正在追加到本轮..."
                : "正在扫描...")
        let operationID = UUID()
        snapshotGeneration &+= 1
        activeBridgeOperationID = operationID
        do {
            try bridge.startScan(
                intent: intent,
                onEvent: { [weak self] event in
                    self?.consume(event: event, operationID: operationID)
                },
                onComplete: { [weak self] in
                    guard self?.completeBridgeOperation(operationID) == true else { return }
                    self?.reloadSnapshotAsync(
                        autoResumeOnInterruption: autoResumeOnInterruption
                    )
                }
            )
            armCurrentModelRefreshTimer()
        } catch {
            _ = completeBridgeOperation(operationID)
            transientMessage = error.localizedDescription
        }
    }

    private func reportScanConflict(
        _ message: String,
        presentation: ScanConflictPresentation
    ) {
        transientMessage = message
        guard presentation != .background else { return }
        scanConflictPresentation = presentation
        scanConflictMessage = message
    }

    @discardableResult
    private func completeBridgeOperation(_ operationID: UUID) -> Bool {
        guard activeBridgeOperationID == operationID else { return false }
        snapshotGeneration &+= 1
        activeBridgeOperationID = nil
        clearPendingScanControl()
        armCurrentModelRefreshTimer()
        resolveGlance()
        return true
    }

    private func clearPendingScanControl(requestID: UUID? = nil) {
        if let requestID, pendingScanControlRequestID != requestID {
            return
        }
        self.pendingScanControlAction = nil
        pendingScanControlRequestID = nil
        pendingScanControlRequestedAt = nil
    }

    func refresh() {
        DebugLog.write("AppSessionStore.refresh")
        if isSnapshotReloadInFlight {
            isSnapshotReloadPending = true
            DebugLog.write("AppSessionStore.refresh queued")
            return
        }
        reloadSnapshotAsync()
    }

    func refreshReferenceSnapshotNow() {
        guard !isReferenceSnapshotRefreshInFlight else { return }
        clearReferenceSnapshotRefreshFeedback()
        isReferenceSnapshotRefreshInFlight = true
        reloadSnapshotAsync(
            autoResumeOnInterruption: false,
            allowsStartupMaintenance: false,
            allowsReferenceRefresh: true,
            forceReferenceRefresh: true
        )
    }

    private func acceptAuthoritativeSnapshot(_ newSnapshot: BridgeSnapshot) {
        snapshotGeneration &+= 1
        DebugLog.write("AppSessionStore.acceptAuthoritativeSnapshot")
        consumeAuthoritativeSnapshot(
            newSnapshot,
            autoResumeTrigger: nil
        )
    }

    func applySettingsPatch(_ patch: SettingsConfigPatch) async throws -> BridgeConfig {
        let savedSnapshot = try await commandGateway.patchConfig(patch)
        acceptAuthoritativeSnapshot(savedSnapshot)
        return savedSnapshot.config
    }

    func upsertSettingsEndpoint(
        _ intent: BridgeEndpointUpsertIntent
    ) async throws -> BridgeConfig {
        let savedSnapshot = try await commandGateway.upsertEndpoint(intent)
        acceptAuthoritativeSnapshot(savedSnapshot)
        return savedSnapshot.config
    }

    func addSettingsEndpointModels(
        _ intent: BridgeEndpointModelsIntent
    ) async throws -> BridgeConfig {
        let savedSnapshot = try await commandGateway.addEndpointModels(intent)
        acceptAuthoritativeSnapshot(savedSnapshot)
        return savedSnapshot.config
    }

    func previewCustomScanOptions(
        candidateIDs: [String],
        evaluationProfileID: String?
    ) async throws -> BridgeCustomScanPlanOptions {
        try await commandGateway.previewCustomScanOptions(
            candidateIDs: candidateIDs,
            evaluationProfileID: evaluationProfileID
        )
    }

    func previewScan(_ intent: BridgeScanIntent) async throws -> BridgeScanPlanPreview {
        try await commandGateway.previewScan(intent)
    }

    func discoverSettingsModels(
        connectionID: String
    ) async throws -> BridgeModelDiscoveryResponse {
        try await commandGateway.discoverModels(connectionID: connectionID)
    }

    func testSettingsConnection(
        connectionID: String,
        modelID: String
    ) async throws -> BridgeConnectionTestResponse {
        let response = try await commandGateway.testConnection(
            connectionID: connectionID,
            modelID: modelID
        )
        try await acceptLatestSettingsSnapshot()
        return response
    }

    func importSettingsLocalProvider(
        providerID: String
    ) async throws -> BridgeLocalImportResponse {
        let response = try await commandGateway.importLocalProvider(providerID: providerID)
        if response.ok {
            try await acceptLatestSettingsSnapshot()
        }
        return response
    }

    func discoverSettingsLocalModels(
        providerID: String
    ) async throws -> BridgeLocalModelDiscoveryResponse {
        try await commandGateway.discoverLocalModels(providerID: providerID)
    }

    func probeSettingsEndpointConnection(
        baseURL: String,
        apiFormat: String,
        providerPreset: String,
        modelID: String,
        scanProfile: String = "default",
        apiKey: String
    ) async throws -> BridgeConnectionTestResponse {
        try await commandGateway.probeEndpointConnection(
            baseURL: baseURL,
            apiFormat: apiFormat,
            providerPreset: providerPreset,
            modelID: modelID,
            scanProfile: scanProfile,
            apiKey: apiKey
        )
    }

    func probeSettingsEndpointModels(
        baseURL: String,
        apiFormat: String,
        apiKey: String
    ) async throws -> BridgeModelDiscoveryResponse {
        try await commandGateway.probeEndpointModels(
            baseURL: baseURL,
            apiFormat: apiFormat,
            apiKey: apiKey
        )
    }

    func exportSettingsPersonalObservations(to url: URL) async throws {
        try await commandGateway.exportPersonalObservations(to: url)
    }

    func clearSettingsPersonalObservations() async throws -> BridgeDataOperationResponse {
        let response = try await commandGateway.clearPersonalObservations()
        if response.ok {
            try await acceptLatestSettingsSnapshot()
        }
        return response
    }

    private func acceptLatestSettingsSnapshot() async throws {
        let newSnapshot = try await commandGateway.snapshot()
        acceptAuthoritativeSnapshot(newSnapshot)
    }

    func copyRecommendedModel() {
        guard let best = snapshot?.dashboard.bestCombination else {
            transientMessage = "暂无可复制推荐"
            return
        }
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.setString(best.copyValue, forType: .string)
        transientMessage = "已复制 \(best.displayLabel)"
    }

    private func consume(event: ScanEvent, operationID: UUID) {
        guard activeBridgeOperationID == operationID else {
            DebugLog.write("AppSessionStore.consume ignored stale operation=\(operationID)")
            return
        }
        consume(event: event)
    }

    private func consume(event: ScanEvent) {
        snapshotGeneration &+= 1
        DebugLog.write("AppSessionStore.consume event=\(event.type)")
        switch RuntimeEventReducer.stateUpdate(for: event) {
        case .runtime(let runtime):
            applyRuntime(runtime)
        case .snapshot(let state):
            if event.type != "scan.finished" {
                applySnapshot(state)
            }
        case .none:
            break
        }
        switch event.type {
        case "auto-resume.started":
            transientMessage = event.message ?? "正在自动继续扫描..."
        case "auto-resume.noop":
            break
        case "auto-resume.manual-attention":
            transientMessage = event.message ?? "自动续扫已停止，请手动检查"
        case "timeout-repair.started":
            repairFailureMessage = nil
            transientMessage = "正在重试全部超时题..."
        case "timeout-repair.question.started":
            transientMessage = "正在重试超时题..."
        case "timeout-repair.finalizing":
            transientMessage = "正在更新本轮排名..."
        case "timeout-repair.finished":
            repairFailureMessage = nil
            transientMessage = "超时题重试完成"
        case "timeout-repair.paused":
            transientMessage = "超时题重试已暂停"
        case "timeout-repair.stopped":
            transientMessage = "超时题重试已停止"
        case "timeout-repair.failed":
            let failureMessage = event.failureMessage ?? "超时题重试失败"
            repairFailureMessage = failureMessage
            transientMessage = failureMessage
        case "timeout-repair.already_running":
            transientMessage = "扫描中"
        case "repair.started":
            repairFailureMessage = nil
            transientMessage = "正在重试失败题..."
        case "repair.question.started":
            transientMessage = "正在重试失败题..."
        case "repair.finalizing":
            transientMessage = "正在更新本轮排名..."
        case "repair.finished":
            repairFailureMessage = nil
            transientMessage = "失败题重试完成"
        case "repair.paused":
            transientMessage = "失败题重试已暂停"
        case "repair.stopped":
            transientMessage = "失败题重试已停止"
        case "repair.failed":
            let failureMessage = event.failureMessage ?? "失败题重试失败"
            repairFailureMessage = failureMessage
            transientMessage = failureMessage
        case "repair.already_running":
            transientMessage = "扫描中"
        case "scan.finished":
            if let state = event.snapshot {
                let previousRecommendation = snapshot?.recommendationDecisionIdentity
                let currentRecommendation = state.recommendationDecisionIdentity
                applySnapshot(state)
                transientMessage = "扫描完成"
                if let previousRecommendation,
                   currentRecommendation != previousRecommendation {
                    if currentRecommendation?.isActionableRecommendation == true,
                       let targetID = currentRecommendation?.targetConfigurationID,
                       let identity = radarGlanceIdentity(for: targetID) {
                        transientMessage = "推荐已变更：\(identity.shortDisplayName) \(identity.effortLabel)"
                    } else {
                        transientMessage = "推荐结论已更新"
                    }
                    isExpanded = true
                }
            }
        case "scan.failed":
            if event.failureCategory == "bridge_event_decode_failed" {
                snapshotRefreshIssue = SnapshotRefreshIssue(
                    message: "扫描结果未能更新，当前显示上次结果。",
                    detail: event.failureMessage ?? "扫描结果数据与当前版本不兼容"
                )
            }
            transientMessage = event.failureMessage ?? "扫描失败"
        case "scan.progress":
            transientMessage = progressMessage(for: event)
        case "scan.paused":
            transientMessage = "扫描已暂停"
        case "scan.stopped":
            transientMessage = "扫描已停止"
        case "scan.already_running":
            reportScanConflict(
                "已有扫描任务正在进行，请等待完成，或先暂停／停止当前任务后再试。",
                presentation: activeScanConflictPresentation
            )
        case "target.started":
            transientMessage = event.label.map { "正在扫描 \($0)" } ?? "正在扫描"
        default:
            break
        }
    }

    private func reloadSnapshotAsync() {
        reloadSnapshotAsync(
            autoResumeOnInterruption: false,
            allowsStartupMaintenance: true,
            allowsReferenceRefresh: true
        )
    }

    private func reloadSnapshotAsync(
        autoResumeOnInterruption: Bool,
        allowsStartupMaintenance: Bool = true,
        allowsReferenceRefresh: Bool = true,
        forceReferenceRefresh: Bool = false
    ) {
        guard !isSnapshotReloadInFlight else {
            isSnapshotReloadPending = true
            pendingSnapshotReloadAutoResumeOnInterruption =
                pendingSnapshotReloadAutoResumeOnInterruption
                || autoResumeOnInterruption
            pendingSnapshotReloadAllowsStartupMaintenance =
                pendingSnapshotReloadAllowsStartupMaintenance
                || allowsStartupMaintenance
            pendingSnapshotReloadAllowsReferenceRefresh =
                pendingSnapshotReloadAllowsReferenceRefresh
                || allowsReferenceRefresh
            pendingSnapshotReloadForcesReferenceRefresh =
                pendingSnapshotReloadForcesReferenceRefresh
                || forceReferenceRefresh
            return
        }
        isSnapshotReloadInFlight = true
        let shouldPerformStartupMaintenance = allowsStartupMaintenance
            && startupLoadCoordinator.claimMaintenanceIfNeeded()
        let shouldRefreshReference = !shouldPerformStartupMaintenance
            && allowsReferenceRefresh
            && referenceSnapshotRefreshPolicy.claimIfDue(
                force: forceReferenceRefresh
            )
        if shouldPerformStartupMaintenance
            && allowsReferenceRefresh
            && (forceReferenceRefresh || referenceSnapshotRefreshPolicy.isDue()) {
            isSnapshotReloadPending = true
            pendingSnapshotReloadAllowsReferenceRefresh = true
            pendingSnapshotReloadForcesReferenceRefresh =
                pendingSnapshotReloadForcesReferenceRefresh
                || forceReferenceRefresh
        }
        let shouldObserveLocalState = !shouldPerformStartupMaintenance
            && activeBridgeOperationID == nil
            && snapshot?.runtime.isRunning != true
        let requestGeneration = snapshotGeneration
        Task(priority: .userInitiated) { [weak self] in
            guard let self else { return }
            DebugLog.write("AppSessionStore.reloadSnapshotAsync begin")
            do {
                var observationFailureDetail: String?
                if shouldPerformStartupMaintenance && snapshot == nil {
                    do {
                        let cachedSnapshot = try await commandGateway.snapshot()
                        if requestGeneration == self.snapshotGeneration {
                            DebugLog.write(
                                "AppSessionStore.reloadSnapshotAsync published cached startup snapshot"
                            )
                            consumeAuthoritativeSnapshot(
                                cachedSnapshot,
                                autoResumeTrigger: nil
                            )
                        }
                    } catch {
                        DebugLog.write(
                            "AppSessionStore.reloadSnapshotAsync cached startup snapshot unavailable"
                        )
                    }
                }
                if shouldObserveLocalState {
                    do {
                        _ = try await commandGateway.observeState(
                            includeCodexInsights: true
                        )
                    } catch {
                        observationFailureDetail = error.localizedDescription
                        DebugLog.write(
                            "AppSessionStore.reloadSnapshotAsync observation error="
                                + error.localizedDescription
                        )
                    }
                }
                let loadResult = try await commandGateway.loadSnapshot(
                    performStartupMaintenance: shouldPerformStartupMaintenance,
                    refreshReference: shouldRefreshReference
                )
                if shouldPerformStartupMaintenance {
                    startupLoadCoordinator.recordMaintenanceResult(
                        successfully: loadResult.warningDetail == nil
                    )
                    if loadResult.warningDetail != nil {
                        enqueueStartupMaintenanceRetryIfAvailable()
                    }
                }
                let newSnapshot = loadResult.snapshot
                let maintenanceFailureDetail = [
                    observationFailureDetail,
                    loadResult.warningDetail,
                ]
                .compactMap { $0 }
                .joined(separator: "\n")
                if let refreshStatus = loadResult.referenceRefreshStatus {
                    referenceSnapshotRefreshPolicy.record(
                        status: refreshStatus,
                        latestPublishedAt: newSnapshot.referenceSnapshotFeed.trustedLatest.flatMap {
                            self.bridgeDate(from: $0.publishedAt)
                        }
                    )
                }
                isSnapshotReloadInFlight = false
                if forceReferenceRefresh {
                    isReferenceSnapshotRefreshInFlight = false
                }
                defer { startPendingSnapshotReloadIfNeeded() }
                guard requestGeneration == self.snapshotGeneration else {
                    DebugLog.write("AppSessionStore.reloadSnapshotAsync ignored stale generation")
                    return
                }
                if forceReferenceRefresh,
                   let refreshStatus = loadResult.referenceRefreshStatus {
                    presentReferenceSnapshotRefreshFeedback(status: refreshStatus)
                }
                DebugLog.write("AppSessionStore.reloadSnapshotAsync success")
                let autoResumeTrigger: BridgeAutoResumeTrigger? =
                    autoResumeOnInterruption
                    ? .interruption
                    : (shouldPerformStartupMaintenance ? .startup : nil)
                consumeAuthoritativeSnapshot(
                    newSnapshot,
                    autoResumeTrigger: autoResumeTrigger
                )
                if !maintenanceFailureDetail.isEmpty {
                    snapshotRefreshIssue = SnapshotRefreshIssue(
                        message: shouldPerformStartupMaintenance
                            ? "启动维护未全部完成，当前仍显示已保存状态。"
                            : observationFailureDetail != nil
                            ? "本机使用记录暂未更新，当前仍显示已保存状态。"
                            : "远端参考结果暂未更新，当前仍显示已验证缓存。",
                        detail: maintenanceFailureDetail
                    )
                }
            } catch {
                if shouldRefreshReference {
                    referenceSnapshotRefreshPolicy.record(status: "failed")
                }
                if shouldPerformStartupMaintenance {
                    enqueueStartupMaintenanceRetryIfAvailable()
                }
                isSnapshotReloadInFlight = false
                if forceReferenceRefresh {
                    isReferenceSnapshotRefreshInFlight = false
                }
                defer { startPendingSnapshotReloadIfNeeded() }
                guard requestGeneration == self.snapshotGeneration else { return }
                DebugLog.write("AppSessionStore.reloadSnapshotAsync error=\(error.localizedDescription)")
                if forceReferenceRefresh {
                    presentReferenceSnapshotRefreshFeedback(status: "failed")
                }
                recordSnapshotRefreshFailure(error)
            }
        }
    }

    private func enqueueStartupMaintenanceRetryIfAvailable() {
        guard startupLoadCoordinator.canRetryMaintenance else { return }
        isSnapshotReloadPending = true
        pendingSnapshotReloadAllowsStartupMaintenance = true
        guard !startupMaintenanceRetryScheduled else { return }
        startupMaintenanceRetryScheduled = true
        startupMaintenanceRetryTask = Task { [weak self] in
            try? await Task.sleep(
                nanoseconds: Self.startupMaintenanceRetryDelayNanoseconds
            )
            guard let self, !Task.isCancelled else { return }
            self.startupMaintenanceRetryTask = nil
            self.startupMaintenanceRetryScheduled = false
            self.startPendingSnapshotReloadIfNeeded()
        }
    }

    private func presentReferenceSnapshotRefreshFeedback(status: String) {
        referenceSnapshotRefreshFeedbackDismissTask?.cancel()
        referenceSnapshotRefreshFeedbackStatus = status
        referenceSnapshotRefreshFeedbackDismissTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            guard let self,
                  !Task.isCancelled,
                  self.referenceSnapshotRefreshFeedbackStatus == status else {
                return
            }
            self.referenceSnapshotRefreshFeedbackStatus = nil
        }
    }

    private func clearReferenceSnapshotRefreshFeedback() {
        referenceSnapshotRefreshFeedbackDismissTask?.cancel()
        referenceSnapshotRefreshFeedbackDismissTask = nil
        referenceSnapshotRefreshFeedbackStatus = nil
    }

    private func consumeAuthoritativeSnapshot(
        _ newSnapshot: BridgeSnapshot,
        autoResumeTrigger: BridgeAutoResumeTrigger?
    ) {
        recordSnapshotRefreshSuccess()
        applySnapshot(newSnapshot)
        armTimer()
        if let autoResumeTrigger {
            startAutomaticResume(trigger: autoResumeTrigger)
        }
    }

    func reloadPeriodicSnapshotAsync() {
        reloadSnapshotAsync(
            autoResumeOnInterruption: false,
            allowsStartupMaintenance: false,
            allowsReferenceRefresh: true
        )
    }

    private func startPendingSnapshotReloadIfNeeded() {
        guard isSnapshotReloadPending else { return }
        guard !startupMaintenanceRetryScheduled else { return }
        let autoResumeOnInterruption = pendingSnapshotReloadAutoResumeOnInterruption
        let allowsStartupMaintenance = pendingSnapshotReloadAllowsStartupMaintenance
        let allowsReferenceRefresh = pendingSnapshotReloadAllowsReferenceRefresh
        let forceReferenceRefresh = pendingSnapshotReloadForcesReferenceRefresh
        isSnapshotReloadPending = false
        pendingSnapshotReloadAutoResumeOnInterruption = false
        pendingSnapshotReloadAllowsStartupMaintenance = false
        pendingSnapshotReloadAllowsReferenceRefresh = false
        pendingSnapshotReloadForcesReferenceRefresh = false
        DebugLog.write("AppSessionStore.refresh dequeued")
        reloadSnapshotAsync(
            autoResumeOnInterruption: autoResumeOnInterruption,
            allowsStartupMaintenance: allowsStartupMaintenance,
            allowsReferenceRefresh: allowsReferenceRefresh,
            forceReferenceRefresh: forceReferenceRefresh
        )
    }

    private func armCurrentModelRefreshTimer() {
        guard timersEnabled else { return }
        currentModelRefreshTimer?.invalidate()
        let isRuntimeRefreshActive = activeBridgeOperationID != nil
            || snapshot?.runtime.isRunning == true
        let refreshInterval = isRuntimeRefreshActive
            ? activeRuntimeRefreshInterval
            : idleCurrentModelRefreshInterval
        currentModelRefreshTimer = Timer.scheduledTimer(
            withTimeInterval: refreshInterval,
            repeats: false
        ) { [weak self] _ in
            Task { @MainActor in
                guard let self else { return }
                self.currentModelRefreshTimer = nil
                self.reloadPeriodicSnapshotAsync()
                self.armCurrentModelRefreshTimer()
            }
        }
    }

    private func recordSnapshotRefreshSuccess() {
        consecutiveSnapshotRefreshFailures = 0
        lastSuccessfulSnapshotRefreshAt = Date()
        snapshotRefreshIssue = nil
    }

    private func recordSnapshotRefreshFailure(_ error: Error) {
        consecutiveSnapshotRefreshFailures += 1

        if snapshot == nil {
            transientMessage = error.localizedDescription
            resolveGlance()
            return
        }

        guard consecutiveSnapshotRefreshFailures >= snapshotRefreshWarningThreshold else { return }
        let scanIsStillRunning = activeBridgeOperationID != nil
            || snapshot?.runtime.isRunning == true
        let message = scanIsStillRunning
            ? "部分状态暂未刷新，扫描仍在后台继续。"
            : "数据暂未更新，当前显示上次结果。"
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "zh_CN")
        formatter.dateFormat = "HH:mm:ss"
        let retryStatus = lastSuccessfulSnapshotRefreshAt.map {
            "上次完整刷新 \(formatter.string(from: $0))，正在自动重试"
        } ?? "正在自动重试"
        snapshotRefreshIssue = SnapshotRefreshIssue(
            message: message,
            detail: "\(retryStatus)；\(error.localizedDescription)"
        )
    }

    private func armTimer() {
        guard let scheduler = snapshot?.config.scheduler,
              scheduler.enabled,
              snapshot?.runtime.isRunning != true,
              (snapshot?.runtime.enabledTargetCount ?? 0) > 0 else {
            scheduledScanTimer?.invalidate()
            scheduledScanTimer = nil
            scheduledScanFireDate = nil
            scheduledScanFingerprint = nil
            return
        }
        let fingerprint = schedulerFingerprint(scheduler)
        if scheduledScanTimer?.isValid == true,
           scheduledScanFingerprint == fingerprint,
           let fireDate = scheduledScanFireDate,
           fireDate > Date() {
            return
        }
        scheduledScanTimer?.invalidate()
        scheduledScanTimer = nil
        scheduledScanFireDate = nil
        scheduledScanFingerprint = nil
        guard let nextDate = nextScheduledRun().date else { return }
        let interval = max(1, nextDate.timeIntervalSinceNow)
        scheduledScanFireDate = nextDate
        scheduledScanFingerprint = fingerprint
        scheduledScanTimer = Timer.scheduledTimer(withTimeInterval: interval, repeats: false) { [weak self] _ in
            Task { @MainActor in
                self?.scheduledScanTimer = nil
                self?.scheduledScanFireDate = nil
                self?.scheduledScanFingerprint = nil
                self?.startScheduledScan()
            }
        }
    }

    func nextScheduledRun(now: Date = Date()) -> ScheduledRunStatus {
        let candidateCount = snapshot?.runtime.enabledTargetCount ?? 0
        let questionCount = max(
            1,
            scheduledEvaluationProfile?.questionCount
                ?? snapshot?.questionPack.questionCount
                ?? 1
        )
        guard let scheduler = snapshot?.config.scheduler, scheduler.enabled else {
            return ScheduledRunStatus(
                date: nil,
                absoluteText: "未安排",
                relativeText: "自动扫描已关闭",
                candidateCount: candidateCount,
                questionCount: questionCount,
                reason: "自动扫描已关闭"
            )
        }
        if snapshot?.runtime.isRunning == true {
            return ScheduledRunStatus(
                date: nil,
                absoluteText: "本轮结束后计算",
                relativeText: "本轮完成后重新计算",
                candidateCount: candidateCount,
                questionCount: questionCount,
                reason: "扫描正在运行"
            )
        }
        guard candidateCount > 0 else {
            return ScheduledRunStatus(
                date: nil,
                absoluteText: "未安排",
                relativeText: "没有已启用扫描档位",
                candidateCount: 0,
                questionCount: questionCount,
                reason: "没有已启用扫描档位"
            )
        }
        let fingerprint = schedulerFingerprint(scheduler)
        let cachedFireDate = scheduledScanFingerprint == fingerprint
            && (scheduledScanFireDate ?? .distantPast) > now
            ? scheduledScanFireDate
            : nil
        guard let date = cachedFireDate ?? nextFireDate(for: scheduler, now: now) else {
            return ScheduledRunStatus(
                date: nil,
                absoluteText: "未安排",
                relativeText: "扫描计划不可用",
                candidateCount: candidateCount,
                questionCount: questionCount,
                reason: "扫描计划不可用"
            )
        }

        return ScheduledRunStatus(
            date: date,
            absoluteText: "",
            relativeText: "",
            candidateCount: candidateCount,
            questionCount: questionCount,
            reason: "定时任务使用%@"
        )
    }

    private func applySnapshot(_ newSnapshot: BridgeSnapshot) {
        let previous = snapshot
        if let completedAt = newSnapshot.dashboard.runMetadata.completedAt,
           let completedDate = bridgeDate(from: completedAt) {
            lastCompletedScanAt = completedDate
        }
        reconcileActiveModelSessionOrder(
            newSnapshot.config.recommendation.activeModelSessions
        )
        snapshot = newSnapshot
        reconcilePendingScanControl(using: newSnapshot)
        reconcilePreferredEvaluationProfile(using: newSnapshot)
        armCurrentModelRefreshTimer()
        if timersEnabled {
            notificationEngine.consume(
                previous: previous,
                current: newSnapshot,
                isPanelExpanded: isExpanded
            )
        }
        resolveGlance()
    }

    private func applyRuntime(_ runtime: BridgeRuntime) {
        guard var currentSnapshot = snapshot else { return }
        currentSnapshot.runtime = runtime
        snapshot = currentSnapshot
        armCurrentModelRefreshTimer()
        resolveGlance()
    }

    private func activeModelSessionKey(_ session: BridgeDetectedModelSession) -> String {
        "\(session.source):\(session.id)"
    }

    private func reconcileActiveModelSessionOrder(
        _ sessions: [BridgeDetectedModelSession]
    ) {
        let incomingKeys = sessions.map(activeModelSessionKey)
        let liveKeys = Set(incomingKeys)
        stableActiveModelSessionKeys = stableActiveModelSessionKeys.filter {
            liveKeys.contains($0)
        }
        var knownKeys = Set(stableActiveModelSessionKeys)
        for key in incomingKeys where knownKeys.insert(key).inserted {
            stableActiveModelSessionKeys.append(key)
        }
    }

    private func reconcilePendingScanControl(using newSnapshot: BridgeSnapshot) {
        guard let action = pendingScanControlAction else { return }
        guard activeBridgeOperationID == nil else { return }
        if let requestedAt = pendingScanControlRequestedAt,
           let updatedAt = bridgeDate(from: newSnapshot.runtime.updatedAt),
           updatedAt < requestedAt.addingTimeInterval(-1) {
            return
        }

        let reachedTerminalState: Bool
        switch action {
        case "pause":
            reachedTerminalState = !newSnapshot.runtime.isRunning
                && newSnapshot.runtime.hasResumableRun
                && newSnapshot.runtime.lifecycleState == .pausedRecoverable
        case "stop":
            reachedTerminalState = !newSnapshot.runtime.isRunning
                && !newSnapshot.runtime.hasResumableRun
        default:
            reachedTerminalState = false
        }
        guard reachedTerminalState else { return }

        clearPendingScanControl()
        transientMessage = action == "pause" ? "扫描已暂停" : "扫描已停止"
    }

    private func reconcilePreferredEvaluationProfile(using newSnapshot: BridgeSnapshot) {
        let profiles = newSnapshot.questionPack.evaluationProfiles
        guard !profiles.isEmpty else { return }
        if let preferredManualEvaluationProfileID,
           profiles.contains(where: { $0.id == preferredManualEvaluationProfileID }) {
            return
        }
        let defaultID = newSnapshot.questionPack.defaultEvaluationProfileId
        let fallbackID = (
            profiles.contains(where: { $0.id == defaultID }) ? defaultID : nil
        ) ?? profiles.first(where: { $0.resultLevel == "complete" })?.id
            ?? profiles[0].id
        preferredManualEvaluationProfileID = fallbackID
        UserDefaults.standard.set(
            fallbackID,
            forKey: Self.preferredManualEvaluationProfileKey
        )
    }

    private static let idleRuntimeSnapshot = RuntimeSnapshot(
        lifecycleState: .idle,
        phase: nil,
        progressCompleted: 0,
        progressTotal: nil,
        lastPhase: nil,
        lastPhaseCompleted: 0,
        lastPhaseTotal: nil,
        stateChangedAt: nil,
        finalizingStartedAt: nil,
        updatedAt: nil,
        leaseExpiresAt: nil,
        isRecoverable: false,
        failureCategory: nil,
        currentTargetShortName: nil
    )

    private func resolveGlance(now: Date = Date()) {
        if pendingScanControlAction == "stop" {
            glancePresentation = GlanceStateResolver.stoppingPresentation(
                runtime: runtimeSnapshotState()
            )
            armGlanceBoundaryTimer(now: now)
            return
        }
        if pendingScanControlAction == "pause" {
            glancePresentation = GlanceStateResolver.pausingPresentation(
                runtime: runtimeSnapshotState()
            )
            armGlanceBoundaryTimer(now: now)
            return
        }
        glancePresentation = GlanceStateResolver.resolve(
            runtime: runtimeSnapshotState(),
            recommendation: recommendationSnapshot(),
            recommendationStatus: snapshot?.recommendationPortfolioV2.status,
            hasOfficialReferenceResults: radarDisplaySource == "official_snapshot"
                && !referenceSnapshotLeaderboardItems.isEmpty,
            now: now
        )
        armGlanceBoundaryTimer(now: now)
    }

    private func runtimeSnapshotState() -> RuntimeSnapshotState {
        guard let runtime = snapshot?.runtime else {
            return .available(Self.idleRuntimeSnapshot)
        }
        return .available(RuntimeSnapshot(
            lifecycleState: runtimeLifecycle(runtime),
            phase: glancePhase(runtime.currentPhase),
            progressCompleted: runtime.progressCompleted,
            progressTotal: runtime.progressTotal,
            lastPhase: glancePhase(runtime.lastPhase),
            lastPhaseCompleted: runtime.lastPhaseCompleted,
            lastPhaseTotal: runtime.lastPhaseTotal,
            stateChangedAt: bridgeDate(from: runtime.stateChangedAt),
            finalizingStartedAt: bridgeDate(from: runtime.finalizingStartedAt),
            updatedAt: bridgeDate(from: runtime.updatedAt),
            leaseExpiresAt: bridgeDate(from: runtime.leaseExpiresAt),
            isRecoverable: runtime.hasResumableRun || runtime.lifecycleState == .pausedRecoverable,
            failureCategory: runtime.lastError,
            currentTargetShortName: runtime.currentTarget,
            activeEvaluationCount: runtime.activeEvaluationCount,
            oldestActiveEvaluationStartedAt: bridgeDate(
                from: runtime.oldestActiveEvaluationStartedAt
            ),
            executionTimeoutSeconds: runtime.executionTimeoutSeconds
        ))
    }

    private func recommendationSnapshot() -> RecommendationSnapshot? {
        guard (snapshot?.runtime.enabledTargetCount ?? 0) > 0 else { return nil }
        if snapshot?.recommendationPortfolioV2 != nil {
            return radarRecommendationSnapshot()
        }
        guard let dashboard = snapshot?.dashboard,
              let best = dashboard.bestCombination else { return nil }
        let createdAt = bridgeDate(from: best.recommendationCreatedAt)
            ?? bridgeDate(from: dashboard.runMetadata.completedAt)
            ?? leaderboard.first(where: { $0.candidateId == best.candidateId }).flatMap { bridgeDate(from: $0.latestValidAt) }
        let staleAt = bridgeDate(from: best.staleAt)
        let expiresAt = bridgeDate(from: best.expiresAt)
        return RecommendationSnapshot(
            fullDisplayName: best.model,
            shortDisplayName: best.shortDisplayName,
            effortLabel: best.effortLabel,
            recommendationOutcome: best.recommendationOutcome,
            currentDefaultCandidateId: best.currentDefaultCandidateId,
            recommendedCandidateId: best.candidateId,
            currentUsageStatus: snapshot?.config.recommendation.currentModelDetectionStatus ?? "unavailable",
            activeSessionCount: snapshot?.config.recommendation.detectedActiveSessionCount ?? 0,
            evidenceState: best.evidenceState,
            runStatus: dashboard.runMetadata.status,
            scoreText: best.overallScoreText ?? best.scoreText,
            recommendationCreatedAt: createdAt,
            runCompletedAt: bridgeDate(from: best.runCompletedAt) ?? bridgeDate(from: dashboard.runMetadata.completedAt),
            staleAt: staleAt,
            expiresAt: expiresAt
        )
    }

    private func radarRecommendationSnapshot() -> RecommendationSnapshot? {
        guard let portfolio = snapshot?.recommendationPortfolioV2,
              let decision = portfolio.representativeDecision else {
            return nil
        }
        let targetID: String
        if decision.decision == "recommend" {
            guard let candidateID = decision.candidateModelConfigurationId else { return nil }
            targetID = candidateID
        } else if decision.decision == "keep" {
            targetID = decision.currentModelConfigurationId
        } else {
            return nil
        }
        guard let identity = radarGlanceIdentity(for: targetID) else { return nil }
        let createdAt = identity.completedAt
            ?? radarDashboard?.runMetadata.completedAt.flatMap(bridgeDate(from:))
        let outcome: String
        if portfolio.recommendationLifecycle.isAdopted {
            outcome = "adopted"
        } else if portfolio.recommendationLifecycle.status == "reoptimize_required" {
            outcome = "reoptimize"
        } else {
            outcome = decision.decision == "recommend" ? "switch" : "keep"
        }
        return RecommendationSnapshot(
            fullDisplayName: identity.fullDisplayName,
            shortDisplayName: identity.shortDisplayName,
            effortLabel: identity.effortLabel,
            recommendationOutcome: outcome,
            currentDefaultCandidateId: decision.currentModelConfigurationId,
            recommendedCandidateId: targetID,
            currentUsageStatus: snapshot?.config.recommendation.currentModelDetectionStatus ?? "unavailable",
            activeSessionCount: snapshot?.config.recommendation.detectedActiveSessionCount ?? 0,
            evidenceState: "fresh",
            runStatus: radarDashboard?.runMetadata.status ?? "completed",
            scoreText: identity.scoreText,
            recommendationCreatedAt: createdAt,
            runCompletedAt: createdAt,
            staleAt: nil,
            expiresAt: nil
        )
    }

    private func radarGlanceIdentity(
        for configurationID: String
    ) -> (
        fullDisplayName: String,
        shortDisplayName: String,
        effortLabel: String,
        scoreText: String,
        completedAt: Date?
    )? {
        let localIdentity: (
            fullDisplayName: String,
            shortDisplayName: String,
            effortLabel: String,
            scoreText: String,
            completedAt: Date?
        )? = radarDashboard?.leaderboard.first(
            where: { $0.candidateId == configurationID }
        ).map { entry in
            return (
                entry.model,
                glanceShortDisplayName(model: entry.model, fallback: entry.label),
                entry.effort,
                entry.overallScoreText ?? entry.modeScoreText,
                bridgeDate(from: entry.validCompletedAt ?? entry.latestValidAt)
            )
        }
        let officialIdentity: (
            fullDisplayName: String,
            shortDisplayName: String,
            effortLabel: String,
            scoreText: String,
            completedAt: Date?
        )? = referenceSnapshotEntry(for: configurationID).map { entry in
            let completedAt = snapshot?.referenceSnapshotFeed.trustedLatest.flatMap {
                bridgeDate(from: $0.publishedAt)
            }
            return (
                fullDisplayName: entry.modelConfiguration.canonicalModelId,
                shortDisplayName: glanceShortDisplayName(
                    model: entry.modelConfiguration.canonicalModelId,
                    fallback: entry.modelConfiguration.displayName
                ),
                effortLabel: entry.modelConfiguration.reasoningEffort,
                scoreText: glanceScoreText(entry.score, maximum: entry.maxScore),
                completedAt: completedAt
            )
        }

        let configuredIdentity = snapshot?.config.modelIngress.connections
            .flatMap(\.modelCandidates)
            .first(where: { $0.id == configurationID })
            .map { candidate in
                (
                candidate.modelId,
                glanceShortDisplayName(model: candidate.modelId, fallback: candidate.displayName),
                candidate.scanProfile,
                "—",
                nil as Date?
            )
        }
        switch radarAuthoritativeDataSource {
        case "official_snapshot":
            return officialIdentity ?? configuredIdentity
        case "local_evaluation":
            return localIdentity ?? configuredIdentity
        default:
            return configuredIdentity
        }
    }

    private func glanceShortDisplayName(model: String, fallback: String) -> String {
        let modelID = String(model.split(separator: "/").last ?? Substring(model))
        let normalized = modelID.lowercased()
        if normalized.hasPrefix("gpt-") {
            let components = normalized.dropFirst(4).split(separator: "-")
            guard let version = components.first else { return fallback }
            let variant = components.dropFirst().map { $0.capitalized }.joined(separator: " ")
            return ([String(version), variant].filter { !$0.isEmpty }).joined(separator: " ")
        }
        return modelID
            .split(separator: "-")
            .map { component in
                let value = String(component)
                if value.caseInsensitiveCompare("deepseek") == .orderedSame { return "DeepSeek" }
                if value.caseInsensitiveCompare("grok") == .orderedSame { return "Grok" }
                return Double(value) == nil ? value.capitalized : value
            }
            .joined(separator: " ")
    }

    private func glanceScoreText(_ score: Double, maximum: Double) -> String {
        let roundedScore = score.rounded()
        let roundedMaximum = maximum.rounded()
        if abs(score - roundedScore) < 0.001, abs(maximum - roundedMaximum) < 0.001 {
            return "\(Int(roundedScore))/\(Int(roundedMaximum))"
        }
        return String(format: "%.1f/%.1f", score, maximum)
    }

    private func runtimeLifecycle(_ runtime: BridgeRuntime) -> RuntimeLifecycleState {
        switch runtime.lifecycleState {
        case .idle:
            return .idle
        case .preparing:
            return .preparing
        case .activeScan:
            return .activeScan
        case .pausedRecoverable:
            return .pausedRecoverable
        case .finalizing:
            return .finalizing
        case .failed:
            return .failed
        case .recommendationUnavailable:
            return .recommendationUnavailable
        }
    }

    private func glancePhase(_ value: BridgeRuntimePhase?) -> GlancePhase? {
        switch value {
        case .scan:
            return .scan
        case .repair:
            return .repair
        case nil:
            return nil
        }
    }

    private func armGlanceBoundaryTimer(now: Date) {
        glanceBoundaryTimer?.invalidate()
        glanceBoundaryTimer = nil

        let runtime: RuntimeSnapshot? = {
            if case .available(let value) = runtimeSnapshotState() { return value }
            return nil
        }()
        let recommendation = recommendationSnapshot()
        let nextBoundary = [
            runtime?.finalizingStartedAt?.addingTimeInterval(0.3),
            recommendation?.staleAt,
            recommendation?.expiresAt,
        ]
        .compactMap { $0 }
        .filter { $0 > now }
        .min()

        guard let nextBoundary else { return }
        glanceBoundaryTimer = Timer.scheduledTimer(
            withTimeInterval: max(0.01, nextBoundary.timeIntervalSince(now)),
            repeats: false
        ) { [weak self] _ in
            Task { @MainActor in
                self?.resolveGlance(now: Date())
            }
        }
    }

    func suspendGlanceBoundaryRefresh() {
        glanceBoundaryTimer?.invalidate()
        glanceBoundaryTimer = nil
    }

    func resumeGlanceBoundaryRefresh() {
        resolveGlance(now: Date())
        reloadSnapshotAsync(
            autoResumeOnInterruption: false,
            allowsStartupMaintenance: false,
            allowsReferenceRefresh: true
        )
    }

    private func bridgeDate(from value: String?) -> Date? {
        guard let value else { return nil }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: value) {
            return date
        }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: value)
    }

    private func startAutomaticResume(trigger: BridgeAutoResumeTrigger) {
        guard pendingScanPlanPreviewID == nil, activeBridgeOperationID == nil else {
            return
        }
        let operationID = UUID()
        var didStart = false
        snapshotGeneration &+= 1
        activeBridgeOperationID = operationID
        do {
            try bridge.startAutoResume(
                trigger: trigger,
                clientSessionID: clientSessionID,
                onEvent: { [weak self] event in
                    if event.type == "auto-resume.started" {
                        didStart = true
                    }
                    self?.consume(event: event, operationID: operationID)
                },
                onComplete: { [weak self] in
                    guard self?.completeBridgeOperation(operationID) == true else {
                        return
                    }
                    self?.reloadSnapshotAsync(
                        autoResumeOnInterruption: didStart,
                        allowsStartupMaintenance: false
                    )
                }
            )
            armCurrentModelRefreshTimer()
        } catch {
            _ = completeBridgeOperation(operationID)
            transientMessage = error.localizedDescription
        }
    }

    private func nextFireDate(for scheduler: BridgeSchedulerConfig, now: Date) -> Date? {
        let calendar = Calendar.autoupdatingCurrent
        switch scheduler.mode {
        case "interval":
            return now.addingTimeInterval(TimeInterval(max(1800, scheduler.intervalSeconds)))
        case "daily":
            let today = calendar.date(bySettingHour: scheduler.dailyHour, minute: scheduler.dailyMinute, second: 0, of: now)
            if let today, today > now {
                return today
            }
            let tomorrow = calendar.date(byAdding: .day, value: 1, to: calendar.startOfDay(for: now))
            return tomorrow.flatMap {
                calendar.date(bySettingHour: scheduler.dailyHour, minute: scheduler.dailyMinute, second: 0, of: $0)
            }
        case "weekly":
            let targetWeekday = min(max(scheduler.weeklyWeekday, 1), 7)
            let currentWeekday = mondayBasedWeekday(for: now, calendar: calendar)
            var dayOffset = targetWeekday - currentWeekday
            if dayOffset < 0 {
                dayOffset += 7
            }
            let targetDay = calendar.date(byAdding: .day, value: dayOffset, to: calendar.startOfDay(for: now))
            if let targetDay,
               let candidate = calendar.date(bySettingHour: scheduler.weeklyHour, minute: scheduler.weeklyMinute, second: 0, of: targetDay),
               candidate > now {
                return candidate
            }
            let nextWeek = calendar.date(byAdding: .day, value: 7, to: targetDay ?? calendar.startOfDay(for: now))
            return nextWeek.flatMap {
                calendar.date(bySettingHour: scheduler.weeklyHour, minute: scheduler.weeklyMinute, second: 0, of: $0)
            }
        default:
            return nil
        }
    }

    private func schedulerFingerprint(_ scheduler: BridgeSchedulerConfig) -> String {
        [
            scheduler.enabled ? "1" : "0",
            scheduler.mode,
            String(scheduler.intervalSeconds),
            String(scheduler.dailyHour),
            String(scheduler.dailyMinute),
            String(scheduler.weeklyWeekday),
            String(scheduler.weeklyHour),
            String(scheduler.weeklyMinute),
            "full",
        ].joined(separator: ":")
    }

    private func mondayBasedWeekday(for date: Date, calendar: Calendar) -> Int {
        let weekday = calendar.component(.weekday, from: date)
        return weekday == 1 ? 7 : weekday - 1
    }

    private func progressMessage(for event: ScanEvent) -> String {
        let runtime = event.runtimeState?.runtime ?? snapshot?.runtime
        let label = event.label ?? snapshot?.runtime.currentTarget ?? "-"
        guard let runtime else { return "正在扫描 · \(label)" }
        return "正在扫描 \(displayProgressText(for: runtime)) · \(label)"
    }

    private func displayProgressCounter(for runtime: BridgeRuntime) -> String {
        let progress = displayProgress(for: runtime)
        guard progress.total > 0 else { return "待扫" }
        return "\(progress.completed)/\(progress.total)"
    }

    private func displayProgressText(for runtime: BridgeRuntime) -> String {
        let progress = displayProgress(for: runtime)
        guard progress.total > 0 else { return "待扫描" }
        return "\(progress.phaseLabel) \(progress.completed)/\(progress.total)"
    }

    private func displayProgress(for runtime: BridgeRuntime) -> (phaseLabel: String, completed: Int, total: Int) {
        if runtime.currentPhase != .repair {
            let progressTotal = runtime.progressTotal ?? 0
            let completed = progressTotal > 0 ? runtime.progressCompleted : runtime.completedTargets
            let total = progressTotal > 0 ? progressTotal : runtime.totalTargets
            return ("扫描", completed, total)
        }
        if runtime.currentPhaseTotalTargets > 0 {
            return ("重试", runtime.currentPhaseCompletedTargets, runtime.currentPhaseTotalTargets)
        }
        return ("重试", runtime.completedTargets, runtime.totalTargets)
    }
}
