from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from .models import ScanPlan
from .scan_planner import ScanPlanningError
from .snapshot_query import SnapshotProjector

if TYPE_CHECKING:
    from .service import MonitorService


SCAN_PLAN_PREVIEW_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ScanPlanPreviewQuery:
    """Project the real scan planner result without owning execution or persistence."""

    service: MonitorService
    snapshot_projector: SnapshotProjector
    quick_candidate_ids_provider: Callable[[], list[str] | None] | None = None

    def preview_custom_options(
        self,
        *,
        requested_candidate_ids: list[str],
        evaluation_profile_id: str | None,
    ) -> dict[str, object]:
        return {
            "schema_version": SCAN_PLAN_PREVIEW_SCHEMA_VERSION,
            "new_round": self.build_preview(
                requested_candidate_ids=requested_candidate_ids,
                selection_mode="custom",
                custom_round_mode="new_round",
                evaluation_profile_id=evaluation_profile_id,
            ),
            "append": self.build_preview(
                requested_candidate_ids=requested_candidate_ids,
                selection_mode="custom",
                custom_round_mode="append",
                evaluation_profile_id=evaluation_profile_id,
            ),
        }

    def build_preview(
        self,
        *,
        force_restart: bool = False,
        requested_candidate_ids: list[str] | None = None,
        selection_mode: str = "regular",
        custom_round_mode: str = "new_round",
        evaluation_profile_id: str | None = None,
        upgrade_from_run_id: str | None = None,
    ) -> dict[str, object]:
        try:
            requested_candidate_ids = self._resolve_regular_quick_candidate_ids(
                requested_candidate_ids=requested_candidate_ids,
                selection_mode=selection_mode,
                evaluation_profile_id=evaluation_profile_id,
            )
            plan = self.service.plan_scan(
                force_restart=force_restart,
                requested_candidate_ids=requested_candidate_ids,
                selection_mode=selection_mode,
                custom_round_mode=custom_round_mode,
                evaluation_profile_id=evaluation_profile_id,
                upgrade_from_run_id=upgrade_from_run_id,
            )
        except ValueError as exc:
            return self._invalid_preview(
                error=exc,
                requested_candidate_ids=requested_candidate_ids,
                selection_mode=selection_mode,
                custom_round_mode=custom_round_mode,
                evaluation_profile_id=evaluation_profile_id,
            )
        return self._project_plan(
            plan,
            requested_selection_mode=selection_mode,
            requested_custom_round_mode=custom_round_mode,
        )

    def _resolve_regular_quick_candidate_ids(
        self,
        *,
        requested_candidate_ids: list[str] | None,
        selection_mode: str,
        evaluation_profile_id: str | None,
    ) -> list[str] | None:
        if requested_candidate_ids is not None or selection_mode != "regular":
            return requested_candidate_ids
        profile = self.service.scan_planner.evaluation_profile(
            self.service.question_bank.load(),
            evaluation_profile_id,
        )
        if profile.id != "quick":
            return None
        candidate_ids = (
            self.quick_candidate_ids_provider()
            if self.quick_candidate_ids_provider is not None
            else self._quick_candidate_ids_from_snapshot()
        )
        normalized = list(dict.fromkeys(candidate_ids or []))
        if len(normalized) != 2:
            raise ScanPlanningError(
                "quick_recommendation_pair_unavailable",
                "暂无唯一可用的建议配置，请在“自定义本轮”中选择两个配置",
            )
        return normalized

    def _quick_candidate_ids_from_snapshot(self) -> list[str] | None:
        from .reference_snapshot import read_reference_snapshot_feed_for_app

        data_dir = self.service.history_store.path.parent
        state, _ = self.snapshot_projector.project(
            reference_snapshot_feed=read_reference_snapshot_feed_for_app(
                cache_root=data_dir / "reference_snapshots",
            ),
            codex_insights=None,
        )
        portfolio = state.get("recommendation_portfolio_v2")
        if not isinstance(portfolio, dict):
            return None
        current_id = _optional_string(
            portfolio.get("representative_configuration_id")
        )
        decisions = portfolio.get("decisions")
        decision_items = (
            [item for item in decisions if isinstance(item, dict)]
            if isinstance(decisions, list)
            else []
        )
        representative = next(
            (
                item
                for item in decision_items
                if _optional_string(item.get("current_model_configuration_id"))
                == current_id
            ),
            decision_items[0] if decision_items else None,
        )
        recommended_id = (
            _optional_string(representative.get("candidate_model_configuration_id"))
            if representative is not None
            else None
        )
        if not current_id or not recommended_id or current_id == recommended_id:
            return None
        enabled_candidate_ids = {
            target.candidate_id
            for target in self.service.scan_target_resolver.enabled_targets(
                self.service.load_config()
            )
        }
        if not {current_id, recommended_id}.issubset(enabled_candidate_ids):
            return None
        return [current_id, recommended_id]

    @staticmethod
    def _project_plan(
        plan: ScanPlan,
        *,
        requested_selection_mode: str,
        requested_custom_round_mode: str,
    ) -> dict[str, object]:
        metadata = plan.run_metadata
        comparison_group_id = _optional_string(
            metadata.get("comparison_group_id")
        )
        if comparison_group_id == plan.run_id:
            comparison_group_id = None
        return {
            "schema_version": SCAN_PLAN_PREVIEW_SCHEMA_VERSION,
            "valid": True,
            "reason": None,
            "message": None,
            "requested_selection_mode": requested_selection_mode,
            "requested_custom_round_mode": requested_custom_round_mode,
            "execution_selection_mode": plan.execution_selection_mode,
            "execution_custom_round_mode": plan.execution_custom_round_mode,
            "profile": {
                "id": plan.evaluation_profile_id,
                "label": plan.evaluation_profile_label,
                "question_count": plan.question_count,
            },
            "requested_candidate_ids": list(plan.requested_candidate_ids or ()),
            "effective_candidate_ids": list(
                plan.effective_requested_candidate_ids
            ),
            "execution_candidate_ids": [
                target.candidate_id for target in plan.enabled_targets
            ],
            "regular_candidate_ids": list(plan.regular_candidate_ids),
            "appended_candidate_ids": _string_list(
                metadata.get("appended_candidate_ids")
            ),
            "skipped_candidate_ids": _string_list(
                metadata.get("skipped_candidate_ids")
            ),
            "comparison_group": {
                "id": comparison_group_id,
                "mode": _optional_string(metadata.get("comparison_group_mode")),
                "parent_run_id": _optional_string(
                    metadata.get("comparison_parent_run_id")
                ),
                "append_target_group_id": _optional_string(
                    metadata.get("append_target_group_id")
                ),
            },
            "total_evaluations": plan.total_targets,
            "completed_evaluations": plan.completed_targets,
        }

    def _invalid_preview(
        self,
        *,
        error: ValueError,
        requested_candidate_ids: list[str] | None,
        selection_mode: str,
        custom_round_mode: str,
        evaluation_profile_id: str | None,
    ) -> dict[str, object]:
        message = str(error)
        return {
            "schema_version": SCAN_PLAN_PREVIEW_SCHEMA_VERSION,
            "valid": False,
            "reason": (
                error.reason
                if isinstance(error, ScanPlanningError)
                else "invalid_scan_plan"
            ),
            "message": message,
            "requested_selection_mode": selection_mode,
            "requested_custom_round_mode": custom_round_mode,
            "execution_selection_mode": None,
            "execution_custom_round_mode": None,
            "profile": self._requested_profile(
                evaluation_profile_id
                or ("full" if selection_mode == "incremental_full" else None)
            ),
            "requested_candidate_ids": list(requested_candidate_ids or []),
            "effective_candidate_ids": [],
            "execution_candidate_ids": [],
            "regular_candidate_ids": [],
            "appended_candidate_ids": [],
            "skipped_candidate_ids": [],
            "comparison_group": {
                "id": None,
                "mode": None,
                "parent_run_id": None,
                "append_target_group_id": None,
            },
            "total_evaluations": 0,
            "completed_evaluations": 0,
        }

    def _requested_profile(
        self,
        evaluation_profile_id: str | None,
    ) -> dict[str, object]:
        try:
            profile = self.service.scan_planner.evaluation_profile(
                self.service.question_bank.load(),
                evaluation_profile_id,
            )
        except ValueError:
            return {
                "id": evaluation_profile_id,
                "label": None,
                "question_count": None,
            }
        return {
            "id": profile.id,
            "label": profile.label,
            "question_count": len(profile.question_ids),
        }


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value]
