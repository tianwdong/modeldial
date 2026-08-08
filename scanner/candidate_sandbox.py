from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .bounded_subprocess import (
    BoundedSubprocessOutputError,
    run_bounded_process,
)
from .black_box_regression_grader import (
    _sandbox_environment,
    _sandbox_profile,
    SANDBOX_EXECUTABLE,
)
from .frozen_runtime import is_frozen_runtime, module_worker_command


RESULT_MARKER = "__MODELDIAL_CANDIDATE_RESULT__"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCANNER_ROOT = Path(__file__).resolve().parent
OUTPUT_TAIL_BYTES = 64 * 1024
CANDIDATE_OUTPUT_LIMIT_BYTES = 256 * 1024


def _validated_backend_root() -> tuple[Path | None, str]:
    configured = os.environ.get("MODELDIAL_BACKEND_ROOT", "").strip()
    if not configured:
        if is_frozen_runtime():
            return None, "sandbox_unavailable:backend_root_missing"
        return None, "ok"
    try:
        backend_root = Path(configured).expanduser().resolve(strict=True)
        scanner_root = (backend_root / "scanner").resolve(strict=True)
    except (OSError, RuntimeError):
        return None, "sandbox_unavailable:backend_root_invalid"
    if (
        not backend_root.is_dir()
        or not scanner_root.is_dir()
        or scanner_root.parent != backend_root
        or not (scanner_root / "pricing_snapshot.json").is_file()
    ):
        return None, "sandbox_unavailable:backend_root_invalid"
    return backend_root, "ok"


def run_sandboxed_candidate_worker(
    worker_name: str,
    payload: dict[str, object],
    timeout_seconds: float,
    *,
    allow_process_fork: bool = False,
) -> tuple[dict[str, object] | None, str]:
    """Run untrusted candidate grading code under the macOS Seatbelt profile.

    Candidate source and patches travel through stdin only.  A missing or
    unusable sandbox is an explicit failure rather than a fallback to an
    unsandboxed Python process.
    """

    if SANDBOX_EXECUTABLE is None:
        return None, "sandbox_unavailable"
    backend_root, backend_status = _validated_backend_root()
    if backend_status != "ok":
        return None, backend_status

    try:
        with tempfile.TemporaryDirectory(prefix="candidate-grader-") as temp:
            root = Path(temp)
            (root / "scratch").mkdir()
            worker_command = module_worker_command("scanner.candidate_worker", worker_name)
            worker_command[0] = str(Path(worker_command[0]).resolve())
            read_roots = [SCANNER_ROOT]
            if backend_root is not None:
                read_roots.append((backend_root / "scanner").resolve())
            command = [
                SANDBOX_EXECUTABLE,
                "-p",
                _sandbox_profile(
                    root,
                    read_roots=tuple(read_roots),
                    allow_process_fork=allow_process_fork,
                ),
                *worker_command,
            ]
            environment = _sandbox_environment(root)
            environment["PYTHONPATH"] = str(PROJECT_ROOT)
            if backend_root is not None:
                environment["MODELDIAL_BACKEND_ROOT"] = str(backend_root)
            encoded_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            completed = run_bounded_process(
                command,
                cwd=str(root),
                env=environment,
                input=encoded_payload.encode("utf-8"),
                timeout=timeout_seconds,
                output_limit_bytes=CANDIDATE_OUTPUT_LIMIT_BYTES,
                merge_stderr=True,
                runner=subprocess.run,
            )
            output = completed.stdout or b""
            if isinstance(output, str):
                output = output.encode("utf-8", errors="replace")
            detail = output[-OUTPUT_TAIL_BYTES:].decode("utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except BoundedSubprocessOutputError:
        return None, "sandbox_unavailable:output_limit_exceeded"
    except OSError as exc:
        return None, f"sandbox_unavailable:{type(exc).__name__}"

    result = _decode_result(detail)
    if result is None or completed.returncode != 0:
        return None, "sandbox_unavailable:worker_exit"
    return result, "ok"


def _decode_result(output: str) -> dict[str, object] | None:
    for line in reversed(output.splitlines()):
        if not line.startswith(RESULT_MARKER):
            continue
        try:
            value = json.loads(line[len(RESULT_MARKER) :])
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None
    return None
