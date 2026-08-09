from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from scanner.recommendation_use import (
    refresh_recommendation_use_observations,
    update_recommendation_use_epochs,
)
from scanner.usage_store import UsageStore


NOW = datetime(2026, 7, 26, 1, 0, tzinfo=timezone.utc)
MAX_ID = "codex-local-default:gpt-5.6-sol:max"
CURRENT_ID = "codex-local-default:gpt-5.6-sol:xhigh"
CANDIDATE_ID = "codex-local-default:gpt-5.6-sol:high"


def _state(
    *,
    duplicate_candidate: bool = False,
    candidate_enabled: bool = True,
) -> dict[str, object]:
    candidates = [
        {
            "id": CURRENT_ID,
            "model_id": "gpt-5.6-sol",
            "scan_profile": "xhigh",
            "enabled": True,
        },
        {
            "id": CANDIDATE_ID,
            "model_id": "gpt-5.6-sol",
            "scan_profile": "high",
            "enabled": candidate_enabled,
        },
    ]
    if duplicate_candidate:
        candidates.append(
            {
                "id": "endpoint-1:gpt-5.6-sol:high",
                "model_id": "gpt-5.6-sol",
                "scan_profile": "high",
                "enabled": True,
            }
        )
    return {
        "config": {
            "model_ingress": {
                "sources": [
                    {
                        "id": "codex_local",
                        "kind": "codex",
                        "mode": "local",
                        "enabled": True,
                    }
                ],
                "connections": [
                    {
                        "id": "codex-local-default",
                        "source_id": "codex_local",
                        "provider_id": "codex",
                        "enabled": True,
                        "model_candidates": candidates,
                    }
                ]
            }
        }
    }


def _context(
    *,
    snapshot_id: str = "snapshot-1",
    candidate_cost_coverage: str = "complete",
    pricing_snapshot_id: str | None = None,
) -> dict[str, object]:
    context: dict[str, object] = {
        "current_model_configuration_id": CURRENT_ID,
        "resolved_data_source": "official_snapshot",
        "source_snapshot_id": snapshot_id,
        "resolved_result_rows": [
            {
                "model_configuration_id": CURRENT_ID,
                "elapsed_seconds": 620.0,
                "estimated_cost_usd": 0.60,
                "cost_coverage": "complete",
            },
            {
                "model_configuration_id": CANDIDATE_ID,
                "elapsed_seconds": 400.0,
                "estimated_cost_usd": 0.30,
                "cost_coverage": candidate_cost_coverage,
            },
        ],
    }
    if pricing_snapshot_id is not None:
        context["pricing_snapshot_id"] = pricing_snapshot_id
    return context


def _switch_chain_state(current_id: str) -> dict[str, object]:
    state = _state()
    state["config"]["recommendation"] = {
        "effective_current_candidate_id": current_id,
    }
    candidates = state["config"]["model_ingress"]["connections"][0][
        "model_candidates"
    ]
    candidates.insert(
        0,
        {
            "id": MAX_ID,
            "model_id": "gpt-5.6-sol",
            "scan_profile": "max",
            "enabled": True,
        },
    )
    return state


def _switch_chain_context(
    current_id: str,
    *,
    snapshot_id: str = "snapshot-chain",
) -> dict[str, object]:
    return {
        "current_model_configuration_id": current_id,
        "resolved_data_source": "official_snapshot",
        "source_snapshot_id": snapshot_id,
        "resolved_result_rows": [
            {
                "model_configuration_id": MAX_ID,
                "elapsed_seconds": 1_000.0,
                "estimated_cost_usd": 1.20,
                "cost_coverage": "complete",
            },
            {
                "model_configuration_id": CURRENT_ID,
                "elapsed_seconds": 620.0,
                "estimated_cost_usd": 0.60,
                "cost_coverage": "complete",
            },
            {
                "model_configuration_id": CANDIDATE_ID,
                "elapsed_seconds": 400.0,
                "estimated_cost_usd": 0.30,
                "cost_coverage": "complete",
            },
        ],
    }


def _switch_chain_portfolio(
    current_id: str,
    *,
    candidate_id: str | None = None,
) -> dict[str, object]:
    decision = "recommend" if candidate_id is not None else "keep"
    return {
        "schema_version": 2,
        "preference": "smart",
        "status": decision,
        "representative_configuration_id": current_id,
        "decisions": [
            {
                "current_model_configuration_id": current_id,
                "candidate_model_configuration_id": candidate_id,
                "decision": decision,
            }
        ],
    }


def _portfolio(*, preference: str = "smart", decision: str = "recommend") -> dict[str, object]:
    return {
        "schema_version": 2,
        "preference": preference,
        "status": decision,
        "decisions": [
            {
                "current_model_configuration_id": CURRENT_ID,
                "candidate_model_configuration_id": (
                    CANDIDATE_ID if decision == "recommend" else None
                ),
                "decision": decision,
            }
        ],
    }


def _workload(
    *,
    completed_work_units: int = 14,
    reference_cost_usd: float | None = 120.0,
    response_wait_ms: int | None = 600_000,
    response_wait_work_unit_count: int = 10,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "available",
        "period_start": "2026-07-19T01:00:00Z",
        "period_end": "2026-07-26T01:00:00Z",
        "coverage_complete": False,
        "aggregates": [
            {
                "model_configuration_id": "codex:openai:gpt-5.6-sol:xhigh",
                "provider_id": "openai",
                "raw_model_id": "gpt-5.6-sol",
                "reasoning_effort": "xhigh",
                "completed_work_units": completed_work_units,
                "reference_cost_usd": reference_cost_usd,
                "reference_cost_status": (
                    "estimated" if reference_cost_usd is not None else "unavailable"
                ),
                "reference_cost_pricing_snapshot_id": "pricing-v1-2026-07-24-1",
                "response_wait_ms": response_wait_ms,
                "response_wait_work_unit_count": response_wait_work_unit_count,
            },
            {
                "model_configuration_id": "codex:openai:gpt-5.4:high",
                "provider_id": "openai",
                "raw_model_id": "gpt-5.4",
                "reasoning_effort": "high",
                "completed_work_units": 86,
                "reference_cost_usd": 880.0,
                "reference_cost_status": "estimated",
                "reference_cost_pricing_snapshot_id": "pricing-v1-2026-07-24-1",
                "response_wait_ms": None,
                "response_wait_work_unit_count": 0,
            },
        ],
    }


def _observation(
    observation_id: str,
    *,
    ended_at: datetime,
    model: str = "gpt-5.6-sol",
    effort: str = "high",
    session_key: str | None = None,
    response_wait_ms: int | None = None,
    outcome: str = "completed",
) -> dict[str, object]:
    row: dict[str, object] = {
        "schema_version": 1,
        "observation_id": observation_id,
        "session_key": session_key or f"session:{observation_id}",
        "turn_key": f"turn:{observation_id}",
        "provider_id": "openai",
        "raw_model_id": model,
        "reasoning_effort": effort,
        "started_at": (ended_at - timedelta(minutes=1)).isoformat(),
        "ended_at": ended_at.isoformat(),
        "active_duration_ms": 60_000,
        "wall_duration_ms": 60_000,
        "usage": {
            "input_tokens": 10_000,
            "cached_input_tokens": 2_000,
            "cache_write_input_tokens": 0,
            "output_tokens": 2_000,
            "reasoning_tokens": 1_000,
        },
        "outcome": outcome,
        "is_subagent": False,
        "is_modeldial_evaluation": False,
        "attribution_confidence": 1.0,
        "exclusion_reasons": [],
    }
    if response_wait_ms is not None:
        row["response_wait_ms"] = response_wait_ms
    return row


class RecommendationUseEpochTests(unittest.TestCase):
    def test_actual_switch_counts_new_work_in_a_reused_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir))
            update_recommendation_use_epochs(
                store=store,
                state=_switch_chain_state(MAX_ID),
                contexts=[_switch_chain_context(MAX_ID)],
                portfolio=_switch_chain_portfolio(MAX_ID),
                now=NOW,
            )
            usage_state = store.load_usage_state()
            usage_state["observations"] = {
                "before-switch": _observation(
                    "before-switch",
                    effort="xhigh",
                    session_key="reused-session",
                    ended_at=NOW - timedelta(minutes=1),
                ),
            }
            store.save_usage_state(usage_state)
            update_recommendation_use_epochs(
                store=store,
                state=_switch_chain_state(CURRENT_ID),
                contexts=[_switch_chain_context(CURRENT_ID)],
                portfolio=_switch_chain_portfolio(CURRENT_ID),
                now=NOW + timedelta(minutes=5),
            )
            usage_state = store.load_usage_state()
            usage_state["observations"]["after-switch"] = _observation(
                "after-switch",
                effort="xhigh",
                session_key="reused-session",
                ended_at=NOW + timedelta(minutes=7),
            )
            store.save_usage_state(usage_state)
            summary = refresh_recommendation_use_observations(
                store=store,
                now=NOW + timedelta(minutes=8),
            )

        actual = next(
            epoch
            for epoch in summary["epochs"]
            if epoch["segment_kind"] == "actual_switch"
        )
        self.assertEqual(actual["observed_candidate_work_unit_count"], 1)
        self.assertEqual(summary["benefit_summary"]["observed_work_unit_count"], 1)

    def test_actual_switch_backdates_to_new_usage_observed_between_polls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir))
            update_recommendation_use_epochs(
                store=store,
                state=_switch_chain_state(MAX_ID),
                contexts=[_switch_chain_context(MAX_ID)],
                portfolio=_switch_chain_portfolio(MAX_ID),
                now=NOW,
            )
            usage_state = store.load_usage_state()
            usage_state["observations"] = {
                "before-detection": _observation(
                    "before-detection",
                    effort="xhigh",
                    ended_at=NOW + timedelta(minutes=7),
                )
            }
            store.save_usage_state(usage_state)

            summary = update_recommendation_use_epochs(
                store=store,
                state=_switch_chain_state(CURRENT_ID),
                contexts=[_switch_chain_context(CURRENT_ID)],
                portfolio=_switch_chain_portfolio(CURRENT_ID),
                now=NOW + timedelta(minutes=10),
            )

        actual = next(
            epoch
            for epoch in summary["epochs"]
            if epoch["segment_kind"] == "actual_switch"
        )
        self.assertEqual(
            actual["started_at"],
            "2026-07-26T01:06:00Z",
        )
        self.assertEqual(actual["observed_candidate_work_unit_count"], 1)

    def test_actual_switch_is_recorded_without_comparison_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir))
            update_recommendation_use_epochs(
                store=store,
                state=_switch_chain_state(MAX_ID),
                contexts=[],
                portfolio=_switch_chain_portfolio(MAX_ID),
                now=NOW,
            )

            summary = update_recommendation_use_epochs(
                store=store,
                state=_switch_chain_state(CURRENT_ID),
                contexts=[],
                portfolio=_switch_chain_portfolio(CURRENT_ID),
                now=NOW + timedelta(minutes=5),
            )

        actual = next(
            epoch
            for epoch in summary["epochs"]
            if epoch["segment_kind"] == "actual_switch"
        )
        self.assertEqual(actual["current_model_configuration_id"], MAX_ID)
        self.assertEqual(actual["recommended_model_configuration_id"], CURRENT_ID)
        self.assertEqual(actual["reference_cost_estimate_status"], "prospective")

    def test_actual_switch_segments_coexist_with_next_recommendation_and_accumulate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir))
            max_state = _switch_chain_state(MAX_ID)
            baseline = update_recommendation_use_epochs(
                store=store,
                state=max_state,
                contexts=[_switch_chain_context(MAX_ID)],
                portfolio=_switch_chain_portfolio(
                    MAX_ID,
                    candidate_id=CANDIDATE_ID,
                ),
                now=NOW,
            )
            self.assertEqual(len(baseline["epochs"]), 1)
            self.assertEqual(baseline["epochs"][0]["segment_kind"], "recommendation")

            switched_to_xhigh = update_recommendation_use_epochs(
                store=store,
                state=_switch_chain_state(CURRENT_ID),
                contexts=[_switch_chain_context(CURRENT_ID)],
                portfolio=_switch_chain_portfolio(
                    CURRENT_ID,
                    candidate_id=CANDIDATE_ID,
                ),
                now=NOW + timedelta(minutes=5),
            )
            self.assertEqual(
                {
                    (
                        epoch["current_model_configuration_id"],
                        epoch["recommended_model_configuration_id"],
                        epoch["segment_kind"],
                    )
                    for epoch in switched_to_xhigh["epochs"]
                    if epoch["lifecycle_status"] == "open"
                },
                {
                    (MAX_ID, CURRENT_ID, "actual_switch"),
                    (CURRENT_ID, CANDIDATE_ID, "recommendation"),
                },
            )

            usage_state = store.load_usage_state()
            usage_state["observations"] = {
                "xhigh-work": _observation(
                    "xhigh-work",
                    effort="xhigh",
                    ended_at=NOW + timedelta(minutes=7),
                    response_wait_ms=62_000,
                )
            }
            store.save_usage_state(usage_state)
            xhigh_summary = refresh_recommendation_use_observations(
                store=store,
                now=NOW + timedelta(minutes=8),
            )
            xhigh_actual = next(
                epoch
                for epoch in xhigh_summary["epochs"]
                if epoch["segment_kind"] == "actual_switch"
            )
            next_recommendation = next(
                epoch
                for epoch in xhigh_summary["epochs"]
                if epoch["segment_kind"] == "recommendation"
                and epoch["lifecycle_status"] == "open"
            )
            self.assertEqual(xhigh_actual["observed_candidate_work_unit_count"], 1)
            self.assertEqual(
                next_recommendation["observed_candidate_work_unit_count"],
                0,
            )
            self.assertEqual(xhigh_summary["benefit_summary"]["observed_work_unit_count"], 1)

            switched_to_high = update_recommendation_use_epochs(
                store=store,
                state=_switch_chain_state(CANDIDATE_ID),
                contexts=[_switch_chain_context(CANDIDATE_ID)],
                portfolio=_switch_chain_portfolio(CANDIDATE_ID),
                now=NOW + timedelta(minutes=10),
            )
            self.assertEqual(
                sum(
                    epoch["segment_kind"] == "actual_switch"
                    for epoch in switched_to_high["epochs"]
                ),
                2,
            )
            self.assertFalse(
                any(
                    epoch["segment_kind"] == "recommendation"
                    and epoch["lifecycle_status"] == "open"
                    for epoch in switched_to_high["epochs"]
                )
            )

            usage_state = store.load_usage_state()
            usage_state["observations"]["high-work"] = _observation(
                "high-work",
                effort="high",
                ended_at=NOW + timedelta(minutes=12),
                response_wait_ms=40_000,
            )
            store.save_usage_state(usage_state)
            final_summary = update_recommendation_use_epochs(
                store=store,
                state=_switch_chain_state(CANDIDATE_ID),
                contexts=[_switch_chain_context(CANDIDATE_ID)],
                portfolio=_switch_chain_portfolio(CANDIDATE_ID),
                workload=_workload(),
                now=NOW + timedelta(minutes=13),
            )

        actual_segments = [
            epoch
            for epoch in final_summary["epochs"]
            if epoch["segment_kind"] == "actual_switch"
        ]
        benefit = final_summary["benefit_summary"]
        self.assertEqual(benefit["observed_work_unit_count"], 2)
        self.assertEqual(benefit["reference_cost_epoch_count"], 2)
        self.assertEqual(benefit["model_wait_epoch_count"], 2)
        self.assertAlmostEqual(
            benefit["reference_cost_delta_usd"],
            sum(
                epoch["estimated_reference_cost_delta_usd"]
                for epoch in actual_segments
            ),
        )
        self.assertEqual(
            benefit["model_wait_delta_ms"],
            sum(epoch["estimated_model_wait_delta_ms"] for epoch in actual_segments),
        )
        self.assertEqual(final_summary["value_summary"]["mode"], "realized")
        self.assertEqual(
            final_summary["value_summary"]["completed_work_unit_count"],
            2,
        )
        self.assertEqual(
            final_summary["value_summary"]["reference_cost_delta_usd"],
            benefit["reference_cost_delta_usd"],
        )
        self.assertEqual(
            final_summary["value_summary"]["model_wait_delta_ms"],
            benefit["model_wait_delta_ms"],
        )

    def test_representative_change_alone_does_not_create_actual_switch_segment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir))
            state = _switch_chain_state(MAX_ID)
            update_recommendation_use_epochs(
                store=store,
                state=state,
                contexts=[_switch_chain_context(MAX_ID)],
                portfolio=_switch_chain_portfolio(MAX_ID),
                now=NOW,
            )

            summary = update_recommendation_use_epochs(
                store=store,
                state=state,
                contexts=[_switch_chain_context(CURRENT_ID)],
                portfolio=_switch_chain_portfolio(
                    CURRENT_ID,
                    candidate_id=CANDIDATE_ID,
                ),
                now=NOW + timedelta(minutes=5),
            )

        self.assertFalse(
            any(
                epoch["segment_kind"] == "actual_switch"
                for epoch in summary["epochs"]
            )
        )

    def test_unobserved_default_change_does_not_create_actual_switch_segment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir))
            update_recommendation_use_epochs(
                store=store,
                state=_switch_chain_state(MAX_ID),
                contexts=[_switch_chain_context(MAX_ID)],
                portfolio=_switch_chain_portfolio(MAX_ID),
                now=NOW,
            )
            unavailable_state = _switch_chain_state(CURRENT_ID)
            unavailable_state["config"]["recommendation"] = {
                "effective_current_candidate_id": None,
                "current_default_candidate_id": CURRENT_ID,
            }

            summary = update_recommendation_use_epochs(
                store=store,
                state=unavailable_state,
                contexts=[_switch_chain_context(CURRENT_ID)],
                portfolio=_switch_chain_portfolio(CURRENT_ID),
                now=NOW + timedelta(minutes=5),
            )

        self.assertFalse(
            any(
                epoch["segment_kind"] == "actual_switch"
                for epoch in summary["epochs"]
            )
        )

    def test_v1_state_migrates_the_current_contiguous_usage_tail_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir))
            update_recommendation_use_epochs(
                store=store,
                state=_switch_chain_state(CURRENT_ID),
                contexts=[_switch_chain_context(CURRENT_ID)],
                portfolio=_switch_chain_portfolio(
                    CURRENT_ID,
                    candidate_id=CANDIDATE_ID,
                ),
                now=NOW,
            )
            persisted = store.load_recommendation_use_state()
            persisted.pop("segment_contract_version", None)
            persisted["representative_current_model_configuration_id"] = CURRENT_ID
            persisted["effective_current_model_configuration_id"] = CURRENT_ID
            store.save_recommendation_use_state(persisted)
            usage_state = store.load_usage_state()
            usage_state["observations"] = {
                "last-max": _observation(
                    "last-max",
                    effort="max",
                    ended_at=NOW + timedelta(minutes=1),
                ),
                "first-xhigh": _observation(
                    "first-xhigh",
                    effort="xhigh",
                    ended_at=NOW + timedelta(minutes=3),
                    response_wait_ms=62_000,
                ),
            }
            store.save_usage_state(usage_state)

            migrated = update_recommendation_use_epochs(
                store=store,
                state=_switch_chain_state(CURRENT_ID),
                contexts=[_switch_chain_context(CURRENT_ID)],
                portfolio=_switch_chain_portfolio(
                    CURRENT_ID,
                    candidate_id=CANDIDATE_ID,
                ),
                workload=_workload(),
                now=NOW + timedelta(minutes=4),
            )
            repeated = update_recommendation_use_epochs(
                store=store,
                state=_switch_chain_state(CURRENT_ID),
                contexts=[_switch_chain_context(CURRENT_ID)],
                portfolio=_switch_chain_portfolio(
                    CURRENT_ID,
                    candidate_id=CANDIDATE_ID,
                ),
                workload=_workload(),
                now=NOW + timedelta(minutes=5),
            )
            segment_contract_version = store.load_recommendation_use_state()[
                "segment_contract_version"
            ]

        actual_segments = [
            epoch
            for epoch in migrated["epochs"]
            if epoch["segment_kind"] == "actual_switch"
        ]
        self.assertEqual(len(actual_segments), 1)
        self.assertEqual(
            actual_segments[0]["current_model_configuration_id"],
            MAX_ID,
        )
        self.assertEqual(
            actual_segments[0]["recommended_model_configuration_id"],
            CURRENT_ID,
        )
        self.assertEqual(actual_segments[0]["observed_candidate_work_unit_count"], 1)
        self.assertEqual(migrated["value_summary"]["mode"], "realized")
        self.assertEqual(
            sum(
                epoch["segment_kind"] == "actual_switch"
                for epoch in repeated["epochs"]
            ),
            1,
        )
        self.assertEqual(segment_contract_version, 2)

    def test_new_recommendation_snapshot_does_not_close_actual_switch_segment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir))
            update_recommendation_use_epochs(
                store=store,
                state=_switch_chain_state(MAX_ID),
                contexts=[_switch_chain_context(MAX_ID)],
                portfolio=_switch_chain_portfolio(MAX_ID),
                now=NOW,
            )
            update_recommendation_use_epochs(
                store=store,
                state=_switch_chain_state(CURRENT_ID),
                contexts=[_switch_chain_context(CURRENT_ID)],
                portfolio=_switch_chain_portfolio(
                    CURRENT_ID,
                    candidate_id=CANDIDATE_ID,
                ),
                now=NOW + timedelta(minutes=5),
            )

            summary = update_recommendation_use_epochs(
                store=store,
                state=_switch_chain_state(CURRENT_ID),
                contexts=[
                    _switch_chain_context(
                        CURRENT_ID,
                        snapshot_id="snapshot-chain-2",
                    )
                ],
                portfolio=_switch_chain_portfolio(
                    CURRENT_ID,
                    candidate_id=CANDIDATE_ID,
                ),
                now=NOW + timedelta(hours=6),
            )

        actual_segment = next(
            epoch
            for epoch in summary["epochs"]
            if epoch["segment_kind"] == "actual_switch"
        )
        self.assertEqual(actual_segment["lifecycle_status"], "open")
        self.assertIsNone(actual_segment["ended_at"])

    def test_keep_uses_current_history_as_usage_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            summary = update_recommendation_use_epochs(
                store=UsageStore(Path(temp_dir)),
                state=_state(),
                contexts=[_context()],
                portfolio=_portfolio(decision="keep"),
                workload=_workload(response_wait_work_unit_count=2),
                now=NOW,
            )

        value = summary["value_summary"]
        self.assertEqual(value["mode"], "usage_baseline")
        self.assertEqual(value["completed_work_unit_count"], 100)
        self.assertEqual(value["reference_cost_usd"], 1000.0)
        self.assertEqual(value["reference_cost_status"], "lower_bound")
        self.assertIsNone(value["reference_cost_delta_usd"])
        self.assertIsNone(value["model_wait_ms"])
        self.assertEqual(value["model_wait_status"], "insufficient_coverage")
        self.assertEqual(value["model_wait_work_unit_count"], 2)
        self.assertEqual(value["period_days"], 7)

    def test_actionable_recommendation_keeps_recent_usage_baseline_until_adoption(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            summary = update_recommendation_use_epochs(
                store=UsageStore(Path(temp_dir)),
                state=_state(),
                contexts=[_context()],
                portfolio=_portfolio(),
                workload=_workload(),
                now=NOW,
            )

        value = summary["value_summary"]
        self.assertEqual(value["mode"], "usage_baseline")
        self.assertEqual(value["current_model_configuration_id"], CURRENT_ID)
        self.assertIsNone(value["candidate_model_configuration_id"])
        self.assertIsNone(value["reference_cost_delta_usd"])
        self.assertIsNone(value["model_wait_delta_ms"])
        self.assertEqual(value["completed_work_unit_count"], 100)
        self.assertEqual(value["reference_cost_usd"], 1000.0)
        self.assertEqual(value["basis"], "recent_usage_all_configurations")

    def test_lightweight_refresh_preserves_the_last_value_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir))
            update_recommendation_use_epochs(
                store=store,
                state=_state(),
                contexts=[_context()],
                portfolio=_portfolio(decision="keep"),
                workload=_workload(),
                now=NOW,
            )

            summary = refresh_recommendation_use_observations(store=store)

        self.assertEqual(summary["value_summary"]["mode"], "usage_baseline")
        self.assertEqual(summary["value_summary"]["reference_cost_usd"], 1000.0)

    def test_observed_candidate_usage_takes_precedence_over_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir))
            update_recommendation_use_epochs(
                store=store,
                state=_state(),
                contexts=[_context()],
                portfolio=_portfolio(),
                workload=_workload(),
                now=NOW,
            )
            usage_state = store.load_usage_state()
            usage_state["observations"] = {
                "candidate": _observation(
                    "candidate",
                    ended_at=NOW + timedelta(minutes=10),
                    response_wait_ms=30_000,
                )
            }
            store.save_usage_state(usage_state)
            summary = refresh_recommendation_use_observations(
                store=store,
                state=_state(),
                workload=_workload(),
            )

        value = summary["value_summary"]
        self.assertEqual(value["mode"], "realized")
        self.assertEqual(value["completed_work_unit_count"], 1)
        self.assertLess(value["reference_cost_delta_usd"], 0)
        self.assertLess(value["model_wait_delta_ms"], 0)
        self.assertEqual(value["basis"], "observed_candidate_usage")

    def test_concurrent_refreshes_do_not_lose_new_observation_assignments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir))
            update_recommendation_use_epochs(
                store=store,
                state=_state(),
                contexts=[_context()],
                portfolio=_portfolio(),
                now=NOW,
            )
            store.save_usage_state(
                {
                    "schema_version": 1,
                    "files": {},
                    "observations": {
                        "first": _observation(
                            "first",
                            ended_at=NOW + timedelta(minutes=10),
                        )
                    },
                    "bootstrap_truncated": False,
                }
            )
            first_save_started = threading.Event()
            release_first_save = threading.Event()
            second_finished = threading.Event()
            original_save = store.save_recommendation_use_state
            save_calls = 0
            save_calls_lock = threading.Lock()

            def blocking_save(payload):  # type: ignore[no-untyped-def]
                nonlocal save_calls
                with save_calls_lock:
                    save_calls += 1
                    is_first = save_calls == 1
                if is_first:
                    first_save_started.set()
                    self.assertTrue(release_first_save.wait(timeout=2))
                original_save(payload)

            def refresh() -> None:
                refresh_recommendation_use_observations(store=store)

            def refresh_second() -> None:
                refresh()
                second_finished.set()

            with mock.patch.object(
                store,
                "save_recommendation_use_state",
                side_effect=blocking_save,
            ):
                first_thread = threading.Thread(target=refresh)
                first_thread.start()
                self.assertTrue(first_save_started.wait(timeout=2))
                usage_state = store.load_usage_state()
                usage_state["observations"]["second"] = _observation(  # type: ignore[index]
                    "second",
                    ended_at=NOW + timedelta(minutes=11),
                )
                store.save_usage_state(usage_state)
                second_thread = threading.Thread(target=refresh_second)
                second_thread.start()

                self.assertFalse(second_finished.wait(timeout=0.1))
                release_first_save.set()
                first_thread.join(timeout=2)
                second_thread.join(timeout=2)

            persisted = store.load_recommendation_use_state()
            self.assertEqual(
                set(persisted["observation_assignments"]),  # type: ignore[arg-type]
                {"first", "second"},
            )
            summary = refresh_recommendation_use_observations(store=store)
            self.assertEqual(
                summary["representative_epoch"]["observed_candidate_work_unit_count"],
                2,
            )

    def test_codex_rollout_usage_is_not_attributed_to_a_custom_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir))
            state = _state()
            ingress = state["config"]["model_ingress"]
            ingress["sources"] = [
                {
                    "id": "custom_endpoint",
                    "kind": "custom_endpoint",
                    "mode": "api",
                    "enabled": True,
                }
            ]
            ingress["connections"][0]["source_id"] = "custom_endpoint"
            ingress["connections"][0]["provider_id"] = "openai"
            update_recommendation_use_epochs(
                store=store,
                state=state,
                contexts=[_context()],
                portfolio=_portfolio(),
                now=NOW,
            )
            usage_state = store.load_usage_state()
            usage_state["observations"] = {
                "candidate": _observation(
                    "candidate", ended_at=NOW + timedelta(minutes=10)
                )
            }
            store.save_usage_state(usage_state)

            epoch = refresh_recommendation_use_observations(
                store=store
            )["representative_epoch"]

            self.assertEqual(epoch["observed_candidate_work_unit_count"], 0)
            self.assertEqual(epoch["attribution_route_basis"], "unsupported_route")

    def test_recommendation_opens_prospective_epoch_without_backfilling_old_usage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir))
            store.save_usage_state(
                {
                    "schema_version": 1,
                    "files": {},
                    "observations": {
                        "old": _observation("old", ended_at=NOW - timedelta(hours=1))
                    },
                    "bootstrap_truncated": False,
                }
            )

            summary = update_recommendation_use_epochs(
                store=store,
                state=_state(),
                contexts=[_context()],
                portfolio=_portfolio(),
                now=NOW,
            )

            epoch = summary["representative_epoch"]
            self.assertEqual(epoch["lifecycle_status"], "open")
            self.assertEqual(epoch["estimate_status"], "prospective")
            self.assertEqual(epoch["observed_candidate_work_unit_count"], 0)
            self.assertIsNone(epoch["estimated_reference_cost_delta_usd"])

    def test_disabled_candidate_closes_epoch_as_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir))
            update_recommendation_use_epochs(
                store=store,
                state=_state(),
                contexts=[_context()],
                portfolio=_portfolio(),
                now=NOW,
            )

            summary = update_recommendation_use_epochs(
                store=store,
                state=_state(candidate_enabled=False),
                contexts=[_context()],
                portfolio=_portfolio(decision="needs_test"),
                now=NOW + timedelta(minutes=10),
            )

            epoch = summary["epochs"][0]
            self.assertEqual(epoch["lifecycle_status"], "settling")
            self.assertEqual(epoch["end_reason"], "candidate_unavailable")
            self.assertIsNone(summary["representative_epoch"])

    def test_new_candidate_usage_is_assigned_once_and_cost_delta_is_signed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir))
            update_recommendation_use_epochs(
                store=store,
                state=_state(),
                contexts=[_context()],
                portfolio=_portfolio(),
                now=NOW,
            )
            usage_state = store.load_usage_state()
            usage_state["observations"] = {
                "candidate": _observation(
                    "candidate", ended_at=NOW + timedelta(minutes=10)
                ),
                "other": _observation(
                    "other",
                    ended_at=NOW + timedelta(minutes=11),
                    effort="xhigh",
                ),
            }
            store.save_usage_state(usage_state)

            first = refresh_recommendation_use_observations(store=store)
            second = refresh_recommendation_use_observations(store=store)

            epoch = first["representative_epoch"]
            self.assertEqual(epoch["observed_candidate_session_count"], 1)
            self.assertEqual(epoch["observed_candidate_work_unit_count"], 1)
            self.assertGreater(epoch["observed_candidate_reference_cost_usd"], 0)
            self.assertLess(epoch["estimated_reference_cost_delta_usd"], 0)
            self.assertEqual(epoch["estimate_status"], "estimated")
            self.assertIsNone(epoch["observed_candidate_response_wait_ms"])
            self.assertIsNone(epoch["estimated_model_wait_delta_ms"])
            self.assertEqual(first, second)

    def test_response_wait_is_used_only_when_explicitly_observed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir))
            update_recommendation_use_epochs(
                store=store,
                state=_state(),
                contexts=[_context()],
                portfolio=_portfolio(),
                now=NOW,
            )
            usage_state = store.load_usage_state()
            usage_state["observations"] = {
                "candidate": _observation(
                    "candidate",
                    ended_at=NOW + timedelta(minutes=10),
                    response_wait_ms=100_000,
                )
            }
            store.save_usage_state(usage_state)

            summary = refresh_recommendation_use_observations(store=store)
            epoch = summary["representative_epoch"]

            self.assertEqual(epoch["observed_candidate_response_wait_ms"], 100_000)
            self.assertEqual(epoch["estimated_model_wait_delta_ms"], -55_000)
            benefit = summary["benefit_summary"]
            self.assertEqual(benefit["observed_work_unit_count"], 1)
            self.assertEqual(benefit["reference_cost_work_unit_count"], 1)
            self.assertEqual(benefit["model_wait_work_unit_count"], 1)
            self.assertLess(benefit["reference_cost_delta_usd"], 0)
            self.assertEqual(benefit["model_wait_delta_ms"], -55_000)

    def test_benefit_summary_counts_completed_work_even_when_one_epoch_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir))
            update_recommendation_use_epochs(
                store=store,
                state=_state(),
                contexts=[_context(snapshot_id="snapshot-1")],
                portfolio=_portfolio(),
                now=NOW,
            )
            usage_state = store.load_usage_state()
            usage_state["observations"] = {
                "priced": _observation(
                    "priced",
                    ended_at=NOW + timedelta(minutes=10),
                )
            }
            store.save_usage_state(usage_state)
            update_recommendation_use_epochs(
                store=store,
                state=_state(),
                contexts=[
                    _context(
                        snapshot_id="snapshot-2",
                        candidate_cost_coverage="partial",
                    )
                ],
                portfolio=_portfolio(),
                now=NOW + timedelta(hours=6),
            )
            usage_state = store.load_usage_state()
            usage_state["observations"]["unpriced"] = _observation(
                "unpriced",
                ended_at=NOW + timedelta(hours=6, minutes=10),
            )
            store.save_usage_state(usage_state)

            benefit = refresh_recommendation_use_observations(
                store=store,
                now=NOW + timedelta(hours=6, minutes=11),
            )["benefit_summary"]

            self.assertEqual(benefit["status"], "estimated")
            self.assertEqual(benefit["observed_work_unit_count"], 2)
            self.assertEqual(benefit["reference_cost_work_unit_count"], 1)
            self.assertEqual(benefit["model_wait_work_unit_count"], 0)
            self.assertEqual(benefit["reference_cost_epoch_count"], 1)
            self.assertEqual(benefit["model_wait_epoch_count"], 0)
            self.assertEqual(
                benefit["latest_observed_at"],
                (NOW + timedelta(hours=6, minutes=10)).isoformat(),
            )

    def test_switch_to_recommended_configuration_keeps_epoch_open(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir))
            update_recommendation_use_epochs(
                store=store,
                state=_state(),
                contexts=[_context()],
                portfolio=_portfolio(),
                now=NOW,
            )
            switched_portfolio = {
                "schema_version": 2,
                "preference": "smart",
                "status": "keep",
                "representative_configuration_id": CANDIDATE_ID,
                "decisions": [
                    {
                        "current_model_configuration_id": CANDIDATE_ID,
                        "candidate_model_configuration_id": None,
                        "decision": "keep",
                    }
                ],
            }

            summary = update_recommendation_use_epochs(
                store=store,
                state=_state(),
                contexts=[_context()],
                portfolio=switched_portfolio,
                now=NOW + timedelta(minutes=5),
            )

            epoch = summary["epochs"][0]
            self.assertEqual(epoch["lifecycle_status"], "open")
            self.assertIsNone(epoch["ended_at"])

    def test_late_observation_is_absorbed_before_settling_epoch_closes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir))
            update_recommendation_use_epochs(
                store=store,
                state=_state(),
                contexts=[_context(snapshot_id="snapshot-1")],
                portfolio=_portfolio(),
                now=NOW,
            )
            changed = update_recommendation_use_epochs(
                store=store,
                state=_state(),
                contexts=[_context(snapshot_id="snapshot-2")],
                portfolio=_portfolio(),
                now=NOW + timedelta(hours=6),
            )
            self.assertEqual(changed["epochs"][0]["lifecycle_status"], "settling")

            usage_state = store.load_usage_state()
            usage_state["observations"] = {
                "late": _observation(
                    "late",
                    ended_at=NOW + timedelta(hours=5, minutes=59),
                )
            }
            store.save_usage_state(usage_state)
            settled = refresh_recommendation_use_observations(
                store=store,
                now=NOW + timedelta(hours=6, minutes=1),
            )
            self.assertEqual(
                settled["epochs"][0]["observed_candidate_work_unit_count"],
                1,
            )
            self.assertEqual(settled["epochs"][0]["lifecycle_status"], "settling")

            closed = refresh_recommendation_use_observations(
                store=store,
                now=NOW + timedelta(hours=6, minutes=3),
            )
            self.assertEqual(closed["epochs"][0]["lifecycle_status"], "closed")

    def test_late_observation_reconciles_into_a_closed_actual_switch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir))
            update_recommendation_use_epochs(
                store=store,
                state=_switch_chain_state(MAX_ID),
                contexts=[_switch_chain_context(MAX_ID)],
                portfolio=_switch_chain_portfolio(MAX_ID),
                now=NOW,
            )
            update_recommendation_use_epochs(
                store=store,
                state=_switch_chain_state(CURRENT_ID),
                contexts=[_switch_chain_context(CURRENT_ID)],
                portfolio=_switch_chain_portfolio(CURRENT_ID),
                now=NOW + timedelta(minutes=5),
            )
            update_recommendation_use_epochs(
                store=store,
                state=_switch_chain_state(CANDIDATE_ID),
                contexts=[_switch_chain_context(CANDIDATE_ID)],
                portfolio=_switch_chain_portfolio(CANDIDATE_ID),
                now=NOW + timedelta(minutes=10),
            )
            refresh_recommendation_use_observations(
                store=store,
                now=NOW + timedelta(minutes=13),
            )
            usage_state = store.load_usage_state()
            usage_state["observations"] = {
                "late-xhigh": _observation(
                    "late-xhigh",
                    effort="xhigh",
                    ended_at=NOW + timedelta(minutes=8),
                )
            }
            store.save_usage_state(usage_state)

            summary = refresh_recommendation_use_observations(
                store=store,
                now=NOW + timedelta(minutes=14),
            )

        closed_actual = next(
            epoch
            for epoch in summary["epochs"]
            if epoch["segment_kind"] == "actual_switch"
            and epoch["recommended_model_configuration_id"] == CURRENT_ID
        )
        self.assertEqual(closed_actual["lifecycle_status"], "closed")
        self.assertEqual(closed_actual["observed_candidate_work_unit_count"], 1)

    def test_completed_history_is_not_dropped_after_one_hundred_epochs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir))
            epochs = [
                {
                    "schema_version": 1,
                    "use_epoch_id": f"actual-{index}",
                    "segment_kind": "actual_switch",
                    "lifecycle_status": "closed",
                    "started_at": (NOW + timedelta(minutes=index)).isoformat(),
                    "ended_at": (
                        NOW + timedelta(minutes=index, seconds=30)
                    ).isoformat(),
                    "last_observed_at": (
                        NOW + timedelta(minutes=index, seconds=20)
                    ).isoformat(),
                    "observed_candidate_work_unit_count": 1,
                    "observed_candidate_reference_cost_usd": 0.1,
                    "observed_candidate_response_wait_ms": 1_000,
                    "estimated_reference_cost_delta_usd": -0.05,
                    "estimated_model_wait_delta_ms": -500,
                }
                for index in range(105)
            ]
            store.save_recommendation_use_state(
                {
                    "schema_version": 1,
                    "segment_contract_version": 2,
                    "epochs": epochs,
                    "observation_assignments": {},
                }
            )

            summary = refresh_recommendation_use_observations(
                store=store,
                now=NOW + timedelta(days=1),
            )

        self.assertEqual(len(summary["epochs"]), 105)
        self.assertEqual(summary["benefit_summary"]["observed_work_unit_count"], 105)
        self.assertAlmostEqual(
            summary["benefit_summary"]["reference_cost_delta_usd"],
            -5.25,
        )

    def test_failed_usage_is_counted_but_does_not_claim_realized_benefit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir))
            update_recommendation_use_epochs(
                store=store,
                state=_state(),
                contexts=[_context()],
                portfolio=_portfolio(),
                now=NOW,
            )
            usage_state = store.load_usage_state()
            usage_state["observations"] = {
                "failed": _observation(
                    "failed",
                    ended_at=NOW + timedelta(minutes=10),
                    outcome="failed",
                    response_wait_ms=20_000,
                )
            }
            store.save_usage_state(usage_state)

            summary = refresh_recommendation_use_observations(store=store)
            epoch = summary["representative_epoch"]
            self.assertGreater(epoch["observed_candidate_reference_cost_usd"], 0)
            self.assertEqual(epoch["observed_candidate_work_unit_count"], 0)
            self.assertIsNone(epoch["estimated_reference_cost_delta_usd"])
            self.assertIsNone(epoch["estimated_model_wait_delta_ms"])
            self.assertEqual(summary["benefit_summary"]["status"], "insufficient_work")

    def test_epoch_uses_frozen_candidate_rates_after_runtime_price_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir))
            update_recommendation_use_epochs(
                store=store,
                state=_state(),
                contexts=[_context()],
                portfolio=_portfolio(),
                now=NOW,
            )
            persisted = store.load_recommendation_use_state()
            self.assertIsInstance(
                persisted["epochs"][0].get("recommended_pricing"),
                dict,
            )
            usage_state = store.load_usage_state()
            usage_state["observations"] = {
                "candidate": _observation(
                    "candidate",
                    ended_at=NOW + timedelta(minutes=10),
                )
            }
            store.save_usage_state(usage_state)

            with mock.patch(
                "scanner.recommendation_use.estimate_reference_cost",
                side_effect=AssertionError("current prices must not be used"),
            ):
                epoch = refresh_recommendation_use_observations(
                    store=store
                )["representative_epoch"]

            self.assertGreater(epoch["observed_candidate_reference_cost_usd"], 0)
            self.assertLess(epoch["estimated_reference_cost_delta_usd"], 0)

    def test_partial_full_pack_cost_never_drives_actual_reference_estimate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir))
            update_recommendation_use_epochs(
                store=store,
                state=_state(),
                contexts=[_context(candidate_cost_coverage="partial")],
                portfolio=_portfolio(),
                now=NOW,
            )
            usage_state = store.load_usage_state()
            usage_state["observations"] = {
                "candidate": _observation(
                    "candidate", ended_at=NOW + timedelta(minutes=10)
                )
            }
            store.save_usage_state(usage_state)

            epoch = refresh_recommendation_use_observations(
                store=store
            )["representative_epoch"]

            self.assertEqual(epoch["estimate_status"], "unavailable")
            self.assertIsNone(epoch["estimated_reference_cost_delta_usd"])

    def test_new_snapshot_closes_old_epoch_and_keep_does_not_open_zero_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir))
            update_recommendation_use_epochs(
                store=store,
                state=_state(),
                contexts=[_context(snapshot_id="snapshot-1")],
                portfolio=_portfolio(),
                now=NOW,
            )
            changed = update_recommendation_use_epochs(
                store=store,
                state=_state(),
                contexts=[_context(snapshot_id="snapshot-2")],
                portfolio=_portfolio(),
                now=NOW + timedelta(hours=6),
            )
            self.assertEqual(len(changed["epochs"]), 2)
            self.assertEqual(changed["epochs"][0]["end_reason"], "new_evaluation_snapshot")
            self.assertEqual(changed["epochs"][0]["lifecycle_status"], "settling")

            kept = update_recommendation_use_epochs(
                store=store,
                state=_state(),
                contexts=[_context(snapshot_id="snapshot-2")],
                portfolio=_portfolio(decision="keep"),
                now=NOW + timedelta(hours=7),
            )
            self.assertEqual(len(kept["epochs"]), 2)
            self.assertIsNone(kept["representative_epoch"])
            self.assertEqual(kept["epochs"][1]["end_reason"], "recommendation_changed")

    def test_closing_snapshot_absorbs_the_last_observation_before_freezing_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir))
            update_recommendation_use_epochs(
                store=store,
                state=_state(),
                contexts=[_context(snapshot_id="snapshot-1")],
                portfolio=_portfolio(),
                now=NOW,
            )
            usage_state = store.load_usage_state()
            usage_state["observations"] = {
                "candidate": _observation(
                    "candidate", ended_at=NOW + timedelta(hours=5)
                )
            }
            store.save_usage_state(usage_state)

            changed = update_recommendation_use_epochs(
                store=store,
                state=_state(),
                contexts=[_context(snapshot_id="snapshot-2")],
                portfolio=_portfolio(),
                now=NOW + timedelta(hours=6),
            )

            self.assertEqual(changed["epochs"][0]["lifecycle_status"], "settling")
            self.assertEqual(changed["epochs"][0]["observed_candidate_work_unit_count"], 1)
            self.assertLess(changed["epochs"][0]["estimated_reference_cost_delta_usd"], 0)

    def test_ambiguous_model_and_effort_are_not_attributed_across_routes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir))
            update_recommendation_use_epochs(
                store=store,
                state=_state(duplicate_candidate=True),
                contexts=[_context()],
                portfolio=_portfolio(),
                now=NOW,
            )
            usage_state = store.load_usage_state()
            usage_state["observations"] = {
                "candidate": _observation(
                    "candidate", ended_at=NOW + timedelta(minutes=10)
                )
            }
            store.save_usage_state(usage_state)

            epoch = refresh_recommendation_use_observations(
                store=store
            )["representative_epoch"]

            self.assertEqual(epoch["observed_candidate_work_unit_count"], 0)
            self.assertEqual(epoch["estimate_status"], "prospective")

    def test_session_started_before_recommendation_is_not_counted_as_adoption(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir))
            update_recommendation_use_epochs(
                store=store,
                state=_state(),
                contexts=[_context()],
                portfolio=_portfolio(),
                now=NOW,
            )
            crossing = _observation(
                "crossing", ended_at=NOW + timedelta(seconds=30)
            )
            crossing["started_at"] = (NOW - timedelta(minutes=5)).isoformat()
            usage_state = store.load_usage_state()
            usage_state["observations"] = {"crossing": crossing}
            store.save_usage_state(usage_state)

            epoch = refresh_recommendation_use_observations(
                store=store
            )["representative_epoch"]

            self.assertEqual(epoch["observed_candidate_session_count"], 0)
            self.assertEqual(epoch["estimate_status"], "prospective")

    def test_session_already_using_candidate_before_recommendation_is_not_attributed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir))
            update_recommendation_use_epochs(
                store=store,
                state=_state(),
                contexts=[_context()],
                portfolio=_portfolio(),
                workload=_workload(),
                now=NOW,
            )
            session_key = "session:already-on-candidate"
            usage_state = store.load_usage_state()
            usage_state["observations"] = {
                "before": _observation(
                    "before",
                    ended_at=NOW - timedelta(minutes=1),
                    session_key=session_key,
                ),
                "after": _observation(
                    "after",
                    ended_at=NOW + timedelta(minutes=10),
                    session_key=session_key,
                ),
            }
            store.save_usage_state(usage_state)
            persisted = store.load_recommendation_use_state()
            epoch_id = persisted["epochs"][0]["use_epoch_id"]
            persisted["epochs"][0].update(
                {
                    "last_observed_at": usage_state["observations"]["after"]["ended_at"],
                    "observed_candidate_session_keys": [session_key],
                    "observed_candidate_session_count": 1,
                    "observed_candidate_work_unit_count": 1,
                    "observed_candidate_reference_cost_usd": 0.10,
                    "estimate_status": "estimated",
                }
            )
            persisted["observation_assignments"] = {"after": epoch_id}
            store.save_recommendation_use_state(persisted)

            summary = refresh_recommendation_use_observations(
                store=store,
                state=_state(),
                workload=_workload(),
            )

            epoch = summary["representative_epoch"]
            self.assertEqual(epoch["observed_candidate_session_count"], 0)
            self.assertEqual(epoch["observed_candidate_work_unit_count"], 0)
            self.assertNotEqual(summary["value_summary"]["mode"], "realized")
            self.assertEqual(store.load_recommendation_use_state()["observation_assignments"], {})


if __name__ == "__main__":
    unittest.main()
