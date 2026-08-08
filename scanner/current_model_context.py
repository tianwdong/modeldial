from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .codex_current_model import DetectedCodexModel
from .models import AppConfig
from .model_sessions import DetectedModelSession


CurrentModelDetector = Callable[[], DetectedCodexModel | None]
ActiveSessionDetector = Callable[[], tuple[DetectedModelSession, ...]]

_TERMINAL_SOURCE_KINDS = {
    "codex": {"codex"},
    "claude": {"claude", "claude_code"},
    "grok": {"grok", "grok_build"},
}


def _terminal_source_matches(source: object, terminal_source: str) -> bool:
    source_id = str(getattr(source, "id", "")).strip().lower()
    source_kind = str(getattr(source, "kind", "")).strip().lower()
    source_mode = str(getattr(source, "mode", "")).strip().lower()
    terminal_source = terminal_source.strip().lower()
    accepted_kinds = _TERMINAL_SOURCE_KINDS.get(
        terminal_source,
        {terminal_source},
    )
    return source_mode == "local" and (
        source_kind in accepted_kinds
        or source_id == terminal_source
        or source_id == f"{terminal_source}_local"
    )


def _terminal_model_matches(
    *,
    terminal_source: str,
    observed_model: str,
    configured_model: str,
    configured_family: str | None,
) -> bool:
    observed = observed_model.strip().lower()
    candidates = {
        configured_model.strip().lower(),
        (configured_family or "").strip().lower(),
    } - {""}
    if observed in candidates:
        return True
    if terminal_source.strip().lower() != "claude":
        return False
    observed_parts = set(observed.replace("_", "-").split("-"))
    return any(
        candidate in {"sonnet", "opus", "haiku"}
        and candidate in observed_parts
        for candidate in candidates
    )


def _matching_terminal_candidate_ids(
    config: AppConfig,
    session: DetectedModelSession,
) -> tuple[str, ...]:
    if not session.model or not session.effort:
        return ()
    matching_source_ids = {
        source.id
        for source in config.model_ingress.sources
        if source.enabled and _terminal_source_matches(source, session.source)
    }
    matches: list[str] = []
    for connection in config.model_ingress.connections:
        if not connection.enabled or connection.source_id not in matching_source_ids:
            continue
        for candidate in connection.model_candidates:
            if (
                candidate.enabled
                and candidate.scan_profile.strip().lower()
                == session.effort.strip().lower()
                and _terminal_model_matches(
                    terminal_source=session.source,
                    observed_model=session.model,
                    configured_model=candidate.model_id,
                    configured_family=candidate.family_id,
                )
            ):
                matches.append(candidate.id)
    return tuple(dict.fromkeys(matches))


def _deduplicated_terminal_sessions(
    sessions: list[DetectedModelSession],
) -> list[DetectedModelSession]:
    deduplicated: list[DetectedModelSession] = []
    seen: set[tuple[str, str]] = set()
    for session in sessions:
        key = (session.source, session.id)
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(session)
    return deduplicated


@dataclass(frozen=True)
class CurrentModelContextQuery:
    current_model_detector: CurrentModelDetector
    active_session_detector: ActiveSessionDetector

    def build(self, config: AppConfig) -> dict[str, object]:
        configured_candidate_id = config.recommendation.current_default_candidate_id
        try:
            detected = self.current_model_detector()
        except OSError:
            detected = None
        try:
            external_sessions = self.active_session_detector()
        except OSError:
            external_sessions = ()

        visible_codex_sessions = ()
        if detected is not None:
            visible_codex_sessions = detected.display_sessions or detected.active_sessions
        codex_display_sessions = [
            DetectedModelSession(
                id=session.id,
                source="codex",
                workspace_name=session.workspace_name,
                model=session.model,
                effort=session.effort,
                thread_name=session.thread_name,
                last_active_at=session.last_active_at,
                is_currently_producing=session.is_currently_producing,
                is_evaluation_session=bool(
                    getattr(session, "is_modeldial_scan", False)
                ),
            )
            for session in visible_codex_sessions
        ]
        display_session_models = _deduplicated_terminal_sessions(
            codex_display_sessions + list(external_sessions)
        )

        codex_active_sessions = []
        if detected is not None and detected.status in {
            "active_single",
            "active_mixed",
        }:
            codex_active_sessions = [
                DetectedModelSession(
                    id=session.id,
                    source="codex",
                    workspace_name=session.workspace_name,
                    model=session.model,
                    effort=session.effort,
                    thread_name=session.thread_name,
                    last_active_at=session.last_active_at,
                    is_currently_producing=session.is_currently_producing,
                )
                for session in detected.active_sessions
            ]
        active_sessions = _deduplicated_terminal_sessions(
            codex_active_sessions + list(external_sessions)
        )

        detected_candidate_id: str | None = None
        detected_model: str | None = None
        detected_effort: str | None = None
        detected_at = detected.detected_at if detected else None
        active_model_identities: list[tuple[str, str, str]] = []
        for session in active_sessions:
            if not session.model or not session.effort:
                continue
            identity = (
                session.source.strip().lower(),
                session.model.strip(),
                session.effort.strip().lower(),
            )
            if identity not in active_model_identities:
                active_model_identities.append(identity)

        if active_sessions:
            has_incomplete_identity = any(
                not session.model or not session.effort
                for session in active_sessions
            )
            if len(active_model_identities) == 1 and not has_incomplete_identity:
                _, detected_model, detected_effort = active_model_identities[0]
                matching_ids = {
                    candidate_id
                    for session in active_sessions
                    for candidate_id in _matching_terminal_candidate_ids(
                        config,
                        session,
                    )
                }
                if len(matching_ids) == 1:
                    detected_candidate_id = next(iter(matching_ids))
                    detection_status = "active_single"
                else:
                    detection_status = "unmapped"
            elif len(active_sessions) == 1 and not active_model_identities:
                detection_status = "unmapped"
            else:
                detection_status = "active_mixed"
        elif detected is not None and detected.status != "scan_only":
            detected_model = detected.model
            detected_effort = detected.effort
            recent_session = DetectedModelSession(
                id="recent-codex-session",
                source="codex",
                workspace_name="Codex",
                model=detected.model,
                effort=detected.effort,
            )
            matching_ids = _matching_terminal_candidate_ids(config, recent_session)
            if len(matching_ids) == 1:
                detected_candidate_id = matching_ids[0]
                detection_status = detected.status
            else:
                detection_status = "unmapped"
        elif detected is not None:
            detection_status = detected.status
        else:
            detection_status = "unavailable"

        if (
            config.recommendation.current_model_mode == "manual"
            and configured_candidate_id
        ):
            effective_candidate_id = configured_candidate_id
            source = "manual"
        elif detected_candidate_id:
            effective_candidate_id = detected_candidate_id
            source = "terminal_session"
        elif active_sessions or (
            detected is not None and detected.status != "scan_only"
        ):
            effective_candidate_id = None
            source = "terminal_session"
        else:
            effective_candidate_id = None
            source = "unavailable"

        display_sessions = [
            {
                "id": session.id,
                "source": session.source,
                "workspace_name": session.workspace_name,
                "model": session.model,
                "effort": session.effort,
                "thread_name": session.thread_name,
                "is_evaluation_session": session.is_evaluation_session,
            }
            for session in display_session_models
        ]
        active_configuration_sessions = []
        for session in active_sessions:
            matching_ids = _matching_terminal_candidate_ids(config, session)
            active_configuration_sessions.append(
                {
                    "candidate_id": (
                        matching_ids[0] if len(matching_ids) == 1 else None
                    ),
                    "mapping_status": (
                        "matched"
                        if len(matching_ids) == 1
                        else "ambiguous"
                        if matching_ids
                        else "unmapped"
                    ),
                    "last_active_at": session.last_active_at,
                    "is_currently_producing": session.is_currently_producing,
                }
            )
        active_model_pairs: list[tuple[str, str]] = []
        for _, model, effort in active_model_identities:
            pair = (model, effort)
            if pair not in active_model_pairs:
                active_model_pairs.append(pair)
        return {
            "effective_candidate_id": effective_candidate_id,
            "source": source,
            "detection_status": detection_status,
            "detected_at": detected_at,
            "model": detected_model,
            "effort": detected_effort,
            "active_session_count": len(active_sessions),
            "active_models": [
                {"model": model, "effort": effort}
                for model, effort in active_model_pairs
            ],
            "active_sessions": [
                {
                    "id": session.id,
                    "workspace_name": session.workspace_name,
                    "model": session.model,
                    "effort": session.effort,
                    "thread_name": session.thread_name,
                }
                for session in active_sessions
            ],
            "active_configuration_sessions": active_configuration_sessions,
            "display_sessions": display_sessions,
        }
