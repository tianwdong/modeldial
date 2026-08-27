from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import ipaddress
import json
import math
import os
import re
from typing import Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

from .costing import (
    pricing_snapshot_content_hash,
    validate_pricing_snapshot_payload,
)


PRICING_CATALOG_SCHEMA_VERSION = 1
PRICING_CATALOG_URL_ENV = "MODELDIAL_PRICING_CATALOG_URL"
DEFAULT_PRICING_CATALOG_URL = (
    "https://modeldial.com/data/pricing"
)
DEFAULT_PRICING_CATALOG_TIMEOUT_SECONDS = 8.0
MAX_PRICING_CATALOG_MANIFEST_BYTES = 64 * 1024
MAX_PRICING_CATALOG_SNAPSHOT_BYTES = 1024 * 1024
MAX_PRICING_CATALOG_MODELS = 10_000
MAX_PRICING_RATE_PER_TOKEN = 0.01
_SNAPSHOT_ID = re.compile(r"^pricing-v1-([0-9a-f]{64})$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class PricingCatalogDownloadError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class DownloadedPricingCatalog:
    manifest: dict[str, object]
    snapshot: dict[str, object]


def configured_pricing_catalog_url(base_url: str | None = None) -> str:
    if base_url is not None:
        return base_url.strip()
    return os.environ.get(
        PRICING_CATALOG_URL_ENV,
        DEFAULT_PRICING_CATALOG_URL,
    ).strip()


def download_pricing_catalog(
    *,
    base_url: str | None = None,
    timeout_seconds: float = DEFAULT_PRICING_CATALOG_TIMEOUT_SECONDS,
    opener: Callable[..., object] = urlopen,
) -> DownloadedPricingCatalog:
    configured_url = configured_pricing_catalog_url(base_url)
    if not configured_url:
        raise PricingCatalogDownloadError("not_configured")
    catalog_root = _normalize_catalog_url(configured_url)
    manifest_url = _same_origin_url(catalog_root, "current.json")
    manifest = _read_json_object(
        manifest_url,
        max_bytes=MAX_PRICING_CATALOG_MANIFEST_BYTES,
        timeout_seconds=timeout_seconds,
        opener=opener,
    )
    snapshot_id, snapshot_path, snapshot_sha256, model_count = (
        _validate_manifest(manifest)
    )
    snapshot_url = _same_origin_url(catalog_root, snapshot_path)
    snapshot_bytes = _read_http_bytes(
        snapshot_url,
        max_bytes=MAX_PRICING_CATALOG_SNAPSHOT_BYTES,
        timeout_seconds=timeout_seconds,
        opener=opener,
    )
    if "sha256:" + hashlib.sha256(snapshot_bytes).hexdigest() != snapshot_sha256:
        raise PricingCatalogDownloadError("invalid_payload")
    try:
        snapshot = json.loads(snapshot_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PricingCatalogDownloadError("invalid_payload") from error
    if not isinstance(snapshot, dict):
        raise PricingCatalogDownloadError("invalid_payload")
    try:
        if validate_pricing_snapshot_payload(snapshot) != snapshot_id:
            raise ValueError("pricing snapshot identity mismatch")
        match = _SNAPSHOT_ID.fullmatch(snapshot_id)
        if match is None:
            raise ValueError("pricing snapshot identity is invalid")
        content_hash = pricing_snapshot_content_hash(snapshot)
        if snapshot.get("content_hash") != content_hash or match.group(1) != content_hash:
            raise ValueError("pricing snapshot content hash mismatch")
        models = snapshot.get("models")
        if not isinstance(models, Mapping) or len(models) != model_count:
            raise ValueError("pricing snapshot model count mismatch")
        _validate_remote_models(models)
    except (TypeError, ValueError) as error:
        raise PricingCatalogDownloadError("invalid_payload") from error
    return DownloadedPricingCatalog(manifest=manifest, snapshot=snapshot)


def _validate_manifest(
    manifest: Mapping[str, object],
) -> tuple[str, str, str, int]:
    if manifest.get("schema_version") != PRICING_CATALOG_SCHEMA_VERSION:
        raise PricingCatalogDownloadError("invalid_payload")
    snapshot_id = manifest.get("snapshot_id")
    snapshot_path = manifest.get("snapshot_path")
    snapshot_sha256 = manifest.get("snapshot_sha256")
    model_count = manifest.get("model_count")
    published_at = manifest.get("published_at")
    if not isinstance(snapshot_id, str) or _SNAPSHOT_ID.fullmatch(snapshot_id) is None:
        raise PricingCatalogDownloadError("invalid_payload")
    expected_path = f"snapshots/{snapshot_id}.json"
    if snapshot_path != expected_path:
        raise PricingCatalogDownloadError("invalid_payload")
    if not isinstance(snapshot_sha256, str) or _SHA256.fullmatch(snapshot_sha256) is None:
        raise PricingCatalogDownloadError("invalid_payload")
    if (
        isinstance(model_count, bool)
        or not isinstance(model_count, int)
        or model_count <= 0
        or model_count > MAX_PRICING_CATALOG_MODELS
    ):
        raise PricingCatalogDownloadError("invalid_payload")
    if not isinstance(published_at, str):
        raise PricingCatalogDownloadError("invalid_payload")
    try:
        parsed_published_at = datetime.fromisoformat(
            published_at.replace("Z", "+00:00")
        )
    except ValueError as error:
        raise PricingCatalogDownloadError("invalid_payload") from error
    if parsed_published_at.tzinfo is None:
        raise PricingCatalogDownloadError("invalid_payload")
    return snapshot_id, expected_path, snapshot_sha256, model_count


def _validate_remote_models(models: Mapping[object, object]) -> None:
    rate_keys = (
        "input_per_token",
        "cached_input_per_token",
        "cache_write_input_per_token",
        "output_per_token",
        "reasoning_output_per_token",
    )
    for model_id, raw_rate in models.items():
        if (
            not isinstance(model_id, str)
            or not model_id
            or len(model_id) > 256
            or not isinstance(raw_rate, Mapping)
            or not isinstance(raw_rate.get("provenance"), Mapping)
        ):
            raise ValueError("invalid remote pricing model entry")
        for key in rate_keys:
            value = raw_rate.get(key)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("invalid remote pricing rate")
            parsed = float(value)
            if (
                not math.isfinite(parsed)
                or parsed < 0
                or parsed > MAX_PRICING_RATE_PER_TOKEN
            ):
                raise ValueError("remote pricing rate is outside the allowed range")


def _read_json_object(
    url: str,
    *,
    max_bytes: int,
    timeout_seconds: float,
    opener: Callable[..., object],
) -> dict[str, object]:
    body = _read_http_bytes(
        url,
        max_bytes=max_bytes,
        timeout_seconds=timeout_seconds,
        opener=opener,
    )
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PricingCatalogDownloadError("invalid_payload") from error
    if not isinstance(payload, dict):
        raise PricingCatalogDownloadError("invalid_payload")
    return payload


def _read_http_bytes(
    url: str,
    *,
    max_bytes: int,
    timeout_seconds: float,
    opener: Callable[..., object],
) -> bytes:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "ModelDial/PricingCatalogV1",
        },
    )
    try:
        with opener(request, timeout=max(0.1, timeout_seconds)) as response:  # type: ignore[attr-defined]
            if not _same_origin(url, response.geturl()):  # type: ignore[attr-defined]
                raise PricingCatalogDownloadError("invalid_payload")
            body = response.read(max_bytes + 1)  # type: ignore[attr-defined]
    except PricingCatalogDownloadError:
        raise
    except HTTPError as error:
        error.close()
        raise PricingCatalogDownloadError("unavailable") from error
    except (URLError, OSError, TimeoutError) as error:
        raise PricingCatalogDownloadError("unavailable") from error
    if len(body) > max_bytes:
        raise PricingCatalogDownloadError("invalid_payload")
    return body


def _normalize_catalog_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.scheme not in {"http", "https"}
    ):
        raise PricingCatalogDownloadError("invalid_url")
    if parsed.scheme != "https" and not _is_loopback(parsed.hostname):
        raise PricingCatalogDownloadError("invalid_url")
    return value.rstrip("/") + "/"


def _same_origin_url(base_url: str, relative_path: str) -> str:
    resolved = urljoin(base_url, relative_path)
    if not _same_origin(base_url, resolved):
        raise PricingCatalogDownloadError("invalid_url")
    return resolved


def _same_origin(first: str, second: str) -> bool:
    left = urlsplit(first)
    right = urlsplit(second)
    return (
        left.scheme.casefold(),
        (left.hostname or "").casefold(),
        _effective_port(left.scheme, left.port),
    ) == (
        right.scheme.casefold(),
        (right.hostname or "").casefold(),
        _effective_port(right.scheme, right.port),
    )


def _effective_port(scheme: str, port: int | None) -> int | None:
    if port is not None:
        return port
    return 443 if scheme.casefold() == "https" else 80


def _is_loopback(hostname: str) -> bool:
    if hostname.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False
