from __future__ import annotations

import subprocess
import unittest
from unittest.mock import Mock

from scanner.secret_store import (
    SecretStore,
    SecretStoreError,
    install_process_secret_overrides,
)


class SecretStoreTest(unittest.TestCase):
    def tearDown(self) -> None:
        install_process_secret_overrides({})

    def test_environment_reference_is_resolved_for_tests(self) -> None:
        store = SecretStore(environ={"MODELDIAL_TEST_KEY": "secret"})

        self.assertEqual(store.resolve("env:MODELDIAL_TEST_KEY"), "secret")

    def test_keychain_reference_uses_service_and_account(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout="secret\n", stderr="")
        run_command = Mock(return_value=completed)
        store = SecretStore(run_command=run_command)

        self.assertEqual(
            store.resolve("keychain:com.modeldial.api-key:api-1"),
            "secret",
        )
        self.assertEqual(
            run_command.call_args.args[0],
            [
                "/usr/bin/security",
                "find-generic-password",
                "-s",
                "com.modeldial.api-key",
                "-a",
                "api-1",
                "-w",
            ],
        )

    def test_plaintext_reference_is_rejected(self) -> None:
        with self.assertRaisesRegex(SecretStoreError, "unsupported secret reference"):
            SecretStore().resolve("plaintext:secret")

    def test_missing_environment_secret_does_not_echo_reference_value(self) -> None:
        with self.assertRaisesRegex(SecretStoreError, "environment secret is unavailable") as error:
            SecretStore(environ={}).resolve("env:MODELDIAL_TEST_KEY")

        self.assertNotIn("MODELDIAL_TEST_KEY", str(error.exception))

    def test_keychain_failure_does_not_echo_stderr(self) -> None:
        completed = subprocess.CompletedProcess(
            [],
            44,
            stdout="",
            stderr="security: SecKeychainSearchCopyNext: api-key-secret",
        )
        store = SecretStore(run_command=Mock(return_value=completed))

        with self.assertRaisesRegex(SecretStoreError, "keychain secret is unavailable") as error:
            store.resolve("keychain:com.modeldial.api-key:api-1")

        self.assertNotIn("api-key-secret", str(error.exception))

    def test_process_override_resolves_keychain_reference_without_security_cli(self) -> None:
        run_command = Mock()
        reference = "keychain:com.modeldial.api-key:api-1"
        install_process_secret_overrides({reference: "pipe-secret"})

        secret = SecretStore(run_command=run_command).resolve(reference)

        self.assertEqual(secret, "pipe-secret")
        run_command.assert_not_called()

    def test_process_override_resolves_local_encrypted_reference(self) -> None:
        run_command = Mock()
        reference = "local_encrypted:endpoint-1"
        install_process_secret_overrides({reference: "pipe-secret"}, strict=True)

        secret = SecretStore(run_command=run_command).resolve(reference)

        self.assertEqual(secret, "pipe-secret")
        run_command.assert_not_called()

    def test_local_encrypted_reference_requires_bridge_override(self) -> None:
        with self.assertRaisesRegex(SecretStoreError, "local encrypted secret is unavailable"):
            SecretStore().resolve("local_encrypted:endpoint-1")

    def test_strict_process_override_does_not_fall_back_to_security_cli(self) -> None:
        run_command = Mock()
        install_process_secret_overrides({}, strict=True)

        with self.assertRaisesRegex(SecretStoreError, "keychain secret is unavailable"):
            SecretStore(run_command=run_command).resolve(
                "keychain:com.modeldial.api-key:api-1"
            )

        run_command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
