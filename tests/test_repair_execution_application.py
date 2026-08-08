from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scanner.active_run_store import ActiveRunStore
from scanner.execution import (
    ExecutionEngine,
    ExecutionJob,
    RunLifecycleCoordinator,
    RunStateMachine,
)
from scanner.history_store import HistoryStore
from scanner.models import AppConfig
from scanner.repair_execution_application import (
    RepairExecutionApplicationService,
    RepairExecutionPorts,
)
from scanner.run_journal import RunJournalStore
from scanner.service import MonitorService


class RepairExecutionApplicationTests(unittest.TestCase):
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

    @staticmethod
    def _run_entry(candidate_id: str) -> dict[str, object]:
        return {
            "candidate_id": candidate_id,
            "model": "model-a",
            "effort": "high",
            "label": "Model A / high",
            "status": "pending",
            "phase": "repair",
            "attempts_completed": 0,
            "attempts_per_target": 1,
            "final_status": None,
            "reasoning_tokens": None,
            "flags": [],
            "error_message": None,
        }

    def _application(
        self,
        root: Path,
        *,
        job_planner: object,
        journal_event: Mock | None = None,
    ) -> tuple[
        RepairExecutionApplicationService,
        dict[str, object],
        HistoryStore,
        ActiveRunStore,
        Mock,
    ]:
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
        application = RepairExecutionApplicationService(
            runtime_state=runtime_state,
            state_machine=state_machine,
            lifecycle=lifecycle,
            engine=ExecutionEngine(),
            history_store=history_store,
            active_run_store=active_run_store,
            job_planner=job_planner,
            target_resolver=Mock(),
            ports=RepairExecutionPorts(
                build_run_entries=lambda **_kwargs: [
                    self._run_entry("candidate-a")
                ],
                persist_active_run=Mock(),
                journal_event=journal_event or Mock(),
                run_target=Mock(),
                emit_progress_event=Mock(),
                reset_progress_state_cache=reset_progress_state_cache,
                set_last_control_action=Mock(),
                lease_duration_seconds=Mock(return_value=420),
                timestamp=lambda: "2026-07-29T12:00:00+08:00",
            ),
        )
        return (
            application,
            runtime_state,
            history_store,
            active_run_store,
            reset_progress_state_cache,
        )

    @staticmethod
    def _repair_plan(*, operation_kind: str) -> SimpleNamespace:
        question = SimpleNamespace(id="q1", title="Question 1")
        target = SimpleNamespace(
            candidate_id="candidate-a",
            display_label="Model A / high",
        )
        return SimpleNamespace(
            operation_kind=operation_kind,
            config=AppConfig.default(),
            persisted_active=None,
            history=(),
            metadata={"run_id": "run-repair-setup-failure"},
            requested_group_id="run-repair-setup-failure",
            group_member_run_ids=("run-repair-setup-failure",),
            persist_run_id="run-repair-setup-failure",
            question_pack=object(),
            candidate_id="candidate-a",
            question_id=None,
            selected_targets=(target,),
            all_targets=(target,),
            questions=(question,),
            persisted_run_metadata={
                "run_id": "run-repair-setup-failure",
                "status": "degraded",
            },
            completion_only=False,
            completed_by_candidate=(("candidate-a", 0),),
            repair_steps_by_candidate=(("candidate-a", (question,)),),
            latest_by_question=lambda _candidate_id: {},
            steps_for=lambda _candidate_id: (question,),
        )

    def test_repair_execution_ports_stay_explicit_and_narrow(self) -> None:
        self.assertEqual(
            [field.name for field in fields(RepairExecutionPorts)],
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
            ],
        )

    def test_candidate_facade_plans_validates_and_delegates_once(self) -> None:
        service = object.__new__(MonitorService)
        plan = SimpleNamespace(
            operation_kind="candidate_repair",
            requested_run_id="run-a",
            candidate_id="candidate-a",
            question_id="question-a",
        )
        callback = Mock()
        service.runtime_state = {"is_running": False}
        service.repair_planner = SimpleNamespace(
            plan_candidate=Mock(return_value=plan)
        )
        service.repair_execution_application = SimpleNamespace(
            execute_candidate=Mock(return_value=["result"])
        )

        results = MonitorService.repair_failed_candidate(
            service,
            run_id="run-a",
            candidate_id="candidate-a",
            question_id="question-a",
            progress_callback=callback,
            retain_finalizing_state=True,
        )

        self.assertEqual(results, ["result"])
        service.repair_planner.plan_candidate.assert_called_once_with(
            run_id="run-a",
            candidate_id="candidate-a",
            question_id="question-a",
        )
        service.repair_execution_application.execute_candidate.assert_called_once_with(
            plan=plan,
            progress_callback=callback,
            retain_finalizing_state=True,
        )

    def test_batch_facade_uses_prepared_plan_and_delegates_once(self) -> None:
        service = object.__new__(MonitorService)
        plan = SimpleNamespace(
            operation_kind="timeout_repair",
            requested_run_id="run-a",
            selected_candidate_ids=("candidate-a",),
            metadata={"requested_candidate_ids": ["candidate-a"]},
        )
        callback = Mock()
        service.runtime_state = {"is_running": False}
        service.repair_execution_application = SimpleNamespace(
            execute_batch=Mock(return_value=["result"])
        )

        results = MonitorService.repair_timed_out_questions(
            service,
            run_id="run-a",
            candidate_ids=["candidate-a"],
            progress_callback=callback,
            repair_plan=plan,
            retain_finalizing_state=True,
        )

        self.assertEqual(results, ["result"])
        service.repair_execution_application.execute_batch.assert_called_once_with(
            plan=plan,
            progress_callback=callback,
            retain_finalizing_state=True,
        )

    def test_candidate_setup_failures_settle_runtime_and_clear_cache(self) -> None:
        for failure_point in ("journal", "planner"):
            with self.subTest(failure_point=failure_point), TemporaryDirectory() as temporary:
                planner = SimpleNamespace(
                    plan_candidate_repair=Mock(
                        side_effect=(
                            OSError("candidate planner failed")
                            if failure_point == "planner"
                            else None
                        ),
                        return_value=[],
                    ),
                    plan_batch_repair=Mock(),
                )
                journal_event = Mock(
                    side_effect=(
                        OSError("candidate journal failed")
                        if failure_point == "journal"
                        else None
                    )
                )
                (
                    application,
                    runtime_state,
                    history_store,
                    active_run_store,
                    reset_progress_state_cache,
                ) = self._application(
                    Path(temporary),
                    job_planner=planner,
                    journal_event=journal_event,
                )

                with self.assertRaisesRegex(OSError, f"candidate {failure_point} failed"):
                    application.execute_candidate(
                        plan=self._repair_plan(
                            operation_kind="candidate_repair"
                        ),
                        progress_callback=None,
                        retain_finalizing_state=True,
                    )

                self.assertFalse(runtime_state["is_running"])
                self.assertEqual(runtime_state["lifecycle_state"], "failed")
                self.assertEqual(runtime_state["active_evaluation_count"], 0)
                self.assertEqual(runtime_state["queued_evaluation_count"], 0)
                self.assertEqual(reset_progress_state_cache.call_count, 2)
                reset_progress_state_cache.assert_called_with()
                self.assertEqual(history_store.load_all(), [])
                self.assertIsNone(active_run_store.load())
                self.assertEqual(
                    history_store.load_run_metadata(
                        "run-repair-setup-failure"
                    )["status"],
                    "degraded",
                )

    def test_completion_only_candidate_does_not_enter_started_cleanup(self) -> None:
        with TemporaryDirectory() as temporary:
            planner = SimpleNamespace(
                plan_candidate_repair=Mock(),
                plan_batch_repair=Mock(),
            )
            (
                application,
                runtime_state,
                _history_store,
                _active_run_store,
                reset_progress_state_cache,
            ) = self._application(
                Path(temporary),
                job_planner=planner,
            )
            plan = self._repair_plan(operation_kind="candidate_repair")
            plan.completion_only = True
            plan.steps_for = lambda _candidate_id: ()

            with patch(
                "scanner.repair_execution_application.recomputed_repair_metadata",
                return_value={
                    "run_id": "run-repair-setup-failure",
                    "status": "completed",
                },
            ):
                results = application.execute_candidate(
                    plan=plan,
                    progress_callback=None,
                    retain_finalizing_state=False,
                )

            self.assertEqual(results, [])
            self.assertFalse(runtime_state["is_running"])
            self.assertEqual(runtime_state["lifecycle_state"], "idle")
            reset_progress_state_cache.assert_not_called()
            planner.plan_candidate_repair.assert_not_called()

    def test_batch_planner_failure_settles_runtime_and_clears_cache(self) -> None:
        for operation_kind in ("failed_repair", "timeout_repair"):
            with (
                self.subTest(operation_kind=operation_kind),
                TemporaryDirectory() as temporary,
            ):
                planner = SimpleNamespace(
                    plan_candidate_repair=Mock(),
                    plan_batch_repair=Mock(
                        side_effect=OSError("batch planner failed")
                    ),
                )
                (
                    application,
                    runtime_state,
                    history_store,
                    active_run_store,
                    reset_progress_state_cache,
                ) = self._application(
                    Path(temporary),
                    job_planner=planner,
                )

                with self.assertRaisesRegex(OSError, "batch planner failed"):
                    application.execute_batch(
                        plan=self._repair_plan(
                            operation_kind=operation_kind
                        ),
                        progress_callback=None,
                        retain_finalizing_state=True,
                    )

                self.assertFalse(runtime_state["is_running"])
                self.assertEqual(runtime_state["lifecycle_state"], "failed")
                self.assertEqual(runtime_state["active_evaluation_count"], 0)
                self.assertEqual(runtime_state["queued_evaluation_count"], 0)
                self.assertEqual(reset_progress_state_cache.call_count, 2)
                reset_progress_state_cache.assert_called_with()
                self.assertEqual(history_store.load_all(), [])
                self.assertIsNone(active_run_store.load())
                self.assertEqual(
                    history_store.load_run_metadata(
                        "run-repair-setup-failure"
                    )["status"],
                    "degraded",
                )

    def test_candidate_and_batch_variants_share_execution_outcomes(self) -> None:
        variants = (
            ("candidate_repair", "repair"),
            ("failed_repair", "repair"),
            ("timeout_repair", "timeout-repair"),
        )
        behaviors = (
            "success",
            "pause",
            "stop",
            "pause_after_complete",
            "worker_failure",
        )
        for operation_kind, event_prefix in variants:
            for behavior in behaviors:
                with (
                    self.subTest(
                        operation_kind=operation_kind,
                        behavior=behavior,
                    ),
                    TemporaryDirectory() as temporary,
                ):
                    question = SimpleNamespace(id="q1", title="Question 1")
                    target = SimpleNamespace(
                        candidate_id="candidate-a",
                        display_label="Model A / high",
                    )
                    job = ExecutionJob(
                        target=target,
                        question=question,
                        attempt_index=1,
                    )
                    planner = SimpleNamespace(
                        plan_candidate_repair=Mock(return_value=[job]),
                        plan_batch_repair=Mock(return_value=[job]),
                    )
                    journal_event = Mock()
                    (
                        application,
                        runtime_state,
                        _history_store,
                        _active_run_store,
                        reset_progress_state_cache,
                    ) = self._application(
                        Path(temporary),
                        job_planner=planner,
                        journal_event=journal_event,
                    )
                    result = SimpleNamespace(
                        final_status="completed",
                        reasoning_tokens=10,
                        flags=(),
                        error_message=None,
                    )
                    application.ports.run_target.return_value = result

                    command = SimpleNamespace(
                        session=object(),
                        results=[],
                        control_action=None,
                        begin=Mock(),
                        clear_control_action=Mock(),
                        settle=Mock(return_value="idle"),
                    )

                    def begin(**kwargs: object) -> None:
                        runtime_state["run_entries"] = kwargs["run_entries"]

                    command.begin.side_effect = begin

                    def clear_control_action() -> None:
                        command.control_action = None

                    command.clear_control_action.side_effect = clear_control_action

                    def execute_jobs(
                        jobs: list[ExecutionJob],
                        **callbacks: object,
                    ) -> None:
                        current_job = jobs[0]
                        on_started = callbacks["on_started"]
                        after_started = callbacks["after_started"]
                        assert callable(on_started)
                        assert callable(after_started)
                        on_started(current_job)
                        after_started(current_job)
                        if behavior in {"pause", "stop"}:
                            command.control_action = behavior
                            on_stopped = callbacks.get("on_stopped")
                            if callable(on_stopped):
                                on_stopped(current_job)
                            return
                        if behavior == "worker_failure":
                            error = OSError("worker failed")
                            on_failed = callbacks.get("on_failed")
                            if callable(on_failed):
                                on_failed(current_job, error)
                            raise error
                        run_job = callbacks["run_job"]
                        on_finished = callbacks["on_finished"]
                        after_finished = callbacks["after_finished"]
                        assert callable(run_job)
                        assert callable(on_finished)
                        assert callable(after_finished)
                        completed = run_job(current_job)
                        command.results.append(completed)
                        on_finished(current_job, completed)
                        after_finished(current_job, completed)
                        if behavior == "pause_after_complete":
                            command.control_action = "pause"

                    command.execute_jobs = Mock(side_effect=execute_jobs)
                    outcomes = SimpleNamespace(
                        finish_completed=Mock(),
                        finish_controlled=Mock(),
                        finish_failed=Mock(),
                    )

                    with (
                        patch(
                            "scanner.repair_execution_application.RepairExecutionCommand",
                            return_value=command,
                        ),
                        patch(
                            "scanner.repair_execution_application.RepairOutcomeCoordinator",
                            return_value=outcomes,
                        ) as outcome_type,
                    ):
                        invoke = (
                            application.execute_candidate
                            if operation_kind == "candidate_repair"
                            else application.execute_batch
                        )
                        arguments = {
                            "plan": self._repair_plan(
                                operation_kind=operation_kind
                            ),
                            "progress_callback": None,
                            "retain_finalizing_state": True,
                        }
                        if behavior == "worker_failure":
                            with self.assertRaisesRegex(OSError, "worker failed"):
                                invoke(**arguments)
                        else:
                            self.assertEqual(invoke(**arguments), command.results)

                    self.assertEqual(command.begin.call_count, 1)
                    self.assertEqual(command.execute_jobs.call_count, 1)
                    self.assertEqual(command.settle.call_count, 1)
                    self.assertEqual(reset_progress_state_cache.call_count, 2)
                    outcome_arguments = outcome_type.call_args.kwargs
                    self.assertEqual(
                        outcome_arguments["event_prefix"],
                        event_prefix,
                    )
                    if operation_kind == "candidate_repair":
                        self.assertEqual(
                            outcome_arguments["journal_scope"],
                            {
                                "candidate_id": "candidate-a",
                                "question_ids": ["q1"],
                            },
                        )
                        journal_event.assert_called_once_with(
                            "repair.started",
                            {
                                "candidate_id": "candidate-a",
                                "question_ids": ["q1"],
                                "scope": "candidate",
                            },
                            run_id="run-repair-setup-failure",
                        )
                    else:
                        self.assertEqual(
                            outcome_arguments["journal_scope"],
                            {"candidate_ids": ["candidate-a"]},
                        )
                        journal_event.assert_not_called()

                    if behavior in {"pause", "stop"}:
                        outcomes.finish_controlled.assert_called_once()
                        outcomes.finish_completed.assert_not_called()
                        outcomes.finish_failed.assert_not_called()
                    elif behavior == "worker_failure":
                        outcomes.finish_failed.assert_called_once_with(
                            error_message="worker failed",
                            result_count=0,
                        )
                        outcomes.finish_completed.assert_not_called()
                        outcomes.finish_controlled.assert_not_called()
                    else:
                        outcomes.finish_completed.assert_called_once()
                        outcomes.finish_controlled.assert_not_called()
                        outcomes.finish_failed.assert_not_called()
                        if behavior == "pause_after_complete":
                            command.clear_control_action.assert_called_once_with()

    def test_candidate_and_batch_persistence_failures_share_terminal_cleanup(self) -> None:
        for operation_kind in (
            "candidate_repair",
            "failed_repair",
            "timeout_repair",
        ):
            with self.subTest(operation_kind=operation_kind), TemporaryDirectory() as temporary:
                question = SimpleNamespace(id="q1", title="Question 1")
                target = SimpleNamespace(
                    candidate_id="candidate-a",
                    display_label="Model A / high",
                )
                job = ExecutionJob(
                    target=target,
                    question=question,
                    attempt_index=1,
                )
                planner = SimpleNamespace(
                    plan_candidate_repair=Mock(return_value=[job]),
                    plan_batch_repair=Mock(return_value=[job]),
                )
                (
                    application,
                    runtime_state,
                    history_store,
                    active_run_store,
                    reset_progress_state_cache,
                ) = self._application(
                    Path(temporary),
                    job_planner=planner,
                )
                application.ports.persist_active_run.side_effect = OSError(
                    "checkpoint failed"
                )

                if operation_kind == "candidate_repair":
                    invoke = application.execute_candidate
                else:
                    invoke = application.execute_batch
                    original_execute = application.engine.execute

                    def fail_batch_checkpoint(*args: object, **kwargs: object) -> object:
                        return original_execute(*args, **kwargs)

                    application.engine.execute = fail_batch_checkpoint  # type: ignore[method-assign]

                with self.assertRaisesRegex(OSError, "checkpoint failed"):
                    invoke(
                        plan=self._repair_plan(operation_kind=operation_kind),
                        progress_callback=None,
                        retain_finalizing_state=True,
                    )

                self.assertFalse(runtime_state["is_running"])
                self.assertEqual(runtime_state["lifecycle_state"], "failed")
                self.assertEqual(reset_progress_state_cache.call_count, 2)
                self.assertEqual(history_store.load_all(), [])
                self.assertIsNone(active_run_store.load())


if __name__ == "__main__":
    unittest.main()
