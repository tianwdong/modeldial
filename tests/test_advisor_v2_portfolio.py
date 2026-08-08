from __future__ import annotations

import unittest

from scanner.advisor_v2_portfolio import (
    build_multi_recommendation_portfolio,
    build_recommendation_portfolio,
)


CURRENT_ID = "current"


def _row(
    configuration_id: str,
    *,
    score: float,
    elapsed_seconds: float,
    cost_usd: float | None,
    question_scores: tuple[int, ...] = (16, 16, 16, 16, 16),
) -> dict[str, object]:
    return {
        "model_configuration_id": configuration_id,
        "overall_score": score,
        "elapsed_seconds": elapsed_seconds,
        "estimated_cost_usd": cost_usd,
        "cost_coverage": "complete" if cost_usd is not None else "unknown",
        "question_pack_version": "pack-v1",
        "grader_version": "grader-v1",
        "question_results": [
            {
                "question_id": f"q{index}",
                "semantic_score": value,
                "semantic_total": 20,
            }
            for index, value in enumerate(question_scores, start=1)
        ],
    }


def _evidence(
    candidates: list[dict[str, object]],
    *,
    current: dict[str, object] | None = None,
    current_status: str = "ready",
    testable_ids: list[str] | None = None,
) -> dict[str, object]:
    current_row = current or _row(
        CURRENT_ID,
        score=84,
        elapsed_seconds=600,
        cost_usd=1.0,
    )
    return {
        "schema_version": 2,
        "source_mode": "auto",
        "resolved_data_source": "local_evaluation",
        "source_reason": "local_exact_match",
        "source_snapshot_id": "local:run-1",
        "pricing_snapshot_id": "pricing:run-1",
        "current_model_configuration_id": str(
            current_row["model_configuration_id"]
        ),
        "current_status": current_status,
        "eligible_candidate_ids": [
            str(candidate["model_configuration_id"])
            for candidate in candidates
        ],
        "testable_candidate_ids": testable_ids or [],
        "resolved_result_rows": [current_row, *candidates],
    }


def _adopted_epoch(
    *,
    baseline_id: str,
    adopted_id: str,
    preference: str = "speed",
    source_snapshot_id: str = "local:run-1",
    pricing_snapshot_id: str = "pricing:run-1",
) -> dict[str, object]:
    return {
        "lifecycle_status": "open",
        "segment_kind": "actual_switch",
        "current_model_configuration_id": baseline_id,
        "recommended_model_configuration_id": adopted_id,
        "preference": preference,
        "resolved_data_source": "local_evaluation",
        "evaluation_snapshot_id": source_snapshot_id,
        "pricing_snapshot_id": pricing_snapshot_id,
        "question_pack_version": "pack-v1",
        "grader_version": "grader-v1",
    }


class RecommendationPortfolioV2Tests(unittest.TestCase):
    def test_adopted_recommendation_does_not_immediately_cascade_to_next_tier(self) -> None:
        baseline = _row("baseline", score=84, elapsed_seconds=600, cost_usd=1.0)
        adopted = _row("adopted", score=80, elapsed_seconds=400, cost_usd=0.6)
        next_tier = _row("next-tier", score=74, elapsed_seconds=200, cost_usd=0.3)

        portfolio = build_recommendation_portfolio(
            _evidence([baseline, next_tier], current=adopted),
            preference="speed",
            prior_recommendation_epochs=[
                _adopted_epoch(
                    baseline_id="baseline",
                    adopted_id="adopted",
                )
            ],
        )

        self.assertEqual(portfolio["status"], "keep")
        self.assertEqual(
            portfolio["recommendation_lifecycle"],
            {
                "schema_version": 1,
                "status": "adopted",
                "trigger": "recommendation_accepted",
                "anchor_configuration_id": "baseline",
                "adopted_configuration_id": "adopted",
            },
        )
        self.assertEqual(portfolio["decisions"][0]["decision"], "keep")
        self.assertIsNone(
            portfolio["decisions"][0]["candidate_model_configuration_id"]
        )

    def test_new_evidence_keeps_original_quality_anchor_instead_of_ratchet(self) -> None:
        baseline = _row("baseline", score=84, elapsed_seconds=600, cost_usd=1.0)
        adopted = _row("adopted", score=80, elapsed_seconds=400, cost_usd=0.6)
        too_low = _row("too-low", score=72, elapsed_seconds=180, cost_usd=0.2)
        evidence = _evidence([baseline, too_low], current=adopted)
        evidence["source_snapshot_id"] = "local:run-2"
        evidence["eligible_candidate_ids"] = ["too-low"]

        portfolio = build_recommendation_portfolio(
            evidence,
            preference="speed",
            prior_recommendation_epochs=[
                _adopted_epoch(
                    baseline_id="baseline",
                    adopted_id="adopted",
                )
            ],
        )
        decision = portfolio["decisions"][0]

        self.assertEqual(portfolio["status"], "keep")
        self.assertEqual(
            portfolio["recommendation_lifecycle"]["trigger"],
            "new_evidence_kept",
        )
        self.assertEqual(
            decision["comparison_candidate_model_configuration_id"],
            "too-low",
        )
        self.assertEqual(decision["quality"]["score_delta"], -8.0)
        self.assertEqual(decision["quality_guard"]["score_delta_points"], -12.0)
        self.assertEqual(
            decision["quality_guard"]["anchor_model_configuration_id"],
            "baseline",
        )
        self.assertEqual(
            decision["comparison_candidate_reasons"],
            ["quality_guard_failed"],
        )

    def test_multi_configuration_keeps_adopted_cycle_independent_from_other_sessions(self) -> None:
        baseline = _row("baseline", score=84, elapsed_seconds=600, cost_usd=1.0)
        adopted = _row("adopted", score=80, elapsed_seconds=400, cost_usd=0.6)
        next_tier = _row("next-tier", score=74, elapsed_seconds=200, cost_usd=0.3)
        other_current = _row("other-current", score=84, elapsed_seconds=500, cost_usd=0.9)
        other_candidate = _row("other-candidate", score=78, elapsed_seconds=300, cost_usd=0.5)
        adopted_context = _evidence([baseline, next_tier], current=adopted)
        other_context = _evidence([other_candidate], current=other_current)

        portfolio = build_multi_recommendation_portfolio(
            [adopted_context, other_context],
            activity=[
                {
                    "model_configuration_id": "adopted",
                    "is_currently_producing": True,
                    "active_session_count": 1,
                },
                {
                    "model_configuration_id": "other-current",
                    "active_session_count": 1,
                },
            ],
            preference="speed",
            prior_recommendation_epochs=[
                _adopted_epoch(
                    baseline_id="baseline",
                    adopted_id="adopted",
                )
            ],
        )

        self.assertEqual(portfolio["representative_configuration_id"], "adopted")
        self.assertEqual(portfolio["status"], "keep")
        self.assertEqual(
            portfolio["recommendation_lifecycle"]["status"],
            "adopted",
        )
        decisions = {
            decision["current_model_configuration_id"]: decision
            for decision in portfolio["decisions"]
        }
        self.assertEqual(decisions["adopted"]["decision"], "keep")
        self.assertEqual(decisions["other-current"]["decision"], "recommend")

    def test_new_evidence_can_propose_again_when_original_quality_floor_holds(self) -> None:
        baseline = _row("baseline", score=84, elapsed_seconds=600, cost_usd=1.0)
        adopted = _row("adopted", score=80, elapsed_seconds=400, cost_usd=0.6)
        safe_next_tier = _row(
            "safe-next-tier",
            score=75,
            elapsed_seconds=180,
            cost_usd=0.2,
        )
        evidence = _evidence([baseline, safe_next_tier], current=adopted)
        evidence["source_snapshot_id"] = "local:run-2"
        evidence["eligible_candidate_ids"] = ["safe-next-tier"]

        portfolio = build_recommendation_portfolio(
            evidence,
            preference="speed",
            prior_recommendation_epochs=[
                _adopted_epoch(
                    baseline_id="baseline",
                    adopted_id="adopted",
                )
            ],
        )

        self.assertEqual(portfolio["status"], "recommend")
        self.assertEqual(
            portfolio["recommendation_lifecycle"],
            {
                "schema_version": 1,
                "status": "proposed",
                "trigger": "new_evidence",
                "anchor_configuration_id": "baseline",
                "adopted_configuration_id": "adopted",
            },
        )
        self.assertEqual(
            portfolio["decisions"][0]["candidate_model_configuration_id"],
            "safe-next-tier",
        )

    def test_smart_recommends_material_gain_without_other_regression(self) -> None:
        evidence = _evidence(
            [
                _row(
                    "balanced",
                    score=81,
                    elapsed_seconds=400,
                    cost_usd=0.6,
                )
            ]
        )

        portfolio = build_recommendation_portfolio(evidence, preference="smart")
        decision = portfolio["decisions"][0]

        self.assertEqual(decision["decision"], "recommend")
        self.assertEqual(decision["candidate_model_configuration_id"], "balanced")
        self.assertEqual(decision["quality"]["score_delta"], -3.0)
        self.assertEqual(decision["time"]["reduction_percent"], 33.3)
        self.assertEqual(decision["reference_cost"]["reduction_percent"], 40.0)

    def test_smart_rejects_cost_regression_and_unknown_cost(self) -> None:
        for candidate in (
            _row("cost-regression", score=84, elapsed_seconds=400, cost_usd=1.01),
            _row("unknown-cost", score=84, elapsed_seconds=400, cost_usd=None),
        ):
            with self.subTest(candidate=candidate["model_configuration_id"]):
                portfolio = build_recommendation_portfolio(
                    _evidence([candidate]),
                    preference="smart",
                )
                self.assertEqual(portfolio["decisions"][0]["decision"], "keep")

    def test_keep_exposes_closest_candidate_without_turning_it_into_a_recommendation(self) -> None:
        portfolio = build_recommendation_portfolio(
            _evidence(
                [
                    _row("cost-regression", score=84, elapsed_seconds=400, cost_usd=1.01),
                    _row("too-slow", score=83, elapsed_seconds=650, cost_usd=0.5),
                ]
            ),
            preference="smart",
        )
        decision = portfolio["decisions"][0]

        self.assertEqual(decision["decision"], "keep")
        self.assertIsNone(decision["candidate_model_configuration_id"])
        self.assertEqual(
            decision["comparison_candidate_model_configuration_id"],
            "cost-regression",
        )
        self.assertEqual(
            decision["comparison_candidate_reasons"],
            ["reference_cost_regressed"],
        )
        self.assertEqual(decision["quality"]["score_delta"], 0.0)
        self.assertEqual(decision["time"]["reduction_percent"], 33.3)
        self.assertEqual(decision["reference_cost"]["reduction_percent"], -1.0)

    def test_quality_keep_explains_minimum_gain_rule(self) -> None:
        portfolio = build_recommendation_portfolio(
            _evidence([_row("plus-one", score=85, elapsed_seconds=300, cost_usd=0.5)]),
            preference="quality",
        )
        decision = portfolio["decisions"][0]

        self.assertEqual(
            decision["comparison_candidate_model_configuration_id"],
            "plus-one",
        )
        self.assertEqual(
            decision["comparison_candidate_reasons"],
            ["quality_gain_below_minimum"],
        )

    def test_smart_prefers_two_improvements_before_smaller_quality_loss(self) -> None:
        portfolio = build_recommendation_portfolio(
            _evidence(
                [
                    _row("time-only", score=84, elapsed_seconds=300, cost_usd=1.0),
                    _row("both", score=80, elapsed_seconds=400, cost_usd=0.6),
                ]
            ),
            preference="smart",
        )

        self.assertEqual(
            portfolio["decisions"][0]["candidate_model_configuration_id"],
            "both",
        )

    def test_quality_guards_include_exact_boundary_only(self) -> None:
        smart = build_recommendation_portfolio(
            _evidence(
                [
                    _row("minus-five", score=79, elapsed_seconds=400, cost_usd=0.6),
                    _row("below-five", score=78.9, elapsed_seconds=200, cost_usd=0.2),
                ]
            ),
            preference="smart",
        )
        speed = build_recommendation_portfolio(
            _evidence(
                [
                    _row("minus-ten", score=74, elapsed_seconds=450, cost_usd=None),
                    _row("below-ten", score=73.9, elapsed_seconds=100, cost_usd=None),
                ]
            ),
            preference="speed",
        )

        self.assertEqual(
            smart["decisions"][0]["candidate_model_configuration_id"],
            "minus-five",
        )
        self.assertEqual(
            speed["decisions"][0]["candidate_model_configuration_id"],
            "minus-ten",
        )

    def test_quality_guard_projection_is_versioned_and_authoritative(self) -> None:
        smart = build_recommendation_portfolio(
            _evidence([_row("balanced", score=81, elapsed_seconds=400, cost_usd=0.6)]),
            preference="smart",
        )["decisions"][0]
        self.assertEqual(
            smart["quality_guard"],
            {
                "schema_version": 1,
                "status": "passed",
                "rule": "maximum_loss",
                "preference": "smart",
                "decision": "recommend",
                "threshold_points": 5.0,
                "score_delta_points": -3.0,
                "passed": True,
            },
        )

        quality_below_minimum = build_recommendation_portfolio(
            _evidence([_row("plus-one", score=85, elapsed_seconds=300, cost_usd=0.5)]),
            preference="quality",
        )["decisions"][0]
        self.assertEqual(quality_below_minimum["quality_guard"]["status"], "failed")
        self.assertEqual(quality_below_minimum["quality_guard"]["rule"], "minimum_gain")
        self.assertEqual(quality_below_minimum["quality_guard"]["threshold_points"], 2.0)
        self.assertFalse(quality_below_minimum["quality_guard"]["passed"])

        quality_not_improved = build_recommendation_portfolio(
            _evidence([_row("lower", score=80, elapsed_seconds=300, cost_usd=0.5)]),
            preference="quality",
        )["decisions"][0]
        self.assertEqual(
            quality_not_improved["quality_guard"]["status"],
            "current_is_best",
        )
        self.assertFalse(quality_not_improved["quality_guard"]["passed"])

        quality_improved = build_recommendation_portfolio(
            _evidence([_row("better-and-cheaper", score=86, elapsed_seconds=500, cost_usd=0.5)]),
            preference="cost",
        )["decisions"][0]
        self.assertEqual(quality_improved["quality_guard"]["status"], "quality_improved")
        self.assertTrue(quality_improved["quality_guard"]["passed"])

        unavailable = build_recommendation_portfolio(
            _evidence([], current_status="needs_test"),
            preference="speed",
        )["decisions"][0]
        self.assertEqual(
            unavailable["quality_guard"],
            {
                "schema_version": 1,
                "status": "unavailable",
                "rule": "maximum_loss",
                "preference": "speed",
                "decision": "needs_test",
                "threshold_points": 10.0,
                "score_delta_points": None,
                "passed": None,
            },
        )

    def test_quality_requires_two_point_gain(self) -> None:
        one_point = build_recommendation_portfolio(
            _evidence([_row("plus-one", score=85, elapsed_seconds=300, cost_usd=0.5)]),
            preference="quality",
        )
        two_points = build_recommendation_portfolio(
            _evidence([_row("plus-two", score=86, elapsed_seconds=900, cost_usd=1.5)]),
            preference="quality",
        )

        self.assertEqual(one_point["decisions"][0]["decision"], "keep")
        self.assertEqual(two_points["decisions"][0]["decision"], "recommend")
        self.assertEqual(
            two_points["decisions"][0]["reason"],
            "quality_gain_with_tradeoff",
        )

    def test_speed_uses_ten_point_guard_and_marks_six_point_tradeoff(self) -> None:
        portfolio = build_recommendation_portfolio(
            _evidence(
                [
                    _row("too-low", score=73, elapsed_seconds=100, cost_usd=0.2),
                    _row("tradeoff", score=78, elapsed_seconds=400, cost_usd=None),
                ]
            ),
            preference="speed",
        )
        decision = portfolio["decisions"][0]

        self.assertEqual(decision["candidate_model_configuration_id"], "tradeoff")
        self.assertTrue(decision["quality_tradeoff"])
        self.assertEqual(decision["quality"]["score_delta"], -6.0)

    def test_cost_requires_known_price_and_chooses_lowest_cost(self) -> None:
        portfolio = build_recommendation_portfolio(
            _evidence(
                [
                    _row("unknown", score=84, elapsed_seconds=100, cost_usd=None),
                    _row("cheap", score=77, elapsed_seconds=500, cost_usd=0.4),
                    _row("less-cheap", score=84, elapsed_seconds=300, cost_usd=0.6),
                ]
            ),
            preference="cost",
        )

        decision = portfolio["decisions"][0]
        self.assertEqual(decision["candidate_model_configuration_id"], "cheap")
        self.assertTrue(decision["quality_tradeoff"])

    def test_single_question_regression_is_warning_not_hard_block(self) -> None:
        portfolio = build_recommendation_portfolio(
            _evidence(
                [
                    _row(
                        "warning",
                        score=82,
                        elapsed_seconds=400,
                        cost_usd=0.6,
                        question_scores=(10, 18, 18, 18, 18),
                    )
                ]
            ),
            preference="smart",
        )

        decision = portfolio["decisions"][0]
        self.assertEqual(decision["decision"], "recommend")
        self.assertEqual(decision["quality_warning_question_ids"], ["q1"])

    def test_deterministic_tie_break_uses_configuration_id(self) -> None:
        candidates = [
            _row("z-candidate", score=82, elapsed_seconds=400, cost_usd=0.6),
            _row("a-candidate", score=82, elapsed_seconds=400, cost_usd=0.6),
        ]
        portfolio = build_recommendation_portfolio(
            _evidence(candidates),
            preference="smart",
        )

        self.assertEqual(
            portfolio["decisions"][0]["candidate_model_configuration_id"],
            "a-candidate",
        )

    def test_unready_current_evidence_returns_needs_test(self) -> None:
        portfolio = build_recommendation_portfolio(
            _evidence([], current_status="needs_test", testable_ids=["candidate"]),
            preference="smart",
        )

        decision = portfolio["decisions"][0]
        self.assertEqual(decision["decision"], "needs_test")
        self.assertIsNone(decision["candidate_model_configuration_id"])
        self.assertEqual(portfolio["testable_candidate_ids"], ["candidate"])

    def test_invalid_preference_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported recommendation preference"):
            build_recommendation_portfolio(_evidence([]), preference="balanced")

    def test_multi_configuration_builds_independent_decisions_and_uses_latest_activity(self) -> None:
        current_a = _row("current-a", score=84, elapsed_seconds=600, cost_usd=1.0)
        current_b = _row("current-b", score=79, elapsed_seconds=500, cost_usd=0.8)
        candidate = _row("candidate", score=80, elapsed_seconds=350, cost_usd=0.5)
        contexts = [
            {
                **_evidence([current_b, candidate], current=current_a),
                "current_model_configuration_id": "current-a",
            },
            {
                **_evidence([current_a, candidate], current=current_b),
                "current_model_configuration_id": "current-b",
            },
        ]

        portfolio = build_multi_recommendation_portfolio(
            contexts,
            activity=[
                {
                    "model_configuration_id": "current-a",
                    "active_session_count": 3,
                    "last_active_at": "2026-07-25T07:00:00Z",
                },
                {
                    "model_configuration_id": "current-b",
                    "active_session_count": 1,
                    "last_active_at": "2026-07-25T07:30:00Z",
                },
            ],
            preference="smart",
        )

        self.assertEqual(portfolio["representative_configuration_id"], "current-b")
        self.assertEqual(portfolio["representative_reason"], "most_recent_activity")
        self.assertEqual(
            portfolio["representative_evidence"]["current_model_configuration_id"],
            "current-b",
        )
        self.assertEqual(
            [item["current_model_configuration_id"] for item in portfolio["decisions"]],
            ["current-a", "current-b"],
        )

    def test_multi_configuration_prefers_currently_producing_configuration(self) -> None:
        contexts = [
            {
                **_evidence([], current=_row("a", score=84, elapsed_seconds=600, cost_usd=1.0)),
                "current_model_configuration_id": "a",
            },
            {
                **_evidence([], current=_row("b", score=84, elapsed_seconds=600, cost_usd=1.0)),
                "current_model_configuration_id": "b",
            },
        ]

        portfolio = build_multi_recommendation_portfolio(
            contexts,
            activity=[
                {
                    "model_configuration_id": "a",
                    "active_session_count": 1,
                    "last_active_at": "2026-07-25T07:00:00Z",
                    "is_currently_producing": True,
                },
                {
                    "model_configuration_id": "b",
                    "active_session_count": 1,
                    "last_active_at": "2026-07-25T07:30:00Z",
                    "is_currently_producing": False,
                },
            ],
        )

        self.assertEqual(portfolio["representative_configuration_id"], "a")
        self.assertEqual(portfolio["representative_reason"], "currently_producing")

    def test_multi_configuration_uses_session_count_only_without_activity_time(self) -> None:
        contexts = [
            {
                **_evidence([], current=_row("a", score=84, elapsed_seconds=600, cost_usd=1.0)),
                "current_model_configuration_id": "a",
            },
            {
                **_evidence([], current=_row("b", score=84, elapsed_seconds=600, cost_usd=1.0)),
                "current_model_configuration_id": "b",
            },
        ]

        portfolio = build_multi_recommendation_portfolio(
            contexts,
            activity=[
                {"model_configuration_id": "a", "active_session_count": 1},
                {"model_configuration_id": "b", "active_session_count": 2},
            ],
        )

        self.assertEqual(portfolio["representative_configuration_id"], "b")
        self.assertEqual(
            portfolio["representative_reason"],
            "active_session_count_fallback",
        )

    def test_multi_configuration_keeps_independent_decisions_across_sources(self) -> None:
        local = _evidence([])
        official = {
            **_evidence(
                [],
                current=_row("other", score=82, elapsed_seconds=500, cost_usd=0.8),
            ),
            "current_model_configuration_id": "other",
            "source_mode": "official_snapshot",
            "resolved_data_source": "official_snapshot",
            "source_reason": "official_snapshot_selected",
        }

        portfolio = build_multi_recommendation_portfolio(
            [local, official],
            activity=[
                {"model_configuration_id": CURRENT_ID, "active_session_count": 1},
                {"model_configuration_id": "other", "active_session_count": 1},
            ],
        )

        self.assertEqual(portfolio["status"], "keep")
        self.assertEqual(portfolio["resolved_data_source"], "local_evaluation")
        self.assertEqual(
            portfolio["source_resolution_reason"],
            "local_exact_match",
        )
        self.assertEqual(
            portfolio["source_mode_by_configuration_id"],
            {CURRENT_ID: "auto", "other": "official_snapshot"},
        )
        self.assertEqual(
            [item["current_model_configuration_id"] for item in portfolio["decisions"]],
            [CURRENT_ID, "other"],
        )

    def test_single_configuration_without_resolved_source_keeps_its_own_reason(self) -> None:
        unresolved = {
            **_evidence([], current_status="needs_test", testable_ids=["candidate"]),
            "resolved_data_source": None,
            "source_reason": "no_actionable_source",
        }

        portfolio = build_multi_recommendation_portfolio(
            [unresolved],
            activity=[
                {"model_configuration_id": CURRENT_ID, "active_session_count": 1},
            ],
        )

        self.assertEqual(portfolio["status"], "needs_test")
        self.assertIsNone(portfolio["resolved_data_source"])
        self.assertEqual(
            portfolio["source_resolution_reason"],
            "no_actionable_source",
        )
        self.assertEqual(portfolio["decisions"][0]["decision"], "needs_test")


if __name__ == "__main__":
    unittest.main()
