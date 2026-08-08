from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import time
from uuid import uuid4

from .process_lock import exclusive_process_lock


ACTIVE_STATUSES = {"running", "waiting"}
ACTIVE_EVENT_STATUSES = {
    "PermissionRequest": "waiting",
    "PreToolUse": "running",
    "UserPromptSubmit": "running",
}
IDLE_EVENT_NAMES = {"SessionStart", "Stop", "StopFailure"}
MODELDIAL_PROCESS_END_EVENT = "ModelDialProcessEnd"
MODELDIAL_WORKSPACE_RELEASED_EVENT = "ModelDialWorkspaceReleased"
MODELDIAL_EVALUATION_WORKSPACE_PREFIX = "modeldial-evaluation-"
ENDED_EVENT_NAMES = {"SessionEnd", MODELDIAL_PROCESS_END_EVENT}
ACTIVE_LEASE_SECONDS = 6 * 60 * 60
DATA_DIR_ENV = "MODELDIAL_DATA_DIR"
LEGACY_DATA_DIR_ENV = "MODEL_PILOT_DATA_DIR"


def application_support_root() -> Path:
    override = os.environ.get(DATA_DIR_ENV) or os.environ.get(LEGACY_DATA_DIR_ENV)
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        current = Path.home() / "Library" / "Application Support" / "modeldial"
        legacy = Path.home() / "Library" / "Application Support" / "ModelPilot"
    elif os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            current = Path(local_app_data) / "modeldial"
            legacy = Path(local_app_data) / "ModelPilot"
        else:
            current = Path.home() / ".local" / "share" / "modeldial"
            legacy = Path.home() / ".local" / "share" / "modelpilot"
    else:
        current = Path.home() / ".local" / "share" / "modeldial"
        legacy = Path.home() / ".local" / "share" / "modelpilot"
    return _migrate_legacy_application_support(current, legacy)


def _migrate_legacy_application_support(current: Path, legacy: Path) -> Path:
    if current == legacy or current.exists() or not legacy.is_dir():
        return current
    try:
        current.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(legacy, current)
    except FileExistsError:
        return current
    except OSError:
        return legacy
    return current


def default_event_inbox_path() -> Path:
    return application_support_root() / "session-events" / "inbox"


def default_registry_path() -> Path:
    return application_support_root() / "session-registry.json"


def _is_modeldial_evaluation_workspace(value: str | None) -> bool:
    normalized = (value or "").replace("\\", "/").rstrip("/")
    return normalized.rsplit("/", 1)[-1].startswith(
        MODELDIAL_EVALUATION_WORKSPACE_PREFIX
    )


@dataclass(frozen=True)
class SessionRecord:
    id: str
    source: str
    status: str
    updated_at: str
    workspace_path: str | None = None
    model: str | None = None
    effort: str | None = None
    thread_name: str | None = None
    turn_id: str | None = None
    transcript_path: str | None = None
    last_event: str | None = None
    is_modeldial_scan: bool = False

    @property
    def key(self) -> str:
        return f"{self.source}:{self.id}"

    @property
    def workspace_name(self) -> str:
        if self.workspace_path:
            name = Path(self.workspace_path).name
            if name:
                return name
        return {
            "claude": "Claude Code",
            "codex": "Codex",
            "grok": "Grok Build",
            "opencode": "OpenCode",
        }.get(self.source, "模型会话")

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    def is_effectively_active(
        self,
        *,
        now: float | None = None,
        lease_seconds: float = ACTIVE_LEASE_SECONDS,
    ) -> bool:
        if not self.is_active:
            return False
        timestamp = _timestamp_value(self.updated_at)
        if timestamp <= 0:
            return False
        return (now if now is not None else time.time()) - timestamp <= lease_seconds

    @classmethod
    def from_dict(cls, payload: object) -> SessionRecord | None:
        if not isinstance(payload, dict):
            return None
        session_id = _optional_text(payload.get("id"))
        source = _optional_text(payload.get("source"))
        status = _optional_text(payload.get("status"))
        updated_at = _optional_text(payload.get("updated_at"))
        if not session_id or not source or not status or not updated_at:
            return None
        workspace_path = _optional_text(payload.get("workspace_path"))
        return cls(
            id=session_id,
            source=source,
            status=status,
            updated_at=updated_at,
            workspace_path=workspace_path,
            model=_optional_text(payload.get("model")),
            effort=_optional_text(payload.get("effort")),
            thread_name=_optional_text(payload.get("thread_name")),
            turn_id=_optional_text(payload.get("turn_id")),
            transcript_path=_optional_text(payload.get("transcript_path")),
            last_event=_optional_text(payload.get("last_event")),
            is_modeldial_scan=bool(payload.get("is_modeldial_scan", False))
            or _is_modeldial_evaluation_workspace(workspace_path),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "source": self.source,
            "status": self.status,
            "updated_at": self.updated_at,
            "workspace_path": self.workspace_path,
            "model": self.model,
            "effort": self.effort,
            "thread_name": self.thread_name,
            "turn_id": self.turn_id,
            "transcript_path": self.transcript_path,
            "last_event": self.last_event,
            "is_modeldial_scan": self.is_modeldial_scan,
        }


def load_session_registry(path: Path | None = None) -> dict[str, SessionRecord]:
    registry_path = path or default_registry_path()
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw_records = payload.get("sessions") if isinstance(payload, dict) else None
    if not isinstance(raw_records, dict):
        return {}
    records: dict[str, SessionRecord] = {}
    for key, raw_record in raw_records.items():
        record = SessionRecord.from_dict(raw_record)
        if record is not None:
            records[str(key)] = record
    return records


def consume_session_events(
    *,
    inbox_path: Path | None = None,
    registry_path: Path | None = None,
) -> dict[str, SessionRecord]:
    inbox = inbox_path or default_event_inbox_path()
    destination = registry_path or default_registry_path()
    lock_path = destination.with_name(f".{destination.name}.lock")
    with exclusive_process_lock(lock_path) as acquired:
        if not acquired:
            return load_session_registry(destination)
        return _consume_session_events_unlocked(inbox, destination)


def _consume_session_events_unlocked(
    inbox: Path,
    destination: Path,
) -> dict[str, SessionRecord]:
    records = load_session_registry(destination)
    try:
        event_paths = sorted(inbox.glob("*.json")) if inbox.is_dir() else []
    except OSError:
        event_paths = []
    changed = False
    for event_path in event_paths:
        event = _load_event(event_path)
        if event is None:
            continue
        key = f"{event['source']}:{event['session_id']}"
        updated = _apply_event(records.get(key), event)
        if updated is not None and updated != records.get(key):
            records[key] = updated
            changed = True

    if _reconcile_modeldial_evaluation_workspaces(records):
        changed = True

    if changed and not _save_registry(destination, records):
        return load_session_registry(destination)
    if changed or event_paths:
        for event_path in event_paths:
            try:
                event_path.unlink()
            except OSError:
                pass
    return records


def record_modeldial_session_end(
    session_id: str,
    *,
    inbox_path: Path | None = None,
    observed_at: str | None = None,
) -> Path | None:
    normalized_session_id = _optional_text(session_id)
    if not normalized_session_id:
        return None
    event_id = uuid4().hex
    event = {
        "version": 1,
        "event_id": event_id,
        "source": "codex",
        "session_id": normalized_session_id,
        "hook_event_name": MODELDIAL_PROCESS_END_EVENT,
        "observed_at": observed_at or _observed_at(),
        "is_modeldial_scan": True,
    }
    inbox = inbox_path or default_event_inbox_path()
    try:
        inbox.mkdir(parents=True, exist_ok=True, mode=0o700)
        filename = f"{time.time_ns():020d}-{os.getpid()}-{event_id}.json"
        destination = inbox / filename
        temporary = inbox / f".{filename}.tmp"
        temporary.write_text(
            json.dumps(event, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(destination)
        return destination
    except OSError:
        return None


def _reconcile_modeldial_evaluation_workspaces(
    records: dict[str, SessionRecord],
) -> bool:
    changed = False
    for key, current in tuple(records.items()):
        workspace_path = current.workspace_path
        if not _is_modeldial_evaluation_workspace(workspace_path):
            continue
        if current.is_active and workspace_path and not Path(workspace_path).is_dir():
            records[key] = replace(
                current,
                status="ended",
                updated_at=_observed_at(),
                last_event=MODELDIAL_WORKSPACE_RELEASED_EVENT,
                is_modeldial_scan=True,
            )
            changed = True
        elif not current.is_modeldial_scan:
            records[key] = replace(current, is_modeldial_scan=True)
            changed = True
    return changed


def active_session_records(
    source: str,
    *,
    inbox_path: Path | None = None,
    registry_path: Path | None = None,
    now: float | None = None,
) -> tuple[SessionRecord, ...]:
    records = consume_session_events(
        inbox_path=inbox_path,
        registry_path=registry_path,
    )
    active = [
        record
        for record in records.values()
        if record.source == source and record.is_effectively_active(now=now)
    ]
    active.sort(key=lambda record: _timestamp_value(record.updated_at), reverse=True)
    return tuple(active)


def _load_event(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    required = ("source", "session_id", "hook_event_name", "observed_at")
    if any(not _optional_text(payload.get(field)) for field in required):
        return None
    return payload


def _apply_event(
    current: SessionRecord | None,
    event: dict[str, object],
) -> SessionRecord | None:
    source = _optional_text(event.get("source"))
    session_id = _optional_text(event.get("session_id"))
    event_name = _optional_text(event.get("hook_event_name"))
    observed_at = _optional_text(event.get("observed_at"))
    if not source or not session_id or not event_name or not observed_at:
        return current
    if current is not None and _timestamp_value(observed_at) < _timestamp_value(current.updated_at):
        return current
    if event_name in ENDED_EVENT_NAMES:
        status = "ended"
    elif event_name in IDLE_EVENT_NAMES:
        status = "idle"
    else:
        status = ACTIVE_EVENT_STATUSES.get(event_name, current.status if current else "idle")
    workspace_path = _optional_text(event.get("cwd")) or (
        current.workspace_path if current else None
    )
    return SessionRecord(
        id=session_id,
        source=source,
        status=status,
        updated_at=observed_at,
        workspace_path=workspace_path,
        model=_optional_text(event.get("model")) or (current.model if current else None),
        effort=(
            (_optional_text(event.get("effort")) or "").lower() or None
        )
        or (current.effort if current else None),
        thread_name=current.thread_name if current else None,
        turn_id=_optional_text(event.get("turn_id")) or (current.turn_id if current else None),
        transcript_path=_optional_text(event.get("transcript_path"))
        or (current.transcript_path if current else None),
        last_event=event_name,
        is_modeldial_scan=bool(event.get("is_modeldial_scan"))
        or _is_modeldial_evaluation_workspace(workspace_path)
        or (current.is_modeldial_scan if current else False),
    )


def _save_registry(path: Path, records: dict[str, SessionRecord]) -> bool:
    payload = {
        "version": 1,
        "sessions": {key: record.to_dict() for key, record in sorted(records.items())},
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(path)
    except OSError:
        return False
    return True


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _observed_at() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00",
        "Z",
    )


def _timestamp_value(value: str) -> float:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0
