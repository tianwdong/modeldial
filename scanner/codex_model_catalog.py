from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
import threading
import time
from typing import Optional, Sequence

from .bounded_subprocess import (
    BoundedOutputBudget,
    DEFAULT_SUBPROCESS_OUTPUT_LIMIT_BYTES,
)
from .codex_runtime import resolve_codex_executable
from .process_environment import build_child_environment


DEFAULT_TIMEOUT_SECONDS = 20.0
CODEX_CATALOG_OUTPUT_LIMIT_BYTES = DEFAULT_SUBPROCESS_OUTPUT_LIMIT_BYTES
_OUTPUT_LIMIT_SENTINEL = object()
_READ_CHUNK_BYTES = 8192
_STDERR_TAIL_BYTES = 8 * 1024


class CodexCatalogError(RuntimeError):
    pass


@dataclass(frozen=True)
class CodexCatalogCandidate:
    model_id: str
    model_display_name: str
    scan_profile: str
    is_default: bool


def discover_codex_model_catalog(
    *,
    binary_candidates: Optional[Sequence[Path]] = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[CodexCatalogCandidate, ...]:
    candidates = tuple(binary_candidates or _default_binary_candidates())
    if not candidates:
        raise CodexCatalogError("未找到可用的 Codex CLI")

    errors: list[str] = []
    for binary in candidates:
        try:
            return _discover_with_binary(binary, timeout_seconds=timeout_seconds)
        except CodexCatalogError as exc:
            errors.append(f"{binary}: {exc}")
    raise CodexCatalogError("；".join(errors))


def parse_model_list_page(payload: object) -> tuple[CodexCatalogCandidate, ...]:
    if not isinstance(payload, dict):
        raise CodexCatalogError("Codex 返回了无效的模型目录")
    raw_models = payload.get("data")
    if not isinstance(raw_models, list):
        raise CodexCatalogError("Codex 模型目录缺少 data")

    candidates: list[CodexCatalogCandidate] = []
    for raw_model in raw_models:
        if not isinstance(raw_model, dict):
            continue
        model_id = _text(raw_model.get("model") or raw_model.get("id"))
        if not model_id or model_id == "codex-auto-review":
            continue
        display_name = _text(raw_model.get("displayName")) or model_id
        default_effort = (_text(raw_model.get("defaultReasoningEffort")) or "").lower()
        raw_efforts = raw_model.get("supportedReasoningEfforts")
        if not isinstance(raw_efforts, list):
            continue
        seen_efforts: set[str] = set()
        for raw_effort in raw_efforts:
            if not isinstance(raw_effort, dict):
                continue
            effort = (_text(raw_effort.get("reasoningEffort")) or "").lower()
            if not effort or effort in seen_efforts:
                continue
            seen_efforts.add(effort)
            candidates.append(
                CodexCatalogCandidate(
                    model_id=model_id,
                    model_display_name=display_name,
                    scan_profile=effort,
                    is_default=effort == default_effort,
                )
            )
    return tuple(candidates)


def _default_binary_candidates() -> tuple[Path, ...]:
    executable = resolve_codex_executable()
    return (Path(executable),) if executable else ()


def _discover_with_binary(
    binary: Path,
    *,
    timeout_seconds: float,
) -> tuple[CodexCatalogCandidate, ...]:
    try:
        process = subprocess.Popen(
            [str(binary), "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            env=build_child_environment(),
        )
    except OSError as exc:
        raise CodexCatalogError("无法启动 Codex app-server") from exc

    try:
        output_queue: Queue[object] = Queue()
        output_budget = BoundedOutputBudget(CODEX_CATALOG_OUTPUT_LIMIT_BYTES)
        stderr_tail = bytearray()
        reader = threading.Thread(
            target=_read_output,
            args=(process, output_queue, output_budget),
            daemon=True,
        )
        stderr_reader = threading.Thread(
            target=_read_stderr,
            args=(process, output_budget, stderr_tail),
            daemon=True,
        )
        reader.start()
        stderr_reader.start()
        _send_request(
            process,
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {
                        "name": "modeldial",
                        "title": "modeldial",
                        "version": "0.1.0",
                    }
                },
            },
        )
        _wait_for_response(
            process,
            output_queue,
            1,
            timeout_seconds,
            output_budget=output_budget,
            stderr_tail=stderr_tail,
        )

        cursor: Optional[str] = None
        request_id = 2
        all_candidates: list[CodexCatalogCandidate] = []
        while True:
            params: dict[str, object] = {
                "includeHidden": False,
                "limit": 100,
            }
            if cursor:
                params["cursor"] = cursor
            _send_request(
                process,
                {"id": request_id, "method": "model/list", "params": params},
            )
            response = _wait_for_response(
                process,
                output_queue,
                request_id,
                timeout_seconds,
                output_budget=output_budget,
                stderr_tail=stderr_tail,
            )
            result = response.get("result")
            all_candidates.extend(parse_model_list_page(result))
            cursor = _text(result.get("nextCursor")) if isinstance(result, dict) else None
            if not cursor:
                break
            request_id += 1
        if not all_candidates:
            raise CodexCatalogError("Codex 未返回可用模型")
        return tuple(all_candidates)
    finally:
        had_active_exception = sys.exc_info()[0] is not None
        _stop_process(process)
        reader.join(timeout=1)
        stderr_reader.join(timeout=1)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
        if output_budget.exceeded and not had_active_exception:
            raise CodexCatalogError("Codex 模型目录输出超出限制")


def _send_request(process: subprocess.Popen[bytes], payload: dict[str, object]) -> None:
    if process.stdin is None:
        raise CodexCatalogError("Codex app-server 输入不可用")
    try:
        process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        process.stdin.flush()
    except (BrokenPipeError, OSError) as exc:
        raise CodexCatalogError(_process_error(process)) from exc


def _read_output(
    process: subprocess.Popen[bytes],
    output_queue: Queue[object],
    output_budget: BoundedOutputBudget,
) -> None:
    if process.stdout is None:
        output_queue.put(None)
        return
    pending = bytearray()
    try:
        while True:
            chunk = process.stdout.read(_READ_CHUNK_BYTES)
            if not chunk:
                break
            bounded, exceeded = output_budget.accept(chunk)
            pending.extend(bounded)
            while True:
                try:
                    line_end = pending.index(10)
                except ValueError:
                    break
                line = bytes(pending[: line_end + 1])
                del pending[: line_end + 1]
                output_queue.put(line.decode("utf-8", errors="replace"))
            if exceeded:
                output_queue.put(_OUTPUT_LIMIT_SENTINEL)
                break
        if pending and not output_budget.exceeded:
            output_queue.put(pending.decode("utf-8", errors="replace"))
    finally:
        output_queue.put(None)


def _read_stderr(
    process: subprocess.Popen[bytes],
    output_budget: BoundedOutputBudget,
    stderr_tail: bytearray,
) -> None:
    if process.stderr is None:
        return
    try:
        while True:
            chunk = process.stderr.read(_READ_CHUNK_BYTES)
            if not chunk:
                return
            bounded, exceeded = output_budget.accept(chunk)
            if bounded:
                stderr_tail.extend(bounded)
                del stderr_tail[:-_STDERR_TAIL_BYTES]
            if exceeded:
                return
    except (OSError, ValueError):
        return


def _wait_for_response(
    process: subprocess.Popen[bytes],
    output_queue: Queue[object],
    request_id: int,
    timeout_seconds: float,
    *,
    output_budget: BoundedOutputBudget,
    stderr_tail: bytearray,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if output_budget.exceeded:
            raise CodexCatalogError("Codex 模型目录输出超出限制")
        remaining = max(0.0, deadline - time.monotonic())
        try:
            line = output_queue.get(timeout=min(remaining, 0.05))
        except Empty:
            continue
        if line is _OUTPUT_LIMIT_SENTINEL:
            raise CodexCatalogError("Codex 模型目录输出超出限制")
        if line is None:
            break
        if not isinstance(line, str):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or payload.get("id") != request_id:
            continue
        if payload.get("error") is not None:
            raise CodexCatalogError("Codex 模型目录请求失败")
        return payload
    raise CodexCatalogError(
        _process_error(
            process,
            fallback="Codex 模型目录请求超时",
            stderr_tail=stderr_tail,
        )
    )


def _process_error(
    process: subprocess.Popen[bytes],
    fallback: str = "Codex app-server 已退出",
    *,
    stderr_tail: bytearray | None = None,
) -> str:
    if stderr_tail:
        detail = bytes(stderr_tail).decode("utf-8", errors="replace").strip().splitlines()
        if detail:
            return detail[-1]
    return fallback


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _text(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None
