from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEED_ROOT = PROJECT_ROOT / "scanner" / "reference_snapshots"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scanner.costing import estimate_reference_cost
from scanner.models import ScanResult
from scanner.reference_snapshot import (
    build_reference_snapshot_leaderboard_projection,
    reference_snapshot_hash,
)


SEED_GENERATED_AT = "2000-01-01T00:00:00Z"
SEED_PUBLISHED_AT = (
    "2000-01-01T00:00:00Z",
    "2000-01-02T00:00:00Z",
    "2000-01-03T00:00:00Z",
    "2000-01-04T00:00:00Z",
)
SEED_ENTRY_COUNTS = (12, 13, 15, 18)
SEED_CANDIDATES = (
    ("codex-local-default:gpt-5.6-sol:ultra", "gpt-5.6-sol", "ultra"),
    ("codex-local-default:gpt-5.6-sol:xhigh", "gpt-5.6-sol", "xhigh"),
    ("codex-local-default:gpt-5.6-sol:max", "gpt-5.6-sol", "max"),
    ("codex-local-default:gpt-5.6-terra:max", "gpt-5.6-terra", "max"),
    ("codex-local-default:gpt-5.6-terra:ultra", "gpt-5.6-terra", "ultra"),
    ("codex-local-default:gpt-5.6-sol:medium", "gpt-5.6-sol", "medium"),
    ("codex-local-default:gpt-5.6-sol:high", "gpt-5.6-sol", "high"),
    ("codex-local-default:gpt-5.6-luna:xhigh", "gpt-5.6-luna", "xhigh"),
    ("codex-local-default:gpt-5.5:xhigh", "gpt-5.5", "xhigh"),
    ("codex-local-default:gpt-5.6-luna:max", "gpt-5.6-luna", "max"),
    ("codex-local-default:gpt-5.6-terra:xhigh", "gpt-5.6-terra", "xhigh"),
    ("codex-local-default:gpt-5.6-sol:low", "gpt-5.6-sol", "low"),
    ("codex-local-default:gpt-5.5:high", "gpt-5.5", "high"),
    ("codex-local-default:gpt-5.6-terra:high", "gpt-5.6-terra", "high"),
    ("codex-local-default:gpt-5.5:medium", "gpt-5.5", "medium"),
    ("synthetic-endpoint-a:synthetic-model-a:high", "synthetic-model-a", "high"),
    ("codex-local-default:gpt-5.6-luna:high", "gpt-5.6-luna", "high"),
    (
        "synthetic-endpoint-b:synthetic-model-b:default",
        "synthetic-model-b",
        "default",
    ),
)


def build_seed_snapshots() -> list[dict[str, object]]:
    """Build a deterministic public fixture without reading local App data."""
    question_semantics = _seed_question_semantics()
    question_ids = [str(item["id"]) for item in question_semantics]
    snapshots: list[dict[str, object]] = []
    for batch_index, (published_at, entry_count) in enumerate(
        zip(SEED_PUBLISHED_AT, SEED_ENTRY_COUNTS, strict=True),
        start=1,
    ):
        snapshot: dict[str, object] = {
            "schema_version": 1,
            "kind": "development_seed",
            "batch_id": f"synthetic-reference-seed-v1-{batch_index:02d}",
            "revision": 1,
            "run_date": published_at[:10],
            "status": "complete",
            "generated_at": SEED_GENERATED_AT,
            "published_at": published_at,
            "question_pack_id": "coding-fast",
            "question_pack_version": "coding-fast-v4.10",
            "grader_version": "scoring-mode:semantic_q1_q5_equal_v2",
            "evaluation_profile": "full",
            "score_baseline_id": "coding-fast-v4.10:synthetic-v1",
            "pricing_snapshot_id": "synthetic-pricing-v1",
            "retry_policy": {
                "mode": "synthetic_app_policy_v1",
                "max_attempts_per_question": None,
                "selective_score_retry": None,
            },
            "provenance": {
                "kind": "development_seed",
                "source": "synthetic_fixture",
                "public_official_snapshot": False,
            },
            "question_ids": question_ids,
            "entry_count": entry_count,
            "entries": [
                _build_synthetic_entry(
                    candidate_index,
                    batch_index=batch_index,
                    published_at=published_at,
                    question_ids=question_ids,
                )
                for candidate_index in range(entry_count)
            ],
            "revision_reason": None,
        }
        snapshot["leaderboard_projection"] = (
            build_reference_snapshot_leaderboard_projection(
                snapshot,
                question_semantics=question_semantics,
                prior_snapshots=snapshots,
            )
        )
        snapshot["batch_sha256"] = reference_snapshot_hash(snapshot)
        snapshots.append(snapshot)
    return snapshots


def _seed_question_semantics() -> list[dict[str, object]]:
    catalog = _read_json_object(PROJECT_ROOT / "questions" / "catalog.json")
    questions = catalog.get("questions")
    if not isinstance(questions, list):
        raise ValueError("question catalog is missing questions")
    return [
        {
            "id": str(question["id"]),
            "ordinal": ordinal,
            "short_label": f"Q{ordinal}",
            "title": str(question["title"]),
            "capability_id": str(question["capability_id"]),
            "capability_label": str(question["capability_label"]),
            "detail_label": str(question["detail_label"]),
        }
        for ordinal, question in enumerate(questions, start=1)
        if isinstance(question, Mapping)
    ]


def _build_synthetic_entry(
    candidate_index: int,
    *,
    batch_index: int,
    published_at: str,
    question_ids: Sequence[str],
) -> dict[str, object]:
    candidate_id, model_id, effort = SEED_CANDIDATES[candidate_index]
    is_custom = candidate_id.startswith("synthetic-endpoint-")
    score = max(50, 91 - (candidate_index * 2) + batch_index)
    base_question_score, remainder = divmod(score, len(question_ids))
    question_scores = {
        question_id: base_question_score + (1 if index < remainder else 0)
        for index, question_id in enumerate(question_ids)
    }
    input_tokens = 18_000 + candidate_index * 1_100 + batch_index * 200
    output_tokens = 4_000 + candidate_index * 350 + batch_index * 100
    reasoning_tokens = 2_000 + candidate_index * 250 + batch_index * 75
    return {
        "model_configuration_id": candidate_id,
        "model_configuration": {
            "provider_id": "synthetic" if is_custom else "openai",
            "raw_model_id": model_id,
            "canonical_model_id": model_id,
            "display_name": f"{model_id} / {effort}",
            "reasoning_effort": effort,
            "service_tier": "synthetic_fixture" if is_custom else "chatgpt_subscription",
            "route_type": "custom_endpoint" if is_custom else "official_login",
        },
        "advisor_eligible": not is_custom,
        "score_integrity": "synthetic_fixture",
        "route_identity": "synthetic_fixture",
        "route_fingerprint": (
            f"synthetic-route-{candidate_index:02d}" if is_custom else None
        ),
        "score": score,
        "max_score": 100,
        "question_scores": question_scores,
        "elapsed_ms": 240_000 + candidate_index * 35_000 - batch_index * 5_000,
        "usage": {
            "input_tokens": input_tokens,
            "cached_input_tokens": input_tokens // 5,
            "cache_write_input_tokens": 0,
            "output_tokens": output_tokens,
            "reasoning_tokens": reasoning_tokens,
        },
        "estimated_api_cost_usd": round(
            0.2 + candidate_index * 0.075 + batch_index * 0.01,
            6,
        ),
        "cost_coverage": "complete",
        "attempt_count": 5,
        "failure_count": (candidate_index + batch_index) % 2,
        "hard_failure_count": 0,
        "completed_at": published_at,
        "source_evidence_group_id": None,
    }


def write_seed_feed(
    snapshots: Sequence[Mapping[str, object]],
    output_root: Path,
) -> None:
    """Write the deterministic synthetic seed feed."""
    if not snapshots:
        raise ValueError("cannot write an empty development seed feed")
    archive_root = output_root / "archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, object]] = []
    for snapshot in snapshots:
        filename = f"{snapshot['batch_id']}.json"
        relative_path = f"archive/{filename}"
        _write_json_atomic(archive_root / filename, snapshot)
        summaries.append(
            {
                "batch_id": snapshot["batch_id"],
                "published_at": snapshot["published_at"],
                "question_pack_version": snapshot["question_pack_version"],
                "score_baseline_id": snapshot["score_baseline_id"],
                "entry_count": snapshot["entry_count"],
                "batch_sha256": snapshot["batch_sha256"],
                "path": relative_path,
            }
        )
    latest = snapshots[-1]
    _write_json_atomic(output_root / "latest.json", latest)
    _write_json_atomic(
        output_root / "index.json",
        {
            "schema_version": 1,
            "kind": "development_seed",
            "generated_at": SEED_GENERATED_AT,
            "latest_batch_id": latest["batch_id"],
            "latest_path": "latest.json",
            "snapshots": summaries,
        },
    )


def _seed_reference_cost_summary(
    evidence: Sequence[ScanResult],
) -> tuple[float | None, str]:
    completed = [item for item in evidence if _is_completed_model_call(item)]
    costs: list[float] = []
    for item in completed:
        if item.reference_cost_usd is not None:
            costs.append(float(item.reference_cost_usd))
            continue
        estimate = estimate_reference_cost(
            item.model,
            input_tokens=item.input_tokens,
            cached_input_tokens=item.cached_input_tokens,
            cache_write_input_tokens=item.cache_write_input_tokens,
            output_tokens=item.output_tokens,
            reasoning_output_tokens=item.reasoning_tokens,
        )
        if estimate.usd is not None:
            costs.append(estimate.usd)
    if completed and len(costs) == len(completed):
        return round(sum(costs), 6), "complete"
    if costs:
        return round(sum(costs), 6), "partial"
    return None, "unknown"


def _seed_reference_usage(evidence: Sequence[ScanResult]) -> dict[str, int]:
    completed = [item for item in evidence if _is_completed_model_call(item)]
    return {
        "input_tokens": sum(max(0, item.input_tokens or 0) for item in completed),
        "cached_input_tokens": sum(
            max(0, item.cached_input_tokens or 0) for item in completed
        ),
        "cache_write_input_tokens": sum(
            max(0, item.cache_write_input_tokens or 0) for item in completed
        ),
        "output_tokens": sum(max(0, item.output_tokens or 0) for item in completed),
        "reasoning_tokens": sum(
            max(0, item.reasoning_tokens or 0) for item in completed
        ),
    }


def _is_completed_model_call(item: ScanResult) -> bool:
    terminal_state = str(item.execution_trace.get("terminal_state") or "").strip()
    if terminal_state:
        return terminal_state in {"completed_response", "completed_turn"}
    return item.error_message is None


def _read_json_object(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON file must contain an object: {path}")
    return payload


def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the deterministic synthetic public development seed."
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_SEED_ROOT,
    )
    args = parser.parse_args()
    snapshots = build_seed_snapshots()
    write_seed_feed(snapshots, args.output_root)
    print(
        json.dumps(
            {
                "snapshot_count": len(snapshots),
                "entry_counts": [item["entry_count"] for item in snapshots],
                "latest_batch_id": snapshots[-1]["batch_id"],
                "output_root": str(args.output_root),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
