from __future__ import annotations

from pathlib import Path
import unittest


class LeaderboardExportViewTest(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).resolve().parent.parent
        self.root = root
        self.export_source = (
            root / "Sources" / "Views" / "LeaderboardExportView.swift"
        ).read_text(encoding="utf-8")
        self.expanded_source = (
            root / "Sources" / "Views" / "ExpandedSelectionView.swift"
        ).read_text(encoding="utf-8")
        self.build_source = (root / "build.sh").read_text(encoding="utf-8")
        self.dev_build_source = (root / "build-dev.sh").read_text(encoding="utf-8")
        self.wordmark_source = (
            root
            / "Resources"
            / "ModeldialWordmark.svg"
        ).read_text(encoding="utf-8")

    def test_share_card_uses_fixed_9_by_16_png_canvas(self) -> None:
        self.assertIn("CGSize(width: 1080, height: 1920)", self.export_source)
        self.assertIn("ImageRenderer(content: exportView)", self.export_source)
        self.assertIn("renderer.scale = 1", self.export_source)
        self.assertIn("using: .png", self.export_source)
        self.assertIn('panel.message = "PNG · 1080 × 1920"', self.export_source)

    def test_share_card_contains_only_leaderboard_summary_columns(self) -> None:
        for title in ("排名", "模型", "总分", "总耗时", "参考费用"):
            self.assertIn(f'copy.text("{title}")', self.export_source)
        self.assertNotIn('Text("/100")', self.export_source)
        for excluded in ("Q1", "Q2", "Q3", "Q4", "Q5", "每题得分", "当前在用"):
            self.assertNotIn(excluded, self.export_source)

    def test_export_rank_uses_canonical_dto_semantics_without_array_reconstruction(self) -> None:
        row_model = self._section(
            self.export_source,
            "struct LeaderboardExportRow: Identifiable {",
            "struct LeaderboardExportContent",
        )
        table = self._section(
            self.export_source,
            "private var leaderboardTable",
            "private var tableHeader",
        )
        row_view = self._section(
            self.export_source,
            "private func leaderboardRow",
            "private var rowHeight",
        )
        export_projection = self._section(
            self.expanded_source,
            "private func exportLeaderboardImage",
            "private func presentLeaderboardExport",
        )

        self.assertIn("let canonicalRank: Int?", row_model)
        self.assertIn("let providerID: String?", row_model)
        self.assertIn("let isTiedRank: Bool", row_model)
        self.assertNotIn("let canonicalRankLabel: String", row_model)
        self.assertIn("let isRecommended: Bool", row_model)
        self.assertIn("leaderboardRow(row)", table)
        self.assertNotIn("index + 1", table)
        self.assertIn("Text(copy.rankLabel(for: row))", row_view)
        self.assertIn("row.isRecommended", row_view)
        self.assertNotIn("row.canonicalRank == 1", row_view)
        self.assertNotIn('String(format: "%02d"', row_view)
        self.assertIn("canonicalRank: projected.presentation.rank", export_projection)
        self.assertIn("providerID: entry.providerId", export_projection)
        self.assertIn("isTiedRank: projected.presentation.rank.map", export_projection)
        self.assertIn("RadarPresenter.leaderboardExportSemantics(", export_projection)
        self.assertIn("decisionTagKinds: exportTags.map(\\.kind)", export_projection)
        self.assertIn("isRecommended: exportSemantics.isRecommended", export_projection)
        self.assertIn("copy.tagLabel(tag.kind)", self.export_source)

    def test_share_card_carries_brand_lockup_and_decision_tag_visual_hierarchy(self) -> None:
        self.assertIn('loadBrandImage(named: "ModeldialShareMark")', self.export_source)
        self.assertIn('loadBrandImage(named: "ModeldialWordmark")', self.export_source)
        self.assertIn("Image(nsImage: brandWordmark)", self.export_source)
        self.assertNotIn("Text(\"modeldial\")", self.export_source)
        self.assertNotIn("<text", self.wordmark_source)
        self.assertIn('aria-label="modeldial"', self.wordmark_source)
        self.assertGreaterEqual(self.wordmark_source.count("<path"), 9)
        self.assertIn('stroke-linejoin="round"', self.wordmark_source)
        self.assertIn('kind == "recommended"', self.export_source)
        self.assertIn("LeaderboardExportPalette.accent", self.export_source)
        self.assertIn("LeaderboardExportPalette.weakTagText", self.export_source)
        self.assertIn("var priority: Int", self.export_source)
        for kind, priority in (
            ("recommended", 0),
            ("value", 1),
            ("speed", 2),
            ("lightweight", 3),
        ):
            self.assertIn(f'case "{kind}": return {priority}', self.export_source)
        self.assertIn("decisionTags", self.export_source)
        self.assertIn(".sorted { $0.priority < $1.priority }", self.export_source)
        export_projection = self._section(
            self.expanded_source,
            "private func exportLeaderboardImage",
            "private func presentLeaderboardExport",
        )
        self.assertIn("projected.presentation.tags.compactMap(leaderboardExportTag)", export_projection)
        for kind in ("recommended", "value", "speed", "lightweight"):
            self.assertIn(f'kind: "{kind}"', export_projection)

    def test_native_builds_bundle_provider_logos_and_third_party_notices(self) -> None:
        for source in (self.build_source, self.dev_build_source):
            self.assertIn('mkdir -p "$RES_DIR/ProviderLogos" "$RES_DIR/Legal"', source)
            self.assertIn(
                "Resources/ProviderLogos/*-lobe.svg",
                source,
            )
            self.assertIn(
                'cp Resources/Legal/* "$RES_DIR/Legal/"',
                source,
            )
        legal_dir = self.root / "Resources" / "Legal"
        self.assertTrue((legal_dir / "THIRD_PARTY_NOTICES.txt").is_file())
        self.assertTrue((legal_dir / "Sparkle-LICENSE.txt").is_file())

    def test_social_share_card_uses_readable_type_and_plain_language_tag_legend(self) -> None:
        for size in (30, 40, 26, 24):
            self.assertIn(f"size: {size}", self.export_source)
        self.assertIn('case "recommended": return text("推荐")', self.export_source)
        self.assertIn('case "recommended": return text("由当前推荐决策标记。")', self.export_source)
        self.assertNotIn("本轮总分最高", self.export_source)
        self.assertIn('case "value": return text("性价比")', self.export_source)
        self.assertIn('case "value": return text("第一梯队模型，参考费用最低。")', self.export_source)
        self.assertIn('case "speed": return text("速度优选")', self.export_source)
        self.assertIn('case "speed": return text("第一梯队模型，总耗时最短。")', self.export_source)
        self.assertIn('case "lightweight": return text("轻量优选")', self.export_source)
        self.assertIn(
            'return text("费用显著更低、性能适中，适合 OpenClaw、Hermes 等日常使用。")',
            self.export_source,
        )
        self.assertNotIn("第一梯队模型中", self.export_source)
        self.assertNotIn("整套题", self.export_source)
        self.assertIn("brandSlogan", self.export_source)
        self.assertIn("alignment: .bottomTrailing", self.export_source)
        self.assertIn('Text(copy.text("模型综合榜单"))', self.export_source)
        self.assertNotIn('Text("本轮模型榜单")', self.export_source)

    def test_share_card_can_render_chinese_and_english_independently(self) -> None:
        required_calls = (
            'enum LeaderboardExportLanguage: String, CaseIterable, Identifiable',
            'case simplifiedChinese',
            'case english',
            'AppLanguageResolver.localizationBundle(for: language.appLanguage)',
            'copy.format("%d 个有效结果，展示其中 15 个"',
            '"图片展示其中 15 个，另有 %d 个有效结果未展示。"',
            'return copy.text("未提供")',
            'alert.messageText = L10n.tr("当前榜单有未导出结果")',
            'alert.addButton(withTitle: L10n.tr("仍要导出"))',
            'alert.addButton(withTitle: L10n.tr("取消"))',
            'panel.title = L10n.tr("导出榜单图片")',
            'panel.prompt = L10n.tr("导出")',
        )
        for call in required_calls:
            with self.subTest(call=call):
                self.assertIn(call, self.export_source)

        body_source = self._section(
            self.export_source,
            "private var exportBody",
            "private var brandHeader",
        )
        self.assertLess(body_source.index("leaderboardTable"), body_source.index("tagLegend"))
        self.assertLess(body_source.index("tagLegend"), body_source.index("Spacer(minLength:"))
        self.assertLess(body_source.index("Spacer(minLength:"), body_source.index("brandFooter"))

        legend_source = self._section(
            self.export_source,
            "private func tagLegendLine",
            "private var brandSlogan",
        )
        self.assertIn("Capsule()", legend_source)
        self.assertIn("strokeBorder", legend_source)
        self.assertIn("LeaderboardExportPalette.weakTagFill", legend_source)
        self.assertIn("LeaderboardExportPalette.weakTagBorder", legend_source)
        self.assertIn("size: 24, weight: .medium", legend_source)
        self.assertIn("content.language == .english ? 152 : 112", legend_source)
        self.assertIn("content.language == .english ? 19 : 22", legend_source)

        row_source = self._section(
            self.export_source,
            "private func leaderboardRow",
            "private var rowHeight",
        )
        self.assertIn("HStack(alignment: .center", row_source)
        self.assertIn("ProviderLogoMark(providerID: row.providerID)", row_source)
        self.assertIn("Spacer(minLength:", row_source)
        self.assertLess(
            row_source.index("ProviderLogoMark(providerID: row.providerID)"),
            row_source.index("Text(row.modelLabel)"),
        )
        self.assertLess(
            row_source.index("Text(row.modelLabel)"),
            row_source.index("LeaderboardExportTagCluster"),
        )
        self.assertNotIn("VStack(alignment: .leading", row_source)
        self.assertIn("LeaderboardExportPalette.recommendedRowFill", row_source)
        self.assertIn("LeaderboardExportPalette.recommendedRowBar", row_source)

        header_source = self._section(
            self.export_source,
            "private var tableHeader",
            "private func leaderboardRow",
        )
        for source in (header_source, row_source):
            self.assertIn("HStack(spacing: 20)", source)
            self.assertIn(".padding(.leading, 12)", source)
            self.assertIn(".frame(width: 72", source)
            self.assertGreaterEqual(source.count(".frame(width: 116"), 2)

        tag_cluster_source = self._section(
            self.export_source,
            "private struct LeaderboardExportTagCluster",
            "private enum LeaderboardExportPalette",
        )
        self.assertIn("tags.count > 1", tag_cluster_source)
        self.assertIn("isCompact ? 22 : 24", tag_cluster_source)
        self.assertIn("isCompact ? 8 : 10", tag_cluster_source)

    def test_export_tags_are_capped_at_two_without_recomputing_decision_tags(self) -> None:
        row_model = self._section(
            self.export_source,
            "struct LeaderboardExportRow: Identifiable {",
            "struct LeaderboardExportContent",
        )
        row_source = self._section(
            self.export_source,
            "private func leaderboardRow",
            "private var rowHeight",
        )

        self.assertIn("let decisionTags: [LeaderboardExportTag]", row_model)
        self.assertIn("var visibleDecisionTags: [LeaderboardExportTag]", row_model)
        self.assertIn("decisionTags", row_model)
        self.assertIn(".sorted { $0.priority < $1.priority }", row_model)
        self.assertIn(".prefix(2)", row_model)
        self.assertIn("row.visibleDecisionTags", row_source)
        self.assertNotIn("sortedTags(row.decisionTags)", row_source)
        self.assertIn("Text(row.modelLabel)", row_source)

    def test_export_legend_uses_only_visible_row_tags_in_priority_order(self) -> None:
        legend_tags_source = self._section(
            self.export_source,
            "private var legendTags",
            "private var tagLegend",
        )
        legend_source = self._section(
            self.export_source,
            "private var tagLegend",
            "private func tagLegendLine",
        )

        self.assertIn("content.rows", legend_tags_source)
        self.assertIn("visibleDecisionTags", legend_tags_source)
        self.assertIn("Set<String>()", legend_tags_source)
        self.assertIn("seenKinds.insert($0.kind).inserted", legend_tags_source)
        self.assertIn(".sorted { $0.priority < $1.priority }", legend_tags_source)
        self.assertIn("ForEach(legendTags)", legend_source)
        self.assertIn("copy.tagLabel(tag.kind)", legend_source)
        self.assertIn("copy.tagDescription(tag.kind)", legend_source)
        self.assertNotIn('kind: "recommended"', legend_source)
        self.assertNotIn('kind: "value"', legend_source)
        self.assertNotIn('kind: "speed"', legend_source)
        self.assertNotIn('kind: "lightweight"', legend_source)

    def test_share_card_caps_output_at_fifteen_and_explains_hidden_valid_results(self) -> None:
        export_projection = self._section(
            self.expanded_source,
            "private func exportLeaderboardImage",
            "private func presentLeaderboardExport",
        )
        self.assertIn(
            "let allRows: [LeaderboardExportRow] = projectedEntries.compactMap",
            export_projection,
        )
        self.assertIn("totalValidResultCount: allRows.count", export_projection)
        self.assertIn("rows: Array(allRows.prefix(15))", export_projection)
        self.assertIn("content.totalValidResultCount", self.export_source)
        self.assertIn("个有效结果，展示其中 15 个", self.export_source)
        self.assertIn("图片展示其中 15 个，另有", self.export_source)
        self.assertNotIn("展示前 15 名", self.export_source)

    def test_export_consumes_the_same_authoritative_rows_as_radar(self) -> None:
        filter_source = self._section(
            self.expanded_source,
            "private var exportableLeaderboardEntries",
            "private var leaderboardExportOmittedCount",
        )
        self.assertIn("store.radarLeaderboardItems.filter", filter_source)
        self.assertIn("$0.score != nil", filter_source)
        self.assertNotIn("detailEntries", filter_source)
        self.assertNotIn("evidenceAvailability", filter_source)

    def test_out_of_scope_rows_require_confirmation_before_export(self) -> None:
        flow_source = self._section(
            self.export_source,
            "static func presentExportFlow",
            "static func renderPNG",
        )
        self.assertIn("let alert = NSAlert()", flow_source)
        self.assertIn('alert.messageText = L10n.tr("当前榜单有未导出结果")', flow_source)
        self.assertIn('alert.addButton(withTitle: L10n.tr("仍要导出"))', flow_source)
        self.assertIn('alert.addButton(withTitle: L10n.tr("取消"))', flow_source)
        self.assertIn("失败、未完成、过期或配置不适用", flow_source)
        self.assertIn("alert.beginSheetModal(for: presentingWindow)", flow_source)
        self.assertIn("guard response == .alertFirstButtonReturn", flow_source)
        self.assertIn("presentPreview(", flow_source)
        self.assertNotIn("DispatchQueue.main.async", flow_source)
        self.assertNotIn("pendingLeaderboardExportContent", self.expanded_source)

    def test_header_owns_global_tools_and_save_panel_is_nonblocking(self) -> None:
        header_source = self._section(
            self.expanded_source,
            "private var headerToolControls",
            "private var overviewPanelHeader",
        )
        footer_source = self._section(
            self.expanded_source,
            "private var panelFooter",
            "private var footerPageTabs",
        )
        self.assertIn('Image(systemName: "square.and.arrow.up")', header_source)
        self.assertNotIn('Text("导出榜单")', header_source)
        self.assertIn(".disabled(!canExportLeaderboard)", header_source)
        self.assertLess(
            header_source.index('Image(systemName: "square.and.arrow.up")'),
            header_source.index("SettingsButton(action: openSettings)"),
        )
        self.assertLess(
            header_source.index("SettingsButton(action: openSettings)"),
            header_source.index("QuitButton()"),
        )
        self.assertIn(".accessibilityLabel(L10n.tr(\"导出榜单\"))", header_source)
        self.assertNotIn('Image(systemName: "square.and.arrow.up")', footer_source)
        self.assertNotIn("SettingsButton(action: openSettings)", footer_source)
        self.assertNotIn("QuitButton()", footer_source)
        self.assertIn("panel.beginSheetModal(for: window, completionHandler: completion)", self.export_source)
        self.assertNotIn("runModal", self.export_source)

    def test_success_feedback_can_open_or_reveal_the_exported_image(self) -> None:
        self.assertIn("onSuccess: @escaping (URL) -> Void", self.export_source)
        self.assertIn("onSaved(destinationURL)", self.export_source)
        self.assertIn('.alert(L10n.tr("榜单已导出")', self.expanded_source)
        self.assertIn('Button(L10n.tr("打开图片"))', self.expanded_source)
        self.assertIn('Button(L10n.tr("在 Finder 中显示"))', self.expanded_source)
        self.assertIn('Button(L10n.Common.done, role: .cancel)', self.expanded_source)
        self.assertIn("NSWorkspace.shared.open(url)", self.expanded_source)
        self.assertIn("NSWorkspace.shared.activateFileViewerSelecting([url])", self.expanded_source)
        self.assertIn("url.deletingLastPathComponent().path", self.expanded_source)

    def test_export_uses_brand_slogan_and_preserves_unknown_cost_semantics(self) -> None:
        self.assertIn("The best model is the one ready now.", self.export_source)
        self.assertIn('Text("modeldial.com")', self.export_source)
        self.assertIn("LeaderboardExportPalette.accent", self.export_source)
        footer_source = self._section(
            self.export_source,
            "private var brandFooter",
            "private func durationText",
        )
        self.assertIn("Spacer(minLength: 24)", footer_source)
        self.assertLess(
            footer_source.index('Text("modeldial.com")'),
            footer_source.index("The best model is the one ready now."),
        )
        self.assertNotIn("总分 ≥ 榜首总分 − 4", self.export_source)
        self.assertIn('guard let cost else { return copy.text("未提供") }', self.export_source)
        self.assertIn('cost < 0.01 { return "<$0.01" }', self.export_source)
        self.assertIn('String(format: "$%.2f", cost)', self.export_source)
        self.assertNotIn('String(format: "$%.4f", cost)', self.export_source)
        self.assertNotIn("费用与耗时均为完成本轮题包的汇总参考值", self.export_source)
        self.assertIn('return "\\(minutes)m \\(remainder)s"', self.export_source)
        self.assertNotIn("remainder == 0 ?", self.export_source)

    def test_export_opens_a_real_preview_before_copy_save_or_share(self) -> None:
        preview_source = self._section(
            self.export_source,
            "private struct LeaderboardExportPreviewView: View",
            "@MainActor\nenum LeaderboardImageExporter",
        )
        self.assertIn("Image(nsImage: previewImage)", preview_source)
        self.assertIn('Picker(copy.text("导出语言")', preview_source)
        self.assertIn("ForEach(LeaderboardExportLanguage.allCases)", preview_source)
        self.assertIn('Button(action: copyImage)', preview_source)
        self.assertIn('Button(action: saveImage)', preview_source)
        self.assertIn("ShareLink(item: shareURL)", preview_source)
        self.assertIn("NSPasteboard.general", preview_source)
        self.assertIn("let panel = NSSavePanel()", preview_source)
        self.assertIn("PNG · 1080 × 1920", preview_source)

        flow_source = self._section(
            self.export_source,
            "static func presentExportFlow",
            "static func renderPNG",
        )
        self.assertIn("NSHostingController(rootView: preview)", flow_source)
        self.assertIn("windowController.showWindow(nil)", flow_source)
        self.assertIn("previewWindow.makeKeyAndOrderFront(nil)", flow_source)
        self.assertIn("previewWindow.contentMinSize = NSSize(width: 660, height: 640)", flow_source)
        self.assertIn(
            "rawValue: NSWindow.Level.popUpMenu.rawValue + 1",
            flow_source,
        )
        self.assertNotIn("previewWindow.level = .floating", flow_source)
        self.assertNotIn("presentSavePanel(", flow_source)

    def test_export_uses_result_freshness_in_image_and_export_time_in_filename(self) -> None:
        content_model = self._section(
            self.export_source,
            "struct LeaderboardExportContent",
            "struct LeaderboardExportView",
        )
        self.assertIn("let resultsUpdatedAt: Date?", content_model)
        self.assertIn("let exportedAt: Date", content_model)
        self.assertIn('formatter.dateFormat = "yyyy-MM-dd-HHmmss"', content_model)
        self.assertIn("language.filenameCode", content_model)

        self.assertIn("copy.resultsAsOfText(content.resultsUpdatedAt)", self.export_source)
        export_projection = self._section(
            self.expanded_source,
            "private func exportLeaderboardImage",
            "private func presentLeaderboardExport",
        )
        self.assertIn("resultsUpdatedAt: leaderboardResultsUpdatedAt", export_projection)
        self.assertIn("exportedAt: Date()", export_projection)
        timestamp_source = self._section(
            self.expanded_source,
            "private var leaderboardResultsUpdatedAt",
            "private func parseLeaderboardTimestamp",
        )
        self.assertIn("store.radarResultsUpdatedAt", timestamp_source)

    def test_build_packages_flat_share_mark(self) -> None:
        self.assertIn(
            'cp "Resources/ModeldialShareMark.svg" "$RES_DIR/ModeldialShareMark.svg"',
            self.build_source,
        )
        self.assertIn(
            'Resources/ModeldialWordmark.svg',
            self.build_source,
        )
        self.assertIn('"$RES_DIR/ModeldialWordmark.svg"', self.build_source)

    def _section(self, source: str, start: str, end: str) -> str:
        self.assertIn(start, source)
        self.assertIn(end, source)
        return source.split(start, 1)[1].split(end, 1)[0]


if __name__ == "__main__":
    unittest.main()
