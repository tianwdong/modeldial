from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Iterable

from .costing import current_pricing_snapshot_id, estimate_reference_cost


ADVISOR_SCHEMA_VERSION = 1
ADVISOR_RULESET_VERSION = "advisor-p0-v1"
EVALUATION_FRESHNESS_DAYS = 14
PREVIEW_WORK_UNITS = 5
QUALIFIED_WORK_UNITS = 30
MAX_TOTAL_SCORE_REGRESSION = 3.0
MAX_CRITICAL_SCORE_REGRESSION = 5.0
MIN_QUOTA_REDUCTION_PERCENT = 20.0
MIN_TIME_REDUCTION_PERCENT = 15.0
MIN_STANDARD_COST_REDUCTION_PERCENT = 25.0
MEDIUM_CONFIDENCE = 0.6
HARD_FAILURE_STATUSES = frozenset({"error", "timeout", "truncated", "interrupted"})


def build_advisor_decision(
    snapshot: dict[str, object],
    codex_insights: dict[str, object],
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    generated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    recommendation = _dict(_dict(snapshot.get("config")).get("recommendation"))
    current_id = _text(recommendation.get("effective_current_candidate_id"))
    identity_confidence = _identity_confidence(recommendation, current_id)
    rows = [
        row
        for row in _dict(_dict(snapshot.get("dashboard"))).get("leaderboard", [])
        if isinstance(row, dict)
    ]
    rows_by_id = {
        candidate_id: row
        for row in rows
        if (candidate_id := _text(row.get("candidate_id")))
    }
    base = _base_decision(generated_at, current_id)

    if identity_confidence <= 0 or current_id not in rows_by_id:
        return _finish(
            base,
            decision="unmapped",
            short_circuit_reason="current_identity_unmapped",
            confidence=0.0,
            reasons=["无法可靠确定当前模型与思考档位。"],
            limitations=["不会根据展示名称或自定义 Endpoint 声明猜测当前配置。"],
            next_action="在设置中确认当前模型与思考档位",
        )

    pack = _dict(snapshot.get("question_pack"))
    pack_version = _text(pack.get("version"))
    question_count = _positive_int(pack.get("question_count"))
    current = _evaluation(
        rows_by_id[current_id],
        pack_version=pack_version,
        question_count=question_count,
        now=generated_at,
    )
    identity_confidence = min(
        identity_confidence,
        _route_identity_confidence(current),
    )
    base["quality"] = _quality_payload(current, None, guard_passed=False)
    if not current["route_identity_valid"]:
        return _finish(
            base,
            decision="compare_first",
            short_circuit_reason="current_route_not_current",
            confidence=min(identity_confidence, 0.3),
            reasons=["当前 Endpoint 评测的路由指纹与现有连接不一致或缺失。"],
            limitations=["连接路由变化后，旧五题结果不能继续作为当前基线。"],
            next_action="用当前 Endpoint 连接重新完成完整五题评测",
        )
    if not current["complete"]:
        return _finish(
            base,
            decision="compare_first",
            short_circuit_reason="current_evaluation_incomplete",
            confidence=min(identity_confidence, 0.2),
            reasons=["当前配置没有相同完整题包的有效五题结果。"],
            limitations=["不同题包、缺题或未完成结果不能用于换模判断。"],
            next_action="先完成当前配置的同版五题评测",
        )
    if not current["fresh"]:
        return _finish(
            base,
            decision="compare_first",
            short_circuit_reason="current_evaluation_not_fresh",
            confidence=min(identity_confidence, 0.3),
            reasons=["当前配置的评测证据已经过期。"],
            limitations=[f"评测默认只在 {EVALUATION_FRESHNESS_DAYS} 天内有效。"],
            next_action="用当前题包重新评测当前配置和候选配置",
        )

    comparable_candidates: list[dict[str, object]] = []
    guarded_candidates: list[dict[str, object]] = []
    route_invalid_candidates: list[dict[str, object]] = []
    for candidate_id, row in rows_by_id.items():
        if candidate_id == current_id:
            continue
        candidate = _evaluation(
            row,
            pack_version=pack_version,
            question_count=question_count,
            now=generated_at,
        )
        if not candidate["route_identity_valid"]:
            route_invalid_candidates.append(candidate)
            continue
        if not candidate["complete"] or not candidate["fresh"]:
            continue
        comparable_candidates.append(candidate)
        quality = _quality_payload(current, candidate, guard_passed=False)
        if bool(candidate["hard_failure"]):
            continue
        if float(quality["score_delta"]) < -MAX_TOTAL_SCORE_REGRESSION:
            continue
        if quality["critical_regressions"]:
            continue
        quality["guard_passed"] = True
        candidate["quality"] = quality
        guarded_candidates.append(candidate)

    quota_exhausted = _official_quota_exhausted(_dict(codex_insights.get("account")))
    if not guarded_candidates:
        if quota_exhausted:
            return _finish(
                base,
                decision="quota_risk",
                short_circuit_reason="quota_exhausted_no_alternative",
                confidence=min(identity_confidence, 0.8),
                reasons=["官方额度窗口已经耗尽，且没有候选通过质量护栏。"],
                limitations=["当前还没有可归因的额度 burn-rate，不能估算候选可多完成多少任务。"],
                next_action="等待额度窗口重置，或先评测一个质量合格的候选配置",
            )
        if route_invalid_candidates:
            return _finish(
                base,
                decision="compare_first",
                short_circuit_reason="candidate_route_not_current",
                confidence=min(identity_confidence, 0.3),
                reasons=["候选 Endpoint 的评测路由指纹与现有连接不一致或缺失。"],
                limitations=["旧路由结果不会与当前 Endpoint 配置混用。"],
                next_action="用当前 Endpoint 连接重新完成候选的完整五题评测",
            )
        if not comparable_candidates and len(rows_by_id) > 1:
            return _finish(
                base,
                decision="compare_first",
                short_circuit_reason="candidate_evaluation_missing",
                confidence=min(identity_confidence, 0.3),
                reasons=["候选配置缺少同题包、同版本、完整且新鲜的五题结果。"],
                limitations=["旧题包和不完整结果不会进入质量护栏。"],
                next_action="先完成候选配置的同版五题评测",
            )
        return _finish(
            base,
            decision="keep",
            short_circuit_reason="no_candidate_passed_guard",
            confidence=min(identity_confidence, 0.8),
            reasons=["现有候选没有通过总分、关键题和运行可靠性护栏。"],
            limitations=["速度或标准成本优势不能覆盖质量护栏失败。"],
            next_action="继续使用当前配置",
        )

    workload = _dict(codex_insights.get("workload"))
    quota_burn = _dict(codex_insights.get("quota_burn"))
    account = _dict(codex_insights.get("account"))
    aggregates = [
        row
        for row in workload.get("aggregates", [])
        if isinstance(row, dict)
    ]
    current_aggregate = _unique_workload_aggregate(current, aggregates)
    completed_work_units = (
        _nonnegative_int(current_aggregate.get("completed_work_units"))
        if current_aggregate is not None
        else 0
    )
    coverage_complete = bool(workload.get("coverage_complete", False))
    workload_confidence = _workload_confidence(
        current_aggregate,
        completed_work_units=completed_work_units,
        coverage_complete=coverage_complete,
    )

    evaluated_candidates = []
    for candidate in guarded_candidates:
        candidate_aggregate = _unique_workload_aggregate(candidate, aggregates)
        benefits = _benefits(
            current,
            candidate,
            current_aggregate=current_aggregate,
            candidate_aggregate=candidate_aggregate,
            quota_burn=quota_burn,
            account=account,
        )
        evaluated_candidates.append(
            {
                "candidate": candidate,
                "benefits": benefits,
                "material_count": sum(
                    1
                    for value, threshold in (
                        (benefits["quota_reduction_percent"], MIN_QUOTA_REDUCTION_PERCENT),
                        (benefits["active_time_reduction_percent"], MIN_TIME_REDUCTION_PERCENT),
                        (
                            benefits["standard_cost_reduction_percent"],
                            MIN_STANDARD_COST_REDUCTION_PERCENT,
                        ),
                    )
                    if isinstance(value, (int, float)) and value >= threshold
                ),
            }
        )
    evaluated_candidates.sort(key=_candidate_rank, reverse=True)
    selected = evaluated_candidates[0]
    candidate = _dict(selected["candidate"])
    benefits = _dict(selected["benefits"])
    candidate_id = _text(candidate.get("candidate_id"))
    base["candidate_model_configuration_id"] = candidate_id
    base["quality"] = candidate["quality"]
    base["benefits"] = _public_benefits(benefits)

    freshness_confidence = min(float(current["freshness_confidence"]), float(candidate["freshness_confidence"]))
    benefit_confidence = float(benefits["confidence"])
    confidence = round(
        min(
            identity_confidence,
            _route_identity_confidence(candidate),
            1.0,
            workload_confidence,
            benefit_confidence,
            freshness_confidence,
        ),
        2,
    )
    limitations = ["五题只作为能力护栏，不能证明候选在所有真实项目中表现相同。"]
    if benefits["active_time_evidence"] == "evaluation_proxy":
        limitations.append("时间收益仅来自同版五题耗时代理。")
    if benefits["standard_cost_evidence"] == "evaluation_proxy":
        limitations.append("标准成本收益仅来自同版五题 token 口径，不是实际账单。")
    if not coverage_complete:
        limitations.append("历史覆盖尚不完整。")
    if benefits["quota_reduction_percent"] is None:
        limitations.append("缺少可归因的额度前后快照，未估算额度节省。")
    else:
        limitations.append("官方额度百分比按整数上报，额度收益使用可归因区间的 P25～P75 范围。")
    if current.get("source_mode") == "api" or candidate.get("source_mode") == "api":
        limitations.append("自定义 Endpoint 的模型身份来自本地连接声明，不等同官方直连证明。")

    if completed_work_units < PREVIEW_WORK_UNITS:
        return _finish(
            base,
            decision="wait",
            short_circuit_reason="workload_preview_missing",
            confidence=confidence,
            reasons=[f"最近仅观察到 {completed_work_units} 个完成工作单元。"],
            limitations=limitations,
            next_action=f"继续使用当前配置，累计至少 {PREVIEW_WORK_UNITS} 个完成工作单元",
        )
    if int(selected["material_count"]) == 0:
        return _finish(
            base,
            decision="keep",
            short_circuit_reason="no_material_benefit",
            confidence=confidence,
            reasons=["候选通过质量护栏，但额度、时间和标准成本收益都未达到实质门槛。"],
            limitations=limitations,
            next_action="继续使用当前配置",
        )
    if completed_work_units < QUALIFIED_WORK_UNITS or confidence < MEDIUM_CONFIDENCE:
        return _finish(
            base,
            decision="compare_first",
            short_circuit_reason=(
                "workload_sample_preview_only"
                if completed_work_units < QUALIFIED_WORK_UNITS
                else "confidence_below_medium"
            ),
            confidence=confidence,
            reasons=[
                f"候选通过质量护栏并有实质收益，但当前只有 {completed_work_units} 个完成工作单元或证据链仍偏弱。"
            ],
            limitations=limitations,
            next_action="先用候选完成 5 个真实任务，再复核",
        )
    return _finish(
        base,
        decision="trial_switch",
        short_circuit_reason="material_benefit_with_qualified_evidence",
        confidence=confidence,
        reasons=[
            f"候选通过同版完整五题质量护栏，并基于 {completed_work_units} 个近期工作单元达到实质收益门槛。"
        ],
        limitations=limitations,
        next_action="先用候选完成 5 个真实任务，再复核；不会自动切换模型",
    )


def _base_decision(now: datetime, current_id: str | None) -> dict[str, object]:
    return {
        "schema_version": ADVISOR_SCHEMA_VERSION,
        "ruleset_version": ADVISOR_RULESET_VERSION,
        "decision": "wait",
        "short_circuit_reason": "unresolved",
        "current_model_configuration_id": current_id,
        "candidate_model_configuration_id": None,
        "generated_at": _iso(now),
        "valid_until": _iso(now + timedelta(days=EVALUATION_FRESHNESS_DAYS)),
        "quality": {
            "current_score": None,
            "candidate_score": None,
            "score_delta": None,
            "guard_passed": False,
            "critical_regressions": [],
            "hard_failures": [],
        },
        "benefits": {
            "quota_reduction_percent_range": None,
            "additional_similar_tasks_range": None,
            "quota_evidence": None,
            "active_time_reduction_percent": None,
            "active_time_evidence": None,
            "standard_cost_reduction_percent": None,
            "standard_cost_evidence": None,
            "pricing_snapshot_id": current_pricing_snapshot_id(),
        },
        "confidence": 0.0,
        "confidence_level": "low",
        "reasons": [],
        "limitations": [],
        "next_action": "等待更多证据",
    }


def _finish(
    payload: dict[str, object],
    *,
    decision: str,
    short_circuit_reason: str,
    confidence: float,
    reasons: list[str],
    limitations: list[str],
    next_action: str,
) -> dict[str, object]:
    normalized_confidence = round(max(0.0, min(1.0, confidence)), 2)
    payload.update(
        {
            "decision": decision,
            "short_circuit_reason": short_circuit_reason,
            "confidence": normalized_confidence,
            "confidence_level": _confidence_level(normalized_confidence),
            "reasons": reasons,
            "limitations": list(dict.fromkeys(limitations)),
            "next_action": next_action,
        }
    )
    return payload


def _evaluation(
    row: dict[str, object],
    *,
    pack_version: str | None,
    question_count: int,
    now: datetime,
) -> dict[str, object]:
    question_results = [
        item for item in row.get("question_results", []) if isinstance(item, dict)
    ]
    question_scores = _normalized_question_scores(question_results)
    completed_at = _datetime(row.get("latest_valid_at") or row.get("valid_completed_at"))
    age = (now - completed_at).total_seconds() if completed_at is not None else None
    fresh = bool(
        age is not None
        and age >= -300
        and age <= EVALUATION_FRESHNESS_DAYS * 86400
    )
    score = _number(row.get("overall_score"))
    complete = bool(
        score is not None
        and pack_version
        and _text(row.get("question_pack_version")) == pack_version
        and bool(row.get("is_current_pack_comparable"))
        and question_count > 0
        and _nonnegative_int(row.get("question_completed")) == question_count
        and len(question_results) == question_count
        and len(question_scores) == question_count
        and _text(row.get("candidate_id")) is not None
        and _text(row.get("model")) is not None
        and _text(row.get("effort")) is not None
        and (
            _text(row.get("source_mode")) != "api"
            or _text(row.get("route_identity_status")) == "matched"
        )
    )
    hard_failures = [
        _text(item.get("question_id")) or "unknown"
        for item in question_results
        if (_text(item.get("status")) or "").casefold() in HARD_FAILURE_STATUSES
    ]
    return {
        **row,
        "candidate_id": _text(row.get("candidate_id")),
        "model": _text(row.get("model")),
        "effort": (_text(row.get("effort")) or "").casefold(),
        "score": score,
        "complete": complete,
        "fresh": fresh,
        "freshness_confidence": 1.0 if fresh else 0.0,
        "completed_at": completed_at,
        "question_scores": question_scores,
        "hard_failure": bool(hard_failures),
        "hard_failures": hard_failures,
        "route_identity_valid": (
            _text(row.get("source_mode")) != "api"
            or _text(row.get("route_identity_status")) == "matched"
        ),
    }


def _quality_payload(
    current: dict[str, object],
    candidate: dict[str, object] | None,
    *,
    guard_passed: bool,
) -> dict[str, object]:
    current_score = _number(current.get("score"))
    candidate_score = _number(candidate.get("score")) if candidate else None
    score_delta = (
        round(candidate_score - current_score, 1)
        if current_score is not None and candidate_score is not None
        else None
    )
    regressions = []
    if candidate:
        current_questions = _dict(current.get("question_scores"))
        candidate_questions = _dict(candidate.get("question_scores"))
        for question_id, current_value in current_questions.items():
            candidate_value = _number(candidate_questions.get(question_id))
            current_number = _number(current_value)
            if candidate_value is None or current_number is None:
                continue
            regression = current_number - candidate_value
            if regression > MAX_CRITICAL_SCORE_REGRESSION:
                regressions.append(
                    {
                        "question_id": question_id,
                        "regression": round(regression, 1),
                    }
                )
    return {
        "current_score": current_score,
        "candidate_score": candidate_score,
        "score_delta": score_delta,
        "guard_passed": guard_passed,
        "critical_regressions": regressions,
        "hard_failures": list(candidate.get("hard_failures", [])) if candidate else [],
    }


def _normalized_question_scores(
    question_results: Iterable[dict[str, object]],
) -> dict[str, float]:
    scores: dict[str, float] = {}
    for item in question_results:
        question_id = _text(item.get("question_id"))
        score = _number(item.get("semantic_score"))
        total = _number(item.get("semantic_total"))
        if question_id and score is not None and total is not None and total > 0:
            scores[question_id] = round(score * 20.0 / total, 4)
    return scores


def _benefits(
    current: dict[str, object],
    candidate: dict[str, object],
    *,
    current_aggregate: dict[str, object] | None,
    candidate_aggregate: dict[str, object] | None,
    quota_burn: dict[str, object],
    account: dict[str, object],
) -> dict[str, object]:
    quota = _quota_benefit(current, candidate, quota_burn, account)
    time_reduction = None
    time_evidence = None
    time_confidence = None
    current_duration = _aggregate_median_duration(current_aggregate)
    candidate_duration = _aggregate_median_duration(candidate_aggregate)
    if current_duration and candidate_duration:
        time_reduction = _reduction_percent(current_duration, candidate_duration)
        time_evidence = "real_workload"
        time_confidence = min(
            _aggregate_confidence(current_aggregate),
            _aggregate_confidence(candidate_aggregate),
        )
    else:
        current_elapsed = _number(current.get("elapsed_seconds"))
        candidate_elapsed = _number(candidate.get("elapsed_seconds"))
        if current_elapsed and candidate_elapsed is not None:
            time_reduction = _reduction_percent(current_elapsed, candidate_elapsed)
            time_evidence = "evaluation_proxy"
            time_confidence = 0.6

    cost_reduction = None
    cost_evidence = None
    cost_confidence = None
    current_cost = _aggregate_cost_per_work_unit(current_aggregate)
    candidate_cost = _aggregate_cost_per_work_unit(candidate_aggregate)
    if current_cost and candidate_cost is not None:
        cost_reduction = _reduction_percent(current_cost, candidate_cost)
        cost_evidence = "real_workload"
        cost_confidence = min(
            _aggregate_confidence(current_aggregate),
            _aggregate_confidence(candidate_aggregate),
        )
    elif (
        current.get("cost_coverage") == "complete"
        and candidate.get("cost_coverage") == "complete"
    ):
        current_evaluation_cost = _number(current.get("estimated_cost_usd"))
        candidate_evaluation_cost = _number(candidate.get("estimated_cost_usd"))
        if current_evaluation_cost and candidate_evaluation_cost is not None:
            cost_reduction = _reduction_percent(
                current_evaluation_cost,
                candidate_evaluation_cost,
            )
            cost_evidence = "evaluation_proxy"
            cost_confidence = 0.7

    confidences = [
        value
        for value in (quota.get("confidence"), time_confidence, cost_confidence)
        if value is not None
    ]
    return {
        "quota_reduction_percent": quota.get("reduction_percent"),
        "quota_reduction_percent_range": quota.get("reduction_percent_range"),
        "additional_similar_tasks_range": quota.get("additional_tasks_range"),
        "quota_evidence": quota.get("evidence"),
        "active_time_reduction_percent": time_reduction,
        "active_time_evidence": time_evidence,
        "standard_cost_reduction_percent": cost_reduction,
        "standard_cost_evidence": cost_evidence,
        "confidence": min(confidences) if confidences else 0.0,
    }


def _public_benefits(benefits: dict[str, object]) -> dict[str, object]:
    return {
        "quota_reduction_percent_range": benefits.get(
            "quota_reduction_percent_range"
        ),
        "additional_similar_tasks_range": benefits.get(
            "additional_similar_tasks_range"
        ),
        "quota_evidence": benefits.get("quota_evidence"),
        "active_time_reduction_percent": benefits.get("active_time_reduction_percent"),
        "active_time_evidence": benefits.get("active_time_evidence"),
        "standard_cost_reduction_percent": benefits.get("standard_cost_reduction_percent"),
        "standard_cost_evidence": benefits.get("standard_cost_evidence"),
        "pricing_snapshot_id": current_pricing_snapshot_id(),
    }


def _quota_benefit(
    current: dict[str, object],
    candidate: dict[str, object],
    quota_burn: dict[str, object],
    account: dict[str, object],
) -> dict[str, object]:
    empty = {
        "reduction_percent": None,
        "reduction_percent_range": None,
        "additional_tasks_range": None,
        "evidence": None,
        "confidence": None,
    }
    if (
        current.get("source_id") != "codex_local"
        or candidate.get("source_id") != "codex_local"
        or current.get("source_mode") == "api"
        or candidate.get("source_mode") == "api"
        or quota_burn.get("status") != "available"
        or account.get("quota_status") != "available"
    ):
        return empty
    aggregates = [
        item
        for item in quota_burn.get("aggregates", [])
        if isinstance(item, dict)
        and item.get("usable_for_recommendation") is True
    ]
    current_rows = _quota_aggregates_for_evaluation(current, aggregates)
    candidate_rows = _quota_aggregates_for_evaluation(candidate, aggregates)
    pairs = [
        (current_row, candidate_row)
        for current_row in current_rows
        for candidate_row in candidate_rows
        if current_row.get("window_id") == candidate_row.get("window_id")
        and _positive_int(current_row.get("window_seconds"))
        == _positive_int(candidate_row.get("window_seconds"))
    ]
    pairs.sort(key=lambda pair: _positive_int(pair[0].get("window_seconds")) or 0)
    for current_row, candidate_row in pairs:
        current_values = _dict(current_row.get("quota_per_work_unit_percent"))
        candidate_values = _dict(candidate_row.get("quota_per_work_unit_percent"))
        current_median = _positive_number(current_values.get("median"))
        current_p25 = _positive_number(current_values.get("p25"))
        current_p75 = _positive_number(current_values.get("p75"))
        candidate_median = _positive_number(candidate_values.get("median"))
        candidate_p25 = _positive_number(candidate_values.get("p25"))
        candidate_p75 = _positive_number(candidate_values.get("p75"))
        if None in {
            current_median,
            current_p25,
            current_p75,
            candidate_median,
            candidate_p25,
            candidate_p75,
        }:
            continue
        assert current_median is not None
        assert current_p25 is not None
        assert current_p75 is not None
        assert candidate_median is not None
        assert candidate_p25 is not None
        assert candidate_p75 is not None
        reduction_range = sorted(
            (
                _reduction_percent(current_p25, candidate_p75),
                _reduction_percent(current_p75, candidate_p25),
            )
        )
        remaining = _remaining_quota_percent(account, current_row)
        additional_range = None
        if remaining is not None:
            additional_range = [
                round(
                    remaining / candidate_p75 - remaining / current_p25,
                    1,
                ),
                round(
                    remaining / candidate_p25 - remaining / current_p75,
                    1,
                ),
            ]
        return {
            "reduction_percent": _reduction_percent(
                current_median,
                candidate_median,
            ),
            "reduction_percent_range": reduction_range,
            "additional_tasks_range": additional_range,
            "evidence": "official_window_attributed",
            "confidence": min(
                _confidence_value(current_row.get("confidence")),
                _confidence_value(candidate_row.get("confidence")),
            ),
        }
    return empty


def _quota_aggregates_for_evaluation(
    evaluation: dict[str, object],
    aggregates: Iterable[dict[str, object]],
) -> list[dict[str, object]]:
    model = (_text(evaluation.get("model")) or "").casefold()
    effort = (_text(evaluation.get("effort")) or "").casefold()
    return [
        item
        for item in aggregates
        if (_text(item.get("provider_id")) or "").casefold() == "openai"
        and (_text(item.get("raw_model_id")) or "").casefold() == model
        and (_text(item.get("reasoning_effort")) or "").casefold() == effort
    ]


def _remaining_quota_percent(
    account: dict[str, object],
    aggregate: dict[str, object],
) -> float | None:
    window_id = _text(aggregate.get("window_id"))
    if window_id is None:
        return None
    matches = [
        item
        for item in account.get("quota_windows", [])
        if isinstance(item, dict) and _text(item.get("window_id")) == window_id
    ]
    if len(matches) != 1:
        return None
    used = _number(matches[0].get("used_percent"))
    if used is None or not 0 <= used <= 100:
        return None
    return 100.0 - used


def _candidate_rank(item: dict[str, object]) -> tuple[float, float, float, str]:
    benefits = _dict(item.get("benefits"))
    reductions = [
        value
        for value in (
            _number(benefits.get("quota_reduction_percent")),
            _number(benefits.get("active_time_reduction_percent")),
            _number(benefits.get("standard_cost_reduction_percent")),
        )
        if value is not None
    ]
    candidate = _dict(item.get("candidate"))
    return (
        float(_nonnegative_int(item.get("material_count"))),
        max(reductions, default=-math.inf),
        _number(candidate.get("score")) or 0.0,
        _text(candidate.get("candidate_id")) or "",
    )


def _unique_workload_aggregate(
    evaluation: dict[str, object],
    aggregates: Iterable[dict[str, object]],
) -> dict[str, object] | None:
    model = (_text(evaluation.get("model")) or "").casefold()
    effort = (_text(evaluation.get("effort")) or "").casefold()
    matches = [
        row
        for row in aggregates
        if (_text(row.get("raw_model_id")) or "").casefold() == model
        and (_text(row.get("reasoning_effort")) or "").casefold() == effort
    ]
    return matches[0] if len(matches) == 1 else None


def _workload_confidence(
    aggregate: dict[str, object] | None,
    *,
    completed_work_units: int,
    coverage_complete: bool,
) -> float:
    if aggregate is None:
        return 0.0
    sample_cap = (
        1.0
        if completed_work_units >= QUALIFIED_WORK_UNITS
        else 0.55
        if completed_work_units >= PREVIEW_WORK_UNITS
        else 0.3
    )
    coverage_cap = 1.0 if coverage_complete else 0.5
    return round(min(_aggregate_confidence(aggregate), sample_cap, coverage_cap), 2)


def _aggregate_confidence(aggregate: dict[str, object] | None) -> float:
    if aggregate is None:
        return 0.0
    value = _number(aggregate.get("attribution_confidence"))
    return max(0.0, min(1.0, value if value is not None else 0.0))


def _aggregate_median_duration(aggregate: dict[str, object] | None) -> float | None:
    if aggregate is None or _nonnegative_int(aggregate.get("completed_work_units")) < PREVIEW_WORK_UNITS:
        return None
    value = _number(aggregate.get("median_active_duration_ms"))
    return value if value is not None and value > 0 else None


def _aggregate_cost_per_work_unit(aggregate: dict[str, object] | None) -> float | None:
    if aggregate is None:
        return None
    completed = _nonnegative_int(aggregate.get("completed_work_units"))
    model = _text(aggregate.get("raw_model_id"))
    if completed < PREVIEW_WORK_UNITS or not model:
        return None
    estimate = estimate_reference_cost(
        model,
        input_tokens=_optional_int(aggregate.get("input_tokens")),
        cached_input_tokens=_optional_int(aggregate.get("cached_input_tokens")),
        cache_write_input_tokens=_optional_int(aggregate.get("cache_write_input_tokens")),
        output_tokens=_optional_int(aggregate.get("output_tokens")),
        reasoning_output_tokens=_optional_int(aggregate.get("reasoning_tokens")),
    )
    if estimate.usd is None:
        return None
    return estimate.usd / completed


def _identity_confidence(
    recommendation: dict[str, object],
    current_id: str | None,
) -> float:
    if not current_id:
        return 0.0
    source = _text(recommendation.get("current_model_source"))
    status = _text(recommendation.get("current_model_detection_status"))
    if source == "manual":
        return 1.0
    if source != "terminal_session" or status in {None, "unmapped", "active_mixed", "unavailable", "scan_only"}:
        return 0.0
    return 1.0 if status == "active_single" else 0.8


def _route_identity_confidence(evaluation: dict[str, object]) -> float:
    return 0.8 if evaluation.get("source_mode") == "api" else 1.0


def _official_quota_exhausted(account: dict[str, object]) -> bool:
    if account.get("quota_status") != "available":
        return False
    return any(
        (_number(window.get("used_percent")) or 0.0) >= 100.0
        for window in account.get("quota_windows", [])
        if isinstance(window, dict)
    )


def _reduction_percent(current: float, candidate: float) -> float:
    if current <= 0:
        return 0.0
    return round((1.0 - candidate / current) * 100.0, 1)


def _confidence_level(confidence: float) -> str:
    if confidence >= 0.8:
        return "high"
    if confidence >= MEDIUM_CONFIDENCE:
        return "medium"
    return "low"


def _datetime(value: object) -> datetime | None:
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


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _positive_number(value: object) -> float | None:
    parsed = _number(value)
    return parsed if parsed is not None and parsed > 0 else None


def _confidence_value(value: object) -> float:
    parsed = _number(value)
    return max(0.0, min(1.0, parsed if parsed is not None else 0.0))


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _nonnegative_int(value: object) -> int:
    return _optional_int(value) or 0


def _positive_int(value: object) -> int:
    parsed = _nonnegative_int(value)
    return parsed if parsed > 0 else 0


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None
