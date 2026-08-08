from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from typing import Callable, Iterator

from .active_run_store import ActiveRunStore
from .application_services import (
    AutoResumeExecutionRouter,
    RepairCommand,
    ScanCommand,
)
from .config_application import ConfigApplicationService, ConfigCommand
from .connection_application import ConnectionProbe, ConnectionQuery
from .config_store import ConfigStore
from .codex_model_catalog import discover_codex_model_catalog
from .codex_account import read_codex_account_snapshot
from .codex_current_model import detect_codex_current_model
from .endpoint_client import discover_model_catalog, run_endpoint_request
from .history_store import HistoryStore
from .insights_application import build_codex_insights, read_codex_insights
from .local_pricing import prepare_local_pricing_snapshot
from .maintenance_application import (
    AutoResumeCommand,
    PersonalObservationCommand,
    RunControlCommand,
    RunRecoveryCommand,
)
from .models import ScanPlan, ScanResult, TargetConfig
from .model_sessions import detect_external_model_sessions
from .observation_application import (
    ObservationCommand,
    observe_session_context,
    session_observation_paths,
)
from .process_lock import exclusive_system_process_lock
from .process_environment import build_child_environment
from .protocol import version_runtime_event_stream
from .repair_planner import RepairPlan
from .runner import run_target
from .scan_plan_preview import ScanPlanPreviewQuery
from .secret_store import (
    SecretStore,
    SecretStoreError,
    install_process_secret_overrides,
)
from .service import MonitorService
from .settings_projection import SettingsProjectionProjector
from .snapshot_query import SnapshotCommand, SnapshotProjector, SnapshotQuery
from .usage_observer import observe_codex_usage

Runner = Callable[[TargetConfig, bool], ScanResult]
CodexInsightsProvider = Callable[[Path], dict[str, object]]
LOCK_HEARTBEAT_INTERVAL_SECONDS = 15
LOCK_STALE_SECONDS = 420


def _log(message: str) -> None:
    if os.environ.get("MODELDIAL_DEBUG_LOG") != "1":
        return
    try:
        print(f"[native_bridge] {message}", file=sys.stderr, flush=True)
    except (BrokenPipeError, OSError, ValueError):
        pass


def _write_json(payload: object, *, flush: bool = False) -> None:
    try:
        print(json.dumps(payload, ensure_ascii=False), flush=flush)
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


def _read_lock_payload(lock_path: Path) -> tuple[int, float | None]:
    try:
        raw_text = lock_path.read_text(encoding="utf-8").strip()
    except OSError:
        return -1, None
    if not raw_text:
        return -1, None
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        try:
            return int(raw_text), None
        except ValueError:
            return -1, None
    if isinstance(payload, int):
        return payload, None
    if not isinstance(payload, dict):
        return -1, None
    try:
        pid = int(payload.get("pid", -1))
    except (TypeError, ValueError):
        pid = -1
    heartbeat_raw = payload.get("heartbeat_at")
    try:
        heartbeat_at = float(heartbeat_raw) if heartbeat_raw is not None else None
    except (TypeError, ValueError):
        heartbeat_at = None
    return pid, heartbeat_at


def _write_lock_payload(lock_path: Path, pid: int) -> None:
    temporary = lock_path.with_name(
        f".{lock_path.name}.{pid}.{threading.get_ident()}.tmp"
    )
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(
                {
                    "pid": pid,
                    "heartbeat_at": time.time(),
                },
                handle,
            )
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(lock_path)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _lock_is_stale(lock_path: Path, heartbeat_at: float | None) -> bool:
    reference_time = heartbeat_at
    if reference_time is None:
        try:
            reference_time = lock_path.stat().st_mtime
        except OSError:
            return True
    return (time.time() - reference_time) > LOCK_STALE_SECONDS


def _scan_child_process_ids(parent_pid: int) -> list[int]:
    if parent_pid <= 0:
        return []
    if os.name == "nt":
        command = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-CimInstance Win32_Process | ForEach-Object { '{0} {1}' -f $_.ProcessId, $_.ParentProcessId }",
        ]
    else:
        command = ["/bin/ps", "-ax", "-o", "pid=,ppid="]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
            env=build_child_environment(),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []
    children_by_parent: dict[int, list[int]] = {}
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            pid, ppid = (int(part) for part in parts)
        except ValueError:
            continue
        children_by_parent.setdefault(ppid, []).append(pid)

    descendants: list[int] = []
    pending = list(children_by_parent.get(parent_pid, []))
    while pending:
        pid = pending.pop()
        descendants.append(pid)
        pending.extend(children_by_parent.get(pid, []))
    return descendants


def _terminate_scan_child_processes(lock_path: Path) -> int:
    owner_pid, heartbeat_at = _read_lock_payload(lock_path)
    if (
        not _process_is_alive(owner_pid)
        or _lock_is_stale(lock_path, heartbeat_at)
    ):
        return 0
    child_pids = list(reversed(_scan_child_process_ids(owner_pid)))
    if os.name == "nt":
        terminated = 0
        for pid in child_pids:
            try:
                completed = subprocess.run(
                    ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=2,
                    env=build_child_environment(),
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if completed.returncode == 0:
                terminated += 1
        return terminated
    terminated = 0
    for pid in child_pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            continue
        terminated += 1
    if terminated:
        time.sleep(0.2)
    for pid in child_pids:
        if not _process_is_alive(pid):
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            continue
    return terminated


@contextmanager
def _scan_process_lock(
    active_run_store: ActiveRunStore,
    history_store: HistoryStore,
    *,
    lease_heartbeat: Callable[[], object] | None = None,
) -> Iterator[bool]:
    lock_path = active_run_store.path.with_name("scan.lock")
    guard_path = lock_path.with_name(f"{lock_path.name}.guard")
    current_pid = os.getpid()
    heartbeat_stop = threading.Event()
    heartbeat_thread: threading.Thread | None = None
    heartbeat_started = False

    with exclusive_system_process_lock(guard_path) as guard_acquired:
        if not guard_acquired:
            yield False
            return

        if lock_path.exists():
            existing_pid, heartbeat_at = _read_lock_payload(lock_path)
            if (
                _process_is_alive(existing_pid)
                and not _lock_is_stale(lock_path, heartbeat_at)
            ):
                yield False
                return
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass

        _write_lock_payload(lock_path, current_pid)

        def heartbeat() -> None:
            while not heartbeat_stop.wait(LOCK_HEARTBEAT_INTERVAL_SECONDS):
                try:
                    pid, _ = _read_lock_payload(lock_path)
                    if pid != current_pid:
                        return
                    _write_lock_payload(lock_path, current_pid)
                    if lease_heartbeat is not None:
                        lease_heartbeat()
                except OSError:
                    return

        heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        try:
            heartbeat_thread.start()
            heartbeat_started = True
            yield True
        finally:
            heartbeat_stop.set()
            if heartbeat_thread is not None and heartbeat_started:
                heartbeat_thread.join()
            try:
                pid, _ = _read_lock_payload(lock_path)
                if pid == current_pid:
                    lock_path.unlink()
            except FileNotFoundError:
                pass


def read_config(config_store: ConfigStore | None = None) -> dict[str, object]:
    store = config_store or ConfigStore(
        Path(__file__).resolve().parent.parent / "artifacts" / "config.json"
    )
    return store.load().to_dict()


def recover_orphaned_run(
    config_store: ConfigStore | None = None,
    history_store: HistoryStore | None = None,
    active_run_store: ActiveRunStore | None = None,
) -> dict[str, object]:
    service = MonitorService(
        config_store=config_store,
        history_store=history_store,
        active_run_store=active_run_store,
    )
    return RunRecoveryCommand(service).recover(process_lock=_scan_process_lock)


def _session_observation_paths(data_dir: Path) -> tuple[Path, Path, Path]:
    return session_observation_paths(data_dir)


def _query_monitor_service(
    config_store: ConfigStore | None = None,
    history_store: HistoryStore | None = None,
    active_run_store: ActiveRunStore | None = None,
) -> MonitorService:
    service = MonitorService(
        config_store=config_store,
        history_store=history_store,
        active_run_store=active_run_store,
    )
    data_dir = service.history_store.path.parent
    tracker_path, inbox_path, registry_path = _session_observation_paths(data_dir)
    service.current_model_detector = lambda: detect_codex_current_model(
        cache_path=tracker_path,
        event_inbox_path=inbox_path,
        registry_path=registry_path,
        persist_cache=False,
        consume_registry_events=False,
    )
    service.active_session_detector = lambda: detect_external_model_sessions(
        event_inbox_path=inbox_path,
        registry_path=registry_path,
        consume_registry_events=False,
    )
    return service


def _snapshot_projector(service: MonitorService) -> SnapshotProjector:
    return SnapshotProjector(
        config_reader=service.config_store.load,
        state_reader=service.monitor_state_projector.build_state,
        settings_projector=SettingsProjectionProjector(
            service.scan_target_resolver
        ),
    )


def _snapshot_query(service: MonitorService) -> SnapshotQuery:
    return SnapshotQuery(
        snapshot_projector=_snapshot_projector(service),
        refresh_state_reader=service.monitor_state_projector.build_refresh_state,
        data_dir=service.history_store.path.parent,
    )


def _snapshot_command(service: MonitorService) -> SnapshotCommand:
    return SnapshotCommand(
        snapshot_projector=_snapshot_projector(service),
        data_dir=service.history_store.path.parent,
    )


def _observe_session_context(data_dir: Path) -> dict[str, int]:
    return observe_session_context(data_dir)


def build_snapshot(
    config_store: ConfigStore | None = None,
    history_store: HistoryStore | None = None,
    active_run_store: ActiveRunStore | None = None,
    *,
    codex_insights: dict[str, object] | None = None,
) -> dict[str, object]:
    service = _query_monitor_service(
        config_store=config_store,
        history_store=history_store,
        active_run_store=active_run_store,
    )
    return _snapshot_query(service).build_snapshot(codex_insights=codex_insights)


def _build_terminal_snapshot(
    config_store: ConfigStore | None = None,
    history_store: HistoryStore | None = None,
    active_run_store: ActiveRunStore | None = None,
    *,
    codex_insights_provider: CodexInsightsProvider | None = None,
) -> dict[str, object]:
    codex_insights = (
        _read_codex_insights(_data_directory(history_store))
        if codex_insights_provider is not None
        else None
    )
    return build_snapshot(
        config_store,
        history_store,
        active_run_store,
        codex_insights=codex_insights,
    )


def _build_codex_insights(
    data_dir: Path,
    *,
    account_reader: Callable[..., dict[str, object]] = read_codex_account_snapshot,
    usage_observer: Callable[..., dict[str, object]] = observe_codex_usage,
    force_account_refresh: bool = False,
) -> dict[str, object]:
    return build_codex_insights(
        data_dir,
        account_reader=account_reader,
        usage_observer=usage_observer,
        force_account_refresh=force_account_refresh,
    )


def _read_codex_insights(data_dir: Path) -> dict[str, object]:
    return read_codex_insights(data_dir)


def build_refresh_snapshot(
    config_store: ConfigStore | None = None,
    history_store: HistoryStore | None = None,
    active_run_store: ActiveRunStore | None = None,
    *,
    codex_insights: dict[str, object] | None = None,
) -> dict[str, object]:
    service = _query_monitor_service(
        config_store=config_store,
        history_store=history_store,
        active_run_store=active_run_store,
    )
    return _snapshot_query(service).build_refresh_snapshot(
        codex_insights=codex_insights
    )


def preview_scan(
    config_store: ConfigStore | None = None,
    history_store: HistoryStore | None = None,
    active_run_store: ActiveRunStore | None = None,
    *,
    force_restart: bool = False,
    requested_candidate_ids: list[str] | None = None,
    selection_mode: str = "regular",
    custom_round_mode: str = "new_round",
    evaluation_profile_id: str | None = None,
    upgrade_from_run_id: str | None = None,
    custom_options: bool = False,
) -> dict[str, object]:
    service = _query_monitor_service(
        config_store=config_store,
        history_store=history_store,
        active_run_store=active_run_store,
    )
    query = ScanPlanPreviewQuery(
        service=service,
        snapshot_projector=_snapshot_projector(service),
    )
    if custom_options:
        return query.preview_custom_options(
            requested_candidate_ids=list(requested_candidate_ids or []),
            evaluation_profile_id=evaluation_profile_id,
        )
    return query.build_preview(
        force_restart=force_restart,
        requested_candidate_ids=requested_candidate_ids,
        selection_mode=selection_mode,
        custom_round_mode=custom_round_mode,
        evaluation_profile_id=evaluation_profile_id,
        upgrade_from_run_id=upgrade_from_run_id,
    )


def _build_command_snapshot(
    config_store: ConfigStore | None = None,
    history_store: HistoryStore | None = None,
    active_run_store: ActiveRunStore | None = None,
    *,
    codex_insights_provider: CodexInsightsProvider | None = None,
    codex_insights: dict[str, object] | None = None,
    refresh_reference: bool = False,
) -> dict[str, object]:
    service = _query_monitor_service(
        config_store=config_store,
        history_store=history_store,
        active_run_store=active_run_store,
    )
    return _snapshot_command(service).build_snapshot(
        codex_insights_provider=codex_insights_provider,
        codex_insights=codex_insights,
        refresh_reference=refresh_reference,
    )


def observe_state(
    config_store: ConfigStore | None = None,
    history_store: HistoryStore | None = None,
    active_run_store: ActiveRunStore | None = None,
    *,
    include_codex_insights: bool = False,
) -> dict[str, object]:
    service = _query_monitor_service(
        config_store=config_store,
        history_store=history_store,
        active_run_store=active_run_store,
    )
    return ObservationCommand(_snapshot_command(service)).observe_state(
        include_codex_insights=include_codex_insights,
        session_observer=_observe_session_context,
        build_insights=_build_codex_insights,
        read_insights=_read_codex_insights,
    )


def refresh_reference_snapshots(
    config_store: ConfigStore | None = None,
    history_store: HistoryStore | None = None,
    active_run_store: ActiveRunStore | None = None,
) -> dict[str, object]:
    service = _query_monitor_service(
        config_store=config_store,
        history_store=history_store,
        active_run_store=active_run_store,
    )
    return ObservationCommand(_snapshot_command(service)).refresh_reference(
        read_insights=_read_codex_insights,
    )


def save_config(
    payload: dict[str, object],
    config_store: ConfigStore | None = None,
    history_store: HistoryStore | None = None,
    active_run_store: ActiveRunStore | None = None,
    codex_insights_provider: CodexInsightsProvider | None = None,
) -> dict[str, object]:
    return _config_application_service(
        config_store=config_store,
        history_store=history_store,
        active_run_store=active_run_store,
        codex_insights_provider=codex_insights_provider,
    ).replace_legacy_config(payload)


def patch_config(
    payload: dict[str, object],
    config_store: ConfigStore | None = None,
    history_store: HistoryStore | None = None,
    active_run_store: ActiveRunStore | None = None,
    codex_insights_provider: CodexInsightsProvider | None = None,
) -> dict[str, object]:
    return _config_application_service(
        config_store=config_store,
        history_store=history_store,
        active_run_store=active_run_store,
        codex_insights_provider=codex_insights_provider,
    ).patch_config(payload)


def migrate_secret_references(
    payload: dict[str, object],
    config_store: ConfigStore | None = None,
) -> dict[str, object]:
    store = config_store or ConfigStore(
        Path(__file__).resolve().parent.parent / "artifacts" / "config.json"
    )
    return ConfigCommand(store).migrate_secret_references(payload)


def upsert_endpoint(
    payload: dict[str, object],
    config_store: ConfigStore | None = None,
    history_store: HistoryStore | None = None,
    active_run_store: ActiveRunStore | None = None,
    codex_insights_provider: CodexInsightsProvider | None = None,
) -> dict[str, object]:
    return _config_application_service(
        config_store=config_store,
        history_store=history_store,
        active_run_store=active_run_store,
        codex_insights_provider=codex_insights_provider,
    ).upsert_endpoint(payload)


def add_endpoint_models(
    payload: dict[str, object],
    config_store: ConfigStore | None = None,
    history_store: HistoryStore | None = None,
    active_run_store: ActiveRunStore | None = None,
    codex_insights_provider: CodexInsightsProvider | None = None,
) -> dict[str, object]:
    return _config_application_service(
        config_store=config_store,
        history_store=history_store,
        active_run_store=active_run_store,
        codex_insights_provider=codex_insights_provider,
    ).add_endpoint_models(payload)


def _config_application_service(
    *,
    config_store: ConfigStore | None,
    history_store: HistoryStore | None,
    active_run_store: ActiveRunStore | None,
    codex_insights_provider: CodexInsightsProvider | None,
) -> ConfigApplicationService:
    service = MonitorService(
        config_store=config_store,
        history_store=history_store,
        active_run_store=active_run_store,
    )
    return ConfigApplicationService(
        service=service,
        snapshot_builder=_build_command_snapshot,
        codex_insights_provider=codex_insights_provider,
    )


def import_local_provider(
    provider_id: str,
    *,
    config_store: ConfigStore | None = None,
    grok_login_checker: Callable[[], None] | None = None,
    claude_login_checker: Callable[[], None] | None = None,
    local_provider_detector: Callable[[], list[dict[str, object]]] | None = None,
) -> dict[str, object]:
    store = config_store or ConfigStore(
        Path(__file__).resolve().parent.parent / "artifacts" / "config.json"
    )
    return ConfigCommand(store).import_local_provider(
        provider_id,
        grok_login_checker=grok_login_checker,
        claude_login_checker=claude_login_checker,
        local_provider_detector=local_provider_detector,
    )


def discover_local_models(
    provider_id: str,
    *,
    config_store: ConfigStore | None = None,
    discoverer=discover_codex_model_catalog,
) -> dict[str, object]:
    store = config_store or ConfigStore(
        Path(__file__).resolve().parent.parent / "artifacts" / "config.json"
    )
    return ConnectionQuery(store).discover_local_models(
        provider_id,
        discoverer=discoverer,
    )


def probe_endpoint_connection(
    *,
    base_url: str,
    api_format: str,
    provider_preset: str,
    model_id: str,
    api_key: str,
    scan_profile: str = "default",
    requester=run_endpoint_request,
) -> dict[str, object]:
    return ConnectionProbe.test(
        base_url=base_url,
        api_format=api_format,
        provider_preset=provider_preset,
        model_id=model_id,
        api_key=api_key,
        scan_profile=scan_profile,
        requester=requester,
    )


def probe_endpoint_models(
    *,
    base_url: str,
    api_format: str,
    api_key: str,
    discoverer=discover_model_catalog,
) -> dict[str, object]:
    return ConnectionProbe.discover(
        base_url=base_url,
        api_format=api_format,
        api_key=api_key,
        discoverer=discoverer,
    )


def request_scan_control(
    action: str,
    active_run_store: ActiveRunStore | None = None,
    *,
    client_session_id: str | None = None,
) -> dict[str, object]:
    store = active_run_store or ActiveRunStore(
        Path(__file__).resolve().parent.parent / "artifacts" / "active_run.json"
    )
    return RunControlCommand(
        store,
        HistoryStore(store.path.with_name("history.jsonl")),
    ).request(
        action,
        client_session_id=client_session_id,
        terminate_children=_terminate_scan_child_processes,
    )


def dismiss_resumable_run(
    active_run_store: ActiveRunStore | None = None,
    history_store: HistoryStore | None = None,
) -> dict[str, object]:
    store = active_run_store or ActiveRunStore(
        Path(__file__).resolve().parent.parent / "artifacts" / "active_run.json"
    )
    history = history_store or HistoryStore(store.path.with_name("history.jsonl"))
    return RunControlCommand(store, history).dismiss_resumable()


def export_personal_observations(
    history_store: HistoryStore | None = None,
) -> dict[str, object]:
    return PersonalObservationCommand(
        _data_directory(history_store)
    ).export()


def clear_personal_observations(
    history_store: HistoryStore | None = None,
    *,
    sessions_root: Path | None = None,
) -> dict[str, object]:
    return PersonalObservationCommand(_data_directory(history_store)).clear(
        sessions_root=sessions_root,
    )


def _data_directory(history_store: HistoryStore | None) -> Path:
    if history_store is not None:
        return history_store.path.parent
    return Path(__file__).resolve().parent.parent / "artifacts"


def _backend_root() -> Path:
    configured = os.environ.get("MODELDIAL_BACKEND_ROOT", "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent.parent


def _prepare_local_scan_pricing(plan: ScanPlan, data_root: Path) -> dict[str, object]:
    scope_id = str(
        plan.run_metadata.get("comparison_group_id") or plan.run_id
    )
    resume_history = (
        plan.resume.get("run_history", [])
        if isinstance(plan.resume, dict)
        else []
    )
    snapshot_ids = (
        result.pricing_snapshot
        for result in resume_history
        if isinstance(result, ScanResult) and result.pricing_snapshot
    )
    return prepare_local_pricing_snapshot(
        backend_root=_backend_root(),
        data_root=data_root,
        scope_id=scope_id,
        historical_snapshot_ids=snapshot_ids,
        refresh=plan.resume is None,
    )


def _prepare_local_repair_pricing(
    plan: RepairPlan,
    data_root: Path,
) -> dict[str, object]:
    member_run_ids = set(plan.group_member_run_ids)
    snapshot_ids = (
        result.pricing_snapshot
        for result in plan.history
        if result.run_id in member_run_ids and result.pricing_snapshot
    )
    return prepare_local_pricing_snapshot(
        backend_root=_backend_root(),
        data_root=data_root,
        scope_id=plan.requested_group_id,
        historical_snapshot_ids=snapshot_ids,
        refresh=False,
    )


def discover_connection_models(
    connection_id: str,
    *,
    config_store: ConfigStore | None = None,
    secret_store: SecretStore | None = None,
    discoverer=discover_model_catalog,
) -> dict[str, object]:
    store = config_store or ConfigStore(
        Path(__file__).resolve().parent.parent / "artifacts" / "config.json"
    )
    return ConnectionQuery(store).discover_models(
        connection_id,
        secret_store=secret_store,
        discoverer=discoverer,
    )


def verify_endpoint_connection(
    connection_id: str,
    model_id: str,
    *,
    config_store: ConfigStore | None = None,
    secret_store: SecretStore | None = None,
    requester=run_endpoint_request,
) -> dict[str, object]:
    store = config_store or ConfigStore(
        Path(__file__).resolve().parent.parent / "artifacts" / "config.json"
    )
    return ConfigCommand(store).verify_endpoint_connection(
        connection_id,
        model_id,
        secret_store=secret_store,
        requester=requester,
    )


@version_runtime_event_stream
def stream_scan_events(
    config_store: ConfigStore | None = None,
    history_store: HistoryStore | None = None,
    active_run_store: ActiveRunStore | None = None,
    runner: Runner = run_target,
    force_restart: bool = False,
    requested_candidate_ids: list[str] | None = None,
    selection_mode: str = "regular",
    custom_round_mode: str = "new_round",
    evaluation_profile_id: str | None = None,
    upgrade_from_run_id: str | None = None,
    codex_insights_provider: CodexInsightsProvider | None = None,
    pricing_preparer: Callable[[ScanPlan], object] | None = None,
) -> Iterator[dict[str, object]]:
    service = MonitorService(
        config_store=config_store,
        history_store=history_store,
        active_run_store=active_run_store,
        runner=runner,
    )
    yield from ScanCommand(service).stream_events(
        force_restart=force_restart,
        requested_candidate_ids=requested_candidate_ids,
        selection_mode=selection_mode,
        custom_round_mode=custom_round_mode,
        evaluation_profile_id=evaluation_profile_id,
        upgrade_from_run_id=upgrade_from_run_id,
        process_lock=_scan_process_lock,
        snapshot_builder=_build_command_snapshot,
        terminal_snapshot_builder=_build_terminal_snapshot,
        codex_insights_provider=codex_insights_provider,
        prepare_execution=pricing_preparer,
        log=_log,
    )


@version_runtime_event_stream
def stream_repair_events(
    *,
    run_id: str,
    candidate_id: str,
    question_id: str | None = None,
    config_store: ConfigStore | None = None,
    history_store: HistoryStore | None = None,
    active_run_store: ActiveRunStore | None = None,
    runner: Runner = run_target,
    codex_insights_provider: CodexInsightsProvider | None = None,
    pricing_preparer: Callable[[RepairPlan], object] | None = None,
) -> Iterator[dict[str, object]]:
    service = MonitorService(
        config_store=config_store,
        history_store=history_store,
        active_run_store=active_run_store,
        runner=runner,
    )
    yield from RepairCommand(service).stream_candidate_events(
        run_id=run_id,
        candidate_id=candidate_id,
        question_id=question_id,
        process_lock=_scan_process_lock,
        snapshot_builder=_build_command_snapshot,
        terminal_snapshot_builder=_build_terminal_snapshot,
        codex_insights_provider=codex_insights_provider,
        prepare_execution=pricing_preparer,
    )


@version_runtime_event_stream
def stream_failed_repair_events(
    *,
    run_id: str,
    candidate_ids: list[str],
    config_store: ConfigStore | None = None,
    history_store: HistoryStore | None = None,
    active_run_store: ActiveRunStore | None = None,
    runner: Runner = run_target,
    codex_insights_provider: CodexInsightsProvider | None = None,
    pricing_preparer: Callable[[RepairPlan], object] | None = None,
) -> Iterator[dict[str, object]]:
    yield from _stream_batch_repair_events(
        run_id=run_id,
        candidate_ids=candidate_ids,
        timeouts_only=False,
        config_store=config_store,
        history_store=history_store,
        active_run_store=active_run_store,
        runner=runner,
        codex_insights_provider=codex_insights_provider,
        pricing_preparer=pricing_preparer,
    )


@version_runtime_event_stream
def stream_timed_out_repair_events(
    *,
    run_id: str,
    candidate_ids: list[str],
    config_store: ConfigStore | None = None,
    history_store: HistoryStore | None = None,
    active_run_store: ActiveRunStore | None = None,
    runner: Runner = run_target,
    codex_insights_provider: CodexInsightsProvider | None = None,
    pricing_preparer: Callable[[RepairPlan], object] | None = None,
) -> Iterator[dict[str, object]]:
    yield from _stream_batch_repair_events(
        run_id=run_id,
        candidate_ids=candidate_ids,
        timeouts_only=True,
        config_store=config_store,
        history_store=history_store,
        active_run_store=active_run_store,
        runner=runner,
        codex_insights_provider=codex_insights_provider,
        pricing_preparer=pricing_preparer,
    )


def _stream_batch_repair_events(
    *,
    run_id: str,
    candidate_ids: list[str],
    timeouts_only: bool,
    config_store: ConfigStore | None,
    history_store: HistoryStore | None,
    active_run_store: ActiveRunStore | None,
    runner: Runner,
    codex_insights_provider: CodexInsightsProvider | None,
    pricing_preparer: Callable[[RepairPlan], object] | None,
) -> Iterator[dict[str, object]]:
    service = MonitorService(
        config_store=config_store,
        history_store=history_store,
        active_run_store=active_run_store,
        runner=runner,
    )
    yield from RepairCommand(service).stream_batch_events(
        run_id=run_id,
        candidate_ids=candidate_ids,
        timeouts_only=timeouts_only,
        process_lock=_scan_process_lock,
        snapshot_builder=_build_command_snapshot,
        terminal_snapshot_builder=_build_terminal_snapshot,
        codex_insights_provider=codex_insights_provider,
        prepare_execution=pricing_preparer,
    )


@version_runtime_event_stream
def stream_auto_resume_events(
    *,
    trigger: str,
    client_session_id: str,
    config_store: ConfigStore | None = None,
    history_store: HistoryStore | None = None,
    active_run_store: ActiveRunStore | None = None,
    runner: Runner = run_target,
    codex_insights_provider: CodexInsightsProvider | None = None,
    scan_pricing_preparer: Callable[[ScanPlan], object] | None = None,
    repair_pricing_preparer: Callable[[RepairPlan], object] | None = None,
) -> Iterator[dict[str, object]]:
    service = MonitorService(
        config_store=config_store,
        history_store=history_store,
        active_run_store=active_run_store,
        runner=runner,
    )

    resume_router = AutoResumeExecutionRouter(
        service=service,
        snapshot_builder=_build_command_snapshot,
        terminal_snapshot_builder=_build_terminal_snapshot,
        codex_insights_provider=codex_insights_provider,
        prepare_scan_execution=scan_pricing_preparer,
        prepare_repair_execution=repair_pricing_preparer,
        log=_log,
    )

    yield from AutoResumeCommand(
        service,
        process_lock=_scan_process_lock,
        resume_stream=resume_router.stream,
        terminal_snapshot_builder=lambda: _build_terminal_snapshot(
            service.config_store,
            service.history_store,
            service.active_run_store,
            codex_insights_provider=codex_insights_provider,
        ),
    ).resume_if_needed(trigger, client_session_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Bridge for native modeldial host.")
    parser.add_argument(
        "command",
        choices=[
            "read-config",
            "recover-run",
            "observe-state",
            "refresh-reference",
            "snapshot",
            "refresh-snapshot",
            "preview-scan",
            "import-local-provider",
            "scan",
            "auto-resume",
            "repair-candidate",
            "repair-failures",
            "repair-timeouts",
            "control-scan",
            "dismiss-resumable",
            "export-personal-observations",
            "clear-personal-observations",
            "migrate-secret-references",
            "patch-config",
            "upsert-endpoint",
            "add-endpoint-models",
            "save-config",
            "discover-local-models",
            "discover-models",
            "test-connection",
            "probe-endpoint",
        ],
    )
    parser.add_argument("--config-path")
    parser.add_argument("--history-path")
    parser.add_argument("--active-run-path")
    parser.add_argument("--payload")
    parser.add_argument("--connection-id")
    parser.add_argument("--provider-id")
    parser.add_argument("--model-id")
    parser.add_argument("--probe-action", choices=["test", "discover"])
    parser.add_argument("--action", choices=["pause", "stop"])
    parser.add_argument("--trigger", choices=["startup", "interruption"])
    parser.add_argument("--client-session-id")
    parser.add_argument("--secret-stdin", action="store_true")
    parser.add_argument("--include-codex-insights", action="store_true")
    parser.add_argument("--force-restart", action="store_true")
    parser.add_argument("--candidate-id", action="append", dest="candidate_ids")
    parser.add_argument("--run-id")
    parser.add_argument("--question-id")
    parser.add_argument(
        "--selection-mode",
        choices=["regular", "custom", "single", "incremental_full"],
        default="regular",
    )
    parser.add_argument(
        "--custom-round-mode",
        choices=["append", "new_round"],
        default="new_round",
    )
    parser.add_argument("--evaluation-profile-id")
    parser.add_argument("--upgrade-from-run-id")
    parser.add_argument("--custom-options", action="store_true")
    args = parser.parse_args()

    if args.secret_stdin:
        if args.command == "preview-scan":
            parser.error("preview-scan does not accept --secret-stdin")
        secret_payload = json.load(sys.stdin)
        if not isinstance(secret_payload, dict) or not all(
            isinstance(reference, str) and isinstance(secret, str)
            for reference, secret in secret_payload.items()
        ):
            parser.error("secret stdin payload must be a string map")
        install_process_secret_overrides(secret_payload, strict=True)

    config_store = (
        ConfigStore(Path(args.config_path), first_run_defaults=True)
        if args.config_path
        else None
    )
    history_store = HistoryStore(Path(args.history_path)) if args.history_path else None
    active_run_store = ActiveRunStore(Path(args.active_run_path)) if args.active_run_path else None
    local_data_root = _data_directory(history_store)
    scan_pricing_preparer = lambda plan: _prepare_local_scan_pricing(
        plan,
        local_data_root,
    )
    repair_pricing_preparer = lambda plan: _prepare_local_repair_pricing(
        plan,
        local_data_root,
    )

    if args.command == "read-config":
        _write_json(read_config(config_store))
        return

    if args.command == "recover-run":
        _write_json(
            recover_orphaned_run(
                config_store,
                history_store,
                active_run_store,
            )
        )
        return

    if args.command == "observe-state":
        _write_json(
            observe_state(
                config_store,
                history_store,
                active_run_store,
                include_codex_insights=args.include_codex_insights,
            )
        )
        return

    if args.command == "refresh-reference":
        _write_json(
            refresh_reference_snapshots(
                config_store,
                history_store,
                active_run_store,
            )
        )
        return

    if args.command == "snapshot":
        codex_insights = (
            _read_codex_insights(_data_directory(history_store))
            if args.include_codex_insights
            else None
        )
        _write_json(
            build_snapshot(
                config_store,
                history_store,
                active_run_store,
                codex_insights=codex_insights,
            )
        )
        return

    if args.command == "refresh-snapshot":
        codex_insights = (
            _read_codex_insights(_data_directory(history_store))
            if args.include_codex_insights
            else None
        )
        _write_json(
            build_refresh_snapshot(
                config_store,
                history_store,
                active_run_store,
                codex_insights=codex_insights,
            )
        )
        return

    if args.command == "preview-scan":
        _write_json(
            preview_scan(
                config_store,
                history_store,
                active_run_store,
                force_restart=args.force_restart,
                requested_candidate_ids=args.candidate_ids,
                selection_mode=args.selection_mode,
                custom_round_mode=args.custom_round_mode,
                evaluation_profile_id=args.evaluation_profile_id,
                upgrade_from_run_id=args.upgrade_from_run_id,
                custom_options=args.custom_options,
            )
        )
        return

    if args.command == "save-config":
        payload = json.loads(args.payload or "{}")
        _write_json(
            save_config(
                payload,
                config_store,
                history_store,
                active_run_store,
                codex_insights_provider=(
                    _build_codex_insights if args.include_codex_insights else None
                ),
            )
        )
        return

    if args.command == "patch-config":
        payload = json.loads(args.payload or "{}")
        if not isinstance(payload, dict):
            parser.error("patch-config payload must be an object")
        _write_json(
            patch_config(
                payload,
                config_store,
                history_store,
                active_run_store,
                codex_insights_provider=(
                    _build_codex_insights if args.include_codex_insights else None
                ),
            )
        )
        return

    if args.command == "migrate-secret-references":
        payload = json.loads(args.payload or "{}")
        if not isinstance(payload, dict):
            parser.error("migrate-secret-references payload must be an object")
        _write_json(migrate_secret_references(payload, config_store))
        return

    if args.command in {"upsert-endpoint", "add-endpoint-models"}:
        payload = json.loads(args.payload or "{}")
        if not isinstance(payload, dict):
            parser.error(f"{args.command} payload must be an object")
        command = (
            upsert_endpoint
            if args.command == "upsert-endpoint"
            else add_endpoint_models
        )
        _write_json(
            command(
                payload,
                config_store,
                history_store,
                active_run_store,
                codex_insights_provider=(
                    _build_codex_insights if args.include_codex_insights else None
                ),
            )
        )
        return

    if args.command == "import-local-provider":
        if not args.provider_id:
            parser.error("import-local-provider requires --provider-id")
        _write_json(
            import_local_provider(args.provider_id, config_store=config_store)
        )
        return

    if args.command == "discover-local-models":
        if not args.provider_id:
            parser.error("discover-local-models requires --provider-id")
        _write_json(
            discover_local_models(
                args.provider_id,
                config_store=config_store,
            )
        )
        return

    if args.command == "control-scan":
        if not args.action:
            parser.error("control-scan requires --action")
        _write_json(
            request_scan_control(
                args.action,
                active_run_store,
                client_session_id=args.client_session_id,
            )
        )
        return

    if args.command == "dismiss-resumable":
        _write_json(dismiss_resumable_run(active_run_store, history_store))
        return

    if args.command == "export-personal-observations":
        _write_json(export_personal_observations(history_store))
        return

    if args.command == "clear-personal-observations":
        _write_json(clear_personal_observations(history_store))
        return

    if args.command == "discover-models":
        if not args.connection_id:
            parser.error("discover-models requires --connection-id")
        _write_json(
            discover_connection_models(
                args.connection_id,
                config_store=config_store,
            )
        )
        return

    if args.command == "test-connection":
        if not args.connection_id or not args.model_id:
            parser.error("test-connection requires --connection-id and --model-id")
        _write_json(
            verify_endpoint_connection(
                args.connection_id,
                args.model_id,
                config_store=config_store,
            )
        )
        return

    if args.command == "probe-endpoint":
        payload = json.loads(args.payload or "{}")
        api_key_ref = str(payload.get("api_key_ref") or "")
        try:
            api_key = SecretStore().resolve(api_key_ref)
        except SecretStoreError:
            parser.error("probe-endpoint requires a valid secret payload")
        if args.probe_action == "discover":
            response = probe_endpoint_models(
                base_url=str(payload.get("base_url") or ""),
                api_format=str(
                    payload.get("api_format") or "openai_chat_completions"
                ),
                api_key=api_key,
            )
        elif args.probe_action == "test":
            response = probe_endpoint_connection(
                base_url=str(payload.get("base_url") or ""),
                api_format=str(payload.get("api_format") or ""),
                provider_preset=str(payload.get("provider_preset") or "generic"),
                model_id=str(payload.get("model_id") or ""),
                scan_profile=str(payload.get("scan_profile") or "default"),
                api_key=api_key,
            )
        else:
            parser.error("probe-endpoint requires --probe-action")
        _write_json(response)
        return

    if args.command == "repair-candidate":
        if not args.run_id:
            parser.error("repair-candidate requires --run-id")
        if not args.candidate_ids or len(args.candidate_ids) != 1:
            parser.error("repair-candidate requires exactly one --candidate-id")
        for event in stream_repair_events(
            run_id=args.run_id,
            candidate_id=args.candidate_ids[0],
            question_id=args.question_id,
            config_store=config_store,
            history_store=history_store,
            active_run_store=active_run_store,
            codex_insights_provider=(
                _build_codex_insights if args.include_codex_insights else None
            ),
            pricing_preparer=repair_pricing_preparer,
        ):
            _write_json(event, flush=True)
        return

    if args.command == "auto-resume":
        if not args.trigger:
            parser.error("auto-resume requires --trigger")
        if not args.client_session_id:
            parser.error("auto-resume requires --client-session-id")
        for event in stream_auto_resume_events(
            trigger=args.trigger,
            client_session_id=args.client_session_id,
            config_store=config_store,
            history_store=history_store,
            active_run_store=active_run_store,
            codex_insights_provider=(
                _build_codex_insights if args.include_codex_insights else None
            ),
            scan_pricing_preparer=scan_pricing_preparer,
            repair_pricing_preparer=repair_pricing_preparer,
        ):
            _write_json(event, flush=True)
        return

    if args.command == "repair-failures":
        if not args.run_id:
            parser.error("repair-failures requires --run-id")
        if not args.candidate_ids:
            parser.error("repair-failures requires at least one --candidate-id")
        for event in stream_failed_repair_events(
            run_id=args.run_id,
            candidate_ids=args.candidate_ids,
            config_store=config_store,
            history_store=history_store,
            active_run_store=active_run_store,
            codex_insights_provider=(
                _build_codex_insights if args.include_codex_insights else None
            ),
            pricing_preparer=repair_pricing_preparer,
        ):
            _write_json(event, flush=True)
        return

    if args.command == "repair-timeouts":
        if not args.run_id:
            parser.error("repair-timeouts requires --run-id")
        if not args.candidate_ids:
            parser.error("repair-timeouts requires at least one --candidate-id")
        for event in stream_timed_out_repair_events(
            run_id=args.run_id,
            candidate_ids=args.candidate_ids,
            config_store=config_store,
            history_store=history_store,
            active_run_store=active_run_store,
            codex_insights_provider=(
                _build_codex_insights if args.include_codex_insights else None
            ),
            pricing_preparer=repair_pricing_preparer,
        ):
            _write_json(event, flush=True)
        return

    for event in stream_scan_events(
        config_store,
        history_store,
        active_run_store,
        force_restart=args.force_restart,
        requested_candidate_ids=args.candidate_ids,
        selection_mode=args.selection_mode,
        custom_round_mode=args.custom_round_mode,
        evaluation_profile_id=args.evaluation_profile_id,
        upgrade_from_run_id=args.upgrade_from_run_id,
        codex_insights_provider=(
            _build_codex_insights if args.include_codex_insights else None
        ),
        pricing_preparer=scan_pricing_preparer,
    ):
        _write_json(event, flush=True)


if __name__ == "__main__":
    main()
