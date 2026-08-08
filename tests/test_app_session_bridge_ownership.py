from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
NON_OWNERSHIP_SOURCES = {
    "App.swift",
    "SettingsView.swift",
    "UpdaterController.swift",
}


class AppSessionBridgeOwnershipTest(unittest.TestCase):
    def test_snapshot_setter_is_inaccessible_outside_app_session_store(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            probe = Path(temp_dir) / "SnapshotSetterAccessProbe.swift"
            probe.write_text(
                """
                @MainActor
                func attemptSnapshotWrite(_ store: AppSessionStore) {
                    store.snapshot = nil
                }
                """,
                encoding="utf-8",
            )
            sources = sorted(
                str(path.relative_to(ROOT))
                for path in (ROOT / "Sources").rglob("*.swift")
                if path.name not in NON_OWNERSHIP_SOURCES
            )
            compile_result = subprocess.run(
                [
                    "swiftc",
                    "-typecheck",
                    "-module-cache-path",
                    str(Path(temp_dir) / "module-cache"),
                    *sources,
                    str(probe),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(compile_result.returncode, 0)
            self.assertIn("'snapshot' setter is inaccessible", compile_result.stderr)

    def test_settings_commands_publish_only_through_app_session_store(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "app-session-bridge-ownership"
            sources = sorted(
                str(path.relative_to(ROOT))
                for path in (ROOT / "Sources").rglob("*.swift")
                if path.name not in NON_OWNERSHIP_SOURCES
            )
            compile_result = subprocess.run(
                [
                    "swiftc",
                    "-module-cache-path",
                    str(Path(temp_dir) / "module-cache"),
                    *sources,
                    "tests/swift/AppSessionBridgeOwnershipTests.swift",
                    "-o",
                    str(executable),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            run_result = subprocess.run(
                [str(executable)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(run_result.returncode, 0, run_result.stderr)
            self.assertIn(
                "App session bridge ownership tests passed",
                run_result.stdout,
            )


if __name__ == "__main__":
    unittest.main()
