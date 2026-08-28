from __future__ import annotations

import json
import io
import hashlib
import os
from http.client import RemoteDisconnected
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

from scanner.bounded_subprocess import BoundedSubprocessOutputError
from scanner.endpoint_client import (
    EndpointError,
    EndpointRequest,
    _EndpointRedirectHandler,
    _default_endpoint_opener,
    _isolated_worker_main,
    build_endpoint_request,
    discover_model_catalog,
    discover_models,
    execute_endpoint_request,
    parse_endpoint_response,
    run_endpoint_request_isolated,
)


FIXTURES = Path(__file__).parent / "fixtures"


def target(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "model_id": "gpt-test",
        "scan_profile": "high",
        "api_format": "openai_chat_completions",
        "provider_preset": "generic",
        "base_url": "https://example.com/v1",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._data = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self, amount: int = -1) -> bytes:
        return self._data if amount < 0 else self._data[:amount]


class FakeStreamingResponse:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines
        self._index = 0

    def __enter__(self) -> "FakeStreamingResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def readline(self, amount: int = -1) -> bytes:
        if self._index >= len(self._lines):
            return b""
        line = self._lines[self._index]
        self._index += 1
        return line if amount < 0 else line[:amount]


CHAT_SUCCESS = {
    "id": "chatcmpl-isolated",
    "choices": [{"message": {"content": "21"}}],
    "usage": {
        "prompt_tokens": 10,
        "completion_tokens": 3,
        "prompt_tokens_details": {"cached_tokens": 6},
        "completion_tokens_details": {"reasoning_tokens": 2},
    },
}


class EndpointClientTest(unittest.TestCase):
    def tearDown(self) -> None:
        _default_endpoint_opener.cache_clear()

    @patch("scanner.endpoint_client.build_opener")
    def test_default_endpoint_opener_is_created_lazily(self, build_opener) -> None:  # type: ignore[no-untyped-def]
        opener = object()
        build_opener.return_value = opener
        _default_endpoint_opener.cache_clear()

        self.assertIs(_default_endpoint_opener(), opener)
        self.assertIs(_default_endpoint_opener(), opener)

        build_opener.assert_called_once()
        redirect_handler = build_opener.call_args.args[0]
        self.assertIsInstance(redirect_handler, _EndpointRedirectHandler)

    def test_default_redirect_handler_keeps_auth_on_same_origin_redirect(self) -> None:
        request = Request(
            "https://example.com/v1/chat/completions",
            headers={
                "Authorization": "Bearer api-secret",
                "X-Api-Key": "api-secret",
            },
            method="POST",
        )

        redirected = _EndpointRedirectHandler().redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://example.com/v1/redirected",
        )

        assert redirected is not None
        self.assertEqual(redirected.get_header("Authorization"), "Bearer api-secret")
        self.assertEqual(
            dict(redirected.header_items()).get("X-api-key"),
            "api-secret",
        )

    def test_default_redirect_handler_strips_auth_on_cross_origin_redirect(self) -> None:
        request = Request(
            "https://example.com/v1/chat/completions",
            headers={
                "Authorization": "Bearer api-secret",
                "X-Api-Key": "api-secret",
            },
            method="POST",
        )

        redirected = _EndpointRedirectHandler().redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://other.example/v1/redirected",
        )

        assert redirected is not None
        self.assertIsNone(redirected.get_header("Authorization"))
        self.assertNotIn(
            "x-api-key",
            {name.casefold() for name, _ in redirected.header_items()},
        )

    @patch("scanner.endpoint_client.MAX_ENDPOINT_RESPONSE_BYTES", 8)
    def test_non_stream_response_budget_fails_closed(self) -> None:
        with self.assertRaises(EndpointError) as error:
            execute_endpoint_request(
                EndpointRequest(
                    url="https://example.com/v1/chat/completions",
                    body={"model": "gpt-test", "stream": False},
                ),
                "api-secret",
                urlopen=lambda *_args, **_kwargs: FakeResponse({"data": "123456789"}),
            )

        self.assertEqual(error.exception.category, "invalid_response")

    @patch("scanner.endpoint_client.MAX_MODEL_LIST_RESPONSE_BYTES", 8)
    def test_model_discovery_response_budget_fails_closed(self) -> None:
        with self.assertRaises(EndpointError) as error:
            discover_models(
                "https://example.com/v1",
                "api-secret",
                urlopen=lambda *_args, **_kwargs: FakeResponse({"data": ["123456789"]}),
            )

        self.assertEqual(error.exception.category, "invalid_response")

    def test_model_discovery_remote_disconnect_is_network_error(self) -> None:
        def urlopen(*_: object, **__: object) -> FakeResponse:
            raise RemoteDisconnected("proxy token=api-secret")

        with self.assertRaises(EndpointError) as error:
            discover_models(
                "https://example.com/v1",
                "api-secret",
                urlopen=urlopen,
            )

        self.assertEqual(error.exception.category, "network_error")
        self.assertEqual(
            error.exception.diagnostics,
            {"exception_type": "RemoteDisconnected"},
        )
        self.assertNotIn("api-secret", str(error.exception.diagnostics))

    @patch("scanner.endpoint_client.MAX_SSE_RESPONSE_BYTES", 24)
    def test_sse_total_response_budget_fails_closed(self) -> None:
        with self.assertRaises(EndpointError) as error:
            execute_endpoint_request(
                build_endpoint_request(target(api_format="openai_responses"), "2+2"),
                "api-secret",
                urlopen=lambda *_args, **_kwargs: FakeStreamingResponse(
                    [b"event: response.created\n", b"\n"]
                ),
            )

        self.assertEqual(error.exception.category, "invalid_response")

    @patch("scanner.endpoint_client.execute_endpoint_request", return_value=CHAT_SUCCESS)
    def test_isolated_request_worker_returns_structured_payload(self, execute) -> None:  # type: ignore[no-untyped-def]
        worker_input = {
            "request": {
                "url": "https://example.com/v1/chat/completions",
                "body": {"model": "gpt-test"},
            },
            "api_key": "api-secret",
            "timeout_seconds": 2,
            "evaluation_id": "eval-1",
        }
        output = io.StringIO()

        with patch("scanner.endpoint_client.sys.stdin", io.StringIO(json.dumps(worker_input))), \
             patch("scanner.endpoint_client.sys.stdout", output):
            exit_code = _isolated_worker_main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output.getvalue()), {"ok": True, "payload": CHAT_SUCCESS})
        execute.assert_called_once_with(
            EndpointRequest(
                url="https://example.com/v1/chat/completions",
                body={"model": "gpt-test"},
            ),
            "api-secret",
            timeout_seconds=2.0,
            evaluation_id="eval-1",
        )

    @patch("scanner.endpoint_client.subprocess.run")
    def test_isolated_request_keeps_secret_out_of_process_arguments(self, run) -> None:  # type: ignore[no-untyped-def]
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"ok": True, "payload": CHAT_SUCCESS}),
            stderr="",
        )

        result = run_endpoint_request_isolated(
            target(),
            "return 21",
            "api-secret",
            timeout_seconds=300,
            evaluation_id="eval-1",
        )

        process_args = run.call_args.args[0]
        process_input = json.loads(run.call_args.kwargs["input"])
        self.assertNotIn("api-secret", " ".join(process_args))
        self.assertEqual(process_input["api_key"], "api-secret")
        self.assertEqual(result.text, "21")
        self.assertEqual(result.cached_input_tokens, 6)

    @patch("scanner.endpoint_client.is_frozen_runtime", return_value=True)
    @patch("scanner.endpoint_client.module_worker_command")
    def test_frozen_isolated_request_fixture_worker_succeeds_with_backend_root(
        self,
        module_command,
        _is_frozen,
    ) -> None:  # type: ignore[no-untyped-def]
        expected_output = json.dumps({"ok": True, "payload": CHAT_SUCCESS})
        fixture_code = (
            "import json, os, sys\n"
            "assert os.environ.get('MODELDIAL_BACKEND_ROOT') == "
            "'/tmp/modeldial-backend'\n"
            "assert 'MODELDIAL_DATA_DIR' not in os.environ\n"
            "assert 'OPENAI_API_KEY' not in os.environ\n"
            "json.load(sys.stdin)\n"
            f"print({expected_output!r})\n"
        )
        module_command.return_value = [sys.executable, "-c", fixture_code]

        with patch.dict(
            os.environ,
            {
                "MODELDIAL_BACKEND_ROOT": "/tmp/modeldial-backend",
                "MODELDIAL_DATA_DIR": "/must-not-pass",
                "OPENAI_API_KEY": "must-not-pass",
            },
            clear=True,
        ):
            result = run_endpoint_request_isolated(
                target(),
                "return 21",
                "api-secret",
            )

        module_command.assert_called_once_with(
            "scanner.endpoint_client",
            "--execute-request",
        )
        self.assertEqual(result.text, "21")

    @patch("scanner.endpoint_client.is_frozen_runtime", return_value=False)
    @patch("scanner.endpoint_client.subprocess.run")
    def test_non_frozen_isolated_request_does_not_forward_backend_root(
        self,
        run,
        _is_frozen,
    ) -> None:  # type: ignore[no-untyped-def]
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"ok": True, "payload": CHAT_SUCCESS}),
            stderr="",
        )

        with patch.dict(
            os.environ,
            {"MODELDIAL_BACKEND_ROOT": "/tmp/modeldial-backend"},
            clear=True,
        ):
            run_endpoint_request_isolated(target(), "return 21", "api-secret")

        self.assertNotIn(
            "MODELDIAL_BACKEND_ROOT",
            run.call_args.kwargs["env"],
        )

    def test_response_usage_preserves_cached_input_tokens(self) -> None:
        result = parse_endpoint_response(
            "openai_responses",
            {
                "id": "resp-1",
                "output_text": "OK",
                "usage": {
                    "input_tokens": 20,
                    "output_tokens": 4,
                    "input_tokens_details": {
                        "cached_tokens": 12,
                        "cache_write_tokens": 3,
                    },
                },
            },
        )

        self.assertEqual(result.input_tokens, 20)
        self.assertEqual(result.cached_input_tokens, 12)
        self.assertEqual(result.cache_write_input_tokens, 3)

    def test_anthropic_usage_combines_cached_tokens_into_total_input(self) -> None:
        result = parse_endpoint_response(
            "anthropic_messages",
            {
                "id": "msg-1",
                "content": [{"type": "text", "text": "OK"}],
                "usage": {
                    "input_tokens": 10,
                    "cache_creation_input_tokens": 5,
                    "cache_read_input_tokens": 20,
                    "output_tokens": 3,
                },
            },
        )

        self.assertEqual(result.input_tokens, 35)
        self.assertEqual(result.cached_input_tokens, 20)
        self.assertEqual(result.cache_write_input_tokens, 5)

    @patch("scanner.endpoint_client.subprocess.run")
    def test_isolated_request_preserves_anthropic_api_format(self, run) -> None:  # type: ignore[no-untyped-def]
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({
                "ok": True,
                "payload": {
                    "id": "msg-isolated",
                    "content": [{"type": "text", "text": "OK"}],
                    "usage": {"input_tokens": 2, "output_tokens": 1},
                },
            }),
            stderr="",
        )

        result = run_endpoint_request_isolated(
            target(
                model_id="claude-fable-5",
                api_format="anthropic_messages",
                scan_profile="default",
            ),
            "Reply with only OK.",
            "api-secret",
            streaming=False,
        )

        process_input = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(
            process_input["request"]["api_format"],
            "anthropic_messages",
        )
        self.assertIs(process_input["request"]["body"]["stream"], False)
        self.assertEqual(result.text, "OK")

    @patch("scanner.endpoint_client.subprocess.run")
    def test_isolated_request_reports_terminated_worker(self, run) -> None:  # type: ignore[no-untyped-def]
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=-15,
            stdout="",
            stderr="",
        )

        with self.assertRaises(EndpointError) as error:
            run_endpoint_request_isolated(
                target(),
                "return 21",
                "api-secret",
            )

        self.assertEqual(error.exception.category, "request_interrupted")

    @patch(
        "scanner.endpoint_client.run_bounded_process",
        side_effect=BoundedSubprocessOutputError(
            ["endpoint-worker"],
            output_limit_bytes=128,
            total_output_bytes=129,
        ),
    )
    def test_isolated_request_fails_closed_when_worker_output_exceeds_budget(
        self,
        _run,
    ) -> None:  # type: ignore[no-untyped-def]
        with self.assertRaises(EndpointError) as error:
            run_endpoint_request_isolated(target(), "return 21", "api-secret")

        self.assertEqual(error.exception.category, "worker_failed")
        self.assertEqual(
            error.exception.diagnostics,
            {
                "output_limit_bytes": 128,
                "output_total_bytes": 129,
            },
        )

    @patch("scanner.endpoint_client.subprocess.run")
    def test_isolated_request_hashes_worker_stderr_without_persisting_it(self, run) -> None:  # type: ignore[no-untyped-def]
        stderr = "upstream failed with api-secret"
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr=stderr,
        )

        with self.assertRaises(EndpointError) as error:
            run_endpoint_request_isolated(target(), "return 21", "api-secret")

        self.assertEqual(error.exception.category, "worker_failed")
        self.assertEqual(
            error.exception.diagnostics,
            {
                "worker_return_code": 1,
                "stderr_bytes": len(stderr.encode("utf-8")),
                "stderr_sha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
            },
        )
        self.assertNotIn("api-secret", str(error.exception.diagnostics))

    @patch(
        "scanner.endpoint_client.execute_endpoint_request",
        side_effect=RuntimeError("unexpected api-secret"),
    )
    def test_isolated_worker_sanitizes_unexpected_exception(self, _execute) -> None:  # type: ignore[no-untyped-def]
        worker_input = {
            "request": {
                "url": "https://example.com/v1/chat/completions",
                "body": {"model": "gpt-test"},
            },
            "api_key": "api-secret",
        }
        output = io.StringIO()

        with patch("scanner.endpoint_client.sys.stdin", io.StringIO(json.dumps(worker_input))), \
             patch("scanner.endpoint_client.sys.stdout", output):
            exit_code = _isolated_worker_main()

        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["category"], "worker_failed")
        self.assertEqual(payload["diagnostics"], {"exception_type": "RuntimeError"})
        self.assertNotIn("api-secret", output.getvalue())
    def test_model_discovery_uses_models_endpoint_and_bearer_auth(self) -> None:
        captured: dict[str, object] = {}

        def urlopen(request, **kwargs):  # type: ignore[no-untyped-def]
            captured["url"] = request.full_url
            captured["authorization"] = request.get_header("Authorization")
            captured["user_agent"] = request.get_header("User-agent")
            return FakeResponse({"data": [{"id": "z-model"}, {"id": "a-model"}]})

        models = discover_models(
            "https://example.com/v1/",
            "api-secret",
            urlopen=urlopen,
        )

        self.assertEqual(models, ["z-model", "a-model"])
        self.assertEqual(captured["url"], "https://example.com/v1/models")
        self.assertEqual(captured["authorization"], "Bearer api-secret")
        self.assertEqual(captured["user_agent"], "ModelDial/EndpointClientV1")

    def test_anthropic_model_discovery_uses_native_auth_headers(self) -> None:
        captured: dict[str, object] = {}

        def urlopen(request, **kwargs):  # type: ignore[no-untyped-def]
            captured["headers"] = {
                key.lower(): value for key, value in request.header_items()
            }
            return FakeResponse({"data": [{"id": "claude-fable-5"}]})

        models = discover_models(
            "https://example.com/v1",
            "api-secret",
            api_format="anthropic_messages",
            urlopen=urlopen,
        )

        self.assertEqual(models, ["claude-fable-5"])
        self.assertEqual(captured["headers"]["x-api-key"], "api-secret")
        self.assertEqual(captured["headers"]["anthropic-version"], "2023-06-01")
        self.assertEqual(
            captured["headers"]["user-agent"],
            "ModelDial/EndpointClientV1",
        )
        self.assertNotIn("authorization", captured["headers"])

    def test_model_discovery_extracts_openrouter_reasoning_efforts(self) -> None:
        def urlopen(request, **kwargs):  # type: ignore[no-untyped-def]
            return FakeResponse({
                "data": [{
                    "id": "vendor/model",
                    "reasoning": {
                        "supported_efforts": ["low", "high", "max"],
                        "default_effort": "high",
                    },
                }]
            })

        models = discover_model_catalog(
            "https://openrouter.ai/api/v1",
            "api-secret",
            urlopen=urlopen,
        )

        self.assertEqual(models[0].model_id, "vendor/model")
        self.assertEqual(models[0].reasoning_efforts, ("low", "high", "max"))
        self.assertEqual(models[0].default_reasoning_effort, "high")

    def test_deepseek_model_discovery_enriches_official_reasoning_contract(self) -> None:
        def urlopen(request, **kwargs):  # type: ignore[no-untyped-def]
            return FakeResponse({
                "data": [
                    {"id": "deepseek-v4-flash"},
                    {"id": "deepseek-v4-pro"},
                ]
            })

        models = discover_model_catalog(
            "https://api.deepseek.com",
            "api-secret",
            urlopen=urlopen,
        )

        self.assertEqual(models[0].reasoning_efforts, ("low", "high", "max"))
        self.assertEqual(models[0].default_reasoning_effort, "high")
        self.assertEqual(models[1].reasoning_efforts, ("high", "max"))
        self.assertEqual(models[1].default_reasoning_effort, "high")

    def test_model_discovery_extracts_anthropic_effort_capabilities(self) -> None:
        def urlopen(request, **kwargs):  # type: ignore[no-untyped-def]
            return FakeResponse({
                "data": [{
                    "id": "claude-fable-5",
                    "capabilities": {
                        "effort": {
                            "low": {"supported": True},
                            "medium": {"supported": True},
                            "high": {"supported": True},
                            "xhigh": {"supported": True},
                            "max": {"supported": True},
                        }
                    },
                }]
            })

        models = discover_model_catalog(
            "https://api.anthropic.com/v1",
            "api-secret",
            api_format="anthropic_messages",
            urlopen=urlopen,
        )

        self.assertEqual(
            models[0].reasoning_efforts,
            ("low", "medium", "high", "xhigh", "max"),
        )

    def test_endpoint_request_includes_evaluation_correlation_header(self) -> None:
        captured: dict[str, object] = {}

        def urlopen(request, **kwargs):  # type: ignore[no-untyped-def]
            captured["headers"] = {
                key.lower(): value for key, value in request.header_items()
            }
            return FakeStreamingResponse([
                b'data: {"id":"chat-header","choices":[{"delta":{"content":"OK"}}]}\n',
                b"\n",
                b"data: [DONE]\n",
                b"\n",
            ])

        execute_endpoint_request(
            build_endpoint_request(target(), "2+2"),
            "api-secret",
            evaluation_id="md-eval-endpoint-test",
            urlopen=urlopen,
        )

        self.assertEqual(
            captured["headers"],
            {
                "authorization": "Bearer api-secret",
                "content-type": "application/json",
                "accept": "text/event-stream",
                "x-modeldial-evaluation-id": "md-eval-endpoint-test",
                "user-agent": "ModelDial/EndpointClientV1",
            },
        )

    def test_generic_chat_request_uses_openai_reasoning_effort(self) -> None:
        request = build_endpoint_request(target(), "2+2")

        self.assertEqual(request.url, "https://example.com/v1/chat/completions")
        self.assertIs(request.body["stream"], True)
        self.assertEqual(request.body["stream_options"], {"include_usage": True})
        self.assertEqual(request.body["reasoning_effort"], "high")
        self.assertNotIn("reasoning", request.body)

    def test_kimi_k3_request_uses_selected_reasoning_effort(self) -> None:
        for effort in ("low", "high", "max"):
            with self.subTest(effort=effort):
                request = build_endpoint_request(
                    target(
                        model_id="k3",
                        scan_profile=effort,
                        provider_preset="custom",
                        base_url="https://api.kimi.com/coding/v1",
                    ),
                    "2+2",
                )

                self.assertEqual(request.body["reasoning_effort"], effort)

    def test_deepseek_request_enables_thinking_with_canonical_effort(self) -> None:
        for effort in ("low", "high", "max"):
            with self.subTest(effort=effort):
                request = build_endpoint_request(
                    target(
                        model_id="deepseek-v4-flash",
                        scan_profile=effort,
                        base_url="https://api.deepseek.com",
                    ),
                    "2+2",
                )

                self.assertEqual(request.body["thinking"], {"type": "enabled"})
                self.assertEqual(request.body["reasoning_effort"], effort)

    def test_deepseek_request_rejects_unsupported_effort_for_model(self) -> None:
        with self.assertRaisesRegex(EndpointError, "protocol_mismatch"):
            build_endpoint_request(
                target(
                    model_id="deepseek-v4-pro",
                    scan_profile="low",
                    base_url="https://api.deepseek.com",
                ),
                "2+2",
            )

    def test_legacy_deepseek_default_still_enables_thinking(self) -> None:
        request = build_endpoint_request(
            target(
                model_id="deepseek-v4-flash",
                scan_profile="default",
                base_url="https://api.deepseek.com",
            ),
            "2+2",
        )

        self.assertEqual(request.body["thinking"], {"type": "enabled"})
        self.assertNotIn("reasoning_effort", request.body)

    def test_default_api_profile_does_not_invent_reasoning_effort(self) -> None:
        request = build_endpoint_request(target(scan_profile="default"), "2+2")

        self.assertNotIn("reasoning_effort", request.body)
        self.assertNotIn("reasoning", request.body)

    def test_display_normalization_does_not_change_gemini_request_identity(self) -> None:
        request = build_endpoint_request(
            target(
                model_id="gemini-3.6-flash-high",
                scan_profile="default",
            ),
            "2+2",
        )

        self.assertEqual(request.body["model"], "gemini-3.6-flash-high")
        self.assertNotIn("reasoning_effort", request.body)
        self.assertNotIn("reasoning", request.body)

    def test_openrouter_chat_request_uses_nested_reasoning(self) -> None:
        request = build_endpoint_request(
            target(provider_preset="openrouter"),
            "2+2",
        )

        self.assertEqual(request.body["reasoning"], {"effort": "high"})
        self.assertNotIn("reasoning_effort", request.body)

    def test_responses_request_is_stateless(self) -> None:
        request = build_endpoint_request(
            target(api_format="openai_responses"),
            "2+2",
        )

        self.assertEqual(request.url, "https://example.com/v1/responses")
        self.assertEqual(request.body["reasoning"], {"effort": "high"})
        self.assertIs(request.body["store"], False)
        self.assertIs(request.body["stream"], True)

    def test_responses_stream_returns_completed_response(self) -> None:
        completed = {
            "id": "resp-stream",
            "status": "completed",
            "output_text": "21",
            "usage": {"input_tokens": 10, "output_tokens": 2},
        }

        payload = execute_endpoint_request(
            build_endpoint_request(target(api_format="openai_responses"), "2+2"),
            "api-secret",
            urlopen=lambda *_args, **_kwargs: FakeStreamingResponse([
                b": keepalive\n",
                b"\n",
                b"event: response.created\n",
                b'data: {"type":"response.created","response":{"id":"resp-stream"}}\n',
                b"\n",
                b"event: response.completed\n",
                (
                    "data: "
                    + json.dumps({"type": "response.completed", "response": completed})
                    + "\n"
                ).encode("utf-8"),
                b"\n",
            ]),
        )

        self.assertEqual(payload, completed)

    def test_responses_stream_failure_is_categorized(self) -> None:
        with self.assertRaises(EndpointError) as error:
            execute_endpoint_request(
                build_endpoint_request(target(api_format="openai_responses"), "2+2"),
                "api-secret",
                urlopen=lambda *_args, **_kwargs: FakeStreamingResponse([
                    b"event: response.failed\n",
                    b'data: {"type":"response.failed","response":{"error":{"code":"server_error"}}}\n',
                    b"\n",
                ]),
            )

        self.assertEqual(error.exception.category, "server_error")
        self.assertEqual(
            error.exception.diagnostics,
            {"event_type": "response.failed", "error_code": "server_error"},
        )

    def test_responses_stream_without_terminal_event_is_network_error(self) -> None:
        with self.assertRaises(EndpointError) as error:
            execute_endpoint_request(
                build_endpoint_request(target(api_format="openai_responses"), "2+2"),
                "api-secret",
                urlopen=lambda *_args, **_kwargs: FakeStreamingResponse([
                    b"event: response.in_progress\n",
                    b'data: {"type":"response.in_progress"}\n',
                    b"\n",
                ]),
            )

        self.assertEqual(error.exception.category, "network_error")
        self.assertEqual(
            error.exception.diagnostics,
            {"exception_type": "IncompleteSSEStream"},
        )

    def test_chat_stream_assembles_text_usage_and_response_id(self) -> None:
        payload = execute_endpoint_request(
            build_endpoint_request(target(scan_profile="default"), "2+2"),
            "api-secret",
            urlopen=lambda *_args, **_kwargs: FakeStreamingResponse([
                b'data: {"id":"chat-stream","choices":[{"delta":{"role":"assistant","content":"2"}}]}\n',
                b"\n",
                b'data: {"id":"chat-stream","choices":[{"delta":{"content":"1"},"finish_reason":"stop"}]}\n',
                b"\n",
                b'data: {"id":"chat-stream","choices":[],"usage":{"prompt_tokens":10,"completion_tokens":2,"prompt_tokens_details":{"cached_tokens":4},"completion_tokens_details":{"reasoning_tokens":1}}}\n',
                b"\n",
                b"data: [DONE]\n",
                b"\n",
            ]),
        )

        result = parse_endpoint_response("openai_chat_completions", payload)

        self.assertEqual(result.text, "21")
        self.assertEqual(result.input_tokens, 10)
        self.assertEqual(result.output_tokens, 2)
        self.assertEqual(result.reasoning_tokens, 1)
        self.assertEqual(result.cached_input_tokens, 4)
        self.assertEqual(result.response_id, "chat-stream")

    def test_chat_stream_disconnect_after_partial_output_is_network_error(self) -> None:
        class DisconnectingResponse(FakeStreamingResponse):
            def readline(self, amount: int = -1) -> bytes:
                if self._index >= len(self._lines):
                    raise RemoteDisconnected("proxy token=api-secret")
                return super().readline(amount)

        with self.assertRaises(EndpointError) as error:
            execute_endpoint_request(
                build_endpoint_request(target(scan_profile="default"), "2+2"),
                "api-secret",
                urlopen=lambda *_args, **_kwargs: DisconnectingResponse([
                    b'data: {"id":"chat-stream","choices":[{"delta":{"content":"2"}}]}\n',
                    b"\n",
                ]),
            )

        self.assertEqual(error.exception.category, "network_error")
        self.assertEqual(
            error.exception.diagnostics,
            {"exception_type": "RemoteDisconnected"},
        )

    def test_chat_stream_without_done_is_network_error(self) -> None:
        with self.assertRaises(EndpointError) as error:
            execute_endpoint_request(
                build_endpoint_request(target(scan_profile="default"), "2+2"),
                "api-secret",
                urlopen=lambda *_args, **_kwargs: FakeStreamingResponse([
                    b'data: {"id":"chat-stream","choices":[{"delta":{"content":"21"}}]}\n',
                    b"\n",
                ]),
            )

        self.assertEqual(error.exception.category, "network_error")
        self.assertEqual(
            error.exception.diagnostics,
            {"exception_type": "IncompleteSSEStream"},
        )

    def test_anthropic_messages_request_uses_native_shape(self) -> None:
        request = build_endpoint_request(
            target(
                model_id="claude-fable-5",
                api_format="anthropic_messages",
                scan_profile="default",
            ),
            "2+2",
        )

        self.assertEqual(request.url, "https://example.com/v1/messages")
        self.assertEqual(request.api_format, "anthropic_messages")
        self.assertEqual(request.body, {
            "model": "claude-fable-5",
            "messages": [{"role": "user", "content": "2+2"}],
            "max_tokens": 128_000,
            "stream": True,
        })

    def test_anthropic_messages_request_can_disable_streaming(self) -> None:
        request = build_endpoint_request(
            target(
                model_id="claude-fable-5",
                api_format="anthropic_messages",
                scan_profile="high",
            ),
            "2+2",
            streaming=False,
        )

        self.assertIs(request.body["stream"], False)
        self.assertEqual(request.body["thinking"], {"type": "adaptive"})
        self.assertEqual(request.body["output_config"], {"effort": "high"})

    def test_anthropic_messages_effort_uses_adaptive_thinking(self) -> None:
        request = build_endpoint_request(
            target(
                model_id="claude-fable-5",
                api_format="anthropic_messages",
                scan_profile="high",
            ),
            "2+2",
        )

        self.assertEqual(request.body["thinking"], {"type": "adaptive"})
        self.assertEqual(request.body["output_config"], {"effort": "high"})

    def test_all_efforts_use_decimal_128k_output_budget_across_api_formats(self) -> None:
        cases = (
            ("openai_chat_completions", "max_tokens"),
            ("openai_responses", "max_output_tokens"),
            ("anthropic_messages", "max_tokens"),
        )
        efforts = ("default", "low", "medium", "high", "xhigh", "max")

        for api_format, field in cases:
            for effort in efforts:
                with self.subTest(api_format=api_format, effort=effort):
                    request = build_endpoint_request(
                        target(
                            model_id="claude-opus-4-8",
                            api_format=api_format,
                            scan_profile=effort,
                        ),
                        "2+2",
                    )

                    self.assertEqual(request.body[field], 128_000)

    def test_anthropic_messages_rejects_unknown_effort(self) -> None:
        with self.assertRaises(EndpointError) as error:
            build_endpoint_request(
                target(
                    model_id="claude-fable-5",
                    api_format="anthropic_messages",
                    scan_profile="ultra",
                ),
                "2+2",
            )

        self.assertEqual(error.exception.category, "protocol_mismatch")

    def test_anthropic_request_uses_native_auth_headers(self) -> None:
        captured: dict[str, object] = {}

        def urlopen(request, **kwargs):  # type: ignore[no-untyped-def]
            captured["headers"] = {
                key.lower(): value for key, value in request.header_items()
            }
            return FakeStreamingResponse([
                b"event: message_start\n",
                b'data: {"type":"message_start","message":{"id":"msg-header","usage":{"input_tokens":1}}}\n',
                b"\n",
                b"event: message_stop\n",
                b'data: {"type":"message_stop"}\n',
                b"\n",
            ])

        request = build_endpoint_request(
            target(api_format="anthropic_messages", scan_profile="default"),
            "2+2",
        )
        execute_endpoint_request(
            request,
            "api-secret",
            evaluation_id="md-eval-anthropic",
            urlopen=urlopen,
        )

        self.assertEqual(captured["headers"], {
            "x-api-key": "api-secret",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "accept": "text/event-stream",
            "x-modeldial-evaluation-id": "md-eval-anthropic",
            "user-agent": "ModelDial/EndpointClientV1",
        })

    def test_anthropic_stream_assembles_text_usage_and_response_id(self) -> None:
        payload = execute_endpoint_request(
            build_endpoint_request(
                target(api_format="anthropic_messages", scan_profile="default"),
                "2+2",
            ),
            "api-secret",
            urlopen=lambda *_args, **_kwargs: FakeStreamingResponse([
                b"event: message_start\n",
                b'data: {"type":"message_start","message":{"id":"msg-stream","usage":{"input_tokens":10,"cache_creation_input_tokens":2,"cache_read_input_tokens":4}}}\n',
                b"\n",
                b"event: content_block_start\n",
                b'data: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":"hidden"}}\n',
                b"\n",
                b"event: content_block_start\n",
                b'data: {"type":"content_block_start","index":1,"content_block":{"type":"text","text":"2"}}\n',
                b"\n",
                b"event: content_block_delta\n",
                b'data: {"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"1"}}\n',
                b"\n",
                b"event: message_delta\n",
                b'data: {"type":"message_delta","usage":{"output_tokens":3}}\n',
                b"\n",
                b"event: message_stop\n",
                b'data: {"type":"message_stop"}\n',
                b"\n",
            ]),
        )

        result = parse_endpoint_response("anthropic_messages", payload)

        self.assertEqual(result.text, "21")
        self.assertEqual(result.input_tokens, 16)
        self.assertEqual(result.output_tokens, 3)
        self.assertEqual(result.cached_input_tokens, 4)
        self.assertEqual(result.cache_write_input_tokens, 2)
        self.assertEqual(result.response_id, "msg-stream")

    def test_anthropic_stream_refusal_is_categorized(self) -> None:
        payload = execute_endpoint_request(
            build_endpoint_request(
                target(api_format="anthropic_messages", scan_profile="default"),
                "2+2",
            ),
            "api-secret",
            urlopen=lambda *_args, **_kwargs: FakeStreamingResponse([
                b"event: message_start\n",
                b'data: {"type":"message_start","message":{"id":"msg-refusal","usage":{"input_tokens":10}}}\n',
                b"\n",
                b"event: message_delta\n",
                b'data: {"type":"message_delta","delta":{"stop_reason":"refusal","stop_details":{"category":"content_policy_violation","explanation":"sensitive detail"}},"usage":{"output_tokens":0}}\n',
                b"\n",
                b"event: message_stop\n",
                b'data: {"type":"message_stop"}\n',
                b"\n",
            ]),
        )

        with self.assertRaises(EndpointError) as error:
            parse_endpoint_response("anthropic_messages", payload)

        self.assertEqual(error.exception.category, "model_refusal")
        self.assertEqual(error.exception.diagnostics, {
            "stop_reason": "refusal",
            "refusal_category": "content_policy_violation",
        })
        self.assertNotIn("sensitive detail", str(error.exception.diagnostics))

    def test_anthropic_stream_error_is_categorized_without_message(self) -> None:
        with self.assertRaises(EndpointError) as error:
            execute_endpoint_request(
                build_endpoint_request(
                    target(api_format="anthropic_messages", scan_profile="default"),
                    "2+2",
                ),
                "api-secret",
                urlopen=lambda *_args, **_kwargs: FakeStreamingResponse([
                    b"event: error\n",
                    b'data: {"type":"error","error":{"type":"rate_limit_error","message":"token=api-secret"}}\n',
                    b"\n",
                ]),
            )

        self.assertEqual(error.exception.category, "rate_limited")
        self.assertEqual(error.exception.diagnostics, {
            "event_type": "error",
            "error_code": "rate_limit_error",
        })
        self.assertNotIn("api-secret", str(error.exception.diagnostics))

    def test_anthropic_stream_without_message_stop_is_network_error(self) -> None:
        with self.assertRaises(EndpointError) as error:
            execute_endpoint_request(
                build_endpoint_request(
                    target(api_format="anthropic_messages", scan_profile="default"),
                    "2+2",
                ),
                "api-secret",
                urlopen=lambda *_args, **_kwargs: FakeStreamingResponse([
                    b"event: message_start\n",
                    b'data: {"type":"message_start","message":{"id":"msg-stream"}}\n',
                    b"\n",
                ]),
            )

        self.assertEqual(error.exception.category, "network_error")
        self.assertEqual(
            error.exception.diagnostics,
            {"exception_type": "IncompleteSSEStream"},
        )

    def test_chat_fixture_is_normalized(self) -> None:
        payload = json.loads(
            (FIXTURES / "endpoint_chat_success.json").read_text(encoding="utf-8")
        )

        result = parse_endpoint_response("openai_chat_completions", payload)

        self.assertEqual(result.text, "21")
        self.assertEqual(result.input_tokens, 128)
        self.assertEqual(result.output_tokens, 32)
        self.assertEqual(result.reasoning_tokens, 24)
        self.assertEqual(result.response_id, "chatcmpl-test")

    def test_responses_fixture_is_normalized(self) -> None:
        payload = json.loads(
            (FIXTURES / "endpoint_responses_success.json").read_text(encoding="utf-8")
        )

        result = parse_endpoint_response("openai_responses", payload)

        self.assertEqual(result.text, "21")
        self.assertEqual(result.input_tokens, 128)
        self.assertEqual(result.output_tokens, 32)
        self.assertEqual(result.reasoning_tokens, 24)
        self.assertEqual(result.response_id, "resp-test")

    def test_anthropic_messages_response_is_normalized(self) -> None:
        result = parse_endpoint_response("anthropic_messages", {
            "id": "msg-test",
            "type": "message",
            "content": [
                {"type": "thinking", "thinking": "hidden"},
                {"type": "text", "text": "21"},
            ],
            "usage": {"input_tokens": 128, "output_tokens": 32},
        })

        self.assertEqual(result.text, "21")
        self.assertEqual(result.input_tokens, 128)
        self.assertEqual(result.output_tokens, 32)
        self.assertIsNone(result.reasoning_tokens)
        self.assertEqual(result.response_id, "msg-test")

    def test_anthropic_messages_refusal_is_categorized(self) -> None:
        with self.assertRaises(EndpointError) as error:
            parse_endpoint_response("anthropic_messages", {
                "id": "msg-refusal",
                "type": "message",
                "content": [],
                "stop_reason": "refusal",
                "stop_details": {
                    "category": "content_policy_violation",
                    "explanation": "sensitive detail",
                },
                "usage": {"input_tokens": 128, "output_tokens": 0},
            })

        self.assertEqual(error.exception.category, "model_refusal")
        self.assertEqual(error.exception.diagnostics, {
            "stop_reason": "refusal",
            "refusal_category": "content_policy_violation",
        })
        self.assertNotIn("sensitive detail", str(error.exception.diagnostics))

    def test_http_error_is_categorized_without_response_body(self) -> None:
        def urlopen(*_: object, **__: object) -> FakeResponse:
            raise HTTPError(
                "https://example.com/v1/chat/completions?token=secret",
                401,
                "Unauthorized",
                {},
                None,
            )

        with self.assertRaises(EndpointError) as error:
            execute_endpoint_request(
                build_endpoint_request(target(), "2+2"),
                "api-secret",
                urlopen=urlopen,
            )

        self.assertEqual(error.exception.category, "authentication_failed")
        self.assertNotIn("secret", str(error.exception))
        self.assertNotIn("example.com", str(error.exception))

    def test_http_error_records_bounded_sanitized_diagnostics(self) -> None:
        response_body = b"sensitive response body"

        def urlopen(*_: object, **__: object) -> FakeResponse:
            raise HTTPError(
                "https://example.com/v1/chat/completions?token=secret",
                502,
                "Bad Gateway",
                {"Server": "edge-proxy", "X-Powered-By": "ARR/3.0"},
                io.BytesIO(response_body),
            )

        with self.assertRaises(EndpointError) as error:
            execute_endpoint_request(
                build_endpoint_request(target(), "2+2"),
                "api-secret",
                urlopen=urlopen,
            )

        self.assertEqual(error.exception.category, "server_error")
        self.assertEqual(error.exception.diagnostics["exception_type"], "HTTPError")
        self.assertEqual(error.exception.diagnostics["response_body_bytes"], len(response_body))
        self.assertEqual(
            error.exception.diagnostics["response_body_sha256"],
            hashlib.sha256(response_body).hexdigest(),
        )
        self.assertEqual(error.exception.diagnostics["header_server"], "edge-proxy")
        self.assertEqual(error.exception.diagnostics["header_x_powered_by"], "ARR/3.0")
        self.assertNotIn(response_body.decode(), str(error.exception.diagnostics))

    def test_remote_disconnect_is_categorized_without_raw_reason(self) -> None:
        def urlopen(*_: object, **__: object) -> FakeResponse:
            raise RemoteDisconnected("proxy token=api-secret")

        with self.assertRaises(EndpointError) as error:
            execute_endpoint_request(
                build_endpoint_request(target(), "2+2"),
                "api-secret",
                urlopen=urlopen,
            )

        self.assertEqual(error.exception.category, "network_error")
        self.assertEqual(
            error.exception.diagnostics,
            {"exception_type": "RemoteDisconnected"},
        )
        self.assertNotIn("api-secret", str(error.exception.diagnostics))

    def test_network_error_is_categorized_without_raw_reason(self) -> None:
        def urlopen(*_: object, **__: object) -> FakeResponse:
            raise URLError("proxy token=api-secret")

        with self.assertRaises(EndpointError) as error:
            execute_endpoint_request(
                build_endpoint_request(target(), "2+2"),
                "api-secret",
                urlopen=urlopen,
            )

        self.assertEqual(error.exception.category, "network_error")
        self.assertNotIn("api-secret", str(error.exception))


if __name__ == "__main__":
    unittest.main()
