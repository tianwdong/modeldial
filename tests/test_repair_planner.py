from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scanner.active_run_store import ActiveRunStore
from scanner.comparison_groups import ComparisonGroupProjector
from scanner.config_store import ConfigStore
from scanner.history_store import HistoryStore
from scanner.models import ResolvedScanTarget, ScanResult
from scanner.question_bank import QuestionBank, QuestionSpec
from scanner.repair_planner import RepairPlan, RepairPlanner
from scanner.scan_target_resolver import ScanTargetResolver
from scanner.scoring import EQUAL_SCORING_MODE
from scanner.service import MonitorService


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RepairPlannerTest(unittest.TestCase):
    def test_candidate_plan_is_frozen_read_only_and_only_contains_hard_failures(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            planner, _, _, _, targets, questions, run_id = self._seed_round(
                Path(temp_dir),
                outcome_by_step={(0, 1): "error"},
            )
            before = self._file_snapshot(Path(temp_dir))

            plan = planner.plan_candidate(
                run_id=run_id,
                candidate_id=targets[0].candidate_id,
            )

            self.assertIsInstance(plan, RepairPlan)
            self.assertEqual(plan.operation_kind, "candidate_repair")
            self.assertEqual(
                [question.id for question in plan.steps_for(targets[0].candidate_id)],
                [questions[1].id],
            )
            self.assertEqual(plan.total_steps, 1)
            self.assertEqual(before, self._file_snapshot(Path(temp_dir)))
            with self.assertRaises(FrozenInstanceError):
                plan.operation_kind = "other"  # type: ignore[misc]
            with self.assertRaisesRegex(ValueError, "不是执行失败"):
                planner.plan_candidate(
                    run_id=run_id,
                    candidate_id=targets[0].candidate_id,
                    question_id=questions[0].id,
                )

    def test_matching_candidate_resume_can_return_completion_only_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            planner, history_store, active_run_store, _, targets, questions, run_id = (
                self._seed_round(
                    root,
                    outcome_by_step={(0, 1): "error"},
                )
            )
            target = targets[0]
            repaired_question = questions[1]
            history_store.append(
                self._result(
                    run_id=run_id,
                    target=target,
                    question=repaired_question,
                    attempt_index=2,
                    outcome="pass",
                )
            )
            active_run_store.save(
                {
                    "run_id": run_id,
                    "repair_run_id": run_id,
                    "repair_candidate_id": target.candidate_id,
                    "repair_question_ids": [repaired_question.id],
                    "runtime": {"lifecycle_state": "active_scan"},
                }
            )
            before = self._file_snapshot(root)

            plan = planner.plan_candidate(
                run_id=run_id,
                candidate_id=target.candidate_id,
            )

            self.assertTrue(plan.is_matching_repair)
            self.assertTrue(plan.completion_only)
            self.assertEqual(plan.total_steps, 0)
            self.assertEqual(before, self._file_snapshot(root))

    def test_failed_and_timeout_batch_plans_keep_distinct_question_scopes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            planner, _, _, _, targets, questions, run_id = self._seed_round(
                Path(temp_dir),
                candidate_count=2,
                outcome_by_step={
                    (0, 0): "timeout",
                    (0, 1): "error",
                    (1, 2): "missing",
                    (1, 3): "flag_timeout",
                },
            )

            timeout_plan = planner.plan_timeout_batch(run_id=run_id)
            failed_plan = planner.plan_failed_batch(run_id=run_id)

            self.assertEqual(timeout_plan.operation_kind, "timeout_repair")
            self.assertEqual(
                self._planned_step_ids(timeout_plan),
                {(targets[0].candidate_id, questions[0].id)},
            )
            self.assertEqual(failed_plan.operation_kind, "failed_repair")
            self.assertEqual(
                self._planned_step_ids(failed_plan),
                {
                    (targets[0].candidate_id, questions[0].id),
                    (targets[0].candidate_id, questions[1].id),
                    (targets[1].candidate_id, questions[2].id),
                },
            )
            self.assertNotIn(
                (targets[1].candidate_id, questions[3].id),
                self._planned_step_ids(timeout_plan),
            )

    def test_service_accepts_preplanned_candidate_repair_without_replanning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            planner, history_store, active_run_store, config_store, targets, _, run_id = (
                self._seed_round(
                    root,
                    outcome_by_step={(0, 1): "error"},
                )
            )
            plan = planner.plan_candidate(
                run_id=run_id,
                candidate_id=targets[0].candidate_id,
            )

            def runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                return self._result(
                    run_id=str(kwargs["run_id"]),
                    target=target,
                    question=question,
                    attempt_index=int(kwargs["attempt_index"]),
                    outcome="pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
                runner=runner,
            )
            with patch.object(
                service.repair_planner,
                "plan_candidate",
                side_effect=AssertionError("planner should not run twice"),
            ) as plan_candidate:
                results = service.repair_failed_candidate(
                    run_id=run_id,
                    candidate_id=targets[0].candidate_id,
                    repair_plan=plan,
                )

            plan_candidate.assert_not_called()
            self.assertEqual(len(results), 1)

    def test_service_accepts_preplanned_batch_repair_without_replanning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            planner, history_store, active_run_store, config_store, targets, _, run_id = (
                self._seed_round(
                    root,
                    candidate_count=2,
                    outcome_by_step={(0, 1): "error", (1, 1): "error"},
                )
            )
            plan = planner.plan_failed_batch(run_id=run_id)

            def runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                return self._result(
                    run_id=str(kwargs["run_id"]),
                    target=target,
                    question=question,
                    attempt_index=int(kwargs["attempt_index"]),
                    outcome="pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
                runner=runner,
            )
            with patch.object(
                service.repair_planner,
                "plan_failed_batch",
                side_effect=AssertionError("planner should not run twice"),
            ) as plan_failed_batch:
                results = service.repair_failed_questions(
                    run_id=run_id,
                    repair_plan=plan,
                )

            plan_failed_batch.assert_not_called()
            self.assertEqual(
                {result.candidate_id for result in results},
                {target.candidate_id for target in targets},
            )

    def _seed_round(
        self,
        root: Path,
        *,
        candidate_count: int = 1,
        outcome_by_step: dict[tuple[int, int], str],
    ) -> tuple[
        RepairPlanner,
        HistoryStore,
        ActiveRunStore,
        ConfigStore,
        list[ResolvedScanTarget],
        list[QuestionSpec],
        str,
    ]:
        config_store = ConfigStore(root / "config.json")
        history_store = HistoryStore(root / "history.jsonl")
        active_run_store = ActiveRunStore(root / "active_run.json")
        target_resolver = ScanTargetResolver()
        config = config_store.load()
        available_targets = target_resolver.available_targets(config)
        selected_ids = {
            target.candidate_id for target in available_targets[:candidate_count]
        }
        for connection in config.model_ingress.connections:
            for candidate in connection.model_candidates:
                candidate.enabled = candidate.id in selected_ids
        config_store.save(config)
        targets = target_resolver.enabled_targets(config)
        self.assertEqual(len(targets), candidate_count)

        question_bank = QuestionBank(PROJECT_ROOT / "questions")
        question_pack = question_bank.load()
        questions = question_pack.enabled_questions
        run_id = "repair-plan-run"
        for target_index, target in enumerate(targets):
            for question_index, question in enumerate(questions):
                outcome = outcome_by_step.get(
                    (target_index, question_index),
                    "pass",
                )
                if outcome == "missing":
                    continue
                history_store.append(
                    self._result(
                        run_id=run_id,
                        target=target,
                        question=question,
                        attempt_index=question_index + 1,
                        outcome=outcome,
                    )
                )
        history_store.save_run_metadata(
            {
                "run_id": run_id,
                "question_pack_id": question_pack.metadata.question_pack_id,
                "question_pack_version": (
                    question_pack.metadata.question_pack_version
                ),
                "started_at": "2026-07-28T10:00:00+08:00",
                "completed_at": "2026-07-28T10:05:00+08:00",
                "candidate_count": len(targets),
                "question_count": len(questions),
                "question_ids": [question.id for question in questions],
                "status": "degraded",
                "selection_mode": "regular",
                "requested_candidate_ids": [
                    target.candidate_id for target in targets
                ],
                "regular_candidate_ids": [
                    target.candidate_id for target in targets
                ],
                "is_complete_regular_round": False,
                "scoring_mode": EQUAL_SCORING_MODE,
            }
        )
        projector = ComparisonGroupProjector(target_resolver)
        planner = RepairPlanner(
            config_store=config_store,
            history_store=history_store,
            active_run_store=active_run_store,
            question_bank=question_bank,
            target_resolver=target_resolver,
            comparison_group_projector=projector,
        )
        return (
            planner,
            history_store,
            active_run_store,
            config_store,
            targets,
            questions,
            run_id,
        )

    @staticmethod
    def _result(
        *,
        run_id: str,
        target: ResolvedScanTarget,
        question: QuestionSpec,
        attempt_index: int,
        outcome: str,
    ) -> ScanResult:
        is_timeout = outcome == "timeout"
        is_error = outcome in {"timeout", "error"}
        return ScanResult(
            run_id=run_id,
            candidate_id=target.candidate_id,
            model=target.model,
            effort=target.effort,
            phase="scan",
            question_id=question.id,
            question_title=question.title,
            grader_kind=question.grader.kind,
            attempt_index=attempt_index,
            started_at="2026-07-28T10:00:00+08:00",
            elapsed_seconds=300.0 if is_timeout else 1.0,
            source_mode="live",
            answer_ok=outcome == "pass",
            answer_preview=outcome,
            input_tokens=None if is_error else 100,
            output_tokens=None if is_error else 20,
            reasoning_tokens=None if is_error else 430,
            error_message=(
                "codex exec timed out after 300s"
                if is_timeout
                else "endpoint connection refused"
                if outcome == "error"
                else None
            ),
            flags=["timeout"] if outcome == "flag_timeout" else [],
            final_status=(
                "warn" if outcome in {"timeout", "flag_timeout"} else "error"
                if outcome == "error"
                else "pass"
            ),
        )

    @staticmethod
    def _planned_step_ids(plan: RepairPlan) -> set[tuple[str, str]]:
        return {
            (candidate_id, question.id)
            for candidate_id, questions in plan.repair_steps_by_candidate
            for question in questions
        }

    @staticmethod
    def _file_snapshot(root: Path) -> dict[str, bytes]:
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }


if __name__ == "__main__":
    unittest.main()
