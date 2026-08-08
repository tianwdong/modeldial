from __future__ import annotations

from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass
from queue import Empty, Queue
import threading
from typing import ContextManager

from .maintenance_application import AutoResumeClaim
from .models import ScanPlan, ScanResult
from .repair_planner import RepairPlan
from .runtime_event_projector import (
    project_finalizing_runtime_event,
    project_started_runtime_state,
    project_terminal_failure_event,
)
from .service import MonitorService, SCAN_LOCK_STALE_SECONDS

ProgressCallback = Callable[[dict[str, object]], None]
ProcessLock = Callable[..., ContextManager[bool]]
SnapshotBuilder = Callable[..., dict[str, object]]
LogCallback = Callable[[str], None]


def _ignore_log(_message: str) -> None:
    return


def _build_snapshot(
    service: MonitorService,
    builder: SnapshotBuilder,
    codex_insights_provider: Callable[..., dict[str, object]] | None,
) -> dict[str, object]:
    return builder(
        service.config_store,
        service.history_store,
        service.active_run_store,
        codex_insights_provider=codex_insights_provider,
    )


def _terminal_failure_event(
    service: MonitorService,
    builder: SnapshotBuilder,
    codex_insights_provider: Callable[..., dict[str, object]] | None,
    *,
    event_type: str,
    failure_category: str,
    failure_message: str,
    fields: dict[str, object] | None = None,
    prepare_failure_state: Callable[[], dict[str, object]] | None = None,
    preparation_error_field: str = "failure_state_recording_error",
) -> dict[str, object]:
    return project_terminal_failure_event(
        lambda: _build_snapshot(
            service,
            builder,
            codex_insights_provider,
        ),
        event_type=event_type,
        failure_category=failure_category,
        failure_message=failure_message,
        fields=fields,
        prepare_failure_state=prepare_failure_state,
        preparation_error_field=preparation_error_field,
    )


def _stream_worker_events(
    execute: Callable[[ProgressCallback], list[ScanResult]],
    control_action: Callable[[], object],
) -> Generator[dict[str, object], None, dict[str, object]]:
    event_queue: Queue[dict[str, object]] = Queue()
    outcome: dict[str, object] = {}

    def worker() -> None:
        try:
            outcome["results"] = execute(event_queue.put)
            outcome["control_action"] = control_action()
        except Exception as exc:  # pragma: no cover - surfaced by caller events
            outcome["error"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        while thread.is_alive() or not event_queue.empty():
            try:
                yield event_queue.get(timeout=0.1)
            except Empty:
                continue
    finally:
        thread.join()
    return outcome


@dataclass(frozen=True)
class ScanCommand:
    service: MonitorService

    def plan(
        self,
        *,
        force_restart: bool,
        requested_candidate_ids: list[str] | None,
        selection_mode: str,
        custom_round_mode: str,
        evaluation_profile_id: str | None,
        upgrade_from_run_id: str | None,
    ) -> ScanPlan:
        return self.service.plan_scan(
            force_restart=force_restart,
            requested_candidate_ids=requested_candidate_ids,
            selection_mode=selection_mode,
            custom_round_mode=custom_round_mode,
            evaluation_profile_id=evaluation_profile_id,
            upgrade_from_run_id=upgrade_from_run_id,
        )

    def execute(
        self,
        plan: ScanPlan,
        *,
        progress_callback: ProgressCallback,
    ) -> list[ScanResult]:
        return self.service.run_enabled_targets(
            scan_plan=plan,
            retain_finalizing_state=True,
            progress_callback=progress_callback,
        )

    def stream_events(
        self,
        *,
        force_restart: bool,
        requested_candidate_ids: list[str] | None,
        selection_mode: str,
        custom_round_mode: str,
        evaluation_profile_id: str | None,
        upgrade_from_run_id: str | None,
        expected_resume_run_id: str | None = None,
        process_lock: ProcessLock,
        snapshot_builder: SnapshotBuilder,
        terminal_snapshot_builder: SnapshotBuilder | None = None,
        codex_insights_provider: Callable[..., dict[str, object]] | None = None,
        prepare_execution: Callable[[ScanPlan], object] | None = None,
        log: LogCallback = _ignore_log,
    ) -> Iterator[dict[str, object]]:
        service = self.service
        failure_snapshot_builder = terminal_snapshot_builder or snapshot_builder
        with process_lock(
            service.active_run_store,
            service.history_store,
            lease_heartbeat=service.heartbeat_active_run_lease,
        ) as lock_acquired:
            if not lock_acquired:
                state = snapshot_builder(
                    service.config_store,
                    service.history_store,
                    service.active_run_store,
                    codex_insights_provider=codex_insights_provider,
                )
                runtime = state["runtime"]
                total = int(runtime["total_targets"] or 0)
                log("scan.already_running")
                yield {
                    "type": "scan.already_running",
                    "total_targets": total,
                    "completed_targets": int(runtime["completed_targets"] or 0),
                    "state": state,
                }
                return

            recovery = service.recover_orphaned_finalizing_run(
                exclusive_lock_held=True
            )
            if recovery.get("status") == "incomplete":
                yield _terminal_failure_event(
                    service,
                    failure_snapshot_builder,
                    codex_insights_provider,
                    event_type="scan.failed",
                    failure_category="run_recovery_failed",
                    failure_message=str(
                        recovery.get("message")
                        or "finalizing run recovery failed"
                    ),
                    fields={"run_id": recovery.get("run_id")},
                )
                return

            try:
                scan_plan = self.plan(
                    force_restart=force_restart,
                    requested_candidate_ids=requested_candidate_ids,
                    selection_mode=selection_mode,
                    custom_round_mode=custom_round_mode,
                    evaluation_profile_id=evaluation_profile_id,
                    upgrade_from_run_id=upgrade_from_run_id,
                )
            except Exception as exc:
                failure_message = str(exc)
                failure_reason = str(
                    getattr(exc, "reason", "invalid_scan_plan")
                )
                log(
                    "scan.failed scan_planning_failed "
                    f"reason={failure_reason} error={failure_message}"
                )
                yield _terminal_failure_event(
                    service,
                    failure_snapshot_builder,
                    codex_insights_provider,
                    event_type="scan.failed",
                    failure_category="scan_planning_failed",
                    failure_message=failure_message,
                    fields={
                        "failure_reason": failure_reason,
                        "selection_mode": selection_mode,
                        "custom_round_mode": custom_round_mode,
                        "evaluation_profile_id": evaluation_profile_id,
                        "requested_candidate_ids": list(
                            requested_candidate_ids or []
                        ),
                    },
                )
                return
            if expected_resume_run_id is not None and (
                scan_plan.run_id != expected_resume_run_id
                or scan_plan.resume is None
            ):
                yield _terminal_failure_event(
                    service,
                    failure_snapshot_builder,
                    codex_insights_provider,
                    event_type="scan.failed",
                    failure_category="auto_resume_plan_mismatch",
                    failure_message=(
                        "自动续扫计划未恢复预期运行，已阻止创建新任务"
                    ),
                    fields={
                        "run_id": expected_resume_run_id,
                        "planned_run_id": scan_plan.run_id,
                    },
                )
                return
            if prepare_execution is not None:
                try:
                    prepare_execution(scan_plan)
                except Exception as exc:
                    yield _terminal_failure_event(
                        service,
                        failure_snapshot_builder,
                        codex_insights_provider,
                        event_type="scan.failed",
                        failure_category="pricing_preparation_failed",
                        failure_message=str(exc),
                        fields={"run_id": scan_plan.run_id},
                    )
                    return
            planned_candidate_ids = (
                list(scan_plan.requested_candidate_ids)
                if scan_plan.requested_candidate_ids is not None
                else None
            )
            total = scan_plan.total_targets
            completed = scan_plan.completed_targets
            log(f"scan.started total={total}")
            yield {
                "type": "scan.started",
                "total_targets": total,
                "completed_targets": completed,
                "selection_mode": scan_plan.selection_mode,
                "custom_round_mode": scan_plan.custom_round_mode,
                "evaluation_profile_id": scan_plan.evaluation_profile_id,
                "evaluation_profile_label": scan_plan.evaluation_profile_label,
                "evaluation_result_level": scan_plan.evaluation_result_level,
                "question_count": scan_plan.question_count,
                "upgrade_from_run_id": scan_plan.upgrade_from_run_id,
                "requested_candidate_ids": list(planned_candidate_ids or []),
                "state": project_started_runtime_state(
                    service.build_runtime_event,
                    run_id=scan_plan.run_id,
                    phase="scan",
                    completed_targets=completed,
                    total_targets=total,
                    scan_lock_stale_seconds=SCAN_LOCK_STALE_SECONDS,
                ),
            }
            before_count = 0 if force_restart else completed
            outcome = yield from _stream_worker_events(
                lambda progress: self.execute(
                    scan_plan,
                    progress_callback=lambda event: progress(
                        project_finalizing_runtime_event(
                            service.build_runtime_event,
                            event,
                        )
                        if event.get("type") == "scan.finalizing"
                        else event
                    ),
                ),
                lambda: service.last_control_action,
            )

            error = outcome.get("error")
            if error is not None:
                failure_message = str(error)
                log(f"scan.failed scan_execution_failed error={failure_message}")
                yield _terminal_failure_event(
                    service,
                    failure_snapshot_builder,
                    codex_insights_provider,
                    event_type="scan.failed",
                    failure_category="scan_execution_failed",
                    failure_message=failure_message,
                    fields={
                        "total_targets": total,
                        "result_count": before_count,
                    },
                )
                return
            results = outcome.get("results", [])
            control_action = outcome.get("control_action")
            if control_action in {"pause", "stop"}:
                event_type = (
                    "scan.paused" if control_action == "pause" else "scan.stopped"
                )
                log(f"{event_type} result_count={len(results)}")
                try:
                    control_state = snapshot_builder(
                        service.config_store,
                        service.history_store,
                        service.active_run_store,
                        codex_insights_provider=codex_insights_provider,
                    )
                except Exception as exc:
                    yield _terminal_failure_event(
                        service,
                        failure_snapshot_builder,
                        codex_insights_provider,
                        event_type="scan.failed",
                        failure_category="scan_terminal_projection_failed",
                        failure_message=str(exc),
                        fields={
                            "control_action": control_action,
                            "total_targets": total,
                            "result_count": before_count + len(results),
                        },
                    )
                    return
                yield {
                    "type": event_type,
                    "total_targets": total,
                    "result_count": before_count + len(results),
                    "state": control_state,
                }
                return
            terminal_failure = service.scan_terminal_failure_state()
            if terminal_failure is not None:
                failure_message = str(
                    terminal_failure.get("failure_message")
                    or "scan execution failed"
                )
                yield _terminal_failure_event(
                    service,
                    failure_snapshot_builder,
                    codex_insights_provider,
                    event_type="scan.failed",
                    failure_category="scan_execution_failed",
                    failure_message=failure_message,
                    fields={
                        "total_targets": total,
                        "result_count": before_count + len(results),
                    },
                )
                return
            try:
                final_state = snapshot_builder(
                    service.config_store,
                    service.history_store,
                    service.active_run_store,
                    codex_insights_provider=codex_insights_provider,
                )
            except Exception as exc:
                log(f"scan.failed recommendation_build_failed error={exc}")
                failure_message = str(exc)
                yield _terminal_failure_event(
                    service,
                    failure_snapshot_builder,
                    codex_insights_provider,
                    event_type="scan.failed",
                    failure_category="recommendation_build_failed",
                    failure_message=failure_message,
                    prepare_failure_state=lambda: (
                        service.record_finalization_projection_failure(
                            failure_message,
                            exclusive_lock_held=True,
                        )
                    ),
                    preparation_error_field="finalization_recording_error",
                )
                return
            try:
                final_state = service.complete_finalizing_snapshot(
                    final_state,
                    exclusive_lock_held=True,
                )
            except Exception as exc:
                log(f"scan.failed finalization_commit_failed error={exc}")
                failure_message = str(exc)
                yield _terminal_failure_event(
                    service,
                    failure_snapshot_builder,
                    codex_insights_provider,
                    event_type="scan.failed",
                    failure_category="finalization_commit_failed",
                    failure_message=failure_message,
                    prepare_failure_state=lambda: (
                        service.record_finalization_commit_failure(
                            failure_message,
                            exclusive_lock_held=True,
                        )
                    ),
                    preparation_error_field="finalization_recording_error",
                )
                return
            log(f"scan.finished result_count={len(results)}")
            yield {
                "type": "scan.finished",
                "total_targets": total,
                "result_count": before_count + len(results),
                "state": final_state,
            }


def _stream_repair_events(
    service: MonitorService,
    *,
    run_id: str,
    event_prefix: str,
    target_fields: dict[str, object],
    plan_builder: Callable[[], RepairPlan],
    started_fields_builder: Callable[
        [RepairPlan], tuple[dict[str, object], int]
    ],
    execute: Callable[[RepairPlan, ProgressCallback], list[ScanResult]],
    expected_resume_run_id: str | None,
    process_lock: ProcessLock,
    snapshot_builder: SnapshotBuilder,
    terminal_snapshot_builder: SnapshotBuilder | None,
    codex_insights_provider: Callable[..., dict[str, object]] | None,
    prepare_execution: Callable[[RepairPlan], object] | None,
) -> Iterator[dict[str, object]]:
    failure_snapshot_builder = terminal_snapshot_builder or snapshot_builder
    failure_category_prefix = event_prefix.replace("-", "_")
    with process_lock(
        service.active_run_store,
        service.history_store,
        lease_heartbeat=service.heartbeat_active_run_lease,
    ) as lock_acquired:
        if not lock_acquired:
            yield {
                "type": f"{event_prefix}.already_running",
                "run_id": run_id,
                **target_fields,
                "state": _build_snapshot(
                    service,
                    snapshot_builder,
                    codex_insights_provider,
                ),
            }
            return

        recovery = service.recover_orphaned_finalizing_run(
            exclusive_lock_held=True
        )
        if recovery.get("status") == "incomplete":
            yield _terminal_failure_event(
                service,
                failure_snapshot_builder,
                codex_insights_provider,
                event_type=f"{event_prefix}.failed",
                failure_category="run_recovery_failed",
                failure_message=str(
                    recovery.get("message")
                    or "finalizing run recovery failed"
                ),
                fields={"run_id": run_id, **target_fields},
            )
            return

        try:
            repair_plan = plan_builder()
        except Exception as exc:
            yield _terminal_failure_event(
                service,
                failure_snapshot_builder,
                codex_insights_provider,
                event_type=f"{event_prefix}.failed",
                failure_category=f"{failure_category_prefix}_plan_failed",
                failure_message=str(exc),
                fields={"run_id": run_id, **target_fields},
            )
            return
        if (
            expected_resume_run_id is not None
            and repair_plan.persist_run_id != expected_resume_run_id
        ):
            yield _terminal_failure_event(
                service,
                failure_snapshot_builder,
                codex_insights_provider,
                event_type=f"{event_prefix}.failed",
                failure_category="auto_resume_plan_mismatch",
                failure_message=(
                    "自动续修计划未恢复预期运行，已阻止创建新任务"
                ),
                fields={
                    "run_id": expected_resume_run_id,
                    "planned_run_id": repair_plan.persist_run_id,
                    **target_fields,
                },
            )
            return

        if prepare_execution is not None:
            try:
                prepare_execution(repair_plan)
            except Exception as exc:
                yield _terminal_failure_event(
                    service,
                    failure_snapshot_builder,
                    codex_insights_provider,
                    event_type=f"{event_prefix}.failed",
                    failure_category="pricing_preparation_failed",
                    failure_message=str(exc),
                    fields={"run_id": run_id, **target_fields},
                )
                return

        started_fields, total_targets = started_fields_builder(repair_plan)
        yield {
            "type": f"{event_prefix}.started",
            "run_id": run_id,
            **started_fields,
            "total_targets": total_targets,
            "completed_targets": 0,
            "state": project_started_runtime_state(
                service.build_runtime_event,
                run_id=repair_plan.persist_run_id,
                phase="repair",
                completed_targets=0,
                total_targets=total_targets,
                scan_lock_stale_seconds=SCAN_LOCK_STALE_SECONDS,
            ),
        }

        outcome = yield from _stream_worker_events(
            lambda progress: execute(repair_plan, progress),
            lambda: service.last_control_action,
        )
        error = outcome.get("error")
        if error is not None:
            yield _terminal_failure_event(
                service,
                failure_snapshot_builder,
                codex_insights_provider,
                event_type=f"{event_prefix}.failed",
                failure_category=f"{failure_category_prefix}_failed",
                failure_message=str(error),
                fields={"run_id": run_id, **target_fields},
            )
            return

        results = outcome.get("results", [])
        control_action = outcome.get("control_action")
        if control_action in {"pause", "stop"}:
            try:
                control_state = _build_snapshot(
                    service,
                    snapshot_builder,
                    codex_insights_provider,
                )
            except Exception as exc:
                yield _terminal_failure_event(
                    service,
                    failure_snapshot_builder,
                    codex_insights_provider,
                    event_type=f"{event_prefix}.failed",
                    failure_category=(
                        f"{failure_category_prefix}_terminal_projection_failed"
                    ),
                    failure_message=str(exc),
                    fields={
                        "run_id": run_id,
                        **target_fields,
                        "control_action": control_action,
                        "result_count": len(results),
                    },
                )
                return
            yield {
                "type": (
                    f"{event_prefix}.paused"
                    if control_action == "pause"
                    else f"{event_prefix}.stopped"
                ),
                "run_id": run_id,
                **target_fields,
                "result_count": len(results),
                "state": control_state,
            }
            return

        yield project_finalizing_runtime_event(
            service.build_runtime_event,
            {
                "type": f"{event_prefix}.finalizing",
                "run_id": run_id,
                **target_fields,
                "result_count": len(results),
            },
        )
        try:
            final_state = _build_snapshot(
                service,
                snapshot_builder,
                codex_insights_provider,
            )
        except Exception as exc:
            failure_message = str(exc)
            yield _terminal_failure_event(
                service,
                failure_snapshot_builder,
                codex_insights_provider,
                event_type=f"{event_prefix}.failed",
                failure_category=(
                    f"{failure_category_prefix}_terminal_projection_failed"
                ),
                failure_message=failure_message,
                fields={
                    "run_id": run_id,
                    **target_fields,
                    "result_count": len(results),
                },
                prepare_failure_state=lambda: (
                    service.record_finalization_projection_failure(
                        failure_message,
                        exclusive_lock_held=True,
                    )
                ),
                preparation_error_field="finalization_recording_error",
            )
            return
        try:
            final_state = service.complete_finalizing_snapshot(
                final_state,
                exclusive_lock_held=True,
            )
        except Exception as exc:
            failure_message = str(exc)
            yield _terminal_failure_event(
                service,
                failure_snapshot_builder,
                codex_insights_provider,
                event_type=f"{event_prefix}.failed",
                failure_category=(
                    f"{failure_category_prefix}_finalization_commit_failed"
                ),
                failure_message=failure_message,
                fields={
                    "run_id": run_id,
                    **target_fields,
                    "result_count": len(results),
                },
                prepare_failure_state=lambda: (
                    service.record_finalization_commit_failure(
                        failure_message,
                        exclusive_lock_held=True,
                    )
                ),
                preparation_error_field="finalization_recording_error",
            )
            return
        yield {
            "type": f"{event_prefix}.finished",
            "run_id": run_id,
            **target_fields,
            "result_count": len(results),
            "state": final_state,
        }


@dataclass(frozen=True)
class RepairCommand:
    service: MonitorService

    def plan_candidate(
        self,
        *,
        run_id: str,
        candidate_id: str,
        question_id: str | None,
    ) -> RepairPlan:
        return self.service.repair_planner.plan_candidate(
            run_id=run_id,
            candidate_id=candidate_id,
            question_id=question_id,
        )

    def execute_candidate(
        self,
        plan: RepairPlan,
        *,
        progress_callback: ProgressCallback,
    ) -> list[ScanResult]:
        return self.service.repair_failed_candidate(
            run_id=plan.requested_run_id,
            candidate_id=str(plan.candidate_id),
            question_id=plan.question_id,
            progress_callback=progress_callback,
            repair_plan=plan,
            retain_finalizing_state=True,
        )

    def plan_batch(
        self,
        *,
        run_id: str,
        candidate_ids: list[str],
        timeouts_only: bool,
    ) -> RepairPlan:
        planner = (
            self.service.repair_planner.plan_timeout_batch
            if timeouts_only
            else self.service.repair_planner.plan_failed_batch
        )
        return planner(run_id=run_id, candidate_ids=candidate_ids)

    def execute_batch(
        self,
        plan: RepairPlan,
        *,
        timeouts_only: bool,
        progress_callback: ProgressCallback,
    ) -> list[ScanResult]:
        repair = (
            self.service.repair_timed_out_questions
            if timeouts_only
            else self.service.repair_failed_questions
        )
        return repair(
            run_id=plan.requested_run_id,
            candidate_ids=list(plan.selected_candidate_ids),
            progress_callback=progress_callback,
            repair_plan=plan,
            retain_finalizing_state=True,
        )

    def stream_candidate_events(
        self,
        *,
        run_id: str,
        candidate_id: str,
        question_id: str | None,
        expected_resume_run_id: str | None = None,
        process_lock: ProcessLock,
        snapshot_builder: SnapshotBuilder,
        terminal_snapshot_builder: SnapshotBuilder | None = None,
        codex_insights_provider: Callable[..., dict[str, object]] | None = None,
        prepare_execution: Callable[[RepairPlan], object] | None = None,
    ) -> Iterator[dict[str, object]]:
        def started_fields(plan: RepairPlan) -> tuple[dict[str, object], int]:
            repairable_question_ids = [
                question.id for question in plan.steps_for(candidate_id)
            ]
            return (
                {
                    "candidate_id": candidate_id,
                    "repairable_question_ids": repairable_question_ids,
                },
                len(repairable_question_ids),
            )

        yield from _stream_repair_events(
            self.service,
            run_id=run_id,
            event_prefix="repair",
            target_fields={"candidate_id": candidate_id},
            plan_builder=lambda: self.plan_candidate(
                run_id=run_id,
                candidate_id=candidate_id,
                question_id=question_id,
            ),
            started_fields_builder=started_fields,
            execute=lambda plan, progress: self.execute_candidate(
                plan,
                progress_callback=progress,
            ),
            expected_resume_run_id=expected_resume_run_id,
            process_lock=process_lock,
            snapshot_builder=snapshot_builder,
            terminal_snapshot_builder=terminal_snapshot_builder,
            codex_insights_provider=codex_insights_provider,
            prepare_execution=prepare_execution,
        )

    def stream_batch_events(
        self,
        *,
        run_id: str,
        candidate_ids: list[str],
        timeouts_only: bool,
        expected_resume_run_id: str | None = None,
        process_lock: ProcessLock,
        snapshot_builder: SnapshotBuilder,
        terminal_snapshot_builder: SnapshotBuilder | None = None,
        codex_insights_provider: Callable[..., dict[str, object]] | None = None,
        prepare_execution: Callable[[RepairPlan], object] | None = None,
    ) -> Iterator[dict[str, object]]:
        event_prefix = "timeout-repair" if timeouts_only else "repair"
        yield from _stream_repair_events(
            self.service,
            run_id=run_id,
            event_prefix=event_prefix,
            target_fields={"candidate_ids": candidate_ids},
            plan_builder=lambda: self.plan_batch(
                run_id=run_id,
                candidate_ids=candidate_ids,
                timeouts_only=timeouts_only,
            ),
            started_fields_builder=lambda plan: (
                {"candidate_ids": list(plan.selected_candidate_ids)},
                plan.total_steps,
            ),
            execute=lambda plan, progress: self.execute_batch(
                plan,
                timeouts_only=timeouts_only,
                progress_callback=progress,
            ),
            expected_resume_run_id=expected_resume_run_id,
            process_lock=process_lock,
            snapshot_builder=snapshot_builder,
            terminal_snapshot_builder=terminal_snapshot_builder,
            codex_insights_provider=codex_insights_provider,
            prepare_execution=prepare_execution,
        )


@dataclass(frozen=True)
class AutoResumeExecutionRouter:
    service: MonitorService
    snapshot_builder: SnapshotBuilder
    terminal_snapshot_builder: SnapshotBuilder
    codex_insights_provider: Callable[..., dict[str, object]] | None = None
    prepare_scan_execution: Callable[[ScanPlan], object] | None = None
    prepare_repair_execution: Callable[[RepairPlan], object] | None = None
    log: LogCallback = _ignore_log

    def stream(
        self,
        claim: AutoResumeClaim,
        *,
        process_lock: ProcessLock,
    ) -> Iterator[dict[str, object]]:
        if claim.operation_kind == "candidate_repair":
            yield from RepairCommand(self.service).stream_candidate_events(
                run_id=claim.operation_run_id,
                candidate_id=claim.candidate_ids[0],
                question_id=claim.question_id,
                expected_resume_run_id=claim.run_id,
                process_lock=process_lock,
                snapshot_builder=self.snapshot_builder,
                terminal_snapshot_builder=self.terminal_snapshot_builder,
                codex_insights_provider=self.codex_insights_provider,
                prepare_execution=self.prepare_repair_execution,
            )
            return
        if claim.operation_kind in {"failed_repair", "timeout_repair"}:
            yield from RepairCommand(self.service).stream_batch_events(
                run_id=claim.operation_run_id,
                candidate_ids=list(claim.candidate_ids),
                timeouts_only=claim.operation_kind == "timeout_repair",
                expected_resume_run_id=claim.run_id,
                process_lock=process_lock,
                snapshot_builder=self.snapshot_builder,
                terminal_snapshot_builder=self.terminal_snapshot_builder,
                codex_insights_provider=self.codex_insights_provider,
                prepare_execution=self.prepare_repair_execution,
            )
            return
        yield from ScanCommand(self.service).stream_events(
            force_restart=False,
            requested_candidate_ids=None,
            selection_mode="regular",
            custom_round_mode="new_round",
            evaluation_profile_id=None,
            upgrade_from_run_id=None,
            expected_resume_run_id=claim.run_id,
            process_lock=process_lock,
            snapshot_builder=self.snapshot_builder,
            terminal_snapshot_builder=self.terminal_snapshot_builder,
            codex_insights_provider=self.codex_insights_provider,
            prepare_execution=self.prepare_scan_execution,
            log=self.log,
        )
