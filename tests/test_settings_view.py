from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest


class SettingsViewSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).resolve().parent.parent
        self.settings_view_source = (
            root / "Sources" / "Views" / "SettingsView.swift"
        ).read_text(encoding="utf-8")
        self.settings_modifier_path = (
            root / "Sources" / "Views" / "SettingsViewModifiers.swift"
        )
        settings_sources = [self.settings_view_source]
        if self.settings_modifier_path.exists():
            settings_sources.append(
                self.settings_modifier_path.read_text(encoding="utf-8")
            )
        self.source = "\n".join(settings_sources)
        self.settings_store_source = (
            root / "Sources" / "Model" / "SelectionSettingsStore.swift"
        ).read_text(encoding="utf-8")
        self.settings_patch_path = (
            root / "Sources" / "Model" / "SettingsConfigPatch.swift"
        )
        self.settings_patch_source = (
            self.settings_patch_path.read_text(encoding="utf-8")
            if self.settings_patch_path.exists()
            else ""
        )
        self.settings_ingress_presenter_source = (
            root / "Sources" / "Model" / "SettingsIngressPresenter.swift"
        ).read_text(encoding="utf-8")
        self.endpoint_state_path = (
            root / "Sources" / "Model" / "EndpointOperationState.swift"
        )
        self.selection_store_source = (
            root / "Sources" / "Model" / "SelectionStore.swift"
        ).read_text(encoding="utf-8")
        self.session_gateway_source = (
            root / "Sources" / "Model" / "AppSessionBridgeGateway.swift"
        ).read_text(encoding="utf-8")
        self.app_source = (root / "Sources" / "App.swift").read_text(encoding="utf-8")
        self.expanded_source = (
            root / "Sources" / "Views" / "ExpandedSelectionView.swift"
        ).read_text(encoding="utf-8")
        self.drag_source = (root / "Sources" / "Views" / "WindowDragArea.swift").read_text(
            encoding="utf-8"
        )

    def test_settings_root_separates_sync_presentation_and_page_boundaries(self) -> None:
        self.assertTrue(self.settings_modifier_path.exists())
        self.assertIn(
            ".modifier(SettingsSynchronizationModifier(",
            self.settings_view_source,
        )
        self.assertIn(
            ".modifier(SettingsPresentationModifier(",
            self.settings_view_source,
        )
        self.assertIn("private var activeSettingsPage: AnyView", self.settings_view_source)

        body = self.settings_view_source.split("var body: some View {", 1)[1].split(
            "private var scanConflictAlertIsPresented", 1
        )[0]
        self.assertNotIn(".onChange(", body)
        self.assertNotIn(".sheet(", body)
        self.assertNotIn(".alert(", body)

    def test_settings_store_has_no_snapshot_or_bridge_client_dependency(self) -> None:
        self.assertNotIn("BridgeSnapshot", self.settings_store_source)
        self.assertNotIn("NativeBridgeClient", self.settings_store_source)
        self.assertNotIn("readSettingsConfig", self.settings_store_source)
        self.assertNotIn("readSettingsConfig", self.selection_store_source)
        self.assertNotIn("func readConfig()", self.session_gateway_source)

    def test_backend_unavailability_replaces_false_empty_settings_states(self) -> None:
        self.assertIn("enum BackendAvailability: Equatable", self.selection_store_source)
        self.assertIn(
            "@Published private(set) var backendAvailability: BackendAvailability = .loading",
            self.selection_store_source,
        )
        self.assertIn("backendAvailability = .available", self.selection_store_source)
        self.assertIn("backendAvailability = .unavailable(", self.selection_store_source)

        scan = self.section_source("scanContent")
        targets = self.section_source("targetsContent")
        for section in (scan, targets):
            self.assertIn("selectionStore.isBackendAvailable", section)
            self.assertIn("backendAvailabilitySection", section)
        self.assertIn(
            "selectionStore.backendAvailability.unavailableMessage",
            self.section_source("settingsPageErrorMessage"),
        )
        self.assertIn("copyBackendFailureDiagnostic", self.settings_view_source)
        self.assertIn(
            ".disabled(!selectionStore.isBackendAvailable)",
            self.section_source("endpointConnectionSheet"),
        )
        self.assertIn(
            "if store.isBackendAvailable",
            self.expanded_source,
        )
        self.assertIn(
            "expandedBackendAvailabilityState",
            self.expanded_source,
        )
        self.assertIn(
            "panelFooter.disabled(!store.isBackendAvailable)",
            self.expanded_source,
        )

    def test_transient_ingress_projection_comes_from_authoritative_app_snapshot(self) -> None:
        provider_catalog = self.section_source("providerCatalog")
        detected_local_providers = self.section_source("detectedLocalProviders")

        self.assertIn(
            "selectionStore.snapshot?.config.providerCatalog",
            provider_catalog,
        )
        self.assertIn(
            "selectionStore.snapshot?.config.detectedLocalProviders",
            detected_local_providers,
        )
        self.assertNotIn("settings.draftConfig", provider_catalog)
        self.assertNotIn("settings.draftConfig", detected_local_providers)

    def test_endpoint_transient_state_has_a_dedicated_value_state_machine(self) -> None:
        self.assertTrue(self.endpoint_state_path.exists())
        endpoint_state_source = self.endpoint_state_path.read_text(encoding="utf-8")

        self.assertIn("struct EndpointOperationState", endpoint_state_source)
        self.assertIn("mutating func beginDraftOperation()", endpoint_state_source)
        self.assertIn("mutating func finishDraftOperation(", endpoint_state_source)
        self.assertIn("mutating func invalidateDraftOperations()", endpoint_state_source)
        self.assertIn(
            "@Published private(set) var endpoint = EndpointOperationState()",
            self.settings_store_source,
        )
        for old_publisher in (
            "@Published var discoveredModelIDs",
            "@Published var newlyDiscoveredModelIDs",
            "@Published var configuredDiscoveredModelIDs",
            "@Published var discoveredReasoningProfilesByModel",
            "@Published var discoveredDefaultReasoningProfileByModel",
            "@Published var endpointOperationMessage",
            "@Published var isEndpointOperationRunning",
            "@Published private(set) var testingConnectionID",
            "@Published private(set) var testingModelID",
            "@Published private(set) var endpointTestFeedback",
        ):
            self.assertNotIn(old_publisher, self.settings_store_source)
        self.assertNotIn("func isTestingConnection(", self.settings_store_source)
        self.assertNotIn("func testFeedback(", self.settings_store_source)
        self.assertIn("func isTesting(", endpoint_state_source)
        self.assertIn("func feedback(", endpoint_state_source)
        self.assertIn("settings.endpoint.isRunning", self.settings_view_source)
        self.assertNotIn(
            "settings.isEndpointOperationRunning",
            self.settings_view_source,
        )
        self.assertIn("settings.endpoint.isRunning", self.expanded_source)
        self.assertNotIn(
            "settings.isEndpointOperationRunning",
            self.expanded_source,
        )

    def section_source(self, name: str) -> str:
        markers = (f"private var {name}:", f"private func {name}(", f"private func {name}<")
        start = next(
            (self.source.index(marker) for marker in markers if marker in self.source),
            None,
        )
        if start is None:
            self.fail(f"missing Swift section: {name}")
        opening_brace = self.source.index("{", start)
        depth = 0
        for index in range(opening_brace, len(self.source)):
            character = self.source[index]
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    return self.source[start : index + 1]
        self.fail(f"unterminated Swift section: {name}")

    def test_model_ingress_exposes_regular_custom_and_single_actions(self) -> None:
        self.assertIn("Text(regularScanButtonTitle)", self.source)
        self.assertIn('return L10n.tr("开始扫描")', self.section_source("regularScanButtonTitle"))
        self.assertNotIn(
            "selectedEvaluationProfile",
            self.section_source("regularScanButtonTitle"),
        )
        self.assertIn('Text("自定义本轮")', self.source)
        self.assertIn('Text("单独扫描")', self.source)
        self.assertIn("settings.setModelCandidateEnabled", self.source)
        self.assertIn("selectionStore.startCustomScan(", self.source)
        self.assertIn("candidateIDs:", self.source)
        self.assertIn("selectionStore.startSingleScan(candidateID:", self.source)

    def test_model_ingress_exposes_quota_windows_and_data_health_owns_personal_data_lifecycle(self) -> None:
        data_health = self.section_source("dataHealthContent")
        ingress_detail = self.section_source("ingressDetailPane")
        self.assertIn("accountQuotaSection", ingress_detail)
        self.assertNotIn("accountQuotaSection", data_health)
        self.assertIn("dataManagementSection", data_health)
        self.assertIn('title: "官方额度"', self.section_source("accountQuotaSection"))
        self.assertIn("account.quotaWindows", self.source)
        self.assertIn("L10n.tr(account.planType ?? account.accountType)", self.source)
        self.assertIn("window.usedPercent", self.source)
        self.assertIn("window.resetsAt", self.source)
        self.assertIn('Label(L10n.tr("导出观察数据")', self.source)
        self.assertIn('Label(L10n.tr("清除观察数据")', self.source)
        self.assertIn("settings.exportPersonalObservations", self.source)
        self.assertIn("settings.clearPersonalObservations", self.source)

        root = Path(__file__).resolve().parent.parent
        bridge_source = (root / "Sources" / "Model" / "NativeBridgeClient.swift").read_text(
            encoding="utf-8"
        )
        self.assertIn('run(arguments: ["export-personal-observations"])', bridge_source)
        self.assertIn('run(arguments: ["clear-personal-observations"])', bridge_source)

    def test_data_health_dynamic_copy_follows_the_app_language(self) -> None:
        health = self.section_source("dataHealthContent")
        history = self.section_source("diagnosticHistorySection")
        management = self.section_source("dataManagementSection")

        for token in (
            'L10n.tr("数据链正常")',
            'L10n.tr("有数据需要检查")',
            'L10n.tr("已复制")',
            'L10n.tr("复制诊断")',
        ):
            self.assertIn(token, health)
        for token in (
            'L10n.tr("%d 个", history.sourceCount)',
            '"发现 %@ · 采样 %@ · 成功 %@ · 失败 %@ · 未知 %@"',
            '"%d / %d 个 · %.1f%%"',
            'L10n.tr("暂无已完成工作单元")',
            '"可判断 %@ · 不可判断 %@"',
        ):
            self.assertIn(token, history)
        self.assertIn("Text(L10n.tr(message))", management)

    def test_manual_profile_is_selectable_and_scheduled_scan_is_fixed_to_full(self) -> None:
        self.assertIn("private var evaluationProfileBinding: Binding<String>", self.source)
        self.assertIn("selectionStore.evaluationProfiles", self.source)
        self.assertIn("selectionStore.isEvaluationProfileSelectionLocked", self.source)
        scheduler = self.section_source("schedulerSection")
        self.assertIn('formRow("扫描范围")', scheduler)
        self.assertIn("selectionStore.scheduledEvaluationProfile", scheduler)
        self.assertIn("全部已启用配置", scheduler)
        self.assertNotIn("scheduledEvaluationProfileBinding", self.source)
        scheduled_profile = self.selection_store_source.split(
            "var scheduledEvaluationProfile: BridgeEvaluationProfile? {", 1
        )[1].split("}", 1)[0]
        self.assertIn("return completeEvaluationProfile", scheduled_profile)

    def test_custom_scan_validation_consumes_backend_preview(self) -> None:
        sheet = self.section_source("customScanSheet")
        self.assertIn("ScanPlanPreviewPresenter.option", sheet)
        self.assertIn("selectedPreview?.valid == true", sheet)
        self.assertIn("await loadCustomScanPlanOptions(request)", sheet)
        self.assertNotIn('selectedEvaluationProfile?.id == "quick"', sheet)
        self.assertNotIn("selectedCandidateIDs.count == 2", sheet)
        self.assertNotIn("hasValidCandidateCount", sheet)

    def test_candidate_identity_and_current_default_are_bridged_and_editable(self) -> None:
        root = Path(__file__).resolve().parent.parent
        models_source = (root / "Sources" / "Model" / "SelectionModels.swift").read_text(
            encoding="utf-8"
        )
        store_source = (
            root / "Sources" / "Model" / "SelectionSettingsStore.swift"
        ).read_text(encoding="utf-8")

        for token in (
            "let familyId: String?",
            "let variantId: String?",
            "let recommendation: BridgeRecommendationConfig",
            "let currentDefaultCandidateId: String?",
            "let currentModelMode: String",
        ):
            self.assertIn(token, models_source)
        self.assertNotIn('"family_id"', self.settings_patch_source)
        self.assertNotIn('"variant_id"', self.settings_patch_source)
        self.assertNotIn("func toPayload()", store_source)
        self.assertIn("func setCurrentDefault(candidateID: String?)", store_source)
        self.assertIn("func useAutomaticCurrentModel()", store_source)
        self.assertIn(
            "settings.setCurrentDefault(candidateID: option.id)",
            self.expanded_source,
        )
        self.assertNotIn("private func currentDefaultButton", self.source)
        self.assertNotIn(
            "settings.setCurrentDefault(candidateID: candidate.id)",
            self.source,
        )
        self.assertNotIn("codex config", store_source)

    def test_model_ingress_rows_defer_current_model_override_to_overview_picker(self) -> None:
        api_row = self.section_source("apiModelVariantRow")
        single_variant = self.section_source("singleVariantRow")
        profile_row = self.section_source("candidateRow")

        self.assertNotIn("currentDefaultButton(candidate)", self.source)
        self.assertNotIn("private func currentDefaultButton", self.source)
        self.assertNotIn('Text("设为当前在用")', self.source)
        self.assertNotIn('Text("设为比较基准")', self.source)
        self.assertIn(
            ".fixedSize(horizontal: true, vertical: false)",
            api_row,
        )
        self.assertIn(
            ".fixedSize(horizontal: true, vertical: false)",
            single_variant,
        )
        self.assertIn(
            ".fixedSize(horizontal: true, vertical: false)",
            profile_row,
        )
        self.assertNotIn("currentModelSelectionSection", self.source)
        self.assertNotIn("currentModelMenu", self.source)
        self.assertIn("private var currentInUsePicker", self.expanded_source)
        self.assertIn(
            "settings.setCurrentDefault(candidateID: option.id)",
            self.expanded_source,
        )
        self.assertIn("settings.useAutomaticCurrentModel()", self.expanded_source)
        self.assertIn('Button("恢复自动识别")', self.expanded_source)

    def test_settings_uses_locked_visual_tokens_and_shared_actions(self) -> None:
        for token in (
            "Typography.pageTitle",
            "Typography.sectionTitle",
            "IslandVisual.primaryText",
            "IslandVisual.secondaryText",
            "IslandVisual.hairline",
            "IslandActionButtonStyle",
            ".islandPointerOnHover()",
        ):
            self.assertIn(token, self.source)
        self.assertNotIn("PrimarySettingsButtonStyle", self.source)
        self.assertNotIn("SecondarySettingsButtonStyle", self.source)

    def test_settings_color_semantics_separate_interaction_from_status(self) -> None:
        body_start = self.source.index("var body: some View")
        body = self.source[body_start : self.source.index(".onAppear", body_start)]
        sidebar_button = self.section_source("settingsSidebarButton")
        source_card = self.section_source("sourceWorkspaceCard")
        provider_variant = self.section_source("endpointProviderVariantRow")
        save_feedback = self.section_source("settingsSaveFeedbackChip")

        self.assertIn(".tint(IslandColor.interaction)", body)
        self.assertNotIn(".tint(IslandColor.liveTeal)", body)
        self.assertIn(
            "isSelected ? IslandColor.interaction : IslandVisual.tertiaryText",
            sidebar_button,
        )
        self.assertIn("IslandVisual.surfaceStrong", sidebar_button)
        self.assertIn("IslandVisual.selectedBorder", sidebar_button)
        self.assertIn("settings-sidebar-selection", sidebar_button)
        self.assertIn(
            ".background(isSelected ? IslandVisual.surfaceStrong : Color.clear)",
            source_card,
        )
        self.assertIn("isSelected ? IslandColor.interaction : IslandVisual.hintText", provider_variant)
        self.assertIn("isPersisted ? IslandColor.liveTeal", provider_variant)
        self.assertIn("case .saving:", save_feedback)
        self.assertIn("IslandColor.interaction", save_feedback)
        self.assertIn("case .saved:", save_feedback)
        self.assertIn("IslandColor.liveTeal", save_feedback)
        self.assertIn("case .failed:", save_feedback)
        self.assertIn("IslandColor.alertRed", save_feedback)
        readiness_color = self.section_source("readinessColor")
        self.assertIn("case .needsBaseline: return IslandColor.alertAmber", readiness_color)
        self.assertNotIn("case .needsBaseline: return IslandColor.endpoint", readiness_color)

    def test_settings_uses_shared_page_gutter_and_eight_point_layout_rhythm(self) -> None:
        for token in (
            "static let contentPadding: CGFloat = 22",
            "static let sidebarWidth: CGFloat = 176",
            "static let sectionSpacing: CGFloat = 20",
            "LayoutRhythm.compact",
            "LayoutRhythm.standard",
            "LayoutRhythm.section",
        ):
            self.assertIn(token, self.source)
        self.assertNotIn("static let contentPadding: CGFloat = 32", self.source)

        for section_name in (
            "settingsPageHeader",
            "scanContent",
            "automationContent",
            "generalContent",
            "contentFooter",
        ):
            self.assertIn(
                ".padding(.horizontal, Layout.contentPadding)",
                self.section_source(section_name),
            )
        self.assertIn(
            ".padding(.horizontal, Layout.contentPadding)",
            self.section_source("targetsContent"),
        )

    def test_settings_uses_one_dark_custom_window_shell(self) -> None:
        self.assertNotIn("NavigationSplitView", self.source)
        self.assertNotIn("List(selection:", self.source)
        self.assertIn("IslandColor.canvas", self.source)
        self.assertIn("IslandVisual.workspaceSurface", self.source)
        self.assertIn("settingsSidebarButton", self.source)
        self.assertIn(".windowStyle(.hiddenTitleBar)", self.app_source)

    def test_settings_sidebar_and_footer_do_not_repeat_brand_wordmark(self) -> None:
        self.assertNotIn('Text("modeldial")', self.section_source("sidebar"))
        self.assertNotIn('Text("modeldial")', self.section_source("contentFooter"))
        self.assertIn("WindowDragArea()", self.section_source("sidebar"))
        self.assertIn("mouseDownCanMoveWindow", self.drag_source)

    def test_settings_rows_use_flat_native_grouping_language(self) -> None:
        row = self.section_source("formRow")
        section = self.section_source("settingsSection")

        self.assertIn("IslandVisual.hairline", row)
        self.assertIn("Divider()", row)
        self.assertIn("cornerRadius: IslandRadius.card", section)

    def test_model_candidate_rows_use_dividers_instead_of_nested_cards(self) -> None:
        for name in ("singleVariantRow", "candidateRow"):
            section = self.section_source(name)
            self.assertIn("IslandVisual.hairline", section)
            self.assertNotIn("RoundedRectangle", section)

    def test_scan_scope_summary_uses_one_continuous_metric_strip(self) -> None:
        metric_strip = self.section_source("metricStrip")
        self.assertIn("IslandVisual.hairline", metric_strip)
        self.assertIn("Typography.metricValue", self.section_source("metricLabel"))
        self.assertIn("metricStrip(regularScanScopeMetrics)", self.section_source("regularScanScopeSection"))

    def test_scan_scope_picker_reserves_room_for_the_full_profile_label(self) -> None:
        scope = self.section_source("regularScanScopeSection")

        self.assertIn('Picker("评测模式", selection: evaluationProfileBinding)', scope)
        self.assertIn(".frame(width: Layout.longControlWidth)", scope)

    def test_ingress_toolbar_exposes_enabled_configuration_popover(self) -> None:
        self.assertIn("@State private var showsEnabledCandidatesPopover", self.source)
        button = self.section_source("enabledCandidatesButton")
        self.assertIn('Text("已启用档位")', button)
        self.assertIn("enabledIngressCandidateCount", button)
        self.assertIn("showsEnabledCandidatesPopover = true", button)
        self.assertIn("enabledCandidatesPopover", button)
        popover = self.section_source("enabledCandidatesPopover")
        self.assertIn("enabledIngressWorkspaceItems", popover)
        self.assertIn("settingsIngressPresentation.regularModelFamilyGroups(", popover)
        self.assertIn("settings.setModelCandidateEnabled", popover)
        self.assertIn("settingsCandidatePresentation(", popover)
        self.assertIn("Text(presentation.displayModel)", popover)
        self.assertIn("Text(presentation.variantName)", popover)
        self.assertNotIn("Text(candidate.modelId)", popover)
        self.assertNotIn("Text(candidate.displayName)", popover)
        self.assertNotIn('Text("管理全部")', popover)

    def test_api_connections_can_be_explicitly_edited_and_deleted(self) -> None:
        connection_card = self.section_source("endpointConnectionCard")
        self.assertIn('Button("编辑连接")', connection_card)
        self.assertIn('Button("删除连接", role: .destructive)', connection_card)
        self.assertIn("requestConnectionDeletion(connection)", connection_card)
        local_connection = self.section_source("localConnectionDetailCard")
        self.assertNotIn('Button("移除接入", role: .destructive)', local_connection)
        self.assertNotIn("requestConnectionDeletion(connection)", local_connection)
        self.assertIn("showsDeleteConnectionConfirmation", self.source)
        self.assertIn("历史扫描成绩会保留", self.source)
        self.assertNotIn("本机登录状态不会受影响", self.source)
        self.assertIn(
            'guard source(for: connection.sourceId)?.mode == "api" else { return }',
            self.source,
        )

        self.assertIn("func deleteConnection(", self.settings_store_source)
        self.assertIn(".deleteConnection(connectionID: connectionID)", self.settings_store_source)
        self.assertNotIn("connections.remove(at: connectionIndex)", self.settings_store_source)
        self.assertIn("try AppSecretStore().delete(", self.settings_store_source)
        self.assertIn("func delete(connectionID: String, apiKeyRef: String?) throws", (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Model"
            / "AppSecretStore.swift"
        ).read_text(encoding="utf-8"))

    def test_endpoint_editor_keeps_persisted_models_read_only(self) -> None:
        editor = self.section_source("endpointConnectionSheet")
        family = self.section_source("endpointProviderFamilyCard")
        variant = self.section_source("endpointProviderVariantRow")
        opener = self.section_source("openEndpointEditor")

        self.assertIn("private var persistedEndpointModelIDs", self.source)
        self.assertIn("persistedEndpointModelIDs", family)
        self.assertIn("persistedEndpointModelIDs", variant)
        self.assertIn("endpointModelID = \"\"", opener)
        self.assertNotIn("endpointModelID = existingModelIDs.first", opener)
        self.assertIn("@State private var dismissEndpointEditorAfterSave = false", self.source)
        self.assertIn("finishEndpointEditorSaveIfNeeded", self.source)
        self.assertIn("dismissEndpointEditorAfterSave = true", editor)

    def test_api_model_clusters_and_variants_can_be_removed_without_deleting_history(self) -> None:
        family_card = self.section_source("apiModelFamilyCard")
        variant_row = self.section_source("apiModelVariantRow")
        removal = self.section_source("removeModelCandidates")

        self.assertIn("requestModelFamilyRemoval", family_card)
        self.assertIn('Button("移除模型簇", role: .destructive)', self.source)
        self.assertIn("requestModelCandidateRemoval", variant_row)
        self.assertIn('Button("移除档位", role: .destructive)', variant_row)
        self.assertIn("showsModelCandidateRemovalConfirmation", self.source)
        self.assertIn("modelCandidatesPendingRemoval", self.source)
        self.assertIn("settings.removeModelCandidates", removal)
        self.assertIn("历史扫描成绩会保留", self.source)

        self.assertIn("func removeModelCandidates(", self.settings_store_source)
        self.assertIn(".removeModelCandidates(", self.settings_store_source)
        self.assertNotIn("candidates.removeAll", self.settings_store_source)
        self.assertIn(
            "sessionStore.snapshot?.runtime.isRunning == true",
            self.settings_store_source,
        )
        self.assertIn(
            "sessionStore.snapshot?.runtime.hasResumableRun == true",
            self.settings_store_source,
        )

    def test_api_removal_menus_preserve_full_width_row_alignment(self) -> None:
        connection_card = self.section_source("endpointConnectionCard")
        family_card = self.section_source("apiModelFamilyCard")
        family_header = self.section_source("modelFamilyHeader")
        variant_row = self.section_source("apiModelVariantRow")

        self.assertIn('.frame(maxWidth: .infinity, alignment: .leading)', connection_card)
        self.assertIn('.frame(maxWidth: .infinity, alignment: .leading)', family_card)
        self.assertIn('.frame(maxWidth: .infinity, alignment: .leading)', family_header)
        self.assertIn('.frame(maxWidth: .infinity, alignment: .leading)', variant_row)
        self.assertIn('static let ingressModelIdentityWidth: CGFloat = 180', self.source)
        self.assertIn('static let ingressModelFamilyIdentityWidth: CGFloat = 220', self.source)
        self.assertIn(
            '.frame(width: Layout.ingressModelFamilyIdentityWidth, alignment: .leading)',
            family_header,
        )
        self.assertIn(
            '.frame(width: Layout.ingressModelIdentityWidth, alignment: .leading)',
            variant_row,
        )

    def test_new_scans_only_use_tested_enabled_endpoint_connections(self) -> None:
        scope = self.section_source("regularScanScopeSection")
        custom = self.section_source("customScanSheet")
        initialize = self.section_source("initializeCustomCandidateIDs")

        self.assertIn("unverifiedEnabledEndpointWorkspaceItems", self.source)
        self.assertIn("regularScanIsBlockedByEndpointVerification", scope)
        self.assertIn("guard !regularScanIsBlockedByEndpointVerification else { return }", scope)
        self.assertIn("settingsIngressPresentation.customSourceSections", custom)
        self.assertIn("selectedCustomCandidateIDs", custom)
        self.assertIn("customScanEligibleCandidateIDs", self.source)
        self.assertIn("customScanEligibleCandidateIDs", initialize)

    def test_editing_request_identity_is_delegated_to_the_backend_endpoint_command(self) -> None:
        save_connection = self.settings_store_source[
            self.settings_store_source.index("func saveEndpointConnection(") :
            self.settings_store_source.index("func importLocalProvider(")
        ]
        self.assertIn("BridgeEndpointUpsertIntent(", save_connection)
        self.assertIn("modelIDs: intendedModelIDs", save_connection)
        self.assertIn("lastTestStatus: lastTestStatus", save_connection)
        self.assertNotIn("SettingsConfigPatch.upsertEndpointConnection(", save_connection)
        self.assertNotIn("modelCandidates:", save_connection)
        self.assertNotIn("connectionRequestIdentityChanged", save_connection)
        self.assertNotIn('"连接信息已变更，请重新测试"', save_connection)

    def test_ingress_sources_use_one_continuous_row_group(self) -> None:
        grouped_list = self.section_source("ingressWorkspaceCardList")
        self.assertIn("VStack(spacing: 0)", grouped_list)
        self.assertIn("IslandVisual.hairline", grouped_list)
        self.assertNotIn("LazyVGrid", grouped_list)

    def test_custom_selection_is_transient(self) -> None:
        self.assertIn("@State private var customCandidateIDs", self.source)
        self.assertIn('@State private var customRoundMode = "new_round"', self.source)
        self.assertIn(".sheet(isPresented: $showsCustomScanSheet)", self.source)
        self.assertIn("initializeCustomCandidateIDs", self.source)
        self.assertIn('title: "追加到当前轮"', self.source)
        self.assertIn('title: "作为新一轮运行"', self.source)
        self.assertIn("@State private var customScanPlanOptions", self.source)
        self.assertIn("selectionStore.previewCustomScanOptions(", self.source)
        self.assertIn("selectionStore.startCustomScan(", self.source)
        self.assertIn("preview: selectedPreview", self.source)
        self.assertNotIn("canAppendSelectionToCurrentRound", self.source)
        self.assertNotIn("currentComparisonCandidateIDs", self.source)

    def test_model_candidates_are_grouped_into_expandable_families(self) -> None:
        self.assertIn(
            "typealias ModelFamilyGroup = SettingsIngressPresenter.ModelFamilyGroup",
            self.source,
        )
        self.assertIn("@State private var expandedModelFamilyIDs", self.source)
        self.assertIn("modelFamilyGroups(for: connection)", self.source)
        self.assertIn(
            "SettingsCandidatePresenter.presentation(",
            self.settings_ingress_presenter_source,
        )
        self.assertIn("candidatesByFamilyID", self.settings_ingress_presenter_source)
        self.assertIn("modelFamilyHeader", self.source)
        self.assertIn("expandedModelFamilyIDs.contains(family.id)", self.source)

    def test_model_family_total_switch_and_individual_switch_coexist(self) -> None:
        self.assertIn("settings.setModelCandidatesEnabled", self.source)
        self.assertIn("settings.setModelCandidateEnabled", self.source)
        self.assertIn("family.candidates.filter(\\.enabled).count", self.source)
        self.assertIn("candidateIDs: family.candidates.map(\\.id)", self.source)

    def test_model_family_group_control_exposes_a_real_mixed_state(self) -> None:
        header = self.section_source("modelFamilyHeader")
        control = self.section_source("modelFamilyEnableControl")

        self.assertIn("modelFamilyEnableControl(", header)
        self.assertNotIn("Toggle(", header)
        self.assertIn("let isPartiallyEnabled", control)
        self.assertIn('Image(systemName: "minus")', control)
        self.assertIn(".accessibilityValue(stateTitle)", control)
        self.assertIn("settings.setModelCandidatesEnabled", control)

    def test_secret_store_keeps_api_keys_out_of_bridge_arguments(self) -> None:
        root = Path(__file__).resolve().parent.parent
        keychain_source = (
            root / "Sources" / "Model" / "KeychainSecretStore.swift"
        ).read_text(encoding="utf-8")
        local_source = (
            root / "Sources" / "Model" / "LocalEncryptedSecretStore.swift"
        ).read_text(encoding="utf-8")
        app_secret_source = (
            root / "Sources" / "Model" / "AppSecretStore.swift"
        ).read_text(encoding="utf-8")
        settings_store_source = (
            root / "Sources" / "Model" / "SelectionSettingsStore.swift"
        ).read_text(encoding="utf-8")
        bridge_source = (
            root / "Sources" / "Model" / "NativeBridgeClient.swift"
        ).read_text(encoding="utf-8")

        self.assertIn("import Security", keychain_source)
        self.assertIn("SecItemAdd", keychain_source)
        self.assertIn("SecItemUpdate", keychain_source)
        self.assertIn("SecItemCopyMatching", keychain_source)
        self.assertIn("SecItemDelete", keychain_source)
        self.assertIn('keychain:\\(Self.service):\\(connectionID)', keychain_source)
        self.assertIn('static let service = "com.modeldial.api-key"', keychain_source)
        self.assertIn('legacyService = "com.modelpilot.api-key"', keychain_source)
        self.assertIn("service: Self.legacyService", keychain_source)
        self.assertIn("import CryptoKit", local_source)
        self.assertIn('static let referencePrefix = "local_encrypted:"', local_source)
        self.assertIn('appendingPathComponent("Application Support"', local_source)
        self.assertIn('appendingPathComponent("modeldial"', local_source)
        self.assertIn('appendingPathComponent("ModelPilot"', local_source)
        self.assertIn("fileManager.copyItem(at: legacy, to: base)", local_source)
        self.assertIn('appendingPathComponent("Secrets"', local_source)
        self.assertIn("AES.GCM.seal", local_source)
        self.assertIn("AES.GCM.open", local_source)
        self.assertIn("func bridgeSecret(", app_secret_source)
        self.assertIn('apiKeyRef.hasPrefix("keychain:")', app_secret_source)
        self.assertIn("LocalEncryptedSecretStore.referencePrefix", app_secret_source)
        self.assertIn("try? secretStore.bridgeSecret(", settings_store_source)
        self.assertIn("private let secretStore = AppSecretStore()", settings_store_source)
        self.assertIn("try secretStore.stage(apiKey, connectionID: resolvedID)", settings_store_source)
        self.assertIn("func discoverModels(connectionID: String)", bridge_source)
        self.assertIn("func testConnection(connectionID: String, modelID: String)", bridge_source)
        self.assertNotIn('"--api-key"', bridge_source)

    def test_keychain_store_reuses_secrets_from_process_memory(self) -> None:
        root = Path(__file__).resolve().parent.parent
        source = (
            root / "Sources" / "Model" / "KeychainSecretStore.swift"
        ).read_text(encoding="utf-8")

        self.assertIn("private static var processCache", source)
        self.assertIn("if let cached = Self.cachedSecret", source)
        self.assertIn("Self.cache(secret:", source)
        self.assertIn("Self.removeCachedSecret", source)

    def test_keychain_is_the_default_secret_store_and_staged_items_are_reference_scoped(self) -> None:
        root = Path(__file__).resolve().parent.parent
        app_source = (
            root / "Sources" / "Model" / "AppSecretStore.swift"
        ).read_text(encoding="utf-8")
        keychain_source = (
            root / "Sources" / "Model" / "KeychainSecretStore.swift"
        ).read_text(encoding="utf-8")

        self.assertIn("try keychain.save(secret, connectionID: connectionID)", app_source)
        self.assertIn("try keychain.delete(reference: apiKeyRef)", app_source)
        self.assertIn("func read(reference: String) throws -> String?", keychain_source)
        self.assertIn("func delete(reference: String) throws", keychain_source)
        self.assertIn(
            "query[kSecUseDataProtectionKeychain as String] = true",
            keychain_source,
        )
        self.assertIn(
            "addQuery[kSecAttrAccessible as String] =",
            keychain_source,
        )
        self.assertIn("kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly", keychain_source)

    def test_data_protection_keychain_missing_entitlement_falls_back_to_file_keychain(self) -> None:
        root = Path(__file__).resolve().parent.parent
        source = (
            root / "Sources" / "Model" / "KeychainSecretStore.swift"
        ).read_text(encoding="utf-8")

        save_scope = source[source.index("func save(") : source.index("func read(")]
        self.assertRegex(
            save_scope,
            r"catch KeychainSecretStoreError\.operationFailed\(let status\)\s+"
            r"where status == errSecMissingEntitlement",
        )
        self.assertIn("useDataProtection: false", save_scope)
        self.assertIn("private func saveSecret(", source)
        self.assertGreaterEqual(
            source.count("useDataProtection && status == errSecMissingEntitlement"),
            2,
        )

    def test_probe_success_waits_for_secret_and_config_persistence_before_reporting_success(self) -> None:
        probe_scope = self.settings_store_source[
            self.settings_store_source.index("func probeAndSaveEndpointConnection(") :
            self.settings_store_source.index("func probeEndpointModels(")
        ]
        save_scope = self.settings_store_source[
            self.settings_store_source.index("private func saveEndpointIntent(") :
            self.settings_store_source.index("private func apply(")
        ]
        editor = self.section_source("endpointConnectionSheet")

        self.assertIn('endpoint.message = "连接验证成功，正在保存…"', probe_scope)
        self.assertIn("completion: { saved in", probe_scope)
        self.assertIn('self.endpoint.message = "连接并保存成功"', probe_scope)
        self.assertIn('self.endpoint.message = "连接验证成功，但保存失败。"', probe_scope)
        self.assertIn("completion?(true)", save_scope)
        self.assertIn("completion?(false)", save_scope)
        self.assertIn("sessionStore.upsertSettingsEndpoint(intent)", save_scope)
        self.assertIn("guard success else { return }", editor)
        self.assertIn("dismissEndpointEditor()", editor)

    def test_local_encrypted_secrets_migrate_to_keychain_only_after_config_persists(self) -> None:
        root = Path(__file__).resolve().parent.parent
        app_source = (
            root / "Sources" / "Model" / "AppSecretStore.swift"
        ).read_text(encoding="utf-8")
        bridge_source = (
            root / "Sources" / "Model" / "NativeBridgeClient.swift"
        ).read_text(encoding="utf-8")

        self.assertIn("try keychain.save(secret, connectionID: connectionID)", app_source)
        self.assertIn("updatedConfigReference: migratedReference", app_source)
        self.assertIn("cleanupMigratedSecretReferences", bridge_source)
        self.assertIn("try? secretStore.deleteReference(", bridge_source)
        self.assertIn(".connectionSecretReferences(referencesByConnectionID)", bridge_source)

    def test_endpoint_editor_reuses_bound_keychain_secret_until_user_replaces_it(self) -> None:
        self.assertIn('@State private var endpointHasStoredAPIKey = false', self.source)
        self.assertIn('@State private var endpointIsReplacingAPIKey = false', self.source)
        self.assertIn('Text(L10n.tr(', self.source)
        self.assertIn('editingEndpointConnection?.secretStorageSummaryText', self.source)
        self.assertIn('Text(L10n.tr(connection.secretStorageSummaryText))', self.source)
        self.assertIn("private var endpointStoredAPIKeyDescription: String", self.source)
        self.assertIn('if connection.usesLocalEncryptedSecret {', self.source)
        self.assertIn("这是旧版本地加密存储。保存、测试或扫描时会迁移到 macOS 钥匙串；配置保存成功后才会清理旧副本。", self.source)
        self.assertIn('Button("更换 Key")', self.source)
        self.assertIn('Button("继续沿用当前 Key")', self.source)
        self.assertIn("Text(L10n.tr(endpointStoredAPIKeyDescription))", self.source)
        self.assertIn('editingEndpointConnectionID == nil ? "添加连接" : "编辑连接"', self.source)
        self.assertIn('endpointHasStoredAPIKey = connection.apiKeyRef != nil', self.source)
        self.assertIn('private var endpointRequiresAPIKey: Bool', self.source)
        self.assertIn('&& !(endpointRequiresAPIKey && endpointAPIKey.isEmpty)', self.source)

    def test_api_placeholders_are_replaced_by_connection_management(self) -> None:
        self.assertNotIn('id: "openrouter_api"', self.source)
        self.assertNotIn("入口预留", self.source)
        for copy in (
            "本机已有登录态",
            "常用提供商",
            "自定义 endpoint",
            "接入提供方",
            "推荐模型目录",
            "更多提供商",
            "手工补充模型",
            "自动发现模型",
            "OpenAI — Chat Completions",
            "OpenAI — Responses",
            "测试连接会发送一次最小真实请求，可能产生少量费用",
            "API Key 只在首次绑定或主动更换时输入一次，并保存到 macOS 钥匙串。",
        ):
            self.assertIn(copy, self.source)

    def test_endpoint_editor_presents_supported_api_formats(self) -> None:
        editor = self.section_source("endpointConnectionSheet")
        selector = self.section_source("endpointAPIFormatSelector")

        self.assertIn('endpointEditorField("API 格式")', editor)
        self.assertIn("endpointAPIFormatSelector", editor)
        for copy in (
            "OpenAI — Chat Completions",
            "POST · Base URL + /chat/completions",
            "OpenAI — Responses",
            "POST · Base URL + /responses",
            "Anthropic — Messages",
            "POST · Base URL + /messages",
            "不同网关对 Responses 的支持程度不同，连接并验证会发送最小真实请求。",
        ):
            self.assertIn(copy, selector)
        self.assertIn('apiFormat: "openai_chat_completions"', selector)
        self.assertIn('apiFormat: "openai_responses"', selector)
        self.assertIn('apiFormat: "anthropic_messages"', selector)
        self.assertNotIn("Responses（实验）", self.source)
        self.assertIn("Anthropic Messages 使用 x-api-key 与 anthropic-version", selector)

    def test_discovered_models_distinguish_new_from_configured(self) -> None:
        root = Path(__file__).resolve().parent.parent
        models_source = (root / "Sources" / "Model" / "SelectionModels.swift").read_text(
            encoding="utf-8"
        )
        store_source = (
            root / "Sources" / "Model" / "SelectionSettingsStore.swift"
        ).read_text(encoding="utf-8")

        self.assertIn("let newModels: [String]", models_source)
        self.assertIn("let configuredModels: [String]", models_source)
        endpoint_state_source = self.endpoint_state_path.read_text(encoding="utf-8")
        self.assertIn("var newlyDiscoveredModelIDs", endpoint_state_source)
        self.assertIn("var configuredDiscoveredModelIDs", endpoint_state_source)
        self.assertIn("response.newModels", store_source)
        self.assertIn("response.configuredModels", store_source)
        self.assertIn('Text("新增模型")', self.source)
        self.assertIn('Text("已配置")', self.source)
        self.assertIn('Button("加入配置")', self.source)
        self.assertIn("settings.endpoint.newlyDiscoveredModelIDs", self.source)
        self.assertIn("settings.endpoint.configuredDiscoveredModelIDs", self.source)

    def test_endpoint_editor_discovery_and_test_use_draft_connection_values(self) -> None:
        editor = self.section_source("endpointConnectionSheet")

        self.assertIn("settings.discoverEndpointDraftModels(", editor)
        self.assertIn("baseURL: endpointBaseURL", editor)
        self.assertIn("apiKey: endpointAPIKey", editor)
        self.assertIn("settings.testEndpointDraftConnection(", editor)
        self.assertIn("apiFormat: endpointAPIFormat", editor)
        self.assertIn("providerPreset: endpointPreset", editor)
        self.assertNotIn("settings.discoverModels(connectionID: connectionID)", editor)
        self.assertNotIn("settings.testConnection(connectionID: connectionID, modelID: modelID)", editor)

        store_source = self.settings_store_source
        self.assertIn("func discoverEndpointDraftModels(", store_source)
        self.assertIn("func testEndpointDraftConnection(", store_source)
        self.assertIn("resolvedEndpointProbeAPIKey(", store_source)
        self.assertIn("sessionStore.probeSettingsEndpointModels(", store_source)
        self.assertIn("apiKey: resolvedAPIKey", store_source)
        self.assertIn("sessionStore.probeSettingsEndpointConnection(", store_source)

    def test_endpoint_editor_tests_manual_model_and_clears_stale_feedback_on_change(self) -> None:
        primary_model = self.section_source("endpointPrimaryTestModelID")
        self.assertIn("endpointModelID.trimmingCharacters", primary_model)
        self.assertIn("guard manualModelID.isEmpty else { return manualModelID }", primary_model)

        editor = self.section_source("endpointConnectionSheet")
        for draft_field in (
            "endpointBaseURL",
            "endpointAPIFormat",
            "endpointAPIKey",
            "endpointModelID",
        ):
            self.assertIn(f".onChange(of: {draft_field})", editor)
        self.assertIn("settings.resetEndpointDraftFeedback()", editor)
        self.assertIn('Text(L10n.tr("测试模型：%@", modelID))', editor)
        self.assertIn("func resetEndpointDraftFeedback()", self.settings_store_source)

    def test_endpoint_draft_operations_ignore_stale_completions(self) -> None:
        store = self.settings_store_source
        endpoint_state = self.endpoint_state_path.read_text(encoding="utf-8")
        discover_start = store.index("func discoverEndpointDraftModels(")
        discover_end = store.index("func resetModelDiscovery()", discover_start)
        discover = store[discover_start:discover_end]
        test_start = store.index("func testEndpointDraftConnection(")
        test_end = store.index("func setScheduler(", test_start)
        endpoint_test = store[test_start:test_end]
        reset_start = store.index("func resetEndpointDraftFeedback()")
        reset_end = store.index("private func resolvedEndpointProbeAPIKey(", reset_start)
        reset = store[reset_start:reset_end]

        self.assertIn("draftOperationGeneration", endpoint_state)
        self.assertIn("let operationGeneration = endpoint.beginDraftOperation()", discover)
        self.assertIn("endpoint.finishDraftOperation(operationGeneration)", discover)
        self.assertIn("let operationGeneration = endpoint.beginDraftOperation()", endpoint_test)
        self.assertIn("endpoint.finishDraftOperation(operationGeneration)", endpoint_test)
        self.assertIn("endpoint.resetDraftFeedback()", reset)
        self.assertIn("invalidateDraftOperations()", endpoint_state)

    def test_discovered_models_share_endpoint_editor_scroll_context(self) -> None:
        editor = self.section_source("endpointConnectionSheet")
        discovery_results = self.section_source("discoveredModelsResult")

        self.assertIn("ScrollView(.vertical, showsIndicators: false)", editor)
        self.assertIn("LazyVStack", discovery_results)
        self.assertNotIn("ScrollView", discovery_results)
        self.assertNotIn(".frame(maxHeight: 150)", discovery_results)

    def test_scan_settings_hide_legacy_project_profile_but_preserve_config_compatibility(self) -> None:
        root = Path(__file__).resolve().parent.parent
        models_source = (root / "Sources" / "Model" / "SelectionModels.swift").read_text(
            encoding="utf-8"
        )
        store_source = (
            root / "Sources" / "Model" / "SelectionSettingsStore.swift"
        ).read_text(encoding="utf-8")

        self.assertIn("struct BridgeProjectProfile", models_source)
        self.assertIn("let projectProfile: BridgeProjectProfile", models_source)
        self.assertIn("func setProjectTaskProfile", store_source)
        self.assertIn("apply(.projectTaskProfile(", store_source)
        self.assertNotIn("projectProfileSection", self.source)
        self.assertNotIn('title: "当前项目画像"', self.source)
        self.assertNotIn('formRow("项目名称")', self.source)
        self.assertNotIn('Picker("默认视角"', self.source)
        self.assertNotIn("settings.setProjectTaskProfile", self.source)

    def test_scan_settings_expose_task_concurrency(self) -> None:
        root = Path(__file__).resolve().parent.parent
        models_source = (root / "Sources" / "Model" / "SelectionModels.swift").read_text(
            encoding="utf-8"
        )
        store_source = (
            root / "Sources" / "Model" / "SelectionSettingsStore.swift"
        ).read_text(encoding="utf-8")

        self.assertIn("let maxConcurrentTargets: Int", models_source)
        self.assertIn("executionTimeoutSeconds: Int", models_source)
        self.assertIn("timeoutRetryCount: Int", models_source)
        self.assertIn("executionTimeoutSeconds: Int,", store_source)
        self.assertIn("timeoutRetryCount: Int", store_source)
        self.assertIn("apply(.scanExecution(", store_source)

    def test_single_execution_timeout_exposes_five_to_twenty_minutes_and_defaults_to_twenty(self) -> None:
        root = Path(__file__).resolve().parent.parent
        bridge_source = (
            root / "Sources" / "Model" / "NativeBridgeClient.swift"
        ).read_text(encoding="utf-8")
        scan_scope = self.section_source("regularScanScopeSection")

        self.assertIn("@State private var executionTimeoutSeconds = 1200", self.source)
        self.assertIn("@State private var timeoutRetryCount = 0", self.source)
        self.assertIn('Picker("单次超时", selection: $executionTimeoutSeconds)', scan_scope)
        for label, seconds in (("5 分钟", 300), ("10 分钟", 600), ("15 分钟", 900), ("20 分钟", 1200)):
            self.assertIn(f'Text("{label}").tag({seconds})', scan_scope)
        self.assertNotIn('Text("3 分钟").tag(180)', scan_scope)
        self.assertNotIn('Text("7 分钟").tag(420)', scan_scope)
        self.assertNotIn("repairTimeout", bridge_source)
        self.assertIn('formRow("任务并发")', self.source)
        self.assertIn('Picker("任务并发", selection: $maxConcurrentTargets)', self.source)
        self.assertIn('formRow("单次超时")', self.source)
        self.assertIn('formRow("超时重试")', self.source)
        self.assertIn('Text("同轮题目共享任务并发上限；只要仍有待测题就尽量占满并发槽，同一模型的不同题也可并行。超时会终止当前请求；仅启用重试时重新发起，普通慢响应只记录耗时。")', self.source)
        self.assertIn("settings.setScanExecution", self.source)

    def test_endpoint_models_support_per_model_reasoning_profiles(self) -> None:
        store_source = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Model"
            / "SelectionSettingsStore.swift"
        ).read_text(encoding="utf-8")

        self.assertNotIn('for scanProfile in ["medium", "high", "xhigh"]', store_source)
        self.assertNotIn('scanProfile: "default"', store_source)
        self.assertIn("reasoningProfilesByModel: [String: [String]]", store_source)
        self.assertIn("BridgeEndpointUpsertIntent(", store_source)
        self.assertIn("BridgeEndpointModelsIntent(", store_source)
        self.assertIn("modelIDs: intendedModelIDs", store_source)
        self.assertIn("candidateEnabled: candidateEnabled", store_source)
        self.assertNotIn("endpointCandidates(", store_source)
        self.assertNotIn("modelCandidates:", store_source)
        self.assertIn("discoveredReasoningProfilesByModel[normalizedModelID]", store_source)
        self.assertIn("discoveredDefaultReasoningProfileByModel[normalizedModelID]", store_source)
        self.assertIn("response.defaultReasoningProfileByModel", store_source)
        self.assertIn("func addEndpointModel", store_source)

    def test_endpoint_verification_covers_enabled_scope_and_invalidates_on_expansion(self) -> None:
        store_source = self.settings_store_source

        self.assertIn('"model_candidates_enabled"', self.settings_patch_source)
        self.assertNotIn("expandedVerifiedScope", self.settings_patch_source)
        self.assertNotIn("endpointEnabledRequestIdentities", store_source)
        self.assertIn("for (modelID, scanProfile) in probeTargets", store_source)
        self.assertIn("scanProfile: scanProfile", store_source)

    def test_endpoint_key_update_is_staged_until_config_save_succeeds(self) -> None:
        store_source = self.settings_store_source
        secret_source = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Model"
            / "AppSecretStore.swift"
        ).read_text(encoding="utf-8")

        self.assertIn("func stage(_ secret: String", secret_source)
        self.assertIn("secretStore.stage(apiKey", store_source)
        self.assertIn("saveEndpointIntent(", store_source)
        self.assertIn("if let stagedApiKeyRef", store_source)
        self.assertIn("previousApiKeyRef != stagedApiKeyRef", store_source)
        self.assertRegex(store_source, r"deleteReference\(\s+previousApiKeyRef")
        self.assertRegex(store_source, r"deleteReference\(\s+stagedApiKeyRef")
        self.assertIn("func deleteReference(_ apiKeyRef: String", secret_source)

    def test_api_connection_groups_models_by_family_and_keeps_variants_testable(self) -> None:
        connection_card = self.section_source("endpointConnectionCard")
        self.assertIn("modelFamilyGroups(for: connection)", connection_card)
        self.assertIn("apiModelVariantName", self.source)
        self.assertIn('Text(L10n.tr(isTesting ? "测试中" : "测试连接"))', self.source)
        self.assertNotIn('Text("测试一次")', self.source)
        self.assertIn("settings.testConnection(", self.source)
        self.assertIn("modelID: candidate.modelId", self.source)

    def test_api_connection_test_shows_loading_and_inline_feedback(self) -> None:
        endpoint_state_source = self.endpoint_state_path.read_text(encoding="utf-8")

        self.assertIn("ProgressView()", self.source)
        self.assertIn("settings.endpoint.isTesting(", self.source)
        self.assertIn("settings.endpoint.feedback(", self.source)
        self.assertIn("EndpointTestFeedback", endpoint_state_source)
        self.assertIn("testingConnectionID", endpoint_state_source)
        self.assertIn("testingModelID", endpoint_state_source)
        self.assertIn("response.ok", self.settings_store_source)

    def test_endpoint_editor_scopes_connection_test_feedback_to_active_modal(self) -> None:
        editor = self.section_source("endpointConnectionSheet")
        model_row = self.section_source("apiModelVariantRow")
        feedback = self.section_source("endpointEditorTestFeedback")
        testing = self.section_source("endpointEditorIsTestingConnection")

        self.assertIn("endpointEditorTestFeedback", editor)
        self.assertIn("Text(L10n.tr(feedback.message))", editor)
        self.assertIn("endpointEditorIsTestingConnection", editor)
        self.assertIn("settings.endpoint.feedback(", feedback)
        self.assertIn("settings.endpoint.isTesting(", testing)
        self.assertIn("isEndpointEditorOpen(for: connection.id)", model_row)

    def test_model_ingress_uses_one_dynamic_source_workspace(self) -> None:
        for token in (
            "typealias IngressWorkspaceItem = SettingsIngressPresenter.WorkspaceItem",
            "@State private var selectedIngressConnectionID",
            "ingressSourceGrid",
            "ingressWorkspaceCardList",
            "sourceWorkspaceCard",
            "selectedIngressDetail",
        ):
            self.assertIn(token, self.source)
        self.assertNotIn("localIngressDefinitions", self.source)
        self.assertNotIn("apiConnectionsSection", self.source)

    def test_api_sources_always_render_model_clusters_before_effort_profiles(self) -> None:
        connection_card = self.section_source("endpointConnectionCard")
        self.assertNotIn("family.candidates.count == 1", connection_card)
        self.assertIn("apiModelFamilyCard(family, connection: connection)", connection_card)
        self.assertIn('Text("模型与档位")', self.source)

    def test_anthropic_endpoint_explains_native_effort_profiles(self) -> None:
        card = self.section_source("endpointConnectionCard")
        variant_name = self.section_source("apiModelVariantName")

        self.assertIn('connection.apiFormat == "anthropic_messages"', card)
        self.assertIn("low、medium、high、xhigh、max", card)
        self.assertIn("Anthropic adaptive thinking", card)
        self.assertIn(
            "settingsCandidatePresentation(for: candidate).variantName",
            variant_name,
        )
        self.assertNotIn("candidate.scanProfile", variant_name)
        self.assertIn('case "anthropic_messages": return "Anthropic Messages"', self.source)

    def test_kimi_k3_endpoint_explains_native_effort_profiles(self) -> None:
        card = self.section_source("endpointConnectionCard")

        self.assertIn('connection.providerId == "moonshot"', card)
        self.assertIn('candidate.modelId == "k3"', card)
        self.assertIn("low、high、max", card)
        self.assertIn("reasoning_effort", card)

    def test_endpoint_editor_uses_provider_selection_with_custom_fallback(self) -> None:
        editor = self.section_source("endpointConnectionSheet")
        provider_menu = self.section_source("endpointProviderMenu")

        self.assertIn('endpointEditorField("接入提供方")', editor)
        self.assertIn("endpointProviderMenu", editor)
        self.assertIn("Menu", provider_menu)
        self.assertIn("ForEach(endpointProviderOptions)", provider_menu)
        self.assertIn("endpointProvider = provider.id", provider_menu)
        self.assertIn("applyEndpointProviderDefaults(provider.id)", provider_menu)
        self.assertIn("IslandVisual.controlFill", provider_menu)
        self.assertIn("IslandVisual.selectedBorder", provider_menu)
        self.assertNotIn("Picker", provider_menu)
        self.assertIn('private let customEndpointProviderID = "custom"', self.source)
        self.assertIn('if endpointProvider == customEndpointProviderID', editor)
        self.assertIn("endpointProviderCatalogSection", editor)
        self.assertNotIn('endpointEditorField("连接名称")', editor)

    def test_endpoint_api_format_uses_interaction_selection_semantics(self) -> None:
        option = self.section_source("endpointAPIFormatOption")

        self.assertIn("isSelected ? IslandColor.interaction : IslandVisual.hintText", option)
        self.assertIn("IslandVisual.selectedSurface", option)
        self.assertIn("IslandVisual.selectedBorder", option)
        self.assertNotIn("IslandColor.endpoint", option)

    def test_provider_onboarding_defaults_to_one_step_connection(self) -> None:
        editor = self.section_source("endpointConnectionSheet")
        defaults = self.section_source("applyEndpointProviderDefaults")

        self.assertIn('Button("连接并验证")', editor)
        self.assertIn("settings.probeAndSaveEndpointConnection", editor)
        self.assertIn("DisclosureGroup", editor)
        self.assertIn('Text("获取 API Key")', editor)
        self.assertIn("catalogProvider.defaultModelIds", defaults)
        self.assertIn('Button("发现可用模型")', editor)
        self.assertIn("settings.probeEndpointModels", editor)
        self.assertNotIn('endpointEditorField("连接协议")', editor.split("DisclosureGroup", 1)[0])

    def test_local_ingress_uses_detection_cards_instead_of_config_only_filter(self) -> None:
        section = self.section_source("localIngressSection")

        self.assertIn("detectedLocalProviders", section)
        self.assertIn("localProviderDetectionCard", section)
        self.assertIn("settings.importLocalProvider", self.source)
        self.assertIn('Text("适配器待接入")', self.source)

    def test_api_model_family_grouping_uses_settings_projection_identity(self) -> None:
        grouping = self.section_source("modelFamilyGroups")
        connection_card = self.section_source("endpointConnectionCard")
        family_header = self.section_source("modelFamilyHeader")
        provider = self.section_source("endpointProviderID")
        store_source = (
            Path(__file__).resolve().parent.parent / "Sources" / "Model" / "SelectionSettingsStore.swift"
        ).read_text(encoding="utf-8")

        self.assertIn("settingsIngressPresentation.modelFamilyGroups", grouping)
        self.assertIn("candidateProjectionsByID", self.settings_ingress_presenter_source)
        self.assertIn("($0.candidateId, $0)", self.settings_ingress_presenter_source)
        self.assertIn("settingsIngressPresentation.endpointProviderID", provider)
        self.assertIn("SettingsCandidatePresenter.providerID(", self.settings_ingress_presenter_source)
        self.assertIn("fallbackProviderID: connection.providerId", self.settings_ingress_presenter_source)
        for removed_rule in (
            "APIReasoningAliasIdentity",
            "CandidateDisplayIdentity",
            "reasoningProfileIDs",
            "apiReasoningAliasIdentities",
            "candidateDisplayIdentity",
            "modelFamilyDisplayName",
        ):
            self.assertNotIn(removed_rule, self.source)
        self.assertIn("apiModelFamilyCard", connection_card)
        self.assertIn("Text(family.displayModel)", family_header)
        family_card = self.section_source("apiModelFamilyCard")
        self.assertIn('itemLabel: "档位"', family_card)
        self.assertIn("modelFamilyHeader(", family_card)
        self.assertIn("apiModelVariantRow", family_card)
        self.assertIn("func setModelCandidatesEnabled", store_source)
        self.assertIn("apply(.modelCandidatesEnabled(", store_source)

    def test_api_connection_model_cluster_count_uses_rendered_families(self) -> None:
        connection_card = self.section_source("endpointConnectionCard")

        self.assertIn('value: "\\(modelFamilyGroups(for: connection).count)"', connection_card)
        self.assertIn('label: "模型簇"', connection_card)

    def test_endpoint_editor_allows_reasoning_profiles_per_model_cluster(self) -> None:
        editor = self.section_source("endpointConnectionSheet")
        opener = self.section_source("openEndpointEditor")
        store_source = (
            Path(__file__).resolve().parent.parent / "Sources" / "Model" / "SelectionSettingsStore.swift"
        ).read_text(encoding="utf-8")

        self.assertIn("endpointReasoningProfileDrafts", self.source)
        self.assertIn("endpointReasoningProfileEditor", editor)
        profile_editor = self.section_source("endpointReasoningProfileEditor")
        self.assertIn('endpointEditorField("模型思考深度")', profile_editor)
        self.assertIn("endpointReasoningProfileEditor", editor)
        self.assertIn("reasoningProfilesByModel: endpointReasoningProfilesByModel", editor)
        self.assertIn("settings.endpoint.discoveredReasoningProfilesByModel", editor)
        self.assertIn("profiles.joined(separator: \", \")", editor)
        self.assertIn("endpointReasoningProfileDrafts", opener)
        self.assertNotIn("func replaceEndpointReasoningProfiles", store_source)
        self.assertIn("reasoningProfilesByModel: reasoningProfilesByModel", store_source)

    def test_settings_sidebar_groups_evaluation_app_and_data_privacy(self) -> None:
        self.assertIn("@State private var activeTab: SettingsTab = .scan", self.source)
        for token in (
            'case .scan: return "扫描设置"',
            'case .targets: return "模型接入"',
            'case .automation: return "扫描计划"',
            'case .general: return "通用"',
            'case .updates: return "软件更新"',
            "SettingsSection.allCases",
            "settingsSidebarButton(tab)",
            "settingsPageHeader",
        ):
            self.assertIn(token, self.source)

        sidebar = self.section_source("sidebar")
        self.assertIn('case .evaluation: return "评测"', self.source)
        self.assertIn('case .application: return "应用"', self.source)
        self.assertIn('case .dataPrivacy: return "数据与隐私"', self.source)
        self.assertNotIn('case .system: return "系统"', self.source)
        self.assertIn(
            "case .targets, .scan, .automation: return .evaluation",
            self.source,
        )
        self.assertIn("case .general, .updates: return .application", self.source)
        self.assertIn("case .health: return .dataPrivacy", self.source)
        tabs = self.source.split("private enum SettingsTab: String, CaseIterable, Identifiable {", 1)[1].split("var id: String", 1)[0]
        self.assertLess(tabs.index("case targets"), tabs.index("case scan"))
        self.assertIn("ForEach(section.tabs)", sidebar)
        self.assertIn("settingsSidebarButton(tab)", sidebar)
        button = self.section_source("settingsSidebarButton")
        self.assertIn("Image(systemName: tab.icon)", button)
        self.assertIn("IslandVisual.surfaceStrong", button)
        self.assertIn("IslandVisual.selectedBorder", button)
        self.assertNotIn("NavigationSplitView", self.source)

        scan = self.section_source("scanContent")
        automation = self.section_source("automationContent")
        general = self.section_source("generalContent")
        updates = self.section_source("softwareUpdateContent")
        self.assertIn("regularScanScopeSection", scan)
        self.assertNotIn("projectProfileSection", scan)
        self.assertNotIn("scanBudgetSection", scan)
        self.assertNotIn("compactPillPreviewSection", scan)
        self.assertNotIn("schedulerSection", scan)
        self.assertNotIn("displaySection", scan)
        self.assertIn("schedulerSection", automation)
        self.assertNotIn("applicationPreferencesSection", automation)
        self.assertNotIn("scanBudgetSection", automation)
        self.assertIn("applicationPreferencesSection", general)
        self.assertNotIn("softwareUpdateSection", general)
        self.assertIn("displaySection", general)
        self.assertNotIn("schedulerSection", general)
        self.assertIn("softwareUpdateSection", updates)
        self.assertIn("guard tab == .updates", updates)

    def test_data_health_is_a_secondary_read_only_settings_entry(self) -> None:
        for token in (
            'case .health: return "数据健康"',
            'case .health: return "stethoscope"',
            "case .health: return .dataPrivacy",
            "case .health:",
            "dataHealthContent",
        ):
            self.assertIn(token, self.source)

        health = self.section_source("dataHealthContent")
        self.assertIn("selectionStore.snapshot?.diagnostics", health)
        self.assertIn("diagnosticOverviewSection", health)
        self.assertIn("diagnosticHistorySection", health)
        self.assertIn("diagnosticVersionsSection", health)
        self.assertIn("copyDiagnosticSummary", health)
        self.assertNotIn("startScan", health)
        self.assertNotIn("save", health)

        scheduler = self.section_source("schedulerSection")
        self.assertIn('title: "扫描计划"', scheduler)
        self.assertIn("定时扫描仅在 modeldial 运行期间生效", self.source)

    def test_scan_settings_hides_soft_budget_but_preserves_config_compatibility(self) -> None:
        root = Path(__file__).resolve().parent.parent
        models_source = (root / "Sources" / "Model" / "SelectionModels.swift").read_text(encoding="utf-8")
        settings_store_source = (root / "Sources" / "Model" / "SelectionSettingsStore.swift").read_text(encoding="utf-8")
        self.assertIn("struct BridgeScanBudgetConfig", models_source)
        self.assertIn("let scanBudget: BridgeScanBudgetConfig", models_source)
        self.assertNotIn("scanBudgetSection", self.source)
        self.assertNotIn('title: "扫描软预算"', self.source)
        self.assertIn("apply(.scanBudget(", settings_store_source)
        self.assertIn('"scan_budget"', self.settings_patch_source)
        self.assertNotIn("scanBudget.toPayload()", settings_store_source)
        self.assertIn("setScanBudget", settings_store_source)

    def test_scan_settings_does_not_render_a_read_only_compact_pill_preview(self) -> None:
        root = Path(__file__).resolve().parent.parent
        root_source = (
            root / "Sources" / "Views" / "IslandRootView.swift"
        ).read_text(encoding="utf-8")
        compact_source = (
            root / "Sources" / "Views" / "CompactPillView.swift"
        ).read_text(encoding="utf-8")
        scan = self.section_source("scanContent")
        self.assertNotIn("compactPillPreviewSection", scan)
        self.assertNotIn("CompactPillView(", self.source)
        self.assertIn("CompactPillView(", root_source)
        self.assertIn("let presentation: GlancePresentation", compact_source)

    def test_scan_settings_auto_save_replaces_local_commit_buttons(self) -> None:
        scope = self.section_source("regularScanScopeSection")

        for token in (
            'Button("保存预算")',
            'Button("保存画像")',
            'Button("保存执行设置")',
        ):
            self.assertNotIn(token, self.source)

        self.assertIn("执行参数修改后自动保存", scope)
        for token in (
            ".onChange(of: maxConcurrentTargets)",
            ".onChange(of: executionTimeoutSeconds)",
            ".onChange(of: timeoutRetryCount)",
            "persistScanExecutionIfNeeded()",
        ):
            self.assertIn(token, self.source)
        for token in (
            ".onChange(of: projectProfileName)",
            ".onChange(of: projectTaskMode)",
            "persistProjectProfileIfNeeded()",
        ):
            self.assertNotIn(token, self.source)

    def test_settings_auto_save_surfaces_serial_feedback(self) -> None:
        self.assertIn("@State private var settingsHydrationDepth = 0", self.source)
        self.assertIn("private func withFieldHydration", self.source)
        self.assertIn("guard !isHydratingFields else { return }", self.source)
        self.assertIn("settings.saveFeedbackState != .idle", self.source)
        self.assertIn('Text("保存中")', self.source)
        self.assertIn('Text("已保存")', self.source)
        self.assertIn('Text("保存失败")', self.source)

    def test_scan_plan_and_general_split_scheduler_from_app_preferences(self) -> None:
        root = Path(__file__).resolve().parent.parent
        models_source = (root / "Sources" / "Model" / "SelectionModels.swift").read_text(encoding="utf-8")
        store_source = (root / "Sources" / "Model" / "SelectionStore.swift").read_text(encoding="utf-8")
        launch_path = root / "Sources" / "Model" / "LaunchAtLoginStore.swift"
        scheduler = self.section_source("schedulerSection")
        preferences = self.section_source("applicationPreferencesSection")

        self.assertIn("let enabled: Bool", models_source)
        self.assertIn('formRow("自动扫描")', scheduler)
        self.assertIn("schedulerEnabledBinding", scheduler)
        self.assertIn(".disabled(!schedulerEnabled)", scheduler)
        self.assertNotIn('Text("手动").tag("manual")', scheduler)
        self.assertIn("selectionStore.nextScheduledRun", self.source)
        self.assertIn("absoluteText", self.source)
        self.assertIn("relativeText", self.source)
        self.assertIn("candidateCount", self.source)
        self.assertIn("questionCount", self.source)
        self.assertIn("func nextScheduledRun(now: Date = Date())", store_source)
        self.assertIn("private var scheduledScanFireDate: Date?", store_source)
        self.assertIn("private var scheduledScanFingerprint: String?", store_source)
        self.assertIn("scheduledScanTimer?.isValid == true", store_source)
        self.assertIn("scheduledScanFireDate = nil", store_source)
        self.assertTrue(launch_path.exists())
        launch_source = launch_path.read_text(encoding="utf-8")
        self.assertIn("SMAppService.mainApp", launch_source)
        self.assertNotIn('formRow("登录时启动")', scheduler)
        self.assertNotIn('formRow("本地通知")', scheduler)
        self.assertIn('formRow("登录时启动")', preferences)
        self.assertIn('formRow("本地通知")', preferences)
        self.assertIn("launchAtLoginBinding", preferences)
        self.assertIn("notificationEngine.permissionStatusText", preferences)

    def test_round_scan_actions_live_in_scan_settings_scope_section_only(self) -> None:
        scan = self.section_source("scanContent")
        automation = self.section_source("automationContent")
        scope = self.section_source("regularScanScopeSection")
        ingress = self.section_source("targetsContent")

        self.assertIn("regularScanScopeSection", scan)
        self.assertNotIn("regularScanScopeSection", automation)
        self.assertIn("Text(regularScanButtonTitle)", scope)
        self.assertIn(
            "selectionStore.startRegularScan(conflictPresentation: .settings)",
            scope,
        )
        self.assertIn('Text("自定义本轮")', scope)
        self.assertIn("initializeCustomCandidateIDs()", scope)
        self.assertIn("showsCustomScanSheet = true", scope)
        self.assertNotIn('Text("常规扫描")', ingress)
        self.assertNotIn('Text("自定义本轮")', ingress)
        self.assertNotIn("整轮扫描在扫描设置中发起", ingress)

    def test_scan_scope_and_ingress_use_consistent_hierarchical_counts(self) -> None:
        for token in (
            "regularScanScopeMetrics",
            "enabledIngressCandidateCount",
            "settingsIngressPresentation",
        ):
            self.assertIn(token, self.source)
        for token in (
            "sourceCount:",
            "modelEntryCount:",
            "totalCandidateCount:",
            'label: L10n.tr("已启用来源")',
            'label: L10n.tr("本轮模型")',
            'label: L10n.tr("扫描档位")',
        ):
            self.assertIn(token, self.settings_ingress_presenter_source)

        source_card = self.section_source("sourceWorkspaceCard")
        self.assertIn('L10n.tr("%d 个模型簇，%d/%d 已启用"', source_card)
        self.assertIn('L10n.tr("%d 个模型，%d/%d 已启用"', source_card)
        self.assertIn("已启用", self.section_source("sourceWorkspaceCard"))
        self.assertNotIn("已开启", self.section_source("sourceWorkspaceCard"))
        local_detail = self.section_source("localConnectionDetailCard")
        endpoint_detail = self.section_source("endpointConnectionCard")
        self.assertIn('label: "模型条目"', local_detail)
        self.assertIn('label: "模型簇"', endpoint_detail)
        self.assertIn('label: "扫描档位"', local_detail)
        self.assertIn('label: "扫描档位"', endpoint_detail)
        self.assertNotIn('label: "纳入扫描"', local_detail)
        self.assertNotIn('label: "纳入扫描"', endpoint_detail)

    def test_scan_scope_counts_only_effectively_enabled_unique_sources_and_models(self) -> None:
        scope_metrics = self.section_source("regularScanScopeMetrics")

        self.assertIn("settingsIngressPresentation.regularScanScopeMetrics", scope_metrics)
        self.assertIn("let enabledSourceCount = scanScope?.sourceCount ?? 0", self.settings_ingress_presenter_source)
        self.assertIn("let enabledModelEntryCount = scanScope?.modelCount ?? 0", self.settings_ingress_presenter_source)
        self.assertIn("let enabledCandidateCount = scanScope?.candidateCount ?? 0", self.settings_ingress_presenter_source)

    def test_settings_tabs_restore_scroll_to_top_and_show_scroll_affordance(self) -> None:
        for section_name in (
            "scanContent",
            "automationContent",
            "generalContent",
        ):
            self.assertIn("ScrollViewReader", self.section_source(section_name))
        self.assertNotIn("ScrollViewReader", self.section_source("targetsContent"))
        self.assertIn("settingsScrollTopID", self.source)
        self.assertIn("showsIndicators: true", self.source)
        self.assertIn("scrollTo(settingsScrollTopID", self.source)

    def test_model_ingress_keeps_toolbar_fixed_and_scrolls_source_and_detail_independently(self) -> None:
        targets = self.section_source("targetsContent")
        workspace = self.section_source("ingressWorkspaceSection")
        source_rail = self.section_source("ingressSourceRail")
        detail_pane = self.section_source("ingressDetailPane")
        header = self.section_source("settingsPageHeader")

        self.assertIn("enabledCandidatesButton", header)
        self.assertNotIn("ingressSummarySection", targets)
        self.assertIn("ingressWorkspaceSection", targets)
        self.assertNotIn("ScrollView(.vertical", targets)
        self.assertIn("ingressSourceRail", workspace)
        self.assertIn("ingressDetailPane", workspace)
        for section in (source_rail, detail_pane):
            self.assertIn("ScrollView(.vertical, showsIndicators: true)", section)
            self.assertIn("maxHeight: .infinity", section)
        self.assertIn("ScrollViewReader", detail_pane)
        self.assertIn("ingressDetailTopID", detail_pane)
        self.assertIn("selectedIngressItem?.id", detail_pane)

    def test_settings_use_one_custom_header_and_sidebar_chrome(self) -> None:
        self.assertNotIn(".navigationTitle", self.source)
        self.assertNotIn("NavigationSplitView", self.source)
        header = self.section_source("settingsPageHeader")
        self.assertIn("Text(LocalizedStringKey(activeTab.title))", header)
        self.assertIn("Typography.sectionTitle", header)
        self.assertIn("IslandVisual.workspaceSurface", header)
        self.assertIn("WindowDragArea()", header)
        for section_name in ("scanContent", "targetsContent", "automationContent", "generalContent"):
            section = self.section_source(section_name)
            self.assertNotIn("Typography.pageTitle", section)
            if section_name != "targetsContent":
                self.assertNotIn("VStack(spacing: 0)", section)

    def test_settings_window_copies_reference_swiftui_window_scene_structure(self) -> None:
        root = Path(__file__).resolve().parent.parent
        app_source = (root / "Sources" / "App.swift").read_text(encoding="utf-8")
        window_source = (
            root / "Sources" / "Window" / "SettingsWindowController.swift"
        ).read_text(encoding="utf-8")

        self.assertIn('Window("modeldial Settings", id: "settings")', app_source)
        self.assertIn("SettingsWindowContent()", app_source)
        self.assertIn(".defaultSize(width: 1160, height: 560)", app_source)
        self.assertIn(r"@Environment(\.openWindow) private var openWindow", app_source)
        self.assertIn('openWindow(id: "settings")', app_source)
        self.assertIn("registerOpenWindow", window_source)
        self.assertIn("openWindowAction?()", window_source)
        self.assertNotIn("NSWindow(", window_source)
        self.assertNotIn(".fullSizeContentView", window_source)

    def test_settings_window_can_route_directly_to_model_ingress(self) -> None:
        root = Path(__file__).resolve().parent.parent
        window_source = (
            root / "Sources" / "Window" / "SettingsWindowController.swift"
        ).read_text(encoding="utf-8")
        route_source = self.section_source("applySettingsDestination")

        self.assertIn("enum SettingsDestination", window_source)
        self.assertIn("case modelIngress", window_source)
        self.assertIn("@Published private(set) var destinationRequest", window_source)
        self.assertIn("func show(destination: SettingsDestination? = nil)", window_source)
        self.assertIn("func consumeDestination", window_source)
        self.assertIn("SettingsWindowController.shared", self.source)
        self.assertIn("destinationRequest", self.source)
        self.assertIn("activeTab = .targets", route_source)
        self.assertIn("consumeDestination(destination)", route_source)

    def test_ingress_uses_source_detail_split_to_keep_core_actions_above_fold(self) -> None:
        workspace = self.section_source("ingressWorkspaceSection")
        targets = self.section_source("targetsContent")
        self.assertIn("ingressWorkspaceSection", targets)
        self.assertIn("ingressSourceRail", workspace)
        self.assertIn("ingressDetailPane", workspace)
        self.assertIn("HStack(alignment: .top", workspace)
        self.assertNotIn("ingressSourceGrid\n                    selectedIngressDetail", targets)

    def test_ingress_top_keeps_only_enabled_configuration_control(self) -> None:
        targets = self.section_source("targetsContent")
        button = self.section_source("enabledCandidatesButton")
        header = self.section_source("settingsPageHeader")

        self.assertIn("enabledCandidatesButton", header)
        self.assertIn("activeTab == .targets", header)
        self.assertIn(".padding(.top, LayoutRhythm.section)", targets)
        self.assertIn(".padding(.horizontal, Layout.contentPadding)", targets)
        self.assertIn('Text("已启用档位")', button)
        self.assertIn("enabledIngressCandidateCount", button)
        self.assertIn("chevron.down", button)
        self.assertNotIn("ingressSummarySection", self.source)
        self.assertNotIn("ingressSummaryMetrics", self.source)
        self.assertNotIn("currentModelSelectionSection", targets)
        self.assertNotIn("currentModelSelectionSection", self.source)

    def test_ingress_workspace_uses_wide_canvas_and_readable_master_column(self) -> None:
        targets = self.section_source("targetsContent")
        workspace = self.section_source("ingressWorkspaceSection")
        grid = self.section_source("ingressSourceGrid")

        self.assertIn("Layout.ingressContentMaxWidth", targets)
        self.assertIn("Layout.sourceListWidth", workspace)
        self.assertNotIn("ingressDetailMinWidth", self.source)
        self.assertIn("maxWidth: .infinity", workspace)
        self.assertIn('sectionTitle("接入来源")', grid)
        self.assertIn("localIngressSection", grid)
        self.assertIn("commonProviderCatalogSection", grid)
        self.assertIn("customEndpointSection", grid)
        self.assertIn("优先复用本机登录态，再按需接入 API 或自定义服务", grid)
        self.assertIn(
            ".frame(minWidth: 980, idealWidth: 1160, minHeight: 480, idealHeight: 560)",
            self.source,
        )

    def test_ingress_source_rail_uses_compact_rows_and_semantic_status_colors(self) -> None:
        catalog = self.section_source("commonProviderCatalogSection")
        row = self.section_source("providerCatalogRow")
        local_row = self.section_source("localProviderDetectionCard")
        local_row_content = self.section_source("localProviderDetectionRow")
        custom = self.section_source("customEndpointSection")
        configured_row = self.section_source("sourceWorkspaceCard")

        self.assertIn("connectableFeaturedProviderCatalog", catalog)
        self.assertIn("VStack(spacing: 0)", catalog)
        self.assertIn("providerCatalogRow(provider)", catalog)
        self.assertNotIn("LazyVGrid", catalog)
        self.assertNotIn("GridItem", catalog)

        self.assertIn(".lineLimit(1)", row)
        self.assertIn(".truncationMode(.tail)", row)
        self.assertIn(".lineLimit(2)", row)
        self.assertIn(".fixedSize(horizontal: false, vertical: true)", row)
        self.assertIn('Text(L10n.tr("已接入 %d", connectionCount))', row)
        self.assertIn("IslandColor.liveTeal", row)
        self.assertNotIn("ingressMetaPill", row)
        self.assertNotIn(".opacity(provider.connectionSupported", row)

        self.assertIn("localProviderDetectionRow", local_row)
        self.assertIn("localProviderStatusText", local_row_content)
        self.assertIn(".lineLimit(1)", local_row_content)
        self.assertIn("IslandColor.endpoint", custom)
        self.assertNotIn("IslandColor.alertAmber", custom)
        self.assertIn("Text(inventoryText)", configured_row)
        self.assertIn('L10n.tr("%d 个模型簇，%d/%d 已启用"', configured_row)
        self.assertIn('L10n.tr("%d 个模型，%d/%d 已启用"', configured_row)
        self.assertIn('item.source.mode == "api"', configured_row)
        self.assertIn('modelFamilyGroups(for: item.connection).count', configured_row)
        self.assertIn(
            "connectableFeaturedProviders: featuredProviders.filter",
            self.settings_ingress_presenter_source,
        )

    def test_xai_uses_the_normal_endpoint_provider_accent(self) -> None:
        accent = self.section_source("providerAccent")

        self.assertIn('"xai"', accent)
        self.assertIn("IslandColor.endpoint", accent)

    def test_grok_build_cli_detection_does_not_claim_login_before_import_probe(self) -> None:
        status = self.section_source("localProviderStatusText")

        self.assertIn('provider.status == "login_check_required"', status)
        self.assertIn("return L10n.tr(provider.statusMessage)", status)

    def test_grok_build_import_focuses_its_local_model_detail(self) -> None:
        local_row = self.section_source("localProviderDetectionCard")
        import_start = self.settings_store_source.index("func importLocalProvider(")
        import_end = self.settings_store_source.index("func discoverLocalModels(")
        local_import = self.settings_store_source[import_start:import_end]

        self.assertIn("localProviderDetectionRow", local_row)
        self.assertIn(".contentShape(Rectangle())", local_row)
        self.assertIn("selectedIngressConnectionID = provider.connectionId", local_row)
        self.assertIn("response.ok", local_import)
        self.assertIn("response.connectionId", local_import)
        self.assertIn("localImportSelectionID", local_import)
        self.assertIn("localImportSelectionID = response.connectionId", local_import)
        self.assertIn("guard importingLocalProviderID == nil else { return }", local_import)
        self.assertIn("importingLocalProviderID = nil", local_import)
        self.assertIn("localImportSucceeded = response.ok", local_import)
        self.assertIn(".onChange(of: localImportSelectionID", self.source)
        self.assertIn("onLocalImportSelectionChange", self.source)
        self.assertIn("selectedIngressConnectionID = connectionID", self.source)

    def test_imported_local_provider_card_keeps_enablement_in_the_detail_pane(self) -> None:
        local_row = self.section_source("localProviderDetectionCard")
        local_row_content = self.section_source("localProviderDetectionRow")
        local_section = self.section_source("localIngressSection")
        status = self.section_source("localProviderStatusText")
        import_action = local_row_content.split("} else if provider.importable {", 1)[1].split(
            "} else {", 1
        )[0]

        self.assertNotIn("管理并启用", local_row_content)
        self.assertNotIn('Button("管理")', local_row_content)
        self.assertIn(".layoutPriority(1)", local_row_content)
        self.assertIn("L10n.tr(", import_action)
        self.assertIn('"验证中…"', import_action)
        self.assertIn('"导入"', import_action)
        self.assertIn("settings.importingLocalProviderID != nil", import_action)
        self.assertIn('Text("正在验证本机登录态…")', local_row_content)
        self.assertIn("settings.localImportFeedbackProviderID == provider.providerId", local_row_content)
        self.assertNotIn("settings.localImportMessage", local_section)
        self.assertNotIn('Button("一键导入")', import_action)
        self.assertIn('Image(systemName: "chevron.right")', local_row_content)
        self.assertIn(".fixedSize(horizontal: true, vertical: false)", import_action)
        self.assertIn('if isImported { return L10n.tr("已接入") }', status)
        self.assertNotIn("选择模型以启用扫描", status)

    def test_typed_config_patch_behavior_is_executable(self) -> None:
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "settings-config-patch-tests"
            compile_result = subprocess.run(
                [
                    "swiftc",
                    "-module-cache-path",
                    str(Path(temp_dir) / "module-cache"),
                    "Sources/Model/SettingsConfigPatch.swift",
                    "tests/swift/SettingsConfigPatchTests.swift",
                    "-o",
                    str(executable),
                ],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            run_result = subprocess.run(
                [str(executable)],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(run_result.returncode, 0, run_result.stderr)
            self.assertIn("SettingsConfigPatch tests passed", run_result.stdout)

    def test_settings_surface_save_and_launch_at_login_errors_globally(self) -> None:
        layout = self.section_source("settingsLayout")
        message = self.section_source("settingsPageErrorMessage")
        banner = self.section_source("settingsErrorBanner")

        self.assertIn("settingsPageErrorMessage", layout)
        self.assertIn("settingsErrorBanner", layout)
        self.assertIn("settings.errorMessage", message)
        self.assertIn("launchAtLoginStore.errorMessage", message)
        self.assertIn("IslandColor.alertRed", banner)

    def test_grok_build_detail_does_not_offer_codex_model_discovery(self) -> None:
        detail = self.section_source("localConnectionDetailCard")
        single_variant = self.section_source("singleVariantRow")

        self.assertIn('if item.source.kind == "codex"', detail)
        self.assertIn('Text("纳入扫描")', single_variant)
        self.assertIn("settings.setModelCandidateEnabled", single_variant)

    def test_grok_build_profiles_are_explicit_and_independently_selectable(self) -> None:
        detail = self.section_source("localConnectionDetailCard")
        profile_row = self.section_source("candidateRow")

        self.assertIn('item.source.kind == "grok_build"', detail)
        self.assertIn("low、medium、high", detail)
        self.assertIn("settings.setModelCandidateEnabled", profile_row)

    def test_claude_code_requires_import_before_model_selection_and_labels_reasoning_limit(self) -> None:
        detail = self.section_source("localConnectionDetailCard")
        header = self.section_source("ingressConnectionHeader")

        self.assertIn("isLocalConnectionImported(item)", detail)
        self.assertIn("本机登录态验证", detail)
        self.assertIn('item.source.kind == "claude_code"', detail)
        self.assertIn("不会参与 Reason Tok 推荐", detail)
        self.assertIn("isLocalConnectionImported(item)", header)

    def test_api_provider_catalog_is_explicitly_labeled(self) -> None:
        catalog = self.section_source("commonProviderCatalogSection")

        self.assertIn('Text("常用 API 提供商")', catalog)

    def test_ingress_tab_does_not_change_sidebar_geometry(self) -> None:
        sidebar = self.section_source("sidebar")
        workspace = self.section_source("ingressWorkspaceSection")

        self.assertIn("Layout.sidebarWidth", self.source)
        self.assertIn("SettingsSection.allCases", sidebar)
        self.assertIn("IslandColor.canvas", sidebar)
        self.assertNotIn("Layout.ingressDetailMinWidth", workspace)
        self.assertNotIn("ViewThatFits(in: .horizontal)", workspace)
        self.assertIn("HStack(alignment: .top, spacing: 0)", workspace)

    def test_ingress_workspace_keeps_source_rail_visible_at_supported_widths(self) -> None:
        workspace = self.section_source("ingressWorkspaceSection")

        self.assertNotIn("ViewThatFits(in: .horizontal)", workspace)
        self.assertNotIn("compactIngressSourceSelector", workspace)
        self.assertIn("ingressSourceRail", workspace)
        self.assertIn("Layout.sourceListWidth", workspace)
        self.assertIn("Layout.ingressReadableDetailWidth", workspace)
        self.assertIn("ingressDetailPane", workspace)

    def test_ingress_readiness_steps_do_not_wrap_vertically(self) -> None:
        track = self.section_source("ingressReadinessTrack")

        self.assertIn(".lineLimit(1)", track)
        self.assertIn(".fixedSize(horizontal: true, vertical: false)", track)

    def test_configured_ingress_source_rows_use_full_width_hit_targets(self) -> None:
        configured_row = self.section_source("sourceWorkspaceCard")

        self.assertIn(".contentShape(Rectangle())", configured_row)

    def test_ingress_readiness_places_track_and_action_on_one_compact_row(self) -> None:
        detail = self.section_source("ingressConnectionHeader")

        self.assertIn("ingressReadinessTrack(readiness)", detail)
        self.assertIn("ingressReadinessAction(readiness, item: item)", detail)
        self.assertIn("HStack(alignment: .center", detail)

    def test_ingress_readiness_uses_user_facing_scan_language(self) -> None:
        root = Path(__file__).resolve().parent.parent
        readiness = (root / "Sources" / "Model" / "IngressReadiness.swift").read_text(encoding="utf-8")
        track = self.section_source("ingressReadinessTrack")
        action = self.section_source("ingressReadinessAction")

        self.assertIn('title: L10n.tr("待扫描")', readiness)
        self.assertIn('Button("扫描所选档位")', action)
        self.assertIn("selectionStore.startIngressCandidateScan", action)
        self.assertIn('["连接", "选择档位", "扫描一次", "可参与推荐"]', track)
        for internal_term in ("待基线", "建立有效基线", "有效基线模型"):
            self.assertNotIn(internal_term, readiness + track + action)

    def test_ingress_scan_planning_is_delegated_and_evidence_rules_stay_out_of_view(self) -> None:
        self.assertNotIn("currentAppendableComparisonRunMetadata", self.selection_store_source)
        self.assertNotIn("hasComparisonRound", self.selection_store_source)
        self.assertNotIn("hasReusableIncrementalFullEvidence", self.selection_store_source)
        self.assertNotIn("startAddedCandidatesScan", self.selection_store_source)
        self.assertIn(
            "SettingsCandidatePresenter.evidencePresentation(for: evidence)",
            self.settings_view_source,
        )
        for evidence_rule in (
            "evidence.questionCompleted",
            "evidence.latestValidAt",
            "evidence.isUsingPreviousValidResult",
            "evidence.isCurrentPackComparable",
            "evidence.scoreText",
        ):
            self.assertNotIn(evidence_rule, self.settings_view_source)

    def test_added_candidates_submit_single_intent_for_backend_planning(self) -> None:
        action = self.section_source("ingressReadinessAction")

        self.assertIn("func startIngressCandidateScan(", self.selection_store_source)
        self.assertIn("candidateIDs: [String]", self.selection_store_source)
        self.assertIn("selectionMode: .single", self.selection_store_source)
        self.assertIn("previewAndStartScan(", self.selection_store_source)
        self.assertIn("selectionStore.startIngressCandidateScan", action)

    def test_rate_limited_endpoint_probe_saves_a_disabled_encrypted_draft(self) -> None:
        source = self.settings_store_source

        self.assertIn('response.errorCategory == "rate_limited"', source)
        self.assertIn("connectionEnabled: false", source)
        self.assertIn("candidateEnabled: false", source)
        self.assertIn('lastTestStatus: "rate_limited"', source)
        self.assertIn("secretStore.stage(apiKey, connectionID: resolvedID)", source)
        self.assertIn("连接草稿已保存", source)

    def test_candidate_effort_labels_come_from_settings_projection(self) -> None:
        custom_toggle = self.section_source("customCandidateToggle")
        display_name = self.section_source("candidateDisplayName")
        variant_name = self.section_source("apiModelVariantName")
        presenter = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Model"
            / "SettingsCandidatePresenter.swift"
        ).read_text(encoding="utf-8")

        self.assertIn("settingsCandidatePresentation(for: candidate).displayName", display_name)
        self.assertIn("settingsCandidatePresentation(for: candidate).variantName", variant_name)
        self.assertIn("candidateDisplayName(candidate)", custom_toggle)
        self.assertNotIn("candidate.displayName", custom_toggle)
        self.assertIn("projection.displayScanProfile", presenter)
        self.assertIn("familyID: candidate.modelId", presenter)
        self.assertIn("displayModel: candidate.modelId", presenter)
        self.assertNotIn("candidate.familyId", presenter)
        self.assertNotIn("candidate.variantId", presenter)
        self.assertNotIn("candidate.scanProfile", presenter)

    def test_model_families_render_as_continuous_rows_not_nested_cards(self) -> None:
        local_detail = self.section_source("localConnectionDetailCard")
        family = self.section_source("profileFamilyCard")
        api_family = self.section_source("apiModelFamilyCard")
        api_variant = self.section_source("apiModelVariantRow")

        self.assertIn("modelFamilyGroups(for: connection)", local_detail)
        self.assertIn("IslandVisual.hairline", family)
        self.assertNotIn("RoundedRectangle", family)
        self.assertIn("IslandVisual.hairline", api_family)
        self.assertNotIn("RoundedRectangle", api_family)
        self.assertIn("IslandVisual.hairline", api_variant)
        self.assertNotIn("RoundedRectangle", api_variant)

    def test_ingress_detail_uses_one_section_surface_and_flat_metadata(self) -> None:
        selected = self.section_source("selectedIngressDetail")
        metadata = self.section_source("ingressDetailMetadataStrip")

        self.assertIn("ingressConnectionHeader", selected)
        self.assertNotIn(".background(cardBackground)", selected)
        self.assertIn("IslandVisual.hairline", selected)
        for name in ("localConnectionDetailCard", "endpointConnectionCard"):
            detail = self.section_source(name)
            self.assertIn("ingressDetailMetadataStrip", detail)
            self.assertNotIn("ingressMetaPill", detail)
            self.assertNotIn(".background(cardBackground)", detail)
        self.assertIn("IslandVisual.hairline", metadata)
        self.assertNotIn("RoundedRectangle", metadata)

    def test_ingress_top_does_not_repeat_operational_inventory(self) -> None:
        header = self.section_source("settingsPageHeader")

        self.assertIn("enabledCandidatesButton", header)
        for token in (
            "ingressSummaryMetrics",
            "readyIngressSourceCount",
            "pendingIngressSourceCount",
            "validBaselineCandidateCount",
            'label: "可推荐来源"',
            'label: "待处理来源"',
            'label: "已有成绩模型"',
        ):
            self.assertNotIn(token, self.source)

    def test_resumable_run_continues_regular_scan_and_keeps_conflicts_explicit(self) -> None:
        scope = self.section_source("regularScanScopeSection")
        self.assertIn("Text(regularScanButtonTitle)", scope)
        self.assertIn(
            "selectionStore.startRegularScan(conflictPresentation: .settings)",
            scope,
        )
        self.assertIn("private var regularScanButtonTitle", self.source)
        self.assertIn('return L10n.tr("扫描进行中")', self.source)
        self.assertIn('return L10n.tr("继续扫描")', self.source)
        self.assertIn("if regularScanIsRunning", scope)
        self.assertIn("else if hasPausedResumableRun", scope)
        self.assertIn("|| regularScanIsRunning", scope)
        self.assertIn("从下一轮开始生效", scope)
        self.assertIn("RestartScanButton()", scope)

    def test_missing_target_display_falls_back_to_auto_menu_selection(self) -> None:
        display = self.section_source("displaySection")
        selection = self.section_source("targetDisplaySelection")

        self.assertIn(".pickerStyle(.menu)", display)
        self.assertIn("DisplayInfo.all().contains", selection)
        self.assertIn('? id : "auto"', selection)

    def test_builtin_target_display_name_follows_the_app_language(self) -> None:
        display = self.section_source("displaySection")

        self.assertIn("display.isBuiltin", display)
        self.assertIn('L10n.tr("内建视网膜显示器")', display)
        self.assertIn(": display.name", display)
        self.assertNotIn("Text(display.name)", display)

    def test_source_card_status_uses_shared_operational_readiness(self) -> None:
        card = self.section_source("sourceWorkspaceCard")
        self.assertIn("let readiness = ingressReadiness(for: item)", card)
        self.assertIn("readinessStatusBadge(readiness)", card)
        self.assertNotIn("isEffectivelyEnabled", card)

    def test_scan_conflict_alert_keeps_single_scan_actions_clickable(self) -> None:
        self.assertIn('.alert(L10n.tr("无法开始扫描")', self.source)
        self.assertIn("selectionStore.scanConflictMessage", self.source)
        self.assertIn("selectionStore.scanConflictPresentation == .settings", self.source)
        self.assertIn("selectionStore.dismissScanConflict()", self.source)

        for section_name in (
            "apiModelVariantRow",
            "singleVariantRow",
            "candidateRow",
        ):
            section = self.section_source(section_name)
            self.assertIn("selectionStore.startSingleScan(candidateID:", section)
            self.assertIn("conflictPresentation: .settings", section)
            after_action = section.split("selectionStore.startSingleScan(candidateID:", 1)[1]
            disabled_scope = after_action.split(".disabled(", 1)[1].split(")", 1)[0]
            self.assertNotIn("runtime.isRunning", disabled_scope)
            self.assertNotIn("runtime.hasResumableRun", disabled_scope)
            self.assertIn("settings.isSaving", disabled_scope)

    def test_data_health_states_the_local_privacy_boundary(self) -> None:
        section = self.section_source("diagnosticPrivacySection")
        self.assertIn('title: "本地数据与隐私"', section)
        self.assertIn('formRow("评测与历史")', section)
        self.assertIn('Text("仅保存在本机，不自动上传")', section)
        self.assertIn('formRow("官网数据")', section)
        self.assertIn('Text("仅下载公开快照、价格和版本信息")', section)
        self.assertIn('formRow("诊断导出")', section)
        self.assertNotIn("共享匿名评测", self.source)


if __name__ == "__main__":
    unittest.main()
