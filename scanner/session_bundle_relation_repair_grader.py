from __future__ import annotations

import copy
import json
from typing import Any, Mapping

from .bounded_json import BoundedJSONError, bounded_json_loads


TEST_SUITE = "session_bundle_relation_repair_v1"
CANDIDATE_ID = "q1_session_bundle_repair_isolated_contract_v64"
MAX_SCORE = 20

START_IDS = (
    "p02",
    "p06",
    "p05",
    "p09",
    "p11",
    "p14",
    "p15",
    "p16",
    "p17",
    "p19",
    "p20",
    "p23",
    "p24",
    "p27",
    "p29",
    "p30",
    "p32",
    "p37",
)
ALTERNATIVE_IDS = (
    "p01",
    "p03",
    "p04",
    "p07",
    "p08",
    "p12",
    "p18",
    "p22",
    "p25",
    "p33",
    "p35",
    "p36",
)
DRAFT_ORDER = tuple(f"e{index}" for index in range(1, 12))
PROPOSAL_ORDER = tuple(
    f"p{index:02d}"
    for index in range(1, 38)
    if f"p{index:02d}" in set(START_IDS) | set(ALTERNATIVE_IDS)
)

ROOT_ORDER = (
    "consumes_despite_existing_target",
    "snapshots_metadata_after_events",
    "late_event_snapshot",
    "skips_recursive_mapping_normalization",
    "overconsumes_event_limit",
    "deletes_target_on_validation_failure",
    "deletes_target_on_iteration_failure",
    "deletes_target_on_serialization_failure",
    "deletes_target_on_member_size_failure",
    "leaves_temp_file",
    "clobbers_racing_target",
    "preserves_mapping_insertion_order",
    "uses_wall_clock_zip_timestamps",
    "skips_temporary_archive_fsync",
    "skips_parent_directory_fsync",
    "uses_recorded_replay_result",
    "ignores_stop_on_error",
    "always_stops_on_failure",
    "hardcodes_store_history",
    "leaks_history_state",
)
ROOT_DETAILS = {
    "consumes_despite_existing_target": ("拒绝覆盖优先级", "snapshot"),
    "snapshots_metadata_after_events": ("元数据预消费快照", "snapshot"),
    "late_event_snapshot": ("逐事件快照时机", "snapshot"),
    "skips_recursive_mapping_normalization": ("递归映射归一化", "snapshot"),
    "overconsumes_event_limit": ("事件上限与停止消费", "snapshot"),
    "deletes_target_on_validation_failure": ("校验失败原子性", "atomicity"),
    "deletes_target_on_iteration_failure": ("迭代失败原子性", "atomicity"),
    "deletes_target_on_serialization_failure": ("序列化失败原子性", "atomicity"),
    "deletes_target_on_member_size_failure": ("成员超限原子性", "atomicity"),
    "leaves_temp_file": ("替换失败原子性", "atomicity"),
    "clobbers_racing_target": ("无覆盖提交竞态", "atomicity"),
    "preserves_mapping_insertion_order": ("逻辑输入确定性编码", "durability"),
    "uses_wall_clock_zip_timestamps": ("归档成员顺序与时间戳", "durability"),
    "skips_temporary_archive_fsync": ("临时归档先落盘", "durability"),
    "skips_parent_directory_fsync": ("提交后目录落盘", "durability"),
    "uses_recorded_replay_result": ("实际结果写入回放输出", "replay"),
    "ignores_stop_on_error": ("按实际失败停止", "replay"),
    "always_stops_on_failure": ("实际失败后继续", "replay"),
    "hardcodes_store_history": ("历史参数透传", "replay"),
    "leaks_history_state": ("历史状态逐次与异常恢复", "replay"),
}

SNAPSHOT_ROOTS = frozenset(ROOT_ORDER[:5])
FAULT_ROOTS = frozenset(ROOT_ORDER[5:10])
DURABILITY_ROOTS = frozenset(ROOT_ORDER[11:15])
REPLAY_ROOTS = frozenset(ROOT_ORDER[15:])
CATEGORY_SPECS = (
    ("snapshot", "输入与快照", 4),
    ("atomicity", "提交原子性", 5),
    ("durability", "确定性与持久化", 4),
    ("replay", "回放状态", 4),
    ("portfolio", "组合覆盖", 3),
)


def _proposal(
    draft: str,
    relation: tuple[str, str, str],
    *roots: str,
    valid: bool = True,
) -> dict[str, object]:
    return {
        "draft": draft,
        "relation": relation,
        "valid": valid,
        "roots": roots,
    }


# This table is the frozen v64 relation bank evaluated against the fixed v64 drafts.
# It is intentionally independent of the development candidate modules.
PROPOSALS = {
    "p01": _proposal("e1", ("b.events_consumed", "!=", "i.events_consumed"), "consumes_despite_existing_target"),
    "p02": _proposal("e1", ("b.status", "!=", "i.status")),
    "p03": _proposal("e1", ("b.target", "!=", "i.target")),
    "p04": _proposal("e2", ("b.metadata_snapshot", "=", "i.metadata_snapshot"), "snapshots_metadata_after_events"),
    "p05": _proposal("e2", ("b.status", "=", "i.status"), "skips_recursive_mapping_normalization"),
    "p06": _proposal("e2", ("b.metadata_snapshot", "!=", "i.metadata_snapshot"), valid=False),
    "p07": _proposal("e2", ("b.nested_snapshot", "=", "i.nested_snapshot"), valid=False),
    "p08": _proposal("e3", ("b.event_snapshot", "=", "i.event_snapshot"), "late_event_snapshot"),
    "p09": _proposal("e3", ("b.status", "=", "i.status")),
    "p11": _proposal("e4", ("b.events_consumed", "=", "i.events_consumed"), "overconsumes_event_limit"),
    "p12": _proposal("e4", ("b.status", "!=", "i.status")),
    "p14": _proposal("e5", ("b.faults.validation.filesystem", "=", "b.before.filesystem"), "deletes_target_on_validation_failure"),
    "p15": _proposal("e5", ("b.faults.iteration.filesystem", "=", "b.before.filesystem"), "deletes_target_on_iteration_failure"),
    "p16": _proposal("e5", ("b.faults.serialization.filesystem", "=", "b.before.filesystem"), "deletes_target_on_serialization_failure"),
    "p17": _proposal("e5", ("b.faults.member_size.filesystem", "=", "b.before.filesystem"), "deletes_target_on_member_size_failure"),
    "p18": _proposal("e5", ("b.faults.replace.filesystem", "=", "b.before.filesystem"), "leaves_temp_file"),
    "p19": _proposal("e5", ("b.status", "!=", "i.status")),
    "p20": _proposal("e6", ("b.status", "!=", "i.status"), "clobbers_racing_target"),
    "p22": _proposal("e6", ("b.target", "!=", "i.target"), "clobbers_racing_target"),
    "p23": _proposal("e7", ("b.archive.mapping_order", "=", "i.archive.mapping_order"), "preserves_mapping_insertion_order"),
    "p24": _proposal("e7", ("b.status", "=", "i.status")),
    "p25": _proposal("e7", ("b.durability.temporary_fsync", "=", "b.durability.parent_fsync_attempted"), "skips_temporary_archive_fsync", "skips_parent_directory_fsync"),
    "p27": _proposal("e8", ("b.archive.timestamp", "=", "i.archive.timestamp"), "uses_wall_clock_zip_timestamps"),
    "p29": _proposal("e9", ("b.outcomes", "=", "i.outcomes"), "uses_recorded_replay_result"),
    "p30": _proposal("e9", ("b.final_execution_count", "=", "i.final_execution_count")),
    "p32": _proposal("e10", ("b.outcomes", "!=", "i.outcomes"), "uses_recorded_replay_result", "ignores_stop_on_error", "always_stops_on_failure"),
    "p33": _proposal("e10", ("b.call_start_counts", "!=", "i.call_start_counts"), "uses_recorded_replay_result", "ignores_stop_on_error", "always_stops_on_failure"),
    "p35": _proposal("e11", ("b.store_history_calls", "!=", "i.store_history_calls"), "hardcodes_store_history"),
    "p36": _proposal("e11", ("b.call_start_counts", "!=", "i.call_start_counts"), "leaks_history_state"),
    "p37": _proposal("e11", ("b.final_execution_count", "!=", "i.final_execution_count"), "leaks_history_state"),
}


class SubmissionError(ValueError):
    pass


def _unscored(failure_summary: str) -> dict[str, object]:
    return {
        "protocol_version": "backend-grade-v1",
        "candidate_id": CANDIDATE_ID,
        "status": "invalid_submission",
        "score": None,
        "max_score": MAX_SCORE,
        "checks": [],
        "root_causes": [],
        "canonical_root_causes": [],
        "canonical_root_score": None,
        "experiment_reports": [],
        "invalid_relations": [],
        "coverage_metrics": {},
        "failure_summary": failure_summary,
        "budget": {},
        "submitted_budget": {},
        "scored_budget": {},
        "budget_violations": [],
        "ignored_excess_relations": [],
        "repairs": [],
    }


def _parse_replacements(payload: object) -> tuple[set[str], list[list[str]]]:
    if not isinstance(payload, dict) or set(payload) != {"replace"}:
        raise SubmissionError("payload_must_contain_only_replace")
    replacements = payload["replace"]
    if not isinstance(replacements, list) or len(replacements) > 6:
        raise SubmissionError("replace_must_be_array_of_at_most_six")

    start = set(START_IDS)
    alternatives = set(ALTERNATIVE_IDS)
    old_ids: set[str] = set()
    new_ids: set[str] = set()
    normalized: list[list[str]] = []
    for pair in replacements:
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or not all(isinstance(item, str) for item in pair)
        ):
            raise SubmissionError("replacement_must_be_two_ids")
        old_id, new_id = pair
        if (
            old_id not in start
            or old_id in old_ids
            or new_id not in alternatives
            or new_id in new_ids
        ):
            raise SubmissionError("invalid_or_duplicate_replacement")
        old_ids.add(old_id)
        new_ids.add(new_id)
        normalized.append([old_id, new_id])

    selected = (start - old_ids) | new_ids
    if len(selected) != 18:
        raise SubmissionError("replacement_created_invalid_final_set")
    return selected, normalized


def _selection_groups(selected: set[str]) -> list[dict[str, object]]:
    groups: list[dict[str, object]] = []
    for draft in DRAFT_ORDER:
        ids = [
            proposal_id
            for proposal_id in PROPOSAL_ORDER
            if proposal_id in selected and PROPOSALS[proposal_id]["draft"] == draft
        ]
        if ids:
            groups.append({"n": draft, "ids": ids})
    return groups


def _root_evidence(
    groups: list[dict[str, object]],
) -> tuple[set[str], dict[str, dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    killed: set[str] = set()
    witnesses: dict[str, dict[str, object]] = {}
    invalid: list[dict[str, object]] = []
    reports: list[dict[str, object]] = []
    for group in groups:
        draft = str(group["n"])
        ids = [str(item) for item in group["ids"]]
        valid_count = 0
        invalid_count = 0
        for relation_index, proposal_id in enumerate(ids):
            proposal = PROPOSALS[proposal_id]
            if proposal["valid"] is not True:
                invalid_count += 1
                invalid.append(
                    {
                        "experiment": draft,
                        "relation": relation_index,
                        "proposal_id": proposal_id,
                        "reason": "reference_relation_not_satisfied",
                    }
                )
                continue
            valid_count += 1
            for root in proposal["roots"]:
                root_id = str(root)
                killed.add(root_id)
                witnesses.setdefault(
                    root_id,
                    {
                        "experiment": draft,
                        "relation": relation_index,
                        "proposal_id": proposal_id,
                        "reason": "declared_relation_broken",
                    },
                )
        reports.append(
            {
                "name": draft,
                "changed_fields": ["fixed_delta"],
                "valid_relation_count": valid_count,
                "invalid_relation_count": invalid_count,
            }
        )
    return killed, witnesses, invalid, reports


def _check(
    check_id: str,
    label: str,
    category: str,
    passed: bool,
    evidence: list[str],
) -> dict[str, object]:
    return {
        "id": check_id,
        "label": label,
        "category": category,
        "points": 1 if passed else 0,
        "max_points": 1,
        "passed": passed,
        "evidence": evidence,
    }


def _scoring_checks(
    killed: set[str], witnesses: Mapping[str, Mapping[str, object]]
) -> tuple[list[dict[str, object]], dict[str, object]]:
    def has(*roots: str) -> bool:
        return all(root in killed for root in roots)

    fault_kills = killed & FAULT_ROOTS
    fault_witness_experiments = {
        str(witnesses[root]["experiment"])
        for root in fault_kills
        if root in witnesses and witnesses[root].get("experiment") is not None
    }
    compact_fault_breadth = (
        len(fault_kills) >= 3 and len(fault_witness_experiments) <= 2
    )
    early_fault_pair = has(
        "deletes_target_on_validation_failure",
        "deletes_target_on_iteration_failure",
    )
    encoding_fault_pair = has(
        "deletes_target_on_serialization_failure",
        "deletes_target_on_member_size_failure",
    )
    replace_cleanup = has("leaves_temp_file")
    racing_writer = has("clobbers_racing_target")
    atomic_core_count = sum(
        (early_fault_pair, encoding_fault_pair, replace_cleanup, racing_writer)
    )
    snapshot_count = len(killed & SNAPSHOT_ROOTS)
    durability_count = len(killed & DURABILITY_ROOTS)
    replay_count = len(killed & REPLAY_ROOTS)

    checks = [
        _check("snapshot_reject_priority", "拒绝覆盖优先级", "snapshot", has("consumes_despite_existing_target"), ["consumes_despite_existing_target"]),
        _check("snapshot_recursive_capture", "元数据时机与递归归一化", "snapshot", has("snapshots_metadata_after_events", "skips_recursive_mapping_normalization"), ["snapshots_metadata_after_events", "skips_recursive_mapping_normalization"]),
        _check("snapshot_event_yield", "逐事件快照时机", "snapshot", has("late_event_snapshot"), ["late_event_snapshot"]),
        _check("snapshot_event_limit", "事件上限与停止消费", "snapshot", has("overconsumes_event_limit"), ["overconsumes_event_limit"]),
        _check("atomicity_early_fault_pair", "前置故障原子性", "atomicity", early_fault_pair, ["deletes_target_on_validation_failure", "deletes_target_on_iteration_failure"]),
        _check("atomicity_encoding_fault_pair", "编码故障原子性", "atomicity", encoding_fault_pair, ["deletes_target_on_serialization_failure", "deletes_target_on_member_size_failure"]),
        _check("atomicity_replace_cleanup", "替换失败清理", "atomicity", replace_cleanup, ["leaves_temp_file"]),
        _check("atomicity_racing_writer", "无覆盖提交竞态", "atomicity", racing_writer, ["clobbers_racing_target"]),
        _check("atomicity_compact_fault_breadth", "紧凑故障覆盖", "atomicity", compact_fault_breadth, sorted(fault_kills)),
        _check("durability_mapping_order", "逻辑输入确定性编码", "durability", has("preserves_mapping_insertion_order"), ["preserves_mapping_insertion_order"]),
        _check("durability_fixed_timestamp", "归档成员顺序与时间戳", "durability", has("uses_wall_clock_zip_timestamps"), ["uses_wall_clock_zip_timestamps"]),
        _check("durability_temporary_fsync", "临时归档先落盘", "durability", has("skips_temporary_archive_fsync"), ["skips_temporary_archive_fsync"]),
        _check("durability_parent_fsync", "提交后目录落盘", "durability", has("skips_parent_directory_fsync"), ["skips_parent_directory_fsync"]),
        _check("replay_actual_result", "实际结果写入回放输出", "replay", has("uses_recorded_replay_result"), ["uses_recorded_replay_result"]),
        _check("replay_stop_policy", "失败停止策略", "replay", has("ignores_stop_on_error", "always_stops_on_failure"), ["ignores_stop_on_error", "always_stops_on_failure"]),
        _check("replay_history_forwarding", "历史参数透传", "replay", has("hardcodes_store_history"), ["hardcodes_store_history"]),
        _check("replay_history_restoration", "历史状态逐次与异常恢复", "replay", has("leaks_history_state"), ["leaks_history_state"]),
        _check("portfolio_snapshot_breadth", "输入捕获覆盖", "portfolio", snapshot_count >= 4, sorted(killed & SNAPSHOT_ROOTS)),
        _check("portfolio_durable_save_breadth", "保存路径跨域覆盖", "portfolio", atomic_core_count >= 2 and durability_count >= 2, sorted((killed & FAULT_ROOTS) | (killed & DURABILITY_ROOTS))),
        _check("portfolio_end_to_end_breadth", "端到端组合覆盖", "portfolio", snapshot_count >= 3 and len(fault_kills) >= 3 and durability_count >= 2 and replay_count >= 4, sorted(killed)),
    ]
    metrics = {
        "canonical_root_score": len(killed),
        "snapshot_roots": snapshot_count,
        "fault_roots": len(fault_kills),
        "fault_witness_experiments": sorted(fault_witness_experiments),
        "atomic_core_checks": atomic_core_count,
        "durability_roots": durability_count,
        "replay_roots": replay_count,
    }
    return checks, metrics


def _category_counts(checks: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {
        category: {
            "label": label,
            "score": sum(
                int(item["points"])
                for item in checks
                if item["category"] == category
            ),
            "max_score": maximum,
        }
        for category, label, maximum in CATEGORY_SPECS
    }


def grade_response(raw_answer: str) -> dict[str, object]:
    try:
        payload = bounded_json_loads(raw_answer, strip_code_fence=True)
        selected, repairs = _parse_replacements(payload)
    except (BoundedJSONError, json.JSONDecodeError, SubmissionError, TypeError, ValueError) as exc:
        return _unscored(f"{type(exc).__name__}:{exc}")

    groups = _selection_groups(selected)
    killed, witnesses, invalid, reports = _root_evidence(groups)
    checks, metrics = _scoring_checks(killed, witnesses)
    coverage_score = sum(int(item["points"]) for item in checks)
    validity_penalty = len(invalid)
    score = max(0, coverage_score - validity_penalty)
    canonical_roots = [
        {
            "id": root,
            "label": ROOT_DETAILS[root][0],
            "category": ROOT_DETAILS[root][1],
            "points": 1 if root in killed else 0,
            "max_points": 1,
            "passed": root in killed,
            "context_killed_variants": [],
            "context_variants_scored": False,
        }
        for root in ROOT_ORDER
    ]
    budget = {
        "experiments": len(groups),
        "relations": len(selected),
        "fault_observations": 5 if any(group["n"] == "e5" for group in groups) else 0,
    }
    return {
        "protocol_version": "backend-grade-v1",
        "candidate_id": CANDIDATE_ID,
        "status": "scored",
        "score": score,
        "max_score": MAX_SCORE,
        "checks": [
            {"id": item["id"], "passed": item["passed"]} for item in checks
        ],
        "root_causes": checks,
        "canonical_root_causes": canonical_roots,
        "canonical_root_score": len(killed),
        "categories": _category_counts(checks),
        "experiment_reports": reports,
        "invalid_relations": invalid,
        "killed_by_experiment": copy.deepcopy(witnesses),
        "coverage_metrics": metrics,
        "coverage_score": coverage_score,
        "validity_penalty": validity_penalty,
        "validity_metrics": {
            "invalid_relation_count": validity_penalty,
            "covered_root_count": len(killed),
        },
        "failure_summary": "",
        "budget": budget,
        "submitted_budget": budget,
        "scored_budget": budget,
        "budget_violations": [],
        "ignored_excess_relations": [],
        "repairs": repairs,
        "repair_count": len(repairs),
        "final_selection": {"x": groups},
    }


__all__ = [
    "ALTERNATIVE_IDS",
    "CANDIDATE_ID",
    "MAX_SCORE",
    "PROPOSALS",
    "START_IDS",
    "TEST_SUITE",
    "grade_response",
]
