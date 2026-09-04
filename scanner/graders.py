from __future__ import annotations

import ast
import copy
import json
import multiprocessing
import re
import subprocess
import sys
import tempfile
import types
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Callable

from .bounded_json import bounded_json_loads
from .candidate_sandbox import run_sandboxed_candidate_worker


@dataclass
class GradeResult:
    ok: bool
    summary: str
    score: int | None = None
    max_score: int | None = None
    failure_details: list[dict[str, str]] | None = None
    diagnostics: dict[str, object] = field(default_factory=dict)


def grade_answer(text: str, grader: dict[str, object]) -> GradeResult:
    kind = str(grader["kind"])
    if kind == "regex":
        pattern = re.compile(str(grader["pattern"]))
        return GradeResult(ok=bool(pattern.search(text)), summary="regex")
    if kind == "json_exact":
        try:
            actual = json.loads(text)
        except json.JSONDecodeError:
            return GradeResult(ok=False, summary="json_decode_error")
        expected = grader["expected"]
        ok = actual == expected
        diagnostics = {} if ok else {"mismatch_paths": _json_mismatch_paths(expected, actual)}
        return GradeResult(ok=ok, summary="json_exact", diagnostics=diagnostics)
    if kind == "retry_counterexample_design":
        return _grade_retry_counterexample_design(text, grader)
    if kind == "coverage_dual_instance":
        return _grade_coverage_dual_instance(text, grader)
    if kind == "bounded_ci_replan":
        return _grade_bounded_ci_replan(text, grader)
    if kind == "ci_optimality_certificate":
        return _grade_ci_optimality_certificate(text, grader)
    if kind == "ci_adversarial_audit":
        return _grade_ci_adversarial_audit(text, grader)
    if kind == "cache_propagation_certificate":
        return _grade_cache_propagation_certificate(text, grader)
    if kind == "transaction_regression_design":
        return _grade_transaction_regression_design(text, grader)
    if kind == "session_bundle_test_design":
        return _grade_session_bundle_test_design(text, grader)
    if kind == "expression_24":
        ok = _check_expression_24(text, grader)
        return GradeResult(ok=ok, summary="expression_24")
    if kind == "python_function":
        return _grade_python_function(text, grader)
    if kind == "unified_diff_patch":
        return _grade_unified_diff_patch(text, grader)
    if kind == "search_replace_patch":
        return _grade_search_replace_patch(text, grader)
    if kind == "session_bundle_patch":
        return _grade_session_bundle_patch(text, grader)
    if kind == "black_box_regression_proof":
        return _grade_black_box_regression_proof(text, grader)
    if kind == "cross_loop_singleflight_patch":
        return _grade_cross_loop_singleflight_patch(text, grader)
    if kind == "scalar_cross_loop_flight_patch":
        return _grade_cross_loop_singleflight_patch(text, grader)
    if kind == "mutation_test_design":
        return _grade_mutation_test_design(text, grader)
    raise ValueError(f"Unsupported grader kind: {kind}")


def _json_mismatch_paths(expected: object, actual: object, path: str = "$") -> list[str]:
    if isinstance(expected, dict) and isinstance(actual, dict):
        mismatches: list[str] = []
        for key in sorted(set(expected) | set(actual), key=str):
            child_path = f"{path}.{key}"
            if key not in expected or key not in actual:
                mismatches.append(child_path)
                continue
            mismatches.extend(_json_mismatch_paths(expected[key], actual[key], child_path))
        return mismatches

    if isinstance(expected, list) and isinstance(actual, list):
        mismatches = []
        for index in range(max(len(expected), len(actual))):
            child_path = f"{path}[{index}]"
            if index >= len(expected) or index >= len(actual):
                mismatches.append(child_path)
                continue
            mismatches.extend(_json_mismatch_paths(expected[index], actual[index], child_path))
        return mismatches

    return [] if expected == actual else [path]


_RETRY_COUNTEREXAMPLE_STATUSES = {"failed", "timeout", "succeeded", "cancelled"}
_RETRY_COUNTEREXAMPLE_RECORD_FIELDS = {
    "job_id",
    "group",
    "revision",
    "status",
    "attempt",
    "ready_at",
    "priority",
}
_RETRY_COUNTEREXAMPLE_PARAM_FIELDS = {
    "now",
    "max_attempts",
    "global_limit",
    "per_group_limit",
}
_RETRY_COUNTEREXAMPLE_MUTANTS_V1 = (
    "latest_revision",
    "equal_revision_last",
    "terminal_suppression",
    "attempt_boundary",
    "ready_boundary_and_null",
    "priority_order",
    "per_group_limit",
    "quota_backfill",
    "next_ready_filter",
    "next_ready_minimum",
)
_RETRY_COUNTEREXAMPLE_MUTANTS_V2 = (
    "latest_revision_ready_only",
    "equal_revision_high_priority",
    "terminal_same_revision_sticky",
    "timeout_attempt_boundary",
    "deferred_ready_only",
    "deferred_priority_order",
    "global_limit_before_readiness",
    "quota_backfill",
    "next_ready_filter",
    "next_ready_minimum",
)
_RETRY_COUNTEREXAMPLE_MUTANTS_V3 = _RETRY_COUNTEREXAMPLE_MUTANTS_V2 + (
    "older_terminal_sticky",
    "equal_revision_first_record",
    "latest_group_from_first_record",
    "quota_consumed_by_unready",
    "next_ready_from_physical_records",
    "priority_absolute_value",
    "revision_tiebreak_low_first",
    "job_id_tiebreak_high_first",
    "zero_limits_as_one",
    "ready_at_zero_as_null",
)
_RETRY_COUNTEREXAMPLE_DETAILS = {
    "latest_revision": ("最高版本归并", "state", "状态归并"),
    "equal_revision_last": ("同版本物理末项", "state", "状态归并"),
    "terminal_suppression": ("终态抑制旧失败", "state", "状态归并"),
    "attempt_boundary": ("重试次数边界", "eligibility", "执行资格"),
    "ready_boundary_and_null": ("就绪时间边界", "eligibility", "执行资格"),
    "priority_order": ("优先级顺序", "ordering", "排序"),
    "per_group_limit": ("分组容量", "capacity", "容量"),
    "latest_revision_ready_only": ("归并前错误过滤", "state", "状态归并"),
    "equal_revision_high_priority": ("同版本错误决胜", "state", "状态归并"),
    "terminal_same_revision_sticky": ("同版本终态粘滞", "state", "状态归并"),
    "timeout_attempt_boundary": ("超时次数边界", "eligibility", "执行资格"),
    "deferred_ready_only": ("延后集合完整性", "scheduling", "调度"),
    "deferred_priority_order": ("延后集合排序", "ordering", "排序"),
    "global_limit_before_readiness": ("就绪前错误截断", "capacity", "容量"),
    "quota_backfill": ("配额跳过后回填", "capacity", "容量"),
    "next_ready_filter": ("唤醒时间过滤", "scheduling", "调度"),
    "next_ready_minimum": ("最早唤醒时间", "scheduling", "调度"),
    "older_terminal_sticky": ("旧终态错误压制新失败", "state", "状态归并"),
    "equal_revision_first_record": ("同版本错误保留首项", "state", "状态归并"),
    "latest_group_from_first_record": ("最新任务沿用旧分组", "state", "状态归并"),
    "quota_consumed_by_unready": ("未就绪任务消耗配额", "capacity", "容量"),
    "next_ready_from_physical_records": ("从物理记录计算唤醒时间", "scheduling", "调度"),
    "priority_absolute_value": ("优先级错误取绝对值", "ordering", "排序"),
    "revision_tiebreak_low_first": ("版本决胜方向错误", "ordering", "排序"),
    "job_id_tiebreak_high_first": ("任务 ID 决胜方向错误", "ordering", "排序"),
    "zero_limits_as_one": ("零容量错误提升为一", "capacity", "容量"),
    "ready_at_zero_as_null": ("零时刻错误视为空值", "eligibility", "执行资格"),
}
_COVERAGE_ENTRY_FIELDS = {
    "budget",
    "greedy",
    "greedy_score",
    "optimal",
    "optimal_score",
    "gap",
}
_COVERAGE_REFERENCE = {
    "frontier": [
        {
            "budget": 13,
            "greedy": ["A", "B", "C", "G", "H"],
            "greedy_score": 84,
            "optimal": ["A", "G", "H", "I", "K"],
            "optimal_score": 95,
            "gap": 11,
        },
        {
            "budget": 7,
            "greedy": ["A", "B", "G"],
            "greedy_score": 57,
            "optimal": ["A", "E", "G"],
            "optimal_score": 67,
            "gap": 10,
        },
    ],
    "mirror": [
        {
            "budget": 6,
            "greedy": ["A", "F", "H", "I"],
            "greedy_score": 54,
            "optimal": ["A", "F", "G", "I"],
            "optimal_score": 80,
            "gap": 26,
        },
        {
            "budget": 9,
            "greedy": ["A", "F", "G", "H", "I"],
            "greedy_score": 80,
            "optimal": ["A", "D", "F", "G", "I"],
            "optimal_score": 91,
            "gap": 11,
        },
    ],
}

_BOUNDED_CI_REPLAN_ENTRY_FIELDS = {
    "remove",
    "add",
    "selected",
    "runtime",
    "normal",
    "critical_addition",
    "fallback",
}
_BOUNDED_CI_REPLAN_COVERAGE_FIELDS = {"modules", "score"}
_BOUNDED_CI_REPLAN_REFERENCE = {
    "alpha": {
        "remove": ["A", "B"],
        "add": ["K"],
        "selected": ["C", "D", "G", "K"],
        "runtime": 15,
        "normal": {
            "modules": ["auth", "billing", "cache", "catalog", "docs", "search", "ui"],
            "score": 81,
        },
        "critical_addition": "K",
        "fallback": {
            "modules": ["auth", "billing", "cache", "catalog", "search", "ui"],
            "score": 76,
        },
    },
    "beta": {
        "remove": ["C", "D"],
        "add": ["H", "J"],
        "selected": ["A", "B", "G", "H", "J"],
        "runtime": 13,
        "normal": {
            "modules": ["audit", "auth", "billing", "docs", "email", "export", "search", "ui"],
            "score": 86,
        },
        "critical_addition": "J",
        "fallback": {
            "modules": ["audit", "auth", "billing", "docs", "email", "search", "ui"],
            "score": 79,
        },
    },
}

_CI_OPTIMALITY_COMPARISON_IDS = ("B", "C", "D", "E", "F")
_CI_OPTIMALITY_COUNTERFACTUAL_IDS = (
    "private_b_public_d",
    "private_a_public_d",
    "private_e_public_f",
    "public_b_public_c",
    "private_b_public_c",
)
_CI_OPTIMALITY_REFERENCE = {
    "comparisons": {
        "B": {"field": "fallback", "winner": "A", "a_value": 11, "other_value": 10},
        "C": {"field": "normal", "winner": "A", "a_value": 26, "other_value": 20},
        "D": {"field": "makespan", "winner": "A", "a_value": 7, "other_value": 8},
        "E": {"field": "cost", "winner": "A", "a_value": 17, "other_value": 18},
        "F": {
            "field": "lexicographic",
            "winner": "A",
            "a_value": ["P", "T", "U"],
            "other_value": ["R", "T", "Z"],
        },
    },
    "counterfactuals": {
        "private_b_public_d": {
            "dirty": ["B", "D", "F", "G", "H"],
            "winner": "D",
            "runner_up": "C",
            "field": "fallback",
            "winner_value": 15,
            "runner_up_value": 12,
        },
        "private_a_public_d": {
            "dirty": ["A", "D", "F", "G", "H"],
            "winner": "B",
            "runner_up": "A",
            "field": "cost",
            "winner_value": 16,
            "runner_up_value": 17,
        },
        "private_e_public_f": {
            "dirty": ["E", "F", "H"],
            "winner": "F",
            "runner_up": "D",
            "field": "makespan",
            "winner_value": 7,
            "runner_up_value": 8,
        },
        "public_b_public_c": {
            "dirty": ["B", "C", "D", "E", "F", "H"],
            "winner": "C",
            "runner_up": "E",
            "field": "fallback",
            "winner_value": 28,
            "runner_up_value": 21,
        },
        "private_b_public_c": {
            "dirty": ["B", "C", "E", "F", "H"],
            "winner": "E",
            "runner_up": "D",
            "field": "makespan",
            "winner_value": 7,
            "runner_up_value": 8,
        },
    },
}


def _is_plain_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _strict_json_equal(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(actual) == set(expected) and all(
            _strict_json_equal(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _strict_json_equal(actual_item, expected_item)
            for actual_item, expected_item in zip(actual, expected)
        )
    return actual == expected


def _score_component(component_id: str, label: str, passed: bool) -> dict[str, object]:
    return {
        "id": component_id,
        "label": label,
        "points": 1 if passed else 0,
        "max_points": 1,
        "passed": passed,
    }


def _semantic_json_result(
    *,
    test_suite: str,
    components: list[dict[str, object]],
    pass_threshold: int,
    status_override: str | None = None,
    failure_summary: str | None = None,
) -> GradeResult:
    score = sum(int(component["points"]) for component in components)
    max_score = len(components)
    failures = [component for component in components if not component["passed"]]
    status = status_override or ("passed" if score == max_score else "semantic_failed")
    summary = f"{test_suite} {score}/{max_score}"
    if failure_summary:
        summary += f"; {failure_summary}"
    elif failures:
        summary += "; failed=" + ",".join(str(item["id"]) for item in failures[:4])
    return GradeResult(
        ok=score >= pass_threshold,
        summary=summary,
        score=score,
        max_score=max_score,
        failure_details=[
            {
                "case_id": str(item["id"]),
                "label": str(item["label"]),
                "category": "semantic_component",
                "category_label": "得分点",
            }
            for item in failures
        ],
        diagnostics={
            "status": status,
            "semantic_passed": score,
            "semantic_total": max_score,
            "failed_components": [str(item["id"]) for item in failures],
            "failure_summary": failure_summary or "",
            "score_details": components,
        },
    )


def _validate_retry_counterexamples(payload: object) -> list[dict[str, object]]:
    if not isinstance(payload, dict) or set(payload) != {"counterexamples"}:
        raise ValueError("invalid_top_level_shape")
    cases = payload["counterexamples"]
    if not isinstance(cases, list) or not cases:
        raise ValueError("counterexamples_must_be_non_empty_list")
    if len(cases) > 3:
        raise ValueError("too_many_counterexamples")

    validated: list[dict[str, object]] = []
    for case in cases:
        if not isinstance(case, dict) or set(case) != {"name", "records", "params"}:
            raise ValueError("invalid_counterexample_shape")
        name = case["name"]
        records = case["records"]
        params = case["params"]
        if not isinstance(name, str) or not name:
            raise ValueError("invalid_counterexample_name")
        if not isinstance(records, list) or len(records) > 16:
            raise ValueError("records_must_have_at_most_16_items")
        if not isinstance(params, dict) or set(params) != _RETRY_COUNTEREXAMPLE_PARAM_FIELDS:
            raise ValueError("invalid_params_shape")
        for record in records:
            _validate_retry_counterexample_record(record)
        _validate_retry_counterexample_params(params)
        validated.append(copy.deepcopy(case))
    return validated


def _validate_retry_counterexample_record(payload: object) -> None:
    if not isinstance(payload, dict) or set(payload) != _RETRY_COUNTEREXAMPLE_RECORD_FIELDS:
        raise ValueError("invalid_record_shape")
    for key in ("job_id", "group"):
        value = payload[key]
        if not isinstance(value, str) or not value:
            raise ValueError(f"invalid_{key}")
    if payload["status"] not in _RETRY_COUNTEREXAMPLE_STATUSES:
        raise ValueError("invalid_status")
    for key in ("revision", "attempt", "priority"):
        if not _is_plain_int(payload[key]):
            raise ValueError(f"{key}_must_be_int")
    if int(payload["revision"]) < 0 or int(payload["attempt"]) < 0:
        raise ValueError("revision_and_attempt_must_be_non_negative")
    ready_at = payload["ready_at"]
    if ready_at is not None and not _is_plain_int(ready_at):
        raise ValueError("ready_at_must_be_int_or_null")


def _validate_retry_counterexample_params(payload: dict[str, object]) -> None:
    for key in _RETRY_COUNTEREXAMPLE_PARAM_FIELDS:
        if not _is_plain_int(payload[key]):
            raise ValueError(f"{key}_must_be_int")
    for key in ("max_attempts", "global_limit", "per_group_limit"):
        if int(payload[key]) < 0:
            raise ValueError(f"{key}_must_be_non_negative")


def _retry_latest_records(
    records: list[dict[str, object]],
    *,
    bug: str = "",
) -> list[dict[str, object]]:
    latest: dict[str, tuple[int, dict[str, object]]] = {}
    for index, record in enumerate(records):
        if bug == "terminal_suppression" and record["status"] not in {"failed", "timeout"}:
            continue
        job_id = str(record["job_id"])
        current = latest.get(job_id)
        if current is None:
            latest[job_id] = (index, record)
            continue
        if bug == "latest_revision":
            continue
        current_index, current_record = current
        revision = int(record["revision"])
        current_revision = int(current_record["revision"])
        if revision > current_revision:
            latest[job_id] = (index, record)
        elif revision == current_revision:
            if bug == "equal_revision_last":
                continue
            if bug == "equal_revision_high_priority":
                priority = int(record["priority"])
                current_priority = int(current_record["priority"])
                if priority > current_priority or (
                    priority == current_priority and index > current_index
                ):
                    latest[job_id] = (index, record)
                continue
            if bug == "terminal_same_revision_sticky":
                terminal = record["status"] in {"succeeded", "cancelled"}
                current_terminal = current_record["status"] in {"succeeded", "cancelled"}
                if terminal and not current_terminal:
                    latest[job_id] = (index, record)
                elif terminal == current_terminal and index > current_index:
                    latest[job_id] = (index, record)
                continue
            if index > current_index:
                latest[job_id] = (index, record)
    return [item[1] for item in latest.values()]


def _retry_record_is_retryable(
    record: dict[str, object],
    params: dict[str, object],
    *,
    bug: str = "",
) -> bool:
    if record["status"] not in {"failed", "timeout"}:
        return False
    attempt = int(record["attempt"])
    max_attempts = int(params["max_attempts"])
    if bug == "attempt_boundary":
        return attempt <= max_attempts
    if bug == "timeout_attempt_boundary" and record["status"] == "timeout":
        return attempt <= max_attempts
    return attempt < max_attempts


def _retry_record_is_ready(
    record: dict[str, object],
    params: dict[str, object],
    *,
    bug: str = "",
) -> bool:
    ready_at = record["ready_at"]
    now = int(params["now"])
    if bug == "ready_boundary_and_null":
        return ready_at is not None and int(ready_at) < now
    return ready_at is None or int(ready_at) <= now


def _retry_plan_variant(
    records: list[dict[str, object]],
    params: dict[str, object],
    *,
    bug: str = "",
) -> dict[str, object]:
    merge_source = records
    if bug == "latest_revision_ready_only":
        merge_source = [
            record
            for record in records
            if _retry_record_is_retryable(record, params)
            and _retry_record_is_ready(record, params)
        ]
    latest = _retry_latest_records(merge_source, bug=bug)
    retryable = [
        record
        for record in latest
        if _retry_record_is_retryable(record, params, bug=bug)
    ]
    global_limit = int(params["global_limit"])
    rank_key = lambda record: (
        -int(record["priority"]),
        -int(record["revision"]),
        str(record["job_id"]),
    )
    if bug == "priority_order":
        ready = [
            record
            for record in retryable
            if _retry_record_is_ready(record, params, bug=bug)
        ]
        ready.sort(
            key=lambda record: (
                int(record["priority"]),
                int(record["revision"]),
                str(record["job_id"]),
            )
        )
    elif bug == "global_limit_before_readiness":
        ordered_retryable = sorted(retryable, key=rank_key)
        ready = [
            record
            for record in ordered_retryable[:global_limit]
            if _retry_record_is_ready(record, params)
        ]
    else:
        ready = [
            record
            for record in retryable
            if _retry_record_is_ready(record, params, bug=bug)
        ]
        ready.sort(key=rank_key)

    per_group_limit = int(params["per_group_limit"])
    selected: list[str] = []
    group_counts: dict[str, int] = {}
    candidates = ready[:global_limit] if bug == "quota_backfill" else ready
    for record in candidates:
        if len(selected) >= global_limit:
            break
        group = str(record["group"])
        if bug != "per_group_limit" and group_counts.get(group, 0) >= per_group_limit:
            continue
        selected.append(str(record["job_id"]))
        group_counts[group] = group_counts.get(group, 0) + 1

    selected_set = set(selected)
    deferred_source = ready if bug == "deferred_ready_only" else retryable
    deferred_records = [
        record
        for record in deferred_source
        if str(record["job_id"]) not in selected_set
    ]
    if bug == "deferred_priority_order":
        deferred = [
            str(record["job_id"])
            for record in sorted(deferred_records, key=rank_key)
        ]
    else:
        deferred = sorted(str(record["job_id"]) for record in deferred_records)
    wakeup_source = latest if bug == "next_ready_filter" else retryable
    future_times = [
        int(record["ready_at"])
        for record in wakeup_source
        if record["ready_at"] is not None and int(record["ready_at"]) > int(params["now"])
    ]
    if not future_times:
        next_ready_at: int | None = None
    elif bug == "next_ready_minimum":
        next_ready_at = max(future_times)
    else:
        next_ready_at = min(future_times)
    return {
        "selected": selected,
        "deferred": deferred,
        "next_ready_at": next_ready_at,
    }


def _retry_latest_first_on_equal(
    records: list[dict[str, object]],
) -> list[dict[str, object]]:
    latest: dict[str, dict[str, object]] = {}
    for record in records:
        job_id = str(record["job_id"])
        current = latest.get(job_id)
        if current is None or int(record["revision"]) > int(current["revision"]):
            latest[job_id] = record
    return list(latest.values())


def _retry_ordered_selected(
    records: list[dict[str, object]],
    params: dict[str, object],
    *,
    low_revision_first: bool = False,
    high_job_id_first: bool = False,
) -> list[str]:
    latest = _retry_latest_records(records)
    ready = [
        record
        for record in latest
        if _retry_record_is_retryable(record, params)
        and _retry_record_is_ready(record, params)
    ]
    if high_job_id_first:
        ready.sort(key=lambda record: str(record["job_id"]), reverse=True)
        ready.sort(key=lambda record: int(record["revision"]), reverse=True)
        ready.sort(key=lambda record: int(record["priority"]), reverse=True)
    elif low_revision_first:
        ready.sort(key=lambda record: str(record["job_id"]))
        ready.sort(key=lambda record: int(record["revision"]))
        ready.sort(key=lambda record: int(record["priority"]), reverse=True)

    selected: list[str] = []
    group_counts: dict[str, int] = {}
    global_limit = int(params["global_limit"])
    per_group_limit = int(params["per_group_limit"])
    for record in ready:
        if len(selected) >= global_limit:
            break
        group = str(record["group"])
        if group_counts.get(group, 0) >= per_group_limit:
            continue
        selected.append(str(record["job_id"]))
        group_counts[group] = group_counts.get(group, 0) + 1
    return selected


def _retry_extra_variant(
    records: list[dict[str, object]],
    params: dict[str, object],
    bug: str,
) -> dict[str, object]:
    mutated_records = copy.deepcopy(records)
    mutated_params = copy.deepcopy(params)

    if bug == "older_terminal_sticky":
        latest_by_job = {
            str(record["job_id"]): record
            for record in _retry_latest_records(mutated_records)
        }
        for job_id, latest in latest_by_job.items():
            has_older_terminal = any(
                str(record["job_id"]) == job_id
                and int(record["revision"]) < int(latest["revision"])
                and record["status"] in {"succeeded", "cancelled"}
                for record in mutated_records
            )
            if has_older_terminal and latest["status"] in {"failed", "timeout"}:
                latest["status"] = "succeeded"
        return _retry_plan_variant(mutated_records, mutated_params)

    if bug == "equal_revision_first_record":
        return _retry_plan_variant(
            _retry_latest_first_on_equal(mutated_records),
            mutated_params,
        )

    if bug == "latest_group_from_first_record":
        first_group_by_job: dict[str, object] = {}
        for record in mutated_records:
            first_group_by_job.setdefault(str(record["job_id"]), record["group"])
        for record in _retry_latest_records(mutated_records):
            record["group"] = first_group_by_job[str(record["job_id"])]
        return _retry_plan_variant(mutated_records, mutated_params)

    if bug == "quota_consumed_by_unready":
        output = _retry_plan_variant(mutated_records, mutated_params)
        latest = _retry_latest_records(mutated_records)
        retryable = [
            record
            for record in latest
            if _retry_record_is_retryable(record, mutated_params)
        ]
        retryable.sort(
            key=lambda record: (
                -int(record["priority"]),
                -int(record["revision"]),
                str(record["job_id"]),
            )
        )
        selected: list[str] = []
        group_counts: dict[str, int] = {}
        for record in retryable:
            if len(selected) >= int(mutated_params["global_limit"]):
                break
            group = str(record["group"])
            if group_counts.get(group, 0) >= int(mutated_params["per_group_limit"]):
                continue
            group_counts[group] = group_counts.get(group, 0) + 1
            if _retry_record_is_ready(record, mutated_params):
                selected.append(str(record["job_id"]))
        selected_set = set(selected)
        output["selected"] = selected
        output["deferred"] = sorted(
            str(record["job_id"])
            for record in retryable
            if str(record["job_id"]) not in selected_set
        )
        return output

    if bug == "next_ready_from_physical_records":
        output = _retry_plan_variant(mutated_records, mutated_params)
        future_times = [
            int(record["ready_at"])
            for record in mutated_records
            if _retry_record_is_retryable(record, mutated_params)
            and record["ready_at"] is not None
            and int(record["ready_at"]) > int(mutated_params["now"])
        ]
        output["next_ready_at"] = min(future_times) if future_times else None
        return output

    if bug == "priority_absolute_value":
        for record in mutated_records:
            record["priority"] = abs(int(record["priority"]))
        return _retry_plan_variant(mutated_records, mutated_params)

    if bug in {"revision_tiebreak_low_first", "job_id_tiebreak_high_first"}:
        output = _retry_plan_variant(mutated_records, mutated_params)
        output["selected"] = _retry_ordered_selected(
            mutated_records,
            mutated_params,
            low_revision_first=bug == "revision_tiebreak_low_first",
            high_job_id_first=bug == "job_id_tiebreak_high_first",
        )
        return output

    if bug == "zero_limits_as_one":
        for key in ("global_limit", "per_group_limit"):
            if int(mutated_params[key]) == 0:
                mutated_params[key] = 1
        return _retry_plan_variant(mutated_records, mutated_params)

    if bug == "ready_at_zero_as_null":
        for record in mutated_records:
            if record["ready_at"] == 0:
                record["ready_at"] = None
        return _retry_plan_variant(mutated_records, mutated_params)

    raise ValueError(f"unknown extra mutant: {bug}")


def _retry_mutant_output(
    records: list[dict[str, object]],
    params: dict[str, object],
    mutant_id: str,
) -> dict[str, object]:
    if mutant_id in _RETRY_COUNTEREXAMPLE_MUTANTS_V1 + _RETRY_COUNTEREXAMPLE_MUTANTS_V2:
        return _retry_plan_variant(records, params, bug=mutant_id)
    return _retry_extra_variant(records, params, mutant_id)


def _retry_counterexample_mutants(
    mutant_ids: tuple[str, ...],
) -> list[dict[str, object]]:
    return [
        {
            "id": mutant_id,
            "run": lambda records, params, bug=mutant_id: _retry_mutant_output(
                records, params, bug
            ),
        }
        for mutant_id in mutant_ids
    ]


def _retry_counterexample_detail(mutant_id: str) -> dict[str, str]:
    label, category, category_label = _RETRY_COUNTEREXAMPLE_DETAILS[mutant_id]
    return {
        "case_id": mutant_id,
        "label": label,
        "category": category,
        "category_label": category_label,
    }


def _grade_retry_counterexample_design(
    text: str,
    grader: dict[str, object],
) -> GradeResult:
    test_suite = str(grader.get("test_suite") or "")
    if test_suite == "retry_planner_mutants_v1":
        mutant_ids = _RETRY_COUNTEREXAMPLE_MUTANTS_V1
    elif test_suite == "retry_planner_mutants_v2":
        mutant_ids = _RETRY_COUNTEREXAMPLE_MUTANTS_V2
    elif test_suite == "retry_planner_mutants_v3":
        mutant_ids = _RETRY_COUNTEREXAMPLE_MUTANTS_V3
    else:
        raise ValueError("unknown_test_suite")
    pass_threshold = int(grader.get("pass_threshold", 10))
    try:
        cases = _validate_retry_counterexamples(json.loads(_strip_code_fence(text)))
    except Exception as exc:
        details = [
            _retry_counterexample_detail(mutant_id)
            for mutant_id in mutant_ids
        ]
        return GradeResult(
            ok=False,
            summary=f"{test_suite} invalid_json_or_schema; {type(exc).__name__}:{exc}",
            score=0,
            max_score=len(mutant_ids),
            failure_details=details,
            diagnostics={
                "status": "invalid_counterexamples",
                "semantic_passed": 0,
                "semantic_total": len(mutant_ids),
                "killed_mutants": [],
                "survived_mutants": list(mutant_ids),
                "failure_summary": f"{type(exc).__name__}:{exc}",
                "score_details": [
                    {
                        "id": item["case_id"],
                        "label": item["label"],
                        "points": 0,
                        "max_points": 1,
                        "passed": False,
                    }
                    for item in details
                ],
            },
        )

    killed: set[str] = set()
    for case in cases:
        records = case["records"]
        params = case["params"]
        expected = _retry_plan_variant(copy.deepcopy(records), copy.deepcopy(params))
        for mutant in _retry_counterexample_mutants(mutant_ids):
            mutant_id = str(mutant["id"])
            if mutant_id in killed:
                continue
            got = mutant["run"](copy.deepcopy(records), copy.deepcopy(params))
            if got != expected:
                killed.add(mutant_id)

    survived = [
        mutant_id
        for mutant_id in mutant_ids
        if mutant_id not in killed
    ]
    score = len(killed)
    summary = f"{test_suite} {score}/{len(mutant_ids)}"
    if survived:
        summary += f"; survived={','.join(survived[:4])}"
    return GradeResult(
        ok=score >= pass_threshold,
        summary=summary,
        score=score,
        max_score=len(mutant_ids),
        failure_details=[_retry_counterexample_detail(item) for item in survived],
        diagnostics={
            "status": "passed" if score == len(mutant_ids) else "semantic_failed",
            "semantic_passed": score,
            "semantic_total": len(mutant_ids),
            "killed_mutants": sorted(killed),
            "survived_mutants": survived,
            "failure_summary": ",".join(survived),
            "score_details": [
                {
                    "id": mutant_id,
                    "label": _retry_counterexample_detail(mutant_id)["label"],
                    "points": 1 if mutant_id in killed else 0,
                    "max_points": 1,
                    "passed": mutant_id in killed,
                }
                for mutant_id in mutant_ids
            ],
        },
    )


def _grade_coverage_dual_instance(
    text: str,
    grader: dict[str, object],
) -> GradeResult:
    test_suite = str(grader.get("test_suite") or "")
    if test_suite != "coverage_dual_v1":
        raise ValueError("unknown_test_suite")
    pass_threshold = int(grader.get("pass_threshold", 10))
    component_specs: list[tuple[str, str]] = []
    for repository in ("frontier", "mirror"):
        component_specs.append((f"{repository}.ranked_budgets", f"{repository} 排名预算"))
        for index in (1, 2):
            component_specs.extend(
                [
                    (f"{repository}.rank_{index}.greedy", f"{repository} 第 {index} 名贪心结果"),
                    (
                        f"{repository}.rank_{index}.optimal_and_gap",
                        f"{repository} 第 {index} 名最优解与差距",
                    ),
                ]
            )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        components = [_score_component(item_id, label, False) for item_id, label in component_specs]
        return _semantic_json_result(
            test_suite=test_suite,
            components=components,
            pass_threshold=pass_threshold,
            status_override="invalid_json",
            failure_summary=f"json_decode_error:{exc.msg}",
        )
    if not isinstance(payload, dict):
        components = [_score_component(item_id, label, False) for item_id, label in component_specs]
        return _semantic_json_result(
            test_suite=test_suite,
            components=components,
            pass_threshold=pass_threshold,
            status_override="invalid_schema",
            failure_summary="top_level_not_object",
        )

    exact_top = set(payload) == {"frontier", "mirror"}
    components: list[dict[str, object]] = []
    for repository in ("frontier", "mirror"):
        expected_entries = _COVERAGE_REFERENCE[repository]
        actual_entries = payload.get(repository)
        valid_entries = (
            exact_top
            and isinstance(actual_entries, list)
            and len(actual_entries) == 2
            and all(
                isinstance(entry, dict) and set(entry) == _COVERAGE_ENTRY_FIELDS
                for entry in actual_entries
            )
        )
        budgets_ok = valid_entries and [entry.get("budget") for entry in actual_entries] == [
            entry["budget"] for entry in expected_entries
        ]
        components.append(
            _score_component(
                f"{repository}.ranked_budgets",
                f"{repository} 排名预算",
                budgets_ok,
            )
        )
        for index, expected in enumerate(expected_entries, start=1):
            actual = (
                actual_entries[index - 1]
                if isinstance(actual_entries, list) and len(actual_entries) >= index
                else None
            )
            is_entry = isinstance(actual, dict)
            components.extend(
                [
                    _score_component(
                        f"{repository}.rank_{index}.greedy",
                        f"{repository} 第 {index} 名贪心结果",
                        is_entry
                        and actual.get("greedy") == expected["greedy"]
                        and actual.get("greedy_score") == expected["greedy_score"],
                    ),
                    _score_component(
                        f"{repository}.rank_{index}.optimal_and_gap",
                        f"{repository} 第 {index} 名最优解与差距",
                        is_entry
                        and actual.get("optimal") == expected["optimal"]
                        and actual.get("optimal_score") == expected["optimal_score"]
                        and actual.get("gap") == expected["gap"],
                    ),
                ]
            )
    return _semantic_json_result(
        test_suite=test_suite,
        components=components,
        pass_threshold=pass_threshold,
    )


def _grade_bounded_ci_replan(
    text: str,
    grader: dict[str, object],
) -> GradeResult:
    test_suite = str(grader.get("test_suite") or "")
    if test_suite != "bounded_ci_replan_v1":
        raise ValueError("unknown_test_suite")
    pass_threshold = int(grader.get("pass_threshold", 10))
    component_specs = [
        item
        for scenario in _BOUNDED_CI_REPLAN_REFERENCE
        for item in (
            (f"{scenario}.changes", f"{scenario} 变更"),
            (f"{scenario}.selection_and_runtime", f"{scenario} 选择与耗时"),
            (f"{scenario}.normal", f"{scenario} 正常覆盖"),
            (f"{scenario}.critical_addition", f"{scenario} 关键新增项"),
            (f"{scenario}.fallback", f"{scenario} 回退覆盖"),
        )
    ]

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return _semantic_json_result(
            test_suite=test_suite,
            components=[
                _score_component(component_id, label, False)
                for component_id, label in component_specs
            ],
            pass_threshold=pass_threshold,
            status_override="invalid_json",
            failure_summary=f"json_decode_error:{exc.msg}",
        )

    scenarios = (
        payload.get("scenarios")
        if isinstance(payload, dict) and set(payload) == {"scenarios"}
        else None
    )
    schema_ok = (
        isinstance(scenarios, dict)
        and set(scenarios) == set(_BOUNDED_CI_REPLAN_REFERENCE)
        and all(
            isinstance(scenarios.get(name), dict)
            and set(scenarios[name]) == _BOUNDED_CI_REPLAN_ENTRY_FIELDS
            and isinstance(scenarios[name].get("normal"), dict)
            and set(scenarios[name]["normal"]) == _BOUNDED_CI_REPLAN_COVERAGE_FIELDS
            and isinstance(scenarios[name].get("fallback"), dict)
            and set(scenarios[name]["fallback"]) == _BOUNDED_CI_REPLAN_COVERAGE_FIELDS
            for name in _BOUNDED_CI_REPLAN_REFERENCE
        )
    )

    def coverage_matches(actual: object, expected: dict[str, object]) -> bool:
        if not isinstance(actual, dict):
            return False
        modules = actual.get("modules")
        expected_modules = expected["modules"]
        return (
            isinstance(modules, list)
            and all(isinstance(module, str) for module in modules)
            and isinstance(expected_modules, list)
            and sorted(modules) == expected_modules
            and actual.get("score") == expected["score"]
        )

    components: list[dict[str, object]] = []
    for scenario, expected in _BOUNDED_CI_REPLAN_REFERENCE.items():
        actual = scenarios.get(scenario) if schema_ok else None
        components.extend(
            [
                _score_component(
                    f"{scenario}.changes",
                    f"{scenario} 变更",
                    isinstance(actual, dict)
                    and actual.get("remove") == expected["remove"]
                    and actual.get("add") == expected["add"],
                ),
                _score_component(
                    f"{scenario}.selection_and_runtime",
                    f"{scenario} 选择与耗时",
                    isinstance(actual, dict)
                    and actual.get("selected") == expected["selected"]
                    and actual.get("runtime") == expected["runtime"],
                ),
                _score_component(
                    f"{scenario}.normal",
                    f"{scenario} 正常覆盖",
                    isinstance(actual, dict)
                    and coverage_matches(actual.get("normal"), expected["normal"]),
                ),
                _score_component(
                    f"{scenario}.critical_addition",
                    f"{scenario} 关键新增项",
                    isinstance(actual, dict)
                    and actual.get("critical_addition") == expected["critical_addition"],
                ),
                _score_component(
                    f"{scenario}.fallback",
                    f"{scenario} 回退覆盖",
                    isinstance(actual, dict)
                    and coverage_matches(actual.get("fallback"), expected["fallback"]),
                ),
            ]
        )
    return _semantic_json_result(
        test_suite=test_suite,
        components=components,
        pass_threshold=pass_threshold,
        status_override=None if schema_ok else "invalid_schema",
        failure_summary=None if schema_ok else "invalid_schema",
    )


def _grade_ci_optimality_certificate(
    text: str,
    grader: dict[str, object],
) -> GradeResult:
    test_suite = str(grader.get("test_suite") or "")
    if test_suite != "ci_optimality_certificate_v1":
        raise ValueError("unknown_test_suite")
    pass_threshold = int(grader.get("pass_threshold", 10))
    component_specs = [
        (f"comparison.{plan_id}", f"计划 A 与 {plan_id} 比较")
        for plan_id in _CI_OPTIMALITY_COMPARISON_IDS
    ] + [
        (f"counterfactual.{counterfactual_id}", f"{counterfactual_id} 反事实证书")
        for counterfactual_id in _CI_OPTIMALITY_COUNTERFACTUAL_IDS
    ]

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return _semantic_json_result(
            test_suite=test_suite,
            components=[
                _score_component(component_id, label, False)
                for component_id, label in component_specs
            ],
            pass_threshold=pass_threshold,
            status_override="invalid_json",
            failure_summary=f"json_decode_error:{exc.msg}",
        )

    comparisons = payload.get("comparisons") if isinstance(payload, dict) else None
    counterfactuals = payload.get("counterfactuals") if isinstance(payload, dict) else None
    components = [
        _score_component(
            f"comparison.{plan_id}",
            f"计划 A 与 {plan_id} 比较",
            isinstance(comparisons, dict)
            and _strict_json_equal(
                comparisons.get(plan_id),
                _CI_OPTIMALITY_REFERENCE["comparisons"][plan_id],
            ),
        )
        for plan_id in _CI_OPTIMALITY_COMPARISON_IDS
    ]
    components.extend(
        _score_component(
            f"counterfactual.{counterfactual_id}",
            f"{counterfactual_id} 反事实证书",
            isinstance(counterfactuals, dict)
            and _strict_json_equal(
                counterfactuals.get(counterfactual_id),
                _CI_OPTIMALITY_REFERENCE["counterfactuals"][counterfactual_id],
            ),
        )
        for counterfactual_id in _CI_OPTIMALITY_COUNTERFACTUAL_IDS
    )
    return _semantic_json_result(
        test_suite=test_suite,
        components=components,
        pass_threshold=pass_threshold,
    )


def _grade_ci_adversarial_audit(
    text: str,
    grader: dict[str, object],
) -> GradeResult:
    test_suite = str(grader.get("test_suite") or "")
    if test_suite not in {
        "ci_adversarial_audit_v1",
        "ci_adversarial_audit_v2",
        "ci_adversarial_audit_certificate_v4",
    }:
        raise ValueError("unknown_test_suite")

    if test_suite == "ci_adversarial_audit_certificate_v4":
        from .ci_adversarial_audit_certificate_grader import grade_response

        payload = grade_response(text)
    else:
        from .ci_adversarial_audit_grader import grade_response

        payload = grade_response(text, test_suite)
    score = int(payload["score"])
    max_score = int(payload["max_score"])
    pass_threshold = int(grader.get("pass_threshold", max_score))
    status = str(payload.get("status") or "semantic_failed")
    failure_summary = str(payload.get("failure_summary") or "")
    if status in {"invalid_json", "invalid_schema"}:
        summary = f"{test_suite} {status}"
        if failure_summary:
            summary += f"; {failure_summary}"
    else:
        summary = f"{test_suite} {score}/{max_score}"
        survived = [str(item) for item in payload.get("survived_mutants", [])]
        if survived:
            summary += f"; survived={','.join(survived[:4])}"
    return GradeResult(
        ok=score >= pass_threshold,
        summary=summary,
        score=score,
        max_score=max_score,
        failure_details=[
            {
                "case_id": str(item.get("case_id", "")),
                "label": str(item.get("label", "")),
                "category": str(item.get("category", "")),
                "category_label": str(item.get("category_label", "")),
            }
            for item in payload.get("failure_details", [])
            if isinstance(item, dict)
        ],
        diagnostics={
            "status": status,
            "test_suite": test_suite,
            "semantic_passed": score,
            "semantic_total": max_score,
            "killed_mutants": payload.get("killed_mutants", []),
            "survived_mutants": payload.get("survived_mutants", []),
            "killed_by_test": payload.get("killed_by_test", {}),
            "scenario_count": payload.get("scenario_count", 0),
            "facets": payload.get("categories", {}),
            "certificate_facets": payload.get("certificate_facets", []),
            "score_details": payload.get("score_details", []),
            "failure_summary": failure_summary,
        },
    )


def _grade_cache_propagation_certificate(
    text: str,
    grader: dict[str, object],
) -> GradeResult:
    test_suite = str(grader.get("test_suite") or "")
    if test_suite != "compact_propagation_certificate_v1":
        raise ValueError("unknown_test_suite")

    from .cache_propagation_certificate_grader import (
        BEHAVIOR_GROUPS,
        BEHAVIOR_MUTANTS,
        grade_response,
    )

    payload = grade_response(text)
    grade_state = str(payload.get("status") or "schema_error")
    max_score = int(payload.get("max_score") or 20)
    pass_threshold = int(grader.get("pass_threshold", max_score))
    failure_summary = str(payload.get("failure_summary") or "")
    if grade_state != "scored":
        summary = f"{test_suite} {grade_state}"
        if failure_summary:
            summary += f"; {failure_summary}"
        return GradeResult(
            ok=False,
            summary=summary,
            score=None,
            max_score=max_score,
            diagnostics={
                "status": grade_state,
                "grade_state": grade_state,
                "test_suite": test_suite,
                "configured_max_score": max_score,
                "failure_summary": failure_summary,
                "score_details": [],
            },
        )

    score = int(payload["score"])
    checks = [item for item in payload.get("checks", []) if isinstance(item, dict)]
    failed = [item for item in checks if not bool(item.get("passed"))]
    category_by_check = {
        check_id: group
        for group, check_ids in BEHAVIOR_GROUPS.items()
        for check_id in check_ids
    }
    survived = [mutant for mutant in BEHAVIOR_MUTANTS if mutant in {str(item.get("id")) for item in failed}]
    status = "passed" if score >= pass_threshold else "semantic_failed"
    summary = f"{test_suite} {score}/{max_score}"
    if survived:
        summary += f"; survived={','.join(survived[:4])}"
    return GradeResult(
        ok=score >= pass_threshold,
        summary=summary,
        score=score,
        max_score=max_score,
        failure_details=[
            {
                "case_id": str(item.get("id", "")),
                "label": str(item.get("id", "")),
                "category": category_by_check.get(str(item.get("id", "")), "certificate"),
                "category_label": category_by_check.get(str(item.get("id", "")), "certificate"),
            }
            for item in failed
        ],
        diagnostics={
            "status": status,
            "grade_state": grade_state,
            "test_suite": test_suite,
            "semantic_passed": score,
            "semantic_total": max_score,
            "killed_mutants": [mutant for mutant in BEHAVIOR_MUTANTS if mutant not in survived],
            "survived_mutants": survived,
            "killed_by_test": payload.get("killed_by_evidence", {}),
            "facets": payload.get("facets", {}),
            "certificate_facets": payload.get("certificate_facets", {}),
            "score_details": checks,
            "invalid_cases": payload.get("invalid_cases", []),
            "blocked_by_certificate": payload.get("blocked_by_certificate", {}),
            "blocked_by_interaction": payload.get("blocked_by_interaction", {}),
            "failure_summary": failure_summary,
        },
    )


def _candidate_worker_failure(
    test_suite: str,
    max_score: int,
    status: str,
) -> GradeResult:
    if status.startswith("sandbox_unavailable"):
        return GradeResult(
            ok=False,
            summary=f"{test_suite} grader_unavailable",
            score=None,
            max_score=max_score,
            diagnostics={
                "status": "grader_unavailable",
                "test_suite": test_suite,
                "semantic_passed": None,
                "semantic_total": max_score,
                "failure_summary": "sandbox_unavailable",
            },
        )
    if status == "timeout":
        return GradeResult(
            ok=False,
            summary=f"{test_suite} timeout",
            score=0,
            max_score=max_score,
            diagnostics={
                "status": "timeout",
                "test_suite": test_suite,
                "semantic_passed": 0,
                "semantic_total": max_score,
                "failure_summary": "timeout",
            },
        )
    return GradeResult(
        ok=False,
        summary=f"{test_suite} runner_error",
        score=0,
        max_score=max_score,
        diagnostics={
            "status": "runner_error",
            "test_suite": test_suite,
            "semantic_passed": 0,
            "semantic_total": max_score,
            "failure_summary": "runner_error",
        },
    )


def _grade_python_function(text: str, grader: dict[str, object]) -> GradeResult:
    function_name = str(grader["function_name"])
    test_suite = str(grader["test_suite"])
    timeout_seconds = float(grader.get("timeout_seconds", 3))
    pass_threshold = int(grader.get("pass_threshold", 0))

    source = _strip_code_fence(text)
    try:
        _validate_python_source(source)
    except ValueError as exc:
        return GradeResult(
            ok=False,
            summary=f"{test_suite} 0/0; {exc}",
            score=0,
            max_score=0,
        )

    max_score = _suite_max_score(test_suite)
    payload, status = run_sandboxed_candidate_worker(
        "python_function",
        {
            "source": source,
            "function_name": function_name,
            "test_suite": test_suite,
        },
        timeout_seconds,
    )
    if payload is None:
        return _candidate_worker_failure(test_suite, max_score, status)

    score = int(payload.get("score", 0))
    max_score = int(payload.get("max_score", 0))
    raw_failure_details = payload.get("failure_details", [])
    failure_details = [
        {
            "case_id": str(item.get("case_id", "")),
            "label": str(item.get("label", "")),
            "category": str(item.get("category", "")),
            "category_label": str(item.get("category_label", "")),
        }
        for item in raw_failure_details
        if isinstance(item, dict)
    ]
    failures = [item["case_id"] for item in failure_details]
    if not failures:
        failures = [str(item) for item in payload.get("failures", [])]
    threshold = pass_threshold or max_score
    summary = f"{test_suite} {score}/{max_score}"
    if failures:
        summary += f"; failed={','.join(failures[:4])}"
    if payload.get("error"):
        summary += f"; {payload['error']}"
    return GradeResult(
        ok=score >= threshold,
        summary=summary,
        score=score,
        max_score=max_score,
        failure_details=failure_details,
    )


def _grade_unified_diff_patch(text: str, grader: dict[str, object]) -> GradeResult:
    test_suite = str(grader["test_suite"])
    timeout_seconds = float(grader.get("timeout_seconds", 3))
    pass_threshold = int(grader.get("pass_threshold", 0))

    diff_text = _strip_code_fence(text)
    max_score = _suite_max_score(test_suite)
    payload, status = run_sandboxed_candidate_worker(
        "unified_diff_patch",
        {"diff_text": diff_text, "test_suite": test_suite},
        timeout_seconds,
    )
    if payload is None:
        return _candidate_worker_failure(test_suite, max_score, status)

    score = int(payload.get("score", 0))
    max_score = int(payload.get("max_score", 0))
    raw_failure_details = payload.get("failure_details", [])
    failure_details = [
        {
            "case_id": str(item.get("case_id", "")),
            "label": str(item.get("label", "")),
            "category": str(item.get("category", "")),
            "category_label": str(item.get("category_label", "")),
        }
        for item in raw_failure_details
        if isinstance(item, dict)
    ]
    failures = [item["case_id"] for item in failure_details]
    threshold = pass_threshold or max_score
    diagnostics = _unified_diff_patch_diagnostics(payload, score, max_score, failures)
    status = str(diagnostics.get("status") or "")
    if status == "patch_apply_failed":
        summary = f"{test_suite} patch_apply_failed"
        if diagnostics.get("failure_summary"):
            summary += f"; {diagnostics['failure_summary']}"
    elif status == "timeout":
        summary = f"{test_suite} timeout"
    elif status == "passed":
        summary = f"{test_suite} {score}/{max_score}"
    else:
        summary = f"{test_suite} {score}/{max_score}"
        if failures:
            summary += f"; failed={','.join(failures[:4])}"
        if diagnostics.get("failure_summary"):
            summary += f"; {diagnostics['failure_summary']}"
    return GradeResult(
        ok=score >= threshold,
        summary=summary,
        score=score,
        max_score=max_score,
        failure_details=failure_details,
        diagnostics=diagnostics,
    )


def _grade_search_replace_patch(text: str, grader: dict[str, object]) -> GradeResult:
    test_suite = str(grader["test_suite"])
    timeout_seconds = float(grader.get("timeout_seconds", 3))
    pass_threshold = int(grader.get("pass_threshold", 0))

    patch_text = _strip_code_fence(text)
    max_score = _suite_max_score(test_suite)
    payload, status = run_sandboxed_candidate_worker(
        "search_replace_patch",
        {"patch_text": patch_text, "test_suite": test_suite},
        timeout_seconds,
    )
    if payload is None:
        return _candidate_worker_failure(test_suite, max_score, status)

    score = int(payload.get("score", 0))
    max_score = int(payload.get("max_score", 0))
    raw_failure_details = payload.get("failure_details", [])
    failure_details = [
        {
            "case_id": str(item.get("case_id", "")),
            "label": str(item.get("label", "")),
            "category": str(item.get("category", "")),
            "category_label": str(item.get("category_label", "")),
        }
        for item in raw_failure_details
        if isinstance(item, dict)
    ]
    failures = [item["case_id"] for item in failure_details]
    threshold = pass_threshold or max_score
    diagnostics = _search_replace_patch_diagnostics(payload, score, max_score, failures)
    status = str(diagnostics.get("status") or "")
    if status == "patch_apply_failed":
        summary = f"{test_suite} patch_apply_failed"
        if diagnostics.get("failure_summary"):
            summary += f"; {diagnostics['failure_summary']}"
    elif status == "passed":
        summary = f"{test_suite} {score}/{max_score}"
    else:
        summary = f"{test_suite} {score}/{max_score}"
        if failures:
            summary += f"; failed={','.join(failures[:4])}"
        if diagnostics.get("failure_summary"):
            summary += f"; {diagnostics['failure_summary']}"
    return GradeResult(
        ok=score >= threshold,
        summary=summary,
        score=score,
        max_score=max_score,
        failure_details=failure_details,
        diagnostics=diagnostics,
    )


def _grade_session_bundle_patch(text: str, grader: dict[str, object]) -> GradeResult:
    test_suite = str(grader["test_suite"])
    if test_suite != "compact_session_repair_v1":
        raise ValueError("unknown_test_suite")
    timeout_seconds = float(grader.get("timeout_seconds", 8))
    pass_threshold = int(grader.get("pass_threshold", 0))
    max_score = _suite_max_score(test_suite)

    payload, status = run_sandboxed_candidate_worker(
        "session_bundle_patch",
        {"patch_text": _strip_code_fence(text)},
        timeout_seconds,
    )
    if payload is None:
        return _candidate_worker_failure(test_suite, max_score, status)

    score = int(payload.get("score", 0))
    raw_failure_details = payload.get("failure_details", [])
    failure_details = [
        {
            "case_id": str(item.get("case_id", "")),
            "label": str(item.get("label", "")),
            "category": str(item.get("category", "")),
            "category_label": str(item.get("category_label", "")),
        }
        for item in raw_failure_details
        if isinstance(item, dict)
    ]
    failures = [item["case_id"] for item in failure_details]
    diagnostics = _search_replace_patch_diagnostics(payload, score, max_score, failures)
    diagnostics["facets"] = payload.get("facets", {})
    diagnostics["score_details"] = payload.get("clusters", [])
    diagnostics["raw_score"] = payload.get("raw_score", 0)
    diagnostics["raw_max_score"] = payload.get("raw_max_score", 0)
    status = str(diagnostics.get("status") or "")
    if status == "patch_apply_failed":
        summary = f"{test_suite} patch_apply_failed"
        if diagnostics.get("failure_summary"):
            summary += f"; {diagnostics['failure_summary']}"
    elif status == "runner_error":
        summary = f"{test_suite} runner_error"
        if diagnostics.get("failure_summary"):
            summary += f"; {diagnostics['failure_summary']}"
    else:
        summary = f"{test_suite} {score}/{max_score}"
        if failures:
            summary += f"; failed={','.join(failures[:4])}"
    threshold = pass_threshold or max_score
    return GradeResult(
        ok=score >= threshold,
        summary=summary,
        score=score,
        max_score=max_score,
        failure_details=failure_details,
        diagnostics=diagnostics,
    )


def _grade_black_box_regression_proof(
    text: str,
    grader: dict[str, object],
) -> GradeResult:
    test_suite = str(grader.get("test_suite") or "")
    if test_suite not in {"black_box_regression_v2", "black_box_regression_v3"}:
        raise ValueError("unknown_test_suite")

    from .black_box_regression_grader import grade_response

    payload = grade_response(text)
    max_score = int(payload["max_score"])
    pass_threshold = int(grader.get("pass_threshold", max_score))
    survived = [str(item) for item in payload.get("survived_mutants", [])]
    status = str(payload.get("status") or "semantic_failed")
    failure_summary = str(payload.get("failure_summary") or "")
    if payload.get("score") is None:
        return GradeResult(
            ok=False,
            summary=f"{test_suite} {status}",
            score=None,
            max_score=max_score,
            diagnostics={
                "status": status,
                "test_suite": test_suite,
                "failure_summary": failure_summary,
                "regression_proof": payload.get("regression_proof", {}),
            },
        )
    score = int(payload["score"])
    summary = f"{test_suite} {score}/{max_score}"
    if status in {"patch_apply_failed", "submission_validation_failed"}:
        summary = f"{test_suite} {status}"
    elif survived:
        summary += f"; survived={','.join(survived[:4])}"
    return GradeResult(
        ok=score >= pass_threshold,
        summary=summary,
        score=score,
        max_score=max_score,
        failure_details=[
            {
                "case_id": str(item.get("id", "")),
                "label": str(item.get("label", "")),
                "category": "black_box_regression",
                "category_label": "黑盒回归",
            }
            for item in payload.get("score_details", [])
            if isinstance(item, dict) and not item.get("passed")
        ],
        diagnostics={
            "status": status,
            "test_suite": test_suite,
            "semantic_passed": score,
            "semantic_total": max_score,
            "killed_mutants": payload.get("killed_mutants", []),
            "survived_mutants": survived,
            "score_details": payload.get("score_details", []),
            "regression_proof": payload.get("regression_proof", {}),
            "failure_summary": failure_summary,
        },
    )


def _grade_session_bundle_test_design(
    text: str,
    grader: dict[str, object],
) -> GradeResult:
    test_suite = str(grader.get("test_suite") or "")
    if test_suite != "session_bundle_scenarios_v1":
        raise ValueError("unknown_test_suite")

    from .session_bundle_scenario_grader import grade_response

    payload = grade_response(text)
    score = int(payload["score"])
    max_score = int(payload["max_score"])
    pass_threshold = int(grader.get("pass_threshold", max_score))
    status = str(payload.get("status") or "semantic_failed")
    failure_summary = str(payload.get("failure_summary") or "")
    if status in {"invalid_json", "invalid_schema"}:
        summary = f"{test_suite} {status}"
        if failure_summary:
            summary += f"; {failure_summary}"
    else:
        summary = f"{test_suite} {score}/{max_score}"
        survived = [str(item) for item in payload.get("survived_mutants", [])]
        if survived:
            summary += f"; survived={','.join(survived[:4])}"
    return GradeResult(
        ok=score >= pass_threshold,
        summary=summary,
        score=score,
        max_score=max_score,
        failure_details=[
            {
                "case_id": str(item.get("case_id", "")),
                "label": str(item.get("label", "")),
                "category": str(item.get("category", "")),
                "category_label": str(item.get("category_label", "")),
            }
            for item in payload.get("failure_details", [])
            if isinstance(item, dict)
        ],
        diagnostics={
            "status": status,
            "test_suite": test_suite,
            "semantic_passed": score,
            "semantic_total": max_score,
            "killed_mutants": payload.get("killed_mutants", []),
            "survived_mutants": payload.get("survived_mutants", []),
            "killed_variants": payload.get("killed_variants", []),
            "survived_variants": payload.get("survived_variants", []),
            "killed_by_test": payload.get("killed_by_test", {}),
            "facets": payload.get("categories", {}),
            "score_details": payload.get("score_details", []),
            "eligible_steps": payload.get("eligible_steps", 0),
            "invalid_steps": payload.get("invalid_steps", []),
            "failure_summary": failure_summary,
        },
    )


def _grade_cross_loop_singleflight_patch(
    text: str,
    grader: dict[str, object],
) -> GradeResult:
    test_suite = str(grader["test_suite"])
    workers = {
        "cross_loop_singleflight_v2": _cross_loop_singleflight_patch_worker,
        "scalar_cross_loop_flight_v1": _scalar_cross_loop_flight_patch_worker,
    }
    if test_suite not in workers:
        raise ValueError("unknown_test_suite")
    timeout_seconds = float(grader.get("timeout_seconds", 120))
    pass_threshold = int(grader.get("pass_threshold", 0))
    max_score = _suite_max_score(test_suite)

    worker_name = (
        "cross_loop_singleflight_patch"
        if test_suite == "cross_loop_singleflight_v2"
        else "scalar_cross_loop_flight_patch"
    )
    payload, status = run_sandboxed_candidate_worker(
        worker_name,
        {"patch_text": _strip_code_fence(text)},
        timeout_seconds,
        allow_process_fork=True,
    )
    if payload is None:
        return _candidate_worker_failure(test_suite, max_score, status)

    score = int(payload.get("score", 0))
    raw_failure_details = payload.get("failure_details", [])
    failure_details = [
        {
            "case_id": str(item.get("case_id", "")),
            "label": str(item.get("label", "")),
            "category": str(item.get("category", "")),
            "category_label": str(item.get("category_label", "")),
        }
        for item in raw_failure_details
        if isinstance(item, dict)
    ]
    failures = [item["case_id"] for item in failure_details]
    diagnostics = _search_replace_patch_diagnostics(payload, score, max_score, failures)
    diagnostics["facets"] = payload.get("facets", {})
    diagnostics["score_details"] = payload.get("clusters", [])
    diagnostics["raw_score"] = payload.get("raw_score", 0)
    diagnostics["raw_max_score"] = payload.get("raw_max_score", 0)
    status = str(diagnostics.get("status") or "")
    if status == "patch_apply_failed":
        summary = f"{test_suite} patch_apply_failed"
        if diagnostics.get("failure_summary"):
            summary += f"; {diagnostics['failure_summary']}"
    elif status == "runner_error":
        summary = f"{test_suite} runner_error"
        if diagnostics.get("failure_summary"):
            summary += f"; {diagnostics['failure_summary']}"
    else:
        summary = f"{test_suite} {score}/{max_score}"
        if failures:
            summary += f"; failed={','.join(failures[:4])}"
    threshold = pass_threshold or max_score
    return GradeResult(
        ok=score >= threshold,
        summary=summary,
        score=score,
        max_score=max_score,
        failure_details=failure_details,
        diagnostics=diagnostics,
    )


def _grade_mutation_test_design(text: str, grader: dict[str, object]) -> GradeResult:
    test_suite = str(grader["test_suite"])
    if test_suite not in {
        "cache_regression_mutants",
        "cache_regression_mutants_v2",
        "cache_regression_mutants_v3",
    }:
        raise ValueError("unknown_test_suite")
    pass_threshold = int(grader.get("pass_threshold", 0))
    mutants = _mutation_cache_test_mutants(test_suite)
    max_score = _suite_max_score(test_suite)

    try:
        payload = bounded_json_loads(text, strip_code_fence=True)
        tests = _validate_mutation_cache_tests(payload)
    except Exception as exc:
        return GradeResult(
            ok=False,
            summary=f"{test_suite} invalid_json_or_schema; {type(exc).__name__}:{exc}",
            score=0,
            max_score=max_score,
            failure_details=[_mutation_cache_test_case_detail(str(mutant["id"])) for mutant in mutants],
            diagnostics={
                "status": "invalid_test_cases",
                "test_suite": test_suite,
                "killed_mutants": [],
                "survived_mutants": [str(mutant["id"]) for mutant in mutants],
                "semantic_passed": 0,
                "semantic_total": max_score,
                "failure_summary": f"{type(exc).__name__}:{exc}",
                "score_details": [
                    {
                        "id": str(mutant["id"]),
                        "label": _mutation_cache_test_case_detail(str(mutant["id"]))["label"],
                        "points": 0,
                        "max_points": 1,
                        "passed": False,
                    }
                    for mutant in mutants
                ],
            },
        )

    killed: set[str] = set()
    for test_case in tests:
        files = copy.deepcopy(test_case["files"])
        cache = copy.deepcopy(test_case["cache"])
        params = copy.deepcopy(test_case["params"])
        expected = _canonicalize_mutation_output(_reference_cache_run_scan(files, cache, params))
        for mutant in mutants:
            mutant_id = str(mutant["id"])
            if mutant_id in killed:
                continue
            try:
                got = mutant["run"](  # type: ignore[index,operator]
                    copy.deepcopy(test_case["files"]),
                    copy.deepcopy(test_case["cache"]),
                    copy.deepcopy(test_case["params"]),
                )
            except Exception as exc:
                got = {"__exception__": type(exc).__name__, "message": str(exc)}
            if _canonicalize_mutation_output(got) != expected:
                killed.add(mutant_id)

    mutant_ids = [str(mutant["id"]) for mutant in mutants]
    survived = [mutant_id for mutant_id in mutant_ids if mutant_id not in killed]
    score = len(killed)
    threshold = pass_threshold or max_score
    status = "passed" if score == max_score else "semantic_failed"
    summary = f"{test_suite} {score}/{max_score}"
    if survived:
        summary += f"; survived={','.join(survived[:4])}"
    return GradeResult(
        ok=score >= threshold,
        summary=summary,
        score=score,
        max_score=max_score,
        failure_details=[_mutation_cache_test_case_detail(mutant_id) for mutant_id in survived],
        diagnostics={
            "status": status,
            "test_suite": test_suite,
            "killed_mutants": sorted(killed),
            "survived_mutants": survived,
            "semantic_passed": score,
            "semantic_total": max_score,
            "score_details": [
                {
                    "id": mutant_id,
                    "label": _mutation_cache_test_case_detail(mutant_id)["label"],
                    "points": 1 if mutant_id in killed else 0,
                    "max_points": 1,
                    "passed": mutant_id in killed,
                }
                for mutant_id in mutant_ids
            ],
        },
    )


def _grade_transaction_regression_design(
    text: str,
    grader: dict[str, object],
) -> GradeResult:
    test_suite = str(grader.get("test_suite") or "")
    if test_suite not in {"transaction_replay_mutants_v1", "transaction_replay_mutants_v2"}:
        raise ValueError("unknown_test_suite")

    from .transaction_regression_grader import grade_response

    payload = grade_response(text, test_suite)
    score = int(payload["score"])
    max_score = int(payload["max_score"])
    pass_threshold = int(grader.get("pass_threshold", max_score))
    failure_details = [
        {
            "case_id": str(item.get("case_id", "")),
            "label": str(item.get("label", "")),
            "category": str(item.get("category", "")),
            "category_label": str(item.get("category_label", "")),
        }
        for item in payload.get("failure_details", [])
        if isinstance(item, dict)
    ]
    status = str(payload.get("status") or "semantic_failed")
    failure_summary = str(payload.get("failure_summary") or "")
    if status == "invalid_test_cases":
        summary = f"{test_suite} invalid_test_cases"
        if failure_summary:
            summary += f"; {failure_summary}"
    else:
        summary = f"{test_suite} {score}/{max_score}"
        survived = [str(item) for item in payload.get("survived_mutants", [])]
        if survived:
            summary += f"; survived={','.join(survived[:4])}"
    return GradeResult(
        ok=score >= pass_threshold,
        summary=summary,
        score=score,
        max_score=max_score,
        failure_details=failure_details,
        diagnostics={
            "status": status,
            "semantic_passed": score,
            "semantic_total": max_score,
            "killed_mutants": payload.get("killed_mutants", []),
            "survived_mutants": payload.get("survived_mutants", []),
            "killed_by_test": payload.get("killed_by_test", {}),
            "facets": payload.get("categories", {}),
            "score_details": payload.get("score_details", []),
            "failure_summary": failure_summary,
        },
    )


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip() + "\n"
    return text


def _unified_diff_patch_diagnostics(
    payload: dict[str, object],
    score: int,
    max_score: int,
    failures: list[str],
) -> dict[str, object]:
    patch_applies = bool(payload.get("patch_applies"))
    error = str(payload.get("error") or "")
    if not patch_applies:
        return {
            "patch_applies": False,
            "semantic_passed": 0,
            "semantic_total": max_score,
            "status": "patch_apply_failed",
            "failure_summary": _patch_failure_summary(error),
        }
    if error:
        return {
            "patch_applies": True,
            "semantic_passed": 0,
            "semantic_total": max_score,
            "status": "runner_error",
            "failure_summary": error,
            "failed_cases": failures,
        }
    if score == max_score:
        return {
            "patch_applies": True,
            "semantic_passed": score,
            "semantic_total": max_score,
            "status": "passed",
            "failed_cases": [],
        }
    return {
        "patch_applies": True,
        "semantic_passed": score,
        "semantic_total": max_score,
        "status": "semantic_failed",
        "failed_cases": failures,
    }


def _search_replace_patch_diagnostics(
    payload: dict[str, object],
    score: int,
    max_score: int,
    failures: list[str],
) -> dict[str, object]:
    patch_format_ok = bool(payload.get("patch_format_ok"))
    patch_applies = bool(payload.get("patch_applies"))
    error = str(payload.get("error") or "")
    if "search_block_not_unique" in error:
        patch_format_ok = True
    if not patch_applies:
        return {
            "patch_format_ok": patch_format_ok,
            "patch_applies": False,
            "semantic_passed": 0,
            "semantic_total": max_score,
            "status": "patch_apply_failed",
            "failure_summary": _patch_failure_summary(error),
            "failed_cases": [],
        }
    if error:
        return {
            "patch_format_ok": patch_format_ok,
            "patch_applies": True,
            "semantic_passed": 0,
            "semantic_total": max_score,
            "status": "runner_error",
            "failure_summary": error,
            "failed_cases": failures,
        }
    if score == max_score:
        return {
            "patch_format_ok": patch_format_ok,
            "patch_applies": True,
            "semantic_passed": score,
            "semantic_total": max_score,
            "status": "passed",
            "failed_cases": [],
        }
    return {
        "patch_format_ok": patch_format_ok,
        "patch_applies": True,
        "semantic_passed": score,
        "semantic_total": max_score,
        "status": "semantic_failed",
        "failed_cases": failures,
    }


def _patch_failure_summary(error: str) -> str:
    if not error:
        return "patch_apply_failed"
    for marker in ("patch_apply_failed:", "ValueError:"):
        if marker in error:
            error = error.split(marker, 1)[1]
    return error.strip() or "patch_apply_failed"


def _validate_python_source(source: str) -> None:
    allowed_imports = {"cache_policy", "copy", "re", "typing"}
    blocked_names = {"__builtins__", "__import__", "__loader__", "__package__", "__spec__"}
    blocked_attributes = {
        "__bases__",
        "__builtins__",
        "__class__",
        "__closure__",
        "__code__",
        "__dict__",
        "__func__",
        "__getattribute__",
        "__globals__",
        "__loader__",
        "__mro__",
        "__package__",
        "__spec__",
        "__subclasses__",
    }
    blocked_calls = {
        "__import__",
        "breakpoint",
        "compile",
        "eval",
        "exec",
        "getattr",
        "globals",
        "input",
        "locals",
        "open",
        "setattr",
        "vars",
    }
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError("syntax_error") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] not in allowed_imports:
                    raise ValueError("import_not_allowed")
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").split(".", 1)[0]
            if module not in allowed_imports:
                raise ValueError("import_not_allowed")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in blocked_calls:
                raise ValueError("call_not_allowed")
        elif isinstance(node, ast.Name) and node.id in blocked_names:
            raise ValueError("name_not_allowed")
        elif isinstance(node, ast.Attribute) and node.attr in blocked_attributes:
            raise ValueError("attribute_not_allowed")


def _python_function_worker(
    source: str,
    function_name: str,
    test_suite: str,
    result_queue: multiprocessing.Queue,
) -> None:
    try:
        namespace: dict[str, object] = {
            "__builtins__": _safe_builtins(),
        }
        exec(compile(source, "<candidate>", "exec"), namespace, namespace)
        function = namespace.get(function_name)
        if not callable(function):
            raise ValueError("function_missing")
        if test_suite == "micro_unified_diff":
            score, max_score, failure_details = _run_micro_unified_diff_suite(function)
        else:
            raise ValueError("unknown_test_suite")
        result_queue.put(
            {
                "score": score,
                "max_score": max_score,
                "failures": [item["case_id"] for item in failure_details],
                "failure_details": failure_details,
            }
        )
    except Exception as exc:
        result_queue.put(
            {
                "score": 0,
                "max_score": _suite_max_score(test_suite),
                "failures": [],
                "error": f"{type(exc).__name__}:{exc}",
            }
        )


def _unified_diff_patch_worker(
    diff_text: str,
    test_suite: str,
    result_queue: multiprocessing.Queue,
) -> None:
    patch_applies = False
    try:
        if test_suite != "micro_repo_cache_patch":
            raise ValueError("unknown_test_suite")
        patched_files = _apply_cache_runner_patch(
            _MICRO_REPO_CACHE_PATCH_INITIAL_SOURCE,
            diff_text,
        )
        patch_applies = True
        if set(patched_files) != {"cache_runner.py"}:
            raise ValueError("patch_must_only_modify_cache_runner")
        patched_source = patched_files["cache_runner.py"]
        _validate_python_source(patched_source)
        namespace: dict[str, object] = {
            "__builtins__": _safe_builtins(),
        }
        exec(compile(patched_source, "cache_runner.py", "exec"), namespace, namespace)
        function = namespace.get("run_scan")
        if not callable(function):
            raise ValueError("function_missing")
        score, max_score, failure_details = _run_micro_repo_cache_patch_suite(function)
        result_queue.put(
            {
                "score": score,
                "max_score": max_score,
                "failures": [item["case_id"] for item in failure_details],
                "failure_details": failure_details,
                "patch_applies": True,
            }
        )
    except Exception as exc:
        failure_details = [
            _micro_repo_cache_patch_case_detail(str(case["name"]))
            for case in _micro_repo_cache_patch_cases()
        ]
        result_queue.put(
            {
                "score": 0,
                "max_score": _suite_max_score(test_suite),
                "failures": [item["case_id"] for item in failure_details],
                "failure_details": failure_details,
                "error": f"{type(exc).__name__}:{exc}",
                "patch_applies": patch_applies,
            }
        )


def _search_replace_patch_worker(
    patch_text: str,
    test_suite: str,
    result_queue: multiprocessing.Queue,
) -> None:
    patch_format_ok = False
    patch_applies = False
    try:
        if test_suite != "micro_repo_cache_patch":
            raise ValueError("unknown_test_suite")
        patched_files = _apply_search_replace_patch(_MICRO_REPO_CACHE_PATCH_INITIAL_FILES, patch_text)
        patch_format_ok = True
        patch_applies = True
        if not set(patched_files).issubset({"cache_runner.py", "cache_policy.py"}):
            raise ValueError("patch_must_only_modify_cache_files")
        if "cache_policy.py" not in patched_files:
            raise ValueError("patch_must_modify_cache_policy")
        function = _load_micro_repo_cache_patch_function(patched_files)
        score, max_score, failure_details = _run_micro_repo_cache_patch_suite(function)
        result_queue.put(
            {
                "score": score,
                "max_score": max_score,
                "failures": [item["case_id"] for item in failure_details],
                "failure_details": failure_details,
                "patch_format_ok": patch_format_ok,
                "patch_applies": patch_applies,
            }
        )
    except Exception as exc:
        failure_details = [
            _micro_repo_cache_patch_case_detail(str(case["name"]))
            for case in _micro_repo_cache_patch_cases()
        ]
        result_queue.put(
            {
                "score": 0,
                "max_score": _suite_max_score(test_suite),
                "failures": [item["case_id"] for item in failure_details],
                "failure_details": failure_details,
                "error": f"{type(exc).__name__}:{exc}",
                "patch_format_ok": patch_format_ok,
                "patch_applies": patch_applies,
            }
        )


def _session_bundle_patch_worker(
    patch_text: str,
    result_queue: multiprocessing.Queue,
) -> None:
    try:
        from .compact_session_repair_grader import grade_patch

        result_queue.put(grade_patch(patch_text))
    except BaseException as exc:
        result_queue.put(
            {
                "score": 0,
                "max_score": 10,
                "failure_details": [],
                "patch_format_ok": False,
                "patch_applies": False,
                "error": f"{type(exc).__name__}:{exc}",
            }
        )


def _cross_loop_singleflight_patch_worker(
    patch_text: str,
    result_queue: multiprocessing.Queue,
) -> None:
    try:
        from .cross_loop_singleflight_grader import grade_patch

        result_queue.put(grade_patch(patch_text))
    except BaseException as exc:
        result_queue.put(
            {
                "score": 0,
                "max_score": 10,
                "failure_details": [],
                "patch_format_ok": False,
                "patch_applies": False,
                "error": f"{type(exc).__name__}:{exc}",
            }
        )


def _scalar_cross_loop_flight_patch_worker(
    patch_text: str,
    result_queue: multiprocessing.Queue,
) -> None:
    try:
        from .scalar_cross_loop_flight_grader import grade_patch

        result_queue.put(grade_patch(patch_text))
    except BaseException as exc:
        result_queue.put(
            {
                "score": 0,
                "max_score": 10,
                "failure_details": [],
                "patch_format_ok": False,
                "patch_applies": False,
                "error": f"{type(exc).__name__}:{exc}",
            }
        )


def _load_micro_repo_cache_patch_function(files: dict[str, str]) -> Callable[..., dict[str, object]]:
    policy_source = files["cache_policy.py"]
    runner_source = files["cache_runner.py"]
    _validate_python_source(policy_source)
    _validate_python_source(runner_source)

    policy_module = types.ModuleType("cache_policy")
    policy_module.__dict__["__builtins__"] = _safe_builtins()
    exec(compile(policy_source, "cache_policy.py", "exec"), policy_module.__dict__, policy_module.__dict__)

    previous_policy = sys.modules.get("cache_policy")
    sys.modules["cache_policy"] = policy_module
    try:
        namespace: dict[str, object] = {
            "__builtins__": _safe_builtins(),
            "__name__": "cache_runner",
        }
        exec(compile(runner_source, "cache_runner.py", "exec"), namespace, namespace)
    finally:
        if previous_policy is None:
            sys.modules.pop("cache_policy", None)
        else:
            sys.modules["cache_policy"] = previous_policy

    function = namespace.get("run_scan")
    if not callable(function):
        raise ValueError("function_missing")
    return function


def _safe_builtins() -> dict[str, object]:
    allowed_modules = {"cache_policy", "copy", "re", "typing"}

    def limited_import(name: str, globals=None, locals=None, fromlist=(), level: int = 0):  # type: ignore[no-untyped-def]
        root = name.split(".", 1)[0]
        if root not in allowed_modules:
            raise ImportError(f"import not allowed: {name}")
        return __import__(name, globals, locals, fromlist, level)

    return {
        "__import__": limited_import,
        "abs": abs,
        "all": all,
        "any": any,
        "bool": bool,
        "dict": dict,
        "enumerate": enumerate,
        "Exception": Exception,
        "int": int,
        "isinstance": isinstance,
        "len": len,
        "list": list,
        "max": max,
        "min": min,
        "object": object,
        "range": range,
        "reversed": reversed,
        "set": set,
        "sorted": sorted,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "ValueError": ValueError,
        "zip": zip,
    }


def _suite_max_score(test_suite: str) -> int:
    if test_suite == "micro_unified_diff":
        return len(_micro_unified_diff_cases())
    if test_suite == "micro_repo_cache_patch":
        return len(_micro_repo_cache_patch_cases())
    if test_suite in {
        "cache_regression_mutants",
        "cache_regression_mutants_v2",
        "cache_regression_mutants_v3",
    }:
        return len(_mutation_cache_test_mutants(test_suite))
    if test_suite == "compact_session_repair_v1":
        return 10
    if test_suite in {"black_box_regression_v2", "black_box_regression_v3"}:
        return 20
    if test_suite == "session_bundle_scenarios_v1":
        return 20
    if test_suite == "cross_loop_singleflight_v2":
        return 10
    if test_suite == "scalar_cross_loop_flight_v1":
        return 10
    return 0


def _run_micro_unified_diff_suite(function: Callable[[dict[str, str], str], dict[str, str]]) -> tuple[int, int, list[dict[str, str]]]:
    cases = _micro_unified_diff_cases()
    score = 0
    failures: list[dict[str, str]] = []
    for case in cases:
        name = str(case["name"])
        files = copy.deepcopy(case["files"])
        before = copy.deepcopy(files)
        try:
            result = function(files, str(case["diff"]))
            if case.get("raises"):
                failures.append(_micro_unified_diff_case_detail(name))
                continue
            if result != case["expected"]:
                failures.append(_micro_unified_diff_case_detail(name))
                continue
            if files != before:
                failures.append(_micro_unified_diff_case_detail(name))
                continue
            if "/dev/null" in result:
                failures.append(_micro_unified_diff_case_detail(name))
                continue
            score += 1
        except ValueError:
            if case.get("raises") and files == before:
                score += 1
            else:
                failures.append(_micro_unified_diff_case_detail(name))
        except Exception:
            failures.append(_micro_unified_diff_case_detail(name))
    return score, len(cases), failures


def _apply_cache_runner_patch(source: str, diff_text: str) -> dict[str, str]:
    with tempfile.TemporaryDirectory(prefix="micro_repo_cache_patch_") as tmp:
        root = Path(tmp)
        source_path = root / "cache_runner.py"
        diff_path = root / "candidate.patch"
        source_path.write_text(source, encoding="utf-8")
        diff_path.write_text(diff_text, encoding="utf-8")

        try:
            check = subprocess.run(
                ["git", "apply", "--check", str(diff_path)],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return _apply_unified_diff_to_files({"cache_runner.py": source}, diff_text)

        if check.returncode != 0:
            message = (check.stderr or check.stdout).strip().splitlines()
            raise ValueError(f"patch_apply_failed:{message[0] if message else 'unknown'}")

        apply = subprocess.run(
            ["git", "apply", str(diff_path)],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
        )
        if apply.returncode != 0:
            message = (apply.stderr or apply.stdout).strip().splitlines()
            raise ValueError(f"patch_apply_failed:{message[0] if message else 'unknown'}")

        files = {
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() and path != diff_path
        }
        if files != {"cache_runner.py"}:
            raise ValueError("patch_must_only_modify_cache_runner")
        return {"cache_runner.py": source_path.read_text(encoding="utf-8")}


def _apply_search_replace_patch(source: str | dict[str, str], patch_text: str) -> dict[str, str]:
    if isinstance(source, str):
        source_files = {"cache_runner.py": source}
    else:
        source_files = dict(source)
    lines = patch_text.replace("\r\n", "\n").replace("\r", "\n").splitlines(True)
    start = _skip_blank_lines(lines, 0)
    if start >= len(lines) or lines[start].strip() != "*** Begin Patch":
        raise ValueError("missing_begin_patch")
    i = _skip_blank_lines(lines, start + 1)
    replacements: list[tuple[str, str, str]] = []
    while i < len(lines) and lines[i].strip() != "*** End Patch":
        stripped = lines[i].strip()
        if not stripped.startswith("*** Update File: "):
            raise ValueError("missing_update_file")
        path = stripped.removeprefix("*** Update File: ").strip()
        if path not in source_files:
            raise ValueError(f"unknown_update_file:{path}")
        i = _skip_blank_lines(lines, i + 1)
        saw_block = False
        while i < len(lines):
            stripped = lines[i].strip()
            if stripped == "*** End Patch" or stripped.startswith("*** Update File: "):
                break
            if stripped != "<<<<<<< SEARCH":
                raise ValueError("missing_search_marker")
            search_start = i + 1
            separator = _find_marker(lines, search_start, "=======")
            replace_end = _find_marker(lines, separator + 1, ">>>>>>> REPLACE")
            search = "".join(lines[search_start:separator])
            replacement = "".join(lines[separator + 1:replace_end])
            if not search:
                raise ValueError("empty_search_block")
            replacements.append((path, search, replacement))
            saw_block = True
            i = _skip_blank_lines(lines, replace_end + 1)
        if not saw_block:
            raise ValueError("empty_update_file")

    if i >= len(lines) or lines[i].strip() != "*** End Patch":
        raise ValueError("missing_end_patch")
    if not replacements:
        raise ValueError("empty_patch")
    trailing = "".join(lines[i + 1 :]).strip()
    if trailing:
        raise ValueError("trailing_content")

    current_files = dict(source_files)
    for path, search, replacement in replacements:
        current = current_files[path]
        count = current.count(search)
        if count != 1:
            raise ValueError(f"search_block_not_unique:{count}")
        current_files[path] = current.replace(search, replacement, 1)
    return current_files


def _skip_blank_lines(lines: list[str], index: int) -> int:
    while index < len(lines) and not lines[index].strip():
        index += 1
    return index


def _find_marker(lines: list[str], start: int, marker: str) -> int:
    for index in range(start, len(lines)):
        if lines[index].strip() == marker:
            return index
    raise ValueError(f"missing_marker:{marker}")


def _apply_unified_diff_to_files(files: dict[str, str], diff_text: str) -> dict[str, str]:
    lines = [
        line
        for line in diff_text.splitlines(True)
        if not line.startswith("index ")
        and not line.startswith("new file mode ")
        and not line.startswith("deleted file mode ")
        and not line.startswith("similarity index ")
    ]
    if not lines:
        raise ValueError("empty_diff")
    out = copy.deepcopy(files)
    i = 0
    while i < len(lines):
        if lines[i].startswith("diff --git "):
            i += 1
            while i < len(lines) and not lines[i].startswith("--- "):
                i += 1
        if i >= len(lines):
            raise ValueError("missing_old_header")
        if not lines[i].startswith("--- "):
            raise ValueError("missing_old_header")
        old_header = lines[i].rstrip("\n")
        i += 1
        if i >= len(lines) or not lines[i].startswith("+++ "):
            raise ValueError("missing_new_header")
        new_header = lines[i].rstrip("\n")
        i += 1
        old_path = _normalize_patch_path(_patch_header_path(old_header[4:]))
        new_path = _normalize_patch_path(_patch_header_path(new_header[4:]))
        if old_path != "cache_runner.py" or new_path != "cache_runner.py":
            raise ValueError("patch_must_only_modify_cache_runner")
        old_lines = out[old_path].splitlines(True)
        result: list[str] = []
        old_index = 0
        saw_hunk = False
        while i < len(lines):
            if lines[i].startswith("--- "):
                break
            header = lines[i].rstrip("\n")
            if not header.startswith("@@ ") or " @@" not in header:
                raise ValueError("bad_hunk_header")
            old_start, old_count, new_count = _parse_patch_hunk_header(header)
            start_index = old_start if old_count == 0 else old_start - 1
            if start_index < old_index or start_index > len(old_lines):
                raise ValueError("bad_hunk_position")
            result.extend(old_lines[old_index:start_index])
            old_index = start_index
            i += 1
            saw_hunk = True
            old_seen = 0
            new_seen = 0
            while i < len(lines) and not lines[i].startswith("@@ ") and not lines[i].startswith("--- "):
                line = lines[i]
                if line.startswith("\\ No newline at end of file"):
                    raise ValueError("newline_marker_not_supported")
                if not line:
                    raise ValueError("bad_hunk_line")
                prefix = line[0]
                content = line[1:]
                if prefix == " ":
                    if old_index >= len(old_lines) or old_lines[old_index] != content:
                        raise ValueError("context_mismatch")
                    result.append(content)
                    old_index += 1
                    old_seen += 1
                    new_seen += 1
                elif prefix == "-":
                    if old_index >= len(old_lines) or old_lines[old_index] != content:
                        raise ValueError("delete_mismatch")
                    old_index += 1
                    old_seen += 1
                elif prefix == "+":
                    result.append(content)
                    new_seen += 1
                else:
                    raise ValueError("bad_hunk_line")
                i += 1
            if old_seen != old_count or new_seen != new_count:
                raise ValueError("hunk_count_mismatch")
        if not saw_hunk:
            raise ValueError("missing_hunk")
        result.extend(old_lines[old_index:])
        out["cache_runner.py"] = "".join(result)
    return out


def _normalize_patch_path(path: str) -> str:
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


def _patch_header_path(raw: str) -> str:
    # Some diff generators append a timestamp after the path. The prompt path
    # has no spaces, so whitespace splitting is safe for this micro-repo.
    return raw.split("\t", 1)[0].split(None, 1)[0]


def _parse_patch_hunk_header(header: str) -> tuple[int, int, int]:
    body = header.split(" @@", 1)[0][3:]
    old_part, new_part = body.split(" ", 1)

    def parse(part: str) -> tuple[int, int]:
        rest = part[1:]
        if "," in rest:
            start_text, count_text = rest.split(",", 1)
            return int(start_text), int(count_text)
        return int(rest), 1

    old_start, old_count = parse(old_part)
    _new_start, new_count = parse(new_part)
    if old_start < 0 or old_count < 0 or new_count < 0:
        raise ValueError("negative_hunk_range")
    return old_start, old_count, new_count


def _run_micro_repo_cache_patch_suite(function: Callable[[list[dict[str, object]], dict[str, dict[str, object]], dict[str, object]], dict[str, object]]) -> tuple[int, int, list[dict[str, str]]]:
    cases = _micro_repo_cache_patch_cases()
    score = 0
    failures: list[dict[str, str]] = []
    for case in cases:
        name = str(case["name"])
        try:
            ok = case["check"](function)  # type: ignore[index,operator]
        except Exception:
            ok = False
        if ok:
            score += 1
        else:
            failures.append(_micro_repo_cache_patch_case_detail(name))
    return score, len(cases), failures


def _validate_mutation_cache_tests(payload: object) -> list[dict[str, object]]:
    if not isinstance(payload, dict) or set(payload) != {"tests"}:
        raise ValueError("payload_must_be_object")
    tests = payload.get("tests")
    if not isinstance(tests, list) or not tests:
        raise ValueError("tests_must_be_non_empty_list")
    if len(tests) > 3:
        raise ValueError("too_many_tests")
    validated: list[dict[str, object]] = []
    names: set[str] = set()
    for item in tests:
        if not isinstance(item, dict) or set(item) != {"name", "files", "cache", "params"}:
            raise ValueError("test_must_be_object")
        name = item.get("name")
        files = item.get("files")
        cache = item.get("cache")
        params = item.get("params")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("invalid_test_name")
        names.add(name)
        if not isinstance(files, list) or len(files) > 8:
            raise ValueError("files_must_have_at_most_8_items")
        if not isinstance(cache, dict) or len(cache) > 10:
            raise ValueError("cache_must_have_at_most_10_entries")
        if not isinstance(params, dict):
            raise ValueError("params_must_be_object")
        for file_info in files:
            _validate_mutation_file_info(file_info)
        for path, entry in cache.items():
            if not isinstance(path, str):
                raise ValueError("cache_path_must_be_string")
            _validate_mutation_cache_entry(entry)
        _validate_mutation_params(params)
        validated.append({"name": name, "files": files, "cache": cache, "params": params})
    return validated


def _validate_mutation_file_info(payload: object) -> None:
    if not isinstance(payload, dict):
        raise ValueError("file_must_be_object")
    if not isinstance(payload.get("path"), str):
        raise ValueError("file_path_must_be_string")
    if not isinstance(payload.get("content_hash"), str):
        raise ValueError("content_hash_must_be_string")
    if type(payload.get("issue_count")) is not int or int(payload["issue_count"]) < 0:
        raise ValueError("issue_count_must_be_non_negative_int")


def _validate_mutation_cache_entry(payload: object) -> None:
    if not isinstance(payload, dict):
        raise ValueError("entry_must_be_object")
    for key in ("path", "content_hash", "config_hash", "profile_name", "profile_hash", "options_key"):
        if not isinstance(payload.get(key), str):
            raise ValueError(f"{key}_must_be_string")
    for key in ("stored_day", "issue_count"):
        if type(payload.get(key)) is not int:
            raise ValueError(f"{key}_must_be_int")
    if int(payload["issue_count"]) < 0:
        raise ValueError("entry_issue_count_must_be_non_negative")
    if not isinstance(payload.get("corrupted"), bool):
        raise ValueError("corrupted_must_be_bool")


def _validate_mutation_params(payload: dict[str, object]) -> None:
    for key in ("current_day", "cache_expiry_days"):
        if type(payload.get(key)) is not int or int(payload[key]) < 0:
            raise ValueError(f"{key}_must_be_non_negative_int")
    for key in ("config_hash", "profile_name", "profile_hash", "options_key"):
        if not isinstance(payload.get(key), str):
            raise ValueError(f"{key}_must_be_string")
    for key in ("force_rescan", "warm_cache"):
        if not isinstance(payload.get(key), bool):
            raise ValueError(f"{key}_must_be_bool")


def _reference_cache_run_scan(
    files: list[dict[str, object]],
    cache: dict[str, dict[str, object]],
    params: dict[str, object],
) -> dict[str, object]:
    return _cache_run_scan_variant(files, cache, params)


def _cache_run_scan_variant(
    files: list[dict[str, object]],
    cache: dict[str, dict[str, object]],
    params: dict[str, object],
    *,
    bug: str = "",
) -> dict[str, object]:
    new_cache = copy.deepcopy(cache)

    cache_hits = 0
    cache_misses = 0
    invalidation_counts: dict[str, int] = {}
    scanned_files: list[str] = []
    reported_issues: list[list[object]] = []
    listed_paths = {str(file_info["path"]) for file_info in files}

    for file_info in files:
        path = str(file_info["path"])
        entry = cache.get(path)
        reason = _cache_invalidation_reason_variant(file_info, entry, params, bug=bug)
        if reason is None:
            cache_hits += 1
            if bug == "hit_uses_current_issue_count":
                issue_count = int(file_info.get("issue_count", 0))
            else:
                issue_count = int(entry.get("issue_count", 0)) if entry else 0
            if bug == "hit_refreshes_stored_day":
                new_cache[path]["stored_day"] = params["current_day"]
            if bug == "warm_hit_refreshes_current_issue_count" and params.get("warm_cache"):
                new_cache[path]["issue_count"] = file_info["issue_count"]
        else:
            hide_metrics = bug == "warm_miss_hides_scan_metrics" and params.get("warm_cache")
            if not hide_metrics:
                cache_misses += 1
                if bug == "invalidation_counts_collapse_duplicates":
                    invalidation_counts[reason] = 1
                else:
                    invalidation_counts[reason] = invalidation_counts.get(reason, 0) + 1
                scanned_files.append(path)
            issue_count = int(file_info.get("issue_count", 0))
            profile_hash = params["profile_hash"]
            content_hash = file_info["content_hash"]
            if (
                bug == "force_warm_keeps_stale_profile_hash"
                and reason == "force_rescan"
                and entry is not None
                and params.get("warm_cache")
            ):
                profile_hash = entry["profile_hash"]
            if (
                bug == "force_refresh_keeps_stale_content_hash"
                and reason == "force_rescan"
                and entry is not None
            ):
                content_hash = entry["content_hash"]
            new_cache[path] = {
                "path": path,
                "content_hash": content_hash,
                "config_hash": params["config_hash"],
                "profile_name": params["profile_name"],
                "profile_hash": profile_hash,
                "options_key": params["options_key"],
                "stored_day": params["current_day"],
                "issue_count": issue_count,
                "corrupted": False,
            }

        warm_cache = bool(params.get("warm_cache"))
        if bug == "warm_cache_reports_hits" and reason is None:
            warm_cache = False
        if not warm_cache and issue_count > 0:
            reported_issues.append([path, issue_count])

    if bug == "purges_expired_unlisted_entries":
        expiry_days = int(params.get("cache_expiry_days", 7))
        current_day = int(params["current_day"])
        new_cache = {
            path: entry
            for path, entry in new_cache.items()
            if path in listed_paths
            or not (
                expiry_days == 0
                or current_day - int(entry.get("stored_day", 0)) > expiry_days
            )
        }
    if bug == "miss_drops_unlisted_entries" and any(
        _cache_invalidation_reason_variant(file_info, cache.get(str(file_info["path"])), params)
        is not None
        for file_info in files
    ):
        new_cache = {path: entry for path, entry in new_cache.items() if path in listed_paths}

    reported_issues.sort(key=lambda item: str(item[0]))
    scanned_files.sort()
    total_reported_issues = sum(int(item[1]) for item in reported_issues)
    if bug == "total_reported_issues_counts_files":
        total_reported_issues = len(reported_issues)
    return {
        "cache": new_cache,
        "metrics": {
            "cache_hits": cache_hits,
            "cache_misses": cache_misses,
            "invalidation_counts": invalidation_counts,
            "scanned_files": scanned_files,
            "reported_issues": reported_issues,
            "total_reported_issues": total_reported_issues,
        },
    }


def _cache_invalidation_reason_variant(
    file_info: dict[str, object],
    entry: dict[str, object] | None,
    params: dict[str, object],
    *,
    bug: str = "",
) -> str | None:
    if (
        params.get("force_rescan")
        and bug == "priority_corrupted_before_force"
        and entry is not None
        and entry.get("corrupted")
    ):
        return "corrupted"
    if params.get("force_rescan"):
        return "force_rescan"
    if entry is None:
        return "not_cached"
    if bug == "priority_config_before_corrupted" and entry.get("config_hash") != params.get("config_hash"):
        return "config_changed"
    if bug == "priority_file_before_corrupted" and (
        entry.get("path") != file_info.get("path")
        or entry.get("content_hash") != file_info.get("content_hash")
    ):
        return "file_changed"
    if entry.get("corrupted"):
        return "corrupted"
    entry_path_changed = entry.get("path") != file_info.get("path")
    if bug == "entry_path_mismatch_ignored":
        entry_path_changed = False
    if entry_path_changed or entry.get("content_hash") != file_info.get("content_hash"):
        return "file_changed"
    if entry.get("config_hash") != params.get("config_hash"):
        return "config_changed"
    profile_changed = entry.get("profile_name") != params.get("profile_name")
    if bug == "profile_name_change_ignored":
        profile_changed = False
    if bug != "profile_hash_change_ignored":
        profile_changed = profile_changed or entry.get("profile_hash") != params.get("profile_hash")
    if bug == "priority_expiry_before_profile" and profile_changed:
        expiry_days = int(params.get("cache_expiry_days", 7))
        age_days = int(params["current_day"]) - int(entry.get("stored_day", 0))
        if expiry_days == 0 or age_days > expiry_days:
            return "expired"
    if profile_changed:
        return "profile_changed"
    options_changed = entry.get("options_key") != params.get("options_key")
    if bug == "priority_expiry_before_options" and options_changed:
        expiry_days = int(params.get("cache_expiry_days", 7))
        age_days = int(params["current_day"]) - int(entry.get("stored_day", 0))
        if expiry_days == 0 or age_days > expiry_days:
            return "expired"
    if bug != "options_change_ignored" and options_changed:
        return "options_changed"
    expiry_days = int(params.get("cache_expiry_days", 7))
    age_days = int(params["current_day"]) - int(entry.get("stored_day", 0))
    if bug == "expiry_zero_never_expires" and expiry_days == 0:
        return None
    if expiry_days == 0:
        return "expired"
    if bug == "expiry_boundary_inclusive":
        return "expired" if age_days >= expiry_days else None
    return "expired" if age_days > expiry_days else None


def _canonicalize_mutation_output(payload: object) -> object:
    return json.loads(json.dumps(payload, sort_keys=True, ensure_ascii=False))


def _mutation_cache_test_mutants(
    test_suite: str = "cache_regression_mutants",
) -> list[dict[str, object]]:
    def run_bug(bug: str) -> Callable[[list[dict[str, object]], dict[str, dict[str, object]], dict[str, object]], dict[str, object]]:
        return lambda files, cache, params: _cache_run_scan_variant(files, cache, params, bug=bug)

    if test_suite == "cache_regression_mutants":
        mutant_ids = (
            "priority_config_before_corrupted",
            "entry_path_mismatch_ignored",
            "profile_hash_change_ignored",
            "options_change_ignored",
            "priority_expiry_before_options",
            "priority_corrupted_before_force",
            "force_warm_keeps_stale_profile_hash",
            "warm_cache_reports_hits",
            "hit_uses_current_issue_count",
            "purges_expired_unlisted_entries",
        )
    elif test_suite == "cache_regression_mutants_v2":
        mutant_ids = (
            "priority_config_before_corrupted",
            "entry_path_mismatch_ignored",
            "profile_hash_change_ignored",
            "options_change_ignored",
            "expiry_boundary_inclusive",
            "priority_corrupted_before_force",
            "expiry_zero_never_expires",
            "warm_cache_reports_hits",
            "profile_name_change_ignored",
            "purges_expired_unlisted_entries",
        )
    elif test_suite == "cache_regression_mutants_v3":
        mutant_ids = (
            "priority_config_before_corrupted",
            "entry_path_mismatch_ignored",
            "profile_hash_change_ignored",
            "options_change_ignored",
            "expiry_boundary_inclusive",
            "priority_corrupted_before_force",
            "expiry_zero_never_expires",
            "warm_cache_reports_hits",
            "profile_name_change_ignored",
            "purges_expired_unlisted_entries",
            "priority_file_before_corrupted",
            "priority_expiry_before_profile",
            "force_warm_keeps_stale_profile_hash",
            "force_refresh_keeps_stale_content_hash",
            "hit_refreshes_stored_day",
            "warm_hit_refreshes_current_issue_count",
            "warm_miss_hides_scan_metrics",
            "miss_drops_unlisted_entries",
            "invalidation_counts_collapse_duplicates",
            "total_reported_issues_counts_files",
        )
    else:
        raise ValueError("unknown_test_suite")
    return [{"id": mutant_id, "run": run_bug(mutant_id)} for mutant_id in mutant_ids]


_MUTATION_CACHE_TEST_CATEGORY_LABELS = {
    "cache_key": "缓存键",
    "priority": "优先级",
    "expiry": "过期规则",
    "mode_state": "模式／状态",
    "reporting": "报告口径",
    "preservation": "状态保持",
}


_MUTATION_CACHE_TEST_TAGS = {
    "priority_config_before_corrupted": ("配置错误抢占损坏优先级", "priority"),
    "entry_path_mismatch_ignored": ("忽略 entry path 不一致", "cache_key"),
    "profile_hash_change_ignored": ("忽略 profile hash 变更", "cache_key"),
    "options_change_ignored": ("忽略选项变更", "cache_key"),
    "priority_expiry_before_options": ("过期错误抢占选项变更", "expiry"),
    "expiry_boundary_inclusive": ("过期边界提前失效", "expiry"),
    "expiry_zero_never_expires": ("零天过期错误保留缓存", "expiry"),
    "priority_corrupted_before_force": ("损坏错误抢占强制重扫", "priority"),
    "force_warm_keeps_stale_profile_hash": ("强制预热写回旧 profile hash", "mode_state"),
    "warm_cache_reports_hits": ("预热命中仍报告", "mode_state"),
    "hit_uses_current_issue_count": ("命中报告当前问题数", "reporting"),
    "profile_name_change_ignored": ("忽略 profile name 变更", "cache_key"),
    "purges_expired_unlisted_entries": ("清理过期的未列缓存", "preservation"),
    "priority_file_before_corrupted": ("文件变化错误抢占损坏优先级", "priority"),
    "priority_expiry_before_profile": ("过期错误抢占 profile 变更", "priority"),
    "force_refresh_keeps_stale_content_hash": ("强制刷新写回旧内容哈希", "mode_state"),
    "hit_refreshes_stored_day": ("命中错误刷新存储日期", "preservation"),
    "warm_hit_refreshes_current_issue_count": ("预热命中覆盖缓存问题数", "mode_state"),
    "warm_miss_hides_scan_metrics": ("预热未命中错误隐藏扫描指标", "reporting"),
    "miss_drops_unlisted_entries": ("未命中错误丢弃未列缓存", "preservation"),
    "invalidation_counts_collapse_duplicates": ("相同失效原因错误合并计数", "reporting"),
    "total_reported_issues_counts_files": ("报告总数错误按文件计数", "reporting"),
}


def _mutation_cache_test_case_detail(mutant_id: str) -> dict[str, str]:
    label, category = _MUTATION_CACHE_TEST_TAGS.get(mutant_id, (mutant_id, "cache_key"))
    return {
        "case_id": mutant_id,
        "label": label,
        "category": category,
        "category_label": _MUTATION_CACHE_TEST_CATEGORY_LABELS.get(category, category),
    }


def mutation_test_design_facets(diagnostics: dict[str, object]) -> list[dict[str, object]]:
    killed = {str(item) for item in diagnostics.get("killed_mutants", [])}
    survived = {str(item) for item in diagnostics.get("survived_mutants", [])}
    if not killed and not survived:
        return []

    counts = {
        category: {"passed": 0, "total": 0}
        for category in _MUTATION_CACHE_TEST_CATEGORY_LABELS
    }
    test_suite = str(diagnostics.get("test_suite") or "cache_regression_mutants")
    for mutant in _mutation_cache_test_mutants(test_suite):
        mutant_id = str(mutant["id"])
        detail = _mutation_cache_test_case_detail(mutant_id)
        category = detail["category"]
        counts[category]["total"] += 1
        if mutant_id in killed:
            counts[category]["passed"] += 1

    return [
        {
            "id": category,
            "label": label,
            "passed": counts[category]["passed"],
            "total": counts[category]["total"],
        }
        for category, label in _MUTATION_CACHE_TEST_CATEGORY_LABELS.items()
        if counts[category]["total"] > 0
    ]


def cache_propagation_certificate_facets(
    diagnostics: dict[str, object],
) -> list[dict[str, object]]:
    labels = {
        "invalidation": "失效判定",
        "state": "缓存状态",
        "transaction": "事务提交",
        "eviction": "容量淘汰",
        "metrics": "指标报告",
        "certificate": "传播证书",
    }
    raw_facets = diagnostics.get("facets")
    if not isinstance(raw_facets, dict):
        return []
    facets: list[dict[str, object]] = []
    for facet_id, label in labels.items():
        raw = raw_facets.get(facet_id)
        if not isinstance(raw, dict):
            continue
        passed = raw.get("passed")
        total = raw.get("total")
        if not _is_plain_int(passed) or not _is_plain_int(total):
            continue
        facets.append(
            {
                "id": facet_id,
                "label": label,
                "passed": int(passed),
                "total": int(total),
            }
        )
    return facets


def _base_cache_entry(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "path": "a.py",
        "content_hash": "ha",
        "config_hash": "cfg",
        "profile_name": "strict",
        "profile_hash": "p1",
        "options_key": "opt",
        "stored_day": 10,
        "issue_count": 2,
        "corrupted": False,
    }
    entry.update(overrides)
    return entry


def _base_params(**overrides: object) -> dict[str, object]:
    params: dict[str, object] = {
        "current_day": 12,
        "config_hash": "cfg",
        "profile_name": "strict",
        "profile_hash": "p1",
        "options_key": "opt",
        "cache_expiry_days": 7,
        "force_rescan": False,
        "warm_cache": False,
    }
    params.update(overrides)
    return params


def _metrics(result: dict[str, object]) -> dict[str, object]:
    metrics = result.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("metrics_missing")
    return metrics


def _micro_repo_cache_patch_cases() -> list[dict[str, object]]:
    def hit_and_not_cached(function: Callable[..., dict[str, object]]) -> bool:
        cache = {"a.py": _base_cache_entry()}
        files = [
            {"path": "a.py", "content_hash": "ha", "issue_count": 99},
            {"path": "b.py", "content_hash": "hb", "issue_count": 3},
        ]
        result = function(files, cache, _base_params())
        return _metrics(result) == {
            "cache_hits": 1,
            "cache_misses": 1,
            "invalidation_counts": {"not_cached": 1},
            "scanned_files": ["b.py"],
            "reported_issues": [["a.py", 2], ["b.py", 3]],
            "total_reported_issues": 5,
        }

    def single_reason(reason: str, entry_overrides: dict[str, object], params_overrides: dict[str, object] | None = None) -> Callable[..., bool]:
        def check(function: Callable[..., dict[str, object]]) -> bool:
            cache = {"a.py": _base_cache_entry(**entry_overrides)}
            files = [{"path": "a.py", "content_hash": "ha", "issue_count": 4}]
            result = function(files, cache, _base_params(**(params_overrides or {})))
            metrics = _metrics(result)
            return (
                metrics["cache_hits"] == 0
                and metrics["cache_misses"] == 1
                and metrics["invalidation_counts"] == {reason: 1}
                and metrics["scanned_files"] == ["a.py"]
                and metrics["reported_issues"] == [["a.py", 4]]
                and metrics["total_reported_issues"] == 4
            )

        return check

    def force_rescan_beats_everything(function: Callable[..., dict[str, object]]) -> bool:
        cache = {"a.py": _base_cache_entry(content_hash="old", corrupted=True)}
        files = [{"path": "a.py", "content_hash": "ha", "issue_count": 4}]
        result = function(files, cache, _base_params(force_rescan=True))
        metrics = _metrics(result)
        new_cache = result.get("cache", {})
        return (
            metrics["cache_hits"] == 0
            and metrics["cache_misses"] == 1
            and metrics["invalidation_counts"] == {"force_rescan": 1}
            and isinstance(new_cache, dict)
            and new_cache["a.py"]["content_hash"] == "ha"  # type: ignore[index]
            and new_cache["a.py"]["corrupted"] is False  # type: ignore[index]
        )

    def warm_cache_scans_but_reports_nothing(function: Callable[..., dict[str, object]]) -> bool:
        cache: dict[str, dict[str, object]] = {}
        files = [{"path": "a.py", "content_hash": "ha", "issue_count": 4}]
        result = function(files, cache, _base_params(warm_cache=True))
        metrics = _metrics(result)
        new_cache = result.get("cache", {})
        return (
            metrics["cache_hits"] == 0
            and metrics["cache_misses"] == 1
            and metrics["reported_issues"] == []
            and metrics["total_reported_issues"] == 0
            and isinstance(new_cache, dict)
            and new_cache["a.py"]["issue_count"] == 4  # type: ignore[index]
        )

    def unlisted_cache_entries_preserved(function: Callable[..., dict[str, object]]) -> bool:
        cache = {
            "a.py": _base_cache_entry(),
            "z.py": _base_cache_entry(path="z.py", content_hash="hz", issue_count=8),
        }
        files = [{"path": "a.py", "content_hash": "ha", "issue_count": 99}]
        result = function(files, cache, _base_params())
        return result["cache"]["z.py"] == cache["z.py"]  # type: ignore[index]

    def no_input_mutation(function: Callable[..., dict[str, object]]) -> bool:
        cache = {"a.py": _base_cache_entry(content_hash="old")}
        files = [{"path": "a.py", "content_hash": "ha", "issue_count": 4}]
        params = _base_params()
        before = (copy.deepcopy(files), copy.deepcopy(cache), copy.deepcopy(params))
        function(files, cache, params)
        return before == (files, cache, params)

    def two_run_update_affects_next_run(function: Callable[..., dict[str, object]]) -> bool:
        params = _base_params(current_day=20)
        first_files = [{"path": "a.py", "content_hash": "ha2", "issue_count": 4}]
        first = function(first_files, {}, params)
        second = function(first_files, first["cache"], params)  # type: ignore[arg-type,index]
        first_metrics = _metrics(first)
        second_metrics = _metrics(second)
        return (
            first_metrics["cache_misses"] == 1
            and second_metrics["cache_hits"] == 1
            and second_metrics["cache_misses"] == 0
            and second_metrics["reported_issues"] == [["a.py", 4]]
        )

    def force_rescan_updates_cache_for_next_run(function: Callable[..., dict[str, object]]) -> bool:
        files = [{"path": "a.py", "content_hash": "ha2", "issue_count": 5}]
        cache = {"a.py": _base_cache_entry(content_hash="ha1", stored_day=10, issue_count=1)}
        params_force = _base_params(current_day=20, force_rescan=True)

        first = function(files, cache, params_force)
        first_metrics = _metrics(first)

        params_normal = _base_params(current_day=21, force_rescan=False)
        second = function(files, first["cache"], params_normal)  # type: ignore[arg-type,index]
        second_metrics = _metrics(second)

        return (
            first_metrics["cache_hits"] == 0
            and first_metrics["cache_misses"] == 1
            and first_metrics["invalidation_counts"] == {"force_rescan": 1}
            and first_metrics["reported_issues"] == [["a.py", 5]]
            and second_metrics["cache_hits"] == 1
            and second_metrics["cache_misses"] == 0
            and second_metrics["reported_issues"] == [["a.py", 5]]
        )

    def warm_cache_updates_then_next_run_hits(function: Callable[..., dict[str, object]]) -> bool:
        files = [{"path": "a.py", "content_hash": "ha", "issue_count": 7}]
        params_warm = _base_params(current_day=1, warm_cache=True)

        first = function(files, {}, params_warm)
        first_metrics = _metrics(first)

        params_normal = _base_params(current_day=2, warm_cache=False)
        second = function(files, first["cache"], params_normal)  # type: ignore[arg-type,index]
        second_metrics = _metrics(second)

        return (
            first_metrics["cache_hits"] == 0
            and first_metrics["cache_misses"] == 1
            and first_metrics["reported_issues"] == []
            and first_metrics["total_reported_issues"] == 0
            and second_metrics["cache_hits"] == 1
            and second_metrics["cache_misses"] == 0
            and second_metrics["reported_issues"] == [["a.py", 7]]
        )

    def force_rescan_with_warm_cache_reports_nothing(function: Callable[..., dict[str, object]]) -> bool:
        files = [{"path": "a.py", "content_hash": "ha2", "issue_count": 5}]
        cache = {"a.py": _base_cache_entry(content_hash="ha1", corrupted=True, issue_count=1)}
        result = function(files, cache, _base_params(current_day=20, force_rescan=True, warm_cache=True))
        metrics = _metrics(result)
        new_cache = result.get("cache", {})
        return (
            metrics["cache_hits"] == 0
            and metrics["cache_misses"] == 1
            and metrics["invalidation_counts"] == {"force_rescan": 1}
            and metrics["scanned_files"] == ["a.py"]
            and metrics["reported_issues"] == []
            and metrics["total_reported_issues"] == 0
            and isinstance(new_cache, dict)
            and new_cache["a.py"]["content_hash"] == "ha2"  # type: ignore[index]
            and new_cache["a.py"]["issue_count"] == 5  # type: ignore[index]
            and new_cache["a.py"]["corrupted"] is False  # type: ignore[index]
        )

    def corrupted_entry_repaired_then_next_run_hits(function: Callable[..., dict[str, object]]) -> bool:
        files = [{"path": "a.py", "content_hash": "ha", "issue_count": 6}]
        cache = {"a.py": _base_cache_entry(corrupted=True, issue_count=1)}
        params = _base_params(current_day=20)

        first = function(files, cache, params)
        first_metrics = _metrics(first)
        second = function(files, first["cache"], _base_params(current_day=21))  # type: ignore[arg-type,index]
        second_metrics = _metrics(second)

        return (
            first_metrics["cache_misses"] == 1
            and first_metrics["invalidation_counts"] == {"corrupted": 1}
            and second_metrics["cache_hits"] == 1
            and second_metrics["cache_misses"] == 0
            and second_metrics["reported_issues"] == [["a.py", 6]]
        )

    def hit_uses_cached_issue_count(function: Callable[..., dict[str, object]]) -> bool:
        cache = {"a.py": _base_cache_entry(issue_count=2)}
        files = [{"path": "a.py", "content_hash": "ha", "issue_count": 99}]
        metrics = _metrics(function(files, cache, _base_params()))
        return metrics["cache_hits"] == 1 and metrics["reported_issues"] == [["a.py", 2]]

    def reported_issues_sorted_and_excludes_zero(function: Callable[..., dict[str, object]]) -> bool:
        cache = {
            "b.py": _base_cache_entry(path="b.py", content_hash="hb", issue_count=0),
            "c.py": _base_cache_entry(path="c.py", content_hash="hc", issue_count=3),
        }
        files = [
            {"path": "d.py", "content_hash": "hd", "issue_count": 4},
            {"path": "b.py", "content_hash": "hb", "issue_count": 9},
            {"path": "a.py", "content_hash": "ha", "issue_count": 1},
            {"path": "c.py", "content_hash": "hc", "issue_count": 7},
        ]
        metrics = _metrics(function(files, cache, _base_params()))
        return (
            metrics["scanned_files"] == ["a.py", "d.py"]
            and metrics["reported_issues"] == [["a.py", 1], ["c.py", 3], ["d.py", 4]]
            and metrics["total_reported_issues"] == 8
        )

    def no_input_mutation_and_unlisted_preserved(function: Callable[..., dict[str, object]]) -> bool:
        cache = {
            "a.py": _base_cache_entry(content_hash="old"),
            "z.py": _base_cache_entry(path="z.py", content_hash="hz", issue_count=8),
        }
        files = [{"path": "a.py", "content_hash": "ha", "issue_count": 4}]
        params = _base_params()
        before = (copy.deepcopy(files), copy.deepcopy(cache), copy.deepcopy(params))
        result = function(files, cache, params)
        return before == (files, cache, params) and result["cache"]["z.py"] == cache["z.py"]  # type: ignore[index]

    return [
        {"name": "hit_and_not_cached", "check": hit_and_not_cached},
        {"name": "profile_changed", "check": single_reason("profile_changed", {"profile_hash": "old"})},
        {"name": "options_changed", "check": single_reason("options_changed", {"options_key": "old"})},
        {
            "name": "corrupted_beats_file_changed",
            "check": single_reason("corrupted", {"content_hash": "old", "corrupted": True}),
        },
        {"name": "force_rescan_beats_everything", "check": force_rescan_beats_everything},
        {"name": "force_rescan_with_warm_cache_reports_nothing", "check": force_rescan_with_warm_cache_reports_nothing},
        {"name": "warm_cache_scans_but_reports_nothing", "check": warm_cache_scans_but_reports_nothing},
        {"name": "warm_cache_updates_then_next_run_hits", "check": warm_cache_updates_then_next_run_hits},
        {"name": "corrupted_entry_repaired_then_next_run_hits", "check": corrupted_entry_repaired_then_next_run_hits},
        {"name": "hit_uses_cached_issue_count", "check": hit_uses_cached_issue_count},
        {"name": "reported_issues_sorted_and_excludes_zero", "check": reported_issues_sorted_and_excludes_zero},
        {"name": "no_input_mutation_and_unlisted_preserved", "check": no_input_mutation_and_unlisted_preserved},
    ]


_MICRO_REPO_CACHE_PATCH_CATEGORY_LABELS = {
    "basic_cache": "基础缓存",
    "cache_key": "缓存键",
    "priority": "优先级",
    "expiry": "过期规则",
    "warm_cache": "预热模式",
    "state": "状态保持",
}


_MICRO_REPO_CACHE_PATCH_CASE_TAGS = {
    "hit_and_not_cached": ("命中与未缓存", "basic_cache"),
    "config_changed": ("配置变更", "cache_key"),
    "profile_changed": ("profile 变更", "cache_key"),
    "options_changed": ("选项变更", "cache_key"),
    "corrupted_beats_file_changed": ("损坏优先于文件变更", "priority"),
    "force_rescan_beats_everything": ("强制重扫优先", "priority"),
    "force_rescan_with_warm_cache_reports_nothing": ("强制预热不报告", "priority"),
    "warm_cache_scans_but_reports_nothing": ("预热不报告", "warm_cache"),
    "warm_cache_updates_then_next_run_hits": ("预热写回后命中", "warm_cache"),
    "corrupted_entry_repaired_then_next_run_hits": ("损坏修复后命中", "state"),
    "hit_uses_cached_issue_count": ("命中使用缓存问题数", "basic_cache"),
    "reported_issues_sorted_and_excludes_zero": ("报告排序与过滤", "basic_cache"),
    "no_input_mutation_and_unlisted_preserved": ("输入不变与保留未列文件", "state"),
}


def _micro_repo_cache_patch_case_detail(case_id: str) -> dict[str, str]:
    label, category = _MICRO_REPO_CACHE_PATCH_CASE_TAGS.get(case_id, (case_id, "basic_cache"))
    return {
        "case_id": case_id,
        "label": label,
        "category": category,
        "category_label": _MICRO_REPO_CACHE_PATCH_CATEGORY_LABELS.get(category, category),
    }


_MICRO_REPO_CACHE_PATCH_INITIAL_SOURCE = '''from copy import deepcopy

from cache_policy import build_cache_entry, choose_invalidation_reason

REASON_ORDER = [
    "force_rescan",
    "not_cached",
    "corrupted",
    "file_changed",
    "config_changed",
    "profile_changed",
    "options_changed",
    "expired",
]


def run_scan(files, cache, params):
    """
    files: list of dicts with:
      path, content_hash, issue_count

    cache: dict mapping path -> cache entry

    params: dict with:
      current_day
      config_hash
      profile_name
      profile_hash
      options_key
      cache_expiry_days
      force_rescan
      warm_cache

    Returns:
      {
        "cache": new_cache,
        "metrics": {
          "cache_hits": int,
          "cache_misses": int,
          "invalidation_counts": dict,
          "scanned_files": list[str],
          "reported_issues": list[[path, issue_count]],
          "total_reported_issues": int
        }
      }
    """
    new_cache = deepcopy(cache)

    cache_hits = 0
    cache_misses = 0
    invalidation_counts = {}
    scanned_files = []
    reported_issues = []

    for f in files:
        path = f["path"]
        entry = new_cache.get(path)
        reason = choose_invalidation_reason(f, entry, params)

        if reason is None:
            cache_hits += 1
            issue_count = int(entry.get("issue_count", 0))
        else:
            cache_misses += 1
            invalidation_counts[reason] = invalidation_counts.get(reason, 0) + 1
            scanned_files.append(path)
            issue_count = int(f.get("issue_count", 0))

            new_cache[path] = build_cache_entry(f, params, issue_count)

        if issue_count > 0:
            reported_issues.append([path, issue_count])

    reported_issues.sort(key=lambda item: item[0])
    scanned_files.sort()

    return {
        "cache": new_cache,
        "metrics": {
            "cache_hits": cache_hits,
            "cache_misses": cache_misses,
            "invalidation_counts": invalidation_counts,
            "scanned_files": scanned_files,
            "reported_issues": reported_issues,
            "total_reported_issues": sum(item[1] for item in reported_issues),
        },
    }
'''


_MICRO_REPO_CACHE_PATCH_POLICY_SOURCE = '''REASON_ORDER = [
    "force_rescan",
    "not_cached",
    "corrupted",
    "file_changed",
    "config_changed",
    "profile_changed",
    "options_changed",
    "expired",
]


def choose_invalidation_reason(file_info, entry, params):
    expiry_days = params.get("cache_expiry_days", 7)

    if entry is None:
        return "not_cached"
    if entry.get("content_hash") != file_info.get("content_hash"):
        return "file_changed"
    if entry.get("corrupted"):
        return "corrupted"
    if params["current_day"] - entry.get("stored_day", 0) >= expiry_days:
        return "expired"

    if params.get("force_rescan"):
        return "force_rescan"

    return None


def build_cache_entry(file_info, params, issue_count):
    return {
        "path": file_info["path"],
        "content_hash": file_info["content_hash"],
        "config_hash": params["config_hash"],
        "profile_name": params["profile_name"],
        "profile_hash": params["profile_hash"],
        "options_key": params["options_key"],
        "stored_day": params["current_day"],
        "issue_count": issue_count,
        "corrupted": False,
    }
'''


_MICRO_REPO_CACHE_PATCH_INITIAL_FILES = {
    "cache_runner.py": _MICRO_REPO_CACHE_PATCH_INITIAL_SOURCE,
    "cache_policy.py": _MICRO_REPO_CACHE_PATCH_POLICY_SOURCE,
}


_MICRO_UNIFIED_DIFF_CATEGORY_LABELS = {
    "basic_editing": "基础编辑",
    "file_operations": "文件操作",
    "hunk_semantics": "hunk 边界",
    "state_order": "状态顺序",
    "malformed_atomic": "格式/原子性",
}


_MICRO_UNIFIED_DIFF_CASE_TAGS = {
    "single_line_replace": ("单行替换", "basic_editing"),
    "multiple_hunks_same_file": ("同文件多 hunk", "basic_editing"),
    "new_file": ("新建文件", "file_operations"),
    "delete_file": ("删除文件", "file_operations"),
    "empty_new_file": ("空文件创建", "file_operations"),
    "new_file_already_exists_must_fail": ("新文件已存在", "file_operations"),
    "delete_file_then_recreate_same_path": ("删除后重建", "file_operations"),
    "zero_old_count_insert_middle": ("零行插入", "hunk_semantics"),
    "hunk_after_prior_insertion_offset": ("插入后偏移", "hunk_semantics"),
    "hunk_after_prior_deletion_offset": ("删除后偏移", "hunk_semantics"),
    "hunk_count_mismatch_must_fail": ("hunk 旧计数错误", "hunk_semantics"),
    "new_count_mismatch_must_fail": ("hunk 新计数错误", "hunk_semantics"),
    "header_count_zero_delete_at_start": ("零行删除", "hunk_semantics"),
    "same_file_multiple_sections_sequential": ("同文件连续 section", "state_order"),
    "repeated_line_position": ("重复行定位", "state_order"),
    "overlapping_hunks_must_fail": ("重叠 hunk", "state_order"),
    "out_of_order_hunks_must_fail": ("hunk 倒序", "state_order"),
    "file_section_header_mismatch_must_fail": ("文件头不一致", "malformed_atomic"),
    "delete_file_with_addition_must_fail": ("删除文件含新增", "malformed_atomic"),
    "atomic_failure_after_create": ("创建后失败原子性", "malformed_atomic"),
}


def _micro_unified_diff_case_detail(case_id: str) -> dict[str, str]:
    label, category = _MICRO_UNIFIED_DIFF_CASE_TAGS.get(case_id, (case_id, "basic_editing"))
    return {
        "case_id": case_id,
        "label": label,
        "category": category,
        "category_label": _MICRO_UNIFIED_DIFF_CATEGORY_LABELS.get(category, category),
    }


def _micro_unified_diff_cases() -> list[dict[str, object]]:
    return [
        {
            "name": "single_line_replace",
            "files": {"app.py": "a = 1\nb = 2\nc = 3\n"},
            "diff": "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1,3 +1,3 @@\n a = 1\n-b = 2\n+b = 20\n c = 3\n",
            "expected": {"app.py": "a = 1\nb = 20\nc = 3\n"},
        },
        {
            "name": "multiple_hunks_same_file",
            "files": {"app.py": "a\nb\nc\nd\ne\nf\n"},
            "diff": "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\n@@ -4,3 +4,4 @@\n d\n e\n+E2\n f\n",
            "expected": {"app.py": "a\nB\nc\nd\ne\nE2\nf\n"},
        },
        {
            "name": "new_file",
            "files": {"app.py": "ok\n"},
            "diff": "diff --git a/new.py b/new.py\n--- /dev/null\n+++ b/new.py\n@@ -0,0 +1,2 @@\n+hello\n+world\n",
            "expected": {"app.py": "ok\n", "new.py": "hello\nworld\n"},
        },
        {
            "name": "delete_file",
            "files": {"old.py": "gone\nsoon\n", "keep.py": "keep\n"},
            "diff": "diff --git a/old.py b/old.py\n--- a/old.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-gone\n-soon\n",
            "expected": {"keep.py": "keep\n"},
        },
        {
            "name": "empty_new_file",
            "files": {},
            "diff": "diff --git a/empty.txt b/empty.txt\n--- /dev/null\n+++ b/empty.txt\n@@ -0,0 +0,0 @@\n",
            "expected": {"empty.txt": ""},
        },
        {
            "name": "new_file_already_exists_must_fail",
            "files": {"new.py": "already\n", "a.py": "ok\n"},
            "diff": "diff --git a/new.py b/new.py\n--- /dev/null\n+++ b/new.py\n@@ -0,0 +1,1 @@\n+created\n",
            "raises": True,
        },
        {
            "name": "delete_file_then_recreate_same_path",
            "files": {"a.txt": "old\n"},
            "diff": "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\ndiff --git a/a.txt b/a.txt\n--- /dev/null\n+++ b/a.txt\n@@ -0,0 +1,1 @@\n+new\n",
            "expected": {"a.txt": "new\n"},
        },
        {
            "name": "zero_old_count_insert_middle",
            "files": {"a.txt": "a\nb\nc\n"},
            "diff": "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -2,0 +3,2 @@\n+X\n+Y\n",
            "expected": {"a.txt": "a\nb\nX\nY\nc\n"},
        },
        {
            "name": "hunk_after_prior_insertion_offset",
            "files": {"a.txt": "a\nb\nc\nd\ne\nf\n"},
            "diff": "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1,2 +1,3 @@\n a\n+X\n b\n@@ -5,2 +6,2 @@\n e\n-f\n+F\n",
            "expected": {"a.txt": "a\nX\nb\nc\nd\ne\nF\n"},
        },
        {
            "name": "hunk_after_prior_deletion_offset",
            "files": {"a.txt": "a\nb\nc\nd\ne\nf\n"},
            "diff": "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1,3 +1,2 @@\n a\n-b\n c\n@@ -5,2 +4,2 @@\n e\n-f\n+F\n",
            "expected": {"a.txt": "a\nc\nd\ne\nF\n"},
        },
        {
            "name": "hunk_count_mismatch_must_fail",
            "files": {"a.txt": "a\nb\nc\n"},
            "diff": "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1,2 +1,2 @@\n a\n+B\n",
            "raises": True,
        },
        {
            "name": "new_count_mismatch_must_fail",
            "files": {"a.txt": "a\nb\n"},
            "diff": "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1,2 +1,3 @@\n a\n-b\n+B\n",
            "raises": True,
        },
        {
            "name": "header_count_zero_delete_at_start",
            "files": {"a.txt": "a\nb\nc\n"},
            "diff": "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1,2 +0,0 @@\n-a\n-b\n",
            "expected": {"a.txt": "c\n"},
        },
        {
            "name": "same_file_multiple_sections_sequential",
            "files": {"a.txt": "a\nb\nc\n"},
            "diff": "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\ndiff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1,3 +1,4 @@\n a\n B\n+X\n c\n",
            "expected": {"a.txt": "a\nB\nX\nc\n"},
        },
        {
            "name": "repeated_line_position",
            "files": {"a.txt": "x\nsame\nx\nsame\nx\n"},
            "diff": "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -3,3 +3,3 @@\n x\n-same\n+DIFF\n x\n",
            "expected": {"a.txt": "x\nsame\nx\nDIFF\nx\n"},
        },
        {
            "name": "overlapping_hunks_must_fail",
            "files": {"a.txt": "a\nb\nc\nd\n"},
            "diff": "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -2,2 +2,2 @@\n-b\n+B\n c\n@@ -3,2 +3,2 @@\n-c\n+C\n d\n",
            "raises": True,
        },
        {
            "name": "out_of_order_hunks_must_fail",
            "files": {"a.txt": "a\nb\nc\nd\n"},
            "diff": "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -3,1 +3,1 @@\n-c\n+C\n@@ -1,1 +1,1 @@\n-a\n+A\n",
            "raises": True,
        },
        {
            "name": "file_section_header_mismatch_must_fail",
            "files": {"a.txt": "a\n"},
            "diff": "diff --git a/a.txt b/a.txt\n--- a/other.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-a\n+A\n",
            "raises": True,
        },
        {
            "name": "delete_file_with_addition_must_fail",
            "files": {"a.txt": "a\n"},
            "diff": "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-a\n+new\n",
            "raises": True,
        },
        {
            "name": "atomic_failure_after_create",
            "files": {"a.txt": "a\n"},
            "diff": "diff --git a/new.txt b/new.txt\n--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1,1 @@\n+created\ndiff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-wrong\n+W\n",
            "raises": True,
        },
    ]


def _check_expression_24(text: str, grader: dict[str, object]) -> bool:
    stripped = text.strip()
    if not stripped:
        return False

    numbers = [int(item) for item in grader["numbers"]]  # type: ignore[index]
    target = Fraction(int(grader["target"]))  # type: ignore[index]
    allowed_operators = set(str(item) for item in grader["allowed_operators"])  # type: ignore[index]

    try:
        node = ast.parse(stripped, mode="eval")
    except SyntaxError:
        return False

    used_numbers: list[int] = []
    try:
        value = _eval_expression(node.body, used_numbers, allowed_operators)
    except ValueError:
        return False
    except ZeroDivisionError:
        return False

    if sorted(used_numbers) != sorted(numbers):
        return False
    return value == target


def _eval_expression(
    node: ast.AST,
    used_numbers: list[int],
    allowed_operators: set[str],
) -> Fraction:
    if isinstance(node, ast.BinOp):
        left = _eval_expression(node.left, used_numbers, allowed_operators)
        right = _eval_expression(node.right, used_numbers, allowed_operators)
        if isinstance(node.op, ast.Add):
            _require_operator("+", allowed_operators)
            return left + right
        if isinstance(node.op, ast.Sub):
            _require_operator("-", allowed_operators)
            return left - right
        if isinstance(node.op, ast.Mult):
            _require_operator("*", allowed_operators)
            return left * right
        if isinstance(node.op, ast.Div):
            _require_operator("/", allowed_operators)
            return left / right
        raise ValueError("unsupported operator")

    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.USub):
            return -_eval_expression(node.operand, used_numbers, allowed_operators)
        raise ValueError("unsupported unary operator")

    if isinstance(node, ast.Constant):
        if not isinstance(node.value, int) or isinstance(node.value, bool):
            raise ValueError("non-integer constant")
        if node.value < 0:
            raise ValueError("negative constant")
        if node.value >= 10:
            raise ValueError("digit concatenation")
        used_numbers.append(node.value)
        return Fraction(node.value)

    raise ValueError(f"unsupported syntax: {type(node).__name__}")


def _require_operator(operator: str, allowed_operators: set[str]) -> None:
    if operator not in allowed_operators:
        raise ValueError(f"operator not allowed: {operator}")
