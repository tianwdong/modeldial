from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scanner.codex_model_catalog import (
    CodexCatalogError,
    CodexCatalogCandidate,
    _discover_with_binary,
    _default_binary_candidates,
    parse_model_list_page,
)
from scanner.config_store import ConfigStore
from scanner.native_bridge import discover_local_models


class CodexModelCatalogTest(unittest.TestCase):
    def test_model_catalog_fails_closed_when_stderr_budget_is_exceeded(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-catalog-output-") as temp_dir:
            binary = Path(temp_dir) / "noisy-codex"
            binary.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "sys.stderr.write('x' * 1000000)\n"
                "sys.stderr.flush()\n"
                "for _line in sys.stdin:\n"
                "    pass\n",
                encoding="utf-8",
            )
            binary.chmod(0o755)
            with patch(
                "scanner.codex_model_catalog.CODEX_CATALOG_OUTPUT_LIMIT_BYTES",
                1024,
            ):
                with self.assertRaisesRegex(CodexCatalogError, "输出超出限制"):
                    _discover_with_binary(binary, timeout_seconds=2)

    def test_catalog_uses_the_same_resolved_runtime_as_scanning(self) -> None:
        with patch(
            "scanner.codex_model_catalog.resolve_codex_executable",
            return_value="/runtime/codex",
        ):
            self.assertEqual(_default_binary_candidates(), (Path("/runtime/codex"),))

    def test_model_list_expands_only_server_reported_reasoning_efforts(self) -> None:
        candidates = parse_model_list_page(
            {
                "data": [
                    {
                        "id": "gpt-5.6-sol",
                        "model": "gpt-5.6-sol",
                        "displayName": "GPT-5.6-Sol",
                        "defaultReasoningEffort": "low",
                        "supportedReasoningEfforts": [
                            {"reasoningEffort": "low"},
                            {"reasoningEffort": "max"},
                            {"reasoningEffort": "ultra"},
                        ],
                    },
                    {
                        "id": "codex-auto-review",
                        "model": "codex-auto-review",
                        "displayName": "Auto review",
                        "defaultReasoningEffort": "medium",
                        "supportedReasoningEfforts": [
                            {"reasoningEffort": "medium"}
                        ],
                    },
                ]
            }
        )

        self.assertEqual(
            [(item.model_id, item.scan_profile) for item in candidates],
            [
                ("gpt-5.6-sol", "low"),
                ("gpt-5.6-sol", "max"),
                ("gpt-5.6-sol", "ultra"),
            ],
        )
        self.assertTrue(candidates[0].is_default)

    def test_local_discovery_classifies_exact_model_effort_pairs_without_saving(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            store = ConfigStore(config_path)
            store.save(store.load())
            before = config_path.read_text(encoding="utf-8")

            response = discover_local_models(
                "codex",
                config_store=store,
                discoverer=lambda: (
                    CodexCatalogCandidate(
                        model_id="gpt-5.6-sol",
                        model_display_name="GPT-5.6-Sol",
                        scan_profile="medium",
                        is_default=False,
                    ),
                    CodexCatalogCandidate(
                        model_id="gpt-5.6-sol",
                        model_display_name="GPT-5.6-Sol",
                        scan_profile="max",
                        is_default=False,
                    ),
                    CodexCatalogCandidate(
                        model_id="gpt-5.6-sol",
                        model_display_name="GPT-5.6-Sol",
                        scan_profile="ultra",
                        is_default=False,
                    ),
                ),
            )

            after = config_path.read_text(encoding="utf-8")

        self.assertTrue(response["ok"])
        self.assertEqual(response["connection_id"], "codex-local-default")
        self.assertEqual(
            [(item["scan_profile"], item["configured"]) for item in response["candidates"]],
            [("medium", True), ("max", False), ("ultra", False)],
        )
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
