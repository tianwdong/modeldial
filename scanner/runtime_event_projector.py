from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta


RuntimeStateBuilder = Callable[..., dict[str, object]]
SnapshotStateBuilder = Callable[[], dict[str, object]]


def project_terminal_failure_event(
    build_snapshot: SnapshotStateBuilder,
    *,
    event_type: str,
    failure_category: str,
    failure_message: str,
    fields: dict[str, object] | None = None,
    prepare_failure_state: Callable[[], dict[str, object]] | None = None,
    preparation_error_field: str = "failure_state_recording_error",
) -> dict[str, object]:
    """Project a business-terminal failure with one authoritative snapshot.

    Snapshot construction errors deliberately propagate.  Callers must surface
    those as bridge/transport failures instead of emitting a state-less business
    terminal event.
    """
    prepared_state: dict[str, object] | None = None
    preparation_error: str | None = None
    if prepare_failure_state is not None:
        try:
            prepared_state = prepare_failure_state()
        except Exception as exc:
            preparation_error = str(exc)

    state = build_snapshot()
    event: dict[str, object] = {
        "type": event_type,
        "failure_category": failure_category,
        "failure_message": failure_message,
        **dict(fields or {}),
        "state": state,
    }
    runtime = state.get("runtime")
    if isinstance(runtime, dict):
        event["updated_at"] = runtime.get("updated_at")
    if preparation_error is not None:
        event[preparation_error_field] = preparation_error
    if isinstance(prepared_state, dict):
        persistence_errors = prepared_state.get("persistence_errors")
        if isinstance(persistence_errors, list) and persistence_errors:
            event["persistence_errors"] = list(persistence_errors)
    return event


def project_finalizing_runtime_event(
    build_runtime_event: RuntimeStateBuilder,
    event: dict[str, object],
) -> dict[str, object]:
    if "state" in event:
        return event
    try:
        event["state"] = build_runtime_event()
    except Exception as exc:
        event["runtime_state_error"] = str(exc)
    return event


def project_started_runtime_state(
    build_runtime_event: RuntimeStateBuilder,
    *,
    run_id: str,
    phase: str,
    completed_targets: int,
    total_targets: int,
    scan_lock_stale_seconds: int,
) -> dict[str, object]:
    state = build_runtime_event()
    runtime = state.get("runtime")
    if not isinstance(runtime, dict):
        raise ValueError("runtime event projection is missing runtime")

    completed = max(0, int(completed_targets))
    total = max(completed, int(total_targets))
    progress_percent = round(completed * 100 / total) if total else 0
    now = datetime.now().astimezone()
    execution_timeout_seconds = max(
        60,
        int(runtime.get("execution_timeout_seconds") or 60),
    )
    lease_duration_seconds = max(
        scan_lock_stale_seconds,
        execution_timeout_seconds + 120,
    )
    timestamp = now.isoformat(timespec="seconds")
    lease_expires_at = (now + timedelta(seconds=lease_duration_seconds)).isoformat(
        timespec="seconds"
    )
    return {
        "schema_version": 1,
        "runtime": {
            **runtime,
            "is_running": True,
            "last_error": None,
            "completed_targets": completed,
            "total_targets": total,
            "progress_percent": progress_percent,
            "current_target": None,
            "run_entries": [],
            "current_run_id": run_id,
            "has_resumable_run": False,
            "resumable_run_id": None,
            "resumable_operation_kind": None,
            "resumable_operation_run_id": None,
            "resumable_candidate_ids": [],
            "resumable_question_id": None,
            "current_phase": phase,
            "current_phase_completed_targets": completed,
            "current_phase_total_targets": total,
            "progress_completed": completed,
            "progress_total": total,
            "active_evaluation_count": 0,
            "queued_evaluation_count": 0,
            "oldest_active_evaluation_started_at": None,
            "execution_timeout_seconds": execution_timeout_seconds,
            "lifecycle_state": "active_scan",
            "state_changed_at": timestamp,
            "finalizing_started_at": None,
            "updated_at": timestamp,
            "lease_expires_at": lease_expires_at,
        },
    }
