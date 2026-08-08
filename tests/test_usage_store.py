from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scanner.usage_observer import reset_codex_usage_observations
from scanner.usage_store import UsageStore


class UsageStoreLifecycleTests(unittest.TestCase):
    def test_export_excludes_identity_key_and_keeps_minimal_local_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir))
            store.save_usage_state(
                {
                    "schema_version": 1,
                    "files": {},
                    "observations": {"observation": {"input_tokens": 10}},
                    "bootstrap_truncated": False,
                }
            )
            store.save_recommendation_use_state(
                {"schema_version": 1, "epochs": [], "observation_assignments": {}}
            )
            store.identity_key()

            exported = store.export_personal_observations()

            self.assertEqual(exported["schema_version"], 1)
            self.assertIn("usage_observations", exported)
            self.assertIn("recommendation_use", exported)
            self.assertNotIn("identity_key", exported)
            self.assertNotIn(store.identity_key_path.name, str(exported))

    def test_clear_removes_only_personal_observation_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = UsageStore(root)
            history_path = root / "history.jsonl"
            history_path.write_text("scan-result\n", encoding="utf-8")
            store.save_usage_state(
                {"schema_version": 1, "files": {}, "observations": {}}
            )
            store.save_recommendation_use_state(
                {"schema_version": 1, "epochs": [], "observation_assignments": {}}
            )
            store.save_account_snapshot(
                {
                    "schema_version": 1,
                    "captured_at": "2026-07-26T12:00:00Z",
                    "quota_windows": [],
                }
            )
            store.identity_key()

            removed = store.clear_personal_observations()

            self.assertGreaterEqual(len(removed), 4)
            self.assertTrue(history_path.exists())
            self.assertFalse(store.usage_state_path.exists())
            self.assertFalse(store.account_snapshot_path.exists())
            self.assertFalse(store.account_snapshot_history_path.exists())
            self.assertFalse(store.recommendation_use_epochs_path.exists())
            self.assertFalse(store.identity_key_path.exists())

    def test_reset_starts_future_observation_at_current_log_end(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            rollout = sessions_root / "rollout-existing.jsonl"
            rollout.write_text('{"type":"event_msg"}\n', encoding="utf-8")
            store = UsageStore(root / "data")

            reset_codex_usage_observations(
                sessions_root=sessions_root,
                store=store,
                now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc),
            )

            state = store.load_usage_state()
            self.assertEqual(state["observations"], {})
            self.assertEqual(state["coverage_continuous_since"], "2026-07-26T12:00:00Z")
            self.assertEqual(len(state["files"]), 1)
            file_state = next(iter(state["files"].values()))
            self.assertEqual(file_state["offset"], rollout.stat().st_size)


if __name__ == "__main__":
    unittest.main()
