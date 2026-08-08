from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scanner import model_sessions as model_sessions_module
from scanner import native_bridge as native_bridge_module
from scanner.process_environment import build_child_environment


class ProcessEnvironmentTest(unittest.TestCase):
    def test_runtime_environment_is_preserved_and_credentials_are_dropped(self) -> None:
        source = {
            "PATH": "/usr/bin",
            "HOME": "/Users/tester",
            "TMPDIR": "/tmp/modeldial",
            "LANG": "en_US.UTF-8",
            "LC_CTYPE": "UTF-8",
            "TERM": "xterm-256color",
            "OPENAI_API_KEY": "openai-secret",
            "GITHUB_TOKEN": "github-secret",
            "SSH_AUTH_SOCK": "/tmp/agent.sock",
            "GIT_CONFIG_COUNT": "1",
            "MODELDIAL_CLOUD_API_KEY": "cloud-secret",
            "CODEX_API_KEY": "parent-secret",
        }

        environment = build_child_environment(environ=source)

        self.assertEqual(
            environment,
            {
                "PATH": "/usr/bin",
                "HOME": "/Users/tester",
                "TMPDIR": "/tmp/modeldial",
                "LANG": "en_US.UTF-8",
                "LC_CTYPE": "UTF-8",
                "TERM": "xterm-256color",
            },
        )
        self.assertNotIn("CODEX_API_KEY", environment)

    def test_only_explicit_modeldial_configuration_and_codex_key_are_injected(self) -> None:
        environment = build_child_environment(
            environ={"PATH": "/usr/bin", "OPENAI_API_KEY": "not-forwarded"},
            overrides={
                "MODELDIAL_SCAN_SESSION": "1",
                "CODEX_API_KEY": "explicit-secret",
                "OPENAI_API_KEY": "ignored-secret",
            },
        )

        self.assertEqual(environment["MODELDIAL_SCAN_SESSION"], "1")
        self.assertEqual(environment["CODEX_API_KEY"], "explicit-secret")
        self.assertNotIn("OPENAI_API_KEY", environment)

    def test_model_session_observer_passes_filtered_environment(self) -> None:
        source = {
            "PATH": "/usr/bin",
            "HOME": "/Users/tester",
            "FAKE_TOKEN": "fake-secret",
            "SSH_AUTH_SOCK": "/tmp/fake-agent.sock",
        }
        with patch.object(model_sessions_module.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess(
                ("ps",), 0, stdout="observed", stderr=""
            )
            with patch.dict(model_sessions_module.os.environ, source, clear=True):
                self.assertEqual(
                    model_sessions_module._command_output(("ps", "-ax")),
                    "observed",
                )

        environment = run.call_args.kwargs["env"]
        self.assertEqual(environment["PATH"], "/usr/bin")
        self.assertEqual(environment["HOME"], "/Users/tester")
        self.assertNotIn("FAKE_TOKEN", environment)
        self.assertNotIn("SSH_AUTH_SOCK", environment)

    def test_native_process_observer_passes_filtered_environment(self) -> None:
        source = {
            "PATH": "/usr/bin",
            "HOME": "/Users/tester",
            "FAKE_TOKEN": "fake-secret",
            "SSH_AUTH_SOCK": "/tmp/fake-agent.sock",
        }
        with patch.object(native_bridge_module.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess(
                ("/bin/ps",), 0, stdout="", stderr=""
            )
            with (
                patch.dict(native_bridge_module.os.environ, source, clear=True),
                patch.object(native_bridge_module.os, "name", "posix"),
            ):
                self.assertEqual(
                    native_bridge_module._scan_child_process_ids(101),
                    [],
                )

        command = run.call_args.args[0]
        self.assertEqual(command[0], "/bin/ps")
        environment = run.call_args.kwargs["env"]
        self.assertEqual(environment["PATH"], "/usr/bin")
        self.assertNotIn("FAKE_TOKEN", environment)
        self.assertNotIn("SSH_AUTH_SOCK", environment)

    def test_native_powershell_observer_passes_filtered_environment(self) -> None:
        source = {
            "PATH": r"C:\\Windows\\System32",
            "SystemRoot": r"C:\\Windows",
            "FAKE_TOKEN": "fake-secret",
        }
        with patch.object(native_bridge_module.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess(
                ("powershell.exe",), 0, stdout="", stderr=""
            )
            with (
                patch.dict(native_bridge_module.os.environ, source, clear=True),
                patch.object(native_bridge_module.os, "name", "nt"),
            ):
                self.assertEqual(
                    native_bridge_module._scan_child_process_ids(101),
                    [],
                )

        command = run.call_args.args[0]
        self.assertEqual(command[0], "powershell.exe")
        environment = run.call_args.kwargs["env"]
        self.assertEqual(environment["SystemRoot"], r"C:\\Windows")
        self.assertNotIn("FAKE_TOKEN", environment)

    def test_native_taskkill_passes_filtered_environment(self) -> None:
        source = {
            "PATH": r"C:\\Windows\\System32",
            "SystemRoot": r"C:\\Windows",
            "FAKE_TOKEN": "fake-secret",
            "SSH_AUTH_SOCK": "fake-agent",
        }
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "scan.lock"
            lock_path.write_text(
                json.dumps({"pid": 101, "heartbeat_at": 0}),
                encoding="utf-8",
            )
            with patch.object(native_bridge_module.subprocess, "run") as run:
                run.return_value = subprocess.CompletedProcess(
                    ("taskkill.exe",), 0, stdout="", stderr=""
                )
                with (
                    patch.dict(native_bridge_module.os.environ, source, clear=True),
                    patch.object(native_bridge_module.os, "name", "nt"),
                    patch.object(
                        native_bridge_module,
                        "_process_is_alive",
                        return_value=True,
                    ),
                    patch.object(
                        native_bridge_module,
                        "_lock_is_stale",
                        return_value=False,
                    ),
                    patch.object(
                        native_bridge_module,
                        "_scan_child_process_ids",
                        return_value=[202],
                    ),
                ):
                    self.assertEqual(
                        native_bridge_module._terminate_scan_child_processes(lock_path),
                        1,
                    )

        command = run.call_args.args[0]
        self.assertEqual(command, ["taskkill.exe", "/PID", "202", "/T", "/F"])
        environment = run.call_args.kwargs["env"]
        self.assertEqual(environment["SystemRoot"], r"C:\\Windows")
        self.assertNotIn("FAKE_TOKEN", environment)
        self.assertNotIn("SSH_AUTH_SOCK", environment)


if __name__ == "__main__":
    unittest.main()
