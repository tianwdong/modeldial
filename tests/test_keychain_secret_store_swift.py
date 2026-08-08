from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class KeychainSecretStoreSwiftTest(unittest.TestCase):
    def test_keychain_secret_store_round_trip_uses_isolated_temporary_item(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "keychain-secret-store-tests"
            compile_result = subprocess.run(
                [
                    "swiftc",
                    "-module-cache-path",
                    str(Path(temp_dir) / "module-cache"),
                    "Sources/Model/KeychainSecretStore.swift",
                    "tests/swift/KeychainSecretStoreTests.swift",
                    "-o",
                    str(executable),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                compile_result.returncode,
                0,
                compile_result.stderr or compile_result.stdout,
            )

            run_result = subprocess.run(
                [str(executable)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                run_result.returncode,
                0,
                run_result.stderr or run_result.stdout,
            )
            self.assertIn("KeychainSecretStore tests passed", run_result.stdout)


if __name__ == "__main__":
    unittest.main()
