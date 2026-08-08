from __future__ import annotations

from pathlib import Path
import unittest


class BrandIdentityContractTest(unittest.TestCase):
    def test_current_product_surfaces_only_use_modeldial(self) -> None:
        root = Path(__file__).resolve().parent.parent
        current_surface_paths = [
            root / "Sources",
            root / "Resources",
            root / "scanner",
            root / "scripts",
            root / "questions",
        ]
        legacy_allowlist = {
            root / "scanner/session_registry.py",
            root / "scanner/session_hook_installer.py",
            root / "scanner/codex_current_model.py",
            root / "scripts/modeldial_session_hook.py",
            root / "Sources/Model/LocalEncryptedSecretStore.swift",
            root / "Sources/Model/RecommendationNotificationEngine.swift",
            root / "Sources/Model/IslandTargetDisplayStore.swift",
            root / "Sources/Model/KeychainSecretStore.swift",
        }
        legacy_terms = ("ModelPilot", "modelpilot", "MODEL_PILOT", "Model Pilot")

        violations: list[str] = []
        for base in current_surface_paths:
            for path in base.rglob("*"):
                if not path.is_file() or path in legacy_allowlist:
                    continue
                if path.suffix not in {".swift", ".py", ".json", ".xcstrings"}:
                    continue
                source = path.read_text(encoding="utf-8")
                if any(term in source for term in legacy_terms):
                    violations.append(str(path.relative_to(root)))

        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
