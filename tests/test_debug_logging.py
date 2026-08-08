from __future__ import annotations

from pathlib import Path
import unittest


class DebugLoggingSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent.parent

    def test_native_debug_log_is_opt_in_rotated_and_private(self) -> None:
        source = (self.root / "Sources" / "Support" / "DebugLog.swift").read_text(
            encoding="utf-8"
        )

        self.assertIn('environment["MODELDIAL_DEBUG_LOG"] == "1"', source)
        self.assertIn('appendingPathComponent("Library"', source)
        self.assertIn('appendingPathComponent("Application Support"', source)
        self.assertIn('appendingPathComponent("modeldial"', source)
        self.assertIn("private static let maxLogSizeBytes", source)
        self.assertIn("rotateIfNeeded", source)
        self.assertIn(".posixPermissions: 0o600", source)
        self.assertNotIn("/private/tmp/codex_selection_island_debug.log", source)

    def test_high_frequency_or_payload_bearing_events_are_not_logged(self) -> None:
        window_source = (
            self.root / "Sources" / "Window" / "SelectionWindowController.swift"
        ).read_text(encoding="utf-8")
        bridge_source = (
            self.root / "Sources" / "Model" / "NativeBridgeClient.swift"
        ).read_text(encoding="utf-8")

        self.assertNotIn("mouse cursor=", window_source)
        self.assertNotIn("rawEvent=", bridge_source)
        self.assertIn("startScan event=", bridge_source)

    def test_python_scan_logs_are_opt_in(self) -> None:
        for relative_path in (
            "scanner/native_bridge.py",
            "scanner/runner.py",
            "scanner/service.py",
        ):
            with self.subTest(path=relative_path):
                source = (self.root / relative_path).read_text(encoding="utf-8")
                self.assertIn(
                    'os.environ.get("MODELDIAL_DEBUG_LOG") != "1"',
                    source,
                )


if __name__ == "__main__":
    unittest.main()
