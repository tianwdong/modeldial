from __future__ import annotations

from contextlib import contextmanager
import multiprocessing
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from scanner.active_run_store import ActiveRunStore
from scanner.application_services import AutoResumeExecutionRouter
from scanner.history_store import HistoryStore
from scanner.execution import RunControlCoordinator
from scanner.maintenance_application import (
    AutoResumeClaim,
    AutoResumeCommand,
    RunControlCommand,
)
from scanner.service import MonitorService
from scanner.native_bridge import _scan_process_lock


class _FakeResumeService:
    def __init__(
        self,
        active_run_store: ActiveRunStore,
        runtime: dict[str, object],
    ) -> None:
        self.active_run_store = active_run_store
        self.history_store = HistoryStore(
            active_run_store.path.with_name("history.jsonl")
        )
        self.runtime = runtime

    def build_refresh_state(self) -> dict[str, object]:
        return {"runtime": dict(self.runtime)}

    def heartbeat_active_run_lease(self) -> None:
        return None


class _RecordingProcessLock:
    def __init__(self, acquired: bool = True) -> None:
        self.acquired = acquired
        self.call_count = 0
        self.active = False

    @contextmanager
    def __call__(self, *_args: object, **_kwargs: object):
        self.call_count += 1
        self.active = self.acquired
        try:
            yield self.acquired
        finally:
            self.active = False


def _active_payload(
    *,
    operation_kind: str = "scan",
    operation_run_id: str = "run-a",
    candidate_ids: list[str] | None = None,
    question_id: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "run_id": "run-a",
        "runtime": {"lifecycle_state": "paused_recoverable"},
        "run_metadata": {"run_id": "run-a", "status": "running"},
        "entries": [
            {
                "candidate_id": "candidate-a",
                "model": "model-a",
                "effort": "high",
                "label": "model-a / high",
                "status": "interrupted",
                "attempts_completed": 0,
                "attempts_per_target": 1,
                "phase": "scan" if operation_kind == "scan" else "repair",
                "flags": [],
            }
        ],
    }
    if operation_kind != "scan":
        payload["repair_operation_kind"] = operation_kind
        payload["repair_operation_run_id"] = operation_run_id
        if operation_kind == "candidate_repair":
            payload["repair_candidate_id"] = (candidate_ids or ["candidate-a"])[0]
            if question_id is not None:
                payload["repair_question_id"] = question_id
        else:
            payload["repair_candidate_ids"] = list(candidate_ids or ["candidate-a"])
    return payload


def _resumable_runtime(
    *,
    operation_kind: str = "scan",
    operation_run_id: str = "run-a",
    candidate_ids: list[str] | None = None,
    question_id: str | None = None,
) -> dict[str, object]:
    return {
        "is_running": False,
        "has_resumable_run": True,
        "resumable_run_id": "run-a",
        "resumable_operation_kind": operation_kind,
        "resumable_operation_run_id": operation_run_id,
        "resumable_candidate_ids": list(candidate_ids or []),
        "resumable_question_id": question_id,
    }


def _minimal_snapshot() -> dict[str, object]:
    return {
        "schema_version": 2,
        "config": {},
        "dashboard": {},
        "runtime": {},
        "question_pack": {},
        "settings_projection": {},
        "advisor_v2_evidence": {},
        "recommendation_portfolio_v2": {},
        "reference_snapshot_feed": {},
        "recommendation_use": {},
    }


def _auto_resume_process_worker(
    active_run_path: str,
    client_session_id: str,
    entered,
    release,
    results,
) -> None:
    store = ActiveRunStore(Path(active_run_path))
    service = _FakeResumeService(store, _resumable_runtime())

    def resume_stream(
        _claim: AutoResumeClaim,
        *,
        process_lock,
    ):
        with process_lock(store, service.history_store) as acquired:
            if not acquired:
                raise AssertionError("held-lock adapter did not report ownership")
            entered.set()
            release.wait(timeout=10)
            yield {"type": "scan.started"}

    events = list(
        AutoResumeCommand(
            service,
            process_lock=_scan_process_lock,
            resume_stream=resume_stream,
            terminal_snapshot_builder=_minimal_snapshot,
        ).resume_if_needed("startup", client_session_id)
    )
    results.put((client_session_id, [event["type"] for event in events]))


class AutoResumeCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.active_run_store = ActiveRunStore(self.root / "active_run.json")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _command(
        self,
        *,
        runtime: dict[str, object],
        process_lock: _RecordingProcessLock | None = None,
        claims: list[AutoResumeClaim] | None = None,
    ) -> tuple[AutoResumeCommand, _RecordingProcessLock, list[AutoResumeClaim]]:
        lock = process_lock or _RecordingProcessLock()
        captured_claims = claims if claims is not None else []

        def stream_resume(
            claim: AutoResumeClaim,
            *,
            process_lock,
        ):
            captured_claims.append(claim)
            with process_lock(
                self.active_run_store,
                HistoryStore(self.root / "history.jsonl"),
            ) as acquired:
                self.assertTrue(acquired)
                yield {"type": "scan.started"}

        service = _FakeResumeService(self.active_run_store, runtime)

        def terminal_snapshot() -> dict[str, object]:
            self.assertFalse(lock.active)
            return _minimal_snapshot()

        return (
            AutoResumeCommand(
                service,
                process_lock=lock,
                resume_stream=stream_resume,
                terminal_snapshot_builder=terminal_snapshot,
            ),
            lock,
            captured_claims,
        )

    def test_no_resumable_run_is_noop_and_never_starts_a_run(self) -> None:
        command, process_lock, claims = self._command(
            runtime={"has_resumable_run": False}
        )

        events = list(
            command.resume_if_needed(
                trigger="startup",
                client_session_id="client-a",
            )
        )

        self.assertEqual([event["type"] for event in events], ["auto-resume.noop"])
        self.assertEqual(events[0]["reason"], "no_resumable_run")
        self.assertEqual(events[0]["state"]["schema_version"], 2)  # type: ignore[index]
        self.assertEqual(process_lock.call_count, 1)
        self.assertEqual(claims, [])
        self.assertIsNone(self.active_run_store.load())

    def test_terminal_snapshot_failure_is_not_downgraded_to_state_free_noop(self) -> None:
        lock = _RecordingProcessLock()
        service = _FakeResumeService(
            self.active_run_store,
            {"has_resumable_run": False},
        )

        def fail_snapshot() -> dict[str, object]:
            raise RuntimeError("snapshot projection failed")

        command = AutoResumeCommand(
            service,
            process_lock=lock,
            resume_stream=MagicMock(),
            terminal_snapshot_builder=fail_snapshot,
        )

        with self.assertRaisesRegex(RuntimeError, "snapshot projection failed"):
            list(command.resume_if_needed("startup", "client-a"))
        self.assertFalse(lock.active)

    def test_claim_and_delegated_started_event_remain_inside_one_real_lock(self) -> None:
        self.active_run_store.save(_active_payload())
        command, process_lock, claims = self._command(
            runtime=_resumable_runtime()
        )

        stream = command.resume_if_needed(
            trigger="startup",
            client_session_id="client-a",
        )
        marker = next(stream)
        self.assertEqual(marker["type"], "auto-resume.started")
        self.assertTrue(process_lock.active)
        delegated = next(stream)
        self.assertEqual(delegated["type"], "scan.started")
        self.assertTrue(process_lock.active)
        with self.assertRaises(StopIteration):
            next(stream)

        self.assertFalse(process_lock.active)
        self.assertEqual(process_lock.call_count, 1)
        self.assertEqual(claims[0].run_id, "run-a")
        maintenance = self.active_run_store.load()["maintenance"]  # type: ignore[index]
        session = maintenance["auto_resume"]["sessions"]["client-a"]  # type: ignore[index]
        self.assertEqual(session["attempt_count"], 1)

    def test_third_attempt_requires_manual_attention_but_new_session_resets(self) -> None:
        self.active_run_store.save(_active_payload())
        command, _, claims = self._command(runtime=_resumable_runtime())

        first = list(command.resume_if_needed("startup", "client-a"))
        second = list(command.resume_if_needed("interruption", "client-a"))
        third = list(command.resume_if_needed("interruption", "client-a"))
        new_session = list(command.resume_if_needed("startup", "client-b"))

        self.assertEqual(first[0]["attempt"], 1)
        self.assertEqual(second[0]["attempt"], 2)
        self.assertEqual(third[0]["type"], "auto-resume.manual-attention")
        self.assertEqual(third[0]["reason"], "attempt_limit_reached")
        self.assertEqual(new_session[0]["type"], "auto-resume.started")
        self.assertEqual(new_session[0]["attempt"], 1)
        self.assertEqual(len(claims), 3)

    def test_live_scan_lock_is_noop_without_consuming_an_attempt(self) -> None:
        self.active_run_store.save(_active_payload())
        command, process_lock, claims = self._command(
            runtime=_resumable_runtime(),
            process_lock=_RecordingProcessLock(acquired=False),
        )

        events = list(command.resume_if_needed("startup", "client-a"))

        self.assertEqual(events[0]["type"], "auto-resume.noop")
        self.assertEqual(events[0]["reason"], "scan_active")
        self.assertEqual(process_lock.call_count, 1)
        self.assertEqual(claims, [])
        self.assertNotIn("maintenance", self.active_run_store.load())  # type: ignore[operator]

    def test_same_session_pause_suppresses_resume_but_new_session_can_claim(self) -> None:
        self.active_run_store.save(_active_payload())
        history_store = HistoryStore(self.root / "history.jsonl")
        RunControlCommand(self.active_run_store, history_store).request(
            "pause",
            client_session_id="client-a",
            terminate_children=lambda _path: 0,
        )
        command, _, claims = self._command(runtime=_resumable_runtime())

        suppressed = list(command.resume_if_needed("interruption", "client-a"))
        resumed = list(command.resume_if_needed("startup", "client-b"))

        self.assertEqual(suppressed[0]["type"], "auto-resume.noop")
        self.assertEqual(suppressed[0]["reason"], "paused_by_client_session")
        self.assertEqual(resumed[0]["type"], "auto-resume.started")
        self.assertEqual([claim.client_session_id for claim in claims], ["client-b"])

    def test_running_process_reapplies_pause_suppression_from_control_request(self) -> None:
        active_payload = _active_payload()
        self.active_run_store.save(active_payload)
        history_store = HistoryStore(self.root / "history.jsonl")
        RunControlCommand(self.active_run_store, history_store).request(
            "pause",
            client_session_id="client-a",
            terminate_children=lambda _path: 0,
        )
        self.active_run_store.save(active_payload)

        action = RunControlCoordinator(self.active_run_store).poll()

        self.assertEqual(action, "pause")
        active = self.active_run_store.load()
        session = active["maintenance"]["auto_resume"]["sessions"]["client-a"]  # type: ignore[index]
        self.assertTrue(session["pause_suppressed"])

    def test_all_resumable_operation_kinds_are_delegated_with_authoritative_args(self) -> None:
        cases = [
            ("scan", "run-a", [], None),
            ("candidate_repair", "source-run", ["candidate-a"], "q-1"),
            ("failed_repair", "source-run", ["candidate-a", "candidate-b"], None),
            ("timeout_repair", "source-run", ["candidate-b"], None),
        ]
        for index, (kind, operation_run_id, candidate_ids, question_id) in enumerate(cases):
            with self.subTest(kind=kind):
                self.active_run_store.save(
                    _active_payload(
                        operation_kind=kind,
                        operation_run_id=operation_run_id,
                        candidate_ids=candidate_ids,
                        question_id=question_id,
                    )
                )
                command, _, claims = self._command(
                    runtime=_resumable_runtime(
                        operation_kind=kind,
                        operation_run_id=operation_run_id,
                        candidate_ids=candidate_ids,
                        question_id=question_id,
                    )
                )

                events = list(
                    command.resume_if_needed("startup", f"client-{index}")
                )

                self.assertEqual(events[0]["type"], "auto-resume.started")
                self.assertEqual(claims[0].operation_kind, kind)
                self.assertEqual(claims[0].operation_run_id, operation_run_id)
                self.assertEqual(claims[0].candidate_ids, tuple(candidate_ids))
                self.assertEqual(claims[0].question_id, question_id)

    def test_concurrent_processes_allow_only_one_auto_resume_claim(self) -> None:
        self.active_run_store.save(_active_payload())
        context = multiprocessing.get_context("spawn")
        first_entered = context.Event()
        second_entered = context.Event()
        release_first = context.Event()
        second_release = context.Event()
        second_release.set()
        results = context.Queue()
        first = context.Process(
            target=_auto_resume_process_worker,
            args=(
                str(self.active_run_store.path),
                "client-a",
                first_entered,
                release_first,
                results,
            ),
        )
        second = context.Process(
            target=_auto_resume_process_worker,
            args=(
                str(self.active_run_store.path),
                "client-b",
                second_entered,
                second_release,
                results,
            ),
        )

        first.start()
        self.assertTrue(first_entered.wait(timeout=10))
        second.start()
        second.join(timeout=10)
        release_first.set()
        first.join(timeout=10)

        self.assertEqual(first.exitcode, 0)
        self.assertEqual(second.exitcode, 0)
        observed = dict(results.get(timeout=2) for _ in range(2))
        self.assertEqual(
            observed["client-a"],
            ["auto-resume.started", "scan.started"],
        )
        self.assertEqual(observed["client-b"], ["auto-resume.noop"])
        active = self.active_run_store.load()
        sessions = active["maintenance"]["auto_resume"]["sessions"]  # type: ignore[index]
        self.assertEqual(sessions["client-a"]["attempt_count"], 1)
        self.assertNotIn("client-b", sessions)


class ActiveRunMaintenancePersistenceTest(unittest.TestCase):
    def test_scan_checkpoint_preserves_same_run_maintenance_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            store = ActiveRunStore(root / "active_run.json")
            store.save(
                {
                    "run_id": "run-a",
                    "maintenance": {
                        "auto_resume": {
                            "sessions": {"client-a": {"attempt_count": 1}}
                        }
                    },
                }
            )
            fake_service = SimpleNamespace(
                active_run_store=store,
                runtime_state={
                    "lifecycle_state": "active_scan",
                    "last_error": None,
                    "state_changed_at": None,
                    "finalizing_started_at": None,
                    "last_phase": None,
                    "last_phase_completed": 0,
                    "last_phase_total": 0,
                    "progress_completed": 0,
                    "progress_total": 1,
                    "current_phase": "scan",
                    "active_evaluation_count": 0,
                    "queued_evaluation_count": 1,
                    "oldest_active_evaluation_started_at": None,
                },
                load_config=MagicMock(
                    return_value=SimpleNamespace(
                        system=SimpleNamespace(execution_timeout_seconds=120)
                    )
                ),
                _lease_duration_seconds=MagicMock(return_value=420),
                _timestamp=MagicMock(return_value="2026-07-29T12:00:00+08:00"),
                _save_journal_summary=MagicMock(),
            )

            MonitorService._persist_active_run(
                fake_service,
                run_id="run-a",
                enabled_targets=[],
                attempts_per_target=1,
                run_entries=[],
            )

            active = store.load()
            self.assertIn("maintenance", active)  # type: ignore[operator]
            self.assertEqual(
                active["maintenance"]["auto_resume"]["sessions"]["client-a"][  # type: ignore[index]
                    "attempt_count"
                ],
                1,
            )


class AutoResumeExecutionRouterTest(unittest.TestCase):
    def test_scan_claim_uses_resume_only_guard_and_held_lock_adapter(self) -> None:
        service = MagicMock()
        process_lock = MagicMock()
        command = MagicMock()
        command.stream_events.return_value = iter([{"type": "delegated"}])
        claim = AutoResumeClaim(
            run_id="run-a",
            operation_kind="scan",
            operation_run_id="run-a",
            candidate_ids=(),
            question_id=None,
            trigger="startup",
            client_session_id="client-a",
            attempt=1,
        )

        with patch(
            "scanner.application_services.ScanCommand",
            return_value=command,
        ):
            events = list(
                AutoResumeExecutionRouter(
                    service=service,
                    snapshot_builder=MagicMock(),
                    terminal_snapshot_builder=MagicMock(),
                ).stream(
                    claim,
                    process_lock=process_lock,
                )
            )

        self.assertEqual(events, [{"type": "delegated"}])
        kwargs = command.stream_events.call_args.kwargs
        self.assertEqual(kwargs["expected_resume_run_id"], "run-a")
        self.assertIs(kwargs["process_lock"], process_lock)
        self.assertFalse(kwargs["force_restart"])

    def test_repair_claims_route_to_existing_candidate_and_batch_streams(self) -> None:
        cases = [
            ("candidate_repair", False),
            ("failed_repair", False),
            ("timeout_repair", True),
        ]
        for operation_kind, timeouts_only in cases:
            with self.subTest(operation_kind=operation_kind):
                service = MagicMock()
                process_lock = MagicMock()
                command = MagicMock()
                command.stream_candidate_events.return_value = iter(
                    [{"type": "candidate"}]
                )
                command.stream_batch_events.return_value = iter(
                    [{"type": "batch"}]
                )
                claim = AutoResumeClaim(
                    run_id="repair-active",
                    operation_kind=operation_kind,
                    operation_run_id="source-run",
                    candidate_ids=("candidate-a", "candidate-b"),
                    question_id="q-1",
                    trigger="interruption",
                    client_session_id="client-a",
                    attempt=2,
                )

                with patch(
                    "scanner.application_services.RepairCommand",
                    return_value=command,
                ):
                    events = list(
                        AutoResumeExecutionRouter(
                            service=service,
                            snapshot_builder=MagicMock(),
                            terminal_snapshot_builder=MagicMock(),
                        ).stream(
                            claim,
                            process_lock=process_lock,
                        )
                    )

                if operation_kind == "candidate_repair":
                    self.assertEqual(events, [{"type": "candidate"}])
                    kwargs = command.stream_candidate_events.call_args.kwargs
                    self.assertEqual(kwargs["run_id"], "source-run")
                    self.assertEqual(kwargs["candidate_id"], "candidate-a")
                    self.assertEqual(kwargs["question_id"], "q-1")
                else:
                    self.assertEqual(events, [{"type": "batch"}])
                    kwargs = command.stream_batch_events.call_args.kwargs
                    self.assertEqual(kwargs["run_id"], "source-run")
                    self.assertEqual(
                        kwargs["candidate_ids"],
                        ["candidate-a", "candidate-b"],
                    )
                    self.assertEqual(kwargs["timeouts_only"], timeouts_only)
                self.assertEqual(
                    kwargs["expected_resume_run_id"],
                    "repair-active",
                )
                self.assertIs(kwargs["process_lock"], process_lock)


if __name__ == "__main__":
    unittest.main()
