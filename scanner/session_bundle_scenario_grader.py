from __future__ import annotations

import copy
import json

from .bounded_json import BoundedJSONError, bounded_json_loads


MAX_TESTS = 3
MAX_STEPS = 8

CASE_SPECS = (
    ("consumes_despite_existing_target", "拒绝覆盖优先级", "snapshot"),
    ("snapshots_metadata_after_events", "元数据预消费快照", "snapshot"),
    ("late_event_snapshot", "逐事件快照时机", "snapshot"),
    ("skips_recursive_mapping_normalization", "递归映射归一化", "snapshot"),
    ("overconsumes_event_limit", "事件上限与停止消费", "snapshot"),
    ("deletes_target_on_validation_failure", "校验失败原子性", "atomicity"),
    ("deletes_target_on_iteration_failure", "迭代失败原子性", "atomicity"),
    ("deletes_target_on_serialization_failure", "序列化失败原子性", "atomicity"),
    ("deletes_target_on_member_size_failure", "成员超限原子性", "atomicity"),
    ("leaves_temp_file", "替换失败原子性", "atomicity"),
    ("clobbers_racing_target", "无覆盖提交竞态", "atomicity"),
    ("preserves_mapping_insertion_order", "逻辑输入确定性编码", "durability"),
    ("uses_wall_clock_zip_timestamps", "归档成员顺序与时间戳", "durability"),
    ("skips_temporary_archive_fsync", "临时归档先落盘", "durability"),
    ("skips_parent_directory_fsync", "提交后目录落盘", "durability"),
    ("uses_recorded_replay_result", "实际结果写入回放输出", "replay"),
    ("ignores_stop_on_error", "按实际失败停止", "replay"),
    ("always_stops_on_failure", "实际失败后继续", "replay"),
    ("hardcodes_store_history", "历史参数透传", "replay"),
    ("leaks_history_state", "历史状态逐次与异常恢复", "replay"),
)
MUTANT_IDS = tuple(spec[0] for spec in CASE_SPECS)
MAX_SCORE = len(MUTANT_IDS)
SECONDARY_VARIANTS = {
    "consumes_despite_existing_target": "consumes_despite_existing_target__metadata",
    "snapshots_metadata_after_events": "snapshots_metadata_after_events__nested",
    "late_event_snapshot": "late_event_snapshot__three_events",
    "skips_recursive_mapping_normalization": "skips_recursive_mapping_normalization__ba",
    "overconsumes_event_limit": "overconsumes_event_limit__existing",
    "deletes_target_on_validation_failure": "deletes_target_on_validation_failure__missing",
    "deletes_target_on_iteration_failure": "deletes_target_on_iteration_failure__missing",
    "deletes_target_on_serialization_failure": "deletes_target_on_serialization_failure__missing",
    "deletes_target_on_member_size_failure": "deletes_target_on_member_size_failure__missing",
    "leaves_temp_file": "leaves_temp_file__missing",
    "clobbers_racing_target": "clobbers_racing_target__metadata",
    "preserves_mapping_insertion_order": "preserves_mapping_insertion_order__nested",
    "skips_temporary_archive_fsync": "skips_temporary_archive_fsync__overwrite",
    "skips_parent_directory_fsync": "skips_parent_directory_fsync__unsupported",
    "uses_recorded_replay_result": "uses_recorded_replay_result__inverse",
    "ignores_stop_on_error": "ignores_stop_on_error__recorded_false",
    "always_stops_on_failure": "always_stops_on_failure__history_true",
    "hardcodes_store_history": "hardcodes_store_history__raise",
    "leaks_history_state": "leaks_history_state__raise",
}
CONTEXT_VARIANTS = {
    "deletes_target_on_validation_failure": "deletes_target_on_validation_failure__empty",
    "deletes_target_on_iteration_failure": "deletes_target_on_iteration_failure__late_events",
    "deletes_target_on_serialization_failure": "deletes_target_on_serialization_failure__nested_mapping",
    "deletes_target_on_member_size_failure": "deletes_target_on_member_size_failure__boundary",
    "leaves_temp_file": "leaves_temp_file__noncanonical_archive",
}
VARIANTS_BY_CASE = {
    mutant_id: (
        (mutant_id,)
        + ((SECONDARY_VARIANTS[mutant_id],) if mutant_id in SECONDARY_VARIANTS else ())
        + ((CONTEXT_VARIANTS[mutant_id],) if mutant_id in CONTEXT_VARIANTS else ())
    )
    for mutant_id in MUTANT_IDS
}
VARIANT_TO_CASE = {
    variant: mutant_id
    for mutant_id, variants in VARIANTS_BY_CASE.items()
    for variant in variants
}
MUTANT_VARIANTS = tuple(
    variant
    for mutant_id in MUTANT_IDS
    for variant in VARIANTS_BY_CASE[mutant_id]
)
CASE_DETAILS = {
    case_id: (label, category)
    for case_id, label, category in CASE_SPECS
}
CATEGORY_LABELS = {
    "snapshot": "输入与快照",
    "atomicity": "提交原子性",
    "durability": "确定性与持久化",
    "replay": "回放状态",
}

SAVE_FIELDS = {
    "op",
    "target",
    "overwrite",
    "race_create",
    "metadata_features",
    "event_features",
    "event_count",
    "faults",
    "mapping_order",
    "clock",
    "directory_fsync",
    "checks",
}
REPLAY_FIELDS = {
    "op",
    "recorded_success",
    "actual_results",
    "stop_on_error",
    "store_history",
    "checks",
}
METADATA_FEATURES = {"mutates_during_iteration", "nested_mapping"}
EVENT_FEATURES = {"mutates_after_yield"}
FAULTS = {"validation", "iteration", "serialization", "member_size", "replace"}


def _plain_int(value: object) -> bool:
    return type(value) is int


def _unique_choices(
    value: object,
    field: str,
    allowed: set[str],
) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field}_must_be_string_list")
    if len(value) != len(set(value)) or not set(value) <= allowed:
        raise ValueError(f"invalid_{field}")
    return list(value)


def _validate_checks(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 12:
        raise ValueError("checks_must_contain_one_through_twelve_entries")
    paths: set[str] = set()
    checks: list[dict[str, object]] = []
    for raw_check in value:
        if not isinstance(raw_check, dict) or set(raw_check) != {"path", "equals"}:
            raise ValueError("invalid_check_fields")
        path = raw_check["path"]
        if (
            not isinstance(path, str)
            or not path
            or any(not part for part in path.split("."))
            or path in paths
        ):
            raise ValueError("invalid_check_path")
        json.dumps(raw_check["equals"], ensure_ascii=False)
        paths.add(path)
        checks.append({"path": path, "equals": copy.deepcopy(raw_check["equals"])})
    return checks


def _validate_save_step(raw: dict[str, object]) -> dict[str, object]:
    if set(raw) - SAVE_FIELDS:
        raise ValueError("unknown_save_field")
    target = raw.get("target")
    if target not in {"missing", "existing"}:
        raise ValueError("invalid_target")
    overwrite = raw.get("overwrite", False)
    race_create = raw.get("race_create", False)
    if not isinstance(overwrite, bool) or not isinstance(race_create, bool):
        raise ValueError("save_flags_must_be_boolean")
    metadata_features = _unique_choices(
        raw.get("metadata_features", []),
        "metadata_features",
        METADATA_FEATURES,
    )
    event_features = _unique_choices(
        raw.get("event_features", []),
        "event_features",
        EVENT_FEATURES,
    )
    faults = _unique_choices(raw.get("faults", []), "faults", FAULTS)
    event_count = raw.get("event_count", 1)
    clock = raw.get("clock", 19_800_101)
    if not _plain_int(event_count) or not 0 <= event_count <= 1_001:
        raise ValueError("invalid_event_count")
    if not _plain_int(clock) or not 0 <= clock <= 99_999_999:
        raise ValueError("invalid_clock")
    mapping_order = raw.get("mapping_order", "ab")
    directory_fsync = raw.get("directory_fsync", "ok")
    if mapping_order not in {"ab", "ba"}:
        raise ValueError("invalid_mapping_order")
    if directory_fsync not in {"ok", "unsupported"}:
        raise ValueError("invalid_directory_fsync")
    return {
        "op": "save",
        "target": target,
        "overwrite": overwrite,
        "race_create": race_create,
        "metadata_features": metadata_features,
        "event_features": event_features,
        "event_count": event_count,
        "faults": faults,
        "mapping_order": mapping_order,
        "clock": clock,
        "directory_fsync": directory_fsync,
        "checks": _validate_checks(raw.get("checks")),
    }


def _validate_replay_step(raw: dict[str, object]) -> dict[str, object]:
    if set(raw) - REPLAY_FIELDS or set(raw) != REPLAY_FIELDS:
        raise ValueError("invalid_replay_fields")
    recorded = raw.get("recorded_success")
    actual = raw.get("actual_results")
    if not isinstance(recorded, list) or not 1 <= len(recorded) <= 4:
        raise ValueError("invalid_recorded_success")
    if any(not isinstance(item, bool) for item in recorded):
        raise ValueError("recorded_success_must_be_boolean_list")
    if not isinstance(actual, list) or len(actual) != len(recorded):
        raise ValueError("actual_results_length_mismatch")
    if any(item not in {"success", "failure", "raise"} for item in actual):
        raise ValueError("invalid_actual_result")
    stop_on_error = raw.get("stop_on_error")
    store_history = raw.get("store_history")
    if not isinstance(stop_on_error, bool) or not isinstance(store_history, bool):
        raise ValueError("replay_flags_must_be_boolean")
    return {
        "op": "replay",
        "recorded_success": list(recorded),
        "actual_results": list(actual),
        "stop_on_error": stop_on_error,
        "store_history": store_history,
        "checks": _validate_checks(raw.get("checks")),
    }


def _validate_payload(payload: object) -> list[dict[str, object]]:
    if not isinstance(payload, dict) or set(payload) != {"tests"}:
        raise ValueError("payload_must_contain_only_tests")
    raw_tests = payload["tests"]
    if not isinstance(raw_tests, list) or not 1 <= len(raw_tests) <= MAX_TESTS:
        raise ValueError("tests_must_contain_one_through_three_entries")
    names: set[str] = set()
    tests: list[dict[str, object]] = []
    for raw_test in raw_tests:
        if not isinstance(raw_test, dict) or set(raw_test) != {"name", "steps"}:
            raise ValueError("invalid_test_fields")
        name = raw_test["name"]
        raw_steps = raw_test["steps"]
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("invalid_test_name")
        if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= MAX_STEPS:
            raise ValueError("steps_must_contain_one_through_eight_entries")
        steps: list[dict[str, object]] = []
        for raw_step in raw_steps:
            if not isinstance(raw_step, dict):
                raise ValueError("step_must_be_object")
            if raw_step.get("op") == "save":
                steps.append(_validate_save_step(raw_step))
            elif raw_step.get("op") == "replay":
                steps.append(_validate_replay_step(raw_step))
            else:
                raise ValueError("unknown_step_operation")
        names.add(name)
        tests.append({"name": name, "steps": steps})
    return tests


def _fault_observations(
    step: dict[str, object],
    bug: str,
) -> dict[str, dict[str, object]]:
    target_before = "old" if step["target"] == "existing" else "missing"
    bug_by_fault = {
        "validation": "deletes_target_on_validation_failure",
        "iteration": "deletes_target_on_iteration_failure",
        "serialization": "deletes_target_on_serialization_failure",
        "member_size": "deletes_target_on_member_size_failure",
        "replace": "leaves_temp_file",
    }
    secondary_by_fault = {
        "validation": "deletes_target_on_validation_failure__missing",
        "iteration": "deletes_target_on_iteration_failure__missing",
        "serialization": "deletes_target_on_serialization_failure__missing",
        "member_size": "deletes_target_on_member_size_failure__missing",
        "replace": "leaves_temp_file__missing",
    }
    observations: dict[str, dict[str, object]] = {}
    for fault in step["faults"]:
        observations[str(fault)] = {
            "status": f"{fault}_error",
            "target": (
                "partial"
                if bug == secondary_by_fault[fault] and fault != "replace"
                else
                "missing"
                if bug == bug_by_fault[fault] and fault != "replace"
                else target_before
            ),
            "temporary_exists": (
                bug in {bug_by_fault[fault], secondary_by_fault[fault]}
                and fault == "replace"
            ),
        }
    return observations


def _simulate_save(step: dict[str, object], bug: str) -> dict[str, object]:
    target_before = "old" if step["target"] == "existing" else "missing"
    if step["target"] == "existing" and not step["overwrite"]:
        return {
            "status": "FileExistsError",
            "events_consumed": 1 if bug == "consumes_despite_existing_target" else 0,
            "target": target_before,
            "temporary_exists": False,
        }

    metadata_features = set(step["metadata_features"])
    event_features = set(step["event_features"])
    metadata_snapshot = (
        "after"
        if bug == "snapshots_metadata_after_events"
        and "mutates_during_iteration" in metadata_features
        else "before"
        if "mutates_during_iteration" in metadata_features
        else "stable"
    )
    event_snapshot = (
        "after"
        if bug == "late_event_snapshot" and "mutates_after_yield" in event_features
        else "before"
        if "mutates_after_yield" in event_features
        else "stable"
    )
    nested_snapshot = (
        "serialization_error"
        if bug == "skips_recursive_mapping_normalization"
        and "nested_mapping" in metadata_features
        else "normalized"
        if "nested_mapping" in metadata_features
        else "absent"
    )
    event_count = int(step["event_count"])
    if event_count > 1_000:
        return {
            "status": "event_limit_error",
            "events_consumed": 1_001 if bug == "overconsumes_event_limit" else 1_000,
            "metadata_snapshot": metadata_snapshot,
            "event_snapshot": event_snapshot,
            "nested_snapshot": nested_snapshot,
            "target": target_before,
            "temporary_exists": False,
        }
    if nested_snapshot == "serialization_error":
        return {
            "status": "serialization_error",
            "events_consumed": event_count,
            "metadata_snapshot": metadata_snapshot,
            "event_snapshot": event_snapshot,
            "nested_snapshot": nested_snapshot,
            "target": target_before,
            "temporary_exists": False,
        }
    if step["faults"]:
        return {
            "status": "fault_matrix",
            "events_consumed": event_count,
            "faults": _fault_observations(step, bug),
        }

    mapping_order = (
        str(step["mapping_order"])
        if bug == "preserves_mapping_insertion_order"
        else "ab"
    )
    timestamp = int(step["clock"]) if bug == "uses_wall_clock_zip_timestamps" else 19800101
    temporary_fsync = bug != "skips_temporary_archive_fsync"

    if step["race_create"] and not step["overwrite"]:
        race_wins = bug == "clobbers_racing_target"
        return {
            "status": "ok" if race_wins else "FileExistsError",
            "events_consumed": event_count,
            "metadata_snapshot": metadata_snapshot,
            "event_snapshot": event_snapshot,
            "nested_snapshot": nested_snapshot,
            "target": "archive" if race_wins else "rival",
            "temporary_exists": False,
            "candidate_archive": {
                "mapping_order": mapping_order,
                "member_order": ["metadata.json", "events.jsonl"],
                "timestamp": timestamp,
            },
            "durability": {
                "temporary_fsync": temporary_fsync,
                "parent_fsync_attempted": race_wins
                and bug != "skips_parent_directory_fsync",
            },
        }

    return {
        "status": "ok",
        "events_consumed": event_count,
        "metadata_snapshot": metadata_snapshot,
        "event_snapshot": event_snapshot,
        "nested_snapshot": nested_snapshot,
        "target": "archive",
        "temporary_exists": False,
        "archive": {
            "mapping_order": mapping_order,
            "member_order": ["metadata.json", "events.jsonl"],
            "timestamp": timestamp,
        },
        "durability": {
            "temporary_fsync": temporary_fsync,
            "parent_fsync_attempted": bug != "skips_parent_directory_fsync",
            "parent_fsync_error_ignored": step["directory_fsync"] == "unsupported",
        },
    }


def _simulate_replay(step: dict[str, object], bug: str) -> dict[str, object]:
    execution_count = 40
    outcomes: list[dict[str, object]] = []
    call_history: list[bool] = []
    call_start_counts: list[int] = []
    status = "ok"
    for index, (recorded, actual) in enumerate(
        zip(step["recorded_success"], step["actual_results"]),
        start=1,
    ):
        call_start_counts.append(execution_count)
        call_history.append(True if bug == "hardcodes_store_history" else bool(step["store_history"]))
        execution_count += 1
        if actual == "raise":
            status = "RuntimeError"
            break
        success = bool(recorded) if bug == "uses_recorded_replay_result" else actual == "success"
        outcomes.append({"seq": index, "success": success})
        if not step["store_history"] and bug != "leaks_history_state":
            execution_count = 40
        should_stop = bool(step["stop_on_error"])
        if bug == "ignores_stop_on_error":
            should_stop = False
        elif bug == "always_stops_on_failure":
            should_stop = True
        if should_stop and not success:
            break
    if not step["store_history"] and bug != "leaks_history_state":
        execution_count = 40
    return {
        "status": status,
        "outcomes": outcomes,
        "store_history_calls": call_history,
        "call_start_counts": call_start_counts,
        "final_execution_count": execution_count,
    }


def _effective_bug(variant: str, step: dict[str, object]) -> str:
    if not variant:
        return ""
    case_id = VARIANT_TO_CASE[variant]
    if variant == case_id:
        return case_id
    if step["op"] == "save":
        metadata = set(step["metadata_features"])
        events = set(step["event_features"])
        secondary_conditions = {
            "consumes_despite_existing_target__metadata": (
                step["target"] == "existing"
                and not step["overwrite"]
                and "mutates_during_iteration" in metadata
            ),
            "snapshots_metadata_after_events__nested": (
                "mutates_during_iteration" in metadata and "nested_mapping" in metadata
            ),
            "late_event_snapshot__three_events": (
                "mutates_after_yield" in events and int(step["event_count"]) >= 3
            ),
            "skips_recursive_mapping_normalization__ba": (
                "nested_mapping" in metadata and step["mapping_order"] == "ba"
            ),
            "overconsumes_event_limit__existing": (
                int(step["event_count"]) > 1_000
                and step["target"] == "existing"
                and bool(step["overwrite"])
            ),
            "deletes_target_on_validation_failure__missing": (
                step["target"] == "missing" and "validation" in step["faults"]
            ),
            "deletes_target_on_iteration_failure__missing": (
                step["target"] == "missing" and "iteration" in step["faults"]
            ),
            "deletes_target_on_serialization_failure__missing": (
                step["target"] == "missing" and "serialization" in step["faults"]
            ),
            "deletes_target_on_member_size_failure__missing": (
                step["target"] == "missing" and "member_size" in step["faults"]
            ),
            "leaves_temp_file__missing": (
                step["target"] == "missing" and "replace" in step["faults"]
            ),
            "deletes_target_on_validation_failure__empty": (
                step["target"] == "existing"
                and bool(step["overwrite"])
                and int(step["event_count"]) == 0
                and "validation" in step["faults"]
            ),
            "deletes_target_on_iteration_failure__late_events": (
                step["target"] == "existing"
                and bool(step["overwrite"])
                and int(step["event_count"]) >= 3
                and "mutates_after_yield" in events
                and "iteration" in step["faults"]
            ),
            "deletes_target_on_serialization_failure__nested_mapping": (
                step["target"] == "existing"
                and bool(step["overwrite"])
                and "nested_mapping" in metadata
                and step["mapping_order"] == "ba"
                and "serialization" in step["faults"]
            ),
            "deletes_target_on_member_size_failure__boundary": (
                step["target"] == "existing"
                and bool(step["overwrite"])
                and int(step["event_count"]) == 1_000
                and "member_size" in step["faults"]
            ),
            "leaves_temp_file__noncanonical_archive": (
                step["target"] == "existing"
                and bool(step["overwrite"])
                and step["mapping_order"] == "ba"
                and "replace" in step["faults"]
            ),
            "clobbers_racing_target__metadata": (
                bool(step["race_create"])
                and "mutates_during_iteration" in metadata
            ),
            "preserves_mapping_insertion_order__nested": (
                step["mapping_order"] == "ba" and "nested_mapping" in metadata
            ),
            "skips_temporary_archive_fsync__overwrite": (
                step["target"] == "existing"
                and bool(step["overwrite"])
                and not step["faults"]
                and int(step["event_count"]) <= 1_000
            ),
            "skips_parent_directory_fsync__unsupported": (
                step["directory_fsync"] == "unsupported"
                and not step["faults"]
                and int(step["event_count"]) <= 1_000
            ),
        }
        if variant in {
            "deletes_target_on_validation_failure__missing",
            "deletes_target_on_iteration_failure__missing",
            "deletes_target_on_serialization_failure__missing",
            "deletes_target_on_member_size_failure__missing",
            "leaves_temp_file__missing",
        }:
            return variant if secondary_conditions.get(variant, False) else ""
        return case_id if secondary_conditions.get(variant, False) else ""

    recorded = list(step["recorded_success"])
    actual = list(step["actual_results"])
    secondary_conditions = {
        "uses_recorded_replay_result__inverse": any(
            recorded_value is False and actual_value == "success"
            for recorded_value, actual_value in zip(recorded, actual)
        ),
        "ignores_stop_on_error__recorded_false": (
            bool(step["stop_on_error"])
            and any(
                actual_value == "failure"
                and recorded[index] is False
                and index + 1 < len(actual)
                for index, actual_value in enumerate(actual)
            )
        ),
        "always_stops_on_failure__history_true": (
            not step["stop_on_error"]
            and bool(step["store_history"])
            and any(
                actual_value == "failure" and index + 1 < len(actual)
                for index, actual_value in enumerate(actual)
            )
        ),
        "hardcodes_store_history__raise": (
            not step["store_history"] and "raise" in actual
        ),
        "leaks_history_state__raise": (
            not step["store_history"] and "raise" in actual
        ),
    }
    return case_id if secondary_conditions.get(variant, False) else ""


def _simulate_step(step: dict[str, object], bug: str = "") -> dict[str, object]:
    if bug and bug not in MUTANT_VARIANTS:
        raise ValueError("unknown_mutant")
    effective_bug = _effective_bug(bug, step)
    if step["op"] == "save":
        return _simulate_save(step, effective_bug)
    return _simulate_replay(step, effective_bug)


def _resolve_path(value: object, path: str) -> object:
    current = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise KeyError(path)
        current = current[part]
    return current


def _json_equal(left: object, right: object) -> bool:
    return json.dumps(
        left,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) == json.dumps(
        right,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _detail(mutant_id: str) -> dict[str, str]:
    label, category = CASE_DETAILS[mutant_id]
    return {
        "case_id": mutant_id,
        "label": label,
        "category": category,
        "category_label": CATEGORY_LABELS[category],
    }


def _score_details(killed: set[str]) -> list[dict[str, object]]:
    return [
        {
            "id": mutant_id,
            "label": CASE_DETAILS[mutant_id][0],
            "points": 1 if mutant_id in killed else 0,
            "max_points": 1,
            "passed": mutant_id in killed,
        }
        for mutant_id in MUTANT_IDS
    ]


def _category_counts(killed: set[str]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for category, label in CATEGORY_LABELS.items():
        members = [
            mutant_id
            for mutant_id in MUTANT_IDS
            if CASE_DETAILS[mutant_id][1] == category
        ]
        result[category] = {
            "label": label,
            "score": sum(mutant_id in killed for mutant_id in members),
            "max_score": len(members),
        }
    return result


def _empty_result(status: str, error: str) -> dict[str, object]:
    killed: set[str] = set()
    return {
        "status": status,
        "score": 0,
        "max_score": MAX_SCORE,
        "killed_mutants": [],
        "survived_mutants": list(MUTANT_IDS),
        "killed_by_test": {},
        "killed_variants": [],
        "survived_variants": list(MUTANT_VARIANTS),
        "failure_summary": error,
        "failure_details": [_detail(mutant_id) for mutant_id in MUTANT_IDS],
        "score_details": _score_details(killed),
        "categories": _category_counts(killed),
        "eligible_steps": 0,
        "invalid_steps": [],
    }


def grade_response(response: str) -> dict[str, object]:
    try:
        payload = bounded_json_loads(response, strip_code_fence=True)
    except (json.JSONDecodeError, BoundedJSONError) as exc:
        message = getattr(exc, "msg", str(exc))
        return _empty_result("invalid_json", f"json_decode_error:{message}")
    try:
        tests = _validate_payload(payload)
    except Exception as exc:
        return _empty_result("invalid_schema", f"{type(exc).__name__}:{exc}")

    eligible_by_test: dict[str, list[dict[str, object]]] = {}
    invalid_steps: list[dict[str, object]] = []
    for test in tests:
        name = str(test["name"])
        eligible: list[dict[str, object]] = []
        for index, step in enumerate(test["steps"]):
            reference = _simulate_step(step)
            mismatches: list[str] = []
            for check in step["checks"]:
                path = str(check["path"])
                try:
                    actual = _resolve_path(reference, path)
                except KeyError:
                    mismatches.append(path)
                    continue
                if not _json_equal(actual, check["equals"]):
                    mismatches.append(path)
            if mismatches:
                invalid_steps.append(
                    {"test": name, "step": index, "mismatched_checks": mismatches}
                )
            else:
                eligible.append(step)
        eligible_by_test[name] = eligible
    killed_variants: set[str] = set()
    variant_witness: dict[str, str] = {}
    killed_by_test: dict[str, list[str]] = {
        str(test["name"]): []
        for test in tests
    }
    for variant in MUTANT_VARIANTS:
        for name, steps in eligible_by_test.items():
            detected = False
            for step in steps:
                actual = _simulate_step(copy.deepcopy(step), variant)
                for check in step["checks"]:
                    path = str(check["path"])
                    try:
                        observed = _resolve_path(actual, path)
                    except KeyError:
                        detected = True
                        break
                    if not _json_equal(observed, check["equals"]):
                        detected = True
                        break
                if detected:
                    break
            if detected:
                killed_variants.add(variant)
                variant_witness[variant] = name
                break

    killed = {
        mutant_id
        for mutant_id, variants in VARIANTS_BY_CASE.items()
        if set(variants) <= killed_variants
    }
    for mutant_id in MUTANT_IDS:
        if mutant_id not in killed:
            continue
        for name in sorted({
            variant_witness[variant]
            for variant in VARIANTS_BY_CASE[mutant_id]
        }):
            killed_by_test[name].append(mutant_id)

    survived = [mutant_id for mutant_id in MUTANT_IDS if mutant_id not in killed]
    return {
        "status": "passed" if len(killed) == MAX_SCORE else "semantic_failed",
        "score": len(killed),
        "max_score": MAX_SCORE,
        "killed_mutants": [mutant_id for mutant_id in MUTANT_IDS if mutant_id in killed],
        "survived_mutants": survived,
        "killed_by_test": killed_by_test,
        "killed_variants": [
            variant for variant in MUTANT_VARIANTS if variant in killed_variants
        ],
        "survived_variants": [
            variant for variant in MUTANT_VARIANTS if variant not in killed_variants
        ],
        "failure_summary": "",
        "failure_details": [_detail(mutant_id) for mutant_id in survived],
        "score_details": _score_details(killed),
        "categories": _category_counts(killed),
        "eligible_steps": sum(len(steps) for steps in eligible_by_test.values()),
        "invalid_steps": invalid_steps,
    }


__all__ = ["MAX_SCORE", "MUTANT_IDS", "MUTANT_VARIANTS", "grade_response"]
