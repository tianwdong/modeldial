from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from functools import wraps
from typing import ParamSpec


RUNTIME_EVENT_SCHEMA_VERSION = 1
APP_SNAPSHOT_SCHEMA_VERSION = 2
REFRESH_SNAPSHOT_SCHEMA_VERSION = 1

P = ParamSpec("P")
_MISSING_STATE = object()


_RUNTIME_EVENT_STATE_KINDS: dict[str, frozenset[str]] = {
    "auto-resume.started": frozenset({"none"}),
    "auto-resume.noop": frozenset({"snapshot"}),
    "auto-resume.manual-attention": frozenset({"snapshot"}),
    "scan.started": frozenset({"runtime_delta"}),
    "target.started": frozenset({"runtime_delta"}),
    "scan.progress": frozenset({"runtime_delta"}),
    "scan.finalizing": frozenset({"runtime_delta"}),
    "scan.finished": frozenset({"snapshot"}),
    "scan.paused": frozenset({"snapshot"}),
    "scan.stopped": frozenset({"snapshot"}),
    "scan.already_running": frozenset({"snapshot"}),
    "scan.failed": frozenset({"snapshot"}),
    "repair.started": frozenset({"runtime_delta"}),
    "repair.question.started": frozenset({"runtime_delta"}),
    "repair.question.finished": frozenset({"runtime_delta"}),
    "repair.finalizing": frozenset({"runtime_delta"}),
    "repair.finished": frozenset({"snapshot"}),
    "repair.paused": frozenset({"snapshot"}),
    "repair.stopped": frozenset({"snapshot"}),
    "repair.already_running": frozenset({"snapshot"}),
    "repair.failed": frozenset({"snapshot"}),
    "timeout-repair.started": frozenset({"runtime_delta"}),
    "timeout-repair.question.started": frozenset({"runtime_delta"}),
    "timeout-repair.question.finished": frozenset({"runtime_delta"}),
    "timeout-repair.finalizing": frozenset({"runtime_delta"}),
    "timeout-repair.finished": frozenset({"snapshot"}),
    "timeout-repair.paused": frozenset({"snapshot"}),
    "timeout-repair.stopped": frozenset({"snapshot"}),
    "timeout-repair.already_running": frozenset({"snapshot"}),
    "timeout-repair.failed": frozenset({"snapshot"}),
}


def project_app_snapshot_v2(
    snapshot: Mapping[str, object],
) -> dict[str, object]:
    """Project an unversioned MonitorService state or validate AppSnapshotV2."""
    projected = dict(snapshot)
    explicit_version = projected.pop("schema_version", None)
    if explicit_version not in {None, APP_SNAPSHOT_SCHEMA_VERSION}:
        raise ValueError("unsupported app snapshot schema version")
    projected.pop("history", None)
    for required_key in (
        "config",
        "dashboard",
        "runtime",
        "question_pack",
        "settings_projection",
        "advisor_v2_evidence",
        "recommendation_portfolio_v2",
        "reference_snapshot_feed",
        "recommendation_use",
    ):
        if not isinstance(projected.get(required_key), Mapping):
            raise ValueError(f"app snapshot is missing {required_key}")
    return {
        "schema_version": APP_SNAPSHOT_SCHEMA_VERSION,
        **projected,
    }


def project_refresh_snapshot_v1(
    snapshot: Mapping[str, object],
) -> dict[str, object]:
    projected = dict(snapshot)
    explicit_version = projected.pop("schema_version", None)
    if explicit_version not in {None, REFRESH_SNAPSHOT_SCHEMA_VERSION}:
        raise ValueError("unsupported refresh snapshot schema version")
    for required_key in ("config", "runtime"):
        if not isinstance(projected.get(required_key), Mapping):
            raise ValueError(f"refresh snapshot is missing {required_key}")
    return {
        "schema_version": REFRESH_SNAPSHOT_SCHEMA_VERSION,
        **projected,
    }


def project_runtime_event_v1(
    event: Mapping[str, object],
) -> dict[str, object]:
    projected = dict(event)
    explicit_version = projected.pop("schema_version", None)
    if explicit_version not in {None, RUNTIME_EVENT_SCHEMA_VERSION}:
        raise ValueError("unsupported runtime event schema version")

    event_type = projected.get("type")
    if not isinstance(event_type, str) or event_type not in _RUNTIME_EVENT_STATE_KINDS:
        raise ValueError("unsupported runtime event type")

    state_kind = _runtime_event_state_kind(projected.get("state", _MISSING_STATE))
    if state_kind == "invalid":
        raise ValueError("runtime event state is not a supported projection")
    explicit_state_kind = projected.pop("state_kind", None)
    if explicit_state_kind not in {None, state_kind}:
        raise ValueError("runtime event state kind does not match payload")
    if state_kind not in _RUNTIME_EVENT_STATE_KINDS[event_type]:
        raise ValueError(
            f"runtime event {event_type} does not allow state kind {state_kind}"
        )
    if state_kind == "snapshot":
        state = projected.get("state")
        assert isinstance(state, Mapping)
        projected["state"] = project_app_snapshot_v2(state)

    return {
        "schema_version": RUNTIME_EVENT_SCHEMA_VERSION,
        "state_kind": state_kind,
        **projected,
    }


def version_runtime_event_stream(
    stream: Callable[P, Iterator[dict[str, object]]],
) -> Callable[P, Iterator[dict[str, object]]]:
    @wraps(stream)
    def versioned(*args: P.args, **kwargs: P.kwargs) -> Iterator[dict[str, object]]:
        for event in stream(*args, **kwargs):
            yield project_runtime_event_v1(event)

    return versioned


def _runtime_event_state_kind(state: object) -> str:
    if state is _MISSING_STATE:
        return "none"
    if not isinstance(state, Mapping):
        return "invalid"
    if (
        state.get("schema_version") == 1
        and isinstance(state.get("runtime"), Mapping)
        and "config" not in state
    ):
        return "runtime_delta"
    if state.get("schema_version") == APP_SNAPSHOT_SCHEMA_VERSION:
        return "snapshot"
    return "invalid"
