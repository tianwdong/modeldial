from __future__ import annotations

from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from typing import Iterable

from .candidate_evidence import build_candidate_evidence
from .costing import current_pricing_snapshot_id, estimate_reference_cost
from .graders import (
    cache_propagation_certificate_facets,
    mutation_test_design_facets,
)
from .legacy_scan_compat import (
    SCAN_PHASE,
    metadata_question_count,
    metadata_question_ids,
    normalized_capability_label,
    normalized_detail_label,
    normalize_phase,
    planned_attempts_payload,
)
from .leaderboard_decision_tags import (
    assign_leaderboard_decision_tags as _assign_leaderboard_decision_tags,
    duration_text as _duration_text,
)
from .model_identity import (
    infer_reasoning_suffix_aliases,
    model_display_label,
    resolve_model_display_identity,
)
from .models import (
    ModelIngressConfig,
    ResolvedScanTarget,
    RunMetadata,
    ScanBudgetConfig,
    ScanResult,
    TargetConfig,
)
from .recommendation_state import resolve_recommendation_decision
from .route_identity import build_route_fingerprint


_KNOWN_COST_STATUSES = {"estimated", "observed"}


@dataclass(frozen=True)
class DashboardModelView:
    id: str
    label: str
    model: str
    model_id: str
    effort: str
    display_name: str
    source_id: str | None = None
    source_title: str | None = None
    source_mode: str | None = None
    connection_id: str | None = None
    connection_name: str | None = None
    family_id: str | None = None
    variant_id: str | None = None
    reasoning_tokens_supported: bool = True
    route_fingerprint: str | None = None


def build_dashboard_summary(
    history: list[ScanResult],
    model_catalog: ModelIngressConfig | Iterable[ResolvedScanTarget | TargetConfig],
    current_run_id: str | None = None,
    active_run: dict[str, object] | None = None,
    run_metadata: dict[str, object] | None = None,
    scan_interval_seconds: int | None = None,
    run_metadata_by_id: dict[str, dict[str, object]] | None = None,
    current_default_candidate_id: str | None = None,
    scan_budget: ScanBudgetConfig | None = None,
    current_question_pack_id: str | None = None,
    current_question_pack_version: str | None = None,
) -> dict[str, object]:
    current_run_id = current_run_id or (history[-1].run_id if history else None)
    current_run_history = (
        [item for item in history if item.run_id == current_run_id] if current_run_id else []
    )
    raw_current_metadata = run_metadata
    if raw_current_metadata is None:
        active_metadata = (active_run or {}).get("run_metadata")
        raw_current_metadata = (
            dict(active_metadata) if isinstance(active_metadata, dict) else None
        )
    uses_candidate_evidence = bool(
        isinstance(raw_current_metadata, dict)
        and (
            "selection_mode" in raw_current_metadata
            or "requested_candidate_ids" in raw_current_metadata
        )
    )
    if isinstance(model_catalog, ModelIngressConfig):
        configured_models = _enabled_model_views(model_catalog)
        enabled_models = _enabled_model_views(
            model_catalog,
            include_disabled=uses_candidate_evidence,
        )
    else:
        materialized_catalog = list(model_catalog)
        configured_models = _enabled_model_views(materialized_catalog)
        enabled_models = _enabled_model_views(
            materialized_catalog,
            include_disabled=uses_candidate_evidence,
        )
    configured_candidate_ids = {model.id for model in configured_models}
    planned_attempts = _planned_attempt_payload(
        active_run,
        by_candidate_key="planned_attempts_by_candidate",
        by_label_key="planned_attempts",
        fallback_payload=planned_attempts_payload(active_run),
    )
    metadata = _run_metadata(
        current_run_id=current_run_id,
        active_run=active_run,
        run_metadata=run_metadata,
        fallback_question_count=_fallback_question_count(planned_attempts),
    )
    evaluation_profile_id = str(
        metadata.get("evaluation_profile_id") or "legacy_full"
    )
    evaluation_result_level = str(
        metadata.get("evaluation_result_level") or "unknown"
    )
    selection_mode = str(metadata.get("selection_mode") or "regular")
    uses_strict_current_round = uses_candidate_evidence and selection_mode in {
        "regular",
        "custom",
    }
    recommendation_status = str(metadata.get("status") or "legacy")
    requested_candidate_ids = {
        str(candidate_id)
        for candidate_id in metadata.get("requested_candidate_ids", [])
    }
    is_active_scan = recommendation_status in {"running", "paused"}
    projects_current_selection = bool(
        uses_strict_current_round
        and not is_active_scan
        and evaluation_result_level == "provisional"
        and configured_candidate_ids != requested_candidate_ids
    )
    repair_candidate_ids: set[str] = set()
    repair_candidate_id = (active_run or {}).get("repair_candidate_id")
    if isinstance(repair_candidate_id, str) and repair_candidate_id.strip():
        repair_candidate_ids.add(repair_candidate_id.strip())
    repair_candidate_id_list = (active_run or {}).get("repair_candidate_ids")
    if isinstance(repair_candidate_id_list, list):
        repair_candidate_ids.update(
            str(candidate_id).strip()
            for candidate_id in repair_candidate_id_list
            if str(candidate_id).strip()
        )
    active_candidate_ids = (
        repair_candidate_ids
        if is_active_scan and repair_candidate_ids
        else requested_candidate_ids
    )
    repair_pack_matches = (
        (
            current_question_pack_id is None
            or str(metadata.get("question_pack_id") or "unknown")
            == current_question_pack_id
        )
        and (
            current_question_pack_version is None
            or str(metadata.get("question_pack_version") or "unknown")
            == current_question_pack_version
        )
    )
    current_pack_id = str(
        current_question_pack_id
        or metadata.get("question_pack_id")
        or "unknown"
    )
    current_pack_version = str(
        current_question_pack_version
        or metadata.get("question_pack_version")
        or "unknown"
    )
    models_by_id = {model.id: model for model in enabled_models}
    models_by_label = {model.label: model for model in enabled_models}
    active_entries = _active_entry_payloads(active_run, models_by_id, models_by_label)

    current_grouped: dict[str, list[ScanResult]] = defaultdict(list)
    for item in current_run_history:
        model_view = _match_model_view(item, models_by_id, models_by_label)
        current_grouped[
            model_view.id if model_view else _fallback_history_key(item)
        ].append(item)
    metadata_by_id = dict(run_metadata_by_id or {})
    if current_run_id and current_run_id not in metadata_by_id:
        current_metadata = dict(metadata)
        if not uses_candidate_evidence:
            for key in (
                "selection_mode",
                "requested_candidate_ids",
                "regular_candidate_ids",
                "is_complete_regular_round",
            ):
                current_metadata.pop(key, None)
        metadata_by_id[current_run_id] = current_metadata
    evidence_by_candidate = {}
    if uses_candidate_evidence:
        required_question_ids = tuple(
            str(question_id)
            for question_id in metadata.get("question_ids", [])
            if str(question_id)
        )
        history_by_candidate: dict[str, list[ScanResult]] = defaultdict(list)
        for candidate_id in requested_candidate_ids:
            history_by_candidate.setdefault(candidate_id, [])
        for item in history:
            model_view = _match_model_view(item, models_by_id, models_by_label)
            if model_view is not None:
                history_by_candidate[model_view.id].append(item)
        evidence_metadata_by_id = dict(metadata_by_id)
        if is_active_scan and repair_candidate_ids and current_run_id:
            evidence_metadata_by_id[str(current_run_id)] = {
                **evidence_metadata_by_id.get(str(current_run_id), {}),
                **metadata,
                "status": "degraded",
            }
        evidence_by_candidate = build_candidate_evidence(
            history_by_candidate,
            evidence_metadata_by_id,
            current_run_id=current_run_id,
            current_pack_id=current_pack_id,
            current_pack_version=current_pack_version,
            required_question_count=max(
                1,
                metadata_question_count(metadata),
            ),
            required_question_ids=required_question_ids or None,
        )

    cards: list[dict[str, object]] = []
    ingress_groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for model_view in enabled_models:
        evidence = evidence_by_candidate.get(model_view.id)
        is_active_candidate = is_active_scan and model_view.id in active_candidate_ids
        is_requested_candidate = model_view.id in requested_candidate_ids
        uses_current_round_results = repair_pack_matches and (
            is_active_candidate
            or (uses_strict_current_round and is_requested_candidate)
        )
        bucket = (
            current_grouped[model_view.id]
            if uses_current_round_results
            else list(evidence.results)
            if uses_candidate_evidence and evidence
            else current_grouped[model_view.id]
        )
        total = len(bucket)
        hits_516 = sum(1 for item in bucket if item.reasoning_tokens == 516)
        passes = sum(1 for item in bucket if item.answer_ok)
        scan_results = _deduplicate_question_results(
            [item for item in bucket if normalize_phase(item.phase) == SCAN_PHASE]
        )
        route_identity_status = _route_identity_status(
            model_view,
            scan_results,
        )
        route_is_comparable = (
            model_view.source_mode != "api"
            or route_identity_status == "matched"
        )
        question_results = _question_result_payload(
            _order_question_results(
                scan_results,
                list(metadata.get("question_ids", [])),
            )
        )
        correct = sum(1 for item in scan_results if item.answer_ok)
        semantic_score, semantic_total = _semantic_score(scan_results)
        score_facets = _score_facets(scan_results)
        expected_total = _planned_total(
            planned_attempts, model_view, len(scan_results)
        )
        if uses_strict_current_round and is_requested_candidate:
            expected_total = max(
                expected_total,
                max(1, int(metadata.get("question_count") or 0)),
            )
        overall_score = None
        if (
            evaluation_result_level != "provisional"
            and expected_total > 0
            and len(scan_results) >= expected_total
            and semantic_total > 0
            and not _has_unscored_execution_error(scan_results)
        ):
            overall_score = int(round(semantic_score * 100 / semantic_total))
        active_entry = active_entries.get(model_view.id)
        completed_scan_results = [
            item for item in scan_results if _is_completed_model_call(item)
        ]
        reasoning_values = [
            item.reasoning_tokens for item in bucket if item.reasoning_tokens is not None
        ]
        elapsed_values = [
            float(item.elapsed_seconds)
            for item in completed_scan_results
            if item.elapsed_seconds is not None
        ]
        elapsed_seconds = _scan_elapsed_total(completed_scan_results)
        estimated_costs, cost_coverage = _reference_cost_summary(completed_scan_results)
        latest = bucket[-1] if bucket else None
        historical_score_text = None
        historical_valid_at = None
        if (
            evidence
            and evidence.valid_run_id
            and evidence.valid_run_id != current_run_id
        ):
            historical_score, historical_total = _semantic_score(list(evidence.results))
            historical_score_text = _score_text(historical_score, historical_total)
            historical_valid_at = evidence.valid_at
        if (
            projects_current_selection
            and model_view.id in configured_candidate_ids
            and not is_requested_candidate
        ):
            is_current_run_eligible = bool(
                evidence and evidence.is_current_pack_comparable
            ) and route_is_comparable
        else:
            is_current_run_eligible = bool(
                evidence
                and is_requested_candidate
                and evidence.is_current_run_eligible
            ) if uses_candidate_evidence else bool(bucket)
            is_current_run_eligible = (
                is_current_run_eligible and route_is_comparable
            )
        hard_failure_question_ids = (
            list(evidence.hard_failure_question_ids)
            if evidence and is_requested_candidate
            else []
        )
        repair_requires_full_scan = bool(
            hard_failure_question_ids
            and uses_strict_current_round
            and not repair_pack_matches
        )
        repairable_question_ids = (
            hard_failure_question_ids
            if uses_strict_current_round and repair_pack_matches
            else []
        )
        card = {
            "id": model_view.id,
            "label": model_view.label,
            "display_name": model_view.display_name,
            "model": model_view.model,
            "model_id": model_view.model_id,
            "effort": model_view.effort,
            "source_id": model_view.source_id,
            "source_title": model_view.source_title,
            "source_mode": model_view.source_mode,
            "connection_id": model_view.connection_id,
            "connection_name": model_view.connection_name,
            "family_id": model_view.family_id,
            "variant_id": model_view.variant_id,
            "reasoning_tokens_supported": model_view.reasoning_tokens_supported,
            "route_identity_status": route_identity_status,
            "recent_count": total,
            "pass_count": passes,
            "question_count": len(scan_results),
            "question_completed": len(scan_results),
            "question_attempted": expected_total,
            "correct_count": correct,
            "semantic_score": semantic_score,
            "semantic_total": semantic_total,
            "evaluation_profile_id": evaluation_profile_id,
            "evaluation_result_level": evaluation_result_level,
            "mode_score": semantic_score,
            "mode_score_max": max(
                0,
                int(metadata.get("evaluation_score_max") or semantic_total),
            ),
            "mode_score_text": _score_text(
                semantic_score,
                max(0, int(metadata.get("evaluation_score_max") or semantic_total)),
            ),
            "score_text": _score_text(semantic_score, semantic_total),
            "scoring_mode": "semantic_q1_q5_equal_v2",
            "overall_score": overall_score,
            "overall_score_text": (
                _score_text(overall_score, 100)
                if overall_score is not None
                else None
            ),
            "score_facets": score_facets,
            "question_results": question_results,
            "hits_516": hits_516,
            "hit_rate_516": _percent(hits_516, total),
            "pass_rate": _percent(passes, total),
            "avg_reasoning_tokens": round(sum(reasoning_values) / len(reasoning_values))
            if reasoning_values else 0,
            "median_elapsed_seconds": _median(elapsed_values),
            "elapsed_seconds": elapsed_seconds,
            "estimated_cost_usd": round(sum(estimated_costs), 6) if estimated_costs else None,
            "cost_coverage": cost_coverage,
            "latest_reasoning_tokens": latest.reasoning_tokens if latest else None,
            "latest_status": latest.final_status if latest else None,
            "sparkline": reasoning_values[-12:],
            "latest_valid_run_id": evidence.valid_run_id if evidence and not is_active_candidate else None,
            "latest_valid_at": evidence.valid_at if evidence and not is_active_candidate else None,
            "latest_attempt_run_id": evidence.latest_attempt_run_id if evidence and not is_active_candidate else None,
            "latest_attempt_at": evidence.latest_attempt_at if evidence and not is_active_candidate else None,
            "latest_attempt_status": evidence.latest_attempt_status if evidence and not is_active_candidate else None,
            "latest_attempt_error_category": evidence.latest_attempt_error_category if evidence and not is_active_candidate else None,
            "latest_attempt_error_summary": evidence.latest_attempt_error_summary if evidence and not is_active_candidate else None,
            "question_pack_version": str(
                (run_metadata_by_id or {}).get(evidence.valid_run_id or "", {}).get("question_pack_version")
                or metadata.get("question_pack_version")
                or "unknown"
            ) if evidence else str(metadata.get("question_pack_version") or "unknown"),
            "active_scan_order": active_entry["order"] if active_entry else None,
            "active_scan_status": active_entry["status"] if active_entry else None,
            "is_current_pack_comparable": (
                False
                if is_active_candidate
                else route_is_comparable
                and (
                    evidence.is_current_pack_comparable
                    if uses_candidate_evidence and evidence
                    else bool(bucket)
                )
            ),
            "is_using_previous_valid_result": (
                evidence.is_using_previous_valid_result
                if evidence and not is_active_candidate
                else False
            ),
            "is_current_run_eligible": is_current_run_eligible,
            "repairable_question_ids": repairable_question_ids,
            "repair_requires_full_scan": repair_requires_full_scan,
            "historical_score_text": historical_score_text,
            "historical_valid_at": historical_valid_at,
        }
        cards.append(card)
        ingress_groups[_ingress_group_key(model_view)].append(dict(card))

    run_count = len(current_run_history)
    hits_516_total = sum(1 for item in current_run_history if item.reasoning_tokens == 516)
    passes_total = sum(1 for item in current_run_history if item.answer_ok)
    reasoning_tokens_total = sum(
        item.reasoning_tokens for item in current_run_history if item.reasoning_tokens is not None
    )
    if uses_candidate_evidence:
        comparable_cards = [
            card for card in cards if bool(card["is_current_pack_comparable"])
        ]
        requested_cards = [
            card for card in cards if card["id"] in requested_candidate_ids
        ]
        if not repair_pack_matches:
            display_cards = [
                card for card in cards
                if card["id"] in configured_candidate_ids
            ]
            best_combination = None
        elif is_active_scan:
            display_cards = requested_cards or cards
            best_combination = None
        elif uses_strict_current_round:
            display_cards = (
                [
                    card for card in cards
                    if card["id"] in configured_candidate_ids
                ]
                if projects_current_selection
                else requested_cards or cards
            )
            recommendation_cards = [
                card
                for card in display_cards
                if bool(card["is_current_run_eligible"])
            ]
            recommendation_cards = [
                card
                for card in recommendation_cards
                if bool(card["reasoning_tokens_supported"])
            ]
            has_complete_selection_evidence = (
                bool(recommendation_cards)
                and (
                    not projects_current_selection
                    or len(recommendation_cards) == len(display_cards)
                )
            )
            best_combination = _best_combination(
                recommendation_cards,
                metadata,
                scan_interval_seconds,
                current_default_candidate_id,
            ) if has_complete_selection_evidence else None
        else:
            requested_only_cards = [
                card
                for card in cards
                if card["id"] in requested_candidate_ids
                and card not in comparable_cards
            ]
            display_cards = [*comparable_cards, *requested_only_cards]
            best_combination = _best_combination(
                [
                    card
                    for card in comparable_cards
                    if bool(card["reasoning_tokens_supported"])
                ],
                metadata,
                scan_interval_seconds,
                current_default_candidate_id,
            ) if comparable_cards else None
    else:
        display_cards = cards
        current_run_has_hard_error = any(
            item.error_message for item in current_run_history
        )
        best_combination = (
            _best_combination(
                [
                    card
                    for card in display_cards
                    if bool(card["reasoning_tokens_supported"])
                ],
                metadata,
                scan_interval_seconds,
                current_default_candidate_id,
            )
            if recommendation_status not in {"degraded", "failed"}
            and not current_run_has_hard_error
            else None
        )
    if evaluation_result_level == "provisional":
        best_combination = None
    leaderboard = _build_leaderboard(display_cards, best_combination)
    comparison_contract = _build_comparison_contract(
        metadata,
        current_run_id=current_run_id,
    )
    pairwise_comparisons = _build_pairwise_comparisons(leaderboard)
    provisional_leader = (
        _build_provisional_leader(
            leaderboard,
            metadata,
            required_candidate_ids=(
                configured_candidate_ids
                if projects_current_selection
                else None
            ),
        )
        if evaluation_result_level == "provisional"
        else None
    )
    return {
        "current_run_id": current_run_id,
        "run_count": run_count,
        "hits_516": hits_516_total,
        "hit_rate_516": _percent(hits_516_total, run_count),
        "pass_rate": _percent(passes_total, run_count),
        "reasoning_tokens_total": reasoning_tokens_total,
        "budget_summary": _build_scan_budget_summary(
            current_run_history,
            scan_budget or ScanBudgetConfig(),
            run_metadata=metadata,
        ),
        "truncation_trend": [1 if item.reasoning_tokens == 516 else 0 for item in history[-12:]],
        "cards": cards,
        "ingress_groups": _build_ingress_groups(ingress_groups),
        "best_combination": best_combination,
        "provisional_leader": provisional_leader,
        "leaderboard": leaderboard,
        "comparison_contract": comparison_contract,
        "pairwise_comparisons": pairwise_comparisons,
        "statistics": _build_statistics_summary(
            history=history,
            enabled_models=enabled_models,
            run_metadata=metadata,
            run_metadata_by_id=metadata_by_id,
        ),
        "run_metadata": metadata,
    }


def _build_scan_budget_summary(
    current_run_history: list[ScanResult],
    budget: ScanBudgetConfig,
    *,
    run_metadata: dict[str, object],
) -> dict[str, object]:
    elapsed_seconds = _run_wall_clock_seconds(current_run_history, run_metadata)
    completed_results = [
        item for item in current_run_history if _is_completed_model_call(item)
    ]
    estimated_costs, cost_coverage = _reference_cost_summary(completed_results)
    reference_cost_usd = round(sum(estimated_costs), 6) if estimated_costs else None

    duration_exceeded = elapsed_seconds > budget.max_duration_seconds
    cost_exceeded = (
        reference_cost_usd is not None
        and reference_cost_usd > budget.max_reference_cost_usd
    )
    if not budget.enabled:
        status = "disabled"
        status_text = "软预算未启用"
    elif duration_exceeded or cost_exceeded:
        status = "exceeded"
        status_text = "本轮已超软预算"
    elif cost_coverage != "complete":
        status = "partial"
        status_text = "预算内，部分费用未知"
    else:
        status = "within"
        status_text = "本轮在软预算内"

    cost_text = (
        f"参考费用 ${reference_cost_usd:.4f}"
        if reference_cost_usd is not None
        else "参考费用未知"
    )
    coverage_suffix = " · 部分费用未知" if cost_coverage == "partial" else ""
    detail_text = (
        f"耗时 {_duration_text(elapsed_seconds)} / {_duration_text(budget.max_duration_seconds)}"
        f" · {cost_text} / ${budget.max_reference_cost_usd:.2f}{coverage_suffix}"
    )
    return {
        "enabled": budget.enabled,
        "status": status,
        "status_text": status_text,
        "detail_text": detail_text,
        "elapsed_seconds": elapsed_seconds,
        "max_duration_seconds": budget.max_duration_seconds,
        "duration_exceeded": duration_exceeded,
        "reference_cost_usd": reference_cost_usd,
        "max_reference_cost_usd": budget.max_reference_cost_usd,
        "cost_exceeded": cost_exceeded,
        "cost_coverage": cost_coverage,
    }


def _run_wall_clock_seconds(
    current_run_history: list[ScanResult],
    run_metadata: dict[str, object],
) -> int:
    if run_metadata.get("aggregate_wall_clock_seconds") is not None:
        return int(run_metadata.get("aggregate_wall_clock_seconds") or 0)
    try:
        started_at = datetime.fromisoformat(str(run_metadata["started_at"]))
        completed_at = datetime.fromisoformat(str(run_metadata["completed_at"]))
        return round(max(0.0, (completed_at - started_at).total_seconds()))
    except (KeyError, TypeError, ValueError):
        pass

    intervals: list[tuple[datetime, datetime]] = []
    for item in current_run_history:
        try:
            started_at = datetime.fromisoformat(item.started_at)
        except (TypeError, ValueError):
            continue
        intervals.append(
            (started_at, started_at + timedelta(seconds=max(0.0, item.elapsed_seconds)))
        )
    if intervals:
        return round(
            max(0.0, (max(end for _, end in intervals) - min(start for start, _ in intervals)).total_seconds())
        )
    return round(sum(item.elapsed_seconds for item in current_run_history))


def _percent(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        return 0
    return round(numerator * 100 / denominator)


def _has_known_reference_cost(item: ScanResult) -> bool:
    return item.cost_status in _KNOWN_COST_STATUSES and item.reference_cost_usd is not None


def _is_completed_model_call(item: ScanResult) -> bool:
    terminal_state = str(item.execution_trace.get("terminal_state") or "").strip()
    if terminal_state:
        return terminal_state in {"completed_response", "completed_turn"}
    return item.error_message is None


def _reference_cost_value(item: ScanResult) -> float | None:
    if not _is_completed_model_call(item):
        return None
    if _has_known_reference_cost(item):
        return float(item.reference_cost_usd)
    estimate = estimate_reference_cost(
        item.model,
        input_tokens=item.input_tokens,
        cached_input_tokens=item.cached_input_tokens,
        cache_write_input_tokens=item.cache_write_input_tokens,
        output_tokens=item.output_tokens,
        reasoning_output_tokens=item.reasoning_tokens,
    )
    return estimate.usd


def _reference_cost_summary(items: Iterable[ScanResult]) -> tuple[list[float], str]:
    completed_items = [item for item in items if _is_completed_model_call(item)]
    estimated_costs = []
    for item in completed_items:
        cost = _reference_cost_value(item)
        if cost is not None:
            estimated_costs.append(cost)
    if completed_items and len(estimated_costs) == len(completed_items):
        return estimated_costs, "complete"
    if estimated_costs:
        return estimated_costs, "partial"
    return estimated_costs, "unknown"


def _score_text(correct: int, total: int) -> str:
    return f"{correct}/{total}"


def _planned_attempt_payload(
    active_run: dict[str, object] | None,
    *,
    by_candidate_key: str,
    by_label_key: str,
    fallback_payload: object | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {}
    if not active_run:
        return payload
    if isinstance(fallback_payload, dict):
        payload.update(fallback_payload)
    payload.update(dict(active_run.get(by_label_key, {})))
    payload.update(dict(active_run.get(by_candidate_key, {})))
    return payload


def _active_entry_payloads(
    active_run: dict[str, object] | None,
    models_by_id: dict[str, DashboardModelView],
    models_by_label: dict[str, DashboardModelView],
) -> dict[str, dict[str, object]]:
    payload: dict[str, dict[str, object]] = {}
    for index, item in enumerate((active_run or {}).get("entries", [])):
        if not isinstance(item, dict):
            continue
        candidate_id = str(item.get("candidate_id") or "").strip()
        if not candidate_id:
            label = str(item.get("label") or "").strip()
            model_view = models_by_label.get(label)
            candidate_id = model_view.id if model_view is not None else ""
        if not candidate_id or candidate_id not in models_by_id:
            continue
        payload[candidate_id] = {
            "order": index,
            "status": str(item.get("status") or "pending"),
        }
    return payload


def _fallback_question_count(planned_attempts: dict[str, object]) -> int:
    values = [int(value) for value in planned_attempts.values() if int(value) > 0]
    return max(values) if values else 0


def _run_metadata(
    *,
    current_run_id: str | None,
    active_run: dict[str, object] | None,
    run_metadata: dict[str, object] | None,
    fallback_question_count: int,
) -> dict[str, object]:
    raw = run_metadata
    if raw is None:
        active_metadata = (active_run or {}).get("run_metadata")
        raw = dict(active_metadata) if isinstance(active_metadata, dict) else None
    if raw is None:
        return RunMetadata.legacy(
            run_id=current_run_id,
            question_count=fallback_question_count,
        ).to_dict()
    merged = {
        "run_id": current_run_id or "unknown",
        "question_pack_id": "unknown",
        "question_pack_version": "unknown",
        "started_at": None,
        "completed_at": None,
        "candidate_count": 0,
        "question_count": fallback_question_count,
        "status": "legacy",
        **raw,
    }
    normalized = RunMetadata.from_dict(merged).to_dict()
    normalized["question_count"] = metadata_question_count(merged)
    question_ids = metadata_question_ids(raw)
    if question_ids:
        normalized["question_ids"] = question_ids
    return normalized


def _deduplicate_question_results(results: list[ScanResult]) -> list[ScanResult]:
    by_question: dict[str, ScanResult] = {}
    for item in results:
        by_question[item.question_id] = item
    return list(by_question.values())


def _order_question_results(
    results: list[ScanResult],
    planned_question_ids: Iterable[object],
) -> list[ScanResult]:
    question_order: dict[str, int] = {}
    for question_id in planned_question_ids:
        normalized = str(question_id)
        if normalized and normalized not in question_order:
            question_order[normalized] = len(question_order)
    if not question_order:
        return list(results)
    return sorted(
        results,
        key=lambda item: (
            question_order.get(item.question_id, len(question_order)),
            item.question_id,
        ),
    )


def _build_ingress_groups(
    ingress_groups: dict[str, list[dict[str, object]]]
) -> list[dict[str, object]]:
    groups: list[dict[str, object]] = []
    for group_key, model_candidates in sorted(ingress_groups.items()):
        recent_count = sum(int(item["recent_count"]) for item in model_candidates)
        hits_516 = sum(int(item["hits_516"]) for item in model_candidates)
        pass_total = sum(int(item["pass_count"]) for item in model_candidates)
        sample = model_candidates[0] if model_candidates else {}
        groups.append(
            {
                "group_id": group_key,
                "source_id": sample.get("source_id"),
                "source_title": sample.get("source_title"),
                "source_mode": sample.get("source_mode"),
                "recent_count": recent_count,
                "hits_516": hits_516,
                "hit_rate_516": _percent(hits_516, recent_count),
                "pass_rate": _percent(pass_total, recent_count),
                "reasoning_tokens_total": sum(
                    int(item["avg_reasoning_tokens"]) * int(item["recent_count"])
                    for item in model_candidates
                ),
                "model_candidates": model_candidates,
            }
        )
    return groups


def _build_leaderboard(
    cards: list[dict[str, object]],
    best_combination: dict[str, object] | None,
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for card in cards:
        total_count = int(card["recent_count"])
        correct_count = int(card["correct_count"])
        truncation_hits = int(card["hits_516"])
        entries.append(
            {
                "candidate_id": card["id"],
                "label": card["label"],
                "model": card["model"],
                "model_id": card["model_id"],
                "effort": card["effort"],
                "source_id": card["source_id"],
                "source_mode": card["source_mode"],
                "route_identity_status": card["route_identity_status"],
                "connection_id": card["connection_id"],
                "family_id": card["family_id"],
                "variant_id": card["variant_id"],
                "correct_count": correct_count,
                "total_count": total_count,
                "question_count": int(card["question_count"]),
                "question_completed": int(card["question_completed"]),
                "question_attempted": int(card["question_attempted"]),
                "semantic_score": int(card["semantic_score"]),
                "semantic_total": int(card["semantic_total"]),
                "evaluation_profile_id": card["evaluation_profile_id"],
                "evaluation_result_level": card["evaluation_result_level"],
                "mode_score": int(card["mode_score"]),
                "mode_score_max": int(card["mode_score_max"]),
                "mode_score_text": str(card["mode_score_text"]),
                "score_text": str(card["score_text"]),
                "scoring_mode": card["scoring_mode"],
                "overall_score": card["overall_score"],
                "overall_score_text": card["overall_score_text"],
                "score_facets": card["score_facets"],
                "question_results": card["question_results"],
                "avg_reasoning_tokens": int(card["avg_reasoning_tokens"]),
                "median_elapsed_seconds": card["median_elapsed_seconds"],
                "elapsed_seconds": card["elapsed_seconds"],
                "estimated_cost_usd": card["estimated_cost_usd"],
                "cost_coverage": card["cost_coverage"],
                "pass_rate": _percent(correct_count, total_count),
                "truncation_hits": truncation_hits,
                "latest_valid_run_id": card["latest_valid_run_id"],
                "latest_valid_at": card["latest_valid_at"],
                "valid_run_id": card["latest_valid_run_id"],
                "valid_completed_at": card["latest_valid_at"],
                "question_pack_version": card["question_pack_version"],
                "latest_attempt_at": card["latest_attempt_at"],
                "latest_attempt_status": card["latest_attempt_status"],
                "latest_attempt_error_category": card["latest_attempt_error_category"],
                "latest_attempt_error_summary": card["latest_attempt_error_summary"],
                "active_scan_order": card.get("active_scan_order"),
                "active_scan_status": card.get("active_scan_status"),
                "is_current_pack_comparable": card["is_current_pack_comparable"],
                "is_using_previous_valid_result": card["is_using_previous_valid_result"],
                "is_current_run_eligible": card["is_current_run_eligible"],
                "repairable_question_ids": card["repairable_question_ids"],
                "repair_requires_full_scan": card["repair_requires_full_scan"],
                "historical_score_text": card["historical_score_text"],
                "historical_valid_at": card["historical_valid_at"],
            }
        )

    def sort_key(item: dict[str, object]) -> tuple[object, ...]:
        active_scan_order = item.get("active_scan_order")
        has_evidence = int(item["question_completed"]) > 0
        score = item.get("overall_score")
        score_sort = -int(score) if score is not None else -int(item["semantic_score"])
        elapsed = (
            float(item["elapsed_seconds"])
            if item.get("elapsed_seconds") is not None
            else 10**9
        )
        cost = (
            float(item["estimated_cost_usd"])
            if item.get("estimated_cost_usd") is not None
            else 10**9
        )
        if active_scan_order is not None:
            active_status = str(item.get("active_scan_status") or "")
            priority = 0 if has_evidence else 1 if active_status == "running" else 2
            return (
                priority,
                0 if score is not None else 1,
                score_sort if has_evidence else 0,
                int(item["truncation_hits"]) if has_evidence else 0,
                elapsed if has_evidence else 0,
                cost if has_evidence else 0,
                int(item["avg_reasoning_tokens"]) if has_evidence else 0,
                f"{int(active_scan_order):08d}",
            )
        return (
            0 if score is not None else 1,
            score_sort,
            0 if bool(item["is_current_run_eligible"]) else 1,
            int(item["truncation_hits"]),
            elapsed,
            cost,
            int(item["avg_reasoning_tokens"]),
            str(item["candidate_id"]),
        )

    entries.sort(key=sort_key)
    best_candidate_id = (best_combination or {}).get("candidate_id")
    for entry in entries:
        entry["is_best"] = entry["candidate_id"] == best_candidate_id
    _assign_leaderboard_decision_tags(entries, str(best_candidate_id or ""))
    _assign_canonical_leaderboard_projection(entries)
    return entries


def _build_comparison_contract(
    run_metadata: dict[str, object],
    *,
    current_run_id: str | None,
) -> dict[str, object]:
    question_pack_version = str(
        run_metadata.get("question_pack_version") or "unknown"
    )
    grader_version = _comparison_grader_version(
        run_metadata.get("scoring_mode")
    )
    evaluation_profile_id = str(
        run_metadata.get("evaluation_profile_id") or "legacy_full"
    )
    question_ids = [
        str(question_id)
        for question_id in run_metadata.get("question_ids", [])
        if str(question_id)
    ]
    question_identity = (
        ",".join(question_ids)
        if question_ids
        else f"count:{int(run_metadata.get('question_count') or 0)}"
    )
    run_id = str(
        run_metadata.get("run_id")
        or current_run_id
        or "unknown"
    )
    return {
        "schema_version": 1,
        "question_pack_version": question_pack_version,
        "grader_version": grader_version,
        "evaluation_snapshot_id": f"local:{run_id}",
        "pricing_snapshot_id": current_pricing_snapshot_id(),
        "trend_comparability_key": (
            f"v1|pack:{question_pack_version}|grader:{grader_version}|"
            f"profile:{evaluation_profile_id}|questions:{question_identity}"
        ),
    }


def _comparison_grader_version(value: object) -> str:
    scoring_mode = str(value or "").strip()
    if scoring_mode in {"", "unknown", "legacy"}:
        return "unknown"
    return f"scoring-mode:{scoring_mode}"


def _assign_canonical_leaderboard_projection(
    entries: list[dict[str, object]],
) -> None:
    ranked_groups: dict[tuple[str, int], list[dict[str, object]]] = defaultdict(list)
    for entry in entries:
        entry["canonical_rank"] = None
        entry["canonical_rank_label"] = "暂不排名"
        entry["canonical_rank_status"] = "unranked"
        entry["canonical_rank_semantics"] = "competition"
        entry["canonical_rank_score_basis"] = None
        entry["is_canonical_rank_tied"] = False
        entry["canonical_rank_tie_count"] = 0
        basis_and_score = _canonical_rank_score(entry)
        if basis_and_score is not None:
            ranked_groups[basis_and_score].append(entry)

    next_rank = 1
    for (score_basis, _), tied_entries in sorted(
        ranked_groups.items(),
        key=lambda item: (
            0 if item[0][0] == "overall_score" else 1,
            -item[0][1],
        ),
    ):
        tie_count = len(tied_entries)
        is_tied = tie_count > 1
        rank_label = (
            f"并列第 {next_rank} 名"
            if is_tied
            else f"第 {next_rank} 名"
        )
        for entry in tied_entries:
            entry["canonical_rank"] = next_rank
            entry["canonical_rank_label"] = rank_label
            entry["canonical_rank_status"] = "tied" if is_tied else "ranked"
            entry["canonical_rank_score_basis"] = score_basis
            entry["is_canonical_rank_tied"] = is_tied
            entry["canonical_rank_tie_count"] = tie_count
        next_rank += tie_count

    for entry in entries:
        labels = [str(entry["canonical_rank_label"])]
        labels.extend(
            str(tag.get("label") or "")
            for tag in entry.get("decision_tags", [])
            if isinstance(tag, dict) and str(tag.get("label") or "")
        )
        if bool(entry.get("is_using_previous_valid_result")):
            labels.append("沿用上次有效结果")
        entry["canonical_labels"] = list(dict.fromkeys(labels))


def _canonical_rank_score(
    entry: dict[str, object],
) -> tuple[str, int] | None:
    if not bool(entry.get("is_current_pack_comparable")):
        return None
    overall_score = entry.get("overall_score")
    if overall_score is not None:
        return "overall_score", int(overall_score)
    if (
        str(entry.get("evaluation_result_level") or "") == "provisional"
        and int(entry.get("question_completed") or 0) > 0
    ):
        return "mode_score", int(entry.get("mode_score") or 0)
    return None


def _build_pairwise_comparisons(
    leaderboard: list[dict[str, object]],
) -> list[dict[str, object]]:
    comparisons: list[dict[str, object]] = []
    for baseline_entry in leaderboard:
        baseline_id = str(baseline_entry.get("candidate_id") or "")
        if not baseline_id:
            continue
        for candidate in leaderboard:
            candidate_id = str(candidate.get("candidate_id") or "")
            if not candidate_id or candidate_id == baseline_id:
                continue
            comparisons.append(
                _build_pairwise_comparison(
                    baseline_entry,
                    candidate,
                    baseline_id=baseline_id,
                )
            )
    return comparisons


def _build_pairwise_comparison(
    baseline: dict[str, object],
    candidate: dict[str, object],
    *,
    baseline_id: str,
) -> dict[str, object]:
    candidate_id = str(candidate.get("candidate_id") or "")
    comparison_status = _pairwise_comparison_status(baseline, candidate)
    is_comparable = comparison_status == "comparable"
    baseline_score = _optional_float(baseline.get("overall_score"))
    candidate_score = _optional_float(candidate.get("overall_score"))
    baseline_elapsed = _optional_float(baseline.get("elapsed_seconds"))
    candidate_elapsed = _optional_float(candidate.get("elapsed_seconds"))
    baseline_cost = _optional_float(baseline.get("estimated_cost_usd"))
    candidate_cost = _optional_float(candidate.get("estimated_cost_usd"))
    return {
        "schema_version": 1,
        "pair_key": f"{baseline_id}__to__{candidate_id}",
        "baseline_candidate_id": baseline_id,
        "baseline_label": str(baseline.get("label") or ""),
        "candidate_id": candidate_id,
        "candidate_label": str(candidate.get("label") or ""),
        "comparison_status": comparison_status,
        "is_comparable": is_comparable,
        "baseline_quality_score": baseline_score,
        "candidate_quality_score": candidate_score,
        "quality_delta_points": (
            _rounded_metric(candidate_score - baseline_score)
            if is_comparable
            and baseline_score is not None
            and candidate_score is not None
            else None
        ),
        "baseline_elapsed_seconds": baseline_elapsed,
        "candidate_elapsed_seconds": candidate_elapsed,
        "time_delta_percent": (
            _percentage_improvement(baseline_elapsed, candidate_elapsed)
            if is_comparable
            else None
        ),
        "baseline_cost_usd": baseline_cost,
        "candidate_cost_usd": candidate_cost,
        "cost_delta_percent": (
            _percentage_improvement(baseline_cost, candidate_cost)
            if is_comparable
            and baseline.get("cost_coverage") == "complete"
            and candidate.get("cost_coverage") == "complete"
            else None
        ),
        "baseline_cost_coverage": baseline.get("cost_coverage"),
        "candidate_cost_coverage": candidate.get("cost_coverage"),
        "baseline_token_totals": _question_token_totals(
            baseline.get("question_results")
        ),
        "candidate_token_totals": _question_token_totals(
            candidate.get("question_results")
        ),
        "warning_question_ids": (
            _warning_question_ids(baseline, candidate)
            if is_comparable
            else []
        ),
    }


def _pairwise_comparison_status(
    baseline: dict[str, object],
    candidate: dict[str, object],
) -> str:
    if not bool(baseline.get("is_current_pack_comparable")):
        return "baseline_not_comparable"
    if not bool(candidate.get("is_current_pack_comparable")):
        return "candidate_not_comparable"
    if (
        baseline.get("question_pack_version")
        != candidate.get("question_pack_version")
        or baseline.get("scoring_mode") != candidate.get("scoring_mode")
    ):
        return "contract_mismatch"
    if (
        baseline.get("overall_score") is None
        or candidate.get("overall_score") is None
    ):
        return "insufficient_evidence"
    return "comparable"


def _question_token_totals(value: object) -> dict[str, int | None]:
    question_results = value if isinstance(value, list) else []
    token_keys = (
        "input_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "reasoning_tokens",
    )
    return {
        key: _sum_known_values(
            result.get(key)
            for result in question_results
            if isinstance(result, dict)
        )
        for key in token_keys
    }


def _sum_known_values(values: Iterable[object]) -> int | None:
    known = [int(value) for value in values if value is not None]
    return sum(known) if known else None


def _percentage_improvement(
    baseline: float | None,
    candidate: float | None,
) -> float | int | None:
    if baseline is None or candidate is None or baseline <= 0:
        return None
    return _rounded_metric((baseline - candidate) * 100 / baseline)


def _rounded_metric(value: float) -> float | int:
    rounded = round(value, 3)
    if rounded.is_integer():
        return int(rounded)
    return rounded


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _warning_question_ids(
    baseline: dict[str, object],
    candidate: dict[str, object],
) -> list[str]:
    baseline_scores = {
        str(result.get("question_id") or "").casefold(): _question_score_percent(
            result
        )
        for result in baseline.get("question_results", [])
        if isinstance(result, dict) and str(result.get("question_id") or "")
    }
    warnings: list[str] = []
    for result in candidate.get("question_results", []):
        if not isinstance(result, dict):
            continue
        question_id = str(result.get("question_id") or "")
        candidate_score = _question_score_percent(result)
        baseline_score = baseline_scores.get(question_id.casefold())
        if (
            question_id
            and candidate_score is not None
            and baseline_score is not None
            and candidate_score - baseline_score < -5
        ):
            warnings.append(question_id)
    return warnings


def _question_score_percent(result: dict[str, object]) -> float | None:
    score = result.get("semantic_score")
    total = result.get("semantic_total")
    if score is None or total is None or float(total) <= 0:
        return None
    return float(score) * 100 / float(total)


def _build_provisional_leader(
    leaderboard: list[dict[str, object]],
    run_metadata: dict[str, object],
    *,
    required_candidate_ids: set[str] | None = None,
) -> dict[str, object] | None:
    requested_candidate_ids = required_candidate_ids or {
        str(candidate_id)
        for candidate_id in run_metadata.get("requested_candidate_ids", [])
    }
    requested_entries = [
        entry
        for entry in leaderboard
        if not requested_candidate_ids
        or str(entry.get("candidate_id")) in requested_candidate_ids
    ]
    eligible = [
        entry
        for entry in requested_entries
        if bool(entry.get("is_current_run_eligible"))
        and int(entry.get("question_completed") or 0)
        >= int(run_metadata.get("question_count") or 0)
    ]
    metadata_status = str(run_metadata.get("status") or "legacy")
    if metadata_status in {"failed", "degraded", "partial"}:
        return {
            "status": "execution_error",
            "label": "执行异常",
            "status_label": "执行异常",
            "candidate_id": None,
            "confidence_label": "低",
            "reason": "本轮存在缺失或执行错误，不能形成可靠初步排序。",
            "confidence_reason": "本轮存在缺失或执行错误，不能形成可靠初步排序。",
        }
    if not eligible or len(eligible) < len(requested_entries):
        return {
            "status": "insufficient",
            "label": "证据不足",
            "status_label": "证据不足",
            "candidate_id": None,
            "confidence_label": "低",
            "reason": "仍有候选未完成当前评测档案。",
            "confidence_reason": "仍有候选未完成当前评测档案。",
        }
    ordered = sorted(
        eligible,
        key=lambda entry: (
            -int(entry.get("mode_score") or 0),
            float(entry.get("elapsed_seconds") or 10**9),
            str(entry.get("candidate_id") or ""),
        ),
    )
    best = ordered[0]
    runner_up = ordered[1] if len(ordered) > 1 else None
    best_score = int(best.get("mode_score") or 0)
    runner_up_score = (
        int(runner_up.get("mode_score") or 0)
        if runner_up is not None
        else None
    )
    mode_score_max = max(1, int(best.get("mode_score_max") or 0))
    if runner_up_score is not None and best_score == runner_up_score:
        return {
            "status": "tied",
            "label": "同档候选",
            "status_label": "同档候选",
            "candidate_id": str(best.get("candidate_id") or ""),
            "confidence_label": "低",
            "reason": "榜首同分，建议补全评测后再决定。",
            "confidence_reason": "榜首同分，建议补全评测后再决定。",
            "mode_score": best_score,
            "mode_score_max": mode_score_max,
            "mode_score_text": str(best.get("mode_score_text") or ""),
        }
    gap = best_score - (runner_up_score if runner_up_score is not None else best_score)
    confidence_label = (
        "中"
        if runner_up_score is not None and gap >= mode_score_max * 0.10
        else "低"
    )
    confidence_reason = (
        "当前档案内领先，但仍需完整评测确认。"
        if confidence_label == "中"
        else "领先差距较小，建议补全评测后再决定。"
    )
    return {
        "status": "leading",
        "label": "极速领先",
        "status_label": "极速领先",
        "candidate_id": str(best.get("candidate_id") or ""),
        "display_label": str(best.get("label") or ""),
        "confidence_label": confidence_label,
        "reason": confidence_reason,
        "confidence_reason": confidence_reason,
        "mode_score": best_score,
        "mode_score_max": mode_score_max,
        "mode_score_text": str(best.get("mode_score_text") or ""),
        "runner_up_gap": gap if runner_up_score is not None else None,
    }


def _build_statistics_summary(
    *,
    history: list[ScanResult],
    enabled_models: list[DashboardModelView],
    run_metadata: dict[str, object],
    run_metadata_by_id: dict[str, dict[str, object]],
) -> dict[str, object]:
    required_runs = 8
    recent_run_ids = _recent_run_ids(
        history,
        run_metadata_by_id=run_metadata_by_id,
    )
    recent_run_ids = _profile_compatible_run_ids(
        recent_run_ids,
        current_metadata=run_metadata,
        run_metadata_by_id=run_metadata_by_id,
    )
    trend_run_ids = _trend_run_ids(
        history=history,
        enabled_models=enabled_models,
        run_ids=recent_run_ids,
        run_metadata_by_id=run_metadata_by_id,
        required_runs=required_runs,
    )
    return {
        "trend_series": _statistics_trend_series(
            history=history,
            enabled_models=enabled_models,
            run_ids=trend_run_ids,
            run_metadata_by_id=run_metadata_by_id,
        ) if trend_run_ids else [],
    }


def _recent_run_ids(
    history: list[ScanResult],
    *,
    run_metadata_by_id: dict[str, dict[str, object]] | None = None,
) -> list[str]:
    first_index_by_run: dict[str, int] = {}
    last_index_by_run: dict[str, int] = {}
    for index, item in enumerate(history):
        first_index_by_run.setdefault(item.run_id, index)
        last_index_by_run[item.run_id] = index
    metadata_by_id = run_metadata_by_id or {}
    ordered = sorted(
        first_index_by_run,
        key=lambda run_id: (
            last_index_by_run[run_id]
            if str((metadata_by_id.get(run_id) or {}).get("comparison_group_mode") or "") == "custom_append"
            else first_index_by_run[run_id],
            first_index_by_run[run_id],
        ),
    )
    return ordered


def _profile_compatible_run_ids(
    run_ids: list[str],
    *,
    current_metadata: dict[str, object],
    run_metadata_by_id: dict[str, dict[str, object]],
) -> list[str]:
    current_pack_version = str(
        current_metadata.get("question_pack_version") or "unknown"
    )
    current_profile_id = str(
        current_metadata.get("evaluation_profile_id") or "legacy_full"
    )
    return [
        run_id
        for run_id in run_ids
        if str(
            (run_metadata_by_id.get(run_id) or {}).get("question_pack_version")
            or "unknown"
        ) == current_pack_version
        and str(
            (run_metadata_by_id.get(run_id) or {}).get("evaluation_profile_id")
            or "legacy_full"
        ) == current_profile_id
    ]


def _trend_run_ids(
    *,
    history: list[ScanResult],
    enabled_models: list[DashboardModelView],
    run_ids: list[str],
    run_metadata_by_id: dict[str, dict[str, object]],
    required_runs: int,
) -> list[str]:
    observed_run_ids = [
        run_id
        for run_id in run_ids
        if _run_has_successful_observation(
            history=history,
            enabled_models=enabled_models,
            run_id=run_id,
            run_metadata_by_id=run_metadata_by_id,
        )
    ]
    return observed_run_ids[-required_runs:]


def _run_has_successful_observation(
    *,
    history: list[ScanResult],
    enabled_models: list[DashboardModelView],
    run_id: str,
    run_metadata_by_id: dict[str, dict[str, object]],
) -> bool:
    return any(
        _has_successful_scan_observation(
            history=history,
            model_view=model_view,
            run_id=run_id,
            run_metadata_by_id=run_metadata_by_id,
        )
        for model_view in enabled_models
    )


def _metadata_question_count(metadata: dict[str, object] | None, *, fallback: int) -> int:
    if not metadata:
        return fallback
    try:
        value = int(metadata.get("question_count") or fallback)
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


def _median(values: list[float]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return int(ordered[midpoint] + 0.5)
    return int(((ordered[midpoint - 1] + ordered[midpoint]) / 2) + 0.5)


def _scan_elapsed_total(results: list[ScanResult]) -> float | None:
    scan_results = _deduplicate_question_results(
        [item for item in results if normalize_phase(item.phase) == SCAN_PHASE]
    )
    if not scan_results:
        return None
    return round(sum(float(item.elapsed_seconds) for item in scan_results), 3)


def _statistics_trend_series(
    *,
    history: list[ScanResult],
    enabled_models: list[DashboardModelView],
    run_ids: list[str],
    run_metadata_by_id: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    series: list[dict[str, object]] = []
    for model_view in enabled_models:
        run_indices, score_values = _overall_score_observations(
            history=history,
            model_view=model_view,
            run_ids=run_ids,
            run_metadata_by_id=run_metadata_by_id,
        )
        if not score_values:
            continue
        series.append(
            {
                "candidate_id": model_view.id,
                "overall_score_run_indices": run_indices,
                "overall_score_values": score_values,
            }
        )
    return series


def _scan_results_for_run(
    *,
    history: list[ScanResult],
    model_view: DashboardModelView,
    run_id: str,
) -> list[ScanResult]:
    models_by_id = {model_view.id: model_view}
    models_by_label = {model_view.label: model_view}
    bucket = [
        item
        for item in history
        if item.run_id == run_id
        and _match_model_view(item, models_by_id, models_by_label) is not None
        and normalize_phase(item.phase) == SCAN_PHASE
    ]
    return _deduplicate_question_results(bucket)


def _has_successful_scan_observation(
    *,
    history: list[ScanResult],
    model_view: DashboardModelView,
    run_id: str,
    run_metadata_by_id: dict[str, dict[str, object]],
) -> bool:
    scan_results = _scan_results_for_run(
        history=history,
        model_view=model_view,
        run_id=run_id,
    )
    if not scan_results:
        return False
    metadata = run_metadata_by_id.get(run_id)
    if not _requires_complete_scan_observation(metadata):
        return all(not item.error_message for item in scan_results)
    expected_questions = _metadata_question_count(
        metadata,
        fallback=len(scan_results),
    )
    return (
        expected_questions > 0
        and len(scan_results) >= expected_questions
        and all(not item.error_message for item in scan_results)
    )


def _requires_complete_scan_observation(
    metadata: dict[str, object] | None,
) -> bool:
    if not metadata:
        return False
    return any(
        key in metadata
        for key in (
            "selection_mode",
            "requested_candidate_ids",
            "regular_candidate_ids",
            "is_complete_regular_round",
        )
    )


def _overall_score_observations(
    *,
    history: list[ScanResult],
    model_view: DashboardModelView,
    run_ids: list[str],
    run_metadata_by_id: dict[str, dict[str, object]],
) -> tuple[list[int], list[int]]:
    observations = _scored_run_observations(
        history=history,
        model_view=model_view,
        run_ids=run_ids,
        run_metadata_by_id=run_metadata_by_id,
    )
    return (
        [run_index for run_index, _, _ in observations],
        [overall_score for _, overall_score, _ in observations],
    )


def _scored_run_observations(
    *,
    history: list[ScanResult],
    model_view: DashboardModelView,
    run_ids: list[str],
    run_metadata_by_id: dict[str, dict[str, object]],
) -> list[tuple[int, int, list[ScanResult]]]:
    models_by_id = {model_view.id: model_view}
    models_by_label = {model_view.label: model_view}
    observations: list[tuple[int, int, list[ScanResult]]] = []
    for run_index, run_id in enumerate(run_ids):
        metadata = run_metadata_by_id.get(run_id, {})
        if str(metadata.get("status") or "") not in {"completed", "degraded"}:
            continue
        if str(metadata.get("evaluation_result_level") or "") == "provisional":
            continue
        bucket = [
            item
            for item in history
            if item.run_id == run_id
            and _match_model_view(item, models_by_id, models_by_label) is not None
            and normalize_phase(item.phase) == SCAN_PHASE
        ]
        scan_results = _deduplicate_question_results(bucket)
        expected_questions = _metadata_question_count(
            metadata,
            fallback=len(scan_results),
        )
        if (
            expected_questions <= 0
            or len(scan_results) < expected_questions
            or any(item.error_message for item in scan_results)
        ):
            continue
        semantic_score, semantic_total = _semantic_score(scan_results)
        if semantic_total <= 0:
            continue
        observations.append(
            (
                run_index,
                int(round(semantic_score * 100 / semantic_total)),
                scan_results,
            )
        )
    return observations


def _question_result_payload(results: list[ScanResult]) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    for item in results:
        diagnostics = dict(item.scorer_diagnostics or {})
        score_details = []
        for detail in diagnostics.get("score_details", []):
            if not isinstance(detail, dict):
                continue
            score_details.append(
                {
                    "id": str(detail.get("id") or detail.get("case_id") or "unknown"),
                    "label": str(detail.get("label") or detail.get("id") or "得分点"),
                    "points": int(detail.get("points") or 0),
                    "max_points": int(detail.get("max_points") or 1),
                    "passed": bool(detail.get("passed")),
                }
            )
        result_payload: dict[str, object] = {
            "question_id": item.question_id,
            "question_title": item.question_title or item.question_id,
            "capability_id": item.capability_id or item.question_id,
            "capability_label": normalized_capability_label(
                item.question_id,
                item.capability_label or item.question_title,
            ),
            "detail_label": normalized_detail_label(
                item.question_id,
                item.detail_label or item.question_title,
            ),
            "phase": normalize_phase(item.phase),
            "status": _question_status(item),
            "expected_summary": item.expected_summary or "",
            "actual_summary": item.actual_summary or item.answer_preview,
            "answer_preview": item.answer_preview,
            "scorer_reason": item.scorer_reason or item.error_message or ("matched" if item.answer_ok else "not_matched"),
            "latency_s": round(float(item.elapsed_seconds), 3),
            "input_tokens": item.input_tokens,
            "cached_input_tokens": item.cached_input_tokens,
            "cache_write_input_tokens": item.cache_write_input_tokens,
            "output_tokens": item.output_tokens,
            "reasoning_tokens": item.reasoning_tokens,
        }
        if "semantic_passed" in diagnostics and "semantic_total" in diagnostics:
            result_payload["semantic_score"] = int(diagnostics["semantic_passed"])
            result_payload["semantic_total"] = int(diagnostics["semantic_total"])
        if score_details:
            result_payload["score_details"] = score_details
        failure_summary = str(diagnostics.get("failure_summary") or "")
        if failure_summary:
            result_payload["failure_summary"] = failure_summary
        payload.append(result_payload)
    return payload


def _semantic_item_score(item: ScanResult) -> tuple[int, int]:
    diagnostics = dict(item.scorer_diagnostics or {})
    if diagnostics.get("status") == "grader_unavailable":
        return 0, 0
    if "semantic_passed" in diagnostics and "semantic_total" in diagnostics:
        return int(diagnostics["semantic_passed"]), int(diagnostics["semantic_total"])
    return (1 if item.answer_ok else 0), 1


def _semantic_score(results: list[ScanResult]) -> tuple[int, int]:
    score = 0
    total = 0
    for item in results:
        item_score, item_total = _semantic_item_score(item)
        score += item_score
        total += item_total
    return score, total


def _has_unscored_execution_error(results: list[ScanResult]) -> bool:
    for item in results:
        if not item.error_message:
            continue
        diagnostics = item.scorer_diagnostics or {}
        if "semantic_passed" not in diagnostics or "semantic_total" not in diagnostics:
            return True
    return False


def _question_status(item: ScanResult) -> str:
    error_message = (item.error_message or "").lower()
    if "timed out" in error_message or "timeout" in error_message:
        return "timeout"
    if item.final_status == "truncated":
        return "truncated"
    if item.error_message or item.final_status in {"error", "interrupted"}:
        return "error"
    return "pass" if item.answer_ok else "fail"


def _score_facets(results: list[ScanResult]) -> list[dict[str, object]]:
    combined: dict[str, dict[str, object]] = {}
    for item in results:
        diagnostics = dict(item.scorer_diagnostics or {})
        if item.grader_kind == "mutation_test_design":
            facets = mutation_test_design_facets(diagnostics)
        elif item.grader_kind == "cache_propagation_certificate":
            facets = cache_propagation_certificate_facets(diagnostics)
        else:
            continue
        for facet in facets:
            facet_id = str(facet["id"])
            aggregate = combined.setdefault(
                facet_id,
                {
                    "id": facet_id,
                    "label": str(facet["label"]),
                    "passed": 0,
                    "total": 0,
                },
            )
            aggregate["passed"] = int(aggregate["passed"]) + int(facet["passed"])
            aggregate["total"] = int(aggregate["total"]) + int(facet["total"])
    return list(combined.values())


def _best_combination(
    cards: list[dict[str, object]],
    run_metadata: dict[str, object],
    scan_interval_seconds: int | None,
    current_default_candidate_id: str | None,
) -> dict[str, object] | None:
    candidates = [card for card in cards if int(card["recent_count"]) > 0]
    if not candidates:
        return None
    selection = _recommendation_selection(
        candidates,
        current_default_candidate_id=current_default_candidate_id,
    )
    best = selection["best"]
    runner_up = selection["runner_up"]
    confidence_label, confidence_reason, confidence_reasons = _confidence(
        best=best,
        runner_up=runner_up,
        candidate_count=len(candidates),
        overall_gap=selection["overall_gap"],
        run_metadata=run_metadata,
        scan_interval_seconds=scan_interval_seconds,
    )
    gap_text = _runner_up_gap_text(
        best,
        runner_up,
        str(selection["recommendation_basis"]),
        run_metadata,
    )
    question_pack_display_text, question_pack_context_text = _question_pack_texts(run_metadata)
    stability_text = str(best["score_text"])
    latest_attempt_status = str(best.get("latest_attempt_status") or "").lower()
    latest_attempt_has_error = bool(best.get("latest_attempt_error_summary"))
    retained_after_failure = bool(best.get("is_using_previous_valid_result")) and (
        latest_attempt_status in {"failed", "error", "invalid", "degraded"}
        or (latest_attempt_has_error and latest_attempt_status != "completed")
    )
    is_comparable = (
        confidence_label == "高"
        and bool(best.get("is_current_pack_comparable"))
    )
    decision = resolve_recommendation_decision(
        current_default_candidate_id=current_default_candidate_id,
        recommended_candidate_id=str(best["id"]),
        is_comparable=(
            is_comparable
            or (
                retained_after_failure
                and bool(best.get("is_current_pack_comparable"))
            )
        ),
        retained_after_failure=retained_after_failure,
        latest_error_category=str(best.get("latest_attempt_error_category") or "")
        or None,
    )
    freshness = _recommendation_freshness(
        best,
        run_metadata,
        scan_interval_seconds,
    )
    return {
        "label": best["label"],
        "display_label": best["label"],
        "copy_value": best["label"],
        "candidate_id": best["id"],
        "model": best["model"],
        "effort": best["effort"],
        "effort_label": _effort_label(str(best["effort"])),
        "short_display_name": _short_display_name(best),
        **freshness,
        "stability_text": stability_text,
        "score_text": str(best["score_text"]),
        "semantic_score": int(best["semantic_score"]),
        "semantic_total": int(best["semantic_total"]),
        "scoring_mode": "semantic_q1_q5_equal_v2",
        "overall_score": best.get("overall_score"),
        "overall_score_text": best.get("overall_score_text"),
        "avg_reasoning_tokens": best["avg_reasoning_tokens"],
        "truncation_hits": best["hits_516"],
        "pass_rate": best["pass_rate"],
        "hit_rate_516": best["hit_rate_516"],
        "recommendation_basis": selection["recommendation_basis"],
        "confidence_label": confidence_label,
        "confidence_reason": confidence_reason,
        "confidence_reasons": confidence_reasons,
        "current_default_candidate_id": current_default_candidate_id,
        "recommendation_outcome": decision.recommendation_outcome,
        "evidence_state": decision.evidence_state,
        "decision_state": decision.decision_state,
        "decision_title": decision.title,
        "decision_action_label": decision.action_label,
        "decision_reason": decision.reason,
        "runner_up_gap_text": gap_text,
        "overall_gap": selection["overall_gap"],
        "question_pack_version": run_metadata.get("question_pack_version", "unknown"),
        "question_pack_display_text": question_pack_display_text,
        "question_pack_context_text": question_pack_context_text,
    }


def _recommendation_freshness(
    best: dict[str, object],
    run_metadata: dict[str, object],
    scan_interval_seconds: int | None,
) -> dict[str, str | None]:
    recommendation_created_at = (
        run_metadata.get("recommendation_created_at")
        or run_metadata.get("completed_at")
        or best.get("latest_valid_at")
    )
    run_completed_at = run_metadata.get("completed_at")
    if not recommendation_created_at:
        return {
            "recommendation_created_at": None,
            "run_completed_at": str(run_completed_at) if run_completed_at else None,
            "stale_at": None,
            "expires_at": None,
        }
    try:
        created_at = datetime.fromisoformat(str(recommendation_created_at))
    except ValueError:
        return {
            "recommendation_created_at": None,
            "run_completed_at": str(run_completed_at) if run_completed_at else None,
            "stale_at": None,
            "expires_at": None,
        }
    interval_seconds = max(0, int(scan_interval_seconds or 0))
    stale_seconds = max(24 * 60 * 60, interval_seconds * 2)
    expiry_seconds = max(72 * 60 * 60, interval_seconds * 6)
    return {
        "recommendation_created_at": created_at.isoformat(),
        "run_completed_at": str(run_completed_at) if run_completed_at else None,
        "stale_at": (created_at + timedelta(seconds=stale_seconds)).isoformat(),
        "expires_at": (created_at + timedelta(seconds=expiry_seconds)).isoformat(),
    }


def _short_display_name(candidate: dict[str, object]) -> str:
    model = str(candidate.get("model") or "")
    source_id = str(candidate.get("source_id") or "")
    if source_id == "codex_local" and model.lower().startswith("gpt-"):
        components = model.removeprefix("gpt-").split("-")
        version = components[0]
        variant = " ".join(part.capitalize() for part in components[1:])
        return f"{version} {variant}".strip()
    return str(candidate.get("display_name") or candidate.get("label") or model)

def _recommendation_selection(
    candidates: list[dict[str, object]],
    *,
    current_default_candidate_id: str | None,
) -> dict[str, object]:
    scored_candidates = [
        item for item in candidates if item.get("overall_score") is not None
    ]
    complete = bool(candidates) and len(scored_candidates) == len(candidates)
    ranked = sorted(
        scored_candidates or candidates,
        key=lambda item: (
            1 if item.get("overall_score") is not None else 0,
            int(item.get("overall_score") or 0),
            int(item.get("semantic_score") or 0),
            -int(item.get("hits_516") or 0),
            -float(item.get("elapsed_seconds") or 10**9),
            -float(item.get("estimated_cost_usd") or 10**9),
            -int(item.get("avg_reasoning_tokens") or 0),
            1
            if current_default_candidate_id
            and str(item["id"]) == current_default_candidate_id
            else 0,
        ),
        reverse=True,
    )
    best = ranked[0]
    runner_up = ranked[1] if len(ranked) > 1 else None
    overall_gap = (
        int(best.get("overall_score") or 0)
        - int(runner_up.get("overall_score") or 0)
        if complete and runner_up is not None
        else None
    )
    return {
        "best": best,
        "runner_up": runner_up,
        "recommendation_basis": (
            "overall_score_pending"
            if not complete
            else "overall_score_lead"
            if overall_gap is None or overall_gap > 0
            else "overall_score_tie"
        ),
        "overall_gap": overall_gap,
    }


def _confidence(
    *,
    best: dict[str, object],
    runner_up: dict[str, object] | None,
    candidate_count: int,
    overall_gap: int | None,
    run_metadata: dict[str, object],
    scan_interval_seconds: int | None,
) -> tuple[str, str, list[str]]:
    expected_question_count = int(run_metadata.get("question_count") or 0)
    if expected_question_count <= 0:
        expected_question_count = int(best.get("question_attempted") or 0)
    anomaly_reason = _anomaly_reason(
        best,
        run_metadata,
        expected_question_count,
        scan_interval_seconds,
    )
    if anomaly_reason:
        return "低", anomaly_reason, _health_reason_codes(
            run_metadata,
            best,
            scan_interval_seconds,
        )
    if candidate_count <= 1:
        return "低", "当前只有一个有效候选模型", ["single_candidate"]
    if best.get("overall_score") is None:
        return "低", "本轮 Q1～Q5 尚未全部完成", ["overall_score_incomplete"]

    gap = int(overall_gap or 0)
    if gap >= 5:
        return "高", f"总分领先第二名 {gap} 分", ["overall_score_leads_by_5_plus"]
    if gap >= 3:
        return "中", f"总分领先第二名 {gap} 分", ["overall_score_leads_by_3_plus"]
    if gap > 0:
        return "低", f"总分仅领先第二名 {gap} 分，仍属同档", ["overall_score_same_tier"]
    return "低", "总分同分，仍属同档", ["overall_score_tie"]


def _anomaly_reason(
    best: dict[str, object],
    run_metadata: dict[str, object],
    expected_question_count: int,
    scan_interval_seconds: int | None,
) -> str | None:
    status = str(run_metadata.get("status") or "legacy")
    if status in {"running", "partial"}:
        return "当前 run 未完成"
    question_pack_version = str(run_metadata.get("question_pack_version") or "unknown")
    if not question_pack_version or question_pack_version == "unknown":
        if status == "legacy":
            return "当前结果来自旧数据，题包版本未记录"
        return "当前 run 缺少题包版本，结果可参考但不完整"
    if int(best.get("question_completed") or 0) < expected_question_count:
        return "当前推荐模型未完成全部题目"
    if int(best.get("hits_516") or 0) > 0:
        return "当前推荐模型出现截断风险，建议查看详情"
    latest_status = str(best.get("latest_status") or "")
    if latest_status and latest_status not in {"pass", "recovered", "warn"}:
        if latest_status == "error":
            return "推荐模型输出异常，需查看详情"
        if latest_status == "truncated":
            return "推荐模型输出被截断，需查看详情"
        return f"推荐模型状态为 {latest_status}，需查看详情"
    if _is_stale(run_metadata, scan_interval_seconds):
        return "当前结果已过时"
    return None


def _is_stale(run_metadata: dict[str, object], scan_interval_seconds: int | None) -> bool:
    completed_at = run_metadata.get("completed_at")
    if not completed_at:
        return False
    try:
        completed = datetime.fromisoformat(str(completed_at))
    except ValueError:
        return False
    now = datetime.now(completed.tzinfo)
    scheduler_threshold = max(0, scan_interval_seconds or 0) * 2
    stale_threshold = max(86400, scheduler_threshold)
    return (now - completed).total_seconds() > stale_threshold


def _runner_up_gap_text(
    best: dict[str, object],
    runner_up: dict[str, object] | None,
    recommendation_basis: str,
    run_metadata: dict[str, object],
) -> str:
    if runner_up is None:
        return "当前只有一个有效候选模型"
    if recommendation_basis == "overall_score_pending":
        return "扫描中，等待所有模型完成 Q1～Q5"
    best_score = best.get("overall_score")
    runner_up_score = runner_up.get("overall_score")
    if best_score is None or runner_up_score is None:
        return "等待所有模型完成 Q1～Q5"
    gap = int(best_score) - int(runner_up_score)
    if gap > 0:
        return f"总分领先第二名 {gap} 分"
    return "总分同分"


def _health_reason_codes(
    run_metadata: dict[str, object],
    best: dict[str, object],
    scan_interval_seconds: int | None,
) -> list[str]:
    status = str(run_metadata.get("status") or "legacy")
    question_pack_version = str(run_metadata.get("question_pack_version") or "unknown")
    latest_status = str(best.get("latest_status") or "")
    if status in {"running", "partial"}:
        return ["run_incomplete"]
    if not question_pack_version or question_pack_version == "unknown":
        return ["legacy_data" if status == "legacy" else "pack_metadata_missing"]
    if int(best.get("hits_516") or 0) > 0 or (latest_status and latest_status not in {"pass", "recovered", "warn"}):
        return ["anomalous"]
    if _is_stale(run_metadata, scan_interval_seconds):
        return ["stale"]
    return ["anomalous"]


def _question_pack_texts(run_metadata: dict[str, object]) -> tuple[str, str]:
    version = str(run_metadata.get("question_pack_version") or "unknown")
    status = str(run_metadata.get("status") or "legacy")
    if version and version != "unknown":
        return version, "旧数据" if status == "legacy" else "当前 run"
    if status == "legacy":
        return "未记录（旧数据）", "旧数据"
    return "题包版本缺失", "当前 run"


def _effort_label(effort: str) -> str:
    return effort.strip().lower()


def _route_identity_status(
    model_view: DashboardModelView,
    results: list[ScanResult],
) -> str:
    if model_view.source_mode != "api":
        return "not_required"
    if model_view.route_fingerprint is None:
        return "unavailable"
    fingerprints = [
        str(item.execution_trace.get("route_fingerprint") or "").strip()
        for item in results
    ]
    if not fingerprints or any(not fingerprint for fingerprint in fingerprints):
        return "missing"
    distinct = set(fingerprints)
    if len(distinct) > 1:
        return "mixed"
    return (
        "matched"
        if next(iter(distinct)) == model_view.route_fingerprint
        else "changed"
    )


def _enabled_model_views(
    model_catalog: ModelIngressConfig | Iterable[ResolvedScanTarget | TargetConfig],
    *,
    include_disabled: bool = False,
) -> list[DashboardModelView]:
    if isinstance(model_catalog, ModelIngressConfig):
        source_by_id = {source.id: source for source in model_catalog.sources}
        views: list[DashboardModelView] = []
        for connection in model_catalog.connections:
            source = source_by_id.get(connection.source_id)
            source_enabled = source.enabled if source else True
            if not include_disabled and (not source_enabled or not connection.enabled):
                continue
            suffix_aliases = (
                infer_reasoning_suffix_aliases(
                    [candidate.model_id for candidate in connection.model_candidates]
                )
                if connection.api_format is not None
                else {}
            )
            for candidate in connection.model_candidates:
                if not include_disabled and not candidate.enabled:
                    continue
                identity = resolve_model_display_identity(
                    model_id=candidate.model_id,
                    scan_profile=candidate.scan_profile,
                    family_id=candidate.family_id,
                    variant_id=candidate.variant_id,
                    inferred_alias=suffix_aliases.get(candidate.model_id),
                )
                views.append(
                    DashboardModelView(
                        id=candidate.id,
                        label=model_display_label(
                            raw_model_id=candidate.model_id,
                            identity=identity,
                        ),
                        model=identity.model,
                        model_id=candidate.model_id,
                        effort=identity.effort,
                        display_name=candidate.display_name,
                        source_id=source.id if source else None,
                        source_title=source.title if source else None,
                        source_mode=source.mode if source else None,
                        connection_id=connection.id,
                        connection_name=connection.name,
                        family_id=identity.family_id,
                        variant_id=identity.variant_id,
                        reasoning_tokens_supported=(
                            "reasoning_tokens_unavailable"
                            not in candidate.capabilities
                        ),
                        route_fingerprint=build_route_fingerprint(
                            source_id=source.id if source else "unknown",
                            connection_id=connection.id,
                            connection_mode=source.mode if source else "unknown",
                            api_format=connection.api_format,
                            provider_preset=connection.provider_preset,
                            base_url=connection.base_url,
                            model_id=candidate.model_id,
                            scan_profile=candidate.scan_profile,
                        ),
                    )
                )
        return views

    views = []
    for item in model_catalog:
        if isinstance(item, ResolvedScanTarget):
            views.append(
                DashboardModelView(
                    id=item.candidate_id,
                    label=item.label,
                    model=item.model_id,
                    model_id=item.model_id,
                    effort=item.scan_profile,
                    display_name=item.display_name,
                    source_id=item.source_id,
                    source_title="兼容接入",
                    source_mode=item.connection_mode,
                    connection_id=item.connection_id,
                    reasoning_tokens_supported=item.reasoning_tokens_supported,
                    route_fingerprint=build_route_fingerprint(
                        source_id=item.source_id,
                        connection_id=item.connection_id,
                        connection_mode=item.connection_mode,
                        api_format=item.api_format,
                        provider_preset=item.provider_preset,
                        base_url=item.base_url,
                        model_id=item.model_id,
                        scan_profile=item.scan_profile,
                    ),
                )
            )
            continue
        if isinstance(item, TargetConfig) and (include_disabled or item.enabled):
            label = f"{item.model} / {item.effort}"
            views.append(
                DashboardModelView(
                    id=label,
                    label=label,
                    model=item.model,
                    model_id=item.model,
                    effort=item.effort,
                    display_name=label,
                    source_id="ingress_compat",
                    source_title="兼容接入",
                    source_mode="compat",
                )
            )
    return views


def _match_model_view(
    item: ScanResult,
    models_by_id: dict[str, DashboardModelView],
    models_by_label: dict[str, DashboardModelView],
) -> DashboardModelView | None:
    if item.candidate_id and item.candidate_id in models_by_id:
        return models_by_id[item.candidate_id]
    return models_by_label.get(_fallback_history_key(item))


def _fallback_history_key(item: ScanResult) -> str:
    return f"{item.model} / {item.effort}"


def _planned_total(
    planned_attempts: dict[str, object],
    model_view: DashboardModelView,
    fallback_total: int,
) -> int:
    for key in (model_view.id, model_view.label):
        planned = planned_attempts.get(key)
        if planned is not None:
            return int(planned)
    return fallback_total


def _ingress_group_key(model_view: DashboardModelView) -> str:
    return model_view.source_id or model_view.connection_id or "ingress_compat"
