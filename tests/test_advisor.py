from __future__ import annotations

from datetime import datetime, timezone
import unittest

from scanner.advisor import build_advisor_decision


NOW = datetime(2026, 7, 24, 9, 0, tzinfo=timezone.utc)


def _question_results(scores: tuple[int, ...], *, hard_failure: bool = False) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index, score in enumerate(scores, start=1):
        rows.append(
            {
                "question_id": f"q{index}",
                "status": "timeout" if hard_failure and index == 2 else "pass",
                "semantic_score": score,
                "semantic_total": 20,
            }
        )
    return rows


def _row(
    candidate_id: str,
    *,
    model: str,
    effort: str,
    score: int,
    question_scores: tuple[int, ...],
    elapsed_seconds: float,
    cost_usd: float | None,
    completed_at: str = "2026-07-24T08:00:00Z",
    hard_failure: bool = False,
    comparable: bool = True,
    source_mode: str = "local",
    source_id: str = "codex_local",
    route_identity_status: str = "not_required",
) -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "model": model,
        "effort": effort,
        "overall_score": score,
        "question_count": 5,
        "question_completed": 5,
        "question_pack_version": "coding-fast-v4.10",
        "question_results": _question_results(
            question_scores,
            hard_failure=hard_failure,
        ),
        "elapsed_seconds": elapsed_seconds,
        "estimated_cost_usd": cost_usd,
        "cost_coverage": "complete" if cost_usd is not None else "unknown",
        "latest_valid_at": completed_at,
        "is_current_pack_comparable": comparable,
        "source_mode": source_mode,
        "source_id": source_id,
        "route_identity_status": route_identity_status,
    }


def _aggregate(
    *,
    model: str,
    effort: str,
    completed: int,
    duration_ms: int,
    input_tokens: int = 100_000,
    cached_input_tokens: int = 40_000,
    output_tokens: int = 20_000,
    attribution_confidence: float = 1.0,
) -> dict[str, object]:
    return {
        "model_configuration_id": f"codex:openai:{model}:{effort}",
        "provider_id": "openai",
        "raw_model_id": model,
        "reasoning_effort": effort,
        "completed_work_units": completed,
        "median_active_duration_ms": duration_ms,
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "cache_write_input_tokens": 0,
        "output_tokens": output_tokens,
        "reasoning_tokens": 5_000,
        "failure_count": 0,
        "attribution_confidence": attribution_confidence,
    }


def _snapshot(
    *,
    current_id: str | None = "current",
    detection_status: str = "active_single",
    rows: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "config": {
            "recommendation": {
                "effective_current_candidate_id": current_id,
                "current_model_source": "terminal_session",
                "current_model_detection_status": detection_status,
            }
        },
        "question_pack": {
            "id": "coding-fast",
            "version": "coding-fast-v4.10",
            "question_count": 5,
        },
        "dashboard": {"leaderboard": rows or []},
    }


def _insights(
    aggregates: list[dict[str, object]],
    *,
    coverage_complete: bool = True,
    used_percent: float | None = None,
    quota_burn: dict[str, object] | None = None,
) -> dict[str, object]:
    windows = []
    if used_percent is not None:
        windows.append(
            {
                "window_id": "primary",
                "used_percent": used_percent,
                "resets_at": "2026-07-24T10:30:00Z",
            }
        )
    return {
        "schema_version": 1,
        "account": {
            "quota_status": "available" if windows else "not_applicable",
            "quota_windows": windows,
        },
        "workload": {
            "status": "available",
            "coverage_complete": coverage_complete,
            "aggregates": aggregates,
        },
        "quota_burn": quota_burn
        or {
            "schema_version": 1,
            "status": "collecting",
            "aggregates": [],
        },
    }


def _quota_aggregate(
    *,
    model: str,
    effort: str,
    median: float,
    p25: float,
    p75: float,
    window_id: str = "primary",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "model_configuration_id": f"codex:openai:{model}:{effort}",
        "provider_id": "openai",
        "raw_model_id": model,
        "reasoning_effort": effort,
        "window_id": window_id,
        "window_label": "5h",
        "window_seconds": 18_000,
        "attributed_interval_count": 10,
        "attributed_work_units": 30,
        "quota_per_work_unit_percent": {
            "median": median,
            "p25": p25,
            "p75": p75,
        },
        "measurement_resolution_percent": 1.0,
        "confidence": 0.8,
        "usable_for_recommendation": True,
    }


class AdvisorDecisionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.current = _row(
            "current",
            model="gpt-5.6-terra",
            effort="high",
            score=82,
            question_scores=(16, 16, 17, 16, 17),
            elapsed_seconds=600,
            cost_usd=1.0,
        )
        self.candidate = _row(
            "candidate",
            model="gpt-5.6-sol",
            effort="xhigh",
            score=84,
            question_scores=(17, 16, 17, 17, 17),
            elapsed_seconds=450,
            cost_usd=0.6,
        )

    def _decision(
        self,
        *,
        current: dict[str, object] | None = None,
        candidate: dict[str, object] | None = None,
        completed: int = 30,
        coverage_complete: bool = True,
        current_id: str | None = "current",
        detection_status: str = "active_single",
        used_percent: float | None = None,
        include_candidate: bool = True,
        include_candidate_workload: bool = False,
        quota_burn: dict[str, object] | None = None,
    ) -> dict[str, object]:
        current_row = current if current is not None else self.current
        candidate_row = candidate if candidate is not None else self.candidate
        rows = [current_row]
        if include_candidate:
            rows.append(candidate_row)
        aggregates = [
            _aggregate(
                model="gpt-5.6-terra",
                effort="high",
                completed=completed,
                duration_ms=100_000,
            )
        ]
        if include_candidate_workload:
            aggregates.append(
                _aggregate(
                    model=str(candidate_row["model"]),
                    effort=str(candidate_row["effort"]),
                    completed=completed,
                    duration_ms=50_000,
                    input_tokens=10_000,
                    cached_input_tokens=4_000,
                    output_tokens=2_000,
                )
            )
        return build_advisor_decision(
            _snapshot(
                current_id=current_id,
                detection_status=detection_status,
                rows=rows,
            ),
            _insights(
                aggregates,
                coverage_complete=coverage_complete,
                used_percent=used_percent,
                quota_burn=quota_burn,
            ),
            now=NOW,
        )

    def test_unmapped_current_identity_short_circuits(self) -> None:
        decision = self._decision(current_id=None, detection_status="unmapped")

        self.assertEqual(decision["decision"], "unmapped")
        self.assertIsNone(decision["candidate_model_configuration_id"])
        self.assertEqual(decision["short_circuit_reason"], "current_identity_unmapped")

    def test_stale_current_evaluation_requires_same_protocol_comparison(self) -> None:
        stale = dict(self.current)
        stale["latest_valid_at"] = "2026-07-01T08:00:00Z"

        decision = self._decision(current=stale)

        self.assertEqual(decision["decision"], "compare_first")
        self.assertEqual(decision["short_circuit_reason"], "current_evaluation_not_fresh")

    def test_quality_regression_blocks_switch_even_when_benefit_is_large(self) -> None:
        weak = _row(
            "candidate",
            model="gpt-5.6-sol",
            effort="xhigh",
            score=78,
            question_scores=(16, 15, 16, 15, 16),
            elapsed_seconds=100,
            cost_usd=0.1,
        )

        decision = self._decision(candidate=weak)

        self.assertEqual(decision["decision"], "keep")
        self.assertEqual(decision["short_circuit_reason"], "no_candidate_passed_guard")

    def test_critical_question_regression_and_hard_failure_block_switch(self) -> None:
        for candidate in (
            _row(
                "candidate",
                model="gpt-5.6-sol",
                effort="xhigh",
                score=82,
                question_scores=(10, 18, 18, 18, 18),
                elapsed_seconds=100,
                cost_usd=0.1,
            ),
            _row(
                "candidate",
                model="gpt-5.6-sol",
                effort="xhigh",
                score=84,
                question_scores=(17, 16, 17, 17, 17),
                elapsed_seconds=100,
                cost_usd=0.1,
                hard_failure=True,
            ),
        ):
            with self.subTest(candidate=candidate):
                decision = self._decision(candidate=candidate)
                self.assertEqual(decision["decision"], "keep")

    def test_quality_guard_accepts_exact_total_and_critical_boundaries(self) -> None:
        boundary = _row(
            "candidate",
            model="gpt-5.6-sol",
            effort="xhigh",
            score=79,
            question_scores=(11, 18, 17, 16, 17),
            elapsed_seconds=450,
            cost_usd=0.6,
        )

        decision = self._decision(candidate=boundary)

        self.assertEqual(decision["decision"], "trial_switch")
        self.assertTrue(decision["quality"]["guard_passed"])
        self.assertEqual(decision["quality"]["score_delta"], -3.0)

    def test_quota_risk_does_not_guess_from_a_nearly_used_window(self) -> None:
        decision = self._decision(include_candidate=False, used_percent=99.0)

        self.assertEqual(decision["decision"], "keep")

    def test_fewer_than_five_work_units_waits_for_evidence(self) -> None:
        decision = self._decision(completed=4)

        self.assertEqual(decision["decision"], "wait")
        self.assertEqual(decision["short_circuit_reason"], "workload_preview_missing")

    def test_preview_sample_only_allows_compare_first(self) -> None:
        decision = self._decision(completed=8)

        self.assertEqual(decision["decision"], "compare_first")
        self.assertEqual(decision["confidence_level"], "low")
        self.assertIn("5 个真实任务", decision["next_action"])

    def test_qualified_sample_and_material_benefit_produce_trial_switch(self) -> None:
        decision = self._decision(completed=30)

        self.assertEqual(decision["decision"], "trial_switch")
        self.assertEqual(decision["candidate_model_configuration_id"], "candidate")
        self.assertEqual(decision["quality"]["score_delta"], 2)
        self.assertEqual(
            decision["benefits"]["active_time_reduction_percent"],
            25.0,
        )
        self.assertEqual(
            decision["benefits"]["standard_cost_reduction_percent"],
            40.0,
        )
        self.assertIsNone(decision["benefits"]["quota_reduction_percent_range"])
        self.assertGreaterEqual(decision["confidence"], 0.6)

    def test_real_workload_tokens_and_median_time_take_precedence_over_eval_proxy(self) -> None:
        decision = self._decision(
            completed=30,
            include_candidate_workload=True,
        )

        self.assertEqual(decision["decision"], "trial_switch")
        self.assertEqual(
            decision["benefits"]["active_time_evidence"],
            "real_workload",
        )
        self.assertEqual(
            decision["benefits"]["standard_cost_evidence"],
            "real_workload",
        )
        self.assertEqual(
            decision["benefits"]["active_time_reduction_percent"],
            50.0,
        )
        self.assertGreater(
            decision["benefits"]["standard_cost_reduction_percent"],
            70.0,
        )

    def test_attributed_official_quota_burn_produces_bounded_benefit(self) -> None:
        quota_burn = {
            "schema_version": 1,
            "status": "available",
            "aggregates": [
                _quota_aggregate(
                    model="gpt-5.6-terra",
                    effort="high",
                    median=2.0,
                    p25=1.5,
                    p75=2.5,
                ),
                _quota_aggregate(
                    model="gpt-5.6-sol",
                    effort="xhigh",
                    median=1.0,
                    p25=0.8,
                    p75=1.2,
                ),
            ],
        }

        decision = self._decision(
            completed=30,
            used_percent=60.0,
            quota_burn=quota_burn,
        )

        self.assertEqual(decision["decision"], "trial_switch")
        self.assertEqual(
            decision["benefits"]["quota_reduction_percent_range"],
            [20.0, 68.0],
        )
        self.assertEqual(
            decision["benefits"]["additional_similar_tasks_range"],
            [6.7, 34.0],
        )
        self.assertEqual(
            decision["benefits"]["quota_evidence"],
            "official_window_attributed",
        )

    def test_quota_burn_does_not_compare_different_official_windows(self) -> None:
        quota_burn = {
            "schema_version": 1,
            "status": "available",
            "aggregates": [
                _quota_aggregate(
                    model="gpt-5.6-terra",
                    effort="high",
                    median=2.0,
                    p25=1.5,
                    p75=2.5,
                    window_id="primary",
                ),
                _quota_aggregate(
                    model="gpt-5.6-sol",
                    effort="xhigh",
                    median=1.0,
                    p25=0.8,
                    p75=1.2,
                    window_id="secondary",
                ),
            ],
        }

        decision = self._decision(
            completed=30,
            used_percent=60.0,
            quota_burn=quota_burn,
        )

        self.assertIsNone(decision["benefits"]["quota_reduction_percent_range"])
        self.assertIsNone(decision["benefits"]["additional_similar_tasks_range"])

    def test_custom_endpoint_never_receives_official_quota_benefit(self) -> None:
        endpoint_candidate = {
            **self.candidate,
            "source_mode": "api",
            "route_identity_status": "matched",
        }
        quota_burn = {
            "schema_version": 1,
            "status": "available",
            "aggregates": [
                _quota_aggregate(
                    model="gpt-5.6-terra",
                    effort="high",
                    median=2.0,
                    p25=1.5,
                    p75=2.5,
                ),
                _quota_aggregate(
                    model="gpt-5.6-sol",
                    effort="xhigh",
                    median=1.0,
                    p25=0.8,
                    p75=1.2,
                ),
            ],
        }

        decision = self._decision(
            candidate=endpoint_candidate,
            completed=30,
            used_percent=60.0,
            quota_burn=quota_burn,
        )

        self.assertIsNone(decision["benefits"]["quota_reduction_percent_range"])
        self.assertIsNone(decision["benefits"]["additional_similar_tasks_range"])

    def test_incomplete_history_coverage_caps_confidence_and_requires_comparison(self) -> None:
        decision = self._decision(completed=30, coverage_complete=False)

        self.assertEqual(decision["decision"], "compare_first")
        self.assertEqual(decision["confidence"], 0.5)
        self.assertTrue(
            any(
                "历史覆盖尚不完整" in limitation
                for limitation in decision["limitations"]
            )
        )

    def test_non_material_benefit_keeps_current_model(self) -> None:
        marginal = _row(
            "candidate",
            model="gpt-5.6-sol",
            effort="xhigh",
            score=84,
            question_scores=(17, 16, 17, 17, 17),
            elapsed_seconds=540,
            cost_usd=0.8,
        )

        decision = self._decision(candidate=marginal)

        self.assertEqual(decision["decision"], "keep")
        self.assertEqual(decision["short_circuit_reason"], "no_material_benefit")

    def test_exhausted_official_quota_without_guarded_alternative_reports_risk(self) -> None:
        decision = self._decision(include_candidate=False, used_percent=100.0)

        self.assertEqual(decision["decision"], "quota_risk")
        self.assertEqual(decision["short_circuit_reason"], "quota_exhausted_no_alternative")

    def test_missing_price_is_unavailable_not_zero_cost(self) -> None:
        unknown_price = _row(
            "candidate",
            model="private-model",
            effort="high",
            score=84,
            question_scores=(17, 16, 17, 17, 17),
            elapsed_seconds=450,
            cost_usd=None,
        )

        decision = self._decision(candidate=unknown_price)

        self.assertIsNone(
            decision["benefits"]["standard_cost_reduction_percent"]
        )

    def test_custom_endpoint_identity_is_retained_as_a_limitation(self) -> None:
        endpoint_candidate = dict(self.candidate)
        endpoint_candidate["source_mode"] = "api"
        endpoint_candidate["route_identity_status"] = "matched"

        decision = self._decision(candidate=endpoint_candidate)

        self.assertEqual(decision["decision"], "trial_switch")
        self.assertTrue(
            any("不等同官方直连证明" in item for item in decision["limitations"])
        )

    def test_changed_candidate_endpoint_route_requires_full_comparison(self) -> None:
        endpoint_candidate = dict(self.candidate)
        endpoint_candidate["source_mode"] = "api"
        endpoint_candidate["route_identity_status"] = "changed"

        decision = self._decision(candidate=endpoint_candidate)

        self.assertEqual(decision["decision"], "compare_first")
        self.assertEqual(
            decision["short_circuit_reason"],
            "candidate_route_not_current",
        )

    def test_changed_current_endpoint_route_invalidates_current_baseline(self) -> None:
        endpoint_current = dict(self.current)
        endpoint_current["source_mode"] = "api"
        endpoint_current["route_identity_status"] = "changed"

        decision = self._decision(current=endpoint_current)

        self.assertEqual(decision["decision"], "compare_first")
        self.assertEqual(
            decision["short_circuit_reason"],
            "current_route_not_current",
        )


if __name__ == "__main__":
    unittest.main()
