from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
import time

from .codex_account import CodexAccountError, read_codex_account_snapshot
from .quota_burn import build_quota_burn_summary
from .usage_observer import observe_codex_usage, read_codex_usage
from .usage_store import UsageStore


ACCOUNT_SNAPSHOT_TTL_SECONDS = 60


def build_codex_insights(
    data_dir: Path,
    *,
    account_reader: Callable[..., dict[str, object]] = read_codex_account_snapshot,
    usage_observer: Callable[..., dict[str, object]] = observe_codex_usage,
    force_account_refresh: bool = False,
) -> dict[str, object]:
    store = UsageStore(data_dir)
    now = datetime.now(timezone.utc)
    cached_account = store.load_account_snapshot()
    account = None
    app_server_status = "unavailable"
    app_server_read_duration_ms: int | None = None
    if cached_account is not None and not force_account_refresh:
        captured_at = _parse_datetime(cached_account.get("captured_at"))
        if (
            captured_at is not None
            and (now - captured_at).total_seconds()
            <= ACCOUNT_SNAPSHOT_TTL_SECONDS
        ):
            account = cached_account
            app_server_status = "cached"
    if account is None:
        read_started_at = time.monotonic()
        try:
            account = account_reader()
        except (CodexAccountError, OSError, ValueError):
            app_server_read_duration_ms = round(
                (time.monotonic() - read_started_at) * 1000
            )
            if cached_account is not None:
                account = {**cached_account, "cache_status": "stale"}
                app_server_status = "stale"
            else:
                account = _unavailable_account(now)
                app_server_status = "unavailable"
        else:
            app_server_read_duration_ms = round(
                (time.monotonic() - read_started_at) * 1000
            )
            app_server_status = "fresh"
            store.save_account_snapshot(account)
    elif account.get("cache_status") != "stale":
        store.save_account_snapshot(account)
    try:
        workload = usage_observer(store=store, now=now)
    except (OSError, ValueError):
        workload = {
            "schema_version": 1,
            "status": "unavailable",
            "captured_at": _iso_timestamp(now),
            "coverage_complete": False,
            "observation_count": 0,
            "aggregates": [],
        }
    return _project_insights(
        store=store,
        account=account,
        workload=workload,
        app_server_status=app_server_status,
        app_server_read_duration_ms=app_server_read_duration_ms,
    )


def read_codex_insights(data_dir: Path) -> dict[str, object]:
    store = UsageStore(data_dir)
    now = datetime.now(timezone.utc)
    account = store.load_account_snapshot()
    if account is None:
        account = _unavailable_account(now)
        app_server_status = "unavailable"
    else:
        captured_at = _parse_datetime(account.get("captured_at"))
        app_server_status = (
            "cached"
            if captured_at is not None
            and (now - captured_at).total_seconds()
            <= ACCOUNT_SNAPSHOT_TTL_SECONDS
            else "stale"
        )
    workload = read_codex_usage(store=store, now=now)
    return _project_insights(
        store=store,
        account=account,
        workload=workload,
        app_server_status=app_server_status,
        app_server_read_duration_ms=None,
    )


def _project_insights(
    *,
    store: UsageStore,
    account: dict[str, object],
    workload: dict[str, object],
    app_server_status: str,
    app_server_read_duration_ms: int | None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "account": account,
        "workload": workload,
        "quota_burn": build_quota_burn_summary(
            store.load_account_snapshots(),
            store.load_usage_state(),
            workload,
        ),
        "collection": {
            "app_server": {
                "status": app_server_status,
                "last_read_at": account.get("captured_at"),
                "read_duration_ms": app_server_read_duration_ms,
                "model_catalog_status": "not_checked",
            }
        },
    }


def _unavailable_account(now: datetime) -> dict[str, object]:
    return {
        "schema_version": 1,
        "captured_at": _iso_timestamp(now),
        "source": "codex_app_server",
        "account_type": "unknown",
        "login_state": "unknown",
        "quota_status": "unavailable",
        "quota_windows": [],
        "usage_status": "unavailable",
        "usage_summary": None,
        "daily_usage": [],
        "unavailable_capabilities": [
            "account",
            "rate_limits",
            "account_usage",
        ],
    }


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_timestamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")
