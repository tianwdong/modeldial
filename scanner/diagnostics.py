from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping


DIAGNOSTIC_SCHEMA_VERSION = 1


def build_diagnostic_summary(
    snapshot: Mapping[str, object],
    codex_insights: Mapping[str, object],
    advisor: Mapping[str, object] | None,
    *,
    recommendation_portfolio: Mapping[str, object] | None = None,
    generated_at: str | None = None,
) -> dict[str, object]:
    account = _mapping(codex_insights.get("account"))
    workload = _mapping(codex_insights.get("workload"))
    quota_burn = _mapping(codex_insights.get("quota_burn"))
    collection = _mapping(codex_insights.get("collection"))
    app_server_collection = _mapping(collection.get("app_server"))
    session_collection = _mapping(workload.get("collection"))
    advisor_payload = _mapping(advisor)
    benefits = _mapping(advisor_payload.get("benefits"))
    portfolio_payload = _mapping(recommendation_portfolio)
    portfolio_decision = _representative_portfolio_decision(portfolio_payload)
    portfolio_schema_version = _nonnegative_int(
        portfolio_payload.get("schema_version")
    )
    advisor_ruleset_version = (
        f"recommendation-portfolio-v{portfolio_schema_version}"
        if portfolio_schema_version > 0
        else _text(advisor_payload.get("ruleset_version"))
    )
    advisor_reason = (
        _text(portfolio_decision.get("reason"))
        or _text(portfolio_payload.get("status"))
        or _text(advisor_payload.get("short_circuit_reason"))
    )
    question_pack = _mapping(snapshot.get("question_pack"))

    app_server_status = _status(
        app_server_collection.get("status"),
        allowed={"fresh", "cached", "stale", "unavailable"},
        fallback="unavailable",
    )
    account_status = (
        "available"
        if _text(account.get("login_state")) not in {None, "unknown"}
        else "unavailable"
    )
    rate_limits_status = _status(
        account.get("quota_status"),
        allowed={"available", "not_applicable", "unavailable"},
        fallback="unavailable",
    )
    model_catalog_status = _status(
        app_server_collection.get("model_catalog_status"),
        allowed={"available", "not_checked", "unavailable"},
        fallback="not_checked",
    )

    completed_work_units = 0
    behavior_observed_work_units = 0
    edit_work_units = 0
    retry_observed_edit_work_units = 0
    aggregates = workload.get("aggregates")
    if isinstance(aggregates, list):
        for item in aggregates:
            aggregate = _mapping(item)
            completed_work_units += _nonnegative_int(
                aggregate.get("completed_work_units")
            )
            behavior_observed_work_units += _nonnegative_int(
                aggregate.get("behavior_observed_work_units")
            )
            edit_work_units += _nonnegative_int(aggregate.get("edit_work_units"))
            retry_observed_edit_work_units += _nonnegative_int(
                aggregate.get("retry_observed_edit_work_units")
            )
    behavior_coverage_percent = (
        round(behavior_observed_work_units / completed_work_units * 100, 1)
        if completed_work_units
        else None
    )

    rejected_intervals = {
        str(key): _nonnegative_int(value)
        for key, value in _mapping(quota_burn.get("rejected_intervals")).items()
        if _nonnegative_int(value) > 0
    }
    session_history = {
        "source_count": _nonnegative_int(session_collection.get("source_count")),
        "discovered_file_count": _nonnegative_int(
            session_collection.get("discovered_file_count")
        ),
        "sampled_file_count": _nonnegative_int(
            session_collection.get("sampled_file_count")
        ),
        "parsed_file_count": _nonnegative_int(
            session_collection.get("parsed_file_count")
        ),
        "failed_file_count": _nonnegative_int(
            session_collection.get("failed_file_count")
        ),
        "unknown_file_count": _nonnegative_int(
            session_collection.get("unknown_file_count")
        ),
        "deduplicated_file_count": _nonnegative_int(
            session_collection.get("deduplicated_file_count")
        ),
        "budget_limited_file_count": _nonnegative_int(
            session_collection.get("budget_limited_file_count")
        ),
        "visible_started_at": _text(workload.get("coverage_started_at")),
        "continuous_since": _text(workload.get("coverage_continuous_since")),
        "coverage_complete": bool(workload.get("coverage_complete", False)),
        "gap_detected": bool(session_collection.get("gap_detected", False)),
        "upstream_retention_risk": _status(
            session_collection.get("upstream_retention_risk"),
            allowed={"not_detected", "possible", "unknown"},
            fallback="unknown",
        ),
    }
    workload_status = _status(
        workload.get("status"),
        allowed={"available", "unavailable"},
        fallback="unavailable",
    )
    has_health_issue = any(
        (
            app_server_status in {"stale", "unavailable"},
            account_status == "unavailable",
            workload_status == "unavailable",
            session_history["gap_detected"],
            session_history["failed_file_count"] > 0,
            session_history["budget_limited_file_count"] > 0,
            session_history["upstream_retention_risk"] == "possible",
        )
    )

    return {
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "generated_at": generated_at or _timestamp(),
        "overall_status": "attention" if has_health_issue else "healthy",
        "app_server": {
            "status": app_server_status,
            "last_read_at": _text(app_server_collection.get("last_read_at"))
            or _text(account.get("captured_at")),
            "read_duration_ms": _optional_nonnegative_int(
                app_server_collection.get("read_duration_ms")
            ),
        },
        "capabilities": {
            "model_catalog": model_catalog_status,
            "account": account_status,
            "rate_limits": rate_limits_status,
        },
        "session_history": session_history,
        "behavior": {
            "completed_work_units": completed_work_units,
            "observed_work_units": behavior_observed_work_units,
            "coverage_percent": behavior_coverage_percent,
            "edit_work_units": edit_work_units,
            "retry_observed_edit_work_units": retry_observed_edit_work_units,
            "retry_indeterminate_edit_work_units": max(
                0,
                edit_work_units - retry_observed_edit_work_units,
            ),
        },
        "versions": {
            "question_pack_id": _text(question_pack.get("id")),
            "question_pack_version": _text(question_pack.get("version")),
            "advisor_ruleset_version": advisor_ruleset_version,
            "pricing_snapshot_id": _text(benefits.get("pricing_snapshot_id")),
        },
        "advisor_short_circuit_reason": advisor_reason,
        "quota_status": _status(
            quota_burn.get("status"),
            allowed={"available", "insufficient_evidence", "not_applicable", "unavailable"},
            fallback="unavailable",
        ),
        "quota_rejected_intervals": dict(sorted(rejected_intervals.items())),
    }


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _representative_portfolio_decision(
    portfolio: Mapping[str, object],
) -> Mapping[str, object]:
    decisions = portfolio.get("decisions")
    if not isinstance(decisions, list):
        return {}
    representative_id = _text(portfolio.get("representative_configuration_id"))
    mapped_decisions = [_mapping(item) for item in decisions]
    if representative_id is not None:
        for decision in mapped_decisions:
            if (
                _text(decision.get("current_model_configuration_id"))
                == representative_id
            ):
                return decision
    return next((decision for decision in mapped_decisions if decision), {})


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _status(value: object, *, allowed: set[str], fallback: str) -> str:
    normalized = _text(value)
    return normalized if normalized in allowed else fallback


def _nonnegative_int(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def _optional_nonnegative_int(value: object) -> int | None:
    if value is None:
        return None
    return _nonnegative_int(value)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
