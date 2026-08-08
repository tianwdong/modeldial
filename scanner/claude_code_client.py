from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import shutil
import subprocess
from typing import Callable

from .bounded_subprocess import (
    BoundedSubprocessOutputError,
    DEFAULT_SUBPROCESS_OUTPUT_LIMIT_BYTES,
    run_bounded_process,
)
from .models import CLAUDE_CODE_REASONING_EFFORTS
from .process_environment import build_child_environment


CLAUDE_CODE_LOGIN_TIMEOUT_SECONDS = 10
CLAUDE_CODE_EXEC_TIMEOUT_SECONDS = 300
CLAUDE_CODE_OUTPUT_LIMIT_BYTES = DEFAULT_SUBPROCESS_OUTPUT_LIMIT_BYTES
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class ClaudeCodeResult:
    text: str
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    total_cost_usd: float | None
    execution_trace: dict[str, object]


class ClaudeCodeError(RuntimeError):
    def __init__(
        self,
        category: str,
        message: str,
        execution_trace: dict[str, object],
    ) -> None:
        super().__init__(message)
        self.category = category
        self.execution_trace = execution_trace


def resolve_claude_code_executable() -> str | None:
    return shutil.which("claude")


def check_claude_code_login(
    *,
    timeout_seconds: int = CLAUDE_CODE_LOGIN_TIMEOUT_SECONDS,
    runner: CommandRunner | None = None,
) -> None:
    executable = resolve_claude_code_executable()
    if executable is None:
        raise ClaudeCodeError(
            "not_installed",
            "未找到 Claude Code CLI，请先安装后再导入。",
            _execution_trace(
                started_at_utc=None,
                timeout_seconds=timeout_seconds,
                terminal_state="executable_not_found",
            ),
        )

    bounded_timeout_seconds = max(1, int(timeout_seconds))
    started_at_utc = _utc_timestamp()
    try:
        completed = run_bounded_process(
            [executable, "auth", "status"],
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=bounded_timeout_seconds,
            env=build_child_environment(),
            output_limit_bytes=CLAUDE_CODE_OUTPUT_LIMIT_BYTES,
            runner=runner,
        )
    except BoundedSubprocessOutputError as exc:
        trace = _execution_trace(
            started_at_utc=started_at_utc,
            timeout_seconds=bounded_timeout_seconds,
            terminal_state="output_limit_exceeded",
            stdout=exc.stdout,
            stderr=exc.stderr,
        )
        trace["output_limit_bytes"] = exc.output_limit_bytes
        trace["output_total_bytes"] = exc.total_output_bytes
        raise ClaudeCodeError(
            "output_limit_exceeded",
            "Claude Code 登录状态输出超出限制。",
            trace,
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ClaudeCodeError(
            "timeout",
            "Claude Code 登录状态校验超时。",
            _execution_trace(
                started_at_utc=started_at_utc,
                timeout_seconds=bounded_timeout_seconds,
                terminal_state="timeout",
                stdout=exc.stdout or exc.output,
                stderr=exc.stderr,
            ),
        ) from None
    except OSError:
        raise ClaudeCodeError(
            "not_installed",
            "未找到 Claude Code CLI，请先安装后再导入。",
            _execution_trace(
                started_at_utc=started_at_utc,
                timeout_seconds=bounded_timeout_seconds,
                terminal_state="process_start_failed",
            ),
        ) from None

    payload = _parse_json_object(completed.stdout)
    if payload.get("loggedIn") is True:
        return
    if payload:
        terminal_state = "not_logged_in"
    else:
        terminal_state = "invalid_login_status"
    raise ClaudeCodeError(
        "authentication_required" if terminal_state == "not_logged_in" else "login_check_failed",
        "Claude Code 登录态不可用，请先运行 claude auth login。"
        if terminal_state == "not_logged_in"
        else "Claude Code 登录状态校验失败。",
        _execution_trace(
            started_at_utc=started_at_utc,
            timeout_seconds=bounded_timeout_seconds,
            terminal_state=terminal_state,
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        ),
    )


def run_claude_code_prompt(
    prompt: str,
    model: str,
    scan_profile: str,
    *,
    timeout_seconds: int = CLAUDE_CODE_EXEC_TIMEOUT_SECONDS,
    evaluation_id: str | None = None,
    runner: CommandRunner | None = None,
) -> ClaudeCodeResult:
    bounded_timeout_seconds = max(1, int(timeout_seconds))
    reasoning_effort = scan_profile.strip().lower()
    if reasoning_effort not in CLAUDE_CODE_REASONING_EFFORTS:
        raise ClaudeCodeError(
            "unsupported_reasoning_effort",
            "Claude Code 仅支持 low、medium、high 三档推理强度。",
            _execution_trace(
                started_at_utc=None,
                timeout_seconds=bounded_timeout_seconds,
                terminal_state="unsupported_reasoning_effort",
                evaluation_id=evaluation_id,
                prompt=prompt,
            ),
        )
    executable = resolve_claude_code_executable()
    if executable is None:
        raise ClaudeCodeError(
            "not_installed",
            "未找到 Claude Code CLI，请先安装后再扫描。",
            _execution_trace(
                started_at_utc=None,
                timeout_seconds=bounded_timeout_seconds,
                terminal_state="executable_not_found",
                evaluation_id=evaluation_id,
                prompt=prompt,
            ),
        )

    started_at_utc = _utc_timestamp()
    command = [
        executable,
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--tools",
        "",
        "--max-turns",
        "1",
        "--model",
        model,
        "--effort",
        reasoning_effort,
    ]
    try:
        completed = run_bounded_process(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=bounded_timeout_seconds,
            env=build_child_environment(),
            output_limit_bytes=CLAUDE_CODE_OUTPUT_LIMIT_BYTES,
            runner=runner,
        )
    except BoundedSubprocessOutputError as exc:
        trace = _execution_trace(
            started_at_utc=started_at_utc,
            timeout_seconds=bounded_timeout_seconds,
            terminal_state="output_limit_exceeded",
            stdout=exc.stdout,
            stderr=exc.stderr,
            evaluation_id=evaluation_id,
            prompt=prompt,
            reasoning_effort=reasoning_effort,
        )
        trace["output_limit_bytes"] = exc.output_limit_bytes
        trace["output_total_bytes"] = exc.total_output_bytes
        raise ClaudeCodeError(
            "output_limit_exceeded",
            "Claude Code 输出超出限制。",
            trace,
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ClaudeCodeError(
            "timeout",
            "Claude Code 调用超时。",
            _execution_trace(
                started_at_utc=started_at_utc,
                timeout_seconds=bounded_timeout_seconds,
                terminal_state="timeout",
                stdout=exc.stdout or exc.output,
                stderr=exc.stderr,
                evaluation_id=evaluation_id,
                prompt=prompt,
                reasoning_effort=reasoning_effort,
            ),
        ) from None
    except OSError:
        raise ClaudeCodeError(
            "not_installed",
            "未找到 Claude Code CLI，请先安装后再扫描。",
            _execution_trace(
                started_at_utc=started_at_utc,
                timeout_seconds=bounded_timeout_seconds,
                terminal_state="process_start_failed",
                evaluation_id=evaluation_id,
                prompt=prompt,
                reasoning_effort=reasoning_effort,
            ),
        ) from None

    trace = _execution_trace(
        started_at_utc=started_at_utc,
        timeout_seconds=bounded_timeout_seconds,
        terminal_state="completed_response" if completed.returncode == 0 else "process_error",
        stdout=completed.stdout,
        stderr=completed.stderr,
        returncode=completed.returncode,
        evaluation_id=evaluation_id,
        prompt=prompt,
        reasoning_effort=reasoning_effort,
    )
    if completed.returncode != 0:
        raise ClaudeCodeError("runtime_error", "Claude Code 调用失败。", trace)

    payloads = _parse_json_lines(completed.stdout)
    final_result = next(
        (
            payload
            for payload in reversed(payloads)
            if payload.get("type") == "result"
        ),
        None,
    )
    if (
        final_result is None
        or final_result.get("is_error") is True
        or final_result.get("subtype") not in {None, "success"}
    ):
        raise ClaudeCodeError("invalid_response", "Claude Code 返回格式无效。", trace)
    text = final_result.get("result")
    if not isinstance(text, str) or not text.strip():
        raise ClaudeCodeError("invalid_response", "Claude Code 返回格式无效。", trace)

    usage_payload = _last_assistant_usage(payloads)
    return ClaudeCodeResult(
        text=text,
        input_tokens=_to_int(usage_payload.get("input_tokens")),
        cached_input_tokens=_to_int(usage_payload.get("cache_read_input_tokens")),
        output_tokens=_to_int(usage_payload.get("output_tokens")),
        total_cost_usd=_to_float(final_result.get("total_cost_usd")),
        execution_trace=trace,
    )


def _last_assistant_usage(payloads: list[dict[str, object]]) -> dict[str, object]:
    for payload in reversed(payloads):
        if payload.get("type") != "assistant":
            continue
        message = payload.get("message")
        if not isinstance(message, dict):
            continue
        usage = message.get("usage")
        if isinstance(usage, dict):
            return usage
    return {}


def _parse_json_object(value: object) -> dict[str, object]:
    try:
        payload = json.loads(_as_text(value).strip())
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _parse_json_lines(value: object) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for line in _as_text(value).splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def _execution_trace(
    *,
    started_at_utc: str | None,
    timeout_seconds: int,
    terminal_state: str,
    stdout: object = "",
    stderr: object = "",
    returncode: int | None = None,
    evaluation_id: str | None = None,
    prompt: str | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, object]:
    stdout_text = _as_text(stdout)
    stderr_text = _as_text(stderr)
    trace: dict[str, object] = {
        "correlation_mode": "claude_code_cli_stream_json",
        "timeout_seconds": max(1, int(timeout_seconds)),
        "terminal_state": terminal_state,
        "stdout_bytes": len(stdout_text.encode("utf-8", errors="replace")),
        "stderr_bytes": len(stderr_text.encode("utf-8", errors="replace")),
        "stream_event_count": len(_parse_json_lines(stdout_text)),
    }
    if started_at_utc is not None:
        trace["started_at_utc"] = started_at_utc
        trace["finished_at_utc"] = _utc_timestamp()
    if evaluation_id is not None:
        trace["evaluation_id"] = evaluation_id
    if prompt is not None:
        trace["prompt_sha256"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
    if returncode is not None:
        trace["process_returncode"] = returncode
    if reasoning_effort is not None:
        trace["reasoning_effort"] = reasoning_effort
    return trace


def _as_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    return ""


def _to_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _to_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
