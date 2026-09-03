from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from scanner.active_run_store import ActiveRunStore
from scanner.config_store import ConfigStore
from scanner.history_store import HistoryStore
from scanner.models import ConnectionConfig, ModelCandidateConfig
from scanner.native_bridge import discover_connection_models, verify_endpoint_connection
from scanner.service import MonitorService
from tests.question_pack_fixtures import DEFAULT_EVALUATION_COUNT


def _q1_gold_response() -> str:
    gold_path = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "session_bundle_relation_repair_v1_gold.json"
    )
    payload = json.loads(gold_path.read_text(encoding="utf-8"))
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _q5_gold_response() -> str:
    gold_path = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "cache_propagation_certificate_v1_gold.json"
    )
    payload = json.loads(gold_path.read_text(encoding="utf-8"))
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class _EndpointHandler(BaseHTTPRequestHandler):
    requests: list[tuple[str, str | None]] = []

    def do_GET(self) -> None:
        self.requests.append((self.path, self.headers.get("Authorization")))
        if self.path != "/v1/models":
            self.send_error(404)
            return
        self._send_json({"data": [{"id": "gpt-smoke"}]})

    def do_POST(self) -> None:
        self.requests.append((self.path, self.headers.get("Authorization")))
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or "0")
        request = json.loads(self.rfile.read(length).decode("utf-8"))
        if request.get("stream") is not True:
            self.send_error(400)
            return
        prompt = str(request["messages"][0]["content"])
        answer = self._answer_for(prompt)
        self._send_chat_stream(answer)

    def log_message(self, *_: object) -> None:
        return

    @staticmethod
    def _answer_for(prompt: str) -> str:
        if prompt == "Reply with only OK.":
            return "OK"
        if "repair a fixed 18-relation black-box regression suite" in prompt:
            return _q1_gold_response()
        if "Design exactly two cache-regression portfolios" in prompt:
            return _q5_gold_response()
        if "black bag containing candies" in prompt:
            return "21"
        if any(
            marker in prompt
            for marker in (
                "constructing counterexamples for a retry planner",
                "constructing compact scenarios that expose incorrect audit",
                "designing compact regression scenarios",
                "designing regression tests for a function named `run_scan`",
                "A CI planner upgrade has produced inconsistent audit cards",
            )
        ):
            return "{}"
        raise AssertionError("unexpected smoke prompt")

    def _send_json(self, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_chat_stream(self, answer: str) -> None:
        events = [
            {
                "id": "chatcmpl-smoke",
                "choices": [{"delta": {"role": "assistant", "content": answer}}],
            },
            {
                "id": "chatcmpl-smoke",
                "choices": [{"delta": {}, "finish_reason": "stop"}],
            },
            {
                "id": "chatcmpl-smoke",
                "choices": [],
                "usage": {
                    "prompt_tokens": 128,
                    "completion_tokens": 32,
                    "completion_tokens_details": {"reasoning_tokens": 24},
                },
            },
        ]
        encoded = b"".join(
            f"data: {json.dumps(event)}\n\n".encode("utf-8")
            for event in events
        ) + b"data: [DONE]\n\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class EndpointSmokeTest(unittest.TestCase):
    def test_discovery_connection_test_and_single_scan_share_one_endpoint(self) -> None:
        _EndpointHandler.requests = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), _EndpointHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                config_store = ConfigStore(root / "config.json")
                history_store = HistoryStore(root / "history.jsonl")
                active_run_store = ActiveRunStore(root / "active_run.json")
                config = config_store.load()
                candidate = ModelCandidateConfig(
                    id="endpoint-smoke:gpt-smoke:default",
                    connection_id="endpoint-smoke",
                    model_id="gpt-smoke",
                    display_name="gpt-smoke",
                    enabled=False,
                    scan_profile="default",
                )
                config.model_ingress.connections.append(
                    ConnectionConfig(
                        id="endpoint-smoke",
                        source_id="custom_endpoint",
                        name="Endpoint Smoke",
                        enabled=True,
                        api_format="openai_chat_completions",
                        provider_preset="generic",
                        base_url=f"http://127.0.0.1:{server.server_port}/v1",
                        api_key_ref="env:MODELDIAL_SMOKE_KEY",
                        model_candidates=[candidate],
                    )
                )
                config.system.use_mock_results = False
                config_store.save(config)

                with patch.dict(os.environ, {"MODELDIAL_SMOKE_KEY": "smoke-secret"}):
                    discovery = discover_connection_models(
                        "endpoint-smoke",
                        config_store=config_store,
                    )
                    verification = verify_endpoint_connection(
                        "endpoint-smoke",
                        "gpt-smoke",
                        config_store=config_store,
                    )
                    results = MonitorService(
                        config_store=config_store,
                        history_store=history_store,
                        active_run_store=active_run_store,
                    ).run_enabled_targets(
                        requested_candidate_ids=[candidate.id],
                        selection_mode="single",
                    )

                self.assertEqual(discovery["models"], ["gpt-smoke"])
                self.assertTrue(verification["ok"])
                self.assertEqual(len(results), DEFAULT_EVALUATION_COUNT)
                self.assertTrue(all(item.candidate_id == candidate.id for item in results))
                self.assertTrue(all(item.source_mode == "api" for item in results))
                self.assertTrue(any(item.answer_ok for item in results))
                self.assertTrue(any(not item.answer_ok for item in results))
                self.assertTrue(all(item.error_message is None for item in results))
                self.assertTrue(
                    all(auth == "Bearer smoke-secret" for _, auth in _EndpointHandler.requests)
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
