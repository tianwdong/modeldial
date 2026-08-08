from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from scanner.session_registry import (
    application_support_root,
    consume_session_events,
    record_modeldial_session_end,
)
from scripts.modeldial_session_hook import record_hook_payload


class SessionRegistryTest(unittest.TestCase):
    def test_application_support_migrates_legacy_directory_without_deleting_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            legacy = home / "Library" / "Application Support" / "ModelPilot"
            legacy.mkdir(parents=True)
            (legacy / "session-registry.json").write_text("{}", encoding="utf-8")

            with patch("scanner.session_registry.Path.home", return_value=home), patch(
                "scanner.session_registry.sys.platform", "darwin"
            ), patch.dict(os.environ, {}, clear=True):
                migrated = application_support_root()

            self.assertEqual(
                migrated,
                home / "Library" / "Application Support" / "modeldial",
            )
            self.assertTrue((migrated / "session-registry.json").exists())
            self.assertTrue((legacy / "session-registry.json").exists())

    def test_new_data_dir_environment_variable_wins_with_legacy_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch.dict(
                os.environ,
                {
                    "MODELDIAL_DATA_DIR": str(root / "new"),
                    "MODEL_PILOT_DATA_DIR": str(root / "old"),
                },
                clear=True,
            ):
                self.assertEqual(application_support_root(), root / "new")
            with patch.dict(
                os.environ,
                {"MODEL_PILOT_DATA_DIR": str(root / "old")},
                clear=True,
            ):
                self.assertEqual(application_support_root(), root / "old")

    def test_records_only_session_metadata_and_never_persists_prompt_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            payload = {
                "session_id": "codex-session",
                "turn_id": "turn-one",
                "hook_event_name": "UserPromptSubmit",
                "cwd": "/Users/example/project",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "xhigh",
                "prompt": "private prompt that must not be stored",
            }

            event_path = record_hook_payload(
                "codex",
                payload,
                inbox_path=inbox,
                observed_at="2026-07-22T08:00:00Z",
            )

            self.assertIsNotNone(event_path)
            persisted = event_path.read_text(encoding="utf-8")
            self.assertNotIn("private prompt", persisted)
            self.assertNotIn('"prompt"', persisted)
            self.assertEqual(json.loads(persisted)["session_id"], "codex-session")

    def test_hook_uses_process_scoped_scan_effort_when_payload_omits_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = Path(temp_dir) / "inbox"
            with patch.dict(
                os.environ,
                {"MODELDIAL_SCAN_EFFORT": "xhigh"},
                clear=False,
            ):
                event_path = record_hook_payload(
                    "codex",
                    {
                        "session_id": "scan-session",
                        "hook_event_name": "SessionStart",
                        "model": "gpt-5.6-sol",
                    },
                    inbox_path=inbox,
                    observed_at="2026-07-22T08:00:00Z",
                )

            self.assertIsNotNone(event_path)
            event = json.loads(event_path.read_text(encoding="utf-8"))
            self.assertEqual(event["effort"], "xhigh")

    def test_hook_marks_modeldial_owned_scan_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            registry_path = root / "registry.json"
            with patch.dict(
                os.environ,
                {"MODELDIAL_SCAN_SESSION": "1"},
                clear=False,
            ):
                event_path = record_hook_payload(
                    "codex",
                    {
                        "session_id": "scan-session",
                        "hook_event_name": "UserPromptSubmit",
                        "cwd": "/Users/example/project",
                        "model": "gpt-5.6-sol",
                        "reasoning_effort": "high",
                    },
                    inbox_path=inbox,
                    observed_at="2026-07-22T08:00:00Z",
                )

            self.assertIsNotNone(event_path)
            self.assertTrue(
                json.loads(event_path.read_text(encoding="utf-8"))["is_modeldial_scan"]
            )
            records = consume_session_events(
                inbox_path=inbox,
                registry_path=registry_path,
            )
            self.assertTrue(records["codex:scan-session"].is_modeldial_scan)

    def test_modeldial_workspace_is_reclaimed_when_the_app_process_releases_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            registry_path = root / "registry.json"
            workspace = root / "modeldial-evaluation-live"
            workspace.mkdir()
            record_hook_payload(
                "codex",
                {
                    "session_id": "scan-session",
                    "hook_event_name": "UserPromptSubmit",
                    "cwd": str(workspace),
                    "model": "gpt-5.6-sol",
                    "reasoning_effort": "high",
                },
                inbox_path=inbox,
                observed_at="2026-07-24T01:00:00Z",
            )

            running = consume_session_events(
                inbox_path=inbox,
                registry_path=registry_path,
            )
            self.assertEqual(running["codex:scan-session"].status, "running")
            self.assertTrue(running["codex:scan-session"].is_modeldial_scan)

            workspace.rmdir()
            reclaimed = consume_session_events(
                inbox_path=inbox,
                registry_path=registry_path,
            )

            self.assertEqual(reclaimed["codex:scan-session"].status, "ended")
            self.assertEqual(
                reclaimed["codex:scan-session"].last_event,
                "ModelDialWorkspaceReleased",
            )

    def test_modeldial_runtime_can_record_a_definitive_process_end(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            registry_path = root / "registry.json"

            event_path = record_modeldial_session_end(
                "scan-session",
                inbox_path=inbox,
                observed_at="2026-07-24T01:00:02Z",
            )
            records = consume_session_events(
                inbox_path=inbox,
                registry_path=registry_path,
            )

            self.assertIsNotNone(event_path)
            self.assertEqual(records["codex:scan-session"].status, "ended")
            self.assertTrue(records["codex:scan-session"].is_modeldial_scan)
            self.assertEqual(
                records["codex:scan-session"].last_event,
                "ModelDialProcessEnd",
            )

    def test_hook_payload_effort_takes_precedence_over_scan_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = Path(temp_dir) / "inbox"
            with patch.dict(
                os.environ,
                {"MODELDIAL_SCAN_EFFORT": "medium"},
                clear=False,
            ):
                event_path = record_hook_payload(
                    "codex",
                    {
                        "session_id": "regular-session",
                        "hook_event_name": "SessionStart",
                        "reasoning_effort": "high",
                    },
                    inbox_path=inbox,
                    observed_at="2026-07-22T08:00:00Z",
                )

            self.assertIsNotNone(event_path)
            event = json.loads(event_path.read_text(encoding="utf-8"))
            self.assertEqual(event["effort"], "high")

    def test_reduces_lifecycle_events_and_consumes_spool_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            registry_path = root / "registry.json"
            common = {
                "session_id": "codex-session",
                "cwd": "/Users/example/project",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "high",
            }
            record_hook_payload(
                "codex",
                {**common, "hook_event_name": "SessionStart"},
                inbox_path=inbox,
                observed_at="2026-07-22T08:00:00Z",
            )
            record_hook_payload(
                "codex",
                {
                    **common,
                    "hook_event_name": "UserPromptSubmit",
                    "turn_id": "turn-one",
                },
                inbox_path=inbox,
                observed_at="2026-07-22T08:00:01Z",
            )

            records = consume_session_events(
                inbox_path=inbox,
                registry_path=registry_path,
            )

            record = records["codex:codex-session"]
            self.assertEqual(record.status, "running")
            self.assertEqual(record.turn_id, "turn-one")
            self.assertEqual(record.workspace_name, "project")
            self.assertEqual(list(inbox.glob("*.json")), [])

            record_hook_payload(
                "codex",
                {
                    **common,
                    "hook_event_name": "Stop",
                    "turn_id": "turn-one",
                },
                inbox_path=inbox,
                observed_at="2026-07-22T08:00:02Z",
            )
            stopped = consume_session_events(
                inbox_path=inbox,
                registry_path=registry_path,
            )
            reloaded = consume_session_events(
                inbox_path=inbox,
                registry_path=registry_path,
            )

            self.assertEqual(stopped["codex:codex-session"].status, "idle")
            self.assertEqual(reloaded, stopped)

    def test_concurrent_consumers_serialize_registry_updates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            registry_path = root / "registry.json"
            record_hook_payload(
                "codex",
                {
                    "session_id": "session-1",
                    "hook_event_name": "UserPromptSubmit",
                },
                inbox_path=inbox,
                observed_at="2026-07-28T08:00:00Z",
            )
            first_save_started = threading.Event()
            release_first_save = threading.Event()
            second_finished = threading.Event()
            save_calls = 0
            save_calls_lock = threading.Lock()

            from scanner import session_registry as registry_module

            original_save = registry_module._save_registry

            def blocking_save(path, records):  # type: ignore[no-untyped-def]
                nonlocal save_calls
                with save_calls_lock:
                    save_calls += 1
                    is_first = save_calls == 1
                if is_first:
                    first_save_started.set()
                    self.assertTrue(release_first_save.wait(timeout=2))
                return original_save(path, records)

            def consume_first() -> None:
                consume_session_events(
                    inbox_path=inbox,
                    registry_path=registry_path,
                )

            def consume_second() -> None:
                consume_session_events(
                    inbox_path=inbox,
                    registry_path=registry_path,
                )
                second_finished.set()

            with patch.object(registry_module, "_save_registry", side_effect=blocking_save):
                first_thread = threading.Thread(target=consume_first)
                first_thread.start()
                self.assertTrue(first_save_started.wait(timeout=2))
                record_hook_payload(
                    "codex",
                    {
                        "session_id": "session-2",
                        "hook_event_name": "UserPromptSubmit",
                    },
                    inbox_path=inbox,
                    observed_at="2026-07-28T08:00:01Z",
                )
                second_thread = threading.Thread(target=consume_second)
                second_thread.start()

                self.assertFalse(second_finished.wait(timeout=0.1))
                release_first_save.set()
                first_thread.join(timeout=2)
                second_thread.join(timeout=2)

            records = consume_session_events(
                inbox_path=inbox,
                registry_path=registry_path,
            )
            self.assertEqual(
                set(records),
                {"codex:session-1", "codex:session-2"},
            )
            self.assertEqual(list(inbox.glob("*.json")), [])

    def test_session_end_is_definitive_but_stop_only_ends_the_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            registry_path = root / "registry.json"
            for event_name, observed_at in (
                ("UserPromptSubmit", "2026-07-22T08:00:00Z"),
                ("Stop", "2026-07-22T08:00:01Z"),
                ("SessionEnd", "2026-07-22T08:00:02Z"),
            ):
                record_hook_payload(
                    "claude",
                    {
                        "session_id": "claude-session",
                        "hook_event_name": event_name,
                        "cwd": "/Users/example/claude-project",
                    },
                    inbox_path=inbox,
                    observed_at=observed_at,
                )

            records = consume_session_events(
                inbox_path=inbox,
                registry_path=registry_path,
            )

            self.assertEqual(records["claude:claude-session"].status, "ended")


if __name__ == "__main__":
    unittest.main()
