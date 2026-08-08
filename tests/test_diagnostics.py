from __future__ import annotations

import json
import unittest

from scanner.diagnostics import build_diagnostic_summary


class DiagnosticSummaryTest(unittest.TestCase):
    def test_summary_whitelists_health_evidence_and_preserves_not_applicable(self) -> None:
        summary = build_diagnostic_summary(
            {
                "question_pack": {
                    "id": "coding-fast",
                    "version": "coding-fast-v4.10",
                },
                "config": {
                    "recommendation": {
                        "current_model_detection_status": "mapped",
                    }
                },
            },
            {
                "account": {
                    "captured_at": "2026-07-24T10:00:00Z",
                    "account_type": "api_key",
                    "login_state": "authenticated",
                    "quota_status": "not_applicable",
                    "email": "private@example.com",
                },
                "workload": {
                    "status": "available",
                    "coverage_started_at": "2026-07-20T10:00:00Z",
                    "coverage_continuous_since": "2026-07-20T10:00:00Z",
                    "coverage_complete": True,
                    "collection": {
                        "source_count": 2,
                        "discovered_file_count": 8,
                        "sampled_file_count": 8,
                        "parsed_file_count": 8,
                        "failed_file_count": 0,
                        "unknown_file_count": 0,
                        "deduplicated_file_count": 1,
                        "budget_limited_file_count": 0,
                        "gap_detected": False,
                        "upstream_retention_risk": "not_detected",
                        "private_path": "/Users/private/.codex/sessions",
                    },
                    "aggregates": [
                        {
                            "completed_work_units": 5,
                            "behavior_observed_work_units": 4,
                            "behavior_coverage_percent": 80.0,
                            "edit_work_units": 3,
                            "retry_observed_edit_work_units": 2,
                            "prompt": "private prompt",
                        }
                    ],
                },
                "quota_burn": {
                    "status": "not_applicable",
                    "rejected_intervals": {"coverage_incomplete": 2},
                },
                "collection": {
                    "app_server": {
                        "status": "fresh",
                        "last_read_at": "2026-07-24T10:00:00Z",
                        "read_duration_ms": 42,
                        "model_catalog_status": "not_checked",
                    }
                },
            },
            {
                "ruleset_version": "advisor-p0-v1",
                "short_circuit_reason": "current_evaluation_incomplete",
                "benefits": {"pricing_snapshot_id": "pricing-v1-2026-07-24-1"},
            },
            generated_at="2026-07-24T10:01:00Z",
        )

        self.assertEqual(summary["schema_version"], 1)
        self.assertEqual(summary["overall_status"], "healthy")
        self.assertEqual(summary["app_server"]["read_duration_ms"], 42)
        self.assertEqual(summary["capabilities"]["account"], "available")
        self.assertEqual(summary["capabilities"]["rate_limits"], "not_applicable")
        self.assertEqual(summary["capabilities"]["model_catalog"], "not_checked")
        self.assertEqual(summary["session_history"]["parsed_file_count"], 8)
        self.assertEqual(summary["session_history"]["unknown_file_count"], 0)
        self.assertEqual(summary["behavior"]["completed_work_units"], 5)
        self.assertEqual(summary["behavior"]["retry_indeterminate_edit_work_units"], 1)
        self.assertEqual(summary["versions"]["question_pack_version"], "coding-fast-v4.10")
        self.assertEqual(summary["advisor_short_circuit_reason"], "current_evaluation_incomplete")
        self.assertEqual(summary["quota_rejected_intervals"], {"coverage_incomplete": 2})

        serialized = json.dumps(summary, ensure_ascii=False)
        for private_value in (
            "private@example.com",
            "/Users/private/.codex/sessions",
            "private prompt",
        ):
            self.assertNotIn(private_value, serialized)

    def test_v2_representative_decision_overrides_legacy_advisor_diagnostics(self) -> None:
        summary = build_diagnostic_summary(
            {"question_pack": {}},
            {
                "account": {
                    "login_state": "authenticated",
                    "quota_status": "not_applicable",
                },
                "workload": {
                    "status": "available",
                    "coverage_complete": True,
                    "aggregates": [],
                },
                "quota_burn": {
                    "status": "not_applicable",
                    "rejected_intervals": {},
                },
                "collection": {
                    "app_server": {
                        "status": "fresh",
                        "model_catalog_status": "not_checked",
                    }
                },
            },
            {
                "ruleset_version": "advisor-p0-v1",
                "short_circuit_reason": "no_candidate_passed_guard",
            },
            recommendation_portfolio={
                "schema_version": 2,
                "representative_configuration_id": "current",
                "status": "recommend",
                "decisions": [
                    {
                        "current_model_configuration_id": "other",
                        "reason": "no_material_benefit",
                    },
                    {
                        "current_model_configuration_id": "current",
                        "reason": "material_time_gain",
                    },
                ],
            },
            generated_at="2026-07-24T10:01:00Z",
        )

        self.assertEqual(
            summary["versions"]["advisor_ruleset_version"],
            "recommendation-portfolio-v2",
        )
        self.assertEqual(
            summary["advisor_short_circuit_reason"],
            "material_time_gain",
        )

    def test_unavailable_collection_is_attention_but_not_applicable_quota_is_not(self) -> None:
        summary = build_diagnostic_summary(
            {"question_pack": {}},
            {
                "account": {
                    "login_state": "unknown",
                    "quota_status": "unavailable",
                },
                "workload": {
                    "status": "unavailable",
                    "coverage_complete": False,
                    "aggregates": [],
                },
                "quota_burn": {"status": "unavailable", "rejected_intervals": {}},
                "collection": {
                    "app_server": {
                        "status": "unavailable",
                        "model_catalog_status": "not_checked",
                    }
                },
            },
            None,
            generated_at="2026-07-24T10:01:00Z",
        )

        self.assertEqual(summary["overall_status"], "attention")
        self.assertEqual(summary["capabilities"]["account"], "unavailable")
        self.assertEqual(summary["capabilities"]["rate_limits"], "unavailable")


if __name__ == "__main__":
    unittest.main()
