from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from .jsonl_store import repair_truncated_jsonl_tail
from .models import ScanResult


class HistoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.metadata_path = path.with_suffix(".run_metadata.json")
        self._lock = threading.RLock()

    def append(self, result: ScanResult) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            repair_truncated_jsonl_tail(self.path)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    def load_recent(self, limit: int = 50) -> list[ScanResult]:
        return self.load_recent_with_count(limit=limit)[0]

    def load_recent_with_count(
        self,
        limit: int = 50,
    ) -> tuple[list[ScanResult], int]:
        lines = self._load_valid_lines()
        selected = lines[-max(0, limit):] if limit > 0 else []
        return (
            [ScanResult.from_dict(json.loads(line)) for line in selected],
            len(lines),
        )

    def load_all(self) -> list[ScanResult]:
        lines = self._load_valid_lines()
        return [ScanResult.from_dict(json.loads(line)) for line in lines]

    def _load_valid_lines(self) -> list[str]:
        with self._lock:
            if not self.path.exists():
                return []
            with self.path.open("r", encoding="utf-8") as handle:
                lines = [line.strip() for line in handle if line.strip()]
        if not lines:
            return []
        try:
            json.loads(lines[-1])
        except json.JSONDecodeError:
            return lines[:-1]
        return lines

    def save_run_metadata(self, payload: dict[str, object]) -> None:
        run_id = str(payload["run_id"])
        all_metadata = self.load_run_metadata_map()
        all_metadata[run_id] = dict(payload)
        self.metadata_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.metadata_path.with_name(
            f".{self.metadata_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(all_metadata, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self.metadata_path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def load_run_metadata(self, run_id: str) -> dict[str, object] | None:
        return self.load_run_metadata_map().get(run_id)

    def load_run_metadata_map(self) -> dict[str, dict[str, object]]:
        if not self.metadata_path.exists():
            return {}
        with self.metadata_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return {
            str(run_id): dict(metadata)
            for run_id, metadata in payload.items()
            if isinstance(metadata, dict)
        }
