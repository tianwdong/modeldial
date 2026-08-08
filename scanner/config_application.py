from __future__ import annotations

from collections.abc import Callable, Collection, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from .active_run_store import ActiveRunStore
from .claude_code_client import ClaudeCodeError, check_claude_code_login
from .config_commands import apply_config_patch
from .config_store import ConfigStore
from .endpoint_client import EndpointError, run_endpoint_request
from .grok_build_client import GrokBuildError, check_grok_build_login
from .local_provider_detection import detected_local_provider_payload
from .models import AppConfig, ResolvedScanTarget
from .process_lock import exclusive_system_process_lock, scan_lock_is_active
from .secret_store import SecretStore, SecretStoreError
from .service import MonitorService


class ConfigMutationConflict(ValueError):
    pass


@dataclass(frozen=True)
class ConfigCommand:
    store: ConfigStore
    active_run_store: ActiveRunStore | None = None

    def replace_legacy_config(self, payload: dict[str, object]) -> AppConfig:
        """Single backend compatibility adapter for the retired whole-config API."""
        return self.store.save(AppConfig.from_dict(payload))

    def apply_patch(
        self,
        payload: dict[str, object],
        *,
        valid_evaluation_profile_ids: Collection[str],
    ) -> tuple[AppConfig, str]:
        operation = (
            str(payload.get("operation") or "")
            if isinstance(payload, dict)
            else ""
        )
        if operation in {"delete_connection", "remove_model_candidates"}:
            with self._quiescent_runtime(operation):
                return apply_config_patch(
                    self.store,
                    payload,
                    valid_evaluation_profile_ids=valid_evaluation_profile_ids,
                )
        return apply_config_patch(
            self.store,
            payload,
            valid_evaluation_profile_ids=valid_evaluation_profile_ids,
        )

    def migrate_secret_references(
        self,
        payload: dict[str, object],
    ) -> dict[str, object]:
        if (
            not isinstance(payload, dict)
            or payload.get("operation") != "connection_secret_references"
        ):
            raise ValueError(
                "secret reference migration only accepts "
                "connection_secret_references"
            )
        _, operation = self.apply_patch(
            payload,
            valid_evaluation_profile_ids=set(),
        )
        return {
            "schema_version": 1,
            "ok": True,
            "action": "migrate_secret_references",
            "operation": operation,
        }

    def upsert_endpoint(
        self,
        payload: dict[str, object],
    ) -> tuple[AppConfig, str]:
        return self._apply_endpoint_command("upsert_endpoint", payload)

    def add_endpoint_models(
        self,
        payload: dict[str, object],
    ) -> tuple[AppConfig, str]:
        return self._apply_endpoint_command("add_endpoint_models", payload)

    def _apply_endpoint_command(
        self,
        operation: str,
        payload: dict[str, object],
    ) -> tuple[AppConfig, str]:
        if not isinstance(payload, dict):
            raise ValueError("endpoint command payload must be an object")
        arguments = dict(payload)
        schema_version = arguments.pop("schema_version", None)
        return self.apply_patch(
            {
                "schema_version": schema_version,
                "operation": operation,
                "arguments": arguments,
            },
            valid_evaluation_profile_ids=set(),
        )

    @contextmanager
    def _quiescent_runtime(self, operation: str) -> Iterator[None]:
        if self.active_run_store is None:
            yield
            return
        scan_lock_path = self.active_run_store.path.with_name("scan.lock")
        guard_path = scan_lock_path.with_name(f"{scan_lock_path.name}.guard")
        with exclusive_system_process_lock(guard_path) as acquired:
            if not acquired or scan_lock_is_active(scan_lock_path):
                raise ConfigMutationConflict(
                    f"{operation} is unavailable while an active or resumable run exists"
                )
            active_run = self.active_run_store.load()
            if active_run is not None:
                runtime = active_run.get("runtime")
                lifecycle_state = (
                    str(runtime.get("lifecycle_state") or "unknown")
                    if isinstance(runtime, dict)
                    else "unknown"
                )
                if lifecycle_state != "idle":
                    raise ConfigMutationConflict(
                        f"{operation} is unavailable while an active or resumable run exists "
                        f"({lifecycle_state})"
                    )
            yield

    def import_local_provider(
        self,
        provider_id: str,
        *,
        grok_login_checker: Callable[[], None] | None = None,
        claude_login_checker: Callable[[], None] | None = None,
        local_provider_detector: Callable[[], list[dict[str, object]]] | None = None,
    ) -> dict[str, object]:
        identities = {
            "codex": ("codex_local", "codex-local-default"),
            "claude": ("claude_local", "claude-local-default"),
            "grok": ("grok_local", "grok-local-default"),
        }
        identity = identities.get(provider_id)
        if identity is None:
            return {
                "ok": False,
                "provider_id": provider_id,
                "message": "该本机来源的执行适配器尚未接入",
            }
        if provider_id == "codex":
            providers = (
                local_provider_detector or detected_local_provider_payload
            )()
            provider = next(
                (
                    item
                    for item in providers
                    if item.get("provider_id") == "codex"
                ),
                None,
            )
            if provider is None or not bool(provider.get("importable")):
                return {
                    "ok": False,
                    "provider_id": provider_id,
                    "source_id": identity[0],
                    "connection_id": identity[1],
                    "error_category": "local_login_unavailable",
                    "message": "未同时检测到 Codex CLI 与本机登录态",
                }
        if provider_id == "grok":
            try:
                (grok_login_checker or check_grok_build_login)()
            except GrokBuildError as exc:
                return {
                    "ok": False,
                    "provider_id": provider_id,
                    "error_category": exc.category,
                    "message": str(exc),
                }
        if provider_id == "claude":
            try:
                (claude_login_checker or check_claude_code_login)()
            except ClaudeCodeError as exc:
                return {
                    "ok": False,
                    "provider_id": provider_id,
                    "error_category": exc.category,
                    "message": str(exc),
                }

        source_id, connection_id = identity

        def enable(config):
            source = next(
                (
                    item
                    for item in config.model_ingress.sources
                    if item.id == source_id
                ),
                None,
            )
            connection = next(
                (
                    item
                    for item in config.model_ingress.connections
                    if item.id == connection_id
                ),
                None,
            )
            if source is None or connection is None:
                raise ValueError("local provider defaults are missing")
            source.enabled = True
            connection.enabled = True
            if provider_id == "claude":
                connection.local_login_verified = True
            return config

        self.store.update(enable)
        return {
            "ok": True,
            "provider_id": provider_id,
            "source_id": source_id,
            "connection_id": connection_id,
            "message": (
                "已复用本机 Grok Build 登录态"
                if provider_id == "grok"
                else "已复用本机 Claude Code 登录态"
                if provider_id == "claude"
                else "已复用本机 Codex 登录态"
            ),
        }

    def verify_endpoint_connection(
        self,
        connection_id: str,
        model_id: str,
        *,
        secret_store: SecretStore | None = None,
        requester=run_endpoint_request,
    ) -> dict[str, object]:
        config = self.store.load()
        connection = next(
            (
                item
                for item in config.model_ingress.connections
                if item.id == connection_id
            ),
            None,
        )
        if connection is None:
            raise ValueError("unknown connection_id")
        enabled_candidates = [
            candidate
            for candidate in connection.model_candidates
            if candidate.enabled
        ]
        targets = [
            ResolvedScanTarget(
                candidate_id=candidate.id,
                source_id=connection.source_id,
                connection_id=connection.id,
                model_id=candidate.model_id,
                scan_profile=candidate.scan_profile,
                display_name=candidate.display_name,
                connection_mode="api",
                api_format=connection.api_format,
                provider_preset=connection.provider_preset,
                base_url=connection.base_url,
                api_key_ref=connection.api_key_ref,
            )
            for candidate in enabled_candidates
        ]
        if not targets:
            targets = [
                ResolvedScanTarget(
                    candidate_id=f"{connection.id}:{model_id}:default",
                    source_id=connection.source_id,
                    connection_id=connection.id,
                    model_id=model_id,
                    scan_profile="default",
                    display_name=model_id,
                    connection_mode="api",
                    api_format=connection.api_format,
                    provider_preset=connection.provider_preset,
                    base_url=connection.base_url,
                    api_key_ref=connection.api_key_ref,
                )
            ]
        status = "ok"
        message = "连接成功"
        category: str | None = None
        try:
            api_key = (secret_store or SecretStore()).resolve(
                connection.api_key_ref
            )
            for target in targets:
                requester(target, "Reply with only OK.", api_key)
        except SecretStoreError:
            status = "authentication_failed"
            category = status
            message = "API Key 不可用"
        except EndpointError as exc:
            status = exc.category
            category = exc.category
            message = _safe_endpoint_message(exc.category)
        tested_at = datetime.now().astimezone().isoformat(timespec="seconds")

        def record(config):
            persisted = next(
                (
                    item
                    for item in config.model_ingress.connections
                    if item.id == connection_id
                ),
                None,
            )
            if persisted is None:
                raise ValueError("unknown connection_id")
            persisted.last_test_status = status
            persisted.last_test_at = tested_at
            persisted.last_test_message = message
            return config

        self.store.update(record)
        return {
            "ok": status == "ok",
            "status": status,
            "error_category": category,
            "message": message,
            "tested_at": tested_at,
        }


@dataclass(frozen=True)
class ConfigApplicationService:
    service: MonitorService
    snapshot_builder: Callable[..., dict[str, object]]
    codex_insights_provider: Callable[..., dict[str, object]] | None = None

    def replace_legacy_config(
        self,
        payload: dict[str, object],
    ) -> dict[str, object]:
        self._command().replace_legacy_config(payload)
        state = self._snapshot()
        return {"config": state["config"], "state": state}

    def patch_config(
        self,
        payload: dict[str, object],
    ) -> dict[str, object]:
        question_pack = self.service.question_bank.load()
        _, operation = self._command().apply_patch(
            payload,
            valid_evaluation_profile_ids={
                profile.id for profile in question_pack.evaluation_profiles
            },
        )
        return self._mutation_response("patch_config", operation)

    def upsert_endpoint(
        self,
        payload: dict[str, object],
    ) -> dict[str, object]:
        _, operation = self._command().upsert_endpoint(payload)
        return self._mutation_response("upsert_endpoint", operation)

    def add_endpoint_models(
        self,
        payload: dict[str, object],
    ) -> dict[str, object]:
        _, operation = self._command().add_endpoint_models(payload)
        return self._mutation_response("add_endpoint_models", operation)

    def _mutation_response(
        self,
        action: str,
        operation: str,
    ) -> dict[str, object]:
        state = self._snapshot()
        return {
            "schema_version": 1,
            "ok": True,
            "action": action,
            "operation": operation,
            "config": state["config"],
            "state": state,
        }

    def _command(self) -> ConfigCommand:
        return ConfigCommand(
            self.service.config_store,
            self.service.active_run_store,
        )

    def _snapshot(self) -> dict[str, object]:
        return self.snapshot_builder(
            self.service.config_store,
            self.service.history_store,
            self.service.active_run_store,
            codex_insights_provider=self.codex_insights_provider,
        )


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
