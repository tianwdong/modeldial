from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from devtools import frontend_v17_balanced_candidate as balanced  # noqa: E402
from devtools import frontend_v17_visual_relational_v2 as visual_v2  # noqa: E402
from scanner import frontend_image_metrics as image_metrics  # noqa: E402
from scanner.frontend_deterministic_evaluation import (  # noqa: E402
    default_deterministic_frontend_question_root,
    load_deterministic_frontend_question,
    score_deterministic_frontend_html,
)
from scanner.frontend_image_metrics import RGBImage  # noqa: E402


QUESTION_ROOT = (
    PROJECT_ROOT / "questions" / "frontend" / "frontend_v17_visual_semantic_v3"
)
FIXTURES_PATH = QUESTION_ROOT / "calibration-fixtures.json"
CONTRACT_PATH = QUESTION_ROOT / "score-contract.json"
SOURCE_REFERENCE_ROOT = (
    PROJECT_ROOT / "questions" / "frontend" / "case_stream_explorer_v17_v2"
)
CANDIDATE_ID = "case_stream_explorer_v17_visual_semantic_v3"
CONTRACT_SCHEMA = "frontend_v17_visual_semantic_v3_contract_v1"
FIXTURES_SCHEMA = "frontend_v17_visual_semantic_v3_fixtures_v1"
RESULT_SCHEMA = "frontend_v17_visual_semantic_v3_score_v1"
REPORT_SCHEMA = "frontend_v17_visual_semantic_v3_calibration_report_v1"

STATE_FILES = dict(visual_v2.STATE_FILES)
STATE_CROPS = dict(visual_v2.STATE_CROPS)
ATOM_POINTS = dict(visual_v2.ATOM_POINTS)
STATE_BASELINES = dict(visual_v2.STATE_BASELINES)


class VisualSemanticV3Error(RuntimeError):
    pass


def _round(value: float) -> float:
    return round(float(value), 6)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VisualSemanticV3Error(f"cannot read JSON: {path}") from error
    if not isinstance(value, dict):
        raise VisualSemanticV3Error(f"JSON object required: {path}")
    return value


def _sha256(path: Path) -> str:
    return visual_v2._sha256(path)


def _project_path(value: str) -> Path:
    path = (PROJECT_ROOT / value).resolve()
    if path != PROJECT_ROOT and PROJECT_ROOT not in path.parents:
        raise VisualSemanticV3Error(f"path escapes project: {value}")
    return path


def _load_pack(path: Path) -> dict[str, RGBImage]:
    return visual_v2._load_pack(path)


def _saving_change(left: RGBImage, right: RGBImage, crop: Sequence[int]) -> float:
    if (left.width, left.height) != (right.width, right.height):
        raise VisualSemanticV3Error("state screenshots have different dimensions")
    left_colors, left_luma, width, height = visual_v2._blocks(
        left, block_size=8, crop=crop
    )
    right_colors, right_luma, other_width, other_height = visual_v2._blocks(
        right, block_size=8, crop=crop
    )
    if (width, height) != (other_width, other_height):
        raise VisualSemanticV3Error("state block grids changed")
    changed = [
        sum(abs(a - b) for a, b in zip(left_color, right_color)) / 3 >= 12
        for left_color, right_color in zip(left_colors, right_colors)
    ]
    left_edges = visual_v2._edge_mask(left_luma, width, height, 8)
    right_edges = visual_v2._edge_mask(right_luma, width, height, 8)
    union = left_edges | right_edges
    global_change = 0.7 * (sum(changed) / len(changed)) + 0.3 * (
        len(left_edges ^ right_edges) / len(union) if union else 0.0
    )
    local_changes: list[float] = []
    for top in range(0, height, 5):
        for left_column in range(0, width, 10):
            coordinates = {
                (column, row)
                for row in range(top, min(top + 5, height))
                for column in range(left_column, min(left_column + 10, width))
            }
            indexes = [row * width + column for column, row in coordinates]
            rgb_change = sum(changed[index] for index in indexes) / len(indexes)
            local_left_edges = left_edges & coordinates
            local_right_edges = right_edges & coordinates
            local_union = local_left_edges | local_right_edges
            edge_change = (
                len(local_left_edges ^ local_right_edges) / len(local_union)
                if local_union
                else 0.0
            )
            local_changes.append(0.7 * rgb_change + 0.3 * edge_change)
    concentrated_change = visual_v2._percentile(local_changes, 0.90)
    return _round(0.75 * global_change + 0.25 * concentrated_change)


CoarseFeatures = tuple[list[float], int, int, set[tuple[int, int]]]


def _coarse_features(image: RGBImage) -> CoarseFeatures:
    _colors, luminance, width, height = visual_v2._blocks(
        image, block_size=32
    )
    return (
        luminance,
        width,
        height,
        visual_v2._edge_mask(luminance, width, height, 3),
    )


def _coarse_structure_similarity(
    left: CoarseFeatures,
    right: CoarseFeatures,
) -> float:
    left_luma, left_width, left_height, left_edges = left
    right_luma, right_width, right_height, right_edges = right
    if (left_width, left_height) != (right_width, right_height):
        raise VisualSemanticV3Error("coarse structure grids changed")
    ssim = image_metrics._ssim(left_luma, right_luma)
    edge = image_metrics._edge_f1(left_edges, right_edges, 1)
    return _round(0.45 * ssim + 0.55 * edge)


def raw_metrics(
    pack: Mapping[str, RGBImage],
    positive_packs: Sequence[Mapping[str, RGBImage]],
    *,
    positive_structure: Sequence[Mapping[str, CoarseFeatures]] | None = None,
) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for atom, (left_state, right_state) in STATE_BASELINES.items():
        metric = _saving_change if atom == "state_saving" else visual_v2._state_change
        metrics[atom] = metric(pack[left_state], pack[right_state], STATE_CROPS[atom])
    descriptors = {
        state: visual_v2._descriptor(pack[state])
        for state in ("default_desktop", "default_tablet", "default_mobile")
    }
    metrics["responsive_desktop"] = visual_v2._interior_vertical_seam(
        pack["default_desktop"]
    )
    metrics["responsive_tablet"] = visual_v2._interior_vertical_seam(
        pack["default_tablet"]
    )
    metrics["responsive_mobile"] = visual_v2._right_containment_seam(
        pack["default_mobile"]
    )
    metrics["responsive_reflow"] = _round(
        (
            metrics["responsive_desktop"]
            * metrics["responsive_tablet"]
            * metrics["responsive_mobile"]
        )
        ** (1 / 3)
    )
    metrics["hierarchy_contrast"] = _round(
        statistics.mean(item["luma_range"] for item in descriptors.values())
    )
    metrics["hierarchy_occupancy"] = _round(
        statistics.mean(
            item["foreground_coverage"] for item in descriptors.values()
        )
    )
    metrics["hierarchy_scale"] = _round(
        statistics.mean(
            visual_v2._top_level_text_scale(pack[state])
            for state in descriptors
        )
    )
    candidate_structure = {
        state: _coarse_features(pack[state]) for state in STATE_FILES
    }
    if positive_structure is None:
        positive_structure = [
            {state: _coarse_features(positive[state]) for state in STATE_FILES}
            for positive in positive_packs
        ]
    for state in STATE_FILES:
        metrics[f"structure_{state}"] = max(
            _coarse_structure_similarity(candidate_structure[state], positive[state])
            for positive in positive_structure
        )
    if set(metrics) != set(ATOM_POINTS):
        raise VisualSemanticV3Error("visual metric identity changed")
    return metrics


def _normalize(metric: float, anchor: Mapping[str, Any]) -> float:
    positive = float(anchor["positive_floor"])
    mutant = float(anchor["mutant_ceiling"])
    if positive <= mutant:
        raise VisualSemanticV3Error("visual anchor margin is not positive")
    return min(1.0, max(0.0, (metric - mutant) / (positive - mutant)))


def score_visual(
    metrics: Mapping[str, float],
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    anchors = contract.get("anchors")
    atoms = contract.get("visual_atoms")
    if not isinstance(anchors, Mapping) or not isinstance(atoms, Mapping):
        raise VisualSemanticV3Error("visual contract anchors are unavailable")
    details: dict[str, Any] = {}
    total = 0.0
    dimensions = {
        "state_distinction": 0.0,
        "responsive_composition": 0.0,
        "readability_hierarchy": 0.0,
        "coarse_structure": 0.0,
    }
    for atom, points in ATOM_POINTS.items():
        if atoms.get(atom) != points or atom not in metrics or atom not in anchors:
            raise VisualSemanticV3Error(f"visual atom changed: {atom}")
        normalized = _normalize(float(metrics[atom]), anchors[atom])
        earned = points * normalized
        total += earned
        if atom.startswith("state_"):
            dimension = "state_distinction"
        elif atom.startswith("responsive_"):
            dimension = "responsive_composition"
        elif atom.startswith("hierarchy_"):
            dimension = "readability_hierarchy"
        else:
            dimension = "coarse_structure"
        dimensions[dimension] += earned
        details[atom] = {
            "points": _round(earned),
            "max_points": points,
            "metric": _round(float(metrics[atom])),
            "normalized": _round(normalized),
            "positive_floor": anchors[atom]["positive_floor"],
            "mutant_ceiling": anchors[atom]["mutant_ceiling"],
        }
    return {
        "points": _round(total),
        "max_points": 45,
        "dimensions": {key: _round(value) for key, value in dimensions.items()},
        "atoms": details,
    }


def _display_score(raw_score: float, contract: Mapping[str, Any]) -> float:
    return balanced.display_score(raw_score, contract)


def _official_score(display_score: float) -> int:
    if display_score < 0:
        raise VisualSemanticV3Error("display score cannot be negative")
    return int(math.floor(display_score + 0.5))


def load_contract(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    contract = _load_json(path)
    if (
        contract.get("schema_version") != CONTRACT_SCHEMA
        or contract.get("candidate_id") != CANDIDATE_ID
        or contract.get("source_benchmark_ref")
        != "frontend-case-stream-explorer-v17@v2"
        or contract.get("total_points") != 100
        or contract.get("visual_points") != 45
        or contract.get("visual_atoms") != ATOM_POINTS
        or contract.get("official_score_rounding") != "decimal_half_up_total_only"
    ):
        raise VisualSemanticV3Error("visual semantic v3 contract identity changed")
    return contract


def score_saved_evidence(
    *,
    source_score: Path,
    screenshot_root: Path,
    calibration_root: Path,
    contract_path: Path = CONTRACT_PATH,
) -> dict[str, Any]:
    contract = load_contract(contract_path)
    positive_packs = [
        _load_pack(calibration_root / "packs" / str(identifier))
        for identifier in contract["positive_pack_ids"]
    ]
    positive_structure = [
        {state: _coarse_features(positive[state]) for state in STATE_FILES}
        for positive in positive_packs
    ]
    metrics = raw_metrics(
        _load_pack(screenshot_root),
        positive_packs,
        positive_structure=positive_structure,
    )
    visual = score_visual(metrics, contract)
    base = balanced.score_evidence_file(source_score)
    behavior = base["layers"]["behavior"]
    workflow = base["layers"]["workflow"]
    raw = _round(
        float(behavior["points"])
        + float(workflow["points"])
        + visual["points"]
    )
    display = _round(_display_score(raw, contract))
    return {
        "schema_version": RESULT_SCHEMA,
        "candidate_id": CANDIDATE_ID,
        "benchmark_ref": contract["benchmark_ref"],
        "source_benchmark_ref": contract["source_benchmark_ref"],
        "score_state": "complete",
        "raw_score": raw,
        "display_score": display,
        "official_score": _official_score(display),
        "layers": {
            "behavior": behavior,
            "workflow": workflow,
            "visual": visual,
        },
        "source_score_sha256": _sha256(source_score),
        "screenshot_sha256": {
            state: _sha256(screenshot_root / filename)
            for state, filename in STATE_FILES.items()
        },
        "contract_sha256": _sha256(contract_path),
        "all_or_nothing_gate": False,
    }


def _fixtures() -> dict[str, Any]:
    fixtures = _load_json(FIXTURES_PATH)
    if (
        fixtures.get("schema_version") != FIXTURES_SCHEMA
        or not isinstance(fixtures.get("positives"), list)
        or len(fixtures["positives"]) != 4
        or not isinstance(fixtures.get("mutants"), list)
    ):
        raise VisualSemanticV3Error("visual semantic v3 fixtures changed")
    targets = [
        target
        for mutant in fixtures["mutants"]
        for target in mutant.get("target_atoms", [])
    ]
    if set(targets) != set(ATOM_POINTS) or len(targets) != len(ATOM_POINTS):
        raise VisualSemanticV3Error("visual semantic v3 mutant coverage changed")
    return fixtures


def _materialize(source: str, fixture: Mapping[str, Any], path: Path) -> Path:
    style = str(fixture.get("style", ""))
    script = str(fixture.get("script", ""))
    if not style or "</head>" not in source or "</body>" not in source:
        raise VisualSemanticV3Error("visual fixture source is invalid")
    value = source.replace("</head>", f"<style>{style}</style></head>", 1)
    if script:
        value = value.replace("</body>", f"<script>{script}</script></body>", 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return path


def _capture(candidate: Path, destination: Path) -> None:
    package = load_deterministic_frontend_question(
        default_deterministic_frontend_question_root(PROJECT_ROOT)
    )
    score_deterministic_frontend_html(
        candidate,
        destination,
        package,
        browser="chrome",
        timeout_seconds=180,
    )


def derive_calibration(
    output_root: Path,
    *,
    reuse_existing_packs: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if (
        not reuse_existing_packs
        and output_root.exists()
        and any(output_root.iterdir())
    ):
        raise VisualSemanticV3Error(
            "visual semantic v3 calibration output must be new and empty"
        )
    output_root.mkdir(parents=True, exist_ok=True)
    fixtures = _fixtures()
    reference_html = _project_path(str(fixtures["source_reference_html"]))
    source = reference_html.read_text(encoding="utf-8")
    materialized = output_root / "materialized"
    packs_root = output_root / "packs"
    candidates: dict[str, Path] = {"reference": reference_html}
    for fixture in [*fixtures["positives"], *fixtures["mutants"]]:
        identifier = str(fixture["id"])
        candidates[identifier] = _materialize(
            source,
            fixture,
            materialized / f"{identifier}.html",
        )
    candidates["starter"] = SOURCE_REFERENCE_ROOT / "starter.html"
    if not reuse_existing_packs:
        for identifier, candidate in candidates.items():
            _capture(candidate, packs_root / identifier)
    positive_ids = [
        "reference", *[str(item["id"]) for item in fixtures["positives"]]
    ]
    positive_packs = [
        _load_pack(packs_root / identifier) for identifier in positive_ids
    ]
    positive_structure = [
        {state: _coarse_features(positive[state]) for state in STATE_FILES}
        for positive in positive_packs
    ]
    raw_by_pack = {
        identifier: raw_metrics(
            _load_pack(packs_root / identifier),
            positive_packs,
            positive_structure=positive_structure,
        )
        for identifier in candidates
    }
    _write_json(
        output_root / "raw-metrics.json",
        {"schema_version": REPORT_SCHEMA, "raw_metrics": raw_by_pack},
    )
    mutant_by_atom: dict[str, str] = {}
    for mutant in fixtures["mutants"]:
        for atom in mutant["target_atoms"]:
            mutant_by_atom[str(atom)] = str(mutant["id"])
    anchors: dict[str, Any] = {}
    violations: list[str] = []
    for atom in ATOM_POINTS:
        positive_floor = min(
            raw_by_pack[identifier][atom] for identifier in positive_ids
        )
        mutant_id = mutant_by_atom[atom]
        mutant_ceiling = raw_by_pack[mutant_id][atom]
        if positive_floor - mutant_ceiling < 0.005:
            violations.append(
                f"{atom}: positive_floor={positive_floor}, "
                f"mutant_ceiling={mutant_ceiling}"
            )
        anchors[atom] = {
            "positive_floor": _round(positive_floor),
            "mutant_ceiling": _round(mutant_ceiling),
            "directed_mutant": mutant_id,
            "margin": _round(positive_floor - mutant_ceiling),
        }
    if violations:
        raise VisualSemanticV3Error(
            "visual calibration margins are too small: " + "; ".join(violations)
        )
    balanced_contract = balanced.load_contract()
    contract: dict[str, Any] = {
        "schema_version": CONTRACT_SCHEMA,
        "candidate_id": CANDIDATE_ID,
        "benchmark_ref": "frontend-case-stream-explorer-v17-visual-semantic@v3",
        "source_benchmark_ref": "frontend-case-stream-explorer-v17@v2",
        "total_points": 100,
        "behavior_points": 33,
        "workflow_points": 22,
        "visual_points": 45,
        "behavior_dimensions": balanced_contract["layers"]["behavior"]["dimensions"],
        "workflow_dimensions": balanced_contract["layers"]["workflow"]["dimensions"],
        "visual_atoms": ATOM_POINTS,
        "positive_pack_ids": positive_ids,
        "feature_config": {
            "state": {
                "block_size": 8,
                "crops": STATE_CROPS,
                "rgb_change_threshold": 12,
                "edge_threshold": 8,
                "rgb_weight": 0.7,
                "edge_weight": 0.3,
                "credit_saturation": "weakest_correct_positive",
                "saving_concentration": {
                    "tile_blocks": [10, 5],
                    "percentile": 0.90,
                    "global_weight": 0.75,
                    "local_weight": 0.25,
                },
            },
            "responsive": visual_v2.load_contract()["feature_config"]["responsive"],
            "hierarchy": visual_v2.load_contract()["feature_config"]["hierarchy"],
            "structure": {
                "block_size": 32,
                "edge_threshold": 3,
                "edge_tolerance_blocks": 1,
                "weights": {"ssim": 0.45, "color": 0.0, "edge": 0.55},
                "content_policy": "coarse_geometry_only",
            },
            "structure_formula": "0.45*coarse_grayscale_ssim + 0.55*coarse_edge_f1",
            "state_curve": "linear_positive_floor_mutant_ceiling_then_saturate",
            "llm_judge": False,
        },
        "anchors": anchors,
        "display_mapping": {},
        "official_score_rounding": "decimal_half_up_total_only",
        "ranking_precision": "unrounded_display_score",
        "ranking_policy": "Every behavior, workflow, and visual atom retains independently earned points. No all-or-nothing gate is applied.",
    }
    positive_scores = {
        identifier: score_visual(raw_by_pack[identifier], contract)
        for identifier in positive_ids
    }
    for identifier, value in positive_scores.items():
        if value["points"] != 45:
            raise VisualSemanticV3Error(
                f"visual positive does not score 45: {identifier}"
            )
    reference_base = balanced.score_evidence_file(
        packs_root / "reference" / "automatic-score.json"
    )
    reference_raw = _round(
        reference_base["layers"]["behavior"]["points"]
        + reference_base["layers"]["workflow"]["points"]
        + positive_scores["reference"]["points"]
    )
    if reference_raw != 100:
        raise VisualSemanticV3Error(
            f"visual reference raw score changed: {reference_raw}"
        )
    starter_visual = score_visual(raw_by_pack["starter"], contract)
    starter_base = balanced.score_evidence_file(
        packs_root / "starter" / "automatic-score.json"
    )
    starter_raw = _round(
        starter_base["layers"]["behavior"]["points"]
        + starter_base["layers"]["workflow"]["points"]
        + starter_visual["points"]
    )
    contract["display_mapping"] = {
        "starter_raw": starter_raw,
        "starter_display": 20,
        "reference_raw": 100,
        "reference_display": 100,
        "above_starter_exponent": 0.5,
    }
    mutant_results: list[dict[str, Any]] = []
    for mutant in fixtures["mutants"]:
        identifier = str(mutant["id"])
        visual = score_visual(raw_by_pack[identifier], contract)
        target_points = sum(
            visual["atoms"][atom]["points"] for atom in mutant["target_atoms"]
        )
        mutant_results.append(
            {
                "id": identifier,
                "target_atoms": mutant["target_atoms"],
                "target_points": _round(target_points),
                "visual_score": visual["points"],
                "passed": target_points == 0,
            }
        )
    if not all(item["passed"] for item in mutant_results):
        raise VisualSemanticV3Error("visual semantic v3 directed mutant survived")
    report = {
        "schema_version": REPORT_SCHEMA,
        "candidate_id": CANDIDATE_ID,
        "status": "passed",
        "model_outputs_used_for_anchors": False,
        "positive_pack_ids": positive_ids,
        "positive_visual_scores": {
            identifier: value["points"]
            for identifier, value in positive_scores.items()
        },
        "mutant_results": mutant_results,
        "anchors": anchors,
        "starter": {
            "raw_score": starter_raw,
            "visual_score": starter_visual["points"],
        },
        "reference": {"raw_score": reference_raw, "visual_score": 45},
        "raw_metrics": raw_by_pack,
    }
    return contract, report


def verify_calibration(
    calibration_root: Path,
    contract_path: Path = CONTRACT_PATH,
) -> dict[str, Any]:
    contract = load_contract(contract_path)
    positive_packs = [
        _load_pack(calibration_root / "packs" / identifier)
        for identifier in contract["positive_pack_ids"]
    ]
    positive_structure = [
        {state: _coarse_features(positive[state]) for state in STATE_FILES}
        for positive in positive_packs
    ]
    fixtures = _fixtures()
    identifiers = [
        "reference",
        "starter",
        *[str(item["id"]) for item in fixtures["positives"]],
        *[str(item["id"]) for item in fixtures["mutants"]],
    ]
    metrics = {
        identifier: raw_metrics(
            _load_pack(calibration_root / "packs" / identifier),
            positive_packs,
            positive_structure=positive_structure,
        )
        for identifier in identifiers
    }
    for atom, anchor in contract["anchors"].items():
        positives = [
            metrics[identifier][atom]
            for identifier in contract["positive_pack_ids"]
        ]
        if _round(min(positives)) != anchor["positive_floor"]:
            raise VisualSemanticV3Error(f"positive anchor drifted: {atom}")
        if (
            _round(metrics[anchor["directed_mutant"]][atom])
            != anchor["mutant_ceiling"]
        ):
            raise VisualSemanticV3Error(f"mutant anchor drifted: {atom}")
    return {
        "schema_version": REPORT_SCHEMA,
        "candidate_id": CANDIDATE_ID,
        "status": "passed",
        "contract_sha256": _sha256(contract_path),
        "positive_visual_scores": {
            identifier: score_visual(metrics[identifier], contract)["points"]
            for identifier in contract["positive_pack_ids"]
        },
        "starter_visual_score": score_visual(metrics["starter"], contract)["points"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Frontend V17 screenshot-semantic visual v3"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--derive-calibration", action="store_true")
    mode.add_argument("--verify-calibration", action="store_true")
    mode.add_argument("--score", action="store_true")
    parser.add_argument("--calibration-root", type=Path, required=True)
    parser.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    parser.add_argument("--contract-output", type=Path)
    parser.add_argument("--report-output", type=Path)
    parser.add_argument("--source-score", type=Path)
    parser.add_argument("--screenshots", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--reuse-existing-packs", action="store_true")
    args = parser.parse_args()

    if args.derive_calibration:
        if args.contract_output is None or args.report_output is None:
            parser.error("derive calibration requires contract and report outputs")
        contract, report = derive_calibration(
            args.calibration_root.resolve(),
            reuse_existing_packs=args.reuse_existing_packs,
        )
        _write_json(args.contract_output.resolve(), contract)
        _write_json(args.report_output.resolve(), report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.verify_calibration:
        report = verify_calibration(
            args.calibration_root.resolve(), args.contract.resolve()
        )
        if args.report_output is not None:
            _write_json(args.report_output.resolve(), report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.source_score is None or args.screenshots is None or args.output is None:
        parser.error("score requires source-score, screenshots, and output")
    result = score_saved_evidence(
        source_score=args.source_score.resolve(),
        screenshot_root=args.screenshots.resolve(),
        calibration_root=args.calibration_root.resolve(),
        contract_path=args.contract.resolve(),
    )
    _write_json(args.output.resolve(), result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
