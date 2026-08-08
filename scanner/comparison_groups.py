from __future__ import annotations

from datetime import datetime

from .legacy_scan_compat import (
    SCAN_PHASE,
    metadata_question_count,
    metadata_question_ids,
    normalize_phase,
)
from .models import ResolvedScanTarget, RunMetadata, ScanResult
from .scan_target_resolver import ScanTargetResolver


def parse_iso_timestamp(value: object) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


class ComparisonGroupProjector:
    """Project comparison-group membership and completed scan evidence."""

    def __init__(self, target_resolver: ScanTargetResolver | None = None) -> None:
        self.target_resolver = target_resolver or ScanTargetResolver()

    @staticmethod
    def group_id(
        run_id: str | None,
        metadata: dict[str, object] | None,
    ) -> str | None:
        if isinstance(metadata, dict):
            value = str(metadata.get("comparison_group_id") or "").strip()
            if value:
                return value
        if run_id:
            return str(run_id)
        return None

    def member_run_ids(
        self,
        *,
        group_id: str,
        history: list[ScanResult],
        run_metadata_by_id: dict[str, dict[str, object]],
    ) -> list[str]:
        ordered_run_ids: list[str] = []
        seen_run_ids: set[str] = set()
        for item in history:
            item_group_id = self.group_id(
                item.run_id,
                run_metadata_by_id.get(item.run_id),
            )
            if item_group_id != group_id or item.run_id in seen_run_ids:
                continue
            seen_run_ids.add(item.run_id)
            ordered_run_ids.append(item.run_id)
        return ordered_run_ids

    def candidate_ids(
        self,
        *,
        group_id: str,
        history: list[ScanResult],
        run_metadata_by_id: dict[str, dict[str, object]],
        enabled_targets: list[ResolvedScanTarget] | None = None,
    ) -> list[str]:
        ordered_candidate_ids: list[str] = []
        seen_candidate_ids: set[str] = set()
        member_run_ids = self.member_run_ids(
            group_id=group_id,
            history=history,
            run_metadata_by_id=run_metadata_by_id,
        )
        member_run_id_set = set(member_run_ids)
        for run_id in member_run_ids:
            metadata = run_metadata_by_id.get(run_id, {})
            for candidate_id in metadata.get("requested_candidate_ids", []):
                candidate_value = str(candidate_id)
                if candidate_value and candidate_value not in seen_candidate_ids:
                    seen_candidate_ids.add(candidate_value)
                    ordered_candidate_ids.append(candidate_value)
        if enabled_targets is not None:
            candidate_ids_by_label = self.target_resolver.candidate_ids_by_label(
                enabled_targets
            )
            for item in history:
                if item.run_id not in member_run_id_set:
                    continue
                candidate_id = self.target_resolver.result_candidate_id(
                    item,
                    candidate_ids_by_label,
                )
                if candidate_id and candidate_id not in seen_candidate_ids:
                    seen_candidate_ids.add(candidate_id)
                    ordered_candidate_ids.append(candidate_id)
        return ordered_candidate_ids

    @staticmethod
    def ordered_unique_candidate_ids(*sources: object) -> list[str]:
        ordered_values: list[str] = []
        seen_values: set[str] = set()
        for source in sources:
            if not isinstance(source, list):
                continue
            for item in source:
                value = str(item).strip()
                if not value or value in seen_values:
                    continue
                seen_values.add(value)
                ordered_values.append(value)
        return ordered_values

    def dashboard_overlay_metadata_by_run_id(
        self,
        *,
        history: list[ScanResult],
        run_metadata_by_id: dict[str, dict[str, object]],
        current_run_id: str | None,
    ) -> dict[str, dict[str, object]]:
        """Return the legacy dashboard grouping projection without persisting it."""
        adjusted_metadata_by_id = {
            run_id: dict(metadata)
            for run_id, metadata in run_metadata_by_id.items()
            if isinstance(metadata, dict)
        }
        if not current_run_id:
            return adjusted_metadata_by_id
        current_metadata = adjusted_metadata_by_id.get(current_run_id)
        if not isinstance(current_metadata, dict):
            return adjusted_metadata_by_id
        selection_mode = str(current_metadata.get("selection_mode") or "")
        group_mode = str(
            current_metadata.get("comparison_group_mode") or selection_mode
        )
        requested_current_candidate_ids = self.ordered_unique_candidate_ids(
            current_metadata.get("requested_candidate_ids") or []
        )
        is_legacy_single = selection_mode == "single" or group_mode == "single"
        is_misgrouped_first_scan = (
            selection_mode == "custom"
            and group_mode == "custom_new_round"
            and not bool(current_metadata.get("is_complete_regular_round"))
            and len(requested_current_candidate_ids) == 1
            and not any(
                item.run_id != current_run_id
                and item.candidate_id == requested_current_candidate_ids[0]
                for item in history
            )
        )
        if not is_legacy_single and not is_misgrouped_first_scan:
            return adjusted_metadata_by_id
        fallback_history = [
            item for item in history if item.run_id != current_run_id
        ]
        if not fallback_history:
            return adjusted_metadata_by_id
        fallback_metadata_by_id = {
            run_id: dict(metadata)
            for run_id, metadata in adjusted_metadata_by_id.items()
            if run_id != current_run_id and isinstance(metadata, dict)
        }
        target_group_id = self.latest_appendable_group_id(
            fallback_history,
            fallback_metadata_by_id,
        )
        if not target_group_id:
            return adjusted_metadata_by_id
        target_metadata = fallback_metadata_by_id.get(target_group_id)
        if not isinstance(target_metadata, dict):
            return adjusted_metadata_by_id
        regular_candidate_ids = self.ordered_unique_candidate_ids(
            target_metadata.get("regular_candidate_ids")
            or target_metadata.get("requested_candidate_ids")
            or []
        )
        requested_candidate_ids = self.ordered_unique_candidate_ids(
            target_metadata.get("requested_candidate_ids") or [],
            current_metadata.get("requested_candidate_ids") or [],
        )
        if not requested_candidate_ids:
            return adjusted_metadata_by_id
        appended_candidate_ids = self.ordered_unique_candidate_ids(
            target_metadata.get("appended_candidate_ids") or [],
            [
                candidate_id
                for candidate_id in requested_candidate_ids
                if candidate_id not in set(regular_candidate_ids)
            ],
        )
        adjusted_metadata_by_id[current_run_id] = {
            **current_metadata,
            "selection_mode": "custom",
            "comparison_group_id": target_group_id,
            "comparison_group_mode": "custom_append",
            "comparison_parent_run_id": str(
                target_metadata.get("run_id") or target_group_id
            ),
            "append_target_group_id": target_group_id,
            "regular_candidate_ids": regular_candidate_ids,
            "requested_candidate_ids": requested_candidate_ids,
            "appended_candidate_ids": appended_candidate_ids,
            "candidate_count": len(requested_candidate_ids),
            "dashboard_overlay_legacy_single": is_legacy_single,
            "dashboard_overlay_append_recovery": is_misgrouped_first_scan,
        }
        return adjusted_metadata_by_id

    @staticmethod
    def run_wall_clock_seconds(
        run_history: list[ScanResult],
        run_metadata: dict[str, object],
    ) -> int:
        if run_metadata.get("aggregate_wall_clock_seconds") is not None:
            return int(run_metadata.get("aggregate_wall_clock_seconds") or 0)
        started_at = parse_iso_timestamp(run_metadata.get("started_at"))
        completed_at = parse_iso_timestamp(run_metadata.get("completed_at"))
        if started_at is not None and completed_at is not None:
            return round(max(0.0, completed_at - started_at))
        intervals: list[tuple[float, float]] = []
        for item in run_history:
            item_started_at = parse_iso_timestamp(item.started_at)
            if item_started_at is None:
                continue
            intervals.append(
                (
                    item_started_at,
                    item_started_at + max(0.0, float(item.elapsed_seconds)),
                )
            )
        if intervals:
            return round(
                max(
                    0.0,
                    max(end for _, end in intervals)
                    - min(start for start, _ in intervals),
                )
            )
        return round(sum(item.elapsed_seconds for item in run_history))

    @staticmethod
    def preserve_legacy_selection_metadata(
        raw_metadata: dict[str, object],
        normalized: dict[str, object],
    ) -> dict[str, object]:
        if (
            "selection_mode" in raw_metadata
            or "requested_candidate_ids" in raw_metadata
        ):
            return normalized
        for key in (
            "selection_mode",
            "requested_candidate_ids",
            "regular_candidate_ids",
            "comparison_group_id",
            "comparison_group_mode",
            "comparison_parent_run_id",
            "append_target_group_id",
            "appended_candidate_ids",
            "skipped_candidate_ids",
            "aggregate_wall_clock_seconds",
            "is_complete_regular_round",
        ):
            normalized.pop(key, None)
        return normalized

    def aggregate_metadata(
        self,
        *,
        group_id: str,
        history: list[ScanResult],
        run_metadata_by_id: dict[str, dict[str, object]],
        member_run_ids: list[str],
    ) -> dict[str, object] | None:
        ordered_run_ids = [run_id for run_id in member_run_ids if run_id]
        ordered_metadata = [
            dict(run_metadata_by_id[run_id])
            for run_id in ordered_run_ids
            if isinstance(run_metadata_by_id.get(run_id), dict)
        ]
        if not ordered_metadata:
            return None
        latest_metadata = ordered_metadata[-1]
        display_metadata = latest_metadata
        if bool(
            latest_metadata.get("dashboard_overlay_legacy_single")
            or latest_metadata.get("dashboard_overlay_append_recovery")
        ):
            for metadata in reversed(ordered_metadata[:-1]):
                selection_mode = str(metadata.get("selection_mode") or "regular")
                group_mode = str(
                    metadata.get("comparison_group_mode") or selection_mode
                )
                if selection_mode == "single" or group_mode == "single":
                    continue
                display_metadata = metadata
                break
        requested_candidate_ids: list[str] = []
        regular_candidate_ids: list[str] = []
        appended_candidate_ids: list[str] = []
        skipped_candidate_ids: list[str] = []
        is_complete_regular_round = False
        started_candidates: set[str] = set()
        regular_candidates_seen: set[str] = set()
        appended_candidates_seen: set[str] = set()
        skipped_candidates_seen: set[str] = set()
        started_values = [
            str(metadata["started_at"])
            for metadata in ordered_metadata
            if metadata.get("started_at")
        ]
        completed_values = [
            str(metadata["completed_at"])
            for metadata in ordered_metadata
            if metadata.get("completed_at")
        ]
        aggregate_wall_clock_seconds = 0
        history_by_run_id: dict[str, list[ScanResult]] = {}
        for item in history:
            history_by_run_id.setdefault(item.run_id, []).append(item)
        for run_id, metadata in zip(ordered_run_ids, ordered_metadata):
            for candidate_id in metadata.get("requested_candidate_ids", []):
                candidate_value = str(candidate_id)
                if candidate_value and candidate_value not in started_candidates:
                    started_candidates.add(candidate_value)
                    requested_candidate_ids.append(candidate_value)
            for candidate_id in metadata.get("regular_candidate_ids", []):
                candidate_value = str(candidate_id)
                if candidate_value and candidate_value not in regular_candidates_seen:
                    regular_candidates_seen.add(candidate_value)
                    regular_candidate_ids.append(candidate_value)
            for candidate_id in metadata.get("appended_candidate_ids", []):
                candidate_value = str(candidate_id)
                if candidate_value and candidate_value not in appended_candidates_seen:
                    appended_candidates_seen.add(candidate_value)
                    appended_candidate_ids.append(candidate_value)
            for candidate_id in metadata.get("skipped_candidate_ids", []):
                candidate_value = str(candidate_id)
                if candidate_value and candidate_value not in skipped_candidates_seen:
                    skipped_candidates_seen.add(candidate_value)
                    skipped_candidate_ids.append(candidate_value)
            if bool(metadata.get("is_complete_regular_round")):
                is_complete_regular_round = True
            aggregate_wall_clock_seconds += self.run_wall_clock_seconds(
                history_by_run_id.get(run_id, []),
                metadata,
            )
        latest_selection_mode = str(
            display_metadata.get("selection_mode") or "regular"
        )
        latest_group_mode = str(
            display_metadata.get("comparison_group_mode") or latest_selection_mode
        )
        if latest_group_mode == "profile_upgrade":
            requested_candidate_ids = self.ordered_unique_candidate_ids(
                display_metadata.get("requested_candidate_ids") or []
            )
            regular_candidate_ids = self.ordered_unique_candidate_ids(
                display_metadata.get("regular_candidate_ids") or []
            )
            appended_candidate_ids = self.ordered_unique_candidate_ids(
                display_metadata.get("appended_candidate_ids") or []
            )
            skipped_candidate_ids = self.ordered_unique_candidate_ids(
                display_metadata.get("skipped_candidate_ids") or []
            )
            is_complete_regular_round = bool(
                display_metadata.get("is_complete_regular_round")
            )
        selection_mode = (
            "single"
            if latest_group_mode == "single"
            else "custom"
            if latest_group_mode in {"custom_append", "custom_new_round"}
            else latest_selection_mode
        )
        completed_at = (
            None
            if str(display_metadata.get("status") or "") in {"running", "paused"}
            else completed_values[-1]
            if completed_values
            else None
        )
        normalized = RunMetadata(
            run_id=group_id,
            question_pack_id=str(
                display_metadata.get("question_pack_id") or "unknown"
            ),
            question_pack_version=str(
                display_metadata.get("question_pack_version") or "unknown"
            ),
            started_at=started_values[0] if started_values else None,
            completed_at=completed_at,
            candidate_count=len(requested_candidate_ids),
            question_count=metadata_question_count(display_metadata),
            status=str(display_metadata.get("status") or "legacy"),
            evaluation_profile_id=str(
                display_metadata.get("evaluation_profile_id") or "legacy_full"
            ),
            evaluation_profile_label=str(
                display_metadata.get("evaluation_profile_label") or "完整评测"
            ),
            evaluation_result_level=str(
                display_metadata.get("evaluation_result_level") or "unknown"
            ),
            evaluation_score_max=int(
                display_metadata.get("evaluation_score_max") or 0
            ),
            question_ids=metadata_question_ids(display_metadata),
            upgrade_from_run_id=(
                str(display_metadata.get("upgrade_from_run_id"))
                if display_metadata.get("upgrade_from_run_id")
                and str(display_metadata.get("upgrade_from_run_id")) != group_id
                else None
            ),
            upgrade_target_profile_id=(
                str(display_metadata.get("upgrade_target_profile_id"))
                if display_metadata.get("upgrade_target_profile_id")
                else None
            ),
            selection_mode=selection_mode,
            requested_candidate_ids=requested_candidate_ids,
            regular_candidate_ids=regular_candidate_ids,
            comparison_group_id=group_id,
            comparison_group_mode=latest_group_mode,
            comparison_parent_run_id=(
                str(display_metadata.get("comparison_parent_run_id"))
                if display_metadata.get("comparison_parent_run_id")
                else None
            ),
            append_target_group_id=(
                str(display_metadata.get("append_target_group_id"))
                if display_metadata.get("append_target_group_id")
                else None
            ),
            appended_candidate_ids=appended_candidate_ids,
            skipped_candidate_ids=skipped_candidate_ids,
            aggregate_wall_clock_seconds=aggregate_wall_clock_seconds or None,
            is_complete_regular_round=is_complete_regular_round,
            scoring_mode=str(display_metadata.get("scoring_mode") or "legacy"),
        ).to_dict()
        return self.preserve_legacy_selection_metadata(
            display_metadata,
            normalized,
        )

    def result_state(
        self,
        *,
        history: list[ScanResult],
        run_ids: list[str],
        enabled_targets: list[ResolvedScanTarget],
        question_ids: list[str] | None = None,
    ) -> dict[str, object]:
        candidate_ids_by_label = self.target_resolver.candidate_ids_by_label(
            enabled_targets
        )
        enabled_candidate_ids = {target.candidate_id for target in enabled_targets}
        run_id_set = set(run_ids)
        question_id_set = set(question_ids or [])
        completed_steps: set[tuple[str, str, str]] = set()
        completed_by_candidate: dict[str, int] = {}
        buckets: dict[str, list[ScanResult]] = {}
        latest_by_candidate: dict[str, dict[str, ScanResult]] = {}
        for item in history:
            if item.run_id not in run_id_set:
                continue
            candidate_id = self.target_resolver.result_candidate_id(
                item,
                candidate_ids_by_label,
            )
            if candidate_id not in enabled_candidate_ids:
                continue
            phase = normalize_phase(item.phase)
            if phase != SCAN_PHASE:
                continue
            if question_id_set and item.question_id not in question_id_set:
                continue
            step_key = (candidate_id, phase, item.question_id)
            completed_steps.add(step_key)
            latest_by_candidate.setdefault(candidate_id, {})[item.question_id] = item

        for candidate_id, latest_by_question in latest_by_candidate.items():
            buckets[candidate_id] = list(latest_by_question.values())
            completed_by_candidate[candidate_id] = len(latest_by_question)

        return {
            "completed_steps": completed_steps,
            "completed_by_candidate": completed_by_candidate,
            "buckets": buckets,
            "completed_count": sum(completed_by_candidate.values()),
        }

    def latest_appendable_group_id(
        self,
        history: list[ScanResult],
        run_metadata_by_id: dict[str, dict[str, object]],
    ) -> str | None:
        seen_run_ids: set[str] = set()
        for item in reversed(history):
            if item.run_id in seen_run_ids:
                continue
            seen_run_ids.add(item.run_id)
            metadata = run_metadata_by_id.get(item.run_id, {})
            selection_mode = str(metadata.get("selection_mode") or "regular")
            group_mode = str(
                metadata.get("comparison_group_mode") or selection_mode
            )
            if group_mode == "single" or selection_mode == "single":
                continue
            return self.group_id(item.run_id, metadata)
        return None
