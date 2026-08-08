from __future__ import annotations

import hashlib
import json


ROUTE_FINGERPRINT_VERSION = 1


def build_route_fingerprint(
    *,
    source_id: str,
    connection_id: str,
    connection_mode: str,
    api_format: str | None,
    provider_preset: str,
    base_url: str | None,
    model_id: str,
    scan_profile: str,
) -> str:
    payload = {
        "schema_version": ROUTE_FINGERPRINT_VERSION,
        "source_id": source_id.strip().casefold(),
        "connection_id": connection_id.strip(),
        "connection_mode": connection_mode.strip().casefold(),
        "api_format": (api_format or "").strip().casefold(),
        "provider_preset": provider_preset.strip().casefold(),
        "base_url": (base_url or "").strip().rstrip("/"),
        "model_id": model_id.strip().casefold(),
        "scan_profile": scan_profile.strip().casefold(),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"route-v{ROUTE_FINGERPRINT_VERSION}:sha256:{digest}"
