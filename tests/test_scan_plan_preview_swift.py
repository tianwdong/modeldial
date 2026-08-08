from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SwiftScanPlanPreviewTest(unittest.TestCase):
    def test_preview_dto_and_presenter_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "scan-plan-preview-tests"
            compile_result = subprocess.run(
                [
                    "swiftc",
                    "Sources/Model/ScanPlanPreview.swift",
                    "tests/swift/ScanPlanPreviewTests.swift",
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
            self.assertIn("Scan plan preview tests passed", run_result.stdout)


if __name__ == "__main__":
    unittest.main()
