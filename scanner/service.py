from __future__ import annotations

import json
import os
import random
from datetime import datetime, timedelta
from pathlib import Path
import sys
import time
from typing import Callable

from .active_run_store import ActiveRunStore
from .codex_current_model import detect_codex_current_model
from .comparison_groups import ComparisonGroupProjector
from .config_store import ConfigStore
from .current_model_context import (
    ActiveSessionDetector,
    CurrentModelContextQuery,
    CurrentModelDetector,
)
from .execution import (
    ExecutionEngine,
    FinalizationCoordinator,
    RunLifecycleCoordinator,
    RunStateMachine,
)
from .history_store import HistoryStore
from .execution_job_planner import ExecutionJobPlanner
from .legacy_scan_compat import (
    ACTIVE_SCAN_LIFECYCLE,
    SCAN_PHASE,
    is_active_lifecycle,
    metadata_question_count,
    normalize_lifecycle,
    normalize_phase,
)
from .models import AppConfig, ResolvedScanTarget, ScanPlan, ScanResult
from .model_sessions import detect_external_model_sessions
from .monitor_state_projection import MonitorStateProjector
from .process_lock import (
    read_scan_lock_payload as _read_scan_lock_payload,
    scan_lock_is_active as _shared_scan_lock_is_active,
)
from .question_bank import (
    QuestionBank,
    QuestionSpec,
)
from .repair_execution_application import (
    RepairExecutionApplicationService,
    RepairExecutionPorts,
    validate_batch_repair_plan,
    validate_candidate_repair_plan,
)
from .repair_planner import RepairPlan, RepairPlanner
from .run_journal import RunJournalStore
from .rules import (
    evaluate_result,
    is_grok_outbound_replay,
    is_transient_execution_error,
)
from .runner import run_target
from .runtime_snapshot_projection import RuntimeSnapshotProjector
from .scan_execution_application import (
    ScanExecutionApplicationService,
    ScanExecutionPorts,
)
from .scan_planner import ScanPlanner
from .scan_target_resolver import ScanTargetResolver

Runner = Callable[..., ScanResult]
ProgressCallback = Callable[[dict[str, object]], None]
SCAN_LOCK_STALE_SECONDS = 420
TRANSIENT_RETRY_BASE_DELAY_SECONDS = 15.0


def _log(message: str) -> None:
    if os.environ.get("MODELDIAL_DEBUG_LOG") != "1":
        return
    try:
        print(f"[service] {message}", file=sys.stderr, flush=True)
    except (BrokenPipeError, OSError, ValueError):
        pass


def _process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _scan_lock_is_active(lock_path: Path) -> bool:
    return _shared_scan_lock_is_active(
        lock_path,
        stale_seconds=SCAN_LOCK_STALE_SECONDS,
    )


class MonitorService:
    def __init__(
        self,
        config_store: ConfigStore | None = None,
        history_store: HistoryStore | None = None,
        active_run_store: ActiveRunStore | None = None,
        runner: Runner = run_target,
        current_model_detector: CurrentModelDetector = detect_codex_current_model,
        active_session_detector: ActiveSessionDetector = detect_external_model_sessions,
        run_journal_store: RunJournalStore | None = None,
    ) -> None:
        configured_root = os.environ.get("MODELDIAL_BACKEND_ROOT", "").strip()
        root = (
            Path(configured_root).expanduser()
            if configured_root
            else Path(__file__).resolve().parent.parent
        )
        artifacts_dir = root / "artifacts"
        self.config_store = config_store or ConfigStore(artifacts_dir / "config.json")
        self.history_store = history_store or HistoryStore(artifacts_dir / "history.jsonl")
        self.active_run_store = active_run_store or ActiveRunStore(artifacts_dir / "active_run.json")
        self.run_journal_store = run_journal_store or RunJournalStore(
            self.history_store.path.parent / "runs"
        )
        self.runner = runner
        self.current_model_detector = current_model_detector
        self.active_session_detector = active_session_detector
        self.current_model_context_query = CurrentModelContextQuery(
            current_model_detector=lambda: self.current_model_detector(),
            active_session_detector=lambda: self.active_session_detector(),
        )
        self.question_bank = QuestionBank(root / "questions")
        self.scan_target_resolver = ScanTargetResolver()
        self.comparison_group_projector = ComparisonGroupProjector(
            self.scan_target_resolver
        )
        self.scan_planner = ScanPlanner(
            config_store=self.config_store,
            history_store=self.history_store,
            active_run_store=self.active_run_store,
            question_bank=self.question_bank,
            target_resolver=self.scan_target_resolver,
            comparison_group_projector=self.comparison_group_projector,
        )
        self.repair_planner = RepairPlanner(
            config_store=self.config_store,
            history_store=self.history_store,
            active_run_store=self.active_run_store,
            question_bank=self.question_bank,
            target_resolver=self.scan_target_resolver,
            comparison_group_projector=self.comparison_group_projector,
        )
        self.execution_job_planner = ExecutionJobPlanner()
        self.last_control_action: str | None = None
        self._progress_state_config: AppConfig | None = None
        self._progress_history_count = 0
        initialized_at = self._timestamp()
        self.runtime_state = {
            "is_running": False,
            "last_run_count": 0,
            "last_error": None,
            "last_run_mode": "live",
            "completed_targets": 0,
            "total_targets": 0,
            "current_target": None,
            "run_entries": [],
            "current_run_id": None,
            "lifecycle_state": "idle",
            "state_changed_at": initialized_at,
            "finalizing_started_at": None,
            "last_phase": None,
            "last_phase_completed": 0,
            "last_phase_total": 0,
            "updated_at": initialized_at,
            "lease_expires_at": None,
            "current_phase": None,
            "progress_completed": 0,
            "progress_total": 0,
            "active_evaluation_count": 0,
            "queued_evaluation_count": 0,
            "oldest_active_evaluation_started_at": None,
        }
        self.runtime_snapshot_projector = RuntimeSnapshotProjector(
            runtime_state=self.runtime_state,
            history_store=self.history_store,
            active_run_store=self.active_run_store,
            target_resolver=self.scan_target_resolver,
            comparison_group_projector=self.comparison_group_projector,
            scan_lock_is_active=_scan_lock_is_active,
            stale_seconds=SCAN_LOCK_STALE_SECONDS,
            clock=time.time,
        )
        self.monitor_state_projector = MonitorStateProjector(
            config_store=self.config_store,
            history_store=self.history_store,
            active_run_store=self.active_run_store,
            question_bank=self.question_bank,
            current_model_context_query=self.current_model_context_query,
            runtime_snapshot_projector=self.runtime_snapshot_projector,
            scan_planner=self.scan_planner,
            comparison_group_projector=self.comparison_group_projector,
        )
        self.run_state_machine = RunStateMachine(
            self.runtime_state,
            timestamp=self._timestamp,
        )
        self.run_lifecycle = RunLifecycleCoordinator(
            state_machine=self.run_state_machine,
            history_store=self.history_store,
            active_run_store=self.active_run_store,
            journal_store=self.run_journal_store,
            timestamp=self._timestamp,
        )
        self.finalization_coordinator = FinalizationCoordinator(
            state_machine=self.run_state_machine,
            lifecycle=self.run_lifecycle,
            active_run_store=self.active_run_store,
            journal_store=self.run_journal_store,
            timestamp=self._timestamp,
            scan_lock_is_active=_scan_lock_is_active,
            read_scan_lock_payload=_read_scan_lock_payload,
            current_process_id=os.getpid,
        )
        self.execution_engine: ExecutionEngine[ScanResult] = ExecutionEngine()
        self.scan_execution_application = ScanExecutionApplicationService(
            runtime_state=self.runtime_state,
            state_machine=self.run_state_machine,
            lifecycle=self.run_lifecycle,
            engine=self.execution_engine,
            history_store=self.history_store,
            active_run_store=self.active_run_store,
            job_planner=self.execution_job_planner,
            ports=ScanExecutionPorts(
                build_run_entries=self._build_run_entries,
                persist_active_run=self._persist_active_run,
                journal_event=self._journal_event,
                run_target=self._run_target_with_rules,
                emit_progress_event=self._emit_progress_event,
                reset_progress_state_cache=self._reset_progress_state_cache,
                set_last_control_action=lambda action: setattr(
                    self,
                    "last_control_action",
                    action,
                ),
                lease_duration_seconds=self._lease_duration_seconds,
                timestamp=self._timestamp,
                log=_log,
            ),
        )
        self.repair_execution_application = RepairExecutionApplicationService(
            runtime_state=self.runtime_state,
            state_machine=self.run_state_machine,
            lifecycle=self.run_lifecycle,
            engine=self.execution_engine,
            history_store=self.history_store,
            active_run_store=self.active_run_store,
            job_planner=self.execution_job_planner,
            target_resolver=self.scan_target_resolver,
            ports=RepairExecutionPorts(
                build_run_entries=self._build_run_entries,
                persist_active_run=self._persist_active_run,
                journal_event=self._journal_event,
                run_target=self._run_target_with_rules,
                emit_progress_event=self._emit_progress_event,
                reset_progress_state_cache=self._reset_progress_state_cache,
                set_last_control_action=lambda action: setattr(
                    self,
                    "last_control_action",
                    action,
                ),
                lease_duration_seconds=self._lease_duration_seconds,
                timestamp=self._timestamp,
            ),
        )

    def recover_orphaned_finalizing_run(
        self,
        *,
        exclusive_lock_held: bool,
    ) -> dict[str, object]:
        result = self.finalization_coordinator.recover_orphaned_finalizing_run(
            exclusive_lock_held=exclusive_lock_held,
        )
        if result.get("recovered"):
            _log(
                "recover orphaned finalizing run "
                f"run_id={result.get('run_id')}"
            )
        return result

    def record_finalization_projection_failure(
        self,
        error_message: str,
        *,
        exclusive_lock_held: bool,
    ) -> dict[str, object]:
        return self._record_finalization_failure(
            error_message,
            exclusive_lock_held=exclusive_lock_held,
            journal_event_type="run.projection_failed",
        )

    def record_finalization_commit_failure(
        self,
        error_message: str,
        *,
        exclusive_lock_held: bool,
    ) -> dict[str, object]:
        return self._record_finalization_failure(
            error_message,
            exclusive_lock_held=exclusive_lock_held,
            journal_event_type="run.finalization_commit_failed",
        )

    def _record_finalization_failure(
        self,
        error_message: str,
        *,
        exclusive_lock_held: bool,
        journal_event_type: str,
    ) -> dict[str, object]:
        message, persistence_errors = self.finalization_coordinator.record_failure(
            error_message,
            exclusive_lock_held=exclusive_lock_held,
            journal_event_type=journal_event_type,
        )
        state = self.build_runtime_event(last_error=message)
        if persistence_errors:
            state["persistence_errors"] = persistence_errors
        return state

    def complete_finalizing_snapshot(
        self,
        projected_state: dict[str, object],
        *,
        exclusive_lock_held: bool,
    ) -> dict[str, object]:
        terminal_state = self.finalization_coordinator.complete_snapshot(
            projected_state,
            exclusive_lock_held=exclusive_lock_held,
        )
        self._progress_state_config = None
        self._progress_history_count = 0
        return terminal_state

    def heartbeat_active_run_lease(self) -> dict[str, object] | None:
        return self.active_run_store.refresh_runtime_lease()

    def build_runtime_event(
        self,
        *,
        last_error: str | None = None,
    ) -> dict[str, object]:
        config = self._progress_state_config or self.load_config()
        runtime = self._snapshot_runtime(
            config,
            [],
            self.active_run_store.load(),
            history_count=self._progress_history_count,
        )
        state: dict[str, object] = {
            "schema_version": 1,
            "runtime": runtime,
        }
        if last_error is None:
            return state
        state["runtime"] = {
            **runtime,
            "last_error": last_error,
            "updated_at": self._timestamp(),
        }
        return state

    def scan_terminal_failure_state(self) -> dict[str, object] | None:
        active_run = self.active_run_store.load()
        if not isinstance(active_run, dict):
            return None
        run_metadata = active_run.get("run_metadata")
        persisted_runtime = active_run.get("runtime")
        if (
            not isinstance(run_metadata, dict)
            or str(run_metadata.get("status") or "") != "failed"
            or not isinstance(persisted_runtime, dict)
            or normalize_lifecycle(persisted_runtime.get("lifecycle_state"))
            != "failed"
        ):
            return None
        failure_message = str(
            persisted_runtime.get("last_error") or "scan execution failed"
        )
        return {
            "failure_message": failure_message,
            "state": self.build_runtime_event(last_error=failure_message),
        }

    def load_config(self) -> AppConfig:
        return self.config_store.load()

    def build_refresh_state(self) -> dict[str, object]:
        return self.monitor_state_projector.build_refresh_state()

    def build_state(self) -> dict[str, object]:
        return self.monitor_state_projector.build_state()

    def plan_scan(
        self,
        *,
        force_restart: bool = False,
        requested_candidate_ids: list[str] | None = None,
        selection_mode: str = "regular",
        custom_round_mode: str = "new_round",
        evaluation_profile_id: str | None = None,
        upgrade_from_run_id: str | None = None,
        retry_failed_results: bool = False,
    ) -> ScanPlan:
        retry_options = (
            {"retry_failed_results": True} if retry_failed_results else {}
        )
        return self.scan_planner.plan(
            force_restart=force_restart,
            requested_candidate_ids=requested_candidate_ids,
            selection_mode=selection_mode,
            custom_round_mode=custom_round_mode,
            evaluation_profile_id=evaluation_profile_id,
            upgrade_from_run_id=upgrade_from_run_id,
            **retry_options,
        )

    def run_enabled_targets(
        self,
        *,
        scan_plan: ScanPlan | None = None,
        force_restart: bool = False,
        retain_finalizing_state: bool = False,
        progress_callback: ProgressCallback | None = None,
        requested_candidate_ids: list[str] | None = None,
        selection_mode: str = "regular",
        custom_round_mode: str = "new_round",
        evaluation_profile_id: str | None = None,
        upgrade_from_run_id: str | None = None,
        retry_failed_results: bool = False,
    ) -> list[ScanResult]:
        self.last_control_action = None
        if scan_plan is None:
            retry_options = (
                {"retry_failed_results": True} if retry_failed_results else {}
            )
            scan_plan = self.plan_scan(
                force_restart=force_restart,
                requested_candidate_ids=requested_candidate_ids,
                selection_mode=selection_mode,
                custom_round_mode=custom_round_mode,
                evaluation_profile_id=evaluation_profile_id,
                upgrade_from_run_id=upgrade_from_run_id,
                **retry_options,
            )
        if scan_plan.force_restart:
            self.active_run_store.clear()
        return self.scan_execution_application.execute(
            scan_plan=scan_plan,
            retain_finalizing_state=retain_finalizing_state,
            progress_callback=progress_callback,
        )

    def repair_failed_candidate(
        self,
        *,
        run_id: str,
        candidate_id: str,
        question_id: str | None = None,
        progress_callback: ProgressCallback | None = None,
        repair_plan: RepairPlan | None = None,
        retain_finalizing_state: bool = False,
    ) -> list[ScanResult]:
        if self.runtime_state["is_running"]:
            raise ValueError("当前已有扫描正在运行")
        plan = repair_plan or self.repair_planner.plan_candidate(
            run_id=run_id,
            candidate_id=candidate_id,
            question_id=question_id,
        )
        validate_candidate_repair_plan(
            plan,
            run_id=run_id,
            candidate_id=candidate_id,
            question_id=question_id,
        )
        return self.repair_execution_application.execute_candidate(
            plan=plan,
            progress_callback=progress_callback,
            retain_finalizing_state=retain_finalizing_state,
        )

    def repair_timed_out_questions(
        self,
        *,
        run_id: str,
        candidate_ids: list[str] | None = None,
        progress_callback: ProgressCallback | None = None,
        repair_plan: RepairPlan | None = None,
        retain_finalizing_state: bool = False,
    ) -> list[ScanResult]:
        return self._repair_questions_batch(
            run_id=run_id,
            candidate_ids=candidate_ids,
            progress_callback=progress_callback,
            timeouts_only=True,
            repair_plan=repair_plan,
            retain_finalizing_state=retain_finalizing_state,
        )

    def repair_failed_questions(
        self,
        *,
        run_id: str,
        candidate_ids: list[str] | None = None,
        progress_callback: ProgressCallback | None = None,
        repair_plan: RepairPlan | None = None,
        retain_finalizing_state: bool = False,
    ) -> list[ScanResult]:
        return self._repair_questions_batch(
            run_id=run_id,
            candidate_ids=candidate_ids,
            progress_callback=progress_callback,
            timeouts_only=False,
            repair_plan=repair_plan,
            retain_finalizing_state=retain_finalizing_state,
        )

    def _repair_questions_batch(
        self,
        *,
        run_id: str,
        candidate_ids: list[str] | None,
        progress_callback: ProgressCallback | None,
        timeouts_only: bool,
        repair_plan: RepairPlan | None,
        retain_finalizing_state: bool,
    ) -> list[ScanResult]:
        if self.runtime_state["is_running"]:
            raise ValueError("当前已有扫描正在运行")
        expected_operation_kind = (
            "timeout_repair" if timeouts_only else "failed_repair"
        )
        plan = repair_plan or (
            self.repair_planner.plan_timeout_batch(
                run_id=run_id,
                candidate_ids=candidate_ids,
            )
            if timeouts_only
            else self.repair_planner.plan_failed_batch(
                run_id=run_id,
                candidate_ids=candidate_ids,
            )
        )
        validate_batch_repair_plan(
            plan,
            run_id=run_id,
            candidate_ids=candidate_ids,
            expected_operation_kind=expected_operation_kind,
        )
        return self.repair_execution_application.execute_batch(
            plan=plan,
            progress_callback=progress_callback,
            retain_finalizing_state=retain_finalizing_state,
        )

    def _emit_progress_event(
        self,
        progress_callback: ProgressCallback | None,
        payload: dict[str, object],
    ) -> None:
        event = dict(payload)
        event["total_targets"] = int(self.runtime_state["total_targets"] or 0)
        event["completed_targets"] = int(self.runtime_state["completed_targets"] or 0)
        event_type = str(event.get("type") or "progress")
        journal_type = {
            "target.started": "evaluation.started",
            "scan.progress": "evaluation.finished",
            "repair.question.started": "evaluation.retry_started",
            "repair.question.finished": "evaluation.retry_finished",
        }.get(event_type)
        if journal_type is not None:
            journal_data = {
                key: value
                for key, value in event.items()
                if key != "type"
            }
            self._journal_event(journal_type, journal_data)
        if progress_callback is None:
            return
        event["state"] = self._build_progress_state()
        progress_callback(event)

    def _journal_event(
        self,
        event_type: str,
        data: dict[str, object],
        *,
        run_id: str | None = None,
    ) -> None:
        active_run_id = str(run_id or self.runtime_state.get("current_run_id") or "")
        self.run_lifecycle.journal_event(
            active_run_id,
            event_type,
            data,
        )

    def _save_journal_summary(
        self,
        run_id: str,
        run_metadata: dict[str, object],
    ) -> None:
        self.run_lifecycle.save_summary(run_id, run_metadata)

    def _build_progress_state(self) -> dict[str, object]:
        config = self._progress_state_config or self.load_config()
        runtime = self._snapshot_runtime(
            config,
            [],
            None,
            history_count=self._progress_history_count,
        )
        return {
            "schema_version": 1,
            "runtime": runtime,
        }

    def _reset_progress_state_cache(
        self,
        config: AppConfig | None = None,
        *,
        history_count: int = 0,
    ) -> None:
        self._progress_state_config = config
        self._progress_history_count = max(0, int(history_count))

    def _run_target_with_rules(
        self,
        target: ResolvedScanTarget,
        question: QuestionSpec,
        config: AppConfig,
        *,
        run_id: str,
        phase: str,
        attempt_index: int,
    ) -> ScanResult:
        attempt = 0
        while True:
            result = self.runner(
                target,
                question,
                config.system.use_mock_results,
                run_id=run_id,
                phase=phase,
                attempt_index=attempt_index,
                execution_timeout_seconds=config.system.execution_timeout_seconds,
            )
            result.run_id = run_id
            result.phase = phase
            result.candidate_id = target.candidate_id
            result.question_id = question.id
            result.question_title = question.title
            result.capability_id = question.capability_id
            result.capability_label = question.capability_label
            result.detail_label = question.detail_label
            result.grader_kind = question.grader.kind
            result.attempt_index = attempt_index
            result.reasoning_tokens_supported = target.reasoning_tokens_supported
            evaluation = evaluate_result(
                result,
                config.rules,
                hard_timeout_retry_count=config.system.timeout_retry_count,
            )
            result.flags = evaluation.flags
            result.retry_index = attempt
            transient_error = is_transient_execution_error(result)
            max_retries = evaluation.max_retries
            should_retry = evaluation.should_retry
            if transient_error:
                max_retries = max(
                    max_retries,
                    max(0, config.system.timeout_retry_count),
                )
                should_retry = max_retries > 0
            if is_grok_outbound_replay(result):
                should_retry = False
            if should_retry and attempt < max_retries:
                if self.last_control_action in {"pause", "stop"} or (
                    self.active_run_store.peek_control() in {"pause", "stop"}
                ):
                    return result
                if transient_error and not config.system.use_mock_results:
                    retry_delay = min(
                        60.0,
                        TRANSIENT_RETRY_BASE_DELAY_SECONDS * (2 ** attempt),
                    )
                    time.sleep(retry_delay * random.uniform(0.8, 1.2))
                attempt += 1
                continue
            if attempt > 0 and evaluation.final_status == "pass":
                result.final_status = "recovered"
            else:
                result.final_status = evaluation.final_status
            return result

    def _timestamp(self) -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")

    def _lease_duration_seconds(self, config: AppConfig) -> int:
        return max(
            SCAN_LOCK_STALE_SECONDS,
            int(config.system.execution_timeout_seconds) + 120,
        )

    def _candidate_ids_by_label(
        self,
        enabled_targets: list[ResolvedScanTarget],
    ) -> dict[str, list[str]]:
        return self.scan_target_resolver.candidate_ids_by_label(enabled_targets)

    def _entry_candidate_id(
        self,
        entry: dict[str, object],
        candidate_ids_by_label: dict[str, list[str]],
    ) -> str | None:
        return self.scan_target_resolver.entry_candidate_id(
            entry,
            candidate_ids_by_label,
        )

    def _build_run_entries(
        self,
        *,
        enabled_targets: list[ResolvedScanTarget],
        attempts_per_target: int,
        completed_by_candidate: dict[str, int],
        active_run: dict[str, object] | None,
    ) -> list[dict[str, object]]:
        candidate_ids_by_label = self._candidate_ids_by_label(enabled_targets)
        stored_entries = {
            candidate_id: item
            for item in (active_run or {}).get("entries", [])
            if isinstance(item, dict)
            for candidate_id in [self._entry_candidate_id(item, candidate_ids_by_label)]
            if candidate_id is not None
        }
        entries: list[dict[str, object]] = []
        for target in enabled_targets:
            stored = stored_entries.get(target.candidate_id, {})
            completed = completed_by_candidate.get(target.candidate_id, 0)
            entries.append(
                {
                    "candidate_id": target.candidate_id,
                    "model": target.model,
                    "effort": target.effort,
                    "label": target.label,
                    "status": "done" if completed >= attempts_per_target else "pending",
                    "final_status": stored.get("final_status"),
                    "reasoning_tokens": stored.get("reasoning_tokens"),
                    "attempts_completed": completed,
                    "attempts_per_target": attempts_per_target,
                    "phase": SCAN_PHASE,
                    "flags": list(stored.get("flags", [])),
                    "error_message": stored.get("error_message"),
                }
            )
        return entries

    def _persist_active_run(
        self,
        *,
        run_id: str,
        enabled_targets: list[ResolvedScanTarget],
        attempts_per_target: int,
        run_entries: list[dict[str, object]],
        run_metadata: dict[str, object] | None = None,
        config: AppConfig | None = None,
    ) -> None:
        metadata = dict(run_metadata or {})
        if not metadata:
            existing = self.active_run_store.load() or {}
            existing_metadata = existing.get("run_metadata")
            metadata = dict(existing_metadata) if isinstance(existing_metadata, dict) else {}
        active_config = config or self.load_config()
        lease_duration_seconds = self._lease_duration_seconds(active_config)
        updated_at = self._timestamp()
        lifecycle_state = normalize_lifecycle(
            self.runtime_state.get("lifecycle_state") or ACTIVE_SCAN_LIFECYCLE
        )
        self.runtime_state["updated_at"] = updated_at
        if is_active_lifecycle(lifecycle_state):
            self.runtime_state["lease_expires_at"] = (
                datetime.fromisoformat(updated_at)
                + timedelta(seconds=lease_duration_seconds)
            ).isoformat(timespec="seconds")
        runtime_payload = {
            "lifecycle_state": lifecycle_state,
            "last_error": self.runtime_state.get("last_error"),
            "state_changed_at": self.runtime_state.get("state_changed_at"),
            "finalizing_started_at": self.runtime_state.get("finalizing_started_at"),
            "last_phase": self.runtime_state.get("last_phase"),
            "last_phase_completed": int(
                self.runtime_state.get("last_phase_completed") or 0
            ),
            "last_phase_total": int(self.runtime_state.get("last_phase_total") or 0),
            "updated_at": updated_at,
            "lease_expires_at": self.runtime_state.get("lease_expires_at"),
            "lease_duration_seconds": lease_duration_seconds,
            "progress_completed": int(self.runtime_state.get("progress_completed") or 0),
            "progress_total": int(self.runtime_state.get("progress_total") or 0),
            "progress_unit": "evaluationUnit",
            "current_phase": self.runtime_state.get("current_phase"),
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
                int(active_config.system.execution_timeout_seconds),
            ),
        }
        payload = {
                "run_id": run_id,
                "run_metadata": metadata,
                "runtime": runtime_payload,
                "planned_attempts_by_candidate": {
                    target.candidate_id: attempts_per_target
                    for target in enabled_targets
                },
                "planned_attempts": {
                    target.label: attempts_per_target
                    for target in enabled_targets
                },
                "entries": [
                    {
                        "candidate_id": entry["candidate_id"],
                        "model": entry["model"],
                        "effort": entry["effort"],
                        "label": entry["label"],
                        "status": entry["status"],
                        "final_status": entry["final_status"],
                        "reasoning_tokens": entry["reasoning_tokens"],
                        "attempts_completed": entry["attempts_completed"],
                        "attempts_per_target": entry["attempts_per_target"],
                        "phase": entry["phase"],
                        "flags": list(entry["flags"]),
                        "error_message": entry["error_message"],
                    }
                    for entry in run_entries
                ],
            }
        def replace_checkpoint(
            current: dict[str, object],
        ) -> dict[str, object]:
            if str(current.get("run_id") or "") != run_id:
                return payload
            maintenance = current.get("maintenance")
            if isinstance(maintenance, dict):
                return {**payload, "maintenance": dict(maintenance)}
            return payload

        self.active_run_store.mutate(replace_checkpoint)
        self._save_journal_summary(run_id, metadata)

    def _snapshot_runtime(
        self,
        config: AppConfig,
        history: list[ScanResult],
        active_run: dict[str, object] | None,
        *,
        history_count: int | None = None,
    ) -> dict[str, object]:
        return self.runtime_snapshot_projector.project(config, history, active_run, history_count=history_count)
