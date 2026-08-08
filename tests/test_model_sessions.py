from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from scanner.model_sessions import (
    detect_claude_active_sessions,
    detect_grok_active_sessions,
    detect_registered_active_sessions,
)
from scripts.modeldial_session_hook import record_hook_payload


class ModelSessionDetectionTest(unittest.TestCase):
    def test_detects_claude_hook_session_without_process_introspection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            registry_path = root / "registry.json"
            record_hook_payload(
                "claude",
                {
                    "session_id": "claude-hook-session",
                    "hook_event_name": "SessionStart",
                    "cwd": "/Users/example/claude-project",
                    "model": "claude-sonnet-4-5",
                },
                inbox_path=inbox,
                observed_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            )
            record_hook_payload(
                "claude",
                {
                    "session_id": "claude-hook-session",
                    "hook_event_name": "UserPromptSubmit",
                    "cwd": "/Users/example/claude-project",
                },
                inbox_path=inbox,
                observed_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            )

            sessions = detect_claude_active_sessions(
                home=root,
                command_runner=lambda _command: None,
                event_inbox_path=inbox,
                registry_path=registry_path,
            )

            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].id, "claude-hook-session")
            self.assertEqual(sessions[0].workspace_name, "claude-project")
            self.assertEqual(sessions[0].model, "claude-sonnet-4-5")

    def test_detects_active_claude_process_without_reading_message_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            workspace = home / "projects" / "demo"
            transcript = home / ".claude" / "projects" / "demo" / "session-one.jsonl"
            transcript.parent.mkdir(parents=True)
            transcript.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "user",
                                "sessionId": "session-one",
                                "cwd": str(workspace),
                                "timestamp": "2026-07-22T08:00:00Z",
                                "message": {"role": "user", "content": "private prompt"},
                            }
                        ),
                        json.dumps(
                            {
                                "type": "assistant",
                                "sessionId": "session-one",
                                "cwd": str(workspace),
                                "timestamp": "2026-07-22T08:00:01Z",
                                "message": {
                                    "role": "assistant",
                                    "model": "claude-sonnet-4-5",
                                    "content": "private response",
                                },
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            def command_runner(command: tuple[str, ...]) -> str | None:
                if command == ("/bin/ps", "-Ao", "pid=,command="):
                    return "321 /Users/example/.local/bin/claude --effort high\n"
                if command == (
                    "/usr/sbin/lsof",
                    "-a",
                    "-p",
                    "321",
                    "-d",
                    "cwd",
                    "-Fn",
                ):
                    return f"p321\nfcwd\nn{workspace}\n"
                if command == ("/usr/sbin/lsof", "-p", "321", "-Fn"):
                    return f"p321\nftxt\nn{transcript}\n"
                return None

            sessions = detect_claude_active_sessions(
                home=home,
                command_runner=command_runner,
            )

            self.assertEqual(len(sessions), 1)
            session = sessions[0]
            self.assertEqual(session.id, "session-one")
            self.assertEqual(session.source, "claude")
            self.assertEqual(session.workspace_name, "demo")
            self.assertEqual(session.model, "claude-sonnet-4-5")
            self.assertEqual(session.effort, "high")
            self.assertIsNone(session.thread_name)
            self.assertNotIn("private", repr(session))

    def test_detects_grok_session_from_read_only_active_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            active_sessions_path = home / ".grok" / "active_sessions.json"
            active_sessions_path.parent.mkdir(parents=True)
            active_sessions_path.write_text(
                json.dumps(
                    [
                        {
                            "session_id": "grok-session",
                            "pid": 456,
                            "opened_at": "2026-07-22T08:00:00Z",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            encoded_workspace = "%2FUsers%2Fexample%2Fgrok-project"
            summary_path = (
                home
                / ".grok"
                / "sessions"
                / encoded_workspace
                / "grok-session"
                / "summary.json"
            )
            summary_path.parent.mkdir(parents=True)
            summary_path.write_text(
                json.dumps(
                    {
                        "generated_title": "修复缓存回归",
                        "current_model_id": "grok-4.5",
                        "reasoning_effort": "high",
                    }
                ),
                encoding="utf-8",
            )

            sessions = detect_grok_active_sessions(
                home=home,
                process_is_alive=lambda pid: pid == 456,
            )

            self.assertEqual(len(sessions), 1)
            session = sessions[0]
            self.assertEqual(session.id, "grok-session")
            self.assertEqual(session.source, "grok")
            self.assertEqual(session.thread_name, "修复缓存回归")
            self.assertEqual(session.workspace_name, "grok-project")
            self.assertEqual(session.model, "grok-4.5")
            self.assertEqual(session.effort, "high")

    def test_ignores_stale_grok_registry_entry_with_dead_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            active_sessions_path = home / ".grok" / "active_sessions.json"
            active_sessions_path.parent.mkdir(parents=True)
            active_sessions_path.write_text(
                json.dumps([{"session_id": "stale", "pid": 999}]),
                encoding="utf-8",
            )

            sessions = detect_grok_active_sessions(
                home=home,
                process_is_alive=lambda _pid: False,
            )

            self.assertEqual(sessions, ())

    def test_detects_other_supported_terminal_sessions_from_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            registry_path = root / "registry.json"
            observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            record_hook_payload(
                "opencode",
                {
                    "session_id": "opencode-session",
                    "hook_event_name": "UserPromptSubmit",
                    "cwd": "/Users/example/opencode-project",
                    "model": "vendor-model",
                    "effort": "high",
                },
                inbox_path=inbox,
                observed_at=observed_at,
            )

            sessions = detect_registered_active_sessions(
                inbox_path=inbox,
                registry_path=registry_path,
            )

            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].id, "opencode-session")
            self.assertEqual(sessions[0].source, "opencode")
            self.assertEqual(sessions[0].workspace_name, "opencode-project")
            self.assertEqual(sessions[0].model, "vendor-model")
            self.assertEqual(sessions[0].effort, "high")


if __name__ == "__main__":
    unittest.main()
