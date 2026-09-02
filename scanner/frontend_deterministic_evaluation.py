from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import quote
from uuid import uuid4

from .frontend_evaluation import (
    DEFAULT_BROWSER_TIMEOUT_SECONDS,
    FrontendEvaluationError,
    _build_evidence_manifest,
    _load_json_object,
    _materialize_visual_evidence_script,
    _run_cli,
    _run_code_json,
    _serve_directory,
    _sha256_file,
    _sha256_payload,
    _sha256_text,
    _write_json,
    resolve_playwright_cli,
)
from .frontend_image_metrics import (
    FrontendImageError,
    read_rgb_png,
    visual_similarity,
)


FRONTEND_PACKAGE_ID = "frontend-case-stream-explorer-v17"
FRONTEND_BENCHMARK_REF = "frontend-case-stream-explorer-v17@v2"
FRONTEND_PACKAGE_SCHEMA = "frontend_score_contract_v17_deterministic_v1"
FRONTEND_ASSET_LOCK_SCHEMA = "frontend_v17_deterministic_asset_lock_v1"
FRONTEND_VISUAL_RULES_SCHEMA = "frontend_v17_deterministic_visual_rules_v1"
FRONTEND_REFERENCE_MANIFEST_SCHEMA = (
    "frontend_v17_deterministic_reference_manifest_v1"
)
FRONTEND_RENDER_ENVIRONMENT_SCHEMA = "frontend_v21_render_environment_v1"

_LOCKED_ASSET_PATHS = {
    "prompt": "questions/frontend/case_stream_explorer_v17_v2/prompt.md",
    "starter": "questions/frontend/case_stream_explorer_v17_v2/starter.html",
    "score_contract": (
        "questions/frontend/case_stream_explorer_v17_v2/score-contract.json"
    ),
    "browser_scorer": (
        "questions/frontend/case_stream_explorer_v17_v2/browser_score.js"
    ),
    "trace_scorer": "questions/frontend/case_stream_explorer_v17_v2/trace_score.js",
    "visual_evidence": (
        "questions/frontend/case_stream_explorer_v17_v2/visual_evidence.js"
    ),
    "visual_rules": (
        "questions/frontend/case_stream_explorer_v17_v2/visual-rules.json"
    ),
    "render_environment": (
        "questions/frontend/case_stream_explorer_v17_v2/render-environment.json"
    ),
    "reference_manifest": (
        "questions/frontend/case_stream_explorer_v17_v2/reference/manifest.json"
    ),
    "reference_default_desktop": (
        "questions/frontend/case_stream_explorer_v17_v2/reference/default_desktop.png"
    ),
    "reference_default_tablet": (
        "questions/frontend/case_stream_explorer_v17_v2/reference/default_tablet.png"
    ),
    "reference_default_mobile": (
        "questions/frontend/case_stream_explorer_v17_v2/reference/default_mobile.png"
    ),
    "reference_selected_saving": (
        "questions/frontend/case_stream_explorer_v17_v2/reference/selected_saving.png"
    ),
    "reference_failure": (
        "questions/frontend/case_stream_explorer_v17_v2/reference/failure.png"
    ),
    "reference_desktop_inspector": (
        "questions/frontend/case_stream_explorer_v17_v2/reference/desktop_inspector.png"
    ),
    "reference_mobile_inspector": (
        "questions/frontend/case_stream_explorer_v17_v2/reference/mobile_inspector.png"
    ),
    "deterministic_runtime": "scanner/frontend_deterministic_evaluation.py",
    "image_metrics_runtime": "scanner/frontend_image_metrics.py",
    "legacy_helper_runtime": "scanner/frontend_evaluation.py",
}


@dataclass(frozen=True)
class DeterministicFrontendQuestionPackage:
    root: Path
    prompt_template: str
    starter_html: str
    contract: dict[str, Any]
    asset_lock: dict[str, Any]
    visual_rules: dict[str, Any]
    render_environment: dict[str, Any]
    reference_manifest: dict[str, Any]

    @property
    def prompt(self) -> str:
        marker = "{{STARTER_HTML}}"
        if self.prompt_template.count(marker) != 1:
            raise FrontendEvaluationError(
                "deterministic frontend prompt must contain the starter marker exactly once"
            )
        return self.prompt_template.replace(marker, self.starter_html.rstrip())

    @property
    def browser_score_script(self) -> Path:
        return self.root / "browser_score.js"

    @property
    def trace_score_script(self) -> Path:
        return self.root / "trace_score.js"

    @property
    def visual_evidence_script(self) -> Path:
        return self.root / "visual_evidence.js"

    @property
    def reference_root(self) -> Path:
        return self.root / "reference"


def default_deterministic_frontend_question_root(backend_root: Path) -> Path:
    return (
        backend_root
        / "questions"
        / "frontend"
        / "case_stream_explorer_v17_v2"
    )


def _contract_checks(contract: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    dimensions = contract.get("dimensions")
    if not isinstance(dimensions, list):
        raise FrontendEvaluationError(
            "deterministic frontend score dimensions are invalid"
        )
    checks: dict[str, dict[str, Any]] = {}
    total = 0
    for dimension in dimensions:
        if not isinstance(dimension, Mapping):
            raise FrontendEvaluationError(
                "deterministic frontend score dimension is invalid"
            )
        dimension_id = str(dimension.get("id") or "")
        raw_checks = dimension.get("checks")
        if not dimension_id or not isinstance(raw_checks, list):
            raise FrontendEvaluationError(
                "deterministic frontend score dimension identity is invalid"
            )
        dimension_total = 0
        for check in raw_checks:
            if not isinstance(check, Mapping):
                raise FrontendEvaluationError(
                    "deterministic frontend score check is invalid"
                )
            check_id = str(check.get("id") or "")
            points = check.get("points")
            mode = check.get("mode")
            if (
                not check_id
                or check_id in checks
                or not isinstance(points, int)
                or isinstance(points, bool)
                or points < 1
                or mode not in {"browser", "workflow", "screenshot"}
            ):
                raise FrontendEvaluationError(
                    f"deterministic frontend score check is invalid: {check_id}"
                )
            checks[check_id] = {**check, "dimension_id": dimension_id}
            dimension_total += points
        if dimension.get("points") != dimension_total:
            raise FrontendEvaluationError(
                f"deterministic frontend dimension total is invalid: {dimension_id}"
            )
        total += dimension_total
    if total != contract.get("total_points"):
        raise FrontendEvaluationError(
            "deterministic frontend score contract does not total 100"
        )
    return checks


def _manifest_state_map(manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    raw = manifest.get("states")
    if not isinstance(raw, list):
        raise FrontendEvaluationError("frontend evidence states are invalid")
    states = {
        str(item.get("id")): item
        for item in raw
        if isinstance(item, Mapping) and item.get("id")
    }
    if len(states) != len(raw):
        raise FrontendEvaluationError("frontend evidence state identity is invalid")
    return states


def _validate_contract(contract: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    if (
        contract.get("schema_version") != FRONTEND_PACKAGE_SCHEMA
        or contract.get("candidate_id") != FRONTEND_PACKAGE_ID
        or contract.get("benchmark_ref") != FRONTEND_BENCHMARK_REF
        or contract.get("total_points") != 100
        or contract.get("behavior_points") != 55
        or contract.get("workflow_points") != 30
        or contract.get("visual_points") != 15
        or contract.get("llm_visual_judge") is not False
    ):
        raise FrontendEvaluationError(
            "deterministic frontend score contract identity changed"
        )
    checks = _contract_checks(contract)
    by_mode = {
        mode: {key for key, item in checks.items() if item["mode"] == mode}
        for mode in ("browser", "workflow", "screenshot")
    }
    if len(by_mode["browser"]) != 45 or sum(
        int(checks[key]["points"]) for key in by_mode["browser"]
    ) != 55:
        raise FrontendEvaluationError(
            "deterministic frontend browser contract must contain 45 checks and 55 points"
        )
    if by_mode["workflow"] != {"W01", "W02", "W03"} or sum(
        int(checks[key]["points"]) for key in by_mode["workflow"]
    ) != 30:
        raise FrontendEvaluationError(
            "deterministic frontend workflow contract must total 30 points"
        )
    if by_mode["screenshot"] != {f"V{index:02d}" for index in range(3, 10)} or sum(
        int(checks[key]["points"]) for key in by_mode["screenshot"]
    ) != 15:
        raise FrontendEvaluationError(
            "deterministic frontend screenshot contract must total 15 points"
        )
    trace_contract = contract.get("trace_contract")
    trace_ids = {f"C{index:02d}" for index in range(1, 7)}
    if (
        not isinstance(trace_contract, Mapping)
        or set(trace_contract.get("source_ids", [])) != trace_ids
        or trace_contract.get("scored_individually") is not False
    ):
        raise FrontendEvaluationError("deterministic frontend trace contract changed")
    workflows = contract.get("workflows")
    if not isinstance(workflows, list) or len(workflows) != 3:
        raise FrontendEvaluationError("deterministic frontend workflows are invalid")
    required: list[str] = []
    for workflow in workflows:
        if not isinstance(workflow, Mapping):
            raise FrontendEvaluationError("deterministic frontend workflow is invalid")
        workflow_id = str(workflow.get("id") or "")
        members = workflow.get("requires_all")
        if (
            workflow_id not in by_mode["workflow"]
            or workflow.get("dimension_id") != checks[workflow_id]["dimension_id"]
            or workflow.get("points") != checks[workflow_id]["points"]
            or not isinstance(members, list)
            or not members
        ):
            raise FrontendEvaluationError(
                f"deterministic frontend workflow identity is invalid: {workflow_id}"
            )
        required.extend(str(member) for member in members)
    if set(required) != trace_ids or len(required) != len(trace_ids):
        raise FrontendEvaluationError(
            "deterministic frontend workflows must partition all traces"
        )
    return checks


def _validate_asset_lock(root: Path, asset_lock: Mapping[str, Any]) -> None:
    assets = asset_lock.get("assets")
    if (
        asset_lock.get("schema_version") != FRONTEND_ASSET_LOCK_SCHEMA
        or not isinstance(assets, Mapping)
        or set(assets) != set(_LOCKED_ASSET_PATHS)
    ):
        raise FrontendEvaluationError("deterministic frontend asset lock changed")
    project_root = root.parents[2]
    for name, expected_relative in _LOCKED_ASSET_PATHS.items():
        item = assets.get(name)
        if (
            not isinstance(item, Mapping)
            or set(item) != {"path", "sha256"}
            or item.get("path") != expected_relative
        ):
            raise FrontendEvaluationError(
                f"deterministic frontend locked asset identity changed: {name}"
            )
        path = (project_root / expected_relative).resolve()
        if project_root != path and project_root not in path.parents:
            raise FrontendEvaluationError(
                f"deterministic frontend locked asset escapes project: {name}"
            )
        if not path.is_file() or _sha256_file(path) != item.get("sha256"):
            raise FrontendEvaluationError(
                f"deterministic frontend locked asset changed: {name}"
            )


def _validate_assets(
    root: Path,
    *,
    contract: Mapping[str, Any],
    visual_rules: Mapping[str, Any],
    render_environment: Mapping[str, Any],
    reference_manifest: Mapping[str, Any],
) -> None:
    checks = _contract_checks(contract)
    if (
        visual_rules.get("schema_version") != FRONTEND_VISUAL_RULES_SCHEMA
        or visual_rules.get("block_size") != 4
        or visual_rules.get("edge_threshold") != 12
        or visual_rules.get("edge_tolerance_blocks") != 1
    ):
        raise FrontendEvaluationError("deterministic frontend visual rules changed")
    weights = visual_rules.get("weights")
    if not isinstance(weights, Mapping) or round(
        sum(float(weights.get(key, -1)) for key in ("ssim", "color", "edge")),
        6,
    ) != 1:
        raise FrontendEvaluationError("deterministic frontend visual weights changed")
    expected_viewports = {
        "default_desktop": (1440, 900),
        "default_tablet": (768, 1024),
        "default_mobile": (390, 844),
        "selected_saving": (1440, 900),
        "failure": (1440, 900),
        "desktop_inspector": (1440, 900),
        "mobile_inspector": (390, 844),
    }
    rules = visual_rules.get("rules")
    if not isinstance(rules, list) or len(rules) != 7:
        raise FrontendEvaluationError("deterministic frontend visual rules are incomplete")
    found_ids: set[str] = set()
    found_states: set[str] = set()
    for rule in rules:
        if not isinstance(rule, Mapping):
            raise FrontendEvaluationError("deterministic frontend visual rule is invalid")
        check_id = str(rule.get("id") or "")
        state_id = str(rule.get("state") or "")
        crop = rule.get("crop")
        starter = rule.get("starter_similarity")
        viewport = expected_viewports.get(state_id)
        if (
            check_id in found_ids
            or check_id not in checks
            or checks[check_id]["mode"] != "screenshot"
            or state_id in found_states
            or viewport is None
            or rule.get("points") != checks[check_id]["points"]
            or not isinstance(crop, list)
            or len(crop) != 4
            or not all(isinstance(value, int) and value >= 0 for value in crop)
            or crop[2] < 1
            or crop[3] < 1
            or crop[0] + crop[2] > viewport[0]
            or crop[1] + crop[3] > viewport[1]
            or not isinstance(starter, (int, float))
            or isinstance(starter, bool)
            or not 0 <= float(starter) < 1
        ):
            raise FrontendEvaluationError(
                f"deterministic frontend visual rule is invalid: {check_id}"
            )
        found_ids.add(check_id)
        found_states.add(state_id)
    if found_ids != {f"V{index:02d}" for index in range(3, 10)} or found_states != set(
        expected_viewports
    ):
        raise FrontendEvaluationError("deterministic frontend visual states changed")
    if (
        render_environment.get("schema_version")
        != FRONTEND_RENDER_ENVIRONMENT_SCHEMA
        or render_environment.get("browser_channel") != "chrome"
        or render_environment.get("browser_version") != "152.0.7977.65"
        or render_environment.get("locale") != "en-US"
        or render_environment.get("timezone_id") != "UTC"
        or render_environment.get("device_scale_factor") != 1
        or render_environment.get("reduced_motion") != "reduce"
        or render_environment.get("color_scheme") != "light"
    ):
        raise FrontendEvaluationError(
            "deterministic frontend render environment changed"
        )
    if (
        reference_manifest.get("schema_version")
        != FRONTEND_REFERENCE_MANIFEST_SCHEMA
        or reference_manifest.get("render_environment_sha256")
        != _sha256_file(root / "render-environment.json")
    ):
        raise FrontendEvaluationError(
            "deterministic frontend reference manifest changed"
        )
    reference_states = _manifest_state_map(reference_manifest)
    if set(reference_states) != set(expected_viewports):
        raise FrontendEvaluationError(
            "deterministic frontend reference states changed"
        )
    for state_id, (width, height) in expected_viewports.items():
        state = reference_states[state_id]
        filename = str(state.get("filename") or "")
        path = root / "reference" / filename
        if (
            filename != f"{state_id}.png"
            or state.get("width") != width
            or state.get("height") != height
            or not path.is_file()
            or _sha256_file(path) != state.get("sha256")
        ):
            raise FrontendEvaluationError(
                f"deterministic frontend reference state changed: {state_id}"
            )
    browser_ids = set(
        re.findall(
            r'record\("([A-Z]\d{2}[a-z]?)"',
            (root / "browser_score.js").read_text(encoding="utf-8"),
        )
    )
    if browser_ids != {
        key for key, value in checks.items() if value["mode"] == "browser"
    }:
        raise FrontendEvaluationError(
            "deterministic frontend browser scorer and contract differ"
        )
    trace_ids = set(
        re.findall(
            r'record\("(C\d{2})"',
            (root / "trace_score.js").read_text(encoding="utf-8"),
        )
    )
    if trace_ids != set(contract["trace_contract"]["source_ids"]):
        raise FrontendEvaluationError(
            "deterministic frontend trace scorer and contract differ"
        )


def load_deterministic_frontend_question(
    root: Path,
) -> DeterministicFrontendQuestionPackage:
    resolved = root.expanduser().resolve()
    required = {
        "asset-lock.json",
        "prompt.md",
        "starter.html",
        "score-contract.json",
        "browser_score.js",
        "trace_score.js",
        "visual_evidence.js",
        "visual-rules.json",
        "render-environment.json",
        "reference/manifest.json",
    }
    missing = sorted(name for name in required if not (resolved / name).is_file())
    if missing:
        raise FrontendEvaluationError(
            "deterministic frontend question package is incomplete: "
            + ", ".join(missing)
        )
    asset_lock = _load_json_object(resolved / "asset-lock.json")
    _validate_asset_lock(resolved, asset_lock)
    contract = _load_json_object(resolved / "score-contract.json")
    visual_rules = _load_json_object(resolved / "visual-rules.json")
    render_environment = _load_json_object(resolved / "render-environment.json")
    reference_manifest = _load_json_object(resolved / "reference" / "manifest.json")
    _validate_contract(contract)
    _validate_assets(
        resolved,
        contract=contract,
        visual_rules=visual_rules,
        render_environment=render_environment,
        reference_manifest=reference_manifest,
    )
    package = DeterministicFrontendQuestionPackage(
        root=resolved,
        prompt_template=(resolved / "prompt.md").read_text(encoding="utf-8"),
        starter_html=(resolved / "starter.html").read_text(encoding="utf-8"),
        contract=contract,
        asset_lock=asset_lock,
        visual_rules=visual_rules,
        render_environment=render_environment,
        reference_manifest=reference_manifest,
    )
    package.prompt
    return package


def _visual_progress(similarity: float, starter_similarity: float) -> float:
    raw = (similarity - starter_similarity) / (1.0 - starter_similarity)
    return round(max(0.0, min(1.0, raw)), 6)


def apply_deterministic_frontend_points(
    browser_payload: Mapping[str, Any],
    trace_payload: Mapping[str, Any],
    evidence_manifest: Mapping[str, Any],
    *,
    candidate_dir: Path,
    package: DeterministicFrontendQuestionPackage,
) -> dict[str, Any]:
    contract = package.contract
    checks = _contract_checks(contract)
    browser_ids = {
        check_id for check_id, check in checks.items() if check["mode"] == "browser"
    }
    workflow_ids = {
        check_id for check_id, check in checks.items() if check["mode"] == "workflow"
    }
    visual_ids = {
        check_id for check_id, check in checks.items() if check["mode"] == "screenshot"
    }
    raw_browser = browser_payload.get("check_results")
    if not isinstance(raw_browser, Mapping) or set(raw_browser) != browser_ids:
        raise FrontendEvaluationError(
            "deterministic frontend browser payload and contract differ"
        )
    trace_ids = set(contract["trace_contract"]["source_ids"])
    raw_trace = trace_payload.get("certificate_results")
    if not isinstance(raw_trace, Mapping) or set(raw_trace) != trace_ids:
        raise FrontendEvaluationError(
            "deterministic frontend trace payload and contract differ"
        )
    details: list[dict[str, Any]] = []
    for check_id in sorted(browser_ids):
        observed = raw_browser[check_id]
        if not isinstance(observed, Mapping):
            raise FrontendEvaluationError(
                f"deterministic frontend browser result is invalid: {check_id}"
            )
        check = checks[check_id]
        passed = observed.get("passed") is True
        details.append(
            {
                "id": check_id,
                "dimension_id": check["dimension_id"],
                "mode": "browser",
                "source": check.get("source"),
                "passed": passed,
                "points": check["points"] if passed else 0,
                "max_points": check["points"],
                "evidence": str(observed.get("evidence") or "")[:2_000],
            }
        )
    trace_passes = {
        check_id: raw_trace[check_id].get("passed") is True
        for check_id in sorted(trace_ids)
        if isinstance(raw_trace[check_id], Mapping)
    }
    if set(trace_passes) != trace_ids:
        raise FrontendEvaluationError("deterministic frontend trace result is invalid")
    for workflow in contract["workflows"]:
        workflow_id = str(workflow["id"])
        if workflow_id not in workflow_ids:
            raise FrontendEvaluationError(
                f"deterministic frontend workflow is missing: {workflow_id}"
            )
        members = [str(value) for value in workflow["requires_all"]]
        member_passes = {member: trace_passes[member] for member in members}
        passed = all(member_passes.values())
        check = checks[workflow_id]
        details.append(
            {
                "id": workflow_id,
                "dimension_id": check["dimension_id"],
                "mode": "workflow",
                "passed": passed,
                "points": check["points"] if passed else 0,
                "max_points": check["points"],
                "evidence": {
                    "requires_all": members,
                    "member_passes": member_passes,
                    "source_trace_evidence_sha256": _sha256_payload(
                        {
                            member: raw_trace[member].get("evidence")
                            for member in members
                        }
                    ),
                },
            }
        )
    candidate_states = _manifest_state_map(evidence_manifest)
    reference_states = _manifest_state_map(package.reference_manifest)
    for rule in package.visual_rules["rules"]:
        check_id = str(rule["id"])
        if check_id not in visual_ids:
            raise FrontendEvaluationError(
                f"deterministic frontend visual rule is unknown: {check_id}"
            )
        state_id = str(rule["state"])
        candidate_state = candidate_states.get(state_id)
        reference_state = reference_states.get(state_id)
        if candidate_state is None or reference_state is None:
            raise FrontendEvaluationError(
                f"deterministic frontend visual state is missing: {state_id}"
            )
        demonstrated = candidate_state.get("demonstrated") is True
        if demonstrated:
            reference = read_rgb_png(
                package.reference_root / str(reference_state["filename"])
            )
            candidate = read_rgb_png(
                candidate_dir / str(candidate_state["filename"])
            )
            metrics = visual_similarity(
                reference,
                candidate,
                crop=rule["crop"],
                visual_rules=package.visual_rules,
            )
        else:
            metrics = {
                "ssim": 0.0,
                "color": 0.0,
                "edge_f1": 0.0,
                "similarity": 0.0,
            }
        starter_similarity = round(float(rule["starter_similarity"]), 6)
        progress = _visual_progress(metrics["similarity"], starter_similarity)
        check = checks[check_id]
        points = round(int(check["points"]) * progress, 6)
        details.append(
            {
                "id": check_id,
                "dimension_id": check["dimension_id"],
                "mode": "screenshot",
                "passed": progress == 1.0,
                "points": points,
                "max_points": check["points"],
                "evidence": {
                    "state": state_id,
                    "crop": list(rule["crop"]),
                    "demonstrated": demonstrated,
                    **metrics,
                    "starter_similarity": starter_similarity,
                    "progress": progress,
                    "progress_formula": "starter_anchored_linear_progress",
                },
            }
        )
    raw_diagnostics = browser_payload.get("diagnostics")
    diagnostics = dict(raw_diagnostics) if isinstance(raw_diagnostics, Mapping) else {}
    page_errors = diagnostics.get("pageErrors")
    uncaught = page_errors if isinstance(page_errors, list) else []
    expected_environment = {
        "browserVersion": package.render_environment["browser_version"],
        "locale": package.render_environment["locale"],
        "timeZone": package.render_environment["timezone_id"],
        "deviceScaleFactor": package.render_environment["device_scale_factor"],
        "reducedMotion": package.render_environment["reduced_motion"],
        "colorScheme": package.render_environment["color_scheme"],
    }
    observed_environment = browser_payload.get("environment")
    environment_matches = observed_environment == expected_environment
    if not environment_matches:
        diagnostics["environmentDrift"] = {
            "expected": expected_environment,
            "observed": observed_environment,
        }
    app_shell_rendered = browser_payload.get("app_shell_rendered") is True
    initial_data_rendered = browser_payload.get("initial_data_rendered") is True
    invalid = (
        not app_shell_rendered
        or (not initial_data_rendered and bool(uncaught))
        or not environment_matches
    )
    behavior_score = sum(
        int(item["points"]) for item in details if item["mode"] == "browser"
    )
    workflow_score = sum(
        int(item["points"]) for item in details if item["mode"] == "workflow"
    )
    visual_score = round(
        sum(float(item["points"]) for item in details if item["mode"] == "screenshot"),
        6,
    )
    diagnostic_score = round(behavior_score + workflow_score + visual_score, 6)
    dimensions: dict[str, dict[str, float | int]] = {}
    for dimension in contract["dimensions"]:
        dimension_id = str(dimension["id"])
        value = round(
            sum(
                float(item["points"])
                for item in details
                if item["dimension_id"] == dimension_id
            ),
            6,
        )
        dimensions[dimension_id] = {
            "points": int(value) if value.is_integer() else value,
            "max_points": int(dimension["points"]),
        }
    source_trace_results = {
        check_id: {
            "passed": trace_passes[check_id],
            "evidence_sha256": _sha256_payload(raw_trace[check_id].get("evidence")),
        }
        for check_id in sorted(trace_ids)
    }
    ranking_score = 0 if invalid else diagnostic_score
    return {
        "schema_version": "frontend_score_v2",
        "status": "complete",
        "validity_state": "invalid" if invalid else "qualified",
        "automatic_score": diagnostic_score,
        "automatic_max_score": 100,
        "behavior_score": behavior_score,
        "behavior_max_score": int(contract["behavior_points"]),
        "workflow_score": workflow_score,
        "workflow_max_score": int(contract["workflow_points"]),
        "visual_score": visual_score,
        "visual_max_score": int(contract["visual_points"]),
        "diagnostic_score": diagnostic_score,
        "ranking_score": ranking_score,
        "total_score": ranking_score,
        "max_score": int(contract["total_points"]),
        "llm_visual_judge": False,
        "dimensions": dimensions,
        "failed_check_ids": sorted(
            str(item["id"]) for item in details if item.get("passed") is not True
        ),
        "validity_evidence": {
            "app_shell_rendered": app_shell_rendered,
            "initial_data_rendered": initial_data_rendered,
            "uncaught_error_count": len(uncaught),
            "environment_matches": environment_matches,
        },
        "source_trace_results": source_trace_results,
        "observed_environment": observed_environment,
        "score_details": sorted(details, key=lambda item: str(item["id"])),
        "browser_diagnostics": diagnostics,
        "trace_workflow_errors": trace_payload.get("workflow_errors", {}),
        "contract_sha256": _sha256_file(package.root / "score-contract.json"),
        "asset_lock_sha256": _sha256_file(package.root / "asset-lock.json"),
        "visual_rules_sha256": _sha256_file(package.root / "visual-rules.json"),
        "render_environment_sha256": _sha256_file(
            package.root / "render-environment.json"
        ),
        "reference_manifest_sha256": _sha256_file(
            package.reference_root / "manifest.json"
        ),
        "browser_scorer_sha256": _sha256_file(package.browser_score_script),
        "trace_scorer_sha256": _sha256_file(package.trace_score_script),
    }


def score_deterministic_frontend_html(
    html_path: Path,
    output_dir: Path,
    package: DeterministicFrontendQuestionPackage,
    *,
    playwright_cli: Path | None = None,
    browser: str | None = None,
    timeout_seconds: int = DEFAULT_BROWSER_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    source = html_path.expanduser().resolve()
    if not source.is_file():
        raise FrontendEvaluationError("frontend HTML is unavailable")
    destination = output_dir.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    cli = playwright_cli or resolve_playwright_cli()
    browser_name = browser or os.environ.get(
        "MODELDIAL_PLAYWRIGHT_BROWSER",
        "chromium",
    )
    generated_evidence = _materialize_visual_evidence_script(
        package.visual_evidence_script,
        destination,
    )
    cli_config = destination / "playwright-cli.config.json"
    environment = package.render_environment
    _write_json(
        cli_config,
        {
            "browser": {
                "contextOptions": {
                    "locale": environment["locale"],
                    "timezoneId": environment["timezone_id"],
                    "deviceScaleFactor": environment["device_scale_factor"],
                    "reducedMotion": environment["reduced_motion"],
                    "colorScheme": environment["color_scheme"],
                }
            }
        },
    )
    browser_session = f"modeldial-frontend-v2-browser-{uuid4().hex[:12]}"
    trace_session = f"modeldial-frontend-v2-trace-{uuid4().hex[:12]}"
    with _serve_directory(source.parent) as root_url:
        url = root_url.rsplit("/", 1)[0] + "/" + quote(source.name)
        try:
            _run_cli(
                cli,
                browser_session,
                ["open", url, "--browser", browser_name, "--config", str(cli_config)],
                cwd=destination,
                timeout=timeout_seconds,
            )
            browser_payload = _run_code_json(
                cli,
                browser_session,
                package.browser_score_script,
                cwd=destination,
                timeout=timeout_seconds,
            )
            evidence_payload = _run_code_json(
                cli,
                browser_session,
                generated_evidence,
                cwd=destination,
                timeout=timeout_seconds,
            )
        finally:
            try:
                _run_cli(cli, browser_session, ["close"], cwd=destination, timeout=20)
            except BaseException:
                pass
        try:
            _run_cli(
                cli,
                trace_session,
                ["open", url, "--browser", browser_name, "--config", str(cli_config)],
                cwd=destination,
                timeout=timeout_seconds,
            )
            trace_payload = _run_code_json(
                cli,
                trace_session,
                package.trace_score_script,
                cwd=destination,
                timeout=timeout_seconds,
            )
        finally:
            try:
                _run_cli(cli, trace_session, ["close"], cwd=destination, timeout=20)
            except BaseException:
                pass
    evidence_manifest = _build_evidence_manifest(
        destination,
        evidence_payload,
        cli=cli,
        browser=browser_name,
        timeout_seconds=timeout_seconds,
    )
    try:
        score = apply_deterministic_frontend_points(
            browser_payload,
            trace_payload,
            evidence_manifest,
            candidate_dir=destination,
            package=package,
        )
    except FrontendImageError as error:
        raise FrontendEvaluationError(str(error)) from error
    score.update(
        {
            "question_id": FRONTEND_PACKAGE_ID,
            "benchmark_ref": FRONTEND_BENCHMARK_REF,
            "prompt_sha256": _sha256_text(package.prompt),
            "html_sha256": _sha256_file(source),
            "evidence_manifest_sha256": _sha256_payload(evidence_manifest),
        }
    )
    _write_json(destination / "browser-payload.json", browser_payload)
    _write_json(destination / "trace-payload.json", trace_payload)
    _write_json(destination / "automatic-score.json", score)
    _write_json(destination / "evidence-manifest.json", evidence_manifest)
    return {
        "score": score,
        "evidence_manifest": evidence_manifest,
        "contact_sheet_path": str(destination / "contact-sheet.png"),
    }


__all__ = [
    "DeterministicFrontendQuestionPackage",
    "FRONTEND_BENCHMARK_REF",
    "FRONTEND_PACKAGE_ID",
    "apply_deterministic_frontend_points",
    "default_deterministic_frontend_question_root",
    "load_deterministic_frontend_question",
    "score_deterministic_frontend_html",
]
