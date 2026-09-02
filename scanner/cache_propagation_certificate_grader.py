from __future__ import annotations

import copy
import json
from typing import Any, Iterable, Mapping

from .bounded_json import BoundedJSONError, bounded_json_loads


MAX_SCORE = 20
MAX_ANSWER_BYTES = 24_000
PORTFOLIOS = 2
CASES_PER_PORTFOLIO = 4
MIN_FILES_PER_CASE = 3
MAX_FILES_PER_CASE = 5
MIN_CACHE_ENTRIES = 3
MAX_CACHE_ENTRIES = 7

TOP_LEVEL_FIELDS = {"portfolios", "audit"}
PORTFOLIO_FIELDS = {"name", "cases"}
CASE_FIELDS = {
    "id",
    "duration",
    "environment",
    "priority",
    "requires",
    "files",
    "cache",
    "scans",
    "params",
    "outcome",
}
PARAM_FIELDS = {
    "current_day",
    "expiry_days",
    "config_hash",
    "profile_hash",
    "options_key",
    "force",
    "warm",
    "atomic",
    "capacity",
}
OUTCOME_FIELDS = {
    "committed",
    "decisions",
    "writes",
    "kept",
    "evicted",
    "counts",
    "failed",
    "reported",
}
COUNT_FIELDS = {"hits", "misses", "reasons"}
AUDIT_ROW_FIELDS = {"step", "outcome"}
MISS_REASONS = {
    "force_rescan",
    "not_cached",
    "corrupted",
    "file_changed",
    "config_changed",
    "profile_changed",
    "options_changed",
    "expired",
}

BEHAVIOR_MUTANTS = (
    "priority_config_before_corrupted",
    "entry_path_mismatch_ignored",
    "expiry_boundary_inclusive",
    "priority_corrupted_before_force",
    "hit_not_touched",
    "successful_miss_keeps_stale_content_hash",
    "failed_miss_drops_old_entry",
    "atomic_failure_commits",
    "non_atomic_failure_rolls_back",
    "rollback_applies_eviction",
    "eviction_newest_first",
    "eviction_can_remove_current",
    "warm_reports",
    "invalidation_counts_collapse_duplicates",
)
BEHAVIOR_GROUPS = {
    "invalidation": BEHAVIOR_MUTANTS[:4],
    "state": BEHAVIOR_MUTANTS[4:7],
    "transaction": BEHAVIOR_MUTANTS[7:10],
    "eviction": BEHAVIOR_MUTANTS[10:12],
    "metrics": BEHAVIOR_MUTANTS[12:],
}
MUTANT_OUTCOME_FACET = {
    "priority_config_before_corrupted": "decisions",
    "entry_path_mismatch_ignored": "decisions",
    "expiry_boundary_inclusive": "decisions",
    "priority_corrupted_before_force": "decisions",
    "hit_not_touched": "writes",
    "successful_miss_keeps_stale_content_hash": "writes",
    "failed_miss_drops_old_entry": "preservation",
    "atomic_failure_commits": "transaction",
    "non_atomic_failure_rolls_back": "transaction",
    "rollback_applies_eviction": "preservation",
    "eviction_newest_first": "reporting_eviction",
    "eviction_can_remove_current": "preservation",
    "warm_reports": "reporting_eviction",
    "invalidation_counts_collapse_duplicates": "scan_metrics",
}
BASE_CERTIFICATE_FACETS = (
    "decisions",
    "state",
    "transaction",
    "eviction",
    "counters",
    "reporting",
)
CERTIFICATE_FACETS = (
    "decisions",
    "state",
    "transaction",
    "eviction",
    "metrics",
    "end_to_end",
)
BEHAVIOR_CERTIFICATE_GATE = {
    "priority_config_before_corrupted": "decisions",
    "entry_path_mismatch_ignored": "decisions",
    "expiry_boundary_inclusive": "decisions",
    "priority_corrupted_before_force": "decisions",
    "hit_not_touched": "state",
    "successful_miss_keeps_stale_content_hash": "state",
    "failed_miss_drops_old_entry": "state",
    "atomic_failure_commits": "transaction",
    "non_atomic_failure_rolls_back": "transaction",
    "rollback_applies_eviction": "transaction",
    "eviction_newest_first": "eviction",
    "eviction_can_remove_current": "eviction",
    "invalidation_counts_collapse_duplicates": "counters",
    "warm_reports": "reporting",
}
COVERAGE_REQUIREMENTS = {
    facet: tuple(
        mutant
        for mutant, required_facet in BEHAVIOR_CERTIFICATE_GATE.items()
        if required_facet == facet
    )
    for facet in BASE_CERTIFICATE_FACETS
}
INTERACTION_FIELDS = (
    "hit",
    "eviction",
    "atomic_failed_scan",
    "non_atomic_failed_scan",
    "semantic_atom_minimum",
)
INTERACTION_BEHAVIOR_GATES = {
    "hit": ("hit_not_touched",),
    "eviction": ("eviction_newest_first", "eviction_can_remove_current"),
    "atomic_failed_scan": ("atomic_failure_commits", "rollback_applies_eviction"),
    "non_atomic_failed_scan": ("non_atomic_failure_rolls_back",),
    "semantic_atom_minimum": (),
}
INTERACTION_CERTIFICATE_GATES = {
    "hit": ("state",),
    "eviction": ("eviction",),
    "atomic_failed_scan": ("transaction",),
    "non_atomic_failed_scan": ("transaction",),
    "semantic_atom_minimum": ("end_to_end",),
}
STEP_IDS = ("transaction_split", "branch_followup", "eviction_tail")

ATOM_NAMES = {
    "hit",
    "reason:force_rescan",
    "reason:not_cached",
    "reason:corrupted",
    "reason:file_changed",
    "reason:config_changed",
    "reason:profile_changed",
    "reason:options_changed",
    "reason:expired",
    "scan_failed",
    "commit",
    "rollback",
    "write",
    "preserve",
    "remove",
    "evict",
    "reported",
    "silent",
}

AUDIT_INPUT: dict[str, Any] = {
    "initial_cache": {
        "src/a.py": {
            "path": "src/a.py",
            "content_hash": "a-v1",
            "config_hash": "cfg-live",
            "profile_hash": "profile-live",
            "options_key": "opts-live",
            "stored_day": 5,
            "last_used_day": 4,
            "issue_count": 2,
            "corrupted": False,
        },
        "src/b.py": {
            "path": "src/b.py",
            "content_hash": "b-v0",
            "config_hash": "cfg-old",
            "profile_hash": "profile-live",
            "options_key": "opts-live",
            "stored_day": 7,
            "last_used_day": 3,
            "issue_count": 1,
            "corrupted": True,
        },
        "src/c.py": {
            "path": "archive/c.py",
            "content_hash": "c-v1",
            "config_hash": "cfg-live",
            "profile_hash": "profile-live",
            "options_key": "opts-live",
            "stored_day": 8,
            "last_used_day": 0,
            "issue_count": 7,
            "corrupted": False,
        },
        "legacy/x.py": {
            "path": "legacy/x.py",
            "content_hash": "x-v1",
            "config_hash": "cfg-old",
            "profile_hash": "profile-old",
            "options_key": "opts-old",
            "stored_day": 1,
            "last_used_day": 1,
            "issue_count": 0,
            "corrupted": False,
        },
        "legacy/y.py": {
            "path": "legacy/y.py",
            "content_hash": "y-v1",
            "config_hash": "cfg-old",
            "profile_hash": "profile-old",
            "options_key": "opts-old",
            "stored_day": 2,
            "last_used_day": 2,
            "issue_count": 0,
            "corrupted": False,
        },
    },
    "steps": [
        {
            "id": "transaction_split",
            "files": [
                {"path": "src/a.py", "content_hash": "a-v1"},
                {"path": "src/b.py", "content_hash": "b-v1"},
                {"path": "src/c.py", "content_hash": "c-v1"},
                {"path": "src/d.py", "content_hash": "d-v1"},
            ],
            "scans": {
                "src/a.py": {"ok": True, "issue_count": 2},
                "src/b.py": {"ok": False, "issue_count": 0},
                "src/c.py": {"ok": True, "issue_count": 6},
                "src/d.py": {"ok": True, "issue_count": 4},
            },
            "params": {
                "current_day": 10,
                "expiry_days": 5,
                "config_hash": "cfg-live",
                "profile_hash": "profile-live",
                "options_key": "opts-live",
                "force": False,
                "warm": False,
                "atomic": True,
                "capacity": 6,
            },
        },
        {
            "id": "branch_followup",
            "files": [
                {"path": "src/b.py", "content_hash": "b-v1"},
                {"path": "src/d.py", "content_hash": "d-v1"},
                {"path": "src/e.py", "content_hash": "e-v1"},
            ],
            "scans": {
                "src/b.py": {"ok": True, "issue_count": 5},
                "src/d.py": {"ok": True, "issue_count": 9},
                "src/e.py": {"ok": True, "issue_count": 3},
            },
            "params": {
                "current_day": 11,
                "expiry_days": 5,
                "config_hash": "cfg-live",
                "profile_hash": "profile-live",
                "options_key": "opts-live",
                "force": False,
                "warm": False,
                "atomic": False,
                "capacity": 6,
            },
        },
        {
            "id": "eviction_tail",
            "files": [
                {"path": "src/d.py", "content_hash": "d-v1"},
                {"path": "src/e.py", "content_hash": "e-v1"},
                {"path": "src/f.py", "content_hash": "f-v1"},
            ],
            "scans": {
                "src/d.py": {"ok": True, "issue_count": 4},
                "src/e.py": {"ok": True, "issue_count": 3},
                "src/f.py": {"ok": True, "issue_count": 8},
            },
            "params": {
                "current_day": 12,
                "expiry_days": 5,
                "config_hash": "cfg-live",
                "profile_hash": "profile-live",
                "options_key": "opts-live",
                "force": False,
                "warm": True,
                "atomic": False,
                "capacity": 5,
            },
        },
    ],
}


class CandidateError(ValueError):
    pass


def _plain_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise CandidateError(f"{field} must be a non-empty string")
    return value


def _integer(value: object, field: str) -> int:
    if not _plain_int(value):
        raise CandidateError(f"{field} must be an integer")
    return int(value)


def _normalize_files(raw: object) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        raise CandidateError("case.files must be a list")
    files: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in raw:
        if not isinstance(row, list) or len(row) != 2:
            raise CandidateError("case.files rows are invalid")
        path = _identifier(row[0], "case.files path")
        if path in seen:
            raise CandidateError("case.files contains duplicate paths")
        files.append({"path": path, "content_hash": _identifier(row[1], "case.files hash")})
        seen.add(path)
    return files


def _normalize_cache(raw: object) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, list):
        raise CandidateError("case.cache must be a list")
    cache: dict[str, dict[str, Any]] = {}
    for row in raw:
        if not isinstance(row, list) or len(row) != 10:
            raise CandidateError("case.cache rows are invalid")
        lookup_path = _identifier(row[0], "case.cache lookup path")
        if lookup_path in cache:
            raise CandidateError("case.cache contains duplicate lookup paths")
        if not isinstance(row[9], bool):
            raise CandidateError("case.cache corrupted must be a boolean")
        cache[lookup_path] = {
            "path": _identifier(row[1], "case.cache saved path"),
            "content_hash": _identifier(row[2], "case.cache content hash"),
            "config_hash": _identifier(row[3], "case.cache config hash"),
            "profile_hash": _identifier(row[4], "case.cache profile hash"),
            "options_key": _identifier(row[5], "case.cache options key"),
            "stored_day": _integer(row[6], "case.cache stored day"),
            "last_used_day": _integer(row[7], "case.cache last-used day"),
            "issue_count": _integer(row[8], "case.cache issue count"),
            "corrupted": bool(row[9]),
        }
    return cache


def _normalize_scans(raw: object, current_paths: set[str]) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, list):
        raise CandidateError("case.scans must be a list")
    scans: dict[str, dict[str, Any]] = {}
    for row in raw:
        if not isinstance(row, list) or len(row) != 3:
            raise CandidateError("case.scans rows are invalid")
        path = _identifier(row[0], "case.scans path")
        if path in scans:
            raise CandidateError("case.scans contains duplicate paths")
        if not isinstance(row[1], bool):
            raise CandidateError("case.scans ok must be a boolean")
        scans[path] = {
            "ok": bool(row[1]),
            "issue_count": _integer(row[2], "case.scans issue count"),
        }
    if set(scans) != current_paths:
        raise CandidateError("case.scans must identify every current file exactly once")
    return scans


def _normalize_params(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != PARAM_FIELDS:
        raise CandidateError("case.params fields are invalid")
    for field in ("force", "warm", "atomic"):
        if not isinstance(raw[field], bool):
            raise CandidateError(f"case.params.{field} must be a boolean")
    return {
        "current_day": _integer(raw["current_day"], "case.params.current_day"),
        "expiry_days": _integer(raw["expiry_days"], "case.params.expiry_days"),
        "config_hash": _identifier(raw["config_hash"], "case.params.config_hash"),
        "profile_hash": _identifier(raw["profile_hash"], "case.params.profile_hash"),
        "options_key": _identifier(raw["options_key"], "case.params.options_key"),
        "force": bool(raw["force"]),
        "warm": bool(raw["warm"]),
        "atomic": bool(raw["atomic"]),
        "capacity": _integer(raw["capacity"], "case.params.capacity"),
    }


def _normalize_identifiers(
    raw: object,
    field: str,
    *,
    allowed: set[str] | None = None,
) -> list[str]:
    if not isinstance(raw, list):
        raise CandidateError(f"{field} must be a list")
    result = [_identifier(item, field) for item in raw]
    if len(result) != len(set(result)):
        raise CandidateError(f"{field} contains duplicates")
    if allowed is not None and not set(result) <= allowed:
        raise CandidateError(f"{field} contains unknown IDs")
    return sorted(result)


def _normalize_outcome(
    raw: object,
    *,
    current_paths: set[str],
    state_paths: set[str],
) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != OUTCOME_FIELDS:
        raise CandidateError("outcome fields are invalid")
    if not isinstance(raw["committed"], bool):
        raise CandidateError("outcome.committed must be a boolean")

    decisions_raw = raw["decisions"]
    if not isinstance(decisions_raw, list):
        raise CandidateError("outcome.decisions must be a list")
    decisions: dict[str, str] = {}
    for row in decisions_raw:
        if not isinstance(row, list) or len(row) != 2:
            raise CandidateError("outcome.decisions rows are invalid")
        path = _identifier(row[0], "outcome.decisions path")
        decision = _identifier(row[1], "outcome.decisions value")
        if path not in current_paths or path in decisions:
            raise CandidateError("outcome.decisions contains duplicate or unknown IDs")
        if decision != "hit" and decision not in MISS_REASONS:
            raise CandidateError("outcome.decisions contains an unknown result")
        decisions[path] = decision

    writes_raw = raw["writes"]
    if not isinstance(writes_raw, list):
        raise CandidateError("outcome.writes must be a list")
    writes: dict[str, list[Any]] = {}
    for row in writes_raw:
        if not isinstance(row, list) or len(row) != 3:
            raise CandidateError("outcome.writes rows are invalid")
        path = _identifier(row[0], "outcome.writes path")
        if path not in state_paths or path in writes:
            raise CandidateError("outcome.writes contains duplicate or unknown IDs")
        writes[path] = [
            path,
            _identifier(row[1], "outcome.writes content hash"),
            _integer(row[2], "outcome.writes last-used day"),
        ]

    counts_raw = raw["counts"]
    if not isinstance(counts_raw, dict) or set(counts_raw) != COUNT_FIELDS:
        raise CandidateError("outcome.counts fields are invalid")
    reasons_raw = counts_raw["reasons"]
    if not isinstance(reasons_raw, list):
        raise CandidateError("outcome.counts.reasons must be a list")
    reasons: dict[str, int] = {}
    for row in reasons_raw:
        if not isinstance(row, list) or len(row) != 2:
            raise CandidateError("outcome.counts.reasons rows are invalid")
        reason = _identifier(row[0], "outcome.counts reason")
        if reason not in MISS_REASONS or reason in reasons:
            raise CandidateError("outcome.counts.reasons contains duplicate or unknown IDs")
        reasons[reason] = _integer(row[1], "outcome.counts reason count")

    reported_raw = raw["reported"]
    if not isinstance(reported_raw, list):
        raise CandidateError("outcome.reported must be a list")
    reported: dict[str, list[Any]] = {}
    for row in reported_raw:
        if not isinstance(row, list) or len(row) != 2:
            raise CandidateError("outcome.reported rows are invalid")
        path = _identifier(row[0], "outcome.reported path")
        if path not in current_paths or path in reported:
            raise CandidateError("outcome.reported contains duplicate or unknown IDs")
        reported[path] = [path, _integer(row[1], "outcome.reported issue count")]

    return {
        "committed": bool(raw["committed"]),
        "decisions": [[path, decisions[path]] for path in sorted(decisions)],
        "writes": [writes[path] for path in sorted(writes)],
        "kept": _normalize_identifiers(raw["kept"], "outcome.kept", allowed=state_paths),
        "evicted": _normalize_identifiers(raw["evicted"], "outcome.evicted", allowed=state_paths),
        "counts": {
            "hits": _integer(counts_raw["hits"], "outcome.counts.hits"),
            "misses": _integer(counts_raw["misses"], "outcome.counts.misses"),
            "reasons": [[reason, reasons[reason]] for reason in sorted(reasons)],
        },
        "failed": _normalize_identifiers(raw["failed"], "outcome.failed", allowed=current_paths),
        "reported": [reported[path] for path in sorted(reported)],
    }


def _valid_engine_input(case: Mapping[str, Any]) -> bool:
    files = case["files"]
    cache = case["cache"]
    scans = case["scans"]
    params = case["params"]
    if not MIN_FILES_PER_CASE <= len(files) <= MAX_FILES_PER_CASE:
        return False
    if not MIN_CACHE_ENTRIES <= len(cache) <= MAX_CACHE_ENTRIES:
        return False
    if len(case["requires"]) > 3:
        return False
    if not 1 <= int(case["duration"]) <= 8:
        return False
    if not -10 <= int(case["priority"]) <= 10:
        return False
    for entry in cache.values():
        if any(int(entry[field]) < 0 for field in ("stored_day", "last_used_day", "issue_count")):
            return False
    for scan in scans.values():
        if int(scan["issue_count"]) < 0 or (not scan["ok"] and int(scan["issue_count"]) != 0):
            return False
    if any(int(params[field]) < 0 for field in ("current_day", "expiry_days", "capacity")):
        return False
    return len(files) <= int(params["capacity"]) <= 10


def _reason(
    file_info: Mapping[str, Any],
    entry: Mapping[str, Any] | None,
    params: Mapping[str, Any],
    mutant: str,
) -> str | None:
    if (
        mutant == "priority_corrupted_before_force"
        and params["force"]
        and entry is not None
        and entry["corrupted"]
    ):
        return "corrupted"
    if params["force"]:
        return "force_rescan"
    if entry is None:
        return "not_cached"
    if mutant == "priority_config_before_corrupted" and entry["config_hash"] != params["config_hash"]:
        return "config_changed"
    if entry["corrupted"]:
        return "corrupted"
    if mutant != "entry_path_mismatch_ignored" and entry["path"] != file_info["path"]:
        return "file_changed"
    if entry["content_hash"] != file_info["content_hash"]:
        return "file_changed"
    if entry["config_hash"] != params["config_hash"]:
        return "config_changed"
    if entry["profile_hash"] != params["profile_hash"]:
        return "profile_changed"
    if entry["options_key"] != params["options_key"]:
        return "options_changed"
    age = int(params["current_day"]) - int(entry["stored_day"])
    lifetime = int(params["expiry_days"])
    if lifetime == 0:
        return "expired"
    if mutant == "expiry_boundary_inclusive":
        return "expired" if age >= lifetime else None
    return "expired" if age > lifetime else None


def _new_entry(file_info: Mapping[str, Any], params: Mapping[str, Any], issue_count: int) -> dict[str, Any]:
    return {
        "path": file_info["path"],
        "content_hash": file_info["content_hash"],
        "config_hash": params["config_hash"],
        "profile_hash": params["profile_hash"],
        "options_key": params["options_key"],
        "stored_day": params["current_day"],
        "last_used_day": params["current_day"],
        "issue_count": issue_count,
        "corrupted": False,
    }


def _run_batch(case: Mapping[str, Any], mutant: str = "") -> dict[str, Any]:
    files = copy.deepcopy(case["files"])
    cache = copy.deepcopy(case["cache"])
    scans = copy.deepcopy(case["scans"])
    params = copy.deepcopy(case["params"])
    working = copy.deepcopy(cache)
    decisions: list[dict[str, Any]] = []
    hits = 0
    misses = 0
    counts: dict[str, int] = {}
    scanned: list[str] = []
    failed: list[str] = []
    reported: list[list[Any]] = []

    for file_info in files:
        path = str(file_info["path"])
        saved = working.get(path)
        reason = _reason(file_info, saved, params, mutant)
        if reason is None:
            hits += 1
            issue_count = int(saved["issue_count"])
            if mutant != "hit_not_touched":
                saved["last_used_day"] = params["current_day"]
            outcome = "hit"
            reportable = True
        else:
            misses += 1
            if mutant == "invalidation_counts_collapse_duplicates":
                counts[reason] = 1
            else:
                counts[reason] = counts.get(reason, 0) + 1
            scanned.append(path)
            scan = scans[path]
            issue_count = int(scan["issue_count"])
            outcome = "miss"
            reportable = bool(scan["ok"])
            if scan["ok"]:
                entry = _new_entry(file_info, params, issue_count)
                if mutant == "successful_miss_keeps_stale_content_hash" and saved is not None:
                    entry["content_hash"] = saved["content_hash"]
                working[path] = entry
            else:
                failed.append(path)
                if mutant == "failed_miss_drops_old_entry":
                    working.pop(path, None)
        decisions.append({"path": path, "outcome": outcome, "reason": reason})
        warm = bool(params["warm"]) and mutant != "warm_reports"
        if not warm and reportable and issue_count > 0:
            reported.append([path, issue_count])

    rollback = bool(params["atomic"]) and bool(failed)
    if mutant == "atomic_failure_commits" and params["atomic"]:
        rollback = False
    if mutant == "non_atomic_failure_rolls_back" and not params["atomic"] and failed:
        rollback = True
    if rollback:
        committed = False
        result_cache = copy.deepcopy(cache)
        evicted: list[str] = []
    else:
        committed = True
        result_cache = working
        evicted = []

    if not rollback or mutant == "rollback_applies_eviction":
        protected = {str(item["path"]) for item in files if str(item["path"]) in result_cache}
        if mutant == "eviction_can_remove_current":
            protected = set()
        if mutant == "eviction_newest_first":
            key = lambda path: (-int(result_cache[path]["last_used_day"]), path)
        else:
            key = lambda path: (int(result_cache[path]["last_used_day"]), path)
        victims = sorted((path for path in result_cache if path not in protected), key=key)
        while len(result_cache) > int(params["capacity"]):
            path = victims.pop(0)
            result_cache.pop(path)
            evicted.append(path)

    scanned.sort()
    failed.sort()
    evicted.sort()
    reported.sort(key=lambda item: str(item[0]))
    return {
        "committed": committed,
        "cache": result_cache,
        "decisions": decisions,
        "metrics": {
            "cache_hits": hits,
            "cache_misses": misses,
            "invalidation_counts": counts,
            "scanned_files": scanned,
            "failed_files": failed,
            "evicted_files": evicted,
            "reported_issues": reported,
        },
    }


def _entry_row(lookup_path: str, entry: Mapping[str, Any]) -> list[Any]:
    return [
        lookup_path,
        entry["path"],
        entry["content_hash"],
        entry["config_hash"],
        entry["profile_hash"],
        entry["options_key"],
        entry["stored_day"],
        entry["last_used_day"],
        entry["issue_count"],
        entry["corrupted"],
    ]


def _audit_card(initial_cache: Mapping[str, Any], output: Mapping[str, Any]) -> dict[str, Any]:
    final_cache = output["cache"]
    writes = [
        _entry_row(path, final_cache[path])
        for path in sorted(final_cache)
        if initial_cache.get(path) != final_cache[path]
    ]
    preserved = [
        path
        for path in sorted(final_cache)
        if initial_cache.get(path) == final_cache[path]
    ]
    removed = [path for path in sorted(initial_cache) if path not in final_cache]
    metrics = output["metrics"]
    return {
        "committed": output["committed"],
        "decisions": [
            [item["path"], item["outcome"], item["reason"]]
            for item in output["decisions"]
        ],
        "writes": writes,
        "preserved": preserved,
        "removed": removed,
        "metrics": {
            "hits": metrics["cache_hits"],
            "misses": metrics["cache_misses"],
            "reasons": metrics["invalidation_counts"],
            "failed": metrics["failed_files"],
            "evicted": metrics["evicted_files"],
            "reported": metrics["reported_issues"],
        },
    }


def _expected_card(case: Mapping[str, Any], mutant: str = "") -> dict[str, Any]:
    return _audit_card(case["cache"], _run_batch(case, mutant))


def _compact_outcome(card: Mapping[str, Any]) -> dict[str, Any]:
    metrics = card["metrics"]
    return {
        "committed": bool(card["committed"]),
        "decisions": [
            [str(path), "hit" if outcome == "hit" else str(reason)]
            for path, outcome, reason in card["decisions"]
        ],
        "writes": [[str(row[0]), str(row[2]), int(row[7])] for row in card["writes"]],
        "kept": list(card["preserved"]),
        "evicted": list(metrics["evicted"]),
        "counts": {
            "hits": int(metrics["hits"]),
            "misses": int(metrics["misses"]),
            "reasons": [[str(reason), int(count)] for reason, count in sorted(metrics["reasons"].items())],
        },
        "failed": list(metrics["failed"]),
        "reported": [list(row) for row in metrics["reported"]],
    }


def _normalize_case(raw: object, previous_ids: set[str]) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != CASE_FIELDS:
        raise CandidateError("case fields are invalid")
    case_id = _identifier(raw["id"], "case.id")
    if case_id in previous_ids:
        raise CandidateError("case IDs must be unique")
    requires = _normalize_identifiers(raw["requires"], "case.requires", allowed=previous_ids)
    files = _normalize_files(raw["files"])
    current_paths = {str(item["path"]) for item in files}
    cache = _normalize_cache(raw["cache"])
    scans = _normalize_scans(raw["scans"], current_paths)
    case = {
        "id": case_id,
        "duration": _integer(raw["duration"], "case.duration"),
        "environment": _identifier(raw["environment"], "case.environment"),
        "priority": _integer(raw["priority"], "case.priority"),
        "requires": requires,
        "files": files,
        "cache": cache,
        "scans": scans,
        "params": _normalize_params(raw["params"]),
    }
    outcome = _normalize_outcome(
        raw["outcome"],
        current_paths=current_paths,
        state_paths=set(cache) | current_paths,
    )
    invalid_reason: str | None = None
    if not _valid_engine_input(case):
        invalid_reason = "CandidateError:cache case inputs are invalid"
    elif int(_expected_card(case)["metrics"]["misses"]) < 1:
        invalid_reason = "CandidateError:every case needs at least one cache miss"
    previous_ids.add(case_id)
    return {
        "id": case_id,
        "case": case,
        "outcome": outcome,
        "valid": invalid_reason is None,
        "invalid_reason": invalid_reason,
    }


def _normalize_portfolio(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != PORTFOLIO_FIELDS:
        raise CandidateError("portfolio fields are invalid")
    raw_cases = raw["cases"]
    if not isinstance(raw_cases, list) or len(raw_cases) != CASES_PER_PORTFOLIO:
        raise CandidateError("a portfolio must contain exactly four cases")
    previous_ids: set[str] = set()
    cases = [_normalize_case(case, previous_ids) for case in raw_cases]
    return {"name": _identifier(raw["name"], "portfolio.name"), "cases": cases}


def _expected_audit_rows() -> list[dict[str, Any]]:
    cache = copy.deepcopy(AUDIT_INPUT["initial_cache"])
    rows: list[dict[str, Any]] = []
    for step in AUDIT_INPUT["steps"]:
        case = {
            "id": step["id"],
            "duration": 1,
            "environment": "audit",
            "priority": 0,
            "requires": [],
            "files": copy.deepcopy(step["files"]),
            "cache": copy.deepcopy(cache),
            "scans": copy.deepcopy(step["scans"]),
            "params": copy.deepcopy(step["params"]),
        }
        output = _run_batch(case)
        rows.append(
            {
                "step": str(step["id"]),
                "case": case,
                "outcome": _compact_outcome(_audit_card(cache, output)),
            }
        )
        cache = copy.deepcopy(output["cache"])
    return rows


def _normalize_audit(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or len(raw) != len(STEP_IDS):
        raise CandidateError("audit must contain exactly three rows")
    expected = {row["step"]: row for row in _expected_audit_rows()}
    submitted: dict[str, dict[str, Any]] = {}
    for raw_row in raw:
        if not isinstance(raw_row, dict) or set(raw_row) != AUDIT_ROW_FIELDS:
            raise CandidateError("audit row fields are invalid")
        step = _identifier(raw_row["step"], "audit.step")
        if step not in expected or step in submitted:
            raise CandidateError("audit contains duplicate or unknown step IDs")
        case = expected[step]["case"]
        current_paths = {str(item["path"]) for item in case["files"]}
        submitted[step] = {
            "step": step,
            "outcome": _normalize_outcome(
                raw_row["outcome"],
                current_paths=current_paths,
                state_paths=set(case["cache"]) | current_paths,
            ),
        }
    if set(submitted) != set(STEP_IDS):
        raise CandidateError("audit step IDs are incomplete")
    return [submitted[step] for step in STEP_IDS]


def _normalize_payload(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != TOP_LEVEL_FIELDS:
        raise CandidateError("top-level fields are invalid")
    raw_portfolios = payload["portfolios"]
    if not isinstance(raw_portfolios, list) or len(raw_portfolios) != PORTFOLIOS:
        raise CandidateError("portfolios must contain exactly two entries")
    portfolios = [_normalize_portfolio(raw) for raw in raw_portfolios]
    names = [str(portfolio["name"]) for portfolio in portfolios]
    if len(names) != len(set(names)):
        raise CandidateError("portfolio names must be unique")
    portfolios.sort(key=lambda item: str(item["name"]))
    return {"portfolios": portfolios, "audit": _normalize_audit(payload["audit"])}


def _facet_value(outcome: Mapping[str, Any], facet: str) -> object:
    if facet == "decisions":
        return outcome["decisions"]
    if facet == "state":
        return outcome["writes"], outcome["kept"], outcome["evicted"]
    if facet == "transaction":
        return outcome["committed"]
    if facet == "eviction":
        return outcome["evicted"]
    if facet == "counters":
        return outcome["counts"], outcome["failed"]
    if facet == "reporting":
        return outcome["reported"]
    raise CandidateError(f"unknown facet: {facet}")


def _behavior_facet_value(outcome: Mapping[str, Any], facet: str) -> object:
    if facet == "decisions":
        return outcome["decisions"]
    if facet == "writes":
        return outcome["writes"]
    if facet == "preservation":
        return outcome["kept"], outcome["evicted"]
    if facet == "transaction":
        return outcome["committed"]
    if facet == "reporting_eviction":
        return outcome["evicted"], outcome["reported"]
    if facet == "scan_metrics":
        return outcome["counts"], outcome["failed"]
    raise CandidateError(f"unknown behavior outcome facet: {facet}")


def _coverage_atoms(card: Mapping[str, Any]) -> set[str]:
    metrics = card["metrics"]
    atoms: set[str] = set()
    if int(metrics["hits"]) > 0:
        atoms.add("hit")
    atoms.update(
        f"reason:{reason}"
        for reason, count in metrics["reasons"].items()
        if int(count) > 0
    )
    if metrics["failed"]:
        atoms.add("scan_failed")
    atoms.add("commit" if card["committed"] else "rollback")
    if card["writes"]:
        atoms.add("write")
    if card["preserved"]:
        atoms.add("preserve")
    if card["removed"]:
        atoms.add("remove")
    if metrics["evicted"]:
        atoms.add("evict")
    atoms.add("reported" if metrics["reported"] else "silent")
    if not atoms <= ATOM_NAMES:
        raise CandidateError("unknown coverage atom")
    return atoms


def _valid_case_cards(portfolio: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(item["id"]): _expected_card(item["case"])
        for item in portfolio["cases"]
        if bool(item["valid"])
    }


def _interaction_report(portfolio: Mapping[str, Any]) -> dict[str, Any]:
    cards = _valid_case_cards(portfolio)
    valid_cases = [item for item in portfolio["cases"] if bool(item["valid"])]
    outputs = {str(item["id"]): _run_batch(item["case"]) for item in valid_cases}
    atoms = set().union(*(_coverage_atoms(card) for card in cards.values())) if cards else set()
    return {
        "hit": any(int(card["metrics"]["hits"]) > 0 for card in cards.values()),
        "eviction": any(card["metrics"]["evicted"] for card in cards.values()),
        "atomic_failed_scan": any(
            bool(item["case"]["params"]["atomic"])
            and outputs[str(item["id"])]["metrics"]["failed_files"]
            for item in valid_cases
        ),
        "non_atomic_failed_scan": any(
            not bool(item["case"]["params"]["atomic"])
            and outputs[str(item["id"])]["metrics"]["failed_files"]
            for item in valid_cases
        ),
        "semantic_atom_minimum": len(atoms) >= 9,
        "semantic_atom_count": len(atoms),
        "valid_case_count": len(valid_cases),
    }


def _mutant_witness(mutant: str, portfolios: Iterable[Mapping[str, Any]]) -> str | None:
    facet = MUTANT_OUTCOME_FACET[mutant]
    for portfolio in portfolios:
        for item in portfolio["cases"]:
            if not bool(item["valid"]):
                continue
            expected = _compact_outcome(_expected_card(item["case"]))
            if _behavior_facet_value(item["outcome"], facet) != _behavior_facet_value(expected, facet):
                continue
            actual = _compact_outcome(_expected_card(item["case"], mutant))
            if _behavior_facet_value(actual, facet) != _behavior_facet_value(expected, facet):
                return f"{portfolio['name']}:{item['id']}"
    return None


def _audit_nonvacuous(expected: list[Mapping[str, Any]]) -> dict[str, bool]:
    outcomes = [row["outcome"] for row in expected]
    return {
        "decisions": any(outcome["decisions"] for outcome in outcomes),
        "state": any(outcome["writes"] or outcome["kept"] or outcome["evicted"] for outcome in outcomes),
        "transaction": len({bool(outcome["committed"]) for outcome in outcomes}) > 1,
        "eviction": any(outcome["evicted"] for outcome in outcomes),
        "counters": any(outcome["counts"]["hits"] and outcome["counts"]["misses"] for outcome in outcomes),
        "reporting": any(outcome["reported"] for outcome in outcomes) and any(not outcome["reported"] for outcome in outcomes),
    }


def _audit_report(audit: list[Mapping[str, Any]]) -> dict[str, Any]:
    expected = _expected_audit_rows()
    nonvacuous = _audit_nonvacuous(expected)
    rows = [
        {
            "step": wanted["step"],
            "facets": {
                facet: _facet_value(submitted["outcome"], facet) == _facet_value(wanted["outcome"], facet)
                for facet in BASE_CERTIFICATE_FACETS
            },
        }
        for submitted, wanted in zip(audit, expected)
    ]
    facets = {
        facet: bool(nonvacuous[facet] and all(row["facets"][facet] for row in rows))
        for facet in BASE_CERTIFICATE_FACETS
    }
    return {"facets": facets, "nonvacuous": nonvacuous, "rows": rows}


def _hierarchical_facets(base: Mapping[str, bool]) -> dict[str, bool]:
    metrics = bool(base["counters"] and base["reporting"])
    foundations = {
        "decisions": bool(base["decisions"]),
        "state": bool(base["state"]),
        "transaction": bool(base["transaction"]),
        "eviction": bool(base["eviction"]),
        "metrics": metrics,
    }
    return {**foundations, "end_to_end": all(foundations.values())}


def _score_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    portfolios = list(candidate["portfolios"])
    raw_witnesses = {mutant: _mutant_witness(mutant, portfolios) for mutant in BEHAVIOR_MUTANTS}
    audit_report = _audit_report(list(candidate["audit"]))
    audit_facets = dict(audit_report["facets"])
    portfolio_coverage = {
        facet: all(raw_witnesses[mutant] for mutant in requirements)
        for facet, requirements in COVERAGE_REQUIREMENTS.items()
    }
    cross_object = {
        facet: bool(audit_facets[facet] and portfolio_coverage[facet])
        for facet in BASE_CERTIFICATE_FACETS
    }
    interaction_reports = [_interaction_report(portfolio) for portfolio in portfolios]
    joint_interactions = {
        field: all(bool(report[field]) for report in interaction_reports)
        for field in INTERACTION_FIELDS
    }
    for interaction, facets in INTERACTION_CERTIFICATE_GATES.items():
        if not joint_interactions[interaction]:
            for facet in facets:
                if facet in cross_object:
                    cross_object[facet] = False

    behavior_passed = {
        mutant: bool(raw_witnesses[mutant] and audit_facets[BEHAVIOR_CERTIFICATE_GATE[mutant]])
        for mutant in BEHAVIOR_MUTANTS
    }
    blocked_by_interaction: dict[str, list[str]] = {}
    for interaction, check_ids in INTERACTION_BEHAVIOR_GATES.items():
        if not joint_interactions[interaction]:
            for check_id in check_ids:
                behavior_passed[check_id] = False
                blocked_by_interaction.setdefault(check_id, []).append(interaction)

    hierarchical = _hierarchical_facets(cross_object)
    for interaction, facets in INTERACTION_CERTIFICATE_GATES.items():
        if not joint_interactions[interaction]:
            for facet in facets:
                if facet in hierarchical:
                    hierarchical[facet] = False

    checks = [
        {
            "id": mutant,
            "passed": behavior_passed[mutant],
            "points": 1 if behavior_passed[mutant] else 0,
            "max_points": 1,
        }
        for mutant in BEHAVIOR_MUTANTS
    ] + [
        {
            "id": f"certificate_{facet}",
            "passed": hierarchical[facet],
            "points": 1 if hierarchical[facet] else 0,
            "max_points": 1,
        }
        for facet in CERTIFICATE_FACETS
    ]
    facets = {
        group: {
            "passed": sum(bool(behavior_passed[item]) for item in mutant_ids),
            "total": len(mutant_ids),
        }
        for group, mutant_ids in BEHAVIOR_GROUPS.items()
    }
    facets["certificate"] = {
        "passed": sum(bool(value) for value in hierarchical.values()),
        "total": len(hierarchical),
    }
    invalid_cases = [
        {
            "portfolio": str(portfolio["name"]),
            "case": str(item["id"]),
            "reason": str(item["invalid_reason"]),
        }
        for portfolio in portfolios
        for item in portfolio["cases"]
        if not bool(item["valid"])
    ]
    blocked_by_certificate = {
        mutant: BEHAVIOR_CERTIFICATE_GATE[mutant]
        for mutant in BEHAVIOR_MUTANTS
        if raw_witnesses[mutant]
        and not behavior_passed[mutant]
        and mutant not in blocked_by_interaction
    }
    return {
        "score": sum(int(check["points"]) for check in checks),
        "checks": checks,
        "facets": facets,
        "certificate_facets": {
            "audit_facets": audit_facets,
            "audit_nonvacuous": audit_report["nonvacuous"],
            "audit_rows": audit_report["rows"],
            "portfolio_coverage": portfolio_coverage,
            "portfolio_missing": {
                facet: [mutant for mutant in requirements if not raw_witnesses[mutant]]
                for facet, requirements in COVERAGE_REQUIREMENTS.items()
            },
            "cross_object_facets": cross_object,
            "portfolio_interactions": [
                {"portfolio": str(portfolio["name"]), **report}
                for portfolio, report in zip(portfolios, interaction_reports)
            ],
            "interaction_facets": joint_interactions,
            "facets": hierarchical,
        },
        "raw_behavior_witnesses": raw_witnesses,
        "killed_by_evidence": {
            mutant: str(raw_witnesses[mutant])
            for mutant in BEHAVIOR_MUTANTS
            if behavior_passed[mutant]
        },
        "blocked_by_certificate": blocked_by_certificate,
        "blocked_by_interaction": blocked_by_interaction,
        "invalid_cases": invalid_cases,
    }


def _empty_result(status: str, failure_summary: str) -> dict[str, Any]:
    return {
        "status": status,
        "score": 0,
        "max_score": MAX_SCORE,
        "checks": [],
        "facets": {},
        "certificate_facets": {},
        "raw_behavior_witnesses": {},
        "killed_by_evidence": {},
        "blocked_by_certificate": {},
        "blocked_by_interaction": {},
        "invalid_cases": [],
        "failure_summary": failure_summary,
    }


def grade_response(text: str) -> dict[str, Any]:
    if len(text.encode("utf-8")) > MAX_ANSWER_BYTES:
        return _empty_result("schema_error", "answer_budget_exceeded")
    try:
        payload = bounded_json_loads(text, strip_code_fence=True)
    except json.JSONDecodeError as exc:
        return _empty_result("format_error", f"json_decode_error:{exc.msg}")
    except BoundedJSONError as exc:
        return _empty_result("schema_error", str(exc))
    try:
        result = _score_candidate(_normalize_payload(payload))
    except (CandidateError, KeyError, IndexError, TypeError, ValueError) as exc:
        return _empty_result("schema_error", f"{type(exc).__name__}:{exc}")
    score = int(result["score"])
    return {
        "status": "scored",
        "score": score,
        "max_score": MAX_SCORE,
        **result,
        "failure_summary": "",
    }
