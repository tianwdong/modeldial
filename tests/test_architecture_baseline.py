from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import scanner.native_bridge as native_bridge_module
from scanner.active_run_store import ActiveRunStore
from scanner.config_store import ConfigStore
from scanner.history_store import HistoryStore
from scanner.protocol import project_runtime_event_v1


class ArchitectureBaselineFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture_dir = Path(__file__).resolve().parent / "fixtures"
        cls.app_snapshot = cls._load("architecture_app_snapshot_v2.json")
        cls.refresh_snapshot = cls._load("architecture_refresh_snapshot_v1.json")
        cls.scan_events = cls._load("architecture_scan_events_v1.json")
        cls.repair_events = cls._load("architecture_repair_events_v1.json")
        cls.failed_batch_events = cls._load(
            "architecture_failed_batch_events_v1.json"
        )
        cls.timeout_batch_events = cls._load(
            "architecture_timeout_batch_events_v1.json"
        )
        cls.batch_failure_events = cls._load(
            "architecture_batch_failure_events_v1.json"
        )

    @classmethod
    def _load(cls, name: str) -> object:
        return json.loads((cls.fixture_dir / name).read_text(encoding="utf-8"))

    def test_snapshot_and_refresh_envelopes_stay_distinct(self) -> None:
        self.assertEqual(
            set(self.app_snapshot),
            {
                "schema_version",
                "config",
                "dashboard",
                "stable_dashboard",
                "runtime",
                "question_pack",
                "settings_projection",
                "codex_insights",
                "advisor",
                "diagnostics",
                "advisor_v2_evidence",
                "recommendation_portfolio_v2",
                "reference_snapshot_feed",
                "recommendation_use",
            },
        )
        self.assertEqual(self.app_snapshot["schema_version"], 2)
        self.assertEqual(
            set(self.refresh_snapshot),
            {
                "schema_version",
                "config",
                "runtime",
                "question_pack",
                "recommendation_use",
            },
        )
        self.assertEqual(self.refresh_snapshot["schema_version"], 1)
        self.assertNotIn("dashboard", self.refresh_snapshot)
        self.assertNotIn("reference_snapshot_feed", self.refresh_snapshot)

    def test_scan_event_fixture_preserves_delta_and_terminal_boundaries(self) -> None:
        self.assertEqual(
            [event["type"] for event in self.scan_events],
            [
                "scan.started",
                "target.started",
                "scan.progress",
                "scan.finalizing",
                "scan.finished",
            ],
        )
        self._assert_event_state_boundaries(
            self.scan_events,
            expected_progress_history_count=0,
            expected_terminal_history_count=1,
        )
        started_runtime = self.scan_events[0]["state"]["runtime"]
        self.assertEqual(self.scan_events[0]["state_kind"], "runtime_delta")
        self.assertTrue(started_runtime["is_running"])
        self.assertEqual(started_runtime["lifecycle_state"], "active_scan")
        self.assertEqual(started_runtime["progress_completed"], 0)
        self.assertEqual(started_runtime["progress_total"], 1)
        self.assertEqual(self.scan_events[-1]["result_count"], 1)
        self.assertNotIn("completed_targets", self.scan_events[-1])

    def test_repair_event_fixture_preserves_delta_and_terminal_boundaries(self) -> None:
        self.assertEqual(
            [event["type"] for event in self.repair_events],
            [
                "repair.started",
                "repair.question.started",
                "repair.question.finished",
                "repair.finalizing",
                "repair.finished",
            ],
        )
        self._assert_event_state_boundaries(
            self.repair_events,
            expected_progress_history_count=1,
            expected_terminal_history_count=2,
        )
        started_runtime = self.repair_events[0]["state"]["runtime"]
        self.assertEqual(self.repair_events[0]["state_kind"], "runtime_delta")
        self.assertTrue(started_runtime["is_running"])
        self.assertEqual(started_runtime["current_phase"], "repair")
        self.assertEqual(started_runtime["progress_completed"], 0)
        self.assertEqual(started_runtime["progress_total"], 1)
        self.assertEqual(self.repair_events[-2]["state_kind"], "runtime_delta")
        self.assertEqual(
            self.repair_events[-2]["state"]["runtime"]["lifecycle_state"],
            "finalizing",
        )
        self.assertEqual(self.repair_events[-1]["result_count"], 1)
        self.assertNotIn("completed_targets", self.repair_events[-1])

    def test_batch_repair_fixtures_cover_both_runtime_event_families(self) -> None:
        for events, prefix in (
            (self.failed_batch_events, "repair"),
            (self.timeout_batch_events, "timeout-repair"),
        ):
            with self.subTest(prefix=prefix):
                self.assertEqual(
                    [event["type"] for event in events],
                    [
                        f"{prefix}.started",
                        f"{prefix}.question.started",
                        f"{prefix}.question.finished",
                        f"{prefix}.finalizing",
                        f"{prefix}.finished",
                    ],
                )
                self._assert_event_state_boundaries(
                    events,
                    expected_progress_history_count=1,
                    expected_terminal_history_count=2,
                )
                for event in events:
                    raw_event = {
                        key: value
                        for key, value in event.items()
                        if key not in {"schema_version", "state_kind"}
                    }
                    projected = project_runtime_event_v1(raw_event)
                    self.assertEqual(
                        projected["state_kind"],
                        event["state_kind"],
                    )

        repair_failure, timeout_failure = self.batch_failure_events
        self.assertEqual(repair_failure["type"], "repair.failed")
        self.assertEqual(repair_failure["state_kind"], "snapshot")
        self.assertEqual(
            repair_failure["state"]["runtime"]["lifecycle_state"],
            "failed",
        )
        self.assertEqual(timeout_failure["type"], "timeout-repair.failed")
        self.assertEqual(timeout_failure["state_kind"], "snapshot")
        self.assertEqual(
            timeout_failure["state"]["runtime"]["lifecycle_state"],
            "idle",
        )
        for event in self.batch_failure_events:
            raw_event = {
                key: value
                for key, value in event.items()
                if key not in {"schema_version", "state_kind"}
            }
            self.assertEqual(
                project_runtime_event_v1(raw_event)["state_kind"],
                event["state_kind"],
            )

    def test_architecture_fixtures_contain_no_secret_material(self) -> None:
        fixture_text = "\n".join(
            json.dumps(payload, ensure_ascii=False, sort_keys=True)
            for payload in (
                self.app_snapshot,
                self.refresh_snapshot,
                self.scan_events,
                self.repair_events,
                self.failed_batch_events,
                self.timeout_batch_events,
                self.batch_failure_events,
            )
        ).lower()
        for forbidden in ("api_key", "keychain:", "local_encrypted:", "bearer "):
            self.assertNotIn(forbidden, fixture_text)

    def test_swift_decodes_versioned_snapshot_and_runtime_event_fixtures(self) -> None:
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "architecture-fixture-tests"
            compile_result = subprocess.run(
                [
                    "swiftc",
                    "-module-cache-path",
                    str(Path(temp_dir) / "module-cache"),
                    "Sources/Model/LocalEncryptedSecretStore.swift",
                    "Sources/Model/SelectionModels.swift",
                    "tests/swift/ArchitectureFixtureDecodingTests.swift",
                    "-o",
                    str(executable),
                ],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            data_dir = Path(temp_dir) / "data"
            observation_response = Path(temp_dir) / "observe-state.json"
            with patch.object(
                native_bridge_module,
                "_observe_session_context",
                return_value={
                    "codex_session_count": 0,
                    "external_session_count": 0,
                },
            ):
                response = native_bridge_module.observe_state(
                    ConfigStore(
                        data_dir / "config.json",
                        first_run_defaults=True,
                    ),
                    HistoryStore(data_dir / "history.jsonl"),
                    ActiveRunStore(data_dir / "active_run.json"),
                )
            observation_response.write_text(
                json.dumps(response, ensure_ascii=False),
                encoding="utf-8",
            )
            run_result = subprocess.run(
                [str(executable), str(observation_response)],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(run_result.returncode, 0, run_result.stderr)
            self.assertIn(
                "Architecture fixture decoding tests passed",
                run_result.stdout,
            )

    def _assert_event_state_boundaries(
        self,
        events: list[dict[str, object]],
        *,
        expected_progress_history_count: int,
        expected_terminal_history_count: int,
    ) -> None:
        for event in events:
            self.assertEqual(event["schema_version"], 1)
            self.assertIn(
                event["state_kind"],
                {"none", "runtime_delta", "snapshot"},
            )
        progress_states = [
            event["state"]
            for event in events[:-2]
            if isinstance(event.get("state"), dict)
        ]
        self.assertTrue(progress_states)
        for state in progress_states:
            self.assertEqual(set(state), {"schema_version", "runtime"})
            self.assertEqual(state["schema_version"], 1)
            self.assertTrue(state["runtime"]["is_running"])
            self.assertEqual(
                state["runtime"]["history_count"],
                expected_progress_history_count,
            )

        finalizing_event = events[-2]
        self.assertEqual(finalizing_event["state_kind"], "runtime_delta")
        finalizing_state = finalizing_event["state"]
        self.assertEqual(set(finalizing_state), {"schema_version", "runtime"})
        self.assertEqual(finalizing_state["schema_version"], 1)
        self.assertFalse(finalizing_state["runtime"]["is_running"])
        self.assertEqual(
            finalizing_state["runtime"]["lifecycle_state"],
            "finalizing",
        )
        self.assertEqual(
            finalizing_state["runtime"]["history_count"],
            expected_progress_history_count,
        )
        self.assertEqual(events[-1]["state_kind"], "snapshot")
        terminal_state = events[-1]["state"]
        self.assertEqual(set(terminal_state), set(self.app_snapshot))
        terminal_runtime = terminal_state["runtime"]
        self.assertFalse(terminal_runtime["is_running"])
        self.assertEqual(terminal_runtime["lifecycle_state"], "idle")
        self.assertEqual(
            terminal_runtime["history_count"],
            expected_terminal_history_count,
        )
        for field in (
            "last_run_count",
            "completed_targets",
            "total_targets",
            "progress_percent",
            "current_phase_completed_targets",
            "current_phase_total_targets",
            "progress_completed",
            "progress_total",
            "active_evaluation_count",
            "queued_evaluation_count",
            "last_phase_completed",
            "last_phase_total",
        ):
            self.assertEqual(terminal_runtime[field], 0)
        for field in (
            "current_target",
            "current_run_id",
            "resumable_run_id",
            "current_phase",
            "finalizing_started_at",
            "last_phase",
            "lease_expires_at",
        ):
            self.assertIsNone(terminal_runtime[field])
        self.assertEqual(terminal_runtime["run_entries"], [])
        self.assertFalse(terminal_runtime["has_resumable_run"])
        self.assertEqual(terminal_runtime["progress_unit"], "evaluationUnit")


class ArchitectureBoundaryTest(unittest.TestCase):
    @staticmethod
    def _attribute_path(node: ast.AST) -> str | None:
        parts: list[str] = []
        current = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if not isinstance(current, ast.Name):
            return None
        parts.append(current.id)
        return ".".join(reversed(parts))

    @staticmethod
    def _module_tree(filename: str) -> ast.Module:
        path = (
            Path(__file__).resolve().parent.parent
            / "scanner"
            / filename
        )
        return ast.parse(path.read_text(encoding="utf-8"))

    @staticmethod
    def _class(tree: ast.Module, name: str) -> ast.ClassDef:
        return next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == name
        )

    def test_native_bridge_has_no_service_private_or_domain_orchestration(
        self,
    ) -> None:
        tree = self._module_tree("native_bridge.py")
        private_service_accesses = sorted(
            {
                f"{node.attr}@{node.lineno}"
                for node in ast.walk(tree)
                if isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "service"
                and node.attr.startswith("_")
            }
        )
        private_service_imports = sorted(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.endswith("service")
            for alias in node.names
            if alias.name.startswith("_")
        )
        imported_names = {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }

        self.assertEqual(private_service_accesses, [])
        self.assertEqual(private_service_imports, [])
        self.assertEqual(
            imported_names
            & {
                "QuestionBank",
                "ScanPlanner",
                "RepairPlanner",
                "ExecutionEngine",
                "RunStateMachine",
            },
            set(),
        )

    def test_domain_and_application_modules_do_not_depend_on_service(
        self,
    ) -> None:
        for filename in (
            "scan_planner.py",
            "repair_planner.py",
            "scan_execution_application.py",
            "repair_execution_application.py",
            "current_model_context.py",
        ):
            with self.subTest(filename=filename):
                tree = self._module_tree(filename)
                service_imports = sorted(
                    {
                        node.module
                        for node in ast.walk(tree)
                        if isinstance(node, ast.ImportFrom)
                        and node.module is not None
                        and node.module.endswith("service")
                    }
                )
                service_references = {
                    node.id
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Name)
                    and node.id == "MonitorService"
                }
                self.assertEqual(service_imports, [])
                self.assertEqual(service_references, set())

        persistence_modules = {
            "active_run_store",
            "config_store",
            "history_store",
            "run_journal",
            "service",
        }
        for filename in ("job_reducers.py", "execution_job_planner.py"):
            with self.subTest(pure_module=filename):
                tree = self._module_tree(filename)
                imported_roots = {
                    (node.module or "").split(".")[0]
                    for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom)
                }
                imported_roots.update(
                    alias.name.split(".")[0]
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Import)
                    for alias in node.names
                )
                self.assertEqual(imported_roots & persistence_modules, set())

    def test_query_classes_have_no_persistence_writes(self) -> None:
        query_specs = (
            ("current_model_context.py", "CurrentModelContextQuery"),
            ("snapshot_query.py", "SnapshotQuery"),
        )
        forbidden_calls = {
            "append_event",
            "clear",
            "load_reference_snapshot_feed_for_app",
            "mutate",
            "save",
            "unlink",
            "update_recommendation_use_epochs",
            "update_run_metadata",
            "update_runtime_state",
            "write_bytes",
            "write_text",
        }
        for filename, class_name in query_specs:
            with self.subTest(query=class_name):
                tree = self._module_tree(filename)
                query = self._class(tree, class_name)
                called_names = {
                    path.rsplit(".", 1)[-1]
                    for node in ast.walk(query)
                    if isinstance(node, ast.Call)
                    and (path := self._attribute_path(node.func)) is not None
                }
                self.assertEqual(called_names & forbidden_calls, set())

    def test_execution_engine_and_state_machine_have_unique_definitions(
        self,
    ) -> None:
        scanner_root = Path(__file__).resolve().parent.parent / "scanner"
        definitions: dict[str, list[str]] = {
            "ExecutionEngine": [],
            "RunStateMachine": [],
        }
        for path in scanner_root.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.ClassDef) and node.name in definitions:
                    definitions[node.name].append(path.name)

        self.assertEqual(definitions["ExecutionEngine"], ["execution.py"])
        self.assertEqual(definitions["RunStateMachine"], ["execution.py"])

    def test_service_shares_one_engine_and_state_machine_across_applications(
        self,
    ) -> None:
        tree = self._module_tree("service.py")
        service = self._class(tree, "MonitorService")
        initializer = next(
            node
            for node in service.body
            if isinstance(node, ast.FunctionDef) and node.name == "__init__"
        )
        calls = [
            node
            for node in ast.walk(initializer)
            if isinstance(node, ast.Call)
        ]
        called_paths = [
            self._attribute_path(node.func)
            for node in calls
        ]
        self.assertEqual(called_paths.count("ExecutionEngine"), 1)
        self.assertEqual(called_paths.count("RunStateMachine"), 1)

        for constructor_name in (
            "ScanExecutionApplicationService",
            "RepairExecutionApplicationService",
        ):
            with self.subTest(application=constructor_name):
                constructor = next(
                    node
                    for node in calls
                    if self._attribute_path(node.func) == constructor_name
                )
                dependencies = {
                    keyword.arg: self._attribute_path(keyword.value)
                    for keyword in constructor.keywords
                    if keyword.arg is not None
                }
                self.assertEqual(
                    dependencies["state_machine"],
                    "self.run_state_machine",
                )
                self.assertEqual(
                    dependencies["engine"],
                    "self.execution_engine",
                )


if __name__ == "__main__":
    unittest.main()
