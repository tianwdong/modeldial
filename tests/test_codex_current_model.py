from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scanner.codex_current_model import detect_codex_current_model
from scripts.modeldial_session_hook import record_hook_payload


def _session_meta_event(
    *,
    originator: str,
    source: str,
    session_id: str | None = None,
    cwd: str | None = None,
) -> str:
    payload = {
        "originator": originator,
        "source": source,
    }
    if session_id is not None:
        payload["session_id"] = session_id
    if cwd is not None:
        payload["cwd"] = cwd
    return json.dumps(
        {
            "timestamp": "2026-07-15T01:59:59Z",
            "type": "session_meta",
            "payload": payload,
        }
    )


def _settings_event(
    timestamp: str,
    *,
    model: str,
    effort: str,
    provider: str = "OpenAI",
) -> str:
    return json.dumps(
        {
            "timestamp": timestamp,
            "type": "event_msg",
            "payload": {
                "type": "thread_settings_applied",
                "thread_settings": {
                    "model": model,
                    "reasoning_effort": effort,
                    "model_provider_id": provider,
                },
            },
        }
    )


def _session_index_event(
    session_id: str,
    thread_name: str,
    updated_at: str = "2026-07-15T02:00:00Z",
) -> str:
    return json.dumps(
        {
            "id": session_id,
            "thread_name": thread_name,
            "updated_at": updated_at,
        }
    )


def _lifecycle_event(timestamp: str, event_type: str) -> str:
    return json.dumps(
        {
            "timestamp": timestamp,
            "type": "event_msg",
            "payload": {"type": event_type, "turn_id": f"turn-{timestamp}"},
        }
    )


def _write_session(
    path: Path,
    *,
    model: str,
    effort: str,
    settings_at: str,
    lifecycle: str,
    lifecycle_at: str,
) -> None:
    path.write_text(
        "\n".join(
            [
                _settings_event(settings_at, model=model, effort=effort),
                _lifecycle_event(lifecycle_at, lifecycle),
            ]
        )
        + "\n",
        encoding="utf-8",
    )


class CodexCurrentModelDetectorTest(unittest.TestCase):
    def test_default_tracker_uses_application_support_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions_root = root / "sessions"
            data_root = root / "application-support"
            sessions_root.mkdir()

            with patch.dict(os.environ, {"MODELDIAL_DATA_DIR": str(data_root)}):
                detect_codex_current_model(sessions_root)

            self.assertTrue((data_root / "codex_session_tracker.json").is_file())

    def test_unchanged_tracker_is_not_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "sessions"
            day = root / "2026" / "07" / "22"
            day.mkdir(parents=True)
            _write_session(
                day / "rollout-one.jsonl",
                model="gpt-5.6-sol",
                effort="high",
                settings_at="2026-07-22T08:00:00Z",
                lifecycle="task_started",
                lifecycle_at="2026-07-22T08:00:01Z",
            )
            cache_path = Path(temp_dir) / "tracker.json"
            detect_codex_current_model(root, cache_path=cache_path)
            original_mtime = cache_path.stat().st_mtime_ns
            pinned_mtime = original_mtime - 1_000_000_000
            os.utime(cache_path, ns=(pinned_mtime, pinned_mtime))

            detect_codex_current_model(root, cache_path=cache_path)

            self.assertEqual(cache_path.stat().st_mtime_ns, pinned_mtime)

    def test_does_not_drop_active_rollouts_after_the_twenty_fourth_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "sessions"
            day = root / "2026" / "07" / "22"
            day.mkdir(parents=True)
            for index in range(30):
                _write_session(
                    day / f"rollout-{index:02d}.jsonl",
                    model="gpt-5.6-sol",
                    effort="xhigh",
                    settings_at=f"2026-07-22T08:00:{index:02d}Z",
                    lifecycle="task_started",
                    lifecycle_at=f"2026-07-22T08:01:{index:02d}Z",
                )

            detected = detect_codex_current_model(
                root,
                cache_path=root.parent / "tracker.json",
            )

            self.assertIsNotNone(detected)
            self.assertEqual(detected.active_session_count, 30)

    def test_ignores_generic_hook_event_without_rollout_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            registry_path = root / "registry.json"
            observed_at = datetime(2026, 7, 24, 0, 0, tzinfo=timezone.utc)
            record_hook_payload(
                "codex",
                {
                    "session_id": "hook-session",
                    "turn_id": "turn-one",
                    "hook_event_name": "UserPromptSubmit",
                    "cwd": "/Users/example/hook-project",
                    "model": "gpt-5.6-sol",
                    "reasoning_effort": "max",
                },
                inbox_path=inbox,
                observed_at=observed_at.isoformat().replace("+00:00", "Z"),
            )

            with patch(
                "scanner.codex_current_model.time.time",
                return_value=observed_at.timestamp() + 60,
            ):
                detected = detect_codex_current_model(
                    root / "sessions",
                    cache_path=root / "tracker.json",
                    event_inbox_path=inbox,
                    registry_path=registry_path,
                )

            self.assertIsNone(detected)

    def test_ignores_background_memory_hook_even_while_recent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            registry_path = root / "registry.json"
            observed_at = datetime(2026, 7, 24, 0, 0, tzinfo=timezone.utc)
            record_hook_payload(
                "codex",
                {
                    "session_id": "background-memory-job",
                    "hook_event_name": "UserPromptSubmit",
                    "cwd": "/Users/example/.codex/memories",
                    "model": "gpt-5.6-terra",
                },
                inbox_path=inbox,
                observed_at=observed_at.isoformat().replace("+00:00", "Z"),
            )

            with patch(
                "scanner.codex_current_model.time.time",
                return_value=observed_at.timestamp() + 60,
            ):
                detected = detect_codex_current_model(
                    root / "sessions",
                    cache_path=root / "tracker.json",
                    event_inbox_path=inbox,
                    registry_path=registry_path,
                )

            self.assertIsNone(detected)

    def test_modeldial_scan_is_visible_but_does_not_change_current_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            day = sessions_root / "2026" / "07" / "22"
            day.mkdir(parents=True)
            _write_session(
                day / "rollout-user.jsonl",
                model="gpt-5.6-sol",
                effort="high",
                settings_at="2026-07-22T08:00:00Z",
                lifecycle="task_started",
                lifecycle_at="2026-07-22T08:00:01Z",
            )
            inbox = root / "inbox"
            registry_path = root / "registry.json"
            with patch.dict(
                os.environ,
                {"MODELDIAL_SCAN_SESSION": "1"},
                clear=False,
            ):
                record_hook_payload(
                    "codex",
                    {
                        "session_id": "modeldial-scan",
                        "hook_event_name": "UserPromptSubmit",
                        "cwd": "/Users/example/project",
                        "model": "gpt-5.4",
                        "reasoning_effort": "xhigh",
                    },
                    inbox_path=inbox,
                    observed_at=datetime.now(timezone.utc).isoformat().replace(
                        "+00:00", "Z"
                    ),
                )

            detected = detect_codex_current_model(
                sessions_root,
                cache_path=root / "tracker.json",
                event_inbox_path=inbox,
                registry_path=registry_path,
            )

            self.assertIsNotNone(detected)
            self.assertEqual(detected.status, "active_single")
            self.assertEqual((detected.model, detected.effort), ("gpt-5.6-sol", "high"))
            self.assertEqual(detected.active_session_count, 1)
            self.assertEqual(len(detected.active_sessions), 1)
            self.assertEqual(len(detected.display_sessions), 2)
            self.assertIn(
                "modeldial-scan",
                {session.id for session in detected.display_sessions},
            )
            scan_session = next(
                session
                for session in detected.display_sessions
                if session.id == "modeldial-scan"
            )
            self.assertTrue(scan_session.is_modeldial_scan)

    def test_modeldial_ephemeral_session_disappears_with_its_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            inbox = root / "inbox"
            registry_path = root / "registry.json"
            workspace = root / "modeldial-evaluation-active"
            workspace.mkdir()
            record_hook_payload(
                "codex",
                {
                    "session_id": "modeldial-ephemeral",
                    "hook_event_name": "UserPromptSubmit",
                    "cwd": str(workspace),
                    "model": "gpt-5.6-terra",
                    "reasoning_effort": "max",
                },
                inbox_path=inbox,
                observed_at=datetime.now(timezone.utc).isoformat().replace(
                    "+00:00", "Z"
                ),
            )

            active = detect_codex_current_model(
                sessions_root,
                cache_path=root / "tracker.json",
                event_inbox_path=inbox,
                registry_path=registry_path,
            )
            workspace.rmdir()
            reclaimed = detect_codex_current_model(
                sessions_root,
                cache_path=root / "tracker.json",
                event_inbox_path=inbox,
                registry_path=registry_path,
            )

            self.assertIsNotNone(active)
            self.assertEqual(active.status, "scan_only")
            self.assertEqual(len(active.display_sessions), 1)
            self.assertIsNone(reclaimed)

    def test_legacy_bundled_scan_workspace_is_excluded_from_current_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            registry_path = root / "registry.json"
            record_hook_payload(
                "codex",
                {
                    "session_id": "legacy-modeldial-scan",
                    "hook_event_name": "UserPromptSubmit",
                    "cwd": "/Applications/modeldial.app/Contents/Resources/Backend",
                    "model": "gpt-5.4",
                    "reasoning_effort": "xhigh",
                },
                inbox_path=inbox,
                observed_at=datetime.now(timezone.utc).isoformat().replace(
                    "+00:00", "Z"
                ),
            )

            detected = detect_codex_current_model(
                root / "sessions",
                cache_path=root / "tracker.json",
                event_inbox_path=inbox,
                registry_path=registry_path,
            )

            self.assertIsNotNone(detected)
            self.assertEqual(detected.status, "scan_only")
            self.assertEqual(detected.active_session_count, 0)
            self.assertEqual(detected.active_sessions, ())
            self.assertEqual(len(detected.display_sessions), 1)

    def test_maps_latest_thread_name_from_session_index_to_active_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "sessions"
            day = root / "2026" / "07" / "15"
            day.mkdir(parents=True)
            session_id = "019f83cd-5030-7d61-8945-0524a52ba4ba"
            (day / f"rollout-{session_id}.jsonl").write_text(
                "\n".join(
                    [
                        _session_meta_event(
                            originator="Codex Desktop",
                            source="vscode",
                            session_id=session_id,
                            cwd="/Users/test/workspaces/is_your_codex_clever",
                        ),
                        _settings_event(
                            "2026-07-15T02:00:00Z",
                            model="gpt-5.6-sol",
                            effort="xhigh",
                        ),
                        _lifecycle_event("2026-07-15T02:00:01Z", "task_started"),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (root.parent / "session_index.jsonl").write_text(
                "\n".join(
                    [
                        _session_index_event(session_id, "旧标题"),
                        _session_index_event(
                            session_id,
                            "改进题目难度",
                            "2026-07-15T02:05:00Z",
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            detected = detect_codex_current_model(
                root,
                cache_path=root.parent / "tracker.json",
            )

            self.assertIsNotNone(detected)
            self.assertEqual(detected.active_sessions[0].thread_name, "改进题目难度")

    def test_includes_recent_codex_exec_in_active_model_aggregation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            day = root / "2026" / "07" / "15"
            day.mkdir(parents=True)
            (day / "rollout-desktop.jsonl").write_text(
                "\n".join(
                    [
                        _session_meta_event(
                            originator="Codex Desktop",
                            source="vscode",
                            session_id="desktop-session",
                            cwd="/Users/test/workspaces/desktop-project",
                        ),
                        _settings_event(
                            "2026-07-15T02:00:00Z",
                            model="gpt-5.6-sol",
                            effort="max",
                        ),
                        _lifecycle_event("2026-07-15T02:00:01Z", "task_started"),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (day / "rollout-exec.jsonl").write_text(
                "\n".join(
                    [
                        _session_meta_event(
                            originator="codex_exec",
                            source="exec",
                            session_id="exec-session",
                            cwd="/Users/test/workspaces/exec-project",
                        ),
                        _settings_event(
                            "2026-07-15T02:01:00Z",
                            model="gpt-5.6-sol",
                            effort="xhigh",
                        ),
                        _lifecycle_event("2026-07-15T02:01:01Z", "task_started"),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            detected = detect_codex_current_model(
                root,
                cache_path=root / "tracker.json",
            )

            self.assertIsNotNone(detected)
            self.assertEqual(detected.status, "active_mixed")
            self.assertEqual(detected.active_session_count, 2)
            self.assertIsNone(detected.model)
            self.assertIsNone(detected.effort)
            self.assertEqual(
                detected.distinct_active_models,
                (("gpt-5.6-sol", "max"), ("gpt-5.6-sol", "xhigh")),
            )
            self.assertEqual(
                [session.id for session in detected.active_sessions],
                ["exec-session", "desktop-session"],
            )
            self.assertEqual(
                [session.workspace_name for session in detected.active_sessions],
                ["exec-project", "desktop-project"],
            )
            self.assertEqual(
                [session.effort for session in detected.active_sessions],
                ["xhigh", "max"],
            )

    def test_stale_unfinished_codex_exec_does_not_remain_active_forever(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            day = root / "2026" / "07" / "15"
            day.mkdir(parents=True)
            desktop_path = day / "rollout-desktop.jsonl"
            desktop_path.write_text(
                "\n".join(
                    [
                        _session_meta_event(
                            originator="Codex Desktop",
                            source="vscode",
                        ),
                        _settings_event(
                            "2026-07-15T02:00:00Z",
                            model="gpt-5.6-sol",
                            effort="max",
                        ),
                        _lifecycle_event("2026-07-15T02:00:01Z", "task_started"),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            exec_path = day / "rollout-exec.jsonl"
            exec_path.write_text(
                "\n".join(
                    [
                        _session_meta_event(
                            originator="codex_exec",
                            source="exec",
                        ),
                        _settings_event(
                            "2026-07-15T01:00:00Z",
                            model="gpt-5.6-sol",
                            effort="xhigh",
                        ),
                        _lifecycle_event("2026-07-15T01:00:01Z", "task_started"),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            os.utime(exec_path, (1, 1))

            detected = detect_codex_current_model(
                root,
                cache_path=root / "tracker.json",
            )

            self.assertIsNotNone(detected)
            self.assertEqual(detected.status, "active_single")
            self.assertEqual(detected.active_session_count, 1)
            self.assertEqual(detected.model, "gpt-5.6-sol")
            self.assertEqual(detected.effort, "max")
            self.assertEqual(
                detected.distinct_active_models,
                (("gpt-5.6-sol", "max"),),
            )

            with exec_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    _lifecycle_event("2026-07-15T03:00:00Z", "agent_message") + "\n"
                )
            resumed = detect_codex_current_model(
                root,
                cache_path=root / "tracker.json",
            )

            self.assertEqual(resumed.status, "active_mixed")
            self.assertEqual(resumed.active_session_count, 2)

    def test_quiet_unregistered_rollout_is_reclaimed_before_six_hour_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            day = root / "2026" / "07" / "23"
            day.mkdir(parents=True)
            current_path = day / "rollout-current.jsonl"
            _write_session(
                current_path,
                model="gpt-5.6-sol",
                effort="xhigh",
                settings_at="2026-07-23T07:40:00Z",
                lifecycle="task_started",
                lifecycle_at="2026-07-23T07:40:01Z",
            )
            abandoned_path = day / "rollout-abandoned.jsonl"
            _write_session(
                abandoned_path,
                model="gpt-5.6-sol",
                effort="xhigh",
                settings_at="2026-07-23T07:00:00Z",
                lifecycle="task_started",
                lifecycle_at="2026-07-23T07:00:01Z",
            )
            now = datetime(2026, 7, 23, 8, 0, tzinfo=timezone.utc).timestamp()
            os.utime(current_path, (now - 29 * 60, now - 29 * 60))
            os.utime(abandoned_path, (now - 31 * 60, now - 31 * 60))

            with patch("scanner.codex_current_model.time.time", return_value=now):
                detected = detect_codex_current_model(
                    root,
                    cache_path=root / "tracker.json",
                )

            self.assertIsNotNone(detected)
            self.assertEqual(detected.status, "active_single")
            self.assertEqual(detected.active_session_count, 1)
            self.assertEqual(detected.active_sessions[0].id, "rollout-current")

    def test_active_hook_keeps_quiet_rollout_during_full_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            day = sessions_root / "2026" / "07" / "23"
            day.mkdir(parents=True)
            session_path = day / "rollout-hook-session.jsonl"
            _write_session(
                session_path,
                model="gpt-5.6-sol",
                effort="xhigh",
                settings_at="2026-07-23T07:00:00Z",
                lifecycle="task_started",
                lifecycle_at="2026-07-23T07:00:01Z",
            )
            now = datetime(2026, 7, 23, 8, 0, tzinfo=timezone.utc).timestamp()
            quiet_at = now - 31 * 60
            os.utime(session_path, (quiet_at, quiet_at))
            inbox = root / "inbox"
            registry_path = root / "registry.json"
            record_hook_payload(
                "codex",
                {
                    "session_id": "rollout-hook-session",
                    "hook_event_name": "UserPromptSubmit",
                    "cwd": "/Users/example/project",
                    "model": "gpt-5.6-sol",
                    "reasoning_effort": "xhigh",
                },
                inbox_path=inbox,
                observed_at="2026-07-23T07:29:00Z",
            )

            with patch("scanner.codex_current_model.time.time", return_value=now):
                detected = detect_codex_current_model(
                    sessions_root,
                    cache_path=root / "tracker.json",
                    event_inbox_path=inbox,
                    registry_path=registry_path,
                )

            self.assertIsNotNone(detected)
            self.assertEqual(detected.status, "active_single")
            self.assertEqual(detected.active_session_count, 1)
            self.assertEqual(detected.active_sessions[0].id, "rollout-hook-session")

    def test_uses_latest_user_codex_request_and_ignores_internal_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            sessions_root = Path(temp_dir)
            day = sessions_root / "2026" / "07" / "15"
            day.mkdir(parents=True)
            (day / "rollout-user.jsonl").write_text(
                "\n".join(
                    [
                        "{malformed",
                        _settings_event(
                            "2026-07-15T01:00:00Z",
                            model="gpt-5.6-sol",
                            effort="medium",
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            (day / "rollout-review.jsonl").write_text(
                _settings_event(
                    "2026-07-15T01:05:00Z",
                    model="codex-auto-review",
                    effort="low",
                ),
                encoding="utf-8",
            )

            detected = detect_codex_current_model(
                sessions_root,
                cache_path=sessions_root / "tracker.json",
            )

            self.assertIsNotNone(detected)
            self.assertEqual(detected.model, "gpt-5.6-sol")
            self.assertEqual(detected.effort, "medium")
            self.assertEqual(detected.detected_at, "2026-07-15T01:00:00Z")
            self.assertEqual(detected.status, "recent")

    def test_returns_none_when_no_supported_user_request_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            sessions_root = Path(temp_dir)
            day = sessions_root / "2026" / "07" / "15"
            day.mkdir(parents=True)
            (day / "rollout-review.jsonl").write_text(
                _settings_event(
                    "2026-07-15T01:05:00Z",
                    model="codex-auto-review",
                    effort="low",
                ),
                encoding="utf-8",
            )

            self.assertIsNone(
                detect_codex_current_model(
                    sessions_root,
                    cache_path=sessions_root / "tracker.json",
                )
            )

    def test_reports_single_model_when_multiple_active_sessions_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            day = root / "2026" / "07" / "15"
            day.mkdir(parents=True)
            _write_session(
                day / "rollout-one.jsonl",
                model="gpt-5.6-sol",
                effort="high",
                settings_at="2026-07-15T02:00:00Z",
                lifecycle="task_started",
                lifecycle_at="2026-07-15T02:00:01Z",
            )
            _write_session(
                day / "rollout-two.jsonl",
                model="gpt-5.6-sol",
                effort="high",
                settings_at="2026-07-15T02:01:00Z",
                lifecycle="task_started",
                lifecycle_at="2026-07-15T02:01:01Z",
            )

            detected = detect_codex_current_model(root, cache_path=root / "tracker.json")

            self.assertIsNotNone(detected)
            self.assertEqual(detected.status, "active_single")
            self.assertEqual(detected.active_session_count, 2)
            self.assertEqual(detected.model, "gpt-5.6-sol")
            self.assertEqual(detected.effort, "high")
            self.assertEqual(len(detected.distinct_active_models), 1)

    def test_reports_mixed_when_active_sessions_use_different_models(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            day = root / "2026" / "07" / "15"
            day.mkdir(parents=True)
            _write_session(
                day / "rollout-one.jsonl",
                model="gpt-5.6-sol",
                effort="high",
                settings_at="2026-07-15T02:00:00Z",
                lifecycle="task_started",
                lifecycle_at="2026-07-15T02:00:01Z",
            )
            _write_session(
                day / "rollout-two.jsonl",
                model="gpt-5.4",
                effort="xhigh",
                settings_at="2026-07-15T02:01:00Z",
                lifecycle="task_started",
                lifecycle_at="2026-07-15T02:01:01Z",
            )

            detected = detect_codex_current_model(root, cache_path=root / "tracker.json")

            self.assertIsNotNone(detected)
            self.assertEqual(detected.status, "active_mixed")
            self.assertEqual(detected.active_session_count, 2)
            self.assertIsNone(detected.model)
            self.assertIsNone(detected.effort)
            self.assertEqual(len(detected.distinct_active_models), 2)

    def test_completed_turn_leaves_active_set_and_falls_back_to_recent_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            day = root / "2026" / "07" / "15"
            day.mkdir(parents=True)
            path = day / "rollout-one.jsonl"
            _write_session(
                path,
                model="gpt-5.6-sol",
                effort="medium",
                settings_at="2026-07-15T02:00:00Z",
                lifecycle="task_started",
                lifecycle_at="2026-07-15T02:00:01Z",
            )
            cache_path = root / "tracker.json"
            active = detect_codex_current_model(root, cache_path=cache_path)
            cached_offset = json.loads(cache_path.read_text(encoding="utf-8"))["sessions"][str(path)]["offset"]

            with path.open("a", encoding="utf-8") as handle:
                handle.write(_lifecycle_event("2026-07-15T02:05:00Z", "task_complete") + "\n")
            recent = detect_codex_current_model(root, cache_path=cache_path)
            updated_offset = json.loads(cache_path.read_text(encoding="utf-8"))["sessions"][str(path)]["offset"]

            self.assertEqual(active.status, "active_single")
            self.assertEqual(recent.status, "recent")
            self.assertEqual(recent.model, "gpt-5.6-sol")
            self.assertEqual(cached_offset, path.stat().st_size - len((_lifecycle_event("2026-07-15T02:05:00Z", "task_complete") + "\n").encode("utf-8")))
            self.assertEqual(updated_offset, path.stat().st_size)

    def test_bootstrap_keeps_offset_before_an_incomplete_final_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            day = root / "2026" / "07" / "15"
            day.mkdir(parents=True)
            path = day / "rollout-one.jsonl"
            complete_event = _lifecycle_event(
                "2026-07-15T02:05:00Z",
                "task_complete",
            )
            prefix = "\n".join(
                [
                    _settings_event(
                        "2026-07-15T02:00:00Z",
                        model="gpt-5.6-sol",
                        effort="medium",
                    ),
                    _lifecycle_event("2026-07-15T02:00:01Z", "task_started"),
                ]
            ) + "\n"
            split_at = len(complete_event) // 2
            path.write_text(prefix + complete_event[:split_at], encoding="utf-8")
            cache_path = root / "tracker.json"

            active = detect_codex_current_model(root, cache_path=cache_path)
            cached_offset = json.loads(cache_path.read_text(encoding="utf-8"))["sessions"][str(path)]["offset"]
            with path.open("a", encoding="utf-8") as handle:
                handle.write(complete_event[split_at:] + "\n")
            recent = detect_codex_current_model(root, cache_path=cache_path)

            self.assertEqual(active.status, "active_single")
            self.assertEqual(cached_offset, len(prefix.encode("utf-8")))
            self.assertEqual(recent.status, "recent")
            self.assertEqual(recent.model, "gpt-5.6-sol")


if __name__ == "__main__":
    unittest.main()
