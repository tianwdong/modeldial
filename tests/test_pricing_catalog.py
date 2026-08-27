from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
from typing import Iterator
import unittest

from devtools.pricing.catalog import build_pricing_catalog
from scanner.pricing_catalog import (
    DEFAULT_PRICING_CATALOG_URL,
    PricingCatalogDownloadError,
    download_pricing_catalog,
)


ROOT = Path(__file__).resolve().parent.parent


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextmanager
def _serve(root: Path) -> Iterator[str]:
    handler = lambda *args, **kwargs: _QuietHandler(  # noqa: E731
        *args,
        directory=str(root),
        **kwargs,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


class PricingCatalogTest(unittest.TestCase):
    def test_default_catalog_uses_first_party_website_data(self) -> None:
        self.assertEqual(
            DEFAULT_PRICING_CATALOG_URL,
            "https://modeldial.com/data/pricing",
        )

    def test_builds_and_downloads_content_addressed_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            catalog_root = Path(temp_dir) / "pricing"
            manifest = build_pricing_catalog(
                snapshot_path=ROOT / "scanner" / "pricing_snapshot.json",
                output_root=catalog_root,
            )

            with _serve(catalog_root) as base_url:
                downloaded = download_pricing_catalog(base_url=base_url)

            self.assertEqual(
                downloaded.snapshot["snapshot_id"],
                manifest["snapshot_id"],
            )
            self.assertEqual(
                len(downloaded.snapshot["models"]),
                manifest["model_count"],
            )
            self.assertTrue(
                (
                    catalog_root
                    / "snapshots"
                    / f"{manifest['snapshot_id']}.json"
                ).is_file()
            )

    def test_rejects_snapshot_whose_raw_hash_does_not_match_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            catalog_root = Path(temp_dir) / "pricing"
            manifest = build_pricing_catalog(
                snapshot_path=ROOT / "scanner" / "pricing_snapshot.json",
                output_root=catalog_root,
            )
            archive_path = catalog_root / str(manifest["snapshot_path"])
            snapshot = json.loads(archive_path.read_text(encoding="utf-8"))
            snapshot["models"]["qwen3.8-flash"]["input_per_token"] = 999
            archive_path.write_text(
                json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )

            with _serve(catalog_root) as base_url:
                with self.assertRaisesRegex(
                    PricingCatalogDownloadError,
                    "invalid_payload",
                ):
                    download_pricing_catalog(base_url=base_url)

    def test_rejects_snapshot_whose_content_address_no_longer_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            catalog_root = Path(temp_dir) / "pricing"
            manifest = build_pricing_catalog(
                snapshot_path=ROOT / "scanner" / "pricing_snapshot.json",
                output_root=catalog_root,
            )
            archive_path = catalog_root / str(manifest["snapshot_path"])
            snapshot = json.loads(archive_path.read_text(encoding="utf-8"))
            snapshot["models"]["qwen3.8-flash"]["input_per_token"] = 0.17e-6
            changed_bytes = (
                json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n"
            ).encode("utf-8")
            archive_path.write_bytes(changed_bytes)
            manifest["snapshot_sha256"] = (
                "sha256:" + hashlib.sha256(changed_bytes).hexdigest()
            )
            (catalog_root / "current.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )

            with _serve(catalog_root) as base_url:
                with self.assertRaisesRegex(
                    PricingCatalogDownloadError,
                    "invalid_payload",
                ):
                    download_pricing_catalog(base_url=base_url)

    def test_rejects_non_https_non_loopback_catalog(self) -> None:
        with self.assertRaisesRegex(PricingCatalogDownloadError, "invalid_url"):
            download_pricing_catalog(base_url="http://pricing.example.test/v1")

    def test_refuses_to_overwrite_immutable_snapshot_with_different_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            catalog_root = Path(temp_dir) / "pricing"
            manifest = build_pricing_catalog(
                snapshot_path=ROOT / "scanner" / "pricing_snapshot.json",
                output_root=catalog_root,
            )
            archive_path = catalog_root / str(manifest["snapshot_path"])
            archive_path.write_bytes(b"{}\n")

            with self.assertRaisesRegex(ValueError, "immutable"):
                build_pricing_catalog(
                    snapshot_path=ROOT / "scanner" / "pricing_snapshot.json",
                    output_root=catalog_root,
                )


if __name__ == "__main__":
    unittest.main()
