from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from scanner.analytics import build_dashboard_summary
from scanner.costing import PRICING_SNAPSHOT
from scanner.models import ResolvedScanTarget, ScanResult


ROOT = Path(__file__).resolve().parents[1]


class ComparisonProjectionTest(unittest.TestCase):
    def test_dashboard_projects_versioned_contract_pairwise_metrics_and_canonical_ranks(
        self,
    ) -> None:
        summary = _summary(current_default_candidate_id="local:model-a:high")

        self.assertEqual(
            summary["comparison_contract"],
            {
                "schema_version": 1,
                "question_pack_version": "coding-fast-v-test",
                "grader_version": "scoring-mode:semantic_q1_q5_equal_v2",
                "evaluation_snapshot_id": "local:run-comparison",
                "pricing_snapshot_id": PRICING_SNAPSHOT,
                "trend_comparability_key": (
                    "v1|pack:coding-fast-v-test|"
                    "grader:scoring-mode:semantic_q1_q5_equal_v2|"
                    "profile:full|questions:q1,q2"
                ),
            },
        )

        rows = {row["candidate_id"]: row for row in summary["leaderboard"]}
        for candidate_id in ("local:model-a:high", "local:model-c:high"):
            self.assertEqual(rows[candidate_id]["canonical_rank"], 1)
            self.assertEqual(
                rows[candidate_id]["canonical_rank_label"],
                "并列第 1 名",
            )
            self.assertEqual(rows[candidate_id]["canonical_rank_status"], "tied")
            self.assertEqual(
                rows[candidate_id]["canonical_rank_semantics"],
                "competition",
            )
            self.assertEqual(
                rows[candidate_id]["canonical_rank_score_basis"],
                "overall_score",
            )
            self.assertTrue(rows[candidate_id]["is_canonical_rank_tied"])
            self.assertEqual(rows[candidate_id]["canonical_rank_tie_count"], 2)
            self.assertIn("并列第 1 名", rows[candidate_id]["canonical_labels"])

        self.assertEqual(rows["local:model-b:high"]["canonical_rank"], 3)
        self.assertEqual(
            rows["local:model-b:high"]["canonical_rank_label"],
            "第 3 名",
        )
        self.assertEqual(rows["local:model-b:high"]["canonical_rank_status"], "ranked")
        self.assertFalse(rows["local:model-b:high"]["is_canonical_rank_tied"])
        self.assertEqual(rows["local:model-b:high"]["canonical_rank_tie_count"], 1)

        comparisons = {
            item["pair_key"]: item
            for item in summary["pairwise_comparisons"]
        }
        expected_pair_keys = {
            f"{baseline['candidate_id']}__to__{candidate['candidate_id']}"
            for baseline in summary["leaderboard"]
            for candidate in summary["leaderboard"]
            if baseline["candidate_id"] != candidate["candidate_id"]
        }
        self.assertEqual(set(comparisons), expected_pair_keys)
        self.assertEqual(
            len(comparisons),
            len(summary["leaderboard"]) * (len(summary["leaderboard"]) - 1),
        )

        model_b = comparisons[
            "local:model-a:high__to__local:model-b:high"
        ]
        self.assertEqual(
            model_b["pair_key"],
            "local:model-a:high__to__local:model-b:high",
        )
        self.assertEqual(model_b["schema_version"], 1)
        self.assertEqual(model_b["baseline_candidate_id"], "local:model-a:high")
        self.assertEqual(model_b["baseline_label"], "model-a / high")
        self.assertEqual(model_b["candidate_label"], "model-b / high")
        self.assertEqual(model_b["comparison_status"], "comparable")
        self.assertTrue(model_b["is_comparable"])
        self.assertEqual(model_b["baseline_quality_score"], 90)
        self.assertEqual(model_b["candidate_quality_score"], 85)
        self.assertEqual(model_b["quality_delta_points"], -5)
        self.assertEqual(model_b["baseline_elapsed_seconds"], 20)
        self.assertEqual(model_b["candidate_elapsed_seconds"], 15)
        self.assertEqual(model_b["time_delta_percent"], 25)
        self.assertEqual(model_b["baseline_cost_usd"], 0.04)
        self.assertEqual(model_b["candidate_cost_usd"], 0.02)
        self.assertEqual(model_b["cost_delta_percent"], 50)
        self.assertEqual(model_b["baseline_cost_coverage"], "complete")
        self.assertEqual(model_b["candidate_cost_coverage"], "complete")
        self.assertEqual(
            model_b["baseline_token_totals"],
            {
                "input_tokens": 200,
                "cached_input_tokens": 20,
                "cache_write_input_tokens": 10,
                "output_tokens": 40,
                "reasoning_tokens": 60,
            },
        )
        self.assertEqual(
            model_b["candidate_token_totals"],
            {
                "input_tokens": 160,
                "cached_input_tokens": 16,
                "cache_write_input_tokens": 8,
                "output_tokens": 32,
                "reasoning_tokens": 48,
            },
        )
        self.assertEqual(model_b["warning_question_ids"], ["q2"])

        reverse_pair = comparisons[
            "local:model-b:high__to__local:model-c:high"
        ]
        self.assertEqual(reverse_pair["baseline_candidate_id"], "local:model-b:high")
        self.assertEqual(reverse_pair["candidate_id"], "local:model-c:high")
        self.assertEqual(reverse_pair["comparison_status"], "comparable")
        self.assertEqual(reverse_pair["quality_delta_points"], 5)

    def test_pairwise_projection_is_available_without_an_authoritative_baseline(self) -> None:
        summary = _summary(current_default_candidate_id=None)

        comparisons = summary["pairwise_comparisons"]
        self.assertEqual(len(comparisons), 6)
        self.assertEqual(
            {
                (item["baseline_candidate_id"], item["candidate_id"])
                for item in comparisons
            },
            {
                (baseline["candidate_id"], candidate["candidate_id"])
                for baseline in summary["leaderboard"]
                for candidate in summary["leaderboard"]
                if baseline["candidate_id"] != candidate["candidate_id"]
            },
        )
        self.assertEqual(summary["comparison_contract"]["schema_version"], 1)

    def test_swift_decodes_comparison_projection_and_legacy_rank_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "comparison-projection-decoding-tests"
            compile_result = subprocess.run(
                [
                    "swiftc",
                    "-module-cache-path",
                    str(Path(temp_dir) / "module-cache"),
                    "Sources/Model/LocalEncryptedSecretStore.swift",
                    "Sources/Model/SelectionModels.swift",
                    "Sources/Model/ComparisonSelectionPresenter.swift",
                    "tests/swift/ComparisonProjectionDecodingTests.swift",
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
                "Comparison projection decoding tests passed",
                run_result.stdout,
            )


def _summary(*, current_default_candidate_id: str | None) -> dict[str, object]:
    run_id = "run-comparison"
    candidate_specs = (
        ("local:model-a:high", "model-a", ((18, 20), (18, 20)), 10.0, 0.02, 100),
        ("local:model-b:high", "model-b", ((20, 20), (14, 20)), 7.5, 0.01, 80),
        ("local:model-c:high", "model-c", ((18, 20), (18, 20)), 9.0, 0.015, 90),
    )
    targets = [
        ResolvedScanTarget(
            candidate_id=candidate_id,
            source_id="codex_local",
            connection_id="local",
            model_id=model,
            scan_profile="high",
            display_name=f"{model} High",
        )
        for candidate_id, model, *_ in candidate_specs
    ]
    history: list[ScanResult] = []
    for candidate_id, model, scores, latency, cost, input_tokens in candidate_specs:
        for index, (score, total) in enumerate(scores, start=1):
            history.append(
                ScanResult(
                    run_id=run_id,
                    candidate_id=candidate_id,
                    model=model,
                    effort="high",
                    phase="scan",
                    question_id=f"q{index}",
                    question_title=f"Question {index}",
                    attempt_index=index,
                    started_at=f"2026-07-29T10:00:0{index}+08:00",
                    elapsed_seconds=latency,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=input_tokens,
                    cached_input_tokens=input_tokens // 10,
                    cache_write_input_tokens=input_tokens // 20,
                    output_tokens=input_tokens // 5,
                    reasoning_tokens=input_tokens * 3 // 10,
                    reference_cost_usd=cost,
                    cost_status="estimated",
                    scorer_diagnostics={
                        "semantic_passed": score,
                        "semantic_total": total,
                    },
                    final_status="pass",
                )
            )
    metadata = {
        "run_id": run_id,
        "question_pack_id": "coding-fast",
        "question_pack_version": "coding-fast-v-test",
        "started_at": "2026-07-29T10:00:00+08:00",
        "completed_at": "2026-07-29T10:01:00+08:00",
        "candidate_count": 3,
        "question_count": 2,
        "question_ids": ["q1", "q2"],
        "status": "completed",
        "selection_mode": "regular",
        "requested_candidate_ids": [item[0] for item in candidate_specs],
        "regular_candidate_ids": [item[0] for item in candidate_specs],
        "is_complete_regular_round": True,
        "scoring_mode": "semantic_q1_q5_equal_v2",
        "evaluation_profile_id": "full",
        "evaluation_profile_label": "完整评测",
        "evaluation_result_level": "full",
        "evaluation_score_max": 40,
    }
    return build_dashboard_summary(
        history,
        targets,
        current_run_id=run_id,
        run_metadata=metadata,
        run_metadata_by_id={run_id: metadata},
        current_default_candidate_id=current_default_candidate_id,
        current_question_pack_id="coding-fast",
        current_question_pack_version="coding-fast-v-test",
    )


if __name__ == "__main__":
    unittest.main()
