from __future__ import annotations

from dataclasses import dataclass

from .legacy_scan_compat import SCAN_PHASE, normalize_phase
from .models import ScanResult
from .scoring import uses_equal_scoring


@dataclass(frozen=True)
class CandidateEvidence:
    candidate_id: str
    valid_run_id: str | None
    valid_at: str | None
    results: tuple[ScanResult, ...]
    current_results: tuple[ScanResult, ...]
    is_current_run_eligible: bool
    hard_failure_question_ids: tuple[str, ...]
    latest_attempt_run_id: str | None
    latest_attempt_at: str | None
    latest_attempt_status: str | None
    latest_attempt_error_category: str | None
    latest_attempt_error_summary: str | None
    is_current_pack_comparable: bool
    is_using_previous_valid_result: bool


def build_candidate_evidence(
    history_by_candidate: dict[str, list[ScanResult]],
    run_metadata_by_id: dict[str, dict[str, object]],
    *,
    current_run_id: str | None,
    current_pack_id: str,
    current_pack_version: str,
    required_question_count: int,
    required_question_ids: tuple[str, ...] | None = None,
) -> dict[str, CandidateEvidence]:
    return {
        candidate_id: _candidate_evidence(
            candidate_id,
            history,
            run_metadata_by_id,
            current_run_id=current_run_id,
            current_pack_id=current_pack_id,
            current_pack_version=current_pack_version,
            required_question_count=required_question_count,
            required_question_ids=required_question_ids,
        )
        for candidate_id, history in history_by_candidate.items()
    }


def _candidate_evidence(
    candidate_id: str,
    history: list[ScanResult],
    run_metadata_by_id: dict[str, dict[str, object]],
    *,
    current_run_id: str | None,
    current_pack_id: str,
    current_pack_version: str,
    required_question_count: int,
    required_question_ids: tuple[str, ...] | None,
) -> CandidateEvidence:
    batches: dict[str, list[ScanResult]] = {}
    run_order: list[str] = []
    for item in history:
        if item.run_id not in batches:
            run_order.append(item.run_id)
            batches[item.run_id] = []
        batches[item.run_id].append(item)

    latest_attempt_run_id = run_order[-1] if run_order else None
    latest_attempt = batches.get(latest_attempt_run_id or "", [])
    latest_effective_results = _deduplicate(latest_attempt)
    latest_metadata = run_metadata_by_id.get(latest_attempt_run_id or "", {})
    latest_attempt_status = (
        str(latest_metadata.get("status") or "legacy")
        if latest_attempt_run_id
        else None
    )
    current_results = batches.get(current_run_id or "", [])
    current_scan_results = _deduplicate(
        [item for item in current_results if normalize_phase(item.phase) == SCAN_PHASE]
    )
    current_by_question = {
        item.question_id: item for item in current_scan_results
    }
    required_ids = tuple(required_question_ids or ())
    missing_question_ids = tuple(
        question_id
        for question_id in required_ids
        if question_id not in current_by_question
    )
    error_question_ids = tuple(
        item.question_id for item in current_scan_results if item.error_message
    )
    hard_failure_question_ids = tuple(
        dict.fromkeys((*missing_question_ids, *error_question_ids))
    )
    current_run_metadata = run_metadata_by_id.get(current_run_id or "", {})
    current_run_pack_matches = _same_pack(
        current_run_id or "",
        current_run_metadata,
        current_run_id=current_run_id,
        current_pack_id=current_pack_id,
        current_pack_version=current_pack_version,
    )

    valid_run_id: str | None = None
    valid_results: list[ScanResult] = []
    valid_at: str | None = None
    for run_id in reversed(run_order):
        metadata = run_metadata_by_id.get(run_id, {})
        if not _same_pack(
            run_id,
            metadata,
            current_run_id=current_run_id,
            current_pack_id=current_pack_id,
            current_pack_version=current_pack_version,
        ):
            continue
        if str(metadata.get("status") or "legacy") not in {
            "completed",
            "degraded",
            "legacy",
        }:
            continue
        run_results = batches[run_id]
        scan_results = _deduplicate(
            [item for item in run_results if normalize_phase(item.phase) == SCAN_PHASE]
        )
        if len(scan_results) != required_question_count:
            continue
        if required_ids and {
            item.question_id for item in scan_results
        } != set(required_ids):
            continue
        if any(item.error_message for item in scan_results) and not _scored_execution_failures(
            metadata,
            scan_results,
        ):
            continue
        valid_run_id = run_id
        valid_results = scan_results
        valid_at = _result_time(metadata, run_results)
        break

    comparable = valid_run_id is not None
    return CandidateEvidence(
        candidate_id=candidate_id,
        valid_run_id=valid_run_id,
        valid_at=valid_at,
        results=tuple(valid_results),
        current_results=tuple(current_scan_results),
        is_current_run_eligible=(
            current_run_pack_matches
            and len(current_scan_results) == required_question_count
            and (
                not required_ids
                or set(current_by_question) == set(required_ids)
            )
            and not hard_failure_question_ids
        ),
        hard_failure_question_ids=hard_failure_question_ids,
        latest_attempt_run_id=latest_attempt_run_id,
        latest_attempt_at=_result_time(latest_metadata, latest_attempt),
        latest_attempt_status=latest_attempt_status,
        latest_attempt_error_category=_failure_category(latest_effective_results),
        latest_attempt_error_summary=_failure_summary(latest_effective_results),
        is_current_pack_comparable=comparable,
        is_using_previous_valid_result=(
            valid_run_id is not None
            and latest_attempt_run_id is not None
            and valid_run_id != latest_attempt_run_id
        ),
    )


def _scored_execution_failures(
    metadata: dict[str, object],
    results: list[ScanResult],
) -> bool:
    failures = [item for item in results if item.error_message]
    if not failures or not uses_equal_scoring(metadata):
        return False
    for item in failures:
        diagnostics = dict(item.scorer_diagnostics or {})
        if "semantic_passed" not in diagnostics or "semantic_total" not in diagnostics:
            return False
        try:
            score = int(diagnostics["semantic_passed"])
            total = int(diagnostics["semantic_total"])
        except (TypeError, ValueError):
            return False
        if total <= 0 or score < 0 or score > total:
            return False
    return True


def _same_pack(
    run_id: str,
    metadata: dict[str, object],
    *,
    current_run_id: str | None,
    current_pack_id: str,
    current_pack_version: str,
) -> bool:
    if current_pack_version and current_pack_version != "unknown":
        if str(metadata.get("question_pack_version") or "unknown") != current_pack_version:
            return False
        if current_pack_id and current_pack_id != "unknown":
            return str(metadata.get("question_pack_id") or "unknown") == current_pack_id
        return True
    return run_id == current_run_id


def _deduplicate(results: list[ScanResult]) -> list[ScanResult]:
    by_question: dict[str, ScanResult] = {}
    for item in results:
        by_question[item.question_id] = item
    return list(by_question.values())


def _result_time(
    metadata: dict[str, object],
    results: list[ScanResult],
) -> str | None:
    completed_at = metadata.get("completed_at")
    if completed_at:
        return str(completed_at)
    if not results:
        return None
    return results[-1].started_at


def _failure_summary(results: list[ScanResult]) -> str | None:
    if not results:
        return None
    timeout_result = next(
        (item for item in results if _is_hard_timeout(item.error_message)),
        None,
    )
    if timeout_result is not None:
        question = _question_label(timeout_result.question_id)
        return f"扫描 {question} 超时" if question else "扫描超时"
    if any(item.error_message for item in results):
        return "执行错误"
    if any(item.final_status in {"interrupted", "error"} for item in results):
        return "运行中断"
    return None


def _failure_category(results: list[ScanResult]) -> str | None:
    if not results:
        return None
    if any(_is_hard_timeout(item.error_message) for item in results):
        return "timeout"
    messages = " ".join(
        item.error_message for item in results if item.error_message
    ).lower()
    if any(token in messages for token in ("unauthorized", "authentication", "api key", "401")):
        return "authentication_failed"
    if any(token in messages for token in ("model not found", "unknown model", "404")):
        return "model_not_found"
    if any(token in messages for token in ("protocol", "invalid response", "decode")):
        return "protocol_mismatch"
    if messages.strip():
        return "execution_error"
    return None


def _is_hard_timeout(message: str | None) -> bool:
    normalized = (message or "").lower()
    return "timed out" in normalized or "timeout" in normalized


def _question_label(question_id: str) -> str:
    digits = "".join(char for char in question_id if char.isdigit())
    if not digits:
        return ""
    return f"Q{int(digits)}"
