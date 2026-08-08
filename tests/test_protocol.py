from __future__ import annotations

import unittest

from scanner.protocol import (
    project_app_snapshot_v2,
    project_refresh_snapshot_v1,
    project_runtime_event_v1,
    version_runtime_event_stream,
)


class RuntimeEventProtocolTest(unittest.TestCase):
    @staticmethod
    def _active_runtime_state() -> dict[str, object]:
        return {
            "schema_version": 1,
            "runtime": {
                "enabled_target_count": 1,
                "history_count": 0,
                "is_running": True,
                "completed_targets": 2,
                "total_targets": 5,
                "progress_completed": 2,
                "progress_total": 5,
                "current_phase": "scan",
                "lifecycle_state": "active_scan",
                "lease_expires_at": "2026-07-29T12:10:00+08:00",
            },
        }

    @staticmethod
    def _app_snapshot_state() -> dict[str, object]:
        return {
            "schema_version": 2,
            "config": {},
            "dashboard": {},
            "runtime": {"lifecycle_state": "idle"},
            "question_pack": {},
            "settings_projection": {},
            "advisor_v2_evidence": {},
            "recommendation_portfolio_v2": {},
            "reference_snapshot_feed": {},
            "recommendation_use": {},
        }

    def test_projects_unversioned_monitor_state_to_app_snapshot_v2(self) -> None:
        internal_state = {
            "config": {},
            "dashboard": {},
            "runtime": {},
            "question_pack": {},
            "settings_projection": {},
            "advisor_v2_evidence": {},
            "recommendation_portfolio_v2": {},
            "reference_snapshot_feed": {},
            "recommendation_use": {},
            "history": ["internal-only"],
        }

        snapshot = project_app_snapshot_v2(internal_state)

        self.assertEqual(snapshot["schema_version"], 2)
        self.assertEqual(snapshot["dashboard"], {})
        self.assertNotIn("history", snapshot)

        for key in (
            "config",
            "dashboard",
            "runtime",
            "question_pack",
            "settings_projection",
            "advisor_v2_evidence",
            "recommendation_portfolio_v2",
            "reference_snapshot_feed",
            "recommendation_use",
        ):
            for replacement in ("missing", None):
                with self.subTest(key=key, replacement=replacement):
                    invalid = dict(snapshot)
                    if replacement == "missing":
                        invalid.pop(key)
                    else:
                        invalid[key] = None
                    with self.assertRaisesRegex(ValueError, key):
                        project_app_snapshot_v2(invalid)

    def test_rejects_wire_v1_and_unknown_app_snapshot_versions(self) -> None:
        for schema_version in (1, 3):
            with self.subTest(schema_version=schema_version):
                snapshot = self._app_snapshot_state()
                snapshot["schema_version"] = schema_version
                with self.assertRaisesRegex(ValueError, "schema version"):
                    project_app_snapshot_v2(snapshot)

        v1_snapshot = self._app_snapshot_state()
        v1_snapshot["schema_version"] = 1
        with self.assertRaisesRegex(ValueError, "supported projection"):
            project_runtime_event_v1(
                {"type": "scan.finished", "state": v1_snapshot}
            )

    def test_projects_refresh_snapshot_with_its_own_v1_contract(self) -> None:
        snapshot = project_refresh_snapshot_v1({"config": {}, "runtime": {}})

        self.assertEqual(snapshot["schema_version"], 1)

    def test_projects_started_progress_and_snapshot_payloads(self) -> None:
        started = project_runtime_event_v1(
            {"type": "scan.started", "state": self._active_runtime_state()}
        )
        progress = project_runtime_event_v1(
            {
                "type": "scan.progress",
                "state": {
                    "schema_version": 1,
                    "runtime": {"lifecycle_state": "active_scan"},
                },
            }
        )
        terminal = project_runtime_event_v1(
            {
                "type": "scan.finished",
                "state": self._app_snapshot_state(),
            }
        )

        self.assertEqual(started["state_kind"], "runtime_delta")
        self.assertEqual(
            started["state"]["runtime"]["progress_completed"],  # type: ignore[index]
            2,
        )
        self.assertEqual(progress["state_kind"], "runtime_delta")
        self.assertEqual(terminal["state_kind"], "snapshot")
        self.assertEqual(started["schema_version"], 1)

    def test_auto_resume_started_is_state_free_but_terminals_are_authoritative(self) -> None:
        started = project_runtime_event_v1({"type": "auto-resume.started"})
        self.assertEqual(started["state_kind"], "none")
        self.assertNotIn("state", started)

        for event_type in (
            "auto-resume.noop",
            "auto-resume.manual-attention",
        ):
            with self.subTest(event_type=event_type):
                event = project_runtime_event_v1(
                    {
                        "type": event_type,
                        "reason": "fixture",
                        "message": "fixture marker",
                        "state": self._app_snapshot_state(),
                    }
                )
                self.assertEqual(event["schema_version"], 1)
                self.assertEqual(event["state_kind"], "snapshot")
                self.assertEqual(event["state"]["schema_version"], 2)
                with self.assertRaisesRegex(ValueError, "does not allow state kind"):
                    project_runtime_event_v1({"type": event_type})

    def test_state_kind_none_rejects_any_explicit_state_value(self) -> None:
        for state in (None, "invalid", 1, []):
            with self.subTest(state=state):
                with self.assertRaisesRegex(ValueError, "supported projection"):
                    project_runtime_event_v1(
                        {
                            "type": "auto-resume.started",
                            "state": state,
                        }
                    )

    def test_terminal_snapshot_can_include_projection_diagnostics(self) -> None:
        snapshot = self._app_snapshot_state()
        snapshot["persistence_errors"] = ["injected"]
        event = project_runtime_event_v1(
            {
                "type": "scan.failed",
                "state": snapshot,
            }
        )

        self.assertEqual(event["state_kind"], "snapshot")
        self.assertEqual(event["state"]["persistence_errors"], ["injected"])

    def test_rejects_conflicting_explicit_envelope(self) -> None:
        with self.assertRaisesRegex(ValueError, "schema version"):
            project_runtime_event_v1(
                {"schema_version": 2, "type": "scan.started"}
            )
        with self.assertRaisesRegex(ValueError, "state kind"):
            project_runtime_event_v1(
                {
                    "schema_version": 1,
                    "state_kind": "snapshot",
                    "type": "scan.progress",
                    "state": {"schema_version": 1, "runtime": {}},
                }
            )

    def test_event_type_controls_its_allowed_state_projection(self) -> None:
        runtime_delta = {"schema_version": 1, "runtime": {}}
        snapshot = self._app_snapshot_state()
        runtime_event_types = {
            "scan.started",
            "target.started",
            "scan.progress",
            "scan.finalizing",
            "repair.started",
            "repair.question.started",
            "repair.question.finished",
            "repair.finalizing",
            "timeout-repair.started",
            "timeout-repair.question.started",
            "timeout-repair.question.finished",
            "timeout-repair.finalizing",
        }
        snapshot_event_types = {
            "auto-resume.noop",
            "auto-resume.manual-attention",
            "scan.finished",
            "scan.paused",
            "scan.stopped",
            "scan.already_running",
            "repair.finished",
            "repair.paused",
            "repair.stopped",
            "repair.already_running",
            "timeout-repair.finished",
            "timeout-repair.paused",
            "timeout-repair.stopped",
            "timeout-repair.already_running",
        }
        failure_event_types = {
            "scan.failed",
            "repair.failed",
            "timeout-repair.failed",
        }

        for event_type in runtime_event_types:
            with self.subTest(event_type=event_type):
                projected = project_runtime_event_v1(
                    {"type": event_type, "state": runtime_delta}
                )
                self.assertEqual(projected["state_kind"], "runtime_delta")
                with self.assertRaisesRegex(ValueError, "does not allow state kind"):
                    project_runtime_event_v1(
                        {"type": event_type, "state": snapshot}
                    )

        for event_type in snapshot_event_types:
            with self.subTest(event_type=event_type):
                projected = project_runtime_event_v1(
                    {"type": event_type, "state": snapshot}
                )
                self.assertEqual(projected["state_kind"], "snapshot")
                with self.assertRaisesRegex(ValueError, "does not allow state kind"):
                    project_runtime_event_v1(
                        {"type": event_type, "state": runtime_delta}
                    )

        for event_type in failure_event_types:
            with self.subTest(event_type=event_type):
                projected = project_runtime_event_v1(
                    {"type": event_type, "state": snapshot}
                )
                self.assertEqual(projected["state_kind"], "snapshot")
                with self.assertRaisesRegex(ValueError, "does not allow state kind"):
                    project_runtime_event_v1({"type": event_type})
                with self.assertRaisesRegex(ValueError, "does not allow state kind"):
                    project_runtime_event_v1(
                        {"type": event_type, "state": runtime_delta}
                    )

        with self.assertRaisesRegex(ValueError, "unsupported runtime event type"):
            project_runtime_event_v1({"type": "scan.unknown"})
        with self.assertRaisesRegex(ValueError, "supported projection"):
            project_runtime_event_v1(
                {"type": "scan.finished", "state": {"config": {}, "runtime": {}}}
            )

    def test_stream_adapter_versions_every_event(self) -> None:
        @version_runtime_event_stream
        def stream() -> object:
            yield {"type": "scan.started", "state": self._active_runtime_state()}
            yield {"type": "scan.finished", "state": self._app_snapshot_state()}

        events = list(stream())

        self.assertEqual(
            [event["state_kind"] for event in events],
            ["runtime_delta", "snapshot"],
        )


if __name__ == "__main__":
    unittest.main()
