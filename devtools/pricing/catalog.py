from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Mapping

from scanner.costing import (
    pricing_snapshot_content_hash,
    validate_pricing_snapshot_payload,
)


def build_pricing_catalog(
    *,
    snapshot_path: Path,
    output_root: Path,
    published_at: str | None = None,
) -> dict[str, object]:
    raw_snapshot = snapshot_path.read_bytes()
    try:
        snapshot = json.loads(raw_snapshot.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("pricing snapshot is not valid UTF-8 JSON") from error
    if not isinstance(snapshot, dict):
        raise ValueError("pricing snapshot must contain an object")
    snapshot_id = validate_pricing_snapshot_payload(snapshot)
    content_hash = pricing_snapshot_content_hash(snapshot)
    if (
        snapshot.get("content_hash") != content_hash
        or snapshot_id != f"pricing-v1-{content_hash}"
    ):
        raise ValueError("pricing snapshot content hash is invalid")
    models = snapshot.get("models")
    if not isinstance(models, Mapping) or not models:
        raise ValueError("pricing snapshot models are required")

    publication_time = published_at or snapshot.get("generated_at")
    if not isinstance(publication_time, str):
        raise ValueError("pricing catalog published_at is required")
    try:
        parsed_publication_time = datetime.fromisoformat(
            publication_time.replace("Z", "+00:00")
        )
    except ValueError as error:
        raise ValueError("pricing catalog published_at must be ISO 8601") from error
    if parsed_publication_time.tzinfo is None:
        raise ValueError("pricing catalog published_at must include a timezone")

    snapshot_relative_path = f"snapshots/{snapshot_id}.json"
    snapshot_sha256 = "sha256:" + hashlib.sha256(raw_snapshot).hexdigest()
    manifest: dict[str, object] = {
        "schema_version": 1,
        "snapshot_id": snapshot_id,
        "snapshot_path": snapshot_relative_path,
        "snapshot_sha256": snapshot_sha256,
        "published_at": publication_time,
        "model_count": len(models),
    }

    output_root = output_root.expanduser().resolve()
    archive_path = output_root / snapshot_relative_path
    if archive_path.is_file() and archive_path.read_bytes() != raw_snapshot:
        raise ValueError("immutable pricing catalog snapshot already exists")
    if not archive_path.is_file():
        _write_bytes_atomic(archive_path, raw_snapshot)
    _write_bytes_atomic(
        output_root / "current.json",
        (
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8"),
    )
    return manifest


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary_path = Path(handle.name)
    try:
        with handle:
            handle.write(payload)
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)
