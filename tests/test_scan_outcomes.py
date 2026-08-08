from __future__ import annotations

import unittest

from scanner.scan_outcomes import ScanOutcomeCoordinator


class RecordingSession:
    def __init__(self, *, control_action: str | None = None) -> None:
        self.control_action = control_action
        self.calls: list[tuple[str, dict[str, object]]] = []

    def finish_controlled(self, **kwargs: object) -> str:
        self.calls.append(("controlled", kwargs))
        return "running"

    def finish_completed(self, **kwargs: object) -> str:
        self.calls.append(("completed", kwargs))
        checkpoint = kwargs.get("persist_retained_checkpoint")
        if callable(checkpoint):
            checkpoint()
        retained = kwargs.get("on_retained")
        if callable(retained):
            retained(str(kwargs["retained_lifecycle"]))
        return str(kwargs.get("retained_lifecycle") or "idle")

    def finish_failed(self, **kwargs: object) -> str:
        self.calls.append(("failed", kwargs))
        return "failed"


def coordinator(
    session: RecordingSession,
    *,
    result_level: str = "complete",
    selection_mode: str = "regular",
    effective_candidate_ids: list[str] | None = None,
) -> ScanOutcomeCoordinator[object]:
    return ScanOutcomeCoordinator(
        session=session,  # type: ignore[arg-type]
        base_run_metadata={
            "run_id": "run-1",
            "status": "running",
            "is_complete_regular_round": False,
            "completed_at": None,
        },
        evaluation_result_level=result_level,
        selection_mode=selection_mode,
        effective_candidate_ids=effective_candidate_ids or ["candidate-a"],
        regular_candidate_ids=["candidate-a"],
        timestamp=lambda: "2026-07-29T11:00:00+08:00",
    )


class ScanOutcomeCoordinatorTests(unittest.TestCase):
    def test_completed_status_policy_covers_success_degraded_and_circuit(self) -> None:
        cases = (
            (False, 0, "completed", "run.completed", "finalizing", True),
            (False, 1, "degraded", "run.completed", "finalizing", False),
            (True, 3, "failed", "run.failed", "failed", False),
        )

        for (
            circuit_open,
            hard_error_count,
            expected_status,
            expected_event,
            expected_lifecycle,
            expected_complete_round,
        ) in cases:
            with self.subTest(status=expected_status):
                session = RecordingSession()
                outcomes = coordinator(session)

                metadata = outcomes.finish_completed(
                    circuit_open=circuit_open,
                    hard_error_count=hard_error_count,
                    result_count=2,
                    progress_completed=2,
                    progress_total=2,
                    retain_finalizing_state=False,
                )

                self.assertEqual(metadata["status"], expected_status)
                self.assertEqual(
                    metadata["is_complete_regular_round"],
                    expected_complete_round,
                )
                self.assertEqual(
                    metadata["completed_at"],
                    "2026-07-29T11:00:00+08:00",
                )
                method, kwargs = session.calls[0]
                self.assertEqual(method, "completed")
                self.assertEqual(kwargs["journal_event_type"], expected_event)
                self.assertEqual(kwargs["retained_lifecycle"], expected_lifecycle)
                self.assertEqual(
                    kwargs["journal_data"],
                    {
                        "status": expected_status,
                        "result_count": 2,
                        "progress_completed": 2,
                        "progress_total": 2,
                    },
                )

    def test_retained_completion_passes_metadata_and_lifecycle_callbacks(self) -> None:
        session = RecordingSession()
        outcomes = coordinator(session)
        checkpoints: list[dict[str, object]] = []
        retained_lifecycles: list[str] = []

        metadata = outcomes.finish_completed(
            circuit_open=False,
            hard_error_count=0,
            result_count=1,
            progress_completed=1,
            progress_total=1,
            retain_finalizing_state=True,
            persist_retained_checkpoint=checkpoints.append,
            on_retained=retained_lifecycles.append,
        )

        self.assertEqual(checkpoints, [metadata])
        self.assertEqual(retained_lifecycles, ["finalizing"])
        kwargs = session.calls[0][1]
        self.assertFalse(kwargs["clear_active_run"])
        self.assertTrue(kwargs["capture_before_clear"])
        self.assertTrue(callable(kwargs["persist_retained_checkpoint"]))
        self.assertEqual(kwargs["on_retained"], retained_lifecycles.append)

    def test_controlled_outcome_preserves_scan_control_policy(self) -> None:
        session = RecordingSession(control_action="pause")
        outcomes = coordinator(session)

        metadata = outcomes.finish_controlled(
            action="pause",
            progress_completed=2,
            progress_total=5,
        )

        self.assertEqual(
            session.calls[0],
            (
                "controlled",
                {
                    "run_metadata": metadata,
                    "journal_event_type": "run.paused",
                    "journal_data": {
                        "progress_completed": 2,
                        "progress_total": 5,
                    },
                    "persist_controlled_metadata": True,
                    "transition_before_persist": False,
                    "settle_after": False,
                },
            ),
        )

    def test_exception_outcome_marks_empty_and_partial_runs(self) -> None:
        for result_count, expected_status in ((0, "failed"), (2, "partial")):
            with self.subTest(status=expected_status):
                session = RecordingSession()
                outcomes = coordinator(session)

                metadata = outcomes.finish_exception(
                    error_message="runner failed",
                    result_count=result_count,
                )

                self.assertEqual(metadata["status"], expected_status)
                self.assertEqual(
                    session.calls[0],
                    (
                        "failed",
                        {
                            "run_metadata": metadata,
                            "error_message": "runner failed",
                            "journal_event_type": "run.failed",
                            "journal_data": {
                                "error_message": "runner failed",
                                "result_count": result_count,
                            },
                            "clear_active_run": False,
                            "retain_active_checkpoint": True,
                        },
                    ),
                )


if __name__ == "__main__":
    unittest.main()
