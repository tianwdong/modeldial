from __future__ import annotations

from dataclasses import dataclass

from .model_identity import DEFAULT_SCAN_PROFILES
from .models import AppConfig, ConnectionConfig, ResolvedScanTarget, SourceConfig
from .scan_target_resolver import ScanTargetResolver


SETTINGS_PROJECTION_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class SettingsProjectionProjector:
    """Project read-only settings facts from the executable target resolver."""

    target_resolver: ScanTargetResolver

    def project(
        self,
        config: AppConfig,
        *,
        dashboard_cards: list[dict[str, object]],
    ) -> dict[str, object]:
        configured_targets = self.target_resolver.configured_targets(config)
        connection_ready_targets = self.target_resolver.connection_ready_targets(config)
        available_targets = self.target_resolver.available_targets(config)
        regular_targets = self.target_resolver.enabled_targets(config)
        valid_baseline_ids = self._valid_baseline_candidate_ids(dashboard_cards)

        sources_by_id = {
            source.id: source for source in config.model_ingress.sources
        }
        ready_ids = {target.candidate_id for target in connection_ready_targets}
        available_ids = {target.candidate_id for target in available_targets}
        regular_ids = {target.candidate_id for target in regular_targets}

        connections = [
            self._connection_projection(
                source=sources_by_id.get(connection.source_id),
                connection=connection,
                ready_ids=ready_ids,
                available_ids=available_ids,
                regular_ids=regular_ids,
                valid_baseline_ids=valid_baseline_ids,
            )
            for connection in config.model_ingress.connections
        ]
        candidates = self._candidate_projections(
            config=config,
            targets=configured_targets,
            available_ids=available_ids,
        )
        blocked_reasons = [
            {
                "connection_id": item["connection_id"],
                "source_id": item["source_id"],
                "reason": item["reason"],
                "action": item["action"],
                "candidate_ids": list(item["enabled_candidate_ids"]),
            }
            for item in connections
            if item["operational_status"] != "operational"
        ]

        return {
            "schema_version": SETTINGS_PROJECTION_SCHEMA_VERSION,
            "connections": connections,
            "scan_scope": {
                "regular_candidate_ids": [
                    target.candidate_id for target in regular_targets
                ],
                "custom_candidate_ids": [
                    target.candidate_id for target in available_targets
                ],
                "source_count": len(
                    {target.source_id for target in regular_targets}
                ),
                "model_count": len(
                    {
                        (target.connection_id, target.model_id)
                        for target in regular_targets
                    }
                ),
                "candidate_count": len(regular_targets),
                "blocked_reasons": blocked_reasons,
            },
            "candidates": candidates,
        }

    @staticmethod
    def _connection_projection(
        *,
        source: SourceConfig | None,
        connection: ConnectionConfig,
        ready_ids: set[str],
        available_ids: set[str],
        regular_ids: set[str],
        valid_baseline_ids: set[str],
    ) -> dict[str, object]:
        candidate_ids = [
            candidate.id for candidate in connection.model_candidates
        ]
        enabled_candidate_ids = [
            candidate.id
            for candidate in connection.model_candidates
            if candidate.enabled
        ]
        connection_ready_ids = [
            candidate_id
            for candidate_id in candidate_ids
            if candidate_id in ready_ids
        ]
        connection_available_ids = [
            candidate_id
            for candidate_id in candidate_ids
            if candidate_id in available_ids
        ]
        regular_candidate_ids = [
            candidate_id
            for candidate_id in candidate_ids
            if candidate_id in regular_ids
        ]
        status, reason, action = SettingsProjectionProjector._operational_state(
            source=source,
            connection=connection,
            candidate_ids=candidate_ids,
            enabled_candidate_ids=enabled_candidate_ids,
            connection_ready_ids=connection_ready_ids,
            available_candidate_ids=connection_available_ids,
            regular_candidate_ids=regular_candidate_ids,
        )
        baseline_candidate_ids = [
            candidate_id
            for candidate_id in enabled_candidate_ids
            if candidate_id in valid_baseline_ids
        ]
        (
            recommendation_status,
            recommendation_reason,
            recommendation_action,
            completed_step_count,
        ) = SettingsProjectionProjector._recommendation_state(
            operational_status=status,
            operational_reason=reason,
            operational_action=action,
            baseline_candidate_ids=baseline_candidate_ids,
        )
        return {
            "connection_id": connection.id,
            "source_id": connection.source_id,
            "operational_status": status,
            "reason": reason,
            "action": action,
            "enabled_candidate_ids": enabled_candidate_ids,
            "available_candidate_ids": connection_available_ids,
            "recommendation_status": recommendation_status,
            "recommendation_reason": recommendation_reason,
            "recommendation_action": recommendation_action,
            "completed_step_count": completed_step_count,
            "baseline_candidate_ids": baseline_candidate_ids,
        }

    @staticmethod
    def _recommendation_state(
        *,
        operational_status: str,
        operational_reason: str,
        operational_action: str,
        baseline_candidate_ids: list[str],
    ) -> tuple[str, str, str, int]:
        if operational_status == "disabled":
            return (
                "disabled",
                operational_reason,
                operational_action,
                0,
            )
        if operational_reason in {
            "api_configuration_incomplete",
            "source_missing",
        }:
            return (
                "needs_configuration",
                operational_reason,
                operational_action,
                0,
            )
        if (
            operational_status == "needs_selection"
            or operational_reason == "no_candidates_configured"
        ):
            return (
                "needs_model_selection",
                operational_reason,
                operational_action,
                1,
            )
        if operational_status != "operational":
            return (
                "needs_connection_test",
                operational_reason,
                operational_action,
                0,
            )
        if not baseline_candidate_ids:
            return "needs_baseline", "no_valid_baseline", "scan_baseline", 2
        return "ready", "ready", "none", 4

    @staticmethod
    def _valid_baseline_candidate_ids(
        dashboard_cards: list[dict[str, object]],
    ) -> set[str]:
        return {
            str(card["id"])
            for card in dashboard_cards
            if card.get("id")
            and card.get("is_current_pack_comparable") is True
            and isinstance(card.get("recent_count"), int)
            and int(card["recent_count"]) > 0
        }

    @staticmethod
    def _operational_state(
        *,
        source: SourceConfig | None,
        connection: ConnectionConfig,
        candidate_ids: list[str],
        enabled_candidate_ids: list[str],
        connection_ready_ids: list[str],
        available_candidate_ids: list[str],
        regular_candidate_ids: list[str],
    ) -> tuple[str, str, str]:
        if source is None:
            return "blocked", "source_missing", "repair_configuration"
        if source.id == "claude_local" and not connection.local_login_verified:
            return "blocked", "local_login_unverified", "verify_local_login"
        if not source.enabled:
            return "disabled", "source_disabled", "enable_source"
        if not connection.enabled:
            return "disabled", "connection_disabled", "enable_connection"
        if not candidate_ids:
            return "blocked", "no_candidates_configured", "add_candidate"
        if not connection_ready_ids:
            if source.mode == "api":
                if not connection.base_url or not connection.api_key_ref:
                    return (
                        "blocked",
                        "api_configuration_incomplete",
                        "configure_connection",
                    )
                return "blocked", "api_connection_unverified", "test_connection"
            return "blocked", "connection_unavailable", "inspect_connection"
        if not available_candidate_ids:
            return "blocked", "connection_unavailable", "inspect_connection"
        if not enabled_candidate_ids or not regular_candidate_ids:
            return "needs_selection", "no_enabled_candidates", "enable_candidate"
        return "operational", "ready", "none"

    @staticmethod
    def _candidate_projections(
        *,
        config: AppConfig,
        targets: list[ResolvedScanTarget],
        available_ids: set[str],
    ) -> list[dict[str, object]]:
        connections_by_id = {
            connection.id: connection
            for connection in config.model_ingress.connections
        }
        candidate_by_identity = {
            (connection.id, candidate.id): candidate
            for connection in config.model_ingress.connections
            for candidate in connection.model_candidates
        }
        projected: list[dict[str, object]] = []
        for target in targets:
            connection = connections_by_id[target.connection_id]
            candidate = candidate_by_identity[(target.connection_id, target.candidate_id)]
            display_scan_profile = target.display_effort
            projected.append(
                {
                    "candidate_id": target.candidate_id,
                    "source_id": target.source_id,
                    "connection_id": target.connection_id,
                    "provider_id": connection.provider_id,
                    "family_id": candidate.family_id or target.display_model,
                    "variant_id": candidate.variant_id or (
                        display_scan_profile
                        if display_scan_profile not in DEFAULT_SCAN_PROFILES
                        else None
                    ),
                    "model_id": target.model_id,
                    "display_model": target.display_model,
                    "scan_profile": target.scan_profile,
                    "display_scan_profile": display_scan_profile,
                    "enabled": candidate.enabled,
                    "available": target.candidate_id in available_ids,
                }
            )
        return projected
