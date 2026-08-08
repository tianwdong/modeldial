from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scanner.costing import (
    PRICING_SNAPSHOT,
    current_pricing_snapshot_id,
    estimate_reference_cost,
    install_pricing_snapshot,
)


class CostingTest(unittest.TestCase):
    def test_uses_versioned_pricing_snapshot(self) -> None:
        self.assertTrue(PRICING_SNAPSHOT.startswith("pricing-v1-"))

    def test_runtime_snapshot_install_updates_rates_and_identity_atomically(self) -> None:
        default_snapshot = (
            Path(__file__).resolve().parent.parent
            / "scanner"
            / "pricing_snapshot.json"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / "pricing_snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "snapshot_id": "pricing-v1-runtime-test",
                        "models": {
                            "gpt-5.6-luna": {
                                "input_per_token": 0.2e-6,
                                "cached_input_per_token": 0.02e-6,
                                "cache_write_input_per_token": 0.25e-6,
                                "output_per_token": 1.2e-6,
                            }
                        },
                        "aliases": {},
                    }
                ),
                encoding="utf-8",
            )
            try:
                installed = install_pricing_snapshot(snapshot_path)
                estimate = estimate_reference_cost(
                    "gpt-5.6-luna",
                    input_tokens=1000,
                    cached_input_tokens=400,
                    output_tokens=100,
                )

                self.assertEqual(installed, "pricing-v1-runtime-test")
                self.assertEqual(current_pricing_snapshot_id(), installed)
                self.assertEqual(estimate.pricing_snapshot, installed)
                self.assertAlmostEqual(
                    estimate.usd or 0,
                    600 * 0.2e-6 + 400 * 0.02e-6 + 100 * 1.2e-6,
                )
            finally:
                install_pricing_snapshot(default_snapshot)

    def test_estimate_separates_cached_input_from_standard_input(self) -> None:
        estimate = estimate_reference_cost(
            "gpt-5.4",
            input_tokens=1000,
            cached_input_tokens=600,
            output_tokens=100,
        )

        self.assertEqual(estimate.status, "estimated")
        self.assertEqual(estimate.pricing_snapshot, PRICING_SNAPSHOT)
        self.assertAlmostEqual(
            estimate.usd or 0,
            400 * 2.5e-6 + 600 * 0.25e-6 + 100 * 15e-6,
        )

    def test_estimate_applies_long_context_rates_to_whole_request(self) -> None:
        estimate = estimate_reference_cost(
            "gpt-5.6-terra",
            input_tokens=300_000,
            cached_input_tokens=200_000,
            output_tokens=1_000,
        )

        self.assertAlmostEqual(
            estimate.usd or 0,
            100_000 * 2e-6 * 2 + 200_000 * 0.2e-6 * 2 + 1_000 * 12e-6 * 1.5,
        )

    def test_estimate_prices_cache_writes_separately(self) -> None:
        estimate = estimate_reference_cost(
            "gpt-5.6-terra",
            input_tokens=1000,
            cached_input_tokens=400,
            cache_write_input_tokens=200,
            output_tokens=100,
        )

        self.assertAlmostEqual(
            estimate.usd or 0,
            400 * 2e-6 + 400 * 0.2e-6 + 200 * 2.5e-6 + 100 * 12e-6,
        )

    def test_provider_prefix_uses_same_standard_model_price(self) -> None:
        estimate = estimate_reference_cost(
            "openai/gpt-5.6-terra",
            input_tokens=100,
            cached_input_tokens=0,
            output_tokens=10,
        )

        self.assertEqual(estimate.status, "estimated")
        self.assertAlmostEqual(estimate.usd or 0, 100 * 2e-6 + 10 * 12e-6)

    def test_unknown_model_is_unpriced_instead_of_zero_cost(self) -> None:
        estimate = estimate_reference_cost(
            "unknown-model",
            input_tokens=100,
            cached_input_tokens=0,
            output_tokens=20,
        )

        self.assertEqual(estimate.status, "unpriced")
        self.assertIsNone(estimate.usd)

    def test_estimate_supports_non_codex_provider_models(self) -> None:
        cases = {
            "claude-sonnet-4-6": (3e-6, 0.3e-6, 15e-6),
            "gemini-2.5-flash": (0.3e-6, 0.03e-6, 2.5e-6),
            "deepseek-v4-flash": (0.14e-6, 0.0028e-6, 0.28e-6),
            "grok-4.5": (2e-6, 0.5e-6, 6e-6),
            "kimi-for-coding": (0.95e-6, 0.15e-6, 4e-6),
            "glm-5.2": (1e-6, 0.2e-6, 3.2e-6),
            "MiniMax-M2.5": (0.3e-6, 0.03e-6, 1.2e-6),
        }

        for model, rates in cases.items():
            with self.subTest(model=model):
                estimate = estimate_reference_cost(
                    model,
                    input_tokens=1000,
                    cached_input_tokens=400,
                    output_tokens=100,
                )

                self.assertEqual(estimate.status, "estimated")
                self.assertAlmostEqual(
                    estimate.usd or 0,
                    600 * rates[0] + 400 * rates[1] + 100 * rates[2],
                )

    def test_reasoning_suffix_is_not_part_of_pricing_identity(self) -> None:
        estimate = estimate_reference_cost(
            "gemini-3.1-pro-high",
            input_tokens=100,
            cached_input_tokens=0,
            output_tokens=10,
        )

        self.assertEqual(estimate.status, "estimated")
        self.assertAlmostEqual(estimate.usd or 0, 100 * 2e-6 + 10 * 12e-6)

    def test_reasoning_tokens_are_not_added_twice_when_already_in_output(self) -> None:
        estimate = estimate_reference_cost(
            "gpt-5.4",
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=100,
            reasoning_output_tokens=60,
        )

        self.assertAlmostEqual(estimate.usd or 0, 100 * 15e-6)

    def test_missing_usage_is_unavailable(self) -> None:
        estimate = estimate_reference_cost(
            "gpt-5.4",
            input_tokens=None,
            cached_input_tokens=None,
            output_tokens=None,
        )

        self.assertEqual(estimate.status, "unavailable")
        self.assertIsNone(estimate.usd)


if __name__ == "__main__":
    unittest.main()
