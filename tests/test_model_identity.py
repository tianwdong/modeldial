from __future__ import annotations

import unittest

from scanner.model_identity import (
    infer_reasoning_suffix_aliases,
    resolve_model_display_identity,
)


class ModelIdentityTest(unittest.TestCase):
    def test_reasoning_suffix_requires_multiple_sibling_models(self) -> None:
        aliases = infer_reasoning_suffix_aliases(
            [
                "gemini-3.6-flash-low",
                "gemini-3.6-flash-high",
                "gemini-3.6-flash-tiered",
            ]
        )

        self.assertEqual(
            aliases,
            {
                "gemini-3.6-flash-low": ("gemini-3.6-flash", "low"),
                "gemini-3.6-flash-high": ("gemini-3.6-flash", "high"),
            },
        )
        self.assertEqual(infer_reasoning_suffix_aliases(["model-high"]), {})

    def test_non_effort_suffixes_are_not_rewritten(self) -> None:
        aliases = infer_reasoning_suffix_aliases(
            ["model-tiered", "model-highspeed", "model-pro"]
        )

        self.assertEqual(aliases, {})

    def test_explicit_scan_profile_remains_the_display_effort(self) -> None:
        identity = resolve_model_display_identity(
            model_id="k3",
            scan_profile="high",
            family_id="k3",
            variant_id="high",
        )

        self.assertEqual(identity.model, "k3")
        self.assertEqual(identity.effort, "high")

    def test_existing_codex_default_display_behavior_is_preserved(self) -> None:
        identity = resolve_model_display_identity(
            model_id="gpt-test",
            scan_profile="codex_default",
        )

        self.assertEqual(identity.model, "gpt-test")
        self.assertEqual(identity.effort, "codex_default")


if __name__ == "__main__":
    unittest.main()
