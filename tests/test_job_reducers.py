from __future__ import annotations

from types import SimpleNamespace
import unittest

from scanner.job_reducers import RepairJobReducer, ScanJobReducer


def result(
    *,
    error_message: str | None = None,
    final_status: str = "pass",
    reasoning_tokens: int | None = 100,
    endpoint_error_category: str | None = None,
):
    return SimpleNamespace(
        error_message=error_message,
        final_status=final_status,
        reasoning_tokens=reasoning_tokens,
        flags=["hard_error"] if error_message else [],
        execution_trace=(
            {"endpoint_error_category": endpoint_error_category}
            if endpoint_error_category
            else {}
        ),
    )


class ScanJobReducerTests(unittest.TestCase):
    def test_reduces_entry_progress_health_and_active_job_timestamps(self) -> None:
        runtime: dict[str, object] = {
            "oldest_active_evaluation_started_at": None,
            "current_target": None,
            "last_error": None,
        }
        entries = [
            {
                "candidate_id": "a",
                "status": "pending",
                "error_message": None,
            },
            {
                "candidate_id": "b",
                "status": "pending",
                "error_message": None,
            },
        ]
        buckets = {"a": [result(error_message="old failure", final_status="fail")]}
        completed_steps = {("a", "scan", "q1")}
        reducer = ScanJobReducer(
            runtime_state=runtime,
            run_entries=entries,
            candidate_ids=["a", "b"],
            attempts_per_target=2,
            result_buckets=buckets,  # type: ignore[arg-type]
            completed_steps=completed_steps,
            circuit_breaker_threshold=2,
        )

        reducer.job_started(
            candidate_id="a",
            job_key=("a", "scan", "q2"),
            started_at="2026-07-29T10:00:00+08:00",
            current_target="A · 扫描 2/2",
        )
        self.assertEqual(entries[0]["status"], "running")
        self.assertEqual(
            runtime["oldest_active_evaluation_started_at"],
            "2026-07-29T10:00:00+08:00",
        )
        self.assertEqual(runtime["current_target"], "A · 扫描 2/2")

        reducer.job_stopped(
            candidate_id="a",
            job_key=("a", "scan", "q2"),
        )
        reducer.job_finished(
            candidate_id="a",
            job_key=("a", "scan", "q2"),
            result=result(),  # type: ignore[arg-type]
        )

        self.assertEqual(entries[0]["status"], "done")
        self.assertEqual(entries[0]["attempts_completed"], 2)
        self.assertEqual(entries[0]["final_status"], "pass")
        self.assertIsNone(runtime["oldest_active_evaluation_started_at"])
        self.assertEqual(reducer.hard_error_count, 1)
        self.assertFalse(reducer.circuit_open)
        self.assertIn(("a", "scan", "q2"), completed_steps)

        reducer.job_finished(
            candidate_id="b",
            job_key=("b", "scan", "q1"),
            result=result(error_message="failure 1", final_status="fail"),  # type: ignore[arg-type]
        )
        reducer.job_finished(
            candidate_id="b",
            job_key=("b", "scan", "q2"),
            result=result(error_message="failure 2", final_status="fail"),  # type: ignore[arg-type]
        )

        self.assertTrue(reducer.circuit_open)
        self.assertFalse(reducer.can_start())
        self.assertEqual(reducer.hard_error_count, 3)
        self.assertIn("扫描已熔断", str(runtime["last_error"]))
        self.assertEqual(entries[1]["status"], "done")

    def test_transient_endpoint_errors_do_not_open_global_circuit(self) -> None:
        runtime: dict[str, object] = {"last_error": None}
        entries = [{"candidate_id": "a", "status": "pending"}]
        reducer = ScanJobReducer(
            runtime_state=runtime,
            run_entries=entries,
            candidate_ids=["a"],
            attempts_per_target=3,
            result_buckets={},
            completed_steps=set(),
            circuit_breaker_threshold=3,
        )

        for index, category in enumerate(
            ["server_error", "network_error", "rate_limited"],
            start=1,
        ):
            reducer.job_finished(
                candidate_id="a",
                job_key=("a", "scan", f"q{index}"),
                result=result(
                    error_message=f"endpoint request failed: {category}",
                    final_status="warn",
                    reasoning_tokens=None,
                    endpoint_error_category=category,
                ),  # type: ignore[arg-type]
            )

        self.assertEqual(reducer.hard_error_count, 3)
        self.assertEqual(reducer.consecutive_hard_errors, 0)
        self.assertFalse(reducer.circuit_open)
        self.assertTrue(reducer.can_start())
        self.assertIsNone(runtime["last_error"])

    def test_failed_execution_marks_unfinished_entries_failed(self) -> None:
        runtime: dict[str, object] = {}
        entries = [{"candidate_id": "a", "status": "pending"}]
        reducer = ScanJobReducer(
            runtime_state=runtime,
            run_entries=entries,
            candidate_ids=["a"],
            attempts_per_target=1,
            result_buckets={},
            completed_steps=set(),
            circuit_breaker_threshold=3,
        )

        reducer.job_failed(candidate_id="a")

        self.assertFalse(reducer.can_start())
        self.assertEqual(entries[0]["status"], "failed")


class RepairJobReducerTests(unittest.TestCase):
    def test_candidate_repair_tracks_retryable_questions_and_latest_entry(self) -> None:
        runtime: dict[str, object] = {}
        entry = {"candidate_id": "a", "status": "pending"}
        latest = {
            "q2": result(error_message="old failure", final_status="fail"),
            "q3": result(),
        }
        reducer = RepairJobReducer(
            runtime_state=runtime,
            run_entries=[entry],
            question_ids_by_candidate={"a": ["q1", "q2", "q3"]},
            latest_by_candidate={"a": latest},  # type: ignore[arg-type]
        )
        reducer.initialize_entries(initial_status="running")

        self.assertEqual(
            reducer.retryable_question_ids("a"),
            ["q1", "q2"],
        )
        reducer.candidate_job_started(
            current_target="A · 重试 q2",
        )
        reducer.candidate_job_finished(
            candidate_id="a",
            question_id="q2",
            result=result(final_status="recovered"),  # type: ignore[arg-type]
        )

        self.assertEqual(reducer.retryable_question_ids("a"), ["q1"])
        self.assertEqual(entry["attempts_completed"], 1)
        self.assertEqual(entry["final_status"], "recovered")
        self.assertEqual(runtime["current_phase"], "repair")
        self.assertEqual(runtime["current_target"], "A · 重试 q2")

    def test_batch_repair_reduces_status_and_completed_step_scope(self) -> None:
        runtime: dict[str, object] = {}
        entries = [
            {"candidate_id": "a", "status": "pending"},
            {"candidate_id": "b", "status": "pending"},
        ]
        reducer = RepairJobReducer(
            runtime_state=runtime,
            run_entries=entries,
            question_ids_by_candidate={"a": ["q1"], "b": ["q2"]},
        )
        reducer.initialize_entries(initial_status="pending")

        reducer.batch_job_started(
            candidate_id="a",
            current_target="重试失败题",
        )
        self.assertEqual(entries[0]["status"], "running")
        reducer.batch_job_stopped(candidate_id="a")
        reducer.batch_job_finished(
            candidate_id="a",
            question_id="q1",
            result=result(error_message="still failed", final_status="fail"),  # type: ignore[arg-type]
        )

        self.assertEqual(entries[0]["status"], "failed")
        self.assertEqual(entries[0]["attempts_completed"], 1)
        self.assertEqual(entries[1]["status"], "pending")
        self.assertEqual(reducer.completed_step_count, 1)
        self.assertEqual(
            reducer.pending_question_ids_by_candidate(),
            {"a": [], "b": ["q2"]},
        )
        self.assertEqual(runtime["current_target"], "重试失败题")


if __name__ == "__main__":
    unittest.main()
