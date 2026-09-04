from __future__ import annotations

import time
from datetime import datetime, timezone
import os
import sys
from uuid import uuid4

from .endpoint_client import EndpointError, run_endpoint_request_isolated
from .claude_code_client import ClaudeCodeError, run_claude_code_prompt
from .codex_runtime import CodexPromptExecutionError, preview, run_codex_prompt
from .costing import ReferenceCostEstimate, estimate_reference_cost
from .graders import GradeResult, grade_answer
from .grok_build_client import GrokBuildError, run_grok_build_prompt
from .models import ResolvedScanTarget, ScanResult, TargetConfig
from .question_bank import QuestionSpec
from .raw_answer_evidence import capture_raw_answer
from .route_identity import build_route_fingerprint
from .secret_store import SecretStore


def _log(message: str) -> None:
    if os.environ.get("MODELDIAL_DEBUG_LOG") != "1":
        return
    try:
        print(f"[runner] {message}", file=sys.stderr, flush=True)
    except (BrokenPipeError, OSError, ValueError):
        pass


def _grade_diagnostics(grade_result: GradeResult) -> dict[str, object]:
    diagnostics = dict(grade_result.diagnostics or {})
    if grade_result.score is not None and grade_result.max_score is not None:
        diagnostics.setdefault("semantic_passed", grade_result.score)
        diagnostics.setdefault("semantic_total", grade_result.max_score)
    if grade_result.failure_details:
        diagnostics.setdefault("failure_details", grade_result.failure_details)
    return diagnostics


def _execution_failure_diagnostics(
    question: QuestionSpec,
    error_message: str,
    execution_trace: dict[str, object],
) -> dict[str, object]:
    max_score = int(question.grader.payload.get("max_score") or 1)
    terminal_state = str(execution_trace.get("terminal_state") or "")
    endpoint_category = str(execution_trace.get("endpoint_error_category") or "")
    status = (
        "timeout"
        if (
            "timeout" in error_message.lower()
            or "timed out" in error_message.lower()
            or terminal_state.startswith("timeout")
            or endpoint_category == "timeout"
        )
        else "runtime_error"
    )
    return {
        "status": status,
        "semantic_passed": 0,
        "semantic_total": max_score,
        "failure_summary": error_message,
        "score_details": [],
    }


def _endpoint_execution_trace(
    evaluation_id: str,
    timeout_seconds: int,
    *,
    terminal_state: str,
    started_at_utc: str,
    response_id: str | None = None,
    response_model: str | None = None,
    stop_reason: str | None = None,
    error_category: str | None = None,
    error_diagnostics: dict[str, object] | None = None,
) -> dict[str, object]:
    trace: dict[str, object] = {
        "evaluation_id": evaluation_id,
        "correlation_mode": "request_header",
        "request_header": "X-Modeldial-Evaluation-ID",
        "started_at_utc": started_at_utc,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "timeout_seconds": timeout_seconds,
        "terminal_state": terminal_state,
    }
    if response_id:
        trace["response_id"] = response_id
    if response_model:
        trace["response_model"] = response_model
    if stop_reason:
        trace["stop_reason"] = stop_reason
    if error_category:
        trace["endpoint_error_category"] = error_category
    if error_diagnostics:
        trace["endpoint_diagnostics"] = dict(error_diagnostics)
    return trace


def run_target(
    target: ResolvedScanTarget | TargetConfig,
    question: QuestionSpec,
    use_mock_results: bool = True,
    *,
    run_id: str = "run-default",
    phase: str = "scan",
    attempt_index: int = 1,
    execution_timeout_seconds: int = 300,
) -> ScanResult:
    resolved_target = _resolve_target(target)
    if use_mock_results:
        return _run_mock_target(
            resolved_target,
            question,
            run_id=run_id,
            phase=phase,
            attempt_index=attempt_index,
        )
    return _run_live_target(
        resolved_target,
        question,
        run_id=run_id,
        phase=phase,
        attempt_index=attempt_index,
        execution_timeout_seconds=execution_timeout_seconds,
    )


def _resolve_target(target: ResolvedScanTarget | TargetConfig) -> ResolvedScanTarget:
    if isinstance(target, ResolvedScanTarget):
        return target
    return ResolvedScanTarget(
        candidate_id=f"legacy-local-default:{target.model}:{target.effort}",
        source_id="legacy_local",
        connection_id="legacy-local-default",
        model_id=target.model,
        scan_profile=target.effort,
        display_name=f"{target.model} / {target.effort}",
    )


def _run_mock_target(
    target: ResolvedScanTarget,
    question: QuestionSpec,
    *,
    run_id: str,
    phase: str,
    attempt_index: int,
) -> ScanResult:
    started_at = _timestamp()
    evaluation_id = f"md-eval-{uuid4().hex}"
    preset = {
        ("gpt-5.4", "medium"): ("21", 980),
        ("gpt-5.4", "high"): ("21", 516),
        ("gpt-5.4", "xhigh"): ("21", 432),
        ("gpt-5.5", "medium"): ("20", 1180),
        ("gpt-5.5", "high"): ("20", 388),
        ("gpt-5.5", "xhigh"): ("21", 612),
    }.get((target.model_id, target.scan_profile), ("21", 400))
    answer_preview, reasoning_tokens = preset
    configured_max_score = int(question.grader.payload.get("max_score") or 10)
    configured_test_suite = str(question.grader.payload.get("test_suite") or "")
    if question.grader.kind == "session_bundle_test_design":
        answer_preview = '{"tests":[{"name":"mock","steps":[{"op":"save","target":"missing"}]}]}'
        grade_result = GradeResult(
            ok=True,
            summary=f"{configured_test_suite} {configured_max_score}/{configured_max_score}",
            score=configured_max_score,
            max_score=configured_max_score,
            diagnostics={
                "semantic_passed": configured_max_score,
                "semantic_total": configured_max_score,
                "status": "passed",
                "survived_mutants": [],
            },
        )
    elif question.grader.kind == "black_box_regression_proof":
        answer_preview = "mock black-box regression patch"
        grade_result = GradeResult(
            ok=True,
            summary=f"{configured_test_suite} {configured_max_score}/{configured_max_score}",
            score=configured_max_score,
            max_score=configured_max_score,
            diagnostics={
                "patch_format_ok": True,
                "patch_applies": True,
                "semantic_passed": configured_max_score,
                "semantic_total": configured_max_score,
                "status": "passed",
                "survived_mutants": [],
            },
        )
    elif question.grader.kind == "session_bundle_patch":
        answer_preview = "mock session bundle patch"
        grade_result = GradeResult(
            ok=True,
            summary="compact_session_repair_v1 10/10",
            score=10,
            max_score=10,
            diagnostics={
                "patch_format_ok": True,
                "patch_applies": True,
                "semantic_passed": 10,
                "semantic_total": 10,
                "status": "passed",
                "failed_cases": [],
            },
        )
    elif question.grader.kind == "retry_counterexample_design":
        test_suite = str(
            question.grader.payload.get("test_suite") or "retry_planner_mutants_v1"
        )
        answer_preview = '{"counterexamples":[{"name":"mock"}]}'
        grade_result = GradeResult(
            ok=True,
            summary=f"{test_suite} {configured_max_score}/{configured_max_score}",
            score=configured_max_score,
            max_score=configured_max_score,
            diagnostics={
                "semantic_passed": configured_max_score,
                "semantic_total": configured_max_score,
                "status": "passed",
                "survived_mutants": [],
            },
        )
    elif question.grader.kind == "cross_loop_singleflight_patch":
        answer_preview = "mock cross-loop single-flight patch"
        grade_result = GradeResult(
            ok=True,
            summary="cross_loop_singleflight_v2 10/10",
            score=10,
            max_score=10,
            diagnostics={
                "patch_format_ok": True,
                "patch_applies": True,
                "semantic_passed": 10,
                "semantic_total": 10,
                "status": "passed",
                "failed_cases": [],
            },
        )
    elif question.grader.kind == "scalar_cross_loop_flight_patch":
        answer_preview = "mock scalar cross-loop flight patch"
        grade_result = GradeResult(
            ok=True,
            summary="scalar_cross_loop_flight_v1 10/10",
            score=10,
            max_score=10,
            diagnostics={
                "patch_format_ok": True,
                "patch_applies": True,
                "semantic_passed": 10,
                "semantic_total": 10,
                "status": "passed",
                "failed_cases": [],
            },
        )
    elif question.grader.kind == "transaction_regression_design":
        answer_preview = '{"tests":[{"name":"mock","frames":[]}]}'
        grade_result = GradeResult(
            ok=True,
            summary=f"{configured_test_suite} {configured_max_score}/{configured_max_score}",
            score=configured_max_score,
            max_score=configured_max_score,
            diagnostics={
                "semantic_passed": configured_max_score,
                "semantic_total": configured_max_score,
                "status": "passed",
                "survived_mutants": [],
            },
        )
    elif question.grader.kind == "mutation_test_design":
        test_suite = str(
            question.grader.payload.get("test_suite") or "cache_regression_mutants"
        )
        answer_preview = '{"tests":[{"name":"mock","files":[],"cache":{},"params":{}}]}'
        grade_result = GradeResult(
            ok=True,
            summary=f"{test_suite} {configured_max_score}/{configured_max_score}",
            score=configured_max_score,
            max_score=configured_max_score,
            diagnostics={
                "test_suite": test_suite,
                "semantic_passed": configured_max_score,
                "semantic_total": configured_max_score,
                "status": "passed",
                "survived_mutants": [],
            },
        )
    elif question.grader.kind == "cache_propagation_certificate":
        answer_preview = '{"portfolios":[],"audit":[]}'
        grade_result = GradeResult(
            ok=True,
            summary=f"{configured_test_suite} {configured_max_score}/{configured_max_score}",
            score=configured_max_score,
            max_score=configured_max_score,
            diagnostics={
                "test_suite": configured_test_suite,
                "semantic_passed": configured_max_score,
                "semantic_total": configured_max_score,
                "status": "passed",
                "grade_state": "scored",
                "survived_mutants": [],
            },
        )
    elif question.grader.kind == "ci_adversarial_audit":
        test_suite = str(
            question.grader.payload.get("test_suite") or "ci_adversarial_audit_v1"
        )
        answer_preview = '{"scenarios":[{"name":"mock"}]}'
        grade_result = GradeResult(
            ok=True,
            summary=f"{test_suite} {configured_max_score}/{configured_max_score}",
            score=configured_max_score,
            max_score=configured_max_score,
            diagnostics={
                "test_suite": test_suite,
                "semantic_passed": configured_max_score,
                "semantic_total": configured_max_score,
                "status": "passed",
                "survived_mutants": [],
            },
        )
    elif question.grader.kind == "ci_optimality_certificate":
        answer_preview = '{"comparisons":{},"counterfactuals":{}}'
        grade_result = GradeResult(
            ok=True,
            summary="ci_optimality_certificate_v1 10/10",
            score=10,
            max_score=10,
            diagnostics={
                "semantic_passed": 10,
                "semantic_total": 10,
                "status": "passed",
                "failed_components": [],
            },
        )
    else:
        grade_result = grade_answer(answer_preview, question.grader.payload)
    execution_trace = {
        "evaluation_id": evaluation_id,
        "correlation_mode": "mock",
        "terminal_state": "completed_response",
        "route_fingerprint": _target_route_fingerprint(target),
    }
    raw_answer_sha256 = capture_raw_answer(
        run_id=run_id,
        evaluation_id=evaluation_id,
        candidate_id=target.candidate_id,
        question_id=question.id,
        attempt_index=attempt_index,
        answer=answer_preview,
    )
    if raw_answer_sha256:
        execution_trace["raw_answer_sha256"] = raw_answer_sha256
    return ScanResult(
        candidate_id=target.candidate_id,
        run_id=run_id,
        phase=phase,
        model=target.model_id,
        effort=target.scan_profile,
        question_id=question.id,
        question_title=question.title,
        capability_id=question.capability_id,
        capability_label=question.capability_label,
        detail_label=question.detail_label,
        grader_kind=question.grader.kind,
        attempt_index=attempt_index,
        started_at=started_at,
        elapsed_seconds=0.2,
        source_mode="mock",
        answer_ok=grade_result.ok,
        answer_preview=answer_preview,
        scorer_reason=grade_result.summary,
        scorer_diagnostics=_grade_diagnostics(grade_result),
        expected_summary=_expected_summary(question.grader.payload),
        actual_summary=_actual_summary(answer_preview),
        input_tokens=128,
        output_tokens=32,
        reasoning_tokens=reasoning_tokens,
        reasoning_tokens_supported=target.reasoning_tokens_supported,
        evaluation_id=evaluation_id,
        execution_trace=execution_trace,
    )


def _run_live_target(
    target: ResolvedScanTarget,
    question: QuestionSpec,
    *,
    run_id: str,
    phase: str,
    attempt_index: int,
    execution_timeout_seconds: int,
) -> ScanResult:
    started_at = _timestamp()
    start = time.perf_counter()
    execution_started_at_utc = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    source_mode = "api" if target.connection_mode == "api" else "live"
    local_correlation_mode = {
        "grok_local": "grok_build_cli_json",
        "claude_local": "claude_code_cli_stream_json",
    }.get(target.source_id, "local_timing_fingerprint")
    evaluation_id = f"md-eval-{uuid4().hex}"
    route_fingerprint = _target_route_fingerprint(target)
    execution_trace: dict[str, object] = {
        "evaluation_id": evaluation_id,
        "correlation_mode": "request_header"
        if source_mode == "api"
        else local_correlation_mode,
        "timeout_seconds": execution_timeout_seconds,
        "started_at_utc": execution_started_at_utc,
        "terminal_state": "runner_started",
        "route_fingerprint": route_fingerprint,
    }
    _log(
        f"live.start target={target.model_id}/{target.scan_profile} question={question.id} "
        f"evaluation_id={evaluation_id}"
    )
    cache_write_input_tokens: int | None = None
    try:
        if target.connection_mode == "api":
            api_key = SecretStore().resolve(target.api_key_ref)
            endpoint_result = run_endpoint_request_isolated(
                target,
                question.prompt,
                api_key,
                timeout_seconds=execution_timeout_seconds,
                evaluation_id=evaluation_id,
            )
            text = endpoint_result.text
            input_tokens = endpoint_result.input_tokens
            cached_input_tokens = endpoint_result.cached_input_tokens
            cache_write_input_tokens = endpoint_result.cache_write_input_tokens
            output_tokens = endpoint_result.output_tokens
            reasoning_tokens = endpoint_result.reasoning_tokens
            execution_trace = _endpoint_execution_trace(
                evaluation_id,
                execution_timeout_seconds,
                terminal_state="completed_response",
                started_at_utc=execution_started_at_utc,
                response_id=endpoint_result.response_id,
                response_model=endpoint_result.response_model,
                stop_reason=endpoint_result.stop_reason,
            )
            cost_estimate = estimate_reference_cost(
                target.model_id,
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                cache_write_input_tokens=cache_write_input_tokens,
                output_tokens=output_tokens,
                reasoning_output_tokens=reasoning_tokens,
            )
        elif target.source_id == "grok_local":
            grok_result = run_grok_build_prompt(
                question.prompt,
                target.model_id,
                target.scan_profile,
                timeout_seconds=execution_timeout_seconds,
                evaluation_id=evaluation_id,
            )
            text = grok_result.text
            input_tokens = grok_result.input_tokens
            cached_input_tokens = grok_result.cached_input_tokens
            output_tokens = grok_result.output_tokens
            reasoning_tokens = grok_result.reasoning_tokens
            execution_trace = grok_result.execution_trace
            cost_estimate = ReferenceCostEstimate(
                grok_result.total_cost_usd,
                "observed" if grok_result.total_cost_usd is not None else "unavailable",
                "grok_build_cli",
            )
        elif target.source_id == "claude_local":
            claude_result = run_claude_code_prompt(
                question.prompt,
                target.model_id,
                target.scan_profile,
                timeout_seconds=execution_timeout_seconds,
                evaluation_id=evaluation_id,
            )
            text = claude_result.text
            input_tokens = claude_result.input_tokens
            cached_input_tokens = claude_result.cached_input_tokens
            output_tokens = claude_result.output_tokens
            reasoning_tokens = None
            execution_trace = claude_result.execution_trace
            cost_estimate = ReferenceCostEstimate(
                claude_result.total_cost_usd,
                "observed" if claude_result.total_cost_usd is not None else "unavailable",
                "claude_code_cli",
            )
        elif target.source_id in {"codex_local", "legacy_local"}:
            (
                text,
                input_tokens,
                cached_input_tokens,
                output_tokens,
                reasoning_tokens,
                execution_trace,
            ) = run_codex_prompt(
                question.prompt,
                target.model_id,
                target.scan_profile,
                timeout_seconds=execution_timeout_seconds,
                evaluation_id=evaluation_id,
                return_trace=True,
            )
            cost_estimate = estimate_reference_cost(
                target.model_id,
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
            )
        else:
            raise RuntimeError(f"unsupported local source: {target.source_id}")
        execution_trace = {
            **execution_trace,
            "route_fingerprint": route_fingerprint,
        }
        raw_answer_sha256 = capture_raw_answer(
            run_id=run_id,
            evaluation_id=evaluation_id,
            candidate_id=target.candidate_id,
            question_id=question.id,
            attempt_index=attempt_index,
            answer=text,
        )
        if raw_answer_sha256:
            execution_trace["raw_answer_sha256"] = raw_answer_sha256
        elapsed_seconds = time.perf_counter() - start
        grade_result = grade_answer(text, question.grader.payload)
        ok = grade_result.ok
        grade_diagnostics = _grade_diagnostics(grade_result)
        grader_error_message = None
        if grade_diagnostics.get("status") == "grader_unavailable":
            failure_summary = str(grade_diagnostics.get("failure_summary") or "unknown")
            grader_error_message = f"grader_unavailable: {failure_summary}"
        elif grade_result.score is None and grade_diagnostics.get("status") in {
            "format_error",
            "schema_error",
        }:
            grade_state = str(grade_diagnostics["status"])
            failure_summary = str(grade_diagnostics.get("failure_summary") or "unknown")
            grader_error_message = f"unscored_answer:{grade_state}: {failure_summary}"
        answer_preview = preview(text)
        _log(
            f"live.success target={target.model_id}/{target.scan_profile} "
            f"question={question.id} elapsed={elapsed_seconds:.3f} ok={ok} "
            f"evaluation_id={evaluation_id}"
        )
        return ScanResult(
            candidate_id=target.candidate_id,
            run_id=run_id,
            phase=phase,
            model=target.model_id,
            effort=target.scan_profile,
            question_id=question.id,
            question_title=question.title,
            capability_id=question.capability_id,
            capability_label=question.capability_label,
            detail_label=question.detail_label,
            grader_kind=question.grader.kind,
            attempt_index=attempt_index,
            started_at=started_at,
            elapsed_seconds=elapsed_seconds,
            source_mode=source_mode,
            answer_ok=ok,
            answer_preview=answer_preview,
            error_message=grader_error_message,
            scorer_reason=grade_result.summary,
            scorer_diagnostics=grade_diagnostics,
            expected_summary=_expected_summary(question.grader.payload),
            actual_summary=_actual_summary(answer_preview),
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            cache_write_input_tokens=cache_write_input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            reasoning_tokens_supported=target.reasoning_tokens_supported,
            reference_cost_usd=cost_estimate.usd,
            cost_status=cost_estimate.status,
            pricing_snapshot=cost_estimate.pricing_snapshot,
            evaluation_id=evaluation_id,
            execution_trace=execution_trace,
        )
    except Exception as exc:
        elapsed_seconds = time.perf_counter() - start
        if isinstance(exc, CodexPromptExecutionError):
            execution_trace = exc.execution_trace
        elif isinstance(exc, GrokBuildError):
            execution_trace = exc.execution_trace
        elif isinstance(exc, ClaudeCodeError):
            execution_trace = exc.execution_trace
        elif isinstance(exc, EndpointError):
            execution_trace = _endpoint_execution_trace(
                evaluation_id,
                execution_timeout_seconds,
                terminal_state="endpoint_error",
                started_at_utc=execution_started_at_utc,
                error_category=exc.category,
                error_diagnostics=exc.diagnostics,
            )
        else:
            execution_trace = {
                "evaluation_id": evaluation_id,
                "correlation_mode": "request_header"
                if source_mode == "api"
                else local_correlation_mode,
                "timeout_seconds": execution_timeout_seconds,
                "started_at_utc": execution_started_at_utc,
                "terminal_state": "runner_exception",
            }
        execution_trace = {
            **execution_trace,
            "route_fingerprint": route_fingerprint,
        }
        _log(
            f"live.error target={target.model_id}/{target.scan_profile} "
            f"question={question.id} elapsed={elapsed_seconds:.3f} error={exc} "
            f"evaluation_id={evaluation_id}"
        )
        return ScanResult(
            candidate_id=target.candidate_id,
            run_id=run_id,
            phase=phase,
            model=target.model_id,
            effort=target.scan_profile,
            question_id=question.id,
            question_title=question.title,
            capability_id=question.capability_id,
            capability_label=question.capability_label,
            detail_label=question.detail_label,
            grader_kind=question.grader.kind,
            attempt_index=attempt_index,
            started_at=started_at,
            elapsed_seconds=elapsed_seconds,
            source_mode=source_mode,
            answer_ok=False,
            answer_preview=f"ERROR: {exc}",
            scorer_reason=str(exc),
            scorer_diagnostics=_execution_failure_diagnostics(
                question,
                str(exc),
                execution_trace,
            ),
            expected_summary=_expected_summary(question.grader.payload),
            actual_summary=f"ERROR: {exc}",
            input_tokens=None,
            output_tokens=None,
            reasoning_tokens=None,
            reasoning_tokens_supported=target.reasoning_tokens_supported,
            error_message=str(exc),
            evaluation_id=evaluation_id,
            execution_trace=execution_trace,
        )


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _target_route_fingerprint(target: ResolvedScanTarget) -> str:
    return build_route_fingerprint(
        source_id=target.source_id,
        connection_id=target.connection_id,
        connection_mode=target.connection_mode,
        api_format=target.api_format,
        provider_preset=target.provider_preset,
        base_url=target.base_url,
        model_id=target.model_id,
        scan_profile=target.scan_profile,
    )


def _expected_summary(grader: dict[str, object]) -> str:
    kind = str(grader.get("kind") or "")
    if kind == "regex":
        return str(grader.get("pattern") or "")
    if kind == "json_exact":
        return _compact(str(json_dumps(grader.get("expected"))))
    if kind == "expression_24":
        numbers = ",".join(str(item) for item in grader.get("numbers", []))  # type: ignore[arg-type]
        return f"target={grader.get('target')}; numbers={numbers}"
    return kind


def _actual_summary(answer_preview: str) -> str:
    return _compact(answer_preview)


def _compact(value: str) -> str:
    return value if len(value) <= 120 else f"{value[:117]}..."


def json_dumps(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
