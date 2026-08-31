from __future__ import annotations

import os
import subprocess
import unittest
from unittest.mock import patch

from scanner.bounded_subprocess import BoundedSubprocessOutputError
from scanner.grok_build_client import (
    GROK_BUILD_EVAL_SYSTEM_PROMPT,
    GrokBuildError,
    check_grok_build_login,
    run_grok_build_prompt,
)


class GrokBuildClientTest(unittest.TestCase):
    @patch(
        "scanner.grok_build_client.resolve_grok_build_executable",
        return_value="/opt/homebrew/bin/grok",
    )
    @patch(
        "scanner.grok_build_client.run_bounded_process",
        side_effect=BoundedSubprocessOutputError(
            ["grok"],
            output_limit_bytes=128,
            total_output_bytes=129,
        ),
    )
    def test_run_fails_closed_when_output_budget_is_exceeded(
        self,
        run_mock,  # type: ignore[no-untyped-def]
        resolve_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        with self.assertRaises(GrokBuildError) as error:
            run_grok_build_prompt("Reply with only OK.", "grok-4.5", "high")

        self.assertEqual(error.exception.category, "output_limit_exceeded")
        self.assertEqual(
            error.exception.execution_trace["terminal_state"],
            "output_limit_exceeded",
        )
        self.assertEqual(error.exception.execution_trace["output_limit_bytes"], 128)

    @patch(
        "scanner.grok_build_client.resolve_grok_build_executable",
        return_value="/opt/homebrew/bin/grok",
    )
    def test_run_uses_json_stdout_and_preserves_observed_usage(
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
                    '{"text":"OK","sessionId":"session-id","requestId":"request-id",'
                    '"usage":{"input_tokens":10,"cache_read_input_tokens":6,'
                    '"output_tokens":3,"reasoning_tokens":2},"total_cost_usd":0.0125}'
                ),
                stderr="WARN non-fatal diagnostic",
            )

        with patch.dict(os.environ, {"XAI_API_KEY": "not-forwarded"}):
            result = run_grok_build_prompt(
                "Reply with only OK.",
                "grok-4.5",
                "default",
                timeout_seconds=123,
                evaluation_id="md-eval-grok",
                runner=runner,
            )

        command, kwargs = calls[0]
        self.assertEqual(command[0], "/opt/homebrew/bin/grok")
        self.assertIn("--no-auto-update", command)
        self.assertIn("--no-memory", command)
        self.assertIn("--disable-web-search", command)
        self.assertIn("--no-subagents", command)
        self.assertNotIn("--sandbox", command)
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertEqual(command[command.index("--deny") + 1], "MCPTool")
        self.assertIn("--verbatim", command)
        self.assertEqual(command[command.index("--max-turns") + 1], "1")
        self.assertEqual(
            command[command.index("--system-prompt-override") + 1],
            GROK_BUILD_EVAL_SYSTEM_PROMPT,
        )
        self.assertIn("--permission-mode", command)
        self.assertIn("dontAsk", command)
        self.assertIn("--reasoning-effort", command)
        effort_index = command.index("--reasoning-effort")
        self.assertEqual(command[effort_index + 1], "high")
        self.assertEqual(command[-2:], ["--output-format", "json"])
        self.assertEqual(kwargs["timeout"], 123)
        self.assertNotIn("XAI_API_KEY", kwargs["env"])
        self.assertEqual(result.text, "OK")
        self.assertEqual(result.input_tokens, 10)
        self.assertEqual(result.cached_input_tokens, 6)
        self.assertEqual(result.output_tokens, 3)
        self.assertEqual(result.reasoning_tokens, 2)
        self.assertEqual(result.total_cost_usd, 0.0125)
        self.assertEqual(result.execution_trace["terminal_state"], "completed_response")
        self.assertEqual(result.execution_trace["reasoning_effort"], "high")
        self.assertNotIn("WARN non-fatal diagnostic", str(result.execution_trace))

    @patch(
        "scanner.grok_build_client.resolve_grok_build_executable",
        return_value="/opt/homebrew/bin/grok",
    )
    def test_run_forwards_each_supported_reasoning_effort(
        self,
        resolve_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        for profile in ("low", "medium", "high"):
            calls: list[list[str]] = []

            def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                calls.append(command)
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout='{"text":"OK","usage":{}}',
                    stderr="",
                )

            with self.subTest(profile=profile):
                run_grok_build_prompt("Reply with only OK.", "grok-4.5", profile, runner=runner)
                effort_index = calls[0].index("--reasoning-effort")
                self.assertEqual(calls[0][effort_index + 1], profile)

    @patch(
        "scanner.grok_build_client.resolve_grok_build_executable",
        return_value="/opt/homebrew/bin/grok",
    )
    def test_run_does_not_retry_when_tool_free_cli_fails(
        self,
        resolve_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        calls: list[list[str]] = []

        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            if len(calls) == 1:
                return subprocess.CompletedProcess(
                    command,
                    1,
                    stdout='{"type":"error","message":"FS_PERMISSION_DENIED"}',
                    stderr="runtime startup failed",
                )
            raise AssertionError("CLI failure must not trigger a retry")

        with self.assertRaises(GrokBuildError) as error:
            run_grok_build_prompt(
                "Reply with only OK.",
                "grok-4.5",
                "default",
                runner=runner,
            )

        self.assertEqual(len(calls), 1)
        self.assertNotIn("--sandbox", calls[0])
        self.assertEqual(calls[0][calls[0].index("--tools") + 1], "")
        self.assertEqual(calls[0][calls[0].index("--deny") + 1], "MCPTool")
        self.assertIn("--verbatim", calls[0])
        self.assertEqual(calls[0][calls[0].index("--max-turns") + 1], "1")
        self.assertEqual(
            calls[0][calls[0].index("--system-prompt-override") + 1],
            GROK_BUILD_EVAL_SYSTEM_PROMPT,
        )
        effort_index = calls[0].index("--reasoning-effort")
        self.assertEqual(calls[0][effort_index + 1], "high")
        self.assertEqual(error.exception.category, "runtime_error")
        self.assertEqual(error.exception.execution_trace["terminal_state"], "process_error")
        self.assertEqual(error.exception.execution_trace["process_returncode"], 1)
        self.assertEqual(
            error.exception.execution_trace["stdout_bytes"],
            len('{"type":"error","message":"FS_PERMISSION_DENIED"}'),
        )
        self.assertGreater(error.exception.execution_trace["stderr_bytes"], 0)

    @patch(
        "scanner.grok_build_client.resolve_grok_build_executable",
        return_value="/opt/homebrew/bin/grok",
    )
    def test_run_rejects_grok_4_5_unsupported_reasoning_effort(
        self,
        resolve_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        calls: list[list[str]] = []

        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout='{"text":"OK","usage":{}}', stderr="")

        with self.assertRaises(GrokBuildError) as error:
            run_grok_build_prompt("Reply with only OK.", "grok-4.5", "xhigh", runner=runner)

        self.assertEqual(error.exception.category, "unsupported_reasoning_effort")
        self.assertEqual(calls, [])

    @patch(
        "scanner.grok_build_client.resolve_grok_build_executable",
        return_value="/opt/homebrew/bin/grok",
    )
    def test_run_forwards_grok_4_6_xhigh_reasoning_effort(
        self,
        resolve_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        calls: list[list[str]] = []

        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='{"text":"OK","usage":{}}',
                stderr="",
            )

        run_grok_build_prompt(
            "Reply with only OK.",
            "grok-4.6",
            "xhigh",
            runner=runner,
        )

        effort_index = calls[0].index("--reasoning-effort")
        self.assertEqual(calls[0][effort_index + 1], "xhigh")

    @patch(
        "scanner.grok_build_client.resolve_grok_build_executable",
        return_value="/opt/homebrew/bin/grok",
    )
    def test_run_rejects_nonzero_exit_without_leaking_stderr(
        self,
        resolve_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr="authentication detail that must not leak",
            )

        with self.assertRaises(GrokBuildError) as error:
            run_grok_build_prompt("Reply with only OK.", "grok-4.5", "default", runner=runner)

        self.assertEqual(error.exception.category, "runtime_error")
        self.assertNotIn("authentication detail", str(error.exception))
        self.assertNotIn("authentication detail", str(error.exception.execution_trace))

    @patch(
        "scanner.grok_build_client.resolve_grok_build_executable",
        return_value="/opt/homebrew/bin/grok",
    )
    def test_login_check_uses_cli_without_reading_credentials(
        self,
        resolve_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="You are logged in with grok.com.\n\nAvailable models:",
                stderr="",
            )

        check_grok_build_login(runner=runner)

        self.assertEqual(calls[0][0], ["/opt/homebrew/bin/grok", "models"])
        self.assertEqual(calls[0][1]["timeout"], 10)

    @patch(
        "scanner.grok_build_client.resolve_grok_build_executable",
        return_value="/opt/homebrew/bin/grok",
    )
    def test_login_check_rejects_successful_cli_without_explicit_auth_status(
        self,
        resolve_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="Available models:\ngrok-4.5",
                stderr="",
            )

        with self.assertRaises(GrokBuildError) as error:
            check_grok_build_login(runner=runner)

        self.assertEqual(error.exception.category, "authentication_required")
        self.assertEqual(
            error.exception.execution_trace["terminal_state"],
            "login_status_unavailable",
        )

    @patch(
        "scanner.grok_build_client.resolve_grok_build_executable",
        return_value="/opt/homebrew/bin/grok",
    )
    def test_login_check_does_not_treat_api_key_as_user_login(
        self,
        resolve_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="You are using XAI_API_KEY.\n",
                stderr="",
            )

        with self.assertRaises(GrokBuildError) as error:
            check_grok_build_login(runner=runner)

        self.assertEqual(error.exception.category, "authentication_required")

    @patch(
        "scanner.grok_build_client.resolve_grok_build_executable",
        return_value="/opt/homebrew/bin/grok",
    )
    def test_login_check_reports_authentication_required(
        self,
        resolve_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="not logged in")

        with self.assertRaises(GrokBuildError) as error:
            check_grok_build_login(runner=runner)

        self.assertEqual(error.exception.category, "authentication_required")
        self.assertEqual(
            error.exception.execution_trace["terminal_state"],
            "login_check_failed",
        )
        self.assertNotIn("not logged in", str(error.exception))


if __name__ == "__main__":
    unittest.main()
