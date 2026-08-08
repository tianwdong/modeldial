from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar

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


ResultT = TypeVar("ResultT")


class RepairExecutionCommand(Generic[ResultT]):
    """Own the shared repair session and callback wiring skeleton."""

    _CANDIDATE_OPERATION = "candidate_repair"
    _SUPPORTED_OPERATIONS = {
        _CANDIDATE_OPERATION,
        "failed_repair",
        "timeout_repair",
    }

    def __init__(
        self,
        *,
        run_id: str,
        operation_kind: str,
        total: int,
        max_workers: int,
        state_machine: RunStateMachine,
        lifecycle: RunLifecycleCoordinator,
        engine: ExecutionEngine[ResultT],
        history_store: HistoryStore,
        active_run_store: ActiveRunStore,
        on_control: Callable[[str], None] | None,
    ) -> None:
        if operation_kind not in self._SUPPORTED_OPERATIONS:
            raise ValueError(f"unsupported repair operation: {operation_kind}")
        self.operation_kind = operation_kind
        context = ExecutionContext(
            run_id=run_id,
            operation_kind=operation_kind,
            total=total,
            max_workers=max_workers,
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
            current_phase="repair",
            lease_duration_seconds=lease_duration_seconds,
        )

    def execute_jobs(
        self,
        jobs: list[ExecutionJob],
        *,
        run_job: Callable[[ExecutionJob], ResultT],
        persist_state: Callable[[], None],
        on_started: Callable[[ExecutionJob], None],
        after_started: Callable[[ExecutionJob], None],
        on_finished: Callable[[ExecutionJob, ResultT], None],
        after_finished: Callable[[ExecutionJob, ResultT], None],
        discard_result: Callable[[ResultT, str | None], bool],
        on_stopped: Callable[[ExecutionJob], None] | None = None,
        on_failed: Callable[[ExecutionJob, Exception], None] | None = None,
        on_discarded: Callable[[ExecutionJob, ResultT], None] | None = None,
    ) -> None:
        is_candidate = self.operation_kind == self._CANDIDATE_OPERATION
        self.session.execute_jobs(
            jobs,
            callbacks=ExecutionJobCallbacks(
                run_job=run_job,
                persist_state=persist_state,
                on_started=on_started,
                after_started=after_started,
                on_stopped=on_stopped,
                on_failed=on_failed,
                on_discarded=on_discarded,
                on_finished=on_finished,
                after_finished=after_finished,
                discard_result=discard_result,
                persist_on_start=not is_candidate,
                persist_on_failure=not is_candidate,
                persist_on_skip=not is_candidate,
            ),
            stop_on_failure=is_candidate,
            persist_before_execute=not is_candidate,
        )

    def clear_control_action(self) -> None:
        self.session.clear_control_action()

    def settle(self) -> str:
        return self.session.settle()
