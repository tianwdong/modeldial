from __future__ import annotations

from .model_identity import infer_reasoning_suffix_aliases, resolve_model_display_identity
from .models import AppConfig, ResolvedScanTarget, ScanResult


class ScanTargetResolver:
    """Resolve configured model ingress into executable scan targets."""

    def enabled_targets(self, config: AppConfig) -> list[ResolvedScanTarget]:
        enabled_candidate_ids = {
            candidate.id
            for connection in config.model_ingress.connections
            for candidate in connection.model_candidates
            if candidate.enabled
        }
        return [
            target
            for target in self.available_targets(config)
            if target.candidate_id in enabled_candidate_ids
        ]

    def configured_targets(self, config: AppConfig) -> list[ResolvedScanTarget]:
        sources_by_id = {
            source.id: source
            for source in config.model_ingress.sources
        }
        targets: list[ResolvedScanTarget] = []
        for connection in config.model_ingress.connections:
            source = sources_by_id.get(connection.source_id)
            if source is None:
                continue
            suffix_aliases = (
                infer_reasoning_suffix_aliases(
                    [candidate.model_id for candidate in connection.model_candidates]
                )
                if connection.api_format is not None
                else {}
            )
            for candidate in connection.model_candidates:
                identity = resolve_model_display_identity(
                    model_id=candidate.model_id,
                    scan_profile=candidate.scan_profile,
                    family_id=candidate.family_id,
                    variant_id=candidate.variant_id,
                    inferred_alias=suffix_aliases.get(candidate.model_id),
                )
                targets.append(
                    ResolvedScanTarget(
                        candidate_id=candidate.id,
                        source_id=source.id,
                        connection_id=connection.id,
                        model_id=candidate.model_id,
                        scan_profile=candidate.scan_profile,
                        display_name=candidate.display_name,
                        display_model_id=identity.model,
                        display_scan_profile=identity.effort,
                        connection_mode=source.mode,
                        api_format=connection.api_format,
                        provider_preset=connection.provider_preset,
                        base_url=connection.base_url,
                        api_key_ref=connection.api_key_ref,
                        reasoning_tokens_supported=(
                            "reasoning_tokens_unavailable"
                            not in candidate.capabilities
                        ),
                    )
                )
        return targets

    def available_targets(self, config: AppConfig) -> list[ResolvedScanTarget]:
        enabled_source_ids = {
            source.id
            for source in config.model_ingress.sources
            if source.enabled
        }
        enabled_connection_ids = {
            connection.id
            for connection in config.model_ingress.connections
            if connection.enabled
        }
        return [
            target
            for target in self.connection_ready_targets(config)
            if target.source_id in enabled_source_ids
            and target.connection_id in enabled_connection_ids
        ]

    def connection_ready_targets(
        self,
        config: AppConfig,
    ) -> list[ResolvedScanTarget]:
        sources_by_id = {
            source.id: source
            for source in config.model_ingress.sources
        }
        connections_by_id = {
            connection.id: connection
            for connection in config.model_ingress.connections
        }
        return [
            target
            for target in self.configured_targets(config)
            if (
                sources_by_id[target.source_id].mode != "api"
                or connections_by_id[target.connection_id].last_test_status == "ok"
            )
            and (
                target.source_id != "claude_local"
                or connections_by_id[target.connection_id].local_login_verified
            )
        ]

    def requested_targets(
        self,
        config: AppConfig,
        requested_candidate_ids: list[str] | None,
        *,
        allow_disabled: bool = False,
    ) -> list[ResolvedScanTarget]:
        if requested_candidate_ids is None:
            return self.enabled_targets(config)
        if not requested_candidate_ids:
            raise ValueError("at least one candidate_id is required")
        if len(set(requested_candidate_ids)) != len(requested_candidate_ids):
            raise ValueError("duplicate candidate_id")
        available = {
            target.candidate_id: target
            for target in (
                self.connection_ready_targets(config)
                if allow_disabled
                else self.available_targets(config)
            )
        }
        missing = [
            candidate_id
            for candidate_id in requested_candidate_ids
            if candidate_id not in available
        ]
        if missing:
            raise ValueError(f"unknown candidate_id: {missing[0]}")
        return [available[candidate_id] for candidate_id in requested_candidate_ids]

    @staticmethod
    def candidate_ids_by_label(
        targets: list[ResolvedScanTarget],
    ) -> dict[str, list[str]]:
        candidate_ids_by_label: dict[str, list[str]] = {}
        for target in targets:
            candidate_ids_by_label.setdefault(target.label, []).append(target.candidate_id)
        return candidate_ids_by_label

    @staticmethod
    def candidate_id_from_label(
        label: str,
        candidate_ids_by_label: dict[str, list[str]],
    ) -> str | None:
        candidate_ids = candidate_ids_by_label.get(label, [])
        if len(candidate_ids) != 1:
            return None
        return candidate_ids[0]

    def entry_candidate_id(
        self,
        entry: dict[str, object],
        candidate_ids_by_label: dict[str, list[str]],
    ) -> str | None:
        candidate_id = entry.get("candidate_id")
        if candidate_id is not None:
            return str(candidate_id)
        label = entry.get("label")
        if label is None:
            return None
        return self.candidate_id_from_label(str(label), candidate_ids_by_label)

    def result_candidate_id(
        self,
        result: ScanResult,
        candidate_ids_by_label: dict[str, list[str]],
    ) -> str | None:
        if result.candidate_id:
            return result.candidate_id
        return self.candidate_id_from_label(
            f"{result.model} / {result.effort}",
            candidate_ids_by_label,
        )
