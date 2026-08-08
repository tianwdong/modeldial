from __future__ import annotations

from datetime import datetime, timezone
import unittest

from scanner.advisor_v2 import build_advisor_evidence_context


NOW = datetime(2026, 7, 25, 8, 0, tzinfo=timezone.utc)


def _configuration(
    configuration_id: str,
    *,
    enabled: bool = True,
    connection_ready: bool = True,
    identity_resolved: bool = True,
    route_fingerprint: str | None = None,
) -> dict[str, object]:
    return {
        "model_configuration_id": configuration_id,
        "enabled": enabled,
        "connection_ready": connection_ready,
        "identity_resolved": identity_resolved,
        "route_fingerprint": route_fingerprint,
    }


def _row(
    configuration_id: str,
    *,
    completed_at: str = "2026-07-25T07:00:00Z",
    complete: bool = True,
    hard_failure: bool = False,
    route_fingerprint: str | None = None,
    question_pack_version: str = "coding-fast-v4.10",
    grader_version: str = "grader-v2",
) -> dict[str, object]:
    return {
        "model_configuration_id": configuration_id,
        "completed_at": completed_at,
        "complete": complete,
        "hard_failure": hard_failure,
        "question_pack_version": question_pack_version,
        "grader_version": grader_version,
        "route_fingerprint": route_fingerprint,
    }


def _source(
    source: str,
    rows: list[dict[str, object]],
    *,
    published_at: str = "2026-07-25T07:30:00Z",
) -> dict[str, object]:
    return {
        "source": source,
        "snapshot_id": f"{source}-snapshot",
        "published_at": published_at,
        "question_pack_version": "coding-fast-v4.10",
        "grader_version": "grader-v2",
        "rows": rows,
    }


class AdvisorEvidenceContextV2Tests(unittest.TestCase):
    def test_auto_prefers_qualifying_local_current_evidence(self) -> None:
        configurations = [
            _configuration("current"),
            _configuration("local-candidate"),
            _configuration("official-candidate"),
        ]
        context = build_advisor_evidence_context(
            source_mode="auto",
            current_configuration_id="current",
            configurations=configurations,
            local_evaluation=_source(
                "local_evaluation",
                [_row("current"), _row("local-candidate")],
            ),
            official_snapshot=_source(
                "official_snapshot",
                [_row("current"), _row("official-candidate")],
            ),
            now=NOW,
        )

        self.assertEqual(context["resolved_data_source"], "local_evaluation")
        self.assertEqual(context["current_status"], "ready")
        self.assertEqual(context["eligible_candidate_ids"], ["local-candidate"])
        self.assertEqual(context["testable_candidate_ids"], ["official-candidate"])

    def test_auto_falls_back_to_actionable_official_snapshot(self) -> None:
        configurations = [
            _configuration("current"),
            _configuration("official-candidate"),
        ]
        context = build_advisor_evidence_context(
            source_mode="auto",
            current_configuration_id="current",
            configurations=configurations,
            local_evaluation=_source(
                "local_evaluation",
                [_row("current", completed_at="2026-07-21T07:00:00Z")],
            ),
            official_snapshot=_source(
                "official_snapshot",
                [_row("current"), _row("official-candidate")],
            ),
            now=NOW,
        )

        self.assertEqual(context["resolved_data_source"], "official_snapshot")
        self.assertEqual(context["eligible_candidate_ids"], ["official-candidate"])

    def test_auto_does_not_select_official_snapshot_without_an_actionable_candidate(self) -> None:
        context = build_advisor_evidence_context(
            source_mode="auto",
            current_configuration_id="current",
            configurations=[_configuration("current"), _configuration("candidate")],
            local_evaluation=None,
            official_snapshot=_source(
                "official_snapshot",
                [_row("current"), _row("candidate", complete=False)],
            ),
            now=NOW,
        )

        self.assertIsNone(context["resolved_data_source"])
        self.assertEqual(context["current_status"], "needs_test")
        self.assertEqual(context["eligible_candidate_ids"], [])
        self.assertEqual(context["testable_candidate_ids"], ["candidate"])

    def test_manual_source_selection_never_mixes_rows(self) -> None:
        context = build_advisor_evidence_context(
            source_mode="local_evaluation",
            current_configuration_id="current",
            configurations=[
                _configuration("current"),
                _configuration("local-candidate"),
                _configuration("official-candidate"),
            ],
            local_evaluation=_source(
                "local_evaluation",
                [_row("current"), _row("local-candidate")],
            ),
            official_snapshot=_source(
                "official_snapshot",
                [_row("current"), _row("official-candidate")],
            ),
            now=NOW,
        )

        self.assertEqual(context["resolved_data_source"], "local_evaluation")
        self.assertEqual(context["eligible_candidate_ids"], ["local-candidate"])
        self.assertEqual(context["testable_candidate_ids"], ["official-candidate"])

    def test_missing_manual_source_does_not_borrow_other_source_evidence(self) -> None:
        context = build_advisor_evidence_context(
            source_mode="local_evaluation",
            current_configuration_id="current",
            configurations=[_configuration("current"), _configuration("candidate")],
            local_evaluation=None,
            official_snapshot=_source(
                "official_snapshot",
                [_row("current"), _row("candidate")],
            ),
            now=NOW,
        )

        self.assertIsNone(context["resolved_data_source"])
        self.assertEqual(context["eligible_candidate_ids"], [])
        self.assertEqual(context["testable_candidate_ids"], ["candidate"])
        self.assertEqual(
            context["candidate_decisions"][0]["reasons"],
            ["missing_result"],
        )

    def test_candidate_ladder_filters_route_completeness_and_connection_state(self) -> None:
        configurations = [
            _configuration("current", route_fingerprint="route-a"),
            _configuration("eligible", route_fingerprint="route-a"),
            _configuration("route-mismatch", route_fingerprint="route-a"),
            _configuration("incomplete"),
            _configuration("disconnected", connection_ready=False),
            _configuration("disabled", enabled=False),
            _configuration("unmapped", identity_resolved=False),
        ]
        context = build_advisor_evidence_context(
            source_mode="local_evaluation",
            current_configuration_id="current",
            configurations=configurations,
            local_evaluation=_source(
                "local_evaluation",
                [
                    _row("current", route_fingerprint="route-a"),
                    _row("eligible", route_fingerprint="route-a"),
                    _row("route-mismatch", route_fingerprint="route-b"),
                    _row("incomplete", complete=False),
                    _row("disconnected"),
                    _row("disabled"),
                    _row("unmapped"),
                ],
            ),
            official_snapshot=None,
            now=NOW,
        )

        self.assertEqual(context["eligible_candidate_ids"], ["eligible"])
        self.assertEqual(
            context["testable_candidate_ids"],
            ["incomplete", "route-mismatch"],
        )
        decisions = {
            item["model_configuration_id"]: item
            for item in context["candidate_decisions"]
        }
        self.assertEqual(decisions["route-mismatch"]["reasons"], ["route_mismatch"])
        self.assertEqual(decisions["incomplete"]["reasons"], ["incomplete_result"])
        self.assertEqual(decisions["disconnected"]["status"], "ineligible")
        self.assertEqual(decisions["disabled"]["reasons"], ["disabled"])
        self.assertEqual(decisions["unmapped"]["reasons"], ["identity_unresolved"])

    def test_display_ranks_ignore_filtered_rows(self) -> None:
        current = _row("current", route_fingerprint="route-a")
        current["overall_score"] = 90
        eligible = _row("eligible", route_fingerprint="route-a")
        eligible["overall_score"] = 80
        disabled = _row("disabled")
        disabled["overall_score"] = 99
        route_mismatch = _row("route-mismatch", route_fingerprint="route-b")
        route_mismatch["overall_score"] = 95
        context = build_advisor_evidence_context(
            source_mode="local_evaluation",
            current_configuration_id="current",
            configurations=[
                _configuration("current", route_fingerprint="route-a"),
                _configuration("eligible", route_fingerprint="route-a"),
                _configuration("disabled", enabled=False),
                _configuration("route-mismatch", route_fingerprint="route-a"),
            ],
            local_evaluation=_source(
                "local_evaluation",
                [disabled, route_mismatch, current, eligible],
            ),
            official_snapshot=None,
            now=NOW,
        )

        rows = {
            row["model_configuration_id"]: row
            for row in context["resolved_result_rows"]
        }
        self.assertEqual(rows["current"]["display_rank"], 1)
        self.assertEqual(rows["eligible"]["display_rank"], 2)
        self.assertIsNone(rows["disabled"]["display_rank"])
        self.assertIsNone(rows["route-mismatch"]["display_rank"])

    def test_missing_current_usage_has_an_explicit_empty_state(self) -> None:
        context = build_advisor_evidence_context(
            source_mode="auto",
            current_configuration_id=None,
            configurations=[_configuration("candidate")],
            local_evaluation=None,
            official_snapshot=None,
            now=NOW,
        )

        self.assertEqual(context["current_status"], "no_usage")
        self.assertIsNone(context["resolved_data_source"])
        self.assertEqual(context["eligible_candidate_ids"], [])
        self.assertEqual(context["testable_candidate_ids"], [])

    def test_missing_current_id_can_preserve_an_unmapped_detection_state(self) -> None:
        context = build_advisor_evidence_context(
            source_mode="auto",
            current_configuration_id=None,
            current_status_hint="unmapped",
            configurations=[_configuration("candidate")],
            local_evaluation=None,
            official_snapshot=None,
            now=NOW,
        )

        self.assertEqual(context["current_status"], "unmapped")
        self.assertEqual(context["source_reason"], "current_unmapped")


if __name__ == "__main__":
    unittest.main()
