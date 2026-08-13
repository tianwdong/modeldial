from __future__ import annotations

import json
from pathlib import Path
import unittest

from devtools.first_run_acceptance import (
    AcceptanceError,
    EXPECTED_UPDATE_FEED_URL,
    EXPECTED_UPDATE_PUBLIC_KEY,
    MANUAL_BOUNDARIES,
    SYNTHETIC_SECRET_REFERENCE,
    SYNTHETIC_SECRET_VALUE,
    _acceptance_answer,
    build_acceptance_config,
    render_text_report,
    validate_preview,
    validate_snapshot,
)


ROOT = Path(__file__).resolve().parent.parent


class FirstRunAcceptanceTest(unittest.TestCase):
    def test_fixture_answers_the_numeric_question_without_retry(self) -> None:
        answer, expected = _acceptance_answer(
            "A black bag containing candies is sampled.",
            "{}",
        )

        self.assertEqual(answer, "21")
        self.assertTrue(expected)

    def test_acceptance_config_is_isolated_and_has_exactly_two_targets(self) -> None:
        config = build_acceptance_config("http://127.0.0.1:43123/v1")
        enabled_sources = [
            source for source in config.model_ingress.sources if source.enabled
        ]
        enabled_connections = [
            connection
            for connection in config.model_ingress.connections
            if connection.enabled
        ]
        enabled_candidates = [
            candidate
            for connection in enabled_connections
            for candidate in connection.model_candidates
            if candidate.enabled
        ]

        self.assertEqual([source.id for source in enabled_sources], ["custom_endpoint"])
        self.assertEqual(
            [connection.id for connection in enabled_connections],
            ["acceptance-endpoint"],
        )
        self.assertEqual(len(enabled_candidates), 2)
        self.assertEqual(
            {candidate.model_id for candidate in enabled_candidates},
            {"gpt-5.6-luna", "gpt-5.6-terra"},
        )
        self.assertTrue(all(candidate.scan_profile == "low" for candidate in enabled_candidates))
        self.assertEqual(enabled_connections[0].api_key_ref, SYNTHETIC_SECRET_REFERENCE)
        self.assertIsNone(config.recommendation.current_default_candidate_id)

    def test_preview_requires_the_complete_two_by_five_plan(self) -> None:
        payload = {
            "valid": True,
            "reason": None,
            "total_evaluations": 10,
            "effective_candidate_ids": ["candidate-a", "candidate-b"],
        }

        self.assertEqual(
            validate_preview(payload),
            {"candidate_count": 2, "evaluation_count": 10},
        )

        payload["total_evaluations"] = 5
        with self.assertRaisesRegex(AcceptanceError, "10 evaluations"):
            validate_preview(payload)

    def test_snapshot_requires_two_eligible_route_matched_rows(self) -> None:
        payload = {
            "dashboard": {
                "run_metadata": {"is_complete_regular_round": True},
                "leaderboard": [
                    {
                        "candidate_id": "candidate-a",
                        "route_identity_status": "matched",
                        "is_current_pack_comparable": True,
                        "is_current_run_eligible": True,
                        "overall_score": 63,
                        "question_completed": 5,
                    },
                    {
                        "candidate_id": "candidate-b",
                        "route_identity_status": "matched",
                        "is_current_pack_comparable": True,
                        "is_current_run_eligible": True,
                        "overall_score": 51,
                        "question_completed": 5,
                    },
                ],
                "pairwise_comparisons": [
                    {
                        "baseline_candidate_id": "candidate-a",
                        "candidate_id": "candidate-b",
                    },
                    {
                        "baseline_candidate_id": "candidate-b",
                        "candidate_id": "candidate-a",
                    },
                ],
            }
        }

        self.assertEqual(
            validate_snapshot(payload),
            {
                "candidate_count": 2,
                "question_count": 10,
                "pairwise_count": 2,
                "route_statuses": ["matched", "matched"],
            },
        )

        payload["dashboard"]["leaderboard"][0]["route_identity_status"] = "missing"
        with self.assertRaisesRegex(AcceptanceError, "route evidence"):
            validate_snapshot(payload)

    def test_report_keeps_manual_boundaries_and_secrets_out(self) -> None:
        report = {
            "schema_version": 1,
            "status": "passed",
            "checks": [
                {
                    "id": "local_endpoint_scan",
                    "status": "passed",
                    "detail": {"authorization_present": True},
                }
            ],
            "manual_boundaries": list(MANUAL_BOUNDARIES),
            "privacy": {
                "real_model_requests": 0,
                "keychain_reads": 0,
                "user_data_paths": 0,
            },
        }

        rendered = render_text_report(report)
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertIn("人工验收边界", rendered)
        self.assertIn("Keychain", rendered)
        self.assertIn("Gatekeeper", rendered)
        self.assertNotIn(SYNTHETIC_SECRET_VALUE, rendered)
        self.assertNotIn(SYNTHETIC_SECRET_VALUE, serialized)

    def test_tooling_never_hardcodes_user_data_or_calls_keychain(self) -> None:
        source = (ROOT / "devtools" / "first_run_acceptance.py").read_text(
            encoding="utf-8"
        )
        wrapper = (ROOT / "devtools" / "verify-first-run-acceptance.sh").read_text(
            encoding="utf-8"
        )
        documentation = (ROOT / "docs" / "first-run-acceptance.md").read_text(
            encoding="utf-8"
        )

        self.assertIn('ThreadingHTTPServer(("127.0.0.1", 0)', source)
        self.assertIn("TemporaryDirectory", source)
        self.assertIn("--secret-stdin", source)
        self.assertNotIn("/Users/", source)
        self.assertNotIn("find-generic-password", source)
        self.assertIn("MODELDIAL_REFERENCE_SNAPSHOT_URL", wrapper)
        self.assertIn(f"MODELDIAL_UPDATE_FEED_URL:-{EXPECTED_UPDATE_FEED_URL}", wrapper)
        self.assertIn(
            f"MODELDIAL_UPDATE_PUBLIC_ED_KEY:-{EXPECTED_UPDATE_PUBLIC_KEY}",
            wrapper,
        )
        self.assertIn("--skip-build", wrapper)
        self.assertIn("./devtools/verify-first-run-acceptance.sh", documentation)
        self.assertIn("manual_required", documentation)


if __name__ == "__main__":
    unittest.main()
