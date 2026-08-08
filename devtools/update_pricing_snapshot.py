from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from devtools.pricing.updater import (
    execute_update,
    fetch_upstream_json,
    load_json_object,
    load_upstream_json,
    record_failed_update,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build and validate a ModelDial pricing snapshot candidate.",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=PROJECT_ROOT / "scanner" / "pricing_snapshot.json",
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=PROJECT_ROOT / "devtools" / "pricing" / "policy.json",
    )
    parser.add_argument("--source-file", type=Path)
    parser.add_argument(
        "--candidate",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "pricing" / "pricing_snapshot.candidate.json",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "pricing" / "pricing_update_report.json",
    )
    parser.add_argument("--include-model", action="append", default=[])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Atomically replace the validated snapshot. The default is dry-run.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        policy = load_json_object(args.policy)
        upstream = (
            load_upstream_json(args.source_file, policy)
            if args.source_file is not None
            else fetch_upstream_json(policy)
        )
        report = execute_update(
            snapshot_path=args.snapshot,
            upstream_payload=upstream,
            policy=policy,
            fetched_at=fetched_at,
            candidate_path=args.candidate,
            report_path=args.report,
            apply=args.apply,
            requested_models=tuple(args.include_model),
        )
    except Exception as exc:
        report = record_failed_update(
            snapshot_path=args.snapshot,
            report_path=args.report,
            fetched_at=fetched_at,
            error=exc,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
