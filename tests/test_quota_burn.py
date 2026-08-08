from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from scanner.quota_burn import build_quota_burn_summary
from scanner.usage_store import UsageStore


RESET_AT = "2026-07-24T12:00:00Z"


def _account_snapshot(
    captured_at: str,
    used_percent: float,
    *,
    resets_at: str = RESET_AT,
    window_id: str = "primary",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "captured_at": captured_at,
        "source": "codex_app_server",
        "account_type": "chatgpt",
        "login_state": "authenticated",
        "plan_type": "pro",
        "quota_status": "available",
        "quota_windows": [
            {
                "window_id": window_id,
                "label": "5h",
                "limit_id": "codex",
                "used_percent": used_percent,
                "window_seconds": 18_000,
                "resets_at": resets_at,
            }
        ],
        "usage_status": "available",
        "usage_summary": {"lifetime_tokens": 123_456},
        "daily_usage": [{"start_date": "2026-07-24", "tokens": 4_000}],
        "unavailable_capabilities": [],
    }


def _account_snapshot_with_windows(
    captured_at: str,
    windows: list[dict[str, object]],
) -> dict[str, object]:
    snapshot = _account_snapshot(captured_at, 0)
    snapshot["quota_windows"] = windows
    return snapshot


def _observation(
    index: int,
    started_at: str,
    ended_at: str,
    *,
    model: str = "gpt-5.6-terra",
    effort: str = "high",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "observation_id": f"sha256:{index}",
        "model_configuration_id": f"codex:openai:{model}:{effort}",
        "provider_id": "openai",
        "raw_model_id": model,
        "reasoning_effort": effort,
        "started_at": started_at,
        "ended_at": ended_at,
        "outcome": "completed",
        "is_subagent": False,
        "is_modeldial_evaluation": False,
        "attribution_confidence": 1.0,
        "exclusion_reasons": [],
    }


def _usage_state(observations: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "files": {},
        "observations": {
            str(item["observation_id"]): item for item in observations
        },
        "bootstrap_truncated": False,
        "coverage_continuous_since": None,
    }


def _workload_summary() -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "available",
        "period_start": "2026-07-24T07:00:00Z",
        "period_end": "2026-07-24T11:00:00Z",
        "coverage_complete": True,
        "coverage_continuous_since": None,
    }


class QuotaBurnTest(unittest.TestCase):
    def test_account_history_keeps_only_minimal_quota_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir))
            store.save_account_snapshot(
                _account_snapshot("2026-04-01T08:00:00Z", 1)
            )
            current = _account_snapshot("2026-07-24T08:00:00Z", 10)
            store.save_account_snapshot(current)
            history = store.load_account_snapshots()

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["captured_at"], "2026-07-24T08:00:00Z")
        self.assertEqual(
            set(history[0]),
            {
                "schema_version",
                "captured_at",
                "source",
                "account_type",
                "login_state",
                "plan_type",
                "quota_status",
                "quota_windows",
            },
        )
        self.assertNotIn("usage_summary", str(history))
        self.assertNotIn("daily_usage", str(history))

    def test_clean_same_window_intervals_produce_attributed_model_burn(self) -> None:
        snapshots = [
            _account_snapshot("2026-07-24T08:00:00Z", 10),
            _account_snapshot("2026-07-24T08:30:00Z", 12),
            _account_snapshot("2026-07-24T09:00:00Z", 12),
            _account_snapshot("2026-07-24T09:30:00Z", 14),
            _account_snapshot("2026-07-24T10:00:00Z", 14),
            _account_snapshot("2026-07-24T10:30:00Z", 16),
        ]
        observations = [
            _observation(1, "2026-07-24T08:05:00Z", "2026-07-24T08:10:00Z"),
            _observation(2, "2026-07-24T08:15:00Z", "2026-07-24T08:20:00Z"),
            _observation(3, "2026-07-24T09:05:00Z", "2026-07-24T09:10:00Z"),
            _observation(4, "2026-07-24T09:15:00Z", "2026-07-24T09:20:00Z"),
            _observation(5, "2026-07-24T10:05:00Z", "2026-07-24T10:10:00Z"),
            _observation(6, "2026-07-24T10:15:00Z", "2026-07-24T10:20:00Z"),
        ]

        summary = build_quota_burn_summary(
            snapshots,
            _usage_state(observations),
            _workload_summary(),
        )

        self.assertEqual(summary["status"], "available")
        self.assertEqual(summary["attributed_interval_count"], 3)
        aggregate = summary["aggregates"][0]
        self.assertEqual(aggregate["raw_model_id"], "gpt-5.6-terra")
        self.assertEqual(aggregate["reasoning_effort"], "high")
        self.assertEqual(aggregate["attributed_work_units"], 6)
        self.assertEqual(aggregate["attributed_interval_count"], 3)
        self.assertEqual(aggregate["quota_per_work_unit_percent"]["median"], 1.0)
        self.assertEqual(aggregate["quota_per_work_unit_percent"]["p25"], 1.0)
        self.assertEqual(aggregate["quota_per_work_unit_percent"]["p75"], 1.0)
        self.assertTrue(aggregate["usable_for_recommendation"])
        self.assertEqual(aggregate["confidence"], 0.55)

    def test_new_short_window_does_not_break_existing_weekly_attribution(self) -> None:
        weekly_before = {
            "window_id": "primary",
            "label": "weekly",
            "limit_id": "codex",
            "used_percent": 10,
            "window_seconds": 604_800,
            "resets_at": "2026-07-31T00:00:00Z",
        }
        five_hour_after = {
            "window_id": "codex:300m",
            "label": "5h",
            "limit_id": "codex",
            "used_percent": 1,
            "window_seconds": 18_000,
            "resets_at": "2026-07-24T13:00:00Z",
        }
        weekly_after = {
            **weekly_before,
            "window_id": "codex:10080m",
            "used_percent": 12,
        }

        summary = build_quota_burn_summary(
            [
                _account_snapshot_with_windows(
                    "2026-07-24T08:00:00Z", [weekly_before]
                ),
                _account_snapshot_with_windows(
                    "2026-07-24T08:30:00Z",
                    [five_hour_after, weekly_after],
                ),
            ],
            _usage_state(
                [_observation(1, "2026-07-24T08:05:00Z", "2026-07-24T08:20:00Z")]
            ),
            _workload_summary(),
        )

        self.assertEqual(summary["attributed_interval_count"], 1)
        self.assertEqual(len(summary["aggregates"]), 1)
        self.assertEqual(summary["aggregates"][0]["window_id"], "codex:10080m")
        self.assertEqual(
            summary["aggregates"][0]["quota_per_work_unit_percent"]["median"],
            2.0,
        )

    def test_cross_reset_counter_decrease_and_mixed_models_are_rejected(self) -> None:
        cases = [
            (
                [
                    _account_snapshot("2026-07-24T08:00:00Z", 10),
                    _account_snapshot(
                        "2026-07-24T08:30:00Z",
                        2,
                        resets_at="2026-07-24T17:00:00Z",
                    ),
                ],
                [_observation(1, "2026-07-24T08:05:00Z", "2026-07-24T08:20:00Z")],
                "window_changed",
            ),
            (
                [
                    _account_snapshot("2026-07-24T08:00:00Z", 10),
                    _account_snapshot("2026-07-24T08:30:00Z", 8),
                ],
                [_observation(1, "2026-07-24T08:05:00Z", "2026-07-24T08:20:00Z")],
                "counter_decreased",
            ),
            (
                [
                    _account_snapshot("2026-07-24T08:00:00Z", 10),
                    _account_snapshot("2026-07-24T08:30:00Z", 12),
                ],
                [
                    _observation(1, "2026-07-24T08:05:00Z", "2026-07-24T08:10:00Z"),
                    _observation(
                        2,
                        "2026-07-24T08:15:00Z",
                        "2026-07-24T08:20:00Z",
                        model="gpt-5.6-sol",
                        effort="xhigh",
                    ),
                ],
                "mixed_model_configuration",
            ),
        ]

        for snapshots, observations, reason in cases:
            with self.subTest(reason=reason):
                summary = build_quota_burn_summary(
                    snapshots,
                    _usage_state(observations),
                    _workload_summary(),
                )
                self.assertEqual(summary["status"], "collecting")
                self.assertEqual(summary["aggregates"], [])
                self.assertGreater(summary["rejected_intervals"].get(reason, 0), 0)

    def test_rounded_zero_delta_is_not_treated_as_zero_consumption(self) -> None:
        summary = build_quota_burn_summary(
            [
                _account_snapshot("2026-07-24T08:00:00Z", 10),
                _account_snapshot("2026-07-24T08:30:00Z", 10),
            ],
            _usage_state(
                [_observation(1, "2026-07-24T08:05:00Z", "2026-07-24T08:20:00Z")]
            ),
            _workload_summary(),
        )

        self.assertEqual(summary["status"], "collecting")
        self.assertEqual(summary["aggregates"], [])
        self.assertEqual(summary["rejected_intervals"]["below_resolution"], 1)

    def test_concurrent_main_work_is_not_attributed(self) -> None:
        summary = build_quota_burn_summary(
            [
                _account_snapshot("2026-07-24T08:00:00Z", 10),
                _account_snapshot("2026-07-24T08:30:00Z", 12),
            ],
            _usage_state(
                [
                    _observation(1, "2026-07-24T08:05:00Z", "2026-07-24T08:20:00Z"),
                    _observation(2, "2026-07-24T08:10:00Z", "2026-07-24T08:25:00Z"),
                ]
            ),
            _workload_summary(),
        )

        self.assertEqual(summary["aggregates"], [])
        self.assertEqual(summary["rejected_intervals"]["concurrent_main_work"], 1)

    def test_incomplete_usage_coverage_is_not_attributed(self) -> None:
        workload = {
            **_workload_summary(),
            "coverage_complete": False,
            "coverage_continuous_since": "2026-07-24T08:15:00Z",
        }
        summary = build_quota_burn_summary(
            [
                _account_snapshot("2026-07-24T08:00:00Z", 10),
                _account_snapshot("2026-07-24T08:30:00Z", 12),
            ],
            _usage_state(
                [_observation(1, "2026-07-24T08:16:00Z", "2026-07-24T08:20:00Z")]
            ),
            workload,
        )

        self.assertEqual(summary["aggregates"], [])
        self.assertEqual(summary["rejected_intervals"]["coverage_gap"], 1)


if __name__ == "__main__":
    unittest.main()
