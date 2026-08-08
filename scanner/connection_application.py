from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .codex_model_catalog import (
    CodexCatalogCandidate,
    CodexCatalogError,
    discover_codex_model_catalog,
)
from .config_store import ConfigStore
from .endpoint_client import (
    DiscoveredModel,
    EndpointError,
    discover_model_catalog,
    run_endpoint_request,
)
from .models import ConnectionConfig, ResolvedScanTarget
from .secret_store import SecretStore, SecretStoreError


@dataclass(frozen=True)
class ConnectionQuery:
    store: ConfigStore

    def discover_local_models(
        self,
        provider_id: str,
        *,
        discoverer=discover_codex_model_catalog,
    ) -> dict[str, object]:
        if provider_id != "codex":
            return {
                "ok": False,
                "provider_id": provider_id,
                "connection_id": None,
                "candidates": [],
                "message": "该本机来源暂不支持模型目录发现",
            }
        config = self.store.load()
        connection = next(
            (
                item
                for item in config.model_ingress.connections
                if item.id == "codex-local-default"
            ),
            None,
        )
        if connection is None:
            raise ValueError("local Codex connection is missing")
        try:
            catalog_candidates = discoverer()
        except CodexCatalogError as exc:
            return {
                "ok": False,
                "provider_id": provider_id,
                "connection_id": connection.id,
                "candidates": [],
                "message": str(exc),
            }
        configured = {
            (candidate.model_id, candidate.scan_profile)
            for candidate in connection.model_candidates
        }
        candidates = [
            _local_catalog_candidate_payload(candidate, configured)
            for candidate in catalog_candidates
        ]
        new_count = sum(
            not bool(candidate["configured"]) for candidate in candidates
        )
        return {
            "ok": True,
            "provider_id": provider_id,
            "connection_id": connection.id,
            "candidates": candidates,
            "message": (
                f"已从 Codex 服务端发现 {len(candidates)} 个模型档位，"
                f"其中 {new_count} 个可加入"
            ),
        }

    def discover_models(
        self,
        connection_id: str,
        *,
        secret_store: SecretStore | None = None,
        discoverer=discover_model_catalog,
    ) -> dict[str, object]:
        connection = _connection_by_id(self.store, connection_id)
        secrets = secret_store or SecretStore()
        try:
            api_key = secrets.resolve(connection.api_key_ref)
            discovered = discoverer(
                str(connection.base_url or ""),
                api_key,
                api_format=str(
                    connection.api_format or "openai_chat_completions"
                ),
            )
        except SecretStoreError:
            return _discovery_failure(
                "authentication_failed",
                "API Key 不可用",
                include_configured=True,
            )
        except EndpointError as exc:
            return _discovery_failure(
                exc.category,
                _safe_endpoint_message(exc.category),
                include_configured=True,
            )
        models, reasoning_profiles, default_reasoning_profiles = (
            _normalize_discovered_models(discovered)
        )
        configured_model_ids = {
            candidate.model_id for candidate in connection.model_candidates
        }
        configured_models = [
            model_id for model_id in models if model_id in configured_model_ids
        ]
        new_models = [
            model_id for model_id in models if model_id not in configured_model_ids
        ]
        return {
            "ok": True,
            "models": models,
            "reasoning_profiles_by_model": reasoning_profiles,
            "default_reasoning_profile_by_model": default_reasoning_profiles,
            "new_models": new_models,
            "configured_models": configured_models,
            "error_category": None,
            "message": (
                f"模型发现成功：新增 {len(new_models)} 个，"
                f"已配置 {len(configured_models)} 个"
            ),
            "manual_entry_allowed": True,
        }


class ConnectionProbe:
    @staticmethod
    def test(
        *,
        base_url: str,
        api_format: str,
        provider_preset: str,
        model_id: str,
        api_key: str,
        scan_profile: str = "default",
        requester=run_endpoint_request,
    ) -> dict[str, object]:
        target = ResolvedScanTarget(
            candidate_id=f"endpoint-preview:{model_id}:{scan_profile}",
            source_id="custom_endpoint",
            connection_id="endpoint-preview",
            model_id=model_id,
            scan_profile=scan_profile,
            display_name=model_id,
            connection_mode="api",
            api_format=api_format,
            provider_preset=provider_preset,
            base_url=base_url,
            api_key_ref=None,
        )
        try:
            requester(target, "Reply with only OK.", api_key)
        except EndpointError as exc:
            return {
                "ok": False,
                "status": exc.category,
                "error_category": exc.category,
                "message": _safe_endpoint_message(exc.category),
                "tested_at": _timestamp(),
            }
        return {
            "ok": True,
            "status": "ok",
            "error_category": None,
            "message": "连接成功",
            "tested_at": _timestamp(),
        }

    @staticmethod
    def discover(
        *,
        base_url: str,
        api_format: str,
        api_key: str,
        discoverer=discover_model_catalog,
    ) -> dict[str, object]:
        try:
            discovered = discoverer(base_url, api_key, api_format=api_format)
            models, reasoning_profiles, default_reasoning_profiles = (
                _normalize_discovered_models(discovered)
            )
        except EndpointError as exc:
            return _discovery_failure(
                exc.category,
                _safe_endpoint_message(exc.category),
                include_configured=False,
            )
        return {
            "ok": True,
            "models": models,
            "reasoning_profiles_by_model": reasoning_profiles,
            "default_reasoning_profile_by_model": default_reasoning_profiles,
            "error_category": None,
            "message": f"发现 {len(models)} 个模型",
            "manual_entry_allowed": True,
        }


def _local_catalog_candidate_payload(
    candidate: CodexCatalogCandidate,
    configured: set[tuple[str, str]],
) -> dict[str, object]:
    key = (candidate.model_id, candidate.scan_profile)
    return {
        "id": f"{candidate.model_id}:{candidate.scan_profile}",
        "model_id": candidate.model_id,
        "model_display_name": candidate.model_display_name,
        "display_name": (
            f"{candidate.model_display_name} [{candidate.scan_profile}]"
        ),
        "scan_profile": candidate.scan_profile,
        "is_default": candidate.is_default,
        "configured": key in configured,
    }


def _connection_by_id(store: ConfigStore, connection_id: str) -> ConnectionConfig:
    connection = next(
        (
            item
            for item in store.load().model_ingress.connections
            if item.id == connection_id
        ),
        None,
    )
    if connection is None:
        raise ValueError("unknown connection_id")
    if not connection.base_url or not connection.api_format:
        raise ValueError("connection is not configured")
    return connection


def _normalize_discovered_models(
    discovered: list[str] | list[DiscoveredModel],
) -> tuple[list[str], dict[str, list[str]], dict[str, str]]:
    model_ids: set[str] = set()
    reasoning_profiles: dict[str, list[str]] = {}
    default_reasoning_profiles: dict[str, str] = {}
    for item in discovered:
        if isinstance(item, DiscoveredModel):
            model_ids.add(item.model_id)
            if item.reasoning_efforts:
                reasoning_profiles[item.model_id] = list(
                    item.reasoning_efforts
                )
            if item.default_reasoning_effort:
                default_reasoning_profiles[item.model_id] = (
                    item.default_reasoning_effort
                )
        else:
            model_ids.add(str(item))
    return sorted(model_ids), reasoning_profiles, default_reasoning_profiles


def _discovery_failure(
    category: str,
    message: str,
    *,
    include_configured: bool,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": False,
        "models": [],
        "reasoning_profiles_by_model": {},
        "default_reasoning_profile_by_model": {},
        "error_category": category,
        "message": message,
        "manual_entry_allowed": True,
    }
    if include_configured:
        payload["new_models"] = []
        payload["configured_models"] = []
    return payload


def _safe_endpoint_message(category: str) -> str:
    return {
        "authentication_failed": "认证失败",
        "model_not_found": "模型不存在或无权限",
        "rate_limited": "请求受到限流",
        "timeout": "连接超时",
        "protocol_mismatch": "API 协议不匹配",
        "invalid_response": "响应格式无效",
        "network_error": "网络连接失败",
        "server_error": "服务端错误",
    }.get(category, "连接失败")


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
