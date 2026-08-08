from __future__ import annotations


def assign_leaderboard_decision_tags(
    entries: list[dict[str, object]],
    best_candidate_id: str,
) -> None:
    score_window = 10
    minimum_cost_saving_ratio = 0.30
    minimum_speed_gain_ratio = 0.25
    lightweight_minimum_score = 60
    lightweight_score_window = 25
    lightweight_maximum_cost_ratio = 1 / 3
    lightweight_maximum_elapsed_ratio = 0.50
    for entry in entries:
        entry["decision_tags"] = []
    eligible = [
        entry
        for entry in entries
        if bool(entry.get("is_current_run_eligible"))
        and entry.get("overall_score") is not None
    ]
    if not best_candidate_id or not eligible:
        return

    best_score = max(int(entry["overall_score"]) for entry in eligible)
    efficiency_candidates = [
        entry
        for entry in eligible
        if int(entry["overall_score"]) >= best_score - score_window
    ]
    recommended = next(
        (entry for entry in eligible if entry.get("candidate_id") == best_candidate_id),
        None,
    )
    if recommended is not None:
        _append_decision_tag(
            recommended,
            kind="recommended",
            label="推荐",
            detail=f"本轮总分 {int(recommended['overall_score'])}，位列当前可比较模型第一。",
        )

    if recommended is None:
        return

    recommended_cost = recommended.get("estimated_cost_usd")
    recommended_elapsed = recommended.get("elapsed_seconds")
    if (
        recommended.get("cost_coverage") == "complete"
        and recommended_cost is not None
        and float(recommended_cost) > 0
        and recommended_elapsed is not None
        and float(recommended_elapsed) > 0
    ):
        lightweight_candidates = [
            entry
            for entry in eligible
            if entry is not recommended
            and int(entry["overall_score"]) >= lightweight_minimum_score
            and best_score - int(entry["overall_score"]) <= lightweight_score_window
            and entry.get("cost_coverage") == "complete"
            and entry.get("estimated_cost_usd") is not None
            and float(entry["estimated_cost_usd"])
            <= float(recommended_cost) * lightweight_maximum_cost_ratio
            and entry.get("elapsed_seconds") is not None
            and float(entry["elapsed_seconds"])
            <= float(recommended_elapsed) * lightweight_maximum_elapsed_ratio
        ]
        if lightweight_candidates:
            lightweight_winner = min(
                lightweight_candidates,
                key=lambda entry: (
                    -int(entry["overall_score"]),
                    float(entry["estimated_cost_usd"]),
                    float(entry["elapsed_seconds"]),
                    str(entry.get("candidate_id") or ""),
                ),
            )
            lightweight_cost = float(lightweight_winner["estimated_cost_usd"])
            lightweight_elapsed = float(lightweight_winner["elapsed_seconds"])
            cost_saving_ratio = (
                float(recommended_cost) - lightweight_cost
            ) / float(recommended_cost)
            elapsed_saving_ratio = (
                float(recommended_elapsed) - lightweight_elapsed
            ) / float(recommended_elapsed)
            score_gap = best_score - int(lightweight_winner["overall_score"])
            _append_decision_tag(
                lightweight_winner,
                kind="lightweight",
                label="轻量优选",
                detail=(
                    f"轻量任务候选中总分最高 {int(lightweight_winner['overall_score'])}；"
                    f"较榜首低 {score_gap} 分，参考费用 ${lightweight_cost:.4f}、"
                    f"费用降低 {cost_saving_ratio * 100:.1f}%，总耗时 "
                    f"{duration_text(round(lightweight_elapsed))}、"
                    f"耗时降低 {elapsed_saving_ratio * 100:.1f}%。"
                    f"入选标准：总分不低于 {lightweight_minimum_score}、"
                    f"分差不超过 {lightweight_score_window} 分、费用不高于榜首 1/3，"
                    "且耗时不高于榜首 1/2。"
                ),
            )

    if len(efficiency_candidates) < 2:
        return

    if all(
        entry.get("cost_coverage") == "complete"
        and entry.get("estimated_cost_usd") is not None
        for entry in efficiency_candidates
    ):
        value_winner = min(
            efficiency_candidates,
            key=lambda entry: (
                float(entry["estimated_cost_usd"]),
                -int(entry["overall_score"]),
                float(entry.get("elapsed_seconds") or 10**9),
                str(entry.get("candidate_id") or ""),
            ),
        )
        recommended_cost = float(recommended["estimated_cost_usd"])
        value_cost = float(value_winner["estimated_cost_usd"])
        cost_saving_ratio = (
            (recommended_cost - value_cost) / recommended_cost
            if recommended_cost > 0
            else 0.0
        )
        if cost_saving_ratio >= minimum_cost_saving_ratio:
            score_gap = best_score - int(value_winner["overall_score"])
            _append_decision_tag(
                value_winner,
                kind="value",
                label="性价比",
                detail=(
                    f"可接受分差范围内参考费用最低 ${value_cost:.4f}；"
                    f"较榜首低 {score_gap} 分，费用降低 {cost_saving_ratio * 100:.1f}%。"
                    f"候选范围为距榜首不超过 {score_window} 分，且费用至少降低 30%。"
                ),
            )

    if all(entry.get("elapsed_seconds") is not None for entry in efficiency_candidates):
        speed_winner = min(
            efficiency_candidates,
            key=lambda entry: (
                float(entry["elapsed_seconds"]),
                -int(entry["overall_score"]),
                str(entry.get("candidate_id") or ""),
            ),
        )
        recommended_elapsed = float(recommended["elapsed_seconds"])
        speed_elapsed = float(speed_winner["elapsed_seconds"])
        speed_gain_ratio = (
            (recommended_elapsed - speed_elapsed) / recommended_elapsed
            if recommended_elapsed > 0
            else 0.0
        )
        if speed_gain_ratio >= minimum_speed_gain_ratio:
            score_gap = best_score - int(speed_winner["overall_score"])
            _append_decision_tag(
                speed_winner,
                kind="speed",
                label="速度优选",
                detail=(
                    f"可接受分差范围内总耗时最短 {duration_text(round(speed_elapsed))}；"
                    f"较榜首低 {score_gap} 分，速度提升 {speed_gain_ratio * 100:.1f}%。"
                    f"候选范围为距榜首不超过 {score_window} 分，且耗时至少降低 25%。"
                ),
            )


def duration_text(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{seconds}s"
    minutes, rest = divmod(seconds, 60)
    if rest == 0:
        return f"{minutes}m"
    return f"{minutes}m {rest}s"


def _append_decision_tag(
    entry: dict[str, object],
    *,
    kind: str,
    label: str,
    detail: str,
) -> None:
    tags = entry.setdefault("decision_tags", [])
    if isinstance(tags, list):
        tags.append({"kind": kind, "label": label, "detail": detail})
