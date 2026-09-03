from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scanner.question_bank import QuestionBank
from scanner.runner import run_target
from scanner.models import TargetConfig


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class V4QuestionPackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bank = QuestionBank(PROJECT_ROOT / "questions").load()

    def test_current_pack_is_five_native_twenty_point_questions(self) -> None:
        self.assertEqual(self.bank.metadata.question_pack_version, "coding-fast-v4.13")
        self.assertEqual(self.bank.question_count, 5)
        self.assertEqual(
            [question.grader.payload["max_score"] for question in self.bank.questions],
            [20, 20, 20, 20, 20],
        )
        self.assertEqual(
            sum(int(question.grader.payload["max_score"]) for question in self.bank.questions),
            100,
        )

    def test_coding_questions_directory_contains_only_the_current_catalog_pack(self) -> None:
        questions_root = PROJECT_ROOT / "questions"
        catalog = json.loads(
            (questions_root / "catalog.json").read_text(encoding="utf-8")
        )
        expected_files = {"catalog.json"}
        for question in catalog["questions"]:
            expected_files.add(str(question["prompt_path"]))
            expected_files.add(str(question["answer_path"]))

        actual_files = set()
        for path in questions_root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(questions_root)
            if relative.parts[0] == "frontend":
                continue
            actual_files.add(relative.as_posix())

        self.assertEqual(actual_files, expected_files)
        self.assertFalse((questions_root / "archive").exists())

    def test_current_pack_uses_only_v4_grader_contracts(self) -> None:
        contracts = {
            question.id: (
                question.grader.kind,
                question.grader.payload["test_suite"],
                question.grader.payload["pass_threshold"],
            )
            for question in self.bank.questions
        }
        self.assertEqual(
            contracts,
            {
                "01_session_bundle_repair": (
                    "session_bundle_relation_repair",
                    "session_bundle_relation_repair_v1",
                    20,
                ),
                "02_code_counterexample_maxgap": (
                    "retry_counterexample_design",
                    "retry_planner_mutants_v3",
                    20,
                ),
                "03_ci_optimality_certificate": (
                    "ci_adversarial_audit",
                    "ci_adversarial_audit_certificate_v4",
                    20,
                ),
                "04_transaction_regression_design": (
                    "transaction_regression_design",
                    "transaction_replay_mutants_v2",
                    20,
                ),
                "05_cache_regression_test_design": (
                    "cache_propagation_certificate",
                    "compact_propagation_certificate_v1",
                    20,
                ),
            },
        )

    def test_q1_prompt_exposes_the_frozen_relation_repair_contract(self) -> None:
        prompt = self.bank.questions[0].prompt

        self.assertIn("Without tools or code", prompt)
        self.assertIn("Use at most six pairs", prompt)
        self.assertIn("Starting 18 proposals", prompt)
        self.assertIn("Alternative proposals", prompt)
        self.assertIn("No-overwrite to an existing target", prompt)
        self.assertIn("The shell starts at count 40", prompt)
        self.assertNotIn('[["p02","p01"]]', prompt)

    def test_prompts_use_behavior_language_instead_of_grader_gaming_instructions(self) -> None:
        for question in self.bank.questions:
            with self.subTest(question=question.id):
                lowered = question.prompt.lower()
                self.assertNotIn("hidden buggy", lowered)
                self.assertNotIn("killed", lowered)
                self.assertNotIn("internal reasoning tokens", lowered)

    def test_mock_runner_respects_each_question_max_score(self) -> None:
        target = TargetConfig(model="gpt-5.6-sol", effort="xhigh")
        for question in self.bank.questions:
            with self.subTest(question=question.id):
                result = run_target(target, question, use_mock_results=True)
                self.assertEqual(result.scorer_diagnostics["semantic_passed"], 20)
                self.assertEqual(result.scorer_diagnostics["semantic_total"], 20)

    def test_current_swift_score_contract_consumes_native_twenty_point_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "v4-score-contract-tests"
            compile_result = subprocess.run(
                [
                    "swiftc",
                    "-module-cache-path",
                    str(Path(temp_dir) / "module-cache"),
                    "Sources/Model/AppLanguageStore.swift",
                    "Sources/Localization/L10n.swift",
                    "Sources/Model/LocalEncryptedSecretStore.swift",
                    "Sources/Model/SelectionModels.swift",
                    "Sources/Model/ComparisonPresenter.swift",
                    "tests/swift/V4ScoreContractTests.swift",
                    "-o",
                    str(executable),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            run_result = subprocess.run(
                [str(executable)],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(run_result.returncode, 0, run_result.stderr)
            self.assertIn("V4 score contract tests passed", run_result.stdout)


if __name__ == "__main__":
    unittest.main()
