from __future__ import annotations

import math
from collections.abc import Collection

from .config_store import ConfigStore
from .models import (
    AppConfig,
    ConnectionConfig,
    ModelCandidateConfig,
    ProjectProfileConfig,
)


CONFIG_PATCH_SCHEMA_VERSION = 1
CONFIG_PATCH_OPERATIONS = {
    "model_candidates_enabled",
    "connection_enabled",
    "upsert_endpoint",
    "upsert_endpoint_connection",
    "delete_connection",
    "remove_model_candidates",
    "add_endpoint_models",
    "add_model_candidates",
    "connection_secret_references",
    "add_discovered_local_candidate",
    "current_default",
    "automatic_current_model",
    "recommendation_preference",
    "source_mode",
    "project_task_profile",
    "scan_budget",
    "scan_execution",
    "scheduler",
    "scheduler_enabled",
    "scheduler_mode",
    "daily_schedule",
    "weekly_schedule",
    "scheduled_evaluation_profile",
}


def apply_config_patch(
    store: ConfigStore,
    payload: dict[str, object],
    *,
    valid_evaluation_profile_ids: Collection[str],
) -> tuple[AppConfig, str]:
    if not isinstance(payload, dict):
        raise ValueError("config patch must be an object")
    _expect_fields(
        payload,
        context="config patch",
        expected={"schema_version", "operation", "arguments"},
    )
    schema_version = _integer(payload, "schema_version")
    if schema_version != CONFIG_PATCH_SCHEMA_VERSION:
        raise ValueError(f"unsupported config patch schema: {schema_version}")
    operation = _string(payload, "operation")
    if operation not in CONFIG_PATCH_OPERATIONS:
        raise ValueError(f"unsupported config patch operation: {operation}")
    arguments = payload.get("arguments")
    if not isinstance(arguments, dict):
        raise ValueError("config patch arguments must be an object")
    profile_ids = {
        profile_id.strip()
        for profile_id in valid_evaluation_profile_ids
        if isinstance(profile_id, str) and profile_id.strip()
    }

    def update(config: AppConfig) -> AppConfig:
        _apply_operation(
            config,
            operation=operation,
            arguments=arguments,
            valid_evaluation_profile_ids=profile_ids,
        )
        return config

    return store.update(update), operation


def _apply_operation(
    config: AppConfig,
    *,
    operation: str,
    arguments: dict[str, object],
    valid_evaluation_profile_ids: set[str],
) -> None:
    if operation == "model_candidates_enabled":
        _expect_fields(
            arguments,
            context=operation,
            expected={"connection_id", "candidate_ids", "enabled"},
        )
        connection = _connection(config, _string(arguments, "connection_id"))
        candidate_ids = _string_list(arguments, "candidate_ids")
        enabled = _boolean(arguments, "enabled")
        candidates_by_id = {
            candidate.id: candidate for candidate in connection.model_candidates
        }
        missing = [item for item in candidate_ids if item not in candidates_by_id]
        if missing:
            raise ValueError(
                f"unknown candidate_ids for connection {connection.id}: "
                + ", ".join(missing)
            )
        expanded_verified_scope = False
        for candidate_id in candidate_ids:
            candidate = candidates_by_id[candidate_id]
            expanded_verified_scope = (
                expanded_verified_scope or enabled and not candidate.enabled
            )
            candidate.enabled = enabled
        if expanded_verified_scope and connection.api_format is not None:
            connection.last_test_status = "untested"
            connection.last_test_message = "启用范围已变更，请重新测试"
        return

    if operation == "connection_enabled":
        _expect_fields(
            arguments,
            context=operation,
            expected={"connection_id", "enabled"},
        )
        connection = _connection(config, _string(arguments, "connection_id"))
        connection.enabled = _boolean(arguments, "enabled")
        return

    if operation == "upsert_endpoint":
        _expect_fields(
            arguments,
            context=operation,
            expected={
                "connection_id",
                "name",
                "provider_preset",
                "api_format",
                "base_url",
                "api_key_ref",
                "enabled",
                "model_ids",
                "reasoning_profiles_by_model",
                "default_reasoning_profile_by_model",
                "candidate_enabled",
                "last_test_status",
                "last_test_at",
                "last_test_message",
            },
        )
        connection_id = _string(arguments, "connection_id")
        existing = _optional_connection(config, connection_id)
        model_ids = _string_list(arguments, "model_ids")
        reasoning_profiles = _reasoning_profiles_by_model(
            arguments,
            "reasoning_profiles_by_model",
        )
        default_profiles = _profile_mapping(
            arguments,
            "default_reasoning_profile_by_model",
        )
        _validate_profile_model_ids(
            model_ids,
            reasoning_profiles=reasoning_profiles,
            default_profiles=default_profiles,
        )
        candidates = _endpoint_candidates_from_intent(
            connection_id=connection_id,
            existing_candidates=(
                existing.model_candidates if existing is not None else []
            ),
            model_ids=model_ids,
            reasoning_profiles_by_model=reasoning_profiles,
            default_reasoning_profile_by_model=default_profiles,
            enabled_by_default=_boolean(arguments, "candidate_enabled"),
            preserve_unrequested=True,
        )
        _apply_operation(
            config,
            operation="upsert_endpoint_connection",
            arguments={
                "connection_id": connection_id,
                "name": _string(arguments, "name"),
                "provider_preset": _string(arguments, "provider_preset"),
                "api_format": _string(arguments, "api_format"),
                "base_url": _string(arguments, "base_url"),
                "api_key_ref": _string(arguments, "api_key_ref"),
                "enabled": _boolean(arguments, "enabled"),
                "model_candidates": [
                    _model_candidate_payload(candidate) for candidate in candidates
                ],
                "last_test_status": arguments.get("last_test_status"),
                "last_test_at": arguments.get("last_test_at"),
                "last_test_message": arguments.get("last_test_message"),
            },
            valid_evaluation_profile_ids=valid_evaluation_profile_ids,
        )
        return

    if operation == "upsert_endpoint_connection":
        _expect_fields(
            arguments,
            context=operation,
            expected={
                "connection_id",
                "name",
                "provider_preset",
                "api_format",
                "base_url",
                "api_key_ref",
                "enabled",
                "model_candidates",
                "last_test_status",
                "last_test_at",
                "last_test_message",
            },
        )
        connection_id = _string(arguments, "connection_id")
        existing = _optional_connection(config, connection_id)
        candidates = _model_candidates(
            arguments,
            "model_candidates",
            connection_id=connection_id,
            allow_empty=True,
        )
        _ensure_candidate_ids_available(
            config,
            candidates,
            replacing_connection_id=connection_id,
        )
        updated = ConnectionConfig(
            id=connection_id,
            source_id="custom_endpoint",
            name=_string(arguments, "name"),
            enabled=_boolean(arguments, "enabled"),
            api_format=_string(arguments, "api_format"),
            provider_preset=_string(arguments, "provider_preset"),
            base_url=_string(arguments, "base_url"),
            api_key_ref=_string(arguments, "api_key_ref"),
            notes=existing.notes if existing is not None else None,
            model_candidates=candidates,
        )
        request_identity_changed = existing is not None and (
            existing.provider_preset != updated.provider_preset
            or existing.api_format != updated.api_format
            or existing.base_url != updated.base_url
            or existing.api_key_ref != updated.api_key_ref
            or _enabled_request_identities(existing.model_candidates)
            != _enabled_request_identities(updated.model_candidates)
        )
        explicit_test_status = _optional_string(arguments, "last_test_status")
        if explicit_test_status is not None:
            updated.last_test_status = explicit_test_status
            updated.last_test_at = _optional_string(arguments, "last_test_at")
            updated.last_test_message = _optional_string(
                arguments,
                "last_test_message",
            )
        elif request_identity_changed:
            updated.last_test_status = "untested"
            updated.last_test_message = "连接信息已变更，请重新测试"
        elif existing is not None:
            updated.last_test_status = existing.last_test_status
            updated.last_test_at = existing.last_test_at
            updated.last_test_message = existing.last_test_message
        if existing is None:
            config.model_ingress.connections.append(updated)
        else:
            index = config.model_ingress.connections.index(existing)
            config.model_ingress.connections[index] = updated
        return

    if operation == "delete_connection":
        _expect_fields(
            arguments,
            context=operation,
            expected={"connection_id"},
        )
        connection = _connection(config, _string(arguments, "connection_id"))
        if connection.source_id != "custom_endpoint":
            raise ValueError("only custom endpoint connections can be deleted")
        removed_candidate_ids = {
            candidate.id for candidate in connection.model_candidates
        }
        config.model_ingress.connections.remove(connection)
        _clear_removed_candidate_references(config, removed_candidate_ids)
        return

    if operation == "remove_model_candidates":
        _expect_fields(
            arguments,
            context=operation,
            expected={"connection_id", "candidate_ids"},
        )
        connection = _connection(config, _string(arguments, "connection_id"))
        candidate_ids = set(_string_list(arguments, "candidate_ids"))
        configured_ids = {
            candidate.id for candidate in connection.model_candidates
        }
        missing = sorted(candidate_ids - configured_ids)
        if missing:
            raise ValueError(
                f"unknown candidate_ids for connection {connection.id}: "
                + ", ".join(missing)
            )
        connection.model_candidates = [
            candidate
            for candidate in connection.model_candidates
            if candidate.id not in candidate_ids
        ]
        _clear_removed_candidate_references(config, candidate_ids)
        return

    if operation == "add_endpoint_models":
        _expect_fields(
            arguments,
            context=operation,
            expected={
                "connection_id",
                "model_ids",
                "reasoning_profiles_by_model",
                "default_reasoning_profile_by_model",
                "candidate_enabled",
            },
        )
        connection_id = _string(arguments, "connection_id")
        model_ids = _string_list(arguments, "model_ids")
        reasoning_profiles = _reasoning_profiles_by_model(
            arguments,
            "reasoning_profiles_by_model",
        )
        default_profiles = _profile_mapping(
            arguments,
            "default_reasoning_profile_by_model",
        )
        _validate_profile_model_ids(
            model_ids,
            reasoning_profiles=reasoning_profiles,
            default_profiles=default_profiles,
        )
        candidates = _endpoint_candidates_from_intent(
            connection_id=connection_id,
            existing_candidates=[],
            model_ids=model_ids,
            reasoning_profiles_by_model=reasoning_profiles,
            default_reasoning_profile_by_model=default_profiles,
            enabled_by_default=_boolean(arguments, "candidate_enabled"),
            preserve_unrequested=False,
        )
        _apply_operation(
            config,
            operation="add_model_candidates",
            arguments={
                "connection_id": connection_id,
                "model_candidates": [
                    _model_candidate_payload(candidate) for candidate in candidates
                ],
            },
            valid_evaluation_profile_ids=valid_evaluation_profile_ids,
        )
        return

    if operation == "add_model_candidates":
        _expect_fields(
            arguments,
            context=operation,
            expected={"connection_id", "model_candidates"},
        )
        connection = _connection(config, _string(arguments, "connection_id"))
        if connection.source_id != "custom_endpoint":
            raise ValueError("model candidates can only be added to custom endpoints")
        candidates = _model_candidates(
            arguments,
            "model_candidates",
            connection_id=connection.id,
            allow_empty=False,
        )
        existing_model_ids = {
            candidate.model_id for candidate in connection.model_candidates
        }
        duplicate_models = sorted(
            {candidate.model_id for candidate in candidates} & existing_model_ids
        )
        if duplicate_models:
            raise ValueError(
                "model candidates already exist: " + ", ".join(duplicate_models)
            )
        _ensure_candidate_ids_available(config, candidates)
        connection.model_candidates.extend(candidates)
        return

    if operation == "connection_secret_references":
        _expect_fields(
            arguments,
            context=operation,
            expected={"references_by_connection_id"},
        )
        references = _string_mapping(arguments, "references_by_connection_id")
        for connection_id in references:
            _connection(config, connection_id)
        for connection_id, reference in references.items():
            if reference.startswith("plaintext:"):
                raise ValueError("plaintext API key references are not allowed")
            _connection(config, connection_id).api_key_ref = reference
        return

    if operation == "add_discovered_local_candidate":
        _expect_fields(
            arguments,
            context=operation,
            expected={
                "connection_id",
                "model_id",
                "display_name",
                "scan_profile",
            },
        )
        connection = _connection(config, _string(arguments, "connection_id"))
        source = next(
            (
                source
                for source in config.model_ingress.sources
                if source.id == connection.source_id
            ),
            None,
        )
        if source is None or source.mode != "local":
            raise ValueError(
                f"connection is not a local model source: {connection.id}"
            )
        model_id = _string(arguments, "model_id")
        display_name = _string(arguments, "display_name")
        scan_profile = _string(arguments, "scan_profile")
        if any(
            candidate.model_id == model_id
            and candidate.scan_profile == scan_profile
            for candidate in connection.model_candidates
        ):
            raise ValueError(
                f"candidate already exists for {model_id} / {scan_profile}"
            )
        candidate_id = f"{connection.id}:{model_id}:{scan_profile}"
        if _optional_candidate(config, candidate_id) is not None:
            raise ValueError(f"candidate id already exists: {candidate_id}")
        connection.model_candidates.append(
            ModelCandidateConfig(
                id=candidate_id,
                connection_id=connection.id,
                model_id=model_id,
                display_name=display_name,
                family_id=model_id,
                enabled=False,
                scan_profile=scan_profile,
                capabilities=["reasoning"],
            )
        )
        return

    if operation == "current_default":
        _expect_fields(
            arguments,
            context=operation,
            expected={"candidate_id"},
        )
        candidate_id = _optional_string(arguments, "candidate_id")
        if candidate_id is None:
            config.recommendation.current_default_candidate_id = None
            config.recommendation.current_model_mode = "auto"
            return
        _candidate(config, candidate_id)
        config.recommendation.current_default_candidate_id = candidate_id
        config.recommendation.current_model_mode = "manual"
        return

    if operation == "automatic_current_model":
        _expect_fields(arguments, context=operation, expected=set())
        config.recommendation.current_model_mode = "auto"
        return

    if operation == "recommendation_preference":
        _expect_fields(
            arguments,
            context=operation,
            expected={"preference"},
        )
        preference = _string(arguments, "preference")
        if preference not in {"smart", "quality", "speed", "cost"}:
            raise ValueError(f"unsupported recommendation preference: {preference}")
        config.recommendation.preference = preference
        return

    if operation == "source_mode":
        _expect_fields(
            arguments,
            context=operation,
            expected={"source_mode", "configuration_id"},
        )
        source_mode = _string(arguments, "source_mode")
        if source_mode not in {
            "auto",
            "official_snapshot",
            "local_evaluation",
        }:
            raise ValueError(f"unsupported recommendation source mode: {source_mode}")
        configuration_id = _string(arguments, "configuration_id")
        _candidate(config, configuration_id)
        config.recommendation.source_mode_by_configuration_id[
            configuration_id
        ] = source_mode
        return

    if operation == "project_task_profile":
        _expect_fields(
            arguments,
            context=operation,
            expected={"name", "task_mode"},
        )
        name = _string(arguments, "name", allow_empty=True)
        task_mode = _string(arguments, "task_mode")
        if task_mode not in ProjectProfileConfig.TASK_MODES:
            raise ValueError(f"unsupported project task mode: {task_mode}")
        config.recommendation.project_profile.project_name = name or "当前项目"
        config.recommendation.project_profile.task_mode = task_mode
        return

    if operation == "scan_budget":
        _expect_fields(
            arguments,
            context=operation,
            expected={
                "enabled",
                "max_duration_seconds",
                "max_reference_cost_usd",
            },
        )
        config.scan_budget.enabled = _boolean(arguments, "enabled")
        config.scan_budget.max_duration_seconds = max(
            60,
            _integer(arguments, "max_duration_seconds"),
        )
        config.scan_budget.max_reference_cost_usd = max(
            0.01,
            _number(arguments, "max_reference_cost_usd"),
        )
        return

    if operation == "scan_execution":
        _expect_fields(
            arguments,
            context=operation,
            expected={
                "max_concurrent_targets",
                "execution_timeout_seconds",
                "timeout_retry_count",
            },
        )
        config.system.max_concurrent_targets = max(
            1,
            _integer(arguments, "max_concurrent_targets"),
        )
        config.system.execution_timeout_seconds = max(
            60,
            _integer(arguments, "execution_timeout_seconds"),
        )
        config.system.timeout_retry_count = max(
            0,
            _integer(arguments, "timeout_retry_count"),
        )
        return

    if operation == "scheduler":
        _expect_fields(
            arguments,
            context=operation,
            expected={"mode", "interval_seconds"},
        )
        config.scheduler.mode = _scheduler_mode(arguments, "mode")
        config.scheduler.interval_seconds = max(
            1800,
            _integer(arguments, "interval_seconds"),
        )
        return

    if operation == "scheduler_enabled":
        _expect_fields(arguments, context=operation, expected={"enabled"})
        config.scheduler.enabled = _boolean(arguments, "enabled")
        return

    if operation == "scheduler_mode":
        _expect_fields(arguments, context=operation, expected={"mode"})
        config.scheduler.mode = _scheduler_mode(arguments, "mode")
        return

    if operation == "daily_schedule":
        _expect_fields(
            arguments,
            context=operation,
            expected={"hour", "minute"},
        )
        config.scheduler.mode = "daily"
        config.scheduler.daily_hour = _bounded_integer(
            arguments,
            "hour",
            lower=0,
            upper=23,
        )
        config.scheduler.daily_minute = _bounded_integer(
            arguments,
            "minute",
            lower=0,
            upper=59,
        )
        return

    if operation == "weekly_schedule":
        _expect_fields(
            arguments,
            context=operation,
            expected={"weekday", "hour", "minute"},
        )
        config.scheduler.mode = "weekly"
        config.scheduler.weekly_weekday = _bounded_integer(
            arguments,
            "weekday",
            lower=1,
            upper=7,
        )
        config.scheduler.weekly_hour = _bounded_integer(
            arguments,
            "hour",
            lower=0,
            upper=23,
        )
        config.scheduler.weekly_minute = _bounded_integer(
            arguments,
            "minute",
            lower=0,
            upper=59,
        )
        return

    if operation == "scheduled_evaluation_profile":
        _expect_fields(arguments, context=operation, expected={"profile_id"})
        profile_id = _string(arguments, "profile_id")
        if profile_id not in valid_evaluation_profile_ids:
            raise ValueError(f"unknown evaluation profile id: {profile_id}")
        config.scheduler.scheduled_evaluation_profile_id = profile_id
        return

    raise ValueError(f"unsupported config patch operation: {operation}")


def _expect_fields(
    payload: dict[str, object],
    *,
    context: str,
    expected: set[str],
) -> None:
    missing = sorted(expected - set(payload))
    if missing:
        raise ValueError(f"{context} is missing fields: {', '.join(missing)}")
    unknown = sorted(set(payload) - expected)
    if unknown:
        raise ValueError(f"{context} contains unknown fields: {', '.join(unknown)}")


def _string(
    payload: dict[str, object],
    key: str,
    *,
    allow_empty: bool = False,
) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    normalized = value.strip()
    if not normalized and not allow_empty:
        raise ValueError(f"{key} must not be empty")
    return normalized


def _optional_string(payload: dict[str, object], key: str) -> str | None:
    if payload.get(key) is None:
        return None
    return _string(payload, key)


def _string_list(payload: dict[str, object], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"{key} must be a non-empty string array")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{key} must be a non-empty string array")
        normalized.append(item.strip())
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{key} must not contain duplicates")
    return normalized


def _string_mapping(payload: dict[str, object], key: str) -> dict[str, str]:
    value = payload.get(key)
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{key} must be a non-empty string object")
    normalized: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise ValueError(f"{key} keys must be non-empty strings")
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise ValueError(f"{key} values must be non-empty strings")
        normalized[raw_key.strip()] = raw_value.strip()
    return normalized


def _model_candidates(
    payload: dict[str, object],
    key: str,
    *,
    connection_id: str,
    allow_empty: bool,
) -> list[ModelCandidateConfig]:
    value = payload.get(key)
    if not isinstance(value, list) or (not allow_empty and not value):
        suffix = "an array" if allow_empty else "a non-empty array"
        raise ValueError(f"{key} must be {suffix}")
    candidates: list[ModelCandidateConfig] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"{key}[{index}] must be an object")
        _expect_fields(
            item,
            context=f"{key}[{index}]",
            expected={
                "id",
                "connection_id",
                "model_id",
                "display_name",
                "family_id",
                "variant_id",
                "enabled",
                "scan_profile",
                "capabilities",
            },
        )
        candidate_connection_id = _string(item, "connection_id")
        if candidate_connection_id != connection_id:
            raise ValueError(
                f"{key}[{index}] connection_id must match {connection_id}"
            )
        candidate_id = _string(item, "id")
        if candidate_id in seen_ids:
            raise ValueError(f"duplicate candidate id: {candidate_id}")
        seen_ids.add(candidate_id)
        candidates.append(
            ModelCandidateConfig(
                id=candidate_id,
                connection_id=candidate_connection_id,
                model_id=_string(item, "model_id"),
                display_name=_string(item, "display_name"),
                family_id=_optional_string(item, "family_id"),
                variant_id=_optional_string(item, "variant_id"),
                enabled=_boolean(item, "enabled"),
                scan_profile=_string(item, "scan_profile"),
                capabilities=_string_array(
                    item,
                    "capabilities",
                    allow_empty=True,
                ),
            )
        )
    return candidates


def _reasoning_profiles_by_model(
    payload: dict[str, object],
    key: str,
) -> dict[str, list[str]]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    normalized: dict[str, list[str]] = {}
    for raw_model_id, raw_profiles in value.items():
        if not isinstance(raw_model_id, str) or not raw_model_id.strip():
            raise ValueError(f"{key} keys must be non-empty strings")
        if not isinstance(raw_profiles, list):
            raise ValueError(f"{key} values must be string arrays")
        profiles: list[str] = []
        seen: set[str] = set()
        for raw_profile in raw_profiles:
            if not isinstance(raw_profile, str) or not raw_profile.strip():
                raise ValueError(f"{key} values must be string arrays")
            profile = raw_profile.strip().lower()
            if profile in {"default", "codex_default"} or profile in seen:
                continue
            seen.add(profile)
            profiles.append(profile)
        normalized[raw_model_id.strip()] = profiles
    return normalized


def _profile_mapping(
    payload: dict[str, object],
    key: str,
) -> dict[str, str]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    normalized: dict[str, str] = {}
    for raw_model_id, raw_profile in value.items():
        if not isinstance(raw_model_id, str) or not raw_model_id.strip():
            raise ValueError(f"{key} keys must be non-empty strings")
        if not isinstance(raw_profile, str) or not raw_profile.strip():
            raise ValueError(f"{key} values must be non-empty strings")
        normalized[raw_model_id.strip()] = raw_profile.strip().lower()
    return normalized


def _validate_profile_model_ids(
    model_ids: list[str],
    *,
    reasoning_profiles: dict[str, list[str]],
    default_profiles: dict[str, str],
) -> None:
    unknown = sorted(
        (set(reasoning_profiles) | set(default_profiles)) - set(model_ids)
    )
    if unknown:
        raise ValueError(
            "reasoning profile model ids are not selected: " + ", ".join(unknown)
        )


def _endpoint_candidates_from_intent(
    *,
    connection_id: str,
    existing_candidates: list[ModelCandidateConfig],
    model_ids: list[str],
    reasoning_profiles_by_model: dict[str, list[str]],
    default_reasoning_profile_by_model: dict[str, str],
    enabled_by_default: bool,
    preserve_unrequested: bool,
) -> list[ModelCandidateConfig]:
    requested_model_ids = set(model_ids)
    candidates = (
        [
            ModelCandidateConfig.from_dict(candidate.to_dict())
            for candidate in existing_candidates
            if candidate.model_id not in requested_model_ids
        ]
        if preserve_unrequested
        else []
    )
    for model_id in model_ids:
        existing = [
            candidate
            for candidate in existing_candidates
            if candidate.model_id == model_id
        ]
        if model_id not in reasoning_profiles_by_model and existing:
            candidates.extend(
                ModelCandidateConfig.from_dict(candidate.to_dict())
                for candidate in existing
            )
            continue

        profiles = reasoning_profiles_by_model.get(model_id, [])
        enabled_profiles = {
            candidate.scan_profile.strip().lower()
            for candidate in existing
            if candidate.enabled
        }
        had_enabled_candidate = bool(enabled_profiles)
        if not profiles:
            candidates.append(
                ModelCandidateConfig(
                    id=f"{connection_id}:{model_id}:default",
                    connection_id=connection_id,
                    model_id=model_id,
                    display_name=model_id,
                    enabled=enabled_by_default or had_enabled_candidate,
                    scan_profile="default",
                )
            )
            continue

        preferred = default_reasoning_profile_by_model.get(model_id)
        enabled_profile = (
            preferred
            if preferred in profiles
            else "high"
            if "high" in profiles
            else profiles[0]
        )
        for profile in profiles:
            candidates.append(
                ModelCandidateConfig(
                    id=f"{connection_id}:{model_id}:{profile}",
                    connection_id=connection_id,
                    model_id=model_id,
                    display_name=model_id,
                    family_id=model_id,
                    variant_id=profile,
                    enabled=(
                        profile in enabled_profiles
                        or (enabled_by_default or had_enabled_candidate)
                        and profile == enabled_profile
                    ),
                    scan_profile=profile,
                    capabilities=["reasoning"],
                )
            )
    return candidates


def _model_candidate_payload(
    candidate: ModelCandidateConfig,
) -> dict[str, object]:
    return {
        "id": candidate.id,
        "connection_id": candidate.connection_id,
        "model_id": candidate.model_id,
        "display_name": candidate.display_name,
        "family_id": candidate.family_id,
        "variant_id": candidate.variant_id,
        "enabled": candidate.enabled,
        "scan_profile": candidate.scan_profile,
        "capabilities": list(candidate.capabilities),
    }


def _string_array(
    payload: dict[str, object],
    key: str,
    *,
    allow_empty: bool,
) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list) or (not allow_empty and not value):
        suffix = "an array" if allow_empty else "a non-empty string array"
        raise ValueError(f"{key} must be {suffix}")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{key} must contain only non-empty strings")
        normalized.append(item.strip())
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{key} must not contain duplicates")
    return normalized


def _boolean(payload: dict[str, object], key: str) -> bool:
    value = payload.get(key)
    if type(value) is not bool:
        raise ValueError(f"{key} must be a boolean")
    return value


def _integer(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if type(value) is not int:
        raise ValueError(f"{key} must be an integer")
    return value


def _bounded_integer(
    payload: dict[str, object],
    key: str,
    *,
    lower: int,
    upper: int,
) -> int:
    value = _integer(payload, key)
    if not lower <= value <= upper:
        raise ValueError(f"{key} must be between {lower} and {upper}")
    return value


def _number(payload: dict[str, object], key: str) -> float:
    value = payload.get(key)
    if type(value) not in {int, float} or not math.isfinite(float(value)):
        raise ValueError(f"{key} must be a finite number")
    return float(value)


def _scheduler_mode(payload: dict[str, object], key: str) -> str:
    mode = _string(payload, key)
    if mode not in {"interval", "daily", "weekly"}:
        raise ValueError(f"unsupported scheduler mode: {mode}")
    return mode


def _connection(config: AppConfig, connection_id: str) -> ConnectionConfig:
    connection = _optional_connection(config, connection_id)
    if connection is not None:
        return connection
    raise ValueError(f"unknown connection id: {connection_id}")


def _optional_connection(
    config: AppConfig,
    connection_id: str,
) -> ConnectionConfig | None:
    for connection in config.model_ingress.connections:
        if connection.id == connection_id:
            return connection
    return None


def _enabled_request_identities(
    candidates: list[ModelCandidateConfig],
) -> set[tuple[str, str]]:
    return {
        (candidate.model_id, candidate.scan_profile)
        for candidate in candidates
        if candidate.enabled
    }


def _ensure_candidate_ids_available(
    config: AppConfig,
    candidates: list[ModelCandidateConfig],
    *,
    replacing_connection_id: str | None = None,
) -> None:
    configured_ids = {
        candidate.id
        for connection in config.model_ingress.connections
        if connection.id != replacing_connection_id
        for candidate in connection.model_candidates
    }
    duplicates = sorted(
        candidate.id for candidate in candidates if candidate.id in configured_ids
    )
    if duplicates:
        raise ValueError("candidate ids already exist: " + ", ".join(duplicates))


def _clear_removed_candidate_references(
    config: AppConfig,
    candidate_ids: set[str],
) -> None:
    if config.recommendation.current_default_candidate_id in candidate_ids:
        config.recommendation.current_default_candidate_id = None
        config.recommendation.current_model_mode = "auto"
    for candidate_id in candidate_ids:
        config.recommendation.source_mode_by_configuration_id.pop(
            candidate_id,
            None,
        )


def _optional_candidate(
    config: AppConfig,
    candidate_id: str,
) -> ModelCandidateConfig | None:
    for connection in config.model_ingress.connections:
        for candidate in connection.model_candidates:
            if candidate.id == candidate_id:
                return candidate
    return None


def _candidate(config: AppConfig, candidate_id: str) -> ModelCandidateConfig:
    candidate = _optional_candidate(config, candidate_id)
    if candidate is None:
        raise ValueError(f"unknown candidate id: {candidate_id}")
    return candidate
