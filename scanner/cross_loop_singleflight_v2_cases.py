from __future__ import annotations

import asyncio
import contextlib
import threading
from concurrent.futures import ThreadPoolExecutor


def _apply_v2(module, base, cases) -> dict[str, object]:
    cases = dict(cases)
    for case_id in (
        "completed_results_are_fresh",
        "cross_loop_error_cleans_for_retry",
        "early_consumer_exit_keeps_producer_alive",
        "different_keys_overlap",
        "factory_stays_on_leader_location",
        "stream_factory_is_lazy_and_leader_local",
    ):
        del cases[case_id]

    def register(facet: str, case_id: str):
        def decorator(operation):
            cases[case_id] = base.Case(case_id=case_id, facet=facet, run=operation)
            return operation

        return decorator

    @register("scalar", "cancelled_joiner_completion_and_retry_are_isolated")
    def cancelled_joiner_completion_and_retry_are_isolated() -> None:
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
                    return "shared"
                return "fresh"

            leader = asyncio.create_task(runner.run("x", factory))
            await asyncio.wait_for(started.wait(), 1)
            joiner = asyncio.create_task(runner.run("x", factory))
            try:
                for _ in range(100):
                    if runner.info().joined == 1:
                        break
                    await asyncio.sleep(0)
                assert runner.info() == module.FlightInfo(1, 1, 2)
                joiner.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await joiner
                assert joiner.cancelled()
            finally:
                release.set()

            assert await asyncio.wait_for(leader, 1) == "shared"
            assert await runner.run("x", factory) == "fresh"
            assert calls == 2
            assert runner.info() == module.FlightInfo(0, 1, 3)

        asyncio.run(scenario())

    @register("scalar", "scalar_reentry_tracks_factory_context")
    def scalar_reentry_tracks_factory_context() -> None:
        async def scenario() -> None:
            runner = module.AsyncSingleFlight()
            caller = (threading.get_ident(), id(asyncio.get_running_loop()))
            locations: list[tuple[int, int]] = []
            nested_calls = 0

            async def nested_factory():
                nonlocal nested_calls
                nested_calls += 1
                return "nested"

            async def expect_reentry(operation) -> None:
                try:
                    await asyncio.wait_for(operation, 0.15)
                except RuntimeError:
                    return
                raise AssertionError("same-flight dependency did not fail fast")

            async def factory():
                locations.append((threading.get_ident(), id(asyncio.get_running_loop())))
                await expect_reentry(runner.run("x", nested_factory))
                child = asyncio.create_task(runner.run("x", nested_factory))
                await expect_reentry(child)
                return "outer"

            assert await runner.run("x", factory) == "outer"
            assert locations == [caller]
            assert nested_calls == 0
            assert runner.info() == module.FlightInfo(0, 0, 1)

        asyncio.run(scenario())

    @register("cross_loop", "cancelled_leader_restart_does_not_remove_replacement")
    def cancelled_leader_restart_does_not_remove_replacement() -> None:
        runner = module.AsyncSingleFlight()
        old_started = threading.Event()
        cancel_old = threading.Event()
        new_started = threading.Event()
        release_new = threading.Event()

        def old_worker() -> str:
            async def scenario() -> str:
                async def old_factory():
                    old_started.set()
                    await asyncio.Event().wait()

                task = asyncio.create_task(runner.run("x", old_factory))
                while not cancel_old.is_set():
                    await asyncio.sleep(0.001)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    return "cancelled"
                raise AssertionError("old leader was not cancelled")

            return asyncio.run(scenario())

        def new_worker() -> str:
            async def new_factory():
                new_started.set()
                while not release_new.is_set():
                    await asyncio.sleep(0.001)
                return "fresh"

            return asyncio.run(runner.run("x", new_factory))

        with ThreadPoolExecutor(max_workers=2) as pool:
            old = pool.submit(old_worker)
            assert old_started.wait(1)
            cancel_old.set()
            base._wait_until(lambda: runner.info().active == 0)
            new = pool.submit(new_worker)
            assert new_started.wait(1)
            try:
                assert old.result(timeout=1) == "cancelled"
                assert runner.info().active == 1
            finally:
                release_new.set()
            assert new.result(timeout=1) == "fresh"

        assert runner.info() == module.FlightInfo(0, 0, 2)

    @register("stream", "late_stream_cancel_error_replays_buffer_before_failure")
    def late_stream_cancel_error_replays_buffer_before_failure() -> None:
        async def scenario() -> None:
            runner = module.AsyncSingleFlight()
            first_ready = asyncio.Event()
            release = asyncio.Event()
            calls = 0

            async def factory():
                nonlocal calls
                calls += 1
                if calls == 1:
                    yield "first"
                    first_ready.set()
                    await release.wait()
                    yield "second"
                    raise base._Boom("stream")
                yield "fresh"

            leader = asyncio.create_task(base._consume(runner.stream("x", factory)))
            await asyncio.wait_for(first_ready.wait(), 1)
            transient = asyncio.create_task(base._consume(runner.stream("x", factory)))
            for _ in range(100):
                if runner.info().joined == 1:
                    break
                await asyncio.sleep(0)
            transient.cancel()
            transient_items, transient_error = await transient

            late = asyncio.create_task(base._consume(runner.stream("x", factory)))
            try:
                for _ in range(100):
                    if runner.info().joined == 2:
                        break
                    await asyncio.sleep(0)
                assert runner.info() == module.FlightInfo(1, 2, 3)
            finally:
                release.set()

            leader_items, leader_error = await asyncio.wait_for(leader, 1)
            late_items, late_error = await asyncio.wait_for(late, 1)
            assert transient_items == ["first"]
            assert isinstance(transient_error, asyncio.CancelledError)
            assert leader_items == ["first", "second"]
            assert late_items == ["first", "second"]
            assert isinstance(leader_error, base._Boom) and str(leader_error) == "stream"
            assert isinstance(late_error, base._Boom) and str(late_error) == "stream"
            assert await base._consume(runner.stream("x", factory)) == (["fresh"], None)
            assert calls == 2
            assert runner.info() == module.FlightInfo(0, 2, 4)

        asyncio.run(scenario())

    @register("stream", "stream_reentry_tracks_factory_context")
    def stream_reentry_tracks_factory_context() -> None:
        async def scenario() -> None:
            runner = module.AsyncSingleFlight()
            caller = (threading.get_ident(), id(asyncio.get_running_loop()))
            locations: list[tuple[int, int]] = []
            nested_calls = 0

            async def nested_factory():
                nonlocal nested_calls
                nested_calls += 1
                yield "nested"

            async def factory():
                locations.append((threading.get_ident(), id(asyncio.get_running_loop())))
                direct_items, direct_error = await asyncio.wait_for(
                    base._consume(runner.stream("x", nested_factory)),
                    0.15,
                )
                assert direct_items == []
                assert isinstance(direct_error, RuntimeError)

                child = asyncio.create_task(
                    base._consume(runner.stream("x", nested_factory))
                )
                child_items, child_error = await asyncio.wait_for(child, 0.15)
                assert child_items == []
                assert isinstance(child_error, RuntimeError)
                yield "outer"

            iterator = runner.stream("x", factory)
            assert locations == []
            assert runner.info() == module.FlightInfo(0, 0, 0)
            assert await base._consume(iterator) == (["outer"], None)
            assert locations == [caller]
            assert nested_calls == 0
            assert runner.info() == module.FlightInfo(0, 0, 1)

        asyncio.run(scenario())

    @register("lifecycle", "factory_context_expires_and_allowed_nesting_stays_independent")
    def factory_context_expires_and_allowed_nesting_stays_independent() -> None:
        async def scenario() -> None:
            runner = module.AsyncSingleFlight()

            def scalar_boom():
                raise base._Boom("scalar setup")

            def stream_boom():
                raise base._Boom("stream setup")

            try:
                await runner.run("run", scalar_boom)
            except base._Boom as exc:
                assert str(exc) == "scalar setup"
            else:
                raise AssertionError("scalar factory setup failure was swallowed")

            items, error = await base._consume(runner.stream("stream", stream_boom))
            assert items == []
            assert isinstance(error, base._Boom) and str(error) == "stream setup"
            assert runner.info() == module.FlightInfo(0, 0, 2)

            runner.clear()
            assert runner.info() == module.FlightInfo(0, 0, 0)

            async def scalar_ok():
                return "run"

            async def stream_ok():
                yield "stream"

            assert await runner.run("run", scalar_ok) == "run"
            assert await base._consume(runner.stream("stream", stream_ok)) == (
                ["stream"],
                None,
            )
            assert runner.info() == module.FlightInfo(0, 0, 2)

            nested_runner = module.AsyncSingleFlight()
            other_runner = module.AsyncSingleFlight()
            release_detached = asyncio.Event()
            detached_holder: dict[str, asyncio.Task] = {}

            async def value_factory(value: str):
                return value

            async def nested_stream_factory():
                yield "stream"

            async def outer_factory():
                async def detached_call():
                    await release_detached.wait()
                    return await nested_runner.run(
                        "root",
                        lambda: value_factory("detached"),
                    )

                detached_holder["task"] = asyncio.create_task(detached_call())
                assert await nested_runner.run(
                    "child",
                    lambda: value_factory("child"),
                ) == "child"
                assert await base._consume(
                    nested_runner.stream("root", nested_stream_factory)
                ) == (["stream"], None)
                assert await other_runner.run(
                    "root",
                    lambda: value_factory("other"),
                ) == "other"
                return "outer"

            assert await nested_runner.run("root", outer_factory) == "outer"
            assert nested_runner.info() == module.FlightInfo(0, 0, 3)
            assert other_runner.info() == module.FlightInfo(0, 0, 1)
            release_detached.set()
            assert await asyncio.wait_for(detached_holder["task"], 1) == "detached"
            assert nested_runner.info() == module.FlightInfo(0, 0, 4)

        asyncio.run(scenario())

    assert len(cases) == base.RAW_MAX_SCORE
    facet_counts = {
        facet: sum(case.facet == facet for case in cases.values())
        for facet in base.FACET_LABELS
    }
    assert set(facet_counts.values()) == {base.CASES_PER_FACET}
    return cases




def _apply_v3(module, base, cases) -> dict[str, object]:
    cases = dict(cases)
    for case_id in (
        "scalar_shares_across_thread_loops",
        "cancelled_scalar_leader_fans_out",
        "cancelled_stream_joiner_does_not_stop_producer",
        "cancelled_leader_restart_does_not_remove_replacement",
    ):
        del cases[case_id]

    def register(facet: str, case_id: str):
        def decorator(operation):
            cases[case_id] = base.Case(case_id=case_id, facet=facet, run=operation)
            return operation

        return decorator

    @register("cancellation", "cancelled_original_observer_is_local")
    def cancelled_original_observer_is_local() -> None:
        async def scenario() -> None:
            runner = module.AsyncSingleFlight()
            started = asyncio.Event()
            release = asyncio.Event()
            producer_cancelled = asyncio.Event()
            producer_tasks: list[asyncio.Task] = []
            calls = 0

            async def factory():
                nonlocal calls
                calls += 1
                producer_tasks.append(asyncio.current_task())
                started.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    producer_cancelled.set()
                    raise
                return "shared"

            original = asyncio.create_task(runner.run("x", factory))
            await asyncio.wait_for(started.wait(), 1)
            joiner = asyncio.create_task(runner.run("x", factory))
            for _ in range(100):
                if runner.info().joined == 1:
                    break
                await asyncio.sleep(0)
            assert runner.info() == module.FlightInfo(1, 1, 2)
            assert producer_tasks == [producer_tasks[0]]
            assert producer_tasks[0] is not original

            original.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await original
            assert original.cancelled()
            await asyncio.sleep(0)
            assert runner.info() == module.FlightInfo(1, 1, 2)
            assert not producer_cancelled.is_set()

            release.set()
            assert await asyncio.wait_for(joiner, 1) == "shared"
            assert calls == 1
            assert runner.info() == module.FlightInfo(0, 1, 2)

        asyncio.run(scenario())

    @register("cancellation", "last_stream_subscriber_cancels_and_retry_is_fresh")
    def last_stream_subscriber_cancels_and_retry_is_fresh() -> None:
        async def scenario() -> None:
            runner = module.AsyncSingleFlight()
            old_cleanup_started = asyncio.Event()
            allow_old_cleanup = asyncio.Event()
            old_cleanup_finished = asyncio.Event()
            new_started = asyncio.Event()
            release_new = asyncio.Event()
            old_producers: list[asyncio.Task] = []
            calls = 0

            async def factory():
                nonlocal calls
                calls += 1
                generation = calls
                if generation == 1:
                    old_producers.append(asyncio.current_task())
                    try:
                        yield "old"
                        await asyncio.Event().wait()
                    finally:
                        old_cleanup_started.set()
                        await allow_old_cleanup.wait()
                        old_cleanup_finished.set()
                    return
                yield "fresh"
                new_started.set()
                await release_new.wait()
                yield "done"

            first = runner.stream("x", factory)
            second = runner.stream("x", factory)
            assert await asyncio.wait_for(first.__anext__(), 1) == "old"
            assert await asyncio.wait_for(second.__anext__(), 1) == "old"
            assert runner.info() == module.FlightInfo(1, 1, 2)

            await first.aclose()
            await asyncio.sleep(0)
            assert runner.info() == module.FlightInfo(1, 1, 2)
            assert not old_cleanup_started.is_set()

            await second.aclose()
            assert runner.info() == module.FlightInfo(0, 1, 2)
            await asyncio.wait_for(old_cleanup_started.wait(), 1)

            replacement = runner.stream("x", factory)
            assert await asyncio.wait_for(replacement.__anext__(), 1) == "fresh"
            await asyncio.wait_for(new_started.wait(), 1)
            assert runner.info() == module.FlightInfo(1, 1, 3)

            allow_old_cleanup.set()
            await asyncio.wait_for(old_cleanup_finished.wait(), 1)
            for _ in range(100):
                if old_producers[0].done():
                    break
                await asyncio.sleep(0)
            assert old_producers[0].done()
            assert runner.info() == module.FlightInfo(1, 1, 3)

            release_new.set()
            assert await asyncio.wait_for(replacement.__anext__(), 1) == "done"
            try:
                await asyncio.wait_for(replacement.__anext__(), 1)
            except StopAsyncIteration:
                pass
            else:
                raise AssertionError("replacement stream did not finish")
            assert calls == 2
            assert runner.info() == module.FlightInfo(0, 1, 3)

        asyncio.run(scenario())

    @register("cross_loop", "origin_cancel_keeps_cross_loop_joiner_alive")
    def origin_cancel_keeps_cross_loop_joiner_alive() -> None:
        runner = module.AsyncSingleFlight()
        factory_started = threading.Event()
        cancel_origin = threading.Event()
        origin_cancelled = threading.Event()
        release_factory = threading.Event()
        allow_origin_exit = threading.Event()
        factory_cancelled = threading.Event()
        locations: dict[str, tuple[int, int]] = {}
        task_ids: dict[str, int] = {}
        calls = 0

        def origin_worker() -> str:
            async def scenario() -> str:
                nonlocal calls
                locations["caller"] = (
                    threading.get_ident(),
                    id(asyncio.get_running_loop()),
                )

                async def factory():
                    nonlocal calls
                    calls += 1
                    locations["producer"] = (
                        threading.get_ident(),
                        id(asyncio.get_running_loop()),
                    )
                    task_ids["producer"] = id(asyncio.current_task())
                    factory_started.set()
                    try:
                        while not release_factory.is_set():
                            await asyncio.sleep(0.001)
                    except asyncio.CancelledError:
                        factory_cancelled.set()
                        raise
                    return "shared"

                observer = asyncio.create_task(runner.run("x", factory))
                task_ids["observer"] = id(observer)
                while not cancel_origin.is_set():
                    await asyncio.sleep(0.001)
                observer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await observer
                origin_cancelled.set()
                while not allow_origin_exit.is_set():
                    await asyncio.sleep(0.001)
                return "cancelled"

            return asyncio.run(scenario())

        def joiner_worker() -> str:
            async def forbidden_factory():
                raise AssertionError("joiner invoked its factory")

            return asyncio.run(runner.run("x", forbidden_factory))

        with ThreadPoolExecutor(max_workers=2) as pool:
            origin = pool.submit(origin_worker)
            assert factory_started.wait(1)
            joiner = pool.submit(joiner_worker)
            base._wait_until(lambda: runner.info().joined == 1)
            cancel_origin.set()
            assert origin_cancelled.wait(1)
            assert runner.info() == module.FlightInfo(1, 1, 2)
            assert not factory_cancelled.is_set()

            release_factory.set()
            assert joiner.result(timeout=1) == "shared"
            allow_origin_exit.set()
            assert origin.result(timeout=1) == "cancelled"

        assert calls == 1
        assert locations["producer"] == locations["caller"]
        assert task_ids["producer"] != task_ids["observer"]
        assert runner.info() == module.FlightInfo(0, 1, 2)

    @register("cross_loop", "detached_generation_cannot_remove_cross_loop_retry")
    def detached_generation_cannot_remove_cross_loop_retry() -> None:
        runner = module.AsyncSingleFlight()
        old_started = threading.Event()
        cancel_old = threading.Event()
        old_observer_done = threading.Event()
        old_cancel_started = threading.Event()
        allow_old_cleanup = threading.Event()
        old_producer_done = threading.Event()
        allow_old_loop_exit = threading.Event()
        new_started = threading.Event()
        release_new = threading.Event()

        def old_worker() -> str:
            async def scenario() -> str:
                async def old_factory():
                    asyncio.current_task().add_done_callback(
                        lambda _task: old_producer_done.set()
                    )
                    old_started.set()
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        old_cancel_started.set()
                        while not allow_old_cleanup.is_set():
                            await asyncio.sleep(0.001)
                        raise

                observer = asyncio.create_task(runner.run("x", old_factory))
                while not cancel_old.is_set():
                    await asyncio.sleep(0.001)
                observer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await observer
                old_observer_done.set()
                while not allow_old_loop_exit.is_set():
                    await asyncio.sleep(0.001)
                return "cancelled"

            return asyncio.run(scenario())

        def new_worker() -> str:
            async def new_factory():
                new_started.set()
                while not release_new.is_set():
                    await asyncio.sleep(0.001)
                return "fresh"

            return asyncio.run(runner.run("x", new_factory))

        with ThreadPoolExecutor(max_workers=2) as pool:
            old = pool.submit(old_worker)
            assert old_started.wait(1)
            cancel_old.set()
            assert old_observer_done.wait(1)
            assert old_cancel_started.wait(1)
            assert runner.info() == module.FlightInfo(0, 0, 1)

            new = pool.submit(new_worker)
            assert new_started.wait(1)
            assert runner.info() == module.FlightInfo(1, 0, 2)

            allow_old_cleanup.set()
            assert old_producer_done.wait(1)
            assert runner.info() == module.FlightInfo(1, 0, 2)

            release_new.set()
            assert new.result(timeout=1) == "fresh"
            allow_old_loop_exit.set()
            assert old.result(timeout=1) == "cancelled"

        assert runner.info() == module.FlightInfo(0, 0, 2)

    assert len(cases) == base.RAW_MAX_SCORE
    facet_counts = {
        facet: sum(case.facet == facet for case in cases.values())
        for facet in base.FACET_LABELS
    }
    assert set(facet_counts.values()) == {base.CASES_PER_FACET}
    return cases




def _consume(iterator):
    async def collect():
        items: list[object] = []
        try:
            async for item in iterator:
                items.append(item)
        except BaseException as exc:
            return items, exc
        return items, None

    return collect()


def _blocking_loop_worker(
    operation,
    runner,
    waiter_registered: threading.Event,
    armed: threading.Event,
    notification_paused: threading.Event,
    resume_notification: threading.Event,
):
    loop = asyncio.new_event_loop()
    original_create_future = loop.create_future
    original_call_soon_threadsafe = loop.call_soon_threadsafe
    paused = False

    def create_future():
        future = original_create_future()
        if runner.info().joined >= 1:
            waiter_registered.set()
        return future

    def call_soon_threadsafe(callback, *args, context=None):
        nonlocal paused
        if armed.is_set() and not paused:
            paused = True
            notification_paused.set()
            if not resume_notification.wait(2):
                raise RuntimeError("notification was not resumed")
        if context is None:
            return original_call_soon_threadsafe(callback, *args)
        return original_call_soon_threadsafe(callback, *args, context=context)

    loop.create_future = create_future
    loop.call_soon_threadsafe = call_soon_threadsafe
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(operation())
    finally:
        asyncio.set_event_loop(None)
        loop.close()


def _apply_v3_1(module, base, cases) -> dict[str, object]:
    cases = dict(cases)
    for case_id in (
        "same_loop_duplicates_execute_once",
        "same_loop_joiner_replays_from_zero",
        "scalar_and_stream_lanes_are_independent",
        "clear_is_guarded_then_resets_stats",
    ):
        del cases[case_id]

    def register(facet: str, case_id: str):
        def decorator(operation):
            cases[case_id] = base.Case(case_id=case_id, facet=facet, run=operation)
            return operation

        return decorator

    @register("scalar", "completed_scalar_detaches_before_notification")
    def completed_scalar_detaches_before_notification() -> None:
        runner = module.AsyncSingleFlight()
        old_started = threading.Event()
        release_old = threading.Event()
        waiter_registered = threading.Event()
        armed = threading.Event()
        notification_paused = threading.Event()
        resume_notification = threading.Event()
        calls = 0

        async def old_factory():
            nonlocal calls
            calls += 1
            old_started.set()
            while not release_old.is_set():
                await asyncio.sleep(0.001)
            return "old"

        def owner_worker() -> object:
            return asyncio.run(runner.run("x", old_factory))

        def joiner_worker() -> object:
            async def forbidden_factory():
                raise AssertionError("joiner invoked its factory")

            return _blocking_loop_worker(
                lambda: runner.run("x", forbidden_factory),
                runner,
                waiter_registered,
                armed,
                notification_paused,
                resume_notification,
            )

        def replacement_worker() -> object:
            async def replacement_factory():
                nonlocal calls
                calls += 1
                return "fresh"

            return asyncio.run(runner.run("x", replacement_factory))

        with ThreadPoolExecutor(max_workers=3) as pool:
            owner = pool.submit(owner_worker)
            assert old_started.wait(1)
            joiner = pool.submit(joiner_worker)
            assert waiter_registered.wait(1)
            assert runner.info() == module.FlightInfo(1, 1, 2)

            armed.set()
            release_old.set()
            assert notification_paused.wait(1)
            try:
                replacement = pool.submit(replacement_worker)
                assert replacement.result(timeout=1) == "fresh"
            finally:
                resume_notification.set()

            assert owner.result(timeout=1) == "old"
            assert joiner.result(timeout=1) == "old"

        assert calls == 2
        assert runner.info() == module.FlightInfo(0, 1, 3)

    @register("stream", "completed_stream_detaches_before_notification")
    def completed_stream_detaches_before_notification() -> None:
        runner = module.AsyncSingleFlight()
        old_started = threading.Event()
        release_old = threading.Event()
        waiter_registered = threading.Event()
        armed = threading.Event()
        notification_paused = threading.Event()
        resume_notification = threading.Event()
        calls = 0

        async def old_factory():
            nonlocal calls
            calls += 1
            old_started.set()
            while not release_old.is_set():
                await asyncio.sleep(0.001)
            if False:
                yield None

        def owner_worker():
            return asyncio.run(_consume(runner.stream("x", old_factory)))

        def joiner_worker():
            async def forbidden_factory():
                raise AssertionError("joiner invoked its factory")
                if False:
                    yield None

            return _blocking_loop_worker(
                lambda: _consume(runner.stream("x", forbidden_factory)),
                runner,
                waiter_registered,
                armed,
                notification_paused,
                resume_notification,
            )

        def replacement_worker():
            async def replacement_factory():
                nonlocal calls
                calls += 1
                yield "fresh"

            return asyncio.run(_consume(runner.stream("x", replacement_factory)))

        with ThreadPoolExecutor(max_workers=3) as pool:
            owner = pool.submit(owner_worker)
            assert old_started.wait(1)
            joiner = pool.submit(joiner_worker)
            assert waiter_registered.wait(1)
            assert runner.info() == module.FlightInfo(1, 1, 2)

            armed.set()
            release_old.set()
            assert notification_paused.wait(1)
            try:
                replacement = pool.submit(replacement_worker)
                items, error = replacement.result(timeout=1)
                assert items == ["fresh"]
                assert error is None
            finally:
                resume_notification.set()

            assert owner.result(timeout=1) == ([], None)
            assert joiner.result(timeout=1) == ([], None)

        assert calls == 2
        assert runner.info() == module.FlightInfo(0, 1, 3)

    @register("lifecycle", "cancelled_scalar_cleanup_cannot_join_replacement")
    def cancelled_scalar_cleanup_cannot_join_replacement() -> None:
        async def scenario() -> None:
            runner = module.AsyncSingleFlight()
            old_started = asyncio.Event()
            cleanup_started = asyncio.Event()
            allow_cleanup = asyncio.Event()
            cleanup_done = asyncio.Event()
            replacement_started = asyncio.Event()
            release_replacement = asyncio.Event()
            cleanup_outcome: list[str] = []
            forbidden_calls = 0

            async def forbidden_factory():
                nonlocal forbidden_calls
                forbidden_calls += 1
                return "forbidden"

            async def old_factory():
                old_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cleanup_started.set()
                    await allow_cleanup.wait()
                    try:
                        await asyncio.wait_for(
                            runner.run("x", forbidden_factory),
                            0.15,
                        )
                    except RuntimeError:
                        cleanup_outcome.append("rejected")
                    except asyncio.TimeoutError:
                        cleanup_outcome.append("joined")
                    else:
                        cleanup_outcome.append("ran")
                    cleanup_done.set()
                    raise

            async def replacement_factory():
                replacement_started.set()
                await release_replacement.wait()
                return "fresh"

            observer = asyncio.create_task(runner.run("x", old_factory))
            await asyncio.wait_for(old_started.wait(), 1)
            observer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await observer
            await asyncio.wait_for(cleanup_started.wait(), 1)
            assert runner.info() == module.FlightInfo(0, 0, 1)

            replacement = asyncio.create_task(runner.run("x", replacement_factory))
            await asyncio.wait_for(replacement_started.wait(), 1)
            assert runner.info() == module.FlightInfo(1, 0, 2)

            allow_cleanup.set()
            await asyncio.wait_for(cleanup_done.wait(), 1)
            assert cleanup_outcome == ["rejected"]
            assert forbidden_calls == 0
            assert runner.info() == module.FlightInfo(1, 0, 2)

            release_replacement.set()
            assert await asyncio.wait_for(replacement, 1) == "fresh"
            assert runner.info() == module.FlightInfo(0, 0, 2)

        asyncio.run(scenario())

    @register("lifecycle", "cancelled_stream_cleanup_cannot_join_replacement")
    def cancelled_stream_cleanup_cannot_join_replacement() -> None:
        async def scenario() -> None:
            runner = module.AsyncSingleFlight()
            cleanup_started = asyncio.Event()
            allow_cleanup = asyncio.Event()
            cleanup_done = asyncio.Event()
            release_replacement = asyncio.Event()
            cleanup_outcome: list[str] = []
            forbidden_calls = 0

            async def forbidden_factory():
                nonlocal forbidden_calls
                forbidden_calls += 1
                yield "forbidden"

            async def old_factory():
                try:
                    yield "old"
                    await asyncio.Event().wait()
                finally:
                    cleanup_started.set()
                    await allow_cleanup.wait()
                    nested = runner.stream("x", forbidden_factory)
                    try:
                        item = await asyncio.wait_for(nested.__anext__(), 0.15)
                    except RuntimeError:
                        cleanup_outcome.append("rejected")
                    except asyncio.TimeoutError:
                        cleanup_outcome.append("joined")
                    else:
                        cleanup_outcome.append(f"ran:{item}")
                    finally:
                        await nested.aclose()
                    cleanup_done.set()

            async def replacement_factory():
                yield "fresh"
                await release_replacement.wait()
                yield "done"

            first = runner.stream("x", old_factory)
            assert await asyncio.wait_for(first.__anext__(), 1) == "old"
            assert runner.info() == module.FlightInfo(1, 0, 1)

            await first.aclose()
            await asyncio.wait_for(cleanup_started.wait(), 1)
            assert runner.info() == module.FlightInfo(0, 0, 1)

            replacement = runner.stream("x", replacement_factory)
            assert await asyncio.wait_for(replacement.__anext__(), 1) == "fresh"
            assert runner.info() == module.FlightInfo(1, 0, 2)

            allow_cleanup.set()
            await asyncio.wait_for(cleanup_done.wait(), 1)
            assert cleanup_outcome == ["rejected"]
            assert forbidden_calls == 0
            assert runner.info() == module.FlightInfo(1, 0, 2)

            release_replacement.set()
            assert await asyncio.wait_for(replacement.__anext__(), 1) == "done"
            with contextlib.suppress(StopAsyncIteration):
                item = await asyncio.wait_for(replacement.__anext__(), 1)
                raise AssertionError(f"replacement yielded extra item: {item!r}")
            assert runner.info() == module.FlightInfo(0, 0, 2)

        asyncio.run(scenario())

    assert len(cases) == base.RAW_MAX_SCORE
    facet_counts = {
        facet: sum(case.facet == facet for case in cases.values())
        for facet in base.FACET_LABELS
    }
    assert set(facet_counts.values()) == {base.CASES_PER_FACET}

    return cases


def build_cases(module, base) -> dict[str, object]:
    cases = base._build_base_cases(module)
    cases = _apply_v2(module, base, cases)
    cases = _apply_v3(module, base, cases)
    return _apply_v3_1(module, base, cases)
