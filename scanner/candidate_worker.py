from __future__ import annotations

import json
import sys
from typing import Any

from . import graders
from .candidate_sandbox import RESULT_MARKER


class _ResultSink:
    def __init__(self) -> None:
        self.value: dict[str, object] | None = None

    def put(self, value: Any) -> None:
        if isinstance(value, dict):
            self.value = value


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        return 2
    worker_name = arguments[0]
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("payload_not_object")
        result = _run_worker(worker_name, payload)
    except BaseException as exc:
        result = {
            "score": 0,
            "max_score": 0,
            "failure_details": [],
            "error": f"{type(exc).__name__}:{exc}",
        }
    print(RESULT_MARKER + json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


def _run_worker(worker_name: str, payload: dict[str, object]) -> dict[str, object]:
    sink = _ResultSink()
    if worker_name == "python_function":
        graders._python_function_worker(
            str(payload["source"]),
            str(payload["function_name"]),
            str(payload["test_suite"]),
            sink,
        )
    elif worker_name == "unified_diff_patch":
        graders._unified_diff_patch_worker(
            str(payload["diff_text"]),
            str(payload["test_suite"]),
            sink,
        )
    elif worker_name == "search_replace_patch":
        graders._search_replace_patch_worker(
            str(payload["patch_text"]),
            str(payload["test_suite"]),
            sink,
        )
    elif worker_name == "session_bundle_patch":
        graders._session_bundle_patch_worker(str(payload["patch_text"]), sink)
    elif worker_name == "cross_loop_singleflight_patch":
        graders._cross_loop_singleflight_patch_worker(str(payload["patch_text"]), sink)
    elif worker_name == "scalar_cross_loop_flight_patch":
        graders._scalar_cross_loop_flight_patch_worker(str(payload["patch_text"]), sink)
    else:
        raise ValueError("unknown_worker")
    if sink.value is None:
        raise RuntimeError("worker_no_result")
    return sink.value


if __name__ == "__main__":
    raise SystemExit(main())
