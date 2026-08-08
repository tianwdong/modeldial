from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Iterable

from .costing import install_pricing_snapshot, validate_pricing_snapshot


LOCAL_PRICING_REPORT_SCHEMA_VERSION = 1
_SAFE_SCOPE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def prepare_local_pricing_snapshot(
    *,
    backend_root: Path,
    data_root: Path,
    scope_id: str,
    historical_snapshot_ids: Iterable[str] = (),
    refresh: bool,
    fetch_upstream: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, object]:
    from devtools.pricing.updater import (
        execute_update,
        fetch_upstream_json,
        load_json_object,
        record_failed_update,
    )

    normalized_scope_id = scope_id.strip()
    if not _SAFE_SCOPE_ID.fullmatch(normalized_scope_id):
        raise ValueError("local pricing scope identity is invalid")

    pricing_root = data_root.expanduser().resolve() / "pricing"
    run_snapshot_path = pricing_root / "runs" / f"{normalized_scope_id}.json"
    report_path = pricing_root / "reports" / f"{normalized_scope_id}.json"
    if run_snapshot_path.is_file():
        snapshot_id = install_pricing_snapshot(run_snapshot_path)
        return {
            **(_optional_json_mapping(report_path) or {}),
            "schema_version": LOCAL_PRICING_REPORT_SCHEMA_VERSION,
            "status": "reused",
            "effective_snapshot_id": snapshot_id,
            "fallback_used": False,
        }

    backend_root = backend_root.expanduser().resolve()
    baked_snapshot_path = backend_root / "scanner" / "pricing_snapshot.json"
    current_snapshot_path = pricing_root / "current.json"
    snapshot_ids = {
        str(item).strip()
        for item in historical_snapshot_ids
        if str(item).strip()
    }
    if len(snapshot_ids) > 1:
        raise ValueError("local run contains mixed pricing snapshots")
    historical_snapshot_id = next(iter(snapshot_ids), None)
    if historical_snapshot_id is not None:
        source_path = _resolve_snapshot_path(
            snapshot_id=historical_snapshot_id,
            backend_root=backend_root,
            pricing_root=pricing_root,
            current_snapshot_path=current_snapshot_path,
            baked_snapshot_path=baked_snapshot_path,
            load_json_object=load_json_object,
            validate_snapshot=validate_pricing_snapshot,
        )
        if source_path is None:
            raise ValueError(
                "historical pricing snapshot is unavailable; start a new run"
            )
        snapshot = load_json_object(source_path)
        _write_json_atomic(run_snapshot_path, snapshot)
        snapshot_id = install_pricing_snapshot(run_snapshot_path)
        report = {
            "schema_version": LOCAL_PRICING_REPORT_SCHEMA_VERSION,
            "checked_at": _utc_now(),
            "status": "historical_reused",
            "effective_snapshot_id": snapshot_id,
            "fallback_used": False,
            "errors": [],
            "warnings": [],
        }
        _write_json_atomic(report_path, report)
        return report

    base_snapshot_path = _first_valid_snapshot_path(
        (current_snapshot_path, baked_snapshot_path),
        validate_snapshot=validate_pricing_snapshot,
    )
    if base_snapshot_path is None:
        raise ValueError("no validated local pricing snapshot is available")
    base_snapshot = load_json_object(base_snapshot_path)

    if not refresh:
        _write_json_atomic(run_snapshot_path, base_snapshot)
        snapshot_id = install_pricing_snapshot(run_snapshot_path)
        report = {
            "schema_version": LOCAL_PRICING_REPORT_SCHEMA_VERSION,
            "checked_at": _utc_now(),
            "status": "fallback_reused",
            "effective_snapshot_id": snapshot_id,
            "fallback_used": True,
            "errors": [],
            "warnings": ["run had no recorded pricing snapshot"],
        }
        _write_json_atomic(report_path, report)
        return report

    pricing_root.mkdir(parents=True, exist_ok=True)
    working_path = pricing_root / f".{normalized_scope_id}.working.json"
    candidate_path = pricing_root / f".{normalized_scope_id}.candidate.json"
    _write_json_atomic(working_path, base_snapshot)
    fetched_at = _utc_now()
    policy_path = Path(
        os.environ.get(
            "MODELDIAL_PRICING_POLICY",
            str(backend_root / "devtools" / "pricing" / "policy.json"),
        )
    )
    try:
        try:
            policy = load_json_object(policy_path)
            upstream = (
                fetch_upstream(policy)
                if fetch_upstream is not None
                else fetch_upstream_json(policy, timeout_seconds=10.0)
            )
            report = execute_update(
                snapshot_path=working_path,
                upstream_payload=upstream,
                policy=policy,
                fetched_at=fetched_at,
                candidate_path=candidate_path,
                report_path=report_path,
                apply=True,
            )
        except Exception as error:
            report = record_failed_update(
                snapshot_path=working_path,
                report_path=report_path,
                fetched_at=fetched_at,
                error=error,
            )

        effective_snapshot = load_json_object(working_path)
        _write_json_atomic(current_snapshot_path, effective_snapshot)
        snapshot_id = str(effective_snapshot.get("snapshot_id") or "")
        if not _SAFE_SCOPE_ID.fullmatch(snapshot_id):
            raise ValueError("effective local pricing snapshot has no identity")
        archive_path = pricing_root / "snapshots" / f"{snapshot_id}.json"
        _write_json_atomic(archive_path, effective_snapshot)
        _write_json_atomic(run_snapshot_path, effective_snapshot)
        installed_snapshot_id = install_pricing_snapshot(run_snapshot_path)
        report = {
            **report,
            "effective_snapshot_id": installed_snapshot_id,
            "fallback_used": report.get("status") == "failed",
        }
        _write_json_atomic(report_path, report)
        return report
    finally:
        working_path.unlink(missing_ok=True)
        candidate_path.unlink(missing_ok=True)


def _resolve_snapshot_path(
    *,
    snapshot_id: str,
    backend_root: Path,
    pricing_root: Path,
    current_snapshot_path: Path,
    baked_snapshot_path: Path,
    load_json_object: Callable[[Path], dict[str, Any]],
    validate_snapshot: Callable[[Path], str],
) -> Path | None:
    if not _SAFE_SCOPE_ID.fullmatch(snapshot_id):
        return None
    candidates = (
        current_snapshot_path,
        pricing_root / "snapshots" / f"{snapshot_id}.json",
        baked_snapshot_path,
        backend_root / "scanner" / "pricing_snapshots" / f"{snapshot_id}.json",
    )
    for candidate in candidates:
        try:
            if (
                candidate.is_file()
                and validate_snapshot(candidate) == snapshot_id
                and load_json_object(candidate).get("snapshot_id") == snapshot_id
            ):
                return candidate
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return None


def _first_valid_snapshot_path(
    candidates: Iterable[Path],
    *,
    validate_snapshot: Callable[[Path], str],
) -> Path | None:
    for candidate in candidates:
        try:
            if candidate.is_file() and validate_snapshot(candidate):
                return candidate
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return None


def _optional_json_mapping(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
