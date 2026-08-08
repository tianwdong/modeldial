from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scanner.active_run_store import ActiveRunStore
from scanner.config_store import ConfigStore
from scanner.history_store import HistoryStore
from scanner.models import ScanResult
from scanner.run_journal import RunJournalStore
from scanner.service import MonitorService
from tests.question_pack_fixtures import DEFAULT_QUESTION_COUNT


class RunJournalStoreTest(unittest.TestCase):
    def test_rejects_run_ids_that_escape_the_journal_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = RunJournalStore(Path(temp_dir) / "runs")

            for run_id in ("..", "../outside", "folder/run", "folder\\run"):
                with self.subTest(run_id=run_id), self.assertRaisesRegex(
                    ValueError,
                    "invalid run_id",
                ):
                    store.append_event(run_id, "run.started")

    def test_appends_ordered_events_and_writes_atomic_summary_and_host_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = RunJournalStore(Path(temp_dir) / "runs")

            store.append_event(
                "run-1",
                "run.started",
                {"evaluation_profile_id": "quick"},
                occurred_at="2026-07-23T10:00:00+08:00",
            )
            store.append_event(
                "run-1",
                "evaluation.started",
                {"candidate_id": "candidate-1", "question_id": "q5"},
                occurred_at="2026-07-23T10:00:01+08:00",
            )
            store.save_summary(
                "run-1",
                {
                    "status": "running",
                    "progress_completed": 0,
                    "progress_total": 1,
                },
            )

            run_dir = Path(temp_dir) / "runs" / "run-1"
            events = [
                json.loads(line)
                for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            host_log = (run_dir / "host.log").read_text(encoding="utf-8")

            self.assertEqual([event["sequence"] for event in events], [1, 2])
            self.assertEqual([event["type"] for event in events], ["run.started", "evaluation.started"])
            self.assertEqual(events[1]["data"]["question_id"], "q5")
            self.assertEqual(summary["schema_version"], 1)
            self.assertEqual(summary["run_id"], "run-1")
            self.assertEqual(summary["status"], "running")
            self.assertIn("run.started", host_log)
            self.assertIn("evaluation.started", host_log)
            self.assertEqual(list(run_dir.glob("*.tmp")), [])

    def test_append_repairs_a_truncated_event_tail_and_keeps_sequences_contiguous(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "runs"
            store = RunJournalStore(root)
            store.append_event("run-1", "run.started")
            events_path = root / "run-1" / "events.jsonl"
            with events_path.open("a", encoding="utf-8") as handle:
                handle.write('{"schema_version":1,"run_id":"run-1"')

            restarted_store = RunJournalStore(root)
            restarted_store.append_event("run-1", "evaluation.started")

            events = restarted_store.load_events("run-1")
            self.assertEqual([event["sequence"] for event in events], [1, 2])
            self.assertEqual(
                [event["type"] for event in events],
                ["run.started", "evaluation.started"],
            )

    def test_host_log_failure_does_not_rollback_committed_event_or_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = RunJournalStore(Path(temp_dir) / "runs")
            original_append_line = store._append_line

            def fail_host_log(path: Path, line: str) -> None:
                if path.name == "host.log":
                    raise OSError("host log unavailable")
                original_append_line(path, line)

            with patch.object(
                store,
                "_append_line",
                side_effect=fail_host_log,
            ):
                first = store.append_event("run-1", "run.started")

            second = store.append_event("run-1", "run.completed")
            events = store.load_events("run-1")

            self.assertEqual(first["sequence"], 1)
            self.assertEqual(second["sequence"], 2)
            self.assertEqual(
                [(event["sequence"], event["type"]) for event in events],
                [(1, "run.started"), (2, "run.completed")],
            )

    def test_events_failure_does_not_advance_sequence_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = RunJournalStore(Path(temp_dir) / "runs")
            original_append_line = store._append_line

            def fail_events(path: Path, line: str) -> None:
                if path.name == "events.jsonl":
                    raise OSError("events unavailable")
                original_append_line(path, line)

            with patch.object(
                store,
                "_append_line",
                side_effect=fail_events,
            ), self.assertRaisesRegex(OSError, "events unavailable"):
                store.append_event("run-1", "run.started")

            committed = store.append_event("run-1", "run.started")
            events = store.load_events("run-1")

            self.assertEqual(committed["sequence"], 1)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["sequence"], 1)


class RunJournalServiceIntegrationTest(unittest.TestCase):
    @staticmethod
    def _enable_one_candidate(config) -> str:  # type: ignore[no-untyped-def]
        enabled_candidate_id = ""
        for connection in config.model_ingress.connections:
            for candidate in connection.model_candidates:
                candidate.enabled = not enabled_candidate_id
                if candidate.enabled:
                    enabled_candidate_id = candidate.id
        return enabled_candidate_id

    def test_successful_quick_scan_persists_replayable_cell_events_and_final_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_store = ConfigStore(root / "config.json")
            config = config_store.load()
            enabled_candidate_id = self._enable_one_candidate(config)
            config_store.save(config)

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
                    started_at="2026-07-23T10:00:00+08:00",
                    elapsed_seconds=1.0,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=10,
                    output_tokens=5,
                    reasoning_tokens=1,
                    final_status="pass",
                    scorer_diagnostics={"semantic_score": 20, "semantic_total": 20},
                )

            history_store = HistoryStore(root / "history.jsonl")
            service = MonitorService(
                config_store=config_store,
                history_store=history_store,
                active_run_store=ActiveRunStore(root / "active_run.json"),
                runner=successful_runner,
            )
            journal_store = service.run_journal_store

            self.assertEqual(journal_store.root, root / "runs")

            results = service.run_enabled_targets(
                force_restart=True,
                evaluation_profile_id="quick",
            )

            run_id = results[0].run_id
            events = journal_store.load_events(run_id)
            summary = journal_store.load_summary(run_id)
            evaluation_events = [
                event for event in events if event["type"].startswith("evaluation.")
            ]

            event_types = [event["type"] for event in events]
            self.assertEqual(event_types[0], "run.started")
            self.assertEqual(event_types[-1], "run.completed")
            execution_policy = events[0]["data"]["execution_policy"]
            self.assertEqual(execution_policy["mode"], "app_rules_v1")
            self.assertEqual(execution_policy["max_attempts_per_question"], 2)
            self.assertFalse(execution_policy["selective_score_retry"])
            self.assertEqual(
                execution_policy["rules"]["missing_usage"]["action"],
                "retry",
            )
            self.assertEqual(event_types.count("evaluation.started"), DEFAULT_QUESTION_COUNT)
            self.assertEqual(event_types.count("evaluation.finished"), DEFAULT_QUESTION_COUNT)
            self.assertEqual(
                [event["data"]["candidate_id"] for event in evaluation_events],
                [enabled_candidate_id] * (DEFAULT_QUESTION_COUNT * 2),
            )
            self.assertEqual(
                {
                    event["data"]["question_id"]
                    for event in evaluation_events
                    if event["type"] == "evaluation.finished"
                },
                {result.question_id for result in results},
            )
            self.assertEqual(summary["status"], "completed")
            self.assertEqual(summary["progress_completed"], DEFAULT_QUESTION_COUNT)
            self.assertEqual(summary["progress_total"], DEFAULT_QUESTION_COUNT)
            self.assertEqual(summary["run_metadata"]["evaluation_profile_id"], "quick")

    def test_recovery_distinguishes_authoritative_event_failure_from_host_mirror_failure(self) -> None:
        for failed_file in ("host.log", "events.jsonl"):
            with self.subTest(failed_file=failed_file), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                active_run_store = ActiveRunStore(root / "active_run.json")
                service = MonitorService(
                    config_store=ConfigStore(root / "config.json"),
                    history_store=HistoryStore(root / "history.jsonl"),
                    active_run_store=active_run_store,
                )
                run_id = f"run-recovery-{failed_file.replace('.', '-')}"
                completed_at = "2026-07-28T20:00:00+08:00"
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
                        "last_error": None,
                        "updated_at": completed_at,
                        "run_metadata": run_metadata,
                    },
                )
                original_append_line = service.run_journal_store._append_line

                def fail_selected_mirror(path: Path, line: str) -> None:
                    if path.name == failed_file:
                        raise OSError(f"{failed_file} unavailable")
                    original_append_line(path, line)

                with patch.object(
                    service.run_journal_store,
                    "_append_line",
                    side_effect=fail_selected_mirror,
                ):
                    recovery = service.recover_orphaned_finalizing_run(
                        exclusive_lock_held=True
                    )

                events = service.run_journal_store.load_events(run_id)
                summary = service.run_journal_store.load_summary(run_id)
                if failed_file == "host.log":
                    self.assertEqual(recovery["status"], "recovered")
                    self.assertTrue(recovery["recovered"])
                    self.assertIsNone(active_run_store.load())
                    self.assertEqual(summary["lifecycle_state"], "idle")  # type: ignore[index]
                    self.assertEqual(events[-1]["type"], "run.finalization_recovered")
                    self.assertEqual(
                        [event["type"] for event in events].count(
                            "run.finalization_recovered"
                        ),
                        1,
                    )
                else:
                    self.assertEqual(recovery["status"], "incomplete")
                    self.assertFalse(recovery["recovered"])
                    active_run = active_run_store.load()
                    self.assertIsNotNone(active_run)
                    self.assertEqual(
                        active_run["runtime"]["lifecycle_state"],
                        "finalizing",
                    )
                    self.assertEqual(summary["lifecycle_state"], "finalizing")  # type: ignore[index]
                    self.assertNotIn(
                        "run.finalization_recovered",
                        [event["type"] for event in events],
                    )

    def test_paused_scan_records_control_event_and_resume_continues_same_journal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_store = ConfigStore(root / "config.json")
            config = config_store.load()
            self._enable_one_candidate(config)
            config_store.save(config)
            active_run_store = ActiveRunStore(root / "active_run.json")
            journal_store = RunJournalStore(root / "runs")
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
                    started_at="2026-07-23T10:00:00+08:00",
                    elapsed_seconds=0.1,
                    source_mode="live",
                    answer_ok=False,
                    answer_preview="interrupted",
                    input_tokens=None,
                    output_tokens=None,
                    reasoning_tokens=None,
                    error_message="interrupted",
                    final_status="warn",
                )

            service = MonitorService(
                config_store=config_store,
                history_store=HistoryStore(root / "history.jsonl"),
                active_run_store=active_run_store,
                run_journal_store=journal_store,
                runner=interrupted_runner,
            )
            service_holder["service"] = service

            self.assertEqual(
                service.run_enabled_targets(
                    force_restart=True,
                    evaluation_profile_id="quick",
                ),
                [],
            )
            paused_run_id = str(active_run_store.load()["run_id"])  # type: ignore[index]
            self.assertEqual(
                [event["type"] for event in journal_store.load_events(paused_run_id)],
                ["run.started", "evaluation.started", "run.paused"],
            )
            self.assertEqual(journal_store.load_summary(paused_run_id)["status"], "paused")  # type: ignore[index]

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
                    started_at="2026-07-23T10:00:01+08:00",
                    elapsed_seconds=0.1,
                    source_mode="live",
                    answer_ok=True,
                    answer_preview="ok",
                    input_tokens=10,
                    output_tokens=5,
                    reasoning_tokens=1,
                    final_status="pass",
                    scorer_diagnostics={"semantic_score": 20, "semantic_total": 20},
                )

            service.runner = successful_runner
            resumed = service.run_enabled_targets(evaluation_profile_id="quick")

            self.assertEqual(resumed[0].run_id, paused_run_id)
            resumed_event_types = [
                event["type"] for event in journal_store.load_events(paused_run_id)
            ]
            self.assertEqual(
                resumed_event_types[:4],
                ["run.started", "evaluation.started", "run.paused", "run.resumed"],
            )
            self.assertEqual(resumed_event_types[-1], "run.completed")
            self.assertEqual(
                resumed_event_types.count("evaluation.started"),
                DEFAULT_QUESTION_COUNT + 1,
            )
            self.assertEqual(
                resumed_event_types.count("evaluation.finished"),
                DEFAULT_QUESTION_COUNT,
            )
            self.assertEqual(journal_store.load_summary(paused_run_id)["status"], "completed")  # type: ignore[index]

    def test_failed_scan_records_terminal_error_without_losing_started_cell(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_store = ConfigStore(root / "config.json")
            config = config_store.load()
            self._enable_one_candidate(config)
            config_store.save(config)
            journal_store = RunJournalStore(root / "runs")

            def failed_runner(target, question, use_mock_results, **kwargs):  # type: ignore[no-untyped-def]
                raise RuntimeError("runner failed")

            service = MonitorService(
                config_store=config_store,
                history_store=HistoryStore(root / "history.jsonl"),
                active_run_store=ActiveRunStore(root / "active_run.json"),
                run_journal_store=journal_store,
                runner=failed_runner,
            )

            with self.assertRaisesRegex(RuntimeError, "runner failed"):
                service.run_enabled_targets(
                    force_restart=True,
                    evaluation_profile_id="quick",
                )

            run_id = next(path.name for path in (root / "runs").iterdir())
            events = journal_store.load_events(run_id)
            summary = journal_store.load_summary(run_id)

            self.assertEqual(
                [event["type"] for event in events],
                ["run.started", "evaluation.started", "run.failed"],
            )
            self.assertEqual(events[-1]["data"]["error_message"], "runner failed")
            self.assertEqual(summary["status"], "failed")  # type: ignore[index]
            self.assertEqual(summary["last_error"], "runner failed")  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
