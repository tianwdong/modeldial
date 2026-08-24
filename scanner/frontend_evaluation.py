from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache, partial
import base64
import hashlib
import html
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
from typing import Any, Iterator, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
    urlopen as default_urlopen,
)
from uuid import uuid4


FRONTEND_PACKAGE_ID = "frontend-case-stream-explorer-v17"
FRONTEND_PACKAGE_SCHEMA = "frontend_score_contract_v17"
FRONTEND_VISUAL_RUBRIC_SCHEMA = "frontend_visual_rubric_v2"
FRONTEND_VISUAL_JUDGMENT_SCHEMA = "frontend_visual_judgment_v1"
FRONTEND_JUDGE_MODEL = "gpt-5.6-sol"
FRONTEND_JUDGE_EFFORT = "max"
DEFAULT_BROWSER_TIMEOUT_SECONDS = 180
MAX_VISUAL_EVIDENCE_LENGTH = 1_000
MAX_VISUAL_SUMMARY_LENGTH = 2_000
MAX_VISUAL_CONTACT_SHEET_BYTES = 16 * 1024 * 1024
MAX_VISUAL_JUDGE_RESPONSE_BYTES = 2 * 1024 * 1024


class FrontendEvaluationError(RuntimeError):
    pass


@dataclass(frozen=True)
class FrontendQuestionPackage:
    root: Path
    prompt_template: str
    starter_html: str
    contract: dict[str, Any]
    visual_rubric: dict[str, Any]

    @property
    def prompt(self) -> str:
        marker = "{{STARTER_HTML}}"
        if self.prompt_template.count(marker) != 1:
            raise FrontendEvaluationError(
                "frontend prompt must contain the starter marker exactly once"
            )
        return self.prompt_template.replace(marker, self.starter_html.rstrip())

    @property
    def browser_score_script(self) -> Path:
        return self.root / "browser_score.js"

    @property
    def visual_evidence_script(self) -> Path:
        return self.root / "visual_evidence.js"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - stdlib protocol method
        if self.path.split("?", 1)[0] == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        super().do_GET()


class _RejectRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


@lru_cache(maxsize=1)
def _visual_judge_opener():
    return build_opener(_RejectRedirectHandler())


def default_frontend_question_root(backend_root: Path) -> Path:
    return (
        backend_root
        / "questions"
        / "frontend"
        / "case_stream_explorer_v17"
    )


def load_frontend_question(root: Path) -> FrontendQuestionPackage:
    resolved = root.expanduser().resolve()
    required = {
        "prompt.md",
        "starter.html",
        "score-contract.json",
        "visual-rubric.json",
        "browser_score.js",
        "visual_evidence.js",
    }
    missing = sorted(name for name in required if not (resolved / name).is_file())
    if missing:
        raise FrontendEvaluationError(
            f"frontend question package is incomplete: {', '.join(missing)}"
        )
    contract = _load_json_object(resolved / "score-contract.json")
    rubric = _load_json_object(resolved / "visual-rubric.json")
    _validate_contract(contract)
    _validate_rubric(rubric)
    package = FrontendQuestionPackage(
        root=resolved,
        prompt_template=(resolved / "prompt.md").read_text(encoding="utf-8"),
        starter_html=(resolved / "starter.html").read_text(encoding="utf-8"),
        contract=contract,
        visual_rubric=rubric,
    )
    package.prompt
    return package


def normalize_frontend_html(response: str) -> tuple[str, str]:
    stripped = response.strip()
    fenced = re.fullmatch(
        r"```(?:html)?\s*\n(.*?)\n```",
        stripped,
        re.DOTALL | re.IGNORECASE,
    )
    if fenced:
        stripped = fenced.group(1).strip()
        source_format = "html_fence_recovered"
    else:
        source_format = "html"
    start_match = re.search(r"<!doctype\s+html|<html(?:\s|>)", stripped, re.IGNORECASE)
    if start_match and start_match.start() > 0:
        stripped = stripped[start_match.start():]
        source_format = f"{source_format}_prefix_recovered"
    end_match = re.search(r"</html\s*>", stripped, re.IGNORECASE)
    if end_match:
        stripped = stripped[:end_match.end()]
    if not re.search(r"<html(?:\s|>)", stripped, re.IGNORECASE):
        safe = html.escape(stripped)
        stripped = f"<!doctype html><html><body><pre>{safe}</pre></body></html>"
        source_format = "invalid_html_wrapped"
    return stripped + "\n", source_format


def score_frontend_html(
    html_path: Path,
    output_dir: Path,
    package: FrontendQuestionPackage,
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
    session = f"modeldial-frontend-{uuid4().hex[:12]}"
    generated_evidence = _materialize_visual_evidence_script(
        package.visual_evidence_script,
        destination,
    )
    with _serve_directory(source.parent) as url:
        try:
            _run_cli(
                cli,
                session,
                ["open", url, "--browser", browser_name],
                cwd=destination,
                timeout=timeout_seconds,
            )
            browser_payload = _run_code_json(
                cli,
                session,
                package.browser_score_script,
                cwd=destination,
                timeout=timeout_seconds,
            )
            evidence_payload = _run_code_json(
                cli,
                session,
                generated_evidence,
                cwd=destination,
                timeout=timeout_seconds,
            )
        finally:
            try:
                _run_cli(
                    cli,
                    session,
                    ["close"],
                    cwd=destination,
                    timeout=20,
                )
            except BaseException:
                pass
    evidence_manifest = _build_evidence_manifest(
        destination,
        evidence_payload,
        cli=cli,
        browser=browser_name,
        timeout_seconds=timeout_seconds,
    )
    score = apply_automatic_frontend_points(browser_payload, package.contract)
    score.update(
        {
            "question_id": FRONTEND_PACKAGE_ID,
            "prompt_sha256": _sha256_text(package.prompt),
            "html_sha256": _sha256_file(source),
            "evidence_manifest_sha256": _sha256_payload(evidence_manifest),
        }
    )
    _write_json(destination / "automatic-score.json", score)
    _write_json(destination / "evidence-manifest.json", evidence_manifest)
    return {
        "score": score,
        "evidence_manifest": evidence_manifest,
        "contact_sheet_path": str(destination / "contact-sheet.png"),
    }


def apply_automatic_frontend_points(
    browser_payload: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    checks = _score_check_map(contract)
    automated_ids = {
        check_id
        for check_id, check in checks.items()
        if check["mode"] != "visual_judge"
    }
    raw_results = browser_payload.get("check_results", {})
    if not isinstance(raw_results, Mapping):
        raise FrontendEvaluationError("browser scorer returned invalid checks")
    unknown = set(raw_results) - automated_ids
    if unknown:
        raise FrontendEvaluationError(
            f"browser scorer returned unknown checks: {sorted(unknown)}"
        )
    details: list[dict[str, Any]] = []
    for check_id in sorted(automated_ids):
        check = checks[check_id]
        observed = raw_results.get(check_id, {})
        if not isinstance(observed, Mapping):
            observed = {}
        passed = bool(observed.get("passed"))
        details.append(
            {
                "id": check_id,
                "dimension_id": check["dimension_id"],
                "mode": check["mode"],
                "passed": passed,
                "points": check["points"] if passed else 0,
                "max_points": check["points"],
                "evidence": str(
                    observed.get(
                        "evidence",
                        "browser scorer did not return this check",
                    )
                )[:2_000],
            }
        )
    automatic_score = sum(int(item["points"]) for item in details)
    configured_automatic = int(contract["automated_points"])
    if automatic_score > configured_automatic:
        raise FrontendEvaluationError("automatic frontend score exceeds its contract")
    failed_ids = {item["id"] for item in details if not item["passed"]}
    diagnostics = browser_payload.get("diagnostics", {})
    if not isinstance(diagnostics, Mapping):
        diagnostics = {}
    app_shell_rendered = bool(browser_payload.get("app_shell_rendered"))
    initial_data_rendered = browser_payload.get("initial_data_rendered")
    page_errors = diagnostics.get("pageErrors", [])
    uncaught_errors = page_errors if isinstance(page_errors, list) else []
    qualified_requires = set(contract["validity"]["qualified_requires"])
    missing_qualified_checks = sorted(qualified_requires & failed_ids)
    if not app_shell_rendered or (
        initial_data_rendered is False and bool(uncaught_errors)
    ):
        validity_state = "invalid"
    elif not missing_qualified_checks:
        validity_state = "qualified"
    else:
        validity_state = "partial"
    dimensions = _dimension_totals(contract, details)
    return {
        "schema_version": "frontend_score_v1",
        "status": "visual_pending",
        "validity_state": validity_state,
        "automatic_score": automatic_score,
        "automatic_max_score": configured_automatic,
        "visual_score": None,
        "visual_max_score": int(contract["visual_judge_points"]),
        "diagnostic_score": None,
        "ranking_score": None,
        "total_score": None,
        "max_score": int(contract["total_points"]),
        "dimensions": dimensions,
        "failed_check_ids": sorted(failed_ids),
        "validity_evidence": {
            "app_shell_rendered": app_shell_rendered,
            "initial_data_rendered": initial_data_rendered,
            "uncaught_error_count": len(uncaught_errors),
            "missing_qualified_checks": missing_qualified_checks,
        },
        "score_details": sorted(details, key=lambda item: item["id"]),
        "browser_diagnostics": dict(diagnostics),
    }


def build_visual_judge_prompt(
    candidate_alias: str,
    rubric: Mapping[str, Any],
    evidence_manifest: Mapping[str, Any],
) -> str:
    alias = _required_alias(candidate_alias)
    demonstrated = [
        str(item.get("id"))
        for item in evidence_manifest.get("states", [])
        if isinstance(item, Mapping) and item.get("demonstrated") is True
    ]
    rubric_payload = {
        "schema_version": rubric["schema_version"],
        "checks": rubric["checks"],
        "evidence_caps": rubric["evidence_caps"],
    }
    return (
        "You are the independent visual rater for one anonymous frontend output. "
        "Judge only what is visibly demonstrated in the attached contact sheet. "
        "Do not infer model identity or functional correctness, and do not award "
        "visual points for hidden behavior. Use integer points only.\n\n"
        f"Candidate alias: {alias}\n"
        f"Demonstrated evidence states: {json.dumps(demonstrated)}\n"
        f"Rubric: {json.dumps(rubric_payload, ensure_ascii=True, sort_keys=True)}\n\n"
        "Return one JSON object with exactly: schema_version, candidate_alias, "
        "checks, total, summary. schema_version must be "
        f"{FRONTEND_VISUAL_JUDGMENT_SCHEMA}. checks must contain V03, V04, V05, "
        "and V06 exactly once; every item must contain id, points, and concise "
        "visible evidence. total must equal the sum."
    )


def request_visual_judgment(
    *,
    base_url: str,
    api_key: str,
    prompt: str,
    contact_sheet_path: Path,
    timeout_seconds: int,
    urlopen=default_urlopen,
) -> dict[str, Any]:
    endpoint = base_url.strip().rstrip("/")
    if not endpoint.startswith("https://"):
        raise FrontendEvaluationError("visual judge endpoint must use HTTPS")
    secret = api_key.strip()
    if not secret:
        raise FrontendEvaluationError("visual judge credential is unavailable")
    image_path = contact_sheet_path.expanduser().resolve()
    if not image_path.is_file():
        raise FrontendEvaluationError("visual judge contact sheet is unavailable")
    image = image_path.read_bytes()
    if not image or len(image) > MAX_VISUAL_CONTACT_SHEET_BYTES:
        raise FrontendEvaluationError("visual judge contact sheet size is invalid")
    body = {
        "model": FRONTEND_JUDGE_MODEL,
        "reasoning": {"effort": FRONTEND_JUDGE_EFFORT},
        "store": False,
        "max_output_tokens": 8_192,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {
                        "type": "input_image",
                        "image_url": (
                            "data:image/png;base64,"
                            + base64.b64encode(image).decode("ascii")
                        ),
                    },
                ],
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "frontend_visual_judgment",
                "strict": True,
                "schema": _visual_judgment_json_schema(),
            }
        },
    }
    request = Request(
        f"{endpoint}/responses",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
            "User-Agent": "ModelDial/FrontendVisualJudgeV1",
        },
        method="POST",
    )
    try:
        response_context = (
            _visual_judge_opener().open(
                request,
                timeout=max(1, int(timeout_seconds)),
            )
            if urlopen is default_urlopen
            else urlopen(request, timeout=max(1, int(timeout_seconds)))
        )
        with response_context as response:
            raw = response.read(MAX_VISUAL_JUDGE_RESPONSE_BYTES + 1)
    except HTTPError as error:
        status = error.code
        error.close()
        raise FrontendEvaluationError(
            f"visual judge request failed (http {status})"
        ) from None
    except (TimeoutError, URLError, OSError) as error:
        raise FrontendEvaluationError("visual judge request failed") from error
    if len(raw) > MAX_VISUAL_JUDGE_RESPONSE_BYTES:
        raise FrontendEvaluationError("visual judge response is too large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FrontendEvaluationError("visual judge returned invalid JSON") from error
    text = _responses_output_text(payload)
    try:
        judgment = json.loads(text)
    except json.JSONDecodeError as error:
        raise FrontendEvaluationError(
            "visual judge returned an invalid judgment object"
        ) from error
    if not isinstance(judgment, dict):
        raise FrontendEvaluationError("visual judge returned no judgment object")
    return judgment


def validate_visual_judgment(
    value: Mapping[str, Any],
    *,
    candidate_alias: str,
    rubric: Mapping[str, Any],
    evidence_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    alias = _required_alias(candidate_alias)
    if set(value) != {
        "schema_version",
        "candidate_alias",
        "checks",
        "total",
        "summary",
    }:
        raise FrontendEvaluationError("visual judgment fields are invalid")
    if value.get("schema_version") != FRONTEND_VISUAL_JUDGMENT_SCHEMA:
        raise FrontendEvaluationError("visual judgment schema is unsupported")
    if value.get("candidate_alias") != alias:
        raise FrontendEvaluationError("visual judgment alias changed")
    checks = value.get("checks")
    if not isinstance(checks, list):
        raise FrontendEvaluationError("visual judgment checks are invalid")
    expected = {
        str(item["id"]): int(item["max_points"])
        for item in rubric["checks"]
    }
    supplied: dict[str, dict[str, Any]] = {}
    for item in checks:
        if not isinstance(item, Mapping) or set(item) != {"id", "points", "evidence"}:
            raise FrontendEvaluationError("visual judgment check fields are invalid")
        check_id = str(item.get("id") or "")
        if check_id not in expected or check_id in supplied:
            raise FrontendEvaluationError("visual judgment check identity is invalid")
        points = item.get("points")
        evidence = item.get("evidence")
        if (
            not isinstance(points, int)
            or isinstance(points, bool)
            or not 0 <= points <= expected[check_id]
        ):
            raise FrontendEvaluationError(
                f"visual judgment points are invalid for {check_id}"
            )
        if (
            not isinstance(evidence, str)
            or not evidence.strip()
            or len(evidence) > MAX_VISUAL_EVIDENCE_LENGTH
        ):
            raise FrontendEvaluationError(
                f"visual judgment evidence is invalid for {check_id}"
            )
        supplied[check_id] = {
            "id": check_id,
            "points": points,
            "evidence": evidence.strip(),
        }
    if set(supplied) != set(expected):
        raise FrontendEvaluationError("visual judgment check coverage is incomplete")
    demonstrated = {
        str(item.get("id"))
        for item in evidence_manifest.get("states", [])
        if isinstance(item, Mapping) and item.get("demonstrated") is True
    }
    for cap in rubric["evidence_caps"]:
        required = {str(item) for item in cap["required_states"]}
        check_id = str(cap["check_id"])
        maximum = int(cap["maximum_without_states"])
        if not required.issubset(demonstrated) and supplied[check_id]["points"] > maximum:
            raise FrontendEvaluationError(
                f"visual judgment exceeds the evidence cap for {check_id}"
            )
    total = value.get("total")
    computed_total = sum(int(item["points"]) for item in supplied.values())
    if total != computed_total or computed_total > int(rubric["visual_points"]):
        raise FrontendEvaluationError("visual judgment total is invalid")
    summary = value.get("summary")
    if (
        not isinstance(summary, str)
        or not summary.strip()
        or len(summary) > MAX_VISUAL_SUMMARY_LENGTH
    ):
        raise FrontendEvaluationError("visual judgment summary is invalid")
    return {
        "schema_version": FRONTEND_VISUAL_JUDGMENT_SCHEMA,
        "candidate_alias": alias,
        "checks": [supplied[check_id] for check_id in sorted(supplied)],
        "total": computed_total,
        "summary": summary.strip(),
    }


def merge_visual_judgment(
    automatic_score: Mapping[str, Any],
    judgment: Mapping[str, Any],
    *,
    candidate_alias: str,
    contract: Mapping[str, Any],
    rubric: Mapping[str, Any],
    evidence_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    validated = validate_visual_judgment(
        judgment,
        candidate_alias=candidate_alias,
        rubric=rubric,
        evidence_manifest=evidence_manifest,
    )
    checks = _score_check_map(contract)
    details = [dict(item) for item in automatic_score["score_details"]]
    for item in validated["checks"]:
        check = checks[item["id"]]
        details.append(
            {
                "id": item["id"],
                "dimension_id": check["dimension_id"],
                "mode": "visual_judge",
                "passed": item["points"] == check["points"],
                "points": item["points"],
                "max_points": check["points"],
                "evidence": item["evidence"],
            }
        )
    automatic_points = int(automatic_score["automatic_score"])
    visual_points = int(validated["total"])
    diagnostic_score = automatic_points + visual_points
    ranking_score = (
        0
        if automatic_score.get("validity_state") == "invalid"
        else diagnostic_score
    )
    merged = dict(automatic_score)
    merged.update(
        {
            "status": "complete",
            "visual_score": visual_points,
            "diagnostic_score": diagnostic_score,
            "ranking_score": ranking_score,
            "total_score": ranking_score,
            "dimensions": _dimension_totals(contract, details),
            "score_details": sorted(details, key=lambda item: item["id"]),
            "visual_judgment": validated,
            "visual_judge": {
                "model_id": FRONTEND_JUDGE_MODEL,
                "reasoning_effort": FRONTEND_JUDGE_EFFORT,
                "rubric_schema_version": rubric["schema_version"],
            },
        }
    )
    return merged


def resolve_playwright_cli() -> Path:
    configured = os.environ.get("MODELDIAL_PLAYWRIGHT_CLI", "").strip()
    candidates = [
        Path(configured).expanduser() if configured else None,
        Path(executable) if (executable := shutil.which("playwright-cli")) else None,
        Path.home() / ".codex" / "skills" / "playwright" / "scripts" / "playwright_cli.sh",
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    raise FrontendEvaluationError("Playwright CLI is unavailable")


@contextmanager
def _serve_directory(directory: Path) -> Iterator[str]:
    handler = partial(_QuietHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/index.html"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _run_cli(
    cli: Path,
    session: str,
    args: list[str],
    *,
    cwd: Path,
    timeout: int,
    json_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = [str(cli)]
    if json_output:
        command.extend(["--json", "--raw"])
    command.extend([f"-s={session}", *args])
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=max(1, int(timeout)),
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise FrontendEvaluationError("Playwright CLI timed out") from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise FrontendEvaluationError(
            f"Playwright CLI failed: {detail[-2_000:]}"
        )
    return completed


def _run_code_json(
    cli: Path,
    session: str,
    script: Path,
    *,
    cwd: Path,
    timeout: int,
) -> dict[str, Any]:
    completed = _run_cli(
        cli,
        session,
        ["run-code", "--filename", str(script)],
        cwd=cwd,
        timeout=timeout,
        json_output=True,
    )
    try:
        outer = json.loads(completed.stdout)
        result = outer.get("result") if isinstance(outer, Mapping) else None
        payload = json.loads(result) if isinstance(result, str) else result
    except (json.JSONDecodeError, TypeError) as error:
        raise FrontendEvaluationError("Playwright CLI returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise FrontendEvaluationError("Playwright CLI returned no result object")
    return payload


def _materialize_visual_evidence_script(template: Path, output_dir: Path) -> Path:
    marker = "__MODELDIAL_EVIDENCE_DIR_JSON__"
    source = template.read_text(encoding="utf-8")
    if source.count(marker) != 1:
        raise FrontendEvaluationError("visual evidence template marker is invalid")
    generated = output_dir / "visual-evidence.generated.js"
    generated.write_text(
        source.replace(marker, json.dumps(str(output_dir))),
        encoding="utf-8",
    )
    return generated


def _build_evidence_manifest(
    output_dir: Path,
    evidence_payload: Mapping[str, Any],
    *,
    cli: Path,
    browser: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    if evidence_payload.get("schema_version") != "frontend_visual_evidence_v1":
        raise FrontendEvaluationError("visual evidence schema is unsupported")
    raw_states = evidence_payload.get("states")
    if not isinstance(raw_states, list):
        raise FrontendEvaluationError("visual evidence states are invalid")
    expected_ids = [
        "default_desktop",
        "default_tablet",
        "default_mobile",
        "selected_saving",
        "failure",
        "desktop_inspector",
        "mobile_inspector",
    ]
    states: list[dict[str, Any]] = []
    for expected_id, raw in zip(expected_ids, raw_states, strict=True):
        if not isinstance(raw, Mapping) or raw.get("id") != expected_id:
            raise FrontendEvaluationError("visual evidence state order changed")
        filename = str(raw.get("filename") or "")
        if filename != f"{expected_id}.png":
            raise FrontendEvaluationError("visual evidence filename changed")
        path = output_dir / filename
        if not path.is_file() or path.stat().st_size == 0:
            raise FrontendEvaluationError("visual evidence screenshot is missing")
        states.append(
            {
                "id": expected_id,
                "filename": filename,
                "sha256": _sha256_file(path),
                "width": int(raw.get("width") or 0),
                "height": int(raw.get("height") or 0),
                "demonstrated": raw.get("demonstrated") is True,
                "error": str(raw.get("error") or "")[:500],
            }
        )
    _write_contact_sheet_html(output_dir, states)
    contact_sheet = output_dir / "contact-sheet.png"
    session = f"modeldial-contact-{uuid4().hex[:12]}"
    capture_script = output_dir / "capture-contact-sheet.generated.js"
    capture_script.write_text(
        "async (page)=>{await page.setViewportSize({width:1600,height:1100});"
        f"await page.screenshot({{path:{json.dumps(str(contact_sheet))},fullPage:true,animations:'disabled'}});"
        "return {ok:true}}\n",
        encoding="utf-8",
    )
    with _serve_directory(output_dir) as root_url:
        contact_url = root_url.rsplit("/", 1)[0] + "/contact-sheet.html"
        try:
            _run_cli(
                cli,
                session,
                ["open", contact_url, "--browser", browser],
                cwd=output_dir,
                timeout=timeout_seconds,
            )
            _run_code_json(
                cli,
                session,
                capture_script,
                cwd=output_dir,
                timeout=timeout_seconds,
            )
        finally:
            try:
                _run_cli(cli, session, ["close"], cwd=output_dir, timeout=20)
            except BaseException:
                pass
    if not contact_sheet.is_file() or contact_sheet.stat().st_size == 0:
        raise FrontendEvaluationError("visual contact sheet is missing")
    return {
        "schema_version": "frontend_visual_evidence_manifest_v1",
        "states": states,
        "contact_sheet": {
            "filename": contact_sheet.name,
            "sha256": _sha256_file(contact_sheet),
        },
    }


def _write_contact_sheet_html(output_dir: Path, states: list[dict[str, Any]]) -> None:
    columns: list[list[str]] = [[], []]
    column_heights = [0.0, 0.0]
    for state in states:
        label = html.escape(str(state["id"]).replace("_", " ").title())
        status = "demonstrated" if state["demonstrated"] else "capture only"
        card = (
            "<figure>"
            f"<figcaption><strong>{label}</strong><span>{status}</span></figcaption>"
            f"<img src=\"{quote(str(state['filename']))}\" alt=\"{label}\">"
            "</figure>"
        )
        width = max(1, int(state.get("width") or 1))
        height = max(1, int(state.get("height") or 1))
        column_index = 0 if column_heights[0] <= column_heights[1] else 1
        columns[column_index].append(card)
        column_heights[column_index] += height / width
    column_markup = "".join(
        f"<section class='column'>{''.join(cards)}</section>" for cards in columns
    )
    document = (
        "<!doctype html><html><head><meta charset='utf-8'><style>"
        "*{box-sizing:border-box}body{margin:0;padding:28px;background:#080d0f;color:#f0f7f8;"
        "font:16px/1.4 system-ui,sans-serif}header{margin-bottom:20px}h1{margin:0;font-size:28px}"
        "p{margin:6px 0 0;color:#91a5aa}.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px;"
        "align-items:start}.column{display:flex;min-width:0;flex-direction:column;gap:20px}"
        "figure{margin:0;border:1px solid #26383e;border-radius:14px;overflow:hidden;"
        "background:#0e171a}figcaption{display:flex;justify-content:space-between;gap:12px;padding:12px 14px;"
        "border-bottom:1px solid #26383e}figcaption span{color:#91a5aa}img{display:block;width:100%;height:auto;"
        "background:#fff}</style></head><body>"
        "<header><h1>Anonymous Case Stream visual evidence</h1>"
        "<p>Standardized viewport and representative-state captures. Functional correctness is scored separately.</p>"
        f"</header><main class='grid'>{column_markup}</main></body></html>"
    )
    (output_dir / "contact-sheet.html").write_text(document, encoding="utf-8")


def _validate_contract(contract: Mapping[str, Any]) -> None:
    if contract.get("schema_version") != FRONTEND_PACKAGE_SCHEMA:
        raise FrontendEvaluationError("frontend score contract schema is unsupported")
    if contract.get("candidate_id") != FRONTEND_PACKAGE_ID:
        raise FrontendEvaluationError("frontend score contract identity changed")
    if (
        contract.get("total_points") != 100
        or contract.get("automated_points") != 85
        or contract.get("visual_judge_points") != 15
    ):
        raise FrontendEvaluationError("frontend score allocation changed")
    checks = _score_check_map(contract)
    if sum(int(item["points"]) for item in checks.values()) != 100:
        raise FrontendEvaluationError("frontend score contract does not total 100")


def _validate_rubric(rubric: Mapping[str, Any]) -> None:
    if rubric.get("schema_version") != FRONTEND_VISUAL_RUBRIC_SCHEMA:
        raise FrontendEvaluationError("frontend visual rubric schema is unsupported")
    if rubric.get("candidate_id") != FRONTEND_PACKAGE_ID:
        raise FrontendEvaluationError("frontend visual rubric identity changed")
    if rubric.get("judge_model") != FRONTEND_JUDGE_MODEL:
        raise FrontendEvaluationError("frontend visual judge model changed")
    if rubric.get("judge_reasoning_effort") != FRONTEND_JUDGE_EFFORT:
        raise FrontendEvaluationError("frontend visual judge effort changed")
    checks = rubric.get("checks")
    if not isinstance(checks, list) or [item.get("id") for item in checks] != [
        "V03",
        "V04",
        "V05",
        "V06",
    ]:
        raise FrontendEvaluationError("frontend visual rubric checks changed")
    if sum(int(item.get("max_points") or 0) for item in checks) != 15:
        raise FrontendEvaluationError("frontend visual rubric does not total 15")


def _score_check_map(contract: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    dimensions = contract.get("dimensions")
    if not isinstance(dimensions, list):
        raise FrontendEvaluationError("frontend score dimensions are invalid")
    checks: dict[str, dict[str, Any]] = {}
    for dimension in dimensions:
        if not isinstance(dimension, Mapping) or not isinstance(dimension.get("checks"), list):
            raise FrontendEvaluationError("frontend score dimension is invalid")
        dimension_id = str(dimension.get("id") or "")
        for check in dimension["checks"]:
            if not isinstance(check, Mapping):
                raise FrontendEvaluationError("frontend score check is invalid")
            check_id = str(check.get("id") or "")
            if not check_id or check_id in checks:
                raise FrontendEvaluationError("frontend score check identity is invalid")
            points = check.get("points")
            if not isinstance(points, int) or isinstance(points, bool) or points < 1:
                raise FrontendEvaluationError("frontend score check points are invalid")
            checks[check_id] = {**check, "dimension_id": dimension_id}
    return checks


def _dimension_totals(
    contract: Mapping[str, Any],
    details: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    totals: dict[str, dict[str, int]] = {}
    for dimension in contract["dimensions"]:
        dimension_id = str(dimension["id"])
        items = [item for item in details if item["dimension_id"] == dimension_id]
        totals[dimension_id] = {
            "points": sum(int(item["points"]) for item in items),
            "max_points": int(dimension["points"]),
        }
    return totals


def _required_alias(value: str) -> str:
    alias = value.strip()
    if not re.fullmatch(r"F[0-9A-F]{12}", alias):
        raise FrontendEvaluationError("frontend blind alias is invalid")
    return alias


def _visual_judgment_json_schema() -> dict[str, Any]:
    check = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "id": {"type": "string", "enum": ["V03", "V04", "V05", "V06"]},
            "points": {"type": "integer", "minimum": 0, "maximum": 4},
            "evidence": {"type": "string", "minLength": 1},
        },
        "required": ["id", "points", "evidence"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_version": {
                "type": "string",
                "const": FRONTEND_VISUAL_JUDGMENT_SCHEMA,
            },
            "candidate_alias": {"type": "string", "pattern": "^F[0-9A-F]{12}$"},
            "checks": {
                "type": "array",
                "items": check,
                "minItems": 4,
                "maxItems": 4,
            },
            "total": {"type": "integer", "minimum": 0, "maximum": 15},
            "summary": {"type": "string", "minLength": 1},
        },
        "required": [
            "schema_version",
            "candidate_alias",
            "checks",
            "total",
            "summary",
        ],
    }


def _responses_output_text(payload: object) -> str:
    if not isinstance(payload, Mapping):
        raise FrontendEvaluationError("visual judge response is malformed")
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    output = payload.get("output")
    if not isinstance(output, list):
        raise FrontendEvaluationError("visual judge response has no output")
    texts: list[str] = []
    for item in output:
        if not isinstance(item, Mapping):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, Mapping):
                continue
            value = part.get("text")
            if isinstance(value, str) and value:
                texts.append(value)
    text = "".join(texts).strip()
    if not text:
        raise FrontendEvaluationError("visual judge response has no output text")
    return text


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FrontendEvaluationError(f"invalid frontend JSON: {path.name}") from error
    if not isinstance(value, dict):
        raise FrontendEvaluationError(f"frontend JSON must be an object: {path.name}")
    return value


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_payload(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


__all__ = [
    "DEFAULT_BROWSER_TIMEOUT_SECONDS",
    "FRONTEND_JUDGE_EFFORT",
    "FRONTEND_JUDGE_MODEL",
    "FRONTEND_PACKAGE_ID",
    "FRONTEND_VISUAL_JUDGMENT_SCHEMA",
    "FrontendEvaluationError",
    "FrontendQuestionPackage",
    "apply_automatic_frontend_points",
    "build_visual_judge_prompt",
    "default_frontend_question_root",
    "load_frontend_question",
    "merge_visual_judgment",
    "normalize_frontend_html",
    "request_visual_judgment",
    "resolve_playwright_cli",
    "score_frontend_html",
    "validate_visual_judgment",
]
