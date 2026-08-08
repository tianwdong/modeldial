from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping, Sequence

from .advisor_v2 import AUTO_SOURCE, SOURCE_MODES, build_advisor_evidence_context
from .costing import current_pricing_snapshot_id
from .route_identity import build_route_fingerprint


def build_advisor_v2_evidence(
    state: Mapping[str, object],
    *,
    source_mode: str | None = None,
    official_snapshot: Mapping[str, object] | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    return build_advisor_v2_evidence_bundle(
        state,
        source_mode=source_mode,
        official_snapshot=official_snapshot,
        now=now,
    )["primary_evidence"]


def build_advisor_v2_evidence_bundle(
    state: Mapping[str, object],
    *,
    source_mode: str | None = None,
    official_snapshot: Mapping[str, object] | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    observed_at = _as_utc(now or datetime.now(timezone.utc))
    config = _mapping(state.get("config"))
    ingress = _mapping(config.get("model_ingress"))
    recommendation = _mapping(config.get("recommendation"))
    configurations = _configurations(ingress)
    aligned_official_snapshot = _align_official_snapshot(
        official_snapshot,
        configurations,
    )
    current_configuration_id = _optional_text(
        recommendation.get("effective_current_candidate_id")
    )
    detection_status = str(
        recommendation.get("current_model_detection_status") or "unavailable"
    )
    local_evaluation = _local_evaluation_source(
        state,
        configurations=configurations,
        now=observed_at,
    )
    primary_source_mode = _source_mode_for_configuration(
        recommendation,
        current_configuration_id,
        override=source_mode,
    )
    primary_evidence = build_advisor_evidence_context(
        source_mode=primary_source_mode,
        current_configuration_id=current_configuration_id,
        current_status_hint=_current_status_hint(
            current_configuration_id,
            detection_status,
        ),
        configurations=configurations,
        local_evaluation=local_evaluation,
        official_snapshot=aligned_official_snapshot,
        now=observed_at,
    )
    activity, unmapped_count = _active_configuration_activity(recommendation)
    contexts = [
        build_advisor_evidence_context(
            source_mode=_source_mode_for_configuration(
                recommendation,
                str(item["model_configuration_id"]),
                override=source_mode,
            ),
            current_configuration_id=str(item["model_configuration_id"]),
            current_status_hint=None,
            configurations=configurations,
            local_evaluation=local_evaluation,
            official_snapshot=aligned_official_snapshot,
            now=observed_at,
        )
        for item in activity
    ]
    if not contexts and current_configuration_id:
        contexts = [primary_evidence]
    return {
        "primary_evidence": primary_evidence,
        "contexts": contexts,
        "activity": activity,
        "unmapped_active_session_count": unmapped_count,
    }


def _source_mode_for_configuration(
    recommendation: Mapping[str, object],
    configuration_id: str | None,
    *,
    override: str | None,
) -> str:
    if override is not None:
        if override not in SOURCE_MODES:
            raise ValueError(f"unsupported source_mode: {override}")
        return override
    source_modes = _mapping(recommendation.get("source_mode_by_configuration_id"))
    configured = str(source_modes.get(configuration_id) or "")
    return configured if configured in SOURCE_MODES else AUTO_SOURCE


def _active_configuration_activity(
    recommendation: Mapping[str, object],
) -> tuple[list[dict[str, object]], int]:
    grouped: dict[str, dict[str, object]] = {}
    unmapped_count = 0
    for session in _mapping_items(recommendation.get("active_configuration_sessions")):
        configuration_id = _optional_text(session.get("candidate_id"))
        if session.get("mapping_status") != "matched" or not configuration_id:
            unmapped_count += 1
            continue
        item = grouped.setdefault(
            configuration_id,
            {
                "model_configuration_id": configuration_id,
                "active_session_count": 0,
                "last_active_at": None,
                "is_currently_producing": False,
            },
        )
        item["active_session_count"] = int(item["active_session_count"]) + 1
        item["is_currently_producing"] = bool(
            item["is_currently_producing"]
        ) or bool(session.get("is_currently_producing", False))
        last_active_at = _optional_text(session.get("last_active_at"))
        if last_active_at and (
            item["last_active_at"] is None
            or _timestamp_sort_value(last_active_at)
            > _timestamp_sort_value(str(item["last_active_at"]))
        ):
            item["last_active_at"] = last_active_at
    return list(grouped.values()), unmapped_count


def _timestamp_sort_value(value: str) -> float:
    try:
        return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00"))).timestamp()
    except ValueError:
        return float("-inf")


def _configurations(ingress: Mapping[str, object]) -> list[dict[str, object]]:
    sources = {
        str(source.get("id") or ""): source
        for source in _mapping_items(ingress.get("sources"))
    }
    configurations: list[dict[str, object]] = []
    for connection in _mapping_items(ingress.get("connections")):
        connection_id = str(connection.get("id") or "")
        source_id = str(connection.get("source_id") or "")
        source = sources.get(source_id, {})
        source_mode = str(source.get("mode") or "")
        provider_id = _configuration_provider_id(connection, source_id)
        source_enabled = bool(source.get("enabled", False))
        connection_enabled = bool(connection.get("enabled", False))
        connection_ready = _connection_ready(
            source_id=source_id,
            source_mode=source_mode,
            connection=connection,
        )
        for candidate in _mapping_items(connection.get("model_candidates")):
            configuration_id = str(candidate.get("id") or "")
            model_id = str(candidate.get("model_id") or "")
            scan_profile = str(candidate.get("scan_profile") or "")
            route_fingerprint = None
            if source_mode == "api" and configuration_id and model_id and scan_profile:
                route_fingerprint = build_route_fingerprint(
                    source_id=source_id,
                    connection_id=connection_id,
                    connection_mode=source_mode,
                    api_format=_optional_text(connection.get("api_format")),
                    provider_preset=str(
                        connection.get("provider_preset") or "generic"
                    ),
                    base_url=_optional_text(connection.get("base_url")),
                    model_id=model_id,
                    scan_profile=scan_profile,
                )
            configurations.append(
                {
                    "model_configuration_id": configuration_id,
                    "enabled": (
                        source_enabled
                        and connection_enabled
                        and bool(candidate.get("enabled", False))
                    ),
                    "connection_ready": connection_ready,
                    "identity_resolved": bool(
                        configuration_id and model_id and scan_profile
                    ),
                    "provider_id": provider_id,
                    "canonical_model_id": model_id,
                    "reasoning_effort": scan_profile,
                    "route_fingerprint": route_fingerprint,
                }
            )
    return configurations


def _align_official_snapshot(
    official_snapshot: Mapping[str, object] | None,
    configurations: Sequence[Mapping[str, object]],
) -> Mapping[str, object] | None:
    if official_snapshot is None:
        return None
    rows = _mapping_items(official_snapshot.get("rows"))
    configuration_by_id = {
        str(item.get("model_configuration_id") or ""): item
        for item in configurations
        if item.get("model_configuration_id")
    }
    configurations_by_identity: dict[
        tuple[str, str, str], list[Mapping[str, object]]
    ] = {}
    for configuration in configurations:
        identity = _canonical_configuration_identity(configuration)
        if identity is not None:
            configurations_by_identity.setdefault(identity, []).append(configuration)
    rows_by_identity: dict[tuple[str, str, str], list[Mapping[str, object]]] = {}
    for row in rows:
        identity = _canonical_configuration_identity(row)
        if identity is not None:
            rows_by_identity.setdefault(identity, []).append(row)

    aligned_rows: list[dict[str, object]] = []
    for row in rows:
        row_copy = dict(row)
        source_configuration_id = str(row.get("model_configuration_id") or "")
        if source_configuration_id in configuration_by_id:
            aligned_rows.append(row_copy)
            continue
        identity = _canonical_configuration_identity(row)
        matching_configurations = (
            configurations_by_identity.get(identity, []) if identity is not None else []
        )
        matching_rows = rows_by_identity.get(identity, []) if identity is not None else []
        if len(matching_configurations) == 1 and len(matching_rows) == 1:
            row_copy["source_model_configuration_id"] = (
                row.get("source_model_configuration_id") or source_configuration_id
            )
            row_copy["model_configuration_id"] = str(
                matching_configurations[0]["model_configuration_id"]
            )
        aligned_rows.append(row_copy)

    aligned_snapshot = dict(official_snapshot)
    aligned_snapshot["rows"] = aligned_rows
    return aligned_snapshot


def _canonical_configuration_identity(
    item: Mapping[str, object],
) -> tuple[str, str, str] | None:
    provider_id = _canonical_provider_id(_optional_text(item.get("provider_id")))
    model_id = str(item.get("canonical_model_id") or "").strip().casefold()
    effort = _canonical_effort(_optional_text(item.get("reasoning_effort")))
    if not provider_id or not model_id or not effort:
        return None
    return provider_id, model_id, effort


def _configuration_provider_id(
    connection: Mapping[str, object],
    source_id: str,
) -> str:
    explicit = _optional_text(connection.get("provider_id"))
    if explicit:
        return explicit
    return {
        "codex_local": "codex",
        "claude_local": "claude-code",
        "grok_local": "grok-build",
    }.get(source_id, source_id)


def _canonical_provider_id(value: str | None) -> str:
    normalized = str(value or "").strip().casefold()
    return {
        "codex": "openai",
        "openai": "openai",
        "claude": "anthropic",
        "claude-code": "anthropic",
        "anthropic": "anthropic",
        "grok": "xai",
        "grok-build": "xai",
        "xai": "xai",
    }.get(normalized, normalized)


def _canonical_effort(value: str | None) -> str:
    return str(value or "").strip().casefold().replace("-", "").replace("_", "")


def _connection_ready(
    *,
    source_id: str,
    source_mode: str,
    connection: Mapping[str, object],
) -> bool:
    if source_mode == "api":
        return str(connection.get("last_test_status") or "") == "ok"
    if source_id == "claude_local":
        return bool(connection.get("local_login_verified", False))
    return bool(source_mode)


def _local_evaluation_source(
    state: Mapping[str, object],
    *,
    configurations: Sequence[Mapping[str, object]],
    now: datetime,
) -> dict[str, object]:
    dashboard = _local_evaluation_dashboard(state)
    question_pack = _mapping(state.get("question_pack"))
    run_metadata = _mapping(dashboard.get("run_metadata"))
    rows = _mapping_items(dashboard.get("leaderboard"))
    route_by_configuration = {
        str(configuration.get("model_configuration_id") or ""): _optional_text(
            configuration.get("route_fingerprint")
        )
        for configuration in configurations
    }
    grader_version = _grader_version(run_metadata.get("scoring_mode"))
    question_pack_version = str(question_pack.get("version") or "")
    required_question_count = _integer(question_pack.get("question_count"))
    normalized_rows = [
        _local_result_row(
            row,
            expected_route=route_by_configuration.get(
                str(row.get("candidate_id") or "")
            ),
            required_question_count=required_question_count,
        )
        for row in rows
    ]
    current_run_id = str(dashboard.get("current_run_id") or "unknown")
    return {
        "source": "local_evaluation",
        "snapshot_id": f"local:{current_run_id}",
        "pricing_snapshot_id": current_pricing_snapshot_id(),
        "published_at": now.isoformat().replace("+00:00", "Z"),
        "question_pack_version": question_pack_version,
        "grader_version": grader_version,
        "rows": normalized_rows,
    }


def _local_evaluation_dashboard(
    state: Mapping[str, object],
) -> Mapping[str, object]:
    dashboard = _mapping(state.get("dashboard"))
    run_metadata = _mapping(dashboard.get("run_metadata"))
    runtime = _mapping(state.get("runtime"))
    if (
        str(run_metadata.get("status") or "") == "completed"
        and str(run_metadata.get("evaluation_result_level") or "") == "complete"
        and not bool(runtime.get("is_running", False))
    ):
        return dashboard
    stable_evidence_dashboard = _mapping(state.get("stable_evidence_dashboard"))
    if stable_evidence_dashboard:
        return stable_evidence_dashboard
    stable_dashboard = _mapping(state.get("stable_dashboard"))
    return stable_dashboard if stable_dashboard else dashboard


def _local_result_row(
    row: Mapping[str, object],
    *,
    expected_route: str | None,
    required_question_count: int,
) -> dict[str, object]:
    route_status = str(row.get("route_identity_status") or "")
    actual_route = None
    if expected_route:
        actual_route = (
            expected_route
            if route_status == "matched"
            else f"route-status:{route_status or 'missing'}"
        )
    question_results = row.get("question_results")
    completed = _integer(row.get("question_completed"))
    row_pack = str(row.get("question_pack_version") or "")
    row_grader = _grader_version(row.get("scoring_mode"))
    complete = (
        bool(row.get("is_current_pack_comparable", False))
        and required_question_count > 0
        and completed == required_question_count
    )
    return {
        "model_configuration_id": str(row.get("candidate_id") or ""),
        "completed_at": (
            row.get("latest_valid_at") or row.get("valid_completed_at")
        ),
        "complete": complete,
        "hard_failure": _has_hard_failure(row),
        "question_pack_version": row_pack,
        "grader_version": row_grader,
        "route_fingerprint": actual_route,
        "overall_score": row.get("overall_score"),
        "elapsed_seconds": row.get("elapsed_seconds"),
        "estimated_cost_usd": row.get("estimated_cost_usd"),
        "cost_coverage": row.get("cost_coverage"),
        "question_results": question_results if isinstance(question_results, list) else [],
    }


def _has_hard_failure(row: Mapping[str, object]) -> bool:
    if row.get("repairable_question_ids"):
        return True
    question_results = row.get("question_results")
    if not isinstance(question_results, list):
        return False
    failure_statuses = {"error", "failed", "timeout", "cancelled"}
    return any(
        str(item.get("status") or item.get("final_status") or "").lower()
        in failure_statuses
        for item in question_results
        if isinstance(item, Mapping)
    )


def _current_status_hint(
    current_configuration_id: str | None,
    detection_status: str,
) -> str | None:
    if current_configuration_id:
        return None
    if detection_status in {"active_mixed", "active_single", "unmapped"}:
        return "unmapped"
    return "no_usage"


def _grader_version(value: object) -> str:
    scoring_mode = str(value or "").strip()
    if scoring_mode in {"", "unknown", "legacy"}:
        return ""
    return f"scoring-mode:{scoring_mode}"


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _mapping_items(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _integer(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
