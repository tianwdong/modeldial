from __future__ import annotations

from contextlib import contextmanager
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator
from uuid import uuid4

from .legacy_scan_compat import is_active_lifecycle
from .process_lock import exclusive_process_lock


class ActiveRunStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    @property
    def transaction_lock_path(self) -> Path:
        return self.path.with_name(f".{self.path.name}.transaction.lock")

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with exclusive_process_lock(
            self.transaction_lock_path,
            timeout_seconds=5.0,
        ) as acquired:
            if not acquired:
                raise TimeoutError(
                    f"timed out acquiring active run lock: {self.transaction_lock_path}"
                )
            yield

    def load(self) -> dict[str, object] | None:
        with self._lock:
            return self._load_unlocked()

    def _load_unlocked(self) -> dict[str, object] | None:
        if not self.path.exists():
            return None
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except FileNotFoundError:
            return None

    def save(self, payload: dict[str, object]) -> dict[str, object]:
        with self._lock:
            with self._transaction():
                return self._save_unlocked(payload)

    def _save_unlocked(self, payload: dict[str, object]) -> dict[str, object]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self.path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return payload

    def mutate(
        self,
        mutator: Callable[[dict[str, object]], dict[str, object]],
    ) -> dict[str, object]:
        with self._lock:
            with self._transaction():
                current = self._load_unlocked() or {}
                updated = mutator(current)
                return self._save_unlocked(updated)

    def refresh_runtime_lease(
        self,
        *,
        now: datetime | None = None,
    ) -> dict[str, object] | None:
        with self._lock:
            with self._transaction():
                payload = self._load_unlocked()
                if payload is None:
                    return None
                runtime = payload.get("runtime")
                if not isinstance(runtime, dict):
                    return payload
                if not is_active_lifecycle(runtime.get("lifecycle_state")):
                    return payload
                heartbeat_at = now or datetime.now(timezone.utc)
                duration = max(1, int(runtime.get("lease_duration_seconds") or 420))
                runtime["updated_at"] = heartbeat_at.isoformat()
                runtime["lease_expires_at"] = (
                    heartbeat_at + timedelta(seconds=duration)
                ).isoformat()
                payload["runtime"] = runtime
                return self._save_unlocked(payload)

    @property
    def control_path(self) -> Path:
        return self.path.with_name(f"{self.path.stem}.control.json")

    def request_control(
        self,
        action: str,
        *,
        client_session_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        if action not in {"pause", "stop"}:
            raise ValueError(f"unsupported scan control: {action}")
        with self._lock:
            with self._transaction():
                active_run = self._load_unlocked()
                resolved_run_id = str(run_id or "").strip() or self._run_id(
                    active_run
                )
                self._write_control_unlocked(
                    action,
                    client_session_id=client_session_id,
                    run_id=resolved_run_id or None,
                )

    def request_control_for_active_run(
        self,
        action: str,
        *,
        client_session_id: str | None = None,
        paused_at: str | None = None,
    ) -> str | None:
        if action not in {"pause", "stop"}:
            raise ValueError(f"unsupported scan control: {action}")
        with self._lock:
            with self._transaction():
                active_run = self._load_unlocked()
                run_id = self._run_id(active_run)
                if active_run is None or not run_id:
                    return None
                if (
                    action == "pause"
                    and self._active_control_action(active_run) == "stop"
                ):
                    return "stop"
                _, effective_action = self._write_control_unlocked(
                    action,
                    client_session_id=client_session_id,
                    run_id=run_id,
                )
                if effective_action == "pause" and client_session_id:
                    updated = self._suppress_auto_resume_payload(
                        active_run,
                        client_session_id,
                        paused_at=paused_at
                        or datetime.now()
                        .astimezone()
                        .isoformat(timespec="seconds"),
                    )
                    self._save_unlocked(updated)
                return effective_action

    def _write_control_unlocked(
        self,
        action: str,
        *,
        client_session_id: str | None,
        run_id: str | None,
    ) -> tuple[str, str]:
        existing = self._read_control_unlocked()
        existing_action = str((existing or {}).get("action") or "")
        existing_run_id = str((existing or {}).get("run_id") or "").strip()
        incoming_run_id = str(run_id or "").strip()
        if (
            action == "pause"
            and existing_action == "stop"
            and existing_run_id == incoming_run_id
        ):
            return str((existing or {}).get("request_id") or ""), "stop"

        request_id = uuid4().hex
        payload: dict[str, object] = {
            "schema_version": 1,
            "request_id": request_id,
            "run_id": run_id,
            "action": action,
        }
        if client_session_id:
            payload["client_session_id"] = client_session_id
        self.control_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.control_path.with_name(
            f".{self.control_path.name}.{os.getpid()}.{threading.get_ident()}.{request_id}.tmp"
        )
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self.control_path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return request_id, action

    def consume_control(self) -> str | None:
        with self._lock:
            with self._transaction():
                payload = self._claim_control_unlocked()
                action = str((payload or {}).get("action") or "")
        return action if action in {"pause", "stop"} else None

    def peek_control(self) -> str | None:
        payload = self.peek_control_request()
        action = str((payload or {}).get("action") or "")
        return action if action in {"pause", "stop"} else None

    def peek_control_request(self) -> dict[str, object] | None:
        with self._lock:
            return self._read_control_unlocked()

    def claim_control(
        self,
        *,
        expected_run_id: str | None = None,
    ) -> dict[str, object] | None:
        with self._lock:
            with self._transaction():
                return self._claim_control_unlocked(expected_run_id=expected_run_id)

    def _read_control_unlocked(self) -> dict[str, object] | None:
        if not self.control_path.exists():
            return None
        try:
            with self.control_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _claim_control_unlocked(
        self,
        *,
        expected_run_id: str | None = None,
    ) -> dict[str, object] | None:
        payload = self._read_control_unlocked()
        if payload is None:
            if self.control_path.exists():
                self._clear_control_unlocked()
            return None
        action = str(payload.get("action") or "")
        request_id = str(payload.get("request_id") or "").strip()
        if action not in {"pause", "stop"}:
            self._clear_control_unlocked(request_id=request_id or None)
            return None

        active_run = self._load_unlocked()
        active_run_id = self._run_id(active_run)
        request_run_id = str(payload.get("run_id") or "").strip()
        if expected_run_id is not None:
            expected = str(expected_run_id).strip()
            if active_run is not None and active_run_id != expected:
                return None
            if active_run is None and request_run_id and request_run_id != expected:
                return None
        if request_run_id and active_run is None:
            expected = (
                str(expected_run_id).strip()
                if expected_run_id is not None
                else ""
            )
            if request_run_id != expected:
                self._clear_control_unlocked(request_id=request_id or None)
                return None
        elif request_run_id and request_run_id != active_run_id:
            self._clear_control_unlocked(request_id=request_id or None)
            return None

        if action == "stop" and active_run is not None:
            self._save_unlocked(
                self._set_active_control_action(active_run, "stop")
            )

        client_session_id = str(payload.get("client_session_id") or "").strip()
        if action == "pause" and client_session_id:
            if active_run is None:
                self._clear_control_unlocked(request_id=request_id or None)
                return None
            updated = self._suppress_auto_resume_payload(
                active_run,
                client_session_id,
                paused_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            )
            self._save_unlocked(updated)
        self._clear_control_unlocked(request_id=request_id or None)
        return payload

    @staticmethod
    def _run_id(payload: dict[str, object] | None) -> str:
        if not isinstance(payload, dict):
            return ""
        for key in ("run_id", "repair_run_id", "repair_operation_run_id"):
            run_id = str(payload.get(key) or "").strip()
            if run_id:
                return run_id
        return ""

    @staticmethod
    def _active_control_action(payload: dict[str, object] | None) -> str | None:
        if not isinstance(payload, dict):
            return None
        maintenance = payload.get("maintenance")
        if not isinstance(maintenance, dict):
            return None
        action = str(maintenance.get("control_action") or "").strip()
        return action if action in {"pause", "stop"} else None

    @staticmethod
    def _set_active_control_action(
        payload: dict[str, object],
        action: str,
    ) -> dict[str, object]:
        updated = dict(payload)
        maintenance = payload.get("maintenance")
        maintenance = dict(maintenance) if isinstance(maintenance, dict) else {}
        maintenance["control_action"] = action
        updated["maintenance"] = maintenance
        return updated

    def suppress_auto_resume_for_session(
        self,
        client_session_id: str,
        *,
        paused_at: str,
    ) -> None:
        with self._lock:
            with self._transaction():
                current = self._load_unlocked()
                if current is None:
                    return
                self._save_unlocked(
                    self._suppress_auto_resume_payload(
                        current,
                        client_session_id,
                        paused_at=paused_at,
                    )
                )

    @staticmethod
    def _suppress_auto_resume_payload(
        current: dict[str, object],
        client_session_id: str,
        *,
        paused_at: str,
    ) -> dict[str, object]:
        updated = dict(current)
        maintenance = updated.get("maintenance")
        maintenance = dict(maintenance) if isinstance(maintenance, dict) else {}
        auto_resume = maintenance.get("auto_resume")
        auto_resume = dict(auto_resume) if isinstance(auto_resume, dict) else {}
        sessions = auto_resume.get("sessions")
        sessions = dict(sessions) if isinstance(sessions, dict) else {}
        session = sessions.get(client_session_id)
        session = dict(session) if isinstance(session, dict) else {}
        sessions[client_session_id] = {
            **session,
            "pause_suppressed": True,
            "paused_at": paused_at,
        }
        auto_resume["sessions"] = sessions
        maintenance["auto_resume"] = auto_resume
        updated["maintenance"] = maintenance
        return updated

    def clear_control(self, *, request_id: str | None = None) -> None:
        with self._lock:
            with self._transaction():
                self._clear_control_unlocked(request_id=request_id)

    def clear_control_for_run(
        self,
        run_id: str | None,
        *,
        owner_active_run_id: str | None = None,
    ) -> None:
        with self._lock:
            with self._transaction():
                if owner_active_run_id is not None:
                    current_active_run_id = self._run_id(self._load_unlocked())
                    if current_active_run_id != str(owner_active_run_id).strip():
                        return
                payload = self._read_control_unlocked()
                if payload is None:
                    return
                request_run_id = str(payload.get("run_id") or "").strip()
                expected_run_id = str(run_id or "").strip()
                if expected_run_id and request_run_id == expected_run_id:
                    return
                self._clear_control_unlocked(
                    request_id=str(payload.get("request_id") or "").strip()
                    or None
                )

    def _clear_control_unlocked(self, *, request_id: str | None = None) -> None:
        if not self.control_path.exists():
            return
        if request_id:
            payload = self._read_control_unlocked()
            if str((payload or {}).get("request_id") or "").strip() != request_id:
                return
        try:
            self.control_path.unlink()
        except FileNotFoundError:
            pass

    def update_run_metadata(self, metadata: dict[str, object]) -> None:
        with self._lock:
            with self._transaction():
                payload = self._load_unlocked()
                if payload is None:
                    return
                payload["run_metadata"] = dict(metadata)
                self._save_unlocked(payload)

    def update_runtime_state(
        self,
        lifecycle_state: str,
        *,
        updated_at: str,
        last_error: str | None = None,
    ) -> None:
        with self._lock:
            with self._transaction():
                payload = self._load_unlocked()
                if payload is None:
                    return
                runtime = payload.get("runtime")
                runtime = dict(runtime) if isinstance(runtime, dict) else {}
                runtime["lifecycle_state"] = lifecycle_state
                runtime["state_changed_at"] = updated_at
                runtime["updated_at"] = updated_at
                runtime["lease_expires_at"] = None
                if last_error is not None:
                    runtime["last_error"] = last_error
                payload["runtime"] = runtime
                self._save_unlocked(payload)

    def clear(self, *, run_id: str | None = None) -> None:
        with self._lock:
            with self._transaction():
                active_run = self._load_unlocked()
                active_run_id = self._run_id(active_run)
                if run_id is not None:
                    expected_run_id = str(run_id).strip()
                    if active_run_id != expected_run_id:
                        return
                if active_run_id:
                    control_request = self._read_control_unlocked()
                    if self._run_id(control_request) == active_run_id:
                        self._clear_control_unlocked(
                            request_id=str(
                                (control_request or {}).get("request_id") or ""
                            ).strip()
                            or None
                        )
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass

    def clear_for_run(self, run_id: str) -> None:
        self.clear(run_id=run_id)
