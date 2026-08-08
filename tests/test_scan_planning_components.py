from __future__ import annotations

import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

from scanner.active_run_store import ActiveRunStore
from scanner.comparison_groups import ComparisonGroupProjector
from scanner.config_store import ConfigStore
from scanner.history_store import HistoryStore
from scanner.models import (
    AppConfig,
    ConnectionConfig,
    ModelCandidateConfig,
    ScanResult,
)
from scanner.question_bank import QuestionBank
from scanner.scan_planner import ScanPlanner
from scanner.scan_target_resolver import ScanTargetResolver
from scanner.service import MonitorService


def _result(
    *,
    run_id: str,
    model: str,
    effort: str,
    question_id: str,
    candidate_id: str | None = None,
    phase: str = "scan",
) -> ScanResult:
    return ScanResult(
        run_id=run_id,
        candidate_id=candidate_id,
        model=model,
        effort=effort,
        phase=phase,
        question_id=question_id,
        started_at="2026-07-28T10:00:00+08:00",
        elapsed_seconds=1.0,
        source_mode="live",
        answer_ok=True,
        answer_preview="ok",
        input_tokens=10,
        output_tokens=5,
        reasoning_tokens=3,
    )


class ScanTargetResolverTest(unittest.TestCase):
    def test_resolves_target_layers_and_preserves_requested_order(self) -> None:
        config = AppConfig.default()
        resolver = ScanTargetResolver()

        configured = resolver.configured_targets(config)
        connection_ready = resolver.connection_ready_targets(config)
        available = resolver.available_targets(config)
        enabled = resolver.enabled_targets(config)

        self.assertGreater(len(configured), 1)
        self.assertTrue(
            {target.candidate_id for target in enabled}.issubset(
                {target.candidate_id for target in available}
            )
        )
        self.assertTrue(
            {target.candidate_id for target in available}.issubset(
                {target.candidate_id for target in connection_ready}
            )
        )
        enabled_candidate_ids = {
            candidate.id
            for connection in config.model_ingress.connections
            for candidate in connection.model_candidates
            if candidate.enabled
        }
        self.assertEqual(
            [target.candidate_id for target in enabled],
            [
                target.candidate_id
                for target in available
                if target.candidate_id in enabled_candidate_ids
            ],
        )

        requested_ids = [enabled[-1].candidate_id, enabled[0].candidate_id]
        self.assertEqual(
            [
                target.candidate_id
                for target in resolver.requested_targets(config, requested_ids)
            ],
            requested_ids,
        )
        with self.assertRaisesRegex(ValueError, "duplicate candidate_id"):
            resolver.requested_targets(config, [requested_ids[0], requested_ids[0]])
        with self.assertRaisesRegex(ValueError, "unknown candidate_id"):
            resolver.requested_targets(config, ["missing-candidate"])

    def test_resolves_explicit_and_unique_legacy_candidate_identity(self) -> None:
        resolver = ScanTargetResolver()
        targets = resolver.enabled_targets(AppConfig.default())
        candidate_ids_by_label = resolver.candidate_ids_by_label(targets)
        target = targets[0]

        self.assertEqual(
            resolver.entry_candidate_id(
                {"candidate_id": target.candidate_id, "label": "wrong / label"},
                candidate_ids_by_label,
            ),
            target.candidate_id,
        )
        self.assertEqual(
            resolver.result_candidate_id(
                _result(
                    run_id="legacy",
                    model=target.model,
                    effort=target.effort,
                    question_id="q1",
                ),
                candidate_ids_by_label,
            ),
            target.candidate_id,
        )
        ambiguous = dict(candidate_ids_by_label)
        ambiguous[target.label] = [target.candidate_id, "duplicate-route"]
        self.assertIsNone(
            resolver.candidate_id_from_label(target.label, ambiguous)
        )

    def test_availability_layers_preserve_route_identity_for_duplicate_labels(
        self,
    ) -> None:
        config = AppConfig.first_run()
        candidate_ids: list[str] = []
        for suffix, enabled, verified in (
            ("a", True, True),
            ("b", False, True),
            ("c", True, False),
        ):
            connection_id = f"duplicate-{suffix}"
            candidate_id = f"{connection_id}:gpt-5.4:high"
            candidate_ids.append(candidate_id)
            config.model_ingress.connections.append(
                ConnectionConfig(
                    id=connection_id,
                    source_id="custom_endpoint",
                    name=f"Duplicate {suffix.upper()}",
                    enabled=True,
                    api_format="openai_responses",
                    provider_id="openai",
                    base_url=f"https://{suffix}.example.test/v1",
                    api_key_ref=f"keychain://{connection_id}",
                    last_test_status="ok" if verified else "untested",
                    model_candidates=[
                        ModelCandidateConfig(
                            id=candidate_id,
                            connection_id=connection_id,
                            model_id="gpt-5.4",
                            display_name="GPT-5.4 High",
                            family_id="gpt-5.4",
                            enabled=enabled,
                            scan_profile="high",
                        )
                    ],
                )
            )

        resolver = ScanTargetResolver()
        configured = resolver.configured_targets(config)
        available = resolver.available_targets(config)
        enabled = resolver.enabled_targets(config)
        configured_ids = [
            target.candidate_id
            for target in configured
            if target.candidate_id in candidate_ids
        ]
        available_ids = [
            target.candidate_id
            for target in available
            if target.candidate_id in candidate_ids
        ]
        enabled_ids = [
            target.candidate_id
            for target in enabled
            if target.candidate_id in candidate_ids
        ]

        self.assertEqual(configured_ids, candidate_ids)
        self.assertEqual(available_ids, candidate_ids[:2])
        self.assertEqual(enabled_ids, candidate_ids[:1])
        duplicate_targets = [
            target for target in configured if target.candidate_id in candidate_ids
        ]
        candidate_ids_by_label = resolver.candidate_ids_by_label(duplicate_targets)
        self.assertEqual(
            candidate_ids_by_label["gpt-5.4 / high"],
            candidate_ids,
        )
        self.assertIsNone(
            resolver.candidate_id_from_label(
                "gpt-5.4 / high",
                candidate_ids_by_label,
            )
        )


class ScanPlannerBoundaryTest(unittest.TestCase):
    def test_planner_builds_authoritative_plan_without_monitor_service(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()
            selected_candidate = config.model_ingress.connections[0].model_candidates[0]
            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = candidate.id == selected_candidate.id
            config_store.save(config)
            resolver = ScanTargetResolver()
            projector = ComparisonGroupProjector(resolver)
            planner = ScanPlanner(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
                question_bank=QuestionBank(
                    Path(__file__).resolve().parent.parent / "questions"
                ),
                target_resolver=resolver,
                comparison_group_projector=projector,
            )

            plan = planner.plan(evaluation_profile_id="quick")

            self.assertEqual(plan.evaluation_profile_id, "quick")
            self.assertEqual(
                plan.effective_requested_candidate_ids,
                (selected_candidate.id,),
            )
            self.assertEqual(plan.enabled_targets[0].candidate_id, selected_candidate.id)
            self.assertIsNone(active_run_store.load())
            self.assertEqual(history_store.load_all(), [])

    def test_monitor_service_delegates_scan_planning_to_owned_planner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = MonitorService(
                config_store=ConfigStore(Path(temp_dir) / "config.json"),
                history_store=HistoryStore(Path(temp_dir) / "history.jsonl"),
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
            )
            sentinel = object()

            with patch.object(
                service.scan_planner,
                "plan",
                return_value=sentinel,
            ) as plan:
                result = service.plan_scan(
                    force_restart=True,
                    requested_candidate_ids=["candidate-a"],
                    selection_mode="custom",
                    custom_round_mode="append",
                    evaluation_profile_id="quick",
                    upgrade_from_run_id="run-source",
                )

            self.assertIs(result, sentinel)
            plan.assert_called_once_with(
                force_restart=True,
                requested_candidate_ids=["candidate-a"],
                selection_mode="custom",
                custom_round_mode="append",
                evaluation_profile_id="quick",
                upgrade_from_run_id="run-source",
            )
            self.assertIsInstance(service.scan_planner, ScanPlanner)


class ComparisonGroupProjectorTest(unittest.TestCase):
    def test_aggregates_append_metadata_without_redefining_group_semantics(self) -> None:
        first, second = ScanTargetResolver().enabled_targets(AppConfig.default())[:2]
        history = [
            _result(
                run_id="run-a",
                candidate_id=first.candidate_id,
                model=first.model,
                effort=first.effort,
                question_id="q1",
            ),
            _result(
                run_id="run-b",
                candidate_id=second.candidate_id,
                model=second.model,
                effort=second.effort,
                question_id="q1",
            ),
        ]
        metadata = {
            "run-a": {
                "run_id": "run-a",
                "question_pack_id": "pack",
                "question_pack_version": "v1",
                "started_at": "2026-07-28T10:00:00+08:00",
                "completed_at": "2026-07-28T10:00:04+08:00",
                "status": "completed",
                "question_count": 1,
                "evaluation_profile_id": "quick",
                "evaluation_profile_label": "快测",
                "evaluation_result_level": "directional",
                "evaluation_score_max": 20,
                "question_ids": ["q1"],
                "selection_mode": "regular",
                "comparison_group_mode": "regular",
                "requested_candidate_ids": [first.candidate_id],
                "regular_candidate_ids": [first.candidate_id],
                "is_complete_regular_round": True,
            },
            "run-b": {
                "run_id": "run-b",
                "question_pack_id": "pack",
                "question_pack_version": "v1",
                "started_at": "2026-07-28T10:01:00+08:00",
                "completed_at": "2026-07-28T10:01:03+08:00",
                "status": "completed",
                "question_count": 1,
                "evaluation_profile_id": "quick",
                "evaluation_profile_label": "快测",
                "evaluation_result_level": "directional",
                "evaluation_score_max": 20,
                "question_ids": ["q1"],
                "selection_mode": "custom",
                "comparison_group_mode": "custom_append",
                "comparison_parent_run_id": "run-a",
                "append_target_group_id": "group-ab",
                "requested_candidate_ids": [first.candidate_id, second.candidate_id],
                "regular_candidate_ids": [first.candidate_id],
                "appended_candidate_ids": [second.candidate_id],
            },
        }

        aggregated = ComparisonGroupProjector().aggregate_metadata(
            group_id="group-ab",
            history=history,
            run_metadata_by_id=metadata,
            member_run_ids=["run-a", "run-b"],
        )

        self.assertIsNotNone(aggregated)
        assert aggregated is not None
        self.assertEqual(aggregated["run_id"], "group-ab")
        self.assertEqual(aggregated["selection_mode"], "custom")
        self.assertEqual(aggregated["comparison_group_mode"], "custom_append")
        self.assertEqual(
            aggregated["requested_candidate_ids"],
            [first.candidate_id, second.candidate_id],
        )
        self.assertEqual(aggregated["appended_candidate_ids"], [second.candidate_id])
        self.assertEqual(aggregated["aggregate_wall_clock_seconds"], 7)
        self.assertTrue(aggregated["is_complete_regular_round"])

    def test_projects_members_candidates_and_latest_appendable_group(self) -> None:
        resolver = ScanTargetResolver()
        targets = resolver.enabled_targets(AppConfig.default())[:2]
        first, second = targets
        history = [
            _result(
                run_id="run-a",
                candidate_id=first.candidate_id,
                model=first.model,
                effort=first.effort,
                question_id="q1",
            ),
            _result(
                run_id="run-b",
                model=second.model,
                effort=second.effort,
                question_id="q1",
            ),
            _result(
                run_id="run-single",
                candidate_id=first.candidate_id,
                model=first.model,
                effort=first.effort,
                question_id="q1",
            ),
        ]
        metadata = {
            "run-a": {
                "comparison_group_id": "group-ab",
                "requested_candidate_ids": [first.candidate_id],
            },
            "run-b": {
                "comparison_group_id": "group-ab",
                "requested_candidate_ids": [],
            },
            "run-single": {
                "comparison_group_id": "group-single",
                "selection_mode": "single",
            },
        }
        projector = ComparisonGroupProjector(resolver)

        self.assertEqual(
            projector.member_run_ids(
                group_id="group-ab",
                history=history,
                run_metadata_by_id=metadata,
            ),
            ["run-a", "run-b"],
        )
        self.assertEqual(
            projector.candidate_ids(
                group_id="group-ab",
                history=history,
                run_metadata_by_id=metadata,
                enabled_targets=targets,
            ),
            [first.candidate_id, second.candidate_id],
        )
        self.assertEqual(
            projector.latest_appendable_group_id(history, metadata),
            "group-ab",
        )

    def test_projects_latest_scan_result_state_by_candidate_and_question(self) -> None:
        resolver = ScanTargetResolver()
        targets = resolver.enabled_targets(AppConfig.default())[:2]
        first, second = targets
        history = [
            _result(
                run_id="run-a",
                candidate_id=first.candidate_id,
                model=first.model,
                effort=first.effort,
                question_id="q1",
            ),
            _result(
                run_id="run-b",
                candidate_id=second.candidate_id,
                model=second.model,
                effort=second.effort,
                question_id="q1",
            ),
            _result(
                run_id="run-b",
                candidate_id=second.candidate_id,
                model=second.model,
                effort=second.effort,
                question_id="q2",
            ),
            _result(
                run_id="run-b",
                candidate_id=second.candidate_id,
                model=second.model,
                effort=second.effort,
                question_id="q3",
                phase="profile_upgrade",
            ),
        ]

        state = ComparisonGroupProjector(resolver).result_state(
            history=history,
            run_ids=["run-a", "run-b"],
            enabled_targets=targets,
            question_ids=["q1", "q2"],
        )

        self.assertEqual(state["completed_count"], 3)
        self.assertEqual(
            state["completed_by_candidate"],
            {first.candidate_id: 1, second.candidate_id: 2},
        )
        self.assertEqual(
            state["completed_steps"],
            {
                (first.candidate_id, "scan", "q1"),
                (second.candidate_id, "scan", "q1"),
                (second.candidate_id, "scan", "q2"),
            },
        )


if __name__ == "__main__":
    unittest.main()
