from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from devtools.pricing.catalog import build_pricing_catalog


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build an immutable ModelDial pricing catalog.",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=PROJECT_ROOT / "scanner" / "pricing_snapshot.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "pricing" / "catalog",
    )
    parser.add_argument("--published-at")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        manifest = build_pricing_catalog(
            snapshot_path=args.snapshot,
            output_root=args.output,
            published_at=args.published_at,
        )
    except (OSError, TypeError, ValueError) as error:
        print(
            json.dumps(
                {"status": "failed", "error": str(error)},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 1
    print(
        json.dumps(
            {"status": "built", **manifest},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
