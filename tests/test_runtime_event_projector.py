from __future__ import annotations

from datetime import datetime
import unittest
from unittest.mock import MagicMock

from scanner.runtime_event_projector import (
    project_finalizing_runtime_event,
    project_started_runtime_state,
    project_terminal_failure_event,
)


class RuntimeEventProjectorTest(unittest.TestCase):
    @staticmethod
    def _runtime_state(
        *,
        lifecycle_state: str = "idle",
        updated_at: str = "2026-07-29T12:00:00+08:00",
    ) -> dict[str, object]:
        return {
            "schema_version": 1,
            "runtime": {
                "history_count": 3,
                "lifecycle_state": lifecycle_state,
                "execution_timeout_seconds": 30,
                "updated_at": updated_at,
            },
        }

    @classmethod
    def _snapshot_state(
        cls,
        *,
        lifecycle_state: str = "idle",
    ) -> dict[str, object]:
        return {
            "schema_version": 2,
            "config": {},
            "dashboard": {},
            "runtime": cls._runtime_state(
                lifecycle_state=lifecycle_state
            )["runtime"],
            "question_pack": {},
            "settings_projection": {},
            "advisor_v2_evidence": {},
            "recommendation_portfolio_v2": {},
            "reference_snapshot_feed": {},
            "recommendation_use": {},
        }

    def test_started_state_projects_one_active_runtime_contract(self) -> None:
        build_runtime_event = MagicMock(return_value=self._runtime_state())

        state = project_started_runtime_state(
            build_runtime_event,
            run_id="run-a",
            phase="scan",
            completed_targets=3,
            total_targets=1,
            scan_lock_stale_seconds=300,
        )

        runtime = state["runtime"]
        self.assertEqual(state["schema_version"], 1)
        self.assertEqual(runtime["history_count"], 3)
        self.assertTrue(runtime["is_running"])
        self.assertIsNone(runtime["last_error"])
        self.assertEqual(runtime["current_run_id"], "run-a")
        self.assertEqual(runtime["current_phase"], "scan")
        self.assertEqual(runtime["completed_targets"], 3)
        self.assertEqual(runtime["total_targets"], 3)
        self.assertEqual(runtime["progress_percent"], 100)
        self.assertEqual(runtime["progress_completed"], 3)
        self.assertEqual(runtime["progress_total"], 3)
        self.assertEqual(runtime["execution_timeout_seconds"], 60)
        self.assertEqual(runtime["lifecycle_state"], "active_scan")
        self.assertEqual(runtime["state_changed_at"], runtime["updated_at"])
        self.assertEqual(
            (
                datetime.fromisoformat(runtime["lease_expires_at"])
                - datetime.fromisoformat(runtime["updated_at"])
            ).total_seconds(),
            300,
        )
        build_runtime_event.assert_called_once_with()

    def test_started_state_requires_a_runtime_projection(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing runtime"):
            project_started_runtime_state(
                MagicMock(return_value={"schema_version": 1}),
                run_id="run-a",
                phase="repair",
                completed_targets=0,
                total_targets=1,
                scan_lock_stale_seconds=300,
            )

    def test_terminal_failure_attaches_one_authoritative_snapshot(self) -> None:
        state = self._snapshot_state(lifecycle_state="failed_terminal")
        build_snapshot = MagicMock(return_value=state)

        event = project_terminal_failure_event(
            build_snapshot,
            event_type="scan.failed",
            failure_category="scan_execution_failed",
            failure_message="boom",
            fields={"result_count": 2},
        )

        self.assertEqual(
            event,
            {
                "type": "scan.failed",
                "failure_category": "scan_execution_failed",
                "failure_message": "boom",
                "result_count": 2,
                "state": state,
                "updated_at": "2026-07-29T12:00:00+08:00",
            },
        )
        build_snapshot.assert_called_once_with()

    def test_terminal_snapshot_failure_propagates_to_the_transport(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "snapshot unavailable"):
            project_terminal_failure_event(
                MagicMock(side_effect=RuntimeError("snapshot unavailable")),
                event_type="repair.failed",
                failure_category="repair_failed",
                failure_message="boom",
            )

    def test_finalizing_event_only_fills_missing_runtime_state(self) -> None:
        state = self._runtime_state(lifecycle_state="finalizing")
        build_runtime_event = MagicMock(return_value=state)
        existing = {"type": "scan.finalizing", "state": {"existing": True}}

        self.assertIs(
            project_finalizing_runtime_event(build_runtime_event, existing),
            existing,
        )
        build_runtime_event.assert_not_called()

        event = {"type": "scan.finalizing"}
        self.assertIs(
            project_finalizing_runtime_event(build_runtime_event, event),
            event,
        )
        self.assertIs(event["state"], state)

        failed_event = project_finalizing_runtime_event(
            MagicMock(side_effect=RuntimeError("state unavailable")),
            {"type": "repair.finalizing"},
        )
        self.assertEqual(failed_event["runtime_state_error"], "state unavailable")

    def test_finalization_failure_preserves_recorded_recovery_diagnostics(self) -> None:
        snapshot = self._snapshot_state(lifecycle_state="finalizing")
        recorded = self._runtime_state(lifecycle_state="finalizing")
        recorded["persistence_errors"] = ["journal unavailable"]
        recorder = MagicMock(return_value=recorded)

        event = project_terminal_failure_event(
            MagicMock(return_value=snapshot),
            event_type="timeout-repair.failed",
            failure_category="timeout_repair_finalization_commit_failed",
            failure_message="commit failed",
            fields={"run_id": "run-a"},
            prepare_failure_state=recorder,
            preparation_error_field="finalization_recording_error",
        )

        self.assertIs(event["state"], snapshot)
        self.assertEqual(event["persistence_errors"], ["journal unavailable"])
        self.assertIsNot(
            event["persistence_errors"],
            recorded["persistence_errors"],
        )
        recorder.assert_called_once_with()

    def test_finalization_recording_failure_keeps_authoritative_snapshot(
        self,
    ) -> None:
        snapshot = self._snapshot_state(lifecycle_state="finalizing")
        build_snapshot = MagicMock(return_value=snapshot)

        event = project_terminal_failure_event(
            build_snapshot,
            event_type="scan.failed",
            failure_category="finalization_commit_failed",
            failure_message="commit failed",
            prepare_failure_state=MagicMock(
                side_effect=OSError("journal unavailable")
            ),
            preparation_error_field="finalization_recording_error",
        )

        self.assertEqual(event["finalization_recording_error"], "journal unavailable")
        self.assertIs(event["state"], snapshot)
        build_snapshot.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
