from __future__ import annotations

import os
from collections.abc import Mapping


# These variables are runtime plumbing rather than credentials.  Everything
# else is dropped unless a caller explicitly injects a ModelDial setting.
_RUNTIME_ENVIRONMENT_KEYS = frozenset(
    {
        "PATH",
        "HOME",
        "TMPDIR",
        "TMP",
        "TEMP",
        "LANG",
        "LANGUAGE",
        "TERM",
        "TERM_PROGRAM",
        "COLORTERM",
        "USER",
        "LOGNAME",
        "SHELL",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "CODEX_HOME",
        "CLAUDE_CONFIG_DIR",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "HOMEDRIVE",
        "HOMEPATH",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
    }
)
_EXPLICIT_OVERRIDE_KEYS = frozenset({"CODEX_API_KEY"})


def build_child_environment(
    overrides: Mapping[str, str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build a minimal environment for a local CLI or model subprocess.

    The parent environment is intentionally not copied wholesale.  Callers
    may explicitly inject a ModelDial setting (or the Codex API key used by
    the cloud fallback) through ``overrides``; other override names are
    ignored so an accidental secret cannot widen the child environment.
    """

    source = os.environ if environ is None else environ
    child: dict[str, str] = {}
    for key, value in source.items():
        normalized_key = str(key).upper()
        if normalized_key in _RUNTIME_ENVIRONMENT_KEYS or normalized_key.startswith(
            "LC_"
        ):
            child[str(key)] = str(value)

    for key, value in (overrides or {}).items():
        normalized_key = str(key)
        if normalized_key.startswith("MODELDIAL_") or normalized_key in _EXPLICIT_OVERRIDE_KEYS:
            child[normalized_key] = str(value)
    return child


__all__ = ["build_child_environment"]
