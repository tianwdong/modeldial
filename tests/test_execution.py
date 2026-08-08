from __future__ import annotations

import threading
import tempfile
import time
import unittest
from pathlib import Path

from scanner.active_run_store import ActiveRunStore
from scanner.execution import (
    ExecutionContext,
    ExecutionEngine,
    ExecutionJobCallbacks,
    ExecutionSession,
    RunControlCoordinator,
    RunLifecycleCoordinator,
    RunStateMachine,
)
from scanner.history_store import HistoryStore
from scanner.run_journal import RunJournalStore


class _OrderedCallRecorder:
    def __init__(self, *, fail_on: str | None = None) -> None:
        self.calls: list[str] = []
        self.fail_on = fail_on

    def record(self, operation: str) -> None:
        self.calls.append(operation)
        if operation == self.fail_on:
            raise RuntimeError(f"injected failure at {operation}")


class _OrderedHistoryStore:
    def __init__(self, recorder: _OrderedCallRecorder) -> None:
        self.recorder = recorder
        self.saved_metadata: list[dict[str, object]] = []

    def save_run_metadata(self, metadata: dict[str, object]) -> None:
        self.recorder.record("history.save")
        self.saved_metadata.append(dict(metadata))


class _OrderedActiveRunStore:
    def __init__(self, recorder: _OrderedCallRecorder) -> None:
        self.recorder = recorder
        self.cleared = False
        self.saved_metadata: list[dict[str, object]] = []
        self.runtime_updates: list[tuple[str, str, str | None]] = []

    def clear(self) -> None:
        self.recorder.record("active.clear")
        self.cleared = True

    def update_run_metadata(self, metadata: dict[str, object]) -> None:
        self.recorder.record("active.update_metadata")
        self.saved_metadata.append(dict(metadata))

    def update_runtime_state(
        self,
        lifecycle_state: str,
        *,
        updated_at: str,
        last_error: str | None = None,
    ) -> None:
        self.recorder.record(f"active.update_runtime:{lifecycle_state}")
        self.runtime_updates.append((lifecycle_state, updated_at, last_error))


class _OrderedJournalStore:
    def __init__(self, recorder: _OrderedCallRecorder) -> None:
        self.recorder = recorder
        self.events: list[dict[str, object]] = []
        self.summaries: list[dict[str, object]] = []

    def append_event(
        self,
        run_id: str,
        event_type: str,
        data: dict[str, object],
        *,
        occurred_at: str,
    ) -> None:
        self.recorder.record(f"journal.append:{event_type}")
        self.events.append(
            {
                "run_id": run_id,
                "type": event_type,
                "data": dict(data),
                "occurred_at": occurred_at,
            }
        )

    def save_summary(self, run_id: str, summary: dict[str, object]) -> None:
        self.recorder.record("journal.summary")
        self.summaries.append({"run_id": run_id, **summary})


class _OrderedStateMachine:
    def __init__(self, recorder: _OrderedCallRecorder) -> None:
        self.recorder = recorder
        self.runtime_state: dict[str, object] = {
            "lifecycle_state": "active_scan",
            "current_phase": "scan",
            "progress_completed": 2,
            "progress_total": 5,
            "updated_at": "2026-07-28T10:00:00+08:00",
            "last_error": None,
        }

    def capture_last_phase(self) -> None:
        self.recorder.record("state.capture")
        self.runtime_state["last_phase"] = "scan"
        self.runtime_state["last_phase_completed"] = 2
        self.runtime_state["last_phase_total"] = 5

    def transition(
        self,
        lifecycle_state: str,
        *,
        lease_duration_seconds: int | None = None,
    ) -> None:
        self.recorder.record(f"state.transition:{lifecycle_state}")
        self.runtime_state["lifecycle_state"] = lifecycle_state
        self.runtime_state["state_changed_at"] = "2026-07-28T10:00:01+08:00"
        self.runtime_state["updated_at"] = "2026-07-28T10:00:01+08:00"
        self.runtime_state["lease_expires_at"] = None


class _SessionHistoryStore:
    def __init__(self, *, fail_append: bool = False) -> None:
        self.appended: list[object] = []
        self.fail_append = fail_append

    def append(self, result: object) -> None:
        if self.fail_append:
            raise RuntimeError("injected history append failure")
        self.appended.append(result)


class ExecutionContextTests(unittest.TestCase):
    def test_rejects_invalid_progress_and_worker_values(self) -> None:
        with self.assertRaises(ValueError):
            ExecutionContext(
                run_id="run-1",
                operation_kind="scan",
                total=1,
                max_workers=0,
            )
        with self.assertRaises(ValueError):
            ExecutionContext(
                run_id="run-1",
                operation_kind="scan",
                total=1,
                max_workers=1,
                initial_completed=2,
            )


class RunStateMachineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.timestamps = iter(
            [
                "2026-07-28T10:00:00+08:00",
                "2026-07-28T10:00:01+08:00",
                "2026-07-28T10:00:02+08:00",
            ]
        )
        self.state: dict[str, object] = {
            "lifecycle_state": "idle",
            "last_phase": None,
            "last_phase_completed": 0,
            "last_phase_total": 0,
        }
        self.machine = RunStateMachine(
            self.state,
            timestamp=lambda: next(self.timestamps),
        )

    def test_begin_initializes_authoritative_runtime_and_lease(self) -> None:
        context = ExecutionContext(
            run_id="run-1",
            operation_kind="scan",
            total=5,
            max_workers=2,
            initial_completed=2,
        )
        entries = [{"candidate_id": "candidate-1"}]

        self.machine.begin(
            context,
            run_entries=entries,
            last_run_mode="mock",
            current_phase="scan",
            lease_duration_seconds=120,
        )

        self.assertTrue(self.state["is_running"])
        self.assertEqual(self.state["current_run_id"], "run-1")
        self.assertEqual(self.state["completed_targets"], 2)
        self.assertEqual(self.state["progress_completed"], 2)
        self.assertEqual(self.state["progress_total"], 5)
        self.assertIs(self.state["run_entries"], entries)
        self.assertEqual(self.state["lifecycle_state"], "active_scan")
        self.assertEqual(
            self.state["lease_expires_at"],
            "2026-07-28T10:02:00+08:00",
        )

    def test_prepare_background_run_owns_preparing_runtime_reset(self) -> None:
        self.state.update(
            {
                "is_running": False,
                "last_error": "old error",
                "last_run_count": 4,
                "completed_targets": 3,
                "total_targets": 5,
                "current_target": "old target",
                "run_entries": [{"candidate_id": "old"}],
                "current_run_id": "run-old",
            }
        )

        self.machine.prepare_background_run(lease_duration_seconds=120)

        self.assertTrue(self.state["is_running"])
        self.assertIsNone(self.state["last_error"])
        self.assertEqual(self.state["last_run_count"], 0)
        self.assertEqual(self.state["completed_targets"], 0)
        self.assertEqual(self.state["total_targets"], 0)
        self.assertIsNone(self.state["current_target"])
        self.assertEqual(self.state["run_entries"], [])
        self.assertIsNone(self.state["current_run_id"])
        self.assertEqual(self.state["lifecycle_state"], "preparing")
        self.assertEqual(
            self.state["lease_expires_at"],
            "2026-07-28T10:02:00+08:00",
        )

    def test_restores_finalizing_failure_and_committed_idle_runtime(self) -> None:
        self.machine.restore_finalizing_failure(
            {
                "state_changed_at": "2026-07-28T09:59:00+08:00",
                "finalizing_started_at": "2026-07-28T09:59:01+08:00",
                "lease_expires_at": "2026-07-28T10:05:00+08:00",
            },
            error_message="projection failed",
            updated_at="2026-07-28T10:00:00+08:00",
        )

        self.assertEqual(self.state["lifecycle_state"], "finalizing")
        self.assertEqual(
            self.state["state_changed_at"],
            "2026-07-28T09:59:00+08:00",
        )
        self.assertEqual(self.state["last_error"], "projection failed")
        self.assertEqual(
            self.state["updated_at"],
            "2026-07-28T10:00:00+08:00",
        )

        self.machine.restore_idle(changed_at="2026-07-28T10:00:01+08:00")

        self.assertFalse(self.state["is_running"])
        self.assertIsNone(self.state["last_error"])
        self.assertIsNone(self.state["current_target"])
        self.assertEqual(self.state["lifecycle_state"], "idle")
        self.assertEqual(
            self.state["state_changed_at"],
            "2026-07-28T10:00:01+08:00",
        )
        self.assertEqual(
            self.state["updated_at"],
            "2026-07-28T10:00:01+08:00",
        )
        self.assertIsNone(self.state["finalizing_started_at"])
        self.assertIsNone(self.state["lease_expires_at"])

    def test_job_counters_never_become_negative(self) -> None:
        self.state.update(
            {
                "queued_evaluation_count": 0,
                "active_evaluation_count": 0,
                "completed_targets": 0,
                "progress_completed": 0,
            }
        )

        self.machine.job_dequeued()
        self.machine.job_started()
        self.machine.job_stopped()
        self.machine.job_stopped()
        self.machine.job_committed()

        self.assertEqual(self.state["queued_evaluation_count"], 0)
        self.assertEqual(self.state["active_evaluation_count"], 0)
        self.assertEqual(self.state["completed_targets"], 1)
        self.assertEqual(self.state["progress_completed"], 1)

    def test_settle_preserves_failure_and_clears_transient_runtime(self) -> None:
        self.state.update(
            {
                "is_running": True,
                "lifecycle_state": "failed",
                "current_target": "target",
                "current_run_id": "run-1",
                "active_evaluation_count": 1,
                "queued_evaluation_count": 2,
                "oldest_active_evaluation_started_at": "now",
            }
        )

        lifecycle = self.machine.settle(result_count=3, control_action=None)

        self.assertEqual(lifecycle, "failed")
        self.assertFalse(self.state["is_running"])
        self.assertEqual(self.state["last_run_count"], 3)
        self.assertIsNone(self.state["current_target"])
        self.assertIsNone(self.state["current_run_id"])
        self.assertEqual(self.state["active_evaluation_count"], 0)
        self.assertEqual(self.state["queued_evaluation_count"], 0)

    def test_settle_maps_control_and_captures_successful_scan(self) -> None:
        self.state.update(
            {
                "is_running": True,
                "lifecycle_state": "active_scan",
                "current_phase": "scan",
                "progress_completed": 4,
                "progress_total": 5,
            }
        )

        lifecycle = self.machine.settle(result_count=4, control_action=None)

        self.assertEqual(lifecycle, "finalizing")
        self.assertEqual(self.state["last_phase"], "scan")
        self.assertEqual(self.state["last_phase_completed"], 4)
        self.assertEqual(self.state["last_phase_total"], 5)
        self.assertEqual(
            self.state["finalizing_started_at"],
            "2026-07-28T10:00:00+08:00",
        )
        self.assertEqual(
            self.state["state_changed_at"],
            "2026-07-28T10:00:01+08:00",
        )


class RunControlCoordinatorTests(unittest.TestCase):
    def test_poll_consumes_one_control_until_explicitly_cleared(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ActiveRunStore(Path(temp_dir) / "active-run.json")
            control = RunControlCoordinator(store)
            store.request_control("pause")

            self.assertEqual(control.poll(), "pause")
            self.assertEqual(control.poll(), "pause")
            self.assertIsNone(store.peek_control())

            control.clear_action()
            store.request_control("stop")
            self.assertEqual(control.poll(), "stop")

    def test_reset_discards_stale_control(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ActiveRunStore(Path(temp_dir) / "active-run.json")
            control = RunControlCoordinator(store)
            store.request_control("stop")

            control.reset()

            self.assertIsNone(control.action)
            self.assertIsNone(store.peek_control())


class ExecutionEngineTests(unittest.TestCase):
    def test_respects_concurrency_and_commits_each_started_job_once(self) -> None:
        jobs = [object() for _ in range(6)]
        lock = threading.Lock()
        active = 0
        maximum_active = 0
        finished: list[tuple[object, bool]] = []

        def run_job(job: object) -> object:
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.01)
            with lock:
                active -= 1
            return job

        engine: ExecutionEngine[object] = ExecutionEngine()
        engine.execute(  # type: ignore[arg-type]
            jobs,
            max_workers=2,
            try_start=lambda _job: True,
            run_job=run_job,
            finish_job=lambda job, _result: finished.append((job, True)),
            fail_job=lambda _job, _error: None,
        )

        self.assertEqual(maximum_active, 2)
        self.assertCountEqual([job for job, _ in finished], jobs)
        self.assertTrue(all(committed for _, committed in finished))

    def test_propagates_worker_failure_after_reporting_it(self) -> None:
        job = object()
        failures: list[tuple[object, Exception]] = []
        engine: ExecutionEngine[object] = ExecutionEngine()

        with self.assertRaisesRegex(RuntimeError, "boom"):
            engine.execute(  # type: ignore[arg-type]
                [job],
                max_workers=1,
                try_start=lambda _job: True,
                run_job=lambda _job: (_ for _ in ()).throw(RuntimeError("boom")),
                finish_job=lambda _job, _result: None,
                fail_job=lambda failed_job, error: failures.append(
                    (failed_job, error)
                ),
            )

        self.assertEqual(len(failures), 1)
        self.assertIs(failures[0][0], job)
        self.assertIsInstance(failures[0][1], RuntimeError)

    def test_stop_on_failure_does_not_start_queued_jobs(self) -> None:
        jobs = [object(), object(), object()]
        started: list[object] = []
        skipped: list[object] = []
        engine: ExecutionEngine[object] = ExecutionEngine()

        with self.assertRaisesRegex(RuntimeError, "boom"):
            engine.execute(  # type: ignore[arg-type]
                jobs,
                max_workers=1,
                try_start=lambda job: started.append(job) is None,
                run_job=lambda _job: (_ for _ in ()).throw(RuntimeError("boom")),
                finish_job=lambda _job, _result: None,
                fail_job=lambda _job, _error: None,
                stop_on_failure=True,
                skip_job=skipped.append,
            )

        self.assertEqual(started, [jobs[0]])
        self.assertEqual(skipped, jobs[1:])


class ExecutionSessionTests(unittest.TestCase):
    @staticmethod
    def build_session(
        temp_dir: str,
        *,
        total: int,
        history_store: _SessionHistoryStore | None = None,
        on_control=None,  # type: ignore[no-untyped-def]
    ) -> tuple[
        ExecutionSession[object],
        dict[str, object],
        ActiveRunStore,
        _SessionHistoryStore,
    ]:
        runtime_state: dict[str, object] = {"lifecycle_state": "idle"}
        state_machine = RunStateMachine(
            runtime_state,
            timestamp=lambda: "2026-07-28T10:00:00+08:00",
        )
        active_run_store = ActiveRunStore(Path(temp_dir) / "active-run.json")
        selected_history_store = history_store or _SessionHistoryStore()
        session = ExecutionSession[object](
            context=ExecutionContext(
                run_id="run-1",
                operation_kind="scan",
                total=total,
                max_workers=1,
            ),
            state_machine=state_machine,
            lifecycle=object(),  # type: ignore[arg-type]
            engine=ExecutionEngine(),
            history_store=selected_history_store,  # type: ignore[arg-type]
            active_run_store=active_run_store,
            on_control=on_control,
        )
        session.begin(
            run_entries=[],
            last_run_mode="mock",
            current_phase="scan",
            lease_duration_seconds=120,
        )
        return session, runtime_state, active_run_store, selected_history_store

    @staticmethod
    def build_terminal_session(
        temp_dir: str,
        *,
        operation_kind: str = "scan",
    ) -> tuple[
        ExecutionSession[object],
        dict[str, object],
        ActiveRunStore,
        RunJournalStore,
    ]:
        runtime_state: dict[str, object] = {"lifecycle_state": "idle"}
        timestamp = lambda: "2026-07-28T10:00:00+08:00"
        state_machine = RunStateMachine(runtime_state, timestamp=timestamp)
        history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
        active_run_store = ActiveRunStore(Path(temp_dir) / "active-run.json")
        journal_store = RunJournalStore(Path(temp_dir) / "runs")
        session = ExecutionSession[object](
            context=ExecutionContext(
                run_id="run-1",
                operation_kind=operation_kind,
                total=0,
                max_workers=1,
            ),
            state_machine=state_machine,
            lifecycle=RunLifecycleCoordinator(
                state_machine=state_machine,
                history_store=history_store,
                active_run_store=active_run_store,
                journal_store=journal_store,
                timestamp=timestamp,
            ),
            engine=ExecutionEngine(),
            history_store=history_store,
            active_run_store=active_run_store,
        )
        session.begin(
            run_entries=[],
            last_run_mode="mock",
            current_phase="scan" if operation_kind == "scan" else "repair",
            lease_duration_seconds=120,
        )
        active_run_store.save({"run_id": "run-1", "runtime": {}})
        return session, runtime_state, active_run_store, journal_store

    def test_owns_worker_counters_history_results_and_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session, runtime, _, history = self.build_session(temp_dir, total=2)
            persistence_snapshots: list[tuple[int, int, int]] = []

            def persist_state() -> None:
                persistence_snapshots.append(
                    (
                        int(runtime["active_evaluation_count"]),
                        int(runtime["queued_evaluation_count"]),
                        int(runtime["progress_completed"]),
                    )
                )

            session.execute_jobs(  # type: ignore[arg-type]
                ["a", "b"],
                callbacks=ExecutionJobCallbacks(
                    run_job=lambda job: f"result-{job}",
                    persist_state=persist_state,
                ),
                persist_before_execute=True,
            )

            self.assertEqual(session.results, ["result-a", "result-b"])
            self.assertEqual(history.appended, session.results)
            self.assertEqual(runtime["progress_completed"], 2)
            self.assertEqual(runtime["active_evaluation_count"], 0)
            self.assertEqual(runtime["queued_evaluation_count"], 0)
            self.assertEqual(persistence_snapshots[0], (0, 2, 0))
            self.assertEqual(persistence_snapshots[-1], (0, 0, 2))

            self.assertEqual(session.settle(), "finalizing")
            self.assertEqual(session.settle(), "finalizing")
            self.assertFalse(runtime["is_running"])
            self.assertEqual(runtime["last_run_count"], 2)

    def test_consumes_control_before_starting_more_work(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            observed_controls: list[str] = []
            session, runtime, active, history = self.build_session(
                temp_dir,
                total=1,
                on_control=observed_controls.append,
            )
            active.request_control("pause")

            session.execute_jobs(  # type: ignore[arg-type]
                ["job"],
                callbacks=ExecutionJobCallbacks(
                    run_job=lambda _job: self.fail("controlled job must not start"),
                    persist_state=lambda: None,
                ),
            )

            self.assertEqual(observed_controls, ["pause"])
            self.assertEqual(session.control_action, "pause")
            self.assertEqual(history.appended, [])
            self.assertEqual(session.results, [])
            self.assertEqual(runtime["active_evaluation_count"], 0)
            self.assertEqual(runtime["queued_evaluation_count"], 0)
            self.assertEqual(session.settle(), "paused_recoverable")

    def test_history_append_failure_never_commits_progress_or_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            history = _SessionHistoryStore(fail_append=True)
            session, runtime, _, _ = self.build_session(
                temp_dir,
                total=1,
                history_store=history,
            )

            with self.assertRaisesRegex(RuntimeError, "history append failure"):
                session.execute_jobs(  # type: ignore[arg-type]
                    ["job"],
                    callbacks=ExecutionJobCallbacks(
                        run_job=lambda _job: "result",
                        persist_state=lambda: None,
                    ),
                )

            self.assertEqual(history.appended, [])
            self.assertEqual(session.results, [])
            self.assertEqual(runtime["progress_completed"], 0)
            self.assertEqual(runtime["active_evaluation_count"], 0)

    def test_terminal_summary_observes_settled_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_state: dict[str, object] = {"lifecycle_state": "idle"}
            timestamp = lambda: "2026-07-28T10:00:00+08:00"
            state_machine = RunStateMachine(runtime_state, timestamp=timestamp)
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active-run.json")
            journal_store = RunJournalStore(Path(temp_dir) / "runs")
            lifecycle = RunLifecycleCoordinator(
                state_machine=state_machine,
                history_store=history_store,
                active_run_store=active_run_store,
                journal_store=journal_store,
                timestamp=timestamp,
            )
            session = ExecutionSession[object](
                context=ExecutionContext(
                    run_id="run-1",
                    operation_kind="repair",
                    total=0,
                    max_workers=1,
                ),
                state_machine=state_machine,
                lifecycle=lifecycle,
                engine=ExecutionEngine(),
                history_store=history_store,
                active_run_store=active_run_store,
            )
            session.begin(
                run_entries=[],
                last_run_mode="mock",
                current_phase="repair",
                lease_duration_seconds=120,
            )
            active_run_store.save({"run_id": "run-1", "runtime": {}})

            session.complete(
                run_metadata={"run_id": "run-1", "status": "completed"},
                journal_event_type="repair.completed",
                journal_data={"result_count": 0},
                clear_active_run=True,
                settle_before_persist=True,
            )

            summary = journal_store.load_summary("run-1")
            self.assertEqual(summary["status"], "completed")  # type: ignore[index]
            self.assertEqual(summary["lifecycle_state"], "finalizing")  # type: ignore[index]
            self.assertIsNone(active_run_store.load())
            self.assertEqual(
                journal_store.load_events("run-1")[-1]["type"],
                "repair.completed",
            )

    def test_finish_completed_retains_checkpoint_then_settles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session, runtime, _, journal = self.build_terminal_session(temp_dir)
            checkpoint_states: list[dict[str, object]] = []

            lifecycle = session.finish_completed(
                run_metadata={"run_id": "run-1", "status": "completed"},
                journal_event_type="run.completed",
                journal_data={"result_count": 0},
                clear_active_run=False,
                capture_before_clear=True,
                retained_lifecycle="finalizing",
                persist_retained_checkpoint=lambda: checkpoint_states.append(
                    dict(runtime)
                ),
            )

            self.assertEqual(lifecycle, "finalizing")
            self.assertEqual(len(checkpoint_states), 1)
            self.assertEqual(
                checkpoint_states[0]["lifecycle_state"],
                "finalizing",
            )
            self.assertFalse(checkpoint_states[0]["is_running"])
            self.assertEqual(checkpoint_states[0]["current_run_id"], "run-1")
            self.assertIsNone(runtime["current_run_id"])
            self.assertEqual(journal.load_events("run-1")[-1]["type"], "run.completed")

    def test_finish_completed_can_retain_failed_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session, runtime, _, _ = self.build_terminal_session(temp_dir)
            checkpoint_states: list[dict[str, object]] = []

            lifecycle = session.finish_completed(
                run_metadata={"run_id": "run-1", "status": "failed"},
                journal_event_type="run.failed",
                journal_data={"result_count": 0},
                clear_active_run=False,
                retained_lifecycle="failed",
                persist_retained_checkpoint=lambda: checkpoint_states.append(
                    dict(runtime)
                ),
            )

            self.assertEqual(lifecycle, "failed")
            self.assertEqual(checkpoint_states[0]["lifecycle_state"], "failed")
            self.assertIsNone(checkpoint_states[0]["finalizing_started_at"])
            self.assertEqual(runtime["lifecycle_state"], "failed")

    def test_finish_failed_retains_failure_checkpoint_and_settles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session, runtime, active, _ = self.build_terminal_session(temp_dir)
            failed_metadata = {"run_id": "run-1", "status": "partial"}

            lifecycle = session.finish_failed(
                run_metadata=failed_metadata,
                error_message="boom",
                journal_event_type="run.failed",
                journal_data={"error_message": "boom"},
                clear_active_run=False,
                retain_active_checkpoint=True,
            )

            self.assertEqual(lifecycle, "failed")
            checkpoint = active.load()
            self.assertEqual(checkpoint["run_metadata"], failed_metadata)  # type: ignore[index]
            self.assertEqual(checkpoint["runtime"]["lifecycle_state"], "failed")  # type: ignore[index]
            self.assertEqual(checkpoint["runtime"]["last_error"], "boom")  # type: ignore[index]
            self.assertFalse(runtime["is_running"])
            self.assertIsNone(runtime["current_run_id"])

    def test_finish_controlled_can_defer_settle_until_workers_drain(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session, runtime, active, _ = self.build_terminal_session(temp_dir)
            active.request_control("pause")
            self.assertEqual(session.poll_control(), "pause")

            lifecycle = session.finish_controlled(
                run_metadata={"run_id": "run-1", "status": "running"},
                journal_event_type="run.paused",
                journal_data={"progress_completed": 0},
                persist_controlled_metadata=True,
                transition_before_persist=False,
                settle_after=False,
            )

            self.assertEqual(lifecycle, "paused_recoverable")
            self.assertTrue(runtime["is_running"])
            self.assertEqual(session.settle(), "paused_recoverable")
            self.assertFalse(runtime["is_running"])


class RunLifecycleCoordinatorTests(unittest.TestCase):
    def build_coordinator(
        self,
        temp_dir: str,
    ) -> tuple[
        RunLifecycleCoordinator,
        RunStateMachine,
        HistoryStore,
        ActiveRunStore,
        RunJournalStore,
    ]:
        runtime_state: dict[str, object] = {
            "lifecycle_state": "active_scan",
            "current_phase": "repair",
            "progress_completed": 1,
            "progress_total": 1,
            "updated_at": "2026-07-28T10:00:00+08:00",
            "last_error": None,
        }
        timestamp = lambda: "2026-07-28T10:00:01+08:00"
        state_machine = RunStateMachine(runtime_state, timestamp=timestamp)
        history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
        active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
        journal_store = RunJournalStore(Path(temp_dir) / "runs")
        coordinator = RunLifecycleCoordinator(
            state_machine=state_machine,
            history_store=history_store,
            active_run_store=active_run_store,
            journal_store=journal_store,
            timestamp=timestamp,
        )
        return (
            coordinator,
            state_machine,
            history_store,
            active_run_store,
            journal_store,
        )

    def test_complete_commits_history_journal_summary_and_active_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator, _, history, active, journal = self.build_coordinator(temp_dir)
            active.save({"run_id": "run-1", "runtime": {}})
            metadata = {"run_id": "run-1", "status": "completed"}

            coordinator.complete(
                run_id="run-1",
                run_metadata=metadata,
                journal_event_type="repair.completed",
                journal_data={"result_count": 1},
                clear_active_run=True,
            )

            self.assertEqual(history.load_run_metadata("run-1"), metadata)
            self.assertIsNone(active.load())
            self.assertEqual(journal.load_events("run-1")[-1]["type"], "repair.completed")
            self.assertEqual(journal.load_summary("run-1")["status"], "completed")  # type: ignore[index]

    def test_fail_sets_lifecycle_and_records_authoritative_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator, machine, history, active, journal = self.build_coordinator(temp_dir)
            active.save({"run_id": "run-1", "runtime": {}})
            metadata = {"run_id": "run-1", "status": "degraded"}

            coordinator.fail(
                run_id="run-1",
                run_metadata=metadata,
                error_message="boom",
                journal_event_type="repair.failed",
                journal_data={"error_message": "boom"},
                clear_active_run=True,
            )

            self.assertEqual(history.load_run_metadata("run-1"), metadata)
            self.assertIsNone(active.load())
            self.assertEqual(machine.runtime_state["lifecycle_state"], "failed")
            self.assertEqual(machine.runtime_state["last_error"], "boom")
            self.assertEqual(journal.load_events("run-1")[-1]["type"], "repair.failed")

    def test_terminal_persistence_can_preserve_a_legacy_metadata_only_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator, _, history, active, journal = self.build_coordinator(temp_dir)
            active.save({"run_id": "run-1", "runtime": {}})
            metadata = {"run_id": "run-1", "status": "completed"}

            coordinator.complete(
                run_id="run-1",
                run_metadata=metadata,
                journal_event_type=None,
                journal_data={},
                clear_active_run=True,
                persist_journal_summary=False,
            )

            self.assertEqual(history.load_run_metadata("run-1"), metadata)
            self.assertIsNone(active.load())
            self.assertEqual(journal.load_events("run-1"), [])
            self.assertIsNone(journal.load_summary("run-1"))

    def test_control_keeps_pause_checkpoint_and_clears_stopped_run(self) -> None:
        for action, expected_lifecycle in (
            ("pause", "paused_recoverable"),
            ("stop", "stopped"),
        ):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as temp_dir:
                coordinator, machine, _, active, journal = self.build_coordinator(temp_dir)
                active.save({"run_id": "run-1", "runtime": {}})

                coordinator.control(
                    action=action,
                    run_id="run-1",
                    run_metadata={"run_id": "run-1", "status": "degraded"},
                    journal_event_type=f"repair.{action}d",
                    journal_data={},
                )

                self.assertEqual(
                    machine.runtime_state["lifecycle_state"],
                    expected_lifecycle,
                )
                if action == "pause":
                    self.assertEqual(
                        active.load()["runtime"]["lifecycle_state"],  # type: ignore[index]
                        "paused_recoverable",
                    )
                else:
                    self.assertIsNone(active.load())
                self.assertEqual(
                    journal.load_events("run-1")[-1]["type"],
                    f"repair.{action}d",
                )


class RunLifecycleCoordinatorScanOrderingTests(unittest.TestCase):
    def build_ordered_coordinator(
        self,
        *,
        fail_on: str | None = None,
    ) -> tuple[
        RunLifecycleCoordinator,
        _OrderedCallRecorder,
        _OrderedStateMachine,
        _OrderedHistoryStore,
        _OrderedActiveRunStore,
        _OrderedJournalStore,
    ]:
        recorder = _OrderedCallRecorder(fail_on=fail_on)
        state_machine = _OrderedStateMachine(recorder)
        history_store = _OrderedHistoryStore(recorder)
        active_run_store = _OrderedActiveRunStore(recorder)
        journal_store = _OrderedJournalStore(recorder)
        coordinator = RunLifecycleCoordinator(
            state_machine=state_machine,  # type: ignore[arg-type]
            history_store=history_store,  # type: ignore[arg-type]
            active_run_store=active_run_store,  # type: ignore[arg-type]
            journal_store=journal_store,  # type: ignore[arg-type]
            timestamp=lambda: "2026-07-28T10:00:01+08:00",
        )
        return (
            coordinator,
            recorder,
            state_machine,
            history_store,
            active_run_store,
            journal_store,
        )

    def test_scan_complete_captures_phase_before_clearing_active_run(self) -> None:
        coordinator, recorder, state, _, active, _ = (
            self.build_ordered_coordinator()
        )

        coordinator.complete(
            run_id="run-1",
            run_metadata={"run_id": "run-1", "status": "completed"},
            journal_event_type="run.completed",
            journal_data={"result_count": 2},
            clear_active_run=True,
            capture_before_clear=True,
        )

        self.assertEqual(
            recorder.calls,
            [
                "history.save",
                "journal.append:run.completed",
                "journal.summary",
                "state.capture",
                "active.clear",
            ],
        )
        self.assertEqual(state.runtime_state["last_phase_completed"], 2)
        self.assertTrue(active.cleared)

    def test_scan_complete_summary_failure_preserves_active_checkpoint(self) -> None:
        coordinator, recorder, state, _, active, _ = (
            self.build_ordered_coordinator(fail_on="journal.summary")
        )

        with self.assertRaisesRegex(RuntimeError, "journal.summary"):
            coordinator.complete(
                run_id="run-1",
                run_metadata={"run_id": "run-1", "status": "completed"},
                journal_event_type="run.completed",
                journal_data={"result_count": 2},
                clear_active_run=True,
                capture_before_clear=True,
            )

        self.assertEqual(
            recorder.calls,
            [
                "history.save",
                "journal.append:run.completed",
                "journal.summary",
            ],
        )
        self.assertNotIn("last_phase", state.runtime_state)
        self.assertFalse(active.cleared)

    def test_scan_fail_transitions_before_terminal_journal_and_keeps_checkpoint(self) -> None:
        coordinator, recorder, state, history, active, journal = (
            self.build_ordered_coordinator()
        )

        coordinator.fail(
            run_id="run-1",
            run_metadata={"run_id": "run-1", "status": "partial"},
            error_message="boom",
            journal_event_type="run.failed",
            journal_data={"error_message": "boom"},
            clear_active_run=False,
        )

        self.assertEqual(
            recorder.calls,
            [
                "history.save",
                "state.transition:failed",
                "journal.append:run.failed",
                "journal.summary",
            ],
        )
        self.assertEqual(history.saved_metadata[0]["status"], "partial")
        self.assertEqual(state.runtime_state["last_error"], "boom")
        self.assertEqual(journal.summaries[0]["lifecycle_state"], "failed")
        self.assertFalse(active.cleared)

    def test_scan_fail_can_commit_recoverable_failure_checkpoint(self) -> None:
        coordinator, recorder, state, history, active, journal = (
            self.build_ordered_coordinator()
        )
        metadata = {"run_id": "run-1", "status": "partial"}

        coordinator.fail(
            run_id="run-1",
            run_metadata=metadata,
            error_message="boom",
            journal_event_type="run.failed",
            journal_data={"error_message": "boom"},
            clear_active_run=False,
            retain_active_checkpoint=True,
        )

        self.assertEqual(
            recorder.calls,
            [
                "history.save",
                "state.transition:failed",
                "journal.append:run.failed",
                "journal.summary",
                "active.update_metadata",
                "active.update_runtime:failed",
            ],
        )
        self.assertEqual(history.saved_metadata[0], metadata)
        self.assertEqual(journal.summaries[0]["lifecycle_state"], "failed")
        self.assertEqual(active.saved_metadata[0], metadata)
        self.assertEqual(active.runtime_updates[0], ("failed", state.runtime_state["updated_at"], "boom"))

    def test_scan_fail_journal_failure_leaves_failed_checkpoint_recoverable(self) -> None:
        coordinator, recorder, state, _, active, journal = (
            self.build_ordered_coordinator(fail_on="journal.append:run.failed")
        )

        with self.assertRaisesRegex(RuntimeError, "journal.append:run.failed"):
            coordinator.fail(
                run_id="run-1",
                run_metadata={"run_id": "run-1", "status": "failed"},
                error_message="boom",
                journal_event_type="run.failed",
                journal_data={"error_message": "boom"},
                clear_active_run=False,
            )

        self.assertEqual(
            recorder.calls,
            [
                "history.save",
                "state.transition:failed",
                "journal.append:run.failed",
            ],
        )
        self.assertEqual(state.runtime_state["lifecycle_state"], "failed")
        self.assertEqual(journal.summaries, [])
        self.assertFalse(active.cleared)

    def test_scan_pause_persists_summary_before_runtime_transition(self) -> None:
        coordinator, recorder, state, history, active, journal = (
            self.build_ordered_coordinator()
        )

        coordinator.control(
            action="pause",
            run_id="run-1",
            run_metadata={"run_id": "run-1", "status": "running"},
            journal_event_type="run.paused",
            journal_data={"progress_completed": 2},
            persist_controlled_metadata=True,
            transition_before_persist=False,
        )

        self.assertEqual(
            recorder.calls,
            [
                "history.save",
                "journal.append:run.paused",
                "journal.summary",
                "state.capture",
                "state.transition:paused_recoverable",
                "active.update_metadata",
                "active.update_runtime:paused_recoverable",
            ],
        )
        self.assertEqual(history.saved_metadata[0]["status"], "paused")
        self.assertEqual(journal.summaries[0]["lifecycle_state"], "active_scan")
        self.assertEqual(state.runtime_state["lifecycle_state"], "paused_recoverable")
        self.assertEqual(active.saved_metadata[0]["status"], "paused")

    def test_scan_pause_summary_failure_prevents_runtime_transition(self) -> None:
        coordinator, recorder, state, history, active, _ = (
            self.build_ordered_coordinator(fail_on="journal.summary")
        )

        with self.assertRaisesRegex(RuntimeError, "journal.summary"):
            coordinator.control(
                action="pause",
                run_id="run-1",
                run_metadata={"run_id": "run-1", "status": "running"},
                journal_event_type="run.paused",
                journal_data={"progress_completed": 2},
                persist_controlled_metadata=True,
                transition_before_persist=False,
            )

        self.assertEqual(
            recorder.calls,
            [
                "history.save",
                "journal.append:run.paused",
                "journal.summary",
            ],
        )
        self.assertEqual(history.saved_metadata[0]["status"], "paused")
        self.assertEqual(state.runtime_state["lifecycle_state"], "active_scan")
        self.assertEqual(active.saved_metadata, [])
        self.assertEqual(active.runtime_updates, [])


if __name__ == "__main__":
    unittest.main()
