from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
import threading
import time
from typing import Callable, ContextManager, Protocol, Sequence

from .bounded_subprocess import (
    BoundedOutputBudget,
    DEFAULT_SUBPROCESS_OUTPUT_LIMIT_BYTES,
)
from .codex_runtime import resolve_codex_executable
from .process_environment import build_child_environment


DEFAULT_TIMEOUT_SECONDS = 8.0
MAX_STDOUT_LINE_CHARS = 4 * 1024 * 1024
CODEX_ACCOUNT_OUTPUT_LIMIT_BYTES = DEFAULT_SUBPROCESS_OUTPUT_LIMIT_BYTES
_READ_CHUNK_BYTES = 8192


class CodexAccountError(RuntimeError):
    pass


class CodexAccountOutputLimitError(CodexAccountError):
    pass


class _Session(Protocol):
    def request(self, method: str, params: object = None) -> object: ...


SessionFactory = Callable[[Path, float], ContextManager[_Session]]


class _ReaderFailure:
    pass


class _OutputLimitExceeded:
    pass


class _CodexAppServerSession:
    def __init__(self, binary: Path, timeout_seconds: float) -> None:
        self.binary = binary
        self.timeout_seconds = timeout_seconds
        self.process: subprocess.Popen[bytes] | None = None
        self.output_queue: Queue[str | None | _ReaderFailure | _OutputLimitExceeded] = Queue()
        self.request_id = 0
        self.output_budget = BoundedOutputBudget(CODEX_ACCOUNT_OUTPUT_LIMIT_BYTES)
        self._reader_threads: list[threading.Thread] = []

    def __enter__(self) -> _CodexAppServerSession:
        try:
            self.process = subprocess.Popen(
                [str(self.binary), "app-server", "--listen", "stdio://"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                env=build_child_environment(),
            )
        except OSError as exc:
            raise CodexAccountError("无法启动 Codex app-server") from exc
        self._reader_threads = [
            threading.Thread(target=self._read_output, daemon=True),
            threading.Thread(target=self._read_stderr, daemon=True),
        ]
        for reader in self._reader_threads:
            reader.start()
        try:
            self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "modeldial",
                        "title": "modeldial",
                        "version": "0.1.0",
                    }
                },
            )
        except Exception:
            self.__exit__()
            raise
        return self

    def __exit__(self, *_args: object) -> None:
        process = self.process
        if process is None:
            return
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                    process.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        for reader in self._reader_threads:
            reader.join(timeout=1)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass

    def request(self, method: str, params: object = None) -> object:
        process = self.process
        if process is None or process.stdin is None:
            raise CodexAccountError("Codex app-server 输入不可用")
        self.request_id += 1
        request_id = self.request_id
        payload: dict[str, object] = {"id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        try:
            process.stdin.write(
                (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
            )
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise CodexAccountError("Codex app-server 已退出") from exc

        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            if self.output_budget.exceeded:
                raise CodexAccountOutputLimitError(
                    "Codex app-server 输出超出限制"
                )
            try:
                item = self.output_queue.get(
                    timeout=max(0.0, deadline - time.monotonic())
                )
            except Empty:
                break
            if self.output_budget.exceeded:
                raise CodexAccountOutputLimitError(
                    "Codex app-server 输出超出限制"
                )
            if isinstance(item, _OutputLimitExceeded):
                raise CodexAccountOutputLimitError(
                    "Codex app-server 输出超出限制"
                )
            if isinstance(item, _ReaderFailure):
                raise CodexAccountError("Codex app-server 输出行过大")
            if item is None:
                break
            try:
                response = json.loads(item)
            except json.JSONDecodeError:
                continue
            if not isinstance(response, dict) or response.get("id") != request_id:
                continue
            if response.get("error") is not None:
                raise CodexAccountError(f"{method} 请求失败")
            return response.get("result")
        raise CodexAccountError(f"{method} 请求超时")

    def _read_output(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            self.output_queue.put(None)
            return
        pending = bytearray()
        line_overflow = False
        try:
            while True:
                chunk = process.stdout.read(_READ_CHUNK_BYTES)
                if not chunk:
                    break
                bounded, exceeded = self.output_budget.accept(chunk)
                pending.extend(bounded)
                if exceeded:
                    self.output_queue.put(_OutputLimitExceeded())
                    return
                while True:
                    try:
                        line_end = pending.index(10)
                    except ValueError:
                        break
                    line = bytes(pending[: line_end + 1])
                    del pending[: line_end + 1]
                    if line_overflow or len(line) > MAX_STDOUT_LINE_CHARS:
                        self.output_queue.put(_ReaderFailure())
                    else:
                        self.output_queue.put(
                            line.decode("utf-8", errors="replace")
                        )
                    line_overflow = False
                if not line_overflow and len(pending) > MAX_STDOUT_LINE_CHARS:
                    pending.clear()
                    line_overflow = True
            if pending and not line_overflow:
                if len(pending) > MAX_STDOUT_LINE_CHARS:
                    self.output_queue.put(_ReaderFailure())
                else:
                    self.output_queue.put(
                        pending.decode("utf-8", errors="replace")
                    )
        finally:
            self.output_queue.put(None)

    def _read_stderr(self) -> None:
        process = self.process
        if process is None or process.stderr is None:
            return
        try:
            while True:
                chunk = process.stderr.read(_READ_CHUNK_BYTES)
                if not chunk:
                    return
                _bounded, exceeded = self.output_budget.accept(chunk)
                if exceeded:
                    self.output_queue.put(_OutputLimitExceeded())
                    return
        except (OSError, ValueError):
            return


def read_codex_account_snapshot(
    *,
    binary_candidates: Sequence[Path] | None = None,
    codex_home: Path | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    captured_at: str | None = None,
    session_factory: SessionFactory = _CodexAppServerSession,
) -> dict[str, object]:
    if binary_candidates is None:
        resolved_codex_home = codex_home or _default_codex_home()
        if not resolved_codex_home.is_dir():
            raise CodexAccountError("Codex 用户目录尚未初始化")
        candidates = _default_binary_candidates()
    else:
        candidates = tuple(binary_candidates)
    if not candidates:
        raise CodexAccountError("未找到可用的 Codex CLI")
    failures: list[Exception] = []
    for binary in candidates:
        try:
            with session_factory(binary, timeout_seconds) as session:
                account = session.request(
                    "account/read",
                    {"refreshToken": False},
                )
                return _read_optional_account_capabilities(
                    session,
                    account,
                    captured_at=captured_at or _timestamp(),
                )
        except Exception as exc:
            failures.append(exc)
    raise CodexAccountError("Codex 账号快照不可用") from failures[-1]


def _read_optional_account_capabilities(
    session: _Session,
    account_result: object,
    *,
    captured_at: str,
) -> dict[str, object]:
    account_payload = _dict(account_result)
    account = _dict(account_payload.get("account"))
    account_type = _account_type(account.get("type")) if account else "none"
    login_state = "authenticated" if account else "unauthenticated"
    plan_type = _text(account.get("planType")) if account else None
    snapshot: dict[str, object] = {
        "schema_version": 1,
        "captured_at": captured_at,
        "source": "codex_app_server",
        "account_type": account_type,
        "login_state": login_state,
        "requires_openai_auth": bool(account_payload.get("requiresOpenaiAuth", False)),
        "plan_type": plan_type,
        "quota_status": "not_applicable",
        "quota_windows": [],
        "usage_status": "not_applicable",
        "usage_summary": None,
        "daily_usage": [],
        "unavailable_capabilities": [],
    }
    if account_type != "chatgpt":
        return snapshot

    unavailable: list[str] = []
    try:
        rate_limits = session.request("account/rateLimits/read")
    except CodexAccountOutputLimitError:
        raise
    except Exception:
        snapshot["quota_status"] = "unavailable"
        unavailable.append("rate_limits")
    else:
        windows = _quota_windows(rate_limits)
        snapshot["quota_status"] = "available" if windows else "unavailable"
        snapshot["quota_windows"] = windows
        if not windows:
            unavailable.append("rate_limits")

    try:
        usage = session.request("account/usage/read")
    except CodexAccountOutputLimitError:
        raise
    except Exception:
        snapshot["usage_status"] = "unavailable"
        unavailable.append("account_usage")
    else:
        snapshot["usage_status"] = "available"
        summary, daily = _account_usage(usage)
        snapshot["usage_summary"] = summary
        snapshot["daily_usage"] = daily
    snapshot["unavailable_capabilities"] = unavailable
    return snapshot


def _quota_windows(payload: object) -> list[dict[str, object]]:
    body = _dict(payload)
    by_limit_id = _dict(body.get("rateLimitsByLimitId"))
    if by_limit_id:
        snapshots = [
            (str(key), _dict(value))
            for key, value in sorted(by_limit_id.items(), key=lambda item: str(item[0]))
            if _dict(value)
        ]
    else:
        rate_limits = _dict(body.get("rateLimits"))
        snapshots = [
            (_text(rate_limits.get("limitId")) or "default", rate_limits)
        ] if rate_limits else []
    windows: list[dict[str, object]] = []
    for limit_key, rate_limits in snapshots:
        limit_id = _text(rate_limits.get("limitId")) or limit_key
        for slot in ("primary", "secondary"):
            window = _dict(rate_limits.get(slot))
            if not window:
                continue
            duration_minutes = _optional_int(window.get("windowDurationMins"))
            windows.append(
                {
                    "window_id": _quota_window_id(limit_id, duration_minutes, slot),
                    "label": _window_label(duration_minutes, slot),
                    "limit_id": limit_id,
                    "limit_name": _text(rate_limits.get("limitName")),
                    "source_slot": slot,
                    "used_percent": _optional_number(window.get("usedPercent")),
                    "window_seconds": (
                        duration_minutes * 60 if duration_minutes is not None else None
                    ),
                    "resets_at": _epoch_timestamp(window.get("resetsAt")),
                }
            )
    return windows


def _quota_window_id(
    limit_id: str,
    duration_minutes: int | None,
    source_slot: str,
) -> str:
    if duration_minutes is not None and duration_minutes > 0:
        return f"{limit_id}:{duration_minutes}m"
    return f"{limit_id}:{source_slot}"


def _account_usage(
    payload: object,
) -> tuple[dict[str, int | None], list[dict[str, object]]]:
    body = _dict(payload)
    raw_summary = _dict(body.get("summary"))
    summary = {
        "lifetime_tokens": _optional_int(raw_summary.get("lifetimeTokens")),
        "peak_daily_tokens": _optional_int(raw_summary.get("peakDailyTokens")),
        "longest_running_turn_seconds": _optional_int(
            raw_summary.get("longestRunningTurnSec")
        ),
        "current_streak_days": _optional_int(raw_summary.get("currentStreakDays")),
        "longest_streak_days": _optional_int(raw_summary.get("longestStreakDays")),
    }
    raw_daily = body.get("dailyUsageBuckets")
    daily = []
    if isinstance(raw_daily, list):
        for item in raw_daily:
            bucket = _dict(item)
            start_date = _text(bucket.get("startDate"))
            tokens = _optional_int(bucket.get("tokens"))
            if start_date and tokens is not None:
                daily.append({"start_date": start_date, "tokens": tokens})
    return summary, daily


def _default_binary_candidates() -> tuple[Path, ...]:
    executable = resolve_codex_executable()
    return (Path(executable),) if executable else ()


def _default_codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def _account_type(value: object) -> str:
    return {
        "apiKey": "api_key",
        "chatgpt": "chatgpt",
        "amazonBedrock": "amazon_bedrock",
    }.get(value, "unknown")


def _window_label(duration_minutes: int | None, window_id: str) -> str:
    if duration_minutes == 300:
        return "5h"
    if duration_minutes == 10080:
        return "weekly"
    if duration_minutes and duration_minutes % 1440 == 0:
        return f"{duration_minutes // 1440}d"
    if duration_minutes and duration_minutes % 60 == 0:
        return f"{duration_minutes // 60}h"
    return window_id


def _epoch_timestamp(value: object) -> str | None:
    epoch = _optional_number(value)
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")
    except (OSError, OverflowError, ValueError):
        return None


def _optional_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
