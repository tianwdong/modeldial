from __future__ import annotations

import json
import unittest

from scanner.graders import grade_answer
from scanner.session_bundle_scenario_grader import (
    MAX_SCORE,
    MUTANT_IDS,
    MUTANT_VARIANTS,
    grade_response,
)


GOLD_PAYLOAD = {
    "tests": [
        {
            "name": "save_contracts",
            "steps": [
                {
                    "op": "save",
                    "target": "existing",
                    "overwrite": False,
                    "event_count": 2,
                    "metadata_features": ["mutates_during_iteration"],
                    "checks": [
                        {"path": "status", "equals": "FileExistsError"},
                        {"path": "events_consumed", "equals": 0},
                    ],
                },
                {
                    "op": "save",
                    "target": "missing",
                    "metadata_features": [
                        "mutates_during_iteration",
                        "nested_mapping",
                    ],
                    "event_features": ["mutates_after_yield"],
                    "event_count": 3,
                    "mapping_order": "ba",
                    "checks": [
                        {"path": "metadata_snapshot", "equals": "before"},
                        {"path": "event_snapshot", "equals": "before"},
                        {"path": "nested_snapshot", "equals": "normalized"},
                        {"path": "archive.mapping_order", "equals": "ab"},
                        {"path": "durability.temporary_fsync", "equals": True},
                    ],
                },
                {
                    "op": "save",
                    "target": "missing",
                    "event_count": 1001,
                    "checks": [
                        {"path": "status", "equals": "event_limit_error"},
                        {"path": "events_consumed", "equals": 1000},
                    ],
                },
                {
                    "op": "save",
                    "target": "existing",
                    "overwrite": True,
                    "event_count": 1001,
                    "checks": [
                        {"path": "status", "equals": "event_limit_error"},
                        {"path": "events_consumed", "equals": 1000},
                        {"path": "target", "equals": "old"},
                    ],
                },
                {
                    "op": "save",
                    "target": "existing",
                    "overwrite": True,
                    "faults": [
                        "validation",
                        "iteration",
                        "serialization",
                        "member_size",
                        "replace",
                    ],
                    "checks": [
                        {"path": "faults.validation.target", "equals": "old"},
                        {"path": "faults.iteration.target", "equals": "old"},
                        {"path": "faults.serialization.target", "equals": "old"},
                        {"path": "faults.member_size.target", "equals": "old"},
                        {"path": "faults.replace.temporary_exists", "equals": False},
                    ],
                },
                {
                    "op": "save",
                    "target": "missing",
                    "overwrite": True,
                    "faults": [
                        "validation",
                        "iteration",
                        "serialization",
                        "member_size",
                        "replace",
                    ],
                    "checks": [
                        {"path": "faults.validation.target", "equals": "missing"},
                        {"path": "faults.iteration.target", "equals": "missing"},
                        {"path": "faults.serialization.target", "equals": "missing"},
                        {"path": "faults.member_size.target", "equals": "missing"},
                        {"path": "faults.replace.temporary_exists", "equals": False},
                    ],
                },
                {
                    "op": "save",
                    "target": "missing",
                    "race_create": True,
                    "metadata_features": ["mutates_during_iteration"],
                    "checks": [
                        {"path": "status", "equals": "FileExistsError"},
                        {"path": "target", "equals": "rival"},
                    ],
                },
                {
                    "op": "save",
                    "target": "existing",
                    "overwrite": True,
                    "mapping_order": "ba",
                    "clock": 20260723,
                    "directory_fsync": "unsupported",
                    "checks": [
                        {"path": "archive.mapping_order", "equals": "ab"},
                        {"path": "archive.timestamp", "equals": 19800101},
                        {"path": "durability.temporary_fsync", "equals": True},
                        {"path": "durability.parent_fsync_attempted", "equals": True},
                    ],
                },
            ],
        },
        {
            "name": "fault_contexts",
            "steps": [
                {
                    "op": "save",
                    "target": "existing",
                    "overwrite": True,
                    "event_count": 0,
                    "faults": ["validation"],
                    "checks": [
                        {"path": "faults.validation.target", "equals": "old"},
                    ],
                },
                {
                    "op": "save",
                    "target": "existing",
                    "overwrite": True,
                    "event_features": ["mutates_after_yield"],
                    "event_count": 3,
                    "faults": ["iteration"],
                    "checks": [
                        {"path": "faults.iteration.target", "equals": "old"},
                    ],
                },
                {
                    "op": "save",
                    "target": "existing",
                    "overwrite": True,
                    "metadata_features": ["nested_mapping"],
                    "mapping_order": "ba",
                    "faults": ["serialization"],
                    "checks": [
                        {"path": "faults.serialization.target", "equals": "old"},
                    ],
                },
                {
                    "op": "save",
                    "target": "existing",
                    "overwrite": True,
                    "event_count": 1000,
                    "faults": ["member_size"],
                    "checks": [
                        {"path": "faults.member_size.target", "equals": "old"},
                    ],
                },
                {
                    "op": "save",
                    "target": "existing",
                    "overwrite": True,
                    "mapping_order": "ba",
                    "faults": ["replace"],
                    "checks": [
                        {"path": "faults.replace.temporary_exists", "equals": False},
                    ],
                },
            ],
        },
        {
            "name": "replay_contracts",
            "steps": [
                {
                    "op": "replay",
                    "recorded_success": [False, True],
                    "actual_results": ["failure", "success"],
                    "stop_on_error": True,
                    "store_history": True,
                    "checks": [
                        {
                            "path": "outcomes",
                            "equals": [{"seq": 1, "success": False}],
                        }
                    ],
                },
                {
                    "op": "replay",
                    "recorded_success": [False, False, True],
                    "actual_results": ["failure", "success", "raise"],
                    "stop_on_error": False,
                    "store_history": False,
                    "checks": [
                        {
                            "path": "outcomes",
                            "equals": [
                                {"seq": 1, "success": False},
                                {"seq": 2, "success": True},
                            ],
                        },
                        {
                            "path": "store_history_calls",
                            "equals": [False, False, False],
                        },
                        {"path": "call_start_counts", "equals": [40, 40, 40]},
                        {"path": "final_execution_count", "equals": 40},
                    ],
                },
                {
                    "op": "replay",
                    "recorded_success": [True, True],
                    "actual_results": ["failure", "success"],
                    "stop_on_error": False,
                    "store_history": True,
                    "checks": [
                        {
                            "path": "outcomes",
                            "equals": [
                                {"seq": 1, "success": False},
                                {"seq": 2, "success": True},
                            ],
                        },
                        {"path": "store_history_calls", "equals": [True, True]},
                    ],
                },
            ],
        },
    ]
}


class SessionBundleScenarioGraderTest(unittest.TestCase):
    def test_gold_payload_kills_all_twenty_mutants(self) -> None:
        result = grade_response(json.dumps(GOLD_PAYLOAD))

        self.assertEqual(MAX_SCORE, 20)
        self.assertEqual(len(MUTANT_IDS), 20)
        self.assertEqual(len(MUTANT_VARIANTS), 44)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["score"], 20)
        self.assertEqual(result["survived_mutants"], [])
        self.assertEqual(len(result["score_details"]), 20)

    def test_each_gold_step_is_scored_by_reference_mutant_output_difference(self) -> None:
        result = grade_response(json.dumps(GOLD_PAYLOAD))

        killed_by_test = result["killed_by_test"]
        self.assertEqual(
            set(killed_by_test),
            {"save_contracts", "fault_contexts", "replay_contracts"},
        )
        self.assertEqual(
            set(killed_by_test["save_contracts"]),
            set(MUTANT_IDS[:15]),
        )
        self.assertEqual(
            set(killed_by_test["replay_contracts"]),
            set(MUTANT_IDS[15:]),
        )
        self.assertEqual(
            set(killed_by_test["fault_contexts"]),
            set(MUTANT_IDS[5:10]),
        )

    def test_invalid_json_and_schema_fail_without_execution_statuses(self) -> None:
        invalid_json = grade_response("not json")
        invalid_schema = grade_response('{"tests": []}')

        self.assertEqual(invalid_json["status"], "invalid_json")
        self.assertEqual(invalid_schema["status"], "invalid_schema")
        self.assertEqual(invalid_json["score"], 0)
        self.assertEqual(invalid_schema["score"], 0)
        self.assertNotIn("runner_error", {invalid_json["status"], invalid_schema["status"]})

    def test_json_code_fence_is_tolerated(self) -> None:
        response = "```json\n" + json.dumps(GOLD_PAYLOAD) + "\n```"

        self.assertEqual(grade_response(response)["score"], 20)

    def test_undeclared_check_path_length_does_not_invalidate_the_answer(self) -> None:
        payload = {
            "tests": [
                {
                    "name": "unknown_long_path",
                    "steps": [
                        {
                            "op": "save",
                            "target": "missing",
                            "checks": [
                                {"path": "unknown_" + ("x" * 120), "equals": None}
                            ],
                        }
                    ],
                }
            ]
        }

        result = grade_response(json.dumps(payload))

        self.assertEqual(result["status"], "semantic_failed")
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["eligible_steps"], 0)
        self.assertEqual(len(result["invalid_steps"]), 1)

    def test_production_grade_adapter_preserves_twenty_score_details(self) -> None:
        result = grade_answer(
            json.dumps(GOLD_PAYLOAD),
            {
                "kind": "session_bundle_test_design",
                "test_suite": "session_bundle_scenarios_v1",
                "pass_threshold": 20,
            },
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.summary, "session_bundle_scenarios_v1 20/20")
        self.assertEqual(result.diagnostics["semantic_passed"], 20)
        self.assertEqual(result.diagnostics["semantic_total"], 20)
        self.assertEqual(len(result.diagnostics["score_details"]), 20)

    def test_limits_and_unknown_fields_are_rejected(self) -> None:
        too_many_steps = {
            "tests": [
                {
                    "name": "too_many",
                    "steps": [
                        {"op": "save", "target": "missing"}
                        for _ in range(9)
                    ],
                }
            ]
        }
        unknown_field = {
            "tests": [
                {
                    "name": "unknown",
                    "steps": [
                        {"op": "save", "target": "missing", "python": "pass"}
                    ],
                }
            ]
        }

        self.assertEqual(
            grade_response(json.dumps(too_many_steps))["status"],
            "invalid_schema",
        )
        self.assertEqual(
            grade_response(json.dumps(unknown_field))["status"],
            "invalid_schema",
        )

    def test_wrong_reference_check_is_ignored_instead_of_killing_mutants(self) -> None:
        payload = {
            "tests": [
                {
                    "name": "wrong_oracle",
                    "steps": [
                        {
                            "op": "save",
                            "target": "existing",
                            "checks": [
                                {"path": "events_consumed", "equals": 99}
                            ],
                        }
                    ],
                }
            ]
        }

        result = grade_response(json.dumps(payload))

        self.assertEqual(result["status"], "semantic_failed")
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["eligible_steps"], 0)
        self.assertEqual(len(result["invalid_steps"]), 1)

    def test_one_sided_atomicity_probe_does_not_complete_the_axis(self) -> None:
        payload = {
            "tests": [
                {
                    "name": "existing_only",
                    "steps": [
                        {
                            "op": "save",
                            "target": "existing",
                            "overwrite": True,
                            "faults": ["validation"],
                            "checks": [
                                {"path": "faults.validation.target", "equals": "old"}
                            ],
                        }
                    ],
                }
            ]
        }

        result = grade_response(json.dumps(payload))

        self.assertIn(
            "deletes_target_on_validation_failure",
            result["killed_variants"],
        )
        self.assertIn(
            "deletes_target_on_validation_failure__missing",
            result["survived_variants"],
        )
        detail = next(
            item
            for item in result["score_details"]
            if item["id"] == "deletes_target_on_validation_failure"
        )
        self.assertFalse(detail["passed"])


if __name__ == "__main__":
    unittest.main()
