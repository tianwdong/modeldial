from __future__ import annotations

from pathlib import Path
import unittest


class ExpandedSelectionViewCopyTest(unittest.TestCase):
    def test_scan_conflict_alert_only_claims_expanded_origin(self) -> None:
        self.assertIn("store.scanConflictPresentation == .expanded", self.source)
        self.assertNotIn("store.scanConflictPresentation == .settings", self.source)

    def test_expanded_view_consumes_destination_once_on_entry(self) -> None:
        self.assertIn("@State private var entryDestination: GlanceDestination = .overview", self.source)
        self.assertIn("let destination = store.consumeExpandedDestination()", self.source)
        self.assertIn("entryDestination = destination", self.source)
        self.assertIn("applyEntryDestination(destination)", self.source)

    def test_entry_reason_is_projected_from_authoritative_destination(self) -> None:
        projection_source = self._section(
            self.source,
            "private var operationalPresentation: OperationalStatePresenter.Presentation {",
            "private var headerDetailText",
        )

        self.assertIn("entryDestination: entryDestination", projection_source)
        self.assertIn(
            "glanceDestination: store.glancePresentation.destination",
            projection_source,
        )
        self.assertNotIn("entryDestinationReasonText", self.source)

    def test_destination_mapping_reuses_existing_surfaces(self) -> None:
        source = self._section(
            self.source,
            "private func applyEntryDestination",
            "private var evidenceBackdrop",
        )
        for destination in (
            ".overview", ".runProgress", ".failureEvidence",
            ".recommendationIssue", ".rescan", ".connectionDiagnostics",
        ):
            self.assertIn(destination, source)
        self.assertIn("pageIndex = 0", source)
        self.assertIn("pageIndex = 1", source)
        self.assertIn("selectedEvidence", source)
        failure_source = source.split("case .failureEvidence:", 1)[1]
        self.assertIn("pageIndex = 1", failure_source)
        self.assertIn("selectedEvidence = nil", failure_source)
        self.assertNotIn("?? store.leaderboard.first", failure_source)

    def test_expanded_surfaces_delegate_glance_state_projection(self) -> None:
        projection_source = self._section(
            self.source,
            "private var operationalPresentation: OperationalStatePresenter.Presentation {",
            "private var headerDetailText",
        )

        self.assertIn("OperationalStatePresenter.presentation(", projection_source)
        self.assertIn("state: expandedOperationalState", projection_source)
        self.assertIn("glanceTone: store.glancePresentation.tone", projection_source)
        self.assertNotIn("switch expandedOperationalState", projection_source)

    def test_snapshot_refresh_warning_keeps_scan_or_cached_result_context_outside_footer(self) -> None:
        input_source = self._section(
            self.source,
            "private var operationalPresentation: OperationalStatePresenter.Presentation {",
            "private var headerDetailText",
        )
        footer_source = self._section(
            self.source,
            "private var panelFooter: some View {",
            "private var footerPageTabs",
        )

        self.assertIn("hasSnapshotRefreshIssue: refreshIssue != nil", input_source)
        self.assertIn("snapshotRefreshMessage: refreshIssue?.message", input_source)
        self.assertIn("snapshotRefreshDetail: refreshIssue?.detail", input_source)
        self.assertIn("operationalPresentation.operationalTone", self.source)
        self.assertIn("operationalPresentation.heroDecisionReason", self.source)
        self.assertIn("if let footerStatus = footerDataStatusText", footer_source)
        self.assertIn("Text(footerStatus)", footer_source)
        self.assertNotIn("snapshotRefreshIssue", footer_source)
        self.assertNotIn("switch expandedOperationalState", self.source)

    def test_radar_exposes_an_immediate_remote_refresh_control(self) -> None:
        control_source = self._section(
            self.source,
            "private var radarReferenceRefreshControl: some View {",
            "private func radarControlLabel",
        )
        control_bar_source = self._section(
            self.source,
            "private var radarControlBar: some View {",
            "private var radarSourceControl",
        )
        footer_source = self._section(
            self.source,
            "private var panelFooter: some View {",
            "private var footerControls: some View {",
        )

        self.assertIn("store.refreshReferenceSnapshotNow()", control_source)
        self.assertIn("store.isReferenceSnapshotRefreshInFlight", control_source)
        self.assertIn("store.referenceSnapshotRefreshFeedbackStatus", control_source)
        self.assertIn(
            "OperationalStatePresenter.referenceRefreshPresentation(",
            control_source,
        )
        self.assertIn('L10n.tr("刷新远端结果")', control_source)
        self.assertNotIn("radarReferenceRefreshControl", control_bar_source)
        self.assertIn("!comparisonDatasetSelection.showsLocalRepairControls", footer_source)
        self.assertIn("radarReferenceRefreshControl", footer_source)

    def test_comparison_page_is_the_only_detail_entry(self) -> None:
        call_source = self._section(
            self.source,
            "private var detailPage: some View {",
            "private var panelFooter",
        )

        self.assertIn("ComparisonPage(", call_source)
        self.assertIn("recommendationUse: store.snapshot?.recommendationUse", call_source)
        self.assertNotIn("StatisticsPage(", call_source)
        self.assertNotIn("scanInProgress:", call_source)

    def test_comparison_evidence_uses_one_content_slot_and_one_vertical_scroll(self) -> None:
        comparison_source = self.source.split("private struct ComparisonPage: View {", 1)[1]
        content_source = self._section(
            comparison_source,
            "private func comparisonContent(",
            "private var comparisonHairline",
        )
        details_source = self._section(
            comparison_source,
            "private func evaluationDetailsContent(",
            "private func realUsageSection(",
        )

        self.assertIn("if isEvaluationDetailsExpanded", content_source)
        self.assertIn("evaluationDetailsContent(", content_source)
        self.assertIn("comparisonOverview(", content_source)
        self.assertIn("scoreTrendSection(", comparison_source)
        self.assertIn("presentation: presentation", content_source)
        self.assertNotIn("ZStack(alignment: .topLeading)", content_source)
        self.assertEqual(details_source.count("ScrollView(.vertical"), 1)
        self.assertNotIn(".shadow(", details_source)

    def test_comparison_page_respects_the_expanded_shell_safe_area(self) -> None:
        comparison_source = self.source.split("private struct ComparisonPage: View {", 1)[1]
        details_bar_source = self._section(
            comparison_source,
            "private var evaluationDetailsBar: some View {",
            "private func evaluationDetailsContent(",
        )

        self.assertIn(
            "LayoutRhythm.section + IslandShape.expandedShoulderRadius",
            comparison_source,
        )
        self.assertGreaterEqual(
            comparison_source.count(
                ".padding(.horizontal, comparisonContentHorizontalInset)"
            ),
            10,
        )
        self.assertIn(
            ".padding(.horizontal, comparisonContentHorizontalInset)",
            details_bar_source,
        )
        self.assertNotIn(".padding(.horizontal, 18)", details_bar_source)

    def test_comparison_trend_fills_the_evidence_slot_and_detail_headers_stay_stable(self) -> None:
        chart_source = self._section(
            self.source,
            "private struct ComparisonScoreTrendChart: View {",
            "private struct ComparisonPage: View {",
        )
        trend_source = self._section(
            self.source,
            "private func scoreTrendSection(",
            "private func comparisonTrendLegend(",
        )
        token_header_source = self._section(
            self.source,
            "private var tokenHeader: some View {",
            "private func tokenRow(",
        )

        self.assertIn(".frame(minHeight: 168, maxHeight: .infinity)", trend_source)
        self.assertNotIn(".frame(height: 72)", trend_source)
        self.assertNotIn("maxHeight: .infinity, alignment: .topLeading", trend_source)
        self.assertIn("let plotHeight = max(0, outer.size.height - 18)", chart_source)
        self.assertNotIn(".frame(height: 84)", chart_source)
        self.assertIn("trendAreaPath(points: data.candidate", chart_source)
        self.assertIn("trendBaseline(point: latestCurrent", chart_source)
        self.assertIn("latestTrendPoint(", chart_source)
        self.assertIn('Text(L10n.tr("当前"))', token_header_source)
        self.assertIn('Text(L10n.tr("候选"))', token_header_source)
        self.assertNotIn("current.displayName", token_header_source)
        self.assertNotIn("recommended.displayName", token_header_source)
        self.assertNotIn(".frame(width: 92", token_header_source)

    def test_comparison_basis_uses_full_width_rows_in_the_detail_column(self) -> None:
        evidence_source = self._section(
            self.source,
            "private func evidenceSection(",
            "private func evidenceRow(",
        )

        for label in ("来源", "题包", "评分器", "路线", "评测快照", "价格快照"):
            self.assertIn(f'evidenceRow(L10n.tr("{label}")', evidence_source)
        self.assertIn("sharedConnectionText", evidence_source)
        self.assertIn("sharedCompletionText", evidence_source)
        self.assertNotIn("HStack(spacing: LayoutRhythm.section)", evidence_source)

    def setUp(self) -> None:
        root = Path(__file__).resolve().parent.parent
        self.source = (root / "Sources" / "Views" / "ExpandedSelectionView.swift").read_text(
            encoding="utf-8"
        )
        self.model_source = (root / "Sources" / "Model" / "SelectionModels.swift").read_text(
            encoding="utf-8"
        )
        self.compact_session_source = (
            root / "Sources" / "Views" / "CompactSessionPanelView.swift"
        ).read_text(encoding="utf-8")
        self.selection_store_source = (
            root / "Sources" / "Model" / "SelectionStore.swift"
        ).read_text(encoding="utf-8")
        self.identity_presentation_source = (
            root / "Sources" / "Support" / "ModelIdentityPresentation.swift"
        ).read_text(encoding="utf-8")
        self.radar_presenter_source = (
            root / "Sources" / "Model" / "RadarPresenter.swift"
        ).read_text(encoding="utf-8")
        self.operational_presenter_source = (
            root / "Sources" / "Model" / "OperationalStatePresenter.swift"
        ).read_text(encoding="utf-8")
        self.radar_entry_presenter_source = (
            root / "Sources" / "Model" / "RadarEntryPresenter.swift"
        ).read_text(encoding="utf-8")
        self.profile_scope_presenter_source = (
            root / "Sources" / "Model" / "EvaluationProfileScopePresenter.swift"
        ).read_text(encoding="utf-8")
        self.comparison_selection_presenter_source = (
            root / "Sources" / "Model" / "ComparisonSelectionPresenter.swift"
        ).read_text(encoding="utf-8")
        self.active_session_presenter_source = (
            root / "Sources" / "Model" / "ActiveSessionPresenter.swift"
        ).read_text(encoding="utf-8")

    def _section(self, source: str, start: str, end: str) -> str:
        self.assertIn(start, source)
        self.assertIn(end, source)
        return source.split(start, 1)[1].split(end, 1)[0]

    def test_v2_ranking_is_the_only_overview_path_and_opens_evidence(self) -> None:
        ranking_source = self._section(
            self.source,
            "private var overviewRankingCard: some View {",
            "private func repairNotice",
        )
        row_source = self._section(
            self.source,
            "private struct RadarLeaderboardRow: View {",
            "private struct ComparisonScoreTrendChart",
        )
        empty_source = self._section(
            self.source,
            "private var radarLeaderboardEmptyState: some View {",
            "private var detailPage: some View {",
        )

        self.assertNotIn("usesRadarV2", ranking_source)
        self.assertIn("} else {", ranking_source)
        self.assertNotIn("radarRankingContext", ranking_source)
        self.assertIn("radarRankingHeader", ranking_source)
        self.assertIn("let presentation = radarLeaderboardPresentation(for: entry)", ranking_source)
        self.assertIn("let decisionTags = presentation.tags.compactMap(leaderboardExportTag)", ranking_source)
        self.assertIn("rank: presentation.rank", ranking_source)
        self.assertIn("decisionTag: decisionTags.min { $0.priority < $1.priority }", ranking_source)
        self.assertNotIn("tags: presentation.tags", ranking_source)
        self.assertIn("ForEach(store.radarLeaderboardItems)", ranking_source)
        self.assertNotIn("enumerated()", ranking_source)
        self.assertIn("onPresentEvidence: { presentEvidence(candidateID: entry.id) }", ranking_source)
        self.assertIn("Button(action: onPresentEvidence)", row_source)
        self.assertIn("ProviderLogoMark(providerID: entry.providerId)", row_source)
        self.assertIn("RadarLeaderboardDecisionTag(tag: decisionTag)", row_source)
        self.assertNotIn("VStack(alignment: .leading", row_source)
        self.assertNotIn("ForEach(tags", row_source)
        self.assertIn("static let rowHeight: CGFloat = 50", self.source)
        self.assertIn("accessibilityStatus", row_source)
        self.assertIn('.accessibilityHint(L10n.tr("查看评测证据"))', row_source)
        self.assertLess(ranking_source.index("repairNotice(entry:"), ranking_source.index("ScrollView"))
        self.assertIn("if isBatchRepairRunning", ranking_source)
        self.assertIn("repairFailureNotice(message:", ranking_source)
        self.assertIn("Text(radarSurfacePresentation.emptyReason)", empty_source)
        self.assertIn(".fixedSize(horizontal: false, vertical: true)", empty_source)
        self.assertIn(
            "maxHeight: overviewRankingFillsAvailableHeight ? .infinity",
            ranking_source,
        )
        self.assertIn(
            "store.radarLeaderboardItems.isEmpty",
            ranking_source,
        )
        self.assertIn("if showsRadarModelSetupCTA", ranking_source)
        self.assertIn("radarModelSetupNotice", ranking_source)
        self.assertIn(
            "store.snapshot?.referenceSnapshotFeed.trustedLatest != nil",
            self.source,
        )

    def test_radar_uses_explicit_provider_identity_and_bundled_logo_assets(self) -> None:
        logo_source = self._section(
            self.source,
            "struct ProviderLogoMark: View {",
            "private enum RadarRankingLayout",
        )

        self.assertIn("let providerId: String?", self.model_source)
        self.assertIn(
            "providerId: ModelIdentityPresentation.providerBrandID(",
            self.selection_store_source,
        )
        self.assertIn("providerId: localProviderID(for: entry)", self.selection_store_source)
        self.assertIn("familyID: entry.familyId", self.selection_store_source)
        self.assertNotIn("brandCode(for:", self.source)
        self.assertIn('subdirectory: "ProviderLogos"', logo_source)
        self.assertIn("renderingMode(.template)", logo_source)
        self.assertIn("frame(width: 20, height: 20)", logo_source)
        self.assertNotIn("RoundedRectangle", logo_source)
        self.assertIn('case "codex": return "openai"', self.identity_presentation_source)
        self.assertIn('case "claude-code": return "anthropic"', self.identity_presentation_source)
        self.assertIn('case "grok-build": return "xai"', self.identity_presentation_source)
        self.assertIn('provider == "custom_endpoint"', self.identity_presentation_source)
        self.assertIn('("deepseek-", "deepseek")', self.identity_presentation_source)
        self.assertIn("case nil:", self.identity_presentation_source)

    def test_overview_does_not_show_copy_value_or_accuracy_percentage_copy(self) -> None:
        self.assertNotIn('Text("复制值")', self.source)
        self.assertNotIn(
            'Text("\\(stageSummaryText) · 准确率 \\(entry.passRate)%")',
            self.source,
        )

    def test_overview_identity_label_never_wraps_inside_the_comparison_phrase(self) -> None:
        identity_row = self._section(
            self.source,
            "private func heroIdentityRow<Content: View>(",
            "private var radarControlBar",
        )

        self.assertIn("Text(label)", identity_row)
        self.assertIn(".lineLimit(1)", identity_row)
        self.assertIn(".minimumScaleFactor(0.85)", identity_row)

    def test_radar_context_controls_separate_compact_visible_and_full_accessibility_copy(self) -> None:
        source_control = self._section(
            self.source,
            "private var radarSourceControl: some View {",
            "private var radarPreferenceControl: some View {",
        )
        self.assertIn("title: radarSourcePresentation.control", source_control)
        self.assertIn(
            ".accessibilityValue(radarSourcePresentation.accessibilityValue)",
            source_control,
        )
        self.assertIn("RadarPresenter.sourceLabels(", self.source)

    def test_radar_models_keep_official_and_local_sources_separate(self) -> None:
        self.assertIn("let advisorV2Evidence: BridgeAdvisorV2Evidence", self.model_source)
        self.assertIn(
            "let recommendationPortfolioV2: BridgeRecommendationPortfolioV2",
            self.model_source,
        )
        self.assertIn("let referenceSnapshotFeed: BridgeReferenceSnapshotFeed", self.model_source)
        self.assertIn("radarOfficialCanonicalRows", self.source)
        self.assertIn("row.decisionTags.map(\\.kind)", self.source)
        self.assertIn("radarLocalCanonicalRows", self.source)
        self.assertIn("targetLabels: row.canonicalLabels", self.source)
        self.assertIn("RadarPresenter.leaderboardRow(", self.source)
        self.assertNotIn('case "推荐", "Recommended", "Highest score"', self.source)

    def test_radar_comparison_and_empty_state_use_trusted_official_snapshot(self) -> None:
        self.assertIn("referenceSnapshotFeed.trustedLatest", self.source)
        self.assertIn(
            "officialSnapshotIsTrusted: latest?.isPublicOfficialSnapshot == true",
            self.source,
        )
        self.assertNotIn("referenceSnapshotFeed.latest", self.source)
        self.assertIn("requiresModelSetup: requiresModelSetup", self.source)
        self.assertIn("requiresModelSetup: Bool = false", self.radar_presenter_source)
        self.assertIn('L10n.tr("官方 Radar 尚未载入")', self.radar_presenter_source)

    def test_unmapped_current_model_uses_radar_presenter_before_legacy_copy(self) -> None:
        projection_source = self._section(
            self.source,
            "private var operationalPresentation: OperationalStatePresenter.Presentation {",
            "private var headerDetailText",
        )
        self.assertIn("RadarPresenter.decision(", self.source)
        self.assertIn("radarReason: radarDecisionPresentation.reason", projection_source)
        self.assertIn("fallbackReason: radarDecisionPresentation.reasonOrFallback", projection_source)
        self.assertIn("isUnmappedCurrentModel: isUnmappedDetectedCurrentModel", projection_source)

    def test_v2_surface_has_no_dynamic_schema_compatibility_switch(self) -> None:
        self.assertNotIn("usesRadarV2", self.source)
        self.assertNotIn("usesRadarV2", self.operational_presenter_source)
        self.assertNotIn("legacyTitle", self.operational_presenter_source)
        self.assertNotIn("legacyReason", self.operational_presenter_source)
        self.assertIn("showsConfidenceChip: false", self.operational_presenter_source)

    def test_radar_uses_committed_dashboard_while_working_scan_updates(self) -> None:
        root = Path(__file__).resolve().parent.parent
        store_source = (root / "Sources" / "Model" / "SelectionStore.swift").read_text(
            encoding="utf-8"
        )
        detail_source = self._section(
            self.source,
            "private var comparisonDatasetSelection: ComparisonSelectionPresenter.DatasetSelection {",
            "private var comparisonQuestionPackVersion: String? {",
        )
        apply_runtime = store_source.split(
            "private func applyRuntime(_ runtime: BridgeRuntime)", 1
        )[1].split("private func activeModelSessionKey", 1)[0]

        radar_dashboard = self._section(
            store_source,
            "var radarDashboard: BridgeDashboard? {",
            "var radarPortfolio: BridgeRecommendationPortfolioV2? {",
        )
        self.assertIn("RadarPresenter.shouldUseCurrentDashboard(", radar_dashboard)
        self.assertIn("return snapshot.dashboard", radar_dashboard)
        self.assertIn("return snapshot.stableEvidenceDashboard", radar_dashboard)
        self.assertIn("?? snapshot.stableDashboard", radar_dashboard)
        self.assertIn("?? snapshot.dashboard", radar_dashboard)
        self.assertIn("currentSnapshot.runtime = runtime", apply_runtime)
        self.assertNotIn("dashboard", apply_runtime)
        self.assertIn("store.radarDashboard?.statistics", detail_source)
        self.assertIn("store.radarDashboard?.leaderboard", detail_source)
        self.assertIn("evidenceUpdating: isEvidenceUpdating", self.source)

    def test_radar_current_sessions_exclude_modeldial_evaluations(self) -> None:
        session_source = self._section(
            self.source,
            "private var radarSessionSummary: some View {",
            "private var multiConfigurationDecisions:",
        )

        self.assertIn("radarUserSessions", session_source)
        self.assertIn("isEvaluationSession: session.isEvaluationSession == true", self.source)
        self.assertIn("let isEvaluationSession: Bool?", self.model_source)

    def test_radar_session_summary_prioritizes_task_and_model_identity(self) -> None:
        summary_source = self._section(
            self.source,
            "private var radarSessionSummary: some View {",
            "private func radarSessionSummaryPreview(",
        )
        preview_source = self._section(
            self.source,
            "private func radarSessionSummaryPreview(",
            "private var radarSessionsPopover: some View {",
        )
        self.assertIn(
            "ActiveSessionPresenter.overview(radarUserSessions)",
            summary_source,
        )
        self.assertIn("overviewPresentation.visibleSessions.enumerated()", summary_source)
        self.assertIn("radarSessionSummaryPreview(session)", summary_source)
        self.assertIn("Text(radarSessionPresentation.title)", summary_source)
        self.assertIn(
            "L10n.Sessions.overflow(overviewPresentation.overflowCount)",
            summary_source,
        )
        self.assertNotIn("Text(detail)", summary_source)
        self.assertIn('help(L10n.tr("查看全部会话与各配置建议"))', summary_source)
        self.assertIn("ActiveSessionPresenter.SessionPresentation", preview_source)
        self.assertIn("Text(session.title)", preview_source)
        self.assertIn(".truncationMode(.tail)", preview_source)
        self.assertIn("radarActiveUsagePresentation.sessionIdentities[session.id]", preview_source)
        self.assertIn(".minimumScaleFactor(0.78)", preview_source)
        self.assertIn(".frame(maxWidth: 132, alignment: .trailing)", preview_source)
        self.assertIn(".layoutPriority(2)", preview_source)
        self.assertIn("RadarPresenter.sessionSummary(", self.source)
        self.assertIn("RadarPresenter.activeUsage(", self.source)
        self.assertIn("context: sessionPresentation.context", self.source)

    def test_visible_model_identity_uses_one_unboxed_presentation(self) -> None:
        presentation = self.identity_presentation_source

        self.assertIn('case "gpt": return "GPT"', presentation)
        self.assertIn('case "xhigh": return "XHigh"', presentation)
        self.assertIn('case "ultra": return "Ultra"', presentation)
        self.assertIn('case "medium": return "Medium"', presentation)
        self.assertNotIn('return "[\\(normalized)]"', presentation)
        self.assertIn(
            "ModelIdentityPresentation.displayLabel(\n"
            "                model: candidate.modelId,\n"
            "                effort: candidate.scanProfile",
            self.selection_store_source,
        )
        self.assertGreaterEqual(
            self.selection_store_source.count(
                "displayName: ModelIdentityPresentation.displayLabel("
            ),
            2,
        )
        self.assertIn("RadarPresenter.activeUsage(", self.source)
        self.assertIn("ModelIdentityPresentation.displayLabel(", self.radar_presenter_source)
        self.assertIn("radarActiveUsagePresentation.sessionIdentities[session.id]", self.source)
        self.assertNotIn("overviewActiveSessionIdentity", self.source)
        self.assertIn(
            "ModelIdentityPresentation.displayLabel(",
            self.active_session_presenter_source,
        )
        self.assertNotIn(
            'contextParts.append("\\(model) [\\(effort)]")',
            self.active_session_presenter_source,
        )

    def test_radar_comparison_stacks_complete_identities_instead_of_squeezing_one_line(self) -> None:
        identity_source = self._section(
            self.source,
            "private var radarDecisionIdentityStrip: some View {",
            "private func radarDecisionIdentityLine(",
        )
        line_source = self._section(
            self.source,
            "private func radarDecisionIdentityLine(",
            "private func radarDecisionMetric",
        )

        self.assertIn("VStack(alignment: .leading, spacing: 6)", identity_source)
        self.assertIn("radarCurrentIdentityLine", identity_source)
        self.assertIn("radarDecisionIdentityLine(", identity_source)
        self.assertIn("IslandTransitionElement.primaryIdentity.rawValue", identity_source)
        self.assertIn("IslandTransitionElement.secondaryStatus.rawValue", identity_source)
        self.assertIn("element: .candidateIdentity", identity_source)
        self.assertNotIn("HStack(spacing: 7)", identity_source)
        self.assertNotIn(".minimumScaleFactor(0.72)", identity_source)
        self.assertIn("Text(value)", line_source)
        self.assertIn(".lineLimit(1)", line_source)
        self.assertIn(".minimumScaleFactor(0.84)", line_source)
        self.assertIn(".layoutPriority(1)", line_source)

    def test_selection_models_decode_candidate_evidence_provenance_compatibly(self) -> None:
        self.assertIn("let latestValidRunId: String?", self.model_source)
        self.assertIn("let latestValidAt: String?", self.model_source)
        self.assertIn("let latestAttemptStatus: String?", self.model_source)
        self.assertIn("let latestAttemptErrorSummary: String?", self.model_source)
        self.assertIn("let isCurrentPackComparable: Bool", self.model_source)
        self.assertIn("let isUsingPreviousValidResult: Bool", self.model_source)
        self.assertIn(
            "decodeIfPresent(Bool.self, forKey: .isCurrentPackComparable) ?? true",
            self.model_source,
        )

    def test_best_combination_decodes_four_state_contract_with_conservative_legacy_fallback(self) -> None:
        self.assertIn("let decisionState: String", self.model_source)
        self.assertIn("let recommendationOutcome: String", self.model_source)
        self.assertIn("let evidenceState: String", self.model_source)
        self.assertIn("let decisionReason: String", self.model_source)
        self.assertIn("let currentDefaultCandidateId: String?", self.model_source)
        self.assertIn("let decisionTitle: String", self.model_source)
        self.assertIn("let decisionActionLabel: String", self.model_source)
        self.assertIn('case "keep", "switch", "recommend", "wait", "retain_after_failure":', self.model_source)
        self.assertIn('case "retry_required":', self.model_source)
        self.assertIn('fallbackDecisionState = "retain_after_failure"', self.model_source)
        self.assertIn('default:', self.model_source)
        self.assertIn('fallbackDecisionState = "wait"', self.model_source)
        self.assertNotIn('fallbackDecisionState = "switch_validation_ready"', self.model_source)
        self.assertNotIn('fallbackDecisionState = "complete_review"', self.model_source)
        self.assertNotIn('fallbackDecisionState = "provisional_leader"', self.model_source)

    def test_overview_hero_matches_current_recommendation_structure(self) -> None:
        hero_source = self._section(
            self.source,
            "private var overviewHeroCard: some View {",
            "private var heroDecisionHeader",
        )
        self.assertIn("OperationalStatePresenter.presentation(", self.source)
        self.assertIn("radarControlBar", hero_source)
        self.assertIn("heroDecisionHeader", hero_source)
        self.assertIn("decisionIdentityStrip", hero_source)
        self.assertNotIn("radarBenefitReceipt", hero_source)
        self.assertNotIn("legacyDecisionIdentityStrip", self.source)
        self.assertNotIn('Text("当前推荐")', hero_source)

    def test_overview_hero_reduces_repeated_summary_cards(self) -> None:
        width_source = self._section(
            self.source,
            "private func overviewLeftWidth(for totalWidth: CGFloat) -> CGFloat {",
            "private var overviewHeroCard: some View {",
        )
        hero_source = self._section(
            self.source,
            "private var overviewHeroCard: some View {",
            "private var heroDecisionHeader",
        )
        identity_source = self._section(
            self.source,
            "private var decisionIdentityStrip: some View {",
            "private func heroIdentityRow",
        )
        confidence_source = self._section(
            self.source,
            "private var showsHeroConfidenceChip: Bool {",
            "private var heroDecisionTitleText: String {",
        )

        self.assertIn("min(352, max(340, totalWidth * 0.34))", width_source)
        self.assertIn("radarDecisionIdentityStrip", identity_source)
        self.assertNotIn("RoundedRectangle", identity_source)
        self.assertIn("operationalPresentation.showsConfidenceChip", confidence_source)
        self.assertIn("operationalPresentation.confidenceLabel", self.source)

    def test_overview_uses_distinct_summary_and_evidence_surfaces(self) -> None:
        overview_source = self._section(
            self.source,
            "private var overviewPage: some View {",
            "private func overviewLeftWidth(for totalWidth: CGFloat) -> CGFloat {",
        )

        self.assertIn(".background(IslandVisual.summarySurface)", overview_source)
        self.assertIn(".background(IslandVisual.evidenceSurface)", overview_source)
        self.assertIn(".background(IslandVisual.shellSurface)", overview_source)
        self.assertIn("IslandVisual.contentTopHighlight", overview_source)

    def test_current_in_use_picker_only_lists_enabled_candidates_and_persists_choice(self) -> None:
        self.assertIn("@State private var showsCurrentInUsePicker = false", self.source)
        self.assertIn("private var currentInUseCandidateOptions", self.source)
        self.assertIn("OperationalStatePresenter.ingress(", self.source)
        self.assertIn("ingressPresentation.currentCandidates", self.source)
        self.assertNotIn("ingress.sources.filter", self.source)
        self.assertNotIn("ingress.connections.compactMap", self.source)
        self.assertIn("source.isEnabled", self.operational_presenter_source)
        self.assertIn("connection.isEnabled", self.operational_presenter_source)
        self.assertIn("candidate.isEnabled", self.operational_presenter_source)
        self.assertIn("settings.setCurrentDefault(candidateID: option.id)", self.source)
        self.assertIn("settings.useAutomaticCurrentModel()", self.source)
        self.assertIn("默认根据活动终端会话自动识别", self.source)
        self.assertIn("不会修改任何终端配置", self.source)
        self.assertNotIn("活动 Codex 会话", self.source)
        self.assertNotIn("不会修改 Codex 配置", self.source)
        self.assertIn('proxy.scrollTo("current-in-use-picker-top", anchor: .top)', self.source)

    def test_unmapped_detected_effort_keeps_raw_current_model_identity(self) -> None:
        operational_presenter = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Model"
            / "OperationalStatePresenter.swift"
        ).read_text(encoding="utf-8")
        self.assertIn("private var detectedCurrentModelIdentity: String?", self.source)
        self.assertIn("recommendation?.detectedCurrentModel", self.source)
        self.assertIn("recommendation?.detectedCurrentEffort", self.source)
        self.assertIn("ModelIdentityPresentation.displayLabel", self.source)
        self.assertIn("OperationalStatePresenter.currentModel(", self.source)
        self.assertNotIn('currentModelDetectionStatus == "unmapped"', self.source)
        self.assertIn('detectionStatus == "unmapped"', operational_presenter)
        self.assertIn('case "unmapped": modeLabel = L10n.tr("未比较")', operational_presenter)
        self.assertIn(
            "isUnmappedCurrentModel: isUnmappedDetectedCurrentModel",
            self.source,
        )
        self.assertIn(
            "detectedCurrentModelIdentity: detectedCurrentModelIdentity",
            self.source,
        )
        self.assertIn("operationalPresentation.heroDecisionTitle", self.source)
        self.assertIn("operationalPresentation.heroDecisionReason", self.source)
        self.assertNotIn('detectedCurrentEffort == "ultra"', self.source)
        self.assertNotIn('detectedCurrentEffort == "max"', self.source)

    def test_settings_action_collapses_expanded_panel_before_opening_window(self) -> None:
        root = Path(__file__).resolve().parent.parent
        settings_button_source = (
            root / "Sources" / "Views" / "SettingsButton.swift"
        ).read_text(encoding="utf-8")
        header_source = self._section(
            self.source,
            "private var headerToolControls: some View {",
            "private func collapseHeaderLead",
        )
        open_settings_source = self._section(
            self.source,
            "private func openSettings()",
            "private func applyEntryDestination",
        )

        self.assertIn("SettingsButton(action: openSettings)", header_source)
        self.assertIn("onCollapse()", open_settings_source)
        self.assertIn("DispatchQueue.main.async", open_settings_source)
        self.assertIn("SettingsWindowController.shared.show()", open_settings_source)
        self.assertLess(
            open_settings_source.index("onCollapse()"),
            open_settings_source.index("SettingsWindowController.shared.show()"),
        )
        self.assertIn("let action: () -> Void", settings_button_source)
        self.assertIn("Button(action: action)", settings_button_source)
        self.assertNotIn("SettingsWindowController.shared.show()", settings_button_source)

    def test_current_model_identity_uses_canonical_effort_name(self) -> None:
        presentation_source = self._section(
            self.source,
            "private var currentModelPresentation: OperationalStatePresenter.CurrentModelPresentation {",
            "private var currentInUseCandidateOptions",
        )
        picker_source = self._section(
            self.source,
            "private func currentInUseCandidateRow(",
            "private var currentModelAutomaticDescription",
        )

        self.assertIn("candidateLabels[option.id] = option.currentModelLabel", presentation_source)
        self.assertIn("option.currentModelLabel", picker_source)
        self.assertIn("ModelIdentityPresentation.displayLabel(", self.operational_presenter_source)
        self.assertNotIn("option.candidate.displayName", picker_source)

    def test_radar_current_effort_comes_from_current_ranking_item(self) -> None:
        identity_source = self._section(
            self.source,
            "private var radarCurrentLeaderboardItem: RadarLeaderboardItem? {",
            "private var radarDecisionPresentation",
        )

        self.assertIn("store.radarLeaderboardItem(for: configurationID)", identity_source)
        self.assertIn("radarCurrentLeaderboardItem?.effort", identity_source)
        self.assertIn("radarCurrentLeaderboardItem?.modelName", identity_source)
        self.assertNotIn("store.glancePresentation.compactRight", identity_source)

    def test_evidence_detail_normalizes_effort_to_service_name(self) -> None:
        detail_source = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Views"
            / "CandidateEvidenceDetailView.swift"
        ).read_text(encoding="utf-8")

        visible_effort = self._section(
            detail_source,
            "private var visibleEffort: String? {",
            "private func evidenceSection",
        )
        self.assertIn(".lowercased()", visible_effort)
        self.assertIn('value != "default"', visible_effort)

    def test_overview_omits_effort_scope_controls(self) -> None:
        self.assertNotIn("每模型最佳档位", self.source)
        self.assertNotIn("全部档位", self.source)
        self.assertNotIn("扫描中显示全部档位", self.source)

    def test_legacy_runner_up_projection_is_removed_from_the_v2_surface(self) -> None:
        self.assertNotIn("LegacyHeroPresentation", self.source)
        self.assertNotIn("RadarPresenter.legacyHero(", self.source)
        self.assertNotIn("legacyDecisionIdentityStrip", self.source)
        self.assertNotIn("firstRunnerUpEntry", self.source)

    def test_failed_leaderboard_rows_remain_visible_without_a_recommendation(self) -> None:
        detail_source = self._section(
            self.source,
            "private var detailEntries: [DisplayEntry] {",
            "private var exportableLeaderboardEntries",
        )

        self.assertNotIn("bestCombination != nil", detail_source)
        self.assertIn("RadarEntryPresenter.entries(", detail_source)
        self.assertIn("return leaderboard.map", self.radar_entry_presenter_source)

    def test_panel_headers_fall_back_to_compact_content_without_overlapping(self) -> None:
        overview_header_source = self._section(
            self.source,
            "private var overviewPanelHeader: some View {",
            "private var overviewFullPanelHeader: some View {",
        )
        detail_header_source = self._section(
            self.source,
            "private var detailPanelHeader: some View {",
            "private var detailFullPanelHeader: some View {",
        )

        self.assertIn("ViewThatFits(in: .horizontal)", overview_header_source)
        self.assertIn("overviewCompactPanelHeader", overview_header_source)
        self.assertIn("if isEvidenceUpdating", overview_header_source)
        self.assertIn("Text(store.runtimeProgressText)", overview_header_source)
        self.assertIn("ViewThatFits(in: .horizontal)", detail_header_source)
        self.assertIn("detailCompactPanelHeader", detail_header_source)

    def test_overview_picker_header_reports_effective_scan_scope(self) -> None:
        self.assertIn(
            "scanModelPickerButton(title: overviewModelCountText)",
            self.source,
        )
        count_source = self._section(
            self.source,
            "private var overviewModelCountText: String {",
            "private var heroAccentColor",
        )
        self.assertIn(
            'return L10n.tr("%lld 个已选档位", scanExecutionCandidateCount)',
            count_source,
        )
        self.assertNotIn("detailEntries.count", count_source)
        self.assertNotIn("个可比较档位", count_source)
        self.assertNotIn("本轮 Q1～Q5", self.source)

        execution_count_source = self._section(
            self.source,
            "private var scanExecutionCandidateCount: Int {",
            "private var scanModelSelectionIsLocked",
        )
        self.assertIn('displayedEvaluationProfile?.id == "quick"', execution_count_source)
        self.assertIn("return 2", execution_count_source)
        self.assertIn("return scanModelPickerSelectedCount", execution_count_source)

        confirmation_source = self._section(
            self.source,
            "private func scanConfirmationMessage(",
            "private var exportErrorIsPresented",
        )
        self.assertIn("scanExecutionCandidateCount", confirmation_source)
        self.assertNotIn("scanModelPickerSelectedCount", confirmation_source)

    def test_zero_scan_candidate_state_is_driven_by_loaded_model_ingress(self) -> None:
        state_source = self._section(
            self.source,
            "private var modelIngressConfig: BridgeModelIngress? {",
            "private var detailEntries: [DisplayEntry] {",
        )

        self.assertIn("store.snapshot?.config.modelIngress", state_source)
        self.assertNotIn("settings.config", state_source)
        self.assertNotIn("settings.draftConfig", state_source)
        self.assertIn("OperationalStatePresenter.ingress(", state_source)
        self.assertIn(
            "enabledCandidateCount: store.snapshot?.settingsProjection.scanScope",
            state_source,
        )
        self.assertNotIn(r"targets.filter(\.enabled)", state_source)
        self.assertIn("private var requiresModelSetup: Bool", state_source)
        self.assertIn("ingressPresentation.requiresModelSetup", state_source)
        self.assertNotIn("enabledModelCandidateCount == 0", state_source)
        self.assertIn('"尚未接入模型"', self.operational_presenter_source)
        self.assertIn('"尚未选择扫描档位"', self.operational_presenter_source)

    def test_zero_scan_candidate_state_keeps_radar_surface(self) -> None:
        overview_source = self._section(
            self.source,
            "private var overviewRankingCard: some View {",
            "private func repairNotice",
        )

        self.assertIn("radarRankingHeader", overview_source)
        self.assertIn("radarLeaderboardEmptyState", overview_source)
        self.assertNotIn("ModelSetupEmptyState", overview_source)

    def test_official_radar_remains_visible_before_local_model_setup(self) -> None:
        ranking_source = self._section(
            self.source,
            "private var overviewRankingCard: some View {",
            "private func repairNotice",
        )
        sizing_source = self._section(
            self.source,
            "private var overviewRankingPreferredHeight: CGFloat {",
            "private var radarRankingHeader",
        )

        self.assertIn("radarRankingHeader", ranking_source)
        self.assertIn("ForEach(store.radarLeaderboardItems)", ranking_source)
        self.assertIn("showsRadarModelSetupCTA", ranking_source)
        self.assertIn(
            "let modelSetupNoticeHeight: CGFloat = showsRadarModelSetupCTA ? 74 : 0",
            sizing_source,
        )

    def test_zero_scan_candidate_primary_actions_open_model_ingress(self) -> None:
        current_model_source = self._section(
            self.source,
            "private var currentModelActionButton: some View {",
            "private func overviewActiveSessionRow(",
        )
        title_source = self._section(
            self.source,
            "private var scanControlActionTitle: String {",
            "private var stopScanActionTitle: String {",
        )
        action_source = self._section(
            self.source,
            "private func performScanControlAction() {",
            "private func presentEvidence(candidateID: String)",
        )

        self.assertIn("requiresModelSetup", current_model_source)
        self.assertIn("openModelIngress()", current_model_source)
        self.assertIn('return L10n.tr("设置模型")', title_source)
        self.assertIn("if requiresModelSetup", action_source)
        self.assertIn("openModelIngress()", action_source)
        self.assertIn("SettingsWindowController.shared.show(destination: .modelIngress)", self.source)

    def test_running_status_matches_by_candidate_id_instead_of_display_label(self) -> None:
        detail_source = self._section(
            self.source,
            "private var detailEntries: [DisplayEntry] {",
            "private var exportableLeaderboardEntries",
        )

        self.assertIn("runEntries: store.runEntries", detail_source)
        self.assertIn("runEntryByCandidateID", self.radar_entry_presenter_source)
        self.assertIn(
            "runEntryByCandidateID[leaderboardEntry.candidateId]",
            self.radar_entry_presenter_source,
        )
        self.assertNotIn("runEntryByLabel", self.radar_entry_presenter_source)
        self.assertIn("let candidateId: String", self.model_source)
        self.assertIn("let bestCandidateID", detail_source)
        self.assertIn(
            "bestCandidateID == leaderboardEntry.candidateId",
            self.radar_entry_presenter_source,
        )
        self.assertNotIn("bestLabel == leaderboardEntry.label", self.radar_entry_presenter_source)

    def test_store_correlates_cards_and_runtime_entries_by_candidate_id(self) -> None:
        store_source = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Model"
            / "SelectionStore.swift"
        ).read_text(encoding="utf-8")

        self.assertIn("byCandidateID[$0.candidateId]", store_source)
        self.assertNotIn("byLabel[$0.label]", store_source)

    def test_overview_decision_copy_consumes_operational_projection(self) -> None:
        decision_source = self._section(
            self.source,
            "private var heroDecisionTitleText: String {",
            "private var scanControlActionTitle",
        )
        self.assertIn("operationalPresentation.heroDecisionTitle", decision_source)
        self.assertNotIn("if ", decision_source)
        self.assertNotIn("switch ", decision_source)

    def test_overview_uses_only_the_v2_decision_projection(self) -> None:
        availability_source = self._section(
            self.source,
            "private var operationalAvailability: OperationalStatePresenter.Availability {",
            "private var expandedOperationalTone",
        )
        self.assertIn("OperationalStatePresenter.availability(", availability_source)
        self.assertIn("advisor: nil", availability_source)
        self.assertNotIn("operationalAdvisorInput", self.source)
        self.assertNotIn("personalAdvisorDecision", self.source)
        self.assertNotIn("personalAdvisorDisplayEntry", self.source)

    def test_overview_decision_title_is_a_compact_single_line_status(self) -> None:
        header_source = self._section(
            self.source,
            "private var heroDecisionHeader: some View {",
            "private var decisionIdentityStrip: some View {",
        )
        decision_source = self._section(
            self.source,
            "private var heroDecisionTitleText: String {",
            "private var scanControlActionTitle",
        )

        self.assertIn("Typography.heroDecision", header_source)
        self.assertIn(".lineLimit(1)", header_source)
        self.assertIn(".minimumScaleFactor(0.9)", header_source)
        self.assertIn("operationalPresentation.heroDecisionTitle", decision_source)
        self.assertNotIn("return \"", decision_source)

    def test_failure_fallback_is_projected_by_radar_presenter(self) -> None:
        reason_source = self._section(
            self.source,
            "private var heroDecisionReasonText: String {",
            "private var footerDataStatusText",
        )

        self.assertIn("operationalPresentation.heroDecisionReason", reason_source)
        self.assertNotIn('best.evidenceState == "retained_after_failure"', reason_source)
        self.assertNotIn('best.recommendationOutcome == "switch"', reason_source)

    def test_overview_hero_has_no_scan_cta_and_footer_owns_scan_controls(self) -> None:
        hero_source = self._section(
            self.source,
            "private var overviewHeroCard: some View {",
            "private var heroDecisionHeader: some View {",
        )
        footer_source = self._section(
            self.source,
            "private var panelFooter: some View {",
            "private var footerPageTabs: some View {",
        )

        self.assertIn("Button(action: performScanControlAction)", footer_source)
        self.assertIn("Text(scanControlActionTitle)", footer_source)
        self.assertIn("Button(stopScanActionTitle)", footer_source)
        self.assertIn(".disabled(isScanControlPending)", footer_source)
        self.assertIn(".fixedSize(horizontal: true, vertical: false)", footer_source)

    def test_running_scan_hides_restart_until_the_runtime_is_no_longer_active(self) -> None:
        restart_visibility = self._section(
            self.source,
            "private var showRestartButton: Bool {",
            "private var footerStatusColor",
        )

        self.assertIn("repairPresentation.showRestartButton", restart_visibility)
        self.assertIn("hasResumableRun", self.operational_presenter_source)
        self.assertIn("!input.runtimeIsRunning", self.operational_presenter_source)
        self.assertIn("input.pendingControlAction == nil", self.operational_presenter_source)



    def test_recommendation_panel_does_not_duplicate_scan_actions(self) -> None:
        self.assertNotIn('Text("建议动作")', self.source)

    def test_primary_action_maps_runtime_and_decision_states_to_real_operations(self) -> None:
        title_source = self._section(
            self.source,
            "private var scanControlActionTitle: String {",
            "private var stopScanActionTitle: String {",
        )
        action_source = self._section(
            self.source,
            "private func performScanControlAction() {",
            "private func presentEvidence(candidateID: String)",
        )
        self.assertIn('return L10n.tr("暂停中")', title_source)
        self.assertIn('return L10n.tr("\u6682\u505c")', title_source)
        self.assertIn('return L10n.tr("\u7ee7\u7eed\u626b\u63cf")', title_source)
        self.assertIn('return L10n.tr("开始扫描")', title_source)
        self.assertNotIn("selectedEvaluationProfile", title_source)
        self.assertIn("private var stopScanActionTitle: String", self.source)
        self.assertIn('return L10n.tr("停止中")', self.source)
        self.assertIn("private var isScanControlPending: Bool", self.source)
        self.assertIn("pendingScanConfirmation = .pause", action_source)
        self.assertIn("pendingScanConfirmation = .start", action_source)
        confirmed_action_source = self._section(
            self.source,
            "private func performConfirmedScanAction(",
            "private var isProvisionalResult: Bool {",
        )
        self.assertIn("store.pauseScan()", confirmed_action_source)
        self.assertIn("store.stopScan()", confirmed_action_source)
        self.assertIn("store.startRegularScan()", confirmed_action_source)
        self.assertNotIn("best.decisionState", action_source)

    def test_manual_scan_pause_and_stop_require_confirmation(self) -> None:
        body_source = self._section(
            self.source,
            "var body: some View {",
            "private var transitionChromeOpacity: Double {",
        )
        footer_source = self._section(
            self.source,
            "private var footerControls: some View {",
            "private var footerPageTabs: some View {",
        )

        self.assertIn("pendingScanConfirmation", body_source)
        self.assertIn("performConfirmedScanAction", body_source)
        self.assertIn("pendingScanConfirmation = .stop", footer_source)
        self.assertIn('Button(L10n.tr("取消"), role: .cancel)', body_source)

    def test_footer_has_no_manual_review_control(self) -> None:
        footer_source = self._section(
            self.source,
            "private var panelFooter: some View {",
            "private var footerPageTabs: some View {",
        )

        self.assertNotIn('Text("人工复核")', footer_source)

    def test_footer_places_functional_remote_refresh_next_to_data_status(self) -> None:
        footer_source = self._section(
            self.source,
            "private var panelFooter: some View {",
            "private var footerPageTabs: some View {",
        )

        status_position = footer_source.index("Text(footerStatus)")
        refresh_position = footer_source.index("radarReferenceRefreshControl")
        self.assertLess(status_position, refresh_position)
        self.assertIn("!comparisonDatasetSelection.showsLocalRepairControls", footer_source)

    def test_evidence_inspector_uses_standard_dismiss_interactions(self) -> None:
        detail_source = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Views"
            / "CandidateEvidenceDetailView.swift"
        ).read_text(encoding="utf-8")
        body_source = self._section(
            self.source,
            "var body: some View {",
            "@ViewBuilder\n    private var panelHeader",
        )

        self.assertNotIn(".sheet(item: $selectedEvidence)", body_source)
        self.assertIn("if let selectedEvidence", body_source)
        self.assertIn("evidenceBackdrop", body_source)
        self.assertIn("onDismiss: dismissEvidence", body_source)
        self.assertIn("onTapGesture(perform: dismissEvidence)", self.source)
        self.assertIn('Image(systemName: "xmark")', detail_source)
        self.assertIn('.accessibilityLabel(L10n.tr("关闭"))', detail_source)
        self.assertIn(".keyboardShortcut(.cancelAction)", detail_source)
        self.assertIn(".buttonStyle(IslandIconButtonStyle())", detail_source)
        self.assertNotIn("Circle()", detail_source)

    def test_evidence_inspector_keeps_scores_neutral(self) -> None:
        detail_source = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Views"
            / "CandidateEvidenceDetailView.swift"
        ).read_text(encoding="utf-8")
        header_source = self._section(
            detail_source,
            "private var evidenceHeader: some View {",
            "private var visibleEffort: String?",
        )

        self.assertIn(".foregroundStyle(IslandVisual.primaryText)", header_source)
        self.assertNotIn("IslandColor.liveTeal", header_source)

    def test_evidence_inspector_keeps_header_fixed_and_leaves_modal_margin(self) -> None:
        detail_source = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Views"
            / "CandidateEvidenceDetailView.swift"
        ).read_text(encoding="utf-8")
        body_source = self._section(
            detail_source,
            "var body: some View {",
            "private var evidenceHeader: some View",
        )

        self.assertIn("VStack(spacing: 0)", body_source)
        self.assertIn("evidenceHeader", body_source)
        self.assertIn("ScrollView", body_source)
        self.assertLess(body_source.index("evidenceHeader"), body_source.index("ScrollView"))
        self.assertIn("model.size.height * 0.82", self.source)

    def test_scan_control_does_not_depend_on_recommendation_copy(self) -> None:
        title_source = self._section(
            self.source,
            "private var scanControlActionTitle: String {",
            "private func performScanControlAction() {",
        )
        self.assertIn('return L10n.tr("开始扫描")', title_source)
        self.assertNotIn("selectedEvaluationProfile", title_source)
        self.assertNotIn("decisionActionLabel", title_source)
        self.assertNotIn("provisional_leader", title_source)

    def test_current_model_picker_supports_auto_and_manual_modes(self) -> None:
        operational_presenter = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Model"
            / "OperationalStatePresenter.swift"
        ).read_text(encoding="utf-8")
        action_button_source = self._section(
            self.source,
            "private var currentModelActionButton: some View {",
            "private func overviewActiveSessionRow(",
        )
        self.assertIn("let effectiveCurrentCandidateId: String?", self.model_source)
        self.assertIn("let currentModelSource: String", self.model_source)
        self.assertIn("let currentModelDetectedAt: String?", self.model_source)
        self.assertIn("let detectedActiveSessionCount: Int", self.model_source)
        self.assertIn("let detectedActiveModels: [BridgeDetectedCodexModel]", self.model_source)
        self.assertIn("let detectedActiveSessions: [BridgeDetectedCodexSession]", self.model_source)
        self.assertIn("let activeModelSessions: [BridgeDetectedModelSession]", self.model_source)
        self.assertIn("let currentModelMode: String", self.model_source)
        self.assertIn("OperationalStatePresenter.currentModel(", self.source)
        self.assertIn("currentModelModeLabel", action_button_source)
        self.assertIn("showsCurrentInUsePicker = true", action_button_source)
        self.assertIn('"指定当前模型"', action_button_source)
        self.assertIn('case "active_mixed": modeLabel = L10n.tr("多会话")', operational_presenter)
        self.assertIn('case "recent": modeLabel = L10n.tr("最近使用")', operational_presenter)
        self.assertNotIn(".disabled(isCurrentModelAutomaticallyDetected)", action_button_source)
        self.assertNotIn('Text("当前推荐")', action_button_source)
        self.assertNotIn("currentRecommendationDisplayText", self.source)
        picker_source = self._section(
            self.source,
            "private var currentInUsePicker: some View {",
            "private var heroConfidenceChip: some View {",
        )
        self.assertIn("settings.useAutomaticCurrentModel()", picker_source)
        self.assertIn("settings.setCurrentDefault(candidateID: option.id)", picker_source)
        self.assertIn('Text("手动指定")', picker_source)

    def test_selection_store_refreshes_current_codex_usage_without_reentry(self) -> None:
        store_source = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Model"
            / "SelectionStore.swift"
        ).read_text(encoding="utf-8")
        self.assertIn("currentModelRefreshTimer", store_source)
        self.assertIn("activeRuntimeRefreshInterval: TimeInterval = 3", store_source)
        self.assertIn("idleCurrentModelRefreshInterval: TimeInterval = 30", store_source)
        self.assertIn("let refreshInterval = isRuntimeRefreshActive", store_source)
        self.assertIn("repeats: false", store_source)
        self.assertIn("armCurrentModelRefreshTimer()", store_source)

    def test_candidate_evidence_inspector_is_shared_by_overview_and_history(self) -> None:
        detail_source = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Views"
            / "CandidateEvidenceDetailView.swift"
        )
        self.assertTrue(detail_source.exists())
        contents = detail_source.read_text(encoding="utf-8")
        for section in ("身份", "当前有效成绩", "最新尝试", "逐题结果", "旧成绩说明"):
            self.assertIn(f'Text("{section}")', contents)
        self.assertIn("presentEvidence(candidateID:", self.source)
        self.assertIn("onPresentEvidence", self.source)
        self.assertIn("CandidateEvidenceDetailView", self.source)

    def test_leaderboard_bridge_decodes_evidence_identity_and_attempt_timestamps(self) -> None:
        for field in (
            "sourceId", "connectionId", "familyId", "variantId", "modelId",
            "latestAttemptAt", "latestAttemptErrorCategory", "validRunId",
            "validCompletedAt", "questionPackVersion", "elapsedSeconds",
        ):
            self.assertIn(f"let {field}:", self.model_source)

    def test_expanded_view_has_no_sub_micro_user_text(self) -> None:
        for size in (7, 8, 9):
            self.assertNotIn(f".font(.system(size: {size}", self.source)

    def test_resumable_state_is_explicit_and_footer_offers_resume(self) -> None:
        projection_source = self._section(
            self.source,
            "private var operationalPresentation: OperationalStatePresenter.Presentation {",
            "private var headerDetailText",
        )
        scan_title_source = self._section(
            self.source,
            "private var scanControlActionTitle: String {",
            "private func performScanControlAction() {",
        )

        self.assertIn("hasResumableRun: store.snapshot?.runtime.hasResumableRun == true", projection_source)
        self.assertIn("operationalPresentation.heroDecisionTitle", self.source)
        self.assertIn('return L10n.tr("暂停")', scan_title_source)
        self.assertIn('return L10n.tr("继续扫描")', scan_title_source)


    def test_overview_hero_copy_uses_radar_presenter_for_recommendation_basis(self) -> None:
        reason_source = self._section(
            self.source,
            "private var heroDecisionReasonText: String {",
            "private var footerDataStatusText",
        )
        self.assertIn("operationalPresentation.heroDecisionReason", reason_source)
        self.assertNotIn("best.recommendationOutcome", reason_source)
        self.assertNotIn("best.decisionReason", reason_source)
        self.assertNotIn("Q5 未触发", self.source)

    def test_v2_decision_copy_has_no_legacy_runner_up_evidence_path(self) -> None:
        reason_source = self._section(
            self.source,
            "private var heroDecisionReasonText: String {",
            "private var footerDataStatusText",
        )

        self.assertIn("operationalPresentation.heroDecisionReason", reason_source)
        self.assertNotIn("RadarPresenter.legacyHero(", self.source)
        self.assertNotIn("heroEvidenceReasons", self.source)
        self.assertNotIn("currentSwitchReasonText", self.source)
        self.assertNotIn("firstRunnerUpEntry", self.source)

    def test_overview_hero_does_not_show_metadata_labels_near_footer(self) -> None:
        hero_source = self._section(
            self.source,
            "private var overviewHeroCard: some View {",
            "private var heroDecisionHeader: some View {",
        )
        self.assertNotIn('Text("题包")', hero_source)
        self.assertNotIn('Text("数据")', hero_source)

    def test_footer_offers_one_click_retry_for_all_timed_out_questions(self) -> None:
        root = Path(__file__).resolve().parent.parent
        store_source = (root / "Sources" / "Model" / "SelectionStore.swift").read_text(
            encoding="utf-8"
        )
        bridge_source = (root / "Sources" / "Model" / "NativeBridgeClient.swift").read_text(
            encoding="utf-8"
        )
        footer_source = self._section(
            self.source,
            "private var panelFooter: some View {",
            "private var footerPageTabs: some View {",
        )

        self.assertIn("func startTimedOutRepair(runID: String, candidateIDs: [String])", store_source)
        self.assertIn("func startTimedOutRepair(", bridge_source)
        self.assertIn('"repair-timeouts"', bridge_source)
        self.assertIn("retryTimedOutQuestions", footer_source)
        self.assertIn("timedOutQuestionCount", footer_source)

    def test_footer_offers_one_click_retry_for_all_hard_failed_questions(self) -> None:
        footer_source = self._section(
            self.source,
            "private var panelFooter: some View {",
            "private var footerPageTabs: some View {",
        )
        eligibility_source = self._section(
            self.source,
            "private var canRetryFailedQuestions: Bool {",
            "private var failedRepairNoticeTitle: String {",
        )

        self.assertIn("if canRetryFailedQuestions", footer_source)
        self.assertIn("Button(action: retryFailedQuestions)", footer_source)
        self.assertIn('Text(L10n.tr("重试全部失败 %d", repairableQuestionCount))', footer_source)
        self.assertIn("repairPresentation.canRetryFailedQuestions", eligibility_source)
        self.assertIn("repairPresentation.repairableQuestionCount", eligibility_source)
        self.assertNotIn("runtime.hasResumableRun != true", eligibility_source)

    def test_local_repair_scope_follows_the_displayed_source_and_current_candidates(self) -> None:
        repair_source = self._section(
            self.source,
            "private var repairPresentation: OperationalStatePresenter.RepairPresentation {",
            "private var isBatchRepairRunning",
        )

        self.assertIn(
            "showsLocalRepairControls: comparisonDatasetSelection.showsLocalRepairControls",
            repair_source,
        )
        self.assertIn("configuredCandidateIDs: store.snapshot?.settingsProjection.scanScope", repair_source)
        self.assertIn(".regularCandidateIds ?? []", repair_source)
        self.assertIn("runCandidateIDs: store.snapshot?.dashboard.runMetadata", repair_source)
        self.assertIn(".requestedCandidateIds ?? []", repair_source)

    def test_q1_q5_progress_uses_one_run_entry_total(self) -> None:
        presenter = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Model"
            / "OperationalStatePresenter.swift"
        ).read_text(encoding="utf-8")

        self.assertIn("OperationalStatePresenter.progress(", self.radar_entry_presenter_source)
        self.assertIn("runEntry?.attemptsCompleted", self.radar_entry_presenter_source)
        self.assertIn("runEntry?.attemptsPerTarget", self.radar_entry_presenter_source)
        self.assertNotIn(
            "result.semanticTotal == semantic.scoreMax",
            self.radar_entry_presenter_source,
        )
        self.assertIn("result.semanticTotal == question.scoreMax", presenter)

    def test_current_score_contract_is_projected_by_radar_presenter(self) -> None:
        detail_entries_source = self._section(
            self.source,
            "private var detailEntries: [DisplayEntry] {",
            "private var exportableLeaderboardEntries",
        )
        self.assertIn("RadarEntryPresenter.entries(", detail_entries_source)
        self.assertIn("RadarPresenter.evidenceAvailability(", self.radar_entry_presenter_source)
        self.assertIn("RadarPresenter.QuestionContractInput(", self.radar_entry_presenter_source)
        self.assertIn("RadarPresenter.QuestionResultContractInput(", self.radar_entry_presenter_source)
        self.assertIn(
            "let evidenceAvailability: RadarPresenter.EvidenceAvailabilityPresentation",
            self.radar_entry_presenter_source,
        )
        self.assertNotIn("semantic_q1_q5_equal_v2", self.source)
        self.assertNotIn("currentQuestionResult(", self.radar_entry_presenter_source)

    def test_leaderboard_export_consumes_displayed_authoritative_rows(self) -> None:
        export_source = self._section(
            self.source,
            "private var exportableLeaderboardEntries",
            "private var leaderboardExportOmittedCount",
        )

        self.assertIn("store.radarLeaderboardItems.filter", export_source)
        self.assertIn("$0.score != nil", export_source)
        self.assertNotIn("detailEntries", export_source)
        self.assertNotIn("evidenceAvailability", export_source)

    def test_legacy_pack_visibility_consumes_presenter_availability(self) -> None:
        availability_source = self._section(
            self.source,
            "private var operationalAvailability: OperationalStatePresenter.Availability {",
            "private var expandedOperationalTone",
        )
        self.assertIn(
            "$0.evidenceAvailability.canDisplayCurrentQuestionScores",
            availability_source,
        )
        self.assertIn("OperationalStatePresenter.availability(", availability_source)
        self.assertNotIn("scoringMode ==", self.source)
        self.assertNotIn("isCurrentPackComparable ||", self.source)
        self.assertNotIn("private var shouldHideLegacyOverviewScores", self.source)
        self.assertNotIn("private var isAwaitingCurrentScoreScan", self.source)

    def test_expanded_pages_use_overview_and_comparison_surfaces(self) -> None:
        content_source = self._section(
            self.source,
            "private var pagedContent: some View {",
            "private var overviewPage: some View {",
        )
        footer_source = self._section(
            self.source,
            "private var footerPageTabs: some View {",
            "private func footerPageTab",
        )

        self.assertIn("pageIndex", content_source)
        self.assertIn("detailPage", content_source)
        self.assertNotIn(".offset(x:", content_source)
        self.assertIn("if pageIndex == 0", content_source)
        self.assertIn("overviewPage", content_source)
        self.assertIn("} else {", content_source)
        self.assertIn(".transition(.opacity)", content_source)
        self.assertIn("footerPageTab(title: L10n.Overview.radarTab, index: 0)", footer_source)
        self.assertIn("footerPageTab(title: L10n.Overview.comparisonTab, index: 1)", footer_source)
        self.assertIn("ComparisonPage(", self.source)

    def test_evaluation_profiles_drive_scan_scope_and_provisional_evidence(self) -> None:
        root = Path(__file__).resolve().parent.parent
        store_source = (root / "Sources" / "Model" / "SelectionStore.swift").read_text(
            encoding="utf-8"
        )

        selector_source = self._section(
            self.source,
            "private var evaluationProfileSelector: some View {",
            "private func presentEvidence(candidateID: String)",
        )
        self.assertIn("private var evaluationProfileSelector: some View", self.source)
        self.assertIn("store.evaluationProfiles", self.source)
        self.assertIn(".popover(isPresented: $showsEvaluationProfilePopover", selector_source)
        self.assertIn("evaluationProfilePopover", selector_source)
        self.assertIn("evaluationProfileOption(profile)", selector_source)
        self.assertIn("profile.label", selector_source)
        self.assertIn("profile.questionCount", selector_source)
        self.assertIn("profile.summary", selector_source)
        self.assertIn("store.isEvaluationProfileSelectionLocked", selector_source)
        self.assertNotIn(".pickerStyle(.segmented)", selector_source)
        self.assertNotIn("Menu {", selector_source)
        compact_selector_label = selector_source.split(
            ".popover(isPresented: $showsEvaluationProfilePopover", 1
        )[0]
        self.assertNotIn("profile.questionCount", compact_selector_label)
        self.assertIn(".fixedSize(horizontal: true, vertical: false)", compact_selector_label)
        self.assertIn(".frame(width: 172, alignment: .leading)", compact_selector_label)
        self.assertIn("Button(action: performEvaluationProfileUpgradeAction)", self.source)
        self.assertIn("private var isProvisionalResult: Bool", self.source)
        self.assertIn("func selectEvaluationProfile", store_source)
        self.assertIn("func upgradeCurrentEvaluationProfile", store_source)

    def test_fixed_radar_control_copy_keeps_its_intrinsic_width(self) -> None:
        control_bar = self._section(
            self.source,
            "private var radarControlBar: some View {",
            "private var radarSourceControl: some View {",
        )

        self.assertEqual(
            control_bar.count(".fixedSize(horizontal: true, vertical: false)"),
            2,
        )

    def test_profile_upgrade_requires_an_explicit_decision_when_models_changed(self) -> None:
        root = Path(__file__).resolve().parent.parent
        store_source = (root / "Sources" / "Model" / "SelectionStore.swift").read_text(
            encoding="utf-8"
        )
        footer_source = self._section(
            self.source,
            "private var panelFooter: some View {",
            "private var footerPageTabs: some View {",
        )
        decision_source = self._section(
            self.source,
            "private var evaluationProfileScopePresentation:",
            "private var evaluationProfileSelector: some View {",
        )

        self.assertIn("performEvaluationProfileUpgradeAction", footer_source)
        self.assertIn(
            ".disabled(isScanControlPending || settings.isSaving)", footer_source
        )
        self.assertIn("requestedCandidateIds", decision_source)
        self.assertIn("regularCandidateIds", decision_source)
        self.assertIn(".subtracting", self.profile_scope_presenter_source)
        self.assertIn("showsEvaluationProfileDecision", decision_source)
        self.assertIn("delta.currentCount > 0", self.source)
        self.assertIn(
            "Button(evaluationProfileScopePresentation.currentSelectionFullScanTitle)",
            self.source,
        )
        self.assertIn(
            "Button(evaluationProfileScopePresentation.originalRoundUpgradeTitle)",
            self.source,
        )
        self.assertIn("store.upgradeCurrentEvaluationProfile()", self.source)
        self.assertIn("store.upgradeCurrentSelectionEvaluationProfile(", self.source)
        self.assertIn("func upgradeCurrentSelectionEvaluationProfile(", store_source)
        self.assertIn("evaluationProfileID: profile.id", store_source)
        self.assertIn("candidateIDs: candidateIDs", store_source)
        self.assertIn("upgradeFromRunID: metadata.runId", store_source)
        self.assertIn("补全当前", self.profile_scope_presenter_source)
        self.assertIn("复用已有题目结果", self.profile_scope_presenter_source)



    def test_expanded_shell_has_visible_collapse_and_layered_escape_handling(self) -> None:
        header_source = self._section(
            self.source,
            "@ViewBuilder\n    private var panelHeader",
            "private var overviewPanelHeader",
        )
        body_source = self._section(
            self.source,
            "var body: some View {",
            "private func applyEntryDestination",
        )
        picker_source = self._section(
            self.source,
            "private var currentInUsePicker: some View {",
            "private func currentInUseCandidateRow",
        )
        self.assertIn("let onCollapse: () -> Void", self.source)
        self.assertIn('Image(systemName: "chevron.up")', header_source)
        self.assertIn(".help(L10n.Common.collapse)", header_source)
        self.assertIn(".onExitCommand", body_source)
        self.assertIn("handleExitCommand()", body_source)
        self.assertIn("selectedEvidence = nil", self.source)
        self.assertIn("showsCurrentInUsePicker = false", self.source)
        self.assertIn("onCollapse()", self.source)
        self.assertIn("collapseHeaderButton {", header_source)
        self.assertIn("private func collapseHeaderButton<Content: View>", self.source)
        self.assertIn("Button(action: onCollapse)", self.source)
        self.assertIn(".contentShape(Rectangle())", self.source)
        self.assertIn(".onExitCommand", picker_source)
        self.assertIn("showsCurrentInUsePicker = false", picker_source)

    def test_expanded_shell_content_is_staged_after_the_shell_opens(self) -> None:
        root_source = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Views"
            / "IslandRootView.swift"
        ).read_text(encoding="utf-8")
        expanded_section = self._section(
            root_source,
            "if model.state == .expanded && isExpandedContentMounted {",
            "        }\n        .frame(width: compactSurfaceWidth, height: model.size.height)"
        )
        self.assertIn("isTransitionContentVisible: isExpandedContentVisible", expanded_section)
        self.assertIn(
            "preservesDecisionEvidenceDuringTransition: isSessionPanelTransitioningToExpanded",
            expanded_section,
        )
        self.assertIn(".allowsHitTesting(isExpandedContentVisible)", expanded_section)
        self.assertNotIn(".opacity(isExpandedContentVisible ? 1 : 0)", expanded_section)
        self.assertNotIn(".offset(y:", expanded_section)
        self.assertNotIn(".transition(", expanded_section)

    def test_expanded_transition_keeps_identity_ahead_of_page_chrome(self) -> None:
        body_source = self._section(
            self.source,
            "var body: some View {",
            "private func handleExitCommand",
        )
        hero_source = self._section(
            self.source,
            "private var overviewHeroCard: some View {",
            "private var heroDecisionHeader",
        )
        identity_source = self._section(
            self.source,
            "private var radarDecisionIdentityStrip: some View {",
            "private var radarCurrentIdentityLine",
        )

        self.assertIn("let isTransitionContentVisible: Bool", self.source)
        self.assertIn("let preservesDecisionEvidenceDuringTransition: Bool", self.source)
        self.assertGreaterEqual(body_source.count(".opacity(transitionChromeOpacity)"), 2)
        self.assertIn(".opacity(transitionChromeOpacity)", hero_source)
        self.assertIn(".opacity(transitionDecisionEvidenceOpacity)", identity_source)
        self.assertIn("radarCurrentIdentityLine", identity_source)
        current_line = identity_source.split("radarCurrentIdentityLine", 1)[1].split(
            "if let candidate", 1
        )[0]
        self.assertNotIn(".opacity(", current_line)

    def test_pages_crossfade_and_reduce_motion_stops_the_transition(self) -> None:
        content_source = self._section(
            self.source,
            "private var pagedContent: some View {",
            "private var overviewPage: some View {",
        )
        self.assertIn("@Environment(\\.accessibilityReduceMotion)", self.source)
        self.assertNotIn("if reduceMotion", content_source)
        self.assertIn("if pageIndex == 0", content_source)
        self.assertEqual(content_source.count(".transition(.opacity)"), 2)
        self.assertNotIn(".opacity(pageIndex == 0 ? 1 : 0)", content_source)
        self.assertNotIn(".opacity(pageIndex == 1 ? 1 : 0)", content_source)
        self.assertIn(".animation(reduceMotion ? nil : .easeOut(duration: 0.18)", content_source)

    def test_footer_prioritizes_operational_status_then_shows_idle_freshness(self) -> None:
        footer_source = self._section(
            self.source,
            "private var panelFooter: some View {",
            "private var footerPageTabs",
        )
        color_source = self._section(
            self.source,
            "private var footerStatusColor: Color {",
            "private var showRestartButton",
        )

        self.assertIn("if let footerStatus = footerDataStatusText", footer_source)
        self.assertIn("Text(footerStatus)", footer_source)
        self.assertIn("footerStatusColor", footer_source)
        self.assertNotIn("Circle()", footer_source)
        self.assertIn(".font(Typography.micro)", footer_source)
        self.assertIn("operationalPresentation.footerDataStatusText", self.source)
        for redundant_copy in ("部分结果异常", "当前推荐与排名", "题包已更新"):
            self.assertNotIn(redundant_copy, footer_source)
        self.assertIn("switch operationalPresentation.footerTone", color_source)
        self.assertIn("case .warning:", color_source)
        self.assertIn("IslandColor.alertAmber", color_source)
        self.assertIn("IslandColor.alertRed", color_source)
        self.assertIn("IslandVisual.tertiaryText", color_source)

    def test_footer_matches_the_html_three_zone_hierarchy_and_keeps_tools_stable(self) -> None:
        footer_source = self._section(
            self.source,
            "private var panelFooter: some View {",
            "private var footerPageTabs",
        )

        self.assertIn("ZStack", footer_source)
        self.assertLess(
            footer_source.index("Text(footerStatus)"),
            footer_source.index("footerPageTabs"),
        )
        self.assertIn("footerControls", footer_source)
        self.assertIn(".padding(.horizontal, expandedContentHorizontalInset)", footer_source)
        self.assertIn(".padding(.bottom, LayoutRhythm.standard)", footer_source)
        self.assertNotIn("SettingsButton(action: openSettings)", footer_source)
        self.assertNotIn("QuitButton()", footer_source)
        self.assertNotIn('Image(systemName: "square.and.arrow.up")', footer_source)
        self.assertNotIn("if pageIndex == 0", footer_source)

    def test_both_pages_remove_repeated_static_context(self) -> None:
        operational_presenter = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Model"
            / "OperationalStatePresenter.swift"
        ).read_text(encoding="utf-8")
        control_bar = self._section(
            self.source,
            "private var radarControlBar: some View {",
            "private var radarSourceControl",
        )
        candidate_control = self._section(
            self.source,
            "private var comparisonCandidateControl: some View {",
            "private func comparisonChoiceLabel",
        )
        decision_summary = self._section(
            self.source,
            "private func comparisonDecisionSummary(",
            "private var comparisonDecisionPresentation",
        )
        details_bar = self._section(
            self.source,
            "private var evaluationDetailsBar: some View {",
            "private func evaluationDetailsContent(",
        )

        self.assertNotIn("private var footerSourceStatusText", self.source)
        self.assertIn("private static func sourceStatus(", operational_presenter)
        self.assertNotIn("当前采用本机实测", operational_presenter)
        self.assertNotIn("footerSourceStatusText", control_bar)
        self.assertNotIn("Circle()", control_bar)
        self.assertNotIn("private var radarRankingContext", self.source)
        self.assertNotIn("Text(radarSurfacePresentation.rankingContext)", self.source)
        self.assertNotIn("Text(radarSurfacePresentation.questionPackVersion)", self.source)
        self.assertIn("candidateItem?.displayName", candidate_control)
        self.assertNotIn(": automaticCandidateMenuLabel,", candidate_control)
        self.assertNotIn("comparisonBasisFooter", decision_summary)
        self.assertNotIn("收起详情，恢复趋势与置信依据", details_bar)
        self.assertNotIn("五题明细、Token、模型身份与计价口径", details_bar)

    def test_realized_benefit_keeps_time_and_token_dollars_separate(self) -> None:
        benefit_projection = self._section(
            self.source,
            "private var realizedBenefitPresentation: ComparisonPresenter.RealizedBenefitPresentation? {",
            "private func realizedBenefitMetric(",
        )
        usage_source = self._section(
            self.source,
            "private func realUsageSection(",
            "private func usageRow(",
        )

        self.assertIn("ComparisonPresenter.realizedBenefit(", benefit_projection)
        self.assertIn("modelWaitDeltaMs", benefit_projection)
        self.assertIn("referenceCostDeltaUsd", benefit_projection)
        self.assertIn("observedWorkUnitCount", benefit_projection)
        self.assertIn("referenceCostWorkUnitCount", benefit_projection)
        self.assertIn("modelWaitWorkUnitCount", benefit_projection)
        self.assertIn('Text(L10n.tr("ModelDial 记录到的变化"))', usage_source)
        self.assertIn('Text(L10n.tr("近期归因"))', usage_source)
        self.assertIn("Text(benefit.title)", usage_source)
        self.assertIn('title: L10n.tr("等待时间")', usage_source)
        self.assertIn('title: L10n.tr("参考费用")', usage_source)
        self.assertIn("value: benefit.modelWaitText", usage_source)
        self.assertIn("value: benefit.referenceCostText", usage_source)
        self.assertIn("Text(benefit.noteText)", usage_source)
        self.assertIn(".help(benefit.helpText)", usage_source)
        self.assertNotIn('summary.status == "unavailable"', usage_source)
        self.assertIn("VStack(alignment: .leading, spacing: 3)", self.source)
        self.assertIn(".minimumScaleFactor(0.85)", self.source)
        self.assertNotIn("参考额度", usage_source)
        self.assertNotIn("完成工作单元", usage_source)
        self.assertNotIn("Token 等价美元", usage_source)
        self.assertNotIn('decision?.decision == "recommend"', benefit_projection)

    def test_default_pages_omit_realized_benefit_while_comparison_details_keep_it(self) -> None:
        hero_source = self._section(
            self.source,
            "private var overviewHeroCard: some View {",
            "private var heroDecisionHeader: some View {",
        )
        decision_source = self._section(
            self.source,
            "private func comparisonDecisionSummary(",
            "private var comparisonDecisionPresentation",
        )
        usage_source = self._section(
            self.source,
            "private func realUsageSection(",
            "private func usageRow(",
        )
        self.assertNotIn("radarBenefitReceipt", hero_source)
        self.assertNotIn("recommendationUse.valueSummary", hero_source)
        self.assertNotIn("RadarPresenter.value(", self.source)
        self.assertNotIn("realizedBenefitPresentation", decision_source)
        self.assertIn("Text(benefit.title)", usage_source)

    def test_quality_guard_and_reference_cost_semantics_are_presenter_owned(self) -> None:
        decision_source = self._section(
            self.source,
            "private func comparisonDecisionSummary(",
            "private var comparisonDecisionPresentation",
        )
        row_source = self._section(
            self.source,
            "private struct RadarLeaderboardRow: View {",
            "private struct ComparisonScoreTrendChart",
        )

        self.assertIn("ComparisonPresenter.qualityGuard(", decision_source)
        self.assertIn("sameConfiguration: current.id == candidate.id", decision_source)
        self.assertIn("pairwiseComparable: presentation.evidence?.pairwiseComparable", decision_source)
        self.assertNotIn("guardStatus", decision_source)
        self.assertIn("RadarPresenter.referenceCost(", row_source)
        self.assertIn("Text(costPresentation.text)", row_source)
        self.assertIn(".help(costPresentation.helpText)", row_source)
        self.assertNotIn("switch entry.costCoverage", row_source)

    def test_route_evidence_is_kept_in_details_only(self) -> None:
        decision_source = self._section(
            self.source,
            "private func comparisonDecisionSummary(",
            "private var comparisonDecisionPresentation",
        )
        evidence_source = self._section(
            self.source,
            "private func evidenceSection(",
            "private func evidenceRow(",
        )
        self.assertNotIn("comparisonBasisFooter", decision_source)
        self.assertNotIn("ComparisonPresenter.routeBasis(", self.source)
        self.assertIn("ConfigurationEvidencePresenter", self.source)
        self.assertIn(
            'evidenceRow(L10n.tr("路线"), configuration.routeEvidenceText)',
            evidence_source,
        )





    def test_rows_do_not_require_a_best_combination(self) -> None:
        detail_entries_source = self._section(
            self.source,
            "private var detailEntries: [DisplayEntry] {",
            "private var exportableLeaderboardEntries",
        )

        self.assertIn("RadarEntryPresenter.entries(", detail_entries_source)
        self.assertIn("return leaderboard.map", self.radar_entry_presenter_source)
        self.assertNotIn("bestCombination != nil", detail_entries_source)






    def test_leaderboard_bridge_decodes_equal_score_facets(self) -> None:
        self.assertIn("struct BridgeScoreFacet: Decodable", self.model_source)
        leaderboard_source = self._section(
            self.model_source,
            "struct BridgeLeaderboardEntry: Decodable, Identifiable {",
            "struct BridgeBestCombination: Decodable {",
        )
        self.assertIn("let scoreFacets: [BridgeScoreFacet]", leaderboard_source)
        self.assertIn("case scoreFacets", leaderboard_source)
        self.assertIn(
            "decodeIfPresent([BridgeScoreFacet].self, forKey: .scoreFacets) ?? []",
            leaderboard_source,
        )















    def test_statistics_header_matches_stats_first_html_context(self) -> None:
        header_source = self._section(
            self.source,
            "private var detailPanelHeader: some View {",
            "private var pagedContent: some View {",
        )
        primary_source = self._section(
            self.source,
            "private var detailHeaderPrimaryText: String {",
            "private var overviewModelCountText: String {",
        )
        self.assertNotIn("Text(currentBestShortLabel)", header_source)
        self.assertNotIn('Text("结果：\\(currentBestShortLabel)")', header_source)
        self.assertEqual(
            header_source.count("scanModelPickerButton(title: detailModelCountText"),
            2,
        )
        self.assertIn("private var detailModelCountText: String", self.source)
        self.assertIn("overviewModelCountText", self.source)
        self.assertNotIn('decision.decision == "recommend"', primary_source)
        self.assertNotIn('return "当前与建议"', primary_source)
        self.assertIn('return L10n.tr("当前与候选")', primary_source)
        self.assertIn('return L10n.tr("当前配置")', primary_source)
        self.assertNotIn('Text("统计")', header_source)

    def test_page_header_count_opens_shared_regular_scan_model_picker(self) -> None:
        header_source = self._section(
            self.source,
            "private var overviewFullPanelHeader: some View {",
            "private var pagedContent: some View {",
        )
        picker_source = self._section(
            self.source,
            "private func scanModelPickerButton(",
            "private var pagedContent: some View {",
        )

        self.assertIn("@State private var showsScanModelPicker = false", self.source)
        self.assertEqual(
            header_source.count("scanModelPickerButton(title: overviewModelCountText"),
            2,
        )
        self.assertEqual(
            header_source.count("scanModelPickerButton(title: detailModelCountText"),
            2,
        )
        self.assertIn(".popover(isPresented: $showsScanModelPicker", picker_source)
        self.assertIn('Text(L10n.tr("扫描档位"))', picker_source)
        self.assertIn("settings.setModelCandidateEnabled(", picker_source)
        self.assertIn("scanModelPickerConnections", picker_source)
        self.assertIn("scanModelSelectionIsLocked", picker_source)
        self.assertNotIn("source.enabled", picker_source)
        self.assertNotIn("connection.enabled", picker_source)
        self.assertIn("修改后自动保存，并同步到两页。", picker_source)
        self.assertIn("candidate.pickerLabel", picker_source)

    def test_header_title_and_free_space_still_collapse_around_model_picker(self) -> None:
        header_source = self._section(
            self.source,
            "private var overviewFullPanelHeader: some View {",
            "private func scanModelPickerButton(",
        )
        lead_source = self._section(
            self.source,
            "private func collapseHeaderButton<Content: View>(",
            "private var overviewPanelHeader: some View {",
        )

        self.assertEqual(
            header_source.count("scanModelPickerButton(title:"),
            4,
        )
        self.assertIn("Button(action: onCollapse)", lead_source)
        self.assertIn("content()", lead_source)
        self.assertIn(".frame(maxWidth: .infinity, alignment: .leading)", lead_source)
        self.assertIn(".contentShape(Rectangle())", lead_source)







    def test_effort_level_picker_is_removed_from_both_pages(self) -> None:
        self.assertNotIn("每模型最佳档位", self.source)
        self.assertNotIn("全部档位", self.source)




if __name__ == "__main__":
    unittest.main()
