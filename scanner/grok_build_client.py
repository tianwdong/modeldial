from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import shutil
import subprocess
from typing import Callable

from .bounded_subprocess import (
    BoundedSubprocessOutputError,
    DEFAULT_SUBPROCESS_OUTPUT_LIMIT_BYTES,
    run_bounded_process,
)
from .models import (
    GROK_BUILD_4_5_REASONING_EFFORTS,
    GROK_BUILD_4_6_MODEL_ID,
    GROK_BUILD_4_6_REASONING_EFFORTS,
)
from .process_environment import build_child_environment


GROK_BUILD_LOGIN_TIMEOUT_SECONDS = 10
GROK_BUILD_EXEC_TIMEOUT_SECONDS = 300
GROK_BUILD_OUTPUT_LIMIT_BYTES = DEFAULT_SUBPROCESS_OUTPUT_LIMIT_BYTES
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class GrokBuildResult:
    text: str
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    total_cost_usd: float | None
    execution_trace: dict[str, object]


class GrokBuildError(RuntimeError):
    def __init__(
        self,
        category: str,
        message: str,
        execution_trace: dict[str, object],
    ) -> None:
        super().__init__(message)
        self.category = category
        self.execution_trace = execution_trace


def resolve_grok_build_executable() -> str | None:
    executable = shutil.which("grok")
    if executable:
        return executable
    for candidate in ("/opt/homebrew/bin/grok", "/usr/local/bin/grok"):
        if os.path.exists(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def check_grok_build_login(
    *,
    timeout_seconds: int = GROK_BUILD_LOGIN_TIMEOUT_SECONDS,
    runner: CommandRunner | None = None,
) -> None:
    executable = resolve_grok_build_executable()
    if executable is None:
        raise GrokBuildError(
            "not_installed",
            "未找到 Grok Build CLI，请先安装后再导入。",
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
            [executable, "models"],
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=bounded_timeout_seconds,
            env=build_child_environment(),
            output_limit_bytes=GROK_BUILD_OUTPUT_LIMIT_BYTES,
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
        raise GrokBuildError(
            "output_limit_exceeded",
            "Grok Build 登录状态输出超出限制。",
            trace,
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise GrokBuildError(
            "timeout",
            "Grok Build 登录状态校验超时。",
            _execution_trace(
                started_at_utc=started_at_utc,
                timeout_seconds=bounded_timeout_seconds,
                terminal_state="timeout",
                stdout=exc.stdout or exc.output,
                stderr=exc.stderr,
            ),
        ) from None
    except OSError:
        raise GrokBuildError(
            "not_installed",
            "未找到 Grok Build CLI，请先安装后再导入。",
            _execution_trace(
                started_at_utc=started_at_utc,
                timeout_seconds=bounded_timeout_seconds,
                terminal_state="process_start_failed",
            ),
        ) from None

    login_status = _parse_grok_models_auth_status(completed.stdout)
    if completed.returncode != 0 or login_status is not True:
        terminal_state = (
            "login_check_failed"
            if completed.returncode != 0 or login_status is False
            else "login_status_unavailable"
        )
        raise GrokBuildError(
            "authentication_required",
            "Grok Build 登录态不可用，请先运行 grok login。",
            _execution_trace(
                started_at_utc=started_at_utc,
                timeout_seconds=bounded_timeout_seconds,
                terminal_state=terminal_state,
                stdout=completed.stdout,
                stderr=completed.stderr,
                returncode=completed.returncode,
            ),
        )


def run_grok_build_prompt(
    prompt: str,
    model: str,
    scan_profile: str,
    *,
    timeout_seconds: int = GROK_BUILD_EXEC_TIMEOUT_SECONDS,
    evaluation_id: str | None = None,
    runner: CommandRunner | None = None,
) -> GrokBuildResult:
    bounded_timeout_seconds = max(1, int(timeout_seconds))
    reasoning_effort = scan_profile.strip().lower()
    if reasoning_effort == "default":
        reasoning_effort = "high"
    supported_reasoning_efforts = (
        GROK_BUILD_4_6_REASONING_EFFORTS
        if model == GROK_BUILD_4_6_MODEL_ID
        else GROK_BUILD_4_5_REASONING_EFFORTS
    )
    if reasoning_effort not in supported_reasoning_efforts:
        raise GrokBuildError(
            "unsupported_reasoning_effort",
            "该 Grok 模型不支持此推理强度。",
            _execution_trace(
                started_at_utc=None,
                timeout_seconds=bounded_timeout_seconds,
                terminal_state="unsupported_reasoning_effort",
                evaluation_id=evaluation_id,
                prompt=prompt,
            ),
        )
    executable = resolve_grok_build_executable()
    if executable is None:
        raise GrokBuildError(
            "not_installed",
            "未找到 Grok Build CLI，请先安装后再扫描。",
            _execution_trace(
                started_at_utc=None,
                timeout_seconds=timeout_seconds,
                terminal_state="executable_not_found",
                evaluation_id=evaluation_id,
                prompt=prompt,
            ),
        )

    started_at_utc = _utc_timestamp()
    command = _prompt_command(
        executable,
        prompt,
        model,
        reasoning_effort,
    )
    try:
        completed = run_bounded_process(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=bounded_timeout_seconds,
            env=build_child_environment(),
            output_limit_bytes=GROK_BUILD_OUTPUT_LIMIT_BYTES,
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
        raise GrokBuildError(
            "output_limit_exceeded",
            "Grok Build 输出超出限制。",
            trace,
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise GrokBuildError(
            "timeout",
            "Grok Build 调用超时。",
            _execution_trace(
                started_at_utc=started_at_utc,
                timeout_seconds=bounded_timeout_seconds,
                terminal_state="timeout",
                stdout=exc.stdout or exc.output,
                stderr=exc.stderr,
                evaluation_id=evaluation_id,
                prompt=prompt,
            ),
        ) from None
    except OSError:
        raise GrokBuildError(
            "not_installed",
            "未找到 Grok Build CLI，请先安装后再扫描。",
            _execution_trace(
                started_at_utc=started_at_utc,
                timeout_seconds=bounded_timeout_seconds,
                terminal_state="process_start_failed",
                evaluation_id=evaluation_id,
                prompt=prompt,
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
        raise GrokBuildError("runtime_error", "Grok Build 调用失败。", trace)

    payload = _parse_json_object(completed.stdout)
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise GrokBuildError(
            "invalid_response",
            "Grok Build 返回格式无效。",
            trace,
        )
    usage = payload.get("usage")
    usage_payload = usage if isinstance(usage, dict) else {}
    return GrokBuildResult(
        text=text,
        input_tokens=_to_int(usage_payload.get("input_tokens")),
        cached_input_tokens=_to_int(usage_payload.get("cache_read_input_tokens")),
        output_tokens=_to_int(usage_payload.get("output_tokens")),
        reasoning_tokens=_to_int(usage_payload.get("reasoning_tokens")),
        total_cost_usd=_to_float(payload.get("total_cost_usd")),
        execution_trace=trace,
    )


def _prompt_command(
    executable: str,
    prompt: str,
    model: str,
    reasoning_effort: str,
) -> list[str]:
    command = [
        executable,
        "--no-auto-update",
        "--no-memory",
        "--no-subagents",
        "--disable-web-search",
        "--tools",
        "",
        "--deny",
        "MCPTool",
    ]
    command.extend(
        [
            "--permission-mode",
            "dontAsk",
            "-p",
            prompt,
            "-m",
            model,
            "--reasoning-effort",
            reasoning_effort,
            "--output-format",
            "json",
        ]
    )
    return command


def _parse_json_object(value: object) -> dict[str, object]:
    try:
        payload = json.loads(_as_text(value).strip())
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _parse_grok_models_auth_status(value: object) -> bool | None:
    """Parse only the official auth banner emitted by ``grok models``."""
    text = _as_text(value)
    for line in text.splitlines():
        normalized = line.strip().lower()
        if normalized.startswith("you are not authenticated"):
            return False
        if normalized.startswith("you are logged in with"):
            return True
    return None


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
        "correlation_mode": "grok_build_cli_json",
        "timeout_seconds": max(1, int(timeout_seconds)),
        "terminal_state": terminal_state,
        "stdout_bytes": len(stdout_text.encode("utf-8", errors="replace")),
        "stderr_bytes": len(stderr_text.encode("utf-8", errors="replace")),
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
