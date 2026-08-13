from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from scanner.active_run_store import ActiveRunStore
from scanner.execution import (
    ExecutionEngine,
    RunLifecycleCoordinator,
    RunStateMachine,
)
from scanner.execution_job_planner import ExecutionJobPlanner
from scanner.history_store import HistoryStore
from scanner.models import AppConfig
from scanner.run_journal import RunJournalStore
from scanner.scan_execution_application import (
    ScanExecutionApplicationService,
    ScanExecutionPorts,
    execution_policy_snapshot,
)
from scanner.service import MonitorService


class ScanExecutionApplicationTests(unittest.TestCase):
    @staticmethod
    def _runtime_state() -> dict[str, object]:
        return {
            "is_running": False,
            "lifecycle_state": "idle",
            "last_error": None,
            "current_phase": None,
            "progress_completed": 0,
            "progress_total": 0,
            "run_entries": [],
        }

    def test_execution_policy_snapshot_preserves_complete_rule_contract(self) -> None:
        config = AppConfig.default()
        config.system.max_concurrent_targets = 4
        config.system.max_concurrent_targets_by_connection = {
            "first-party": 2,
            "deepseek": 1,
        }

        payload = execution_policy_snapshot(config)

        self.assertEqual(
            list(payload["rules"]),
            sorted(config.rules),
        )
        self.assertEqual(
            payload["rules"],
            {
                name: rule.to_dict()
                for name, rule in sorted(config.rules.items())
            },
        )
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["max_concurrent_targets"], 4)
        self.assertEqual(
            payload["max_concurrent_targets_by_connection"],
            {"deepseek": 1, "first-party": 2},
        )

    def test_execution_policy_snapshot_preserves_legacy_shape_without_groups(
        self,
    ) -> None:
        payload = execution_policy_snapshot(AppConfig.default())

        self.assertEqual(payload["schema_version"], 1)
        self.assertNotIn("max_concurrent_targets", payload)
        self.assertNotIn("max_concurrent_targets_by_connection", payload)

    def test_scan_execution_ports_stay_explicit_and_narrow(self) -> None:
        self.assertEqual(
            [field.name for field in fields(ScanExecutionPorts)],
            [
                "build_run_entries",
                "persist_active_run",
                "journal_event",
                "run_target",
                "emit_progress_event",
                "reset_progress_state_cache",
                "set_last_control_action",
                "lease_duration_seconds",
                "timestamp",
                "log",
            ],
        )

    def test_scan_facade_plans_clears_and_delegates_once(self) -> None:
        service = object.__new__(MonitorService)
        plan = SimpleNamespace(force_restart=True)
        callback = Mock()
        service.last_control_action = "pause"
        service.plan_scan = Mock(return_value=plan)
        service.active_run_store = SimpleNamespace(clear=Mock())
        service.scan_execution_application = SimpleNamespace(
            execute=Mock(return_value=["result"])
        )

        results = MonitorService.run_enabled_targets(
            service,
            force_restart=True,
            retain_finalizing_state=True,
            progress_callback=callback,
            requested_candidate_ids=["candidate-a"],
            selection_mode="custom",
            custom_round_mode="append",
            evaluation_profile_id="quick",
            upgrade_from_run_id="run-parent",
        )

        self.assertEqual(results, ["result"])
        self.assertIsNone(service.last_control_action)
        service.plan_scan.assert_called_once_with(
            force_restart=True,
            requested_candidate_ids=["candidate-a"],
            selection_mode="custom",
            custom_round_mode="append",
            evaluation_profile_id="quick",
            upgrade_from_run_id="run-parent",
        )
        service.active_run_store.clear.assert_called_once_with()
        service.scan_execution_application.execute.assert_called_once_with(
            scan_plan=plan,
            retain_finalizing_state=True,
            progress_callback=callback,
        )

    def test_pre_loop_persist_failure_settles_runtime_and_clears_cache(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime_state = self._runtime_state()
            history_store = HistoryStore(root / "history.jsonl")
            active_run_store = ActiveRunStore(root / "active_run.json")
            state_machine = RunStateMachine(
                runtime_state,
                timestamp=lambda: "2026-07-29T12:00:00+08:00",
            )
            lifecycle = RunLifecycleCoordinator(
                state_machine=state_machine,
                history_store=history_store,
                active_run_store=active_run_store,
                journal_store=RunJournalStore(root / "runs"),
                timestamp=lambda: "2026-07-29T12:00:00+08:00",
            )
            reset_progress_state_cache = Mock()
            persist_active_run = Mock(
                side_effect=OSError("initial active run persist failed")
            )
            application = ScanExecutionApplicationService(
                runtime_state=runtime_state,
                state_machine=state_machine,
                lifecycle=lifecycle,
                engine=ExecutionEngine(),
                history_store=history_store,
                active_run_store=active_run_store,
                job_planner=ExecutionJobPlanner(),
                ports=ScanExecutionPorts(
                    build_run_entries=Mock(return_value=[]),
                    persist_active_run=persist_active_run,
                    journal_event=Mock(),
                    run_target=Mock(),
                    emit_progress_event=Mock(),
                    reset_progress_state_cache=reset_progress_state_cache,
                    set_last_control_action=Mock(),
                    lease_duration_seconds=Mock(return_value=420),
                    timestamp=lambda: "2026-07-29T12:00:00+08:00",
                    log=Mock(),
                ),
            )
            config = AppConfig.default()
            plan = SimpleNamespace(
                config=config,
                history=(),
                regular_candidate_ids=(),
                comparison_targets=(),
                enabled_targets=(),
                evaluation_profile=SimpleNamespace(
                    id="quick",
                    result_level="screening",
                ),
                enabled_questions=(),
                question_ids=(),
                attempts_per_target=1,
                total_targets=0,
                resume=None,
                run_id="run-scan-setup-failure",
                run_metadata={
                    "run_id": "run-scan-setup-failure",
                    "status": "running",
                },
                execution_selection_mode="regular",
                effective_requested_candidate_ids=(),
            )

            with self.assertRaisesRegex(
                OSError,
                "initial active run persist failed",
            ):
                application.execute(
                    scan_plan=plan,
                    retain_finalizing_state=True,
                    progress_callback=None,
                )

            self.assertFalse(runtime_state["is_running"])
            self.assertEqual(runtime_state["lifecycle_state"], "failed")
            self.assertEqual(runtime_state["active_evaluation_count"], 0)
            self.assertEqual(runtime_state["queued_evaluation_count"], 0)
            self.assertEqual(reset_progress_state_cache.call_count, 2)
            reset_progress_state_cache.assert_called_with()
            self.assertEqual(history_store.load_all(), [])
            self.assertEqual(
                history_store.load_run_metadata(
                    "run-scan-setup-failure"
                )["status"],
                "failed",
            )


if __name__ == "__main__":
    unittest.main()
