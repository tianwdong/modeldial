from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import uuid4

from .provider_catalog import (
    resolve_candidate_catalog_identity,
    resolve_connection_catalog_metadata,
    resolve_model_reasoning_efforts,
)

if TYPE_CHECKING:
    from .question_bank import EvaluationProfileSpec, QuestionSpec


GROK_BUILD_LOCAL_SOURCE_ID = "grok_local"
GROK_BUILD_LOCAL_CONNECTION_ID = "grok-local-default"
GROK_BUILD_4_5_MODEL_ID = "grok-4.5"
GROK_BUILD_4_5_REASONING_EFFORTS = ("low", "medium", "high")
CLAUDE_CODE_LOCAL_SOURCE_ID = "claude_local"
CLAUDE_CODE_LOCAL_CONNECTION_ID = "claude-local-default"
CLAUDE_CODE_SONNET_MODEL_ID = "sonnet"
CLAUDE_CODE_REASONING_EFFORTS = ("low", "medium", "high")
ANTHROPIC_MESSAGES_REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max")
KIMI_K3_MODEL_ID = "k3"
KIMI_K3_REASONING_EFFORTS = ("low", "high", "max")
REASONING_TOKENS_UNAVAILABLE_CAPABILITY = "reasoning_tokens_unavailable"
ADVISOR_PREFERENCES = {"smart", "quality", "speed", "cost"}
ADVISOR_SOURCE_MODES = {"auto", "official_snapshot", "local_evaluation"}


@dataclass
class TargetConfig:
    model: str
    effort: str
    enabled: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "effort": self.effort,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "TargetConfig":
        return cls(
            model=str(payload["model"]),
            effort=str(payload["effort"]),
            enabled=bool(payload.get("enabled", True)),
        )


@dataclass
class ResolvedScanTarget:
    candidate_id: str
    source_id: str
    connection_id: str
    model_id: str
    scan_profile: str
    display_name: str
    display_model_id: str | None = None
    display_scan_profile: str | None = None
    connection_mode: str = "local"
    api_format: str | None = None
    provider_preset: str = "generic"
    base_url: str | None = None
    api_key_ref: str | None = None
    reasoning_tokens_supported: bool = True

    @property
    def model(self) -> str:
        return self.model_id

    @property
    def effort(self) -> str:
        return self.scan_profile

    @property
    def display_model(self) -> str:
        return self.display_model_id or self.model_id

    @property
    def display_effort(self) -> str:
        return self.display_scan_profile or self.scan_profile

    @property
    def display_label(self) -> str:
        return f"{self.display_model} / {self.display_effort}"

    @property
    def label(self) -> str:
        return f"{self.model_id} / {self.scan_profile}"


@dataclass(frozen=True)
class ScanPlan:
    run_id: str
    force_restart: bool
    total_targets: int
    completed_targets: int
    selection_mode: str
    custom_round_mode: str
    execution_selection_mode: str
    execution_custom_round_mode: str
    evaluation_profile_id: str
    evaluation_profile_label: str
    evaluation_result_level: str
    question_count: int
    upgrade_from_run_id: str | None
    requested_candidate_ids: tuple[str, ...] | None
    effective_requested_candidate_ids: tuple[str, ...]
    regular_candidate_ids: tuple[str, ...]
    config: AppConfig = field(repr=False, compare=False)
    history: tuple[ScanResult, ...] = field(repr=False, compare=False)
    comparison_targets: tuple[ResolvedScanTarget, ...] = field(
        repr=False,
        compare=False,
    )
    enabled_targets: tuple[ResolvedScanTarget, ...] = field(
        repr=False,
        compare=False,
    )
    evaluation_profile: EvaluationProfileSpec = field(repr=False, compare=False)
    enabled_questions: tuple[QuestionSpec, ...] = field(repr=False, compare=False)
    question_ids: tuple[str, ...]
    attempts_per_target: int
    resume: dict[str, object] | None = field(repr=False, compare=False)
    run_metadata: dict[str, object] = field(repr=False, compare=False)


@dataclass
class SourceConfig:
    id: str
    kind: str
    title: str
    description: str
    mode: str
    enabled: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "description": self.description,
            "mode": self.mode,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "SourceConfig":
        return cls(
            id=str(payload["id"]),
            kind=str(payload["kind"]),
            title=str(payload["title"]),
            description=str(payload["description"]),
            mode=str(payload["mode"]),
            enabled=bool(payload.get("enabled", True)),
        )


@dataclass
class ModelCandidateConfig:
    id: str
    connection_id: str
    model_id: str
    display_name: str
    family_id: str | None = None
    variant_id: str | None = None
    enabled: bool = True
    scan_profile: str = "codex_default"
    capabilities: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.id,
            "connection_id": self.connection_id,
            "model_id": self.model_id,
            "display_name": self.display_name,
            "enabled": self.enabled,
            "scan_profile": self.scan_profile,
            "capabilities": list(self.capabilities),
        }
        if self.family_id is not None:
            payload["family_id"] = self.family_id
        if self.variant_id is not None:
            payload["variant_id"] = self.variant_id
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "ModelCandidateConfig":
        return cls(
            id=str(payload["id"]),
            connection_id=str(payload["connection_id"]),
            model_id=str(payload["model_id"]),
            display_name=str(payload["display_name"]),
            family_id=_to_optional_str(payload.get("family_id")),
            variant_id=_to_optional_str(payload.get("variant_id")),
            enabled=bool(payload.get("enabled", True)),
            scan_profile=str(payload.get("scan_profile", "codex_default")),
            capabilities=[
                str(item)
                for item in payload.get("capabilities", [])  # type: ignore[arg-type]
            ],
        )


def _grok_build_4_5_candidate(
    connection_id: str,
    scan_profile: str,
    *,
    enabled: bool,
) -> ModelCandidateConfig:
    return ModelCandidateConfig(
        id=f"{connection_id}:{GROK_BUILD_4_5_MODEL_ID}:{scan_profile}",
        connection_id=connection_id,
        model_id=GROK_BUILD_4_5_MODEL_ID,
        display_name=f"Grok 4.5 {scan_profile.title()}",
        family_id=GROK_BUILD_4_5_MODEL_ID,
        enabled=enabled,
        scan_profile=scan_profile,
    )


def _claude_code_sonnet_candidate(
    connection_id: str,
    scan_profile: str,
    *,
    enabled: bool,
) -> ModelCandidateConfig:
    return ModelCandidateConfig(
        id=f"{connection_id}:{CLAUDE_CODE_SONNET_MODEL_ID}:{scan_profile}",
        connection_id=connection_id,
        model_id=CLAUDE_CODE_SONNET_MODEL_ID,
        display_name=f"Claude Sonnet {scan_profile.title()}",
        family_id=CLAUDE_CODE_SONNET_MODEL_ID,
        enabled=enabled,
        scan_profile=scan_profile,
        capabilities=[REASONING_TOKENS_UNAVAILABLE_CAPABILITY],
    )


def _api_reasoning_candidate(
    connection_id: str,
    model_id: str,
    scan_profile: str,
    *,
    enabled: bool,
    reasoning_tokens_supported: bool,
) -> ModelCandidateConfig:
    capabilities = ["reasoning"]
    if not reasoning_tokens_supported:
        capabilities.append(REASONING_TOKENS_UNAVAILABLE_CAPABILITY)
    return ModelCandidateConfig(
        id=f"{connection_id}:{model_id}:{scan_profile}",
        connection_id=connection_id,
        model_id=model_id,
        display_name=f"{model_id} {scan_profile}",
        family_id=model_id,
        enabled=enabled,
        scan_profile=scan_profile,
        capabilities=capabilities,
    )


@dataclass
class ConnectionConfig:
    id: str
    source_id: str
    name: str
    enabled: bool
    api_format: str | None = None
    provider_preset: str = "generic"
    provider_id: str | None = None
    provider_display_name: str | None = None
    auth_mode: str | None = None
    catalog_source: str | None = None
    base_url: str | None = None
    api_key_ref: str | None = None
    notes: str | None = None
    last_test_status: str | None = None
    last_test_at: str | None = None
    last_test_message: str | None = None
    local_login_verified: bool = False
    model_candidates: list[ModelCandidateConfig] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.source_id == "openrouter_api":
            self.source_id = "custom_endpoint"
            self.provider_preset = "openrouter"
        format_aliases = {
            "openai_chat": "openai_chat_completions",
            "openai_chat_completions": "openai_chat_completions",
            "openai_responses": "openai_responses",
            "anthropic_messages": "anthropic_messages",
        }
        if self.api_format is not None:
            if self.api_format not in format_aliases:
                raise ValueError(f"unsupported api_format: {self.api_format}")
            self.api_format = format_aliases[self.api_format]
        if self.provider_preset not in {"generic", "openrouter", "custom"}:
            raise ValueError(f"unsupported provider_preset: {self.provider_preset}")
        if self.base_url is not None:
            self.base_url = self.base_url.rstrip("/")
        if self.api_key_ref and self.api_key_ref.startswith("plaintext:"):
            raise ValueError("plaintext API key references are not allowed")
        _apply_connection_catalog_identity(self)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.id,
            "source_id": self.source_id,
            "name": self.name,
            "enabled": self.enabled,
            "api_format": self.api_format,
            "provider_preset": self.provider_preset,
            "base_url": self.base_url,
            "api_key_ref": self.api_key_ref,
            "notes": self.notes,
            "last_test_status": self.last_test_status,
            "last_test_at": self.last_test_at,
            "last_test_message": self.last_test_message,
            "local_login_verified": self.local_login_verified,
            "model_candidates": [
                candidate.to_dict() for candidate in self.model_candidates
            ],
        }
        if self.provider_id is not None:
            payload["provider_id"] = self.provider_id
        if self.provider_display_name is not None:
            payload["provider_display_name"] = self.provider_display_name
        if self.auth_mode is not None:
            payload["auth_mode"] = self.auth_mode
        if self.catalog_source is not None:
            payload["catalog_source"] = self.catalog_source
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "ConnectionConfig":
        return cls(
            id=str(payload["id"]),
            source_id=str(payload["source_id"]),
            name=str(payload["name"]),
            enabled=bool(payload.get("enabled", True)),
            api_format=_to_optional_str(payload.get("api_format")),
            provider_preset=str(
                payload.get(
                    "provider_preset",
                    "openrouter"
                    if str(payload.get("source_id")) == "openrouter_api"
                    else "generic",
                )
            ),
            provider_id=_to_optional_str(payload.get("provider_id")),
            provider_display_name=_to_optional_str(payload.get("provider_display_name")),
            auth_mode=_to_optional_str(payload.get("auth_mode")),
            catalog_source=_to_optional_str(payload.get("catalog_source")),
            base_url=_to_optional_str(payload.get("base_url")),
            api_key_ref=_to_optional_str(payload.get("api_key_ref")),
            notes=_to_optional_str(payload.get("notes")),
            last_test_status=_to_optional_str(payload.get("last_test_status")),
            last_test_at=_to_optional_str(payload.get("last_test_at")),
            last_test_message=_to_optional_str(payload.get("last_test_message")),
            local_login_verified=bool(payload.get("local_login_verified", False)),
            model_candidates=[
                ModelCandidateConfig.from_dict(item)
                for item in payload.get("model_candidates", [])  # type: ignore[arg-type]
            ],
        )


@dataclass
class ModelIngressConfig:
    sources: list[SourceConfig]
    connections: list[ConnectionConfig]

    def local_model_candidates(self) -> list[ModelCandidateConfig]:
        local_source_ids = {source.id for source in self.sources if source.mode == "local"}
        return [
            candidate
            for connection in self.connections
            if connection.source_id in local_source_ids
            for candidate in connection.model_candidates
        ]

    def api_connections(self) -> list[ConnectionConfig]:
        api_source_ids = {source.id for source in self.sources if source.mode == "api"}
        return [
            connection
            for connection in self.connections
            if connection.source_id in api_source_ids
        ]

    def to_dict(self) -> dict[str, object]:
        return {
            "sources": [source.to_dict() for source in self.sources],
            "connections": [
                connection.to_dict() for connection in self.connections
            ],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "ModelIngressConfig":
        raw_connections = payload.get("connections", [])  # type: ignore[arg-type]
        connections = [ConnectionConfig.from_dict(item) for item in raw_connections]
        legacy_candidates = payload.get("model_candidates")
        if legacy_candidates is not None:
            candidates_by_connection: dict[str, list[ModelCandidateConfig]] = {}
            for item in legacy_candidates:  # type: ignore[assignment]
                candidate = ModelCandidateConfig.from_dict(item)
                candidates_by_connection.setdefault(candidate.connection_id, []).append(
                    candidate
                )
            for connection in connections:
                if candidates_by_connection.get(connection.id):
                    connection.model_candidates = candidates_by_connection[connection.id]
        sources = [
            SourceConfig.from_dict(item)
            for item in payload.get("sources", [])  # type: ignore[arg-type]
            if str(item.get("id")) != "openrouter_api"
        ]
        if (
            any(connection.source_id == "custom_endpoint" for connection in connections)
            and not any(source.id == "custom_endpoint" for source in sources)
        ):
            sources.append(
                SourceConfig(
                    id="custom_endpoint",
                    kind="custom_endpoint",
                    title="自定义 endpoint",
                    description="添加 OpenAI-compatible API 连接。",
                    mode="api",
                )
            )
        api_source_ids = {source.id for source in sources if source.mode == "api"}
        claude_source_ids = {
            source.id for source in sources if source.kind == "claude_code"
        }
        for connection in connections:
            if connection.source_id in api_source_ids:
                connection.model_candidates = _collapse_legacy_api_effort_candidates(
                    connection
                )
                _normalize_api_reasoning_candidates(connection)
                _enrich_api_candidate_identity(connection)
            if connection.source_id in claude_source_ids:
                connection.model_candidates = _normalize_single_variant_profiles(
                    connection.model_candidates
                )
        connections = _merge_duplicate_api_connections(connections, api_source_ids)
        for connection in connections:
            _apply_connection_catalog_identity(connection)
        return cls(sources=sources, connections=connections)


def _collapse_legacy_api_effort_candidates(
    connection: ConnectionConfig,
) -> list[ModelCandidateConfig]:
    candidates_by_model: dict[str, list[ModelCandidateConfig]] = {}
    ordered_model_ids: list[str] = []
    for candidate in connection.model_candidates:
        if candidate.model_id not in candidates_by_model:
            ordered_model_ids.append(candidate.model_id)
        candidates_by_model.setdefault(candidate.model_id, []).append(candidate)

    normalized: list[ModelCandidateConfig] = []
    for model_id in ordered_model_ids:
        candidates = candidates_by_model[model_id]
        is_legacy_auto_expansion = (
            {candidate.scan_profile for candidate in candidates}
            == {"medium", "high", "xhigh"}
            and len(candidates) == 3
            and all(candidate.capabilities == ["reasoning"] for candidate in candidates)
            and all(candidate.variant_id != candidate.scan_profile for candidate in candidates)
        )
        if not is_legacy_auto_expansion:
            normalized.extend(candidates)
            continue
        normalized.append(
            ModelCandidateConfig(
                id=f"{connection.id}:{model_id}:default",
                connection_id=connection.id,
                model_id=model_id,
                display_name=model_id,
                enabled=any(candidate.enabled for candidate in candidates),
                scan_profile="default",
                capabilities=[],
            )
        )
    return normalized


def _enrich_api_candidate_identity(connection: ConnectionConfig) -> None:
    _apply_connection_catalog_identity(connection)


def _api_reasoning_efforts(
    connection: ConnectionConfig,
    model_id: str,
) -> tuple[str, ...]:
    if connection.api_format == "anthropic_messages":
        return ANTHROPIC_MESSAGES_REASONING_EFFORTS
    if connection.provider_id == "moonshot" and model_id == KIMI_K3_MODEL_ID:
        return KIMI_K3_REASONING_EFFORTS
    return resolve_model_reasoning_efforts(
        model_id=model_id,
        provider_id=connection.provider_id,
        base_url=connection.base_url,
    )


def _normalize_api_reasoning_candidates(
    connection: ConnectionConfig,
) -> None:
    candidates_by_model: dict[str, list[ModelCandidateConfig]] = {}
    ordered_model_ids: list[str] = []
    for candidate in connection.model_candidates:
        if candidate.model_id not in candidates_by_model:
            ordered_model_ids.append(candidate.model_id)
        candidates_by_model.setdefault(candidate.model_id, []).append(candidate)

    normalized: list[ModelCandidateConfig] = []
    for model_id in ordered_model_ids:
        supported_efforts = _api_reasoning_efforts(connection, model_id)
        if not supported_efforts:
            normalized.extend(candidates_by_model[model_id])
            continue
        current_profiles = {
            candidate.scan_profile.strip().lower()
            for candidate in candidates_by_model[model_id]
        }
        legacy_profiles = (
            connection.api_format == "anthropic_messages"
            and current_profiles == {"low", "medium", "high", "max"}
            and all(
                candidate.variant_id != candidate.scan_profile
                for candidate in candidates_by_model[model_id]
            )
        ) or (
            connection.provider_id == "moonshot"
            and model_id == KIMI_K3_MODEL_ID
            and current_profiles == {"low", "medium", "high", "xhigh", "max"}
            and all(
                candidate.variant_id != candidate.scan_profile
                for candidate in candidates_by_model[model_id]
            )
        )
        has_manual_profiles = any(
            profile not in {"default", "codex_default"}
            for profile in current_profiles
        )
        if has_manual_profiles and not legacy_profiles:
            normalized.extend(candidates_by_model[model_id])
            continue
        reasoning_tokens_supported = connection.api_format != "anthropic_messages"
        existing_by_profile: dict[str, ModelCandidateConfig] = {}
        legacy_candidates: list[ModelCandidateConfig] = []
        for candidate in candidates_by_model[model_id]:
            profile = candidate.scan_profile.strip().lower()
            if (
                profile in supported_efforts
                and profile not in existing_by_profile
            ):
                existing_by_profile[profile] = candidate
            else:
                legacy_candidates.append(candidate)

        legacy_enabled = any(candidate.enabled for candidate in legacy_candidates)
        for profile in supported_efforts:
            candidate = existing_by_profile.get(profile)
            if candidate is None:
                candidate = _api_reasoning_candidate(
                    connection.id,
                    model_id,
                    profile,
                    enabled=profile == "high" and legacy_enabled,
                    reasoning_tokens_supported=reasoning_tokens_supported,
                )
            else:
                candidate.id = f"{connection.id}:{model_id}:{profile}"
                candidate.connection_id = connection.id
                candidate.model_id = model_id
                candidate.family_id = model_id
                candidate.scan_profile = profile
                if "reasoning" not in candidate.capabilities:
                    candidate.capabilities.append("reasoning")
                if (
                    not reasoning_tokens_supported
                    and REASONING_TOKENS_UNAVAILABLE_CAPABILITY
                    not in candidate.capabilities
                ):
                    candidate.capabilities.append(
                        REASONING_TOKENS_UNAVAILABLE_CAPABILITY
                    )
                if reasoning_tokens_supported:
                    candidate.capabilities = [
                        capability
                        for capability in candidate.capabilities
                        if capability != REASONING_TOKENS_UNAVAILABLE_CAPABILITY
                    ]
            if profile == "high" and legacy_enabled:
                candidate.enabled = True
            normalized.append(candidate)
    connection.model_candidates = normalized


def _merge_duplicate_api_connections(
    connections: list[ConnectionConfig],
    api_source_ids: set[str],
) -> list[ConnectionConfig]:
    merged: list[ConnectionConfig] = []
    connection_by_endpoint: dict[tuple[str, str, str, str], ConnectionConfig] = {}
    for connection in connections:
        if connection.source_id not in api_source_ids:
            merged.append(connection)
            continue
        key = (
            (connection.provider_id or connection.name).strip().casefold(),
            (connection.base_url or "").rstrip("/").casefold(),
            connection.api_format or "",
            connection.provider_preset or "",
        )
        existing = connection_by_endpoint.get(key)
        if existing is None:
            connection_by_endpoint[key] = connection
            merged.append(connection)
            continue
        existing_model_ids = {
            candidate.model_id for candidate in existing.model_candidates
        }
        new_model_ids = {
            candidate.model_id for candidate in connection.model_candidates
        } - existing_model_ids
        for candidate in connection.model_candidates:
            if candidate.model_id not in new_model_ids:
                continue
            candidate.connection_id = existing.id
            candidate.id = f"{existing.id}:{candidate.model_id}:{candidate.scan_profile}"
            existing.model_candidates.append(candidate)
        existing_model_ids.update(new_model_ids)
        existing.enabled = existing.enabled or connection.enabled
        if existing.api_key_ref is None:
            existing.api_key_ref = connection.api_key_ref
    return merged


def _apply_connection_catalog_identity(connection: ConnectionConfig) -> None:
    metadata = resolve_connection_catalog_metadata(
        source_id=connection.source_id,
        name=connection.name,
        base_url=connection.base_url,
        provider_preset=connection.provider_preset,
        explicit_provider_id=connection.provider_id,
        explicit_provider_display_name=connection.provider_display_name,
        explicit_auth_mode=connection.auth_mode,
        explicit_catalog_source=connection.catalog_source,
    )
    connection.provider_id = metadata.provider_id
    connection.provider_display_name = metadata.provider_display_name
    connection.auth_mode = metadata.auth_mode
    connection.catalog_source = metadata.catalog_source
    for candidate in connection.model_candidates:
        resolved_identity = resolve_candidate_catalog_identity(
            model_id=candidate.model_id,
            provider_id=connection.provider_id,
        )
        if resolved_identity.family_id is not None and (
            candidate.family_id is None or candidate.family_id == candidate.model_id
        ):
            candidate.family_id = resolved_identity.family_id
        if resolved_identity.variant_id is not None and candidate.variant_id is None:
            candidate.variant_id = resolved_identity.variant_id


def _normalize_single_variant_profiles(
    candidates: list[ModelCandidateConfig],
) -> list[ModelCandidateConfig]:
    model_counts: dict[str, int] = {}
    for candidate in candidates:
        model_counts[candidate.model_id] = model_counts.get(candidate.model_id, 0) + 1
    for candidate in candidates:
        if model_counts[candidate.model_id] == 1:
            candidate.scan_profile = "default"
    return candidates


def _normalize_grok_build_4_5_candidates(
    connection: ConnectionConfig,
) -> None:
    if (
        connection.source_id != GROK_BUILD_LOCAL_SOURCE_ID
        or connection.id != GROK_BUILD_LOCAL_CONNECTION_ID
    ):
        return

    configured_by_profile: dict[str, ModelCandidateConfig] = {}
    legacy_candidates: list[ModelCandidateConfig] = []
    other_candidates: list[ModelCandidateConfig] = []
    for candidate in connection.model_candidates:
        if candidate.model_id != GROK_BUILD_4_5_MODEL_ID:
            other_candidates.append(candidate)
            continue
        profile = candidate.scan_profile.strip().lower()
        if (
            profile in GROK_BUILD_4_5_REASONING_EFFORTS
            and profile not in configured_by_profile
        ):
            configured_by_profile[profile] = candidate
            continue
        legacy_candidates.append(candidate)

    legacy_enabled = any(candidate.enabled for candidate in legacy_candidates)
    normalized: list[ModelCandidateConfig] = []
    for profile in GROK_BUILD_4_5_REASONING_EFFORTS:
        candidate = configured_by_profile.get(profile)
        if candidate is None:
            if profile == "high" and legacy_candidates:
                candidate = legacy_candidates[0]
                candidate.connection_id = connection.id
                candidate.model_id = GROK_BUILD_4_5_MODEL_ID
                candidate.display_name = f"Grok 4.5 {profile.title()}"
                candidate.family_id = GROK_BUILD_4_5_MODEL_ID
                candidate.scan_profile = profile
            else:
                candidate = _grok_build_4_5_candidate(
                    connection.id,
                    profile,
                    enabled=False,
                )
        if profile == "high":
            candidate.enabled = candidate.enabled or legacy_enabled
        normalized.append(candidate)
    connection.model_candidates = [*normalized, *other_candidates]


def _normalize_claude_code_sonnet_candidates(
    connection: ConnectionConfig,
) -> None:
    if (
        connection.source_id != CLAUDE_CODE_LOCAL_SOURCE_ID
        or connection.id != CLAUDE_CODE_LOCAL_CONNECTION_ID
    ):
        return

    configured_by_profile: dict[str, ModelCandidateConfig] = {}
    legacy_candidates: list[ModelCandidateConfig] = []
    for candidate in connection.model_candidates:
        profile = candidate.scan_profile.strip().lower()
        if (
            candidate.model_id == CLAUDE_CODE_SONNET_MODEL_ID
            and profile in CLAUDE_CODE_REASONING_EFFORTS
            and profile not in configured_by_profile
        ):
            configured_by_profile[profile] = candidate
        else:
            legacy_candidates.append(candidate)

    legacy_enabled = any(candidate.enabled for candidate in legacy_candidates)
    normalized: list[ModelCandidateConfig] = []
    for profile in CLAUDE_CODE_REASONING_EFFORTS:
        candidate = configured_by_profile.get(profile)
        if candidate is None:
            if profile == "high" and legacy_candidates:
                candidate = legacy_candidates[0]
                candidate.connection_id = connection.id
                candidate.model_id = CLAUDE_CODE_SONNET_MODEL_ID
                candidate.display_name = f"Claude Sonnet {profile.title()}"
                candidate.family_id = CLAUDE_CODE_SONNET_MODEL_ID
                candidate.scan_profile = profile
            else:
                candidate = _claude_code_sonnet_candidate(
                    connection.id,
                    profile,
                    enabled=False,
                )
        if REASONING_TOKENS_UNAVAILABLE_CAPABILITY not in candidate.capabilities:
            candidate.capabilities.append(REASONING_TOKENS_UNAVAILABLE_CAPABILITY)
        if profile == "high":
            candidate.enabled = candidate.enabled or legacy_enabled
        normalized.append(candidate)
    connection.model_candidates = normalized


@dataclass
class RuleConfig:
    enabled: bool = True
    action: str = "warn"
    max_retries: int = 0
    cooldown_seconds: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "action": self.action,
            "max_retries": self.max_retries,
            "cooldown_seconds": self.cooldown_seconds,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "RuleConfig":
        return cls(
            enabled=bool(payload.get("enabled", True)),
            action=str(payload.get("action", "warn")),
            max_retries=int(payload.get("max_retries", 0)),
            cooldown_seconds=int(payload.get("cooldown_seconds", 0)),
        )


@dataclass
class SchedulerConfig:
    enabled: bool = False
    mode: str = "daily"
    interval_seconds: int = 1800
    daily_hour: int = 9
    daily_minute: int = 0
    weekly_weekday: int = 1
    weekly_hour: int = 9
    weekly_minute: int = 0
    scheduled_evaluation_profile_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "interval_seconds": max(1800, self.interval_seconds),
            "daily_hour": self.daily_hour,
            "daily_minute": self.daily_minute,
            "weekly_weekday": self.weekly_weekday,
            "weekly_hour": self.weekly_hour,
            "weekly_minute": self.weekly_minute,
            "scheduled_evaluation_profile_id": self.scheduled_evaluation_profile_id,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "SchedulerConfig":
        raw_mode = str(payload.get("mode", "manual"))
        enabled = bool(payload.get("enabled", raw_mode != "manual"))
        mode = raw_mode if raw_mode in {"interval", "daily", "weekly"} else "daily"
        return cls(
            enabled=enabled,
            mode=mode,
            interval_seconds=max(1800, int(payload.get("interval_seconds", 1800))),
            daily_hour=int(payload.get("daily_hour", 9)),
            daily_minute=int(payload.get("daily_minute", 0)),
            weekly_weekday=int(payload.get("weekly_weekday", 1)),
            weekly_hour=int(payload.get("weekly_hour", 9)),
            weekly_minute=int(payload.get("weekly_minute", 0)),
            scheduled_evaluation_profile_id=(
                str(payload["scheduled_evaluation_profile_id"]).strip() or None
                if payload.get("scheduled_evaluation_profile_id") is not None
                else None
            ),
        )


@dataclass
class ScanBudgetConfig:
    enabled: bool = False
    max_duration_seconds: int = 900
    max_reference_cost_usd: float = 1.0

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "max_duration_seconds": max(60, self.max_duration_seconds),
            "max_reference_cost_usd": max(0.01, self.max_reference_cost_usd),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object] | None) -> "ScanBudgetConfig":
        payload = payload or {}
        return cls(
            enabled=bool(payload.get("enabled", False)),
            max_duration_seconds=max(
                60, int(payload.get("max_duration_seconds", 900))
            ),
            max_reference_cost_usd=max(
                0.01, float(payload.get("max_reference_cost_usd", 1.0))
            ),
        )


@dataclass
class SystemConfig:
    use_mock_results: bool = False
    auto_open_browser: bool = True
    history_limit: int = 50
    language: str = "zh-CN"
    attempts_per_target: int = 3
    max_concurrent_targets: int = 1
    max_concurrent_targets_by_connection: dict[str, int] = field(
        default_factory=dict
    )
    execution_timeout_seconds: int = 1200
    timeout_retry_count: int = 0

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "use_mock_results": self.use_mock_results,
            "auto_open_browser": self.auto_open_browser,
            "history_limit": self.history_limit,
            "language": self.language,
            "attempts_per_target": max(1, self.attempts_per_target),
            "max_concurrent_targets": max(1, self.max_concurrent_targets),
            "execution_timeout_seconds": max(60, self.execution_timeout_seconds),
            "timeout_retry_count": max(0, self.timeout_retry_count),
        }
        if self.max_concurrent_targets_by_connection:
            payload["max_concurrent_targets_by_connection"] = {
                connection_id: max(1, int(limit))
                for connection_id, limit in sorted(
                    self.max_concurrent_targets_by_connection.items()
                )
                if connection_id
            }
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "SystemConfig":
        raw_connection_limits = payload.get(
            "max_concurrent_targets_by_connection",
            {},
        )
        connection_limits = (
            {
                str(connection_id): max(1, int(limit))
                for connection_id, limit in raw_connection_limits.items()
                if str(connection_id)
            }
            if isinstance(raw_connection_limits, dict)
            else {}
        )
        return cls(
            use_mock_results=bool(payload.get("use_mock_results", True)),
            auto_open_browser=bool(payload.get("auto_open_browser", True)),
            history_limit=int(payload.get("history_limit", 50)),
            language=str(payload.get("language", "zh-CN")),
            attempts_per_target=max(1, int(payload.get("attempts_per_target", 3))),
            max_concurrent_targets=max(
                1, int(payload.get("max_concurrent_targets", 1))
            ),
            max_concurrent_targets_by_connection=connection_limits,
            execution_timeout_seconds=max(
                60, int(payload.get("execution_timeout_seconds", 1200))
            ),
            timeout_retry_count=max(
                0, int(payload.get("timeout_retry_count", 0))
            ),
        )


@dataclass
class ProjectProfileConfig:
    project_name: str = "当前项目"
    task_mode: str = "综合推荐"

    TASK_MODES = {
        "综合推荐",
        "开发实现",
        "调试修复",
        "测试验证",
        "重构维护",
        "代码评审",
    }
    LEGACY_TASK_MODE_ALIASES = {
        "功能开发": "开发实现",
        "修 Bug": "调试修复",
        "写测试": "测试验证",
        "数据处理": "综合推荐",
    }

    def to_dict(self) -> dict[str, object]:
        return {
            "project_name": self.project_name,
            "task_mode": self.task_mode,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object] | None) -> "ProjectProfileConfig":
        payload = payload or {}
        project_name = str(payload.get("project_name", "当前项目")).strip() or "当前项目"
        raw_task_mode = str(payload.get("task_mode", "综合推荐"))
        task_mode = cls.LEGACY_TASK_MODE_ALIASES.get(raw_task_mode, raw_task_mode)
        return cls(
            project_name=project_name,
            task_mode=task_mode if task_mode in cls.TASK_MODES else "综合推荐",
        )


@dataclass
class RecommendationConfig:
    current_default_candidate_id: str | None = None
    current_model_mode: str = "auto"
    preference: str = "smart"
    source_mode_by_configuration_id: dict[str, str] = field(default_factory=dict)
    project_profile: ProjectProfileConfig = field(default_factory=ProjectProfileConfig)

    def to_dict(self) -> dict[str, object]:
        return {
            "current_default_candidate_id": self.current_default_candidate_id,
            "current_model_mode": self.current_model_mode,
            "preference": self.preference,
            "source_mode_by_configuration_id": dict(
                self.source_mode_by_configuration_id
            ),
            "project_profile": self.project_profile.to_dict(),
        }

    @classmethod
    def from_dict(
        cls, payload: dict[str, object] | None
    ) -> "RecommendationConfig":
        payload = payload or {}
        current_model_mode = str(payload.get("current_model_mode", "auto"))
        preference = str(payload.get("preference", "smart"))
        raw_source_modes = payload.get("source_mode_by_configuration_id")
        source_modes = {
            configuration_id.strip(): source_mode
            for raw_configuration_id, raw_source_mode in (
                raw_source_modes.items()
                if isinstance(raw_source_modes, dict)
                else ()
            )
            if isinstance(raw_configuration_id, str)
            if (configuration_id := raw_configuration_id.strip())
            if isinstance(raw_source_mode, str)
            if (source_mode := raw_source_mode.strip()) in ADVISOR_SOURCE_MODES
        }
        return cls(
            current_default_candidate_id=_to_optional_str(
                payload.get("current_default_candidate_id")
            ),
            current_model_mode=(
                current_model_mode
                if current_model_mode in {"auto", "manual"}
                else "auto"
            ),
            preference=(
                preference if preference in ADVISOR_PREFERENCES else "smart"
            ),
            source_mode_by_configuration_id=source_modes,
            project_profile=ProjectProfileConfig.from_dict(
                payload.get("project_profile")
                if isinstance(payload.get("project_profile"), dict)
                else None
            ),
        )


@dataclass
class AppConfig:
    model_ingress: ModelIngressConfig
    recommendation: RecommendationConfig
    scheduler: SchedulerConfig
    scan_budget: ScanBudgetConfig
    system: SystemConfig
    rules: dict[str, RuleConfig]

    @classmethod
    def default(cls) -> "AppConfig":
        return cls(
            model_ingress=ModelIngressConfig(
                sources=[
                    SourceConfig(
                        id="codex_local",
                        kind="codex",
                        title="Codex",
                        description="复用本机 ChatGPT / Codex 登录态。",
                        mode="local",
                    ),
                    SourceConfig(
                        id=CLAUDE_CODE_LOCAL_SOURCE_ID,
                        kind="claude_code",
                        title="Claude Code",
                        description="复用本机 Claude Code 登录态。",
                        mode="local",
                        enabled=False,
                    ),
                    SourceConfig(
                        id=GROK_BUILD_LOCAL_SOURCE_ID,
                        kind="grok_build",
                        title="Grok Build",
                        description="复用本机 Grok Build 登录态。",
                        mode="local",
                        enabled=False,
                    ),
                    SourceConfig(
                        id="custom_endpoint",
                        kind="custom_endpoint",
                        title="自定义 endpoint",
                        description="添加 OpenAI-compatible API 连接。",
                        mode="api",
                    ),
                ],
                connections=[
                    ConnectionConfig(
                        id="codex-local-default",
                        source_id="codex_local",
                        name="Codex Local",
                        enabled=True,
                        model_candidates=[
                            ModelCandidateConfig(
                                id="codex-local-default:gpt-5.4:medium",
                                connection_id="codex-local-default",
                                model_id="gpt-5.4",
                                display_name="GPT-5.4 Medium",
                                family_id="gpt-5.4",
                                scan_profile="medium",
                                enabled=True,
                            ),
                            ModelCandidateConfig(
                                id="codex-local-default:gpt-5.4:high",
                                connection_id="codex-local-default",
                                model_id="gpt-5.4",
                                display_name="GPT-5.4 High",
                                family_id="gpt-5.4",
                                scan_profile="high",
                                enabled=True,
                            ),
                            ModelCandidateConfig(
                                id="codex-local-default:gpt-5.4:xhigh",
                                connection_id="codex-local-default",
                                model_id="gpt-5.4",
                                display_name="GPT-5.4 XHigh",
                                family_id="gpt-5.4",
                                scan_profile="xhigh",
                                enabled=True,
                            ),
                            ModelCandidateConfig(
                                id="codex-local-default:gpt-5.5:medium",
                                connection_id="codex-local-default",
                                model_id="gpt-5.5",
                                display_name="GPT-5.5 Medium",
                                family_id="gpt-5.5",
                                scan_profile="medium",
                                enabled=True,
                            ),
                            ModelCandidateConfig(
                                id="codex-local-default:gpt-5.5:high",
                                connection_id="codex-local-default",
                                model_id="gpt-5.5",
                                display_name="GPT-5.5 High",
                                family_id="gpt-5.5",
                                scan_profile="high",
                                enabled=True,
                            ),
                            ModelCandidateConfig(
                                id="codex-local-default:gpt-5.5:xhigh",
                                connection_id="codex-local-default",
                                model_id="gpt-5.5",
                                display_name="GPT-5.5 XHigh",
                                family_id="gpt-5.5",
                                scan_profile="xhigh",
                                enabled=True,
                            ),
                            *[
                                ModelCandidateConfig(
                                    id=f"codex-local-default:{model_id}:{scan_profile}",
                                    connection_id="codex-local-default",
                                    model_id=model_id,
                                    display_name=f"{display_name} {profile_label}",
                                    family_id=model_id,
                                    scan_profile=scan_profile,
                                    enabled=False,
                                )
                                for model_id, display_name in (
                                    ("gpt-5.6-sol", "GPT-5.6 Sol"),
                                    ("gpt-5.6-luna", "GPT-5.6 Luna"),
                                    ("gpt-5.6-terra", "GPT-5.6 Terra"),
                                )
                                for scan_profile, profile_label in (
                                    ("medium", "Medium"),
                                    ("high", "High"),
                                    ("xhigh", "XHigh"),
                                )
                            ],
                        ],
                    ),
                    ConnectionConfig(
                        id=CLAUDE_CODE_LOCAL_CONNECTION_ID,
                        source_id=CLAUDE_CODE_LOCAL_SOURCE_ID,
                        name="Claude Code Local",
                        enabled=False,
                        model_candidates=[
                            _claude_code_sonnet_candidate(
                                CLAUDE_CODE_LOCAL_CONNECTION_ID,
                                scan_profile,
                                enabled=False,
                            )
                            for scan_profile in CLAUDE_CODE_REASONING_EFFORTS
                        ],
                    ),
                    ConnectionConfig(
                        id=GROK_BUILD_LOCAL_CONNECTION_ID,
                        source_id=GROK_BUILD_LOCAL_SOURCE_ID,
                        name="Grok Build Local",
                        enabled=False,
                        model_candidates=[
                            _grok_build_4_5_candidate(
                                GROK_BUILD_LOCAL_CONNECTION_ID,
                                scan_profile,
                                enabled=False,
                            )
                            for scan_profile in GROK_BUILD_4_5_REASONING_EFFORTS
                        ],
                    ),
                ],
            ),
            recommendation=RecommendationConfig(),
            scheduler=SchedulerConfig(),
            scan_budget=ScanBudgetConfig(),
            system=SystemConfig(),
            rules={
                "reason_tok_516": RuleConfig(action="retry", max_retries=1),
                "wrong_answer": RuleConfig(action="warn", max_retries=0),
                "missing_usage": RuleConfig(action="retry", max_retries=1),
                "timeout": RuleConfig(action="warn", max_retries=0),
                "slow_response": RuleConfig(action="warn", max_retries=0),
            },
        )

    @classmethod
    def first_run(cls) -> "AppConfig":
        config = cls.default()
        codex_source = next(
            source
            for source in config.model_ingress.sources
            if source.id == "codex_local"
        )
        codex_connection = next(
            connection
            for connection in config.model_ingress.connections
            if connection.id == "codex-local-default"
        )
        codex_source.enabled = False
        codex_connection.enabled = False
        for candidate in codex_connection.model_candidates:
            candidate.enabled = False
        return config

    def to_dict(self) -> dict[str, object]:
        return {
            "model_ingress": self.model_ingress.to_dict(),
            "recommendation": self.recommendation.to_dict(),
            "scheduler": self.scheduler.to_dict(),
            "scan_budget": self.scan_budget.to_dict(),
            "system": self.system.to_dict(),
            "rules": {name: rule.to_dict() for name, rule in self.rules.items()},
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "AppConfig":
        defaults = cls.default()
        rules_payload = payload.get("rules", {})
        model_ingress_payload = payload.get(
            "model_ingress", defaults.model_ingress.to_dict()
        )
        model_ingress = ModelIngressConfig.from_dict(
            model_ingress_payload  # type: ignore[arg-type]
        )
        has_existing_local_source = any(
            source.mode == "local" for source in model_ingress.sources
        )
        existing_source_ids = {source.id for source in model_ingress.sources}
        for default_source in defaults.model_ingress.sources:
            if (
                default_source.id == GROK_BUILD_LOCAL_SOURCE_ID
                and has_existing_local_source
                and default_source.id not in existing_source_ids
            ):
                model_ingress.sources.append(
                    SourceConfig.from_dict(default_source.to_dict())
                )
        existing_connection_ids = {
            connection.id for connection in model_ingress.connections
        }
        for default_connection in defaults.model_ingress.connections:
            if (
                default_connection.id == GROK_BUILD_LOCAL_CONNECTION_ID
                and has_existing_local_source
                and default_connection.id not in existing_connection_ids
            ):
                model_ingress.connections.append(
                    ConnectionConfig.from_dict(default_connection.to_dict())
                )
        connections_by_id = {
            connection.id: connection for connection in model_ingress.connections
        }
        for default_connection in defaults.model_ingress.connections:
            connection = connections_by_id.get(default_connection.id)
            if connection is None:
                continue
            existing_candidate_ids = {
                candidate.id for candidate in connection.model_candidates
            }
            connection.model_candidates.extend(
                ModelCandidateConfig.from_dict(candidate.to_dict())
                for candidate in default_connection.model_candidates
                if candidate.model_id.startswith("gpt-5.6-")
                and candidate.id not in existing_candidate_ids
            )
        source_by_id = {source.id: source for source in model_ingress.sources}
        for connection in model_ingress.connections:
            _normalize_grok_build_4_5_candidates(connection)
            _normalize_claude_code_sonnet_candidates(connection)
            source = source_by_id.get(connection.source_id)
            if (
                source is not None
                and source.kind in {"claude_code", "grok_build"}
                and not connection.local_login_verified
            ):
                source.enabled = False
                connection.enabled = False
                if source.kind == "claude_code":
                    for candidate in connection.model_candidates:
                        candidate.enabled = False
        recommendation = RecommendationConfig.from_dict(
            payload.get("recommendation")  # type: ignore[arg-type]
        )
        return cls(
            model_ingress=model_ingress,
            recommendation=recommendation,
            scheduler=SchedulerConfig.from_dict(
                payload.get("scheduler", defaults.scheduler.to_dict())  # type: ignore[arg-type]
            ),
            scan_budget=ScanBudgetConfig.from_dict(
                payload.get("scan_budget")  # type: ignore[arg-type]
                if isinstance(payload.get("scan_budget"), dict)
                else None
            ),
            system=SystemConfig.from_dict(
                payload.get("system", defaults.system.to_dict())  # type: ignore[arg-type]
            ),
            rules={
                name: RuleConfig.from_dict(rule_payload)
                for name, rule_payload in {
                    **defaults.to_dict()["rules"],  # type: ignore[arg-type]
                    **rules_payload,  # type: ignore[arg-type]
                }.items()
            },
        )


@dataclass
class ScanResult:
    model: str
    effort: str
    started_at: str
    elapsed_seconds: float
    source_mode: str
    answer_ok: bool
    answer_preview: str
    input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    reasoning_tokens_supported: bool = True
    cached_input_tokens: int | None = None
    cache_write_input_tokens: int | None = None
    reference_cost_usd: float | None = None
    cost_status: str = "unavailable"
    pricing_snapshot: str | None = None
    candidate_id: str | None = None
    run_id: str = field(default_factory=lambda: uuid4().hex)
    phase: str = "legacy"
    question_id: str = "logic"
    question_title: str | None = None
    capability_id: str | None = None
    capability_label: str | None = None
    detail_label: str | None = None
    grader_kind: str | None = None
    attempt_index: int = 1
    error_message: str | None = None
    scorer_reason: str | None = None
    scorer_diagnostics: dict[str, object] = field(default_factory=dict)
    expected_summary: str | None = None
    actual_summary: str | None = None
    retry_index: int = 0
    flags: list[str] = field(default_factory=list)
    final_status: str = "pass"
    evaluation_id: str | None = None
    execution_trace: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "phase": self.phase,
            "candidate_id": self.candidate_id,
            "model": self.model,
            "effort": self.effort,
            "question_id": self.question_id,
            "question_title": self.question_title,
            "capability_id": self.capability_id,
            "capability_label": self.capability_label,
            "detail_label": self.detail_label,
            "grader_kind": self.grader_kind,
            "attempt_index": self.attempt_index,
            "started_at": self.started_at,
            "elapsed_seconds": self.elapsed_seconds,
            "source_mode": self.source_mode,
            "answer_ok": self.answer_ok,
            "answer_preview": self.answer_preview,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_write_input_tokens": self.cache_write_input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "reasoning_tokens_supported": self.reasoning_tokens_supported,
            "reference_cost_usd": self.reference_cost_usd,
            "cost_status": self.cost_status,
            "pricing_snapshot": self.pricing_snapshot,
            "error_message": self.error_message,
            "scorer_reason": self.scorer_reason,
            "scorer_diagnostics": dict(self.scorer_diagnostics),
            "expected_summary": self.expected_summary,
            "actual_summary": self.actual_summary,
            "retry_index": self.retry_index,
            "flags": list(self.flags),
            "final_status": self.final_status,
            "evaluation_id": self.evaluation_id,
            "execution_trace": dict(self.execution_trace),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "ScanResult":
        return cls(
            run_id=str(payload.get("run_id", uuid4().hex)),
            phase=str(payload.get("phase", "legacy")),
            candidate_id=_to_optional_str(payload.get("candidate_id")),
            model=str(payload["model"]),
            effort=str(payload["effort"]),
            question_id=str(payload.get("question_id", "logic")),
            question_title=_to_optional_str(payload.get("question_title")),
            capability_id=_to_optional_str(payload.get("capability_id")),
            capability_label=_to_optional_str(payload.get("capability_label")),
            detail_label=_to_optional_str(payload.get("detail_label")),
            grader_kind=_to_optional_str(payload.get("grader_kind")),
            attempt_index=int(payload.get("attempt_index", 1)),
            started_at=str(payload["started_at"]),
            elapsed_seconds=float(payload["elapsed_seconds"]),
            source_mode=str(payload.get("source_mode", "unknown")),
            answer_ok=bool(payload["answer_ok"]),
            answer_preview=str(payload["answer_preview"]),
            input_tokens=_to_optional_int(payload.get("input_tokens")),
            cached_input_tokens=_to_optional_int(payload.get("cached_input_tokens")),
            cache_write_input_tokens=_to_optional_int(
                payload.get("cache_write_input_tokens")
            ),
            output_tokens=_to_optional_int(payload.get("output_tokens")),
            reasoning_tokens=_to_optional_int(payload.get("reasoning_tokens")),
            reasoning_tokens_supported=bool(
                payload.get("reasoning_tokens_supported", True)
            ),
            reference_cost_usd=_to_optional_float(payload.get("reference_cost_usd")),
            cost_status=str(payload.get("cost_status", "unavailable")),
            pricing_snapshot=_to_optional_str(payload.get("pricing_snapshot")),
            error_message=_to_optional_str(payload.get("error_message")),
            scorer_reason=_to_optional_str(payload.get("scorer_reason")),
            scorer_diagnostics=_to_dict(payload.get("scorer_diagnostics")),
            expected_summary=_to_optional_str(payload.get("expected_summary")),
            actual_summary=_to_optional_str(payload.get("actual_summary")),
            retry_index=int(payload.get("retry_index", 0)),
            flags=[str(item) for item in payload.get("flags", [])],  # type: ignore[arg-type]
            final_status=str(payload.get("final_status", "pass")),
            evaluation_id=_to_optional_str(payload.get("evaluation_id")),
            execution_trace=_to_dict(payload.get("execution_trace")),
        )


@dataclass
class RunMetadata:
    run_id: str
    question_pack_id: str
    question_pack_version: str
    started_at: str | None
    completed_at: str | None
    candidate_count: int
    question_count: int
    status: str
    evaluation_profile_id: str = "legacy_full"
    evaluation_profile_label: str = "完整评测"
    evaluation_result_level: str = "unknown"
    evaluation_score_max: int = 0
    question_ids: list[str] = field(default_factory=list)
    upgrade_from_run_id: str | None = None
    upgrade_target_profile_id: str | None = None
    selection_mode: str = "regular"
    requested_candidate_ids: list[str] = field(default_factory=list)
    regular_candidate_ids: list[str] = field(default_factory=list)
    comparison_group_id: str | None = None
    comparison_group_mode: str = "regular"
    comparison_parent_run_id: str | None = None
    append_target_group_id: str | None = None
    appended_candidate_ids: list[str] = field(default_factory=list)
    skipped_candidate_ids: list[str] = field(default_factory=list)
    aggregate_wall_clock_seconds: int | None = None
    is_complete_regular_round: bool = False
    scoring_mode: str = "semantic_q1_q5_equal_v2"

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "question_pack_id": self.question_pack_id,
            "question_pack_version": self.question_pack_version,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "candidate_count": self.candidate_count,
            "question_count": self.question_count,
            "status": self.status,
            "evaluation_profile_id": self.evaluation_profile_id,
            "evaluation_profile_label": self.evaluation_profile_label,
            "evaluation_result_level": self.evaluation_result_level,
            "evaluation_score_max": self.evaluation_score_max,
            "question_ids": list(self.question_ids),
            "upgrade_from_run_id": self.upgrade_from_run_id,
            "upgrade_target_profile_id": self.upgrade_target_profile_id,
            "selection_mode": self.selection_mode,
            "requested_candidate_ids": list(self.requested_candidate_ids),
            "regular_candidate_ids": list(self.regular_candidate_ids),
            "comparison_group_id": self.comparison_group_id,
            "comparison_group_mode": self.comparison_group_mode,
            "comparison_parent_run_id": self.comparison_parent_run_id,
            "append_target_group_id": self.append_target_group_id,
            "appended_candidate_ids": list(self.appended_candidate_ids),
            "skipped_candidate_ids": list(self.skipped_candidate_ids),
            "aggregate_wall_clock_seconds": self.aggregate_wall_clock_seconds,
            "is_complete_regular_round": self.is_complete_regular_round,
            "scoring_mode": self.scoring_mode,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "RunMetadata":
        return cls(
            run_id=str(payload.get("run_id", "unknown")),
            question_pack_id=str(payload.get("question_pack_id", "unknown")),
            question_pack_version=str(payload.get("question_pack_version", "unknown")),
            started_at=_to_optional_str(payload.get("started_at")),
            completed_at=_to_optional_str(payload.get("completed_at")),
            candidate_count=int(payload.get("candidate_count", 0)),
            question_count=int(payload.get("question_count", 0)),
            status=str(payload.get("status", "legacy")),
            evaluation_profile_id=str(
                payload.get("evaluation_profile_id", "legacy_full")
            ),
            evaluation_profile_label=str(
                payload.get("evaluation_profile_label", "完整评测")
            ),
            evaluation_result_level=str(
                payload.get("evaluation_result_level", "unknown")
            ),
            evaluation_score_max=int(payload.get("evaluation_score_max", 0)),
            question_ids=[
                str(item) for item in payload.get("question_ids", [])  # type: ignore[arg-type]
            ],
            upgrade_from_run_id=_to_optional_str(payload.get("upgrade_from_run_id")),
            upgrade_target_profile_id=_to_optional_str(
                payload.get("upgrade_target_profile_id")
            ),
            selection_mode=str(payload.get("selection_mode", "regular")),
            requested_candidate_ids=[
                str(item) for item in payload.get("requested_candidate_ids", [])  # type: ignore[arg-type]
            ],
            regular_candidate_ids=[
                str(item) for item in payload.get("regular_candidate_ids", [])  # type: ignore[arg-type]
            ],
            comparison_group_id=_to_optional_str(payload.get("comparison_group_id")),
            comparison_group_mode=str(payload.get("comparison_group_mode", "regular")),
            comparison_parent_run_id=_to_optional_str(payload.get("comparison_parent_run_id")),
            append_target_group_id=_to_optional_str(payload.get("append_target_group_id")),
            appended_candidate_ids=[
                str(item) for item in payload.get("appended_candidate_ids", [])  # type: ignore[arg-type]
            ],
            skipped_candidate_ids=[
                str(item) for item in payload.get("skipped_candidate_ids", [])  # type: ignore[arg-type]
            ],
            aggregate_wall_clock_seconds=_to_optional_int(payload.get("aggregate_wall_clock_seconds")),
            is_complete_regular_round=bool(payload.get("is_complete_regular_round", False)),
            scoring_mode=str(payload.get("scoring_mode", "legacy")),
        )

    @classmethod
    def legacy(cls, *, run_id: str | None, question_count: int = 0) -> "RunMetadata":
        return cls(
            run_id=run_id or "unknown",
            question_pack_id="unknown",
            question_pack_version="unknown",
            started_at=None,
            completed_at=None,
            candidate_count=0,
            question_count=question_count,
            status="legacy",
            comparison_group_id=run_id or "unknown",
            scoring_mode="legacy",
        )


@dataclass
class RuleEvaluation:
    flags: list[str]
    action: str
    should_retry: bool
    max_retries: int
    final_status: str


def _to_optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _to_optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _to_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _to_optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
