from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

from .leaderboard_decision_tags import assign_leaderboard_decision_tags


REFERENCE_SNAPSHOT_SCHEMA_VERSION = 1
REFERENCE_LEADERBOARD_SCHEMA_VERSION = 2
REFERENCE_RANKING_RULE = (
    "score_desc_hard_failure_asc_elapsed_asc_cost_asc_identity_v1"
)
REFERENCE_TREND_RULE = "latest_6_same_configuration_protocol_route_v1"
REFERENCE_SNAPSHOT_URL_ENV = "MODELDIAL_REFERENCE_SNAPSHOT_URL"
DEFAULT_REFERENCE_SNAPSHOT_URL = ""
DEFAULT_REFERENCE_SNAPSHOT_TIMEOUT_SECONDS = 8.0
MAX_INDEX_BYTES = 1024 * 1024
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
# Keep the wire value below the largest duration that the Swift UI can round
# into a signed Int without trapping.  This is intentionally a conversion
# guard, not a product timeout; scan policy remains responsible for normal
# duration limits.
MAX_REFERENCE_ELAPSED_MS = float(2**52) * 1_000
_HTTP_CACHE_METADATA_NAME = ".http-cache.json"
_HTTP_CACHE_BUNDLE_NAME = ".http-cache-bundle.json"
_HTTP_CACHE_BUNDLE_SCHEMA_VERSION = 1
_REFERENCE_TREND_WINDOW = 6
_TARGET_LABELS = (
    ("highest_score", "Highest score"),
    ("fastest", "Fastest"),
    ("lowest_cost", "Lowest cost"),
)


class ReferenceSnapshotDownloadError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class _HttpJsonResponse:
    payload: dict[str, object] | None
    etag: str | None
    not_modified: bool = False


@dataclass(frozen=True)
class _DownloadedReferenceSnapshotFeed:
    index: dict[str, object] | None
    latest: dict[str, object] | None
    snapshots: tuple[dict[str, object], ...]
    index_etag: str | None
    not_modified: bool = False


def load_reference_snapshot_feed(root: Path | None = None) -> dict[str, object]:
    snapshot_root = root or _reference_snapshot_root()
    bundle = _load_reference_snapshot_bundle(snapshot_root)
    if bundle["status"] == "loaded":
        return bundle
    return _load_reference_snapshot_feed_root(snapshot_root)


def _load_reference_snapshot_feed_root(snapshot_root: Path) -> dict[str, object]:
    index_path = snapshot_root / "index.json"
    if not index_path.is_file():
        return _empty_feed("missing")

    try:
        index = _read_json_object(index_path)
        kind, summaries, latest_batch_id, latest_path = (
            _validate_reference_snapshot_index(index)
        )

        snapshots: list[dict[str, object]] = []
        for summary in summaries:
            relative_path = _required_text(summary, "path")
            snapshot_path = _safe_child(snapshot_root, relative_path)
            snapshot = validate_reference_snapshot(
                _read_json_object(snapshot_path)
            )
            _validate_indexed_snapshot(kind, summary, snapshot)
            snapshots.append(snapshot)

        latest_summary = next(
            summary
            for summary in summaries
            if summary["batch_id"] == latest_batch_id
        )
        latest = next(
            (
                snapshot
                for snapshot in snapshots
                if snapshot["batch_id"] == latest_batch_id
            ),
            None,
        )
        if latest is None:
            raise ValueError("latest reference snapshot is missing from index")
        indexed_latest = validate_reference_snapshot(
            _read_json_object(_safe_child(snapshot_root, latest_path))
        )
        _validate_indexed_snapshot(kind, latest_summary, indexed_latest)
        if indexed_latest != latest:
            raise ValueError("latest reference snapshot does not match archive")
        _validate_optional_latest_copy(
            snapshot_root,
            kind=kind,
            summaries=summaries,
            latest=latest,
        )
        return {
            "schema_version": REFERENCE_SNAPSHOT_SCHEMA_VERSION,
            "status": "loaded",
            "kind": kind,
            "latest": latest,
            "snapshots": snapshots,
        }
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return _empty_feed("invalid")


def load_reference_snapshot_feed_for_app(
    *,
    cache_root: Path,
    base_url: str | None = None,
    timeout_seconds: float = DEFAULT_REFERENCE_SNAPSHOT_TIMEOUT_SECONDS,
) -> dict[str, object]:
    configured_url = _configured_reference_snapshot_url(base_url)
    if not configured_url:
        cached = load_reference_snapshot_feed(cache_root)
        if cached["status"] == "loaded":
            return _with_delivery(cached, "cache", "not_configured")
        return _with_delivery(
            load_reference_snapshot_feed(),
            "bundled",
            "not_configured",
        )

    index_url = ""
    try:
        index_url = _same_origin_url(
            _normalize_base_url(configured_url),
            "index.json",
        )
        cached = _load_reference_snapshot_cache_for_url(cache_root, index_url)
        downloaded = _download_reference_snapshot_feed(
            configured_url,
            cached_feed=cached,
            index_etag=_read_reference_snapshot_index_etag(
                cache_root,
                index_url,
            ),
            timeout_seconds=timeout_seconds,
        )
        if downloaded.not_modified:
            if cached["status"] != "loaded":
                raise ReferenceSnapshotDownloadError("invalid_payload")
            return _with_delivery(cached, "http", "not_modified")
        if downloaded.index is None or downloaded.latest is None:
            raise ReferenceSnapshotDownloadError("invalid_payload")
        _write_reference_snapshot_cache(
            cache_root,
            index=downloaded.index,
            latest=downloaded.latest,
            snapshots=downloaded.snapshots,
            index_url=index_url,
            index_etag=downloaded.index_etag,
        )
        loaded = load_reference_snapshot_feed(cache_root)
        if loaded["status"] != "loaded":
            raise ReferenceSnapshotDownloadError("invalid_payload")
        return _with_delivery(loaded, "http", "refreshed")
    except ReferenceSnapshotDownloadError as error:
        return _reference_snapshot_fallback(cache_root, error.code, index_url)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return _reference_snapshot_fallback(
            cache_root,
            "cache_write_failed",
            index_url,
        )


def read_reference_snapshot_feed_for_app(
    *,
    cache_root: Path,
    base_url: str | None = None,
) -> dict[str, object]:
    configured_url = _configured_reference_snapshot_url(base_url)
    if configured_url:
        try:
            index_url = _same_origin_url(
                _normalize_base_url(configured_url),
                "index.json",
            )
        except ReferenceSnapshotDownloadError as error:
            return _reference_snapshot_fallback(cache_root, error.code, "")
        cached = _load_reference_snapshot_cache_for_url(cache_root, index_url)
    else:
        cached = load_reference_snapshot_feed(cache_root)
    if cached["status"] == "loaded":
        return _with_delivery(
            cached,
            "http" if configured_url else "cache",
            "cached" if configured_url else "not_configured",
        )
    return _with_delivery(
        load_reference_snapshot_feed(),
        "bundled",
        "not_cached" if configured_url else "not_configured",
    )


def _reference_snapshot_fallback(
    cache_root: Path,
    error_code: str,
    index_url: str,
) -> dict[str, object]:
    if index_url:
        cached = _load_reference_snapshot_cache_for_url(cache_root, index_url)
    else:
        cached = _empty_feed("missing")
    if cached["status"] == "loaded":
        return _with_delivery(
            cached,
            "cache",
            "failed",
            error_code=error_code,
        )
    return _with_delivery(
        load_reference_snapshot_feed(),
        "bundled",
        "failed",
        error_code=error_code,
    )


def _configured_reference_snapshot_url(base_url: str | None) -> str:
    if base_url is not None:
        return base_url.strip()
    return os.environ.get(
        REFERENCE_SNAPSHOT_URL_ENV,
        DEFAULT_REFERENCE_SNAPSHOT_URL,
    ).strip()


def validate_reference_snapshot(
    payload: Mapping[str, object],
) -> dict[str, object]:
    snapshot = dict(payload)
    if snapshot.get("schema_version") != REFERENCE_SNAPSHOT_SCHEMA_VERSION:
        raise ValueError("unsupported reference snapshot schema")
    _required_text(snapshot, "kind")
    _required_text(snapshot, "batch_id")
    _required_text(snapshot, "published_at")
    _required_text(snapshot, "question_pack_version")
    _required_text(snapshot, "grader_version")
    _required_text(snapshot, "score_baseline_id")
    if _required_text(snapshot, "status") != "complete":
        raise ValueError("reference snapshot is not complete")
    entries = _mapping_items(snapshot.get("entries"))
    if int(snapshot.get("entry_count") or 0) != len(entries) or not entries:
        raise ValueError("reference snapshot entry count mismatch")

    planned_configuration_ids = snapshot.get("planned_configuration_ids")
    question_ids = snapshot.get("question_ids")
    if snapshot.get("kind") == "first_party_snapshot":
        if not _required_text(snapshot, "runner_commit"):
            raise ValueError("first-party snapshot runner commit is required")
        environment = snapshot.get("environment")
        if not isinstance(environment, Mapping):
            raise ValueError("first-party snapshot environment is required")
        for key in ("os", "app_version", "codex_version", "machine_profile"):
            value = _required_text(environment, key)
            if value.lower() in {"unknown", "development"}:
                raise ValueError("first-party snapshot environment is incomplete")
        retry_policy = snapshot.get("retry_policy")
        if not isinstance(retry_policy, Mapping):
            raise ValueError("first-party snapshot retry policy is required")
        if (
            retry_policy.get("schema_version") != 1
            or retry_policy.get("mode") != "app_rules_v1"
            or retry_policy.get("selective_score_retry") is not False
            or not isinstance(retry_policy.get("rules"), Mapping)
        ):
            raise ValueError("first-party snapshot retry policy is invalid")
        grader_replay = snapshot.get("grader_replay")
        if not isinstance(grader_replay, Mapping):
            raise ValueError("first-party snapshot grader replay is required")
        if (
            grader_replay.get("status") != "matched"
            or grader_replay.get("method") != "independent_regrade"
        ):
            raise ValueError("first-party snapshot grader replay is invalid")
        _required_text(grader_replay, "regraded_at")
        for key in ("raw_answer_bundle_sha256", "manifest_sha256"):
            if not _is_sha256(_required_text(grader_replay, key)):
                raise ValueError("first-party snapshot grader replay hash is invalid")
        if not isinstance(planned_configuration_ids, list) or not isinstance(
            question_ids, list
        ):
            raise ValueError("first-party snapshot manifest is required")
        if len(question_ids) != 5 or len(set(question_ids)) != 5:
            raise ValueError("first-party snapshot must contain five unique questions")

    seen_ids: set[str] = set()
    for entry in entries:
        configuration_id = _validate_reference_snapshot_entry(entry)
        if configuration_id in seen_ids:
            raise ValueError("duplicate reference snapshot configuration")
        seen_ids.add(configuration_id)
        score = _bounded_number(entry.get("score"), 0, 100)
        max_score = _bounded_number(entry.get("max_score"), 1, 100)
        if score > max_score:
            raise ValueError("reference snapshot score exceeds maximum")
        raw_question_scores = entry.get("question_scores")
        if not isinstance(raw_question_scores, Mapping):
            raise ValueError("reference snapshot question scores are required")
        if not isinstance(entry.get("model_configuration"), Mapping):
            raise ValueError("reference snapshot model configuration is required")
        if snapshot.get("kind") == "first_party_snapshot":
            model_configuration = entry["model_configuration"]
            if set(raw_question_scores) != set(question_ids):
                raise ValueError("first-party snapshot question manifest mismatch")
            normalized_total = round(
                sum(
                    _bounded_number(value, 0, 20)
                    for value in raw_question_scores.values()
                ),
                3,
            )
            if abs(normalized_total - score) > 0.001:
                raise ValueError("first-party snapshot score total mismatch")
            if entry.get("score_integrity") != "first_party_controlled":
                raise ValueError("first-party snapshot score integrity is invalid")
            if entry.get("route_identity") != "first_party_controlled":
                raise ValueError("first-party snapshot route identity is invalid")
            if (
                model_configuration.get("route_type") == "custom_endpoint"
                and not _required_text(entry, "route_fingerprint")
            ):
                raise ValueError("first-party endpoint route evidence is required")
            _required_text(entry, "run_manifest_sha256")

    if snapshot.get("kind") == "first_party_snapshot":
        if len(planned_configuration_ids) != len(set(planned_configuration_ids)):
            raise ValueError("first-party snapshot configuration manifest is duplicated")
        if set(planned_configuration_ids) != seen_ids:
            raise ValueError("first-party snapshot configuration manifest mismatch")

    if snapshot.get("leaderboard_projection") is not None:
        _validate_reference_snapshot_leaderboard_projection(
            snapshot,
            entries=entries,
            question_ids=question_ids if isinstance(question_ids, list) else None,
        )

    expected_hash = _required_text(snapshot, "batch_sha256")
    if expected_hash != reference_snapshot_hash(snapshot):
        raise ValueError("reference snapshot batch hash mismatch")
    return snapshot


def _validate_reference_snapshot_entry(entry: Mapping[str, object]) -> str:
    configuration_id = _required_text(entry, "model_configuration_id")
    for key in ("score_integrity", "route_identity", "cost_coverage", "completed_at"):
        _required_text(entry, key)

    model_configuration = entry.get("model_configuration")
    if not isinstance(model_configuration, Mapping):
        raise ValueError("reference snapshot model configuration is required")
    for key in (
        "provider_id",
        "raw_model_id",
        "canonical_model_id",
        "display_name",
        "reasoning_effort",
        "service_tier",
        "route_type",
    ):
        _required_text(model_configuration, key)

    if not isinstance(entry.get("advisor_eligible"), bool):
        raise ValueError("reference snapshot advisor_eligible is invalid")
    _bounded_number(entry.get("elapsed_ms"), 0, MAX_REFERENCE_ELAPSED_MS)

    question_scores = entry.get("question_scores")
    if not isinstance(question_scores, Mapping):
        raise ValueError("reference snapshot question scores are required")
    for score in question_scores.values():
        _bounded_number(score, 0, 20)

    usage = entry.get("usage")
    if not isinstance(usage, Mapping):
        raise ValueError("reference snapshot usage is required")
    for key in (
        "input_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "reasoning_tokens",
    ):
        if key in usage:
            _non_negative_integer(usage[key], f"reference snapshot usage {key}")

    estimated_cost = entry.get("estimated_api_cost_usd")
    if estimated_cost is not None:
        _bounded_number(estimated_cost, 0, float("inf"))
    attempt_count = _non_negative_integer(
        entry.get("attempt_count"),
        "reference snapshot attempt_count",
    )
    failure_count = _non_negative_integer(
        entry.get("failure_count"),
        "reference snapshot failure_count",
    )
    hard_failure_count = _non_negative_integer(
        entry.get("hard_failure_count"),
        "reference snapshot hard_failure_count",
    )
    if attempt_count == 0:
        raise ValueError("reference snapshot attempt_count is invalid")
    if hard_failure_count > failure_count or failure_count > attempt_count:
        raise ValueError("reference snapshot failure counts are inconsistent")
    return configuration_id


def reference_snapshot_hash(payload: Mapping[str, object]) -> str:
    canonical = dict(payload)
    canonical.pop("batch_sha256", None)
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def build_reference_snapshot_leaderboard_projection(
    snapshot: Mapping[str, object],
    *,
    question_semantics: Sequence[Mapping[str, object]],
    prior_snapshots: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    questions = _normalize_leaderboard_questions(question_semantics)
    question_ids = [str(question["id"]) for question in questions]
    snapshot_question_ids = snapshot.get("question_ids")
    if isinstance(snapshot_question_ids, list) and snapshot_question_ids != question_ids:
        raise ValueError("leaderboard question semantics do not match the snapshot")

    entries = _mapping_items(snapshot.get("entries"))
    ranked_entries = sorted(entries, key=_leaderboard_ranking_key)
    decision_tags_by_id = _leaderboard_decision_tags(entries)
    rows: list[dict[str, object]] = []
    for rank, entry in enumerate(ranked_entries, start=1):
        configuration_id = _required_text(entry, "model_configuration_id")
        raw_scores = entry.get("question_scores")
        if not isinstance(raw_scores, Mapping) or set(raw_scores) != set(question_ids):
            raise ValueError("leaderboard question scores do not match semantics")
        trend_points = _leaderboard_trend_points(
            snapshot,
            entry,
            prior_snapshots=prior_snapshots,
        )
        sample_count = len(trend_points)
        rows.append(
            {
                "model_configuration_id": configuration_id,
                "rank": rank,
                "target_labels": _leaderboard_target_labels(entry, entries),
                "decision_tags": decision_tags_by_id[configuration_id],
                "question_scores": [
                    {
                        "question_id": question_id,
                        "score": _bounded_number(raw_scores[question_id], 0, 20),
                    }
                    for question_id in question_ids
                ],
                "trend": {
                    "compatibility_key": _leaderboard_compatibility_key(
                        snapshot, entry
                    ),
                    "sample_count": sample_count,
                    "comparable": sample_count >= 2,
                    "stable_ranking_eligible": sample_count >= 3,
                    "points": trend_points,
                },
            }
        )
    return {
        "schema_version": REFERENCE_LEADERBOARD_SCHEMA_VERSION,
        "source": "publisher",
        "ranking_rule": REFERENCE_RANKING_RULE,
        "trend_rule": REFERENCE_TREND_RULE,
        "questions": questions,
        "rows": rows,
    }


def build_reference_snapshot_pairwise_comparisons(
    snapshot: Mapping[str, object],
) -> list[dict[str, object]]:
    """Project every ordered pair from one validated snapshot.

    The published snapshot is a complete comparison group but does not have a
    user-specific baseline. Keeping both directions lets the App compare any
    current configuration with any manually selected candidate while still
    using the same evidence and comparability semantics.
    """
    entries = _mapping_items(snapshot.get("entries"))
    comparisons: list[dict[str, object]] = []
    for baseline in entries:
        baseline_id = _required_text(baseline, "model_configuration_id")
        for candidate in entries:
            candidate_id = _required_text(candidate, "model_configuration_id")
            if candidate_id == baseline_id:
                continue

            comparison_status = _reference_pairwise_comparison_status(
                baseline,
                candidate,
            )
            is_comparable = comparison_status == "comparable"
            baseline_score = _optional_reference_number(
                baseline.get("score"),
                0,
                100,
            )
            candidate_score = _optional_reference_number(
                candidate.get("score"),
                0,
                100,
            )
            baseline_elapsed = _optional_reference_number(
                baseline.get("elapsed_ms"),
                0,
                MAX_REFERENCE_ELAPSED_MS,
            )
            candidate_elapsed = _optional_reference_number(
                candidate.get("elapsed_ms"),
                0,
                MAX_REFERENCE_ELAPSED_MS,
            )
            baseline_cost = _optional_reference_number(
                baseline.get("estimated_api_cost_usd"),
                0,
                float("inf"),
            )
            candidate_cost = _optional_reference_number(
                candidate.get("estimated_api_cost_usd"),
                0,
                float("inf"),
            )
            comparisons.append(
                {
                    "schema_version": 1,
                    "pair_key": f"{baseline_id}__to__{candidate_id}",
                    "baseline_candidate_id": baseline_id,
                    "baseline_label": _reference_snapshot_entry_label(baseline),
                    "candidate_id": candidate_id,
                    "candidate_label": _reference_snapshot_entry_label(candidate),
                    "comparison_status": comparison_status,
                    "is_comparable": is_comparable,
                    "baseline_quality_score": baseline_score,
                    "candidate_quality_score": candidate_score,
                    "quality_delta_points": (
                        _reference_rounded_metric(candidate_score - baseline_score)
                        if is_comparable
                        and baseline_score is not None
                        and candidate_score is not None
                        else None
                    ),
                    "baseline_elapsed_seconds": (
                        baseline_elapsed / 1000
                        if baseline_elapsed is not None
                        else None
                    ),
                    "candidate_elapsed_seconds": (
                        candidate_elapsed / 1000
                        if candidate_elapsed is not None
                        else None
                    ),
                    "time_delta_percent": (
                        _reference_percentage_improvement(
                            baseline_elapsed,
                            candidate_elapsed,
                        )
                        if is_comparable
                        else None
                    ),
                    "baseline_cost_usd": baseline_cost,
                    "candidate_cost_usd": candidate_cost,
                    "cost_delta_percent": (
                        _reference_percentage_improvement(
                            baseline_cost,
                            candidate_cost,
                        )
                        if is_comparable
                        and baseline.get("cost_coverage") == "complete"
                        and candidate.get("cost_coverage") == "complete"
                        else None
                    ),
                    "baseline_cost_coverage": baseline.get("cost_coverage"),
                    "candidate_cost_coverage": candidate.get("cost_coverage"),
                    "baseline_token_totals": _reference_snapshot_token_totals(
                        baseline
                    ),
                    "candidate_token_totals": _reference_snapshot_token_totals(
                        candidate
                    ),
                    "warning_question_ids": (
                        _reference_warning_question_ids(baseline, candidate)
                        if is_comparable
                        else []
                    ),
                }
            )
    return comparisons


def project_reference_snapshot_pairwise(
    feed: Mapping[str, object],
) -> dict[str, object]:
    """Attach latest runtime-only pairwise evidence without changing hashes."""
    projected = dict(feed)
    latest = feed.get("latest")
    if isinstance(latest, Mapping):
        latest_projection = dict(latest)
        latest_projection["pairwise_comparisons"] = (
            build_reference_snapshot_pairwise_comparisons(latest)
        )
        projected["latest"] = latest_projection
    return projected


def _reference_pairwise_comparison_status(
    baseline: Mapping[str, object],
    candidate: Mapping[str, object],
) -> str:
    if not bool(baseline.get("advisor_eligible")):
        return "baseline_not_comparable"
    if not bool(candidate.get("advisor_eligible")):
        return "candidate_not_comparable"
    if (
        _optional_reference_number(baseline.get("score"), 0, 100) is None
        or _optional_reference_number(candidate.get("score"), 0, 100) is None
    ):
        return "insufficient_evidence"
    return "comparable"


def _reference_snapshot_entry_label(entry: Mapping[str, object]) -> str:
    configuration = entry.get("model_configuration")
    if isinstance(configuration, Mapping):
        display_name = configuration.get("display_name")
        if isinstance(display_name, str) and display_name.strip():
            return display_name.strip()
    return _required_text(entry, "model_configuration_id")


def _reference_snapshot_token_totals(
    entry: Mapping[str, object],
) -> dict[str, int | None]:
    usage = entry.get("usage")
    if not isinstance(usage, Mapping):
        usage = {}
    return {
        key: (
            int(usage[key])
            if isinstance(usage.get(key), (int, float))
            and not isinstance(usage.get(key), bool)
            else None
        )
        for key in (
            "input_tokens",
            "cached_input_tokens",
            "cache_write_input_tokens",
            "output_tokens",
            "reasoning_tokens",
        )
    }


def _reference_warning_question_ids(
    baseline: Mapping[str, object],
    candidate: Mapping[str, object],
) -> list[str]:
    baseline_scores = {
        str(question_id).casefold(): _reference_question_score_percent(score)
        for question_id, score in dict(baseline.get("question_scores") or {}).items()
    }
    warnings: list[str] = []
    for question_id, score in dict(candidate.get("question_scores") or {}).items():
        question_name = str(question_id)
        candidate_percent = _reference_question_score_percent(score)
        baseline_percent = baseline_scores.get(question_name.casefold())
        if (
            candidate_percent is not None
            and baseline_percent is not None
            and candidate_percent - baseline_percent < -5
        ):
            warnings.append(question_name)
    return warnings


def _reference_question_score_percent(value: object) -> float | None:
    if value is None:
        return None
    return _bounded_number(value, 0, 20) * 5


def _optional_reference_number(
    value: object,
    minimum: float,
    maximum: float,
) -> float | None:
    if value is None:
        return None
    return _bounded_number(value, minimum, maximum)


def _reference_percentage_improvement(
    baseline: float | None,
    candidate: float | None,
) -> float | int | None:
    if baseline is None or candidate is None or baseline <= 0:
        return None
    return _reference_rounded_metric((baseline - candidate) * 100 / baseline)


def _reference_rounded_metric(value: float) -> float | int:
    rounded = round(value, 3)
    return int(rounded) if rounded.is_integer() else rounded


def _is_sha256(value: str) -> bool:
    return (
        value.startswith("sha256:")
        and len(value) == 71
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def reference_snapshot_to_advisor_source(
    snapshot: Mapping[str, object],
) -> dict[str, object] | None:
    validated = validate_reference_snapshot(snapshot)
    if not _is_official_reference_snapshot(validated):
        return None
    rows = [
        _entry_to_advisor_row(entry, validated)
        for entry in _mapping_items(validated.get("entries"))
        if bool(entry.get("advisor_eligible", False))
    ]
    return {
        "source": "official_snapshot",
        "snapshot_id": str(validated["batch_id"]),
        "pricing_snapshot_id": validated.get("pricing_snapshot_id"),
        "published_at": str(validated["published_at"]),
        "question_pack_version": str(validated["question_pack_version"]),
        "grader_version": str(validated["grader_version"]),
        "rows": rows,
    }


def _is_official_reference_snapshot(snapshot: Mapping[str, object]) -> bool:
    if snapshot.get("kind") != "first_party_snapshot":
        return False
    provenance = snapshot.get("provenance")
    return (
        isinstance(provenance, Mapping)
        and provenance.get("kind") == "first_party_snapshot"
        and provenance.get("public_official_snapshot") is True
    )


def _entry_to_advisor_row(
    entry: Mapping[str, object],
    snapshot: Mapping[str, object],
) -> dict[str, object]:
    raw_model_configuration = entry.get("model_configuration")
    model_configuration = (
        raw_model_configuration
        if isinstance(raw_model_configuration, Mapping)
        else {}
    )
    question_results = [
        {
            "question_id": question_id,
            "semantic_score": score,
            "semantic_total": 20,
        }
        for question_id, score in sorted(
            dict(entry.get("question_scores") or {}).items()
        )
    ]
    return {
        "model_configuration_id": str(entry["model_configuration_id"]),
        "source_model_configuration_id": str(entry["model_configuration_id"]),
        "provider_id": model_configuration.get("provider_id"),
        "canonical_model_id": model_configuration.get("canonical_model_id"),
        "reasoning_effort": model_configuration.get("reasoning_effort"),
        "service_tier": model_configuration.get("service_tier"),
        "route_type": model_configuration.get("route_type"),
        "route_identity": entry.get("route_identity"),
        "completed_at": str(
            entry.get("completed_at") or snapshot["published_at"]
        ),
        "complete": len(question_results) == 5,
        "hard_failure": int(entry.get("hard_failure_count") or 0) > 0,
        "question_pack_version": str(snapshot["question_pack_version"]),
        "grader_version": str(snapshot["grader_version"]),
        "route_fingerprint": entry.get("route_fingerprint"),
        "overall_score": entry.get("score"),
        "elapsed_seconds": float(entry.get("elapsed_ms") or 0) / 1000,
        "estimated_cost_usd": entry.get("estimated_api_cost_usd"),
        "cost_coverage": entry.get("cost_coverage"),
        "question_results": question_results,
    }


def _reference_snapshot_root() -> Path:
    configured_root = os.environ.get("MODELDIAL_BACKEND_ROOT", "").strip()
    if configured_root:
        external = (
            Path(configured_root).expanduser()
            / "scanner"
            / "reference_snapshots"
        )
        if external.is_dir():
            return external
    return Path(__file__).with_name("reference_snapshots")


def _read_json_object(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("reference snapshot file must contain an object")
    return payload


def _load_reference_snapshot_bundle(root: Path) -> dict[str, object]:
    try:
        bundle = _read_json_object(root / _HTTP_CACHE_BUNDLE_NAME)
        if bundle.get("schema_version") != _HTTP_CACHE_BUNDLE_SCHEMA_VERSION:
            raise ValueError("unsupported reference snapshot cache bundle")
        index = bundle.get("index")
        latest = bundle.get("latest")
        snapshots = bundle.get("snapshots")
        if not isinstance(index, Mapping) or not isinstance(latest, Mapping):
            raise ValueError("reference snapshot cache bundle payload is invalid")
        if not isinstance(snapshots, list) or not all(
            isinstance(snapshot, Mapping) for snapshot in snapshots
        ):
            raise ValueError("reference snapshot cache bundle snapshots are invalid")
        return _validated_snapshot_feed(
            index,
            latest,
            tuple(snapshots),
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return _empty_feed("missing")


def _validated_snapshot_feed(
    index: Mapping[str, object],
    latest: Mapping[str, object],
    snapshots: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    kind, summaries, latest_batch_id, _latest_path = (
        _validate_reference_snapshot_index(index)
    )
    by_batch_id: dict[str, Mapping[str, object]] = {}
    for snapshot in snapshots:
        validated = validate_reference_snapshot(snapshot)
        batch_id = _required_text(validated, "batch_id")
        if batch_id in by_batch_id:
            raise ValueError("duplicate reference snapshot cache batch")
        by_batch_id[batch_id] = validated
    if set(by_batch_id) != {
        _required_text(summary, "batch_id") for summary in summaries
    }:
        raise ValueError("reference snapshot cache batch set mismatch")
    ordered: list[dict[str, object]] = []
    for summary in summaries:
        snapshot = by_batch_id[_required_text(summary, "batch_id")]
        _validate_indexed_snapshot(kind, summary, snapshot)
        ordered.append(snapshot)
    latest_snapshot = by_batch_id.get(latest_batch_id)
    if latest_snapshot is None:
        raise ValueError("latest reference snapshot is missing from cache")
    validated_latest = validate_reference_snapshot(latest)
    _validate_indexed_snapshot(
        kind,
        next(
            summary
            for summary in summaries
            if summary["batch_id"] == latest_batch_id
        ),
        validated_latest,
    )
    if validated_latest != latest_snapshot:
        raise ValueError("latest reference snapshot does not match cache")
    return {
        "schema_version": REFERENCE_SNAPSHOT_SCHEMA_VERSION,
        "status": "loaded",
        "kind": kind,
        "latest": dict(latest_snapshot),
        "snapshots": ordered,
    }


def _read_legacy_reference_snapshot_cache_source(root: Path) -> str | None:
    try:
        metadata = _read_json_object(root / _HTTP_CACHE_METADATA_NAME)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        metadata = None
    if metadata is not None:
        source = metadata.get("index_url")
        if isinstance(source, str) and source:
            return source
    try:
        index = _read_json_object(root / "index.json")
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    source = index.get("cache_source_url")
    return source if isinstance(source, str) and source else None


def _load_reference_snapshot_cache_for_url(
    root: Path,
    index_url: str,
) -> dict[str, object]:
    bundle_path = root / _HTTP_CACHE_BUNDLE_NAME
    if bundle_path.exists() or bundle_path.is_symlink():
        try:
            bundle = _read_json_object(bundle_path)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return _empty_feed("invalid")
        if bundle.get("index_url") != index_url:
            return _empty_feed("missing")
        return _load_reference_snapshot_bundle(root)
    source = _read_legacy_reference_snapshot_cache_source(root)
    if source != index_url:
        return _empty_feed("missing")
    return load_reference_snapshot_feed(root)


def _download_reference_snapshot_feed(
    base_url: str,
    *,
    cached_feed: Mapping[str, object],
    index_etag: str | None,
    timeout_seconds: float,
) -> _DownloadedReferenceSnapshotFeed:
    normalized_url = _normalize_base_url(base_url)
    index_response = _read_http_json(
        _same_origin_url(normalized_url, "index.json"),
        timeout_seconds=timeout_seconds,
        max_bytes=MAX_INDEX_BYTES,
        if_none_match=index_etag,
        allow_not_modified=True,
    )
    if index_response.not_modified:
        if cached_feed.get("status") == "loaded":
            return _DownloadedReferenceSnapshotFeed(
                index=None,
                latest=None,
                snapshots=(),
                index_etag=index_response.etag or index_etag,
                not_modified=True,
            )
        index_response = _read_http_json(
            _same_origin_url(normalized_url, "index.json"),
            timeout_seconds=timeout_seconds,
            max_bytes=MAX_INDEX_BYTES,
        )
    index = index_response.payload
    if index is None:
        raise ReferenceSnapshotDownloadError("invalid_payload")
    try:
        kind, summaries, latest_batch_id, latest_path = (
            _validate_reference_snapshot_index(index)
        )
        _same_origin_url(normalized_url, latest_path)
        cached_snapshots = (
            {
                str(snapshot.get("batch_id")): snapshot
                for snapshot in _mapping_items(cached_feed.get("snapshots"))
            }
            if cached_feed.get("status") == "loaded"
            else {}
        )
        snapshots: list[dict[str, object]] = []
        for summary in summaries:
            relative_path = _required_text(summary, "path")
            batch_id = _required_text(summary, "batch_id")
            snapshot: dict[str, object] | None = None
            cached_snapshot = cached_snapshots.get(batch_id)
            if cached_snapshot is not None:
                try:
                    snapshot = validate_reference_snapshot(cached_snapshot)
                    _validate_indexed_snapshot(kind, summary, snapshot)
                except (TypeError, ValueError):
                    snapshot = None
            if snapshot is None:
                response = _read_http_json(
                    _same_origin_url(normalized_url, relative_path),
                    timeout_seconds=timeout_seconds,
                    max_bytes=MAX_SNAPSHOT_BYTES,
                )
                if response.payload is None:
                    raise ValueError("reference snapshot archive is empty")
                snapshot = validate_reference_snapshot(response.payload)
                _validate_indexed_snapshot(kind, summary, snapshot)
            snapshots.append(snapshot)
        latest = next(
            (
                snapshot
                for snapshot in snapshots
                if snapshot["batch_id"] == latest_batch_id
            ),
            None,
        )
        if latest is None:
            raise ValueError("latest reference snapshot is missing from archive")
    except (TypeError, ValueError) as error:
        raise ReferenceSnapshotDownloadError("invalid_payload") from error
    return _DownloadedReferenceSnapshotFeed(
        index=index,
        latest=latest,
        snapshots=tuple(snapshots),
        index_etag=index_response.etag,
    )


def _read_http_json(
    url: str,
    *,
    timeout_seconds: float,
    max_bytes: int,
    if_none_match: str | None = None,
    allow_not_modified: bool = False,
) -> _HttpJsonResponse:
    headers = {
        "Accept": "application/json",
        "User-Agent": "ModelDial/ReferenceSnapshotV1",
    }
    if if_none_match:
        headers["If-None-Match"] = if_none_match
    if allow_not_modified:
        headers["Cache-Control"] = "no-cache"
    request = Request(
        url,
        headers=headers,
    )
    try:
        with urlopen(request, timeout=max(0.1, timeout_seconds)) as response:
            if not _same_origin(url, response.geturl()):
                raise ReferenceSnapshotDownloadError("invalid_payload")
            response_etag = _valid_http_etag(response.headers.get("ETag"))
            if response.status == 304:
                if not allow_not_modified:
                    raise ReferenceSnapshotDownloadError("invalid_payload")
                return _HttpJsonResponse(
                    payload=None,
                    etag=response_etag,
                    not_modified=True,
                )
            body = response.read(max_bytes + 1)
    except ReferenceSnapshotDownloadError:
        raise
    except HTTPError as error:
        if error.code == 304 and allow_not_modified:
            if not _same_origin(url, error.geturl()):
                error.close()
                raise ReferenceSnapshotDownloadError("invalid_payload") from error
            response_headers = error.headers
            response = _HttpJsonResponse(
                payload=None,
                etag=_valid_http_etag(
                    response_headers.get("ETag")
                    if response_headers is not None
                    else None
                ),
                not_modified=True,
            )
            error.close()
            return response
        error.close()
        raise ReferenceSnapshotDownloadError("unavailable") from error
    except (URLError, OSError, TimeoutError) as error:
        raise ReferenceSnapshotDownloadError("unavailable") from error
    if len(body) > max_bytes:
        raise ReferenceSnapshotDownloadError("invalid_payload")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReferenceSnapshotDownloadError("invalid_payload") from error
    if not isinstance(payload, dict):
        raise ReferenceSnapshotDownloadError("invalid_payload")
    return _HttpJsonResponse(
        payload=payload,
        etag=response_etag,
    )


def _valid_http_etag(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    etag = value.strip()
    if not etag or len(etag) > 512 or "\r" in etag or "\n" in etag:
        return None
    return etag


def _read_reference_snapshot_index_etag(
    root: Path,
    index_url: str,
) -> str | None:
    try:
        bundle = _read_json_object(root / _HTTP_CACHE_BUNDLE_NAME)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        bundle = None
    if bundle is not None and bundle.get("index_url") == index_url:
        return _valid_http_etag(bundle.get("index_etag"))
    try:
        metadata = _read_json_object(root / _HTTP_CACHE_METADATA_NAME)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if (
        metadata.get("schema_version") != 1
        or metadata.get("index_url") != index_url
    ):
        return None
    return _valid_http_etag(metadata.get("index_etag"))


def _write_reference_snapshot_cache(
    root: Path,
    *,
    index: Mapping[str, object],
    latest: Mapping[str, object],
    snapshots: Sequence[Mapping[str, object]],
    index_url: str | None = None,
    index_etag: str | None = None,
) -> None:
    cache_index = _cache_index(index)
    bundle = {
        "schema_version": _HTTP_CACHE_BUNDLE_SCHEMA_VERSION,
        "index_url": index_url,
        "index_etag": _valid_http_etag(index_etag),
        "index": cache_index,
        "latest": dict(latest),
        "snapshots": [dict(snapshot) for snapshot in snapshots],
    }
    # Validate the complete candidate before replacing the last-good bundle.
    candidate = _validated_snapshot_feed(
        bundle["index"],
        bundle["latest"],
        bundle["snapshots"],
    )
    if candidate["status"] != "loaded":
        raise ReferenceSnapshotDownloadError("invalid_payload")
    _write_json_atomic(root / _HTTP_CACHE_BUNDLE_NAME, bundle)
    committed = _load_reference_snapshot_bundle(root)
    if committed["status"] != "loaded":
        raise ReferenceSnapshotDownloadError("invalid_payload")


def _cache_index(index: Mapping[str, object]) -> dict[str, object]:
    cache_index = dict(index)
    cache_index["latest_path"] = "latest.json"
    cache_index["snapshots"] = [
        {
            **dict(summary),
            "path": (
                "archive/"
                + hashlib.sha256(
                    _required_text(summary, "batch_id").encode("utf-8")
                ).hexdigest()
                + ".json"
            ),
        }
        for summary in _mapping_items(index.get("snapshots"))
    ]
    return cache_index


def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _normalize_base_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ReferenceSnapshotDownloadError("invalid_configuration")
    if parsed.scheme == "http" and not _is_loopback_hostname(parsed.hostname):
        raise ReferenceSnapshotDownloadError("https_required")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ReferenceSnapshotDownloadError("invalid_configuration")
    if parsed.path.rstrip("/").endswith("/index.json"):
        return urljoin(base_url, "./")
    return base_url.rstrip("/") + "/"


def _is_loopback_hostname(hostname: str) -> bool:
    normalized = hostname.rstrip(".").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _same_origin_url(base_url: str, relative_path: str) -> str:
    parsed_path = urlsplit(relative_path)
    if (
        parsed_path.scheme
        or parsed_path.netloc
        or parsed_path.query
        or parsed_path.fragment
    ):
        raise ReferenceSnapshotDownloadError("invalid_payload")
    parts = [part for part in parsed_path.path.split("/") if part]
    if ".." in parts:
        raise ReferenceSnapshotDownloadError("invalid_payload")
    resolved = urljoin(base_url, relative_path)
    if not _same_origin(base_url, resolved):
        raise ReferenceSnapshotDownloadError("invalid_payload")
    return resolved


def _same_origin(left: str, right: str) -> bool:
    left_url = urlsplit(left)
    right_url = urlsplit(right)
    return (
        left_url.scheme.lower(),
        left_url.hostname,
        left_url.port,
    ) == (
        right_url.scheme.lower(),
        right_url.hostname,
        right_url.port,
    )


def _with_delivery(
    feed: Mapping[str, object],
    source: str,
    refresh_status: str,
    *,
    error_code: str | None = None,
) -> dict[str, object]:
    result = dict(feed)
    result["delivery"] = {
        "source": source,
        "refresh_status": refresh_status,
        "error_code": error_code,
    }
    return result


def _safe_child(root: Path, relative_path: str) -> Path:
    resolved_root = root.resolve()
    resolved_path = (root / relative_path).resolve()
    if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
        raise ValueError("reference snapshot path escapes its root")
    return resolved_path


def _validate_index_summary(
    summary: Mapping[str, object],
    snapshot: Mapping[str, object],
) -> None:
    fields = (
        "batch_id",
        "published_at",
        "question_pack_version",
        "score_baseline_id",
        "entry_count",
        "batch_sha256",
    )
    if any(summary.get(field) != snapshot.get(field) for field in fields):
        raise ValueError("reference snapshot index summary mismatch")


def _validate_indexed_snapshot(
    kind: str,
    summary: Mapping[str, object],
    snapshot: Mapping[str, object],
) -> None:
    if snapshot.get("kind") != kind:
        raise ValueError("reference snapshot kind does not match index")
    _validate_index_summary(summary, snapshot)


def _validate_reference_snapshot_index(
    index: Mapping[str, object],
) -> tuple[str, list[Mapping[str, object]], str, str]:
    if index.get("schema_version") != REFERENCE_SNAPSHOT_SCHEMA_VERSION:
        raise ValueError("unsupported reference snapshot index schema")
    kind = _required_text(index, "kind")
    _required_text(index, "generated_at")
    latest_batch_id = _required_text(index, "latest_batch_id")
    latest_path = _required_text(index, "latest_path")
    summaries = _mapping_items(index.get("snapshots"))
    if not summaries:
        raise ValueError("reference snapshot index is empty")

    batch_ids: list[str] = []
    paths: list[str] = []
    for summary in summaries:
        batch_ids.append(_required_text(summary, "batch_id"))
        _required_text(summary, "published_at")
        _required_text(summary, "question_pack_version")
        _required_text(summary, "score_baseline_id")
        path = _required_text(summary, "path")
        paths.append(path)
        entry_count = summary.get("entry_count")
        if (
            isinstance(entry_count, bool)
            or not isinstance(entry_count, int)
            or entry_count < 0
        ):
            raise ValueError("reference snapshot index entry count is invalid")
        if not _is_sha256(_required_text(summary, "batch_sha256")):
            raise ValueError("reference snapshot index batch hash is invalid")

    if len(batch_ids) != len(set(batch_ids)):
        raise ValueError("reference snapshot index contains duplicate batch ids")
    if len(paths) != len(set(paths)):
        raise ValueError("reference snapshot index contains duplicate paths")
    if latest_batch_id not in batch_ids:
        raise ValueError("latest reference snapshot is missing from index")
    return kind, summaries, latest_batch_id, latest_path


def _validate_optional_latest_copy(
    root: Path,
    *,
    kind: str,
    summaries: Sequence[Mapping[str, object]],
    latest: Mapping[str, object],
) -> None:
    latest_copy_path = _safe_child(root, "latest.json")
    if not latest_copy_path.is_file():
        return
    latest_copy = validate_reference_snapshot(
        _read_json_object(latest_copy_path)
    )
    if latest_copy.get("kind") != kind:
        raise ValueError("latest reference snapshot kind does not match index")
    matching_summary = next(
        (
            summary
            for summary in summaries
            if summary["batch_id"] == latest_copy["batch_id"]
        ),
        None,
    )
    if matching_summary is None:
        raise ValueError("latest reference snapshot copy is not indexed")
    _validate_index_summary(matching_summary, latest_copy)
    if latest_copy != latest:
        raise ValueError("latest reference snapshot copy does not match archive")


def _normalize_leaderboard_questions(
    value: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    questions = _mapping_items(value)
    normalized: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    required_fields = (
        "id",
        "short_label",
        "title",
        "capability_id",
        "capability_label",
        "detail_label",
    )
    for ordinal, question in enumerate(questions, start=1):
        question_id = _required_text(question, "id")
        if question_id in seen_ids:
            raise ValueError("leaderboard question semantics are duplicated")
        seen_ids.add(question_id)
        if question.get("ordinal") != ordinal:
            raise ValueError("leaderboard question semantics order is invalid")
        normalized.append(
            {
                field: _required_text(question, field)
                for field in required_fields
            }
            | {"ordinal": ordinal}
        )
    if not normalized:
        raise ValueError("leaderboard question semantics are required")
    return normalized


def _leaderboard_ranking_key(entry: Mapping[str, object]) -> tuple[object, ...]:
    score = _bounded_number(entry.get("score"), 0, 100)
    hard_failures = _non_negative_integer(
        entry.get("hard_failure_count"),
        "leaderboard hard failure count",
    )
    elapsed_ms = _bounded_number(
        entry.get("elapsed_ms"), 0, MAX_REFERENCE_ELAPSED_MS
    )
    raw_cost = entry.get("estimated_api_cost_usd")
    cost = (
        float("inf")
        if raw_cost is None
        else _bounded_number(raw_cost, 0, float("inf"))
    )
    return (
        -score,
        hard_failures,
        elapsed_ms,
        cost,
        _required_text(entry, "model_configuration_id"),
    )


def _leaderboard_target_labels(
    entry: Mapping[str, object],
    entries: Sequence[Mapping[str, object]],
) -> list[dict[str, str]]:
    eligible = [
        candidate
        for candidate in entries
        if _non_negative_integer(
            candidate.get("hard_failure_count"),
            "leaderboard hard failure count",
        )
        == 0
    ]
    if not eligible or entry not in eligible:
        return []
    highest_score = max(
        _bounded_number(candidate.get("score"), 0, 100)
        for candidate in eligible
    )
    fastest = min(
        _bounded_number(candidate.get("elapsed_ms"), 0, MAX_REFERENCE_ELAPSED_MS)
        for candidate in eligible
    )
    priced = [
        _bounded_number(candidate.get("estimated_api_cost_usd"), 0, float("inf"))
        for candidate in eligible
        if candidate.get("estimated_api_cost_usd") is not None
    ]
    lowest_cost = min(priced) if priced else None
    targets = (
        _bounded_number(entry.get("score"), 0, 100) == highest_score,
        _bounded_number(entry.get("elapsed_ms"), 0, MAX_REFERENCE_ELAPSED_MS)
        == fastest,
        lowest_cost is not None
        and entry.get("estimated_api_cost_usd") is not None
        and _bounded_number(
            entry.get("estimated_api_cost_usd"), 0, float("inf")
        )
        == lowest_cost,
    )
    return [
        {"id": label_id, "label": label}
        for (label_id, label), selected in zip(_TARGET_LABELS, targets)
        if selected
    ]


def _leaderboard_decision_tags(
    entries: Sequence[Mapping[str, object]],
) -> dict[str, list[dict[str, str]]]:
    decision_entries: list[dict[str, object]] = []
    for entry in entries:
        decision_entries.append(
            {
                "candidate_id": _required_text(entry, "model_configuration_id"),
                "overall_score": _bounded_number(entry.get("score"), 0, 100),
                "elapsed_seconds": _bounded_number(
                    entry.get("elapsed_ms"), 0, MAX_REFERENCE_ELAPSED_MS
                )
                / 1000,
                "estimated_cost_usd": entry.get("estimated_api_cost_usd"),
                "cost_coverage": str(entry.get("cost_coverage") or "unknown"),
                "is_current_run_eligible": _non_negative_integer(
                    entry.get("hard_failure_count"),
                    "leaderboard hard failure count",
                )
                == 0,
            }
        )
    ranked_eligible = [
        entry
        for entry in sorted(entries, key=_leaderboard_ranking_key)
        if _non_negative_integer(
            entry.get("hard_failure_count"),
            "leaderboard hard failure count",
        )
        == 0
    ]
    best_candidate_id = (
        _required_text(ranked_eligible[0], "model_configuration_id")
        if ranked_eligible
        else ""
    )
    assign_leaderboard_decision_tags(decision_entries, best_candidate_id)
    return {
        str(entry["candidate_id"]): [
            {"kind": str(tag.get("kind") or "")}
            for tag in _mapping_items(entry.get("decision_tags"))
        ]
        for entry in decision_entries
    }


def _leaderboard_compatibility_key(
    snapshot: Mapping[str, object],
    entry: Mapping[str, object],
) -> str:
    return _leaderboard_compatibility_hash(
        snapshot,
        entry,
        include_retry_policy=False,
    )


def _legacy_leaderboard_compatibility_key(
    snapshot: Mapping[str, object],
    entry: Mapping[str, object],
) -> str:
    return _leaderboard_compatibility_hash(
        snapshot,
        entry,
        include_retry_policy=True,
    )


def _leaderboard_compatibility_hash(
    snapshot: Mapping[str, object],
    entry: Mapping[str, object],
    *,
    include_retry_policy: bool,
) -> str:
    model = entry.get("model_configuration")
    if not isinstance(model, Mapping):
        raise ValueError("leaderboard model configuration is required")
    identity = {
        "schema_version": 1,
        "kind": _required_text(snapshot, "kind"),
        "model_configuration_id": _required_text(
            entry, "model_configuration_id"
        ),
        "model_configuration": {
            field: _required_text(model, field)
            for field in (
                "provider_id",
                "raw_model_id",
                "canonical_model_id",
                "reasoning_effort",
                "service_tier",
                "route_type",
            )
        },
        "route_fingerprint": entry.get("route_fingerprint"),
        "question_pack_id": _required_text(snapshot, "question_pack_id"),
        "question_pack_version": _required_text(
            snapshot, "question_pack_version"
        ),
        "grader_version": _required_text(snapshot, "grader_version"),
        "evaluation_profile": _required_text(snapshot, "evaluation_profile"),
        "score_baseline_id": _required_text(snapshot, "score_baseline_id"),
    }
    if include_retry_policy:
        identity["retry_policy"] = snapshot.get("retry_policy")
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _leaderboard_trend_points(
    snapshot: Mapping[str, object],
    entry: Mapping[str, object],
    *,
    prior_snapshots: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    current_batch_id = _required_text(snapshot, "batch_id")
    current_published_at = _required_text(snapshot, "published_at")
    configuration_id = _required_text(entry, "model_configuration_id")
    compatibility_key = _leaderboard_compatibility_key(snapshot, entry)
    points_by_batch: dict[str, dict[str, object]] = {}
    for candidate_snapshot in [*prior_snapshots, snapshot]:
        batch_id = _required_text(candidate_snapshot, "batch_id")
        published_at = _required_text(candidate_snapshot, "published_at")
        if batch_id != current_batch_id and published_at >= current_published_at:
            continue
        candidate_entry = next(
            (
                candidate
                for candidate in _mapping_items(candidate_snapshot.get("entries"))
                if candidate.get("model_configuration_id") == configuration_id
            ),
            None,
        )
        if candidate_entry is None:
            continue
        if (
            _leaderboard_compatibility_key(candidate_snapshot, candidate_entry)
            != compatibility_key
        ):
            continue
        points_by_batch[batch_id] = {
            "batch_id": batch_id,
            "published_at": published_at,
            "score": _bounded_number(candidate_entry.get("score"), 0, 100),
            "elapsed_ms": _bounded_number(
                candidate_entry.get("elapsed_ms"), 0, MAX_REFERENCE_ELAPSED_MS
            ),
        }
    return sorted(
        points_by_batch.values(),
        key=lambda point: (str(point["published_at"]), str(point["batch_id"])),
    )[-_REFERENCE_TREND_WINDOW:]


def _validate_reference_snapshot_leaderboard_projection(
    snapshot: Mapping[str, object],
    *,
    entries: Sequence[Mapping[str, object]],
    question_ids: list[object] | None,
) -> None:
    projection = snapshot.get("leaderboard_projection")
    if not isinstance(projection, Mapping):
        raise ValueError("leaderboard projection must contain an object")
    if (
        projection.get("schema_version") != REFERENCE_LEADERBOARD_SCHEMA_VERSION
        or projection.get("source") != "publisher"
        or projection.get("ranking_rule") != REFERENCE_RANKING_RULE
        or projection.get("trend_rule") != REFERENCE_TREND_RULE
    ):
        raise ValueError("leaderboard projection contract is invalid")
    questions = _normalize_leaderboard_questions(
        _mapping_items(projection.get("questions"))
    )
    normalized_question_ids = [str(question["id"]) for question in questions]
    if question_ids is not None and normalized_question_ids != question_ids:
        raise ValueError("leaderboard projection question manifest mismatch")

    rows = _mapping_items(projection.get("rows"))
    ranked_entries = sorted(entries, key=_leaderboard_ranking_key)
    decision_tags_by_id = _leaderboard_decision_tags(entries)
    if len(rows) != len(ranked_entries):
        raise ValueError("leaderboard projection row count mismatch")
    for rank, (row, entry) in enumerate(zip(rows, ranked_entries), start=1):
        configuration_id = _required_text(entry, "model_configuration_id")
        if (
            row.get("rank") != rank
            or row.get("model_configuration_id") != configuration_id
        ):
            raise ValueError("leaderboard projection rank is not canonical")
        labels = _mapping_items(row.get("target_labels"))
        if labels != _leaderboard_target_labels(entry, entries):
            raise ValueError("leaderboard projection target labels are not canonical")
        decision_tags = _mapping_items(row.get("decision_tags"))
        if decision_tags != decision_tags_by_id[configuration_id]:
            raise ValueError("leaderboard projection decision tags are not canonical")
        raw_scores = entry.get("question_scores")
        if not isinstance(raw_scores, Mapping):
            raise ValueError("leaderboard projection question scores are invalid")
        projected_scores = _mapping_items(row.get("question_scores"))
        if len(projected_scores) != len(normalized_question_ids):
            raise ValueError("leaderboard projection question scores are incomplete")
        for question_id, projected_score in zip(
            normalized_question_ids, projected_scores
        ):
            if projected_score.get("question_id") != question_id or abs(
                _bounded_number(projected_score.get("score"), 0, 20)
                - _bounded_number(raw_scores.get(question_id), 0, 20)
            ) > 0.001:
                raise ValueError(
                    "leaderboard projection question scores are not canonical"
                )
        _validate_leaderboard_trend(snapshot, entry, row.get("trend"))


def _validate_leaderboard_trend(
    snapshot: Mapping[str, object],
    entry: Mapping[str, object],
    value: object,
) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("leaderboard projection trend is required")
    compatibility_key = value.get("compatibility_key")
    if compatibility_key not in {
        _leaderboard_compatibility_key(snapshot, entry),
        _legacy_leaderboard_compatibility_key(snapshot, entry),
    }:
        raise ValueError("leaderboard projection trend compatibility is invalid")
    points = _mapping_items(value.get("points"))
    if not points or len(points) > _REFERENCE_TREND_WINDOW:
        raise ValueError("leaderboard projection trend window is invalid")
    if value.get("sample_count") != len(points):
        raise ValueError("leaderboard projection trend sample count is invalid")
    if value.get("comparable") is not (len(points) >= 2):
        raise ValueError("leaderboard projection trend comparability is invalid")
    if value.get("stable_ranking_eligible") is not (len(points) >= 3):
        raise ValueError("leaderboard projection trend stability is invalid")

    identities: list[tuple[str, str]] = []
    for point in points:
        identity = (
            _required_text(point, "published_at"),
            _required_text(point, "batch_id"),
        )
        if identity in identities:
            raise ValueError("leaderboard projection trend points are duplicated")
        identities.append(identity)
        _bounded_number(point.get("score"), 0, 100)
        _bounded_number(point.get("elapsed_ms"), 0, MAX_REFERENCE_ELAPSED_MS)
    if identities != sorted(identities):
        raise ValueError("leaderboard projection trend order is invalid")
    current = points[-1]
    if (
        current.get("batch_id") != snapshot.get("batch_id")
        or current.get("published_at") != snapshot.get("published_at")
        or abs(
            _bounded_number(current.get("score"), 0, 100)
            - _bounded_number(entry.get("score"), 0, 100)
        )
        > 0.001
        or abs(
            _bounded_number(current.get("elapsed_ms"), 0, MAX_REFERENCE_ELAPSED_MS)
            - _bounded_number(entry.get("elapsed_ms"), 0, MAX_REFERENCE_ELAPSED_MS)
        )
        > 0.001
    ):
        raise ValueError("leaderboard projection trend does not end at current row")


def _non_negative_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} is invalid")
    return value


def _empty_feed(status: str) -> dict[str, object]:
    return {
        "schema_version": REFERENCE_SNAPSHOT_SCHEMA_VERSION,
        "status": status,
        "kind": None,
        "latest": None,
        "snapshots": [],
    }


def _required_text(payload: Mapping[str, object], key: str) -> str:
    raw_value = payload.get(key)
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise ValueError(f"reference snapshot {key} is required")
    return raw_value.strip()


def _mapping_items(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("reference snapshot collection must contain an array")
    items: list[Mapping[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("reference snapshot collection must contain objects")
        items.append(item)
    return items


def _bounded_number(value: object, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise ValueError("reference snapshot numeric field is invalid")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("reference snapshot numeric field is invalid") from error
    if not math.isfinite(parsed) or parsed < minimum or parsed > maximum:
        raise ValueError("reference snapshot numeric field is out of range")
    return parsed
