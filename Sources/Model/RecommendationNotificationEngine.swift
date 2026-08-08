import AppKit
import Combine
import Foundation
import UserNotifications

@MainActor
final class RecommendationNotificationEngine: ObservableObject {
    static let shared = RecommendationNotificationEngine()

    @Published private(set) var permissionStatusText = "未决定"

    private let center = UNUserNotificationCenter.current()
    private let defaults = UserDefaults.standard
    private let fingerprintKey = "modeldial.notification.fingerprints"
    private let legacyFingerprintKey = "modelpilot.notification.fingerprints"
    private let legacyBundleID = "dev.codexselectionisland.app"
    private var hasBaseline = false
    private var cancellables: Set<AnyCancellable> = []

    private init() {
        migrateLegacyFingerprintsIfNeeded()
        refreshPermissionStatus()
        NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)
            .sink { [weak self] _ in
                Task { @MainActor in self?.refreshPermissionStatus() }
            }
            .store(in: &cancellables)
    }

    private func migrateLegacyFingerprintsIfNeeded() {
        guard defaults.stringArray(forKey: fingerprintKey) == nil else { return }
        let legacyDomain = defaults.persistentDomain(forName: legacyBundleID)
        let legacyFingerprints = defaults.stringArray(forKey: legacyFingerprintKey)
            ?? legacyDomain?[legacyFingerprintKey] as? [String]
        if let legacyFingerprints {
            defaults.set(legacyFingerprints, forKey: fingerprintKey)
        }
    }

    func consume(
        previous: BridgeSnapshot?,
        current: BridgeSnapshot,
        isPanelExpanded: Bool
    ) {
        guard let previous else {
            hasBaseline = true
            return
        }
        guard hasBaseline else {
            hasBaseline = true
            return
        }
        guard !isPanelExpanded else { return }
        guard let event = notificationEvent(previous: previous, current: current) else { return }

        let fingerprint = "\(event.eventType)|\(event.runID)|\(event.candidateID)"
        var fingerprints = defaults.stringArray(forKey: fingerprintKey) ?? []
        guard !fingerprints.contains(fingerprint) else { return }

        center.getNotificationSettings { [weak self] settings in
            Task { @MainActor in
                guard let self else { return }
                switch settings.authorizationStatus {
                case .denied:
                    self.permissionStatusText = "前往设置"
                    return
                case .notDetermined:
                    self.permissionStatusText = "未决定"
                    return
                case .authorized, .provisional, .ephemeral:
                    self.permissionStatusText = "允许"
                @unknown default:
                    return
                }

                let content = UNMutableNotificationContent()
                content.title = event.title
                content.body = event.body
                content.sound = .default
                let request = UNNotificationRequest(
                    identifier: fingerprint,
                    content: content,
                    trigger: nil
                )
                do {
                    try await self.center.add(request)
                    fingerprints.append(fingerprint)
                    self.defaults.set(Array(fingerprints.suffix(64)), forKey: self.fingerprintKey)
                } catch {
                    // 通知失败不影响扫描与推荐状态。
                }
            }
        }
    }

    func requestPermissionFromUser() {
        center.getNotificationSettings { [weak self] settings in
            guard let self else { return }
            switch settings.authorizationStatus {
            case .denied:
                Task { @MainActor in self.openNotificationSettings() }
            case .notDetermined:
                self.center.requestAuthorization(options: [.alert, .sound]) { _, _ in
                    Task { @MainActor in self.refreshPermissionStatus() }
                }
            case .authorized, .provisional, .ephemeral:
                Task { @MainActor in self.permissionStatusText = "允许" }
            @unknown default:
                break
            }
        }
    }

    func refreshPermissionStatus() {
        center.getNotificationSettings { [weak self] settings in
            let text: String
            switch settings.authorizationStatus {
            case .authorized, .provisional, .ephemeral: text = "允许"
            case .denied: text = "前往设置"
            case .notDetermined: text = "未决定"
            @unknown default: text = "未知"
            }
            Task { @MainActor in self?.permissionStatusText = text }
        }
    }

    private func openNotificationSettings() {
        guard let url = URL(
            string: "x-apple.systempreferences:com.apple.Notifications-Settings.extension"
        ) else { return }
        NSWorkspace.shared.open(url)
    }

    private func notificationEvent(previous: BridgeSnapshot, current: BridgeSnapshot) -> NotificationEvent? {
        let best = current.stableDashboard?.bestCombination
            ?? current.dashboard.bestCombination
        let previousBest = previous.stableDashboard?.bestCombination
            ?? previous.dashboard.bestCombination
        let operationalRunID = current.runtime.currentRunId
            ?? current.runtime.resumableRunId
            ?? current.stableDashboard?.runMetadata.runId
            ?? current.dashboard.runMetadata.runId
        let recommendationRunID = current.recommendationPortfolioV2
            .representativeEvidence?.sourceSnapshotId
            ?? current.advisorV2Evidence.sourceSnapshotId
            ?? operationalRunID
        let fallbackCandidateID = best?.candidateId ?? "unknown"
        let fallbackDisplayName = best?.displayLabel ?? best?.label ?? L10n.tr("候选模型")

        if best?.decisionState == "retain_after_failure",
           previousBest?.evidenceState != "retained_after_failure" {
            let summary = (current.stableDashboard ?? current.dashboard)
                .leaderboard.first(where: { $0.candidateId == fallbackCandidateID })?
                .latestAttemptErrorSummary
                ?? L10n.tr("本次重扫失败")
            return NotificationEvent(
                eventType: "retained_after_failure",
                runID: operationalRunID,
                candidateID: fallbackCandidateID,
                title: L10n.tr("重扫失败，已保留旧成绩"),
                body: L10n.tr("%@：%@", fallbackDisplayName, summary)
            )
        }

        if let decisionIdentity = current.recommendationDecisionIdentity,
           decisionIdentity.isActionableRecommendation,
           decisionIdentity != previous.recommendationDecisionIdentity,
           let candidateID = decisionIdentity.targetConfigurationID {
            let displayName = recommendationDisplayName(
                in: current,
                configurationID: candidateID
            )
            let isNewProposal = current.recommendationPortfolioV2
                .recommendationLifecycle.isNewProposal
            return NotificationEvent(
                eventType: "recommendation_changed",
                runID: recommendationRunID,
                candidateID: candidateID,
                title: isNewProposal
                    ? L10n.tr("有新建议")
                    : L10n.tr("推荐模型已变化"),
                body: isNewProposal
                    ? L10n.tr("基于新结果，建议切换到 %@", displayName)
                    : L10n.tr("当前推荐：%@", displayName)
            )
        }
        if current.runtime.hasResumableRun, !previous.runtime.hasResumableRun {
            return NotificationEvent(
                eventType: "resume_circuit_open",
                runID: operationalRunID,
                candidateID: fallbackCandidateID,
                title: L10n.tr("自动续扫已暂停"),
                body: L10n.tr("请打开 modeldial 检查本轮中断原因。")
            )
        }
        return nil
    }

    private func recommendationDisplayName(
        in snapshot: BridgeSnapshot,
        configurationID: String
    ) -> String {
        if let candidate = snapshot.config.modelIngress.connections
            .flatMap(\.modelCandidates)
            .first(where: { $0.id == configurationID }) {
            return ModelIdentityPresentation.displayLabel(
                model: candidate.modelId,
                effort: candidate.scanProfile
            )
        }
        if let entry = (snapshot.stableDashboard ?? snapshot.dashboard)
            .leaderboard.first(where: { $0.candidateId == configurationID }) {
            return ModelIdentityPresentation.displayLabel(
                model: entry.modelId,
                effort: entry.effort
            )
        }
        if let entry = snapshot.referenceSnapshotFeed.latest?.entries.first(
            where: { $0.modelConfigurationId == configurationID }
        ) {
            return ModelIdentityPresentation.displayLabel(
                model: entry.modelConfiguration.canonicalModelId,
                effort: entry.modelConfiguration.reasoningEffort
            )
        }
        return configurationID
    }
}

private struct NotificationEvent {
    let eventType: String
    let runID: String
    let candidateID: String
    let title: String
    let body: String
}
