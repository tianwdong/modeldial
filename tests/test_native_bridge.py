from __future__ import annotations

import ast
import json
import os
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from unittest.mock import ANY, MagicMock, call, patch

import scanner.native_bridge as native_bridge_module
import scanner.runner as runner_module
import scanner.service as service_module
from scanner.active_run_store import ActiveRunStore
from scanner.config_store import ConfigStore
from scanner.endpoint_client import EndpointError, EndpointResult
from scanner.claude_code_client import ClaudeCodeError
from scanner.grok_build_client import GrokBuildError
from scanner.history_store import HistoryStore
from scanner.models import ConnectionConfig, ModelCandidateConfig, ScanResult
from scanner.service import MonitorService
from scanner.usage_store import UsageStore
from scanner.native_bridge import (
    build_refresh_snapshot,
    build_snapshot,
    clear_personal_observations,
    dismiss_resumable_run,
    discover_local_models,
    import_local_provider,
    probe_endpoint_connection,
    preview_scan,
    recover_orphaned_run,
    request_scan_control,
    discover_connection_models,
    export_personal_observations,
    add_endpoint_models,
    migrate_secret_references,
    save_config,
    stream_repair_events,
    stream_scan_events,
    stream_timed_out_repair_events,
    upsert_endpoint,
    verify_endpoint_connection,
)
from tests.question_pack_fixtures import (
    DEFAULT_EVALUATION_COUNT,
    DEFAULT_QUESTION_COUNT,
    DEFAULT_QUESTION_IDS,
    DEFAULT_QUESTION_PACK_VERSION,
)


class _BrokenPipeStream:
    def write(self, value: str) -> int:
        raise BrokenPipeError("parent pipe closed")

    def flush(self) -> None:
        raise BrokenPipeError("parent pipe closed")


def _disable_candidate(
    config_store: ConfigStore,
    connection_id: str,
    model: str,
    effort: str,
) -> None:
    config = config_store.load()
    for connection in config.model_ingress.connections:
        for candidate in connection.model_candidates:
            if (
                candidate.connection_id == connection_id
                and candidate.model_id == model
                and candidate.scan_profile == effort
            ):
                candidate.enabled = False
                config_store.save(config)
                return
    raise AssertionError(
        f"candidate not found: {connection_id} / {model} / {effort}"
    )


def _mock_repair_plan(
    *,
    run_id: str,
    candidate_ids: list[str],
    question_ids: list[str] | None = None,
    question_id: str | None = None,
) -> MagicMock:
    planned_question_ids = list(question_ids or [])
    plan = MagicMock()
    plan.requested_run_id = run_id
    plan.persist_run_id = run_id
    plan.selected_candidate_ids = tuple(candidate_ids)
    plan.candidate_id = candidate_ids[0] if len(candidate_ids) == 1 else None
    plan.question_id = question_id
    plan.total_steps = len(planned_question_ids)
    plan.steps_for.return_value = [
        MagicMock(id=item) for item in planned_question_ids
    ]
    return plan


def _filesystem_fingerprint(root: Path) -> dict[str, tuple[object, ...]]:
    fingerprint: dict[str, tuple[object, ...]] = {}
    for path in [root, *sorted(root.rglob("*"), key=lambda item: str(item))]:
        relative = "." if path == root else str(path.relative_to(root))
        metadata = path.lstat()
        if path.is_symlink():
            fingerprint[relative] = (
                "symlink",
                metadata.st_mode,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ino,
                os.readlink(path),
            )
        elif path.is_file():
            fingerprint[relative] = (
                "file",
                metadata.st_mode,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ino,
                path.read_bytes(),
            )
        else:
            fingerprint[relative] = (
                "directory",
                metadata.st_mode,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ino,
            )
    return fingerprint


def _changed_fingerprint_paths(
    before: dict[str, tuple[object, ...]],
    after: dict[str, tuple[object, ...]],
) -> set[str]:
    return {
        path
        for path in before.keys() | after.keys()
        if before.get(path) != after.get(path)
    }


@contextmanager
def _counting_http_server() -> object:
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def _respond(self) -> None:
            requests.append(f"{self.command} {self.path}")
            self.send_response(500)
            self.end_headers()

        do_GET = _respond
        do_HEAD = _respond

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/latest.json", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class NativeBridgeTest(unittest.TestCase):
    _AUTHORITATIVE_SNAPSHOT_REQUIRED_KEYS = {
        "schema_version",
        "config",
        "question_pack",
        "dashboard",
        "runtime",
        "advisor_v2_evidence",
        "recommendation_portfolio_v2",
        "reference_snapshot_feed",
        "recommendation_use",
        "settings_projection",
    }
    _AUTHORITATIVE_SNAPSHOT_OPTIONAL_KEYS = {
        "stable_dashboard",
        "stable_evidence_dashboard",
        "codex_insights",
        "advisor",
        "diagnostics",
    }

    def assert_authoritative_event_state(
        self,
        event: dict[str, object],
        *,
        lifecycle_state: str,
        is_running: bool,
        has_resumable_run: bool,
    ) -> None:
        state = event.get("state")
        if not isinstance(state, dict):
            self.fail(f"{event.get('type')} must include an authoritative snapshot")
        self.assertEqual(
            set(state) - self._AUTHORITATIVE_SNAPSHOT_OPTIONAL_KEYS,
            self._AUTHORITATIVE_SNAPSHOT_REQUIRED_KEYS,
        )
        self.assertEqual(state.get("schema_version"), 2)
        runtime = state.get("runtime")
        if not isinstance(runtime, dict):
            self.fail(f"{event.get('type')} snapshot must include runtime")
        self.assertEqual(runtime.get("lifecycle_state"), lifecycle_state)
        self.assertIs(runtime.get("is_running"), is_running)
        self.assertIs(runtime.get("has_resumable_run"), has_resumable_run)

    def assert_failure_snapshot_state(
        self,
        event: dict[str, object],
        *,
        lifecycle_state: str,
    ) -> None:
        self.assertEqual(event.get("state_kind"), "snapshot")
        state = event.get("state")
        if not isinstance(state, dict):
            self.fail(f"{event.get('type')} must include an authoritative snapshot")
        self.assertEqual(
            set(state) - self._AUTHORITATIVE_SNAPSHOT_OPTIONAL_KEYS,
            self._AUTHORITATIVE_SNAPSHOT_REQUIRED_KEYS,
        )
        self.assertEqual(state.get("schema_version"), 2)
        runtime = state.get("runtime")
        if not isinstance(runtime, dict):
            self.fail(f"{event.get('type')} snapshot must include runtime")
        self.assertEqual(runtime.get("lifecycle_state"), lifecycle_state)
        self.assertFalse(runtime.get("is_running"))

    def assert_active_runtime_event_state(
        self,
        event: dict[str, object],
        *,
        run_id: str,
        phase: str,
        completed_targets: int,
        total_targets: int,
    ) -> None:
        self.assertEqual(event.get("state_kind"), "runtime_delta")
        state = event.get("state")
        if not isinstance(state, dict):
            self.fail(f"{event.get('type')} must include a runtime event state")
        self.assertEqual(state.get("schema_version"), 1)
        runtime = state.get("runtime")
        if not isinstance(runtime, dict):
            self.fail(f"{event.get('type')} runtime event must include runtime")
        self.assertEqual(runtime.get("lifecycle_state"), "active_scan")
        self.assertIs(runtime.get("is_running"), True)
        self.assertEqual(runtime.get("current_run_id"), run_id)
        self.assertEqual(runtime.get("current_phase"), phase)
        self.assertEqual(runtime.get("progress_completed"), completed_targets)
        self.assertEqual(runtime.get("progress_total"), total_targets)

    def test_config_adapters_only_delegate_to_application_service(
        self,
    ) -> None:
        application = MagicMock()
        responses = {
            "replace_legacy_config": {"legacy": True},
            "patch_config": {"patch": True},
            "upsert_endpoint": {"upsert": True},
            "add_endpoint_models": {"add": True},
        }
        for method, response in responses.items():
            getattr(application, method).return_value = response
        payload = {"schema_version": 1, "connection_id": "endpoint-a"}

        with patch.object(
            native_bridge_module,
            "_config_application_service",
            return_value=application,
        ) as application_factory:
            actual = {
                "replace_legacy_config": save_config(payload),
                "patch_config": native_bridge_module.patch_config(payload),
                "upsert_endpoint": upsert_endpoint(payload),
                "add_endpoint_models": add_endpoint_models(payload),
            }

        self.assertEqual(actual, responses)
        self.assertEqual(application_factory.call_count, 4)
        for method in responses:
            getattr(application, method).assert_called_once_with(payload)

    def test_secret_reference_migration_skips_monitor_and_snapshot_layers(self) -> None:
        payload = {
            "schema_version": 1,
            "operation": "connection_secret_references",
            "arguments": {
                "references_by_connection_id": {
                    "endpoint-a": "keychain:com.modeldial.api-key:endpoint-a",
                }
            },
        }
        config_store = MagicMock(spec=ConfigStore)
        command = MagicMock()
        command.migrate_secret_references.return_value = {
            "schema_version": 1,
            "ok": True,
            "action": "migrate_secret_references",
            "operation": "connection_secret_references",
        }

        with (
            patch.object(
                native_bridge_module,
                "ConfigCommand",
                return_value=command,
            ) as command_type,
            patch.object(
                native_bridge_module,
                "MonitorService",
                side_effect=AssertionError("migration must not construct MonitorService"),
            ),
            patch.object(
                native_bridge_module,
                "_build_command_snapshot",
                side_effect=AssertionError("migration must not build a snapshot"),
            ),
            patch.object(
                native_bridge_module,
                "_build_codex_insights",
                side_effect=AssertionError("migration must not build insights"),
            ),
        ):
            response = migrate_secret_references(payload, config_store)

        command_type.assert_called_once_with(config_store)
        command.migrate_secret_references.assert_called_once_with(payload)
        self.assertEqual(set(response), {"schema_version", "ok", "action", "operation"})
        self.assertNotIn("keychain:", repr(response))

    def test_preview_scan_thinly_delegates_to_the_preview_query(self) -> None:
        config_store = MagicMock(spec=ConfigStore)
        history_store = MagicMock(spec=HistoryStore)
        active_run_store = MagicMock(spec=ActiveRunStore)
        custom_preview = {"schema_version": 1, "new_round": {}, "append": {}}
        regular_preview = {"schema_version": 1, "valid": True}

        with (
            patch.object(native_bridge_module, "_query_monitor_service") as service_query,
            patch.object(
                native_bridge_module,
                "ScanPlanPreviewQuery",
            ) as query_type,
            patch.object(native_bridge_module, "_scan_process_lock") as process_lock,
            patch.object(
                native_bridge_module,
                "_build_command_snapshot",
            ) as snapshot_builder,
            patch.object(native_bridge_module, "SecretStore") as secret_store,
        ):
            query = query_type.return_value
            query.preview_custom_options.return_value = custom_preview
            query.build_preview.return_value = regular_preview

            custom_result = preview_scan(
                config_store,
                history_store,
                active_run_store,
                requested_candidate_ids=["candidate-a", "candidate-b"],
                evaluation_profile_id="quick",
                custom_options=True,
            )
            regular_result = preview_scan(
                config_store,
                history_store,
                active_run_store,
                force_restart=True,
                requested_candidate_ids=["candidate-c"],
                selection_mode="incremental_full",
                custom_round_mode="append",
                evaluation_profile_id="full",
                upgrade_from_run_id="run-parent",
            )

        self.assertIs(custom_result, custom_preview)
        self.assertIs(regular_result, regular_preview)
        self.assertEqual(service_query.call_count, 2)
        service_query.assert_has_calls(
            [
                call(
                    config_store=config_store,
                    history_store=history_store,
                    active_run_store=active_run_store,
                ),
                call(
                    config_store=config_store,
                    history_store=history_store,
                    active_run_store=active_run_store,
                ),
            ]
        )
        self.assertEqual(query_type.call_count, 2)
        query.preview_custom_options.assert_called_once_with(
            requested_candidate_ids=["candidate-a", "candidate-b"],
            evaluation_profile_id="quick",
        )
        query.build_preview.assert_called_once_with(
            force_restart=True,
            requested_candidate_ids=["candidate-c"],
            selection_mode="incremental_full",
            custom_round_mode="append",
            evaluation_profile_id="full",
            upgrade_from_run_id="run-parent",
        )
        process_lock.assert_not_called()
        snapshot_builder.assert_not_called()
        secret_store.assert_not_called()

    def test_preview_scan_custom_options_cli_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            home_dir = root / "home"
            temp_root = root / "tmp"
            data_dir.mkdir()
            home_dir.mkdir()
            temp_root.mkdir()
            config_store = ConfigStore(data_dir / "config.json")
            history_store = HistoryStore(data_dir / "history.jsonl")
            active_run_store = ActiveRunStore(data_dir / "active_run.json")
            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
            )
            config = config_store.save(config_store.load())
            candidate_ids = [
                target.candidate_id
                for target in service.scan_target_resolver.available_targets(config)[:2]
            ]
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(home_dir),
                    "MODELDIAL_DATA_DIR": str(data_dir),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "TMPDIR": str(temp_root),
                }
            )
            environment.pop("MODEL_PILOT_DATA_DIR", None)
            arguments = [
                sys.executable,
                "scripts/native_bridge.py",
                "preview-scan",
                "--custom-options",
                "--evaluation-profile-id",
                "quick",
                "--config-path",
                str(config_store.path),
                "--history-path",
                str(history_store.path),
                "--active-run-path",
                str(active_run_store.path),
            ]
            for candidate_id in candidate_ids:
                arguments.extend(["--candidate-id", candidate_id])
            before = _filesystem_fingerprint(root)

            completed = subprocess.run(
                arguments,
                cwd=Path(__file__).resolve().parent.parent,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )

            after = _filesystem_fingerprint(root)

        payload = json.loads(completed.stdout)
        self.assertEqual(before, after)
        self.assertEqual(set(payload), {"schema_version", "new_round", "append"})
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["new_round"]["schema_version"], 1)
        self.assertEqual(payload["append"]["schema_version"], 1)
        self.assertEqual(payload["new_round"]["requested_candidate_ids"], candidate_ids)
        self.assertEqual(payload["append"]["requested_candidate_ids"], candidate_ids)

    def test_native_bridge_does_not_access_monitor_service_private_api(self) -> None:
        source = Path(native_bridge_module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        private_accesses = sorted(
            {
                f"{node.attr}@{node.lineno}"
                for node in ast.walk(tree)
                if isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "service"
                and node.attr.startswith("_")
            }
        )
        private_imports = sorted(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.endswith("service")
            for alias in node.names
            if alias.name.startswith("_")
        )

        self.assertEqual(private_accesses, [])
        self.assertEqual(private_imports, [])

    def save_active_run_for_event_state(
        self,
        service: MonitorService,
        *,
        run_id: str,
        candidate_id: str,
        lifecycle_state: str,
        phase: str,
    ) -> None:
        config = service.load_config()
        target = next(
            item
            for item in service.scan_target_resolver.enabled_targets(config)
            if item.candidate_id == candidate_id
        )
        question_pack = service.question_bank.load()
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        attempts_per_target = 1 if phase == "repair" else DEFAULT_QUESTION_COUNT
        runtime = {
            "lifecycle_state": lifecycle_state,
            "state_changed_at": timestamp,
            "updated_at": timestamp,
            "current_phase": phase,
            "progress_completed": 0,
            "progress_total": attempts_per_target,
        }
        if lifecycle_state == "active_scan":
            runtime["lease_expires_at"] = (
                datetime.now().astimezone() + timedelta(minutes=5)
            ).isoformat(timespec="seconds")
        payload: dict[str, object] = {
            "run_id": run_id,
            "run_metadata": {
                "run_id": run_id,
                "question_pack_id": question_pack.metadata.question_pack_id,
                "question_pack_version": question_pack.metadata.question_pack_version,
                "started_at": timestamp,
                "completed_at": None,
                "candidate_count": 1,
                "question_count": DEFAULT_QUESTION_COUNT,
                "status": "running" if lifecycle_state == "active_scan" else "paused",
                "selection_mode": "regular",
                "requested_candidate_ids": [candidate_id],
                "regular_candidate_ids": [candidate_id],
                "is_complete_regular_round": False,
            },
            "planned_attempts_by_candidate": {
                candidate_id: attempts_per_target,
            },
            "entries": [
                {
                    "candidate_id": candidate_id,
                    "model": target.model,
                    "effort": target.effort,
                    "label": target.label,
                    "status": "running" if lifecycle_state == "active_scan" else "pending",
                    "final_status": None,
                    "reasoning_tokens": None,
                    "attempts_completed": 0,
                    "attempts_per_target": attempts_per_target,
                    "phase": phase,
                    "flags": [],
                    "error_message": None,
                }
            ],
            "runtime": runtime,
        }
        if phase == "repair":
            payload.update(
                {
                    "repair_operation_kind": "candidate_repair",
                    "repair_operation_run_id": run_id,
                    "repair_run_id": run_id,
                    "repair_candidate_id": candidate_id,
                    "repair_question_ids": [DEFAULT_QUESTION_IDS[0]],
                }
            )
        service.active_run_store.save(payload)

    def save_completed_finalizing_run(
        self,
        service: MonitorService,
        *,
        run_id: str = "run-finalizing",
        candidate_id: str = "codex-local-default:gpt-5.4:high",
    ) -> None:
        self.save_active_run_for_event_state(
            service,
            run_id=run_id,
            candidate_id=candidate_id,
            lifecycle_state="finalizing",
            phase="scan",
        )
        payload = service.active_run_store.load()
        assert payload is not None
        metadata = payload["run_metadata"]
        assert isinstance(metadata, dict)
        metadata["status"] = "completed"
        metadata["completed_at"] = datetime.now().astimezone().isoformat(
            timespec="seconds"
        )
        for entry in payload["entries"]:
            entry["attempts_completed"] = entry["attempts_per_target"]
            entry["status"] = "done"
        service.active_run_store.save(payload)

    def save_finalizing_repair_checkpoint(
        self,
        service: MonitorService,
        *,
        run_id: str,
    ) -> list[ScanResult]:
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        service.active_run_store.save(
            {
                "run_id": run_id,
                "run_metadata": {
                    "run_id": run_id,
                    "status": "completed",
                    "completed_at": timestamp,
                },
                "runtime": {
                    "lifecycle_state": "finalizing",
                    "state_changed_at": timestamp,
                    "finalizing_started_at": timestamp,
                    "updated_at": timestamp,
                    "lease_expires_at": None,
                    "progress_completed": 1,
                    "progress_total": 1,
                    "current_phase": "repair",
                },
                "entries": [],
            }
        )
        return []

    def start_scan_lock_contender(
        self,
        root: Path,
        *,
        ready_path: Path,
        go_path: Path,
        release_path: Path,
        heartbeat_interval: float = 15,
    ) -> subprocess.Popen[str]:
        script = """
import json
from pathlib import Path
import sys
import time

import scanner.native_bridge as native_bridge
from scanner.active_run_store import ActiveRunStore
from scanner.history_store import HistoryStore

root = Path(sys.argv[1])
ready_path = Path(sys.argv[2])
go_path = Path(sys.argv[3])
release_path = Path(sys.argv[4])
native_bridge.LOCK_HEARTBEAT_INTERVAL_SECONDS = float(sys.argv[5])
ready_path.write_text("ready", encoding="utf-8")
deadline = time.monotonic() + 5
while not go_path.exists():
    if time.monotonic() >= deadline:
        raise TimeoutError("lock contender did not receive start signal")
    time.sleep(0.005)
with native_bridge._scan_process_lock(
    ActiveRunStore(root / "active_run.json"),
    HistoryStore(root / "history.jsonl"),
) as acquired:
    print(json.dumps({"acquired": acquired}), flush=True)
    if acquired:
        deadline = time.monotonic() + 5
        while not release_path.exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("lock contender did not receive release signal")
            time.sleep(0.005)
"""
        return subprocess.Popen(
            [
                sys.executable,
                "-c",
                script,
                str(root),
                str(ready_path),
                str(go_path),
                str(release_path),
                str(heartbeat_interval),
            ],
            cwd=Path(__file__).resolve().parent.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def wait_for_paths(self, *paths: Path) -> None:
        deadline = time.monotonic() + 5
        while not all(path.exists() for path in paths):
            if time.monotonic() >= deadline:
                self.fail(f"timed out waiting for paths: {paths}")
            time.sleep(0.005)

    def read_lock_contender_result(
        self,
        process: subprocess.Popen[str],
    ) -> dict[str, object]:
        assert process.stdout is not None
        line = process.stdout.readline()
        if not line:
            assert process.stderr is not None
            self.fail(
                "lock contender exited before reporting: "
                f"{process.stderr.read()}"
            )
        return json.loads(line)

    def test_read_config_is_side_effect_free(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_store = ConfigStore(
                root / "config.json",
                first_run_defaults=True,
            )
            before = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }

            with patch.object(
                native_bridge_module,
                "MonitorService",
                side_effect=AssertionError("read-config must not construct MonitorService"),
            ):
                payload = native_bridge_module.read_config(config_store)

            after = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }

        self.assertIn("model_ingress", payload)
        self.assertEqual(before, after)

    def test_snapshot_queries_do_not_mutate_the_isolated_filesystem(self) -> None:
        root_path = Path(__file__).resolve().parent.parent
        for command in ("snapshot", "refresh-snapshot"):
            for include_insights in (False, True):
                with self.subTest(command=command, include_insights=include_insights):
                    with tempfile.TemporaryDirectory() as temp_dir:
                        root = Path(temp_dir)
                        data_dir = root / "data"
                        home_dir = root / "home"
                        temp_root = root / "tmp"
                        bin_dir = root / "bin"
                        for directory in (data_dir, home_dir, temp_root, bin_dir):
                            directory.mkdir(parents=True)

                        config_path = data_dir / "config.json"
                        config_store = ConfigStore(
                            config_path,
                            first_run_defaults=True,
                        )
                        config_store.save(config_store.load())
                        (data_dir / "codex_session_tracker.json").write_text(
                            "{}",
                            encoding="utf-8",
                        )
                        (data_dir / "session-registry.json").write_text(
                            json.dumps({"schema_version": 1, "sessions": {}}),
                            encoding="utf-8",
                        )
                        inbox = data_dir / "session-events" / "inbox"
                        inbox.mkdir(parents=True)
                        (inbox / "pending.json").write_text("{}", encoding="utf-8")
                        (data_dir / "usage_observations.json").write_text(
                            json.dumps(
                                {
                                    "schema_version": 1,
                                    "files": {},
                                    "observations": {},
                                    "bootstrap_truncated": False,
                                }
                            ),
                            encoding="utf-8",
                        )
                        captured_at = "2026-07-28T04:00:00Z"
                        (data_dir / "codex_account_snapshot.json").write_text(
                            json.dumps(
                                {
                                    "schema_version": 1,
                                    "captured_at": captured_at,
                                    "source": "codex_app_server",
                                    "login_state": "authenticated",
                                    "quota_status": "not_applicable",
                                    "quota_windows": [],
                                }
                            ),
                            encoding="utf-8",
                        )
                        (data_dir / "codex_account_snapshots.json").write_text(
                            json.dumps(
                                {
                                    "schema_version": 1,
                                    "snapshots": [
                                        {
                                            "schema_version": 1,
                                            "captured_at": captured_at,
                                            "source": "codex_app_server",
                                            "login_state": "authenticated",
                                            "quota_status": "not_applicable",
                                            "quota_windows": [],
                                        }
                                    ],
                                }
                            ),
                            encoding="utf-8",
                        )
                        (data_dir / "recommendation_use_epochs.json").write_text(
                            json.dumps(
                                {
                                    "schema_version": 1,
                                    "epochs": [],
                                    "observation_assignments": {},
                                }
                            ),
                            encoding="utf-8",
                        )
                        reference_cache = data_dir / "reference_snapshots"
                        reference_cache.mkdir()
                        (reference_cache / "sentinel.txt").write_text(
                            "cached",
                            encoding="utf-8",
                        )

                        marker_path = root / "codex-invoked"
                        fake_codex = bin_dir / "codex"
                        fake_codex.write_text(
                            "#!/bin/sh\n: > \"$MODELDIAL_TEST_CODEX_MARKER\"\nexit 1\n",
                            encoding="utf-8",
                        )
                        fake_codex.chmod(0o755)
                        environment = os.environ.copy()
                        environment.update(
                            {
                                "HOME": str(home_dir),
                                "MODELDIAL_DATA_DIR": str(data_dir),
                                "MODELDIAL_TEST_CODEX_MARKER": str(marker_path),
                                "MODELDIAL_REFERENCE_SNAPSHOT_URL": "http://127.0.0.1:9/",
                                "PYTHONDONTWRITEBYTECODE": "1",
                                "TMPDIR": str(temp_root),
                                "PATH": f"{bin_dir}:{environment.get('PATH', '')}",
                            }
                        )
                        environment.pop("MODEL_PILOT_DATA_DIR", None)
                        arguments = [
                            sys.executable,
                            "scripts/native_bridge.py",
                            command,
                            "--config-path",
                            str(config_path),
                            "--history-path",
                            str(data_dir / "history.jsonl"),
                            "--active-run-path",
                            str(data_dir / "active_run.json"),
                        ]
                        if include_insights:
                            arguments.append("--include-codex-insights")

                        before = _filesystem_fingerprint(root)
                        with _counting_http_server() as (
                            reference_url,
                            reference_requests,
                        ):
                            environment["MODELDIAL_REFERENCE_SNAPSHOT_URL"] = (
                                reference_url
                            )
                            first = subprocess.run(
                                arguments,
                                cwd=root_path,
                                env=environment,
                                check=True,
                                capture_output=True,
                                text=True,
                            )
                            after_first = _filesystem_fingerprint(root)
                            second = subprocess.run(
                                arguments,
                                cwd=root_path,
                                env=environment,
                                check=True,
                                capture_output=True,
                                text=True,
                            )
                            after_second = _filesystem_fingerprint(root)
                        self.assertEqual(reference_requests, [])
                        first_payload = json.loads(first.stdout)
                        second_payload = json.loads(second.stdout)

                        self.assertIsInstance(first_payload, dict)
                        self.assertIsInstance(second_payload, dict)
                        self.assertEqual(before, after_first)
                        self.assertEqual(before, after_second)
                        self.assertFalse(marker_path.exists())
                        if include_insights:
                            self.assertEqual(
                                first_payload["codex_insights"]["account"]["login_state"],
                                "authenticated",
                            )
                        self.assertEqual(
                            first_payload["recommendation_use"]["schema_version"],
                            1,
                        )

    def test_snapshot_queries_with_insights_accept_the_default_history_path(self) -> None:
        expected_insights = {"schema_version": 1}
        expected_data_dir = Path(native_bridge_module.__file__).resolve().parent.parent / (
            "artifacts"
        )
        for command, builder_name in (
            ("snapshot", "build_snapshot"),
            ("refresh-snapshot", "build_refresh_snapshot"),
        ):
            with self.subTest(command=command), patch.object(
                sys,
                "argv",
                ["native_bridge.py", command, "--include-codex-insights"],
            ), patch.object(
                native_bridge_module,
                "_read_codex_insights",
                return_value=expected_insights,
            ) as read_insights, patch.object(
                native_bridge_module,
                builder_name,
                return_value={"schema_version": 1},
            ) as build_query, patch.object(native_bridge_module, "_write_json"):
                native_bridge_module.main()

            read_insights.assert_called_once_with(expected_data_dir)
            build_query.assert_called_once_with(
                None,
                None,
                None,
                codex_insights=expected_insights,
            )

    def test_observe_state_command_owns_runtime_observation_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            home_dir = root / "home"
            temp_root = root / "tmp"
            for directory in (data_dir, home_dir, temp_root):
                directory.mkdir(parents=True)
            config_path = data_dir / "config.json"
            config_store = ConfigStore(config_path, first_run_defaults=True)
            config_store.save(config_store.load())
            config_bytes = config_path.read_bytes()
            inbox = data_dir / "session-events" / "inbox"
            inbox.mkdir(parents=True)
            pending_event = inbox / "pending.json"
            observed_at = datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            ).replace("+00:00", "Z")
            UsageStore(data_dir).save_account_snapshot(
                {
                    "schema_version": 1,
                    "captured_at": observed_at,
                    "source": "codex_app_server",
                    "account_type": "unknown",
                    "login_state": "authenticated",
                    "quota_status": "not_applicable",
                    "quota_windows": [],
                    "usage_status": "unavailable",
                    "usage_summary": None,
                    "daily_usage": [],
                    "unavailable_capabilities": [],
                }
            )
            pending_event.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "event_id": "event-observe-state",
                        "source": "codex",
                        "session_id": "observe-session",
                        "hook_event_name": "UserPromptSubmit",
                        "observed_at": observed_at,
                        "turn_id": "turn-observe",
                        "cwd": str(root / "workspace"),
                        "model": "gpt-5.6-sol",
                        "effort": "high",
                        "is_modeldial_scan": False,
                    }
                ),
                encoding="utf-8",
            )
            sessions_root = home_dir / ".codex" / "sessions" / "2026" / "07" / "28"
            sessions_root.mkdir(parents=True)
            rollout_path = sessions_root / "rollout-observe-session.jsonl"
            rollout_path.write_text(
                "\n".join(
                    json.dumps(
                        {"timestamp": observed_at, "type": event_type, "payload": payload}
                    )
                    for event_type, payload in (
                        (
                            "session_meta",
                            {
                                "id": "observe-session",
                                "cwd": str(root / "workspace"),
                                "model_provider": "OpenAI",
                            },
                        ),
                        (
                            "event_msg",
                            {"type": "task_started", "turn_id": "turn-observe"},
                        ),
                        (
                            "turn_context",
                            {
                                "turn_id": "turn-observe",
                                "model": "gpt-5.6-sol",
                                "effort": "high",
                            },
                        ),
                        (
                            "event_msg",
                            {
                                "type": "token_count",
                                "info": {
                                    "last_token_usage": {
                                        "input_tokens": 100,
                                        "output_tokens": 20,
                                        "reasoning_output_tokens": 5,
                                    }
                                },
                            },
                        ),
                        (
                            "event_msg",
                            {
                                "type": "task_complete",
                                "turn_id": "turn-observe",
                                "duration_ms": 1_000,
                            },
                        ),
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(home_dir),
                    "MODELDIAL_DATA_DIR": str(data_dir),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "TMPDIR": str(temp_root),
                }
            )
            environment.pop("MODEL_PILOT_DATA_DIR", None)
            arguments = [
                sys.executable,
                "scripts/native_bridge.py",
                "observe-state",
                "--include-codex-insights",
                "--config-path",
                str(config_path),
                "--history-path",
                str(data_dir / "history.jsonl"),
                "--active-run-path",
                str(data_dir / "active_run.json"),
            ]

            before = _filesystem_fingerprint(root)
            first = subprocess.run(
                arguments,
                cwd=Path(__file__).resolve().parent.parent,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            after_first = _filesystem_fingerprint(root)
            first_tracker_state = json.loads(
                (data_dir / "codex_session_tracker.json").read_text(
                    encoding="utf-8"
                )
            )
            first_registry_state = json.loads(
                (data_dir / "session-registry.json").read_text(encoding="utf-8")
            )
            first_recommendation_state = json.loads(
                (data_dir / "recommendation_use_epochs.json").read_text(
                    encoding="utf-8"
                )
            )
            first_usage_state = json.loads(
                (data_dir / "usage_observations.json").read_text(
                    encoding="utf-8"
                )
            )
            second = subprocess.run(
                arguments,
                cwd=Path(__file__).resolve().parent.parent,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            after_second = _filesystem_fingerprint(root)

            first_payload = json.loads(first.stdout)
            second_payload = json.loads(second.stdout)
            self.assertEqual(first_payload["action"], "observe_state")
            self.assertEqual(first_payload["status"], "observed")
            self.assertEqual(
                set(first_payload["state"]),
                {
                    "schema_version",
                    "config",
                    "question_pack",
                    "runtime",
                    "codex_insights",
                    "recommendation_use",
                },
            )
            self.assertEqual(first_payload["state"]["schema_version"], 1)
            self.assertEqual(second_payload["action"], "observe_state")
            self.assertIn(
                first_payload["state"]["codex_insights"]["collection"]["app_server"]["status"],
                {"cached", "stale", "unavailable"},
            )
            self.assertEqual(
                first_payload["session_counts"],
                {"codex_session_count": 1, "external_session_count": 0},
            )
            self.assertFalse(pending_event.exists())
            self.assertTrue((data_dir / "codex_session_tracker.json").is_file())
            self.assertEqual(
                _changed_fingerprint_paths(before, after_first),
                {
                    "data",
                    "data/.session-registry.json.lock.guard",
                    "data/codex_account_snapshot.json",
                    "data/codex_account_snapshots.json",
                    "data/codex_session_tracker.json",
                    "data/recommendation_use_epochs.json",
                    "data/session-events/inbox",
                    "data/session-events/inbox/pending.json",
                    "data/session-registry.json",
                    "data/usage_identity.key",
                    "data/usage_observations.json",
                },
            )
            self.assertLessEqual(
                _changed_fingerprint_paths(after_first, after_second),
                {
                    "data",
                    "data/codex_account_snapshot.json",
                    "data/codex_account_snapshots.json",
                    "data/recommendation_use_epochs.json",
                    "data/usage_observations.json",
                },
            )
            self.assertEqual(config_path.read_bytes(), config_bytes)
            self.assertFalse((data_dir / "history.jsonl").exists())
            self.assertFalse((data_dir / "active_run.json").exists())
            second_recommendation_state = json.loads(
                (data_dir / "recommendation_use_epochs.json").read_text(
                    encoding="utf-8"
                )
            )
            second_usage_state = json.loads(
                (data_dir / "usage_observations.json").read_text(
                    encoding="utf-8"
                )
            )
            second_tracker_state = json.loads(
                (data_dir / "codex_session_tracker.json").read_text(
                    encoding="utf-8"
                )
            )
            second_registry_state = json.loads(
                (data_dir / "session-registry.json").read_text(encoding="utf-8")
            )
            self.assertEqual(first_tracker_state, second_tracker_state)
            self.assertEqual(first_registry_state, second_registry_state)
            self.assertEqual(
                set(second_registry_state["sessions"]),
                {"codex:observe-session"},
            )
            first_observation_ids = set(first_usage_state["observations"])
            second_observation_ids = set(second_usage_state["observations"])
            self.assertEqual(len(first_observation_ids), 1)
            self.assertEqual(first_observation_ids, second_observation_ids)
            self.assertEqual(
                {
                    path: state.get("offset")
                    for path, state in first_usage_state["files"].items()
                },
                {
                    path: state.get("offset")
                    for path, state in second_usage_state["files"].items()
                },
            )
            self.assertEqual(
                [
                    epoch.get("use_epoch_id")
                    for epoch in first_recommendation_state.get("epochs", [])
                ],
                [
                    epoch.get("use_epoch_id")
                    for epoch in second_recommendation_state.get("epochs", [])
                ],
            )
            self.assertEqual(
                first_recommendation_state.get("observation_assignments"),
                second_recommendation_state.get("observation_assignments"),
            )
            self.assertFalse(
                any(
                    path.name.endswith(".tmp")
                    or path.name in {
                        ".usage-state.lock",
                        ".session-registry.json.lock",
                    }
                    for path in data_dir.rglob("*")
                )
            )

    def test_repository_lock_serializes_concurrent_stale_reclaimers(self) -> None:
        worker = """
import json
from pathlib import Path
import sys
import time
from scanner.process_lock import exclusive_process_lock

lock_path = Path(sys.argv[1])
ready_path = Path(sys.argv[2])
barrier_path = Path(sys.argv[3])
ready_path.touch()
while not barrier_path.exists():
    time.sleep(0.001)
with exclusive_process_lock(
    lock_path,
    timeout_seconds=0,
    stale_seconds=0,
) as acquired:
    print(json.dumps({"acquired": acquired}), flush=True)
    if acquired:
        time.sleep(0.2)
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lock_path = root / ".usage-state.lock"
            lock_path.write_text(
                json.dumps({"pid": 999_999_999, "token": "stale"}),
                encoding="utf-8",
            )
            barrier_path = root / "go"
            ready_paths = [root / "ready-1", root / "ready-2"]
            processes = [
                subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        worker,
                        str(lock_path),
                        str(ready_path),
                        str(barrier_path),
                    ],
                    cwd=Path(__file__).resolve().parent.parent,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for ready_path in ready_paths
            ]
            self.wait_for_paths(*ready_paths)
            barrier_path.touch()
            outputs = []
            for process in processes:
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, stderr)
                outputs.append(json.loads(stdout))

            self.assertEqual(
                sum(1 for payload in outputs if payload["acquired"]),
                1,
            )
            self.assertFalse(lock_path.exists())
            self.assertTrue((root / ".usage-state.lock.guard").is_file())

    def test_snapshot_does_not_recover_finalizing_run_until_explicit_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
            )
            self.save_completed_finalizing_run(service)
            before = active_run_store.path.read_bytes()

            observer = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
            )
            snapshot = build_snapshot(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
            )
            refresh_snapshot = build_refresh_snapshot(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
            )

            self.assertEqual(active_run_store.path.read_bytes(), before)
            self.assertEqual(
                observer.build_state()["runtime"]["lifecycle_state"],
                "finalizing",
            )
            self.assertEqual(snapshot["runtime"]["lifecycle_state"], "finalizing")
            self.assertEqual(
                refresh_snapshot["runtime"]["lifecycle_state"],
                "finalizing",
            )

            recovery = recover_orphaned_run(
                config_store,
                history_store,
                active_run_store,
            )

            self.assertTrue(recovery["ok"])
            self.assertTrue(recovery["recovered"])
            self.assertEqual(recovery["status"], "recovered")
            self.assertEqual(recovery["run_id"], "run-finalizing")
            self.assertIsNone(active_run_store.load())

    def test_script_recover_run_entrypoint_performs_explicit_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_store = ConfigStore(root / "config.json")
            history_store = HistoryStore(root / "history.jsonl")
            active_run_store = ActiveRunStore(root / "active_run.json")
            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
            )
            self.save_completed_finalizing_run(
                service,
                run_id="run-cli-finalizing",
            )

            output = subprocess.check_output(
                [
                    "python3",
                    "scripts/native_bridge.py",
                    "recover-run",
                    "--config-path",
                    str(config_store.path),
                    "--history-path",
                    str(history_store.path),
                    "--active-run-path",
                    str(active_run_store.path),
                ],
                text=True,
                cwd=Path(__file__).resolve().parent.parent,
            )
            payload = json.loads(output)

            self.assertTrue(payload["ok"])
            self.assertTrue(payload["recovered"])
            self.assertEqual(payload["action"], "recover_run")
            self.assertEqual(payload["status"], "recovered")
            self.assertEqual(payload["run_id"], "run-cli-finalizing")
            self.assertIsNone(active_run_store.load())

    def test_explicit_recovery_does_not_run_without_process_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            self.save_completed_finalizing_run(
                MonitorService(
                    config_store=config_store,
                    history_store=history_store,
                    active_run_store=active_run_store,
                )
            )
            before = active_run_store.path.read_bytes()
            lock_context = MagicMock()
            lock_context.__enter__.return_value = False
            lock_context.__exit__.return_value = False

            with patch.object(
                native_bridge_module,
                "_scan_process_lock",
                return_value=lock_context,
            ), patch.object(
                MonitorService,
                "recover_orphaned_finalizing_run",
                autospec=True,
            ) as recover:
                result = recover_orphaned_run(
                    config_store,
                    history_store,
                    active_run_store,
                )

            recover.assert_not_called()
            self.assertEqual(result["status"], "scan_active")
            self.assertFalse(result["recovered"])
            self.assertEqual(result["run_id"], "run-finalizing")
            self.assertEqual(active_run_store.path.read_bytes(), before)

    def test_scan_and_repair_commands_recover_only_after_process_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            call_order: list[str] = []
            lease_heartbeats: list[Callable[[], object]] = []
            lock_context = MagicMock()
            lock_context.__enter__.side_effect = (
                lambda: call_order.append("lock") or True
            )
            lock_context.__exit__.return_value = False

            class RecoveryObserved(Exception):
                pass

            def recover_once(
                _service: MonitorService,
                *,
                exclusive_lock_held: bool,
            ) -> dict[str, object]:
                self.assertTrue(exclusive_lock_held)
                call_order.append("recover")
                raise RecoveryObserved

            def process_lock_once(
                _active_run_store: ActiveRunStore,
                _history_store: HistoryStore,
                *,
                lease_heartbeat: Callable[[], object],
            ) -> MagicMock:
                lease_heartbeats.append(lease_heartbeat)
                return lock_context

            with patch.object(
                MonitorService,
                "recover_orphaned_finalizing_run",
                autospec=True,
                side_effect=recover_once,
            ) as recover, patch.object(
                native_bridge_module,
                "_scan_process_lock",
                side_effect=process_lock_once,
            ) as process_lock:
                with self.assertRaises(RecoveryObserved):
                    list(
                        stream_scan_events(
                            config_store=config_store,
                            history_store=history_store,
                            active_run_store=active_run_store,
                        )
                    )
                with self.assertRaises(RecoveryObserved):
                    list(
                        stream_repair_events(
                            run_id="run-repair",
                            candidate_id="codex-local-default:gpt-5.4:high",
                            config_store=config_store,
                            history_store=history_store,
                            active_run_store=active_run_store,
                        )
                    )
                with self.assertRaises(RecoveryObserved):
                    list(
                        native_bridge_module.stream_failed_repair_events(
                            run_id="run-repair",
                            candidate_ids=["codex-local-default:gpt-5.4:high"],
                            config_store=config_store,
                            history_store=history_store,
                            active_run_store=active_run_store,
                        )
                    )

            self.assertEqual(recover.call_count, 3)
            self.assertEqual(process_lock.call_count, 3)
            self.assertEqual(call_order, ["lock", "recover"] * 3)
            self.assertEqual(len(lease_heartbeats), 3)
            self.assertTrue(all(callable(item) for item in lease_heartbeats))

    def test_scan_and_repair_commands_stop_when_finalizing_recovery_is_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
            )
            self.save_completed_finalizing_run(service)
            recovery = {
                "ok": True,
                "action": "recover_run",
                "recovered": False,
                "status": "incomplete",
                "message": "收尾状态持久化失败，未执行恢复。",
                "run_id": "run-finalizing",
            }

            with (
                patch.object(
                    MonitorService,
                    "recover_orphaned_finalizing_run",
                    autospec=True,
                    return_value=recovery,
                ),
                patch.object(
                    MonitorService,
                    "run_enabled_targets",
                    autospec=True,
                ) as scan,
                patch.object(
                    MonitorService,
                    "repair_failed_candidate",
                    autospec=True,
                ) as repair,
                patch.object(
                    MonitorService,
                    "repair_timed_out_questions",
                    autospec=True,
                ) as timeout_repair,
            ):
                scan_events = list(
                    stream_scan_events(
                        config_store=config_store,
                        history_store=history_store,
                        active_run_store=active_run_store,
                    )
                )
                repair_events = list(
                    stream_repair_events(
                        run_id="run-finalizing",
                        candidate_id="codex-local-default:gpt-5.4:high",
                        config_store=config_store,
                        history_store=history_store,
                        active_run_store=active_run_store,
                    )
                )
                timeout_events = list(
                    stream_timed_out_repair_events(
                        run_id="run-finalizing",
                        candidate_ids=["codex-local-default:gpt-5.4:high"],
                        config_store=config_store,
                        history_store=history_store,
                        active_run_store=active_run_store,
                    )
                )

            for terminal, event_type in (
                (scan_events[-1], "scan.failed"),
                (repair_events[-1], "repair.failed"),
                (timeout_events[-1], "timeout-repair.failed"),
            ):
                self.assertEqual(terminal["type"], event_type)
                self.assertEqual(
                    terminal["failure_category"],
                    "run_recovery_failed",
                )
                self.assert_failure_snapshot_state(
                    terminal,
                    lifecycle_state="finalizing",
                )
            scan.assert_not_called()
            repair.assert_not_called()
            timeout_repair.assert_not_called()
            self.assertIsNotNone(active_run_store.load())

    def test_personal_observation_export_and_clear_keep_scan_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            history_store = HistoryStore(root / "history.jsonl")
            history_store.path.write_text("scan-result\n", encoding="utf-8")
            usage_store = UsageStore(root)
            usage_store.save_usage_state(
                {
                    "schema_version": 1,
                    "files": {},
                    "observations": {"observation": {"input_tokens": 10}},
                    "bootstrap_truncated": False,
                }
            )

            exported = export_personal_observations(history_store)
            cleared = clear_personal_observations(
                history_store,
                sessions_root=root / "sessions",
            )

            self.assertEqual(exported["schema_version"], 1)
            self.assertIn("usage_observations", exported)
            self.assertTrue(cleared["ok"])
            self.assertEqual(cleared["action"], "clear_personal_observations")
            self.assertTrue(history_store.path.exists())
            self.assertEqual(usage_store.load_usage_state()["observations"], {})

    def test_detached_bridge_keeps_consuming_events_after_parent_pipe_closes(self) -> None:
        consumed: list[str] = []

        def events(**kwargs):  # type: ignore[no-untyped-def]
            for event_type in ("timeout-repair.started", "timeout-repair.finished"):
                consumed.append(event_type)
                yield {"type": event_type}

        with (
            patch.object(
                native_bridge_module,
                "stream_timed_out_repair_events",
                side_effect=events,
            ),
            patch.object(
                native_bridge_module.sys,
                "argv",
                [
                    "native_bridge.py",
                    "repair-timeouts",
                    "--run-id",
                    "run-test",
                    "--candidate-id",
                    "candidate-test",
                ],
            ),
            patch.object(native_bridge_module.sys, "stdout", _BrokenPipeStream()),
        ):
            native_bridge_module.main()

        self.assertEqual(
            consumed,
            ["timeout-repair.started", "timeout-repair.finished"],
        )

    def test_scan_logs_ignore_closed_parent_stderr(self) -> None:
        for module in (native_bridge_module, runner_module, service_module):
            with self.subTest(module=module.__name__):
                with patch.object(module.sys, "stderr", _BrokenPipeStream()):
                    module._log("detached bridge")

    def test_snapshot_exposes_detected_local_providers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")

            snapshot = build_snapshot(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
            )

        self.assertIn("detected_local_providers", snapshot["config"])

    def test_snapshot_can_expose_codex_insights_without_coupling_service_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            expected = {
                "schema_version": 1,
                "account": {"login_state": "authenticated"},
                "workload": {"observation_count": 3},
            }
            snapshot = build_snapshot(
                config_store=ConfigStore(Path(temp_dir) / "config.json"),
                history_store=HistoryStore(Path(temp_dir) / "history.jsonl"),
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                codex_insights=expected,
            )

        self.assertEqual(snapshot["codex_insights"], expected)
        self.assertEqual(snapshot["advisor"]["schema_version"], 1)
        self.assertEqual(snapshot["advisor"]["decision"], "unmapped")

    def test_native_snapshot_explicitly_requests_codex_insights(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "Sources"
            / "Model"
            / "NativeBridgeClient.swift"
        ).read_text(encoding="utf-8")

        self.assertIn('["snapshot", "--include-codex-insights"]', source)

    def test_swift_scan_stream_buffers_raw_bytes_until_a_complete_line(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = (
            root / "Sources" / "Model" / "NativeBridgeClient.swift"
        ).read_text(encoding="utf-8")
        handler = source[
            source.index("var bufferedOutputData = Data()") :
            source.index("var bufferedErrorText = \"\"")
        ]

        self.assertIn("bufferedOutputData.append(data)", handler)
        self.assertIn("bufferedOutputData.firstIndex(of: 0x0A)", handler)
        self.assertIn("Data(bufferedOutputData[..<newlineIndex])", handler)
        self.assertNotIn("String(data: data, encoding: .utf8)", handler)
        self.assertIn(
            "private func consumeScanOutput(_ data: Data",
            source,
        )

    def test_snapshot_exposes_privacy_safe_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot = build_snapshot(
                config_store=ConfigStore(Path(temp_dir) / "config.json"),
                history_store=HistoryStore(Path(temp_dir) / "history.jsonl"),
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                codex_insights={
                    "schema_version": 1,
                    "account": {
                        "login_state": "authenticated",
                        "quota_status": "not_applicable",
                    },
                    "workload": {
                        "status": "available",
                        "coverage_complete": True,
                        "aggregates": [],
                    },
                    "quota_burn": {
                        "status": "not_applicable",
                        "rejected_intervals": {},
                    },
                    "collection": {
                        "app_server": {
                            "status": "cached",
                            "model_catalog_status": "not_checked",
                        }
                    },
                },
            )

        self.assertEqual(snapshot["diagnostics"]["schema_version"], 1)
        self.assertEqual(snapshot["diagnostics"]["overall_status"], "healthy")
        portfolio = snapshot["recommendation_portfolio_v2"]
        representative_id = portfolio.get("representative_configuration_id")
        representative_decision = next(
            (
                item
                for item in portfolio.get("decisions", [])
                if item.get("current_model_configuration_id") == representative_id
            ),
            None,
        )
        self.assertEqual(
            snapshot["diagnostics"]["versions"]["advisor_ruleset_version"],
            "recommendation-portfolio-v2",
        )
        self.assertEqual(
            snapshot["diagnostics"]["advisor_short_circuit_reason"],
            (representative_decision or {}).get("reason") or portfolio.get("status"),
        )

    def test_codex_insights_reuses_recent_account_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            calls = 0

            def account_reader() -> dict[str, object]:
                nonlocal calls
                calls += 1
                return {
                    "schema_version": 1,
                    "captured_at": datetime.now(timezone.utc)
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z"),
                    "source": "codex_app_server",
                    "account_type": "api_key",
                    "login_state": "authenticated",
                    "quota_status": "not_applicable",
                    "quota_windows": [],
                    "usage_status": "not_applicable",
                    "usage_summary": None,
                    "daily_usage": [],
                    "unavailable_capabilities": [],
                }

            def usage_observer(**_kwargs: object) -> dict[str, object]:
                return {
                    "schema_version": 1,
                    "status": "available",
                    "captured_at": "2026-07-24T08:00:00Z",
                    "coverage_complete": False,
                    "observation_count": 0,
                    "aggregates": [],
                }

            first = native_bridge_module._build_codex_insights(
                Path(temp_dir),
                account_reader=account_reader,
                usage_observer=usage_observer,
            )
            second = native_bridge_module._build_codex_insights(
                Path(temp_dir),
                account_reader=account_reader,
                usage_observer=usage_observer,
            )

        self.assertEqual(calls, 1)
        self.assertEqual(first["account"], second["account"])
        self.assertEqual(first["quota_burn"]["status"], "not_applicable")

    def test_refresh_snapshot_accepts_materialized_final_quota_boundary(self) -> None:
        current_model = {
            "effective_candidate_id": None,
            "source": "unavailable",
            "detection_status": "unavailable",
            "detected_at": None,
            "model": None,
            "effort": None,
            "active_session_count": 0,
            "active_models": [],
            "active_sessions": [],
            "display_sessions": [],
        }
        calls: list[tuple[Path, bool]] = []

        def provider(data_dir: Path, force_account_refresh: bool) -> dict[str, object]:
            calls.append((data_dir, force_account_refresh))
            return {
                "schema_version": 1,
                "account": {"quota_status": "available"},
                "workload": {"status": "available"},
                "quota_burn": {"status": "collecting", "aggregates": []},
            }

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "scanner.current_model_context.CurrentModelContextQuery.build",
            return_value=current_model,
        ):
            data_dir = Path(temp_dir)
            snapshot = build_refresh_snapshot(
                config_store=ConfigStore(data_dir / "config.json"),
                history_store=HistoryStore(data_dir / "history.jsonl"),
                active_run_store=ActiveRunStore(data_dir / "active_run.json"),
                codex_insights=provider(data_dir, True),
            )

        self.assertEqual(snapshot["codex_insights"]["quota_burn"]["status"], "collecting")
        self.assertEqual(calls, [(data_dir, True)])

    def test_fresh_snapshot_has_no_enabled_scan_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot = build_snapshot(
                config_store=ConfigStore(
                    Path(temp_dir) / "config.json",
                    first_run_defaults=True,
                ),
                history_store=HistoryStore(Path(temp_dir) / "history.jsonl"),
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
            )

        self.assertEqual(snapshot["runtime"]["enabled_target_count"], 0)
        self.assertEqual(snapshot["runtime"]["run_entries"], [])

    def test_refresh_snapshot_omits_dashboard_and_history_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            refresh_snapshot = build_refresh_snapshot(
                config_store=ConfigStore(
                    Path(temp_dir) / "config.json",
                    first_run_defaults=True,
                ),
                history_store=HistoryStore(Path(temp_dir) / "history.jsonl"),
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
            )

        self.assertEqual(
            set(refresh_snapshot),
            {
                "schema_version",
                "config",
                "runtime",
                "question_pack",
                "recommendation_use",
            },
        )
        self.assertEqual(refresh_snapshot["schema_version"], 1)
        self.assertIn("evaluation_profiles", refresh_snapshot["question_pack"])
        self.assertEqual(refresh_snapshot["runtime"]["enabled_target_count"], 0)

    def test_import_local_provider_enables_existing_codex_source_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(
                Path(temp_dir) / "config.json",
                first_run_defaults=True,
            )
            config = config_store.load()
            codex_source = next(
                item for item in config.model_ingress.sources if item.id == "codex_local"
            )
            codex_connection = next(
                item
                for item in config.model_ingress.connections
                if item.id == "codex-local-default"
            )
            codex_source.enabled = False
            codex_connection.enabled = False
            config_store.save(config)

            detector = lambda: [
                {
                    "provider_id": "codex",
                    "importable": True,
                }
            ]
            first = import_local_provider(
                "codex",
                config_store=config_store,
                local_provider_detector=detector,
            )
            second = import_local_provider(
                "codex",
                config_store=config_store,
                local_provider_detector=detector,
            )

            reloaded = config_store.load()

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertTrue(
            next(item for item in reloaded.model_ingress.sources if item.id == "codex_local").enabled
        )
        self.assertTrue(
            next(
                item
                for item in reloaded.model_ingress.connections
                if item.id == "codex-local-default"
            ).enabled
        )

    def test_import_codex_keeps_first_run_disabled_when_cli_or_login_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(
                Path(temp_dir) / "config.json",
                first_run_defaults=True,
            )

            response = import_local_provider(
                "codex",
                config_store=config_store,
                local_provider_detector=lambda: [
                    {
                        "provider_id": "codex",
                        "importable": False,
                    }
                ],
            )
            config = config_store.load()

        source = next(
            item for item in config.model_ingress.sources if item.id == "codex_local"
        )
        connection = next(
            item
            for item in config.model_ingress.connections
            if item.id == "codex-local-default"
        )
        self.assertFalse(response["ok"])
        self.assertEqual(response["error_category"], "local_login_unavailable")
        self.assertFalse(source.enabled)
        self.assertFalse(connection.enabled)
        self.assertTrue(all(not candidate.enabled for candidate in connection.model_candidates))

    def test_import_grok_build_enables_source_only_after_login_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            config = config_store.load()
            source = next(
                item for item in config.model_ingress.sources if item.id == "grok_local"
            )
            connection = next(
                item
                for item in config.model_ingress.connections
                if item.id == "grok-local-default"
            )
            self.assertFalse(source.enabled)
            self.assertFalse(connection.enabled)

            response = import_local_provider(
                "grok",
                config_store=config_store,
                grok_login_checker=lambda: None,
            )
            reloaded = config_store.load()

        self.assertTrue(response["ok"])
        self.assertEqual(response["message"], "已复用本机 Grok Build 登录态")
        self.assertTrue(
            next(
                item
                for item in reloaded.model_ingress.sources
                if item.id == "grok_local"
            ).enabled
        )
        self.assertTrue(
            next(
                item
                for item in reloaded.model_ingress.connections
                if item.id == "grok-local-default"
            ).enabled
        )
        grok_candidates = next(
            item
            for item in reloaded.model_ingress.connections
            if item.id == "grok-local-default"
        ).model_candidates
        self.assertEqual(
            [candidate.scan_profile for candidate in grok_candidates],
            ["low", "medium", "high"],
        )
        self.assertTrue(all(not candidate.enabled for candidate in grok_candidates))

    def test_import_grok_build_keeps_source_disabled_when_login_probe_fails(self) -> None:
        def unavailable() -> None:
            raise GrokBuildError(
                "authentication_required",
                "Grok Build 登录态不可用，请先运行 grok login。",
                {},
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")

            response = import_local_provider(
                "grok",
                config_store=config_store,
                grok_login_checker=unavailable,
            )
            reloaded = config_store.load()

        self.assertFalse(response["ok"])
        self.assertEqual(response["error_category"], "authentication_required")
        self.assertFalse(
            next(
                item
                for item in reloaded.model_ingress.sources
                if item.id == "grok_local"
            ).enabled
        )

    def test_import_claude_code_enables_source_only_after_login_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")

            response = import_local_provider(
                "claude",
                config_store=config_store,
                claude_login_checker=lambda: None,
            )
            reloaded = config_store.load()

        self.assertTrue(response["ok"])
        self.assertEqual(response["message"], "已复用本机 Claude Code 登录态")
        source = next(
            item for item in reloaded.model_ingress.sources if item.id == "claude_local"
        )
        connection = next(
            item
            for item in reloaded.model_ingress.connections
            if item.id == "claude-local-default"
        )
        self.assertTrue(source.enabled)
        self.assertTrue(connection.enabled)
        self.assertTrue(connection.local_login_verified)
        self.assertEqual(
            [candidate.scan_profile for candidate in connection.model_candidates],
            ["low", "medium", "high"],
        )
        self.assertTrue(all(not candidate.enabled for candidate in connection.model_candidates))

    def test_import_claude_code_keeps_source_disabled_when_login_probe_fails(self) -> None:
        def unavailable() -> None:
            raise ClaudeCodeError(
                "authentication_required",
                "Claude Code 登录态不可用，请先运行 claude auth login。",
                {},
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")

            response = import_local_provider(
                "claude",
                config_store=config_store,
                claude_login_checker=unavailable,
            )
            reloaded = config_store.load()

        self.assertFalse(response["ok"])
        self.assertEqual(response["error_category"], "authentication_required")
        self.assertFalse(
            next(
                item
                for item in reloaded.model_ingress.sources
                if item.id == "claude_local"
            ).enabled
        )

    def test_probe_endpoint_connection_uses_ephemeral_payload(self) -> None:
        captured = {}

        def requester(target, prompt, api_key):
            captured["target"] = target
            captured["prompt"] = prompt
            captured["api_key"] = api_key
            return object()

        response = probe_endpoint_connection(
            base_url="https://api.deepseek.com",
            api_format="openai_chat_completions",
            provider_preset="generic",
            model_id="deepseek-v4-flash",
            scan_profile="high",
            api_key="secret-value",
            requester=requester,
        )

        self.assertTrue(response["ok"])
        self.assertEqual(captured["target"].base_url, "https://api.deepseek.com")
        self.assertEqual(captured["target"].scan_profile, "high")
        self.assertEqual(captured["api_key"], "secret-value")
        self.assertNotIn("secret-value", str(response))

    def test_stream_repair_events_repairs_original_run_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            config = config_store.load()
            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = (
                        candidate.id == "codex-local-default:gpt-5.4:high"
                    )
            config_store.save(config)
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            seed_service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
            )
            target = seed_service.scan_target_resolver.enabled_targets(config)[0]
            run_id = "run-repair"
            for index, question in enumerate(
                seed_service.question_bank.load().enabled_questions,
                start=1,
            ):
                is_timeout = question.id == "02_code_counterexample_maxgap"
                history_store.append(
                    ScanResult(
                        run_id=run_id,
                        candidate_id=target.candidate_id,
                        model=target.model,
                        effort=target.effort,
                        phase="scan",
                        question_id=question.id,
                        question_title=question.title,
                        grader_kind=question.grader.kind,
                        attempt_index=index,
                        started_at="2026-07-14T10:00:00+08:00",
                        elapsed_seconds=300.0 if is_timeout else 1.0,
                        source_mode="live",
                        answer_ok=not is_timeout,
                        answer_preview="timeout" if is_timeout else "ok",
                        input_tokens=100,
                        output_tokens=20,
                        reasoning_tokens=None if is_timeout else 430,
                        error_message="timeout" if is_timeout else None,
                        final_status="warn" if is_timeout else "pass",
                    )
                )
            pack = seed_service.question_bank.metadata()
            history_store.save_run_metadata(
                {
                    "run_id": run_id,
                    "question_pack_id": pack.question_pack_id,
                    "question_pack_version": pack.question_pack_version,
                    "started_at": "2026-07-14T10:00:00+08:00",
                    "completed_at": "2026-07-14T10:05:00+08:00",
                    "candidate_count": 1,
                    "question_count": DEFAULT_QUESTION_COUNT,
                    "status": "degraded",
                    "selection_mode": "regular",
                    "requested_candidate_ids": [target.candidate_id],
                    "regular_candidate_ids": [target.candidate_id],
                    "is_complete_regular_round": False,
                }
            )
            calls: list[str] = []

            def repair_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                calls.append(question.id)
                return ScanResult(
                    run_id=str(kwargs["run_id"]),
                    candidate_id=target.candidate_id,
                    model=target.model,
                    effort=target.effort,
                    phase="scan",
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=int(kwargs["attempt_index"]),
                    started_at="2026-07-14T10:10:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                )

            events = list(
                stream_repair_events(
                    run_id=run_id,
                    candidate_id=target.candidate_id,
                    question_id="02_code_counterexample_maxgap",
                    config_store=config_store,
                    history_store=history_store,
                    active_run_store=active_run_store,
                    runner=repair_runner,
                )
            )

            self.assertEqual(events[0]["type"], "repair.started")
            self.assert_active_runtime_event_state(
                events[0],
                run_id=run_id,
                phase="repair",
                completed_targets=0,
                total_targets=1,
            )
            self.assertEqual(
                events[0]["repairable_question_ids"],
                ["02_code_counterexample_maxgap"],
            )
            self.assertEqual(events[0]["total_targets"], 1)
            self.assertEqual(events[-1]["type"], "repair.finished")
            self.assertEqual(events[-1]["run_id"], run_id)
            progress_events = [
                event
                for event in events
                if event["type"] in {
                    "repair.question.started",
                    "repair.question.finished",
                }
            ]
            self.assertTrue(progress_events)
            for event in progress_events:
                self.assertEqual(set(event["state"]), {"schema_version", "runtime"})
                self.assertEqual(event["state"]["schema_version"], 1)
            self.assertEqual(calls, ["02_code_counterexample_maxgap"])

            repeated_events = list(
                stream_repair_events(
                    run_id=run_id,
                    candidate_id=target.candidate_id,
                    config_store=config_store,
                    history_store=history_store,
                    active_run_store=active_run_store,
                    runner=repair_runner,
                )
            )
            self.assertEqual(repeated_events[-1]["type"], "repair.failed")
            self.assertIn("没有可重试", repeated_events[-1]["failure_message"])

    def test_stream_timed_out_repair_events_exposes_one_batch_operation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            candidate_ids = [
                "codex-local-default:gpt-5.4:high",
                "codex-local-default:gpt-5.5:high",
            ]
            repaired = [
                ScanResult(
                    run_id="run-timeouts",
                    candidate_id=candidate_ids[0],
                    model="gpt-5.4",
                    effort="high",
                    phase="scan",
                    question_id="q1",
                    started_at="2026-07-20T10:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=1,
                    output_tokens=1,
                    reasoning_tokens=1,
                )
            ]
            repair_plan = _mock_repair_plan(
                run_id="run-timeouts",
                candidate_ids=candidate_ids,
                question_ids=["q1"],
            )

            with (
                patch.object(
                    native_bridge_module.RepairCommand,
                    "plan_batch",
                    return_value=repair_plan,
                ),
                patch.object(
                    MonitorService,
                    "repair_timed_out_questions",
                    return_value=repaired,
                ) as repair,
                patch.object(
                    MonitorService,
                    "complete_finalizing_snapshot",
                    autospec=True,
                    side_effect=lambda _service, state, **_kwargs: state,
                ),
            ):
                events = list(
                    stream_timed_out_repair_events(
                        run_id="run-timeouts",
                        candidate_ids=candidate_ids,
                        config_store=config_store,
                        history_store=history_store,
                        active_run_store=active_run_store,
                    )
                )

            self.assertEqual(events[0]["type"], "timeout-repair.started")
            self.assert_active_runtime_event_state(
                events[0],
                run_id="run-timeouts",
                phase="repair",
                completed_targets=0,
                total_targets=1,
            )
            self.assertEqual(events[-2]["type"], "timeout-repair.finalizing")
            self.assertEqual(events[-2]["state_kind"], "runtime_delta")
            self.assertEqual(events[-2]["state"]["schema_version"], 1)
            self.assertEqual(events[-1]["type"], "timeout-repair.finished")
            self.assertEqual(events[-1]["result_count"], 1)
            repair.assert_called_once_with(
                run_id="run-timeouts",
                candidate_ids=candidate_ids,
                progress_callback=ANY,
                repair_plan=repair_plan,
                retain_finalizing_state=True,
            )

    def test_stream_failed_repair_events_exposes_one_batch_operation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            candidate_ids = [
                "codex-local-default:gpt-5.4:high",
                "codex-local-default:gpt-5.5:high",
            ]
            repair_plan = _mock_repair_plan(
                run_id="run-failures",
                candidate_ids=candidate_ids,
            )

            with (
                patch.object(
                    native_bridge_module.RepairCommand,
                    "plan_batch",
                    return_value=repair_plan,
                ),
                patch.object(
                    MonitorService,
                    "repair_failed_questions",
                    return_value=[],
                    create=True,
                ) as repair,
                patch.object(
                    MonitorService,
                    "complete_finalizing_snapshot",
                    autospec=True,
                    side_effect=lambda _service, state, **_kwargs: state,
                ),
            ):
                events = list(
                    native_bridge_module.stream_failed_repair_events(
                        run_id="run-failures",
                        candidate_ids=candidate_ids,
                        config_store=config_store,
                        history_store=history_store,
                        active_run_store=active_run_store,
                    )
                )

            self.assertEqual(events[0]["type"], "repair.started")
            self.assert_active_runtime_event_state(
                events[0],
                run_id="run-failures",
                phase="repair",
                completed_targets=0,
                total_targets=0,
            )
            self.assertEqual(events[-2]["type"], "repair.finalizing")
            self.assertEqual(events[-2]["state_kind"], "runtime_delta")
            self.assertEqual(events[-2]["state"]["schema_version"], 1)
            self.assertEqual(events[-1]["type"], "repair.finished")
            repair.assert_called_once_with(
                run_id="run-failures",
                candidate_ids=candidate_ids,
                progress_callback=ANY,
                repair_plan=repair_plan,
                retain_finalizing_state=True,
            )

    def test_stream_timed_out_repair_events_exposes_stopped_operation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            repair_plan = _mock_repair_plan(
                run_id="run-timeouts",
                candidate_ids=["codex-local-default:gpt-5.4:high"],
            )

            def stop_repair(service, **kwargs):  # type: ignore[no-untyped-def]
                service.last_control_action = "stop"
                service.active_run_store.clear()
                return []

            with (
                patch.object(
                    native_bridge_module.RepairCommand,
                    "plan_batch",
                    return_value=repair_plan,
                ),
                patch.object(
                    MonitorService,
                    "repair_timed_out_questions",
                    autospec=True,
                    side_effect=stop_repair,
                ),
            ):
                events = list(
                    stream_timed_out_repair_events(
                        run_id="run-timeouts",
                        candidate_ids=["codex-local-default:gpt-5.4:high"],
                        config_store=config_store,
                        history_store=history_store,
                        active_run_store=active_run_store,
                    )
                )

            self.assertEqual(events[0]["type"], "timeout-repair.started")
            self.assertEqual(events[-1]["type"], "timeout-repair.stopped")
            self.assert_authoritative_event_state(
                events[-1],
                lifecycle_state="idle",
                is_running=False,
                has_resumable_run=False,
            )
            self.assertNotIn(
                "timeout-repair.finalizing",
                [event["type"] for event in events],
            )

    def test_stream_repair_events_exposes_pause_and_stop_operations(self) -> None:
        for action in ("pause", "stop"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as temp_dir:
                config_store = ConfigStore(Path(temp_dir) / "config.json")
                history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
                active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
                candidate_id = "codex-local-default:gpt-5.4:high"
                repair_plan = _mock_repair_plan(
                    run_id="run-repair",
                    candidate_ids=[candidate_id],
                )

                def control_repair(service, **kwargs):  # type: ignore[no-untyped-def]
                    service.last_control_action = action
                    if action == "pause":
                        self.save_active_run_for_event_state(
                            service,
                            run_id="run-repair",
                            candidate_id=candidate_id,
                            lifecycle_state="paused_recoverable",
                            phase="repair",
                        )
                    else:
                        service.active_run_store.clear()
                    return []

                with (
                    patch.object(
                        native_bridge_module.RepairCommand,
                        "plan_candidate",
                        return_value=repair_plan,
                    ),
                    patch.object(
                        MonitorService,
                        "repair_failed_candidate",
                        autospec=True,
                        side_effect=control_repair,
                    ),
                ):
                    events = list(
                        stream_repair_events(
                            run_id="run-repair",
                            candidate_id=candidate_id,
                            config_store=config_store,
                            history_store=history_store,
                            active_run_store=active_run_store,
                        )
                    )

                self.assertEqual(events[0]["type"], "repair.started")
                self.assertEqual(
                    events[-1]["type"],
                    "repair.paused" if action == "pause" else "repair.stopped",
                )
                self.assert_authoritative_event_state(
                    events[-1],
                    lifecycle_state=(
                        "paused_recoverable" if action == "pause" else "idle"
                    ),
                    is_running=False,
                    has_resumable_run=action == "pause",
                )
                self.assertNotIn(
                    "repair.finalizing",
                    [event["type"] for event in events],
                )

    def test_stream_scan_control_terminal_includes_authoritative_state(self) -> None:
        for action in ("pause", "stop"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as temp_dir:
                config_store = ConfigStore(Path(temp_dir) / "config.json")
                history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
                active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
                candidate_id = "codex-local-default:gpt-5.4:high"

                def controlled_scan(service, **kwargs):  # type: ignore[no-untyped-def]
                    service.last_control_action = action
                    if action == "pause":
                        self.save_active_run_for_event_state(
                            service,
                            run_id="run-paused-scan",
                            candidate_id=candidate_id,
                            lifecycle_state="paused_recoverable",
                            phase="scan",
                        )
                    else:
                        service.active_run_store.clear()
                    return []

                with patch.object(
                    MonitorService,
                    "run_enabled_targets",
                    autospec=True,
                    side_effect=controlled_scan,
                ):
                    events = list(
                        stream_scan_events(
                            config_store=config_store,
                            history_store=history_store,
                            active_run_store=active_run_store,
                        )
                    )

                terminal = events[-1]
                self.assertEqual(
                    terminal["type"],
                    "scan.paused" if action == "pause" else "scan.stopped",
                )
                self.assert_authoritative_event_state(
                    terminal,
                    lifecycle_state=(
                        "paused_recoverable" if action == "pause" else "idle"
                    ),
                    is_running=False,
                    has_resumable_run=action == "pause",
                )

    def test_scan_control_projection_failure_emits_authoritative_snapshot(self) -> None:
        for action in ("pause", "stop"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as temp_dir:
                config_store = ConfigStore(Path(temp_dir) / "config.json")
                history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
                active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
                candidate_id = "codex-local-default:gpt-5.4:high"

                def controlled_scan(service, **kwargs):  # type: ignore[no-untyped-def]
                    service.last_control_action = action
                    if action == "pause":
                        self.save_active_run_for_event_state(
                            service,
                            run_id="run-paused-scan",
                            candidate_id=candidate_id,
                            lifecycle_state="paused_recoverable",
                            phase="scan",
                        )
                    else:
                        service.run_state_machine.transition("stopped")
                        service.active_run_store.clear()
                    return []

                with (
                    patch.object(
                        MonitorService,
                        "run_enabled_targets",
                        autospec=True,
                        side_effect=controlled_scan,
                    ),
                    patch.object(
                        native_bridge_module,
                        "_build_command_snapshot",
                        side_effect=RuntimeError("control snapshot failed"),
                    ),
                ):
                    events = list(
                        stream_scan_events(
                            config_store=config_store,
                            history_store=history_store,
                            active_run_store=active_run_store,
                        )
                    )

                terminal = events[-1]
                self.assertEqual(terminal["type"], "scan.failed")
                self.assertEqual(
                    terminal["failure_category"],
                    "scan_terminal_projection_failed",
                )
                self.assertEqual(terminal["control_action"], action)
                self.assert_failure_snapshot_state(
                    terminal,
                    lifecycle_state=(
                        "paused_recoverable" if action == "pause" else "idle"
                    ),
                )

    def test_repair_control_projection_failure_emits_authoritative_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            candidate_id = "codex-local-default:gpt-5.4:high"
            repair_plan = _mock_repair_plan(
                run_id="run-repair",
                candidate_ids=[candidate_id],
            )

            def pause_repair(service, **kwargs):  # type: ignore[no-untyped-def]
                service.last_control_action = "pause"
                self.save_active_run_for_event_state(
                    service,
                    run_id="run-repair",
                    candidate_id=candidate_id,
                    lifecycle_state="paused_recoverable",
                    phase="repair",
                )
                return []

            with (
                patch.object(
                    native_bridge_module.RepairCommand,
                    "plan_candidate",
                    return_value=repair_plan,
                ),
                patch.object(
                    MonitorService,
                    "repair_failed_candidate",
                    autospec=True,
                    side_effect=pause_repair,
                ),
                patch.object(
                    native_bridge_module,
                    "_build_command_snapshot",
                    side_effect=RuntimeError("repair snapshot failed"),
                ),
            ):
                events = list(
                    stream_repair_events(
                        run_id="run-repair",
                        candidate_id=candidate_id,
                        config_store=config_store,
                        history_store=history_store,
                        active_run_store=active_run_store,
                    )
                )

            terminal = events[-1]
            self.assertEqual(terminal["type"], "repair.failed")
            self.assertEqual(
                terminal["failure_category"],
                "repair_terminal_projection_failed",
            )
            self.assertEqual(terminal["control_action"], "pause")
            self.assert_failure_snapshot_state(
                terminal,
                lifecycle_state="paused_recoverable",
            )

    def test_timeout_repair_control_projection_failure_emits_authoritative_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            repair_plan = _mock_repair_plan(
                run_id="run-timeouts",
                candidate_ids=["codex-local-default:gpt-5.4:high"],
            )

            def stop_repair(service, **kwargs):  # type: ignore[no-untyped-def]
                service.last_control_action = "stop"
                service.run_state_machine.transition("stopped")
                service.active_run_store.clear()
                return []

            with (
                patch.object(
                    native_bridge_module.RepairCommand,
                    "plan_batch",
                    return_value=repair_plan,
                ),
                patch.object(
                    MonitorService,
                    "repair_timed_out_questions",
                    autospec=True,
                    side_effect=stop_repair,
                ),
                patch.object(
                    native_bridge_module,
                    "_build_command_snapshot",
                    side_effect=RuntimeError("timeout repair snapshot failed"),
                ),
            ):
                events = list(
                    stream_timed_out_repair_events(
                        run_id="run-timeouts",
                        candidate_ids=["codex-local-default:gpt-5.4:high"],
                        config_store=config_store,
                        history_store=history_store,
                        active_run_store=active_run_store,
                    )
                )

            terminal = events[-1]
            self.assertEqual(terminal["type"], "timeout-repair.failed")
            self.assertEqual(
                terminal["failure_category"],
                "timeout_repair_terminal_projection_failed",
            )
            self.assertEqual(terminal["control_action"], "stop")
            self.assert_failure_snapshot_state(
                terminal,
                lifecycle_state="idle",
            )

    def test_repair_completion_projection_failure_emits_authoritative_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_store = ConfigStore(root / "config.json")
            history_store = HistoryStore(root / "history.jsonl")
            active_run_store = ActiveRunStore(root / "active_run.json")
            repair_plan = _mock_repair_plan(
                run_id="run-repair",
                candidate_ids=["codex-local-default:gpt-5.4:high"],
            )

            with (
                patch.object(
                    native_bridge_module.RepairCommand,
                    "plan_candidate",
                    return_value=repair_plan,
                ),
                patch.object(
                    MonitorService,
                    "repair_failed_candidate",
                    autospec=True,
                    side_effect=lambda service, **_kwargs: (
                        self.save_finalizing_repair_checkpoint(
                            service,
                            run_id="run-repair",
                        )
                    ),
                ),
                patch.object(
                    native_bridge_module,
                    "_build_command_snapshot",
                    side_effect=RuntimeError("repair projection failed"),
                ),
            ):
                events = list(
                    stream_repair_events(
                        run_id="run-repair",
                        candidate_id="codex-local-default:gpt-5.4:high",
                        config_store=config_store,
                        history_store=history_store,
                        active_run_store=active_run_store,
                    )
                )

            self.assertEqual(events[-2]["type"], "repair.finalizing")
            terminal = events[-1]
            self.assertEqual(terminal["type"], "repair.failed")
            self.assertEqual(
                terminal["failure_category"],
                "repair_terminal_projection_failed",
            )
            self.assert_failure_snapshot_state(
                terminal,
                lifecycle_state="finalizing",
            )
            active = active_run_store.load()
            self.assertIsNotNone(active)
            self.assertEqual(active["runtime"]["lifecycle_state"], "finalizing")  # type: ignore[index]
            self.assertEqual(active["runtime"]["last_error"], "repair projection failed")  # type: ignore[index]
            journal = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
            ).run_journal_store.load_events("run-repair")
            self.assertEqual(journal[-1]["type"], "run.projection_failed")

    def test_batch_repair_completion_projection_failure_emits_authoritative_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_store = ConfigStore(root / "config.json")
            history_store = HistoryStore(root / "history.jsonl")
            active_run_store = ActiveRunStore(root / "active_run.json")
            repair_plan = _mock_repair_plan(
                run_id="run-timeouts",
                candidate_ids=["codex-local-default:gpt-5.4:high"],
            )

            with (
                patch.object(
                    native_bridge_module.RepairCommand,
                    "plan_batch",
                    return_value=repair_plan,
                ),
                patch.object(
                    MonitorService,
                    "repair_timed_out_questions",
                    autospec=True,
                    side_effect=lambda service, **_kwargs: (
                        self.save_finalizing_repair_checkpoint(
                            service,
                            run_id="run-timeouts",
                        )
                    ),
                ),
                patch.object(
                    native_bridge_module,
                    "_build_command_snapshot",
                    side_effect=RuntimeError("batch projection failed"),
                ),
            ):
                events = list(
                    stream_timed_out_repair_events(
                        run_id="run-timeouts",
                        candidate_ids=["codex-local-default:gpt-5.4:high"],
                        config_store=config_store,
                        history_store=history_store,
                        active_run_store=active_run_store,
                    )
                )

            self.assertEqual(events[-2]["type"], "timeout-repair.finalizing")
            terminal = events[-1]
            self.assertEqual(terminal["type"], "timeout-repair.failed")
            self.assertEqual(
                terminal["failure_category"],
                "timeout_repair_terminal_projection_failed",
            )
            self.assert_failure_snapshot_state(
                terminal,
                lifecycle_state="finalizing",
            )
            active = active_run_store.load()
            self.assertIsNotNone(active)
            self.assertEqual(active["runtime"]["lifecycle_state"], "finalizing")  # type: ignore[index]
            self.assertEqual(active["runtime"]["last_error"], "batch projection failed")  # type: ignore[index]

    def test_discover_connection_models_classifies_new_and_configured_models(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            config = config_store.load()
            config.model_ingress.connections.append(
                ConnectionConfig(
                    id="api-1",
                    source_id="custom_endpoint",
                    name="Team Gateway",
                    enabled=True,
                    api_format="openai_chat_completions",
                    base_url="https://example.com/v1",
                    api_key_ref="env:MODELDIAL_TEST_KEY",
                    model_candidates=[
                        ModelCandidateConfig(
                            id="api-1:model-b:default",
                            connection_id="api-1",
                            model_id="model-b",
                            display_name="model-b",
                            enabled=False,
                            scan_profile="default",
                        )
                    ],
                )
            )
            config_store.save(config)

            response = discover_connection_models(
                "api-1",
                config_store=config_store,
                secret_store=type("Secrets", (), {"resolve": lambda self, reference: "api-secret"})(),
                discoverer=lambda base_url, api_key, *, api_format: ["model-c", "model-b", "model-a", "model-c"],
            )

            self.assertEqual(response["models"], ["model-a", "model-b", "model-c"])
            self.assertEqual(response["new_models"], ["model-a", "model-c"])
            self.assertEqual(response["configured_models"], ["model-b"])

    def test_discover_connection_models_passes_anthropic_api_format(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            config = config_store.load()
            config.model_ingress.connections.append(
                ConnectionConfig(
                    id="anthropic-1",
                    source_id="custom_endpoint",
                    name="Claude Gateway",
                    enabled=True,
                    api_format="anthropic_messages",
                    base_url="https://example.com/v1",
                    api_key_ref="env:MODELDIAL_TEST_KEY",
                )
            )
            config_store.save(config)
            captured = {}

            response = discover_connection_models(
                "anthropic-1",
                config_store=config_store,
                secret_store=type("Secrets", (), {"resolve": lambda self, reference: "api-secret"})(),
                discoverer=lambda base_url, api_key, *, api_format: (
                    captured.update(api_format=api_format) or ["claude-fable-5"]
                ),
            )

            self.assertTrue(response["ok"])
            self.assertEqual(captured["api_format"], "anthropic_messages")

    def test_discover_connection_models_returns_sorted_unique_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            config = config_store.load()
            config.model_ingress.connections.append(
                ConnectionConfig(
                    id="api-1",
                    source_id="custom_endpoint",
                    name="Team Gateway",
                    enabled=True,
                    api_format="openai_chat_completions",
                    base_url="https://example.com/v1",
                    api_key_ref="env:MODELDIAL_TEST_KEY",
                )
            )
            config_store.save(config)

            class FakeSecrets:
                def resolve(self, reference):  # type: ignore[no-untyped-def]
                    self.reference = reference
                    return "api-secret"

            response = discover_connection_models(
                "api-1",
                config_store=config_store,
                secret_store=FakeSecrets(),
                discoverer=lambda base_url, api_key, *, api_format: ["z-model", "a-model", "z-model"],
            )

            self.assertTrue(response["ok"])
            self.assertEqual(response["models"], ["a-model", "z-model"])
            self.assertTrue(response["manual_entry_allowed"])

    def test_discovery_returns_reasoning_profiles_by_model(self) -> None:
        from scanner.endpoint_client import DiscoveredModel

        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            config = config_store.load()
            config.model_ingress.connections.append(
                ConnectionConfig(
                    id="api-1",
                    source_id="custom_endpoint",
                    name="Team Gateway",
                    enabled=True,
                    api_format="openai_chat_completions",
                    base_url="https://example.com/v1",
                    api_key_ref="env:MODELDIAL_TEST_KEY",
                )
            )
            config_store.save(config)

            response = discover_connection_models(
                "api-1",
                config_store=config_store,
                secret_store=type("Secrets", (), {"resolve": lambda self, reference: "api-secret"})(),
                discoverer=lambda base_url, api_key, *, api_format: [
                    DiscoveredModel("model-a", ("low", "high", "max"), "high")
                ],
            )

            self.assertEqual(response["models"], ["model-a"])
            self.assertEqual(
                response["reasoning_profiles_by_model"],
                {"model-a": ["low", "high", "max"]},
            )
            self.assertEqual(
                response["default_reasoning_profile_by_model"],
                {"model-a": "high"},
            )

    def test_discover_failure_keeps_manual_entry_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            config = config_store.load()
            config.model_ingress.connections.append(
                ConnectionConfig(
                    id="api-1",
                    source_id="custom_endpoint",
                    name="Team Gateway",
                    enabled=True,
                    api_format="openai_chat_completions",
                    base_url="https://example.com/v1",
                    api_key_ref="env:MODELDIAL_TEST_KEY",
                )
            )
            config_store.save(config)

            response = discover_connection_models(
                "api-1",
                config_store=config_store,
                secret_store=type("Secrets", (), {"resolve": lambda self, reference: "api-secret"})(),
                discoverer=lambda base_url, api_key, *, api_format: (_ for _ in ()).throw(
                    EndpointError("authentication_failed", 401)
                ),
            )

            self.assertFalse(response["ok"])
            self.assertEqual(response["error_category"], "authentication_failed")
            self.assertTrue(response["manual_entry_allowed"])

    def test_verify_endpoint_connection_persists_only_safe_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            config = config_store.load()
            config.model_ingress.connections.append(
                ConnectionConfig(
                    id="api-1",
                    source_id="custom_endpoint",
                    name="Team Gateway",
                    enabled=True,
                    api_format="openai_responses",
                    base_url="https://example.com/v1",
                    api_key_ref="env:MODELDIAL_TEST_KEY",
                    model_candidates=[
                        ModelCandidateConfig(
                            id="api-1:gpt-test:medium",
                            connection_id="api-1",
                            model_id="gpt-test",
                            display_name="GPT Test Medium",
                            enabled=True,
                            scan_profile="medium",
                        ),
                        ModelCandidateConfig(
                            id="api-1:gpt-other:max",
                            connection_id="api-1",
                            model_id="gpt-other",
                            display_name="GPT Other Max",
                            enabled=True,
                            scan_profile="max",
                        )
                    ],
                )
            )
            config_store.save(config)
            captured: list[tuple[str, str]] = []

            def requester(target, prompt, api_key):  # type: ignore[no-untyped-def]
                captured.append((target.model_id, target.scan_profile))
                return EndpointResult(
                    text="OK",
                    input_tokens=1,
                    output_tokens=1,
                    reasoning_tokens=None,
                )

            response = verify_endpoint_connection(
                "api-1",
                "gpt-test",
                config_store=config_store,
                secret_store=type("Secrets", (), {"resolve": lambda self, reference: "api-secret"})(),
                requester=requester,
            )
            connection = next(
                item
                for item in config_store.load().model_ingress.connections
                if item.id == "api-1"
            )

            self.assertTrue(response["ok"])
            self.assertEqual(connection.last_test_status, "ok")
            self.assertEqual(connection.last_test_message, "连接成功")
            self.assertIsNotNone(connection.last_test_at)
            saved = json.dumps(connection.to_dict(), ensure_ascii=False)
            self.assertNotIn("api-secret", saved)
            self.assertNotIn("OK", saved)
            self.assertEqual(
                captured,
                [("gpt-test", "medium"), ("gpt-other", "max")],
            )

    def test_swift_bridge_injects_homebrew_path_for_gui_launches(self) -> None:
        source = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Model"
            / "NativeBridgeClient.swift"
        ).read_text(encoding="utf-8")

        self.assertIn("fallbackPathComponents", source)
        self.assertIn('"/opt/homebrew/bin"', source)
        self.assertIn('"/usr/local/bin"', source)
        self.assertIn('environment["PATH"]', source)

    def test_swift_bridge_prefers_the_bundled_backend_runtime(self) -> None:
        source = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Model"
            / "NativeBridgeClient.swift"
        ).read_text(encoding="utf-8")

        self.assertIn('appendingPathComponent("Runtime/modeldial-backend")', source)
        self.assertIn("private func bridgeInvocation(", source)
        self.assertIn('environment["MODELDIAL_BACKEND_ROOT"] = repoRoot.path', source)
        self.assertNotIn('URL(fileURLWithPath: "/usr/bin/python3")', source)

    def test_swift_bridge_passes_only_scan_required_managed_secrets_through_stdin(self) -> None:
        root = Path(__file__).resolve().parent.parent
        source = (
            root / "Sources"
            / "Model"
            / "NativeBridgeClient.swift"
        ).read_text(encoding="utf-8")
        models_source = (
            root / "Sources" / "Model" / "SelectionModels.swift"
        ).read_text(encoding="utf-8")

        self.assertIn('"--secret-stdin"', source)
        self.assertIn("process.standardInput = inputPipe", source)
        self.assertIn("secretStore.bridgeSecret(", source)
        self.assertIn(
            "func secretBackedConnectionIDs(for candidateIDs: [String]?) -> [String]",
            models_source,
        )
        self.assertIn("if let requestedCandidateIDs", models_source)
        self.assertIn("requestedCandidateIDs.contains(candidate.id)", models_source)
        self.assertIn("return candidate.enabled", models_source)
        self.assertIn("if requestedCandidateIDs != nil {", models_source)
        self.assertIn("guard source.enabled && connection.enabled", models_source)
        self.assertIn('source.mode != "api" || connection.lastTestStatus == "ok"', models_source)
        self.assertIn('apiKeyRef.hasPrefix("keychain:")', models_source)
        self.assertIn("LocalEncryptedSecretStore.referencePrefix", models_source)
        self.assertNotIn('environment["MODELDIAL_API_KEY"]', source)
        self.assertNotIn('"--api-key"', source)

    def test_python_bridge_installs_stdin_secrets_before_commands_run(self) -> None:
        source = (
            Path(__file__).resolve().parent.parent
            / "scanner"
            / "native_bridge.py"
        ).read_text(encoding="utf-8")

        self.assertIn('parser.add_argument("--secret-stdin"', source)
        self.assertIn("install_process_secret_overrides", source)
        self.assertIn("json.load(sys.stdin)", source)

    def test_frozen_runtime_rewrites_python_worker_commands_to_its_dispatch_entrypoint(self) -> None:
        import scanner.frozen_runtime as frozen_runtime

        with patch.object(frozen_runtime.sys, "frozen", True, create=True):
            module_command = frozen_runtime.module_worker_command(
                "scanner.endpoint_client",
                "--execute-request",
            )
            code_command = frozen_runtime.python_code_worker_command(
                'print("ok")',
                "test_name",
            )

        self.assertEqual(
            module_command,
            [
                frozen_runtime.sys.executable,
                "--modeldial-worker",
                "scanner.endpoint_client",
                "--execute-request",
            ],
        )
        self.assertEqual(
            code_command,
            [
                frozen_runtime.sys.executable,
                "--modeldial-python-code",
                'print("ok")',
                "test_name",
            ],
        )

    def test_frozen_runtime_entrypoint_dispatches_before_the_regular_bridge_cli(self) -> None:
        source = (
            Path(__file__).resolve().parent.parent
            / "scripts"
            / "native_bridge.py"
        ).read_text(encoding="utf-8")

        self.assertIn("dispatch_frozen_worker", source)
        self.assertIn("multiprocessing.freeze_support()", source)
        self.assertIn("configure_frozen_tls_trust()", source)
        self.assertLess(
            source.index("configure_frozen_tls_trust()"),
            source.index("dispatch_frozen_worker(sys.argv[1:])"),
        )

    def test_frozen_runtime_uses_only_its_bundled_ca_file(self) -> None:
        import scanner.frozen_runtime as frozen_runtime

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir) / "_internal"
            certificate_bundle = runtime_root / "certifi" / "cacert.pem"
            certificate_bundle.parent.mkdir(parents=True)
            certificate_bundle.write_text("test certificate bundle", encoding="utf-8")
            certifi_module = types.ModuleType("certifi")
            certifi_module.where = lambda: str(certificate_bundle)

            with (
                patch.object(frozen_runtime.sys, "frozen", True, create=True),
                patch.object(
                    frozen_runtime.sys,
                    "_MEIPASS",
                    str(runtime_root),
                    create=True,
                ),
                patch.dict(sys.modules, {"certifi": certifi_module}),
                patch.dict(os.environ, {}, clear=True),
            ):
                frozen_runtime.configure_frozen_tls_trust()
                self.assertEqual(
                    os.environ["SSL_CERT_FILE"],
                    str(certificate_bundle.resolve()),
                )

    def test_swift_bridge_keeps_scan_json_stream_separate_from_stderr_logs(self) -> None:
        source = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Model"
            / "NativeBridgeClient.swift"
        ).read_text(encoding="utf-8")

        self.assertIn("let outputPipe = Pipe()", source)
        self.assertIn("let errorPipe = Pipe()", source)
        self.assertIn("process.standardOutput = outputPipe", source)
        self.assertIn("process.standardError = errorPipe", source)
        self.assertNotIn("process.standardError = pipe", source)

    def test_swift_non_stream_bridge_drains_both_pipes_and_has_a_deadline(self) -> None:
        source = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Model"
            / "NativeBridgeClient.swift"
        ).read_text(encoding="utf-8")

        run_source = source.split(
            "private func run(arguments: [String], secretInput: Data? = nil) throws -> String {",
            1,
        )[1].split("private static func decodingErrorDetail", 1)[0]
        self.assertIn("BridgeProcessOutputCollector", run_source)
        self.assertIn("DispatchGroup", run_source)
        self.assertIn("DispatchQueue.global", run_source)
        self.assertIn("terminationSemaphore.wait(timeout:", run_source)
        self.assertIn("BridgeClientError.processTimedOut", run_source)
        self.assertNotIn(
            "let outputData = outputPipe.fileHandleForReading.readDataToEndOfFile()\n"
            "        let errorData = errorPipe.fileHandleForReading.readDataToEndOfFile()",
            run_source,
        )

    def test_swift_bridge_passes_targeted_scan_selection_to_python(self) -> None:
        source = (
            Path(__file__).resolve().parent.parent
            / "Sources"
            / "Model"
            / "NativeBridgeClient.swift"
        ).read_text(encoding="utf-8")

        self.assertIn("intent: BridgeScanIntent", source)
        self.assertIn("intent.selectionMode.rawValue", source)
        self.assertIn('"--selection-mode"', source)
        self.assertIn('"--candidate-id"', source)
        native_source = (
            Path(__file__).resolve().parent.parent / "scanner" / "native_bridge.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"incremental_full"', native_source)
        self.assertNotIn("reviewOnly", source)
        self.assertNotIn('"--review-only"', source)

    def test_swift_bridge_prefers_bundled_backend_and_uses_application_support_data(self) -> None:
        root = Path(__file__).resolve().parent.parent
        bridge_source = (
            root / "Sources" / "Model" / "NativeBridgeClient.swift"
        ).read_text(encoding="utf-8")

        self.assertIn('appendingPathComponent("Backend", isDirectory: true)', bridge_source)
        self.assertIn('appendingPathComponent("Application Support", isDirectory: true)', bridge_source)
        self.assertIn('environment["MODELDIAL_DATA_DIR"]', bridge_source)
        self.assertIn('environment["PYTHONDONTWRITEBYTECODE"] = "1"', bridge_source)
        self.assertIn('"--config-path"', bridge_source)
        self.assertIn('"--history-path"', bridge_source)
        self.assertIn('"--active-run-path"', bridge_source)

    def test_native_bridge_has_no_manual_review_mode(self) -> None:
        source = (
            Path(__file__).resolve().parent.parent
            / "scanner"
            / "native_bridge.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("review_only", source)
        self.assertNotIn('"--review-only"', source)

    def test_native_bridge_converts_nonzero_process_exit_into_failure_event(self) -> None:
        root = Path(__file__).resolve().parent.parent
        bridge_source = (
            root / "Sources" / "Model" / "NativeBridgeClient.swift"
        ).read_text(encoding="utf-8")
        model_source = (
            root / "Sources" / "Model" / "SelectionModels.swift"
        ).read_text(encoding="utf-8")

        self.assertIn("process.terminationStatus != 0", bridge_source)
        self.assertIn("ScanEvent.bridgeFailure", bridge_source)
        self.assertIn('failureCategory: "bridge_process_failed"', model_source)

    def test_native_scan_controls_expose_pause_stop_and_resume_ui(self) -> None:
        root = Path(__file__).resolve().parent.parent
        view_source = (
            root / "Sources" / "Views" / "ExpandedSelectionView.swift"
        ).read_text(encoding="utf-8")

        self.assertIn('return L10n.tr("暂停")', view_source)
        self.assertIn("store.pauseScan()", view_source)
        self.assertIn("store.dismissResumableRun()", view_source)
        self.assertIn("Button(action: performScanControlAction)", view_source)
        self.assertIn("Button(stopScanActionTitle)", view_source)
        self.assertIn('return L10n.tr("继续扫描")', view_source)

    @patch("scanner.native_bridge._terminate_scan_child_processes", return_value=4)
    def test_stop_control_terminates_active_scan_children(self, terminate) -> None:  # type: ignore[no-untyped-def]
        with tempfile.TemporaryDirectory() as temp_dir:
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            active_run_store.save({"run_id": "run-active"})

            response = request_scan_control("stop", active_run_store)

            self.assertTrue(response["ok"])
            self.assertEqual(response["terminated_process_count"], 4)
            terminate.assert_called_once_with(
                active_run_store.path.with_name("scan.lock")
            )
            control_payload = json.loads(
                active_run_store.control_path.read_text(encoding="utf-8")
            )
            self.assertEqual(control_payload["action"], "stop")
            self.assertEqual(control_payload["schema_version"], 1)
            self.assertEqual(control_payload["run_id"], "run-active")
            self.assertTrue(control_payload["request_id"])

    @patch("scanner.native_bridge._terminate_scan_child_processes", return_value=2)
    def test_pause_control_terminates_active_scan_children(self, terminate) -> None:  # type: ignore[no-untyped-def]
        with tempfile.TemporaryDirectory() as temp_dir:
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            active_run_store.save({"run_id": "run-active"})

            response = request_scan_control("pause", active_run_store)

            self.assertTrue(response["ok"])
            self.assertEqual(response["terminated_process_count"], 2)
            terminate.assert_called_once_with(
                active_run_store.path.with_name("scan.lock")
            )

    def test_terminate_scan_children_escalates_when_sigterm_is_ignored(self) -> None:
        with patch.object(native_bridge_module, "_read_lock_payload", return_value=(101, time.time())), \
             patch.object(native_bridge_module, "_lock_is_stale", return_value=False), \
             patch.object(native_bridge_module, "_scan_child_process_ids", return_value=[202]), \
             patch.object(native_bridge_module, "_process_is_alive", side_effect=[True, True]), \
             patch.object(native_bridge_module.time, "sleep"), \
             patch.object(native_bridge_module.os, "kill") as kill:
            terminated = native_bridge_module._terminate_scan_child_processes(
                Path("/tmp/scan.lock")
            )

        self.assertEqual(terminated, 1)
        self.assertEqual(
            kill.call_args_list,
            [
                call(202, native_bridge_module.signal.SIGTERM),
                call(202, native_bridge_module.signal.SIGKILL),
            ],
        )

    def test_windows_scan_child_discovery_walks_the_process_tree(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="202 101\n303 202\n404 101\n",
            stderr="",
        )
        with patch.dict(
            os.environ,
            {"FAKE_NATIVE_BRIDGE_SECRET": "native-fake-secret"},
            clear=False,
        ), \
             patch.object(native_bridge_module.os, "name", "nt"), \
             patch.object(native_bridge_module.subprocess, "run", return_value=completed) as run:
            descendants = native_bridge_module._scan_child_process_ids(101)

        self.assertEqual(set(descendants), {202, 303, 404})
        run.assert_called_once_with(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Get-CimInstance Win32_Process | ForEach-Object { '{0} {1}' -f $_.ProcessId, $_.ParentProcessId }",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
            env=ANY,
        )
        child_environment = run.call_args.kwargs["env"]
        self.assertNotIn("FAKE_NATIVE_BRIDGE_SECRET", child_environment)
        self.assertNotIn("native-fake-secret", child_environment.values())

    def test_windows_scan_child_termination_uses_taskkill_tree_mode(self) -> None:
        lock_path = Path("scan.lock")
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )
        with patch.dict(
            os.environ,
            {"FAKE_NATIVE_BRIDGE_SECRET": "native-fake-secret"},
            clear=False,
        ), \
             patch.object(native_bridge_module.os, "name", "nt"), \
             patch.object(native_bridge_module, "_read_lock_payload", return_value=(101, time.time())), \
             patch.object(native_bridge_module, "_lock_is_stale", return_value=False), \
             patch.object(native_bridge_module, "_scan_child_process_ids", return_value=[202]), \
             patch.object(native_bridge_module, "_process_is_alive", return_value=True), \
             patch.object(native_bridge_module.subprocess, "run", return_value=completed) as run, \
             patch.object(native_bridge_module.os, "kill") as kill:
            terminated = native_bridge_module._terminate_scan_child_processes(lock_path)

        self.assertEqual(terminated, 1)
        run.assert_called_once_with(
            ["taskkill.exe", "/PID", "202", "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
            env=ANY,
        )
        child_environment = run.call_args.kwargs["env"]
        self.assertNotIn("FAKE_NATIVE_BRIDGE_SECRET", child_environment)
        self.assertNotIn("native-fake-secret", child_environment.values())
        kill.assert_not_called()

    def test_dismiss_resumable_run_marks_history_terminal_before_clearing_active_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
            )
            target = service.scan_target_resolver.enabled_targets(config_store.load())[0]
            history_store.append(
                ScanResult(
                    run_id="run-recoverable",
                    candidate_id=target.candidate_id,
                    model=target.model,
                    effort=target.effort,
                    phase="scan",
                    question_id="01_session_bundle_repair",
                    question_title="Q1",
                    grader_kind="test",
                    attempt_index=1,
                    started_at="2026-07-22T10:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="mock",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=1,
                    output_tokens=1,
                    reasoning_tokens=1,
                )
            )
            active_run_store.save(
                {
                    "run_id": "run-recoverable",
                    "run_metadata": {
                        "run_id": "run-recoverable",
                        "status": "paused",
                        "completed_at": None,
                        "question_count": DEFAULT_QUESTION_COUNT,
                        "requested_candidate_ids": [target.candidate_id],
                    },
                    "runtime": {
                        "lifecycle_state": "paused_recoverable",
                    },
                }
            )

            response = dismiss_resumable_run(active_run_store, history_store)

            self.assertTrue(response["ok"])
            self.assertEqual(response["action"], "dismiss")
            self.assertIsNone(active_run_store.load())
            metadata = history_store.load_run_metadata("run-recoverable")
            self.assertIsNotNone(metadata)
            self.assertEqual(metadata["status"], "stopped")
            self.assertIsNotNone(metadata["completed_at"])
            self.assertIsNotNone(metadata["dismissed_at"])
            self.assertFalse(service.build_state()["runtime"]["has_resumable_run"])

    def test_script_snapshot_entrypoint_outputs_json(self) -> None:
        output = subprocess.check_output(
            ["python3", "scripts/native_bridge.py", "snapshot"],
            text=True,
            cwd=Path(__file__).resolve().parent.parent,
        )

        payload = json.loads(output)

        self.assertIn("runtime", payload)
        self.assertIn("dashboard", payload)
        self.assertIn("config", payload)
        self.assertIn("model_ingress", payload["config"])
        self.assertIn("provider_catalog", payload["config"])
        self.assertIn("sources", payload["config"]["model_ingress"])
        self.assertIn("connections", payload["config"]["model_ingress"])

    def test_script_read_config_entrypoint_outputs_only_effective_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.json"
            config_store = ConfigStore(config_path)
            config = config_store.load()
            config.recommendation.preference = "cost"
            config_store.save(config)

            output = subprocess.check_output(
                [
                    "python3",
                    "scripts/native_bridge.py",
                    "read-config",
                    "--config-path",
                    str(config_path),
                ],
                text=True,
                cwd=Path(__file__).resolve().parent.parent,
            )

            payload = json.loads(output)
            created_files = {
                path.relative_to(root)
                for path in root.rglob("*")
                if path.is_file()
            }

        self.assertEqual(
            set(payload),
            {
                "model_ingress",
                "recommendation",
                "scheduler",
                "scan_budget",
                "system",
                "rules",
            },
        )
        self.assertEqual(payload["recommendation"]["preference"], "cost")
        self.assertEqual(created_files, {Path("config.json")})

    def test_script_secret_reference_migration_returns_only_ack(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.json"
            config_store = ConfigStore(config_path, first_run_defaults=True)
            config = config_store.load()
            connection = config.model_ingress.connections[0]
            old_reference = (
                f"keychain:com.modelpilot.api-key:{connection.id}"
            )
            new_reference = (
                f"keychain:com.modeldial.api-key:{connection.id}"
            )
            connection.api_key_ref = old_reference
            config_store.save(config)
            payload = {
                "schema_version": 1,
                "operation": "connection_secret_references",
                "arguments": {
                    "references_by_connection_id": {
                        connection.id: new_reference,
                    }
                },
            }

            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/native_bridge.py",
                    "migrate-secret-references",
                    "--config-path",
                    str(config_path),
                    "--payload",
                    json.dumps(payload),
                ],
                cwd=Path(__file__).resolve().parent.parent,
                check=True,
                capture_output=True,
                text=True,
            )
            response = json.loads(completed.stdout)
            persisted = config_store.load().model_ingress.connections[0]
            created_files = {
                path.relative_to(root)
                for path in root.rglob("*")
                if path.is_file()
            }

        self.assertEqual(
            response,
            {
                "schema_version": 1,
                "ok": True,
                "action": "migrate_secret_references",
                "operation": "connection_secret_references",
            },
        )
        self.assertEqual(persisted.api_key_ref, new_reference)
        self.assertNotIn(old_reference, completed.stdout)
        self.assertNotIn(new_reference, completed.stdout)
        self.assertEqual(
            created_files,
            {Path("config.json"), Path(".config.json.update.lock.guard")},
        )

    def test_script_secret_reference_migration_rejects_other_operations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_store = ConfigStore(config_path, first_run_defaults=True)
            config_store.save(config_store.load())
            before = config_path.read_bytes()
            payload = {
                "schema_version": 1,
                "operation": "scheduler_enabled",
                "arguments": {"enabled": True},
            }

            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/native_bridge.py",
                    "migrate-secret-references",
                    "--config-path",
                    str(config_path),
                    "--payload",
                    json.dumps(payload),
                ],
                cwd=Path(__file__).resolve().parent.parent,
                capture_output=True,
                text=True,
            )
            after = config_path.read_bytes()

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(
            "only accepts connection_secret_references",
            completed.stderr,
        )
        self.assertEqual(before, after)

    def test_script_refresh_snapshot_entrypoint_outputs_lightweight_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(__file__).resolve().parent.parent
            output = subprocess.check_output(
                [
                    "python3",
                    "scripts/native_bridge.py",
                    "refresh-snapshot",
                    "--config-path",
                    str(Path(temp_dir) / "config.json"),
                    "--history-path",
                    str(Path(temp_dir) / "history.jsonl"),
                    "--active-run-path",
                    str(Path(temp_dir) / "active_run.json"),
                ],
                text=True,
                cwd=root,
            )

        payload = json.loads(output)
        self.assertEqual(
            set(payload),
            {
                "schema_version",
                "config",
                "runtime",
                "question_pack",
                "recommendation_use",
            },
        )
        self.assertEqual(payload["schema_version"], 1)
        self.assertIn("evaluation_profiles", payload["question_pack"])
        self.assertEqual(payload["runtime"]["enabled_target_count"], 0)

    def test_script_save_config_entrypoint_outputs_model_ingress_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cwd = Path(__file__).resolve().parent.parent
            config_path = Path(temp_dir) / "config.json"
            history_path = Path(temp_dir) / "history.jsonl"
            active_run_path = Path(temp_dir) / "active_run.json"
            payload = {
                "model_ingress": {
                    "sources": [
                        {
                            "id": "local",
                            "kind": "codex",
                            "title": "Local Codex",
                            "description": "Use local Codex CLI.",
                            "mode": "local",
                            "enabled": True,
                        }
                    ],
                    "connections": [
                        {
                            "id": "local-default",
                            "source_id": "local",
                            "name": "Local CLI",
                            "enabled": True,
                            "api_format": None,
                            "provider_preset": "generic",
                            "base_url": None,
                            "api_key_ref": None,
                            "notes": None,
                            "last_test_status": None,
                            "last_test_at": None,
                            "last_test_message": None,
                            "model_candidates": [
                                {
                                    "id": "gpt-5.4-medium",
                                    "connection_id": "local-default",
                                    "model_id": "gpt-5.4",
                                    "display_name": "GPT-5.4 Medium",
                                    "scan_profile": "medium",
                                    "enabled": True,
                                    "capabilities": [],
                                }
                            ],
                        }
                    ],
                }
            }

            output = subprocess.check_output(
                [
                    "python3",
                    "scripts/native_bridge.py",
                    "save-config",
                    "--config-path",
                    str(config_path),
                    "--history-path",
                    str(history_path),
                    "--active-run-path",
                    str(active_run_path),
                    "--payload",
                    json.dumps(payload, ensure_ascii=False),
                ],
                text=True,
                cwd=cwd,
            )

            response = json.loads(output)

            self.assertIn("provider_catalog", response["config"])
            self.assertIn("sources", response["config"]["model_ingress"])
            self.assertIn("connections", response["config"]["model_ingress"])
            self.assertIn("provider_catalog", response["state"]["config"])
            self.assertIn("sources", response["state"]["config"]["model_ingress"])
            self.assertIn("connections", response["state"]["config"]["model_ingress"])
            for key in (
                "advisor_v2_evidence",
                "recommendation_portfolio_v2",
                "reference_snapshot_feed",
                "recommendation_use",
            ):
                self.assertIn(key, response["state"])

    def test_build_snapshot_returns_dashboard_and_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            history_store.append(
                ScanResult(
                    model="gpt-5.4",
                    effort="high",
                    question_id="01_candy",
                    question_title="Candy",
                    capability_id="worst_case_reasoning",
                    capability_label="最坏情况",
                    detail_label="部分可见抽样",
                    started_at="2026-06-30T10:00:00+08:00",
                    elapsed_seconds=10.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="21",
                    scorer_reason="regex",
                    expected_summary="21",
                    actual_summary="21",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )
            )

            snapshot = build_snapshot(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
            )

            self.assertIn("dashboard", snapshot)
            self.assertIn("runtime", snapshot)
            self.assertIn("model_ingress", snapshot["config"])
            self.assertIn("provider_catalog", snapshot["config"])
            self.assertIn("sources", snapshot["config"]["model_ingress"])
            self.assertIn("connections", snapshot["config"]["model_ingress"])
            self.assertEqual(snapshot["dashboard"]["best_combination"]["label"], "gpt-5.4 / high")
            self.assertEqual(snapshot["dashboard"]["run_metadata"]["status"], "partial")
            self.assertEqual(
                snapshot["dashboard"]["run_metadata"]["question_pack_version"],
                "unknown",
            )
            self.assertEqual(
                snapshot["dashboard"]["run_metadata"]["scoring_mode"],
                "legacy",
            )
            question_results = snapshot["dashboard"]["leaderboard"][0]["question_results"]
            self.assertEqual(question_results[0]["capability_label"], "最坏情况")
            self.assertEqual(question_results[0]["scorer_reason"], "regex")
            self.assertEqual(question_results[0]["expected_summary"], "21")
            self.assertEqual(question_results[0]["actual_summary"], "21")

    def test_build_snapshot_exposes_active_run_metadata_status_and_pack_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            history_store.append(
                ScanResult(
                    run_id="run-meta",
                    candidate_id="codex-local-default:gpt-5.4:high",
                    model="gpt-5.4",
                    effort="high",
                    phase="scan",
                    question_id="01_session_bundle_repair",
                    question_title="Session Bundle Contract Repair",
                    grader_kind="session_bundle_patch",
                    attempt_index=1,
                    started_at="2026-07-06T10:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="21",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )
            )
            active_run_store.save(
                {
                    "run_id": "run-meta",
                    "run_metadata": {
                        "run_id": "run-meta",
                        "question_pack_id": "coding-fast",
                        "question_pack_version": DEFAULT_QUESTION_PACK_VERSION,
                        "started_at": "2026-07-06T10:00:00+08:00",
                        "completed_at": None,
                        "candidate_count": 6,
                        "question_count": DEFAULT_QUESTION_COUNT,
                        "status": "partial",
                    },
                    "planned_attempts": {"gpt-5.4 / high": DEFAULT_QUESTION_COUNT},
                    "entries": [
                        {
                            "candidate_id": "codex-local-default:gpt-5.4:high",
                            "model": "gpt-5.4",
                            "effort": "high",
                            "label": "gpt-5.4 / high",
                            "status": "interrupted",
                            "final_status": "pass",
                            "reasoning_tokens": 430,
                            "attempts_completed": 1,
                            "attempts_per_target": DEFAULT_QUESTION_COUNT,
                            "phase": "scan",
                            "flags": [],
                            "error_message": None,
                        }
                    ],
                }
            )

            snapshot = build_snapshot(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
            )

            metadata = snapshot["dashboard"]["run_metadata"]
            self.assertEqual(metadata["run_id"], "run-meta")
            self.assertEqual(metadata["question_pack_id"], "coding-fast")
            self.assertEqual(metadata["question_pack_version"], DEFAULT_QUESTION_PACK_VERSION)
            self.assertEqual(metadata["question_count"], DEFAULT_QUESTION_COUNT)
            self.assertEqual(metadata["status"], "partial")

    def test_stream_scan_events_passes_the_started_plan_to_the_executor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()
            selected_candidate = config.model_ingress.connections[0].model_candidates[0]
            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = candidate.id == selected_candidate.id
            config_store.save(config)
            planned: list[object] = []
            executed: list[object] = []
            original_plan_scan = MonitorService.plan_scan
            original_run_enabled_targets = MonitorService.run_enabled_targets

            def capture_plan(service, **kwargs):  # type: ignore[no-untyped-def]
                plan = original_plan_scan(service, **kwargs)
                planned.append(plan)
                return plan

            def capture_execution(service, **kwargs):  # type: ignore[no-untyped-def]
                executed.append(kwargs.get("scan_plan"))
                return original_run_enabled_targets(service, **kwargs)

            def successful_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                return ScanResult(
                    model=target.model,
                    effort=target.effort,
                    started_at=datetime.now(timezone.utc).isoformat(),
                    elapsed_seconds=1.0,
                    source_mode="mock" if use_mock_results else "live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            with (
                patch.object(MonitorService, "plan_scan", capture_plan),
                patch.object(
                    MonitorService,
                    "run_enabled_targets",
                    capture_execution,
                ),
            ):
                events = list(
                    stream_scan_events(
                        config_store=config_store,
                        history_store=history_store,
                        active_run_store=active_run_store,
                        runner=successful_runner,
                    )
                )

            self.assertEqual(events[0]["type"], "scan.started")
            self.assertEqual(events[-1]["type"], "scan.finished")
            self.assertEqual(len(planned), 1)
            self.assertEqual(len(executed), 1)
            self.assertIs(executed[0], planned[0])

    def test_stream_scan_events_emits_started_target_and_finished(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            calls: list[tuple[str, str]] = []

            def fake_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                calls.append((target.model, target.effort))
                return ScanResult(
                    model=target.model,
                    effort=target.effort,
                    phase="scan",
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    started_at="2026-06-30T10:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=target.model == "gpt-5.4" and target.effort == "medium",
                    answer_preview="21",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            events = list(
                stream_scan_events(
                    config_store=config_store,
                    history_store=history_store,
                    active_run_store=active_run_store,
                    runner=fake_runner,
                    codex_insights_provider=lambda _data_dir: {
                        "workload": {"aggregates": []},
                    },
                )
            )

            self.assertEqual(events[0]["type"], "scan.started")
            self.assertEqual(events[-1]["type"], "scan.finished")
            self.assertEqual(events[-2]["type"], "scan.finalizing")
            self.assertEqual(events[-2]["state_kind"], "runtime_delta")
            self.assertEqual(
                events[-2]["state"]["runtime"]["lifecycle_state"],
                "finalizing",
            )
            for key in (
                "last_phase",
                "last_phase_completed",
                "last_phase_total",
                "finalizing_started_at",
                "updated_at",
                "lease_expires_at",
            ):
                self.assertIn(key, events[-2])
            self.assertEqual(len(calls), 6 * DEFAULT_EVALUATION_COUNT)
            self.assertEqual(events[0]["total_targets"], 6 * DEFAULT_EVALUATION_COUNT)
            self.assertEqual(events[-1]["result_count"], 6 * DEFAULT_EVALUATION_COUNT)
            finished_runtime = events[-1]["state"]["runtime"]
            self.assertFalse(finished_runtime["is_running"])
            self.assertEqual(finished_runtime["lifecycle_state"], "idle")
            self.assertIsNone(finished_runtime["finalizing_started_at"])
            self.assertIsNone(finished_runtime["lease_expires_at"])
            self.assertIsNone(active_run_store.load())
            for key in (
                "advisor_v2_evidence",
                "recommendation_portfolio_v2",
                "reference_snapshot_feed",
                "recommendation_use",
                "codex_insights",
            ):
                self.assertIn(key, events[-1]["state"])
            self.assertEqual(
                events[-1]["state"]["codex_insights"]["workload"]["aggregates"],
                [],
            )
            questions = events[-1]["state"]["question_pack"]["questions"]
            self.assertEqual(len(questions), DEFAULT_QUESTION_COUNT)
            self.assertEqual(questions[0]["id"], "01_session_bundle_repair")
            self.assertEqual(questions[0]["score_max"], 20)

    def test_snapshot_and_scan_stream_expose_dynamic_evaluation_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()
            selected_candidate_id = "codex-local-default:gpt-5.4:medium"
            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = candidate.id == selected_candidate_id
            config_store.save(config)
            calls: list[str] = []

            def fake_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                calls.append(question.id)
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    started_at="2026-07-23T10:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                    scorer_diagnostics={
                        "semantic_score": 16,
                        "semantic_total": 20,
                    },
                )

            snapshot = build_snapshot(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
            )
            events = list(
                stream_scan_events(
                    config_store=config_store,
                    history_store=history_store,
                    active_run_store=active_run_store,
                    runner=fake_runner,
                    evaluation_profile_id="quick",
                )
            )

            self.assertEqual(snapshot["question_pack"]["default_evaluation_profile_id"], "quick")
            self.assertEqual(
                [
                    profile["id"]
                    for profile in snapshot["question_pack"]["evaluation_profiles"]
                ],
                ["quick", "full"],
            )
            self.assertEqual(calls, DEFAULT_QUESTION_IDS)
            self.assertEqual(events[0]["type"], "scan.started")
            self.assertEqual(events[0]["evaluation_profile_id"], "quick")
            self.assertEqual(events[0]["evaluation_profile_label"], "快速对比")
            self.assertEqual(events[0]["question_count"], DEFAULT_QUESTION_COUNT)
            self.assertEqual(events[0]["total_targets"], DEFAULT_QUESTION_COUNT)
            metadata = history_store.load_run_metadata_map()
            self.assertEqual(next(iter(metadata.values()))["evaluation_profile_id"], "quick")

    def test_custom_append_stream_adds_only_the_new_complete_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()
            candidates = config.model_ingress.connections[0].model_candidates[:2]
            original_candidate = candidates[0]
            added_candidate = candidates[1]
            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = candidate.id == original_candidate.id
            config_store.save(config)
            calls: list[tuple[str, str, str]] = []

            def fake_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                calls.append((kwargs["run_id"], target.candidate_id, question.id))
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    started_at="2026-07-23T10:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            quick_events = list(
                stream_scan_events(
                    config_store=config_store,
                    history_store=history_store,
                    active_run_store=active_run_store,
                    runner=fake_runner,
                    evaluation_profile_id="quick",
                )
            )
            quick_run_id = quick_events[-1]["state"]["dashboard"]["current_run_id"]
            config = config_store.load()
            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = candidate.id in {
                        original_candidate.id,
                        added_candidate.id,
                    }
            config_store.save(config)

            appended_events = list(
                stream_scan_events(
                    config_store=config_store,
                    history_store=history_store,
                    active_run_store=active_run_store,
                    runner=fake_runner,
                    requested_candidate_ids=[added_candidate.id],
                    selection_mode="custom",
                    custom_round_mode="append",
                    evaluation_profile_id="quick",
                )
            )
            appended_run_id = next(
                run_id
                for run_id, _, _ in reversed(calls)
                if run_id != quick_run_id
            )
            appended_calls = [
                (candidate_id, question_id)
                for run_id, candidate_id, question_id in calls
                if run_id == appended_run_id
            ]

            self.assertEqual(
                appended_events[0]["requested_candidate_ids"],
                [added_candidate.id],
            )
            self.assertEqual(len(appended_calls), DEFAULT_QUESTION_COUNT)
            self.assertEqual(
                {question_id for candidate_id, question_id in appended_calls if candidate_id == added_candidate.id},
                set(DEFAULT_QUESTION_IDS),
            )
            self.assertFalse(
                any(candidate_id == original_candidate.id for candidate_id, _ in appended_calls)
            )

    def test_custom_append_stream_reports_full_group_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()
            original_candidate, added_candidate = (
                config.model_ingress.connections[0].model_candidates[:2]
            )
            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = candidate.id == original_candidate.id
            config_store.save(config)
            should_pause = False
            pause_requested = False

            def fake_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                nonlocal pause_requested
                if should_pause and not pause_requested:
                    pause_requested = True
                    active_run_store.request_control("pause")
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-28T10:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            list(
                stream_scan_events(
                    config_store=config_store,
                    history_store=history_store,
                    active_run_store=active_run_store,
                    runner=fake_runner,
                )
            )
            config = config_store.load()
            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = candidate.id in {
                        original_candidate.id,
                        added_candidate.id,
                    }
            config_store.save(config)
            should_pause = True

            events = list(
                stream_scan_events(
                    config_store=config_store,
                    history_store=history_store,
                    active_run_store=active_run_store,
                    runner=fake_runner,
                    requested_candidate_ids=[added_candidate.id],
                    selection_mode="custom",
                    custom_round_mode="append",
                )
            )
            first_progress = next(
                event for event in events if event["type"] == "scan.progress"
            )

            self.assertEqual(events[0]["total_targets"], 10)
            self.assertEqual(first_progress["completed_targets"], 6)
            self.assertEqual(first_progress["total_targets"], 10)
            self.assertEqual(first_progress["state"]["runtime"]["completed_targets"], 6)

    def test_stream_scan_events_preserves_finalizing_run_when_final_projection_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()
            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = candidate.scan_profile == "medium"
            config_store.save(config)

            def fake_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-13T10:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="mock",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            fail_summary = False
            original_save_summary = service_module.RunJournalStore.save_summary

            def conditional_save_summary(store, run_id, payload):  # type: ignore[no-untyped-def]
                if fail_summary:
                    raise OSError("journal summary unavailable")
                return original_save_summary(store, run_id, payload)

            def fail_projection(*args, **kwargs):  # type: ignore[no-untyped-def]
                nonlocal fail_summary
                fail_summary = True
                raise RuntimeError("recommendation build failed")

            with (
                patch.object(
                    service_module.RunJournalStore,
                    "save_summary",
                    autospec=True,
                    side_effect=conditional_save_summary,
                ),
                patch.object(
                    native_bridge_module,
                    "_build_command_snapshot",
                    side_effect=fail_projection,
                ),
            ):
                events = list(
                    stream_scan_events(
                        config_store=config_store,
                        history_store=history_store,
                        active_run_store=active_run_store,
                        runner=fake_runner,
                    )
                )

            self.assertEqual(events[-2]["type"], "scan.finalizing")
            self.assertEqual(events[-1]["type"], "scan.failed")
            self.assertEqual(
                events[-1]["failure_category"],
                "recommendation_build_failed",
            )
            self.assertEqual(
                events[-1]["state"]["runtime"]["lifecycle_state"],
                "finalizing",
            )
            self.assertEqual(
                events[-1]["state"]["runtime"]["last_error"],
                "recommendation build failed",
            )
            self.assertEqual(
                events[-1]["persistence_errors"],
                ["journal_summary: journal summary unavailable"],
            )
            active_run = active_run_store.load()
            self.assertIsNotNone(active_run)
            self.assertEqual(active_run["run_metadata"]["status"], "completed")
            self.assertEqual(
                active_run["runtime"]["lifecycle_state"],
                "finalizing",
            )
            self.assertEqual(
                active_run["runtime"]["last_error"],
                "recommendation build failed",
            )
            journal_events = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
            ).run_journal_store.load_events(str(active_run["run_id"]))
            self.assertEqual(journal_events[-1]["type"], "run.projection_failed")
            self.assertNotIn(
                "run.failed",
                [event["type"] for event in journal_events[-2:]],
            )

    def test_stream_scan_events_reports_circuit_breaker_as_failed_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()
            enabled_one = False
            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = not enabled_one
                    enabled_one = True
            config.system.max_concurrent_targets = 1
            for rule in config.rules.values():
                rule.max_retries = 0
            config_store.save(config)

            def hard_error_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-28T20:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=False,
                    answer_preview="ERROR: transport unavailable",
                    input_tokens=None,
                    output_tokens=None,
                    reasoning_tokens=None,
                    error_message="transport unavailable",
                    final_status="warn",
                )

            with patch.object(
                native_bridge_module,
                "_build_command_snapshot",
                side_effect=AssertionError("failed scan must not build final snapshot"),
            ):
                events = list(
                    stream_scan_events(
                        config_store=config_store,
                        history_store=history_store,
                        active_run_store=active_run_store,
                        runner=hard_error_runner,
                    )
                )

            terminal = events[-1]
            self.assertEqual(terminal["type"], "scan.failed")
            self.assertEqual(
                terminal["failure_category"],
                "scan_execution_failed",
            )
            self.assertIn("扫描已熔断", terminal["failure_message"])
            self.assert_failure_snapshot_state(
                terminal,
                lifecycle_state="failed",
            )
            self.assertNotIn("scan.finished", [event["type"] for event in events])
            self.assertNotIn("scan.finalizing", [event["type"] for event in events])
            active_run = active_run_store.load()
            self.assertIsNotNone(active_run)
            self.assertEqual(active_run["run_metadata"]["status"], "failed")
            self.assertEqual(active_run["runtime"]["lifecycle_state"], "failed")

    def test_stream_scan_events_persists_and_emits_generic_execution_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()
            enabled_one = False
            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = not enabled_one
                    enabled_one = True
            config_store.save(config)

            def failing_runner(*args, **kwargs):  # type: ignore[no-untyped-def]
                raise RuntimeError("synthetic hard failure")

            events = list(
                stream_scan_events(
                    config_store=config_store,
                    history_store=history_store,
                    active_run_store=active_run_store,
                    runner=failing_runner,
                )
            )

            self.assertEqual(events[0]["type"], "scan.started")
            self.assertEqual(events[-1]["type"], "scan.failed")
            self.assertEqual(
                events[-1]["failure_category"],
                "scan_execution_failed",
            )
            self.assertEqual(
                events[-1]["failure_message"],
                "synthetic hard failure",
            )
            self.assert_failure_snapshot_state(
                events[-1],
                lifecycle_state="failed",
            )

            active_run = active_run_store.load()
            self.assertIsNotNone(active_run)
            self.assertEqual(active_run["run_metadata"]["status"], "failed")
            self.assertEqual(active_run["runtime"]["lifecycle_state"], "failed")
            self.assertEqual(
                active_run["runtime"]["last_error"],
                "synthetic hard failure",
            )

            snapshot = build_snapshot(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
            )
            self.assertEqual(snapshot["runtime"]["lifecycle_state"], "failed")
            self.assertEqual(
                snapshot["runtime"]["last_error"],
                "synthetic hard failure",
            )
            self.assertEqual(
                snapshot["dashboard"]["run_metadata"]["status"],
                "failed",
            )

    def test_stream_scan_events_preserves_finalizing_run_when_commit_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()
            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = candidate.scan_profile == "medium"
            config_store.save(config)

            def fake_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-13T10:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="mock",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            with patch.object(
                active_run_store,
                "clear",
                side_effect=OSError("finalization commit failed"),
            ):
                events = list(
                    stream_scan_events(
                        config_store=config_store,
                        history_store=history_store,
                        active_run_store=active_run_store,
                        runner=fake_runner,
                    )
                )

            self.assertEqual(events[-2]["type"], "scan.finalizing")
            self.assertEqual(events[-1]["type"], "scan.failed")
            self.assertEqual(
                events[-1]["failure_category"],
                "finalization_commit_failed",
            )
            self.assertEqual(
                events[-1]["state"]["runtime"]["lifecycle_state"],
                "finalizing",
            )
            active_run = active_run_store.load()
            self.assertIsNotNone(active_run)
            self.assertEqual(active_run["run_metadata"]["status"], "completed")
            self.assertEqual(
                active_run["runtime"]["lifecycle_state"],
                "finalizing",
            )
            self.assertEqual(
                active_run["runtime"]["last_error"],
                "finalization commit failed",
            )
            journal_service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
            )
            journal_events = journal_service.run_journal_store.load_events(
                str(active_run["run_id"])
            )
            self.assertEqual(
                journal_events[-1]["type"],
                "run.finalization_commit_failed",
            )
            summary = journal_service.run_journal_store.load_summary(
                str(active_run["run_id"])
            )
            self.assertEqual(summary["status"], "completed")  # type: ignore[index]
            self.assertEqual(summary["lifecycle_state"], "finalizing")  # type: ignore[index]

    def test_stream_scan_events_runs_only_requested_candidate_and_records_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            candidate_id = "codex-local-default:gpt-5.4:medium"
            _disable_candidate(
                config_store,
                "codex-local-default",
                "gpt-5.4",
                "medium",
            )
            calls: list[str] = []

            def fake_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                calls.append(target.candidate_id)
                return ScanResult(
                    model=target.model,
                    effort=target.effort,
                    phase="scan",
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    started_at="2026-07-10T10:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="21",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            events = list(
                stream_scan_events(
                    config_store=config_store,
                    history_store=history_store,
                    active_run_store=active_run_store,
                    runner=fake_runner,
                    requested_candidate_ids=[candidate_id],
                    selection_mode="single",
                )
            )

            self.assertEqual(events[0]["type"], "scan.started")
            self.assertEqual(events[0]["selection_mode"], "single")
            self.assertEqual(events[0]["requested_candidate_ids"], [candidate_id])
            self.assertEqual(calls, [candidate_id] * DEFAULT_EVALUATION_COUNT)
            run_id = events[-1]["state"]["dashboard"]["run_metadata"]["run_id"]
            metadata = history_store.load_run_metadata(run_id)
            self.assertIsNotNone(metadata)
            self.assertEqual(metadata["selection_mode"], "single")  # type: ignore[index]
            self.assertEqual(metadata["requested_candidate_ids"], [candidate_id])  # type: ignore[index]

    def test_stream_scan_events_single_scan_defaults_to_append_when_regular_round_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()

            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = False
            candidate_a = config.model_ingress.connections[0].model_candidates[0]
            candidate_b = config.model_ingress.connections[0].model_candidates[1]
            candidate_a.enabled = True
            config_store.save(config)

            def fake_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-16T10:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            list(
                stream_scan_events(
                    config_store=config_store,
                    history_store=history_store,
                    active_run_store=active_run_store,
                    runner=fake_runner,
                    requested_candidate_ids=[candidate_a.id],
                    selection_mode="regular",
                )
            )
            events = list(
                stream_scan_events(
                    config_store=config_store,
                    history_store=history_store,
                    active_run_store=active_run_store,
                    runner=fake_runner,
                    requested_candidate_ids=[candidate_b.id],
                    selection_mode="single",
                )
            )

            self.assertEqual(events[0]["type"], "scan.started")
            self.assertEqual(events[0]["selection_mode"], "single")
            metadata_by_run = history_store.load_run_metadata_map()
            append_metadata = next(
                (
                    item
                    for item in metadata_by_run.values()
                    if item.get("comparison_group_mode") == "custom_append"
                ),
                None,
            )
            self.assertIsNotNone(append_metadata)
            self.assertEqual(append_metadata["appended_candidate_ids"], [candidate_b.id])  # type: ignore[index]
            dashboard_metadata = events[-1]["state"]["dashboard"]["run_metadata"]
            self.assertEqual(
                set(dashboard_metadata["requested_candidate_ids"]),
                {candidate_a.id, candidate_b.id},
            )

    def test_stream_scan_events_allows_single_scan_for_disabled_source_connection_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()
            connection = config.model_ingress.connections[0]
            source = next(
                item for item in config.model_ingress.sources if item.id == connection.source_id
            )
            source.enabled = False
            connection.enabled = False
            candidate = connection.model_candidates[0]
            candidate.enabled = False
            candidate_id = candidate.id
            config_store.save(config)
            calls: list[str] = []

            def fake_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                calls.append(target.candidate_id)
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-16T12:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="21",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            events = list(
                stream_scan_events(
                    config_store=config_store,
                    history_store=history_store,
                    active_run_store=active_run_store,
                    runner=fake_runner,
                    requested_candidate_ids=[candidate_id],
                    selection_mode="single",
                )
            )

            self.assertEqual(events[0]["type"], "scan.started")
            self.assertEqual(calls, [candidate_id] * DEFAULT_EVALUATION_COUNT)
            metadata = history_store.load_run_metadata(
                events[-1]["state"]["dashboard"]["run_metadata"]["run_id"]
            )
            self.assertEqual(metadata["requested_candidate_ids"], [candidate_id])  # type: ignore[index]

    def test_stream_scan_events_passes_custom_round_mode_for_append(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()

            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = False
            candidate_a = config.model_ingress.connections[0].model_candidates[0]
            candidate_b = config.model_ingress.connections[0].model_candidates[1]
            candidate_a.enabled = True
            config_store.save(config)

            def fake_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-16T10:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            list(
                stream_scan_events(
                    config_store=config_store,
                    history_store=history_store,
                    active_run_store=active_run_store,
                    runner=fake_runner,
                    requested_candidate_ids=[candidate_a.id],
                    selection_mode="regular",
                )
            )
            events = list(
                stream_scan_events(
                    config_store=config_store,
                    history_store=history_store,
                    active_run_store=active_run_store,
                    runner=fake_runner,
                    requested_candidate_ids=[candidate_a.id, candidate_b.id],
                    selection_mode="custom",
                    custom_round_mode="append",
                )
            )

            self.assertEqual(events[0]["type"], "scan.started")
            self.assertEqual(events[0]["selection_mode"], "custom")
            self.assertEqual(events[0]["custom_round_mode"], "append")
            metadata_by_run = history_store.load_run_metadata_map()
            append_metadata = next(
                (
                    item
                    for item in metadata_by_run.values()
                    if item.get("comparison_group_mode") == "custom_append"
                ),
                None,
            )
            self.assertIsNotNone(append_metadata)
            self.assertEqual(append_metadata["appended_candidate_ids"], [candidate_b.id])  # type: ignore[index]

    def test_stream_scan_events_does_not_start_second_process_when_scan_lock_is_alive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            candidate_id = "codex-local-default:gpt-5.4:high"
            self.save_active_run_for_event_state(
                MonitorService(
                    config_store=config_store,
                    history_store=history_store,
                    active_run_store=active_run_store,
                ),
                run_id="run-active-scan",
                candidate_id=candidate_id,
                lifecycle_state="active_scan",
                phase="scan",
            )
            lock_path = active_run_store.path.with_name("scan.lock")
            lock_path.write_text(str(os.getpid()), encoding="utf-8")
            runner_called = False

            def runner(*args, **kwargs):  # type: ignore[no-untyped-def]
                nonlocal runner_called
                runner_called = True
                raise AssertionError("runner should not start while scan lock is alive")

            events = list(
                stream_scan_events(
                    config_store=config_store,
                    history_store=history_store,
                    active_run_store=active_run_store,
                    runner=runner,
                )
            )

            self.assertFalse(runner_called)
            self.assertEqual(events[0]["type"], "scan.already_running")
            self.assert_authoritative_event_state(
                events[0],
                lifecycle_state="active_scan",
                is_running=True,
                has_resumable_run=True,
            )

    def test_repair_conflicts_include_authoritative_running_snapshot(self) -> None:
        candidate_id = "codex-local-default:gpt-5.4:high"
        for event_type in (
            "repair.already_running",
            "timeout-repair.already_running",
        ):
            with self.subTest(event_type=event_type), tempfile.TemporaryDirectory() as temp_dir:
                config_store = ConfigStore(Path(temp_dir) / "config.json")
                history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
                active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
                self.save_active_run_for_event_state(
                    MonitorService(
                        config_store=config_store,
                        history_store=history_store,
                        active_run_store=active_run_store,
                    ),
                    run_id="run-active-scan",
                    candidate_id=candidate_id,
                    lifecycle_state="active_scan",
                    phase="scan",
                )
                active_run_store.path.with_name("scan.lock").write_text(
                    json.dumps(
                        {
                            "pid": os.getpid(),
                            "heartbeat_at": time.time(),
                        }
                    ),
                    encoding="utf-8",
                )

                if event_type == "repair.already_running":
                    events = list(
                        stream_repair_events(
                            run_id="run-requested-repair",
                            candidate_id=candidate_id,
                            config_store=config_store,
                            history_store=history_store,
                            active_run_store=active_run_store,
                        )
                    )
                else:
                    events = list(
                        stream_timed_out_repair_events(
                            run_id="run-requested-repair",
                            candidate_ids=[candidate_id],
                            config_store=config_store,
                            history_store=history_store,
                            active_run_store=active_run_store,
                        )
                    )

                self.assertEqual(events[0]["type"], event_type)
                self.assert_authoritative_event_state(
                    events[0],
                    lifecycle_state="active_scan",
                    is_running=True,
                    has_resumable_run=True,
                )

    def test_stream_scan_events_reclaims_stale_alive_scan_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            lock_path = active_run_store.path.with_name("scan.lock")
            lock_path.write_text(str(os.getpid()), encoding="utf-8")
            stale_time = time.time() - 600
            os.utime(lock_path, (stale_time, stale_time))
            calls: list[tuple[str, str, str]] = []

            def fake_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                calls.append((target.model, target.effort, question.id))
                return ScanResult(
                    run_id="run-stale-lock",
                    candidate_id=f"codex-local-default:{target.model}:{target.effort}",
                    model=target.model,
                    effort=target.effort,
                    phase="scan",
                    question_id=question.id,
                    question_title=question.title,
                    capability_id=question.capability_id,
                    capability_label=question.capability_label,
                    detail_label=question.detail_label,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-08T14:10:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="21",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            events = list(
                stream_scan_events(
                    config_store=config_store,
                    history_store=history_store,
                    active_run_store=active_run_store,
                    runner=fake_runner,
                )
            )

            self.assertGreater(len(calls), 0)
            self.assertEqual(events[0]["type"], "scan.started")

    def test_scan_process_lock_serializes_concurrent_stale_reclaimers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lock_path = root / "scan.lock"
            lock_path.write_text(
                json.dumps(
                    {
                        "pid": 999_999_999,
                        "heartbeat_at": time.time() - 600,
                    }
                ),
                encoding="utf-8",
            )
            go_path = root / "go"
            release_path = root / "release"
            processes = [
                self.start_scan_lock_contender(
                    root,
                    ready_path=root / f"ready-{index}",
                    go_path=go_path,
                    release_path=release_path,
                )
                for index in range(2)
            ]

            try:
                self.wait_for_paths(root / "ready-0", root / "ready-1")
                go_path.write_text("go", encoding="utf-8")
                results = [
                    self.read_lock_contender_result(process)
                    for process in processes
                ]

                self.assertEqual(
                    sorted(result["acquired"] for result in results),
                    [False, True],
                )
            finally:
                release_path.write_text("release", encoding="utf-8")
                for process in processes:
                    try:
                        process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()

            for process in processes:
                self.assertEqual(process.returncode, 0)

    def test_scan_process_lock_heartbeat_is_atomic_and_blocks_competitor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lock_path = root / "scan.lock"
            go_path = root / "go"
            release_path = root / "release"
            holder = self.start_scan_lock_contender(
                root,
                ready_path=root / "holder-ready",
                go_path=go_path,
                release_path=release_path,
                heartbeat_interval=0.005,
            )
            processes = [holder]

            try:
                self.wait_for_paths(root / "holder-ready")
                go_path.write_text("go", encoding="utf-8")
                holder_result = self.read_lock_contender_result(holder)
                self.assertTrue(holder_result["acquired"])
                self.wait_for_paths(lock_path)

                observed_heartbeats = 0
                deadline = time.monotonic() + 0.2
                while time.monotonic() < deadline:
                    payload = json.loads(lock_path.read_text(encoding="utf-8"))
                    self.assertEqual(payload["pid"], holder.pid)
                    self.assertIsInstance(payload["heartbeat_at"], float)
                    observed_heartbeats += 1
                self.assertGreater(observed_heartbeats, 1)

                competitor = self.start_scan_lock_contender(
                    root,
                    ready_path=root / "competitor-ready",
                    go_path=go_path,
                    release_path=release_path,
                )
                processes.append(competitor)
                self.wait_for_paths(root / "competitor-ready")
                competitor_result = self.read_lock_contender_result(competitor)
                self.assertFalse(competitor_result["acquired"])
            finally:
                release_path.write_text("release", encoding="utf-8")
                for process in processes:
                    try:
                        process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()

            for process in processes:
                self.assertEqual(process.returncode, 0)
            self.assertFalse(lock_path.exists())

    def test_scan_process_lock_waits_for_blocked_heartbeat_before_release(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            active_run_store = ActiveRunStore(root / "active_run.json")
            history_store = HistoryStore(root / "history.jsonl")
            lock_path = root / "scan.lock"
            holder_inside = threading.Event()
            allow_holder_exit = threading.Event()
            heartbeat_blocked = threading.Event()
            release_heartbeat = threading.Event()
            holder_exited = threading.Event()
            acquired_values: list[bool] = []
            holder_errors: list[BaseException] = []
            write_count = 0
            write_count_lock = threading.Lock()
            original_write = native_bridge_module._write_lock_payload

            def blocking_write(path: Path, pid: int) -> None:
                nonlocal write_count
                with write_count_lock:
                    write_count += 1
                    current_write = write_count
                if current_write > 1:
                    heartbeat_blocked.set()
                    if not release_heartbeat.wait(timeout=5):
                        raise TimeoutError("blocked heartbeat was not released")
                original_write(path, pid)

            def hold_lock() -> None:
                try:
                    with native_bridge_module._scan_process_lock(
                        active_run_store,
                        history_store,
                    ) as acquired:
                        acquired_values.append(acquired)
                        holder_inside.set()
                        if not allow_holder_exit.wait(timeout=5):
                            raise TimeoutError("lock holder was not released")
                except BaseException as error:
                    holder_errors.append(error)
                finally:
                    holder_exited.set()

            with patch.object(
                native_bridge_module,
                "LOCK_HEARTBEAT_INTERVAL_SECONDS",
                0.005,
            ), patch.object(
                native_bridge_module,
                "_write_lock_payload",
                side_effect=blocking_write,
            ):
                holder_thread = threading.Thread(target=hold_lock)
                holder_thread.start()
                try:
                    self.assertTrue(holder_inside.wait(timeout=5))
                    self.assertTrue(heartbeat_blocked.wait(timeout=5))
                    allow_holder_exit.set()
                    holder_thread.join(timeout=0.2)

                    self.assertTrue(holder_thread.is_alive())
                    self.assertFalse(holder_exited.is_set())
                    self.assertTrue(lock_path.exists())
                finally:
                    release_heartbeat.set()
                    allow_holder_exit.set()
                    holder_thread.join(timeout=5)

            self.assertFalse(holder_thread.is_alive())
            self.assertEqual(holder_errors, [])
            self.assertEqual(acquired_values, [True])
            self.assertFalse(lock_path.exists())

    def test_scan_process_lock_calls_injected_lease_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            heartbeat_called = threading.Event()

            with patch.object(
                native_bridge_module,
                "LOCK_HEARTBEAT_INTERVAL_SECONDS",
                0.005,
            ):
                with native_bridge_module._scan_process_lock(
                    ActiveRunStore(root / "active_run.json"),
                    HistoryStore(root / "history.jsonl"),
                    lease_heartbeat=heartbeat_called.set,
                ) as acquired:
                    self.assertTrue(acquired)
                    self.assertTrue(heartbeat_called.wait(timeout=1))

    def test_bridge_does_not_write_service_runtime_or_active_run_state(self) -> None:
        source = Path(native_bridge_module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)

        def attribute_path(node: ast.AST) -> str | None:
            parts: list[str] = []
            while isinstance(node, ast.Attribute):
                parts.append(node.attr)
                node = node.value
            if not isinstance(node, ast.Name):
                return None
            parts.append(node.id)
            return ".".join(reversed(parts))

        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        self.assertFalse(
            any(
                isinstance(call.func, ast.Attribute)
                and call.func.attr == "refresh_runtime_lease"
                for call in calls
            )
        )
        self.assertFalse(
            any(
                attribute_path(node) == "service.runtime_state"
                for node in ast.walk(tree)
                if isinstance(node, ast.Attribute)
            )
        )
        self.assertFalse(
            any(
                attribute_path(call.func) == "service.active_run_store.clear"
                for call in calls
            )
        )

    def test_build_snapshot_marks_resumable_run_running_when_live_scan_lock_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            lock_path = active_run_store.path.with_name("scan.lock")
            now = datetime.now(timezone(timedelta(hours=8)))

            history_store.append(
                ScanResult(
                    run_id="run-live-lock",
                    candidate_id="codex-local-default:gpt-5.4:xhigh",
                    model="gpt-5.4",
                    effort="xhigh",
                    phase="scan",
                    question_id="01_candy",
                    question_title="Candy",
                    grader_kind="regex",
                    attempt_index=1,
                    started_at=(now - timedelta(seconds=30)).isoformat(),
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="21",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )
            )
            active_run_store.save(
                {
                    "run_id": "run-live-lock",
                    "run_metadata": {
                        "run_id": "run-live-lock",
                        "question_pack_id": "coding-fast",
                        "question_pack_version": DEFAULT_QUESTION_PACK_VERSION,
                        "started_at": (now - timedelta(seconds=45)).isoformat(),
                        "completed_at": None,
                        "candidate_count": 6,
                        "question_count": DEFAULT_QUESTION_COUNT,
                        "status": "running",
                    },
                    "planned_attempts_by_candidate": {
                        "codex-local-default:gpt-5.4:medium": DEFAULT_QUESTION_COUNT,
                        "codex-local-default:gpt-5.4:high": DEFAULT_QUESTION_COUNT,
                        "codex-local-default:gpt-5.4:xhigh": DEFAULT_QUESTION_COUNT,
                        "codex-local-default:gpt-5.5:medium": DEFAULT_QUESTION_COUNT,
                        "codex-local-default:gpt-5.5:high": DEFAULT_QUESTION_COUNT,
                        "codex-local-default:gpt-5.5:xhigh": DEFAULT_QUESTION_COUNT,
                    },
                    "entries": [
                        {
                            "candidate_id": "codex-local-default:gpt-5.4:medium",
                            "model": "gpt-5.4",
                            "effort": "medium",
                            "label": "gpt-5.4 / medium",
                            "status": "done",
                            "final_status": "pass",
                            "reasoning_tokens": 400,
                            "attempts_completed": DEFAULT_QUESTION_COUNT,
                            "attempts_per_target": DEFAULT_QUESTION_COUNT,
                            "phase": "scan",
                            "flags": [],
                            "error_message": None,
                        },
                        {
                            "candidate_id": "codex-local-default:gpt-5.4:xhigh",
                            "model": "gpt-5.4",
                            "effort": "xhigh",
                            "label": "gpt-5.4 / xhigh",
                            "status": "running",
                            "final_status": "pass",
                            "reasoning_tokens": 430,
                            "attempts_completed": 1,
                            "attempts_per_target": DEFAULT_QUESTION_COUNT,
                            "phase": "scan",
                            "flags": [],
                            "error_message": None,
                        },
                    ],
                }
            )
            lock_path.write_text(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "heartbeat_at": time.time(),
                    }
                ),
                encoding="utf-8",
            )

            snapshot = build_snapshot(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
            )

            self.assertTrue(snapshot["runtime"]["is_running"])
            self.assertTrue(snapshot["runtime"]["has_resumable_run"])
            xhigh_entry = next(
                entry
                for entry in snapshot["runtime"]["run_entries"]
                if entry["candidate_id"] == "codex-local-default:gpt-5.4:xhigh"
            )
            self.assertEqual(xhigh_entry["status"], "running")

    def test_build_snapshot_does_not_invent_running_zero_progress_from_orphan_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            lock_path = active_run_store.path.with_name("scan.lock")
            lock_path.write_text(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "heartbeat_at": time.time(),
                    }
                ),
                encoding="utf-8",
            )

            snapshot = build_snapshot(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
            )

            self.assertFalse(snapshot["runtime"]["is_running"])
            self.assertEqual(snapshot["runtime"]["progress_completed"], 0)
            self.assertEqual(snapshot["runtime"]["progress_total"], 0)

    def test_stream_scan_events_keeps_fresh_live_lock_when_run_progress_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            lock_path = active_run_store.path.with_name("scan.lock")
            now = datetime.now(timezone(timedelta(hours=8)))
            stale_started_at = (now - timedelta(minutes=10)).isoformat()
            stale_metadata_started_at = (now - timedelta(minutes=11)).isoformat()

            history_store.append(
                ScanResult(
                    run_id="run-stale-progress",
                    candidate_id="codex-local-default:gpt-5.4:xhigh",
                    model="gpt-5.4",
                    effort="xhigh",
                    phase="scan",
                    question_id="01_candy",
                    question_title="Candy",
                    grader_kind="regex",
                    attempt_index=1,
                    started_at=stale_started_at,
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="21",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )
            )
            active_run_store.save(
                {
                    "run_id": "run-stale-progress",
                    "run_metadata": {
                        "run_id": "run-stale-progress",
                        "question_pack_id": "coding-fast",
                        "question_pack_version": DEFAULT_QUESTION_PACK_VERSION,
                        "started_at": stale_metadata_started_at,
                        "completed_at": None,
                        "candidate_count": 6,
                        "question_count": DEFAULT_QUESTION_COUNT,
                        "status": "running",
                    },
                    "planned_attempts_by_candidate": {
                        "codex-local-default:gpt-5.4:medium": DEFAULT_QUESTION_COUNT,
                        "codex-local-default:gpt-5.4:high": DEFAULT_QUESTION_COUNT,
                        "codex-local-default:gpt-5.4:xhigh": DEFAULT_QUESTION_COUNT,
                        "codex-local-default:gpt-5.5:medium": DEFAULT_QUESTION_COUNT,
                        "codex-local-default:gpt-5.5:high": DEFAULT_QUESTION_COUNT,
                        "codex-local-default:gpt-5.5:xhigh": DEFAULT_QUESTION_COUNT,
                    },
                    "entries": [
                        {
                            "candidate_id": "codex-local-default:gpt-5.4:xhigh",
                            "model": "gpt-5.4",
                            "effort": "xhigh",
                            "label": "gpt-5.4 / xhigh",
                            "status": "running",
                            "final_status": "pass",
                            "reasoning_tokens": 430,
                            "attempts_completed": 1,
                            "attempts_per_target": DEFAULT_QUESTION_COUNT,
                            "phase": "scan",
                            "flags": [],
                            "error_message": None,
                        }
                    ],
                }
            )
            lock_path.write_text(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "heartbeat_at": time.time(),
                    }
                ),
                encoding="utf-8",
            )
            calls: list[str] = []

            def fake_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                calls.append(question.id)
                return ScanResult(
                    run_id="run-stale-progress-recovered",
                    candidate_id=f"codex-local-default:{target.model}:{target.effort}",
                    model=target.model,
                    effort=target.effort,
                    phase="scan",
                    question_id=question.id,
                    question_title=question.title,
                    capability_id=question.capability_id,
                    capability_label=question.capability_label,
                    detail_label=question.detail_label,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at=now.isoformat(),
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="21",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            events = list(
                stream_scan_events(
                    config_store=config_store,
                    history_store=history_store,
                    active_run_store=active_run_store,
                    runner=fake_runner,
                )
            )

            self.assertEqual(calls, [])
            self.assertEqual(events[0]["type"], "scan.already_running")

    def test_stream_scan_events_reports_existing_progress_before_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            _disable_candidate(config_store, "codex-local-default", "gpt-5.4", "medium")
            _disable_candidate(config_store, "codex-local-default", "gpt-5.5", "medium")

            history_store.append(
                ScanResult(
                    run_id="run-partial",
                    model="gpt-5.4",
                    effort="high",
                    phase="scan",
                    question_id="01_session_bundle_repair",
                    question_title="Session Bundle Contract Repair",
                    grader_kind="session_bundle_patch",
                    attempt_index=1,
                    started_at="2026-07-02T14:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="21",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )
            )
            calls: list[str] = []

            def fake_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                calls.append(question.id)
                return ScanResult(
                    run_id="run-partial",
                    model=target.model,
                    effort=target.effort,
                    phase="scan",
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-02T14:01:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="21",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            events = list(
                stream_scan_events(
                    config_store=config_store,
                    history_store=history_store,
                    active_run_store=active_run_store,
                    runner=fake_runner,
                )
            )

            self.assertEqual(events[0]["type"], "scan.started")
            self.assertEqual(events[0]["total_targets"], 4 * DEFAULT_EVALUATION_COUNT)
            self.assertEqual(
                events[-1]["result_count"],
                4 * DEFAULT_QUESTION_COUNT,
            )
            self.assertNotIn(
                ("gpt-5.4", "high", "01_session_bundle_repair"),
                calls,
            )

    def test_stream_scan_events_emits_progress_state_during_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            _disable_candidate(config_store, "codex-local-default", "gpt-5.4", "medium")
            _disable_candidate(config_store, "codex-local-default", "gpt-5.5", "medium")

            history_store.append(
                ScanResult(
                    run_id="run-partial",
                    model="gpt-5.4",
                    effort="high",
                    phase="scan",
                    question_id="01_session_bundle_repair",
                    question_title="Session Bundle Contract Repair",
                    grader_kind="session_bundle_patch",
                    attempt_index=1,
                    started_at="2026-07-03T20:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="21",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )
            )
            active_run_store.save(
                {
                    "run_id": "run-partial",
                    "planned_attempts": {
                        "gpt-5.4 / high": 4,
                        "gpt-5.4 / xhigh": 4,
                        "gpt-5.5 / high": 4,
                        "gpt-5.5 / xhigh": 4,
                    },
                    "entries": [
                        {
                            "model": "gpt-5.4",
                            "effort": "high",
                            "label": "gpt-5.4 / high",
                            "status": "interrupted",
                            "final_status": "pass",
                            "reasoning_tokens": 430,
                            "attempts_completed": 1,
                            "attempts_per_target": 4,
                            "phase": "scan",
                            "flags": [],
                            "error_message": None,
                        },
                        {
                            "model": "gpt-5.4",
                            "effort": "xhigh",
                            "label": "gpt-5.4 / xhigh",
                            "status": "pending",
                            "final_status": None,
                            "reasoning_tokens": None,
                            "attempts_completed": 0,
                            "attempts_per_target": 4,
                            "phase": "scan",
                            "flags": [],
                            "error_message": None,
                        },
                        {
                            "model": "gpt-5.5",
                            "effort": "high",
                            "label": "gpt-5.5 / high",
                            "status": "pending",
                            "final_status": None,
                            "reasoning_tokens": None,
                            "attempts_completed": 0,
                            "attempts_per_target": 4,
                            "phase": "scan",
                            "flags": [],
                            "error_message": None,
                        },
                        {
                            "model": "gpt-5.5",
                            "effort": "xhigh",
                            "label": "gpt-5.5 / xhigh",
                            "status": "pending",
                            "final_status": None,
                            "reasoning_tokens": None,
                            "attempts_completed": 0,
                            "attempts_per_target": 4,
                            "phase": "scan",
                            "flags": [],
                            "error_message": None,
                        },
                    ],
                }
            )

            def fake_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                return ScanResult(
                    run_id="run-partial",
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-03T20:01:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="21",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            events = list(
                stream_scan_events(
                    config_store=config_store,
                    history_store=history_store,
                    active_run_store=active_run_store,
                    runner=fake_runner,
                )
            )

            self.assertEqual(events[0]["type"], "scan.started")
            self.assertEqual(events[0]["total_targets"], 4 * DEFAULT_EVALUATION_COUNT)
            self.assert_active_runtime_event_state(
                events[0],
                run_id="run-partial",
                phase="scan",
                completed_targets=1,
                total_targets=4 * DEFAULT_EVALUATION_COUNT,
            )
            self.assertEqual(events[-1]["type"], "scan.finished")
            self.assertEqual(events[-1]["total_targets"], 4 * DEFAULT_EVALUATION_COUNT)
            progress_events = [event for event in events if event["type"] == "scan.progress"]
            self.assertTrue(progress_events)
            self.assertEqual(
                set(progress_events[0]["state"]),
                {"schema_version", "runtime"},
            )
            self.assertEqual(progress_events[0]["state"]["schema_version"], 1)
            self.assertTrue(progress_events[0]["state"]["runtime"]["is_running"])
            self.assertGreaterEqual(
                progress_events[0]["state"]["runtime"]["queued_evaluation_count"],
                1,
            )
            self.assertEqual(
                progress_events[0]["state"]["runtime"]["current_phase"],
                "scan",
            )
            self.assertEqual(
                progress_events[0]["state"]["runtime"]["current_phase_completed_targets"],
                2,
            )
            self.assertGreaterEqual(
                progress_events[0]["state"]["runtime"]["progress_completed"],
                events[0]["state"]["runtime"]["progress_completed"],
            )
            self.assertEqual(
                progress_events[0]["state"]["runtime"]["current_phase_total_targets"],
                4 * DEFAULT_EVALUATION_COUNT,
            )

    def test_stream_scan_events_force_restart_does_not_carry_previous_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            _disable_candidate(config_store, "codex-local-default", "gpt-5.4", "medium")
            _disable_candidate(config_store, "codex-local-default", "gpt-5.5", "medium")

            history_store.append(
                ScanResult(
                    run_id="run-partial",
                    model="gpt-5.4",
                    effort="high",
                    phase="scan",
                    question_id="01_candy",
                    question_title="Candy",
                    grader_kind="regex",
                    attempt_index=1,
                    started_at="2026-07-03T11:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="21",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )
            )

            calls: list[tuple[str, str]] = []

            def fake_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                calls.append((question.id, kwargs["run_id"]))
                return ScanResult(
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase="scan",
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-03T11:05:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="21",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            events = list(
                stream_scan_events(
                    config_store=config_store,
                    history_store=history_store,
                    active_run_store=active_run_store,
                    runner=fake_runner,
                    force_restart=True,
                )
            )

            self.assertEqual(events[0]["type"], "scan.started")
            self.assertEqual(events[0]["total_targets"], 4 * DEFAULT_EVALUATION_COUNT)
            finished_run_id = calls[0][1]
            self.assert_active_runtime_event_state(
                events[0],
                run_id=finished_run_id,
                phase="scan",
                completed_targets=0,
                total_targets=4 * DEFAULT_EVALUATION_COUNT,
            )
            self.assertEqual(
                events[-1]["result_count"],
                4 * DEFAULT_QUESTION_COUNT,
            )
            self.assertEqual(calls[0][0], "01_session_bundle_repair")
            self.assertNotEqual(calls[0][1], "run-partial")

    def test_save_config_persists_updated_scheduler_and_model_ingress(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            codex_insights_calls: list[Path] = []

            def codex_insights_provider(data_dir: Path) -> dict[str, object]:
                codex_insights_calls.append(data_dir)
                return {"workload": {"aggregates": []}}

            payload = {
                "model_ingress": {
                    "sources": [
                        {
                            "id": "local",
                            "kind": "codex",
                            "title": "Local Codex",
                            "description": "Use local Codex CLI.",
                            "mode": "local",
                            "enabled": True,
                        },
                        {
                            "id": "api",
                            "kind": "openai",
                            "title": "API Models",
                            "description": "Use remote API models.",
                            "mode": "api",
                            "enabled": True,
                        },
                    ],
                    "connections": [
                        {
                            "id": "local-default",
                            "source_id": "local",
                            "name": "Local CLI",
                            "enabled": True,
                            "api_format": None,
                            "provider_preset": "generic",
                            "base_url": None,
                            "api_key_ref": None,
                            "notes": None,
                            "last_test_status": None,
                            "last_test_at": None,
                            "last_test_message": None,
                            "model_candidates": [
                                {
                                    "id": "gpt-5.4-medium",
                                    "connection_id": "local-default",
                                    "model_id": "gpt-5.4",
                                    "display_name": "GPT-5.4 Medium",
                                    "scan_profile": "medium",
                                    "enabled": True,
                                    "capabilities": [],
                                },
                                {
                                    "id": "gpt-5.4-high",
                                    "connection_id": "local-default",
                                    "model_id": "gpt-5.4",
                                    "display_name": "GPT-5.4 High",
                                    "scan_profile": "high",
                                    "enabled": False,
                                    "capabilities": [],
                                },
                            ],
                        },
                        {
                            "id": "api-openai",
                            "source_id": "api",
                            "name": "OpenAI",
                            "enabled": True,
                            "api_format": "openai_responses",
                            "provider_preset": "generic",
                            "base_url": "https://api.example.com/v1",
                            "api_key_ref": "env:OPENAI_API_KEY",
                            "notes": "team endpoint",
                            "last_test_status": "pass",
                            "last_test_at": "2026-07-03T10:00:00+08:00",
                            "last_test_message": None,
                            "model_candidates": [
                                {
                                    "id": "gpt-5.5-high",
                                    "connection_id": "api-openai",
                                    "model_id": "gpt-5.5",
                                    "display_name": "GPT-5.5 High",
                                    "scan_profile": "high",
                                    "enabled": True,
                                    "capabilities": ["reasoning"],
                                },
                                {
                                    "id": "gpt-5.5-xhigh",
                                    "connection_id": "api-openai",
                                    "model_id": "gpt-5.5",
                                    "display_name": "GPT-5.5 XHigh",
                                    "scan_profile": "xhigh",
                                    "enabled": False,
                                    "capabilities": [],
                                },
                            ],
                        },
                    ],
                },
                "scheduler": {
                    "mode": "weekly",
                    "interval_seconds": 1800,
                    "daily_hour": 9,
                    "daily_minute": 15,
                    "weekly_weekday": 5,
                    "weekly_hour": 21,
                    "weekly_minute": 40,
                },
                "system": {
                    "use_mock_results": False,
                    "auto_open_browser": True,
                    "history_limit": 50,
                    "language": "zh-CN",
                },
                "rules": {
                    "reason_tok_516": {"enabled": True, "action": "retry", "max_retries": 2, "cooldown_seconds": 0}
                },
            }

            response = save_config(
                payload,
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
                codex_insights_provider=codex_insights_provider,
            )

            self.assertEqual(response["config"], response["state"]["config"])
            for key in (
                "advisor_v2_evidence",
                "recommendation_portfolio_v2",
                "reference_snapshot_feed",
                "recommendation_use",
                "codex_insights",
                "advisor",
                "diagnostics",
            ):
                self.assertIn(key, response["state"])
            self.assertEqual(codex_insights_calls, [Path(temp_dir)])
            self.assertEqual(response["state"]["config"]["scheduler"]["mode"], "weekly")
            self.assertEqual(response["state"]["config"]["scheduler"]["interval_seconds"], 1800)
            self.assertEqual(response["state"]["config"]["scheduler"]["daily_hour"], 9)
            self.assertEqual(response["state"]["config"]["scheduler"]["daily_minute"], 15)
            self.assertEqual(response["state"]["config"]["scheduler"]["weekly_weekday"], 5)
            self.assertEqual(response["state"]["config"]["scheduler"]["weekly_hour"], 21)
            self.assertEqual(response["state"]["config"]["scheduler"]["weekly_minute"], 40)
            self.assertEqual(
                response["state"]["config"]["model_ingress"],
                response["config"]["model_ingress"],
            )
            self.assertIn("provider_catalog", response["config"])
            self.assertIn("provider_catalog", response["state"]["config"])

            connections = response["config"]["model_ingress"]["connections"]
            self.assertEqual(len(connections), 3)
            self.assertEqual(
                connections[0]["model_candidates"][0]["family_id"],
                "gpt-5.4",
            )
            self.assertEqual(
                connections[1]["provider_id"],
                "openai",
            )
            self.assertEqual(
                connections[1]["provider_display_name"],
                "OpenAI",
            )
            self.assertEqual(
                connections[1]["catalog_source"],
                "catalog_inferred",
            )
            self.assertEqual(
                connections[1]["model_candidates"][0]["family_id"],
                "gpt-5.5",
            )

    def test_save_config_persists_advisor_v2_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            payload = config_store.load().to_dict()
            payload["recommendation"]["preference"] = "cost"
            payload["recommendation"]["source_mode_by_configuration_id"] = {
                "candidate-a": "official_snapshot"
            }

            response = save_config(
                payload,
                config_store=config_store,
                history_store=HistoryStore(Path(temp_dir) / "history.jsonl"),
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
            )
            reloaded = config_store.load()

        self.assertEqual(response["config"]["recommendation"]["preference"], "cost")
        self.assertEqual(reloaded.recommendation.preference, "cost")
        self.assertEqual(
            reloaded.recommendation.source_mode_by_configuration_id,
            {"candidate-a": "official_snapshot"},
        )


if __name__ == "__main__":
    unittest.main()
