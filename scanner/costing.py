from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Mapping


@dataclass(frozen=True)
class ReferenceCostEstimate:
    usd: float | None
    status: str
    pricing_snapshot: str | None


@dataclass(frozen=True)
class _Rates:
    input_per_token: float
    cached_input_per_token: float
    cache_write_input_per_token: float
    output_per_token: float
    reasoning_output_per_token: float | None = None
    long_context_threshold: int | None = None
    long_context_input_multiplier: float = 1.0
    long_context_output_multiplier: float = 1.0


def _pricing_snapshot_path() -> Path:
    configured_data_root = os.environ.get("MODELDIAL_DATA_DIR", "").strip()
    if configured_data_root:
        persisted = (
            Path(configured_data_root).expanduser()
            / "pricing"
            / "current.json"
        )
        if persisted.is_file():
            return persisted
    configured_root = os.environ.get("MODELDIAL_BACKEND_ROOT", "").strip()
    if configured_root:
        external = (
            Path(configured_root).expanduser()
            / "scanner"
            / "pricing_snapshot.json"
        )
        if external.is_file():
            return external
    return Path(__file__).with_name("pricing_snapshot.json")


def _load_pricing_snapshot(path: Path) -> tuple[str, dict[str, _Rates], dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported pricing snapshot schema")
    snapshot_id = payload.get("snapshot_id")
    raw_models = payload.get("models")
    raw_aliases = payload.get("aliases", {})
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise ValueError("pricing snapshot id is required")
    if not isinstance(raw_models, dict) or not raw_models:
        raise ValueError("pricing snapshot models are required")
    if not isinstance(raw_aliases, dict):
        raise ValueError("pricing snapshot aliases must be an object")

    rates: dict[str, _Rates] = {}
    for model_id, raw_rate in raw_models.items():
        if not isinstance(model_id, str) or not isinstance(raw_rate, dict):
            raise ValueError("invalid pricing model entry")
        input_rate = _nonnegative_float(raw_rate, "input_per_token")
        cached_rate = _nonnegative_float(
            raw_rate,
            "cached_input_per_token",
            default=input_rate,
        )
        cache_write_rate = _nonnegative_float(
            raw_rate,
            "cache_write_input_per_token",
            default=input_rate,
        )
        long_context = raw_rate.get("long_context")
        if long_context is not None and not isinstance(long_context, dict):
            raise ValueError(f"invalid long context pricing for {model_id}")
        long_context = long_context or {}
        threshold = long_context.get("threshold_tokens")
        if threshold is not None and (not isinstance(threshold, int) or threshold <= 0):
            raise ValueError(f"invalid long context threshold for {model_id}")
        rates[model_id.casefold()] = _Rates(
            input_per_token=input_rate,
            cached_input_per_token=cached_rate,
            cache_write_input_per_token=cache_write_rate,
            output_per_token=_nonnegative_float(raw_rate, "output_per_token"),
            reasoning_output_per_token=_optional_nonnegative_float(
                raw_rate.get("reasoning_output_per_token")
            ),
            long_context_threshold=threshold,
            long_context_input_multiplier=_positive_float(
                long_context.get("input_multiplier"),
                default=1.0,
            ),
            long_context_output_multiplier=_positive_float(
                long_context.get("output_multiplier"),
                default=1.0,
            ),
        )

    aliases = {
        str(alias).casefold(): str(model_id).casefold()
        for alias, model_id in raw_aliases.items()
    }
    if any(model_id not in rates for model_id in aliases.values()):
        raise ValueError("pricing alias references an unknown model")
    return snapshot_id, rates, aliases


def _nonnegative_float(
    payload: dict[str, object],
    key: str,
    *,
    default: float | None = None,
) -> float:
    value = payload.get(key, default)
    parsed = _optional_nonnegative_float(value)
    if parsed is None:
        raise ValueError(f"pricing field {key} is required")
    return parsed


def _optional_nonnegative_float(value: object) -> float | None:
    if value is None:
        return None
    parsed = float(value)
    if parsed < 0:
        raise ValueError("pricing values cannot be negative")
    return parsed


def _positive_float(value: object, *, default: float) -> float:
    if value is None:
        return default
    parsed = float(value)
    if parsed <= 0:
        raise ValueError("pricing multipliers must be positive")
    return parsed


PRICING_SNAPSHOT, _RATES, _ALIASES = _load_pricing_snapshot(
    _pricing_snapshot_path()
)
_PRICING_LOCK = RLock()
_REASONING_SUFFIXES = ("ultra", "xhigh", "medium", "high", "low", "max")


def install_pricing_snapshot(path: Path) -> str:
    snapshot_id, rates, aliases = _load_pricing_snapshot(path)
    global PRICING_SNAPSHOT, _RATES, _ALIASES
    with _PRICING_LOCK:
        PRICING_SNAPSHOT = snapshot_id
        _RATES = rates
        _ALIASES = aliases
    return snapshot_id


def validate_pricing_snapshot(path: Path) -> str:
    snapshot_id, _, _ = _load_pricing_snapshot(path)
    return snapshot_id


def current_pricing_snapshot_id() -> str:
    with _PRICING_LOCK:
        return PRICING_SNAPSHOT


def estimate_reference_cost(
    model: str,
    *,
    input_tokens: int | None,
    cached_input_tokens: int | None,
    output_tokens: int | None,
    cache_write_input_tokens: int | None = None,
    reasoning_output_tokens: int | None = None,
) -> ReferenceCostEstimate:
    if input_tokens is None or output_tokens is None:
        return ReferenceCostEstimate(None, "unavailable", None)

    with _PRICING_LOCK:
        pricing_snapshot = PRICING_SNAPSHOT
        rates = _RATES.get(_canonical_model(model))
    if rates is None:
        return ReferenceCostEstimate(None, "unpriced", pricing_snapshot)

    return _estimate_reference_cost_with_rates(
        rates,
        pricing_snapshot=pricing_snapshot,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        cache_write_input_tokens=cache_write_input_tokens,
        output_tokens=output_tokens,
        reasoning_output_tokens=reasoning_output_tokens,
    )


def frozen_reference_pricing(model: str) -> dict[str, object] | None:
    with _PRICING_LOCK:
        pricing_snapshot = PRICING_SNAPSHOT
        canonical_model = _canonical_model(model)
        rates = _RATES.get(canonical_model)
    if rates is None:
        return None
    return {
        "schema_version": 1,
        "pricing_snapshot_id": pricing_snapshot,
        "canonical_model_id": canonical_model,
        "input_per_token": rates.input_per_token,
        "cached_input_per_token": rates.cached_input_per_token,
        "cache_write_input_per_token": rates.cache_write_input_per_token,
        "output_per_token": rates.output_per_token,
        "reasoning_output_per_token": rates.reasoning_output_per_token,
        "long_context_threshold": rates.long_context_threshold,
        "long_context_input_multiplier": rates.long_context_input_multiplier,
        "long_context_output_multiplier": rates.long_context_output_multiplier,
    }


def estimate_reference_cost_from_frozen_pricing(
    pricing: Mapping[str, object],
    *,
    input_tokens: int | None,
    cached_input_tokens: int | None,
    output_tokens: int | None,
    cache_write_input_tokens: int | None = None,
    reasoning_output_tokens: int | None = None,
) -> ReferenceCostEstimate:
    snapshot_id = pricing.get("pricing_snapshot_id")
    try:
        rates = _Rates(
            input_per_token=float(pricing["input_per_token"]),
            cached_input_per_token=float(pricing["cached_input_per_token"]),
            cache_write_input_per_token=float(
                pricing["cache_write_input_per_token"]
            ),
            output_per_token=float(pricing["output_per_token"]),
            reasoning_output_per_token=(
                float(pricing["reasoning_output_per_token"])
                if pricing.get("reasoning_output_per_token") is not None
                else None
            ),
            long_context_threshold=(
                int(pricing["long_context_threshold"])
                if pricing.get("long_context_threshold") is not None
                else None
            ),
            long_context_input_multiplier=float(
                pricing.get("long_context_input_multiplier", 1.0)
            ),
            long_context_output_multiplier=float(
                pricing.get("long_context_output_multiplier", 1.0)
            ),
        )
    except (KeyError, TypeError, ValueError):
        return ReferenceCostEstimate(None, "unavailable", None)
    if any(
        value < 0
        for value in (
            rates.input_per_token,
            rates.cached_input_per_token,
            rates.cache_write_input_per_token,
            rates.output_per_token,
        )
    ):
        return ReferenceCostEstimate(None, "unavailable", None)
    return _estimate_reference_cost_with_rates(
        rates,
        pricing_snapshot=(
            str(snapshot_id) if isinstance(snapshot_id, str) and snapshot_id else None
        ),
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        cache_write_input_tokens=cache_write_input_tokens,
        output_tokens=output_tokens,
        reasoning_output_tokens=reasoning_output_tokens,
    )


def _estimate_reference_cost_with_rates(
    rates: _Rates,
    *,
    pricing_snapshot: str | None,
    input_tokens: int | None,
    cached_input_tokens: int | None,
    output_tokens: int | None,
    cache_write_input_tokens: int | None,
    reasoning_output_tokens: int | None,
) -> ReferenceCostEstimate:
    if input_tokens is None or output_tokens is None:
        return ReferenceCostEstimate(None, "unavailable", None)

    total_input = max(0, input_tokens)
    cached_input = min(total_input, max(0, cached_input_tokens or 0))
    remaining_input = total_input - cached_input
    cache_write_input = min(
        remaining_input,
        max(0, cache_write_input_tokens or 0),
    )
    uncached_input = remaining_input - cache_write_input
    output = max(0, output_tokens)
    reasoning_output = min(output, max(0, reasoning_output_tokens or 0))
    standard_output = (
        output - reasoning_output
        if rates.reasoning_output_per_token is not None
        else output
    )
    input_multiplier = 1.0
    output_multiplier = 1.0
    if (
        rates.long_context_threshold is not None
        and total_input > rates.long_context_threshold
    ):
        input_multiplier = rates.long_context_input_multiplier
        output_multiplier = rates.long_context_output_multiplier

    usd = (
        uncached_input * rates.input_per_token * input_multiplier
        + cached_input * rates.cached_input_per_token * input_multiplier
        + cache_write_input * rates.cache_write_input_per_token * input_multiplier
        + standard_output * rates.output_per_token * output_multiplier
    )
    if rates.reasoning_output_per_token is not None:
        usd += (
            reasoning_output
            * rates.reasoning_output_per_token
            * output_multiplier
        )
    return ReferenceCostEstimate(usd, "estimated", pricing_snapshot)


def _canonical_model(model: str) -> str:
    normalized = model.strip().casefold()
    candidates = [normalized]
    parts = normalized.split("/")
    candidates.extend("/".join(parts[index:]) for index in range(1, len(parts)))
    for candidate in candidates:
        resolved = _canonical_candidate(candidate)
        if resolved in _RATES:
            return resolved
    return normalized


def _canonical_candidate(model: str) -> str:
    if model in _ALIASES:
        return _ALIASES[model]
    for name in sorted(_RATES, key=len, reverse=True):
        if model == name or model.startswith(f"{name}-202"):
            return name
    for suffix in _REASONING_SUFFIXES:
        marker = f"-{suffix}"
        if model.endswith(marker):
            base = model.removesuffix(marker)
            if base in _RATES:
                return base
    return model
