from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scanner.local_provider_detection import detect_local_providers


class LocalProviderDetectionTest(unittest.TestCase):
    def test_module_keeps_system_python_compatibility(self) -> None:
        source = (
            Path(__file__).resolve().parent.parent
            / "scanner"
            / "local_provider_detection.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("str | None", source)

    def test_codex_login_is_ready_without_reading_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            auth_path = home / ".codex" / "auth.json"
            auth_path.parent.mkdir(parents=True)
            auth_path.write_text('{"token":"must-not-leak"}', encoding="utf-8")

            providers = detect_local_providers(
                home=home,
                which=lambda command: "/opt/homebrew/bin/codex" if command == "codex" else None,
            )

        codex = next(item for item in providers if item.provider_id == "codex")
        self.assertTrue(codex.detected)
        self.assertTrue(codex.importable)
        self.assertEqual(codex.status, "ready")
        self.assertNotIn("token", str(codex.to_dict()))
        self.assertNotIn("must-not-leak", str(codex.to_dict()))

    def test_claude_detection_requires_cli_login_check_without_reading_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            credentials_path = home / ".claude" / ".credentials.json"
            credentials_path.parent.mkdir(parents=True)
            credentials_path.write_text("{}", encoding="utf-8")

            providers = detect_local_providers(
                home=home,
                which=lambda command: "/usr/local/bin/claude" if command == "claude" else None,
            )

        claude = next(item for item in providers if item.provider_id == "claude")
        self.assertTrue(claude.detected)
        self.assertTrue(claude.importable)
        self.assertEqual(claude.status, "login_check_required")
        self.assertNotIn("token", str(claude.to_dict()))

    def test_grok_build_detection_never_reads_local_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            credential_path = home / ".grok" / "credentials.json"
            credential_path.parent.mkdir(parents=True)
            credential_path.write_text('{"token":"must-not-leak"}', encoding="utf-8")

            providers = detect_local_providers(
                home=home,
                which=lambda command: "/opt/homebrew/bin/grok" if command == "grok" else None,
            )

        grok = next(item for item in providers if item.provider_id == "grok")
        self.assertTrue(grok.detected)
        self.assertTrue(grok.importable)
        self.assertEqual(grok.status, "login_check_required")
        self.assertEqual(grok.source_id, "grok_local")
        self.assertEqual(grok.connection_id, "grok-local-default")
        self.assertNotIn("token", str(grok.to_dict()))
        self.assertNotIn("must-not-leak", str(grok.to_dict()))


if __name__ == "__main__":
    unittest.main()
