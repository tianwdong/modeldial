from __future__ import annotations

from dataclasses import dataclass

from .active_run_store import ActiveRunStore
from .analytics import build_dashboard_summary
from .comparison_groups import ComparisonGroupProjector
from .config_store import ConfigStore
from .costing import current_pricing_snapshot_id
from .current_model_context import CurrentModelContextQuery
from .history_store import HistoryStore
from .legacy_scan_compat import (
    SCAN_PHASE,
    metadata_question_ids,
    normalized_metadata_projection,
    normalize_phase,
)
from .local_provider_detection import detected_local_provider_payload
from .models import AppConfig, RunMetadata, ScanResult
from .provider_catalog import provider_catalog_payload
from .question_bank import QuestionBank, QuestionCollection
from .runtime_snapshot_projection import RuntimeSnapshotProjector
from .scan_planner import ScanPlanner


@dataclass(frozen=True)
class MonitorStateProjector:
    config_store: ConfigStore
    history_store: HistoryStore
    active_run_store: ActiveRunStore
    question_bank: QuestionBank
    current_model_context_query: CurrentModelContextQuery
    runtime_snapshot_projector: RuntimeSnapshotProjector
    scan_planner: ScanPlanner
    comparison_group_projector: ComparisonGroupProjector

    def build_refresh_state(self) -> dict[str, object]:
        config = self.config_store.load()
        question_pack = self.question_bank.load()
        current_model = self.current_model_context_query.build(config)
        history, history_count = self.history_store.load_recent_with_count(
            limit=500
        )
        active_run = self.active_run_store.load()
        if active_run is None:
            active_run = self.scan_planner.infer_active_run_from_history(
                config,
                history,
            )
        return {
            "config": self._snapshot_config_payload(config, current_model),
            "question_pack": self._question_pack_payload(question_pack),
            "runtime": self.runtime_snapshot_projector.project(
                config,
                history,
                active_run,
                history_count=history_count,
            ),
        }

    def build_state(self) -> dict[str, object]:
        config = self.config_store.load()
        current_model = self.current_model_context_query.build(config)
        effective_current_candidate_id = current_model["effective_candidate_id"]
        history = self.history_store.load_all()
        recent_history = history[-max(config.system.history_limit, 100) :]
        active_run = self.active_run_store.load()
        if active_run is None:
            active_run = self.scan_planner.infer_active_run_from_history(
                config,
                history,
            )
        runtime = self.runtime_snapshot_projector.project(
            config,
            history,
            active_run,
        )
        current_run_id = runtime["current_run_id"] or (
            history[-1].run_id if history else None
        )
        historical_run_metadata_by_id = (
            self.history_store.load_run_metadata_map()
        )
        raw_run_metadata_by_id = dict(historical_run_metadata_by_id)
        active_metadata = (active_run or {}).get("run_metadata")
        if current_run_id and isinstance(active_metadata, dict):
            raw_run_metadata_by_id[str(current_run_id)] = {
                **raw_run_metadata_by_id.get(str(current_run_id), {}),
                **active_metadata,
            }
        dashboard_run_metadata_by_id = (
            self.comparison_group_projector.dashboard_overlay_metadata_by_run_id(
                history=history,
                run_metadata_by_id=raw_run_metadata_by_id,
                current_run_id=(
                    str(current_run_id) if current_run_id else None
                ),
            )
        )
        dashboard_seed_run_id = self._dashboard_seed_run_id(
            history=history,
            run_metadata_by_id=dashboard_run_metadata_by_id,
            current_run_id=str(current_run_id) if current_run_id else None,
        )
        (
            dashboard_history,
            dashboard_current_run_id,
            dashboard_run_metadata_by_id,
        ) = self.dashboard_history_context(
            history=history,
            run_metadata_by_id=dashboard_run_metadata_by_id,
            current_run_id=dashboard_seed_run_id,
        )
        run_metadata = self.dashboard_run_metadata(
            config=config,
            history=dashboard_history,
            run_metadata_by_id=dashboard_run_metadata_by_id,
            current_run_id=dashboard_current_run_id,
        )
        current_question_pack = self.question_bank.load()
        if not metadata_question_ids(run_metadata):
            run_metadata["question_ids"] = list(
                current_question_pack.complete_evaluation_profile.question_ids
            )
        if dashboard_current_run_id:
            dashboard_run_metadata_by_id[str(dashboard_current_run_id)] = {
                **dashboard_run_metadata_by_id.get(
                    str(dashboard_current_run_id),
                    {},
                ),
                **run_metadata,
            }
        dashboard = build_dashboard_summary(
            dashboard_history,
            config.model_ingress,
            current_run_id=dashboard_current_run_id,
            active_run=active_run,
            run_metadata=run_metadata,
            scan_interval_seconds=config.scheduler.interval_seconds,
            run_metadata_by_id=dashboard_run_metadata_by_id,
            current_default_candidate_id=(
                str(effective_current_candidate_id)
                if effective_current_candidate_id
                else None
            ),
            scan_budget=config.scan_budget,
            current_question_pack_id=(
                current_question_pack.metadata.question_pack_id
            ),
            current_question_pack_version=(
                current_question_pack.metadata.question_pack_version
            ),
        )
        stable_dashboard = None
        if not (
            str(run_metadata.get("status") or "") == "completed"
            and bool(run_metadata.get("is_complete_regular_round"))
        ):
            stable_dashboard = self._stable_dashboard(
                config=config,
                history=history,
                run_metadata_by_id=historical_run_metadata_by_id,
                current_question_pack=current_question_pack,
                current_default_candidate_id=(
                    str(effective_current_candidate_id)
                    if effective_current_candidate_id
                    else None
                ),
            )
        stable_evidence_dashboard = None
        if not (
            str(run_metadata.get("status") or "") == "completed"
            and str(run_metadata.get("evaluation_result_level") or "")
            == "complete"
        ):
            stable_evidence_dashboard = self._stable_evidence_dashboard(
                config=config,
                history=history,
                run_metadata_by_id=historical_run_metadata_by_id,
                current_question_pack=current_question_pack,
                current_default_candidate_id=(
                    str(effective_current_candidate_id)
                    if effective_current_candidate_id
                    else None
                ),
            )
        state = {
            "config": self._snapshot_config_payload(config, current_model),
            "question_pack": self._question_pack_payload(
                current_question_pack
            ),
            "history": [item.to_dict() for item in recent_history],
            "dashboard": dashboard,
            "runtime": runtime,
        }
        if stable_dashboard is not None:
            state["stable_dashboard"] = stable_dashboard
        if stable_evidence_dashboard is not None:
            state["stable_evidence_dashboard"] = stable_evidence_dashboard
        return state

    @staticmethod
    def _question_pack_payload(
        question_pack: QuestionCollection,
    ) -> dict[str, object]:
        return {
            "id": question_pack.metadata.question_pack_id,
            "version": question_pack.metadata.question_pack_version,
            "question_count": question_pack.question_count,
            "questions": [
                {
                    "id": question.id,
                    "question_number": index,
                    "title": question.title,
                    "capability_id": question.capability_id,
                    "capability_label": question.capability_label,
                    "detail_label": question.detail_label,
                    "score_max": max(
                        1,
                        int(question.grader.payload.get("max_score") or 1),
                    ),
                }
                for index, question in enumerate(
                    question_pack.enabled_questions,
                    start=1,
                )
            ],
            "default_evaluation_profile_id": (
                question_pack.default_evaluation_profile_id
            ),
            "evaluation_profiles": [
                profile.to_dict()
                for profile in question_pack.evaluation_profiles
            ],
        }

    @staticmethod
    def _snapshot_config_payload(
        config: AppConfig,
        current_model: dict[str, object],
    ) -> dict[str, object]:
        config_payload = config.to_dict()
        config_payload["provider_catalog"] = provider_catalog_payload()
        config_payload["detected_local_providers"] = (
            detected_local_provider_payload()
        )
        recommendation_payload = config_payload["recommendation"]
        assert isinstance(recommendation_payload, dict)
        recommendation_payload.update(
            {
                "effective_current_candidate_id": current_model[
                    "effective_candidate_id"
                ],
                "current_model_source": current_model["source"],
                "current_model_detection_status": current_model[
                    "detection_status"
                ],
                "current_model_detected_at": current_model["detected_at"],
                "detected_current_model": current_model["model"],
                "detected_current_effort": current_model["effort"],
                "detected_active_session_count": current_model[
                    "active_session_count"
                ],
                "detected_active_models": current_model["active_models"],
                "detected_active_sessions": current_model[
                    "active_sessions"
                ],
                "active_model_sessions": current_model["display_sessions"],
                "active_configuration_sessions": current_model.get(
                    "active_configuration_sessions",
                    [],
                ),
            }
        )
        return config_payload

    def _dashboard_seed_run_id(
        self,
        *,
        history: list[ScanResult],
        run_metadata_by_id: dict[str, dict[str, object]],
        current_run_id: str | None,
    ) -> str | None:
        if not current_run_id:
            return None
        current_metadata = run_metadata_by_id.get(current_run_id)
        if not isinstance(current_metadata, dict):
            return current_run_id
        selection_mode = str(current_metadata.get("selection_mode") or "")
        group_mode = str(
            current_metadata.get("comparison_group_mode") or selection_mode
        )
        if selection_mode != "single" and group_mode != "single":
            return current_run_id
        fallback_history = [
            item for item in history if item.run_id != current_run_id
        ]
        if not fallback_history:
            return current_run_id
        fallback_metadata_by_id = {
            run_id: dict(metadata)
            for run_id, metadata in run_metadata_by_id.items()
            if run_id != current_run_id and isinstance(metadata, dict)
        }
        fallback_group_id = (
            self.comparison_group_projector.latest_appendable_group_id(
                fallback_history,
                fallback_metadata_by_id,
            )
        )
        return fallback_group_id or current_run_id

    def dashboard_history_context(
        self,
        *,
        history: list[ScanResult],
        run_metadata_by_id: dict[str, dict[str, object]],
        current_run_id: str | None,
    ) -> tuple[list[ScanResult], str | None, dict[str, dict[str, object]]]:
        grouped_history: dict[str, list[ScanResult]] = {}
        group_to_run_ids: dict[str, list[str]] = {}
        first_index_by_group: dict[str, int] = {}
        last_index_by_group: dict[str, int] = {}
        append_group_ids: set[str] = set()
        repriced_group_ids = {
            group_id
            for run_id, metadata in run_metadata_by_id.items()
            if str(metadata.get("comparison_group_mode") or "")
            == "incremental_full"
            if (
                group_id := self.comparison_group_projector.group_id(
                    run_id,
                    metadata,
                )
            )
            is not None
        }
        for index, item in enumerate(history):
            raw_metadata = run_metadata_by_id.get(item.run_id)
            group_id = self.comparison_group_projector.group_id(
                item.run_id,
                raw_metadata,
            )
            if group_id is None:
                continue
            first_index_by_group.setdefault(group_id, index)
            last_index_by_group[group_id] = index
            run_ids = group_to_run_ids.setdefault(group_id, [])
            if item.run_id not in run_ids:
                run_ids.append(item.run_id)
            if (
                str((raw_metadata or {}).get("comparison_group_mode") or "")
                == "custom_append"
            ):
                append_group_ids.add(group_id)
            reprice_from_usage = group_id in repriced_group_ids
            grouped_history.setdefault(group_id, []).append(
                ScanResult(
                    run_id=group_id,
                    phase=item.phase,
                    candidate_id=item.candidate_id,
                    model=item.model,
                    effort=item.effort,
                    question_id=item.question_id,
                    question_title=item.question_title,
                    capability_id=item.capability_id,
                    capability_label=item.capability_label,
                    detail_label=item.detail_label,
                    grader_kind=item.grader_kind,
                    attempt_index=item.attempt_index,
                    started_at=item.started_at,
                    elapsed_seconds=item.elapsed_seconds,
                    source_mode=item.source_mode,
                    answer_ok=item.answer_ok,
                    answer_preview=item.answer_preview,
                    input_tokens=item.input_tokens,
                    cached_input_tokens=item.cached_input_tokens,
                    cache_write_input_tokens=item.cache_write_input_tokens,
                    output_tokens=item.output_tokens,
                    reasoning_tokens=item.reasoning_tokens,
                    reasoning_tokens_supported=item.reasoning_tokens_supported,
                    reference_cost_usd=(
                        None
                        if reprice_from_usage
                        else item.reference_cost_usd
                    ),
                    cost_status=(
                        "unavailable"
                        if reprice_from_usage
                        else item.cost_status
                    ),
                    pricing_snapshot=(
                        current_pricing_snapshot_id()
                        if reprice_from_usage
                        else item.pricing_snapshot
                    ),
                    error_message=item.error_message,
                    scorer_reason=item.scorer_reason,
                    scorer_diagnostics=dict(item.scorer_diagnostics),
                    expected_summary=item.expected_summary,
                    actual_summary=item.actual_summary,
                    retry_index=item.retry_index,
                    flags=list(item.flags),
                    final_status=item.final_status,
                    evaluation_id=item.evaluation_id,
                    execution_trace=dict(item.execution_trace),
                )
            )
        dashboard_current_run_id = self.comparison_group_projector.group_id(
            current_run_id,
            run_metadata_by_id.get(current_run_id or ""),
        )
        ordered_group_ids = sorted(
            grouped_history,
            key=lambda group_id: (
                last_index_by_group[group_id]
                if group_id in append_group_ids
                else first_index_by_group[group_id],
                first_index_by_group[group_id],
            ),
        )
        dashboard_history = [
            item
            for group_id in ordered_group_ids
            for item in grouped_history[group_id]
        ]
        if dashboard_current_run_id is None and ordered_group_ids:
            dashboard_current_run_id = ordered_group_ids[-1]
        metadata_by_group: dict[str, dict[str, object]] = {}
        all_group_ids = list(group_to_run_ids.keys())
        for run_id, metadata in run_metadata_by_id.items():
            group_id = self.comparison_group_projector.group_id(
                run_id,
                metadata,
            )
            if group_id is None:
                continue
            if group_id not in all_group_ids:
                all_group_ids.append(group_id)
            run_ids = group_to_run_ids.setdefault(group_id, [])
            if run_id not in run_ids:
                run_ids.append(run_id)
        for group_id in all_group_ids:
            aggregated = self.comparison_group_projector.aggregate_metadata(
                group_id=group_id,
                history=history,
                run_metadata_by_id=run_metadata_by_id,
                member_run_ids=group_to_run_ids.get(group_id, []),
            )
            if aggregated is not None:
                metadata_by_group[group_id] = aggregated
                continue
            group_history = grouped_history.get(group_id, [])
            if not group_history:
                continue
            metadata_by_group[group_id] = (
                self._synthetic_legacy_run_metadata(
                    run_id=group_id,
                    run_history=group_history,
                    status="completed",
                )
            )
        return dashboard_history, dashboard_current_run_id, metadata_by_group

    def dashboard_run_metadata(
        self,
        *,
        config: AppConfig,
        history: list[ScanResult],
        run_metadata_by_id: dict[str, dict[str, object]],
        current_run_id: str | None,
    ) -> dict[str, object]:
        if current_run_id:
            stored_metadata = run_metadata_by_id.get(current_run_id)
            if isinstance(stored_metadata, dict):
                normalized = normalized_metadata_projection(
                    stored_metadata,
                    RunMetadata.from_dict(stored_metadata).to_dict(),
                )
                return (
                    ComparisonGroupProjector.preserve_legacy_selection_metadata(
                        stored_metadata,
                        normalized,
                    )
                )
        return self._synthetic_legacy_run_metadata(
            run_id=current_run_id or (
                history[-1].run_id if history else None
            ),
            run_history=history,
            status="legacy",
            fallback_question_count=(
                len(self.question_bank.load().enabled_questions)
                if config
                else 0
            ),
        )

    @staticmethod
    def _synthetic_legacy_run_metadata(
        *,
        run_id: str | None,
        run_history: list[ScanResult],
        status: str,
        fallback_question_count: int = 0,
    ) -> dict[str, object]:
        question_ids = {
            str(item.question_id)
            for item in run_history
            if normalize_phase(item.phase) == SCAN_PHASE and item.question_id
        }
        candidate_keys = {
            str(item.candidate_id or f"{item.model}:{item.effort}")
            for item in run_history
        }
        started_at = next(
            (item.started_at for item in run_history if item.started_at),
            None,
        )
        completed_at = (
            next(
                (
                    item.started_at
                    for item in reversed(run_history)
                    if item.started_at
                ),
                None,
            )
            if status == "completed"
            else None
        )
        question_count = max(len(question_ids), fallback_question_count)
        normalized = RunMetadata.legacy(
            run_id=run_id,
            question_count=question_count,
        ).to_dict()
        normalized.update(
            {
                "started_at": started_at,
                "completed_at": completed_at,
                "candidate_count": len(candidate_keys),
                "question_count": question_count,
                "status": status,
            }
        )
        if run_history:
            normalized["aggregate_wall_clock_seconds"] = (
                ComparisonGroupProjector.run_wall_clock_seconds(
                    run_history,
                    normalized,
                )
            )
        return ComparisonGroupProjector.preserve_legacy_selection_metadata(
            {},
            normalized,
        )

    def _stable_dashboard(
        self,
        *,
        config: AppConfig,
        history: list[ScanResult],
        run_metadata_by_id: dict[str, dict[str, object]],
        current_question_pack: QuestionCollection,
        current_default_candidate_id: str | None,
    ) -> dict[str, object] | None:
        stable_group_id = self._latest_complete_comparison_group_id(
            history=history,
            run_metadata_by_id=run_metadata_by_id,
            question_pack=current_question_pack,
        )
        if stable_group_id is None:
            return None
        stable_history, stable_current_run_id, metadata_by_group = (
            self.dashboard_history_context(
                history=history,
                run_metadata_by_id=run_metadata_by_id,
                current_run_id=stable_group_id,
            )
        )
        stable_metadata = self.dashboard_run_metadata(
            config=config,
            history=stable_history,
            run_metadata_by_id=metadata_by_group,
            current_run_id=stable_current_run_id,
        )
        return build_dashboard_summary(
            stable_history,
            config.model_ingress,
            current_run_id=stable_current_run_id,
            run_metadata=stable_metadata,
            scan_interval_seconds=config.scheduler.interval_seconds,
            run_metadata_by_id=metadata_by_group,
            current_default_candidate_id=current_default_candidate_id,
            scan_budget=config.scan_budget,
            current_question_pack_id=(
                current_question_pack.metadata.question_pack_id
            ),
            current_question_pack_version=(
                current_question_pack.metadata.question_pack_version
            ),
        )

    def _stable_evidence_dashboard(
        self,
        *,
        config: AppConfig,
        history: list[ScanResult],
        run_metadata_by_id: dict[str, dict[str, object]],
        current_question_pack: QuestionCollection,
        current_default_candidate_id: str | None,
    ) -> dict[str, object] | None:
        stable_group_id = self._latest_complete_evidence_group_id(
            history=history,
            run_metadata_by_id=run_metadata_by_id,
            question_pack=current_question_pack,
        )
        if stable_group_id is None:
            return None
        stable_history, stable_current_run_id, metadata_by_group = (
            self.dashboard_history_context(
                history=history,
                run_metadata_by_id=run_metadata_by_id,
                current_run_id=stable_group_id,
            )
        )
        stable_metadata = self.dashboard_run_metadata(
            config=config,
            history=stable_history,
            run_metadata_by_id=metadata_by_group,
            current_run_id=stable_current_run_id,
        )
        return build_dashboard_summary(
            stable_history,
            config.model_ingress,
            current_run_id=stable_current_run_id,
            run_metadata=stable_metadata,
            scan_interval_seconds=config.scheduler.interval_seconds,
            run_metadata_by_id=metadata_by_group,
            current_default_candidate_id=current_default_candidate_id,
            scan_budget=config.scan_budget,
            current_question_pack_id=(
                current_question_pack.metadata.question_pack_id
            ),
            current_question_pack_version=(
                current_question_pack.metadata.question_pack_version
            ),
        )

    def _latest_complete_evidence_group_id(
        self,
        *,
        history: list[ScanResult],
        run_metadata_by_id: dict[str, dict[str, object]],
        question_pack: QuestionCollection,
    ) -> str | None:
        return self._latest_completed_group_id(
            history=history,
            run_metadata_by_id=run_metadata_by_id,
            question_pack=question_pack,
            require_complete_regular_round=False,
        )

    def _latest_complete_comparison_group_id(
        self,
        *,
        history: list[ScanResult],
        run_metadata_by_id: dict[str, dict[str, object]],
        question_pack: QuestionCollection,
    ) -> str | None:
        return self._latest_completed_group_id(
            history=history,
            run_metadata_by_id=run_metadata_by_id,
            question_pack=question_pack,
            require_complete_regular_round=True,
        )

    def _latest_completed_group_id(
        self,
        *,
        history: list[ScanResult],
        run_metadata_by_id: dict[str, dict[str, object]],
        question_pack: QuestionCollection,
        require_complete_regular_round: bool,
    ) -> str | None:
        seen_group_ids: set[str] = set()
        for item in reversed(history):
            group_id = self.comparison_group_projector.group_id(
                item.run_id,
                run_metadata_by_id.get(item.run_id),
            )
            if group_id is None or group_id in seen_group_ids:
                continue
            seen_group_ids.add(group_id)
            member_run_ids = self.comparison_group_projector.member_run_ids(
                group_id=group_id,
                history=history,
                run_metadata_by_id=run_metadata_by_id,
            )
            metadata = self.comparison_group_projector.aggregate_metadata(
                group_id=group_id,
                history=history,
                run_metadata_by_id=run_metadata_by_id,
                member_run_ids=member_run_ids,
            )
            if not isinstance(metadata, dict):
                continue
            if not ScanPlanner.metadata_matches_question_pack(
                metadata,
                question_pack,
            ):
                continue
            if str(metadata.get("status") or "") != "completed":
                continue
            if (
                str(metadata.get("evaluation_result_level") or "")
                != "complete"
            ):
                continue
            if require_complete_regular_round and not bool(
                metadata.get("is_complete_regular_round")
            ):
                continue
            return group_id
        return None
