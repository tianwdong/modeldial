from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar

from .execution import ExecutionSession


ResultT = TypeVar("ResultT")


class RepairOutcomeCoordinator(Generic[ResultT]):
    """Normalize repair outcomes before delegating durable lifecycle changes."""

    def __init__(
        self,
        *,
        session: ExecutionSession[ResultT],
        base_run_metadata: dict[str, object],
        persist_run_id: str,
        comparison_group_id: str,
        event_prefix: str,
        journal_scope: dict[str, object],
        metadata_factory: Callable[[list[ResultT]], dict[str, object]],
        timestamp: Callable[[], str],
    ) -> None:
        self.session = session
        self.base_run_metadata = dict(base_run_metadata)
        self.persist_run_id = persist_run_id
        self.comparison_group_id = comparison_group_id
        self.event_prefix = event_prefix
        self.journal_scope = dict(journal_scope)
        self.metadata_factory = metadata_factory
        self.timestamp = timestamp

    def finish_controlled(
        self,
        *,
        history: list[ResultT],
        result_count: int,
    ) -> dict[str, object]:
        metadata = self._normalized_metadata(history)
        action = self.session.control_action
        event_suffix = "paused" if action == "pause" else "stopped"
        self.session.finish_controlled(
            run_metadata=metadata,
            journal_event_type=f"{self.event_prefix}.{event_suffix}",
            journal_data=self._journal_data(result_count=result_count),
        )
        return metadata

    def finish_completed(
        self,
        *,
        history: list[ResultT],
        result_count: int,
        retain_finalizing_state: bool,
        persist_retained_checkpoint: (
            Callable[[dict[str, object]], None] | None
        ) = None,
        completion_only: bool = False,
    ) -> dict[str, object]:
        metadata = self._normalized_metadata(history)
        checkpoint = None
        if retain_finalizing_state and persist_retained_checkpoint is not None:
            checkpoint = lambda: persist_retained_checkpoint(metadata)
        common_arguments = {
            "run_metadata": metadata,
            "journal_event_type": f"{self.event_prefix}.completed",
            "journal_data": self._journal_data(result_count=result_count),
            "clear_active_run": not retain_finalizing_state,
        }
        if completion_only:
            self.session.finish_completed(
                **common_arguments,
                settle_before_checkpoint=retain_finalizing_state,
                persist_retained_checkpoint=checkpoint,
                settle_after=retain_finalizing_state,
            )
        else:
            self.session.finish_completed(
                **common_arguments,
                settle_before_persist=True,
                persist_retained_checkpoint=checkpoint,
            )
        return metadata

    def finish_failed(
        self,
        *,
        error_message: str,
        result_count: int,
    ) -> dict[str, object]:
        metadata = dict(self.base_run_metadata)
        metadata.update(
            {
                "run_id": self.persist_run_id,
                "status": "degraded",
                "is_complete_regular_round": False,
                "completed_at": self.timestamp(),
                "comparison_group_id": self.comparison_group_id,
            }
        )
        self.session.finish_failed(
            run_metadata=metadata,
            error_message=error_message,
            journal_event_type=f"{self.event_prefix}.failed",
            journal_data=self._journal_data(
                result_count=result_count,
                error_message=error_message,
            ),
            clear_active_run=True,
        )
        return metadata

    def _normalized_metadata(
        self,
        history: list[ResultT],
    ) -> dict[str, object]:
        metadata = dict(self.metadata_factory(history))
        metadata["run_id"] = self.persist_run_id
        metadata["comparison_group_id"] = self.comparison_group_id
        return metadata

    def _journal_data(
        self,
        *,
        result_count: int,
        error_message: str | None = None,
    ) -> dict[str, object]:
        data = dict(self.journal_scope)
        if error_message is not None:
            data["error_message"] = error_message
        data["result_count"] = result_count
        return data
