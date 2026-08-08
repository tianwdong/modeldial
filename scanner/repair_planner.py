from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .active_run_store import ActiveRunStore
from .comparison_groups import ComparisonGroupProjector
from .config_store import ConfigStore
from .history_store import HistoryStore
from .legacy_scan_compat import (
    SCAN_PHASE,
    is_active_lifecycle,
    metadata_question_count,
    metadata_question_ids,
    normalize_phase,
)
from .models import AppConfig, ResolvedScanTarget, ScanResult
from .question_bank import (
    EvaluationProfileSpec,
    QuestionBank,
    QuestionCollection,
    QuestionSpec,
)
from .scan_target_resolver import ScanTargetResolver
from .scoring import EQUAL_SCORING_MODE


@dataclass(frozen=True)
class RepairPlan:
    requested_run_id: str
    requested_group_id: str
    persist_run_id: str
    operation_kind: str
    group_member_run_ids: tuple[str, ...]
    selected_candidate_ids: tuple[str, ...]
    candidate_id: str | None
    question_id: str | None
    completion_only: bool
    is_matching_repair: bool
    config: AppConfig = field(repr=False, compare=False)
    history: tuple[ScanResult, ...] = field(repr=False, compare=False)
    persisted_active: dict[str, object] | None = field(repr=False, compare=False)
    metadata: dict[str, object] = field(repr=False, compare=False)
    persisted_run_metadata: dict[str, object] = field(repr=False, compare=False)
    question_pack: QuestionCollection = field(repr=False, compare=False)
    questions: tuple[QuestionSpec, ...] = field(repr=False, compare=False)
    all_targets: tuple[ResolvedScanTarget, ...] = field(repr=False, compare=False)
    selected_targets: tuple[ResolvedScanTarget, ...] = field(
        repr=False,
        compare=False,
    )
    repair_steps_by_candidate: tuple[
        tuple[str, tuple[QuestionSpec, ...]], ...
    ] = field(repr=False, compare=False)
    latest_results_by_candidate: tuple[
        tuple[str, tuple[ScanResult, ...]], ...
    ] = field(repr=False, compare=False)
    completed_by_candidate: tuple[tuple[str, int], ...] = field(
        repr=False,
        compare=False,
    )

    @property
    def total_steps(self) -> int:
        return sum(len(steps) for _, steps in self.repair_steps_by_candidate)

    def steps_for(self, candidate_id: str) -> tuple[QuestionSpec, ...]:
        return next(
            (
                steps
                for planned_candidate_id, steps in self.repair_steps_by_candidate
                if planned_candidate_id == candidate_id
            ),
            (),
        )

    def latest_by_question(self, candidate_id: str) -> dict[str, ScanResult]:
        results = next(
            (
                items
                for planned_candidate_id, items in self.latest_results_by_candidate
                if planned_candidate_id == candidate_id
            ),
            (),
        )
        return {result.question_id: result for result in results}


@dataclass(frozen=True)
class _RoundContext:
    config: AppConfig
    history: tuple[ScanResult, ...]
    requested_group_id: str
    persist_run_id: str
    group_member_run_ids: tuple[str, ...]
    metadata: dict[str, object]


class RepairPlanner:
    """Build repair execution plans without owning runtime or writing stores."""

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
        self.target_resolver = target_resolver
        self.comparison_group_projector = comparison_group_projector

    def plan_candidate(
        self,
        *,
        run_id: str,
        candidate_id: str,
        question_id: str | None = None,
    ) -> RepairPlan:
        persisted_active = self.active_run_store.load()
        persisted_lifecycle = str(
            ((persisted_active or {}).get("runtime") or {}).get("lifecycle_state")
            if isinstance((persisted_active or {}).get("runtime"), dict)
            else ""
        )
        is_matching_repair = bool(
            persisted_active
            and str(persisted_active.get("repair_run_id") or "") == run_id
            and str(persisted_active.get("repair_candidate_id") or "")
            == candidate_id
        )
        if is_active_lifecycle(persisted_lifecycle) and not is_matching_repair:
            raise ValueError("当前已有扫描正在运行")

        context = self._latest_round(
            run_id=run_id,
            empty_error="只能修复当前页面展示的最新一轮",
            stale_error="只能修复当前页面展示的最新一轮",
        )
        metadata = context.metadata
        if str(metadata.get("selection_mode") or "") not in {"regular", "custom"}:
            raise ValueError("仅常规扫描或自选扫描支持重试失败题")
        requested_candidate_ids = tuple(
            str(item) for item in metadata.get("requested_candidate_ids", [])
        )
        if candidate_id not in requested_candidate_ids:
            raise ValueError("该模型不在本轮扫描范围内")

        question_pack = self.question_bank.load()
        self._require_matching_question_pack(metadata, question_pack)
        target = self.target_resolver.requested_targets(
            context.config,
            [candidate_id],
        )[0]
        current_targets_by_id = {
            item.candidate_id: item
            for item in self.target_resolver.enabled_targets(context.config)
        }
        current_targets_by_id[target.candidate_id] = target
        all_targets = tuple(
            current_targets_by_id[item]
            for item in requested_candidate_ids
            if item in current_targets_by_id
        )
        questions = tuple(self._round_questions(metadata, question_pack))
        required_question_ids = {question.id for question in questions}
        group_run_id_set = set(context.group_member_run_ids)
        candidate_ids_by_label = self.target_resolver.candidate_ids_by_label([target])
        latest_by_question: dict[str, ScanResult] = {}
        for item in context.history:
            if item.run_id not in group_run_id_set:
                continue
            if (
                self.target_resolver.result_candidate_id(
                    item,
                    candidate_ids_by_label,
                )
                != candidate_id
            ):
                continue
            if (
                normalize_phase(item.phase) == SCAN_PHASE
                and item.question_id in required_question_ids
            ):
                latest_by_question[item.question_id] = item

        repair_steps = [
            question
            for question in questions
            if question.id not in latest_by_question
            or latest_by_question[question.id].error_message is not None
        ]
        if is_matching_repair and question_id is None:
            persisted_question_ids = (persisted_active or {}).get(
                "repair_question_ids"
            )
            if isinstance(persisted_question_ids, list):
                remaining_question_ids = {
                    str(item) for item in persisted_question_ids
                }
                repair_steps = [
                    question
                    for question in repair_steps
                    if question.id in remaining_question_ids
                ]
        if question_id is not None:
            if question_id not in required_question_ids:
                raise ValueError("该题目不在本轮评测范围内")
            latest_result = latest_by_question.get(question_id)
            if latest_result is not None and latest_result.error_message is None:
                raise ValueError("该题目不是执行失败，不能单独重试")
            repair_steps = [
                question for question in repair_steps if question.id == question_id
            ]
        completion_only = is_matching_repair and not repair_steps
        if not repair_steps and not completion_only:
            raise ValueError("当前模型没有可重试的硬失败题")

        comparison_state = self.comparison_group_projector.result_state(
            history=list(context.history),
            run_ids=list(context.group_member_run_ids),
            enabled_targets=list(all_targets),
            question_ids=[question.id for question in questions],
        )
        persisted_run_metadata = dict(
            self.history_store.load_run_metadata(context.persist_run_id) or metadata
        )
        return RepairPlan(
            requested_run_id=run_id,
            requested_group_id=context.requested_group_id,
            persist_run_id=context.persist_run_id,
            operation_kind="candidate_repair",
            group_member_run_ids=context.group_member_run_ids,
            selected_candidate_ids=(candidate_id,),
            candidate_id=candidate_id,
            question_id=question_id,
            completion_only=completion_only,
            is_matching_repair=is_matching_repair,
            config=context.config,
            history=context.history,
            persisted_active=persisted_active,
            metadata=dict(metadata),
            persisted_run_metadata=persisted_run_metadata,
            question_pack=question_pack,
            questions=questions,
            all_targets=all_targets,
            selected_targets=(target,),
            repair_steps_by_candidate=(
                (candidate_id, tuple(repair_steps)),
            ),
            latest_results_by_candidate=(
                (candidate_id, tuple(latest_by_question.values())),
            ),
            completed_by_candidate=tuple(
                (
                    str(planned_candidate_id),
                    int(completed_count),
                )
                for planned_candidate_id, completed_count in dict(
                    comparison_state["completed_by_candidate"]
                ).items()
            ),
        )

    def plan_failed_batch(
        self,
        *,
        run_id: str,
        candidate_ids: list[str] | None = None,
    ) -> RepairPlan:
        return self._plan_batch(
            run_id=run_id,
            candidate_ids=candidate_ids,
            operation_kind="failed_repair",
            repair_label="失败题",
            should_repair=lambda result: (
                result is None or result.error_message is not None
            ),
        )

    def plan_timeout_batch(
        self,
        *,
        run_id: str,
        candidate_ids: list[str] | None = None,
    ) -> RepairPlan:
        return self._plan_batch(
            run_id=run_id,
            candidate_ids=candidate_ids,
            operation_kind="timeout_repair",
            repair_label="超时题",
            should_repair=lambda result: (
                result is not None and is_timeout_result(result)
            ),
        )

    def _plan_batch(
        self,
        *,
        run_id: str,
        candidate_ids: list[str] | None,
        operation_kind: str,
        repair_label: str,
        should_repair: Callable[[ScanResult | None], bool],
    ) -> RepairPlan:
        persisted_active = self.active_run_store.load()
        persisted_runtime = (
            (persisted_active or {}).get("runtime")
            if isinstance((persisted_active or {}).get("runtime"), dict)
            else {}
        )
        if is_active_lifecycle((persisted_runtime or {}).get("lifecycle_state")):
            raise ValueError("当前已有扫描正在运行")
        context = self._latest_round(
            run_id=run_id,
            empty_error="只能重试当前页面展示的最新一轮",
            stale_error="只能重试当前页面展示的最新一轮",
        )
        metadata = context.metadata
        if str(metadata.get("selection_mode") or "") not in {"regular", "custom"}:
            raise ValueError(f"仅常规扫描或自选扫描支持重试{repair_label}")
        if str(metadata.get("scoring_mode") or "") != EQUAL_SCORING_MODE:
            raise ValueError(f"仅五题等权模式支持批量重试{repair_label}")

        requested_candidate_ids = tuple(
            str(item) for item in metadata.get("requested_candidate_ids", [])
        )
        selected_candidate_ids = tuple(
            dict.fromkeys(candidate_ids or requested_candidate_ids)
        )
        if not selected_candidate_ids:
            raise ValueError(f"当前没有可重试的{repair_label}")
        if any(
            candidate_id not in requested_candidate_ids
            for candidate_id in selected_candidate_ids
        ):
            raise ValueError("所选模型不在本轮扫描范围内")

        question_pack = self.question_bank.load()
        self._require_matching_question_pack(metadata, question_pack)
        all_targets = tuple(
            self.target_resolver.requested_targets(
                context.config,
                list(requested_candidate_ids),
            )
        )
        target_by_id = {target.candidate_id: target for target in all_targets}
        selected_targets = tuple(
            target_by_id[item] for item in selected_candidate_ids
        )
        questions = tuple(self._round_questions(metadata, question_pack))
        required_question_ids = {question.id for question in questions}
        candidate_ids_by_label = self.target_resolver.candidate_ids_by_label(
            list(all_targets)
        )
        latest_by_candidate_question: dict[str, dict[str, ScanResult]] = {}
        group_run_id_set = set(context.group_member_run_ids)
        for item in context.history:
            if item.run_id not in group_run_id_set:
                continue
            candidate_id = self.target_resolver.result_candidate_id(
                item,
                candidate_ids_by_label,
            )
            if candidate_id not in selected_candidate_ids:
                continue
            if (
                normalize_phase(item.phase) == SCAN_PHASE
                and item.question_id in required_question_ids
            ):
                latest_by_candidate_question.setdefault(
                    str(candidate_id),
                    {},
                )[item.question_id] = item

        repair_steps_by_candidate: list[
            tuple[str, tuple[QuestionSpec, ...]]
        ] = []
        for target in selected_targets:
            latest_by_question = latest_by_candidate_question.get(
                target.candidate_id,
                {},
            )
            repair_steps = tuple(
                question
                for question in questions
                if should_repair(latest_by_question.get(question.id))
            )
            if repair_steps:
                repair_steps_by_candidate.append(
                    (target.candidate_id, repair_steps)
                )
        if not repair_steps_by_candidate:
            raise ValueError(f"当前没有可重试的{repair_label}")

        comparison_state = self.comparison_group_projector.result_state(
            history=list(context.history),
            run_ids=list(context.group_member_run_ids),
            enabled_targets=list(all_targets),
            question_ids=[question.id for question in questions],
        )
        persisted_run_metadata = dict(
            self.history_store.load_run_metadata(context.persist_run_id) or metadata
        )
        return RepairPlan(
            requested_run_id=run_id,
            requested_group_id=context.requested_group_id,
            persist_run_id=context.persist_run_id,
            operation_kind=operation_kind,
            group_member_run_ids=context.group_member_run_ids,
            selected_candidate_ids=selected_candidate_ids,
            candidate_id=None,
            question_id=None,
            completion_only=False,
            is_matching_repair=False,
            config=context.config,
            history=context.history,
            persisted_active=persisted_active,
            metadata=dict(metadata),
            persisted_run_metadata=persisted_run_metadata,
            question_pack=question_pack,
            questions=questions,
            all_targets=all_targets,
            selected_targets=selected_targets,
            repair_steps_by_candidate=tuple(repair_steps_by_candidate),
            latest_results_by_candidate=tuple(
                (
                    candidate_id,
                    tuple(latest_by_question.values()),
                )
                for candidate_id, latest_by_question
                in latest_by_candidate_question.items()
            ),
            completed_by_candidate=tuple(
                (
                    str(planned_candidate_id),
                    int(completed_count),
                )
                for planned_candidate_id, completed_count in dict(
                    comparison_state["completed_by_candidate"]
                ).items()
            ),
        )

    def _latest_round(
        self,
        *,
        run_id: str,
        empty_error: str,
        stale_error: str,
    ) -> _RoundContext:
        config = self.config_store.load()
        history = self.history_store.load_all()
        if not history:
            raise ValueError(empty_error)
        run_metadata_by_id = self.history_store.load_run_metadata_map()
        run_metadata_by_id = (
            self.comparison_group_projector.dashboard_overlay_metadata_by_run_id(
                history=history,
                run_metadata_by_id=run_metadata_by_id,
                current_run_id=history[-1].run_id,
            )
        )
        latest_group_id = (
            self.comparison_group_projector.latest_appendable_group_id(
                history,
                run_metadata_by_id,
            )
            or self.comparison_group_projector.group_id(
                history[-1].run_id,
                run_metadata_by_id.get(history[-1].run_id),
            )
        )
        requested_group_id = self.comparison_group_projector.group_id(
            run_id,
            run_metadata_by_id.get(run_id),
        )
        if not latest_group_id or requested_group_id != latest_group_id:
            raise ValueError(stale_error)
        assert requested_group_id is not None
        group_member_run_ids = self.comparison_group_projector.member_run_ids(
            group_id=requested_group_id,
            history=history,
            run_metadata_by_id=run_metadata_by_id,
        )
        if not group_member_run_ids:
            group_member_run_ids = [history[-1].run_id]
        persist_run_id = group_member_run_ids[-1]
        metadata = self.comparison_group_projector.aggregate_metadata(
            group_id=requested_group_id,
            history=history,
            run_metadata_by_id=run_metadata_by_id,
            member_run_ids=group_member_run_ids,
        )
        if metadata is None:
            raise ValueError("找不到本轮扫描信息")
        return _RoundContext(
            config=config,
            history=tuple(history),
            requested_group_id=requested_group_id,
            persist_run_id=persist_run_id,
            group_member_run_ids=tuple(group_member_run_ids),
            metadata=metadata,
        )

    @staticmethod
    def _require_matching_question_pack(
        metadata: dict[str, object],
        question_pack: QuestionCollection,
    ) -> None:
        if (
            str(metadata.get("question_pack_id") or "unknown")
            != question_pack.metadata.question_pack_id
            or str(metadata.get("question_pack_version") or "unknown")
            != question_pack.metadata.question_pack_version
        ):
            raise ValueError("题包已变化，请运行全量扫描")

    @classmethod
    def _round_questions(
        cls,
        metadata: dict[str, object],
        question_pack: QuestionCollection,
    ) -> list[QuestionSpec]:
        profile_id = cls._metadata_evaluation_profile_id(metadata, question_pack)
        evaluation_profile = cls._evaluation_profile(question_pack, profile_id)
        return question_pack.questions_for_ids(
            metadata_question_ids(metadata)
            or list(evaluation_profile.question_ids)
        )

    @staticmethod
    def _evaluation_profile(
        question_pack: QuestionCollection,
        evaluation_profile_id: str | None,
    ) -> EvaluationProfileSpec:
        if evaluation_profile_id is None or evaluation_profile_id == "legacy_full":
            return question_pack.complete_evaluation_profile
        return question_pack.evaluation_profile(evaluation_profile_id)

    @staticmethod
    def _metadata_evaluation_profile_id(
        metadata: dict[str, object] | None,
        question_pack: QuestionCollection,
    ) -> str | None:
        if not isinstance(metadata, dict):
            return None
        explicit_profile_id = str(
            metadata.get("evaluation_profile_id") or ""
        ).strip()
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


def is_timeout_result(result: ScanResult) -> bool:
    error_message = (result.error_message or "").lower()
    return (
        result.final_status == "timeout"
        or "timeout" in error_message
        or "timed out" in error_message
    )
