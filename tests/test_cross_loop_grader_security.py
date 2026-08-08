from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scanner import cross_loop_singleflight_grader as grader
from scanner import scalar_cross_loop_flight_grader as scalar_grader
from scanner.bounded_subprocess import BoundedSubprocessOutputError


class CrossLoopGraderSecurityTest(unittest.TestCase):
    def test_case_worker_output_budget_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(
                grader,
                "run_bounded_process",
                side_effect=BoundedSubprocessOutputError(
                    ["worker"],
                    output_limit_bytes=128,
                    total_output_bytes=129,
                ),
            ):
                result = grader._run_case(Path(temp_dir), "case")

        self.assertEqual(
            result,
            {"passed": False, "error": "OutputLimitError:case output exceeded budget"},
        )

    def test_catalog_worker_output_budget_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(
                grader,
                "run_bounded_process",
                side_effect=BoundedSubprocessOutputError(
                    ["worker"],
                    output_limit_bytes=128,
                    total_output_bytes=129,
                ),
            ):
                result = grader._run_catalog(Path(temp_dir))

        self.assertIsNone(result)

    def test_scalar_custom_worker_output_budget_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(
                scalar_grader,
                "run_bounded_process",
                side_effect=BoundedSubprocessOutputError(
                    ["worker"],
                    output_limit_bytes=128,
                    total_output_bytes=129,
                ),
            ):
                result = scalar_grader._run_custom_case(Path(temp_dir), "case")

        self.assertEqual(
            result,
            {"passed": False, "error": "OutputLimitError:case output exceeded budget"},
        )

    def test_controller_does_not_import_candidate_for_case_catalog(self) -> None:
        case_ids = [
            case_id
            for cluster_case_ids in grader.CLUSTERS.values()
            for case_id in cluster_case_ids
        ]
        facets = list(grader.FACET_LABELS)
        catalog = [
            {"case_id": case_id, "facet": facets[index % len(facets)]}
            for index, case_id in enumerate(case_ids)
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(grader, "_run_catalog", return_value=catalog),
                patch.object(grader, "_run_case", return_value={"passed": True}),
                patch.object(
                    grader,
                    "_load_module",
                    side_effect=AssertionError("candidate import escaped controller"),
                ),
            ):
                result = grader._grade_tree(Path(temp_dir))

        self.assertTrue(result["passed"])
        self.assertEqual(result["score"], grader.MAX_SCORE)


if __name__ == "__main__":
    unittest.main()
