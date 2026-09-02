from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

from scanner.cache_propagation_certificate_grader import (
    BEHAVIOR_MUTANTS,
    CERTIFICATE_FACETS,
    grade_response,
)
from scanner.graders import cache_propagation_certificate_facets, grade_answer


PROJECT_ROOT = Path(__file__).resolve().parent.parent
GOLD_PATH = PROJECT_ROOT / "tests" / "fixtures" / "cache_propagation_certificate_v1_gold.json"
PROMPT_PATH = PROJECT_ROOT / "questions" / "05_unified_diff_patch_applicator.prompt.md"
GRADER = {
    "kind": "cache_propagation_certificate",
    "test_suite": "compact_propagation_certificate_v1",
    "pass_threshold": 20,
    "max_score": 20,
}


def _gold() -> dict[str, object]:
    return json.loads(GOLD_PATH.read_text(encoding="utf-8"))


def _raw(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class CachePropagationCertificateGraderTests(unittest.TestCase):
    def test_formal_prompt_is_the_frozen_v28_prompt(self) -> None:
        self.assertEqual(
            "sha256:" + hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest(),
            "sha256:52a7471776065b74c732b919a26c77d7e4f8145163ee53662f466d1cb3d4717b",
        )

    def test_gold_kills_all_behavior_mutants_and_completes_certificate(self) -> None:
        result = grade_response(_raw(_gold()))

        self.assertEqual((result["status"], result["score"]), ("scored", 20))
        self.assertEqual(set(result["killed_by_evidence"]), set(BEHAVIOR_MUTANTS))
        self.assertEqual(
            result["certificate_facets"]["facets"],
            {facet: True for facet in CERTIFICATE_FACETS},
        )

    def test_semantically_unordered_rows_are_canonicalized(self) -> None:
        payload = _gold()
        payload["portfolios"].reverse()
        payload["audit"].reverse()
        for portfolio in payload["portfolios"]:
            for case in portfolio["cases"]:
                case["requires"].reverse()
                case["cache"].reverse()
                case["scans"].reverse()
                outcome = case["outcome"]
                for field in ("decisions", "writes", "kept", "evicted", "failed", "reported"):
                    outcome[field].reverse()
                outcome["counts"]["reasons"].reverse()
        for row in payload["audit"]:
            outcome = row["outcome"]
            for field in ("decisions", "writes", "kept", "evicted", "failed", "reported"):
                outcome[field].reverse()
            outcome["counts"]["reasons"].reverse()

        result = grade_response(_raw(payload))

        self.assertEqual((result["status"], result["score"]), ("scored", 20))

    def test_semantically_invalid_case_is_scored_locally(self) -> None:
        payload = _gold()
        payload["portfolios"][0]["cases"][0]["params"]["capacity"] = 1

        result = grade_response(_raw(payload))

        self.assertEqual((result["status"], result["score"]), ("scored", 12))
        self.assertEqual(len(result["invalid_cases"]), 1)

    def test_fixed_audit_error_removes_matching_certificate_credit(self) -> None:
        payload = _gold()
        payload["audit"][0]["outcome"]["decisions"][0][1] = "not_cached"

        result = grade_response(_raw(payload))

        self.assertEqual((result["status"], result["score"]), ("scored", 14))
        self.assertFalse(result["certificate_facets"]["audit_facets"]["decisions"])

    def test_structural_errors_are_unscored_by_formal_dispatch(self) -> None:
        payload = _gold()
        del payload["portfolios"][0]["cases"][0]["outcome"]["kept"]

        result = grade_answer(_raw(payload), GRADER)

        self.assertFalse(result.ok)
        self.assertIsNone(result.score)
        self.assertEqual(result.max_score, 20)
        self.assertEqual(result.diagnostics["status"], "schema_error")
        self.assertNotIn("semantic_passed", result.diagnostics)

    def test_formal_dispatch_preserves_twenty_point_score(self) -> None:
        result = grade_answer(_raw(_gold()), GRADER)

        self.assertTrue(result.ok)
        self.assertEqual((result.score, result.max_score), (20, 20))
        self.assertEqual(result.diagnostics["grade_state"], "scored")
        self.assertEqual(result.diagnostics["status"], "passed")
        self.assertEqual(
            [item["id"] for item in cache_propagation_certificate_facets(result.diagnostics)],
            ["invalidation", "state", "transaction", "eviction", "metrics", "certificate"],
        )


if __name__ == "__main__":
    unittest.main()
