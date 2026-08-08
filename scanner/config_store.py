from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Callable

from .models import AppConfig
from .process_lock import exclusive_process_lock


class ConfigStore:
    def __init__(self, path: Path, *, first_run_defaults: bool = False) -> None:
        self.path = path
        self.first_run_defaults = first_run_defaults

    def load(self) -> AppConfig:
        if not self.path.exists():
            return AppConfig.first_run() if self.first_run_defaults else AppConfig.default()
        with self.path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return AppConfig.from_dict(payload)

    def save(self, config: AppConfig) -> AppConfig:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(config.to_dict(), handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self.path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return config

    def update(
        self,
        updater: Callable[[AppConfig], AppConfig],
        *,
        timeout_seconds: float = 5.0,
    ) -> AppConfig:
        lock_path = self.path.with_name(f".{self.path.name}.update.lock")
        with exclusive_process_lock(
            lock_path,
            timeout_seconds=timeout_seconds,
        ) as acquired:
            if not acquired:
                raise TimeoutError("config update lock is unavailable")
            current = self.load()
            updated = updater(current)
            if not isinstance(updated, AppConfig):
                raise TypeError("config updater must return AppConfig")
            return self.save(updated)
