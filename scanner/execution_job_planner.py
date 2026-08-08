from __future__ import annotations

from .execution import ExecutionJob
from .legacy_scan_compat import SCAN_PHASE
from .models import ResolvedScanTarget
from .question_bank import QuestionSpec


class ExecutionJobPlanner:
    """Pure ordering policy for scan and repair execution jobs."""

    def plan_scan_missing_steps(
        self,
        *,
        targets: list[ResolvedScanTarget],
        questions: list[QuestionSpec],
        completed_steps: set[tuple[str, str, str]],
    ) -> list[ExecutionJob]:
        jobs: list[ExecutionJob] = []
        for attempt_index, question in enumerate(questions, start=1):
            for target in targets:
                step_key = (target.candidate_id, SCAN_PHASE, question.id)
                if step_key in completed_steps:
                    continue
                jobs.append(
                    ExecutionJob(
                        target=target,
                        question=question,
                        attempt_index=attempt_index,
                    )
                )
        return jobs

    def plan_candidate_repair(
        self,
        *,
        target: ResolvedScanTarget,
        repair_questions: list[QuestionSpec],
        all_questions: list[QuestionSpec],
    ) -> list[ExecutionJob]:
        question_index = self._question_index(all_questions)
        return [
            ExecutionJob(
                target=target,
                question=question,
                attempt_index=question_index[question.id],
            )
            for question in repair_questions
        ]

    def plan_batch_repair(
        self,
        *,
        targets: list[ResolvedScanTarget],
        repair_questions_by_candidate: dict[str, list[QuestionSpec]],
        all_questions: list[QuestionSpec],
    ) -> list[ExecutionJob]:
        question_index = self._question_index(all_questions)
        repair_targets = [
            target
            for target in targets
            if target.candidate_id in repair_questions_by_candidate
        ]
        max_repair_steps = max(
            len(repair_questions_by_candidate[target.candidate_id])
            for target in repair_targets
        )
        jobs: list[ExecutionJob] = []
        for step_index in range(max_repair_steps):
            for target in repair_targets:
                candidate_steps = repair_questions_by_candidate[target.candidate_id]
                if step_index >= len(candidate_steps):
                    continue
                question = candidate_steps[step_index]
                jobs.append(
                    ExecutionJob(
                        target=target,
                        question=question,
                        attempt_index=question_index[question.id],
                    )
                )
        return jobs

    @staticmethod
    def _question_index(questions: list[QuestionSpec]) -> dict[str, int]:
        return {
            question.id: index
            for index, question in enumerate(questions, start=1)
        }
