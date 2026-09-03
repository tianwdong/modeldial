from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

from scanner.graders import grade_answer
from scanner.session_bundle_relation_repair_grader import (
    ALTERNATIVE_IDS,
    PROPOSALS,
    START_IDS,
    grade_response,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = PROJECT_ROOT / "questions" / "01_session_bundle_repair.prompt.md"
GOLD_PATH = PROJECT_ROOT / "tests" / "fixtures" / "session_bundle_relation_repair_v1_gold.json"
GRADER = {
    "kind": "session_bundle_relation_repair",
    "test_suite": "session_bundle_relation_repair_v1",
    "pass_threshold": 20,
    "max_score": 20,
}


def _raw(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _gold() -> dict[str, object]:
    return json.loads(GOLD_PATH.read_text(encoding="utf-8"))


class SessionBundleRelationRepairGraderTests(unittest.TestCase):
    def test_formal_prompt_is_the_frozen_v64_prompt(self) -> None:
        self.assertEqual(
            "sha256:" + hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest(),
            "sha256:4c5cc2dc620e065356527d368c30ad28633d26b9f4a46766200880f1b034e6ca",
        )
        self.assertEqual(len(PROMPT_PATH.read_bytes()), 6752)

    def test_relation_bank_matches_the_frozen_v64_contract(self) -> None:
        self.assertEqual(set(PROPOSALS), set(START_IDS) | set(ALTERNATIVE_IDS))
        self.assertEqual(len(START_IDS), 18)
        self.assertEqual(len(ALTERNATIVE_IDS), 12)
        self.assertEqual(
            {proposal_id for proposal_id, item in PROPOSALS.items() if not item["valid"]},
            {"p06", "p07"},
        )

    def test_starting_suite_and_gold_keep_the_v59_scores(self) -> None:
        starting = grade_response('{"replace":[]}')
        gold = grade_response(_raw(_gold()))

        self.assertEqual(
            (starting["status"], starting["coverage_score"], starting["validity_penalty"], starting["score"]),
            ("scored", 11, 1, 10),
        )
        self.assertEqual(
            (gold["status"], gold["coverage_score"], gold["validity_penalty"], gold["score"]),
            ("scored", 20, 0, 20),
        )
        self.assertEqual(
            gold["budget"],
            {"experiments": 11, "relations": 18, "fault_observations": 5},
        )

    def test_replacement_pairs_are_set_edits(self) -> None:
        payload = _gold()
        reversed_payload = {"replace": list(reversed(payload["replace"]))}

        original = grade_response(_raw(payload))
        reversed_grade = grade_response(_raw(reversed_payload))

        self.assertEqual(original["score"], reversed_grade["score"])
        self.assertEqual(original["checks"], reversed_grade["checks"])
        self.assertEqual(original["final_selection"], reversed_grade["final_selection"])

    def test_every_single_in_bank_edit_remains_scoreable(self) -> None:
        for old_id in START_IDS:
            for new_id in ALTERNATIVE_IDS:
                with self.subTest(old_id=old_id, new_id=new_id):
                    result = grade_response(_raw({"replace": [[old_id, new_id]]}))
                    self.assertEqual(result["status"], "scored")
                    self.assertIsInstance(result["score"], int)

    def test_frozen_live_answers_keep_their_scores(self) -> None:
        answers = {
            "luna-1": (10, [["p05", "p07"], ["p11", "p12"], ["p24", "p25"]]),
            "luna-2": (12, [["p11", "p12"], ["p24", "p25"], ["p30", "p35"], ["p37", "p36"]]),
            "fable-1": (17, [["p06", "p04"], ["p05", "p07"], ["p09", "p08"], ["p24", "p25"], ["p30", "p35"], ["p02", "p01"]]),
            "fable-2": (16, [["p06", "p04"], ["p05", "p08"], ["p09", "p12"], ["p24", "p25"], ["p30", "p35"]]),
            "terra-1": (14, [["p06", "p07"], ["p09", "p18"], ["p19", "p35"], ["p24", "p25"], ["p37", "p36"]]),
            "terra-2": (16, [["p06", "p04"], ["p05", "p07"], ["p09", "p08"], ["p19", "p18"], ["p24", "p25"], ["p30", "p35"]]),
            "sol-1": (16, [["p06", "p04"], ["p05", "p07"], ["p09", "p08"], ["p19", "p18"], ["p24", "p25"], ["p30", "p35"]]),
            "sol-2": (16, [["p06", "p04"], ["p05", "p07"], ["p09", "p08"], ["p19", "p18"], ["p24", "p25"], ["p30", "p35"]]),
            "opus-1": (16, [["p06", "p04"], ["p11", "p12"], ["p09", "p08"], ["p24", "p25"], ["p05", "p01"], ["p30", "p35"]]),
            "opus-2": (13, [["p06", "p04"], ["p11", "p12"], ["p09", "p08"], ["p24", "p25"], ["p05", "p07"], ["p30", "p35"]]),
            "opus-3": (16, [["p06", "p04"], ["p11", "p12"], ["p05", "p01"], ["p09", "p08"], ["p24", "p25"], ["p30", "p35"]]),
        }
        for label, (expected_score, replacements) in answers.items():
            with self.subTest(label=label):
                result = grade_response(_raw({"replace": replacements}))
                self.assertEqual((result["status"], result["score"]), ("scored", expected_score))

    def test_structural_errors_are_unscored_by_formal_dispatch(self) -> None:
        invalid_answers = (
            "not json",
            '{"replace":[["p01","p04"]]}',
            '{"replace":[["p02","p99"]]}',
            '{"replace":[["p02","p01"],["p02","p03"]]}',
            '{"replace":[["p02","p01"],["p06","p03"],["p05","p04"],["p09","p07"],["p11","p08"],["p14","p12"],["p15","p18"]]}',
        )
        for raw_answer in invalid_answers:
            with self.subTest(raw_answer=raw_answer):
                result = grade_answer(raw_answer, GRADER)
                self.assertFalse(result.ok)
                self.assertIsNone(result.score)
                self.assertEqual(result.max_score, 20)
                self.assertEqual(result.diagnostics["grade_state"], "invalid_submission")
                self.assertNotIn("semantic_passed", result.diagnostics)

    def test_formal_dispatch_preserves_twenty_point_score(self) -> None:
        result = grade_answer(_raw(_gold()), GRADER)

        self.assertTrue(result.ok)
        self.assertEqual((result.score, result.max_score), (20, 20))
        self.assertEqual(result.summary, "session_bundle_relation_repair_v1 20/20")
        self.assertEqual(result.diagnostics["grade_state"], "scored")
        self.assertEqual(result.diagnostics["status"], "passed")
        self.assertEqual(result.diagnostics["validity_penalty"], 0)


if __name__ == "__main__":
    unittest.main()
