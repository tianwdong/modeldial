from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
from typing import Callable, Optional


WhichCommand = Callable[[str], Optional[str]]


@dataclass(frozen=True)
class DetectedLocalProvider:
    provider_id: str
    display_name: str
    source_id: str
    connection_id: str
    detected: bool
    importable: bool
    status: str
    status_message: str

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "source_id": self.source_id,
            "connection_id": self.connection_id,
            "detected": self.detected,
            "importable": self.importable,
            "status": self.status,
            "status_message": self.status_message,
        }


def detect_local_providers(
    *,
    home: Path | None = None,
    which: WhichCommand = shutil.which,
) -> tuple[DetectedLocalProvider, ...]:
    home = home or Path.home()
    codex_cli = which("codex")
    codex_login = (home / ".codex" / "auth.json").is_file()
    codex_ready = bool(codex_cli and codex_login)

    claude_cli = which("claude")
    claude_detected = bool(claude_cli)

    grok_cli = which("grok") or _grok_build_executable_from_common_paths()
    grok_detected = bool(grok_cli)

    return (
        DetectedLocalProvider(
            provider_id="codex",
            display_name="Codex",
            source_id="codex_local",
            connection_id="codex-local-default",
            detected=codex_ready,
            importable=codex_ready,
            status="ready" if codex_ready else "not_detected",
            status_message=(
                "已检测到 Codex CLI 和本机登录态"
                if codex_ready
                else "未同时检测到 Codex CLI 与登录态"
            ),
        ),
        DetectedLocalProvider(
            provider_id="claude",
            display_name="Claude Code",
            source_id="claude_local",
            connection_id="claude-local-default",
            detected=claude_detected,
            importable=claude_detected,
            status="login_check_required" if claude_detected else "not_detected",
            status_message=(
                "已检测到 Claude Code CLI，导入时验证本机登录态"
                if claude_detected
                else "未检测到 Claude Code CLI"
            ),
        ),
        DetectedLocalProvider(
            provider_id="grok",
            display_name="Grok Build",
            source_id="grok_local",
            connection_id="grok-local-default",
            detected=grok_detected,
            importable=grok_detected,
            status="login_check_required" if grok_detected else "not_detected",
            status_message=(
                "已检测到 Grok Build CLI，导入时验证本机登录态"
                if grok_detected
                else "未检测到 Grok Build CLI"
            ),
        ),
    )


def detected_local_provider_payload() -> list[dict[str, object]]:
    return [provider.to_dict() for provider in detect_local_providers()]


def _grok_build_executable_from_common_paths() -> Optional[str]:
    for candidate in (Path("/opt/homebrew/bin/grok"), Path("/usr/local/bin/grok")):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None
