from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class AdvisorDecodingSwiftTest(unittest.TestCase):
    def test_advisor_decoding_fixture_compiles_and_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "advisor-decoding-tests"
            compile_result = subprocess.run(
                [
                    "swiftc",
                    "-module-cache-path",
                    str(Path(temp_dir) / "module-cache"),
                    "Sources/Model/LocalEncryptedSecretStore.swift",
                    "Sources/Model/SelectionModels.swift",
                    "tests/swift/AdvisorDecodingTests.swift",
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
            self.assertIn("Advisor decoding tests passed", run_result.stdout)


if __name__ == "__main__":
    unittest.main()
