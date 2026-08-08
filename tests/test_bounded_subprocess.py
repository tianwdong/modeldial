from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path
import unittest

from scanner.bounded_subprocess import (
    BoundedSubprocessOutputError,
    run_bounded_process,
)


class BoundedSubprocessTest(unittest.TestCase):
    def test_combined_stdout_and_stderr_budget_fails_closed(self) -> None:
        with self.assertRaises(BoundedSubprocessOutputError) as error:
            run_bounded_process(
                [
                    sys.executable,
                    "-c",
                    (
                        "import sys;"
                        "sys.stdout.write('a' * 4096); sys.stdout.flush();"
                        "sys.stderr.write('b' * 4096); sys.stderr.flush()"
                    ),
                ],
                timeout=5,
                output_limit_bytes=4096,
                text=False,
            )

        exc = error.exception
        self.assertLessEqual(len(exc.stdout) + len(exc.stderr), 4096)
        self.assertGreater(exc.total_output_bytes, exc.output_limit_bytes)

    def test_output_overflow_terminates_child_before_later_side_effect(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bounded-subprocess-") as temp:
            marker = Path(temp) / "must-not-be-written"
            code = (
                "import pathlib, sys, time;"
                "sys.stdout.write('x' * 1000000); sys.stdout.flush();"
                f"time.sleep(0.5); pathlib.Path({str(marker)!r}).write_text('late')"
            )
            started = time.monotonic()
            with self.assertRaises(BoundedSubprocessOutputError):
                run_bounded_process(
                    [sys.executable, "-c", code],
                    timeout=5,
                    output_limit_bytes=4096,
                )
            elapsed = time.monotonic() - started

            self.assertLess(elapsed, 2.0)
            self.assertFalse(marker.exists())

    def test_timeout_terminates_child_before_later_side_effect(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bounded-subprocess-timeout-") as temp:
            marker = Path(temp) / "must-not-be-written"
            code = (
                "import pathlib, time;"
                "time.sleep(0.5);"
                f"pathlib.Path({str(marker)!r}).write_text('late')"
            )
            started = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired):
                run_bounded_process(
                    [sys.executable, "-c", code],
                    timeout=0.05,
                    output_limit_bytes=4096,
                )
            elapsed = time.monotonic() - started

            self.assertLess(elapsed, 2.0)
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
