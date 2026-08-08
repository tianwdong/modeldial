from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import math
from typing import Iterable


QUOTA_BURN_SCHEMA_VERSION = 1
MAX_ATTRIBUTION_INTERVAL_SECONDS = 6 * 60 * 60
MIN_ATTRIBUTED_INTERVALS = 3
MIN_ATTRIBUTED_WORK_UNITS = 5
QUALIFIED_ATTRIBUTED_INTERVALS = 10
QUALIFIED_ATTRIBUTED_WORK_UNITS = 30
MEASUREMENT_RESOLUTION_PERCENT = 1.0


def build_quota_burn_summary(
    account_snapshots: Iterable[dict[str, object]],
    usage_state: dict[str, object],
    workload_summary: dict[str, object],
) -> dict[str, object]:
    snapshots = sorted(
        (
            (timestamp, item)
            for item in account_snapshots
            if isinstance(item, dict)
            and (timestamp := _timestamp(item.get("captured_at"))) is not None
        ),
        key=lambda item: item[0],
    )
    base = {
        "schema_version": QUOTA_BURN_SCHEMA_VERSION,
        "status": "collecting",
        "source": "codex_app_server",
        "snapshot_count": len(snapshots),
        "measurement_resolution_percent": MEASUREMENT_RESOLUTION_PERCENT,
        "attributed_interval_count": 0,
        "rejected_intervals": {},
        "aggregates": [],
    }
    if snapshots:
        latest = snapshots[-1][1]
        if (
            latest.get("account_type") != "chatgpt"
            or latest.get("quota_status") == "not_applicable"
        ):
            return {**base, "status": "not_applicable"}
    if len(snapshots) < 2:
        return base

    coverage_floor = _coverage_floor(usage_state, workload_summary)
    if coverage_floor is None:
        return {
            **base,
            "rejected_intervals": {"coverage_gap": 1},
        }

    raw_observations = usage_state.get("observations")
    observations = (
        [item for item in raw_observations.values() if isinstance(item, dict)]
        if isinstance(raw_observations, dict)
        else []
    )
    main_rows = [row for row in observations if _is_main_workload(row)]
    active_starts = _active_main_turn_starts(usage_state)
    rejected: Counter[str] = Counter()
    interval_keys: set[tuple[datetime, datetime]] = set()
    for row in main_rows:
        if row.get("outcome") != "completed":
            continue
        started_at = _timestamp(row.get("started_at"))
        ended_at = _timestamp(row.get("ended_at"))
        if started_at is None or ended_at is None or ended_at < started_at:
            continue
        before = _latest_snapshot_at_or_before(snapshots, started_at)
        after = _earliest_snapshot_at_or_after(snapshots, ended_at)
        if before is None or after is None or before[0] >= after[0]:
            rejected["snapshot_bracket_missing"] += 1
            continue
        interval_keys.add((before[0], after[0]))

    samples: dict[tuple[str, int, str], list[dict[str, object]]] = defaultdict(list)
    attributed_intervals: set[tuple[datetime, datetime]] = set()
    for before_time, after_time in sorted(interval_keys):
        if before_time < coverage_floor:
            rejected["coverage_gap"] += 1
            continue
        if (after_time - before_time).total_seconds() > MAX_ATTRIBUTION_INTERVAL_SECONDS:
            rejected["interval_too_long"] += 1
            continue
        before = _snapshot_at(snapshots, before_time)
        after = _snapshot_at(snapshots, after_time)
        if before is None or after is None:
            rejected["snapshot_bracket_missing"] += 1
            continue
        if not _same_account_context(before, after):
            rejected["account_context_changed"] += 1
            continue
        overlapping = [
            row
            for row in main_rows
            if _row_overlaps(row, before_time, after_time)
        ]
        if not overlapping or not _clean_completed_interval(
            overlapping,
            before_time,
            after_time,
        ):
            rejected["unclean_workload"] += 1
            continue
        if _has_concurrency(overlapping):
            rejected["concurrent_main_work"] += 1
            continue
        if any(before_time <= start < after_time for start in active_starts):
            rejected["active_workload"] += 1
            continue
        configuration_ids = {
            _text(row.get("model_configuration_id")) for row in overlapping
        }
        if None in configuration_ids or len(configuration_ids) != 1:
            rejected["mixed_model_configuration"] += 1
            continue
        first = overlapping[0]
        configuration_id = next(iter(configuration_ids))
        assert configuration_id is not None
        work_unit_count = len(overlapping)
        valid_window_found = False
        before_windows = _windows_by_id(before)
        after_windows = _windows_by_id(after)
        for window_id, before_window in before_windows.items():
            after_window = after_windows.get(window_id)
            if after_window is None or not _same_window(before_window, after_window):
                rejected["window_changed"] += 1
                continue
            reset_at = _timestamp(before_window.get("resets_at"))
            if reset_at is None or after_time > reset_at:
                rejected["window_changed"] += 1
                continue
            before_used = _percentage(before_window.get("used_percent"))
            after_used = _percentage(after_window.get("used_percent"))
            if before_used is None or after_used is None:
                rejected["missing_counter"] += 1
                continue
            delta = after_used - before_used
            if delta < 0:
                rejected["counter_decreased"] += 1
                continue
            if delta == 0:
                rejected["below_resolution"] += 1
                continue
            window_seconds = _positive_int(before_window.get("window_seconds"))
            if window_seconds is None:
                rejected["window_changed"] += 1
                continue
            confidence = min(
                _confidence(row.get("attribution_confidence"))
                for row in overlapping
            )
            samples[(window_id, window_seconds, configuration_id)].append(
                {
                    "quota_per_work_unit_percent": delta / work_unit_count,
                    "work_unit_count": work_unit_count,
                    "confidence": confidence,
                    "raw_model_id": first.get("raw_model_id"),
                    "reasoning_effort": first.get("reasoning_effort"),
                    "provider_id": first.get("provider_id"),
                    "window_label": before_window.get("label"),
                }
            )
            valid_window_found = True
        if valid_window_found:
            attributed_intervals.add((before_time, after_time))

    aggregates = [
        _aggregate(key, group)
        for key, group in samples.items()
    ]
    aggregates.sort(
        key=lambda item: (
            int(item.get("window_seconds") or 0),
            str(item.get("model_configuration_id") or ""),
        )
    )
    return {
        **base,
        "status": (
            "available"
            if any(item["usable_for_recommendation"] for item in aggregates)
            else "collecting"
        ),
        "attributed_interval_count": len(attributed_intervals),
        "rejected_intervals": dict(sorted(rejected.items())),
        "aggregates": aggregates,
    }


def _aggregate(
    key: tuple[str, int, str],
    samples: list[dict[str, object]],
) -> dict[str, object]:
    window_id, window_seconds, configuration_id = key
    values = sorted(float(item["quota_per_work_unit_percent"]) for item in samples)
    interval_count = len(samples)
    work_units = sum(int(item["work_unit_count"]) for item in samples)
    usable = (
        interval_count >= MIN_ATTRIBUTED_INTERVALS
        and work_units >= MIN_ATTRIBUTED_WORK_UNITS
    )
    sample_cap = (
        1.0
        if interval_count >= QUALIFIED_ATTRIBUTED_INTERVALS
        and work_units >= QUALIFIED_ATTRIBUTED_WORK_UNITS
        else 0.55
        if usable
        else 0.3
    )
    first = samples[0]
    return {
        "schema_version": QUOTA_BURN_SCHEMA_VERSION,
        "model_configuration_id": configuration_id,
        "provider_id": first.get("provider_id"),
        "raw_model_id": first.get("raw_model_id"),
        "reasoning_effort": first.get("reasoning_effort"),
        "window_id": window_id,
        "window_label": first.get("window_label"),
        "window_seconds": window_seconds,
        "attributed_interval_count": interval_count,
        "attributed_work_units": work_units,
        "quota_per_work_unit_percent": {
            "median": round(_percentile(values, 0.5), 4),
            "p25": round(_percentile(values, 0.25), 4),
            "p75": round(_percentile(values, 0.75), 4),
        },
        "measurement_resolution_percent": MEASUREMENT_RESOLUTION_PERCENT,
        "confidence": round(
            min(
                sample_cap,
                min(float(item["confidence"]) for item in samples),
            ),
            2,
        ),
        "usable_for_recommendation": usable,
    }


def _coverage_floor(
    usage_state: dict[str, object],
    workload_summary: dict[str, object],
) -> datetime | None:
    if bool(workload_summary.get("coverage_complete", False)):
        return _timestamp(workload_summary.get("period_start"))
    return _timestamp(
        workload_summary.get("coverage_continuous_since")
        or usage_state.get("coverage_continuous_since")
    )


def _is_main_workload(row: dict[str, object]) -> bool:
    reasons = set(_string_list(row.get("exclusion_reasons")))
    return bool(
        not row.get("is_subagent", False)
        and not row.get("is_modeldial_evaluation", False)
        and _text(row.get("raw_model_id")) != "codex-auto-review"
        and not reasons.intersection({"system_model", "modeldial_evaluation"})
    )


def _clean_completed_interval(
    rows: list[dict[str, object]],
    before: datetime,
    after: datetime,
) -> bool:
    for row in rows:
        started_at = _timestamp(row.get("started_at"))
        ended_at = _timestamp(row.get("ended_at"))
        if (
            started_at is None
            or ended_at is None
            or started_at < before
            or ended_at > after
            or ended_at < started_at
            or row.get("outcome") != "completed"
            or (_text(row.get("provider_id")) or "").casefold() != "openai"
            or not _text(row.get("raw_model_id"))
            or not _text(row.get("reasoning_effort"))
        ):
            return False
    return True


def _has_concurrency(rows: list[dict[str, object]]) -> bool:
    intervals = sorted(
        (
            _timestamp(row.get("started_at")),
            _timestamp(row.get("ended_at")),
        )
        for row in rows
    )
    previous_end = None
    for started_at, ended_at in intervals:
        if started_at is None or ended_at is None:
            return True
        if previous_end is not None and started_at < previous_end:
            return True
        previous_end = ended_at
    return False


def _active_main_turn_starts(usage_state: dict[str, object]) -> list[datetime]:
    files = usage_state.get("files")
    if not isinstance(files, dict):
        return []
    starts = []
    for state in files.values():
        if not isinstance(state, dict) or state.get("is_subagent", False):
            continue
        if state.get("is_modeldial_scan", False):
            continue
        current = state.get("current_turn")
        if not isinstance(current, dict):
            continue
        started_at = _timestamp(current.get("started_at"))
        if started_at is not None:
            starts.append(started_at)
    return starts


def _same_account_context(
    before: dict[str, object],
    after: dict[str, object],
) -> bool:
    return bool(
        before.get("source") == after.get("source") == "codex_app_server"
        and before.get("account_type") == after.get("account_type") == "chatgpt"
        and before.get("login_state") == after.get("login_state") == "authenticated"
        and before.get("plan_type") == after.get("plan_type")
        and before.get("quota_status") == after.get("quota_status") == "available"
    )


def _same_window(
    before: dict[str, object],
    after: dict[str, object],
) -> bool:
    return bool(
        _text(before.get("resets_at"))
        and before.get("resets_at") == after.get("resets_at")
        and _positive_int(before.get("window_seconds"))
        == _positive_int(after.get("window_seconds"))
        and before.get("limit_id") == after.get("limit_id")
    )


def _windows_by_id(snapshot: dict[str, object]) -> dict[str, dict[str, object]]:
    windows = snapshot.get("quota_windows")
    if not isinstance(windows, list):
        return {}
    return {
        window_id: item
        for item in windows
        if isinstance(item, dict)
        and (window_id := _quota_window_identity(item)) is not None
    }


def _quota_window_identity(window: dict[str, object]) -> str | None:
    limit_id = _text(window.get("limit_id"))
    window_seconds = _positive_int(window.get("window_seconds"))
    if limit_id is not None and window_seconds is not None:
        if window_seconds % 60 == 0:
            return f"{limit_id}:{window_seconds // 60}m"
        return f"{limit_id}:{window_seconds}s"
    return _text(window.get("window_id"))


def _row_overlaps(
    row: dict[str, object],
    before: datetime,
    after: datetime,
) -> bool:
    started_at = _timestamp(row.get("started_at"))
    ended_at = _timestamp(row.get("ended_at"))
    return bool(
        started_at is not None
        and ended_at is not None
        and started_at < after
        and ended_at > before
    )


def _latest_snapshot_at_or_before(
    snapshots: list[tuple[datetime, dict[str, object]]],
    timestamp: datetime,
) -> tuple[datetime, dict[str, object]] | None:
    matches = [item for item in snapshots if item[0] <= timestamp]
    return matches[-1] if matches else None


def _earliest_snapshot_at_or_after(
    snapshots: list[tuple[datetime, dict[str, object]]],
    timestamp: datetime,
) -> tuple[datetime, dict[str, object]] | None:
    return next((item for item in snapshots if item[0] >= timestamp), None)


def _snapshot_at(
    snapshots: list[tuple[datetime, dict[str, object]]],
    timestamp: datetime,
) -> dict[str, object] | None:
    return next((item for item_time, item in snapshots if item_time == timestamp), None)


def _percentile(values: list[float], fraction: float) -> float:
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    weight = position - lower
    return values[lower] * (1.0 - weight) + values[upper] * weight


def _percentage(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) and 0 <= parsed <= 100 else None


def _confidence(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _positive_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _timestamp(value: object) -> datetime | None:
    text = _text(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None
