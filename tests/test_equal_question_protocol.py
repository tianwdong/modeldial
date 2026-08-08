from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scanner.active_run_store import ActiveRunStore
from scanner.config_store import ConfigStore
from scanner.history_store import HistoryStore
from scanner.models import ScanResult
from scanner.service import MonitorService


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class EqualQuestionProtocolTest(unittest.TestCase):
    def test_new_scan_runs_q1_to_q5_in_one_scan_phase(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_store = ConfigStore(root / "config.json")
            config = config_store.load()
            selected_candidate_id = None
            for connection in config.model_ingress.connections:
                for candidate in connection.model_candidates:
                    candidate.enabled = selected_candidate_id is None
                    if candidate.enabled:
                        selected_candidate_id = candidate.id
            config.system.max_concurrent_targets = 1
            config_store.save(config)
            history_store = HistoryStore(root / "history.jsonl")
            active_run_store = ActiveRunStore(root / "active_run.json")
            observed_active_runs: list[dict[str, object]] = []

            def runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
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
                    started_at="2026-07-21T14:00:00+08:00",
                    elapsed_seconds=0.1,
                    source_mode="mock",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=10,
                    output_tokens=5,
                    reasoning_tokens=1,
                    scorer_diagnostics={"semantic_passed": 10, "semantic_total": 10},
                    final_status="pass",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=active_run_store,
                runner=runner,
            )

            def capture_active_run(_event: dict[str, object]) -> None:
                payload = active_run_store.load()
                if payload is not None:
                    observed_active_runs.append(payload)

            results = service.run_enabled_targets(
                force_restart=True,
                progress_callback=capture_active_run,
            )

            self.assertEqual(len(results), 5)
            self.assertEqual(
                [item.question_id for item in results],
                [
                    "01_session_bundle_repair",
                    "02_code_counterexample_maxgap",
                    "03_ci_optimality_certificate",
                    "04_transaction_regression_design",
                    "05_cache_regression_test_design",
                ],
            )
            self.assertEqual({item.phase for item in results}, {"scan"})
            self.assertTrue(observed_active_runs)
            active_payload = observed_active_runs[-1]
            self.assertEqual(
                active_payload["planned_attempts_by_candidate"],
                {selected_candidate_id: 5},
            )
            self.assertNotIn("planned_quick_attempts", active_payload)
            self.assertNotIn("planned_review_attempts", active_payload)
            metadata = history_store.load_run_metadata(results[0].run_id)
            self.assertEqual(metadata["question_count"], 5)
            self.assertEqual(metadata["question_ids"], [item.question_id for item in results])
            self.assertFalse(any("review" in key or "quick" in key for key in metadata))

    def test_current_sources_do_not_expose_two_stage_scan_contract(self) -> None:
        service_source = (PROJECT_ROOT / "scanner" / "service.py").read_text(encoding="utf-8")
        bridge_source = (PROJECT_ROOT / "scanner" / "native_bridge.py").read_text(encoding="utf-8")
        native_client_source = (
            PROJECT_ROOT / "Sources" / "Model" / "NativeBridgeClient.swift"
        ).read_text(encoding="utf-8")
        selection_store_source = (
            PROJECT_ROOT / "Sources" / "Model" / "SelectionStore.swift"
        ).read_text(encoding="utf-8")

        for forbidden in (
            "review_only",
            "_run_manual_review",
            "_challenge_questions",
            "planned_review_attempts",
        ):
            self.assertNotIn(forbidden, service_source)
        self.assertNotIn("--review-only", bridge_source)
        self.assertNotIn("review_only", bridge_source)
        self.assertNotIn("reviewOnly", native_client_source)
        self.assertNotIn("startManualReview", selection_store_source)
        self.assertNotIn("reviewOnly", selection_store_source)

    def test_current_ui_has_no_two_stage_quick_or_review_copy(self) -> None:
        paths = [
            PROJECT_ROOT / "Sources" / "Model" / "GlanceState.swift",
            PROJECT_ROOT / "Sources" / "Views" / "CandidateEvidenceDetailView.swift",
            PROJECT_ROOT / "Sources" / "Views" / "ExpandedSelectionView.swift",
        ]
        for path in paths:
            source = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertNotIn("复核", source)
                self.assertNotIn("首轮快测", source)
                self.assertNotIn("快测阶段", source)
                self.assertNotIn("快测完成后", source)

        expanded_source = paths[-1].read_text(encoding="utf-8")
        self.assertIn("private var completeQuestionSetLabel", expanded_source)
        self.assertIn(
            'L10n.tr("同题包完整 %d 题", questionSemantics.count)',
            expanded_source,
        )
        self.assertNotIn("完整五题对比", expanded_source)


if __name__ == "__main__":
    unittest.main()
