from __future__ import annotations

from collections.abc import Callable
from unittest.mock import patch
import unittest

from scanner.repair_execution import RepairExecutionCommand


class RecordingSession:
    instances: list["RecordingSession"] = []

    @classmethod
    def __class_getitem__(cls, _item: object) -> type["RecordingSession"]:
        return cls

    def __init__(self, **kwargs: object) -> None:
        self.constructor_arguments = kwargs
        self.results: list[object] = []
        self.control_action: str | None = None
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
    operation_kind: str,
    *,
    on_control: Callable[[str], None] | None = None,
) -> RepairExecutionCommand[object]:
    return RepairExecutionCommand(
        run_id="run-1",
        operation_kind=operation_kind,
        total=2,
        max_workers=2,
        state_machine=object(),  # type: ignore[arg-type]
        lifecycle=object(),  # type: ignore[arg-type]
        engine=object(),  # type: ignore[arg-type]
        history_store=object(),  # type: ignore[arg-type]
        active_run_store=object(),  # type: ignore[arg-type]
        on_control=on_control,
    )


class RepairExecutionCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        RecordingSession.instances.clear()

    def test_constructs_and_begins_candidate_session_with_repair_phase(self) -> None:
        control_events: list[str] = []
        on_control = control_events.append
        with patch(
            "scanner.repair_execution.create_execution_session",
            side_effect=lambda **kwargs: RecordingSession(**kwargs),
        ):
            command = build_command(
                "candidate_repair",
                on_control=on_control,
            )
            command.begin(
                run_entries=[{"candidate_id": "candidate-a"}],
                last_run_mode="mock",
                lease_duration_seconds=30,
            )

        session = RecordingSession.instances[0]
        context = session.constructor_arguments["context"]
        self.assertEqual(context.run_id, "run-1")
        self.assertEqual(context.operation_kind, "candidate_repair")
        self.assertEqual(context.total, 2)
        self.assertEqual(context.max_workers, 2)
        self.assertIs(session.constructor_arguments["on_control"], on_control)
        self.assertEqual(
            session.begin_arguments,
            {
                "run_entries": [{"candidate_id": "candidate-a"}],
                "last_run_mode": "mock",
                "current_phase": "repair",
                "lease_duration_seconds": 30,
            },
        )

    def test_candidate_callback_policy_preserves_failure_and_persistence_order(self) -> None:
        callbacks = {name: (lambda *args: None) for name in (
            "run_job",
            "persist_state",
            "on_started",
            "after_started",
            "on_finished",
            "after_finished",
            "discard_result",
        )}
        jobs = [object()]
        with patch(
            "scanner.repair_execution.create_execution_session",
            side_effect=lambda **kwargs: RecordingSession(**kwargs),
        ):
            command = build_command("candidate_repair")
            command.execute_jobs(jobs, **callbacks)  # type: ignore[arg-type]

        recorded_jobs, execute_arguments = RecordingSession.instances[0].execute_arguments
        wired = execute_arguments["callbacks"]
        self.assertIs(recorded_jobs, jobs)
        for name, callback in callbacks.items():
            self.assertIs(getattr(wired, name), callback)
        self.assertFalse(wired.persist_on_start)
        self.assertFalse(wired.persist_on_failure)
        self.assertFalse(wired.persist_on_skip)
        self.assertTrue(execute_arguments["stop_on_failure"])
        self.assertFalse(execute_arguments["persist_before_execute"])

    def test_batch_callback_policy_forwards_stop_discard_and_failure_hooks(self) -> None:
        callbacks = {name: (lambda *args: None) for name in (
            "run_job",
            "persist_state",
            "on_started",
            "after_started",
            "on_stopped",
            "on_failed",
            "on_discarded",
            "on_finished",
            "after_finished",
            "discard_result",
        )}
        with patch(
            "scanner.repair_execution.create_execution_session",
            side_effect=lambda **kwargs: RecordingSession(**kwargs),
        ):
            command = build_command("failed_repair")
            command.execute_jobs([object()], **callbacks)  # type: ignore[arg-type]

        _jobs, execute_arguments = RecordingSession.instances[0].execute_arguments
        wired = execute_arguments["callbacks"]
        for name, callback in callbacks.items():
            self.assertIs(getattr(wired, name), callback)
        self.assertTrue(wired.persist_on_start)
        self.assertTrue(wired.persist_on_failure)
        self.assertTrue(wired.persist_on_skip)
        self.assertFalse(execute_arguments["stop_on_failure"])
        self.assertTrue(execute_arguments["persist_before_execute"])

    def test_control_and_settlement_delegate_without_hidden_transitions(self) -> None:
        with patch(
            "scanner.repair_execution.create_execution_session",
            side_effect=lambda **kwargs: RecordingSession(**kwargs),
        ):
            command = build_command("timeout_repair")
            session = RecordingSession.instances[0]
            session.control_action = "stop"
            self.assertEqual(command.control_action, "stop")
            command.clear_control_action()
            lifecycle = command.settle()

        self.assertIsNone(command.control_action)
        self.assertEqual(session.clear_count, 1)
        self.assertEqual(session.settle_arguments, {})
        self.assertEqual(lifecycle, "idle")
        self.assertIs(command.results, session.results)


if __name__ == "__main__":
    unittest.main()
