from __future__ import annotations

import json
import unittest

from scanner.bounded_json import (
    BoundedJSONError,
    MAX_INTEGER_DIGITS,
    MAX_JSON_DEPTH,
    MAX_RESPONSE_BYTES,
    MAX_STRING_BYTES,
    bounded_json_loads,
)
from scanner.graders import grade_answer
from scanner.session_bundle_scenario_grader import grade_response as grade_session
from scanner.transaction_regression_grader import grade_response as grade_transaction


class BoundedJSONTest(unittest.TestCase):
    def test_accepts_normal_json_and_rejects_each_shared_limit(self) -> None:
        self.assertEqual(bounded_json_loads('{"ok": [1, true, null]}'), {"ok": [1, True, None]})

        oversize = '"' + ("x" * MAX_RESPONSE_BYTES) + '"'
        with self.assertRaisesRegex(BoundedJSONError, "response_too_large"):
            bounded_json_loads(oversize)
        with self.assertRaisesRegex(BoundedJSONError, "response_too_large"):
            bounded_json_loads(" " * (MAX_RESPONSE_BYTES + 1), strip_code_fence=True)

        nested = "0"
        for _ in range(MAX_JSON_DEPTH + 1):
            nested = "[" + nested + "]"
        with self.assertRaisesRegex(BoundedJSONError, "json_depth_exceeded"):
            bounded_json_loads(nested)

        with self.assertRaisesRegex(BoundedJSONError, "string_too_large"):
            bounded_json_loads(json.dumps("x" * (MAX_STRING_BYTES + 1)))

        huge_integer = "1" + ("0" * MAX_INTEGER_DIGITS)
        with self.assertRaisesRegex(BoundedJSONError, "integer_too_large"):
            bounded_json_loads(huge_integer)

    def test_all_three_target_graders_fail_closed_for_oversized_responses(self) -> None:
        oversized = "{" + ("x" * MAX_RESPONSE_BYTES) + "}"
        self.assertEqual(grade_session(oversized)["status"], "invalid_json")
        self.assertEqual(grade_transaction(oversized)["status"], "invalid_test_cases")

        result = grade_answer(
            oversized,
            {
                "kind": "mutation_test_design",
                "test_suite": "cache_regression_mutants",
                "pass_threshold": 10,
            },
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.diagnostics["status"], "invalid_test_cases")

    def test_all_three_target_graders_reject_deep_and_large_values(self) -> None:
        deep_value: object = "ok"
        for _ in range(MAX_JSON_DEPTH + 1):
            deep_value = [deep_value]

        session_payload = {
            "tests": [
                {
                    "name": "deep",
                    "steps": [
                        {
                            "op": "save",
                            "target": "missing",
                            "checks": [{"path": "status", "equals": deep_value}],
                        }
                    ],
                }
            ]
        }
        self.assertEqual(grade_session(json.dumps(session_payload))["status"], "invalid_json")

        transaction_payload = {
            "tests": [
                {
                    "name": "large",
                    "frames": [
                        {
                            "id": "A",
                            "after": [],
                            "ops": [
                                {
                                    "op": "put",
                                    "key": "k",
                                    "value": "x" * (MAX_STRING_BYTES + 1),
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        self.assertEqual(
            grade_transaction(json.dumps(transaction_payload))["status"],
            "invalid_test_cases",
        )

        mutation_payload = {
            "tests": [
                {
                    "name": "huge",
                    "files": [
                        {
                            "path": "a.py",
                            "content_hash": "h",
                            "issue_count": 10**19,
                        }
                    ],
                    "cache": {},
                    "params": {
                        "current_day": 12,
                        "config_hash": "cfg",
                        "profile_name": "strict",
                        "profile_hash": "p1",
                        "options_key": "opt",
                        "cache_expiry_days": 7,
                        "force_rescan": False,
                        "warm_cache": False,
                    },
                }
            ]
        }
        result = grade_answer(
            json.dumps(mutation_payload),
            {
                "kind": "mutation_test_design",
                "test_suite": "cache_regression_mutants",
                "pass_threshold": 10,
            },
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.diagnostics["status"], "invalid_test_cases")


if __name__ == "__main__":
    unittest.main()
