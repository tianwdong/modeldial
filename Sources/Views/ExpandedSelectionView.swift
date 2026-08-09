import AppKit
import SwiftUI

struct ExpandedSelectionView: View, Equatable {
    private enum ScanConfirmation {
        case start
        case pause
        case stop

        var title: String {
            switch self {
            case .start: return L10n.tr("开始新一轮扫描？")
            case .pause: return L10n.tr("暂停当前扫描？")
            case .stop: return L10n.tr("停止当前扫描？")
            }
        }

        var actionTitle: String {
            switch self {
            case .start: return L10n.tr("开始扫描")
            case .pause: return L10n.tr("暂停扫描")
            case .stop: return L10n.tr("停止扫描")
            }
        }

        var role: ButtonRole? {
            switch self {
            case .start, .pause: return nil
            case .stop: return .destructive
            }
        }
    }

    @ObservedObject var store: AppSessionStore
    @ObservedObject private var appLanguage = AppLanguageStore.shared
    let expandedSize: CGSize
    let notchHeight: CGFloat
    let entryDestination: GlanceDestination
    let transitionNamespace: Namespace.ID
    let onCollapse: () -> Void
    @ObservedObject private var settings = SelectionSettingsStore.shared
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.colorSchemeContrast) private var colorSchemeContrast
    @State private var pageIndex = 0
    @State private var selectedEvidence: RadarEvidenceSelection?
    @State private var showsCurrentInUsePicker = false
    @State private var showsRadarSessionsPopover = false
    @State private var showsScanModelPicker = false
    @State private var showsEvaluationProfilePopover = false
    @State private var showsEvaluationProfileDecision = false
    @State private var exportErrorMessage: String?
    @State private var exportedLeaderboardURL: URL?
    @State private var pendingScanConfirmation: ScanConfirmation?
    @Namespace private var pageSelectionNamespace

    private var questionSemantics: [QuestionSemantic] {
        store.radarQuestionSemantics
    }

    private var questionRoundLabel: String {
        questionSemantics.isEmpty ? L10n.tr("同轮") : L10n.tr("%d 题", questionSemantics.count)
    }

    private var completeQuestionSetLabel: String {
        questionSemantics.isEmpty
            ? L10n.tr("同题包完整结果")
            : L10n.tr("同题包完整 %d 题", questionSemantics.count)
    }

    var body: some View {
        ZStack {
            VStack(spacing: 0) {
                panelHeader
                pagedContent
                panelFooter
            }

            if let selectedEvidence {
                evidenceBackdrop

                Group {
                    switch selectedEvidence {
                    case .local(let entry, let evidenceState):
                        CandidateEvidenceDetailView(
                            entry: entry,
                            evidenceState: evidenceState,
                            onDismiss: dismissEvidence
                        )
                    case .official(let entry, let sourceSnapshot):
                        OfficialCandidateEvidenceDetailView(
                            entry: entry,
                            sourceSnapshot: sourceSnapshot,
                            questions: sourceSnapshot.leaderboardProjection?.questions ?? [],
                            onDismiss: dismissEvidence
                        )
                    }
                }
                .frame(
                    width: min(620, expandedSize.width - 72),
                    height: min(460, max(360, expandedSize.height * 0.82))
                )
                .zIndex(2)
            }

            if showsCurrentInUsePicker {
                currentInUsePickerBackdrop
                currentInUsePicker
                    .frame(
                        width: min(500, expandedSize.width - 80),
                        height: min(520, max(360, expandedSize.height * 0.82))
                    )
                    .zIndex(4)
            }
        }
        .frame(
            width: expandedSize.width,
            height: expandedSize.height,
            alignment: .topLeading
        )
        .background(IslandVisual.panelBackground(reduceTransparency: reduceTransparency))
        .overlay {
            if colorSchemeContrast == .increased {
                IslandShape.expanded
                    .strokeBorder(IslandVisual.border(increasedContrast: true), lineWidth: 1)
                    .allowsHitTesting(false)
            }
        }
        .onAppear {
            applyEntryDestination(entryDestination)
        }
        .onChange(of: entryDestination) { destination in
            applyEntryDestination(destination)
        }
        .onChange(of: store.radarDisplaySource) { _ in
            selectedEvidence = nil
        }
        .onChange(of: store.radarResultsUpdatedAt) { _ in
            selectedEvidence = nil
        }
        .onExitCommand {
            handleExitCommand()
        }
        .alert(
            pendingScanConfirmation?.title ?? L10n.tr("确认扫描操作"),
            isPresented: scanConfirmationIsPresented,
            presenting: pendingScanConfirmation
        ) { confirmation in
            Button(confirmation.actionTitle, role: confirmation.role) {
                performConfirmedScanAction(confirmation)
            }
            Button(L10n.tr("取消"), role: .cancel) {}
        } message: { confirmation in
            Text(scanConfirmationMessage(for: confirmation))
        }
        .alert(L10n.tr("无法开始扫描"), isPresented: scanConflictAlertIsPresented) {
            Button(L10n.tr("知道了"), role: .cancel) {
                store.dismissScanConflict()
            }
        } message: {
            Text(L10n.tr(store.scanConflictMessage ?? "已有扫描任务占用运行队列。"))
        }
        .alert(L10n.tr("无法导出榜单"), isPresented: exportErrorIsPresented) {
            Button(L10n.tr("知道了"), role: .cancel) {
                exportErrorMessage = nil
            }
        } message: {
            Text(L10n.tr(exportErrorMessage ?? "请稍后重试。"))
        }
        .alert(L10n.tr("榜单已导出"), isPresented: leaderboardExportSuccessIsPresented, presenting: exportedLeaderboardURL) { url in
            Button(L10n.tr("打开图片")) {
                NSWorkspace.shared.open(url)
            }
            Button(L10n.tr("在 Finder 中显示")) {
                NSWorkspace.shared.activateFileViewerSelecting([url])
            }
            Button(L10n.Common.done, role: .cancel) {
                exportedLeaderboardURL = nil
            }
        } message: { url in
            Text(L10n.tr(
                "已保存为 %@\n%@",
                url.lastPathComponent,
                url.deletingLastPathComponent().path
            ))
        }
        .alert(L10n.tr("扫描档位已变化"), isPresented: $showsEvaluationProfileDecision) {
            if let delta = evaluationProfileScopePresentation.delta,
               delta.currentCount > 0,
               let target = store.upgradeEvaluationProfile {
                Button(evaluationProfileScopePresentation.currentSelectionFullScanTitle) {
                    store.upgradeCurrentSelectionEvaluationProfile(
                        profileID: target.id,
                        candidateIDs: Array(delta.currentCandidateIDs).sorted()
                    )
                }
                .keyboardShortcut(.defaultAction)
            }
            Button(evaluationProfileScopePresentation.originalRoundUpgradeTitle) {
                store.upgradeCurrentEvaluationProfile()
            }
            Button(L10n.tr("取消"), role: .cancel) {}
        } message: {
            Text(evaluationProfileScopePresentation.decisionMessage)
        }
    }

    static func == (lhs: ExpandedSelectionView, rhs: ExpandedSelectionView) -> Bool {
        lhs.expandedSize == rhs.expandedSize
            && lhs.notchHeight == rhs.notchHeight
            && lhs.entryDestination == rhs.entryDestination
    }

    private var scanConflictAlertIsPresented: Binding<Bool> {
        Binding(
            get: {
                store.scanConflictMessage != nil
                    && store.scanConflictPresentation == .expanded
            },
            set: { isPresented in
                if !isPresented {
                    store.dismissScanConflict()
                }
            }
        )
    }

    private var scanConfirmationIsPresented: Binding<Bool> {
        Binding(
            get: { pendingScanConfirmation != nil },
            set: { isPresented in
                if !isPresented {
                    pendingScanConfirmation = nil
                }
            }
        )
    }

    private func scanConfirmationMessage(
        for confirmation: ScanConfirmation
    ) -> String {
        switch confirmation {
        case .start:
            let profileLabel = displayedEvaluationProfile.map(localizedEvaluationProfileLabel)
                ?? L10n.tr("当前评测范围")
            return L10n.tr(
                "将按“%@”启动新一轮扫描，共 %d 个档位。扫描会调用模型并可能产生 Token 消耗。",
                profileLabel,
                scanExecutionCandidateCount
            )
        case .pause:
            return L10n.tr("正在执行的请求会被终止；已完成进度会保留，可以稍后继续。")
        case .stop:
            return L10n.tr("正在执行的请求会被终止；未完成进度会被放弃，停止后不能继续本轮扫描。")
        }
    }

    private var exportErrorIsPresented: Binding<Bool> {
        Binding(
            get: { exportErrorMessage != nil },
            set: { isPresented in
                if !isPresented {
                    exportErrorMessage = nil
                }
            }
        )
    }

    private var leaderboardExportSuccessIsPresented: Binding<Bool> {
        Binding(
            get: { exportedLeaderboardURL != nil },
            set: { isPresented in
                if !isPresented {
                    exportedLeaderboardURL = nil
                }
            }
        )
    }

    private func handleExitCommand() {
        if selectedEvidence != nil {
            selectedEvidence = nil
        } else if showsCurrentInUsePicker {
            showsCurrentInUsePicker = false
        } else if showsScanModelPicker {
            showsScanModelPicker = false
        } else {
            onCollapse()
        }
    }

    private func openSettings() {
        DebugLog.write("SettingsButton.tap")
        onCollapse()
        DispatchQueue.main.async {
            SettingsWindowController.shared.show()
        }
    }

    private func openModelIngress() {
        DebugLog.write("ModelSetup.openModelIngress")
        onCollapse()
        DispatchQueue.main.async {
            SettingsWindowController.shared.show(destination: .modelIngress)
        }
    }

    private func applyEntryDestination(_ destination: GlanceDestination) {
        switch destination {
        case .overview, .runProgress, .recommendationIssue, .rescan, .connectionDiagnostics:
            pageIndex = 0
            selectedEvidence = nil
        case .failureEvidence:
            pageIndex = 1
            selectedEvidence = nil
        }
    }

    private var evidenceBackdrop: some View {
        Color.black.opacity(0.58)
            .contentShape(Rectangle())
            .onTapGesture(perform: dismissEvidence)
            .zIndex(1)
    }

    private func dismissEvidence() {
        selectedEvidence = nil
    }

    private var expandedShellBodyInset: CGFloat {
        IslandShape.expandedShoulderRadius
    }

    private var expandedHeaderHorizontalInset: CGFloat {
        LayoutRhythm.section + expandedShellBodyInset
    }

    private var expandedContentHorizontalInset: CGFloat {
        LayoutRhythm.large + expandedShellBodyInset
    }

    private var currentInUsePickerBackdrop: some View {
        Color.black.opacity(0.58)
            .contentShape(Rectangle())
            .onTapGesture { showsCurrentInUsePicker = false }
            .zIndex(3)
    }

    @ViewBuilder
    private var panelHeader: some View {
        if pageIndex == 1 {
            panelHeaderContainer {
                detailPanelHeader
            }
        } else {
            panelHeaderContainer {
                overviewPanelHeader
            }
        }
    }

    private func panelHeaderContainer<Content: View>(
        @ViewBuilder content: () -> Content
    ) -> some View {
        HStack(alignment: .center, spacing: LayoutRhythm.standard) {
            content()
                .frame(maxWidth: .infinity, alignment: .leading)

            headerToolControls
        }
        .padding(.horizontal, expandedHeaderHorizontalInset)
        .background(IslandColor.chrome)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(IslandVisual.workspaceBorder)
                .frame(height: 0.5)
        }
    }

    private func collapseHeaderLead<Content: View>(
        @ViewBuilder content: () -> Content
    ) -> some View {
        Button(action: onCollapse) {
            HStack(spacing: LayoutRhythm.standard) {
                Image(systemName: "chevron.up")
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.secondaryText)
                    .frame(width: 30, height: 30)
                    .background(
                        RoundedRectangle(cornerRadius: IslandRadius.control)
                            .fill(IslandVisual.surfaceSubtle)
                            .overlay(
                                RoundedRectangle(cornerRadius: IslandRadius.control)
                                    .strokeBorder(IslandVisual.hairline, lineWidth: 0.5)
                            )
                    )

                content()
                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(L10n.Common.collapse)
        .accessibilityLabel(L10n.Common.collapse)
        .islandPointerOnHover()
    }

    private var headerToolControls: some View {
        HStack(spacing: 6) {
            Rectangle()
                .fill(IslandVisual.hairline)
                .frame(width: 0.5, height: 18)
                .padding(.horizontal, 2)

            Button(action: exportLeaderboardImage) {
                Image(systemName: "square.and.arrow.up")
                    .symbolRenderingMode(.hierarchical)
            }
            .buttonStyle(IslandIconButtonStyle())
            .disabled(!canExportLeaderboard)
            .help(
                canExportLeaderboard
                    ? L10n.tr("导出榜单")
                    : L10n.tr("暂无可导出的完整成绩")
            )
            .accessibilityLabel(L10n.tr("导出榜单"))

            SettingsButton(action: openSettings)
            QuitButton()
        }
        .fixedSize(horizontal: true, vertical: false)
    }

    private var overviewPanelHeader: some View {
        HStack(alignment: .center, spacing: LayoutRhythm.section) {
            collapseHeaderLead {
                HStack(spacing: LayoutRhythm.compact) {
                    Circle()
                        .fill(heroAccentColor)
                        .frame(width: 10, height: 10)

                    Text(L10n.Overview.recommendationDecision)
                        .font(Typography.sectionTitle)
                        .foregroundStyle(IslandVisual.primaryText)
                        .lineLimit(1)
                }
            }

            scanModelPickerButton(title: overviewModelCountText)
        }
        .overlay {
            if isEvidenceUpdating {
                Text(store.runtimeProgressText)
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.tertiaryText)
                    .lineLimit(1)
                    .allowsHitTesting(false)
                    .islandMatchedGeometry(
                        id: IslandTransitionElement.secondaryStatus.rawValue,
                        in: transitionNamespace,
                        isSource: false,
                        reduceMotion: reduceMotion
                    )
            }
        }
        .frame(height: 34)
        .padding(.leading, 0)
        .padding(.trailing, 22)
        .padding(.top, 10)
        .padding(.bottom, max(8, min(16, max(0, notchHeight - 34 - 10))))
    }

    private var detailPanelHeader: some View {
        HStack(alignment: .center, spacing: LayoutRhythm.section) {
            collapseHeaderLead {
                HStack(spacing: LayoutRhythm.compact) {
                    Circle()
                        .fill(IslandColor.interaction)
                        .frame(width: 10, height: 10)

                    Text(detailHeaderPrimaryText)
                        .font(Typography.sectionTitle)
                        .foregroundStyle(IslandVisual.primaryText)
                        .lineLimit(1)
                }
            }

            scanModelPickerButton(title: detailModelCountText)
        }
        .frame(height: 34)
        .padding(.leading, 0)
        .padding(.trailing, 22)
        .padding(.top, 10)
        .padding(.bottom, max(8, min(16, max(0, notchHeight - 34 - 10))))
    }

    private func scanModelPickerButton(title: String) -> some View {
        Button {
            showsScanModelPicker = true
        } label: {
            HStack(spacing: 6) {
                Text(title)
                    .font(Typography.rowTitle)
                    .foregroundStyle(IslandVisual.secondaryText)
                    .lineLimit(1)

                Image(systemName: "chevron.down")
                    .font(Typography.micro)
                    .foregroundStyle(IslandColor.interaction)
            }
            .padding(.horizontal, 8)
            .frame(height: 28)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .popover(isPresented: $showsScanModelPicker, arrowEdge: .top) {
            scanModelPickerPopover
        }
        .disabled(!isModelIngressLoaded || settings.isSaving)
        .help(L10n.tr("选择常规扫描档位"))
        .accessibilityLabel(L10n.tr("选择常规扫描档位"))
        .accessibilityValue(title)
        .islandPointerOnHover(enabled: isModelIngressLoaded && !settings.isSaving)
    }

    private var scanModelPickerPopover: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 4) {
                Text(L10n.tr("扫描档位"))
                    .font(Typography.settingsCardTitle)
                    .foregroundStyle(IslandVisual.primaryText)
                Text(L10n.tr(
                    "已选择 %d/%d 个档位",
                    scanModelPickerSelectedCount,
                    scanModelPickerCandidateCount
                ))
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.secondaryText)
            }
            .padding(.horizontal, LayoutRhythm.standard)
            .padding(.vertical, 12)

            Rectangle()
                .fill(IslandVisual.hairline)
                .frame(height: 0.5)

            if scanModelPickerConnections.isEmpty {
                Text(L10n.tr("当前没有可选择的扫描档位。"))
                    .font(Typography.label)
                    .foregroundStyle(IslandVisual.secondaryText)
                    .frame(maxWidth: .infinity, minHeight: 88, alignment: .center)
                    .padding(.horizontal, LayoutRhythm.standard)
            } else {
                ScrollView(.vertical, showsIndicators: true) {
                    VStack(alignment: .leading, spacing: LayoutRhythm.standard) {
                        ForEach(scanModelPickerConnections) { item in
                            VStack(alignment: .leading, spacing: LayoutRhythm.compact) {
                                HStack(spacing: LayoutRhythm.compact) {
                                    Circle()
                                        .fill(IslandColor.interaction)
                                        .frame(width: 6, height: 6)
                                    Text(item.title)
                                        .font(Typography.label)
                                        .foregroundStyle(IslandVisual.secondaryText)
                                    Spacer(minLength: 8)
                                    Text("\(item.selectedCandidateCount)/\(item.candidates.count)")
                                        .font(Typography.micro)
                                        .foregroundStyle(IslandVisual.tertiaryText)
                                        .monospacedDigit()
                                }

                                ForEach(item.candidates) { candidate in
                                    scanModelPickerRow(candidate)
                                }
                            }
                            .padding(LayoutRhythm.compact)
                            .background(
                                RoundedRectangle(cornerRadius: IslandRadius.control)
                                    .fill(Color.white.opacity(0.03))
                            )
                        }
                    }
                    .padding(LayoutRhythm.standard)
                }
                .frame(maxHeight: 380)
            }

            Rectangle()
                .fill(IslandVisual.hairline)
                .frame(height: 0.5)

            HStack(spacing: LayoutRhythm.compact) {
                if scanModelSelectionIsLocked {
                    Text(L10n.tr("扫描进行中或存在可续扫任务，暂不可修改。"))
                        .font(Typography.micro)
                        .foregroundStyle(IslandColor.alertAmber.opacity(0.82))
                } else if settings.isSaving {
                    ProgressView()
                        .controlSize(.small)
                    Text(L10n.tr("正在保存"))
                        .font(Typography.micro)
                        .foregroundStyle(IslandColor.interaction)
                } else {
                    Text(L10n.tr("修改后自动保存，并同步到两页。"))
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.tertiaryText)
                }

                Spacer(minLength: 8)

                Button(L10n.Common.done) {
                    showsScanModelPicker = false
                }
                .buttonStyle(IslandActionButtonStyle(.secondary))
            }
            .padding(LayoutRhythm.standard)
        }
        .frame(width: 440)
        .background(IslandColor.panelRaised)
        .preferredColorScheme(.dark)
        .environment(\.locale, appLanguage.locale)
    }

    private var scanModelPickerConnections: [OperationalStatePresenter.IngressConnectionPresentation] {
        ingressPresentation.scanConnections
    }

    private var scanModelPickerCandidateCount: Int {
        ingressPresentation.candidateCount
    }

    private var scanModelPickerSelectedCount: Int {
        ingressPresentation.selectedCandidateCount
    }

    private var scanExecutionCandidateCount: Int {
        if displayedEvaluationProfile?.id == "quick" {
            return 2
        }
        return scanModelPickerSelectedCount
    }

    private var scanModelSelectionIsLocked: Bool {
        ingressPresentation.selectionIsLocked
    }

    private func scanModelPickerRow(
        _ candidate: OperationalStatePresenter.IngressCandidatePresentation
    ) -> some View {
        let isEnabled = candidate.isEnabled
        return Toggle(
            isOn: Binding(
                get: { candidate.isEnabled },
                set: { enabled in
                    settings.setModelCandidateEnabled(
                        connectionID: candidate.connectionID,
                        candidateID: candidate.id,
                        enabled: enabled
                    )
                }
            )
        ) {
            HStack(spacing: LayoutRhythm.compact) {
                Text(candidate.pickerLabel)
                    .font(Typography.label)
                    .foregroundStyle(IslandVisual.primaryText)
                    .lineLimit(1)

                Spacer(minLength: 8)

                Text(L10n.tr(isEnabled ? "已选择" : "未选择"))
                    .font(Typography.micro)
                    .foregroundStyle(
                        isEnabled
                            ? IslandColor.interaction
                            : IslandVisual.tertiaryText
                    )
            }
        }
        .toggleStyle(.switch)
        .controlSize(.small)
        .disabled(
            scanModelSelectionIsLocked
                || settings.isSaving
                || settings.endpoint.isRunning
        )
        .accessibilityHint(L10n.tr(
            isEnabled ? "关闭后不再参与常规扫描" : "开启后参与常规扫描"
        ))
    }

    private var pagedContent: some View {
        GeometryReader { geo in
            ZStack(alignment: .topLeading) {
                if pageIndex == 0 {
                    overviewPage
                        .frame(width: geo.size.width, height: geo.size.height, alignment: .topLeading)
                        .clipped()
                        .transition(.opacity)
                } else {
                    detailPage
                        .frame(width: geo.size.width, height: geo.size.height, alignment: .topLeading)
                        .clipped()
                        .transition(.opacity)
                }
            }
            .animation(reduceMotion ? nil : .easeOut(duration: 0.18), value: pageIndex)
            .frame(width: geo.size.width, height: geo.size.height, alignment: .topLeading)
            .clipped()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .clipped()
    }

    private var overviewPage: some View {
        GeometryReader { geo in
            let leftWidth = overviewLeftWidth(for: geo.size.width)

            HStack(alignment: .top, spacing: 0) {
                overviewHeroCard
                    .padding(.horizontal, expandedContentHorizontalInset)
                    .padding(.top, 22)
                    .padding(.bottom, LayoutRhythm.standard)
                    .frame(width: leftWidth, height: geo.size.height, alignment: .topLeading)
                    .clipped()
                    .background(IslandVisual.summarySurface)
                    .overlay(alignment: .top) {
                        Rectangle()
                            .fill(IslandVisual.contentTopHighlight)
                            .frame(height: 0.5)
                    }
                    .overlay(alignment: .trailing) {
                        Rectangle()
                            .fill(IslandVisual.workspaceBorder)
                            .frame(width: 0.5)
                    }
                overviewRankingCard
                    .padding(.horizontal, expandedHeaderHorizontalInset)
                    .padding(.top, LayoutRhythm.compact)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                    .background(IslandVisual.evidenceSurface)
                    .overlay(alignment: .top) {
                        Rectangle()
                            .fill(IslandVisual.contentTopHighlight)
                            .frame(height: 0.5)
                    }
            }
            .frame(width: geo.size.width, height: geo.size.height, alignment: .topLeading)
            .background(IslandVisual.shellSurface)
        }
    }

    private func overviewLeftWidth(for totalWidth: CGFloat) -> CGFloat {
        min(352, max(340, totalWidth * 0.34))
    }

    private var overviewHeroCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            radarControlBar

            heroDecisionHeader

            decisionIdentityStrip
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .padding(.top, 4)
        .padding(.bottom, 6)
    }

    private var heroDecisionHeader: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 8) {
                Text(heroDecisionTitleText)
                    .font(Typography.heroDecision)
                    .tracking(-0.4)
                    .foregroundStyle(IslandVisual.primaryText)
                    .lineLimit(1)
                    .minimumScaleFactor(0.9)
                    .layoutPriority(1)

                Spacer(minLength: 8)

                if showsHeroConfidenceChip {
                    heroConfidenceChip
                        .fixedSize()
                }
            }

            Text(heroDecisionReasonText)
                .font(Typography.settingsCardBody)
                .foregroundStyle(IslandVisual.secondaryText)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var decisionIdentityStrip: some View {
        radarDecisionIdentityStrip
    }

    private var radarDecisionIdentityStrip: some View {
        VStack(alignment: .leading, spacing: 0) {
            heroIdentityRow(label: radarDecisionPresentation.comparisonLabel) {
                VStack(alignment: .leading, spacing: 6) {
                    radarCurrentIdentityLine
                    if let candidate = radarDecisionPresentation.candidateLabel {
                        radarDecisionIdentityLine(
                            value: candidate,
                            color: radarCandidateModelColor,
                            element: .candidateIdentity,
                            showsTransition: true
                        )
                    }
                }
            }

            if radarDecisionPresentation.candidateLabel != nil {
                Rectangle()
                    .fill(IslandVisual.hairline)
                    .frame(height: 0.5)

                HStack(spacing: 0) {
                    radarDecisionMetric(
                        label: "质量",
                        value: radarDecisionPresentation.qualityText,
                        element: .qualityMetric
                    )
                    radarDecisionMetric(
                        label: "时间",
                        value: radarDecisionPresentation.timeText,
                        element: .timeMetric
                    )
                    radarDecisionMetric(
                        label: "参考费用",
                        value: radarDecisionPresentation.referenceCostText,
                        element: .costMetric
                    )
                }
                .padding(.vertical, 12)
            }

            Rectangle()
                .fill(IslandVisual.hairline)
                .frame(height: 0.5)

            radarSessionSummary
        }
        .overlay(alignment: .top) {
            Rectangle()
                .fill(IslandVisual.hairline)
                .frame(height: 0.5)
        }
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(IslandVisual.hairline)
                .frame(height: 0.5)
        }
    }

    private var radarCurrentIdentityLine: some View {
        HStack(alignment: .firstTextBaseline, spacing: 5) {
            Text(radarCurrentModelName)
                .font(Typography.rowTitle)
                .foregroundStyle(IslandVisual.primaryText)
                .lineLimit(1)
                .minimumScaleFactor(0.84)
                .layoutPriority(1)
                .islandMatchedGeometry(
                    id: IslandTransitionElement.primaryIdentity.rawValue,
                    in: transitionNamespace,
                    isSource: false,
                    reduceMotion: reduceMotion
                )

            if let effort = radarCurrentEffortLabel {
                Text(effort)
                    .font(Typography.rowTitle)
                    .foregroundStyle(IslandVisual.secondaryText)
                    .lineLimit(1)
                    .islandMatchedGeometry(
                        id: IslandTransitionElement.secondaryStatus.rawValue,
                        in: transitionNamespace,
                        isSource: false,
                        reduceMotion: reduceMotion
                    )
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(radarCurrentModelLabel)
    }

    private func radarDecisionIdentityLine(
        value: String,
        color: Color,
        element: IslandTransitionElement,
        showsTransition: Bool = false
    ) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 7) {
            if showsTransition {
                Image(systemName: "arrow.turn.down.right")
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.hintText)
            }
            Text(value)
                .font(Typography.rowTitle)
                .foregroundStyle(color)
                .lineLimit(1)
                .minimumScaleFactor(0.84)
                .layoutPriority(1)
                .islandMatchedGeometry(
                    id: element.rawValue,
                    in: transitionNamespace,
                    isSource: false,
                    reduceMotion: reduceMotion
                )
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func radarDecisionMetric(
        label: String,
        value: String,
        element: IslandTransitionElement
    ) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(value)
                .font(Typography.bodyNumber)
                .foregroundStyle(value == "未知" ? IslandVisual.tertiaryText : IslandVisual.primaryText)
                .lineLimit(1)
                .minimumScaleFactor(0.72)
            Text(LocalizedStringKey(label))
                .font(Typography.micro)
                .foregroundStyle(IslandVisual.tertiaryText)
                .lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .islandMatchedGeometry(
            id: element.rawValue,
            in: transitionNamespace,
            isSource: false,
            reduceMotion: reduceMotion
        )
    }

    private var radarSessionPresentation: RadarPresenter.SessionSummary {
        RadarPresenter.sessionSummary(
            sessions: store.activeModelSessions.map { session in
                let sessionPresentation = ActiveSessionPresenter.present(session)
                return RadarPresenter.SessionInput(
                    id: session.id,
                    source: session.source,
                    model: session.model,
                    effort: session.effort,
                    title: sessionPresentation.title,
                    context: sessionPresentation.context,
                    isEvaluationSession: session.isEvaluationSession == true
                )
            },
            isCurrentModelAutomatic: isCurrentModelAutomaticMode,
            currentModelDetectionStatus: currentModelDetectionStatus
        )
    }

    private var radarActiveUsagePresentation: RadarPresenter.ActiveUsagePresentation {
        RadarPresenter.activeUsage(
            sessions: store.activeModelSessions.map {
                RadarPresenter.ActiveUsageSessionInput(
                    id: $0.id,
                    sourceDisplayName: $0.sourceDisplayName,
                    model: $0.model,
                    effort: $0.effort
                )
            }
        )
    }

    private var radarUserSessions: [BridgeDetectedModelSession] {
        let visibleSessionIDs = Set(radarSessionPresentation.visibleSessionIDs)
        return store.activeModelSessions.filter { visibleSessionIDs.contains($0.id) }
    }

    @ViewBuilder
    private var radarSessionSummary: some View {
        let overviewPresentation = ActiveSessionPresenter.overview(radarUserSessions)

        if radarUserSessions.isEmpty {
            HStack(spacing: 8) {
                Image(systemName: "rectangle.stack")
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.hintText)

                Text(radarSessionPresentation.title)
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.tertiaryText)
                    .lineLimit(1)

                Spacer(minLength: 8)
                currentModelActionButton
            }
            .padding(.vertical, 11)
        } else {
            Button {
                showsRadarSessionsPopover.toggle()
            } label: {
                VStack(alignment: .leading, spacing: 0) {
                    HStack(spacing: 8) {
                        Image(systemName: "rectangle.stack")
                            .font(Typography.micro)
                            .foregroundStyle(IslandVisual.hintText)

                        Text(radarSessionPresentation.title)
                            .font(Typography.micro)
                            .foregroundStyle(IslandVisual.secondaryText)
                            .lineLimit(1)

                        Spacer(minLength: 8)

                        Image(systemName: "chevron.right")
                            .font(Typography.micro)
                            .foregroundStyle(IslandVisual.hintText)
                    }
                    .padding(.vertical, 8)

                    ForEach(
                        Array(overviewPresentation.visibleSessions.enumerated()),
                        id: \.element.id
                    ) { index, session in
                        radarSessionSummaryPreview(session)
                            .padding(.leading, 28)
                            .padding(.vertical, 7)

                        if index < overviewPresentation.visibleSessions.count - 1 {
                            Rectangle()
                                .fill(IslandVisual.hairline.opacity(0.72))
                                .frame(height: 0.5)
                                .padding(.leading, 28)
                        }
                    }

                    if overviewPresentation.overflowCount > 0 {
                        Text(L10n.Sessions.overflow(overviewPresentation.overflowCount))
                            .font(Typography.micro)
                            .foregroundStyle(IslandVisual.tertiaryText)
                            .padding(.leading, 28)
                            .padding(.vertical, 7)
                    }
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .islandPointerOnHover()
            .help(L10n.tr("查看全部会话与各配置建议"))
            .accessibilityLabel(radarSessionPresentation.accessibilityLabel)
            .popover(isPresented: $showsRadarSessionsPopover, arrowEdge: .leading) {
                radarSessionsPopover
            }
        }
    }

    private func radarSessionSummaryPreview(
        _ session: ActiveSessionPresenter.SessionPresentation
    ) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(session.title)
                .font(Typography.micro)
                .foregroundStyle(IslandVisual.tertiaryText)
                .lineLimit(1)
                .truncationMode(.tail)
                .layoutPriority(1)

            Spacer(minLength: 8)

            Text(
                radarActiveUsagePresentation.sessionIdentities[session.id]
                    ?? session.sourceDisplayName
            )
                .font(Typography.micro)
                .foregroundStyle(IslandVisual.secondaryText)
                .lineLimit(1)
                .minimumScaleFactor(0.78)
                .frame(maxWidth: 132, alignment: .trailing)
                .layoutPriority(2)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var radarSessionsPopover: some View {
        VStack(alignment: .leading, spacing: 0) {
            if !multiConfigurationDecisions.isEmpty {
                HStack(spacing: 8) {
                    Text(L10n.tr("各配置建议"))
                        .font(Typography.sectionTitle)
                        .foregroundStyle(IslandVisual.primaryText)
                    Text(L10n.tr("同一策略 · 分别计算"))
                        .font(Typography.caption)
                        .foregroundStyle(IslandVisual.hintText)
                    Spacer(minLength: 8)
                }
                .padding(.bottom, 8)

                ScrollView(.vertical, showsIndicators: true) {
                    LazyVStack(spacing: 0) {
                        ForEach(multiConfigurationDecisions, id: \.currentModelConfigurationId) { decision in
                            radarConfigurationDecisionRow(decision)
                        }
                    }
                }
                .frame(maxHeight: 176)

                Rectangle()
                    .fill(IslandVisual.hairline)
                    .frame(height: 0.5)
                    .padding(.vertical, 10)
            }

            HStack(spacing: 8) {
                Text(L10n.tr("活动会话"))
                    .font(Typography.sectionTitle)
                    .foregroundStyle(IslandVisual.primaryText)

                Text(L10n.tr("%d 个", radarUserSessions.count))
                    .font(Typography.caption)
                    .foregroundStyle(IslandVisual.hintText)
                    .monospacedDigit()

                Spacer(minLength: 8)
            }
            .padding(.bottom, 10)

            ScrollView {
                LazyVStack(spacing: 0) {
                    ForEach(Array(radarUserSessions.enumerated()), id: \.element.id) { index, session in
                        overviewActiveSessionRow(ActiveSessionPresenter.present(session))

                        if index < radarUserSessions.count - 1 {
                            Rectangle()
                                .fill(IslandVisual.hairline.opacity(0.72))
                                .frame(height: 0.5)
                                .padding(.leading, 16)
                        }
                    }
                }
            }
            .frame(maxHeight: 260)

            Rectangle()
                .fill(IslandVisual.hairline)
                .frame(height: 0.5)
                .padding(.top, 6)

            HStack(spacing: 8) {
                Text(L10n.tr("当前模型默认根据工作会话自动识别"))
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.tertiaryText)
                    .lineLimit(1)

                Spacer(minLength: 8)
                currentModelActionButton
            }
            .padding(.top, 10)
        }
        .padding(14)
        .frame(width: 360)
        .background(IslandColor.panelRaised)
        .preferredColorScheme(.dark)
        .environment(\.locale, appLanguage.locale)
    }

    private var multiConfigurationDecisions: [BridgeRecommendationDecisionV2] {
        let decisions = store.radarPortfolio?.decisions ?? []
        return decisions.count > 1 ? decisions : []
    }

    private func radarConfigurationDecisionRow(
        _ decision: BridgeRecommendationDecisionV2
    ) -> some View {
        let presentation = radarConfigurationDecisionPresentation(decision)
        return HStack(alignment: .top, spacing: 10) {
            Circle()
                .fill(radarDecisionColor(presentation.emphasis, neutral: IslandVisual.hintText))
                .frame(width: 6, height: 6)
                .padding(.top, 5)

            VStack(alignment: .leading, spacing: 3) {
                Text(store.radarDisplayName(for: decision.currentModelConfigurationId) ?? L10n.tr("当前配置"))
                    .font(Typography.label)
                    .foregroundStyle(IslandVisual.primaryText)
                    .lineLimit(1)
                Text(presentation.text)
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.tertiaryText)
                    .lineLimit(1)
            }
            Spacer(minLength: 8)
        }
        .padding(.vertical, 6)
    }

    private func radarConfigurationDecisionPresentation(
        _ decision: BridgeRecommendationDecisionV2
    ) -> RadarPresenter.ConfigurationDecisionPresentation {
        RadarPresenter.configurationDecision(
            decision: decision,
            target: store.radarDisplayName(
                for: decision.candidateModelConfigurationId
            ) ?? "候选配置"
        )
    }

    private func heroIdentityRow<Content: View>(
        label: String,
        labelColor: Color = IslandVisual.tertiaryText,
        @ViewBuilder content: () -> Content
    ) -> some View {
        HStack(alignment: .center, spacing: 12) {
            Text(label)
                .font(Typography.label)
                .foregroundStyle(labelColor)
                .lineLimit(1)
                .minimumScaleFactor(0.85)
                .frame(width: 76, alignment: .leading)

            content()
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.vertical, 14)
    }

    private var radarControlBar: some View {
        HStack(spacing: 8) {
            radarSourceControl
                .fixedSize(horizontal: true, vertical: false)
            radarPreferenceControl
                .fixedSize(horizontal: true, vertical: false)
        }
    }

    private var radarSourceControl: some View {
        Menu {
            Button(L10n.tr("自动选择（优先本机实测）")) { setRadarSourceMode("auto") }
            Button(L10n.tr("官网榜单")) { setRadarSourceMode("official_snapshot") }
            Button(L10n.tr("本机实测")) { setRadarSourceMode("local_evaluation") }
        } label: {
            radarControlLabel(
                icon: "square.stack.3d.up",
                title: radarSourcePresentation.control
            )
        }
        .menuStyle(.borderlessButton)
        .disabled(store.radarRepresentativeConfigurationID == nil || settings.isSaving)
        .help(L10n.tr("选择本页唯一数据来源"))
        .accessibilityLabel(L10n.tr("数据来源"))
        .accessibilityValue(radarSourcePresentation.accessibilityValue)
    }

    private var radarPreferenceControl: some View {
        Menu {
            Button(L10n.tr("综合平衡")) { settings.setRecommendationPreference("smart") }
            Button(L10n.tr("质量优先")) { settings.setRecommendationPreference("quality") }
            Button(L10n.tr("速度优先")) { settings.setRecommendationPreference("speed") }
            Button(L10n.tr("费用优先")) { settings.setRecommendationPreference("cost") }
        } label: {
            radarControlLabel(
                icon: "slider.horizontal.3",
                title: radarPreferencePresentation
            )
        }
        .menuStyle(.borderlessButton)
        .disabled(settings.isSaving)
        .help(L10n.tr("选择全局推荐策略"))
        .accessibilityLabel(L10n.tr("推荐策略"))
        .accessibilityValue(radarPreferencePresentation)
    }

    private var radarReferenceRefreshControl: some View {
        let feedback = OperationalStatePresenter.referenceRefreshPresentation(
            status: store.referenceSnapshotRefreshFeedbackStatus
        )
        let isRefreshing = store.isReferenceSnapshotRefreshInFlight
        return Button {
            store.refreshReferenceSnapshotNow()
        } label: {
            radarReferenceRefreshLabel(
                isRefreshing: isRefreshing,
                feedback: feedback
            )
        }
        .buttonStyle(.plain)
        .disabled(isRefreshing)
        .help(
            isRefreshing
                ? L10n.tr("正在更新榜单")
                : feedback?.text ?? L10n.tr("立即从官网拉取最新评测结果")
        )
        .accessibilityLabel(L10n.tr("刷新远端结果"))
        .accessibilityValue(
            isRefreshing ? L10n.tr("正在更新榜单") : feedback?.text ?? ""
        )
    }

    @ViewBuilder
    private func radarReferenceRefreshLabel(
        isRefreshing: Bool,
        feedback: OperationalStatePresenter.ReferenceRefreshPresentation?
    ) -> some View {
        HStack(spacing: 5) {
            if isRefreshing {
                ProgressView()
                    .controlSize(.mini)
                Text(L10n.tr("正在更新榜单"))
                    .lineLimit(1)
            } else if let feedback {
                Image(systemName: feedback.symbolName)
                Text(feedback.text)
                    .lineLimit(1)
            } else {
                Image(systemName: "arrow.clockwise")
                    .font(Typography.micro)
            }
        }
        .font(Typography.micro)
        .foregroundStyle(referenceRefreshColor(feedback?.tone ?? .neutral))
        .padding(.horizontal, isRefreshing || feedback != nil ? 8 : 0)
        .frame(minWidth: 28, minHeight: 28, maxHeight: 28)
        .background(
            RoundedRectangle(cornerRadius: IslandRadius.control, style: .continuous)
                .fill(IslandVisual.surfaceSubtle)
                .overlay(
                    RoundedRectangle(cornerRadius: IslandRadius.control, style: .continuous)
                        .strokeBorder(IslandVisual.hairline, lineWidth: 0.5)
                )
        )
        .contentShape(
            RoundedRectangle(cornerRadius: IslandRadius.control, style: .continuous)
        )
    }

    private func referenceRefreshColor(_ tone: GlanceTone) -> Color {
        switch tone {
        case .success:
            return IslandColor.liveTeal
        case .warning:
            return IslandColor.alertAmber
        case .failure:
            return IslandColor.alertRed
        case .neutral, .active:
            return IslandVisual.secondaryText
        }
    }

    private func radarControlLabel(icon: String, title: String) -> some View {
        HStack(spacing: 5) {
            Image(systemName: icon)
                .font(Typography.micro)
            Text(title)
                .font(Typography.label)
                .lineLimit(1)
                .minimumScaleFactor(0.72)
                .layoutPriority(1)
            Image(systemName: "chevron.down")
                .font(Typography.micro)
        }
        .foregroundStyle(IslandVisual.secondaryText)
        .padding(.horizontal, 8)
        .frame(height: 28)
        .background(
            RoundedRectangle(cornerRadius: IslandRadius.control, style: .continuous)
                .fill(IslandVisual.surfaceSubtle)
                .overlay(
                    RoundedRectangle(cornerRadius: IslandRadius.control, style: .continuous)
                        .strokeBorder(IslandVisual.hairline, lineWidth: 0.5)
                )
        )
        .contentShape(RoundedRectangle(cornerRadius: IslandRadius.control, style: .continuous))
    }

    private var currentModelActionButton: some View {
        Button {
            selectedEvidence = nil
            showsRadarSessionsPopover = false
            if requiresModelSetup {
                openModelIngress()
            } else {
                showsCurrentInUsePicker = true
            }
        } label: {
            HStack(spacing: 4) {
                Text(
                    requiresModelSetup
                        ? L10n.Overview.connectModel
                        : L10n.Overview.selectCurrentModel
                )
                Image(systemName: requiresModelSetup ? "arrow.up.right" : "chevron.right")
            }
            .font(Typography.micro)
            .foregroundStyle(IslandVisual.interactionText)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .islandPointerOnHover()
        .accessibilityLabel(requiresModelSetup ? "前往模型接入" : "指定当前模型")
        .accessibilityValue(currentModelModeLabel)
        .accessibilityHint(currentModelPresentation.actionAccessibilityHint)
        .help(requiresModelSetup ? "前往模型接入" : "指定用于推荐的当前模型")
    }

    private func overviewActiveSessionRow(
        _ session: ActiveSessionPresenter.SessionPresentation
    ) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Circle()
                .fill(IslandColor.interaction)
                .frame(width: 6, height: 6)
                .padding(.top, 5)
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 3) {
                Text(session.title)
                    .font(Typography.label)
                    .foregroundStyle(IslandVisual.primaryText)
                    .lineLimit(1)
                    .truncationMode(.tail)

                Text(overviewActiveSessionDetail(session))
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.tertiaryText)
                    .lineLimit(1)
                    .truncationMode(.tail)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .frame(height: 46, alignment: .leading)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(
            "\(session.title)，\(session.context)，活动中"
        )
    }

    private func overviewActiveSessionDetail(
        _ session: ActiveSessionPresenter.SessionPresentation
    ) -> String {
        radarActiveUsagePresentation.sessionDetails[session.id]
            ?? session.sourceDisplayName
    }

    private var currentModelDetectionStatus: String {
        currentModelPresentation.detectionStatus
    }

    private var currentModelModeLabel: String {
        currentModelPresentation.modeLabel
    }

    private var isCurrentModelAutomaticMode: Bool {
        currentModelPresentation.isAutomatic
    }

    private var configuredCurrentCandidateID: String? {
        currentModelPresentation.configuredCandidateID
    }

    private var detectedCurrentModelIdentity: String? {
        let recommendation = store.snapshot?.config.recommendation
        guard let model = recommendation?.detectedCurrentModel?
            .trimmingCharacters(in: .whitespacesAndNewlines),
            !model.isEmpty else {
            return nil
        }
        return ModelIdentityPresentation.displayLabel(
            model: model,
            effort: recommendation?.detectedCurrentEffort ?? ""
        )
    }

    private var isUnmappedDetectedCurrentModel: Bool {
        currentModelPresentation.isUnmapped
    }

    private var currentModelPresentation: OperationalStatePresenter.CurrentModelPresentation {
        let recommendation = store.snapshot?.config.recommendation
        var candidateLabels: [String: String] = [:]
        for option in currentInUseCandidateOptions where candidateLabels[option.id] == nil {
            candidateLabels[option.id] = option.currentModelLabel
        }
        for entry in store.leaderboard where candidateLabels[entry.candidateId] == nil {
            candidateLabels[entry.candidateId] = entry.label
        }
        return OperationalStatePresenter.currentModel(
            OperationalStatePresenter.CurrentModelInput(
                hasRecommendation: recommendation != nil,
                mode: recommendation?.currentModelMode,
                detectionStatus: recommendation?.currentModelDetectionStatus,
                effectiveCandidateID: recommendation?.effectiveCurrentCandidateId,
                defaultCandidateID: recommendation?.currentDefaultCandidateId,
                fallbackCandidateID: store.snapshot?.dashboard.bestCombination?
                    .currentDefaultCandidateId,
                detectedIdentity: detectedCurrentModelIdentity,
                detectedActiveSessionCount: recommendation?.detectedActiveSessionCount ?? 0,
                candidateLabels: candidateLabels,
                requiresModelSetup: requiresModelSetup
            )
        )
    }

    private var currentInUseCandidateOptions: [OperationalStatePresenter.IngressCandidatePresentation] {
        ingressPresentation.currentCandidates
    }

    private var currentInUsePicker: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 5) {
                    Text("指定当前使用的模型")
                        .font(Typography.pageTitle)
                        .foregroundStyle(IslandVisual.primaryText)
                    Text("默认根据活动终端会话自动识别。也可手动指定；这里只改变推荐所依据的当前模型，不会修改任何终端配置。")
                        .font(Typography.settingsCardBody)
                        .foregroundStyle(IslandVisual.secondaryText)
                }
                Spacer(minLength: 12)
                Button {
                    showsCurrentInUsePicker = false
                } label: {
                    Image(systemName: "xmark")
                        .font(Typography.rowTitle)
                        .foregroundStyle(IslandVisual.secondaryText)
                        .frame(width: 32, height: 32)
                }
                .buttonStyle(.plain)
                .islandPointerOnHover()
            }
            .padding(20)

            Rectangle()
                .fill(IslandVisual.hairline)
                .frame(height: 0.5)

            ScrollViewReader { proxy in
                ScrollView(.vertical, showsIndicators: false) {
                    VStack(spacing: 0) {
                        Color.clear
                            .frame(height: 0)
                            .id("current-in-use-picker-top")
                        Button {
                            settings.useAutomaticCurrentModel()
                            showsCurrentInUsePicker = false
                        } label: {
                            HStack(spacing: 12) {
                                Image(systemName: isCurrentModelAutomaticMode ? "checkmark.circle.fill" : "circle")
                                    .foregroundStyle(
                                        isCurrentModelAutomaticMode
                                            ? IslandColor.interaction
                                            : IslandVisual.tertiaryText
                                    )
                                VStack(alignment: .leading, spacing: 3) {
                                    Text("自动识别")
                                        .font(Typography.rowTitle)
                                        .foregroundStyle(IslandVisual.primaryText)
                                    Text(currentModelAutomaticDescription)
                                    .font(Typography.micro)
                                    .foregroundStyle(IslandVisual.tertiaryText)
                                }
                                Spacer(minLength: 8)
                            }
                            .padding(.horizontal, 20)
                            .padding(.vertical, 11)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .islandPointerOnHover()
                        .overlay(alignment: .bottom) {
                            Rectangle().fill(IslandVisual.hairline).frame(height: 0.5)
                        }
                        if currentInUseCandidateOptions.isEmpty {
                            Text("暂无已启用的扫描档位")
                                .font(Typography.label)
                                .foregroundStyle(IslandVisual.tertiaryText)
                                .frame(maxWidth: .infinity, alignment: .center)
                                .padding(.vertical, 36)
                        } else {
                            ForEach(currentInUseCandidateOptions) { option in
                                currentInUseCandidateRow(option)
                            }
                        }
                    }
                }
                .onAppear {
                    proxy.scrollTo("current-in-use-picker-top", anchor: .top)
                }
            }

            if !isCurrentModelAutomaticMode {
                Rectangle()
                    .fill(IslandVisual.hairline)
                    .frame(height: 0.5)

                HStack {
                    Button("恢复自动识别") {
                        settings.useAutomaticCurrentModel()
                        showsCurrentInUsePicker = false
                    }
                    .buttonStyle(IslandActionButtonStyle(.secondary))
                    Spacer()
                }
                .padding(16)
            }
        }
        .background(
            RoundedRectangle(cornerRadius: IslandRadius.modal)
                .fill(IslandVisual.panelBackground(reduceTransparency: reduceTransparency))
                .overlay(
                    RoundedRectangle(cornerRadius: IslandRadius.modal)
                        .strokeBorder(
                            IslandVisual.border(increasedContrast: colorSchemeContrast == .increased),
                            lineWidth: colorSchemeContrast == .increased ? 1 : 0.5
                        )
                )
        )
        .onExitCommand {
            showsCurrentInUsePicker = false
        }
    }

    private var currentModelAutomaticDescription: String {
        currentModelPresentation.automaticDescription
    }

    private func currentInUseCandidateRow(
        _ option: OperationalStatePresenter.IngressCandidatePresentation
    ) -> some View {
        let isSelected = !isCurrentModelAutomaticMode && configuredCurrentCandidateID == option.id
        return Button {
            settings.setCurrentDefault(candidateID: option.id)
            showsCurrentInUsePicker = false
        } label: {
            HStack(spacing: 12) {
                Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(isSelected ? IslandColor.interaction : IslandVisual.tertiaryText)
                VStack(alignment: .leading, spacing: 3) {
                    Text(option.currentModelLabel)
                        .font(Typography.rowTitle)
                        .foregroundStyle(IslandVisual.primaryText)
                    Text(option.currentModelDetail)
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.tertiaryText)
                }
                Spacer(minLength: 8)
                if isSelected {
                    Text("手动指定")
                        .font(Typography.micro)
                        .foregroundStyle(IslandColor.interaction)
                }
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 11)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .islandPointerOnHover()
        .overlay(alignment: .bottom) {
            Rectangle().fill(IslandVisual.hairline).frame(height: 0.5)
        }
    }

    private var heroConfidenceChip: some View {
        Text(heroConfidenceLabel)
            .font(Typography.button)
            .foregroundStyle(bestConfidenceColor)
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(
                RoundedRectangle(cornerRadius: 6)
                    .fill(bestConfidenceColor.opacity(0.09))
                    .overlay(
                        RoundedRectangle(cornerRadius: 6)
                            .strokeBorder(bestConfidenceColor.opacity(0.18), lineWidth: 1)
                    )
            )
    }

    private var overviewRankingCard: some View {
        VStack(alignment: .leading, spacing: 0) {
            if showsRadarModelSetupCTA {
                radarModelSetupNotice
            }
            radarRankingHeader

            if comparisonDatasetSelection.showsLocalRepairControls {
                if let message = store.repairFailureMessage {
                    repairFailureNotice(message: message)
                } else if isBatchRepairRunning {
                    batchRepairNotice
                } else if let entry = repairNoticeEntry {
                    ViewThatFits(in: .horizontal) {
                        repairNotice(entry: entry)
                        compactRepairNotice(entry: entry)
                    }
                }
            }

            if store.radarLeaderboardItems.isEmpty {
                radarLeaderboardEmptyState
            } else {
                ScrollView(.vertical, showsIndicators: false) {
                    LazyVStack(alignment: .leading, spacing: 0) {
                        ForEach(store.radarLeaderboardItems) { entry in
                            let presentation = radarLeaderboardPresentation(for: entry)
                            let decisionTags = presentation.tags.compactMap(leaderboardExportTag)
                            RadarLeaderboardRow(
                                entry: entry,
                                rank: presentation.rank,
                                decisionTag: decisionTags.min { $0.priority < $1.priority },
                                onPresentEvidence: { presentEvidence(candidateID: entry.id) }
                            )
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .topLeading)
                    .padding(.bottom, 2)
                }
                .frame(maxHeight: .infinity, alignment: .top)
            }
        }
        .frame(
            maxWidth: .infinity,
            idealHeight: overviewRankingFillsAvailableHeight ? nil : overviewRankingPreferredHeight,
            maxHeight: overviewRankingFillsAvailableHeight ? .infinity : overviewRankingPreferredHeight,
            alignment: .topLeading
        )
    }

    private var overviewRankingFillsAvailableHeight: Bool {
        store.radarLeaderboardItems.isEmpty
    }

    private var hasValidOfficialRadarSnapshot: Bool {
        store.snapshot?.referenceSnapshotFeed.trustedLatest != nil
            && !store.radarLeaderboardItems.isEmpty
    }

    private var showsRadarModelSetupCTA: Bool {
        requiresModelSetup
    }

    private var radarModelSetupNotice: some View {
        HStack(spacing: 10) {
            Image(systemName: "info.circle")
                .font(Typography.button)
                .foregroundStyle(IslandColor.interaction)

            VStack(alignment: .leading, spacing: 2) {
                Text(L10n.tr(
                    hasValidOfficialRadarSnapshot
                        ? "官方 Radar 可直接浏览"
                        : "官方 Radar 尚未载入"
                ))
                    .font(Typography.rowTitle)
                    .foregroundStyle(IslandVisual.primaryText)
                    .lineLimit(1)
                Text(L10n.tr(
                    hasValidOfficialRadarSnapshot
                        ? "接入本地模型后，可继续进行本机实测与个性化推荐。"
                        : "可刷新官方榜单；本地模型接入与本机评测是可选项。"
                ))
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.secondaryText)
                    .lineLimit(2)
                Text(L10n.tr("不会读取凭据原文；项目内容与对话正文不持久化、不上传"))
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.tertiaryText)
                    .lineLimit(1)
            }

            Spacer(minLength: 8)

            Button(L10n.tr("接入模型"), action: openModelIngress)
                .buttonStyle(IslandActionButtonStyle(.secondary))
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .background(IslandColor.interaction.opacity(0.055))
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(IslandColor.interaction.opacity(0.14))
                .frame(height: 0.5)
        }
    }

    private func repairNotice(entry: DisplayEntry) -> some View {
        HStack(spacing: 10) {
            Image(systemName: entry.isRunning ? "arrow.triangle.2.circlepath" : "exclamationmark.circle.fill")
                .font(Typography.button)
                .foregroundStyle(IslandColor.alertAmber.opacity(0.92))

            VStack(alignment: .leading, spacing: 2) {
                Text(entry.isRunning
                    ? L10n.tr("正在重试 %@", entry.canonicalModelName)
                    : failedRepairNoticeTitle)
                    .font(Typography.rowTitle)
                    .foregroundStyle(IslandVisual.primaryText)
                    .lineLimit(1)
                Text(entry.isRunning
                    ? L10n.tr(entry.progressText)
                    : L10n.tr("%d 道失败题可并行重试", repairableQuestionCount))
                    .font(Typography.micro)
                    .foregroundStyle(IslandColor.alertAmber.opacity(0.78))
                    .lineLimit(1)
            }

            Spacer(minLength: 8)

            HStack(spacing: 8) {
                if canDismissRepairNotice {
                    Button {
                        store.dismissResumableRun()
                    } label: {
                        Text("先不重试")
                    }
                    .buttonStyle(IslandActionButtonStyle(.secondary))
                }

                Button {
                    retryFailedQuestions()
                } label: {
                    Text("重试失败题")
                }
                .buttonStyle(IslandActionButtonStyle(.secondary))
                .disabled(repairPresentation.noticeRetryIsDisabled)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(IslandColor.alertAmber.opacity(0.055))
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(IslandColor.alertAmber.opacity(0.14))
                .frame(height: 0.5)
        }
    }

    private func compactRepairNotice(entry: DisplayEntry) -> some View {
        HStack(spacing: 10) {
            Image(systemName: entry.isRunning ? "arrow.triangle.2.circlepath" : "exclamationmark.circle.fill")
                .font(Typography.button)
                .foregroundStyle(IslandColor.alertAmber.opacity(0.92))

            Text(entry.isRunning
                ? L10n.tr("正在重试 %@", entry.canonicalModelName)
                : failedRepairNoticeTitle)
                .font(Typography.rowTitle)
                .foregroundStyle(IslandVisual.primaryText)
                .lineLimit(1)
                .truncationMode(.tail)

            Spacer(minLength: 8)

            if canDismissRepairNotice {
                Button("先不重试") {
                    store.dismissResumableRun()
                }
                .buttonStyle(IslandActionButtonStyle(.secondary))
            }

            Button("重试") {
                retryFailedQuestions()
            }
            .buttonStyle(IslandActionButtonStyle(.secondary))
            .disabled(repairPresentation.noticeRetryIsDisabled)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(IslandColor.alertAmber.opacity(0.055))
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(IslandColor.alertAmber.opacity(0.14))
                .frame(height: 0.5)
        }
    }

    private var batchRepairNotice: some View {
        HStack(spacing: 10) {
            Image(systemName: "arrow.triangle.2.circlepath")
                .font(Typography.button)
                .foregroundStyle(IslandColor.alertAmber.opacity(0.92))

            VStack(alignment: .leading, spacing: 2) {
                Text(repairPresentation.batchTitle)
                    .font(Typography.rowTitle)
                    .foregroundStyle(IslandVisual.primaryText)
                    .lineLimit(1)
                Text(batchRepairStatusText)
                    .font(Typography.micro)
                    .foregroundStyle(IslandColor.alertAmber.opacity(0.78))
                    .lineLimit(1)
            }

            Spacer(minLength: 8)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(IslandColor.alertAmber.opacity(0.055))
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(IslandColor.alertAmber.opacity(0.14))
                .frame(height: 0.5)
        }
    }

    private var repairPresentation: OperationalStatePresenter.RepairPresentation {
        let runtime = store.snapshot?.runtime
        return OperationalStatePresenter.repair(
            OperationalStatePresenter.RepairInput(
                showsLocalRepairControls: comparisonDatasetSelection.showsLocalRepairControls,
                runtimeIsRunning: runtime?.isRunning == true,
                hasResumableRun: runtime?.hasResumableRun == true,
                lifecycleState: runtime?.lifecycleState.rawValue ?? "idle",
                isScanOperationActive: store.isScanOperationActive,
                pendingControlAction: store.pendingScanControlAction,
                currentPhase: runtime?.currentPhase?.rawValue,
                currentTarget: runtime?.currentTarget,
                activeEvaluationCount: runtime?.activeEvaluationCount ?? 0,
                queuedEvaluationCount: runtime?.queuedEvaluationCount ?? 0,
                runID: store.snapshot?.dashboard.runMetadata.runId,
                configuredCandidateIDs: store.snapshot?.settingsProjection.scanScope
                    .regularCandidateIds ?? [],
                runCandidateIDs: store.snapshot?.dashboard.runMetadata
                    .requestedCandidateIds ?? [],
                entries: detailEntries.map {
                    OperationalStatePresenter.RepairEntryInput(
                        id: $0.id,
                        displayName: $0.canonicalModelName,
                        isRunning: $0.isRunning,
                        progressText: $0.progressText,
                        isCurrentRunEligible: $0.isCurrentRunEligible,
                        repairableQuestionIDs: $0.repairableQuestionIds,
                        canDisplayCurrentQuestionScores: $0.evidenceAvailability
                            .canDisplayCurrentQuestionScores,
                        questionStatuses: $0.questionResults.map(\.status)
                    )
                }
            )
        )
    }

    private var isBatchRepairRunning: Bool {
        repairPresentation.isBatchRunning
    }

    private var batchRepairStatusText: String {
        repairPresentation.batchStatusText
    }

    private func repairFailureNotice(message: String) -> some View {
        HStack(spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(Typography.button)
                .foregroundStyle(IslandColor.alertRed.opacity(0.92))

            Text(L10n.tr(message))
                .font(Typography.label)
                .foregroundStyle(IslandColor.alertRed.opacity(0.88))
                .lineLimit(2)

            Spacer(minLength: 8)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .background(IslandColor.alertRed.opacity(0.055))
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(IslandColor.alertRed.opacity(0.14))
                .frame(height: 0.5)
        }
    }

    private func retryFailedQuestions() {
        guard let runID = repairPresentation.runID else { return }
        store.startFailedRepair(
            runID: runID,
            candidateIDs: repairPresentation.repairableCandidateIDs
        )
    }

    private var overviewRankingPreferredHeight: CGFloat {
        let modelSetupNoticeHeight: CGFloat = showsRadarModelSetupCTA ? 74 : 0
        let repairNoticeHeight: CGFloat = (
            store.repairFailureMessage == nil
                && (isBatchRepairRunning || repairNoticeEntry != nil)
        ) ? 58 : 0
        let repairFailureHeight: CGFloat = store.repairFailureMessage == nil ? 0 : 42
        let rowCount = store.radarLeaderboardItems.count
        return min(
            520,
            max(
                188,
                34
                    + modelSetupNoticeHeight
                    + repairNoticeHeight
                    + repairFailureHeight
                    + CGFloat(rowCount) * RadarRankingLayout.rowHeight
            )
        )
    }

    private var radarRankingHeader: some View {
        HStack(spacing: RadarRankingLayout.columnSpacing) {
            Text("模型")
                .font(Typography.rankingHeader)
                .foregroundStyle(IslandVisual.secondaryText)
                .padding(.leading, RadarRankingLayout.modelHeaderLeading)
                .frame(maxWidth: .infinity, alignment: .leading)

            Text("总分")
                .frame(width: RadarRankingLayout.scoreWidth, alignment: .trailing)

            Text("总耗时")
                .frame(width: RadarRankingLayout.durationWidth, alignment: .trailing)

            Text("参考费用")
                .frame(width: RadarRankingLayout.costWidth, alignment: .trailing)
        }
        .font(Typography.rankingHeader)
        .foregroundStyle(IslandVisual.secondaryText)
        .padding(.leading, RadarRankingLayout.outerLeading)
        .padding(.trailing, RadarRankingLayout.outerTrailing)
        .padding(.top, 8)
        .padding(.bottom, 8)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(IslandVisual.hairline)
                .frame(height: 0.5)
        }
    }

    private var radarLeaderboardEmptyState: some View {
        VStack(spacing: 8) {
            Image(systemName: "chart.bar.xaxis")
                .font(Typography.icon)
                .foregroundStyle(IslandVisual.tertiaryText)
            Text(radarSurfacePresentation.emptyTitle)
                .font(Typography.rowTitle)
                .foregroundStyle(IslandVisual.primaryText)
            Text(radarSurfacePresentation.emptyReason)
                .font(Typography.settingsCardBody)
                .foregroundStyle(IslandVisual.secondaryText)
                .multilineTextAlignment(.center)
                .lineLimit(3)
                .fixedSize(horizontal: false, vertical: true)
                .layoutPriority(1)
                .frame(maxWidth: 360)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .center)
        .padding(24)
    }

    private var detailPage: some View {
        ComparisonPage(
            items: store.radarLeaderboardItems,
            representativeDecision: store.radarRepresentativeDecision,
            decisions: store.radarPortfolio?.decisions ?? [],
            recommendationUse: store.snapshot?.recommendationUse,
            statistics: localComparisonStatistics,
            leaderboard: localComparisonLeaderboard,
            pairwiseComparisons: localComparisonPairwiseComparisons,
            referenceSnapshot: selectedReferenceSnapshot,
            advisorEvidence: store.radarEvidence,
            workload: store.snapshot?.codexInsights?.workload,
            displaySource: store.radarDisplaySource,
            questionPackVersion: comparisonQuestionPackVersion,
            graderVersion: comparisonGraderVersion,
            evaluationSnapshotID: comparisonEvaluationSnapshotID,
            pricingSnapshotID: comparisonPricingSnapshotID,
            itemIDByConfigurationID: comparisonItemIDByConfigurationID,
            sourceModeByConfigurationID: store.radarPortfolio?.sourceModeByConfigurationId ?? [:],
            questionSemantics: questionSemantics
        )
    }

    private var comparisonItemIDByConfigurationID: [String: String] {
        let decisions = (store.radarPortfolio?.decisions ?? [])
            + [store.radarRepresentativeDecision].compactMap { $0 }
        let configurationIDs = decisions.flatMap { decision in
            [
                decision.currentModelConfigurationId,
                decision.candidateModelConfigurationId,
                decision.comparisonCandidateModelConfigurationId,
            ].compactMap { $0 }
        }
        return Dictionary(
            configurationIDs.compactMap { configurationID in
                store.radarLeaderboardItem(for: configurationID).map {
                    (configurationID, $0.id)
                }
            },
            uniquingKeysWith: { first, _ in first }
        )
    }

    private var localComparisonStatistics: BridgeStatistics? {
        comparisonDatasetSelection.statistics
    }

    private var localComparisonLeaderboard: [BridgeLeaderboardEntry] {
        comparisonDatasetSelection.leaderboard
    }

    private var localComparisonPairwiseComparisons: [BridgePairwiseComparison] {
        comparisonDatasetSelection.pairwiseComparisons
    }

    private var selectedReferenceSnapshot: BridgeReferenceSnapshot? {
        comparisonDatasetSelection.referenceSnapshot
    }

    private var comparisonDatasetSelection: ComparisonSelectionPresenter.DatasetSelection {
        ComparisonSelectionPresenter.dataset(
            usesLocalDataset: comparisonRoutingPresentation.usesLocalDataset,
            usesOfficialSnapshot: comparisonRoutingPresentation.usesOfficialSnapshot,
            localStatistics: store.radarDashboard?.statistics,
            localLeaderboard: store.radarDashboard?.leaderboard ?? [],
            localPairwiseComparisons: store.radarDashboard?.pairwiseComparisons ?? [],
            officialSnapshot: store.snapshot?.referenceSnapshotFeed.trustedLatest
        )
    }

    private var comparisonQuestionPackVersion: String? {
        comparisonRoutingPresentation.questionPackVersion
    }

    private var comparisonGraderVersion: String? {
        comparisonRoutingPresentation.graderVersion
    }

    private var comparisonEvaluationSnapshotID: String? {
        comparisonRoutingPresentation.evaluationSnapshotID
    }

    private var comparisonPricingSnapshotID: String? {
        comparisonRoutingPresentation.pricingSnapshotID
    }

    private var comparisonRoutingPresentation: ConfigurationEvidencePresenter.RoutingPresentation {
        let latest = store.snapshot?.referenceSnapshotFeed.trustedLatest
        return ConfigurationEvidencePresenter.routing(
            ConfigurationEvidencePresenter.RoutingInput(
                displaySource: store.radarDisplaySource,
                officialSnapshotIsTrusted: latest?.isPublicOfficialSnapshot == true,
                officialQuestionPackVersion: latest?.questionPackVersion,
                officialGraderVersion: latest?.graderVersion,
                officialSnapshotID: latest?.batchId,
                officialPricingSnapshotID: latest?.pricingSnapshotId,
                localQuestionPackVersion: store.snapshot?.questionPack.version,
                localGraderResults: store.radarEvidence?.resolvedResultRows.map {
                    ConfigurationEvidencePresenter.GraderResultInput(
                        questionPackVersion: $0.questionPackVersion,
                        graderVersion: $0.graderVersion
                    )
                } ?? [],
                localSnapshotID: store.radarEvidence?.sourceSnapshotId,
                recommendationPricingSnapshotID: store.radarEvidence?.pricingSnapshotId
                    ?? store.snapshot?.recommendationUse.representativeEpoch?.pricingSnapshotId,
                diagnosticPricingSnapshotID: store.snapshot?.diagnostics?.versions
                    .pricingSnapshotId
            )
        )
    }

    private var panelFooter: some View {
        VStack(spacing: 0) {
            Rectangle()
                .fill(IslandVisual.workspaceBorder)
                .frame(height: 0.5)

            ZStack {
                HStack(spacing: 12) {
                    if let footerStatus = footerDataStatusText {
                        HStack(spacing: 6) {
                            Text(footerStatus)
                                .font(Typography.micro)
                                .foregroundStyle(footerStatusColor)
                                .lineLimit(1)

                            if !comparisonDatasetSelection.showsLocalRepairControls {
                                radarReferenceRefreshControl
                            }
                        }
                            .frame(maxWidth: 300, alignment: .leading)
                    }

                    Spacer(minLength: 120)

                    footerControls
                }

                footerPageTabs
            }
            .frame(height: 28, alignment: .center)
            .padding(.horizontal, expandedContentHorizontalInset)
            .padding(.top, 6)
            .padding(.bottom, LayoutRhythm.standard)
        }
    }

    private var footerControls: some View {
        HStack(spacing: 8) {
            if showRestartButton {
                RestartScanButton()
            }
            if canRetryFailedQuestions {
                Button(action: retryFailedQuestions) {
                    Text(L10n.tr("重试全部失败 %d", repairableQuestionCount))
                }
                .buttonStyle(IslandActionButtonStyle(.secondary))
            } else if canRetryTimedOutQuestions {
                Button(action: retryTimedOutQuestions) {
                    Text(L10n.tr("重试全部超时 %d", timedOutQuestionCount))
                }
                .buttonStyle(IslandActionButtonStyle(.secondary))
            }
            if showsEvaluationProfileSelector {
                evaluationProfileSelector
            }
            if canUpgradeEvaluationProfile {
                Button(action: performEvaluationProfileUpgradeAction) {
                    Text(upgradeEvaluationProfileActionTitle)
                }
                .buttonStyle(IslandActionButtonStyle(.primary))
                .disabled(isScanControlPending || settings.isSaving)
                .disabled(store.isScanOperationActive)
            } else if store.pendingScanControlAction != "stop" {
                Button(action: performScanControlAction) {
                    Text(scanControlActionTitle)
                }
                .buttonStyle(IslandActionButtonStyle(.primary))
                .disabled(
                    isScanControlPending
                        || (store.isScanOperationActive && !store.canRequestScanPause)
                )
            }
            if store.canRequestScanStop
                || store.snapshot?.runtime.isRunning == true
                || store.pendingScanControlAction == "stop" {
                Button(stopScanActionTitle) {
                    pendingScanConfirmation = .stop
                }
                .buttonStyle(IslandActionButtonStyle(.danger))
                .disabled(isScanControlPending)
            }
        }
        .fixedSize(horizontal: true, vertical: false)
    }

    private var footerPageTabs: some View {
        HStack(spacing: 20) {
            footerPageTab(title: L10n.Overview.radarTab, index: 0)
            footerPageTab(title: L10n.Overview.comparisonTab, index: 1)
        }
    }

    private func footerPageTab(title: String, index: Int) -> some View {
        Button {
            withAnimation(reduceMotion ? nil : .controlSelection) {
                pageIndex = index
            }
        } label: {
            Text(title)
                .font(Typography.tabLabel)
                .foregroundStyle(
                    pageIndex == index
                        ? IslandColor.interaction
                        : IslandVisual.tertiaryText
                )
                .padding(.horizontal, 2)
                .padding(.vertical, 8)
                .overlay(alignment: .bottom) {
                    if pageIndex == index {
                        RoundedRectangle(cornerRadius: 1)
                            .fill(IslandColor.interaction)
                            .frame(height: 2)
                            .matchedGeometryEffect(
                                id: "footer-page-indicator",
                                in: pageSelectionNamespace
                            )
                    }
                }
                .contentShape(Rectangle().inset(by: -6))
        }
        .buttonStyle(.plain)
        .accessibilityValue(pageIndex == index ? L10n.tr("已选择") : L10n.tr("未选择"))
    }

    private var modelIngressConfig: BridgeModelIngress? {
        store.snapshot?.config.modelIngress
    }

    private var isModelIngressLoaded: Bool {
        store.snapshot != nil
    }

    private var ingressPresentation: OperationalStatePresenter.IngressPresentation {
        let ingress = modelIngressConfig
        return OperationalStatePresenter.ingress(
            OperationalStatePresenter.IngressInput(
                isLoaded: isModelIngressLoaded,
                sources: ingress?.sources.map {
                    OperationalStatePresenter.IngressSourceInput(
                        id: $0.id,
                        title: $0.title,
                        mode: $0.mode,
                        isEnabled: $0.enabled
                    )
                } ?? [],
                connections: ingress?.connections.map { connection in
                    OperationalStatePresenter.IngressConnectionInput(
                        id: connection.id,
                        sourceID: connection.sourceId,
                        name: connection.name,
                        isEnabled: connection.enabled,
                        hasAPIFormat: connection.apiFormat != nil,
                        candidates: connection.modelCandidates.map {
                            OperationalStatePresenter.IngressCandidateInput(
                                id: $0.id,
                                modelID: $0.modelId,
                                familyID: $0.familyId,
                                variantID: $0.variantId,
                                scanProfile: $0.scanProfile,
                                isEnabled: $0.enabled
                            )
                        }
                    )
                } ?? [],
                enabledCandidateCount: store.snapshot?.settingsProjection.scanScope
                    .candidateCount ?? 0,
                runtimeIsRunning: store.snapshot?.runtime.isRunning == true,
                hasResumableRun: store.snapshot?.runtime.hasResumableRun == true
            )
        )
    }

    private var requiresModelSetup: Bool {
        ingressPresentation.requiresModelSetup
    }

    private var hasConfiguredModelCandidates: Bool {
        ingressPresentation.hasConfiguredCandidates
    }

    private var modelSetupHeaderText: String {
        ingressPresentation.setupHeaderText
    }

    private var detailModelCountText: String {
        overviewModelCountText
    }

    private var detailEntries: [DisplayEntry] {
        let bestCandidateID = store.snapshot?.dashboard.bestCombination?.candidateId
            ?? store.snapshot?.dashboard.provisionalLeader?.candidateId
        return RadarEntryPresenter.entries(
            leaderboard: store.leaderboard,
            runEntries: store.runEntries,
            bestCandidateID: bestCandidateID,
            currentDefaultCandidateID: store.snapshot?.dashboard.bestCombination?
                .currentDefaultCandidateId,
            currentPhase: store.snapshot?.runtime.currentPhase,
            questionSemantics: questionSemantics
        )
    }

    private var exportableLeaderboardEntries: [RadarLeaderboardItem] {
        store.radarLeaderboardItems.filter { $0.score != nil }
    }

    private var leaderboardExportOmittedCount: Int {
        let sourceCount: Int
        switch store.radarDisplaySource {
        case "official_snapshot":
            sourceCount = selectedReferenceSnapshot?.entries.count ?? 0
        case "local_evaluation":
            sourceCount = store.radarDashboard?.leaderboard.count ?? 0
        default:
            sourceCount = store.radarLeaderboardItems.count
        }
        return max(0, sourceCount - exportableLeaderboardEntries.count)
    }

    private var canExportLeaderboard: Bool {
        !isEvidenceUpdating && !exportableLeaderboardEntries.isEmpty
    }

    private func exportLeaderboardImage() {
        guard canExportLeaderboard else { return }

        let projectedEntries = exportableLeaderboardEntries.map { entry in
            (entry: entry, presentation: radarLeaderboardPresentation(for: entry))
        }
        let rankCounts = Dictionary(
            grouping: projectedEntries.compactMap { $0.presentation.rank },
            by: { $0 }
        ).mapValues { $0.count }
        let allRows: [LeaderboardExportRow] = projectedEntries.compactMap { projected in
            let entry = projected.entry
            guard let score = entry.score else { return nil }
            let exportTags = projected.presentation.tags.compactMap(leaderboardExportTag)
            let exportSemantics = RadarPresenter.leaderboardExportSemantics(
                decisionTagKinds: exportTags.map(\.kind)
            )
            return LeaderboardExportRow(
                id: entry.id,
                providerID: entry.providerId,
                modelLabel: entry.displayName,
                canonicalRank: projected.presentation.rank,
                isTiedRank: projected.presentation.rank.map {
                    rankCounts[$0, default: 0] > 1
                } ?? false,
                isRecommended: exportSemantics.isRecommended,
                score: Int(score.rounded()),
                elapsedSeconds: entry.elapsedSeconds,
                referenceCostUsd: entry.referenceCostUsd,
                decisionTags: exportTags
            )
        }
        let content = LeaderboardExportContent(
            resultsUpdatedAt: leaderboardResultsUpdatedAt,
            exportedAt: Date(),
            language: LeaderboardExportLanguage.currentAppDefault,
            totalValidResultCount: allRows.count,
            rows: Array(allRows.prefix(15))
        )

        presentLeaderboardExport(
            content,
            omittedCount: leaderboardExportOmittedCount
        )
    }

    private func leaderboardExportTag(_ label: String) -> LeaderboardExportTag? {
        switch label {
        case "推荐", "Recommended":
            return LeaderboardExportTag(kind: "recommended", label: L10n.tr("推荐"))
        case "性价比", "Best value":
            return LeaderboardExportTag(kind: "value", label: L10n.tr("性价比"))
        case "速度优选", "Speed pick":
            return LeaderboardExportTag(kind: "speed", label: L10n.tr("速度优选"))
        case "轻量优选", "Lightweight":
            return LeaderboardExportTag(kind: "lightweight", label: L10n.tr("轻量优选"))
        default:
            return nil
        }
    }

    private func presentLeaderboardExport(
        _ content: LeaderboardExportContent,
        omittedCount: Int
    ) {
        LeaderboardImageExporter.presentExportFlow(
            content: content,
            omittedCount: omittedCount,
            onSuccess: { url in
                exportedLeaderboardURL = url
            },
            onFailure: { message in
                exportErrorMessage = message
            }
        )
    }

    private var leaderboardResultsUpdatedAt: Date? {
        parseLeaderboardTimestamp(store.radarResultsUpdatedAt)
    }

    private func parseLeaderboardTimestamp(_ value: String?) -> Date? {
        guard let value, !value.isEmpty else { return nil }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: value) {
            return date
        }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: value)
    }

    private var expandedOperationalState: GlanceState {
        store.glancePresentation.state
    }

    private var expandedRunStatus: String {
        store.snapshot?.dashboard.runMetadata.status ?? "legacy"
    }

    private var operationalAvailability: OperationalStatePresenter.Availability {
        OperationalStatePresenter.availability(
            OperationalStatePresenter.AvailabilityInput(
                state: expandedOperationalState,
                hasEntries: !detailEntries.isEmpty,
                canDisplayCurrentQuestionScores: detailEntries.map {
                    $0.evidenceAvailability.canDisplayCurrentQuestionScores
                },
                hasResumableRun: store.snapshot?.runtime.hasResumableRun == true,
                requiresModelSetup: requiresModelSetup,
                isProvisionalResult: isProvisionalResult,
                hasBestCombination: store.snapshot?.dashboard.bestCombination != nil,
                bestEvidenceState: store.snapshot?.dashboard.bestCombination?.evidenceState,
                runStatus: expandedRunStatus,
                advisor: nil
            )
        )
    }

    private var expandedOperationalTone: GlanceTone {
        operationalPresentation.operationalTone
    }

    private var expandedOperationalColor: Color {
        switch expandedOperationalTone {
        case .neutral:
            return IslandVisual.tertiaryText
        case .active:
            return IslandColor.interaction
        case .success:
            return IslandColor.liveTeal
        case .warning:
            return IslandColor.alertAmber
        case .failure:
            return IslandColor.alertRed
        }
    }

    private var isEvidenceUpdating: Bool {
        operationalAvailability.isEvidenceUpdating
    }

    private var radarSourcePresentation: RadarPresenter.SourceLabels {
        RadarPresenter.sourceLabels(
            selectedSourceMode: store.radarSelectedSourceMode,
            displaySource: store.radarDisplaySource
        )
    }

    private var radarPreferencePresentation: String {
        RadarPresenter.preferenceLabel(
            store.radarPortfolio?.preference
                ?? store.snapshot?.config.recommendation.preference
        )
    }

    private func setRadarSourceMode(_ sourceMode: String) {
        guard let configurationID = store.radarRepresentativeConfigurationID else { return }
        settings.setSourceMode(sourceMode, configurationID: configurationID)
    }

    private var radarSurfacePresentation: RadarPresenter.SurfacePresentation {
        RadarPresenter.surface(
            displaySource: store.radarDisplaySource,
            selectedSourceMode: store.radarSelectedSourceMode,
            portfolioStatus: store.radarPortfolio?.status,
            evidenceUpdating: isEvidenceUpdating,
            hasStableDashboard: store.snapshot?.stableEvidenceDashboard != nil
                || store.snapshot?.stableDashboard != nil,
            completeQuestionSetLabel: completeQuestionSetLabel,
            officialQuestionPackVersion: store.snapshot?.referenceSnapshotFeed
                .trustedLatest?.questionPackVersion,
            localQuestionPackVersion: store.snapshot?.questionPack.version
                ?? store.radarDashboard?.runMetadata.questionPackVersion,
            requiresModelSetup: requiresModelSetup
        )
    }

    private var radarCurrentModelLabel: String {
        if isUnmappedDetectedCurrentModel, let identity = detectedCurrentModelIdentity {
            return identity
        }
        return store.radarDisplayName(for: store.radarRepresentativeConfigurationID)
            ?? L10n.tr("尚无使用记录")
    }

    private var radarCurrentLeaderboardItem: RadarLeaderboardItem? {
        guard let configurationID = store.radarRepresentativeConfigurationID else {
            return nil
        }
        return store.radarLeaderboardItem(for: configurationID)
    }

    private var radarCurrentEffortLabel: String? {
        guard let effort = radarCurrentLeaderboardItem?.effort else { return nil }
        return ModelIdentityPresentation.effortTag(for: effort)
    }

    private var radarCurrentModelName: String {
        guard let model = radarCurrentLeaderboardItem?.modelName else {
            return radarCurrentModelLabel
        }
        return ModelIdentityPresentation.canonicalName(for: model)
    }

    private var radarDecisionPresentation: RadarPresenter.DecisionPresentation {
        let decision = store.radarRepresentativeDecision
        let candidateID = RadarPresenter.candidateConfigurationID(for: decision)
        return RadarPresenter.decision(
            evidenceUpdating: isEvidenceUpdating,
            hasSnapshotRefreshIssue: store.snapshotRefreshIssue != nil,
            hasResumableRun: store.snapshot?.runtime.hasResumableRun == true,
            isUnmappedCurrentModel: isUnmappedDetectedCurrentModel,
            detectedCurrentModelIdentity: detectedCurrentModelIdentity,
            selectedSourceMode: store.radarSelectedSourceMode,
            displaySource: store.radarDisplaySource,
            portfolio: store.radarPortfolio,
            decision: decision,
            candidateLabel: store.radarDisplayName(for: candidateID),
            candidateCostCoverage: candidateID.flatMap {
                store.radarLeaderboardItem(for: $0)?.costCoverage
            }
        )
    }

    private var radarCandidateModelColor: Color {
        radarDecisionColor(
            RadarPresenter.decisionEmphasis(store.radarRepresentativeDecision?.decision),
            neutral: IslandColor.interaction
        )
    }

    private func radarDecisionColor(
        _ emphasis: RadarPresenter.DecisionEmphasis,
        neutral: Color
    ) -> Color {
        switch emphasis {
        case .recommended: return IslandColor.liveTeal
        case .neutral: return neutral
        }
    }

    private var radarOfficialCanonicalRows: [RadarPresenter.CanonicalLeaderboardRow] {
        selectedReferenceSnapshot?.leaderboardProjection?.rows.map { row in
            RadarPresenter.CanonicalLeaderboardRow(
                configurationID: row.modelConfigurationId,
                alternateConfigurationID: nil,
                rank: row.rank,
                targetLabels: row.targetLabels.map(\.label),
                decisionTagKinds: row.decisionTags.map(\.kind)
            )
        } ?? []
    }

    private var radarLocalCanonicalRows: [RadarPresenter.CanonicalLeaderboardRow] {
        localComparisonLeaderboard.map { row in
            RadarPresenter.CanonicalLeaderboardRow(
                configurationID: row.candidateId,
                alternateConfigurationID: row.id,
                rank: store.radarDisplayRank(for: row.candidateId) ?? row.canonicalRank,
                targetLabels: row.canonicalLabels,
                decisionTagKinds: []
            )
        }
    }

    private func radarLeaderboardPresentation(
        for entry: RadarLeaderboardItem
    ) -> RadarPresenter.LeaderboardRowPresentation {
        RadarPresenter.leaderboardRow(
            item: RadarPresenter.LeaderboardItemInput(
                configurationID: entry.id,
                isCurrent: entry.isCurrent,
                isRecommended: entry.isRecommended
            ),
            displaySource: store.radarDisplaySource,
            portfolioStatus: store.radarPortfolio?.status,
            officialRows: radarOfficialCanonicalRows,
            localRows: radarLocalCanonicalRows
        )
    }

    private func shortTimestamp(_ value: String) -> String {
        String(value.prefix(16)).replacingOccurrences(of: "T", with: " ")
    }

    private var operationalBestInput: OperationalStatePresenter.BestInput? {
        store.snapshot?.dashboard.bestCombination.map { best in
            let modelName = ModelIdentityPresentation.canonicalName(for: best.model)
            let effort = ModelIdentityPresentation.effortTag(for: best.effort)
                .map { " \($0)" } ?? ""
            return OperationalStatePresenter.BestInput(
                displayLabel: "\(modelName)\(effort)",
                evidenceState: best.evidenceState,
                recommendationOutcome: best.recommendationOutcome,
                decisionReason: best.decisionReason,
                overallScore: best.overallScore,
                overallScoreText: best.overallScoreText,
                scoreText: best.scoreText,
                confidenceLabel: best.confidenceLabel
            )
        }
    }

    private var operationalProvisionalInput: OperationalStatePresenter.ProvisionalInput? {
        guard isProvisionalResult,
              let provisional = store.snapshot?.dashboard.provisionalLeader else {
            return nil
        }
        let displayLabel = provisional.candidateId.flatMap { candidateID in
            detailEntries.first { $0.id == candidateID }?.identityDisplayLabel
        }
        let metadata = store.snapshot?.dashboard.runMetadata
        return OperationalStatePresenter.ProvisionalInput(
            displayLabel: displayLabel,
            scoreText: provisional.modeScoreText,
            hasModeScore: provisional.modeScore != nil,
            confidenceLabel: provisional.confidenceLabel,
            confidenceReason: provisional.confidenceReason,
            statusLabel: provisional.statusLabel,
            evaluationProfileLabel: metadata?.evaluationProfileLabel,
            completedQuestionCount: metadata?.questionCount ?? 0,
            totalQuestionCount: store.snapshot?.questionPack.questionCount ?? 0
        )
    }

    private var operationalPresentation: OperationalStatePresenter.Presentation {
        let refreshIssue = store.snapshotRefreshIssue
        return OperationalStatePresenter.presentation(
            OperationalStatePresenter.PresentationInput(
                availability: operationalAvailability,
                state: expandedOperationalState,
                glanceTone: store.glancePresentation.tone,
                runStatus: expandedRunStatus,
                hasSnapshotRefreshIssue: refreshIssue != nil,
                snapshotRefreshMessage: refreshIssue?.message,
                snapshotRefreshDetail: refreshIssue?.detail,
                runtimeIsRunning: store.snapshot?.runtime.isRunning == true,
                runtimeLastError: store.snapshot?.runtime.lastError,
                runtimeProgressText: store.runtimeProgressText,
                activeEvaluationTimingText: store.activeEvaluationTimingText,
                hasResumableRun: store.snapshot?.runtime.hasResumableRun == true,
                entryDestination: entryDestination,
                glanceDestination: store.glancePresentation.destination,
                glancePeekLeftSecondary: store.glancePresentation.peekLeftSecondary,
                requiresModelSetup: requiresModelSetup,
                hasConfiguredModelCandidates: hasConfiguredModelCandidates,
                radarDisplaySource: store.radarDisplaySource,
                radarReferenceFreshness: store.radarReferenceFreshness,
                radarReferenceAgeHours: store.radarReferenceAgeHours,
                referenceDeliveryRefreshStatus: store.snapshot?.referenceSnapshotFeed
                    .delivery?.refreshStatus,
                referenceDeliverySource: store.snapshot?.referenceSnapshotFeed.delivery?.source,
                referencePublishedAt: selectedReferenceSnapshot?.publishedAt,
                localCompletedAt: store.radarDashboard?.runMetadata.completedAt,
                now: Date(),
                hasRadarPortfolio: store.radarPortfolio != nil,
                radarPortfolioStatus: store.radarPortfolio?.status,
                best: operationalBestInput,
                provisional: operationalProvisionalInput,
                advisor: nil,
                advisorDisplayLabel: nil,
                advisorOverallScore: nil,
                radarTitle: radarDecisionPresentation.title,
                radarReason: radarDecisionPresentation.reason,
                fallbackTitle: radarDecisionPresentation.titleOrFallback,
                fallbackReason: radarDecisionPresentation.reasonOrFallback,
                isUnmappedCurrentModel: isUnmappedDetectedCurrentModel,
                detectedCurrentModelIdentity: detectedCurrentModelIdentity,
                completeQuestionSetLabel: completeQuestionSetLabel,
                questionRoundLabel: questionRoundLabel
            )
        )
    }

    private var headerDetailText: String {
        operationalPresentation.headerDetailText
    }

    private var detailHeaderPrimaryText: String {
        guard let decision = store.radarRepresentativeDecision else {
            return L10n.tr("当前配置")
        }
        if decision.candidateModelConfigurationId != nil
            || decision.comparisonCandidateModelConfigurationId != nil {
            return L10n.tr("当前与候选")
        }
        return L10n.tr("当前配置")
    }

    private var overviewModelCountText: String {
        if requiresModelSetup {
            return modelSetupHeaderText
        }
        return L10n.tr("%lld 个已选档位", scanExecutionCandidateCount)
    }

    private var heroAccentColor: Color {
        switch operationalPresentation.heroAccent {
        case .interaction:
            return IslandColor.interaction
        case .warning:
            return IslandColor.alertAmber
        case .operational:
            return expandedOperationalColor
        }
    }

    private var bestConfidenceColor: Color {
        operationalTextColor(operationalPresentation.confidenceEmphasis)
    }

    private func operationalTextColor(
        _ emphasis: OperationalStatePresenter.TextEmphasis
    ) -> Color {
        switch emphasis {
        case .primary:
            return IslandVisual.primaryText
        case .secondary:
            return IslandVisual.secondaryText
        case .tertiary:
            return IslandVisual.tertiaryText
        case .positive:
            return IslandColor.liveTeal
        case .warning:
            return IslandColor.alertAmber
        case .accent:
            return heroAccentColor
        }
    }

    private var heroConfidenceLabel: String {
        operationalPresentation.confidenceLabel
    }

    private var showsHeroConfidenceChip: Bool {
        operationalPresentation.showsConfidenceChip
    }

    private var heroDecisionTitleText: String {
        operationalPresentation.heroDecisionTitle
    }

    private var scanControlActionTitle: String {
        if requiresModelSetup {
            return L10n.tr("设置模型")
        }
        if store.pendingScanControlAction == "pause" {
            return L10n.tr("暂停中")
        }
        if store.canRequestScanPause {
            return L10n.tr("暂停")
        }
        if store.isScanOperationActive {
            return expandedOperationalState == .finalizing
                ? L10n.tr("整理中")
                : L10n.tr("启动中")
        }
        if store.snapshot?.runtime.hasResumableRun == true {
            return L10n.tr("继续扫描")
        }
        return L10n.tr("开始扫描")
    }

    private var stopScanActionTitle: String {
        if store.pendingScanControlAction == "stop" {
            return L10n.tr("停止中")
        }
        return L10n.tr("停止")
    }

    private var isScanControlPending: Bool {
        store.pendingScanControlAction != nil
    }

    private func performScanControlAction() {
        if requiresModelSetup {
            openModelIngress()
            return
        }
        if store.canRequestScanPause {
            pendingScanConfirmation = .pause
            return
        }
        if store.snapshot?.runtime.hasResumableRun == true {
            store.resumeCurrentOperation()
            return
        }
        pendingScanConfirmation = .start
    }

    private func performConfirmedScanAction(
        _ confirmation: ScanConfirmation
    ) {
        switch confirmation {
        case .start:
            store.startRegularScan()
        case .pause:
            store.pauseScan()
        case .stop:
            store.stopScan()
        }
    }

    private var isProvisionalResult: Bool {
        store.snapshot?.dashboard.runMetadata.evaluationResultLevel == "provisional"
    }

    private var showsEvaluationProfileSelector: Bool {
        store.evaluationProfiles.count > 1
    }

    private var canUpgradeEvaluationProfile: Bool {
        isProvisionalResult
            && store.upgradeEvaluationProfile != nil
            && !store.isEvaluationProfileSelectionLocked
    }

    private var upgradeEvaluationProfileActionTitle: String {
        store.upgradeEvaluationProfile.map {
            L10n.tr("补全为%@", $0.label)
        } ?? L10n.tr("补全评测")
    }

    private var evaluationProfileScopePresentation:
        EvaluationProfileScopePresenter.Presentation {
        EvaluationProfileScopePresenter.present(
            EvaluationProfileScopePresenter.Input(
                isProvisional: isProvisionalResult,
                originalCandidateIDs: store.snapshot?.dashboard.runMetadata
                    .requestedCandidateIds ?? [],
                currentCandidateIDs: store.snapshot?.settingsProjection.scanScope
                    .regularCandidateIds ?? [],
                upgradeProfileLabel: store.upgradeEvaluationProfile?.label
            )
        )
    }

    private func performEvaluationProfileUpgradeAction() {
        guard !settings.isSaving else { return }
        if evaluationProfileScopePresentation.requiresDecision {
            showsEvaluationProfileDecision = true
            return
        }
        store.upgradeCurrentEvaluationProfile()
    }

    private var evaluationProfileSelector: some View {
        Button {
            showsEvaluationProfilePopover.toggle()
        } label: {
            HStack(spacing: 7) {
                Text(
                    displayedEvaluationProfile.map(localizedEvaluationProfileLabel)
                        ?? L10n.tr("评测范围")
                )
                    .lineLimit(1)
                    .fixedSize(horizontal: true, vertical: false)

                Spacer(minLength: 6)

                Image(
                    systemName: store.isEvaluationProfileSelectionLocked
                        ? "lock.fill"
                        : "chevron.down"
                )
                .font(Typography.micro)
                .foregroundStyle(IslandVisual.interactionText)
            }
            .frame(width: 172, alignment: .leading)
        }
        .buttonStyle(IslandActionButtonStyle(.secondary))
        .disabled(store.isEvaluationProfileSelectionLocked)
        .popover(isPresented: $showsEvaluationProfilePopover, arrowEdge: .bottom) {
            evaluationProfilePopover
        }
        .help(
            displayedEvaluationProfile.map(localizedEvaluationProfileSummary)
                ?? L10n.tr("选择下一轮评测范围")
        )
        .accessibilityLabel(L10n.tr("评测范围"))
        .accessibilityValue(
            displayedEvaluationProfile.map {
                L10n.tr(
                    "%@，%d 题",
                    localizedEvaluationProfileLabel($0),
                    $0.questionCount
                )
            } ?? L10n.tr("未选择")
        )
        .onChange(of: store.isEvaluationProfileSelectionLocked) { isLocked in
            if isLocked {
                showsEvaluationProfilePopover = false
            }
        }
    }

    private var displayedEvaluationProfile: BridgeEvaluationProfile? {
        if store.isEvaluationProfileSelectionLocked {
            return store.activeEvaluationProfile ?? store.selectedEvaluationProfile
        }
        return store.selectedEvaluationProfile
    }

    private var evaluationProfilePopover: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(L10n.tr("评测范围"))
                .font(Typography.sectionTitle)
                .foregroundStyle(IslandVisual.primaryText)

            VStack(spacing: 2) {
                ForEach(store.evaluationProfiles) { profile in
                    evaluationProfileOption(profile)
                }
            }
        }
        .padding(14)
        .frame(width: 330)
        .background(IslandColor.panelRaised)
        .preferredColorScheme(.dark)
        .environment(\.locale, appLanguage.locale)
    }

    private func evaluationProfileOption(_ profile: BridgeEvaluationProfile) -> some View {
        let isSelected = displayedEvaluationProfile?.id == profile.id

        return Button {
            store.selectEvaluationProfile(profile.id)
            showsEvaluationProfilePopover = false
        } label: {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                    .font(Typography.label)
                    .foregroundStyle(
                        isSelected ? IslandVisual.interactionText : IslandVisual.hintText
                    )
                    .frame(width: 16, height: 18)

                VStack(alignment: .leading, spacing: 4) {
                    Text(localizedEvaluationProfileLabel(profile))
                        .font(Typography.label)
                        .foregroundStyle(IslandVisual.primaryText)
                        .lineLimit(1)

                    Text(localizedEvaluationProfileSummary(profile))
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.secondaryText)
                        .fixedSize(horizontal: false, vertical: true)
                }

                Spacer(minLength: 8)

                Text(L10n.tr("%d 题", profile.questionCount))
                    .font(Typography.caption)
                    .monospacedDigit()
                    .foregroundStyle(IslandVisual.tertiaryText)
                    .padding(.top, 1)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 9)
            .background(
                RoundedRectangle(cornerRadius: IslandRadius.control, style: .continuous)
                    .fill(isSelected ? IslandVisual.selectedSurface : Color.clear)
            )
            .contentShape(RoundedRectangle(cornerRadius: IslandRadius.control, style: .continuous))
        }
        .buttonStyle(.plain)
        .islandPointerOnHover()
        .accessibilityValue(isSelected ? L10n.tr("已选择") : L10n.tr("未选择"))
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

    private func presentEvidence(candidateID: String) {
        selectedEvidence = store.radarEvidenceSelection(for: candidateID)
    }

    private var heroDecisionReasonText: String {
        operationalPresentation.heroDecisionReason
    }

    private var footerDataStatusText: String? {
        operationalPresentation.footerDataStatusText
    }

    private var footerStatusColor: Color {
        switch operationalPresentation.footerTone {
        case .active:
            return IslandColor.interaction
        case .warning:
            return IslandColor.alertAmber
        case .failure:
            return IslandColor.alertRed
        case .neutral, .success:
            return IslandVisual.tertiaryText
        }
    }

    private var showRestartButton: Bool {
        repairPresentation.showRestartButton
    }

    private var canRetryFailedQuestions: Bool {
        repairPresentation.canRetryFailedQuestions
    }

    private var timedOutQuestionCount: Int {
        repairPresentation.timedOutQuestionCount
    }

    private var canRetryTimedOutQuestions: Bool {
        repairPresentation.canRetryTimedOutQuestions
    }

    private func retryTimedOutQuestions() {
        guard let runID = repairPresentation.runID else { return }
        store.startTimedOutRepair(
            runID: runID,
            candidateIDs: repairPresentation.timedOutCandidateIDs
        )
    }

    private var repairableQuestionCount: Int {
        repairPresentation.repairableQuestionCount
    }

    private var failedRepairNoticeTitle: String {
        repairPresentation.failedNoticeTitle
    }

    private var repairNoticeEntry: DisplayEntry? {
        guard let entryID = repairPresentation.noticeEntryID else { return nil }
        return detailEntries.first { $0.id == entryID }
    }

    private var canDismissRepairNotice: Bool {
        repairPresentation.canDismissNotice
    }

}

private typealias DisplayEntry = RadarEntryPresenter.Entry

private extension RadarEntryPresenter.Entry {
    var accentColor: Color {
        switch accentTone {
        case .active:
            return IslandColor.interaction
        case .warning:
            return IslandColor.alertAmber
        case .neutral:
            return IslandVisual.secondaryText
        }
    }
}

struct ProviderLogoMark: View {
    let providerID: String?

    var body: some View {
        Group {
            if let image = providerLogoImage {
                Image(nsImage: image)
                    .resizable()
                    .renderingMode(.template)
                    .scaledToFit()
                    .foregroundStyle(IslandVisual.secondaryText)
                    .frame(width: 20, height: 20)
            } else {
                Text(ModelIdentityPresentation.providerMonogram(for: providerID))
                    .font(Typography.chip)
                    .foregroundStyle(IslandVisual.secondaryText)
            }
        }
            .frame(width: 28, height: 20)
            .accessibilityLabel(ModelIdentityPresentation.providerDisplayName(for: providerID))
    }

    private var providerLogoImage: NSImage? {
        guard let resourceName = ModelIdentityPresentation.providerLogoResourceName(
            for: providerID
        ),
              let url = Bundle.main.url(
                forResource: resourceName,
                withExtension: "svg",
                subdirectory: "ProviderLogos"
              ),
              let image = NSImage(contentsOf: url) else {
            return nil
        }
        image.isTemplate = true
        return image
    }
}

private enum RadarRankingLayout {
    static let columnSpacing: CGFloat = 4
    static let outerLeading: CGFloat = 8
    static let outerTrailing: CGFloat = 16
    static let modelHeaderLeading: CGFloat = 32
    static let rankWidth: CGFloat = 24
    static let brandWidth: CGFloat = 28
    static let scoreWidth: CGFloat = 72
    static let durationWidth: CGFloat = 92
    static let costWidth: CGFloat = 92
    static let rowHeight: CGFloat = 50
}

private struct RadarLeaderboardRow: View {
    let entry: RadarLeaderboardItem
    let rank: Int?
    let decisionTag: LeaderboardExportTag?
    let onPresentEvidence: () -> Void

    var body: some View {
        Button(action: onPresentEvidence) {
            HStack(spacing: RadarRankingLayout.columnSpacing) {
            Text(rank.map { "\($0)." } ?? "—")
                .font(Typography.caption)
                .foregroundStyle(IslandVisual.tertiaryText)
                .frame(width: RadarRankingLayout.rankWidth, alignment: .trailing)

            HStack(spacing: 9) {
                ProviderLogoMark(providerID: entry.providerId)

                HStack(spacing: 7) {
                    Text(entry.displayName)
                        .font(isEmphasized ? Typography.rankingModelEmphasis : Typography.rankingModel)
                        .foregroundStyle(isEmphasized ? IslandVisual.primaryText : IslandVisual.secondaryText)
                        .lineLimit(1)
                        .truncationMode(.middle)
                        .layoutPriority(1)

                    if let decisionTag {
                        RadarLeaderboardDecisionTag(tag: decisionTag)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            Text(scoreText)
                .font(rankingValueFont)
                .foregroundStyle(entry.score == nil
                    ? IslandVisual.tertiaryText
                    : (isEmphasized ? IslandVisual.primaryText : IslandVisual.secondaryText))
                .frame(width: RadarRankingLayout.scoreWidth, alignment: .trailing)

            Text(durationText)
                .font(rankingValueFont)
                .foregroundStyle(entry.elapsedSeconds == nil ? IslandVisual.tertiaryText : IslandVisual.secondaryText)
                .frame(width: RadarRankingLayout.durationWidth, alignment: .trailing)

            Text(costPresentation.text)
                .font(rankingValueFont)
                .foregroundStyle(entry.referenceCostUsd == nil ? IslandVisual.tertiaryText : IslandVisual.secondaryText)
                .frame(width: RadarRankingLayout.costWidth, alignment: .trailing)
                .help(costPresentation.helpText)
            }
        }
        .buttonStyle(.plain)
        .padding(.leading, RadarRankingLayout.outerLeading)
        .padding(.trailing, RadarRankingLayout.outerTrailing)
        .frame(height: RadarRankingLayout.rowHeight)
        .background(rowBackground)
        .overlay(alignment: .leading) {
            if entry.isRecommended || entry.isCurrent {
                Rectangle()
                    .fill(entry.isRecommended ? IslandColor.liveTeal : IslandColor.interaction)
                    .frame(width: 2)
            }
        }
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(IslandVisual.hairline.opacity(0.68))
                .frame(height: 0.5)
        }
        .accessibilityValue(accessibilityStatus)
        .accessibilityHint(L10n.tr("查看评测证据"))
    }

    private var accessibilityStatus: String {
        if entry.isRecommended { return L10n.tr("建议切换") }
        if entry.isCurrent { return L10n.tr("当前在用") }
        return decisionTag?.label ?? ""
    }

    private var scoreText: String {
        guard let score = entry.score else { return "—" }
        let scoreValue = score.rounded() == score
            ? String(Int(score))
            : String(format: "%.1f", score)
        guard let maxScore = entry.maxScore, maxScore != 100 else { return scoreValue }
        return "\(scoreValue)/\(Int(maxScore))"
    }

    private var durationText: String {
        guard let rounded = checkedRoundedDurationSeconds(entry.elapsedSeconds) else {
            return "—"
        }
        if rounded < 60 { return "\(rounded)s" }
        let minutes = rounded / 60
        let remainder = rounded % 60
        return remainder == 0 ? "\(minutes)m" : "\(minutes)m \(remainder)s"
    }

    private var costPresentation: RadarPresenter.ReferenceCostPresentation {
        RadarPresenter.referenceCost(
            value: entry.referenceCostUsd,
            coverage: entry.costCoverage
        )
    }

    private var rowBackground: Color {
        if entry.isRecommended { return IslandColor.liveTeal.opacity(0.03) }
        if entry.isCurrent { return IslandColor.interaction.opacity(0.03) }
        return .clear
    }

    private var isEmphasized: Bool {
        rank == 1 || entry.isCurrent || entry.isRecommended
    }

    private var rankingValueFont: Font {
        isEmphasized ? Typography.rankingValueEmphasis : Typography.rankingValue
    }

}

private struct RadarLeaderboardDecisionTag: View {
    let tag: LeaderboardExportTag

    var body: some View {
        Text(tag.label)
            .font(Typography.micro.weight(.semibold))
            .foregroundStyle(tag.kind == "recommended" ? IslandColor.interaction : IslandVisual.secondaryText)
            .lineLimit(1)
            .padding(.horizontal, 7)
            .frame(height: 20)
            .background(
                Capsule()
                    .fill(
                        tag.kind == "recommended"
                            ? IslandColor.interaction.opacity(0.12)
                            : IslandVisual.surfaceStrong.opacity(0.72)
                    )
            )
            .overlay {
                Capsule()
                    .strokeBorder(
                        tag.kind == "recommended"
                            ? IslandColor.interaction.opacity(0.34)
                            : IslandVisual.hairline,
                        lineWidth: 0.5
                    )
            }
            .fixedSize(horizontal: true, vertical: false)
    }
}

private struct ComparisonScoreTrendChart: View {
    let data: ComparisonPresenter.TrendData

    var body: some View {
        GeometryReader { outer in
            let plotHeight = max(0, outer.size.height - 18)
            HStack(alignment: .top, spacing: 8) {
                VStack {
                    Text("\(data.scale.upper)")
                    Spacer()
                    Text("\(data.scale.midpoint)")
                    Spacer()
                    Text("\(data.scale.lower)")
                }
                .font(Typography.micro)
                .foregroundStyle(IslandVisual.hintText)
                .monospacedDigit()
                .frame(width: 24, height: plotHeight)

                VStack(spacing: 5) {
                    GeometryReader { geometry in
                        ZStack(alignment: .topLeading) {
                            ForEach(0..<3, id: \.self) { index in
                                Rectangle()
                                    .fill(IslandVisual.workspaceBorder)
                                    .frame(height: 0.5)
                                    .offset(y: geometry.size.height * CGFloat(index) / 2)
                            }

                            if let latestCurrent = data.current.last {
                                trendBaseline(point: latestCurrent, size: geometry.size)
                                    .stroke(
                                        IslandColor.interaction.opacity(0.22),
                                        style: StrokeStyle(lineWidth: 1, dash: [2, 3])
                                    )
                            }

                            trendAreaPath(points: data.candidate, size: geometry.size)
                                .fill(
                                    LinearGradient(
                                        colors: [
                                            IslandColor.liveTeal.opacity(0.16),
                                            IslandColor.liveTeal.opacity(0)
                                        ],
                                        startPoint: .top,
                                        endPoint: .bottom
                                    )
                                )

                            trendPath(points: data.current, size: geometry.size)
                                .stroke(
                                    IslandColor.interaction,
                                    style: StrokeStyle(lineWidth: 1.4, lineCap: .round, lineJoin: .round)
                                )
                            trendPath(points: data.candidate, size: geometry.size)
                                .stroke(
                                    IslandColor.liveTeal,
                                    style: StrokeStyle(lineWidth: 1.4, lineCap: .round, lineJoin: .round)
                                )

                            ForEach(data.current.dropLast()) { point in
                                historicalTrendPoint(
                                    point,
                                    color: IslandColor.interaction,
                                    size: geometry.size
                                )
                            }
                            ForEach(data.candidate.dropLast()) { point in
                                historicalTrendPoint(
                                    point,
                                    color: IslandColor.liveTeal,
                                    size: geometry.size
                                )
                            }
                            if let latestCurrent = data.current.last {
                                latestTrendPoint(
                                    latestCurrent,
                                    color: IslandColor.interaction,
                                    size: geometry.size
                                )
                            }
                            if let latestCandidate = data.candidate.last {
                                latestTrendPoint(
                                    latestCandidate,
                                    color: IslandColor.liveTeal,
                                    size: geometry.size
                                )
                            }
                        }
                    }
                    .frame(height: plotHeight)

                    HStack {
                        Text(L10n.tr("较早"))
                        Spacer()
                        Text(L10n.tr("最新"))
                    }
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.hintText)
                }
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(L10n.tr("最近可比批次总分趋势"))
    }

    private func trendPath(
        points: [ComparisonPresenter.TrendPoint],
        size: CGSize
    ) -> Path {
        let positioned = points.compactMap { point -> (Int, CGPoint)? in
            guard let slotIndex = data.slots.firstIndex(of: point.slot) else { return nil }
            return (slotIndex, position(point, slotIndex: slotIndex, size: size))
        }

        var path = Path()
        var previousSlotIndex: Int?
        for (slotIndex, point) in positioned {
            if let previousSlotIndex, slotIndex == previousSlotIndex + 1 {
                path.addLine(to: point)
            } else {
                path.move(to: point)
            }
            previousSlotIndex = slotIndex
        }
        return path
    }

    private func trendAreaPath(
        points: [ComparisonPresenter.TrendPoint],
        size: CGSize
    ) -> Path {
        let positioned = points.compactMap { point -> (Int, CGPoint)? in
            guard let slotIndex = data.slots.firstIndex(of: point.slot) else { return nil }
            return (slotIndex, position(point, slotIndex: slotIndex, size: size))
        }

        var path = Path()
        var segmentStart: CGPoint?
        var previousPoint: CGPoint?
        var previousSlotIndex: Int?

        func closeSegment() {
            guard let segmentStart, let previousPoint else { return }
            path.addLine(to: CGPoint(x: previousPoint.x, y: size.height))
            path.addLine(to: CGPoint(x: segmentStart.x, y: size.height))
            path.closeSubpath()
        }

        for (slotIndex, point) in positioned {
            if let previousSlotIndex, slotIndex == previousSlotIndex + 1 {
                path.addLine(to: point)
            } else {
                closeSegment()
                path.move(to: point)
                segmentStart = point
            }
            previousPoint = point
            previousSlotIndex = slotIndex
        }
        closeSegment()
        return path
    }

    private func trendBaseline(
        point: ComparisonPresenter.TrendPoint,
        size: CGSize
    ) -> Path {
        let slotIndex = data.slots.firstIndex(of: point.slot) ?? 0
        let y = position(point, slotIndex: slotIndex, size: size).y
        return Path { path in
            path.move(to: CGPoint(x: 0, y: y))
            path.addLine(to: CGPoint(x: size.width, y: y))
        }
    }

    private func historicalTrendPoint(
        _ point: ComparisonPresenter.TrendPoint,
        color: Color,
        size: CGSize
    ) -> some View {
        let slotIndex = data.slots.firstIndex(of: point.slot) ?? 0
        let coordinate = position(point, slotIndex: slotIndex, size: size)
        return Circle()
            .fill(color.opacity(0.72))
            .frame(width: 4, height: 4)
            .position(coordinate)
    }

    private func latestTrendPoint(
        _ point: ComparisonPresenter.TrendPoint,
        color: Color,
        size: CGSize
    ) -> some View {
        let slotIndex = data.slots.firstIndex(of: point.slot) ?? 0
        let coordinate = position(point, slotIndex: slotIndex, size: size)
        return ZStack {
            Circle()
                .fill(color.opacity(0.15))
                .frame(width: 12, height: 12)
            Circle()
                .fill(color)
                .frame(width: 5, height: 5)
        }
        .position(coordinate)
    }

    private func position(
        _ point: ComparisonPresenter.TrendPoint,
        slotIndex: Int,
        size: CGSize
    ) -> CGPoint {
        let horizontalDivisor = max(data.slots.count - 1, 1)
        let x = data.slots.count == 1
            ? size.width / 2
            : size.width * CGFloat(slotIndex) / CGFloat(horizontalDivisor)
        let scoreSpan = max(data.scale.upper - data.scale.lower, 1)
        let normalizedScore = CGFloat(point.score - data.scale.lower) / CGFloat(scoreSpan)
        let y = size.height * (1 - min(max(normalizedScore, 0), 1))
        return CGPoint(x: x, y: y)
    }
}

private struct ComparisonPage: View {
    let items: [RadarLeaderboardItem]
    let representativeDecision: BridgeRecommendationDecisionV2?
    let decisions: [BridgeRecommendationDecisionV2]
    let recommendationUse: BridgeRecommendationUseSummary?
    let statistics: BridgeStatistics?
    let leaderboard: [BridgeLeaderboardEntry]
    let pairwiseComparisons: [BridgePairwiseComparison]
    let referenceSnapshot: BridgeReferenceSnapshot?
    let advisorEvidence: BridgeAdvisorV2Evidence?
    let workload: BridgeCodexWorkloadSnapshot?
    let displaySource: String?
    let questionPackVersion: String?
    let graderVersion: String?
    let evaluationSnapshotID: String?
    let pricingSnapshotID: String?
    let itemIDByConfigurationID: [String: String]
    let sourceModeByConfigurationID: [String: String]
    let questionSemantics: [QuestionSemantic]

    @State private var selectedCurrentConfigurationID: String?
    @State private var manualCandidateByCurrentConfigurationID: [String: String] = [:]
    @State private var isEvaluationDetailsExpanded = false

    private var comparisonContentHorizontalInset: CGFloat {
        LayoutRhythm.section + IslandShape.expandedShoulderRadius
    }

    var body: some View {
        Group {
            if let currentItem, let candidateItem {
                comparisonContent(current: currentItem, candidate: candidateItem)
            } else {
                emptyState
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(IslandVisual.workspaceSurface)
        .onChange(of: selectedCurrentConfigurationID) { _ in
            isEvaluationDetailsExpanded = false
        }
        .onChange(of: displaySource) { _ in
            manualCandidateByCurrentConfigurationID.removeAll()
            isEvaluationDetailsExpanded = false
        }
    }

    private var comparisonSelection: ComparisonSelectionPresenter.Selection {
        ComparisonSelectionPresenter.select(
            items: items,
            representativeDecision: representativeDecision,
            decisions: decisions,
            selectedCurrentConfigurationID: selectedCurrentConfigurationID,
            manualCandidateByCurrentConfigurationID: manualCandidateByCurrentConfigurationID,
            itemIDByConfigurationID: itemIDByConfigurationID,
            displaySource: displaySource,
            sourceModeByConfigurationID: sourceModeByConfigurationID
        )
    }

    private var decision: BridgeRecommendationDecisionV2? {
        comparisonSelection.decision
    }

    private var comparisonChoices: [BridgeRecommendationDecisionV2] {
        comparisonSelection.choices
    }

    private var currentItem: RadarLeaderboardItem? {
        comparisonSelection.currentItem
    }

    private var automaticCandidateItem: RadarLeaderboardItem? {
        comparisonSelection.automaticCandidateItem
    }

    private var selectedManualCandidateID: String? {
        comparisonSelection.selectedManualCandidateID
    }

    private var candidateItem: RadarLeaderboardItem? {
        comparisonSelection.candidateItem
    }

    private var isManualComparison: Bool {
        comparisonSelection.isManualComparison
    }

    private var selectableManualCandidates: [RadarLeaderboardItem] {
        comparisonSelection.selectableManualCandidates
    }

    private func comparisonPresentation(
        current: RadarLeaderboardItem,
        candidate: RadarLeaderboardItem
    ) -> ComparisonPresenter.ComparisonOutput {
        ComparisonPresenter.present(
            ComparisonPresenter.ComparisonInput(
                current: presenterCandidate(current),
                candidate: presenterCandidate(candidate),
                isManualComparison: isManualComparison,
                decision: presenterDecision,
                pairwiseComparisons: presenterPairwiseComparisons,
                displaySource: displaySource,
                localTrendSeries: presenterLocalTrendSeries,
                officialTrendSeries: presenterOfficialTrendSeries,
                officialTokens: presenterOfficialTokens
            )
        )
    }

    private func presenterCandidate(
        _ item: RadarLeaderboardItem
    ) -> ComparisonPresenter.CandidateInput {
        ComparisonPresenter.CandidateInput(
            id: item.id,
            score: item.score,
            elapsedSeconds: item.elapsedSeconds,
            referenceCostUsd: item.referenceCostUsd
        )
    }

    private var presenterDecision: ComparisonPresenter.DecisionEvidenceInput? {
        guard let decision else { return nil }
        return ComparisonPresenter.DecisionEvidenceInput(
            currentCandidateId: comparisonSelection.itemID(
                for: decision.currentModelConfigurationId
            ) ?? decision.currentModelConfigurationId,
            candidateCandidateId: comparisonSelection.itemID(
                for: decision.candidateModelConfigurationId
            ),
            comparisonCandidateId: comparisonSelection.itemID(
                for: decision.comparisonCandidateModelConfigurationId
            ),
            currentScore: decision.quality.currentScore,
            candidateScore: decision.quality.candidateScore,
            qualityDeltaPoints: decision.quality.scoreDelta,
            currentSeconds: decision.time.currentSeconds,
            candidateSeconds: decision.time.candidateSeconds,
            timeDeltaPercent: decision.time.reductionPercent,
            currentCostUsd: decision.referenceCost.currentUsd,
            candidateCostUsd: decision.referenceCost.candidateUsd,
            costDeltaPercent: decision.referenceCost.reductionPercent,
            warningQuestionIds: decision.qualityWarningQuestionIds
        )
    }

    private var presenterPairwiseComparisons: [ComparisonPresenter.PairwiseInput] {
        pairwiseComparisons.map { pair in
            ComparisonPresenter.PairwiseInput(
                baselineCandidateId: pair.baselineCandidateId,
                candidateId: pair.candidateId,
                isComparable: pair.isComparable,
                baselineQualityScore: pair.baselineQualityScore,
                candidateQualityScore: pair.candidateQualityScore,
                qualityDeltaPoints: pair.qualityDeltaPoints,
                baselineElapsedSeconds: pair.baselineElapsedSeconds,
                candidateElapsedSeconds: pair.candidateElapsedSeconds,
                timeDeltaPercent: pair.timeDeltaPercent,
                baselineCostUsd: pair.baselineCostUsd,
                candidateCostUsd: pair.candidateCostUsd,
                costDeltaPercent: pair.costDeltaPercent,
                baselineTokens: presenterTokenValues(pair.baselineTokenTotals),
                candidateTokens: presenterTokenValues(pair.candidateTokenTotals),
                warningQuestionIds: pair.warningQuestionIds
            )
        }
    }

    private var presenterLocalTrendSeries: [ComparisonPresenter.LocalTrendSeriesInput] {
        statistics?.trendSeries.map { trend in
            ComparisonPresenter.LocalTrendSeriesInput(
                candidateId: trend.candidateId,
                runIndices: trend.overallScoreRunIndices,
                scores: trend.overallScoreValues
            )
        } ?? []
    }

    private var presenterOfficialTrendSeries: [ComparisonPresenter.OfficialTrendSeriesInput] {
        referenceSnapshot?.leaderboardProjection?.rows.map { row in
            ComparisonPresenter.OfficialTrendSeriesInput(
                candidateId: row.modelConfigurationId,
                points: row.trend.points.map { point in
                    ComparisonPresenter.OfficialTrendPointInput(
                        batchId: point.batchId,
                        publishedAt: point.publishedAt,
                        score: point.score
                    )
                }
            )
        } ?? []
    }

    private var presenterOfficialTokens: [ComparisonPresenter.OfficialTokenInput] {
        referenceSnapshot?.entries.compactMap { entry in
            guard let usage = entry.usage else { return nil }
            return ComparisonPresenter.OfficialTokenInput(
                candidateId: entry.modelConfigurationId,
                values: presenterTokenValues(usage)
            )
        } ?? []
    }

    private func presenterTokenValues(
        _ totals: BridgeTokenTotals
    ) -> ComparisonPresenter.TokenValues {
        ComparisonPresenter.TokenValues(
            input: totals.inputTokens,
            cachedInput: totals.cachedInputTokens,
            cacheWriteInput: totals.cacheWriteInputTokens,
            output: totals.outputTokens,
            reasoning: totals.reasoningTokens
        )
    }

    private func presenterTokenValues(
        _ usage: BridgeReferenceUsage
    ) -> ComparisonPresenter.TokenValues {
        ComparisonPresenter.TokenValues(
            input: usage.inputTokens,
            cachedInput: usage.cachedInputTokens,
            cacheWriteInput: usage.cacheWriteInputTokens,
            output: usage.outputTokens,
            reasoning: usage.reasoningTokens
        )
    }

    private func comparisonContent(
        current: RadarLeaderboardItem,
        candidate: RadarLeaderboardItem
    ) -> some View {
        let presentation = comparisonPresentation(
            current: current,
            candidate: candidate
        )
        return VStack(spacing: 0) {
            Group {
                if isEvaluationDetailsExpanded {
                    evaluationDetailsContent(
                        current: current,
                        recommended: candidate,
                        presentation: presentation
                    )
                } else {
                    comparisonOverview(
                        current: current,
                        candidate: candidate,
                        presentation: presentation
                    )
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .clipped()

            evaluationDetailsBar
                .frame(height: 36)
        }
    }

    private func comparisonOverview(
        current: RadarLeaderboardItem,
        candidate: RadarLeaderboardItem,
        presentation: ComparisonPresenter.ComparisonOutput
    ) -> some View {
        HStack(spacing: 0) {
            comparisonDecisionSummary(
                current: current,
                candidate: candidate,
                presentation: presentation
            )
                .frame(width: 352)

            Rectangle()
                .fill(IslandVisual.workspaceBorder)
                .frame(width: 0.5)

            scoreTrendSection(
                current: current,
                candidate: candidate,
                presentation: presentation
            )
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .background(IslandVisual.workspaceSurface)
    }

    private var comparisonHairline: some View {
        Rectangle()
            .fill(IslandVisual.workspaceBorder)
            .frame(height: 0.5)
    }

    private func comparisonDecisionSummary(
        current: RadarLeaderboardItem,
        candidate: RadarLeaderboardItem,
        presentation: ComparisonPresenter.ComparisonOutput
    ) -> some View {
        let qualityGuard = ComparisonPresenter.qualityGuard(
            sameConfiguration: current.id == candidate.id,
            isManualComparison: isManualComparison,
            pairwiseComparable: presentation.evidence?.pairwiseComparable,
            status: decision?.qualityGuard?.status,
            rule: decision?.qualityGuard?.rule,
            thresholdPoints: decision?.qualityGuard?.thresholdPoints
        )
        return VStack(alignment: .leading, spacing: 0) {
            VStack(spacing: 0) {
                comparisonCurrentControl
                comparisonCandidateControl
            }
            .padding(.horizontal, comparisonContentHorizontalInset)
            .frame(height: 64)

            comparisonHairline

            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(comparisonDecisionPresentation.title)
                    .font(Typography.heroDecision)
                    .foregroundStyle(
                        comparisonEmphasisColor(comparisonDecisionPresentation.emphasis)
                    )
                    .lineLimit(1)
                    .minimumScaleFactor(0.82)
                    .layoutPriority(1)
                Spacer(minLength: 8)
                Text(qualityGuard.text)
                    .font(Typography.micro)
                    .foregroundStyle(comparisonEmphasisColor(qualityGuard.emphasis))
                    .lineLimit(1)
                    .minimumScaleFactor(0.72)
            }
            .padding(.horizontal, comparisonContentHorizontalInset)
            .frame(height: 54)

            comparisonHairline

            comparisonMetricList(
                current: current,
                candidate: candidate,
                presentation: presentation
            )

            Spacer(minLength: 8)
        }
        .background(IslandVisual.summarySurface)
    }

    private var comparisonDecisionPresentation: ComparisonPresenter.DecisionPresentation {
        ComparisonPresenter.decisionPresentation(
            decision: decision?.decision,
            isManualComparison: isManualComparison
        )
    }

    @ViewBuilder
    private var comparisonCurrentControl: some View {
        if comparisonChoices.count > 1 {
            Menu {
                ForEach(comparisonChoices, id: \.currentModelConfigurationId) { choice in
                    Button {
                        selectedCurrentConfigurationID = choice.currentModelConfigurationId
                    } label: {
                        if choice.currentModelConfigurationId == decision?.currentModelConfigurationId {
                            Label(comparisonChoiceLabel(choice), systemImage: "checkmark")
                        } else {
                            Text(comparisonChoiceLabel(choice))
                        }
                    }
                }
            } label: {
                Color.clear.frame(maxWidth: .infinity, minHeight: 28, maxHeight: 28)
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .overlay(alignment: .leading) {
                comparisonIdentity(
                    label: L10n.tr("当前"),
                    value: currentItem?.displayName ?? L10n.tr("选择配置"),
                    showsChevron: true
                )
                .allowsHitTesting(false)
            }
            .help(L10n.tr("切换要解释的当前模型配置"))
        } else {
            comparisonIdentity(
                label: L10n.tr("当前"),
                value: currentItem?.displayName ?? L10n.tr("选择配置"),
                showsChevron: false
            )
        }
    }

    private var comparisonCandidateControl: some View {
        Menu {
            Button {
                setManualCandidateID(nil)
            } label: {
                if !isManualComparison {
                    Label(automaticCandidateMenuLabel, systemImage: "checkmark")
                } else {
                    Text(automaticCandidateMenuLabel)
                }
            }

            if !selectableManualCandidates.isEmpty {
                Divider()
                ForEach(selectableManualCandidates) { candidate in
                    Button {
                        setManualCandidateID(candidate.id)
                    } label: {
                        if candidate.id == selectedManualCandidateID {
                            Label(candidate.displayName, systemImage: "checkmark")
                        } else {
                            Text(candidate.displayName)
                        }
                    }
                }
            }
        } label: {
            Color.clear.frame(maxWidth: .infinity, minHeight: 28, maxHeight: 28)
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .overlay(alignment: .leading) {
            comparisonIdentity(
                label: L10n.tr("候选"),
                value: candidateItem?.displayName ?? L10n.tr("选择候选"),
                showsChevron: true
            )
            .allowsHitTesting(false)
        }
        .help(L10n.tr("替换本页对比候选；不改变雷达和胶囊推荐"))
        .accessibilityLabel(L10n.tr("选择对比候选"))
        .accessibilityValue(candidateItem?.displayName ?? L10n.tr("暂无候选"))
    }

    private func comparisonChoiceLabel(_ choice: BridgeRecommendationDecisionV2) -> String {
        comparisonSelection.choiceLabel(for: choice)
    }

    private var automaticCandidateMenuLabel: String {
        "\(comparisonDecisionPresentation.automaticCandidatePrefix) · "
            + (automaticCandidateItem?.displayName ?? L10n.tr("暂无候选"))
    }

    private func setManualCandidateID(_ candidateID: String?) {
        guard let currentID = decision?.currentModelConfigurationId else { return }
        if let candidateID {
            manualCandidateByCurrentConfigurationID[currentID] = candidateID
        } else {
            manualCandidateByCurrentConfigurationID.removeValue(forKey: currentID)
        }
        isEvaluationDetailsExpanded = false
    }

    private func comparisonIdentity(
        label: String,
        value: String,
        showsChevron: Bool
    ) -> some View {
        HStack(spacing: 7) {
            Text(label)
                .font(Typography.micro)
                .foregroundStyle(IslandVisual.tertiaryText)
                .lineLimit(1)
                .minimumScaleFactor(0.82)
                .frame(width: 56, alignment: .leading)
            Text(value)
                .font(Typography.label)
                .foregroundStyle(IslandVisual.primaryText)
                .lineLimit(1)
                .minimumScaleFactor(0.78)
            if showsChevron {
                Image(systemName: "chevron.down")
                    .font(Typography.micro)
                    .foregroundStyle(IslandColor.interaction)
            }
        }
        .frame(maxWidth: .infinity, minHeight: 28, alignment: .leading)
        .contentShape(Rectangle())
    }

    private func comparisonMetricList(
        current: RadarLeaderboardItem,
        candidate: RadarLeaderboardItem,
        presentation: ComparisonPresenter.ComparisonOutput
    ) -> some View {
        let sameConfiguration = current.id == candidate.id
        let evidence = presentation.evidence
        let qualityChange = ComparisonPresenter.qualityChange(
            deltaPoints: evidence?.qualityDeltaPoints,
            sameConfiguration: sameConfiguration
        )
        let timeChange = ComparisonPresenter.timeChange(
            deltaPercent: evidence?.timeDeltaPercent,
            sameConfiguration: sameConfiguration
        )
        let costChange = ComparisonPresenter.costChange(
            deltaPercent: evidence?.costDeltaPercent,
            sameConfiguration: sameConfiguration
        )
        return VStack(spacing: 0) {
            comparisonMetricRow(
                title: L10n.tr("总分"),
                current: scoreText(evidence?.currentScore),
                candidate: scoreText(evidence?.candidateScore),
                change: qualityChange
            )
            comparisonHairline.padding(.leading, comparisonContentHorizontalInset)
            comparisonMetricRow(
                title: L10n.tr("整轮耗时"),
                current: durationText(evidence?.currentSeconds),
                candidate: durationText(evidence?.candidateSeconds),
                change: timeChange
            )
            comparisonHairline.padding(.leading, comparisonContentHorizontalInset)
            comparisonMetricRow(
                title: L10n.tr("参考费用"),
                current: costText(evidence?.currentCostUsd),
                candidate: costText(evidence?.candidateCostUsd),
                change: costChange
            )
        }
    }

    private func comparisonMetricRow(
        title: String,
        current: String,
        candidate: String,
        change: ComparisonPresenter.Presentation
    ) -> some View {
        HStack(alignment: .center, spacing: 10) {
            Text(title)
                .font(Typography.micro)
                .foregroundStyle(IslandVisual.tertiaryText)
                .frame(width: 58, alignment: .leading)
            Text("\(current) → \(candidate)")
                .font(Typography.label)
                .foregroundStyle(IslandVisual.secondaryText)
                .monospacedDigit()
                .lineLimit(1)
                .minimumScaleFactor(0.72)
                .layoutPriority(1)
            Spacer(minLength: 6)
            Text(change.text)
                .font(Typography.label)
                .foregroundStyle(comparisonEmphasisColor(change.emphasis))
                .monospacedDigit()
                .lineLimit(1)
                .minimumScaleFactor(0.78)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, comparisonContentHorizontalInset)
        .frame(height: 46)
    }

    private func comparisonEmphasisColor(
        _ emphasis: ComparisonPresenter.Emphasis
    ) -> Color {
        switch emphasis {
        case .primary:
            return IslandVisual.primaryText
        case .positive:
            return IslandColor.liveTeal
        case .warning:
            return IslandColor.alertAmber
        case .secondary:
            return IslandVisual.secondaryText
        case .tertiary:
            return IslandVisual.tertiaryText
        }
    }

    private func questionComparison(
        current: RadarLeaderboardItem,
        recommended: RadarLeaderboardItem,
        presentation: ComparisonPresenter.ComparisonOutput
    ) -> some View {
        let rows = ComparisonPresenter.questionRows(
            questions: questionSemantics.map {
                ComparisonPresenter.QuestionInput(
                    id: $0.questionId,
                    shortLabel: L10n.Question.shortLabel($0.questionNumber),
                    capabilityLabel: L10n.Question.capability(
                        id: $0.capabilityId,
                        fallback: $0.capabilityLabel
                    )
                )
            },
            currentScores: current.questionScores,
            candidateScores: recommended.questionScores,
            warningQuestionIDs: presentation.questionRisk.warningQuestionIds
        )
        return VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                Text(L10n.tr("%d 题明细", rows.count))
                    .font(Typography.sectionLabel)
                    .foregroundStyle(IslandVisual.secondaryText)
                Spacer(minLength: 8)
                Text(L10n.tr("当前 → 候选"))
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.tertiaryText)
            }
                .padding(.horizontal, comparisonContentHorizontalInset)
                .frame(height: 34, alignment: .leading)

            ForEach(Array(rows.enumerated()), id: \.element.id) { index, row in
                HStack(spacing: 12) {
                    Text(row.shortLabel)
                        .font(Typography.label)
                        .foregroundStyle(IslandVisual.secondaryText)
                        .frame(width: 34, alignment: .leading)
                    Text(row.capabilityLabel)
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.tertiaryText)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .lineLimit(1)
                    if row.showsWarning {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .font(Typography.micro)
                            .foregroundStyle(IslandColor.alertAmber)
                            .help(L10n.tr("后端对比投影将本题标记为显著下降"))
                    }
                    questionScore(row.currentScoreText)
                        .frame(width: 48, alignment: .trailing)
                    Image(systemName: "arrow.right")
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.hintText)
                    questionScore(row.candidateScoreText)
                        .frame(width: 48, alignment: .trailing)
                }
                .padding(.horizontal, comparisonContentHorizontalInset)
                .frame(height: 32)
                .overlay(alignment: .bottom) {
                    if index < rows.count - 1 {
                        Rectangle()
                            .fill(IslandVisual.hairline.opacity(0.7))
                            .frame(height: 0.5)
                            .padding(.leading, comparisonContentHorizontalInset)
                    }
                }
            }

        }
    }

    private func questionScore(
        _ value: String?
    ) -> some View {
        Text(value ?? "-")
            .font(Typography.label)
            .foregroundStyle(value == nil ? IslandVisual.hintText : IslandVisual.primaryText)
            .monospacedDigit()
    }

    private var realizedBenefitPresentation: ComparisonPresenter.RealizedBenefitPresentation? {
        ComparisonPresenter.realizedBenefit(
            recommendationUse?.benefitSummary.map {
                ComparisonPresenter.RealizedBenefitInput(
                    status: $0.status,
                    observedWorkUnitCount: $0.observedWorkUnitCount,
                    referenceCostWorkUnitCount: $0.referenceCostWorkUnitCount,
                    modelWaitWorkUnitCount: $0.modelWaitWorkUnitCount,
                    referenceCostDeltaUsd: $0.referenceCostDeltaUsd,
                    modelWaitDeltaMs: $0.modelWaitDeltaMs
                )
            },
            isManualComparison: isManualComparison
        )
    }

    private func realizedBenefitMetric(
        title: String,
        value: String
    ) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title)
                .font(Typography.micro)
                .foregroundStyle(IslandVisual.tertiaryText)
            Text(value)
                .font(Typography.micro.weight(.semibold))
                .foregroundStyle(IslandVisual.secondaryText)
                .monospacedDigit()
                .lineLimit(1)
                .minimumScaleFactor(0.85)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func scoreTrendSection(
        current: RadarLeaderboardItem,
        candidate: RadarLeaderboardItem,
        presentation: ComparisonPresenter.ComparisonOutput
    ) -> some View {
        let trendData = presentation.trend
        return VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(L10n.tr("稳定性证据"))
                    .font(Typography.sectionLabel)
                    .foregroundStyle(IslandVisual.secondaryText)
                Text(L10n.tr("最近 %d 次", trendData.slots.count))
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.tertiaryText)
                Spacer(minLength: 8)
                singleQuestionRiskSummary(presentation.questionRisk)
            }
            .padding(.horizontal, comparisonContentHorizontalInset)
            .frame(height: 64)

            comparisonHairline

            if trendData.hasValues {
                VStack(spacing: 14) {
                    HStack(spacing: LayoutRhythm.section) {
                        comparisonTrendLegend(
                            label: L10n.tr("当前"),
                            latest: trendData.current.last?.score,
                            color: IslandColor.interaction
                        )
                        Spacer(minLength: 8)
                    if current.id != candidate.id {
                            comparisonTrendLegend(
                                label: L10n.tr("候选"),
                                latest: trendData.candidate.last?.score,
                            color: IslandColor.liveTeal
                        )
                    }
                    }

                    ComparisonScoreTrendChart(data: trendData)
                        .frame(minHeight: 168, maxHeight: .infinity)
                        .layoutPriority(1)

                    if trendData.hasGap {
                        Text(L10n.tr("断点表示该批次没有有效总分，不做插值"))
                            .font(Typography.micro)
                            .foregroundStyle(IslandVisual.hintText)
                    }
                }
                .padding(.horizontal, comparisonContentHorizontalInset)
                .padding(.vertical, 16)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                Text(L10n.tr("当前题包与评分器下尚无可比历史，本轮差值仍可用于左侧即时比较。"))
                    .font(Typography.label)
                    .foregroundStyle(IslandVisual.tertiaryText)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .center)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func comparisonTrendLegend(
        label: String,
        latest: Int?,
        color: Color
    ) -> some View {
        HStack(spacing: 7) {
            Circle()
                .fill(color)
                .frame(width: 6, height: 6)
            Text(label)
                .font(Typography.micro)
                .foregroundStyle(IslandVisual.tertiaryText)
            Text(latest.map(String.init) ?? "-")
                .font(Typography.chartValue)
                .foregroundStyle(color)
                .monospacedDigit()
        }
    }

    @ViewBuilder
    private func singleQuestionRiskSummary(
        _ risk: ComparisonPresenter.QuestionRisk
    ) -> some View {
        if risk.warningCount > 0 {
            HStack(spacing: 6) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(Typography.micro)
                    .foregroundStyle(IslandColor.alertAmber)
                Text(L10n.tr("%d 项存在显著下降", risk.warningCount))
                    .font(Typography.micro)
                    .foregroundStyle(IslandColor.alertAmber)
            }
        }
    }

    private var evaluationDetailsBar: some View {
        Button {
            isEvaluationDetailsExpanded.toggle()
        } label: {
            HStack(spacing: 8) {
                Text(
                    isEvaluationDetailsExpanded
                        ? L10n.tr("返回趋势")
                        : L10n.tr("评测详情")
                )
                    .font(Typography.sectionLabel)
                    .foregroundStyle(IslandVisual.secondaryText)
                Spacer(minLength: 8)
                Image(systemName: isEvaluationDetailsExpanded ? "chevron.up" : "chevron.down")
                    .font(Typography.micro)
                    .foregroundStyle(IslandColor.interaction)
            }
            .padding(.horizontal, comparisonContentHorizontalInset)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(
            isEvaluationDetailsExpanded
                ? L10n.tr("返回总分趋势")
                : L10n.tr("查看评测详情")
        )
        .background(IslandVisual.workspaceSurface)
        .overlay(alignment: .top) { comparisonHairline }
    }

    private func evaluationDetailsContent(
        current: RadarLeaderboardItem,
        recommended: RadarLeaderboardItem,
        presentation: ComparisonPresenter.ComparisonOutput
    ) -> some View {
        GeometryReader { geometry in
            ScrollView(.vertical, showsIndicators: true) {
                if geometry.size.width >= 820 {
                    HStack(alignment: .top, spacing: 0) {
                        evaluationPrimaryDetails(
                            current: current,
                            recommended: recommended,
                            presentation: presentation
                        )
                        .frame(width: geometry.size.width * 0.5)

                        Rectangle()
                            .fill(IslandVisual.workspaceBorder)
                            .frame(width: 0.5)

                        evaluationSupportingDetails(
                            current: current,
                            recommended: recommended
                        )
                        .frame(maxWidth: .infinity)
                    }
                    .frame(width: geometry.size.width, alignment: .topLeading)
                } else {
                    VStack(spacing: 0) {
                        evaluationPrimaryDetails(
                            current: current,
                            recommended: recommended,
                            presentation: presentation
                        )
                        comparisonHairline
                        evaluationSupportingDetails(
                            current: current,
                            recommended: recommended
                        )
                    }
                    .frame(width: geometry.size.width, alignment: .topLeading)
                }
            }
        }
        .background(IslandVisual.workspaceSurface)
        .overlay(alignment: .top) { comparisonHairline }
    }

    private func evaluationPrimaryDetails(
        current: RadarLeaderboardItem,
        recommended: RadarLeaderboardItem,
        presentation: ComparisonPresenter.ComparisonOutput
    ) -> some View {
        VStack(spacing: 0) {
            questionComparison(
                current: current,
                recommended: recommended,
                presentation: presentation
            )
            comparisonHairline
            tokenSection(presentation.tokens)
        }
    }

    private func evaluationSupportingDetails(
        current: RadarLeaderboardItem,
        recommended: RadarLeaderboardItem
    ) -> some View {
        VStack(spacing: 0) {
            evidenceSection(current: current, recommended: recommended)
            if configurationEvidencePresentation(
                current: current,
                recommended: recommended
            ).hasDetails {
                comparisonHairline
                configurationEvidenceSection(current: current, recommended: recommended)
            }
            comparisonHairline
            realUsageSection(current: current, recommended: recommended)
        }
    }

    private func realUsageSection(
        current: RadarLeaderboardItem,
        recommended: RadarLeaderboardItem
    ) -> some View {
        let presentation = realUsagePresentation(
            current: current,
            recommended: recommended
        )
        return VStack(alignment: .leading, spacing: 10) {
            Text(L10n.tr("ModelDial 记录到的变化"))
                .font(Typography.sectionLabel)
                .foregroundStyle(IslandVisual.secondaryText)

            if let benefit = realizedBenefitPresentation {
                VStack(alignment: .leading, spacing: 8) {
                    HStack(spacing: 7) {
                        Image(systemName: benefit.statusIcon)
                            .font(Typography.micro)
                            .foregroundStyle(comparisonEmphasisColor(benefit.emphasis))
                        Text(benefit.title)
                            .font(Typography.micro.weight(.semibold))
                            .foregroundStyle(IslandVisual.secondaryText)
                        Spacer(minLength: 8)
                        Text(benefit.completedWorkText)
                            .font(Typography.micro)
                            .foregroundStyle(IslandVisual.tertiaryText)
                            .monospacedDigit()
                    }

                    HStack(spacing: 16) {
                        realizedBenefitMetric(
                            title: L10n.tr("等待时间"),
                            value: benefit.modelWaitText
                        )
                        realizedBenefitMetric(
                            title: L10n.tr("参考费用"),
                            value: benefit.referenceCostText
                        )
                    }

                    Text(benefit.noteText)
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.hintText)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .help(benefit.helpText)

                Rectangle()
                    .fill(IslandVisual.hairline.opacity(0.72))
                    .frame(height: 0.5)
                    .padding(.vertical, 2)
            }

            Text(L10n.tr("近期归因"))
                .font(Typography.micro.weight(.semibold))
                .foregroundStyle(IslandVisual.tertiaryText)

            if let emptyText = presentation.emptyText {
                Text(emptyText)
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.tertiaryText)
            } else {
                ForEach(presentation.rows) { row in
                    usageRow(row)
                }
            }

            Text(presentation.coverageText)
                .font(Typography.micro)
                .foregroundStyle(IslandVisual.hintText)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, comparisonContentHorizontalInset)
        .padding(.vertical, 12)
        .frame(maxWidth: .infinity, alignment: .topLeading)
    }

    private func usageRow(
        _ presentation: ComparisonPresenter.UsageRowPresentation
    ) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(presentation.displayName)
                .font(Typography.label)
                .foregroundStyle(IslandVisual.primaryText)
                .lineLimit(1)
            if let summaryText = presentation.summaryText {
                Text(summaryText)
                .font(Typography.micro)
                .foregroundStyle(IslandVisual.secondaryText)
                if let confidenceText = presentation.confidenceText {
                    Text(confidenceText)
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.tertiaryText)
                }
                if let behaviorText = presentation.behaviorText {
                    Text(behaviorText)
                        .font(Typography.micro)
                        .foregroundStyle(IslandVisual.tertiaryText)
                        .lineLimit(1)
                }
            } else if let emptyText = presentation.emptyText {
                Text(emptyText)
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.tertiaryText)
            }
        }
    }

    private func realUsagePresentation(
        current: RadarLeaderboardItem,
        recommended: RadarLeaderboardItem
    ) -> ComparisonPresenter.UsagePresentation {
        ComparisonPresenter.realUsage(
            current: presenterUsageCandidate(current),
            candidate: presenterUsageCandidate(recommended),
            workload: workload.map { workload in
                ComparisonPresenter.UsageWorkloadInput(
                    coverageStartedAtText: shortTimestamp(workload.coverageStartedAt),
                    coverageComplete: workload.coverageComplete,
                    aggregates: workload.aggregates.map { aggregate in
                        ComparisonPresenter.UsageAggregateInput(
                            modelConfigurationId: aggregate.modelConfigurationId,
                            providerId: aggregate.providerId,
                            rawModelId: aggregate.rawModelId,
                            reasoningEffort: aggregate.reasoningEffort,
                            completedWorkUnits: aggregate.completedWorkUnits,
                            failureCount: aggregate.failureCount,
                            sampleDays: aggregate.sampleDays,
                            attributionConfidence: aggregate.attributionConfidence,
                            behaviorObservedWorkUnits: aggregate.behaviorObservedWorkUnits,
                            behaviorCoveragePercent: aggregate.behaviorCoveragePercent,
                            oneShotRatePercent: aggregate.oneShotRatePercent
                        )
                    }
                )
            }
        )
    }

    private func presenterUsageCandidate(
        _ item: RadarLeaderboardItem
    ) -> ComparisonPresenter.UsageCandidateInput {
        ComparisonPresenter.UsageCandidateInput(
            id: item.id,
            displayName: item.displayName,
            modelName: item.modelName,
            providerId: item.providerId,
            effort: item.effort
        )
    }

    private func tokenSection(
        _ totals: ComparisonPresenter.TokenComparison
    ) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(L10n.tr("Token 明细"))
                .font(Typography.sectionLabel)
                .foregroundStyle(IslandVisual.secondaryText)
            tokenHeader
            tokenRow(L10n.tr("输入"), totals.current?.input, totals.candidate?.input)
            tokenRow(L10n.tr("缓存读取"), totals.current?.cachedInput, totals.candidate?.cachedInput)
            tokenRow(L10n.tr("缓存写入"), totals.current?.cacheWriteInput, totals.candidate?.cacheWriteInput)
            tokenRow(L10n.tr("输出"), totals.current?.output, totals.candidate?.output)
            tokenRow("Reasoning", totals.current?.reasoning, totals.candidate?.reasoning)
            Text(totals.evidenceNote)
                .font(Typography.micro)
                .foregroundStyle(IslandVisual.hintText)
        }
        .padding(.horizontal, comparisonContentHorizontalInset)
        .padding(.vertical, 12)
        .frame(maxWidth: .infinity, alignment: .topLeading)
    }

    private var tokenHeader: some View {
        HStack(spacing: 8) {
            Text(L10n.tr("类别"))
                .frame(maxWidth: .infinity, alignment: .leading)
            Text(L10n.tr("当前"))
                .frame(minWidth: 64, idealWidth: 88, maxWidth: 96, alignment: .trailing)
            Text(L10n.tr("候选"))
                .frame(minWidth: 64, idealWidth: 88, maxWidth: 96, alignment: .trailing)
        }
        .font(Typography.micro)
        .foregroundStyle(IslandVisual.tertiaryText)
        .lineLimit(1)
    }

    private func tokenRow(_ label: String, _ current: Int?, _ recommended: Int?) -> some View {
        HStack(spacing: 8) {
            Text(label)
                .frame(maxWidth: .infinity, alignment: .leading)
            Text(tokenText(current))
                .frame(minWidth: 64, idealWidth: 88, maxWidth: 96, alignment: .trailing)
            Text(tokenText(recommended))
                .frame(minWidth: 64, idealWidth: 88, maxWidth: 96, alignment: .trailing)
        }
        .font(Typography.micro)
        .foregroundStyle(IslandVisual.secondaryText)
        .monospacedDigit()
    }

    private func tokenText(_ value: Int?) -> String {
        guard let value else { return "-" }
        return value.formatted(.number.grouping(.automatic))
    }

    private func configurationEvidenceSection(
        current: RadarLeaderboardItem,
        recommended: RadarLeaderboardItem
    ) -> some View {
        let presentation = configurationEvidencePresentation(
            current: current,
            recommended: recommended
        )
        return VStack(alignment: .leading, spacing: 8) {
            Text(L10n.tr("配置证据"))
                .font(Typography.sectionLabel)
                .foregroundStyle(IslandVisual.secondaryText)
            ForEach(presentation.rows) { row in
                configurationRow(row)
            }
        }
        .padding(.horizontal, comparisonContentHorizontalInset)
        .padding(.vertical, 12)
        .frame(maxWidth: .infinity, alignment: .topLeading)
    }

    private func configurationRow(
        _ row: ConfigurationEvidencePresenter.RowPresentation
    ) -> some View {
        return VStack(alignment: .leading, spacing: 3) {
            Text(row.displayName)
                .font(Typography.label)
                .foregroundStyle(IslandVisual.primaryText)
                .lineLimit(1)
            if let identityDifference = row.identityDifferenceText {
                Text(identityDifference)
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.secondaryText)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            if let connection = row.connectionText {
                Text(L10n.tr("连接：%@", connection))
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.tertiaryText)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            if let route = row.routeText {
                Text(L10n.tr("路线：%@", route))
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.tertiaryText)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            if let completion = row.completionText {
                Text(L10n.tr("完成：%@", completion))
                    .font(Typography.micro)
                    .foregroundStyle(IslandVisual.hintText)
            }
        }
    }

    private func configurationEvidencePresentation(
        current: RadarLeaderboardItem,
        recommended: RadarLeaderboardItem
    ) -> ConfigurationEvidencePresenter.Presentation {
        ConfigurationEvidencePresenter.presentation(
            displaySource: displaySource,
            current: configurationEvidenceInput(current),
            candidate: configurationEvidenceInput(recommended)
        )
    }

    private func configurationEvidenceInput(
        _ item: RadarLeaderboardItem
    ) -> ConfigurationEvidencePresenter.ItemInput {
        let reference = referenceEntry(for: item)
        let local = leaderboardEntry(for: item)
        let resolved = advisorEvidence?.resolvedResultRows.first {
            $0.modelConfigurationId == item.id
        }
        return ConfigurationEvidencePresenter.ItemInput(
            id: item.id,
            displayName: item.displayName,
            modelName: item.modelName,
            effort: item.effort,
            official: ConfigurationEvidencePresenter.SourceInput(
                rawModelID: reference?.modelConfiguration.rawModelId,
                rawEffort: reference?.modelConfiguration.reasoningEffort,
                connectionParts: [
                    reference?.modelConfiguration.providerId,
                    reference?.modelConfiguration.serviceTier,
                ],
                routeFingerprint: reference?.routeFingerprint,
                completedAt: reference?.completedAt
            ),
            local: ConfigurationEvidencePresenter.SourceInput(
                rawModelID: local?.modelId,
                rawEffort: item.effort,
                connectionParts: [local?.sourceId, local?.connectionId],
                routeFingerprint: resolved?.routeFingerprint,
                completedAt: resolved?.completedAt ?? local?.latestValidAt
            )
        )
    }

    private func leaderboardEntry(for item: RadarLeaderboardItem) -> BridgeLeaderboardEntry? {
        leaderboard.first { $0.candidateId == item.id || $0.id == item.id }
    }

    private func referenceEntry(for item: RadarLeaderboardItem) -> BridgeReferenceSnapshotEntry? {
        referenceSnapshot?.entries.first { $0.modelConfigurationId == item.id }
    }

    private func shortTimestamp(_ value: String?) -> String {
        guard let value, !value.isEmpty else { return L10n.tr("未知") }
        return String(value.prefix(16)).replacingOccurrences(of: "T", with: " ")
    }

    private func evidenceSection(
        current: RadarLeaderboardItem,
        recommended: RadarLeaderboardItem
    ) -> some View {
        let configuration = configurationEvidencePresentation(
            current: current,
            recommended: recommended
        )
        return VStack(alignment: .leading, spacing: 8) {
            Text(L10n.tr("比较口径"))
                .font(Typography.sectionLabel)
                .foregroundStyle(IslandVisual.secondaryText)
            evidenceRow(L10n.tr("来源"), configuration.sourceLabel)
            evidenceRow(L10n.tr("题包"), questionPackVersion ?? L10n.tr("未知"))
            evidenceRow(L10n.tr("评分器"), graderVersion ?? L10n.tr("未知"))
            if let connection = configuration.sharedConnectionText {
                evidenceRow(L10n.tr("连接"), connection)
            }
            evidenceRow(L10n.tr("路线"), configuration.routeEvidenceText)
            if let completion = configuration.sharedCompletionText {
                evidenceRow(L10n.tr("完成"), completion)
            }
            evidenceRow(L10n.tr("评测快照"), evaluationSnapshotID ?? L10n.tr("未知"))
            evidenceRow(L10n.tr("价格快照"), pricingSnapshotID ?? L10n.tr("未知"))
        }
        .padding(.horizontal, comparisonContentHorizontalInset)
        .padding(.vertical, 12)
        .frame(maxWidth: .infinity, alignment: .topLeading)
    }

    private func evidenceRow(_ label: String, _ value: String) -> some View {
        HStack(spacing: 10) {
            Text(label)
                .font(Typography.micro)
                .foregroundStyle(IslandVisual.tertiaryText)
                .lineLimit(1)
                .minimumScaleFactor(0.72)
                .frame(width: 96, alignment: .leading)
            Text(value)
                .font(Typography.micro)
                .foregroundStyle(IslandVisual.secondaryText)
                .lineLimit(1)
                .truncationMode(.middle)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var emptyState: some View {
        VStack(spacing: 10) {
            Image(systemName: "arrow.left.arrow.right")
                .font(Typography.pageTitle)
                .foregroundStyle(IslandVisual.tertiaryText)
            Text(L10n.tr("尚无可比较的建议配置"))
                .font(Typography.sectionTitle)
                .foregroundStyle(IslandVisual.primaryText)
            Text(L10n.tr("先在雷达页完成当前配置与候选配置的同轮测试。"))
                .font(Typography.label)
                .foregroundStyle(IslandVisual.secondaryText)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func scoreText(_ score: Double?) -> String {
        guard let score else { return "-" }
        return String(Int(score.rounded()))
    }

    private func durationText(_ seconds: Double?) -> String {
        guard let total = checkedRoundedDurationSeconds(seconds) else { return "-" }
        return String(format: "%d:%02d", total / 60, total % 60)
    }

    private func costText(_ value: Double?) -> String {
        value.map { String(format: "$%.3f", $0) } ?? "-"
    }

}
