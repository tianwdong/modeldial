from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scanner.config_store import ConfigStore
from scanner.models import (
    AppConfig,
    ConnectionConfig,
    ModelCandidateConfig,
    ProjectProfileConfig,
    RecommendationConfig,
    SchedulerConfig,
)


class ConfigStoreTest(unittest.TestCase):
    def test_failed_save_preserves_the_previous_config_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            store = ConfigStore(path)
            store.save(AppConfig.default())
            previous = path.read_text(encoding="utf-8")

            with patch("scanner.config_store.json.dump", side_effect=RuntimeError("boom")):
                with self.assertRaisesRegex(RuntimeError, "boom"):
                    store.save(AppConfig.first_run())

            self.assertEqual(path.read_text(encoding="utf-8"), previous)
            self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_scan_budget_defaults_round_trip_and_legacy_fallback(self) -> None:
        config = AppConfig.default()

        self.assertFalse(config.scan_budget.enabled)
        self.assertEqual(config.scan_budget.max_duration_seconds, 900)
        self.assertEqual(config.scan_budget.max_reference_cost_usd, 1.0)

        config.scan_budget.enabled = True
        config.scan_budget.max_duration_seconds = 420
        config.scan_budget.max_reference_cost_usd = 0.25
        restored = AppConfig.from_dict(config.to_dict())
        self.assertTrue(restored.scan_budget.enabled)
        self.assertEqual(restored.scan_budget.max_duration_seconds, 420)
        self.assertEqual(restored.scan_budget.max_reference_cost_usd, 0.25)

        legacy_payload = config.to_dict()
        legacy_payload.pop("scan_budget")
        legacy = AppConfig.from_dict(legacy_payload)
        self.assertFalse(legacy.scan_budget.enabled)

    def test_system_concurrency_defaults_round_trip_and_legacy_fallback(self) -> None:
        config = AppConfig.default()

        self.assertEqual(config.system.max_concurrent_targets, 1)
        self.assertEqual(config.system.execution_timeout_seconds, 1200)
        self.assertEqual(config.system.timeout_retry_count, 0)

        config.system.max_concurrent_targets = 4
        config.system.execution_timeout_seconds = 420
        config.system.timeout_retry_count = 2
        restored = AppConfig.from_dict(config.to_dict())
        self.assertEqual(restored.system.max_concurrent_targets, 4)
        self.assertEqual(restored.system.execution_timeout_seconds, 420)
        self.assertEqual(restored.system.timeout_retry_count, 2)

        legacy_payload = config.to_dict()
        legacy_payload["system"].pop("max_concurrent_targets")
        legacy_payload["system"].pop("execution_timeout_seconds")
        legacy_payload["system"].pop("timeout_retry_count")
        legacy = AppConfig.from_dict(legacy_payload)
        self.assertEqual(legacy.system.max_concurrent_targets, 1)
        self.assertEqual(legacy.system.execution_timeout_seconds, 1200)
        self.assertEqual(legacy.system.timeout_retry_count, 0)

    def test_scan_budget_normalizes_invalid_lower_bounds(self) -> None:
        payload = AppConfig.default().to_dict()
        payload["scan_budget"] = {
            "enabled": True,
            "max_duration_seconds": 0,
            "max_reference_cost_usd": -1,
        }

        config = AppConfig.from_dict(payload)

        self.assertEqual(config.scan_budget.max_duration_seconds, 60)
        self.assertEqual(config.scan_budget.max_reference_cost_usd, 0.01)

    def test_project_profile_round_trip_and_invalid_mode_fallback(self) -> None:
        config = AppConfig.default()
        config.recommendation = RecommendationConfig(
            current_default_candidate_id=None,
            project_profile=ProjectProfileConfig(
                project_name="支付重构",
                task_mode="重构维护",
            ),
        )

        reloaded = AppConfig.from_dict(config.to_dict())
        self.assertEqual(reloaded.recommendation.project_profile.project_name, "支付重构")
        self.assertEqual(reloaded.recommendation.project_profile.task_mode, "重构维护")

        payload = config.to_dict()
        payload["recommendation"]["project_profile"]["task_mode"] = "未知模式"
        fallback = AppConfig.from_dict(payload)
        self.assertEqual(fallback.recommendation.project_profile.task_mode, "综合推荐")

    def test_legacy_project_task_modes_migrate_to_current_scenarios(self) -> None:
        aliases = {
            "功能开发": "开发实现",
            "修 Bug": "调试修复",
            "写测试": "测试验证",
            "数据处理": "综合推荐",
        }

        for legacy_mode, current_mode in aliases.items():
            with self.subTest(legacy_mode=legacy_mode):
                payload = AppConfig.default().to_dict()
                payload["recommendation"]["project_profile"]["task_mode"] = legacy_mode

                config = AppConfig.from_dict(payload)

                self.assertEqual(
                    config.recommendation.project_profile.task_mode,
                    current_mode,
                )

    def test_legacy_recommendation_gets_default_project_profile(self) -> None:
        payload = AppConfig.default().to_dict()
        payload["recommendation"].pop("project_profile", None)
        payload["recommendation"].pop("current_model_mode", None)

        config = AppConfig.from_dict(payload)

        self.assertEqual(config.recommendation.project_profile.project_name, "当前项目")
        self.assertEqual(config.recommendation.project_profile.task_mode, "综合推荐")
        self.assertEqual(config.recommendation.current_model_mode, "auto")
        self.assertEqual(config.recommendation.preference, "smart")
        self.assertEqual(
            config.recommendation.source_mode_by_configuration_id,
            {},
        )

    def test_current_model_mode_round_trips_and_rejects_unknown_values(self) -> None:
        config = AppConfig.default()
        config.recommendation.current_model_mode = "manual"

        restored = AppConfig.from_dict(config.to_dict())
        self.assertEqual(restored.recommendation.current_model_mode, "manual")

        payload = config.to_dict()
        payload["recommendation"]["current_model_mode"] = "unknown"
        fallback = AppConfig.from_dict(payload)
        self.assertEqual(fallback.recommendation.current_model_mode, "auto")

    def test_advisor_v2_preferences_round_trip_and_reject_unknown_values(self) -> None:
        config = AppConfig.default()
        config.recommendation.preference = "cost"
        config.recommendation.source_mode_by_configuration_id = {
            "candidate-a": "official_snapshot",
            "candidate-b": "local_evaluation",
        }

        restored = AppConfig.from_dict(config.to_dict())

        self.assertEqual(restored.recommendation.preference, "cost")
        self.assertEqual(
            restored.recommendation.source_mode_by_configuration_id,
            {
                "candidate-a": "official_snapshot",
                "candidate-b": "local_evaluation",
            },
        )

        payload = config.to_dict()
        payload["recommendation"]["preference"] = "balanced"
        payload["recommendation"]["source_mode_by_configuration_id"] = {
            "candidate-a": "unknown",
            "candidate-b": "auto",
            "": "local_evaluation",
        }
        fallback = AppConfig.from_dict(payload)
        self.assertEqual(fallback.recommendation.preference, "smart")
        self.assertEqual(
            fallback.recommendation.source_mode_by_configuration_id,
            {"candidate-b": "auto"},
        )

    def test_load_returns_defaults_when_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConfigStore(
                Path(temp_dir) / "config.json",
                first_run_defaults=True,
            )

            config = store.load()

            self.assertEqual(
                [source.id for source in config.model_ingress.sources],
                [
                    "codex_local",
                    "claude_local",
                    "grok_local",
                    "custom_endpoint",
                ],
            )
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
            self.assertFalse(codex_source.enabled)
            self.assertFalse(codex_connection.enabled)
            self.assertTrue(
                all(not candidate.enabled for candidate in codex_connection.model_candidates)
            )
            self.assertEqual(
                [
                    candidate.model_id
                    for candidate in config.model_ingress.local_model_candidates()
                ],
                [
                    "gpt-5.4",
                    "gpt-5.4",
                    "gpt-5.4",
                    "gpt-5.5",
                    "gpt-5.5",
                    "gpt-5.5",
                    "gpt-5.6-sol",
                    "gpt-5.6-sol",
                    "gpt-5.6-sol",
                    "gpt-5.6-luna",
                    "gpt-5.6-luna",
                    "gpt-5.6-luna",
                    "gpt-5.6-terra",
                    "gpt-5.6-terra",
                    "gpt-5.6-terra",
                    "sonnet",
                    "sonnet",
                    "sonnet",
                    "grok-4.5",
                    "grok-4.5",
                    "grok-4.5",
                ],
            )
            new_candidates = [
                candidate
                for candidate in config.model_ingress.local_model_candidates()
                if candidate.model_id.startswith("gpt-5.6-")
            ]
            self.assertEqual(len(new_candidates), 9)
            self.assertEqual(
                {candidate.scan_profile for candidate in new_candidates},
                {"medium", "high", "xhigh"},
            )
            self.assertTrue(all(not candidate.enabled for candidate in new_candidates))
            claude_source = next(
                source
                for source in config.model_ingress.sources
                if source.id == "claude_local"
            )
            claude_connection = next(
                connection
                for connection in config.model_ingress.connections
                if connection.id == "claude-local-default"
            )
            self.assertFalse(claude_source.enabled)
            self.assertFalse(claude_connection.enabled)
            self.assertFalse(claude_connection.local_login_verified)
            self.assertEqual(
                [candidate.scan_profile for candidate in claude_connection.model_candidates],
                ["low", "medium", "high"],
            )
            self.assertTrue(
                all(
                    "reasoning_tokens_unavailable" in candidate.capabilities
                    for candidate in claude_connection.model_candidates
                )
            )
            grok_candidate = next(
                candidate
                for candidate in config.model_ingress.local_model_candidates()
                if candidate.model_id == "grok-4.5"
            )
            self.assertEqual(grok_candidate.scan_profile, "low")
            self.assertFalse(grok_candidate.enabled)
            self.assertEqual(
                [
                    candidate.connection_id
                    for connection in config.model_ingress.connections
                    for candidate in connection.model_candidates
                ],
                [
                    "codex-local-default",
                    "codex-local-default",
                    "codex-local-default",
                    "codex-local-default",
                    "codex-local-default",
                    "codex-local-default",
                    "codex-local-default",
                    "codex-local-default",
                    "codex-local-default",
                    "codex-local-default",
                    "codex-local-default",
                    "codex-local-default",
                    "codex-local-default",
                    "codex-local-default",
                    "codex-local-default",
                    "claude-local-default",
                    "claude-local-default",
                    "claude-local-default",
                    "grok-local-default",
                    "grok-local-default",
                    "grok-local-default",
                ],
            )
            self.assertFalse(config.system.use_mock_results)
            self.assertEqual(config.system.language, "zh-CN")
            self.assertEqual(config.rules["reason_tok_516"].action, "retry")

    def test_load_disables_legacy_claude_config_until_cli_login_is_verified(self) -> None:
        payload = AppConfig.default().to_dict()
        claude_source = next(
            source
            for source in payload["model_ingress"]["sources"]
            if source["id"] == "claude_local"
        )
        claude_connection = next(
            connection
            for connection in payload["model_ingress"]["connections"]
            if connection["id"] == "claude-local-default"
        )
        claude_source["enabled"] = True
        claude_connection["enabled"] = True
        claude_connection.pop("local_login_verified")
        claude_connection["model_candidates"] = [
            {
                "id": "claude-local-default:claude-sonnet-4",
                "connection_id": "claude-local-default",
                "model_id": "claude-sonnet-4",
                "display_name": "Claude Sonnet 4",
                "family_id": "claude-sonnet-4",
                "enabled": True,
                "scan_profile": "default",
                "capabilities": [],
            }
        ]

        config = AppConfig.from_dict(payload)
        migrated_source = next(
            source
            for source in config.model_ingress.sources
            if source.id == "claude_local"
        )
        migrated_connection = next(
            connection
            for connection in config.model_ingress.connections
            if connection.id == "claude-local-default"
        )

        self.assertFalse(migrated_source.enabled)
        self.assertFalse(migrated_connection.enabled)
        self.assertFalse(migrated_connection.local_login_verified)
        self.assertEqual(
            [candidate.scan_profile for candidate in migrated_connection.model_candidates],
            ["low", "medium", "high"],
        )
        self.assertTrue(all(not candidate.enabled for candidate in migrated_connection.model_candidates))

    def test_load_backfills_grok_build_local_source_for_existing_config(self) -> None:
        payload = AppConfig.default().to_dict()
        payload["model_ingress"]["sources"] = [
            source
            for source in payload["model_ingress"]["sources"]
            if source["id"] != "grok_local"
        ]
        payload["model_ingress"]["connections"] = [
            connection
            for connection in payload["model_ingress"]["connections"]
            if connection["id"] != "grok-local-default"
        ]

        config = AppConfig.from_dict(payload)

        grok_source = next(
            source
            for source in config.model_ingress.sources
            if source.id == "grok_local"
        )
        grok_connection = next(
            connection
            for connection in config.model_ingress.connections
            if connection.id == "grok-local-default"
        )
        self.assertFalse(grok_source.enabled)
        self.assertFalse(grok_connection.enabled)
        self.assertEqual(
            [candidate.model_id for candidate in grok_connection.model_candidates],
            ["grok-4.5", "grok-4.5", "grok-4.5"],
        )

    def test_load_migrates_legacy_grok_default_to_explicit_profiles(self) -> None:
        payload = AppConfig.default().to_dict()
        grok_connection = next(
            connection
            for connection in payload["model_ingress"]["connections"]
            if connection["id"] == "grok-local-default"
        )
        legacy_candidate_id = "grok-local-default:grok-4.5:default"
        grok_connection["model_candidates"] = [
            {
                "id": legacy_candidate_id,
                "connection_id": "grok-local-default",
                "model_id": "grok-4.5",
                "display_name": "Grok 4.5",
                "family_id": "grok-4.5",
                "enabled": True,
                "scan_profile": "default",
                "capabilities": [],
            }
        ]
        payload["recommendation"]["current_model_mode"] = "manual"
        payload["recommendation"]["current_default_candidate_id"] = legacy_candidate_id

        config = AppConfig.from_dict(payload)
        reloaded_connection = next(
            connection
            for connection in config.model_ingress.connections
            if connection.id == "grok-local-default"
        )

        self.assertEqual(
            [candidate.scan_profile for candidate in reloaded_connection.model_candidates],
            ["low", "medium", "high"],
        )
        self.assertEqual(
            [candidate.enabled for candidate in reloaded_connection.model_candidates],
            [False, False, True],
        )
        self.assertEqual(reloaded_connection.model_candidates[-1].id, legacy_candidate_id)
        self.assertEqual(
            config.recommendation.current_default_candidate_id,
            legacy_candidate_id,
        )

    def test_save_round_trips_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            store = ConfigStore(config_path)
            config = store.load()
            config.model_ingress.connections[0].model_candidates[0].enabled = False
            config.scheduler.interval_seconds = 1800
            config.scheduler.mode = "weekly"
            config.scheduler.daily_hour = 9
            config.scheduler.daily_minute = 30
            config.scheduler.weekly_weekday = 1
            config.scheduler.weekly_hour = 20
            config.scheduler.weekly_minute = 45
            config.system.language = "en"

            store.save(config)
            reloaded = store.load()

            self.assertFalse(
                reloaded.model_ingress.connections[0].model_candidates[0].enabled
            )
            self.assertEqual(reloaded.scheduler.mode, "weekly")
            self.assertEqual(reloaded.scheduler.interval_seconds, 1800)
            self.assertEqual(reloaded.scheduler.daily_hour, 9)
            self.assertEqual(reloaded.scheduler.daily_minute, 30)
            self.assertEqual(reloaded.scheduler.weekly_weekday, 1)
            self.assertEqual(reloaded.scheduler.weekly_hour, 20)
            self.assertEqual(reloaded.scheduler.weekly_minute, 45)
            self.assertEqual(reloaded.system.language, "en")

    def test_load_backfills_scheduler_defaults_for_legacy_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                """
                {
                  "targets": [],
                  "scheduler": {
                    "mode": "daily",
                    "interval_seconds": 1800
                  },
                  "system": {
                    "use_mock_results": false,
                    "auto_open_browser": true,
                    "history_limit": 50,
                    "language": "zh-CN"
                  },
                  "rules": {}
                }
                """.strip(),
                encoding="utf-8",
            )
            store = ConfigStore(config_path)

            config = store.load()

            self.assertEqual(config.scheduler.mode, "daily")
            self.assertEqual(config.scheduler.interval_seconds, 1800)
            self.assertEqual(config.scheduler.daily_hour, 9)
            self.assertEqual(config.scheduler.daily_minute, 0)
            self.assertEqual(config.scheduler.weekly_weekday, 1)
            self.assertEqual(config.scheduler.weekly_hour, 9)
            self.assertEqual(config.scheduler.weekly_minute, 0)

    def test_scheduler_migrates_manual_to_disabled_without_losing_schedule(self) -> None:
        manual = SchedulerConfig.from_dict({
            "mode": "manual",
            "daily_hour": 7,
            "daily_minute": 30,
            "interval_seconds": 60,
        })
        weekly = SchedulerConfig.from_dict({"mode": "weekly"})

        self.assertFalse(manual.enabled)
        self.assertEqual(manual.mode, "daily")
        self.assertEqual(manual.daily_hour, 7)
        self.assertEqual(manual.daily_minute, 30)
        self.assertEqual(manual.interval_seconds, 1800)
        self.assertTrue(weekly.enabled)
        self.assertEqual(weekly.mode, "weekly")

    def test_scheduler_round_trips_optional_evaluation_profile(self) -> None:
        scheduler = SchedulerConfig.from_dict({
            "enabled": True,
            "mode": "daily",
            "scheduled_evaluation_profile_id": "quick",
        })

        self.assertEqual(scheduler.scheduled_evaluation_profile_id, "quick")
        self.assertEqual(
            scheduler.to_dict()["scheduled_evaluation_profile_id"],
            "quick",
        )
        self.assertIsNone(
            SchedulerConfig.from_dict({"mode": "daily"})
            .scheduled_evaluation_profile_id
        )


class ConfigStoreIngressTest(unittest.TestCase):
    def test_candidate_identity_and_current_default_round_trip(self) -> None:
        payload = AppConfig.default().to_dict()
        candidate = payload["model_ingress"]["connections"][0]["model_candidates"][0]
        candidate["family_id"] = "gpt-5.4"
        candidate["variant_id"] = None
        payload["recommendation"] = {
            "current_default_candidate_id": candidate["id"],
        }

        config = AppConfig.from_dict(payload)
        reloaded_candidate = config.model_ingress.connections[0].model_candidates[0]

        self.assertEqual(reloaded_candidate.family_id, "gpt-5.4")
        self.assertIsNone(reloaded_candidate.variant_id)
        self.assertEqual(
            config.recommendation.current_default_candidate_id,
            candidate["id"],
        )
        self.assertEqual(
            config.to_dict()["recommendation"]["current_default_candidate_id"],
            candidate["id"],
        )

    def test_legacy_candidate_identity_is_backfilled_by_catalog_when_available(self) -> None:
        payload = AppConfig.default().to_dict()
        payload.pop("recommendation", None)
        candidate = payload["model_ingress"]["connections"][0]["model_candidates"][0]
        candidate.pop("family_id", None)
        candidate.pop("variant_id", None)
        original_candidate_id = candidate["id"]

        config = AppConfig.from_dict(payload)
        reloaded_candidate = config.model_ingress.connections[0].model_candidates[0]

        self.assertEqual(reloaded_candidate.id, original_candidate_id)
        self.assertEqual(reloaded_candidate.family_id, "gpt-5.4")
        self.assertIsNone(reloaded_candidate.variant_id)
        self.assertIsNone(config.recommendation.current_default_candidate_id)

    def test_load_defaults_include_local_and_api_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConfigStore(Path(temp_dir) / "config.json")

            config = store.load()

            self.assertEqual(
                [source.id for source in config.model_ingress.sources],
                [
                    "codex_local",
                    "claude_local",
                    "grok_local",
                    "custom_endpoint",
                ],
            )
            self.assertEqual(
                [
                    candidate.model_id
                    for candidate in config.model_ingress.local_model_candidates()
                ],
                [
                    "gpt-5.4",
                    "gpt-5.4",
                    "gpt-5.4",
                    "gpt-5.5",
                    "gpt-5.5",
                    "gpt-5.5",
                    "gpt-5.6-sol",
                    "gpt-5.6-sol",
                    "gpt-5.6-sol",
                    "gpt-5.6-luna",
                    "gpt-5.6-luna",
                    "gpt-5.6-luna",
                    "gpt-5.6-terra",
                    "gpt-5.6-terra",
                    "gpt-5.6-terra",
                    "sonnet",
                    "sonnet",
                    "sonnet",
                    "grok-4.5",
                    "grok-4.5",
                    "grok-4.5",
                ],
            )
            self.assertEqual(config.model_ingress.api_connections(), [])
            self.assertEqual(
                sum(
                    len(connection.model_candidates)
                    for connection in config.model_ingress.connections
                ),
                21,
            )

    def test_load_existing_ingress_backfills_new_default_candidates_without_changing_existing_switches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            payload = ConfigStore(config_path).load().to_dict()
            codex_connection = payload["model_ingress"]["connections"][0]
            codex_connection["model_candidates"] = [
                candidate
                for candidate in codex_connection["model_candidates"]
                if not str(candidate["model_id"]).startswith("gpt-5.6-")
            ]
            codex_connection["model_candidates"][0]["enabled"] = False
            config_path.write_text(json.dumps(payload), encoding="utf-8")

            config = ConfigStore(config_path).load()
            candidates = config.model_ingress.connections[0].model_candidates

            self.assertFalse(candidates[0].enabled)
            backfilled = [item for item in candidates if item.model_id.startswith("gpt-5.6-")]
            self.assertEqual(len(backfilled), 9)
            self.assertTrue(all(not item.enabled for item in backfilled))

    def test_verified_legacy_claude_candidate_migrates_to_explicit_profiles(self) -> None:
        payload = AppConfig.default().to_dict()
        claude = next(
            item
            for item in payload["model_ingress"]["connections"]
            if item["source_id"] == "claude_local"
        )
        claude["local_login_verified"] = True
        claude["model_candidates"] = [
            {
                "id": "claude-local-default:claude-sonnet-4",
                "connection_id": "claude-local-default",
                "model_id": "claude-sonnet-4",
                "display_name": "Claude Sonnet 4",
                "family_id": "claude-sonnet-4",
                "enabled": True,
                "scan_profile": "default",
                "capabilities": [],
            }
        ]

        config = AppConfig.from_dict(payload)
        candidates = next(
            connection
            for connection in config.model_ingress.connections
            if connection.source_id == "claude_local"
        ).model_candidates

        self.assertEqual(
            [candidate.scan_profile for candidate in candidates],
            ["low", "medium", "high"],
        )
        self.assertEqual(candidates[-1].id, "claude-local-default:claude-sonnet-4")
        self.assertTrue(candidates[-1].enabled)

    def test_save_round_trips_custom_endpoint_connection_and_models(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConfigStore(Path(temp_dir) / "config.json")
            config = store.load()
            config.model_ingress.connections.append(
                ConnectionConfig(
                    id="custom-1",
                    source_id="custom_endpoint",
                    name="Team Gateway",
                    enabled=True,
                    api_format="openai_responses",
                    provider_preset="generic",
                    base_url="http://127.0.0.1:18080/v1/",
                    api_key_ref="env:MODELDIAL_TEST_KEY",
                    notes="team use",
                    model_candidates=[
                        ModelCandidateConfig(
                            id="custom-1:gpt-5.5",
                            connection_id="custom-1",
                            model_id="gpt-5.5",
                            display_name="GPT-5.5",
                            enabled=True,
                            scan_profile="codex_default",
                        )
                    ],
                )
            )

            store.save(config)
            reloaded = store.load()

            self.assertEqual(len(reloaded.model_ingress.api_connections()), 1)
            self.assertEqual(
                reloaded.model_ingress.api_connections()[0].base_url,
                "http://127.0.0.1:18080/v1",
            )
            self.assertEqual(
                reloaded.model_ingress.api_connections()[0].api_format,
                "openai_responses",
            )
            self.assertEqual(
                reloaded.model_ingress.api_connections()[0].api_key_ref,
                "env:MODELDIAL_TEST_KEY",
            )
            self.assertEqual(
                reloaded.model_ingress.api_connections()[0].notes,
                "team use",
            )
            self.assertEqual(
                reloaded.model_ingress.api_connections()[0].model_candidates[0].model_id,
                "gpt-5.5",
            )
            self.assertEqual(
                reloaded.model_ingress.api_connections()[0].model_candidates[0].connection_id,
                "custom-1",
            )
            self.assertTrue(
                reloaded.model_ingress.api_connections()[0].model_candidates[0].enabled
            )
            self.assertEqual(
                reloaded.model_ingress.api_connections()[0].model_candidates[0].scan_profile,
                "codex_default",
            )
            self.assertEqual(
                reloaded.model_ingress.api_connections()[0].provider_id,
                "custom",
            )
            self.assertEqual(
                reloaded.model_ingress.api_connections()[0].provider_display_name,
                "Team Gateway",
            )
            self.assertEqual(
                reloaded.model_ingress.api_connections()[0].auth_mode,
                "api_key",
            )
            self.assertEqual(
                reloaded.model_ingress.api_connections()[0].catalog_source,
                "manual",
            )

    def test_load_legacy_top_level_model_candidates_and_save_new_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                """
                {
                  "model_ingress": {
                    "sources": [
                      {
                        "id": "custom_endpoint",
                        "kind": "custom_endpoint",
                        "title": "Custom",
                        "description": "Custom endpoint",
                        "mode": "api",
                        "enabled": true
                      }
                    ],
                    "connections": [
                      {
                        "id": "custom-1",
                        "source_id": "custom_endpoint",
                        "name": "Team Gateway",
                        "enabled": true,
                        "api_format": "openai_responses",
                        "base_url": "http://127.0.0.1:18080/v1",
                        "api_key_ref": "env:MODELDIAL_TEST_KEY",
                        "notes": "team use"
                      }
                    ],
                    "model_candidates": [
                      {
                        "id": "custom-1:gpt-5.5",
                        "connection_id": "custom-1",
                        "model_id": "gpt-5.5",
                        "display_name": "GPT-5.5",
                        "enabled": true,
                        "scan_profile": "codex_default"
                      }
                    ]
                  },
                  "scheduler": {
                    "mode": "manual",
                    "interval_seconds": 600
                  },
                  "system": {
                    "use_mock_results": false,
                    "auto_open_browser": true,
                    "history_limit": 50,
                    "language": "zh-CN"
                  },
                  "rules": {}
                }
                """.strip(),
                encoding="utf-8",
            )
            store = ConfigStore(config_path)

            config = store.load()

            self.assertEqual(len(config.model_ingress.connections), 1)
            self.assertEqual(
                len(config.model_ingress.connections[0].model_candidates),
                1,
            )
            self.assertEqual(
                config.model_ingress.connections[0].model_candidates[0].connection_id,
                "custom-1",
            )

            store.save(config)
            saved_payload = json.loads(config_path.read_text(encoding="utf-8"))

            self.assertIn("model_ingress", saved_payload)
            self.assertNotIn(
                "model_candidates",
                saved_payload["model_ingress"],
            )
            self.assertEqual(
                saved_payload["model_ingress"]["connections"][0]["model_candidates"][0][
                    "connection_id"
                ],
                "custom-1",
            )

    def test_legacy_openrouter_connection_migrates_without_changing_identity(self) -> None:
        payload = AppConfig.default().to_dict()
        payload["model_ingress"]["sources"].append(
            {
                "id": "openrouter_api",
                "kind": "openrouter",
                "title": "OpenRouter",
                "description": "legacy",
                "mode": "api",
                "enabled": True,
            }
        )
        payload["model_ingress"]["connections"].append(
            {
                "id": "or-1",
                "source_id": "openrouter_api",
                "name": "OpenRouter",
                "enabled": True,
                "api_format": "openai_chat",
                "base_url": "https://openrouter.ai/api/v1/",
                "api_key_ref": "env:OPENROUTER_API_KEY",
                "model_candidates": [
                    {
                        "id": "or-1:openai/gpt-5.6:high",
                        "connection_id": "or-1",
                        "model_id": "openai/gpt-5.6",
                        "display_name": "GPT-5.6 High",
                        "enabled": False,
                        "scan_profile": "high",
                        "capabilities": [],
                    }
                ],
            }
        )

        config = AppConfig.from_dict(payload)
        connection = next(
            item for item in config.model_ingress.connections if item.id == "or-1"
        )

        self.assertEqual(connection.source_id, "custom_endpoint")
        self.assertEqual(connection.provider_preset, "openrouter")
        self.assertEqual(connection.provider_id, "openrouter")
        self.assertEqual(connection.provider_display_name, "OpenRouter")
        self.assertEqual(connection.auth_mode, "api_key")
        self.assertEqual(connection.catalog_source, "catalog_inferred")
        self.assertEqual(connection.api_format, "openai_chat_completions")
        self.assertEqual(connection.base_url, "https://openrouter.ai/api/v1")
        self.assertEqual(
            connection.model_candidates[0].id,
            "or-1:openai/gpt-5.6:high",
        )

    def test_known_provider_connection_infers_catalog_identity_from_base_url(self) -> None:
        payload = AppConfig.default().to_dict()
        payload["model_ingress"]["connections"].append(
            {
                "id": "deepseek",
                "source_id": "custom_endpoint",
                "name": "DeepSeek V4",
                "enabled": True,
                "api_format": "openai_chat_completions",
                "provider_preset": "generic",
                "base_url": "https://api.deepseek.com",
                "api_key_ref": "env:DEEPSEEK_API_KEY",
                "model_candidates": [
                    {
                        "id": "deepseek:deepseek-v4-flash:default",
                        "connection_id": "deepseek",
                        "model_id": "deepseek-v4-flash",
                        "display_name": "deepseek-v4-flash",
                        "enabled": False,
                        "scan_profile": "default",
                        "capabilities": [],
                    }
                ],
            }
        )

        config = AppConfig.from_dict(payload)
        connection = next(
            item for item in config.model_ingress.connections if item.id == "deepseek"
        )

        self.assertEqual(connection.provider_id, "deepseek")
        self.assertEqual(connection.provider_display_name, "DeepSeek")
        self.assertEqual(connection.auth_mode, "api_key")
        self.assertEqual(connection.catalog_source, "catalog_inferred")
        self.assertEqual(
            [candidate.scan_profile for candidate in connection.model_candidates],
            ["low", "high", "max"],
        )
        self.assertEqual(connection.model_candidates[0].family_id, "deepseek-v4")
        self.assertEqual(connection.model_candidates[0].variant_id, "flash")
        self.assertNotIn(
            "none",
            [candidate.scan_profile for candidate in connection.model_candidates],
        )

    def test_plaintext_api_key_reference_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "plaintext API key references"):
            ConnectionConfig(
                id="custom-1",
                source_id="custom_endpoint",
                name="Unsafe",
                enabled=True,
                api_format="openai_chat_completions",
                base_url="https://example.com/v1",
                api_key_ref="plaintext:test-key",
            )

    def test_legacy_auto_generated_deepseek_candidates_migrate_to_official_efforts(self) -> None:
        payload = AppConfig.default().to_dict()
        payload["model_ingress"]["connections"].append(
            {
                "id": "deepseek",
                "source_id": "custom_endpoint",
                "name": "DeepSeek V4",
                "enabled": True,
                "api_format": "openai_chat_completions",
                "provider_preset": "generic",
                "base_url": "https://api.deepseek.com",
                "api_key_ref": "env:DEEPSEEK_API_KEY",
                "model_candidates": [
                    {
                        "id": f"deepseek:deepseek-v4-flash:{profile}",
                        "connection_id": "deepseek",
                        "model_id": "deepseek-v4-flash",
                        "display_name": f"deepseek-v4-flash {profile}",
                        "enabled": False,
                        "scan_profile": profile,
                        "capabilities": ["reasoning"],
                    }
                    for profile in ("medium", "high", "xhigh")
                ],
            }
        )

        config = AppConfig.from_dict(payload)
        connection = next(
            item for item in config.model_ingress.connections if item.id == "deepseek"
        )

        self.assertEqual(
            [candidate.scan_profile for candidate in connection.model_candidates],
            ["low", "high", "max"],
        )
        self.assertTrue(all(
            candidate.model_id == "deepseek-v4-flash"
            for candidate in connection.model_candidates
        ))
        self.assertTrue(all(
            candidate.capabilities == ["reasoning"]
            for candidate in connection.model_candidates
        ))
        self.assertFalse(any(
            candidate.enabled for candidate in connection.model_candidates
        ))

    def test_anthropic_messages_default_candidate_expands_to_native_efforts(self) -> None:
        payload = AppConfig.default().to_dict()
        payload["model_ingress"]["connections"].append(
            {
                "id": "claude-api",
                "source_id": "custom_endpoint",
                "name": "Claude",
                "enabled": True,
                "api_format": "anthropic_messages",
                "provider_preset": "generic",
                "base_url": "https://example.com/v1",
                "api_key_ref": "env:CLAUDE_API_KEY",
                "model_candidates": [
                    {
                        "id": "claude-api:claude-fable-5:default",
                        "connection_id": "claude-api",
                        "model_id": "claude-fable-5",
                        "display_name": "claude-fable-5",
                        "enabled": True,
                        "scan_profile": "default",
                        "capabilities": [],
                    }
                ],
            }
        )

        config = AppConfig.from_dict(payload)
        connection = next(
            item for item in config.model_ingress.connections
            if item.id == "claude-api"
        )

        self.assertEqual(
            [candidate.scan_profile for candidate in connection.model_candidates],
            ["low", "medium", "high", "xhigh", "max"],
        )
        self.assertEqual(
            [candidate.enabled for candidate in connection.model_candidates],
            [False, False, True, False, False],
        )
        self.assertTrue(all(
            "reasoning_tokens_unavailable" in candidate.capabilities
            for candidate in connection.model_candidates
        ))

    def test_kimi_k3_default_candidate_expands_to_native_efforts(self) -> None:
        payload = AppConfig.default().to_dict()
        payload["model_ingress"]["connections"].append(
            {
                "id": "kimi-coding",
                "source_id": "custom_endpoint",
                "name": "Moonshot",
                "enabled": True,
                "api_format": "openai_chat_completions",
                "provider_preset": "custom",
                "base_url": "https://api.kimi.com/coding/v1",
                "api_key_ref": "env:KIMI_API_KEY",
                "model_candidates": [
                    {
                        "id": "kimi-coding:k3:default",
                        "connection_id": "kimi-coding",
                        "model_id": "k3",
                        "display_name": "k3",
                        "enabled": True,
                        "scan_profile": "default",
                        "capabilities": [],
                    },
                    {
                        "id": "kimi-coding:kimi-k2.7-code:default",
                        "connection_id": "kimi-coding",
                        "model_id": "kimi-k2.7-code",
                        "display_name": "kimi-k2.7-code",
                        "enabled": False,
                        "scan_profile": "default",
                        "capabilities": [],
                    },
                ],
            }
        )

        config = AppConfig.from_dict(payload)
        connection = next(
            item for item in config.model_ingress.connections
            if item.id == "kimi-coding"
        )
        k3_candidates = [
            candidate for candidate in connection.model_candidates
            if candidate.model_id == "k3"
        ]
        other_candidates = [
            candidate for candidate in connection.model_candidates
            if candidate.model_id == "kimi-k2.7-code"
        ]

        self.assertEqual(
            [candidate.scan_profile for candidate in k3_candidates],
            ["low", "high", "max"],
        )
        self.assertEqual(
            [candidate.enabled for candidate in k3_candidates],
            [False, True, False],
        )
        self.assertTrue(all(
            "reasoning_tokens_unavailable" not in candidate.capabilities
            for candidate in k3_candidates
        ))
        self.assertEqual(len(other_candidates), 1)
        self.assertEqual(other_candidates[0].scan_profile, "default")

    def test_manual_api_reasoning_profiles_remain_authoritative(self) -> None:
        payload = AppConfig.default().to_dict()
        payload["model_ingress"]["connections"].append(
            {
                "id": "custom-api",
                "source_id": "custom_endpoint",
                "name": "Custom Gateway",
                "enabled": True,
                "api_format": "openai_chat_completions",
                "provider_preset": "generic",
                "base_url": "https://example.com/v1",
                "api_key_ref": "env:CUSTOM_API_KEY",
                "model_candidates": [
                    {
                        "id": f"custom-api:model-a:{profile}",
                        "connection_id": "custom-api",
                        "model_id": "model-a",
                        "display_name": "model-a",
                        "family_id": "model-a",
                        "variant_id": profile,
                        "enabled": profile == "high",
                        "scan_profile": profile,
                        "capabilities": ["reasoning"],
                    }
                    for profile in ("medium", "high", "xhigh")
                ],
            }
        )

        config = AppConfig.from_dict(payload)
        connection = next(
            item for item in config.model_ingress.connections if item.id == "custom-api"
        )

        self.assertEqual(
            [candidate.scan_profile for candidate in connection.model_candidates],
            ["medium", "high", "xhigh"],
        )

    def test_duplicate_api_connections_for_same_provider_and_endpoint_are_merged(self) -> None:
        payload = AppConfig.default().to_dict()
        payload["model_ingress"]["connections"].extend(
            [
                {
                    "id": "deepseek-flash",
                    "source_id": "custom_endpoint",
                    "name": "deepseek",
                    "enabled": True,
                    "api_format": "openai_chat_completions",
                    "provider_preset": "generic",
                    "base_url": "https://api.deepseek.com",
                    "api_key_ref": "keychain:deepseek-flash",
                    "model_candidates": [
                        {
                            "id": "deepseek-flash:deepseek-v4-flash:default",
                            "connection_id": "deepseek-flash",
                            "model_id": "deepseek-v4-flash",
                            "display_name": "deepseek-v4-flash",
                            "enabled": True,
                            "scan_profile": "default",
                            "capabilities": [],
                        }
                    ],
                },
                {
                    "id": "deepseek-pro",
                    "source_id": "custom_endpoint",
                    "name": "DeepSeek",
                    "enabled": True,
                    "api_format": "openai_chat_completions",
                    "provider_preset": "generic",
                    "base_url": "https://api.deepseek.com/",
                    "api_key_ref": "keychain:deepseek-pro",
                    "model_candidates": [
                        {
                            "id": "deepseek-pro:deepseek-v4-pro:default",
                            "connection_id": "deepseek-pro",
                            "model_id": "deepseek-v4-pro",
                            "display_name": "deepseek-v4-pro",
                            "enabled": True,
                            "scan_profile": "default",
                            "capabilities": [],
                        }
                    ],
                },
            ]
        )

        config = AppConfig.from_dict(payload)
        deepseek_connections = [
            connection
            for connection in config.model_ingress.api_connections()
            if connection.name.casefold() == "deepseek"
        ]

        self.assertEqual(len(deepseek_connections), 1)
        connection = deepseek_connections[0]
        self.assertEqual(connection.id, "deepseek-flash")
        self.assertEqual(
            [candidate.model_id for candidate in connection.model_candidates],
            [
                "deepseek-v4-flash",
                "deepseek-v4-flash",
                "deepseek-v4-flash",
                "deepseek-v4-pro",
                "deepseek-v4-pro",
            ],
        )
        self.assertEqual(
            [candidate.scan_profile for candidate in connection.model_candidates],
            ["low", "high", "max", "high", "max"],
        )
        self.assertTrue(
            all(
                candidate.connection_id == connection.id
                for candidate in connection.model_candidates
            )
        )
        self.assertEqual(
            {candidate.family_id for candidate in connection.model_candidates},
            {"deepseek-v4"},
        )
        self.assertEqual(
            {candidate.variant_id for candidate in connection.model_candidates},
            {"flash", "pro"},
        )
        self.assertEqual(connection.api_key_ref, "keychain:deepseek-flash")


if __name__ == "__main__":
    unittest.main()
