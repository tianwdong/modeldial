from __future__ import annotations

import unittest

from scanner.models import AppConfig, ScanResult
from scanner.rules import (
    evaluate_result,
    is_grok_outbound_replay,
    is_transient_execution_error,
)


class RulesTest(unittest.TestCase):
    def test_codex_transport_disconnect_is_transient(self) -> None:
        result = ScanResult(
            model="gpt-5.6-sol",
            effort="max",
            started_at="2026-08-01T00:00:00Z",
            elapsed_seconds=60.0,
            source_mode="live",
            answer_ok=False,
            answer_preview="ERROR: stream disconnected",
            input_tokens=None,
            output_tokens=None,
            reasoning_tokens=None,
            error_message="codex exec failed",
            execution_trace={"transport_markers": ["websocket_disconnected"]},
        )

        self.assertTrue(is_transient_execution_error(result))

    def test_codex_process_configuration_error_is_not_transient(self) -> None:
        result = ScanResult(
            model="gpt-5.6-sol",
            effort="max",
            started_at="2026-08-01T00:00:00Z",
            elapsed_seconds=1.0,
            source_mode="live",
            answer_ok=False,
            answer_preview="ERROR: invalid config",
            input_tokens=None,
            output_tokens=None,
            reasoning_tokens=None,
            error_message="codex exec failed",
            execution_trace={
                "terminal_state": "process_error",
                "process_returncode": 2,
            },
        )

        self.assertFalse(is_transient_execution_error(result))

    def test_grok_outbound_replay_failure_is_transient(self) -> None:
        result = ScanResult(
            model="grok-4.6",
            effort="xhigh",
            started_at="2026-08-31T11:11:18Z",
            elapsed_seconds=1802.0,
            source_mode="live",
            answer_ok=False,
            answer_preview="ERROR: Grok relay model execution failed",
            input_tokens=None,
            output_tokens=None,
            reasoning_tokens=None,
            error_message="grok_relay_model_failure",
            execution_trace={
                "correlation_mode": "grok_outbound_replay",
                "terminal_state": "relay_terminal_failure",
                "relay_error_code": "timeout",
            },
        )

        self.assertTrue(is_grok_outbound_replay(result))
        self.assertTrue(is_transient_execution_error(result))

    def test_codex_timeout_without_completed_turn_is_transient(self) -> None:
        result = ScanResult(
            model="gpt-5.6-luna",
            effort="max",
            started_at="2026-08-02T00:00:00Z",
            elapsed_seconds=1200.0,
            source_mode="live",
            answer_ok=False,
            answer_preview="ERROR: codex exec timed out after 1200s",
            input_tokens=None,
            output_tokens=None,
            reasoning_tokens=None,
            error_message="codex exec timed out after 1200s",
            execution_trace={"terminal_state": "timeout_without_completed_turn"},
        )

        self.assertTrue(is_transient_execution_error(result))

    def test_codex_process_killed_by_signal_is_transient(self) -> None:
        result = ScanResult(
            model="gpt-5.5",
            effort="xhigh",
            started_at="2026-08-02T00:00:00Z",
            elapsed_seconds=767.0,
            source_mode="live",
            answer_ok=False,
            answer_preview="ERROR: codex exec failed",
            input_tokens=None,
            output_tokens=None,
            reasoning_tokens=None,
            error_message="codex exec failed",
            execution_trace={
                "terminal_state": "process_error",
                "process_returncode": -9,
            },
        )

        self.assertTrue(is_transient_execution_error(result))

    def test_reason_tok_516_requests_retry(self) -> None:
        config = AppConfig.default()
        result = ScanResult(
            model="gpt-5.4",
            effort="high",
            started_at="2026-06-30T10:00:00+08:00",
            elapsed_seconds=10.0,
            source_mode="live",
            answer_ok=True,
            answer_preview="21",
            input_tokens=100,
            output_tokens=20,
            reasoning_tokens=516,
        )

        evaluation = evaluate_result(
            result,
            config.rules,
            hard_timeout_retry_count=config.system.timeout_retry_count,
        )

        self.assertIn("reason_tok_516", evaluation.flags)
        self.assertTrue(evaluation.should_retry)
        self.assertEqual(evaluation.final_status, "warn")

    def test_wrong_answer_warns_without_retry(self) -> None:
        config = AppConfig.default()
        result = ScanResult(
            model="gpt-5.5",
            effort="high",
            started_at="2026-06-30T10:00:00+08:00",
            elapsed_seconds=9.0,
            source_mode="live",
            answer_ok=False,
            answer_preview="20",
            input_tokens=100,
            output_tokens=20,
            reasoning_tokens=480,
        )

        evaluation = evaluate_result(
            result,
            config.rules,
            hard_timeout_retry_count=config.system.timeout_retry_count,
        )

        self.assertIn("wrong_answer", evaluation.flags)
        self.assertFalse(evaluation.should_retry)
        self.assertEqual(evaluation.final_status, "warn")

    def test_hard_execution_timeout_does_not_retry_by_default(self) -> None:
        config = AppConfig.default()
        result = ScanResult(
            model="gpt-5.4",
            effort="xhigh",
            started_at="2026-07-13T08:47:20+08:00",
            elapsed_seconds=300.0,
            source_mode="live",
            answer_ok=False,
            answer_preview="ERROR: codex exec timed out after 300s",
            input_tokens=None,
            output_tokens=None,
            reasoning_tokens=None,
            error_message="codex exec timed out after 300s",
        )

        evaluation = evaluate_result(
            result,
            config.rules,
            hard_timeout_retry_count=config.system.timeout_retry_count,
        )

        self.assertIn("timeout", evaluation.flags)
        self.assertFalse(evaluation.should_retry)
        self.assertEqual(evaluation.max_retries, 0)

        with_timeout_retry = evaluate_result(
            result,
            config.rules,
            hard_timeout_retry_count=1,
        )
        self.assertTrue(with_timeout_retry.should_retry)
        self.assertEqual(with_timeout_retry.max_retries, 1)

    def test_slow_success_warns_without_retry(self) -> None:
        config = AppConfig.default()
        result = ScanResult(
            model="gpt-5.4",
            effort="xhigh",
            started_at="2026-07-13T08:47:20+08:00",
            elapsed_seconds=120.0,
            source_mode="live",
            answer_ok=True,
            answer_preview="ok",
            input_tokens=100,
            output_tokens=20,
            reasoning_tokens=480,
        )

        evaluation = evaluate_result(result, config.rules)

        self.assertIn("slow_response", evaluation.flags)
        self.assertNotIn("timeout", evaluation.flags)
        self.assertFalse(evaluation.should_retry)

    def test_missing_reasoning_tokens_is_not_an_error_when_source_cannot_report_them(self) -> None:
        config = AppConfig.default()
        result = ScanResult(
            model="sonnet",
            effort="high",
            started_at="2026-07-21T10:00:00+08:00",
            elapsed_seconds=10.0,
            source_mode="live",
            answer_ok=True,
            answer_preview="21",
            input_tokens=100,
            output_tokens=20,
            reasoning_tokens=None,
            reasoning_tokens_supported=False,
        )

        evaluation = evaluate_result(result, config.rules)

        self.assertNotIn("missing_usage", evaluation.flags)
        self.assertEqual(evaluation.final_status, "pass")


if __name__ == "__main__":
    unittest.main()
