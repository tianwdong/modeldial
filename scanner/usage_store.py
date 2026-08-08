from __future__ import annotations

from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
from typing import Iterator

from .process_lock import exclusive_process_lock


ACCOUNT_SNAPSHOT_RETENTION_DAYS = 90
MAX_ACCOUNT_SNAPSHOTS = 50_000


class UsageStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.usage_state_path = root / "usage_observations.json"
        self.account_snapshot_path = root / "codex_account_snapshot.json"
        self.account_snapshot_history_path = root / "codex_account_snapshots.json"
        self.recommendation_use_epochs_path = root / "recommendation_use_epochs.json"
        self.identity_key_path = root / "usage_identity.key"
        self.transaction_lock_path = root / ".usage-state.lock"

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with exclusive_process_lock(self.transaction_lock_path) as acquired:
            if not acquired:
                raise TimeoutError("usage state transaction lock timed out")
            yield

    def load_usage_state(self) -> dict[str, object]:
        payload = self._load_json(self.usage_state_path)
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            return {
                "schema_version": 1,
                "files": {},
                "observations": {},
                "bootstrap_truncated": False,
            }
        return payload

    def save_usage_state(self, payload: dict[str, object]) -> None:
        self._save_json(self.usage_state_path, payload)

    def load_recommendation_use_state(self) -> dict[str, object]:
        payload = self._load_json(self.recommendation_use_epochs_path)
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            return {
                "schema_version": 1,
                "epochs": [],
                "observation_assignments": {},
            }
        return payload

    def save_recommendation_use_state(self, payload: dict[str, object]) -> None:
        self._save_json(self.recommendation_use_epochs_path, payload)

    def export_personal_observations(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
                "+00:00", "Z"
            ),
            "usage_observations": self.load_usage_state(),
            "account_snapshot": self.load_account_snapshot(),
            "account_snapshot_history": self.load_account_snapshots(),
            "recommendation_use": self.load_recommendation_use_state(),
        }

    def clear_personal_observations(self) -> list[str]:
        with self.transaction():
            return self._clear_personal_observations()

    def _clear_personal_observations(self) -> list[str]:
        removed: list[str] = []
        for path in (
            self.usage_state_path,
            self.account_snapshot_path,
            self.account_snapshot_history_path,
            self.recommendation_use_epochs_path,
            self.identity_key_path,
        ):
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            removed.append(path.name)
        return removed

    def load_account_snapshot(self) -> dict[str, object] | None:
        payload = self._load_json(self.account_snapshot_path)
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            return None
        return payload

    def save_account_snapshot(self, payload: dict[str, object]) -> None:
        with self.transaction():
            self._save_account_snapshot(payload)

    def _save_account_snapshot(self, payload: dict[str, object]) -> None:
        self._save_json(self.account_snapshot_path, payload)
        captured_at = _text(payload.get("captured_at"))
        if captured_at is None:
            return
        current = _minimal_account_snapshot(payload)
        history = {
            str(item.get("captured_at")): item
            for item in self.load_account_snapshots()
            if _text(item.get("captured_at"))
        }
        history[captured_at] = current
        captured_time = _timestamp(captured_at)
        cutoff = (
            captured_time - timedelta(days=ACCOUNT_SNAPSHOT_RETENTION_DAYS)
            if captured_time is not None
            else None
        )
        retained = [
            item
            for item in history.values()
            if cutoff is None
            or (
                (item_time := _timestamp(item.get("captured_at"))) is not None
                and item_time >= cutoff
            )
        ]
        retained.sort(key=lambda item: str(item.get("captured_at") or ""))
        self._save_json(
            self.account_snapshot_history_path,
            {
                "schema_version": 1,
                "snapshots": retained[-MAX_ACCOUNT_SNAPSHOTS:],
            },
        )

    def load_account_snapshots(self) -> list[dict[str, object]]:
        payload = self._load_json(self.account_snapshot_history_path)
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            return []
        snapshots = payload.get("snapshots")
        if not isinstance(snapshots, list):
            return []
        return [item for item in snapshots if isinstance(item, dict)]

    def identity_key(self) -> bytes:
        try:
            existing = self.identity_key_path.read_bytes()
        except OSError:
            existing = b""
        if len(existing) >= 32:
            return existing

        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        generated = secrets.token_bytes(32)
        try:
            descriptor = os.open(
                self.identity_key_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            existing = self.identity_key_path.read_bytes()
            if len(existing) < 32:
                raise ValueError("invalid usage identity key")
            return existing
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(generated)
            handle.flush()
            os.fsync(handle.fileno())
        return generated

    @staticmethod
    def _load_json(path: Path) -> object:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _save_json(self, path: Path, payload: dict[str, object]) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(
                    payload,
                    handle,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            temporary.replace(path)
        finally:
            if temporary.exists():
                temporary.unlink()


def _minimal_account_snapshot(payload: dict[str, object]) -> dict[str, object]:
    windows = payload.get("quota_windows")
    return {
        "schema_version": 1,
        "captured_at": payload.get("captured_at"),
        "source": payload.get("source"),
        "account_type": payload.get("account_type"),
        "login_state": payload.get("login_state"),
        "plan_type": payload.get("plan_type"),
        "quota_status": payload.get("quota_status"),
        "quota_windows": [
            dict(item) for item in windows if isinstance(item, dict)
        ] if isinstance(windows, list) else [],
    }


def _timestamp(value: object) -> datetime | None:
    text = _text(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None
