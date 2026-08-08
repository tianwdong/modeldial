from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from scanner.costing import estimate_reference_cost
from scanner.usage_observer import observe_codex_usage
from scanner.usage_store import UsageStore


def _event(timestamp: str, event_type: str, payload: dict[str, object]) -> str:
    return json.dumps(
        {"timestamp": timestamp, "type": event_type, "payload": payload},
        ensure_ascii=False,
    )


class UsageObserverTest(unittest.TestCase):
    def test_observer_reports_stable_privacy_safe_collection_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            (sessions_root / "rollout-valid.jsonl").write_text(
                _event(
                    "2026-07-24T07:00:00Z",
                    "session_meta",
                    {"id": "private-session-id", "cwd": "/private/workspace"},
                )
                + "\n",
                encoding="utf-8",
            )
            (sessions_root / "rollout-invalid.jsonl").write_text(
                '{"type":"session_meta","payload":\n',
                encoding="utf-8",
            )
            store = UsageStore(root / "data")
            now = datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc)

            first = observe_codex_usage(
                sessions_root=sessions_root,
                store=store,
                now=now,
            )
            second = observe_codex_usage(
                sessions_root=sessions_root,
                store=store,
                now=now,
            )

        expected = {
            "source_count": 1,
            "discovered_file_count": 2,
            "sampled_file_count": 2,
            "parsed_file_count": 1,
            "failed_file_count": 1,
            "unknown_file_count": 0,
            "deduplicated_file_count": 0,
            "budget_limited_file_count": 0,
            "gap_detected": False,
            "upstream_retention_risk": "unknown",
        }
        self.assertEqual(first["collection"], expected)
        self.assertEqual(second["collection"], expected)
        serialized = json.dumps(first, ensure_ascii=False)
        self.assertNotIn("private-session-id", serialized)
        self.assertNotIn("/private/workspace", serialized)

    def test_legacy_system_observations_are_excluded_even_without_reason_tags(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            store = UsageStore(root / "data")
            store.save_usage_state(
                {
                    "schema_version": 1,
                    "files": {},
                    "observations": {
                        "legacy-review": {
                            "model_configuration_id": "codex:openai:codex-auto-review:low",
                            "raw_model_id": "codex-auto-review",
                            "reasoning_effort": "low",
                            "started_at": "2026-07-24T07:00:00Z",
                            "ended_at": "2026-07-24T07:01:00Z",
                            "outcome": "completed",
                            "usage": {"input_tokens": 10, "output_tokens": 2},
                            "exclusion_reasons": [],
                        },
                        "legacy-evaluation": {
                            "model_configuration_id": "codex:openai:gpt-5.6-terra:high",
                            "raw_model_id": "gpt-5.6-terra",
                            "reasoning_effort": "high",
                            "started_at": "2026-07-24T07:00:00Z",
                            "ended_at": "2026-07-24T07:01:00Z",
                            "outcome": "completed",
                            "usage": {"input_tokens": 10, "output_tokens": 2},
                            "is_modeldial_evaluation": True,
                            "exclusion_reasons": [],
                        },
                    },
                    "bootstrap_truncated": False,
                    "coverage_continuous_since": None,
                }
            )

            summary = observe_codex_usage(
                sessions_root=sessions_root,
                store=store,
                now=datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc),
            )

        self.assertEqual(summary["observation_count"], 0)
        self.assertEqual(summary["excluded_observation_count"], 2)
        self.assertEqual(summary["aggregates"], [])

    def test_observer_aggregates_turn_usage_and_deduplicates_unchanged_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            rollout = sessions_root / "rollout-test.jsonl"
            rollout.write_text(
                "\n".join(
                    [
                        _event(
                            "2026-07-24T07:00:00Z",
                            "session_meta",
                            {
                                "id": "raw-session-id",
                                "cwd": "/private/workspace",
                                "model_provider": "OpenAI",
                                "source": "desktop",
                            },
                        ),
                        _event(
                            "2026-07-24T07:01:00Z",
                            "event_msg",
                            {"type": "task_started", "turn_id": "raw-turn-id"},
                        ),
                        _event(
                            "2026-07-24T07:01:01Z",
                            "turn_context",
                            {
                                "turn_id": "raw-turn-id",
                                "model": "gpt-5.6-terra",
                                "effort": "high",
                            },
                        ),
                        _event(
                            "2026-07-24T07:01:10Z",
                            "event_msg",
                            {
                                "type": "token_count",
                                "info": {
                                    "last_token_usage": {
                                        "input_tokens": 100,
                                        "cached_input_tokens": 60,
                                        "cache_write_input_tokens": 5,
                                        "output_tokens": 20,
                                        "reasoning_output_tokens": 7,
                                    }
                                },
                            },
                        ),
                        _event(
                            "2026-07-24T07:01:20Z",
                            "event_msg",
                            {
                                "type": "token_count",
                                "info": {
                                    "last_token_usage": {
                                        "input_tokens": 50,
                                        "cached_input_tokens": 20,
                                        "output_tokens": 10,
                                        "reasoning_output_tokens": 3,
                                    }
                                },
                            },
                        ),
                        _event(
                            "2026-07-24T07:02:00Z",
                            "event_msg",
                            {
                                "type": "task_complete",
                                "turn_id": "raw-turn-id",
                                "duration_ms": 60000,
                            },
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            store = UsageStore(root / "data")
            now = datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc)

            first = observe_codex_usage(
                sessions_root=sessions_root,
                store=store,
                now=now,
            )
            second = observe_codex_usage(
                sessions_root=sessions_root,
                store=store,
                now=now,
            )

            self.assertEqual(first, second)
            self.assertEqual(first["observation_count"], 1)
            aggregate = first["aggregates"][0]
            self.assertEqual(aggregate["raw_model_id"], "gpt-5.6-terra")
            self.assertEqual(aggregate["reasoning_effort"], "high")
            self.assertEqual(aggregate["completed_work_units"], 1)
            self.assertEqual(aggregate["input_tokens"], 150)
            self.assertEqual(aggregate["cached_input_tokens"], 80)
            self.assertEqual(aggregate["cache_write_input_tokens"], 5)
            self.assertEqual(aggregate["output_tokens"], 30)
            self.assertEqual(aggregate["reasoning_tokens"], 10)
            self.assertEqual(aggregate["reference_cost_status"], "estimated")
            self.assertGreater(aggregate["reference_cost_usd"], 0)
            self.assertIsNotNone(aggregate["reference_cost_pricing_snapshot_id"])
            self.assertIsNone(aggregate["response_wait_ms"])
            self.assertEqual(aggregate["response_wait_work_unit_count"], 0)
            self.assertEqual(aggregate["active_duration_ms"], 60000)
            self.assertEqual(aggregate["median_active_duration_ms"], 60000)
            self.assertEqual(aggregate["behavior_observed_work_units"], 0)
            self.assertEqual(aggregate["behavior_coverage_percent"], 0.0)
            self.assertEqual(aggregate["edit_work_units"], 0)
            self.assertEqual(aggregate["retry_observed_edit_work_units"], 0)
            self.assertIsNone(aggregate["retry_count"])
            self.assertIsNone(aggregate["standard_cost_per_edit_usd"])
            self.assertEqual(aggregate["standard_cost_status"], "unavailable")
            self.assertIsNone(aggregate["pricing_snapshot_id"])

            stored = store.usage_state_path.read_text(encoding="utf-8")
            self.assertNotIn("raw-session-id", stored)
            self.assertNotIn("raw-turn-id", stored)
            self.assertNotIn("/private/workspace", stored)
            self.assertNotIn(str(rollout), stored)

    def test_observer_sums_only_model_wait_boundaries_and_excludes_tool_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            rollout = sessions_root / "rollout-test.jsonl"
            rollout.write_text(
                "\n".join(
                    [
                        _event(
                            "2026-07-24T07:00:00Z",
                            "session_meta",
                            {"id": "session-1", "model_provider": "OpenAI"},
                        ),
                        _event(
                            "2026-07-24T07:01:00Z",
                            "event_msg",
                            {"type": "task_started", "turn_id": "turn-1"},
                        ),
                        _event(
                            "2026-07-24T07:01:00.500Z",
                            "turn_context",
                            {
                                "turn_id": "turn-1",
                                "model": "gpt-5.6-sol",
                                "effort": "high",
                            },
                        ),
                        _event(
                            "2026-07-24T07:01:01Z",
                            "response_item",
                            {"type": "message", "role": "user", "content": []},
                        ),
                        _event(
                            "2026-07-24T07:01:11Z",
                            "response_item",
                            {
                                "type": "function_call",
                                "name": "exec_command",
                                "arguments": "{}",
                            },
                        ),
                        _event(
                            "2026-07-24T07:01:31Z",
                            "response_item",
                            {
                                "type": "function_call_output",
                                "call_id": "call-1",
                                "output": "done",
                            },
                        ),
                        _event(
                            "2026-07-24T07:01:41Z",
                            "response_item",
                            {
                                "type": "message",
                                "role": "assistant",
                                "content": [],
                            },
                        ),
                        _event(
                            "2026-07-24T07:01:42Z",
                            "event_msg",
                            {
                                "type": "token_count",
                                "info": {
                                    "last_token_usage": {
                                        "input_tokens": 100,
                                        "output_tokens": 20,
                                    }
                                },
                            },
                        ),
                        _event(
                            "2026-07-24T07:01:43Z",
                            "event_msg",
                            {"type": "task_complete", "duration_ms": 43_000},
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            store = UsageStore(root / "data")

            summary = observe_codex_usage(
                sessions_root=sessions_root,
                store=store,
                now=datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc),
            )

            observation = next(
                iter(store.load_usage_state()["observations"].values())
            )
            self.assertEqual(observation["response_wait_ms"], 20_000)
            self.assertEqual(observation["response_wait_sample_count"], 2)
            self.assertEqual(observation["active_duration_ms"], 43_000)
            aggregate = summary["aggregates"][0]
            self.assertEqual(aggregate["response_wait_ms"], 20_000)
            self.assertEqual(aggregate["response_wait_work_unit_count"], 1)

    def test_observer_persists_only_derived_behavior_and_edit_efficiency(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            rollout = sessions_root / "rollout-test.jsonl"
            secret_message = "请修复 private-secret-value 这个错误"
            secret_path = "/private/project/secret_app.py"
            secret_command = "python -m unittest private_secret_test"
            patch = (
                "*** Begin Patch\n"
                f"*** Update File: {secret_path}\n"
                "@@\n"
                "*** End Patch"
            )
            rollout.write_text(
                "\n".join(
                    [
                        _event(
                            "2026-07-24T07:00:00Z",
                            "session_meta",
                            {"id": "session-1", "model_provider": "OpenAI"},
                        ),
                        _event(
                            "2026-07-24T07:01:00Z",
                            "turn_context",
                            {
                                "turn_id": "turn-1",
                                "model": "gpt-5.6-terra",
                                "effort": "high",
                            },
                        ),
                        _event(
                            "2026-07-24T07:01:01Z",
                            "response_item",
                            {
                                "type": "message",
                                "role": "user",
                                "content": [
                                    {"type": "input_text", "text": secret_message}
                                ],
                            },
                        ),
                        _event(
                            "2026-07-24T07:01:10Z",
                            "response_item",
                            {
                                "type": "function_call",
                                "name": "apply_patch",
                                "arguments": json.dumps({"patch": patch}),
                            },
                        ),
                        _event(
                            "2026-07-24T07:01:20Z",
                            "response_item",
                            {
                                "type": "function_call",
                                "name": "exec_command",
                                "arguments": json.dumps({"cmd": secret_command}),
                            },
                        ),
                        _event(
                            "2026-07-24T07:01:30Z",
                            "response_item",
                            {
                                "type": "function_call",
                                "name": "apply_patch",
                                "arguments": json.dumps({"patch": patch}),
                            },
                        ),
                        _event(
                            "2026-07-24T07:01:40Z",
                            "event_msg",
                            {
                                "type": "token_count",
                                "info": {
                                    "last_token_usage": {
                                        "input_tokens": 100,
                                        "output_tokens": 20,
                                        "reasoning_output_tokens": 5,
                                    }
                                },
                            },
                        ),
                        _event(
                            "2026-07-24T07:02:00Z",
                            "event_msg",
                            {"type": "task_complete", "duration_ms": 60000},
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            store = UsageStore(root / "data")

            summary = observe_codex_usage(
                sessions_root=sessions_root,
                store=store,
                now=datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc),
            )

            aggregate = summary["aggregates"][0]
            self.assertEqual(aggregate["behavior_observed_work_units"], 1)
            self.assertEqual(aggregate["behavior_coverage_percent"], 100.0)
            self.assertEqual(aggregate["task_category_counts"], {"debugging": 1})
            self.assertEqual(aggregate["edit_work_units"], 1)
            self.assertEqual(aggregate["retry_observed_edit_work_units"], 1)
            self.assertEqual(aggregate["one_shot_edit_work_units"], 0)
            self.assertEqual(aggregate["one_shot_rate_percent"], 0.0)
            self.assertEqual(aggregate["retry_count"], 1)
            self.assertEqual(aggregate["retries_per_edit"], 1.0)
            self.assertEqual(aggregate["edit_usage"]["input_tokens"], 100)
            self.assertEqual(aggregate["edit_usage"]["output_tokens"], 20)
            self.assertEqual(aggregate["standard_cost_status"], "estimated")
            expected_cost = estimate_reference_cost(
                "gpt-5.6-terra",
                input_tokens=100,
                cached_input_tokens=None,
                output_tokens=20,
                reasoning_output_tokens=5,
            )
            self.assertEqual(expected_cost.status, "estimated")
            self.assertIsNotNone(expected_cost.usd)
            self.assertAlmostEqual(
                aggregate["standard_cost_per_edit_usd"],
                expected_cost.usd,
            )

            stored_payload = json.loads(store.usage_state_path.read_text(encoding="utf-8"))
            observation = next(iter(stored_payload["observations"].values()))
            self.assertEqual(observation["task_category"], "debugging")
            self.assertTrue(observation["has_edits"])
            self.assertEqual(observation["retry_count"], 1)
            self.assertFalse(observation["one_shot"])
            stored = json.dumps(stored_payload, ensure_ascii=False)
            self.assertNotIn(secret_message, stored)
            self.assertNotIn(secret_path, stored)
            self.assertNotIn(secret_command, stored)
            self.assertNotIn("private-secret-value", stored)

    def test_observer_reads_only_appended_events_after_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            rollout = sessions_root / "rollout-test.jsonl"
            rollout.write_text(
                "\n".join(
                    [
                        _event(
                            "2026-07-24T07:00:00Z",
                            "session_meta",
                            {
                                "id": "session-1",
                                "model_provider": "custom-gateway",
                            },
                        ),
                        _event(
                            "2026-07-24T07:01:00Z",
                            "turn_context",
                            {
                                "turn_id": "turn-1",
                                "model": "model-a",
                                "effort": "medium",
                            },
                        ),
                        _event(
                            "2026-07-24T07:01:10Z",
                            "event_msg",
                            {
                                "type": "token_count",
                                "info": {
                                    "last_token_usage": {
                                        "input_tokens": 10,
                                        "output_tokens": 2,
                                    }
                                },
                            },
                        ),
                        _event(
                            "2026-07-24T07:01:20Z",
                            "event_msg",
                            {"type": "task_complete", "duration_ms": 20000},
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            store = UsageStore(root / "data")
            now = datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc)
            initial = observe_codex_usage(
                sessions_root=sessions_root,
                store=store,
                now=now,
            )

            with rollout.open("a", encoding="utf-8") as handle:
                handle.write(
                    "\n".join(
                        [
                            _event(
                                "2026-07-24T07:30:00Z",
                                "turn_context",
                                {
                                    "turn_id": "turn-2",
                                    "model": "model-a",
                                    "effort": "medium",
                                },
                            ),
                            _event(
                                "2026-07-24T07:30:10Z",
                                "event_msg",
                                {
                                    "type": "token_count",
                                    "info": {
                                        "last_token_usage": {
                                            "input_tokens": 20,
                                            "output_tokens": 4,
                                        }
                                    },
                                },
                            ),
                            _event(
                                "2026-07-24T07:30:20Z",
                                "event_msg",
                                {"type": "task_complete", "duration_ms": 20000},
                            ),
                        ]
                    )
                    + "\n"
                )

            updated = observe_codex_usage(
                sessions_root=sessions_root,
                store=store,
                now=now,
            )

            self.assertEqual(initial["observation_count"], 1)
            self.assertEqual(updated["observation_count"], 2)
            aggregate = updated["aggregates"][0]
            self.assertEqual(aggregate["provider_id"], "custom-gateway")
            self.assertEqual(aggregate["input_tokens"], 30)
            self.assertEqual(aggregate["output_tokens"], 6)

    def test_duplicate_cumulative_event_split_across_scans_is_counted_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            rollout = sessions_root / "rollout-test.jsonl"
            token_event = _event(
                "2026-07-24T07:01:10Z",
                "event_msg",
                {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {
                            "input_tokens": 100,
                            "cached_input_tokens": 60,
                            "output_tokens": 20,
                            "reasoning_output_tokens": 7,
                            "total_tokens": 120,
                        },
                        "total_token_usage": {
                            "input_tokens": 100,
                            "cached_input_tokens": 60,
                            "output_tokens": 20,
                            "reasoning_output_tokens": 7,
                            "total_tokens": 120,
                        },
                    },
                },
            )
            rollout.write_text(
                "\n".join(
                    [
                        _event(
                            "2026-07-24T07:00:00Z",
                            "session_meta",
                            {"id": "session-1", "model_provider": "OpenAI"},
                        ),
                        _event(
                            "2026-07-24T07:01:00Z",
                            "turn_context",
                            {
                                "turn_id": "turn-1",
                                "model": "gpt-5.6-terra",
                                "effort": "high",
                            },
                        ),
                        token_event,
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            store = UsageStore(root / "data")
            now = datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc)
            observe_codex_usage(sessions_root=sessions_root, store=store, now=now)

            with rollout.open("a", encoding="utf-8") as handle:
                handle.write(
                    "\n".join(
                        [
                            token_event,
                            _event(
                                "2026-07-24T07:02:00Z",
                                "event_msg",
                                {"type": "task_complete", "duration_ms": 60000},
                            ),
                        ]
                    )
                    + "\n"
                )

            summary = observe_codex_usage(
                sessions_root=sessions_root,
                store=store,
                now=now,
            )

            aggregate = summary["aggregates"][0]
            self.assertEqual(aggregate["input_tokens"], 100)
            self.assertEqual(aggregate["cached_input_tokens"], 60)
            self.assertEqual(aggregate["output_tokens"], 20)
            self.assertEqual(aggregate["reasoning_tokens"], 7)

    def test_total_usage_fallback_tracks_mixed_events_without_double_counting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            rollout = sessions_root / "rollout-test.jsonl"
            totals = [
                ({"input_tokens": 10, "output_tokens": 2}, {"input_tokens": 10, "output_tokens": 2}),
                (None, {"input_tokens": 15, "output_tokens": 3}),
                (None, {"input_tokens": 15, "output_tokens": 3}),
                ({"input_tokens": 4, "output_tokens": 1}, {"input_tokens": 19, "output_tokens": 4}),
                (None, {"input_tokens": 23, "output_tokens": 6}),
            ]
            events = [
                _event(
                    "2026-07-24T07:00:00Z",
                    "session_meta",
                    {"id": "session-1", "model_provider": "OpenAI"},
                ),
                _event(
                    "2026-07-24T07:01:00Z",
                    "turn_context",
                    {
                        "turn_id": "turn-1",
                        "model": "gpt-5.6-terra",
                        "effort": "high",
                    },
                ),
            ]
            for index, (last, total) in enumerate(totals):
                info = {"total_token_usage": total}
                if last is not None:
                    info["last_token_usage"] = last
                events.append(
                    _event(
                        f"2026-07-24T07:01:{10 + index:02d}Z",
                        "event_msg",
                        {"type": "token_count", "info": info},
                    )
                )
            events.append(
                _event(
                    "2026-07-24T07:02:00Z",
                    "event_msg",
                    {"type": "task_complete", "duration_ms": 60000},
                )
            )
            rollout.write_text("\n".join(events) + "\n", encoding="utf-8")

            summary = observe_codex_usage(
                sessions_root=sessions_root,
                store=UsageStore(root / "data"),
                now=datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc),
            )

            aggregate = summary["aggregates"][0]
            self.assertEqual(aggregate["input_tokens"], 23)
            self.assertEqual(aggregate["output_tokens"], 6)

    def test_fork_replay_uses_parent_namespace_but_keeps_new_turns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()

            def write_session(
                path: Path,
                *,
                session_id: str,
                forked_from_id: str | None,
                turns: list[tuple[str, int, int]],
            ) -> None:
                session_meta: dict[str, object] = {
                    "id": session_id,
                    "model_provider": "OpenAI",
                }
                if forked_from_id is not None:
                    session_meta["forked_from_id"] = forked_from_id
                events = [_event("2026-07-24T07:00:00Z", "session_meta", session_meta)]
                total_input = 0
                total_output = 0
                for index, (turn_id, input_tokens, output_tokens) in enumerate(turns):
                    total_input += input_tokens
                    total_output += output_tokens
                    events.extend(
                        [
                            _event(
                                f"2026-07-24T07:{index + 1:02d}:00Z",
                                "turn_context",
                                {
                                    "turn_id": turn_id,
                                    "model": "gpt-5.6-terra",
                                    "effort": "high",
                                },
                            ),
                            _event(
                                f"2026-07-24T07:{index + 1:02d}:10Z",
                                "event_msg",
                                {
                                    "type": "token_count",
                                    "info": {
                                        "last_token_usage": {
                                            "input_tokens": input_tokens,
                                            "output_tokens": output_tokens,
                                        },
                                        "total_token_usage": {
                                            "input_tokens": total_input,
                                            "output_tokens": total_output,
                                        },
                                    },
                                },
                            ),
                            _event(
                                f"2026-07-24T07:{index + 1:02d}:20Z",
                                "event_msg",
                                {"type": "task_complete", "duration_ms": 20000},
                            ),
                        ]
                    )
                path.write_text("\n".join(events) + "\n", encoding="utf-8")

            write_session(
                sessions_root / "rollout-parent.jsonl",
                session_id="parent-session",
                forked_from_id=None,
                turns=[("parent-turn", 10, 2)],
            )
            write_session(
                sessions_root / "rollout-child.jsonl",
                session_id="child-session",
                forked_from_id="parent-session",
                turns=[("parent-turn", 10, 2), ("child-turn", 5, 1)],
            )

            summary = observe_codex_usage(
                sessions_root=sessions_root,
                store=UsageStore(root / "data"),
                now=datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc),
            )

            aggregate = summary["aggregates"][0]
            self.assertEqual(aggregate["completed_work_units"], 2)
            self.assertEqual(aggregate["input_tokens"], 15)
            self.assertEqual(aggregate["output_tokens"], 3)

    def test_fork_replay_window_is_skipped_before_new_work(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            rollout = sessions_root / "rollout-child.jsonl"
            rollout.write_text(
                "\n".join(
                    [
                        _event(
                            "2026-07-24T07:00:00Z",
                            "session_meta",
                            {
                                "id": "child-session",
                                "forked_from_id": "parent-session",
                                "model_provider": "OpenAI",
                            },
                        ),
                        _event(
                            "2026-07-24T07:00:01Z",
                            "turn_context",
                            {
                                "turn_id": "parent-turn",
                                "model": "gpt-5.6-terra",
                                "effort": "high",
                            },
                        ),
                        _event(
                            "2026-07-24T07:00:02Z",
                            "event_msg",
                            {
                                "type": "token_count",
                                "info": {
                                    "last_token_usage": {
                                        "input_tokens": 10,
                                        "output_tokens": 2,
                                    },
                                    "total_token_usage": {
                                        "input_tokens": 10,
                                        "output_tokens": 2,
                                    },
                                },
                            },
                        ),
                        _event(
                            "2026-07-24T07:00:03Z",
                            "event_msg",
                            {"type": "task_complete", "duration_ms": 20000},
                        ),
                        _event(
                            "2026-07-24T07:00:06Z",
                            "turn_context",
                            {
                                "turn_id": "child-turn",
                                "model": "gpt-5.6-terra",
                                "effort": "high",
                            },
                        ),
                        _event(
                            "2026-07-24T07:00:07Z",
                            "event_msg",
                            {
                                "type": "token_count",
                                "info": {
                                    "last_token_usage": {
                                        "input_tokens": 5,
                                        "output_tokens": 1,
                                    },
                                    "total_token_usage": {
                                        "input_tokens": 15,
                                        "output_tokens": 3,
                                    },
                                },
                            },
                        ),
                        _event(
                            "2026-07-24T07:00:08Z",
                            "event_msg",
                            {"type": "task_complete", "duration_ms": 2000},
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            summary = observe_codex_usage(
                sessions_root=sessions_root,
                store=UsageStore(root / "data"),
                now=datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc),
            )

            aggregate = summary["aggregates"][0]
            self.assertEqual(aggregate["completed_work_units"], 1)
            self.assertEqual(aggregate["input_tokens"], 5)
            self.assertEqual(aggregate["output_tokens"], 1)

    def test_observer_includes_sibling_archived_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            archived_root = root / "archived_sessions"
            sessions_root.mkdir()
            archived_root.mkdir()
            (archived_root / "rollout-archived.jsonl").write_text(
                "\n".join(
                    [
                        _event(
                            "2026-07-24T07:00:00Z",
                            "session_meta",
                            {"id": "archived-session", "model_provider": "OpenAI"},
                        ),
                        _event(
                            "2026-07-24T07:01:00Z",
                            "turn_context",
                            {
                                "turn_id": "archived-turn",
                                "model": "gpt-5.6-terra",
                                "effort": "high",
                            },
                        ),
                        _event(
                            "2026-07-24T07:01:10Z",
                            "event_msg",
                            {
                                "type": "token_count",
                                "info": {
                                    "last_token_usage": {
                                        "input_tokens": 30,
                                        "output_tokens": 6,
                                    }
                                },
                            },
                        ),
                        _event(
                            "2026-07-24T07:01:20Z",
                            "event_msg",
                            {"type": "task_complete", "duration_ms": 20000},
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            summary = observe_codex_usage(
                sessions_root=sessions_root,
                store=UsageStore(root / "data"),
                now=datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(summary["observation_count"], 1)
            self.assertEqual(summary["aggregates"][0]["input_tokens"], 30)

    def test_system_review_and_modeldial_evaluation_are_not_workload_samples(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            for index, (model, cwd) in enumerate(
                [
                    ("codex-auto-review", "/private/project"),
                    ("gpt-5.6-terra", "/tmp/modeldial-evaluation-123"),
                ]
            ):
                rollout = sessions_root / f"rollout-{index}.jsonl"
                rollout.write_text(
                    "\n".join(
                        [
                            _event(
                                "2026-07-24T07:00:00Z",
                                "session_meta",
                                {
                                    "id": f"session-{index}",
                                    "model_provider": "OpenAI",
                                    "cwd": cwd,
                                },
                            ),
                            _event(
                                "2026-07-24T07:01:00Z",
                                "turn_context",
                                {
                                    "turn_id": f"turn-{index}",
                                    "model": model,
                                    "effort": "high",
                                },
                            ),
                            _event(
                                "2026-07-24T07:01:10Z",
                                "event_msg",
                                {
                                    "type": "token_count",
                                    "info": {
                                        "last_token_usage": {
                                            "input_tokens": 10,
                                            "output_tokens": 2,
                                        }
                                    },
                                },
                            ),
                            _event(
                                "2026-07-24T07:01:20Z",
                                "event_msg",
                                {"type": "task_complete", "duration_ms": 20000},
                            ),
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                )

            summary = observe_codex_usage(
                sessions_root=sessions_root,
                store=UsageStore(root / "data"),
                now=datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(summary["observation_count"], 0)
            self.assertEqual(summary["excluded_observation_count"], 2)
            self.assertEqual(summary["aggregates"], [])

    def test_truncated_bootstrap_becomes_complete_after_the_lookback_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            store = UsageStore(root / "data")
            store.save_usage_state(
                {
                    "schema_version": 1,
                    "files": {},
                    "observations": {},
                    "bootstrap_truncated": True,
                    "coverage_continuous_since": "2026-07-01T00:00:00Z",
                }
            )

            summary = observe_codex_usage(
                sessions_root=sessions_root,
                store=store,
                now=datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc),
                lookback_days=14,
            )

            self.assertTrue(summary["coverage_complete"])
            self.assertFalse(summary["bootstrap_truncated"])


if __name__ == "__main__":
    unittest.main()
