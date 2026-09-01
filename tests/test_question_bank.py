from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scanner.graders import grade_answer
from scanner.question_bank import QuestionBank


class QuestionBankTest(unittest.TestCase):
    def test_builtin_question_pack_exposes_catalog_driven_evaluation_profiles(self) -> None:
        bank = QuestionBank(Path("questions")).load()

        self.assertEqual(bank.default_evaluation_profile_id, "quick")
        self.assertEqual(
            [profile.id for profile in bank.evaluation_profiles],
            ["quick", "full"],
        )
        quick = bank.evaluation_profile("quick")
        full = bank.evaluation_profile("full")
        self.assertEqual(quick.label, "快速对比")
        self.assertEqual(quick.result_level, "complete")
        self.assertEqual(quick.question_ids, bank.enabled_question_ids)
        self.assertIsNone(quick.upgrade_to)
        self.assertEqual(quick.score_max, 100)
        self.assertEqual(full.label, "全量扫描")
        self.assertEqual(full.result_level, "complete")
        self.assertEqual(full.question_ids, bank.enabled_question_ids)
        self.assertEqual(full.score_max, 100)
        self.assertEqual(bank.complete_evaluation_profile.id, "full")

    def test_builtin_question_pack_contains_five_peer_questions(self) -> None:
        bank = QuestionBank(Path("questions")).load()

        self.assertEqual(bank.metadata.question_pack_version, "coding-fast-v4.11")
        self.assertEqual(
            bank.enabled_question_ids,
            [
                "01_session_bundle_repair",
                "02_code_counterexample_maxgap",
                "03_ci_optimality_certificate",
                "04_transaction_regression_design",
                "05_cache_regression_test_design",
            ],
        )
        self.assertEqual(bank.question_count, 5)
        q5 = {question.id: question for question in bank.questions}["05_cache_regression_test_design"]
        self.assertTrue(q5.enabled)
        self.assertEqual(q5.capability_label, "测试设计")
        self.assertEqual(q5.grader.kind, "mutation_test_design")
        self.assertEqual(q5.grader.payload["test_suite"], "cache_regression_mutants_v3")
        self.assertEqual(q5.grader.payload["max_score"], 20)
        self.assertEqual(q5.grader.payload["pass_threshold"], 20)

        by_id = {question.id: question for question in bank.questions}
        q1 = by_id["01_session_bundle_repair"]
        self.assertIn("designing a compact black-box regression suite", q1.prompt)
        self.assertIn("Create 1 through 3 named tests", q1.prompt)
        self.assertIn('"tests"', q1.prompt)
        self.assertIn('"op": "save"', q1.prompt)
        self.assertIn('"op": "replay"', q1.prompt)
        self.assertIn("20 independently scored behaviors", q1.prompt)
        self.assertIn("Return only one JSON object", q1.prompt)
        self.assertNotIn("SEARCH/REPLACE", q1.prompt)
        self.assertNotIn("sandbox", q1.prompt.lower())
        self.assertNotIn("test_session_store.py", q1.prompt)
        self.assertEqual(q1.capability_id, "black_box_regression_testing")
        self.assertEqual(q1.capability_label, "契约测试")
        self.assertEqual(q1.detail_label, "边界场景")
        self.assertEqual(q1.grader.kind, "session_bundle_test_design")
        self.assertEqual(q1.grader.payload["test_suite"], "session_bundle_scenarios_v1")
        self.assertEqual(q1.grader.payload["max_score"], 20)
        self.assertEqual(q1.grader.payload["pass_threshold"], 20)
        self.assertIn("contract-test-design", q1.tags)
        self.assertIn("black-box-regression", q1.tags)
        q2 = by_id["02_code_counterexample_maxgap"]
        self.assertIn("constructing counterexamples for a retry planner", q2.prompt)
        self.assertIn("Create up to 3 compact counterexamples", q2.prompt)
        self.assertIn('"counterexamples"', q2.prompt)
        self.assertNotIn("longest_unique", q2.prompt)
        self.assertEqual(q2.capability_id, "debug_counterexample")
        self.assertEqual(q2.capability_label, "反例构造")
        self.assertEqual(q2.detail_label, "重试调度")
        self.assertEqual(q2.grader.kind, "retry_counterexample_design")
        self.assertEqual(q2.grader.payload["test_suite"], "retry_planner_mutants_v3")
        self.assertEqual(q2.grader.payload["max_score"], 20)
        self.assertEqual(q2.grader.payload["pass_threshold"], 20)
        q3 = by_id["03_ci_optimality_certificate"]
        self.assertEqual(q3.title, "CI Adversarial Audit")
        self.assertIn("small reproduction bundle", q3.prompt)
        self.assertIn('"certificate"', q3.prompt)
        self.assertIn("Return exactly 2 scenarios", q3.prompt)
        self.assertIn("complete ordered list", q3.prompt)
        self.assertIn("key named `scenarios`", q3.prompt)
        self.assertNotIn('"counterfactuals"', q3.prompt)
        self.assertNotIn("The grader checks 20", q3.prompt)
        self.assertEqual(q3.capability_id, "ci_plan_audit")
        self.assertEqual(q3.capability_label, "方案审计")
        self.assertEqual(q3.detail_label, "对抗场景")
        self.assertEqual(q3.grader.kind, "ci_adversarial_audit")
        self.assertEqual(
            q3.grader.payload["test_suite"],
            "ci_adversarial_audit_certificate_v4",
        )
        self.assertEqual(q3.grader.payload["max_score"], 20)
        self.assertEqual(q3.grader.payload["pass_threshold"], 20)
        self.assertIn("mutation-testing", q3.tags)
        q4 = by_id["04_transaction_regression_design"]
        self.assertIn("designing compact regression scenarios", q4.prompt)
        self.assertIn("function named `replay_frames`", q4.prompt)
        self.assertIn("Create 1 through 3 test cases", q4.prompt)
        self.assertIn('"tests"', q4.prompt)
        self.assertNotIn("SEARCH/REPLACE", q4.prompt)
        self.assertNotIn("reference/", q4.prompt)
        self.assertNotIn("eval.py", q4.prompt)
        self.assertEqual(q4.capability_id, "state_machine_testing")
        self.assertEqual(q4.capability_label, "状态机")
        self.assertEqual(q4.detail_label, "事务回归")
        self.assertEqual(q4.grader.kind, "transaction_regression_design")
        self.assertEqual(q4.grader.payload["test_suite"], "transaction_replay_mutants_v2")
        self.assertEqual(q4.grader.payload["max_score"], 20)
        self.assertEqual(q4.grader.payload["pass_threshold"], 20)

    def test_cache_regression_test_design_prompt_is_mutation_testing_task(self) -> None:
        bank = QuestionBank(Path("questions")).load()
        q5 = {question.id: question for question in bank.questions}["05_cache_regression_test_design"]

        self.assertIn("You are designing regression tests", q5.prompt)
        self.assertIn("Return only JSON test inputs.", q5.prompt)
        self.assertIn("Create 1 through 3 compact tests", q5.prompt)
        self.assertIn("Prefer tests", q5.prompt)
        self.assertIn("Bug report", q5.prompt)
        self.assertIn("20 independent cache regression failure modes", q5.prompt)
        self.assertIn('"tests"', q5.prompt)
        self.assertNotIn("*** Begin Patch", q5.prompt)
        self.assertNotIn("SEARCH/REPLACE", q5.prompt)
        self.assertNotIn("The intended behavior is:", q5.prompt)
        self.assertNotIn("If cache_expiry_days == 0, all existing cache entries are expired.", q5.prompt)

    def test_load_exposes_pack_metadata_and_enabled_question_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "catalog.json").write_text(
                """
                {
                  "id": "coding-fast",
                  "version": "coding-fast-test",
                  "questions": [
                    {
                      "id": "01_enabled",
                      "title": "Enabled",
                      "enabled": true,
                      "prompt_path": "01_enabled.prompt.md",
                      "answer_path": "01_enabled.answer.json",
                      "tags": []
                    },
                    {
                      "id": "02_disabled",
                      "title": "Disabled",
                      "enabled": false,
                      "prompt_path": "02_disabled.prompt.md",
                      "answer_path": "02_disabled.answer.json",
                      "tags": []
                    }
                  ]
                }
                """.strip(),
                encoding="utf-8",
            )
            for question_id in ["01_enabled", "02_disabled"]:
                (root / f"{question_id}.prompt.md").write_text(
                    "Return only one integer.",
                    encoding="utf-8",
                )
                (root / f"{question_id}.answer.json").write_text(
                    '{"grader": {"kind": "regex", "pattern": "(?<!\\\\d)21(?!\\\\d)"}}',
                    encoding="utf-8",
                )

            bank = QuestionBank(root).load()

            self.assertEqual(bank.metadata.question_pack_id, "coding-fast")
            self.assertEqual(bank.metadata.question_pack_version, "coding-fast-test")
            self.assertEqual(bank.question_count, 1)
            self.assertEqual(bank.enabled_question_ids, ["01_enabled"])
            self.assertEqual([question.id for question in bank.enabled_questions], ["01_enabled"])
            self.assertEqual(bank.default_evaluation_profile_id, "full")
            self.assertEqual(
                [profile.id for profile in bank.evaluation_profiles],
                ["full"],
            )
            self.assertEqual(
                bank.evaluation_profile("full").question_ids,
                ["01_enabled"],
            )
            self.assertEqual(bank.evaluation_profile("full").result_level, "complete")

    def test_profile_question_set_can_change_without_scanner_code_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            questions = []
            for index in range(1, 5):
                question_id = f"0{index}_question"
                questions.append(
                    {
                        "id": question_id,
                        "title": f"Question {index}",
                        "enabled": True,
                        "prompt_path": f"{question_id}.prompt.md",
                        "answer_path": f"{question_id}.answer.json",
                        "tags": [],
                    }
                )
                (root / f"{question_id}.prompt.md").write_text(
                    "Return only one integer.",
                    encoding="utf-8",
                )
                (root / f"{question_id}.answer.json").write_text(
                    '{"grader": {"kind": "regex", "pattern": "21", "max_score": 20}}',
                    encoding="utf-8",
                )
            (root / "catalog.json").write_text(
                json.dumps(
                    {
                        "id": "synthetic",
                        "version": "synthetic-v1",
                        "default_evaluation_profile_id": "quick",
                        "evaluation_profiles": [
                            {
                                "id": "quick",
                                "label": "Quick",
                                "summary": "Q2 and Q4",
                                "question_selector": {
                                    "kind": "explicit",
                                    "question_ids": ["02_question", "04_question"],
                                },
                                "result_level": "provisional",
                                "score_presentation": "raw",
                                "upgrade_to": "full",
                            },
                            {
                                "id": "full",
                                "label": "Full",
                                "summary": "All enabled questions",
                                "question_selector": {"kind": "all_enabled"},
                                "result_level": "complete",
                                "score_presentation": "percent",
                            },
                        ],
                        "questions": questions,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            bank = QuestionBank(root).load()

            self.assertEqual(
                [question.id for question in bank.questions_for_profile("quick")],
                ["02_question", "04_question"],
            )
            self.assertEqual(bank.evaluation_profile("quick").score_max, 40)
            self.assertEqual(bank.evaluation_profile("full").score_max, 80)

    def test_invalid_evaluation_profiles_fail_during_catalog_load(self) -> None:
        invalid_profiles = {
            "empty": [
                {
                    "id": "quick",
                    "label": "Quick",
                    "question_selector": {"kind": "explicit", "question_ids": []},
                    "result_level": "provisional",
                },
                {
                    "id": "full",
                    "label": "Full",
                    "question_selector": {"kind": "all_enabled"},
                    "result_level": "complete",
                },
            ],
            "unknown-question": [
                {
                    "id": "quick",
                    "label": "Quick",
                    "question_selector": {
                        "kind": "explicit",
                        "question_ids": ["99_missing"],
                    },
                    "result_level": "provisional",
                },
                {
                    "id": "full",
                    "label": "Full",
                    "question_selector": {"kind": "all_enabled"},
                    "result_level": "complete",
                },
            ],
            "non-superset-upgrade": [
                {
                    "id": "quick",
                    "label": "Quick",
                    "question_selector": {"kind": "all_enabled"},
                    "result_level": "provisional",
                    "upgrade_to": "full",
                },
                {
                    "id": "full",
                    "label": "Full",
                    "question_selector": {"kind": "all_enabled"},
                    "result_level": "complete",
                },
            ],
        }
        for case_name, profiles in invalid_profiles.items():
            with self.subTest(case_name=case_name), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                (root / "01_question.prompt.md").write_text("Return 21", encoding="utf-8")
                (root / "01_question.answer.json").write_text(
                    '{"grader": {"kind": "regex", "pattern": "21", "max_score": 20}}',
                    encoding="utf-8",
                )
                (root / "catalog.json").write_text(
                    json.dumps(
                        {
                            "id": "invalid",
                            "version": "invalid-v1",
                            "default_evaluation_profile_id": "quick",
                            "evaluation_profiles": profiles,
                            "questions": [
                                {
                                    "id": "01_question",
                                    "title": "Question",
                                    "enabled": True,
                                    "prompt_path": "01_question.prompt.md",
                                    "answer_path": "01_question.answer.json",
                                    "tags": [],
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )

                with self.assertRaises(ValueError):
                    QuestionBank(root).load()

    def test_load_catalog_reads_prompt_and_answer_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "catalog.json").write_text(
                """
                [
                  {
                    "id": "01_candy",
                    "title": "Candy",
                    "capability_id": "worst_case_reasoning",
                    "capability_label": "最坏情况",
                    "detail_label": "部分可见抽样",
                    "enabled": true,
                    "prompt_path": "01_candy.prompt.md",
                    "answer_path": "01_candy.answer.json",
                    "tags": ["integer-output"]
                  }
                ]
                """.strip(),
                encoding="utf-8",
            )
            (root / "01_candy.prompt.md").write_text(
                "Return only one integer.",
                encoding="utf-8",
            )
            (root / "01_candy.answer.json").write_text(
                '{"grader": {"kind": "regex", "pattern": "(?<!\\\\d)21(?!\\\\d)"}}',
                encoding="utf-8",
            )

            bank = QuestionBank(root).load()

            self.assertEqual(len(bank.questions), 1)
            self.assertEqual(bank.questions[0].id, "01_candy")
            self.assertEqual(bank.questions[0].capability_id, "worst_case_reasoning")
            self.assertEqual(bank.questions[0].capability_label, "最坏情况")
            self.assertEqual(bank.questions[0].detail_label, "部分可见抽样")
            self.assertEqual(bank.questions[0].grader.kind, "regex")
            self.assertIn("Return only one integer.", bank.questions[0].prompt)

    def test_builtin_question_answer_files_match_known_truth(self) -> None:
        bank = QuestionBank(Path("questions")).load()
        by_id = {question.id: question for question in bank.questions}

        for wrong_text in ("", "21", "not a patch"):
            with self.subTest(wrong_text=wrong_text):
                self.assertFalse(
                    grade_answer(
                        wrong_text,
                        by_id["01_session_bundle_repair"].grader.payload,
                    ).ok
                )

        result_02 = grade_answer(
            (Path(__file__).parent / "fixtures" / "retry_planner_counterexamples.json").read_text(
                encoding="utf-8"
            ),
            by_id["02_code_counterexample_maxgap"].grader.payload,
        )
        self.assertGreater(result_02.score or 0, 0)
        self.assertEqual(result_02.max_score, 20)

        result_03 = grade_answer(
            (
                Path(__file__).parent
                / "fixtures"
                / "ci_adversarial_audit_certificate_v4.json"
            ).read_text(encoding="utf-8"),
            by_id["03_ci_optimality_certificate"].grader.payload,
        )
        self.assertEqual((result_03.score, result_03.max_score), (20, 20))

        result_04 = grade_answer(
            (Path(__file__).parent / "fixtures" / "transaction_regression_design.json").read_text(
                encoding="utf-8"
            ),
            by_id["04_transaction_regression_design"].grader.payload,
        )
        self.assertGreater(result_04.score or 0, 0)
        self.assertEqual(result_04.max_score, 20)
        self.assertEqual(result_04.diagnostics["semantic_total"], 20)


if __name__ == "__main__":
    unittest.main()
