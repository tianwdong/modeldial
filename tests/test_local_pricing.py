from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scanner.costing import (
    _pricing_snapshot_path,
    estimate_reference_cost,
    install_pricing_snapshot,
)
from scanner.local_pricing import prepare_local_pricing_snapshot


ROOT = Path(__file__).resolve().parent.parent


def _snapshot(snapshot_id: str, input_rate: float) -> dict[str, object]:
    return {
        "schema_version": 1,
        "snapshot_id": snapshot_id,
        "models": {
            "gpt-5.6-luna": {
                "input_per_token": input_rate,
                "cached_input_per_token": input_rate / 10,
                "cache_write_input_per_token": input_rate * 1.25,
                "output_per_token": input_rate * 6,
            }
        },
        "aliases": {},
    }


def _policy() -> dict[str, object]:
    source_revision = "a" * 40
    return {
        "schema_version": 1,
        "source_name": "test",
        "source_url": f"https://example.test/{source_revision}/prices.json",
        "source_revision": source_revision,
        "source_sha256": "sha256:" + "b" * 64,
        "minimum_upstream_entry_count": 1,
        "minimum_model_count": 1,
        "minimum_fresh_model_count": 1,
        "maximum_cost_per_token": 0.001,
        "required_priceable_prefixes": ["gpt-"],
        "reviewed_matches": {},
    }


class LocalPricingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(
            install_pricing_snapshot,
            ROOT / "scanner" / "pricing_snapshot.json",
        )

    def test_new_run_refreshes_once_and_freezes_its_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            backend = root / "backend"
            data_root = root / "data"
            (backend / "scanner").mkdir(parents=True)
            (backend / "devtools" / "pricing").mkdir(parents=True)
            (backend / "scanner" / "pricing_snapshot.json").write_text(
                json.dumps(_snapshot("pricing-v1-baked", 1e-6)),
                encoding="utf-8",
            )
            (backend / "devtools" / "pricing" / "policy.json").write_text(
                json.dumps(_policy()),
                encoding="utf-8",
            )
            calls = 0

            def fetch(_policy_payload):  # type: ignore[no-untyped-def]
                nonlocal calls
                calls += 1
                return {
                    "gpt-5.6-luna": {
                        "mode": "chat",
                        "input_cost_per_token": 0.2e-6,
                        "cache_read_input_token_cost": 0.02e-6,
                        "cache_creation_input_token_cost": 0.25e-6,
                        "output_cost_per_token": 1.2e-6,
                    }
                }

            first = prepare_local_pricing_snapshot(
                backend_root=backend,
                data_root=data_root,
                scope_id="run-local-1",
                refresh=True,
                fetch_upstream=fetch,
            )
            second = prepare_local_pricing_snapshot(
                backend_root=backend,
                data_root=data_root,
                scope_id="run-local-1",
                refresh=True,
                fetch_upstream=fetch,
            )

            self.assertEqual(first["status"], "applied")
            self.assertEqual(second["status"], "reused")
            self.assertEqual(calls, 1)
            self.assertTrue((data_root / "pricing" / "current.json").is_file())
            self.assertTrue(
                (data_root / "pricing" / "runs" / "run-local-1.json").is_file()
            )
            estimate = estimate_reference_cost(
                "gpt-5.6-luna",
                input_tokens=1000,
                cached_input_tokens=0,
                output_tokens=100,
            )
            self.assertAlmostEqual(estimate.usd or 0, 1000 * 0.2e-6 + 100 * 1.2e-6)

    def test_resume_restores_historical_snapshot_without_fetching(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            backend = root / "backend"
            data_root = root / "data"
            archive = backend / "scanner" / "pricing_snapshots"
            archive.mkdir(parents=True)
            (backend / "scanner" / "pricing_snapshot.json").write_text(
                json.dumps(_snapshot("pricing-v1-current", 0.2e-6)),
                encoding="utf-8",
            )
            (archive / "pricing-v1-legacy.json").write_text(
                json.dumps(_snapshot("pricing-v1-legacy", 1e-6)),
                encoding="utf-8",
            )

            report = prepare_local_pricing_snapshot(
                backend_root=backend,
                data_root=data_root,
                scope_id="run-local-legacy",
                historical_snapshot_ids=("pricing-v1-legacy",),
                refresh=False,
                fetch_upstream=lambda _policy_payload: self.fail("must not fetch"),
            )

            self.assertEqual(report["status"], "historical_reused")
            estimate = estimate_reference_cost(
                "gpt-5.6-luna",
                input_tokens=1000,
                cached_input_tokens=0,
                output_tokens=0,
            )
            self.assertAlmostEqual(estimate.usd or 0, 0.001)

    def test_new_run_keeps_baked_snapshot_when_refresh_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            backend = root / "backend"
            data_root = root / "data"
            (backend / "scanner").mkdir(parents=True)
            (backend / "devtools" / "pricing").mkdir(parents=True)
            (backend / "scanner" / "pricing_snapshot.json").write_text(
                json.dumps(_snapshot("pricing-v1-baked", 1e-6)),
                encoding="utf-8",
            )
            (backend / "devtools" / "pricing" / "policy.json").write_text(
                json.dumps(_policy()),
                encoding="utf-8",
            )

            def fail_fetch(_policy_payload):  # type: ignore[no-untyped-def]
                raise OSError("offline")

            report = prepare_local_pricing_snapshot(
                backend_root=backend,
                data_root=data_root,
                scope_id="run-local-fallback",
                refresh=True,
                fetch_upstream=fail_fetch,
            )

            self.assertEqual(report["status"], "failed")
            self.assertTrue(report["fallback_used"])
            estimate = estimate_reference_cost(
                "gpt-5.6-luna",
                input_tokens=1000,
                cached_input_tokens=0,
                output_tokens=0,
            )
            self.assertAlmostEqual(estimate.usd or 0, 0.001)

    def test_bridge_process_prefers_persisted_local_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir)
            current = data_root / "pricing" / "current.json"
            current.parent.mkdir(parents=True)
            current.write_text(
                json.dumps(_snapshot("pricing-v1-local-current", 0.2e-6)),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"MODELDIAL_DATA_DIR": str(data_root)}):
                self.assertEqual(_pricing_snapshot_path(), current)


if __name__ == "__main__":
    unittest.main()
