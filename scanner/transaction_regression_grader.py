from __future__ import annotations

import copy
import json
from collections.abc import Iterable, Mapping

from .bounded_json import bounded_json_loads


MAX_TESTS = 3
MAX_FRAMES = 8
MAX_OPERATIONS = 4
MAX_DEPENDENCIES = 4
MUTANT_SPECS = (
    ("duplicate_ignores_retry_budget", "重复身份忽略重试预算", "identity"),
    ("conflicting_duplicate_looks_missing", "冲突重复项误报缺失", "identity"),
    ("cycle_marks_dependants", "循环分类扩散到依赖者", "dependency"),
    ("ready_uses_input_order", "就绪帧错误沿用输入顺序", "ordering"),
    ("failed_prepare_leaks_writes", "准备失败泄漏暂存写入", "atomicity"),
    ("intra_frame_version_is_stale", "帧内后续操作读取旧版本", "atomicity"),
    ("read_conflict_precedes_write", "读冲突错误优先于写冲突", "conflict"),
    ("checks_claim_keys", "只读检查错误占用键", "conflict"),
    ("retry_budget_off_by_one", "重试预算多放行一次", "retry"),
    ("absent_delete_keeps_zero_version", "空键删除未递增墓碑版本", "versioning"),
)
MUTANT_SPECS_V2 = MUTANT_SPECS + (
    ("duplicate_treats_ops_as_unordered", "重复身份错误忽略操作顺序", "identity"),
    ("duplicate_after_order_sensitive", "重复身份错误区分依赖顺序", "identity"),
    ("dependant_copies_parent_rejection", "依赖者错误复制父项拒绝原因", "dependency"),
    ("same_wave_dependency_ready", "依赖项错误在同一波次就绪", "dependency"),
    ("prepare_sees_prior_staged", "准备阶段错误观察前序暂存状态", "atomicity"),
    ("failed_prepare_retries", "准备失败错误消耗写重试预算", "retry"),
    ("read_conflict_retries", "读冲突错误消耗写重试预算", "retry"),
    ("retry_reuses_stale_prepare", "重试错误复用旧准备状态", "retry"),
    ("claims_leak_across_waves", "键占用错误泄漏到后续波次", "conflict"),
    ("absent_delete_does_not_claim", "空键删除错误未占用墓碑键", "conflict"),
)
MUTANT_IDS = tuple(spec[0] for spec in MUTANT_SPECS)
MUTANT_IDS_V2 = tuple(spec[0] for spec in MUTANT_SPECS_V2)
MAX_SCORE = len(MUTANT_IDS)
MUTANT_DETAILS = {
    mutant_id: (label, category)
    for mutant_id, label, category in MUTANT_SPECS_V2
}
CATEGORY_LABELS = {
    "identity": "身份归并",
    "dependency": "依赖传播",
    "ordering": "确定性顺序",
    "atomicity": "准备原子性",
    "conflict": "冲突分类",
    "retry": "重试生命周期",
    "versioning": "版本语义",
}


def _operation_key(operation: Mapping[str, object]) -> str:
    return json.dumps(operation, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _same_frame(
    left: tuple[set[str], list[Mapping[str, object]], int],
    right: tuple[set[str], list[Mapping[str, object]], int],
    bug: str,
) -> bool:
    retries_match = left[2] == right[2] or bug == "duplicate_ignores_retry_budget"
    if bug == "duplicate_treats_ops_as_unordered":
        operations_match = sorted(map(_operation_key, left[1])) == sorted(
            map(_operation_key, right[1])
        )
    else:
        operations_match = left[1] == right[1]
    return left[0] == right[0] and operations_match and retries_match


def _cycle_members(
    unresolved: set[str],
    normalized: dict[str, tuple[set[str], list[Mapping[str, object]], int]],
) -> set[str]:
    def reaches(start: str, current: str, seen: set[str]) -> bool:
        if current == start:
            return True
        if current in seen:
            return False
        seen.add(current)
        return any(
            reaches(start, dependency, seen)
            for dependency in normalized[current][0]
            if dependency in unresolved
        )

    return {
        frame_id
        for frame_id in unresolved
        if any(
            reaches(frame_id, dependency, set())
            for dependency in normalized[frame_id][0]
            if dependency in unresolved
        )
    }


def replay_frames(
    frames: Iterable[Mapping[str, object]],
    *,
    bug: str = "",
) -> dict[str, object]:
    if bug and bug not in MUTANT_IDS_V2:
        raise ValueError("unknown bug")

    raw_frames = list(frames)
    raw_after: dict[str, list[tuple[str, ...]]] = {}
    grouped: dict[str, list[tuple[set[str], list[Mapping[str, object]], int]]] = {}
    for raw_frame in raw_frames:
        frame_id = str(raw_frame["id"])
        raw_after.setdefault(frame_id, []).append(tuple(map(str, raw_frame["after"])))
        logical = (
            set(raw_frame["after"]),
            copy.deepcopy(list(raw_frame["ops"])),
            int(raw_frame.get("write_retries", 0)),
        )
        grouped.setdefault(frame_id, []).append(logical)

    normalized: dict[str, tuple[set[str], list[Mapping[str, object]], int]] = {}
    rejected: dict[str, str] = {}
    for frame_id, occurrences in grouped.items():
        first = occurrences[0]
        after_order_conflict = (
            bug == "duplicate_after_order_sensitive"
            and any(item != raw_after[frame_id][0] for item in raw_after[frame_id][1:])
        )
        if after_order_conflict or any(
            not _same_frame(first, occurrence, bug) for occurrence in occurrences[1:]
        ):
            rejected[frame_id] = "conflicting_duplicate"
        else:
            normalized[frame_id] = first

    known_ids = set(normalized) if bug == "conflicting_duplicate_looks_missing" else set(grouped)
    for frame_id, (dependencies, _, _) in normalized.items():
        if any(dependency not in known_ids for dependency in dependencies):
            rejected[frame_id] = "missing_dependency"

    unresolved = set(normalized) - set(rejected)

    def reject_blocked() -> bool:
        blocked = {
            frame_id
            for frame_id in unresolved
            if normalized[frame_id][0] & rejected.keys()
        }
        for frame_id in blocked:
            reason = "rejected_dependency"
            if bug == "dependant_copies_parent_rejection":
                rejected_parents = sorted(normalized[frame_id][0] & rejected.keys())
                reason = rejected[rejected_parents[0]]
            rejected[frame_id] = reason
        unresolved.difference_update(blocked)
        return bool(blocked)

    cycles = _cycle_members(unresolved, normalized)
    if bug == "cycle_marks_dependants" and cycles:
        actual_cycles = set(cycles)

        def reaches_cycle(frame_id: str, seen: set[str]) -> bool:
            if frame_id in actual_cycles:
                return True
            if frame_id in seen:
                return False
            seen.add(frame_id)
            return any(
                dependency in unresolved and reaches_cycle(dependency, seen)
                for dependency in normalized[frame_id][0]
            )

        cycles = {
            frame_id
            for frame_id in unresolved
            if reaches_cycle(frame_id, set())
        }
    for frame_id in cycles:
        rejected[frame_id] = "dependency_cycle"
    unresolved.difference_update(cycles)
    while reject_blocked():
        pass

    state: dict[str, object] = {}
    versions: dict[str, int] = {}
    committed: list[str] = []
    committed_set: set[str] = set()
    retries_used: dict[str, int] = {}
    stale_prepared: dict[
        str,
        tuple[dict[str, object], dict[str, int], set[str], set[str]],
    ] = {}
    leaked_claimed: set[str] = set()

    while unresolved:
        if reject_blocked():
            continue
        if bug == "ready_uses_input_order":
            ready = [
                frame_id
                for frame_id in normalized
                if frame_id in unresolved and normalized[frame_id][0] <= committed_set
            ]
        else:
            ready = sorted(
                frame_id
                for frame_id in unresolved
                if normalized[frame_id][0] <= committed_set
            )
        if bug == "same_wave_dependency_ready" and ready:
            eligible = committed_set | set(ready)
            changed = True
            while changed:
                additions = {
                    frame_id
                    for frame_id in unresolved
                    if normalized[frame_id][0] <= eligible
                } - eligible
                changed = bool(additions)
                eligible.update(additions)
            ready = sorted(unresolved & eligible)
        if not ready:
            for frame_id in sorted(unresolved):
                rejected[frame_id] = "dependency_cycle"
            break

        prepared: dict[str, tuple[dict[str, object], dict[str, int], set[str], set[str]]] = {}
        deferred_preparation: set[str] = set()
        absent_deletes: dict[str, set[str]] = {}
        prepare_state = copy.deepcopy(state)
        prepare_versions = dict(versions)
        for frame_id in ready:
            if bug == "retry_reuses_stale_prepare" and frame_id in stale_prepared:
                prepared[frame_id] = copy.deepcopy(stale_prepared[frame_id])
                continue
            if bug == "prepare_sees_prior_staged":
                staged_state = copy.deepcopy(prepare_state)
                staged_versions = dict(prepare_versions)
            else:
                staged_state = copy.deepcopy(state)
                staged_versions = dict(versions)
            writes: set[str] = set()
            reads: set[str] = set()
            frame_absent_deletes: set[str] = set()
            failed = False
            for operation in normalized[frame_id][1]:
                key = str(operation["key"])
                current_version = (
                    versions.get(key, 0)
                    if bug == "intra_frame_version_is_stale"
                    else staged_versions.get(key, 0)
                )
                if operation["op"] == "check" or "if_version" in operation:
                    reads.add(key)
                version_mismatch = (
                    operation["if_version"] != current_version
                    if operation["op"] == "check"
                    else "if_version" in operation and operation["if_version"] != current_version
                )
                if version_mismatch:
                    failed = True
                    break
                if operation["op"] == "check":
                    continue
                writes.add(key)
                if operation["op"] == "delete" and key not in staged_state:
                    frame_absent_deletes.add(key)
                next_version = current_version + 1
                if (
                    bug == "absent_delete_keeps_zero_version"
                    and operation["op"] == "delete"
                    and key not in staged_state
                ):
                    next_version = current_version
                staged_versions[key] = next_version
                if operation["op"] == "put":
                    staged_state[key] = copy.deepcopy(operation["value"])
                else:
                    staged_state.pop(key, None)
            if failed:
                if bug == "failed_prepare_retries":
                    used = retries_used.get(frame_id, 0)
                    budget = normalized[frame_id][2]
                    if used < budget:
                        retries_used[frame_id] = used + 1
                        deferred_preparation.add(frame_id)
                    else:
                        rejected[frame_id] = "precondition_failed"
                else:
                    rejected[frame_id] = "precondition_failed"
                if bug == "failed_prepare_leaks_writes":
                    for key in writes:
                        versions[key] = staged_versions[key]
                        if key in staged_state:
                            state[key] = copy.deepcopy(staged_state[key])
                        else:
                            state.pop(key, None)
            else:
                prepared[frame_id] = (staged_state, staged_versions, writes, reads)
                absent_deletes[frame_id] = frame_absent_deletes
                if bug == "retry_reuses_stale_prepare":
                    stale_prepared[frame_id] = copy.deepcopy(prepared[frame_id])
                if bug == "prepare_sees_prior_staged":
                    prepare_state = copy.deepcopy(staged_state)
                    prepare_versions = dict(staged_versions)

        claimed = leaked_claimed if bug == "claims_leak_across_waves" else set()
        for frame_id in ready:
            if frame_id not in prepared:
                if frame_id in deferred_preparation:
                    continue
                unresolved.remove(frame_id)
                continue
            staged_state, staged_versions, writes, reads = prepared[frame_id]

            if bug == "read_conflict_precedes_write" and reads & claimed:
                rejected[frame_id] = "read_conflict"
                unresolved.remove(frame_id)
                continue

            if writes & claimed:
                used = retries_used.get(frame_id, 0)
                budget = normalized[frame_id][2]
                can_retry = used < budget
                if bug == "retry_budget_off_by_one":
                    can_retry = used <= budget
                if can_retry:
                    retries_used[frame_id] = used + 1
                    continue
                rejected[frame_id] = "write_conflict"
                unresolved.remove(frame_id)
                continue

            if reads & claimed:
                if bug == "read_conflict_retries":
                    used = retries_used.get(frame_id, 0)
                    budget = normalized[frame_id][2]
                    if used < budget:
                        retries_used[frame_id] = used + 1
                        continue
                rejected[frame_id] = "read_conflict"
                unresolved.remove(frame_id)
                continue

            for key in writes:
                versions[key] = staged_versions[key]
                if key in staged_state:
                    state[key] = copy.deepcopy(staged_state[key])
                else:
                    state.pop(key, None)
            claimed.update(writes)
            if bug == "absent_delete_does_not_claim":
                claimed.difference_update(absent_deletes.get(frame_id, set()))
            if bug == "checks_claim_keys":
                claimed.update(reads)
            committed.append(frame_id)
            committed_set.add(frame_id)
            unresolved.remove(frame_id)

    return {
        "state": {key: state[key] for key in sorted(state)},
        "versions": {key: versions[key] for key in sorted(versions)},
        "committed": committed,
        "rejected": {frame_id: rejected[frame_id] for frame_id in sorted(rejected)},
    }


def _require_non_negative_int(value: object, label: str, maximum: int | None = None) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label}_must_be_non_negative_int")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label}_too_large")
    return value


def _validate_operation(operation: object) -> dict[str, object]:
    if not isinstance(operation, dict):
        raise ValueError("operation_must_be_object")
    op = operation.get("op")
    key = operation.get("key")
    if op not in {"put", "delete", "check"}:
        raise ValueError("unknown_operation")
    if not isinstance(key, str) or not key:
        raise ValueError("operation_key_must_be_non_empty_string")
    allowed = {"op", "key"}
    required = {"op", "key"}
    if op == "put":
        allowed.add("value")
        required.add("value")
    if op == "check":
        allowed.add("if_version")
        required.add("if_version")
    elif "if_version" in operation:
        allowed.add("if_version")
    if set(operation) - allowed or not required <= set(operation):
        raise ValueError("invalid_operation_fields")
    if "if_version" in operation:
        _require_non_negative_int(operation["if_version"], "if_version")
    return copy.deepcopy(operation)


def _validate_frame(frame: object) -> dict[str, object]:
    if not isinstance(frame, dict):
        raise ValueError("frame_must_be_object")
    if set(frame) - {"id", "after", "ops", "write_retries"}:
        raise ValueError("unknown_frame_field")
    frame_id = frame.get("id")
    after = frame.get("after")
    operations = frame.get("ops")
    if not isinstance(frame_id, str) or not frame_id:
        raise ValueError("frame_id_must_be_non_empty_string")
    if not isinstance(after, list) or len(after) > MAX_DEPENDENCIES:
        raise ValueError("invalid_dependencies")
    if any(not isinstance(item, str) or not item for item in after):
        raise ValueError("dependency_must_be_non_empty_string")
    if not isinstance(operations, list) or len(operations) > MAX_OPERATIONS:
        raise ValueError("invalid_operations")
    retries = frame.get("write_retries", 0)
    _require_non_negative_int(retries, "write_retries", 3)
    validated = {
        "id": frame_id,
        "after": list(after),
        "ops": [_validate_operation(operation) for operation in operations],
    }
    if "write_retries" in frame:
        validated["write_retries"] = retries
    return validated


def _validate_payload(payload: object) -> list[dict[str, object]]:
    if not isinstance(payload, dict):
        raise ValueError("payload_must_be_object")
    tests = payload.get("tests")
    if not isinstance(tests, list) or not tests:
        raise ValueError("tests_must_be_non_empty_list")
    if len(tests) > MAX_TESTS:
        raise ValueError("too_many_tests")
    names: set[str] = set()
    validated: list[dict[str, object]] = []
    for test in tests:
        if not isinstance(test, dict) or set(test) != {"name", "frames"}:
            raise ValueError("invalid_test_fields")
        name = test.get("name")
        frames = test.get("frames")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("invalid_test_name")
        if not isinstance(frames, list) or len(frames) > MAX_FRAMES:
            raise ValueError("invalid_frames")
        names.add(name)
        validated.append(
            {
                "name": name,
                "frames": [_validate_frame(frame) for frame in frames],
            }
        )
    return validated


def _canonicalize(value: object) -> object:
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _detail(mutant_id: str) -> dict[str, str]:
    label, category = MUTANT_DETAILS[mutant_id]
    return {
        "case_id": mutant_id,
        "label": label,
        "category": category,
        "category_label": CATEGORY_LABELS[category],
    }


def _empty_score_details(mutant_ids: tuple[str, ...]) -> list[dict[str, object]]:
    return [
        {
            "id": mutant_id,
            "label": MUTANT_DETAILS[mutant_id][0],
            "points": 0,
            "max_points": 1,
            "passed": False,
        }
        for mutant_id in mutant_ids
    ]


def grade_response(
    response: str,
    test_suite: str = "transaction_replay_mutants_v1",
) -> dict[str, object]:
    if test_suite == "transaction_replay_mutants_v1":
        mutant_ids = MUTANT_IDS
    elif test_suite == "transaction_replay_mutants_v2":
        mutant_ids = MUTANT_IDS_V2
    else:
        raise ValueError("unknown_test_suite")
    try:
        payload = bounded_json_loads(response, strip_code_fence=True)
        tests = _validate_payload(payload)
    except Exception as exc:
        return {
            "status": "invalid_test_cases",
            "score": 0,
            "max_score": len(mutant_ids),
            "killed_mutants": [],
            "survived_mutants": list(mutant_ids),
            "killed_by_test": {},
            "categories": {},
            "failure_details": [_detail(mutant_id) for mutant_id in mutant_ids],
            "score_details": _empty_score_details(mutant_ids),
            "failure_summary": f"{type(exc).__name__}:{exc}",
        }

    killed: set[str] = set()
    killed_by_test: dict[str, list[str]] = {}
    for test in tests:
        name = str(test["name"])
        expected = _canonicalize(replay_frames(copy.deepcopy(test["frames"])))
        local_kills: list[str] = []
        for mutant_id in mutant_ids:
            if mutant_id in killed:
                continue
            try:
                actual = replay_frames(copy.deepcopy(test["frames"]), bug=mutant_id)
            except Exception as exc:
                actual = {"__exception__": type(exc).__name__, "message": str(exc)}
            if _canonicalize(actual) != expected:
                killed.add(mutant_id)
                local_kills.append(mutant_id)
        killed_by_test[name] = local_kills

    survived = [mutant_id for mutant_id in mutant_ids if mutant_id not in killed]
    categories: dict[str, dict[str, int | str]] = {}
    for mutant_id in mutant_ids:
        category = MUTANT_DETAILS[mutant_id][1]
        bucket = categories.setdefault(
            category,
            {"label": CATEGORY_LABELS[category], "score": 0, "max_score": 0},
        )
        bucket["max_score"] = int(bucket["max_score"]) + 1
        if mutant_id in killed:
            bucket["score"] = int(bucket["score"]) + 1

    score = len(killed)
    return {
        "status": "passed" if score == len(mutant_ids) else "semantic_failed",
        "score": score,
        "max_score": len(mutant_ids),
        "killed_mutants": [mutant_id for mutant_id in mutant_ids if mutant_id in killed],
        "survived_mutants": survived,
        "killed_by_test": killed_by_test,
        "categories": categories,
        "failure_details": [_detail(mutant_id) for mutant_id in survived],
        "score_details": [
            {
                "id": mutant_id,
                "label": MUTANT_DETAILS[mutant_id][0],
                "points": 1 if mutant_id in killed else 0,
                "max_points": 1,
                "passed": mutant_id in killed,
            }
            for mutant_id in mutant_ids
        ],
        "failure_summary": "",
    }


__all__ = ["MAX_SCORE", "MUTANT_IDS", "MUTANT_IDS_V2", "grade_response", "replay_frames"]
