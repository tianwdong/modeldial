from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from scanner.analytics import (
    DashboardModelView,
    _assign_leaderboard_decision_tags,
    _effort_label,
    _profile_compatible_run_ids,
    _route_identity_status,
    _semantic_item_score,
    build_dashboard_summary,
)
from scanner.costing import estimate_reference_cost
from scanner.models import (
    AppConfig,
    ConnectionConfig,
    ModelCandidateConfig,
    ResolvedScanTarget,
    ScanBudgetConfig,
    ScanResult,
    TargetConfig,
)
from scanner.route_identity import build_route_fingerprint
from tests.question_pack_fixtures import DEFAULT_QUESTION_PACK_VERSION


def _scan_result(
    *,
    run_id: str,
    model: str,
    effort: str,
    question_id: str,
    answer_ok: bool,
    phase: str = "scan",
    candidate_id: str | None = None,
    reasoning_tokens: int | None = 430,
    elapsed_seconds: float = 1.0,
    final_status: str = "pass",
    flags: list[str] | None = None,
    error_message: str | None = None,
    capability_label: str | None = None,
    scorer_reason: str | None = None,
    expected_summary: str | None = None,
    actual_summary: str | None = None,
    grader_kind: str = "regex",
    scorer_diagnostics: dict[str, object] | None = None,
    reference_cost_usd: float | None = None,
    cost_status: str = "unavailable",
    execution_trace: dict[str, object] | None = None,
) -> ScanResult:
    return ScanResult(
        run_id=run_id,
        candidate_id=candidate_id,
        model=model,
        effort=effort,
        phase=phase,
        question_id=question_id,
        question_title=question_id,
        capability_label=capability_label,
        grader_kind=grader_kind,
        attempt_index=_attempt_index(question_id),
        started_at="2026-07-06T10:00:00+08:00",
        elapsed_seconds=elapsed_seconds,
        source_mode="live",
        answer_ok=answer_ok,
        answer_preview="21" if answer_ok else "20",
        error_message=error_message,
        scorer_reason=scorer_reason,
        expected_summary=expected_summary,
        actual_summary=actual_summary,
        input_tokens=100,
        output_tokens=20,
        reasoning_tokens=reasoning_tokens,
        reference_cost_usd=reference_cost_usd,
        cost_status=cost_status,
        flags=list(flags or []),
        final_status=final_status,
        scorer_diagnostics=scorer_diagnostics or {},
        execution_trace=execution_trace or {},
    )


def _attempt_index(question_id: str) -> int:
    if question_id.startswith("q"):
        return int(question_id.removeprefix("q") or 1)
    prefix = question_id.split("_", 1)[0]
    return int(prefix) if prefix.isdigit() else 1


def _run_metadata(run_id: str, question_pack_version: str) -> dict[str, object]:
    return {
        "run_id": run_id,
        "question_pack_id": "coding-fast",
        "question_pack_version": question_pack_version,
        "started_at": "2026-07-06T10:00:00+08:00",
        "completed_at": "2026-07-06T10:02:00+08:00",
        "candidate_count": 6,
        "question_count": 4,
        "status": "completed",
    }


class AnalyticsTest(unittest.TestCase):
    def test_leaderboard_rank_uses_score_before_recommendation_eligibility(self) -> None:
        endpoint_id = "endpoint-1:grok-4.5:high"
        local_id = "codex-local-default:gpt-5.6-luna:high"
        targets = [
            ResolvedScanTarget(
                candidate_id=endpoint_id,
                source_id="custom_endpoint",
                connection_id="endpoint-1",
                model_id="grok-4.5",
                scan_profile="high",
                display_name="Grok 4.5 High",
                connection_mode="api",
                api_format="openai_chat_completions",
                base_url="https://example.com/v1",
            ),
            ResolvedScanTarget(
                candidate_id=local_id,
                source_id="codex_local",
                connection_id="codex-local-default",
                model_id="gpt-5.6-luna",
                scan_profile="high",
                display_name="GPT-5.6 Luna High",
            ),
        ]
        run_id = "rank-by-score"
        history: list[ScanResult] = []
        for candidate_id, model, effort, score in (
            (endpoint_id, "grok-4.5", "high", 16),
            (local_id, "gpt-5.6-luna", "high", 12),
        ):
            history.extend(
                _scan_result(
                    run_id=run_id,
                    candidate_id=candidate_id,
                    model=model,
                    effort=effort,
                    question_id=f"q{index}",
                    answer_ok=score == 20,
                    scorer_diagnostics={
                        "semantic_passed": score,
                        "semantic_total": 20,
                    },
                )
                for index in range(1, 6)
            )
        metadata = {
            **_run_metadata(run_id, DEFAULT_QUESTION_PACK_VERSION),
            "question_count": 5,
            "question_ids": [f"q{index}" for index in range(1, 6)],
            "selection_mode": "regular",
            "requested_candidate_ids": [endpoint_id, local_id],
            "regular_candidate_ids": [endpoint_id, local_id],
            "is_complete_regular_round": True,
            "scoring_mode": "semantic_q1_q5_equal_v2",
        }

        summary = build_dashboard_summary(
            history,
            targets,
            current_run_id=run_id,
            run_metadata=metadata,
            run_metadata_by_id={run_id: metadata},
            current_question_pack_id="coding-fast",
            current_question_pack_version=DEFAULT_QUESTION_PACK_VERSION,
        )

        self.assertFalse(summary["leaderboard"][0]["is_current_run_eligible"])
        self.assertEqual(summary["leaderboard"][0]["candidate_id"], endpoint_id)
        self.assertEqual(summary["leaderboard"][0]["overall_score"], 80)
        self.assertEqual(summary["leaderboard"][1]["overall_score"], 60)

    def test_endpoint_route_change_invalidates_previous_full_evaluation(self) -> None:
        config = AppConfig.default()
        candidate_id = "endpoint-1:gpt-5.6-terra:high"
        connection = ConnectionConfig(
            id="endpoint-1",
            source_id="custom_endpoint",
            name="Endpoint 1",
            enabled=True,
            api_format="openai_chat_completions",
            provider_preset="generic",
            base_url="https://example.com/v1",
            model_candidates=[
                ModelCandidateConfig(
                    id=candidate_id,
                    connection_id="endpoint-1",
                    model_id="gpt-5.6-terra",
                    display_name="GPT-5.6 Terra High",
                    enabled=True,
                    scan_profile="high",
                )
            ],
        )
        config.model_ingress.connections.append(connection)
        route_fingerprint = build_route_fingerprint(
            source_id="custom_endpoint",
            connection_id="endpoint-1",
            connection_mode="api",
            api_format="openai_chat_completions",
            provider_preset="generic",
            base_url="https://example.com/v1",
            model_id="gpt-5.6-terra",
            scan_profile="high",
        )
        run_id = "run-endpoint-route"
        history = [
            _scan_result(
                run_id=run_id,
                candidate_id=candidate_id,
                model="gpt-5.6-terra",
                effort="high",
                question_id=f"q{index}",
                answer_ok=True,
                scorer_diagnostics={
                    "semantic_passed": 20,
                    "semantic_total": 20,
                },
                execution_trace={"route_fingerprint": route_fingerprint},
            )
            for index in range(1, 6)
        ]
        metadata = {
            **_run_metadata(run_id, DEFAULT_QUESTION_PACK_VERSION),
            "question_count": 5,
            "question_ids": [f"q{index}" for index in range(1, 6)],
            "selection_mode": "regular",
            "requested_candidate_ids": [candidate_id],
            "regular_candidate_ids": [candidate_id],
            "is_complete_regular_round": True,
            "scoring_mode": "semantic_q1_q5_equal_v2",
        }

        missing = build_dashboard_summary(
            [
                _scan_result(
                    run_id=item.run_id,
                    candidate_id=item.candidate_id,
                    model=item.model,
                    effort=item.effort,
                    question_id=item.question_id,
                    answer_ok=item.answer_ok,
                    scorer_diagnostics=item.scorer_diagnostics,
                )
                for item in history
            ],
            config.model_ingress,
            current_run_id=run_id,
            run_metadata=metadata,
            run_metadata_by_id={run_id: metadata},
            current_question_pack_id="coding-fast",
            current_question_pack_version=DEFAULT_QUESTION_PACK_VERSION,
        )
        missing_row = next(
            row
            for row in missing["leaderboard"]
            if row["candidate_id"] == candidate_id
        )
        self.assertEqual(missing_row["route_identity_status"], "missing")
        self.assertFalse(missing_row["is_current_pack_comparable"])

        matched = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id=run_id,
            run_metadata=metadata,
            run_metadata_by_id={run_id: metadata},
            current_question_pack_id="coding-fast",
            current_question_pack_version=DEFAULT_QUESTION_PACK_VERSION,
        )
        matched_row = next(
            row
            for row in matched["leaderboard"]
            if row["candidate_id"] == candidate_id
        )
        self.assertEqual(matched_row["route_identity_status"], "matched")
        self.assertTrue(matched_row["is_current_pack_comparable"])

        connection.base_url = "https://gateway.example.com/v1"
        changed = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id=run_id,
            run_metadata=metadata,
            run_metadata_by_id={run_id: metadata},
            current_question_pack_id="coding-fast",
            current_question_pack_version=DEFAULT_QUESTION_PACK_VERSION,
        )
        changed_row = next(
            row
            for row in changed["leaderboard"]
            if row["candidate_id"] == candidate_id
        )
        self.assertEqual(changed_row["route_identity_status"], "changed")
        self.assertFalse(changed_row["is_current_pack_comparable"])
        self.assertFalse(changed_row["is_current_run_eligible"])

    def test_endpoint_mixed_route_evidence_is_not_comparable(self) -> None:
        model_view = DashboardModelView(
            id="endpoint-1:gpt-5.6-terra:high",
            label="GPT-5.6 Terra High",
            model="gpt-5.6-terra",
            model_id="gpt-5.6-terra",
            effort="high",
            display_name="GPT-5.6 Terra High",
            source_mode="api",
            route_fingerprint="route-v1:sha256:expected",
        )
        results = [
            _scan_result(
                run_id="run-mixed",
                model="gpt-5.6-terra",
                effort="high",
                question_id=f"q{index}",
                answer_ok=True,
                execution_trace={"route_fingerprint": fingerprint},
            )
            for index, fingerprint in enumerate(
                ["route-v1:sha256:first", "route-v1:sha256:second"],
                start=1,
            )
        ]

        self.assertEqual(_route_identity_status(model_view, results), "mixed")

    def test_history_isolated_by_pack_version_and_evaluation_profile(self) -> None:
        metadata_by_id = {
            "quick-current": {
                "question_pack_version": DEFAULT_QUESTION_PACK_VERSION,
                "evaluation_profile_id": "quick",
            },
            "full-same-pack": {
                "question_pack_version": DEFAULT_QUESTION_PACK_VERSION,
                "evaluation_profile_id": "full",
            },
            "quick-old-pack": {
                "question_pack_version": "coding-fast-v4.6",
                "evaluation_profile_id": "quick",
            },
        }

        self.assertEqual(
            _profile_compatible_run_ids(
                list(metadata_by_id),
                current_metadata=metadata_by_id["quick-current"],
                run_metadata_by_id=metadata_by_id,
            ),
            ["quick-current"],
        )

    def test_history_compatibility_ignores_retry_and_resume_metadata(self) -> None:
        metadata_by_id = {
            "one-shot": {
                "question_pack_version": DEFAULT_QUESTION_PACK_VERSION,
                "evaluation_profile_id": "full",
                "retry_policy": {"timeout_retry_count": 0},
            },
            "resumed": {
                "question_pack_version": DEFAULT_QUESTION_PACK_VERSION,
                "evaluation_profile_id": "full",
                "retry_policy": {"timeout_retry_count": 2},
                "execution_cycle_count": 3,
                "migration": {"source_run_id": "one-shot"},
            },
        }

        self.assertEqual(
            _profile_compatible_run_ids(
                list(metadata_by_id),
                current_metadata=metadata_by_id["resumed"],
                run_metadata_by_id=metadata_by_id,
            ),
            ["one-shot", "resumed"],
        )

    def test_provisional_profile_exposes_raw_mode_score_without_formal_recommendation(self) -> None:
        config = AppConfig.default()
        candidate_ids = [
            "codex-local-default:gpt-5.4:medium",
            "codex-local-default:gpt-5.4:high",
        ]
        selected = set(candidate_ids)
        for connection in config.model_ingress.connections:
            for candidate in connection.model_candidates:
                candidate.enabled = candidate.id in selected
        run_id = "run-quick"
        history = [
            _scan_result(
                run_id=run_id,
                candidate_id=candidate_id,
                model="gpt-5.4",
                effort=effort,
                question_id="05_cache_regression_test_design",
                answer_ok=False,
                grader_kind="mutation_test_design",
                scorer_diagnostics={
                    "semantic_passed": score,
                    "semantic_total": 20,
                },
            )
            for candidate_id, effort, score in (
                (candidate_ids[0], "medium", 12),
                (candidate_ids[1], "high", 16),
            )
        ]
        metadata = {
            "run_id": run_id,
            "question_pack_id": "coding-fast",
            "question_pack_version": DEFAULT_QUESTION_PACK_VERSION,
            "started_at": "2026-07-23T10:00:00+08:00",
            "completed_at": "2026-07-23T10:02:00+08:00",
            "candidate_count": 2,
            "question_count": 1,
            "status": "completed",
            "selection_mode": "regular",
            "requested_candidate_ids": candidate_ids,
            "regular_candidate_ids": candidate_ids,
            "comparison_group_id": run_id,
            "comparison_group_mode": "regular",
            "evaluation_profile_id": "quick",
            "evaluation_profile_label": "极速筛选",
            "evaluation_result_level": "provisional",
            "upgrade_target_profile_id": "full",
            "is_complete_regular_round": False,
            "scoring_mode": "semantic_q1_q5_equal_v2",
            "question_ids": ["05_cache_regression_test_design"],
        }

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id=run_id,
            run_metadata=metadata,
            run_metadata_by_id={run_id: metadata},
            current_question_pack_id="coding-fast",
            current_question_pack_version=DEFAULT_QUESTION_PACK_VERSION,
        )

        leaderboard = summary["leaderboard"]
        self.assertEqual(leaderboard[0]["candidate_id"], candidate_ids[1])
        self.assertEqual(leaderboard[0]["mode_score"], 16)
        self.assertEqual(leaderboard[0]["mode_score_max"], 20)
        self.assertEqual(leaderboard[0]["mode_score_text"], "16/20")
        self.assertIsNone(leaderboard[0]["overall_score"])
        self.assertEqual(leaderboard[0]["decision_tags"], [])
        self.assertIsNone(summary["best_combination"])
        self.assertEqual(summary["provisional_leader"]["candidate_id"], candidate_ids[1])
        self.assertEqual(summary["provisional_leader"]["status"], "leading")
        self.assertEqual(summary["provisional_leader"]["label"], "极速领先")
        self.assertEqual(
            summary["provisional_leader"]["status_label"],
            summary["provisional_leader"]["label"],
        )
        self.assertEqual(summary["provisional_leader"]["confidence_label"], "中")
        self.assertEqual(
            summary["provisional_leader"]["confidence_reason"],
            summary["provisional_leader"]["reason"],
        )
        self.assertEqual(summary["statistics"], {"trend_series": []})

    def test_idle_quick_round_projects_current_selection_without_discarding_existing_scores(self) -> None:
        config = AppConfig.default()
        candidates = config.model_ingress.connections[0].model_candidates[:2]
        original_candidate = candidates[0]
        added_candidate = candidates[1]
        for connection in config.model_ingress.connections:
            for candidate in connection.model_candidates:
                candidate.enabled = candidate.id in {
                    original_candidate.id,
                    added_candidate.id,
                }
        run_id = "run-quick-original-selection"
        history = [
            _scan_result(
                run_id=run_id,
                candidate_id=original_candidate.id,
                model=original_candidate.model_id,
                effort=original_candidate.scan_profile,
                question_id="05_cache_regression_test_design",
                answer_ok=False,
                grader_kind="mutation_test_design",
                scorer_diagnostics={
                    "semantic_passed": 16,
                    "semantic_total": 20,
                },
            )
        ]
        metadata = {
            "run_id": run_id,
            "question_pack_id": "coding-fast",
            "question_pack_version": DEFAULT_QUESTION_PACK_VERSION,
            "started_at": "2026-07-23T10:00:00+08:00",
            "completed_at": "2026-07-23T10:01:00+08:00",
            "candidate_count": 1,
            "question_count": 1,
            "status": "completed",
            "selection_mode": "regular",
            "requested_candidate_ids": [original_candidate.id],
            "regular_candidate_ids": [original_candidate.id],
            "comparison_group_id": run_id,
            "comparison_group_mode": "regular",
            "evaluation_profile_id": "quick",
            "evaluation_profile_label": "极速筛选",
            "evaluation_result_level": "provisional",
            "evaluation_score_max": 20,
            "upgrade_target_profile_id": "full",
            "is_complete_regular_round": False,
            "scoring_mode": "semantic_q1_q5_equal_v2",
            "question_ids": ["05_cache_regression_test_design"],
        }

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id=run_id,
            run_metadata=metadata,
            run_metadata_by_id={run_id: metadata},
            current_question_pack_id="coding-fast",
            current_question_pack_version=DEFAULT_QUESTION_PACK_VERSION,
        )

        entries = {
            entry["candidate_id"]: entry for entry in summary["leaderboard"]
        }
        self.assertEqual(set(entries), {original_candidate.id, added_candidate.id})
        self.assertEqual(entries[original_candidate.id]["mode_score_text"], "16/20")
        self.assertTrue(entries[original_candidate.id]["is_current_run_eligible"])
        self.assertEqual(entries[added_candidate.id]["question_completed"], 0)
        self.assertFalse(entries[added_candidate.id]["is_current_run_eligible"])
        self.assertEqual(summary["provisional_leader"]["status"], "insufficient")
        self.assertIsNone(summary["provisional_leader"]["candidate_id"])

    def test_efficiency_tags_use_ten_point_window_and_require_material_gain(self) -> None:
        entries = [
            {
                "candidate_id": "best",
                "overall_score": 88,
                "elapsed_seconds": 20.0,
                "estimated_cost_usd": 1.2,
                "cost_coverage": "complete",
                "is_current_run_eligible": True,
            },
            {
                "candidate_id": "value",
                "overall_score": 84,
                "elapsed_seconds": 30.0,
                "estimated_cost_usd": 0.4,
                "cost_coverage": "complete",
                "is_current_run_eligible": True,
            },
            {
                "candidate_id": "speed",
                "overall_score": 80,
                "elapsed_seconds": 10.0,
                "estimated_cost_usd": 1.1,
                "cost_coverage": "complete",
                "is_current_run_eligible": True,
            },
            {
                "candidate_id": "outside",
                "overall_score": 77,
                "elapsed_seconds": 5.0,
                "estimated_cost_usd": 0.1,
                "cost_coverage": "complete",
                "is_current_run_eligible": True,
            },
        ]

        _assign_leaderboard_decision_tags(entries, "best")

        self.assertEqual(
            [tag["kind"] for tag in entries[0]["decision_tags"]],
            ["recommended"],
        )
        self.assertEqual(
            [tag["kind"] for tag in entries[1]["decision_tags"]],
            ["value"],
        )
        self.assertEqual(
            [tag["kind"] for tag in entries[2]["decision_tags"]],
            ["speed"],
        )
        self.assertEqual(
            [tag["kind"] for tag in entries[3]["decision_tags"]],
            ["lightweight"],
        )
        self.assertIn("不超过 10 分", entries[1]["decision_tags"][0]["detail"])
        self.assertIn("费用降低 66.7%", entries[1]["decision_tags"][0]["detail"])
        self.assertIn("速度提升 50.0%", entries[2]["decision_tags"][0]["detail"])
        self.assertIn("10s", entries[2]["decision_tags"][0]["detail"])

    def test_efficiency_tags_ignore_small_cost_and_speed_improvements(self) -> None:
        entries = [
            {
                "candidate_id": "best",
                "overall_score": 90,
                "elapsed_seconds": 100.0,
                "estimated_cost_usd": 1.9,
                "cost_coverage": "complete",
                "is_current_run_eligible": True,
            },
            {
                "candidate_id": "marginal",
                "overall_score": 82,
                "elapsed_seconds": 80.0,
                "estimated_cost_usd": 1.8,
                "cost_coverage": "complete",
                "is_current_run_eligible": True,
            },
        ]

        _assign_leaderboard_decision_tags(entries, "best")

        self.assertEqual(
            [tag["kind"] for tag in entries[0]["decision_tags"]],
            ["recommended"],
        )
        self.assertEqual(entries[1]["decision_tags"], [])

    def test_value_tag_requires_complete_cost_coverage_for_the_whole_tier(self) -> None:
        entries = [
            {
                "candidate_id": "best",
                "overall_score": 90,
                "elapsed_seconds": 20.0,
                "estimated_cost_usd": 1.2,
                "cost_coverage": "complete",
                "is_current_run_eligible": True,
            },
            {
                "candidate_id": "unknown-cost",
                "overall_score": 87,
                "elapsed_seconds": 10.0,
                "estimated_cost_usd": None,
                "cost_coverage": "unknown",
                "is_current_run_eligible": True,
            },
        ]

        _assign_leaderboard_decision_tags(entries, "best")

        self.assertNotIn(
            "value",
            {tag["kind"] for entry in entries for tag in entry["decision_tags"]},
        )
        self.assertIn(
            "speed",
            {tag["kind"] for entry in entries for tag in entry["decision_tags"]},
        )

    def test_efficiency_tags_require_at_least_two_models_in_score_window(self) -> None:
        entries = [
            {
                "candidate_id": "best",
                "overall_score": 90,
                "elapsed_seconds": 20.0,
                "estimated_cost_usd": 1.2,
                "cost_coverage": "complete",
                "is_current_run_eligible": True,
            },
            {
                "candidate_id": "outside",
                "overall_score": 79,
                "elapsed_seconds": 10.0,
                "estimated_cost_usd": 0.2,
                "cost_coverage": "complete",
                "is_current_run_eligible": True,
            },
        ]

        _assign_leaderboard_decision_tags(entries, "best")

        self.assertEqual(
            [tag["kind"] for tag in entries[0]["decision_tags"]],
            ["recommended"],
        )

    def test_lightweight_tag_requires_large_cost_and_time_savings(self) -> None:
        entries = [
            {
                "candidate_id": "best",
                "overall_score": 88,
                "elapsed_seconds": 1123.0,
                "estimated_cost_usd": 1.98,
                "cost_coverage": "complete",
                "is_current_run_eligible": True,
            },
            {
                "candidate_id": "lightweight",
                "overall_score": 66,
                "elapsed_seconds": 514.0,
                "estimated_cost_usd": 0.618,
                "cost_coverage": "complete",
                "is_current_run_eligible": True,
            },
            {
                "candidate_id": "cheap-but-slow",
                "overall_score": 61,
                "elapsed_seconds": 876.0,
                "estimated_cost_usd": 0.344,
                "cost_coverage": "complete",
                "is_current_run_eligible": True,
            },
            {
                "candidate_id": "fast-but-not-cheap",
                "overall_score": 73,
                "elapsed_seconds": 464.0,
                "estimated_cost_usd": 1.052,
                "cost_coverage": "complete",
                "is_current_run_eligible": True,
            },
        ]

        _assign_leaderboard_decision_tags(entries, "best")

        self.assertEqual(
            [tag["kind"] for tag in entries[1]["decision_tags"]],
            ["lightweight"],
        )
        detail = entries[1]["decision_tags"][0]["detail"]
        self.assertIn("轻量优选", entries[1]["decision_tags"][0]["label"])
        self.assertIn("较榜首低 22 分", detail)
        self.assertIn("费用降低 68.8%", detail)
        self.assertIn("耗时降低 54.2%", detail)
        self.assertEqual(entries[2]["decision_tags"], [])
        self.assertEqual(entries[3]["decision_tags"], [])

    def test_lightweight_tag_enforces_each_quality_and_resource_gate(self) -> None:
        cases = [
            ("score-floor", 80, 59, 40.0, 0.5, "complete"),
            ("score-gap", 90, 64, 40.0, 0.5, "complete"),
            ("cost-ratio", 90, 65, 40.0, 0.68, "complete"),
            ("duration-ratio", 90, 65, 51.0, 0.5, "complete"),
            ("unknown-cost", 90, 65, 40.0, None, "unknown"),
        ]
        for name, best_score, candidate_score, elapsed, cost, coverage in cases:
            with self.subTest(name=name):
                entries = [
                    {
                        "candidate_id": "best",
                        "overall_score": best_score,
                        "elapsed_seconds": 100.0,
                        "estimated_cost_usd": 2.0,
                        "cost_coverage": "complete",
                        "is_current_run_eligible": True,
                    },
                    {
                        "candidate_id": "candidate",
                        "overall_score": candidate_score,
                        "elapsed_seconds": elapsed,
                        "estimated_cost_usd": cost,
                        "cost_coverage": coverage,
                        "is_current_run_eligible": True,
                    },
                ]

                _assign_leaderboard_decision_tags(entries, "best")

                self.assertNotIn(
                    "lightweight",
                    {tag["kind"] for tag in entries[1]["decision_tags"]},
                )

    def test_lightweight_tag_prefers_the_highest_scoring_qualified_candidate(self) -> None:
        entries = [
            {
                "candidate_id": "best",
                "overall_score": 90,
                "elapsed_seconds": 100.0,
                "estimated_cost_usd": 2.0,
                "cost_coverage": "complete",
                "is_current_run_eligible": True,
            },
            {
                "candidate_id": "higher-score",
                "overall_score": 70,
                "elapsed_seconds": 45.0,
                "estimated_cost_usd": 0.6,
                "cost_coverage": "complete",
                "is_current_run_eligible": True,
            },
            {
                "candidate_id": "lower-resource",
                "overall_score": 65,
                "elapsed_seconds": 20.0,
                "estimated_cost_usd": 0.2,
                "cost_coverage": "complete",
                "is_current_run_eligible": True,
            },
        ]

        _assign_leaderboard_decision_tags(entries, "best")

        self.assertIn(
            "lightweight",
            [tag["kind"] for tag in entries[1]["decision_tags"]],
        )
        self.assertNotIn(
            "lightweight",
            [tag["kind"] for tag in entries[2]["decision_tags"]],
        )

    def test_grader_unavailable_does_not_contribute_zero_score(self) -> None:
        result = _scan_result(
            run_id="run-grader-unavailable",
            model="gpt-5.6-sol",
            effort="high",
            question_id="q1",
            answer_ok=False,
            error_message="grader_unavailable: sandbox_unavailable",
            scorer_diagnostics={
                "status": "grader_unavailable",
                "failure_summary": "sandbox_unavailable",
            },
        )

        self.assertEqual(_semantic_item_score(result), (0, 0))

    def test_effort_labels_preserve_canonical_profile_names(self) -> None:
        for effort in ("low", "medium", "high", "xhigh", "max", "ultra"):
            self.assertEqual(_effort_label(effort), effort)

    def test_scan_budget_summary_is_disabled_by_default(self) -> None:
        config = AppConfig.default()
        history = [
            _scan_result(
                run_id="budget-run",
                candidate_id="codex-local-default:gpt-5.4:high",
                model="gpt-5.4",
                effort="high",
                question_id="q1",
                answer_ok=True,
                elapsed_seconds=80,
                reference_cost_usd=0.2,
                cost_status="estimated",
            )
        ]

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id="budget-run",
            scan_budget=config.scan_budget,
        )

        self.assertEqual(summary["budget_summary"]["status"], "disabled")
        self.assertEqual(summary["budget_summary"]["elapsed_seconds"], 80)
        self.assertEqual(summary["budget_summary"]["reference_cost_usd"], 0.2)

    def test_scan_budget_summary_reports_exceeded_and_partial_cost(self) -> None:
        config = AppConfig.default()
        budget = ScanBudgetConfig(
            enabled=True,
            max_duration_seconds=30,
            max_reference_cost_usd=0.1,
        )
        history = [
            _scan_result(
                run_id="budget-run",
                candidate_id="codex-local-default:gpt-5.4:high",
                model="gpt-5.4",
                effort="high",
                question_id="q1",
                answer_ok=True,
                elapsed_seconds=40,
                reference_cost_usd=0.12,
                cost_status="estimated",
            ),
            _scan_result(
                run_id="budget-run",
                candidate_id="codex-local-default:gpt-5.5:high",
                model="unsupported-model",
                effort="high",
                question_id="q2",
                answer_ok=True,
                elapsed_seconds=30,
                cost_status="unpriced",
            ),
        ]

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id="budget-run",
            scan_budget=budget,
        )
        budget_summary = summary["budget_summary"]

        self.assertEqual(budget_summary["status"], "exceeded")
        self.assertTrue(budget_summary["duration_exceeded"])
        self.assertTrue(budget_summary["cost_exceeded"])
        self.assertEqual(budget_summary["cost_coverage"], "partial")
        self.assertIn("部分费用未知", budget_summary["detail_text"])

    def test_failed_attempt_without_usage_does_not_make_successful_cost_partial(self) -> None:
        config = AppConfig.default()
        candidate_id = "codex-local-default:gpt-5.4:high"
        successful = _scan_result(
            run_id="retry-cost-run",
            candidate_id=candidate_id,
            model="gpt-5.4",
            effort="high",
            question_id="q1",
            answer_ok=True,
            elapsed_seconds=12,
            reference_cost_usd=0.2,
            cost_status="estimated",
        )
        failed = _scan_result(
            run_id="retry-cost-run",
            candidate_id=candidate_id,
            model="gpt-5.4",
            effort="high",
            question_id="q2",
            answer_ok=False,
            elapsed_seconds=90,
            final_status="warn",
            flags=["missing_usage"],
            error_message="codex exec failed",
        )
        failed.input_tokens = None
        failed.cached_input_tokens = None
        failed.cache_write_input_tokens = None
        failed.output_tokens = None
        failed.reasoning_tokens = None

        summary = build_dashboard_summary(
            [successful, failed],
            config.model_ingress,
            current_run_id="retry-cost-run",
            scan_budget=config.scan_budget,
        )
        row = next(
            item for item in summary["leaderboard"]
            if item["candidate_id"] == candidate_id
        )

        self.assertEqual(row["estimated_cost_usd"], 0.2)
        self.assertEqual(row["cost_coverage"], "complete")
        self.assertEqual(row["elapsed_seconds"], 12)
        self.assertEqual(summary["budget_summary"]["cost_coverage"], "complete")

    def test_unpriced_history_is_reestimated_from_current_snapshot(self) -> None:
        config = AppConfig.default()
        grok_id = "codex-local-default:gpt-5.4:high"
        deepseek_id = "codex-local-default:gpt-5.5:high"
        history = [
            _scan_result(
                run_id="repriced-run",
                candidate_id=grok_id,
                model="grok-4.5",
                effort="high",
                question_id="q1",
                answer_ok=True,
                elapsed_seconds=12,
            ),
            _scan_result(
                run_id="repriced-run",
                candidate_id=grok_id,
                model="grok-4.5",
                effort="high",
                question_id="q2",
                answer_ok=False,
                elapsed_seconds=90,
                error_message="endpoint failed",
            ),
            _scan_result(
                run_id="repriced-run",
                candidate_id=deepseek_id,
                model="deepseek-v4-flash",
                effort="high",
                question_id="q1",
                answer_ok=True,
                elapsed_seconds=8,
            ),
        ]

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id="repriced-run",
            scan_budget=config.scan_budget,
        )
        rows = {item["candidate_id"]: item for item in summary["leaderboard"]}

        self.assertEqual(rows[grok_id]["estimated_cost_usd"], 0.00032)
        self.assertEqual(rows[grok_id]["cost_coverage"], "complete")
        self.assertEqual(rows[grok_id]["elapsed_seconds"], 12)
        current_deepseek_cost = estimate_reference_cost(
            "deepseek-v4-flash",
            input_tokens=100,
            cached_input_tokens=0,
            output_tokens=20,
        )
        self.assertEqual(
            rows[deepseek_id]["estimated_cost_usd"],
            round(current_deepseek_cost.usd or 0, 6),
        )
        self.assertEqual(rows[deepseek_id]["cost_coverage"], "complete")

    def test_scan_budget_uses_full_run_wall_clock_time_when_tasks_overlap(self) -> None:
        config = AppConfig.default()
        budget = ScanBudgetConfig(
            enabled=True,
            max_duration_seconds=60,
            max_reference_cost_usd=1.0,
        )
        history = [
            _scan_result(
                run_id="concurrent-budget-run",
                candidate_id="codex-local-default:gpt-5.4:high",
                model="gpt-5.4",
                effort="high",
                question_id="q1",
                answer_ok=True,
                elapsed_seconds=40,
            ),
            _scan_result(
                run_id="concurrent-budget-run",
                candidate_id="codex-local-default:gpt-5.5:high",
                model="gpt-5.5",
                effort="high",
                question_id="q1",
                answer_ok=True,
                elapsed_seconds=30,
            ),
        ]

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id="concurrent-budget-run",
            run_metadata={
                "run_id": "concurrent-budget-run",
                "started_at": "2026-07-06T10:00:00+08:00",
                "completed_at": "2026-07-06T10:00:40+08:00",
                "status": "completed",
                "question_count": 1,
            },
            scan_budget=budget,
        )

        budget_summary = summary["budget_summary"]
        self.assertEqual(budget_summary["elapsed_seconds"], 40)
        self.assertFalse(budget_summary["duration_exceeded"])
        self.assertIn("耗时 40s / 1m", budget_summary["detail_text"])

    def test_scan_budget_summary_reports_partial_without_false_zero(self) -> None:
        config = AppConfig.default()
        budget = ScanBudgetConfig(enabled=True)
        history = [
            _scan_result(
                run_id="budget-run",
                candidate_id="codex-local-default:gpt-5.4:high",
                model="unsupported-model",
                effort="high",
                question_id="q1",
                answer_ok=True,
                elapsed_seconds=10,
                cost_status="unavailable",
            )
        ]

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id="budget-run",
            scan_budget=budget,
        )

        self.assertEqual(summary["budget_summary"]["status"], "partial")
        self.assertEqual(summary["budget_summary"]["cost_coverage"], "unknown")
        self.assertIsNone(summary["budget_summary"]["reference_cost_usd"])

    def test_versioned_api_model_uses_family_and_variant_in_display_label(self) -> None:
        config = AppConfig.default()
        candidate_id = "api-deepseek:deepseek-v4-flash:default"
        config.model_ingress.connections.append(
            ConnectionConfig(
                id="api-deepseek",
                source_id="custom_endpoint",
                name="DeepSeek",
                enabled=True,
                api_format="openai_chat_completions",
                base_url="https://example.com",
                model_candidates=[
                    ModelCandidateConfig(
                        id=candidate_id,
                        connection_id="api-deepseek",
                        model_id="deepseek-v4-flash",
                        family_id="deepseek-v4",
                        variant_id="flash",
                        display_name="DeepSeek V4 Flash",
                        enabled=True,
                        scan_profile="default",
                    )
                ],
            )
        )
        history = [
            _scan_result(
                run_id="run-deepseek",
                candidate_id=candidate_id,
                model="deepseek-v4-flash",
                effort="default",
                question_id=f"q{index}",
                answer_ok=True,
            )
            for index in range(1, 5)
        ]
        metadata = {
            **_run_metadata("run-deepseek", DEFAULT_QUESTION_PACK_VERSION),
            "selection_mode": "single",
            "requested_candidate_ids": [candidate_id],
            "is_complete_regular_round": False,
        }

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id="run-deepseek",
            run_metadata=metadata,
            run_metadata_by_id={"run-deepseek": metadata},
        )

        entry = next(
            item for item in summary["leaderboard"]
            if item["candidate_id"] == candidate_id
        )
        self.assertEqual(entry["label"], "deepseek-v4 / flash")
        self.assertNotIn("default", entry["label"])
        self.assertTrue(
            {
                "source_id", "connection_id", "family_id", "variant_id",
                "model_id", "effort", "candidate_id", "question_results",
                "latest_attempt_status", "latest_attempt_at",
                "latest_attempt_error_category", "latest_attempt_error_summary",
                "valid_run_id", "valid_completed_at", "question_pack_version",
            }.issubset(entry)
        )
        self.assertEqual(entry["family_id"], "deepseek-v4")
        self.assertEqual(entry["variant_id"], "flash")
        serialized = str(entry).lower()
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("authorization", serialized)

    def test_api_reasoning_suffix_aliases_use_shared_model_and_effort_identity(self) -> None:
        config = AppConfig.default()
        connection_id = "api-gemini"
        high_candidate_id = f"{connection_id}:gemini-3.6-flash-high:default"
        config.model_ingress.connections.append(
            ConnectionConfig(
                id=connection_id,
                source_id="custom_endpoint",
                name="Gemini",
                enabled=True,
                api_format="openai_chat_completions",
                base_url="https://example.com",
                model_candidates=[
                    ModelCandidateConfig(
                        id=f"{connection_id}:gemini-3.6-flash-{suffix}:default",
                        connection_id=connection_id,
                        model_id=f"gemini-3.6-flash-{suffix}",
                        display_name=f"gemini-3.6-flash-{suffix}",
                        enabled=suffix == "high",
                        scan_profile="default",
                    )
                    for suffix in ("low", "medium", "high", "tiered")
                ],
            )
        )
        history = [
            _scan_result(
                run_id="run-gemini",
                candidate_id=high_candidate_id,
                model="gemini-3.6-flash-high",
                effort="default",
                question_id=f"q{index}",
                answer_ok=True,
            )
            for index in range(1, 5)
        ]
        metadata = {
            **_run_metadata("run-gemini", DEFAULT_QUESTION_PACK_VERSION),
            "requested_candidate_ids": [high_candidate_id],
        }

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id="run-gemini",
            run_metadata=metadata,
            run_metadata_by_id={"run-gemini": metadata},
        )

        entry = next(
            item for item in summary["leaderboard"]
            if item["candidate_id"] == high_candidate_id
        )
        self.assertEqual(entry["model"], "gemini-3.6-flash")
        self.assertEqual(entry["model_id"], "gemini-3.6-flash-high")
        self.assertEqual(entry["effort"], "high")
        self.assertEqual(entry["label"], "gemini-3.6-flash / high")
        self.assertEqual(entry["family_id"], "gemini-3.6-flash")
        self.assertEqual(entry["variant_id"], "high")
        self.assertNotIn("tiered", entry["label"])

    def test_explicit_k3_scan_profile_keeps_model_id_and_uses_effort_identity(self) -> None:
        config = AppConfig.default()
        connection_id = "api-kimi"
        candidate_id = f"{connection_id}:k3:high"
        config.model_ingress.connections.append(
            ConnectionConfig(
                id=connection_id,
                source_id="custom_endpoint",
                name="Moonshot",
                enabled=True,
                api_format="openai_chat_completions",
                base_url="https://example.com",
                model_candidates=[
                    ModelCandidateConfig(
                        id=candidate_id,
                        connection_id=connection_id,
                        model_id="k3",
                        family_id="k3",
                        variant_id="high",
                        display_name="k3",
                        enabled=True,
                        scan_profile="high",
                        capabilities=["reasoning"],
                    )
                ],
            )
        )
        history = [
            _scan_result(
                run_id="run-k3",
                candidate_id=candidate_id,
                model="k3",
                effort="high",
                question_id=f"q{index}",
                answer_ok=True,
            )
            for index in range(1, 5)
        ]

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id="run-k3",
        )
        entry = next(
            item for item in summary["leaderboard"]
            if item["candidate_id"] == candidate_id
        )

        self.assertEqual(entry["model"], "k3")
        self.assertEqual(entry["effort"], "high")
        self.assertEqual(entry["label"], "k3 / high")

    def test_completed_single_scan_adds_sparse_trend_point_without_counting_as_regular_round(self) -> None:
        config = AppConfig.default()
        regular_candidate = "codex-local-default:gpt-5.4:high"
        single_candidate = "api-deepseek:deepseek-v4-flash:default"
        config.model_ingress.connections.append(
            ConnectionConfig(
                id="api-deepseek",
                source_id="custom_endpoint",
                name="DeepSeek",
                enabled=True,
                api_format="openai_chat_completions",
                base_url="https://example.com",
                model_candidates=[
                    ModelCandidateConfig(
                        id=single_candidate,
                        connection_id="api-deepseek",
                        model_id="deepseek-v4-flash",
                        display_name="DeepSeek V4 Flash",
                        enabled=True,
                        scan_profile="default",
                    )
                ],
            )
        )
        history: list[ScanResult] = []
        metadata_by_run: dict[str, dict[str, object]] = {}
        for run_index in range(1, 3):
            run_id = f"run-regular-{run_index}"
            metadata_by_run[run_id] = {
                **_run_metadata(run_id, DEFAULT_QUESTION_PACK_VERSION),
                "selection_mode": "regular",
                "requested_candidate_ids": [regular_candidate],
                "regular_candidate_ids": [regular_candidate],
                "is_complete_regular_round": True,
            }
            history.extend(
                _scan_result(
                    run_id=run_id,
                    candidate_id=regular_candidate,
                    model="gpt-5.4",
                    effort="high",
                    question_id=f"q{question_index}",
                    answer_ok=True,
                )
                for question_index in range(1, 5)
            )
        single_metadata = {
            **_run_metadata("run-single", DEFAULT_QUESTION_PACK_VERSION),
            "selection_mode": "single",
            "requested_candidate_ids": [single_candidate],
            "regular_candidate_ids": [regular_candidate, single_candidate],
            "is_complete_regular_round": False,
        }
        metadata_by_run["run-single"] = single_metadata
        history.extend(
            _scan_result(
                run_id="run-single",
                candidate_id=single_candidate,
                model="deepseek-v4-flash",
                effort="default",
                question_id=f"q{question_index}",
                answer_ok=question_index < 4,
            )
            for question_index in range(1, 5)
        )

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id="run-single",
            run_metadata=single_metadata,
            run_metadata_by_id=metadata_by_run,
        )

        statistics = summary["statistics"]
        deepseek_series = next(
            series for series in statistics["trend_series"]
            if series["candidate_id"] == single_candidate
        )
        self.assertEqual(deepseek_series["overall_score_values"], [75])
        self.assertEqual(deepseek_series["overall_score_run_indices"], [2])

    def test_running_single_scan_shows_only_requested_candidate_before_first_result(self) -> None:
        config = AppConfig.default()
        baseline_candidate = "codex-local-default:gpt-5.4:high"
        requested_candidate = "codex-local-default:gpt-5.5:xhigh"
        baseline = [
            _scan_result(
                run_id="run-baseline",
                candidate_id=baseline_candidate,
                model="gpt-5.4",
                effort="high",
                question_id=f"q{index}",
                answer_ok=True,
            )
            for index in range(1, 5)
        ]
        baseline_metadata = {
            **_run_metadata("run-baseline", DEFAULT_QUESTION_PACK_VERSION),
            "selection_mode": "regular",
            "requested_candidate_ids": [baseline_candidate],
            "regular_candidate_ids": [baseline_candidate],
            "is_complete_regular_round": True,
        }
        single_metadata = {
            **_run_metadata("run-single", DEFAULT_QUESTION_PACK_VERSION),
            "status": "running",
            "completed_at": None,
            "selection_mode": "single",
            "requested_candidate_ids": [requested_candidate],
            "regular_candidate_ids": [baseline_candidate, requested_candidate],
            "is_complete_regular_round": False,
        }
        active_run = {
            "run_metadata": single_metadata,
            "planned_attempts_by_candidate": {requested_candidate: 4},
        }

        summary = build_dashboard_summary(
            baseline,
            config.model_ingress,
            current_run_id="run-single",
            active_run=active_run,
            run_metadata=single_metadata,
            run_metadata_by_id={
                "run-baseline": baseline_metadata,
                "run-single": single_metadata,
            },
        )

        self.assertIsNone(summary["best_combination"])
        self.assertEqual(
            [item["candidate_id"] for item in summary["leaderboard"]],
            [requested_candidate],
        )
        self.assertEqual(summary["leaderboard"][0]["score_text"], "0/0")

        paused_metadata = {**single_metadata, "status": "paused"}
        paused_summary = build_dashboard_summary(
            baseline,
            config.model_ingress,
            current_run_id="run-single",
            active_run={
                **active_run,
                "run_metadata": paused_metadata,
            },
            run_metadata=paused_metadata,
            run_metadata_by_id={
                "run-baseline": baseline_metadata,
                "run-single": paused_metadata,
            },
        )
        self.assertEqual(
            [item["candidate_id"] for item in paused_summary["leaderboard"]],
            [requested_candidate],
        )

    def test_paused_single_scan_keeps_explicit_disabled_requested_candidate_visible(self) -> None:
        config = AppConfig.default()
        requested_candidate = "api-deepseek:deepseek-v4-flash:default"
        custom_source = next(
            source for source in config.model_ingress.sources if source.id == "custom_endpoint"
        )
        custom_source.enabled = False
        deepseek_connection = ConnectionConfig(
            id="api-deepseek",
            source_id="custom_endpoint",
            name="DeepSeek",
            enabled=False,
            base_url="https://api.deepseek.com",
            model_candidates=[
                ModelCandidateConfig(
                    id=requested_candidate,
                    connection_id="api-deepseek",
                    model_id="deepseek-v4-flash",
                    family_id="deepseek-v4",
                    variant_id="flash",
                    display_name="DeepSeek V4 Flash",
                    scan_profile="default",
                    enabled=False,
                )
            ],
        )
        config.model_ingress.connections.append(deepseek_connection)

        paused_metadata = {
            **_run_metadata("run-single", DEFAULT_QUESTION_PACK_VERSION),
            "status": "paused",
            "selection_mode": "single",
            "requested_candidate_ids": [requested_candidate],
            "regular_candidate_ids": ["codex-local-default:gpt-5.4:high", requested_candidate],
            "is_complete_regular_round": False,
        }

        summary = build_dashboard_summary(
            [],
            config.model_ingress,
            current_run_id="run-single",
            active_run={
                "run_metadata": paused_metadata,
                "planned_attempts_by_candidate": {requested_candidate: 4},
            },
            run_metadata=paused_metadata,
            run_metadata_by_id={"run-single": paused_metadata},
        )

        self.assertEqual(
            [item["candidate_id"] for item in summary["leaderboard"]],
            [requested_candidate],
        )
        self.assertEqual(summary["leaderboard"][0]["score_text"], "0/0")
        self.assertEqual(summary["statistics"], {"trend_series": []})

    def test_paused_single_scan_preserves_existing_history_statistics(self) -> None:
        config = AppConfig.default()
        baseline_candidate = "codex-local-default:gpt-5.4:high"
        comparison_candidate = "codex-local-default:gpt-5.5:high"
        requested_candidate = "api-deepseek:deepseek-v4-flash:default"
        custom_source = next(
            source for source in config.model_ingress.sources if source.id == "custom_endpoint"
        )
        custom_source.enabled = False
        config.model_ingress.connections.append(
            ConnectionConfig(
                id="api-deepseek",
                source_id="custom_endpoint",
                name="DeepSeek",
                enabled=False,
                base_url="https://api.deepseek.com",
                model_candidates=[
                    ModelCandidateConfig(
                        id=requested_candidate,
                        connection_id="api-deepseek",
                        model_id="deepseek-v4-flash",
                        family_id="deepseek-v4",
                        variant_id="flash",
                        display_name="DeepSeek V4 Flash",
                        scan_profile="default",
                        enabled=False,
                    )
                ],
            )
        )

        history: list[ScanResult] = []
        metadata_by_run: dict[str, dict[str, object]] = {}
        for index in range(1, 9):
            run_id = f"run-{index:02d}"
            metadata_by_run[run_id] = {
                **_run_metadata(run_id, DEFAULT_QUESTION_PACK_VERSION),
                "selection_mode": "regular",
                "requested_candidate_ids": [baseline_candidate, comparison_candidate],
                "regular_candidate_ids": [baseline_candidate, comparison_candidate],
                "is_complete_regular_round": True,
            }
            for question_index in range(1, 5):
                history.append(
                    _scan_result(
                        run_id=run_id,
                        candidate_id=baseline_candidate,
                        model="gpt-5.4",
                        effort="high",
                        question_id=f"q{question_index}",
                        answer_ok=True,
                    )
                )
                history.append(
                    _scan_result(
                        run_id=run_id,
                        candidate_id=comparison_candidate,
                        model="gpt-5.5",
                        effort="high",
                        question_id=f"q{question_index}",
                        answer_ok=question_index < 4,
                    )
                )

        paused_metadata = {
            **_run_metadata("run-single", DEFAULT_QUESTION_PACK_VERSION),
            "status": "paused",
            "selection_mode": "single",
            "requested_candidate_ids": [requested_candidate],
            "regular_candidate_ids": [baseline_candidate, comparison_candidate, requested_candidate],
            "is_complete_regular_round": False,
        }
        metadata_by_run["run-single"] = paused_metadata

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id="run-single",
            active_run={
                "run_metadata": paused_metadata,
                "planned_attempts_by_candidate": {requested_candidate: 4},
            },
            run_metadata=paused_metadata,
            run_metadata_by_id=metadata_by_run,
        )

        statistics = summary["statistics"]
        self.assertTrue(statistics["trend_series"])
        candidate_ids = {item["candidate_id"] for item in statistics["trend_series"]}
        self.assertIn(baseline_candidate, candidate_ids)
        self.assertIn(comparison_candidate, candidate_ids)

    def test_running_regular_scan_clears_previous_scores_until_current_results_arrive(self) -> None:
        config = AppConfig.default()
        candidate_ids = [
            "codex-local-default:gpt-5.4:high",
            "codex-local-default:gpt-5.5:xhigh",
        ]
        baseline = [
            _scan_result(
                run_id="run-baseline",
                candidate_id=candidate_id,
                model="gpt-5.4" if "5.4" in candidate_id else "gpt-5.5",
                effort="high" if "5.4" in candidate_id else "xhigh",
                question_id=f"q{question_index}",
                answer_ok=True,
            )
            for candidate_id in candidate_ids
            for question_index in range(1, 5)
        ]
        baseline_metadata = {
            **_run_metadata("run-baseline", DEFAULT_QUESTION_PACK_VERSION),
            "selection_mode": "regular",
            "requested_candidate_ids": candidate_ids,
            "regular_candidate_ids": candidate_ids,
            "is_complete_regular_round": True,
        }
        running_metadata = {
            **_run_metadata("run-current", DEFAULT_QUESTION_PACK_VERSION),
            "status": "running",
            "completed_at": None,
            "selection_mode": "regular",
            "requested_candidate_ids": candidate_ids,
            "regular_candidate_ids": candidate_ids,
            "is_complete_regular_round": False,
        }

        summary = build_dashboard_summary(
            baseline,
            config.model_ingress,
            current_run_id="run-current",
            active_run={
                "run_metadata": running_metadata,
                "planned_attempts_by_candidate": {
                    candidate_id: 4 for candidate_id in candidate_ids
                },
            },
            run_metadata=running_metadata,
            run_metadata_by_id={
                "run-baseline": baseline_metadata,
                "run-current": running_metadata,
            },
        )

        self.assertIsNone(summary["best_combination"])
        self.assertEqual(
            {item["candidate_id"] for item in summary["leaderboard"]},
            set(candidate_ids),
        )
        self.assertTrue(
            all(item["score_text"] == "0/0" for item in summary["leaderboard"])
        )
        self.assertTrue(
            all(item["latest_valid_run_id"] is None for item in summary["leaderboard"])
        )
        self.assertTrue(
            all(not item["is_using_previous_valid_result"] for item in summary["leaderboard"])
        )

    def test_running_custom_scan_preserves_planned_order_for_zero_score_candidates(self) -> None:
        config = AppConfig.default()
        requested_candidate_ids = [
            "codex-local-default:gpt-5.4:medium",
            "codex-local-default:gpt-5.5:xhigh",
            "codex-local-default:gpt-5.4:high",
        ]
        running_metadata = {
            **_run_metadata("run-current", DEFAULT_QUESTION_PACK_VERSION),
            "status": "running",
            "completed_at": None,
            "selection_mode": "custom",
            "requested_candidate_ids": requested_candidate_ids,
            "regular_candidate_ids": requested_candidate_ids,
            "is_complete_regular_round": False,
        }

        summary = build_dashboard_summary(
            [],
            config.model_ingress,
            current_run_id="run-current",
            active_run={
                "run_metadata": running_metadata,
                "planned_attempts_by_candidate": {
                    candidate_id: 4 for candidate_id in requested_candidate_ids
                },
                "entries": [
                    {
                        "candidate_id": candidate_id,
                        "status": "pending",
                        "attempts_completed": 0,
                        "attempts_per_target": 4,
                        "phase": "scan",
                    }
                    for candidate_id in requested_candidate_ids
                ],
            },
            run_metadata=running_metadata,
            run_metadata_by_id={"run-current": running_metadata},
        )

        self.assertEqual(
            [item["candidate_id"] for item in summary["leaderboard"]],
            requested_candidate_ids,
        )
        self.assertTrue(
            all(item["score_text"] == "0/0" for item in summary["leaderboard"])
        )

    def test_running_scan_surfaces_partial_result_before_unstarted_candidates(self) -> None:
        config = AppConfig.default()
        requested_candidate_ids = [
            "codex-local-default:gpt-5.4:high",
            "codex-local-default:gpt-5.5:xhigh",
            "codex-local-default:gpt-5.4:medium",
        ]
        running_metadata = {
            **_run_metadata("run-current", DEFAULT_QUESTION_PACK_VERSION),
            "status": "running",
            "completed_at": None,
            "selection_mode": "custom",
            "requested_candidate_ids": requested_candidate_ids,
            "regular_candidate_ids": requested_candidate_ids,
            "is_complete_regular_round": False,
        }

        summary = build_dashboard_summary(
            [
                _scan_result(
                    run_id="run-current",
                    candidate_id=requested_candidate_ids[2],
                    model="gpt-5.4",
                    effort="medium",
                    question_id="q1",
                    answer_ok=True,
                )
            ],
            config.model_ingress,
            current_run_id="run-current",
            active_run={
                "run_metadata": running_metadata,
                "planned_attempts_by_candidate": {
                    candidate_id: 4 for candidate_id in requested_candidate_ids
                },
                "entries": [
                    {
                        "candidate_id": requested_candidate_ids[0],
                        "status": "pending",
                        "attempts_completed": 0,
                        "attempts_per_target": 4,
                        "phase": "scan",
                    },
                    {
                        "candidate_id": requested_candidate_ids[1],
                        "status": "running",
                        "attempts_completed": 0,
                        "attempts_per_target": 4,
                        "phase": "scan",
                    },
                    {
                        "candidate_id": requested_candidate_ids[2],
                        "status": "running",
                        "attempts_completed": 1,
                        "attempts_per_target": 4,
                        "phase": "scan",
                    },
                ],
            },
            run_metadata=running_metadata,
            run_metadata_by_id={"run-current": running_metadata},
        )

        self.assertEqual(
            [item["candidate_id"] for item in summary["leaderboard"]],
            [
                requested_candidate_ids[2],
                requested_candidate_ids[1],
                requested_candidate_ids[0],
            ],
        )
        self.assertEqual(summary["leaderboard"][0]["score_text"], "1/1")
        self.assertEqual(summary["leaderboard"][1]["score_text"], "0/0")
        self.assertEqual(summary["leaderboard"][2]["score_text"], "0/0")

    def test_running_single_scan_uses_current_partial_result_for_requested_candidate(self) -> None:
        config = AppConfig.default()
        requested_candidate = "codex-local-default:gpt-5.5:xhigh"
        baseline = [
            _scan_result(
                run_id="run-baseline",
                candidate_id=requested_candidate,
                model="gpt-5.5",
                effort="xhigh",
                question_id=f"q{index}",
                answer_ok=True,
            )
            for index in range(1, 5)
        ]
        current = _scan_result(
            run_id="run-single",
            candidate_id=requested_candidate,
            model="gpt-5.5",
            effort="xhigh",
            question_id="q1",
            answer_ok=True,
        )
        baseline_metadata = {
            **_run_metadata("run-baseline", DEFAULT_QUESTION_PACK_VERSION),
            "selection_mode": "regular",
            "is_complete_regular_round": True,
        }
        single_metadata = {
            **_run_metadata("run-single", DEFAULT_QUESTION_PACK_VERSION),
            "status": "running",
            "completed_at": None,
            "selection_mode": "single",
            "requested_candidate_ids": [requested_candidate],
            "is_complete_regular_round": False,
        }

        summary = build_dashboard_summary(
            [*baseline, current],
            config.model_ingress,
            current_run_id="run-single",
            active_run={
                "run_metadata": single_metadata,
                "planned_attempts_by_candidate": {requested_candidate: 4},
            },
            run_metadata=single_metadata,
            run_metadata_by_id={
                "run-baseline": baseline_metadata,
                "run-single": single_metadata,
            },
        )

        self.assertEqual(len(summary["leaderboard"]), 1)
        self.assertEqual(summary["leaderboard"][0]["candidate_id"], requested_candidate)
        self.assertEqual(summary["leaderboard"][0]["score_text"], "1/1")

    def test_single_success_replaces_only_target_and_can_change_recommendation(self) -> None:
        config = AppConfig.default()
        candidate_a = "codex-local-default:gpt-5.4:high"
        candidate_b = "codex-local-default:gpt-5.5:xhigh"
        baseline = [
            *[
                _scan_result(
                    run_id="run-baseline",
                    candidate_id=candidate_a,
                    model="gpt-5.4",
                    effort="high",
                    question_id=f"q{index}",
                    answer_ok=index < 4,
                )
                for index in range(1, 5)
            ],
            *[
                _scan_result(
                    run_id="run-baseline",
                    candidate_id=candidate_b,
                    model="gpt-5.5",
                    effort="xhigh",
                    question_id=f"q{index}",
                    answer_ok=index < 3,
                )
                for index in range(1, 5)
            ],
        ]
        single_retry = [
            _scan_result(
                run_id="run-single",
                candidate_id=candidate_b,
                model="gpt-5.5",
                effort="xhigh",
                question_id=f"q{index}",
                answer_ok=True,
            )
            for index in range(1, 5)
        ]
        baseline_metadata = {
            **_run_metadata("run-baseline", DEFAULT_QUESTION_PACK_VERSION),
            "selection_mode": "regular",
            "requested_candidate_ids": [candidate_a, candidate_b],
            "regular_candidate_ids": [candidate_a, candidate_b],
            "is_complete_regular_round": True,
        }
        single_metadata = {
            **_run_metadata("run-single", DEFAULT_QUESTION_PACK_VERSION),
            "selection_mode": "single",
            "requested_candidate_ids": [candidate_b],
            "regular_candidate_ids": [candidate_a, candidate_b],
            "is_complete_regular_round": False,
        }

        summary = build_dashboard_summary(
            [*baseline, *single_retry],
            config.model_ingress,
            current_run_id="run-single",
            run_metadata=single_metadata,
            run_metadata_by_id={
                "run-baseline": baseline_metadata,
                "run-single": single_metadata,
            },
        )

        self.assertEqual(summary["best_combination"]["candidate_id"], candidate_b)
        card_a = next(item for item in summary["cards"] if item["id"] == candidate_a)
        card_b = next(item for item in summary["cards"] if item["id"] == candidate_b)
        self.assertEqual(card_a["latest_valid_run_id"], "run-baseline")
        self.assertEqual(card_b["latest_valid_run_id"], "run-single")
        self.assertEqual(card_b["score_text"], "4/4")
        self.assertIn(
            candidate_b,
            [item["candidate_id"] for item in summary["leaderboard"]],
        )

    def test_failed_single_retry_keeps_old_valid_score_and_exposes_failure(self) -> None:
        config = AppConfig.default()
        candidate_a = "codex-local-default:gpt-5.4:high"
        candidate_b = "codex-local-default:gpt-5.5:xhigh"
        baseline = [
            *[
                _scan_result(
                    run_id="run-baseline",
                    candidate_id=candidate_a,
                    model="gpt-5.4",
                    effort="high",
                    question_id=f"q{index}",
                    answer_ok=True,
                )
                for index in range(1, 5)
            ],
            *[
                _scan_result(
                    run_id="run-baseline",
                    candidate_id=candidate_b,
                    model="gpt-5.5",
                    effort="xhigh",
                    question_id=f"q{index}",
                    answer_ok=index < 4,
                )
                for index in range(1, 5)
            ],
        ]
        failed_retry = [
            _scan_result(
                run_id="run-failed",
                candidate_id=candidate_b,
                model="gpt-5.5",
                effort="xhigh",
                question_id=f"q{index}",
                answer_ok=False,
                reasoning_tokens=None,
                error_message="transport unavailable",
                final_status="warn",
            )
            for index in range(1, 4)
        ]
        baseline_metadata = {
            **_run_metadata("run-baseline", DEFAULT_QUESTION_PACK_VERSION),
            "selection_mode": "regular",
            "is_complete_regular_round": True,
        }
        failed_metadata = {
            **_run_metadata("run-failed", DEFAULT_QUESTION_PACK_VERSION),
            "status": "failed",
            "selection_mode": "single",
            "requested_candidate_ids": [candidate_b],
            "is_complete_regular_round": False,
        }

        summary = build_dashboard_summary(
            [*baseline, *failed_retry],
            config.model_ingress,
            current_run_id="run-failed",
            run_metadata=failed_metadata,
            run_metadata_by_id={
                "run-baseline": baseline_metadata,
                "run-failed": failed_metadata,
            },
            current_default_candidate_id=candidate_a,
        )

        card_b = next(item for item in summary["cards"] if item["id"] == candidate_b)
        self.assertEqual(summary["best_combination"]["candidate_id"], candidate_a)
        self.assertEqual(card_b["latest_valid_run_id"], "run-baseline")
        self.assertEqual(card_b["score_text"], "3/4")
        self.assertEqual(card_b["latest_attempt_status"], "failed")
        self.assertEqual(card_b["latest_attempt_error_summary"], "执行错误")
        self.assertTrue(card_b["is_using_previous_valid_result"])

    def test_current_regular_failure_does_not_turn_old_score_into_a_tie(self) -> None:
        config = AppConfig.default()
        candidate_a = "codex-local-default:gpt-5.4:high"
        candidate_b = "codex-local-default:gpt-5.5:xhigh"
        old_candidate_b = [
            _scan_result(
                run_id="run-old",
                candidate_id=candidate_b,
                model="gpt-5.5",
                effort="xhigh",
                question_id=f"q{index}",
                answer_ok=True,
            )
            for index in range(1, 5)
        ]
        current = [
            *[
                _scan_result(
                    run_id="run-current",
                    candidate_id=candidate_a,
                    model="gpt-5.4",
                    effort="high",
                    question_id=f"q{index}",
                    answer_ok=True,
                )
                for index in range(1, 5)
            ],
            *[
                _scan_result(
                    run_id="run-current",
                    candidate_id=candidate_b,
                    model="gpt-5.5",
                    effort="xhigh",
                    question_id=f"q{index}",
                    answer_ok=index != 2,
                    reasoning_tokens=None if index == 2 else 420,
                    error_message=(
                        "codex exec timed out after 300s" if index == 2 else None
                    ),
                    final_status="warn" if index == 2 else "pass",
                )
                for index in range(1, 5)
            ],
        ]
        old_metadata = {
            **_run_metadata("run-old", DEFAULT_QUESTION_PACK_VERSION),
            "selection_mode": "regular",
            "requested_candidate_ids": [candidate_b],
            "is_complete_regular_round": True,
        }
        current_metadata = {
            **_run_metadata("run-current", DEFAULT_QUESTION_PACK_VERSION),
            "status": "degraded",
            "selection_mode": "regular",
            "requested_candidate_ids": [candidate_a, candidate_b],
            "regular_candidate_ids": [candidate_a, candidate_b],
            "is_complete_regular_round": True,
        }

        summary = build_dashboard_summary(
            [*old_candidate_b, *current],
            config.model_ingress,
            current_run_id="run-current",
            run_metadata=current_metadata,
            run_metadata_by_id={
                "run-old": old_metadata,
                "run-current": current_metadata,
            },
        )

        self.assertEqual(summary["best_combination"]["candidate_id"], candidate_a)
        self.assertEqual(summary["best_combination"]["recommendation_basis"], "overall_score_lead")
        card_b = next(item for item in summary["cards"] if item["id"] == candidate_b)
        self.assertEqual(card_b["score_text"], "3/4")
        self.assertFalse(card_b["is_current_run_eligible"])
        self.assertEqual(card_b["repairable_question_ids"], ["q2"])
        self.assertEqual(card_b["historical_score_text"], "4/4")

    def test_current_regular_missing_question_is_repairable(self) -> None:
        config = AppConfig.default()
        candidate_a = "codex-local-default:gpt-5.4:high"
        candidate_b = "codex-local-default:gpt-5.5:xhigh"
        current = [
            *[
                _scan_result(
                    run_id="run-current",
                    candidate_id=candidate_a,
                    model="gpt-5.4",
                    effort="high",
                    question_id=f"q{index}",
                    answer_ok=True,
                )
                for index in range(1, 5)
            ],
            *[
                _scan_result(
                    run_id="run-current",
                    candidate_id=candidate_b,
                    model="gpt-5.5",
                    effort="xhigh",
                    question_id=f"q{index}",
                    answer_ok=True,
                )
                for index in (1, 3, 4)
            ],
        ]
        metadata = {
            **_run_metadata("run-current", DEFAULT_QUESTION_PACK_VERSION),
            "status": "degraded",
            "selection_mode": "regular",
            "requested_candidate_ids": [candidate_a, candidate_b],
            "regular_candidate_ids": [candidate_a, candidate_b],
            "question_ids": ["q1", "q2", "q3", "q4"],
        }

        summary = build_dashboard_summary(
            current,
            config.model_ingress,
            current_run_id="run-current",
            run_metadata=metadata,
            run_metadata_by_id={"run-current": metadata},
        )

        card_b = next(item for item in summary["cards"] if item["id"] == candidate_b)
        self.assertEqual(card_b["score_text"], "3/3")
        self.assertEqual(card_b["question_attempted"], 4)
        self.assertFalse(card_b["is_current_run_eligible"])
        self.assertEqual(card_b["repairable_question_ids"], ["q2"])

    def test_current_regular_candidate_with_no_results_can_repair_all_questions(self) -> None:
        config = AppConfig.default()
        candidate_a = "codex-local-default:gpt-5.4:high"
        candidate_b = "codex-local-default:gpt-5.5:xhigh"
        current = [
            _scan_result(
                run_id="run-current",
                candidate_id=candidate_a,
                model="gpt-5.4",
                effort="high",
                question_id=f"q{index}",
                answer_ok=True,
            )
            for index in range(1, 5)
        ]
        metadata = {
            **_run_metadata("run-current", DEFAULT_QUESTION_PACK_VERSION),
            "status": "degraded",
            "selection_mode": "regular",
            "requested_candidate_ids": [candidate_a, candidate_b],
            "regular_candidate_ids": [candidate_a, candidate_b],
            "question_ids": ["q1", "q2", "q3", "q4"],
        }

        summary = build_dashboard_summary(
            current,
            config.model_ingress,
            current_run_id="run-current",
            run_metadata=metadata,
            run_metadata_by_id={"run-current": metadata},
        )

        card_b = next(item for item in summary["cards"] if item["id"] == candidate_b)
        self.assertEqual(card_b["score_text"], "0/0")
        self.assertEqual(card_b["question_attempted"], 4)
        self.assertFalse(card_b["is_current_run_eligible"])
        self.assertEqual(card_b["repairable_question_ids"], ["q1", "q2", "q3", "q4"])

    def test_failed_retry_of_recommended_candidate_requires_retry_decision(self) -> None:
        config = AppConfig.default()
        candidate_a = "codex-local-default:gpt-5.4:high"
        candidate_b = "codex-local-default:gpt-5.5:xhigh"
        baseline = [
            *[
                _scan_result(
                    run_id="run-baseline",
                    candidate_id=candidate_a,
                    model="gpt-5.4",
                    effort="high",
                    question_id=f"q{index}",
                    answer_ok=True,
                )
                for index in range(1, 5)
            ],
            *[
                _scan_result(
                    run_id="run-baseline",
                    candidate_id=candidate_b,
                    model="gpt-5.5",
                    effort="xhigh",
                    question_id=f"q{index}",
                    answer_ok=index < 4,
                )
                for index in range(1, 5)
            ],
        ]
        failed_retry = [
            _scan_result(
                run_id="run-failed",
                candidate_id=candidate_a,
                model="gpt-5.4",
                effort="high",
                question_id=f"q{index}",
                answer_ok=False,
                reasoning_tokens=None,
                error_message="transport unavailable",
                final_status="error",
            )
            for index in range(1, 4)
        ]
        baseline_metadata = {
            **_run_metadata("run-baseline", DEFAULT_QUESTION_PACK_VERSION),
            "selection_mode": "regular",
            "is_complete_regular_round": True,
        }
        failed_metadata = {
            **_run_metadata("run-failed", DEFAULT_QUESTION_PACK_VERSION),
            "status": "failed",
            "selection_mode": "single",
            "requested_candidate_ids": [candidate_a],
            "is_complete_regular_round": False,
        }

        summary = build_dashboard_summary(
            [*baseline, *failed_retry],
            config.model_ingress,
            current_run_id="run-failed",
            run_metadata=failed_metadata,
            run_metadata_by_id={
                "run-baseline": baseline_metadata,
                "run-failed": failed_metadata,
            },
            current_default_candidate_id=candidate_a,
        )

        best = summary["best_combination"]
        self.assertEqual(best["candidate_id"], candidate_a)
        self.assertEqual(best["recommendation_outcome"], "keep")
        self.assertEqual(best["evidence_state"], "retained_after_failure")
        self.assertEqual(best["decision_state"], "retain_after_failure")
        self.assertEqual(best["decision_title"], "本次失败，保留旧成绩")
        self.assertEqual(best["decision_action_label"], "查看失败详情")

    def test_degraded_retry_with_execution_error_keeps_old_result_and_requires_retry(self) -> None:
        config = AppConfig.default()
        candidate_a = "codex-local-default:gpt-5.4:high"
        candidate_b = "codex-local-default:gpt-5.5:xhigh"
        baseline = [
            *[
                _scan_result(
                    run_id="run-baseline",
                    candidate_id=candidate_a,
                    model="gpt-5.4",
                    effort="high",
                    question_id=f"q{index}",
                    answer_ok=True,
                )
                for index in range(1, 5)
            ],
            *[
                _scan_result(
                    run_id="run-baseline",
                    candidate_id=candidate_b,
                    model="gpt-5.5",
                    effort="xhigh",
                    question_id=f"q{index}",
                    answer_ok=index < 4,
                )
                for index in range(1, 5)
            ],
        ]
        degraded_retry = [
            _scan_result(
                run_id="run-degraded",
                candidate_id=candidate_a,
                model="gpt-5.4",
                effort="high",
                question_id=f"q{index}",
                answer_ok=False,
                reasoning_tokens=None,
                error_message="transport unavailable",
                final_status="error",
            )
            for index in range(1, 4)
        ]
        baseline_metadata = {
            **_run_metadata("run-baseline", DEFAULT_QUESTION_PACK_VERSION),
            "selection_mode": "regular",
            "is_complete_regular_round": True,
        }
        degraded_metadata = {
            **_run_metadata("run-degraded", DEFAULT_QUESTION_PACK_VERSION),
            "status": "degraded",
            "selection_mode": "single",
            "requested_candidate_ids": [candidate_a],
            "is_complete_regular_round": False,
        }

        summary = build_dashboard_summary(
            [*baseline, *degraded_retry],
            config.model_ingress,
            current_run_id="run-degraded",
            run_metadata=degraded_metadata,
            run_metadata_by_id={
                "run-baseline": baseline_metadata,
                "run-degraded": degraded_metadata,
            },
            current_default_candidate_id=candidate_a,
        )

        best = summary["best_combination"]
        self.assertEqual(best["candidate_id"], candidate_a)
        self.assertEqual(best["recommendation_outcome"], "keep")
        self.assertEqual(best["evidence_state"], "retained_after_failure")
        self.assertEqual(best["decision_state"], "retain_after_failure")
        self.assertEqual(best["decision_title"], "本次失败，保留旧成绩")
        self.assertEqual(best["decision_action_label"], "查看失败详情")

    def test_degraded_fixture_suppresses_recommendation_and_statistics(self) -> None:
        fixture_path = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "2026-07-10-degraded-run.json"
        )
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        metadata = fixture["run_metadata"]
        history: list[ScanResult] = []
        for candidate in fixture["candidates"]:
            for attempt_index, outcome in enumerate(candidate["outcomes"], start=1):
                is_error = outcome == "error"
                is_ok = outcome == "ok"
                history.append(
                    _scan_result(
                        run_id=metadata["run_id"],
                        candidate_id=candidate["candidate_id"],
                        model=candidate["model"],
                        effort=candidate["effort"],
                        question_id=f"q{attempt_index}",
                        answer_ok=is_ok,
                        elapsed_seconds=300.0 if not is_ok else 42.0,
                        reasoning_tokens=None if is_error else 430,
                        final_status="warn" if not is_ok else "pass",
                        flags=["timeout", "missing_usage"] if is_error else (["timeout"] if outcome == "wrong" else []),
                        error_message="transport unavailable" if is_error else None,
                    )
                )

        summary = build_dashboard_summary(
            history,
            AppConfig.default().model_ingress,
            current_run_id=metadata["run_id"],
            run_metadata=metadata,
            run_metadata_by_id={metadata["run_id"]: metadata},
        )

        self.assertIsNone(summary["best_combination"])
        self.assertEqual(summary["statistics"], {"trend_series": []})

    def test_build_dashboard_summary_exposes_recent_statistics_from_same_pack_runs(self) -> None:
        config = AppConfig.default()
        history: list[ScanResult] = []
        metadata_by_run: dict[str, dict[str, object]] = {}
        medium_id = "codex-local-default:gpt-5.4:medium"
        xhigh_id = "codex-local-default:gpt-5.4:xhigh"
        high_id = "codex-local-default:gpt-5.5:high"
        medium55_id = "codex-local-default:gpt-5.5:medium"

        for index in range(1, 9):
            run_id = f"run-{index:02d}"
            metadata_by_run[run_id] = _run_metadata(run_id, DEFAULT_QUESTION_PACK_VERSION)
            for question_index in range(1, 5):
                history.append(
                    _scan_result(
                        run_id=run_id,
                        candidate_id=medium_id,
                        model="gpt-5.4",
                        effort="medium",
                        question_id=f"q{question_index}",
                        answer_ok=True,
                        elapsed_seconds=40 + index,
                        reference_cost_usd=0.001,
                        cost_status="estimated",
                    )
                )
                history.append(
                    _scan_result(
                        run_id=run_id,
                        candidate_id=xhigh_id,
                        model="gpt-5.4",
                        effort="xhigh",
                        question_id=f"q{question_index}",
                        answer_ok=not (index == 8 and question_index == 4),
                        elapsed_seconds=90 + index,
                    )
                )
                history.append(
                    _scan_result(
                        run_id=run_id,
                        candidate_id=high_id,
                        model="gpt-5.5",
                        effort="high",
                        question_id=f"q{question_index}",
                        answer_ok=question_index < 4,
                        elapsed_seconds=60 + index,
                        reasoning_tokens=516 if index in {2, 5} and question_index == 1 else 430,
                        final_status="warn" if index in {2, 5} and question_index == 1 else "pass",
                    )
                )
                history.append(
                    _scan_result(
                        run_id=run_id,
                        candidate_id=medium55_id,
                        model="gpt-5.5",
                        effort="medium",
                        question_id=f"q{question_index}",
                        answer_ok=question_index < 3,
                        elapsed_seconds=20 + index,
                    )
                )

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id="run-08",
            run_metadata=metadata_by_run["run-08"],
            run_metadata_by_id=metadata_by_run,
        )

        statistics = summary["statistics"]
        self.assertEqual(set(statistics), {"trend_series"})
        series_by_id = {
            series["candidate_id"]: series
            for series in statistics["trend_series"]
        }
        self.assertEqual(
            set(series_by_id),
            {medium_id, xhigh_id, high_id, medium55_id},
        )
        for series in series_by_id.values():
            self.assertEqual(
                set(series),
                {
                    "candidate_id",
                    "overall_score_run_indices",
                    "overall_score_values",
                },
            )
            self.assertEqual(
                series["overall_score_run_indices"],
                list(range(8)),
            )
        self.assertEqual(
            series_by_id[medium_id]["overall_score_values"],
            [100] * 8,
        )
        self.assertEqual(
            series_by_id[xhigh_id]["overall_score_values"],
            [100, 100, 100, 100, 100, 100, 100, 75],
        )
        self.assertEqual(
            series_by_id[high_id]["overall_score_values"],
            [75] * 8,
        )
        self.assertEqual(
            series_by_id[medium55_id]["overall_score_values"],
            [50] * 8,
        )

    def test_build_dashboard_summary_separates_completed_runs_across_pack_versions(self) -> None:
        config = AppConfig.default()
        history: list[ScanResult] = []
        metadata_by_run: dict[str, dict[str, object]] = {}
        candidate_id = "codex-local-default:gpt-5.4:medium"

        for index in range(1, 9):
            run_id = f"run-{index:02d}"
            pack_version = DEFAULT_QUESTION_PACK_VERSION if index >= 4 else "legacy-pack"
            metadata_by_run[run_id] = _run_metadata(run_id, pack_version)
            for question_index in range(1, 5):
                history.append(
                    _scan_result(
                        run_id=run_id,
                        candidate_id=candidate_id,
                        model="gpt-5.4",
                        effort="medium",
                        question_id=f"q{question_index}",
                        answer_ok=True,
                    )
                )

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id="run-08",
            run_metadata=metadata_by_run["run-08"],
            run_metadata_by_id=metadata_by_run,
        )

        statistics = summary["statistics"]
        self.assertEqual(len(statistics["trend_series"]), 1)
        self.assertEqual(
            statistics["trend_series"][0],
            {
                "candidate_id": candidate_id,
                "overall_score_run_indices": [0, 1, 2, 3, 4],
                "overall_score_values": [100, 100, 100, 100, 100],
            },
        )

    def test_degraded_regular_run_contributes_observed_models_to_trends(self) -> None:
        config = AppConfig.default()
        baseline_id = "codex-local-default:gpt-5.4:high"
        new_model_id = "codex-local-default:gpt-5.6-sol:high"
        baseline_run = "run-baseline"
        degraded_run = "run-degraded-expanded-catalog"
        history: list[ScanResult] = []

        for question_index in range(1, 5):
            history.append(
                _scan_result(
                    run_id=baseline_run,
                    candidate_id=baseline_id,
                    model="gpt-5.4",
                    effort="high",
                    question_id=f"q{question_index}",
                    answer_ok=True,
                )
            )
            history.append(
                _scan_result(
                    run_id=degraded_run,
                    candidate_id=new_model_id,
                    model="gpt-5.6-sol",
                    effort="high",
                    question_id=f"q{question_index}",
                    answer_ok=question_index < 4,
                )
            )

        baseline_metadata = _run_metadata(baseline_run, DEFAULT_QUESTION_PACK_VERSION)
        degraded_metadata = {
            **_run_metadata(degraded_run, DEFAULT_QUESTION_PACK_VERSION),
            "status": "degraded",
            "selection_mode": "regular",
            "requested_candidate_ids": [baseline_id, new_model_id],
            "regular_candidate_ids": [baseline_id, new_model_id],
            "is_complete_regular_round": False,
        }
        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id=degraded_run,
            run_metadata=degraded_metadata,
            run_metadata_by_id={
                baseline_run: baseline_metadata,
                degraded_run: degraded_metadata,
            },
        )

        statistics = summary["statistics"]
        series_by_id = {
            item["candidate_id"]: item
            for item in statistics["trend_series"]
        }
        self.assertIn(new_model_id, series_by_id)
        self.assertEqual(series_by_id[new_model_id]["overall_score_values"], [75])
        self.assertEqual(series_by_id[new_model_id]["overall_score_run_indices"], [1])

    def test_completed_nonqualifying_regular_run_remains_an_observed_trend(self) -> None:
        config = AppConfig.default()
        candidate_id = "codex-local-default:gpt-5.4:high"
        run_id = "run-completed-observed"
        history = [
            _scan_result(
                run_id=run_id,
                candidate_id=candidate_id,
                model="gpt-5.4",
                effort="high",
                question_id=f"q{index}",
                answer_ok=True,
            )
            for index in range(1, 5)
        ]
        metadata = {
            **_run_metadata(run_id, DEFAULT_QUESTION_PACK_VERSION),
            "status": "completed",
            "selection_mode": "regular",
            "requested_candidate_ids": [candidate_id],
            "regular_candidate_ids": [candidate_id],
            "is_complete_regular_round": False,
        }

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id=run_id,
            run_metadata=metadata,
            run_metadata_by_id={run_id: metadata},
        )

        statistics = summary["statistics"]
        self.assertTrue(statistics["trend_series"])
        self.assertEqual(statistics["trend_series"][0]["overall_score_values"], [100])
        self.assertEqual(statistics["trend_series"][0]["overall_score_run_indices"], [0])

    def test_historical_successful_nonqualifying_runs_still_plot_sparse_trend_points(self) -> None:
        config = AppConfig.default()
        baseline_id = "codex-local-default:gpt-5.4:high"
        observed_id = "codex-local-default:gpt-5.6-luna:high"
        baseline_run = "run-formal-baseline"
        degraded_run = "run-observed-degraded"
        nonqualifying_run = "run-observed-completed"
        current_run = "run-formal-current"
        history: list[ScanResult] = []

        for question_index in range(1, 5):
            history.append(
                _scan_result(
                    run_id=baseline_run,
                    candidate_id=baseline_id,
                    model="gpt-5.4",
                    effort="high",
                    question_id=f"q{question_index}",
                    answer_ok=True,
                )
            )
        for question_index in range(1, 5):
            history.append(
                _scan_result(
                    run_id=degraded_run,
                    candidate_id=observed_id,
                    model="gpt-5.6-luna",
                    effort="high",
                    question_id=f"q{question_index}",
                    answer_ok=question_index < 3,
                )
            )
        for question_index in range(1, 5):
            history.append(
                _scan_result(
                    run_id=nonqualifying_run,
                    candidate_id=observed_id,
                    model="gpt-5.6-luna",
                    effort="high",
                    question_id=f"q{question_index}",
                    answer_ok=question_index < 4,
                )
            )
        history.append(
            _scan_result(
                run_id=degraded_run,
                candidate_id=baseline_id,
                model="gpt-5.4",
                effort="high",
                question_id="q2",
                answer_ok=False,
                error_message="transport unavailable",
                final_status="warn",
            )
        )
        for question_index in range(1, 5):
            history.append(
                _scan_result(
                    run_id=current_run,
                    candidate_id=observed_id,
                    model="gpt-5.6-luna",
                    effort="high",
                    question_id=f"q{question_index}",
                    answer_ok=True,
                )
            )

        metadata_by_run = {
            baseline_run: {
                **_run_metadata(baseline_run, DEFAULT_QUESTION_PACK_VERSION),
                "selection_mode": "regular",
                "requested_candidate_ids": [baseline_id],
                "regular_candidate_ids": [baseline_id],
                "is_complete_regular_round": True,
            },
            degraded_run: {
                **_run_metadata(degraded_run, DEFAULT_QUESTION_PACK_VERSION),
                "status": "degraded",
                "selection_mode": "regular",
                "requested_candidate_ids": [baseline_id, observed_id],
                "regular_candidate_ids": [baseline_id, observed_id],
                "is_complete_regular_round": False,
            },
            nonqualifying_run: {
                **_run_metadata(nonqualifying_run, DEFAULT_QUESTION_PACK_VERSION),
                "selection_mode": "regular",
                "requested_candidate_ids": [observed_id],
                "regular_candidate_ids": [observed_id],
                "is_complete_regular_round": False,
            },
            current_run: {
                **_run_metadata(current_run, DEFAULT_QUESTION_PACK_VERSION),
                "selection_mode": "regular",
                "requested_candidate_ids": [observed_id],
                "regular_candidate_ids": [observed_id],
                "is_complete_regular_round": True,
            },
        }

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id=current_run,
            run_metadata=metadata_by_run[current_run],
            run_metadata_by_id=metadata_by_run,
        )

        statistics = summary["statistics"]
        series_by_id = {
            item["candidate_id"]: item
            for item in statistics["trend_series"]
        }
        self.assertEqual(
            series_by_id[observed_id]["overall_score_run_indices"],
            [1, 2, 3],
        )
        self.assertEqual(
            series_by_id[observed_id]["overall_score_values"],
            [50, 75, 100],
        )
        self.assertEqual(
            series_by_id[baseline_id]["overall_score_run_indices"],
            [0],
        )

    def test_appended_physical_run_is_treated_as_latest_logical_round(self) -> None:
        config = AppConfig.default()
        baseline_id = "codex-local-default:gpt-5.4:high"
        appended_id = "codex-local-default:gpt-5.6-sol:medium"
        current_group_id = "run-current-group"
        older_run_id = "run-older"
        history: list[ScanResult] = []

        for question_index in range(1, 5):
            history.append(
                _scan_result(
                    run_id=current_group_id,
                    candidate_id=baseline_id,
                    model="gpt-5.4",
                    effort="high",
                    question_id=f"q{question_index}",
                    answer_ok=True,
                )
            )
        for question_index in range(1, 5):
            history.append(
                _scan_result(
                    run_id=older_run_id,
                    candidate_id=baseline_id,
                    model="gpt-5.4",
                    effort="high",
                    question_id=f"q{question_index}",
                    answer_ok=question_index < 4,
                )
            )
        for question_index in range(1, 5):
            history.append(
                _scan_result(
                    run_id=current_group_id,
                    candidate_id=appended_id,
                    model="gpt-5.6-sol",
                    effort="medium",
                    question_id=f"q{question_index}",
                    answer_ok=True,
                )
            )

        current_metadata = {
            **_run_metadata(current_group_id, DEFAULT_QUESTION_PACK_VERSION),
            "selection_mode": "custom",
            "requested_candidate_ids": [baseline_id, appended_id],
            "regular_candidate_ids": [baseline_id],
            "comparison_group_id": current_group_id,
            "comparison_group_mode": "custom_append",
            "is_complete_regular_round": False,
        }
        older_metadata = {
            **_run_metadata(older_run_id, DEFAULT_QUESTION_PACK_VERSION),
            "selection_mode": "regular",
            "requested_candidate_ids": [baseline_id],
            "regular_candidate_ids": [baseline_id],
            "is_complete_regular_round": True,
        }

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id=current_group_id,
            run_metadata=current_metadata,
            run_metadata_by_id={
                current_group_id: current_metadata,
                older_run_id: older_metadata,
            },
        )

        series_by_id = {
            item["candidate_id"]: item
            for item in summary["statistics"]["trend_series"]
        }
        self.assertEqual(
            series_by_id[appended_id]["overall_score_run_indices"],
            [1],
        )

    def test_single_scan_appended_into_regular_group_does_not_replace_recent_rounds(self) -> None:
        config = AppConfig.default()
        baseline_id = "codex-local-default:gpt-5.4:high"
        appended_id = "api-deepseek:deepseek-v4-flash:default"
        current_group_id = "run-current-group"
        older_run_id = "run-older"
        history: list[ScanResult] = []

        config.model_ingress.connections.append(
            ConnectionConfig(
                id="api-deepseek",
                source_id="api_models",
                name="DeepSeek",
                enabled=True,
                base_url="https://api.deepseek.com",
                api_key_ref="env:DEEPSEEK_KEY",
                api_format="openai_chat",
                model_candidates=[
                    ModelCandidateConfig(
                        id=appended_id,
                        connection_id="api-deepseek",
                        model_id="deepseek-v4-flash",
                        display_name="DeepSeek V4 Flash",
                        scan_profile="default",
                        enabled=False,
                    )
                ],
            )
        )

        for question_index in range(1, 5):
            history.append(
                _scan_result(
                    run_id=current_group_id,
                    candidate_id=baseline_id,
                    model="gpt-5.4",
                    effort="high",
                    question_id=f"q{question_index}",
                    answer_ok=True,
                )
            )
        for question_index in range(1, 5):
            history.append(
                _scan_result(
                    run_id=older_run_id,
                    candidate_id=baseline_id,
                    model="gpt-5.4",
                    effort="high",
                    question_id=f"q{question_index}",
                    answer_ok=question_index < 4,
                )
            )
        history.append(
            _scan_result(
                run_id=current_group_id,
                candidate_id=appended_id,
                model="deepseek-v4-flash",
                effort="default",
                question_id="q1",
                answer_ok=False,
                error_message="keychain secret is unavailable",
                final_status="error",
            )
        )

        current_metadata = {
            **_run_metadata(current_group_id, DEFAULT_QUESTION_PACK_VERSION),
            "selection_mode": "custom",
            "requested_candidate_ids": [baseline_id, appended_id],
            "regular_candidate_ids": [baseline_id],
            "comparison_group_id": current_group_id,
            "comparison_group_mode": "custom_append",
            "appended_candidate_ids": [appended_id],
            "is_complete_regular_round": True,
        }
        older_metadata = {
            **_run_metadata(older_run_id, DEFAULT_QUESTION_PACK_VERSION),
            "selection_mode": "regular",
            "requested_candidate_ids": [baseline_id],
            "regular_candidate_ids": [baseline_id],
            "is_complete_regular_round": True,
        }

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id=current_group_id,
            run_metadata=current_metadata,
            run_metadata_by_id={
                current_group_id: current_metadata,
                older_run_id: older_metadata,
            },
        )

        series_by_id = {
            item["candidate_id"]: item
            for item in summary["statistics"]["trend_series"]
        }
        self.assertEqual(
            series_by_id[baseline_id]["overall_score_run_indices"],
            [0, 1],
        )
        self.assertNotIn(appended_id, series_by_id)

    def test_completed_regular_run_with_recovered_error_still_counts_in_statistics(self) -> None:
        config = AppConfig.default()
        baseline_id = "codex-local-default:gpt-5.4:high"
        expanded_id = "codex-local-default:gpt-5.6-sol:xhigh"
        baseline_run = "run-baseline-completed"
        current_run = "run-recovered-completed"
        history: list[ScanResult] = []

        for question_index in range(1, 5):
            history.append(
                _scan_result(
                    run_id=baseline_run,
                    candidate_id=baseline_id,
                    model="gpt-5.4",
                    effort="high",
                    question_id=f"q{question_index}",
                    answer_ok=True,
                )
            )

        history.append(
            _scan_result(
                run_id=current_run,
                candidate_id=baseline_id,
                model="gpt-5.4",
                effort="high",
                question_id="q2",
                answer_ok=False,
                error_message="codex exec timed out after 180s",
                final_status="warn",
            )
        )
        for question_index in range(1, 5):
            history.append(
                _scan_result(
                    run_id=current_run,
                    candidate_id=baseline_id,
                    model="gpt-5.4",
                    effort="high",
                    question_id=f"q{question_index}",
                    answer_ok=True,
                )
            )
            history.append(
                _scan_result(
                    run_id=current_run,
                    candidate_id=expanded_id,
                    model="gpt-5.6-sol",
                    effort="xhigh",
                    question_id=f"q{question_index}",
                    answer_ok=True,
                )
            )

        baseline_metadata = {
            **_run_metadata(baseline_run, DEFAULT_QUESTION_PACK_VERSION),
            "selection_mode": "regular",
            "requested_candidate_ids": [baseline_id],
            "regular_candidate_ids": [baseline_id],
            "is_complete_regular_round": True,
        }
        current_metadata = {
            **_run_metadata(current_run, DEFAULT_QUESTION_PACK_VERSION),
            "selection_mode": "regular",
            "requested_candidate_ids": [baseline_id, expanded_id],
            "regular_candidate_ids": [baseline_id, expanded_id],
            "is_complete_regular_round": True,
        }

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id=current_run,
            run_metadata=current_metadata,
            run_metadata_by_id={
                baseline_run: baseline_metadata,
                current_run: current_metadata,
            },
        )

        statistics = summary["statistics"]
        series_by_id = {
            item["candidate_id"]: item
            for item in statistics["trend_series"]
        }
        self.assertIn(expanded_id, series_by_id)
        self.assertEqual(series_by_id[expanded_id]["overall_score_values"], [100])
        self.assertEqual(series_by_id[expanded_id]["overall_score_run_indices"], [1])

    def test_cards_expose_question_probe_results_for_current_run_explainability(self) -> None:
        config = AppConfig.default()
        history = [
            _scan_result(
                run_id="run-probes",
                candidate_id="codex-local-default:gpt-5.4:high",
                model="gpt-5.4",
                effort="high",
                question_id="01_candy",
                answer_ok=True,
                capability_label="最坏情况",
                scorer_reason="regex",
                expected_summary="21",
                actual_summary="21",
            ),
            _scan_result(
                run_id="run-probes",
                candidate_id="codex-local-default:gpt-5.4:high",
                model="gpt-5.4",
                effort="high",
                question_id="03_test_coverage_selection",
                answer_ok=False,
                capability_label="覆盖优化",
                scorer_reason="budget_mismatch",
                expected_summary="budget=8, gap=12",
                actual_summary="budget=9, gap=4",
            ),
        ]

        summary = build_dashboard_summary(history, config.model_ingress, current_run_id="run-probes")

        card = next(item for item in summary["cards"] if item["label"] == "gpt-5.4 / high")
        self.assertEqual(
            card["question_results"],
            [
                {
                    "question_id": "01_candy",
                    "question_title": "01_candy",
                    "capability_id": "01_candy",
                    "capability_label": "最坏情况",
                    "detail_label": "01_candy",
                    "phase": "scan",
                    "status": "pass",
                    "expected_summary": "21",
                    "actual_summary": "21",
                    "answer_preview": "21",
                    "scorer_reason": "regex",
                    "latency_s": 1.0,
                    "input_tokens": 100,
                    "cached_input_tokens": None,
                    "cache_write_input_tokens": None,
                    "output_tokens": 20,
                    "reasoning_tokens": 430,
                },
                {
                    "question_id": "03_test_coverage_selection",
                    "question_title": "03_test_coverage_selection",
                    "capability_id": "03_test_coverage_selection",
                    "capability_label": "覆盖优化",
                    "detail_label": "03_test_coverage_selection",
                    "phase": "scan",
                    "status": "fail",
                    "expected_summary": "budget=8, gap=12",
                    "actual_summary": "budget=9, gap=4",
                    "answer_preview": "20",
                    "scorer_reason": "budget_mismatch",
                    "latency_s": 1.0,
                    "input_tokens": 100,
                    "cached_input_tokens": None,
                    "cache_write_input_tokens": None,
                    "output_tokens": 20,
                    "reasoning_tokens": 430,
                },
            ],
        )
        leaderboard_entry = next(item for item in summary["leaderboard"] if item["label"] == "gpt-5.4 / high")
        self.assertEqual(leaderboard_entry["question_results"], card["question_results"])
        self.assertEqual(leaderboard_entry["source_mode"], card["source_mode"])

    def test_question_results_follow_planned_order_when_parallel_cases_finish_out_of_order(self) -> None:
        config = AppConfig.default()
        candidate_id = "codex-local-default:gpt-5.4:high"
        history = [
            _scan_result(
                run_id="run-parallel-order",
                candidate_id=candidate_id,
                model="gpt-5.4",
                effort="high",
                question_id="01_session_bundle_repair",
                answer_ok=False,
                scorer_diagnostics={"semantic_passed": 8, "semantic_total": 10},
            ),
            _scan_result(
                run_id="run-parallel-order",
                candidate_id=candidate_id,
                model="gpt-5.4",
                effort="high",
                phase="scan",
                question_id="05_cache_regression_test_design",
                answer_ok=False,
                scorer_diagnostics={"semantic_passed": 5, "semantic_total": 10},
            ),
            _scan_result(
                run_id="run-parallel-order",
                candidate_id=candidate_id,
                model="gpt-5.4",
                effort="high",
                question_id="04_cross_loop_singleflight",
                answer_ok=False,
                scorer_diagnostics={"semantic_passed": 9, "semantic_total": 10},
            ),
            _scan_result(
                run_id="run-parallel-order",
                candidate_id=candidate_id,
                model="gpt-5.4",
                effort="high",
                question_id="03_test_coverage_selection",
                answer_ok=True,
                scorer_diagnostics={"semantic_passed": 10, "semantic_total": 10},
            ),
        ]
        metadata = {
            **_run_metadata("run-parallel-order", DEFAULT_QUESTION_PACK_VERSION),
            "scoring_mode": "semantic_q1_q5_equal_v2",
            "question_ids": [
                "01_session_bundle_repair",
                "02_code_counterexample_maxgap",
                "03_test_coverage_selection",
                "04_cross_loop_singleflight",
                "05_cache_regression_test_design",
            ],
        }

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id="run-parallel-order",
            run_metadata=metadata,
        )

        card = next(item for item in summary["cards"] if item["id"] == candidate_id)
        self.assertEqual(
            [item["question_id"] for item in card["question_results"]],
            [
                "01_session_bundle_repair",
                "03_test_coverage_selection",
                "04_cross_loop_singleflight",
                "05_cache_regression_test_design",
            ],
        )

    def test_question_results_preserve_execution_failure_kinds(self) -> None:
        config = AppConfig.default()
        candidate_id = "codex-local-default:gpt-5.4:high"
        history = [
            _scan_result(
                run_id="run-question-states",
                candidate_id=candidate_id,
                model="gpt-5.4",
                effort="high",
                question_id="01_candy",
                answer_ok=True,
                elapsed_seconds=91.0,
                final_status="warn",
                flags=["timeout"],
            ),
            _scan_result(
                run_id="run-question-states",
                candidate_id=candidate_id,
                model="gpt-5.4",
                effort="high",
                question_id="02_code_counterexample_maxgap",
                answer_ok=False,
                final_status="warn",
                scorer_diagnostics={"semantic_passed": 19, "semantic_total": 20},
            ),
            _scan_result(
                run_id="run-question-states",
                candidate_id=candidate_id,
                model="gpt-5.4",
                effort="high",
                question_id="03_test_coverage_selection",
                answer_ok=False,
                final_status="warn",
                error_message="request timed out after 600s",
            ),
            _scan_result(
                run_id="run-question-states",
                candidate_id=candidate_id,
                model="gpt-5.4",
                effort="high",
                question_id="04_expression_search_24",
                answer_ok=False,
                final_status="error",
                error_message="transport unavailable",
            ),
            _scan_result(
                run_id="run-question-states",
                candidate_id=candidate_id,
                model="gpt-5.4",
                effort="high",
                question_id="05_truncated_output",
                answer_ok=False,
                final_status="truncated",
            ),
        ]

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id="run-question-states",
        )

        card = next(item for item in summary["cards"] if item["label"] == "gpt-5.4 / high")
        statuses = {
            item["question_id"]: item["status"]
            for item in card["question_results"]
        }
        self.assertEqual(
            statuses,
            {
                "01_candy": "pass",
                "02_code_counterexample_maxgap": "fail",
                "03_test_coverage_selection": "timeout",
                "04_expression_search_24": "error",
                "05_truncated_output": "truncated",
            },
        )
        scored_result = next(
            item
            for item in card["question_results"]
            if item["question_id"] == "02_code_counterexample_maxgap"
        )
        self.assertEqual(scored_result["semantic_score"], 19)
        self.assertEqual(scored_result["semantic_total"], 20)

    def test_build_dashboard_summary_computes_516_frequency_and_breakdown(self) -> None:
        config = AppConfig.default()
        history = [
            ScanResult(
                run_id="run-old",
                model="gpt-5.4",
                effort="high",
                question_id="logic",
                attempt_index=1,
                started_at="2026-06-30T10:00:00+08:00",
                elapsed_seconds=10.0,
                source_mode="live",
                answer_ok=True,
                answer_preview="21",
                input_tokens=100,
                output_tokens=20,
                reasoning_tokens=516,
                final_status="warn",
            ),
            ScanResult(
                run_id="run-new",
                model="gpt-5.4",
                effort="high",
                question_id="logic",
                attempt_index=1,
                started_at="2026-06-30T10:10:00+08:00",
                elapsed_seconds=9.0,
                source_mode="live",
                answer_ok=True,
                answer_preview="21",
                input_tokens=100,
                output_tokens=20,
                reasoning_tokens=430,
                final_status="pass",
            ),
            ScanResult(
                run_id="run-new",
                model="gpt-5.5",
                effort="xhigh",
                question_id="logic",
                attempt_index=1,
                started_at="2026-06-30T10:20:00+08:00",
                elapsed_seconds=11.0,
                source_mode="live",
                answer_ok=False,
                answer_preview="20",
                input_tokens=100,
                output_tokens=20,
                reasoning_tokens=516,
                final_status="warn",
            ),
        ]

        summary = build_dashboard_summary(history, config.model_ingress)

        self.assertEqual(summary["current_run_id"], "run-new")
        self.assertEqual(summary["run_count"], 2)
        self.assertEqual(summary["hits_516"], 1)
        self.assertEqual(summary["hit_rate_516"], 50)
        self.assertEqual(summary["pass_rate"], 50)
        self.assertEqual(summary["reasoning_tokens_total"], 946)
        self.assertEqual(summary["truncation_trend"], [1, 0, 1])
        self.assertEqual(summary["leaderboard"][0]["label"], "gpt-5.4 / high")
        self.assertEqual(summary["leaderboard"][0]["correct_count"], 1)
        self.assertEqual(summary["leaderboard"][0]["total_count"], 1)
        self.assertTrue(summary["leaderboard"][0]["is_best"])
        self.assertIn(
            "gpt-5.5 / xhigh",
            [entry["label"] for entry in summary["leaderboard"]],
        )
        self.assertEqual(summary["best_combination"]["label"], "gpt-5.4 / high")
        self.assertEqual(summary["best_combination"]["stability_text"], "1/1")
        self.assertEqual(summary["best_combination"]["model"], "gpt-5.4")
        self.assertEqual(summary["best_combination"]["effort"], "high")
        self.assertEqual(len(summary["cards"]), 6)
        self.assertEqual(len(summary["ingress_groups"]), 1)

        ingress_group = summary["ingress_groups"][0]
        self.assertEqual(ingress_group["source_id"], "codex_local")
        self.assertEqual(ingress_group["source_title"], "Codex")
        self.assertEqual(ingress_group["recent_count"], 2)
        self.assertEqual(ingress_group["hit_rate_516"], 50)
        self.assertEqual(ingress_group["pass_rate"], 50)
        self.assertEqual(ingress_group["reasoning_tokens_total"], 946)
        high_window = next(
            item for item in ingress_group["model_candidates"] if item["label"] == "gpt-5.4 / high"
        )
        self.assertEqual(high_window["sparkline"], [430])

        first_card = next(item for item in summary["cards"] if item["label"] == "gpt-5.4 / high")
        self.assertEqual(first_card["hits_516"], 0)
        self.assertEqual(first_card["hit_rate_516"], 0)
        self.assertEqual(first_card["pass_rate"], 100)
        self.assertEqual(first_card["avg_reasoning_tokens"], 430)
        self.assertEqual(first_card["latest_reasoning_tokens"], 430)
        self.assertEqual(first_card["sparkline"], [430])

        idle_card = next(item for item in summary["cards"] if item["label"] == "gpt-5.4 / xhigh")
        self.assertEqual(idle_card["recent_count"], 0)
        self.assertEqual(idle_card["sparkline"], [])

    def test_build_dashboard_summary_filters_disabled_targets_from_leaderboard(self) -> None:
        config = AppConfig.default()
        config.model_ingress.connections[0].model_candidates[0].enabled = False
        config.model_ingress.connections[0].model_candidates[3].enabled = False
        history = [
            ScanResult(
                run_id="run-new",
                model="gpt-5.4",
                effort="high",
                phase="scan",
                question_id="q1",
                attempt_index=1,
                started_at="2026-07-02T14:00:00+08:00",
                elapsed_seconds=1.0,
                source_mode="live",
                answer_ok=True,
                answer_preview="21",
                input_tokens=100,
                output_tokens=20,
                reasoning_tokens=430,
                final_status="pass",
            ),
            ScanResult(
                run_id="run-new",
                model="gpt-5.4",
                effort="xhigh",
                phase="scan",
                question_id="q1",
                attempt_index=1,
                started_at="2026-07-02T14:00:01+08:00",
                elapsed_seconds=1.0,
                source_mode="live",
                answer_ok=False,
                answer_preview="20",
                input_tokens=100,
                output_tokens=20,
                reasoning_tokens=430,
                final_status="warn",
            ),
            ScanResult(
                run_id="run-new",
                model="gpt-5.5",
                effort="high",
                phase="scan",
                question_id="q1",
                attempt_index=1,
                started_at="2026-07-02T14:00:02+08:00",
                elapsed_seconds=1.0,
                source_mode="live",
                answer_ok=False,
                answer_preview="20",
                input_tokens=100,
                output_tokens=20,
                reasoning_tokens=430,
                final_status="warn",
            ),
            ScanResult(
                run_id="run-new",
                model="gpt-5.5",
                effort="xhigh",
                phase="scan",
                question_id="q1",
                attempt_index=1,
                started_at="2026-07-02T14:00:03+08:00",
                elapsed_seconds=1.0,
                source_mode="live",
                answer_ok=False,
                answer_preview="20",
                input_tokens=100,
                output_tokens=20,
                reasoning_tokens=430,
                final_status="warn",
            ),
        ]

        summary = build_dashboard_summary(history, config.model_ingress)

        self.assertEqual(len(summary["leaderboard"]), 4)
        self.assertEqual(len(summary["cards"]), 4)
        labels = [entry["label"] for entry in summary["leaderboard"]]
        self.assertNotIn("gpt-5.4 / medium", labels)
        self.assertNotIn("gpt-5.5 / medium", labels)

    def test_build_dashboard_summary_keeps_planned_progress_for_interrupted_run(self) -> None:
        config = AppConfig.default()
        history = [
            ScanResult(
                run_id="run-partial",
                model="gpt-5.4",
                effort="high",
                phase="scan",
                question_id="q1",
                attempt_index=1,
                started_at="2026-07-02T14:00:00+08:00",
                elapsed_seconds=1.0,
                source_mode="live",
                answer_ok=True,
                answer_preview="21",
                input_tokens=100,
                output_tokens=20,
                reasoning_tokens=430,
                final_status="pass",
            ),
            ScanResult(
                run_id="run-partial",
                model="gpt-5.4",
                effort="high",
                phase="scan",
                question_id="q2",
                attempt_index=2,
                started_at="2026-07-02T14:01:00+08:00",
                elapsed_seconds=1.0,
                source_mode="live",
                answer_ok=True,
                answer_preview="21",
                input_tokens=100,
                output_tokens=20,
                reasoning_tokens=430,
                final_status="pass",
            ),
        ]

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id="run-partial",
            active_run={
                "run_id": "run-partial",
                "planned_attempts": {"gpt-5.4 / high": 4},
            },
        )

        self.assertEqual(summary["leaderboard"][0]["score_text"], "2/2")
        self.assertEqual(summary["leaderboard"][0]["question_attempted"], 4)
        self.assertEqual(summary["best_combination"]["score_text"], "2/2")

    def test_build_dashboard_summary_deduplicates_same_question_within_run(self) -> None:
        config = AppConfig.default()
        history = [
            ScanResult(
                run_id="run-dup",
                model="gpt-5.4",
                effort="high",
                phase="scan",
                question_id="q1",
                attempt_index=1,
                started_at="2026-07-03T08:00:00+08:00",
                elapsed_seconds=1.0,
                source_mode="live",
                answer_ok=True,
                answer_preview="21",
                input_tokens=100,
                output_tokens=20,
                reasoning_tokens=430,
                final_status="pass",
            ),
            ScanResult(
                run_id="run-dup",
                model="gpt-5.4",
                effort="high",
                phase="scan",
                question_id="q2",
                attempt_index=2,
                started_at="2026-07-03T08:01:00+08:00",
                elapsed_seconds=1.0,
                source_mode="live",
                answer_ok=True,
                answer_preview="21",
                input_tokens=100,
                output_tokens=20,
                reasoning_tokens=430,
                final_status="pass",
            ),
            ScanResult(
                run_id="run-dup",
                model="gpt-5.4",
                effort="high",
                phase="scan",
                question_id="q2",
                attempt_index=2,
                started_at="2026-07-03T08:02:00+08:00",
                elapsed_seconds=1.0,
                source_mode="live",
                answer_ok=True,
                answer_preview="21",
                input_tokens=100,
                output_tokens=20,
                reasoning_tokens=430,
                final_status="pass",
            ),
            ScanResult(
                run_id="run-dup",
                model="gpt-5.4",
                effort="high",
                phase="scan",
                question_id="q3",
                attempt_index=3,
                started_at="2026-07-03T08:03:00+08:00",
                elapsed_seconds=1.0,
                source_mode="live",
                answer_ok=True,
                answer_preview="21",
                input_tokens=100,
                output_tokens=20,
                reasoning_tokens=430,
                final_status="pass",
            ),
        ]

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id="run-dup",
            active_run={
                "run_id": "run-dup",
                "planned_attempts": {"gpt-5.4 / high": 4},
            },
        )

        self.assertEqual(summary["leaderboard"][0]["score_text"], "3/3")
        self.assertEqual(summary["leaderboard"][0]["question_attempted"], 4)
        self.assertEqual(summary["best_combination"]["score_text"], "3/3")

    def test_build_dashboard_summary_aggregates_ingress_pass_rate_from_raw_counts(self) -> None:
        config = AppConfig.default()
        history = [
            ScanResult(
                run_id="run-pass-raw",
                candidate_id="codex-local-default:gpt-5.4:medium",
                model="gpt-5.4",
                effort="medium",
                phase="scan",
                question_id="q1",
                attempt_index=1,
                started_at="2026-07-03T09:00:00+08:00",
                elapsed_seconds=1.0,
                source_mode="live",
                answer_ok=True,
                answer_preview="21",
                input_tokens=100,
                output_tokens=20,
                reasoning_tokens=300,
                final_status="pass",
            ),
            ScanResult(
                run_id="run-pass-raw",
                candidate_id="codex-local-default:gpt-5.4:medium",
                model="gpt-5.4",
                effort="medium",
                phase="scan",
                question_id="q2",
                attempt_index=2,
                started_at="2026-07-03T09:01:00+08:00",
                elapsed_seconds=1.0,
                source_mode="live",
                answer_ok=True,
                answer_preview="21",
                input_tokens=100,
                output_tokens=20,
                reasoning_tokens=310,
                final_status="pass",
            ),
            ScanResult(
                run_id="run-pass-raw",
                candidate_id="codex-local-default:gpt-5.4:medium",
                model="gpt-5.4",
                effort="medium",
                phase="scan",
                question_id="q3",
                attempt_index=3,
                started_at="2026-07-03T09:02:00+08:00",
                elapsed_seconds=1.0,
                source_mode="live",
                answer_ok=False,
                answer_preview="20",
                input_tokens=100,
                output_tokens=20,
                reasoning_tokens=320,
                final_status="warn",
            ),
            ScanResult(
                run_id="run-pass-raw",
                candidate_id="codex-local-default:gpt-5.5:medium",
                model="gpt-5.5",
                effort="medium",
                phase="scan",
                question_id="q1",
                attempt_index=1,
                started_at="2026-07-03T09:03:00+08:00",
                elapsed_seconds=1.0,
                source_mode="live",
                answer_ok=True,
                answer_preview="21",
                input_tokens=100,
                output_tokens=20,
                reasoning_tokens=330,
                final_status="pass",
            ),
            ScanResult(
                run_id="run-pass-raw",
                candidate_id="codex-local-default:gpt-5.5:medium",
                model="gpt-5.5",
                effort="medium",
                phase="scan",
                question_id="q2",
                attempt_index=2,
                started_at="2026-07-03T09:04:00+08:00",
                elapsed_seconds=1.0,
                source_mode="live",
                answer_ok=False,
                answer_preview="20",
                input_tokens=100,
                output_tokens=20,
                reasoning_tokens=340,
                final_status="warn",
            ),
        ]

        summary = build_dashboard_summary(history, config.model_ingress)

        ingress_group = summary["ingress_groups"][0]
        self.assertEqual(ingress_group["recent_count"], 5)
        self.assertEqual(ingress_group["pass_rate"], 60)

    def test_build_dashboard_summary_supports_legacy_iterable_input_with_neutral_ingress_naming(self) -> None:
        legacy_targets = [
            TargetConfig(model="gpt-5.4", effort="high", enabled=True),
            TargetConfig(model="gpt-5.5", effort="high", enabled=False),
        ]
        history = [
            ScanResult(
                run_id="run-legacy",
                model="gpt-5.4",
                effort="high",
                phase="scan",
                question_id="q1",
                attempt_index=1,
                started_at="2026-07-03T10:00:00+08:00",
                elapsed_seconds=1.0,
                source_mode="live",
                answer_ok=True,
                answer_preview="21",
                input_tokens=100,
                output_tokens=20,
                reasoning_tokens=430,
                final_status="pass",
            )
        ]

        summary = build_dashboard_summary(history, legacy_targets)

        self.assertEqual(len(summary["cards"]), 1)
        ingress_group = summary["ingress_groups"][0]
        self.assertEqual(ingress_group["source_id"], "ingress_compat")
        self.assertEqual(ingress_group["source_title"], "兼容接入")
        self.assertEqual(ingress_group["model_candidates"][0]["label"], "gpt-5.4 / high")

    def test_best_combination_exposes_trust_copy_and_pack_fields(self) -> None:
        config = AppConfig.default()
        completed_at = datetime.now().astimezone()
        started_at = completed_at - timedelta(minutes=5)
        history = [
            *[
                _scan_result(
                    run_id="run-trust",
                    candidate_id="codex-local-default:gpt-5.4:xhigh",
                    model="gpt-5.4",
                    effort="xhigh",
                    question_id=f"q{index}",
                    answer_ok=True,
                    reasoning_tokens=300,
                )
                for index in range(1, 5)
            ],
            _scan_result(
                run_id="run-trust",
                candidate_id="codex-local-default:gpt-5.5:high",
                model="gpt-5.5",
                effort="high",
                question_id="q1",
                answer_ok=True,
            ),
            _scan_result(
                run_id="run-trust",
                candidate_id="codex-local-default:gpt-5.5:high",
                model="gpt-5.5",
                effort="high",
                question_id="q2",
                answer_ok=False,
            ),
            _scan_result(
                run_id="run-trust",
                candidate_id="codex-local-default:gpt-5.5:high",
                model="gpt-5.5",
                effort="high",
                question_id="q3",
                answer_ok=True,
            ),
            _scan_result(
                run_id="run-trust",
                candidate_id="codex-local-default:gpt-5.5:high",
                model="gpt-5.5",
                effort="high",
                question_id="q4",
                answer_ok=False,
            ),
        ]

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id="run-trust",
            active_run={
                "run_id": "run-trust",
                "run_metadata": {
                    "run_id": "run-trust",
                    "question_pack_id": "coding-fast",
                    "question_pack_version": DEFAULT_QUESTION_PACK_VERSION,
                    "started_at": started_at.isoformat(),
                    "completed_at": completed_at.isoformat(),
                    "candidate_count": 6,
                    "question_count": 4,
                    "status": "completed",
                },
                "planned_attempts": {
                    "gpt-5.4 / xhigh": 4,
                    "gpt-5.5 / high": 4,
                },
            },
            current_default_candidate_id="codex-local-default:gpt-5.4:xhigh",
        )

        best = summary["best_combination"]
        self.assertEqual(best["label"], "gpt-5.4 / xhigh")
        self.assertEqual(best["recommendation_basis"], "overall_score_lead")
        self.assertEqual(best["confidence_label"], "高")
        self.assertEqual(best["confidence_reason"], "总分领先第二名 50 分")
        self.assertEqual(best["runner_up_gap_text"], "总分领先第二名 50 分")
        self.assertEqual(best["question_pack_version"], DEFAULT_QUESTION_PACK_VERSION)
        self.assertEqual(best["question_pack_display_text"], DEFAULT_QUESTION_PACK_VERSION)
        self.assertEqual(best["question_pack_context_text"], "当前 run")
        self.assertEqual(best["display_label"], "gpt-5.4 / xhigh")
        self.assertEqual(best["copy_value"], "gpt-5.4 / xhigh")
        self.assertEqual(best["candidate_id"], "codex-local-default:gpt-5.4:xhigh")
        self.assertEqual(best["recommendation_outcome"], "keep")
        self.assertEqual(best["evidence_state"], "fresh")
        self.assertEqual(best["decision_state"], "keep")
        self.assertEqual(best["decision_title"], "保持当前模型")
        self.assertEqual(best["decision_action_label"], "查看证据")
        self.assertEqual(best["short_display_name"], "5.4")
        self.assertEqual(best["effort_label"], "xhigh")
        self.assertEqual(best["recommendation_created_at"], completed_at.isoformat())
        self.assertEqual(best["run_completed_at"], completed_at.isoformat())
        self.assertEqual(
            best["stale_at"],
            (completed_at + timedelta(hours=24)).isoformat(),
        )
        self.assertEqual(
            best["expires_at"],
            (completed_at + timedelta(hours=72)).isoformat(),
        )
        self.assertEqual(summary["run_metadata"]["status"], "completed")

    def test_best_combination_uses_model_variant_short_name(self) -> None:
        config = AppConfig.default()
        candidate_id = "codex-local-default:gpt-5.6-sol:high"
        candidate = next(
            item
            for connection in config.model_ingress.connections
            for item in connection.model_candidates
            if item.id == candidate_id
        )
        candidate.enabled = True
        completed_at = "2026-07-13T10:30:00+08:00"
        metadata = {
            **_run_metadata("run-sol", DEFAULT_QUESTION_PACK_VERSION),
            "completed_at": completed_at,
        }
        history = [
            _scan_result(
                run_id="run-sol",
                candidate_id=candidate_id,
                model="gpt-5.6-sol",
                effort="high",
                question_id=f"q{index}",
                answer_ok=True,
            )
            for index in range(1, 5)
        ]

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id="run-sol",
            run_metadata=metadata,
        )

        best = summary["best_combination"]
        self.assertEqual(best["short_display_name"], "5.6 Sol")
        self.assertEqual(best["effort_label"], "high")

    def test_best_combination_does_not_fake_freshness_without_a_completion_time(self) -> None:
        config = AppConfig.default()
        metadata = {
            **_run_metadata("run-missing-time", DEFAULT_QUESTION_PACK_VERSION),
            "completed_at": None,
        }
        history = [
            _scan_result(
                run_id="run-missing-time",
                candidate_id="codex-local-default:gpt-5.4:high",
                model="gpt-5.4",
                effort="high",
                question_id=f"q{index}",
                answer_ok=True,
            )
            for index in range(1, 5)
        ]

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id="run-missing-time",
            run_metadata=metadata,
        )

        best = summary["best_combination"]
        self.assertIsNone(best["recommendation_created_at"])
        self.assertIsNone(best["run_completed_at"])
        self.assertIsNone(best["stale_at"])
        self.assertIsNone(best["expires_at"])

    def test_confidence_is_low_for_tie_single_candidate_and_partial_run(self) -> None:
        config = AppConfig.default()
        tied_history = []
        for model, effort in [("gpt-5.4", "high"), ("gpt-5.5", "high")]:
            for index in range(1, 5):
                tied_history.append(
                    _scan_result(
                        run_id="run-tie",
                        model=model,
                        effort=effort,
                        question_id=f"q{index}",
                        answer_ok=True,
                    )
                )

        tied = build_dashboard_summary(
            tied_history,
            config.model_ingress,
            current_run_id="run-tie",
            active_run={
                "run_id": "run-tie",
                "run_metadata": {
                    "question_count": 4,
                    "question_pack_version": "coding-fast-v1",
                    "status": "completed",
                },
                "planned_attempts": {
                    "gpt-5.4 / high": 4,
                    "gpt-5.5 / high": 4,
                },
            },
        )
        self.assertEqual(tied["best_combination"]["confidence_label"], "低")
        self.assertIn("同分", tied["best_combination"]["confidence_reason"])
        self.assertEqual(tied["best_combination"]["recommendation_outcome"], "recommend")
        self.assertEqual(tied["best_combination"]["decision_state"], "recommend")
        self.assertEqual(tied["best_combination"]["decision_title"], "推荐当前最佳模型")
        self.assertEqual(tied["best_combination"]["decision_action_label"], "查看推荐依据")

        single = build_dashboard_summary(
            [
                _scan_result(
                    run_id="run-single",
                    model="gpt-5.4",
                    effort="high",
                    question_id=f"q{index}",
                    answer_ok=True,
                )
                for index in range(1, 5)
            ],
            [TargetConfig(model="gpt-5.4", effort="high", enabled=True)],
            current_run_id="run-single",
            active_run={
                "run_id": "run-single",
                "run_metadata": {
                    "question_count": 4,
                    "question_pack_version": "coding-fast-v1",
                    "status": "completed",
                },
                "planned_attempts": {"gpt-5.4 / high": 4},
            },
        )
        self.assertEqual(single["best_combination"]["confidence_label"], "低")
        self.assertIn("只有一个有效候选模型", single["best_combination"]["confidence_reason"])

        partial = build_dashboard_summary(
            [
                _scan_result(
                    run_id="run-partial-trust",
                    model="gpt-5.4",
                    effort="high",
                    question_id=f"q{index}",
                    answer_ok=True,
                )
                for index in range(1, 4)
            ],
            [TargetConfig(model="gpt-5.4", effort="high", enabled=True)],
            current_run_id="run-partial-trust",
            active_run={
                "run_id": "run-partial-trust",
                "run_metadata": {
                    "question_count": 4,
                    "question_pack_version": "coding-fast-v1",
                    "status": "partial",
                },
                "planned_attempts": {"gpt-5.4 / high": 4},
            },
        )
        self.assertEqual(partial["cards"][0]["question_completed"], 3)
        self.assertEqual(partial["cards"][0]["question_attempted"], 4)
        self.assertEqual(partial["best_combination"]["confidence_label"], "低")
        self.assertIn("未完成", partial["best_combination"]["confidence_reason"])

    def test_runner_up_gap_text_waits_for_other_models_during_partial_run(self) -> None:
        config = AppConfig.default()
        partial = build_dashboard_summary(
            [
                _scan_result(
                    run_id="run-partial-gap",
                    candidate_id="codex-local-default:gpt-5.4:medium",
                    model="gpt-5.4",
                    effort="medium",
                    question_id=f"q{index}",
                    answer_ok=True,
                )
                for index in range(1, 5)
            ]
            + [
                _scan_result(
                    run_id="run-partial-gap",
                    candidate_id="codex-local-default:gpt-5.4:high",
                    model="gpt-5.4",
                    effort="high",
                    question_id="q1",
                    answer_ok=False,
                )
            ],
            config.model_ingress,
            current_run_id="run-partial-gap",
            active_run={
                "run_id": "run-partial-gap",
                "run_metadata": {
                    "question_count": 4,
                    "question_pack_version": DEFAULT_QUESTION_PACK_VERSION,
                    "status": "running",
                },
                "planned_attempts": {
                    "gpt-5.4 / medium": 4,
                    "gpt-5.4 / high": 4,
                    "gpt-5.4 / xhigh": 4,
                    "gpt-5.5 / medium": 4,
                    "gpt-5.5 / high": 4,
                    "gpt-5.5 / xhigh": 4,
                },
            },
        )

        best = partial["best_combination"]
        self.assertEqual(best["confidence_reason"], "当前 run 未完成")
        self.assertEqual(best["runner_up_gap_text"], "当前只有一个有效候选模型")

    def test_confidence_is_low_when_only_leading_by_one_question(self) -> None:
        config = AppConfig.default()
        history = [
            *[
                _scan_result(
                    run_id="run-gap-one",
                    model="gpt-5.4",
                    effort="xhigh",
                    question_id=f"q{index}",
                    answer_ok=True,
                )
                for index in range(1, 5)
            ],
            *[
                _scan_result(
                    run_id="run-gap-one",
                    model="gpt-5.5",
                    effort="high",
                    question_id=f"q{index}",
                    answer_ok=index < 4,
                )
                for index in range(1, 5)
            ],
        ]

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id="run-gap-one",
            active_run={
                "run_id": "run-gap-one",
                "run_metadata": {
                    "question_count": 4,
                    "question_pack_version": "coding-fast-v1",
                    "status": "completed",
                },
                "planned_attempts": {
                    "gpt-5.4 / xhigh": 4,
                    "gpt-5.5 / high": 4,
                },
            },
            current_default_candidate_id="codex-local-default:gpt-5.4:xhigh",
        )

        self.assertEqual(summary["best_combination"]["confidence_label"], "高")
        self.assertEqual(summary["best_combination"]["recommendation_outcome"], "keep")
        self.assertEqual(summary["best_combination"]["decision_state"], "keep")
        self.assertEqual(summary["best_combination"]["decision_title"], "保持当前模型")
        self.assertEqual(summary["best_combination"]["decision_action_label"], "查看证据")
        self.assertEqual(
            summary["best_combination"]["confidence_reason"],
            "总分领先第二名 25 分",
        )

    def test_confidence_explains_missing_question_pack_version_on_current_run(self) -> None:
        config = AppConfig.default()
        history = [
            *[
                _scan_result(
                    run_id="run-missing-pack",
                    model="gpt-5.4",
                    effort="xhigh",
                    question_id=f"q{index}",
                    answer_ok=True,
                )
                for index in range(1, 5)
            ],
            *[
                _scan_result(
                    run_id="run-missing-pack",
                    model="gpt-5.5",
                    effort="high",
                    question_id=f"q{index}",
                    answer_ok=index in {1, 2},
                )
                for index in range(1, 5)
            ],
        ]

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id="run-missing-pack",
            active_run={
                "run_id": "run-missing-pack",
                "run_metadata": {
                    "question_count": 4,
                    "question_pack_version": "unknown",
                    "status": "completed",
                },
                "planned_attempts": {
                    "gpt-5.4 / xhigh": 4,
                    "gpt-5.5 / high": 4,
                },
            },
        )

        best = summary["best_combination"]
        self.assertEqual(best["confidence_label"], "低")
        self.assertEqual(
            best["confidence_reason"],
            "当前 run 缺少题包版本，结果可参考但不完整",
        )
        self.assertEqual(best["question_pack_display_text"], "题包版本缺失")
        self.assertEqual(best["question_pack_context_text"], "当前 run")

    def test_confidence_explains_legacy_question_pack_version_missing(self) -> None:
        config = AppConfig.default()
        history = [
            *[
                _scan_result(
                    run_id="run-legacy-pack",
                    model="gpt-5.4",
                    effort="xhigh",
                    question_id=f"q{index}",
                    answer_ok=True,
                )
                for index in range(1, 5)
            ],
            *[
                _scan_result(
                    run_id="run-legacy-pack",
                    model="gpt-5.5",
                    effort="high",
                    question_id=f"q{index}",
                    answer_ok=index in {1, 2},
                )
                for index in range(1, 5)
            ],
        ]

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id="run-legacy-pack",
            active_run={
                "run_id": "run-legacy-pack",
                "run_metadata": {
                    "question_count": 4,
                    "question_pack_version": "unknown",
                    "status": "legacy",
                },
                "planned_attempts": {
                    "gpt-5.4 / xhigh": 4,
                    "gpt-5.5 / high": 4,
                },
            },
        )

        best = summary["best_combination"]
        self.assertEqual(best["confidence_label"], "低")
        self.assertEqual(
            best["confidence_reason"],
            "当前结果来自旧数据，题包版本未记录",
        )
        self.assertEqual(best["question_pack_display_text"], "未记录（旧数据）")
        self.assertEqual(best["question_pack_context_text"], "旧数据")

    def test_current_run_winner_beats_historically_stable_loser(self) -> None:
        config = AppConfig.default()
        history = [
            *[
                _scan_result(
                    run_id=f"run-old-{run_index}",
                    model="gpt-5.4",
                    effort="high",
                    question_id=f"q{question_index}",
                    answer_ok=True,
                )
                for run_index in range(1, 4)
                for question_index in range(1, 5)
            ],
            *[
                _scan_result(
                    run_id="run-current",
                    model="gpt-5.4",
                    effort="high",
                    question_id=f"q{index}",
                    answer_ok=index < 4,
                )
                for index in range(1, 5)
            ],
            *[
                _scan_result(
                    run_id="run-current",
                    model="gpt-5.5",
                    effort="xhigh",
                    question_id=f"q{index}",
                    answer_ok=True,
                )
                for index in range(1, 5)
            ],
        ]

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id="run-current",
            active_run={
                "run_id": "run-current",
                "run_metadata": {"question_count": 4, "status": "completed"},
                "planned_attempts": {
                    "gpt-5.4 / high": 4,
                    "gpt-5.5 / xhigh": 4,
                },
            },
        )

        self.assertEqual(summary["best_combination"]["label"], "gpt-5.5 / xhigh")

    def test_stale_confidence_uses_scan_interval_threshold(self) -> None:
        config = AppConfig.default()
        completed_at = (datetime.now().astimezone() - timedelta(days=2)).isoformat()
        history = [
            *[
                _scan_result(
                    run_id="run-stale",
                    model="gpt-5.4",
                    effort="xhigh",
                    question_id=f"q{index}",
                    answer_ok=True,
                )
                for index in range(1, 5)
            ],
            *[
                _scan_result(
                    run_id="run-stale",
                    model="gpt-5.5",
                    effort="high",
                    question_id=f"q{index}",
                    answer_ok=index in {1, 2},
                )
                for index in range(1, 5)
            ],
        ]
        active_run = {
            "run_id": "run-stale",
            "run_metadata": {
                "question_count": 4,
                "question_pack_version": "coding-fast-v1",
                "completed_at": completed_at,
                "status": "completed",
            },
            "planned_attempts": {
                "gpt-5.4 / xhigh": 4,
                "gpt-5.5 / high": 4,
            },
        }

        stale = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id="run-stale",
            active_run=active_run,
            scan_interval_seconds=600,
        )
        fresh_by_interval = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id="run-stale",
            active_run=active_run,
            scan_interval_seconds=300_000,
        )

        self.assertEqual(stale["best_combination"]["confidence_label"], "低")
        self.assertIn("过时", stale["best_combination"]["confidence_reason"])
        self.assertEqual(fresh_by_interval["best_combination"]["confidence_label"], "高")

    def test_equal_semantic_score_sums_all_five_questions_and_exposes_q5(self) -> None:
        config = AppConfig.default()
        history: list[ScanResult] = []
        for effort, scores, q5_score in (
            ("medium", (10, 8, 9, 10), 6),
            ("high", (10, 10, 10, 10), 5),
        ):
            for index, semantic_score in enumerate(scores, start=1):
                history.append(
                    _scan_result(
                        run_id="run-equal",
                        model="gpt-5.4",
                        effort=effort,
                        question_id=f"q{index}",
                        answer_ok=semantic_score == 10,
                        final_status="pass" if semantic_score == 10 else "warn",
                        scorer_diagnostics={
                            "semantic_passed": semantic_score,
                            "semantic_total": 10,
                            "score_details": [
                                {
                                    "id": "component",
                                    "label": "得分点",
                                    "points": semantic_score,
                                    "max_points": 10,
                                    "passed": semantic_score == 10,
                                }
                            ],
                        },
                    )
                )
            history.append(
                _scan_result(
                    run_id="run-equal",
                    model="gpt-5.4",
                    effort=effort,
                    phase="scan",
                    question_id="05_cache_regression_test_design",
                    capability_label="测试设计",
                    answer_ok=q5_score == 10,
                    final_status="pass" if q5_score == 10 else "warn",
                    grader_kind="mutation_test_design",
                    scorer_diagnostics={
                        "semantic_passed": q5_score,
                        "semantic_total": 10,
                    },
                )
            )

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id="run-equal",
            active_run={
                "run_id": "run-equal",
                "run_metadata": {
                    "run_id": "run-equal",
                    "question_count": 5,
                    "question_pack_id": "coding-fast",
                    "question_pack_version": DEFAULT_QUESTION_PACK_VERSION,
                    "status": "completed",
                    "scoring_mode": "semantic_q1_q5_equal_v2",
                    "question_ids": [
                        "q1",
                        "q2",
                        "q3",
                        "q4",
                        "05_cache_regression_test_design",
                    ],
                },
                "planned_attempts": {
                    "gpt-5.4 / medium": 5,
                    "gpt-5.4 / high": 5,
                },
            },
        )

        leaderboard = summary["leaderboard"]
        self.assertEqual(
            [(row["label"], row["overall_score"]) for row in leaderboard[:2]],
            [("gpt-5.4 / high", 90), ("gpt-5.4 / medium", 86)],
        )
        self.assertEqual(leaderboard[0]["score_text"], "45/50")
        self.assertEqual(len(leaderboard[0]["question_results"]), 5)
        self.assertEqual(
            [item["semantic_total"] for item in leaderboard[0]["question_results"]],
            [10, 10, 10, 10, 10],
        )

    def test_equal_semantic_timeout_remains_comparable_and_visible_after_run_finishes(self) -> None:
        config = AppConfig.default()
        run_id = "run-equal-timeout"
        timeout_candidate_id = "codex-local-default:gpt-5.4:high"
        clean_candidate_id = "codex-local-default:gpt-5.4:xhigh"
        history: list[ScanResult] = []

        for candidate_id, effort, scores, timeout_question in (
            (timeout_candidate_id, "high", (9, 0, 10, 9, 6), "q2"),
            (clean_candidate_id, "xhigh", (8, 8, 10, 7, 5), None),
        ):
            for index, semantic_score in enumerate(scores[:4], start=1):
                question_id = f"q{index}"
                is_timeout = question_id == timeout_question
                history.append(
                    _scan_result(
                        run_id=run_id,
                        candidate_id=candidate_id,
                        model="gpt-5.4",
                        effort=effort,
                        question_id=question_id,
                        answer_ok=semantic_score == 10,
                        final_status="warn" if is_timeout else "pass",
                        flags=["timeout"] if is_timeout else [],
                        error_message=(
                            "codex exec timed out after 1200s" if is_timeout else None
                        ),
                        scorer_diagnostics={
                            "status": "timeout" if is_timeout else "semantic_scored",
                            "semantic_passed": semantic_score,
                            "semantic_total": 10,
                        },
                    )
                )
            history.append(
                _scan_result(
                    run_id=run_id,
                    candidate_id=candidate_id,
                    model="gpt-5.4",
                    effort=effort,
                    phase="scan",
                    question_id="q5",
                    capability_label="测试设计",
                    answer_ok=scores[4] == 10,
                    final_status="pass",
                    grader_kind="mutation_test_design",
                    scorer_diagnostics={
                        "semantic_passed": scores[4],
                        "semantic_total": 10,
                    },
                )
            )

        metadata = {
            "run_id": run_id,
            "question_pack_id": "coding-fast",
            "question_pack_version": DEFAULT_QUESTION_PACK_VERSION,
            "started_at": "2026-07-20T09:54:29+08:00",
            "completed_at": "2026-07-20T12:41:20+08:00",
            "candidate_count": 2,
            "question_count": 5,
            "status": "degraded",
            "selection_mode": "regular",
            "requested_candidate_ids": [timeout_candidate_id, clean_candidate_id],
            "regular_candidate_ids": [timeout_candidate_id, clean_candidate_id],
            "is_complete_regular_round": False,
            "scoring_mode": "semantic_q1_q5_equal_v2",
            "question_ids": ["q1", "q2", "q3", "q4", "q5"],
        }

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id=run_id,
            run_metadata=metadata,
            run_metadata_by_id={run_id: metadata},
            current_question_pack_id="coding-fast",
            current_question_pack_version=DEFAULT_QUESTION_PACK_VERSION,
        )
        rows = {
            row["candidate_id"]: row
            for row in summary["leaderboard"]
        }
        timeout_row = rows[timeout_candidate_id]
        clean_row = rows[clean_candidate_id]

        self.assertEqual(timeout_row["overall_score"], 68)
        self.assertEqual(len(timeout_row["question_results"]), 5)
        self.assertEqual(timeout_row["question_results"][1]["semantic_score"], 0)
        self.assertTrue(timeout_row["is_current_pack_comparable"])
        self.assertFalse(timeout_row["is_current_run_eligible"])
        self.assertEqual(timeout_row["repairable_question_ids"], ["q2"])
        self.assertTrue(clean_row["is_current_run_eligible"])

    def test_previous_pack_scores_do_not_leak_into_current_overview(self) -> None:
        config = AppConfig.default()
        run_id = "run-previous-pack"
        candidate_id = "codex-local-default:gpt-5.4:high"
        newly_selected_candidate_id = "codex-local-default:gpt-5.4:xhigh"
        selected_candidate_ids = {candidate_id, newly_selected_candidate_id}
        for connection in config.model_ingress.connections:
            for candidate in connection.model_candidates:
                candidate.enabled = candidate.id in selected_candidate_ids
        question_ids = [
            "01_session_bundle_repair",
            "02_code_counterexample_maxgap",
            "03_ci_optimality_certificate",
            "04_transaction_regression_design",
            "05_cache_regression_test_design",
        ]
        history = [
            _scan_result(
                run_id=run_id,
                candidate_id=candidate_id,
                model="gpt-5.4",
                effort="high",
                question_id=question_id,
                answer_ok=False,
                scorer_diagnostics={
                    "semantic_passed": 8,
                    "semantic_total": 10,
                },
            )
            for question_id in question_ids
        ]
        metadata = {
            "run_id": run_id,
            "question_pack_id": "coding-fast",
            "question_pack_version": "coding-fast-v3.10",
            "started_at": "2026-07-22T06:30:01+08:00",
            "completed_at": "2026-07-22T09:04:18+08:00",
            "candidate_count": 1,
            "question_count": 5,
            "status": "completed",
            "selection_mode": "regular",
            "requested_candidate_ids": [candidate_id],
            "regular_candidate_ids": [candidate_id],
            "is_complete_regular_round": True,
            "scoring_mode": "semantic_q1_q5_equal_v2",
            "question_ids": question_ids,
        }

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id=run_id,
            run_metadata=metadata,
            run_metadata_by_id={run_id: metadata},
            current_question_pack_id="coding-fast",
            current_question_pack_version=DEFAULT_QUESTION_PACK_VERSION,
        )
        self.assertEqual(
            {
                item["candidate_id"]
                for item in summary["leaderboard"]
            },
            selected_candidate_ids,
        )
        row = next(
            item for item in summary["leaderboard"]
            if item["candidate_id"] == candidate_id
        )

        self.assertIsNone(row["overall_score"])
        self.assertIsNone(row["overall_score_text"])
        self.assertEqual(row["question_results"], [])
        self.assertFalse(row["is_current_pack_comparable"])
        self.assertFalse(row["is_current_run_eligible"])
        self.assertIsNone(summary["best_combination"])

    def test_scoped_repair_keeps_non_target_scores_visible(self) -> None:
        config = AppConfig.default()
        run_id = "run-scoped-repair"
        repair_candidate_id = "codex-local-default:gpt-5.4:high"
        settled_candidate_id = "codex-local-default:gpt-5.4:xhigh"
        history: list[ScanResult] = []

        for candidate_id, effort, scores, timeout_question in (
            (repair_candidate_id, "high", (9, 0, 10, 9, 6), "q2"),
            (settled_candidate_id, "xhigh", (8, 8, 10, 7, 5), None),
        ):
            for index, semantic_score in enumerate(scores[:4], start=1):
                question_id = f"q{index}"
                is_timeout = question_id == timeout_question
                history.append(
                    _scan_result(
                        run_id=run_id,
                        candidate_id=candidate_id,
                        model="gpt-5.4",
                        effort=effort,
                        question_id=question_id,
                        answer_ok=semantic_score == 10,
                        final_status="warn" if is_timeout else "pass",
                        flags=["timeout"] if is_timeout else [],
                        error_message=(
                            "codex exec timed out after 1200s" if is_timeout else None
                        ),
                        scorer_diagnostics={
                            "status": "timeout" if is_timeout else "semantic_scored",
                            "semantic_passed": semantic_score,
                            "semantic_total": 10,
                        },
                    )
                )
            history.append(
                _scan_result(
                    run_id=run_id,
                    candidate_id=candidate_id,
                    model="gpt-5.4",
                    effort=effort,
                    phase="scan",
                    question_id="q5",
                    capability_label="测试设计",
                    answer_ok=scores[4] == 10,
                    final_status="pass",
                    grader_kind="mutation_test_design",
                    scorer_diagnostics={
                        "semantic_passed": scores[4],
                        "semantic_total": 10,
                    },
                )
            )

        metadata = {
            "run_id": run_id,
            "question_pack_id": "coding-fast",
            "question_pack_version": DEFAULT_QUESTION_PACK_VERSION,
            "started_at": "2026-07-20T09:54:29+08:00",
            "completed_at": None,
            "candidate_count": 2,
            "question_count": 5,
            "status": "running",
            "selection_mode": "regular",
            "requested_candidate_ids": [repair_candidate_id, settled_candidate_id],
            "regular_candidate_ids": [repair_candidate_id, settled_candidate_id],
            "is_complete_regular_round": False,
            "scoring_mode": "semantic_q1_q5_equal_v2",
            "question_ids": ["q1", "q2", "q3", "q4", "q5"],
        }
        active_run = {
            "run_id": run_id,
            "repair_run_id": run_id,
            "repair_candidate_id": repair_candidate_id,
            "run_metadata": metadata,
            "planned_attempts_by_candidate": {
                repair_candidate_id: 5,
                settled_candidate_id: 5,
            },
            "entries": [
                {
                    "candidate_id": repair_candidate_id,
                    "status": "running",
                    "phase": "repair",
                },
                {
                    "candidate_id": settled_candidate_id,
                    "status": "done",
                    "phase": "scan",
                },
            ],
        }

        summary = build_dashboard_summary(
            history,
            config.model_ingress,
            current_run_id=run_id,
            active_run=active_run,
            run_metadata=metadata,
            run_metadata_by_id={run_id: metadata},
            current_question_pack_id="coding-fast",
            current_question_pack_version=DEFAULT_QUESTION_PACK_VERSION,
        )
        rows = {
            row["candidate_id"]: row
            for row in summary["leaderboard"]
        }

        self.assertFalse(rows[repair_candidate_id]["is_current_pack_comparable"])
        self.assertTrue(rows[settled_candidate_id]["is_current_pack_comparable"])
        self.assertTrue(rows[settled_candidate_id]["is_current_run_eligible"])
        self.assertEqual(rows[settled_candidate_id]["overall_score"], 76)
        self.assertEqual(len(rows[settled_candidate_id]["question_results"]), 5)


if __name__ == "__main__":
    unittest.main()
