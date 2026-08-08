#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4


SUPPORTED_SOURCES = {"claude", "codex", "grok", "opencode"}
MAX_FIELD_LENGTH = 8_192
MODELDIAL_SCAN_EFFORT_ENV = "MODELDIAL_SCAN_EFFORT"
MODELDIAL_SCAN_SESSION_ENV = "MODELDIAL_SCAN_SESSION"


def application_support_root() -> Path:
    override = os.environ.get("MODELDIAL_DATA_DIR") or os.environ.get(
        "MODEL_PILOT_DATA_DIR"
    )
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "modeldial"
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "modeldial"
    return Path.home() / ".local" / "share" / "modeldial"


def default_event_inbox_path() -> Path:
    return application_support_root() / "session-events" / "inbox"


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    return value[:MAX_FIELD_LENGTH]


def _observed_at() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00",
        "Z",
    )


def sanitize_hook_payload(
    source: str,
    payload: object,
    *,
    observed_at: str | None = None,
) -> dict[str, object] | None:
    source = source.strip().lower()
    if source not in SUPPORTED_SOURCES or not isinstance(payload, dict):
        return None
    session_id = _optional_text(
        payload.get("session_id")
        or payload.get("sessionId")
        or payload.get("sessionID")
    )
    event_name = _optional_text(
        payload.get("hook_event_name") or payload.get("hookEventName")
    )
    if not session_id or not event_name:
        return None
    return {
        "version": 1,
        "event_id": uuid4().hex,
        "source": source,
        "session_id": session_id,
        "hook_event_name": event_name,
        "observed_at": observed_at or _observed_at(),
        "turn_id": _optional_text(payload.get("turn_id") or payload.get("turnId")),
        "cwd": _optional_text(payload.get("cwd")),
        "model": _optional_text(payload.get("model")),
        "effort": _optional_text(
            payload.get("reasoning_effort")
            or payload.get("effort")
            or os.environ.get(MODELDIAL_SCAN_EFFORT_ENV)
        ),
        "transcript_path": _optional_text(
            payload.get("transcript_path") or payload.get("transcriptPath")
        ),
        "start_source": _optional_text(payload.get("source")),
        "is_modeldial_scan": os.environ.get(MODELDIAL_SCAN_SESSION_ENV) == "1",
    }


def record_hook_payload(
    source: str,
    payload: object,
    *,
    inbox_path: Path | None = None,
    observed_at: str | None = None,
) -> Path | None:
    event = sanitize_hook_payload(source, payload, observed_at=observed_at)
    if event is None:
        return None
    inbox = inbox_path or default_event_inbox_path()
    try:
        inbox.mkdir(parents=True, exist_ok=True, mode=0o700)
        filename = f"{time.time_ns():020d}-{os.getpid()}-{event['event_id']}.json"
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


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--source", required=True)
    parser.add_argument("--inbox")
    try:
        args = parser.parse_args()
        payload = json.load(sys.stdin)
        record_hook_payload(
            args.source,
            payload,
            inbox_path=Path(args.inbox).expanduser() if args.inbox else None,
        )
    except (OSError, ValueError, json.JSONDecodeError):
        pass


if __name__ == "__main__":
    main()
