from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar

from .execution import ExecutionSession


ResultT = TypeVar("ResultT")


class ScanOutcomeCoordinator(Generic[ResultT]):
    """Apply scan terminal policy before delegating durable lifecycle changes."""

    def __init__(
        self,
        *,
        session: ExecutionSession[ResultT],
        base_run_metadata: dict[str, object],
        evaluation_result_level: str,
        selection_mode: str,
        effective_candidate_ids: list[str],
        regular_candidate_ids: list[str],
        timestamp: Callable[[], str],
    ) -> None:
        self.session = session
        self.base_run_metadata = dict(base_run_metadata)
        self.evaluation_result_level = evaluation_result_level
        self.selection_mode = selection_mode
        self.effective_candidate_ids = list(effective_candidate_ids)
        self.regular_candidate_ids = list(regular_candidate_ids)
        self.timestamp = timestamp

    def finish_controlled(
        self,
        *,
        action: str,
        progress_completed: int,
        progress_total: int,
    ) -> dict[str, object]:
        metadata = dict(self.base_run_metadata)
        self.session.finish_controlled(
            run_metadata=metadata,
            journal_event_type=(
                "run.paused" if action == "pause" else "run.stopped"
            ),
            journal_data={
                "progress_completed": progress_completed,
                "progress_total": progress_total,
            },
            persist_controlled_metadata=True,
            transition_before_persist=False,
            settle_after=False,
        )
        return metadata

    def finish_completed(
        self,
        *,
        circuit_open: bool,
        hard_error_count: int,
        result_count: int,
        progress_completed: int,
        progress_total: int,
        retain_finalizing_state: bool,
        persist_retained_checkpoint: (
            Callable[[dict[str, object]], None] | None
        ) = None,
        on_retained: Callable[[str], None] | None = None,
    ) -> dict[str, object]:
        metadata = self._completed_metadata(
            circuit_open=circuit_open,
            hard_error_count=hard_error_count,
        )
        checkpoint = None
        if retain_finalizing_state and persist_retained_checkpoint is not None:
            checkpoint = lambda: persist_retained_checkpoint(metadata)
        self.session.finish_completed(
            run_metadata=metadata,
            journal_event_type=(
                "run.failed" if metadata["status"] == "failed" else "run.completed"
            ),
            journal_data={
                "status": metadata["status"],
                "result_count": result_count,
                "progress_completed": progress_completed,
                "progress_total": progress_total,
            },
            clear_active_run=not retain_finalizing_state,
            capture_before_clear=True,
            retained_lifecycle=(
                "failed" if metadata["status"] == "failed" else "finalizing"
            ),
            persist_retained_checkpoint=checkpoint,
            on_retained=on_retained if retain_finalizing_state else None,
        )
        return metadata

    def finish_exception(
        self,
        *,
        error_message: str,
        result_count: int,
    ) -> dict[str, object]:
        metadata = dict(self.base_run_metadata)
        metadata["status"] = "failed" if result_count == 0 else "partial"
        metadata["completed_at"] = self.timestamp()
        self.session.finish_failed(
            run_metadata=metadata,
            error_message=error_message,
            journal_event_type="run.failed",
            journal_data={
                "error_message": error_message,
                "result_count": result_count,
            },
            clear_active_run=False,
            retain_active_checkpoint=True,
        )
        return metadata

    def _completed_metadata(
        self,
        *,
        circuit_open: bool,
        hard_error_count: int,
    ) -> dict[str, object]:
        metadata = dict(self.base_run_metadata)
        if circuit_open:
            status = "failed"
        elif hard_error_count > 0:
            status = "degraded"
        else:
            status = "completed"
        metadata["status"] = status
        metadata["is_complete_regular_round"] = (
            status == "completed"
            and self.evaluation_result_level == "complete"
            and self.selection_mode in {"regular", "incremental_full"}
            and self.effective_candidate_ids == self.regular_candidate_ids
        )
        metadata["completed_at"] = self.timestamp()
        return metadata
