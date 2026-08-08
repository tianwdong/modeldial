from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import shutil


HOOK_TIMEOUT_SECONDS = 2
LEGACY_HELPER_MARKERS = ("ModelPilotSessionHook", "/ModelPilot/bin/")
MODELDIAL_HELPER_NAME = "ModeldialSessionHook"

CODEX_HOOK_EVENTS = (
    ("SessionStart", "startup|resume"),
    ("UserPromptSubmit", None),
    ("Stop", None),
)
CLAUDE_HOOK_EVENTS = (
    ("SessionStart", "startup|resume|clear|compact"),
    ("UserPromptSubmit", None),
    ("Stop", None),
    ("StopFailure", None),
    ("SessionEnd", None),
)


def install_codex_hooks(hooks_path: Path, helper_path: Path) -> bool:
    payload = _load_json_object(hooks_path)
    hooks = payload.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("Codex hooks must be an object")
    changed = False
    for event_name, matcher in CODEX_HOOK_EVENTS:
        entries = hooks.setdefault(event_name, [])
        if not isinstance(entries, list):
            raise ValueError(f"Codex hook event {event_name} must be an array")
        if _remove_legacy_hooks(entries, "codex"):
            changed = True
        if _contains_modeldial_hook(entries, helper_path, "codex"):
            continue
        entries.append(_hook_group(helper_path, "codex", matcher=matcher))
        changed = True
    if changed:
        _write_json_object(hooks_path, payload)
    return changed


def install_claude_hooks(settings_path: Path, helper_path: Path) -> bool:
    payload = _load_json_object(settings_path)
    hooks = payload.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("Claude hooks must be an object")
    changed = False
    for event_name, matcher in CLAUDE_HOOK_EVENTS:
        entries = hooks.setdefault(event_name, [])
        if not isinstance(entries, list):
            raise ValueError(f"Claude hook event {event_name} must be an array")
        if _remove_legacy_hooks(entries, "claude"):
            changed = True
        if _contains_modeldial_hook(entries, helper_path, "claude"):
            continue
        entries.append(_hook_group(helper_path, "claude", matcher=matcher))
        changed = True
    if changed:
        _write_json_object(settings_path, payload)
    return changed


def uninstall_codex_hooks(hooks_path: Path, helper_path: Path) -> bool:
    """Remove ModelDial's Codex hook handlers without touching other hooks."""

    return _uninstall_hooks(
        hooks_path,
        helper_path,
        source="codex",
        event_names=tuple(event_name for event_name, _ in CODEX_HOOK_EVENTS),
        config_name="Codex",
    )


def uninstall_claude_hooks(settings_path: Path, helper_path: Path) -> bool:
    """Remove ModelDial's Claude hook handlers without touching other hooks."""

    return _uninstall_hooks(
        settings_path,
        helper_path,
        source="claude",
        event_names=tuple(event_name for event_name, _ in CLAUDE_HOOK_EVENTS),
        config_name="Claude",
    )


def install_helper(source_path: Path, destination_path: Path) -> bool:
    source_bytes = source_path.read_bytes()
    try:
        existing_bytes = destination_path.read_bytes()
    except OSError:
        existing_bytes = None
    if source_bytes == existing_bytes:
        destination_path.chmod(0o755)
        return False
    destination_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = destination_path.with_name(f".{destination_path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(source_bytes)
    temporary.chmod(0o755)
    temporary.replace(destination_path)
    return True


def uninstall_helper(source_path: Path, destination_path: Path) -> bool:
    """Remove the helper only when it still matches the ModelDial source."""

    if destination_path.name != MODELDIAL_HELPER_NAME:
        return False
    try:
        installed_bytes = destination_path.read_bytes()
    except FileNotFoundError:
        return False
    if installed_bytes != source_path.read_bytes():
        raise ValueError(
            f"refusing to remove unrecognized helper at {destination_path}"
        )
    destination_path.unlink()
    return True


def _hook_group(
    helper_path: Path,
    source: str,
    *,
    matcher: str | None,
) -> dict[str, object]:
    group: dict[str, object] = {
        "hooks": [
            {
                "type": "command",
                "command": f"{shlex.quote(str(helper_path))} --source {source}",
                "timeout": HOOK_TIMEOUT_SECONDS,
            }
        ]
    }
    if matcher:
        group["matcher"] = matcher
    return group


def _contains_modeldial_hook(
    entries: list[object],
    helper_path: Path,
    source: str,
) -> bool:
    helper = str(helper_path)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        handlers = entry.get("hooks")
        if not isinstance(handlers, list):
            continue
        for handler in handlers:
            if not isinstance(handler, dict):
                continue
            command = handler.get("command")
            if (
                isinstance(command, str)
                and helper in command
                and f"--source {source}" in command
            ):
                return True
    return False


def _uninstall_hooks(
    path: Path,
    helper_path: Path,
    *,
    source: str,
    event_names: tuple[str, ...],
    config_name: str,
) -> bool:
    payload = _load_json_object(path)
    hooks = payload.get("hooks")
    if hooks is None:
        return False
    if not isinstance(hooks, dict):
        raise ValueError(f"{config_name} hooks must be an object")

    changed = False
    for event_name in event_names:
        if event_name not in hooks:
            continue
        entries = hooks[event_name]
        if not isinstance(entries, list):
            raise ValueError(f"{config_name} hook event {event_name} must be an array")
        if _remove_modeldial_hooks(entries, helper_path, source):
            changed = True
    if changed:
        _write_json_object(path, payload)
    return changed


def _remove_modeldial_hooks(
    entries: list[object],
    helper_path: Path,
    source: str,
) -> bool:
    changed = False
    retained_entries: list[object] = []
    for entry in entries:
        if not isinstance(entry, dict):
            retained_entries.append(entry)
            continue
        handlers = entry.get("hooks")
        if not isinstance(handlers, list):
            retained_entries.append(entry)
            continue

        retained_handlers = [
            handler
            for handler in handlers
            if not _is_modeldial_hook_handler(handler, helper_path, source)
            and not _is_legacy_modeldial_hook_handler(handler, source)
        ]
        if len(retained_handlers) == len(handlers):
            retained_entries.append(entry)
            continue

        changed = True
        if retained_handlers:
            entry["hooks"] = retained_handlers
            retained_entries.append(entry)
    if changed:
        entries[:] = retained_entries
    return changed


def _is_modeldial_hook_handler(
    handler: object,
    helper_path: Path,
    source: str,
) -> bool:
    if not isinstance(handler, dict):
        return False
    command = handler.get("command")
    return isinstance(command, str) and _is_modeldial_command(
        command, str(helper_path), source
    )


def _is_modeldial_command(
    command: str,
    helper_path: Path | str,
    source: str,
) -> bool:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    helper = str(helper_path)
    return any(
        token == helper
        and index + 2 < len(tokens)
        and tokens[index + 1] == "--source"
        and tokens[index + 2] == source
        for index, token in enumerate(tokens)
    )


def _is_legacy_modeldial_hook_handler(handler: object, source: str) -> bool:
    if not isinstance(handler, dict):
        return False
    command = handler.get("command")
    if not isinstance(command, str):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if not any(
        token == "--source"
        and index + 1 < len(tokens)
        and tokens[index + 1] == source
        for index, token in enumerate(tokens)
    ):
        return False
    return any(marker in command for marker in LEGACY_HELPER_MARKERS)


def _remove_legacy_hooks(entries: list[object], source: str) -> bool:
    retained = [entry for entry in entries if not _is_legacy_hook_group(entry, source)]
    if len(retained) == len(entries):
        return False
    entries[:] = retained
    return True


def _is_legacy_hook_group(entry: object, source: str) -> bool:
    if not isinstance(entry, dict):
        return False
    handlers = entry.get("hooks")
    if not isinstance(handlers, list):
        return False
    for handler in handlers:
        if not isinstance(handler, dict):
            continue
        command = handler.get("command")
        if not isinstance(command, str) or f"--source {source}" not in command:
            continue
        if any(marker in command for marker in LEGACY_HELPER_MARKERS):
            return True
    return False


def _load_json_object(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read JSON config: {path}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"JSON config must be an object: {path}")
    return payload


def _write_json_object(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = path.with_name(f"{path.name}.modeldial-backup-{timestamp}")
        if not backup.exists():
            shutil.copy2(path, backup)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
