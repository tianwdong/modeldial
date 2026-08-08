from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import time

from .active_run_store import ActiveRunStore
from .comparison_groups import ComparisonGroupProjector
from .history_store import HistoryStore
from .legacy_scan_compat import (
    SCAN_PHASE,
    is_active_lifecycle,
    normalize_lifecycle,
    normalize_phase,
    planned_attempts_payload,
)
from .models import AppConfig, ResolvedScanTarget, ScanResult
from .scan_target_resolver import ScanTargetResolver


ScanLockIsActive = Callable[[Path], bool]
Clock = Callable[[], float]


def _parse_iso_timestamp(value: object) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


@dataclass(frozen=True)
class RuntimeSnapshotProjector:
    runtime_state: dict[str, object]
    history_store: HistoryStore
    active_run_store: ActiveRunStore
    target_resolver: ScanTargetResolver
    comparison_group_projector: ComparisonGroupProjector
    scan_lock_is_active: ScanLockIsActive
    stale_seconds: int
    clock: Clock = time.time

    def project(
        self,
        config: AppConfig,
        history: list[ScanResult],
        active_run: dict[str, object] | None,
        *,
        history_count: int | None = None,
    ) -> dict[str, object]:
        enabled_targets = self.target_resolver.enabled_targets(config)
        live_scan_lock_active = self.scan_lock_is_active(
            self.active_run_store.path.with_name("scan.lock")
        )
        stale_run_progress = self._run_progress_is_stale(active_run, history)
        if stale_run_progress:
            live_scan_lock_active = False
        runtime = {
            "enabled_target_count": len(enabled_targets),
            "history_count": len(history) if history_count is None else history_count,
            "is_running": self.runtime_state["is_running"] or (
                live_scan_lock_active and active_run is not None
            ),
            "last_run_count": self.runtime_state["last_run_count"],
            "last_error": self.runtime_state["last_error"],
            "last_run_mode": "mock" if config.system.use_mock_results else "live",
            "completed_targets": self.runtime_state["completed_targets"],
            "total_targets": self.runtime_state["total_targets"],
            "progress_percent": self._progress_percent(),
            "current_target": self.runtime_state["current_target"],
            "run_entries": [
                dict(entry) for entry in self.runtime_state["run_entries"]
            ],
            "current_run_id": self.runtime_state["current_run_id"],
            "has_resumable_run": False,
            "resumable_run_id": None,
            "resumable_operation_kind": None,
            "resumable_operation_run_id": None,
            "resumable_candidate_ids": [],
            "resumable_question_id": None,
            "current_phase": None,
            "current_phase_completed_targets": 0,
            "current_phase_total_targets": 0,
            "progress_completed": int(
                self.runtime_state.get("progress_completed") or 0
            ),
            "progress_total": int(self.runtime_state.get("progress_total") or 0),
            "progress_unit": "evaluationUnit",
            "active_evaluation_count": int(
                self.runtime_state.get("active_evaluation_count") or 0
            ),
            "queued_evaluation_count": int(
                self.runtime_state.get("queued_evaluation_count") or 0
            ),
            "oldest_active_evaluation_started_at": self.runtime_state.get(
                "oldest_active_evaluation_started_at"
            ),
            "execution_timeout_seconds": max(
                60,
                int(config.system.execution_timeout_seconds),
            ),
            "lifecycle_state": self.runtime_state.get("lifecycle_state") or "idle",
            "state_changed_at": self.runtime_state.get("state_changed_at"),
            "finalizing_started_at": self.runtime_state.get(
                "finalizing_started_at"
            ),
            "last_phase": self.runtime_state.get("last_phase"),
            "last_phase_completed": int(
                self.runtime_state.get("last_phase_completed") or 0
            ),
            "last_phase_total": int(
                self.runtime_state.get("last_phase_total") or 0
            ),
            "updated_at": self.runtime_state.get("updated_at"),
            "lease_expires_at": self.runtime_state.get("lease_expires_at"),
        }
        if self.runtime_state.get("current_phase") == "scan":
            phase = "scan"
            phase_completed = int(runtime["progress_completed"])
            phase_total = int(runtime["progress_total"])
        else:
            phase, phase_completed, phase_total = self._current_phase_progress(
                runtime["run_entries"]
            )
        runtime["current_phase"] = phase
        runtime["current_phase_completed_targets"] = phase_completed
        runtime["current_phase_total_targets"] = phase_total
        if phase is not None:
            runtime["progress_completed"] = phase_completed
            runtime["progress_total"] = phase_total
        if self.runtime_state["is_running"] or not active_run:
            return runtime

        run_id = str(active_run.get("run_id"))
        active_metadata = active_run.get("run_metadata")
        comparison_group_mode = (
            str(active_metadata.get("comparison_group_mode") or "")
            if isinstance(active_metadata, dict)
            else ""
        )
        if comparison_group_mode in {
            "custom_append",
            "incremental_full",
            "profile_upgrade",
        }:
            metadata_by_run = self.history_store.load_run_metadata_map()
            if isinstance(active_metadata, dict):
                metadata_by_run[run_id] = {
                    **metadata_by_run.get(run_id, {}),
                    **active_metadata,
                }
            group_id = self.comparison_group_projector.group_id(
                run_id,
                active_metadata if isinstance(active_metadata, dict) else None,
            )
            group_run_ids = self.comparison_group_projector.member_run_ids(
                group_id=str(group_id or run_id),
                history=history,
                run_metadata_by_id=metadata_by_run,
            )
            if run_id not in group_run_ids:
                group_run_ids.append(run_id)
            group_run_id_set = set(group_run_ids)
            run_history = [
                item for item in history if item.run_id in group_run_id_set
            ]
        else:
            run_history = [item for item in history if item.run_id == run_id]
        entry_payloads = [
            item
            for item in active_run.get("entries", [])
            if isinstance(item, dict)
        ]
        persisted_runtime = active_run.get("runtime")
        if not isinstance(persisted_runtime, dict):
            persisted_runtime = {}
        persisted_lifecycle = normalize_lifecycle(
            persisted_runtime.get("lifecycle_state") or "paused_recoverable"
        )
        lease_expires_at = _parse_iso_timestamp(
            persisted_runtime.get("lease_expires_at")
        )
        lease_is_valid = (
            lease_expires_at is not None and lease_expires_at >= self.clock()
        )
        persisted_active = (
            is_active_lifecycle(persisted_lifecycle) and lease_is_valid
        )
        if persisted_active:
            live_scan_lock_active = True
        elif is_active_lifecycle(persisted_lifecycle):
            persisted_lifecycle = "paused_recoverable"
        if (
            persisted_runtime.get("lifecycle_state")
            and not is_active_lifecycle(persisted_lifecycle)
        ):
            live_scan_lock_active = False

        if not entry_payloads:
            progress_completed = int(
                persisted_runtime.get("progress_completed") or 0
            )
            progress_total = int(persisted_runtime.get("progress_total") or 0)
            current_phase = persisted_runtime.get("current_phase")
            runtime.update(
                {
                    "is_running": live_scan_lock_active,
                    "completed_targets": progress_completed,
                    "total_targets": progress_total,
                    "progress_percent": (
                        round(progress_completed * 100 / progress_total)
                        if progress_total
                        else 0
                    ),
                    "current_run_id": run_id,
                    "current_phase": current_phase,
                    "current_phase_completed_targets": progress_completed,
                    "current_phase_total_targets": progress_total,
                    "progress_completed": progress_completed,
                    "progress_total": progress_total,
                    "lifecycle_state": persisted_lifecycle,
                    "last_error": persisted_runtime.get(
                        "last_error",
                        runtime["last_error"],
                    ),
                    "state_changed_at": persisted_runtime.get("state_changed_at"),
                    "finalizing_started_at": persisted_runtime.get(
                        "finalizing_started_at"
                    ),
                    "last_phase": persisted_runtime.get("last_phase"),
                    "last_phase_completed": int(
                        persisted_runtime.get("last_phase_completed") or 0
                    ),
                    "last_phase_total": int(
                        persisted_runtime.get("last_phase_total") or 0
                    ),
                    "updated_at": persisted_runtime.get("updated_at"),
                    "lease_expires_at": persisted_runtime.get("lease_expires_at"),
                    "active_evaluation_count": (
                        int(
                            persisted_runtime.get("active_evaluation_count") or 0
                        )
                        if live_scan_lock_active
                        else 0
                    ),
                    "queued_evaluation_count": (
                        int(
                            persisted_runtime.get("queued_evaluation_count") or 0
                        )
                        if live_scan_lock_active
                        else 0
                    ),
                    "oldest_active_evaluation_started_at": (
                        persisted_runtime.get(
                            "oldest_active_evaluation_started_at"
                        )
                        if live_scan_lock_active
                        else None
                    ),
                    "execution_timeout_seconds": max(
                        60,
                        int(
                            persisted_runtime.get("execution_timeout_seconds")
                            or config.system.execution_timeout_seconds
                        ),
                    ),
                }
            )
            return runtime

        has_persisted_repair = any(
            str(item.get("phase") or "") == "repair" for item in entry_payloads
        )

        candidate_ids_by_label = self.target_resolver.candidate_ids_by_label(
            enabled_targets
        )
        planned_attempts = self._planned_attempts_by_candidate(
            planned_attempts_payload(active_run),
            enabled_targets,
        )
        latest_by_candidate: dict[str, ScanResult] = {}
        unique_step_keys: set[tuple[str, str, str]] = set()
        for item in run_history:
            candidate_id = self.target_resolver.result_candidate_id(
                item,
                candidate_ids_by_label,
            )
            if candidate_id is None:
                return runtime
            latest_by_candidate[candidate_id] = item
            unique_step_keys.add(
                (candidate_id, normalize_phase(item.phase), item.question_id)
            )
        entries: list[dict[str, object]] = []
        total_targets = 0
        for item in entry_payloads:
            candidate_id = self.target_resolver.entry_candidate_id(
                item,
                candidate_ids_by_label,
            )
            if candidate_id is None:
                return runtime
            label = str(item["label"])
            phase = normalize_phase(item.get("phase", SCAN_PHASE))
            attempts_total = int(item.get("attempts_per_target", 0))
            if phase != "repair":
                attempts_total = planned_attempts.get(candidate_id, 0) or attempts_total
            total_targets += attempts_total
            latest = latest_by_candidate.get(candidate_id)
            if phase == "repair":
                attempts_completed = min(
                    int(item.get("attempts_completed") or 0),
                    attempts_total,
                )
            elif has_persisted_repair:
                attempts_completed = min(
                    int(item.get("attempts_completed") or 0),
                    attempts_total,
                )
            else:
                attempts_completed = sum(
                    1
                    for item_candidate_id, item_phase, _ in unique_step_keys
                    if item_candidate_id == candidate_id and item_phase == phase
                )
            stored_status = str(item.get("status") or "pending")
            if attempts_completed >= attempts_total:
                status = "done"
            elif stored_status == "failed":
                status = "failed"
            elif live_scan_lock_active and stored_status in {"running", "pending"}:
                status = stored_status
            else:
                status = "interrupted"
            entries.append(
                {
                    "candidate_id": candidate_id,
                    "model": item["model"],
                    "effort": item["effort"],
                    "label": label,
                    "status": status,
                    "final_status": (
                        latest.final_status if latest else item.get("final_status")
                    ),
                    "reasoning_tokens": (
                        latest.reasoning_tokens
                        if latest
                        else item.get("reasoning_tokens")
                    ),
                    "attempts_completed": attempts_completed,
                    "attempts_per_target": attempts_total,
                    "phase": phase,
                    "flags": (
                        list(latest.flags)
                        if latest
                        else list(item.get("flags", []))
                    ),
                    "error_message": (
                        latest.error_message if latest else item.get("error_message")
                    ),
                }
            )
        repair_entries = [
            entry for entry in entries if entry["phase"] == "repair"
        ]
        if repair_entries:
            completed_targets = sum(
                int(entry["attempts_completed"]) for entry in repair_entries
            )
            total_targets = sum(
                int(entry["attempts_per_target"]) for entry in repair_entries
            )
        else:
            completed_targets = len(unique_step_keys)
        runtime.update(
            {
                "is_running": live_scan_lock_active,
                "completed_targets": completed_targets,
                "total_targets": total_targets,
                "progress_percent": (
                    round(completed_targets * 100 / total_targets)
                    if total_targets
                    else 0
                ),
                "current_target": None,
                "run_entries": entries,
                "current_run_id": run_id,
                "has_resumable_run": completed_targets < total_targets,
                "resumable_run_id": (
                    run_id if completed_targets < total_targets else None
                ),
                "resumable_operation_kind": (
                    str(active_run.get("repair_operation_kind") or "scan")
                    if completed_targets < total_targets
                    else None
                ),
                "resumable_operation_run_id": (
                    str(
                        active_run.get("repair_operation_run_id")
                        or active_run.get("repair_run_id")
                        or run_id
                    )
                    if completed_targets < total_targets
                    else None
                ),
                "resumable_candidate_ids": (
                    self._resumable_repair_candidate_ids(active_run)
                    if completed_targets < total_targets
                    else []
                ),
                "resumable_question_id": (
                    str(active_run.get("repair_question_id"))
                    if completed_targets < total_targets
                    and active_run.get("repair_question_id")
                    else None
                ),
                "lifecycle_state": persisted_lifecycle,
                "last_error": persisted_runtime.get(
                    "last_error",
                    runtime["last_error"],
                ),
                "state_changed_at": persisted_runtime.get("state_changed_at"),
                "finalizing_started_at": persisted_runtime.get(
                    "finalizing_started_at"
                ),
                "last_phase": persisted_runtime.get("last_phase"),
                "last_phase_completed": int(
                    persisted_runtime.get("last_phase_completed") or 0
                ),
                "last_phase_total": int(
                    persisted_runtime.get("last_phase_total") or 0
                ),
                "updated_at": persisted_runtime.get("updated_at"),
                "lease_expires_at": persisted_runtime.get("lease_expires_at"),
                "active_evaluation_count": (
                    int(persisted_runtime.get("active_evaluation_count") or 0)
                    if live_scan_lock_active
                    else 0
                ),
                "queued_evaluation_count": (
                    int(persisted_runtime.get("queued_evaluation_count") or 0)
                    if live_scan_lock_active
                    else 0
                ),
                "oldest_active_evaluation_started_at": (
                    persisted_runtime.get("oldest_active_evaluation_started_at")
                    if live_scan_lock_active
                    else None
                ),
                "execution_timeout_seconds": max(
                    60,
                    int(
                        persisted_runtime.get("execution_timeout_seconds")
                        or config.system.execution_timeout_seconds
                    ),
                ),
            }
        )
        persisted_progress_completed = persisted_runtime.get("progress_completed")
        persisted_progress_total = persisted_runtime.get("progress_total")
        if normalize_phase(persisted_runtime.get("current_phase")) == SCAN_PHASE:
            progress_completed = int(
                persisted_progress_completed
                if persisted_progress_completed is not None
                else completed_targets
            )
            progress_total = int(
                persisted_progress_total
                if persisted_progress_total is not None
                else total_targets
            )
            phase = SCAN_PHASE
            phase_completed = progress_completed
            phase_total = progress_total
        else:
            phase, phase_completed, phase_total = self._current_phase_progress(
                entries
            )
            progress_completed = int(
                persisted_progress_completed
                if persisted_progress_completed is not None
                else phase_completed
            )
            progress_total = int(
                persisted_progress_total
                if persisted_progress_total is not None
                else phase_total
            )
        runtime["current_phase"] = phase
        runtime["current_phase_completed_targets"] = phase_completed
        runtime["current_phase_total_targets"] = phase_total
        runtime["progress_completed"] = progress_completed
        runtime["progress_total"] = progress_total
        if live_scan_lock_active and runtime["current_target"] is None:
            active_entry = next(
                (
                    entry
                    for entry in entries
                    if str(entry.get("status")) == "running"
                ),
                None,
            ) or next(
                (
                    entry
                    for entry in entries
                    if min(
                        int(entry.get("attempts_completed") or 0),
                        int(entry.get("attempts_per_target") or 0),
                    )
                    < int(entry.get("attempts_per_target") or 0)
                ),
                None,
            )
            active_candidate_id = (
                str(active_entry.get("candidate_id"))
                if active_entry is not None
                else None
            )
            active_target = next(
                (
                    target
                    for target in enabled_targets
                    if target.candidate_id == active_candidate_id
                ),
                None,
            )
            runtime["current_target"] = (
                active_target.display_label
                if active_target is not None
                else (
                    str(active_entry.get("label"))
                    if active_entry is not None
                    else None
                )
            )
        return runtime

    def _progress_percent(self) -> int:
        total = int(self.runtime_state["total_targets"] or 0)
        completed = int(self.runtime_state["completed_targets"] or 0)
        if total <= 0:
            return 0
        return round(completed * 100 / total)

    def _planned_attempts_by_candidate(
        self,
        raw_counts: object,
        enabled_targets: list[ResolvedScanTarget],
    ) -> dict[str, int]:
        payload = dict(raw_counts or {})
        candidate_ids_by_label = self.target_resolver.candidate_ids_by_label(
            enabled_targets
        )
        counts: dict[str, int] = {}
        for target in enabled_targets:
            value = payload.get(target.candidate_id)
            if value is None:
                value = payload.get(target.label)
            counts[target.candidate_id] = int(value or 0)
        for key, value in payload.items():
            if key in counts:
                counts[str(key)] = int(value)
                continue
            resolved = self.target_resolver.candidate_id_from_label(
                str(key),
                candidate_ids_by_label,
            )
            if resolved is not None:
                counts[resolved] = int(value)
        return counts

    @staticmethod
    def _resumable_repair_candidate_ids(
        active_run: dict[str, object],
    ) -> list[str]:
        candidate_ids = active_run.get("repair_candidate_ids")
        if isinstance(candidate_ids, list):
            return [str(item) for item in candidate_ids]
        candidate_id = active_run.get("repair_candidate_id")
        return [str(candidate_id)] if candidate_id else []

    def _run_progress_is_stale(
        self,
        active_run: dict[str, object] | None,
        history: list[ScanResult],
    ) -> bool:
        if not active_run:
            return False
        runtime = active_run.get("runtime")
        if isinstance(runtime, dict):
            lease_expires_at = _parse_iso_timestamp(runtime.get("lease_expires_at"))
            if lease_expires_at is not None:
                return lease_expires_at < self.clock()
        run_id = active_run.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            return False

        latest_activity = None
        for item in history:
            if item.run_id != run_id:
                continue
            candidate = _parse_iso_timestamp(item.started_at)
            if candidate is not None:
                latest_activity = max(latest_activity or candidate, candidate)
        if latest_activity is None:
            run_metadata = active_run.get("run_metadata")
            if isinstance(run_metadata, dict):
                latest_activity = _parse_iso_timestamp(
                    run_metadata.get("started_at")
                )
        if latest_activity is None:
            return False
        return (self.clock() - latest_activity) > self.stale_seconds

    @staticmethod
    def _current_phase_progress(
        entries: list[dict[str, object]],
    ) -> tuple[str | None, int, int]:
        if not entries:
            return None, 0, 0

        active_entry = next(
            (
                entry
                for entry in entries
                if str(entry.get("status")) == "running"
            ),
            None,
        )
        if active_entry is None:
            active_entry = next(
                (
                    entry
                    for entry in entries
                    if min(
                        int(entry.get("attempts_completed") or 0),
                        int(entry.get("attempts_per_target") or 0),
                    )
                    < int(entry.get("attempts_per_target") or 0)
                ),
                None,
            )
        if active_entry is None:
            return None, 0, 0

        phase = normalize_phase(active_entry.get("phase") or SCAN_PHASE)
        phase_entries = [
            entry
            for entry in entries
            if normalize_phase(entry.get("phase") or SCAN_PHASE) == phase
        ]
        completed = sum(
            min(
                int(entry.get("attempts_completed") or 0),
                int(entry.get("attempts_per_target") or 0),
            )
            for entry in phase_entries
        )
        total = sum(
            int(entry.get("attempts_per_target") or 0) for entry in phase_entries
        )
        return phase, completed, total
