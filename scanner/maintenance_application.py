from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Protocol

from .active_run_store import ActiveRunStore
from .history_store import HistoryStore
from .process_lock import scan_lock_is_active
from .service import MonitorService
from .usage_observer import reset_codex_usage_observations
from .usage_store import UsageStore


AUTO_RESUME_ATTEMPT_LIMIT = 2
AUTO_RESUME_OPERATION_KINDS = frozenset(
    {"scan", "candidate_repair", "failed_repair", "timeout_repair"}
)


@dataclass(frozen=True)
class AutoResumeClaim:
    run_id: str
    operation_kind: str
    operation_run_id: str
    candidate_ids: tuple[str, ...]
    question_id: str | None
    trigger: str
    client_session_id: str
    attempt: int


class AutoResumeStream(Protocol):
    def __call__(
        self,
        claim: AutoResumeClaim,
        *,
        process_lock: Callable[..., AbstractContextManager[bool]],
    ) -> Iterator[dict[str, object]]: ...


@contextmanager
def _already_held_process_lock(
    *_args: object,
    **_kwargs: object,
) -> Iterator[bool]:
    yield True


def _auto_resume_session(
    payload: dict[str, object],
    client_session_id: str,
) -> dict[str, object]:
    maintenance = payload.get("maintenance")
    maintenance = dict(maintenance) if isinstance(maintenance, dict) else {}
    auto_resume = maintenance.get("auto_resume")
    auto_resume = dict(auto_resume) if isinstance(auto_resume, dict) else {}
    sessions = auto_resume.get("sessions")
    sessions = dict(sessions) if isinstance(sessions, dict) else {}
    session = sessions.get(client_session_id)
    return dict(session) if isinstance(session, dict) else {}


def _update_auto_resume_session(
    payload: dict[str, object],
    client_session_id: str,
    session: dict[str, object],
) -> dict[str, object]:
    updated = dict(payload)
    maintenance = updated.get("maintenance")
    maintenance = dict(maintenance) if isinstance(maintenance, dict) else {}
    auto_resume = maintenance.get("auto_resume")
    auto_resume = dict(auto_resume) if isinstance(auto_resume, dict) else {}
    sessions = auto_resume.get("sessions")
    sessions = dict(sessions) if isinstance(sessions, dict) else {}
    sessions[client_session_id] = dict(session)
    auto_resume["sessions"] = sessions
    maintenance["auto_resume"] = auto_resume
    updated["maintenance"] = maintenance
    return updated


def _validated_client_session_id(client_session_id: str) -> str:
    normalized = client_session_id.strip()
    if not normalized or len(normalized) > 128:
        raise ValueError("client_session_id must contain 1 to 128 characters")
    return normalized


@dataclass(frozen=True)
class AutoResumeCommand:
    service: MonitorService
    process_lock: Callable[..., AbstractContextManager[bool]]
    resume_stream: AutoResumeStream
    terminal_snapshot_builder: Callable[[], dict[str, object]]

    def resume_if_needed(
        self,
        trigger: str,
        client_session_id: str,
    ) -> Iterator[dict[str, object]]:
        if trigger not in {"startup", "interruption"}:
            raise ValueError(f"unsupported auto-resume trigger: {trigger}")
        session_id = _validated_client_session_id(client_session_id)
        decision: AutoResumeClaim | dict[str, object]
        with self.process_lock(
            self.service.active_run_store,
            self.service.history_store,
            lease_heartbeat=self.service.heartbeat_active_run_lease,
        ) as lock_acquired:
            if not lock_acquired:
                decision = {
                    "type": "auto-resume.noop",
                    "trigger": trigger,
                    "reason": "scan_active",
                    "message": "已有扫描进程正在运行",
                }
            else:
                decision = self._claim_if_needed(trigger, session_id)
            if isinstance(decision, AutoResumeClaim):
                yield {
                    "type": "auto-resume.started",
                    "trigger": trigger,
                    "run_id": decision.run_id,
                    "operation_kind": decision.operation_kind,
                    "attempt": decision.attempt,
                    "message": "正在自动继续扫描",
                }
                yield from self.resume_stream(
                    decision,
                    process_lock=_already_held_process_lock,
                )
                return

        yield {
            **decision,
            "state": self.terminal_snapshot_builder(),
        }

    def _claim_if_needed(
        self,
        trigger: str,
        session_id: str,
    ) -> AutoResumeClaim | dict[str, object]:
        active_run = self.service.active_run_store.load()
        if not isinstance(active_run, dict):
            return {
                "type": "auto-resume.noop",
                "trigger": trigger,
                "reason": "no_resumable_run",
                "message": "当前没有可自动继续的任务",
            }
        run_id = str(active_run.get("run_id") or "").strip()
        if not run_id:
            return {
                "type": "auto-resume.manual-attention",
                "trigger": trigger,
                "reason": "invalid_resume_metadata",
                "message": "续扫断点缺少运行标识，请手动检查",
            }

        runtime = self.service.build_refresh_state().get("runtime")
        if not isinstance(runtime, dict) or not runtime.get("has_resumable_run"):
            return {
                "type": "auto-resume.noop",
                "trigger": trigger,
                "run_id": run_id,
                "reason": "no_resumable_run",
                "message": "当前没有可自动继续的任务",
            }
        resumable_run_id = str(runtime.get("resumable_run_id") or "").strip()
        if resumable_run_id != run_id:
            return {
                "type": "auto-resume.manual-attention",
                "trigger": trigger,
                "run_id": run_id,
                "reason": "resume_run_mismatch",
                "message": "续扫断点与当前运行不一致，请手动检查",
            }

        operation_kind = str(runtime.get("resumable_operation_kind") or "scan")
        operation_run_id = str(
            runtime.get("resumable_operation_run_id") or run_id
        ).strip()
        candidate_ids_payload = runtime.get("resumable_candidate_ids")
        candidate_ids = tuple(
            str(item)
            for item in (
                candidate_ids_payload
                if isinstance(candidate_ids_payload, list)
                else []
            )
            if str(item)
        )
        question_id_payload = runtime.get("resumable_question_id")
        question_id = (
            str(question_id_payload).strip()
            if question_id_payload is not None
            else None
        )
        if not question_id:
            question_id = None
        if (
            operation_kind not in AUTO_RESUME_OPERATION_KINDS
            or not operation_run_id
            or operation_kind == "candidate_repair"
            and len(candidate_ids) != 1
            or operation_kind in {"failed_repair", "timeout_repair"}
            and not candidate_ids
        ):
            return {
                "type": "auto-resume.manual-attention",
                "trigger": trigger,
                "run_id": run_id,
                "reason": "invalid_resume_metadata",
                "message": "续扫断点信息不完整，请手动检查",
            }

        session = _auto_resume_session(active_run, session_id)
        if session.get("pause_suppressed") is True:
            return {
                "type": "auto-resume.noop",
                "trigger": trigger,
                "run_id": run_id,
                "reason": "paused_by_client_session",
                "message": "当前会话已主动暂停，不自动继续",
            }
        attempt = int(session.get("attempt_count") or 0) + 1
        if attempt > AUTO_RESUME_ATTEMPT_LIMIT:
            return {
                "type": "auto-resume.manual-attention",
                "trigger": trigger,
                "run_id": run_id,
                "reason": "attempt_limit_reached",
                "attempt_limit": AUTO_RESUME_ATTEMPT_LIMIT,
                "message": "自动续扫已停止，请手动检查",
            }

        claimed_at = datetime.now().astimezone().isoformat(timespec="seconds")

        def claim(current: dict[str, object]) -> dict[str, object]:
            if str(current.get("run_id") or "") != run_id:
                return current
            current_session = _auto_resume_session(current, session_id)
            current_attempt = int(current_session.get("attempt_count") or 0)
            updated_session = {
                **current_session,
                "attempt_count": current_attempt + 1,
                "pause_suppressed": False,
                "last_trigger": trigger,
                "last_claimed_at": claimed_at,
            }
            return _update_auto_resume_session(
                current,
                session_id,
                updated_session,
            )

        claimed_payload = self.service.active_run_store.mutate(claim)
        if str(claimed_payload.get("run_id") or "") != run_id:
            return {
                "type": "auto-resume.noop",
                "trigger": trigger,
                "reason": "resumable_run_changed",
                "message": "续扫断点已变化，本次未自动继续",
            }
        claimed_session = _auto_resume_session(claimed_payload, session_id)
        claimed_attempt = int(claimed_session.get("attempt_count") or 0)
        if claimed_attempt != attempt:
            return {
                "type": "auto-resume.manual-attention",
                "trigger": trigger,
                "run_id": run_id,
                "reason": "resume_claim_conflict",
                "message": "自动续扫 claim 冲突，请手动检查",
            }

        return AutoResumeClaim(
            run_id=run_id,
            operation_kind=operation_kind,
            operation_run_id=operation_run_id,
            candidate_ids=candidate_ids,
            question_id=question_id,
            trigger=trigger,
            client_session_id=session_id,
            attempt=attempt,
        )


@dataclass(frozen=True)
class RunControlCommand:
    active_run_store: ActiveRunStore
    history_store: HistoryStore

    def request(
        self,
        action: str,
        *,
        client_session_id: str | None = None,
        terminate_children: Callable[[Path], int],
    ) -> dict[str, object]:
        if action not in {"pause", "stop"}:
            raise ValueError(f"unsupported scan control: {action}")
        control_session_id: str | None = None
        paused_at: str | None = None
        if action == "pause" and client_session_id is not None:
            session_id = _validated_client_session_id(client_session_id)
            control_session_id = session_id
            paused_at = datetime.now().astimezone().isoformat(timespec="seconds")
        effective_action = self.active_run_store.request_control_for_active_run(
            action,
            client_session_id=control_session_id,
            paused_at=paused_at,
        )
        if effective_action is None:
            return {
                "ok": False,
                "action": action,
                "message": "当前没有可控制的扫描",
            }
        terminated_process_count = terminate_children(
            self.active_run_store.path.with_name("scan.lock")
        )
        return {
            "ok": True,
            "action": effective_action,
            "message": (
                "正在暂停" if effective_action == "pause" else "正在停止"
            ),
            "terminated_process_count": terminated_process_count,
        }

    def dismiss_resumable(self) -> dict[str, object]:
        if scan_lock_is_active(
            self.active_run_store.path.with_name("scan.lock")
        ):
            return {
                "ok": False,
                "action": "dismiss",
                "message": "当前已有扫描正在运行",
            }
        active_run = self.active_run_store.load()
        if active_run is None:
            return {
                "ok": False,
                "action": "dismiss",
                "message": "当前没有待继续项",
            }
        run_id = str(active_run.get("run_id") or "")
        if run_id:
            active_metadata = active_run.get("run_metadata")
            metadata = (
                dict(active_metadata)
                if isinstance(active_metadata, dict)
                else self.history_store.load_run_metadata(run_id)
                or {"run_id": run_id}
            )
            dismissed_at = datetime.now().astimezone().isoformat()
            metadata["run_id"] = run_id
            metadata["status"] = "stopped"
            metadata["completed_at"] = dismissed_at
            metadata["dismissed_at"] = dismissed_at
            self.history_store.save_run_metadata(metadata)
        if run_id:
            self.active_run_store.clear_for_run(run_id)
        else:
            self.active_run_store.clear()
        return {
            "ok": True,
            "action": "dismiss",
            "message": "已忽略本次待重试项",
        }


@dataclass(frozen=True)
class RunRecoveryCommand:
    service: MonitorService

    def recover(
        self,
        *,
        process_lock: Callable[..., AbstractContextManager[bool]],
    ) -> dict[str, object]:
        with process_lock(
            self.service.active_run_store,
            self.service.history_store,
            lease_heartbeat=self.service.heartbeat_active_run_lease,
        ) as lock_acquired:
            if not lock_acquired:
                payload: dict[str, object] = {
                    "ok": True,
                    "action": "recover_run",
                    "recovered": False,
                    "status": "scan_active",
                    "message": "扫描进程仍在运行，未执行恢复。",
                }
                try:
                    active_run = self.service.active_run_store.load()
                except (OSError, json.JSONDecodeError, TypeError, ValueError):
                    active_run = None
                if isinstance(active_run, dict):
                    run_id = str(active_run.get("run_id") or "").strip()
                    if run_id:
                        payload["run_id"] = run_id
                return payload
            return self.service.recover_orphaned_finalizing_run(
                exclusive_lock_held=True
            )


@dataclass(frozen=True)
class PersonalObservationCommand:
    data_dir: Path

    def export(self) -> dict[str, object]:
        return UsageStore(self.data_dir).export_personal_observations()

    def clear(self, *, sessions_root: Path | None = None) -> dict[str, object]:
        store = UsageStore(self.data_dir)
        removed = store.clear_personal_observations()
        reset_codex_usage_observations(
            sessions_root=sessions_root,
            store=store,
        )
        return {
            "ok": True,
            "action": "clear_personal_observations",
            "message": "个人观察数据已清除，将从现在开始重新观察",
            "removed_file_count": len(removed),
        }
