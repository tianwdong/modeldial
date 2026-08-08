from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scanner.active_run_store import ActiveRunStore
from scanner.config_store import ConfigStore
from scanner.history_store import HistoryStore
from scanner.models import AppConfig, ConnectionConfig, ModelCandidateConfig
from scanner.scan_target_resolver import ScanTargetResolver
from scanner.service import MonitorService
from scanner.settings_projection import SettingsProjectionProjector
from scanner.snapshot_query import SnapshotCommand, SnapshotProjector, SnapshotQuery
from scanner.usage_store import UsageStore


def _file_fingerprint(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _query_service(root: Path) -> MonitorService:
    service = MonitorService(
        config_store=ConfigStore(root / "config.json", first_run_defaults=True),
        history_store=HistoryStore(root / "history.jsonl"),
        active_run_store=ActiveRunStore(root / "active_run.json"),
    )
    service.current_model_detector = lambda: None
    service.active_session_detector = lambda: []
    return service


def _snapshot_projector(service: MonitorService) -> SnapshotProjector:
    return SnapshotProjector(
        config_reader=service.config_store.load,
        state_reader=service.monitor_state_projector.build_state,
        settings_projector=SettingsProjectionProjector(
            service.scan_target_resolver
        ),
    )


def _snapshot_query(service: MonitorService) -> SnapshotQuery:
    return SnapshotQuery(
        snapshot_projector=_snapshot_projector(service),
        refresh_state_reader=service.monitor_state_projector.build_refresh_state,
        data_dir=service.history_store.path.parent,
    )


def _snapshot_command(service: MonitorService) -> SnapshotCommand:
    return SnapshotCommand(
        snapshot_projector=_snapshot_projector(service),
        data_dir=service.history_store.path.parent,
    )


class SnapshotApplicationServiceTest(unittest.TestCase):
    def test_snapshot_boundaries_hold_only_explicit_read_and_write_dependencies(
        self,
    ) -> None:
        from scanner.observation_application import ObservationCommand
        from scanner.snapshot_query import (
            SnapshotCommand,
            SnapshotProjector,
            SnapshotQuery,
        )

        self.assertNotIn("service", SnapshotProjector.__dataclass_fields__)
        self.assertEqual(
            set(SnapshotProjector.__dataclass_fields__),
            {"config_reader", "state_reader", "settings_projector"},
        )
        self.assertNotIn("service", SnapshotQuery.__dataclass_fields__)
        self.assertEqual(
            set(SnapshotQuery.__dataclass_fields__),
            {"snapshot_projector", "refresh_state_reader", "data_dir"},
        )
        self.assertNotIn("service", SnapshotCommand.__dataclass_fields__)
        self.assertEqual(
            set(SnapshotCommand.__dataclass_fields__),
            {"snapshot_projector", "data_dir"},
        )
        self.assertEqual(
            set(ObservationCommand.__dataclass_fields__),
            {"snapshot_command"},
        )
        scanner_root = Path(__file__).resolve().parents[1] / "scanner"
        self.assertNotIn(
            "from .service import MonitorService",
            (scanner_root / "snapshot_query.py").read_text(encoding="utf-8"),
        )
        self.assertNotIn(
            "from .service import MonitorService",
            (scanner_root / "observation_application.py").read_text(
                encoding="utf-8"
            ),
        )

    def test_reference_freshness_is_projected_by_the_backend(self) -> None:
        from datetime import datetime, timezone

        from scanner.snapshot_query import project_reference_snapshot_feed

        feed = project_reference_snapshot_feed(
            {
                "schema_version": 1,
                "status": "ready",
                "latest": {"published_at": "2026-07-29T00:00:00Z"},
                "snapshots": [],
            },
            now=datetime(2026, 7, 29, 13, 30, tzinfo=timezone.utc),
        )

        self.assertEqual(feed["freshness"], "delayed")
        self.assertEqual(feed["age_hours"], 13)

    def test_queries_are_read_only_and_keep_distinct_protocol_envelopes(self) -> None:
        from scanner.snapshot_query import SnapshotQuery

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            query = _snapshot_query(_query_service(root))
            before = _file_fingerprint(root)

            snapshot = query.build_snapshot()
            refresh = query.build_refresh_snapshot()

            after = _file_fingerprint(root)

        self.assertEqual(before, after)
        self.assertEqual(snapshot["schema_version"], 2)
        self.assertNotIn("history", snapshot)
        self.assertIn("advisor_v2_evidence", snapshot)
        self.assertIn("recommendation_portfolio_v2", snapshot)
        settings_projection = snapshot["settings_projection"]
        self.assertEqual(settings_projection["schema_version"], 1)  # type: ignore[index]
        self.assertEqual(  # type: ignore[arg-type]
            set(settings_projection),
            {"schema_version", "connections", "scan_scope", "candidates"},
        )
        self.assertEqual(  # type: ignore[index]
            set(settings_projection["connections"][0]),
            {
                "connection_id",
                "source_id",
                "operational_status",
                "reason",
                "action",
                "enabled_candidate_ids",
                "available_candidate_ids",
                "recommendation_status",
                "recommendation_reason",
                "recommendation_action",
                "completed_step_count",
                "baseline_candidate_ids",
            },
        )
        self.assertEqual(  # type: ignore[index]
            set(settings_projection["scan_scope"]),
            {
                "regular_candidate_ids",
                "custom_candidate_ids",
                "source_count",
                "model_count",
                "candidate_count",
                "blocked_reasons",
            },
        )
        self.assertEqual(refresh["schema_version"], 1)
        self.assertEqual(
            set(refresh),
            {
                "schema_version",
                "config",
                "runtime",
                "question_pack",
                "recommendation_use",
            },
        )

    def test_query_forwards_prior_recommendation_epochs_without_mutating_them(
        self,
    ) -> None:
        prior_epoch = {
            "lifecycle_status": "open",
            "segment_kind": "actual_switch",
            "current_model_configuration_id": "baseline",
            "recommended_model_configuration_id": "adopted",
            "preference": "speed",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = UsageStore(root)
            store.save_recommendation_use_state(
                {
                    "schema_version": 1,
                    "epochs": [prior_epoch],
                    "observation_assignments": {},
                }
            )
            service = _query_service(root)
            original_projector = _snapshot_projector(service)

            class CapturingProjector:
                def __init__(self, wrapped: SnapshotProjector) -> None:
                    self.wrapped = wrapped
                    self.kwargs: dict[str, object] | None = None

                def project(self, **kwargs: object) -> tuple[dict[str, object], dict[str, object]]:
                    self.kwargs = kwargs
                    return self.wrapped.project(**kwargs)  # type: ignore[arg-type]

            projector = CapturingProjector(original_projector)
            query = SnapshotQuery(
                snapshot_projector=projector,  # type: ignore[arg-type]
                refresh_state_reader=service.monitor_state_projector.build_refresh_state,
                data_dir=root,
            )
            before = _file_fingerprint(root)

            query.build_snapshot()

            after = _file_fingerprint(root)

        self.assertEqual(before, after)
        self.assertEqual(
            projector.kwargs["prior_recommendation_epochs"],  # type: ignore[index]
            [prior_epoch],
        )

    def test_unverified_api_is_excluded_from_authoritative_scan_scopes(self) -> None:
        from scanner.snapshot_query import SnapshotQuery

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = _query_service(root)
            config = service.config_store.load()
            connection_id = "endpoint-unverified"
            candidate_id = f"{connection_id}:gpt-5.4:high"
            config.model_ingress.connections.append(
                ConnectionConfig(
                    id=connection_id,
                    source_id="custom_endpoint",
                    name="OpenAI",
                    enabled=True,
                    api_format="openai_responses",
                    provider_id="openai",
                    base_url="https://api.openai.com/v1",
                    api_key_ref="keychain://endpoint-unverified",
                    last_test_status="untested",
                    model_candidates=[
                        ModelCandidateConfig(
                            id=candidate_id,
                            connection_id=connection_id,
                            model_id="gpt-5.4",
                            display_name="GPT-5.4 High",
                            family_id="gpt-5.4",
                            enabled=True,
                            scan_profile="high",
                        )
                    ],
                )
            )
            service.config_store.save(config)

            projection = _snapshot_query(service).build_snapshot()[
                "settings_projection"
            ]

        connection = next(  # type: ignore[arg-type]
            item
            for item in projection["connections"]  # type: ignore[index]
            if item["connection_id"] == connection_id
        )
        self.assertEqual(connection["operational_status"], "blocked")
        self.assertEqual(connection["reason"], "api_connection_unverified")
        self.assertEqual(connection["action"], "test_connection")
        self.assertEqual(connection["enabled_candidate_ids"], [candidate_id])
        self.assertEqual(connection["available_candidate_ids"], [])
        scan_scope = projection["scan_scope"]  # type: ignore[index]
        self.assertNotIn(candidate_id, scan_scope["regular_candidate_ids"])
        self.assertNotIn(candidate_id, scan_scope["custom_candidate_ids"])
        self.assertIn(
            "api_connection_unverified",
            [item["reason"] for item in scan_scope["blocked_reasons"]],
        )

    def test_disabled_and_unverified_local_connections_have_backend_status(self) -> None:
        from scanner.snapshot_query import SnapshotQuery

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = _query_service(root)
            config = service.config_store.load()
            codex_connection = next(
                item
                for item in config.model_ingress.connections
                if item.id == "codex-local-default"
            )
            codex_connection.model_candidates[0].enabled = True
            claude_source = next(
                item
                for item in config.model_ingress.sources
                if item.id == "claude_local"
            )
            claude_connection = next(
                item
                for item in config.model_ingress.connections
                if item.id == "claude-local-default"
            )
            claude_source.enabled = True
            claude_connection.enabled = True
            claude_connection.local_login_verified = False
            claude_connection.model_candidates[0].enabled = True
            claude_candidate_id = claude_connection.model_candidates[0].id
            service.config_store.save(config)

            projection = _snapshot_query(service).build_snapshot()[
                "settings_projection"
            ]

        by_connection = {  # type: ignore[arg-type]
            item["connection_id"]: item
            for item in projection["connections"]  # type: ignore[index]
        }
        self.assertEqual(
            by_connection["codex-local-default"]["operational_status"],
            "disabled",
        )
        self.assertEqual(
            by_connection["codex-local-default"]["reason"],
            "source_disabled",
        )
        self.assertEqual(
            by_connection["claude-local-default"]["operational_status"],
            "blocked",
        )
        self.assertEqual(
            by_connection["claude-local-default"]["reason"],
            "local_login_unverified",
        )
        self.assertEqual(
            by_connection["claude-local-default"]["action"],
            "verify_local_login",
        )
        self.assertNotIn(  # type: ignore[operator]
            claude_candidate_id,
            projection["scan_scope"]["custom_candidate_ids"],  # type: ignore[index]
        )

    def test_duplicate_labels_keep_candidate_and_connection_identity(self) -> None:
        from scanner.snapshot_query import SnapshotQuery

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = _query_service(root)
            config = service.config_store.load()
            candidate_ids: list[str] = []
            for suffix in ("a", "b"):
                connection_id = f"endpoint-duplicate-{suffix}"
                candidate_id = f"{connection_id}:gpt-5.4:high"
                candidate_ids.append(candidate_id)
                config.model_ingress.connections.append(
                    ConnectionConfig(
                        id=connection_id,
                        source_id="custom_endpoint",
                        name=f"OpenAI {suffix.upper()}",
                        enabled=True,
                        api_format="openai_responses",
                        provider_id="openai",
                        base_url=f"https://{suffix}.example.test/v1",
                        api_key_ref=f"keychain://{connection_id}",
                        last_test_status="ok",
                        model_candidates=[
                            ModelCandidateConfig(
                                id=candidate_id,
                                connection_id=connection_id,
                                model_id="gpt-5.4",
                                display_name="GPT-5.4 High",
                                family_id="gpt-5.4",
                                enabled=True,
                                scan_profile="high",
                            )
                        ],
                    )
                )
            service.config_store.save(config)

            projection = _snapshot_query(service).build_snapshot()[
                "settings_projection"
            ]

        candidates = [  # type: ignore[arg-type]
            item
            for item in projection["candidates"]  # type: ignore[index]
            if item["candidate_id"] in candidate_ids
        ]
        self.assertEqual([item["candidate_id"] for item in candidates], candidate_ids)
        self.assertEqual(len({item["connection_id"] for item in candidates}), 2)
        self.assertEqual({item["provider_id"] for item in candidates}, {"openai"})
        self.assertEqual({item["family_id"] for item in candidates}, {"gpt-5.4"})
        self.assertEqual({item["display_model"] for item in candidates}, {"gpt-5.4"})
        self.assertEqual({item["scan_profile"] for item in candidates}, {"high"})
        scan_scope = projection["scan_scope"]  # type: ignore[index]
        self.assertEqual(
            [
                candidate_id
                for candidate_id in scan_scope["regular_candidate_ids"]
                if candidate_id in candidate_ids
            ],
            candidate_ids,
        )
        self.assertEqual(scan_scope["source_count"], 1)
        self.assertEqual(scan_scope["model_count"], 2)
        self.assertEqual(scan_scope["candidate_count"], 2)

    def test_settings_projection_owns_legacy_reasoning_suffix_identity(self) -> None:
        from scanner.settings_projection import SettingsProjectionProjector

        config = AppConfig.first_run()
        connection_id = "endpoint-legacy-suffix"
        candidate_ids = [
            f"{connection_id}:legacy-model-high:default",
            f"{connection_id}:legacy-model-low:default",
        ]
        config.model_ingress.connections.append(
            ConnectionConfig(
                id=connection_id,
                source_id="custom_endpoint",
                name="Legacy endpoint",
                enabled=True,
                api_format="openai_responses",
                provider_id="openai",
                base_url="https://legacy.example.test/v1",
                api_key_ref="keychain://endpoint-legacy-suffix",
                last_test_status="ok",
                model_candidates=[
                    ModelCandidateConfig(
                        id=candidate_ids[0],
                        connection_id=connection_id,
                        model_id="legacy-model-high",
                        display_name="Legacy High",
                        enabled=True,
                        scan_profile="default",
                    ),
                    ModelCandidateConfig(
                        id=candidate_ids[1],
                        connection_id=connection_id,
                        model_id="legacy-model-low",
                        display_name="Legacy Low",
                        enabled=True,
                        scan_profile="default",
                    ),
                ],
            )
        )

        projection = SettingsProjectionProjector(ScanTargetResolver()).project(
            config,
            dashboard_cards=[],
        )
        projected_by_id = {
            item["candidate_id"]: item
            for item in projection["candidates"]  # type: ignore[index]
            if item["candidate_id"] in candidate_ids
        }

        self.assertEqual(set(projected_by_id), set(candidate_ids))
        self.assertEqual(
            {item["provider_id"] for item in projected_by_id.values()},
            {"openai"},
        )
        self.assertEqual(
            {item["family_id"] for item in projected_by_id.values()},
            {"legacy-model"},
        )
        self.assertEqual(
            {item["display_model"] for item in projected_by_id.values()},
            {"legacy-model"},
        )
        self.assertEqual(
            {item["variant_id"] for item in projected_by_id.values()},
            {"high", "low"},
        )
        self.assertEqual(
            {item["display_scan_profile"] for item in projected_by_id.values()},
            {"high", "low"},
        )

    def test_recommendation_readiness_uses_operational_state_and_valid_baselines(
        self,
    ) -> None:
        from scanner.settings_projection import SettingsProjectionProjector

        config = AppConfig.first_run()
        candidate_ids: dict[str, str] = {}

        def add_api_connection(
            connection_id: str,
            *,
            base_url: str | None,
            api_key_ref: str | None,
            last_test_status: str,
            candidate_enabled: bool,
        ) -> None:
            candidate_id = f"{connection_id}:gpt-5.4:high"
            candidate_ids[connection_id] = candidate_id
            config.model_ingress.connections.append(
                ConnectionConfig(
                    id=connection_id,
                    source_id="custom_endpoint",
                    name=connection_id,
                    enabled=True,
                    api_format="openai_responses",
                    provider_id="openai",
                    base_url=base_url,
                    api_key_ref=api_key_ref,
                    last_test_status=last_test_status,
                    model_candidates=[
                        ModelCandidateConfig(
                            id=candidate_id,
                            connection_id=connection_id,
                            model_id="gpt-5.4",
                            display_name="GPT-5.4 High",
                            family_id="gpt-5.4",
                            enabled=candidate_enabled,
                            scan_profile="high",
                        )
                    ],
                )
            )

        add_api_connection(
            "api-unconfigured",
            base_url=None,
            api_key_ref=None,
            last_test_status="untested",
            candidate_enabled=True,
        )
        add_api_connection(
            "api-untested",
            base_url="https://untested.example.test/v1",
            api_key_ref="keychain://api-untested",
            last_test_status="untested",
            candidate_enabled=True,
        )
        add_api_connection(
            "api-unselected",
            base_url="https://unselected.example.test/v1",
            api_key_ref="keychain://api-unselected",
            last_test_status="ok",
            candidate_enabled=False,
        )
        add_api_connection(
            "api-no-baseline",
            base_url="https://no-baseline.example.test/v1",
            api_key_ref="keychain://api-no-baseline",
            last_test_status="ok",
            candidate_enabled=True,
        )
        add_api_connection(
            "api-ready",
            base_url="https://ready.example.test/v1",
            api_key_ref="keychain://api-ready",
            last_test_status="ok",
            candidate_enabled=True,
        )

        projection = SettingsProjectionProjector(ScanTargetResolver()).project(
            config,
            dashboard_cards=[
                {
                    "id": candidate_ids["api-no-baseline"],
                    "recent_count": 3,
                    "is_current_pack_comparable": False,
                },
                {
                    "id": candidate_ids["api-ready"],
                    "recent_count": 1,
                    "is_current_pack_comparable": True,
                },
            ],
        )
        connections = {
            item["connection_id"]: item
            for item in projection["connections"]  # type: ignore[index]
        }

        self.assertEqual(
            (
                connections["api-unconfigured"]["recommendation_status"],
                connections["api-unconfigured"]["recommendation_reason"],
                connections["api-unconfigured"]["recommendation_action"],
                connections["api-unconfigured"]["completed_step_count"],
            ),
            (
                "needs_configuration",
                "api_configuration_incomplete",
                "configure_connection",
                0,
            ),
        )
        self.assertEqual(
            (
                connections["api-untested"]["recommendation_status"],
                connections["api-untested"]["recommendation_reason"],
                connections["api-untested"]["recommendation_action"],
                connections["api-untested"]["completed_step_count"],
            ),
            (
                "needs_connection_test",
                "api_connection_unverified",
                "test_connection",
                0,
            ),
        )
        self.assertEqual(
            (
                connections["api-unselected"]["recommendation_status"],
                connections["api-unselected"]["recommendation_reason"],
                connections["api-unselected"]["recommendation_action"],
                connections["api-unselected"]["completed_step_count"],
            ),
            (
                "needs_model_selection",
                "no_enabled_candidates",
                "enable_candidate",
                1,
            ),
        )
        self.assertEqual(
            (
                connections["api-no-baseline"]["recommendation_status"],
                connections["api-no-baseline"]["recommendation_reason"],
                connections["api-no-baseline"]["recommendation_action"],
                connections["api-no-baseline"]["completed_step_count"],
                connections["api-no-baseline"]["baseline_candidate_ids"],
            ),
            ("needs_baseline", "no_valid_baseline", "scan_baseline", 2, []),
        )
        self.assertEqual(
            (
                connections["api-ready"]["recommendation_status"],
                connections["api-ready"]["recommendation_reason"],
                connections["api-ready"]["recommendation_action"],
                connections["api-ready"]["completed_step_count"],
                connections["api-ready"]["baseline_candidate_ids"],
            ),
            (
                "ready",
                "ready",
                "none",
                4,
                [candidate_ids["api-ready"]],
            ),
        )

    def test_command_snapshot_owns_reference_refresh_and_recommendation_updates(
        self,
    ) -> None:
        from scanner.snapshot_query import SnapshotCommand

        reference_feed = {
            "schema_version": 1,
            "status": "missing",
            "kind": "first_party_snapshot",
            "latest": None,
            "snapshots": [],
            "delivery": {
                "source": "bundled",
                "refresh_status": "not_configured",
            },
        }
        recommendation_use = {"schema_version": 1, "epochs": []}

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "scanner.snapshot_query.load_reference_snapshot_feed_for_app",
            return_value=reference_feed,
        ) as load_reference, patch(
            "scanner.snapshot_query.read_reference_snapshot_feed_for_app",
            side_effect=AssertionError("refresh command must use the mutating loader"),
        ), patch(
            "scanner.snapshot_query.update_recommendation_use_epochs",
            return_value=recommendation_use,
        ) as update_recommendation:
            root = Path(temp_dir)
            service = _query_service(root)
            snapshot = _snapshot_command(service).build_snapshot(
                refresh_reference=True,
            )

        load_reference.assert_called_once_with(
            cache_root=root / "reference_snapshots",
        )
        update_recommendation.assert_called_once()
        self.assertEqual(snapshot["schema_version"], 2)
        self.assertEqual(snapshot["recommendation_use"], recommendation_use)

    def test_native_bridge_snapshot_functions_are_thin_service_delegates(self) -> None:
        from scanner import native_bridge

        service = Mock(spec=MonitorService)
        query = Mock()
        query.build_snapshot.return_value = {"schema_version": 2}
        query.build_refresh_snapshot.return_value = {"schema_version": 1}
        command = Mock()
        command.build_snapshot.return_value = {"schema_version": 2, "command": True}

        with patch.object(
            native_bridge,
            "_query_monitor_service",
            return_value=service,
        ) as make_service, patch.object(
            native_bridge,
            "_snapshot_query",
            return_value=query,
        ) as make_query, patch.object(
            native_bridge,
            "_snapshot_command",
            return_value=command,
        ) as make_command:
            snapshot = native_bridge.build_snapshot(codex_insights={"source": "read"})
            refresh = native_bridge.build_refresh_snapshot(
                codex_insights={"source": "refresh"}
            )
            command_snapshot = native_bridge._build_command_snapshot(
                codex_insights={"source": "command"},
                refresh_reference=True,
            )

        self.assertEqual(make_service.call_count, 3)
        self.assertEqual(make_query.call_count, 2)
        make_query.assert_called_with(service)
        query.build_snapshot.assert_called_once_with(
            codex_insights={"source": "read"}
        )
        query.build_refresh_snapshot.assert_called_once_with(
            codex_insights={"source": "refresh"}
        )
        make_command.assert_called_once_with(service)
        command.build_snapshot.assert_called_once_with(
            codex_insights_provider=None,
            codex_insights={"source": "command"},
            refresh_reference=True,
        )
        self.assertEqual(snapshot, {"schema_version": 2})
        self.assertEqual(refresh, {"schema_version": 1})
        self.assertEqual(command_snapshot, {"schema_version": 2, "command": True})


if __name__ == "__main__":
    unittest.main()
