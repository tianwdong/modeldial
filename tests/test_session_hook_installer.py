from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scanner.session_hook_installer import (
    install_claude_hooks,
    install_codex_hooks,
    uninstall_claude_hooks,
    uninstall_codex_hooks,
    uninstall_helper,
)


class SessionHookInstallerTest(unittest.TestCase):
    def test_codex_install_preserves_existing_hooks_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            hooks_path = root / "hooks.json"
            helper_path = root / "ModeldialSessionHook"
            hooks_path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "SessionStart": [
                                {
                                    "matcher": "startup|resume",
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "/existing/OpenIslandHooks",
                                            "timeout": 45,
                                        }
                                    ],
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )

            self.assertTrue(install_codex_hooks(hooks_path, helper_path))
            first = json.loads(hooks_path.read_text(encoding="utf-8"))
            self.assertFalse(install_codex_hooks(hooks_path, helper_path))
            second = json.loads(hooks_path.read_text(encoding="utf-8"))

            self.assertEqual(first, second)
            self.assertIn("/existing/OpenIslandHooks", json.dumps(first))
            for event_name in ("SessionStart", "UserPromptSubmit", "Stop"):
                self.assertIn(event_name, first["hooks"])
                self.assertIn("ModeldialSessionHook", json.dumps(first["hooks"][event_name]))

    def test_claude_install_preserves_unrelated_hooks_and_adds_session_end(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings_path = root / "settings.json"
            helper_path = root / "ModeldialSessionHook"
            settings_path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "PreToolUse": [
                                {
                                    "matcher": "Bash",
                                    "hooks": [
                                        {"type": "command", "command": "rtk hook claude"}
                                    ],
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )

            self.assertTrue(install_claude_hooks(settings_path, helper_path))
            installed = json.loads(settings_path.read_text(encoding="utf-8"))

            self.assertIn("rtk hook claude", json.dumps(installed))
            for event_name in (
                "SessionStart",
                "UserPromptSubmit",
                "Stop",
                "StopFailure",
                "SessionEnd",
            ):
                self.assertIn(event_name, installed["hooks"])
                self.assertIn("ModeldialSessionHook", json.dumps(installed["hooks"][event_name]))

    def test_install_replaces_legacy_modelpilot_hook_without_touching_other_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings_path = root / "settings.json"
            helper_path = root / "modeldial" / "bin" / "ModeldialSessionHook"
            settings_path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "Stop": [
                                {
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": (
                                                "/Users/example/Library/Application Support/"
                                                "ModelPilot/bin/ModelPilotSessionHook --source claude"
                                            ),
                                        }
                                    ]
                                },
                                {
                                    "hooks": [
                                        {"type": "command", "command": "keep-this-hook"}
                                    ]
                                },
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )

            self.assertTrue(install_claude_hooks(settings_path, helper_path))
            installed = settings_path.read_text(encoding="utf-8")

            self.assertNotIn("ModelPilotSessionHook", installed)
            self.assertIn("ModeldialSessionHook", installed)
            self.assertIn("keep-this-hook", installed)
            self.assertFalse(install_claude_hooks(settings_path, helper_path))

    def test_codex_uninstall_removes_only_modeldial_handlers_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            hooks_path = root / "hooks.json"
            helper_path = root / "ModeldialSessionHook"
            hooks_path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "Stop": [
                                {
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "/existing/keep-this-hook",
                                        }
                                    ]
                                }
                            ],
                            "PreToolUse": [
                                {
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "/existing/other-hook",
                                        }
                                    ]
                                }
                            ],
                        }
                    }
                ),
                encoding="utf-8",
            )

            install_codex_hooks(hooks_path, helper_path)
            payload = json.loads(hooks_path.read_text(encoding="utf-8"))
            payload["hooks"]["Stop"][1]["hooks"].append(
                {"type": "command", "command": "/existing/keep-with-modeldial"}
            )
            hooks_path.write_text(json.dumps(payload), encoding="utf-8")

            self.assertTrue(uninstall_codex_hooks(hooks_path, helper_path))
            after_first = json.loads(hooks_path.read_text(encoding="utf-8"))
            self.assertFalse(uninstall_codex_hooks(hooks_path, helper_path))
            after_second = json.loads(hooks_path.read_text(encoding="utf-8"))

            self.assertEqual(after_first, after_second)
            self.assertIn("/existing/keep-this-hook", json.dumps(after_first))
            self.assertIn("/existing/keep-with-modeldial", json.dumps(after_first))
            self.assertIn("/existing/other-hook", json.dumps(after_first))
            self.assertNotIn("ModeldialSessionHook", json.dumps(after_first))

    def test_claude_uninstall_removes_modeldial_hooks_and_preserves_third_party(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings_path = root / "settings.json"
            helper_path = root / "ModeldialSessionHook"
            settings_path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "PreToolUse": [
                                {
                                    "matcher": "Bash",
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "rtk hook claude",
                                        }
                                    ],
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )

            install_claude_hooks(settings_path, helper_path)
            self.assertTrue(uninstall_claude_hooks(settings_path, helper_path))
            uninstalled = json.loads(settings_path.read_text(encoding="utf-8"))

            self.assertIn("rtk hook claude", json.dumps(uninstalled))
            self.assertNotIn("ModeldialSessionHook", json.dumps(uninstalled))
            self.assertFalse(uninstall_claude_hooks(settings_path, helper_path))

    def test_uninstall_rejects_invalid_hook_shape_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            hooks_path = root / "hooks.json"
            helper_path = root / "ModeldialSessionHook"
            hooks_path.write_text(json.dumps({"hooks": []}), encoding="utf-8")
            original = hooks_path.read_bytes()

            with self.assertRaisesRegex(ValueError, "Codex hooks must be an object"):
                uninstall_codex_hooks(hooks_path, helper_path)

            self.assertEqual(hooks_path.read_bytes(), original)

    def test_uninstall_helper_is_safe_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "modeldial_session_hook.py"
            source_path.write_text("owned helper", encoding="utf-8")
            helper_path = root / "ModeldialSessionHook"
            helper_path.write_text("owned helper", encoding="utf-8")
            other_path = root / "third-party-helper"
            other_path.write_text("keep me", encoding="utf-8")

            self.assertTrue(uninstall_helper(source_path, helper_path))
            self.assertFalse(helper_path.exists())
            self.assertFalse(uninstall_helper(source_path, helper_path))
            self.assertFalse(uninstall_helper(source_path, other_path))
            self.assertTrue(other_path.exists())

    def test_uninstall_helper_preserves_replaced_same_name_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "modeldial_session_hook.py"
            source_path.write_text("modeldial helper", encoding="utf-8")
            helper_path = root / "ModeldialSessionHook"
            helper_path.write_text("third-party replacement", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "unrecognized helper"):
                uninstall_helper(source_path, helper_path)

            self.assertEqual(
                helper_path.read_text(encoding="utf-8"),
                "third-party replacement",
            )

    def test_uninstall_script_reports_failure_and_keeps_helper_on_bad_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_root = root / "data"
            helper_path = data_root / "bin" / "ModeldialSessionHook"
            helper_path.parent.mkdir(parents=True)
            helper_path.write_text("owned helper", encoding="utf-8")
            hooks_path = root / "hooks.json"
            hooks_path.write_text(json.dumps({"hooks": []}), encoding="utf-8")
            settings_path = root / "settings.json"
            settings_path.write_text("{}", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/install_session_observer.py",
                    "--uninstall",
                    "--codex-hooks",
                    str(hooks_path),
                    "--claude-settings",
                    str(settings_path),
                ],
                cwd=Path(__file__).resolve().parent.parent,
                env={**os.environ, "MODELDIAL_DATA_DIR": str(data_root)},
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("session observer uninstall failed", result.stderr)
            self.assertIn("Codex hooks must be an object", result.stderr)
            self.assertTrue(helper_path.exists())


if __name__ == "__main__":
    unittest.main()
