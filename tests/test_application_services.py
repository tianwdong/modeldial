from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from scanner.application_services import RepairCommand, ScanCommand
from scanner.scan_planner import ScanPlanningError
from scanner.service import SCAN_LOCK_STALE_SECONDS


class ApplicationServiceTest(unittest.TestCase):
    @staticmethod
    @contextmanager
    def _acquired_lock(*_args: object, **_kwargs: object):
        yield True

    @staticmethod
    def _runtime_state(
        *,
        completed: int = 0,
        total: int = 0,
        phase: str | None = None,
    ) -> dict[str, object]:
        return {
            "schema_version": 1,
            "runtime": {
                "enabled_target_count": 1,
                "history_count": 3,
                "is_running": False,
                "completed_targets": completed,
                "total_targets": total,
                "progress_completed": completed,
                "progress_total": total,
                "current_phase": phase,
                "lifecycle_state": "paused_recoverable",
                "execution_timeout_seconds": 120,
            },
        }

    @staticmethod
    def _app_snapshot_state(
        *,
        lifecycle_state: str = "idle",
    ) -> dict[str, object]:
        return {
            "schema_version": 2,
            "config": {},
            "dashboard": {},
            "runtime": {"lifecycle_state": lifecycle_state},
            "question_pack": {},
            "settings_projection": {},
            "advisor_v2_evidence": {},
            "recommendation_portfolio_v2": {},
            "reference_snapshot_feed": {},
            "recommendation_use": {},
        }

    def test_scan_command_executes_the_prepared_plan(self) -> None:
        service = MagicMock()
        plan = MagicMock()
        service.plan_scan.return_value = plan
        service.run_enabled_targets.return_value = [MagicMock()]
        command = ScanCommand(service)

        prepared = command.plan(
            force_restart=False,
            requested_candidate_ids=["candidate-a"],
            selection_mode="regular",
            custom_round_mode="new_round",
            evaluation_profile_id="quick",
            upgrade_from_run_id=None,
        )
        callback = MagicMock()
        results = command.execute(prepared, progress_callback=callback)

        self.assertIs(prepared, plan)
        self.assertEqual(results, service.run_enabled_targets.return_value)
        service.run_enabled_targets.assert_called_once_with(
            scan_plan=plan,
            retain_finalizing_state=True,
            progress_callback=callback,
        )

    def test_candidate_repair_executes_the_prepared_plan(self) -> None:
        service = MagicMock()
        plan = MagicMock(
            requested_run_id="run-a",
            candidate_id="candidate-a",
            question_id="question-a",
        )
        service.repair_planner.plan_candidate.return_value = plan
        service.repair_failed_candidate.return_value = [MagicMock()]
        command = RepairCommand(service)

        prepared = command.plan_candidate(
            run_id="run-a",
            candidate_id="candidate-a",
            question_id="question-a",
        )
        callback = MagicMock()
        results = command.execute_candidate(prepared, progress_callback=callback)

        self.assertIs(prepared, plan)
        self.assertEqual(results, service.repair_failed_candidate.return_value)
        service.repair_failed_candidate.assert_called_once_with(
            run_id="run-a",
            candidate_id="candidate-a",
            question_id="question-a",
            progress_callback=callback,
            repair_plan=plan,
            retain_finalizing_state=True,
        )

    def test_batch_repair_executes_the_prepared_plan(self) -> None:
        service = MagicMock()
        plan = MagicMock(
            requested_run_id="run-a",
            selected_candidate_ids=("candidate-a", "candidate-b"),
        )
        service.repair_planner.plan_timeout_batch.return_value = plan
        service.repair_timed_out_questions.return_value = [MagicMock()]
        command = RepairCommand(service)

        prepared = command.plan_batch(
            run_id="run-a",
            candidate_ids=["candidate-a", "candidate-b"],
            timeouts_only=True,
        )
        callback = MagicMock()
        results = command.execute_batch(
            prepared,
            timeouts_only=True,
            progress_callback=callback,
        )

        self.assertIs(prepared, plan)
        self.assertEqual(results, service.repair_timed_out_questions.return_value)
        service.repair_timed_out_questions.assert_called_once_with(
            run_id="run-a",
            candidate_ids=["candidate-a", "candidate-b"],
            progress_callback=callback,
            repair_plan=plan,
            retain_finalizing_state=True,
        )

    def test_stream_commands_pass_the_runtime_lease_to_the_process_lock(self) -> None:
        service = MagicMock()
        service.config_store = MagicMock()
        service.history_store = MagicMock()
        service.active_run_store = MagicMock()
        service.recover_orphaned_finalizing_run.return_value = {"status": "none"}
        service.build_runtime_event.return_value = self._runtime_state()
        service.plan_scan.return_value = SimpleNamespace(
            run_id="run-scan",
            requested_candidate_ids=("candidate-a",),
            selection_mode="regular",
            custom_round_mode="new_round",
            evaluation_profile_id="quick",
            evaluation_profile_label="极速筛选",
            evaluation_result_level="screening",
            question_count=1,
            upgrade_from_run_id=None,
            total_targets=1,
            completed_targets=0,
        )
        service.repair_planner.plan_candidate.return_value = SimpleNamespace(
            requested_run_id="run-a",
            persist_run_id="run-a",
            candidate_id="candidate-a",
            question_id=None,
            steps_for=lambda _candidate_id: [SimpleNamespace(id="q1")],
        )
        service.repair_planner.plan_failed_batch.return_value = SimpleNamespace(
            requested_run_id="run-a",
            persist_run_id="run-a",
            selected_candidate_ids=("candidate-a",),
            total_steps=1,
        )
        lease_callbacks: list[object] = []

        @contextmanager
        def capture_lock(*_args: object, **kwargs: object):
            lease_callbacks.append(kwargs["lease_heartbeat"])
            yield True

        streams = [
            ScanCommand(service).stream_events(
                force_restart=False,
                requested_candidate_ids=["candidate-a"],
                selection_mode="regular",
                custom_round_mode="new_round",
                evaluation_profile_id="quick",
                upgrade_from_run_id=None,
                process_lock=capture_lock,
                snapshot_builder=MagicMock(),
            ),
            RepairCommand(service).stream_candidate_events(
                run_id="run-a",
                candidate_id="candidate-a",
                question_id=None,
                process_lock=capture_lock,
                snapshot_builder=MagicMock(),
            ),
            RepairCommand(service).stream_batch_events(
                run_id="run-a",
                candidate_ids=["candidate-a"],
                timeouts_only=False,
                process_lock=capture_lock,
                snapshot_builder=MagicMock(),
            ),
        ]

        for stream in streams:
            next(stream)
            stream.close()

        self.assertEqual(len(lease_callbacks), 3)
        self.assertTrue(
            all(
                callback is service.heartbeat_active_run_lease
                for callback in lease_callbacks
            )
        )

    def test_scan_command_owns_the_complete_event_stream(self) -> None:
        service = MagicMock()
        service.config_store = MagicMock()
        service.history_store = MagicMock()
        service.active_run_store = MagicMock()
        service.last_control_action = None
        service.recover_orphaned_finalizing_run.return_value = {"status": "none"}
        service.scan_terminal_failure_state.return_value = None
        service.complete_finalizing_snapshot.return_value = {"final": True}
        service.build_runtime_event.return_value = {
            "schema_version": 1,
            "runtime": {"lifecycle_state": "finalizing"},
        }
        service.plan_scan.return_value = SimpleNamespace(
            run_id="run-scan",
            requested_candidate_ids=("candidate-a",),
            selection_mode="regular",
            custom_round_mode="new_round",
            evaluation_profile_id="quick",
            evaluation_profile_label="极速筛选",
            evaluation_result_level="screening",
            question_count=1,
            upgrade_from_run_id=None,
            total_targets=1,
            completed_targets=0,
        )

        def execute(**kwargs: object) -> list[object]:
            kwargs["progress_callback"]({"type": "scan.progress"})
            kwargs["progress_callback"]({"type": "scan.finalizing"})
            return [object()]

        service.run_enabled_targets.side_effect = execute
        snapshot_builder = MagicMock(return_value={"projected": True})
        prepare_execution = MagicMock()

        events = list(
            ScanCommand(service).stream_events(
                force_restart=False,
                requested_candidate_ids=["candidate-a"],
                selection_mode="regular",
                custom_round_mode="new_round",
                evaluation_profile_id="quick",
                upgrade_from_run_id=None,
                process_lock=self._acquired_lock,
                snapshot_builder=snapshot_builder,
                prepare_execution=prepare_execution,
            )
        )

        self.assertEqual(
            [event["type"] for event in events],
            [
                "scan.started",
                "scan.progress",
                "scan.finalizing",
                "scan.finished",
            ],
        )
        self.assertEqual(events[0]["state"]["runtime"]["lifecycle_state"], "active_scan")
        self.assertEqual(events[0]["state"]["runtime"]["current_run_id"], "run-scan")
        self.assertEqual(events[0]["state"]["runtime"]["progress_completed"], 0)
        self.assertEqual(events[0]["state"]["runtime"]["progress_total"], 1)
        self.assertEqual(
            events[-2]["state"]["runtime"]["lifecycle_state"],
            "finalizing",
        )
        self.assertEqual(events[-1]["state"], {"final": True})
        service.complete_finalizing_snapshot.assert_called_once_with(
            {"projected": True},
            exclusive_lock_held=True,
        )
        prepare_execution.assert_called_once_with(service.plan_scan.return_value)

    def test_scan_pricing_preparation_failure_stops_before_execution(self) -> None:
        service = MagicMock()
        service.config_store = MagicMock()
        service.history_store = MagicMock()
        service.active_run_store = MagicMock()
        service.recover_orphaned_finalizing_run.return_value = {"status": "none"}
        service.plan_scan.return_value = SimpleNamespace(
            run_id="run-pricing-failed",
            resume=None,
        )
        terminal_snapshot = self._app_snapshot_state()

        events = list(
            ScanCommand(service).stream_events(
                force_restart=False,
                requested_candidate_ids=["candidate-a"],
                selection_mode="regular",
                custom_round_mode="new_round",
                evaluation_profile_id="quick",
                upgrade_from_run_id=None,
                process_lock=self._acquired_lock,
                snapshot_builder=MagicMock(),
                terminal_snapshot_builder=MagicMock(
                    return_value=terminal_snapshot
                ),
                prepare_execution=MagicMock(
                    side_effect=ValueError("pricing snapshot unavailable")
                ),
            )
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "scan.failed")
        self.assertEqual(
            events[0]["failure_category"],
            "pricing_preparation_failed",
        )
        service.run_enabled_targets.assert_not_called()

    def test_scan_planning_failure_carries_authoritative_snapshot(self) -> None:
        service = MagicMock()
        service.config_store = MagicMock()
        service.history_store = MagicMock()
        service.active_run_store = MagicMock()
        service.recover_orphaned_finalizing_run.return_value = {"status": "none"}
        service.plan_scan.side_effect = ScanPlanningError(
            "quick_candidate_count",
            "快速对比需要选择两个配置",
        )
        terminal_snapshot = self._app_snapshot_state()

        events = list(
            ScanCommand(service).stream_events(
                force_restart=False,
                requested_candidate_ids=["candidate-a"],
                selection_mode="custom",
                custom_round_mode="new_round",
                evaluation_profile_id="quick",
                upgrade_from_run_id=None,
                process_lock=self._acquired_lock,
                snapshot_builder=MagicMock(),
                terminal_snapshot_builder=MagicMock(
                    return_value=terminal_snapshot
                ),
            )
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "scan.failed")
        self.assertEqual(events[0]["failure_category"], "scan_planning_failed")
        self.assertEqual(events[0]["failure_reason"], "quick_candidate_count")
        self.assertEqual(
            events[0]["failure_message"],
            "快速对比需要选择两个配置",
        )
        self.assertIs(events[0]["state"], terminal_snapshot)
        service.run_enabled_targets.assert_not_called()

    def test_repair_stream_variants_preserve_complete_behavior_contract(self) -> None:
        variants = (
            {"name": "candidate", "prefix": "repair", "kind": "candidate"},
            {"name": "failed_batch", "prefix": "repair", "kind": "batch"},
            {
                "name": "timeout_batch",
                "prefix": "timeout-repair",
                "kind": "timeout_batch",
            },
        )
        scenarios = (
            "success",
            "lock_denied",
            "recovery_incomplete",
            "plan_failure",
            "plan_mismatch",
            "execution_exception",
            "pause",
            "stop",
            "projection_failure",
            "commit_failure",
        )

        for variant in variants:
            for scenario in scenarios:
                with self.subTest(variant=variant["name"], scenario=scenario):
                    self._assert_repair_stream_case(variant, scenario)

    def _assert_repair_stream_case(
        self,
        variant: dict[str, str],
        scenario: str,
    ) -> None:
        prefix = variant["prefix"]
        kind = variant["kind"]
        category_prefix = prefix.replace("-", "_")
        call_candidate_ids = ["candidate-a", "candidate-b"]
        target_fields: dict[str, object] = (
            {"candidate_id": "candidate-a"}
            if kind == "candidate"
            else {"candidate_ids": call_candidate_ids}
        )
        started_fields: dict[str, object] = (
            {
                "candidate_id": "candidate-a",
                "repairable_question_ids": ["q1", "q2"],
            }
            if kind == "candidate"
            else {"candidate_ids": ["candidate-b"]}
        )
        started_state = {"runtime_state": "started"}
        finalizing_state = {"runtime_state": "finalizing"}
        projected_state = {"snapshot": "projected"}
        final_state = {"snapshot": "final"}
        terminal_state = {
            "snapshot": "terminal",
            "runtime": {"updated_at": "terminal-time"},
        }
        progress_event = {
            "type": f"{prefix}.question.finished",
            "progress_marker": variant["name"],
        }

        service = MagicMock()
        service.config_store = MagicMock()
        service.history_store = MagicMock()
        service.active_run_store = MagicMock()
        service.last_control_action = scenario if scenario in {"pause", "stop"} else None
        service.recover_orphaned_finalizing_run.return_value = (
            {"status": "incomplete", "message": "recovery failed"}
            if scenario == "recovery_incomplete"
            else {"status": "none"}
        )
        service.complete_finalizing_snapshot.return_value = final_state
        service.record_finalization_projection_failure.return_value = {
            "recorded": "projection"
        }
        service.record_finalization_commit_failure.return_value = {
            "recorded": "commit"
        }

        persist_run_id = "other-run" if scenario == "plan_mismatch" else "persist-run"
        if kind == "candidate":
            plan = SimpleNamespace(
                requested_run_id="source-run",
                persist_run_id=persist_run_id,
                candidate_id="candidate-a",
                question_id="q0",
                steps_for=lambda _candidate_id: [
                    SimpleNamespace(id="q1"),
                    SimpleNamespace(id="q2"),
                ],
            )
            planner = service.repair_planner.plan_candidate
            executor = service.repair_failed_candidate
        else:
            plan = SimpleNamespace(
                requested_run_id="source-run",
                persist_run_id=persist_run_id,
                selected_candidate_ids=("candidate-b",),
                total_steps=2,
            )
            planner = (
                service.repair_planner.plan_timeout_batch
                if kind == "timeout_batch"
                else service.repair_planner.plan_failed_batch
            )
            executor = (
                service.repair_timed_out_questions
                if kind == "timeout_batch"
                else service.repair_failed_questions
            )
        if scenario == "plan_failure":
            planner.side_effect = RuntimeError("plan failed")
        else:
            planner.return_value = plan

        def execute(**kwargs: object) -> list[object]:
            progress_callback = kwargs["progress_callback"]
            self.assertTrue(callable(progress_callback))
            progress_callback(progress_event)
            if scenario == "execution_exception":
                raise RuntimeError("execution failed")
            return [object(), object()]

        executor.side_effect = execute
        if scenario == "commit_failure":
            service.complete_finalizing_snapshot.side_effect = OSError(
                "commit failed"
            )

        process_lock = MagicMock()
        lock_context = MagicMock()
        lock_context.__enter__.return_value = scenario != "lock_denied"
        lock_context.__exit__.return_value = False
        process_lock.return_value = lock_context
        snapshot_builder = MagicMock(return_value=projected_state)
        if scenario == "projection_failure":
            snapshot_builder.side_effect = RuntimeError("projection failed")
        terminal_snapshot_builder = MagicMock(return_value=terminal_state)
        insights_provider = MagicMock()

        def project_finalizing(
            _builder: object,
            event: dict[str, object],
        ) -> dict[str, object]:
            return {**event, "state": finalizing_state}

        with patch(
            "scanner.application_services.project_started_runtime_state",
            return_value=started_state,
        ) as started_projector, patch(
            "scanner.application_services.project_finalizing_runtime_event",
            side_effect=project_finalizing,
        ) as finalizing_projector:
            command = RepairCommand(service)
            expected_resume_run_id = (
                "expected-run" if scenario == "plan_mismatch" else None
            )
            if kind == "candidate":
                stream = command.stream_candidate_events(
                    run_id="source-run",
                    candidate_id="candidate-a",
                    question_id="q0",
                    expected_resume_run_id=expected_resume_run_id,
                    process_lock=process_lock,
                    snapshot_builder=snapshot_builder,
                    terminal_snapshot_builder=terminal_snapshot_builder,
                    codex_insights_provider=insights_provider,
                )
            else:
                stream = command.stream_batch_events(
                    run_id="source-run",
                    candidate_ids=call_candidate_ids,
                    timeouts_only=kind == "timeout_batch",
                    expected_resume_run_id=expected_resume_run_id,
                    process_lock=process_lock,
                    snapshot_builder=snapshot_builder,
                    terminal_snapshot_builder=terminal_snapshot_builder,
                    codex_insights_provider=insights_provider,
                )
            events = list(stream)

        failure = lambda category, message, fields: {
            "type": f"{prefix}.failed",
            "failure_category": category,
            "failure_message": message,
            **fields,
            "state": terminal_state,
            "updated_at": "terminal-time",
        }
        started_event = {
            "type": f"{prefix}.started",
            "run_id": "source-run",
            **started_fields,
            "total_targets": 2,
            "completed_targets": 0,
            "state": started_state,
        }
        finalizing_event = {
            "type": f"{prefix}.finalizing",
            "run_id": "source-run",
            **target_fields,
            "result_count": 2,
            "state": finalizing_state,
        }

        if scenario == "lock_denied":
            expected_events = [
                {
                    "type": f"{prefix}.already_running",
                    "run_id": "source-run",
                    **target_fields,
                    "state": projected_state,
                }
            ]
        elif scenario == "recovery_incomplete":
            expected_events = [
                failure(
                    "run_recovery_failed",
                    "recovery failed",
                    {"run_id": "source-run", **target_fields},
                )
            ]
        elif scenario == "plan_failure":
            expected_events = [
                failure(
                    f"{category_prefix}_plan_failed",
                    "plan failed",
                    {"run_id": "source-run", **target_fields},
                )
            ]
        elif scenario == "plan_mismatch":
            expected_events = [
                failure(
                    "auto_resume_plan_mismatch",
                    "自动续修计划未恢复预期运行，已阻止创建新任务",
                    {
                        "run_id": "expected-run",
                        "planned_run_id": "other-run",
                        **target_fields,
                    },
                )
            ]
        elif scenario == "execution_exception":
            expected_events = [
                started_event,
                progress_event,
                failure(
                    f"{category_prefix}_failed",
                    "execution failed",
                    {"run_id": "source-run", **target_fields},
                ),
            ]
        elif scenario in {"pause", "stop"}:
            expected_events = [
                started_event,
                progress_event,
                {
                    "type": f"{prefix}.{'paused' if scenario == 'pause' else 'stopped'}",
                    "run_id": "source-run",
                    **target_fields,
                    "result_count": 2,
                    "state": projected_state,
                },
            ]
        elif scenario == "projection_failure":
            expected_events = [
                started_event,
                progress_event,
                finalizing_event,
                failure(
                    f"{category_prefix}_terminal_projection_failed",
                    "projection failed",
                    {
                        "run_id": "source-run",
                        **target_fields,
                        "result_count": 2,
                    },
                ),
            ]
        elif scenario == "commit_failure":
            expected_events = [
                started_event,
                progress_event,
                finalizing_event,
                failure(
                    f"{category_prefix}_finalization_commit_failed",
                    "commit failed",
                    {
                        "run_id": "source-run",
                        **target_fields,
                        "result_count": 2,
                    },
                ),
            ]
        else:
            expected_events = [
                started_event,
                progress_event,
                finalizing_event,
                {
                    "type": f"{prefix}.finished",
                    "run_id": "source-run",
                    **target_fields,
                    "result_count": 2,
                    "state": final_state,
                },
            ]

        self.assertEqual(events, expected_events)
        process_lock.assert_called_once_with(
            service.active_run_store,
            service.history_store,
            lease_heartbeat=service.heartbeat_active_run_lease,
        )
        self.assertEqual(
            service.recover_orphaned_finalizing_run.call_count,
            0 if scenario == "lock_denied" else 1,
        )
        if scenario == "lock_denied":
            service.recover_orphaned_finalizing_run.assert_not_called()
        else:
            service.recover_orphaned_finalizing_run.assert_called_once_with(
                exclusive_lock_held=True
            )
        planner_expected = scenario not in {"lock_denied", "recovery_incomplete"}
        for planner_mock in (
            service.repair_planner.plan_candidate,
            service.repair_planner.plan_failed_batch,
            service.repair_planner.plan_timeout_batch,
        ):
            self.assertEqual(
                planner_mock.call_count,
                int(planner_expected and planner_mock is planner),
            )
        execution_expected = scenario in {
            "success",
            "execution_exception",
            "pause",
            "stop",
            "projection_failure",
            "commit_failure",
        }
        for executor_mock in (
            service.repair_failed_candidate,
            service.repair_failed_questions,
            service.repair_timed_out_questions,
        ):
            self.assertEqual(
                executor_mock.call_count,
                int(execution_expected and executor_mock is executor),
            )
        self.assertEqual(started_projector.call_count, int(execution_expected))
        if execution_expected:
            started_projector.assert_called_once_with(
                service.build_runtime_event,
                run_id="persist-run",
                phase="repair",
                completed_targets=0,
                total_targets=2,
                scan_lock_stale_seconds=SCAN_LOCK_STALE_SECONDS,
            )
        finalizing_expected = scenario in {
            "success",
            "projection_failure",
            "commit_failure",
        }
        self.assertEqual(finalizing_projector.call_count, int(finalizing_expected))
        self.assertEqual(
            service.complete_finalizing_snapshot.call_count,
            int(scenario in {"success", "commit_failure"}),
        )
        self.assertEqual(
            service.record_finalization_projection_failure.call_count,
            int(scenario == "projection_failure"),
        )
        self.assertEqual(
            service.record_finalization_commit_failure.call_count,
            int(scenario == "commit_failure"),
        )
        terminal_failure_expected = scenario in {
            "recovery_incomplete",
            "plan_failure",
            "plan_mismatch",
            "execution_exception",
            "projection_failure",
            "commit_failure",
        }
        self.assertEqual(
            terminal_snapshot_builder.call_count,
            int(terminal_failure_expected),
        )
        snapshot_expected = scenario in {
            "success",
            "lock_denied",
            "pause",
            "stop",
            "projection_failure",
            "commit_failure",
        }
        self.assertEqual(snapshot_builder.call_count, int(snapshot_expected))
        if snapshot_expected:
            snapshot_builder.assert_called_once_with(
                service.config_store,
                service.history_store,
                service.active_run_store,
                codex_insights_provider=insights_provider,
            )
        if terminal_failure_expected:
            terminal_snapshot_builder.assert_called_once_with(
                service.config_store,
                service.history_store,
                service.active_run_store,
                codex_insights_provider=insights_provider,
            )
        if scenario in {"success", "commit_failure"}:
            service.complete_finalizing_snapshot.assert_called_once_with(
                projected_state,
                exclusive_lock_held=True,
            )
        if scenario == "projection_failure":
            service.record_finalization_projection_failure.assert_called_once_with(
                "projection failed",
                exclusive_lock_held=True,
            )
        if scenario == "commit_failure":
            service.record_finalization_commit_failure.assert_called_once_with(
                "commit failed",
                exclusive_lock_held=True,
            )

        if planner_expected and scenario != "plan_failure":
            if kind == "candidate":
                planner.assert_called_once_with(
                    run_id="source-run",
                    candidate_id="candidate-a",
                    question_id="q0",
                )
            else:
                planner.assert_called_once_with(
                    run_id="source-run",
                    candidate_ids=call_candidate_ids,
                )
        if execution_expected:
            execute_kwargs = dict(executor.call_args.kwargs)
            self.assertTrue(callable(execute_kwargs.pop("progress_callback")))
            if kind == "candidate":
                self.assertEqual(
                    execute_kwargs,
                    {
                        "run_id": "source-run",
                        "candidate_id": "candidate-a",
                        "question_id": "q0",
                        "repair_plan": plan,
                        "retain_finalizing_state": True,
                    },
                )
            else:
                self.assertEqual(
                    execute_kwargs,
                    {
                        "run_id": "source-run",
                        "candidate_ids": ["candidate-b"],
                        "repair_plan": plan,
                        "retain_finalizing_state": True,
                    },
                )

    def test_auto_resume_guard_never_executes_a_new_scan_plan(self) -> None:
        service = MagicMock()
        service.config_store = MagicMock()
        service.history_store = MagicMock()
        service.active_run_store = MagicMock()
        service.recover_orphaned_finalizing_run.return_value = {"status": "none"}
        service.plan_scan.return_value = SimpleNamespace(
            run_id="run-new",
            resume=None,
        )
        snapshot_builder = MagicMock(return_value=self._app_snapshot_state())

        events = list(
            ScanCommand(service).stream_events(
                force_restart=False,
                requested_candidate_ids=None,
                selection_mode="regular",
                custom_round_mode="new_round",
                evaluation_profile_id=None,
                upgrade_from_run_id=None,
                expected_resume_run_id="run-existing",
                process_lock=self._acquired_lock,
                snapshot_builder=snapshot_builder,
            )
        )

        self.assertEqual([event["type"] for event in events], ["scan.failed"])
        self.assertEqual(events[0]["failure_category"], "auto_resume_plan_mismatch")
        service.run_enabled_targets.assert_not_called()

    def test_auto_resume_guard_never_executes_a_different_repair_run(self) -> None:
        snapshot_builder = MagicMock(return_value=self._app_snapshot_state())
        for operation in ("candidate", "batch"):
            with self.subTest(operation=operation):
                service = MagicMock()
                service.config_store = MagicMock()
                service.history_store = MagicMock()
                service.active_run_store = MagicMock()
                service.recover_orphaned_finalizing_run.return_value = {
                    "status": "none"
                }
                plan = SimpleNamespace(persist_run_id="run-other")
                if operation == "candidate":
                    service.repair_planner.plan_candidate.return_value = plan
                    events = list(
                        RepairCommand(service).stream_candidate_events(
                            run_id="source-run",
                            candidate_id="candidate-a",
                            question_id="q-1",
                            expected_resume_run_id="run-active",
                            process_lock=self._acquired_lock,
                            snapshot_builder=snapshot_builder,
                        )
                    )
                    service.repair_failed_candidate.assert_not_called()
                else:
                    service.repair_planner.plan_failed_batch.return_value = plan
                    events = list(
                        RepairCommand(service).stream_batch_events(
                            run_id="source-run",
                            candidate_ids=["candidate-a"],
                            timeouts_only=False,
                            expected_resume_run_id="run-active",
                            process_lock=self._acquired_lock,
                            snapshot_builder=snapshot_builder,
                        )
                    )
                    service.repair_failed_questions.assert_not_called()
                self.assertEqual(len(events), 1)
                self.assertEqual(
                    events[0]["failure_category"],
                    "auto_resume_plan_mismatch",
                )


if __name__ == "__main__":
    unittest.main()
