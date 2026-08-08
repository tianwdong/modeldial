from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ProductDirection22UIContractTests(unittest.TestCase):
    def test_compact_recommendation_keeps_exact_effort_visible(self) -> None:
        store = (ROOT / "Sources/Model/SelectionStore.swift").read_text(encoding="utf-8")
        glance = (ROOT / "Sources/Model/GlanceState.swift").read_text(encoding="utf-8")
        self.assertIn(": recommendation.effortLabel,", glance)
        self.assertNotIn("primaryBenefitText", glance)
        self.assertNotIn("radarPrimaryBenefitText", store)

    def test_hover_has_recommendation_explanation_and_only_first_task(self) -> None:
        source = (ROOT / "Sources/Views/CompactSessionPanelView.swift").read_text(encoding="utf-8")
        metric_presenter = (
            ROOT / "Sources/Model/IslandDecisionMetricPresentation.swift"
        ).read_text(encoding="utf-8")
        presenter = (
            ROOT / "Sources/Model/CompactSessionPresenter.swift"
        ).read_text(encoding="utf-8")
        store = (ROOT / "Sources/Model/SelectionStore.swift").read_text(encoding="utf-8")
        self.assertIn("if let session = sessions.first", source)
        self.assertNotIn("ForEach(", source)
        self.assertIn("recommendationSummary", source)
        self.assertIn("质量", source)
        self.assertIn('label: L10n.tr("成本")', source)
        self.assertIn("store.compactRecommendationPresentation", source)
        self.assertIn("var radarDisplayFreshness: String?", store)
        keep_metrics = source.split("private func recommendationMetrics(", 1)[1].split(
            "private func recommendationMetric", 1
        )[0]
        self.assertIn("metrics.quality", keep_metrics)
        self.assertIn("metrics.time", keep_metrics)
        self.assertIn("metrics.referenceCost", keep_metrics)
        self.assertNotIn("enum IslandDecisionMetricPresentation", source)
        self.assertNotIn("private static func reduction(", metric_presenter)
        self.assertIn("compactTime(decision)", metric_presenter)
        self.assertIn("compactReferenceCost(decision, isPartial: isPartial)", metric_presenter)
        self.assertNotIn("未达门槛", keep_metrics)
        self.assertIn("currentModelConfigurationId", presenter)
        self.assertIn(
            "targetID = decision?.currentModelConfigurationId",
            presenter,
        )
        self.assertNotIn(
            "?? $0.comparisonCandidateModelConfigurationId",
            presenter,
        )
        self.assertIn("private var recommendationCandidateColor: Color", source)
        self.assertNotIn('decision == "recommend"', source)
        self.assertNotIn("store.radarDisplaySource", source)
        self.assertNotIn("store.radarDisplayFreshness", source)
        self.assertIn('decision?.decision == "recommend"', presenter)
        self.assertNotIn('recommendationComparisonTarget.map { "对比', source)
        self.assertNotIn("Button(", source)

    def test_radar_keep_state_uses_closest_candidate_and_actual_metrics(self) -> None:
        source = (ROOT / "Sources/Views/ExpandedSelectionView.swift").read_text(encoding="utf-8")
        presenter = (ROOT / "Sources/Model/RadarPresenter.swift").read_text(
            encoding="utf-8"
        )
        candidate = presenter.split("static func candidateConfigurationID(", 1)[1].split(
            "static func decision(", 1
        )[0]
        self.assertIn("candidateModelConfigurationId", candidate)
        self.assertIn("comparisonCandidateModelConfigurationId", candidate)
        self.assertIn('comparisonLabel = L10n.tr("最接近候选")', presenter)
        self.assertIn("RadarPresenter.decision(", source)
        self.assertIn("private var radarCandidateModelColor: Color", source)

    def test_first_run_explains_local_privacy_boundary(self) -> None:
        source = (ROOT / "Sources/Views/ExpandedSelectionView.swift").read_text(encoding="utf-8")
        empty_state = source.split("private struct ModelSetupEmptyState: View {", 1)[1].split(
            "struct ExpandedSelectionView", 1
        )[0]
        self.assertIn("不会读取凭据原文", empty_state)
        self.assertIn("不持久化、不上传", empty_state)

    def test_local_comparison_trend_uses_shared_six_round_axis(self) -> None:
        source = (ROOT / "Sources/Views/ExpandedSelectionView.swift").read_text(encoding="utf-8")
        self.assertIn('Text(L10n.tr("稳定性证据"))', source)
        self.assertIn("ComparisonPresenter.present(", source)
        self.assertIn("presenterLocalTrendSeries", source)
        self.assertIn("overallScoreRunIndices", source)
        self.assertIn("ComparisonScoreTrendChart", source)
        self.assertNotIn("private func comparisonTrendData(", source)
        self.assertNotIn("private struct ComparisonTrendPoint", source)

    def test_second_page_is_current_vs_one_selected_candidate(self) -> None:
        source = (ROOT / "Sources/Views/ExpandedSelectionView.swift").read_text(encoding="utf-8")
        detail = source.split("private var detailPage: some View {", 1)[1].split(
            "private var panelFooter", 1
        )[0]
        comparison = source.split("private struct ComparisonPage: View {", 1)[1]
        content = comparison.split("private func comparisonContent(", 1)[1].split(
            "private var comparisonHairline", 1
        )[0]
        self.assertIn("ComparisonPage(", detail)
        self.assertNotIn("StatisticsPage(", detail)
        self.assertIn("Text(detailHeaderPrimaryText)", source)
        self.assertIn('return L10n.tr("当前与候选")', source)
        self.assertIn("if isEvaluationDetailsExpanded", content)
        for layer in (
            "comparisonOverview(",
            "comparisonDecisionSummary(",
            ".frame(width: 352)",
            "evaluationDetailsBar",
        ):
            self.assertIn(layer, content)
        self.assertNotIn("geometry.size.width * 0.64", comparison)
        self.assertNotIn('Text("个人收益估算")', comparison)
        self.assertNotIn('Text("推荐判断")', comparison)
        self.assertIn("singleQuestionRiskSummary(presentation.questionRisk)", comparison)
        self.assertIn('Text(L10n.tr("稳定性证据"))', source)
        self.assertIn('? L10n.tr("返回趋势")', source)
        self.assertIn(': L10n.tr("评测详情")', source)
        self.assertIn("evaluationDetailsContent(", comparison)
        self.assertNotIn("evaluationDetailsOverlay(", comparison)
        self.assertNotIn(".offset(y: comparisonHeaderHeight)", comparison)
        self.assertNotIn("DisclosureGroup", comparison)
        self.assertNotIn("comparisonTrendHeight", comparison)
        self.assertNotIn(".frame(height: 96)", content)
        self.assertIn("evaluationDetailsBar", content)
        self.assertIn(".frame(height: 36)", content)
        self.assertIn('Text(L10n.tr("Token 明细"))', source)
        self.assertIn('Text(L10n.tr("配置证据"))', source)
        self.assertIn("statistics: localComparisonStatistics", detail)
        self.assertIn("leaderboard: localComparisonLeaderboard", detail)
        self.assertIn("pairwiseComparisons: localComparisonPairwiseComparisons", detail)
        self.assertIn("referenceSnapshot: selectedReferenceSnapshot", detail)
        self.assertIn("decisions: store.radarPortfolio?.decisions ?? []", detail)
        self.assertIn("advisorEvidence: store.radarEvidence", detail)
        self.assertIn("workload: store.snapshot?.codexInsights?.workload", detail)
        self.assertIn("recommendationUse:", detail)

    def test_token_detail_uses_authoritative_pairwise_or_publisher_usage(self) -> None:
        source = (ROOT / "Sources/Views/ExpandedSelectionView.swift").read_text(encoding="utf-8")
        comparison = source.split("private struct ComparisonPage: View {", 1)[1]
        self.assertIn("presenterPairwiseComparisons", comparison)
        self.assertIn("presenterOfficialTokens", comparison)
        self.assertIn("tokenSection(presentation.tokens)", comparison)
        self.assertNotIn("entry.questionResults.map", comparison)
        self.assertNotIn("private func comparisonTokenValues(", comparison)
        self.assertNotIn("private func sumTokens(", source)
        self.assertIn("Text(totals.evidenceNote)", comparison)
        self.assertNotIn("private var tokenEvidenceNote", comparison)

    def test_comparison_route_metrics_and_basis_follow_the_html_hierarchy(self) -> None:
        source = (ROOT / "Sources/Views/ExpandedSelectionView.swift").read_text(encoding="utf-8")
        comparison = source.split("private struct ComparisonPage: View {", 1)[1]

        self.assertIn('label: L10n.tr("当前")', comparison)
        self.assertIn('label: L10n.tr("候选")', comparison)
        self.assertIn("comparisonCurrentControl", comparison)
        self.assertIn("comparisonCandidateControl", comparison)
        identity_band = comparison.split(
            "private func comparisonDecisionSummary(", 1
        )[1].split("private var comparisonVerdictColor", 1)[0]
        trend_header = comparison.split(
            "private func scoreTrendSection(", 1
        )[1].split("private func comparisonTrendLegend", 1)[0]
        self.assertNotIn('Text("对比")', identity_band)
        self.assertNotIn('Image(systemName: current.id == candidate.id', identity_band)
        self.assertIn(".frame(height: 64)", identity_band)
        self.assertIn(".frame(height: 64)", trend_header)
        self.assertIn('title: L10n.tr("总分")', comparison)
        self.assertIn("realizedBenefitPresentation", comparison)
        self.assertIn("ComparisonPresenter.realizedBenefit(", comparison)
        self.assertIn('title: L10n.tr("等待时间")', comparison)
        self.assertIn('title: L10n.tr("参考费用")', comparison)
        self.assertNotIn("ComparisonPresenter.routeBasis(", comparison)
        self.assertNotIn('"同题包 · 路线未记录"', comparison)
        self.assertNotIn('Text("同题包、同路线")', comparison)
        self.assertNotIn("comparisonBasisFooter", comparison)
        self.assertIn('evidenceRow(L10n.tr("路线"), configuration.routeEvidenceText)', comparison)
        self.assertIn('Text(L10n.tr("最近 %d 次", trendData.slots.count))', comparison)
        self.assertNotIn("comparisonConfidenceBand", comparison)
        self.assertNotIn("trendSummaryRow", comparison)
        self.assertNotIn('Text("同轴自适应 · 最小跨度 10 分")', comparison)
        self.assertIn("private func evaluationDetailsContent(", comparison)
        self.assertNotIn("private func comparisonTrendHeight(totalHeight: CGFloat) -> CGFloat", comparison)

    def test_comparison_supports_switching_active_model_configuration(self) -> None:
        source = (ROOT / "Sources/Views/ExpandedSelectionView.swift").read_text(encoding="utf-8")
        self.assertIn("selectedCurrentConfigurationID", source)
        self.assertIn("comparisonChoices", source)
        self.assertIn("comparisonCurrentControl", source)
        benefit_projection = source.split(
            "private var realizedBenefitPresentation: ComparisonPresenter.RealizedBenefitPresentation? {", 1
        )[1].split("private func realizedBenefitMetric(", 1)[0]
        self.assertIn("recommendationUse?.benefitSummary", benefit_projection)
        self.assertIn("ComparisonPresenter.realizedBenefit(", benefit_projection)
        self.assertNotIn("recommendationUse?.epochs", benefit_projection)

    def test_comparison_supports_one_manual_candidate_per_current_configuration(self) -> None:
        source = (ROOT / "Sources/Views/ExpandedSelectionView.swift").read_text(encoding="utf-8")
        comparison = source.split("private struct ComparisonPage: View {", 1)[1]
        presenter = (ROOT / "Sources/Model/ComparisonPresenter.swift").read_text(encoding="utf-8")

        self.assertIn("manualCandidateByCurrentConfigurationID", comparison)
        self.assertIn("selectedManualCandidateID", comparison)
        self.assertIn("selectableManualCandidates", comparison)
        self.assertIn("comparisonCandidateControl", comparison)
        self.assertIn("ComparisonPresenter.decisionPresentation(", comparison)
        self.assertNotIn('return "手动对比"', comparison)
        self.assertIn('title: L10n.tr("手动对比")', presenter)
        self.assertIn("manualCandidateByCurrentConfigurationID.removeAll()", comparison)
        self.assertNotIn("setRecommendationPreference", comparison)

    def test_comparison_presenter_behavior_is_executable(self) -> None:
        source = (ROOT / "Sources/Views/ExpandedSelectionView.swift").read_text(encoding="utf-8")
        for call in (
            "ComparisonPresenter.qualityChange(",
            "ComparisonPresenter.timeChange(",
            "ComparisonPresenter.costChange(",
            "ComparisonPresenter.qualityGuard(",
            "ComparisonPresenter.present(",
        ):
            self.assertIn(call, source)
        self.assertIn("ConfigurationEvidencePresenter.presentation(", source)
        radar_presenter = (ROOT / "Sources/Model/RadarPresenter.swift").read_text(
            encoding="utf-8"
        )
        self.assertIn("ComparisonPresenter.configurationDecisionText(", radar_presenter)
        self.assertIn("ComparisonPresenter.recommendationReasonText(", radar_presenter)
        for old_view_rule in (
            "private func comparisonScoreDelta(",
            "private func comparisonTimeReduction(",
            "private func comparisonCostReduction(",
            "private func percentageReduction(",
            "private func qualityGuardText(",
            "private func comparisonBasisText(",
            "private func comparisonRouteEvidenceText(",
            "private func significantQuestionDropCount(",
            "private func isQuestionWarning(",
            "private func visibleTrendSlots(",
            "private func hasTrendGap(",
            "private func sumTokens(",
            "private func comparisonEvidence(",
            "private func comparisonTrendData(",
            "private func comparisonTokenValues(",
        ):
            self.assertNotIn(old_view_rule, source)
        self.assertNotIn("candidateScore - currentScore < -5", source)
        presenter = (ROOT / "Sources/Model/ComparisonPresenter.swift").read_text(encoding="utf-8")
        for removed_business_rule in (
            "compatibleReferenceSnapshotIndices",
            "significantQuestionIDs",
            "significantQuestionDropCount",
            "tokenTotals(",
            "percentageChange(",
            "scoreDelta(",
            "sumKnown(",
            "scoreValue(",
        ):
            self.assertNotIn(removed_business_rule, presenter)

        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "comparison-presenter-tests"
            compile_result = subprocess.run(
                [
                    "swiftc",
                    "-module-cache-path",
                    str(Path(temp_dir) / "module-cache"),
                    "Sources/Model/AppLanguageStore.swift",
                    "Sources/Localization/L10n.swift",
                    "Sources/Model/ComparisonPresenter.swift",
                    "tests/swift/ComparisonPresenterTests.swift",
                    "-o",
                    str(executable),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            run_result = subprocess.run(
                [str(executable)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(run_result.returncode, 0, run_result.stderr)
            self.assertIn("ComparisonPresenter tests passed", run_result.stdout)

    def test_comparison_evidence_does_not_mix_official_and_local_sources(self) -> None:
        source = (ROOT / "Sources/Views/ExpandedSelectionView.swift").read_text(encoding="utf-8")
        selection_presenter = (
            ROOT / "Sources/Model/ComparisonSelectionPresenter.swift"
        ).read_text(encoding="utf-8")
        self.assertIn("ComparisonSelectionPresenter.dataset(", source)
        self.assertIn("usesLocalDataset ? localStatistics : nil", selection_presenter)
        self.assertIn("usesOfficialSnapshot ? officialSnapshot : nil", selection_presenter)
        self.assertIn("localComparisonPairwiseComparisons", source)
        self.assertIn("presenterLocalTrendSeries", source)
        self.assertIn("presenterOfficialTrendSeries", source)
        self.assertIn("ComparisonPresenter.present(", source)
        self.assertNotIn("private var compatibleReferenceSnapshots", source)
        self.assertNotIn("ComparisonPresenter.compatibleReferenceSnapshotIndices", source)
        self.assertNotIn("referenceTrendPoints", source)
        self.assertIn("referenceEntry(for:", source)

    def test_radar_rank_and_decision_tags_use_authoritative_projection(self) -> None:
        source = (ROOT / "Sources/Views/ExpandedSelectionView.swift").read_text(encoding="utf-8")
        presenter = (ROOT / "Sources/Model/RadarPresenter.swift").read_text(
            encoding="utf-8"
        )
        self.assertIn("rank: presentation.rank", source)
        self.assertIn("RadarPresenter.leaderboardRow(", source)
        self.assertIn("canonicalLabels", source)
        self.assertIn("decisionTags.map(\\.kind)", source)
        tags = presenter.split("static func leaderboardRow(", 1)[1].split(
            "static func surface(", 1
        )[0]
        self.assertIn("projectedRow?.rank", tags)
        self.assertIn("projectedRow?.decisionTagKinds", tags)
        self.assertIn("projectedRow?.targetLabels", tags)
        self.assertNotIn("compactMap(\\.score).max()", tags)
        self.assertNotIn("compactMap(\\.elapsedSeconds).min()", tags)
        self.assertNotIn("compactMap(\\.referenceCostUsd)", tags)

    def test_multi_configuration_advice_is_bounded_and_scrollable(self) -> None:
        source = (ROOT / "Sources/Views/ExpandedSelectionView.swift").read_text(encoding="utf-8")
        popover = source.split("private var radarSessionsPopover: some View {", 1)[1].split(
            "private var multiConfigurationDecisions", 1
        )[0]
        self.assertIn('Text(L10n.tr("各配置建议"))', popover)
        self.assertIn("ScrollView", popover)
        self.assertIn("maxHeight: 176", popover)


if __name__ == "__main__":
    unittest.main()
