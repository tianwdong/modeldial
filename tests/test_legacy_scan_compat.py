from __future__ import annotations

from copy import deepcopy
import unittest

from scanner.legacy_scan_compat import (
    metadata_question_count,
    metadata_question_ids,
    normalize_lifecycle,
    normalize_phase,
    normalized_capability_label,
    normalized_detail_label,
    normalized_metadata_projection,
    planned_attempts_payload,
)


class LegacyScanCompatibilityTest(unittest.TestCase):
    def test_old_equal_score_metadata_projects_to_five_questions_without_mutation(self) -> None:
        raw = {
            "run_id": "run-old-equal",
            "question_count": 4,
            "question_ids": ["q1", "q2", "q3", "q4"],
            "review_question_count": 1,
            "review_question_ids": ["q5"],
            "scoring_mode": "semantic_q1_q5_equal_v2",
        }
        original = deepcopy(raw)

        projected = normalized_metadata_projection(raw, {**raw, "status": "completed"})

        self.assertEqual(metadata_question_count(raw), 5)
        self.assertEqual(metadata_question_ids(raw), ["q1", "q2", "q3", "q4", "q5"])
        self.assertEqual(projected["question_count"], 5)
        self.assertEqual(projected["question_ids"], ["q1", "q2", "q3", "q4", "q5"])
        self.assertEqual(raw, original)

    def test_old_active_run_attempts_are_merged_read_only(self) -> None:
        raw = {
            "planned_quick_attempts_by_candidate": {"candidate-a": 4},
            "planned_review_attempts_by_candidate": {"candidate-a": 1},
        }
        original = deepcopy(raw)

        self.assertEqual(planned_attempts_payload(raw), {"candidate-a": 5})
        self.assertEqual(raw, original)

    def test_old_phase_lifecycle_and_q5_labels_map_to_current_contract(self) -> None:
        self.assertEqual(normalize_phase("quick"), "scan")
        self.assertEqual(normalize_phase("review"), "scan")
        self.assertEqual(normalize_lifecycle("active_quick"), "active_scan")
        self.assertEqual(normalize_lifecycle("active_review"), "active_scan")
        self.assertEqual(
            normalized_capability_label("05_cache_regression_test_design", "回归验证"),
            "测试设计",
        )
        self.assertEqual(
            normalized_detail_label("05_cache_regression_test_design", "测试设计"),
            "缓存回归",
        )


if __name__ == "__main__":
    unittest.main()
