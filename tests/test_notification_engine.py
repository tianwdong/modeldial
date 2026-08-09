from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from scanner.active_run_store import ActiveRunStore
from scanner.config_store import ConfigStore
from scanner.history_store import HistoryStore
from scanner.service import MonitorService
from scanner.settings_projection import SettingsProjectionProjector
from scanner.snapshot_query import SnapshotProjector, SnapshotQuery


class NotificationEngineSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).resolve().parent.parent
        self.engine = (root / "Sources" / "Model" / "RecommendationNotificationEngine.swift").read_text(encoding="utf-8")
        self.store = (root / "Sources" / "Model" / "SelectionStore.swift").read_text(encoding="utf-8")
        self.settings = (root / "Sources" / "Views" / "SettingsView.swift").read_text(encoding="utf-8")

    def test_notification_engine_is_low_noise_stateful_and_redacted(self) -> None:
        self.assertIn("UNUserNotificationCenter", self.engine)
        self.assertIn("guard let previous else", self.engine)
        self.assertIn("eventType", self.engine)
        self.assertIn("runID", self.engine)
        self.assertIn("candidateID", self.engine)
        self.assertIn("UserDefaults", self.engine)
        self.assertIn('fingerprintKey = "modeldial.notification.fingerprints"', self.engine)
        self.assertIn('legacyBundleID = "dev.codexselectionisland.app"', self.engine)
        self.assertIn("migrateLegacyFingerprintsIfNeeded()", self.engine)
        for event in ("recommendation_changed", "retained_after_failure", "resume_circuit_open"):
            self.assertIn(f'"{event}"', self.engine)
        self.assertIn("recommendationDecisionIdentity", self.engine)
        self.assertIn("isActionableRecommendation", self.engine)
        self.assertIn("let best = current.dashboard.bestCombination", self.engine)
        self.assertIn("let previousBest = previous.dashboard.bestCombination", self.engine)
        self.assertNotIn("let best = current.stableDashboard?.bestCombination", self.engine)
        self.assertIn("let summary = current.dashboard", self.engine)
        self.assertIn("let recommendationRunID", self.engine)
        self.assertIn(".representativeEvidence?.sourceSnapshotId", self.engine)
        self.assertIn('eventType: "recommendation_changed",\n                runID: recommendationRunID', self.engine)
        self.assertIn("guard !isPanelExpanded", self.engine)
        self.assertIn("case .denied", self.engine)
        self.assertIn("case .notDetermined", self.engine)
        self.assertNotIn("apiKey", self.engine)
        self.assertNotIn('"Authorization"', self.engine)

    def test_notification_portfolio_contract_is_owned_by_snapshot_query(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = MonitorService(
                config_store=ConfigStore(
                    root / "config.json",
                    first_run_defaults=True,
                ),
                history_store=HistoryStore(root / "history.jsonl"),
                active_run_store=ActiveRunStore(root / "active_run.json"),
            )
            snapshot = SnapshotQuery(
                snapshot_projector=SnapshotProjector(
                    config_reader=service.config_store.load,
                    state_reader=service.monitor_state_projector.build_state,
                    settings_projector=SettingsProjectionProjector(
                        service.scan_target_resolver
                    ),
                ),
                refresh_state_reader=(
                    service.monitor_state_projector.build_refresh_state
                ),
                data_dir=root,
            ).build_snapshot()

        self.assertEqual(snapshot["schema_version"], 2)
        self.assertIsInstance(snapshot["recommendation_portfolio_v2"], dict)
        self.assertNotIn(
            "recommendation_portfolio_v2",
            (
                Path(__file__).resolve().parent.parent
                / "scanner"
                / "native_bridge.py"
            ).read_text(encoding="utf-8"),
        )

    def test_store_consumes_snapshot_after_replacement_and_settings_owns_permission_action(self) -> None:
        self.assertIn("notificationEngine.consume(", self.store)
        self.assertIn("previous: previous", self.store)
        self.assertIn("isPanelExpanded: isExpanded", self.store)
        self.assertIn('formRow("本地通知")', self.settings)
        self.assertIn("requestPermissionFromUser", self.settings)

    def test_denied_permission_opens_system_settings_and_refreshes_on_return(self) -> None:
        self.assertIn("import AppKit", self.engine)
        self.assertIn("openNotificationSettings()", self.engine)
        self.assertIn("NSWorkspace.shared.open", self.engine)
        self.assertIn("NSApplication.didBecomeActiveNotification", self.engine)
        self.assertIn("refreshPermissionStatus()", self.engine)


if __name__ == "__main__":
    unittest.main()
