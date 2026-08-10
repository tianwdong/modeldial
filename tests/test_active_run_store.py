from __future__ import annotations

import json
import multiprocessing
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scanner.active_run_store import ActiveRunStore
from scanner.execution import RunControlCoordinator
from scanner.history_store import HistoryStore
from scanner.maintenance_application import RunControlCommand


def _concurrent_control_writer(
    path: str,
    action: str,
    start_event,
    iterations: int,
) -> None:
    store = ActiveRunStore(Path(path))
    if not start_event.wait(10):
        raise RuntimeError("control writer did not start")
    for _ in range(iterations):
        store.request_control(action)


def _concurrent_control_reader(
    path: str,
    start_event,
    iterations: int,
) -> None:
    store = ActiveRunStore(Path(path))
    if not start_event.wait(10):
        raise RuntimeError("control reader did not start")
    for _ in range(iterations):
        was_present = store.control_path.exists()
        payload = store.peek_control_request()
        if was_present and payload is None:
            raise AssertionError("control mailbox was not valid JSON")


def _concurrent_control_claimer(
    path: str,
    claimed_event,
    release_event,
) -> None:
    store = ActiveRunStore(Path(path))
    with store._lock:
        with store._transaction():
            payload = store._claim_control_unlocked()
            if payload is None:
                raise AssertionError("claimer did not claim the initial request")
            claimed_event.set()
            if not release_event.wait(10):
                raise RuntimeError("claimer was not released")


def _concurrent_control_writer_after_claim(
    path: str,
    claimed_event,
    writer_ready_event,
) -> None:
    store = ActiveRunStore(Path(path))
    if not claimed_event.wait(10):
        raise RuntimeError("writer did not observe the claim")
    writer_ready_event.set()
    store.request_control("pause")


class ActiveRunStoreTest(unittest.TestCase):
    def test_invalid_json_is_quarantined_and_recovers_without_an_active_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "active_run.json"
            path.write_text("{not-json", encoding="utf-8")

            self.assertIsNone(ActiveRunStore(path).load())

            self.assertFalse(path.exists())
            quarantined = list(path.parent.glob("active_run.json.corrupt-*"))
            self.assertEqual(len(quarantined), 1)
            self.assertEqual(quarantined[0].read_text(encoding="utf-8"), "{not-json")

    def test_read_only_queries_do_not_create_paths_in_an_empty_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = ActiveRunStore(root / "active_run.json")
            before = set(root.rglob("*"))

            self.assertIsNone(store.load())
            self.assertIsNone(store.peek_control_request())

            self.assertEqual(set(root.rglob("*")), before)

    def test_read_only_queries_do_not_create_paths_with_existing_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = ActiveRunStore(root / "active_run.json")
            active_payload = {"run_id": "run-1", "status": "running"}
            control_payload = {
                "schema_version": 1,
                "request_id": "request-1",
                "run_id": "run-1",
                "action": "pause",
            }
            store.path.write_text(
                json.dumps(active_payload),
                encoding="utf-8",
            )
            store.control_path.write_text(
                json.dumps(control_payload),
                encoding="utf-8",
            )
            before = set(root.rglob("*"))

            self.assertEqual(store.load(), active_payload)
            self.assertEqual(store.peek_control_request(), control_payload)

            self.assertEqual(set(root.rglob("*")), before)

    def test_mutate_preserves_concurrent_progress_and_runtime_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            now = datetime.now(timezone.utc)
            store.save(
                {
                    "run_id": "run-1",
                    "entries": [{"candidate_id": "candidate-1", "status": "running"}],
                    "runtime": {
                        "lifecycle_state": "active_scan",
                        "lease_duration_seconds": 420,
                    },
                }
            )

            store.refresh_runtime_lease(now=now)
            payload = store.mutate(
                lambda current: {
                    **current,
                    "run_metadata": {"status": "running"},
                }
            )

            runtime = payload["runtime"]
            self.assertEqual(payload["entries"][0]["candidate_id"], "candidate-1")
            self.assertEqual(runtime["updated_at"], now.isoformat())
            self.assertEqual(
                runtime["lease_expires_at"],
                (now + timedelta(seconds=420)).isoformat(),
            )

    def test_runtime_heartbeat_does_not_recreate_a_cleared_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ActiveRunStore(Path(temp_dir) / "active_run.json")

            store.refresh_runtime_lease(now=datetime.now(timezone.utc))

            self.assertIsNone(store.load())

    def test_failed_runtime_state_persists_error_and_terminal_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            store.save(
                {
                    "run_id": "run-1",
                    "run_metadata": {"status": "running"},
                    "runtime": {"lifecycle_state": "active_scan"},
                }
            )

            store.update_run_metadata({"status": "failed"})
            store.update_runtime_state(
                "failed",
                updated_at="2026-07-18T12:00:00+00:00",
                last_error="synthetic hard failure",
            )

            payload = store.load()

            self.assertEqual(payload["run_metadata"]["status"], "failed")
            self.assertEqual(payload["runtime"]["lifecycle_state"], "failed")
            self.assertEqual(
                payload["runtime"]["last_error"],
                "synthetic hard failure",
            )

    def test_peek_control_does_not_consume_stop_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            store.request_control("stop")

            self.assertEqual(store.peek_control(), "stop")
            self.assertEqual(store.peek_control(), "stop")
            self.assertEqual(store.consume_control(), "stop")
            self.assertIsNone(store.peek_control())

    def test_control_request_is_atomic_and_scoped_to_active_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = ActiveRunStore(root / "active_run.json")
            store.save({"run_id": "run-1"})

            store.request_control("pause", client_session_id="client-1")

            payload = json.loads(store.control_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["action"], "pause")
            self.assertEqual(payload["run_id"], "run-1")
            self.assertTrue(payload["request_id"])
            self.assertEqual(payload["client_session_id"], "client-1")

    def test_clearing_old_request_id_does_not_remove_newer_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = ActiveRunStore(root / "active_run.json")
            store.save({"run_id": "run-1"})
            store.request_control("pause")
            first_request = store.peek_control_request()
            self.assertIsNotNone(first_request)

            store.request_control("stop")
            store.clear_control(request_id=str(first_request["request_id"]))  # type: ignore[index]

            self.assertEqual(store.peek_control(), "stop")

    def test_clear_removes_control_for_the_cleared_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = ActiveRunStore(root / "active_run.json")
            store.save({"run_id": "run-1"})
            store.request_control("stop")

            store.clear()

            self.assertIsNone(store.load())
            self.assertIsNone(store.peek_control_request())

    def test_clear_preserves_control_for_a_different_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = ActiveRunStore(root / "active_run.json")
            store.save({"run_id": "run-1"})
            store.request_control("stop", run_id="run-2")

            store.clear()

            self.assertIsNone(store.load())
            payload = store.peek_control_request()
            self.assertIsNotNone(payload)
            self.assertEqual(payload["run_id"], "run-2")

    def test_reset_preserves_same_run_control_and_clears_different_run_control(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = ActiveRunStore(root / "active_run.json")
            store.save({"run_id": "run-1"})
            store.request_control("stop")
            coordinator = RunControlCoordinator(store)

            coordinator.reset(run_id="run-1")
            self.assertIsNotNone(store.peek_control_request())

            coordinator.reset(run_id="run-2")
            self.assertIsNone(store.peek_control_request())

    def test_bound_coordinator_does_not_claim_control_for_a_newer_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            store.save({"run_id": "run-1"})
            coordinator = RunControlCoordinator(store, run_id="run-1")

            store.save({"run_id": "run-2"})
            store.request_control("stop")

            self.assertIsNone(coordinator.poll())
            self.assertEqual(store.peek_control(), "stop")

    def test_coordinator_drops_old_action_when_active_run_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            store.save({"run_id": "run-1"})
            coordinator = RunControlCoordinator(store, run_id="run-1")
            store.request_control("pause")
            self.assertEqual(coordinator.poll(), "pause")

            store.save({"run_id": "run-2"})
            store.request_control("stop")

            self.assertIsNone(coordinator.poll())
            self.assertEqual(store.peek_control(), "stop")

    def test_old_coordinator_reset_preserves_new_run_control(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            store.save({"run_id": "run-1"})
            coordinator = RunControlCoordinator(store, run_id="run-1")

            store.save({"run_id": "run-2"})
            store.request_control("stop")
            coordinator.reset()

            self.assertEqual(store.load()["run_id"], "run-2")  # type: ignore[index]
            self.assertEqual(store.peek_control(), "stop")

    def test_clear_for_an_old_run_does_not_remove_new_active_run_or_mailbox(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            store.save({"run_id": "run-1"})
            store.save({"run_id": "run-2"})
            store.request_control("stop")

            store.clear(run_id="run-1")

            self.assertEqual(store.load()["run_id"], "run-2")  # type: ignore[index]
            self.assertEqual(store.peek_control(), "stop")

    def test_clear_for_run_uses_repair_identity_when_top_level_run_id_is_absent(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            store.save(
                {
                    "repair_operation_kind": "candidate_repair",
                    "repair_run_id": "run-repair",
                }
            )

            store.clear(run_id="run-repair")

            self.assertIsNone(store.load())

    def test_old_repair_clear_does_not_remove_a_new_regular_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            store.save(
                {
                    "run_id": "run-regular-new",
                    "repair_run_id": "run-repair-old",
                }
            )

            store.clear(run_id="run-repair-old")

            self.assertEqual(store.load()["run_id"], "run-regular-new")  # type: ignore[index]

    def test_old_repair_clear_does_not_remove_a_new_repair_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            store.save({"repair_run_id": "run-repair-new"})

            store.clear(run_id="run-repair-old")

            self.assertEqual(
                store.load()["repair_run_id"],  # type: ignore[index]
                "run-repair-new",
            )

    def test_coordinator_binds_repair_only_active_payload_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            store.save({"repair_run_id": "run-repair-old"})
            coordinator = RunControlCoordinator(store)

            store.save({"repair_run_id": "run-repair-new"})
            store.request_control("stop")

            self.assertIsNone(coordinator.poll())
            self.assertEqual(store.peek_control(), "stop")

    def test_claimed_stop_remains_effective_for_a_later_pause_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = ActiveRunStore(root / "active_run.json")
            store.save({"run_id": "run-1"})
            coordinator = RunControlCoordinator(store, run_id="run-1")
            command = RunControlCommand(store, HistoryStore(root / "history.jsonl"))

            command.request("stop", terminate_children=lambda _path: 0)
            self.assertEqual(coordinator.poll(), "stop")

            paused = command.request(
                "pause",
                client_session_id="client-1",
                terminate_children=lambda _path: 0,
            )

            self.assertEqual(paused["action"], "stop")
            self.assertEqual(paused["message"], "正在停止")
            self.assertIsNone(store.peek_control_request())

    def test_claim_cleans_a_malformed_control_mailbox(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            store.control_path.write_text("{", encoding="utf-8")

            self.assertIsNone(store.consume_control())
            self.assertFalse(store.control_path.exists())

    def test_control_command_reports_stop_when_pause_is_superseded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = ActiveRunStore(root / "active_run.json")
            store.save({"run_id": "run-1"})
            command = RunControlCommand(
                store,
                HistoryStore(root / "history.jsonl"),
            )

            stopped = command.request(
                "stop",
                terminate_children=lambda _path: 0,
            )
            paused = command.request(
                "pause",
                client_session_id="client-1",
                terminate_children=lambda _path: 0,
            )

            self.assertEqual(stopped["action"], "stop")
            self.assertEqual(stopped["message"], "正在停止")
            self.assertEqual(paused["action"], "stop")
            self.assertEqual(paused["message"], "正在停止")

    def test_stop_has_priority_over_a_later_pause_for_the_same_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = ActiveRunStore(root / "active_run.json")
            store.save({"run_id": "run-1"})
            store.request_control("stop")
            first_request = store.peek_control_request()

            store.request_control("pause")
            current_request = store.peek_control_request()

            self.assertEqual(current_request["action"], "stop")  # type: ignore[index]
            self.assertEqual(
                current_request["request_id"],  # type: ignore[index]
                first_request["request_id"],  # type: ignore[index]
            )

    def test_stop_escalates_a_previously_claimed_pause(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = ActiveRunStore(root / "active_run.json")
            store.save({"run_id": "run-1"})
            coordinator = RunControlCoordinator(store)

            store.request_control("pause")
            self.assertEqual(coordinator.poll(), "pause")
            store.request_control("stop")

            self.assertEqual(coordinator.poll(), "stop")

    def test_concurrent_processes_keep_control_mailbox_atomic_and_claimable(self) -> None:
        context = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "active_run.json"
            store = ActiveRunStore(path)
            store.save({"run_id": "run-1"})
            start_event = context.Event()
            processes = [
                context.Process(
                    target=_concurrent_control_writer,
                    args=(str(path), "pause", start_event, 40),
                ),
                context.Process(
                    target=_concurrent_control_writer,
                    args=(str(path), "stop", start_event, 40),
                ),
                context.Process(
                    target=_concurrent_control_reader,
                    args=(str(path), start_event, 120),
                ),
            ]
            for process in processes:
                process.start()
            start_event.set()
            for process in processes:
                process.join(15)
                if process.is_alive():
                    process.terminate()
                    process.join()
                    self.fail("control mailbox process did not finish")
                self.assertEqual(process.exitcode, 0)

            payload = store.peek_control_request()
            self.assertIsNotNone(payload)
            self.assertEqual(payload["run_id"], "run-1")  # type: ignore[index]
            self.assertTrue(payload["request_id"])  # type: ignore[index]
            claimed = store.claim_control()
            self.assertEqual(claimed["request_id"], payload["request_id"])  # type: ignore[index]
            self.assertIsNone(store.peek_control_request())

    def test_claim_does_not_remove_a_request_written_after_the_claim(self) -> None:
        context = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "active_run.json"
            store = ActiveRunStore(path)
            store.save({"run_id": "run-1"})
            store.request_control("stop")
            initial = store.peek_control_request()
            self.assertIsNotNone(initial)

            claimed_event = context.Event()
            release_event = context.Event()
            writer_ready_event = context.Event()
            claimer = context.Process(
                target=_concurrent_control_claimer,
                args=(str(path), claimed_event, release_event),
            )
            writer = context.Process(
                target=_concurrent_control_writer_after_claim,
                args=(str(path), claimed_event, writer_ready_event),
            )
            claimer.start()
            self.assertTrue(claimed_event.wait(10))
            writer.start()
            self.assertTrue(writer_ready_event.wait(10))
            release_event.set()
            for process in (claimer, writer):
                process.join(15)
                if process.is_alive():
                    process.terminate()
                    process.join()
                    self.fail("claim/writer process did not finish")
                self.assertEqual(process.exitcode, 0)

            current = store.peek_control_request()
            self.assertIsNotNone(current)
            self.assertEqual(current["action"], "pause")
            self.assertEqual(current["run_id"], "run-1")
            self.assertNotEqual(
                current["request_id"], initial["request_id"]  # type: ignore[index]
            )
            claimed = store.claim_control()
            self.assertEqual(claimed["request_id"], current["request_id"])  # type: ignore[index]

    def test_pause_suppression_does_not_recreate_a_missing_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            store.suppress_auto_resume_for_session(
                "client-1",
                paused_at="2026-08-06T12:00:00+08:00",
            )
            self.assertIsNone(store.load())


if __name__ == "__main__":
    unittest.main()
