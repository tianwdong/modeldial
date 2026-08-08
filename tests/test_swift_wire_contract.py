from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SwiftWireContractTest(unittest.TestCase):
    def test_v2_wire_dtos_reject_swift_legacy_fallbacks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "wire-dto-compatibility"
            compile_result = subprocess.run(
                [
                    "swiftc",
                    "Sources/Model/LocalEncryptedSecretStore.swift",
                    "Sources/Model/SelectionModels.swift",
                    "tests/swift/WireDTOCompatibilityTests.swift",
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
            self.assertIn("Wire DTO compatibility tests passed", run_result.stdout)


if __name__ == "__main__":
    unittest.main()
