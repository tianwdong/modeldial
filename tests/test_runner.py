from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import ANY, patch

from scanner.bounded_subprocess import BoundedSubprocessOutputError
from scanner.codex_runtime import (
    CODEX_EXEC_TIMEOUT_SECONDS,
    CodexPromptExecutionError,
    resolve_codex_executable,
    run_codex_prompt,
)
from scanner.endpoint_client import EndpointError, EndpointResult
from scanner.claude_code_client import ClaudeCodeResult
from scanner.grok_build_client import GrokBuildResult
from scanner.graders import GradeResult
from scanner.models import ResolvedScanTarget, ScanResult, TargetConfig
from scanner.question_bank import GraderSpec, QuestionBank, QuestionSpec
from scanner.runner import run_target


class RunnerTest(unittest.TestCase):
    QUESTION = QuestionSpec(
        id="01_candy",
        title="Candy",
        enabled=True,
        prompt="Return only one integer.",
        grader=GraderSpec(
            kind="regex",
            payload={"kind": "regex", "pattern": r"(?<!\d)21(?!\d)"},
        ),
        tags=["integer-output"],
    )

    @patch("scanner.codex_runtime.resolve_codex_executable", return_value="/opt/homebrew/bin/codex")
    @patch(
        "scanner.codex_runtime.run_bounded_process",
        side_effect=BoundedSubprocessOutputError(
            ["codex"],
            output_limit_bytes=128,
            total_output_bytes=129,
        ),
    )
    def test_codex_prompt_fails_closed_when_output_budget_is_exceeded(
        self,
        run_mock,  # type: ignore[no-untyped-def]
        resolve_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        with self.assertRaises(CodexPromptExecutionError) as error:
            run_codex_prompt("Return 21.", "gpt-5.4", "high")

        self.assertEqual(error.exception.execution_trace["terminal_state"], "output_limit_exceeded")
        self.assertEqual(error.exception.execution_trace["output_limit_bytes"], 128)

    def test_production_scanner_uses_stable_codex_runtime(self) -> None:
        root = Path(__file__).resolve().parent.parent
        runner_source = (root / "scanner" / "runner.py").read_text(encoding="utf-8")
        catalog_source = (root / "scanner" / "codex_model_catalog.py").read_text(
            encoding="utf-8"
        )
        runtime_source = (root / "scanner" / "codex_runtime.py").read_text(encoding="utf-8")

        self.assertIn("from .codex_runtime import", runner_source)
        self.assertIn("from .codex_runtime import", catalog_source)
        self.assertIn("def run_codex_prompt(", runtime_source)
        self.assertIn("def resolve_codex_executable(", runtime_source)

    def test_mock_runner_supports_session_bundle_test_design(self) -> None:
        question = next(
            item
            for item in QuestionBank(Path("questions")).load().enabled_questions
            if item.id == "01_session_bundle_repair"
        )

        result = run_target(
            TargetConfig(model="gpt-5.4", effort="xhigh"),
            question,
            use_mock_results=True,
        )

        self.assertEqual(question.grader.kind, "session_bundle_test_design")
        self.assertTrue(result.answer_ok)
        self.assertIn('"tests"', result.answer_preview)
        self.assertEqual(result.scorer_reason, "session_bundle_scenarios_v1 20/20")
        self.assertEqual(result.scorer_diagnostics["semantic_passed"], 20)

    def test_mock_runner_supports_retry_counterexample_design(self) -> None:
        question = next(
            item
            for item in QuestionBank(Path("questions")).load().enabled_questions
            if item.id == "02_code_counterexample_maxgap"
        )

        result = run_target(
            TargetConfig(model="gpt-5.4", effort="xhigh"),
            question,
            use_mock_results=True,
        )

        self.assertEqual(question.grader.kind, "retry_counterexample_design")
        self.assertTrue(result.answer_ok)
        self.assertIn('"counterexamples"', result.answer_preview)
        self.assertEqual(result.scorer_reason, "retry_planner_mutants_v3 20/20")
        self.assertEqual(result.scorer_diagnostics["semantic_passed"], 20)

    def test_mock_runner_supports_transaction_regression_design(self) -> None:
        question = next(
            item
            for item in QuestionBank(Path("questions")).load().enabled_questions
            if item.id == "04_transaction_regression_design"
        )

        result = run_target(
            TargetConfig(model="gpt-5.4", effort="xhigh"),
            question,
            use_mock_results=True,
        )

        self.assertEqual(question.grader.kind, "transaction_regression_design")
        self.assertTrue(result.answer_ok)
        self.assertIn('"tests"', result.answer_preview)
        self.assertEqual(result.scorer_reason, "transaction_replay_mutants_v2 20/20")
        self.assertEqual(result.scorer_diagnostics["semantic_passed"], 20)

    def test_mock_runner_supports_cache_propagation_certificate(self) -> None:
        question = next(
            item
            for item in QuestionBank(Path("questions")).load().enabled_questions
            if item.id == "05_cache_regression_test_design"
        )

        result = run_target(
            TargetConfig(model="gpt-5.4", effort="xhigh"),
            question,
            use_mock_results=True,
        )

        self.assertEqual(question.grader.kind, "cache_propagation_certificate")
        self.assertTrue(result.answer_ok)
        self.assertIn('"portfolios"', result.answer_preview)
        self.assertEqual(result.scorer_reason, "compact_propagation_certificate_v1 20/20")
        self.assertEqual(result.scorer_diagnostics["semantic_passed"], 20)

    def test_mock_runner_supports_ci_adversarial_audit(self) -> None:
        question = next(
            item
            for item in QuestionBank(Path("questions")).load().enabled_questions
            if item.id == "03_ci_optimality_certificate"
        )

        result = run_target(
            TargetConfig(model="gpt-5.4", effort="xhigh"),
            question,
            use_mock_results=True,
        )

        self.assertEqual(question.grader.kind, "ci_adversarial_audit")
        self.assertTrue(result.answer_ok)
        self.assertIn('"scenarios"', result.answer_preview)
        self.assertEqual(
            result.scorer_reason,
            "ci_adversarial_audit_certificate_v4 20/20",
        )
        self.assertEqual(result.scorer_diagnostics["semantic_passed"], 20)

    @patch("scanner.codex_runtime.os.access", return_value=True)
    @patch("scanner.codex_runtime.os.path.exists", return_value=True)
    @patch("scanner.codex_runtime.shutil.which", return_value="/opt/homebrew/bin/codex")
    def test_codex_executable_prefers_desktop_runtime_over_older_path_cli(
        self,
        which_mock,  # type: ignore[no-untyped-def]
        exists_mock,  # type: ignore[no-untyped-def]
        access_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        self.assertEqual(
            resolve_codex_executable(),
            "/Applications/ChatGPT.app/Contents/Resources/codex",
        )
        which_mock.assert_not_called()

    @patch("scanner.codex_runtime.os.access", return_value=True)
    @patch("scanner.codex_runtime.os.path.exists")
    @patch("scanner.codex_runtime.shutil.which", return_value=None)
    def test_codex_executable_falls_back_to_homebrew_path_when_gui_path_is_sparse(
        self,
        which_mock,  # type: ignore[no-untyped-def]
        exists_mock,  # type: ignore[no-untyped-def]
        access_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        exists_mock.side_effect = lambda path: path == "/opt/homebrew/bin/codex"

        self.assertEqual(resolve_codex_executable(), "/opt/homebrew/bin/codex")
        which_mock.assert_called_once_with("codex")
        access_mock.assert_called_once_with("/opt/homebrew/bin/codex", 1)

    @patch("scanner.codex_runtime.subprocess.run")
    @patch("scanner.codex_runtime.resolve_codex_executable", return_value="/opt/homebrew/bin/codex")
    def test_codex_prompt_uses_bounded_subprocess_timeout(
        self,
        resolve_mock,  # type: ignore[no-untyped-def]
        run_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = (
            '{"type":"item.completed","item":{"type":"agent_message","text":"21"}}\n'
            '{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":6,"output_tokens":1,"reasoning_output_tokens":2}}\n'
        )
        run_mock.return_value.stderr = ""

        run_codex_prompt("Return 21.", "gpt-5.4", "xhigh")

        kwargs = run_mock.call_args.kwargs
        self.assertEqual(kwargs["timeout"], CODEX_EXEC_TIMEOUT_SECONDS)
        self.assertEqual(kwargs["env"]["MODELDIAL_SCAN_EFFORT"], "xhigh")
        self.assertEqual(kwargs["env"]["MODELDIAL_SCAN_SESSION"], "1")
        self.assertIsNot(kwargs["env"], os.environ)

    @patch("scanner.codex_runtime.subprocess.run")
    @patch(
        "scanner.codex_runtime.resolve_codex_executable",
        return_value="/usr/local/bin/codex",
    )
    def test_codex_prompt_forwards_cloud_key_only_to_child_process(
        self,
        _resolve_mock,  # type: ignore[no-untyped-def]
        run_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = (
            '{"type":"item.completed","item":{"type":"agent_message","text":"21"}}\n'
            '{"type":"turn.completed","usage":{}}\n'
        )
        run_mock.return_value.stderr = ""
        with patch.dict(
            os.environ,
            {"MODELDIAL_CLOUD_API_KEY": "container-secret"},
            clear=False,
        ):
            os.environ.pop("CODEX_API_KEY", None)
            run_codex_prompt("Return 21.", "gpt-5.6-sol", "max")
            self.assertNotIn("CODEX_API_KEY", os.environ)

        self.assertEqual(
            run_mock.call_args.kwargs["env"]["CODEX_API_KEY"],
            "container-secret",
        )

    @patch("scanner.codex_runtime.subprocess.run")
    @patch("scanner.codex_runtime.resolve_codex_executable", return_value="/opt/homebrew/bin/codex")
    def test_codex_prompt_accepts_configured_timeout(
        self,
        resolve_mock,  # type: ignore[no-untyped-def]
        run_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = (
            '{"type":"item.completed","item":{"type":"agent_message","text":"21"}}\n'
            '{"type":"turn.completed","usage":{}}\n'
        )
        run_mock.return_value.stderr = ""

        run_codex_prompt("Return 21.", "gpt-5.4", "xhigh", timeout_seconds=420)

        self.assertEqual(run_mock.call_args.kwargs["timeout"], 420)

    @patch("scanner.codex_runtime.subprocess.run")
    @patch("scanner.codex_runtime.resolve_codex_executable", return_value="/opt/homebrew/bin/codex")
    def test_codex_prompt_runs_from_empty_ephemeral_directory(
        self,
        resolve_mock,  # type: ignore[no-untyped-def]
        run_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        observed_directory: list[Path] = []

        def completed_run(*args, **kwargs):  # type: ignore[no-untyped-def]
            cwd = Path(kwargs["cwd"])
            observed_directory.append(cwd)
            self.assertTrue(cwd.is_dir())
            self.assertEqual(list(cwd.iterdir()), [])
            result = unittest.mock.Mock()
            result.returncode = 0
            result.stdout = (
                '{"type":"item.completed","item":{"type":"agent_message","text":"21"}}\n'
                '{"type":"turn.completed","usage":{}}\n'
            )
            result.stderr = ""
            return result

        run_mock.side_effect = completed_run

        run_codex_prompt("Return 21.", "gpt-5.4", "high")

        self.assertEqual(len(observed_directory), 1)
        self.assertFalse(observed_directory[0].exists())

    @patch("scanner.codex_runtime.subprocess.run")
    @patch("scanner.codex_runtime.resolve_codex_executable", return_value="/opt/homebrew/bin/codex")
    def test_codex_prompt_returns_cached_input_usage(
        self,
        resolve_mock,  # type: ignore[no-untyped-def]
        run_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = (
            '{"type":"item.completed","item":{"type":"agent_message","text":"21"}}\n'
            '{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":6,"output_tokens":1,"reasoning_output_tokens":2}}\n'
        )
        run_mock.return_value.stderr = ""

        result = run_codex_prompt("Return 21.", "gpt-5.4", "high")

        self.assertEqual(result, ("21", 10, 6, 1, 2))

    @patch("scanner.codex_runtime.subprocess.run")
    @patch("scanner.codex_runtime.resolve_codex_executable", return_value="/opt/homebrew/bin/codex")
    def test_codex_prompt_returns_structured_local_trace(
        self,
        resolve_mock,  # type: ignore[no-untyped-def]
        run_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = (
            '{"type":"thread.started","thread_id":"thread-test"}\n'
            '{"type":"item.completed","item":{"type":"agent_message","text":"21"}}\n'
            '{"type":"turn.completed","usage":{"input_tokens":10}}\n'
        )
        run_mock.return_value.stderr = ""

        with patch("scanner.codex_runtime.record_modeldial_session_end") as end_mock:
            result = run_codex_prompt(
                "Return 21.",
                "gpt-5.4",
                "high",
                evaluation_id="md-eval-test",
                return_trace=True,
            )

        self.assertEqual(result[:5], ("21", 10, None, None, None))
        trace = result[5]
        self.assertEqual(trace["evaluation_id"], "md-eval-test")
        self.assertEqual(trace["terminal_state"], "completed_turn")
        self.assertTrue(trace["agent_message_received"])
        self.assertTrue(trace["turn_completed_received"])
        self.assertEqual(trace["provider_ids"], {"thread_id": ["thread-test"]})
        end_mock.assert_called_once_with("thread-test")

    @patch("scanner.codex_runtime.subprocess.run")
    @patch("scanner.codex_runtime.resolve_codex_executable", return_value="/opt/homebrew/bin/codex")
    def test_codex_prompt_process_error_still_closes_the_modeldial_session(
        self,
        resolve_mock,  # type: ignore[no-untyped-def]
        run_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        run_mock.return_value.returncode = 1
        run_mock.return_value.stdout = (
            '{"type":"thread.started","thread_id":"thread-failed"}\n'
        )
        run_mock.return_value.stderr = "request failed"

        with patch("scanner.codex_runtime.record_modeldial_session_end") as end_mock:
            with self.assertRaisesRegex(CodexPromptExecutionError, "codex exec failed"):
                run_codex_prompt("Return 21.", "gpt-5.4", "high")

        end_mock.assert_called_once_with("thread-failed")

    @patch("scanner.codex_runtime.subprocess.run")
    @patch("scanner.codex_runtime.resolve_codex_executable", return_value="/opt/homebrew/bin/codex")
    def test_codex_prompt_timeout_surfaces_clear_error(
        self,
        resolve_mock,  # type: ignore[no-untyped-def]
        run_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        run_mock.side_effect = subprocess.TimeoutExpired(
            cmd=["codex"],
            timeout=123,
            output=(
                b'{"type":"thread.started","thread_id":"thread-timeout"}\n'
            ),
            stderr=b"stream disconnected before completion; falling back to HTTP",
        )

        with patch("scanner.codex_runtime.record_modeldial_session_end") as end_mock:
            with self.assertRaisesRegex(CodexPromptExecutionError, "timed out") as error:
                run_codex_prompt("Return 21.", "gpt-5.4", "xhigh")

        trace = error.exception.execution_trace
        self.assertEqual(trace["terminal_state"], "timeout_without_completed_turn")
        self.assertEqual(
            trace["transport_markers"],
            ["websocket_disconnected", "http_fallback"],
        )
        end_mock.assert_called_once_with("thread-timeout")

    @patch("scanner.codex_runtime.subprocess.run")
    @patch("scanner.codex_runtime.resolve_codex_executable", return_value="/opt/homebrew/bin/codex")
    def test_codex_prompt_recovers_completed_turn_from_timeout_stdout(
        self,
        resolve_mock,  # type: ignore[no-untyped-def]
        run_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        completed_output = (
            '{"type":"item.completed","item":{"type":"agent_message","text":"21"}}\n'
            '{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":6,'
            '"output_tokens":1,"reasoning_output_tokens":2}}\n'
        )
        run_mock.side_effect = subprocess.TimeoutExpired(
            cmd=["codex"],
            timeout=420,
            output=completed_output.encode("utf-8"),
        )

        result = run_codex_prompt(
            "Return 21.",
            "gpt-5.4",
            "xhigh",
            timeout_seconds=420,
        )

        self.assertEqual(result, ("21", 10, 6, 1, 2))

    @patch("scanner.runner.preview", return_value="21")
    @patch(
        "scanner.runner.run_codex_prompt",
        return_value=(
            "21",
            100,
            60,
            20,
            516,
            {"terminal_state": "completed_turn"},
        ),
    )
    def test_live_runner_uses_codex_result(
        self,
        run_codex_prompt_mock,  # type: ignore[no-untyped-def]
        preview_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        result = run_target(
            TargetConfig(model="gpt-5.4", effort="high"),
            self.QUESTION,
            use_mock_results=False,
            run_id="run-test",
            phase="scan",
        )

        run_codex_prompt_mock.assert_called_once_with(
            self.QUESTION.prompt,
            "gpt-5.4",
            "high",
            timeout_seconds=300,
            evaluation_id=ANY,
            return_trace=True,
        )
        preview_mock.assert_called_once_with("21")
        self.assertTrue(result.answer_ok)
        self.assertEqual(result.source_mode, "live")
        self.assertEqual(result.error_message, None)
        self.assertEqual(result.reasoning_tokens, 516)
        self.assertEqual(result.cached_input_tokens, 60)
        self.assertEqual(result.cost_status, "estimated")
        self.assertAlmostEqual(result.reference_cost_usd or 0, 0.000415)
        self.assertIsNotNone(result.pricing_snapshot)
        self.assertEqual(result.question_id, "01_candy")
        self.assertEqual(result.question_title, "Candy")
        self.assertEqual(result.grader_kind, "regex")
        self.assertEqual(result.phase, "scan")
        self.assertTrue((result.evaluation_id or "").startswith("md-eval-"))
        self.assertEqual(result.execution_trace["terminal_state"], "completed_turn")

    @patch(
        "scanner.runner.grade_answer",
        return_value=GradeResult(
            ok=False,
            summary="black_box_regression_v2 grader_unavailable",
            score=None,
            max_score=20,
            diagnostics={
                "status": "grader_unavailable",
                "failure_summary": "sandbox_unavailable:FileNotFoundError",
            },
        ),
    )
    @patch("scanner.runner.preview", return_value="patch")
    @patch(
        "scanner.runner.run_codex_prompt",
        return_value=(
            "patch",
            100,
            60,
            20,
            200,
            {"terminal_state": "completed_turn"},
        ),
    )
    def test_live_runner_marks_grader_unavailable_as_execution_failure(
        self,
        _run_codex_prompt_mock,  # type: ignore[no-untyped-def]
        _preview_mock,  # type: ignore[no-untyped-def]
        _grade_answer_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        result = run_target(
            TargetConfig(model="gpt-5.4", effort="high"),
            self.QUESTION,
            use_mock_results=False,
            run_id="run-grader-unavailable",
            phase="scan",
        )

        self.assertFalse(result.answer_ok)
        self.assertEqual(
            result.error_message,
            "grader_unavailable: sandbox_unavailable:FileNotFoundError",
        )
        self.assertEqual(result.scorer_diagnostics["status"], "grader_unavailable")
        self.assertNotIn("semantic_passed", result.scorer_diagnostics)

    @patch("scanner.runner.run_codex_prompt", side_effect=RuntimeError("codex not logged in"))
    def test_live_runner_surfaces_error_message(
        self,
        run_codex_prompt_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        result = run_target(
            TargetConfig(model="gpt-5.5", effort="xhigh"),
            self.QUESTION,
            use_mock_results=False,
            run_id="run-test",
            phase="scan",
        )

        run_codex_prompt_mock.assert_called_once_with(
            self.QUESTION.prompt,
            "gpt-5.5",
            "xhigh",
            timeout_seconds=300,
            evaluation_id=ANY,
            return_trace=True,
        )
        self.assertFalse(result.answer_ok)
        self.assertEqual(result.source_mode, "live")
        self.assertEqual(result.error_message, "codex not logged in")
        self.assertIn("ERROR:", result.answer_preview)
        self.assertEqual(result.execution_trace["terminal_state"], "runner_exception")

    @patch("scanner.runner.SecretStore")
    @patch("scanner.runner.run_endpoint_request_isolated")
    def test_live_api_runner_uses_endpoint_and_preserves_candidate_identity(
        self,
        endpoint_request_mock,  # type: ignore[no-untyped-def]
        secret_store_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        endpoint_request_mock.return_value = EndpointResult(
            text="21",
            input_tokens=1000,
            output_tokens=30,
            reasoning_tokens=24,
            cached_input_tokens=400,
            cache_write_input_tokens=200,
            response_id="response-api-test",
            response_model="gpt-5.6-terra-20260831",
            stop_reason="stop",
        )
        secret_store_mock.return_value.resolve.return_value = "api-secret"
        target = ResolvedScanTarget(
            candidate_id="api-1:gpt-test:high",
            source_id="custom_endpoint",
            connection_id="api-1",
            model_id="gpt-5.6-terra",
            scan_profile="high",
            display_name="GPT Test High",
            connection_mode="api",
            api_format="openai_chat_completions",
            provider_preset="generic",
            base_url="https://example.com/v1",
            api_key_ref="keychain:com.modeldial.api-key:api-1",
        )

        result = run_target(
            target,
            self.QUESTION,
            use_mock_results=False,
            run_id="run-api",
            phase="scan",
        )

        secret_store_mock.return_value.resolve.assert_called_once_with(
            "keychain:com.modeldial.api-key:api-1"
        )
        endpoint_request_mock.assert_called_once_with(
            target,
            self.QUESTION.prompt,
            "api-secret",
            timeout_seconds=300,
            evaluation_id=ANY,
        )
        self.assertEqual(result.candidate_id, "api-1:gpt-test:high")
        self.assertEqual(result.source_mode, "api")
        self.assertTrue(result.answer_ok)
        self.assertEqual(result.reasoning_tokens, 24)
        self.assertEqual(result.cost_status, "estimated")
        self.assertAlmostEqual(
            result.reference_cost_usd or 0,
            400 * 2e-6 + 400 * 0.2e-6 + 200 * 2.5e-6 + 30 * 12e-6,
        )
        self.assertTrue((result.pricing_snapshot or "").startswith("pricing-v1-"))
        self.assertEqual(result.execution_trace["correlation_mode"], "request_header")
        self.assertEqual(
            result.execution_trace["request_header"],
            "X-Modeldial-Evaluation-ID",
        )
        self.assertEqual(
            result.execution_trace["response_model"],
            "gpt-5.6-terra-20260831",
        )
        self.assertEqual(result.execution_trace["stop_reason"], "stop")
        self.assertTrue(
            str(result.execution_trace["route_fingerprint"]).startswith(
                "route-v1:sha256:"
            )
        )
        self.assertNotIn("example.com", str(result.execution_trace))
        self.assertNotIn("api-secret", str(result.execution_trace))

    @patch("scanner.runner.SecretStore")
    @patch("scanner.runner.run_endpoint_request_isolated")
    def test_live_api_runner_persists_only_sanitized_endpoint_diagnostics(
        self,
        endpoint_request_mock,  # type: ignore[no-untyped-def]
        secret_store_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        endpoint_request_mock.side_effect = EndpointError(
            "worker_failed",
            diagnostics={
                "exception_type": "RemoteDisconnected",
                "stderr_sha256": "a" * 64,
            },
        )
        secret_store_mock.return_value.resolve.return_value = "api-secret"
        api_target = ResolvedScanTarget(
            candidate_id="api-1:gpt-test:high",
            source_id="custom_endpoint",
            connection_id="api-1",
            model_id="gpt-5.6-terra",
            scan_profile="high",
            display_name="GPT Test High",
            connection_mode="api",
            api_format="openai_chat_completions",
            provider_preset="generic",
            base_url="https://example.com/v1",
            api_key_ref="keychain:com.modeldial.api-key:api-1",
        )

        result = run_target(
            api_target,
            self.QUESTION,
            use_mock_results=False,
            run_id="run-api-error",
            phase="scan",
        )

        self.assertEqual(
            result.execution_trace["endpoint_diagnostics"],
            {
                "exception_type": "RemoteDisconnected",
                "stderr_sha256": "a" * 64,
            },
        )
        self.assertNotIn("example.com", str(result.execution_trace))
        self.assertNotIn("api-secret", str(result.execution_trace))

    @patch("scanner.runner.SecretStore")
    @patch("scanner.runner.run_endpoint_request_isolated")
    def test_live_api_runner_preserves_model_refusal_category(
        self,
        endpoint_request_mock,  # type: ignore[no-untyped-def]
        secret_store_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        endpoint_request_mock.side_effect = EndpointError(
            "model_refusal",
            diagnostics={
                "stop_reason": "refusal",
                "refusal_category": "content_policy_violation",
            },
        )
        secret_store_mock.return_value.resolve.return_value = "api-secret"
        api_target = ResolvedScanTarget(
            candidate_id="api-1:claude-fable-5:xhigh",
            source_id="custom_endpoint",
            connection_id="api-1",
            model_id="claude-fable-5",
            scan_profile="xhigh",
            display_name="Claude Fable 5 XHigh",
            connection_mode="api",
            api_format="anthropic_messages",
            provider_preset="anthropic",
            base_url="https://example.com/v1",
            api_key_ref="keychain:com.modeldial.api-key:api-1",
        )

        result = run_target(
            api_target,
            self.QUESTION,
            use_mock_results=False,
            run_id="run-api-refusal",
            phase="scan",
        )

        self.assertFalse(result.answer_ok)
        self.assertEqual(
            result.execution_trace["endpoint_error_category"],
            "model_refusal",
        )
        self.assertEqual(
            result.execution_trace["endpoint_diagnostics"],
            {
                "stop_reason": "refusal",
                "refusal_category": "content_policy_violation",
            },
        )

    @patch("scanner.runner.run_grok_build_prompt")
    def test_live_grok_build_runner_uses_local_cli_and_observed_cost(
        self,
        grok_prompt_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        grok_prompt_mock.return_value = GrokBuildResult(
            text="21",
            input_tokens=120,
            cached_input_tokens=80,
            output_tokens=30,
            reasoning_tokens=24,
            total_cost_usd=0.0081296,
            execution_trace={
                "correlation_mode": "grok_build_cli_json",
                "terminal_state": "completed_response",
            },
        )
        target = ResolvedScanTarget(
            candidate_id="grok-local-default:grok-4.5:high",
            source_id="grok_local",
            connection_id="grok-local-default",
            model_id="grok-4.5",
            scan_profile="high",
            display_name="Grok 4.5 High",
        )

        result = run_target(
            target,
            self.QUESTION,
            use_mock_results=False,
            run_id="run-grok",
            phase="scan",
        )

        grok_prompt_mock.assert_called_once_with(
            self.QUESTION.prompt,
            "grok-4.5",
            "high",
            timeout_seconds=300,
            evaluation_id=ANY,
        )
        self.assertTrue(result.answer_ok)
        self.assertEqual(result.cached_input_tokens, 80)
        self.assertEqual(result.reasoning_tokens, 24)
        self.assertEqual(result.reference_cost_usd, 0.0081296)
        self.assertEqual(result.cost_status, "observed")
        self.assertEqual(result.pricing_snapshot, "grok_build_cli")
        self.assertEqual(result.execution_trace["correlation_mode"], "grok_build_cli_json")

    @patch("scanner.runner.run_claude_code_prompt")
    def test_live_claude_code_runner_uses_local_cli_without_reasoning_tokens(
        self,
        claude_prompt_mock,  # type: ignore[no-untyped-def]
    ) -> None:
        claude_prompt_mock.return_value = ClaudeCodeResult(
            text="21",
            input_tokens=120,
            cached_input_tokens=80,
            output_tokens=30,
            total_cost_usd=0.0081296,
            execution_trace={
                "correlation_mode": "claude_code_cli_stream_json",
                "terminal_state": "completed_response",
            },
        )
        target = ResolvedScanTarget(
            candidate_id="claude-local-default:sonnet:high",
            source_id="claude_local",
            connection_id="claude-local-default",
            model_id="sonnet",
            scan_profile="high",
            display_name="Claude Sonnet High",
            reasoning_tokens_supported=False,
        )

        result = run_target(
            target,
            self.QUESTION,
            use_mock_results=False,
            run_id="run-claude",
            phase="scan",
        )

        claude_prompt_mock.assert_called_once_with(
            self.QUESTION.prompt,
            "sonnet",
            "high",
            timeout_seconds=300,
            evaluation_id=ANY,
        )
        self.assertTrue(result.answer_ok)
        self.assertEqual(result.cached_input_tokens, 80)
        self.assertIsNone(result.reasoning_tokens)
        self.assertFalse(result.reasoning_tokens_supported)
        self.assertEqual(result.reference_cost_usd, 0.0081296)
        self.assertEqual(result.cost_status, "observed")
        self.assertEqual(result.pricing_snapshot, "claude_code_cli")
        self.assertEqual(
            result.execution_trace["correlation_mode"],
            "claude_code_cli_stream_json",
        )

    def test_scan_result_serializes_scorer_diagnostics(self) -> None:
        result = ScanResult(
            model="gpt-5.4",
            effort="low",
            started_at="2026-07-07T09:00:00+08:00",
            elapsed_seconds=1.0,
            source_mode="live",
            answer_ok=False,
            answer_preview="--- a/cache_runner.py",
            input_tokens=1,
            cached_input_tokens=0,
            cache_write_input_tokens=1,
            output_tokens=2,
            reasoning_tokens=3,
            scorer_reason="micro_repo_cache_patch 0/12",
            scorer_diagnostics={
                "patch_applies": False,
                "semantic_passed": 0,
                "semantic_total": 12,
                "status": "patch_apply_failed",
            },
            evaluation_id="md-eval-history",
            execution_trace={
                "terminal_state": "timeout_without_completed_turn",
                "transport_markers": ["websocket_disconnected"],
            },
        )

        payload = result.to_dict()
        restored = ScanResult.from_dict(payload)

        self.assertEqual(
            {
                "patch_applies": False,
                "semantic_passed": 0,
                "semantic_total": 12,
                "status": "patch_apply_failed",
            },
            payload["scorer_diagnostics"],
        )
        self.assertEqual(payload["scorer_diagnostics"], restored.scorer_diagnostics)
        self.assertEqual(restored.cached_input_tokens, 0)
        self.assertEqual(restored.cache_write_input_tokens, 1)
        self.assertEqual(restored.evaluation_id, "md-eval-history")
        self.assertEqual(payload["execution_trace"], restored.execution_trace)

    def test_scan_result_reads_legacy_history_without_cost_fields(self) -> None:
        restored = ScanResult.from_dict(
            {
                "model": "gpt-5.4",
                "effort": "high",
                "started_at": "2026-07-07T09:00:00+08:00",
                "elapsed_seconds": 1,
                "answer_ok": True,
                "answer_preview": "21",
                "input_tokens": 10,
                "output_tokens": 2,
                "reasoning_tokens": 1,
            }
        )

        self.assertIsNone(restored.cached_input_tokens)
        self.assertIsNone(restored.reference_cost_usd)
        self.assertEqual(restored.cost_status, "unavailable")
        self.assertIsNone(restored.evaluation_id)
        self.assertEqual(restored.execution_trace, {})


if __name__ == "__main__":
    unittest.main()
