from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scanner.active_run_store import ActiveRunStore
from scanner.config_application import ConfigCommand, ConfigMutationConflict
from scanner.config_commands import apply_config_patch
from scanner.config_store import ConfigStore
from scanner.history_store import HistoryStore
from scanner.models import AppConfig, ConnectionConfig, ModelCandidateConfig
from scanner.native_bridge import patch_config


class ConfigPatchCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.config_store = ConfigStore(self.root / "config.json")
        self.config_store.save(AppConfig.default())

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def apply(self, operation: str, arguments: dict[str, object]) -> AppConfig:
        config, applied_operation = apply_config_patch(
            self.config_store,
            {
                "schema_version": 1,
                "operation": operation,
                "arguments": arguments,
            },
            valid_evaluation_profile_ids={"quick", "full"},
        )
        self.assertEqual(applied_operation, operation)
        return config

    @staticmethod
    def endpoint_candidate(
        connection_id: str,
        model_id: str,
        profile: str,
        *,
        enabled: bool,
    ) -> dict[str, object]:
        return {
            "id": f"{connection_id}:{model_id}:{profile}",
            "connection_id": connection_id,
            "model_id": model_id,
            "display_name": model_id,
            "family_id": model_id,
            "variant_id": None if profile == "default" else profile,
            "enabled": enabled,
            "scan_profile": profile,
            "capabilities": [] if profile == "default" else ["reasoning"],
        }

    def test_supports_every_current_settings_patch_without_whole_config_input(self) -> None:
        connection_id = "codex-local-default"
        candidate_id = "codex-local-default:gpt-5.4:medium"
        operations = [
            (
                "model_candidates_enabled",
                {
                    "connection_id": connection_id,
                    "candidate_ids": [candidate_id],
                    "enabled": False,
                },
            ),
            (
                "connection_enabled",
                {"connection_id": connection_id, "enabled": False},
            ),
            (
                "add_discovered_local_candidate",
                {
                    "connection_id": connection_id,
                    "model_id": "gpt-new",
                    "display_name": "GPT New High",
                    "scan_profile": "high",
                },
            ),
            ("current_default", {"candidate_id": candidate_id}),
            ("automatic_current_model", {}),
            ("recommendation_preference", {"preference": "quality"}),
            (
                "source_mode",
                {
                    "source_mode": "local_evaluation",
                    "configuration_id": candidate_id,
                },
            ),
            (
                "project_task_profile",
                {"name": "  支付重构  ", "task_mode": "重构维护"},
            ),
            (
                "scan_budget",
                {
                    "enabled": True,
                    "max_duration_seconds": 12,
                    "max_reference_cost_usd": 0,
                },
            ),
            (
                "scan_execution",
                {
                    "max_concurrent_targets": 0,
                    "execution_timeout_seconds": 15,
                    "timeout_retry_count": -1,
                },
            ),
            ("scheduler", {"mode": "interval", "interval_seconds": 60}),
            ("scheduler_enabled", {"enabled": True}),
            ("scheduler_mode", {"mode": "daily"}),
            ("daily_schedule", {"hour": 7, "minute": 45}),
            (
                "weekly_schedule",
                {"weekday": 5, "hour": 18, "minute": 30},
            ),
            ("scheduled_evaluation_profile", {"profile_id": "quick"}),
        ]

        for operation, arguments in operations:
            with self.subTest(operation=operation):
                self.apply(operation, arguments)

        config = self.config_store.load()
        connection = next(
            item
            for item in config.model_ingress.connections
            if item.id == connection_id
        )
        candidate = next(
            item for item in connection.model_candidates if item.id == candidate_id
        )
        discovered = next(
            item
            for item in connection.model_candidates
            if item.model_id == "gpt-new"
        )
        self.assertFalse(connection.enabled)
        self.assertFalse(candidate.enabled)
        self.assertEqual(discovered.id, f"{connection_id}:gpt-new:high")
        self.assertFalse(discovered.enabled)
        self.assertEqual(discovered.capabilities, ["reasoning"])
        self.assertEqual(
            config.recommendation.current_default_candidate_id,
            candidate_id,
        )
        self.assertEqual(config.recommendation.current_model_mode, "auto")
        self.assertEqual(config.recommendation.preference, "quality")
        self.assertEqual(
            config.recommendation.source_mode_by_configuration_id,
            {candidate_id: "local_evaluation"},
        )
        self.assertEqual(
            config.recommendation.project_profile.project_name,
            "支付重构",
        )
        self.assertEqual(
            config.recommendation.project_profile.task_mode,
            "重构维护",
        )
        self.assertTrue(config.scan_budget.enabled)
        self.assertEqual(config.scan_budget.max_duration_seconds, 60)
        self.assertEqual(config.scan_budget.max_reference_cost_usd, 0.01)
        self.assertEqual(config.system.max_concurrent_targets, 1)
        self.assertEqual(config.system.execution_timeout_seconds, 60)
        self.assertEqual(config.system.timeout_retry_count, 0)
        self.assertTrue(config.scheduler.enabled)
        self.assertEqual(config.scheduler.mode, "weekly")
        self.assertEqual(config.scheduler.interval_seconds, 1800)
        self.assertEqual(config.scheduler.daily_hour, 7)
        self.assertEqual(config.scheduler.daily_minute, 45)
        self.assertEqual(config.scheduler.weekly_weekday, 5)
        self.assertEqual(config.scheduler.weekly_hour, 18)
        self.assertEqual(config.scheduler.weekly_minute, 30)
        self.assertEqual(config.scheduler.scheduled_evaluation_profile_id, "quick")

    def test_current_default_null_clears_candidate_and_restores_auto_mode(self) -> None:
        candidate_id = "codex-local-default:gpt-5.4:medium"
        self.apply("current_default", {"candidate_id": candidate_id})

        config = self.apply("current_default", {"candidate_id": None})

        self.assertIsNone(config.recommendation.current_default_candidate_id)
        self.assertEqual(config.recommendation.current_model_mode, "auto")

    def test_enabling_new_api_candidate_invalidates_connection_verification(self) -> None:
        config = self.config_store.load()
        config.model_ingress.connections.append(
            ConnectionConfig(
                id="api-test",
                source_id="custom_endpoint",
                name="API Test",
                enabled=True,
                api_format="openai_responses",
                base_url="https://api.example.test/v1",
                last_test_status="pass",
                last_test_message="ok",
                model_candidates=[
                    ModelCandidateConfig(
                        id="api-test:model-a:high",
                        connection_id="api-test",
                        model_id="model-a",
                        display_name="Model A High",
                        enabled=False,
                        scan_profile="high",
                    )
                ],
            )
        )
        self.config_store.save(config)

        updated = self.apply(
            "model_candidates_enabled",
            {
                "connection_id": "api-test",
                "candidate_ids": ["api-test:model-a:high"],
                "enabled": True,
            },
        )

        connection = next(
            item
            for item in updated.model_ingress.connections
            if item.id == "api-test"
        )
        self.assertEqual(connection.last_test_status, "untested")
        self.assertEqual(connection.last_test_message, "启用范围已变更，请重新测试")

    def test_endpoint_mutations_use_typed_patches_without_whole_config_replacement(
        self,
    ) -> None:
        connection_id = "endpoint-test"
        first_candidate = self.endpoint_candidate(
            connection_id,
            "model-a",
            "default",
            enabled=True,
        )
        created = self.apply(
            "upsert_endpoint_connection",
            {
                "connection_id": connection_id,
                "name": "Endpoint Test",
                "provider_preset": "generic",
                "api_format": "openai_responses",
                "base_url": "https://api.example.test/v1/",
                "api_key_ref": "keychain:created",
                "enabled": True,
                "model_candidates": [first_candidate],
                "last_test_status": "ok",
                "last_test_at": "2026-07-28T12:00:00+08:00",
                "last_test_message": "verified",
            },
        )
        connection = next(
            item
            for item in created.model_ingress.connections
            if item.id == connection_id
        )
        self.assertEqual(connection.base_url, "https://api.example.test/v1")
        self.assertEqual(connection.last_test_status, "ok")

        changed = self.apply(
            "upsert_endpoint_connection",
            {
                "connection_id": connection_id,
                "name": "Endpoint Test",
                "provider_preset": "generic",
                "api_format": "openai_responses",
                "base_url": "https://api.changed.test/v1",
                "api_key_ref": "keychain:created",
                "enabled": True,
                "model_candidates": [first_candidate],
                "last_test_status": None,
                "last_test_at": None,
                "last_test_message": None,
            },
        )
        connection = next(
            item
            for item in changed.model_ingress.connections
            if item.id == connection_id
        )
        self.assertEqual(connection.last_test_status, "untested")
        self.assertEqual(connection.last_test_message, "连接信息已变更，请重新测试")

        second_candidates = [
            self.endpoint_candidate(
                connection_id,
                "model-b",
                profile,
                enabled=profile == "high",
            )
            for profile in ("medium", "high")
        ]
        added = self.apply(
            "add_model_candidates",
            {
                "connection_id": connection_id,
                "model_candidates": second_candidates,
            },
        )
        connection = next(
            item
            for item in added.model_ingress.connections
            if item.id == connection_id
        )
        self.assertEqual(
            {candidate.id for candidate in connection.model_candidates},
            {
                first_candidate["id"],
                *(candidate["id"] for candidate in second_candidates),
            },
        )

        selected_id = str(second_candidates[1]["id"])
        self.apply("current_default", {"candidate_id": selected_id})
        self.apply(
            "source_mode",
            {
                "source_mode": "local_evaluation",
                "configuration_id": selected_id,
            },
        )
        removed = self.apply(
            "remove_model_candidates",
            {
                "connection_id": connection_id,
                "candidate_ids": [selected_id],
            },
        )
        self.assertIsNone(removed.recommendation.current_default_candidate_id)
        self.assertEqual(removed.recommendation.current_model_mode, "auto")
        self.assertNotIn(
            selected_id,
            removed.recommendation.source_mode_by_configuration_id,
        )

        migrated = self.apply(
            "connection_secret_references",
            {
                "references_by_connection_id": {
                    connection_id: "keychain:migrated",
                }
            },
        )
        connection = next(
            item
            for item in migrated.model_ingress.connections
            if item.id == connection_id
        )
        self.assertEqual(connection.api_key_ref, "keychain:migrated")

        deleted = self.apply(
            "delete_connection",
            {"connection_id": connection_id},
        )
        self.assertNotIn(
            connection_id,
            {item.id for item in deleted.model_ingress.connections},
        )

    def test_endpoint_patch_rejects_cross_connection_and_plaintext_secret_input(
        self,
    ) -> None:
        before = self.config_store.path.read_bytes()
        invalid_candidate = self.endpoint_candidate(
            "other-connection",
            "model-a",
            "default",
            enabled=True,
        )
        with self.assertRaisesRegex(ValueError, "connection_id must match"):
            self.apply(
                "upsert_endpoint_connection",
                {
                    "connection_id": "endpoint-test",
                    "name": "Endpoint Test",
                    "provider_preset": "generic",
                    "api_format": "openai_responses",
                    "base_url": "https://api.example.test/v1",
                    "api_key_ref": "keychain:test",
                    "enabled": True,
                    "model_candidates": [invalid_candidate],
                    "last_test_status": None,
                    "last_test_at": None,
                    "last_test_message": None,
                },
            )
        self.assertEqual(self.config_store.path.read_bytes(), before)

        with self.assertRaisesRegex(ValueError, "plaintext API key"):
            self.apply(
                "connection_secret_references",
                {
                    "references_by_connection_id": {
                        "codex-local-default": "plaintext:secret",
                    }
                },
            )
        self.assertEqual(self.config_store.path.read_bytes(), before)

    def test_rejects_unknown_fields_types_operations_and_identifiers_without_write(self) -> None:
        before = self.config_store.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "config patch must be an object"):
            apply_config_patch(
                self.config_store,
                [],  # type: ignore[arg-type]
                valid_evaluation_profile_ids={"quick", "full"},
            )
        invalid_payloads = [
            {
                "schema_version": 1,
                "operation": "automatic_current_model",
                "arguments": {},
                "config": {},
            },
            {
                "schema_version": True,
                "operation": "automatic_current_model",
                "arguments": {},
            },
            {
                "schema_version": 2,
                "operation": "automatic_current_model",
                "arguments": {},
            },
            {"schema_version": 1, "operation": "unknown", "arguments": {}},
            {
                "schema_version": 1,
                "operation": "automatic_current_model",
                "arguments": [],
            },
            {
                "schema_version": 1,
                "operation": "scheduler_enabled",
                "arguments": {"enabled": 1},
            },
            {
                "schema_version": 1,
                "operation": "connection_enabled",
                "arguments": {"connection_id": "missing", "enabled": True},
            },
            {
                "schema_version": 1,
                "operation": "model_candidates_enabled",
                "arguments": {
                    "connection_id": "codex-local-default",
                    "candidate_ids": ["missing"],
                    "enabled": True,
                },
            },
            {
                "schema_version": 1,
                "operation": "current_default",
                "arguments": {"candidate_id": "missing"},
            },
            {
                "schema_version": 1,
                "operation": "source_mode",
                "arguments": {
                    "source_mode": "auto",
                    "configuration_id": "missing",
                },
            },
            {
                "schema_version": 1,
                "operation": "scheduled_evaluation_profile",
                "arguments": {"profile_id": "missing"},
            },
            {
                "schema_version": 1,
                "operation": "daily_schedule",
                "arguments": {"hour": 24, "minute": 0},
            },
            {
                "schema_version": 1,
                "operation": "scheduler_enabled",
                "arguments": {"enabled": True, "unexpected": False},
            },
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    apply_config_patch(
                        self.config_store,
                        payload,
                        valid_evaluation_profile_ids={"quick", "full"},
                    )
                self.assertEqual(self.config_store.path.read_bytes(), before)

    def test_config_store_update_serializes_read_modify_write_and_reads_latest(self) -> None:
        first_entered = threading.Event()
        release_first = threading.Event()
        second_entered = threading.Event()
        second_observed_preferences: list[str] = []
        errors: list[BaseException] = []
        first_store = ConfigStore(self.config_store.path)
        second_store = ConfigStore(self.config_store.path)

        def first_update(config: AppConfig) -> AppConfig:
            first_entered.set()
            if not release_first.wait(timeout=2):
                raise TimeoutError("test did not release first update")
            config.recommendation.preference = "quality"
            return config

        def second_update(config: AppConfig) -> AppConfig:
            second_observed_preferences.append(config.recommendation.preference)
            second_entered.set()
            config.scheduler.enabled = True
            return config

        def run(update) -> None:
            try:
                update()
            except BaseException as error:
                errors.append(error)

        first_thread = threading.Thread(
            target=run,
            args=(lambda: first_store.update(first_update),),
        )
        second_thread = threading.Thread(
            target=run,
            args=(lambda: second_store.update(second_update),),
        )
        first_thread.start()
        self.assertTrue(first_entered.wait(timeout=1))
        second_thread.start()
        self.assertFalse(second_entered.wait(timeout=0.1))
        release_first.set()
        first_thread.join(timeout=3)
        second_thread.join(timeout=3)

        self.assertFalse(first_thread.is_alive())
        self.assertFalse(second_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(second_observed_preferences, ["quality"])
        config = self.config_store.load()
        self.assertEqual(config.recommendation.preference, "quality")
        self.assertTrue(config.scheduler.enabled)

    def test_native_bridge_returns_the_authoritative_command_snapshot(self) -> None:
        authoritative_state = {
            "config": {"authoritative": True},
            "runtime": {"lifecycle_state": "idle"},
        }
        with patch(
            "scanner.native_bridge._build_command_snapshot",
            return_value=authoritative_state,
        ):
            response = patch_config(
                {
                    "schema_version": 1,
                    "operation": "recommendation_preference",
                    "arguments": {"preference": "cost"},
                },
                config_store=self.config_store,
                history_store=HistoryStore(self.root / "history.jsonl"),
                active_run_store=ActiveRunStore(self.root / "active_run.json"),
            )

        self.assertEqual(response["schema_version"], 1)
        self.assertIs(response["ok"], True)
        self.assertEqual(response["action"], "patch_config")
        self.assertEqual(response["operation"], "recommendation_preference")
        self.assertEqual(response["config"], authoritative_state["config"])
        self.assertIs(response["state"], authoritative_state)
        self.assertEqual(
            self.config_store.load().recommendation.preference,
            "cost",
        )

    def test_destructive_patches_reject_active_resumable_and_finalizing_runs(self) -> None:
        connection_id = "endpoint-test"
        candidate = self.endpoint_candidate(
            connection_id,
            "model-a",
            "default",
            enabled=True,
        )
        self.apply(
            "upsert_endpoint_connection",
            {
                "connection_id": connection_id,
                "name": "Endpoint Test",
                "provider_preset": "generic",
                "api_format": "openai_responses",
                "base_url": "https://api.example.test/v1",
                "api_key_ref": "keychain:test",
                "enabled": True,
                "model_candidates": [candidate],
                "last_test_status": "ok",
                "last_test_at": None,
                "last_test_message": "verified",
            },
        )
        active_run_store = ActiveRunStore(self.root / "active_run.json")
        command = ConfigCommand(self.config_store, active_run_store)
        destructive_patches = [
            {
                "schema_version": 1,
                "operation": "delete_connection",
                "arguments": {"connection_id": connection_id},
            },
            {
                "schema_version": 1,
                "operation": "remove_model_candidates",
                "arguments": {
                    "connection_id": connection_id,
                    "candidate_ids": [candidate["id"]],
                },
            },
        ]

        for lifecycle_state in ("active_scan", "paused_recoverable", "finalizing"):
            active_run_store.save(
                {
                    "run_id": "run-active",
                    "runtime": {"lifecycle_state": lifecycle_state},
                    "entries": [{"candidate_id": candidate["id"]}],
                }
            )
            for payload in destructive_patches:
                with self.subTest(
                    lifecycle_state=lifecycle_state,
                    operation=payload["operation"],
                ):
                    before = self.config_store.path.read_bytes()
                    with self.assertRaisesRegex(
                        ConfigMutationConflict,
                        "active or resumable run",
                    ):
                        command.apply_patch(
                            payload,
                            valid_evaluation_profile_ids={"quick", "full"},
                        )
                    self.assertEqual(self.config_store.path.read_bytes(), before)

        active_run_store.save(
            {
                "run_id": "run-active",
                "runtime": {"lifecycle_state": "active_scan"},
                "entries": [{"candidate_id": candidate["id"]}],
            }
        )
        with self.assertRaises(ConfigMutationConflict):
            patch_config(
                destructive_patches[0],
                config_store=self.config_store,
                history_store=HistoryStore(self.root / "history.jsonl"),
                active_run_store=active_run_store,
            )

    def test_destructive_patch_succeeds_when_runtime_is_idle(self) -> None:
        connection_id = "endpoint-idle"
        candidate = self.endpoint_candidate(
            connection_id,
            "model-a",
            "default",
            enabled=True,
        )
        self.apply(
            "upsert_endpoint_connection",
            {
                "connection_id": connection_id,
                "name": "Endpoint Idle",
                "provider_preset": "generic",
                "api_format": "openai_responses",
                "base_url": "https://api.example.test/v1",
                "api_key_ref": "keychain:test",
                "enabled": True,
                "model_candidates": [candidate],
                "last_test_status": "ok",
                "last_test_at": None,
                "last_test_message": "verified",
            },
        )
        active_run_store = ActiveRunStore(self.root / "active_run.json")
        active_run_store.save(
            {
                "run_id": "run-complete",
                "runtime": {"lifecycle_state": "idle"},
                "entries": [],
            }
        )

        config, operation = ConfigCommand(
            self.config_store,
            active_run_store,
        ).apply_patch(
            {
                "schema_version": 1,
                "operation": "delete_connection",
                "arguments": {"connection_id": connection_id},
            },
            valid_evaluation_profile_ids={"quick", "full"},
        )

        self.assertEqual(operation, "delete_connection")
        self.assertNotIn(
            connection_id,
            {connection.id for connection in config.model_ingress.connections},
        )

    def test_destructive_patch_rejects_live_scan_lock_before_active_run_exists(self) -> None:
        connection_id = "endpoint-lock-only"
        self.apply(
            "upsert_endpoint_connection",
            {
                "connection_id": connection_id,
                "name": "Endpoint Lock Only",
                "provider_preset": "generic",
                "api_format": "openai_responses",
                "base_url": "https://api.example.test/v1",
                "api_key_ref": "keychain:test",
                "enabled": True,
                "model_candidates": [],
                "last_test_status": "ok",
                "last_test_at": None,
                "last_test_message": "verified",
            },
        )
        active_run_store = ActiveRunStore(self.root / "active_run.json")
        scan_lock_path = active_run_store.path.with_name("scan.lock")
        scan_lock_path.write_text(
            json.dumps({"pid": os.getpid(), "heartbeat_at": time.time()}),
            encoding="utf-8",
        )
        before = self.config_store.path.read_bytes()

        with self.assertRaisesRegex(
            ConfigMutationConflict,
            "active or resumable run",
        ):
            ConfigCommand(self.config_store, active_run_store).apply_patch(
                {
                    "schema_version": 1,
                    "operation": "delete_connection",
                    "arguments": {"connection_id": connection_id},
                },
                valid_evaluation_profile_ids={"quick", "full"},
            )

        self.assertFalse(active_run_store.path.exists())
        self.assertEqual(self.config_store.path.read_bytes(), before)

    def test_patch_config_cli_persists_patch_and_returns_full_state(self) -> None:
        payload = {
            "schema_version": 1,
            "operation": "recommendation_preference",
            "arguments": {"preference": "speed"},
        }

        output = subprocess.check_output(
            [
                "python3",
                "scripts/native_bridge.py",
                "patch-config",
                "--config-path",
                str(self.config_store.path),
                "--history-path",
                str(self.root / "history.jsonl"),
                "--active-run-path",
                str(self.root / "active_run.json"),
                "--payload",
                json.dumps(payload, ensure_ascii=False),
            ],
            text=True,
            cwd=Path(__file__).resolve().parent.parent,
            timeout=60,
        )

        response = json.loads(output)
        self.assertEqual(response["schema_version"], 1)
        self.assertEqual(response["operation"], "recommendation_preference")
        self.assertEqual(response["config"], response["state"]["config"])
        for key in (
            "advisor_v2_evidence",
            "recommendation_portfolio_v2",
            "reference_snapshot_feed",
            "recommendation_use",
        ):
            self.assertIn(key, response["state"])
        self.assertEqual(
            self.config_store.load().recommendation.preference,
            "speed",
        )

    def test_endpoint_upsert_cli_accepts_versioned_intent_and_returns_full_state(
        self,
    ) -> None:
        payload = {
            "schema_version": 1,
            "connection_id": "endpoint-cli",
            "name": "Endpoint CLI",
            "provider_preset": "generic",
            "api_format": "openai_responses",
            "base_url": "https://api.example.test/v1",
            "api_key_ref": "env:MODELDIAL_TEST_KEY",
            "enabled": True,
            "model_ids": ["model-a"],
            "reasoning_profiles_by_model": {
                "model-a": ["medium", "high"],
            },
            "default_reasoning_profile_by_model": {},
            "candidate_enabled": True,
            "last_test_status": None,
            "last_test_at": None,
            "last_test_message": None,
        }

        output = subprocess.check_output(
            [
                "python3",
                "scripts/native_bridge.py",
                "upsert-endpoint",
                "--config-path",
                str(self.config_store.path),
                "--history-path",
                str(self.root / "history.jsonl"),
                "--active-run-path",
                str(self.root / "active_run.json"),
                "--payload",
                json.dumps(payload, ensure_ascii=False),
            ],
            text=True,
            cwd=Path(__file__).resolve().parent.parent,
            timeout=60,
        )

        response = json.loads(output)
        self.assertEqual(response["schema_version"], 1)
        self.assertEqual(response["action"], "upsert_endpoint")
        self.assertEqual(response["operation"], "upsert_endpoint")
        self.assertEqual(response["state"]["schema_version"], 2)
        self.assertEqual(response["config"], response["state"]["config"])
        connection = next(
            item
            for item in response["config"]["model_ingress"]["connections"]
            if item["id"] == "endpoint-cli"
        )
        enabled = [
            candidate["id"]
            for candidate in connection["model_candidates"]
            if candidate["enabled"]
        ]
        self.assertEqual(enabled, ["endpoint-cli:model-a:high"])


if __name__ == "__main__":
    unittest.main()
