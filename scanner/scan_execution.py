from __future__ import annotations

from collections.abc import Callable
from typing import Any, Generic, TypeVar

from .active_run_store import ActiveRunStore
from .execution import (
    ExecutionContext,
    ExecutionEngine,
    ExecutionJob,
    ExecutionJobCallbacks,
    ExecutionSession,
    RunLifecycleCoordinator,
    RunStateMachine,
    create_execution_session,
)
from .history_store import HistoryStore
from .legacy_scan_compat import SCAN_PHASE


ResultT = TypeVar("ResultT")


class ScanExecutionCommand(Generic[ResultT]):
    """Own the scan-specific session and callback wiring skeleton."""

    def __init__(
        self,
        *,
        run_id: str,
        total: int,
        max_workers: int,
        initial_completed: int,
        circuit_breaker_threshold: int,
        state_machine: RunStateMachine,
        lifecycle: RunLifecycleCoordinator,
        engine: ExecutionEngine[ResultT],
        history_store: HistoryStore,
        active_run_store: ActiveRunStore,
        on_control: Callable[[str], None] | None,
    ) -> None:
        context = ExecutionContext(
            run_id=run_id,
            operation_kind="scan",
            total=total,
            max_workers=max_workers,
            initial_completed=initial_completed,
            circuit_breaker_threshold=circuit_breaker_threshold,
        )
        self.session: ExecutionSession[ResultT] = create_execution_session(
            context=context,
            state_machine=state_machine,
            lifecycle=lifecycle,
            engine=engine,
            history_store=history_store,
            active_run_store=active_run_store,
            on_control=on_control,
        )

    @property
    def results(self) -> list[ResultT]:
        return self.session.results

    @property
    def control_action(self) -> str | None:
        return self.session.control_action

    @property
    def lock(self) -> Any:
        return self.session.lock

    def begin(
        self,
        *,
        run_entries: list[dict[str, object]],
        last_run_mode: str,
        lease_duration_seconds: int,
    ) -> None:
        self.session.begin(
            run_entries=run_entries,
            last_run_mode=last_run_mode,
            current_phase=SCAN_PHASE,
            lease_duration_seconds=lease_duration_seconds,
        )

    def execute_jobs(
        self,
        jobs: list[ExecutionJob],
        *,
        run_job: Callable[[ExecutionJob], ResultT],
        persist_state: Callable[[], None],
        can_start: Callable[[ExecutionJob], bool],
        on_not_started: Callable[[ExecutionJob], None],
        on_started: Callable[[ExecutionJob], None],
        after_started: Callable[[ExecutionJob], None],
        on_stopped: Callable[[ExecutionJob], None],
        on_failed: Callable[[ExecutionJob, Exception], None],
        on_discarded: Callable[[ExecutionJob, ResultT], None],
        on_finished: Callable[[ExecutionJob, ResultT], None],
        after_finished: Callable[[ExecutionJob, ResultT], None],
        on_skipped: Callable[[ExecutionJob], None],
        discard_result: Callable[[ResultT, str | None], bool],
    ) -> None:
        self.session.execute_jobs(
            jobs,
            callbacks=ExecutionJobCallbacks(
                run_job=run_job,
                persist_state=persist_state,
                can_start=can_start,
                on_not_started=on_not_started,
                on_started=on_started,
                after_started=after_started,
                on_stopped=on_stopped,
                on_failed=on_failed,
                on_discarded=on_discarded,
                on_finished=on_finished,
                after_finished=after_finished,
                on_skipped=on_skipped,
                discard_result=discard_result,
            ),
            stop_on_failure=True,
            persist_before_execute=True,
        )

    def clear_control_action(self) -> None:
        self.session.clear_control_action()

    def settle(self) -> str:
        return self.session.settle(control_action=None)
