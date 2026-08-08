from __future__ import annotations


SCAN_PHASE = "scan"
REPAIR_PHASE = "repair"
ACTIVE_SCAN_LIFECYCLE = "active_scan"

_LEGACY_SCAN_PHASES = {"quick", "review", "legacy"}
_LEGACY_ACTIVE_LIFECYCLES = {"active_quick", "active_review"}


def normalize_phase(value: object) -> str:
    phase = str(value or "")
    if phase in _LEGACY_SCAN_PHASES or phase == SCAN_PHASE:
        return SCAN_PHASE
    return phase


def normalize_lifecycle(value: object) -> str:
    lifecycle = str(value or "")
    if lifecycle in _LEGACY_ACTIVE_LIFECYCLES:
        return ACTIVE_SCAN_LIFECYCLE
    return lifecycle


def is_active_lifecycle(value: object) -> bool:
    return normalize_lifecycle(value) in {"preparing", ACTIVE_SCAN_LIFECYCLE}


def planned_attempts_payload(active_run: dict[str, object] | None) -> object:
    payload = active_run or {}
    current = payload.get(
        "planned_attempts_by_candidate",
        payload.get("planned_attempts"),
    )
    if isinstance(current, dict):
        return current

    first = payload.get(
        "planned_quick_attempts_by_candidate",
        payload.get("planned_quick_attempts", {}),
    )
    second = payload.get(
        "planned_review_attempts_by_candidate",
        payload.get("planned_review_attempts", {}),
    )
    combined: dict[str, int] = {}
    for source in (first, second):
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            combined[str(key)] = combined.get(str(key), 0) + int(value or 0)
    return combined


def metadata_question_count(metadata: dict[str, object] | None) -> int:
    payload = metadata or {}
    count = max(0, int(payload.get("question_count") or 0))
    if str(payload.get("scoring_mode") or "") == "semantic_q1_q5_equal_v2":
        count += max(0, int(payload.get("review_question_count") or 0))
    return count


def metadata_question_ids(metadata: dict[str, object] | None) -> list[str]:
    payload = metadata or {}
    values: list[str] = []
    for key in ("question_ids", "review_question_ids"):
        source = payload.get(key)
        if not isinstance(source, list):
            continue
        for item in source:
            value = str(item)
            if value and value not in values:
                values.append(value)
    return values


def normalized_metadata_projection(
    raw_metadata: dict[str, object],
    normalized_metadata: dict[str, object],
) -> dict[str, object]:
    projection = dict(normalized_metadata)
    projection["question_count"] = metadata_question_count(raw_metadata)
    question_ids = metadata_question_ids(raw_metadata)
    if question_ids:
        projection["question_ids"] = question_ids
    return projection


def normalized_capability_label(question_id: str, value: str | None) -> str:
    if question_id == "05_cache_regression_test_design" and value == "回归验证":
        return "测试设计"
    return value or question_id


def normalized_detail_label(question_id: str, value: str | None) -> str:
    if question_id == "05_cache_regression_test_design" and value == "测试设计":
        return "缓存回归"
    return value or question_id
