from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Iterable, Mapping, Sequence


PREFERENCES = {"smart", "quality", "speed", "cost"}
MATERIAL_REDUCTION_PERCENT = 25.0
SMART_SCORE_GUARD = 5.0
EFFICIENCY_SCORE_GUARD = 10.0
QUALITY_GAIN_MINIMUM = 2.0


def build_recommendation_portfolio(
    evidence: Mapping[str, object],
    *,
    preference: str = "smart",
    prior_recommendation_epochs: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    if preference not in PREFERENCES:
        raise ValueError(f"unsupported recommendation preference: {preference}")

    current_id = _text(evidence.get("current_model_configuration_id"))
    portfolio = {
        "schema_version": 2,
        "source_mode": evidence.get("source_mode"),
        "resolved_data_source": evidence.get("resolved_data_source"),
        "source_resolution_reason": evidence.get("source_reason"),
        "preference": preference,
        "representative_configuration_id": current_id,
        "representative_reason": (
            "effective_current_configuration" if current_id else None
        ),
        "representative_evidence": dict(evidence),
        "status": "no_usage" if not current_id else "needs_test",
        "recommendation_lifecycle": _recommendation_lifecycle(),
        "decisions": [],
        "testable_candidate_ids": list(
            _text_items(evidence.get("testable_candidate_ids"))
        ),
    }
    if not current_id:
        if evidence.get("current_status") == "unmapped":
            portfolio["status"] = "needs_test"
        return portfolio

    rows = _row_index(evidence.get("resolved_result_rows"))
    current = _metrics(rows.get(current_id))
    current_status = str(evidence.get("current_status") or "needs_test")
    if current_status != "ready" or current is None:
        decision_status = "stale" if current_status == "stale" else "needs_test"
        portfolio["status"] = decision_status
        portfolio["decisions"] = [
            _empty_decision(
                current_id,
                decision=decision_status,
                reason=f"current_{current_status}",
                preference=preference,
            )
        ]
        return portfolio

    prior_cycle = _adopted_recommendation_cycle(
        prior_recommendation_epochs,
        current_id=current_id,
        preference=preference,
    )
    quality_anchor = current
    lifecycle = _recommendation_lifecycle()
    if prior_cycle is not None:
        anchor_id = _text(prior_cycle.get("current_model_configuration_id"))
        lifecycle = _recommendation_lifecycle(
            status="adopted",
            trigger="recommendation_accepted",
            anchor_configuration_id=anchor_id,
            adopted_configuration_id=current_id,
        )
        if _same_comparison_evidence(prior_cycle, evidence):
            portfolio["status"] = "keep"
            portfolio["recommendation_lifecycle"] = lifecycle
            portfolio["decisions"] = [
                _decision(
                    current_id=current_id,
                    current=current,
                    candidate=None,
                    preference=preference,
                    reason="recommendation_adopted",
                )
            ]
            return portfolio

        anchor = _metrics(rows.get(anchor_id or ""))
        if anchor is None or not _same_quality_contract(
            prior_cycle,
            evidence,
            anchor,
        ):
            portfolio["status"] = "keep"
            portfolio["recommendation_lifecycle"] = _recommendation_lifecycle(
                status="reoptimize_required",
                trigger="quality_anchor_unavailable",
                anchor_configuration_id=anchor_id,
                adopted_configuration_id=current_id,
            )
            portfolio["decisions"] = [
                _decision(
                    current_id=current_id,
                    current=current,
                    candidate=None,
                    preference=preference,
                    reason="quality_anchor_unavailable",
                )
            ]
            return portfolio
        quality_anchor = anchor
        lifecycle = _recommendation_lifecycle(
            status="proposed",
            trigger="new_evidence",
            anchor_configuration_id=anchor_id,
            adopted_configuration_id=current_id,
        )

    candidates = [
        metrics
        for candidate_id in _text_items(evidence.get("eligible_candidate_ids"))
        if (metrics := _metrics(rows.get(candidate_id))) is not None
    ]
    selected = _choose_candidate(
        current,
        candidates,
        preference,
        quality_anchor=quality_anchor,
    )
    if selected is None:
        comparison_candidate = _closest_candidate(candidates, preference)
        portfolio["status"] = "keep"
        if prior_cycle is not None:
            lifecycle = _recommendation_lifecycle(
                status="adopted",
                trigger="new_evidence_kept",
                anchor_configuration_id=_text(
                    prior_cycle.get("current_model_configuration_id")
                ),
                adopted_configuration_id=current_id,
            )
        portfolio["recommendation_lifecycle"] = lifecycle
        portfolio["decisions"] = [
            _keep_decision(
                current_id=current_id,
                current=current,
                comparison_candidate=comparison_candidate,
                preference=preference,
                quality_anchor=quality_anchor,
                reason=(
                    "no_eligible_candidate"
                    if not candidates
                    else "no_material_benefit"
                ),
            )
        ]
        return portfolio

    portfolio["status"] = "recommend"
    portfolio["recommendation_lifecycle"] = (
        lifecycle
        if lifecycle["status"] == "proposed"
        else _recommendation_lifecycle(
            status="proposed",
            trigger="initial",
            anchor_configuration_id=current_id,
        )
    )
    portfolio["decisions"] = [
        _decision(
            current_id=current_id,
            current=current,
            candidate=selected,
            preference=preference,
            quality_anchor=quality_anchor,
            reason=_recommend_reason(current, selected, preference),
        )
    ]
    return portfolio


def build_multi_recommendation_portfolio(
    contexts: Sequence[Mapping[str, object]],
    *,
    activity: Sequence[Mapping[str, object]] = (),
    fallback_evidence: Mapping[str, object] | None = None,
    unmapped_active_session_count: int = 0,
    preference: str = "smart",
    prior_recommendation_epochs: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    if preference not in PREFERENCES:
        raise ValueError(f"unsupported recommendation preference: {preference}")
    if not contexts:
        fallback = build_recommendation_portfolio(
            fallback_evidence or {},
            preference=preference,
            prior_recommendation_epochs=prior_recommendation_epochs,
        )
        fallback["unmapped_active_session_count"] = unmapped_active_session_count
        return fallback

    representative_id, representative_reason = _representative_configuration(
        contexts,
        activity,
    )
    representative_context = next(
        (
            context
            for context in contexts
            if context.get("current_model_configuration_id") == representative_id
        ),
        contexts[0],
    )
    source_modes = {
        configuration_id: context.get("source_mode")
        for context in contexts
        if (
            configuration_id := _text(
                context.get("current_model_configuration_id")
            )
        )
    }
    individual = [
        build_recommendation_portfolio(
            context,
            preference=preference,
            prior_recommendation_epochs=prior_recommendation_epochs,
        )
        for context in contexts
    ]
    representative = next(
        (
            item
            for item in individual
            if item.get("representative_configuration_id") == representative_id
        ),
        individual[0],
    )
    return {
        "schema_version": 2,
        "source_mode": representative_context.get("source_mode"),
        "source_mode_by_configuration_id": source_modes,
        "resolved_data_source": representative_context.get("resolved_data_source"),
        "source_resolution_reason": representative_context.get("source_reason"),
        "preference": preference,
        "representative_configuration_id": representative_id,
        "representative_reason": representative_reason,
        "representative_evidence": dict(representative_context),
        "status": representative.get("status"),
        "recommendation_lifecycle": representative.get(
            "recommendation_lifecycle",
            _recommendation_lifecycle(),
        ),
        "decisions": [
            decision
            for item in individual
            for decision in _mapping_items(item.get("decisions"))
        ],
        "testable_candidate_ids": _combined_text_items(
            item.get("testable_candidate_ids") for item in individual
        ),
        "unmapped_active_session_count": unmapped_active_session_count,
    }


def _recommendation_lifecycle(
    *,
    status: str = "none",
    trigger: str | None = None,
    anchor_configuration_id: str | None = None,
    adopted_configuration_id: str | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": status,
        "trigger": trigger,
        "anchor_configuration_id": anchor_configuration_id,
        "adopted_configuration_id": adopted_configuration_id,
    }


def _adopted_recommendation_cycle(
    epochs: Sequence[Mapping[str, object]],
    *,
    current_id: str,
    preference: str,
) -> Mapping[str, object] | None:
    for epoch in reversed(epochs):
        if epoch.get("lifecycle_status") != "open":
            continue
        if _text(epoch.get("recommended_model_configuration_id")) != current_id:
            continue
        if _text(epoch.get("preference")) != preference:
            continue
        if _text(epoch.get("segment_kind")) not in {None, "recommendation", "actual_switch"}:
            continue
        return epoch
    return None


def _same_comparison_evidence(
    epoch: Mapping[str, object],
    evidence: Mapping[str, object],
) -> bool:
    prior_source = _text(epoch.get("resolved_data_source"))
    prior_snapshot = _text(epoch.get("evaluation_snapshot_id"))
    prior_pricing = _text(epoch.get("pricing_snapshot_id"))
    return (
        prior_source is not None
        and prior_source == _text(evidence.get("resolved_data_source"))
        and prior_snapshot is not None
        and prior_snapshot == _text(evidence.get("source_snapshot_id"))
        and prior_pricing is not None
        and prior_pricing == _text(evidence.get("pricing_snapshot_id"))
    )


def _same_quality_contract(
    epoch: Mapping[str, object],
    evidence: Mapping[str, object],
    anchor: Mapping[str, object],
) -> bool:
    prior_source = _text(epoch.get("resolved_data_source"))
    if prior_source is None or prior_source != _text(
        evidence.get("resolved_data_source")
    ):
        return False
    prior_question_pack = _text(epoch.get("question_pack_version"))
    prior_grader = _text(epoch.get("grader_version"))
    return (
        prior_question_pack is not None
        and prior_question_pack == _text(anchor.get("question_pack_version"))
        and prior_grader is not None
        and prior_grader == _text(anchor.get("grader_version"))
    )


def _representative_configuration(
    contexts: Sequence[Mapping[str, object]],
    activity: Sequence[Mapping[str, object]],
) -> tuple[str | None, str | None]:
    context_ids = {
        configuration_id
        for context in contexts
        if (configuration_id := _text(context.get("current_model_configuration_id")))
    }
    active = [
        item
        for item in activity
        if _text(item.get("model_configuration_id")) in context_ids
    ]
    if not active:
        configuration_id = _text(contexts[0].get("current_model_configuration_id"))
        return configuration_id, (
            "effective_current_configuration" if configuration_id else None
        )

    active.sort(
        key=lambda item: (
            0 if bool(item.get("is_currently_producing", False)) else 1,
            -_timestamp_value(item.get("last_active_at")),
            -_integer(item.get("active_session_count")),
            _text(item.get("model_configuration_id")) or "",
        )
    )
    selected = active[0]
    if bool(selected.get("is_currently_producing", False)):
        reason = "currently_producing"
    elif _text(selected.get("last_active_at")):
        reason = "most_recent_activity"
    else:
        reason = "active_session_count_fallback"
    return _text(selected.get("model_configuration_id")), reason


def _choose_candidate(
    current: dict[str, object],
    candidates: Sequence[dict[str, object]],
    preference: str,
    *,
    quality_anchor: Mapping[str, object] | None = None,
) -> dict[str, object] | None:
    current_score = float(current["score"])
    quality_anchor_score = float((quality_anchor or current)["score"])
    qualified: list[dict[str, object]] = []
    for candidate in candidates:
        score_delta = float(candidate["score"]) - current_score
        quality_score_delta = float(candidate["score"]) - quality_anchor_score
        time_reduction = _reduction(
            _number(current.get("elapsed_seconds")),
            _number(candidate.get("elapsed_seconds")),
        )
        cost_reduction = _reduction(
            _known_cost(current),
            _known_cost(candidate),
        )
        candidate["score_delta"] = round(score_delta, 1)
        candidate["quality_score_delta"] = round(quality_score_delta, 1)
        candidate["time_reduction"] = time_reduction
        candidate["cost_reduction"] = cost_reduction

        if preference == "smart":
            if (
                quality_score_delta >= -SMART_SCORE_GUARD
                and time_reduction is not None
                and cost_reduction is not None
                and max(time_reduction, cost_reduction) >= MATERIAL_REDUCTION_PERCENT
                and min(time_reduction, cost_reduction) >= 0
            ):
                qualified.append(candidate)
        elif preference == "quality":
            if quality_score_delta >= QUALITY_GAIN_MINIMUM:
                qualified.append(candidate)
        elif preference == "speed":
            if (
                quality_score_delta >= -EFFICIENCY_SCORE_GUARD
                and time_reduction is not None
                and time_reduction >= MATERIAL_REDUCTION_PERCENT
            ):
                qualified.append(candidate)
        elif (
            quality_score_delta >= -EFFICIENCY_SCORE_GUARD
            and cost_reduction is not None
            and cost_reduction >= MATERIAL_REDUCTION_PERCENT
        ):
            qualified.append(candidate)

    if not qualified:
        return None
    qualified.sort(key=lambda item: _sort_key(item, preference))
    return qualified[0]


def _closest_candidate(
    candidates: Sequence[dict[str, object]],
    preference: str,
) -> dict[str, object] | None:
    if not candidates:
        return None

    def key(candidate: Mapping[str, object]) -> tuple[object, ...]:
        score_delta = _quality_score_delta(candidate)
        elapsed = _number(candidate.get("elapsed_seconds"))
        cost = _known_cost(candidate)
        configuration_id = str(candidate["model_configuration_id"])
        if preference == "quality":
            return (-float(candidate["score"]), _missing_last(elapsed), _missing_last(cost), configuration_id)
        if preference == "speed":
            return (
                score_delta < -EFFICIENCY_SCORE_GUARD,
                max(0.0, -EFFICIENCY_SCORE_GUARD - score_delta),
                _missing_last(elapsed),
                -float(candidate["score"]),
                configuration_id,
            )
        if preference == "cost":
            return (
                score_delta < -EFFICIENCY_SCORE_GUARD,
                max(0.0, -EFFICIENCY_SCORE_GUARD - score_delta),
                _missing_last(cost),
                -float(candidate["score"]),
                _missing_last(elapsed),
                configuration_id,
            )
        time_reduction = _number(candidate.get("time_reduction"))
        cost_reduction = _number(candidate.get("cost_reduction"))
        reductions = [value for value in (time_reduction, cost_reduction) if value is not None]
        best_reduction = max(reductions) if reductions else -math.inf
        worst_reduction = min(reductions) if len(reductions) == 2 else -math.inf
        return (
            score_delta < -SMART_SCORE_GUARD,
            max(0.0, -SMART_SCORE_GUARD - score_delta),
            len(reductions) < 2,
            max(0.0, MATERIAL_REDUCTION_PERCENT - best_reduction),
            max(0.0, -worst_reduction),
            -score_delta,
            configuration_id,
        )

    return min(candidates, key=key)


def _sort_key(candidate: Mapping[str, object], preference: str) -> tuple[object, ...]:
    score = float(candidate["score"])
    elapsed = _number(candidate.get("elapsed_seconds"))
    cost = _known_cost(candidate)
    configuration_id = str(candidate["model_configuration_id"])
    if preference == "smart":
        time_reduction = float(candidate["time_reduction"])
        cost_reduction = float(candidate["cost_reduction"])
        return (
            0 if time_reduction > 0 and cost_reduction > 0 else 1,
            -float(candidate["score_delta"]),
            -max(time_reduction, cost_reduction),
            -min(time_reduction, cost_reduction),
            configuration_id,
        )
    if preference == "quality":
        return (-score, _missing_last(elapsed), _missing_last(cost), configuration_id)
    if preference == "speed":
        return (_missing_last(elapsed), -score, _missing_last(cost), configuration_id)
    return (_missing_last(cost), -score, _missing_last(elapsed), configuration_id)


def _quality_score_delta(candidate: Mapping[str, object]) -> float:
    value = _number(candidate.get("quality_score_delta"))
    if value is None:
        value = _number(candidate.get("score_delta"))
    return value if value is not None else 0.0


def _decision(
    *,
    current_id: str,
    current: Mapping[str, object],
    candidate: Mapping[str, object] | None,
    preference: str,
    quality_anchor: Mapping[str, object] | None = None,
    reason: str,
) -> dict[str, object]:
    if candidate is None:
        return {
            **_empty_decision(
                current_id,
                decision="keep",
                reason=reason,
                preference=preference,
            ),
            "quality": _quality_change(current, None),
            "time": _time_change(current, None),
            "reference_cost": _cost_change(current, None),
        }

    score_delta = _quality_score_delta(candidate)
    return {
        "current_model_configuration_id": current_id,
        "candidate_model_configuration_id": candidate["model_configuration_id"],
        "comparison_candidate_model_configuration_id": candidate["model_configuration_id"],
        "comparison_candidate_reasons": [],
        "decision": "recommend",
        "reason": reason,
        "quality_tradeoff": (
            preference in {"speed", "cost"}
            and -EFFICIENCY_SCORE_GUARD <= score_delta <= -6.0
        ),
        "quality_warning_question_ids": _quality_warnings(current, candidate),
        "quality_guard": _quality_guard(
            current,
            candidate,
            preference=preference,
            decision="recommend",
            quality_anchor=quality_anchor,
        ),
        "quality": _quality_change(current, candidate),
        "time": _time_change(current, candidate),
        "reference_cost": _cost_change(current, candidate),
        "primary_benefit": _primary_benefit(candidate, preference),
    }


def _keep_decision(
    *,
    current_id: str,
    current: Mapping[str, object],
    comparison_candidate: Mapping[str, object] | None,
    preference: str,
    quality_anchor: Mapping[str, object] | None = None,
    reason: str,
) -> dict[str, object]:
    return {
        **_empty_decision(
            current_id,
            decision="keep",
            reason=reason,
            preference=preference,
        ),
        "comparison_candidate_model_configuration_id": (
            comparison_candidate.get("model_configuration_id")
            if comparison_candidate is not None
            else None
        ),
        "comparison_candidate_reasons": _rejection_reasons(
            comparison_candidate,
            preference,
        ),
        "quality_warning_question_ids": (
            _quality_warnings(current, comparison_candidate)
            if comparison_candidate is not None
            else []
        ),
        "quality_guard": _quality_guard(
            current,
            comparison_candidate,
            preference=preference,
            decision="keep",
            quality_anchor=quality_anchor,
        ),
        "quality": _quality_change(current, comparison_candidate),
        "time": _time_change(current, comparison_candidate),
        "reference_cost": _cost_change(current, comparison_candidate),
    }


def _empty_decision(
    current_id: str,
    *,
    decision: str,
    reason: str,
    preference: str,
) -> dict[str, object]:
    return {
        "current_model_configuration_id": current_id,
        "candidate_model_configuration_id": None,
        "comparison_candidate_model_configuration_id": None,
        "comparison_candidate_reasons": [],
        "decision": decision,
        "reason": reason,
        "quality_tradeoff": False,
        "quality_warning_question_ids": [],
        "quality_guard": _quality_guard(
            None,
            None,
            preference=preference,
            decision=decision,
        ),
        "quality": {
            "current_score": None,
            "candidate_score": None,
            "score_delta": None,
        },
        "time": {
            "current_seconds": None,
            "candidate_seconds": None,
            "reduction_percent": None,
        },
        "reference_cost": {
            "current_usd": None,
            "candidate_usd": None,
            "reduction_percent": None,
        },
        "primary_benefit": None,
    }


def _quality_guard(
    current: Mapping[str, object] | None,
    candidate: Mapping[str, object] | None,
    *,
    preference: str,
    decision: str,
    quality_anchor: Mapping[str, object] | None = None,
) -> dict[str, object]:
    if preference == "quality":
        rule = "minimum_gain"
        threshold = QUALITY_GAIN_MINIMUM
    else:
        rule = "maximum_loss"
        threshold = (
            SMART_SCORE_GUARD
            if preference == "smart"
            else EFFICIENCY_SCORE_GUARD
        )

    score_delta = None
    if current is not None and candidate is not None:
        score_delta = _quality_score_delta(candidate)

    if score_delta is None:
        status = "unavailable"
        passed = None
    elif preference == "quality":
        passed = score_delta >= threshold
        status = (
            "passed"
            if passed
            else ("current_is_best" if score_delta <= 0 else "failed")
        )
    else:
        passed = score_delta >= -threshold
        status = (
            "quality_improved"
            if score_delta > 0
            else ("passed" if passed else "failed")
        )

    guard = {
        "schema_version": 1,
        "status": status,
        "rule": rule,
        "preference": preference,
        "decision": decision,
        "threshold_points": threshold,
        "score_delta_points": score_delta,
        "passed": passed,
    }
    if quality_anchor is not None and current is not None:
        anchor_id = _text(quality_anchor.get("model_configuration_id"))
        current_id = _text(current.get("model_configuration_id"))
        if anchor_id and anchor_id != current_id:
            guard["anchor_model_configuration_id"] = anchor_id
    return guard


def _rejection_reasons(
    candidate: Mapping[str, object] | None,
    preference: str,
) -> list[str]:
    if candidate is None:
        return []
    score_delta = _quality_score_delta(candidate)
    time_reduction = _number(candidate.get("time_reduction"))
    cost_reduction = _number(candidate.get("cost_reduction"))
    reasons: list[str] = []
    if preference == "quality":
        if score_delta < QUALITY_GAIN_MINIMUM:
            reasons.append("quality_gain_below_minimum")
        return reasons

    guard = SMART_SCORE_GUARD if preference == "smart" else EFFICIENCY_SCORE_GUARD
    if score_delta < -guard:
        reasons.append("quality_guard_failed")
    if preference == "speed":
        if time_reduction is None:
            reasons.append("time_unavailable")
        elif time_reduction < MATERIAL_REDUCTION_PERCENT:
            reasons.append("time_gain_below_minimum")
        return reasons
    if preference == "cost":
        if cost_reduction is None:
            reasons.append("reference_cost_unavailable")
        elif cost_reduction < MATERIAL_REDUCTION_PERCENT:
            reasons.append("reference_cost_gain_below_minimum")
        return reasons

    if time_reduction is None:
        reasons.append("time_unavailable")
    if cost_reduction is None:
        reasons.append("reference_cost_unavailable")
    available = [value for value in (time_reduction, cost_reduction) if value is not None]
    if len(available) == 2 and max(available) < MATERIAL_REDUCTION_PERCENT:
        reasons.append("material_benefit_below_minimum")
    if time_reduction is not None and time_reduction < 0:
        reasons.append("time_regressed")
    if cost_reduction is not None and cost_reduction < 0:
        reasons.append("reference_cost_regressed")
    return reasons


def _quality_change(
    current: Mapping[str, object],
    candidate: Mapping[str, object] | None,
) -> dict[str, object]:
    current_score = float(current["score"])
    candidate_score = float(candidate["score"]) if candidate is not None else None
    return {
        "current_score": current_score,
        "candidate_score": candidate_score,
        "score_delta": (
            round(candidate_score - current_score, 1)
            if candidate_score is not None
            else None
        ),
    }


def _time_change(
    current: Mapping[str, object],
    candidate: Mapping[str, object] | None,
) -> dict[str, object]:
    current_value = _number(current.get("elapsed_seconds"))
    candidate_value = (
        _number(candidate.get("elapsed_seconds")) if candidate is not None else None
    )
    return {
        "current_seconds": current_value,
        "candidate_seconds": candidate_value,
        "reduction_percent": _reduction(current_value, candidate_value),
    }


def _cost_change(
    current: Mapping[str, object],
    candidate: Mapping[str, object] | None,
) -> dict[str, object]:
    current_value = _known_cost(current)
    candidate_value = _known_cost(candidate) if candidate is not None else None
    return {
        "current_usd": current_value,
        "candidate_usd": candidate_value,
        "reduction_percent": _reduction(current_value, candidate_value),
    }


def _primary_benefit(
    candidate: Mapping[str, object],
    preference: str,
) -> dict[str, object]:
    if preference == "quality":
        return {
            "kind": "quality",
            "gain_points": float(candidate["score_delta"]),
        }
    if preference == "speed":
        kind = "time"
    elif preference == "cost":
        kind = "reference_cost"
    else:
        time_reduction = float(candidate["time_reduction"])
        cost_reduction = float(candidate["cost_reduction"])
        kind = "time" if time_reduction >= cost_reduction else "reference_cost"
    reduction = (
        candidate["time_reduction"]
        if kind == "time"
        else candidate["cost_reduction"]
    )
    return {"kind": kind, "reduction_percent": reduction}


def _recommend_reason(
    current: Mapping[str, object],
    candidate: Mapping[str, object],
    preference: str,
) -> str:
    if preference == "quality":
        gain = float(candidate["score"]) - float(current["score"])
        return "material_quality_gain" if gain >= 5 else "quality_gain_with_tradeoff"
    return {
        "smart": "material_efficiency_gain",
        "speed": "material_time_gain",
        "cost": "material_reference_cost_gain",
    }[preference]


def _quality_warnings(
    current: Mapping[str, object],
    candidate: Mapping[str, object],
) -> list[str]:
    current_scores = _question_scores(current.get("question_results"))
    candidate_scores = _question_scores(candidate.get("question_results"))
    return sorted(
        question_id
        for question_id, current_score in current_scores.items()
        if question_id in candidate_scores
        and current_score - candidate_scores[question_id] > 5
    )


def _question_scores(value: object) -> dict[str, float]:
    scores: dict[str, float] = {}
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return scores
    for item in value:
        if not isinstance(item, Mapping):
            continue
        question_id = _text(item.get("question_id"))
        score = _number(item.get("semantic_score"))
        total = _number(item.get("semantic_total"))
        if question_id and score is not None and total and total > 0:
            scores[question_id] = score * 20.0 / total
    return scores


def _metrics(row: Mapping[str, object] | None) -> dict[str, object] | None:
    if row is None:
        return None
    configuration_id = _text(row.get("model_configuration_id"))
    score = _number(row.get("overall_score"))
    if configuration_id is None or score is None:
        return None
    return {**row, "model_configuration_id": configuration_id, "score": score}


def _row_index(value: object) -> dict[str, Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return {}
    return {
        configuration_id: item
        for item in value
        if isinstance(item, Mapping)
        if (configuration_id := _text(item.get("model_configuration_id"))) is not None
    }


def _known_cost(row: Mapping[str, object] | None) -> float | None:
    if row is None or row.get("cost_coverage") != "complete":
        return None
    value = _number(row.get("estimated_cost_usd"))
    return value if value is not None and value >= 0 else None


def _reduction(current: float | None, candidate: float | None) -> float | None:
    if current is None or current <= 0 or candidate is None or candidate < 0:
        return None
    return round((current - candidate) / current * 100.0, 1)


def _missing_last(value: float | None) -> float:
    return value if value is not None else math.inf


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _text_items(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [text for item in value if (text := _text(item)) is not None]


def _mapping_items(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _combined_text_items(values: Iterable[object]) -> list[str]:
    combined: list[str] = []
    for value in values:
        for item in _text_items(value):
            if item not in combined:
                combined.append(item)
    return combined


def _timestamp_value(value: object) -> float:
    text = _text(value)
    if text is None:
        return float("-inf")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return float("-inf")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _integer(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
