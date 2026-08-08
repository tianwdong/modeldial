from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SettingsCandidatePresenterTest(unittest.TestCase):
    def test_view_consumes_candidate_and_evidence_presentations(self) -> None:
        view = (ROOT / "Sources/Views/SettingsView.swift").read_text(encoding="utf-8")
        presenter = (
            ROOT / "Sources/Model/SettingsCandidatePresenter.swift"
        ).read_text(encoding="utf-8")
        ingress_presenter = (
            ROOT / "Sources/Model/SettingsIngressPresenter.swift"
        ).read_text(encoding="utf-8")

        self.assertIn("SettingsIngressPresenter.present(", view)
        self.assertIn("candidateProjections:", view)
        self.assertIn("SettingsCandidatePresenter.presentation(", ingress_presenter)
        self.assertIn("SettingsCandidatePresenter.providerID(", ingress_presenter)
        self.assertIn(
            "SettingsCandidatePresenter.evidencePresentation(for: evidence)",
            view,
        )
        self.assertIn(
            "candidateEvidenceColor(evidencePresentation.tone",
            view,
        )
        for removed_rule in (
            "APIReasoningAliasIdentity",
            "CandidateDisplayIdentity",
            "reasoningProfileIDs",
            "apiReasoningAliasIdentities",
            "candidateDisplayIdentity",
            "modelFamilyDisplayName",
            "scanProfileDisplayName",
        ):
            self.assertNotIn(removed_rule, view)
        self.assertNotIn("name.contains", view)
        self.assertNotIn("baseURL.contains", view)
        for evidence_rule in (
            "evidence.questionCompleted",
            "evidence.latestValidAt",
            "evidence.isUsingPreviousValidResult",
            "evidence.isCurrentPackComparable",
            "evidence.scoreText",
            "displayEvidenceTime",
        ):
            self.assertNotIn(evidence_rule, view)
        self.assertIn("SettingsCandidateEvidencePresentation", presenter)
        self.assertIn("static func evidencePresentation(", presenter)
        self.assertNotIn("SwiftUI", presenter)
        self.assertNotIn("AppKit", presenter)

    def test_presenter_behavior_is_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "settings-candidate-presenter-tests"
            compile_result = subprocess.run(
                [
                    "swiftc",
                    "-module-cache-path",
                    str(Path(temp_dir) / "module-cache"),
                    "Sources/Model/AppLanguageStore.swift",
                    "Sources/Localization/L10n.swift",
                    "Sources/Model/LocalEncryptedSecretStore.swift",
                    "Sources/Model/SelectionModels.swift",
                    "Sources/Model/SettingsCandidatePresenter.swift",
                    "tests/swift/SettingsCandidatePresenterTests.swift",
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
                "Settings candidate presenter tests passed",
                run_result.stdout,
            )


if __name__ == "__main__":
    unittest.main()
