from __future__ import annotations

import os
import subprocess
import unittest
from unittest.mock import patch

from scanner.bounded_subprocess import BoundedSubprocessOutputError
from scanner.claude_code_client import (
    ClaudeCodeError,
    check_claude_code_login,
    run_claude_code_prompt,
)


class ClaudeCodeClientTest(unittest.TestCase):
    @patch(
        "scanner.claude_code_client.resolve_claude_code_executable",
        return_value="/opt/homebrew/bin/claude",
    )
    @patch(
        "scanner.claude_code_client.run_bounded_process",
        side_effect=BoundedSubprocessOutputError(
            ["claude"],
            output_limit_bytes=128,
            total_output_bytes=129,
        ),
    )
    def test_run_fails_closed_when_output_budget_is_exceeded(
        self,
        run_mock,  # type: ignore[no-untyped-def]
        resolve_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        with self.assertRaises(ClaudeCodeError) as error:
            run_claude_code_prompt("Reply with only OK.", "sonnet", "high")

        self.assertEqual(error.exception.category, "output_limit_exceeded")
        self.assertEqual(
            error.exception.execution_trace["terminal_state"],
            "output_limit_exceeded",
        )
        self.assertEqual(error.exception.execution_trace["output_limit_bytes"], 128)

    @patch(
        "scanner.claude_code_client.resolve_claude_code_executable",
        return_value="/opt/homebrew/bin/claude",
    )
    def test_login_check_uses_cli_status_without_reading_credentials(
        self,
        resolve_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='{"loggedIn":true,"authMethod":"oauth"}',
                stderr="",
            )

        check_claude_code_login(runner=runner)

        self.assertEqual(calls[0][0], ["/opt/homebrew/bin/claude", "auth", "status"])
        self.assertEqual(calls[0][1]["timeout"], 10)

    @patch(
        "scanner.claude_code_client.resolve_claude_code_executable",
        return_value="/opt/homebrew/bin/claude",
    )
    def test_login_check_reports_not_logged_in_without_leaking_output(
        self,
        resolve_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                command,
                1,
                stdout='{"loggedIn":false,"authMethod":"none"}',
                stderr="sensitive authentication detail",
            )

        with self.assertRaises(ClaudeCodeError) as error:
            check_claude_code_login(runner=runner)

        self.assertEqual(error.exception.category, "authentication_required")
        self.assertNotIn("sensitive authentication detail", str(error.exception))
        self.assertNotIn("sensitive authentication detail", str(error.exception.execution_trace))

    @patch(
        "scanner.claude_code_client.resolve_claude_code_executable",
        return_value="/opt/homebrew/bin/claude",
    )
    def test_run_uses_noninteractive_stream_json_and_preserves_usage(
        self,
        resolve_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    '{"type":"system","subtype":"init"}\n'
                    '{"type":"assistant","message":{"usage":{"input_tokens":10,'
                    '"cache_read_input_tokens":6,"output_tokens":3}}}\n'
                    '{"type":"result","subtype":"success","is_error":false,'
                    '"result":"OK","total_cost_usd":0.0125}\n'
                ),
                stderr="non-fatal diagnostic",
            )

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "not-forwarded"}):
            result = run_claude_code_prompt(
                "Reply with only OK.",
                "sonnet",
                "high",
                timeout_seconds=123,
                evaluation_id="md-eval-claude",
                runner=runner,
            )

        command, kwargs = calls[0]
        self.assertEqual(command[0], "/opt/homebrew/bin/claude")
        self.assertEqual(command[1:3], ["-p", "Reply with only OK."])
        self.assertIn("--output-format", command)
        self.assertEqual(command[command.index("--output-format") + 1], "stream-json")
        self.assertIn("--verbose", command)
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertEqual(command[command.index("--max-turns") + 1], "1")
        self.assertEqual(command[command.index("--model") + 1], "sonnet")
        self.assertEqual(command[command.index("--effort") + 1], "high")
        self.assertEqual(kwargs["timeout"], 123)
        self.assertNotIn("ANTHROPIC_API_KEY", kwargs["env"])
        self.assertEqual(result.text, "OK")
        self.assertEqual(result.input_tokens, 10)
        self.assertEqual(result.cached_input_tokens, 6)
        self.assertEqual(result.output_tokens, 3)
        self.assertEqual(result.total_cost_usd, 0.0125)
        self.assertEqual(result.execution_trace["correlation_mode"], "claude_code_cli_stream_json")
        self.assertNotIn("non-fatal diagnostic", str(result.execution_trace))

    @patch(
        "scanner.claude_code_client.resolve_claude_code_executable",
        return_value="/opt/homebrew/bin/claude",
    )
    def test_run_rejects_unsupported_reasoning_effort(
        self,
        resolve_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        calls: list[list[str]] = []

        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with self.assertRaises(ClaudeCodeError) as error:
            run_claude_code_prompt("Reply with only OK.", "sonnet", "xhigh", runner=runner)

        self.assertEqual(error.exception.category, "unsupported_reasoning_effort")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
