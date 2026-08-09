from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ViewPresenterBoundaryTest(unittest.TestCase):
    def test_views_only_render_model_layer_presenters(self) -> None:
        settings = (ROOT / "Sources/Views/SettingsView.swift").read_text(encoding="utf-8")
        compact = (ROOT / "Sources/Views/CompactSessionPanelView.swift").read_text(
            encoding="utf-8"
        )
        settings_presenter = (
            ROOT / "Sources/Model/SettingsAdvisorReasonPresenter.swift"
        ).read_text(encoding="utf-8")
        metric_presenter = (
            ROOT / "Sources/Model/IslandDecisionMetricPresentation.swift"
        ).read_text(encoding="utf-8")
        compact_presenter = (
            ROOT / "Sources/Model/CompactSessionPresenter.swift"
        ).read_text(encoding="utf-8")
        radar_presenter = (ROOT / "Sources/Model/RadarPresenter.swift").read_text(
            encoding="utf-8"
        )
        operational_presenter = (
            ROOT / "Sources/Model/OperationalStatePresenter.swift"
        ).read_text(encoding="utf-8")
        configuration_presenter = (
            ROOT / "Sources/Model/ConfigurationEvidencePresenter.swift"
        ).read_text(encoding="utf-8")
        settings_ingress_presenter = (
            ROOT / "Sources/Model/SettingsIngressPresenter.swift"
        ).read_text(encoding="utf-8")
        profile_scope_presenter = (
            ROOT / "Sources/Model/EvaluationProfileScopePresenter.swift"
        ).read_text(encoding="utf-8")
        radar_entry_presenter = (
            ROOT / "Sources/Model/RadarEntryPresenter.swift"
        ).read_text(encoding="utf-8")
        comparison_selection_presenter = (
            ROOT / "Sources/Model/ComparisonSelectionPresenter.swift"
        ).read_text(encoding="utf-8")
        active_session_presenter = (
            ROOT / "Sources/Model/ActiveSessionPresenter.swift"
        ).read_text(encoding="utf-8")
        update_check_presenter = (
            ROOT / "Sources/Update/UpdateCheckPresentation.swift"
        ).read_text(encoding="utf-8")
        expanded = (ROOT / "Sources/Views/ExpandedSelectionView.swift").read_text(
            encoding="utf-8"
        )
        selection_store = (ROOT / "Sources/Model/SelectionStore.swift").read_text(
            encoding="utf-8"
        )

        self.assertIn("SettingsAdvisorReasonPresenter.presentation(", settings)
        self.assertIn("Text(advisorReason.text)", settings)
        self.assertNotIn("diagnosticAdvisorReasonText", settings)
        self.assertNotIn('case "current_identity_unmapped"', settings)
        self.assertIn('case "current_identity_unmapped"', settings_presenter)
        self.assertNotIn("AppKit", settings_presenter)
        self.assertNotIn("SwiftUI", settings_presenter)

        self.assertIn("store.compactRecommendationPresentation", compact)
        self.assertNotIn("store.radarRepresentativeDecision", compact)
        self.assertNotIn("store.radarDisplaySource", compact)
        self.assertNotIn('decision == "recommend"', compact)
        self.assertIn("enum CompactSessionPresenter", compact_presenter)
        self.assertIn("enum CompactRecommendationComparisonState: Equatable", compact_presenter)
        self.assertIn("comparisonState", compact)
        self.assertIn("IslandDecisionMetricPresentation.quality(decision)", compact_presenter)
        self.assertIn("IslandDecisionMetricPresentation.compactTime(decision)", compact_presenter)
        self.assertIn(
            "IslandDecisionMetricPresentation.compactReferenceCost(",
            compact_presenter,
        )
        self.assertNotIn("enum IslandDecisionMetricPresentation", compact)
        self.assertIn("enum IslandDecisionMetricPresentation", metric_presenter)
        self.assertNotIn("private static func reduction(", metric_presenter)
        self.assertIn('L10n.tr("快 %d%%", percent)', metric_presenter)
        self.assertIn('L10n.tr("省 %d%%", percent)', metric_presenter)
        self.assertNotIn("SwiftUI", metric_presenter)

        self.assertIn("SettingsIngressPresenter.present(", settings)
        self.assertIn("settingsIngressPresentation.customSourceSections", settings)
        self.assertNotIn("Dictionary(uniqueKeysWithValues: ingress.sources", settings)
        self.assertNotIn("customCandidateIDs.intersection(", settings)
        self.assertNotIn("var candidatesByFamilyID", settings)
        self.assertNotIn("SwiftUI", settings_ingress_presenter)
        self.assertNotIn("AppKit", settings_ingress_presenter)

        self.assertIn("UpdateCheckPresenter.presentation(", settings)
        self.assertIn("enum UpdateCheckPresenter", update_check_presenter)
        self.assertNotIn("SwiftUI", update_check_presenter)
        self.assertNotIn("AppKit", update_check_presenter)

        for call in (
            "RadarPresenter.decision(",
            "RadarPresenter.sessionSummary(",
            "RadarPresenter.activeUsage(",
            "RadarPresenter.leaderboardRow(",
            "RadarPresenter.leaderboardExportSemantics(",
            "RadarPresenter.referenceCost(",
            "RadarPresenter.surface(",
            "RadarEntryPresenter.entries(",
            "OperationalStatePresenter.availability(",
            "OperationalStatePresenter.currentModel(",
            "OperationalStatePresenter.ingress(",
            "OperationalStatePresenter.repair(",
            "OperationalStatePresenter.presentation(",
            "ConfigurationEvidencePresenter.presentation(",
            "ConfigurationEvidencePresenter.routing(",
            "ComparisonPresenter.decisionPresentation(",
            "ComparisonPresenter.questionRows(",
            "ComparisonPresenter.realizedBenefit(",
            "ComparisonPresenter.realUsage(",
        ):
            self.assertIn(call, expanded)
        self.assertIn("EvaluationProfileScopePresenter.present(", expanded)
        self.assertIn("ComparisonSelectionPresenter.dataset(", expanded)
        self.assertIn("ComparisonSelectionPresenter.select(", expanded)
        self.assertIn("ActiveSessionPresenter.overview(", expanded)
        self.assertIn("ActiveSessionPresenter.present(session)", compact)
        self.assertNotIn("ActiveSessionPresentation", expanded)
        self.assertNotIn("ActiveSessionPresentation", compact)
        for removed_view_rule in (
            "private var radarV2DecisionTitleText",
            "private var radarV2DecisionReasonText",
            "private var radarQualityChangeText",
            "private func radarTags(",
            "private func radarCanonicalRank(",
            "private var radarLeaderboardEmptyReason",
            "private var radarReferenceCostChangeText",
            "private var currentSwitchReasonText",
            "private var confidenceReasonTexts",
            "private var firstRunnerUpEntry",
            "private func workloadAggregate(",
            "private var tokenEvidenceNote",
            "var usesCurrentQuestionScoreContract",
            "var requiresCurrentPackRescan",
            "var canDisplayCurrentQuestionScores",
            "var canDisplayCurrentOverallScore",
            "private struct EvaluationProfileCandidateDelta",
            "private var evaluationProfileCandidateDelta",
            "private func progressCounts(",
            "private func progressLabel(",
            "private struct DisplayEntry",
            "let source = decisions.isEmpty",
        ):
            self.assertNotIn(removed_view_rule, expanded)
        self.assertNotIn("semantic_q1_q5_equal_v2", expanded)
        for removed_operational_rule in (
            "switch advisor.decision",
            'case "trial_switch":\n            targetCandidateID',
            "configurationIdentityDifferenceText",
            "configurationConnectionText",
            "configurationRouteFingerprint",
            "configurationCompletionText",
            "sharedConnectionText(current:",
            "sharedCompletionText(current:",
            "questionScoreValue(",
            'decision.decision == "recommend"',
            'decision?.decision == "recommend"',
            "switch currentModelDetectionStatus",
            'currentModelMode == "manual"',
            "result.semanticTotal == semantic.scoreMax",
            "ingress.sources.filter",
            "ingress.connections.compactMap",
            "reasoningProfiles: Set<String>",
            "enabledModelCandidateCount == 0",
            "effortCounts = Dictionary",
            'status == "timeout"',
            "currentPhase == .repair",
            'store.radarDisplaySource == "local_evaluation"',
            'store.radarDisplaySource == "official_snapshot"',
        ):
            self.assertNotIn(removed_operational_rule, expanded)
        self.assertNotIn("RadarPresenter.relevantQuestionSemantics(", expanded)
        self.assertIn("store.radarQuestionSemantics", expanded)
        self.assertIn("RadarPresenter.relevantQuestionSemantics(", selection_store)
        self.assertIn("RadarPresenter.configurationDecision(", expanded)
        self.assertIn(
            "operationalPresentation.heroDecisionTitle",
            expanded,
        )
        self.assertIn(
            "operationalPresentation.heroDecisionReason",
            expanded,
        )
        self.assertIn(
            "operationalPresentation.confidenceLabel",
            expanded,
        )
        self.assertNotIn("SwiftUI", operational_presenter)
        self.assertNotIn("AppKit", operational_presenter)
        self.assertNotIn("usesRadarV2", operational_presenter)
        self.assertNotIn("SwiftUI", configuration_presenter)
        self.assertNotIn("AppKit", configuration_presenter)
        self.assertIn("projectedRow?.rank", radar_presenter)
        self.assertIn("projectedRow?.decisionTagKinds", radar_presenter)
        self.assertIn("projectedRow?.targetLabels", radar_presenter)
        self.assertNotIn("SwiftUI", radar_presenter)
        self.assertNotIn("AppKit", radar_presenter)
        for presenter in (
            profile_scope_presenter,
            radar_entry_presenter,
            comparison_selection_presenter,
            active_session_presenter,
        ):
            self.assertNotIn("SwiftUI", presenter)
            self.assertNotIn("AppKit", presenter)

    def test_presenter_behavior_is_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "view-presenter-tests"
            compile_result = subprocess.run(
                [
                    "swiftc",
                    "-module-cache-path",
                    str(Path(temp_dir) / "module-cache"),
                    "Sources/Model/AppLanguageStore.swift",
                    "Sources/Localization/L10n.swift",
                    "Sources/Model/LocalEncryptedSecretStore.swift",
                    "Sources/Model/SelectionModels.swift",
                    "Sources/Model/SettingsAdvisorReasonPresenter.swift",
                    "Sources/Model/IslandDecisionMetricPresentation.swift",
                    "Sources/Support/ModelIdentityPresentation.swift",
                    "Sources/Model/CompactSessionPresenter.swift",
                    "tests/swift/ViewPresenterTests.swift",
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
            self.assertIn("View presenter tests passed", run_result.stdout)

    def test_radar_presenter_behavior_is_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "radar-presenter-tests"
            compile_result = subprocess.run(
                [
                    "swiftc",
                    "-module-cache-path",
                    str(Path(temp_dir) / "module-cache"),
                    "Sources/Model/AppLanguageStore.swift",
                    "Sources/Localization/L10n.swift",
                    "Sources/Model/LocalEncryptedSecretStore.swift",
                    "Sources/Model/SelectionModels.swift",
                    "Sources/Model/ComparisonPresenter.swift",
                    "Sources/Support/ModelIdentityPresentation.swift",
                    "Sources/Model/IslandDecisionMetricPresentation.swift",
                    "Sources/Model/RadarPresenter.swift",
                    "tests/swift/RadarPresenterTests.swift",
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
            self.assertIn("Radar presenter tests passed", run_result.stdout)

    def test_operational_and_configuration_presenters_are_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "operational-configuration-presenter-tests"
            compile_result = subprocess.run(
                [
                    "swiftc",
                    "-module-cache-path",
                    str(Path(temp_dir) / "module-cache"),
                    "Sources/Model/AppLanguageStore.swift",
                    "Sources/Localization/L10n.swift",
                    "Sources/Model/GlanceState.swift",
                    "Sources/Model/ComparisonPresenter.swift",
                    "Sources/Support/ModelIdentityPresentation.swift",
                    "Sources/Model/OperationalStatePresenter.swift",
                    "Sources/Model/ConfigurationEvidencePresenter.swift",
                    "tests/swift/OperationalAndConfigurationPresenterTests.swift",
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
            self.assertIn(
                "Operational and configuration presenter tests passed",
                run_result.stdout,
            )

    def test_phase_2_gate_4_presenters_are_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "phase-2-gate-4-presenter-tests"
            compile_result = subprocess.run(
                [
                    "swiftc",
                    "-module-cache-path",
                    str(Path(temp_dir) / "module-cache"),
                    "Sources/Model/LocalEncryptedSecretStore.swift",
                    "Sources/Model/AppLanguageStore.swift",
                    "Sources/Localization/L10n.swift",
                    "Sources/Model/SelectionModels.swift",
                    "Sources/Model/GlanceState.swift",
                    "Sources/Model/ComparisonPresenter.swift",
                    "Sources/Support/ModelIdentityPresentation.swift",
                    "Sources/Model/IslandDecisionMetricPresentation.swift",
                    "Sources/Model/RadarPresenter.swift",
                    "Sources/Model/OperationalStatePresenter.swift",
                    "Sources/Model/SettingsCandidatePresenter.swift",
                    "Sources/Model/SettingsIngressPresenter.swift",
                    "Sources/Model/EvaluationProfileScopePresenter.swift",
                    "Sources/Model/RadarEntryPresenter.swift",
                    "Sources/Model/ComparisonSelectionPresenter.swift",
                    "Sources/Model/ActiveSessionPresenter.swift",
                    "tests/swift/Phase2Gate4PresenterTests.swift",
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
            self.assertIn("Phase 2 gate 4 presenter tests passed", run_result.stdout)


if __name__ == "__main__":
    unittest.main()
