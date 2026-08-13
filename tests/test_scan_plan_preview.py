from __future__ import annotations

import hashlib
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

from scanner.active_run_store import ActiveRunStore
from scanner.config_store import ConfigStore
from scanner.history_store import HistoryStore
from scanner.models import ResolvedScanTarget, ScanResult
from scanner.scan_plan_preview import ScanPlanPreviewQuery
from scanner.service import MonitorService
from scanner.settings_projection import SettingsProjectionProjector
from scanner.snapshot_query import SnapshotProjector


def _service(root: Path) -> MonitorService:
    return MonitorService(
        config_store=ConfigStore(root / "config.json"),
        history_store=HistoryStore(root / "history.jsonl"),
        active_run_store=ActiveRunStore(root / "active_run.json"),
    )


def _preview_query(
    service: MonitorService,
    *,
    quick_candidate_ids_provider: Callable[[], list[str] | None] | None = None,
) -> ScanPlanPreviewQuery:
    return ScanPlanPreviewQuery(
        service=service,
        snapshot_projector=SnapshotProjector(
            config_reader=service.config_store.load,
            state_reader=service.monitor_state_projector.build_state,
            settings_projector=SettingsProjectionProjector(
                service.scan_target_resolver
            ),
        ),
        quick_candidate_ids_provider=quick_candidate_ids_provider,
    )


def _fingerprint(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _seed_comparison_group(
    service: MonitorService,
    *,
    target: ResolvedScanTarget,
    profile_id: str,
    run_id: str = "run-existing",
) -> None:
    question_pack = service.question_bank.load()
    profile = question_pack.evaluation_profile(profile_id)
    question = question_pack.questions_for_profile(profile.id)[0]
    service.history_store.append(
        ScanResult(
            run_id=run_id,
            candidate_id=target.candidate_id,
            model=target.model,
            effort=target.effort,
            phase="scan",
            question_id=question.id,
            question_title=question.title,
            grader_kind=question.grader.kind,
            attempt_index=1,
            started_at="2026-07-29T10:00:00+08:00",
            elapsed_seconds=1.0,
            source_mode="live",
            answer_ok=True,
            answer_preview="ok",
            input_tokens=10,
            output_tokens=5,
            reasoning_tokens=3,
            final_status="pass",
        )
    )
    service.history_store.save_run_metadata(
        {
            "run_id": run_id,
            "question_pack_id": question_pack.metadata.question_pack_id,
            "question_pack_version": question_pack.metadata.question_pack_version,
            "started_at": "2026-07-29T10:00:00+08:00",
            "completed_at": "2026-07-29T10:01:00+08:00",
            "candidate_count": 1,
            "question_count": len(profile.question_ids),
            "status": "completed",
            "evaluation_profile_id": profile.id,
            "evaluation_profile_label": profile.label,
            "evaluation_result_level": profile.result_level,
            "evaluation_score_max": profile.score_max,
            "question_ids": list(profile.question_ids),
            "selection_mode": "regular",
            "requested_candidate_ids": [target.candidate_id],
            "regular_candidate_ids": [target.candidate_id],
            "comparison_group_id": run_id,
            "comparison_group_mode": "regular",
            "is_complete_regular_round": True,
        }
    )


class ScanPlanPreviewQueryTest(unittest.TestCase):
    def test_custom_options_return_both_modes_without_sharing_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = _service(root)
            config = service.config_store.load()
            service.config_store.save(config)
            candidate_ids = [
                target.candidate_id
                for target in service.scan_target_resolver.available_targets(config)[:2]
            ]
            before = _fingerprint(root)

            options = _preview_query(service).preview_custom_options(
                requested_candidate_ids=candidate_ids,
                evaluation_profile_id="quick",
            )

            self.assertEqual(_fingerprint(root), before)

        self.assertEqual(set(options), {"schema_version", "new_round", "append"})
        self.assertEqual(options["schema_version"], 1)
        self.assertTrue(options["new_round"]["valid"])  # type: ignore[index]
        self.assertEqual(
            options["new_round"]["requested_custom_round_mode"],  # type: ignore[index]
            "new_round",
        )
        self.assertFalse(options["append"]["valid"])  # type: ignore[index]
        self.assertEqual(
            options["append"]["reason"],  # type: ignore[index]
            "append_no_current_round",
        )
        self.assertEqual(
            options["append"]["requested_custom_round_mode"],  # type: ignore[index]
            "append",
        )
        self.assertEqual(
            options["append"]["message"],  # type: ignore[index]
            "当前没有可补充的比较轮，请先完成至少一轮扫描",
        )

    def test_valid_new_round_projects_stable_authoritative_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = _service(root)
            config = service.config_store.load()
            service.config_store.save(config)
            candidate_ids = [
                target.candidate_id
                for target in service.scan_target_resolver.available_targets(config)[:2]
            ]

            preview = _preview_query(service).build_preview(
                requested_candidate_ids=candidate_ids,
                selection_mode="custom",
                custom_round_mode="new_round",
                evaluation_profile_id="quick",
            )

        self.assertEqual(
            set(preview),
            {
                "schema_version",
                "valid",
                "reason",
                "message",
                "requested_selection_mode",
                "requested_custom_round_mode",
                "execution_selection_mode",
                "execution_custom_round_mode",
                "profile",
                "requested_candidate_ids",
                "effective_candidate_ids",
                "execution_candidate_ids",
                "regular_candidate_ids",
                "appended_candidate_ids",
                "skipped_candidate_ids",
                "comparison_group",
                "total_evaluations",
                "completed_evaluations",
            },
        )
        self.assertEqual(preview["schema_version"], 1)
        self.assertTrue(preview["valid"])
        self.assertIsNone(preview["reason"])
        self.assertIsNone(preview["message"])
        self.assertEqual(preview["requested_selection_mode"], "custom")
        self.assertEqual(preview["requested_custom_round_mode"], "new_round")
        self.assertEqual(preview["execution_selection_mode"], "custom")
        self.assertEqual(preview["execution_custom_round_mode"], "new_round")
        self.assertEqual(
            set(preview["profile"]),  # type: ignore[arg-type]
            {"id", "label", "question_count"},
        )
        self.assertEqual(preview["profile"]["id"], "quick")  # type: ignore[index]
        self.assertEqual(preview["profile"]["label"], "快速对比")  # type: ignore[index]
        self.assertGreater(preview["profile"]["question_count"], 0)  # type: ignore[index,operator]
        self.assertEqual(preview["requested_candidate_ids"], candidate_ids)
        self.assertEqual(preview["effective_candidate_ids"], candidate_ids)
        self.assertEqual(preview["execution_candidate_ids"], candidate_ids)
        self.assertEqual(preview["appended_candidate_ids"], [])
        self.assertEqual(preview["skipped_candidate_ids"], [])
        self.assertEqual(
            set(preview["comparison_group"]),  # type: ignore[arg-type]
            {"id", "mode", "parent_run_id", "append_target_group_id"},
        )
        self.assertEqual(
            preview["comparison_group"]["mode"],  # type: ignore[index]
            "custom_new_round",
        )
        self.assertIsNone(preview["comparison_group"]["id"])  # type: ignore[index]
        self.assertEqual(
            preview["total_evaluations"],
            2 * preview["profile"]["question_count"],  # type: ignore[index,operator]
        )
        self.assertEqual(preview["completed_evaluations"], 0)

    def test_append_profile_mismatch_returns_stable_invalid_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = _service(root)
            config = service.config_store.load()
            service.config_store.save(config)
            existing, added = service.scan_target_resolver.available_targets(config)[:2]
            _seed_comparison_group(service, target=existing, profile_id="full")

            preview = _preview_query(service).build_preview(
                requested_candidate_ids=[added.candidate_id],
                selection_mode="custom",
                custom_round_mode="append",
                evaluation_profile_id="quick",
            )

        self.assertFalse(preview["valid"])
        self.assertEqual(preview["reason"], "append_profile_mismatch")
        self.assertEqual(
            preview["message"],
            "所选评测模式与当前比较轮不一致，请新开一轮",
        )
        self.assertIsNone(preview["execution_selection_mode"])
        self.assertEqual(preview["requested_candidate_ids"], [added.candidate_id])

    def test_append_without_new_candidate_returns_stable_invalid_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = _service(root)
            config = service.config_store.load()
            service.config_store.save(config)
            existing = service.scan_target_resolver.available_targets(config)[0]
            _seed_comparison_group(service, target=existing, profile_id="full")

            preview = _preview_query(service).build_preview(
                requested_candidate_ids=[existing.candidate_id],
                selection_mode="custom",
                custom_round_mode="append",
                evaluation_profile_id="full",
            )

        self.assertFalse(preview["valid"])
        self.assertEqual(preview["reason"], "append_no_new_candidates")
        self.assertEqual(
            preview["message"],
            "所选模型都已在当前轮跑过，请改为新开一轮",
        )

    def test_quick_custom_requires_exactly_two_candidates_in_domain_planner(self) -> None:
        from scanner.scan_planner import ScanPlanningError

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = _service(root)
            config = service.config_store.load()
            service.config_store.save(config)
            candidate_id = service.scan_target_resolver.available_targets(config)[0].candidate_id

            with self.assertRaises(ScanPlanningError) as raised:
                service.plan_scan(
                    requested_candidate_ids=[candidate_id],
                    selection_mode="custom",
                    custom_round_mode="new_round",
                    evaluation_profile_id="quick",
                )
            self.assertEqual(raised.exception.reason, "quick_candidate_count")
            self.assertEqual(str(raised.exception), "快速对比需要选择两个配置")
            preview = _preview_query(service).build_preview(
                requested_candidate_ids=[candidate_id],
                selection_mode="custom",
                custom_round_mode="new_round",
                evaluation_profile_id="quick",
            )

        self.assertFalse(preview["valid"])
        self.assertEqual(preview["reason"], "quick_candidate_count")
        self.assertEqual(preview["message"], "快速对比需要选择两个配置")
        self.assertEqual(preview["profile"]["id"], "quick")  # type: ignore[index]

    def test_regular_quick_preview_selects_the_backend_recommendation_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = _service(root)
            config = service.config_store.load()
            service.config_store.save(config)
            candidate_ids = [
                target.candidate_id
                for target in service.scan_target_resolver.enabled_targets(config)[:2]
            ]
            preview = _preview_query(
                service,
                quick_candidate_ids_provider=lambda: candidate_ids,
            ).build_preview(
                selection_mode="regular",
                evaluation_profile_id="quick",
            )

        self.assertTrue(preview["valid"])
        self.assertEqual(preview["requested_candidate_ids"], candidate_ids)
        self.assertEqual(preview["effective_candidate_ids"], candidate_ids)
        self.assertEqual(preview["execution_candidate_ids"], candidate_ids)

    def test_regular_quick_preview_uses_exactly_two_enabled_targets_without_a_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = _service(root)
            config = service.config_store.load()
            service.config_store.save(config)
            targets = service.scan_target_resolver.enabled_targets(config)[:2]
            candidate_ids = [target.candidate_id for target in targets]

            with patch.object(
                service.scan_target_resolver,
                "enabled_targets",
                return_value=targets,
            ):
                preview = _preview_query(
                    service,
                    quick_candidate_ids_provider=lambda: None,
                ).build_preview(
                    selection_mode="regular",
                    evaluation_profile_id="quick",
                )

        self.assertTrue(preview["valid"])
        self.assertEqual(preview["requested_candidate_ids"], candidate_ids)
        self.assertEqual(preview["effective_candidate_ids"], candidate_ids)
        self.assertEqual(preview["execution_candidate_ids"], candidate_ids)

    def test_regular_quick_preview_rejects_a_missing_backend_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = _service(Path(temp_dir))
            preview = _preview_query(
                service,
                quick_candidate_ids_provider=lambda: None,
            ).build_preview(
                selection_mode="regular",
                evaluation_profile_id="quick",
            )

        self.assertFalse(preview["valid"])
        self.assertEqual(
            preview["reason"],
            "quick_recommendation_pair_unavailable",
        )
        self.assertEqual(preview["requested_candidate_ids"], [])

    def test_incremental_full_preview_owns_reuse_validity_and_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = _service(root)
            service.config_store.save(service.config_store.load())
            before = _fingerprint(root)

            preview = _preview_query(service).build_preview(
                selection_mode="incremental_full",
            )

            self.assertEqual(_fingerprint(root), before)

        self.assertFalse(preview["valid"])
        self.assertEqual(preview["reason"], "incremental_no_reusable_evidence")
        self.assertEqual(
            preview["message"],
            "没有 24 小时内可复用的兼容快测结果，请全新扫描",
        )
        self.assertEqual(preview["profile"]["id"], "full")  # type: ignore[index]

    def test_preview_reason_is_independent_from_planning_error_message(self) -> None:
        from scanner.scan_planner import ScanPlanningError

        with tempfile.TemporaryDirectory() as temp_dir:
            service = _service(Path(temp_dir))
            error = ScanPlanningError(
                "append_no_new_candidates",
                "this display message can change independently",
            )
            with patch.object(service, "plan_scan", side_effect=error):
                preview = _preview_query(service).build_preview(
                    selection_mode="custom",
                    custom_round_mode="append",
                    evaluation_profile_id="full",
                )

        self.assertEqual(preview["reason"], "append_no_new_candidates")
        self.assertEqual(
            preview["message"],
            "this display message can change independently",
        )

    def test_preview_query_does_not_write_config_history_active_or_journal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = _service(root)
            config = service.config_store.load()
            service.config_store.save(config)
            candidate_ids = [
                target.candidate_id
                for target in service.scan_target_resolver.available_targets(config)[:2]
            ]
            before = _fingerprint(root)

            preview = _preview_query(service).build_preview(
                requested_candidate_ids=candidate_ids,
                selection_mode="custom",
                custom_round_mode="new_round",
                evaluation_profile_id="quick",
            )

            self.assertTrue(preview["valid"])
            self.assertEqual(_fingerprint(root), before)


if __name__ == "__main__":
    unittest.main()
