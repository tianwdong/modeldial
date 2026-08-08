from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from scanner.active_run_store import ActiveRunStore
from scanner.advisor_v2_adapter import (
    build_advisor_v2_evidence,
    build_advisor_v2_evidence_bundle,
)
from scanner.config_store import ConfigStore
from scanner.history_store import HistoryStore
from scanner.native_bridge import build_snapshot


NOW = datetime(2026, 7, 25, 8, 0, tzinfo=timezone.utc)
CURRENT_ID = "codex-local-default:gpt-5.6-sol:high"
XHIGH_ID = "codex-local-default:gpt-5.6-sol:xhigh"
ENDPOINT_ID = "endpoint-1:gpt-5.6-terra:high"


def _state(
    *,
    endpoint_test_status: str = "ok",
    endpoint_route_status: str = "matched",
    current_candidate_id: str | None = CURRENT_ID,
    detection_status: str = "active_single",
) -> dict[str, object]:
    return {
        "config": {
            "model_ingress": {
                "sources": [
                    {
                        "id": "codex_local",
                        "mode": "local",
                        "enabled": True,
                    },
                    {
                        "id": "custom_endpoint",
                        "mode": "api",
                        "enabled": True,
                    },
                ],
                "connections": [
                    {
                        "id": "codex-local-default",
                        "source_id": "codex_local",
                        "enabled": True,
                        "last_test_status": None,
                        "local_login_verified": False,
                        "provider_preset": "generic",
                        "model_candidates": [
                            {
                                "id": CURRENT_ID,
                                "connection_id": "codex-local-default",
                                "model_id": "gpt-5.6-sol",
                                "scan_profile": "high",
                                "enabled": True,
                            }
                        ],
                    },
                    {
                        "id": "endpoint-1",
                        "source_id": "custom_endpoint",
                        "enabled": True,
                        "last_test_status": endpoint_test_status,
                        "local_login_verified": False,
                        "api_format": "openai_responses",
                        "provider_preset": "custom",
                        "base_url": "https://endpoint.example/v1",
                        "model_candidates": [
                            {
                                "id": ENDPOINT_ID,
                                "connection_id": "endpoint-1",
                                "model_id": "gpt-5.6-terra",
                                "scan_profile": "high",
                                "enabled": True,
                            }
                        ],
                    },
                ],
            },
            "recommendation": {
                "effective_current_candidate_id": current_candidate_id,
                "current_model_detection_status": detection_status,
            },
        },
        "question_pack": {
            "id": "coding-fast",
            "version": "coding-fast-v4.10",
            "question_count": 5,
        },
        "dashboard": {
            "current_run_id": "run-local-v2",
            "run_metadata": {
                "scoring_mode": "semantic_q1_q5_equal_v2",
            },
            "leaderboard": [
                _row(CURRENT_ID, source_mode="local", route_status="not_required"),
                _row(ENDPOINT_ID, source_mode="api", route_status=endpoint_route_status),
            ],
        },
    }


def _row(
    candidate_id: str,
    *,
    source_mode: str,
    route_status: str,
) -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "source_mode": source_mode,
        "route_identity_status": route_status,
        "question_count": 5,
        "question_completed": 5,
        "is_current_pack_comparable": True,
        "question_pack_version": "coding-fast-v4.10",
        "scoring_mode": "semantic_q1_q5_equal_v2",
        "latest_valid_at": "2026-07-25T07:00:00Z",
        "question_results": [
            {"question_id": f"q{index}", "status": "pass"}
            for index in range(1, 6)
        ],
        "overall_score": 80,
        "elapsed_seconds": 300.0,
        "estimated_cost_usd": 0.5,
        "cost_coverage": "complete",
    }


class AdvisorV2AdapterTests(unittest.TestCase):
    def test_official_rows_align_to_local_configuration_identity_without_losing_route(self) -> None:
        state = _state(current_candidate_id=XHIGH_ID)
        state["config"]["model_ingress"]["connections"][0]["model_candidates"].append(
            {
                "id": XHIGH_ID,
                "connection_id": "codex-local-default",
                "model_id": "gpt-5.6-sol",
                "scan_profile": "xhigh",
                "enabled": True,
            }
        )
        route_fingerprint = "sha256:" + "a" * 64
        official_snapshot = {
            "source": "official_snapshot",
            "snapshot_id": "remote-batch",
            "published_at": "2026-07-25T07:30:00Z",
            "question_pack_version": "coding-fast-v4.10",
            "grader_version": "grader-v2",
            "rows": [
                {
                    "model_configuration_id": "cloudflare-reference:gpt-5.6-sol:high",
                    "provider_id": "codex",
                    "canonical_model_id": "gpt-5.6-sol",
                    "reasoning_effort": "high",
                    "completed_at": "2026-07-25T07:00:00Z",
                    "complete": True,
                    "hard_failure": False,
                    "question_pack_version": "coding-fast-v4.10",
                    "grader_version": "grader-v2",
                    "route_fingerprint": route_fingerprint,
                    "route_identity": "first_party_controlled",
                },
                {
                    "model_configuration_id": "cloudflare-reference:gpt-5.6-sol:xhigh",
                    "provider_id": "codex",
                    "canonical_model_id": "gpt-5.6-sol",
                    "reasoning_effort": "xhigh",
                    "completed_at": "2026-07-25T07:00:00Z",
                    "complete": True,
                    "hard_failure": False,
                    "question_pack_version": "coding-fast-v4.10",
                    "grader_version": "grader-v2",
                    "route_fingerprint": route_fingerprint,
                    "route_identity": "first_party_controlled",
                    "route_type": "mixed_transition",
                },
            ],
        }

        evidence = build_advisor_v2_evidence(
            state,
            source_mode="official_snapshot",
            official_snapshot=official_snapshot,
            now=NOW,
        )

        self.assertEqual(evidence["resolved_data_source"], "official_snapshot")
        self.assertEqual(evidence["current_status"], "ready")
        self.assertEqual(evidence["eligible_candidate_ids"], [CURRENT_ID])
        rows = {
            row["model_configuration_id"]: row
            for row in evidence["resolved_result_rows"]
        }
        self.assertEqual(
            rows[XHIGH_ID]["source_model_configuration_id"],
            "cloudflare-reference:gpt-5.6-sol:xhigh",
        )
        self.assertEqual(rows[XHIGH_ID]["route_fingerprint"], route_fingerprint)
        self.assertEqual(rows[XHIGH_ID]["route_identity"], "first_party_controlled")
        self.assertEqual(rows[XHIGH_ID]["route_type"], "mixed_transition")

    def test_active_scan_uses_stable_dashboard_for_local_evidence(self) -> None:
        state = _state()
        stable_dashboard = state["dashboard"]
        state["stable_dashboard"] = stable_dashboard
        state["dashboard"] = {
            "current_run_id": "run-working",
            "run_metadata": {
                "status": "running",
                "scoring_mode": "semantic_q1_q5_equal_v2",
            },
            "leaderboard": [],
        }

        evidence = build_advisor_v2_evidence(state, now=NOW)

        self.assertEqual(evidence["resolved_data_source"], "local_evaluation")
        self.assertEqual(evidence["source_snapshot_id"], "local:run-local-v2")
        self.assertEqual(evidence["current_status"], "ready")

    def test_active_scan_prefers_latest_complete_quick_evidence(self) -> None:
        state = _state()
        state["stable_dashboard"] = deepcopy(state["dashboard"])
        state["stable_dashboard"]["current_run_id"] = "run-full"
        state["stable_dashboard"]["leaderboard"][0]["overall_score"] = 70
        state["stable_evidence_dashboard"] = deepcopy(state["dashboard"])
        state["stable_evidence_dashboard"]["current_run_id"] = "run-quick"
        state["stable_evidence_dashboard"]["leaderboard"][0]["overall_score"] = 85
        state["dashboard"] = {
            "current_run_id": "run-working",
            "run_metadata": {
                "status": "running",
                "scoring_mode": "semantic_q1_q5_equal_v2",
            },
            "leaderboard": [],
        }

        evidence = build_advisor_v2_evidence(state, now=NOW)

        self.assertEqual(evidence["source_snapshot_id"], "local:run-quick")
        rows = {
            row["model_configuration_id"]: row
            for row in evidence["resolved_result_rows"]
        }
        self.assertEqual(rows[CURRENT_ID]["overall_score"], 85)

    def test_completed_quick_scan_uses_current_dashboard_for_local_evidence(self) -> None:
        state = _state()
        state["stable_dashboard"] = deepcopy(state["dashboard"])
        state["stable_dashboard"]["current_run_id"] = "run-stable"
        state["stable_dashboard"]["leaderboard"][0]["overall_score"] = 70
        state["dashboard"]["current_run_id"] = "run-quick"
        state["dashboard"]["run_metadata"].update(
            {
                "status": "completed",
                "evaluation_result_level": "complete",
            }
        )
        state["dashboard"]["leaderboard"][0]["overall_score"] = 85
        state["runtime"] = {"is_running": False}

        evidence = build_advisor_v2_evidence(state, now=NOW)

        self.assertEqual(evidence["source_snapshot_id"], "local:run-quick")
        rows = {
            row["model_configuration_id"]: row
            for row in evidence["resolved_result_rows"]
        }
        self.assertEqual(rows[CURRENT_ID]["overall_score"], 85)

    def test_local_leaderboard_and_endpoint_route_feed_one_local_evaluation(self) -> None:
        evidence = build_advisor_v2_evidence(_state(), now=NOW)

        self.assertEqual(evidence["resolved_data_source"], "local_evaluation")
        self.assertEqual(evidence["current_status"], "ready")
        self.assertEqual(evidence["eligible_candidate_ids"], [ENDPOINT_ID])
        self.assertEqual(evidence["testable_candidate_ids"], [])

    def test_endpoint_route_change_moves_candidate_to_quick_test(self) -> None:
        evidence = build_advisor_v2_evidence(
            _state(endpoint_route_status="changed"),
            now=NOW,
        )

        self.assertEqual(evidence["eligible_candidate_ids"], [])
        self.assertEqual(evidence["testable_candidate_ids"], [ENDPOINT_ID])
        self.assertEqual(
            evidence["candidate_decisions"][0]["reasons"],
            ["route_mismatch"],
        )

    def test_unverified_endpoint_is_not_a_testable_or_recommendable_candidate(self) -> None:
        evidence = build_advisor_v2_evidence(
            _state(endpoint_test_status="failed"),
            now=NOW,
        )

        decision = evidence["candidate_decisions"][0]
        self.assertEqual(decision["status"], "ineligible")
        self.assertEqual(decision["reasons"], ["connection_not_ready"])

    def test_missing_row_grader_is_not_filled_from_current_run(self) -> None:
        state = _state()
        endpoint_row = state["dashboard"]["leaderboard"][1]
        endpoint_row.pop("scoring_mode")

        evidence = build_advisor_v2_evidence(state, now=NOW)

        self.assertEqual(evidence["eligible_candidate_ids"], [])
        self.assertEqual(evidence["testable_candidate_ids"], [ENDPOINT_ID])
        self.assertEqual(
            evidence["candidate_decisions"][0]["reasons"],
            ["grader_version_mismatch"],
        )

    def test_mixed_active_models_are_not_mislabeled_as_no_usage(self) -> None:
        evidence = build_advisor_v2_evidence(
            _state(current_candidate_id=None, detection_status="active_mixed"),
            now=NOW,
        )

        self.assertEqual(evidence["current_status"], "unmapped")
        self.assertEqual(evidence["source_reason"], "current_unmapped")

    def test_active_configuration_sessions_create_one_context_per_configuration(self) -> None:
        state = _state(current_candidate_id=None, detection_status="active_mixed")
        state["config"]["recommendation"]["active_configuration_sessions"] = [
            {
                "candidate_id": CURRENT_ID,
                "mapping_status": "matched",
                "last_active_at": "2026-07-25T07:30:00Z",
            },
            {
                "candidate_id": CURRENT_ID,
                "mapping_status": "matched",
                "last_active_at": "2026-07-25T07:00:00Z",
                "is_currently_producing": True,
            },
            {
                "candidate_id": ENDPOINT_ID,
                "mapping_status": "matched",
                "last_active_at": None,
            },
            {
                "candidate_id": None,
                "mapping_status": "unmapped",
                "last_active_at": "2026-07-25T07:40:00Z",
            },
        ]

        bundle = build_advisor_v2_evidence_bundle(state, now=NOW)

        self.assertEqual(
            [item["model_configuration_id"] for item in bundle["activity"]],
            [CURRENT_ID, ENDPOINT_ID],
        )
        self.assertEqual(bundle["activity"][0]["active_session_count"], 2)
        self.assertEqual(
            bundle["activity"][0]["last_active_at"],
            "2026-07-25T07:30:00Z",
        )
        self.assertTrue(bundle["activity"][0]["is_currently_producing"])
        self.assertEqual(bundle["unmapped_active_session_count"], 1)
        self.assertEqual(
            [item["current_model_configuration_id"] for item in bundle["contexts"]],
            [CURRENT_ID, ENDPOINT_ID],
        )

    def test_each_active_configuration_uses_its_persisted_source_mode(self) -> None:
        state = _state(current_candidate_id=None, detection_status="active_mixed")
        state["config"]["recommendation"].update(
            {
                "source_mode_by_configuration_id": {
                    CURRENT_ID: "local_evaluation",
                    ENDPOINT_ID: "official_snapshot",
                },
                "active_configuration_sessions": [
                    {"candidate_id": CURRENT_ID, "mapping_status": "matched"},
                    {"candidate_id": ENDPOINT_ID, "mapping_status": "matched"},
                ],
            }
        )

        bundle = build_advisor_v2_evidence_bundle(state, now=NOW)

        self.assertEqual(
            [item["source_mode"] for item in bundle["contexts"]],
            ["local_evaluation", "official_snapshot"],
        )

    def test_native_snapshot_exposes_v2_evidence_without_replacing_v1(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot = build_snapshot(
                config_store=ConfigStore(Path(temp_dir) / "config.json"),
                history_store=HistoryStore(Path(temp_dir) / "history.jsonl"),
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                codex_insights={
                    "schema_version": 1,
                    "account": {},
                    "workload": {"aggregates": []},
                },
            )

        self.assertEqual(snapshot["advisor"]["schema_version"], 1)
        self.assertEqual(snapshot["advisor_v2_evidence"]["schema_version"], 2)
        self.assertEqual(snapshot["recommendation_portfolio_v2"]["schema_version"], 2)
        self.assertEqual(snapshot["recommendation_portfolio_v2"]["preference"], "smart")

    def test_native_snapshot_consumes_persisted_preference_and_source_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            config = config_store.load()
            config.recommendation.current_model_mode = "manual"
            config.recommendation.current_default_candidate_id = CURRENT_ID
            config.recommendation.preference = "speed"
            config.recommendation.source_mode_by_configuration_id = {
                CURRENT_ID: "local_evaluation"
            }
            config_store.save(config)

            snapshot = build_snapshot(
                config_store=config_store,
                history_store=HistoryStore(Path(temp_dir) / "history.jsonl"),
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
            )

        self.assertEqual(snapshot["advisor_v2_evidence"]["source_mode"], "local_evaluation")
        self.assertEqual(snapshot["recommendation_portfolio_v2"]["preference"], "speed")


if __name__ == "__main__":
    unittest.main()
