from __future__ import annotations

import json
from typing import Mapping

from .ci_adversarial_audit_grader import (
    CATEGORY_LABELS,
    MUTANT_DETAILS,
    SCENARIO_FIELDS as CORE_SCENARIO_FIELDS,
    _build_audit,
    _category_counts,
    _coverage_score,
    _indexes,
    _normalize_payload,
    _remaining_after_failure,
    _runtime_scenario,
    _strip_code_fence,
)


MAX_SCORE = 20
MAX_JOBS_PER_SCENARIO = 6
BEHAVIOR_MUTANTS = (
    "direct_public_export",
    "upstream_propagation_gate",
    "dependency_requires_all_exports",
    "private_change_exports",
    "coverage_includes_clean_modules",
    "recursive_fallback_prune",
    "critical_lexicographic_tie",
    "cost_charges_unselected_environments",
    "requirements_finish_before_ready",
    "environment_lock_backfill",
    "simultaneous_completion_one_at_a_time",
    "ranking_precedence_and_lexicographic",
    "ranking_normal_before_fallback",
    "ranking_job_count_before_cost",
)
CERTIFICATE_FACETS = (
    "propagation",
    "coverage",
    "fallback",
    "cost",
    "scheduling",
    "ranking",
)
CERTIFICATE_LABELS = {
    "propagation": "变更传播证书",
    "coverage": "覆盖计分证书",
    "fallback": "回退鲁棒性证书",
    "cost": "成本证书",
    "scheduling": "调度证书",
    "ranking": "最终排序证书",
}
SCENARIO_FIELDS = CORE_SCENARIO_FIELDS | {"certificate"}
CERTIFICATE_FIELDS = {"dirty", "plans", "ranking"}
PLAN_CERTIFICATE_FIELDS = {
    "normal",
    "fallback",
    "critical",
    "cost",
    "makespan",
    "failures",
    "dispatch",
}
DISPATCH_FIELDS = {"time", "jobs"}


def _plain_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _string_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError(f"{field} must be a list of non-empty strings")
    result = list(value)
    if len(result) != len(set(result)):
        raise ValueError(f"{field} contains duplicates")
    return result


def _normalize_certificate(
    raw: object,
    scenario: Mapping[str, object],
) -> dict[str, object]:
    if not isinstance(raw, dict) or set(raw) != CERTIFICATE_FIELDS:
        raise ValueError("certificate fields are invalid")
    module_ids = {str(item["id"]) for item in scenario["modules"]}
    plan_ids = set(scenario["plans"])
    job_ids = {str(item["id"]) for item in scenario["jobs"]}

    dirty = _string_list(raw["dirty"], "certificate.dirty")
    if not set(dirty) <= module_ids:
        raise ValueError("certificate.dirty contains unknown modules")

    raw_plans = raw["plans"]
    if not isinstance(raw_plans, dict) or set(raw_plans) != plan_ids:
        raise ValueError("certificate.plans must exactly match scenario plans")
    plan_certificates: dict[str, dict[str, object]] = {}
    for plan_id, raw_plan in raw_plans.items():
        if not isinstance(raw_plan, dict) or set(raw_plan) != PLAN_CERTIFICATE_FIELDS:
            raise ValueError(f"certificate plan {plan_id} fields are invalid")
        for field in ("normal", "fallback", "cost", "makespan"):
            if not _plain_int(raw_plan[field]) or int(raw_plan[field]) < 0:
                raise ValueError(
                    f"certificate plan {plan_id}.{field} must be a non-negative integer"
                )
        selected = set(scenario["plans"][plan_id])
        critical = raw_plan["critical"]
        if not isinstance(critical, str) or critical not in selected:
            raise ValueError(f"certificate plan {plan_id}.critical is invalid")

        raw_failures = raw_plan["failures"]
        if not isinstance(raw_failures, dict) or set(raw_failures) != selected:
            raise ValueError(
                f"certificate plan {plan_id}.failures must exactly match selected jobs"
            )
        failures: dict[str, int] = {}
        for failed_job, remaining_coverage in raw_failures.items():
            if not _plain_int(remaining_coverage) or int(remaining_coverage) < 0:
                raise ValueError(
                    f"certificate plan {plan_id}.failures values must be non-negative integers"
                )
            failures[failed_job] = int(remaining_coverage)

        raw_dispatch = raw_plan["dispatch"]
        if not isinstance(raw_dispatch, list):
            raise ValueError(f"certificate plan {plan_id}.dispatch must be a list")
        dispatch: list[dict[str, object]] = []
        previous_time = -1
        for event in raw_dispatch:
            if not isinstance(event, dict) or set(event) != DISPATCH_FIELDS:
                raise ValueError("certificate dispatch fields are invalid")
            if not _plain_int(event["time"]) or int(event["time"]) < previous_time:
                raise ValueError(
                    "certificate dispatch times must be non-negative and ordered"
                )
            jobs = _string_list(event["jobs"], "certificate dispatch jobs")
            if not jobs or not set(jobs) <= selected or not set(jobs) <= job_ids:
                raise ValueError("certificate dispatch contains invalid jobs")
            previous_time = int(event["time"])
            dispatch.append({"time": int(event["time"]), "jobs": jobs})

        plan_certificates[plan_id] = {
            "normal": int(raw_plan["normal"]),
            "fallback": int(raw_plan["fallback"]),
            "critical": critical,
            "cost": int(raw_plan["cost"]),
            "makespan": int(raw_plan["makespan"]),
            "failures": failures,
            "dispatch": dispatch,
        }

    ranking = _string_list(raw["ranking"], "certificate.ranking")
    if set(ranking) != plan_ids:
        raise ValueError("certificate.ranking must contain every scenario plan")
    return {"dirty": dirty, "plans": plan_certificates, "ranking": ranking}


def _normalize_certificate_payload(
    payload: object,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    if not isinstance(payload, dict) or set(payload) != {"scenarios"}:
        raise ValueError("top-level object must contain only scenarios")
    raw_scenarios = payload["scenarios"]
    if not isinstance(raw_scenarios, list) or len(raw_scenarios) != 2:
        raise ValueError("scenarios must contain exactly 2 entries")

    core_scenarios: list[dict[str, object]] = []
    raw_certificates: list[object] = []
    for raw_scenario in raw_scenarios:
        if not isinstance(raw_scenario, dict) or set(raw_scenario) != SCENARIO_FIELDS:
            raise ValueError("scenario fields are invalid")
        jobs = raw_scenario["jobs"]
        if isinstance(jobs, list) and len(jobs) > MAX_JOBS_PER_SCENARIO:
            raise ValueError("job_budget_exceeded")
        core_scenarios.append(
            {key: raw_scenario[key] for key in CORE_SCENARIO_FIELDS}
        )
        raw_certificates.append(raw_scenario["certificate"])

    scenarios = _normalize_payload({"scenarios": core_scenarios})
    certificates = [
        _normalize_certificate(raw, scenario)
        for raw, scenario in zip(raw_certificates, scenarios)
    ]
    return scenarios, certificates


def _build_expected_audit(scenario: dict[str, object]) -> dict[str, object]:
    runtime = _runtime_scenario(scenario)
    expected = _build_audit(runtime)
    modules, jobs = _indexes(runtime)
    for plan_id, raw_selected in scenario["plans"].items():
        selected = tuple(raw_selected)
        expected["plans"][plan_id]["failures"] = {
            failed: _coverage_score(
                _remaining_after_failure(selected, failed, jobs, ""),
                expected["dirty"],
                modules,
                jobs,
                "",
            )
            for failed in selected
        }
    return expected


def _certificate_facets(
    certificate: Mapping[str, object],
    expected: Mapping[str, object],
) -> dict[str, bool]:
    plan_certificates = certificate["plans"]
    expected_plans = expected["plans"]
    return {
        "propagation": certificate["dirty"] == expected["dirty"],
        "coverage": all(
            plan_certificates[plan_id]["normal"] == metrics["normal"]
            for plan_id, metrics in expected_plans.items()
        ),
        "fallback": all(
            (
                plan_certificates[plan_id]["fallback"],
                plan_certificates[plan_id]["critical"],
                plan_certificates[plan_id]["failures"],
            )
            == (metrics["fallback"], metrics["critical"], metrics["failures"])
            for plan_id, metrics in expected_plans.items()
        ),
        "cost": all(
            plan_certificates[plan_id]["cost"] == metrics["cost"]
            for plan_id, metrics in expected_plans.items()
        ),
        "scheduling": all(
            (
                plan_certificates[plan_id]["makespan"],
                plan_certificates[plan_id]["dispatch"],
            )
            == (metrics["makespan"], metrics["dispatch"])
            for plan_id, metrics in expected_plans.items()
        ),
        "ranking": certificate["ranking"] == expected["ranking"],
    }


def _certifies_mutant_difference(
    *,
    category: str,
    certificate: Mapping[str, object],
    expected: Mapping[str, object],
    actual: Mapping[str, object],
) -> bool:
    if not _certificate_facets(certificate, expected)[category]:
        return False
    plan_certificates = certificate["plans"]
    if category == "propagation":
        return actual["dirty"] != expected["dirty"]
    if category in {"coverage", "fallback", "cost"}:
        fields = {
            "coverage": ("normal",),
            "fallback": ("fallback", "critical"),
            "cost": ("cost",),
        }[category]
        return any(
            actual["plans"][plan_id][field] != expected_metrics[field]
            for plan_id, expected_metrics in expected["plans"].items()
            for field in fields
        )
    if category == "scheduling":
        return any(
            actual_metrics["dispatch"] != expected_metrics["dispatch"]
            or actual_metrics["makespan"] != expected_metrics["makespan"]
            for plan_id, expected_metrics in expected["plans"].items()
            for actual_metrics in (actual["plans"][plan_id],)
        )
    if category == "ranking":
        return actual["ranking"] != expected["ranking"]
    raise ValueError(f"unknown mutant category: {category}")


def _case_detail(check_id: str) -> dict[str, str]:
    if check_id.startswith("certificate_"):
        facet = check_id.removeprefix("certificate_")
        return {
            "case_id": check_id,
            "label": CERTIFICATE_LABELS[facet],
            "category": "certificate",
            "category_label": "基准计算",
        }
    label, category = MUTANT_DETAILS[check_id]
    return {
        "case_id": check_id,
        "label": label,
        "category": category,
        "category_label": CATEGORY_LABELS[category],
    }


def _empty_result(status: str, error: str) -> dict[str, object]:
    check_ids = list(BEHAVIOR_MUTANTS) + [
        f"certificate_{facet}" for facet in CERTIFICATE_FACETS
    ]
    categories = _category_counts(set(), BEHAVIOR_MUTANTS)
    categories["certificate"] = {
        "label": "基准计算",
        "passed": 0,
        "total": len(CERTIFICATE_FACETS),
    }
    return {
        "status": status,
        "score": 0,
        "max_score": MAX_SCORE,
        "killed_mutants": [],
        "survived_mutants": check_ids,
        "killed_by_test": {},
        "scenario_count": 0,
        "failure_summary": error,
        "failure_details": [_case_detail(check_id) for check_id in check_ids],
        "score_details": [
            {
                "id": check_id,
                "label": _case_detail(check_id)["label"],
                "points": 0,
                "max_points": 1,
                "passed": False,
            }
            for check_id in check_ids
        ],
        "categories": categories,
        "certificate_facets": [],
    }


def _grade_scenarios(
    scenarios: list[dict[str, object]],
    certificates: list[dict[str, object]],
) -> dict[str, object]:
    expected_outputs = [_build_expected_audit(scenario) for scenario in scenarios]
    killed: list[str] = []
    killed_by: dict[str, str] = {}
    for mutant_id in BEHAVIOR_MUTANTS:
        category = MUTANT_DETAILS[mutant_id][1]
        for scenario, certificate, expected in zip(
            scenarios, certificates, expected_outputs
        ):
            try:
                actual = _build_audit(_runtime_scenario(scenario), mutant_id)
            except Exception:
                if _certificate_facets(certificate, expected)[category]:
                    killed.append(mutant_id)
                    killed_by[mutant_id] = str(scenario["name"])
                    break
                continue
            if _certifies_mutant_difference(
                category=category,
                certificate=certificate,
                expected=expected,
                actual=actual,
            ):
                killed.append(mutant_id)
                killed_by[mutant_id] = str(scenario["name"])
                break

    killed_set = set(killed)
    certificate_reports = [
        {
            "scenario": str(scenario["name"]),
            "facets": _certificate_facets(certificate, expected),
        }
        for scenario, certificate, expected in zip(
            scenarios, certificates, expected_outputs
        )
    ]
    certificate_passed = {
        facet: all(report["facets"][facet] for report in certificate_reports)
        for facet in CERTIFICATE_FACETS
    }
    passed_checks = killed_set | {
        f"certificate_{facet}"
        for facet, passed in certificate_passed.items()
        if passed
    }
    check_ids = list(BEHAVIOR_MUTANTS) + [
        f"certificate_{facet}" for facet in CERTIFICATE_FACETS
    ]
    score = len(passed_checks)
    categories = _category_counts(killed_set, BEHAVIOR_MUTANTS)
    categories["certificate"] = {
        "label": "基准计算",
        "passed": sum(certificate_passed.values()),
        "total": len(CERTIFICATE_FACETS),
    }
    survived = [check_id for check_id in check_ids if check_id not in passed_checks]
    return {
        "status": "passed" if score == MAX_SCORE else "semantic_failed",
        "score": score,
        "max_score": MAX_SCORE,
        "killed_mutants": killed,
        "survived_mutants": survived,
        "killed_by_test": killed_by,
        "scenario_count": len(scenarios),
        "failure_summary": "",
        "failure_details": [_case_detail(check_id) for check_id in survived],
        "score_details": [
            {
                "id": check_id,
                "label": _case_detail(check_id)["label"],
                "points": 1 if check_id in passed_checks else 0,
                "max_points": 1,
                "passed": check_id in passed_checks,
            }
            for check_id in check_ids
        ],
        "categories": categories,
        "certificate_facets": certificate_reports,
    }


def grade_response(text: str) -> dict[str, object]:
    try:
        payload = json.loads(_strip_code_fence(text))
    except json.JSONDecodeError as exc:
        return _empty_result("invalid_json", f"json_decode_error:{exc.msg}")
    try:
        scenarios, certificates = _normalize_certificate_payload(payload)
        return _grade_scenarios(scenarios, certificates)
    except Exception as exc:
        return _empty_result("invalid_schema", f"{type(exc).__name__}:{exc}")
