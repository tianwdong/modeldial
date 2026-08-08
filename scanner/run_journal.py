from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

from .jsonl_store import repair_truncated_jsonl_tail


class RunJournalStore:
    SCHEMA_VERSION = 1

    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.RLock()
        self._last_sequence_by_run: dict[str, int] = {}

    def append_event(
        self,
        run_id: str,
        event_type: str,
        data: dict[str, object] | None = None,
        *,
        occurred_at: str | None = None,
    ) -> dict[str, object]:
        with self._lock:
            run_dir = self._run_dir(run_id)
            run_dir.mkdir(parents=True, exist_ok=True)
            repair_truncated_jsonl_tail(run_dir / "events.jsonl")
            sequence = self._next_sequence(run_id)
            event = {
                "schema_version": self.SCHEMA_VERSION,
                "run_id": run_id,
                "sequence": sequence,
                "type": event_type,
                "occurred_at": occurred_at or self._timestamp(),
                "data": dict(data or {}),
            }
            serialized = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            self._append_line(run_dir / "events.jsonl", serialized)
            self._last_sequence_by_run[run_id] = sequence
            try:
                self._append_line(
                    run_dir / "host.log",
                    f'{event["occurred_at"]} #{sequence} {event_type} '
                    f'{json.dumps(event["data"], ensure_ascii=False, separators=(",", ":"))}',
                )
            except OSError:
                pass
            return event

    def save_summary(
        self,
        run_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        with self._lock:
            run_dir = self._run_dir(run_id)
            run_dir.mkdir(parents=True, exist_ok=True)
            summary = {
                "schema_version": self.SCHEMA_VERSION,
                "run_id": run_id,
                **dict(payload),
            }
            summary_path = run_dir / "summary.json"
            temporary = summary_path.with_name(
                f".{summary_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            try:
                with temporary.open("w", encoding="utf-8") as handle:
                    json.dump(summary, handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                temporary.replace(summary_path)
            finally:
                if temporary.exists():
                    temporary.unlink()
            return summary

    def load_events(self, run_id: str) -> list[dict[str, object]]:
        with self._lock:
            path = self._run_dir(run_id) / "events.jsonl"
            if not path.exists():
                return []
            events: list[dict[str, object]] = []
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        break
                    if isinstance(payload, dict):
                        events.append(payload)
            return events

    def load_summary(self, run_id: str) -> dict[str, object] | None:
        with self._lock:
            path = self._run_dir(run_id) / "summary.json"
            if not path.exists():
                return None
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            return dict(payload) if isinstance(payload, dict) else None

    def _next_sequence(self, run_id: str) -> int:
        if run_id in self._last_sequence_by_run:
            return self._last_sequence_by_run[run_id] + 1
        events = self.load_events(run_id)
        if not events:
            return 1
        try:
            return int(events[-1].get("sequence") or len(events)) + 1
        except (TypeError, ValueError):
            return len(events) + 1

    def _run_dir(self, run_id: str) -> Path:
        if (
            not run_id
            or run_id in {".", ".."}
            or "/" in run_id
            or "\\" in run_id
        ):
            raise ValueError("invalid run_id")
        return self.root / run_id

    @staticmethod
    def _append_line(path: Path, line: str) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _timestamp() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")
