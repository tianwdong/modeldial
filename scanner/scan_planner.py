from __future__ import annotations

from datetime import datetime
import time
from uuid import uuid4

from .active_run_store import ActiveRunStore
from .comparison_groups import ComparisonGroupProjector, parse_iso_timestamp as _parse_iso_timestamp
from .config_store import ConfigStore
from .history_store import HistoryStore
from .legacy_scan_compat import (
    SCAN_PHASE,
    metadata_question_count,
    metadata_question_ids,
    normalize_phase,
)
from .models import AppConfig, ResolvedScanTarget, RunMetadata, ScanPlan, ScanResult
from .question_bank import (
    EvaluationProfileSpec,
    QuestionBank,
    QuestionCollection,
    QuestionSpec,
)
from .route_identity import build_route_fingerprint
from .scan_target_resolver import ScanTargetResolver
from .scoring import EQUAL_SCORING_MODE


INCREMENTAL_FULL_REUSE_SECONDS = 24 * 60 * 60


def _result_matches_target_route(
    result: ScanResult,
    target: ResolvedScanTarget,
) -> bool:
    expected = build_route_fingerprint(
        source_id=target.source_id,
        connection_id=target.connection_id,
        connection_mode=target.connection_mode,
        api_format=target.api_format,
        provider_preset=target.provider_preset,
        base_url=target.base_url,
        model_id=target.model_id,
        scan_profile=target.scan_profile,
    )
    observed = str(result.execution_trace.get("route_fingerprint") or "").strip()
    return observed == expected


class ScanPlanningError(ValueError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def validate_scan_selection_contract(
    *,
    evaluation_profile: EvaluationProfileSpec,
    selection_mode: str,
    candidate_count: int,
) -> None:
    if (
        evaluation_profile.id == "quick"
        and selection_mode == "custom"
        and candidate_count != 2
    ):
        raise ScanPlanningError(
            "quick_candidate_count",
            "快速对比需要选择两个配置",
        )


class ScanPlanner:
    """Build one authoritative execution plan without owning runtime state."""

    def __init__(
        self,
        *,
        config_store: ConfigStore,
        history_store: HistoryStore,
        active_run_store: ActiveRunStore,
        question_bank: QuestionBank,
        target_resolver: ScanTargetResolver,
        comparison_group_projector: ComparisonGroupProjector,
    ) -> None:
        self.config_store = config_store
        self.history_store = history_store
        self.active_run_store = active_run_store
        self.question_bank = question_bank
        self.scan_target_resolver = target_resolver
        self.comparison_group_projector = comparison_group_projector

    def load_config(self) -> AppConfig:
        return self.config_store.load()

    @staticmethod
    def _comparison_group_id(
        run_id: str | None,
        metadata: dict[str, object] | None,
    ) -> str | None:
        return ComparisonGroupProjector.group_id(run_id, metadata)

    def _aggregate_comparison_group_metadata(
        self,
        *,
        group_id: str,
        history: list[ScanResult],
        run_metadata_by_id: dict[str, dict[str, object]],
        member_run_ids: list[str],
    ) -> dict[str, object] | None:
        return self.comparison_group_projector.aggregate_metadata(
            group_id=group_id,
            history=history,
            run_metadata_by_id=run_metadata_by_id,
            member_run_ids=member_run_ids,
        )

    def _latest_appendable_comparison_group_id(
        self,
        history: list[ScanResult],
        run_metadata_by_id: dict[str, dict[str, object]],
    ) -> str | None:
        return self.comparison_group_projector.latest_appendable_group_id(
            history,
            run_metadata_by_id,
        )

    def incremental_full_reuse_plan(
        self,
        *,
        history: list[ScanResult],
        run_metadata_by_id: dict[str, dict[str, object]],
        question_pack: QuestionCollection,
        evaluation_profile: EvaluationProfileSpec,
        targets: list[ResolvedScanTarget],
    ) -> dict[str, object] | None:
        expected_question_ids = list(evaluation_profile.question_ids)
        expected_question_id_set = set(expected_question_ids)
        expected_grader_by_question = {
            question.id: question.grader.kind
            for question in question_pack.questions_for_profile(evaluation_profile.id)
        }
        seen_group_ids: set[str] = set()
        now = time.time()

        for item in reversed(history):
            group_id = self._comparison_group_id(
                item.run_id,
                run_metadata_by_id.get(item.run_id),
            )
            if not group_id or group_id in seen_group_ids:
                continue
            seen_group_ids.add(group_id)
            member_run_ids = self._comparison_group_member_run_ids(
                group_id=group_id,
                history=history,
                run_metadata_by_id=run_metadata_by_id,
            )
            metadata = self._aggregate_comparison_group_metadata(
                group_id=group_id,
                history=history,
                run_metadata_by_id=run_metadata_by_id,
                member_run_ids=member_run_ids,
            )
            if not self.metadata_matches_question_pack(metadata, question_pack):
                continue
            if not isinstance(metadata, dict):
                continue
            if str(metadata.get("evaluation_result_level") or "") != "complete":
                continue
            if str(metadata.get("scoring_mode") or "") != EQUAL_SCORING_MODE:
                continue
            if set(metadata_question_ids(metadata)) != expected_question_id_set:
                continue
            completed_at = _parse_iso_timestamp(metadata.get("completed_at"))
            if completed_at is None:
                continue
            age_seconds = now - completed_at
            if age_seconds < -300 or age_seconds > INCREMENTAL_FULL_REUSE_SECONDS:
                continue

            state = self._comparison_group_result_state(
                history=history,
                run_ids=member_run_ids,
                enabled_targets=targets,
                question_ids=expected_question_ids,
            )
            buckets = state["buckets"]
            reusable_candidate_ids: list[str] = []
            for target in targets:
                results = buckets.get(target.candidate_id, [])
                by_question = {result.question_id: result for result in results}
                if set(by_question) != expected_question_id_set:
                    continue
                expected_route = build_route_fingerprint(
                    source_id=target.source_id,
                    connection_id=target.connection_id,
                    connection_mode=target.connection_mode,
                    api_format=target.api_format,
                    provider_preset=target.provider_preset,
                    base_url=target.base_url,
                    model_id=target.model_id,
                    scan_profile=target.scan_profile,
                )
                if not all(
                    result.error_message is None
                    and result.grader_kind == expected_grader_by_question.get(question_id)
                    and str(result.execution_trace.get("route_fingerprint") or "")
                    == expected_route
                    for question_id, result in by_question.items()
                ):
                    continue
                reusable_candidate_ids.append(target.candidate_id)

            if reusable_candidate_ids:
                return {
                    "group_id": group_id,
                    "member_run_ids": member_run_ids,
                    "reusable_candidate_ids": reusable_candidate_ids,
                }
        return None

    @staticmethod
    def metadata_matches_question_pack(
        metadata: dict[str, object] | None,
        question_pack: QuestionCollection,
    ) -> bool:
        return bool(
            isinstance(metadata, dict)
            and str(metadata.get("question_pack_id") or "unknown")
            == question_pack.metadata.question_pack_id
            and str(metadata.get("question_pack_version") or "unknown")
            == question_pack.metadata.question_pack_version
        )

    def _comparison_group_member_run_ids(
        self,
        *,
        group_id: str,
        history: list[ScanResult],
        run_metadata_by_id: dict[str, dict[str, object]],
    ) -> list[str]:
        return self.comparison_group_projector.member_run_ids(
            group_id=group_id,
            history=history,
            run_metadata_by_id=run_metadata_by_id,
        )

    def _comparison_group_candidate_ids(
        self,
        *,
        group_id: str,
        history: list[ScanResult],
        run_metadata_by_id: dict[str, dict[str, object]],
        enabled_targets: list[ResolvedScanTarget] | None = None,
    ) -> list[str]:
        return self.comparison_group_projector.candidate_ids(
            group_id=group_id,
            history=history,
            run_metadata_by_id=run_metadata_by_id,
            enabled_targets=enabled_targets,
        )

    def _comparison_group_result_state(
        self,
        *,
        history: list[ScanResult],
        run_ids: list[str],
        enabled_targets: list[ResolvedScanTarget],
        question_ids: list[str] | None = None,
    ) -> dict[str, object]:
        return self.comparison_group_projector.result_state(
            history=history,
            run_ids=run_ids,
            enabled_targets=enabled_targets,
            question_ids=question_ids,
        )

    def plan(
        self,
        *,
        force_restart: bool = False,
        requested_candidate_ids: list[str] | None = None,
        selection_mode: str = "regular",
        custom_round_mode: str = "new_round",
        evaluation_profile_id: str | None = None,
        upgrade_from_run_id: str | None = None,
        retry_failed_results: bool = False,
    ) -> ScanPlan:
        config = self.load_config()
        history = self.history_store.load_all()
        run_metadata_by_id = self.history_store.load_run_metadata_map()
        question_pack = self.question_bank.load()
        regular_targets = self._resolved_scan_targets(config)
        regular_candidate_ids = [target.candidate_id for target in regular_targets]
        allow_disabled_requested_candidates = selection_mode == "single"
        if selection_mode not in {"regular", "custom", "single", "incremental_full"}:
            raise ValueError(f"unsupported selection_mode: {selection_mode}")
        if selection_mode == "custom" and custom_round_mode not in {"append", "new_round"}:
            raise ValueError(f"unsupported custom_round_mode: {custom_round_mode}")
        if (
            not force_restart
            and selection_mode == "custom"
            and requested_candidate_ids is not None
        ):
            blocking_active_run = self.active_run_store.load() or self.infer_active_run_from_history(
                config,
                history[-500:],
            )
            if blocking_active_run is not None:
                raise ValueError("当前有未完成扫描，请先继续或重新扫描")
        active_metadata: dict[str, object] | None = None
        if not force_restart:
            active_run = self.active_run_store.load()
            if not active_run and requested_candidate_ids is None:
                active_run = self.infer_active_run_from_history(
                    config,
                    history[-500:],
                    retry_failed_results=retry_failed_results,
                )
            loaded_active_metadata = (active_run or {}).get("run_metadata")
            if isinstance(loaded_active_metadata, dict):
                active_metadata = loaded_active_metadata
                frozen_ids = active_metadata.get("requested_candidate_ids")
                if requested_candidate_ids is None and isinstance(frozen_ids, list) and frozen_ids:
                    requested_candidate_ids = [str(item) for item in frozen_ids]
                    selection_mode = str(active_metadata.get("selection_mode") or selection_mode)
                    if selection_mode == "custom":
                        custom_round_mode = (
                            "append"
                            if str(active_metadata.get("comparison_group_mode") or "") == "custom_append"
                            else "new_round"
                        )
                frozen_profile_id = active_metadata.get("evaluation_profile_id")
                if (
                    evaluation_profile_id is None
                    and isinstance(frozen_profile_id, str)
                    and frozen_profile_id
                    and frozen_profile_id != "legacy_full"
                ):
                    evaluation_profile_id = frozen_profile_id
        started_selection_mode = selection_mode
        started_custom_round_mode = custom_round_mode
        if (
            not upgrade_from_run_id
            and selection_mode == "single"
            and requested_candidate_ids is not None
            and not force_restart
        ):
            append_group_id = self._latest_appendable_comparison_group_id(
                history,
                run_metadata_by_id,
            )
            append_group_run_ids = (
                self._comparison_group_member_run_ids(
                    group_id=append_group_id,
                    history=history,
                    run_metadata_by_id=run_metadata_by_id,
                )
                if append_group_id
                else []
            )
            append_group_metadata = (
                self._aggregate_comparison_group_metadata(
                    group_id=append_group_id,
                    history=history,
                    run_metadata_by_id=run_metadata_by_id,
                    member_run_ids=append_group_run_ids,
                )
                if append_group_id
                else None
            )
            if append_group_id and self.metadata_matches_question_pack(
                append_group_metadata,
                question_pack,
            ):
                selection_mode = "custom"
                custom_round_mode = "append"
        comparison_group_mode = (
            "single"
            if selection_mode == "single"
            else "incremental_full"
            if selection_mode == "incremental_full"
            else "custom_append"
            if selection_mode == "custom" and custom_round_mode == "append"
            else "custom_new_round"
            if selection_mode == "custom"
            else "regular"
        )
        comparison_group_id: str | None = None
        comparison_parent_run_id: str | None = None
        append_target_group_id: str | None = None
        appended_candidate_ids: list[str] = []
        skipped_candidate_ids: list[str] = []
        if upgrade_from_run_id:
            requested_upgrade_candidate_ids = (
                list(requested_candidate_ids)
                if requested_candidate_ids is not None
                else None
            )
            requested_upgrade_selection_mode = selection_mode
            source_group_id = self._comparison_group_id(
                upgrade_from_run_id,
                run_metadata_by_id.get(upgrade_from_run_id),
            )
            if not source_group_id:
                raise ValueError("找不到需要补全的比较轮")
            source_run_ids = self._comparison_group_member_run_ids(
                group_id=source_group_id,
                history=history,
                run_metadata_by_id=run_metadata_by_id,
            )
            source_metadata = self._aggregate_comparison_group_metadata(
                group_id=source_group_id,
                history=history,
                run_metadata_by_id=run_metadata_by_id,
                member_run_ids=source_run_ids,
            )
            if source_metadata is None:
                raise ValueError("找不到需要补全的比较轮信息")
            if (
                str(source_metadata.get("question_pack_id") or "unknown")
                != question_pack.metadata.question_pack_id
                or str(source_metadata.get("question_pack_version") or "unknown")
                != question_pack.metadata.question_pack_version
            ):
                raise ValueError("题包已变化，无法补全原比较轮，请新开一轮")
            source_profile_id = self.metadata_evaluation_profile_id(
                source_metadata,
                question_pack,
            )
            if source_profile_id is None:
                raise ValueError("原比较轮缺少可验证的评测范围，无法补全")
            source_profile = question_pack.evaluation_profile(source_profile_id)
            target_profile_id = evaluation_profile_id or source_profile.upgrade_to
            if not target_profile_id:
                raise ValueError("当前评测模式没有可补全的上级档案")
            target_profile = question_pack.evaluation_profile(target_profile_id)
            if not set(source_profile.question_ids) < set(target_profile.question_ids):
                raise ValueError("补全目标必须覆盖并扩展当前评测范围")
            evaluation_profile_id = target_profile.id
            source_candidate_ids = [
                str(item)
                for item in source_metadata.get("requested_candidate_ids", [])
            ]
            if not source_candidate_ids:
                source_candidate_ids = self._comparison_group_candidate_ids(
                    group_id=source_group_id,
                    history=history,
                    run_metadata_by_id=run_metadata_by_id,
                )
            requested_candidate_ids = (
                requested_upgrade_candidate_ids
                if requested_upgrade_candidate_ids is not None
                else source_candidate_ids
            )
            enabled_targets = self._requested_scan_targets(
                config,
                requested_candidate_ids,
                allow_disabled=requested_upgrade_candidate_ids is None,
            )
            comparison_targets = enabled_targets
            effective_requested_candidate_ids = list(requested_candidate_ids)
            if requested_upgrade_candidate_ids is not None:
                selection_mode = requested_upgrade_selection_mode
                regular_candidate_ids = (
                    list(effective_requested_candidate_ids)
                    if selection_mode == "regular"
                    else [
                        str(item)
                        for item in source_metadata.get("regular_candidate_ids", [])
                        if str(item) in set(effective_requested_candidate_ids)
                    ]
                )
            else:
                regular_candidate_ids = [
                    str(item)
                    for item in source_metadata.get("regular_candidate_ids", [])
                ]
                selection_mode = str(source_metadata.get("selection_mode") or "regular")
            comparison_group_id = source_group_id
            comparison_group_mode = "profile_upgrade"
            comparison_parent_run_id = source_run_ids[-1] if source_run_ids else upgrade_from_run_id
        elif selection_mode == "incremental_full":
            if requested_candidate_ids is not None and set(requested_candidate_ids) != set(
                regular_candidate_ids
            ):
                raise ScanPlanningError(
                    "incremental_scope_mismatch",
                    "增量全量扫描必须覆盖全部已启用配置",
                )
            target_profile = self.evaluation_profile(
                question_pack,
                evaluation_profile_id or "full",
            )
            if target_profile.result_level != "complete":
                raise ScanPlanningError(
                    "incremental_profile_required",
                    "增量全量扫描必须使用完整五题评测",
                )
            evaluation_profile_id = target_profile.id

            if (
                active_metadata is not None
                and str(active_metadata.get("comparison_group_mode") or "")
                == "incremental_full"
            ):
                comparison_group_id = str(
                    active_metadata.get("comparison_group_id")
                    or active_metadata.get("run_id")
                )
                reusable_candidate_ids = [
                    str(item)
                    for item in active_metadata.get("skipped_candidate_ids", [])
                ]
                missing_candidate_ids = [
                    str(item)
                    for item in active_metadata.get("appended_candidate_ids", [])
                ]
                source_run_ids = self._comparison_group_member_run_ids(
                    group_id=comparison_group_id,
                    history=history,
                    run_metadata_by_id=run_metadata_by_id,
                )
            else:
                reuse_plan = self.incremental_full_reuse_plan(
                    history=history,
                    run_metadata_by_id=run_metadata_by_id,
                    question_pack=question_pack,
                    evaluation_profile=target_profile,
                    targets=regular_targets,
                )
                if reuse_plan is None:
                    raise ScanPlanningError(
                        "incremental_no_reusable_evidence",
                        "没有 24 小时内可复用的兼容快测结果，请全新扫描",
                    )
                comparison_group_id = str(reuse_plan["group_id"])
                source_run_ids = [str(item) for item in reuse_plan["member_run_ids"]]
                reusable_candidate_ids = [
                    str(item) for item in reuse_plan["reusable_candidate_ids"]
                ]
                reusable_candidate_id_set = set(reusable_candidate_ids)
                missing_candidate_ids = [
                    candidate_id
                    for candidate_id in regular_candidate_ids
                    if candidate_id not in reusable_candidate_id_set
                ]
            if not missing_candidate_ids:
                raise ScanPlanningError(
                    "incremental_already_complete",
                    "全部已启用配置已有 24 小时内兼容结果，无需增量补齐",
                )

            comparison_targets = regular_targets
            enabled_targets = [
                target
                for target in regular_targets
                if target.candidate_id in set(missing_candidate_ids)
            ]
            effective_requested_candidate_ids = list(regular_candidate_ids)
            comparison_group_mode = "incremental_full"
            comparison_parent_run_id = source_run_ids[-1] if source_run_ids else None
            append_target_group_id = comparison_group_id
            appended_candidate_ids = list(missing_candidate_ids)
            skipped_candidate_ids = list(reusable_candidate_ids)
        elif selection_mode == "custom" and custom_round_mode == "append":
            resuming_custom_append = (
                active_metadata is not None
                and str(active_metadata.get("comparison_group_mode") or "")
                == "custom_append"
            )
            if resuming_custom_append:
                target_group_id = self._comparison_group_id(
                    str(active_metadata.get("run_id") or ""),
                    active_metadata,
                )
                effective_requested_candidate_ids = [
                    str(item)
                    for item in active_metadata.get("requested_candidate_ids", [])
                ]
                appended_candidate_ids = [
                    str(item)
                    for item in active_metadata.get("appended_candidate_ids", [])
                ]
                skipped_candidate_ids = [
                    str(item)
                    for item in active_metadata.get("skipped_candidate_ids", [])
                ]
                if not target_group_id or not effective_requested_candidate_ids:
                    raise ValueError("找不到需要继续的比较轮信息")
                if not appended_candidate_ids:
                    raise ValueError("找不到需要继续的追加模型")
            else:
                target_group_id = self._latest_appendable_comparison_group_id(
                    history,
                    run_metadata_by_id,
                )
                if not target_group_id:
                    raise ScanPlanningError(
                        "append_no_current_round",
                        "当前没有可补充的比较轮，请先完成至少一轮扫描",
                    )
                target_group_run_ids = self._comparison_group_member_run_ids(
                    group_id=target_group_id,
                    history=history,
                    run_metadata_by_id=run_metadata_by_id,
                )
                target_group_metadata = self._aggregate_comparison_group_metadata(
                    group_id=target_group_id,
                    history=history,
                    run_metadata_by_id=run_metadata_by_id,
                    member_run_ids=target_group_run_ids,
                )
                if target_group_metadata is None:
                    raise ValueError("找不到需要补入的比较轮信息")
                if not self.metadata_matches_question_pack(
                    target_group_metadata,
                    question_pack,
                ):
                    raise ValueError("题包已变化，无法补入原比较轮，请新开一轮")
                target_group_profile_id = self.metadata_evaluation_profile_id(
                    target_group_metadata,
                    question_pack,
                )
                if evaluation_profile_id is None:
                    evaluation_profile_id = target_group_profile_id
                elif (
                    target_group_profile_id is not None
                    and evaluation_profile_id != target_group_profile_id
                ):
                    raise ScanPlanningError(
                        "append_profile_mismatch",
                        "所选评测模式与当前比较轮不一致，请新开一轮",
                    )
                target_group_candidate_ids = self._comparison_group_candidate_ids(
                    group_id=target_group_id,
                    history=history,
                    run_metadata_by_id=run_metadata_by_id,
                )
                currently_resolvable_candidate_ids = {
                    target.candidate_id
                    for target in (
                        self._resolved_connection_ready_targets(config)
                        if allow_disabled_requested_candidates
                        else self._resolved_available_targets(config)
                    )
                }
                target_group_candidate_ids = [
                    candidate_id
                    for candidate_id in target_group_candidate_ids
                    if candidate_id in currently_resolvable_candidate_ids
                ]
                target_group_candidate_id_set = set(target_group_candidate_ids)
                requested_custom_candidate_ids = list(requested_candidate_ids or [])
                appended_candidate_ids = [
                    candidate_id
                    for candidate_id in requested_custom_candidate_ids
                    if candidate_id not in target_group_candidate_id_set
                ]
                skipped_candidate_ids = [
                    candidate_id
                    for candidate_id in requested_custom_candidate_ids
                    if candidate_id in target_group_candidate_id_set
                ]
                if not appended_candidate_ids:
                    raise ScanPlanningError(
                        "append_no_new_candidates",
                        "所选模型都已在当前轮跑过，请改为新开一轮",
                    )
                effective_requested_candidate_ids = [
                    *target_group_candidate_ids,
                    *appended_candidate_ids,
                ]
            comparison_targets = self._requested_scan_targets(
                config,
                effective_requested_candidate_ids,
                allow_disabled=allow_disabled_requested_candidates,
            )
            enabled_targets = self._requested_scan_targets(
                config,
                appended_candidate_ids,
                allow_disabled=allow_disabled_requested_candidates,
            )
            comparison_group_id = target_group_id
            if resuming_custom_append:
                comparison_parent_run_id = str(
                    active_metadata.get("comparison_parent_run_id") or ""
                ) or None
                append_target_group_id = str(
                    active_metadata.get("append_target_group_id") or target_group_id
                )
            else:
                comparison_parent_run_id = (
                    self._comparison_group_member_run_ids(
                        group_id=target_group_id,
                        history=history,
                        run_metadata_by_id=run_metadata_by_id,
                    )[-1]
                    if target_group_candidate_ids
                    else None
                )
                append_target_group_id = target_group_id
        else:
            enabled_targets = self._requested_scan_targets(
                config,
                requested_candidate_ids,
                allow_disabled=selection_mode == "single",
            )
            comparison_targets = enabled_targets
            effective_requested_candidate_ids = [target.candidate_id for target in enabled_targets]
        evaluation_profile = self.evaluation_profile(
            question_pack,
            evaluation_profile_id,
        )
        validate_scan_selection_contract(
            evaluation_profile=evaluation_profile,
            selection_mode=started_selection_mode,
            candidate_count=len(effective_requested_candidate_ids),
        )
        enabled_questions = question_pack.questions_for_profile(evaluation_profile.id)
        question_ids = [question.id for question in enabled_questions]
        attempts_per_target = max(1, len(enabled_questions))
        evaluation_total = len(comparison_targets) * attempts_per_target
        if force_restart:
            resume = None
        else:
            resume = self.load_resume_state(
                config,
                comparison_targets,
                attempts_per_target,
                question_ids=question_ids,
                retry_failed_results=retry_failed_results,
            )
            if upgrade_from_run_id and comparison_group_id:
                group_run_ids = self._comparison_group_member_run_ids(
                    group_id=comparison_group_id,
                    history=history,
                    run_metadata_by_id=run_metadata_by_id,
                )
                comparison_state = self._comparison_group_result_state(
                    history=history,
                    run_ids=group_run_ids,
                    enabled_targets=comparison_targets,
                    question_ids=question_ids,
                )
                resume = {
                    "active_run": None,
                    "run_id": None,
                    "run_history": [
                        item for item in history if item.run_id in set(group_run_ids)
                    ],
                    "completed_count": comparison_state["completed_count"],
                    "completed_steps": comparison_state["completed_steps"],
                    "completed_by_candidate": comparison_state["completed_by_candidate"],
                    "buckets": comparison_state["buckets"],
                }
            if selection_mode == "incremental_full" and comparison_group_id:
                group_run_ids = self._comparison_group_member_run_ids(
                    group_id=comparison_group_id,
                    history=history,
                    run_metadata_by_id=run_metadata_by_id,
                )
                comparison_state = self._comparison_group_result_state(
                    history=history,
                    run_ids=group_run_ids,
                    enabled_targets=comparison_targets,
                    question_ids=question_ids,
                )
                resume = {
                    "active_run": resume["active_run"] if resume else None,
                    "run_id": resume["run_id"] if resume else None,
                    "run_history": [
                        item for item in history if item.run_id in set(group_run_ids)
                    ],
                    "completed_count": comparison_state["completed_count"],
                    "completed_steps": comparison_state["completed_steps"],
                    "completed_by_candidate": comparison_state["completed_by_candidate"],
                    "buckets": comparison_state["buckets"],
                }
            if (
                resume is None
                and selection_mode == "custom"
                and custom_round_mode == "append"
                and comparison_group_id
            ):
                group_run_ids = self._comparison_group_member_run_ids(
                    group_id=comparison_group_id,
                    history=history,
                    run_metadata_by_id=run_metadata_by_id,
                )
                comparison_state = self._comparison_group_result_state(
                    history=history,
                    run_ids=group_run_ids,
                    enabled_targets=comparison_targets,
                    question_ids=question_ids,
                )
                resume = {
                    "active_run": None,
                    "run_id": None,
                    "run_history": [
                        item for item in history if item.run_id in set(group_run_ids)
                    ],
                    "completed_count": comparison_state["completed_count"],
                    "completed_steps": comparison_state["completed_steps"],
                    "completed_by_candidate": comparison_state["completed_by_candidate"],
                    "buckets": comparison_state["buckets"],
                }
        run_id = str(resume["run_id"]) if resume and resume.get("run_id") else self._new_run_id()
        if resume:
            active_metadata = (resume["active_run"] or {}).get("run_metadata")
            if isinstance(active_metadata, dict):
                comparison_group_id = self._comparison_group_id(run_id, active_metadata)
                comparison_group_mode = str(
                    active_metadata.get("comparison_group_mode") or comparison_group_mode
                )
        if comparison_group_id is None:
            comparison_group_id = run_id
        run_metadata = self.active_run_metadata(
            run_id=str(run_id),
            status="running",
            enabled_targets=comparison_targets,
            question_count=attempts_per_target,
            active_run=resume["active_run"] if resume else None,
            selection_mode=selection_mode,
            requested_candidate_ids=effective_requested_candidate_ids,
            regular_candidate_ids=regular_candidate_ids,
            comparison_group_id=comparison_group_id,
            comparison_group_mode=comparison_group_mode,
            comparison_parent_run_id=comparison_parent_run_id,
            append_target_group_id=append_target_group_id,
            appended_candidate_ids=appended_candidate_ids,
            skipped_candidate_ids=skipped_candidate_ids,
            evaluation_profile=evaluation_profile,
            question_ids=question_ids,
            upgrade_from_run_id=upgrade_from_run_id,
        )

        return ScanPlan(
            run_id=str(run_id),
            force_restart=force_restart,
            total_targets=evaluation_total,
            completed_targets=(
                int(resume["completed_count"]) if resume else 0
            ),
            selection_mode=started_selection_mode,
            custom_round_mode=started_custom_round_mode,
            execution_selection_mode=selection_mode,
            execution_custom_round_mode=custom_round_mode,
            evaluation_profile_id=evaluation_profile.id,
            evaluation_profile_label=evaluation_profile.label,
            evaluation_result_level=evaluation_profile.result_level,
            question_count=attempts_per_target,
            upgrade_from_run_id=upgrade_from_run_id,
            requested_candidate_ids=(
                tuple(requested_candidate_ids)
                if requested_candidate_ids is not None
                else None
            ),
            effective_requested_candidate_ids=tuple(
                effective_requested_candidate_ids
            ),
            regular_candidate_ids=tuple(regular_candidate_ids),
            config=config,
            history=tuple(history),
            comparison_targets=tuple(comparison_targets),
            enabled_targets=tuple(enabled_targets),
            evaluation_profile=evaluation_profile,
            enabled_questions=tuple(enabled_questions),
            question_ids=tuple(question_ids),
            attempts_per_target=attempts_per_target,
            resume=resume,
            run_metadata=run_metadata,
        )

    def _new_run_id(self) -> str:
        return f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"

    def _timestamp(self) -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")

    def active_run_metadata(
        self,
        *,
        run_id: str,
        status: str,
        enabled_targets: list[ResolvedScanTarget],
        question_count: int,
        active_run: dict[str, object] | None = None,
        selection_mode: str = "regular",
        requested_candidate_ids: list[str] | None = None,
        regular_candidate_ids: list[str] | None = None,
        comparison_group_id: str | None = None,
        comparison_group_mode: str | None = None,
        comparison_parent_run_id: str | None = None,
        append_target_group_id: str | None = None,
        appended_candidate_ids: list[str] | None = None,
        skipped_candidate_ids: list[str] | None = None,
        evaluation_profile: EvaluationProfileSpec,
        question_ids: list[str],
        upgrade_from_run_id: str | None = None,
    ) -> dict[str, object]:
        existing = (active_run or {}).get("run_metadata")
        if isinstance(existing, dict):
            metadata = RunMetadata.from_dict(existing).to_dict()
            metadata["question_count"] = len(question_ids)
            metadata["question_ids"] = list(question_ids)
            metadata["evaluation_profile_id"] = evaluation_profile.id
            metadata["evaluation_profile_label"] = evaluation_profile.label
            metadata["evaluation_result_level"] = evaluation_profile.result_level
            metadata["evaluation_score_max"] = evaluation_profile.score_max
            metadata["upgrade_target_profile_id"] = evaluation_profile.upgrade_to
            if upgrade_from_run_id is not None:
                metadata["upgrade_from_run_id"] = upgrade_from_run_id
            metadata["status"] = status if metadata.get("status") == "running" else metadata.get("status", status)
            return self._preserve_legacy_selection_metadata(existing, metadata)
        pack_metadata = self.question_bank.metadata()
        metadata = RunMetadata(
            run_id=run_id,
            question_pack_id=pack_metadata.question_pack_id,
            question_pack_version=pack_metadata.question_pack_version,
            started_at=self._timestamp(),
            completed_at=None,
            candidate_count=len(enabled_targets),
            question_count=question_count,
            status=status,
            evaluation_profile_id=evaluation_profile.id,
            evaluation_profile_label=evaluation_profile.label,
            evaluation_result_level=evaluation_profile.result_level,
            evaluation_score_max=evaluation_profile.score_max,
            question_ids=list(question_ids),
            upgrade_from_run_id=upgrade_from_run_id,
            upgrade_target_profile_id=evaluation_profile.upgrade_to,
            selection_mode=selection_mode,
            requested_candidate_ids=list(requested_candidate_ids or []),
            regular_candidate_ids=list(regular_candidate_ids or []),
            comparison_group_id=comparison_group_id or run_id,
            comparison_group_mode=comparison_group_mode or selection_mode,
            comparison_parent_run_id=comparison_parent_run_id,
            append_target_group_id=append_target_group_id,
            appended_candidate_ids=list(appended_candidate_ids or []),
            skipped_candidate_ids=list(skipped_candidate_ids or []),
            is_complete_regular_round=False,
            scoring_mode=EQUAL_SCORING_MODE,
        ).to_dict()
        return metadata

    @staticmethod
    def _preserve_legacy_selection_metadata(
        raw_metadata: dict[str, object],
        normalized: dict[str, object],
    ) -> dict[str, object]:
        return ComparisonGroupProjector.preserve_legacy_selection_metadata(
            raw_metadata,
            normalized,
        )

    def _enabled_questions(self) -> list[QuestionSpec]:
        return self.question_bank.load().enabled_questions

    @staticmethod
    def evaluation_profile(
        question_pack: QuestionCollection,
        evaluation_profile_id: str | None,
    ) -> EvaluationProfileSpec:
        if evaluation_profile_id is None or evaluation_profile_id == "legacy_full":
            return question_pack.complete_evaluation_profile
        return question_pack.evaluation_profile(evaluation_profile_id)

    @staticmethod
    def metadata_evaluation_profile_id(
        metadata: dict[str, object] | None,
        question_pack: QuestionCollection,
    ) -> str | None:
        if not isinstance(metadata, dict):
            return None
        explicit_profile_id = str(metadata.get("evaluation_profile_id") or "").strip()
        if explicit_profile_id and explicit_profile_id != "legacy_full":
            try:
                question_pack.evaluation_profile(explicit_profile_id)
            except ValueError:
                return None
            return explicit_profile_id
        frozen_question_ids = metadata_question_ids(metadata)
        for profile in question_pack.evaluation_profiles:
            if frozen_question_ids == profile.question_ids:
                return profile.id
        if (
            not frozen_question_ids
            and metadata_question_count(metadata) == question_pack.question_count
        ):
            return question_pack.complete_evaluation_profile.id
        return None

    def _resolved_scan_targets(self, config: AppConfig) -> list[ResolvedScanTarget]:
        return self.scan_target_resolver.enabled_targets(config)

    def _resolved_available_targets(self, config: AppConfig) -> list[ResolvedScanTarget]:
        return self.scan_target_resolver.available_targets(config)

    def _resolved_connection_ready_targets(
        self,
        config: AppConfig,
    ) -> list[ResolvedScanTarget]:
        return self.scan_target_resolver.connection_ready_targets(config)

    def _requested_scan_targets(
        self,
        config: AppConfig,
        requested_candidate_ids: list[str] | None,
        *,
        allow_disabled: bool = False,
    ) -> list[ResolvedScanTarget]:
        return self.scan_target_resolver.requested_targets(
            config,
            requested_candidate_ids,
            allow_disabled=allow_disabled,
        )

    def _candidate_ids_by_label(
        self,
        enabled_targets: list[ResolvedScanTarget],
    ) -> dict[str, list[str]]:
        return self.scan_target_resolver.candidate_ids_by_label(enabled_targets)

    def _candidate_id_from_label(
        self,
        label: str,
        candidate_ids_by_label: dict[str, list[str]],
    ) -> str | None:
        return self.scan_target_resolver.candidate_id_from_label(
            label,
            candidate_ids_by_label,
        )

    def _entry_candidate_id(
        self,
        entry: dict[str, object],
        candidate_ids_by_label: dict[str, list[str]],
    ) -> str | None:
        return self.scan_target_resolver.entry_candidate_id(
            entry,
            candidate_ids_by_label,
        )

    def _result_candidate_id(
        self,
        result: ScanResult,
        candidate_ids_by_label: dict[str, list[str]],
    ) -> str | None:
        return self.scan_target_resolver.result_candidate_id(
            result,
            candidate_ids_by_label,
        )

    def load_resume_state(
        self,
        config: AppConfig,
        enabled_targets: list[ResolvedScanTarget],
        attempts_per_target: int,
        *,
        question_ids: list[str] | None = None,
        retry_failed_results: bool = False,
    ) -> dict[str, object] | None:
        active_run = self.active_run_store.load()
        if not active_run:
            history = self.history_store.load_recent(limit=500)
            active_run = self.infer_active_run_from_history(
                config,
                history,
                attempts_per_target=attempts_per_target,
                question_ids=question_ids,
                retry_failed_results=retry_failed_results,
            )
            if not active_run:
                return None
        candidate_ids_by_label = self._candidate_ids_by_label(enabled_targets)
        payload_candidate_ids = {
            candidate_id
            for item in active_run.get("entries", [])
            if isinstance(item, dict)
            for candidate_id in [self._entry_candidate_id(item, candidate_ids_by_label)]
            if candidate_id is not None
        }
        enabled_candidate_ids = {target.candidate_id for target in enabled_targets}
        if payload_candidate_ids != enabled_candidate_ids:
            return None
        targets_by_candidate_id = {
            target.candidate_id: target for target in enabled_targets
        }

        run_id = str(active_run.get("run_id"))
        history = self.history_store.load_recent(limit=500)
        active_metadata = (active_run.get("run_metadata") or {}) if isinstance(active_run.get("run_metadata"), dict) else {}
        comparison_group_mode = str(
            active_metadata.get("comparison_group_mode") or ""
        )
        if comparison_group_mode == "custom_append":
            metadata_by_run = self.history_store.load_run_metadata_map()
            group_id = self._comparison_group_id(run_id, active_metadata)
            if not group_id:
                return None
            group_run_ids = self._comparison_group_member_run_ids(
                group_id=group_id,
                history=history,
                run_metadata_by_id=metadata_by_run,
            )
            if run_id not in group_run_ids:
                group_run_ids.append(run_id)
            resume_state = self._comparison_group_result_state(
                history=history,
                run_ids=group_run_ids,
                enabled_targets=enabled_targets,
                question_ids=question_ids,
            )
            run_history = [item for item in history if item.run_id in set(group_run_ids)]
        else:
            run_history = [item for item in history if item.run_id == run_id]
            completed_steps: set[tuple[str, str, str]] = set()
            completed_by_candidate: dict[str, int] = {}
            buckets: dict[str, list[ScanResult]] = {}
            latest_by_candidate: dict[str, dict[str, ScanResult]] = {}
            question_id_set = set(question_ids or [])
            for item in run_history:
                candidate_id = self._result_candidate_id(item, candidate_ids_by_label)
                if candidate_id is None:
                    return None
                phase = normalize_phase(item.phase)
                if phase != SCAN_PHASE:
                    continue
                if question_id_set and item.question_id not in question_id_set:
                    continue
                if retry_failed_results and not _result_matches_target_route(
                    item,
                    targets_by_candidate_id[candidate_id],
                ):
                    continue
                latest_by_candidate.setdefault(candidate_id, {})[item.question_id] = item

            for candidate_id, latest_by_question in latest_by_candidate.items():
                completed_results = [
                    item
                    for item in latest_by_question.values()
                    if not retry_failed_results or item.error_message is None
                ]
                buckets[candidate_id] = completed_results
                completed_by_candidate[candidate_id] = len(completed_results)
                completed_steps.update(
                    (candidate_id, SCAN_PHASE, item.question_id)
                    for item in completed_results
                )
            resume_state = {
                "completed_steps": completed_steps,
                "completed_by_candidate": completed_by_candidate,
                "buckets": buckets,
                "completed_count": sum(completed_by_candidate.values()),
            }

        return {
            "active_run": active_run,
            "run_id": run_id,
            "run_history": run_history,
            "completed_count": resume_state["completed_count"],
            "completed_steps": resume_state["completed_steps"],
            "completed_by_candidate": resume_state["completed_by_candidate"],
            "buckets": resume_state["buckets"],
        }

    def infer_active_run_from_history(
        self,
        config: AppConfig,
        history: list[ScanResult],
        *,
        attempts_per_target: int | None = None,
        question_ids: list[str] | None = None,
        retry_failed_results: bool = False,
    ) -> dict[str, object] | None:
        if not history:
            return None
        current_run_id = history[-1].run_id
        run_history = [item for item in history if item.run_id == current_run_id]
        if not run_history:
            return None
        stored_metadata = self.history_store.load_run_metadata(current_run_id)
        if isinstance(stored_metadata, dict) and stored_metadata.get("completed_at"):
            retryable_terminal = (
                retry_failed_results
                and stored_metadata.get("status") in {"failed", "degraded"}
            )
            if not retryable_terminal:
                return None
        stored_question_count = (
            metadata_question_count(stored_metadata)
            if isinstance(stored_metadata, dict)
            else 0
        )
        attempts_per_target = max(
            1,
            attempts_per_target
            or stored_question_count
            or len(self._enabled_questions()),
        )
        stored_requested_ids = (
            stored_metadata.get("requested_candidate_ids")
            if isinstance(stored_metadata, dict)
            else None
        )
        if isinstance(stored_requested_ids, list) and stored_requested_ids:
            try:
                enabled_targets = self._requested_scan_targets(
                    config,
                    [str(candidate_id) for candidate_id in stored_requested_ids],
                )
            except ValueError:
                return None
        else:
            enabled_targets = self._resolved_scan_targets(config)
        candidate_ids_by_label = self._candidate_ids_by_label(enabled_targets)
        enabled_candidate_ids = [target.candidate_id for target in enabled_targets]
        targets_by_candidate_id = {
            target.candidate_id: target for target in enabled_targets
        }

        latest_by_candidate_question: dict[str, dict[str, ScanResult]] = {}
        question_id_set = set(question_ids or metadata_question_ids(stored_metadata))
        latest_by_candidate: dict[str, ScanResult] = {}
        for item in run_history:
            candidate_id = self._result_candidate_id(item, candidate_ids_by_label)
            if candidate_id is None:
                return None
            if normalize_phase(item.phase) != SCAN_PHASE:
                continue
            if question_id_set and item.question_id not in question_id_set:
                continue
            if retry_failed_results and not _result_matches_target_route(
                item,
                targets_by_candidate_id[candidate_id],
            ):
                continue
            latest_by_candidate[candidate_id] = item
            latest_by_candidate_question.setdefault(candidate_id, {})[
                item.question_id
            ] = item

        completed_by_candidate = {
            candidate_id: sum(
                1
                for item in latest_by_candidate_question.get(
                    candidate_id, {}
                ).values()
                if not retry_failed_results or item.error_message is None
            )
            for candidate_id in enabled_candidate_ids
        }
        if all(
            count >= attempts_per_target
            for count in completed_by_candidate.values()
        ):
            return None

        entries: list[dict[str, object]] = []
        for target in enabled_targets:
            attempts_completed = completed_by_candidate.get(target.candidate_id, 0)
            latest = latest_by_candidate.get(target.candidate_id)
            entries.append(
                {
                    "candidate_id": target.candidate_id,
                    "model": target.model,
                    "effort": target.effort,
                    "label": target.label,
                    "status": "done" if attempts_completed >= attempts_per_target else "interrupted",
                    "final_status": latest.final_status if latest else None,
                    "reasoning_tokens": latest.reasoning_tokens if latest else None,
                    "attempts_completed": attempts_completed,
                    "attempts_per_target": attempts_per_target,
                    "phase": SCAN_PHASE,
                    "flags": list(latest.flags) if latest else [],
                    "error_message": latest.error_message if latest else None,
                }
            )

        if isinstance(stored_metadata, dict):
            question_pack = self.question_bank.load()
            stored_profile_id = self.metadata_evaluation_profile_id(
                stored_metadata,
                question_pack,
            )
            evaluation_profile = self.evaluation_profile(
                question_pack,
                stored_profile_id,
            )
            frozen_question_ids = (
                question_ids
                or metadata_question_ids(stored_metadata)
                or list(evaluation_profile.question_ids)
            )
            inferred_metadata = self.active_run_metadata(
                run_id=current_run_id,
                status="partial",
                enabled_targets=enabled_targets,
                question_count=attempts_per_target,
                active_run={"run_metadata": stored_metadata},
                evaluation_profile=evaluation_profile,
                question_ids=list(frozen_question_ids),
            )
        else:
            inferred_metadata = RunMetadata.legacy(
                run_id=current_run_id,
                question_count=attempts_per_target,
            ).to_dict()
            inferred_metadata["status"] = "partial"
        inferred_metadata = self._preserve_legacy_selection_metadata(
            stored_metadata if isinstance(stored_metadata, dict) else {},
            inferred_metadata,
        )
        return {
            "run_id": current_run_id,
            "run_metadata": inferred_metadata,
            "planned_attempts_by_candidate": {
                target.candidate_id: attempts_per_target for target in enabled_targets
            },
            "planned_attempts": {
                target.label: attempts_per_target for target in enabled_targets
            },
            "entries": entries,
        }
