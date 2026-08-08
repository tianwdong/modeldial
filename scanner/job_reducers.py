from __future__ import annotations

from .legacy_scan_compat import SCAN_PHASE
from .models import ScanResult
from .rules import is_transient_execution_error


class ScanJobReducer:
    """Pure in-memory scan entry, progress, and circuit reduction."""

    def __init__(
        self,
        *,
        runtime_state: dict[str, object],
        run_entries: list[dict[str, object]],
        candidate_ids: list[str],
        attempts_per_target: int,
        result_buckets: dict[str, list[ScanResult]],
        completed_steps: set[tuple[str, str, str]],
        circuit_breaker_threshold: int,
    ) -> None:
        self.runtime_state = runtime_state
        self.entry_by_candidate = {
            str(entry["candidate_id"]): entry for entry in run_entries
        }
        self.active_jobs_by_candidate = {
            candidate_id: 0 for candidate_id in candidate_ids
        }
        self.active_job_started_at: dict[tuple[str, str, str], str] = {}
        self.attempts_per_target = attempts_per_target
        self.result_buckets = result_buckets
        self.completed_steps = completed_steps
        self.circuit_breaker_threshold = circuit_breaker_threshold
        existing_results = [
            item for bucket in result_buckets.values() for item in bucket
        ]
        self.hard_error_count = sum(
            1 for item in existing_results if item.error_message
        )
        self.consecutive_hard_errors = 0
        self.circuit_open = False
        self.execution_failed = False

    def can_start(self) -> bool:
        return not self.execution_failed and not self.circuit_open

    def refresh_entry(self, candidate_id: str) -> None:
        entry = self.entry_by_candidate[candidate_id]
        candidate_results = list(self.result_buckets.get(candidate_id, []))
        done = len(candidate_results) >= self.attempts_per_target
        entry["phase"] = SCAN_PHASE
        entry["attempts_completed"] = len(candidate_results)
        entry["attempts_per_target"] = self.attempts_per_target
        if candidate_results:
            latest = candidate_results[-1]
            entry.update(
                {
                    "final_status": latest.final_status,
                    "reasoning_tokens": latest.reasoning_tokens,
                    "flags": list(latest.flags),
                    "error_message": latest.error_message,
                }
            )
        if done:
            entry["status"] = "done"
        elif self.active_jobs_by_candidate[candidate_id] > 0:
            entry["status"] = "running"
        elif self.circuit_open:
            entry["status"] = (
                "failed" if entry.get("error_message") else "interrupted"
            )
        elif self.execution_failed:
            entry["status"] = "failed"
        else:
            entry["status"] = "pending"

    def job_started(
        self,
        *,
        candidate_id: str,
        job_key: tuple[str, str, str],
        started_at: str,
        current_target: str,
    ) -> None:
        self.active_jobs_by_candidate[candidate_id] += 1
        self.active_job_started_at[job_key] = started_at
        self._refresh_oldest_active_started_at()
        self.refresh_entry(candidate_id)
        self.runtime_state["current_target"] = current_target

    def job_stopped(
        self,
        *,
        candidate_id: str,
        job_key: tuple[str, str, str],
    ) -> None:
        self.active_jobs_by_candidate[candidate_id] = max(
            0,
            self.active_jobs_by_candidate[candidate_id] - 1,
        )
        self.active_job_started_at.pop(job_key, None)
        self._refresh_oldest_active_started_at()

    def job_failed(self, *, candidate_id: str) -> None:
        self.execution_failed = True
        self.refresh_entry(candidate_id)

    def job_discarded(self, *, candidate_id: str) -> None:
        self.refresh_entry(candidate_id)

    def job_finished(
        self,
        *,
        candidate_id: str,
        job_key: tuple[str, str, str],
        result: ScanResult,
    ) -> None:
        phase_results = list(self.result_buckets.get(candidate_id, []))
        phase_results.append(result)
        self.result_buckets[candidate_id] = phase_results
        self.completed_steps.add(job_key)
        self._record_run_health(result)
        if self.circuit_open:
            self.runtime_state["last_error"] = (
                f"连续 {self.circuit_breaker_threshold} 个硬执行错误，扫描已熔断"
            )
        self.refresh_entry(candidate_id)

    def _record_run_health(self, result: ScanResult) -> None:
        if result.error_message:
            self.hard_error_count += 1
        if result.error_message and not is_transient_execution_error(result):
            self.consecutive_hard_errors += 1
        else:
            self.consecutive_hard_errors = 0
        if self.consecutive_hard_errors >= self.circuit_breaker_threshold:
            self.circuit_open = True

    def _refresh_oldest_active_started_at(self) -> None:
        self.runtime_state["oldest_active_evaluation_started_at"] = min(
            self.active_job_started_at.values(),
            default=None,
        )


class RepairJobReducer:
    """Pure in-memory candidate and batch repair entry reduction."""

    def __init__(
        self,
        *,
        runtime_state: dict[str, object],
        run_entries: list[dict[str, object]],
        question_ids_by_candidate: dict[str, list[str]],
        latest_by_candidate: dict[str, dict[str, ScanResult]] | None = None,
    ) -> None:
        self.runtime_state = runtime_state
        self.entry_by_candidate = {
            str(entry["candidate_id"]): entry for entry in run_entries
        }
        self.question_ids_by_candidate = {
            candidate_id: list(question_ids)
            for candidate_id, question_ids in question_ids_by_candidate.items()
        }
        supplied_latest = latest_by_candidate or {}
        self.latest_by_candidate = {
            candidate_id: supplied_latest.get(candidate_id, {})
            for candidate_id in question_ids_by_candidate
        }
        self.completed_results_by_candidate: dict[str, list[ScanResult]] = {
            candidate_id: [] for candidate_id in question_ids_by_candidate
        }
        self.active_jobs_by_candidate = {
            candidate_id: 0 for candidate_id in question_ids_by_candidate
        }
        self.completed_step_keys: set[tuple[str, str]] = set()

    @property
    def completed_step_count(self) -> int:
        return len(self.completed_step_keys)

    def initialize_entries(self, *, initial_status: str) -> None:
        for candidate_id, question_ids in self.question_ids_by_candidate.items():
            self.entry_by_candidate[candidate_id].update(
                {
                    "status": initial_status,
                    "phase": "repair",
                    "attempts_completed": 0,
                    "attempts_per_target": len(question_ids),
                    "final_status": None,
                    "reasoning_tokens": None,
                    "flags": [],
                    "error_message": None,
                }
            )

    def retryable_question_ids(self, candidate_id: str) -> list[str]:
        latest_by_question = self.latest_by_candidate[candidate_id]
        return [
            question_id
            for question_id in self.question_ids_by_candidate[candidate_id]
            if question_id not in latest_by_question
            or latest_by_question[question_id].error_message is not None
        ]

    def pending_question_ids_by_candidate(self) -> dict[str, list[str]]:
        return {
            candidate_id: [
                question_id
                for question_id in question_ids
                if (candidate_id, question_id) not in self.completed_step_keys
            ]
            for candidate_id, question_ids in self.question_ids_by_candidate.items()
        }

    def candidate_job_started(self, *, current_target: str) -> None:
        self.runtime_state["current_phase"] = "repair"
        self.runtime_state["current_target"] = current_target

    def candidate_job_finished(
        self,
        *,
        candidate_id: str,
        question_id: str,
        result: ScanResult,
    ) -> None:
        self.latest_by_candidate[candidate_id][question_id] = result
        completed = self.completed_results_by_candidate[candidate_id]
        completed.append(result)
        self.entry_by_candidate[candidate_id].update(
            {
                "attempts_completed": len(completed),
                "final_status": result.final_status,
                "reasoning_tokens": result.reasoning_tokens,
                "flags": list(result.flags),
                "error_message": result.error_message,
            }
        )

    def batch_job_started(
        self,
        *,
        candidate_id: str,
        current_target: str,
    ) -> None:
        self.active_jobs_by_candidate[candidate_id] += 1
        self._refresh_batch_entry(candidate_id)
        self.runtime_state["current_target"] = current_target

    def batch_job_stopped(self, *, candidate_id: str) -> None:
        self.active_jobs_by_candidate[candidate_id] = max(
            0,
            self.active_jobs_by_candidate[candidate_id] - 1,
        )

    def batch_job_failed(self, *, candidate_id: str) -> None:
        self._refresh_batch_entry(candidate_id)

    def batch_job_discarded(self, *, candidate_id: str) -> None:
        self._refresh_batch_entry(candidate_id)

    def batch_job_finished(
        self,
        *,
        candidate_id: str,
        question_id: str,
        result: ScanResult,
    ) -> None:
        self.completed_results_by_candidate[candidate_id].append(result)
        self.completed_step_keys.add((candidate_id, question_id))
        self._refresh_batch_entry(candidate_id)

    def _refresh_batch_entry(self, candidate_id: str) -> None:
        entry = self.entry_by_candidate[candidate_id]
        completed = self.completed_results_by_candidate[candidate_id]
        total = len(self.question_ids_by_candidate[candidate_id])
        if completed:
            latest = completed[-1]
            entry.update(
                {
                    "final_status": latest.final_status,
                    "reasoning_tokens": latest.reasoning_tokens,
                    "flags": list(latest.flags),
                    "error_message": latest.error_message,
                }
            )
        entry["attempts_completed"] = len(completed)
        if len(completed) >= total:
            entry["status"] = (
                "failed" if any(item.error_message for item in completed) else "done"
            )
        elif self.active_jobs_by_candidate[candidate_id] > 0:
            entry["status"] = "running"
        else:
            entry["status"] = "pending"
