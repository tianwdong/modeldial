import SwiftUI
import AppKit
import UniformTypeIdentifiers

struct SettingsView: View {
    private enum Layout {
        static let sidebarWidth: CGFloat = 176
        static let headerHeight: CGFloat = 48
        static let contentPadding: CGFloat = 22
        static let contentMaxWidth: CGFloat = 720
        static let ingressContentMaxWidth: CGFloat = 1200
        static let shortControlWidth: CGFloat = 144
        static let mediumControlWidth: CGFloat = 184
        static let longControlWidth: CGFloat = 280
        static let sectionSpacing: CGFloat = 20
        static let sourceListWidth: CGFloat = 286
        static let ingressReadableDetailWidth: CGFloat = 420
        static let ingressModelIdentityWidth: CGFloat = 180
        static let ingressModelFamilyIdentityWidth: CGFloat = 220
    }

    private let settingsScrollTopID = "settings-scroll-top"
    private let ingressDetailTopID = "ingress-detail-top"

    private typealias IngressWorkspaceItem = SettingsIngressPresenter.WorkspaceItem
    private typealias IngressMetric = SettingsIngressPresenter.Metric
    private typealias ModelFamilyGroup = SettingsIngressPresenter.ModelFamilyGroup
    private typealias EndpointProviderOption = SettingsIngressPresenter.EndpointProviderOption

    private struct CustomScanPreviewRequest: Hashable {
        let candidateIDs: [String]
        let evaluationProfileID: String?
    }

    @ObservedObject private var settings = SelectionSettingsStore.shared
    @ObservedObject private var selectionStore = AppSessionStore.shared
    @ObservedObject private var settingsWindowController = SettingsWindowController.shared
    @ObservedObject private var launchAtLoginStore = LaunchAtLoginStore.shared
    @ObservedObject private var notificationEngine = RecommendationNotificationEngine.shared
    @ObservedObject private var targetDisplayStore = IslandTargetDisplayStore.shared
    @ObservedObject private var appLanguage = AppLanguageStore.shared
    @ObservedObject private var updater = UpdaterController.shared
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Namespace private var sidebarSelectionNamespace
    @State private var activeTab: SettingsTab = .scan
    @State private var schedulerEnabled = false
    @State private var schedulerMode = "daily"
    @State private var intervalSeconds = 1800
    @State private var dailyTime = Date()
    @State private var weeklyWeekday = 1
    @State private var weeklyTime = Date()
    @State private var maxConcurrentTargets = 1
    @State private var executionTimeoutSeconds = 1200
    @State private var timeoutRetryCount = 0
    @State private var customCandidateIDs = Set<String>()
    @State private var customRoundMode = "new_round"
    @State private var customRoundModeWasManuallySelected = false
    @State private var customScanPlanOptions: BridgeCustomScanPlanOptions?
    @State private var customScanPreviewError: String?
    @State private var showsCustomScanSheet = false
    @State private var expandedModelFamilyIDs = Set<String>()
    @State private var selectedIngressConnectionID: String?
    @State private var showsEnabledCandidatesPopover = false
    @State private var showsEndpointConnectionSheet = false
    @State private var editingEndpointConnectionID: String?
    @State private var dismissEndpointEditorAfterSave = false
    @State private var connectionPendingDeletion: BridgeIngressConnection?
    @State private var showsDeleteConnectionConfirmation = false
    @State private var modelCandidatesPendingRemoval: ModelCandidateRemovalRequest?
    @State private var showsModelCandidateRemovalConfirmation = false
    @State private var endpointProvider = "deepseek"
    @State private var endpointCustomProvider = ""
    @State private var endpointPreset = "generic"
    @State private var endpointAPIFormat = "openai_chat_completions"
    @State private var endpointBaseURL = ""
    @State private var endpointAPIKey = ""
    @State private var endpointHasStoredAPIKey = false
    @State private var endpointIsReplacingAPIKey = false
    @State private var endpointModelID = ""
    @State private var endpointSelectedCatalogModelIDs = Set<String>()
    @State private var endpointReasoningProfileDrafts: [String: String] = [:]
    @State private var endpointShowsAdvanced = false
    @State private var settingsHydrationDepth = 0
    @State private var diagnosticCopyFeedback = false
    @State private var showsClearPersonalObservationsConfirmation = false
    private let customEndpointProviderID = "custom"

    var body: some View {
        settingsLayout
            .tint(IslandColor.interaction)
            .preferredColorScheme(.dark)
            .modifier(SettingsSynchronizationModifier(
                destinationRequest: settingsWindowController.destinationRequest,
                configuredSchedulerMode: settings.draftConfig?.scheduler.mode,
                configuredSchedulerEnabled: settings.draftConfig?.scheduler.enabled,
                configuredMaxConcurrentTargets: settings.draftConfig?.system.maxConcurrentTargets,
                configuredExecutionTimeoutSeconds: settings.draftConfig?.system.executionTimeoutSeconds,
                configuredTimeoutRetryCount: settings.draftConfig?.system.timeoutRetryCount,
                localImportSelectionID: settings.localImportSelectionID,
                maxConcurrentTargets: maxConcurrentTargets,
                executionTimeoutSeconds: executionTimeoutSeconds,
                timeoutRetryCount: timeoutRetryCount,
                saveFeedbackState: settings.saveFeedbackState,
                onInitialAppearance: {
                    applySettingsDestination(settingsWindowController.destinationRequest)
                    syncSchedulerFields()
                    syncScanExecutionFields()
                },
                onDestinationRequestChange: applySettingsDestination,
                onSchedulerConfigurationChange: syncSchedulerFields,
                onScanExecutionConfigurationChange: syncScanExecutionFields,
                onLocalImportSelectionChange: { connectionID in
                    guard let connectionID else { return }
                    selectedIngressConnectionID = connectionID
                },
                onScanExecutionDraftChange: persistScanExecutionIfNeeded,
                onSaveFeedbackChange: finishEndpointEditorSaveIfNeeded
            ))
            .modifier(SettingsPresentationModifier(
                showsCustomScanSheet: $showsCustomScanSheet,
                showsEndpointConnectionSheet: $showsEndpointConnectionSheet,
                customScanSheet: { AnyView(customScanSheet) },
                endpointConnectionSheet: { AnyView(endpointConnectionSheet) },
                scanConflictAlertIsPresented: scanConflictAlertIsPresented,
                scanConflictMessage: selectionStore.scanConflictMessage
                    ?? "已有扫描任务占用运行队列。",
                dismissScanConflict: selectionStore.dismissScanConflict,
                showsDeleteConnectionConfirmation: $showsDeleteConnectionConfirmation,
                connectionPendingDeletion: connectionPendingDeletion,
                deleteEndpointConnection: deleteEndpointConnection,
                connectionDeletionMessage: connectionDeletionMessage,
                showsModelCandidateRemovalConfirmation: $showsModelCandidateRemovalConfirmation,
                modelCandidatesPendingRemoval: modelCandidatesPendingRemoval,
                removeModelCandidates: removeModelCandidates,
                showsClearPersonalObservationsConfirmation: $showsClearPersonalObservationsConfirmation,
                clearPersonalObservations: settings.clearPersonalObservations
            ))
    }

    private var scanConflictAlertIsPresented: Binding<Bool> {
        Binding(
            get: {
                selectionStore.scanConflictMessage != nil
                    && selectionStore.scanConflictPresentation == .settings
            },
            set: { isPresented in
                if !isPresented {
                    selectionStore.dismissScanConflict()
                }
            }
        )
    }

    private var settingsLayout: some View {
        HStack(spacing: 0) {
            sidebar
                .frame(width: Layout.sidebarWidth)

            Rectangle()
                .fill(IslandVisual.hairline)
                .frame(width: 0.5)

            VStack(spacing: 0) {
                settingsPageHeader
                Divider().overlay(IslandVisual.hairline)
                if let message = settingsPageErrorMessage {
                    settingsErrorBanner(message)
                    Divider().overlay(IslandVisual.hairline)
                }
                activeSettingsPage
                Divider().overlay(IslandVisual.hairline)
                contentFooter
            }
            .frame(minWidth: 0, maxWidth: .infinity)
            .background(IslandVisual.workspaceSurface)
        }
        .frame(minWidth: 980, idealWidth: 1160, minHeight: 480, idealHeight: 560)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(IslandColor.canvas)
        .ignoresSafeArea()
    }

    private var activeSettingsPage: AnyView {
        switch activeTab {
        case .scan:
            return AnyView(scanContent)
        case .targets:
            return AnyView(targetsContent)
        case .automation:
            return AnyView(automationContent)
        case .general:
            return AnyView(generalContent)
        case .updates:
            return AnyView(softwareUpdateContent)
        case .health:
            return AnyView(dataHealthContent)
        }
    }

    private func applySettingsDestination(_ destination: SettingsDestination?) {
        guard let destination else { return }
        switch destination {
        case .modelIngress:
            activeTab = .targets
        }
        settingsWindowController.consumeDestination(destination)
    }

    private var settingsPageHeader: some View {
        HStack(spacing: LayoutRhythm.standard) {
            Text(LocalizedStringKey(activeTab.title))
                .font(Typography.sectionTitle)
                .foregroundStyle(IslandVisual.primaryText)
            Spacer(minLength: 0)
            if activeTab == .targets {
                enabledCandidatesButton
            }
            if settings.saveFeedbackState != .idle {
                settingsSaveFeedbackChip
            }
        }
        .padding(.horizontal, Layout.contentPadding)
        .frame(height: Layout.headerHeight)
        .background(IslandVisual.workspaceSurface)
        .background(WindowDragArea())
    }

    @ViewBuilder
    private var settingsSaveFeedbackChip: some View {
        switch settings.saveFeedbackState {
        case .idle:
            EmptyView()
        case .saving:
            HStack(spacing: 6) {
                ProgressView()
                    .controlSize(.small)
                Text("保存中")
                    .font(Typography.micro)
            }
            .foregroundStyle(IslandColor.interaction)
        case .saved:
            HStack(spacing: 6) {
                Image(systemName: "checkmark.circle.fill")
                Text("已保存")
                    .font(Typography.micro)
            }
            .foregroundStyle(IslandColor.liveTeal)
        case .failed:
            HStack(spacing: 6) {
                Image(systemName: "exclamationmark.triangle.fill")
                Text("保存失败")
                    .font(Typography.micro)
            }
            .foregroundStyle(IslandColor.alertRed)
        }
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 0) {
                Spacer(minLength: 0)
            }
                .padding(.horizontal, 18)
                .frame(height: Layout.headerHeight)
                .background(WindowDragArea())

            VStack(alignment: .leading, spacing: LayoutRhythm.section) {
                ForEach(SettingsSection.allCases, id: \.self) { section in
                    VStack(alignment: .leading, spacing: LayoutRhythm.compact) {
                        Text(LocalizedStringKey(section.title))
                            .font(Typography.micro)
                            .foregroundStyle(IslandVisual.hintText)
                            .padding(.horizontal, LayoutRhythm.compact)

                        VStack(spacing: LayoutRhythm.micro) {
                            ForEach(section.tabs) { tab in
                                settingsSidebarButton(tab)
                            }
                        }
                    }
                }
            }
            .padding(.horizontal, 12)
            .padding(.top, 14)

            Spacer(minLength: 0)
        }
        .background(IslandColor.canvas)
    }

    private var settingsPageErrorMessage: String? {
        if let message = settings.errorMessage,
           !message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return message
        }
        if activeTab == .general,
           let message = launchAtLoginStore.errorMessage,
           !message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return message
        }
        return nil
    }

    private func settingsErrorBanner(_ message: String) -> some View {
        HStack(alignment: .top, spacing: LayoutRhythm.compact) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(IslandColor.alertRed)
            Text(L10n.tr(message))
                .font(Typography.micro)
                .foregroundStyle(IslandColor.alertRed)
                .lineLimit(2)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, Layout.contentPadding)
        .padding(.vertical, LayoutRhythm.compact)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(IslandColor.alertRed.opacity(0.08))
    }

    private func settingsSidebarButton(_ tab: SettingsTab) -> some View {
        let isSelected = activeTab == tab
        return Button {
            withAnimation(reduceMotion ? nil : .controlSelection) {
                activeTab = tab
            }
        } label: {
            HStack(spacing: 12) {
                Image(systemName: tab.icon)
                    .font(Typography.tabLabel)
                    .foregroundStyle(isSelected ? IslandColor.interaction : IslandVisual.tertiaryText)
                    .frame(width: 16)
                Text(LocalizedStringKey(tab.title))
                    .font(Typography.tabLabel)
                    .foregroundStyle(isSelected ? IslandVisual.primaryText : IslandVisual.secondaryText)
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 12)
            .frame(height: 38)
            .background(
                RoundedRectangle(cornerRadius: IslandRadius.control)
                    .fill(isSelected ? IslandVisual.surfaceStrong : Color.clear)
                    .overlay(
                        RoundedRectangle(cornerRadius: IslandRadius.control)
                            .strokeBorder(isSelected ? IslandVisual.selectedBorder : Color.clear, lineWidth: 0.5)
                    )
            )
            .overlay(alignment: .leading) {
                if isSelected {
                    RoundedRectangle(cornerRadius: 1)
                        .fill(IslandColor.interaction)
                        .frame(width: 2, height: 22)
                        .matchedGeometryEffect(
                            id: "settings-sidebar-selection",
                            in: sidebarSelectionNamespace
                        )
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .islandPointerOnHover()
    }

    private var contentFooter: some View {
        HStack(spacing: 0) {
            Spacer(minLength: 0)
            HStack {
                Spacer()
                Text(selectionStore.scanActivityText)
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.tertiaryText)
                if settings.draftConfig?.scheduler != nil {
                    Text("·")
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.hintText)
                }
                if let scheduler = settings.draftConfig?.scheduler {
                    Text(statusText(for: scheduler))
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.hintText)
                }
            }
            .frame(maxWidth: activeContentMaxWidth, alignment: .leading)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, Layout.contentPadding)
        .padding(.vertical, LayoutRhythm.compact)
        .background(IslandVisual.workspaceSurface)
    }

    private enum SettingsTab: String, CaseIterable, Identifiable {
        case targets
        case scan
        case automation
        case general
        case updates
        case health

        var id: String { rawValue }

        var title: String {
            switch self {
            case .scan: return "扫描设置"
            case .targets: return "模型接入"
            case .automation: return "扫描计划"
            case .general: return "通用"
            case .updates: return "软件更新"
            case .health: return "数据健康"
            }
        }

        var icon: String {
            switch self {
            case .scan: return "scope"
            case .targets: return "point.3.connected.trianglepath.dotted"
            case .automation: return "clock.arrow.circlepath"
            case .general: return "gearshape.fill"
            case .updates: return "arrow.down.circle"
            case .health: return "stethoscope"
            }
        }

        var section: SettingsSection {
            switch self {
            case .targets, .scan, .automation: return .evaluation
            case .general, .updates: return .application
            case .health: return .dataPrivacy
            }
        }
    }

    private enum SettingsSection: String, CaseIterable {
        case evaluation
        case application
        case dataPrivacy

        var title: String {
            switch self {
            case .evaluation: return "评测"
            case .application: return "应用"
            case .dataPrivacy: return "数据与隐私"
            }
        }

        var tabs: [SettingsTab] {
            SettingsTab.allCases.filter { $0.section == self }
        }
    }

    private var activeContentMaxWidth: CGFloat {
        activeTab == .targets ? Layout.ingressContentMaxWidth : Layout.contentMaxWidth
    }

    private var schedulerSection: some View {
        settingsSection(
            title: "扫描计划",
            footer: schedulerFooterText
        ) {
            formRow("自动扫描") {
                Toggle("自动扫描", isOn: schedulerEnabledBinding)
                    .labelsHidden()
            }
            if let profile = selectionStore.scheduledEvaluationProfile {
                formRow("扫描范围") {
                    Text(
                        L10n.tr(
                            "%@ · 全部已启用配置 · %d 题",
                            localizedEvaluationProfileLabel(profile),
                            profile.questionCount
                        )
                    )
                        .font(Typography.settingsCardBody)
                        .foregroundStyle(IslandVisual.secondaryText)
                }
            }
            formRow("频率") {
                Picker("频率", selection: schedulerModeBinding) {
                    Text("按间隔").tag("interval")
                    Text("每天").tag("daily")
                    Text("每周").tag("weekly")
                }
                .labelsHidden()
                .frame(width: Layout.shortControlWidth)
                .disabled(!schedulerEnabled)
            }
            schedulerDetailControls
                .disabled(!schedulerEnabled)
            let nextRun = selectionStore.nextScheduledRun()
            formRow("下次运行") {
                VStack(alignment: .trailing, spacing: 2) {
                    Text(scheduledRunAbsoluteText(nextRun))
                    Text(L10n.tr(
                        "%@ · %d 个模型 · %d 道题",
                        scheduledRunRelativeText(nextRun),
                        nextRun.candidateCount,
                        nextRun.questionCount
                    ))
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.tertiaryText)
                    if let reason = scheduledRunReasonText(nextRun) {
                        Text(reason)
                            .font(Typography.micro)
                            .foregroundStyle(IslandVisual.hintText)
                    }
                }
            }
        }
    }

    private var applicationPreferencesSection: some View {
        settingsSection(title: "应用与通知", footer: nil) {
            formRow("登录时启动") {
                Toggle("登录时启动", isOn: launchAtLoginBinding)
                    .labelsHidden()
            }
            formRow("本地通知") {
                Button(LocalizedStringKey(notificationEngine.permissionStatusText)) {
                    notificationEngine.requestPermissionFromUser()
                }
                .disabled(notificationEngine.permissionStatusText == "允许")
            }
        }
    }

    private var languageSection: some View {
        settingsSection(
            title: L10n.Language.sectionTitle,
            footer: L10n.Language.immediateFooter
        ) {
            formRow(L10n.Language.language) {
                Picker(L10n.Language.language, selection: languageSelection) {
                    ForEach(AppLanguage.allCases, id: \.self) { language in
                        if language == .system {
                            Text("跟随系统").tag(language)
                        } else {
                            Text(language.menuLabel).tag(language)
                        }
                    }
                }
                .labelsHidden()
                .pickerStyle(.menu)
                .frame(width: Layout.longControlWidth)
            }
        }
    }

    private var softwareUpdateSection: some View {
        settingsSection(
            title: L10n.Update.preferencesTitle,
            footer: updater.isConfigured
                ? L10n.Update.configuredFooter
                : L10n.Update.notConfiguredFooter
        ) {
            formRow(L10n.Update.currentVersion) {
                Text(L10n.Update.versionBuild(
                    version: updater.currentVersion,
                    build: updater.currentBuild
                ))
                .font(Typography.settingsCardBody)
                .foregroundStyle(IslandVisual.secondaryText)
            }
            formRow(L10n.Update.checkNow) {
                Button(L10n.Update.checkNow) {
                    updater.checkForUpdates()
                }
                .buttonStyle(IslandActionButtonStyle(.secondary))
                .disabled(!updater.canCheckForUpdates || updater.updateCheckState.isChecking)
            }
            formRow(L10n.Update.status) {
                updateCheckStatus
            }
            formRow(L10n.Update.automaticChecks) {
                Toggle(
                    L10n.Update.automaticChecks,
                    isOn: automaticallyChecksForUpdatesBinding
                )
                .labelsHidden()
                .disabled(!updater.isConfigured)
            }
            formRow(L10n.Update.automaticDownloads) {
                Toggle(
                    L10n.Update.automaticDownloads,
                    isOn: automaticallyDownloadsUpdatesBinding
                )
                .labelsHidden()
                .disabled(!updater.isConfigured || !updater.allowsAutomaticUpdates)
            }
        }
    }

    private var updateCheckStatus: some View {
        let presentation = UpdateCheckPresenter.presentation(
            for: updater.isConfigured ? updater.updateCheckState : .notConfigured
        )
        return HStack(spacing: 6) {
            if updater.updateCheckState.isChecking {
                ProgressView()
                    .controlSize(.small)
            } else {
                Image(systemName: presentation.symbolName)
            }
            Text(presentation.text)
                .lineLimit(2)
        }
        .font(Typography.settingsCardBody)
        .foregroundStyle(updateCheckToneColor(presentation.tone))
        .multilineTextAlignment(.trailing)
    }

    private func updateCheckToneColor(_ tone: UpdateCheckPresenter.Tone) -> Color {
        switch tone {
        case .neutral: return IslandVisual.secondaryText
        case .active: return IslandColor.interaction
        case .success: return IslandColor.liveTeal
        case .warning: return IslandColor.alertAmber
        case .failure: return IslandColor.alertRed
        }
    }

    private var automaticallyChecksForUpdatesBinding: Binding<Bool> {
        Binding(
            get: { updater.automaticallyChecksForUpdates },
            set: { updater.setAutomaticallyChecksForUpdates($0) }
        )
    }

    private var automaticallyDownloadsUpdatesBinding: Binding<Bool> {
        Binding(
            get: { updater.automaticallyDownloadsUpdates },
            set: { updater.setAutomaticallyDownloadsUpdates($0) }
        )
    }

    private var languageSelection: Binding<AppLanguage> {
        Binding(
            get: { appLanguage.language },
            set: { appLanguage.select($0) }
        )
    }

    private var displaySection: some View {
        settingsSection(
            title: "显示器",
            footer: displayFooterText
        ) {
            formRow("目标屏幕") {
                Picker("目标屏幕", selection: targetDisplaySelection) {
                    Text("自动").tag("auto")
                    ForEach(DisplayInfo.all()) { display in
                        Text(
                            display.isBuiltin
                                ? L10n.tr("内建视网膜显示器")
                                : display.name
                        )
                        .tag(display.stableID)
                    }
                }
                .labelsHidden()
                .pickerStyle(.menu)
                .frame(width: Layout.longControlWidth)
            }
        }
    }

    private func sectionTitle(_ text: String) -> some View {
        Text(LocalizedStringKey(text))
            .font(Typography.sectionTitle)
            .foregroundStyle(IslandVisual.primaryText)
    }

    private var scanContent: some View {
        ScrollViewReader { proxy in
            ScrollView(.vertical, showsIndicators: true) {
                HStack(spacing: 0) {
                    Spacer(minLength: 0)
                    VStack(alignment: .leading, spacing: Layout.sectionSpacing) {
                        Color.clear.frame(height: 0).id(settingsScrollTopID)
                        regularScanScopeSection
                    }
                    .frame(maxWidth: Layout.contentMaxWidth, alignment: .leading)
                    Spacer(minLength: 0)
                }
                .padding(.horizontal, Layout.contentPadding)
                .padding(.vertical, LayoutRhythm.section)
            }
            .onChange(of: activeTab) { tab in
                guard tab == .scan else { return }
                proxy.scrollTo(settingsScrollTopID, anchor: .top)
            }
        }
    }

    private var targetsContent: some View {
        ingressWorkspaceSection
            .padding(.horizontal, Layout.contentPadding)
            .padding(.top, LayoutRhythm.section)
            .padding(.bottom, 14)
            .frame(maxHeight: .infinity, alignment: .topLeading)
        .frame(
            maxWidth: Layout.ingressContentMaxWidth,
            maxHeight: .infinity,
            alignment: .topLeading
        )
    }

    private var automationContent: some View {
        ScrollViewReader { proxy in
            ScrollView(.vertical, showsIndicators: true) {
                HStack(spacing: 0) {
                    Spacer(minLength: 0)
                    VStack(alignment: .leading, spacing: Layout.sectionSpacing) {
                        Color.clear.frame(height: 0).id(settingsScrollTopID)
                        schedulerSection
                    }
                    .frame(maxWidth: Layout.contentMaxWidth, alignment: .leading)
                    Spacer(minLength: 0)
                }
                .padding(.horizontal, Layout.contentPadding)
                .padding(.vertical, LayoutRhythm.section)
            }
            .onChange(of: activeTab) { tab in
                guard tab == .automation else { return }
                proxy.scrollTo(settingsScrollTopID, anchor: .top)
            }
        }
    }

    private var generalContent: some View {
        ScrollViewReader { proxy in
            ScrollView(.vertical, showsIndicators: true) {
                HStack(spacing: 0) {
                    Spacer(minLength: 0)
                    VStack(alignment: .leading, spacing: Layout.sectionSpacing) {
                        Color.clear.frame(height: 0).id(settingsScrollTopID)
                        languageSection
                        applicationPreferencesSection
                        displaySection
                    }
                    .frame(maxWidth: Layout.contentMaxWidth, alignment: .leading)
                    Spacer(minLength: 0)
                }
                .padding(.horizontal, Layout.contentPadding)
                .padding(.vertical, LayoutRhythm.section)
            }
            .onChange(of: activeTab) { tab in
                guard tab == .general else { return }
                proxy.scrollTo(settingsScrollTopID, anchor: .top)
            }
        }
    }

    private var softwareUpdateContent: some View {
        ScrollViewReader { proxy in
            ScrollView(.vertical, showsIndicators: true) {
                HStack(spacing: 0) {
                    Spacer(minLength: 0)
                    VStack(alignment: .leading, spacing: Layout.sectionSpacing) {
                        Color.clear.frame(height: 0).id(settingsScrollTopID)
                        softwareUpdateSection
                    }
                    .frame(maxWidth: Layout.contentMaxWidth, alignment: .leading)
                    Spacer(minLength: 0)
                }
                .padding(.horizontal, Layout.contentPadding)
                .padding(.vertical, LayoutRhythm.section)
            }
            .onChange(of: activeTab) { tab in
                guard tab == .updates else { return }
                proxy.scrollTo(settingsScrollTopID, anchor: .top)
            }
        }
    }

    private var dataHealthContent: some View {
        ScrollView(.vertical, showsIndicators: true) {
            HStack(spacing: 0) {
                Spacer(minLength: 0)
                if let diagnostics = selectionStore.snapshot?.diagnostics {
                    VStack(alignment: .leading, spacing: Layout.sectionSpacing) {
                        HStack(spacing: LayoutRhythm.compact) {
                            diagnosticStatusValue(
                                diagnostics.overallStatus == "healthy"
                                    ? L10n.tr("数据链正常")
                                    : L10n.tr("有数据需要检查"),
                                isHealthy: diagnostics.overallStatus == "healthy"
                            )
                            Spacer(minLength: 0)
                            Button {
                                copyDiagnosticSummary(diagnostics)
                            } label: {
                                Label(
                                    diagnosticCopyFeedback
                                        ? L10n.tr("已复制")
                                        : L10n.tr("复制诊断"),
                                    systemImage: diagnosticCopyFeedback ? "checkmark" : "doc.on.doc"
                                )
                            }
                            .buttonStyle(IslandActionButtonStyle(.secondary))
                            .help(L10n.tr("复制不含路径、账号和会话内容的诊断摘要"))
                        }
                        diagnosticOverviewSection(diagnostics)
                        diagnosticHistorySection(diagnostics)
                        diagnosticVersionsSection(diagnostics)
                        diagnosticPrivacySection
                        dataManagementSection
                    }
                    .frame(maxWidth: Layout.contentMaxWidth, alignment: .leading)
                } else {
                    VStack(alignment: .leading, spacing: Layout.sectionSpacing) {
                        VStack(spacing: LayoutRhythm.compact) {
                            Image(systemName: "stethoscope")
                                .font(Typography.pageTitle)
                                .foregroundStyle(IslandVisual.tertiaryText)
                            Text("暂无数据健康摘要")
                                .font(Typography.rowTitle)
                                .foregroundStyle(IslandVisual.secondaryText)
                        }
                        .frame(maxWidth: .infinity, minHeight: 180)
                        diagnosticPrivacySection
                        dataManagementSection
                    }
                    .frame(maxWidth: Layout.contentMaxWidth, alignment: .leading)
                }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, Layout.contentPadding)
            .padding(.vertical, LayoutRhythm.section)
        }
    }

    private var diagnosticPrivacySection: some View {
        settingsSection(title: "本地数据与隐私", footer: nil) {
            formRow("评测与历史") {
                Text("仅保存在本机，不自动上传")
                    .font(Typography.rowTitle)
                    .foregroundStyle(IslandVisual.secondaryText)
            }
            formRow("官网数据") {
                Text("仅下载公开快照、价格和版本信息")
                    .font(Typography.rowTitle)
                    .foregroundStyle(IslandVisual.secondaryText)
                    .multilineTextAlignment(.trailing)
            }
            formRow("诊断导出") {
                Text("仅由你主动复制，且不含路径、账号和会话内容")
                    .font(Typography.rowTitle)
                    .foregroundStyle(IslandVisual.secondaryText)
                    .multilineTextAlignment(.trailing)
            }
        }
    }

    private func accountQuotaSection(
        _ account: BridgeCodexAccountSnapshot?
    ) -> some View {
        settingsSection(
            title: "官方额度",
            footer: "额度窗口来自 Codex 官方账号接口，只作为账户状态展示。"
        ) {
            if let account {
                formRow("账号") {
                    Text(L10n.tr(account.planType ?? account.accountType))
                        .font(Typography.rowTitle)
                        .foregroundStyle(IslandVisual.secondaryText)
                }
                if account.quotaStatus == "available", !account.quotaWindows.isEmpty {
                    ForEach(account.quotaWindows) { window in
                        formRow(window.label) {
                            VStack(alignment: .trailing, spacing: 3) {
                                Text(quotaWindowUsageText(window.usedPercent))
                                    .font(Typography.rowTitle)
                                    .foregroundStyle(IslandVisual.secondaryText)
                                    .monospacedDigit()
                                Text(quotaWindowResetText(window.resetsAt))
                                    .font(Typography.micro)
                                    .foregroundStyle(IslandVisual.tertiaryText)
                            }
                        }
                    }
                } else {
                    formRow("额度窗口") {
                        Text(
                            account.quotaStatus == "not_applicable"
                                ? L10n.tr("当前账号不适用")
                                : L10n.tr("暂时不可读")
                        )
                            .font(Typography.rowTitle)
                            .foregroundStyle(IslandVisual.secondaryText)
                    }
                }
            } else {
                formRow("额度窗口") {
                    Text(L10n.tr("尚未读取"))
                        .font(Typography.rowTitle)
                        .foregroundStyle(IslandVisual.secondaryText)
                }
            }
        }
    }

    private var dataManagementSection: some View {
        settingsSection(
            title: "个人观察数据",
            footer: "导出和清除只处理本地用量、额度与建议采用记录，不包含评测成绩、凭据、项目内容或对话正文。"
        ) {
            formRow("导出") {
                Button {
                    exportPersonalObservations()
                } label: {
                    Label(L10n.tr("导出观察数据"), systemImage: "square.and.arrow.up")
                }
                .buttonStyle(IslandActionButtonStyle(.secondary))
                .disabled(settings.isDataOperationRunning)
            }
            formRow("清除") {
                Button(role: .destructive) {
                    showsClearPersonalObservationsConfirmation = true
                } label: {
                    Label(L10n.tr("清除观察数据"), systemImage: "trash")
                }
                .buttonStyle(IslandActionButtonStyle(.secondary))
                .disabled(settings.isDataOperationRunning)
            }
            if let message = settings.dataOperationMessage {
                formRow("状态") {
                    Text(L10n.tr(message))
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.secondaryText)
                        .multilineTextAlignment(.trailing)
                        .lineLimit(2)
                }
            }
        }
    }

    private func quotaWindowUsageText(_ usedPercent: Double?) -> String {
        guard let usedPercent else { return L10n.tr("使用比例未知") }
        return L10n.tr("已用 %d%%", Int(usedPercent.rounded()))
    }

    private func quotaWindowResetText(_ resetsAt: String?) -> String {
        guard let resetsAt else { return L10n.tr("重置时间未知") }
        return L10n.tr("%@ 重置", diagnosticTimestampText(resetsAt))
    }

    private func exportPersonalObservations() {
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.json]
        panel.canCreateDirectories = true
        panel.nameFieldStringValue = personalObservationExportFilename
        guard panel.runModal() == .OK, let url = panel.url else { return }
        settings.exportPersonalObservations(to: url)
    }

    private var personalObservationExportFilename: String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        return "modeldial-personal-observations-\(formatter.string(from: Date())).json"
    }

    private func diagnosticOverviewSection(
        _ diagnostics: BridgeDiagnosticSummary
    ) -> some View {
        settingsSection(title: "数据读取", footer: nil) {
            formRow("Codex 数据") {
                diagnosticStatusValue(
                    diagnosticStatusText(diagnostics.appServer.status),
                    isHealthy: diagnostics.appServer.status == "fresh"
                        || diagnostics.appServer.status == "cached"
                )
            }
            formRow("最近读取") {
                Text(diagnosticTimestampText(diagnostics.appServer.lastReadAt))
                    .font(Typography.rowTitle)
                    .foregroundStyle(IslandVisual.secondaryText)
            }
            formRow("读取耗时") {
                Text(
                    diagnostics.appServer.readDurationMs.map { "\($0) ms" }
                        ?? (diagnostics.appServer.status == "cached"
                            ? L10n.tr("使用缓存")
                            : L10n.tr("未记录"))
                )
                .font(Typography.rowTitle)
                .foregroundStyle(IslandVisual.secondaryText)
            }
            formRow("账号读取") {
                diagnosticStatusValue(
                    diagnosticStatusText(diagnostics.capabilities.account),
                    isHealthy: diagnostics.capabilities.account == "available"
                )
            }
            formRow("模型目录") {
                diagnosticStatusValue(
                    diagnosticStatusText(diagnostics.capabilities.modelCatalog),
                    isHealthy: diagnostics.capabilities.modelCatalog == "available"
                        || diagnostics.capabilities.modelCatalog == "not_checked"
                )
            }
            formRow("官方额度") {
                diagnosticStatusValue(
                    diagnosticStatusText(diagnostics.capabilities.rateLimits),
                    isHealthy: diagnostics.capabilities.rateLimits == "available"
                        || diagnostics.capabilities.rateLimits == "not_applicable"
                )
            }
        }
    }

    private func diagnosticHistorySection(
        _ diagnostics: BridgeDiagnosticSummary
    ) -> some View {
        let history = diagnostics.sessionHistory
        let behavior = diagnostics.behavior
        return settingsSection(title: "本地历史", footer: nil) {
            formRow("会话来源") {
                Text(L10n.tr("%d 个", history.sourceCount))
                    .font(Typography.rowTitle)
                    .foregroundStyle(IslandVisual.secondaryText)
            }
            formRow("文件采样") {
                Text(
                    L10n.tr(
                        "发现 %@ · 采样 %@ · 成功 %@ · 失败 %@ · 未知 %@",
                        String(history.discoveredFileCount),
                        String(history.sampledFileCount),
                        String(history.parsedFileCount),
                        String(history.failedFileCount),
                        String(history.unknownFileCount)
                    )
                )
                .font(Typography.micro)
                .foregroundStyle(
                    history.failedFileCount == 0
                        ? IslandVisual.secondaryText
                        : IslandColor.alertRed
                )
                .lineLimit(2)
            }
            formRow("可见起点") {
                Text(diagnosticTimestampText(history.visibleStartedAt))
                    .font(Typography.rowTitle)
                    .foregroundStyle(IslandVisual.secondaryText)
            }
            formRow("连续覆盖") {
                Text(diagnosticTimestampText(history.continuousSince))
                    .font(Typography.rowTitle)
                    .foregroundStyle(IslandVisual.secondaryText)
            }
            formRow("覆盖状态") {
                diagnosticStatusValue(
                    diagnosticCoverageText(history),
                    isHealthy: history.coverageComplete && !history.gapDetected
                )
            }
            formRow("行为字段") {
                Text(
                    behavior.coveragePercent.map {
                        L10n.tr(
                            "%d / %d 个 · %.1f%%",
                            behavior.observedWorkUnits,
                            behavior.completedWorkUnits,
                            $0
                        )
                    } ?? L10n.tr("暂无已完成工作单元")
                )
                .font(Typography.rowTitle)
                .foregroundStyle(IslandVisual.secondaryText)
            }
            formRow("重试判断") {
                Text(
                    L10n.tr(
                        "可判断 %@ · 不可判断 %@",
                        String(behavior.retryObservedEditWorkUnits),
                        String(behavior.retryIndeterminateEditWorkUnits)
                    )
                )
                .font(Typography.rowTitle)
                .foregroundStyle(IslandVisual.secondaryText)
            }
        }
    }

    private func diagnosticVersionsSection(
        _ diagnostics: BridgeDiagnosticSummary
    ) -> some View {
        let advisorReason = SettingsAdvisorReasonPresenter.presentation(
            for: diagnostics.advisorShortCircuitReason
        )
        return settingsSection(title: "决策版本", footer: nil) {
            formRow("题包") {
                Text(
                    [
                        diagnostics.versions.questionPackId,
                        diagnostics.versions.questionPackVersion,
                    ]
                    .compactMap { $0 }
                    .joined(separator: " · ")
                )
                .font(Typography.rowTitle)
                .foregroundStyle(IslandVisual.secondaryText)
            }
            formRow("建议规则") {
                Text(diagnostics.versions.advisorRulesetVersion ?? L10n.tr("未生成"))
                    .font(Typography.rowTitle)
                    .foregroundStyle(IslandVisual.secondaryText)
            }
            formRow("价格快照") {
                Text(diagnostics.versions.pricingSnapshotId ?? L10n.tr("未提供"))
                    .font(Typography.rowTitle)
                    .foregroundStyle(IslandVisual.secondaryText)
            }
            formRow("当前门禁") {
                Text(advisorReason.text)
                    .font(Typography.rowTitle)
                    .foregroundStyle(IslandVisual.secondaryText)
                    .multilineTextAlignment(.trailing)
                    .lineLimit(2)
            }
            formRow("额度归因") {
                Text(diagnosticQuotaRejectionText(diagnostics))
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.secondaryText)
                    .multilineTextAlignment(.trailing)
                    .lineLimit(3)
            }
        }
    }

    private func diagnosticStatusValue(
        _ text: String,
        isHealthy: Bool
    ) -> some View {
        HStack(spacing: 6) {
            Image(systemName: isHealthy ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                .foregroundStyle(isHealthy ? IslandColor.liveTeal : IslandColor.alertRed)
            Text(text)
                .font(Typography.rowTitle)
                .foregroundStyle(IslandVisual.secondaryText)
        }
    }

    private func diagnosticStatusText(_ status: String) -> String {
        switch status {
        case "fresh": return L10n.tr("本轮读取正常")
        case "cached": return L10n.tr("使用近期缓存")
        case "stale": return L10n.tr("使用过期缓存")
        case "available": return L10n.tr("可用")
        case "not_checked": return L10n.tr("本轮未读取")
        case "not_applicable": return L10n.tr("当前账号不适用")
        default: return L10n.tr("不可用")
        }
    }

    private func diagnosticCoverageText(
        _ history: BridgeDiagnosticSessionHistory
    ) -> String {
        if history.gapDetected || history.budgetLimitedFileCount > 0 {
            return L10n.tr("检测到覆盖空窗")
        }
        if history.upstreamRetentionRisk == "possible" {
            return L10n.tr("可能受上游保留期影响")
        }
        return history.coverageComplete
            ? L10n.tr("当前窗口完整")
            : L10n.tr("仍在建立连续覆盖")
    }

    private func diagnosticTimestampText(_ value: String?) -> String {
        guard let value else { return L10n.tr("暂无") }
        let parser = ISO8601DateFormatter()
        guard let date = parser.date(from: value) else { return value }
        let formatter = DateFormatter()
        formatter.locale = L10n.locale
        formatter.dateStyle = .medium
        formatter.timeStyle = .short
        return formatter.string(from: date)
    }

    private func diagnosticQuotaRejectionText(
        _ diagnostics: BridgeDiagnosticSummary
    ) -> String {
        if diagnostics.quotaStatus == "not_applicable" {
            return L10n.tr("当前账号没有官方额度窗口")
        }
        if diagnostics.quotaRejectedIntervals.isEmpty {
            return diagnostics.quotaStatus == "available"
                ? L10n.tr("没有拒绝区间")
                : L10n.tr("证据不足")
        }
        return diagnostics.quotaRejectedIntervals
            .sorted { $0.key < $1.key }
            .map { "\(diagnosticQuotaReasonText($0.key)) \($0.value)" }
            .joined(separator: " · ")
    }

    private func diagnosticQuotaReasonText(_ reason: String) -> String {
        switch reason {
        case "coverage_gap": return L10n.tr("覆盖空窗")
        case "interval_too_long": return L10n.tr("区间过长")
        case "snapshot_bracket_missing": return L10n.tr("快照未夹住任务")
        case "account_context_changed": return L10n.tr("账号上下文变化")
        case "unclean_workload": return L10n.tr("工作负载不干净")
        case "concurrent_main_work": return L10n.tr("存在并发主任务")
        case "active_workload": return L10n.tr("任务仍在进行")
        case "mixed_model_configuration": return L10n.tr("混合模型配置")
        case "window_changed": return L10n.tr("额度窗口变化")
        case "missing_counter": return L10n.tr("额度计数缺失")
        case "counter_decreased": return L10n.tr("额度计数回退")
        case "below_resolution": return L10n.tr("变化低于上报精度")
        default: return L10n.tr("其他原因")
        }
    }

    private func copyDiagnosticSummary(_ diagnostics: BridgeDiagnosticSummary) {
        let history = diagnostics.sessionHistory
        let behavior = diagnostics.behavior
        let lines = [
            "ModelDial DiagnosticSummaryV1",
            "generated_at: \(diagnostics.generatedAt)",
            "overall_status: \(diagnostics.overallStatus)",
            "app_server: \(diagnostics.appServer.status)",
            "account: \(diagnostics.capabilities.account)",
            "model_catalog: \(diagnostics.capabilities.modelCatalog)",
            "rate_limits: \(diagnostics.capabilities.rateLimits)",
            "files: discovered=\(history.discoveredFileCount), sampled=\(history.sampledFileCount), parsed=\(history.parsedFileCount), failed=\(history.failedFileCount), unknown=\(history.unknownFileCount), deduplicated=\(history.deduplicatedFileCount), budget_limited=\(history.budgetLimitedFileCount)",
            "coverage: visible=\(history.visibleStartedAt ?? "none"), continuous=\(history.continuousSince ?? "none"), complete=\(history.coverageComplete), gap=\(history.gapDetected), retention=\(history.upstreamRetentionRisk)",
            "behavior: observed=\(behavior.observedWorkUnits)/\(behavior.completedWorkUnits), retry_observed=\(behavior.retryObservedEditWorkUnits), retry_indeterminate=\(behavior.retryIndeterminateEditWorkUnits)",
            "question_pack: \(diagnostics.versions.questionPackId ?? "none")@\(diagnostics.versions.questionPackVersion ?? "none")",
            "advisor_ruleset: \(diagnostics.versions.advisorRulesetVersion ?? "none")",
            "pricing_snapshot: \(diagnostics.versions.pricingSnapshotId ?? "none")",
            "advisor_gate: \(diagnostics.advisorShortCircuitReason ?? "none")",
            "quota_status: \(diagnostics.quotaStatus)",
            "quota_rejections: \(diagnostics.quotaRejectedIntervals.sorted { $0.key < $1.key }.map { "\($0.key)=\($0.value)" }.joined(separator: ","))",
        ]
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.setString(lines.joined(separator: "\n"), forType: .string)
        diagnosticCopyFeedback = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
            diagnosticCopyFeedback = false
        }
    }

    private var ingressWorkspaceSection: some View {
        HStack(alignment: .top, spacing: 0) {
            ingressSourceRail
                .frame(width: Layout.sourceListWidth, alignment: .topLeading)
                .frame(maxHeight: .infinity, alignment: .topLeading)
                .padding(.trailing, 12)
            ingressDetailPane
                .padding(.leading, LayoutRhythm.standard)
                .frame(
                    minWidth: Layout.ingressReadableDetailWidth,
                    maxWidth: .infinity,
                    maxHeight: .infinity,
                    alignment: .topLeading
                )
        }
        .frame(maxHeight: .infinity, alignment: .topLeading)
    }

    private var ingressSourceRail: some View {
        ScrollView(.vertical, showsIndicators: true) {
            ingressSourceGrid
                .padding(.trailing, 2)
        }
        .frame(maxHeight: .infinity, alignment: .topLeading)
    }

    private var ingressDetailPane: some View {
        ScrollViewReader { proxy in
            ScrollView(.vertical, showsIndicators: true) {
                VStack(alignment: .leading, spacing: 0) {
                    Color.clear
                        .frame(height: 0)
                        .id(ingressDetailTopID)
                    selectedIngressDetail
                    accountQuotaSection(selectionStore.snapshot?.codexInsights?.account)
                        .padding(.top, Layout.sectionSpacing)
                }
                .padding(.trailing, LayoutRhythm.micro)
            }
            .frame(maxHeight: .infinity, alignment: .topLeading)
            .onChange(of: selectedIngressItem?.id) { _ in
                proxy.scrollTo(ingressDetailTopID, anchor: .top)
            }
        }
    }

    private var cardBackground: some View {
        RoundedRectangle(cornerRadius: IslandRadius.card)
            .fill(IslandVisual.surfaceSubtle)
            .overlay(
                RoundedRectangle(cornerRadius: IslandRadius.card)
                    .strokeBorder(IslandVisual.hairline, lineWidth: 0.5)
            )
    }

    private func statusText(for scheduler: BridgeSchedulerConfig) -> String {
        if !scheduler.enabled { return L10n.tr("自动扫描已关闭。") }
        if scheduler.mode == "daily" {
            return L10n.tr(
                "当前为每日扫描，每天 %@。",
                timeText(hour: scheduler.dailyHour, minute: scheduler.dailyMinute)
            )
        }
        if scheduler.mode == "weekly" {
            return L10n.tr(
                "当前为每周扫描，%@ %@。",
                L10n.tr(weekdayTitle(scheduler.weeklyWeekday)),
                timeText(hour: scheduler.weeklyHour, minute: scheduler.weeklyMinute)
            )
        }
        return L10n.tr("当前为自动扫描，每 %d 分钟一次。", scheduler.intervalSeconds / 60)
    }

    private var schedulerFooterText: String? {
        guard let persistedScheduler = settings.draftConfig?.scheduler else { return nil }
        let scheduler = BridgeSchedulerConfig(
            enabled: schedulerEnabled,
            mode: schedulerMode,
            intervalSeconds: intervalSeconds,
            dailyHour: schedulerMode == "daily" ? timeParts(from: dailyTime).hour : persistedScheduler.dailyHour,
            dailyMinute: schedulerMode == "daily" ? timeParts(from: dailyTime).minute : persistedScheduler.dailyMinute,
            weeklyWeekday: schedulerMode == "weekly" ? weeklyWeekday : persistedScheduler.weeklyWeekday,
            weeklyHour: schedulerMode == "weekly" ? timeParts(from: weeklyTime).hour : persistedScheduler.weeklyHour,
            weeklyMinute: schedulerMode == "weekly" ? timeParts(from: weeklyTime).minute : persistedScheduler.weeklyMinute,
            scheduledEvaluationProfileId: persistedScheduler.scheduledEvaluationProfileId
        )
        return L10n.tr(
            "%@ 定时扫描仅在 modeldial 运行期间生效。",
            statusText(for: scheduler)
        )
    }

    private func scheduledRunAbsoluteText(_ status: ScheduledRunStatus) -> String {
        guard let date = status.date else { return L10n.tr(status.absoluteText) }
        let formatter = DateFormatter()
        formatter.locale = L10n.locale
        formatter.dateStyle = .medium
        formatter.timeStyle = .short
        return formatter.string(from: date)
    }

    private func scheduledRunRelativeText(_ status: ScheduledRunStatus) -> String {
        guard let date = status.date else { return L10n.tr(status.relativeText) }
        let formatter = RelativeDateTimeFormatter()
        formatter.locale = L10n.locale
        formatter.unitsStyle = .full
        return formatter.localizedString(for: date, relativeTo: Date())
    }

    private func scheduledRunReasonText(_ status: ScheduledRunStatus) -> String? {
        guard status.reason != "自动扫描已关闭" else { return nil }
        if status.date != nil {
            let profileLabel = selectionStore.scheduledEvaluationProfile.map {
                localizedEvaluationProfileLabel($0)
            } ?? L10n.EvaluationProfile.label(id: "full", fallback: "")
            return L10n.tr(status.reason, profileLabel)
        }
        return L10n.tr(status.reason)
    }

    private var displayFooterText: String? {
        guard let current = DisplayInfo.currentTarget() else { return nil }
        return current.notch.hasNotch
            ? L10n.tr("当前为刘海屏布局。")
            : L10n.tr("当前为外接屏布局。")
    }

    @ViewBuilder
    private var schedulerDetailControls: some View {
        if settings.draftConfig?.scheduler != nil {
            switch schedulerMode {
            case "interval":
                formRow("间隔") {
                    Picker("间隔", selection: intervalSecondsBinding) {
                        Text("30 分钟").tag(1800)
                        Text("1 小时").tag(3600)
                        Text("2 小时").tag(7200)
                    }
                    .labelsHidden()
                    .pickerStyle(.menu)
                    .frame(width: Layout.shortControlWidth)
                }
            case "daily":
                formRow("时间") {
                    DatePicker(
                        "",
                        selection: dailyTimeBinding,
                        displayedComponents: .hourAndMinute
                    )
                    .labelsHidden()
                    .frame(width: Layout.mediumControlWidth, alignment: .trailing)
                }
            case "weekly":
                Group {
                    formRow("星期") {
                        Picker("星期", selection: weeklyWeekdayBinding) {
                            ForEach(1...7, id: \.self) { value in
                                Text(L10n.tr(weekdayTitle(value))).tag(value)
                            }
                        }
                        .labelsHidden()
                        .pickerStyle(.menu)
                        .frame(width: Layout.shortControlWidth)
                    }
                    formRow("时间") {
                        DatePicker(
                            "",
                            selection: weeklyTimeBinding,
                            displayedComponents: .hourAndMinute
                        )
                        .labelsHidden()
                        .frame(width: Layout.mediumControlWidth, alignment: .trailing)
                    }
                }
            default:
                EmptyView()
            }
        }
    }

    private func formRow<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(spacing: 0) {
            HStack(alignment: .center, spacing: 16) {
                Text(LocalizedStringKey(title))
                    .font(Typography.rowTitle)
                    .foregroundStyle(IslandVisual.primaryText)
                    .lineLimit(1)
                    .frame(width: 120, alignment: .leading)
                Spacer(minLength: 0)
                content()
            }
            .padding(LayoutRhythm.standard)

            Divider()
                .overlay(IslandVisual.hairline)
                .padding(.leading, 16)
        }
    }

    private func settingsSection<Content: View>(
        title: String,
        footer: String?,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: LayoutRhythm.compact) {
            Text(LocalizedStringKey(title))
                .font(Typography.sectionTitle)
                .foregroundStyle(IslandVisual.primaryText)

            VStack(spacing: 0) {
                content()
            }
            .background(
                RoundedRectangle(cornerRadius: IslandRadius.card)
                    .fill(IslandVisual.surfaceSubtle)
                    .overlay(
                        RoundedRectangle(cornerRadius: IslandRadius.card)
                            .strokeBorder(IslandVisual.hairline, lineWidth: 0.5)
                    )
            )
            .clipShape(RoundedRectangle(cornerRadius: IslandRadius.card))

            if let footer, !footer.isEmpty {
                Text(LocalizedStringKey(footer))
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.tertiaryText)
                    .padding(.horizontal, LayoutRhythm.micro)
            }
        }
    }

    private var settingsIngressPresentation: SettingsIngressPresenter.Presentation {
        SettingsIngressPresenter.present(
            ingress: settings.draftConfig?.modelIngress,
            providerCatalog: providerCatalog,
            scanScope: selectionStore.snapshot?.settingsProjection.scanScope,
            candidateProjections: selectionStore.snapshot?.settingsProjection.candidates ?? [],
            hasResumableRun: selectionStore.snapshot?.runtime.hasResumableRun == true,
            customProviderID: customEndpointProviderID
        )
    }

    private var localIngressWorkspaceItems: [IngressWorkspaceItem] {
        settingsIngressPresentation.localWorkspaceItems
    }

    private var configuredAPIWorkspaceItems: [IngressWorkspaceItem] {
        settingsIngressPresentation.apiWorkspaceItems
    }

    private var providerCatalog: [BridgeProviderCatalogProvider] {
        selectionStore.snapshot?.config.providerCatalog ?? []
    }

    private var detectedLocalProviders: [BridgeDetectedLocalProvider] {
        selectionStore.snapshot?.config.detectedLocalProviders ?? []
    }

    private var connectableFeaturedProviderCatalog: [BridgeProviderCatalogProvider] {
        settingsIngressPresentation.connectableFeaturedProviders
    }

    private var overflowProviderCatalog: [BridgeProviderCatalogProvider] {
        settingsIngressPresentation.overflowProviders
    }

    private var connectableOverflowProviderCatalog: [BridgeProviderCatalogProvider] {
        settingsIngressPresentation.connectableOverflowProviders
    }

    private var endpointProviderOptions: [EndpointProviderOption] {
        settingsIngressPresentation.endpointProviderOptions
    }

    private var selectedEndpointCatalogProvider: BridgeProviderCatalogProvider? {
        providerCatalogProvider(id: endpointProvider)
    }

    private var selectedIngressItem: IngressWorkspaceItem? {
        settingsIngressPresentation.selectedWorkspaceItem(id: selectedIngressConnectionID)
    }

    private var ingressSourceGrid: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                sectionTitle("接入来源")
                Spacer()
                if !overflowProviderCatalog.isEmpty {
                    Menu {
                        ForEach(connectableOverflowProviderCatalog) { provider in
                            Button(provider.displayName) {
                                openNewEndpointEditor(providerID: provider.providerId)
                            }
                        }
                    } label: {
                        HStack(spacing: LayoutRhythm.micro) {
                            Text("更多")
                            Image(systemName: "chevron.down")
                                .font(Typography.micro)
                        }
                        .font(Typography.label)
                        .foregroundStyle(IslandVisual.tertiaryText)
                    }
                    .menuStyle(.borderlessButton)
                    .menuIndicator(.hidden)
                    .help(L10n.tr("更多提供商"))
                }
            }
            Text("优先复用本机登录态，再按需接入 API 或自定义服务。")
                .font(Typography.settingsCardBody)
                .foregroundStyle(IslandVisual.tertiaryText)
            localIngressSection
            commonProviderCatalogSection
            customEndpointSection
            configuredEndpointConnectionsSection
        }
    }

    private var localIngressSection: some View {
        VStack(alignment: .leading, spacing: LayoutRhythm.compact) {
            Text("本机已有登录态")
                .font(Typography.sectionLabel)
                .foregroundStyle(IslandVisual.tertiaryText)

            if detectedLocalProviders.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text("本机探测暂不可用")
                        .font(Typography.settingsCardTitle)
                        .foregroundStyle(IslandVisual.primaryText)
                    Text("当前仍可通过下方 provider 或自定义 endpoint 接入。")
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.tertiaryText)
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(cardBackground)
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(detectedLocalProviders.enumerated()), id: \.element.id) { index, provider in
                        localProviderDetectionCard(provider)
                        if index < detectedLocalProviders.count - 1 {
                            Rectangle()
                                .fill(IslandVisual.hairline)
                                .frame(height: 0.5)
                                .padding(.leading, 52)
                        }
                    }
                }
                .background(cardBackground)
                .clipShape(RoundedRectangle(cornerRadius: IslandRadius.card))
            }
        }
    }

    @ViewBuilder
    private func localProviderDetectionCard(_ provider: BridgeDetectedLocalProvider) -> some View {
        let connection = localIngressWorkspaceItems.first {
            $0.connection.id == provider.connectionId
        }
        let isImported = provider.detected
            && connection.map { isLocalConnectionImported($0) } == true

        if isImported {
            Button {
                selectedIngressConnectionID = provider.connectionId
            } label: {
                localProviderDetectionRow(provider, isImported: true)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .frame(maxWidth: .infinity, alignment: .leading)
            .islandPointerOnHover()
        } else {
            localProviderDetectionRow(provider, isImported: false)
        }
    }

    private func localProviderDetectionRow(
        _ provider: BridgeDetectedLocalProvider,
        isImported: Bool
    ) -> some View {
        HStack(spacing: LayoutRhythm.standard) {
            Image(systemName: provider.providerId == "codex" ? "terminal.fill" : "sparkles")
                .foregroundStyle(provider.detected ? IslandColor.liveTeal : IslandVisual.tertiaryText)
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 4) {
                Text(provider.displayName)
                    .font(Typography.rowTitle)
                    .foregroundStyle(IslandVisual.primaryText)
                    .lineLimit(1)
                Text(localProviderStatusText(provider, isImported: isImported))
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.secondaryText)
                    .lineLimit(1)
                    .truncationMode(.tail)
                if settings.importingLocalProviderID == provider.providerId {
                    Text("正在验证本机登录态…")
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.secondaryText)
                } else if settings.localImportFeedbackProviderID == provider.providerId,
                          let message = settings.localImportMessage {
                    Text(L10n.tr(message))
                        .font(Typography.micro)
                        .foregroundStyle(
                            settings.localImportSucceeded == true
                                ? IslandColor.liveTeal
                                : IslandColor.alertRed
                        )
                        .lineLimit(2)
                }
            }
            .layoutPriority(1)
            Spacer(minLength: 0)
            if provider.status == "adapter_unavailable" {
                Text("适配器待接入")
                    .font(Typography.chip)
                    .foregroundStyle(IslandColor.alertAmber)
                    .fixedSize(horizontal: true, vertical: false)
            } else if isImported {
                Image(systemName: "chevron.right")
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.tertiaryText)
                    .fixedSize(horizontal: true, vertical: false)
            } else if provider.importable {
                Button {
                    settings.importLocalProvider(providerID: provider.providerId)
                } label: {
                    Text(
                        L10n.tr(
                            settings.importingLocalProviderID == provider.providerId
                                ? "验证中…"
                                : "导入"
                        )
                    )
                }
                .buttonStyle(IslandActionButtonStyle(.primary))
                .fixedSize(horizontal: true, vertical: false)
                .disabled(
                    settings.isSaving
                        || settings.endpoint.isRunning
                        || settings.importingLocalProviderID != nil
                )
            } else {
                Text("未检测到")
                    .font(Typography.chip)
                    .foregroundStyle(IslandVisual.tertiaryText)
                    .fixedSize(horizontal: true, vertical: false)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
    }

    private func localProviderStatusText(
        _ provider: BridgeDetectedLocalProvider,
        isImported: Bool
    ) -> String {
        if isImported { return L10n.tr("已接入") }
        if provider.status == "adapter_unavailable" {
            return L10n.tr("本机适配器尚未接入")
        }
        if provider.status == "login_check_required" {
            return L10n.tr(provider.statusMessage)
        }
        if provider.detected { return L10n.tr("已检测到本机登录态") }
        return L10n.tr("未检测到可导入登录态")
    }

    private var commonProviderCatalogSection: some View {
        VStack(alignment: .leading, spacing: LayoutRhythm.compact) {
            Text("常用 API 提供商")
                .font(Typography.sectionLabel)
                .foregroundStyle(IslandVisual.tertiaryText)

            if connectableFeaturedProviderCatalog.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text("目录暂未就绪")
                        .font(Typography.settingsCardTitle)
                        .foregroundStyle(IslandVisual.primaryText)
                    Text("稍后重载 snapshot 后，这里会展示常用提供商目录。")
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.tertiaryText)
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(cardBackground)
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(connectableFeaturedProviderCatalog.enumerated()), id: \.element.id) { index, provider in
                        providerCatalogRow(provider)
                        if index < connectableFeaturedProviderCatalog.count - 1 {
                            Rectangle()
                                .fill(IslandVisual.hairline)
                                .frame(height: 0.5)
                                .padding(.leading, 34)
                        }
                    }
                }
                .background(cardBackground)
                .clipShape(RoundedRectangle(cornerRadius: IslandRadius.card))
            }
        }
    }

    private var customEndpointSection: some View {
        VStack(alignment: .leading, spacing: LayoutRhythm.compact) {
            Text("自定义 endpoint")
                .font(Typography.sectionLabel)
                .foregroundStyle(IslandVisual.tertiaryText)

            Button {
                openNewEndpointEditor(providerID: customEndpointProviderID)
            } label: {
                HStack(alignment: .center, spacing: LayoutRhythm.standard) {
                    Image(systemName: "slider.horizontal.3")
                        .font(Typography.icon)
                        .foregroundStyle(IslandColor.endpoint)
                        .frame(width: 20)
                    VStack(alignment: .leading, spacing: LayoutRhythm.micro) {
                        Text("高级连接配置")
                            .font(Typography.rowTitle)
                            .foregroundStyle(IslandVisual.primaryText)
                            .lineLimit(1)
                        Text("手动配置 Base URL、API 格式和密钥。")
                            .font(Typography.micro)
                            .foregroundStyle(IslandVisual.secondaryText)
                            .lineLimit(1)
                            .truncationMode(.tail)
                    }
                    Spacer(minLength: 0)
                    Text("新建")
                        .font(Typography.chip)
                        .foregroundStyle(IslandColor.endpoint)
                        .fixedSize(horizontal: true, vertical: false)
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(cardBackground)
            }
            .buttonStyle(.plain)
            .islandPointerOnHover()
        }
    }

    @ViewBuilder
    private var configuredEndpointConnectionsSection: some View {
        VStack(alignment: .leading, spacing: LayoutRhythm.compact) {
            Text("已配置连接")
                .font(Typography.sectionLabel)
                .foregroundStyle(IslandVisual.tertiaryText)

            if configuredAPIWorkspaceItems.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text("还没有已配置的 endpoint")
                        .font(Typography.settingsCardTitle)
                        .foregroundStyle(IslandVisual.primaryText)
                    Text("从上方常用提供商或自定义 endpoint 开始，保存后会出现在这里。")
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.tertiaryText)
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(cardBackground)
            } else {
                ingressWorkspaceCardList(configuredAPIWorkspaceItems)
            }
        }
    }

    private func ingressWorkspaceCardList(_ items: [IngressWorkspaceItem]) -> some View {
        VStack(spacing: 0) {
            ForEach(Array(items.enumerated()), id: \.element.id) { index, item in
                sourceWorkspaceCard(item)
                if index < items.count - 1 {
                    Rectangle()
                        .fill(IslandVisual.hairline)
                        .frame(height: 0.5)
                        .padding(.leading, 62)
                }
            }
        }
        .background(cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: IslandRadius.card))
    }

    private func providerCatalogRow(_ provider: BridgeProviderCatalogProvider) -> some View {
        let connectionCount = providerConnectionCount(for: provider.providerId)
        let familyPreview = provider.families.prefix(2).map(\.displayName).joined(separator: " · ")
        let summary: String
        if familyPreview.isEmpty {
            summary = provider.defaultBaseUrl == nil
                ? L10n.tr("支持模型发现 · 地址后填")
                : L10n.tr("支持模型发现 · 地址已预置")
        } else {
            summary = L10n.tr(
                "%@ · %d 个模型簇",
                familyPreview,
                provider.families.count
            )
        }

        return Button {
            openNewEndpointEditor(providerID: provider.providerId)
        } label: {
            HStack(spacing: 10) {
                Circle()
                    .fill(providerAccent(for: provider.providerId))
                    .frame(width: 6, height: 6)
                VStack(alignment: .leading, spacing: 3) {
                    Text(provider.displayName)
                        .font(Typography.label)
                        .foregroundStyle(IslandVisual.primaryText)
                        .lineLimit(1)
                        .truncationMode(.tail)
                    Text(summary)
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.secondaryText)
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 8)
                if connectionCount > 0 {
                    Text(L10n.tr("已接入 %d", connectionCount))
                        .font(Typography.micro)
                        .foregroundStyle(IslandColor.liveTeal)
                        .fixedSize(horizontal: true, vertical: false)
                }
                Image(systemName: "chevron.right")
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.hintText)
            }
            .padding(.horizontal, 12)
            .frame(maxWidth: .infinity, minHeight: 52, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .islandPointerOnHover()
    }

    private func sourceWorkspaceCard(_ item: IngressWorkspaceItem) -> some View {
        let isSelected = selectedIngressItem?.id == item.id
        let enabledCount = enabledCandidateCount(for: item.connection)
        let totalCount = item.connection.modelCandidates.count
        let modelCount = item.source.mode == "api"
            ? modelFamilyGroups(for: item.connection).count
            : uniqueModelNames(in: item.connection.modelCandidates).count
        let inventoryText = item.source.mode == "api"
            ? L10n.tr("%d 个模型簇，%d/%d 已启用", modelCount, enabledCount, totalCount)
            : L10n.tr("%d 个模型，%d/%d 已启用", modelCount, enabledCount, totalCount)
        let readiness = ingressReadiness(for: item)
        return Button {
            selectedIngressConnectionID = item.id
        } label: {
            HStack(spacing: 10) {
                Circle()
                    .fill(readinessColor(readiness.state))
                    .frame(width: 6, height: 6)

                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 6) {
                        Text(item.source.mode == "api" ? item.connection.name : item.source.title)
                            .font(Typography.label)
                            .foregroundStyle(IslandVisual.primaryText)
                            .lineLimit(1)
                        Spacer(minLength: 0)
                        readinessStatusBadge(readiness)
                    }
                    Text(inventoryText)
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.tertiaryText)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
            }
            .padding(.horizontal, 12)
            .frame(maxWidth: .infinity, minHeight: 52, alignment: .leading)
            .contentShape(Rectangle())
            .background(isSelected ? IslandVisual.surfaceStrong : Color.clear)
            .overlay(alignment: .leading) {
                if isSelected {
                    RoundedRectangle(cornerRadius: 1)
                        .fill(IslandColor.interaction)
                        .frame(width: 2, height: 30)
                }
            }
        }
        .buttonStyle(.plain)
        .islandPointerOnHover()
    }

    @ViewBuilder
    private var selectedIngressDetail: some View {
        if let item = selectedIngressItem {
            let readiness = ingressReadiness(for: item)
            VStack(alignment: .leading, spacing: 0) {
                ingressConnectionHeader(item, readiness: readiness)
                    .padding(LayoutRhythm.standard)

                Rectangle()
                    .fill(IslandVisual.hairline)
                    .frame(height: 0.5)

                if item.source.mode == "api" {
                    endpointConnectionCard(item.connection)
                } else {
                    localConnectionDetailCard(item)
                }
            }
        }
    }

    private func ingressConnectionHeader(
        _ item: IngressWorkspaceItem,
        readiness: IngressReadiness
    ) -> some View {
        let connection = item.connection
        let accent = ingressAccent(for: item.source)
        let title = item.source.mode == "api" ? connection.name : item.source.title
        return VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 6) {
                    HStack(spacing: 8) {
                        Circle()
                            .fill(accent)
                            .frame(width: 8, height: 8)
                        Text(title)
                            .font(Typography.settingsCardTitle)
                            .foregroundStyle(IslandVisual.primaryText)
                            .lineLimit(1)
                        readinessStatusBadge(readiness)
                    }
                    Text(readiness.detail)
                        .font(Typography.settingsCardBody)
                        .foregroundStyle(IslandVisual.secondaryText)
                }
                Spacer(minLength: 0)
                Toggle(
                    L10n.tr("启用 %@", title),
                    isOn: Binding(
                        get: { connection.enabled },
                        set: { settings.setConnectionEnabled(connectionID: connection.id, enabled: $0) }
                    )
                )
                .labelsHidden()
                .toggleStyle(.switch)
                .controlSize(.small)
                .disabled(
                    settings.isSaving
                        || settings.endpoint.isRunning
                        || (item.source.mode == "local" && !isLocalConnectionImported(item))
                )
            }

            HStack(alignment: .center, spacing: 14) {
                ingressReadinessTrack(readiness)
                ingressReadinessAction(readiness, item: item)
            }
        }
    }

    private func ingressDetailMetadataStrip(_ metrics: [IngressMetric]) -> some View {
        HStack(spacing: 0) {
            ForEach(Array(metrics.enumerated()), id: \.element.id) { index, metric in
                VStack(alignment: .leading, spacing: LayoutRhythm.micro) {
                    Text(L10n.tr(metric.value))
                        .font(Typography.label)
                        .foregroundStyle(IslandVisual.primaryText)
                        .monospacedDigit()
                        .lineLimit(1)
                        .truncationMode(.tail)
                    Text(L10n.tr(metric.label))
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.tertiaryText)
                        .lineLimit(1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)

                if index < metrics.count - 1 {
                    Rectangle()
                        .fill(IslandVisual.hairline)
                        .frame(width: 0.5, height: 28)
                        .padding(.horizontal, LayoutRhythm.compact)
                }
            }
        }
        .padding(.vertical, LayoutRhythm.micro)
    }

    private func ingressReadiness(for item: IngressWorkspaceItem) -> IngressReadiness {
        let projection = selectionStore.snapshot?.settingsProjection.connections.first {
            $0.connectionId == item.connection.id
        }
        return IngressReadiness.present(projection)
    }

    private func readinessStatusBadge(_ readiness: IngressReadiness) -> some View {
        Text(readiness.title)
            .font(Typography.micro)
            .foregroundStyle(readinessColor(readiness.state))
    }

    private func ingressReadinessTrack(_ readiness: IngressReadiness) -> some View {
        let steps = ["连接", "选择档位", "扫描一次", "可参与推荐"].map {
            L10n.tr($0)
        }
        return HStack(spacing: 6) {
            ForEach(Array(steps.enumerated()), id: \.offset) { index, step in
                HStack(spacing: 5) {
                    Circle()
                        .fill(readinessStepColor(index: index, readiness: readiness))
                        .frame(width: 6, height: 6)
                    Text(step)
                        .font(Typography.micro)
                        .foregroundStyle(
                            index < readiness.completedStepCount
                                ? IslandVisual.secondaryText
                                : IslandVisual.tertiaryText
                        )
                        .lineLimit(1)
                        .fixedSize(horizontal: true, vertical: false)
                }
                if index < steps.count - 1 {
                    Rectangle()
                        .fill(IslandVisual.hairline)
                        .frame(maxWidth: .infinity)
                        .frame(height: 0.5)
                }
            }
        }
    }

    @ViewBuilder
    private func ingressReadinessAction(_ readiness: IngressReadiness, item: IngressWorkspaceItem) -> some View {
        switch readiness.action {
        case .manageConnection:
            Button("完善连接配置") {
                openEndpointEditor(item.connection)
            }
            .buttonStyle(IslandActionButtonStyle(.primary))
        case .testConnection:
            if let candidate = item.connection.modelCandidates.first {
                Button(L10n.tr(
                    settings.endpoint.isTesting(
                        connectionID: item.connection.id,
                        modelID: candidate.modelId
                    ) ? "测试中" : "测试连接"
                )) {
                    settings.testConnection(connectionID: item.connection.id, modelID: candidate.modelId)
                }
                .buttonStyle(IslandActionButtonStyle(.primary))
                .disabled(settings.isSaving || settings.endpoint.isRunning)
            }
        case .selectModels:
            HStack(spacing: 8) {
                Image(systemName: "arrow.down")
                Text(L10n.tr(
                    item.connection.modelCandidates.isEmpty
                        ? "请先添加模型"
                        : "请在下方开启至少一个模型"
                ))
            }
            .font(Typography.micro)
            .foregroundStyle(IslandColor.alertAmber)
        case .scanBaseline:
            Button("扫描所选档位") {
                selectionStore.startIngressCandidateScan(
                    candidateIDs: readiness.enabledCandidateIDs,
                    conflictPresentation: .settings
                )
            }
            .buttonStyle(IslandActionButtonStyle(.primary))
            .disabled(settings.isSaving)
        case .none:
            EmptyView()
        }
    }

    private func readinessStepColor(index: Int, readiness: IngressReadiness) -> Color {
        if index < readiness.completedStepCount {
            if readiness.state == .ready, index == 3 {
                return IslandColor.liveTeal
            }
            return IslandVisual.tertiaryText
        }
        if index == readiness.completedStepCount, readiness.state != .disabled {
            return readinessColor(readiness.state)
        }
        return .white.opacity(0.16)
    }

    private func readinessColor(_ state: IngressReadinessState) -> Color {
        switch state {
        case .ready: return IslandColor.liveTeal
        case .needsBaseline: return IslandColor.alertAmber
        case .needsConfiguration, .needsConnectionTest, .needsModelSelection:
            return IslandColor.alertAmber
        case .disabled: return .white.opacity(0.38)
        }
    }

    private func localConnectionDetailCard(_ item: IngressWorkspaceItem) -> some View {
        let connection = item.connection
        let accent = ingressAccent(for: item.source)
        let enabledCandidates = enabledCandidateCount(for: connection)
        return VStack(alignment: .leading, spacing: 10) {
            if !isLocalConnectionImported(item) {
                Text("请先在左侧完成本机登录态验证，验证通过后才能选择模型与档位。")
                    .font(Typography.micro)
                    .foregroundStyle(IslandColor.alertAmber)
            } else {
                if item.source.kind == "grok_build" {
                    Text("Grok 4.5 可分别开启 low、medium、high；不支持 xhigh。")
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.tertiaryText)
                }
                if item.source.kind == "claude_code" {
                    Text("Claude Sonnet 可分别开启 low、medium、high；CLI 未提供独立 Reason Tok，因此不会参与 Reason Tok 推荐。")
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.tertiaryText)
                }

                ingressDetailMetadataStrip([
                    IngressMetric(
                        id: "model-entries",
                        value: "\(uniqueModelNames(in: connection.modelCandidates).count)",
                        label: "模型条目"
                    ),
                    IngressMetric(
                        id: "scan-configurations",
                        value: "\(enabledCandidates)/\(connection.modelCandidates.count)",
                        label: "扫描档位"
                    ),
                    IngressMetric(id: "access-mode", value: "本机登录态", label: "接入方式"),
                ])

                Rectangle()
                    .fill(IslandVisual.hairline)
                    .frame(height: 0.5)

                if item.source.kind == "codex" {
                    localModelDiscoverySection(connection: connection, accent: accent)
                }

                Text("模型与档位")
                    .font(Typography.label)
                    .foregroundStyle(IslandVisual.secondaryText)

                VStack(spacing: 0) {
                    ForEach(modelFamilyGroups(for: connection)) { family in
                        if family.candidates.count == 1,
                           family.candidates[0].scanProfile == "default" {
                            singleVariantRow(family.candidates[0], connection: connection, accent: accent)
                        } else {
                            profileFamilyCard(family, connection: connection, accent: accent)
                        }
                    }
                }
            }

        }
        .padding(.horizontal, LayoutRhythm.standard)
        .padding(.vertical, 12)
    }

    private func isLocalConnectionImported(_ item: IngressWorkspaceItem) -> Bool {
        guard item.source.mode == "local" else { return true }
        guard item.source.enabled && item.connection.enabled else { return false }
        return item.source.kind != "claude_code"
            || item.connection.localLoginVerified == true
    }

    private func localModelDiscoverySection(
        connection: BridgeIngressConnection,
        accent: Color
    ) -> some View {
        let newCandidates = settings.localModelDiscoveryCandidates.filter { !$0.configured }
        return VStack(alignment: .leading, spacing: LayoutRhythm.compact) {
            HStack(spacing: LayoutRhythm.compact) {
                VStack(alignment: .leading, spacing: LayoutRhythm.micro) {
                    Text("Codex 可用模型")
                        .font(Typography.label)
                        .foregroundStyle(IslandVisual.primaryText)
                    Text("从当前登录账号的服务端目录获取；发现项不会自动参与扫描。")
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.tertiaryText)
                }
                Spacer(minLength: 0)
                Button(L10n.tr("发现可用模型")) {
                    settings.discoverLocalModels(providerID: "codex")
                }
                .buttonStyle(IslandActionButtonStyle(.secondary))
                .disabled(settings.isLocalModelDiscoveryRunning)
            }

            if let message = settings.localModelDiscoveryMessage {
                Text(L10n.tr(message))
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.secondaryText)
            }

            if !newCandidates.isEmpty {
                VStack(spacing: 0) {
                    ForEach(Array(newCandidates.enumerated()), id: \.element.id) { index, candidate in
                        HStack(spacing: LayoutRhythm.standard) {
                            Circle()
                                .fill(accent.opacity(0.88))
                                .frame(width: 6, height: 6)
                            VStack(alignment: .leading, spacing: LayoutRhythm.micro) {
                                Text(candidate.modelDisplayName)
                                    .font(Typography.settingsCardBody)
                                    .foregroundStyle(IslandVisual.primaryText)
                                HStack(spacing: 6) {
                                    Text(candidate.scanProfile)
                                    if candidate.isDefault {
                                        Text("服务端默认")
                                    }
                                }
                                .font(Typography.micro)
                                .foregroundStyle(IslandVisual.tertiaryText)
                            }
                            Spacer(minLength: 0)
                            Button("加入") {
                                settings.addDiscoveredLocalCandidate(
                                    connectionID: connection.id,
                                    candidate: candidate
                                )
                            }
                            .buttonStyle(IslandActionButtonStyle(.secondary))
                        }
                        .padding(.horizontal, LayoutRhythm.standard)
                        .frame(minHeight: 56)
                        if index < newCandidates.count - 1 {
                            Rectangle()
                                .fill(IslandVisual.hairline)
                                .frame(height: 0.5)
                                .padding(.leading, LayoutRhythm.standard)
                        }
                    }
                }
                .background(Color.white.opacity(0.025))
                .clipShape(RoundedRectangle(cornerRadius: IslandRadius.control))
            }
        }
        .padding(.vertical, LayoutRhythm.compact)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(IslandVisual.hairline)
                .frame(height: 0.5)
        }
    }

    private func ingressAccent(for source: BridgeIngressSource) -> Color {
        switch source.kind {
        case "codex": return IslandColor.codex
        case "claude_code": return IslandColor.claude
        default: return IslandColor.endpoint
        }
    }

    private func providerAccent(for providerID: String) -> Color {
        switch providerID {
        case "openai", "openrouter": return IslandColor.codex
        case "anthropic": return IslandColor.claude
        case "deepseek", "gemini", "xai", "moonshot", "zhipu", "z-ai", "minimax":
            return IslandColor.endpoint
        default:
            return IslandColor.alertAmber
        }
    }

    private func providerCatalogProvider(id: String) -> BridgeProviderCatalogProvider? {
        settingsIngressPresentation.provider(id: id)
    }

    private func providerConnectionCount(for providerID: String) -> Int {
        settingsIngressPresentation.providerConnectionCount(for: providerID)
    }

    private func endpointConnectionCard(_ connection: BridgeIngressConnection) -> some View {
        let enabledCandidates = enabledCandidateCount(for: connection)
        let status = L10n.tr(connection.lastTestMessage ?? "尚未测试")
        return VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: LayoutRhythm.micro) {
                Text(L10n.tr(connection.baseUrl ?? "未配置 Base URL"))
                    .font(Typography.settingsCardBody)
                    .foregroundStyle(IslandVisual.secondaryText)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Text("\(endpointFormatTitle(connection.apiFormat)) · \(endpointPresetTitle(connection.providerPreset))")
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.secondaryText)
            }

            ingressDetailMetadataStrip([
                IngressMetric(
                    id: "model-entries",
                    value: "\(modelFamilyGroups(for: connection).count)",
                    label: "模型簇"
                ),
                IngressMetric(
                    id: "scan-configurations",
                    value: "\(enabledCandidates)/\(connection.modelCandidates.count)",
                    label: "扫描档位"
                ),
                IngressMetric(id: "connection-status", value: status, label: "连接状态"),
            ])

            if !connection.modelCandidates.isEmpty {
                Text("模型与档位")
                    .font(Typography.label)
                    .foregroundStyle(IslandVisual.secondaryText)

                if connection.apiFormat == "anthropic_messages" {
                    Text("low、medium、high、xhigh、max 对应 Anthropic adaptive thinking 的原生 effort。")
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.tertiaryText)
                }
                if connection.providerId == "moonshot",
                   connection.modelCandidates.contains(where: { candidate in
                       candidate.modelId == "k3" && candidate.scanProfile != "default"
                   }) {
                    Text("K3 原生提供 low、high、max，通过 reasoning_effort 发送；medium 与 xhigh 属于服务端兼容别名，不作为独立档位。")
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.tertiaryText)
                }

                VStack(spacing: 0) {
                    ForEach(modelFamilyGroups(for: connection)) { family in
                        apiModelFamilyCard(family, connection: connection)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }

            HStack(spacing: 10) {
                Button("编辑连接") {
                    openEndpointEditor(connection)
                }
                .buttonStyle(IslandActionButtonStyle(.secondary))
                .disabled(settings.isSaving || settings.endpoint.isRunning)
                Button("自动发现模型") {
                    openEndpointEditor(connection)
                    settings.discoverModels(connectionID: connection.id)
                }
                .buttonStyle(IslandActionButtonStyle(.secondary))
                .disabled(settings.isSaving || settings.endpoint.isRunning)
                Spacer()
                Text(L10n.tr(connection.secretStorageSummaryText))
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.tertiaryText)
                Menu {
                    Button(L10n.tr(connection.enabled ? "停用连接" : "启用连接")) {
                        settings.setConnectionEnabled(
                            connectionID: connection.id,
                            enabled: !connection.enabled
                        )
                    }
                    Divider()
                    Button("删除连接", role: .destructive) {
                        requestConnectionDeletion(connection)
                    }
                } label: {
                    Image(systemName: "ellipsis")
                        .frame(width: 24, height: 24)
                }
                .menuStyle(.borderlessButton)
                .menuIndicator(.hidden)
                .help(L10n.tr("更多连接操作"))
                .disabled(settings.isSaving || settings.endpoint.isRunning)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
    }

    private func apiModelFamilyCard(
        _ family: ModelFamilyGroup,
        connection: BridgeIngressConnection
    ) -> some View {
        VStack(spacing: 0) {
            modelFamilyHeader(
                family,
                connection: connection,
                accent: IslandColor.endpoint,
                itemLabel: "档位",
                onRemove: {
                    requestModelFamilyRemoval(family, connection: connection)
                }
            )
            if expandedModelFamilyIDs.contains(family.id) {
                VStack(spacing: 0) {
                    ForEach(family.candidates) { candidate in
                        apiModelVariantRow(candidate, connection: connection)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(IslandVisual.hairline)
                .frame(height: 0.5)
        }
    }

    private func apiModelVariantRow(
        _ candidate: BridgeIngressModelCandidate,
        connection: BridgeIngressConnection
    ) -> some View {
        let evidence = selectionStore.snapshot?.dashboard.cards.first {
            $0.candidateId == candidate.id
        }
        let evidencePresentation = SettingsCandidatePresenter.evidencePresentation(for: evidence)
        let isTesting = settings.endpoint.isTesting(
            connectionID: connection.id,
            modelID: candidate.modelId
        )
        let feedback = settings.endpoint.feedback(
            connectionID: connection.id,
            modelID: candidate.modelId
        )
        return VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 12) {
                Toggle(
                    "",
                    isOn: Binding(
                        get: { candidate.enabled },
                        set: { enabled in
                            settings.setModelCandidateEnabled(
                                connectionID: candidate.connectionId,
                                candidateID: candidate.id,
                                enabled: enabled
                            )
                        }
                    )
                )
                .labelsHidden()
                .toggleStyle(.switch)
                .controlSize(.small)
                .disabled(settings.isSaving || settings.endpoint.isRunning)

                VStack(alignment: .leading, spacing: 4) {
                    Text(apiModelVariantName(candidate))
                        .font(Typography.label)
                        .foregroundStyle(IslandVisual.primaryText)
                    Text(candidate.modelId)
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.tertiaryText)
                    Text(evidencePresentation.text)
                        .font(Typography.micro)
                        .foregroundStyle(candidateEvidenceColor(evidencePresentation.tone, accent: IslandColor.endpoint))
                }
                .frame(width: Layout.ingressModelIdentityWidth, alignment: .leading)

                Spacer(minLength: 8)

                Button {
                    settings.testConnection(
                        connectionID: connection.id,
                        modelID: candidate.modelId
                    )
                } label: {
                    HStack(spacing: 6) {
                        if isTesting {
                            ProgressView()
                                .controlSize(.small)
                        }
                        Text(L10n.tr(isTesting ? "测试中" : "测试连接"))
                    }
                }
                .buttonStyle(IslandActionButtonStyle(.secondary))
                .fixedSize(horizontal: true, vertical: false)
                .disabled(settings.isSaving || settings.endpoint.isRunning)

                Button {
                    selectionStore.startSingleScan(candidateID: candidate.id, conflictPresentation: .settings)
                } label: {
                    Text("单独扫描")
                }
                .buttonStyle(IslandActionButtonStyle(.secondary))
                .fixedSize(horizontal: true, vertical: false)
                .disabled(settings.isSaving)

                Menu {
                    Button("移除档位", role: .destructive) {
                        requestModelCandidateRemoval(candidate, connection: connection)
                    }
                } label: {
                    Image(systemName: "ellipsis")
                        .frame(width: 24, height: 24)
                }
                .menuStyle(.borderlessButton)
                .menuIndicator(.hidden)
                .help(L10n.tr("更多档位操作"))
                .disabled(
                    selectionStore.snapshot?.runtime.isRunning == true ||
                    selectionStore.snapshot?.runtime.hasResumableRun == true ||
                    settings.isSaving ||
                    settings.endpoint.isRunning
                )
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            if let feedback, !isEndpointEditorOpen(for: connection.id) {
                HStack(spacing: 6) {
                    Image(systemName: feedback.ok ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                    Text(L10n.tr(feedback.message))
                        .lineLimit(2)
                }
                .font(Typography.micro)
                .foregroundStyle(feedback.ok ? IslandColor.liveTeal : IslandColor.alertRed)
            }
        }
        .padding(LayoutRhythm.standard)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(IslandVisual.hairline)
                .frame(height: 0.5)
        }
    }

    private var endpointConnectionSheet: some View {
        VStack(alignment: .leading, spacing: LayoutRhythm.section) {
            VStack(alignment: .leading, spacing: LayoutRhythm.compact) {
                Text(L10n.tr(editingEndpointConnectionID == nil ? "添加连接" : "编辑连接"))
                    .font(Typography.pageTitle)
                    .foregroundStyle(IslandVisual.primaryText)
                Text("先从 provider 目录里选模型簇和档位，再补齐连接信息。API Key 只在首次绑定或主动更换时输入一次，并保存到 macOS 钥匙串。")
                    .font(Typography.settingsCardBody)
                    .foregroundStyle(IslandVisual.secondaryText)
            }

            ScrollView(.vertical, showsIndicators: false) {
                VStack(alignment: .leading, spacing: 14) {
                    endpointProviderSummaryCard
                    endpointProviderCatalogSection
                    endpointEditorField("API Key") {
                        if endpointHasStoredAPIKey && !endpointIsReplacingAPIKey {
                            VStack(alignment: .leading, spacing: 10) {
                                HStack(spacing: 8) {
                                    Image(systemName: "checkmark.shield.fill")
                                        .foregroundStyle(IslandColor.liveTeal)
                                    Text(L10n.tr(
                                        editingEndpointConnection?.secretStorageSummaryText
                                            ?? "已安全保存"
                                    ))
                                        .font(Typography.label)
                                        .foregroundStyle(IslandVisual.primaryText)
                                    Spacer()
                                    Button("更换 Key") {
                                        endpointIsReplacingAPIKey = true
                                    }
                                    .buttonStyle(IslandActionButtonStyle(.secondary))
                                }
                                Text(L10n.tr(endpointStoredAPIKeyDescription))
                                    .font(Typography.micro)
                                    .foregroundStyle(IslandVisual.secondaryText)
                            }
                        } else {
                            VStack(alignment: .leading, spacing: 8) {
                                SecureField(
                                    editingEndpointConnectionID == nil
                                        ? "输入 API Key"
                                        : "输入新的 API Key",
                                    text: $endpointAPIKey
                                )
                                .textFieldStyle(.roundedBorder)

                                if endpointHasStoredAPIKey {
                                    HStack {
                                        Text("不想更换时，可以继续沿用当前已绑定 Key。")
                                            .font(Typography.micro)
                                            .foregroundStyle(IslandVisual.secondaryText)
                                        Spacer()
                                        Button("继续沿用当前 Key") {
                                            endpointAPIKey = ""
                                            endpointIsReplacingAPIKey = false
                                        }
                                        .buttonStyle(IslandActionButtonStyle(.secondary))
                                    }
                                }
                            }
                        }
                    }

                    if let provider = selectedEndpointCatalogProvider {
                        HStack(spacing: 10) {
                            if let apiKeyUrl = provider.apiKeyUrl,
                               let url = URL(string: apiKeyUrl) {
                                Link(destination: url) {
                                    Text("获取 API Key")
                                }
                                .buttonStyle(IslandActionButtonStyle(.secondary))
                            }
                            if let websiteUrl = provider.websiteUrl,
                               let url = URL(string: websiteUrl) {
                                Link("提供商官网", destination: url)
                                    .buttonStyle(IslandActionButtonStyle(.secondary))
                            }
                        }
                    }

                    DisclosureGroup("高级设置", isExpanded: $endpointShowsAdvanced) {
                        VStack(alignment: .leading, spacing: 12) {
                            endpointEditorField("接入提供方") {
                                endpointProviderMenu
                            }
                            if endpointProvider == customEndpointProviderID {
                                endpointEditorField("提供方名称") {
                                    TextField("例如：团队网关", text: $endpointCustomProvider)
                                        .textFieldStyle(.roundedBorder)
                                }
                            }
                            endpointEditorField("API 格式") {
                                endpointAPIFormatSelector
                            }
                            endpointEditorField("Base URL") {
                                TextField("https://example.com/v1", text: $endpointBaseURL)
                                    .textFieldStyle(.roundedBorder)
                            }
                            endpointEditorField("手工补充模型") {
                                VStack(alignment: .leading, spacing: 8) {
                                    TextField(
                                        editingEndpointConnectionID == nil ? "准确的 Model ID" : "新增 Model ID",
                                        text: $endpointModelID
                                    )
                                        .textFieldStyle(.roundedBorder)
                                    if editingEndpointConnectionID == nil {
                                        Button("发现可用模型") {
                                            settings.probeEndpointModels(
                                                baseURL: endpointBaseURL,
                                                apiFormat: endpointAPIFormat,
                                                apiKey: endpointAPIKey
                                            ) { modelIDs in
                                                if endpointModelID.isEmpty {
                                                    endpointModelID = modelIDs.first ?? ""
                                                }
                                            }
                                        }
                                        .buttonStyle(IslandActionButtonStyle(.secondary))
                                        .disabled(
                                            endpointBaseURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                                                || endpointAPIKey.isEmpty
                                                || settings.endpoint.isRunning
                                                || settings.isSaving
                                        )
                                        if !settings.endpoint.discoveredModelIDs.isEmpty {
                                            Picker("已发现模型", selection: $endpointModelID) {
                                                ForEach(settings.endpoint.discoveredModelIDs, id: \.self) { modelID in
                                                    Text(modelID).tag(modelID)
                                                }
                                            }
                                            .labelsHidden()
                                        }
                                    }
                                }
                            }
                            if !endpointReasoningModelIDs.isEmpty {
                                endpointReasoningProfileEditor
                            }
                        }
                        .padding(.top, 10)
                    }

                    if let connectionID = editingEndpointConnectionID {
                        VStack(alignment: .leading, spacing: 10) {
                            HStack(spacing: 10) {
                                Button("自动发现模型") {
                                    settings.discoverEndpointDraftModels(
                                        connectionID: connectionID,
                                        baseURL: endpointBaseURL,
                                        apiFormat: endpointAPIFormat,
                                        apiKey: endpointAPIKey
                                    )
                                }
                .buttonStyle(IslandActionButtonStyle(.secondary))
                                .disabled(
                                    endpointBaseURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                                        || (endpointRequiresAPIKey && endpointAPIKey.isEmpty)
                                        || settings.endpoint.isRunning
                                        || settings.isSaving
                                )
                            }

                            if !settings.endpoint.newlyDiscoveredModelIDs.isEmpty
                                || !settings.endpoint.configuredDiscoveredModelIDs.isEmpty {
                                discoveredModelsResult(connectionID: connectionID)
                            }

                            Text("测试连接会发送一次最小真实请求，可能产生少量费用")
                                .font(Typography.micro)
                                .foregroundStyle(IslandColor.alertAmber.opacity(0.9))
                            if let modelID = endpointPrimaryTestModelID {
                                Text(L10n.tr("测试模型：%@", modelID))
                                    .font(Typography.micro)
                                    .foregroundStyle(IslandVisual.secondaryText)
                            }
                            Button {
                                guard let modelID = endpointPrimaryTestModelID else { return }
                                settings.testEndpointDraftConnection(
                                    connectionID: connectionID,
                                    modelID: modelID,
                                    baseURL: endpointBaseURL,
                                    apiFormat: endpointAPIFormat,
                                    providerPreset: endpointPreset,
                                    apiKey: endpointAPIKey
                                )
                            } label: {
                                HStack(spacing: 6) {
                                    if endpointEditorIsTestingConnection {
                                        ProgressView()
                                            .controlSize(.small)
                                    }
                                    Text(L10n.tr(
                                        endpointEditorIsTestingConnection ? "测试中" : "测试连接"
                                    ))
                                }
                            }
                            .buttonStyle(IslandActionButtonStyle(.secondary))
                            .disabled(
                                endpointPrimaryTestModelID == nil
                                    || settings.endpoint.isRunning
                                    || settings.isSaving
                            )

                            if let feedback = endpointEditorTestFeedback {
                                HStack(spacing: 6) {
                                    Image(systemName: feedback.ok ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                                    Text(L10n.tr(feedback.message))
                                }
                                .font(Typography.micro)
                                .foregroundStyle(feedback.ok ? IslandColor.liveTeal : IslandColor.alertRed)
                            }
                        }
                        .padding(12)
                        .background(RoundedRectangle(cornerRadius: IslandRadius.card).fill(Color.white.opacity(0.03)))
                    }

                    if let message = settings.endpoint.message,
                       endpointEditorTestFeedback == nil {
                        Text(L10n.tr(message))
                            .font(Typography.micro)
                            .foregroundStyle(IslandVisual.secondaryText)
                    }

                    if let message = settings.errorMessage {
                        Text(L10n.tr(message))
                            .font(Typography.micro)
                            .foregroundStyle(IslandColor.alertRed)
                    }
                }
            }

            HStack {
                Spacer()
                Button("取消") {
                    dismissEndpointEditor()
                }
                .buttonStyle(IslandActionButtonStyle(.secondary))
                if editingEndpointConnectionID == nil {
                    Button("连接并验证") {
                        settings.probeAndSaveEndpointConnection(
                            name: resolvedEndpointProviderName,
                            providerPreset: endpointPreset,
                            apiFormat: endpointAPIFormat,
                            baseURL: endpointBaseURL,
                            apiKey: endpointAPIKey,
                            modelIDs: endpointSelectedModelIDs,
                            reasoningProfilesByModel: endpointReasoningProfilesByModel,
                            defaultReasoningProfilesByModel: endpointDefaultReasoningProfilesByModel
                        ) { success in
                            guard success else { return }
                            dismissEndpointEditor()
                        }
                    }
                    .buttonStyle(IslandActionButtonStyle(.primary))
                    .disabled(!endpointCanSubmit || settings.endpoint.isRunning || settings.isSaving)
                } else {
                    Button("保存") {
                        guard settings.saveEndpointConnection(
                            connectionID: editingEndpointConnectionID,
                            name: resolvedEndpointProviderName,
                            providerPreset: endpointPreset,
                            apiFormat: endpointAPIFormat,
                            baseURL: endpointBaseURL,
                            apiKey: endpointAPIKey,
                            modelIDs: endpointSelectedModelIDs,
                            reasoningProfilesByModel: endpointReasoningProfilesByModel,
                            defaultReasoningProfilesByModel: endpointDefaultReasoningProfilesByModel
                        ) != nil else { return }
                        dismissEndpointEditorAfterSave = true
                    }
                    .buttonStyle(IslandActionButtonStyle(.primary))
                    .disabled(!endpointCanSubmit || settings.endpoint.isRunning || settings.isSaving)
                }
            }
        }
        .padding(LayoutRhythm.section)
        .frame(width: 620, height: 660)
        .background(IslandColor.canvas)
        .preferredColorScheme(.dark)
        .environment(\.locale, appLanguage.locale)
        .onChange(of: endpointBaseURL) { _ in
            settings.resetEndpointDraftFeedback()
        }
        .onChange(of: endpointAPIFormat) { _ in
            settings.resetEndpointDraftFeedback()
        }
        .onChange(of: endpointAPIKey) { _ in
            settings.resetEndpointDraftFeedback()
        }
        .onChange(of: endpointModelID) { _ in
            settings.resetEndpointDraftFeedback()
        }
        .onChange(of: settings.endpoint.discoveredReasoningProfilesByModel) { profilesByModel in
            for (modelID, profiles) in profilesByModel {
                endpointReasoningProfileDrafts[modelID] = profiles.joined(separator: ", ")
            }
        }
    }

    private var endpointAPIFormatSelector: some View {
        VStack(alignment: .leading, spacing: 8) {
            endpointAPIFormatOption(
                apiFormat: "openai_chat_completions",
                title: "OpenAI — Chat Completions",
                route: "POST · Base URL + /chat/completions"
            )
            endpointAPIFormatOption(
                apiFormat: "openai_responses",
                title: "OpenAI — Responses",
                route: "POST · Base URL + /responses"
            )
            endpointAPIFormatOption(
                apiFormat: "anthropic_messages",
                title: "Anthropic — Messages",
                route: "POST · Base URL + /messages"
            )
            Text("不同网关对 Responses 的支持程度不同，连接并验证会发送最小真实请求。")
                .font(Typography.micro)
                .foregroundStyle(IslandVisual.tertiaryText)
            Text("Anthropic Messages 使用 x-api-key 与 anthropic-version；适用于原生 Claude 接口和只开放 /messages 的网关。")
                .font(Typography.micro)
                .foregroundStyle(IslandVisual.tertiaryText)
        }
    }

    private var endpointProviderMenu: some View {
        Menu {
            ForEach(endpointProviderOptions) { provider in
                Button {
                    endpointProvider = provider.id
                    applyEndpointProviderDefaults(provider.id)
                } label: {
                    Label(
                        provider.title,
                        systemImage: endpointProvider == provider.id ? "checkmark" : "circle"
                    )
                }
            }
        } label: {
            HStack(spacing: 8) {
                Text(endpointProviderMenuLabel)
                    .font(Typography.label)
                    .foregroundStyle(IslandVisual.primaryText)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer(minLength: 8)
                Image(systemName: "chevron.down")
                    .font(Typography.micro)
                    .foregroundStyle(IslandColor.interaction)
            }
            .padding(.horizontal, 10)
            .frame(height: 30)
            .background(
                RoundedRectangle(cornerRadius: IslandRadius.control)
                    .fill(IslandVisual.controlFill)
                    .overlay(
                        RoundedRectangle(cornerRadius: IslandRadius.control)
                            .strokeBorder(IslandVisual.selectedBorder, lineWidth: 0.5)
                    )
            )
            .contentShape(Rectangle())
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize(horizontal: false, vertical: true)
        .accessibilityLabel(L10n.tr("接入提供方"))
        .accessibilityValue(endpointProviderMenuLabel)
    }

    private var endpointProviderMenuLabel: String {
        endpointProviderOptions.first(where: { $0.id == endpointProvider })?.title
            ?? "选择提供方"
    }

    private func endpointAPIFormatOption(
        apiFormat: String,
        title: String,
        route: String
    ) -> some View {
        let isSelected = endpointAPIFormat == apiFormat

        return Button {
            endpointAPIFormat = apiFormat
        } label: {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(isSelected ? IslandColor.interaction : IslandVisual.hintText)

                VStack(alignment: .leading, spacing: 4) {
                    Text(title)
                        .font(Typography.label)
                        .foregroundStyle(IslandVisual.primaryText)
                    Text(route)
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.secondaryText)
                }

                Spacer(minLength: 0)
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
            .background(
                RoundedRectangle(cornerRadius: IslandRadius.control)
                    .fill(
                        isSelected
                            ? IslandVisual.selectedSurface
                            : Color.white.opacity(0.025)
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: IslandRadius.control)
                            .strokeBorder(
                                isSelected
                                    ? IslandVisual.selectedBorder
                                    : Color.white.opacity(0.06),
                                lineWidth: 0.8
                            )
                    )
            )
        }
        .buttonStyle(.plain)
        .islandPointerOnHover()
        .accessibilityValue(isSelected ? L10n.tr("已选择") : L10n.tr("未选择"))
    }

    private func discoveredModelsResult(connectionID: String) -> some View {
        LazyVStack(alignment: .leading, spacing: 8) {
            if !settings.endpoint.newlyDiscoveredModelIDs.isEmpty {
                Text("新增模型")
                    .font(Typography.micro.weight(.semibold))
                    .foregroundStyle(IslandColor.interaction)
                ForEach(settings.endpoint.newlyDiscoveredModelIDs, id: \.self) { modelID in
                    HStack(spacing: 10) {
                        Text(modelID)
                            .font(Typography.caption)
                            .foregroundStyle(IslandVisual.primaryText)
                            .lineLimit(1)
                        Spacer()
                        Button("加入配置") {
                            settings.addEndpointModel(
                                connectionID: connectionID,
                                modelID: modelID
                            )
                        }
                        .buttonStyle(IslandActionButtonStyle(.secondary))
                    }
                    .padding(.vertical, 4)
                }
            }

            if !settings.endpoint.configuredDiscoveredModelIDs.isEmpty {
                Text("已配置")
                    .font(Typography.micro.weight(.semibold))
                    .foregroundStyle(IslandVisual.secondaryText)
                    .padding(.top, settings.endpoint.newlyDiscoveredModelIDs.isEmpty ? 0 : 4)
                ForEach(settings.endpoint.configuredDiscoveredModelIDs, id: \.self) { modelID in
                    HStack(spacing: 8) {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundStyle(IslandColor.liveTeal)
                        Text(modelID)
                            .font(Typography.caption)
                            .foregroundStyle(IslandVisual.secondaryText)
                            .lineLimit(1)
                    }
                    .padding(.vertical, 3)
                }
            }
        }
        .padding(10)
        .background(RoundedRectangle(cornerRadius: IslandRadius.control).fill(Color.white.opacity(0.025)))
    }

    private var endpointProviderSummaryCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let provider = selectedEndpointCatalogProvider {
                HStack(alignment: .top, spacing: 10) {
                    Circle()
                        .fill(providerAccent(for: provider.providerId))
                        .frame(width: 8, height: 8)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(provider.displayName)
                            .font(Typography.settingsCardTitle)
                            .foregroundStyle(IslandVisual.primaryText)
                        Text(
                            provider.defaultBaseUrl
                                ?? "当前提供方未预置 Base URL，可在下方手动补充。"
                        )
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.secondaryText)
                    }
                    Spacer(minLength: 0)
                    if providerConnectionCount(for: provider.providerId) > 0 {
                        Text(
                            L10n.tr(
                                "已接入 %d",
                                providerConnectionCount(for: provider.providerId)
                            )
                        )
                            .font(Typography.chip)
                            .foregroundStyle(providerAccent(for: provider.providerId))
                            .padding(.horizontal, 7)
                            .padding(.vertical, 4)
                            .background(
                                Capsule().fill(providerAccent(for: provider.providerId).opacity(0.12))
                            )
                    }
                }

                HStack(spacing: 8) {
                    ingressMetaPill(title: "\(provider.families.count)", subtitle: "模型簇")
                    ingressMetaPill(
                        title: "\(provider.families.reduce(0) { $0 + $1.variants.count })",
                        subtitle: "目录档位"
                    )
                    ingressMetaPill(title: endpointPresetTitle(endpointPreset), subtitle: "接入方式")
                }
            } else {
                VStack(alignment: .leading, spacing: 5) {
                    Text("自定义 endpoint")
                        .font(Typography.settingsCardTitle)
                        .foregroundStyle(IslandVisual.primaryText)
                    Text("适合私有网关、团队代理和兼容 OpenAI 协议的自建服务。")
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.secondaryText)
                }
            }
        }
        .padding(14)
        .background(cardBackground)
    }

    @ViewBuilder
    private var endpointProviderCatalogSection: some View {
        if let provider = selectedEndpointCatalogProvider {
            endpointEditorField("推荐模型目录") {
                VStack(alignment: .leading, spacing: 10) {
                    Text(editingEndpointConnectionID == nil
                        ? "先选模型簇和档位，再保存连接；未命中的模型仍可在下方手工补充。"
                        : "已配置模型会保留；此处只用于新增模型，未命中的模型仍可在下方手工补充。")
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.secondaryText)
                    if provider.families.isEmpty {
                        Text("当前提供方没有预置目录，可先保存连接后用 discovery 或手工模型补齐。")
                            .font(Typography.micro)
                            .foregroundStyle(IslandVisual.tertiaryText)
                            .padding(12)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(
                                RoundedRectangle(cornerRadius: IslandRadius.card)
                                    .fill(Color.white.opacity(0.03))
                            )
                    } else {
                        VStack(spacing: 8) {
                            ForEach(provider.families) { family in
                                endpointProviderFamilyCard(family)
                            }
                        }
                    }
                    if !endpointSelectedCatalogModelIDs.isEmpty {
                Text(L10n.tr(
                    "已选 %d 个目录模型",
                    endpointSelectedCatalogModelIDs.count
                ))
                            .font(Typography.micro)
                            .foregroundStyle(providerAccent(for: provider.providerId))
                    }
                }
            }
        }
    }

    private func endpointProviderFamilyCard(_ family: BridgeProviderCatalogFamily) -> some View {
        let familyModelIDs = endpointCatalogModelIDs(for: family)
        let fullySelected = !familyModelIDs.isEmpty && familyModelIDs.allSatisfy(endpointSelectedCatalogModelIDs.contains)
        let fullyPersisted = !familyModelIDs.isEmpty && familyModelIDs.allSatisfy(persistedEndpointModelIDs.contains)
        let familyActionTitle: String
        if fullyPersisted {
            familyActionTitle = "已配置"
        } else if fullySelected {
            familyActionTitle = editingEndpointConnectionID == nil ? "清空整组" : "已加入"
        } else {
            familyActionTitle = "整组加入"
        }

        return VStack(alignment: .leading, spacing: 9) {
            HStack(alignment: .top, spacing: 10) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(family.displayName)
                        .font(Typography.label)
                        .foregroundStyle(IslandVisual.primaryText)
                    Text(L10n.tr("%d 个档位", family.variants.count))
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.tertiaryText)
                }
                Spacer(minLength: 0)
                Button(familyActionTitle) {
                    toggleEndpointCatalogFamily(family)
                }
                .buttonStyle(IslandActionButtonStyle(.secondary))
                .disabled(fullyPersisted)
            }

            VStack(spacing: 7) {
                ForEach(family.variants) { variant in
                    endpointProviderVariantRow(variant)
                }
            }
        }
        .padding(12)
        .background(
            RoundedRectangle(cornerRadius: IslandRadius.card)
                .fill(Color.white.opacity(0.03))
                .overlay(
                    RoundedRectangle(cornerRadius: IslandRadius.card)
                        .strokeBorder(.white.opacity(0.05), lineWidth: 0.5)
                )
        )
    }

    private func endpointProviderVariantRow(_ variant: BridgeProviderCatalogVariant) -> some View {
        let isSelected = endpointCatalogVariantSelected(variant)
        let isPersisted = !variant.modelIds.isEmpty && variant.modelIds.allSatisfy(persistedEndpointModelIDs.contains)

        return Button {
            toggleEndpointCatalogVariant(variant)
        } label: {
            HStack(spacing: 10) {
                Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(isSelected ? IslandColor.interaction : IslandVisual.hintText)

                VStack(alignment: .leading, spacing: 4) {
                    Text(variant.displayName)
                        .font(Typography.label)
                        .foregroundStyle(IslandVisual.primaryText)
                    Text(variant.modelIds.joined(separator: " · "))
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.tertiaryText)
                        .lineLimit(2)
                }

                Spacer(minLength: 0)

                Text(L10n.tr(isPersisted ? "已配置" : (isSelected ? "已加入" : "加入")))
                    .font(Typography.chip)
                    .foregroundStyle(
                        isPersisted ? IslandColor.liveTeal
                            : (isSelected ? IslandColor.interaction : IslandVisual.secondaryText)
                    )
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 9)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: IslandRadius.control)
                    .fill(isSelected ? IslandVisual.surfaceStrong : Color.white.opacity(0.02))
            )
        }
        .buttonStyle(.plain)
        .disabled(isPersisted)
        .islandPointerOnHover()
    }

    private func endpointEditorField<Content: View>(
        _ title: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(LocalizedStringKey(title))
                .font(Typography.label)
                .foregroundStyle(IslandVisual.secondaryText)
            content()
        }
    }

    private var editingEndpointConnection: BridgeIngressConnection? {
        guard let editingEndpointConnectionID else { return nil }
        return settings.draftConfig?.modelIngress.connections.first {
            $0.id == editingEndpointConnectionID
        }
    }

    private var persistedEndpointModelIDs: Set<String> {
        Set(uniqueModelNames(in: editingEndpointConnection?.modelCandidates ?? []))
    }

    private var endpointSelectedModelIDs: [String] {
        var orderedModelIDs: [String] = []
        if let provider = selectedEndpointCatalogProvider {
            for family in provider.families {
                for variant in family.variants where endpointCatalogVariantSelected(variant) {
                    for modelID in variant.modelIds where !orderedModelIDs.contains(modelID) {
                        orderedModelIDs.append(modelID)
                    }
                }
            }
        }
        let manualModelID = endpointModelID.trimmingCharacters(in: .whitespacesAndNewlines)
        if !manualModelID.isEmpty && !orderedModelIDs.contains(manualModelID) {
            orderedModelIDs.append(manualModelID)
        }
        return orderedModelIDs
    }

    private var endpointReasoningModelIDs: [String] {
        var ordered = endpointSelectedModelIDs
        for modelID in uniqueModelNames(in: editingEndpointConnection?.modelCandidates ?? [])
            where !ordered.contains(modelID) {
            ordered.append(modelID)
        }
        return ordered
    }

    private var endpointReasoningProfilesByModel: [String: [String]] {
        var profilesByModel: [String: [String]] = [:]
        for modelID in endpointReasoningModelIDs {
            if let draft = endpointReasoningProfileDrafts[modelID] {
                profilesByModel[modelID] = parseReasoningProfiles(draft)
            } else if let discovered = settings.endpoint.discoveredReasoningProfilesByModel[modelID] {
                profilesByModel[modelID] = discovered
            } else if let catalogProfiles = endpointCatalogReasoningProfiles(for: modelID) {
                profilesByModel[modelID] = catalogProfiles
            }
        }
        return profilesByModel
    }

    private var endpointDefaultReasoningProfilesByModel: [String: String] {
        var defaultsByModel: [String: String] = [:]
        for modelID in endpointReasoningModelIDs {
            if let discovered = settings.endpoint.discoveredDefaultReasoningProfileByModel[modelID] {
                defaultsByModel[modelID] = discovered
            } else if let catalogDefault = endpointCatalogDefaultReasoningProfile(for: modelID) {
                defaultsByModel[modelID] = catalogDefault
            }
        }
        return defaultsByModel
    }

    private var endpointReasoningProfileEditor: some View {
        endpointEditorField("模型思考深度") {
            VStack(alignment: .leading, spacing: 10) {
                ForEach(endpointReasoningModelIDs, id: \.self) { modelID in
                    VStack(alignment: .leading, spacing: 5) {
                        Text(modelID)
                            .font(Typography.micro)
                            .foregroundStyle(IslandVisual.secondaryText)
                        TextField(
                            "例如 low, medium, high, xhigh, max；留空使用默认档位",
                            text: endpointReasoningProfileBinding(for: modelID)
                        )
                        .textFieldStyle(.roundedBorder)
                    }
                }
                Text("自动发现能读到能力元数据时会预填；读不到时可手工填写。每个档位都会作为独立候选参与扫描。")
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.tertiaryText)
            }
        }
    }

    private func endpointReasoningProfileBinding(for modelID: String) -> Binding<String> {
        Binding(
            get: {
                endpointReasoningProfileDrafts[modelID]
                    ?? settings.endpoint.discoveredReasoningProfilesByModel[modelID]?.joined(separator: ", ")
                    ?? endpointCatalogReasoningProfiles(for: modelID)?.joined(separator: ", ")
                    ?? ""
            },
            set: { endpointReasoningProfileDrafts[modelID] = $0 }
        )
    }

    private func parseReasoningProfiles(_ text: String) -> [String] {
        var seen = Set<String>()
        return text
            .replacingOccurrences(of: "，", with: ",")
            .split(whereSeparator: { $0 == "," || $0.isWhitespace })
            .compactMap { rawValue in
                let profile = rawValue.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
                guard !profile.isEmpty, seen.insert(profile).inserted else { return nil }
                return profile
            }
    }

    private func endpointCatalogReasoningProfiles(for modelID: String) -> [String]? {
        selectedEndpointCatalogProvider?
            .families
            .flatMap(\.variants)
            .first(where: { $0.modelIds.contains(modelID) })?
            .reasoningEfforts
    }

    private func endpointCatalogDefaultReasoningProfile(for modelID: String) -> String? {
        selectedEndpointCatalogProvider?
            .families
            .flatMap(\.variants)
            .first(where: { $0.modelIds.contains(modelID) })?
            .defaultReasoningEffort
    }

    private var endpointPrimaryTestModelID: String? {
        let manualModelID = endpointModelID.trimmingCharacters(in: .whitespacesAndNewlines)
        guard manualModelID.isEmpty else { return manualModelID }
        return endpointSelectedModelIDs.first
            ?? uniqueModelNames(in: editingEndpointConnection?.modelCandidates ?? []).first
    }

    private var endpointEditorIsTestingConnection: Bool {
        guard let connectionID = editingEndpointConnectionID,
              let modelID = endpointPrimaryTestModelID else {
            return false
        }
        return settings.endpoint.isTesting(connectionID: connectionID, modelID: modelID)
    }

    private var endpointEditorTestFeedback: EndpointTestFeedback? {
        guard let connectionID = editingEndpointConnectionID,
              let modelID = endpointPrimaryTestModelID else {
            return nil
        }
        return settings.endpoint.feedback(connectionID: connectionID, modelID: modelID)
    }

    private func isEndpointEditorOpen(for connectionID: String) -> Bool {
        showsEndpointConnectionSheet && editingEndpointConnectionID == connectionID
    }

    private func finishEndpointEditorSaveIfNeeded(_ state: SettingsSaveFeedbackState) {
        guard dismissEndpointEditorAfterSave else { return }
        switch state {
        case .saved:
            dismissEndpointEditor()
        case .failed:
            dismissEndpointEditorAfterSave = false
        case .idle, .saving:
            break
        }
    }

    private func dismissEndpointEditor() {
        dismissEndpointEditorAfterSave = false
        showsEndpointConnectionSheet = false
        editingEndpointConnectionID = nil
        settings.resetEndpointDraftFeedback()
    }

    private var endpointStoredAPIKeyDescription: String {
        guard let connection = editingEndpointConnection else {
            return "留空保存会继续复用当前 Key。只有主动更换时才需要重新输入。"
        }
        if connection.usesLocalEncryptedSecret {
            return "这是旧版本地加密存储。保存、测试或扫描时会迁移到 macOS 钥匙串；配置保存成功后才会清理旧副本。"
        }
        return "留空保存会继续复用当前 Key。只有主动更换时才需要重新输入。"
    }

    private func endpointCatalogModelIDs(for family: BridgeProviderCatalogFamily) -> [String] {
        family.variants.flatMap(\.modelIds)
    }

    private func endpointCatalogVariantSelected(_ variant: BridgeProviderCatalogVariant) -> Bool {
        !variant.modelIds.isEmpty && variant.modelIds.allSatisfy(endpointSelectedCatalogModelIDs.contains)
    }

    private func toggleEndpointCatalogVariant(_ variant: BridgeProviderCatalogVariant) {
        let shouldSelect = !endpointCatalogVariantSelected(variant)
        if shouldSelect {
            endpointSelectedCatalogModelIDs.formUnion(variant.modelIds)
        } else {
            let removableModelIDs = Set(
                variant.modelIds.filter { !persistedEndpointModelIDs.contains($0) }
            )
            endpointSelectedCatalogModelIDs.subtract(removableModelIDs)
        }
    }

    private func toggleEndpointCatalogFamily(_ family: BridgeProviderCatalogFamily) {
        let familyModelIDs = endpointCatalogModelIDs(for: family)
        let shouldSelect = !familyModelIDs.allSatisfy(endpointSelectedCatalogModelIDs.contains)
        if shouldSelect {
            endpointSelectedCatalogModelIDs.formUnion(familyModelIDs)
        } else {
            let removableModelIDs = Set(
                familyModelIDs.filter { !persistedEndpointModelIDs.contains($0) }
            )
            endpointSelectedCatalogModelIDs.subtract(removableModelIDs)
        }
    }

    private func openNewEndpointEditor(providerID: String = "deepseek") {
        dismissEndpointEditorAfterSave = false
        editingEndpointConnectionID = nil
        endpointProvider = providerID
        endpointCustomProvider = ""
        endpointAPIKey = ""
        endpointHasStoredAPIKey = false
        endpointIsReplacingAPIKey = false
        endpointModelID = ""
        endpointSelectedCatalogModelIDs = []
        endpointReasoningProfileDrafts = [:]
        endpointShowsAdvanced = providerID == customEndpointProviderID
        applyEndpointProviderDefaults(providerID)
        settings.resetModelDiscovery()
        showsEndpointConnectionSheet = true
    }

    private func openEndpointEditor(_ connection: BridgeIngressConnection) {
        dismissEndpointEditorAfterSave = false
        editingEndpointConnectionID = connection.id
        let providerID = endpointProviderID(for: connection)
        endpointProvider = providerID
        endpointCustomProvider = providerID == customEndpointProviderID ? connection.name : ""
        endpointPreset = connection.providerPreset
        endpointAPIFormat = connection.apiFormat ?? "openai_chat_completions"
        endpointBaseURL = connection.baseUrl ?? ""
        endpointAPIKey = ""
        endpointHasStoredAPIKey = connection.apiKeyRef != nil
        endpointIsReplacingAPIKey = false
        endpointShowsAdvanced = false
        let existingModelIDs = uniqueModelNames(in: connection.modelCandidates)
        let catalogModelIDs = Set(
            providerCatalogProvider(id: providerID)?
                .families
                .flatMap(\.variants)
                .flatMap(\.modelIds)
                ?? []
        )
        endpointSelectedCatalogModelIDs = Set(
            existingModelIDs.filter { catalogModelIDs.contains($0) }
        )
        endpointReasoningProfileDrafts = Dictionary(uniqueKeysWithValues:
            existingModelIDs.compactMap { modelID -> (String, String)? in
                let profiles = connection.modelCandidates
                    .filter { $0.modelId == modelID }
                    .map {
                        $0.scanProfile
                            .trimmingCharacters(in: .whitespacesAndNewlines)
                            .lowercased()
                    }
                    .filter { $0 != "default" && $0 != "codex_default" }
                guard !profiles.isEmpty else { return nil }
                return (modelID, profiles.joined(separator: ", "))
            }
        )
        endpointModelID = ""
        settings.resetModelDiscovery()
        showsEndpointConnectionSheet = true
    }

    private func requestConnectionDeletion(_ connection: BridgeIngressConnection) {
        guard source(for: connection.sourceId)?.mode == "api" else { return }
        connectionPendingDeletion = connection
        showsDeleteConnectionConfirmation = true
    }

    private func deleteEndpointConnection(_ connection: BridgeIngressConnection) {
        settings.deleteConnection(connectionID: connection.id) { success in
            guard success else { return }
            if selectedIngressConnectionID == connection.id {
                selectedIngressConnectionID = nil
            }
            if editingEndpointConnectionID == connection.id {
                showsEndpointConnectionSheet = false
                editingEndpointConnectionID = nil
            }
            connectionPendingDeletion = nil
        }
    }

    private func connectionDeletionMessage(_ connection: BridgeIngressConnection) -> String {
        L10n.tr(
            "将移除 %d 个模型条目和 %d 个扫描档位。历史扫描成绩会保留，但不再参与推荐。",
            uniqueModelNames(in: connection.modelCandidates).count,
            connection.modelCandidates.count
        )
    }

    private func requestModelFamilyRemoval(
        _ family: ModelFamilyGroup,
        connection: BridgeIngressConnection
    ) {
        modelCandidatesPendingRemoval = ModelCandidateRemovalRequest(
            connectionID: connection.id,
            candidateIDs: family.candidates.map(\.id),
            actionTitle: "移除模型簇",
            message: L10n.tr(
                "将移除 %@ 的 %d 个扫描档位。历史扫描成绩会保留，但不再参与推荐。",
                family.displayModel,
                family.candidates.count
            )
        )
        showsModelCandidateRemovalConfirmation = true
    }

    private func requestModelCandidateRemoval(
        _ candidate: BridgeIngressModelCandidate,
        connection: BridgeIngressConnection
    ) {
        modelCandidatesPendingRemoval = ModelCandidateRemovalRequest(
            connectionID: connection.id,
            candidateIDs: [candidate.id],
            actionTitle: "移除档位",
            message: L10n.tr(
                "将移除 %@ 的 %@ 档位。历史扫描成绩会保留，但不再参与推荐。",
                settingsCandidatePresentation(for: candidate).displayModel,
                apiModelVariantName(candidate)
            )
        )
        showsModelCandidateRemovalConfirmation = true
    }

    private func removeModelCandidates(_ request: ModelCandidateRemovalRequest) {
        settings.removeModelCandidates(
            connectionID: request.connectionID,
            candidateIDs: request.candidateIDs
        ) { success in
            guard success else { return }
            customCandidateIDs.subtract(request.candidateIDs)
            modelCandidatesPendingRemoval = nil
        }
    }

    private var endpointRequiresAPIKey: Bool {
        editingEndpointConnectionID == nil || !endpointHasStoredAPIKey || endpointIsReplacingAPIKey
    }

    private var endpointCanSubmit: Bool {
        let hasSelectedOrPersistedModel = !endpointSelectedModelIDs.isEmpty || !persistedEndpointModelIDs.isEmpty
        return !resolvedEndpointProviderName.isEmpty
            && !endpointBaseURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !(endpointRequiresAPIKey && endpointAPIKey.isEmpty)
            && hasSelectedOrPersistedModel
            && selectedEndpointCatalogProvider?.connectionSupported != false
    }

    private var resolvedEndpointProviderName: String {
        if endpointProvider == customEndpointProviderID {
            return endpointCustomProvider.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        return selectedEndpointCatalogProvider?.displayName
            ?? endpointProviderOptions.first(where: { $0.id == endpointProvider })?.title
            ?? ""
    }

    private func endpointProviderID(for connection: BridgeIngressConnection) -> String {
        settingsIngressPresentation.endpointProviderID(
            for: connection,
            fallback: customEndpointProviderID
        )
    }

    private func applyEndpointProviderDefaults(_ provider: String) {
        endpointSelectedCatalogModelIDs = []
        endpointModelID = ""
        endpointAPIFormat = "openai_chat_completions"
        if provider == customEndpointProviderID {
            endpointPreset = "custom"
            endpointBaseURL = ""
            return
        }
        guard let catalogProvider = providerCatalogProvider(id: provider) else {
            endpointPreset = "generic"
            endpointBaseURL = ""
            return
        }
        endpointPreset = catalogProvider.providerPreset
        endpointBaseURL = catalogProvider.defaultBaseUrl ?? ""
        endpointAPIFormat = catalogProvider.defaultApiFormat ?? "openai_chat_completions"
        endpointSelectedCatalogModelIDs = Set(catalogProvider.defaultModelIds)
    }

    private func endpointFormatTitle(_ apiFormat: String?) -> String {
        switch apiFormat {
        case "openai_responses": return "Responses"
        case "anthropic_messages": return "Anthropic Messages"
        default: return "Chat Completions"
        }
    }

    private func endpointPresetTitle(_ preset: String) -> String {
        switch preset {
        case "openrouter": return "OpenRouter"
        case "custom": return L10n.tr("自定义网关")
        default: return "OpenAI-compatible"
        }
    }

    private var enabledIngressCandidateCount: Int {
        settingsIngressPresentation.enabledCandidateCount
    }

    private var enabledIngressModelEntryCount: Int {
        settingsIngressPresentation.enabledModelEntryCount
    }

    private func enabledCandidateCount(for connection: BridgeIngressConnection) -> Int {
        settingsIngressPresentation.enabledCandidateCount(for: connection)
    }

    private var regularScanScopeMetrics: [IngressMetric] {
        settingsIngressPresentation.regularScanScopeMetrics
    }

    private func metricStrip(_ metrics: [IngressMetric]) -> some View {
        HStack(spacing: 0) {
            ForEach(Array(metrics.enumerated()), id: \.element.id) { index, metric in
                metricCell(metric)

                if index < metrics.count - 1 {
                    Rectangle()
                        .fill(IslandVisual.hairline)
                        .frame(width: 0.5, height: 32)
                        .padding(.horizontal, LayoutRhythm.standard)
                }
            }
        }
    }

    private func metricCell(_ metric: IngressMetric) -> some View {
        metricLabel(metric)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func metricLabel(_ metric: IngressMetric) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(L10n.tr(metric.value))
                .font(Typography.metricValue)
                .foregroundStyle(IslandVisual.primaryText)
            Text(L10n.tr(metric.label))
                .font(Typography.settingsStatLabel)
                .foregroundStyle(IslandVisual.secondaryText)
        }
        .contentShape(Rectangle())
    }

    private var enabledCandidatesButton: some View {
        Button {
            showsEnabledCandidatesPopover = true
        } label: {
            HStack(spacing: LayoutRhythm.compact) {
                Text("已启用档位")
                    .font(Typography.settingsStatLabel)
                    .foregroundStyle(IslandVisual.secondaryText)
                Text("\(enabledIngressCandidateCount)")
                    .font(Typography.label)
                    .foregroundStyle(IslandVisual.primaryText)
                Image(systemName: "chevron.down")
                    .font(Typography.micro)
                    .foregroundStyle(IslandColor.interaction)
            }
            .padding(.horizontal, 10)
            .frame(height: 30)
            .background(
                RoundedRectangle(cornerRadius: IslandRadius.control)
                    .fill(IslandVisual.controlFill)
                    .overlay(
                        RoundedRectangle(cornerRadius: IslandRadius.control)
                            .strokeBorder(IslandVisual.selectedBorder, lineWidth: 0.5)
                    )
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .popover(isPresented: $showsEnabledCandidatesPopover, arrowEdge: .bottom) {
            enabledCandidatesPopover
        }
        .islandPointerOnHover()
    }

    private var enabledIngressWorkspaceItems: [IngressWorkspaceItem] {
        settingsIngressPresentation.enabledWorkspaceItems
    }

    private var unverifiedEnabledEndpointWorkspaceItems: [IngressWorkspaceItem] {
        settingsIngressPresentation.unverifiedEnabledEndpointWorkspaceItems
    }

    private var regularScanIsBlockedByEndpointVerification: Bool {
        settingsIngressPresentation.regularScanIsBlockedByEndpointVerification
    }

    private var customScanEligibleCandidateIDs: Set<String> {
        settingsIngressPresentation.customEligibleCandidateIDs
    }

    private var selectedCustomCandidateIDs: Set<String> {
        settingsIngressPresentation.eligibleCustomSelection(from: customCandidateIDs)
    }

    private var enabledCandidatesPopover: some View {
        return VStack(alignment: .leading, spacing: LayoutRhythm.standard) {
            VStack(alignment: .leading, spacing: 4) {
                Text(L10n.tr("常规扫描档位"))
                    .font(Typography.settingsCardTitle)
                    .foregroundStyle(IslandVisual.primaryText)
                Text(L10n.tr(
                    "%d 个模型 · %d 个档位",
                    enabledIngressModelEntryCount,
                    enabledIngressCandidateCount
                ))
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.secondaryText)
            }

            if enabledIngressWorkspaceItems.isEmpty {
                Text(L10n.tr("当前没有启用的扫描档位。"))
                    .font(Typography.settingsCardBody)
                    .foregroundStyle(IslandVisual.secondaryText)
                    .frame(maxWidth: .infinity, minHeight: 88, alignment: .center)
            } else {
                ScrollView(.vertical, showsIndicators: true) {
                    VStack(alignment: .leading, spacing: LayoutRhythm.standard) {
                        ForEach(enabledIngressWorkspaceItems) { item in
                            VStack(alignment: .leading, spacing: LayoutRhythm.compact) {
                                Text(L10n.tr(
                                    item.source.mode == "api"
                                        ? item.connection.name
                                        : item.source.title
                                ))
                                    .font(Typography.label)
                                    .foregroundStyle(ingressAccent(for: item.source))

                                ForEach(
                                    settingsIngressPresentation.regularModelFamilyGroups(
                                        for: item.connection
                                    )
                                ) { family in
                                    VStack(alignment: .leading, spacing: LayoutRhythm.micro) {
                                        Text(family.displayModel)
                                            .font(Typography.micro.weight(.semibold))
                                            .foregroundStyle(IslandVisual.secondaryText)

                                        ForEach(family.candidates) { candidate in
                                            let presentation = settingsCandidatePresentation(
                                                for: candidate
                                            )
                                            Toggle(
                                                isOn: Binding(
                                                    get: { candidate.enabled },
                                                    set: { enabled in
                                                        settings.setModelCandidateEnabled(
                                                            connectionID: item.connection.id,
                                                            candidateID: candidate.id,
                                                            enabled: enabled
                                                        )
                                                    }
                                                )
                                                ) {
                                                    HStack(spacing: LayoutRhythm.compact) {
                                                    Text(presentation.displayModel)
                                                        .font(Typography.label)
                                                        .foregroundStyle(IslandVisual.primaryText)
                                                        .lineLimit(1)
                                                    Spacer(minLength: 8)
                                                    Text(presentation.variantName)
                                                        .font(Typography.micro)
                                                        .foregroundStyle(IslandVisual.tertiaryText)
                                                }
                                            }
                                            .toggleStyle(.switch)
                                            .controlSize(.small)
                                            .disabled(settings.isSaving || settings.endpoint.isRunning)
                                        }
                                    }
                                    .padding(LayoutRhythm.compact)
                                    .background(
                                        RoundedRectangle(cornerRadius: IslandRadius.control)
                                            .fill(Color.white.opacity(0.03))
                                    )
                                }
                            }
                        }
                    }
                }
                .frame(maxHeight: 360)
            }

        }
        .padding(LayoutRhythm.standard)
        .frame(width: 420)
        .background(IslandColor.panelRaised)
        .preferredColorScheme(.dark)
        .environment(\.locale, appLanguage.locale)
    }

    private var regularScanScopeSection: some View {
        VStack(alignment: .leading, spacing: LayoutRhythm.standard) {
            sectionTitle("扫描范围")
            VStack(alignment: .leading, spacing: 16) {
                metricStrip(regularScanScopeMetrics)

                Text("快速对比只运行当前配置与建议配置；全量扫描运行全部已启用配置。自定义本轮只影响这一次，不会修改常规集合。执行参数修改后自动保存。")
                    .font(Typography.settingsCardBody)
                    .foregroundStyle(IslandVisual.secondaryText)

                formRow("评测模式") {
                    Picker("评测模式", selection: evaluationProfileBinding) {
                        ForEach(selectionStore.evaluationProfiles) { profile in
                            Text(
                                L10n.tr(
                                    "%@ · %d 题",
                                    localizedEvaluationProfileLabel(profile),
                                    profile.questionCount
                                )
                            )
                                .tag(profile.id)
                        }
                    }
                    .labelsHidden()
                    .frame(width: Layout.longControlWidth)
                    .disabled(selectionStore.isEvaluationProfileSelectionLocked)
                }

                if let profile = selectionStore.selectedEvaluationProfile {
                    Text(localizedEvaluationProfileSummary(profile))
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.tertiaryText)
                }

                formRow("任务并发") {
                    Picker("任务并发", selection: $maxConcurrentTargets) {
                        Text("1").tag(1)
                        Text("2").tag(2)
                        Text("3").tag(3)
                        Text("4").tag(4)
                        Text("6").tag(6)
                        Text("8").tag(8)
                    }
                    .labelsHidden()
                    .frame(width: Layout.shortControlWidth)
                }

                formRow("单次超时") {
                    Picker("单次超时", selection: $executionTimeoutSeconds) {
                        Text("5 分钟").tag(300)
                        Text("10 分钟").tag(600)
                        Text("15 分钟").tag(900)
                        Text("20 分钟").tag(1200)
                    }
                    .labelsHidden()
                    .frame(width: Layout.shortControlWidth)
                }

                formRow("超时重试") {
                    Picker("超时重试", selection: $timeoutRetryCount) {
                        Text("不重试").tag(0)
                        Text("1 次").tag(1)
                        Text("2 次").tag(2)
                    }
                    .labelsHidden()
                    .frame(width: Layout.shortControlWidth)
                }

                Text("同轮题目共享任务并发上限；只要仍有待测题就尽量占满并发槽，同一模型的不同题也可并行。超时会终止当前请求；仅启用重试时重新发起，普通慢响应只记录耗时。")
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.tertiaryText)

                if regularScanIsRunning {
                    Text("扫描进行中；并发、超时和重试的修改会保存，并从下一轮开始生效。")
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.tertiaryText)
                } else if hasPausedResumableRun {
                    Text("检测到可续扫任务；常规扫描会继续上次进度，也可重新开始。")
                        .font(Typography.micro)
                        .foregroundStyle(IslandColor.alertAmber.opacity(0.82))
                } else if isManualFullScan {
                    Text("增量补齐会由后端校验 24 小时内同题包、同评分协议且路线一致的结果；不满足条件时不会启动扫描。")
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.tertiaryText)
                }

                if regularScanIsBlockedByEndpointVerification {
                    Text(L10n.tr(
                        "有 %d 个已启用 API 连接尚未重新测试，完成测试后才能发起新的常规扫描。",
                        unverifiedEnabledEndpointWorkspaceItems.count
                    ))
                        .font(Typography.micro)
                        .foregroundStyle(IslandColor.alertAmber.opacity(0.82))
                }

                HStack(spacing: LayoutRhythm.compact) {
                    if isManualFullScan && !hasPausedResumableRun {
                        Button(L10n.tr("增量补齐")) {
                            guard !regularScanIsBlockedByEndpointVerification else { return }
                            selectionStore.startIncrementalFullScan(conflictPresentation: .settings)
                        }
                        .buttonStyle(IslandActionButtonStyle(.primary))
                        .disabled(
                            settings.isSaving
                                || regularScanIsRunning
                                || regularScanIsBlockedByEndpointVerification
                        )

                        Button(L10n.tr("全新扫描")) {
                            guard !regularScanIsBlockedByEndpointVerification else { return }
                            selectionStore.startFreshFullScan(conflictPresentation: .settings)
                        }
                        .buttonStyle(IslandActionButtonStyle(.secondary))
                        .disabled(
                            settings.isSaving
                                || regularScanIsRunning
                                || regularScanIsBlockedByEndpointVerification
                        )
                    } else {
                        Button {
                            guard !regularScanIsBlockedByEndpointVerification else { return }
                            selectionStore.startRegularScan(conflictPresentation: .settings)
                        } label: {
                            Text(regularScanButtonTitle)
                        }
                        .buttonStyle(IslandActionButtonStyle(.primary))
                        .disabled(
                            settings.isSaving
                                || regularScanIsRunning
                                || regularScanIsBlockedByEndpointVerification
                        )
                    }

                    Button {
                        initializeCustomCandidateIDs()
                        customScanPlanOptions = nil
                        customScanPreviewError = nil
                        showsCustomScanSheet = true
                    } label: {
                        Text("自定义本轮")
                    }
                .buttonStyle(IslandActionButtonStyle(.secondary))
                    .disabled(settings.isSaving)

                    if hasPausedResumableRun {
                        RestartScanButton()
                    }
                }
            }
            .padding(LayoutRhythm.standard)
            .background(cardBackground)
        }
    }

    private var regularScanButtonTitle: String {
        if regularScanIsRunning {
            return L10n.tr("扫描进行中")
        }
        if hasPausedResumableRun {
            return L10n.tr("继续扫描")
        }
        return L10n.tr("开始扫描")
    }

    private var regularScanIsRunning: Bool {
        selectionStore.snapshot?.runtime.isRunning == true
    }

    private var isManualFullScan: Bool {
        selectionStore.selectedEvaluationProfile?.id == "full"
    }

    private var hasPausedResumableRun: Bool {
        selectionStore.snapshot?.runtime.hasResumableRun == true && !regularScanIsRunning
    }

    private var evaluationProfileBinding: Binding<String> {
        Binding(
            get: { selectionStore.selectedEvaluationProfile?.id ?? "" },
            set: { selectionStore.selectEvaluationProfile($0) }
        )
    }

    private func localizedEvaluationProfileLabel(
        _ profile: BridgeEvaluationProfile
    ) -> String {
        L10n.EvaluationProfile.label(id: profile.id, fallback: profile.label)
    }

    private func localizedEvaluationProfileSummary(
        _ profile: BridgeEvaluationProfile
    ) -> String {
        L10n.EvaluationProfile.summary(id: profile.id, fallback: profile.summary)
    }

    private func profileFamilyCard(
        _ family: ModelFamilyGroup,
        connection: BridgeIngressConnection,
        accent: Color
    ) -> some View {
        VStack(spacing: 0) {
            modelFamilyHeader(family, connection: connection, accent: accent)
            if expandedModelFamilyIDs.contains(family.id) {
                VStack(spacing: 7) {
                    ForEach(family.candidates) { candidate in
                        candidateRow(candidate, connection: connection, accent: accent)
                    }
                }
                .padding(.horizontal, 10)
                .padding(.bottom, 10)
            }
        }
        .overlay(alignment: .bottom) {
            Rectangle().fill(IslandVisual.hairline).frame(height: 0.5)
        }
    }

    private func singleVariantRow(
        _ candidate: BridgeIngressModelCandidate,
        connection: BridgeIngressConnection,
        accent: Color
    ) -> some View {
        let evidence = selectionStore.snapshot?.dashboard.cards.first {
            $0.candidateId == candidate.id
        }
        let evidencePresentation = SettingsCandidatePresenter.evidencePresentation(for: evidence)
        return HStack(spacing: LayoutRhythm.standard) {
            VStack(alignment: .leading, spacing: 4) {
                Text(settingsCandidatePresentation(for: candidate).displayModel)
                    .font(Typography.label)
                    .foregroundStyle(IslandVisual.primaryText)
                Text(candidate.modelId)
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.tertiaryText)
                Text(L10n.tr(evidencePresentation.text))
                    .font(Typography.micro)
                    .foregroundStyle(candidateEvidenceColor(evidencePresentation.tone, accent: accent))
            }

            Spacer(minLength: 8)

            Button {
                selectionStore.startSingleScan(candidateID: candidate.id, conflictPresentation: .settings)
            } label: {
                Text(L10n.tr("单独扫描"))
            }
            .buttonStyle(IslandActionButtonStyle(.secondary))
            .fixedSize(horizontal: true, vertical: false)
            .disabled(settings.isSaving)

            HStack(spacing: 6) {
                Text("纳入扫描")
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.secondaryText)
                Toggle(
                    "",
                    isOn: Binding(
                        get: { candidate.enabled },
                        set: { enabled in
                            settings.setModelCandidateEnabled(
                                connectionID: candidate.connectionId,
                                candidateID: candidate.id,
                                enabled: enabled
                            )
                        }
                    )
                )
                .labelsHidden()
                .toggleStyle(.switch)
                .controlSize(.small)
                .help(L10n.tr("启用后会纳入扫描"))
                .disabled(settings.isSaving || settings.endpoint.isRunning)
            }
        }
        .padding(12)
        .overlay(alignment: .bottom) {
            Rectangle().fill(IslandVisual.hairline).frame(height: 0.5)
        }
    }

    private func modelFamilyHeader(
        _ family: ModelFamilyGroup,
        connection: BridgeIngressConnection,
        accent: Color,
        itemLabel: String = "档位",
        onRemove: (() -> Void)? = nil
    ) -> some View {
        let enabledCount = family.candidates.filter(\.enabled).count
        let isExpanded = expandedModelFamilyIDs.contains(family.id)
        let localizedItemLabel = L10n.tr(itemLabel)
        return HStack(spacing: 12) {
            Button {
                if isExpanded {
                    expandedModelFamilyIDs.remove(family.id)
                } else {
                    expandedModelFamilyIDs.insert(family.id)
                }
            } label: {
                HStack(spacing: LayoutRhythm.compact) {
                    Image(systemName: isExpanded ? "chevron.down" : "chevron.right")
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.tertiaryText)
                        .frame(width: 12)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(family.displayModel)
                            .font(Typography.rowTitle)
                            .foregroundStyle(IslandVisual.primaryText)
                        Text(
                            L10n.tr(
                                "%@：%d · %d/%d 已开启 · %@",
                                localizedItemLabel,
                                family.candidates.count,
                                enabledCount,
                                family.candidates.count,
                                connection.name
                            )
                        )
                            .font(Typography.micro)
                            .foregroundStyle(IslandVisual.tertiaryText)
                    }
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .frame(width: Layout.ingressModelFamilyIdentityWidth, alignment: .leading)
            .islandPointerOnHover()

            Spacer(minLength: 8)

            Text(
                L10n.tr(
                    enabledCount == family.candidates.count
                        ? "整组开启"
                        : enabledCount == 0 ? "整组关闭" : "部分开启"
                )
            )
                .font(Typography.micro)
                .foregroundStyle(
                    enabledCount > 0 && enabledCount < family.candidates.count
                        ? IslandColor.interaction
                        : IslandVisual.tertiaryText
                )

            modelFamilyEnableControl(
                connectionID: family.connectionID,
                candidateIDs: family.candidates.map(\.id),
                enabledCount: enabledCount
            )

            if let onRemove {
                Menu {
                    Button("移除模型簇", role: .destructive) {
                        onRemove()
                    }
                } label: {
                    Image(systemName: "ellipsis")
                        .frame(width: 24, height: 24)
                }
                .menuStyle(.borderlessButton)
                .menuIndicator(.hidden)
                .help(L10n.tr("更多模型簇操作"))
                .disabled(
                    selectionStore.snapshot?.runtime.isRunning == true ||
                    selectionStore.snapshot?.runtime.hasResumableRun == true ||
                    settings.isSaving ||
                    settings.endpoint.isRunning
                )
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, LayoutRhythm.standard)
        .padding(.vertical, 12)
    }

    private func modelFamilyEnableControl(
        connectionID: String,
        candidateIDs: [String],
        enabledCount: Int
    ) -> some View {
        let totalCount = candidateIDs.count
        let isFullyEnabled = totalCount > 0 && enabledCount == totalCount
        let isPartiallyEnabled = enabledCount > 0 && enabledCount < totalCount
        let stateTitle = L10n.tr(
            isFullyEnabled ? "整组开启" : isPartiallyEnabled ? "部分开启" : "整组关闭"
        )

        return Button {
            settings.setModelCandidatesEnabled(
                connectionID: connectionID,
                candidateIDs: candidateIDs,
                enabled: !isFullyEnabled
            )
        } label: {
            ZStack {
                Capsule()
                    .fill(
                        isFullyEnabled
                            ? IslandColor.interaction.opacity(0.72)
                            : isPartiallyEnabled
                                ? IslandColor.interaction.opacity(0.28)
                                : Color.white.opacity(0.12)
                    )
                    .overlay {
                        Capsule()
                            .strokeBorder(
                                isPartiallyEnabled
                                    ? IslandColor.interaction.opacity(0.52)
                                    : Color.white.opacity(0.08),
                                lineWidth: 0.5
                            )
                    }
                    .frame(width: 34, height: 18)

                Circle()
                    .fill(isPartiallyEnabled ? IslandColor.interaction : IslandVisual.primaryText)
                    .frame(width: 14, height: 14)
                    .overlay {
                        if isPartiallyEnabled {
                            Image(systemName: "minus")
                                .font(Typography.micro.weight(.bold))
                                .foregroundStyle(IslandVisual.primaryActionText)
                        }
                    }
                    .offset(x: isFullyEnabled ? 8 : isPartiallyEnabled ? 0 : -8)
            }
            .frame(width: 34, height: 20)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .islandPointerOnHover(
            enabled: !settings.isSaving && !settings.endpoint.isRunning
        )
        .disabled(settings.isSaving || settings.endpoint.isRunning)
        .help(isFullyEnabled ? L10n.tr("关闭整组扫描档位") : L10n.tr("开启整组扫描档位"))
        .accessibilityLabel(L10n.tr("整组扫描档位"))
        .accessibilityValue(stateTitle)
        .animation(reduceMotion ? nil : .interactionFeedback, value: enabledCount)
    }

    private func candidateRow(
        _ candidate: BridgeIngressModelCandidate,
        connection: BridgeIngressConnection,
        accent: Color
    ) -> some View {
        let evidence = selectionStore.snapshot?.dashboard.cards.first {
            $0.candidateId == candidate.id
        }
        let evidencePresentation = SettingsCandidatePresenter.evidencePresentation(for: evidence)
        return HStack(spacing: LayoutRhythm.standard) {
            Toggle(
                "",
                isOn: Binding(
                    get: { candidate.enabled },
                    set: { enabled in
                        settings.setModelCandidateEnabled(
                            connectionID: candidate.connectionId,
                            candidateID: candidate.id,
                            enabled: enabled
                        )
                    }
                )
            )
            .labelsHidden()
            .toggleStyle(.switch)
            .controlSize(.small)
            .disabled(settings.isSaving || settings.endpoint.isRunning)

            VStack(alignment: .leading, spacing: 4) {
                Text(settingsCandidatePresentation(for: candidate).variantName)
                    .font(Typography.label)
                    .foregroundStyle(IslandVisual.primaryText)
                Text("\(candidate.modelId) · \(connection.name)")
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.tertiaryText)
                Text(evidencePresentation.text)
                    .font(Typography.micro)
                    .foregroundStyle(candidateEvidenceColor(evidencePresentation.tone, accent: accent))
            }

            Spacer(minLength: 8)

            Button {
                selectionStore.startSingleScan(candidateID: candidate.id, conflictPresentation: .settings)
            } label: {
                Text("单独扫描")
            }
            .buttonStyle(IslandActionButtonStyle(.secondary))
            .fixedSize(horizontal: true, vertical: false)
            .disabled(settings.isSaving)
        }
        .padding(10)
        .overlay(alignment: .bottom) {
            Rectangle().fill(IslandVisual.hairline).frame(height: 0.5)
        }
    }

    private func candidateEvidenceColor(
        _ tone: SettingsCandidateEvidenceTone,
        accent: Color
    ) -> Color {
        switch tone {
        case .muted:
            return IslandVisual.tertiaryText
        case .warning:
            return IslandColor.alertAmber
        case .accent:
            return accent.opacity(0.85)
        }
    }

    private var customScanSheet: some View {
        let selectedCandidateIDs = selectedCustomCandidateIDs
        let request = CustomScanPreviewRequest(
            candidateIDs: Array(selectedCandidateIDs).sorted(),
            evaluationProfileID: selectionStore.selectedEvaluationProfile?.id
        )
        let pendingPresentation = ScanPlanOptionPresentation(
            isEnabled: false,
            subtitle: customScanPreviewError ?? "正在校验扫描计划…"
        )
        let appendPresentation = customScanPlanOptions.map {
            ScanPlanPreviewPresenter.option(for: $0.append, isAppend: true)
        } ?? pendingPresentation
        let newRoundPresentation = customScanPlanOptions.map {
            ScanPlanPreviewPresenter.option(for: $0.newRound, isAppend: false)
        } ?? pendingPresentation
        let selectedPreview = customRoundMode == "append"
            ? customScanPlanOptions?.append
            : customScanPlanOptions?.newRound
        let canStart = selectedPreview?.valid == true
        let selectionStatus = selectedPreview.map { preview in
            preview.valid
                ? L10n.tr(
                    "已选 %d 个配置 · %d 次评测",
                    selectedCandidateIDs.count,
                    preview.totalEvaluations
                )
                : ScanPlanPreviewPresenter.failureText(reason: preview.reason)
        } ?? (customScanPreviewError ?? "正在校验所选配置…")
        return VStack(alignment: .leading, spacing: LayoutRhythm.section) {
            VStack(alignment: .leading, spacing: LayoutRhythm.compact) {
                Text(L10n.tr("自定义本轮"))
                    .font(Typography.pageTitle)
                    .foregroundStyle(IslandVisual.primaryText)
                Text(L10n.tr("仅影响这一次扫描，不会修改常规集合。"))
                    .font(Typography.settingsCardBody)
                    .foregroundStyle(IslandVisual.secondaryText)
            }

            VStack(alignment: .leading, spacing: LayoutRhythm.compact) {
                Text(L10n.tr("运行方式"))
                    .font(Typography.sectionLabel)
                    .foregroundStyle(IslandVisual.tertiaryText)
                VStack(spacing: 0) {
                    customRoundModeOption(
                        title: "追加到当前轮",
                        subtitle: appendPresentation.subtitle,
                        mode: "append",
                        isEnabled: appendPresentation.isEnabled
                    )
                    Divider()
                        .overlay(IslandVisual.hairline)
                    customRoundModeOption(
                        title: "作为新一轮运行",
                        subtitle: newRoundPresentation.subtitle,
                        mode: "new_round",
                        isEnabled: newRoundPresentation.isEnabled
                    )
                }
                .background(
                    RoundedRectangle(cornerRadius: IslandRadius.card)
                        .fill(Color.white.opacity(0.03))
                        .overlay(
                            RoundedRectangle(cornerRadius: IslandRadius.card)
                                .strokeBorder(.white.opacity(0.06), lineWidth: 0.5)
                        )
                )
            }

            if let profile = selectionStore.selectedEvaluationProfile {
                HStack(spacing: 8) {
                    Text(L10n.tr("本次评测"))
                        .font(Typography.sectionLabel)
                        .foregroundStyle(IslandVisual.tertiaryText)
                    Text(
                        L10n.tr(
                            "%@ · %d 题",
                            localizedEvaluationProfileLabel(profile),
                            profile.questionCount
                        )
                    )
                        .font(Typography.label)
                        .foregroundStyle(IslandVisual.primaryText)
                    Spacer()
                }
            }

            ScrollView(.vertical, showsIndicators: false) {
                VStack(alignment: .leading, spacing: LayoutRhythm.standard) {
                    if settingsIngressPresentation.customSourceSections.isEmpty {
                        Text(L10n.tr("当前没有可扫描的档位。"))
                            .font(Typography.settingsCardBody)
                            .foregroundStyle(IslandVisual.secondaryText)
                            .frame(maxWidth: .infinity, minHeight: 88, alignment: .center)
                    } else {
                        ForEach(settingsIngressPresentation.customSourceSections) { section in
                            VStack(alignment: .leading, spacing: 8) {
                                Text(L10n.tr(section.source.title))
                                    .font(Typography.sectionLabel)
                                    .foregroundStyle(IslandVisual.tertiaryText)
                                ForEach(section.workspaces) { workspace in
                                    ForEach(workspace.candidates) { candidate in
                                        customCandidateToggle(
                                            candidate,
                                            connection: workspace.connection
                                        )
                                    }
                                }
                            }
                        }
                    }
                }
            }

            HStack {
                Text(L10n.tr(selectionStatus))
                    .font(Typography.micro)
                    .foregroundStyle(
                        canStart
                            ? IslandVisual.tertiaryText
                            : IslandColor.alertAmber.opacity(0.82)
                    )
                Spacer()
                Button(L10n.tr("取消")) {
                    showsCustomScanSheet = false
                }
                .buttonStyle(IslandActionButtonStyle(.secondary))
                Button(L10n.tr("开始扫描")) {
                    guard let selectedPreview, selectedPreview.valid else { return }
                    selectionStore.startCustomScan(
                        preview: selectedPreview,
                        conflictPresentation: .settings
                    )
                    showsCustomScanSheet = false
                }
                .buttonStyle(IslandActionButtonStyle(.primary))
                .disabled(!canStart || settings.isSaving)
            }
        }
        .padding(LayoutRhythm.section)
        .frame(width: 520, height: 560)
        .background(IslandColor.canvas)
        .preferredColorScheme(.dark)
        .environment(\.locale, appLanguage.locale)
        .task(id: request) {
            await loadCustomScanPlanOptions(request)
        }
    }

    private func customCandidateToggle(
        _ candidate: BridgeIngressModelCandidate,
        connection: BridgeIngressConnection
    ) -> some View {
        Button {
            if customCandidateIDs.contains(candidate.id) {
                customCandidateIDs.remove(candidate.id)
            } else {
                customCandidateIDs.insert(candidate.id)
            }
        } label: {
            HStack(spacing: 10) {
                Image(systemName: customCandidateIDs.contains(candidate.id) ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(customCandidateIDs.contains(candidate.id) ? IslandColor.interaction : IslandVisual.hintText)
                VStack(alignment: .leading, spacing: 3) {
                    Text(candidateDisplayName(candidate))
                        .font(Typography.label)
                        .foregroundStyle(IslandVisual.primaryText)
                    Text(candidateContextText(candidate, connection: connection))
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.tertiaryText)
                }
                Spacer()
            }
            .padding(10)
            .background(
                RoundedRectangle(cornerRadius: IslandRadius.control)
                    .fill(Color.white.opacity(customCandidateIDs.contains(candidate.id) ? 0.07 : 0.03))
            )
        }
        .buttonStyle(.plain)
    }

    private func initializeCustomCandidateIDs() {
        let candidateIDs = customScanEligibleCandidateIDs
        if selectionStore.selectedEvaluationProfile?.id == "quick" {
            if let currentID = selectionStore.radarRepresentativeConfigurationID,
               candidateIDs.contains(currentID) {
                customCandidateIDs = [currentID]
            } else {
                customCandidateIDs = []
            }
        } else {
            customCandidateIDs = candidateIDs
        }
        customRoundMode = "new_round"
        customRoundModeWasManuallySelected = false
    }

    @MainActor
    private func loadCustomScanPlanOptions(
        _ request: CustomScanPreviewRequest
    ) async {
        customScanPlanOptions = nil
        customScanPreviewError = nil
        do {
            let options = try await selectionStore.previewCustomScanOptions(
                candidateIDs: request.candidateIDs,
                evaluationProfileID: request.evaluationProfileID
            )
            guard !Task.isCancelled else { return }
            customScanPlanOptions = options
            if customRoundMode == "append" && !options.append.valid {
                customRoundMode = "new_round"
                customRoundModeWasManuallySelected = false
            } else if !customRoundModeWasManuallySelected {
                customRoundMode = options.append.valid ? "append" : "new_round"
            }
        } catch {
            guard !Task.isCancelled else { return }
            customScanPreviewError = error.localizedDescription
            customRoundMode = "new_round"
            customRoundModeWasManuallySelected = false
        }
    }

    private func customRoundModeOption(
        title: String,
        subtitle: String,
        mode: String,
        isEnabled: Bool
    ) -> some View {
        Button {
            guard isEnabled else { return }
            customRoundMode = mode
            customRoundModeWasManuallySelected = true
        } label: {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: customRoundMode == mode ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(
                        isEnabled
                            ? (customRoundMode == mode ? IslandColor.interaction : IslandVisual.hintText)
                            : IslandVisual.hintText.opacity(0.5)
                    )
                VStack(alignment: .leading, spacing: 4) {
                    Text(L10n.tr(title))
                        .font(Typography.label)
                        .foregroundStyle(isEnabled ? IslandVisual.primaryText : IslandVisual.tertiaryText)
                    Text(L10n.tr(subtitle))
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.tertiaryText)
                        .multilineTextAlignment(.leading)
                }
                Spacer(minLength: 0)
            }
            .padding(LayoutRhythm.standard)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(!isEnabled)
        .opacity(isEnabled ? 1 : 0.58)
    }

    private func ingressMetaPill(title: String, subtitle: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(L10n.tr(title))
                .font(Typography.label)
                .foregroundStyle(IslandVisual.primaryText)
            Text(L10n.tr(subtitle))
                .font(Typography.micro)
                .foregroundStyle(IslandVisual.tertiaryText)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: IslandRadius.card)
                .fill(Color.white.opacity(0.04))
                .overlay(
                    RoundedRectangle(cornerRadius: IslandRadius.card)
                        .strokeBorder(.white.opacity(0.06), lineWidth: 0.5)
                )
        )
    }

    private func source(for sourceID: String) -> BridgeIngressSource? {
        settingsIngressPresentation.source(id: sourceID)
    }

    private func settingsCandidatePresentation(
        for candidate: BridgeIngressModelCandidate
    ) -> SettingsCandidatePresentation {
        settingsIngressPresentation.candidatePresentation(for: candidate)
    }

    private func modelFamilyGroups(for connection: BridgeIngressConnection) -> [ModelFamilyGroup] {
        settingsIngressPresentation.modelFamilyGroups(for: connection)
    }

    private func candidateDisplayName(
        _ candidate: BridgeIngressModelCandidate
    ) -> String {
        settingsCandidatePresentation(for: candidate).displayName
    }

    private func candidateContextText(
        _ candidate: BridgeIngressModelCandidate,
        connection: BridgeIngressConnection
    ) -> String {
        "\(candidate.modelId) · \(connection.name)"
    }

    private func uniqueModelNames(in candidates: [BridgeIngressModelCandidate]) -> [String] {
        SettingsIngressPresenter.uniqueModelNames(in: candidates)
    }

    private func apiModelVariantName(
        _ candidate: BridgeIngressModelCandidate
    ) -> String {
        settingsCandidatePresentation(for: candidate).variantName
    }

    private func timeText(hour: Int, minute: Int) -> String {
        String(format: "%02d:%02d", hour, minute)
    }

    private func weekdayTitle(_ value: Int) -> String {
        switch value {
        case 1: return "周一"
        case 2: return "周二"
        case 3: return "周三"
        case 4: return "周四"
        case 5: return "周五"
        case 6: return "周六"
        default: return "周日"
        }
    }

    private func syncSchedulerFields() {
        guard let scheduler = settings.draftConfig?.scheduler else { return }
        withFieldHydration {
            schedulerEnabled = scheduler.enabled
            schedulerMode = scheduler.mode
            intervalSeconds = scheduler.intervalSeconds
            dailyTime = makeTimeDate(hour: scheduler.dailyHour, minute: scheduler.dailyMinute)
            weeklyWeekday = scheduler.weeklyWeekday
            weeklyTime = makeTimeDate(hour: scheduler.weeklyHour, minute: scheduler.weeklyMinute)
        }
    }

    private func syncScanExecutionFields() {
        guard let system = settings.draftConfig?.system else { return }
        withFieldHydration {
            maxConcurrentTargets = max(1, system.maxConcurrentTargets)
            executionTimeoutSeconds = max(60, system.executionTimeoutSeconds)
            timeoutRetryCount = max(0, system.timeoutRetryCount)
        }
    }

    private var isHydratingFields: Bool {
        settingsHydrationDepth > 0
    }

    private func withFieldHydration(_ update: () -> Void) {
        settingsHydrationDepth += 1
        update()
        DispatchQueue.main.async {
            settingsHydrationDepth = max(0, settingsHydrationDepth - 1)
        }
    }

    private func persistScanExecutionIfNeeded() {
        guard !isHydratingFields else { return }
        guard let system = settings.draftConfig?.system else { return }
        let resolvedConcurrency = max(1, maxConcurrentTargets)
        let resolvedTimeout = max(60, executionTimeoutSeconds)
        let resolvedRetryCount = max(0, timeoutRetryCount)
        guard resolvedConcurrency != max(1, system.maxConcurrentTargets)
            || resolvedTimeout != max(60, system.executionTimeoutSeconds)
            || resolvedRetryCount != max(0, system.timeoutRetryCount) else { return }
        settings.setScanExecution(
            maxConcurrentTargets: resolvedConcurrency,
            executionTimeoutSeconds: resolvedTimeout,
            timeoutRetryCount: resolvedRetryCount
        )
    }

    private var schedulerEnabledBinding: Binding<Bool> {
        Binding<Bool>(
            get: { schedulerEnabled },
            set: {
                schedulerEnabled = $0
                settings.setSchedulerEnabled($0)
            }
        )
    }

    private var launchAtLoginBinding: Binding<Bool> {
        Binding<Bool>(
            get: { launchAtLoginStore.isEnabled },
            set: { launchAtLoginStore.setEnabled($0) }
        )
    }

    private var schedulerModeBinding: Binding<String> {
        Binding<String>(
            get: { schedulerMode },
            set: {
                schedulerMode = $0
                settings.setSchedulerMode($0)
            }
        )
    }

    private var intervalSecondsBinding: Binding<Int> {
        Binding<Int>(
            get: { intervalSeconds },
            set: {
                intervalSeconds = $0
                settings.setScheduler(mode: "interval", intervalSeconds: $0)
            }
        )
    }

    private var dailyTimeBinding: Binding<Date> {
        Binding<Date>(
            get: { dailyTime },
            set: {
                dailyTime = $0
                let components = timeParts(from: $0)
                settings.setDailySchedule(hour: components.hour, minute: components.minute)
            }
        )
    }

    private var weeklyWeekdayBinding: Binding<Int> {
        Binding<Int>(
            get: { weeklyWeekday },
            set: {
                weeklyWeekday = $0
                let components = timeParts(from: weeklyTime)
                settings.setWeeklySchedule(weekday: $0, hour: components.hour, minute: components.minute)
            }
        )
    }

    private var weeklyTimeBinding: Binding<Date> {
        Binding<Date>(
            get: { weeklyTime },
            set: {
                weeklyTime = $0
                let components = timeParts(from: $0)
                settings.setWeeklySchedule(weekday: weeklyWeekday, hour: components.hour, minute: components.minute)
            }
        )
    }

    private func makeTimeDate(hour: Int, minute: Int) -> Date {
        let calendar = Calendar.autoupdatingCurrent
        return calendar.date(
            bySettingHour: hour,
            minute: minute,
            second: 0,
            of: Date()
        ) ?? Date()
    }

    private func timeParts(from date: Date) -> (hour: Int, minute: Int) {
        let calendar = Calendar.autoupdatingCurrent
        let hour = calendar.component(.hour, from: date)
        let minute = calendar.component(.minute, from: date)
        return (hour, minute)
    }

    private var targetDisplaySelection: Binding<String> {
        Binding<String>(
            get: {
                switch targetDisplayStore.choice {
                case .auto:
                    return "auto"
                case .stable(let id):
                    return DisplayInfo.all().contains(where: { $0.stableID == id }) ? id : "auto"
                }
            },
            set: { rawValue in
                if rawValue == "auto" {
                    targetDisplayStore.choice = .auto
                } else {
                    targetDisplayStore.choice = .stable(id: rawValue)
                }
            }
        )
    }
}
