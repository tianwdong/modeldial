from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shlex
import subprocess
from typing import Callable, Optional
from urllib.parse import unquote

from .process_environment import build_child_environment
from .session_registry import (
    SessionRecord,
    consume_session_events,
    load_session_registry,
)


@dataclass(frozen=True)
class DetectedModelSession:
    id: str
    source: str
    workspace_name: str
    model: str | None = None
    effort: str | None = None
    thread_name: str | None = None
    last_active_at: str | None = None
    is_currently_producing: bool = False
    is_evaluation_session: bool = False


CommandRunner = Callable[[tuple[str, ...]], Optional[str]]
ProcessChecker = Callable[[int], bool]


def _command_output(command: tuple[str, ...]) -> str | None:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=0.6,
            env=build_child_environment(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


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


def _option_value(command: str, option: str) -> str | None:
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    for index, token in enumerate(tokens):
        if token == option and index + 1 < len(tokens):
            return tokens[index + 1]
        prefix = f"{option}="
        if token.startswith(prefix):
            return token[len(prefix) :]
    return None


def _is_claude_command(command: str) -> bool:
    try:
        first_token = shlex.split(command)[0]
    except (ValueError, IndexError):
        first_token = command.split(maxsplit=1)[0] if command.strip() else ""
    lowered = first_token.lower()
    return lowered == "claude" or lowered.endswith("/claude")


def _lsof_paths(output: str | None) -> list[Path]:
    if not output:
        return []
    paths: list[Path] = []
    for line in output.splitlines():
        if line.startswith("n") and len(line) > 1:
            paths.append(Path(line[1:]))
    return paths


def _claude_transcript_metadata(path: Path) -> tuple[str | None, str | None, str | None]:
    try:
        with path.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            handle.seek(max(0, size - 1_048_576))
            if size > 1_048_576:
                handle.readline()
            lines = handle.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return None, None, None

    session_id: str | None = None
    workspace: str | None = None
    model: str | None = None
    for line in lines:
        try:
            payload = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        raw_session_id = payload.get("sessionId")
        raw_workspace = payload.get("cwd")
        if isinstance(raw_session_id, str) and raw_session_id:
            session_id = raw_session_id
        if isinstance(raw_workspace, str) and raw_workspace:
            workspace = raw_workspace
        message = payload.get("message")
        if isinstance(message, dict):
            raw_model = message.get("model")
            if isinstance(raw_model, str) and raw_model:
                model = raw_model
    return session_id, workspace, model


def detect_claude_active_sessions(
    *,
    home: Path | None = None,
    command_runner: CommandRunner = _command_output,
    event_inbox_path: Path | None = None,
    registry_path: Path | None = None,
    consume_registry_events: bool = True,
) -> tuple[DetectedModelSession, ...]:
    should_load_registry = (
        home is None or event_inbox_path is not None or registry_path is not None
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
    claude_records = {
        record.id: record
        for record in registry.values()
        if record.source == "claude"
    }
    sessions_by_id = {
        record.id: _session_from_record(record)
        for record in sorted(
            claude_records.values(),
            key=lambda item: item.updated_at,
            reverse=True,
        )
        if record.is_effectively_active()
    }
    process_output = command_runner(("/bin/ps", "-Ao", "pid=,command="))
    if not process_output:
        return tuple(sessions_by_id.values())

    seen_process_ids: set[str] = set()
    for line in process_output.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2 or not parts[0].isdigit() or not _is_claude_command(parts[1]):
            continue
        pid, command = parts
        cwd_paths = _lsof_paths(
            command_runner(("/usr/sbin/lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"))
        )
        cwd = str(cwd_paths[-1]) if cwd_paths else None
        if cwd and "/.claude/worktrees/agent-" in cwd:
            continue

        transcript_paths = [
            path
            for path in _lsof_paths(command_runner(("/usr/sbin/lsof", "-p", pid, "-Fn")))
            if "/.claude/projects/" in str(path)
            and str(path).endswith(".jsonl")
            and "/subagents/" not in str(path)
        ]
        transcript = transcript_paths[0] if transcript_paths else None
        session_id: str | None = None
        transcript_workspace: str | None = None
        transcript_model: str | None = None
        if transcript is not None:
            session_id, transcript_workspace, transcript_model = _claude_transcript_metadata(
                transcript
            )

        workspace_path = cwd or transcript_workspace
        if not workspace_path and not session_id:
            continue
        stable_id = session_id or f"claude:{pid}"
        if stable_id in seen_process_ids:
            continue
        seen_process_ids.add(stable_id)
        workspace_name = Path(workspace_path).name if workspace_path else "Claude Code"
        process_session = DetectedModelSession(
            id=stable_id,
            source="claude",
            workspace_name=workspace_name or "Claude Code",
            model=_option_value(command, "--model") or transcript_model,
            effort=_option_value(command, "--effort"),
        )
        hook_record = claude_records.get(stable_id)
        if hook_record is not None and not hook_record.is_active:
            continue
        hook_session = sessions_by_id.get(stable_id)
        sessions_by_id[stable_id] = (
            _merge_detected_sessions(hook_session, process_session)
            if hook_session is not None
            else process_session
        )
    return tuple(sessions_by_id.values())


def _session_from_record(record: SessionRecord) -> DetectedModelSession:
    return DetectedModelSession(
        id=record.id,
        source=record.source,
        workspace_name=record.workspace_name,
        model=record.model,
        effort=record.effort,
        thread_name=record.thread_name,
        last_active_at=record.updated_at,
        is_currently_producing=record.is_active,
    )


def _merge_detected_sessions(
    primary: DetectedModelSession,
    fallback: DetectedModelSession,
) -> DetectedModelSession:
    return DetectedModelSession(
        id=primary.id,
        source=primary.source,
        workspace_name=(
            primary.workspace_name
            if primary.workspace_name not in {"Claude Code", "模型会话"}
            else fallback.workspace_name
        ),
        model=primary.model or fallback.model,
        effort=primary.effort or fallback.effort,
        thread_name=primary.thread_name or fallback.thread_name,
        last_active_at=primary.last_active_at or fallback.last_active_at,
        is_currently_producing=(
            primary.is_currently_producing or fallback.is_currently_producing
        ),
    )


def _grok_summary_path(sessions_root: Path, session_id: str) -> Path | None:
    try:
        return next(sessions_root.glob(f"*/{session_id}/summary.json"), None)
    except OSError:
        return None


def detect_grok_active_sessions(
    *,
    home: Path | None = None,
    process_is_alive: ProcessChecker = _process_is_alive,
) -> tuple[DetectedModelSession, ...]:
    home = home or Path.home()
    grok_root = home / ".grok"
    try:
        payload = json.loads((grok_root / "active_sessions.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    if isinstance(payload, dict):
        entries = payload.get("sessions", [])
    else:
        entries = payload
    if not isinstance(entries, list):
        return ()

    sessions: list[DetectedModelSession] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        session_id = str(
            entry.get("session_id") or entry.get("sessionId") or entry.get("id") or ""
        ).strip()
        if not session_id:
            continue
        try:
            pid = int(entry.get("pid", 0))
        except (TypeError, ValueError):
            pid = 0
        if pid > 0 and not process_is_alive(pid):
            continue

        summary_path = _grok_summary_path(grok_root / "sessions", session_id)
        summary: dict[str, object] = {}
        if summary_path is not None:
            try:
                decoded = json.loads(summary_path.read_text(encoding="utf-8"))
                if isinstance(decoded, dict):
                    summary = decoded
            except (OSError, json.JSONDecodeError):
                pass
        encoded_workspace = summary_path.parent.parent.name if summary_path else ""
        workspace_path = unquote(encoded_workspace)
        workspace_name = Path(workspace_path).name if workspace_path else "Grok Build"
        sessions.append(
            DetectedModelSession(
                id=session_id,
                source="grok",
                workspace_name=workspace_name or "Grok Build",
                model=_optional_text(summary.get("current_model_id") or entry.get("model")),
                effort=_optional_text(summary.get("reasoning_effort") or entry.get("effort")),
                thread_name=_optional_text(summary.get("generated_title")),
                last_active_at=_optional_text(
                    entry.get("updated_at") or entry.get("last_active_at")
                ),
            )
        )
    return tuple(sessions)


def detect_registered_active_sessions(
    *,
    inbox_path: Path | None = None,
    registry_path: Path | None = None,
    consume_registry_events: bool = True,
) -> tuple[DetectedModelSession, ...]:
    records = (
        consume_session_events(
            inbox_path=inbox_path,
            registry_path=registry_path,
        )
        if consume_registry_events
        else load_session_registry(registry_path)
    )
    return tuple(
        _session_from_record(record)
        for record in sorted(
            records.values(),
            key=lambda item: item.updated_at,
            reverse=True,
        )
        if record.source not in {"codex", "claude"}
        and record.is_effectively_active()
    )


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def detect_external_model_sessions(
    *,
    event_inbox_path: Path | None = None,
    registry_path: Path | None = None,
    consume_registry_events: bool = True,
) -> tuple[DetectedModelSession, ...]:
    sessions: list[DetectedModelSession] = []
    seen: set[tuple[str, str]] = set()
    for session in (
        detect_claude_active_sessions(
            event_inbox_path=event_inbox_path,
            registry_path=registry_path,
            consume_registry_events=consume_registry_events,
        )
        + detect_grok_active_sessions()
        + detect_registered_active_sessions(
            inbox_path=event_inbox_path,
            registry_path=registry_path,
            consume_registry_events=consume_registry_events,
        )
    ):
        key = (session.source, session.id)
        if key in seen:
            continue
        seen.add(key)
        sessions.append(session)
    return tuple(sessions)
