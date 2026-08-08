from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class EndpointOperationStateSwiftTest(unittest.TestCase):
    def test_endpoint_operation_state_ignores_stale_completions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "endpoint-operation-state-tests"
            compile_result = subprocess.run(
                [
                    "swiftc",
                    "Sources/Model/EndpointOperationState.swift",
                    "tests/swift/EndpointOperationStateTests.swift",
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
            self.assertIn("EndpointOperationState tests passed", run_result.stdout)


if __name__ == "__main__":
    unittest.main()
