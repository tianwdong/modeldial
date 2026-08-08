from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping

from .process_environment import build_child_environment


class SecretStoreError(RuntimeError):
    pass


RunCommand = Callable[..., subprocess.CompletedProcess[str]]
_PROCESS_SECRET_OVERRIDES: dict[str, str] = {}
_PROCESS_SECRET_OVERRIDES_STRICT = False


def install_process_secret_overrides(
    overrides: Mapping[str, str],
    *,
    strict: bool = False,
) -> None:
    global _PROCESS_SECRET_OVERRIDES, _PROCESS_SECRET_OVERRIDES_STRICT
    _PROCESS_SECRET_OVERRIDES = {
        str(reference): str(secret)
        for reference, secret in overrides.items()
        if reference and secret
    }
    _PROCESS_SECRET_OVERRIDES_STRICT = strict


class SecretStore:
    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        run_command: RunCommand = subprocess.run,
    ) -> None:
        self._environ = os.environ if environ is None else environ
        self._run_command = run_command

    def resolve(self, reference: str | None) -> str:
        if not reference or ":" not in reference:
            raise SecretStoreError("secret reference is missing or malformed")
        override = _PROCESS_SECRET_OVERRIDES.get(reference)
        if override:
            return override
        scheme, value = reference.split(":", 1)
        if scheme == "env":
            secret = self._environ.get(value, "")
            if not secret:
                raise SecretStoreError("environment secret is unavailable")
            return secret
        if scheme == "keychain":
            if _PROCESS_SECRET_OVERRIDES_STRICT:
                raise SecretStoreError("keychain secret is unavailable")
            return self._resolve_keychain(value)
        if scheme == "local_encrypted":
            raise SecretStoreError("local encrypted secret is unavailable")
        raise SecretStoreError("unsupported secret reference")

    def _resolve_keychain(self, value: str) -> str:
        if ":" not in value:
            raise SecretStoreError("keychain secret reference is malformed")
        service, account = value.rsplit(":", 1)
        if not service or not account:
            raise SecretStoreError("keychain secret reference is malformed")
        completed = self._run_command(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-s",
                service,
                "-a",
                account,
                "-w",
            ],
            capture_output=True,
            text=True,
            check=False,
            env=build_child_environment(),
        )
        secret = completed.stdout.rstrip("\r\n") if completed.returncode == 0 else ""
        if not secret:
            raise SecretStoreError("keychain secret is unavailable")
        return secret
