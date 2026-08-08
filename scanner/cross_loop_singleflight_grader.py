from __future__ import annotations

import ast
import asyncio
import contextlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .bounded_subprocess import (
    BoundedSubprocessOutputError,
    run_bounded_process,
)
from .frozen_runtime import module_worker_command

try:
    from scanner.cross_loop_singleflight_v2_starter import STARTER_SOURCE
except ModuleNotFoundError:  # Direct case-worker execution.
    from cross_loop_singleflight_v2_starter import STARTER_SOURCE


SOURCE_FILE = "async_flight.py"
STARTER_FILES = {
    SOURCE_FILE: STARTER_SOURCE
}
FACET_LABELS = {
    "scalar": "单值合并",
    "cross_loop": "跨循环共享",
    "cancellation": "取消隔离",
    "stream": "流式回放",
    "lifecycle": "生命周期",
}
CASES_PER_FACET = 4
RAW_MAX_SCORE = len(FACET_LABELS) * CASES_PER_FACET
WORKER_OUTPUT_LIMIT_BYTES = 256 * 1024
CLUSTERS = {
    "scalar_failure_recovery": (
        "errors_fan_out_then_retry_fresh",
        "cancelled_joiner_completion_and_retry_are_isolated",
    ),
    "scalar_completion_and_reentry": (
        "scalar_reentry_tracks_factory_context",
        "completed_scalar_detaches_before_notification",
    ),
    "cross_loop_registration": (
        "stream_replays_across_thread_loops",
        "completion_during_joiner_registration",
    ),
    "cross_loop_generation": (
        "origin_cancel_keeps_cross_loop_joiner_alive",
        "detached_generation_cannot_remove_cross_loop_retry",
    ),
    "joiner_cancellation": (
        "cancelled_scalar_joiner_is_local",
        "cross_loop_cancelled_joiner_closes_immediately",
    ),
    "observer_lifetime": (
        "cancelled_original_observer_is_local",
        "last_stream_subscriber_cancels_and_retry_is_fresh",
    ),
    "stream_error_replay": (
        "partial_items_precede_error_and_next_is_fresh",
        "late_stream_cancel_error_replays_buffer_before_failure",
    ),
    "stream_completion_and_reentry": (
        "stream_reentry_tracks_factory_context",
        "completed_stream_detaches_before_notification",
    ),
    "context_and_input_lifecycle": (
        "unhashable_keys_fail_before_user_code",
        "factory_context_expires_and_allowed_nesting_stays_independent",
    ),
    "abandoned_cleanup_isolation": (
        "cancelled_scalar_cleanup_cannot_join_replacement",
        "cancelled_stream_cleanup_cannot_join_replacement",
    ),
}
CLUSTER_LABELS = {
    "scalar_failure_recovery": "单值失败恢复",
    "scalar_completion_and_reentry": "单值完成与重入",
    "cross_loop_registration": "跨循环注册",
    "cross_loop_generation": "跨循环代际隔离",
    "joiner_cancellation": "加入者取消",
    "observer_lifetime": "观察者生命周期",
    "stream_error_replay": "流错误回放",
    "stream_completion_and_reentry": "流完成与重入",
    "context_and_input_lifecycle": "上下文与输入生命周期",
    "abandoned_cleanup_isolation": "废弃清理隔离",
}
MAX_SCORE = len(CLUSTERS)
ALLOWED_MODULES = {
    "__future__",
    "asyncio",
    "collections",
    "concurrent",
    "contextvars",
    "dataclasses",
    "threading",
    "typing",
}


class _Boom(RuntimeError):
    pass


@dataclass(frozen=True)
class Case:
    case_id: str
    facet: str
    run: Callable[[], None]


class _DiscardingTextSink:
    encoding = "utf-8"

    def write(self, value: str) -> int:
        return len(value)

    def flush(self) -> None:
        return None


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module



def _expect_raises(expected, operation: Callable[[], object]):
    try:
        operation()
    except expected as exc:
        return exc
    except BaseException as exc:
        raise AssertionError(
            f"expected {expected.__name__}, got {type(exc).__name__}: {exc}"
        ) from exc
    raise AssertionError(f"expected {expected.__name__}")


def _wait_until(predicate: Callable[[], bool], timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.002)
    raise AssertionError("condition timed out")


async def _consume(iterator) -> tuple[list[object], BaseException | None]:
    items: list[object] = []
    try:
        async for item in iterator:
            items.append(item)
    except BaseException as exc:
        return items, exc
    return items, None


def _build_base_cases(module) -> dict[str, Case]:
    cases: list[Case] = []

    def register(facet: str, case_id: str):
        def decorator(operation: Callable[[], None]):
            cases.append(Case(case_id=case_id, facet=facet, run=operation))
            return operation

        return decorator

    @register("scalar", "same_loop_duplicates_execute_once")
    def same_loop_duplicates_execute_once() -> None:
        async def scenario() -> None:
            runner = module.AsyncSingleFlight()
            started = asyncio.Event()
            release = asyncio.Event()
            calls = 0
            marker = object()

            async def factory():
                nonlocal calls
                calls += 1
                started.set()
                await release.wait()
                return marker

            leader = asyncio.create_task(runner.run("x", factory))
            await asyncio.wait_for(started.wait(), 1)
            joiners = [asyncio.create_task(runner.run("x", factory)) for _ in range(4)]
            for _ in range(100):
                if runner.info().total == 5:
                    break
                await asyncio.sleep(0)
            release.set()
            results = await asyncio.wait_for(asyncio.gather(leader, *joiners), 1)
            assert all(result is marker for result in results)
            assert calls == 1
            assert runner.info() == module.FlightInfo(0, 4, 5)

        asyncio.run(scenario())

    @register("scalar", "completed_results_are_fresh")
    def completed_results_are_fresh() -> None:
        async def scenario() -> None:
            runner = module.AsyncSingleFlight()
            calls = 0

            async def factory():
                nonlocal calls
                calls += 1
                return calls

            assert await runner.run("x", factory) == 1
            assert await runner.run("x", factory) == 2
            assert runner.info() == module.FlightInfo(0, 0, 2)

        asyncio.run(scenario())

    @register("scalar", "errors_fan_out_then_retry_fresh")
    def errors_fan_out_then_retry_fresh() -> None:
        async def scenario() -> None:
            runner = module.AsyncSingleFlight()
            started = asyncio.Event()
            release = asyncio.Event()
            calls = 0

            async def factory():
                nonlocal calls
                calls += 1
                if calls == 1:
                    started.set()
                    await release.wait()
                    raise _Boom("shared")
                return "fresh"

            leader = asyncio.create_task(runner.run("x", factory))
            await asyncio.wait_for(started.wait(), 1)
            joiner = asyncio.create_task(runner.run("x", factory))
            for _ in range(100):
                if runner.info().joined == 1:
                    break
                await asyncio.sleep(0)
            release.set()
            results = await asyncio.gather(leader, joiner, return_exceptions=True)
            assert all(isinstance(result, _Boom) and str(result) == "shared" for result in results)
            assert await runner.run("x", factory) == "fresh"
            assert calls == 2

        asyncio.run(scenario())

    @register("scalar", "factory_stays_on_leader_location")
    def factory_stays_on_leader_location() -> None:
        async def scenario() -> None:
            runner = module.AsyncSingleFlight()
            caller = (threading.get_ident(), id(asyncio.get_running_loop()))
            locations: list[tuple[int, int]] = []

            async def factory():
                locations.append((threading.get_ident(), id(asyncio.get_running_loop())))
                return 7

            assert await runner.run("x", factory) == 7
            assert locations == [caller]

        asyncio.run(scenario())

    @register("cross_loop", "scalar_shares_across_thread_loops")
    def scalar_shares_across_thread_loops() -> None:
        runner = module.AsyncSingleFlight()
        started = threading.Event()
        release = threading.Event()
        lock = threading.Lock()
        calls = 0

        async def factory():
            nonlocal calls
            with lock:
                calls += 1
            started.set()
            while not release.is_set():
                await asyncio.sleep(0.001)
            return "shared"

        with ThreadPoolExecutor(max_workers=2) as pool:
            leader = pool.submit(lambda: asyncio.run(runner.run("x", factory)))
            assert started.wait(1)
            joiner = pool.submit(lambda: asyncio.run(runner.run("x", factory)))
            try:
                _wait_until(lambda: runner.info().total == 2)
            finally:
                release.set()
            assert leader.result(timeout=1) == "shared"
            assert joiner.result(timeout=1) == "shared"
        assert calls == 1
        assert runner.info() == module.FlightInfo(0, 1, 2)

    @register("cross_loop", "cross_loop_error_cleans_for_retry")
    def cross_loop_error_cleans_for_retry() -> None:
        runner = module.AsyncSingleFlight()
        started = threading.Event()
        release = threading.Event()
        lock = threading.Lock()
        calls = 0

        async def factory():
            nonlocal calls
            with lock:
                calls += 1
                current = calls
            if current == 1:
                started.set()
                while not release.is_set():
                    await asyncio.sleep(0.001)
                raise _Boom("cross")
            return "fresh"

        with ThreadPoolExecutor(max_workers=2) as pool:
            leader = pool.submit(lambda: asyncio.run(runner.run("x", factory)))
            assert started.wait(1)
            joiner = pool.submit(lambda: asyncio.run(runner.run("x", factory)))
            try:
                _wait_until(lambda: runner.info().total == 2)
            finally:
                release.set()
            for future in (leader, joiner):
                exc = _expect_raises(_Boom, lambda future=future: future.result(timeout=1))
                assert str(exc) == "cross"
        assert asyncio.run(runner.run("x", factory)) == "fresh"
        assert calls == 2

    @register("cross_loop", "stream_replays_across_thread_loops")
    def stream_replays_across_thread_loops() -> None:
        runner = module.AsyncSingleFlight()
        first_ready = threading.Event()
        release = threading.Event()
        calls = 0
        lock = threading.Lock()

        async def factory():
            nonlocal calls
            with lock:
                calls += 1
            yield 1
            first_ready.set()
            while not release.is_set():
                await asyncio.sleep(0.001)
            yield 2

        with ThreadPoolExecutor(max_workers=2) as pool:
            leader = pool.submit(lambda: asyncio.run(_consume(runner.stream("x", factory))))
            assert first_ready.wait(1)
            joiner = pool.submit(lambda: asyncio.run(_consume(runner.stream("x", factory))))
            try:
                _wait_until(lambda: runner.info().total == 2)
            finally:
                release.set()
            assert leader.result(timeout=1) == ([1, 2], None)
            assert joiner.result(timeout=1) == ([1, 2], None)
        assert calls == 1

    @register("cross_loop", "completion_during_joiner_registration")
    def completion_during_joiner_registration() -> None:
        runner = module.AsyncSingleFlight()
        leader_started = threading.Event()
        release_leader = threading.Event()
        joiner_paused = threading.Event()
        resume_joiner = threading.Event()
        holder: dict[str, object] = {}
        calls = 0

        async def factory():
            nonlocal calls
            calls += 1
            leader_started.set()
            while not release_leader.is_set():
                await asyncio.sleep(0.001)
            return "shared"

        class PausingLoop(asyncio.SelectorEventLoop):
            def __init__(self) -> None:
                super().__init__()
                self.pause_next_future = True

            def create_future(self):
                if self.pause_next_future:
                    self.pause_next_future = False
                    joiner_paused.set()
                    assert resume_joiner.wait(1)
                return super().create_future()

        def run_joiner() -> object:
            loop = PausingLoop()
            asyncio.set_event_loop(loop)
            task = loop.create_task(runner.run("x", factory))
            holder["loop"] = loop
            holder["task"] = task
            try:
                return loop.run_until_complete(task)
            finally:
                asyncio.set_event_loop(None)
                loop.close()

        pool = ThreadPoolExecutor(max_workers=2)
        leader = pool.submit(lambda: asyncio.run(runner.run("x", factory)))
        assert leader_started.wait(1)
        joiner = pool.submit(run_joiner)
        assert joiner_paused.wait(1)
        release_leader.set()
        assert leader.result(timeout=1) == "shared"
        _wait_until(lambda: runner.info().active == 0)
        resume_joiner.set()
        try:
            result = joiner.result(timeout=0.5)
        except TimeoutError as exc:
            loop = holder["loop"]
            task = holder["task"]
            loop.call_soon_threadsafe(task.cancel)
            with contextlib.suppress(BaseException):
                joiner.result(timeout=0.5)
            raise AssertionError("joiner missed a flight that completed during registration") from exc
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        assert result == "shared"
        assert calls == 1

    @register("cancellation", "cancelled_scalar_joiner_is_local")
    def cancelled_scalar_joiner_is_local() -> None:
        async def scenario() -> None:
            runner = module.AsyncSingleFlight()
            started = asyncio.Event()
            release = asyncio.Event()
            calls = 0

            async def factory():
                nonlocal calls
                calls += 1
                started.set()
                await release.wait()
                return "ok"

            leader = asyncio.create_task(runner.run("x", factory))
            await asyncio.wait_for(started.wait(), 1)
            joiner = asyncio.create_task(runner.run("x", factory))
            for _ in range(100):
                if runner.info().joined == 1:
                    break
                await asyncio.sleep(0)
            joiner.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await joiner
            release.set()
            assert await asyncio.wait_for(leader, 1) == "ok"
            assert calls == 1

        asyncio.run(scenario())

    @register("cancellation", "cancelled_scalar_leader_fans_out")
    def cancelled_scalar_leader_fans_out() -> None:
        async def scenario() -> None:
            runner = module.AsyncSingleFlight()
            started = asyncio.Event()
            calls = 0

            async def factory():
                nonlocal calls
                calls += 1
                if calls == 1:
                    started.set()
                    await asyncio.Event().wait()
                return "fresh"

            leader = asyncio.create_task(runner.run("x", factory))
            await asyncio.wait_for(started.wait(), 1)
            joiner = asyncio.create_task(runner.run("x", factory))
            for _ in range(100):
                if runner.info().joined == 1:
                    break
                await asyncio.sleep(0)
            leader.cancel()
            results = await asyncio.gather(leader, joiner, return_exceptions=True)
            assert all(isinstance(result, asyncio.CancelledError) for result in results)
            assert runner.info().active == 0
            assert await runner.run("x", factory) == "fresh"
            assert calls == 2

        asyncio.run(scenario())

    @register("cancellation", "cancelled_stream_joiner_does_not_stop_producer")
    def cancelled_stream_joiner_does_not_stop_producer() -> None:
        async def scenario() -> None:
            runner = module.AsyncSingleFlight()
            first_ready = asyncio.Event()
            release = asyncio.Event()
            calls = 0

            async def factory():
                nonlocal calls
                calls += 1
                yield 1
                first_ready.set()
                await release.wait()
                yield 2

            leader = asyncio.create_task(_consume(runner.stream("x", factory)))
            await asyncio.wait_for(first_ready.wait(), 1)
            joiner = asyncio.create_task(_consume(runner.stream("x", factory)))
            for _ in range(100):
                if runner.info().joined == 1:
                    break
                await asyncio.sleep(0)
            joiner.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await joiner
            release.set()
            assert await asyncio.wait_for(leader, 1) == ([1, 2], None)
            assert calls == 1

        asyncio.run(scenario())

    @register("cancellation", "cross_loop_cancelled_joiner_closes_immediately")
    def cross_loop_cancelled_joiner_closes_immediately() -> None:
        runner = module.AsyncSingleFlight()
        started = threading.Event()
        release = threading.Event()
        cancel_joiner = threading.Event()

        async def factory():
            started.set()
            while not release.is_set():
                await asyncio.sleep(0.001)
            return "leader"

        async def join_then_cancel() -> str:
            task = asyncio.create_task(runner.run("x", factory))
            while not cancel_joiner.is_set():
                await asyncio.sleep(0.001)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                return "cancelled"
            raise AssertionError("joiner was not cancelled")

        with ThreadPoolExecutor(max_workers=2) as pool:
            leader = pool.submit(lambda: asyncio.run(runner.run("x", factory)))
            assert started.wait(1)
            joiner = pool.submit(lambda: asyncio.run(join_then_cancel()))
            _wait_until(lambda: runner.info().joined == 1)
            cancel_joiner.set()
            try:
                assert joiner.result(timeout=0.3) == "cancelled"
            finally:
                release.set()
            assert leader.result(timeout=1) == "leader"

    @register("stream", "same_loop_joiner_replays_from_zero")
    def same_loop_joiner_replays_from_zero() -> None:
        async def scenario() -> None:
            runner = module.AsyncSingleFlight()
            first_ready = asyncio.Event()
            release = asyncio.Event()
            calls = 0

            async def factory():
                nonlocal calls
                calls += 1
                yield "first"
                first_ready.set()
                await release.wait()
                yield "second"

            leader = asyncio.create_task(_consume(runner.stream("x", factory)))
            await asyncio.wait_for(first_ready.wait(), 1)
            joiner = asyncio.create_task(_consume(runner.stream("x", factory)))
            for _ in range(100):
                if runner.info().joined == 1:
                    break
                await asyncio.sleep(0)
            release.set()
            assert await asyncio.wait_for(leader, 1) == (["first", "second"], None)
            assert await asyncio.wait_for(joiner, 1) == (["first", "second"], None)
            assert calls == 1

        asyncio.run(scenario())

    @register("stream", "early_consumer_exit_keeps_producer_alive")
    def early_consumer_exit_keeps_producer_alive() -> None:
        async def scenario() -> None:
            runner = module.AsyncSingleFlight()
            first_ready = asyncio.Event()
            release = asyncio.Event()
            calls = 0

            async def factory():
                nonlocal calls
                calls += 1
                yield 1
                first_ready.set()
                await release.wait()
                yield 2

            abandoned = runner.stream("x", factory)
            assert await abandoned.__anext__() == 1
            await abandoned.aclose()
            await asyncio.wait_for(first_ready.wait(), 1)
            joined = asyncio.create_task(_consume(runner.stream("x", factory)))
            for _ in range(100):
                if runner.info().joined == 1:
                    break
                await asyncio.sleep(0)
            release.set()
            assert await asyncio.wait_for(joined, 1) == ([1, 2], None)
            assert calls == 1

        asyncio.run(scenario())

    @register("stream", "partial_items_precede_error_and_next_is_fresh")
    def partial_items_precede_error_and_next_is_fresh() -> None:
        async def scenario() -> None:
            runner = module.AsyncSingleFlight()
            calls = 0

            async def factory():
                nonlocal calls
                calls += 1
                if calls == 1:
                    yield 1
                    yield 2
                    raise _Boom("stream")
                yield "fresh"

            items, error = await _consume(runner.stream("x", factory))
            assert items == [1, 2]
            assert isinstance(error, _Boom) and str(error) == "stream"
            assert await _consume(runner.stream("x", factory)) == (["fresh"], None)
            assert calls == 2

        asyncio.run(scenario())

    @register("stream", "stream_factory_is_lazy_and_leader_local")
    def stream_factory_is_lazy_and_leader_local() -> None:
        async def scenario() -> None:
            runner = module.AsyncSingleFlight()
            calls: list[tuple[int, int]] = []
            caller = (threading.get_ident(), id(asyncio.get_running_loop()))

            async def factory():
                calls.append((threading.get_ident(), id(asyncio.get_running_loop())))
                yield 1

            iterator = runner.stream("x", factory)
            assert calls == []
            assert runner.info() == module.FlightInfo(0, 0, 0)
            assert await _consume(iterator) == ([1], None)
            assert calls == [caller]

        asyncio.run(scenario())

    @register("lifecycle", "scalar_and_stream_lanes_are_independent")
    def scalar_and_stream_lanes_are_independent() -> None:
        async def scenario() -> None:
            runner = module.AsyncSingleFlight()
            run_started = asyncio.Event()
            stream_started = asyncio.Event()
            release = asyncio.Event()

            async def run_factory():
                run_started.set()
                await release.wait()
                return "run"

            async def stream_factory():
                stream_started.set()
                await release.wait()
                yield "stream"

            scalar = asyncio.create_task(runner.run("same", run_factory))
            streamed = asyncio.create_task(_consume(runner.stream("same", stream_factory)))
            await asyncio.wait_for(asyncio.gather(run_started.wait(), stream_started.wait()), 1)
            assert runner.info() == module.FlightInfo(2, 0, 2)
            release.set()
            assert await scalar == "run"
            assert await streamed == (["stream"], None)

        asyncio.run(scenario())

    @register("lifecycle", "different_keys_overlap")
    def different_keys_overlap() -> None:
        async def scenario() -> None:
            runner = module.AsyncSingleFlight()
            both_started = asyncio.Event()
            release = asyncio.Event()
            calls: list[str] = []

            async def factory(name: str):
                calls.append(name)
                if len(calls) == 2:
                    both_started.set()
                await release.wait()
                return name

            left = asyncio.create_task(runner.run("a", lambda: factory("a")))
            right = asyncio.create_task(runner.run("b", lambda: factory("b")))
            await asyncio.wait_for(both_started.wait(), 1)
            assert runner.info().active == 2
            release.set()
            assert await asyncio.gather(left, right) == ["a", "b"]

        asyncio.run(scenario())

    @register("lifecycle", "clear_is_guarded_then_resets_stats")
    def clear_is_guarded_then_resets_stats() -> None:
        async def scenario() -> None:
            runner = module.AsyncSingleFlight()
            started = asyncio.Event()
            release = asyncio.Event()

            async def factory():
                started.set()
                await release.wait()
                return 1

            task = asyncio.create_task(runner.run("x", factory))
            await asyncio.wait_for(started.wait(), 1)
            before = runner.info()
            _expect_raises(RuntimeError, runner.clear)
            assert runner.info() == before
            release.set()
            assert await task == 1
            runner.clear()
            assert runner.info() == module.FlightInfo(0, 0, 0)

        asyncio.run(scenario())

    @register("lifecycle", "unhashable_keys_fail_before_user_code")
    def unhashable_keys_fail_before_user_code() -> None:
        async def scenario() -> None:
            runner = module.AsyncSingleFlight()
            calls = 0

            async def run_factory():
                nonlocal calls
                calls += 1
                return 1

            async def stream_factory():
                nonlocal calls
                calls += 1
                yield 1

            try:
                await runner.run([], run_factory)
            except TypeError:
                pass
            else:
                raise AssertionError("unhashable scalar key was accepted")
            items, error = await _consume(runner.stream([], stream_factory))
            assert items == []
            assert isinstance(error, TypeError)
            assert calls == 0
            assert runner.info() == module.FlightInfo(0, 0, 0)

        asyncio.run(scenario())

    assert len(cases) == RAW_MAX_SCORE
    return {case.case_id: case for case in cases}


def _build_cases(module) -> dict[str, Case]:
    try:
        from scanner.cross_loop_singleflight_v2_cases import build_cases
    except ModuleNotFoundError:  # Direct case-worker execution.
        from cross_loop_singleflight_v2_cases import build_cases

    return build_cases(module, sys.modules[__name__])


def _normalize_patch(response: str) -> str:
    lines = response.replace("\r\n", "\n").replace("\r", "\n").splitlines(True)
    normalized: list[str] = []
    in_block = False
    in_replacement = False
    for line in lines:
        stripped = line.strip()
        if stripped == "<<<<<<< SEARCH":
            in_block = True
            in_replacement = False
        elif in_block and stripped == "=======":
            in_replacement = True
        elif in_replacement and stripped == ">>>>>>> REPLACE":
            in_block = False
            in_replacement = False
        elif in_replacement and (
            line.startswith("*** Update File: ") or stripped == "*** End Patch"
        ):
            normalized.append(">>>>>>> REPLACE\n")
            in_block = False
            in_replacement = False
        normalized.append(line)
    merged: list[str] = []
    saw_envelope = False
    for line in normalized:
        stripped = line.strip()
        if stripped == "*** Begin Patch":
            if not saw_envelope:
                merged.append(line)
                saw_envelope = True
            continue
        if stripped == "*** End Patch":
            continue
        merged.append(line)
    if saw_envelope:
        merged.append("*** End Patch\n")
    return "".join(merged)


def _validate_source(source_files: dict[str, str]) -> None:
    banned_calls = {
        "__import__",
        "breakpoint",
        "compile",
        "eval",
        "exec",
        "globals",
        "input",
        "locals",
        "open",
    }
    dangerous_attributes = {
        "__class__",
        "__code__",
        "__dict__",
        "__globals__",
        "__mro__",
        "__subclasses__",
    }
    source = source_files[SOURCE_FILE]
    try:
        tree = ast.parse(source, filename=SOURCE_FILE)
    except SyntaxError as exc:
        raise ValueError(f"syntax_error:{exc.msg}") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root not in ALLOWED_MODULES:
                    raise ValueError(f"forbidden_import:{alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            root = (node.module or "").split(".", 1)[0]
            if root not in ALLOWED_MODULES:
                raise ValueError(f"forbidden_import:{node.module}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in banned_calls:
                raise ValueError(f"forbidden_call:{node.func.id}")
        elif isinstance(node, ast.Attribute) and node.attr in dangerous_attributes:
            raise ValueError(f"forbidden_dunder_access:{node.attr}")


def _materialize(source_files: dict[str, str], root: Path) -> None:
    (root / SOURCE_FILE).write_text(source_files[SOURCE_FILE], encoding="utf-8")


def _case_worker(root: Path, case_id: str) -> int:
    try:
        sink = _DiscardingTextSink()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            candidate = _load_module(
                root / SOURCE_FILE,
                f"_q4_cross_loop_candidate_{case_id}",
            )
            _build_cases(candidate)[case_id].run()
        payload = {"passed": True}
    except BaseException as exc:
        payload = {"passed": False, "error": f"{type(exc).__name__}:{exc}"}
    print("__Q4_RESULT__" + json.dumps(payload, ensure_ascii=False))
    return 0


def _run_case(root: Path, case_id: str) -> dict[str, object]:
    try:
        completed = run_bounded_process(
            module_worker_command(
                "scanner.cross_loop_singleflight_grader",
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
    marker = "__Q4_RESULT__"
    result_line = next(
        (line for line in reversed(completed.stdout.splitlines()) if line.startswith(marker)),
        None,
    )
    if result_line is None:
        detail = (completed.stderr or completed.stdout).strip()[-500:]
        return {"passed": False, "error": f"RuntimeError:worker failed: {detail}"}
    try:
        return json.loads(result_line.removeprefix(marker))
    except json.JSONDecodeError as exc:
        return {"passed": False, "error": f"JSONDecodeError:{exc}"}


_CATALOG_MARKER = "__Q4_CATALOG__"


def _catalog_worker(root: Path) -> int:
    try:
        # Case discovery is candidate execution too.  Keep the import and
        # case-factory call in a supervised child instead of the controller
        # that coordinates per-case workers.
        sink = _DiscardingTextSink()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            candidate = _load_module(
                root / SOURCE_FILE,
                f"_q4_cross_loop_catalog_{os.getpid()}",
            )
            cases = _build_cases(candidate)
        payload = {
            "cases": [
                {"case_id": case.case_id, "facet": case.facet}
                for case in cases.values()
            ]
        }
    except BaseException as exc:
        payload = {"error": f"{type(exc).__name__}:{exc}"}
    print(_CATALOG_MARKER + json.dumps(payload, ensure_ascii=False))
    return 0


def _run_catalog(root: Path) -> list[dict[str, str]] | None:
    try:
        completed = run_bounded_process(
            module_worker_command(
                "scanner.cross_loop_singleflight_grader",
                "_catalog-worker",
                str(root),
                python_flags=("-B",),
            ),
            text=True,
            timeout=5.0,
            output_limit_bytes=WORKER_OUTPUT_LIMIT_BYTES,
            merge_stderr=True,
            runner=subprocess.run,
        )
    except (OSError, subprocess.TimeoutExpired, BoundedSubprocessOutputError):
        return None
    result_line = next(
        (
            line
            for line in reversed(completed.stdout.splitlines())
            if line.startswith(_CATALOG_MARKER)
        ),
        None,
    )
    if result_line is None:
        return None
    try:
        payload = json.loads(result_line.removeprefix(_CATALOG_MARKER))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or "error" in payload:
        return None
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list):
        return None
    cases: list[dict[str, str]] = []
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            return None
        case_id = raw_case.get("case_id")
        facet = raw_case.get("facet")
        if (
            not isinstance(case_id, str)
            or not isinstance(facet, str)
            or facet not in FACET_LABELS
        ):
            return None
        cases.append({"case_id": case_id, "facet": facet})
    if len(cases) != RAW_MAX_SCORE or len({case["case_id"] for case in cases}) != len(cases):
        return None
    return cases


def _grade_tree(root: Path) -> dict[str, object]:
    facets = {
        facet: {"label": label, "score": 0, "max_score": CASES_PER_FACET}
        for facet, label in FACET_LABELS.items()
    }
    failures: list[dict[str, str]] = []
    catalog = _run_catalog(root)
    if catalog is None:
        return {
            "status": "runner_error",
            "passed": False,
            "score": 0,
            "max_score": MAX_SCORE,
            "facets": facets,
            "clusters": [
                {
                    "id": cluster_id,
                    "label": CLUSTER_LABELS[cluster_id],
                    "case_ids": list(case_ids),
                    "points": 0,
                    "max_points": 1,
                    "passed": False,
                }
                for cluster_id, case_ids in CLUSTERS.items()
            ],
            "raw_score": 0,
            "raw_max_score": RAW_MAX_SCORE,
            "failures": [
                {
                    "case_id": "catalog",
                    "facet": "",
                    "facet_label": "",
                    "error": "catalog worker failed",
                }
            ],
        }
    for case in catalog:
        result = _run_case(root, case["case_id"])
        if result.get("passed"):
            facets[case["facet"]]["score"] += 1
        else:
            failures.append(
                {
                    "case_id": case["case_id"],
                    "facet": case["facet"],
                    "facet_label": FACET_LABELS[case["facet"]],
                    "error": str(result.get("error", "RuntimeError:case failed")),
                }
            )
    failed_ids = {str(item["case_id"]) for item in failures}
    clusters = [
        {
            "id": cluster_id,
            "label": CLUSTER_LABELS[cluster_id],
            "case_ids": list(case_ids),
            "points": 0 if any(case_id in failed_ids for case_id in case_ids) else 1,
            "max_points": 1,
            "passed": not any(case_id in failed_ids for case_id in case_ids),
        }
        for cluster_id, case_ids in CLUSTERS.items()
    ]
    score = sum(int(cluster["points"]) for cluster in clusters)
    return {
        "status": "passed" if score == MAX_SCORE else "semantic_failed",
        "passed": score == MAX_SCORE,
        "score": score,
        "max_score": MAX_SCORE,
        "facets": facets,
        "clusters": clusters,
        "raw_score": RAW_MAX_SCORE - len(failures),
        "raw_max_score": RAW_MAX_SCORE,
        "failures": failures,
    }


def grade_patch(patch_text: str) -> dict[str, object]:
    patch_format_ok = False
    patch_applies = False
    try:
        from scanner.session_bundle_grader import _apply_patch

        patched = _apply_patch(
            STARTER_FILES,
            _normalize_patch(patch_text),
        )
        patch_format_ok = True
        patch_applies = True
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
            "facets": {
                facet: {"label": label, "score": 0, "max_score": CASES_PER_FACET}
                for facet, label in FACET_LABELS.items()
            },
            "clusters": [
                {
                    "id": cluster_id,
                    "label": CLUSTER_LABELS[cluster_id],
                    "case_ids": list(case_ids),
                    "points": 0,
                    "max_points": 1,
                    "passed": False,
                }
                for cluster_id, case_ids in CLUSTERS.items()
            ],
            "raw_score": 0,
            "raw_max_score": RAW_MAX_SCORE,
            "patch_format_ok": patch_format_ok,
            "patch_applies": patch_applies,
            "error": error,
        }

    with tempfile.TemporaryDirectory(prefix="cross-loop-singleflight-grade-") as temp:
        root = Path(temp)
        _materialize(patched, root)
        payload = _grade_tree(root)

    failure_details = [
        {
            "case_id": str(item.get("case_id", "")),
            "label": str(item.get("case_id", "")),
            "category": str(item.get("facet", "")),
            "category_label": str(item.get("facet_label", "")),
            "error": str(item.get("error", "")),
        }
        for item in payload.get("failures", [])
        if isinstance(item, dict)
    ]
    return {
        "status": payload["status"],
        "score": payload["score"],
        "max_score": payload["max_score"],
        "failure_details": failure_details,
        "facets": payload["facets"],
        "clusters": payload["clusters"],
        "raw_score": payload["raw_score"],
        "raw_max_score": payload["raw_max_score"],
        "patch_format_ok": True,
        "patch_applies": True,
    }


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[0] == "_case-worker":
        return _case_worker(Path(argv[1]), argv[2])
    if len(argv) == 2 and argv[0] == "_catalog-worker":
        return _catalog_worker(Path(argv[1]))
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
