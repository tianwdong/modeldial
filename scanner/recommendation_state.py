from __future__ import annotations

from dataclasses import dataclass


DETERMINISTIC_CONFIGURATION_ERRORS = {
    "authentication_failed",
    "configuration_error",
    "model_not_found",
    "protocol_mismatch",
}


@dataclass(frozen=True)
class RecommendationDecision:
    recommendation_outcome: str
    evidence_state: str
    decision_state: str
    title: str
    action_label: str
    reason: str


def resolve_recommendation_decision(
    *,
    current_default_candidate_id: str | None,
    recommended_candidate_id: str | None,
    is_comparable: bool,
    retained_after_failure: bool,
    latest_error_category: str | None,
) -> RecommendationDecision:
    evidence_state = (
        "retained_after_failure" if retained_after_failure else "fresh"
    )
    if latest_error_category in DETERMINISTIC_CONFIGURATION_ERRORS:
        return RecommendationDecision(
            recommendation_outcome="wait",
            evidence_state=evidence_state,
            decision_state="wait",
            title="当前配置不可用，暂不形成推荐",
            action_label="检查模型接入",
            reason="当前默认配置不可用，旧成绩不能证明配置仍可执行。",
        )
    if retained_after_failure:
        outcome = _recommendation_outcome(
            current_default_candidate_id=current_default_candidate_id,
            recommended_candidate_id=recommended_candidate_id,
            is_comparable=is_comparable,
        )
        return RecommendationDecision(
            recommendation_outcome=outcome,
            evidence_state=evidence_state,
            decision_state="retain_after_failure",
            title="本次失败，保留旧成绩",
            action_label="查看失败详情",
            reason="最新重扫失败，当前结论继续使用上一次有效证据。",
        )
    outcome = _recommendation_outcome(
        current_default_candidate_id=current_default_candidate_id,
        recommended_candidate_id=recommended_candidate_id,
        is_comparable=is_comparable,
    )
    if outcome == "keep":
        return RecommendationDecision(
            recommendation_outcome=outcome,
            evidence_state=evidence_state,
            decision_state="keep",
            title="保持当前模型",
            action_label="查看证据",
            reason="当前默认与推荐候选一致，且证据达到可比较门槛。",
        )
    if outcome == "switch":
        return RecommendationDecision(
            recommendation_outcome=outcome,
            evidence_state=evidence_state,
            decision_state="switch",
            title="建议切换模型",
            action_label="查看切换依据",
            reason="推荐候选不同于当前默认，且证据达到可比较门槛。",
        )
    if not current_default_candidate_id:
        return RecommendationDecision(
            recommendation_outcome="recommend",
            evidence_state=evidence_state,
            decision_state="recommend",
            title="推荐当前最佳模型",
            action_label="查看推荐依据",
            reason="未设置当前常用模型，不影响扫描和排序；当前先展示本轮最佳结果。",
        )
    return RecommendationDecision(
        recommendation_outcome="wait",
        evidence_state=evidence_state,
        decision_state="wait",
        title="等待更多可比较证据",
        action_label="再扫描一轮",
        reason="推荐证据尚未达到切换门槛。",
    )


def _recommendation_outcome(
    *,
    current_default_candidate_id: str | None,
    recommended_candidate_id: str | None,
    is_comparable: bool,
) -> str:
    if not current_default_candidate_id or not recommended_candidate_id:
        return "wait"
    if not is_comparable:
        return "wait"
    if current_default_candidate_id == recommended_candidate_id:
        return "keep"
    return "switch"
