from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from typing import Mapping, Sequence

from .costing import (
    current_pricing_snapshot_id,
    estimate_reference_cost,
    estimate_reference_cost_from_frozen_pricing,
    frozen_reference_pricing,
)
from .usage_store import UsageStore


MAX_RETAINED_EPOCHS = 100
SEGMENT_CONTRACT_VERSION = 2
SETTLEMENT_GRACE = timedelta(minutes=2)
MIN_MODEL_WAIT_COVERAGE_PERCENT = 50.0
MIN_MODEL_WAIT_WORK_UNITS = 3


def update_recommendation_use_epochs(
    *,
    store: UsageStore,
    state: Mapping[str, object],
    contexts: Sequence[Mapping[str, object]],
    portfolio: Mapping[str, object],
    workload: Mapping[str, object] | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    with store.transaction():
        return _update_recommendation_use_epochs_unlocked(
            store=store,
            state=state,
            contexts=contexts,
            portfolio=portfolio,
            workload=workload,
            now=now,
        )


def _update_recommendation_use_epochs_unlocked(
    *,
    store: UsageStore,
    state: Mapping[str, object],
    contexts: Sequence[Mapping[str, object]],
    portfolio: Mapping[str, object],
    workload: Mapping[str, object] | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    observed_at = _as_utc(now or datetime.now(timezone.utc))
    _refresh_recommendation_use_observations_unlocked(
        store=store,
        now=observed_at,
    )
    persisted = store.load_recommendation_use_state()
    epochs = _epochs(persisted)
    assignments = _assignments(persisted)
    configurations = _configuration_index(state)
    segment_contract_version = (
        _integer(persisted.get("segment_contract_version")) or 1
    )
    previous_effective_configuration_id = _text(
        persisted.get("effective_current_model_configuration_id")
    ) or _text(persisted.get("representative_current_model_configuration_id"))
    detected_effective_configuration_id = _current_configuration_id(state)
    effective_configuration_id = (
        detected_effective_configuration_id
        or previous_effective_configuration_id
    )
    contexts_by_current = {
        current_id: context
        for context in contexts
        if (current_id := _text(context.get("current_model_configuration_id")))
    }
    decisions = [
        item
        for item in _mapping_items(portfolio.get("decisions"))
        if item.get("decision") == "recommend"
        and _text(item.get("candidate_model_configuration_id"))
        and item.get("candidate_model_configuration_id")
        != item.get("current_model_configuration_id")
    ]
    desired: dict[str, dict[str, object]] = {}
    for decision in decisions:
        current_id = _text(decision.get("current_model_configuration_id"))
        candidate_id = _text(decision.get("candidate_model_configuration_id"))
        context = contexts_by_current.get(current_id or "")
        if not current_id or not candidate_id or context is None:
            continue
        if not _text(context.get("resolved_data_source")) or not _text(
            context.get("source_snapshot_id")
        ):
            continue
        current = configurations.get(current_id)
        candidate = configurations.get(candidate_id)
        if current is None or candidate is None or not candidate["enabled"]:
            continue
        desired[current_id] = _epoch_template(
            current=current,
            candidate=candidate,
            context=context,
            preference=_text(portfolio.get("preference")) or "smart",
            started_at=observed_at,
        )

    switch_detected = bool(
        previous_effective_configuration_id
        and detected_effective_configuration_id
        and previous_effective_configuration_id
        != detected_effective_configuration_id
    )
    segment_contract_ready = segment_contract_version >= SEGMENT_CONTRACT_VERSION
    if switch_detected:
        segment_contract_ready = _record_actual_switch(
            epochs=epochs,
            desired=desired,
            previous_configuration_id=str(previous_effective_configuration_id),
            current_configuration_id=str(detected_effective_configuration_id),
            configurations=configurations,
            contexts=contexts,
            preference=_text(portfolio.get("preference")) or "smart",
            observed_at=observed_at,
        ) or segment_contract_ready
    elif not segment_contract_ready and detected_effective_configuration_id:
        segment_contract_ready = _migrate_current_usage_tail(
            epochs=epochs,
            configurations=configurations,
            contexts=contexts,
            observations=_mapping(
                store.load_usage_state().get("observations")
            ),
            current_configuration_id=detected_effective_configuration_id,
            preference=_text(portfolio.get("preference")) or "smart",
        )

    active_current_ids = {
        current_id
        for decision in _mapping_items(portfolio.get("decisions"))
        if (current_id := _text(decision.get("current_model_configuration_id")))
    }

    open_by_current = {
        str(epoch.get("current_model_configuration_id")): epoch
        for epoch in epochs
        if epoch.get("lifecycle_status") == "open"
    }
    for current_id, epoch in open_by_current.items():
        target = desired.get(current_id)
        if target is None:
            candidate_id = _text(epoch.get("recommended_model_configuration_id"))
            if _adoption_keeps_epoch_open(
                epoch,
                candidate_id=candidate_id,
                active_current_ids=active_current_ids,
                contexts_by_current=contexts_by_current,
                portfolio=portfolio,
                configurations=configurations,
                effective_configuration_id=effective_configuration_id,
            ):
                continue
            _begin_settling(
                epoch,
                at=observed_at,
                reason=_terminal_reason(
                    portfolio,
                    epoch=epoch,
                    configurations=configurations,
                ),
            )
            continue
        if epoch.get("comparison_fingerprint") != target["comparison_fingerprint"]:
            _begin_settling(
                epoch,
                at=observed_at,
                reason=_change_reason(epoch, target),
            )
        else:
            _upgrade_open_epoch_contract(epoch, target)
            desired.pop(current_id, None)

    for target in desired.values():
        epochs.append(target)

    retained_epochs = _retained_epochs(epochs)
    retained_ids = {str(epoch.get("use_epoch_id")) for epoch in retained_epochs}
    previous_value_summary = persisted.get("value_summary")
    persisted = {
        "schema_version": 1,
        "segment_contract_version": (
            SEGMENT_CONTRACT_VERSION
            if segment_contract_ready
            else segment_contract_version
        ),
        "epochs": retained_epochs,
        "observation_assignments": {
            observation_id: epoch_id
            for observation_id, epoch_id in assignments.items()
            if epoch_id in retained_ids
        },
        "representative_current_model_configuration_id": portfolio.get(
            "representative_configuration_id"
        ),
        "effective_current_model_configuration_id": effective_configuration_id,
    }
    if isinstance(previous_value_summary, Mapping):
        persisted["value_summary"] = dict(previous_value_summary)
    store.save_recommendation_use_state(persisted)
    return _refresh_recommendation_use_observations_unlocked(
        store=store,
        state=state,
        workload=workload,
        portfolio=portfolio,
        now=observed_at,
    )


def refresh_recommendation_use_observations(
    *,
    store: UsageStore,
    state: Mapping[str, object] | None = None,
    workload: Mapping[str, object] | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    with store.transaction():
        return _refresh_recommendation_use_observations_unlocked(
            store=store,
            state=state,
            workload=workload,
            now=now,
        )


def _refresh_recommendation_use_observations_unlocked(
    *,
    store: UsageStore,
    state: Mapping[str, object] | None = None,
    workload: Mapping[str, object] | None = None,
    portfolio: Mapping[str, object] | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    observed_at = _as_utc(now or datetime.now(timezone.utc))
    persisted = store.load_recommendation_use_state()
    epochs = _epochs(persisted)
    observations = _mapping(store.load_usage_state().get("observations"))
    assignments = _rebuild_observation_assignments(
        epochs,
        _assignments(persisted),
        observations,
    )
    observable_epochs = [
        epoch
        for epoch in epochs
        if epoch.get("lifecycle_status") in {"open", "settling"}
    ]

    candidates_by_identity: dict[tuple[str, str], list[dict[str, object]]] = {}
    for epoch in observable_epochs:
        key = (
            str(epoch.get("recommended_raw_model_id") or "").casefold(),
            str(epoch.get("recommended_reasoning_effort") or "").casefold(),
        )
        candidates_by_identity.setdefault(key, []).append(epoch)

    for observation_id, raw in observations.items():
        if observation_id in assignments or not isinstance(raw, Mapping):
            continue
        if not _eligible_observation(raw):
            continue
        key = (
            str(raw.get("raw_model_id") or "").casefold(),
            str(raw.get("reasoning_effort") or "").casefold(),
        )
        matching = [
            epoch
            for epoch in candidates_by_identity.get(key, [])
            if _observation_matches_epoch(raw, epoch, observations)
        ]
        if len(matching) != 1:
            continue
        epoch = matching[0]
        assignments[str(observation_id)] = str(epoch["use_epoch_id"])
        _apply_observation(epoch, raw)

    _finalize_settling_epochs(epochs, now=observed_at)
    retained_epochs = _retained_epochs(epochs)
    retained_ids = {str(epoch.get("use_epoch_id")) for epoch in retained_epochs}
    persisted = {
        "schema_version": 1,
        "segment_contract_version": persisted.get(
            "segment_contract_version",
            1,
        ),
        "epochs": retained_epochs,
        "observation_assignments": {
            observation_id: epoch_id
            for observation_id, epoch_id in assignments.items()
            if epoch_id in retained_ids
        },
        "representative_current_model_configuration_id": persisted.get(
            "representative_current_model_configuration_id"
        ),
        "effective_current_model_configuration_id": persisted.get(
            "effective_current_model_configuration_id"
        ),
    }
    previous_value_summary = store.load_recommendation_use_state().get(
        "value_summary"
    )
    if state is not None and workload is not None:
        persisted["value_summary"] = _value_summary(
            persisted,
            state=state,
            workload=workload,
            portfolio=portfolio,
        )
    elif isinstance(previous_value_summary, Mapping):
        persisted["value_summary"] = dict(previous_value_summary)
    store.save_recommendation_use_state(persisted)
    return _public_summary(persisted)


def read_recommendation_use_summary(*, store: UsageStore) -> dict[str, object]:
    return _public_summary(store.load_recommendation_use_state())


def _epoch_template(
    *,
    current: Mapping[str, object],
    candidate: Mapping[str, object],
    context: Mapping[str, object],
    preference: str,
    started_at: datetime,
) -> dict[str, object]:
    rows = {
        str(item.get("model_configuration_id")): item
        for item in _mapping_items(context.get("resolved_result_rows"))
    }
    current_row = rows.get(str(current["id"]), {})
    candidate_row = rows.get(str(candidate["id"]), {})
    source = _text(context.get("resolved_data_source"))
    snapshot_id = _text(context.get("source_snapshot_id"))
    current_pricing_snapshot = current_pricing_snapshot_id()
    pricing_snapshot_id = (
        _text(context.get("pricing_snapshot_id")) or current_pricing_snapshot
    )
    route_fingerprint = _text(candidate_row.get("route_fingerprint"))
    identity_unique = bool(candidate.get("identity_unique", False))
    observable_usage_provider_ids = list(
        candidate.get("observable_usage_provider_ids", [])
    )
    attribution_route_basis = (
        "codex_rollout_unique_enabled_configuration"
        if identity_unique and observable_usage_provider_ids
        else "ambiguous_identity"
        if not identity_unique
        else "unsupported_route"
    )
    fingerprint_parts = [
        str(current["id"]),
        str(candidate["id"]),
        source or "",
        snapshot_id or "",
        pricing_snapshot_id,
        preference,
        route_fingerprint or "",
        str(current_row.get("elapsed_seconds") or ""),
        str(candidate_row.get("elapsed_seconds") or ""),
        str(current_row.get("estimated_cost_usd") or ""),
        str(candidate_row.get("estimated_cost_usd") or ""),
    ]
    comparison_fingerprint = _sha256("|".join(fingerprint_parts))
    started = _iso(started_at)
    use_epoch_id = "use_epoch_" + _sha256(
        f"{comparison_fingerprint}|{started}"
    )[:20]
    return {
        "schema_version": 1,
        "use_epoch_id": use_epoch_id,
        "recommendation_id": "rec_" + comparison_fingerprint[:20],
        "segment_kind": "recommendation",
        "current_model_configuration_id": current["id"],
        "recommended_model_configuration_id": candidate["id"],
        "recommended_raw_model_id": candidate["model_id"],
        "recommended_reasoning_effort": candidate["effort"],
        "identity_unique": identity_unique,
        "observable_usage_provider_ids": observable_usage_provider_ids,
        "attribution_route_basis": attribution_route_basis,
        "resolved_data_source": source,
        "evaluation_snapshot_id": snapshot_id,
        "pricing_snapshot_id": pricing_snapshot_id,
        "question_pack_version": _text(current_row.get("question_pack_version")),
        "grader_version": _text(current_row.get("grader_version")),
        "recommended_pricing": (
            frozen_reference_pricing(str(candidate["model_id"]))
            if pricing_snapshot_id == current_pricing_snapshot
            else None
        ),
        "preference": preference,
        "route_fingerprint": route_fingerprint,
        "current_full_pack_reference_cost_usd": _complete_reference_cost(
            current_row
        ),
        "candidate_full_pack_reference_cost_usd": _complete_reference_cost(
            candidate_row
        ),
        "current_full_pack_duration_ms": _seconds_to_ms(
            current_row.get("elapsed_seconds")
        ),
        "candidate_full_pack_duration_ms": _seconds_to_ms(
            candidate_row.get("elapsed_seconds")
        ),
        "comparison_fingerprint": comparison_fingerprint,
        "started_at": started,
        "ended_at": None,
        "end_reason": None,
        "settle_after": None,
        "last_observed_at": None,
        "observed_candidate_session_keys": [],
        "observed_candidate_session_count": 0,
        "observed_candidate_work_unit_count": 0,
        "observed_candidate_reference_cost_usd": 0.0,
        "observed_candidate_response_wait_ms": None,
        "estimated_reference_cost_delta_usd": None,
        "estimated_model_wait_delta_ms": None,
        "lifecycle_status": "open",
        "estimate_status": "prospective",
        "estimate_basis": "full_pack_evaluation",
        "reference_cost_estimate_status": "prospective",
        "model_wait_estimate_status": "prospective",
    }


def _record_actual_switch(
    *,
    epochs: list[dict[str, object]],
    desired: dict[str, dict[str, object]],
    previous_configuration_id: str,
    current_configuration_id: str,
    configurations: Mapping[str, Mapping[str, object]],
    contexts: Sequence[Mapping[str, object]],
    preference: str,
    observed_at: datetime,
) -> bool:
    for epoch in epochs:
        if (
            epoch.get("lifecycle_status") == "open"
            and _segment_kind(epoch) == "actual_switch"
            and epoch.get("recommended_model_configuration_id")
            != current_configuration_id
        ):
            _begin_settling(
                epoch,
                at=observed_at,
                reason="configuration_switched",
            )

    adopted = next(
        (
            epoch
            for epoch in reversed(epochs)
            if epoch.get("lifecycle_status") == "open"
            and epoch.get("current_model_configuration_id")
            == previous_configuration_id
            and epoch.get("recommended_model_configuration_id")
            == current_configuration_id
        ),
        None,
    )
    if adopted is not None:
        adopted["segment_kind"] = "actual_switch"
    else:
        current = configurations.get(previous_configuration_id)
        candidate = configurations.get(current_configuration_id)
        context = _comparison_context(
            contexts,
            current_configuration_id=previous_configuration_id,
            candidate_configuration_id=current_configuration_id,
        )
        if (
            current is not None
            and candidate is not None
            and bool(candidate.get("enabled", False))
            and context is not None
        ):
            adopted = _epoch_template(
                current=current,
                candidate=candidate,
                context=context,
                preference=preference,
                started_at=observed_at,
            )
            adopted["segment_kind"] = "actual_switch"
            epochs.append(adopted)

    desired.pop(previous_configuration_id, None)
    for epoch in epochs:
        if (
            epoch is not adopted
            and epoch.get("lifecycle_status") == "open"
            and _segment_kind(epoch) == "recommendation"
            and (
                epoch.get("current_model_configuration_id")
                == previous_configuration_id
                or epoch.get("recommended_model_configuration_id")
                == current_configuration_id
            )
        ):
            _begin_settling(
                epoch,
                at=observed_at,
                reason="configuration_switched",
            )
    return adopted is not None


def _migrate_current_usage_tail(
    *,
    epochs: list[dict[str, object]],
    configurations: Mapping[str, Mapping[str, object]],
    contexts: Sequence[Mapping[str, object]],
    observations: Mapping[str, object],
    current_configuration_id: str,
    preference: str,
) -> bool:
    if any(
        _segment_kind(epoch) == "actual_switch"
        and epoch.get("recommended_model_configuration_id")
        == current_configuration_id
        for epoch in epochs
    ):
        return True
    boundary = _current_usage_tail_boundary(
        configurations=configurations,
        observations=observations,
        current_configuration_id=current_configuration_id,
    )
    if boundary is None:
        return False
    previous_configuration_id, started_at = boundary
    current = configurations.get(previous_configuration_id)
    candidate = configurations.get(current_configuration_id)
    context = _comparison_context(
        contexts,
        current_configuration_id=previous_configuration_id,
        candidate_configuration_id=current_configuration_id,
    )
    if current is None or candidate is None or context is None:
        return False
    epoch = _epoch_template(
        current=current,
        candidate=candidate,
        context=context,
        preference=preference,
        started_at=started_at,
    )
    epoch["segment_kind"] = "actual_switch"
    epochs.append(epoch)
    return True


def _current_usage_tail_boundary(
    *,
    configurations: Mapping[str, Mapping[str, object]],
    observations: Mapping[str, object],
    current_configuration_id: str,
) -> tuple[str, datetime] | None:
    attributed: list[tuple[str, datetime, datetime]] = []
    for raw in observations.values():
        if not isinstance(raw, Mapping):
            continue
        configuration_id = _completed_observation_configuration_id(
            raw,
            configurations=configurations,
        )
        started_at = _timestamp(raw.get("started_at"))
        ended_at = _timestamp(raw.get("ended_at")) or started_at
        if configuration_id and started_at is not None and ended_at is not None:
            attributed.append((configuration_id, started_at, ended_at))
    previous = max(
        (
            item
            for item in attributed
            if item[0] != current_configuration_id
        ),
        key=lambda item: item[2],
        default=None,
    )
    if previous is None:
        return None
    first_current = min(
        (
            item
            for item in attributed
            if item[0] == current_configuration_id
            and item[1] >= previous[2]
        ),
        key=lambda item: item[1],
        default=None,
    )
    if first_current is None:
        return None
    return previous[0], first_current[1]


def _completed_observation_configuration_id(
    row: Mapping[str, object],
    *,
    configurations: Mapping[str, Mapping[str, object]],
) -> str | None:
    if (
        not _eligible_observation(row)
        or row.get("outcome") != "completed"
        or row.get("is_subagent") is True
    ):
        return None
    model_id = str(row.get("raw_model_id") or "").casefold()
    effort = str(row.get("reasoning_effort") or "").casefold()
    provider_id = str(row.get("provider_id") or "").casefold()
    matching = [
        configuration_id
        for configuration_id, configuration in configurations.items()
        if bool(configuration.get("identity_unique", False))
        and str(configuration.get("model_id") or "").casefold() == model_id
        and str(configuration.get("effort") or "").casefold() == effort
        and provider_id
        in {
            str(item).casefold()
            for item in configuration.get("observable_usage_provider_ids", [])
        }
    ]
    return matching[0] if len(matching) == 1 else None


def _comparison_context(
    contexts: Sequence[Mapping[str, object]],
    *,
    current_configuration_id: str,
    candidate_configuration_id: str,
) -> Mapping[str, object] | None:
    for context in contexts:
        if not _text(context.get("resolved_data_source")) or not _text(
            context.get("source_snapshot_id")
        ):
            continue
        row_ids = {
            _text(row.get("model_configuration_id"))
            for row in _mapping_items(context.get("resolved_result_rows"))
        }
        if {current_configuration_id, candidate_configuration_id} <= row_ids:
            return context
    return None


def _configuration_index(state: Mapping[str, object]) -> dict[str, dict[str, object]]:
    config = _mapping(state.get("config"))
    ingress = _mapping(config.get("model_ingress"))
    sources = {
        str(source.get("id") or ""): {
            "enabled": bool(source.get("enabled", False)),
            "kind": str(source.get("kind") or "").casefold(),
        }
        for source in _mapping_items(ingress.get("sources"))
    }
    raw: list[dict[str, object]] = []
    for connection in _mapping_items(ingress.get("connections")):
        source_id = str(connection.get("source_id") or "")
        source = sources.get(source_id, {"enabled": True, "kind": ""})
        connection_enabled = bool(source["enabled"]) and bool(
            connection.get("enabled", False)
        )
        for candidate in _mapping_items(connection.get("model_candidates")):
            candidate_id = _text(candidate.get("id"))
            model_id = _text(candidate.get("model_id"))
            effort = _text(candidate.get("scan_profile"))
            if not candidate_id or not model_id or not effort:
                continue
            raw.append(
                {
                    "id": candidate_id,
                    "model_id": model_id,
                    "effort": effort,
                    "enabled": connection_enabled and bool(candidate.get("enabled", False)),
                    "observable_usage_provider_ids": (
                        ["openai", "codex"]
                        if source["kind"] == "codex"
                        else []
                    ),
                }
            )
    counts: dict[tuple[str, str], int] = {}
    for item in raw:
        if item["enabled"]:
            key = (str(item["model_id"]).casefold(), str(item["effort"]).casefold())
            counts[key] = counts.get(key, 0) + 1
    for item in raw:
        key = (str(item["model_id"]).casefold(), str(item["effort"]).casefold())
        item["identity_unique"] = bool(item["enabled"]) and counts.get(key) == 1
    return {str(item["id"]): item for item in raw}


def _eligible_observation(row: Mapping[str, object]) -> bool:
    if row.get("is_modeldial_evaluation") is True:
        return False
    if float(row.get("attribution_confidence") or 0) < 1.0:
        return False
    if _mapping_items(row.get("exclusion_reasons")):
        return False
    exclusions = row.get("exclusion_reasons")
    if isinstance(exclusions, list) and exclusions:
        return False
    return bool(_text(row.get("raw_model_id")) and _text(row.get("reasoning_effort")))


def _rebuild_observation_assignments(
    epochs: Sequence[dict[str, object]],
    assignments: Mapping[str, str],
    observations: Mapping[str, object],
) -> dict[str, str]:
    epochs_by_id = {
        str(epoch.get("use_epoch_id")): epoch
        for epoch in epochs
        if _text(epoch.get("use_epoch_id"))
    }
    assigned_by_epoch: dict[str, list[str]] = {}
    invalid_epoch_ids: set[str] = set()
    for observation_id, epoch_id in assignments.items():
        assigned_by_epoch.setdefault(epoch_id, []).append(observation_id)
        raw = observations.get(observation_id)
        epoch = epochs_by_id.get(epoch_id)
        if epoch is None:
            invalid_epoch_ids.add(epoch_id)
        elif isinstance(raw, Mapping) and not _observation_matches_epoch(
            raw,
            epoch,
            observations,
        ):
            invalid_epoch_ids.add(epoch_id)

    rebuildable_epoch_ids = {
        epoch_id
        for epoch_id in invalid_epoch_ids
        if all(
            observation_id in observations
            for observation_id in assigned_by_epoch.get(epoch_id, [])
        )
    }
    rebuilt = dict(assignments)
    for epoch_id in rebuildable_epoch_ids:
        epoch = epochs_by_id.get(epoch_id)
        if epoch is None:
            for observation_id in assigned_by_epoch.get(epoch_id, []):
                rebuilt.pop(observation_id, None)
            continue
        _reset_observed_usage(epoch)
        for observation_id in assigned_by_epoch.get(epoch_id, []):
            rebuilt.pop(observation_id, None)
            raw = observations.get(observation_id)
            if not isinstance(raw, Mapping) or not _observation_matches_epoch(
                raw,
                epoch,
                observations,
            ):
                continue
            rebuilt[observation_id] = epoch_id
            _apply_observation(epoch, raw)
    return rebuilt


def _reset_observed_usage(epoch: dict[str, object]) -> None:
    epoch["last_observed_at"] = None
    epoch["observed_candidate_session_keys"] = []
    epoch["observed_candidate_session_count"] = 0
    epoch["observed_candidate_work_unit_count"] = 0
    epoch["observed_candidate_reference_cost_usd"] = 0.0
    epoch["observed_candidate_response_wait_ms"] = None
    _recalculate_estimate(epoch)


def _observation_matches_epoch(
    row: Mapping[str, object],
    epoch: Mapping[str, object],
    observations: Mapping[str, object],
) -> bool:
    if not _eligible_observation(row) or not _observation_is_within(row, epoch):
        return False
    if not bool(epoch.get("identity_unique", False)):
        return False
    allowed_providers = {
        str(item).casefold()
        for item in epoch.get("observable_usage_provider_ids", [])
    }
    if str(row.get("provider_id") or "").casefold() not in allowed_providers:
        return False
    return not _session_used_candidate_before_recommendation(
        row,
        epoch,
        observations,
    )


def _session_used_candidate_before_recommendation(
    row: Mapping[str, object],
    epoch: Mapping[str, object],
    observations: Mapping[str, object],
) -> bool:
    session_key = _text(row.get("session_key"))
    epoch_started = _timestamp(epoch.get("started_at"))
    if not session_key or epoch_started is None:
        return True
    candidate_model = str(epoch.get("recommended_raw_model_id") or "").casefold()
    candidate_effort = str(
        epoch.get("recommended_reasoning_effort") or ""
    ).casefold()
    allowed_providers = {
        str(item).casefold()
        for item in epoch.get("observable_usage_provider_ids", [])
    }
    for candidate in observations.values():
        if not isinstance(candidate, Mapping) or not _eligible_observation(candidate):
            continue
        candidate_started = _timestamp(candidate.get("started_at"))
        if candidate_started is None or candidate_started >= epoch_started:
            continue
        if _text(candidate.get("session_key")) != session_key:
            continue
        if str(candidate.get("raw_model_id") or "").casefold() != candidate_model:
            continue
        if str(candidate.get("reasoning_effort") or "").casefold() != candidate_effort:
            continue
        if str(candidate.get("provider_id") or "").casefold() not in allowed_providers:
            continue
        return True
    return False


def _observation_is_within(
    row: Mapping[str, object],
    epoch: Mapping[str, object],
) -> bool:
    observation_started = _timestamp(row.get("started_at"))
    ended = _timestamp(row.get("ended_at")) or observation_started
    started = _timestamp(epoch.get("started_at"))
    if (
        observation_started is None
        or ended is None
        or started is None
        or observation_started < started
    ):
        return False
    epoch_end = _timestamp(epoch.get("ended_at"))
    return epoch_end is None or ended <= epoch_end


def _apply_observation(epoch: dict[str, object], row: Mapping[str, object]) -> None:
    session_key = _text(row.get("session_key"))
    sessions = [str(item) for item in epoch.get("observed_candidate_session_keys", [])]
    if session_key and session_key not in sessions:
        sessions.append(session_key)
    epoch["observed_candidate_session_keys"] = sessions
    epoch["observed_candidate_session_count"] = len(sessions)
    if row.get("outcome") == "completed" and row.get("is_subagent") is not True:
        epoch["observed_candidate_work_unit_count"] = int(
            epoch.get("observed_candidate_work_unit_count") or 0
        ) + 1

    usage = _mapping(row.get("usage"))
    frozen_pricing = epoch.get("recommended_pricing")
    if isinstance(frozen_pricing, Mapping):
        cost = estimate_reference_cost_from_frozen_pricing(
            frozen_pricing,
            input_tokens=_integer(usage.get("input_tokens")),
            cached_input_tokens=_integer(usage.get("cached_input_tokens")),
            cache_write_input_tokens=_integer(
                usage.get("cache_write_input_tokens")
            ),
            output_tokens=_integer(usage.get("output_tokens")),
            reasoning_output_tokens=_integer(usage.get("reasoning_tokens")),
        )
    else:
        cost = estimate_reference_cost(
            str(row.get("raw_model_id") or ""),
            input_tokens=_integer(usage.get("input_tokens")),
            cached_input_tokens=_integer(usage.get("cached_input_tokens")),
            cache_write_input_tokens=_integer(
                usage.get("cache_write_input_tokens")
            ),
            output_tokens=_integer(usage.get("output_tokens")),
            reasoning_output_tokens=_integer(usage.get("reasoning_tokens")),
        )
    if (
        cost.status == "estimated"
        and cost.pricing_snapshot == epoch.get("pricing_snapshot_id")
    ):
        observed_cost = float(epoch.get("observed_candidate_reference_cost_usd") or 0) + float(cost.usd or 0)
        epoch["observed_candidate_reference_cost_usd"] = round(observed_cost, 9)

    response_wait = _positive_integer(row.get("response_wait_ms"))
    if response_wait is not None:
        epoch["observed_candidate_response_wait_ms"] = int(
            epoch.get("observed_candidate_response_wait_ms") or 0
        ) + response_wait
    epoch["last_observed_at"] = row.get("ended_at") or row.get("started_at")
    _recalculate_estimate(epoch)


def _recalculate_estimate(epoch: dict[str, object]) -> None:
    work_units = int(epoch.get("observed_candidate_work_unit_count") or 0)
    observed_cost = _number(epoch.get("observed_candidate_reference_cost_usd"))
    current_cost = _positive_number(epoch.get("current_full_pack_reference_cost_usd"))
    candidate_cost = _positive_number(epoch.get("candidate_full_pack_reference_cost_usd"))
    epoch["estimated_reference_cost_delta_usd"] = None
    if work_units > 0 and observed_cost and current_cost and candidate_cost:
        estimated_current = observed_cost * current_cost / candidate_cost
        epoch["estimated_reference_cost_delta_usd"] = round(
            observed_cost - estimated_current,
            9,
        )
        epoch["reference_cost_estimate_status"] = "estimated"
    elif work_units > 0:
        epoch["reference_cost_estimate_status"] = "unavailable"
    elif observed_cost:
        epoch["reference_cost_estimate_status"] = "insufficient_work"
    else:
        epoch["reference_cost_estimate_status"] = "prospective"

    observed_wait = _positive_integer(epoch.get("observed_candidate_response_wait_ms"))
    current_duration = _positive_number(epoch.get("current_full_pack_duration_ms"))
    candidate_duration = _positive_number(epoch.get("candidate_full_pack_duration_ms"))
    epoch["estimated_model_wait_delta_ms"] = None
    if (
        work_units > 0
        and observed_wait is not None
        and current_duration
        and candidate_duration
    ):
        estimated_current_wait = observed_wait * current_duration / candidate_duration
        epoch["estimated_model_wait_delta_ms"] = int(
            round(observed_wait - estimated_current_wait)
        )
        epoch["model_wait_estimate_status"] = "estimated"
    elif work_units > 0:
        epoch["model_wait_estimate_status"] = "unavailable"
    elif observed_wait is not None:
        epoch["model_wait_estimate_status"] = "insufficient_work"
    else:
        epoch["model_wait_estimate_status"] = "prospective"

    statuses = {
        str(epoch.get("reference_cost_estimate_status")),
        str(epoch.get("model_wait_estimate_status")),
    }
    if "estimated" in statuses:
        epoch["estimate_status"] = "estimated"
        epoch["estimate_basis"] = "observed_candidate_usage_x_full_pack_ratio"
    elif "insufficient_work" in statuses:
        epoch["estimate_status"] = "insufficient_work"
        epoch["estimate_basis"] = "observed_candidate_usage_without_completed_work"
    elif work_units > 0:
        epoch["estimate_status"] = "unavailable"
        epoch["estimate_basis"] = "observed_candidate_usage_incomplete_basis"
    else:
        epoch["estimate_status"] = "prospective"
        epoch["estimate_basis"] = "full_pack_evaluation"


def _begin_settling(epoch: dict[str, object], *, at: datetime, reason: str) -> None:
    epoch["lifecycle_status"] = "settling"
    epoch["ended_at"] = _iso(at)
    epoch["end_reason"] = reason
    epoch["settle_after"] = _iso(at + SETTLEMENT_GRACE)


def _finalize_settling_epochs(
    epochs: Sequence[dict[str, object]],
    *,
    now: datetime,
) -> None:
    for epoch in epochs:
        if epoch.get("lifecycle_status") != "settling":
            continue
        settle_after = _timestamp(epoch.get("settle_after"))
        if settle_after is not None and now >= settle_after:
            epoch["lifecycle_status"] = "closed"


def _terminal_reason(
    portfolio: Mapping[str, object],
    *,
    epoch: Mapping[str, object],
    configurations: Mapping[str, Mapping[str, object]],
) -> str:
    candidate_id = _text(epoch.get("recommended_model_configuration_id"))
    candidate = configurations.get(candidate_id or "")
    if candidate is None or not bool(candidate.get("enabled", False)):
        return "candidate_unavailable"
    if portfolio.get("status") == "stale":
        return "evidence_expired"
    return "recommendation_changed"


def _change_reason(old: Mapping[str, object], new: Mapping[str, object]) -> str:
    if old.get("resolved_data_source") != new.get("resolved_data_source"):
        return "source_changed"
    if old.get("preference") != new.get("preference"):
        return "preference_changed"
    if old.get("recommended_model_configuration_id") != new.get("recommended_model_configuration_id"):
        return "recommendation_changed"
    if old.get("route_fingerprint") != new.get("route_fingerprint"):
        return "route_changed"
    if old.get("evaluation_snapshot_id") != new.get("evaluation_snapshot_id"):
        return "new_evaluation_snapshot"
    return "new_evaluation_snapshot"


def _adoption_keeps_epoch_open(
    epoch: Mapping[str, object],
    *,
    candidate_id: str | None,
    active_current_ids: set[str],
    contexts_by_current: Mapping[str, Mapping[str, object]],
    portfolio: Mapping[str, object],
    configurations: Mapping[str, Mapping[str, object]],
    effective_configuration_id: str | None,
) -> bool:
    if not candidate_id:
        return False
    candidate = configurations.get(candidate_id)
    if candidate is None or not bool(candidate.get("enabled", False)):
        return False
    if _segment_kind(epoch) == "actual_switch":
        return (
            effective_configuration_id is None
            or candidate_id == effective_configuration_id
        )
    if candidate_id not in active_current_ids:
        return False
    if portfolio.get("status") == "stale":
        return False
    if (_text(portfolio.get("preference")) or "smart") != epoch.get("preference"):
        return False
    context = contexts_by_current.get(candidate_id)
    if context is None:
        return True
    return (
        _text(context.get("resolved_data_source"))
        == epoch.get("resolved_data_source")
        and _text(context.get("source_snapshot_id"))
        == epoch.get("evaluation_snapshot_id")
    )


def _upgrade_open_epoch_contract(
    epoch: dict[str, object],
    target: Mapping[str, object],
) -> None:
    if (
        not isinstance(epoch.get("recommended_pricing"), Mapping)
        and isinstance(target.get("recommended_pricing"), Mapping)
    ):
        epoch["recommended_pricing"] = dict(target["recommended_pricing"])
    epoch.setdefault("settle_after", None)
    epoch.setdefault("last_observed_at", None)
    epoch.setdefault("reference_cost_estimate_status", "prospective")
    epoch.setdefault("model_wait_estimate_status", "prospective")
    epoch.setdefault("segment_kind", "recommendation")


def _public_summary(persisted: Mapping[str, object]) -> dict[str, object]:
    epochs = _epochs(persisted)
    public_epochs = [_public_epoch(epoch) for epoch in epochs]
    representative_id = _text(
        persisted.get("representative_current_model_configuration_id")
    )
    representative = next(
        (
            epoch
            for epoch in reversed(public_epochs)
            if epoch.get("lifecycle_status") == "open"
            and (
                representative_id is None
                or epoch.get("current_model_configuration_id") == representative_id
            )
        ),
        None,
    )
    return {
        "schema_version": 1,
        "epochs": public_epochs,
        "representative_epoch": representative,
        "benefit_summary": _benefit_summary(public_epochs),
        "value_summary": (
            dict(persisted["value_summary"])
            if isinstance(persisted.get("value_summary"), Mapping)
            else None
        ),
    }


def _value_summary(
    persisted: Mapping[str, object],
    *,
    state: Mapping[str, object],
    workload: Mapping[str, object],
    portfolio: Mapping[str, object] | None,
) -> dict[str, object]:
    epochs = _epochs(persisted)
    benefit = _benefit_summary([_public_epoch(epoch) for epoch in epochs])
    if benefit["status"] == "estimated":
        work_epochs = [
            epoch
            for epoch in epochs
            if int(epoch.get("observed_candidate_work_unit_count") or 0) > 0
        ]
        return {
            "schema_version": 1,
            "mode": "realized",
            "period_start": min(
                (_text(epoch.get("started_at")) for epoch in work_epochs),
                default=None,
            ),
            "period_end": benefit.get("latest_observed_at"),
            "period_days": None,
            "current_model_configuration_id": None,
            "candidate_model_configuration_id": None,
            "completed_work_unit_count": benefit["observed_work_unit_count"],
            "reference_cost_usd": None,
            "reference_cost_status": (
                "estimated"
                if benefit.get("reference_cost_delta_usd") is not None
                else "unavailable"
            ),
            "model_wait_ms": None,
            "model_wait_status": (
                "estimated"
                if benefit.get("model_wait_delta_ms") is not None
                else "unavailable"
            ),
            "model_wait_work_unit_count": benefit["model_wait_work_unit_count"],
            "reference_cost_delta_usd": benefit.get("reference_cost_delta_usd"),
            "model_wait_delta_ms": benefit.get("model_wait_delta_ms"),
            "pricing_snapshot_id": _latest_pricing_snapshot_id(work_epochs),
            "coverage_complete": None,
            "basis": "observed_candidate_usage",
        }

    current_id = (
        _text(_mapping(portfolio).get("representative_configuration_id"))
        or _text(persisted.get("representative_current_model_configuration_id"))
        or _portfolio_current_configuration_id(portfolio)
        or _current_configuration_id(state)
    )
    period_start = _text(workload.get("period_start"))
    period_end = _text(workload.get("period_end"))
    period_days = _period_days(period_start, period_end)
    base = {
        "schema_version": 1,
        "period_start": period_start,
        "period_end": period_end,
        "period_days": period_days,
        "current_model_configuration_id": current_id,
        "candidate_model_configuration_id": None,
        "completed_work_unit_count": 0,
        "reference_cost_usd": None,
        "reference_cost_status": "unavailable",
        "model_wait_ms": None,
        "model_wait_status": "unavailable",
        "model_wait_work_unit_count": 0,
        "reference_cost_delta_usd": None,
        "model_wait_delta_ms": None,
        "pricing_snapshot_id": None,
        "coverage_complete": bool(workload.get("coverage_complete", False)),
        "basis": "recent_usage_all_configurations",
    }
    return _all_usage_baseline(base, workload)


def _all_usage_baseline(
    base: Mapping[str, object],
    workload: Mapping[str, object],
) -> dict[str, object]:
    aggregates = _mapping_items(workload.get("aggregates"))
    completed = sum(int(aggregate.get("completed_work_units") or 0) for aggregate in aggregates)
    priced = [
        aggregate
        for aggregate in aggregates
        if _number(aggregate.get("reference_cost_usd")) is not None
    ]
    reference_cost = (
        round(
            sum(float(aggregate.get("reference_cost_usd") or 0) for aggregate in priced),
            9,
        )
        if priced
        else None
    )
    all_priced = bool(aggregates) and len(priced) == len(aggregates) and all(
        aggregate.get("reference_cost_status") == "estimated"
        for aggregate in aggregates
    )
    coverage_complete = bool(workload.get("coverage_complete", False))
    reference_cost_status = (
        "unavailable"
        if reference_cost is None
        else "estimated"
        if all_priced and coverage_complete
        else "lower_bound"
        if all_priced
        else "partial"
    )
    wait_count = sum(
        int(aggregate.get("response_wait_work_unit_count") or 0)
        for aggregate in aggregates
    )
    raw_wait = sum(
        int(aggregate.get("response_wait_ms") or 0)
        for aggregate in aggregates
        if _integer(aggregate.get("response_wait_ms")) is not None
    )
    wait_is_reliable = _model_wait_is_reliable(
        completed_work_units=completed,
        covered_work_units=wait_count,
    )
    pricing_ids = {
        str(aggregate.get("reference_cost_pricing_snapshot_id"))
        for aggregate in priced
        if _text(aggregate.get("reference_cost_pricing_snapshot_id"))
    }
    if not aggregates or (completed <= 0 and reference_cost is None):
        return {
            **base,
            "mode": "no_history",
            "basis": "recent_usage_all_configurations",
        }
    return {
        **base,
        "mode": "usage_baseline",
        "completed_work_unit_count": completed,
        "reference_cost_usd": reference_cost,
        "reference_cost_status": reference_cost_status,
        "model_wait_ms": raw_wait if wait_is_reliable else None,
        "model_wait_status": (
            "estimated"
            if wait_is_reliable
            else "insufficient_coverage"
            if wait_count > 0
            else "unavailable"
        ),
        "model_wait_work_unit_count": wait_count,
        "pricing_snapshot_id": (
            next(iter(pricing_ids)) if len(pricing_ids) == 1 else None
        ),
        "basis": "recent_usage_all_configurations",
    }


def _benefit_summary(epochs: Sequence[Mapping[str, object]]) -> dict[str, object]:
    work_epochs = [
        epoch
        for epoch in epochs
        if int(epoch.get("observed_candidate_work_unit_count") or 0) > 0
    ]
    cost_epochs = [
        epoch
        for epoch in work_epochs
        if _number(epoch.get("estimated_reference_cost_delta_usd")) is not None
    ]
    wait_epochs = [
        epoch
        for epoch in work_epochs
        if _number(epoch.get("estimated_model_wait_delta_ms")) is not None
    ]
    observed_without_work = any(
        _positive_number(epoch.get("observed_candidate_reference_cost_usd"))
        or _positive_integer(epoch.get("observed_candidate_response_wait_ms"))
        for epoch in epochs
    ) and not work_epochs
    if cost_epochs or wait_epochs:
        status = "estimated"
    elif observed_without_work:
        status = "insufficient_work"
    elif work_epochs:
        status = "unavailable"
    else:
        status = "prospective"
    latest_observed_at = max(
        (
            str(epoch.get("last_observed_at"))
            for epoch in epochs
            if _text(epoch.get("last_observed_at"))
        ),
        default=None,
    )
    return {
        "schema_version": 1,
        "status": status,
        "observed_work_unit_count": sum(
            int(epoch.get("observed_candidate_work_unit_count") or 0)
            for epoch in work_epochs
        ),
        "reference_cost_work_unit_count": sum(
            int(epoch.get("observed_candidate_work_unit_count") or 0)
            for epoch in cost_epochs
        ),
        "model_wait_work_unit_count": sum(
            int(epoch.get("observed_candidate_work_unit_count") or 0)
            for epoch in wait_epochs
        ),
        "reference_cost_delta_usd": (
            round(
                sum(
                    float(epoch.get("estimated_reference_cost_delta_usd") or 0)
                    for epoch in cost_epochs
                ),
                9,
            )
            if cost_epochs
            else None
        ),
        "model_wait_delta_ms": (
            sum(int(epoch.get("estimated_model_wait_delta_ms") or 0) for epoch in wait_epochs)
            if wait_epochs
            else None
        ),
        "reference_cost_epoch_count": len(cost_epochs),
        "model_wait_epoch_count": len(wait_epochs),
        "latest_observed_at": latest_observed_at,
        "estimate_basis": "observed_candidate_usage_x_frozen_full_pack_ratio",
    }


def _public_epoch(epoch: Mapping[str, object]) -> dict[str, object]:
    keys = (
        "schema_version",
        "use_epoch_id",
        "recommendation_id",
        "segment_kind",
        "current_model_configuration_id",
        "recommended_model_configuration_id",
        "resolved_data_source",
        "evaluation_snapshot_id",
        "pricing_snapshot_id",
        "started_at",
        "ended_at",
        "end_reason",
        "settle_after",
        "last_observed_at",
        "observed_candidate_session_count",
        "observed_candidate_work_unit_count",
        "observed_candidate_reference_cost_usd",
        "observed_candidate_response_wait_ms",
        "estimated_reference_cost_delta_usd",
        "estimated_model_wait_delta_ms",
        "lifecycle_status",
        "estimate_status",
        "estimate_basis",
        "reference_cost_estimate_status",
        "model_wait_estimate_status",
        "attribution_route_basis",
    )
    return {key: epoch.get(key) for key in keys}


def _current_configuration_id(state: Mapping[str, object]) -> str | None:
    config = _mapping(state.get("config"))
    recommendation = _mapping(config.get("recommendation"))
    return _text(recommendation.get("effective_current_candidate_id")) or _text(
        recommendation.get("current_default_candidate_id")
    )


def _portfolio_current_configuration_id(
    portfolio: Mapping[str, object] | None,
) -> str | None:
    for decision in _mapping_items(_mapping(portfolio).get("decisions")):
        if current_id := _text(decision.get("current_model_configuration_id")):
            return current_id
    return None


def _model_wait_is_reliable(
    *,
    completed_work_units: int,
    covered_work_units: int,
) -> bool:
    if completed_work_units <= 0 or covered_work_units <= 0:
        return False
    required_samples = min(MIN_MODEL_WAIT_WORK_UNITS, completed_work_units)
    coverage_percent = covered_work_units / completed_work_units * 100
    return (
        covered_work_units >= required_samples
        and coverage_percent >= MIN_MODEL_WAIT_COVERAGE_PERCENT
    )


def _period_days(start: str | None, end: str | None) -> int | None:
    started = _timestamp(start)
    ended = _timestamp(end)
    if started is None or ended is None or ended < started:
        return None
    return max(1, int(round((ended - started).total_seconds() / 86_400)))


def _latest_pricing_snapshot_id(
    epochs: Sequence[Mapping[str, object]],
) -> str | None:
    for epoch in reversed(epochs):
        if snapshot_id := _text(epoch.get("pricing_snapshot_id")):
            return snapshot_id
    return None


def _epochs(payload: Mapping[str, object]) -> list[dict[str, object]]:
    epochs = [
        dict(item)
        for item in payload.get("epochs", [])
        if isinstance(item, Mapping)
    ]
    for epoch in epochs:
        epoch.setdefault("segment_kind", "recommendation")
    return epochs


def _segment_kind(epoch: Mapping[str, object]) -> str:
    return _text(epoch.get("segment_kind")) or "recommendation"


def _assignments(payload: Mapping[str, object]) -> dict[str, str]:
    return {
        str(key): str(value)
        for key, value in _mapping(payload.get("observation_assignments")).items()
    }


def _retained_epochs(
    epochs: Sequence[dict[str, object]],
) -> list[dict[str, object]]:
    active = [
        epoch
        for epoch in epochs
        if epoch.get("lifecycle_status") in {"open", "settling"}
    ]
    active_ids = {str(epoch.get("use_epoch_id")) for epoch in active}
    closed = [
        epoch for epoch in epochs if str(epoch.get("use_epoch_id")) not in active_ids
    ]
    closed_limit = max(0, MAX_RETAINED_EPOCHS - len(active))
    return [*closed[-closed_limit:], *active] if closed_limit else active


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _mapping_items(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _positive_number(value: object) -> float | None:
    number = _number(value)
    return number if number is not None and number > 0 else None


def _integer(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _positive_integer(value: object) -> int | None:
    integer = _integer(value)
    return integer if integer is not None and integer > 0 else None


def _seconds_to_ms(value: object) -> int | None:
    number = _positive_number(value)
    return int(round(number * 1_000)) if number is not None else None


def _complete_reference_cost(row: Mapping[str, object]) -> float | None:
    if row.get("cost_coverage") != "complete":
        return None
    return _positive_number(row.get("estimated_cost_usd"))


def _timestamp(value: object) -> datetime | None:
    text = _text(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(parsed)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
