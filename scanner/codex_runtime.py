from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unicodedata

from .session_registry import record_modeldial_session_end
from .bounded_subprocess import (
    BoundedSubprocessOutputError,
    DEFAULT_SUBPROCESS_OUTPUT_LIMIT_BYTES,
    run_bounded_process,
)
from .process_environment import build_child_environment


CODEX_EXEC_TIMEOUT_SECONDS = 300
CODEX_OUTPUT_LIMIT_BYTES = DEFAULT_SUBPROCESS_OUTPUT_LIMIT_BYTES
DESKTOP_CODEX_BINARY = "/Applications/ChatGPT.app/Contents/Resources/codex"
MODELDIAL_SCAN_EFFORT_ENV = "MODELDIAL_SCAN_EFFORT"
MODELDIAL_SCAN_SESSION_ENV = "MODELDIAL_SCAN_SESSION"


class CodexPromptExecutionError(RuntimeError):
    """A local Codex invocation that did not yield a complete JSONL turn."""

    def __init__(self, message: str, execution_trace: dict[str, object]) -> None:
        super().__init__(message)
        self.execution_trace = execution_trace


def resolve_codex_executable() -> str | None:
    if os.path.exists(DESKTOP_CODEX_BINARY) and os.access(
        DESKTOP_CODEX_BINARY,
        os.X_OK,
    ):
        return DESKTOP_CODEX_BINARY

    executable = shutil.which("codex")
    if executable:
        return executable
    for candidate in ("/opt/homebrew/bin/codex", "/usr/local/bin/codex"):
        if os.path.exists(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _parse_codex_json_output(output: str) -> tuple[str, dict, bool]:
    summary = _summarize_codex_json_output(output)
    return (
        str(summary["final_text"]),
        dict(summary["usage"]),
        bool(summary["turn_completed"]),
    )


def _summarize_codex_json_output(output: str) -> dict[str, object]:
    final_text = ""
    usage: dict = {}
    turn_completed = False
    event_types: dict[str, int] = {}
    provider_ids: dict[str, list[str]] = {}
    event_count = 0
    agent_message_received = False
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_count += 1
        event_type = event.get("type")
        if isinstance(event_type, str):
            event_types[event_type] = event_types.get(event_type, 0) + 1
        _collect_provider_ids(event, provider_ids)
        if event_type == "item.completed":
            item = event.get("item", {})
            if isinstance(item, dict) and item.get("type") == "agent_message":
                text = item.get("text")
                if isinstance(text, str):
                    final_text = text
                agent_message_received = True
        elif event_type == "turn.completed":
            usage = event.get("usage") or {}
            turn_completed = True
    return {
        "final_text": final_text,
        "usage": usage if isinstance(usage, dict) else {},
        "turn_completed": turn_completed,
        "agent_message_received": agent_message_received,
        "event_count": event_count,
        "event_types": event_types,
        "provider_ids": provider_ids,
    }


def _collect_provider_ids(value: object, found: dict[str, list[str]]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"request_id", "response_id", "thread_id", "turn_id"}:
                if isinstance(item, (str, int)):
                    values = found.setdefault(key, [])
                    normalized = str(item)
                    if normalized not in values and len(values) < 8:
                        values.append(normalized)
            _collect_provider_ids(item, found)
    elif isinstance(value, list):
        for item in value:
            _collect_provider_ids(item, found)


def _as_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    return ""


def _transport_markers(stderr: str) -> list[str]:
    normalized = stderr.lower()
    markers: list[str] = []
    checks = (
        ("websocket_disconnected", ("stream disconnected", "websocket closed")),
        ("http_fallback", ("falling back to http",)),
        ("network_error", ("connection reset", "connection refused", "network error")),
        (
            "tls_certificate_error",
            ("certificate verify failed", "temporalvalidity", "invalidcertificate"),
        ),
    )
    for marker, needles in checks:
        if any(needle in normalized for needle in needles):
            markers.append(marker)
    return markers


def _build_execution_trace(
    *,
    evaluation_id: str | None,
    prompt: str,
    timeout_seconds: int,
    started_at_utc: str,
    stdout: object,
    stderr: object,
    terminal_state: str,
    returncode: int | None = None,
) -> dict[str, object]:
    stdout_text = _as_text(stdout)
    stderr_text = _as_text(stderr)
    summary = _summarize_codex_json_output(stdout_text)
    trace: dict[str, object] = {
        "evaluation_id": evaluation_id,
        "correlation_mode": "local_timing_fingerprint",
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16],
        "started_at_utc": started_at_utc,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "timeout_seconds": timeout_seconds,
        "terminal_state": terminal_state,
        "stdout_event_count": summary["event_count"],
        "event_types": summary["event_types"],
        "agent_message_received": summary["agent_message_received"],
        "turn_completed_received": summary["turn_completed"],
        "provider_ids": summary["provider_ids"],
        "stdout_bytes": len(stdout_text.encode("utf-8", errors="replace")),
        "stderr_bytes": len(stderr_text.encode("utf-8", errors="replace")),
        "transport_markers": _transport_markers(stderr_text),
    }
    if returncode is not None:
        trace["process_returncode"] = returncode
    return trace


def _record_modeldial_terminal_sessions(trace: dict[str, object]) -> None:
    provider_ids = trace.get("provider_ids")
    if not isinstance(provider_ids, dict):
        return
    thread_ids = provider_ids.get("thread_id")
    if not isinstance(thread_ids, list):
        return
    for thread_id in thread_ids:
        if isinstance(thread_id, str) and thread_id:
            record_modeldial_session_end(thread_id)


def _completed_timeout_output(
    exc: subprocess.TimeoutExpired,
) -> tuple[str, dict] | None:
    output = exc.stdout or exc.output or ""
    final_text, usage, turn_completed = _parse_codex_json_output(_as_text(output))
    if not turn_completed or not final_text:
        return None
    return final_text, usage


def run_codex_prompt(
    prompt: str,
    model: str | None,
    effort: str,
    *,
    timeout_seconds: int = CODEX_EXEC_TIMEOUT_SECONDS,
    evaluation_id: str | None = None,
    return_trace: bool = False,
):
    executable = resolve_codex_executable()
    if not executable:
        raise RuntimeError("找不到 codex 可执行文件，请确认已安装并加入 PATH。")

    command = [
        executable,
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--ephemeral",
        "-s",
        "read-only",
        "--disable",
        "memories",
        "-c",
        f"model_reasoning_effort={effort}",
    ]
    if model:
        command += ["-m", model]

    bounded_timeout_seconds = max(1, int(timeout_seconds))
    started_at_utc = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    cloud_api_key = os.environ.get("MODELDIAL_CLOUD_API_KEY", "").strip()
    child_overrides = {
        MODELDIAL_SCAN_EFFORT_ENV: effort,
        MODELDIAL_SCAN_SESSION_ENV: "1",
    }
    if cloud_api_key:
        child_overrides["CODEX_API_KEY"] = cloud_api_key
    child_env = build_child_environment(child_overrides)

    try:
        with tempfile.TemporaryDirectory(prefix="modeldial-evaluation-") as evaluation_root:
            process = run_bounded_process(
                command,
                input=prompt,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=bounded_timeout_seconds,
                env=child_env,
                cwd=evaluation_root,
                output_limit_bytes=CODEX_OUTPUT_LIMIT_BYTES,
                # Keep the existing subprocess.run patch seam used by the
                # scanner tests while production uses the bounded Popen path.
                runner=subprocess.run,
            )
    except BoundedSubprocessOutputError as exc:
        trace = _build_execution_trace(
            evaluation_id=evaluation_id,
            prompt=prompt,
            timeout_seconds=bounded_timeout_seconds,
            started_at_utc=started_at_utc,
            stdout=exc.stdout,
            stderr=exc.stderr,
            terminal_state="output_limit_exceeded",
        )
        trace["output_limit_bytes"] = exc.output_limit_bytes
        trace["output_total_bytes"] = exc.total_output_bytes
        _record_modeldial_terminal_sessions(trace)
        raise CodexPromptExecutionError(
            "codex exec output exceeded the configured limit",
            trace,
        ) from exc
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or exc.output or ""
        trace = _build_execution_trace(
            evaluation_id=evaluation_id,
            prompt=prompt,
            timeout_seconds=bounded_timeout_seconds,
            started_at_utc=started_at_utc,
            stdout=stdout,
            stderr=exc.stderr or "",
            terminal_state="timeout_without_completed_turn",
        )
        _record_modeldial_terminal_sessions(trace)
        completed = _completed_timeout_output(exc)
        if completed is None:
            raise CodexPromptExecutionError(
                f"codex exec timed out after {bounded_timeout_seconds}s",
                trace,
            ) from exc
        final_text, usage = completed
        trace["terminal_state"] = "completed_turn_recovered_after_timeout"
        result = (
            final_text,
            usage.get("input_tokens"),
            usage.get("cached_input_tokens"),
            usage.get("output_tokens"),
            usage.get("reasoning_output_tokens"),
        )
        return (*result, trace) if return_trace else result
    if process.returncode != 0:
        trace = _build_execution_trace(
            evaluation_id=evaluation_id,
            prompt=prompt,
            timeout_seconds=bounded_timeout_seconds,
            started_at_utc=started_at_utc,
            stdout=process.stdout,
            stderr=process.stderr,
            terminal_state="process_error",
            returncode=process.returncode,
        )
        _record_modeldial_terminal_sessions(trace)
        raise CodexPromptExecutionError("codex exec failed", trace)

    final_text, usage, turn_completed = _parse_codex_json_output(process.stdout)
    trace = _build_execution_trace(
        evaluation_id=evaluation_id,
        prompt=prompt,
        timeout_seconds=bounded_timeout_seconds,
        started_at_utc=started_at_utc,
        stdout=process.stdout,
        stderr=process.stderr,
        terminal_state=(
            "completed_turn" if turn_completed else "exited_without_completed_turn"
        ),
        returncode=process.returncode,
    )
    _record_modeldial_terminal_sessions(trace)
    if not turn_completed:
        raise CodexPromptExecutionError(
            "codex exec exited without a completed turn",
            trace,
        )

    result = (
        final_text,
        usage.get("input_tokens"),
        usage.get("cached_input_tokens"),
        usage.get("output_tokens"),
        usage.get("reasoning_output_tokens"),
    )
    return (*result, trace) if return_trace else result


def char_width(char: str) -> int:
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1


def display_width(text: str) -> int:
    return sum(char_width(char) for char in text)


def preview(text: str, limit: int = 40) -> str:
    flat = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", r"\n")
    if display_width(flat) <= limit:
        return flat

    result = []
    width = 0
    for char in flat:
        next_width = char_width(char)
        if width + next_width > limit - 3:
            break
        result.append(char)
        width += next_width
    return "".join(result) + "..."
