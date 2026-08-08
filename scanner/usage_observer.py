from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import math
from pathlib import Path
from statistics import median
from typing import Iterable

from .codex_current_model import _is_modeldial_scan_workspace
from .costing import estimate_reference_cost
from .usage_behavior import (
    message_category_from_response_item,
    mcp_tool_step,
    summarize_turn_behavior,
    tool_step_from_patch_event,
    tool_step_from_response_item,
)
from .usage_store import UsageStore


DEFAULT_LOOKBACK_DAYS = 7
DEFAULT_BOOTSTRAP_MAX_BYTES = 64 * 1024 * 1024
DEFAULT_PER_FILE_MAX_BYTES = 2 * 1024 * 1024
MAX_TRACKED_FILES = 512
_TERMINAL_OUTCOMES = {
    "task_complete": "completed",
    "turn_complete": "completed",
    "turn_aborted": "cancelled",
    "task_failed": "failed",
    "turn_failed": "failed",
}
_INTERESTING_MARKERS = (
    b'"type":"session_meta"',
    b'"type": "session_meta"',
    b'"type":"turn_context"',
    b'"type": "turn_context"',
    b'"type":"task_started"',
    b'"type": "task_started"',
    b'"type":"turn_started"',
    b'"type": "turn_started"',
    b'"type":"token_count"',
    b'"type": "token_count"',
    b'"type":"task_complete"',
    b'"type": "task_complete"',
    b'"type":"turn_complete"',
    b'"type": "turn_complete"',
    b'"type":"turn_aborted"',
    b'"type": "turn_aborted"',
    b'"type":"task_failed"',
    b'"type": "task_failed"',
    b'"type":"turn_failed"',
    b'"type": "turn_failed"',
    b'"type":"function_call"',
    b'"type": "function_call"',
    b'"type":"custom_tool_call"',
    b'"type": "custom_tool_call"',
    b'"type":"function_call_output"',
    b'"type": "function_call_output"',
    b'"type":"custom_tool_call_output"',
    b'"type": "custom_tool_call_output"',
    b'"role":"user"',
    b'"role": "user"',
    b'"role":"assistant"',
    b'"role": "assistant"',
    b'"type":"user_message"',
    b'"type": "user_message"',
    b'"type":"patch_apply_end"',
    b'"type": "patch_apply_end"',
    b'"type":"mcp_tool_call_end"',
    b'"type": "mcp_tool_call_end"',
)


def observe_codex_usage(
    *,
    sessions_root: Path | None = None,
    store: UsageStore,
    now: datetime | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    bootstrap_max_bytes: int = DEFAULT_BOOTSTRAP_MAX_BYTES,
    per_file_max_bytes: int = DEFAULT_PER_FILE_MAX_BYTES,
) -> dict[str, object]:
    with store.transaction():
        return _observe_codex_usage_unlocked(
            sessions_root=sessions_root,
            store=store,
            now=now,
            lookback_days=lookback_days,
            bootstrap_max_bytes=bootstrap_max_bytes,
            per_file_max_bytes=per_file_max_bytes,
        )


def read_codex_usage(
    *,
    store: UsageStore,
    now: datetime | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> dict[str, object]:
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = observed_at - timedelta(days=max(1, lookback_days))
    if not store.usage_state_path.is_file():
        return {
            "schema_version": 1,
            "status": "unavailable",
            "captured_at": _iso(observed_at),
            "period_start": _iso(cutoff),
            "period_end": _iso(observed_at),
            "coverage_started_at": None,
            "coverage_continuous_since": None,
            "coverage_complete": False,
            "bootstrap_truncated": False,
            "observation_count": 0,
            "excluded_observation_count": 0,
            "collection": {
                "source_count": 0,
                "discovered_file_count": 0,
                "sampled_file_count": 0,
                "parsed_file_count": 0,
                "failed_file_count": 0,
                "unknown_file_count": 0,
                "deduplicated_file_count": 0,
                "budget_limited_file_count": 0,
                "gap_detected": False,
                "upstream_retention_risk": "unknown",
            },
            "aggregates": [],
        }

    persisted = store.load_usage_state()
    raw_observations = persisted.get("observations")
    observations = {
        str(key): value
        for key, value in (
            raw_observations.items()
            if isinstance(raw_observations, dict)
            else ()
        )
        if isinstance(value, dict)
        and _timestamp_value(value.get("started_at")) >= cutoff.timestamp()
    }
    stored_files = persisted.get("files")
    raw_files = stored_files if isinstance(stored_files, dict) else {}
    stored_collection = persisted.get("collection")
    collection = (
        dict(stored_collection) if isinstance(stored_collection, dict) else {}
    )
    if not collection:
        parsed_file_count = sum(
            1
            for value in raw_files.values()
            if isinstance(value, dict)
            and value.get("collection_parse_status") == "success"
        )
        failed_file_count = sum(
            1
            for value in raw_files.values()
            if isinstance(value, dict)
            and value.get("collection_parse_status") == "failed"
        )
        collection = {
            "source_count": 0,
            "discovered_file_count": len(raw_files),
            "sampled_file_count": len(raw_files),
            "parsed_file_count": parsed_file_count,
            "failed_file_count": failed_file_count,
            "unknown_file_count": max(
                0,
                len(raw_files) - parsed_file_count - failed_file_count,
            ),
            "deduplicated_file_count": 0,
            "budget_limited_file_count": 0,
            "gap_detected": bool(persisted.get("bootstrap_truncated", False)),
            "upstream_retention_risk": "unknown",
        }
    return _usage_summary(
        observations.values(),
        observed_at=observed_at,
        cutoff=cutoff,
        bootstrap_truncated=bool(persisted.get("bootstrap_truncated", False)),
        coverage_continuous_since=_text(
            persisted.get("coverage_continuous_since")
        ),
        collection=dict(collection),
    )


def _observe_codex_usage_unlocked(
    *,
    sessions_root: Path | None = None,
    store: UsageStore,
    now: datetime | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    bootstrap_max_bytes: int = DEFAULT_BOOTSTRAP_MAX_BYTES,
    per_file_max_bytes: int = DEFAULT_PER_FILE_MAX_BYTES,
) -> dict[str, object]:
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = observed_at - timedelta(days=max(1, lookback_days))
    root = sessions_root or (Path.home() / ".codex" / "sessions")
    roots = (
        (root, root.parent / "archived_sessions")
        if root.name == "sessions"
        else (root,)
    )
    secret = store.identity_key()
    persisted = store.load_usage_state()
    raw_files = persisted.get("files")
    raw_observations = persisted.get("observations")
    file_states = dict(raw_files) if isinstance(raw_files, dict) else {}
    observations = (
        {str(key): value for key, value in raw_observations.items() if isinstance(value, dict)}
        if isinstance(raw_observations, dict)
        else {}
    )
    bootstrap_truncated = bool(persisted.get("bootstrap_truncated", False))
    coverage_continuous_since = _text(
        persisted.get("coverage_continuous_since")
    )
    gap_detected = False
    read_budget = max(0, bootstrap_max_bytes)
    current_path_keys: set[str] = set()
    discovery_diagnostics: dict[str, int] = {}
    recent_files = _recent_session_files(
        roots,
        cutoff,
        diagnostics=discovery_diagnostics,
    )
    sampled_files = recent_files[:MAX_TRACKED_FILES]

    for path, stat in sampled_files:
        path_key = _local_hmac(secret, f"path:{path.resolve()}")
        current_path_keys.add(path_key)
        existing = file_states.get(path_key)
        state = dict(existing) if isinstance(existing, dict) else {}
        same_file = (
            state.get("inode") == stat.st_ino
            and _nonnegative_int(state.get("offset")) <= stat.st_size
        )
        if same_file:
            start = _nonnegative_int(state.get("offset"))
            available = max(0, stat.st_size - start)
        else:
            state = _new_file_state(secret, path_key)
            available = stat.st_size
            start = max(0, stat.st_size - min(available, per_file_max_bytes))
            if start > 0:
                bootstrap_truncated = True
                gap_detected = True

        if available <= 0:
            state["inode"] = stat.st_ino
            state["offset"] = stat.st_size
            file_states[path_key] = state
            continue

        allowed = min(available, per_file_max_bytes, read_budget)
        if allowed <= 0:
            state["inode"] = stat.st_ino
            state["offset"] = stat.st_size
            state["current_turn"] = None
            state["collection_budget_limited"] = True
            file_states[path_key] = state
            bootstrap_truncated = True
            gap_detected = True
            continue
        if same_file and allowed < available:
            start = stat.st_size - allowed
            state["current_turn"] = None
            bootstrap_truncated = True
            gap_detected = True
            state["collection_budget_limited"] = True
        elif not same_file:
            start = stat.st_size - allowed
            if start > 0:
                state["current_turn"] = None
                bootstrap_truncated = True
                gap_detected = True
                state["collection_budget_limited"] = True

        data, next_offset, read_succeeded = _read_complete_slice_with_status(
            path,
            start,
            allowed,
        )
        parse_failed = not read_succeeded
        if data:
            for raw_line in data.splitlines():
                event = _decode_interesting_event(raw_line)
                if event is not None:
                    _apply_event(state, event, observations, secret)
                elif any(marker in raw_line for marker in _INTERESTING_MARKERS):
                    parse_failed = True
        state["collection_parse_status"] = (
            "failed" if parse_failed else "success"
        )
        if not state.get("collection_budget_limited"):
            state["collection_budget_limited"] = False
        state["inode"] = stat.st_ino
        state["offset"] = max(start, next_offset)
        file_states[path_key] = state
        read_budget -= allowed

    file_states = {
        key: value
        for key, value in file_states.items()
        if key in current_path_keys and isinstance(value, dict)
    }
    observations = {
        key: value
        for key, value in observations.items()
        if _timestamp_value(value.get("started_at")) >= cutoff.timestamp()
    }
    if gap_detected or (bootstrap_truncated and not coverage_continuous_since):
        coverage_continuous_since = _iso(observed_at)
    elif (
        bootstrap_truncated
        and coverage_continuous_since
        and cutoff.timestamp() >= _timestamp_value(coverage_continuous_since)
    ):
        bootstrap_truncated = False
    if not bootstrap_truncated:
        for state in file_states.values():
            state["collection_budget_limited"] = False
    persisted_payload: dict[str, object] = {
        "schema_version": 1,
        "files": file_states,
        "observations": observations,
        "bootstrap_truncated": bootstrap_truncated,
        "coverage_continuous_since": coverage_continuous_since,
    }
    parsed_file_count = sum(
        1
        for state in file_states.values()
        if state.get("collection_parse_status") == "success"
    )
    failed_file_count = sum(
        1
        for state in file_states.values()
        if state.get("collection_parse_status") == "failed"
    )
    unknown_file_count = max(
        0,
        len(sampled_files) - parsed_file_count - failed_file_count,
    )
    budget_limited_file_count = max(0, len(recent_files) - len(sampled_files)) + sum(
        1
        for state in file_states.values()
        if state.get("collection_budget_limited") is True
    )
    collection = {
        "source_count": sum(1 for root in roots if root.is_dir()),
        "discovered_file_count": len(recent_files),
        "sampled_file_count": len(sampled_files),
        "parsed_file_count": parsed_file_count,
        "failed_file_count": failed_file_count,
        "unknown_file_count": unknown_file_count,
        "deduplicated_file_count": discovery_diagnostics.get(
            "deduplicated_file_count", 0
        ),
        "budget_limited_file_count": budget_limited_file_count,
        "gap_detected": bool(gap_detected or bootstrap_truncated),
        "upstream_retention_risk": "unknown",
    }
    persisted_payload["collection"] = collection
    store.save_usage_state(persisted_payload)
    return _usage_summary(
        observations.values(),
        observed_at=observed_at,
        cutoff=cutoff,
        bootstrap_truncated=bootstrap_truncated,
        coverage_continuous_since=coverage_continuous_since,
        collection=collection,
    )


def reset_codex_usage_observations(
    *,
    sessions_root: Path | None = None,
    store: UsageStore,
    now: datetime | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> None:
    with store.transaction():
        _reset_codex_usage_observations_unlocked(
            sessions_root=sessions_root,
            store=store,
            now=now,
            lookback_days=lookback_days,
        )


def _reset_codex_usage_observations_unlocked(
    *,
    sessions_root: Path | None = None,
    store: UsageStore,
    now: datetime | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> None:
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = observed_at - timedelta(days=max(1, lookback_days))
    root = sessions_root or (Path.home() / ".codex" / "sessions")
    roots = (
        (root, root.parent / "archived_sessions")
        if root.name == "sessions"
        else (root,)
    )
    secret = store.identity_key()
    file_states: dict[str, dict[str, object]] = {}
    for path, stat in _recent_session_files(roots, cutoff)[:MAX_TRACKED_FILES]:
        path_key = _local_hmac(secret, f"path:{path.resolve()}")
        state = _new_file_state(secret, path_key)
        state.update(
            {
                "inode": stat.st_ino,
                "offset": stat.st_size,
                "collection_parse_status": "success",
                "collection_budget_limited": False,
            }
        )
        file_states[path_key] = state
    store.save_usage_state(
        {
            "schema_version": 1,
            "files": file_states,
            "observations": {},
            "bootstrap_truncated": False,
            "coverage_continuous_since": _iso(observed_at),
            "cleared_at": _iso(observed_at),
        }
    )


def _recent_session_files(
    roots: Iterable[Path],
    cutoff: datetime,
    *,
    diagnostics: dict[str, int] | None = None,
) -> list[tuple[Path, object]]:
    files = []
    seen_paths: set[Path] = set()
    for root in roots:
        try:
            paths = root.rglob("rollout-*.jsonl") if root.is_dir() else ()
            for path in paths:
                try:
                    stat = path.stat()
                except OSError:
                    continue
                resolved = path.resolve()
                if stat.st_mtime < cutoff.timestamp():
                    continue
                if resolved in seen_paths:
                    if diagnostics is not None:
                        diagnostics["deduplicated_file_count"] = (
                            diagnostics.get("deduplicated_file_count", 0) + 1
                        )
                    continue
                seen_paths.add(resolved)
                files.append((path, stat))
        except OSError:
            continue
    files.sort(key=lambda item: (item[1].st_mtime, str(item[0])), reverse=True)
    return files


def _new_file_state(secret: bytes, path_key: str) -> dict[str, object]:
    return {
        "offset": 0,
        "inode": None,
        "session_key": _local_hmac(secret, f"session-path:{path_key}"),
        "provider_id": "unknown",
        "is_subagent": False,
        "is_modeldial_scan": False,
        "fork_replay_before": None,
        "previous_total_usage": None,
        "current_turn": None,
    }


def _read_complete_slice_with_status(
    path: Path,
    start: int,
    length: int,
) -> tuple[bytes, int, bool]:
    try:
        with path.open("rb") as handle:
            aligned = start == 0
            if start > 0:
                handle.seek(start - 1)
                aligned = handle.read(1) == b"\n"
            handle.seek(start)
            data = handle.read(length)
    except OSError:
        return b"", start, False
    if not data:
        return b"", start, True
    first = 0
    if not aligned:
        first_newline = data.find(b"\n")
        if first_newline < 0:
            return b"", start + len(data), True
        first = first_newline + 1
    last_newline = data.rfind(b"\n", first)
    if last_newline < first:
        return b"", start + first, True
    return data[first:last_newline], start + last_newline + 1, True


def _decode_interesting_event(raw_line: bytes) -> dict[str, object] | None:
    if not any(marker in raw_line for marker in _INTERESTING_MARKERS):
        return None
    try:
        payload = json.loads(raw_line)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _apply_event(
    state: dict[str, object],
    event: dict[str, object],
    observations: dict[str, dict[str, object]],
    secret: bytes,
) -> None:
    payload = _dict(event.get("payload"))
    event_kind = _text(event.get("type"))
    payload_type = _text(payload.get("type"))
    timestamp = _text(event.get("timestamp"))

    if event_kind == "session_meta":
        session_id = _text(payload.get("session_id")) or _text(payload.get("id"))
        forked_from_id = _text(payload.get("forked_from_id")) or _text(
            payload.get("forkedFromId")
        )
        identity_id = forked_from_id or session_id
        if identity_id:
            state["session_key"] = _local_hmac(secret, f"session:{identity_id}")
        if forked_from_id and _timestamp_value(timestamp) > 0:
            state["fork_replay_before"] = _from_epoch(
                _timestamp_value(timestamp) + 5
            )
        provider = _text(payload.get("model_provider"))
        if provider:
            state["provider_id"] = _provider_id(provider)
        state["is_modeldial_scan"] = _is_modeldial_scan_workspace(
            _text(payload.get("cwd"))
        )
        source = payload.get("source")
        state["is_subagent"] = bool(
            isinstance(source, dict) and "subagent" in source
        ) or _text(payload.get("thread_source")) == "subagent"
        return

    fork_replay_before = _timestamp_value(state.get("fork_replay_before"))
    if (
        fork_replay_before > 0
        and _timestamp_value(timestamp) > 0
        and _timestamp_value(timestamp) < fork_replay_before
    ):
        return

    if event_kind == "turn_context":
        turn_key = _turn_key(secret, payload, timestamp)
        current = _current_turn(state)
        if current is not None and current.get("turn_key") != turn_key:
            _finalize_current(state, observations, "interrupted", timestamp)
            current = None
        if current is None:
            current = _new_turn(turn_key, timestamp)
            state["current_turn"] = current
        current["model"] = _text(payload.get("model"))
        current["effort"] = (
            _text(payload.get("effort"))
            or _text(payload.get("reasoning_effort"))
        )
        return

    if event_kind == "response_item":
        current = _current_turn(state)
        if current is None:
            return
        item_type = _text(payload.get("type"))
        role = _text(payload.get("role"))
        if item_type == "message" and role == "user":
            _start_model_wait(
                current,
                timestamp,
                source="user_message",
                replace_provisional=True,
            )
        elif item_type in {"function_call_output", "custom_tool_call_output"}:
            _start_model_wait(current, timestamp, source="tool_output")
        elif item_type in {"function_call", "custom_tool_call"}:
            _finish_model_wait(current, timestamp)
        elif item_type == "message" and role == "assistant":
            current["last_assistant_output_at"] = timestamp
        message_category = message_category_from_response_item(payload)
        if message_category is not None:
            current["behavior_observed"] = True
            current["message_category_hint"] = message_category
            return
        step = tool_step_from_response_item(
            payload,
            lambda path: _local_hmac(secret, f"behavior-file:{path}"),
        )
        if step is not None:
            _append_tool_step(current, step)
        return

    if event_kind != "event_msg" or not payload_type:
        return
    if payload_type in {"task_started", "turn_started"}:
        turn_key = _turn_key(secret, payload, timestamp)
        current = _current_turn(state)
        if current is not None and current.get("turn_key") != turn_key:
            _finalize_current(state, observations, "interrupted", timestamp)
        if _current_turn(state) is None:
            state["current_turn"] = _new_turn(turn_key, timestamp)
        return
    if payload_type == "user_message":
        current = _current_turn(state)
        if current is not None:
            _start_model_wait(
                current,
                timestamp,
                source="user_message",
                replace_provisional=True,
            )
        return
    if payload_type == "patch_apply_end":
        current = _current_turn(state)
        if current is not None:
            _append_tool_step(
                current,
                tool_step_from_patch_event(
                    payload,
                    lambda path: _local_hmac(secret, f"behavior-file:{path}"),
                ),
            )
        return
    if payload_type == "mcp_tool_call_end":
        current = _current_turn(state)
        if current is not None:
            _append_tool_step(current, mcp_tool_step())
        return
    if payload_type == "token_count":
        current = _current_turn(state)
        if current is None:
            current = _new_turn(_turn_key(secret, payload, timestamp), timestamp)
            state["current_turn"] = current
        info = _dict(payload.get("info"))
        last_usage = _token_usage(info, "last_token_usage", "lastTokenUsage")
        total_usage = _token_usage(info, "total_token_usage", "totalTokenUsage")
        previous_total = _optional_usage(state.get("previous_total_usage"))
        if total_usage is not None and total_usage == previous_total:
            return
        if last_usage is not None:
            usage = last_usage
        elif total_usage is not None:
            usage = _usage_delta(total_usage, previous_total)
        else:
            usage = _empty_usage()
        if total_usage is not None:
            state["previous_total_usage"] = total_usage
        for key in (
            "input_tokens",
            "cached_input_tokens",
            "cache_write_input_tokens",
            "output_tokens",
            "reasoning_tokens",
        ):
            current[key] = _nonnegative_int(current.get(key)) + usage[key]
        return
    if payload_type in _TERMINAL_OUTCOMES:
        current = _current_turn(state)
        if current is not None:
            _finish_model_wait(
                current,
                _text(current.get("last_assistant_output_at")),
            )
            duration = _optional_nonnegative_int(payload.get("duration_ms"))
            if duration is not None:
                current["active_duration_ms"] = duration
            _finalize_current(
                state,
                observations,
                _TERMINAL_OUTCOMES[payload_type],
                timestamp,
            )


def _new_turn(turn_key: str, timestamp: str | None) -> dict[str, object]:
    return {
        "turn_key": turn_key,
        "started_at": timestamp,
        "model": None,
        "effort": None,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "active_duration_ms": None,
        "response_wait_ms": 0,
        "response_wait_sample_count": 0,
        "response_wait_started_at": timestamp,
        "response_wait_started_source": "turn_started" if timestamp else None,
        "last_assistant_output_at": None,
        "behavior_observed": False,
        "message_category_hint": None,
        "tool_steps": [],
    }


def _finalize_current(
    state: dict[str, object],
    observations: dict[str, dict[str, object]],
    outcome: str,
    ended_at: str | None,
) -> None:
    current = _current_turn(state)
    if current is None:
        return
    session_key = _text(state.get("session_key")) or "local-hmac:unknown"
    turn_key = _text(current.get("turn_key")) or "local-hmac:unknown"
    started_at = _text(current.get("started_at")) or ended_at
    wall_duration = _duration_ms(started_at, ended_at)
    active_duration = _optional_nonnegative_int(current.get("active_duration_ms"))
    if active_duration is None:
        active_duration = wall_duration
    provider_id = _text(state.get("provider_id")) or "unknown"
    model = _text(current.get("model"))
    effort = _text(current.get("effort"))
    exclusion_reasons = []
    if not model or not effort:
        exclusion_reasons.append("missing_model_configuration")
    if sum(
        _nonnegative_int(current.get(key))
        for key in ("input_tokens", "output_tokens")
    ) == 0:
        exclusion_reasons.append("missing_usage")
    if outcome == "interrupted":
        exclusion_reasons.append("missing_terminal")
    if model == "codex-auto-review":
        exclusion_reasons.append("system_model")
    if bool(state.get("is_modeldial_scan", False)):
        exclusion_reasons.append("modeldial_evaluation")
    confidence = 1.0
    if provider_id == "unknown":
        confidence = min(confidence, 0.8)
    if not model or not effort:
        confidence = 0.0
    behavior = summarize_turn_behavior(current)
    observation_id = "sha256:" + hashlib.sha256(
        f"{session_key}|{turn_key}".encode("utf-8")
    ).hexdigest()
    observations[observation_id] = {
        "schema_version": 1,
        "observation_id": observation_id,
        "session_key": session_key,
        "turn_key": turn_key,
        "model_configuration_id": (
            _model_configuration_id(provider_id, model, effort)
            if model and effort
            else None
        ),
        "provider_id": provider_id,
        "raw_model_id": model,
        "reasoning_effort": effort,
        "started_at": started_at,
        "ended_at": ended_at,
        "active_duration_ms": active_duration,
        "wall_duration_ms": wall_duration,
        "response_wait_ms": (
            _nonnegative_int(current.get("response_wait_ms"))
            if _nonnegative_int(current.get("response_wait_sample_count")) > 0
            else None
        ),
        "response_wait_sample_count": _nonnegative_int(
            current.get("response_wait_sample_count")
        ),
        "response_wait_basis": (
            "model_input_to_terminal_output"
            if _nonnegative_int(current.get("response_wait_sample_count")) > 0
            else None
        ),
        "usage": {
            "input_tokens": _nonnegative_int(current.get("input_tokens")),
            "cached_input_tokens": _nonnegative_int(
                current.get("cached_input_tokens")
            ),
            "cache_write_input_tokens": _nonnegative_int(
                current.get("cache_write_input_tokens")
            ),
            "output_tokens": _nonnegative_int(current.get("output_tokens")),
            "reasoning_tokens": _nonnegative_int(
                current.get("reasoning_tokens")
            ),
        },
        "outcome": outcome,
        "origin": "rollout",
        "is_subagent": bool(state.get("is_subagent", False)),
        "is_modeldial_evaluation": bool(state.get("is_modeldial_scan", False)),
        "attribution_confidence": confidence,
        "exclusion_reasons": exclusion_reasons,
        **behavior,
    }
    state["current_turn"] = None


def _start_model_wait(
    current: dict[str, object],
    timestamp: str | None,
    *,
    source: str,
    replace_provisional: bool = False,
) -> None:
    if _timestamp_value(timestamp) <= 0:
        return
    existing = _text(current.get("response_wait_started_at"))
    if existing is not None and not (
        replace_provisional
        and current.get("response_wait_started_source") == "turn_started"
    ):
        return
    current["response_wait_started_at"] = timestamp
    current["response_wait_started_source"] = source
    current["last_assistant_output_at"] = None


def _finish_model_wait(
    current: dict[str, object],
    timestamp: str | None,
) -> None:
    started_at = _text(current.get("response_wait_started_at"))
    duration = _duration_ms(started_at, timestamp)
    if duration is None:
        return
    current["response_wait_ms"] = (
        _nonnegative_int(current.get("response_wait_ms")) + duration
    )
    current["response_wait_sample_count"] = (
        _nonnegative_int(current.get("response_wait_sample_count")) + 1
    )
    current["response_wait_started_at"] = None
    current["response_wait_started_source"] = None
    current["last_assistant_output_at"] = None


def _usage_summary(
    observations: Iterable[dict[str, object]],
    *,
    observed_at: datetime,
    cutoff: datetime,
    bootstrap_truncated: bool,
    coverage_continuous_since: str | None,
    collection: dict[str, object],
) -> dict[str, object]:
    all_rows = list(observations)
    rows = [
        row
        for row in all_rows
        if not _is_excluded_workload_observation(row)
    ]
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        configuration_id = _text(row.get("model_configuration_id"))
        if configuration_id:
            grouped.setdefault(configuration_id, []).append(row)
    aggregates = [
        _aggregate(configuration_id, group, cutoff, observed_at)
        for configuration_id, group in grouped.items()
    ]
    aggregates.sort(key=lambda item: str(item["model_configuration_id"]))
    coverage_timestamps = [
        _timestamp_value(row.get("started_at"))
        for row in rows
        if _timestamp_value(row.get("started_at")) > 0
    ]
    coverage_started_at = (
        _from_epoch(min(coverage_timestamps)) if coverage_timestamps else None
    )
    return {
        "schema_version": 1,
        "status": "available",
        "captured_at": _iso(observed_at),
        "period_start": _iso(cutoff),
        "period_end": _iso(observed_at),
        "coverage_started_at": coverage_started_at,
        "coverage_continuous_since": coverage_continuous_since,
        "coverage_complete": not bootstrap_truncated,
        "bootstrap_truncated": bootstrap_truncated,
        "observation_count": len(rows),
        "excluded_observation_count": len(all_rows) - len(rows),
        "collection": collection,
        "aggregates": aggregates,
    }


def _is_excluded_workload_observation(row: dict[str, object]) -> bool:
    reasons = set(_string_list(row.get("exclusion_reasons")))
    return bool(
        reasons & {"system_model", "modeldial_evaluation"}
        or _text(row.get("raw_model_id")) == "codex-auto-review"
        or row.get("is_modeldial_evaluation") is True
    )


def _aggregate(
    configuration_id: str,
    rows: list[dict[str, object]],
    cutoff: datetime,
    observed_at: datetime,
) -> dict[str, object]:
    usage_rows = [_dict(row.get("usage")) for row in rows]
    intervals = [
        (_timestamp_value(row.get("started_at")), _timestamp_value(row.get("ended_at")))
        for row in rows
    ]
    first = rows[0]
    confidences = [
        _optional_number(row.get("attribution_confidence"))
        for row in rows
    ]
    valid_confidences = [value for value in confidences if value is not None]
    sample_days = {
        str(row.get("started_at"))[:10]
        for row in rows
        if _text(row.get("started_at"))
    }
    completed_durations = [
        _nonnegative_int(row.get("active_duration_ms"))
        for row in rows
        if row.get("outcome") == "completed"
        and not bool(row.get("is_subagent", False))
        and _nonnegative_int(row.get("active_duration_ms")) > 0
    ]
    completed_rows = [
        row
        for row in rows
        if row.get("outcome") == "completed"
        and not bool(row.get("is_subagent", False))
    ]
    reference_costs = [
        estimate_reference_cost(
            _text(row.get("raw_model_id")) or "",
            input_tokens=_optional_nonnegative_int(usage.get("input_tokens")),
            cached_input_tokens=_optional_nonnegative_int(
                usage.get("cached_input_tokens")
            ),
            cache_write_input_tokens=_optional_nonnegative_int(
                usage.get("cache_write_input_tokens")
            ),
            output_tokens=_optional_nonnegative_int(usage.get("output_tokens")),
            reasoning_output_tokens=_optional_nonnegative_int(
                usage.get("reasoning_tokens")
            ),
        )
        for row, usage in zip(rows, usage_rows, strict=True)
    ]
    estimated_reference_costs = [
        estimate for estimate in reference_costs if estimate.status == "estimated"
    ]
    reference_cost_status = (
        "estimated"
        if len(estimated_reference_costs) == len(reference_costs)
        else "partial"
        if estimated_reference_costs
        else "unpriced"
        if any(estimate.status == "unpriced" for estimate in reference_costs)
        else "unavailable"
    )
    pricing_snapshot_ids = {
        estimate.pricing_snapshot
        for estimate in estimated_reference_costs
        if estimate.pricing_snapshot
    }
    response_wait_rows = [
        row
        for row in completed_rows
        if _optional_nonnegative_int(row.get("response_wait_ms")) is not None
    ]
    behavior_rows = [
        row
        for row in completed_rows
        if _nonnegative_int(row.get("behavior_schema_version")) > 0
    ]
    edit_rows = [row for row in behavior_rows if row.get("has_edits") is True]
    retry_observed_rows = [
        row for row in edit_rows if _known_retry_count(row.get("retry_count"))
    ]
    one_shot_rows = [row for row in edit_rows if row.get("one_shot") is True]
    category_counts: dict[str, int] = {}
    for row in behavior_rows:
        category = _text(row.get("task_category"))
        if category:
            category_counts[category] = category_counts.get(category, 0) + 1
    edit_usage = _sum_usage(edit_rows)
    edit_count = len(edit_rows)
    edit_cost = (
        estimate_reference_cost(
            _text(first.get("raw_model_id")) or "",
            input_tokens=edit_usage["input_tokens"],
            cached_input_tokens=edit_usage["cached_input_tokens"],
            cache_write_input_tokens=edit_usage["cache_write_input_tokens"],
            output_tokens=edit_usage["output_tokens"],
            reasoning_output_tokens=edit_usage["reasoning_tokens"],
        )
        if edit_count
        else None
    )
    return {
        "schema_version": 1,
        "model_configuration_id": configuration_id,
        "provider_id": first.get("provider_id"),
        "raw_model_id": first.get("raw_model_id"),
        "reasoning_effort": first.get("reasoning_effort"),
        "period_start": _iso(cutoff),
        "period_end": _iso(observed_at),
        "completed_work_units": sum(
            1
            for row in rows
            if row.get("outcome") == "completed"
            and not bool(row.get("is_subagent", False))
        ),
        "subagent_completed_work_units": sum(
            1
            for row in rows
            if row.get("outcome") == "completed"
            and bool(row.get("is_subagent", False))
        ),
        "active_duration_ms": sum(
            _nonnegative_int(row.get("active_duration_ms")) for row in rows
        ),
        "median_active_duration_ms": (
            round(median(completed_durations)) if completed_durations else None
        ),
        "wallclock_union_ms": _wallclock_union_ms(intervals),
        "input_tokens": sum(_nonnegative_int(row.get("input_tokens")) for row in usage_rows),
        "cached_input_tokens": sum(
            _nonnegative_int(row.get("cached_input_tokens")) for row in usage_rows
        ),
        "cache_write_input_tokens": sum(
            _nonnegative_int(row.get("cache_write_input_tokens")) for row in usage_rows
        ),
        "output_tokens": sum(
            _nonnegative_int(row.get("output_tokens")) for row in usage_rows
        ),
        "reasoning_tokens": sum(
            _nonnegative_int(row.get("reasoning_tokens")) for row in usage_rows
        ),
        "reference_cost_usd": (
            round(
                sum(float(estimate.usd or 0) for estimate in estimated_reference_costs),
                9,
            )
            if estimated_reference_costs
            else None
        ),
        "reference_cost_status": reference_cost_status,
        "reference_cost_pricing_snapshot_id": (
            next(iter(pricing_snapshot_ids))
            if len(pricing_snapshot_ids) == 1
            else None
        ),
        "response_wait_ms": (
            sum(
                _nonnegative_int(row.get("response_wait_ms"))
                for row in response_wait_rows
            )
            if response_wait_rows
            else None
        ),
        "response_wait_work_unit_count": len(response_wait_rows),
        "behavior_observed_work_units": len(behavior_rows),
        "behavior_coverage_percent": (
            round(len(behavior_rows) / len(completed_rows) * 100, 1)
            if completed_rows
            else None
        ),
        "task_category_counts": dict(sorted(category_counts.items())),
        "edit_work_units": edit_count,
        "retry_observed_edit_work_units": len(retry_observed_rows),
        "one_shot_edit_work_units": len(one_shot_rows),
        "one_shot_rate_percent": (
            round(len(one_shot_rows) / len(retry_observed_rows) * 100, 1)
            if retry_observed_rows
            else None
        ),
        "retry_count": (
            sum(_nonnegative_int(row.get("retry_count")) for row in retry_observed_rows)
            if retry_observed_rows
            else None
        ),
        "retries_per_edit": (
            round(
                sum(
                    _nonnegative_int(row.get("retry_count"))
                    for row in retry_observed_rows
                )
                / len(retry_observed_rows),
                2,
            )
            if retry_observed_rows
            else None
        ),
        "edit_usage": edit_usage,
        "standard_cost_per_edit_usd": (
            edit_cost.usd / edit_count
            if edit_cost is not None and edit_cost.usd is not None
            else None
        ),
        "standard_cost_status": edit_cost.status if edit_cost else "unavailable",
        "pricing_snapshot_id": edit_cost.pricing_snapshot if edit_cost else None,
        "failure_count": sum(
            1 for row in rows if row.get("outcome") != "completed"
        ),
        "sample_days": len(sample_days),
        "attribution_confidence": (
            min(valid_confidences) if valid_confidences else 0.0
        ),
    }


def _append_tool_step(
    current: dict[str, object],
    step: dict[str, object],
) -> None:
    raw_steps = current.get("tool_steps")
    steps = raw_steps if isinstance(raw_steps, list) else []
    steps.append(step)
    current["tool_steps"] = steps
    current["behavior_observed"] = True


def _sum_usage(rows: Iterable[dict[str, object]]) -> dict[str, int]:
    usage_rows = [_dict(row.get("usage")) for row in rows]
    return {
        key: sum(_nonnegative_int(usage.get(key)) for usage in usage_rows)
        for key in (
            "input_tokens",
            "cached_input_tokens",
            "cache_write_input_tokens",
            "output_tokens",
            "reasoning_tokens",
        )
    }


def _known_retry_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _wallclock_union_ms(intervals: Iterable[tuple[float, float]]) -> int:
    valid = sorted((start, end) for start, end in intervals if start > 0 and end >= start)
    if not valid:
        return 0
    total = 0.0
    current_start, current_end = valid[0]
    for start, end in valid[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
            continue
        total += current_end - current_start
        current_start, current_end = start, end
    total += current_end - current_start
    return round(total * 1000)


def _token_usage(
    info: dict[str, object],
    snake_key: str,
    camel_key: str,
) -> dict[str, int] | None:
    raw_usage = info.get(snake_key)
    if not isinstance(raw_usage, dict):
        raw_usage = info.get(camel_key)
    if not isinstance(raw_usage, dict):
        return None
    usage = raw_usage
    return {
        "input_tokens": _nonnegative_int(
            usage.get("input_tokens") or usage.get("inputTokens")
        ),
        "cached_input_tokens": _nonnegative_int(
            usage.get("cached_input_tokens") or usage.get("cachedInputTokens")
        ),
        "cache_write_input_tokens": _nonnegative_int(
            usage.get("cache_write_input_tokens")
            or usage.get("cacheWriteInputTokens")
        ),
        "output_tokens": _nonnegative_int(
            usage.get("output_tokens") or usage.get("outputTokens")
        ),
        "reasoning_tokens": _nonnegative_int(
            usage.get("reasoning_output_tokens")
            or usage.get("reasoningOutputTokens")
        ),
    }


def _optional_usage(value: object) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    return {
        key: _nonnegative_int(value.get(key))
        for key in _empty_usage()
    }


def _usage_delta(
    total: dict[str, int],
    previous: dict[str, int] | None,
) -> dict[str, int]:
    if previous is None or any(total[key] < previous[key] for key in total):
        return dict(total)
    return {key: total[key] - previous[key] for key in total}


def _empty_usage() -> dict[str, int]:
    return {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
    }


def _turn_key(secret: bytes, payload: dict[str, object], timestamp: str | None) -> str:
    raw = _text(payload.get("turn_id")) or _text(payload.get("turnId")) or timestamp or "unknown"
    return _local_hmac(secret, f"turn:{raw}")


def _local_hmac(secret: bytes, value: str) -> str:
    digest = hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"local-hmac:{digest}"


def _model_configuration_id(provider: str, model: str, effort: str) -> str:
    return f"codex:{provider.casefold()}:{model.casefold()}:{effort.casefold()}"


def _provider_id(value: str) -> str:
    normalized = value.strip().casefold().replace(" ", "-").replace("_", "-")
    return normalized or "unknown"


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _current_turn(state: dict[str, object]) -> dict[str, object] | None:
    current = state.get("current_turn")
    return current if isinstance(current, dict) else None


def _duration_ms(start: str | None, end: str | None) -> int | None:
    start_value = _timestamp_value(start)
    end_value = _timestamp_value(end)
    if start_value <= 0 or end_value < start_value:
        return None
    return round((end_value - start_value) * 1000)


def _timestamp_value(value: object) -> float:
    if not isinstance(value, str) or not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _from_epoch(value: float) -> str:
    return _iso(datetime.fromtimestamp(value, timezone.utc))


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _optional_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _optional_nonnegative_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _nonnegative_int(value: object) -> int:
    return _optional_nonnegative_int(value) or 0


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None
