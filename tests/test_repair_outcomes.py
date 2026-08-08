from __future__ import annotations

import unittest

from scanner.repair_outcomes import RepairOutcomeCoordinator


class RecordingSession:
    def __init__(self, *, control_action: str | None = None) -> None:
        self.control_action = control_action
        self.calls: list[tuple[str, dict[str, object]]] = []

    def finish_controlled(self, **kwargs: object) -> str:
        self.calls.append(("controlled", kwargs))
        return "idle"

    def finish_completed(self, **kwargs: object) -> str:
        self.calls.append(("completed", kwargs))
        checkpoint = kwargs.get("persist_retained_checkpoint")
        if callable(checkpoint):
            checkpoint()
        return "finalizing"

    def finish_failed(self, **kwargs: object) -> str:
        self.calls.append(("failed", kwargs))
        return "idle"


def coordinator(
    session: RecordingSession,
    *,
    event_prefix: str = "repair",
    journal_scope: dict[str, object] | None = None,
) -> RepairOutcomeCoordinator[object]:
    return RepairOutcomeCoordinator(
        session=session,  # type: ignore[arg-type]
        base_run_metadata={
            "run_id": "requested-run",
            "status": "running",
            "is_complete_regular_round": True,
            "completed_at": None,
        },
        persist_run_id="persisted-run",
        comparison_group_id="comparison-group",
        event_prefix=event_prefix,
        journal_scope=journal_scope or {"candidate_id": "candidate-a"},
        metadata_factory=lambda history: {
            "run_id": "stale-run",
            "comparison_group_id": "stale-group",
            "status": "completed" if history else "degraded",
        },
        timestamp=lambda: "2026-07-29T10:00:00+08:00",
    )


class RepairOutcomeCoordinatorTests(unittest.TestCase):
    def test_controlled_outcome_normalizes_metadata_and_journal_payload(self) -> None:
        session = RecordingSession(control_action="pause")
        outcomes = coordinator(
            session,
            event_prefix="timeout-repair",
            journal_scope={"candidate_ids": ["candidate-a", "candidate-b"]},
        )

        metadata = outcomes.finish_controlled(
            history=[object()],
            result_count=2,
        )

        self.assertEqual(
            metadata,
            {
                "run_id": "persisted-run",
                "comparison_group_id": "comparison-group",
                "status": "completed",
            },
        )
        self.assertEqual(session.calls[0][0], "controlled")
        self.assertEqual(
            session.calls[0][1],
            {
                "run_metadata": metadata,
                "journal_event_type": "timeout-repair.paused",
                "journal_data": {
                    "candidate_ids": ["candidate-a", "candidate-b"],
                    "result_count": 2,
                },
            },
        )

    def test_completed_outcome_passes_normalized_metadata_to_checkpoint(self) -> None:
        session = RecordingSession()
        outcomes = coordinator(
            session,
            journal_scope={
                "candidate_id": "candidate-a",
                "question_ids": ["q1"],
            },
        )
        checkpoint_metadata: list[dict[str, object]] = []

        metadata = outcomes.finish_completed(
            history=[object()],
            result_count=1,
            retain_finalizing_state=True,
            persist_retained_checkpoint=checkpoint_metadata.append,
        )

        self.assertEqual(checkpoint_metadata, [metadata])
        self.assertEqual(session.calls[0][0], "completed")
        self.assertEqual(
            session.calls[0][1],
            {
                "run_metadata": metadata,
                "journal_event_type": "repair.completed",
                "journal_data": {
                    "candidate_id": "candidate-a",
                    "question_ids": ["q1"],
                    "result_count": 1,
                },
                "clear_active_run": False,
                "settle_before_persist": True,
                "persist_retained_checkpoint": session.calls[0][1][
                    "persist_retained_checkpoint"
                ],
            },
        )

    def test_completion_only_preserves_unstarted_session_settlement_policy(self) -> None:
        session = RecordingSession()
        outcomes = coordinator(
            session,
            journal_scope={
                "candidate_id": "candidate-a",
                "question_ids": [],
            },
        )

        outcomes.finish_completed(
            history=[],
            result_count=0,
            retain_finalizing_state=False,
            completion_only=True,
        )

        self.assertEqual(
            session.calls[0][1],
            {
                "run_metadata": {
                    "run_id": "persisted-run",
                    "comparison_group_id": "comparison-group",
                    "status": "degraded",
                },
                "journal_event_type": "repair.completed",
                "journal_data": {
                    "candidate_id": "candidate-a",
                    "question_ids": [],
                    "result_count": 0,
                },
                "clear_active_run": True,
                "settle_before_checkpoint": False,
                "persist_retained_checkpoint": None,
                "settle_after": False,
            },
        )

    def test_failed_outcome_normalizes_metadata_and_error_journal(self) -> None:
        session = RecordingSession()
        outcomes = coordinator(
            session,
            event_prefix="timeout-repair",
            journal_scope={"candidate_ids": ["candidate-a"]},
        )

        metadata = outcomes.finish_failed(
            error_message="runner failed",
            result_count=3,
        )

        self.assertEqual(
            metadata,
            {
                "run_id": "persisted-run",
                "status": "degraded",
                "is_complete_regular_round": False,
                "completed_at": "2026-07-29T10:00:00+08:00",
                "comparison_group_id": "comparison-group",
            },
        )
        self.assertEqual(
            session.calls[0],
            (
                "failed",
                {
                    "run_metadata": metadata,
                    "error_message": "runner failed",
                    "journal_event_type": "timeout-repair.failed",
                    "journal_data": {
                        "candidate_ids": ["candidate-a"],
                        "error_message": "runner failed",
                        "result_count": 3,
                    },
                    "clear_active_run": True,
                },
            ),
        )

    def test_candidate_and_batch_completed_outcomes_share_one_contract(self) -> None:
        cases = (
            ("repair", {"candidate_id": "a", "question_ids": ["q1"]}),
            ("repair", {"candidate_ids": ["a", "b"]}),
        )

        for event_prefix, journal_scope in cases:
            with self.subTest(journal_scope=journal_scope):
                session = RecordingSession()
                outcomes = coordinator(
                    session,
                    event_prefix=event_prefix,
                    journal_scope=journal_scope,
                )
                outcomes.finish_completed(
                    history=[object()],
                    result_count=1,
                    retain_finalizing_state=False,
                )

                method, kwargs = session.calls[0]
                self.assertEqual(method, "completed")
                self.assertEqual(kwargs["journal_event_type"], "repair.completed")
                self.assertEqual(kwargs["clear_active_run"], True)
                self.assertEqual(kwargs["settle_before_persist"], True)
                self.assertEqual(
                    kwargs["journal_data"],
                    {**journal_scope, "result_count": 1},
                )


if __name__ == "__main__":
    unittest.main()
