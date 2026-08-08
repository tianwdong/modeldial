from pathlib import Path
import subprocess
import tempfile
import unittest


class IngressReadinessContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent.parent
        self.readiness_path = self.root / "Sources" / "Model" / "IngressReadiness.swift"

    def test_readiness_presenter_consumes_backend_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "ingress-readiness-tests"
            compile_result = subprocess.run(
                [
                    "swiftc",
                    "Sources/Model/AppLanguageStore.swift",
                    "Sources/Localization/L10n.swift",
                    "Sources/Model/LocalEncryptedSecretStore.swift",
                    "Sources/Model/SelectionModels.swift",
                    "Sources/Model/IngressReadiness.swift",
                    "tests/swift/IngressReadinessTests.swift",
                    "-o",
                    str(executable),
                ],
                cwd=self.root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            run_result = subprocess.run(
                [str(executable)],
                cwd=self.root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(run_result.returncode, 0, run_result.stderr)
            self.assertIn(
                "Ingress readiness presenter tests passed",
                run_result.stdout,
            )

    def test_settings_use_one_readiness_model_for_list_and_detail(self) -> None:
        source = (self.root / "Sources" / "Views" / "SettingsView.swift").read_text(
            encoding="utf-8"
        )
        for token in (
            "ingressReadiness(for: item)",
            "readinessStatusBadge(readiness)",
            "ingressReadinessTrack(readiness)",
            "ingressReadinessAction(readiness, item: item)",
        ):
            self.assertIn(token, source)
        self.assertIn("snapshot?.settingsProjection.connections.first", source)
        self.assertIn("IngressReadiness.present(projection)", source)
        self.assertNotIn("evidenceCards:", source)
        self.assertNotIn("readyIngressSourceCount", source)
        self.assertNotIn("pendingIngressSourceCount", source)
        self.assertNotIn("validBaselineCandidateCount", source)

    def test_footer_owns_idle_freshness_without_using_settings_activity_copy(self) -> None:
        overview = (self.root / "Sources" / "Views" / "ExpandedSelectionView.swift").read_text(
            encoding="utf-8"
        )
        operational_presenter = (
            self.root / "Sources" / "Model" / "OperationalStatePresenter.swift"
        ).read_text(encoding="utf-8")
        settings = (self.root / "Sources" / "Views" / "SettingsView.swift").read_text(
            encoding="utf-8"
        )
        self.assertIn("private var footerDataStatusText: String?", overview)
        self.assertIn("operationalPresentation.footerDataStatusText", overview)
        self.assertNotIn("footerSourceStatusText", overview)
        self.assertIn("localCompletedAt: store.radarDashboard?.runMetadata.completedAt", overview)
        self.assertIn('source: L10n.tr("本机实测")', operational_presenter)
        self.assertIn("selectionStore.scanActivityText", settings)
        self.assertNotIn('return "已同步"', overview)


if __name__ == "__main__":
    unittest.main()
