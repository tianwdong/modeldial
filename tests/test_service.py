from __future__ import annotations

import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scanner.config_store import ConfigStore
from scanner.costing import estimate_reference_cost
from scanner.codex_current_model import DetectedCodexModel, DetectedCodexSession
from scanner.model_sessions import DetectedModelSession
from scanner.active_run_store import ActiveRunStore
from scanner.history_store import HistoryStore
from scanner.models import (
    ConnectionConfig,
    ModelCandidateConfig,
    RunMetadata,
    ScanPlan,
    ScanResult,
)
from scanner.route_identity import build_route_fingerprint
from scanner.repair_planner import is_timeout_result
from scanner.scoring import EQUAL_SCORING_MODE
from scanner.service import MonitorService
from tests.question_pack_fixtures import (
    DEFAULT_EVALUATION_COUNT,
    DEFAULT_QUESTION_COUNT,
    DEFAULT_QUESTION_IDS,
    DEFAULT_QUESTION_PACK_VERSION,
    expected_calls_for,
    expected_question_attempts,
    planned_attempts,
)


def _set_enabled_candidates(config, enabled_labels: set[str]) -> None:  # type: ignore[no-untyped-def]
    for connection in config.model_ingress.connections:
        for candidate in connection.model_candidates:
            label = f"{candidate.model_id} / {candidate.scan_profile}"
            candidate.enabled = label in enabled_labels


def _seed_repair_run(
    temp_dir: str,
    runner,  # type: ignore[no-untyped-def]
    *,
    q2_error_message: str | None = "codex exec timed out after 300s",
    q2_reasoning_tokens: int | None = None,
) -> tuple[MonitorService, HistoryStore, str, str]:
    config_store = ConfigStore(Path(temp_dir) / "config.json")
    config = config_store.load()
    _set_enabled_candidates(config, {"gpt-5.4 / high"})
    config_store.save(config)
    history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
    service = MonitorService(
        config_store=config_store,
        history_store=history_store,
        active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
        runner=runner,
    )
    target = service.scan_target_resolver.enabled_targets(config)[0]
    run_id = "run-repair"
    for index, question in enumerate(service.question_bank.load().enabled_questions, start=1):
        is_q2 = question.id == "02_code_counterexample_maxgap"
        history_store.append(
            ScanResult(
                run_id=run_id,
                candidate_id=target.candidate_id,
                model=target.model,
                effort=target.effort,
                phase="scan",
                question_id=question.id,
                question_title=question.title,
                grader_kind=question.grader.kind,
                attempt_index=index,
                started_at="2026-07-14T10:00:00+08:00",
                elapsed_seconds=300.0 if is_q2 else 1.0,
                source_mode="live",
                answer_ok=not is_q2,
                answer_preview="bad" if is_q2 else "ok",
                input_tokens=100,
                output_tokens=20,
                reasoning_tokens=q2_reasoning_tokens if is_q2 else 430,
                error_message=q2_error_message if is_q2 else None,
                final_status="warn" if is_q2 else "pass",
            )
        )
    pack = service.question_bank.metadata()
    history_store.save_run_metadata(
        {
            "run_id": run_id,
            "question_pack_id": pack.question_pack_id,
            "question_pack_version": pack.question_pack_version,
            "started_at": "2026-07-14T10:00:00+08:00",
            "completed_at": "2026-07-14T10:05:00+08:00",
            "candidate_count": 1,
            "question_count": DEFAULT_QUESTION_COUNT,
            "status": "degraded" if q2_error_message else "completed",
            "selection_mode": "regular",
            "requested_candidate_ids": [target.candidate_id],
            "regular_candidate_ids": [target.candidate_id],
            "is_complete_regular_round": False,
        }
    )
    return service, history_store, run_id, target.candidate_id


class MonitorServiceTest(unittest.TestCase):
    def test_plan_scan_builds_public_start_contract_without_full_snapshot(self) -> None:
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
            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
            )

            with patch.object(
                service,
                "build_state",
                side_effect=AssertionError("scan planning must not build a full snapshot"),
            ):
                plan = service.plan_scan(evaluation_profile_id="quick")

            self.assertIsInstance(plan, ScanPlan)
            self.assertEqual(plan.selection_mode, "regular")
            self.assertEqual(plan.custom_round_mode, "new_round")
            self.assertEqual(plan.evaluation_profile_id, "quick")
            self.assertIsNone(plan.requested_candidate_ids)
            self.assertEqual(plan.question_count, DEFAULT_QUESTION_COUNT)
            self.assertEqual(plan.total_targets, DEFAULT_QUESTION_COUNT)
            self.assertEqual(plan.completed_targets, 0)
            self.assertIsNone(active_run_store.load())
            self.assertEqual(history_store.load_all(), [])

    def test_run_enabled_targets_consumes_prepared_scan_plan_without_replanning(self) -> None:
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

            def successful_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                return ScanResult(
                    model=target.model,
                    effort=target.effort,
                    started_at=datetime.now(timezone.utc).isoformat(),
                    elapsed_seconds=1.0,
                    source_mode="mock" if use_mock_results else "live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
                runner=successful_runner,
            )
            plan = service.plan_scan(evaluation_profile_id="quick")

            with (
                patch.object(
                    service,
                    "plan_scan",
                    side_effect=AssertionError("prepared scan must not be replanned"),
                ),
                patch.object(
                    service,
                    "load_config",
                    side_effect=AssertionError("prepared scan must not reload config"),
                ),
                patch.object(
                    service.execution_engine,
                    "execute",
                    wraps=service.execution_engine.execute,
                ) as execute,
                patch.object(
                    service.scan_execution_application,
                    "execute",
                    wraps=service.scan_execution_application.execute,
                ) as execute_application,
            ):
                results = service.run_enabled_targets(scan_plan=plan)

            execute_application.assert_called_once_with(
                scan_plan=plan,
                retain_finalizing_state=False,
                progress_callback=None,
            )
            execute.assert_called_once()
            self.assertTrue(execute.call_args.kwargs["stop_on_failure"])
            self.assertTrue(callable(execute.call_args.kwargs["skip_job"]))
            self.assertEqual(len(results), plan.question_count)
            self.assertEqual({result.run_id for result in results}, {plan.run_id})
            self.assertIsNone(active_run_store.load())

    def test_incremental_full_reuses_fresh_compatible_quick_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            config = config_store.load()
            candidates = config.model_ingress.connections[0].model_candidates[:3]
            enabled_ids = {candidate.id for candidate in candidates}
            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = candidate.id in enabled_ids
            config_store.save(config)
            calls: list[tuple[str, str, str]] = []

            def successful_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                calls.append((kwargs["run_id"], target.candidate_id, question.id))
                route_fingerprint = build_route_fingerprint(
                    source_id=target.source_id,
                    connection_id=target.connection_id,
                    connection_mode=target.connection_mode,
                    api_format=target.api_format,
                    provider_preset=target.provider_preset,
                    base_url=target.base_url,
                    model_id=target.model_id,
                    scan_profile=target.scan_profile,
                )
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at=datetime.now(timezone.utc).isoformat(),
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    reference_cost_usd=9.0,
                    cost_status="estimated",
                    final_status="pass",
                    execution_trace={"route_fingerprint": route_fingerprint},
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                runner=successful_runner,
            )
            quick_results = service.run_enabled_targets(
                force_restart=True,
                requested_candidate_ids=[candidates[0].id, candidates[1].id],
                selection_mode="regular",
                evaluation_profile_id="quick",
            )
            quick_run_id = quick_results[0].run_id

            incremental_results = service.run_enabled_targets(
                selection_mode="incremental_full",
                evaluation_profile_id="full",
            )
            incremental_run_id = incremental_results[0].run_id
            incremental_metadata = history_store.load_run_metadata(incremental_run_id)
            dashboard_metadata = service.build_state()["dashboard"]["run_metadata"]

            self.assertEqual(len(incremental_results), DEFAULT_QUESTION_COUNT)
            self.assertEqual(
                {candidate_id for run_id, candidate_id, _ in calls if run_id == incremental_run_id},
                {candidates[2].id},
            )
            self.assertEqual(incremental_metadata["comparison_group_id"], quick_run_id)
            self.assertEqual(incremental_metadata["comparison_group_mode"], "incremental_full")
            self.assertEqual(
                set(incremental_metadata["skipped_candidate_ids"]),
                {candidates[0].id, candidates[1].id},
            )
            self.assertEqual(incremental_metadata["appended_candidate_ids"], [candidates[2].id])
            self.assertTrue(incremental_metadata["is_complete_regular_round"])
            self.assertEqual(set(dashboard_metadata["requested_candidate_ids"]), enabled_ids)
            dashboard_rows = {
                row["candidate_id"]: row
                for row in service.build_state()["dashboard"]["leaderboard"]
            }
            for result in quick_results:
                expected = estimate_reference_cost(
                    result.model,
                    input_tokens=result.input_tokens,
                    cached_input_tokens=result.cached_input_tokens,
                    cache_write_input_tokens=result.cache_write_input_tokens,
                    output_tokens=result.output_tokens,
                    reasoning_output_tokens=result.reasoning_tokens,
                ).usd
                self.assertIsNotNone(expected)
                self.assertAlmostEqual(
                    dashboard_rows[result.candidate_id]["estimated_cost_usd"],
                    expected * DEFAULT_QUESTION_COUNT,
                )

    def test_incremental_full_rejects_results_older_than_24_hours(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            config = config_store.load()
            candidates = config.model_ingress.connections[0].model_candidates[:2]
            enabled_ids = {candidate.id for candidate in candidates}
            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = candidate.id in enabled_ids
            config_store.save(config)

            def successful_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                route_fingerprint = build_route_fingerprint(
                    source_id=target.source_id,
                    connection_id=target.connection_id,
                    connection_mode=target.connection_mode,
                    api_format=target.api_format,
                    provider_preset=target.provider_preset,
                    base_url=target.base_url,
                    model_id=target.model_id,
                    scan_profile=target.scan_profile,
                )
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at=datetime.now(timezone.utc).isoformat(),
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                    execution_trace={"route_fingerprint": route_fingerprint},
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                runner=successful_runner,
            )
            quick_results = service.run_enabled_targets(
                force_restart=True,
                requested_candidate_ids=[candidates[0].id],
                selection_mode="regular",
                evaluation_profile_id="quick",
            )
            quick_metadata = history_store.load_run_metadata(quick_results[0].run_id)
            quick_metadata["completed_at"] = (
                datetime.now(timezone.utc) - timedelta(hours=25)
            ).isoformat()
            history_store.save_run_metadata(quick_metadata)

            with self.assertRaisesRegex(ValueError, "24 小时内"):
                service.run_enabled_targets(
                    selection_mode="incremental_full",
                    evaluation_profile_id="full",
                )

    def test_incremental_full_rejects_results_after_route_identity_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            config = config_store.load()
            candidate = config.model_ingress.connections[0].model_candidates[0]
            for connection in config.model_ingress.connections:
                for item in connection.model_candidates:
                    item.enabled = item.id == candidate.id
            config_store.save(config)

            def successful_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                route_fingerprint = build_route_fingerprint(
                    source_id=target.source_id,
                    connection_id=target.connection_id,
                    connection_mode=target.connection_mode,
                    api_format=target.api_format,
                    provider_preset=target.provider_preset,
                    base_url=target.base_url,
                    model_id=target.model_id,
                    scan_profile=target.scan_profile,
                )
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at=datetime.now(timezone.utc).isoformat(),
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                    execution_trace={"route_fingerprint": route_fingerprint},
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                runner=successful_runner,
            )
            service.run_enabled_targets(
                force_restart=True,
                requested_candidate_ids=[candidate.id],
                selection_mode="regular",
                evaluation_profile_id="quick",
            )

            changed = config_store.load()
            changed.model_ingress.connections[0].provider_preset = "custom"
            config_store.save(changed)

            with self.assertRaisesRegex(ValueError, "24 小时内"):
                service.run_enabled_targets(
                    selection_mode="incremental_full",
                    evaluation_profile_id="full",
                )

    def test_quick_evaluation_profile_runs_all_five_questions_and_freezes_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            config = config_store.load()
            _set_enabled_candidates(config, {"gpt-5.4 / medium"})
            config_store.save(config)
            calls: list[str] = []

            def successful_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                calls.append(question.id)
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-23T10:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                    scorer_diagnostics={
                        "semantic_score": 16,
                        "semantic_total": 20,
                    },
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                runner=successful_runner,
            )

            results = service.run_enabled_targets(
                force_restart=True,
                evaluation_profile_id="quick",
            )
            metadata = history_store.load_run_metadata(results[0].run_id)
            dashboard_metadata = service.build_state()["dashboard"]["run_metadata"]

            self.assertEqual(calls, DEFAULT_QUESTION_IDS)
            self.assertEqual(len(results), DEFAULT_QUESTION_COUNT)
            self.assertEqual(metadata["evaluation_profile_id"], "quick")
            self.assertEqual(metadata["evaluation_profile_label"], "快速对比")
            self.assertEqual(metadata["evaluation_result_level"], "complete")
            self.assertEqual(metadata["question_ids"], calls)
            self.assertEqual(metadata["question_count"], DEFAULT_QUESTION_COUNT)
            self.assertIsNone(metadata["upgrade_target_profile_id"])
            self.assertTrue(metadata["is_complete_regular_round"])
            self.assertEqual(dashboard_metadata["question_ids"], calls)

    def test_missing_evaluation_profile_argument_preserves_full_scan_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            config = config_store.load()
            _set_enabled_candidates(config, {"gpt-5.4 / medium"})
            config_store.save(config)
            calls: list[str] = []

            def successful_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                calls.append(question.id)
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-23T10:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=HistoryStore(Path(temp_dir) / "history.jsonl"),
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                runner=successful_runner,
            )

            service.run_enabled_targets(force_restart=True)

            self.assertEqual(calls, DEFAULT_QUESTION_IDS)

    def test_quick_and_full_profiles_both_run_complete_five_question_rounds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            config = config_store.load()
            _set_enabled_candidates(
                config,
                {"gpt-5.4 / medium", "gpt-5.4 / high"},
            )
            config_store.save(config)
            calls: list[tuple[str, str, str]] = []

            def successful_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                calls.append((kwargs["run_id"], target.candidate_id, question.id))
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-23T10:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                runner=successful_runner,
            )

            quick_results = service.run_enabled_targets(
                force_restart=True,
                evaluation_profile_id="quick",
            )
            quick_run_id = quick_results[0].run_id
            full_results = service.run_enabled_targets(
                force_restart=True,
                evaluation_profile_id="full",
            )
            full_run_id = full_results[0].run_id
            full_metadata = history_store.load_run_metadata(full_run_id)
            dashboard_metadata = service.build_state()["dashboard"]["run_metadata"]

            self.assertEqual(len(quick_results), 2 * DEFAULT_QUESTION_COUNT)
            self.assertEqual(len(full_results), 2 * DEFAULT_QUESTION_COUNT)
            self.assertNotEqual(full_run_id, quick_run_id)
            quick_question_ids = [
                question_id for run_id, _, question_id in calls if run_id == quick_run_id
            ]
            full_question_ids = [
                question_id for run_id, _, question_id in calls if run_id == full_run_id
            ]
            for question_id in DEFAULT_QUESTION_IDS:
                self.assertEqual(quick_question_ids.count(question_id), 2)
                self.assertEqual(full_question_ids.count(question_id), 2)
            self.assertEqual(full_metadata["evaluation_profile_id"], "full")
            self.assertEqual(full_metadata["evaluation_result_level"], "complete")
            self.assertIsNone(full_metadata["upgrade_from_run_id"])
            self.assertEqual(full_metadata["comparison_group_id"], full_run_id)
            self.assertEqual(full_metadata["question_count"], DEFAULT_QUESTION_COUNT)
            self.assertEqual(full_metadata["question_ids"], DEFAULT_QUESTION_IDS)
            self.assertTrue(full_metadata["is_complete_regular_round"])
            self.assertEqual(dashboard_metadata["evaluation_profile_id"], "full")
            self.assertEqual(dashboard_metadata["question_ids"], DEFAULT_QUESTION_IDS)
            self.assertNotEqual(
                dashboard_metadata.get("upgrade_from_run_id"),
                dashboard_metadata["run_id"],
            )

    def test_complete_round_can_append_a_new_candidate_without_repeating_existing_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            config = config_store.load()
            candidates = config.model_ingress.connections[0].model_candidates[:2]
            original_candidate = candidates[0]
            added_candidate = candidates[1]
            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = candidate.id == original_candidate.id
            config_store.save(config)
            calls: list[tuple[str, str, str]] = []

            def successful_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                calls.append((kwargs["run_id"], target.candidate_id, question.id))
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-23T10:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                runner=successful_runner,
            )
            quick_results = service.run_enabled_targets(
                force_restart=True,
                evaluation_profile_id="quick",
            )
            quick_run_id = quick_results[0].run_id

            config = config_store.load()
            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = candidate.id in {
                        original_candidate.id,
                        added_candidate.id,
                    }
            config_store.save(config)
            appended_results = service.run_enabled_targets(
                requested_candidate_ids=[added_candidate.id],
                selection_mode="custom",
                custom_round_mode="append",
                evaluation_profile_id="quick",
            )
            appended_run_id = appended_results[0].run_id
            appended_calls = [
                (candidate_id, question_id)
                for run_id, candidate_id, question_id in calls
                if run_id == appended_run_id
            ]
            appended_metadata = history_store.load_run_metadata(appended_run_id)
            dashboard_metadata = service.build_state()["dashboard"]["run_metadata"]

            self.assertEqual(len(appended_results), DEFAULT_QUESTION_COUNT)
            self.assertEqual(
                {
                    question_id
                    for candidate_id, question_id in appended_calls
                    if candidate_id == added_candidate.id
                },
                set(DEFAULT_QUESTION_IDS),
            )
            self.assertFalse(any(candidate_id == original_candidate.id for candidate_id, _ in appended_calls))
            self.assertEqual(appended_metadata["evaluation_profile_id"], "quick")
            self.assertEqual(appended_metadata["append_target_group_id"], quick_run_id)
            self.assertEqual(
                set(dashboard_metadata["requested_candidate_ids"]),
                {original_candidate.id, added_candidate.id},
            )

    def test_append_rejects_a_different_evaluation_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            config = config_store.load()
            candidates = config.model_ingress.connections[0].model_candidates[:2]
            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = candidate.id == candidates[0].id
            config_store.save(config)

            def successful_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-23T10:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                runner=successful_runner,
            )
            service.run_enabled_targets(
                force_restart=True,
                evaluation_profile_id="quick",
            )
            config = config_store.load()
            config.model_ingress.connections[0].model_candidates[1].enabled = True
            config_store.save(config)

            with self.assertRaisesRegex(ValueError, "评测模式与当前比较轮不一致"):
                service.run_enabled_targets(
                    requested_candidate_ids=[candidates[1].id],
                    selection_mode="single",
                    evaluation_profile_id="full",
                )

    def test_backend_root_override_locates_external_question_pack_for_frozen_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "os.environ",
            {"MODELDIAL_BACKEND_ROOT": temp_dir},
            clear=False,
        ):
            service = MonitorService(
                config_store=ConfigStore(Path(temp_dir) / "config.json"),
                history_store=HistoryStore(Path(temp_dir) / "history.jsonl"),
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
            )

        self.assertEqual(service.question_bank.root, Path(temp_dir) / "questions")

    def test_api_suffix_alias_keeps_request_identity_and_adds_display_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            config = config_store.load()
            connection_id = "gemini-display-test"
            config.model_ingress.connections.append(
                ConnectionConfig(
                    id=connection_id,
                    source_id="custom_endpoint",
                    name="Gemini",
                    enabled=True,
                    api_format="openai_chat_completions",
                    model_candidates=[
                        ModelCandidateConfig(
                            id=f"{connection_id}:gemini-3.6-flash-low:default",
                            connection_id=connection_id,
                            model_id="gemini-3.6-flash-low",
                            display_name="gemini-3.6-flash-low",
                            scan_profile="default",
                        ),
                        ModelCandidateConfig(
                            id=f"{connection_id}:gemini-3.6-flash-high:default",
                            connection_id=connection_id,
                            model_id="gemini-3.6-flash-high",
                            display_name="gemini-3.6-flash-high",
                            scan_profile="default",
                        ),
                    ],
                )
            )
            config_store.save(config)
            service = MonitorService(
                config_store=config_store,
                history_store=HistoryStore(Path(temp_dir) / "history.jsonl"),
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
            )

            target = next(
                item
                for item in service.scan_target_resolver.configured_targets(config)
                if item.model_id == "gemini-3.6-flash-high"
            )

            self.assertEqual(target.model, "gemini-3.6-flash-high")
            self.assertEqual(target.effort, "default")
            self.assertEqual(target.display_model, "gemini-3.6-flash")
            self.assertEqual(target.display_effort, "high")
            self.assertEqual(target.display_label, "gemini-3.6-flash / high")

    def test_snapshot_includes_external_sessions_in_automatic_baseline_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = MonitorService(
                config_store=ConfigStore(Path(temp_dir) / "config.json"),
                history_store=HistoryStore(Path(temp_dir) / "history.jsonl"),
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                current_model_detector=lambda: DetectedCodexModel(
                    model="gpt-5.5",
                    effort="high",
                    detected_at="2026-07-22T08:00:00Z",
                    status="active_single",
                    active_session_count=1,
                    distinct_active_models=(("gpt-5.5", "high"),),
                    active_sessions=(
                        DetectedCodexSession(
                            id="codex-session",
                            workspace_name="codex-project",
                            model="gpt-5.5",
                            effort="high",
                            thread_name="调整 Hover",
                        ),
                    ),
                    display_sessions=(
                        DetectedCodexSession(
                            id="codex-session",
                            workspace_name="codex-project",
                            model="gpt-5.5",
                            effort="high",
                            thread_name="调整 Hover",
                        ),
                        DetectedCodexSession(
                            id="modeldial-scan",
                            workspace_name="Backend",
                            model="gpt-5.4",
                            effort="xhigh",
                            is_modeldial_scan=True,
                        ),
                    ),
                ),
                active_session_detector=lambda: (
                    DetectedModelSession(
                        id="claude-session",
                        source="claude",
                        workspace_name="claude-project",
                        model="claude-sonnet-4-5",
                        effort="high",
                    ),
                ),
            )

            with patch("scanner.monitor_state_projection.build_dashboard_summary", return_value={}):
                state = service.build_state()

            recommendation = state["config"]["recommendation"]
            self.assertEqual(recommendation["detected_active_session_count"], 2)
            self.assertEqual(
                recommendation["active_configuration_sessions"][0]["mapping_status"],
                "matched",
            )
            self.assertEqual(
                recommendation["active_configuration_sessions"][0]["candidate_id"],
                "codex-local-default:gpt-5.5:high",
            )
            self.assertEqual(
                recommendation["active_configuration_sessions"][1]["mapping_status"],
                "unmapped",
            )
            self.assertEqual(
                recommendation["detected_active_sessions"][0]["id"],
                "codex-session",
            )
            self.assertEqual(
                recommendation["detected_active_sessions"][1]["id"],
                "claude-session",
            )
            self.assertEqual(
                recommendation["active_model_sessions"],
                [
                    {
                        "id": "codex-session",
                        "source": "codex",
                        "workspace_name": "codex-project",
                        "model": "gpt-5.5",
                        "effort": "high",
                        "thread_name": "调整 Hover",
                        "is_evaluation_session": False,
                    },
                    {
                        "id": "modeldial-scan",
                        "source": "codex",
                        "workspace_name": "Backend",
                        "model": "gpt-5.4",
                        "effort": "xhigh",
                        "thread_name": None,
                        "is_evaluation_session": True,
                    },
                    {
                        "id": "claude-session",
                        "source": "claude",
                        "workspace_name": "claude-project",
                        "model": "claude-sonnet-4-5",
                        "effort": "high",
                        "thread_name": None,
                        "is_evaluation_session": False,
                    },
                ],
            )
            self.assertIsNone(recommendation["effective_current_candidate_id"])
            self.assertEqual(recommendation["current_model_source"], "terminal_session")
            self.assertEqual(recommendation["current_model_detection_status"], "active_mixed")

    def test_working_scans_keep_last_complete_dashboard_committed(self) -> None:
        for selection_mode in ("regular", "incremental_full"):
            with self.subTest(selection_mode=selection_mode), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                config_store = ConfigStore(root / "config.json")
                history_store = HistoryStore(root / "history.jsonl")
                active_run_store = ActiveRunStore(root / "active_run.json")
                config = config_store.load()
                candidates = config.model_ingress.connections[0].model_candidates[:3]
                enabled_ids = {candidate.id for candidate in candidates}
                for connection in config.model_ingress.connections:
                    for candidate in connection.model_candidates:
                        candidate.enabled = candidate.id in enabled_ids
                config_store.save(config)
                service = MonitorService(
                    config_store=config_store,
                    history_store=history_store,
                    active_run_store=active_run_store,
                )
                targets = {
                    target.candidate_id: target
                    for target in service.scan_target_resolver.enabled_targets(config)
                }
                stable_ids = [candidate.id for candidate in candidates[:2]]
                stable_run_id = "run-stable"
                question_pack = service.question_bank.load()
                for candidate_id in stable_ids:
                    target = targets[candidate_id]
                    for attempt_index, question in enumerate(
                        question_pack.enabled_questions,
                        start=1,
                    ):
                        history_store.append(
                            ScanResult(
                                run_id=stable_run_id,
                                candidate_id=candidate_id,
                                model=target.model,
                                effort=target.effort,
                                phase="scan",
                                question_id=question.id,
                                question_title=question.title,
                                grader_kind=question.grader.kind,
                                attempt_index=attempt_index,
                                started_at="2026-07-27T07:30:00+08:00",
                                elapsed_seconds=10.0,
                                source_mode="live",
                                answer_ok=True,
                                answer_preview="ok",
                                input_tokens=100,
                                output_tokens=20,
                                reasoning_tokens=430,
                                scorer_diagnostics={
                                    "semantic_passed": 20,
                                    "semantic_total": 20,
                                },
                                final_status="pass",
                            )
                        )
                history_store.save_run_metadata(
                    {
                        "run_id": stable_run_id,
                        "question_pack_id": question_pack.metadata.question_pack_id,
                        "question_pack_version": question_pack.metadata.question_pack_version,
                        "started_at": "2026-07-27T07:30:00+08:00",
                        "completed_at": "2026-07-27T07:45:00+08:00",
                        "candidate_count": len(stable_ids),
                        "question_count": len(question_pack.enabled_questions),
                        "status": "completed",
                        "evaluation_profile_id": "full",
                        "evaluation_result_level": "complete",
                        "evaluation_score_max": 100,
                        "question_ids": [
                            question.id for question in question_pack.enabled_questions
                        ],
                        "selection_mode": "regular",
                        "requested_candidate_ids": stable_ids,
                        "regular_candidate_ids": stable_ids,
                        "comparison_group_id": stable_run_id,
                        "comparison_group_mode": "regular",
                        "is_complete_regular_round": True,
                        "scoring_mode": EQUAL_SCORING_MODE,
                    }
                )
                requested_ids = (
                    [candidate.id for candidate in candidates]
                    if selection_mode == "incremental_full"
                    else stable_ids
                )
                working_run_id = "run-working"
                active_run_store.save(
                    {
                        "run_id": working_run_id,
                        "run_metadata": {
                            "run_id": working_run_id,
                            "question_pack_id": question_pack.metadata.question_pack_id,
                            "question_pack_version": question_pack.metadata.question_pack_version,
                            "started_at": "2026-07-27T08:30:00+08:00",
                            "completed_at": None,
                            "candidate_count": len(requested_ids),
                            "question_count": len(question_pack.enabled_questions),
                            "status": "running",
                            "evaluation_profile_id": "full",
                            "evaluation_result_level": "complete",
                            "evaluation_score_max": 100,
                            "question_ids": [
                                question.id for question in question_pack.enabled_questions
                            ],
                            "selection_mode": selection_mode,
                            "requested_candidate_ids": requested_ids,
                            "regular_candidate_ids": requested_ids,
                            "comparison_group_id": (
                                stable_run_id
                                if selection_mode == "incremental_full"
                                else working_run_id
                            ),
                            "comparison_group_mode": selection_mode,
                            "comparison_parent_run_id": (
                                stable_run_id
                                if selection_mode == "incremental_full"
                                else None
                            ),
                            "append_target_group_id": (
                                stable_run_id
                                if selection_mode == "incremental_full"
                                else None
                            ),
                            "appended_candidate_ids": (
                                requested_ids[len(stable_ids):]
                                if selection_mode == "incremental_full"
                                else []
                            ),
                            "skipped_candidate_ids": (
                                stable_ids if selection_mode == "incremental_full" else []
                            ),
                            "is_complete_regular_round": False,
                            "scoring_mode": EQUAL_SCORING_MODE,
                        },
                        "planned_attempts_by_candidate": {
                            candidate_id: len(question_pack.enabled_questions)
                            for candidate_id in requested_ids
                        },
                        "entries": [],
                    }
                )

                state = service.build_state()

                self.assertEqual(state["dashboard"]["run_metadata"]["status"], "running")
                stable_dashboard = state["stable_dashboard"]
                self.assertEqual(
                    stable_dashboard["run_metadata"]["status"],
                    "completed",
                )
                self.assertEqual(
                    {
                        row["candidate_id"]
                        for row in stable_dashboard["leaderboard"]
                        if row["question_completed"] == len(question_pack.enabled_questions)
                    },
                    set(stable_ids),
                )
                stable_evidence_dashboard = state["stable_evidence_dashboard"]
                self.assertEqual(
                    stable_evidence_dashboard["current_run_id"],
                    stable_run_id,
                )
                self.assertEqual(
                    stable_evidence_dashboard["run_metadata"]["status"],
                    "completed",
                )

    def test_snapshot_uses_unique_claude_terminal_model_as_automatic_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            config = config_store.load()
            for source in config.model_ingress.sources:
                if source.id == "claude_local":
                    source.enabled = True
            for connection in config.model_ingress.connections:
                if connection.source_id != "claude_local":
                    continue
                connection.enabled = True
                connection.local_login_verified = True
                for candidate in connection.model_candidates:
                    candidate.enabled = candidate.scan_profile == "high"
            config_store.save(config)
            service = MonitorService(
                config_store=config_store,
                history_store=HistoryStore(Path(temp_dir) / "history.jsonl"),
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                current_model_detector=lambda: None,
                active_session_detector=lambda: (
                    DetectedModelSession(
                        id="claude-session",
                        source="claude",
                        workspace_name="claude-project",
                        model="claude-sonnet-4-5",
                        effort="high",
                    ),
                ),
            )

            with patch("scanner.monitor_state_projection.build_dashboard_summary", return_value={}):
                state = service.build_state()

            recommendation = state["config"]["recommendation"]
            self.assertEqual(
                recommendation["effective_current_candidate_id"],
                "claude-local-default:sonnet:high",
            )
            self.assertEqual(recommendation["current_model_source"], "terminal_session")
            self.assertEqual(recommendation["current_model_detection_status"], "active_single")
            self.assertEqual(recommendation["detected_current_model"], "claude-sonnet-4-5")
            self.assertEqual(recommendation["detected_current_effort"], "high")
            self.assertEqual(recommendation["detected_active_session_count"], 1)

    def test_snapshot_does_not_choose_baseline_for_mixed_terminal_models(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            config = config_store.load()
            for source in config.model_ingress.sources:
                if source.id == "claude_local":
                    source.enabled = True
            for connection in config.model_ingress.connections:
                if connection.source_id == "claude_local":
                    connection.enabled = True
                    connection.local_login_verified = True
                    for candidate in connection.model_candidates:
                        candidate.enabled = candidate.scan_profile == "high"
            config_store.save(config)
            service = MonitorService(
                config_store=config_store,
                history_store=HistoryStore(Path(temp_dir) / "history.jsonl"),
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                current_model_detector=lambda: DetectedCodexModel(
                    model="gpt-5.5",
                    effort="high",
                    detected_at="2026-07-23T08:00:00Z",
                    status="active_single",
                    active_session_count=1,
                    distinct_active_models=(("gpt-5.5", "high"),),
                    active_sessions=(
                        DetectedCodexSession(
                            id="codex-session",
                            workspace_name="codex-project",
                            model="gpt-5.5",
                            effort="high",
                        ),
                    ),
                ),
                active_session_detector=lambda: (
                    DetectedModelSession(
                        id="claude-session",
                        source="claude",
                        workspace_name="claude-project",
                        model="claude-sonnet-4-5",
                        effort="high",
                    ),
                ),
            )

            with patch("scanner.monitor_state_projection.build_dashboard_summary", return_value={}):
                state = service.build_state()

            recommendation = state["config"]["recommendation"]
            self.assertIsNone(recommendation["effective_current_candidate_id"])
            self.assertEqual(recommendation["current_model_source"], "terminal_session")
            self.assertEqual(recommendation["current_model_detection_status"], "active_mixed")
            self.assertEqual(recommendation["detected_active_session_count"], 2)
            self.assertEqual(len(recommendation["detected_active_models"]), 2)

    def test_snapshot_keeps_unmapped_external_terminal_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = MonitorService(
                config_store=ConfigStore(Path(temp_dir) / "config.json"),
                history_store=HistoryStore(Path(temp_dir) / "history.jsonl"),
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                current_model_detector=lambda: None,
                active_session_detector=lambda: (
                    DetectedModelSession(
                        id="opencode-session",
                        source="opencode",
                        workspace_name="opencode-project",
                        model="vendor-model",
                        effort="high",
                    ),
                ),
            )

            with patch("scanner.monitor_state_projection.build_dashboard_summary", return_value={}):
                state = service.build_state()

            recommendation = state["config"]["recommendation"]
            self.assertIsNone(recommendation["effective_current_candidate_id"])
            self.assertEqual(recommendation["current_model_source"], "terminal_session")
            self.assertEqual(recommendation["current_model_detection_status"], "unmapped")
            self.assertEqual(recommendation["detected_current_model"], "vendor-model")
            self.assertEqual(recommendation["detected_current_effort"], "high")
            self.assertEqual(recommendation["detected_active_session_count"], 1)

    def test_snapshot_prefers_detected_codex_model_over_manual_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            config = config_store.load()
            config.recommendation.current_default_candidate_id = (
                "codex-local-default:gpt-5.5:high"
            )
            config_store.save(config)
            service = MonitorService(
                config_store=config_store,
                history_store=HistoryStore(Path(temp_dir) / "history.jsonl"),
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                current_model_detector=lambda: DetectedCodexModel(
                    model="gpt-5.4",
                    effort="high",
                    detected_at="2026-07-15T02:10:01Z",
                ),
            )

            with patch("scanner.monitor_state_projection.build_dashboard_summary", return_value={}) as build:
                state = service.build_state()

            recommendation = state["config"]["recommendation"]
            self.assertEqual(
                recommendation["current_default_candidate_id"],
                "codex-local-default:gpt-5.5:high",
            )
            self.assertEqual(
                recommendation["effective_current_candidate_id"],
                "codex-local-default:gpt-5.4:high",
            )
            self.assertEqual(recommendation["current_model_source"], "terminal_session")
            self.assertEqual(
                recommendation["current_model_detected_at"],
                "2026-07-15T02:10:01Z",
            )
            self.assertEqual(
                build.call_args.kwargs["current_default_candidate_id"],
                "codex-local-default:gpt-5.4:high",
            )

    def test_snapshot_prefers_explicit_manual_model_over_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            config = config_store.load()
            config.recommendation.current_model_mode = "manual"
            config.recommendation.current_default_candidate_id = (
                "codex-local-default:gpt-5.5:high"
            )
            config_store.save(config)
            service = MonitorService(
                config_store=config_store,
                history_store=HistoryStore(Path(temp_dir) / "history.jsonl"),
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                current_model_detector=lambda: DetectedCodexModel(
                    model="gpt-5.4",
                    effort="high",
                    detected_at="2026-07-15T02:10:01Z",
                ),
            )

            with patch("scanner.monitor_state_projection.build_dashboard_summary", return_value={}) as build:
                state = service.build_state()

            recommendation = state["config"]["recommendation"]
            self.assertEqual(recommendation["current_model_mode"], "manual")
            self.assertEqual(
                recommendation["effective_current_candidate_id"],
                "codex-local-default:gpt-5.5:high",
            )
            self.assertEqual(recommendation["current_model_source"], "manual")
            self.assertEqual(recommendation["detected_current_model"], "gpt-5.4")
            self.assertEqual(
                build.call_args.kwargs["current_default_candidate_id"],
                "codex-local-default:gpt-5.5:high",
            )

    def test_snapshot_does_not_choose_effective_candidate_for_mixed_active_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = MonitorService(
                config_store=ConfigStore(Path(temp_dir) / "config.json"),
                history_store=HistoryStore(Path(temp_dir) / "history.jsonl"),
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                current_model_detector=lambda: DetectedCodexModel(
                    model=None,
                    effort=None,
                    detected_at="2026-07-15T02:10:01Z",
                    status="active_mixed",
                    active_session_count=2,
                    distinct_active_models=(
                        ("gpt-5.4", "xhigh"),
                        ("gpt-5.6-sol", "high"),
                    ),
                    active_sessions=(
                        DetectedCodexSession(
                            id="session-one",
                            workspace_name="project-one",
                            model="gpt-5.4",
                            effort="xhigh",
                            thread_name="修复回归测试",
                        ),
                        DetectedCodexSession(
                            id="session-two",
                            workspace_name="project-two",
                            model="gpt-5.6-sol",
                            effort="high",
                        ),
                    ),
                ),
            )

            with patch("scanner.monitor_state_projection.build_dashboard_summary", return_value={}) as build:
                state = service.build_state()

            recommendation = state["config"]["recommendation"]
            self.assertIsNone(recommendation["effective_current_candidate_id"])
            self.assertEqual(recommendation["current_model_detection_status"], "active_mixed")
            self.assertEqual(recommendation["detected_active_session_count"], 2)
            self.assertEqual(len(recommendation["detected_active_models"]), 2)
            self.assertEqual(
                recommendation["detected_active_sessions"],
                [
                    {
                        "id": "session-one",
                        "workspace_name": "project-one",
                        "model": "gpt-5.4",
                        "effort": "xhigh",
                        "thread_name": "修复回归测试",
                    },
                    {
                        "id": "session-two",
                        "workspace_name": "project-two",
                        "model": "gpt-5.6-sol",
                        "effort": "high",
                        "thread_name": None,
                    },
                ],
            )
            self.assertIsNone(build.call_args.kwargs["current_default_candidate_id"])

    def test_snapshot_preserves_detected_identity_for_unconfigured_efforts(self) -> None:
        for effort in ("max", "ultra"):
            with self.subTest(effort=effort), tempfile.TemporaryDirectory() as temp_dir:
                service = MonitorService(
                    config_store=ConfigStore(Path(temp_dir) / "config.json"),
                    history_store=HistoryStore(Path(temp_dir) / "history.jsonl"),
                    active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                    current_model_detector=lambda effort=effort: DetectedCodexModel(
                        model="gpt-5.6-sol",
                        effort=effort,
                        detected_at="2026-07-17T07:41:24Z",
                    ),
                )

                with patch("scanner.monitor_state_projection.build_dashboard_summary", return_value={}) as build:
                    state = service.build_state()

                recommendation = state["config"]["recommendation"]
                self.assertIsNone(recommendation["effective_current_candidate_id"])
                self.assertEqual(recommendation["current_model_source"], "terminal_session")
                self.assertEqual(recommendation["current_model_detection_status"], "unmapped")
                self.assertEqual(recommendation["detected_current_model"], "gpt-5.6-sol")
                self.assertEqual(recommendation["detected_current_effort"], effort)
                self.assertIsNone(build.call_args.kwargs["current_default_candidate_id"])

    def test_repair_progress_marks_only_target_as_repairing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            events: list[dict[str, object]] = []
            reconstructed_progress: dict[str, int] = {}

            def runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                return ScanResult(
                    run_id=str(kwargs["run_id"]),
                    candidate_id=target.candidate_id,
                    model=target.model,
                    effort=target.effort,
                    phase="scan",
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=int(kwargs["attempt_index"]),
                    started_at="2026-07-14T10:10:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                )

            service, _, run_id, candidate_id = _seed_repair_run(temp_dir, runner)

            persisted_at_start: dict[str, object] = {}

            def capture_progress(event: dict[str, object]) -> None:
                events.append(event)
                if event["type"] == "repair.question.started":
                    persisted_at_start.update(service.active_run_store.load() or {})
                    observer = MonitorService(
                        config_store=service.config_store,
                        history_store=service.history_store,
                        active_run_store=service.active_run_store,
                    )
                    observed_runtime = observer.build_state()["runtime"]
                    observed_entry = next(
                        item
                        for item in observed_runtime["run_entries"]
                        if item["candidate_id"] == candidate_id
                    )
                    reconstructed_progress["completed"] = int(
                        observed_entry["attempts_completed"]
                    )
                    reconstructed_progress["total"] = int(
                        observed_entry["attempts_per_target"]
                    )
                    reconstructed_progress["phase_completed"] = int(
                        observed_runtime["current_phase_completed_targets"]
                    )
                    reconstructed_progress["phase_total"] = int(
                        observed_runtime["current_phase_total_targets"]
                    )
                    reconstructed_progress["progress_completed"] = int(
                        observed_runtime["progress_completed"]
                    )
                    reconstructed_progress["progress_total"] = int(
                        observed_runtime["progress_total"]
                    )

            service.repair_failed_candidate(
                run_id=run_id,
                candidate_id=candidate_id,
                progress_callback=capture_progress,
            )

            started = next(
                event for event in events if event["type"] == "repair.question.started"
            )
            state = started["state"]
            self.assertIsInstance(state, dict)
            self.assertEqual(set(state), {"schema_version", "runtime"})
            self.assertEqual(state["schema_version"], 1)  # type: ignore[index]
            runtime = state["runtime"]  # type: ignore[index]
            target_entry = next(
                entry
                for entry in runtime["run_entries"]  # type: ignore[index]
                if entry["candidate_id"] == candidate_id
            )
            self.assertEqual(target_entry["status"], "running")
            self.assertEqual(target_entry["phase"], "repair")
            self.assertEqual(target_entry["attempts_completed"], 0)
            self.assertEqual(target_entry["attempts_per_target"], 1)
            persisted_entry = next(
                entry
                for entry in persisted_at_start["entries"]  # type: ignore[index]
                if entry["candidate_id"] == candidate_id
            )
            self.assertEqual(persisted_entry["status"], "running")
            self.assertEqual(
                reconstructed_progress,
                {
                    "completed": 0,
                    "total": 1,
                    "phase_completed": 0,
                    "phase_total": 1,
                    "progress_completed": 0,
                    "progress_total": 1,
                },
            )
            self.assertEqual(persisted_entry["phase"], "repair")

    def test_repair_wrong_answer_restores_eligibility_without_retrying_it_again(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            calls: list[str] = []

            def wrong_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                calls.append(question.id)
                return ScanResult(
                    run_id=str(kwargs["run_id"]),
                    candidate_id=target.candidate_id,
                    model=target.model,
                    effort=target.effort,
                    phase="scan",
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=int(kwargs["attempt_index"]),
                    started_at="2026-07-14T10:10:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=False,
                    answer_preview="wrong",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                )

            service, history_store, run_id, candidate_id = _seed_repair_run(
                temp_dir,
                wrong_runner,
            )

            service.repair_failed_candidate(run_id=run_id, candidate_id=candidate_id)

            self.assertEqual(calls, ["02_code_counterexample_maxgap"])
            self.assertEqual(history_store.load_run_metadata(run_id)["status"], "completed")  # type: ignore[index]
            with self.assertRaisesRegex(ValueError, "没有可重试"):
                service.repair_failed_candidate(run_id=run_id, candidate_id=candidate_id)

    def test_repair_hard_failure_remains_degraded_and_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            calls: list[str] = []

            def timeout_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                calls.append(question.id)
                return ScanResult(
                    run_id=str(kwargs["run_id"]),
                    candidate_id=target.candidate_id,
                    model=target.model,
                    effort=target.effort,
                    phase="scan",
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=int(kwargs["attempt_index"]),
                    started_at="2026-07-14T10:10:00+08:00",
                    elapsed_seconds=1200.0,
                    source_mode="live",
                    answer_ok=False,
                    answer_preview="ERROR: codex exec timed out after 1200s",
                    input_tokens=None,
                    output_tokens=None,
                    reasoning_tokens=None,
                    error_message="codex exec timed out after 1200s",
                )

            service, history_store, run_id, candidate_id = _seed_repair_run(
                temp_dir,
                timeout_runner,
            )

            service.repair_failed_candidate(run_id=run_id, candidate_id=candidate_id)

            self.assertEqual(calls, ["02_code_counterexample_maxgap"])
            self.assertEqual(history_store.load_run_metadata(run_id)["status"], "degraded")  # type: ignore[index]

    def test_repair_ignores_removed_candidates_from_round_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            calls: list[str] = []

            def repair_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                calls.append(question.id)
                return ScanResult(
                    run_id=str(kwargs["run_id"]),
                    candidate_id=target.candidate_id,
                    model=target.model,
                    effort=target.effort,
                    phase="scan",
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=int(kwargs["attempt_index"]),
                    started_at="2026-07-22T15:10:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                )

            service, history_store, run_id, candidate_id = _seed_repair_run(
                temp_dir,
                repair_runner,
            )
            removed_candidate_id = "removed-endpoint:retired-model:default"
            metadata = history_store.load_run_metadata(run_id)
            self.assertIsNotNone(metadata)
            metadata["requested_candidate_ids"].append(removed_candidate_id)  # type: ignore[index, union-attr]
            metadata["regular_candidate_ids"].append(removed_candidate_id)  # type: ignore[index, union-attr]
            metadata["candidate_count"] = 2  # type: ignore[index]
            history_store.save_run_metadata(metadata)  # type: ignore[arg-type]

            repaired = service.repair_failed_candidate(
                run_id=run_id,
                candidate_id=candidate_id,
            )

            self.assertEqual(calls, ["02_code_counterexample_maxgap"])
            self.assertEqual(len(repaired), 1)
            self.assertNotIn(
                removed_candidate_id,
                {
                    str(entry["candidate_id"])
                    for entry in service.runtime_state["run_entries"]
                },
            )

    def test_wrong_or_516_result_cannot_be_manually_retried(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            calls: list[str] = []

            def runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                calls.append(question.id)
                raise AssertionError("runner should not be called")

            service, _, run_id, candidate_id = _seed_repair_run(
                temp_dir,
                runner,
                q2_error_message=None,
                q2_reasoning_tokens=516,
            )

            with self.assertRaisesRegex(ValueError, "没有可重试"):
                service.repair_failed_candidate(run_id=run_id, candidate_id=candidate_id)

            self.assertEqual(calls, [])

    def test_repair_rejects_changed_pack_without_writes(self) -> None:
        def runner(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("runner should not be called")

        with tempfile.TemporaryDirectory() as temp_dir:
            service, history_store, run_id, candidate_id = _seed_repair_run(
                temp_dir,
                runner,
            )
            metadata = history_store.load_run_metadata(run_id) or {}
            metadata["question_pack_version"] = "old-pack"
            history_store.save_run_metadata(metadata)
            before = len(history_store.load_all())

            state = service.build_state()
            dashboard = state["dashboard"]
            self.assertIsInstance(dashboard, dict)
            card = next(
                item
                for item in dashboard["cards"]  # type: ignore[index]
                if item["id"] == candidate_id
            )
            self.assertEqual(card["repairable_question_ids"], [])
            self.assertTrue(card["repair_requires_full_scan"])

            with self.assertRaisesRegex(ValueError, "题包已变化"):
                service.repair_failed_candidate(run_id=run_id, candidate_id=candidate_id)

            self.assertEqual(len(history_store.load_all()), before)

    def test_repair_can_resume_matching_persisted_repair_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            calls: list[str] = []

            def runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                calls.append(question.id)
                return ScanResult(
                    run_id=str(kwargs["run_id"]),
                    candidate_id=target.candidate_id,
                    model=target.model,
                    effort=target.effort,
                    phase="scan",
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=int(kwargs["attempt_index"]),
                    started_at="2026-07-14T10:10:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                )

            service, _, run_id, candidate_id = _seed_repair_run(temp_dir, runner)
            service.active_run_store.save(
                {
                    "run_id": run_id,
                    "repair_run_id": run_id,
                    "repair_candidate_id": candidate_id,
                    "repair_question_ids": ["02_code_counterexample_maxgap"],
                    "runtime": {"lifecycle_state": "active_scan"},
                }
            )

            service.repair_failed_candidate(run_id=run_id, candidate_id=candidate_id)

            self.assertEqual(calls, ["02_code_counterexample_maxgap"])

    def test_repair_rejects_stale_run_without_writes(self) -> None:
        def runner(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("runner should not be called")

        with tempfile.TemporaryDirectory() as temp_dir:
            service, history_store, run_id, candidate_id = _seed_repair_run(
                temp_dir,
                runner,
            )
            history_store.append(
                ScanResult(
                    run_id="run-newer",
                    model="gpt-test",
                    effort="high",
                    phase="scan",
                    question_id="q1",
                    question_title="q1",
                    grader_kind="regex",
                    started_at="2026-07-14T11:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="mock",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=1,
                    output_tokens=1,
                    reasoning_tokens=1,
                )
            )
            before = len(history_store.load_all())

            with self.assertRaisesRegex(ValueError, "最新一轮"):
                service.repair_failed_candidate(run_id=run_id, candidate_id=candidate_id)

            self.assertEqual(len(history_store.load_all()), before)

    def test_repair_resume_finalizes_when_last_result_was_already_appended(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            def runner(*args, **kwargs):  # type: ignore[no-untyped-def]
                raise AssertionError("runner should not be called")

            service, history_store, run_id, candidate_id = _seed_repair_run(
                temp_dir,
                runner,
            )
            target = service.scan_target_resolver.enabled_targets(service.load_config())[0]
            question = next(
                item
                for item in service.question_bank.load().enabled_questions
                if item.id == "02_code_counterexample_maxgap"
            )
            history_store.append(
                ScanResult(
                    run_id=run_id,
                    candidate_id=candidate_id,
                    model=target.model,
                    effort=target.effort,
                    phase="scan",
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=2,
                    started_at="2026-07-14T10:10:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                )
            )
            metadata = history_store.load_run_metadata(run_id) or {}
            metadata["status"] = "running"
            metadata["completed_at"] = None
            history_store.save_run_metadata(metadata)
            service.active_run_store.save(
                {
                    "run_id": run_id,
                    "repair_run_id": run_id,
                    "repair_candidate_id": candidate_id,
                    "repair_question_ids": [question.id],
                    "runtime": {"lifecycle_state": "active_scan"},
                }
            )

            results = service.repair_failed_candidate(
                run_id=run_id,
                candidate_id=candidate_id,
            )

            self.assertEqual(results, [])
            self.assertIsNone(service.active_run_store.load())
            self.assertEqual(history_store.load_run_metadata(run_id)["status"], "completed")  # type: ignore[index]

    def test_repair_rejects_in_memory_concurrent_scan_without_writes(self) -> None:
        def runner(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("runner should not be called")

        with tempfile.TemporaryDirectory() as temp_dir:
            service, history_store, run_id, candidate_id = _seed_repair_run(
                temp_dir,
                runner,
            )
            service.runtime_state["is_running"] = True
            before = len(history_store.load_all())

            with self.assertRaisesRegex(ValueError, "扫描正在运行"):
                service.repair_failed_candidate(run_id=run_id, candidate_id=candidate_id)

            self.assertEqual(len(history_store.load_all()), before)

    def test_repair_failed_candidate_runs_only_hard_failed_questions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            config = config_store.load()
            _set_enabled_candidates(config, {"gpt-5.4 / high"})
            config_store.save(config)
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")

            def initial_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                is_timeout = question.id == "02_code_counterexample_maxgap"
                return ScanResult(
                    run_id=str(kwargs["run_id"]),
                    candidate_id=target.candidate_id,
                    model=target.model,
                    effort=target.effort,
                    phase=str(kwargs["phase"]),
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=int(kwargs["attempt_index"]),
                    started_at="2026-07-14T10:00:00+08:00",
                    elapsed_seconds=300.0 if is_timeout else 1.0,
                    source_mode="live",
                    answer_ok=not is_timeout,
                    answer_preview=(
                        "ERROR: codex exec timed out after 300s"
                        if is_timeout
                        else "ok"
                    ),
                    input_tokens=None if is_timeout else 100,
                    output_tokens=None if is_timeout else 20,
                    reasoning_tokens=None if is_timeout else 430,
                    error_message=(
                        "codex exec timed out after 300s" if is_timeout else None
                    ),
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
                runner=initial_runner,
            )
            initial_results = service.run_enabled_targets(force_restart=True)
            run_id = initial_results[0].run_id
            candidate_id = initial_results[0].candidate_id
            original_count = len(history_store.load_all())
            repair_calls: list[tuple[str, str]] = []

            def repair_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                repair_calls.append((target.candidate_id, question.id))
                return ScanResult(
                    run_id=str(kwargs["run_id"]),
                    candidate_id=target.candidate_id,
                    model=target.model,
                    effort=target.effort,
                    phase=str(kwargs["phase"]),
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=int(kwargs["attempt_index"]),
                    started_at="2026-07-14T10:10:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                )

            service.runner = repair_runner
            with patch.object(
                service.execution_engine,
                "execute",
                wraps=service.execution_engine.execute,
            ) as execute:
                repaired = service.repair_failed_candidate(
                    run_id=run_id,
                    candidate_id=candidate_id,
                )

            execute.assert_called_once()
            self.assertTrue(execute.call_args.kwargs["stop_on_failure"])
            self.assertTrue(callable(execute.call_args.kwargs["skip_job"]))
            self.assertEqual(
                repair_calls,
                [(candidate_id, "02_code_counterexample_maxgap")],
            )
            self.assertEqual([item.run_id for item in repaired], [run_id])
            self.assertEqual(len(history_store.load_all()), original_count + 1)
            self.assertEqual(
                history_store.load_run_metadata(run_id)["status"],  # type: ignore[index]
                "completed",
            )

    def test_repair_failed_candidate_can_target_one_hard_failed_question(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            calls: list[str] = []

            def repair_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                calls.append(question.id)
                return ScanResult(
                    run_id=str(kwargs["run_id"]),
                    candidate_id=target.candidate_id,
                    model=target.model,
                    effort=target.effort,
                    phase=str(kwargs["phase"]),
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=int(kwargs["attempt_index"]),
                    started_at="2026-07-23T10:10:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            service, history_store, run_id, candidate_id = _seed_repair_run(
                temp_dir,
                repair_runner,
            )

            with self.assertRaisesRegex(ValueError, "不是执行失败"):
                service.repair_failed_candidate(
                    run_id=run_id,
                    candidate_id=candidate_id,
                    question_id="01_session_bundle_repair",
                )

            repaired = service.repair_failed_candidate(
                run_id=run_id,
                candidate_id=candidate_id,
                question_id="02_code_counterexample_maxgap",
            )

            self.assertEqual(calls, ["02_code_counterexample_maxgap"])
            self.assertEqual([item.question_id for item in repaired], calls)
            self.assertEqual(history_store.load_run_metadata(run_id)["status"], "completed")  # type: ignore[index]
            self.assertEqual(
                [event["type"] for event in service.run_journal_store.load_events(run_id)],
                [
                    "repair.started",
                    "evaluation.retry_started",
                    "evaluation.retry_finished",
                    "repair.completed",
                ],
            )
            self.assertEqual(service.run_journal_store.load_summary(run_id)["status"], "completed")  # type: ignore[index]

    def test_repair_failed_candidate_honors_pause_and_stop_controls(self) -> None:
        for action in ("pause", "stop"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as temp_dir:
                service_holder: dict[str, MonitorService] = {}
                calls: list[str] = []

                def interrupted_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                    calls.append(question.id)
                    service_holder["service"].active_run_store.request_control(action)
                    return ScanResult(
                        run_id=str(kwargs["run_id"]),
                        candidate_id=target.candidate_id,
                        model=target.model,
                        effort=target.effort,
                        phase=str(kwargs["phase"]),
                        question_id=question.id,
                        question_title=question.title,
                        grader_kind=question.grader.kind,
                        attempt_index=int(kwargs["attempt_index"]),
                        started_at="2026-07-22T15:00:00+08:00",
                        elapsed_seconds=0.1,
                        source_mode="live",
                        answer_ok=False,
                        answer_preview="ERROR: request interrupted",
                        input_tokens=None,
                        output_tokens=None,
                        reasoning_tokens=None,
                        error_message="request interrupted",
                        final_status="warn",
                    )

                service, history_store, run_id, candidate_id = _seed_repair_run(
                    temp_dir,
                    interrupted_runner,
                )
                service_holder["service"] = service

                repaired = service.repair_failed_candidate(
                    run_id=run_id,
                    candidate_id=candidate_id,
                )

                self.assertEqual(calls, ["02_code_counterexample_maxgap"])
                self.assertEqual(repaired, [])
                self.assertEqual(service.last_control_action, action)
                self.assertEqual(
                    service.runtime_state["lifecycle_state"],
                    "paused_recoverable" if action == "pause" else "stopped",
                )
                metadata = history_store.load_run_metadata(run_id)
                self.assertEqual(
                    metadata["status"],  # type: ignore[index]
                    "degraded",
                )
                active_run = service.active_run_store.load()
                if action == "pause":
                    self.assertIsNotNone(active_run)
                    self.assertEqual(
                        active_run["runtime"]["lifecycle_state"],  # type: ignore[index]
                        "paused_recoverable",
                    )
                    self.assertEqual(active_run["repair_operation_kind"], "candidate_repair")  # type: ignore[index]
                    runtime = service.build_state()["runtime"]
                    self.assertTrue(runtime["has_resumable_run"])
                    self.assertEqual(runtime["resumable_operation_kind"], "candidate_repair")
                    self.assertEqual(runtime["resumable_operation_run_id"], run_id)
                    self.assertEqual(runtime["resumable_candidate_ids"], [candidate_id])
                else:
                    self.assertIsNone(active_run)

    def test_candidate_repair_pause_after_success_preserves_remaining_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service_holder: dict[str, MonitorService] = {}
            calls: list[str] = []

            def pausing_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                calls.append(question.id)
                if len(calls) == 1:
                    service_holder["service"].active_run_store.request_control("pause")
                return ScanResult(
                    run_id=str(kwargs["run_id"]),
                    candidate_id=target.candidate_id,
                    model=target.model,
                    effort=target.effort,
                    phase=str(kwargs["phase"]),
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=int(kwargs["attempt_index"]),
                    started_at="2026-07-24T16:00:00+08:00",
                    elapsed_seconds=0.1,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=1,
                    output_tokens=1,
                    reasoning_tokens=1,
                    final_status="pass",
                )

            service, history_store, run_id, candidate_id = _seed_repair_run(
                temp_dir,
                pausing_runner,
            )
            service_holder["service"] = service
            question = service.question_bank.load().enabled_questions[2]
            history_store.append(
                ScanResult(
                    run_id=run_id,
                    candidate_id=candidate_id,
                    model="gpt-5.4",
                    effort="high",
                    phase="scan",
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=3,
                    started_at="2026-07-24T15:59:00+08:00",
                    elapsed_seconds=0.1,
                    source_mode="live",
                    answer_ok=False,
                    answer_preview="error",
                    input_tokens=None,
                    output_tokens=None,
                    reasoning_tokens=None,
                    error_message="execution failed",
                    final_status="warn",
                )
            )

            first_results = service.repair_failed_candidate(
                run_id=run_id,
                candidate_id=candidate_id,
            )
            active_run = service.active_run_store.load()

            self.assertEqual(len(first_results), 1)
            self.assertIsNotNone(active_run)
            self.assertEqual(active_run["repair_operation_kind"], "candidate_repair")  # type: ignore[index]
            self.assertEqual(active_run["repair_operation_run_id"], run_id)  # type: ignore[index]
            self.assertEqual(active_run["repair_candidate_id"], candidate_id)  # type: ignore[index]
            self.assertEqual(active_run["repair_question_ids"], [question.id])  # type: ignore[index]
            runtime = service.build_state()["runtime"]
            self.assertEqual(runtime["resumable_operation_kind"], "candidate_repair")
            self.assertEqual(runtime["progress_completed"], 1)
            self.assertEqual(runtime["progress_total"], 2)

            resumed = service.repair_failed_candidate(
                run_id=run_id,
                candidate_id=candidate_id,
            )

            self.assertEqual([item.question_id for item in resumed], [question.id])
            self.assertIsNone(service.active_run_store.load())

    def test_candidate_repair_pause_on_last_success_converges_to_completed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service_holder: dict[str, MonitorService] = {}

            def pausing_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                service_holder["service"].active_run_store.request_control("pause")
                return ScanResult(
                    run_id=str(kwargs["run_id"]),
                    candidate_id=target.candidate_id,
                    model=target.model,
                    effort=target.effort,
                    phase=str(kwargs["phase"]),
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=int(kwargs["attempt_index"]),
                    started_at="2026-07-24T16:00:00+08:00",
                    elapsed_seconds=0.1,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=1,
                    output_tokens=1,
                    reasoning_tokens=1,
                    final_status="pass",
                )

            service, history_store, run_id, candidate_id = _seed_repair_run(
                temp_dir,
                pausing_runner,
            )
            service_holder["service"] = service

            repaired = service.repair_failed_candidate(
                run_id=run_id,
                candidate_id=candidate_id,
            )

            self.assertEqual(len(repaired), 1)
            self.assertIsNone(service.last_control_action)
            self.assertIsNone(service.active_run_store.load())
            self.assertEqual(history_store.load_run_metadata(run_id)["status"], "completed")  # type: ignore[index]
            self.assertEqual(service.runtime_state["lifecycle_state"], "finalizing")

    def test_timeout_repair_pause_preserves_resumable_operation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service_holder: dict[str, MonitorService] = {}

            def interrupted_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                service_holder["service"].active_run_store.request_control("pause")
                return ScanResult(
                    run_id=str(kwargs["run_id"]),
                    candidate_id=target.candidate_id,
                    model=target.model,
                    effort=target.effort,
                    phase=str(kwargs["phase"]),
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=int(kwargs["attempt_index"]),
                    started_at="2026-07-24T15:00:00+08:00",
                    elapsed_seconds=0.1,
                    source_mode="live",
                    answer_ok=False,
                    answer_preview="ERROR: request interrupted",
                    input_tokens=None,
                    output_tokens=None,
                    reasoning_tokens=None,
                    error_message="request interrupted",
                    final_status="warn",
                )

            service, history_store, run_id, candidate_id = _seed_repair_run(
                temp_dir,
                interrupted_runner,
            )
            service_holder["service"] = service
            metadata = history_store.load_run_metadata(run_id)
            metadata["scoring_mode"] = EQUAL_SCORING_MODE  # type: ignore[index]
            history_store.save_run_metadata(metadata)  # type: ignore[arg-type]

            repaired = service.repair_timed_out_questions(
                run_id=run_id,
                candidate_ids=[candidate_id],
            )

            self.assertEqual(repaired, [])
            self.assertEqual(service.last_control_action, "pause")
            self.assertEqual(history_store.load_run_metadata(run_id)["status"], "degraded")  # type: ignore[index]
            self.assertEqual(
                service.run_journal_store.load_events(run_id)[-1]["type"],
                "timeout-repair.paused",
            )
            paused_summary = service.run_journal_store.load_summary(run_id)
            self.assertEqual(paused_summary["status"], "degraded")  # type: ignore[index]
            self.assertEqual(
                paused_summary["lifecycle_state"],  # type: ignore[index]
                "paused_recoverable",
            )
            active_run = service.active_run_store.load()
            self.assertIsNotNone(active_run)
            self.assertEqual(active_run["repair_operation_kind"], "timeout_repair")  # type: ignore[index]
            self.assertEqual(active_run["runtime"]["lifecycle_state"], "paused_recoverable")  # type: ignore[index]
            runtime = service.build_state()["runtime"]
            self.assertEqual(runtime["resumable_operation_kind"], "timeout_repair")
            self.assertEqual(runtime["resumable_operation_run_id"], run_id)
            self.assertEqual(runtime["resumable_candidate_ids"], [candidate_id])

    def test_timeout_repair_pause_on_last_success_converges_to_completed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service_holder: dict[str, MonitorService] = {}

            def pausing_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                service_holder["service"].active_run_store.request_control("pause")
                return ScanResult(
                    run_id=str(kwargs["run_id"]),
                    candidate_id=target.candidate_id,
                    model=target.model,
                    effort=target.effort,
                    phase=str(kwargs["phase"]),
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=int(kwargs["attempt_index"]),
                    started_at="2026-07-24T16:00:00+08:00",
                    elapsed_seconds=0.1,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=1,
                    output_tokens=1,
                    reasoning_tokens=1,
                    final_status="pass",
                )

            service, history_store, run_id, candidate_id = _seed_repair_run(
                temp_dir,
                pausing_runner,
            )
            service_holder["service"] = service
            metadata = history_store.load_run_metadata(run_id)
            metadata["scoring_mode"] = EQUAL_SCORING_MODE  # type: ignore[index]
            history_store.save_run_metadata(metadata)  # type: ignore[arg-type]

            repaired = service.repair_timed_out_questions(
                run_id=run_id,
                candidate_ids=[candidate_id],
            )

            self.assertEqual(len(repaired), 1)
            self.assertIsNone(service.last_control_action)
            self.assertIsNone(service.active_run_store.load())
            self.assertEqual(history_store.load_run_metadata(run_id)["status"], "completed")  # type: ignore[index]
            self.assertEqual(service.runtime_state["lifecycle_state"], "finalizing")

    def test_repair_failed_candidate_can_retry_only_failed_q5(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            config = config_store.load()
            _set_enabled_candidates(config, {"gpt-5.4 / high"})
            config_store.save(config)
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")

            def initial_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                failed = question.id == DEFAULT_QUESTION_IDS[-1]
                return ScanResult(
                    run_id=str(kwargs["run_id"]),
                    candidate_id=target.candidate_id,
                    model=target.model,
                    effort=target.effort,
                    phase=str(kwargs["phase"]),
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=int(kwargs["attempt_index"]),
                    started_at="2026-07-18T10:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=not failed,
                    answer_preview="error" if failed else "ok",
                    input_tokens=None if failed else 100,
                    output_tokens=None if failed else 20,
                    reasoning_tokens=None if failed else 430,
                    error_message="Q5 interrupted" if failed else None,
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
                runner=initial_runner,
            )
            initial_results = service.run_enabled_targets(force_restart=True)
            run_id = initial_results[0].run_id
            candidate_id = initial_results[0].candidate_id
            repair_calls: list[tuple[str, str]] = []

            def repair_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                repair_calls.append((str(kwargs["phase"]), question.id))
                return ScanResult(
                    run_id=str(kwargs["run_id"]),
                    candidate_id=target.candidate_id,
                    model=target.model,
                    effort=target.effort,
                    phase=str(kwargs["phase"]),
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=int(kwargs["attempt_index"]),
                    started_at="2026-07-18T10:10:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                )

            service.runner = repair_runner
            repaired = service.repair_failed_candidate(
                run_id=run_id,
                candidate_id=candidate_id,
            )

            self.assertEqual(
                repair_calls,
                [("scan", "05_cache_regression_test_design")],
            )
            self.assertEqual([item.phase for item in repaired], ["scan"])
            self.assertEqual(
                history_store.load_run_metadata(run_id)["status"],  # type: ignore[index]
                "completed",
            )

    def test_runtime_snapshot_exposes_stable_progress_and_lifecycle_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = MonitorService(
                config_store=ConfigStore(Path(temp_dir) / "config.json"),
                history_store=HistoryStore(Path(temp_dir) / "history.jsonl"),
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
            )

            runtime = service.build_state()["runtime"]

            self.assertEqual(runtime["progress_unit"], "evaluationUnit")
            for key in (
                "progress_completed",
                "progress_total",
                "state_changed_at",
                "finalizing_started_at",
                "last_phase",
                "last_phase_completed",
                "last_phase_total",
                "updated_at",
                "lease_expires_at",
                "lifecycle_state",
            ):
                self.assertIn(key, runtime)

    def test_completed_scan_finalizing_requires_explicit_recovery_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()
            _set_enabled_candidates(config, {"gpt-5.4 / medium"})
            config_store.save(config)

            def successful_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-24T16:00:00+08:00",
                    elapsed_seconds=0.1,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=1,
                    output_tokens=1,
                    reasoning_tokens=1,
                    final_status="pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
                runner=successful_runner,
            )

            service.run_enabled_targets(
                force_restart=True,
                retain_finalizing_state=True,
            )

            active_run = active_run_store.load()
            self.assertIsNotNone(active_run)
            self.assertEqual(active_run["runtime"]["lifecycle_state"], "finalizing")  # type: ignore[index]
            runtime = service.build_state()["runtime"]
            self.assertEqual(runtime["lifecycle_state"], "finalizing")
            self.assertFalse(runtime["is_running"])
            self.assertFalse(runtime["has_resumable_run"])

            with patch("scanner.service._scan_lock_is_active", return_value=True):
                live_observer = MonitorService(
                    config_store=config_store,
                    history_store=history_store,
                    active_run_store=active_run_store,
                    runner=successful_runner,
                )
                self.assertEqual(
                    live_observer.build_state()["runtime"]["lifecycle_state"],
                    "finalizing",
                )
                live_recovery = live_observer.recover_orphaned_finalizing_run(
                    exclusive_lock_held=True
                )
                self.assertEqual(live_recovery["status"], "scan_active")
                self.assertFalse(live_recovery["recovered"])
                self.assertIsNotNone(active_run_store.load())

            restarted_service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
                runner=successful_runner,
            )
            self.assertEqual(
                restarted_service.build_state()["runtime"]["lifecycle_state"],
                "finalizing",
            )
            self.assertIsNotNone(active_run_store.load())

            recovery = restarted_service.recover_orphaned_finalizing_run(
                exclusive_lock_held=True
            )
            restarted_runtime = restarted_service.build_state()["runtime"]

            self.assertTrue(recovery["ok"])
            self.assertTrue(recovery["recovered"])
            self.assertEqual(recovery["status"], "recovered")
            self.assertEqual(recovery["run_id"], active_run["run_id"])  # type: ignore[index]
            self.assertEqual(restarted_runtime["lifecycle_state"], "idle")
            self.assertFalse(restarted_runtime["is_running"])
            self.assertFalse(restarted_runtime["has_resumable_run"])
            self.assertIsNone(active_run_store.load())
            run_id = str(active_run["run_id"])  # type: ignore[index]
            journal_events = restarted_service.run_journal_store.load_events(run_id)
            self.assertEqual(
                journal_events[-1]["type"],
                "run.finalization_recovered",
            )
            recovered_summary = restarted_service.run_journal_store.load_summary(run_id)
            self.assertEqual(recovered_summary["status"], "completed")  # type: ignore[index]
            self.assertEqual(recovered_summary["lifecycle_state"], "idle")  # type: ignore[index]
            self.assertIsNone(recovered_summary["last_error"])  # type: ignore[index]

    def test_completed_scan_finalization_api_preserves_then_commits_authoritative_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()
            _set_enabled_candidates(config, {"gpt-5.4 / medium"})
            config_store.save(config)

            def successful_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-28T18:00:00+08:00",
                    elapsed_seconds=0.1,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=1,
                    output_tokens=1,
                    reasoning_tokens=1,
                    final_status="pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
                runner=successful_runner,
            )
            service.run_enabled_targets(
                force_restart=True,
                retain_finalizing_state=True,
            )
            active_run = active_run_store.load()
            self.assertIsNotNone(active_run)
            run_id = str(active_run["run_id"])

            failure_state = service.record_finalization_projection_failure(
                "recommendation build failed",
                exclusive_lock_held=True,
            )

            persisted_failure = active_run_store.load()
            self.assertIsNotNone(persisted_failure)
            self.assertEqual(persisted_failure["run_metadata"]["status"], "completed")
            self.assertEqual(
                persisted_failure["runtime"]["lifecycle_state"],
                "finalizing",
            )
            self.assertEqual(
                persisted_failure["runtime"]["last_error"],
                "recommendation build failed",
            )
            self.assertEqual(
                failure_state["runtime"]["lifecycle_state"],
                "finalizing",
            )
            self.assertEqual(
                service.run_journal_store.load_events(run_id)[-1]["type"],
                "run.projection_failed",
            )
            summary = service.run_journal_store.load_summary(run_id)
            self.assertEqual(summary["status"], "completed")  # type: ignore[index]
            self.assertEqual(summary["lifecycle_state"], "finalizing")  # type: ignore[index]

            projected_state = service.build_state()
            with patch.object(
                service,
                "recover_orphaned_finalizing_run",
                side_effect=AssertionError("normal commit must not use orphan recovery"),
            ):
                finalized_state = service.complete_finalizing_snapshot(
                    projected_state,
                    exclusive_lock_held=True,
                )

            self.assertIsNone(active_run_store.load())
            self.assertEqual(finalized_state["runtime"]["lifecycle_state"], "idle")
            self.assertFalse(finalized_state["runtime"]["is_running"])
            self.assertIsNone(finalized_state["runtime"]["last_error"])
            self.assertIsNone(finalized_state["runtime"]["finalizing_started_at"])
            self.assertIsNone(finalized_state["runtime"]["lease_expires_at"])
            committed_summary = service.run_journal_store.load_summary(run_id)
            self.assertEqual(committed_summary["status"], "completed")  # type: ignore[index]
            self.assertEqual(committed_summary["lifecycle_state"], "idle")  # type: ignore[index]
            self.assertIsNone(committed_summary["last_error"])  # type: ignore[index]
            self.assertEqual(  # type: ignore[index]
                committed_summary["run_metadata"]["status"],
                "completed",
            )

    def test_explicit_finalizing_recovery_preserves_incomplete_and_non_finalizing_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
            )

            self.assertEqual(
                service.recover_orphaned_finalizing_run(
                    exclusive_lock_held=True
                )["status"],
                "no_active_run",
            )

            active_run_store.save(
                {
                    "run_id": "run-legacy-paused",
                    "entries": [
                        {
                            "attempts_completed": 1,
                            "attempts_per_target": 2,
                        }
                    ],
                }
            )
            legacy_not_finalizing = service.recover_orphaned_finalizing_run(
                exclusive_lock_held=True
            )
            self.assertEqual(legacy_not_finalizing["status"], "not_finalizing")
            self.assertEqual(active_run_store.load()["run_id"], "run-legacy-paused")  # type: ignore[index]

            active_run_store.save(
                {
                    "run_id": "run-paused",
                    "runtime": {"lifecycle_state": "paused_recoverable"},
                    "run_metadata": {"completed_at": None},
                    "entries": [],
                }
            )
            not_finalizing = service.recover_orphaned_finalizing_run(
                exclusive_lock_held=True
            )
            self.assertEqual(not_finalizing["status"], "not_finalizing")
            self.assertIsNotNone(active_run_store.load())

            active_run_store.save(
                {
                    "run_id": "run-incomplete-finalizing",
                    "runtime": {"lifecycle_state": "finalizing"},
                    "run_metadata": {"completed_at": None},
                    "entries": [
                        {
                            "attempts_completed": 1,
                            "attempts_per_target": 2,
                        }
                    ],
                }
            )
            incomplete = service.recover_orphaned_finalizing_run(
                exclusive_lock_held=True
            )
            self.assertEqual(incomplete["status"], "incomplete")
            self.assertFalse(incomplete["recovered"])
            self.assertIsNotNone(active_run_store.load())

    def test_explicit_finalizing_recovery_is_lock_guarded_and_shape_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            service = MonitorService(
                config_store=ConfigStore(Path(temp_dir) / "config.json"),
                history_store=HistoryStore(Path(temp_dir) / "history.jsonl"),
                active_run_store=active_run_store,
            )

            with self.assertRaisesRegex(RuntimeError, "scan process lock"):
                service.recover_orphaned_finalizing_run(
                    exclusive_lock_held=False
                )

            completed_at = datetime.now().astimezone().isoformat(
                timespec="seconds"
            )
            malformed_payloads: list[object] = [
                [],
                {},
                {
                    "run_id": "run-invalid-runtime",
                    "runtime": "finalizing",
                    "run_metadata": {"completed_at": completed_at},
                    "entries": [],
                },
                {
                    "run_id": "run-invalid-entries",
                    "runtime": {"lifecycle_state": "finalizing"},
                    "run_metadata": {"completed_at": completed_at},
                    "entries": None,
                },
                {
                    "run_id": "run-mixed-entries",
                    "runtime": {"lifecycle_state": "finalizing"},
                    "run_metadata": {"completed_at": completed_at},
                    "entries": [
                        {
                            "attempts_completed": 1,
                            "attempts_per_target": 1,
                        },
                        "corrupt",
                    ],
                },
                {
                    "run_id": "run-invalid-progress",
                    "runtime": {"lifecycle_state": "finalizing"},
                    "run_metadata": {"completed_at": None},
                    "entries": [
                        {
                            "attempts_completed": "1",
                            "attempts_per_target": 1,
                        }
                    ],
                },
            ]
            malformed_json_payloads = [
                json.dumps(payload) for payload in malformed_payloads
            ] + ["{"]
            for payload in malformed_json_payloads:
                with self.subTest(payload=payload):
                    active_run_store.path.parent.mkdir(parents=True, exist_ok=True)
                    active_run_store.path.write_text(
                        payload,
                        encoding="utf-8",
                    )
                    before = active_run_store.path.read_bytes()

                    result = service.recover_orphaned_finalizing_run(
                        exclusive_lock_held=True
                    )

                    self.assertEqual(result["status"], "incomplete")
                    self.assertFalse(result["recovered"])
                    self.assertEqual(active_run_store.path.read_bytes(), before)

            active_run_store.save(
                {
                    "run_id": "run-complete-entries",
                    "runtime": {"lifecycle_state": "finalizing"},
                    "run_metadata": {"completed_at": None},
                    "entries": [
                        {
                            "attempts_completed": 2,
                            "attempts_per_target": 2,
                        }
                    ],
                }
            )
            recovered = service.recover_orphaned_finalizing_run(
                exclusive_lock_held=True
            )

            self.assertEqual(recovered["status"], "recovered")
            self.assertTrue(recovered["recovered"])
            self.assertIsNone(active_run_store.load())

    def test_explicit_finalizing_recovery_compensates_summary_and_clear_failures(self) -> None:
        for failure_kind in ("summary", "clear"):
            with self.subTest(failure_kind=failure_kind), tempfile.TemporaryDirectory() as temp_dir:
                config_store = ConfigStore(Path(temp_dir) / "config.json")
                history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
                active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
                service = MonitorService(
                    config_store=config_store,
                    history_store=history_store,
                    active_run_store=active_run_store,
                )
                run_id = f"run-recovery-{failure_kind}"
                completed_at = datetime.now().astimezone().isoformat(
                    timespec="seconds"
                )
                run_metadata = {
                    "run_id": run_id,
                    "status": "completed",
                    "completed_at": completed_at,
                }
                active_run_store.save(
                    {
                        "run_id": run_id,
                        "runtime": {
                            "lifecycle_state": "finalizing",
                            "last_error": "projection failed",
                            "updated_at": completed_at,
                            "progress_completed": 1,
                            "progress_total": 1,
                        },
                        "run_metadata": run_metadata,
                        "entries": [
                            {
                                "attempts_completed": 1,
                                "attempts_per_target": 1,
                            }
                        ],
                    }
                )
                service.run_journal_store.append_event(
                    run_id,
                    "run.completed",
                    {"status": "completed"},
                    occurred_at=completed_at,
                )
                service.run_journal_store.save_summary(
                    run_id,
                    {
                        "status": "completed",
                        "progress_completed": 1,
                        "progress_total": 1,
                        "lifecycle_state": "finalizing",
                        "last_error": "projection failed",
                        "updated_at": completed_at,
                        "run_metadata": run_metadata,
                    },
                )

                target = (
                    service.run_journal_store
                    if failure_kind == "summary"
                    else active_run_store
                )
                method_name = "save_summary" if failure_kind == "summary" else "clear"
                with patch.object(
                    target,
                    method_name,
                    side_effect=OSError(f"{failure_kind} unavailable"),
                ):
                    recovery = service.recover_orphaned_finalizing_run(
                        exclusive_lock_held=True
                    )

                self.assertEqual(recovery["status"], "incomplete")
                self.assertFalse(recovery["recovered"])
                active_run = active_run_store.load()
                self.assertIsNotNone(active_run)
                self.assertEqual(
                    active_run["runtime"]["lifecycle_state"],
                    "finalizing",
                )
                summary = service.run_journal_store.load_summary(run_id)
                self.assertEqual(summary["lifecycle_state"], "finalizing")  # type: ignore[index]
                events = service.run_journal_store.load_events(run_id)
                self.assertEqual(events[-1]["type"], "run.completed")
                self.assertNotIn(
                    "run.finalization_recovered",
                    [event["type"] for event in events],
                )

    def test_explicit_finalizing_recovery_compensates_event_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            service = MonitorService(
                config_store=ConfigStore(Path(temp_dir) / "config.json"),
                history_store=HistoryStore(Path(temp_dir) / "history.jsonl"),
                active_run_store=active_run_store,
            )
            completed_at = datetime.now().astimezone().isoformat(
                timespec="seconds"
            )
            run_id = "run-recovery-event"
            run_metadata = {
                "run_id": run_id,
                "status": "completed",
                "completed_at": completed_at,
            }
            active_run_store.save(
                {
                    "run_id": run_id,
                    "runtime": {
                        "lifecycle_state": "finalizing",
                        "last_error": "projection failed",
                        "updated_at": completed_at,
                        "progress_completed": 1,
                        "progress_total": 1,
                    },
                    "run_metadata": run_metadata,
                    "entries": [
                        {
                            "attempts_completed": 1,
                            "attempts_per_target": 1,
                        }
                    ],
                }
            )
            service.run_journal_store.save_summary(
                run_id,
                {
                    "status": "completed",
                    "progress_completed": 1,
                    "progress_total": 1,
                    "lifecycle_state": "finalizing",
                    "last_error": "projection failed",
                    "updated_at": completed_at,
                    "run_metadata": run_metadata,
                },
            )

            with patch.object(
                service.run_journal_store,
                "append_event",
                side_effect=OSError("event unavailable"),
            ):
                recovery = service.recover_orphaned_finalizing_run(
                    exclusive_lock_held=True
                )

            self.assertEqual(recovery["status"], "incomplete")
            self.assertFalse(recovery["recovered"])
            self.assertEqual(active_run_store.load()["run_id"], run_id)  # type: ignore[index]
            summary = service.run_journal_store.load_summary(run_id)
            self.assertEqual(summary["lifecycle_state"], "finalizing")  # type: ignore[index]
            self.assertEqual(summary["last_error"], "projection failed")  # type: ignore[index]

    def test_snapshot_projects_finalizing_runtime_without_run_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            active_run_store = ActiveRunStore(root / "active_run.json")
            service = MonitorService(
                config_store=ConfigStore(root / "config.json"),
                history_store=HistoryStore(root / "history.jsonl"),
                active_run_store=active_run_store,
            )
            completed_at = datetime.now().astimezone().isoformat(
                timespec="seconds"
            )
            active_run_store.save(
                {
                    "run_id": "run-empty-finalizing",
                    "runtime": {
                        "lifecycle_state": "finalizing",
                        "state_changed_at": completed_at,
                        "finalizing_started_at": completed_at,
                        "updated_at": completed_at,
                        "progress_completed": 1,
                        "progress_total": 1,
                        "current_phase": "repair",
                        "last_error": "projection failed",
                    },
                    "run_metadata": {
                        "run_id": "run-empty-finalizing",
                        "status": "completed",
                        "completed_at": completed_at,
                    },
                    "entries": [],
                }
            )

            runtime = service.build_state()["runtime"]

            self.assertEqual(runtime["lifecycle_state"], "finalizing")
            self.assertEqual(runtime["current_run_id"], "run-empty-finalizing")
            self.assertEqual(runtime["current_phase"], "repair")
            self.assertEqual(runtime["progress_completed"], 1)
            self.assertEqual(runtime["progress_total"], 1)
            self.assertEqual(runtime["last_error"], "projection failed")

    def test_finalization_failure_reports_each_persistence_error(self) -> None:
        failure_targets = (
            ("active_run", "active", "mutate"),
            ("journal_event", "journal", "append_event"),
            ("journal_summary", "journal", "save_summary"),
        )
        for expected_prefix, target_kind, method_name in failure_targets:
            with self.subTest(expected_prefix=expected_prefix), tempfile.TemporaryDirectory() as temp_dir:
                active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
                service = MonitorService(
                    config_store=ConfigStore(Path(temp_dir) / "config.json"),
                    history_store=HistoryStore(Path(temp_dir) / "history.jsonl"),
                    active_run_store=active_run_store,
                )
                completed_at = datetime.now().astimezone().isoformat(
                    timespec="seconds"
                )
                run_id = f"run-finalization-{expected_prefix}"
                active_run_store.save(
                    {
                        "run_id": run_id,
                        "runtime": {
                            "lifecycle_state": "finalizing",
                            "state_changed_at": completed_at,
                            "finalizing_started_at": completed_at,
                            "updated_at": completed_at,
                        },
                        "run_metadata": {
                            "run_id": run_id,
                            "status": "completed",
                            "completed_at": completed_at,
                        },
                        "entries": [],
                    }
                )
                target = (
                    active_run_store
                    if target_kind == "active"
                    else service.run_journal_store
                )

                with patch.object(
                    target,
                    method_name,
                    side_effect=OSError("persistence unavailable"),
                ):
                    state = service.record_finalization_projection_failure(
                        "projection failed",
                        exclusive_lock_held=True,
                    )

                self.assertTrue(
                    any(
                        item.startswith(f"{expected_prefix}:")
                        for item in state["persistence_errors"]
                    )
                )
                self.assertEqual(
                    state["runtime"]["lifecycle_state"],
                    "finalizing",
                )
                self.assertEqual(
                    state["runtime"]["last_error"],
                    "projection failed",
                )

    def test_persisted_runtime_lease_controls_active_and_recoverable_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
            )
            target = service.scan_target_resolver.enabled_targets(config_store.load())[0]
            now = datetime.now(timezone.utc)

            def active_run(lease_expires_at: datetime) -> dict[str, object]:
                return {
                    "run_id": "run-lease",
                    "run_metadata": {
                        "run_id": "run-lease",
                        "status": "running",
                        "started_at": now.isoformat(),
                    },
                    "runtime": {
                        "lifecycle_state": "active_scan",
                        "state_changed_at": now.isoformat(),
                        "updated_at": now.isoformat(),
                        "lease_expires_at": lease_expires_at.isoformat(),
                        "last_phase": None,
                        "last_phase_completed": 0,
                        "last_phase_total": 0,
                    },
                    "planned_attempts_by_candidate": {
                        target.candidate_id: DEFAULT_QUESTION_COUNT
                    },
                    "entries": [
                        {
                            "candidate_id": target.candidate_id,
                            "model": target.model,
                            "effort": target.effort,
                            "label": target.label,
                            "status": "running",
                            "attempts_completed": 0,
                            "attempts_per_target": DEFAULT_QUESTION_COUNT,
                            "phase": "scan",
                            "flags": [],
                            "error_message": None,
                            "final_status": None,
                            "reasoning_tokens": None,
                        }
                    ],
                }

            active_run_store.save(active_run(now + timedelta(minutes=5)))
            active_runtime = service.build_state()["runtime"]
            self.assertTrue(active_runtime["is_running"])
            self.assertEqual(active_runtime["lifecycle_state"], "active_scan")

            active_run_store.save(active_run(now - timedelta(seconds=1)))
            expired_runtime = service.build_state()["runtime"]
            self.assertFalse(expired_runtime["is_running"])
            self.assertEqual(expired_runtime["lifecycle_state"], "paused_recoverable")
            self.assertTrue(expired_runtime["has_resumable_run"])

    def test_active_run_lease_heartbeat_is_owned_by_service(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            service = MonitorService(
                config_store=ConfigStore(Path(temp_dir) / "config.json"),
                history_store=HistoryStore(Path(temp_dir) / "history.jsonl"),
                active_run_store=active_run_store,
            )
            stale_updated_at = "2026-07-28T10:00:00+00:00"
            active_run_store.save(
                {
                    "run_id": "run-heartbeat",
                    "runtime": {
                        "lifecycle_state": "active_scan",
                        "updated_at": stale_updated_at,
                        "lease_expires_at": stale_updated_at,
                        "lease_duration_seconds": 600,
                    },
                }
            )

            updated = service.heartbeat_active_run_lease()

            self.assertIsNotNone(updated)
            runtime = updated["runtime"]  # type: ignore[index]
            self.assertNotEqual(runtime["updated_at"], stale_updated_at)
            self.assertGreater(
                datetime.fromisoformat(runtime["lease_expires_at"]),
                datetime.fromisoformat(runtime["updated_at"]),
            )

    def test_runtime_snapshot_preserves_failed_and_interrupted_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()
            candidates = [
                candidate
                for connection in config.model_ingress.connections
                for candidate in connection.model_candidates
            ][:3]
            selected_ids = {candidate.id for candidate in candidates}
            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = candidate.id in selected_ids
            config_store.save(config)
            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
            )
            targets = service.scan_target_resolver.enabled_targets(config)
            now = datetime.now(timezone.utc)
            statuses = ("failed", "running", "interrupted")
            active_run_store.save(
                {
                    "run_id": "run-status-contract",
                    "run_metadata": {
                        "run_id": "run-status-contract",
                        "status": "running",
                        "started_at": now.isoformat(),
                    },
                    "runtime": {
                        "lifecycle_state": "active_scan",
                        "state_changed_at": now.isoformat(),
                        "updated_at": now.isoformat(),
                        "lease_expires_at": (now + timedelta(minutes=5)).isoformat(),
                        "active_evaluation_count": 1,
                        "queued_evaluation_count": 0,
                    },
                    "planned_attempts_by_candidate": {
                        target.candidate_id: DEFAULT_QUESTION_COUNT
                        for target in targets
                    },
                    "entries": [
                        {
                            "candidate_id": target.candidate_id,
                            "model": target.model,
                            "effort": target.effort,
                            "label": target.label,
                            "status": status,
                            "attempts_completed": 0,
                            "attempts_per_target": DEFAULT_QUESTION_COUNT,
                            "phase": "scan",
                            "flags": [],
                            "error_message": (
                                "transport unavailable" if status == "failed" else None
                            ),
                            "final_status": "warn" if status == "failed" else None,
                            "reasoning_tokens": None,
                        }
                        for target, status in zip(targets, statuses)
                    ],
                }
            )

            runtime = service.build_refresh_state()["runtime"]
            entries = {
                entry["candidate_id"]: entry
                for entry in runtime["run_entries"]
            }

            self.assertEqual(entries[targets[0].candidate_id]["status"], "failed")
            self.assertEqual(entries[targets[1].candidate_id]["status"], "running")
            self.assertEqual(entries[targets[2].candidate_id]["status"], "interrupted")
            self.assertEqual(runtime["current_target"], targets[1].display_label)

    def test_target_is_done_only_after_q5_completes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()
            _set_enabled_candidates(config, {"gpt-5.4 / medium"})
            config_store.save(config)
            events: list[dict[str, object]] = []

            def fake_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-12T18:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="mock",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
                runner=fake_runner,
            )

            service.run_enabled_targets(progress_callback=events.append)

            progress_events = [event for event in events if event["type"] == "scan.progress"]
            self.assertFalse(
                any(
                    event["state"]["runtime"]["run_entries"][0]["status"] == "done"  # type: ignore[index]
                    and event["state"]["runtime"]["run_entries"][0]["attempts_completed"]
                    < DEFAULT_QUESTION_COUNT  # type: ignore[index]
                    for event in progress_events
                )
            )
            final_entry = progress_events[-1]["state"]["runtime"]["run_entries"][0]  # type: ignore[index]
            self.assertEqual(final_entry["status"], "done")
            self.assertEqual(final_entry["phase"], "scan")
            self.assertEqual(final_entry["attempts_completed"], DEFAULT_QUESTION_COUNT)

    def test_progress_events_emit_runtime_delta_without_building_dashboard(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            config = config_store.load()
            _set_enabled_candidates(config, {"gpt-5.4 / medium"})
            config_store.save(config)

            def fake_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-28T10:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                runner=fake_runner,
            )
            events: list[dict[str, object]] = []
            original_build_state = service.build_state

            with patch.object(
                service,
                "build_state",
                wraps=original_build_state,
            ) as build_state:
                service.run_enabled_targets(progress_callback=events.append)

            progress_completed = [
                event["state"]["runtime"]["completed_targets"]  # type: ignore[index]
                for event in events
                if event["type"] == "scan.progress"
            ]
            progress_states = [
                event["state"]
                for event in events
                if event["type"] in {"target.started", "scan.progress"}
            ]
            self.assertTrue(progress_states)
            for state in progress_states:
                self.assertEqual(set(state), {"schema_version", "runtime"})
                self.assertEqual(state["schema_version"], 1)
            self.assertEqual(build_state.call_count, 0)
            self.assertEqual(
                progress_completed,
                list(range(1, DEFAULT_QUESTION_COUNT + 1)),
            )

    def test_build_state_statistics_reads_eight_rounds_beyond_recent_history_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            config = config_store.load()
            candidates = [
                candidate
                for connection in config.model_ingress.connections
                for candidate in connection.model_candidates
            ][:6]
            for run_index in range(1, 9):
                run_id = f"run-{run_index:02d}"
                history_store.save_run_metadata(
                    {
                        "run_id": run_id,
                        "question_pack_id": "coding-fast",
                        "question_pack_version": DEFAULT_QUESTION_PACK_VERSION,
                        "started_at": f"2026-07-{run_index:02d}T10:00:00+08:00",
                        "completed_at": f"2026-07-{run_index:02d}T10:10:00+08:00",
                        "candidate_count": len(candidates),
                        "question_count": DEFAULT_QUESTION_COUNT,
                        "status": "completed",
                    }
                )
                for candidate in candidates:
                    for question_index in range(1, DEFAULT_QUESTION_COUNT + 1):
                        history_store.append(
                            ScanResult(
                                candidate_id=candidate.id,
                                run_id=run_id,
                                model=candidate.model_id,
                                effort=candidate.scan_profile,
                                phase="scan",
                                question_id=f"q{question_index}",
                                question_title=f"q{question_index}",
                                grader_kind="regex",
                                attempt_index=question_index,
                                started_at=f"2026-07-{run_index:02d}T10:00:00+08:00",
                                elapsed_seconds=1.0,
                                source_mode="live",
                                answer_ok=True,
                                answer_preview="ok",
                                input_tokens=100,
                                output_tokens=20,
                                reasoning_tokens=430,
                                final_status="pass",
                            )
                        )
            config.system.history_limit = 50
            config_store.save(config)
            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
            )

            state = service.build_state()

            self.assertEqual(len(state["history"]), 100)
            self.assertEqual(
                state["runtime"]["history_count"],
                8 * len(candidates) * DEFAULT_QUESTION_COUNT,
            )
            statistics = state["dashboard"]["statistics"]
            self.assertEqual(set(statistics), {"trend_series"})
            self.assertEqual(len(statistics["trend_series"]), len(candidates))
            for series in statistics["trend_series"]:
                self.assertEqual(
                    series["overall_score_run_indices"],
                    list(range(8)),
                )
                self.assertEqual(series["overall_score_values"], [100] * 8)

    def test_pause_preserves_progress_and_resume_skips_completed_questions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()
            _set_enabled_candidates(config, {"gpt-5.4 / medium"})
            config_store.save(config)
            runner_calls: list[str] = []
            resumed_runtime: dict[str, object] | None = None

            def pausing_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                nonlocal resumed_runtime
                runner_calls.append(question.id)
                if len(runner_calls) == 1:
                    active_run_store.request_control("pause")
                elif len(runner_calls) == 2:
                    active_run = active_run_store.load()
                    resumed_runtime = dict((active_run or {}).get("runtime") or {})
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-10T16:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
                runner=pausing_runner,
            )

            first_results = service.run_enabled_targets(selection_mode="regular")
            paused_run = active_run_store.load()

            self.assertEqual(len(first_results), 1)
            self.assertEqual(service.last_control_action, "pause")
            self.assertIsNotNone(paused_run)
            self.assertEqual(paused_run["run_metadata"]["status"], "paused")  # type: ignore[index]
            self.assertEqual(paused_run["runtime"]["current_phase"], "scan")  # type: ignore[index]
            self.assertEqual(paused_run["runtime"]["progress_completed"], 1)  # type: ignore[index]
            self.assertEqual(paused_run["runtime"]["progress_total"], DEFAULT_EVALUATION_COUNT)  # type: ignore[index]

            resumed_results = service.run_enabled_targets(selection_mode="regular")

            self.assertEqual(len(resumed_results), DEFAULT_EVALUATION_COUNT - 1)
            self.assertEqual(len(runner_calls), DEFAULT_EVALUATION_COUNT)
            self.assertEqual(len(set(runner_calls)), DEFAULT_EVALUATION_COUNT)
            self.assertIsNotNone(resumed_runtime)
            self.assertEqual(resumed_runtime["current_phase"], "scan")  # type: ignore[index]
            self.assertEqual(resumed_runtime["progress_completed"], 1)  # type: ignore[index]
            self.assertEqual(resumed_runtime["progress_total"], DEFAULT_EVALUATION_COUNT)  # type: ignore[index]
            self.assertIsNone(active_run_store.load())

    def test_pause_on_last_success_converges_to_completed_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()
            _set_enabled_candidates(config, {"gpt-5.4 / medium"})
            config_store.save(config)

            call_count = 0
            call_lock = threading.Lock()

            def pausing_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                nonlocal call_count
                with call_lock:
                    call_count += 1
                    if call_count == DEFAULT_QUESTION_COUNT:
                        active_run_store.request_control("pause")
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-24T16:00:00+08:00",
                    elapsed_seconds=0.1,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=1,
                    output_tokens=1,
                    reasoning_tokens=1,
                    final_status="pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
                runner=pausing_runner,
            )

            results = service.run_enabled_targets(evaluation_profile_id="quick")

            self.assertEqual(len(results), DEFAULT_QUESTION_COUNT)
            self.assertIsNone(service.last_control_action)
            self.assertIsNone(active_run_store.load())
            metadata = history_store.load_run_metadata(results[0].run_id)
            self.assertEqual(metadata["status"], "completed")  # type: ignore[index]
            self.assertEqual(service.runtime_state["lifecycle_state"], "finalizing")

    def test_pause_discards_an_interrupted_inflight_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()
            _set_enabled_candidates(config, {"gpt-5.4 / medium"})
            config_store.save(config)

            def interrupted_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                active_run_store.request_control("pause")
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-22T15:00:00+08:00",
                    elapsed_seconds=0.1,
                    source_mode="live",
                    answer_ok=False,
                    answer_preview="ERROR: request interrupted",
                    input_tokens=None,
                    output_tokens=None,
                    reasoning_tokens=None,
                    error_message="request interrupted",
                    final_status="warn",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
                runner=interrupted_runner,
            )

            results = service.run_enabled_targets(selection_mode="regular")
            paused_run = active_run_store.load()

            self.assertEqual(results, [])
            self.assertEqual(history_store.load_all(), [])
            self.assertEqual(service.last_control_action, "pause")
            self.assertIsNotNone(paused_run)
            self.assertEqual(paused_run["runtime"]["progress_completed"], 0)  # type: ignore[index]

    def test_stop_discards_inflight_result_and_removes_resume_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()
            _set_enabled_candidates(config, {"gpt-5.4 / medium"})
            config_store.save(config)
            observed_run_ids: list[str] = []

            def stopping_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                observed_run_ids.append(kwargs["run_id"])
                active_run_store.request_control("stop")
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-10T16:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
                runner=stopping_runner,
            )

            results = service.run_enabled_targets(selection_mode="regular")
            metadata = history_store.load_run_metadata(observed_run_ids[0])

            self.assertEqual(results, [])
            self.assertEqual(history_store.load_all(), [])
            self.assertEqual(service.last_control_action, "stop")
            self.assertEqual(metadata["status"], "stopped")
            self.assertIsNone(active_run_store.load())

    def test_concurrent_stop_prevents_other_inflight_jobs_from_retrying(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()
            first_candidate = next(
                candidate
                for connection in config.model_ingress.connections
                for candidate in connection.model_candidates
            )
            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = candidate.id == first_candidate.id
            config.system.max_concurrent_targets = 4
            config_store.save(config)

            first_wave_ready = threading.Barrier(4)
            control_consumed = threading.Event()
            calls_by_question: dict[str, int] = {}
            calls_lock = threading.Lock()
            leader_question_id = DEFAULT_QUESTION_IDS[0]
            original_clear = active_run_store.clear

            def clear() -> None:
                original_clear()
                control_consumed.set()

            active_run_store.clear = clear  # type: ignore[method-assign]

            def stopped_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                with calls_lock:
                    call_count = calls_by_question.get(question.id, 0) + 1
                    calls_by_question[question.id] = call_count
                if call_count == 1:
                    first_wave_ready.wait(timeout=2)
                    if question.id == leader_question_id:
                        active_run_store.request_control("stop")
                    else:
                        control_consumed.wait(timeout=2)
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-22T14:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=False,
                    answer_preview="ERROR: interrupted by stop",
                    input_tokens=None,
                    output_tokens=None,
                    reasoning_tokens=None,
                    error_message="codex exec failed after stop",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
                runner=stopped_runner,
            )

            results = service.run_enabled_targets(selection_mode="regular")

            self.assertTrue(control_consumed.is_set())
            self.assertEqual(service.last_control_action, "stop")
            self.assertEqual(sum(calls_by_question.values()), 4)
            self.assertTrue(all(count == 1 for count in calls_by_question.values()))
            self.assertEqual(results, [])

    def test_api_connection_resolves_immutable_execution_fields(self) -> None:
        config = ConfigStore(Path("unused.json")).load()
        config.model_ingress.connections.append(
            ConnectionConfig(
                id="api-1",
                source_id="custom_endpoint",
                name="Team Gateway",
                enabled=True,
                api_format="openai_responses",
                provider_preset="generic",
                base_url="https://example.com/v1",
                api_key_ref="env:MODELDIAL_TEST_KEY",
                last_test_status="ok",
                model_candidates=[
                    ModelCandidateConfig(
                        id="api-1:gpt-test:high",
                        connection_id="api-1",
                        model_id="gpt-test",
                        display_name="GPT Test High",
                        enabled=False,
                        scan_profile="high",
                    )
                ],
            )
        )
        service = MonitorService()

        target = next(
            item
            for item in service.scan_target_resolver.available_targets(config)
            if item.candidate_id == "api-1:gpt-test:high"
        )

        self.assertEqual(target.connection_mode, "api")
        self.assertEqual(target.api_format, "openai_responses")
        self.assertEqual(target.provider_preset, "generic")
        self.assertEqual(target.base_url, "https://example.com/v1")
        self.assertEqual(target.api_key_ref, "env:MODELDIAL_TEST_KEY")

    def test_unverified_api_connection_is_not_available_for_scan(self) -> None:
        config = ConfigStore(Path("unused.json")).load()
        config.model_ingress.connections.append(
            ConnectionConfig(
                id="api-unverified",
                source_id="custom_endpoint",
                name="Unverified Gateway",
                enabled=True,
                api_format="openai_responses",
                provider_preset="generic",
                base_url="https://example.com/v1",
                api_key_ref="env:MODELDIAL_TEST_KEY",
                last_test_status="rate_limited",
                model_candidates=[
                    ModelCandidateConfig(
                        id="api-unverified:gpt-test:high",
                        connection_id="api-unverified",
                        model_id="gpt-test",
                        display_name="GPT Test High",
                        enabled=True,
                        scan_profile="high",
                    )
                ],
            )
        )
        service = MonitorService()

        available_ids = {
            target.candidate_id
            for target in service.scan_target_resolver.available_targets(config)
        }

        self.assertNotIn("api-unverified:gpt-test:high", available_ids)

        with self.assertRaisesRegex(ValueError, "unknown candidate_id"):
            service.scan_target_resolver.requested_targets(
                config,
                ["api-unverified:gpt-test:high"],
                allow_disabled=True,
            )

    def test_run_metadata_round_trips_scan_selection(self) -> None:
        metadata = RunMetadata(
            run_id="run-single",
            question_pack_id="coding-fast",
            question_pack_version="v1",
            started_at="2026-07-10T10:00:00+08:00",
            completed_at=None,
            candidate_count=1,
            question_count=4,
            status="running",
            selection_mode="single",
            requested_candidate_ids=["candidate-a"],
            regular_candidate_ids=["candidate-a", "candidate-b"],
            comparison_group_id="run-group",
            comparison_group_mode="custom_append",
            comparison_parent_run_id="run-parent",
            append_target_group_id="run-parent",
            appended_candidate_ids=["candidate-c"],
            skipped_candidate_ids=["candidate-a"],
            aggregate_wall_clock_seconds=321,
            is_complete_regular_round=False,
        )

        self.assertEqual(RunMetadata.from_dict(metadata.to_dict()), metadata)

    def test_legacy_run_metadata_defaults_selection_fields(self) -> None:
        metadata = RunMetadata.from_dict({"run_id": "legacy", "status": "completed"})

        self.assertEqual(metadata.selection_mode, "regular")
        self.assertEqual(metadata.requested_candidate_ids, [])
        self.assertEqual(metadata.regular_candidate_ids, [])
        self.assertFalse(metadata.is_complete_regular_round)

    def test_single_scan_runs_explicit_disabled_candidate_without_saving_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()
            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = False
            candidate = config.model_ingress.connections[0].model_candidates[0]
            candidate_id = candidate.id
            config_store.save(config)

            def successful_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-10T10:30:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
                runner=successful_runner,
            )

            results = service.run_enabled_targets(
                requested_candidate_ids=[candidate_id],
                selection_mode="single",
            )
            metadata = history_store.load_run_metadata(results[0].run_id)

            self.assertEqual({item.candidate_id for item in results}, {candidate_id})
            self.assertFalse(
                config_store.load().model_ingress.connections[0].model_candidates[0].enabled
            )
            self.assertEqual(metadata["selection_mode"], "single")
            self.assertEqual(metadata["requested_candidate_ids"], [candidate_id])
            self.assertFalse(metadata["is_complete_regular_round"])

    def test_single_scan_runs_candidate_even_when_source_and_connection_are_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()
            connection = config.model_ingress.connections[0]
            source = next(
                item for item in config.model_ingress.sources if item.id == connection.source_id
            )
            source_id = source.id
            source.enabled = False
            connection.enabled = False
            candidate = connection.model_candidates[0]
            candidate.enabled = False
            candidate_id = candidate.id
            config_store.save(config)

            def successful_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-16T12:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
                runner=successful_runner,
            )

            results = service.run_enabled_targets(
                requested_candidate_ids=[candidate_id],
                selection_mode="single",
            )

            self.assertEqual({item.candidate_id for item in results}, {candidate_id})
            reloaded = config_store.load()
            reloaded_source = next(
                item for item in reloaded.model_ingress.sources if item.id == source_id
            )
            reloaded_connection = next(item for item in reloaded.model_ingress.connections if item.id == connection.id)
            self.assertFalse(reloaded_source.enabled)
            self.assertFalse(reloaded_connection.enabled)
            self.assertFalse(reloaded_connection.model_candidates[0].enabled)

    def test_single_scan_appends_into_latest_regular_round_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()

            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = False
            candidate_a = config.model_ingress.connections[0].model_candidates[0]
            candidate_b = config.model_ingress.connections[0].model_candidates[1]
            candidate_a.enabled = True
            config_store.save(config)

            runner_calls: list[tuple[str, str, str]] = []

            def successful_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                runner_calls.append((kwargs["run_id"], target.candidate_id, question.id))
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-16T10:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
                runner=successful_runner,
            )

            first_results = service.run_enabled_targets(selection_mode="regular")
            first_run_id = first_results[0].run_id
            second_results = service.run_enabled_targets(
                requested_candidate_ids=[candidate_b.id],
                selection_mode="single",
            )
            second_run_id = second_results[0].run_id
            second_metadata = history_store.load_run_metadata(second_run_id)
            state = service.build_state()
            dashboard_metadata = state["dashboard"]["run_metadata"]
            second_scan_results = [item for item in second_results if item.phase == "scan"]

            self.assertEqual(
                {item.candidate_id for item in second_scan_results},
                {candidate_b.id},
            )
            self.assertFalse(
                any(
                    run_id == second_run_id
                    and candidate_id == candidate_a.id
                    and question_id in DEFAULT_QUESTION_IDS
                    for run_id, candidate_id, question_id in runner_calls
                )
            )
            self.assertEqual(second_metadata["selection_mode"], "custom")  # type: ignore[index]
            self.assertEqual(second_metadata["comparison_group_mode"], "custom_append")  # type: ignore[index]
            self.assertEqual(second_metadata["appended_candidate_ids"], [candidate_b.id])  # type: ignore[index]
            self.assertEqual(dashboard_metadata["run_id"], first_run_id)
            self.assertEqual(
                set(dashboard_metadata["requested_candidate_ids"]),
                {candidate_a.id, candidate_b.id},
            )
            self.assertEqual(state["dashboard"]["current_run_id"], first_run_id)

    def test_single_scan_append_ignores_removed_candidates_from_historical_round(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()

            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = False
            connection = config.model_ingress.connections[0]
            removed_candidate = connection.model_candidates[0]
            appended_candidate = connection.model_candidates[1]
            removed_candidate.enabled = True
            config_store.save(config)

            runner_calls: list[str] = []

            def successful_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                runner_calls.append(target.candidate_id)
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-22T22:10:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
                runner=successful_runner,
            )
            service.run_enabled_targets(selection_mode="regular")

            updated = config_store.load()
            updated_connection = updated.model_ingress.connections[0]
            updated_connection.model_candidates = [
                candidate
                for candidate in updated_connection.model_candidates
                if candidate.id != removed_candidate.id
            ]
            config_store.save(updated)
            runner_calls.clear()

            appended_results = service.run_enabled_targets(
                requested_candidate_ids=[appended_candidate.id],
                selection_mode="single",
            )
            appended_metadata = history_store.load_run_metadata(
                appended_results[0].run_id
            )

            self.assertEqual(
                {item.candidate_id for item in appended_results},
                {appended_candidate.id},
            )
            self.assertEqual(
                set(runner_calls),
                {appended_candidate.id},
            )
            self.assertEqual(
                appended_metadata["appended_candidate_ids"],  # type: ignore[index]
                [appended_candidate.id],
            )
            self.assertNotIn(
                removed_candidate.id,
                appended_metadata["requested_candidate_ids"],  # type: ignore[index]
            )

    def test_comparison_group_progress_ignores_removed_candidate_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = MonitorService(
                config_store=ConfigStore(Path(temp_dir) / "config.json"),
                history_store=HistoryStore(Path(temp_dir) / "history.jsonl"),
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
            )
            enabled_target = service.scan_target_resolver.enabled_targets(service.load_config())[0]
            question = service.question_bank.load().enabled_questions[0]

            def result(candidate_id: str) -> ScanResult:
                return ScanResult(
                    candidate_id=candidate_id,
                    run_id="run-regular",
                    model=enabled_target.model,
                    effort=enabled_target.effort,
                    phase="scan",
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=1,
                    started_at="2026-07-22T22:10:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            state = service.comparison_group_projector.result_state(
                history=[
                    result(enabled_target.candidate_id),
                    result("removed-connection:removed-model:default"),
                ],
                run_ids=["run-regular"],
                enabled_targets=[enabled_target],
            )

            self.assertEqual(state["completed_count"], 1)
            self.assertEqual(
                state["completed_by_candidate"],
                {enabled_target.candidate_id: 1},
            )

    def test_build_state_recovers_misgrouped_first_scan_into_latest_round(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            config = config_store.load()
            candidates = config.model_ingress.connections[0].model_candidates
            candidate_a, candidate_b = candidates[0], candidates[1]
            for candidate in candidates:
                candidate.enabled = candidate.id in {candidate_a.id, candidate_b.id}
            config_store.save(config)
            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
            )
            pack = service.question_bank.metadata()
            regular_run_id = "run-regular"
            first_scan_run_id = "run-first-scan"

            for index, question_id in enumerate(DEFAULT_QUESTION_IDS, start=1):
                history_store.append(
                    ScanResult(
                        run_id=regular_run_id,
                        candidate_id=candidate_a.id,
                        model=candidate_a.model_id,
                        effort=candidate_a.scan_profile,
                        phase="scan",
                        question_id=question_id,
                        question_title=question_id,
                        grader_kind="exact_keyword",
                        attempt_index=index,
                        started_at="2026-07-17T09:00:00+08:00",
                        elapsed_seconds=1.0,
                        source_mode="live",
                        answer_ok=True,
                        answer_preview="ok",
                        input_tokens=100,
                        output_tokens=20,
                        reasoning_tokens=430,
                        final_status="pass",
                    )
                )
            history_store.save_run_metadata(
                {
                    "run_id": regular_run_id,
                    "question_pack_id": pack.question_pack_id,
                    "question_pack_version": pack.question_pack_version,
                    "started_at": "2026-07-17T09:00:00+08:00",
                    "completed_at": "2026-07-17T09:01:00+08:00",
                    "candidate_count": 1,
                    "question_count": DEFAULT_QUESTION_COUNT,
                    "status": "completed",
                    "selection_mode": "regular",
                    "requested_candidate_ids": [candidate_a.id],
                    "regular_candidate_ids": [candidate_a.id],
                    "comparison_group_id": regular_run_id,
                    "comparison_group_mode": "regular",
                    "is_complete_regular_round": True,
                }
            )
            history_store.append(
                ScanResult(
                    run_id=first_scan_run_id,
                    candidate_id=candidate_b.id,
                    model=candidate_b.model_id,
                    effort=candidate_b.scan_profile,
                    phase="scan",
                    question_id=DEFAULT_QUESTION_IDS[0],
                    question_title=DEFAULT_QUESTION_IDS[0],
                    grader_kind="exact_keyword",
                    attempt_index=1,
                    started_at="2026-07-17T10:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=False,
                    answer_preview="error",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=None,
                    error_message="rate limited",
                    final_status="error",
                )
            )
            history_store.save_run_metadata(
                {
                    "run_id": first_scan_run_id,
                    "question_pack_id": pack.question_pack_id,
                    "question_pack_version": pack.question_pack_version,
                    "started_at": "2026-07-17T10:00:00+08:00",
                    "completed_at": "2026-07-17T10:01:00+08:00",
                    "candidate_count": 1,
                    "question_count": DEFAULT_QUESTION_COUNT,
                    "status": "degraded",
                    "selection_mode": "custom",
                    "requested_candidate_ids": [candidate_b.id],
                    "regular_candidate_ids": [candidate_a.id, candidate_b.id],
                    "comparison_group_id": first_scan_run_id,
                    "comparison_group_mode": "custom_new_round",
                    "is_complete_regular_round": False,
                }
            )

            state = service.build_state()

            self.assertEqual(state["dashboard"]["current_run_id"], regular_run_id)
            self.assertEqual(state["dashboard"]["run_metadata"]["run_id"], regular_run_id)
            self.assertEqual(
                set(state["dashboard"]["run_metadata"]["requested_candidate_ids"]),
                {candidate_a.id, candidate_b.id},
            )
            self.assertEqual(
                {item["candidate_id"] for item in state["dashboard"]["leaderboard"]},
                {candidate_a.id, candidate_b.id},
            )

            def successful_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                return ScanResult(
                    run_id=kwargs["run_id"],
                    candidate_id=target.candidate_id,
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-17T10:05:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            service.runner = successful_runner
            observed_non_target: dict[str, object] = {}

            def capture_progress(event: dict[str, object]) -> None:
                if event["type"] != "repair.question.started" or observed_non_target:
                    return
                observer = MonitorService(
                    config_store=config_store,
                    history_store=history_store,
                    active_run_store=service.active_run_store,
                )
                non_target_entry = next(
                    item
                    for item in observer.build_state()["runtime"]["run_entries"]
                    if item["candidate_id"] == candidate_a.id
                )
                observed_non_target.update(non_target_entry)

            repaired = service.repair_failed_candidate(
                run_id=regular_run_id,
                candidate_id=candidate_b.id,
                progress_callback=capture_progress,
            )
            self.assertEqual(observed_non_target["status"], "done")
            self.assertEqual(
                observed_non_target["attempts_completed"],
                DEFAULT_QUESTION_COUNT,
            )
            self.assertEqual(
                observed_non_target["attempts_per_target"],
                DEFAULT_QUESTION_COUNT,
            )
            self.assertEqual({item.run_id for item in repaired}, {first_scan_run_id})
            repaired_state = service.build_state()
            self.assertEqual(repaired_state["dashboard"]["current_run_id"], regular_run_id)
            repaired_candidate = next(
                item
                for item in repaired_state["dashboard"]["leaderboard"]
                if item["candidate_id"] == candidate_b.id
            )
            self.assertEqual(repaired_candidate["score_text"], "5/5")

    def test_build_state_keeps_latest_regular_round_visible_when_latest_run_is_legacy_single(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()

            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = False
            candidate_a = config.model_ingress.connections[0].model_candidates[0]
            candidate_b = config.model_ingress.connections[0].model_candidates[1]
            candidate_a.enabled = True
            candidate_b.enabled = True
            config_store.save(config)

            regular_run_id = "run-regular"
            single_run_id = "run-single"
            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
            )
            question_pack = service.question_bank.load()
            question_pack_metadata = service.question_bank.metadata()
            question_ids = [question.id for question in question_pack.enabled_questions]

            for index, question_id in enumerate(question_ids, start=1):
                history_store.append(
                    ScanResult(
                        run_id=regular_run_id,
                        candidate_id=candidate_a.id,
                        model=candidate_a.model_id,
                        effort=candidate_a.scan_profile,
                        phase="scan",
                        question_id=question_id,
                        question_title=question_id,
                        grader_kind="exact_keyword",
                        attempt_index=index,
                        started_at="2026-07-16T09:00:00+08:00",
                        elapsed_seconds=1.0,
                        source_mode="live",
                        answer_ok=True,
                        answer_preview="ok",
                        input_tokens=100,
                        output_tokens=20,
                        reasoning_tokens=430,
                        final_status="pass",
                    )
                )
            history_store.save_run_metadata(
                {
                    "run_id": regular_run_id,
                    "question_pack_id": question_pack_metadata.question_pack_id,
                    "question_pack_version": question_pack_metadata.question_pack_version,
                    "started_at": "2026-07-16T09:00:00+08:00",
                    "completed_at": "2026-07-16T09:04:00+08:00",
                    "candidate_count": 1,
                    "question_count": DEFAULT_QUESTION_COUNT,
                    "status": "completed",
                    "selection_mode": "regular",
                    "requested_candidate_ids": [candidate_a.id],
                    "regular_candidate_ids": [candidate_a.id],
                    "comparison_group_id": regular_run_id,
                    "comparison_group_mode": "regular",
                    "is_complete_regular_round": True,
                }
            )

            history_store.append(
                ScanResult(
                    run_id=single_run_id,
                    candidate_id=candidate_b.id,
                    model=candidate_b.model_id,
                    effort=candidate_b.scan_profile,
                    phase="scan",
                    question_id=question_ids[0],
                    question_title=question_ids[0],
                    grader_kind="exact_keyword",
                    attempt_index=1,
                    started_at="2026-07-16T10:00:00+08:00",
                    elapsed_seconds=2.0,
                    source_mode="live",
                    answer_ok=False,
                    answer_preview="error",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=None,
                    error_message="keychain secret is unavailable",
                    final_status="error",
                )
            )
            single_metadata = {
                "run_id": single_run_id,
                "question_pack_id": question_pack_metadata.question_pack_id,
                "question_pack_version": question_pack_metadata.question_pack_version,
                "started_at": "2026-07-16T10:00:00+08:00",
                "completed_at": None,
                "candidate_count": 1,
                "question_count": DEFAULT_QUESTION_COUNT,
                "status": "paused",
                "selection_mode": "single",
                "requested_candidate_ids": [candidate_b.id],
                "regular_candidate_ids": [candidate_a.id],
                "comparison_group_id": single_run_id,
                "comparison_group_mode": "single",
                "is_complete_regular_round": False,
            }
            history_store.save_run_metadata(single_metadata)
            active_run_store.save(
                {
                    "run_id": single_run_id,
                    "run_metadata": dict(single_metadata),
                    "planned_attempts_by_candidate": {candidate_b.id: DEFAULT_QUESTION_COUNT},
                    "planned_attempts": {f"{candidate_b.model_id} / {candidate_b.scan_profile}": DEFAULT_QUESTION_COUNT},
                    "entries": [
                        {
                            "candidate_id": candidate_b.id,
                            "model": candidate_b.model_id,
                            "effort": candidate_b.scan_profile,
                            "label": f"{candidate_b.model_id} / {candidate_b.scan_profile}",
                            "status": "interrupted",
                            "final_status": "error",
                            "reasoning_tokens": None,
                            "attempts_completed": 1,
                            "attempts_per_target": DEFAULT_QUESTION_COUNT,
                            "phase": "scan",
                            "flags": [],
                            "error_message": "keychain secret is unavailable",
                        }
                    ],
                    "runtime": {
                        "lifecycle_state": "paused_recoverable",
                        "progress_completed": 1,
                        "progress_total": DEFAULT_QUESTION_COUNT,
                    },
                }
            )

            state = service.build_state()
            self.assertEqual(state["runtime"]["current_run_id"], single_run_id)
            self.assertEqual(state["runtime"]["resumable_run_id"], single_run_id)
            self.assertEqual(state["dashboard"]["current_run_id"], regular_run_id)
            self.assertEqual(state["dashboard"]["run_metadata"]["run_id"], regular_run_id)
            self.assertEqual(
                state["dashboard"]["run_metadata"]["requested_candidate_ids"],
                [candidate_a.id, candidate_b.id],
            )
            self.assertEqual(
                state["dashboard"]["best_combination"]["candidate_id"],
                candidate_a.id,
            )
            leaderboard_ids = [
                item["candidate_id"] for item in state["dashboard"]["leaderboard"]
            ]
            self.assertIn(candidate_b.id, leaderboard_ids)
            candidate_b_entry = next(
                item
                for item in state["dashboard"]["leaderboard"]
                if item["candidate_id"] == candidate_b.id
            )
            self.assertEqual(candidate_b_entry["score_text"], "0/1")
            self.assertEqual(
                state["dashboard"]["statistics"],
                {
                    "trend_series": [
                        {
                            "candidate_id": candidate_a.id,
                            "overall_score_run_indices": [0],
                            "overall_score_values": [100],
                        }
                    ]
                },
            )

            active_run_store.clear()
            resumed_state = service.build_state()
            self.assertEqual(resumed_state["dashboard"]["current_run_id"], regular_run_id)
            self.assertEqual(
                resumed_state["dashboard"]["run_metadata"]["run_id"],
                regular_run_id,
            )

    def test_explicit_scan_rejects_unknown_candidate_before_run_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            service = MonitorService(
                config_store=ConfigStore(Path(temp_dir) / "config.json"),
                history_store=HistoryStore(Path(temp_dir) / "history.jsonl"),
                active_run_store=active_run_store,
            )

            with self.assertRaisesRegex(ValueError, "unknown candidate_id"):
                service.run_enabled_targets(
                    requested_candidate_ids=["missing"],
                    selection_mode="single",
                )

            self.assertIsNone(active_run_store.load())

    def test_custom_append_only_runs_new_candidates_and_keeps_one_current_round(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()

            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = False
            candidate_a = config.model_ingress.connections[0].model_candidates[0]
            candidate_b = config.model_ingress.connections[0].model_candidates[1]
            candidate_a.enabled = True
            config_store.save(config)

            runner_calls: list[tuple[str, str, str]] = []

            def successful_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                runner_calls.append((kwargs["run_id"], target.candidate_id, question.id))
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-16T10:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
                runner=successful_runner,
            )

            first_results = service.run_enabled_targets(selection_mode="regular")
            first_run_id = first_results[0].run_id
            second_results = service.run_enabled_targets(
                requested_candidate_ids=[candidate_a.id, candidate_b.id],
                selection_mode="custom",
                custom_round_mode="append",
            )
            second_run_id = second_results[0].run_id
            second_metadata = history_store.load_run_metadata(second_run_id)
            assert isinstance(second_metadata, dict)
            second_metadata["status"] = "degraded"
            history_store.save_run_metadata(second_metadata)
            self.assertIsNotNone(second_metadata["completed_at"])
            self.assertIsNone(
                service.scan_planner.infer_active_run_from_history(
                    config,
                    history_store.load_all(),
                )
            )
            state = service.build_state()
            dashboard_metadata = state["dashboard"]["run_metadata"]
            second_scan_results = [item for item in second_results if item.phase == "scan"]

            self.assertEqual({item.candidate_id for item in first_results}, {candidate_a.id})
            self.assertEqual(
                {item.candidate_id for item in second_scan_results},
                {candidate_b.id},
            )
            self.assertFalse(
                any(
                    run_id == second_run_id
                    and candidate_id == candidate_a.id
                    and question_id in DEFAULT_QUESTION_IDS
                    for run_id, candidate_id, question_id in runner_calls
                )
            )
            self.assertEqual(second_metadata["selection_mode"], "custom")  # type: ignore[index]
            self.assertEqual(second_metadata["comparison_group_mode"], "custom_append")  # type: ignore[index]
            self.assertEqual(second_metadata["requested_candidate_ids"], [candidate_a.id, candidate_b.id])  # type: ignore[index]
            self.assertEqual(second_metadata["appended_candidate_ids"], [candidate_b.id])  # type: ignore[index]
            self.assertEqual(second_metadata["skipped_candidate_ids"], [candidate_a.id])  # type: ignore[index]
            self.assertEqual(dashboard_metadata["run_id"], first_run_id)
            self.assertEqual(
                set(dashboard_metadata["requested_candidate_ids"]),
                {candidate_a.id, candidate_b.id},
            )
            self.assertEqual(dashboard_metadata["candidate_count"], 2)
            self.assertFalse(state["runtime"]["has_resumable_run"])

    def test_custom_append_pause_restores_group_progress_and_resumes_only_missing_questions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()
            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = False
            candidate_a, candidate_b = (
                config.model_ingress.connections[0].model_candidates[:2]
            )
            candidate_a.enabled = True
            config_store.save(config)

            runner_calls: list[tuple[str, str]] = []
            pause_next_append_result = False
            pause_requested = False

            def successful_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                nonlocal pause_requested
                runner_calls.append((target.candidate_id, question.id))
                if (
                    pause_next_append_result
                    and target.candidate_id == candidate_b.id
                    and not pause_requested
                ):
                    pause_requested = True
                    active_run_store.request_control("pause")
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-28T10:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
                runner=successful_runner,
            )
            service.run_enabled_targets(selection_mode="regular")
            config = config_store.load()
            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = candidate.id in {candidate_a.id, candidate_b.id}
            config_store.save(config)

            pause_next_append_result = True
            paused_results = service.run_enabled_targets(
                requested_candidate_ids=[candidate_a.id, candidate_b.id],
                selection_mode="custom",
                custom_round_mode="append",
            )
            paused_run = active_run_store.load()

            self.assertEqual(len(paused_results), 1)
            self.assertIsNotNone(paused_run)
            self.assertEqual(paused_run["runtime"]["progress_completed"], 6)  # type: ignore[index]
            self.assertEqual(paused_run["runtime"]["progress_total"], 10)  # type: ignore[index]

            resumed_service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
                runner=successful_runner,
            )
            restored_runtime = resumed_service.build_state()["runtime"]
            self.assertEqual(restored_runtime["completed_targets"], 6)
            self.assertEqual(restored_runtime["total_targets"], 10)

            resumed_results = resumed_service.run_enabled_targets()

            self.assertEqual(len(resumed_results), 4)
            candidate_b_calls = [
                question_id
                for candidate_id, question_id in runner_calls
                if candidate_id == candidate_b.id
            ]
            self.assertEqual(len(candidate_b_calls), DEFAULT_QUESTION_COUNT)
            self.assertEqual(set(candidate_b_calls), set(DEFAULT_QUESTION_IDS))
            self.assertIsNone(active_run_store.load())

    def test_stale_pack_group_is_neither_auto_appended_nor_explicitly_appendable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            config = config_store.load()

            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = False
            candidate_a, candidate_b, candidate_c = (
                config.model_ingress.connections[0].model_candidates[:3]
            )
            candidate_a.enabled = True
            config_store.save(config)

            def successful_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-24T10:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                runner=successful_runner,
            )

            first_results = service.run_enabled_targets(selection_mode="regular")
            first_metadata = history_store.load_run_metadata(first_results[0].run_id)
            assert isinstance(first_metadata, dict)
            first_metadata["question_pack_version"] = "coding-fast-stale"
            history_store.save_run_metadata(first_metadata)

            single_results = service.run_enabled_targets(
                requested_candidate_ids=[candidate_b.id],
                selection_mode="single",
            )
            single_metadata = history_store.load_run_metadata(single_results[0].run_id)
            assert isinstance(single_metadata, dict)
            self.assertEqual(single_metadata["selection_mode"], "single")
            self.assertEqual(single_metadata["comparison_group_mode"], "single")

            with self.assertRaisesRegex(
                ValueError,
                "题包已变化，无法补入原比较轮，请新开一轮",
            ):
                service.run_enabled_targets(
                    requested_candidate_ids=[candidate_a.id, candidate_c.id],
                    selection_mode="custom",
                    custom_round_mode="append",
                )

    def test_single_append_does_not_fill_missing_questions_for_existing_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()

            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = False
            candidate_a = config.model_ingress.connections[0].model_candidates[0]
            candidate_b = config.model_ingress.connections[0].model_candidates[1]
            candidate_a.enabled = True
            candidate_b.enabled = True
            config_store.save(config)

            runner_calls: list[tuple[str, str]] = []

            def successful_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                runner_calls.append((target.candidate_id, question.id))
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-23T10:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
                runner=successful_runner,
            )
            target_a = service.scan_target_resolver.requested_targets(
                config,
                [candidate_a.id],
            )[0]
            questions = service.question_bank.load().enabled_questions
            parent_run_id = "run-stopped-parent"
            for attempt_index, question in enumerate(questions[:-1], start=1):
                history_store.append(
                    ScanResult(
                        candidate_id=target_a.candidate_id,
                        run_id=parent_run_id,
                        model=target_a.model,
                        effort=target_a.effort,
                        phase="scan",
                        question_id=question.id,
                        question_title=question.title,
                        grader_kind=question.grader.kind,
                        attempt_index=attempt_index,
                        started_at="2026-07-23T09:00:00+08:00",
                        elapsed_seconds=1.0,
                        source_mode="live",
                        answer_ok=True,
                        answer_preview="ok",
                        input_tokens=100,
                        output_tokens=20,
                        reasoning_tokens=430,
                        final_status="pass",
                    )
                )
            pack = service.question_bank.metadata()
            history_store.save_run_metadata(
                {
                    "run_id": parent_run_id,
                    "question_pack_id": pack.question_pack_id,
                    "question_pack_version": pack.question_pack_version,
                    "started_at": "2026-07-23T09:00:00+08:00",
                    "completed_at": "2026-07-23T09:04:00+08:00",
                    "candidate_count": 1,
                    "question_count": DEFAULT_QUESTION_COUNT,
                    "status": "stopped",
                    "selection_mode": "regular",
                    "requested_candidate_ids": [candidate_a.id],
                    "regular_candidate_ids": [candidate_a.id],
                    "comparison_group_id": parent_run_id,
                    "comparison_group_mode": "regular",
                    "appended_candidate_ids": [],
                    "skipped_candidate_ids": [],
                    "is_complete_regular_round": False,
                }
            )

            appended_results = service.run_enabled_targets(
                requested_candidate_ids=[candidate_b.id],
                selection_mode="single",
            )

            self.assertEqual(
                {candidate_id for candidate_id, _ in runner_calls},
                {candidate_b.id},
            )
            self.assertEqual(len(appended_results), DEFAULT_QUESTION_COUNT)
            appended_run_id = appended_results[0].run_id
            appended_metadata = history_store.load_run_metadata(appended_run_id)
            self.assertIsNotNone(appended_metadata)
            self.assertEqual(
                appended_metadata["requested_candidate_ids"],  # type: ignore[index]
                [candidate_a.id, candidate_b.id],
            )
            self.assertEqual(
                appended_metadata["appended_candidate_ids"],  # type: ignore[index]
                [candidate_b.id],
            )

    def test_custom_append_rejects_when_every_selected_model_already_ran(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()

            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = False
            candidate_a = config.model_ingress.connections[0].model_candidates[0]
            candidate_a.enabled = True
            config_store.save(config)

            def successful_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-16T10:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
                runner=successful_runner,
            )

            service.run_enabled_targets(selection_mode="regular")
            with self.assertRaisesRegex(ValueError, "所选模型都已在当前轮跑过，请改为新开一轮"):
                service.run_enabled_targets(
                    requested_candidate_ids=[candidate_a.id],
                    selection_mode="custom",
                    custom_round_mode="append",
                )

    def test_run_stops_after_three_consecutive_hard_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()
            _set_enabled_candidates(config, {"gpt-5.4 / medium"})
            config_store.save(config)
            runner_calls: list[str] = []

            def hard_error_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                runner_calls.append(question.id)
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-10T07:30:00+08:00",
                    elapsed_seconds=300.0,
                    source_mode="live",
                    answer_ok=False,
                    answer_preview="ERROR: transport unavailable",
                    input_tokens=None,
                    output_tokens=None,
                    reasoning_tokens=None,
                    error_message="transport unavailable",
                    final_status="warn",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
                runner=hard_error_runner,
            )

            results = service.run_enabled_targets()
            metadata = history_store.load_run_metadata(results[0].run_id)

            self.assertEqual(len(runner_calls), 6)
            self.assertEqual(len(results), 3)
            self.assertEqual(metadata["status"], "failed")
            self.assertFalse(active_run_store.path.exists())

    def test_transient_endpoint_errors_finish_the_remaining_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            config = config_store.load()
            _set_enabled_candidates(config, {"gpt-5.4 / medium"})
            config.system.max_concurrent_targets = 1
            config.system.timeout_retry_count = 0
            for rule in config.rules.values():
                rule.max_retries = 0
            config_store.save(config)
            runner_calls: list[str] = []

            def transient_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                runner_calls.append(question.id)
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-30T18:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="api",
                    answer_ok=False,
                    answer_preview="ERROR: endpoint request failed: server_error",
                    input_tokens=None,
                    output_tokens=None,
                    reasoning_tokens=None,
                    error_message="endpoint request failed: server_error (http 502)",
                    final_status="warn",
                    execution_trace={"endpoint_error_category": "server_error"},
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                runner=transient_runner,
            )

            results = service.run_enabled_targets()
            metadata = history_store.load_run_metadata(results[0].run_id)

            self.assertEqual(runner_calls, list(DEFAULT_QUESTION_IDS))
            self.assertEqual(len(results), DEFAULT_EVALUATION_COUNT)
            self.assertEqual(metadata["status"], "degraded")

    def test_transient_endpoint_retry_uses_backoff_and_configured_retry_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            config = config_store.load()
            _set_enabled_candidates(config, {"gpt-5.4 / medium"})
            config.system.max_concurrent_targets = 1
            config.system.timeout_retry_count = 1
            config.rules["missing_usage"].enabled = False
            config_store.save(config)
            calls_by_question: dict[str, int] = {}

            def recovering_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                calls_by_question[question.id] = calls_by_question.get(question.id, 0) + 1
                transient = (
                    question.id == DEFAULT_QUESTION_IDS[0]
                    and calls_by_question[question.id] == 1
                )
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-30T18:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="api",
                    answer_ok=not transient,
                    answer_preview=(
                        "ERROR: endpoint request failed: server_error"
                        if transient
                        else "ok"
                    ),
                    input_tokens=None if transient else 10,
                    output_tokens=None if transient else 2,
                    reasoning_tokens=None if transient else 1,
                    error_message=(
                        "endpoint request failed: server_error (http 502)"
                        if transient
                        else None
                    ),
                    execution_trace=(
                        {"endpoint_error_category": "server_error"}
                        if transient
                        else {}
                    ),
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                runner=recovering_runner,
            )

            with patch("scanner.service.time.sleep") as retry_sleep:
                results = service.run_enabled_targets()

            self.assertEqual(calls_by_question[DEFAULT_QUESTION_IDS[0]], 2)
            self.assertEqual(sum(calls_by_question.values()), DEFAULT_EVALUATION_COUNT + 1)
            self.assertEqual(results[0].retry_index, 1)
            self.assertEqual(results[0].final_status, "recovered")
            retry_sleep.assert_called_once()
            self.assertGreaterEqual(retry_sleep.call_args.args[0], 12.0)
            self.assertLessEqual(retry_sleep.call_args.args[0], 18.0)

    def test_circuit_breaker_keeps_inflight_candidate_running(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()
            candidates = [
                candidate
                for connection in config.model_ingress.connections
                for candidate in connection.model_candidates
            ]
            selected = candidates[0]
            for candidate in candidates:
                candidate.enabled = candidate.id == selected.id
            config.system.max_concurrent_targets = 4
            for rule in config.rules.values():
                rule.max_retries = 0
            config_store.save(config)

            first_wave_started = threading.Barrier(4)
            release_inflight = threading.Event()
            circuit_persisted = threading.Event()
            original_mutate = active_run_store.mutate

            def observed_mutate(mutator):  # type: ignore[no-untyped-def]
                saved = original_mutate(mutator)
                runtime = saved.get("runtime")
                if (
                    isinstance(runtime, dict)
                    and "扫描已熔断" in str(runtime.get("last_error") or "")
                ):
                    circuit_persisted.set()
                return saved

            active_run_store.mutate = observed_mutate  # type: ignore[method-assign]

            def mixed_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                if question.id in DEFAULT_QUESTION_IDS[:4]:
                    first_wave_started.wait(timeout=2)
                if question.id in {DEFAULT_QUESTION_IDS[0], DEFAULT_QUESTION_IDS[4]}:
                    release_inflight.wait(timeout=5)
                    return ScanResult(
                        candidate_id=target.candidate_id,
                        run_id=kwargs["run_id"],
                        model=target.model,
                        effort=target.effort,
                        phase=kwargs["phase"],
                        question_id=question.id,
                        question_title=question.title,
                        grader_kind=question.grader.kind,
                        attempt_index=kwargs["attempt_index"],
                        started_at="2026-07-24T09:00:00+08:00",
                        elapsed_seconds=1.0,
                        source_mode="live",
                        answer_ok=True,
                        answer_preview="ok",
                        input_tokens=100,
                        output_tokens=20,
                        reasoning_tokens=430,
                        final_status="pass",
                    )
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-24T09:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=False,
                    answer_preview="ERROR: transport unavailable",
                    input_tokens=None,
                    output_tokens=None,
                    reasoning_tokens=None,
                    error_message="transport unavailable",
                    final_status="warn",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
                runner=mixed_runner,
            )
            thread_errors: list[BaseException] = []

            def run_scan() -> None:
                try:
                    service.run_enabled_targets(evaluation_profile_id="full")
                except BaseException as exc:  # pragma: no cover - surfaced below
                    thread_errors.append(exc)

            scan_thread = threading.Thread(target=run_scan)
            scan_thread.start()
            try:
                self.assertTrue(
                    circuit_persisted.wait(timeout=3),
                    f"thread_errors={thread_errors!r} active_run={active_run_store.load()!r}",
                )
                active_run = active_run_store.load() or {}
                runtime = active_run.get("runtime") or {}
                entry = next(
                    item
                    for item in active_run.get("entries", [])
                    if item["candidate_id"] == selected.id
                )

                self.assertGreater(int(runtime["active_evaluation_count"]), 0)
                self.assertEqual(entry["status"], "running")
            finally:
                release_inflight.set()
                scan_thread.join(timeout=5)

            self.assertFalse(scan_thread.is_alive())
            if thread_errors:
                raise thread_errors[0]

    def test_slow_success_does_not_open_circuit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            config = config_store.load()
            _set_enabled_candidates(config, {"gpt-5.4 / medium"})
            config_store.save(config)
            runner_calls: list[str] = []

            def slow_success_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                runner_calls.append(question.id)
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-10T09:00:00+08:00",
                    elapsed_seconds=90.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                runner=slow_success_runner,
            )

            results = service.run_enabled_targets()
            metadata = history_store.load_run_metadata(results[0].run_id)

            self.assertEqual(len(runner_calls), DEFAULT_EVALUATION_COUNT)
            self.assertEqual(metadata["status"], "completed")

    def test_hard_timeout_uses_configured_retry_count_and_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            config = config_store.load()
            config.system.execution_timeout_seconds = 420
            config.system.timeout_retry_count = 1
            config_store.save(config)
            calls: list[int] = []

            def timeout_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                calls.append(kwargs["execution_timeout_seconds"])
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-13T09:00:00+08:00",
                    elapsed_seconds=420.0,
                    source_mode="live",
                    answer_ok=False,
                    answer_preview="ERROR: request timed out after 420s",
                    input_tokens=None,
                    output_tokens=None,
                    reasoning_tokens=None,
                    error_message="request timed out after 420s",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                runner=timeout_runner,
            )
            target = service.scan_target_resolver.enabled_targets(config)[0]
            question = service.question_bank.load().enabled_questions[0]

            result = service._run_target_with_rules(
                target,
                question,
                config,
                run_id="run-timeout",
                phase="scan",
                attempt_index=1,
            )

            self.assertEqual(calls, [420, 420])
            self.assertEqual(result.retry_index, 1)

    def test_run_enabled_targets_supports_configurable_model_concurrency(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()
            _set_enabled_candidates(
                config,
                {
                    "gpt-5.4 / high",
                    "gpt-5.4 / xhigh",
                },
            )
            config.system.max_concurrent_targets = 2
            config_store.save(config)

            active_workers = 0
            max_active_workers = 0
            started_targets: set[str] = set()
            release_barrier = threading.Event()
            started_lock = threading.Lock()

            def concurrent_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                nonlocal active_workers, max_active_workers
                if kwargs["phase"] == "scan" and question.id == DEFAULT_QUESTION_IDS[0]:
                    with started_lock:
                        active_workers += 1
                        max_active_workers = max(max_active_workers, active_workers)
                        started_targets.add(target.candidate_id)
                        if len(started_targets) >= 2:
                            release_barrier.set()
                    release_barrier.wait(timeout=2)
                try:
                    return ScanResult(
                        candidate_id=target.candidate_id,
                        run_id=kwargs["run_id"],
                        model=target.model,
                        effort=target.effort,
                        phase=kwargs["phase"],
                        question_id=question.id,
                        question_title=question.title,
                        grader_kind=question.grader.kind,
                        attempt_index=kwargs["attempt_index"],
                        started_at="2026-07-11T10:00:00+08:00",
                        elapsed_seconds=0.05,
                        source_mode="live",
                        answer_ok=True,
                        answer_preview="ok",
                        input_tokens=100,
                        output_tokens=20,
                        reasoning_tokens=430,
                        final_status="pass",
                    )
                finally:
                    if kwargs["phase"] == "scan" and question.id == DEFAULT_QUESTION_IDS[0]:
                        with started_lock:
                            active_workers -= 1

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
                runner=concurrent_runner,
            )

            results = service.run_enabled_targets()

            self.assertEqual(
                len([item for item in results if item.phase == "scan"]),
                2 * DEFAULT_QUESTION_COUNT,
            )
            self.assertEqual({item.phase for item in results}, {"scan"})
            self.assertGreaterEqual(max_active_workers, 2)

    def test_run_enabled_targets_fills_concurrency_with_one_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            config = config_store.load()
            candidates = [
                candidate
                for connection in config.model_ingress.connections
                for candidate in connection.model_candidates
            ]
            selected = candidates[0]
            for candidate in candidates:
                candidate.enabled = candidate.id == selected.id
            config.system.max_concurrent_targets = 4
            config_store.save(config)

            active_calls = 0
            maximum_active_calls = 0
            release_first_wave = threading.Event()
            call_lock = threading.Lock()

            def concurrent_case_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                nonlocal active_calls, maximum_active_calls
                with call_lock:
                    active_calls += 1
                    maximum_active_calls = max(maximum_active_calls, active_calls)
                    if active_calls >= 4:
                        release_first_wave.set()
                release_first_wave.wait(timeout=0.2)
                with call_lock:
                    active_calls -= 1
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-20T10:00:00+08:00",
                    elapsed_seconds=0.05,
                    source_mode="mock",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=HistoryStore(Path(temp_dir) / "history.jsonl"),
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                runner=concurrent_case_runner,
            )

            results = service.run_enabled_targets()

            self.assertEqual(len(results), DEFAULT_EVALUATION_COUNT)
            self.assertEqual(maximum_active_calls, 4)

    def test_completed_run_with_nonconsecutive_hard_errors_is_degraded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            config = config_store.load()
            _set_enabled_candidates(config, {"gpt-5.4 / medium"})
            config_store.save(config)
            runner_calls = 0

            def intermittent_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                nonlocal runner_calls
                runner_calls += 1
                is_error = question.id in {
                    "01_session_bundle_repair",
                    "03_ci_optimality_certificate",
                }
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-10T09:30:00+08:00",
                    elapsed_seconds=300.0 if is_error else 1.0,
                    source_mode="live",
                    answer_ok=not is_error,
                    answer_preview="ERROR: transport unavailable" if is_error else "ok",
                    input_tokens=None if is_error else 100,
                    output_tokens=None if is_error else 20,
                    reasoning_tokens=None if is_error else 430,
                    error_message="transport unavailable" if is_error else None,
                    final_status="warn" if is_error else "pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                runner=intermittent_runner,
            )

            results = service.run_enabled_targets()
            metadata = history_store.load_run_metadata(results[0].run_id)

            self.assertEqual(
                runner_calls,
                DEFAULT_EVALUATION_COUNT + 2,
            )
            self.assertEqual(metadata["status"], "degraded")

    def test_build_state_preserves_ingress_identity_for_duplicate_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            config = config_store.load()
            config.model_ingress.sources[1].enabled = True
            config.model_ingress.connections[0].model_candidates = [
                ModelCandidateConfig(
                    id="codex-local-default:gpt-5.4:high:a",
                    connection_id="codex-local-default",
                    model_id="gpt-5.4",
                    display_name="GPT-5.4 High A",
                    scan_profile="high",
                    enabled=True,
                )
            ]
            config.model_ingress.connections[1].enabled = True
            config.model_ingress.connections[1].local_login_verified = True
            config.model_ingress.connections[1].model_candidates = [
                ModelCandidateConfig(
                    id="claude-local-default:gpt-5.4:high:b",
                    connection_id="claude-local-default",
                    model_id="gpt-5.4",
                    display_name="GPT-5.4 High B",
                    scan_profile="high",
                    enabled=True,
                )
            ]
            config_store.save(config)
            run_id = "run-ingress-identity"
            for candidate_id, answer_ok in (
                ("codex-local-default:gpt-5.4:high:a", True),
                ("claude-local-default:gpt-5.4:high:b", False),
            ):
                history_store.append(
                    ScanResult(
                        candidate_id=candidate_id,
                        run_id=run_id,
                        model="gpt-5.4",
                        effort="high",
                        phase="scan",
                        question_id="01_candy",
                        started_at="2026-07-10T10:00:00+08:00",
                        elapsed_seconds=1.0,
                        source_mode="live",
                        answer_ok=answer_ok,
                        answer_preview="21" if answer_ok else "20",
                        input_tokens=100,
                        output_tokens=20,
                        reasoning_tokens=430,
                        final_status="pass" if answer_ok else "warn",
                    )
                )
            history_store.save_run_metadata(
                {
                    "run_id": run_id,
                    "question_pack_id": "coding-fast",
                    "question_pack_version": "coding-fast-v1.3",
                    "started_at": "2026-07-10T10:00:00+08:00",
                    "completed_at": "2026-07-10T10:01:00+08:00",
                    "candidate_count": 2,
                    "question_count": 1,
                    "status": "completed",
                }
            )

            dashboard = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
            ).build_state()["dashboard"]
            cards = {
                card["id"]: card
                for card in dashboard["cards"]
                if int(card["recent_count"]) > 0
            }

            self.assertEqual(
                set(cards),
                {
                    "codex-local-default:gpt-5.4:high:a",
                    "claude-local-default:gpt-5.4:high:b",
                },
            )
            self.assertEqual(cards["codex-local-default:gpt-5.4:high:a"]["source_id"], "codex_local")
            self.assertEqual(cards["codex-local-default:gpt-5.4:high:a"]["connection_id"], "codex-local-default")
            self.assertEqual(cards["claude-local-default:gpt-5.4:high:b"]["source_id"], "claude_local")
            self.assertEqual(cards["claude-local-default:gpt-5.4:high:b"]["connection_id"], "claude-local-default")

    def test_run_enabled_targets_uses_live_mode_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            runner_calls: list[bool] = []

            def fake_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                runner_calls.append(use_mock_results)
                return ScanResult(
                    run_id="run-test",
                    model=target.model,
                    effort=target.effort,
                    phase="scan",
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=1,
                    started_at="2026-06-30T10:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=target.model == "gpt-5.4" and target.effort == "medium",
                    answer_preview="21",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                runner=fake_runner,
            )

            service.run_enabled_targets()

            self.assertTrue(runner_calls)
            self.assertEqual(set(runner_calls), {False})

    def test_run_enabled_targets_scans_only_enabled_model_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            config = config_store.load()
            codex_connection = config.model_ingress.connections[0]
            codex_connection.model_candidates = [
                ModelCandidateConfig(
                    id=f"{codex_connection.id}:gpt-5.4:medium",
                    connection_id=codex_connection.id,
                    model_id="gpt-5.4",
                    display_name="GPT-5.4 Medium",
                    scan_profile="medium",
                    enabled=False,
                ),
                ModelCandidateConfig(
                    id=f"{codex_connection.id}:gpt-5.4:high",
                    connection_id=codex_connection.id,
                    model_id="gpt-5.4",
                    display_name="GPT-5.4 High",
                    scan_profile="high",
                    enabled=True,
                ),
                ModelCandidateConfig(
                    id=f"{codex_connection.id}:gpt-5.5:high",
                    connection_id=codex_connection.id,
                    model_id="gpt-5.5",
                    display_name="GPT-5.5 High",
                    scan_profile="high",
                    enabled=False,
                ),
            ]
            config.model_ingress.connections[1].enabled = False
            config_store.save(config)

            runner_calls: list[tuple[str, str, str]] = []

            def fake_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                runner_calls.append((target.model, target.effort, question.id))
                return ScanResult(
                    run_id="run-test",
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-03T12:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                runner=fake_runner,
            )

            results = service.run_enabled_targets()

            self.assertEqual(len(results), DEFAULT_EVALUATION_COUNT)
            self.assertEqual(
                runner_calls,
                expected_calls_for("gpt-5.4", "high"),
            )

    def test_run_enabled_targets_keeps_duplicate_model_profiles_distinct_by_candidate_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            config = config_store.load()
            config.model_ingress.sources[1].enabled = True
            config.model_ingress.connections[0].model_candidates = [
                ModelCandidateConfig(
                    id="codex-local-default:gpt-5.4:high:a",
                    connection_id="codex-local-default",
                    model_id="gpt-5.4",
                    display_name="GPT-5.4 High A",
                    scan_profile="high",
                    enabled=True,
                )
            ]
            config.model_ingress.connections[1].enabled = True
            config.model_ingress.connections[1].local_login_verified = True
            config.model_ingress.connections[1].model_candidates = [
                ModelCandidateConfig(
                    id="claude-local-default:gpt-5.4:high:b",
                    connection_id="claude-local-default",
                    model_id="gpt-5.4",
                    display_name="GPT-5.4 High B",
                    scan_profile="high",
                    enabled=True,
                )
            ]
            config_store.save(config)

            runner_calls: list[tuple[str, str, int]] = []

            def fake_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                runner_calls.append((target.candidate_id, question.id, kwargs["attempt_index"]))
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id="run-dup-candidate",
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-03T18:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=target.candidate_id.endswith(":a"),
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass" if target.candidate_id.endswith(":a") else "warn",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                runner=fake_runner,
            )

            results = service.run_enabled_targets()
            runtime = service.build_state()["runtime"]

            self.assertEqual(len(results), 2 * DEFAULT_EVALUATION_COUNT)
            self.assertEqual(len(runtime["run_entries"]), 2)
            self.assertEqual(runtime["total_targets"], 2 * DEFAULT_EVALUATION_COUNT)
            self.assertEqual(runtime["completed_targets"], 2 * DEFAULT_EVALUATION_COUNT)
            self.assertEqual(
                sorted(entry["candidate_id"] for entry in runtime["run_entries"]),
                [
                    "claude-local-default:gpt-5.4:high:b",
                    "codex-local-default:gpt-5.4:high:a",
                ],
            )
            self.assertTrue(
                all(
                    entry["attempts_completed"] == DEFAULT_QUESTION_COUNT
                    for entry in runtime["run_entries"]
                )
            )
            self.assertEqual(
                sorted({item.candidate_id for item in history_store.load_recent(limit=20)}),
                [
                    "claude-local-default:gpt-5.4:high:b",
                    "codex-local-default:gpt-5.4:high:a",
                ],
            )
            self.assertEqual(
                len([call for call in runner_calls if call[0] == "codex-local-default:gpt-5.4:high:a"]),
                DEFAULT_EVALUATION_COUNT,
            )
            self.assertEqual(
                len([call for call in runner_calls if call[0] == "claude-local-default:gpt-5.4:high:b"]),
                DEFAULT_EVALUATION_COUNT,
            )

    def test_run_enabled_targets_excludes_candidates_from_disabled_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            config = config_store.load()
            config.model_ingress.sources[0].enabled = False
            config.model_ingress.connections[0].enabled = True
            for candidate in config.model_ingress.connections[0].model_candidates:
                candidate.enabled = True
            config.model_ingress.connections[1].enabled = False
            config_store.save(config)

            runner_calls: list[str] = []

            def fake_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                runner_calls.append(target.candidate_id)
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id="run-disabled-source",
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-03T18:10:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                runner=fake_runner,
            )

            results = service.run_enabled_targets()

            self.assertEqual(results, [])
            self.assertEqual(runner_calls, [])
            self.assertEqual(service.build_state()["runtime"]["enabled_target_count"], 0)

    def test_run_enabled_targets_retries_and_persists_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            attempts = {"count": 0}

            def fake_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                attempts["count"] += 1
                if attempts["count"] == 1:
                    return ScanResult(
                        run_id="run-test",
                        model=target.model,
                        effort=target.effort,
                        phase="scan",
                        question_id=question.id,
                        question_title=question.title,
                        grader_kind=question.grader.kind,
                        attempt_index=1,
                        started_at="2026-06-30T10:00:00+08:00",
                        elapsed_seconds=8.0,
                        source_mode="live",
                        answer_ok=target.model == "gpt-5.4" and target.effort == "medium",
                        answer_preview="21",
                        input_tokens=100,
                        output_tokens=20,
                        reasoning_tokens=516,
                    )
                return ScanResult(
                    run_id="run-test",
                    model=target.model,
                    effort=target.effort,
                    phase="scan",
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=1,
                    started_at="2026-06-30T10:00:01+08:00",
                    elapsed_seconds=7.0,
                    source_mode="live",
                    answer_ok=target.model == "gpt-5.4" and target.effort == "medium",
                    answer_preview="21",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                runner=fake_runner,
            )

            results = service.run_enabled_targets()

            self.assertGreaterEqual(attempts["count"], 2)
            self.assertEqual(results[0].final_status, "recovered")
            self.assertEqual(results[0].retry_index, 1)
            self.assertEqual(results[0].source_mode, "live")
            self.assertEqual(
                len(history_store.load_recent(limit=len(results) + 1)),
                len(results),
            )

    def test_run_enabled_targets_runs_q5_for_every_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            q5_calls: list[tuple[str, str, str]] = []
            progress_events: list[dict[str, object]] = []

            def fake_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                phase = kwargs["phase"]
                key = (target.model, target.effort)
                if question.id == DEFAULT_QUESTION_IDS[-1]:
                    q5_calls.append((target.model, target.effort, question.id))
                answer_ok = key in {
                    ("gpt-5.4", "medium"),
                    ("gpt-5.5", "xhigh"),
                }
                if key == ("gpt-5.4", "high"):
                    answer_ok = question.id != "04_transaction_regression_design"
                return ScanResult(
                    run_id="run-test",
                    model=target.model,
                    effort=target.effort,
                    phase=phase,
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=1,
                    started_at="2026-07-02T10:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="mock",
                    answer_ok=answer_ok,
                    answer_preview="ok" if answer_ok else "bad",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                runner=fake_runner,
            )
            persisted_running_labels: list[tuple[str, set[str]]] = []

            def capture_progress(event: dict[str, object]) -> None:
                progress_events.append(event)
                if event["type"] != "target.started":
                    return
                active_run = service.active_run_store.load() or {}
                running_labels = {
                    str(entry["label"])
                    for entry in active_run.get("entries", [])
                    if entry["status"] == "running"
                }
                persisted_running_labels.append((str(event["label"]), running_labels))

            results = service.run_enabled_targets(progress_callback=capture_progress)

            self.assertEqual(len(results), 6 * DEFAULT_QUESTION_COUNT)
            self.assertEqual({item.phase for item in results}, {"scan"})
            self.assertEqual(
                set(q5_calls),
                {
                    ("gpt-5.4", "medium", "05_cache_regression_test_design"),
                    ("gpt-5.4", "high", "05_cache_regression_test_design"),
                    ("gpt-5.4", "xhigh", "05_cache_regression_test_design"),
                    ("gpt-5.5", "medium", "05_cache_regression_test_design"),
                    ("gpt-5.5", "high", "05_cache_regression_test_design"),
                    ("gpt-5.5", "xhigh", "05_cache_regression_test_design"),
                },
            )
            started_events = [
                event for event in progress_events if event["type"] == "target.started"
            ]
            self.assertGreaterEqual(len(started_events), 8)
            for event in started_events:
                state = event["state"]
                runtime = state["runtime"]  # type: ignore[index]
                running_labels = {
                    entry["label"]
                    for entry in runtime["run_entries"]  # type: ignore[index]
                    if entry["status"] == "running"
                }
                self.assertIn(event["label"], running_labels)
            self.assertEqual(len(persisted_running_labels), len(started_events))
            for label, running_labels in persisted_running_labels:
                self.assertIn(label, running_labels)

            runtime = service.build_state()["runtime"]
            self.assertEqual(runtime["total_targets"], 6 * DEFAULT_QUESTION_COUNT)
            self.assertEqual(runtime["last_run_count"], 6 * DEFAULT_QUESTION_COUNT)
            self.assertEqual(
                len(history_store.load_recent(limit=6 * DEFAULT_QUESTION_COUNT + 1)),
                6 * DEFAULT_QUESTION_COUNT,
            )
            metadata = history_store.load_run_metadata(results[0].run_id) or {}
            self.assertEqual(metadata["scoring_mode"], "semantic_q1_q5_equal_v2")
            self.assertEqual(metadata["question_count"], DEFAULT_QUESTION_COUNT)
            self.assertEqual(metadata["question_ids"], DEFAULT_QUESTION_IDS)

    def test_run_enabled_targets_applies_configured_concurrency_to_q5(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            config = config_store.load()
            config.system.max_concurrent_targets = 3
            config_store.save(config)
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            q5_lock = threading.Lock()
            two_q5_started = threading.Event()
            active_q5 = 0
            max_active_q5 = 0

            def fake_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                nonlocal active_q5, max_active_q5
                if question.id == DEFAULT_QUESTION_IDS[-1]:
                    with q5_lock:
                        active_q5 += 1
                        max_active_q5 = max(max_active_q5, active_q5)
                        if active_q5 >= 2:
                            two_q5_started.set()
                    two_q5_started.wait(timeout=1)
                try:
                    return ScanResult(
                        run_id=kwargs["run_id"],
                        model=target.model,
                        effort=target.effort,
                        phase=kwargs["phase"],
                        question_id=question.id,
                        question_title=question.title,
                        grader_kind=question.grader.kind,
                        attempt_index=1,
                        started_at="2026-07-18T10:10:00+08:00",
                        elapsed_seconds=1.0,
                        source_mode="mock",
                        answer_ok=True,
                        answer_preview="ok",
                        input_tokens=100,
                        output_tokens=20,
                        reasoning_tokens=430,
                    )
                finally:
                    if question.id == DEFAULT_QUESTION_IDS[-1]:
                        with q5_lock:
                            active_q5 -= 1

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                runner=fake_runner,
            )

            service.run_enabled_targets()

            self.assertGreaterEqual(max_active_q5, 2)

    def test_equal_score_pipeline_starts_q5_before_other_models_finish_q4(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            config = config_store.load()
            _set_enabled_candidates(config, {"gpt-5.4 / high", "gpt-5.4 / xhigh"})
            config.system.max_concurrent_targets = 2
            config_store.save(config)
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            slow_q4_started = threading.Event()
            slow_q4_finished = threading.Event()
            fast_q5_started = threading.Event()
            q5_started_before_slow_q4_finished: list[bool] = []
            active_runtime_at_q5: list[dict[str, object]] = []
            active_target_at_q5: list[str] = []
            phases_by_effort: dict[str, list[str]] = {"high": [], "xhigh": []}

            def fake_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                phase = str(kwargs["phase"])
                phases_by_effort[target.effort].append(phase)
                if question.id == DEFAULT_QUESTION_IDS[-2]:
                    if target.effort == "xhigh":
                        slow_q4_started.set()
                        fast_q5_started.wait(timeout=0.5)
                        slow_q4_finished.set()
                    else:
                        slow_q4_started.wait(timeout=0.5)
                if question.id == DEFAULT_QUESTION_IDS[-1] and target.effort == "high":
                    q5_started_before_slow_q4_finished.append(
                        not slow_q4_finished.is_set()
                    )
                    active_run = active_run_store.load()
                    active_runtime_at_q5.append(
                        dict((active_run or {}).get("runtime") or {})
                    )
                    active_target_at_q5.append(
                        str(service.runtime_state.get("current_target") or "")
                    )
                    fast_q5_started.set()
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=phase,
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-20T10:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="mock",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
                runner=fake_runner,
            )

            service.run_enabled_targets()

            self.assertEqual(q5_started_before_slow_q4_finished, [True])
            self.assertEqual(
                phases_by_effort["high"],
                ["scan"] * DEFAULT_QUESTION_COUNT,
            )
            self.assertEqual(
                phases_by_effort["xhigh"],
                ["scan"] * DEFAULT_QUESTION_COUNT,
            )
            self.assertEqual(active_runtime_at_q5[0]["current_phase"], "scan")
            self.assertEqual(
                active_target_at_q5[0],
                "gpt-5.4 / high · 扫描 5/5",
            )
            self.assertEqual(
                active_runtime_at_q5[0]["progress_total"],
                2 * DEFAULT_EVALUATION_COUNT,
            )

    def test_build_state_exposes_resumable_run_after_interruption(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            call_count = {"value": 0}

            def flaky_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                call_count["value"] += 1
                if call_count["value"] == 3:
                    raise RuntimeError("interrupted")
                return ScanResult(
                    run_id="run-test",
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-02T14:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                runner=flaky_runner,
            )

            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                service.run_enabled_targets()

            resumed = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                runner=flaky_runner,
            ).build_state()["runtime"]

            self.assertFalse(resumed["is_running"])
            self.assertTrue(resumed["has_resumable_run"])
            self.assertEqual(resumed["completed_targets"], 2)
            self.assertEqual(resumed["total_targets"], 6 * DEFAULT_EVALUATION_COUNT)
            self.assertEqual(resumed["current_phase"], "scan")
            self.assertEqual(resumed["current_phase_completed_targets"], 2)
            self.assertEqual(resumed["current_phase_total_targets"], 6 * DEFAULT_EVALUATION_COUNT)
            self.assertEqual(
                sum(entry["attempts_completed"] for entry in resumed["run_entries"]),
                2,
            )
            self.assertTrue(
                all(
                    entry["attempts_per_target"] == DEFAULT_QUESTION_COUNT
                    for entry in resumed["run_entries"]
                )
            )
            self.assertEqual(resumed["current_run_id"], resumed["resumable_run_id"])

    def test_run_enabled_targets_resumes_only_missing_steps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            first_pass_calls: list[tuple[str, str, str, int]] = []
            resumed_calls: list[tuple[str, str, str, int]] = []

            def flaky_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                first_pass_calls.append(
                    (target.model, target.effort, question.id, kwargs["attempt_index"])
                )
                if len(first_pass_calls) == 3:
                    raise RuntimeError("interrupted")
                return ScanResult(
                    run_id="run-test",
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-02T14:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=target.model == "gpt-5.4" and target.effort == "medium",
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            def resume_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                resumed_calls.append(
                    (target.model, target.effort, question.id, kwargs["attempt_index"])
                )
                return ScanResult(
                    run_id="run-test",
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-02T14:05:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=target.model == "gpt-5.4" and target.effort == "medium",
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                runner=flaky_runner,
            )
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                service.run_enabled_targets()

            resumed_service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                runner=resume_runner,
            )
            results = resumed_service.run_enabled_targets()

            self.assertEqual(len(first_pass_calls), 3)
            self.assertEqual(len(resumed_calls), 6 * DEFAULT_EVALUATION_COUNT - 2)
            self.assertEqual(len(results), 6 * DEFAULT_EVALUATION_COUNT - 2)
            self.assertNotIn(first_pass_calls[0], resumed_calls)
            self.assertNotIn(first_pass_calls[1], resumed_calls)

            runtime = resumed_service.build_state()["runtime"]
            self.assertFalse(runtime["has_resumable_run"])
            self.assertEqual(runtime["last_run_count"], 6 * DEFAULT_EVALUATION_COUNT - 2)
            self.assertEqual(
                len(history_store.load_recent(limit=6 * DEFAULT_EVALUATION_COUNT + 10)),
                6 * DEFAULT_EVALUATION_COUNT,
            )

    def test_build_state_infers_resumable_run_from_partial_history_without_active_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            config = config_store.load()
            _set_enabled_candidates(
                config,
                {
                    "gpt-5.4 / high",
                    "gpt-5.4 / xhigh",
                    "gpt-5.5 / high",
                    "gpt-5.5 / xhigh",
                },
            )
            config_store.save(config)
            for attempt_index, question_id in enumerate(DEFAULT_QUESTION_IDS[:2], start=1):
                history_store.append(
                    ScanResult(
                        run_id="run-partial",
                        model="gpt-5.4",
                        effort="high",
                        phase="scan",
                        question_id=question_id,
                        question_title=question_id,
                        grader_kind="regex",
                        attempt_index=attempt_index,
                        started_at="2026-07-02T14:00:00+08:00",
                        elapsed_seconds=1.0,
                        source_mode="live",
                        answer_ok=True,
                        answer_preview="ok",
                        input_tokens=100,
                        output_tokens=20,
                        reasoning_tokens=430,
                        final_status="pass",
                    )
                )

            state = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
            ).build_state()

            runtime = state["runtime"]
            self.assertTrue(runtime["has_resumable_run"])
            self.assertEqual(runtime["completed_targets"], 2)
            self.assertEqual(runtime["total_targets"], 4 * DEFAULT_EVALUATION_COUNT)
            high_entry = next(item for item in runtime["run_entries"] if item["label"] == "gpt-5.4 / high")
            self.assertEqual(high_entry["attempts_completed"], 2)
            self.assertEqual(
                state["dashboard"]["best_combination"]["score_text"],
                "2/2",
            )

    def test_build_state_deduplicates_duplicate_question_attempts_in_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            config = config_store.load()
            _set_enabled_candidates(
                config,
                {
                    "gpt-5.4 / high",
                    "gpt-5.4 / xhigh",
                    "gpt-5.5 / high",
                    "gpt-5.5 / xhigh",
                },
            )
            config_store.save(config)

            for question_id, attempt_index in [
                ("01_candy", 1),
                ("02_code_counterexample_maxgap", 2),
                ("02_code_counterexample_maxgap", 2),
                ("03_test_coverage_selection", 3),
            ]:
                history_store.append(
                    ScanResult(
                        run_id="run-dup",
                        model="gpt-5.4",
                        effort="high",
                        phase="scan",
                        question_id=question_id,
                        question_title=question_id,
                        grader_kind="regex",
                        attempt_index=attempt_index,
                        started_at="2026-07-03T08:00:00+08:00",
                        elapsed_seconds=1.0,
                        source_mode="live",
                        answer_ok=True,
                        answer_preview="ok",
                        input_tokens=100,
                        output_tokens=20,
                        reasoning_tokens=430,
                        final_status="pass",
                    )
                )

            active_run_store.save(
                {
                    "run_id": "run-dup",
                    "planned_attempts": {
                        "gpt-5.4 / high": 4,
                        "gpt-5.4 / xhigh": 4,
                        "gpt-5.5 / high": 4,
                        "gpt-5.5 / xhigh": 4,
                    },
                    "entries": [
                        {
                            "model": "gpt-5.4",
                            "effort": "high",
                            "label": "gpt-5.4 / high",
                            "status": "interrupted",
                            "final_status": "pass",
                            "reasoning_tokens": 430,
                            "attempts_completed": 4,
                            "attempts_per_target": 4,
                            "phase": "scan",
                            "flags": [],
                            "error_message": None,
                        }
                    ],
                }
            )

            state = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
            ).build_state()

            runtime = state["runtime"]
            high_entry = next(item for item in runtime["run_entries"] if item["label"] == "gpt-5.4 / high")
            self.assertEqual(runtime["completed_targets"], 3)
            self.assertEqual(high_entry["attempts_completed"], 3)
            self.assertEqual(state["dashboard"]["best_combination"]["score_text"], "3/3")

    def test_run_enabled_targets_resumes_from_partial_history_without_active_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            config = config_store.load()
            _set_enabled_candidates(
                config,
                {
                    "gpt-5.4 / high",
                    "gpt-5.4 / xhigh",
                    "gpt-5.5 / high",
                    "gpt-5.5 / xhigh",
                },
            )
            config_store.save(config)
            for attempt_index, question_id in enumerate(DEFAULT_QUESTION_IDS[:2], start=1):
                history_store.append(
                    ScanResult(
                        run_id="run-partial",
                        model="gpt-5.4",
                        effort="high",
                        phase="scan",
                        question_id=question_id,
                        question_title=question_id,
                        grader_kind="regex",
                        attempt_index=attempt_index,
                        started_at="2026-07-02T14:00:00+08:00",
                        elapsed_seconds=1.0,
                        source_mode="live",
                        answer_ok=True,
                        answer_preview="ok",
                        input_tokens=100,
                        output_tokens=20,
                        reasoning_tokens=430,
                        final_status="pass",
                    )
                )

            resumed_calls: list[tuple[str, str, str, int]] = []

            def resume_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                resumed_calls.append(
                    (target.model, target.effort, question.id, kwargs["attempt_index"])
                )
                return ScanResult(
                    run_id="run-partial",
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-02T14:05:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=target.model == "gpt-5.4" and target.effort == "high",
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            resumed_service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                runner=resume_runner,
            )
            results = resumed_service.run_enabled_targets()

            self.assertEqual(len(results), 4 * DEFAULT_EVALUATION_COUNT - 2)
            self.assertNotIn(("gpt-5.4", "high", DEFAULT_QUESTION_IDS[0], 1), resumed_calls)
            self.assertNotIn(("gpt-5.4", "high", DEFAULT_QUESTION_IDS[1], 2), resumed_calls)

    def test_resume_from_history_restores_single_scan_scope_from_run_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            config = config_store.load()
            regular_candidate_ids = [
                candidate.id
                for connection in config.model_ingress.connections
                for candidate in connection.model_candidates
                if candidate.enabled
            ]
            candidate = next(
                candidate
                for connection in config.model_ingress.connections
                for candidate in connection.model_candidates
                if candidate.enabled
            )
            candidate_id = candidate.id
            run_id = "run-single-partial"
            for attempt_index, question_id in enumerate(DEFAULT_QUESTION_IDS[:2], start=1):
                history_store.append(
                    ScanResult(
                        candidate_id=candidate_id,
                        run_id=run_id,
                        model=candidate.model_id,
                        effort=candidate.scan_profile,
                        phase="scan",
                        question_id=question_id,
                        question_title=question_id,
                        grader_kind="regex",
                        attempt_index=attempt_index,
                        started_at="2026-07-10T20:00:00+08:00",
                        elapsed_seconds=1.0,
                        source_mode="live",
                        answer_ok=True,
                        answer_preview="ok",
                        input_tokens=100,
                        output_tokens=20,
                        reasoning_tokens=430,
                        final_status="pass",
                    )
                )
            history_store.save_run_metadata(
                {
                    "run_id": run_id,
                    "question_pack_id": "coding-fast",
                    "question_pack_version": DEFAULT_QUESTION_PACK_VERSION,
                    "started_at": "2026-07-10T20:00:00+08:00",
                    "completed_at": None,
                    "candidate_count": 1,
                    "question_count": DEFAULT_QUESTION_COUNT,
                    "status": "partial",
                    "selection_mode": "single",
                    "requested_candidate_ids": [candidate_id],
                    "regular_candidate_ids": regular_candidate_ids,
                    "is_complete_regular_round": False,
                }
            )
            resumed_candidate_ids: list[str] = []

            def resume_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                resumed_candidate_ids.append(target.candidate_id)
                return ScanResult(
                    candidate_id=target.candidate_id,
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-10T20:05:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            results = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(Path(temp_dir) / "active_run.json"),
                runner=resume_runner,
            ).run_enabled_targets()

            self.assertEqual(len(results), DEFAULT_EVALUATION_COUNT - 2)
            self.assertEqual(set(resumed_candidate_ids), {candidate_id})
            metadata = history_store.load_run_metadata(run_id)
            self.assertEqual(metadata["selection_mode"], "single")
            self.assertEqual(metadata["requested_candidate_ids"], [candidate_id])

    def test_run_enabled_targets_force_restart_ignores_partial_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            for attempt_index, question_id in enumerate(DEFAULT_QUESTION_IDS[:2], start=1):
                history_store.append(
                    ScanResult(
                        run_id="run-partial",
                        model="gpt-5.4",
                        effort="high",
                        phase="scan",
                        question_id=question_id,
                        question_title=question_id,
                        grader_kind="regex",
                        attempt_index=attempt_index,
                        started_at="2026-07-03T11:00:00+08:00",
                        elapsed_seconds=1.0,
                        source_mode="live",
                        answer_ok=True,
                        answer_preview="ok",
                        input_tokens=100,
                        output_tokens=20,
                        reasoning_tokens=430,
                        final_status="pass",
                    )
                )

            restart_calls: list[tuple[str, str, str, int, str]] = []

            def restart_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                restart_calls.append(
                    (
                        target.model,
                        target.effort,
                        question.id,
                        kwargs["attempt_index"],
                        kwargs["run_id"],
                    )
                )
                return ScanResult(
                    run_id=kwargs["run_id"],
                    model=target.model,
                    effort=target.effort,
                    phase=kwargs["phase"],
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=kwargs["attempt_index"],
                    started_at="2026-07-03T11:05:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=target.model == "gpt-5.4" and target.effort == "high",
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            restarted_service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
                runner=restart_runner,
            )
            results = restarted_service.run_enabled_targets(force_restart=True)

            self.assertEqual(len(results), 6 * DEFAULT_EVALUATION_COUNT)
            self.assertEqual(len(restart_calls), 6 * DEFAULT_EVALUATION_COUNT)
            self.assertIn(
                ("gpt-5.4", "high", DEFAULT_QUESTION_IDS[0], 1, results[0].run_id),
                restart_calls,
            )
            self.assertIn(
                ("gpt-5.4", "high", DEFAULT_QUESTION_IDS[1], 2, results[0].run_id),
                restart_calls,
            )
            self.assertNotEqual(results[0].run_id, "run-partial")
            self.assertTrue(all(item.run_id == results[0].run_id for item in results))
            self.assertFalse(restarted_service.build_state()["runtime"]["has_resumable_run"])

    def test_repair_timed_out_questions_retries_only_timeouts_across_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            config = config_store.load()
            candidates = [
                candidate
                for connection in config.model_ingress.connections
                for candidate in connection.model_candidates
            ]
            selected = candidates[:3]
            selected_ids = {candidate.id for candidate in selected}
            for candidate in candidates:
                candidate.enabled = candidate.id in selected_ids
            config.system.max_concurrent_targets = 4
            config_store.save(config)

            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
            )
            targets = service.scan_target_resolver.enabled_targets(config)
            question_pack = service.question_bank.load()
            questions = question_pack.enabled_questions
            timed_out_questions_by_candidate = {
                targets[0].candidate_id: {
                    questions[0].id,
                    questions[1].id,
                    questions[2].id,
                },
                targets[1].candidate_id: {
                    questions[0].id,
                    questions[1].id,
                    questions[2].id,
                },
                targets[2].candidate_id: {questions[1].id},
            }
            run_id = "run-timeout-batch"

            for target_index, target in enumerate(targets):
                for attempt_index, question in enumerate(questions, start=1):
                    is_timeout = question.id in timed_out_questions_by_candidate[
                        target.candidate_id
                    ]
                    is_wrong_answer = target_index == 0 and attempt_index == 4
                    is_slow_success = target_index == 1 and attempt_index == 4
                    history_store.append(
                        ScanResult(
                            run_id=run_id,
                            candidate_id=target.candidate_id,
                            model=target.model,
                            effort=target.effort,
                            phase="scan",
                            question_id=question.id,
                            question_title=question.title,
                            grader_kind=question.grader.kind,
                            attempt_index=attempt_index,
                            started_at="2026-07-20T10:00:00+08:00",
                            elapsed_seconds=(
                                300.0 if is_timeout else 90.0 if is_slow_success else 1.0
                            ),
                            source_mode="live",
                            answer_ok=not is_timeout and not is_wrong_answer,
                            answer_preview="timeout" if is_timeout else "wrong" if is_wrong_answer else "ok",
                            input_tokens=None if is_timeout else 100,
                            output_tokens=None if is_timeout else 20,
                            reasoning_tokens=None if is_timeout else 430,
                            error_message="codex exec timed out after 300s" if is_timeout else None,
                            flags=["timeout"] if is_timeout or is_slow_success else [],
                            final_status=(
                                "timeout" if is_timeout else "warn" if is_slow_success else "pass"
                            ),
                        )
                    )

            history_store.save_run_metadata(
                {
                    "run_id": run_id,
                    "question_pack_id": question_pack.metadata.question_pack_id,
                    "question_pack_version": question_pack.metadata.question_pack_version,
                    "scoring_mode": EQUAL_SCORING_MODE,
                    "started_at": "2026-07-20T10:00:00+08:00",
                    "completed_at": "2026-07-20T10:05:00+08:00",
                    "candidate_count": len(targets),
                    "question_count": len(questions),
                    "status": "degraded",
                    "selection_mode": "regular",
                    "requested_candidate_ids": [target.candidate_id for target in targets],
                    "regular_candidate_ids": [target.candidate_id for target in targets],
                    "is_complete_regular_round": False,
                }
            )

            release_first_wave = threading.Event()
            active_calls = 0
            maximum_active_calls = 0
            call_lock = threading.Lock()
            calls: list[tuple[str, str]] = []
            observed_repair_statuses: list[str] = []
            observed_runtime_counts: list[tuple[int, int]] = []
            progress_events: list[dict[str, object]] = []
            initial_history_count = len(history_store.load_all())

            def repair_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                nonlocal active_calls, maximum_active_calls
                with call_lock:
                    calls.append((target.candidate_id, question.id))
                    active_calls += 1
                    maximum_active_calls = max(maximum_active_calls, active_calls)
                    if active_calls == 4 and not release_first_wave.is_set():
                        observed_repair_statuses.extend(
                            sorted(
                                str(entry["status"])
                                for entry in service.runtime_state["run_entries"]
                                if entry.get("phase") == "repair"
                            )
                        )
                        observed_runtime_counts.append(
                            (
                                int(service.runtime_state["active_evaluation_count"]),
                                int(service.runtime_state["queued_evaluation_count"]),
                            )
                        )
                        release_first_wave.set()
                release_first_wave.wait(timeout=2)
                with call_lock:
                    active_calls -= 1
                return ScanResult(
                    run_id=str(kwargs["run_id"]),
                    candidate_id=target.candidate_id,
                    model=target.model,
                    effort=target.effort,
                    phase=str(kwargs["phase"]),
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=int(kwargs["attempt_index"]),
                    started_at="2026-07-20T10:10:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            service.runner = repair_runner
            with patch.object(
                service,
                "build_state",
                wraps=service.build_state,
            ) as build_state:
                repaired = service.repair_timed_out_questions(
                    run_id=run_id,
                    candidate_ids=[target.candidate_id for target in targets],
                    progress_callback=progress_events.append,
                )

            expected_calls = {
                (candidate_id, question_id)
                for candidate_id, question_ids in timed_out_questions_by_candidate.items()
                for question_id in question_ids
            }
            self.assertEqual(set(calls), expected_calls)
            self.assertEqual(len(repaired), 7)
            self.assertEqual(maximum_active_calls, 4)
            self.assertEqual(
                observed_repair_statuses,
                ["running", "running", "running"],
            )
            self.assertEqual(observed_runtime_counts, [(4, 3)])
            self.assertTrue(progress_events)
            for event in progress_events:
                self.assertIn(
                    event["type"],
                    {
                        "timeout-repair.question.started",
                        "timeout-repair.question.finished",
                    },
                )
                state = event["state"]
                self.assertEqual(set(state), {"schema_version", "runtime"})  # type: ignore[arg-type]
                self.assertEqual(state["schema_version"], 1)  # type: ignore[index]
                self.assertEqual(
                    state["runtime"]["history_count"],  # type: ignore[index]
                    initial_history_count,
                )
            self.assertEqual(build_state.call_count, 0)
            self.assertEqual(history_store.load_run_metadata(run_id)["status"], "completed")  # type: ignore[index]
            self.assertIsNone(active_run_store.load())
            self.assertEqual(
                service.run_journal_store.load_events(run_id)[-1]["type"],
                "timeout-repair.completed",
            )
            completed_summary = service.run_journal_store.load_summary(run_id)
            self.assertEqual(completed_summary["status"], "completed")  # type: ignore[index]
            self.assertEqual(
                completed_summary["lifecycle_state"],  # type: ignore[index]
                "finalizing",
            )

    def test_repair_failed_questions_runs_hard_failures_in_parallel(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            config = config_store.load()
            candidates = [
                candidate
                for connection in config.model_ingress.connections
                for candidate in connection.model_candidates
            ]
            selected = candidates[:3]
            selected_ids = {candidate.id for candidate in selected}
            for candidate in candidates:
                candidate.enabled = candidate.id in selected_ids
            config.system.max_concurrent_targets = 3
            config_store.save(config)

            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
            )
            targets = service.scan_target_resolver.enabled_targets(config)
            question_pack = service.question_bank.load()
            questions = question_pack.enabled_questions
            failed_question = questions[1]
            run_id = "run-failure-batch"

            for target in targets:
                for attempt_index, question in enumerate(questions, start=1):
                    is_failed = question.id == failed_question.id
                    history_store.append(
                        ScanResult(
                            run_id=run_id,
                            candidate_id=target.candidate_id,
                            model=target.model,
                            effort=target.effort,
                            phase="scan",
                            question_id=question.id,
                            question_title=question.title,
                            grader_kind=question.grader.kind,
                            attempt_index=attempt_index,
                            started_at="2026-07-24T10:00:00+08:00",
                            elapsed_seconds=1.0,
                            source_mode="live",
                            answer_ok=not is_failed,
                            answer_preview="error" if is_failed else "ok",
                            input_tokens=None if is_failed else 100,
                            output_tokens=None if is_failed else 20,
                            reasoning_tokens=None if is_failed else 430,
                            error_message="codex exec failed" if is_failed else None,
                            final_status="error" if is_failed else "pass",
                        )
                    )

            history_store.save_run_metadata(
                {
                    "run_id": run_id,
                    "question_pack_id": question_pack.metadata.question_pack_id,
                    "question_pack_version": question_pack.metadata.question_pack_version,
                    "scoring_mode": EQUAL_SCORING_MODE,
                    "started_at": "2026-07-24T10:00:00+08:00",
                    "completed_at": "2026-07-24T10:05:00+08:00",
                    "candidate_count": len(targets),
                    "question_count": len(questions),
                    "status": "degraded",
                    "selection_mode": "regular",
                    "requested_candidate_ids": [target.candidate_id for target in targets],
                    "regular_candidate_ids": [target.candidate_id for target in targets],
                    "is_complete_regular_round": False,
                }
            )

            release = threading.Event()
            call_lock = threading.Lock()
            active_calls = 0
            maximum_active_calls = 0
            calls: list[tuple[str, str]] = []

            def repair_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                nonlocal active_calls, maximum_active_calls
                with call_lock:
                    calls.append((target.candidate_id, question.id))
                    active_calls += 1
                    maximum_active_calls = max(maximum_active_calls, active_calls)
                    if active_calls == 3:
                        release.set()
                release.wait(timeout=2)
                with call_lock:
                    active_calls -= 1
                return ScanResult(
                    run_id=str(kwargs["run_id"]),
                    candidate_id=target.candidate_id,
                    model=target.model,
                    effort=target.effort,
                    phase=str(kwargs["phase"]),
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=int(kwargs["attempt_index"]),
                    started_at="2026-07-24T10:10:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            service.runner = repair_runner
            with patch.object(
                service.execution_engine,
                "execute",
                wraps=service.execution_engine.execute,
            ) as execute:
                repaired = service.repair_failed_questions(
                    run_id=run_id,
                    candidate_ids=[target.candidate_id for target in targets],
                )

            execute.assert_called_once()
            self.assertFalse(
                execute.call_args.kwargs.get("stop_on_failure", False)
            )
            self.assertEqual(
                set(calls),
                {(target.candidate_id, failed_question.id) for target in targets},
            )
            self.assertEqual(len(repaired), 3)
            self.assertEqual(maximum_active_calls, 3)
            self.assertEqual(history_store.load_run_metadata(run_id)["status"], "completed")  # type: ignore[index]
            self.assertIsNone(active_run_store.load())
            self.assertEqual(
                service.run_journal_store.load_events(run_id)[-1]["type"],
                "repair.completed",
            )
            completed_summary = service.run_journal_store.load_summary(run_id)
            self.assertEqual(completed_summary["status"], "completed")  # type: ignore[index]
            self.assertEqual(
                completed_summary["lifecycle_state"],  # type: ignore[index]
                "finalizing",
            )

    def test_batch_repair_failure_records_durable_terminal_state(self) -> None:
        for repair_method, terminal_event in (
            ("repair_failed_questions", "repair.failed"),
            ("repair_timed_out_questions", "timeout-repair.failed"),
        ):
            with self.subTest(repair_method=repair_method), tempfile.TemporaryDirectory() as temp_dir:
                def failing_runner(*args, **kwargs):  # type: ignore[no-untyped-def]
                    raise RuntimeError("injected batch failure")

                service, history_store, run_id, candidate_id = _seed_repair_run(
                    temp_dir,
                    failing_runner,
                )
                metadata = history_store.load_run_metadata(run_id)
                metadata["scoring_mode"] = EQUAL_SCORING_MODE  # type: ignore[index]
                history_store.save_run_metadata(metadata)  # type: ignore[arg-type]

                with self.assertRaisesRegex(RuntimeError, "injected batch failure"):
                    getattr(service, repair_method)(
                        run_id=run_id,
                        candidate_ids=[candidate_id],
                    )

                self.assertIsNone(service.active_run_store.load())
                self.assertEqual(
                    service.run_journal_store.load_events(run_id)[-1]["type"],
                    terminal_event,
                )
                failed_summary = service.run_journal_store.load_summary(run_id)
                self.assertEqual(failed_summary["status"], "degraded")  # type: ignore[index]
                self.assertEqual(failed_summary["lifecycle_state"], "failed")  # type: ignore[index]
                self.assertEqual(failed_summary["last_error"], "injected batch failure")  # type: ignore[index]

    def test_candidate_repair_can_retain_and_commit_finalizing_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            def successful_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                return ScanResult(
                    run_id=str(kwargs["run_id"]),
                    candidate_id=target.candidate_id,
                    model=target.model,
                    effort=target.effort,
                    phase=str(kwargs["phase"]),
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=int(kwargs["attempt_index"]),
                    started_at="2026-07-28T10:10:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            service, _, run_id, candidate_id = _seed_repair_run(
                temp_dir,
                successful_runner,
            )

            with patch.object(
                service.repair_execution_application,
                "execute_candidate",
                wraps=service.repair_execution_application.execute_candidate,
            ) as execute_candidate:
                repaired = service.repair_failed_candidate(
                    run_id=run_id,
                    candidate_id=candidate_id,
                    retain_finalizing_state=True,
                )

            execute_candidate.assert_called_once()
            self.assertEqual(len(repaired), 1)
            active = service.active_run_store.load()
            self.assertIsNotNone(active)
            self.assertEqual(active["runtime"]["lifecycle_state"], "finalizing")  # type: ignore[index]
            self.assertIsNotNone(active["run_metadata"]["completed_at"])  # type: ignore[index]
            self.assertEqual(
                service.run_journal_store.load_summary(run_id)["lifecycle_state"],  # type: ignore[index]
                "finalizing",
            )

            projected = {"runtime": service.build_runtime_event()["runtime"]}
            terminal = service.complete_finalizing_snapshot(
                projected,
                exclusive_lock_held=True,
            )

            self.assertEqual(terminal["runtime"]["lifecycle_state"], "idle")  # type: ignore[index]
            self.assertIsNone(service.active_run_store.load())
            self.assertEqual(
                service.run_journal_store.load_summary(run_id)["lifecycle_state"],  # type: ignore[index]
                "idle",
            )

    def test_batch_repair_can_retain_finalizing_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            def successful_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                return ScanResult(
                    run_id=str(kwargs["run_id"]),
                    candidate_id=target.candidate_id,
                    model=target.model,
                    effort=target.effort,
                    phase=str(kwargs["phase"]),
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=int(kwargs["attempt_index"]),
                    started_at="2026-07-28T10:10:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=430,
                    final_status="pass",
                )

            service, history_store, run_id, candidate_id = _seed_repair_run(
                temp_dir,
                successful_runner,
            )
            metadata = history_store.load_run_metadata(run_id)
            metadata["scoring_mode"] = EQUAL_SCORING_MODE  # type: ignore[index]
            history_store.save_run_metadata(metadata)  # type: ignore[arg-type]

            with patch.object(
                service.repair_execution_application,
                "execute_batch",
                wraps=service.repair_execution_application.execute_batch,
            ) as execute_batch:
                repaired = service.repair_timed_out_questions(
                    run_id=run_id,
                    candidate_ids=[candidate_id],
                    retain_finalizing_state=True,
                )

            execute_batch.assert_called_once()
            self.assertEqual(len(repaired), 1)
            active = service.active_run_store.load()
            self.assertIsNotNone(active)
            self.assertEqual(active["runtime"]["lifecycle_state"], "finalizing")  # type: ignore[index]
            self.assertIsNone(active["runtime"]["lease_expires_at"])  # type: ignore[index]
            self.assertEqual(
                service.run_journal_store.load_summary(run_id)["lifecycle_state"],  # type: ignore[index]
                "finalizing",
            )

    def test_timeout_repair_filter_excludes_other_hard_failures(self) -> None:
        common = {
            "model": "gpt-test",
            "effort": "high",
            "started_at": "2026-07-20T10:00:00+08:00",
            "elapsed_seconds": 1.0,
            "source_mode": "live",
            "answer_ok": False,
            "answer_preview": "error",
            "input_tokens": None,
            "output_tokens": None,
            "reasoning_tokens": None,
        }

        self.assertTrue(
            is_timeout_result(
                ScanResult(**common, error_message="codex exec timed out after 300s")
            )
        )
        self.assertFalse(
            is_timeout_result(
                ScanResult(**common, flags=["timeout"])
            )
        )
        self.assertTrue(
            is_timeout_result(
                ScanResult(**common, final_status="timeout")
            )
        )
        self.assertFalse(
            is_timeout_result(
                ScanResult(**common, error_message="endpoint connection refused")
            )
        )

    def test_timeout_repair_stop_discards_inflight_result_and_keeps_round_repairable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_store = ConfigStore(Path(temp_dir) / "config.json")
            config = config_store.load()
            candidates = [
                candidate
                for connection in config.model_ingress.connections
                for candidate in connection.model_candidates
            ]
            selected = candidates[0]
            for candidate in candidates:
                candidate.enabled = candidate.id == selected.id
            config.system.max_concurrent_targets = 1
            config_store.save(config)

            history_store = HistoryStore(Path(temp_dir) / "history.jsonl")
            active_run_store = ActiveRunStore(Path(temp_dir) / "active_run.json")
            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
            )
            target = service.scan_target_resolver.enabled_targets(config)[0]
            question_pack = service.question_bank.load()
            questions = question_pack.enabled_questions
            timed_out_ids = {questions[0].id, questions[1].id}
            run_id = "run-timeout-stop"

            for attempt_index, question in enumerate(questions, start=1):
                is_timeout = question.id in timed_out_ids
                history_store.append(
                    ScanResult(
                        run_id=run_id,
                        candidate_id=target.candidate_id,
                        model=target.model,
                        effort=target.effort,
                        phase="scan",
                        question_id=question.id,
                        question_title=question.title,
                        grader_kind=question.grader.kind,
                        attempt_index=attempt_index,
                        started_at="2026-07-20T10:00:00+08:00",
                        elapsed_seconds=300.0 if is_timeout else 1.0,
                        source_mode="live",
                        answer_ok=not is_timeout,
                        answer_preview="timeout" if is_timeout else "ok",
                        input_tokens=None if is_timeout else 100,
                        output_tokens=None if is_timeout else 20,
                        reasoning_tokens=None if is_timeout else 430,
                        error_message=(
                            "codex exec timed out after 300s" if is_timeout else None
                        ),
                        flags=["timeout"] if is_timeout else [],
                        final_status="warn" if is_timeout else "pass",
                    )
                )
            history_store.save_run_metadata(
                {
                    "run_id": run_id,
                    "question_pack_id": question_pack.metadata.question_pack_id,
                    "question_pack_version": question_pack.metadata.question_pack_version,
                    "scoring_mode": EQUAL_SCORING_MODE,
                    "started_at": "2026-07-20T10:00:00+08:00",
                    "completed_at": "2026-07-20T10:05:00+08:00",
                    "candidate_count": 1,
                    "question_count": len(questions),
                    "status": "degraded",
                    "selection_mode": "regular",
                    "requested_candidate_ids": [target.candidate_id],
                    "regular_candidate_ids": [target.candidate_id],
                    "is_complete_regular_round": False,
                }
            )

            calls: list[str] = []

            def stopping_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                calls.append(question.id)
                active_run_store.request_control("stop")
                return ScanResult(
                    run_id=str(kwargs["run_id"]),
                    candidate_id=target.candidate_id,
                    model=target.model,
                    effort=target.effort,
                    phase=str(kwargs["phase"]),
                    question_id=question.id,
                    question_title=question.title,
                    grader_kind=question.grader.kind,
                    attempt_index=int(kwargs["attempt_index"]),
                    started_at="2026-07-20T10:10:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=False,
                    answer_preview="ERROR: codex exec failed",
                    input_tokens=None,
                    output_tokens=None,
                    reasoning_tokens=None,
                    error_message="codex exec failed",
                    final_status="warn",
                )

            service.runner = stopping_runner
            repaired = service.repair_timed_out_questions(
                run_id=run_id,
                candidate_ids=[target.candidate_id],
            )

            self.assertEqual(calls, [questions[0].id])
            self.assertEqual(repaired, [])
            self.assertEqual(service.last_control_action, "stop")
            self.assertEqual(history_store.load_run_metadata(run_id)["status"], "degraded")  # type: ignore[index]
            self.assertIsNone(active_run_store.load())
            self.assertEqual(
                service.run_journal_store.load_events(run_id)[-1]["type"],
                "timeout-repair.stopped",
            )
            stopped_summary = service.run_journal_store.load_summary(run_id)
            self.assertEqual(stopped_summary["status"], "degraded")  # type: ignore[index]
            self.assertEqual(stopped_summary["lifecycle_state"], "stopped")  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
