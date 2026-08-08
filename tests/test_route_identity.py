from __future__ import annotations

import unittest

from scanner.route_identity import build_route_fingerprint


class RouteIdentityTest(unittest.TestCase):
    def test_fingerprint_is_stable_for_equivalent_endpoint_routes(self) -> None:
        first = build_route_fingerprint(
            source_id="custom_endpoint",
            connection_id="endpoint-1",
            connection_mode="api",
            api_format="openai_chat_completions",
            provider_preset="generic",
            base_url="https://example.com/v1/",
            model_id="gpt-5.6-terra",
            scan_profile="high",
        )
        second = build_route_fingerprint(
            source_id=" custom_endpoint ",
            connection_id="endpoint-1",
            connection_mode="API",
            api_format="openai_chat_completions",
            provider_preset="generic",
            base_url="https://example.com/v1",
            model_id="gpt-5.6-terra",
            scan_profile="HIGH",
        )

        self.assertEqual(first, second)
        self.assertTrue(first.startswith("route-v1:sha256:"))

    def test_route_semantic_changes_produce_different_fingerprints(self) -> None:
        base = {
            "source_id": "custom_endpoint",
            "connection_id": "endpoint-1",
            "connection_mode": "api",
            "api_format": "openai_chat_completions",
            "provider_preset": "generic",
            "base_url": "https://example.com/v1",
            "model_id": "gpt-5.6-terra",
            "scan_profile": "high",
        }
        original = build_route_fingerprint(**base)

        for field, value in (
            ("base_url", "https://gateway.example.com/v1"),
            ("api_format", "openai_responses"),
            ("provider_preset", "openrouter"),
            ("model_id", "gpt-5.6-sol"),
            ("scan_profile", "xhigh"),
        ):
            with self.subTest(field=field):
                changed = build_route_fingerprint(**{**base, field: value})
                self.assertNotEqual(original, changed)

    def test_fingerprint_does_not_expose_endpoint_text(self) -> None:
        fingerprint = build_route_fingerprint(
            source_id="custom_endpoint",
            connection_id="endpoint-1",
            connection_mode="api",
            api_format="openai_chat_completions",
            provider_preset="generic",
            base_url="https://private-gateway.example.com/v1",
            model_id="private-model",
            scan_profile="high",
        )

        self.assertNotIn("private-gateway", fingerprint)
        self.assertNotIn("private-model", fingerprint)


if __name__ == "__main__":
    unittest.main()
