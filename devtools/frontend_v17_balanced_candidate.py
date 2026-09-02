from __future__ import annotations

import argparse
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import math
from pathlib import Path
import statistics
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
QUESTION_ROOT = (
    PROJECT_ROOT / "questions" / "frontend" / "frontend_v17_balanced_v1"
)
DEFAULT_CONTRACT = QUESTION_ROOT / "score-contract.json"
DEFAULT_CALIBRATION_MANIFEST = QUESTION_ROOT / "calibration-manifest.json"
CANDIDATE_ID = "case_stream_explorer_v17_balanced_progress_v1"
CONTRACT_SCHEMA = "frontend_v17_balanced_progress_contract_v1"
CALIBRATION_SCHEMA = "frontend_v17_balanced_progress_calibration_manifest_v1"
RESULT_SCHEMA = "frontend_v17_balanced_progress_score_v1"
REPORT_SCHEMA = "frontend_v17_balanced_progress_calibration_report_v1"


class BalancedCandidateError(RuntimeError):
    pass


def _round_score(value: float, precision: int = 6) -> float:
    quantum = Decimal(1).scaleb(-precision)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BalancedCandidateError(f"cannot read JSON: {path}") from error
    if not isinstance(value, dict):
        raise BalancedCandidateError(f"JSON object required: {path}")
    return value


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _number(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BalancedCandidateError(f"numeric value required: {label}")
    result = float(value)
    if not math.isfinite(result):
        raise BalancedCandidateError(f"finite value required: {label}")
    return result


def load_contract(path: Path = DEFAULT_CONTRACT) -> dict[str, Any]:
    contract = _load_json(path)
    if (
        contract.get("schema_version") != CONTRACT_SCHEMA
        or contract.get("candidate_id") != CANDIDATE_ID
        or contract.get("source_benchmark_ref")
        != "frontend-case-stream-explorer-v17@v2"
        or contract.get("score_precision") != 6
        or contract.get("total_points") != 100
    ):
        raise BalancedCandidateError("balanced score contract identity changed")
    layers = contract.get("layers")
    if not isinstance(layers, Mapping) or set(layers) != {
        "behavior",
        "workflow",
        "visual",
    }:
        raise BalancedCandidateError("balanced score layers changed")
    behavior = layers["behavior"]
    workflow = layers["workflow"]
    visual = layers["visual"]
    if not all(isinstance(item, Mapping) for item in (behavior, workflow, visual)):
        raise BalancedCandidateError("balanced score layer is invalid")
    if behavior.get("points") != 33 or workflow.get("points") != 22:
        raise BalancedCandidateError("balanced behavior/workflow totals changed")
    if sum(item["points"] for item in behavior["dimensions"].values()) != 33:
        raise BalancedCandidateError("balanced behavior dimensions changed")
    if sum(item["points"] for item in workflow["dimensions"].values()) != 22:
        raise BalancedCandidateError("balanced workflow dimensions changed")
    if (
        visual.get("points") != 45
        or visual.get("source_points") != 15
        or visual.get("progress_exponent") != 2
        or sum(visual.get("checks", {}).values()) != 15
    ):
        raise BalancedCandidateError("balanced visual contract changed")
    return contract


def _dimension_score(
    evidence: Mapping[str, Any],
    dimensions: Mapping[str, Any],
) -> tuple[float, dict[str, dict[str, float]]]:
    source_dimensions = evidence.get("dimensions")
    if not isinstance(source_dimensions, Mapping):
        raise BalancedCandidateError("source dimensions are unavailable")
    total = 0.0
    output: dict[str, dict[str, float]] = {}
    for dimension_id, rule in dimensions.items():
        source = source_dimensions.get(dimension_id)
        if not isinstance(rule, Mapping) or not isinstance(source, Mapping):
            raise BalancedCandidateError(
                f"source dimension is unavailable: {dimension_id}"
            )
        expected_max = _number(
            rule.get("source_max_points"), label=f"{dimension_id}.source_max"
        )
        source_max = _number(
            source.get("max_points"), label=f"{dimension_id}.max_points"
        )
        source_points = _number(
            source.get("points"), label=f"{dimension_id}.points"
        )
        target_max = _number(rule.get("points"), label=f"{dimension_id}.target")
        if source_max != expected_max or not 0 <= source_points <= source_max:
            raise BalancedCandidateError(
                f"source dimension range changed: {dimension_id}"
            )
        points = target_max * source_points / source_max
        total += points
        output[dimension_id] = {
            "points": _round_score(points),
            "max_points": target_max,
            "source_points": source_points,
            "source_max_points": source_max,
        }
    return total, output


def _visual_score(
    evidence: Mapping[str, Any],
    visual: Mapping[str, Any],
) -> tuple[float, dict[str, dict[str, float]]]:
    details = evidence.get("score_details")
    checks = visual.get("checks")
    if not isinstance(details, list) or not isinstance(checks, Mapping):
        raise BalancedCandidateError("source visual details are unavailable")
    source = {
        str(item.get("id")): item
        for item in details
        if isinstance(item, Mapping) and str(item.get("id")) in checks
    }
    if set(source) != set(checks):
        raise BalancedCandidateError("source visual check identity changed")
    multiplier = _number(visual.get("points"), label="visual.points") / _number(
        visual.get("source_points"), label="visual.source_points"
    )
    exponent = _number(visual.get("progress_exponent"), label="visual.exponent")
    total = 0.0
    output: dict[str, dict[str, float]] = {}
    for check_id, expected_max_value in checks.items():
        item = source[check_id]
        expected_max = _number(expected_max_value, label=f"{check_id}.expected_max")
        source_max = _number(item.get("max_points"), label=f"{check_id}.max")
        source_points = _number(item.get("points"), label=f"{check_id}.points")
        if source_max != expected_max or not 0 <= source_points <= source_max:
            raise BalancedCandidateError(f"source visual range changed: {check_id}")
        progress = source_points / source_max
        points = multiplier * source_max * progress**exponent
        total += points
        output[check_id] = {
            "points": _round_score(points),
            "max_points": multiplier * source_max,
            "source_points": source_points,
            "source_max_points": source_max,
            "progress": _round_score(progress),
        }
    return total, output


def display_score(raw_score: float, contract: Mapping[str, Any]) -> float:
    mapping = contract.get("display_mapping")
    if not isinstance(mapping, Mapping):
        raise BalancedCandidateError("display mapping is unavailable")
    starter_raw = _number(mapping.get("starter_raw"), label="starter_raw")
    starter_display = _number(
        mapping.get("starter_display"), label="starter_display"
    )
    reference_raw = _number(mapping.get("reference_raw"), label="reference_raw")
    reference_display = _number(
        mapping.get("reference_display"), label="reference_display"
    )
    exponent = _number(
        mapping.get("above_starter_exponent"), label="display_exponent"
    )
    if not 0 <= raw_score <= reference_raw:
        raise BalancedCandidateError("raw score is outside the display range")
    if raw_score <= starter_raw:
        return starter_display * raw_score / starter_raw
    progress = (raw_score - starter_raw) / (reference_raw - starter_raw)
    return starter_display + (reference_display - starter_display) * progress**exponent


def score_evidence(
    evidence: Mapping[str, Any],
    contract: Mapping[str, Any],
    *,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    layers = contract["layers"]
    behavior, behavior_dimensions = _dimension_score(
        evidence, layers["behavior"]["dimensions"]
    )
    workflow, workflow_dimensions = _dimension_score(
        evidence, layers["workflow"]["dimensions"]
    )
    visual, visual_checks = _visual_score(evidence, layers["visual"])
    precision = int(contract["score_precision"])
    raw = _round_score(behavior + workflow + visual, precision)
    display = _round_score(display_score(raw, contract), precision)
    return {
        "schema_version": RESULT_SCHEMA,
        "candidate_id": CANDIDATE_ID,
        "benchmark_ref": contract["benchmark_ref"],
        "source_benchmark_ref": contract["source_benchmark_ref"],
        "score_state": "complete",
        "raw_score": raw,
        "display_score": display,
        "layers": {
            "behavior": {
                "points": _round_score(behavior, precision),
                "max_points": 33,
                "dimensions": behavior_dimensions,
            },
            "workflow": {
                "points": _round_score(workflow, precision),
                "max_points": 22,
                "dimensions": workflow_dimensions,
            },
            "visual": {
                "points": _round_score(visual, precision),
                "max_points": 45,
                "checks": visual_checks,
            },
        },
        "source_score_sha256": source_sha256,
        "source_status": evidence.get("status"),
        "source_validity_state": evidence.get("validity_state"),
        "source_failed_check_ids": evidence.get("failed_check_ids", []),
        "all_or_nothing_gate": False,
    }


def score_evidence_file(
    path: Path,
    contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    active_contract = dict(contract or load_contract())
    return score_evidence(
        _load_json(path), active_contract, source_sha256=_sha256(path)
    )


def _project_path(relative: str) -> Path:
    path = (PROJECT_ROOT / relative).resolve()
    if PROJECT_ROOT != path and PROJECT_ROOT not in path.parents:
        raise BalancedCandidateError(f"calibration path escapes project: {relative}")
    return path


def run_calibration(
    manifest_path: Path = DEFAULT_CALIBRATION_MANIFEST,
    contract_path: Path = DEFAULT_CONTRACT,
) -> dict[str, Any]:
    contract = load_contract(contract_path)
    manifest = _load_json(manifest_path)
    if (
        manifest.get("schema_version") != CALIBRATION_SCHEMA
        or manifest.get("candidate_id") != CANDIDATE_ID
        or manifest.get("no_model_reruns") is not True
        or not isinstance(manifest.get("fixtures"), list)
    ):
        raise BalancedCandidateError("balanced calibration manifest changed")
    results: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for fixture in manifest["fixtures"]:
        if not isinstance(fixture, Mapping):
            raise BalancedCandidateError("calibration fixture is invalid")
        path = _project_path(str(fixture.get("path") or ""))
        expected_sha = str(fixture.get("sha256") or "")
        actual_sha = _sha256(path)
        if actual_sha != expected_sha:
            raise BalancedCandidateError(
                f"calibration evidence changed: {fixture.get('id')}"
            )
        score = score_evidence_file(path, contract)
        if score["raw_score"] != fixture.get("expected_raw"):
            raise BalancedCandidateError(
                f"calibration raw score changed: {fixture.get('id')}"
            )
        if score["display_score"] != fixture.get("expected_display"):
            raise BalancedCandidateError(
                f"calibration display score changed: {fixture.get('id')}"
            )
        result = {
            "id": fixture.get("id"),
            "model_group": fixture.get("model_group"),
            "classification": fixture.get("classification"),
            "path": str(path.relative_to(PROJECT_ROOT)),
            "source_score_sha256": actual_sha,
            "raw_score": score["raw_score"],
            "display_score": score["display_score"],
            "layers": score["layers"],
        }
        results.append(result)
        grouped.setdefault(str(result["model_group"]), []).append(result)
    medians: dict[str, dict[str, float]] = {}
    for group in ("sol", "luna"):
        rows = grouped.get(group, [])
        medians[group] = {
            "raw": _round_score(
                statistics.median(row["raw_score"] for row in rows)
            ),
            "display": _round_score(
                statistics.median(row["display_score"] for row in rows)
            ),
        }
    if medians != manifest.get("expected_group_medians"):
        raise BalancedCandidateError("calibration medians changed")
    return {
        "schema_version": REPORT_SCHEMA,
        "candidate_id": CANDIDATE_ID,
        "status": "passed",
        "no_model_reruns": True,
        "contract_sha256": _sha256(contract_path),
        "manifest_sha256": _sha256(manifest_path),
        "fixture_count": len(results),
        "results": results,
        "group_medians": medians,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply the frozen Frontend V17 balanced score adapter"
    )
    parser.add_argument("--score", type=Path, default=None)
    parser.add_argument("--calibrate", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    if bool(args.score) == bool(args.calibrate):
        raise BalancedCandidateError("choose exactly one of --score or --calibrate")
    result = (
        score_evidence_file(args.score.expanduser().resolve())
        if args.score
        else run_calibration()
    )
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BalancedCandidateError, OSError, ValueError) as error:
        print(json.dumps({"event": "error", "message": str(error)}))
        raise SystemExit(1)
