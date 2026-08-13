from __future__ import annotations

import argparse
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
from typing import Iterator

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanner.config_store import ConfigStore
from scanner.history_store import HistoryStore
from scanner.models import AppConfig, ConnectionConfig, ModelCandidateConfig


EXPECTED_REFERENCE_URL = "https://reference.modeldial.com/reference-snapshots"
EXPECTED_UPDATE_FEED_URL = "https://updates.modeldial.com/macos/preview/appcast.xml"
EXPECTED_UPDATE_PUBLIC_KEY = "maaLn09C7fDPrHIh3Hxr6NYjGrj1CNQPzKUp7DEKID0="
SYNTHETIC_SECRET_REFERENCE = "keychain:com.modeldial.acceptance:synthetic"
SYNTHETIC_SECRET_VALUE = "modeldial-acceptance-secret"
MANUAL_BOUNDARIES = (
    {
        "id": "new_user_keychain_and_ui",
        "status": "manual_required",
        "label": "新 macOS 用户的 Keychain 授权与真实 SwiftUI 点击",
    },
    {
        "id": "gatekeeper_quarantine",
        "status": "manual_required",
        "label": "Safari 下载后的 quarantine、Gatekeeper 与首次放行",
    },
    {
        "id": "real_api_smoke",
        "status": "manual_required",
        "label": "真实 API 的一次最小请求与服务端账单核对",
    },
)


class AcceptanceError(RuntimeError):
    pass


def build_acceptance_config(base_url: str) -> AppConfig:
    config = AppConfig.first_run()
    for source in config.model_ingress.sources:
        source.enabled = source.id == "custom_endpoint"
    for connection in config.model_ingress.connections:
        connection.enabled = False
        for candidate in connection.model_candidates:
            candidate.enabled = False

    candidates = [
        ModelCandidateConfig(
            id=f"acceptance-endpoint:{model_id}:low",
            connection_id="acceptance-endpoint",
            model_id=model_id,
            display_name=f"{model_id} Low",
            family_id=model_id,
            variant_id="low",
            enabled=True,
            scan_profile="low",
            capabilities=["reasoning"],
        )
        for model_id in ("gpt-5.6-luna", "gpt-5.6-terra")
    ]
    config.model_ingress.connections.append(
        ConnectionConfig(
            id="acceptance-endpoint",
            source_id="custom_endpoint",
            name="Acceptance endpoint",
            enabled=True,
            api_format="openai_chat_completions",
            provider_preset="custom",
            base_url=base_url,
            api_key_ref=SYNTHETIC_SECRET_REFERENCE,
            last_test_status="ok",
            last_test_at="2026-08-11T00:00:00Z",
            last_test_message="Acceptance fixture",
            model_candidates=candidates,
        )
    )
    config.system.use_mock_results = False
    return config


def validate_preview(payload: dict[str, object]) -> dict[str, object]:
    candidate_ids = payload.get("effective_candidate_ids")
    if payload.get("valid") is not True:
        raise AcceptanceError(
            f"quick preview is invalid: {payload.get('reason') or 'unknown'}"
        )
    if not isinstance(candidate_ids, list) or len(candidate_ids) != 2:
        raise AcceptanceError("quick preview must select exactly two candidates")
    if payload.get("total_evaluations") != 10:
        raise AcceptanceError("quick preview must contain 10 evaluations")
    return {"candidate_count": 2, "evaluation_count": 10}


def validate_snapshot(payload: dict[str, object]) -> dict[str, object]:
    dashboard = payload.get("dashboard")
    if not isinstance(dashboard, dict):
        raise AcceptanceError("snapshot dashboard is missing")
    metadata = dashboard.get("run_metadata")
    if not isinstance(metadata, dict) or metadata.get("is_complete_regular_round") is not True:
        raise AcceptanceError("snapshot is not a complete regular round")
    rows = dashboard.get("leaderboard")
    if not isinstance(rows, list) or len(rows) != 2:
        raise AcceptanceError("snapshot must contain exactly two leaderboard rows")
    route_statuses: list[str] = []
    question_count = 0
    for row in rows:
        if not isinstance(row, dict):
            raise AcceptanceError("snapshot leaderboard row is malformed")
        route_status = str(row.get("route_identity_status") or "")
        route_statuses.append(route_status)
        if route_status != "matched":
            raise AcceptanceError("snapshot route evidence must be matched")
        if row.get("is_current_pack_comparable") is not True:
            raise AcceptanceError("snapshot row must be current-pack comparable")
        if row.get("is_current_run_eligible") is not True:
            raise AcceptanceError("snapshot row must be current-run eligible")
        completed = row.get("question_completed")
        if not isinstance(completed, int) or completed != 5:
            raise AcceptanceError("snapshot row must contain five completed questions")
        question_count += completed
    comparisons = dashboard.get("pairwise_comparisons")
    if not isinstance(comparisons, list) or len(comparisons) != 2:
        raise AcceptanceError("snapshot must contain both ordered candidate comparisons")
    candidate_ids = {str(row.get("candidate_id") or "") for row in rows}
    pair_ids = {
        (
            str(item.get("baseline_candidate_id") or ""),
            str(item.get("candidate_id") or ""),
        )
        for item in comparisons
        if isinstance(item, dict)
    }
    expected_pairs = {
        (baseline_id, candidate_id)
        for baseline_id in candidate_ids
        for candidate_id in candidate_ids
        if baseline_id != candidate_id
    }
    if pair_ids != expected_pairs:
        raise AcceptanceError("snapshot candidate comparisons are incomplete")
    return {
        "candidate_count": len(rows),
        "question_count": question_count,
        "pairwise_count": len(comparisons),
        "route_statuses": route_statuses,
    }


class _EndpointState:
    def __init__(self, q1_answer: str) -> None:
        self.q1_answer = q1_answer
        self.request_count = 0
        self.authorization_present = True
        self.unexpected_prompt_count = 0
        self.request_kinds: dict[str, int] = {}
        self._lock = threading.Lock()

    def record(
        self,
        *,
        authorization_ok: bool,
        expected_prompt: bool,
        request_kind: str,
    ) -> None:
        with self._lock:
            self.request_count += 1
            self.request_kinds[request_kind] = self.request_kinds.get(request_kind, 0) + 1
            self.authorization_present = self.authorization_present and authorization_ok
            if not expected_prompt:
                self.unexpected_prompt_count += 1


def _endpoint_handler(state: _EndpointState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path != "/v1/chat/completions":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length") or "0")
            request = json.loads(self.rfile.read(length).decode("utf-8"))
            messages = request.get("messages")
            prompt = ""
            if isinstance(messages, list) and messages and isinstance(messages[0], dict):
                prompt = str(messages[0].get("content") or "")
            answer, expected_prompt = _acceptance_answer(prompt, state.q1_answer)
            state.record(
                authorization_ok=(
                    self.headers.get("Authorization")
                    == f"Bearer {SYNTHETIC_SECRET_VALUE}"
                ),
                expected_prompt=expected_prompt,
                request_kind=_prompt_kind(prompt),
            )
            self._send_json(
                {
                    "choices": [
                        {"message": {"role": "assistant", "content": answer}}
                    ],
                    "usage": {
                        "prompt_tokens": 128,
                        "completion_tokens": 32,
                        "completion_tokens_details": {"reasoning_tokens": 24},
                    },
                }
            )

        def log_message(self, *_: object) -> None:
            return

        def _send_json(self, payload: dict[str, object]) -> None:
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return Handler


def _acceptance_answer(prompt: str, q1_answer: str) -> tuple[str, bool]:
    if prompt == "Reply with only OK.":
        return "OK", True
    if "session-bundle system" in prompt and '"op": "replay"' in prompt:
        return q1_answer, True
    if "black bag containing candies" in prompt:
        return "21", True
    markers = (
        "constructing counterexamples for a retry planner",
        "constructing compact scenarios that expose incorrect audit",
        "designing compact regression scenarios",
        "designing regression tests for a function named `run_scan`",
    )
    if any(marker in prompt for marker in markers):
        return "{}", True
    return "{}", False


def _prompt_kind(prompt: str) -> str:
    if prompt == "Reply with only OK.":
        return "connection_probe"
    if "session-bundle system" in prompt and '"op": "replay"' in prompt:
        return "session_bundle"
    if "black bag containing candies" in prompt:
        return "numeric_reasoning"
    markers = {
        "retry_planner": "constructing counterexamples for a retry planner",
        "audit": "constructing compact scenarios that expose incorrect audit",
        "regression_scenarios": "designing compact regression scenarios",
        "run_scan_tests": "designing regression tests for a function named `run_scan`",
    }
    for kind, marker in markers.items():
        if marker in prompt:
            return kind
    return "unknown"


@contextmanager
def _local_endpoint() -> Iterator[tuple[ThreadingHTTPServer, _EndpointState]]:
    q1_answer = (
        ROOT / "tests" / "fixtures" / "session_bundle_scenario_gold.json"
    ).read_text(encoding="utf-8")
    state = _EndpointState(q1_answer)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _endpoint_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _clean_environment(
    *,
    home: Path,
    data_dir: Path,
    backend_root: Path,
) -> dict[str, str]:
    return {
        "PATH": os.environ.get(
            "PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        ),
        "HOME": str(home),
        "TMPDIR": str(home / "tmp"),
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
        "MODELDIAL_BACKEND_ROOT": str(backend_root),
        "MODELDIAL_DATA_DIR": str(data_dir),
    }


def _run(
    command: list[str],
    *,
    env: dict[str, str],
    input_text: str | None = None,
    timeout: int = 360,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if completed.returncode != 0:
        stderr = _redact(completed.stderr.strip())[-800:]
        raise AcceptanceError(
            f"command failed with status {completed.returncode}: {stderr or 'no stderr'}"
        )
    return completed


def _decode_json(output: str, label: str) -> dict[str, object]:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as error:
        raise AcceptanceError(f"{label} did not return valid JSON") from error
    if not isinstance(payload, dict):
        raise AcceptanceError(f"{label} did not return a JSON object")
    return payload


def _verify_bundle(app_path: Path) -> dict[str, object]:
    info_path = app_path / "Contents" / "Info.plist"
    backend = app_path / "Contents" / "Resources" / "Backend" / "Runtime" / "modeldial-backend"
    if not info_path.is_file() or not os.access(backend, os.X_OK):
        raise AcceptanceError("candidate app or frozen backend is missing")
    with info_path.open("rb") as handle:
        info = plistlib.load(handle)
    if info.get("ModelDialReferenceSnapshotURL") != EXPECTED_REFERENCE_URL:
        raise AcceptanceError("candidate app does not contain the official Radar URL")
    if info.get("SUFeedURL") != EXPECTED_UPDATE_FEED_URL:
        raise AcceptanceError("candidate app does not contain the preview update feed")
    if info.get("SUPublicEDKey") != EXPECTED_UPDATE_PUBLIC_KEY:
        raise AcceptanceError("candidate app does not contain the preview update public key")

    compatibility = _run(
        [
            str(ROOT / "build-support" / "verify-macos-bundle-compatibility.sh"),
            str(app_path),
            "13.0",
        ],
        env=_tool_environment(),
    )
    signing = _run(
        [
            str(ROOT / "build-support" / "verify-adhoc-signing-policy.sh"),
            str(app_path),
        ],
        env=_tool_environment(),
    )
    return {
        "version": str(info.get("CFBundleShortVersionString") or ""),
        "build": str(info.get("CFBundleVersion") or ""),
        "reference_snapshot_url": EXPECTED_REFERENCE_URL,
        "update_feed_url": EXPECTED_UPDATE_FEED_URL,
        "update_public_key_configured": True,
        "compatibility": compatibility.stdout.strip().splitlines()[-1],
        "signing": signing.stdout.strip().splitlines()[-1],
    }


def _tool_environment(*, home: Path | None = None) -> dict[str, str]:
    return {
        "PATH": os.environ.get(
            "PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        ),
        "HOME": str(home or Path.home()),
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
    }


def _verify_ui_state_contract(home: Path) -> dict[str, object]:
    home.mkdir(parents=True, exist_ok=True)
    completed = _run(
        [
            sys.executable,
            "-m",
            "unittest",
            "tests.test_app_session_bridge_ownership.AppSessionBridgeOwnershipTest.test_settings_commands_publish_only_through_app_session_store",
            "-q",
        ],
        env=_tool_environment(home=home),
        timeout=180,
    )
    if "OK" not in completed.stderr and "OK" not in completed.stdout:
        raise AcceptanceError("Swift source-selection contract did not report success")
    return {"contract": "Swift AppSessionStore source switching passed"}


def _verify_local_endpoint(app_path: Path, workspace: Path) -> dict[str, object]:
    backend_root = app_path / "Contents" / "Resources" / "Backend"
    backend = backend_root / "Runtime" / "modeldial-backend"
    data_dir = workspace / "data"
    home = workspace / "home"
    (home / "tmp").mkdir(parents=True)
    data_dir.mkdir()
    config_path = data_dir / "config.json"
    history_path = data_dir / "history.jsonl"
    active_run_path = data_dir / "active_run.json"

    with _local_endpoint() as (server, endpoint_state):
        config = build_acceptance_config(
            f"http://127.0.0.1:{server.server_port}/v1"
        )
        ConfigStore(config_path, first_run_defaults=True).save(config)
        env = _clean_environment(
            home=home,
            data_dir=data_dir,
            backend_root=backend_root,
        )
        common = [
            "--config-path",
            str(config_path),
            "--history-path",
            str(history_path),
            "--active-run-path",
            str(active_run_path),
        ]
        secret_input = json.dumps(
            {SYNTHETIC_SECRET_REFERENCE: SYNTHETIC_SECRET_VALUE}
        )
        connection = _decode_json(
            _run(
                [
                    str(backend),
                    "test-connection",
                    *common,
                    "--connection-id",
                    "acceptance-endpoint",
                    "--model-id",
                    "gpt-5.6-luna",
                    "--secret-stdin",
                ],
                env=env,
                input_text=secret_input,
                timeout=60,
            ).stdout,
            "connection test",
        )
        if connection.get("ok") is not True:
            raise AcceptanceError("local endpoint connection test failed")

        preview = _decode_json(
            _run(
                [
                    str(backend),
                    "preview-scan",
                    *common,
                    "--selection-mode",
                    "regular",
                    "--evaluation-profile-id",
                    "quick",
                ],
                env=env,
                timeout=60,
            ).stdout,
            "quick preview",
        )
        preview_detail = validate_preview(preview)

        scan = _run(
            [
                str(backend),
                "scan",
                *common,
                "--selection-mode",
                "regular",
                "--evaluation-profile-id",
                "full",
                "--secret-stdin",
            ],
            env=env,
            input_text=secret_input,
        )
        events = [
            json.loads(line)
            for line in scan.stdout.splitlines()
            if line.strip()
        ]
        if not events or events[0].get("type") != "scan.started":
            raise AcceptanceError("local scan did not emit scan.started")
        if events[-1].get("type") != "scan.finished":
            raise AcceptanceError("local scan did not finish successfully")

        snapshot = _decode_json(
            _run(
                [str(backend), "snapshot", *common],
                env=env,
                timeout=60,
            ).stdout,
            "post-scan snapshot",
        )
        snapshot_detail = validate_snapshot(snapshot)
        history_count = len(HistoryStore(history_path).load_all())
        if history_count != 10:
            raise AcceptanceError("local scan must persist exactly 10 results")
        expected_request_kinds = {
            "connection_probe": 2,
            "session_bundle": 2,
            "retry_planner": 2,
            "audit": 2,
            "regression_scenarios": 2,
            "run_scan_tests": 2,
        }
        if endpoint_state.request_count != 12:
            raise AcceptanceError(
                "local endpoint must receive two probes and 10 scans; "
                f"received {endpoint_state.request_count} requests "
                f"({endpoint_state.request_kinds})"
            )
        if endpoint_state.request_kinds != expected_request_kinds:
            raise AcceptanceError(
                "local endpoint request distribution is unexpected: "
                f"{endpoint_state.request_kinds}"
            )
        if not endpoint_state.authorization_present:
            raise AcceptanceError("synthetic stdin secret was not used by every request")
        if endpoint_state.unexpected_prompt_count:
            raise AcceptanceError("local endpoint received an unknown question prompt")

    return {
        "preview": preview_detail,
        "snapshot": snapshot_detail,
        "history_count": history_count,
        "request_count": endpoint_state.request_count,
        "authorization_present": endpoint_state.authorization_present,
        "real_model_requests": 0,
    }


def _redact(value: str) -> str:
    return (
        value.replace(SYNTHETIC_SECRET_VALUE, "***REDACTED***")
        .replace(str(ROOT), ".")
        .replace(str(Path.home()), "~")
    )


def render_text_report(report: dict[str, object]) -> str:
    lines = [
        f"ModelDial 首启自动验收：{str(report.get('status', 'unknown')).upper()}",
        "",
        "自动检查",
    ]
    for check in report.get("checks", []):  # type: ignore[assignment]
        if not isinstance(check, dict):
            continue
        lines.append(
            f"- [{str(check.get('status', 'unknown')).upper()}] {check.get('id', 'unknown')}"
        )
    lines.extend(["", "人工验收边界"])
    for boundary in report.get("manual_boundaries", []):  # type: ignore[assignment]
        if not isinstance(boundary, dict):
            continue
        lines.append(f"- [MANUAL] {boundary.get('label', boundary.get('id'))}")
    privacy = report.get("privacy")
    if isinstance(privacy, dict):
        lines.extend(
            [
                "",
                "隐私与费用",
                f"- 真实模型请求：{privacy.get('real_model_requests', 'unknown')}",
                f"- Keychain 读取：{privacy.get('keychain_reads', 'unknown')}",
                f"- 用户正式数据路径读取：{privacy.get('user_data_paths', 'unknown')}",
            ]
        )
    return "\n".join(lines) + "\n"


def run_acceptance(app_path: Path) -> dict[str, object]:
    started_at = datetime.now(timezone.utc)
    report: dict[str, object] = {
        "schema_version": 1,
        "status": "running",
        "started_at": started_at.isoformat(),
        "app": str(app_path.relative_to(ROOT)) if app_path.is_relative_to(ROOT) else app_path.name,
        "checks": [],
        "manual_boundaries": list(MANUAL_BOUNDARIES),
        "privacy": {
            "real_model_requests": 0,
            "keychain_reads": 0,
            "user_data_paths": 0,
        },
    }
    checks = report["checks"]
    assert isinstance(checks, list)

    def check(check_id: str, operation: Callable[[], dict[str, object]]) -> None:
        began = time.monotonic()
        try:
            detail = operation()
        except Exception as error:
            checks.append(
                {
                    "id": check_id,
                    "status": "failed",
                    "elapsed_seconds": round(time.monotonic() - began, 3),
                    "error": _redact(str(error)),
                }
            )
            raise
        checks.append(
            {
                "id": check_id,
                "status": "passed",
                "elapsed_seconds": round(time.monotonic() - began, 3),
                "detail": detail,
            }
        )

    try:
        check("candidate_bundle", lambda: _verify_bundle(app_path))
        with TemporaryDirectory(prefix="modeldial-first-run-acceptance-") as temp_dir:
            workspace = Path(temp_dir)
            check(
                "local_endpoint_scan",
                lambda: _verify_local_endpoint(app_path, workspace),
            )
            check(
                "swift_source_switching",
                lambda: _verify_ui_state_contract(workspace / "swift-home"),
            )
        report["status"] = "passed"
    except Exception:
        report["status"] = "failed"
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    report["elapsed_seconds"] = round(
        (datetime.now(timezone.utc) - started_at).total_seconds(), 3
    )
    serialized = json.dumps(report, ensure_ascii=False)
    if SYNTHETIC_SECRET_VALUE in serialized:
        raise AcceptanceError("acceptance report contains the synthetic secret")
    return report


def _write_reports(report: dict[str, object], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    json_path = output_dir / f"first-run-acceptance-{timestamp}.json"
    text_path = output_dir / f"first-run-acceptance-{timestamp}.txt"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    text_path.write_text(render_text_report(report), encoding="utf-8")
    return json_path, text_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run isolated first-run acceptance against a built ModelDial app."
    )
    parser.add_argument(
        "--app",
        type=Path,
        default=ROOT / "build" / "modeldial-candidate.app",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "artifacts" / "first-run-acceptance",
    )
    args = parser.parse_args()

    report = run_acceptance(args.app.resolve())
    json_path, text_path = _write_reports(report, args.output_dir.resolve())
    print(render_text_report(report), end="")
    print(f"JSON report: {json_path}")
    print(f"Text report: {text_path}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
