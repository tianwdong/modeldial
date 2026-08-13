from __future__ import annotations

import ast
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

from scanner.active_run_store import ActiveRunStore
from scanner.comparison_groups import ComparisonGroupProjector
from scanner.config_store import ConfigStore
from scanner.history_store import HistoryStore
from scanner.models import AppConfig, ScanResult
from scanner.monitor_state_projection import MonitorStateProjector
from scanner.question_bank import QuestionBank
from scanner.scan_target_resolver import ScanTargetResolver
from scanner.service import MonitorService


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _current_model_payload() -> dict[str, object]:
    return {
        "effective_candidate_id": None,
        "source": "none",
        "detection_status": "unavailable",
        "detected_at": None,
        "model": None,
        "effort": None,
        "active_session_count": 0,
        "active_models": [],
        "active_sessions": [],
        "display_sessions": [],
        "active_configuration_sessions": [],
    }


class MonitorStateProjectorTest(unittest.TestCase):
    def test_independent_projector_matches_service_facade(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = MonitorService(
                config_store=ConfigStore(
                    root / "config.json",
                    first_run_defaults=True,
                ),
                history_store=HistoryStore(root / "history.jsonl"),
                active_run_store=ActiveRunStore(root / "active_run.json"),
                current_model_detector=lambda: None,
                active_session_detector=lambda: (),
            )
            projector = MonitorStateProjector(
                config_store=service.config_store,
                history_store=service.history_store,
                active_run_store=service.active_run_store,
                question_bank=service.question_bank,
                current_model_context_query=service.current_model_context_query,
                runtime_snapshot_projector=service.runtime_snapshot_projector,
                scan_planner=service.scan_planner,
                comparison_group_projector=service.comparison_group_projector,
            )

            self.assertEqual(projector.build_state(), service.build_state())
            self.assertEqual(
                projector.build_refresh_state(),
                service.build_refresh_state(),
            )
            self.assertIs(
                service.monitor_state_projector.config_store,
                service.config_store,
            )
            self.assertIs(
                service.monitor_state_projector.history_store,
                service.history_store,
            )
            self.assertIs(
                service.monitor_state_projector.active_run_store,
                service.active_run_store,
            )
            self.assertIs(
                service.monitor_state_projector.question_bank,
                service.question_bank,
            )
            self.assertIs(
                service.monitor_state_projector.current_model_context_query,
                service.current_model_context_query,
            )
            self.assertIs(
                service.monitor_state_projector.runtime_snapshot_projector,
                service.runtime_snapshot_projector,
            )
            self.assertIs(
                service.monitor_state_projector.scan_planner,
                service.scan_planner,
            )
            self.assertIs(
                service.monitor_state_projector.comparison_group_projector,
                service.comparison_group_projector,
            )

    def test_comparison_group_projection_preserves_route_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = MonitorService(
                config_store=ConfigStore(root / "config.json"),
                history_store=HistoryStore(root / "history.jsonl"),
                active_run_store=ActiveRunStore(root / "active_run.json"),
            )
            run_id = "run-route-evidence"
            result = ScanResult(
                run_id=run_id,
                candidate_id="endpoint:test-model:low",
                model="test-model",
                effort="low",
                phase="scan",
                question_id="q1",
                started_at="2026-08-11T17:20:00+08:00",
                elapsed_seconds=1.0,
                source_mode="api",
                answer_ok=True,
                answer_preview="ok",
                input_tokens=10,
                output_tokens=5,
                reasoning_tokens=None,
                reasoning_tokens_supported=False,
                evaluation_id="evaluation-route-evidence",
                execution_trace={"route_fingerprint": "route-v1:sha256:test"},
            )
            metadata = {
                "run_id": run_id,
                "selection_mode": "regular",
                "comparison_group_id": run_id,
                "comparison_group_mode": "regular",
            }

            history, current_run_id, _ = (
                service.monitor_state_projector.dashboard_history_context(
                    history=[result],
                    run_metadata_by_id={run_id: metadata},
                    current_run_id=run_id,
                )
            )

        self.assertEqual(current_run_id, run_id)
        self.assertEqual(len(history), 1)
        self.assertEqual(
            history[0].execution_trace,
            {"route_fingerprint": "route-v1:sha256:test"},
        )
        self.assertEqual(history[0].evaluation_id, "evaluation-route-evidence")
        self.assertFalse(history[0].reasoning_tokens_supported)

    def test_full_and_refresh_reads_keep_the_existing_order(self) -> None:
        events: list[str] = []
        config = AppConfig.default()
        question_pack = QuestionBank(PROJECT_ROOT / "questions").load()
        config_store = MagicMock()
        history_store = MagicMock()
        active_run_store = MagicMock()
        question_bank = MagicMock()
        current_model_context_query = MagicMock()
        runtime_snapshot_projector = MagicMock()
        scan_planner = MagicMock()

        config_store.load.side_effect = lambda: (
            events.append("config.load") or config
        )
        history_store.load_all.side_effect = lambda: (
            events.append("history.load_all") or []
        )
        history_store.load_run_metadata_map.side_effect = lambda: (
            events.append("history.load_run_metadata_map") or {}
        )
        history_store.load_recent_with_count.side_effect = lambda **_kwargs: (
            events.append("history.load_recent_with_count") or ([], 0)
        )
        active_run_store.load.side_effect = lambda: (
            events.append("active_run.load") or None
        )
        question_bank.load.side_effect = lambda: (
            events.append("question_bank.load") or question_pack
        )
        current_model_context_query.build.side_effect = lambda _config: (
            events.append("current_model.build") or _current_model_payload()
        )
        runtime_snapshot_projector.project.side_effect = (
            lambda *_args, **_kwargs: (
                events.append("runtime.project") or {"current_run_id": None}
            )
        )
        scan_planner.infer_active_run_from_history.side_effect = (
            lambda *_args, **_kwargs: (
                events.append("scan_planner.infer") or None
            )
        )
        projector = MonitorStateProjector(
            config_store=config_store,
            history_store=history_store,
            active_run_store=active_run_store,
            question_bank=question_bank,
            current_model_context_query=current_model_context_query,
            runtime_snapshot_projector=runtime_snapshot_projector,
            scan_planner=scan_planner,
            comparison_group_projector=ComparisonGroupProjector(
                ScanTargetResolver()
            ),
        )

        projector.build_state()
        self.assertEqual(
            events,
            [
                "config.load",
                "current_model.build",
                "history.load_all",
                "active_run.load",
                "scan_planner.infer",
                "runtime.project",
                "history.load_run_metadata_map",
                "question_bank.load",
                "question_bank.load",
            ],
        )

        events.clear()
        projector.build_refresh_state()
        self.assertEqual(
            events,
            [
                "config.load",
                "question_bank.load",
                "current_model.build",
                "history.load_recent_with_count",
                "active_run.load",
                "scan_planner.infer",
                "runtime.project",
            ],
        )

    def test_projector_has_no_service_dependency_or_repository_writes(self) -> None:
        projector_path = PROJECT_ROOT / "scanner" / "monitor_state_projection.py"
        tree = ast.parse(projector_path.read_text(encoding="utf-8"))
        imported_modules = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        referenced_names = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        }
        self.assertNotIn("scanner.service", imported_modules)
        self.assertNotIn("service", imported_modules)
        self.assertNotIn("MonitorService", referenced_names)

        forbidden_write_methods = {
            "append",
            "delete",
            "mutate",
            "refresh_runtime_lease",
            "save",
            "write",
        }
        repository_names = {
            "active_run_store",
            "config_store",
            "history_store",
        }
        writes = sorted(
            f"{node.func.value.attr}.{node.func.attr}@{node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Attribute)
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == "self"
            and node.func.value.attr in repository_names
            and node.func.attr in forbidden_write_methods
        )
        self.assertEqual(writes, [])

    def test_service_state_entrypoints_are_single_delegates(self) -> None:
        service_path = PROJECT_ROOT / "scanner" / "service.py"
        tree = ast.parse(service_path.read_text(encoding="utf-8"))
        service = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "MonitorService"
        )
        methods = {
            node.name: node
            for node in service.body
            if isinstance(node, ast.FunctionDef)
        }
        for method_name in ("build_state", "build_refresh_state"):
            with self.subTest(method_name=method_name):
                method = methods[method_name]
                self.assertEqual(len(method.body), 1)
                statement = method.body[0]
                self.assertIsInstance(statement, ast.Return)
                value = statement.value
                self.assertIsInstance(value, ast.Call)
                function = value.func
                self.assertIsInstance(function, ast.Attribute)
                self.assertEqual(function.attr, method_name)
                owner = function.value
                self.assertIsInstance(owner, ast.Attribute)
                self.assertIsInstance(owner.value, ast.Name)
                self.assertEqual(owner.value.id, "self")
                self.assertEqual(owner.attr, "monitor_state_projector")


if __name__ == "__main__":
    unittest.main()
