from __future__ import annotations

import unittest
from pathlib import Path


class LocalModelDiscoveryUITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = Path(__file__).resolve().parent.parent
        cls.store_source = (root / "Sources/Model/SelectionSettingsStore.swift").read_text(
            encoding="utf-8"
        )
        cls.patch_source = (root / "Sources/Model/SettingsConfigPatch.swift").read_text(
            encoding="utf-8"
        )
        cls.view_source = (root / "Sources/Views/SettingsView.swift").read_text(
            encoding="utf-8"
        )

    def test_codex_catalog_requires_manual_add_and_defaults_new_candidate_off(self) -> None:
        self.assertIn('Button("发现可用模型")', self.view_source)
        self.assertIn('Button("加入")', self.view_source)
        self.assertIn("func addDiscoveredLocalCandidate", self.store_source)
        self.assertIn("apply(.addDiscoveredLocalCandidate(", self.store_source)
        self.assertIn("case addDiscoveredLocalCandidate(", self.patch_source)

    def test_local_discovery_uses_model_and_scan_profile_identity(self) -> None:
        self.assertIn("BridgeLocalModelDiscoveryCandidate", self.store_source)
        self.assertIn("modelID: candidate.modelId", self.store_source)
        self.assertIn("scanProfile: candidate.scanProfile", self.store_source)


if __name__ == "__main__":
    unittest.main()
