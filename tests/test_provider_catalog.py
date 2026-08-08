from __future__ import annotations

import unittest

from scanner.provider_catalog import (
    list_provider_catalog,
    provider_catalog_payload,
    resolve_candidate_catalog_identity,
    resolve_connection_catalog_metadata,
    resolve_model_default_reasoning_effort,
    resolve_model_reasoning_efforts,
)


class ProviderCatalogTest(unittest.TestCase):
    def test_catalog_exposes_executable_connection_defaults(self) -> None:
        providers = {
            item["provider_id"]: item for item in provider_catalog_payload()
        }

        deepseek = providers["deepseek"]
        self.assertTrue(deepseek["connection_supported"])
        self.assertEqual(deepseek["default_api_format"], "openai_chat_completions")
        self.assertEqual(
            deepseek["default_model_ids"],
            ["deepseek-v4-flash", "deepseek-v4-pro"],
        )
        self.assertEqual(
            deepseek["api_key_url"], "https://platform.deepseek.com/api_keys"
        )
        variants = {
            item["variant_id"]: item
            for item in deepseek["families"][0]["variants"]
        }
        self.assertEqual(
            variants["flash"]["reasoning_efforts"],
            ["low", "high", "max"],
        )
        self.assertEqual(
            variants["pro"]["reasoning_efforts"],
            ["high", "max"],
        )
        self.assertEqual(variants["flash"]["default_reasoning_effort"], "high")
        self.assertNotIn("none", variants["flash"]["reasoning_efforts"])

        anthropic = providers["anthropic"]
        self.assertFalse(anthropic["connection_supported"])
        self.assertEqual(anthropic["availability_note"], "原生协议适配器待接入")

        grok = providers["xai"]
        self.assertTrue(grok["featured"])
        self.assertEqual(grok["display_name"], "Grok API")
        self.assertEqual(grok["default_base_url"], "https://api.x.ai/v1")
        self.assertEqual(grok["default_api_format"], "openai_responses")
        self.assertEqual(grok["default_model_ids"], ["grok-4.5"])
        self.assertEqual(
            grok["api_key_url"], "https://console.x.ai/team/default/api-keys"
        )

    def test_catalog_seed_covers_common_providers(self) -> None:
        provider_ids = {provider.provider_id for provider in list_provider_catalog()}

        self.assertTrue(
            {
                "deepseek",
                "openai",
                "openrouter",
                "anthropic",
                "gemini",
                "xai",
                "moonshot",
                "zhipu",
                "z-ai",
                "minimax",
            }.issubset(provider_ids)
        )

    def test_resolve_connection_catalog_metadata_prefers_known_base_url(self) -> None:
        resolved = resolve_connection_catalog_metadata(
            source_id="custom_endpoint",
            name="DeepSeek V4",
            base_url="https://api.deepseek.com/v1",
            provider_preset="generic",
        )

        self.assertEqual(resolved.provider_id, "deepseek")
        self.assertEqual(resolved.provider_display_name, "DeepSeek")
        self.assertEqual(resolved.auth_mode, "api_key")
        self.assertEqual(resolved.catalog_source, "catalog_inferred")

    def test_deepseek_reasoning_contract_resolves_by_provider_or_base_url(self) -> None:
        self.assertEqual(
            resolve_model_reasoning_efforts(
                provider_id="deepseek",
                model_id="deepseek-v4-flash",
            ),
            ("low", "high", "max"),
        )
        self.assertEqual(
            resolve_model_reasoning_efforts(
                base_url="https://api.deepseek.com/v1",
                model_id="deepseek-v4-pro",
            ),
            ("high", "max"),
        )
        self.assertEqual(
            resolve_model_default_reasoning_effort(
                provider_id="deepseek",
                model_id="deepseek-v4-pro",
            ),
            "high",
        )

    def test_resolve_connection_catalog_metadata_recognizes_xai_base_url(self) -> None:
        resolved = resolve_connection_catalog_metadata(
            source_id="custom_endpoint",
            name="Grok",
            base_url="https://api.x.ai/v1",
            provider_preset="generic",
        )

        self.assertEqual(resolved.provider_id, "xai")
        self.assertEqual(resolved.provider_display_name, "Grok API")
        self.assertEqual(resolved.auth_mode, "api_key")
        self.assertEqual(resolved.catalog_source, "catalog_inferred")

    def test_resolve_connection_catalog_metadata_keeps_grok_build_local_auth_distinct(self) -> None:
        resolved = resolve_connection_catalog_metadata(
            source_id="grok_local",
            name="Grok Build Local",
            base_url=None,
            provider_preset="generic",
        )

        self.assertEqual(resolved.provider_id, "grok-build")
        self.assertEqual(resolved.provider_display_name, "Grok Build")
        self.assertEqual(resolved.auth_mode, "local_import")
        self.assertEqual(resolved.catalog_source, "local_builtin")

    def test_resolve_connection_catalog_metadata_keeps_unknown_gateway_manual(self) -> None:
        resolved = resolve_connection_catalog_metadata(
            source_id="custom_endpoint",
            name="Team Gateway",
            base_url="http://127.0.0.1:18080/v1",
            provider_preset="generic",
        )

        self.assertEqual(resolved.provider_id, "custom")
        self.assertEqual(resolved.provider_display_name, "Team Gateway")
        self.assertEqual(resolved.auth_mode, "api_key")
        self.assertEqual(resolved.catalog_source, "manual")

    def test_resolve_candidate_catalog_identity_normalizes_deepseek_family(self) -> None:
        resolved = resolve_candidate_catalog_identity(
            model_id="deepseek-v4-pro",
            provider_id="deepseek",
        )

        self.assertEqual(resolved.family_id, "deepseek-v4")
        self.assertEqual(resolved.variant_id, "pro")

    def test_resolve_candidate_catalog_identity_uses_global_exact_match(self) -> None:
        resolved = resolve_candidate_catalog_identity(model_id="gpt-5.5")

        self.assertEqual(resolved.family_id, "gpt-5.5")
        self.assertIsNone(resolved.variant_id)


if __name__ == "__main__":
    unittest.main()
