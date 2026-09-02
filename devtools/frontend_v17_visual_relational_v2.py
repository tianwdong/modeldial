from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
QUESTION_ROOT = (
    PROJECT_ROOT / "questions" / "frontend" / "frontend_v17_visual_relational_v2"
)
FIXTURES_PATH = QUESTION_ROOT / "calibration-fixtures.json"
CONTRACT_PATH = QUESTION_ROOT / "score-contract.json"
SOURCE_REFERENCE_ROOT = (
    PROJECT_ROOT / "questions" / "frontend" / "case_stream_explorer_v17_v2"
)
CANDIDATE_ID = "case_stream_explorer_v17_visual_relational_v2"
CONTRACT_SCHEMA = "frontend_v17_visual_relational_v2_contract_v1"
FIXTURES_SCHEMA = "frontend_v17_visual_relational_v2_fixtures_v1"
RESULT_SCHEMA = "frontend_v17_visual_relational_v2_score_v1"
REPORT_SCHEMA = "frontend_v17_visual_relational_v2_calibration_report_v1"


from devtools import frontend_v17_balanced_candidate as balanced  # noqa: E402
from scanner.frontend_deterministic_evaluation import (  # noqa: E402
    default_deterministic_frontend_question_root,
    load_deterministic_frontend_question,
    score_deterministic_frontend_html,
)
from scanner.frontend_image_metrics import (  # noqa: E402
    RGBImage,
    read_rgb_png,
    visual_similarity,
)


STATE_FILES = {
    "default_desktop": "default_desktop.png",
    "default_tablet": "default_tablet.png",
    "default_mobile": "default_mobile.png",
    "selected_saving": "selected_saving.png",
    "failure": "failure.png",
    "desktop_inspector": "desktop_inspector.png",
    "mobile_inspector": "mobile_inspector.png",
}
STATE_CROPS = {
    "state_saving": (30, 493, 1039, 120),
    "state_failure": (1080, 493, 330, 405),
    "state_desktop_inspector": (1080, 493, 330, 405),
    "state_mobile_inspector": (0, 0, 390, 844),
}
ATOM_POINTS = {
    "state_saving": 4,
    "state_failure": 4,
    "state_desktop_inspector": 3,
    "state_mobile_inspector": 4,
    "responsive_desktop": 4,
    "responsive_tablet": 3,
    "responsive_mobile": 3,
    "responsive_reflow": 2,
    "hierarchy_contrast": 4,
    "hierarchy_occupancy": 3,
    "hierarchy_scale": 3,
    "structure_default_desktop": 2,
    "structure_default_tablet": 1,
    "structure_default_mobile": 1,
    "structure_selected_saving": 1,
    "structure_failure": 1,
    "structure_desktop_inspector": 1,
    "structure_mobile_inspector": 1,
}
STATE_BASELINES = {
    "state_saving": ("default_desktop", "selected_saving"),
    "state_failure": ("default_desktop", "failure"),
    "state_desktop_inspector": ("default_desktop", "desktop_inspector"),
    "state_mobile_inspector": ("default_mobile", "mobile_inspector"),
}


class VisualV2Error(RuntimeError):
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
        raise VisualV2Error(f"cannot read JSON: {path}") from error
    if not isinstance(value, dict):
        raise VisualV2Error(f"JSON object required: {path}")
    return value


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _project_path(value: str) -> Path:
    path = (PROJECT_ROOT / value).resolve()
    if path != PROJECT_ROOT and PROJECT_ROOT not in path.parents:
        raise VisualV2Error(f"path escapes project: {value}")
    return path


def _blocks(
    image: RGBImage,
    *,
    block_size: int,
    crop: Sequence[int] | None = None,
) -> tuple[list[tuple[float, float, float]], list[float], int, int]:
    x, y, width, height = (
        tuple(int(value) for value in crop)
        if crop is not None
        else (0, 0, image.width, image.height)
    )
    if x < 0 or y < 0 or x + width > image.width or y + height > image.height:
        raise VisualV2Error("visual crop escapes screenshot")
    colors: list[tuple[float, float, float]] = []
    luminance: list[float] = []
    block_width = math.ceil(width / block_size)
    block_height = math.ceil(height / block_size)
    for top in range(y, y + height, block_size):
        bottom = min(y + height, top + block_size)
        for left in range(x, x + width, block_size):
            right = min(x + width, left + block_size)
            red = green = blue = count = 0
            for row in range(top, bottom):
                index = (row * image.width + left) * 3
                for _column in range(left, right):
                    red += image.pixels[index]
                    green += image.pixels[index + 1]
                    blue += image.pixels[index + 2]
                    count += 1
                    index += 3
            color = (red / count, green / count, blue / count)
            colors.append(color)
            luminance.append(
                0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]
            )
    return colors, luminance, block_width, block_height


def _edge_mask(
    luminance: Sequence[float],
    width: int,
    height: int,
    threshold: float,
) -> set[tuple[int, int]]:
    edges: set[tuple[int, int]] = set()
    for row in range(height):
        for column in range(width):
            index = row * width + column
            horizontal = (
                abs(luminance[index] - luminance[index + 1])
                if column + 1 < width
                else 0.0
            )
            vertical = (
                abs(luminance[index] - luminance[index + width])
                if row + 1 < height
                else 0.0
            )
            if max(horizontal, vertical) >= threshold:
                edges.add((column, row))
    return edges


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _state_change(left: RGBImage, right: RGBImage, crop: Sequence[int]) -> float:
    if (left.width, left.height) != (right.width, right.height):
        raise VisualV2Error("state screenshots have different dimensions")
    left_colors, left_luma, width, height = _blocks(
        left, block_size=8, crop=crop
    )
    right_colors, right_luma, other_width, other_height = _blocks(
        right, block_size=8, crop=crop
    )
    if (width, height) != (other_width, other_height):
        raise VisualV2Error("state block grids changed")
    changed = sum(
        sum(abs(a - b) for a, b in zip(left_color, right_color)) / 3 >= 12
        for left_color, right_color in zip(left_colors, right_colors)
    ) / len(left_colors)
    left_edges = _edge_mask(left_luma, width, height, 8)
    right_edges = _edge_mask(right_luma, width, height, 8)
    union = left_edges | right_edges
    edge_change = len(left_edges ^ right_edges) / len(union) if union else 0.0
    return _round(0.7 * changed + 0.3 * edge_change)


def _descriptor(image: RGBImage) -> dict[str, Any]:
    colors, luminance, width, height = _blocks(image, block_size=16)
    corners = [colors[0], colors[width - 1], colors[-width], colors[-1]]
    background = tuple(statistics.median(values) for values in zip(*corners))
    foreground = {
        (column, row)
        for row in range(height)
        for column in range(width)
        if sum(
            abs(channel - background[index])
            for index, channel in enumerate(colors[row * width + column])
        )
        / 3
        >= 8
    }
    return {
        "luma_range": (_percentile(luminance, 0.98) - _percentile(luminance, 0.10)) / 255,
        "foreground_coverage": len(foreground) / (width * height),
    }


def _interior_vertical_seam(image: RGBImage) -> float:
    """Measure a sustained interior split without assuming a reference layout."""
    crop_y = int(image.height * 0.46)
    crop_height = max(1, int(image.height * 0.44))
    _colors, luminance, width, height = _blocks(
        image,
        block_size=4,
        crop=(0, crop_y, image.width, crop_height),
    )
    left = int(width * 0.42)
    right = min(width - 1, int(width * 0.90))
    continuity = []
    for column in range(left, right):
        continuity.append(
            sum(
                abs(
                    luminance[row * width + column]
                    - luminance[row * width + column + 1]
                )
                >= 3
                for row in range(height)
            )
            / height
        )
    return _round(max(continuity, default=0.0))


def _right_containment_seam(image: RGBImage) -> float:
    """Measure whether stacked mobile surfaces visibly close inside the viewport."""
    crop_y = int(image.height * 0.08)
    crop_height = max(1, int(image.height * 0.90))
    _colors, luminance, width, height = _blocks(
        image,
        block_size=2,
        crop=(0, crop_y, image.width, crop_height),
    )
    left = int(width * 0.86)
    right = min(width - 1, int(width * 0.99))
    continuity = []
    for column in range(left, right):
        continuity.append(
            sum(
                abs(
                    luminance[row * width + column]
                    - luminance[row * width + column + 1]
                )
                >= 3
                for row in range(height)
            )
            / height
        )
    return _round(max(continuity, default=0.0))


def _top_level_text_scale(image: RGBImage) -> float:
    """Measure a visible top-level type scale without OCR or a reference image."""
    active_rows: list[bool] = []
    right = int(image.width * 0.58)
    for row in range(10, min(80, image.height)):
        bright = 0
        index = row * image.width * 3
        for _column in range(right):
            red = image.pixels[index]
            green = image.pixels[index + 1]
            blue = image.pixels[index + 2]
            index += 3
            if 0.2126 * red + 0.7152 * green + 0.0722 * blue >= 150:
                bright += 1
        active_rows.append(bright >= 4)
    bridged = active_rows[:]
    for index in range(1, len(active_rows) - 1):
        if not active_rows[index] and active_rows[index - 1] and active_rows[index + 1]:
            bridged[index] = True
    for index in range(1, len(active_rows) - 2):
        if (
            not active_rows[index]
            and not active_rows[index + 1]
            and active_rows[index - 1]
            and active_rows[index + 2]
        ):
            bridged[index] = True
            bridged[index + 1] = True
    longest = current = 0
    for active in bridged:
        current = current + 1 if active else 0
        longest = max(longest, current)
    return _round(longest / 32)


def _load_pack(path: Path) -> dict[str, RGBImage]:
    result: dict[str, RGBImage] = {}
    for state, filename in STATE_FILES.items():
        screenshot = path / filename
        if not screenshot.is_file():
            raise VisualV2Error(f"screenshot is unavailable: {screenshot}")
        result[state] = read_rgb_png(screenshot)
    return result


def _structure_similarity(left: RGBImage, right: RGBImage) -> float:
    values = visual_similarity(
        left,
        right,
        crop=(0, 0, left.width, left.height),
        visual_rules={
            "block_size": 8,
            "edge_threshold": 10,
            "edge_tolerance_blocks": 1,
            "weights": {"ssim": 0.35, "color": 0.0, "edge": 0.65},
        },
    )
    return _round(0.35 * values["ssim"] + 0.65 * values["edge_f1"])


def raw_metrics(
    pack: Mapping[str, RGBImage],
    positive_packs: Sequence[Mapping[str, RGBImage]],
) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for atom, (left_state, right_state) in STATE_BASELINES.items():
        metrics[atom] = _state_change(
            pack[left_state], pack[right_state], STATE_CROPS[atom]
        )
    descriptors = {
        state: _descriptor(pack[state])
        for state in ("default_desktop", "default_tablet", "default_mobile")
    }
    desktop = descriptors["default_desktop"]
    tablet = descriptors["default_tablet"]
    mobile = descriptors["default_mobile"]
    metrics["responsive_desktop"] = _interior_vertical_seam(
        pack["default_desktop"]
    )
    metrics["responsive_tablet"] = _interior_vertical_seam(
        pack["default_tablet"]
    )
    metrics["responsive_mobile"] = _right_containment_seam(
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
        statistics.mean(
            descriptors[state]["luma_range"] for state in descriptors
        )
    )
    metrics["hierarchy_occupancy"] = _round(
        statistics.mean(
            descriptors[state]["foreground_coverage"] for state in descriptors
        )
    )
    metrics["hierarchy_scale"] = _round(
        statistics.mean(
            _top_level_text_scale(pack[state]) for state in descriptors
        )
    )
    for state in STATE_FILES:
        metrics[f"structure_{state}"] = max(
            _structure_similarity(pack[state], positive[state])
            for positive in positive_packs
        )
    if set(metrics) != set(ATOM_POINTS):
        raise VisualV2Error("visual metric identity changed")
    return metrics


def _normalize(metric: float, anchor: Mapping[str, Any]) -> float:
    positive = float(anchor["positive_floor"])
    mutant = float(anchor["mutant_ceiling"])
    if positive <= mutant:
        raise VisualV2Error("visual anchor margin is not positive")
    return min(1.0, max(0.0, (metric - mutant) / (positive - mutant)))


def score_visual(
    metrics: Mapping[str, float],
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    anchors = contract.get("anchors")
    atoms = contract.get("visual_atoms")
    if not isinstance(anchors, Mapping) or not isinstance(atoms, Mapping):
        raise VisualV2Error("visual contract anchors are unavailable")
    details: dict[str, Any] = {}
    total = 0.0
    dimensions = {
        "state_distinction": 0.0,
        "responsive_composition": 0.0,
        "readability_hierarchy": 0.0,
        "multi_reference_structure": 0.0,
    }
    for atom, points in ATOM_POINTS.items():
        if atoms.get(atom) != points or atom not in metrics or atom not in anchors:
            raise VisualV2Error(f"visual atom changed: {atom}")
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
            dimension = "multi_reference_structure"
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
        "dimensions": {
            key: _round(value) for key, value in dimensions.items()
        },
        "atoms": details,
    }


def _display_score(raw_score: float, contract: Mapping[str, Any]) -> float:
    return balanced.display_score(raw_score, contract)


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
    ):
        raise VisualV2Error("visual v2 contract identity changed")
    return contract


def score_saved_evidence(
    *,
    source_score: Path,
    screenshot_root: Path,
    calibration_root: Path,
    contract_path: Path = CONTRACT_PATH,
) -> dict[str, Any]:
    contract = load_contract(contract_path)
    positive_ids = contract["positive_pack_ids"]
    positive_packs = [
        _load_pack(calibration_root / "packs" / str(identifier))
        for identifier in positive_ids
    ]
    metrics = raw_metrics(_load_pack(screenshot_root), positive_packs)
    visual = score_visual(metrics, contract)
    base = balanced.score_evidence_file(source_score)
    behavior = base["layers"]["behavior"]
    workflow = base["layers"]["workflow"]
    raw = _round(float(behavior["points"]) + float(workflow["points"]) + visual["points"])
    display = _round(_display_score(raw, contract))
    return {
        "schema_version": RESULT_SCHEMA,
        "candidate_id": CANDIDATE_ID,
        "benchmark_ref": contract["benchmark_ref"],
        "source_benchmark_ref": contract["source_benchmark_ref"],
        "score_state": "complete",
        "raw_score": raw,
        "display_score": display,
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
        or len(fixtures["positives"]) != 3
        or not isinstance(fixtures.get("mutants"), list)
    ):
        raise VisualV2Error("visual v2 fixtures changed")
    targets = [
        target
        for mutant in fixtures["mutants"]
        for target in mutant.get("target_atoms", [])
    ]
    if set(targets) != set(ATOM_POINTS) or len(targets) != len(ATOM_POINTS):
        raise VisualV2Error("visual v2 mutant coverage changed")
    return fixtures


def _materialize(source: str, style: str, path: Path) -> Path:
    if not style or "</head>" not in source:
        raise VisualV2Error("visual fixture source is invalid")
    value = source.replace("</head>", f"<style>{style}</style></head>", 1)
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
        raise VisualV2Error("visual v2 calibration output must be new and empty")
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
            str(fixture["style"]),
            materialized / f"{identifier}.html",
        )
    candidates["starter"] = SOURCE_REFERENCE_ROOT / "starter.html"
    if not reuse_existing_packs:
        for identifier, candidate in candidates.items():
            _capture(candidate, packs_root / identifier)
    positive_ids = ["reference", *[str(item["id"]) for item in fixtures["positives"]]]
    positive_packs = [_load_pack(packs_root / identifier) for identifier in positive_ids]
    raw_by_pack = {
        identifier: raw_metrics(_load_pack(packs_root / identifier), positive_packs)
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
        positive_floor = min(raw_by_pack[identifier][atom] for identifier in positive_ids)
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
        raise VisualV2Error(
            "visual calibration margins are too small: " + "; ".join(violations)
        )
    balanced_contract = balanced.load_contract()
    contract: dict[str, Any] = {
        "schema_version": CONTRACT_SCHEMA,
        "candidate_id": CANDIDATE_ID,
        "benchmark_ref": "frontend-case-stream-explorer-v17-visual-relational@v2",
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
            },
            "responsive": {
                "interior_seam": {
                    "block_size": 4,
                    "crop_y_fraction": 0.46,
                    "crop_height_fraction": 0.44,
                    "x_fraction": [0.42, 0.90],
                    "luma_threshold": 3,
                },
                "mobile_containment": {
                    "block_size": 2,
                    "crop_y_fraction": 0.08,
                    "crop_height_fraction": 0.90,
                    "x_fraction": [0.86, 0.99],
                    "luma_threshold": 3,
                },
                "reflow_formula": "geometric_mean(desktop,tablet,mobile)",
            },
            "hierarchy": {
                "block_size": 16,
                "background_delta": 8,
                "luma_percentiles": [0.10, 0.98],
                "heading_y_pixels": [10, 80],
                "heading_x_fraction": 0.58,
                "heading_luma_threshold": 150,
                "heading_gap_bridge_pixels": 2,
                "heading_scale_divisor": 32,
            },
            "structure": {
                "block_size": 8,
                "edge_threshold": 10,
                "edge_tolerance_blocks": 1,
                "weights": {"ssim": 0.35, "color": 0.0, "edge": 0.65},
            },
            "structure_formula": "0.35*grayscale_ssim + 0.65*edge_f1",
            "state_curve": "linear_positive_floor_mutant_ceiling",
            "llm_judge": False,
        },
        "anchors": anchors,
        "display_mapping": {},
        "ranking_policy": "Every behavior, workflow, and visual atom retains independently earned points. No all-or-nothing gate is applied.",
    }
    positive_scores = {
        identifier: score_visual(raw_by_pack[identifier], contract)
        for identifier in positive_ids
    }
    for identifier, value in positive_scores.items():
        if value["points"] != 45:
            raise VisualV2Error(f"visual positive does not score 45: {identifier}")
    reference_base = balanced.score_evidence_file(
        packs_root / "reference" / "automatic-score.json"
    )
    reference_raw = _round(
        reference_base["layers"]["behavior"]["points"]
        + reference_base["layers"]["workflow"]["points"]
        + positive_scores["reference"]["points"]
    )
    if reference_raw != 100:
        raise VisualV2Error(f"visual reference raw score changed: {reference_raw}")
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
        raise VisualV2Error("visual v2 directed mutant survived")
    report = {
        "schema_version": REPORT_SCHEMA,
        "candidate_id": CANDIDATE_ID,
        "status": "passed",
        "model_outputs_used_for_anchors": False,
        "positive_pack_ids": positive_ids,
        "positive_visual_scores": {
            identifier: value["points"] for identifier, value in positive_scores.items()
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
    fixtures = _fixtures()
    identifiers = [
        "reference",
        "starter",
        *[str(item["id"]) for item in fixtures["positives"]],
        *[str(item["id"]) for item in fixtures["mutants"]],
    ]
    metrics = {
        identifier: raw_metrics(
            _load_pack(calibration_root / "packs" / identifier), positive_packs
        )
        for identifier in identifiers
    }
    for atom, anchor in contract["anchors"].items():
        positives = [metrics[identifier][atom] for identifier in contract["positive_pack_ids"]]
        if _round(min(positives)) != anchor["positive_floor"]:
            raise VisualV2Error(f"positive anchor drifted: {atom}")
        if _round(metrics[anchor["directed_mutant"]][atom]) != anchor["mutant_ceiling"]:
            raise VisualV2Error(f"mutant anchor drifted: {atom}")
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
        description="Frontend V17 screenshot-relational visual v2"
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
