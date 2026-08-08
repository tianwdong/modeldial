from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from scanner.active_run_store import ActiveRunStore
from scanner.comparison_groups import ComparisonGroupProjector
from scanner.history_store import HistoryStore
from scanner.models import AppConfig, ResolvedScanTarget, ScanResult
from scanner.runtime_snapshot_projection import RuntimeSnapshotProjector
from scanner.scan_target_resolver import ScanTargetResolver


ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-07-29T12:00:00+00:00"
NOW_TIMESTAMP = datetime.fromisoformat(NOW).timestamp()


def _runtime_state(**overrides: object) -> dict[str, object]:
    state: dict[str, object] = {
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
        "state_changed_at": NOW,
        "finalizing_started_at": None,
        "last_phase": None,
        "last_phase_completed": 0,
        "last_phase_total": 0,
        "updated_at": NOW,
        "lease_expires_at": None,
        "current_phase": None,
        "progress_completed": 0,
        "progress_total": 0,
        "active_evaluation_count": 0,
        "queued_evaluation_count": 0,
        "oldest_active_evaluation_started_at": None,
    }
    state.update(overrides)
    return state


def _enable_only(config: AppConfig, candidate_ids: set[str]) -> None:
    for connection in config.model_ingress.connections:
        for candidate in connection.model_candidates:
            candidate.enabled = candidate.id in candidate_ids


def _entry(
    target: ResolvedScanTarget,
    *,
    attempts_completed: int,
    attempts_total: int,
    phase: str = "scan",
    status: str = "pending",
) -> dict[str, object]:
    return {
        "candidate_id": target.candidate_id,
        "model": target.model,
        "effort": target.effort,
        "label": target.label,
        "status": status,
        "final_status": None,
        "reasoning_tokens": None,
        "attempts_completed": attempts_completed,
        "attempts_per_target": attempts_total,
        "phase": phase,
        "flags": [],
        "error_message": None,
    }


def _result(
    target: ResolvedScanTarget,
    *,
    run_id: str,
    question_id: str,
    started_at: str,
    phase: str = "scan",
) -> ScanResult:
    return ScanResult(
        run_id=run_id,
        candidate_id=target.candidate_id,
        model=target.model,
        effort=target.effort,
        phase=phase,
        question_id=question_id,
        question_title=question_id,
        grader_kind="regex",
        attempt_index=1,
        started_at=started_at,
        elapsed_seconds=1.0,
        source_mode="live",
        answer_ok=True,
        answer_preview="ok",
        input_tokens=10,
        output_tokens=2,
        reasoning_tokens=430,
        final_status="pass",
    )


def _projector(
    root: Path,
    *,
    runtime_state: dict[str, object] | None = None,
    scan_lock_active: bool = False,
    stale_seconds: int = 420,
) -> tuple[
    RuntimeSnapshotProjector,
    HistoryStore,
    ActiveRunStore,
    Mock,
]:
    history_store = HistoryStore(root / "history.jsonl")
    active_run_store = ActiveRunStore(root / "active_run.json")
    target_resolver = ScanTargetResolver()
    scan_lock_probe = Mock(return_value=scan_lock_active)
    projector = RuntimeSnapshotProjector(
        runtime_state=runtime_state or _runtime_state(),
        history_store=history_store,
        active_run_store=active_run_store,
        target_resolver=target_resolver,
        comparison_group_projector=ComparisonGroupProjector(target_resolver),
        scan_lock_is_active=scan_lock_probe,
        stale_seconds=stale_seconds,
        clock=lambda: NOW_TIMESTAMP,
    )
    return projector, history_store, active_run_store, scan_lock_probe


class RuntimeSnapshotProjectorTest(unittest.TestCase):
    def test_idle_snapshot_keeps_the_complete_runtime_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            projector, _, _, scan_lock_probe = _projector(root)

            runtime = projector.project(
                AppConfig.default(),
                [],
                None,
                history_count=17,
            )

        self.assertEqual(
            runtime,
            {
                "enabled_target_count": 6,
                "history_count": 17,
                "is_running": False,
                "last_run_count": 0,
                "last_error": None,
                "last_run_mode": "live",
                "completed_targets": 0,
                "total_targets": 0,
                "progress_percent": 0,
                "current_target": None,
                "run_entries": [],
                "current_run_id": None,
                "has_resumable_run": False,
                "resumable_run_id": None,
                "resumable_operation_kind": None,
                "resumable_operation_run_id": None,
                "resumable_candidate_ids": [],
                "resumable_question_id": None,
                "current_phase": None,
                "current_phase_completed_targets": 0,
                "current_phase_total_targets": 0,
                "progress_completed": 0,
                "progress_total": 0,
                "progress_unit": "evaluationUnit",
                "active_evaluation_count": 0,
                "queued_evaluation_count": 0,
                "oldest_active_evaluation_started_at": None,
                "execution_timeout_seconds": 1200,
                "lifecycle_state": "idle",
                "state_changed_at": NOW,
                "finalizing_started_at": None,
                "last_phase": None,
                "last_phase_completed": 0,
                "last_phase_total": 0,
                "updated_at": NOW,
                "lease_expires_at": None,
            },
        )
        scan_lock_probe.assert_called_once_with(root / "scan.lock")

    def test_live_runtime_state_wins_without_a_persisted_run(self) -> None:
        live_entry = {
            "candidate_id": "candidate-live",
            "status": "running",
            "phase": "scan",
            "attempts_completed": 2,
            "attempts_per_target": 5,
        }
        state = _runtime_state(
            is_running=True,
            last_run_count=3,
            completed_targets=2,
            total_targets=5,
            current_target="gpt-5.5 / high · 扫描 3/5",
            run_entries=[live_entry],
            current_run_id="run-live",
            lifecycle_state="active_scan",
            current_phase="scan",
            progress_completed=2,
            progress_total=5,
            active_evaluation_count=1,
            queued_evaluation_count=2,
            oldest_active_evaluation_started_at="2026-07-29T11:59:30+00:00",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            projector, _, _, _ = _projector(
                Path(temp_dir),
                runtime_state=state,
            )

            runtime = projector.project(AppConfig.default(), [], None)

        self.assertTrue(runtime["is_running"])
        self.assertEqual(runtime["current_run_id"], "run-live")
        self.assertEqual(runtime["progress_percent"], 40)
        self.assertEqual(runtime["current_phase"], "scan")
        self.assertEqual(runtime["current_phase_completed_targets"], 2)
        self.assertEqual(runtime["current_phase_total_targets"], 5)
        self.assertEqual(runtime["active_evaluation_count"], 1)
        self.assertEqual(runtime["queued_evaluation_count"], 2)
        self.assertIsNot(runtime["run_entries"][0], live_entry)

    def test_stale_persisted_run_ignores_a_live_lock_and_becomes_recoverable(
        self,
    ) -> None:
        config = AppConfig.default()
        target = ScanTargetResolver().enabled_targets(config)[0]
        history = [
            _result(
                target,
                run_id="run-stale",
                question_id="q1",
                started_at="2026-07-29T11:50:00+00:00",
            )
        ]
        active_run = {
            "run_id": "run-stale",
            "run_metadata": {
                "run_id": "run-stale",
                "started_at": "2026-07-29T11:49:00+00:00",
            },
            "runtime": {
                "lifecycle_state": "active_scan",
                "current_phase": "scan",
                "progress_completed": 1,
                "progress_total": 2,
                "active_evaluation_count": 1,
                "queued_evaluation_count": 1,
            },
            "planned_attempts_by_candidate": {target.candidate_id: 2},
            "entries": [
                _entry(
                    target,
                    attempts_completed=0,
                    attempts_total=2,
                    status="running",
                )
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            projector, _, _, scan_lock_probe = _projector(
                Path(temp_dir),
                scan_lock_active=True,
            )

            runtime = projector.project(config, history, active_run)

        scan_lock_probe.assert_called_once()
        self.assertFalse(runtime["is_running"])
        self.assertEqual(runtime["lifecycle_state"], "paused_recoverable")
        self.assertTrue(runtime["has_resumable_run"])
        self.assertEqual(runtime["completed_targets"], 1)
        self.assertEqual(runtime["total_targets"], 2)
        self.assertEqual(runtime["run_entries"][0]["status"], "interrupted")
        self.assertEqual(runtime["active_evaluation_count"], 0)
        self.assertEqual(runtime["queued_evaluation_count"], 0)

    def test_append_projection_counts_the_complete_comparison_group(self) -> None:
        config = AppConfig.default()
        configured_targets = ScanTargetResolver().configured_targets(config)
        target_a, target_b = configured_targets[:2]
        _enable_only(config, {target_a.candidate_id, target_b.candidate_id})
        history = [
            _result(
                target_a,
                run_id="run-base",
                question_id="q1",
                started_at="2026-07-29T11:55:00+00:00",
            ),
            _result(
                target_a,
                run_id="run-base",
                question_id="q2",
                started_at="2026-07-29T11:56:00+00:00",
            ),
            _result(
                target_b,
                run_id="run-append",
                question_id="q1",
                started_at="2026-07-29T11:59:00+00:00",
            ),
        ]
        active_metadata = {
            "run_id": "run-append",
            "started_at": "2026-07-29T11:58:00+00:00",
            "comparison_group_id": "run-base",
            "comparison_group_mode": "custom_append",
        }
        active_run = {
            "run_id": "run-append",
            "run_metadata": active_metadata,
            "runtime": {
                "lifecycle_state": "paused_recoverable",
                "current_phase": "scan",
                "progress_completed": 3,
                "progress_total": 4,
            },
            "planned_attempts_by_candidate": {
                target_a.candidate_id: 2,
                target_b.candidate_id: 2,
            },
            "entries": [
                _entry(target_a, attempts_completed=2, attempts_total=2),
                _entry(target_b, attempts_completed=1, attempts_total=2),
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            projector, history_store, _, _ = _projector(Path(temp_dir))
            history_store.save_run_metadata(
                {
                    "run_id": "run-base",
                    "comparison_group_id": "run-base",
                    "comparison_group_mode": "regular",
                }
            )

            runtime = projector.project(config, history, active_run)

        entries = {
            str(entry["candidate_id"]): entry for entry in runtime["run_entries"]
        }
        self.assertEqual(runtime["completed_targets"], 3)
        self.assertEqual(runtime["total_targets"], 4)
        self.assertEqual(runtime["progress_percent"], 75)
        self.assertTrue(runtime["has_resumable_run"])
        self.assertEqual(entries[target_a.candidate_id]["attempts_completed"], 2)
        self.assertEqual(entries[target_a.candidate_id]["status"], "done")
        self.assertEqual(entries[target_b.candidate_id]["attempts_completed"], 1)
        self.assertEqual(entries[target_b.candidate_id]["status"], "interrupted")

    def test_repair_projection_restores_resume_identity_and_phase_progress(self) -> None:
        config = AppConfig.default()
        target = ScanTargetResolver().enabled_targets(config)[0]
        active_run = {
            "run_id": "run-repair-checkpoint",
            "run_metadata": {
                "run_id": "run-repair-checkpoint",
                "started_at": "2026-07-29T11:59:00+00:00",
            },
            "runtime": {"lifecycle_state": "paused_recoverable"},
            "repair_operation_kind": "candidate_repair",
            "repair_operation_run_id": "run-original",
            "repair_candidate_id": target.candidate_id,
            "repair_question_id": "q2",
            "entries": [
                _entry(
                    target,
                    attempts_completed=1,
                    attempts_total=2,
                    phase="repair",
                    status="interrupted",
                )
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            projector, _, _, _ = _projector(Path(temp_dir))

            runtime = projector.project(config, [], active_run)

        self.assertTrue(runtime["has_resumable_run"])
        self.assertEqual(runtime["resumable_run_id"], "run-repair-checkpoint")
        self.assertEqual(runtime["resumable_operation_kind"], "candidate_repair")
        self.assertEqual(runtime["resumable_operation_run_id"], "run-original")
        self.assertEqual(
            runtime["resumable_candidate_ids"],
            [target.candidate_id],
        )
        self.assertEqual(runtime["resumable_question_id"], "q2")
        self.assertEqual(runtime["current_phase"], "repair")
        self.assertEqual(runtime["current_phase_completed_targets"], 1)
        self.assertEqual(runtime["current_phase_total_targets"], 2)
        self.assertEqual(runtime["progress_completed"], 1)
        self.assertEqual(runtime["progress_total"], 2)

    def test_empty_entries_preserve_legacy_finalizing_progress(self) -> None:
        config = AppConfig.default()
        active_run = {
            "run_id": "run-empty-finalizing",
            "run_metadata": {
                "run_id": "run-empty-finalizing",
                "completed_at": "2026-07-29T11:59:00+00:00",
            },
            "runtime": {
                "lifecycle_state": "finalizing",
                "state_changed_at": "2026-07-29T11:59:00+00:00",
                "finalizing_started_at": "2026-07-29T11:59:00+00:00",
                "updated_at": "2026-07-29T11:59:30+00:00",
                "current_phase": "repair",
                "progress_completed": 1,
                "progress_total": 1,
                "last_error": "projection failed",
                "active_evaluation_count": 2,
            },
            "entries": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            projector, _, _, _ = _projector(
                Path(temp_dir),
                scan_lock_active=True,
            )

            runtime = projector.project(config, [], active_run)

        self.assertFalse(runtime["is_running"])
        self.assertEqual(runtime["lifecycle_state"], "finalizing")
        self.assertEqual(runtime["current_run_id"], "run-empty-finalizing")
        self.assertEqual(runtime["current_phase"], "repair")
        self.assertEqual(runtime["progress_completed"], 1)
        self.assertEqual(runtime["progress_total"], 1)
        self.assertEqual(runtime["last_error"], "projection failed")
        self.assertEqual(runtime["active_evaluation_count"], 0)


class RuntimeSnapshotProjectionArchitectureTest(unittest.TestCase):
    def test_projector_has_no_monitor_service_dependency_or_write_calls(self) -> None:
        path = ROOT / "scanner" / "runtime_snapshot_projection.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        names = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        }
        called_attributes = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }

        self.assertNotIn("service", imported_modules)
        self.assertNotIn("MonitorService", names)
        self.assertTrue(
            called_attributes.isdisjoint(
                {
                    "clear",
                    "mutate",
                    "save",
                    "unlink",
                    "write_bytes",
                    "write_text",
                }
            )
        )

    def test_monitor_service_keeps_only_the_projection_delegate(self) -> None:
        path = ROOT / "scanner" / "service.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        monitor_service = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "MonitorService"
        )
        method = next(
            node
            for node in monitor_service.body
            if isinstance(node, ast.FunctionDef) and node.name == "_snapshot_runtime"
        )

        self.assertEqual(len(method.body), 1)
        self.assertIsInstance(method.body[0], ast.Return)
        self.assertEqual(
            ast.unparse(method.body[0].value),
            "self.runtime_snapshot_projector.project(config, history, active_run, history_count=history_count)",
        )
        service_methods = {
            node.name
            for node in monitor_service.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertTrue(
            service_methods.isdisjoint(
                {
                    "_current_phase_progress",
                    "_run_progress_is_stale",
                    "_resumable_repair_candidate_ids",
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
