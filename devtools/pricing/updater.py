from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any
from urllib.request import Request, urlopen


_LONG_CONTEXT_INPUT = re.compile(r"input_cost_per_token_above_(\d+)k_tokens")
_TEXT_MODES = {"chat", "completion", "responses"}


class PricingUpdateError(ValueError):
    pass


@dataclass(frozen=True)
class PricingUpdateOutcome:
    candidate: dict[str, Any] | None
    report: dict[str, Any]


def load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise PricingUpdateError(f"{path} must contain a JSON object")
    return payload


def fetch_upstream_json(
    policy: dict[str, Any],
    *,
    timeout_seconds: float = 30.0,
    maximum_bytes: int = 20 * 1024 * 1024,
) -> dict[str, Any]:
    normalized = _validate_policy(policy)
    request = Request(
        normalized["source_url"],
        headers={"User-Agent": "ModelDial-Pricing-Updater/1"},
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > maximum_bytes:
            raise PricingUpdateError("upstream response exceeds maximum size")
        raw = response.read(maximum_bytes + 1)
    if len(raw) > maximum_bytes:
        raise PricingUpdateError("upstream response exceeds maximum size")
    return _decode_upstream_json(raw, normalized)


def load_upstream_json(
    path: Path,
    policy: dict[str, Any],
    *,
    maximum_bytes: int = 20 * 1024 * 1024,
) -> dict[str, Any]:
    normalized = _validate_policy(policy)
    with path.open("rb") as handle:
        raw = handle.read(maximum_bytes + 1)
    if len(raw) > maximum_bytes:
        raise PricingUpdateError("upstream response exceeds maximum size")
    return _decode_upstream_json(raw, normalized)


def _decode_upstream_json(
    raw: bytes,
    normalized_policy: dict[str, Any],
) -> dict[str, Any]:
    actual_hash = "sha256:" + hashlib.sha256(raw).hexdigest()
    if actual_hash != normalized_policy["source_sha256"]:
        raise PricingUpdateError("upstream source hash mismatch")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise PricingUpdateError("upstream price source must be a JSON object")
    return payload


def build_update(
    previous_snapshot: dict[str, Any],
    upstream_payload: dict[str, Any],
    policy: dict[str, Any],
    *,
    fetched_at: str,
    requested_models: tuple[str, ...] = (),
) -> PricingUpdateOutcome:
    normalized_policy = _validate_policy(policy)
    _validate_snapshot(previous_snapshot, require_provenance=False)
    if not isinstance(upstream_payload, dict):
        raise PricingUpdateError("upstream price source must be a JSON object")
    minimum_upstream = normalized_policy["minimum_upstream_entry_count"]
    if len(upstream_payload) < minimum_upstream:
        raise PricingUpdateError(
            f"upstream model count {len(upstream_payload)} is below minimum "
            f"{minimum_upstream}"
        )

    previous_models = previous_snapshot["models"]
    reviewed_matches = normalized_policy["reviewed_matches"]
    models: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    fresh_count = 0

    for model_id in sorted(previous_models):
        previous_rate = previous_models[model_id]
        matched_key, confidence, reason = _resolve_match(
            model_id,
            upstream_payload,
            reviewed_matches,
        )
        fresh_rate = None
        if matched_key is not None:
            try:
                fresh_rate = _convert_upstream_rate(
                    upstream_payload[matched_key],
                    maximum_cost=normalized_policy["maximum_cost_per_token"],
                )
            except PricingUpdateError as exc:
                warnings.append(f"{model_id}: {exc}")
        if fresh_rate is None:
            models[model_id] = _preserve_stale_rate(
                model_id,
                previous_rate,
                previous_snapshot,
            )
            continue
        fresh_rate["provenance"] = {
            "source": normalized_policy["source_name"],
            "matched_key": matched_key,
            "fetched_at": fetched_at,
            "stale": False,
            "confidence": confidence,
            "reason": reason,
        }
        models[model_id] = fresh_rate
        fresh_count += 1

    unpriced_requested: list[str] = []
    for model_id in sorted({item.strip() for item in requested_models if item.strip()}):
        if model_id in models:
            continue
        matched_key, confidence, reason = _resolve_match(
            model_id,
            upstream_payload,
            reviewed_matches,
        )
        if matched_key is None:
            unpriced_requested.append(model_id)
            continue
        try:
            fresh_rate = _convert_upstream_rate(
                upstream_payload[matched_key],
                maximum_cost=normalized_policy["maximum_cost_per_token"],
            )
        except PricingUpdateError as exc:
            warnings.append(f"{model_id}: {exc}")
            unpriced_requested.append(model_id)
            continue
        fresh_rate["provenance"] = {
            "source": normalized_policy["source_name"],
            "matched_key": matched_key,
            "fetched_at": fetched_at,
            "stale": False,
            "confidence": confidence,
            "reason": reason,
        }
        models[model_id] = fresh_rate
        fresh_count += 1

    stale_count = sum(
        1 for rate in models.values() if rate["provenance"]["stale"]
    )
    candidate: dict[str, Any] = {
        "schema_version": 1,
        "snapshot_id": "pending",
        "generated_at": fetched_at,
        "upstreams": [
            {
                "name": normalized_policy["source_name"],
                "url": normalized_policy["source_url"],
                "revision": normalized_policy["source_revision"],
                "sha256": normalized_policy["source_sha256"],
            },
            {
                "name": "ModelDial previous valid snapshot",
                "snapshot_id": previous_snapshot["snapshot_id"],
            },
        ],
        "models": models,
        "aliases": deepcopy(previous_snapshot.get("aliases", {})),
    }
    _validate_candidate(candidate, normalized_policy, fresh_count=fresh_count)
    content_hash = _content_hash(candidate)
    previous_hash = _content_hash(previous_snapshot)
    base_report = {
        "schema_version": 1,
        "checked_at": fetched_at,
        "previous_snapshot_id": previous_snapshot["snapshot_id"],
        "snapshot_id": previous_snapshot["snapshot_id"],
        "changed": content_hash != previous_hash,
        "applied": False,
        "model_count": len(models),
        "fresh_model_count": fresh_count,
        "stale_model_count": stale_count,
        "stale_models": sorted(
            model_id
            for model_id, rate in models.items()
            if rate["provenance"]["stale"]
        ),
        "unpriced_requested_models": unpriced_requested,
        "warnings": warnings,
        "errors": [],
    }
    if content_hash == previous_hash:
        return PricingUpdateOutcome(
            candidate=None,
            report={**base_report, "status": "unchanged"},
        )

    candidate["content_hash"] = content_hash
    candidate["snapshot_id"] = f"pricing-v1-{content_hash}"
    return PricingUpdateOutcome(
        candidate=candidate,
        report={
            **base_report,
            "status": "candidate_ready",
            "snapshot_id": candidate["snapshot_id"],
        },
    )


def execute_update(
    *,
    snapshot_path: Path,
    upstream_payload: dict[str, Any],
    policy: dict[str, Any],
    fetched_at: str,
    candidate_path: Path,
    report_path: Path,
    apply: bool,
    requested_models: tuple[str, ...] = (),
) -> dict[str, Any]:
    previous_snapshot_id: str | None = None
    try:
        previous = load_json_object(snapshot_path)
        raw_previous_id = previous.get("snapshot_id")
        previous_snapshot_id = (
            raw_previous_id if isinstance(raw_previous_id, str) else None
        )
        outcome = build_update(
            previous,
            upstream_payload,
            policy,
            fetched_at=fetched_at,
            requested_models=requested_models,
        )
        report = dict(outcome.report)
        if outcome.candidate is not None:
            _atomic_write_json(candidate_path, outcome.candidate)
            if apply:
                current = load_json_object(snapshot_path)
                if current.get("snapshot_id") != previous_snapshot_id:
                    raise PricingUpdateError(
                        "previous snapshot changed while update was running"
                    )
                _atomic_write_json(snapshot_path, outcome.candidate)
                report["status"] = "applied"
                report["applied"] = True
        _atomic_write_json(report_path, report)
        return report
    except Exception as exc:
        report = {
            "schema_version": 1,
            "checked_at": fetched_at,
            "status": "failed",
            "previous_snapshot_id": previous_snapshot_id,
            "snapshot_id": previous_snapshot_id,
            "changed": False,
            "applied": False,
            "errors": [str(exc)],
            "warnings": [],
            "unpriced_requested_models": [],
        }
        _atomic_write_json(report_path, report)
        return report


def record_failed_update(
    *,
    snapshot_path: Path,
    report_path: Path,
    fetched_at: str,
    error: Exception,
) -> dict[str, Any]:
    snapshot_id = None
    try:
        raw_snapshot_id = load_json_object(snapshot_path).get("snapshot_id")
        if isinstance(raw_snapshot_id, str):
            snapshot_id = raw_snapshot_id
    except Exception:
        pass
    report = {
        "schema_version": 1,
        "checked_at": fetched_at,
        "status": "failed",
        "previous_snapshot_id": snapshot_id,
        "snapshot_id": snapshot_id,
        "changed": False,
        "applied": False,
        "errors": [str(error)],
        "warnings": [],
        "unpriced_requested_models": [],
    }
    _atomic_write_json(report_path, report)
    return report


def _validate_policy(policy: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(policy, dict) or policy.get("schema_version") != 1:
        raise PricingUpdateError("unsupported pricing update policy schema")
    result = deepcopy(policy)
    for key in (
        "source_name",
        "source_url",
        "source_revision",
        "source_sha256",
    ):
        if not isinstance(result.get(key), str) or not result[key].strip():
            raise PricingUpdateError(f"policy {key} is required")
    if not re.fullmatch(r"[0-9a-f]{40}", result["source_revision"]):
        raise PricingUpdateError("policy source_revision must be a full Git commit")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", result["source_sha256"]):
        raise PricingUpdateError("policy source_sha256 must be a SHA-256 digest")
    if f"/{result['source_revision']}/" not in result["source_url"]:
        raise PricingUpdateError("policy source_url must contain source_revision")
    for key in (
        "minimum_upstream_entry_count",
        "minimum_model_count",
        "minimum_fresh_model_count",
    ):
        value = result.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise PricingUpdateError(f"policy {key} must be a nonnegative integer")
    maximum_cost = _number(result.get("maximum_cost_per_token"), "maximum cost")
    if maximum_cost <= 0:
        raise PricingUpdateError("policy maximum cost must be positive")
    result["maximum_cost_per_token"] = maximum_cost
    prefixes = result.get("required_priceable_prefixes")
    if not isinstance(prefixes, list) or any(
        not isinstance(prefix, str) or not prefix for prefix in prefixes
    ):
        raise PricingUpdateError("policy required priceable prefixes are invalid")
    reviewed = result.get("reviewed_matches")
    if not isinstance(reviewed, dict) or any(
        not isinstance(local, str)
        or not local
        or not isinstance(upstream, str)
        or not upstream
        for local, upstream in reviewed.items()
    ):
        raise PricingUpdateError("policy reviewed matches are invalid")
    return result


def _validate_snapshot(
    snapshot: dict[str, Any],
    *,
    require_provenance: bool,
    maximum_cost: float | None = None,
) -> None:
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1:
        raise PricingUpdateError("unsupported pricing snapshot schema")
    if not isinstance(snapshot.get("snapshot_id"), str) or not snapshot["snapshot_id"]:
        raise PricingUpdateError("pricing snapshot id is required")
    models = snapshot.get("models")
    aliases = snapshot.get("aliases", {})
    if not isinstance(models, dict) or not models:
        raise PricingUpdateError("pricing snapshot models are required")
    if not isinstance(aliases, dict):
        raise PricingUpdateError("pricing snapshot aliases must be an object")
    for model_id, rate in models.items():
        if not isinstance(model_id, str) or not model_id or not isinstance(rate, dict):
            raise PricingUpdateError("invalid pricing model entry")
        _validate_rate(rate, maximum_cost=maximum_cost)
        if require_provenance:
            _validate_provenance(rate.get("provenance"), model_id)
    if any(
        not isinstance(alias, str)
        or not isinstance(target, str)
        or target not in models
        for alias, target in aliases.items()
    ):
        raise PricingUpdateError("pricing alias references an unknown model")


def _validate_candidate(
    candidate: dict[str, Any],
    policy: dict[str, Any],
    *,
    fresh_count: int,
) -> None:
    _validate_snapshot(
        candidate,
        require_provenance=True,
        maximum_cost=policy["maximum_cost_per_token"],
    )
    models = candidate["models"]
    if len(models) < policy["minimum_model_count"]:
        raise PricingUpdateError(
            f"candidate model count {len(models)} is below minimum "
            f"{policy['minimum_model_count']}"
        )
    if fresh_count < policy["minimum_fresh_model_count"]:
        raise PricingUpdateError(
            f"fresh model count {fresh_count} is below minimum "
            f"{policy['minimum_fresh_model_count']}"
        )
    folded_ids = tuple(model_id.casefold() for model_id in models)
    missing_prefixes = [
        prefix
        for prefix in policy["required_priceable_prefixes"]
        if not any(model_id.startswith(prefix.casefold()) for model_id in folded_ids)
    ]
    if missing_prefixes:
        raise PricingUpdateError(
            "candidate lacks required priceable model prefixes: "
            + ", ".join(missing_prefixes)
        )


def _resolve_match(
    model_id: str,
    upstream: dict[str, Any],
    reviewed_matches: dict[str, str],
) -> tuple[str | None, str | None, str | None]:
    if model_id in upstream:
        return model_id, "exact", "exact_model_id"
    reviewed_key = reviewed_matches.get(model_id)
    if reviewed_key is not None and reviewed_key in upstream:
        return reviewed_key, "reviewed", "reviewed_alias"
    return None, None, None


def _convert_upstream_rate(
    raw: Any,
    *,
    maximum_cost: float,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PricingUpdateError("matched upstream entry must be an object")
    mode = raw.get("mode")
    if mode not in _TEXT_MODES:
        raise PricingUpdateError(f"matched upstream entry has unsupported mode {mode!r}")
    result: dict[str, Any] = {
        "input_per_token": _bounded_rate(
            raw.get("input_cost_per_token"),
            "input_cost_per_token",
            maximum_cost,
        ),
        "output_per_token": _bounded_rate(
            raw.get("output_cost_per_token"),
            "output_cost_per_token",
            maximum_cost,
        ),
    }
    optional_fields = {
        "cached_input_per_token": (
            "cache_read_input_token_cost",
            "input_cost_per_token_cache_hit",
        ),
        "cache_write_input_per_token": ("cache_creation_input_token_cost",),
        "reasoning_output_per_token": ("output_cost_per_reasoning_token",),
    }
    for target, source_keys in optional_fields.items():
        source_value = next(
            (raw[key] for key in source_keys if raw.get(key) is not None),
            None,
        )
        if source_value is not None:
            result[target] = _bounded_rate(
                source_value,
                source_keys[0],
                maximum_cost,
            )
    if result["input_per_token"] == 0 and result["output_per_token"] == 0:
        raise PricingUpdateError("matched upstream entry has no positive token price")
    long_context = _long_context_rate(raw, result, maximum_cost=maximum_cost)
    if long_context is not None:
        result["long_context"] = long_context
    return result


def _long_context_rate(
    raw: dict[str, Any],
    base: dict[str, Any],
    *,
    maximum_cost: float,
) -> dict[str, Any] | None:
    thresholds = sorted(
        int(match.group(1))
        for key in raw
        if (match := _LONG_CONTEXT_INPUT.fullmatch(key)) is not None
    )
    for threshold_k in thresholds:
        suffix = f"above_{threshold_k}k_tokens"
        input_value = raw.get(f"input_cost_per_token_{suffix}")
        output_value = raw.get(f"output_cost_per_token_{suffix}")
        if input_value is None or output_value is None:
            continue
        input_rate = _bounded_rate(input_value, f"input {suffix}", maximum_cost)
        output_rate = _bounded_rate(output_value, f"output {suffix}", maximum_cost)
        if base["input_per_token"] <= 0 or base["output_per_token"] <= 0:
            continue
        return {
            "threshold_tokens": threshold_k * 1000,
            "input_multiplier": input_rate / base["input_per_token"],
            "output_multiplier": output_rate / base["output_per_token"],
        }
    return None


def _preserve_stale_rate(
    model_id: str,
    previous_rate: dict[str, Any],
    previous_snapshot: dict[str, Any],
) -> dict[str, Any]:
    result = deepcopy(previous_rate)
    previous_provenance = result.pop("provenance", None)
    previous_provenance = (
        previous_provenance if isinstance(previous_provenance, dict) else {}
    )
    result["provenance"] = {
        "source": previous_provenance.get("source", "ModelDial previous snapshot"),
        "matched_key": previous_provenance.get("matched_key", model_id),
        "fetched_at": previous_provenance.get(
            "fetched_at",
            previous_snapshot.get("generated_at", "unknown"),
        ),
        "stale": True,
        "confidence": previous_provenance.get("confidence", "curated"),
        "reason": "upstream_missing_preserved",
    }
    return result


def _validate_rate(rate: dict[str, Any], *, maximum_cost: float | None) -> None:
    for key in ("input_per_token", "output_per_token"):
        _checked_snapshot_rate(rate.get(key), key, maximum_cost)
    for key in (
        "cached_input_per_token",
        "cache_write_input_per_token",
        "reasoning_output_per_token",
    ):
        if rate.get(key) is not None:
            _checked_snapshot_rate(rate[key], key, maximum_cost)
    long_context = rate.get("long_context")
    if long_context is not None:
        if not isinstance(long_context, dict):
            raise PricingUpdateError("long context pricing must be an object")
        threshold = long_context.get("threshold_tokens")
        if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold <= 0:
            raise PricingUpdateError("long context threshold must be positive")
        for key in ("input_multiplier", "output_multiplier"):
            multiplier = _number(long_context.get(key), key)
            if multiplier <= 0:
                raise PricingUpdateError(f"{key} must be positive")


def _validate_provenance(raw: Any, model_id: str) -> None:
    if not isinstance(raw, dict):
        raise PricingUpdateError(f"{model_id} pricing provenance is required")
    for key in ("source", "matched_key", "fetched_at", "confidence", "reason"):
        if not isinstance(raw.get(key), str) or not raw[key]:
            raise PricingUpdateError(f"{model_id} provenance {key} is required")
    if not isinstance(raw.get("stale"), bool):
        raise PricingUpdateError(f"{model_id} provenance stale must be boolean")


def _bounded_rate(value: Any, name: str, maximum_cost: float) -> float:
    parsed = _number(value, name)
    if parsed < 0 or parsed > maximum_cost:
        raise PricingUpdateError(f"{name} is outside the allowed price range")
    return parsed


def _checked_snapshot_rate(value: Any, name: str, maximum_cost: float | None) -> float:
    parsed = _number(value, name)
    if parsed < 0 or (maximum_cost is not None and parsed > maximum_cost):
        raise PricingUpdateError(f"{name} is outside the allowed price range")
    return parsed


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PricingUpdateError(f"{name} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise PricingUpdateError(f"{name} must be finite")
    return parsed


def _content_hash(snapshot: dict[str, Any]) -> str:
    models = deepcopy(snapshot.get("models", {}))
    if isinstance(models, dict):
        for rate in models.values():
            if not isinstance(rate, dict):
                continue
            provenance = rate.get("provenance")
            if isinstance(provenance, dict):
                provenance.pop("fetched_at", None)
    semantic = {
        "schema_version": snapshot.get("schema_version"),
        "models": models,
        "aliases": deepcopy(snapshot.get("aliases", {})),
        "upstreams": [
            deepcopy(upstream)
            for upstream in snapshot.get("upstreams", [])
            if isinstance(upstream, dict)
            and upstream.get("revision")
            and upstream.get("sha256")
        ],
    }
    encoded = json.dumps(
        semantic,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary_path = Path(handle.name)
    try:
        with handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
