from __future__ import annotations

import json


MUTANT_SPECS = (
    ("private_change_dirty", "私有变更未标脏", "propagation"),
    ("direct_public_export", "直接公共变更受传播门阻断", "propagation"),
    ("upstream_propagation_gate", "上游传播忽略模块门", "propagation"),
    ("distinct_coverage", "重复覆盖重复计分", "coverage"),
    ("recursive_fallback_prune", "回退依赖只裁剪一轮", "fallback"),
    ("critical_lexicographic_tie", "关键失败项取反向字典序", "fallback"),
    ("distinct_environment_setup", "环境 setup 按任务重复计费", "cost"),
    ("requirements_finish_before_ready", "依赖开始即错误就绪", "scheduling"),
    ("environment_lock_backfill", "环境锁阻断后续回填", "scheduling"),
    ("ranking_precedence_and_lexicographic", "排名优先级与字典序错误", "ranking"),
)
MUTANT_SPECS_V2 = MUTANT_SPECS + (
    ("dependency_requires_all_exports", "依赖传播错误要求全部上游导出", "propagation"),
    ("private_change_exports", "私有变更错误传播到下游", "propagation"),
    ("coverage_includes_clean_modules", "覆盖分错误计入未变更模块", "coverage"),
    ("ranking_uses_plan_id_final_tie", "排名最终错误使用方案 ID 决胜", "ranking"),
    ("critical_uses_selected_order", "关键失败项错误沿用选择顺序", "fallback"),
    ("cost_charges_unselected_environments", "成本错误计入未选择环境", "cost"),
    ("ready_job_id_descending", "同优先级任务 ID 反向调度", "scheduling"),
    ("simultaneous_completion_one_at_a_time", "同时完成任务被错误串行化", "scheduling"),
    ("ranking_normal_before_fallback", "排名错误先比较正常覆盖", "ranking"),
    ("ranking_job_count_before_cost", "排名错误先比较任务数再比较成本", "ranking"),
)
MUTANT_IDS = tuple(spec[0] for spec in MUTANT_SPECS)
MUTANT_IDS_V2 = tuple(spec[0] for spec in MUTANT_SPECS_V2)
MUTANT_DETAILS = {
    mutant_id: (label, category)
    for mutant_id, label, category in MUTANT_SPECS_V2
}
CATEGORY_LABELS = {
    "propagation": "变更传播",
    "coverage": "覆盖计分",
    "fallback": "回退鲁棒性",
    "cost": "成本",
    "scheduling": "调度",
    "ranking": "最终排序",
}
MAX_SCORE = len(MUTANT_IDS)

SCENARIO_FIELDS = {
    "name",
    "modules",
    "changes",
    "jobs",
    "plans",
    "setup_costs",
    "workers",
}
MODULE_FIELDS = {"id", "deps", "propagates", "weight"}
JOB_FIELDS = {"id", "duration", "environment", "priority", "covers", "requires"}


def _plain_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _integer(value: object, field: str, minimum: int, maximum: int) -> int:
    if not _plain_int(value) or not minimum <= value <= maximum:
        raise ValueError(f"{field} must be an integer from {minimum} through {maximum}")
    return value


def _string_list(
    value: object,
    field: str,
    maximum: int,
    *,
    tolerate_duplicates: bool = False,
) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list with at most {maximum} entries")
    result = [_identifier(item, field) for item in value]
    if tolerate_duplicates:
        result = list(dict.fromkeys(result))
    if len(result) > maximum:
        raise ValueError(f"{field} must be a list with at most {maximum} entries")
    if len(result) != len(set(result)):
        raise ValueError(f"{field} contains duplicates")
    return result


def _normalize_scenario(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict) or set(raw) != SCENARIO_FIELDS:
        raise ValueError("scenario fields are invalid")
    name = _identifier(raw["name"], "scenario.name")

    raw_modules = raw["modules"]
    if not isinstance(raw_modules, list) or not 1 <= len(raw_modules) <= 8:
        raise ValueError("modules must contain 1 through 8 entries")
    modules: list[dict[str, object]] = []
    module_ids: list[str] = []
    for index, item in enumerate(raw_modules):
        if not isinstance(item, dict) or set(item) != MODULE_FIELDS:
            raise ValueError(f"module {index} fields are invalid")
        module_id = _identifier(item["id"], f"module {index}.id")
        if module_id in module_ids:
            raise ValueError("module IDs must be unique")
        deps = _string_list(
            item["deps"],
            f"module {module_id}.deps",
            7,
            tolerate_duplicates=True,
        )
        if not set(deps) <= set(module_ids):
            raise ValueError(f"module {module_id} dependencies must refer to earlier modules")
        if not isinstance(item["propagates"], bool):
            raise ValueError(f"module {module_id}.propagates must be boolean")
        modules.append(
            {
                "id": module_id,
                "deps": deps,
                "propagates": item["propagates"],
                "weight": _integer(item["weight"], f"module {module_id}.weight", 1, 20),
            }
        )
        module_ids.append(module_id)

    raw_changes = raw["changes"]
    if not isinstance(raw_changes, dict) or not raw_changes:
        raise ValueError("changes must be a non-empty object")
    changes: dict[str, str] = {}
    for module_id, change in raw_changes.items():
        if module_id not in module_ids or change not in {"private", "public"}:
            raise ValueError("changes contain an invalid module or change kind")
        changes[module_id] = change

    raw_jobs = raw["jobs"]
    if not isinstance(raw_jobs, list) or not 2 <= len(raw_jobs) <= 8:
        raise ValueError("jobs must contain 2 through 8 entries")
    jobs: list[dict[str, object]] = []
    job_ids: list[str] = []
    environments: set[str] = set()
    for index, item in enumerate(raw_jobs):
        if not isinstance(item, dict) or set(item) != JOB_FIELDS:
            raise ValueError(f"job {index} fields are invalid")
        job_id = _identifier(item["id"], f"job {index}.id")
        if job_id in job_ids:
            raise ValueError("job IDs must be unique")
        environment = _identifier(item["environment"], f"job {job_id}.environment")
        covers = _string_list(
            item["covers"],
            f"job {job_id}.covers",
            8,
            tolerate_duplicates=True,
        )
        if not set(covers) <= set(module_ids):
            raise ValueError(f"job {job_id} covers unknown modules")
        requires = _string_list(
            item["requires"],
            f"job {job_id}.requires",
            7,
            tolerate_duplicates=True,
        )
        if not set(requires) <= set(job_ids):
            raise ValueError(f"job {job_id} requirements must refer to earlier jobs")
        jobs.append(
            {
                "id": job_id,
                "duration": _integer(item["duration"], f"job {job_id}.duration", 1, 8),
                "environment": environment,
                "priority": _integer(item["priority"], f"job {job_id}.priority", -10, 10),
                "covers": covers,
                "requires": requires,
            }
        )
        job_ids.append(job_id)
        environments.add(environment)

    raw_plans = raw["plans"]
    if not isinstance(raw_plans, dict) or not 1 <= len(raw_plans) <= 5:
        raise ValueError("plans must contain 1 through 5 entries")
    plans: dict[str, list[str]] = {}
    jobs_by_id = {str(job["id"]): job for job in jobs}
    for raw_plan_id, raw_selected in raw_plans.items():
        plan_id = _identifier(raw_plan_id, "plan id")
        selected = _string_list(raw_selected, f"plan {plan_id}", 8)
        if not selected or not set(selected) <= set(job_ids):
            raise ValueError(f"plan {plan_id} selects invalid jobs")
        selected_set = set(selected)
        missing = {
            requirement
            for job_id in selected
            for requirement in jobs_by_id[job_id]["requires"]
            if requirement not in selected_set
        }
        if missing:
            raise ValueError(f"plan {plan_id} omits requirements")
        plans[plan_id] = selected

    raw_setup = raw["setup_costs"]
    if not isinstance(raw_setup, dict) or set(raw_setup) != environments:
        raise ValueError("setup_costs must exactly match used environments")
    setup_costs = {
        environment: _integer(cost, f"setup_costs.{environment}", 0, 8)
        for environment, cost in raw_setup.items()
    }
    return {
        "name": name,
        "modules": modules,
        "changes": changes,
        "jobs": jobs,
        "plans": plans,
        "setup_costs": setup_costs,
        "workers": _integer(raw["workers"], "workers", 1, 3),
    }


def _normalize_payload(payload: object) -> list[dict[str, object]]:
    if not isinstance(payload, dict) or set(payload) != {"scenarios"}:
        raise ValueError("top-level object must contain only scenarios")
    raw_scenarios = payload["scenarios"]
    if not isinstance(raw_scenarios, list) or not 1 <= len(raw_scenarios) <= 2:
        raise ValueError("scenarios must contain 1 or 2 entries")
    scenarios = [_normalize_scenario(raw) for raw in raw_scenarios]
    names = [str(scenario["name"]) for scenario in scenarios]
    if len(names) != len(set(names)):
        raise ValueError("scenario names must be unique")
    return scenarios


def _runtime_scenario(scenario: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in scenario.items() if key != "name"}


def _indexes(scenario: dict[str, object]) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, object]]]:
    modules = {str(item["id"]): item for item in scenario["modules"]}
    jobs = {str(item["id"]): item for item in scenario["jobs"]}
    return modules, jobs


def _dirty_modules(scenario: dict[str, object], bug: str) -> list[str]:
    modules, _ = _indexes(scenario)
    changes = scenario["changes"]
    dirty: list[str] = []
    export_changed: dict[str, bool] = {}
    for module_id, module in modules.items():
        direct = changes.get(module_id)
        upstream_values = [export_changed[str(dep)] for dep in module["deps"]]
        if bug == "dependency_requires_all_exports":
            upstream_changed = bool(upstream_values) and all(upstream_values)
        else:
            upstream_changed = any(upstream_values)
        directly_dirty = direct == "public" if bug == "private_change_dirty" else direct in {"private", "public"}
        if directly_dirty or upstream_changed:
            dirty.append(module_id)
        direct_export = (
            direct in {"private", "public"}
            if bug == "private_change_exports"
            else direct == "public"
        )
        if bug == "direct_public_export":
            direct_export = direct_export and bool(module["propagates"])
        upstream_export = upstream_changed
        if bug != "upstream_propagation_gate":
            upstream_export = upstream_export and bool(module["propagates"])
        export_changed[module_id] = direct_export or upstream_export
    return dirty


def _coverage_score(
    selected: set[str],
    dirty: list[str],
    modules: dict[str, dict[str, object]],
    jobs: dict[str, dict[str, object]],
    bug: str,
) -> int:
    if bug == "distinct_coverage":
        return sum(
            int(modules[str(module_id)]["weight"])
            for job_id in selected
            for module_id in jobs[job_id]["covers"]
            if module_id in dirty
        )
    covered = {
        str(module_id)
        for job_id in selected
        for module_id in jobs[job_id]["covers"]
    }
    if bug == "coverage_includes_clean_modules":
        return sum(int(modules[module_id]["weight"]) for module_id in covered)
    return sum(int(modules[module_id]["weight"]) for module_id in dirty if module_id in covered)


def _remaining_after_failure(
    selected: tuple[str, ...],
    failed: str,
    jobs: dict[str, dict[str, object]],
    bug: str,
) -> set[str]:
    remaining = set(selected)
    remaining.remove(failed)
    if bug == "recursive_fallback_prune":
        for job_id in sorted(remaining):
            if not set(jobs[job_id]["requires"]) <= remaining:
                remaining.remove(job_id)
        return remaining
    changed = True
    while changed:
        changed = False
        for job_id in sorted(remaining):
            if not set(jobs[job_id]["requires"]) <= remaining:
                remaining.remove(job_id)
                changed = True
    return remaining


def _fallback(
    selected: tuple[str, ...],
    dirty: list[str],
    modules: dict[str, dict[str, object]],
    jobs: dict[str, dict[str, object]],
    bug: str,
) -> tuple[int, str]:
    scores = {
        failed: _coverage_score(
            _remaining_after_failure(selected, failed, jobs, bug),
            dirty,
            modules,
            jobs,
            bug,
        )
        for failed in selected
    }
    fallback = min(scores.values())
    if bug == "critical_uses_selected_order":
        return fallback, next(failed for failed in selected if scores[failed] == fallback)
    tied = (failed for failed, score in scores.items() if score == fallback)
    critical = max(tied) if bug == "critical_lexicographic_tie" else min(tied)
    return fallback, critical


def _cost(
    selected: tuple[str, ...],
    jobs: dict[str, dict[str, object]],
    setup_costs: dict[str, int],
    bug: str,
) -> int:
    durations = sum(int(jobs[job_id]["duration"]) for job_id in selected)
    if bug == "distinct_environment_setup":
        setups = sum(setup_costs[str(jobs[job_id]["environment"])] for job_id in selected)
    else:
        environments = (
            set(setup_costs)
            if bug == "cost_charges_unselected_environments"
            else {str(jobs[job_id]["environment"]) for job_id in selected}
        )
        setups = sum(setup_costs[environment] for environment in environments)
    return durations + setups


def _schedule(
    selected: tuple[str, ...],
    jobs: dict[str, dict[str, object]],
    workers: int,
    bug: str,
) -> tuple[list[dict[str, object]], int]:
    pending = set(selected)
    finished: set[str] = set()
    running: dict[str, tuple[int, str]] = {}
    held_environments: set[str] = set()
    dispatch: list[dict[str, object]] = []
    now = 0

    while pending or running:
        completed = sorted(job_id for job_id, (end, _) in running.items() if end == now)
        if bug == "simultaneous_completion_one_at_a_time" and len(completed) > 1:
            for delayed_id in completed[1:]:
                _, delayed_environment = running[delayed_id]
                running[delayed_id] = (now + 1, delayed_environment)
            completed = completed[:1]
        for job_id in completed:
            _, environment = running.pop(job_id)
            held_environments.remove(environment)
            finished.add(job_id)

        completed_or_running = finished | set(running)
        ready = sorted(
            (
                job_id
                for job_id in pending
                if set(jobs[job_id]["requires"])
                <= (completed_or_running if bug == "requirements_finish_before_ready" else finished)
            ),
            key=lambda job_id: (
                -int(jobs[job_id]["priority"]),
                tuple(-ord(character) for character in job_id)
                if bug == "ready_job_id_descending"
                else tuple(ord(character) for character in job_id),
            ),
        )
        started: list[str] = []
        for job_id in ready:
            if len(running) >= workers:
                break
            environment = str(jobs[job_id]["environment"])
            if environment in held_environments:
                if bug == "environment_lock_backfill":
                    break
                continue
            pending.remove(job_id)
            running[job_id] = (now + int(jobs[job_id]["duration"]), environment)
            held_environments.add(environment)
            started.append(job_id)
        if started:
            dispatch.append({"time": now, "jobs": started})

        if pending or running:
            if not running:
                raise ValueError("unschedulable plan")
            now = min(end for end, _ in running.values())
    return dispatch, now


def _rank_key(selected: tuple[str, ...], metrics: dict[str, object], bug: str) -> tuple[object, ...]:
    final_order = tuple(selected) if bug == "ranking_precedence_and_lexicographic" else tuple(sorted(selected))
    if bug == "ranking_precedence_and_lexicographic":
        time_and_cost = (metrics["cost"], metrics["makespan"])
    else:
        time_and_cost = (metrics["makespan"], metrics["cost"])
    coverage_order = (
        (-int(metrics["normal"]), -int(metrics["fallback"]))
        if bug == "ranking_normal_before_fallback"
        else (-int(metrics["fallback"]), -int(metrics["normal"]))
    )
    if bug == "ranking_job_count_before_cost":
        tail = (metrics["makespan"], len(selected), metrics["cost"], final_order)
    else:
        tail = (*time_and_cost, len(selected), final_order)
    return (*coverage_order, *tail)


def _build_audit(scenario: dict[str, object], bug: str = "") -> dict[str, object]:
    modules, jobs = _indexes(scenario)
    dirty = _dirty_modules(scenario, bug)
    plan_metrics: dict[str, dict[str, object]] = {}
    for plan_id, raw_selected in scenario["plans"].items():
        selected = tuple(raw_selected)
        fallback, critical = _fallback(selected, dirty, modules, jobs, bug)
        dispatch, makespan = _schedule(selected, jobs, int(scenario["workers"]), bug)
        plan_metrics[plan_id] = {
            "normal": _coverage_score(set(selected), dirty, modules, jobs, bug),
            "fallback": fallback,
            "critical": critical,
            "cost": _cost(selected, jobs, scenario["setup_costs"], bug),
            "dispatch": dispatch,
            "makespan": makespan,
        }
    if bug == "ranking_uses_plan_id_final_tie":
        ranking = sorted(
            scenario["plans"],
            key=lambda plan_id: (
                _rank_key(
                    tuple(scenario["plans"][plan_id]),
                    plan_metrics[plan_id],
                    bug,
                )[:-1],
                plan_id,
            ),
        )
    else:
        ranking = sorted(
            scenario["plans"],
            key=lambda plan_id: _rank_key(
                tuple(scenario["plans"][plan_id]), plan_metrics[plan_id], bug
            ),
        )
    return {"dirty": dirty, "plans": plan_metrics, "ranking": ranking}


def _case_detail(mutant_id: str) -> dict[str, str]:
    label, category = MUTANT_DETAILS[mutant_id]
    return {
        "case_id": mutant_id,
        "label": label,
        "category": category,
        "category_label": CATEGORY_LABELS[category],
    }


def _empty_result(
    status: str,
    error: str,
    mutant_ids: tuple[str, ...],
) -> dict[str, object]:
    return {
        "status": status,
        "score": 0,
        "max_score": len(mutant_ids),
        "killed_mutants": [],
        "survived_mutants": list(mutant_ids),
        "killed_by_test": {},
        "scenario_count": 0,
        "failure_summary": error,
        "failure_details": [_case_detail(mutant_id) for mutant_id in mutant_ids],
        "score_details": [
            {
                "id": mutant_id,
                "label": MUTANT_DETAILS[mutant_id][0],
                "points": 0,
                "max_points": 1,
                "passed": False,
            }
            for mutant_id in mutant_ids
        ],
        "categories": _category_counts(set(), mutant_ids),
    }


def _category_counts(
    killed: set[str],
    mutant_ids: tuple[str, ...],
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for category, label in CATEGORY_LABELS.items():
        members = [
            mutant_id
            for mutant_id in mutant_ids
            if MUTANT_DETAILS[mutant_id][1] == category
        ]
        result[category] = {
            "label": label,
            "passed": sum(mutant_id in killed for mutant_id in members),
            "total": len(members),
        }
    return result


def _grade_scenarios(
    scenarios: list[dict[str, object]],
    mutant_ids: tuple[str, ...],
) -> dict[str, object]:
    expected_outputs = [_build_audit(_runtime_scenario(scenario)) for scenario in scenarios]
    killed: list[str] = []
    killed_by: dict[str, str] = {}
    for mutant_id in mutant_ids:
        witness = None
        for scenario, expected in zip(scenarios, expected_outputs):
            try:
                actual = _build_audit(_runtime_scenario(scenario), mutant_id)
            except Exception:
                witness = str(scenario["name"])
                break
            if actual != expected:
                witness = str(scenario["name"])
                break
        if witness is not None:
            killed.append(mutant_id)
            killed_by[mutant_id] = witness
    killed_set = set(killed)
    survived = [mutant_id for mutant_id in mutant_ids if mutant_id not in killed_set]
    score = len(killed)
    return {
        "status": "passed" if score == len(mutant_ids) else "semantic_failed",
        "score": score,
        "max_score": len(mutant_ids),
        "killed_mutants": killed,
        "survived_mutants": survived,
        "killed_by_test": killed_by,
        "scenario_count": len(scenarios),
        "failure_summary": "",
        "failure_details": [_case_detail(mutant_id) for mutant_id in survived],
        "score_details": [
            {
                "id": mutant_id,
                "label": MUTANT_DETAILS[mutant_id][0],
                "points": 1 if mutant_id in killed_set else 0,
                "max_points": 1,
                "passed": mutant_id in killed_set,
            }
            for mutant_id in mutant_ids
        ],
        "categories": _category_counts(killed_set, mutant_ids),
    }


def grade_response(text: str, test_suite: str = "ci_adversarial_audit_v1") -> dict[str, object]:
    if test_suite == "ci_adversarial_audit_v1":
        mutant_ids = MUTANT_IDS
    elif test_suite == "ci_adversarial_audit_v2":
        mutant_ids = MUTANT_IDS_V2
    else:
        raise ValueError("unknown_test_suite")
    try:
        payload = json.loads(_strip_code_fence(text))
    except json.JSONDecodeError as exc:
        return _empty_result("invalid_json", f"json_decode_error:{exc.msg}", mutant_ids)
    try:
        return _grade_scenarios(_normalize_payload(payload), mutant_ids)
    except Exception as exc:
        return _empty_result(
            "invalid_schema", f"{type(exc).__name__}:{exc}", mutant_ids
        )
