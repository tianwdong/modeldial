from __future__ import annotations

import unittest

from scanner.recommendation_state import resolve_recommendation_decision


class RecommendationStateTest(unittest.TestCase):
    def test_recommends_best_candidate_when_current_default_is_missing(self) -> None:
        decision = resolve_recommendation_decision(
            current_default_candidate_id=None,
            recommended_candidate_id="candidate-b",
            is_comparable=True,
            retained_after_failure=False,
            latest_error_category=None,
        )

        self.assertEqual(decision.recommendation_outcome, "recommend")
        self.assertEqual(decision.evidence_state, "fresh")
        self.assertEqual(decision.decision_state, "recommend")
        self.assertEqual(decision.title, "推荐当前最佳模型")
        self.assertEqual(decision.action_label, "查看推荐依据")
        self.assertIn("不影响扫描和排序", decision.reason)

    def test_keeps_current_default_when_recommendation_matches(self) -> None:
        decision = resolve_recommendation_decision(
            current_default_candidate_id="candidate-a",
            recommended_candidate_id="candidate-a",
            is_comparable=True,
            retained_after_failure=False,
            latest_error_category=None,
        )

        self.assertEqual(decision.recommendation_outcome, "keep")
        self.assertEqual(decision.decision_state, "keep")

    def test_switches_only_when_different_candidate_is_comparable(self) -> None:
        comparable = resolve_recommendation_decision(
            current_default_candidate_id="candidate-a",
            recommended_candidate_id="candidate-b",
            is_comparable=True,
            retained_after_failure=False,
            latest_error_category=None,
        )
        incomplete = resolve_recommendation_decision(
            current_default_candidate_id="candidate-a",
            recommended_candidate_id="candidate-b",
            is_comparable=False,
            retained_after_failure=False,
            latest_error_category=None,
        )

        self.assertEqual(comparable.recommendation_outcome, "switch")
        self.assertEqual(comparable.decision_state, "switch")
        self.assertEqual(incomplete.recommendation_outcome, "wait")
        self.assertEqual(incomplete.decision_state, "wait")

    def test_transient_failure_retains_previous_evidence(self) -> None:
        decision = resolve_recommendation_decision(
            current_default_candidate_id="candidate-a",
            recommended_candidate_id="candidate-a",
            is_comparable=True,
            retained_after_failure=True,
            latest_error_category="timeout",
        )

        self.assertEqual(decision.recommendation_outcome, "keep")
        self.assertEqual(decision.evidence_state, "retained_after_failure")
        self.assertEqual(decision.decision_state, "retain_after_failure")
        self.assertEqual(decision.action_label, "查看失败详情")

    def test_deterministic_configuration_errors_do_not_hide_behind_old_evidence(self) -> None:
        for category in (
            "authentication_failed",
            "configuration_error",
            "model_not_found",
            "protocol_mismatch",
        ):
            with self.subTest(category=category):
                decision = resolve_recommendation_decision(
                    current_default_candidate_id="candidate-a",
                    recommended_candidate_id="candidate-a",
                    is_comparable=True,
                    retained_after_failure=True,
                    latest_error_category=category,
                )

                self.assertEqual(decision.recommendation_outcome, "wait")
                self.assertEqual(decision.evidence_state, "retained_after_failure")
                self.assertEqual(decision.decision_state, "wait")
                self.assertIn("配置不可用", decision.reason)


if __name__ == "__main__":
    unittest.main()
