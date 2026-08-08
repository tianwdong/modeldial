from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Mapping, Sequence


AUTO_SOURCE = "auto"
LOCAL_SOURCE = "local_evaluation"
OFFICIAL_SOURCE = "official_snapshot"
SOURCE_MODES = {AUTO_SOURCE, LOCAL_SOURCE, OFFICIAL_SOURCE}

LOCAL_FRESHNESS = timedelta(hours=72)
OFFICIAL_FRESHNESS = timedelta(hours=24)


def build_advisor_evidence_context(
    *,
    source_mode: str,
    current_configuration_id: str | None,
    current_status_hint: str | None = None,
    configurations: Sequence[Mapping[str, object]],
    local_evaluation: Mapping[str, object] | None,
    official_snapshot: Mapping[str, object] | None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Resolve one evidence source and classify recommendation candidates.

    This is the V2 boundary between model ingress/evaluation data and the later
    recommendation policy. It deliberately does not score or rank candidates.
    """
    if source_mode not in SOURCE_MODES:
        raise ValueError(f"unsupported source_mode: {source_mode}")
    if current_status_hint not in {None, "no_usage", "unmapped"}:
        raise ValueError(f"unsupported current_status_hint: {current_status_hint}")

    observed_at = _as_utc(now or datetime.now(timezone.utc))
    configuration_by_id = _configuration_index(configurations)
    sources = {
        LOCAL_SOURCE: _validated_source(local_evaluation, LOCAL_SOURCE),
        OFFICIAL_SOURCE: _validated_source(official_snapshot, OFFICIAL_SOURCE),
    }

    if not current_configuration_id:
        current_status = current_status_hint or "no_usage"
        return _empty_context(
            source_mode=source_mode,
            current_status=current_status,
            source_reason=(
                "current_unmapped" if current_status == "unmapped" else "no_usage"
            ),
        )

    current = configuration_by_id.get(current_configuration_id)
    if current is None or not bool(current.get("identity_resolved", True)):
        return _empty_context(
            source_mode=source_mode,
            current_status="unmapped",
            source_reason="current_unmapped",
            current_configuration_id=current_configuration_id,
        )

    resolved_source, source_reason = _resolve_source(
        source_mode=source_mode,
        current_configuration_id=current_configuration_id,
        current_configuration=current,
        configurations=configuration_by_id,
        sources=sources,
        now=observed_at,
    )
    evidence_source = sources.get(resolved_source) if resolved_source else None
    candidate_decisions = _candidate_decisions(
        configurations=configuration_by_id,
        current_configuration_id=current_configuration_id,
        source=evidence_source,
        now=observed_at,
    )

    current_status = "needs_test"
    if evidence_source is not None:
        current_reasons = _result_reasons(
            configuration=current,
            row=_row_index(evidence_source).get(current_configuration_id),
            source=evidence_source,
            now=observed_at,
        )
        if not current_reasons:
            current_status = "ready"
        elif any(reason in {"source_stale", "stale_result"} for reason in current_reasons):
            current_status = "stale"

    eligible_ids = [
        str(item["model_configuration_id"])
        for item in candidate_decisions
        if item["status"] == "eligible"
    ]
    testable_ids = [
        str(item["model_configuration_id"])
        for item in candidate_decisions
        if item["status"] == "testable"
    ]
    resolved_result_rows = [
        dict(row)
        for row in evidence_source.get("rows", [])
        if isinstance(row, Mapping)
    ] if evidence_source is not None else []
    _assign_display_ranks(
        resolved_result_rows,
        visible_configuration_ids=(
            [current_configuration_id, *eligible_ids]
            if current_status == "ready"
            else []
        ),
    )
    return {
        "schema_version": 2,
        "source_mode": source_mode,
        "resolved_data_source": resolved_source,
        "source_reason": source_reason,
        "source_snapshot_id": (
            str(evidence_source.get("snapshot_id") or "") or None
            if evidence_source is not None
            else None
        ),
        "pricing_snapshot_id": (
            str(evidence_source.get("pricing_snapshot_id") or "") or None
            if evidence_source is not None
            else None
        ),
        "current_model_configuration_id": current_configuration_id,
        "current_status": current_status,
        "eligible_candidate_ids": eligible_ids,
        "testable_candidate_ids": testable_ids,
        "candidate_decisions": candidate_decisions,
        "resolved_result_rows": resolved_result_rows,
    }


def _assign_display_ranks(
    rows: list[dict[str, object]],
    *,
    visible_configuration_ids: Sequence[str],
) -> None:
    visible_ids = set(visible_configuration_ids)
    ranked_rows: list[tuple[float, str, dict[str, object]]] = []
    for row in rows:
        row["display_rank"] = None
        configuration_id = _record_id(row)
        score = row.get("overall_score")
        if (
            configuration_id in visible_ids
            and isinstance(score, (int, float))
            and not isinstance(score, bool)
        ):
            ranked_rows.append((float(score), configuration_id, row))

    ranked_rows.sort(key=lambda item: (-item[0], item[1]))
    previous_score: float | None = None
    current_rank = 0
    for index, (score, _, row) in enumerate(ranked_rows, start=1):
        if previous_score is None or score != previous_score:
            current_rank = index
            previous_score = score
        row["display_rank"] = current_rank


def _resolve_source(
    *,
    source_mode: str,
    current_configuration_id: str,
    current_configuration: Mapping[str, object],
    configurations: Mapping[str, Mapping[str, object]],
    sources: Mapping[str, Mapping[str, object] | None],
    now: datetime,
) -> tuple[str | None, str]:
    if source_mode != AUTO_SOURCE:
        if sources[source_mode] is None:
            return None, "source_missing"
        return source_mode, f"{source_mode}_selected"

    local_source = sources[LOCAL_SOURCE]
    if local_source is not None:
        local_current_reasons = _result_reasons(
            configuration=current_configuration,
            row=_row_index(local_source).get(current_configuration_id),
            source=local_source,
            now=now,
        )
        if not local_current_reasons:
            return LOCAL_SOURCE, "local_exact_match"

    official_source = sources[OFFICIAL_SOURCE]
    if official_source is not None:
        official_current_reasons = _result_reasons(
            configuration=current_configuration,
            row=_row_index(official_source).get(current_configuration_id),
            source=official_source,
            now=now,
        )
        decisions = _candidate_decisions(
            configurations=configurations,
            current_configuration_id=current_configuration_id,
            source=official_source,
            now=now,
        )
        if not official_current_reasons and any(
            item["status"] == "eligible" for item in decisions
        ):
            return OFFICIAL_SOURCE, "official_actionable_fallback"

    return None, "no_actionable_source"


def _candidate_decisions(
    *,
    configurations: Mapping[str, Mapping[str, object]],
    current_configuration_id: str,
    source: Mapping[str, object] | None,
    now: datetime,
) -> list[dict[str, object]]:
    rows = _row_index(source) if source is not None else {}
    decisions: list[dict[str, object]] = []
    for configuration_id in sorted(configurations):
        if configuration_id == current_configuration_id:
            continue
        configuration = configurations[configuration_id]
        configuration_reasons = _configuration_reasons(configuration)
        if configuration_reasons:
            status = "ineligible"
            reasons = configuration_reasons
        else:
            reasons = _result_reasons(
                configuration=configuration,
                row=rows.get(configuration_id),
                source=source,
                now=now,
            )
            status = "eligible" if not reasons else "testable"
        decisions.append(
            {
                "model_configuration_id": configuration_id,
                "status": status,
                "reasons": reasons,
            }
        )
    return decisions


def _configuration_reasons(configuration: Mapping[str, object]) -> list[str]:
    if not bool(configuration.get("enabled", True)):
        return ["disabled"]
    if not bool(configuration.get("identity_resolved", True)):
        return ["identity_unresolved"]
    if not bool(configuration.get("connection_ready", False)):
        return ["connection_not_ready"]
    return []


def _result_reasons(
    *,
    configuration: Mapping[str, object],
    row: Mapping[str, object] | None,
    source: Mapping[str, object] | None,
    now: datetime,
) -> list[str]:
    if source is None or row is None:
        return ["missing_result"]

    reasons: list[str] = []
    freshness = _source_freshness(source)
    if not _timestamp_is_fresh(source.get("published_at"), freshness, now):
        reasons.append("source_stale")
    completed_at = row.get("completed_at") or row.get("latest_valid_at") or row.get("valid_completed_at")
    if not _timestamp_is_fresh(completed_at, freshness, now):
        reasons.append("stale_result")

    source_pack = str(source.get("question_pack_version") or "")
    row_pack = str(row.get("question_pack_version") or "")
    if not source_pack or row_pack != source_pack:
        reasons.append("question_pack_mismatch")
    source_grader = str(source.get("grader_version") or "")
    row_grader = str(row.get("grader_version") or "")
    if not source_grader or row_grader != source_grader:
        reasons.append("grader_version_mismatch")
    if not _is_complete(row):
        reasons.append("incomplete_result")
    if bool(row.get("hard_failure", False)):
        reasons.append("hard_failure")

    expected_route = str(configuration.get("route_fingerprint") or "")
    actual_route = str(row.get("route_fingerprint") or "")
    if expected_route and actual_route != expected_route:
        reasons.append("route_mismatch")
    return reasons


def _is_complete(row: Mapping[str, object]) -> bool:
    if "complete" in row:
        return bool(row["complete"])
    try:
        completed = int(row.get("question_completed") or 0)
        total = int(row.get("question_count") or 0)
    except (TypeError, ValueError):
        return False
    return total > 0 and completed == total


def _source_freshness(source: Mapping[str, object]) -> timedelta:
    return LOCAL_FRESHNESS if source.get("source") == LOCAL_SOURCE else OFFICIAL_FRESHNESS


def _timestamp_is_fresh(value: object, freshness: timedelta, now: datetime) -> bool:
    timestamp = _parse_timestamp(value)
    if timestamp is None:
        return False
    age = now - timestamp
    return timedelta(0) <= age <= freshness


def _parse_timestamp(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(parsed)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _configuration_index(
    configurations: Sequence[Mapping[str, object]],
) -> dict[str, Mapping[str, object]]:
    indexed: dict[str, Mapping[str, object]] = {}
    for configuration in configurations:
        configuration_id = _record_id(configuration)
        if not configuration_id:
            raise ValueError("configuration is missing model_configuration_id")
        if configuration_id in indexed:
            raise ValueError(f"duplicate model configuration: {configuration_id}")
        indexed[configuration_id] = configuration
    return indexed


def _row_index(source: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    indexed: dict[str, Mapping[str, object]] = {}
    rows = source.get("rows") or []
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return indexed
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        configuration_id = _record_id(row)
        if configuration_id:
            indexed[configuration_id] = row
    return indexed


def _record_id(record: Mapping[str, object]) -> str:
    return str(record.get("model_configuration_id") or record.get("candidate_id") or "")


def _validated_source(
    source: Mapping[str, object] | None,
    expected_source: str,
) -> Mapping[str, object] | None:
    if source is None:
        return None
    actual_source = str(source.get("source") or expected_source)
    if actual_source != expected_source:
        raise ValueError(
            f"source envelope mismatch: expected {expected_source}, got {actual_source}"
        )
    return source


def _empty_context(
    *,
    source_mode: str,
    current_status: str,
    source_reason: str,
    current_configuration_id: str | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 2,
        "source_mode": source_mode,
        "resolved_data_source": None,
        "source_reason": source_reason,
        "source_snapshot_id": None,
        "pricing_snapshot_id": None,
        "current_model_configuration_id": current_configuration_id,
        "current_status": current_status,
        "eligible_candidate_ids": [],
        "testable_candidate_ids": [],
        "candidate_decisions": [],
        "resolved_result_rows": [],
    }
