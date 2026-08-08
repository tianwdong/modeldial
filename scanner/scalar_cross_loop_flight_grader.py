from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from .bounded_subprocess import BoundedSubprocessOutputError, run_bounded_process
from .cross_loop_singleflight_grader import (
    SOURCE_FILE,
    WORKER_OUTPUT_LIMIT_BYTES,
    _load_module,
    _materialize,
    _normalize_patch,
    _run_case,
    _validate_source,
)
from .frozen_runtime import module_worker_command
from .scalar_cross_loop_flight_starter import STARTER_SOURCE
from .session_bundle_grader import _apply_patch


MAX_SCORE = 10
CASE_SPECS = (
    ("errors_fan_out_then_retry_fresh", "failure", "失败共享与重试"),
    (
        "cancelled_joiner_completion_and_retry_are_isolated",
        "failure",
        "取消完成与重试隔离",
    ),
    ("completion_during_joiner_registration", "registration", "跨循环注册竞态"),
    (
        "context_and_input_lifecycle",
        "context",
        "上下文与输入生命周期",
    ),
    (
        "cross_loop_cancelled_joiner_closes_immediately",
        "cancellation",
        "跨循环加入者取消",
    ),
    ("scalar_reentry_tracks_factory_context", "context", "工厂上下文重入"),
    ("cancelled_original_observer_is_local", "cancellation", "原观察者取消"),
    ("origin_cancel_keeps_cross_loop_joiner_alive", "cancellation", "跨循环原观察者取消"),
    (
        "detached_generation_cannot_remove_cross_loop_retry",
        "generation",
        "废弃代际隔离",
    ),
    (
        "completed_scalar_detaches_before_notification",
        "generation",
        "完成前摘除",
    ),
)
CATEGORY_LABELS = {
    "failure": "失败恢复",
    "registration": "跨循环注册",
    "cancellation": "取消隔离",
    "context": "依赖上下文",
    "generation": "代际生命周期",
}
_CUSTOM_CASE_ID = "context_and_input_lifecycle"
_CUSTOM_RESULT_MARKER = "__SCALAR_FLIGHT_RESULT__"


def _empty_clusters() -> list[dict[str, object]]:
    return [
        {
            "id": case_id,
            "label": label,
            "case_ids": [case_id],
            "points": 0,
            "max_points": 1,
            "passed": False,
        }
        for case_id, _, label in CASE_SPECS
    ]


def _empty_facets() -> dict[str, dict[str, object]]:
    return {
        category: {
            "label": label,
            "score": 0,
            "max_score": sum(spec_category == category for _, spec_category, _ in CASE_SPECS),
        }
        for category, label in CATEGORY_LABELS.items()
    }


def _context_and_input_lifecycle(module) -> None:
    async def scenario() -> None:
        runner = module.AsyncSingleFlight()
        invalid_calls = 0

        async def invalid_factory():
            nonlocal invalid_calls
            invalid_calls += 1
            return "invalid"

        try:
            await runner.run([], invalid_factory)
        except TypeError:
            pass
        else:
            raise AssertionError("unhashable key was accepted")
        assert invalid_calls == 0
        assert runner.info() == module.FlightInfo(0, 0, 0)

        release_child = asyncio.Event()
        child_holder: dict[str, asyncio.Task] = {}

        async def value_factory(value: str):
            return value

        async def outer_factory():
            async def detached_call():
                await release_child.wait()
                return await runner.run("x", lambda: value_factory("detached"))

            child_holder["task"] = asyncio.create_task(detached_call())
            return "outer"

        assert await runner.run("x", outer_factory) == "outer"
        assert runner.info() == module.FlightInfo(0, 0, 1)
        release_child.set()
        assert await asyncio.wait_for(child_holder["task"], 1) == "detached"
        assert runner.info() == module.FlightInfo(0, 0, 2)

        started = asyncio.Event()
        release_active = asyncio.Event()

        async def active_factory():
            started.set()
            await release_active.wait()
            return "active"

        active = asyncio.create_task(runner.run("y", active_factory))
        await asyncio.wait_for(started.wait(), 1)
        before_clear = runner.info()
        try:
            runner.clear()
        except RuntimeError:
            pass
        else:
            raise AssertionError("clear accepted an attached generation")
        assert runner.info() == before_clear
        release_active.set()
        assert await asyncio.wait_for(active, 1) == "active"
        runner.clear()
        assert runner.info() == module.FlightInfo(0, 0, 0)

    asyncio.run(scenario())


def _custom_case_worker(root: Path, case_id: str) -> int:
    try:
        candidate = _load_module(root / SOURCE_FILE, f"_scalar_flight_candidate_{case_id}")
        if case_id != _CUSTOM_CASE_ID:
            raise KeyError(case_id)
        _context_and_input_lifecycle(candidate)
        payload = {"passed": True}
    except BaseException as exc:
        payload = {"passed": False, "error": f"{type(exc).__name__}:{exc}"}
    print(_CUSTOM_RESULT_MARKER + json.dumps(payload, ensure_ascii=False))
    return 0


def _run_custom_case(root: Path, case_id: str) -> dict[str, object]:
    try:
        completed = run_bounded_process(
            module_worker_command(
                "scanner.scalar_cross_loop_flight_grader",
                "_case-worker",
                str(root),
                case_id,
                python_flags=("-B",),
            ),
            text=True,
            timeout=5.0,
            output_limit_bytes=WORKER_OUTPUT_LIMIT_BYTES,
            merge_stderr=True,
            runner=subprocess.run,
        )
    except subprocess.TimeoutExpired:
        return {"passed": False, "error": "TimeoutError:case exceeded 5 seconds"}
    except BoundedSubprocessOutputError:
        return {"passed": False, "error": "OutputLimitError:case output exceeded budget"}
    line = next(
        (
            item
            for item in reversed(completed.stdout.splitlines())
            if item.startswith(_CUSTOM_RESULT_MARKER)
        ),
        None,
    )
    if line is None:
        detail = completed.stderr.strip() or completed.stdout.strip() or "missing case result"
        return {"passed": False, "error": f"RuntimeError:{detail}"}
    return json.loads(line[len(_CUSTOM_RESULT_MARKER) :])


def grade_patch(patch_text: str) -> dict[str, object]:
    patch_format_ok = False
    patch_applies = False
    try:
        patched = _apply_patch(
            {SOURCE_FILE: STARTER_SOURCE},
            _normalize_patch(patch_text),
        )
        patch_format_ok = True
        patch_applies = True
        if set(patched) != {SOURCE_FILE}:
            raise ValueError("unexpected_file_set")
        _validate_source(patched)
    except BaseException as exc:
        error = f"{type(exc).__name__}:{exc}"
        if "not_unique" in error:
            patch_format_ok = True
        return {
            "status": "patch_apply_failed" if not patch_applies else "runner_error",
            "score": 0,
            "max_score": MAX_SCORE,
            "failure_details": [],
            "facets": _empty_facets(),
            "clusters": _empty_clusters(),
            "raw_score": 0,
            "raw_max_score": MAX_SCORE,
            "patch_format_ok": patch_format_ok,
            "patch_applies": patch_applies,
            "error": error,
        }

    failures: list[dict[str, str]] = []
    facets = _empty_facets()
    clusters: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="scalar-cross-loop-flight-grade-") as temp:
        root = Path(temp)
        _materialize(patched, root)
        for case_id, category, label in CASE_SPECS:
            result = (
                _run_custom_case(root, case_id)
                if case_id == _CUSTOM_CASE_ID
                else _run_case(root, case_id)
            )
            passed = bool(result.get("passed"))
            clusters.append(
                {
                    "id": case_id,
                    "label": label,
                    "case_ids": [case_id],
                    "points": 1 if passed else 0,
                    "max_points": 1,
                    "passed": passed,
                }
            )
            if passed:
                facets[category]["score"] = int(facets[category]["score"]) + 1
            else:
                failures.append(
                    {
                        "case_id": case_id,
                        "label": label,
                        "category": category,
                        "category_label": CATEGORY_LABELS[category],
                        "error": str(result.get("error", "RuntimeError:case failed")),
                    }
                )

    score = MAX_SCORE - len(failures)
    return {
        "status": "passed" if score == MAX_SCORE else "semantic_failed",
        "score": score,
        "max_score": MAX_SCORE,
        "failure_details": failures,
        "facets": facets,
        "clusters": clusters,
        "raw_score": score,
        "raw_max_score": MAX_SCORE,
        "patch_format_ok": True,
        "patch_applies": True,
    }


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[0] == "_case-worker":
        return _custom_case_worker(Path(argv[1]), argv[2])
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
