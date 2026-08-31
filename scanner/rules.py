from __future__ import annotations

from .models import RuleConfig, RuleEvaluation, ScanResult


ACTION_PRIORITY = {
    "ignore": 0,
    "warn": 1,
    "retry": 2,
    "fail": 3,
}

TRANSIENT_ENDPOINT_ERROR_CATEGORIES = frozenset(
    {"network_error", "rate_limited", "server_error", "timeout"}
)
TRANSIENT_PROCESS_TERMINAL_STATES = frozenset({"timeout_without_completed_turn"})


def is_grok_outbound_replay(result: ScanResult) -> bool:
    return (
        str(result.execution_trace.get("correlation_mode") or "")
        .strip()
        .lower()
        == "grok_outbound_replay"
    )


def is_transient_execution_error(result: ScanResult) -> bool:
    if not result.error_message:
        return False
    if is_grok_outbound_replay(result):
        return True
    terminal_state = str(
        result.execution_trace.get("terminal_state") or ""
    ).strip().lower()
    if terminal_state in TRANSIENT_PROCESS_TERMINAL_STATES:
        return True
    returncode = result.execution_trace.get("process_returncode")
    if (
        terminal_state == "process_error"
        and isinstance(returncode, int)
        and not isinstance(returncode, bool)
        and returncode < 0
    ):
        return True
    category = str(
        result.execution_trace.get("endpoint_error_category") or ""
    ).strip().lower()
    if category in TRANSIENT_ENDPOINT_ERROR_CATEGORIES:
        return True
    raw_markers = result.execution_trace.get("transport_markers")
    markers = (
        {str(marker).strip().lower() for marker in raw_markers}
        if isinstance(raw_markers, list)
        else set()
    )
    return bool(markers & {"network_error", "websocket_disconnected"})


def evaluate_result(
    result: ScanResult,
    rules: dict[str, RuleConfig],
    *,
    hard_timeout_retry_count: int = 0,
) -> RuleEvaluation:
    matched: list[str] = []
    matched_rules: list[RuleConfig] = []

    if result.reasoning_tokens == 516:
        _record_match("reason_tok_516", rules, matched, matched_rules)
    if not result.answer_ok:
        _record_match("wrong_answer", rules, matched, matched_rules)
    if result.reasoning_tokens is None and result.reasoning_tokens_supported:
        _record_match("missing_usage", rules, matched, matched_rules)
    hard_timeout = _is_hard_execution_timeout(result)
    if hard_timeout:
        _record_match("timeout", rules, matched, matched_rules)
    elif result.elapsed_seconds >= 60:
        _record_match("slow_response", rules, matched, matched_rules)

    if not matched_rules:
        return RuleEvaluation(
            flags=[],
            action="ignore",
            should_retry=False,
            max_retries=0,
            final_status="pass",
        )

    highest = max(matched_rules, key=lambda item: ACTION_PRIORITY.get(item.action, 0))
    retry_limits = [rule.max_retries for rule in matched_rules if rule.action == "retry"]
    if hard_timeout:
        max_retries = max(0, hard_timeout_retry_count)
        should_retry = max_retries > 0
    else:
        max_retries = max(retry_limits, default=0)
        should_retry = highest.action == "retry" and max_retries > 0
    final_status = "fail" if highest.action == "fail" else "warn"
    return RuleEvaluation(
        flags=matched,
        action=highest.action,
        should_retry=should_retry,
        max_retries=max_retries,
        final_status=final_status,
    )


def _is_hard_execution_timeout(result: ScanResult) -> bool:
    message = (result.error_message or "").lower()
    terminal_state = str(result.execution_trace.get("terminal_state") or "").lower()
    return (
        result.final_status == "timeout"
        or "timed out" in message
        or "timeout" in message
        or terminal_state.startswith("timeout")
    )


def _record_match(
    rule_name: str,
    rules: dict[str, RuleConfig],
    matched: list[str],
    matched_rules: list[RuleConfig],
) -> None:
    rule = rules.get(rule_name)
    if not rule or not rule.enabled:
        return
    matched.append(rule_name)
    matched_rules.append(rule)
