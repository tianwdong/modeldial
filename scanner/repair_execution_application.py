from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .active_run_store import ActiveRunStore
from .comparison_groups import ComparisonGroupProjector
from .execution import (
    ExecutionEngine,
    ExecutionJob,
    RunLifecycleCoordinator,
    RunStateMachine,
)
from .execution_job_planner import ExecutionJobPlanner
from .history_store import HistoryStore
from .job_reducers import RepairJobReducer
from .legacy_scan_compat import (
    ACTIVE_SCAN_LIFECYCLE,
    SCAN_PHASE,
    metadata_question_ids,
    normalize_phase,
)
from .models import AppConfig, ResolvedScanTarget, RunMetadata, ScanResult
from .question_bank import QuestionCollection
from .repair_execution import RepairExecutionCommand
from .repair_outcomes import RepairOutcomeCoordinator
from .repair_planner import RepairPlan
from .scan_planner import ScanPlanner
from .scan_target_resolver import ScanTargetResolver


ProgressCallback = Callable[[dict[str, object]], None]


@dataclass(frozen=True)
class RepairExecutionPorts:
    build_run_entries: Callable[..., list[dict[str, object]]]
    persist_active_run: Callable[..., None]
    journal_event: Callable[..., None]
    run_target: Callable[..., ScanResult]
    emit_progress_event: Callable[..., None]
    reset_progress_state_cache: Callable[..., None]
    set_last_control_action: Callable[[str | None], None]
    lease_duration_seconds: Callable[[AppConfig], int]
    timestamp: Callable[[], str]


@dataclass(frozen=True)
class _PreparedRepairJobs:
    jobs: list[ExecutionJob]
    persist_state: Callable[[], None]
    on_started: Callable[[ExecutionJob], None]
    on_finished: Callable[[ExecutionJob, ScanResult], None]
    pause_is_complete: Callable[[], bool]
    on_stopped: Callable[[ExecutionJob], None] | None = None
    on_failed: Callable[[ExecutionJob, Exception], None] | None = None
    on_discarded: Callable[[ExecutionJob, ScanResult], None] | None = None


def validate_candidate_repair_plan(
    plan: RepairPlan,
    *,
    run_id: str,
    candidate_id: str,
    question_id: str | None,
) -> None:
    if (
        plan.operation_kind != "candidate_repair"
        or plan.requested_run_id != run_id
        or plan.candidate_id != candidate_id
        or plan.question_id != question_id
    ):
        raise ValueError("repair plan does not match candidate repair request")


def validate_batch_repair_plan(
    plan: RepairPlan,
    *,
    run_id: str,
    candidate_ids: list[str] | None,
    expected_operation_kind: str,
) -> None:
    expected_candidate_ids = tuple(
        dict.fromkeys(
            candidate_ids
            or [
                str(item)
                for item in plan.metadata.get("requested_candidate_ids", [])
            ]
        )
    )
    if (
        plan.operation_kind != expected_operation_kind
        or plan.requested_run_id != run_id
        or expected_candidate_ids != plan.selected_candidate_ids
    ):
        raise ValueError("repair plan does not match batch repair request")


def recomputed_repair_metadata(
    metadata: dict[str, object],
    *,
    history: list[ScanResult],
    enabled_targets: list[ResolvedScanTarget],
    question_pack: QuestionCollection,
    target_resolver: ScanTargetResolver,
    timestamp: Callable[[], str],
    run_ids: list[str] | None = None,
) -> dict[str, object]:
    run_ids = list(run_ids or [str(metadata["run_id"])])
    run_id_set = set(run_ids)
    requested_candidate_ids = [
        str(item) for item in metadata.get("requested_candidate_ids", [])
    ]
    candidate_ids_by_label = target_resolver.candidate_ids_by_label(
        enabled_targets
    )
    latest_results: dict[str, dict[str, ScanResult]] = {}
    for item in history:
        if item.run_id not in run_id_set:
            continue
        candidate_id = target_resolver.result_candidate_id(
            item,
            candidate_ids_by_label,
        )
        if (
            candidate_id in requested_candidate_ids
            and normalize_phase(item.phase) == SCAN_PHASE
        ):
            latest_results.setdefault(str(candidate_id), {})[
                item.question_id
            ] = item
    frozen_question_ids = metadata_question_ids(metadata)
    profile_id = ScanPlanner.metadata_evaluation_profile_id(
        metadata,
        question_pack,
    )
    evaluation_profile = ScanPlanner.evaluation_profile(
        question_pack,
        profile_id,
    )
    expected_question_ids = set(
        frozen_question_ids or evaluation_profile.question_ids
    )
    has_hard_failure = any(
        set(latest_results.get(candidate_id, {})) != expected_question_ids
        or any(
            item.error_message is not None
            for item in latest_results.get(candidate_id, {}).values()
        )
        for candidate_id in requested_candidate_ids
    )
    updated = RunMetadata.from_dict(metadata).to_dict()
    updated = ComparisonGroupProjector.preserve_legacy_selection_metadata(
        metadata,
        updated,
    )
    updated["question_count"] = len(expected_question_ids)
    updated["question_ids"] = [
        question_id
        for question_id in (
            frozen_question_ids or evaluation_profile.question_ids
        )
        if question_id in expected_question_ids
    ]
    updated["status"] = "degraded" if has_hard_failure else "completed"
    regular_candidate_ids = [
        str(item) for item in metadata.get("regular_candidate_ids", [])
    ]
    updated["is_complete_regular_round"] = (
        not has_hard_failure
        and evaluation_profile.result_level == "complete"
        and str(metadata.get("selection_mode")) == "regular"
        and set(requested_candidate_ids) == set(regular_candidate_ids)
    )
    updated["completed_at"] = timestamp()
    return updated


class RepairExecutionApplicationService:
    """Application orchestration for prepared candidate and batch repairs."""

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
        target_resolver: ScanTargetResolver,
        ports: RepairExecutionPorts,
    ) -> None:
        self.runtime_state = runtime_state
        self.state_machine = state_machine
        self.lifecycle = lifecycle
        self.engine = engine
        self.history_store = history_store
        self.active_run_store = active_run_store
        self.job_planner = job_planner
        self.target_resolver = target_resolver
        self.ports = ports

    def execute_candidate(
        self,
        *,
        plan: RepairPlan,
        progress_callback: ProgressCallback | None,
        retain_finalizing_state: bool,
    ) -> list[ScanResult]:
        return self._execute_candidate_plan(
            plan=plan,
            progress_callback=progress_callback,
            retain_finalizing_state=retain_finalizing_state,
        )

    def execute_batch(
        self,
        *,
        plan: RepairPlan,
        progress_callback: ProgressCallback | None,
        retain_finalizing_state: bool,
    ) -> list[ScanResult]:
        if plan.operation_kind == "timeout_repair":
            repair_label = "超时题"
            event_prefix = "timeout-repair"
        elif plan.operation_kind == "failed_repair":
            repair_label = "失败题"
            event_prefix = "repair"
        else:
            raise ValueError("unsupported batch repair operation")
        return self._execute_batch_plan(
            plan=plan,
            repair_label=repair_label,
            event_prefix=event_prefix,
            progress_callback=progress_callback,
            retain_finalizing_state=retain_finalizing_state,
        )

    def _execute_candidate_plan(
        self,
        *,
        plan: RepairPlan,
        progress_callback: ProgressCallback | None,
        retain_finalizing_state: bool,
    ) -> list[ScanResult]:
        config = plan.config
        requested_group_id = plan.requested_group_id
        persist_run_id = plan.persist_run_id
        candidate_id = str(plan.candidate_id)
        question_id = plan.question_id
        target = plan.selected_targets[0]
        all_targets = list(plan.all_targets)
        questions = list(plan.questions)
        latest_by_question = plan.latest_by_question(candidate_id)
        repair_steps = list(plan.steps_for(candidate_id))
        self.ports.set_last_control_action(None)
        repair_execution = RepairExecutionCommand(
            run_id=persist_run_id,
            operation_kind="candidate_repair",
            total=len(repair_steps),
            max_workers=1,
            state_machine=self.state_machine,
            lifecycle=self.lifecycle,
            engine=self.engine,
            history_store=self.history_store,
            active_run_store=self.active_run_store,
            on_control=self.ports.set_last_control_action,
        )
        repair_outcomes = self._new_outcomes(
            plan=plan,
            repair_execution=repair_execution,
            base_run_metadata=dict(plan.persisted_run_metadata),
            event_prefix="repair",
            journal_scope={
                "candidate_id": candidate_id,
                "question_ids": [question.id for question in repair_steps],
            },
        )
        if plan.completion_only:
            return self._finish_completion_only(
                plan=plan,
                repair_outcomes=repair_outcomes,
                retain_finalizing_state=retain_finalizing_state,
            )

        run_metadata = dict(plan.persisted_run_metadata)
        run_metadata["status"] = "running"
        run_metadata["completed_at"] = None
        run_metadata["comparison_group_id"] = requested_group_id

        def prepare_jobs() -> _PreparedRepairJobs:
            repair_reducer = RepairJobReducer(
                runtime_state=self.runtime_state,
                run_entries=self.runtime_state["run_entries"],
                question_ids_by_candidate={
                    candidate_id: [question.id for question in repair_steps]
                },
                latest_by_candidate={candidate_id: latest_by_question},
            )
            repair_reducer.initialize_entries(initial_status="running")

            def persist_repair_state() -> None:
                remaining_question_ids = repair_reducer.retryable_question_ids(
                    candidate_id
                )
                self.ports.persist_active_run(
                    run_id=persist_run_id,
                    enabled_targets=all_targets,
                    attempts_per_target=max(1, len(questions)),
                    run_entries=self.runtime_state["run_entries"],
                    run_metadata=run_metadata,
                )
                self.active_run_store.mutate(
                    lambda current: {
                        **(current or {}),
                        "repair_operation_kind": "candidate_repair",
                        "repair_operation_run_id": requested_group_id,
                        "repair_run_id": requested_group_id,
                        "repair_candidate_id": candidate_id,
                        "repair_question_id": (
                            question_id
                            if question_id in remaining_question_ids
                            else None
                        ),
                        "repair_question_ids": remaining_question_ids,
                    }
                )

            persist_repair_state()
            self.ports.journal_event(
                "repair.started",
                {
                    "candidate_id": candidate_id,
                    "question_ids": [question.id for question in repair_steps],
                    "scope": "cell" if question_id is not None else "candidate",
                },
                run_id=persist_run_id,
            )

            repair_jobs = self.job_planner.plan_candidate_repair(
                target=target,
                repair_questions=repair_steps,
                all_questions=questions,
            )
            return _PreparedRepairJobs(
                jobs=repair_jobs,
                persist_state=persist_repair_state,
                on_started=lambda job: self._start_candidate_job(
                    repair_reducer=repair_reducer,
                    job=job,
                    config=config,
                ),
                on_finished=lambda job, result: (
                    repair_reducer.candidate_job_finished(
                        candidate_id=job.candidate_id,
                        question_id=job.question.id,
                        result=result,
                    )
                ),
                pause_is_complete=lambda: not (
                    repair_reducer.retryable_question_ids(candidate_id)
                ),
            )

        return self._execute_prepared_jobs(
            plan=plan,
            repair_execution=repair_execution,
            repair_outcomes=repair_outcomes,
            event_prefix="repair",
            progress_callback=progress_callback,
            retain_finalizing_state=retain_finalizing_state,
            prepare_jobs=prepare_jobs,
        )

    def _execute_batch_plan(
        self,
        *,
        plan: RepairPlan,
        repair_label: str,
        event_prefix: str,
        progress_callback: ProgressCallback | None,
        retain_finalizing_state: bool,
    ) -> list[ScanResult]:
        config = plan.config
        requested_group_id = plan.requested_group_id
        persist_run_id = plan.persist_run_id
        all_targets = list(plan.all_targets)
        selected_targets = list(plan.selected_targets)
        questions = list(plan.questions)
        repair_steps_by_candidate = {
            candidate_id: list(steps)
            for candidate_id, steps in plan.repair_steps_by_candidate
        }
        self.ports.set_last_control_action(None)

        run_metadata = dict(plan.persisted_run_metadata)
        run_metadata["status"] = "running"
        run_metadata["completed_at"] = None
        run_metadata["comparison_group_id"] = requested_group_id
        total_steps = sum(
            len(items) for items in repair_steps_by_candidate.values()
        )
        repair_execution = RepairExecutionCommand(
            run_id=persist_run_id,
            operation_kind=plan.operation_kind,
            total=total_steps,
            max_workers=max(1, int(config.system.max_concurrent_targets)),
            state_machine=self.state_machine,
            lifecycle=self.lifecycle,
            engine=self.engine,
            history_store=self.history_store,
            active_run_store=self.active_run_store,
            on_control=self.ports.set_last_control_action,
        )
        repair_outcomes = self._new_outcomes(
            plan=plan,
            repair_execution=repair_execution,
            base_run_metadata=run_metadata,
            event_prefix=event_prefix,
            journal_scope={
                "candidate_ids": list(repair_steps_by_candidate),
            },
        )

        def prepare_jobs() -> _PreparedRepairJobs:
            repair_reducer = RepairJobReducer(
                runtime_state=self.runtime_state,
                run_entries=self.runtime_state["run_entries"],
                question_ids_by_candidate={
                    candidate_id: [question.id for question in repair_steps]
                    for candidate_id, repair_steps in (
                        repair_steps_by_candidate.items()
                    )
                },
            )
            repair_reducer.initialize_entries(initial_status="pending")

            def persist_repair_state() -> None:
                self.ports.persist_active_run(
                    run_id=persist_run_id,
                    enabled_targets=all_targets,
                    attempts_per_target=max(1, len(questions)),
                    run_entries=self.runtime_state["run_entries"],
                    run_metadata=run_metadata,
                )
                remaining_question_ids_by_candidate = (
                    repair_reducer.pending_question_ids_by_candidate()
                )
                self.active_run_store.mutate(
                    lambda current: {
                        **(current or {}),
                        "repair_operation_kind": plan.operation_kind,
                        "repair_operation_run_id": requested_group_id,
                        "repair_run_id": requested_group_id,
                        "repair_candidate_ids": [
                            candidate_id
                            for candidate_id, question_ids
                            in remaining_question_ids_by_candidate.items()
                            if question_ids
                        ],
                        "repair_question_ids_by_candidate": (
                            remaining_question_ids_by_candidate
                        ),
                    }
                )

            repair_jobs = self.job_planner.plan_batch_repair(
                targets=selected_targets,
                repair_questions_by_candidate=repair_steps_by_candidate,
                all_questions=questions,
            )
            return _PreparedRepairJobs(
                jobs=repair_jobs,
                persist_state=persist_repair_state,
                on_started=lambda job: repair_reducer.batch_job_started(
                    candidate_id=job.candidate_id,
                    current_target=f"重试{repair_label}",
                ),
                on_stopped=lambda job: repair_reducer.batch_job_stopped(
                    candidate_id=job.candidate_id
                ),
                on_failed=lambda job, _error: (
                    repair_reducer.batch_job_failed(
                        candidate_id=job.candidate_id
                    )
                ),
                on_discarded=lambda job, _result: (
                    repair_reducer.batch_job_discarded(
                        candidate_id=job.candidate_id
                    )
                ),
                on_finished=lambda job, result: (
                    repair_reducer.batch_job_finished(
                        candidate_id=job.candidate_id,
                        question_id=job.question.id,
                        result=result,
                    )
                ),
                pause_is_complete=lambda: (
                    repair_reducer.completed_step_count >= total_steps
                ),
            )

        return self._execute_prepared_jobs(
            plan=plan,
            repair_execution=repair_execution,
            repair_outcomes=repair_outcomes,
            event_prefix=event_prefix,
            progress_callback=progress_callback,
            retain_finalizing_state=retain_finalizing_state,
            prepare_jobs=prepare_jobs,
        )

    def _execute_prepared_jobs(
        self,
        *,
        plan: RepairPlan,
        repair_execution: RepairExecutionCommand[ScanResult],
        repair_outcomes: RepairOutcomeCoordinator[ScanResult],
        event_prefix: str,
        progress_callback: ProgressCallback | None,
        retain_finalizing_state: bool,
        prepare_jobs: Callable[[], _PreparedRepairJobs],
    ) -> list[ScanResult]:
        config = plan.config
        all_targets = list(plan.all_targets)
        questions = list(plan.questions)
        persist_run_id = plan.persist_run_id
        self.ports.reset_progress_state_cache(
            config,
            history_count=len(plan.history),
        )
        run_entries = self.ports.build_run_entries(
            enabled_targets=all_targets,
            attempts_per_target=max(1, len(questions)),
            completed_by_candidate=dict(plan.completed_by_candidate),
            active_run=plan.persisted_active,
        )
        results = repair_execution.results
        try:
            repair_execution.begin(
                run_entries=run_entries,
                last_run_mode=(
                    "mock" if config.system.use_mock_results else "live"
                ),
                lease_duration_seconds=self.ports.lease_duration_seconds(config),
            )
            prepared = prepare_jobs()
        except Exception as exc:
            try:
                repair_outcomes.finish_failed(
                    error_message=str(exc),
                    result_count=len(results),
                )
            finally:
                repair_execution.settle()
                self.ports.reset_progress_state_cache()
            raise

        def run_repair_job(job: ExecutionJob) -> ScanResult:
            return self.ports.run_target(
                job.target,
                job.question,
                config,
                run_id=persist_run_id,
                phase=job.result_phase,
                attempt_index=job.attempt_index,
            )

        def emit_repair_started(job: ExecutionJob) -> None:
            self.ports.emit_progress_event(
                progress_callback,
                {
                    "type": f"{event_prefix}.question.started",
                    "candidate_id": job.candidate_id,
                    "question_id": job.question.id,
                },
            )

        def emit_repair_finished(
            job: ExecutionJob,
            _result: ScanResult,
        ) -> None:
            self.ports.emit_progress_event(
                progress_callback,
                {
                    "type": f"{event_prefix}.question.finished",
                    "candidate_id": job.candidate_id,
                    "question_id": job.question.id,
                },
            )

        try:
            repair_execution.execute_jobs(
                prepared.jobs,
                run_job=run_repair_job,
                persist_state=prepared.persist_state,
                on_started=prepared.on_started,
                after_started=emit_repair_started,
                on_stopped=prepared.on_stopped,
                on_failed=prepared.on_failed,
                on_discarded=prepared.on_discarded,
                on_finished=prepared.on_finished,
                after_finished=emit_repair_finished,
                discard_result=lambda result, action: (
                    action in {"pause", "stop"}
                    and result.error_message is not None
                ),
            )

            if (
                repair_execution.control_action == "pause"
                and prepared.pause_is_complete()
            ):
                repair_execution.clear_control_action()
                self.ports.set_last_control_action(None)
            if repair_execution.control_action is not None:
                repair_outcomes.finish_controlled(
                    history=self.history_store.load_all(),
                    result_count=len(results),
                )
                return results

            def persist_terminal_checkpoint(
                completed_metadata: dict[str, object],
            ) -> None:
                self.ports.persist_active_run(
                    run_id=persist_run_id,
                    enabled_targets=all_targets,
                    attempts_per_target=max(1, len(questions)),
                    run_entries=list(self.runtime_state["run_entries"]),
                    run_metadata=completed_metadata,
                    config=config,
                )

            repair_outcomes.finish_completed(
                history=self.history_store.load_all(),
                result_count=len(results),
                retain_finalizing_state=retain_finalizing_state,
                persist_retained_checkpoint=persist_terminal_checkpoint,
            )
        except Exception as exc:
            repair_outcomes.finish_failed(
                error_message=str(exc),
                result_count=len(results),
            )
            raise
        finally:
            repair_execution.settle()
            self.ports.reset_progress_state_cache()
        return results

    def _new_outcomes(
        self,
        *,
        plan: RepairPlan,
        repair_execution: RepairExecutionCommand[ScanResult],
        base_run_metadata: dict[str, object],
        event_prefix: str,
        journal_scope: dict[str, object],
    ) -> RepairOutcomeCoordinator[ScanResult]:
        metadata = dict(plan.metadata)
        all_targets = list(plan.all_targets)
        question_pack = plan.question_pack
        group_member_run_ids = list(plan.group_member_run_ids)
        return RepairOutcomeCoordinator(
            session=repair_execution.session,
            base_run_metadata=base_run_metadata,
            persist_run_id=plan.persist_run_id,
            comparison_group_id=plan.requested_group_id,
            event_prefix=event_prefix,
            journal_scope=journal_scope,
            metadata_factory=lambda current_history: (
                recomputed_repair_metadata(
                    metadata,
                    history=current_history,
                    enabled_targets=all_targets,
                    question_pack=question_pack,
                    run_ids=group_member_run_ids,
                    target_resolver=self.target_resolver,
                    timestamp=self.ports.timestamp,
                )
            ),
            timestamp=self.ports.timestamp,
        )

    def _finish_completion_only(
        self,
        *,
        plan: RepairPlan,
        repair_outcomes: RepairOutcomeCoordinator[ScanResult],
        retain_finalizing_state: bool,
    ) -> list[ScanResult]:
        all_targets = list(plan.all_targets)
        questions = list(plan.questions)
        completion_entries = self.ports.build_run_entries(
            enabled_targets=all_targets,
            attempts_per_target=max(1, len(questions)),
            completed_by_candidate=dict(plan.completed_by_candidate),
            active_run=plan.persisted_active,
        )

        def persist_completion_checkpoint(
            completed_metadata: dict[str, object],
        ) -> None:
            self.ports.persist_active_run(
                run_id=plan.persist_run_id,
                enabled_targets=all_targets,
                attempts_per_target=max(1, len(questions)),
                run_entries=completion_entries,
                run_metadata=completed_metadata,
                config=plan.config,
            )

        repair_outcomes.finish_completed(
            history=list(plan.history),
            result_count=0,
            retain_finalizing_state=retain_finalizing_state,
            persist_retained_checkpoint=persist_completion_checkpoint,
            completion_only=True,
        )
        return []

    def _start_candidate_job(
        self,
        *,
        repair_reducer: RepairJobReducer,
        job: ExecutionJob,
        config: AppConfig,
    ) -> None:
        repair_reducer.candidate_job_started(
            current_target=(
                f"{job.target.display_label} · 重试 {job.question.title}"
            )
        )
        self.state_machine.transition(
            ACTIVE_SCAN_LIFECYCLE,
            lease_duration_seconds=self.ports.lease_duration_seconds(config),
        )
