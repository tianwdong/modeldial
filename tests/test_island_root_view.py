from __future__ import annotations

from pathlib import Path
import unittest


class IslandRootViewSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).resolve().parent.parent
        self.source = (
            root
            / "Sources"
            / "Views"
            / "IslandRootView.swift"
        ).read_text(encoding="utf-8")
        self.compact_source = (
            root / "Sources" / "Views" / "CompactPillView.swift"
        ).read_text(encoding="utf-8")
        self.panel_source = (
            root / "Sources" / "Views" / "CompactSessionPanelView.swift"
        ).read_text(encoding="utf-8")
        self.expanded_source = (
            root / "Sources" / "Views" / "ExpandedSelectionView.swift"
        ).read_text(encoding="utf-8")
        self.animations_source = (
            root / "Sources" / "Theme" / "Animations.swift"
        ).read_text(encoding="utf-8")
        self.store_source = (
            root / "Sources" / "Model" / "SelectionStore.swift"
        ).read_text(encoding="utf-8")

    def test_island_root_has_compact_session_panel_and_expanded_content(self) -> None:
        self.assertIn("CompactPillView", self.source)
        self.assertIn("CompactSessionPanelView", self.source)
        self.assertNotIn("PeekPillView", self.source)
        self.assertNotIn("PeekGlanceOverlay", self.source)
        self.assertNotIn("CompactSignalOverlay", self.source)
        self.assertNotIn("LeftPeekOverlay", self.source)
        self.assertNotIn("RightPeekOverlay", self.source)

    def test_island_root_uses_one_adaptive_surface_without_redundant_material_halo(self) -> None:
        self.assertNotIn(".fill(.ultraThinMaterial)", self.source)
        self.assertIn("GlowLayer(", self.source)
        self.assertIn("reduceTransparency: reduceTransparency", self.source)
        self.assertIn("increasedContrast: colorSchemeContrast == .increased", self.source)

    def test_compact_identity_and_status_do_not_use_metadata_sized_type(self) -> None:
        section = self.compact_source
        self.assertIn("Typography.label", section)
        self.assertNotIn("Typography.micro", section)

    def test_compact_identity_text_does_not_inherit_ambient_status_color(self) -> None:
        self.assertIn("presentation.compactLeftTextRole", self.compact_source)
        self.assertIn("presentation.compactRightTextRole", self.compact_source)
        self.assertIn("case .identityPrimary:", self.compact_source)
        self.assertIn("return IslandVisual.primaryText", self.compact_source)
        self.assertIn("case .identitySecondary:", self.compact_source)
        self.assertIn("return IslandVisual.secondaryText", self.compact_source)
        self.assertIn("case .status:", self.compact_source)
        self.assertIn("return presentation.tone.foregroundColor", self.compact_source)

    def test_neutral_state_has_no_colored_ambient_glow(self) -> None:
        glow_color = self.source.split("private var glowColor: Color {", 1)[1].split(
            "private struct GlowLayer", 1
        )[0]
        neutral_case = glow_color.split("case .neutral:", 1)[1].split("case .active:", 1)[0]
        self.assertIn("return .clear", neutral_case)
        self.assertNotIn("IslandColor.cobalt", neutral_case)

    def test_compact_glance_only_consumes_resolved_presentation(self) -> None:
        section = self.compact_source
        self.assertIn("let presentation: GlancePresentation", section)
        self.assertIn("presentation.compactLeft", section)
        self.assertIn("presentation.compactRight", section)
        self.assertIn("presentation.compactLeadingSymbol", section)
        self.assertIn("Image(systemName: symbol)", section)
        self.assertIn("HStack(spacing: LayoutRhythm.micro)", section)
        self.assertIn(".accessibilityHidden(true)", section)
        self.assertNotIn("SelectionStore", section)
        self.assertNotIn("AppSessionStore", section)
        self.assertNotIn("bestCombination", section)
        self.assertNotIn("runtime.isRunning", section)
        self.assertNotIn("CompactStatusGlyph", section)
        self.assertIn("Color.clear", section)
        self.assertIn(".frame(width: notch.width)", section)
        self.assertGreaterEqual(section.count(".lineLimit(1)"), 2)

    def test_island_model_has_only_compact_and_expanded_states(self) -> None:
        model_source = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Model"
            / "IslandModel.swift"
        ).read_text(encoding="utf-8")
        state_section = model_source.split("enum State {", 1)[1].split("}", 1)[0]
        self.assertIn("case compact", state_section)
        self.assertIn("case expanded", state_section)
        self.assertNotIn("case peek", state_section)
        self.assertIn("notch.width + compactSideSlotWidth * 2", model_source)
        self.assertNotIn("peekSideSlotWidth", model_source)
        self.assertNotIn("peekWidth", model_source)
        self.assertNotIn("peekHeight", model_source)
        self.assertNotIn("notchPeekExtraWidth", model_source)
        self.assertNotIn("@Published var size", model_source)
        self.assertIn("var size: CGSize {", model_source)
        self.assertNotIn("recomputeSize()", model_source)

    def test_hover_uses_window_interaction_area_as_the_single_transition_authority(self) -> None:
        animations_source = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Theme"
            / "Animations.swift"
        ).read_text(encoding="utf-8")
        model_source = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Model"
            / "IslandModel.swift"
        ).read_text(encoding="utf-8")

        body_section = self.source.split("var body: some View {", 1)[1].split(
            "private var compactIslandButton", 1
        )[0]
        hover_section = body_section.split(
            ".onChange(of: model.isPointerInsideInteractionArea) { isInside in", 1
        )[1].split(
            ".background(alignment: .top)", 1
        )[0]
        hover_function = self.source.split(
            "private func updateCompactHover(_ isHovering: Bool)", 1
        )[1].split("private var shapeOpenAnimation: Animation", 1)[0]

        self.assertIn("@State private var isHovered = false", self.source)
        self.assertIn("@State private var isSessionPanelWidthExpanded = false", self.source)
        self.assertIn("@State private var isSessionPanelVisible = false", self.source)
        self.assertIn("@State private var isSessionPanelContentVisible = false", self.source)
        self.assertIn("updateCompactHover(isInside)", hover_section)
        self.assertEqual(body_section.count("updateCompactHover(isInside)"), 1)
        self.assertNotIn(".onHover", body_section)
        self.assertNotIn("hoverTrackingSize", self.source)
        self.assertIn(
            "@Published private(set) var isPointerInsideInteractionArea = false",
            model_source,
        )
        self.assertIn("func setPointerInsideInteractionArea(_ inside: Bool)", model_source)
        self.assertIn(
            "guard isPointerInsideInteractionArea != inside else { return }",
            model_source,
        )
        self.assertIn("Task.sleep(nanoseconds: 40_000_000)", hover_function)
        self.assertIn("Task.sleep(nanoseconds: 140_000_000)", hover_function)
        self.assertNotIn("Task.sleep(nanoseconds: 120_000_000)", hover_function)
        self.assertNotIn("Task.sleep(nanoseconds: 60_000_000)", hover_function)
        self.assertIn("static let islandHover", animations_source)
        self.assertIn("duration: 0.15", animations_source)
        self.assertIn("static let islandHoverContent = Animation.easeOut(duration: 0.12)", animations_source)
        self.assertIn("guard !Task.isCancelled, isHovered, model.state == .compact", self.source)
        self.assertNotIn("model.setState", hover_section)
        self.assertNotIn("static let hoverMorph", animations_source)
        self.assertNotIn("static let hoverReducedMotion", animations_source)

    def test_hover_exit_shrinks_the_window_interaction_area_before_visual_cleanup(self) -> None:
        hover_function = self.source.split(
            "private func updateCompactHover(_ isHovering: Bool)", 1
        )[1].split("private var shapeOpenAnimation: Animation", 1)[0]
        exit_section = hover_function.split(
            "guard isSessionPanelContentVisible", 1
        )[1]

        shrink_interaction_area = exit_section.index(
            "model.setCompactSessionPanel(visible: false)"
        )
        close_animation = exit_section.index("withAnimation(hoverCloseAnimation)")
        cleanup_delay = exit_section.index("Task.sleep(nanoseconds: 140_000_000)")
        self.assertLess(shrink_interaction_area, close_animation)
        self.assertLess(close_animation, cleanup_delay)
        self.assertEqual(exit_section.count("model.setCompactSessionPanel(visible: false)"), 1)

    def test_hover_phases_overlap_without_a_stop_between_width_and_height(self) -> None:
        hover_function = self.source.split(
            "private func updateCompactHover(_ isHovering: Bool)", 1
        )[1].split("private var shapeOpenAnimation: Animation", 1)[0]
        exit_marker = "\n        guard isSessionPanelContentVisible"
        enter_section = hover_function.split("if shouldHover {", 1)[1].split(exit_marker, 1)[0]
        exit_section = hover_function.split(
            exit_marker, 1
        )[1]

        hover_animation = enter_section.index("withAnimation(hoverShapeAnimation)")
        width_expand = enter_section.index("isSessionPanelWidthExpanded = true")
        panel_reveal = enter_section.index("isSessionPanelVisible = true")
        content_delay = enter_section.index("Task.sleep(nanoseconds: 40_000_000)")
        content_reveal = enter_section.index("isSessionPanelContentVisible = true")
        self.assertLess(hover_animation, width_expand)
        self.assertLess(panel_reveal, content_delay)
        self.assertLess(content_delay, content_reveal)

        close_animation = exit_section.index("withAnimation(hoverCloseAnimation)")
        content_hide = exit_section.index("isSessionPanelContentVisible = false")
        panel_hide = exit_section.index("isSessionPanelVisible = false")
        width_collapse = exit_section.index("isSessionPanelWidthExpanded = false")
        cleanup_delay = exit_section.index("Task.sleep(nanoseconds: 140_000_000)")
        self.assertLess(close_animation, content_hide)
        self.assertLess(content_hide, panel_hide)
        self.assertLess(panel_hide, width_collapse)
        self.assertLess(width_collapse, cleanup_delay)

    def test_compact_content_remains_stable_during_hover(self) -> None:
        overlay_section = self.source.split(".overlay {", 1)[1].split(
            ".clipShape(compactSurfaceShape)", 1
        )[0]

        self.assertIn("CompactPillView", overlay_section)
        self.assertNotIn("PeekGlanceOverlay", overlay_section)
        self.assertNotIn(".opacity(model.state == .compact", overlay_section)
        self.assertNotIn(".scaleEffect", overlay_section)
        self.assertNotIn(".blur(radius: reduceMotion", overlay_section)

    def test_hover_panel_is_a_compact_peek_over_real_sessions(self) -> None:
        self.assertIn("store.activeModelSessions", self.panel_source)
        self.assertIn("ActiveSessionPresenter.present(session)", self.panel_source)
        self.assertIn("Text(presentation.title)", self.panel_source)
        self.assertIn("Text(presentation.identity)", self.panel_source)
        self.assertIn("context: presentation.context", self.panel_source)
        self.assertNotIn("private func sessionTitle", self.panel_source)
        self.assertNotIn("private func sessionContext", self.panel_source)
        self.assertNotIn("private func sessionIdentity", self.panel_source)
        self.assertNotIn("sessionCode", self.panel_source)
        self.assertNotIn("headerHeight", self.panel_source)
        self.assertNotIn("emptyHeight", self.panel_source)
        self.assertNotIn("footerHeight", self.panel_source)
        self.assertNotIn("panelHeader", self.panel_source)
        self.assertNotIn("emptyState", self.panel_source)
        self.assertIn("private static let recommendationHeight: CGFloat = 66", self.panel_source)
        self.assertIn("private static let sessionSummaryHeight: CGFloat = 46", self.panel_source)
        self.assertIn("return recommendationHeight", self.panel_source)
        self.assertIn("return recommendationHeight + sessionSummaryHeight", self.panel_source)
        self.assertNotIn("BridgeDetectedCodexSession", self.panel_source)
        self.assertNotIn("Button(", self.panel_source)
        self.assertNotIn("管理会话", self.panel_source)
        self.assertIn("store.glancePresentation", self.panel_source)
        self.assertIn("operationalSummary", self.panel_source)
        self.assertIn("showsRecommendationSummary", self.panel_source)
        self.assertNotIn("Text(presentation.basisText)", self.panel_source)
        self.assertIn("model.compactSessionPanelWidth", self.panel_source)
        self.assertIn("IslandShape.hoverShoulderRadius * 2", self.panel_source)
        self.assertEqual(
            self.panel_source.count(".padding(.horizontal, LayoutRhythm.section)"),
            2,
        )
        self.assertIn(".padding(.horizontal, LayoutRhythm.standard)", self.panel_source)
        self.assertNotIn(".background {", self.panel_source)
        self.assertNotIn(".clipShape", self.panel_source)
        self.assertNotIn(".shadow(", self.panel_source)
        preview_section = self.source.split(
            "CompactSessionPanelView(", 1
        )[1].split(
            "            islandSurface", 1
        )[0]
        self.assertIn(".padding(.top, max(0, model.compactHeight - 2))", preview_section)
        self.assertIn(".opacity(isSessionPanelContentVisible ? 1 : 0)", preview_section)
        self.assertIn(".offset(y: isSessionPanelContentVisible ? 0 : -4)", preview_section)
        self.assertIn(".allowsHitTesting(isSessionPanelContentVisible)", preview_section)
        self.assertNotIn(".onHover(perform: updateCompactHover)", preview_section)
        self.assertIn(
            ".frame(height: revealedSessionPanelHeight, alignment: .top)",
            preview_section,
        )
        self.assertIn(".clipped()", preview_section)
        self.assertNotIn(".scaleEffect", preview_section)
        self.assertIn(".frame(width: compactSurfaceWidth", self.source)
        self.assertIn("return model.compactSessionPanelWidth", self.source)

    def test_hover_recommendation_uses_compact_labeled_metric_strip(self) -> None:
        recommendation = self.panel_source.split(
            "private var recommendationSummary: some View {", 1
        )[1].split("private var recommendationCandidateColor", 1)[0]
        metrics = self.panel_source.split(
            "private func recommendationMetrics(", 1
        )[1].split("private func recommendationMetric(", 1)[0]
        metric = self.panel_source.split(
            "private func recommendationMetric(", 1
        )[1].split("private func sessionRow", 1)[0]

        self.assertIn("VStack(alignment: .leading, spacing: 10)", recommendation)
        self.assertIn("HStack(alignment: .firstTextBaseline, spacing: 8)", recommendation)
        self.assertIn("Spacer(minLength: 8)", recommendation)
        self.assertNotIn("ZStack(alignment: .trailing)", recommendation)
        self.assertIn("if !presentation.freshnessText.isEmpty", recommendation)
        self.assertIn("recommendationMetrics(presentation.comparisonState)", recommendation)
        self.assertIn("HStack(spacing: 0)", metrics)
        self.assertIn("switch state", metrics)
        self.assertIn("case .pending:", metrics)
        self.assertIn("case .suppressed:", metrics)
        self.assertIn('Text("完成同轮题目后显示差异")', metrics)
        self.assertIn('label: L10n.tr("质量")', metrics)
        self.assertIn('label: L10n.tr("时间")', metrics)
        self.assertIn('label: L10n.tr("成本")', metrics)
        self.assertNotIn('label: L10n.tr("参考费用")', metrics)
        self.assertEqual(metrics.count("recommendationMetricDivider"), 2)
        self.assertIn("label: String", metric)
        self.assertIn("Text(label)", metric)
        self.assertIn("Text(value)", metric)
        self.assertIn("HStack(spacing: LayoutRhythm.micro)", metric)
        self.assertIn("Spacer(minLength: LayoutRhythm.micro)", metric)
        self.assertIn(".minimumScaleFactor(0.8)", metric)
        self.assertIn(".padding(.horizontal, LayoutRhythm.micro / 2)", metric)
        self.assertNotIn(".layoutPriority(1)", metric)
        self.assertIn(".frame(maxWidth: .infinity)", metric)
        self.assertIn(".padding(.horizontal, LayoutRhythm.micro)", metric)

    def test_three_states_share_geometry_for_identity_candidate_and_metrics(self) -> None:
        self.assertIn("@Namespace private var islandTransitionNamespace", self.source)
        for element in (
            "primaryIdentity", "candidateIdentity", "secondaryStatus",
            "qualityMetric", "timeMetric", "costMetric",
        ):
            self.assertIn(element, self.animations_source)
        self.assertIn("func islandMatchedGeometry", self.animations_source)
        self.assertIn("reduceMotion", self.animations_source)
        self.assertIn(".islandMatchedGeometry(", self.compact_source)
        self.assertIn(".islandMatchedGeometry(", self.panel_source)
        self.assertIn(".islandMatchedGeometry(", self.expanded_source)
        self.assertIn("transitionNamespace: islandTransitionNamespace", self.source)
        self.assertIn("primaryIdentityTransitionID", self.source)

        current_identity = self.expanded_source.split(
            "private var radarCurrentIdentityLine: some View {", 1
        )[1].split("private func radarDecisionIdentityLine", 1)[0]
        self.assertIn("IslandTransitionElement.primaryIdentity.rawValue", current_identity)
        self.assertIn("IslandTransitionElement.secondaryStatus.rawValue", current_identity)
        self.assertIn("radarCurrentEffortLabel", current_identity)

    def test_footer_source_status_is_neutral_and_identity_keeps_effort_semantics(self) -> None:
        source_color = self.expanded_source.split(
            "private var footerStatusColor: Color {", 1
        )[1].split("private var showRestartButton", 1)[0]
        self.assertIn("return IslandColor.alertAmber", source_color)
        self.assertIn("return IslandVisual.tertiaryText", source_color)
        self.assertNotIn("IslandColor.liveTeal", source_color)

        self.assertIn("radarCurrentEffortLabel", self.expanded_source)
        self.assertIn("presentation.compactRightTextRole", self.compact_source)
        self.assertNotIn("currentCoreScore", self.compact_source)

    def test_compact_identity_geometry_always_targets_current_model(self) -> None:
        identity = self.source.split(
            "private var compactIdentityTransitionID: String {", 1
        )[1].split("private var connectedPanelShadow", 1)[0]

        self.assertIn("IslandTransitionElement.primaryIdentity.rawValue", identity)
        self.assertNotIn("radarRepresentativeDecision", identity)
        self.assertNotIn("candidateIdentity", identity)

    def test_active_session_order_survives_snapshot_refresh(self) -> None:
        active_sessions = self.store_source.split(
            "var activeModelSessions: [BridgeDetectedModelSession] {", 1
        )[1].split("var evidenceCards", 1)[0]
        apply_snapshot = self.store_source.split(
            "private func applySnapshot(_ newSnapshot: BridgeSnapshot)", 1
        )[1].split("private func activeModelSessionKey", 1)[0]

        self.assertIn("private var stableActiveModelSessionKeys: [String] = []", self.store_source)
        self.assertIn("stableActiveModelSessionKeys.compactMap", active_sessions)
        self.assertIn("reconcileActiveModelSessionOrder", apply_snapshot)
        self.assertLess(
            apply_snapshot.index("reconcileActiveModelSessionOrder"),
            apply_snapshot.index("snapshot = newSnapshot"),
        )
        reconcile = self.store_source.split(
            "private func reconcileActiveModelSessionOrder", 1
        )[1].split("private func reconcilePendingScanControl", 1)[0]
        self.assertIn("stableActiveModelSessionKeys.filter", reconcile)
        self.assertIn("stableActiveModelSessionKeys.append", reconcile)

    def test_hover_shows_only_session_count_and_first_session(self) -> None:
        self.assertIn("if let session = sessions.first", self.panel_source)
        self.assertNotIn("ForEach(", self.panel_source)
        self.assertIn("Text(L10n.Sessions.count(sessions.count))", self.panel_source)
        self.assertIn("Text(presentation.title)", self.panel_source)
        self.assertIn("context: presentation.context", self.panel_source)

    def test_hover_feedback_morphs_into_one_connected_surface(self) -> None:
        glow_section = self.source.split("private struct GlowLayer: View {", 1)[1]
        self.assertIn("let hovered: Bool", glow_section)
        self.assertIn("let connectedPanel: Bool", glow_section)
        self.assertIn("let surfaceShape: IslandShape", glow_section)
        self.assertNotIn("IslandShape(bottomRadius: connectedPanel ? 0 : 14)", glow_section)
        self.assertNotIn("ConnectedCompactOutline", glow_section)
        self.assertIn("if !connectedPanel", glow_section)
        self.assertIn("connectedPanel\n                    ? Color.clear", glow_section)
        self.assertIn(".islandOpen", self.source)
        self.assertIn(".islandClose", self.source)
        self.assertNotIn(".compactSessionMorph", self.source)
        self.assertIn("hovered ? 0.12 : 0.07", glow_section)
        self.assertIn("Color.black.opacity(hovered ? 0.46 : 0.38)", glow_section)
        self.assertNotIn("shadow(\n                color: tint", glow_section)
        shape_source = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Views"
            / "IslandShape.swift"
        ).read_text(encoding="utf-8")
        self.assertIn("var topShoulderRadius: CGFloat = 0", shape_source)
        self.assertIn(
            "IslandShape(topShoulderRadius: expandedShoulderRadius, bottomRadius: 32)",
            shape_source,
        )
        self.assertIn("static let hoverShoulderRadius: CGFloat = 14", shape_source)
        self.assertIn(
            "IslandShape(topShoulderRadius: hoverShoulderRadius, bottomRadius: 24)",
            shape_source,
        )
        self.assertIn(
            "IslandShape(topShoulderRadius: hoverShoulderRadius, bottomRadius: 0)",
            shape_source,
        )
        self.assertNotIn("static var hoverBody", shape_source)
        self.assertIn(
            "var animatableData: AnimatablePair<CGFloat, AnimatablePair<CGFloat, CGFloat>>",
            shape_source,
        )
        self.assertIn(
            "AnimatablePair(inset, AnimatablePair(topShoulderRadius, bottomRadius))",
            shape_source,
        )
        self.assertIn("let bodyMinX = frame.minX + shoulderRadius", shape_source)
        self.assertIn("let bodyMaxX = frame.maxX - shoulderRadius", shape_source)
        self.assertIn("control: CGPoint(x: bodyMaxX, y: frame.minY)", shape_source)
        self.assertIn("control: CGPoint(x: bodyMinX, y: frame.minY)", shape_source)
        self.assertNotIn("UnevenRoundedRectangle", shape_source)

    def test_expanded_surface_uses_one_asymmetric_continuous_shape(self) -> None:
        shape_section = self.source.split(
            "private var compactSurfaceShape: IslandShape {", 1
        )[1].split("private var compactSurfaceWidth", 1)[0]
        glow_section = self.source.split("private var backgroundGlow: some View {", 1)[1].split(
            "private func collapseExpanded", 1
        )[0]
        edge_section = self.source.split("private var activeEdgeSignal: some View {", 1)[1].split(
            "private var compactSurfaceShape", 1
        )[0]

        self.assertIn("if model.state == .expanded", shape_section)
        self.assertIn("return .expanded", shape_section)
        self.assertIn("surfaceShape: compactSurfaceShape", glow_section)
        self.assertIn(".contentShape(compactSurfaceShape)", glow_section)
        self.assertIn("return .hoverCap", self.source)
        self.assertIn("surfaceShape: activeOutlineShape", edge_section)
        self.assertIn("private var hoverSurfaceShape", self.source)
        self.assertIn("return isSessionPanelVisible ? hoverSurfaceShape : IslandShape()", self.source)
        self.assertIn("compactContentShellCompensation", self.source)
        self.assertIn(".mask(surfaceShape)", self.source)

    def test_one_surface_identity_survives_compact_to_expanded_morph(self) -> None:
        body_section = self.source.split("var body: some View {", 1)[1].split(
            "private var compactIslandButton", 1
        )[0]
        self.assertEqual(body_section.count("islandSurface"), 1)
        self.assertIn("compactIslandButton", body_section)
        self.assertLess(
            body_section.index("islandSurface"),
            body_section.index("compactIslandButton"),
        )

    def test_active_edge_tracks_the_full_hover_and_expanded_surface(self) -> None:
        body_section = self.source.split("var body: some View {", 1)[1].split(
            "private var compactIslandButton", 1
        )[0]
        edge_section = self.source.split("private var activeEdgeSignal: some View {", 1)[1].split(
            "private var compactSurfaceShape", 1
        )[0]
        size_section = self.source.split("private var activeEdgeSize: CGSize {", 1)[1].split(
            "private var glowColor", 1
        )[0]

        self.assertIn(".overlay(alignment: .top)", body_section)
        self.assertIn("activeEdgeSignal", body_section)
        self.assertIn("ActivityEdgeLayer(", edge_section)
        self.assertIn("width: activeEdgeSize.width", edge_section)
        self.assertIn("height: activeEdgeSize.height", edge_section)
        self.assertNotIn(".animation(hoverPreviewAnimation", edge_section)
        self.assertIn("return model.size", size_section)
        self.assertIn("width: model.compactSessionPanelWidth", size_section)
        self.assertIn(
            "+ (isSessionPanelVisible ? revealedSessionPanelHeight - 2 : 0)",
            size_section,
        )
        self.assertIn(".mask(surfaceShape)", self.source)

    def test_connected_panel_shadow_and_edge_glow_follow_the_rounded_outline(self) -> None:
        body_section = self.source.split("var body: some View {", 1)[1].split(
            "private var compactIslandButton", 1
        )[0]
        shadow_section = self.source.split("private var connectedPanelShadow: some View {", 1)[1].split(
            "private var activeEdgeSignal", 1
        )[0]
        edge_section = self.source.split("private struct ActivityEdgeLayer: View {", 1)[1].split(
            "private struct ConnectedCompactOutline", 1
        )[0]

        self.assertIn(".background(alignment: .top)", body_section)
        self.assertIn("connectedPanelShadow", body_section)
        self.assertIn("hoverSurfaceShape", shadow_section)
        self.assertIn("width: activeEdgeSize.width", shadow_section)
        self.assertIn("height: activeEdgeSize.height", shadow_section)
        self.assertIn("Color.black.opacity(0.46)", shadow_section)
        self.assertIn("radius: showsShadow ? 22 : 0", shadow_section)
        self.assertGreaterEqual(shadow_section.count("hoverSurfaceShape"), 2)
        self.assertIn(".strokeBorder(", shadow_section)
        self.assertIn("let surfaceShape: IslandShape", edge_section)
        self.assertIn(".mask(surfaceShape)", edge_section)

    def test_click_reveals_prewarmed_expanded_content_without_remounting_it(self) -> None:
        button_action = self.source.split("private var compactIslandButton", 1)[1].split(
            "} label:", 1
        )[0]
        open_section = self.source.split("private func openExpanded()", 1)[1].split(
            "private func updateCompactHover", 1
        )[0]

        self.assertIn("guard model.state == .compact else { return }", button_action)
        self.assertIn("guard model.state == .compact else { return }", open_section)
        self.assertLess(
            open_section.index("guard model.state == .compact else { return }"),
            open_section.index("hoverTransitionTask?.cancel()"),
        )
        self.assertNotIn("dismissSessionPanel()", button_action)
        self.assertIn("hoverTransitionTask?.cancel()", open_section)
        self.assertIn("expandedTransitionTask?.cancel()", open_section)
        self.assertNotIn("isSessionPanelTransitioningToExpanded", open_section)
        self.assertNotIn("withAnimation(hoverContentAnimation)", open_section)
        self.assertIn("withAnimation(shapeOpenAnimation)", open_section)
        self.assertIn("isSessionPanelVisible = false", open_section)
        self.assertIn("isSessionPanelContentVisible = false", open_section)
        self.assertIn("isExpandedContentVisible = true", open_section)
        self.assertIn("isExpandedContentVisible = reduceMotion", open_section)
        self.assertIn("isExpandedShellOpening = !reduceMotion", open_section)
        self.assertIn("Task.sleep(nanoseconds: 120_000_000)", open_section)
        self.assertIn("model.setState(.expanded)", open_section)
        self.assertNotIn("isExpandedContentMounted", open_section)
        self.assertNotIn("Task.yield", open_section)

        body_section = self.source.split("var body: some View {", 1)[1].split(
            "private var compactIslandButton", 1
        )[0]
        self.assertIn("if model.state == .compact {", body_section)
        self.assertNotIn("isSessionPanelTransitioningToExpanded", body_section)
        self.assertIn("max(0, model.compactHeight - 2)", body_section)
        self.assertNotIn("isSessionPanelTransitioningToExpanded", self.source)

        self.assertIn(
            ".onChange(of: store.glancePresentation.destination) { destination in",
            body_section,
        )
        self.assertIn("expandedEntryDestination = destination", body_section)

    def test_window_hit_testing_expands_over_connected_session_panel(self) -> None:
        controller_source = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Window"
            / "SelectionWindowController.swift"
        ).read_text(encoding="utf-8")

        self.assertIn("private static let compactHoverHitInset: CGFloat = 6", controller_source)
        self.assertIn("let interactionSize = model.interactionSize", controller_source)
        self.assertIn("x: frame.width / 2 - interactionSize.width / 2", controller_source)
        self.assertIn("height: interactionSize.height", controller_source)
        self.assertIn(
            "let hitInset = model.state == .compact ? Self.compactHoverHitInset : 0",
            controller_source,
        )
        self.assertIn(
            "let interactiveIslandRect = islandRect.insetBy(dx: -hitInset, dy: -hitInset)",
            controller_source,
        )
        self.assertIn("let inside = interactiveIslandRect.contains(local)", controller_source)
        self.assertIn("model.setPointerInsideInteractionArea(inside)", controller_source)
        self.assertIn("let shouldIgnoreMouseEvents = !inside", controller_source)
        self.assertIn(
            "guard window.ignoresMouseEvents != shouldIgnoreMouseEvents else { return }",
            controller_source,
        )
        self.assertIn("window.ignoresMouseEvents = shouldIgnoreMouseEvents", controller_source)
        self.assertLess(
            controller_source.index("model.setPointerInsideInteractionArea(inside)"),
            controller_source.index(
                "guard window.ignoresMouseEvents != shouldIgnoreMouseEvents else { return }"
            ),
        )
        self.assertNotIn("window.ignoresMouseEvents = !inside", controller_source)
        self.assertNotIn("model.state == .peek", controller_source)
        self.assertNotIn("model.setState(.compact)", controller_source)

        hosting_source = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Window"
            / "IslandHostingView.swift"
        ).read_text(encoding="utf-8")
        self.assertIn("let size = model.interactionSize", hosting_source)

    def test_notch_compact_slot_budget_matches_the_rendered_text_frames(self) -> None:
        model_source = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Model"
            / "IslandModel.swift"
        ).read_text(encoding="utf-8")
        self.assertIn("private static let notchCompactSideSlotWidth: CGFloat = 112", model_source)
        self.assertIn("var compactSideSlotWidth: CGFloat", model_source)
        compact_section = model_source.split("var compactSideSlotWidth: CGFloat", 1)[1].split(
            "init(notch:", 1
        )[0]
        self.assertIn("Self.notchCompactSideSlotWidth", compact_section)
        self.assertNotIn("pillSlotWidth", compact_section)

        section = self.compact_source.split("private var notchCompactLayout: some View {", 1)[1].split(
            "private var menuBarCompactLayout: some View {", 1
        )[0]
        leading = section.split("compactLeftContent", 1)[1].split(
            "Color.clear", 1
        )[0]
        trailing = section.split("compactRightContent", 1)[1]
        self.assertLess(leading.index(".padding(.leading"), leading.index(".frame(width:"))
        self.assertLess(trailing.index(".padding(.trailing"), trailing.index(".frame(width:"))
        self.assertIn("private let notchTextSafetyInset: CGFloat = 8", self.compact_source)
        self.assertIn(".padding(.trailing, notchTextSafetyInset)", leading)
        self.assertIn(".padding(.leading, notchTextSafetyInset)", trailing)

    def test_expanded_shell_uses_html_reference_canvas_budget(self) -> None:
        model_source = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Model"
            / "IslandModel.swift"
        ).read_text(encoding="utf-8")
        self.assertIn("let expandedWidth: CGFloat = 1080", model_source)
        self.assertIn("let expandedBaseContentHeight: CGFloat = 460", model_source)
        self.assertIn("var expandedSize: CGSize", model_source)
        self.assertIn("case .expanded:\n            return expandedSize", model_source)

        window_source = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Window"
            / "SelectionWindowController.swift"
        ).read_text(encoding="utf-8")
        self.assertIn("CGSize(width: 1120, height: 540)", window_source)

    def test_expanded_content_is_not_double_padded_inside_shell(self) -> None:
        expanded_section = self.source.split("ExpandedSelectionView(", 1)[1].split(
            ".equatable()", 1
        )[0]
        self.assertIn("expandedSize: model.expandedSize", expanded_section)
        self.assertIn("onCollapse: collapseExpanded", self.source)
        self.assertNotIn(".padding(.horizontal, 18)", expanded_section)
        self.assertNotIn(".padding(.vertical, 14)", expanded_section)

    def test_root_stage_keeps_morph_top_anchored_inside_a_fixed_expanded_canvas(self) -> None:
        body_section = self.source.split("var body: some View {", 1)[1].split(
            "    private var compactIslandButton", 1
        )[0]
        self.assertIn("ZStack(alignment: .top)", body_section)
        self.assertNotIn("VStack(spacing: 0)", body_section)
        self.assertIn(
            ".frame(width: model.expandedSize.width, height: model.expandedSize.height, alignment: .top)",
            body_section,
        )
        self.assertIn(".frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)", body_section)

    def test_expanded_shell_collapses_from_background_taps(self) -> None:
        glow_section = self.source.split("private var backgroundGlow: some View {", 1)[1].split(
            "private func collapseExpanded()", 1
        )[0]
        self.assertIn("backgroundGlow", self.source)
        self.assertIn("collapseExpanded()", glow_section)
        self.assertIn(".allowsHitTesting(model.state == .expanded)", glow_section)
        self.assertIn("private func collapseExpanded()", self.source)
        self.assertIn("model.setState(.compact)", self.source)

    def test_compact_button_prepares_destination_before_expanding(self) -> None:
        self.assertIn("private var compactIslandButton", self.source)
        compact_section = self.source.split("private var compactIslandButton", 1)[1].split(
            "private var islandSurface", 1
        )[0]
        self.assertIn("Button", compact_section)
        self.assertIn("Color.clear", compact_section)
        self.assertIn(".buttonStyle(.plain)", compact_section)
        self.assertNotIn("CompactIslandButtonStyle", compact_section)
        self.assertIn("accessibilityHint(L10n.Island.openRecommendationDetails)", compact_section)
        self.assertIn("store.prepareExpandedDestination()", compact_section)
        self.assertIn(
            "expandedEntryDestination = store.consumeExpandedDestination()",
            compact_section,
        )
        self.assertIn("openExpanded()", compact_section)
        self.assertLess(
            compact_section.index("store.prepareExpandedDestination()"),
            compact_section.index("expandedEntryDestination = store.consumeExpandedDestination()"),
        )
        self.assertLess(
            compact_section.index("expandedEntryDestination = store.consumeExpandedDestination()"),
            compact_section.index("openExpanded()"),
        )

    def test_compact_button_keeps_button_role_and_hides_inner_overlay_accessibility(self) -> None:
        compact_section = self.source.split("private var compactIslandButton", 1)[1].split(
            "private var islandSurface", 1
        )[0]
        overlay_section = self.compact_source

        self.assertNotIn(".accessibilityElement(children: .ignore)", compact_section)
        self.assertIn(".accessibilityLabel(store.glancePresentation.accessibilityLabel)", compact_section)
        self.assertIn(".accessibilityHint(L10n.Island.openRecommendationDetails)", compact_section)
        self.assertIn(".accessibilityHidden(true)", overlay_section)
        self.assertNotIn(".accessibilityLabel(store.glancePresentation.accessibilityLabel)", overlay_section)

    def test_expanded_content_is_prewarmed_and_only_visibility_changes_during_click(self) -> None:
        animations_source = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Theme"
            / "Animations.swift"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "static let islandOpen = Animation.spring(response: 0.42, dampingFraction: 0.86)",
            animations_source,
        )
        self.assertIn(
            "static let islandClose = Animation.spring(response: 0.30, dampingFraction: 0.90)",
            animations_source,
        )
        self.assertIn("@State private var isExpandedContentVisible = false", self.source)
        self.assertIn(
            "@State private var expandedEntryDestination: GlanceDestination = .overview",
            self.source,
        )
        self.assertIn("@State private var isExpandedShellOpening = false", self.source)
        self.assertIn("@State private var expandedTransitionTask: Task<Void, Never>?", self.source)
        self.assertNotIn("isExpandedContentMounted", self.source)

        surface_section = self.source.split("private var islandSurface", 1)[1].split(
            "private var backgroundGlow", 1
        )[0]
        self.assertIn("ExpandedSelectionView(", surface_section)
        self.assertIn(".equatable()", surface_section)
        self.assertIn(".opacity(isExpandedContentVisible ? 1 : 0)", surface_section)
        self.assertIn("if model.state != .expanded || isExpandedShellOpening", surface_section)
        self.assertIn(".accessibilityHidden(!isExpandedContentVisible)", surface_section)
        self.assertNotIn(".offset(y:", surface_section)
        self.assertIn(".allowsHitTesting(isExpandedContentVisible)", surface_section)

        open_section = self.source.split("private func openExpanded()", 1)[1].split(
            "private func updateCompactHover", 1
        )[0]
        self.assertIn("isExpandedContentVisible = reduceMotion", open_section)
        self.assertIn("Task.sleep(nanoseconds: 120_000_000)", open_section)
        self.assertIn("withAnimation(expandedContentOpenAnimation)", open_section)
        self.assertNotIn("Task.yield", open_section)
        self.assertNotIn("isExpandedContentMounted", open_section)

        close_section = self.source.split("private func collapseExpanded()", 1)[1].split(
            "private func openExpanded()", 1
        )[0]
        self.assertIn("expandedTransitionTask?.cancel()", close_section)
        self.assertIn("withAnimation(expandedContentCloseAnimation)", close_section)
        self.assertIn("withAnimation(shapeCloseAnimation)", close_section)
        self.assertLess(
            close_section.index("isExpandedContentVisible = false"),
            close_section.index("model.setState(.compact)"),
        )
        self.assertNotIn("Task.sleep", close_section)
        self.assertNotIn("isExpandedContentMounted", close_section)

    def test_expanded_content_uses_a_fixed_final_canvas_inside_the_top_anchored_shell(self) -> None:
        expanded_source = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Views"
            / "ExpandedSelectionView.swift"
        ).read_text(encoding="utf-8")
        surface_source = self.source.split("private var islandSurface: some View {", 1)[1].split(
            "private var backgroundGlow", 1
        )[0]
        expanded_body = expanded_source.split("var body: some View {", 1)[1].split(
            "private func handleExitCommand", 1
        )[0]

        self.assertIn("ZStack(alignment: .top)", surface_source)
        self.assertIn("width: expandedSize.width", expanded_body)
        self.assertIn("height: expandedSize.height", expanded_body)
        self.assertIn("alignment: .topLeading", expanded_body)
        self.assertNotIn(
            ".frame(width: model.size.width, height: model.size.height, alignment: .topLeading)",
            expanded_body,
        )

    def test_running_state_uses_one_integrated_edge_signal_without_a_duplicate_spinner(self) -> None:
        self.assertIn("@Environment(\\.accessibilityReduceMotion)", self.source)
        self.assertIn("store.isGlanceActuallyVisible", self.source)
        self.assertNotIn("private struct LoadingSweep", self.source)
        self.assertIn("AngularGradient", self.source)
        self.assertIn("private var activeEdgeSignal: some View", self.source)
        self.assertIn("TimelineView(.animation(minimumInterval: 1.0 / 30.0))", self.source)
        self.assertIn("private func longTailEdgeHighlight", self.source)
        self.assertNotIn("private struct TravelingIslandEdge: Shape", self.source)
        self.assertNotIn("trimmedPath(from:", self.source)
        self.assertIn("let cycleDuration = 4.8", self.source)
        self.assertIn(".init(color: .clear, location: 0.55)", self.source)
        self.assertIn(".init(color: tint, location: 0.78)", self.source)
        self.assertIn(".init(color: Color.white.opacity(0.95), location: 0.92)", self.source)
        self.assertIn(".init(color: .clear, location: 1.00)", self.source)
        self.assertIn("lineWidth: 4", self.source)
        self.assertIn(".blur(radius: 3)", self.source)
        self.assertNotIn("let signalWidth", self.source)
        self.assertNotIn(".frame(width: signalWidth, height: 2)", self.source)
        self.assertNotIn(".mask(IslandShape().strokeBorder", self.source)
        self.assertIn("if reduceMotion || increasedContrast", self.source)
        self.assertIn("presentation.activity == .none", self.compact_source)
        self.assertNotIn("ProgressView()", self.compact_source)

    def test_window_reports_actual_visibility_without_app_focus_proxy(self) -> None:
        window_source = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Window"
            / "SelectionWindowController.swift"
        ).read_text(encoding="utf-8")
        self.assertIn("occlusionState.contains(.visible)", window_source)
        self.assertIn("store.setGlanceActuallyVisible", window_source)
        self.assertNotIn("applicationDidResignActive", window_source)


if __name__ == "__main__":
    unittest.main()
