from __future__ import annotations

import os
import sys
from pathlib import Path


WORKER_FLAG = "--modeldial-worker"
PYTHON_CODE_FLAG = "--modeldial-python-code"


def is_frozen_runtime() -> bool:
    return bool(getattr(sys, "frozen", False))


def configure_frozen_tls_trust() -> None:
    if not is_frozen_runtime():
        return

    runtime_root_value = getattr(sys, "_MEIPASS", None)
    if not isinstance(runtime_root_value, str) or not runtime_root_value:
        raise RuntimeError("frozen runtime root is unavailable")

    from certifi import where

    runtime_root = Path(runtime_root_value).resolve()
    certificate_bundle = Path(where()).resolve()
    try:
        certificate_bundle.relative_to(runtime_root)
    except ValueError as exc:
        raise RuntimeError("frozen CA bundle is outside the runtime root") from exc
    if not certificate_bundle.is_file():
        raise RuntimeError("frozen CA bundle is unavailable")
    os.environ.setdefault("SSL_CERT_FILE", str(certificate_bundle))


def module_worker_command(
    module_name: str,
    *arguments: str,
    python_flags: tuple[str, ...] = (),
) -> list[str]:
    if is_frozen_runtime():
        return [sys.executable, WORKER_FLAG, module_name, *arguments]
    return [sys.executable, *python_flags, "-m", module_name, *arguments]


def python_code_worker_command(code: str, *arguments: str) -> list[str]:
    if is_frozen_runtime():
        return [sys.executable, PYTHON_CODE_FLAG, code, *arguments]
    return [str(Path(sys.executable).resolve()), "-B", "-S", "-c", code, *arguments]


def dispatch_frozen_worker(arguments: list[str]) -> int | None:
    if not is_frozen_runtime() or not arguments:
        return None
    if arguments[0] == PYTHON_CODE_FLAG and len(arguments) >= 2:
        sys.argv = ["-c", *arguments[2:]]
        current_directory = os.getcwd()
        if current_directory not in sys.path:
            sys.path.insert(0, current_directory)
        namespace = {
            "__name__": "__main__",
            "__file__": "<string>",
            "__package__": None,
        }
        exec(compile(arguments[1], "<string>", "exec"), namespace)
        return 0
    if len(arguments) < 2 or arguments[0] != WORKER_FLAG:
        return None
    module_name = arguments[1]
    worker_arguments = arguments[2:]
    if module_name == "scanner.endpoint_client":
        from .endpoint_client import _isolated_worker_main

        return _isolated_worker_main() if worker_arguments == ["--execute-request"] else 2
    if module_name == "scanner.cross_loop_singleflight_grader":
        from .cross_loop_singleflight_grader import main

        return main(worker_arguments)
    if module_name == "scanner.scalar_cross_loop_flight_grader":
        from .scalar_cross_loop_flight_grader import main

        return main(worker_arguments)
    if module_name == "scanner.candidate_worker":
        from .candidate_worker import main

        return main(worker_arguments)
    return 2
