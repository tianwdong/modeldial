from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scanner.frontend_deterministic_evaluation import (
    FRONTEND_BENCHMARK_REF,
    apply_deterministic_frontend_points,
    default_deterministic_frontend_question_root,
    load_deterministic_frontend_question,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


class FrontendV17DeterministicProductionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.package = load_deterministic_frontend_question(
            default_deterministic_frontend_question_root(PROJECT_ROOT)
        )

    def _browser_payload(self) -> dict[str, object]:
        checks = {
            check["id"]: {"passed": True, "evidence": "passed"}
            for dimension in self.package.contract["dimensions"]
            for check in dimension["checks"]
            if check["mode"] == "browser"
        }
        environment = self.package.render_environment
        assert environment is not None
        return {
            "check_results": checks,
            "diagnostics": {
                "pageErrors": [],
                "consoleErrors": [],
                "externalRequests": [],
            },
            "app_shell_rendered": True,
            "initial_data_rendered": True,
            "environment": {
                "browserVersion": environment["browser_version"],
                "locale": environment["locale"],
                "timeZone": environment["timezone_id"],
                "deviceScaleFactor": environment["device_scale_factor"],
                "reducedMotion": environment["reduced_motion"],
                "colorScheme": environment["color_scheme"],
            },
        }

    def _trace_payload(self) -> dict[str, object]:
        return {
            "certificate_results": {
                check_id: {"passed": True, "evidence": {"ok": True}}
                for check_id in self.package.contract["trace_contract"]["source_ids"]
            },
            "workflow_errors": {},
        }

    def _candidate_manifest(self) -> dict[str, object]:
        reference = self.package.reference_manifest
        assert reference is not None
        return {
            "schema_version": "frontend_visual_evidence_manifest_v1",
            "states": [
                {**state, "demonstrated": True, "error": ""}
                for state in reference["states"]
            ],
            "contact_sheet": {"filename": "contact-sheet.png"},
        }

    def test_default_package_is_same_v17_input_with_deterministic_v2_scoring(self) -> None:
        root = self.package.root
        self.assertEqual(root.name, "case_stream_explorer_v17_v2")
        self.assertEqual(FRONTEND_BENCHMARK_REF, "frontend-case-stream-explorer-v17@v2")
        self.assertEqual(self.package.contract["benchmark_ref"], FRONTEND_BENCHMARK_REF)
        self.assertFalse(self.package.contract["llm_visual_judge"])
        self.assertFalse(hasattr(self.package, "visual_rubric"))
        self.assertEqual(
            _sha256(root / "prompt.md"),
            "sha256:fad7572c0bbfaf358ff491ea72d535504497039db865ce333df152c53e42b4cf",
        )
        self.assertEqual(
            _sha256(root / "starter.html"),
            "sha256:c302de3bfd237e38f0594604a1cd35f8fbd1ebdf35bc4bb5373d0e6b5124b6fd",
        )

    def test_asset_lock_covers_every_scoring_input_and_runtime(self) -> None:
        expected = {
            "prompt",
            "starter",
            "score_contract",
            "browser_scorer",
            "trace_scorer",
            "visual_evidence",
            "visual_rules",
            "render_environment",
            "reference_manifest",
            "reference_default_desktop",
            "reference_default_tablet",
            "reference_default_mobile",
            "reference_selected_saving",
            "reference_failure",
            "reference_desktop_inspector",
            "reference_mobile_inspector",
            "deterministic_runtime",
            "image_metrics_runtime",
            "legacy_helper_runtime",
        }
        assets = self.package.asset_lock["assets"]
        self.assertEqual(set(assets), expected)
        for item in assets.values():
            path = PROJECT_ROOT / item["path"]
            self.assertEqual(_sha256(path), item["sha256"])

    def test_asset_lock_rejects_changed_question_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            package_root = (
                temporary_root
                / "questions"
                / "frontend"
                / "case_stream_explorer_v17_v2"
            )
            shutil.copytree(self.package.root, package_root)
            scanner_root = temporary_root / "scanner"
            scanner_root.mkdir()
            for name in (
                "frontend_deterministic_evaluation.py",
                "frontend_image_metrics.py",
                "frontend_evaluation.py",
            ):
                shutil.copy2(PROJECT_ROOT / "scanner" / name, scanner_root / name)
            (package_root / "prompt.md").write_text(
                self.package.prompt_template + "\nchanged\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RuntimeError,
                "locked asset changed: prompt",
            ):
                load_deterministic_frontend_question(package_root)

    def test_frozen_reference_scores_100_without_an_llm(self) -> None:
        score = apply_deterministic_frontend_points(
            self._browser_payload(),
            self._trace_payload(),
            self._candidate_manifest(),
            candidate_dir=self.package.reference_root,
            package=self.package,
        )
        self.assertEqual(score["status"], "complete")
        self.assertEqual(score["ranking_score"], 100)
        self.assertEqual(score["behavior_score"], 55)
        self.assertEqual(score["workflow_score"], 30)
        self.assertEqual(score["visual_score"], 15)
        self.assertFalse(score["llm_visual_judge"])

    def test_workflow_requires_every_member_trace(self) -> None:
        trace = self._trace_payload()
        trace["certificate_results"]["C02"]["passed"] = False
        score = apply_deterministic_frontend_points(
            self._browser_payload(),
            trace,
            self._candidate_manifest(),
            candidate_dir=self.package.reference_root,
            package=self.package,
        )
        self.assertEqual(score["workflow_score"], 20)
        self.assertIn("W01", score["failed_check_ids"])
        self.assertNotIn("W02", score["failed_check_ids"])
        self.assertNotIn("W03", score["failed_check_ids"])

    def test_starter_anchored_similarity_awards_zero_visual_progress(self) -> None:
        visual_rules = self.package.visual_rules
        assert visual_rules is not None
        metrics = [
            {
                "ssim": float(rule["starter_similarity"]),
                "color": float(rule["starter_similarity"]),
                "edge_f1": float(rule["starter_similarity"]),
                "similarity": float(rule["starter_similarity"]),
            }
            for rule in visual_rules["rules"]
        ]
        with patch(
            "scanner.frontend_deterministic_evaluation.visual_similarity",
            side_effect=metrics,
        ):
            score = apply_deterministic_frontend_points(
                self._browser_payload(),
                self._trace_payload(),
                self._candidate_manifest(),
                candidate_dir=self.package.reference_root,
                package=self.package,
            )
        self.assertEqual(score["visual_score"], 0)
        self.assertEqual(score["ranking_score"], 85)


if __name__ == "__main__":
    unittest.main()
