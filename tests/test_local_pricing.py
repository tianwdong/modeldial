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
from scanner.pricing_catalog import (
    DownloadedPricingCatalog,
    PricingCatalogDownloadError,
)


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


class LocalPricingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(
            install_pricing_snapshot,
            ROOT / "scanner" / "pricing_snapshot.json",
        )

    def test_disabled_catalog_freezes_baked_snapshot_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            backend = root / "backend"
            data_root = root / "data"
            (backend / "scanner").mkdir(parents=True)
            (backend / "scanner" / "pricing_snapshot.json").write_text(
                json.dumps(_snapshot("pricing-v1-baked", 1e-6)),
                encoding="utf-8",
            )

            first = prepare_local_pricing_snapshot(
                backend_root=backend,
                data_root=data_root,
                scope_id="run-local-1",
                refresh=True,
                catalog_url="",
            )
            second = prepare_local_pricing_snapshot(
                backend_root=backend,
                data_root=data_root,
                scope_id="run-local-1",
                refresh=True,
                catalog_url="",
            )

            self.assertEqual(first["status"], "failed")
            self.assertEqual(first["errors"], ["not_configured"])
            self.assertTrue(first["fallback_used"])
            self.assertEqual(second["status"], "reused")
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
            self.assertAlmostEqual(estimate.usd or 0, 1000 * 1e-6 + 100 * 6e-6)

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
                catalog_url="",
            )

            self.assertEqual(report["status"], "historical_reused")
            estimate = estimate_reference_cost(
                "gpt-5.6-luna",
                input_tokens=1000,
                cached_input_tokens=0,
                output_tokens=0,
            )
            self.assertAlmostEqual(estimate.usd or 0, 0.001)

    def test_new_run_downloads_catalog_and_freezes_remote_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            backend = root / "backend"
            data_root = root / "data"
            (backend / "scanner").mkdir(parents=True)
            (backend / "scanner" / "pricing_snapshot.json").write_text(
                json.dumps(_snapshot("pricing-v1-baked", 1e-6)),
                encoding="utf-8",
            )
            remote = _snapshot("pricing-v1-remote", 0.2e-6)
            calls = 0

            def fetch(url: str) -> DownloadedPricingCatalog:
                nonlocal calls
                calls += 1
                self.assertEqual(url, "https://pricing.example.test/v1")
                return DownloadedPricingCatalog(
                    manifest={"snapshot_id": remote["snapshot_id"]},
                    snapshot=remote,
                )

            first = prepare_local_pricing_snapshot(
                backend_root=backend,
                data_root=data_root,
                scope_id="run-remote-1",
                refresh=True,
                catalog_url="https://pricing.example.test/v1",
                fetch_catalog=fetch,
            )
            second = prepare_local_pricing_snapshot(
                backend_root=backend,
                data_root=data_root,
                scope_id="run-remote-1",
                refresh=True,
                catalog_url="https://pricing.example.test/v1",
                fetch_catalog=fetch,
            )

            self.assertEqual(first["status"], "applied")
            self.assertEqual(first["source"], "pricing_catalog")
            self.assertEqual(second["status"], "reused")
            self.assertEqual(calls, 1)
            self.assertTrue(
                (data_root / "pricing" / "snapshots" / "pricing-v1-remote.json").is_file()
            )
            estimate = estimate_reference_cost(
                "gpt-5.6-luna",
                input_tokens=1000,
                cached_input_tokens=0,
                output_tokens=0,
            )
            self.assertAlmostEqual(estimate.usd or 0, 0.0002)

    def test_catalog_failure_keeps_last_valid_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            backend = root / "backend"
            data_root = root / "data"
            (backend / "scanner").mkdir(parents=True)
            (backend / "scanner" / "pricing_snapshot.json").write_text(
                json.dumps(_snapshot("pricing-v1-baked", 1e-6)),
                encoding="utf-8",
            )

            def fail(_url: str) -> DownloadedPricingCatalog:
                raise PricingCatalogDownloadError("unavailable")

            report = prepare_local_pricing_snapshot(
                backend_root=backend,
                data_root=data_root,
                scope_id="run-remote-fallback",
                refresh=True,
                catalog_url="https://pricing.example.test/v1",
                fetch_catalog=fail,
            )

            self.assertEqual(report["status"], "failed")
            self.assertTrue(report["fallback_used"])
            self.assertEqual(report["errors"], ["unavailable"])
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
