"""Portable subprocess helpers with a combined stdout/stderr budget.

The standard ``subprocess.run(..., capture_output=True)`` API keeps reading
until the child exits and therefore has no memory bound.  Scanner CLIs and
candidate graders are external processes, so a noisy or compromised child
must be stopped as soon as its combined output exceeds a small, explicit
budget.
"""

from __future__ import annotations

import io
import subprocess
import threading
import time
from typing import Callable, Sequence


DEFAULT_SUBPROCESS_OUTPUT_LIMIT_BYTES = 512 * 1024
_READ_CHUNK_BYTES = 8192
_PROCESS_STOP_GRACE_SECONDS = 0.2
_ORIGINAL_SUBPROCESS_RUN = subprocess.run


class BoundedSubprocessOutputError(subprocess.SubprocessError):
    """The child exceeded the combined stdout/stderr budget."""

    def __init__(
        self,
        cmd: Sequence[str],
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        output_limit_bytes: int,
        total_output_bytes: int,
    ) -> None:
        self.cmd = list(cmd)
        self.stdout = stdout
        self.stderr = stderr
        self.output_limit_bytes = output_limit_bytes
        self.total_output_bytes = total_output_bytes
        super().__init__(
            f"subprocess output exceeded {output_limit_bytes} bytes"
        )


class BoundedOutputBudget:
    """Thread-safe combined output counter used by interactive readers."""

    def __init__(self, limit_bytes: int) -> None:
        self.limit_bytes = max(1, int(limit_bytes))
        self._lock = threading.Lock()
        self._total_output_bytes = 0
        self._exceeded = threading.Event()

    @property
    def exceeded(self) -> bool:
        return self._exceeded.is_set()

    @property
    def total_output_bytes(self) -> int:
        with self._lock:
            return self._total_output_bytes

    def accept(self, chunk: bytes) -> tuple[bytes, bool]:
        """Return the bounded prefix and whether this chunk crossed the limit."""

        if not chunk:
            return b"", False
        with self._lock:
            previous = self._total_output_bytes
            self._total_output_bytes += len(chunk)
            remaining = max(0, self.limit_bytes - previous)
            bounded = chunk[:remaining]
            exceeded = self._total_output_bytes > self.limit_bytes
            if exceeded:
                self._exceeded.set()
            return bounded, exceeded


def run_bounded_process(
    args: Sequence[str],
    *,
    input: str | bytes | None = None,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    output_limit_bytes: int = DEFAULT_SUBPROCESS_OUTPUT_LIMIT_BYTES,
    merge_stderr: bool = False,
    text: bool = False,
    encoding: str = "utf-8",
    errors: str = "replace",
    runner: Callable[..., subprocess.CompletedProcess] | None = None,
) -> subprocess.CompletedProcess:
    """Run a process with a combined stdout/stderr memory and process budget.

    ``runner`` is an injection seam retained for deterministic unit tests.  A
    custom runner is called with the historical ``subprocess.run`` arguments;
    production calls (where ``runner`` is omitted or is the original
    ``subprocess.run``) always use the bounded Popen implementation.
    """

    limit_bytes = max(1, int(output_limit_bytes))
    if runner is not None and runner is not _ORIGINAL_SUBPROCESS_RUN:
        if merge_stderr:
            sink = io.BytesIO()
            completed = runner(
                list(args),
                input=input,
                stdout=sink,
                stderr=subprocess.STDOUT,
                text=text,
                encoding=encoding,
                errors=errors,
                timeout=timeout,
                env=env,
                cwd=cwd,
                check=False,
            )
            captured = getattr(completed, "stdout", None)
            if not isinstance(captured, (bytes, str)):
                captured = sink.getvalue()
            completed = subprocess.CompletedProcess(
                getattr(completed, "args", list(args)),
                getattr(completed, "returncode", 1),
                stdout=captured,
                stderr=getattr(completed, "stderr", b""),
            )
            return _enforce_injected_output_budget(completed, args, limit_bytes)
        completed = runner(
            list(args),
            input=input,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=text,
            encoding=encoding,
            errors=errors,
            timeout=timeout,
            env=env,
            cwd=cwd,
        )
        return _enforce_injected_output_budget(completed, args, limit_bytes)

    input_bytes = _encode_input(input, encoding, errors)
    process = subprocess.Popen(
        list(args),
        # Preserve subprocess.run's inherited-stdin behavior when no payload
        # is supplied.  This also keeps macOS Seatbelt workers from needing a
        # separate /dev/null open permission.
        stdin=subprocess.PIPE if input_bytes is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
        cwd=cwd,
        env=env,
    )
    budget = BoundedOutputBudget(limit_bytes)
    stdout_buffer = bytearray()
    stderr_buffer = bytearray()
    stop_event = threading.Event()

    def read_stream(stream: object, buffer: bytearray) -> None:
        if stream is None:
            return
        try:
            while not stop_event.is_set():
                chunk = stream.read(_READ_CHUNK_BYTES)  # type: ignore[attr-defined]
                if not chunk:
                    return
                bounded, exceeded = budget.accept(chunk)
                buffer.extend(bounded)
                if exceeded:
                    stop_event.set()
                    return
        except (OSError, ValueError):
            return

    readers = (
        threading.Thread(
            target=read_stream,
            args=(process.stdout, stdout_buffer),
            daemon=True,
        ),
    )
    if not merge_stderr:
        readers += (
            threading.Thread(
                target=read_stream,
                args=(process.stderr, stderr_buffer),
                daemon=True,
            ),
        )
    for reader in readers:
        reader.start()

    writer: threading.Thread | None = None
    if input_bytes is not None and process.stdin is not None:
        def write_input() -> None:
            try:
                process.stdin.write(input_bytes)  # type: ignore[arg-type]
                process.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                try:
                    process.stdin.close()
                except (OSError, ValueError):
                    pass

        writer = threading.Thread(target=write_input, daemon=True)
        writer.start()

    timed_out = False
    deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
    try:
        while process.poll() is None:
            if budget.exceeded:
                stop_event.set()
                _terminate_process(process)
                break
            if deadline is not None and time.monotonic() >= deadline:
                timed_out = True
                stop_event.set()
                _terminate_process(process)
                break
            time.sleep(0.01)
        if budget.exceeded and process.poll() is None:
            _terminate_process(process)
        if process.poll() is None:
            _terminate_process(process)
    finally:
        if process.poll() is None:
            stop_event.set()
            _terminate_process(process)
        for reader in readers:
            reader.join(timeout=1)
        if writer is not None:
            writer.join(timeout=1)
        if process.stdin is not None:
            try:
                process.stdin.close()
            except (OSError, ValueError):
                pass
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass

    stdout = bytes(stdout_buffer)
    stderr = bytes(stderr_buffer)
    if budget.exceeded:
        raise BoundedSubprocessOutputError(
            args,
            stdout=stdout,
            stderr=stderr,
            output_limit_bytes=limit_bytes,
            total_output_bytes=budget.total_output_bytes,
        )
    if timed_out:
        raise subprocess.TimeoutExpired(
            list(args),
            timeout,
            output=stdout,
            stderr=stderr,
        )
    return subprocess.CompletedProcess(
        list(args),
        process.returncode,
        stdout=_decode_output(stdout, text=text, encoding=encoding, errors=errors),
        stderr=_decode_output(stderr, text=text, encoding=encoding, errors=errors),
    )


def _encode_input(value: str | bytes | None, encoding: str, errors: str) -> bytes | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value
    return value.encode(encoding, errors=errors)


def _decode_output(
    value: bytes,
    *,
    text: bool,
    encoding: str,
    errors: str,
) -> bytes | str:
    return value.decode(encoding, errors=errors) if text else value


def _enforce_injected_output_budget(
    completed: subprocess.CompletedProcess,
    args: Sequence[str],
    limit_bytes: int,
) -> subprocess.CompletedProcess:
    stdout = _as_bytes(completed.stdout)
    stderr = _as_bytes(completed.stderr)
    total = len(stdout) + len(stderr)
    if total > limit_bytes:
        remaining = max(0, limit_bytes - len(stdout))
        raise BoundedSubprocessOutputError(
            args,
            stdout=stdout[:limit_bytes],
            stderr=stderr[:remaining],
            output_limit_bytes=limit_bytes,
            total_output_bytes=total,
        )
    return completed


def _as_bytes(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8", errors="replace")
    return b""


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=_PROCESS_STOP_GRACE_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            return
        try:
            process.wait(timeout=_PROCESS_STOP_GRACE_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            pass
