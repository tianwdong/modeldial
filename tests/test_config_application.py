from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from scanner.active_run_store import ActiveRunStore
from scanner.config_application import ConfigApplicationService, ConfigCommand
from scanner.config_store import ConfigStore
from scanner.endpoint_client import EndpointResult
from scanner.models import AppConfig, ConnectionConfig, ModelCandidateConfig


class ConfigCommandTest(unittest.TestCase):
    @staticmethod
    def endpoint_payload(
        *,
        connection_id: str = "endpoint-a",
        model_ids: list[str] | None = None,
        reasoning_profiles_by_model: dict[str, list[str]] | None = None,
        default_reasoning_profile_by_model: dict[str, str] | None = None,
        candidate_enabled: bool = True,
    ) -> dict[str, object]:
        return {
            "schema_version": 1,
            "connection_id": connection_id,
            "name": "Endpoint A",
            "provider_preset": "generic",
            "api_format": "openai_responses",
            "base_url": "https://example.invalid/v1",
            "api_key_ref": "env:MODELDIAL_TEST_KEY",
            "enabled": True,
            "model_ids": model_ids or ["model-a"],
            "reasoning_profiles_by_model": reasoning_profiles_by_model or {},
            "default_reasoning_profile_by_model": (
                default_reasoning_profile_by_model or {}
            ),
            "candidate_enabled": candidate_enabled,
            "last_test_status": None,
            "last_test_at": None,
            "last_test_message": None,
        }

    def test_import_local_provider_is_an_idempotent_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConfigStore(
                Path(temp_dir) / "config.json",
                first_run_defaults=True,
            )
            command = ConfigCommand(store)
            detector = lambda: [{"provider_id": "codex", "importable": True}]

            first = command.import_local_provider(
                "codex",
                local_provider_detector=detector,
            )
            second = command.import_local_provider(
                "codex",
                local_provider_detector=detector,
            )
            config = store.load()

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertTrue(
            next(
                source
                for source in config.model_ingress.sources
                if source.id == "codex_local"
            ).enabled
        )
        self.assertTrue(
            next(
                connection
                for connection in config.model_ingress.connections
                if connection.id == "codex-local-default"
            ).enabled
        )

    def test_unverified_legacy_grok_disables_ingress_without_dropping_candidates(self) -> None:
        payload = AppConfig.default().to_dict()
        for source in payload["model_ingress"]["sources"]:  # type: ignore[index]
            if source["id"] == "grok_local":  # type: ignore[index]
                source["enabled"] = True  # type: ignore[index]
        for connection in payload["model_ingress"]["connections"]:  # type: ignore[index]
            if connection["id"] != "grok-local-default":  # type: ignore[index]
                continue
            connection["enabled"] = True  # type: ignore[index]
            for candidate in connection["model_candidates"]:  # type: ignore[index]
                candidate["enabled"] = True  # type: ignore[index]

        config = AppConfig.from_dict(payload)
        source = next(item for item in config.model_ingress.sources if item.id == "grok_local")
        connection = next(
            item
            for item in config.model_ingress.connections
            if item.id == "grok-local-default"
        )

        self.assertFalse(source.enabled)
        self.assertFalse(connection.enabled)
        self.assertTrue(all(candidate.enabled for candidate in connection.model_candidates))

    def test_verify_endpoint_records_only_the_safe_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConfigStore(Path(temp_dir) / "config.json")
            config = store.load()
            config.model_ingress.connections.append(
                ConnectionConfig(
                    id="endpoint-a",
                    source_id="custom_endpoint",
                    name="Endpoint A",
                    enabled=True,
                    api_format="openai_responses",
                    base_url="https://example.invalid/v1",
                    api_key_ref="env:MODELDIAL_TEST_KEY",
                    model_candidates=[
                        ModelCandidateConfig(
                            id="endpoint-a:model-a:high",
                            connection_id="endpoint-a",
                            model_id="model-a",
                            display_name="Model A",
                            enabled=True,
                            scan_profile="high",
                        )
                    ],
                )
            )
            store.save(config)
            secret_store = type(
                "Secrets",
                (),
                {"resolve": lambda self, _reference: "not-persisted-secret"},
            )()

            response = ConfigCommand(store).verify_endpoint_connection(
                "endpoint-a",
                "model-a",
                secret_store=secret_store,
                requester=lambda *_args: EndpointResult(
                    text="OK",
                    input_tokens=1,
                    output_tokens=1,
                    reasoning_tokens=None,
                ),
            )
            connection = next(
                item
                for item in store.load().model_ingress.connections
                if item.id == "endpoint-a"
            )
            persisted = store.path.read_text(encoding="utf-8")

        self.assertTrue(response["ok"])
        self.assertEqual(connection.last_test_status, "ok")
        self.assertEqual(connection.last_test_message, "连接成功")
        self.assertNotIn("not-persisted-secret", persisted)
        self.assertNotIn('"text": "OK"', persisted)

    def test_typed_patch_and_legacy_replace_share_one_command_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConfigStore(Path(temp_dir) / "config.json")
            command = ConfigCommand(store)
            _, operation = command.apply_patch(
                {
                    "schema_version": 1,
                    "operation": "recommendation_preference",
                    "arguments": {"preference": "cost"},
                },
                valid_evaluation_profile_ids={"quick", "full"},
            )
            payload = store.load().to_dict()
            payload["recommendation"]["preference"] = "quality"
            command.replace_legacy_config(payload)
            config = store.load()

        self.assertEqual(operation, "recommendation_preference")
        self.assertEqual(config.recommendation.preference, "quality")

    def test_secret_reference_migration_uses_only_the_atomic_patch_command(
        self,
    ) -> None:
        payload = {
            "schema_version": 1,
            "operation": "connection_secret_references",
            "arguments": {
                "references_by_connection_id": {
                    "endpoint-a": "keychain:com.modeldial.api-key:endpoint-a",
                }
            },
        }
        store = MagicMock(spec=ConfigStore)
        command = ConfigCommand(store)

        with patch.object(
            ConfigCommand,
            "apply_patch",
            autospec=True,
            return_value=(MagicMock(), "connection_secret_references"),
        ) as apply_patch:
            response = command.migrate_secret_references(payload)

        apply_patch.assert_called_once_with(
            command,
            payload,
            valid_evaluation_profile_ids=set(),
        )
        self.assertEqual(
            response,
            {
                "schema_version": 1,
                "ok": True,
                "action": "migrate_secret_references",
                "operation": "connection_secret_references",
            },
        )
        self.assertNotIn("keychain:", repr(response))

    def test_secret_reference_migration_rejects_every_other_patch_operation(
        self,
    ) -> None:
        command = ConfigCommand(MagicMock(spec=ConfigStore))
        payload = {
            "schema_version": 1,
            "operation": "scheduler_enabled",
            "arguments": {"enabled": True},
        }

        with patch.object(
            ConfigCommand,
            "apply_patch",
            autospec=True,
        ) as apply_patch, self.assertRaisesRegex(
            ValueError,
            "only accepts connection_secret_references",
        ):
            command.migrate_secret_references(payload)

        apply_patch.assert_not_called()

    def test_endpoint_upsert_builds_candidates_and_preserves_enabled_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConfigStore(Path(temp_dir) / "config.json")
            command = ConfigCommand(store)

            command.upsert_endpoint(
                self.endpoint_payload(
                    model_ids=["model-a", "model-b"],
                    reasoning_profiles_by_model={
                        "model-a": ["low", "high", "xhigh"],
                        "model-b": ["minimal", "ultra"],
                    },
                )
            )
            first = next(
                item
                for item in store.load().model_ingress.connections
                if item.id == "endpoint-a"
            )
            first_by_id = {candidate.id: candidate for candidate in first.model_candidates}

            self.assertTrue(first_by_id["endpoint-a:model-a:high"].enabled)
            self.assertFalse(first_by_id["endpoint-a:model-a:low"].enabled)
            self.assertTrue(first_by_id["endpoint-a:model-b:minimal"].enabled)
            self.assertEqual(
                first_by_id["endpoint-a:model-a:high"].variant_id,
                "high",
            )
            self.assertEqual(
                first_by_id["endpoint-a:model-a:high"].family_id,
                "model-a",
            )
            self.assertEqual(
                first_by_id["endpoint-a:model-a:high"].display_name,
                "model-a",
            )
            self.assertEqual(
                first_by_id["endpoint-a:model-a:high"].capabilities,
                ["reasoning"],
            )

            command.upsert_endpoint(
                self.endpoint_payload(
                    model_ids=["model-a"],
                    reasoning_profiles_by_model={
                        "model-a": ["medium", "high", "xhigh"],
                    },
                    default_reasoning_profile_by_model={"model-a": "xhigh"},
                )
            )
            updated = next(
                item
                for item in store.load().model_ingress.connections
                if item.id == "endpoint-a"
            )
            updated_by_id = {
                candidate.id: candidate for candidate in updated.model_candidates
            }

        self.assertTrue(updated_by_id["endpoint-a:model-a:high"].enabled)
        self.assertTrue(updated_by_id["endpoint-a:model-a:xhigh"].enabled)
        self.assertIn("endpoint-a:model-b:minimal", updated_by_id)

    def test_endpoint_commands_reject_duplicate_models_and_add_normalized_candidates(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConfigStore(Path(temp_dir) / "config.json")
            command = ConfigCommand(store)
            duplicate_payload = self.endpoint_payload(
                model_ids=["model-a", "model-a"],
            )

            before = store.load().to_dict()
            with self.assertRaisesRegex(ValueError, "model_ids must not contain duplicates"):
                command.upsert_endpoint(duplicate_payload)
            self.assertEqual(store.load().to_dict(), before)

            command.upsert_endpoint(self.endpoint_payload())
            command.add_endpoint_models(
                {
                    "schema_version": 1,
                    "connection_id": "endpoint-a",
                    "model_ids": ["model-b"],
                    "reasoning_profiles_by_model": {
                        "model-b": ["medium", "high"],
                    },
                    "default_reasoning_profile_by_model": {},
                    "candidate_enabled": False,
                }
            )
            connection = next(
                item
                for item in store.load().model_ingress.connections
                if item.id == "endpoint-a"
            )
            added = [
                candidate
                for candidate in connection.model_candidates
                if candidate.model_id == "model-b"
            ]
            self.assertEqual(
                [candidate.id for candidate in added],
                ["endpoint-a:model-b:medium", "endpoint-a:model-b:high"],
            )
            self.assertFalse(any(candidate.enabled for candidate in added))

            with self.assertRaisesRegex(ValueError, "model candidates already exist"):
                command.add_endpoint_models(
                    {
                        "schema_version": 1,
                        "connection_id": "endpoint-a",
                        "model_ids": ["model-b"],
                        "reasoning_profiles_by_model": {},
                        "default_reasoning_profile_by_model": {},
                        "candidate_enabled": False,
                    }
                )


class ConfigApplicationServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.command_patcher = patch(
            "scanner.config_application.ConfigCommand",
        )
        self.command_type = self.command_patcher.start()
        self.addCleanup(self.command_patcher.stop)
        self.command = self.command_type.return_value
        self.command.apply_patch.return_value = (MagicMock(), "scheduler")
        self.command.upsert_endpoint.return_value = (
            MagicMock(),
            "upsert_endpoint",
        )
        self.command.add_endpoint_models.return_value = (
            MagicMock(),
            "add_endpoint_models",
        )
        self.snapshot = {
            "schema_version": 2,
            "config": {"authoritative": True},
            "runtime": {"lifecycle_state": "idle"},
        }
        self.snapshot_builder = MagicMock(return_value=self.snapshot)
        self.service = MagicMock()
        self.service.question_bank.load.return_value = SimpleNamespace(
                evaluation_profiles=[
                    SimpleNamespace(id="quick"),
                    SimpleNamespace(id="full"),
                ]
        )
        self.application = ConfigApplicationService(
            service=self.service,
            snapshot_builder=self.snapshot_builder,
        )

    def test_patch_owns_profile_validation_and_authoritative_response(self) -> None:
        payload = {
            "schema_version": 1,
            "operation": "scheduler",
            "arguments": {"enabled": True},
        }

        response = self.application.patch_config(payload)

        self.command.apply_patch.assert_called_once_with(
            payload,
            valid_evaluation_profile_ids={"quick", "full"},
        )
        self.service.question_bank.load.assert_called_once_with()
        self.assertEqual(
            response,
            {
                "schema_version": 1,
                "ok": True,
                "action": "patch_config",
                "operation": "scheduler",
                "config": self.snapshot["config"],
                "state": self.snapshot,
            },
        )

    def test_legacy_and_endpoint_commands_keep_their_response_schemas(self) -> None:
        legacy_payload = {"scheduler": {"enabled": True}}
        endpoint_payload = {"schema_version": 1, "connection_id": "endpoint-a"}

        legacy = self.application.replace_legacy_config(legacy_payload)
        upsert = self.application.upsert_endpoint(endpoint_payload)
        added = self.application.add_endpoint_models(endpoint_payload)

        self.command.replace_legacy_config.assert_called_once_with(legacy_payload)
        self.command.upsert_endpoint.assert_called_once_with(endpoint_payload)
        self.command.add_endpoint_models.assert_called_once_with(endpoint_payload)
        self.assertEqual(
            legacy,
            {"config": self.snapshot["config"], "state": self.snapshot},
        )
        for action, response in (
            ("upsert_endpoint", upsert),
            ("add_endpoint_models", added),
        ):
            self.assertEqual(response["schema_version"], 1)
            self.assertIs(response["ok"], True)
            self.assertEqual(response["action"], action)
            self.assertEqual(response["operation"], action)
            self.assertIs(response["config"], self.snapshot["config"])
            self.assertIs(response["state"], self.snapshot)


if __name__ == "__main__":
    unittest.main()
