from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path
import threading
from typing import Callable, Generic, TypeVar

from .active_run_store import ActiveRunStore
from .history_store import HistoryStore
from .legacy_scan_compat import (
    ACTIVE_SCAN_LIFECYCLE,
    SCAN_PHASE,
    normalize_lifecycle,
    normalize_phase,
)
from .models import ResolvedScanTarget, ScanResult
from .question_bank import QuestionSpec
from .run_journal import RunJournalStore


@dataclass(frozen=True)
class ExecutionJob:
    target: ResolvedScanTarget
    question: QuestionSpec
    attempt_index: int
    result_phase: str = SCAN_PHASE

    @property
    def candidate_id(self) -> str:
        return self.target.candidate_id

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.candidate_id, self.result_phase, self.question.id)


@dataclass(frozen=True)
class ExecutionContext:
    run_id: str
    operation_kind: str
    total: int
    max_workers: int
    initial_completed: int = 0
    circuit_breaker_threshold: int | None = None

    def __post_init__(self) -> None:
        if not self.run_id:
            raise ValueError("run_id is required")
        if not self.operation_kind:
            raise ValueError("operation_kind is required")
        if self.total < 0:
            raise ValueError("total must be nonnegative")
        if self.initial_completed < 0 or self.initial_completed > self.total:
            raise ValueError("initial_completed must be within total")
        if self.max_workers < 1:
            raise ValueError("max_workers must be positive")
        if (
            self.circuit_breaker_threshold is not None
            and self.circuit_breaker_threshold < 1
        ):
            raise ValueError("circuit_breaker_threshold must be positive")


class RunStateMachine:
    _TERMINAL_LIFECYCLES = {
        "failed",
        "paused_recoverable",
        "stopped",
        "finalizing",
    }

    def __init__(
        self,
        runtime_state: dict[str, object],
        *,
        timestamp: Callable[[], str],
    ) -> None:
        self.runtime_state = runtime_state
        self._timestamp = timestamp

    def begin(
        self,
        context: ExecutionContext,
        *,
        run_entries: list[dict[str, object]],
        last_run_mode: str,
        current_phase: str,
        lease_duration_seconds: int,
    ) -> None:
        self.runtime_state.update(
            {
                "is_running": True,
                "last_error": None,
                "last_run_mode": last_run_mode,
                "completed_targets": context.initial_completed,
                "total_targets": context.total,
                "current_target": None,
                "current_run_id": context.run_id,
                "current_phase": current_phase,
                "progress_completed": context.initial_completed,
                "progress_total": context.total,
                "active_evaluation_count": 0,
                "queued_evaluation_count": 0,
                "oldest_active_evaluation_started_at": None,
                "run_entries": run_entries,
            }
        )
        self.transition(
            ACTIVE_SCAN_LIFECYCLE,
            lease_duration_seconds=lease_duration_seconds,
        )

    def prepare_background_run(self, *, lease_duration_seconds: int) -> None:
        self.runtime_state.update(
            {
                "is_running": True,
                "last_error": None,
                "last_run_count": 0,
                "completed_targets": 0,
                "total_targets": 0,
                "current_target": None,
                "run_entries": [],
                "current_run_id": None,
            }
        )
        self.transition(
            "preparing",
            lease_duration_seconds=lease_duration_seconds,
        )

    def transition(
        self,
        lifecycle_state: str,
        *,
        lease_duration_seconds: int | None = None,
    ) -> None:
        changed_at = self._timestamp()
        self.runtime_state["lifecycle_state"] = lifecycle_state
        self.runtime_state["state_changed_at"] = changed_at
        self.runtime_state["updated_at"] = changed_at
        if lease_duration_seconds is None:
            self.runtime_state["lease_expires_at"] = None
            return
        self.runtime_state["lease_expires_at"] = (
            datetime.fromisoformat(changed_at)
            + timedelta(seconds=lease_duration_seconds)
        ).isoformat(timespec="seconds")

    def capture_last_phase(self) -> None:
        phase = normalize_phase(self.runtime_state.get("current_phase"))
        if phase != SCAN_PHASE:
            return
        self.runtime_state["last_phase"] = phase
        self.runtime_state["last_phase_completed"] = int(
            self.runtime_state.get("progress_completed") or 0
        )
        self.runtime_state["last_phase_total"] = int(
            self.runtime_state.get("progress_total") or 0
        )

    def job_dequeued(self) -> None:
        self.runtime_state["queued_evaluation_count"] = max(
            0,
            int(self.runtime_state.get("queued_evaluation_count") or 0) - 1,
        )

    def job_started(self) -> None:
        self.runtime_state["active_evaluation_count"] = (
            int(self.runtime_state.get("active_evaluation_count") or 0) + 1
        )

    def job_stopped(self) -> None:
        self.runtime_state["active_evaluation_count"] = max(
            0,
            int(self.runtime_state.get("active_evaluation_count") or 0) - 1,
        )

    def job_committed(self) -> None:
        self.runtime_state["completed_targets"] = (
            int(self.runtime_state.get("completed_targets") or 0) + 1
        )
        self.runtime_state["progress_completed"] = (
            int(self.runtime_state.get("progress_completed") or 0) + 1
        )

    def settle(
        self,
        *,
        result_count: int,
        control_action: str | None,
    ) -> str:
        self.runtime_state["is_running"] = False
        self.runtime_state["last_run_count"] = result_count
        self.runtime_state["current_target"] = None
        self.runtime_state["current_run_id"] = None
        self.runtime_state["active_evaluation_count"] = 0
        self.runtime_state["queued_evaluation_count"] = 0
        self.runtime_state["oldest_active_evaluation_started_at"] = None

        lifecycle = str(self.runtime_state.get("lifecycle_state") or "")
        if control_action == "stop":
            if lifecycle != "stopped":
                self.transition("stopped")
            return "stopped"
        if control_action == "pause":
            if lifecycle != "paused_recoverable":
                self.transition("paused_recoverable")
            return "paused_recoverable"
        if lifecycle in self._TERMINAL_LIFECYCLES:
            return lifecycle

        self.capture_last_phase()
        self.runtime_state["finalizing_started_at"] = self._timestamp()
        self.transition("finalizing")
        return "finalizing"

    def prepare_retained_checkpoint(self, lifecycle_state: str) -> str:
        if lifecycle_state not in {"failed", "finalizing"}:
            raise ValueError(
                f"unsupported retained checkpoint lifecycle: {lifecycle_state}"
            )
        self.runtime_state["is_running"] = False
        if lifecycle_state == "failed":
            self.runtime_state["finalizing_started_at"] = None
        elif self.runtime_state.get("finalizing_started_at") is None:
            self.runtime_state["finalizing_started_at"] = self._timestamp()
        if self.runtime_state.get("lifecycle_state") != lifecycle_state:
            self.transition(lifecycle_state)
        return lifecycle_state

    def restore_finalizing_failure(
        self,
        persisted_runtime: dict[str, object],
        *,
        error_message: str,
        updated_at: str,
    ) -> None:
        self.runtime_state.update(
            {
                "lifecycle_state": "finalizing",
                "state_changed_at": persisted_runtime.get("state_changed_at"),
                "finalizing_started_at": persisted_runtime.get(
                    "finalizing_started_at"
                ),
                "lease_expires_at": persisted_runtime.get("lease_expires_at"),
                "last_error": error_message,
                "updated_at": updated_at,
            }
        )

    def restore_idle(self, *, changed_at: str) -> None:
        self.runtime_state.update(
            {
                "is_running": False,
                "last_error": None,
                "current_target": None,
                "lifecycle_state": "idle",
                "state_changed_at": changed_at,
                "updated_at": changed_at,
                "finalizing_started_at": None,
                "lease_expires_at": None,
            }
        )


class RunControlCoordinator:
    def __init__(
        self,
        active_run_store: ActiveRunStore,
        *,
        run_id: str | None = None,
    ) -> None:
        self.active_run_store = active_run_store
        current = active_run_store.load()
        current_run_id = ActiveRunStore._run_id(current)
        explicit_run_id = str(run_id or "").strip()
        self.run_id = explicit_run_id or current_run_id or None
        # An empty owner id is intentional: a coordinator created before a
        # run exists must not later consume that run's control mailbox.
        self._claim_run_id = explicit_run_id or current_run_id
        self._owner_active_run_id = current_run_id
        self.action: str | None = None

    def reset(self, *, run_id: str | None = None) -> None:
        self.action = None
        expected_run_id = self.run_id
        if run_id is not None:
            expected_run_id = str(run_id or "").strip() or None
            self.run_id = expected_run_id
            self._claim_run_id = str(run_id or "").strip()
        self.active_run_store.clear_control_for_run(
            expected_run_id,
            owner_active_run_id=self._owner_active_run_id,
        )

    def poll(self) -> str | None:
        current = self.active_run_store.load()
        current_run_id = ActiveRunStore._run_id(current)
        if current is not None and current_run_id != self._claim_run_id:
            self.action = None
            return None
        request = self.active_run_store.claim_control(
            expected_run_id=self._claim_run_id,
        )
        action = str((request or {}).get("action") or "")
        if action in {"pause", "stop"}:
            if self.action is None or (
                self.action == "pause" and action == "stop"
            ):
                self.action = action
        return self.action

    def clear_action(self) -> None:
        self.action = None


def _clear_active_run_for_run(
    active_run_store: ActiveRunStore,
    run_id: str,
) -> None:
    clear_for_run = getattr(active_run_store, "clear_for_run", None)
    if callable(clear_for_run):
        try:
            clear_for_run(run_id)
            return
        except TypeError as exc:
            # Some embedders monkey-patch the legacy no-argument ``clear``
            # hook to observe terminal cleanup.  ActiveRunStore's ownership
            # check has already run before that compatibility path is used.
            if "unexpected keyword argument 'run_id'" not in str(exc):
                raise
    # Keep compatibility with lightweight test doubles and older adapters.
    active_run_store.clear()


class RunLifecycleCoordinator:
    def __init__(
        self,
        *,
        state_machine: RunStateMachine,
        history_store: HistoryStore,
        active_run_store: ActiveRunStore,
        journal_store: RunJournalStore,
        timestamp: Callable[[], str],
    ) -> None:
        self.state_machine = state_machine
        self.history_store = history_store
        self.active_run_store = active_run_store
        self.journal_store = journal_store
        self._timestamp = timestamp

    def journal_event(
        self,
        run_id: str,
        event_type: str,
        data: dict[str, object],
    ) -> None:
        if not run_id:
            return
        self.journal_store.append_event(
            run_id,
            event_type,
            data,
            occurred_at=self._timestamp(),
        )

    def save_summary(
        self,
        run_id: str,
        run_metadata: dict[str, object],
    ) -> None:
        runtime_state = self.state_machine.runtime_state
        self.journal_store.save_summary(
            run_id,
            {
                "status": str(run_metadata.get("status") or "running"),
                "progress_completed": int(
                    runtime_state.get("progress_completed") or 0
                ),
                "progress_total": int(runtime_state.get("progress_total") or 0),
                "lifecycle_state": str(
                    runtime_state.get("lifecycle_state") or "idle"
                ),
                "last_error": runtime_state.get("last_error"),
                "updated_at": str(
                    runtime_state.get("updated_at") or self._timestamp()
                ),
                "run_metadata": dict(run_metadata),
            },
        )

    def complete(
        self,
        *,
        run_id: str,
        run_metadata: dict[str, object],
        journal_event_type: str | None,
        journal_data: dict[str, object],
        clear_active_run: bool,
        capture_before_clear: bool = False,
        persist_journal_summary: bool = True,
    ) -> None:
        self.history_store.save_run_metadata(run_metadata)
        if journal_event_type is not None:
            self.journal_event(run_id, journal_event_type, journal_data)
        if persist_journal_summary:
            self.save_summary(run_id, run_metadata)
        if capture_before_clear:
            self.state_machine.capture_last_phase()
        if clear_active_run:
            _clear_active_run_for_run(self.active_run_store, run_id)
        if not capture_before_clear:
            self.state_machine.capture_last_phase()

    def fail(
        self,
        *,
        run_id: str,
        run_metadata: dict[str, object],
        error_message: str,
        journal_event_type: str | None,
        journal_data: dict[str, object],
        clear_active_run: bool,
        persist_journal_summary: bool = True,
        retain_active_checkpoint: bool = False,
    ) -> None:
        if clear_active_run and retain_active_checkpoint:
            raise ValueError("cannot clear and retain the same active run")
        self.history_store.save_run_metadata(run_metadata)
        if clear_active_run:
            _clear_active_run_for_run(self.active_run_store, run_id)
        runtime_state = self.state_machine.runtime_state
        runtime_state["last_error"] = error_message
        self.state_machine.transition("failed")
        if journal_event_type is not None:
            self.journal_event(run_id, journal_event_type, journal_data)
        if persist_journal_summary:
            self.save_summary(run_id, run_metadata)
        if retain_active_checkpoint:
            self.active_run_store.update_run_metadata(run_metadata)
            self.active_run_store.update_runtime_state(
                "failed",
                updated_at=str(runtime_state.get("updated_at") or ""),
                last_error=error_message,
            )

    def control(
        self,
        *,
        action: str,
        run_id: str,
        run_metadata: dict[str, object],
        journal_event_type: str,
        journal_data: dict[str, object],
        persist_controlled_metadata: bool = False,
        transition_before_persist: bool = True,
    ) -> None:
        if action not in {"pause", "stop"}:
            raise ValueError(f"unsupported execution control: {action}")
        historical_metadata = dict(run_metadata)
        controlled_metadata = dict(historical_metadata)
        controlled_metadata["status"] = "paused" if action == "pause" else "stopped"
        controlled_metadata["is_complete_regular_round"] = False
        controlled_metadata["completed_at"] = (
            None if action == "pause" else self._timestamp()
        )
        lifecycle = "paused_recoverable" if action == "pause" else "stopped"
        persisted_metadata = (
            controlled_metadata
            if persist_controlled_metadata
            else historical_metadata
        )
        if transition_before_persist:
            self.state_machine.capture_last_phase()
            self.state_machine.transition(lifecycle)
        self.history_store.save_run_metadata(persisted_metadata)
        self.journal_event(run_id, journal_event_type, journal_data)
        if not transition_before_persist:
            self.save_summary(run_id, persisted_metadata)
            self.state_machine.capture_last_phase()
            self.state_machine.transition(lifecycle)
        if action == "pause":
            self.active_run_store.update_run_metadata(controlled_metadata)
            self.active_run_store.update_runtime_state(
                lifecycle,
                updated_at=str(
                    self.state_machine.runtime_state.get("updated_at") or ""
                ),
            )
        else:
            _clear_active_run_for_run(self.active_run_store, run_id)
        if transition_before_persist:
            self.save_summary(run_id, persisted_metadata)


class FinalizationCoordinator:
    """Own explicit finalization recovery, failure, and commit commands."""

    def __init__(
        self,
        *,
        state_machine: RunStateMachine,
        lifecycle: RunLifecycleCoordinator,
        active_run_store: ActiveRunStore,
        journal_store: RunJournalStore,
        timestamp: Callable[[], str],
        scan_lock_is_active: Callable[[Path], bool],
        read_scan_lock_payload: Callable[[Path], tuple[int, float | None]],
        current_process_id: Callable[[], int],
    ) -> None:
        self.state_machine = state_machine
        self.lifecycle = lifecycle
        self.active_run_store = active_run_store
        self.journal_store = journal_store
        self._timestamp = timestamp
        self._scan_lock_is_active = scan_lock_is_active
        self._read_scan_lock_payload = read_scan_lock_payload
        self._current_process_id = current_process_id

    @staticmethod
    def _parse_iso_timestamp(value: object) -> float | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            return None

    @staticmethod
    def _recovery_result(
        run_id: str,
        status: str,
        message: str,
        *,
        recovered: bool = False,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "ok": True,
            "action": "recover_run",
            "recovered": recovered,
            "status": status,
            "message": message,
        }
        if run_id:
            payload["run_id"] = run_id
        return payload

    def recover_orphaned_finalizing_run(
        self,
        *,
        exclusive_lock_held: bool,
    ) -> dict[str, object]:
        if not exclusive_lock_held:
            raise RuntimeError("finalizing recovery requires the scan process lock")
        run_id = ""

        def result(
            status: str,
            message: str,
            *,
            recovered: bool = False,
        ) -> dict[str, object]:
            return self._recovery_result(
                run_id,
                status,
                message,
                recovered=recovered,
            )

        try:
            active_run = self.active_run_store.load()
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return result("incomplete", "活动运行记录无法读取，未执行恢复。")
        if isinstance(active_run, dict):
            run_id = str(active_run.get("run_id") or "").strip()
        if active_run is None:
            return result("no_active_run", "没有需要恢复的活动运行。")
        if not isinstance(active_run, dict):
            return result("incomplete", "活动运行记录格式异常，未执行恢复。")
        if "runtime" not in active_run:
            legacy_entries = active_run.get("entries")
            if (
                run_id
                and isinstance(legacy_entries, list)
                and all(isinstance(item, dict) for item in legacy_entries)
            ):
                return result("not_finalizing", "活动运行不处于收尾状态。")
            return result("incomplete", "活动运行状态格式异常，未执行恢复。")
        persisted_runtime = active_run.get("runtime")
        if not isinstance(persisted_runtime, dict):
            return result("incomplete", "活动运行状态格式异常，未执行恢复。")
        lifecycle_state = persisted_runtime.get("lifecycle_state")
        if not isinstance(lifecycle_state, str) or not lifecycle_state.strip():
            return result("incomplete", "活动运行状态不完整，未执行恢复。")
        if normalize_lifecycle(lifecycle_state) != "finalizing":
            return result("not_finalizing", "活动运行不处于收尾状态。")
        lock_path = self.active_run_store.path.with_name("scan.lock")
        lock_pid, _ = self._read_scan_lock_payload(lock_path)
        if (
            self._scan_lock_is_active(lock_path)
            and lock_pid != self._current_process_id()
        ):
            return result("scan_active", "扫描进程仍在运行，未执行恢复。")

        run_metadata = active_run.get("run_metadata")
        if run_metadata is not None and not isinstance(run_metadata, dict):
            return result("incomplete", "活动运行元数据格式异常，未执行恢复。")
        completed_at = run_metadata.get("completed_at") if run_metadata else None
        if (
            completed_at is not None
            and self._parse_iso_timestamp(completed_at) is None
        ):
            return result("incomplete", "活动运行完成时间格式异常，未执行恢复。")

        entries = active_run.get("entries")
        if not isinstance(entries, list):
            return result("incomplete", "活动运行条目格式异常，未执行恢复。")
        entries_complete = bool(entries)
        for item in entries:
            if not isinstance(item, dict):
                return result("incomplete", "活动运行条目格式异常，未执行恢复。")
            attempts_per_target = item.get("attempts_per_target")
            attempts_completed = item.get("attempts_completed")
            if (
                not isinstance(attempts_per_target, int)
                or isinstance(attempts_per_target, bool)
                or not isinstance(attempts_completed, int)
                or isinstance(attempts_completed, bool)
            ):
                return result("incomplete", "活动运行进度格式异常，未执行恢复。")
            if attempts_per_target <= 0 or attempts_completed < attempts_per_target:
                entries_complete = False
        if not completed_at and not entries_complete:
            return result("incomplete", "收尾完成证据不足，未执行恢复。")

        recovered_at = self._timestamp()
        terminal_metadata = dict(run_metadata or {})
        terminal_metadata["status"] = str(
            terminal_metadata.get("status") or "completed"
        )
        progress_completed = int(persisted_runtime.get("progress_completed") or 0)
        progress_total = int(persisted_runtime.get("progress_total") or 0)
        if progress_total <= 0:
            progress_completed = sum(
                int(item.get("attempts_completed") or 0)
                for item in entries
                if isinstance(item, dict)
            )
            progress_total = sum(
                int(item.get("attempts_per_target") or 0)
                for item in entries
                if isinstance(item, dict)
            )
        finalizing_summary = {
            "status": terminal_metadata["status"],
            "progress_completed": progress_completed,
            "progress_total": progress_total,
            "lifecycle_state": "finalizing",
            "last_error": persisted_runtime.get("last_error"),
            "updated_at": str(
                persisted_runtime.get("updated_at") or recovered_at
            ),
            "run_metadata": terminal_metadata,
        }
        idle_summary = {
            **finalizing_summary,
            "lifecycle_state": "idle",
            "last_error": None,
            "updated_at": recovered_at,
        }

        def restore_finalizing_summary() -> None:
            try:
                self.journal_store.save_summary(run_id, finalizing_summary)
            except OSError:
                pass

        try:
            self.journal_store.save_summary(run_id, idle_summary)
        except OSError as exc:
            restore_finalizing_summary()
            return result(
                "incomplete",
                f"收尾摘要无法更新，未执行恢复：{exc}",
            )

        try:
            _clear_active_run_for_run(self.active_run_store, run_id)
        except OSError as exc:
            restore_finalizing_summary()
            return result(
                "incomplete",
                f"活动运行记录无法清理，未执行恢复：{exc}",
            )

        try:
            self.lifecycle.journal_event(
                run_id,
                "run.finalization_recovered",
                {"status": terminal_metadata["status"]},
            )
        except OSError as exc:
            try:
                self.active_run_store.save(active_run)
            except OSError:
                pass
            restore_finalizing_summary()
            return result(
                "incomplete",
                f"收尾恢复事件无法记录，恢复状态未确认：{exc}",
            )
        self.state_machine.restore_idle(changed_at=recovered_at)
        return result(
            "recovered",
            "已清理孤立的已完成收尾状态。",
            recovered=True,
        )

    def _completed_finalizing_run(
        self,
        *,
        exclusive_lock_held: bool,
    ) -> tuple[dict[str, object], str, dict[str, object]]:
        if not exclusive_lock_held:
            raise RuntimeError("finalization requires the scan process lock")
        active_run = self.active_run_store.load()
        if not isinstance(active_run, dict):
            raise RuntimeError("completed finalizing run is missing")
        run_id = str(active_run.get("run_id") or "").strip()
        persisted_runtime = active_run.get("runtime")
        if (
            not run_id
            or not isinstance(persisted_runtime, dict)
            or normalize_lifecycle(persisted_runtime.get("lifecycle_state"))
            != "finalizing"
        ):
            raise RuntimeError("completed run is not finalizing")
        run_metadata = active_run.get("run_metadata")
        if (
            not isinstance(run_metadata, dict)
            or self._parse_iso_timestamp(run_metadata.get("completed_at")) is None
        ):
            raise RuntimeError("completed finalizing run metadata is incomplete")
        return active_run, run_id, run_metadata

    def record_failure(
        self,
        error_message: str,
        *,
        exclusive_lock_held: bool,
        journal_event_type: str,
    ) -> tuple[str, list[str]]:
        active_run, run_id, run_metadata = self._completed_finalizing_run(
            exclusive_lock_held=exclusive_lock_held,
        )
        persisted_runtime = active_run["runtime"]
        assert isinstance(persisted_runtime, dict)

        message = error_message.strip() or "terminal finalization failed"
        updated_at = self._timestamp()
        self.state_machine.restore_finalizing_failure(
            persisted_runtime,
            error_message=message,
            updated_at=updated_at,
        )

        def record_failure(current: dict[str, object]) -> dict[str, object]:
            runtime = current.get("runtime")
            if (
                not isinstance(runtime, dict)
                or normalize_lifecycle(runtime.get("lifecycle_state"))
                != "finalizing"
            ):
                raise RuntimeError("completed run is not finalizing")
            updated_runtime = dict(runtime)
            updated_runtime["last_error"] = message
            updated_runtime["updated_at"] = updated_at
            return {**current, "runtime": updated_runtime}

        persistence_errors: list[str] = []
        try:
            self.active_run_store.mutate(record_failure)
        except OSError as exc:
            persistence_errors.append(f"active_run: {exc}")
        try:
            self.lifecycle.journal_event(
                run_id,
                journal_event_type,
                {"error_message": message},
            )
        except OSError as exc:
            persistence_errors.append(f"journal_event: {exc}")
        try:
            self.lifecycle.save_summary(run_id, run_metadata)
        except OSError as exc:
            persistence_errors.append(f"journal_summary: {exc}")
        return message, persistence_errors

    def complete_snapshot(
        self,
        projected_state: dict[str, object],
        *,
        exclusive_lock_held: bool,
    ) -> dict[str, object]:
        projected_runtime = projected_state.get("runtime")
        if not isinstance(projected_runtime, dict):
            raise RuntimeError("projected snapshot runtime is missing")
        _active_run, run_id, run_metadata = self._completed_finalizing_run(
            exclusive_lock_held=exclusive_lock_held,
        )
        committed_at = self._timestamp()

        terminal_runtime = dict(projected_runtime)
        terminal_runtime.update(
            {
                "is_running": False,
                "last_error": None,
                "lifecycle_state": "idle",
                "state_changed_at": committed_at,
                "updated_at": committed_at,
                "finalizing_started_at": None,
                "lease_expires_at": None,
                "current_target": None,
                "has_resumable_run": False,
                "resumable_run_id": None,
            }
        )
        terminal_state = {
            **projected_state,
            "runtime": terminal_runtime,
        }
        self.journal_store.save_summary(
            run_id,
            {
                "status": str(run_metadata.get("status") or "completed"),
                "progress_completed": int(
                    terminal_runtime.get("progress_completed") or 0
                ),
                "progress_total": int(
                    terminal_runtime.get("progress_total") or 0
                ),
                "lifecycle_state": "idle",
                "last_error": None,
                "updated_at": committed_at,
                "run_metadata": dict(run_metadata),
            },
        )
        _clear_active_run_for_run(self.active_run_store, run_id)
        self.state_machine.restore_idle(changed_at=committed_at)
        return terminal_state


ResultT = TypeVar("ResultT")


class ExecutionEngine(Generic[ResultT]):
    def execute(
        self,
        jobs: list[ExecutionJob],
        *,
        max_workers: int,
        try_start: Callable[[ExecutionJob], bool],
        run_job: Callable[[ExecutionJob], ResultT],
        finish_job: Callable[[ExecutionJob, ResultT], None],
        fail_job: Callable[[ExecutionJob, Exception], None],
        stop_on_failure: bool = False,
        skip_job: Callable[[ExecutionJob], None] | None = None,
        group_key: Callable[[ExecutionJob], str] | None = None,
        max_workers_by_group: Mapping[str, int] | None = None,
    ) -> None:
        if not jobs:
            return
        if (group_key is None) != (max_workers_by_group is None):
            raise ValueError(
                "group_key and max_workers_by_group must be provided together"
            )
        failure = threading.Event()

        def execute_job(job: ExecutionJob) -> None:
            if stop_on_failure and failure.is_set():
                if skip_job is not None:
                    skip_job(job)
                return
            if not try_start(job):
                return
            try:
                result = run_job(job)
            except Exception as exc:
                if stop_on_failure:
                    failure.set()
                fail_job(job, exc)
                raise
            finish_job(job, result)

        worker_count = min(len(jobs), max(1, max_workers))
        if group_key is not None and max_workers_by_group is not None:
            job_queues: dict[str, deque[ExecutionJob]] = {}
            group_order: list[str] = []
            group_limits: dict[str, int] = {}
            for job in jobs:
                key = group_key(job)
                if not key:
                    raise ValueError("execution job group key must not be empty")
                if key not in max_workers_by_group:
                    raise ValueError(f"execution job group has no limit: {key}")
                limit = int(max_workers_by_group[key])
                if limit < 1:
                    raise ValueError(
                        f"execution job group limit must be positive: {key}"
                    )
                if key not in job_queues:
                    job_queues[key] = deque()
                    group_order.append(key)
                    group_limits[key] = limit
                job_queues[key].append(job)

            active_by_group = {key: 0 for key in group_order}
            group_cursor = 0
            first_error: Exception | None = None
            futures: dict[Future[None], str] = {}

            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                while futures or any(job_queues.values()):
                    while len(futures) < worker_count:
                        selected_index: int | None = None
                        for offset in range(len(group_order)):
                            index = (group_cursor + offset) % len(group_order)
                            key = group_order[index]
                            if (
                                job_queues[key]
                                and active_by_group[key] < group_limits[key]
                            ):
                                selected_index = index
                                break
                        if selected_index is None:
                            break
                        key = group_order[selected_index]
                        group_cursor = (selected_index + 1) % len(group_order)
                        job = job_queues[key].popleft()
                        active_by_group[key] += 1
                        futures[executor.submit(execute_job, job)] = key

                    if not futures:
                        if any(job_queues.values()):
                            raise RuntimeError(
                                "execution group limits prevent pending jobs from starting"
                            )
                        break

                    completed, _pending = wait(
                        tuple(futures),
                        return_when=FIRST_COMPLETED,
                    )
                    for future in completed:
                        key = futures.pop(future)
                        active_by_group[key] -= 1
                        try:
                            future.result()
                        except Exception as exc:
                            if first_error is None:
                                first_error = exc

                    if stop_on_failure and failure.is_set():
                        for key in group_order:
                            while job_queues[key]:
                                job = job_queues[key].popleft()
                                if skip_job is not None:
                                    skip_job(job)

            if first_error is not None:
                raise first_error
            return

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(execute_job, job) for job in jobs]
            for future in futures:
                future.result()


@dataclass(frozen=True)
class ExecutionJobCallbacks(Generic[ResultT]):
    run_job: Callable[[ExecutionJob], ResultT]
    persist_state: Callable[[], None]
    can_start: Callable[[ExecutionJob], bool] | None = None
    on_not_started: Callable[[ExecutionJob], None] | None = None
    on_started: Callable[[ExecutionJob], None] | None = None
    after_started: Callable[[ExecutionJob], None] | None = None
    on_stopped: Callable[[ExecutionJob], None] | None = None
    on_failed: Callable[[ExecutionJob, Exception], None] | None = None
    on_discarded: Callable[[ExecutionJob, ResultT], None] | None = None
    on_finished: Callable[[ExecutionJob, ResultT], None] | None = None
    after_finished: Callable[[ExecutionJob, ResultT], None] | None = None
    on_skipped: Callable[[ExecutionJob], None] | None = None
    discard_result: Callable[[ResultT, str | None], bool] | None = None
    persist_on_start: bool = True
    persist_on_failure: bool = True
    persist_on_skip: bool = True


_UNSET_CONTROL_ACTION = object()


class ExecutionSession(Generic[ResultT]):
    """Shared worker and durable lifecycle boundary for one scan operation."""

    def __init__(
        self,
        *,
        context: ExecutionContext,
        state_machine: RunStateMachine,
        lifecycle: RunLifecycleCoordinator,
        engine: ExecutionEngine[ResultT],
        history_store: HistoryStore,
        active_run_store: ActiveRunStore,
        on_control: Callable[[str], None] | None = None,
    ) -> None:
        self.context = context
        self.state_machine = state_machine
        self.lifecycle = lifecycle
        self.engine = engine
        self.history_store = history_store
        self.control = RunControlCoordinator(
            active_run_store,
            run_id=context.run_id,
        )
        self.results: list[ResultT] = []
        self.lock = threading.RLock()
        self._on_control = on_control
        self._settled = False

    @property
    def control_action(self) -> str | None:
        return self.control.action

    def begin(
        self,
        *,
        run_entries: list[dict[str, object]],
        last_run_mode: str,
        current_phase: str,
        lease_duration_seconds: int,
    ) -> None:
        self.control.reset(run_id=self.context.run_id)
        self.state_machine.begin(
            self.context,
            run_entries=run_entries,
            last_run_mode=last_run_mode,
            current_phase=current_phase,
            lease_duration_seconds=lease_duration_seconds,
        )

    def poll_control(self) -> str | None:
        previous_action = self.control.action
        action = self.control.poll()
        if (
            action is not None
            and action != previous_action
            and self._on_control is not None
        ):
            self._on_control(action)
        return action

    def clear_control_action(self) -> None:
        self.control.clear_action()

    def execute_jobs(
        self,
        jobs: list[ExecutionJob],
        *,
        callbacks: ExecutionJobCallbacks[ResultT],
        stop_on_failure: bool = False,
        persist_before_execute: bool = False,
        group_key: Callable[[ExecutionJob], str] | None = None,
        max_workers_by_group: Mapping[str, int] | None = None,
    ) -> None:
        runtime_state = self.state_machine.runtime_state
        runtime_state["active_evaluation_count"] = 0
        runtime_state["queued_evaluation_count"] = len(jobs)
        if persist_before_execute:
            with self.lock:
                callbacks.persist_state()

        def persist_if(enabled: bool) -> None:
            if enabled:
                callbacks.persist_state()

        def try_start(job: ExecutionJob) -> bool:
            with self.lock:
                self.state_machine.job_dequeued()
                if callbacks.can_start is not None and not callbacks.can_start(job):
                    if callbacks.on_not_started is not None:
                        callbacks.on_not_started(job)
                    persist_if(callbacks.persist_on_skip)
                    return False
                if self.poll_control() is not None:
                    return False
                self.state_machine.job_started()
                if callbacks.on_started is not None:
                    callbacks.on_started(job)
                persist_if(callbacks.persist_on_start)
                if callbacks.after_started is not None:
                    callbacks.after_started(job)
                return True

        def skip(job: ExecutionJob) -> None:
            with self.lock:
                self.state_machine.job_dequeued()
                if callbacks.on_skipped is not None:
                    callbacks.on_skipped(job)
                persist_if(callbacks.persist_on_skip)

        def fail(job: ExecutionJob, error: Exception) -> None:
            with self.lock:
                self.state_machine.job_stopped()
                if callbacks.on_stopped is not None:
                    callbacks.on_stopped(job)
                if callbacks.on_failed is not None:
                    callbacks.on_failed(job, error)
                persist_if(callbacks.persist_on_failure)

        def finish(job: ExecutionJob, result: ResultT) -> None:
            with self.lock:
                self.state_machine.job_stopped()
                if callbacks.on_stopped is not None:
                    callbacks.on_stopped(job)
                control_action = self.poll_control()
                if (
                    callbacks.discard_result is not None
                    and callbacks.discard_result(result, control_action)
                ):
                    if callbacks.on_discarded is not None:
                        callbacks.on_discarded(job, result)
                    return
                self.history_store.append(result)  # type: ignore[arg-type]
                self.results.append(result)
                if callbacks.on_finished is not None:
                    callbacks.on_finished(job, result)
                self.state_machine.job_committed()
                callbacks.persist_state()
                if callbacks.after_finished is not None:
                    callbacks.after_finished(job, result)

        self.engine.execute(
            jobs,
            max_workers=self.context.max_workers,
            try_start=try_start,
            run_job=callbacks.run_job,
            finish_job=finish,
            fail_job=fail,
            stop_on_failure=stop_on_failure,
            skip_job=skip,
            group_key=group_key,
            max_workers_by_group=max_workers_by_group,
        )

    def complete(
        self,
        *,
        run_metadata: dict[str, object],
        journal_event_type: str | None,
        journal_data: dict[str, object],
        clear_active_run: bool,
        capture_before_clear: bool = False,
        persist_journal_summary: bool = True,
        settle_before_persist: bool = False,
    ) -> None:
        if settle_before_persist:
            self.settle()
        self.lifecycle.complete(
            run_id=self.context.run_id,
            run_metadata=run_metadata,
            journal_event_type=journal_event_type,
            journal_data=journal_data,
            clear_active_run=clear_active_run,
            capture_before_clear=capture_before_clear,
            persist_journal_summary=persist_journal_summary,
        )

    def fail(
        self,
        *,
        run_metadata: dict[str, object],
        error_message: str,
        journal_event_type: str | None,
        journal_data: dict[str, object],
        clear_active_run: bool,
        persist_journal_summary: bool = True,
        retain_active_checkpoint: bool = False,
    ) -> None:
        self.lifecycle.fail(
            run_id=self.context.run_id,
            run_metadata=run_metadata,
            error_message=error_message,
            journal_event_type=journal_event_type,
            journal_data=journal_data,
            clear_active_run=clear_active_run,
            persist_journal_summary=persist_journal_summary,
            retain_active_checkpoint=retain_active_checkpoint,
        )

    def finish_completed(
        self,
        *,
        run_metadata: dict[str, object],
        journal_event_type: str | None,
        journal_data: dict[str, object],
        clear_active_run: bool,
        capture_before_clear: bool = False,
        persist_journal_summary: bool = True,
        settle_before_persist: bool = False,
        settle_before_checkpoint: bool = False,
        retained_lifecycle: str = "finalizing",
        persist_retained_checkpoint: Callable[[], None] | None = None,
        on_retained: Callable[[str], None] | None = None,
        settle_after: bool = True,
    ) -> str:
        if clear_active_run and persist_retained_checkpoint is not None:
            raise ValueError("cannot clear and retain the same active run")
        self.complete(
            run_metadata=run_metadata,
            journal_event_type=journal_event_type,
            journal_data=journal_data,
            clear_active_run=clear_active_run,
            capture_before_clear=capture_before_clear,
            persist_journal_summary=persist_journal_summary,
            settle_before_persist=settle_before_persist,
        )
        if persist_retained_checkpoint is not None:
            if settle_before_checkpoint:
                self.settle(control_action=None)
            lifecycle = self.state_machine.prepare_retained_checkpoint(
                retained_lifecycle
            )
            persist_retained_checkpoint()
            if on_retained is not None:
                on_retained(lifecycle)
        if settle_after:
            return self.settle(control_action=None)
        return str(
            self.state_machine.runtime_state.get("lifecycle_state") or "idle"
        )

    def finish_failed(
        self,
        *,
        run_metadata: dict[str, object],
        error_message: str,
        journal_event_type: str | None,
        journal_data: dict[str, object],
        clear_active_run: bool,
        persist_journal_summary: bool = True,
        retain_active_checkpoint: bool = False,
        settle_after: bool = True,
    ) -> str:
        self.fail(
            run_metadata=run_metadata,
            error_message=error_message,
            journal_event_type=journal_event_type,
            journal_data=journal_data,
            clear_active_run=clear_active_run,
            persist_journal_summary=persist_journal_summary,
            retain_active_checkpoint=retain_active_checkpoint,
        )
        if settle_after:
            return self.settle(control_action=None)
        return str(
            self.state_machine.runtime_state.get("lifecycle_state") or "failed"
        )

    def finish_controlled(
        self,
        *,
        run_metadata: dict[str, object],
        journal_event_type: str,
        journal_data: dict[str, object],
        persist_controlled_metadata: bool = False,
        transition_before_persist: bool = True,
        settle_after: bool = True,
    ) -> str:
        action = self.control_action
        if action is None:
            raise RuntimeError("execution control was not requested")
        self.lifecycle.control(
            action=action,
            run_id=self.context.run_id,
            run_metadata=run_metadata,
            journal_event_type=journal_event_type,
            journal_data=journal_data,
            persist_controlled_metadata=persist_controlled_metadata,
            transition_before_persist=transition_before_persist,
        )
        if settle_after:
            return self.settle()
        return str(
            self.state_machine.runtime_state.get("lifecycle_state") or "idle"
        )

    def settle(
        self,
        *,
        control_action: str | None | object = _UNSET_CONTROL_ACTION,
    ) -> str:
        if self._settled:
            return str(
                self.state_machine.runtime_state.get("lifecycle_state") or "idle"
            )
        effective_control = (
            self.control_action
            if control_action is _UNSET_CONTROL_ACTION
            else control_action
        )
        lifecycle = self.state_machine.settle(
            result_count=len(self.results),
            control_action=(
                str(effective_control) if effective_control is not None else None
            ),
        )
        self._settled = True
        return lifecycle


def create_execution_session(
    *,
    context: ExecutionContext,
    state_machine: RunStateMachine,
    lifecycle: RunLifecycleCoordinator,
    engine: ExecutionEngine[ResultT],
    history_store: HistoryStore,
    active_run_store: ActiveRunStore,
    on_control: Callable[[str], None] | None,
) -> ExecutionSession[ResultT]:
    return ExecutionSession(
        context=context,
        state_machine=state_machine,
        lifecycle=lifecycle,
        engine=engine,
        history_store=history_store,
        active_run_store=active_run_store,
        on_control=on_control,
    )
