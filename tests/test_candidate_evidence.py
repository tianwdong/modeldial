from __future__ import annotations

import unittest

from scanner.candidate_evidence import build_candidate_evidence
from scanner.models import ScanResult


class CandidateEvidenceTest(unittest.TestCase):
    def test_current_failed_attempt_is_kept_separate_from_previous_valid_score(self) -> None:
        candidate_id = "codex-local-default:gpt-5.4:xhigh"
        old_results = [
            _result(candidate_id, "run-old", question_number, answer_ok=True)
            for question_number in range(1, 6)
        ]
        current_results = [
            _result(
                candidate_id,
                "run-current",
                question_number,
                answer_ok=question_number != 2,
                error_message=(
                    "codex exec timed out after 300s"
                    if question_number == 2
                    else None
                ),
            )
            for question_number in range(1, 6)
        ]

        evidence = build_candidate_evidence(
            {candidate_id: old_results + current_results},
            {
                "run-old": {
                    "status": "completed",
                    "question_pack_id": "coding-fast",
                    "question_pack_version": "coding-fast-v1.4",
                },
                "run-current": {
                    "status": "degraded",
                    "question_pack_id": "coding-fast",
                    "question_pack_version": "coding-fast-v1.4",
                },
            },
            current_run_id="run-current",
            current_pack_id="coding-fast",
            current_pack_version="coding-fast-v1.4",
            required_question_count=5,
        )[candidate_id]

        self.assertEqual(evidence.valid_run_id, "run-old")
        self.assertEqual(
            [item.question_id for item in evidence.current_results],
            [
                "01_question",
                "02_question",
                "03_question",
                "04_question",
                "05_question",
            ],
        )
        self.assertFalse(evidence.is_current_run_eligible)
        self.assertEqual(evidence.hard_failure_question_ids, ("02_question",))
        self.assertTrue(evidence.is_using_previous_valid_result)

    def test_failure_summary_identifies_question_timeout(self) -> None:
        candidate_id = "codex-local-default:gpt-5.4:xhigh"
        result = ScanResult(
            candidate_id=candidate_id,
            run_id="run-timeout",
            phase="scan",
            model="gpt-5.4",
            effort="xhigh",
            question_id="02_code_counterexample_maxgap",
            question_title="Counterexample Max Gap",
            grader_kind="json_exact",
            attempt_index=2,
            started_at="2026-07-13T08:47:20+08:00",
            elapsed_seconds=300.0,
            source_mode="live",
            answer_ok=False,
            answer_preview="ERROR: codex exec timed out after 300s",
            input_tokens=None,
            output_tokens=None,
            reasoning_tokens=None,
            error_message="codex exec timed out after 300s",
            flags=["wrong_answer", "missing_usage", "timeout"],
            final_status="warn",
        )

        evidence = build_candidate_evidence(
            {candidate_id: [result]},
            {
                "run-timeout": {
                    "status": "degraded",
                    "question_pack_id": "coding-fast",
                    "question_pack_version": "coding-fast-v1.4",
                }
            },
            current_run_id="run-timeout",
            current_pack_id="coding-fast",
            current_pack_version="coding-fast-v1.4",
            required_question_count=5,
        )[candidate_id]

        self.assertEqual(evidence.latest_attempt_error_summary, "扫描 Q2 超时")

    def test_successful_retry_replaces_earlier_timeout_in_failure_summary(self) -> None:
        candidate_id = "codex-local-default:gpt-5.6-sol:medium"
        original_timeout = _result(
            candidate_id,
            "run-repaired",
            2,
            answer_ok=False,
            error_message="codex exec timed out after 300s",
        )
        successful_retry = _result(
            candidate_id,
            "run-repaired",
            2,
            answer_ok=False,
        )
        successful_retry.started_at = "2026-07-20T12:55:25+08:00"
        successful_retry.elapsed_seconds = 406.18
        successful_retry.flags = ["wrong_answer", "timeout"]
        results = [
            _result(candidate_id, "run-repaired", 1, answer_ok=False),
            original_timeout,
            _result(candidate_id, "run-repaired", 3, answer_ok=False),
            _result(candidate_id, "run-repaired", 4, answer_ok=False),
            _result(candidate_id, "run-repaired", 5, answer_ok=False),
            successful_retry,
        ]

        evidence = build_candidate_evidence(
            {candidate_id: results},
            {
                "run-repaired": {
                    "status": "degraded",
                    "question_pack_id": "coding-fast",
                    "question_pack_version": "coding-fast-v1.4",
                }
            },
            current_run_id="run-repaired",
            current_pack_id="coding-fast",
            current_pack_version="coding-fast-v1.4",
            required_question_count=5,
        )[candidate_id]

        self.assertTrue(evidence.is_current_run_eligible)
        self.assertIsNone(evidence.latest_attempt_error_category)
        self.assertIsNone(evidence.latest_attempt_error_summary)

    def test_successful_candidate_in_degraded_run_has_no_failure_category(self) -> None:
        candidate_id = "codex-local-default:gpt-5.4:xhigh"
        results = [
            _result(candidate_id, "run-degraded", question_number, answer_ok=True)
            for question_number in range(1, 6)
        ]

        evidence = build_candidate_evidence(
            {candidate_id: results},
            {
                "run-degraded": {
                    "status": "degraded",
                    "question_pack_id": "coding-fast",
                    "question_pack_version": "coding-fast-v1.4",
                }
            },
            current_run_id="run-degraded",
            current_pack_id="coding-fast",
            current_pack_version="coding-fast-v1.4",
            required_question_count=5,
        )[candidate_id]

        self.assertTrue(evidence.is_current_run_eligible)
        self.assertIsNone(evidence.latest_attempt_error_category)
        self.assertIsNone(evidence.latest_attempt_error_summary)

    def test_missing_current_question_is_repairable_and_not_eligible(self) -> None:
        candidate_id = "codex-local-default:gpt-5.4:xhigh"
        current_results = [
            _result(candidate_id, "run-current", question_number, answer_ok=True)
            for question_number in (1, 3, 4, 5)
        ]

        evidence = build_candidate_evidence(
            {candidate_id: current_results},
            {
                "run-current": {
                    "status": "degraded",
                    "question_pack_id": "coding-fast",
                    "question_pack_version": "coding-fast-v1.4",
                }
            },
            current_run_id="run-current",
            current_pack_id="coding-fast",
            current_pack_version="coding-fast-v1.4",
            required_question_count=5,
            required_question_ids=(
                "01_question",
                "02_question",
                "03_question",
                "04_question",
                "05_question",
            ),
        )[candidate_id]

        self.assertFalse(evidence.is_current_run_eligible)
        self.assertEqual(evidence.hard_failure_question_ids, ("02_question",))

    def test_equal_protocol_requires_q5_and_marks_it_repairable(self) -> None:
        candidate_id = "codex-local-default:gpt-5.4:xhigh"
        current_results = [
            _result(candidate_id, "run-current", question_number, answer_ok=True)
            for question_number in range(1, 5)
        ]

        evidence = build_candidate_evidence(
            {candidate_id: current_results},
            {
                "run-current": {
                    "status": "completed",
                    "question_pack_id": "coding-fast",
                    "question_pack_version": "coding-fast-v1.6",
                    "scoring_mode": "semantic_q1_q5_equal_v2",
                }
            },
            current_run_id="run-current",
            current_pack_id="coding-fast",
            current_pack_version="coding-fast-v1.6",
            required_question_count=5,
            required_question_ids=(
                "01_question",
                "02_question",
                "03_question",
                "04_question",
                "05_cache_regression_test_design",
            ),
        )[candidate_id]

        self.assertFalse(evidence.is_current_run_eligible)
        self.assertEqual(
            evidence.hard_failure_question_ids,
            ("05_cache_regression_test_design",),
        )
        self.assertIsNone(evidence.valid_run_id)


def _result(
    candidate_id: str,
    run_id: str,
    question_number: int,
    *,
    answer_ok: bool,
    error_message: str | None = None,
) -> ScanResult:
    return ScanResult(
        candidate_id=candidate_id,
        run_id=run_id,
        phase="scan",
        model="gpt-5.4",
        effort="xhigh",
        question_id=f"{question_number:02d}_question",
        question_title=f"Question {question_number}",
        grader_kind="json_exact",
        attempt_index=1,
        started_at=f"2026-07-14T10:00:0{question_number}+08:00",
        elapsed_seconds=1.0,
        source_mode="live",
        answer_ok=answer_ok,
        answer_preview=(error_message or "{}"),
        input_tokens=10,
        output_tokens=10,
        reasoning_tokens=10,
        error_message=error_message,
        flags=(["wrong_answer", "timeout"] if error_message else []),
        final_status=("warn" if error_message else "ok"),
    )


if __name__ == "__main__":
    unittest.main()
