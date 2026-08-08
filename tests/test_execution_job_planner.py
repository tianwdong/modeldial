from __future__ import annotations

from types import SimpleNamespace
import unittest

from scanner.execution_job_planner import ExecutionJobPlanner
from scanner.legacy_scan_compat import SCAN_PHASE


def target(candidate_id: str) -> SimpleNamespace:
    return SimpleNamespace(candidate_id=candidate_id)


def question(question_id: str) -> SimpleNamespace:
    return SimpleNamespace(id=question_id)


class ExecutionJobPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = ExecutionJobPlanner()

    def test_scan_jobs_are_question_major_and_skip_completed_steps(self) -> None:
        targets = [target("a"), target("b")]
        questions = [question("q1"), question("q2")]

        jobs = self.planner.plan_scan_missing_steps(
            targets=targets,  # type: ignore[arg-type]
            questions=questions,  # type: ignore[arg-type]
            completed_steps={
                ("b", SCAN_PHASE, "q1"),
                ("a", SCAN_PHASE, "q2"),
            },
        )

        self.assertEqual(
            [(job.candidate_id, job.question.id) for job in jobs],
            [("a", "q1"), ("b", "q2")],
        )
        self.assertEqual([job.attempt_index for job in jobs], [1, 2])
        self.assertEqual([job.result_phase for job in jobs], [SCAN_PHASE, SCAN_PHASE])

    def test_candidate_repair_keeps_planned_order_and_full_pack_attempt_index(self) -> None:
        all_questions = [question("q1"), question("q2"), question("q3")]
        repair_questions = [all_questions[2], all_questions[0]]

        jobs = self.planner.plan_candidate_repair(
            target=target("a"),  # type: ignore[arg-type]
            repair_questions=repair_questions,  # type: ignore[arg-type]
            all_questions=all_questions,  # type: ignore[arg-type]
        )

        self.assertEqual([job.question.id for job in jobs], ["q3", "q1"])
        self.assertEqual([job.attempt_index for job in jobs], [3, 1])
        self.assertEqual([job.result_phase for job in jobs], [SCAN_PHASE, SCAN_PHASE])

    def test_batch_repair_round_robins_targets_and_preserves_question_indexes(self) -> None:
        targets = [target("a"), target("b"), target("ignored")]
        all_questions = [question("q1"), question("q2"), question("q3")]

        jobs = self.planner.plan_batch_repair(
            targets=targets,  # type: ignore[arg-type]
            repair_questions_by_candidate={
                "a": [all_questions[0], all_questions[2]],
                "b": [all_questions[1]],
            },  # type: ignore[arg-type]
            all_questions=all_questions,  # type: ignore[arg-type]
        )

        self.assertEqual(
            [(job.candidate_id, job.question.id) for job in jobs],
            [("a", "q1"), ("b", "q2"), ("a", "q3")],
        )
        self.assertEqual([job.attempt_index for job in jobs], [1, 2, 3])
        self.assertEqual(
            [job.result_phase for job in jobs],
            [SCAN_PHASE, SCAN_PHASE, SCAN_PHASE],
        )


if __name__ == "__main__":
    unittest.main()
