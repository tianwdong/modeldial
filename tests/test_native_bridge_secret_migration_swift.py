from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class NativeBridgeSecretMigrationSwiftTest(unittest.TestCase):
    def test_scan_secret_migration_uses_lightweight_ack_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "native-bridge-secret-migration"
            compile_result = subprocess.run(
                [
                    "swiftc",
                    "-module-cache-path",
                    str(Path(temp_dir) / "module-cache"),
                    "Sources/Support/DebugLog.swift",
                    "Sources/Model/LocalEncryptedSecretStore.swift",
                    "Sources/Model/KeychainSecretStore.swift",
                    "Sources/Model/AppSecretStore.swift",
                    "Sources/Model/SettingsConfigPatch.swift",
                    "Sources/Model/ScanPlanPreview.swift",
                    "Sources/Model/SelectionModels.swift",
                    "Sources/Model/NativeBridgeClient.swift",
                    "tests/swift/NativeBridgeSecretMigrationTests.swift",
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
                "Native bridge secret migration tests passed",
                run_result.stdout,
            )


if __name__ == "__main__":
    unittest.main()
