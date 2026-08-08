from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .codex_current_model import detect_codex_current_model
from .model_sessions import detect_external_model_sessions
from .protocol import project_refresh_snapshot_v1
from .session_registry import consume_session_events
from .snapshot_query import SnapshotCommand


InsightsReader = Callable[[Path], dict[str, object]]
SessionObserver = Callable[[Path], dict[str, int]]


def session_observation_paths(data_dir: Path) -> tuple[Path, Path, Path]:
    return (
        data_dir / "codex_session_tracker.json",
        data_dir / "session-events" / "inbox",
        data_dir / "session-registry.json",
    )


def observe_session_context(data_dir: Path) -> dict[str, int]:
    tracker_path, inbox_path, registry_path = session_observation_paths(data_dir)
    consume_session_events(
        inbox_path=inbox_path,
        registry_path=registry_path,
    )
    detected = detect_codex_current_model(
        cache_path=tracker_path,
        event_inbox_path=inbox_path,
        registry_path=registry_path,
        persist_cache=True,
        consume_registry_events=False,
    )
    external_sessions = detect_external_model_sessions(
        event_inbox_path=inbox_path,
        registry_path=registry_path,
        consume_registry_events=False,
    )
    return {
        "codex_session_count": len(detected.display_sessions) if detected else 0,
        "external_session_count": len(external_sessions),
    }


@dataclass(frozen=True)
class ObservationCommand:
    snapshot_command: SnapshotCommand

    def observe_state(
        self,
        *,
        include_codex_insights: bool,
        session_observer: SessionObserver = observe_session_context,
        build_insights: InsightsReader,
        read_insights: InsightsReader,
    ) -> dict[str, object]:
        data_dir = self.snapshot_command.data_dir
        session_counts = session_observer(data_dir)
        insights = (
            build_insights(data_dir)
            if include_codex_insights
            else read_insights(data_dir)
        )
        state = self.snapshot_command.build_snapshot(
            codex_insights=insights
        )
        refresh_state = {
            key: state[key]
            for key in (
                "config",
                "question_pack",
                "runtime",
                "codex_insights",
                "recommendation_use",
            )
            if key in state
        }
        return {
            "schema_version": 1,
            "ok": True,
            "action": "observe_state",
            "status": "observed",
            "message": "本机状态观察已更新。",
            "session_counts": session_counts,
            "state": project_refresh_snapshot_v1(refresh_state),
        }

    def refresh_reference(
        self,
        *,
        read_insights: InsightsReader,
    ) -> dict[str, object]:
        state = self.snapshot_command.build_snapshot(
            codex_insights_provider=read_insights,
            refresh_reference=True,
        )
        feed = state.get("reference_snapshot_feed")
        delivery = feed.get("delivery") if isinstance(feed, dict) else None
        refresh_status = (
            str(delivery.get("refresh_status") or "unknown")
            if isinstance(delivery, dict)
            else "unknown"
        )
        return {
            "schema_version": 1,
            "ok": True,
            "action": "refresh_reference",
            "status": refresh_status,
            "message": (
                "官网参考快照刷新失败，继续使用本地结果。"
                if refresh_status == "failed"
                else "官网参考快照状态已更新。"
            ),
            "state": state,
        }
