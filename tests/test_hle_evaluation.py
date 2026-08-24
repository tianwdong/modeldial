from __future__ import annotations

import copy
import unittest

from scanner.hle_evaluation import (
    HLE_DEEP20_BENCHMARK_REF,
    HLE_DEEP20_PARSER_ID,
    HLE_DEEP20_PROTOCOL_ID,
    HleEvaluationError,
    build_hle_prompt,
    canonical_payload_sha256,
    load_hle_restricted_bundle,
    prompt_template_sha256,
    score_hle_response,
    source_content_sha256,
)


class HleEvaluationTests(unittest.TestCase):
    def test_loads_frozen_bundle_and_scores_exact_canonical_answer(self) -> None:
        bundle = self._bundle()
        parsed = load_hle_restricted_bundle(
            bundle,
            expected_benchmark_ref=HLE_DEEP20_BENCHMARK_REF,
            expected_manifest_sha256=bundle["manifest"]["canonical_payload_sha256"],
        )

        self.assertEqual(parsed.item_count, 20)
        self.assertEqual(parsed.maximum_score, 100)
        self.assertIn("allowed IDs: A, B", build_hle_prompt(parsed.items[0]))
        self.assertEqual(
            score_hle_response(
                '{"answer":"B"}',
                item=parsed.items[0],
                terminal_state="completed_turn",
            ),
            self._score(valid=True, correct=True, error_kind=None),
        )

    def test_rejects_lenient_output_and_non_completed_turn(self) -> None:
        bundle = self._bundle()
        parsed = load_hle_restricted_bundle(
            bundle,
            expected_benchmark_ref=HLE_DEEP20_BENCHMARK_REF,
            expected_manifest_sha256=bundle["manifest"]["canonical_payload_sha256"],
        )
        item = parsed.items[0]

        duplicate = score_hle_response(
            '{"answer":"B","answer":"A"}',
            item=item,
            terminal_state="completed_turn",
        )
        recovered = score_hle_response(
            '{"answer":"B"}',
            item=item,
            terminal_state="completed_turn_recovered_after_timeout",
        )

        self.assertFalse(duplicate.valid)
        self.assertEqual(duplicate.error_kind, "invalid_json")
        self.assertFalse(recovered.valid)
        self.assertEqual(
            recovered.error_kind,
            "terminal_state:completed_turn_recovered_after_timeout",
        )

    def test_rejects_manifest_or_restricted_content_drift(self) -> None:
        bundle = self._bundle()
        expected = bundle["manifest"]["canonical_payload_sha256"]
        changed_manifest = copy.deepcopy(bundle)
        changed_manifest["manifest"]["scoring"]["points_per_item"] = 9
        changed_content = copy.deepcopy(bundle)
        changed_content["items"][0]["question"] = changed_content["items"][0][
            "question"
        ].replace("B. Second", "B. Third")

        with self.assertRaisesRegex(HleEvaluationError, "canonical payload hash mismatch"):
            load_hle_restricted_bundle(
                changed_manifest,
                expected_benchmark_ref=HLE_DEEP20_BENCHMARK_REF,
                expected_manifest_sha256=expected,
            )
        with self.assertRaisesRegex(HleEvaluationError, "content hash mismatch"):
            load_hle_restricted_bundle(
                changed_content,
                expected_benchmark_ref=HLE_DEEP20_BENCHMARK_REF,
                expected_manifest_sha256=expected,
            )

    def _bundle(self) -> dict:
        questions = [
            (
                f"item-{index:02d}",
                f"Synthetic item {index}?\n\nAnswer Choices:\nA. First\nB. Second",
                "B",
            )
            for index in range(20)
        ]
        manifest = {
            "schema_version": "modeldial.hle-deep-20-golden.v2",
            "golden_set_id": "hle-deep-20",
            "version": "v2",
            "status": "accepted",
            "protocol": {
                "id": HLE_DEEP20_PROTOCOL_ID,
                "parser": HLE_DEEP20_PARSER_ID,
                "prompt_template_sha256": prompt_template_sha256(),
                "timeout_seconds": 300,
                "only_completed_turn_can_score": True,
                "invalid_timeout_or_error_scores_zero": True,
                "selective_retry": False,
            },
            "scoring": {
                "item_count": 20,
                "points_per_item": 5,
                "maximum_score": 100,
                "partial_credit": False,
            },
            "items": [
                {
                    "item_id": item_id,
                    "source_content_sha256": source_content_sha256(question, answer),
                    "option_count": 2,
                }
                for item_id, question, answer in questions
            ],
        }
        manifest["canonical_payload_sha256"] = canonical_payload_sha256(manifest)
        return {
            "schema_version": 1,
            "benchmark_ref": HLE_DEEP20_BENCHMARK_REF,
            "manifest": manifest,
            "items": [
                {
                    "item_id": item_id,
                    "question": question,
                    "answer_choice_id": answer,
                }
                for item_id, question, answer in questions
            ],
        }

    @staticmethod
    def _score(*, valid: bool, correct: bool, error_kind: str | None):
        from scanner.hle_evaluation import HleAnswerScore

        return HleAnswerScore(valid=valid, correct=correct, error_kind=error_kind)


if __name__ == "__main__":
    unittest.main()
