import SwiftUI

struct ModelCandidateRemovalRequest {
    let connectionID: String
    let candidateIDs: [String]
    let actionTitle: String
    let message: String
}

struct SettingsSynchronizationModifier: ViewModifier {
    let destinationRequest: SettingsDestination?
    let configuredSchedulerMode: String?
    let configuredSchedulerEnabled: Bool?
    let configuredMaxConcurrentTargets: Int?
    let configuredExecutionTimeoutSeconds: Int?
    let configuredTimeoutRetryCount: Int?
    let localImportSelectionID: String?
    let maxConcurrentTargets: Int
    let executionTimeoutSeconds: Int
    let timeoutRetryCount: Int
    let saveFeedbackState: SettingsSaveFeedbackState
    let onInitialAppearance: () -> Void
    let onDestinationRequestChange: (SettingsDestination?) -> Void
    let onSchedulerConfigurationChange: () -> Void
    let onScanExecutionConfigurationChange: () -> Void
    let onLocalImportSelectionChange: (String?) -> Void
    let onScanExecutionDraftChange: () -> Void
    let onSaveFeedbackChange: (SettingsSaveFeedbackState) -> Void

    func body(content: Content) -> some View {
        content
            .onAppear(perform: onInitialAppearance)
            .onChange(of: destinationRequest, perform: onDestinationRequestChange)
            .onChange(of: configuredSchedulerMode) { _ in
                onSchedulerConfigurationChange()
            }
            .onChange(of: configuredSchedulerEnabled) { _ in
                onSchedulerConfigurationChange()
            }
            .onChange(of: configuredMaxConcurrentTargets) { _ in
                onScanExecutionConfigurationChange()
            }
            .onChange(of: configuredExecutionTimeoutSeconds) { _ in
                onScanExecutionConfigurationChange()
            }
            .onChange(of: configuredTimeoutRetryCount) { _ in
                onScanExecutionConfigurationChange()
            }
            .onChange(of: localImportSelectionID, perform: onLocalImportSelectionChange)
            .onChange(of: maxConcurrentTargets) { _ in
                onScanExecutionDraftChange()
            }
            .onChange(of: executionTimeoutSeconds) { _ in
                onScanExecutionDraftChange()
            }
            .onChange(of: timeoutRetryCount) { _ in
                onScanExecutionDraftChange()
            }
            .onChange(of: saveFeedbackState, perform: onSaveFeedbackChange)
    }
}

struct SettingsPresentationModifier: ViewModifier {
    @Binding var showsCustomScanSheet: Bool
    @Binding var showsEndpointConnectionSheet: Bool
    let customScanSheet: () -> AnyView
    let endpointConnectionSheet: () -> AnyView
    let scanConflictAlertIsPresented: Binding<Bool>
    let scanConflictMessage: String
    let dismissScanConflict: () -> Void
    @Binding var showsDeleteConnectionConfirmation: Bool
    let connectionPendingDeletion: BridgeIngressConnection?
    let deleteEndpointConnection: (BridgeIngressConnection) -> Void
    let connectionDeletionMessage: (BridgeIngressConnection) -> String
    @Binding var showsModelCandidateRemovalConfirmation: Bool
    let modelCandidatesPendingRemoval: ModelCandidateRemovalRequest?
    let removeModelCandidates: (ModelCandidateRemovalRequest) -> Void
    @Binding var showsClearPersonalObservationsConfirmation: Bool
    let clearPersonalObservations: () -> Void

    func body(content: Content) -> some View {
        content
            .sheet(isPresented: $showsCustomScanSheet) {
                customScanSheet()
            }
            .sheet(isPresented: $showsEndpointConnectionSheet) {
                endpointConnectionSheet()
            }
            .alert(L10n.tr("无法开始扫描"), isPresented: scanConflictAlertIsPresented) {
                Button(L10n.tr("知道了"), role: .cancel, action: dismissScanConflict)
            } message: {
                Text(L10n.tr(scanConflictMessage))
            }
            .alert(
                L10n.tr("删除连接？"),
                isPresented: $showsDeleteConnectionConfirmation,
                presenting: connectionPendingDeletion
            ) { connection in
                Button(L10n.tr("删除连接"), role: .destructive) {
                    deleteEndpointConnection(connection)
                }
                Button(L10n.tr("取消"), role: .cancel) {}
            } message: { connection in
                Text(L10n.tr(connectionDeletionMessage(connection)))
            }
            .alert(
                L10n.tr("移除扫描档位？"),
                isPresented: $showsModelCandidateRemovalConfirmation,
                presenting: modelCandidatesPendingRemoval
            ) { request in
                Button(L10n.tr(request.actionTitle), role: .destructive) {
                    removeModelCandidates(request)
                }
                Button(L10n.tr("取消"), role: .cancel) {}
            } message: { request in
                Text(L10n.tr(request.message))
            }
            .alert(
                L10n.tr("清除个人观察数据？"),
                isPresented: $showsClearPersonalObservationsConfirmation
            ) {
                Button(L10n.tr("清除观察数据"), role: .destructive) {
                    clearPersonalObservations()
                }
                Button(L10n.tr("取消"), role: .cancel) {}
            } message: {
                Text(L10n.tr("这会清除本地用量、额度快照和建议采用记录，并从现在重新观察；评测成绩和扫描设置不会删除。"))
            }
    }
}
