from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import time
from typing import Iterable, Iterator

from .session_registry import (
    SessionRecord,
    application_support_root,
    consume_session_events,
    load_session_registry,
)

MAX_BOOTSTRAP_BYTES = 64 * 1024 * 1024
CHUNK_SIZE = 64 * 1024
ACTIVE_SESSION_LEASE_SECONDS = 6 * 60 * 60
UNCONFIRMED_ACTIVE_SESSION_LEASE_SECONDS = 30 * 60
ACTIVE_EVENT_TYPES = {"task_started", "turn_started"}
IDLE_EVENT_TYPES = {"task_complete", "turn_complete", "turn_aborted"}


@dataclass(frozen=True)
class DetectedCodexSession:
    id: str
    workspace_name: str
    model: str
    effort: str
    thread_name: str | None = None
    last_active_at: str | None = None
    is_currently_producing: bool = False
    is_modeldial_scan: bool = False


@dataclass(frozen=True)
class DetectedCodexModel:
    model: str | None
    effort: str | None
    detected_at: str
    status: str = "recent"
    active_session_count: int = 0
    distinct_active_models: tuple[tuple[str, str], ...] = ()
    active_sessions: tuple[DetectedCodexSession, ...] = ()
    display_sessions: tuple[DetectedCodexSession, ...] = ()


@dataclass
class _SessionState:
    offset: int = 0
    model: str | None = None
    effort: str | None = None
    settings_at: str | None = None
    activity_at: str | None = None
    active: bool = False
    inode: int | None = None
    modified_at: float | None = None
    session_id: str | None = None
    cwd: str | None = None
    is_modeldial_scan: bool = False

    @classmethod
    def from_dict(cls, payload: object) -> _SessionState:
        if not isinstance(payload, dict):
            return cls()
        cwd = _optional_text(payload.get("cwd"))
        return cls(
            offset=max(0, int(payload.get("offset") or 0)),
            model=_optional_text(payload.get("model")),
            effort=_optional_text(payload.get("effort")),
            settings_at=_optional_text(payload.get("settings_at")),
            activity_at=_optional_text(payload.get("activity_at")),
            active=bool(payload.get("active", False)),
            inode=int(payload["inode"]) if payload.get("inode") is not None else None,
            modified_at=(
                float(payload["modified_at"])
                if payload.get("modified_at") is not None
                else None
            ),
            session_id=_optional_text(payload.get("session_id")),
            cwd=cwd,
            is_modeldial_scan=bool(payload.get("is_modeldial_scan", False))
            or _is_modeldial_scan_workspace(cwd),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "offset": self.offset,
            "model": self.model,
            "effort": self.effort,
            "settings_at": self.settings_at,
            "activity_at": self.activity_at,
            "active": self.active,
            "inode": self.inode,
            "modified_at": self.modified_at,
            "session_id": self.session_id,
            "cwd": self.cwd,
            "is_modeldial_scan": self.is_modeldial_scan,
        }


def detect_codex_current_model(
    sessions_root: Path | None = None,
    *,
    cache_path: Path | None = None,
    event_inbox_path: Path | None = None,
    registry_path: Path | None = None,
    persist_cache: bool = True,
    consume_registry_events: bool = True,
) -> DetectedCodexModel | None:
    root = sessions_root or (Path.home() / ".codex" / "sessions")
    tracker_path = cache_path or application_support_root() / "codex_session_tracker.json"
    states = _load_cache(tracker_path)
    current_paths = _session_files(root)
    current_keys = {str(path) for path in current_paths}
    states = {key: value for key, value in states.items() if key in current_keys}

    for path in current_paths:
        key = str(path)
        state = states.get(key)
        try:
            stat = path.stat()
        except OSError:
            continue
        if (
            state is None
            or state.offset > stat.st_size
            or (state.inode is not None and state.inode != stat.st_ino)
            or not state.session_id
            or not state.cwd
        ):
            state = _bootstrap_session(path)
        else:
            _read_appended_events(path, state, stat.st_size)
        state.inode = stat.st_ino
        state.modified_at = stat.st_mtime
        states[key] = state

    if persist_cache:
        _save_cache(tracker_path, states)
    rollout_states = [
        state
        for state in states.values()
        if state.model and state.effort and state.model != "codex-auto-review"
    ]
    should_load_registry = (
        sessions_root is None
        or event_inbox_path is not None
        or registry_path is not None
    )
    if should_load_registry and consume_registry_events:
        registry = consume_session_events(
            inbox_path=event_inbox_path,
            registry_path=registry_path,
        )
    elif should_load_registry:
        registry = load_session_registry(registry_path)
    else:
        registry = {}
    now = time.time()
    rollout_session_ids = {
        state.session_id for state in states.values() if state.session_id
    }
    confirmed_active_session_ids = {
        record.id
        for record in registry.values()
        if record.source == "codex"
        and record.id in rollout_session_ids
        and record.is_effectively_active(now=now)
    }
    supported = [
        state
        for state in _merge_session_records(rollout_states, registry.values())
        if state.session_id in rollout_session_ids
        or state.is_modeldial_scan
    ]
    if not supported:
        return None

    display_active = [
        state
        for state in supported
        if _is_effectively_active(state, now, confirmed_active_session_ids)
    ]
    display_active.sort(
        key=lambda state: _timestamp_value(state.activity_at or state.settings_at or ""),
        reverse=True,
    )
    thread_names = _session_thread_names(
        root.parent / "session_index.jsonl",
        {state.session_id for state in display_active if state.session_id},
    )
    display_sessions = tuple(
        _detected_session(state, thread_names.get(state.session_id or ""))
        for state in display_active
    )
    comparison_states = [state for state in supported if not state.is_modeldial_scan]
    active = [
        state
        for state in comparison_states
        if _is_effectively_active(state, now, confirmed_active_session_ids)
    ]
    if active:
        active.sort(
            key=lambda state: _timestamp_value(state.activity_at or state.settings_at or ""),
            reverse=True,
        )
        combinations = tuple(sorted({(state.model or "", state.effort or "") for state in active}))
        active_sessions = tuple(
            _detected_session(state, thread_names.get(state.session_id or ""))
            for state in active
        )
        detected_at = max(
            (state.activity_at or state.settings_at or "" for state in active),
            key=_timestamp_value,
        )
        if len(combinations) == 1:
            model, effort = combinations[0]
            return DetectedCodexModel(
                model=model,
                effort=effort,
                detected_at=detected_at,
                status="active_single",
                active_session_count=len(active),
                distinct_active_models=combinations,
                active_sessions=active_sessions,
                display_sessions=display_sessions,
            )
        return DetectedCodexModel(
            model=None,
            effort=None,
            detected_at=detected_at,
            status="active_mixed",
            active_session_count=len(active),
            distinct_active_models=combinations,
            active_sessions=active_sessions,
            display_sessions=display_sessions,
        )

    if not comparison_states:
        if not display_active:
            return None
        latest_scan = display_active[0]
        return DetectedCodexModel(
            model=None,
            effort=None,
            detected_at=latest_scan.activity_at or latest_scan.settings_at or "",
            status="scan_only",
            display_sessions=display_sessions,
        )

    latest = max(
        comparison_states,
        key=lambda state: _timestamp_value(state.activity_at or state.settings_at or ""),
    )
    return DetectedCodexModel(
        model=latest.model,
        effort=latest.effort,
        detected_at=latest.activity_at or latest.settings_at or "",
        status="recent",
        display_sessions=display_sessions,
    )


def _session_files(root: Path) -> list[Path]:
    try:
        if not root.is_dir():
            return []
        files = [path for path in root.rglob("rollout-*.jsonl") if path.is_file()]
    except OSError:
        return []
    files.sort(key=str)
    return files


def _merge_session_records(
    rollout_states: list[_SessionState],
    records: Iterable[SessionRecord],
) -> list[_SessionState]:
    states_by_id: dict[str, _SessionState] = {}
    anonymous_states: list[_SessionState] = []
    for state in rollout_states:
        if not state.session_id:
            anonymous_states.append(state)
            continue
        existing = states_by_id.get(state.session_id)
        if existing is None or _state_timestamp(state) >= _state_timestamp(existing):
            states_by_id[state.session_id] = state

    for record in records:
        if not isinstance(record, SessionRecord) or record.source != "codex":
            continue
        existing = states_by_id.get(record.id)
        record_timestamp = _timestamp_value(record.updated_at)
        record_is_modeldial_scan = record.is_modeldial_scan or _is_modeldial_scan_workspace(
            record.workspace_path
        )
        if existing is not None and _state_timestamp(existing) > record_timestamp:
            existing.is_modeldial_scan = (
                existing.is_modeldial_scan or record_is_modeldial_scan
            )
            continue
        model = record.model or (existing.model if existing else None)
        effort = record.effort or (existing.effort if existing else None)
        states_by_id[record.id] = _SessionState(
            offset=existing.offset if existing else 0,
            model=model,
            effort=effort,
            settings_at=(existing.settings_at if existing else None)
            or (record.updated_at if model and effort else None),
            activity_at=record.updated_at,
            active=record.is_active,
            inode=existing.inode if existing else None,
            modified_at=record_timestamp,
            session_id=record.id,
            cwd=record.workspace_path or (existing.cwd if existing else None),
            is_modeldial_scan=record_is_modeldial_scan
            or (existing.is_modeldial_scan if existing else False),
        )
    return [*states_by_id.values(), *anonymous_states]


def _state_timestamp(state: _SessionState) -> float:
    return _timestamp_value(state.activity_at or state.settings_at or "")


def _bootstrap_session(path: Path) -> _SessionState:
    state = _SessionState()
    found_settings = False
    found_lifecycle = False
    found_workspace = False
    for raw_line in _reverse_lines(path):
        event = _decode_event(raw_line)
        if event is None:
            continue
        _apply_session_identity(state, event)
        found_workspace = bool(state.cwd)
        timestamp = _optional_text(event.get("timestamp"))
        payload = event.get("payload")
        event_type = _optional_text(payload.get("type")) if isinstance(payload, dict) else None
        if not found_lifecycle and event_type in ACTIVE_EVENT_TYPES | IDLE_EVENT_TYPES:
            state.active = event_type in ACTIVE_EVENT_TYPES
            state.activity_at = timestamp
            found_lifecycle = True
        if not found_settings:
            settings = _model_settings(event)
            if settings is not None:
                state.model, state.effort = settings
                state.settings_at = timestamp
                found_settings = True
        if found_settings and found_lifecycle and found_workspace:
            break
    state.session_id = state.session_id or _session_id_from_path(path)
    state.offset = _last_complete_line_offset(path)
    return state


def _last_complete_line_offset(path: Path) -> int:
    try:
        with path.open("rb") as handle:
            size = handle.seek(0, 2)
            if size == 0:
                return 0
            handle.seek(size - 1)
            if handle.read(1) == b"\n":
                return size
            position = size
            while position > 0:
                block_size = min(CHUNK_SIZE, position)
                position -= block_size
                handle.seek(position)
                block = handle.read(block_size)
                newline = block.rfind(b"\n")
                if newline >= 0:
                    return position + newline + 1
    except OSError:
        return 0
    return 0


def _read_appended_events(path: Path, state: _SessionState, file_size: int) -> None:
    if file_size <= state.offset:
        return
    try:
        with path.open("rb") as handle:
            handle.seek(state.offset)
            data = handle.read(file_size - state.offset)
    except OSError:
        return
    last_newline = data.rfind(b"\n")
    if last_newline < 0:
        return
    complete = data[:last_newline]
    for raw_line in complete.split(b"\n"):
        event = _decode_event(raw_line)
        if event is not None:
            _apply_event(state, event)
    state.offset += last_newline + 1


def _apply_event(state: _SessionState, event: dict[str, object]) -> None:
    _apply_session_identity(state, event)
    timestamp = _optional_text(event.get("timestamp"))
    settings = _model_settings(event)
    if settings is not None:
        state.model, state.effort = settings
        state.settings_at = timestamp
    payload = event.get("payload")
    event_type = _optional_text(payload.get("type")) if isinstance(payload, dict) else None
    if event_type in ACTIVE_EVENT_TYPES:
        state.active = True
        state.activity_at = timestamp
    elif event_type in IDLE_EVENT_TYPES:
        state.active = False
        state.activity_at = timestamp


def _model_settings(event: dict[str, object]) -> tuple[str, str] | None:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    event_type = payload.get("type")
    if event.get("type") == "event_msg" and event_type == "thread_settings_applied":
        settings = payload.get("thread_settings")
        if not isinstance(settings, dict):
            return None
        provider = _optional_text(settings.get("model_provider_id"))
        if provider and provider != "OpenAI":
            return None
        model = _optional_text(settings.get("model"))
        effort = _optional_text(settings.get("reasoning_effort"))
    elif event.get("type") == "turn_context":
        model = _optional_text(payload.get("model"))
        effort = _optional_text(payload.get("effort") or payload.get("reasoning_effort"))
    else:
        return None
    if not model or not effort or model == "codex-auto-review":
        return None
    return model, effort.lower()


def _apply_session_identity(state: _SessionState, event: dict[str, object]) -> None:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return
    if event.get("type") == "session_meta":
        state.session_id = (
            _optional_text(payload.get("session_id"))
            or _optional_text(payload.get("id"))
            or state.session_id
        )
    if event.get("type") in {"session_meta", "turn_context"}:
        state.cwd = _optional_text(payload.get("cwd")) or state.cwd
        state.is_modeldial_scan = state.is_modeldial_scan or _is_modeldial_scan_workspace(
            state.cwd
        )


def _session_id_from_path(path: Path) -> str:
    stem = path.stem
    candidate = stem[-36:]
    return candidate if len(candidate) == 36 else stem


def _session_thread_names(
    index_path: Path,
    session_ids: set[str],
) -> dict[str, str]:
    if not session_ids:
        return {}
    names: dict[str, str] = {}
    seen: set[str] = set()
    for raw_line in _reverse_lines(index_path):
        record = _decode_event(raw_line)
        if record is None:
            continue
        session_id = _optional_text(record.get("id"))
        if not session_id or session_id not in session_ids or session_id in seen:
            continue
        seen.add(session_id)
        thread_name = _optional_text(record.get("thread_name"))
        if thread_name:
            names[session_id] = thread_name
        if seen == session_ids:
            break
    return names


def _detected_session(
    state: _SessionState,
    thread_name: str | None = None,
) -> DetectedCodexSession:
    workspace_name = Path(state.cwd).name if state.cwd else "Codex 会话"
    return DetectedCodexSession(
        id=state.session_id or "unknown",
        workspace_name=workspace_name or "Codex 会话",
        model=state.model or "",
        effort=state.effort or "",
        thread_name=thread_name,
        last_active_at=state.activity_at,
        is_currently_producing=state.active,
        is_modeldial_scan=state.is_modeldial_scan,
    )


def _reverse_lines(path: Path) -> Iterator[str]:
    try:
        with path.open("rb") as handle:
            position = handle.seek(0, 2)
            scanned = 0
            remainder = b""
            while position > 0 and scanned < MAX_BOOTSTRAP_BYTES:
                size = min(CHUNK_SIZE, position, MAX_BOOTSTRAP_BYTES - scanned)
                position -= size
                handle.seek(position)
                block = handle.read(size)
                scanned += size
                lines = (block + remainder).split(b"\n")
                remainder = lines[0]
                for line in reversed(lines[1:]):
                    if line:
                        yield line.decode("utf-8")
            if position == 0 and remainder:
                yield remainder.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return


def _decode_event(raw_line: str | bytes) -> dict[str, object] | None:
    try:
        event = json.loads(raw_line)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return event if isinstance(event, dict) else None


def _load_cache(path: Path) -> dict[str, _SessionState]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    sessions = payload.get("sessions") if isinstance(payload, dict) else None
    if not isinstance(sessions, dict):
        return {}
    return {str(key): _SessionState.from_dict(value) for key, value in sessions.items()}


def _save_cache(path: Path, states: dict[str, _SessionState]) -> None:
    payload = {
        "version": 1,
        "sessions": {key: state.to_dict() for key, state in states.items()},
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    try:
        if path.read_text(encoding="utf-8") == serialized:
            return
    except OSError:
        pass
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temp_path.write_text(serialized, encoding="utf-8")
        temp_path.replace(path)
    except OSError:
        return


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _is_modeldial_scan_workspace(value: str | None) -> bool:
    normalized = (value or "").replace("\\", "/").lower()
    return any(
        marker in normalized
        for marker in (
            "/modeldial.app/contents/resources/backend",
            "/modelpilot.app/contents/resources/backend",
            "/modeldial-evaluation-",
        )
    )


def _is_effectively_active(
    state: _SessionState,
    now: float,
    confirmed_active_session_ids: set[str],
) -> bool:
    if not state.active or state.modified_at is None:
        return False
    lease_seconds = (
        ACTIVE_SESSION_LEASE_SECONDS
        if state.session_id in confirmed_active_session_ids
        else UNCONFIRMED_ACTIVE_SESSION_LEASE_SECONDS
    )
    return now - state.modified_at <= lease_seconds


def _timestamp_value(value: str) -> float:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0
