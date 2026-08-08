from __future__ import annotations

from collections.abc import Callable
from unittest.mock import patch
import unittest

from scanner.scan_execution import ScanExecutionCommand


class RecordingSession:
    instances: list["RecordingSession"] = []

    def __init__(self, **kwargs: object) -> None:
        self.constructor_arguments = kwargs
        self.results: list[object] = []
        self.control_action: str | None = None
        self.lock = object()
        self.begin_arguments: dict[str, object] | None = None
        self.execute_arguments: tuple[list[object], dict[str, object]] | None = None
        self.settle_arguments: dict[str, object] | None = None
        self.clear_count = 0
        self.__class__.instances.append(self)

    def begin(self, **kwargs: object) -> None:
        self.begin_arguments = kwargs

    def execute_jobs(self, jobs: list[object], **kwargs: object) -> None:
        self.execute_arguments = (jobs, kwargs)

    def clear_control_action(self) -> None:
        self.clear_count += 1
        self.control_action = None

    def settle(self, **kwargs: object) -> str:
        self.settle_arguments = kwargs
        return "idle"


def build_command(
    *,
    on_control: Callable[[str], None] | None = None,
) -> ScanExecutionCommand[object]:
    return ScanExecutionCommand(
        run_id="run-1",
        total=8,
        max_workers=3,
        initial_completed=2,
        circuit_breaker_threshold=3,
        state_machine=object(),  # type: ignore[arg-type]
        lifecycle=object(),  # type: ignore[arg-type]
        engine=object(),  # type: ignore[arg-type]
        history_store=object(),  # type: ignore[arg-type]
        active_run_store=object(),  # type: ignore[arg-type]
        on_control=on_control,
    )


class ScanExecutionCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        RecordingSession.instances.clear()

    def test_constructs_resume_context_and_begins_scan_phase(self) -> None:
        controls: list[str] = []
        on_control = controls.append
        with patch(
            "scanner.scan_execution.create_execution_session",
            side_effect=lambda **kwargs: RecordingSession(**kwargs),
        ):
            command = build_command(on_control=on_control)
            command.begin(
                run_entries=[{"candidate_id": "candidate-a"}],
                last_run_mode="live",
                lease_duration_seconds=45,
            )

        session = RecordingSession.instances[0]
        context = session.constructor_arguments["context"]
        self.assertEqual(context.run_id, "run-1")
        self.assertEqual(context.operation_kind, "scan")
        self.assertEqual(context.total, 8)
        self.assertEqual(context.max_workers, 3)
        self.assertEqual(context.initial_completed, 2)
        self.assertEqual(context.circuit_breaker_threshold, 3)
        self.assertIs(session.constructor_arguments["on_control"], on_control)
        self.assertEqual(
            session.begin_arguments,
            {
                "run_entries": [{"candidate_id": "candidate-a"}],
                "last_run_mode": "live",
                "current_phase": "scan",
                "lease_duration_seconds": 45,
            },
        )

    def test_scan_callback_policy_preserves_circuit_skip_and_failure_hooks(self) -> None:
        callback_names = (
            "run_job",
            "persist_state",
            "can_start",
            "on_not_started",
            "on_started",
            "after_started",
            "on_stopped",
            "on_failed",
            "on_discarded",
            "on_finished",
            "after_finished",
            "on_skipped",
            "discard_result",
        )
        callbacks = {name: (lambda *args: None) for name in callback_names}
        jobs = [object()]
        with patch(
            "scanner.scan_execution.create_execution_session",
            side_effect=lambda **kwargs: RecordingSession(**kwargs),
        ):
            command = build_command()
            command.execute_jobs(jobs, **callbacks)  # type: ignore[arg-type]

        recorded_jobs, execute_arguments = RecordingSession.instances[0].execute_arguments
        wired = execute_arguments["callbacks"]
        self.assertIs(recorded_jobs, jobs)
        for name, callback in callbacks.items():
            self.assertIs(getattr(wired, name), callback)
        self.assertTrue(wired.persist_on_start)
        self.assertTrue(wired.persist_on_failure)
        self.assertTrue(wired.persist_on_skip)
        self.assertTrue(execute_arguments["stop_on_failure"])
        self.assertTrue(execute_arguments["persist_before_execute"])

    def test_control_lock_and_settlement_preserve_scan_policy(self) -> None:
        with patch(
            "scanner.scan_execution.create_execution_session",
            side_effect=lambda **kwargs: RecordingSession(**kwargs),
        ):
            command = build_command()
            session = RecordingSession.instances[0]
            session.control_action = "pause"
            self.assertEqual(command.control_action, "pause")
            self.assertIs(command.lock, session.lock)
            command.clear_control_action()
            lifecycle = command.settle()

        self.assertIsNone(command.control_action)
        self.assertEqual(session.clear_count, 1)
        self.assertEqual(session.settle_arguments, {"control_action": None})
        self.assertEqual(lifecycle, "idle")
        self.assertIs(command.results, session.results)


if __name__ == "__main__":
    unittest.main()
