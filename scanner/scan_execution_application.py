from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .active_run_store import ActiveRunStore
from .execution import (
    ExecutionEngine,
    ExecutionJob,
    RunLifecycleCoordinator,
    RunStateMachine,
)
from .execution_job_planner import ExecutionJobPlanner
from .history_store import HistoryStore
from .job_reducers import ScanJobReducer
from .legacy_scan_compat import ACTIVE_SCAN_LIFECYCLE
from .models import AppConfig, ScanPlan, ScanResult
from .scan_execution import ScanExecutionCommand
from .scan_outcomes import ScanOutcomeCoordinator


ProgressCallback = Callable[[dict[str, object]], None]
HARD_ERROR_CIRCUIT_THRESHOLD = 3


@dataclass(frozen=True)
class ScanExecutionPorts:
    build_run_entries: Callable[..., list[dict[str, object]]]
    persist_active_run: Callable[..., None]
    journal_event: Callable[..., None]
    run_target: Callable[..., ScanResult]
    emit_progress_event: Callable[..., None]
    reset_progress_state_cache: Callable[..., None]
    set_last_control_action: Callable[[str | None], None]
    lease_duration_seconds: Callable[[AppConfig], int]
    timestamp: Callable[[], str]
    log: Callable[[str], None]


def execution_policy_snapshot(config: AppConfig) -> dict[str, object]:
    retry_counts = [
        max(0, rule.max_retries)
        for rule in config.rules.values()
        if rule.enabled and rule.action == "retry"
    ]
    retry_counts.append(max(0, config.system.timeout_retry_count))
    payload: dict[str, object] = {
        "schema_version": 1,
        "mode": "app_rules_v1",
        "execution_timeout_seconds": max(
            60, config.system.execution_timeout_seconds
        ),
        "timeout_retry_count": max(0, config.system.timeout_retry_count),
        "max_attempts_per_question": 1 + max(retry_counts, default=0),
        "selective_score_retry": bool(
            (wrong_answer := config.rules.get("wrong_answer"))
            and wrong_answer.enabled
            and wrong_answer.action == "retry"
        ),
        "rules": {
            name: rule.to_dict()
            for name, rule in sorted(config.rules.items())
        },
    }
    if config.system.max_concurrent_targets_by_connection:
        payload.update(
            {
                "max_concurrent_targets": max(
                    1, int(config.system.max_concurrent_targets)
                ),
                "max_concurrent_targets_by_connection": dict(
                    sorted(
                        config.system.max_concurrent_targets_by_connection.items()
                    )
                ),
            }
        )
    return payload


class ScanExecutionApplicationService:
    """Application orchestration for one prepared scan plan."""

    def __init__(
        self,
        *,
        runtime_state: dict[str, object],
        state_machine: RunStateMachine,
        lifecycle: RunLifecycleCoordinator,
        engine: ExecutionEngine[ScanResult],
        history_store: HistoryStore,
        active_run_store: ActiveRunStore,
        job_planner: ExecutionJobPlanner,
        ports: ScanExecutionPorts,
    ) -> None:
        self.runtime_state = runtime_state
        self.state_machine = state_machine
        self.lifecycle = lifecycle
        self.engine = engine
        self.history_store = history_store
        self.active_run_store = active_run_store
        self.job_planner = job_planner
        self.ports = ports

    def execute(
        self,
        *,
        scan_plan: ScanPlan,
        retain_finalizing_state: bool,
        progress_callback: ProgressCallback | None,
    ) -> list[ScanResult]:
        config = scan_plan.config
        history = list(scan_plan.history)
        regular_candidate_ids = list(scan_plan.regular_candidate_ids)
        comparison_targets = list(scan_plan.comparison_targets)
        enabled_targets = list(scan_plan.enabled_targets)
        evaluation_profile = scan_plan.evaluation_profile
        enabled_questions = list(scan_plan.enabled_questions)
        question_ids = list(scan_plan.question_ids)
        attempts_per_target = scan_plan.attempts_per_target
        evaluation_total = scan_plan.total_targets
        resume = scan_plan.resume
        run_id = scan_plan.run_id
        run_metadata = scan_plan.run_metadata
        selection_mode = scan_plan.execution_selection_mode
        effective_requested_candidate_ids = list(
            scan_plan.effective_requested_candidate_ids
        )

        self.ports.log(
            f"run_enabled_targets start run_id={run_id} total={evaluation_total}"
        )
        self.ports.reset_progress_state_cache(
            config,
            history_count=len(history),
        )
        initial_completed = int(resume["completed_count"]) if resume else 0
        run_entries = self.ports.build_run_entries(
            enabled_targets=comparison_targets,
            attempts_per_target=attempts_per_target,
            completed_by_candidate=(
                resume["completed_by_candidate"] if resume else {}
            ),
            active_run=resume["active_run"] if resume else None,
        )

        def persist_scan_control(action: str) -> None:
            self.ports.set_last_control_action(action)
            scan_outcomes.finish_controlled(
                action=action,
                progress_completed=int(
                    self.runtime_state["progress_completed"] or 0
                ),
                progress_total=int(
                    self.runtime_state["progress_total"] or 0
                ),
            )

        scan_execution = ScanExecutionCommand(
            run_id=run_id,
            total=evaluation_total,
            max_workers=max(1, int(config.system.max_concurrent_targets)),
            initial_completed=initial_completed,
            circuit_breaker_threshold=HARD_ERROR_CIRCUIT_THRESHOLD,
            state_machine=self.state_machine,
            lifecycle=self.lifecycle,
            engine=self.engine,
            history_store=self.history_store,
            active_run_store=self.active_run_store,
            on_control=persist_scan_control,
        )
        scan_outcomes = ScanOutcomeCoordinator(
            session=scan_execution.session,
            base_run_metadata=run_metadata,
            evaluation_result_level=evaluation_profile.result_level,
            selection_mode=selection_mode,
            effective_candidate_ids=effective_requested_candidate_ids,
            regular_candidate_ids=regular_candidate_ids,
            timestamp=self.ports.timestamp,
        )
        results = scan_execution.results
        try:
            scan_execution.begin(
                run_entries=run_entries,
                last_run_mode=(
                    "mock" if config.system.use_mock_results else "live"
                ),
                lease_duration_seconds=self.ports.lease_duration_seconds(config),
            )
            self.ports.persist_active_run(
                run_id=run_id,
                enabled_targets=comparison_targets,
                attempts_per_target=attempts_per_target,
                run_entries=self.runtime_state["run_entries"],
                run_metadata=run_metadata,
                config=config,
            )
            self.ports.journal_event(
                "run.resumed"
                if resume and resume.get("run_id")
                else "run.started",
                {
                    "evaluation_profile_id": evaluation_profile.id,
                    "candidate_ids": list(effective_requested_candidate_ids),
                    "question_ids": list(question_ids),
                    "execution_policy": execution_policy_snapshot(config),
                },
                run_id=run_id,
            )

            completed_steps = resume["completed_steps"] if resume else set()
            result_buckets = resume["buckets"] if resume else {}
            scan_reducer = ScanJobReducer(
                runtime_state=self.runtime_state,
                run_entries=self.runtime_state["run_entries"],
                candidate_ids=[target.candidate_id for target in enabled_targets],
                attempts_per_target=attempts_per_target,
                result_buckets=result_buckets,
                completed_steps=completed_steps,
                circuit_breaker_threshold=HARD_ERROR_CIRCUIT_THRESHOLD,
            )
        except Exception as exc:
            self.ports.log(f"run_enabled_targets exception={exc}")
            try:
                scan_outcomes.finish_exception(
                    error_message=str(exc),
                    result_count=len(results),
                )
            finally:
                scan_execution.settle()
                self.ports.reset_progress_state_cache()
            raise

        try:
            def persist_run_locked() -> None:
                if scan_execution.control_action == "stop":
                    return
                persisted_metadata = run_metadata
                if scan_execution.control_action == "pause":
                    persisted_metadata = dict(run_metadata)
                    persisted_metadata["status"] = "paused"
                    persisted_metadata["is_complete_regular_round"] = False
                    persisted_metadata["completed_at"] = None
                self.ports.persist_active_run(
                    run_id=run_id,
                    enabled_targets=comparison_targets,
                    attempts_per_target=attempts_per_target,
                    run_entries=self.runtime_state["run_entries"],
                    run_metadata=persisted_metadata,
                    config=config,
                )

            scan_jobs = self.job_planner.plan_scan_missing_steps(
                targets=enabled_targets,
                questions=enabled_questions,
                completed_steps=completed_steps,
            )

            def start_scan_job(job: ExecutionJob) -> None:
                scan_reducer.job_started(
                    candidate_id=job.candidate_id,
                    job_key=job.key,
                    started_at=self.ports.timestamp(),
                    current_target=(
                        f"{job.target.display_label} · 扫描 "
                        f"{job.attempt_index}/{attempts_per_target}"
                    ),
                )
                self.ports.log(
                    f"run_target start target={job.target.model}/{job.target.effort} "
                    f"question={job.question.id} phase={job.result_phase} "
                    f"attempt={job.attempt_index}"
                )

            def emit_scan_started(job: ExecutionJob) -> None:
                self.ports.emit_progress_event(
                    progress_callback,
                    {
                        "type": "target.started",
                        "label": job.target.label,
                        "candidate_id": job.candidate_id,
                        "question_id": job.question.id,
                        "phase": job.result_phase,
                        "attempt_index": job.attempt_index,
                    },
                )

            def run_scan_job(job: ExecutionJob) -> ScanResult:
                return self.ports.run_target(
                    job.target,
                    job.question,
                    config,
                    run_id=run_id,
                    phase=job.result_phase,
                    attempt_index=job.attempt_index,
                )

            def reduce_scan_result(job: ExecutionJob, result: ScanResult) -> None:
                scan_reducer.job_finished(
                    candidate_id=job.candidate_id,
                    job_key=job.key,
                    result=result,
                )
                self.ports.log(
                    f"run_target finish target={job.target.model}/{job.target.effort} "
                    f"question={job.question.id} ok={result.answer_ok} "
                    f"status={result.final_status} error={result.error_message!r}"
                )

            def emit_scan_finished(job: ExecutionJob, result: ScanResult) -> None:
                self.ports.emit_progress_event(
                    progress_callback,
                    {
                        "type": "scan.progress",
                        "label": job.target.label,
                        "candidate_id": job.candidate_id,
                        "question_id": job.question.id,
                        "phase": job.result_phase,
                        "attempt_index": job.attempt_index,
                        "final_status": result.final_status,
                        "error_message": result.error_message,
                        "elapsed_seconds": result.elapsed_seconds,
                    },
                )

            scan_execution.execute_jobs(
                scan_jobs,
                run_job=run_scan_job,
                persist_state=persist_run_locked,
                can_start=lambda _job: scan_reducer.can_start(),
                on_not_started=lambda job: scan_reducer.refresh_entry(
                    job.candidate_id
                ),
                on_started=start_scan_job,
                after_started=emit_scan_started,
                on_stopped=lambda job: scan_reducer.job_stopped(
                    candidate_id=job.candidate_id,
                    job_key=job.key,
                ),
                on_failed=lambda job, _error: scan_reducer.job_failed(
                    candidate_id=job.candidate_id
                ),
                on_discarded=lambda job, _result: scan_reducer.job_discarded(
                    candidate_id=job.candidate_id
                ),
                on_finished=reduce_scan_result,
                after_finished=emit_scan_finished,
                on_skipped=lambda job: scan_reducer.refresh_entry(
                    job.candidate_id
                ),
                discard_result=lambda result, action: (
                    action == "stop"
                    or (
                        action == "pause"
                        and result.error_message is not None
                    )
                ),
                group_key=(
                    (lambda job: job.target.connection_id)
                    if config.system.max_concurrent_targets_by_connection
                    else None
                ),
                max_workers_by_group=(
                    config.system.max_concurrent_targets_by_connection or None
                ),
            )

            with scan_execution.lock:
                if (
                    scan_execution.control_action == "pause"
                    and int(self.runtime_state["progress_total"] or 0) > 0
                    and int(self.runtime_state["progress_completed"] or 0)
                    >= int(self.runtime_state["progress_total"] or 0)
                ):
                    scan_execution.clear_control_action()
                    self.ports.set_last_control_action(None)
                    self.state_machine.transition(
                        ACTIVE_SCAN_LIFECYCLE,
                        lease_duration_seconds=(
                            self.ports.lease_duration_seconds(config)
                        ),
                    )
                if scan_execution.control_action is not None:
                    return results

            def persist_terminal_checkpoint(
                completed_metadata: dict[str, object],
            ) -> None:
                self.ports.persist_active_run(
                    run_id=run_id,
                    enabled_targets=comparison_targets,
                    attempts_per_target=attempts_per_target,
                    run_entries=list(self.runtime_state["run_entries"]),
                    run_metadata=completed_metadata,
                    config=config,
                )

            def emit_retained_terminal(lifecycle: str) -> None:
                if lifecycle != "finalizing":
                    return
                self.ports.emit_progress_event(
                    progress_callback,
                    {
                        "type": "scan.finalizing",
                        "last_phase": self.runtime_state.get("last_phase"),
                        "last_phase_completed": int(
                            self.runtime_state.get("last_phase_completed") or 0
                        ),
                        "last_phase_total": int(
                            self.runtime_state.get("last_phase_total") or 0
                        ),
                        "finalizing_started_at": self.runtime_state.get(
                            "finalizing_started_at"
                        ),
                        "updated_at": self.runtime_state.get("updated_at"),
                        "lease_expires_at": self.runtime_state.get(
                            "lease_expires_at"
                        ),
                    },
                )

            scan_outcomes.finish_completed(
                circuit_open=scan_reducer.circuit_open,
                hard_error_count=scan_reducer.hard_error_count,
                result_count=len(results),
                progress_completed=int(
                    self.runtime_state["progress_completed"] or 0
                ),
                progress_total=int(
                    self.runtime_state["progress_total"] or 0
                ),
                retain_finalizing_state=retain_finalizing_state,
                persist_retained_checkpoint=persist_terminal_checkpoint,
                on_retained=emit_retained_terminal,
            )
            return results
        except Exception as exc:
            self.ports.log(f"run_enabled_targets exception={exc}")
            scan_outcomes.finish_exception(
                error_message=str(exc),
                result_count=len(results),
            )
            raise
        finally:
            self.ports.log(
                f"run_enabled_targets end last_run_count={len(results)}"
            )
            scan_execution.settle()
            self.ports.reset_progress_state_cache()
