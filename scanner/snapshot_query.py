from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from .advisor import build_advisor_decision
from .advisor_v2_adapter import build_advisor_v2_evidence_bundle
from .advisor_v2_portfolio import build_multi_recommendation_portfolio
from .diagnostics import build_diagnostic_summary
from .models import AppConfig
from .protocol import project_app_snapshot_v2, project_refresh_snapshot_v1
from .recommendation_use import (
    read_recommendation_use_summary,
    update_recommendation_use_epochs,
)
from .reference_snapshot import (
    load_reference_snapshot_feed_for_app,
    project_reference_snapshot_pairwise,
    read_reference_snapshot_feed_for_app,
    reference_snapshot_to_advisor_source,
)
from .settings_projection import SettingsProjectionProjector
from .usage_store import UsageStore


CodexInsightsProvider = Callable[[Path], dict[str, object]]
ConfigReader = Callable[[], AppConfig]
StateReader = Callable[[], dict[str, object]]
REFERENCE_FRESH_SECONDS = 12 * 60 * 60
REFERENCE_EXPIRES_SECONDS = 24 * 60 * 60


def project_reference_snapshot_feed(
    feed: dict[str, object],
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    projected = dict(feed)
    latest = projected.get("latest")
    published_at = latest.get("published_at") if isinstance(latest, dict) else None
    try:
        published = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        projected["freshness"] = None
        projected["age_hours"] = None
        return projected
    current = now or datetime.now(timezone.utc)
    age_seconds = max(0.0, (current - published).total_seconds())
    projected["age_hours"] = int(age_seconds / 3_600)
    projected["freshness"] = (
        "expired"
        if age_seconds > REFERENCE_EXPIRES_SECONDS
        else "delayed"
        if age_seconds > REFERENCE_FRESH_SECONDS
        else "fresh"
    )
    return projected


@dataclass(frozen=True)
class SnapshotProjector:
    config_reader: ConfigReader
    state_reader: StateReader
    settings_projector: SettingsProjectionProjector

    def project(
        self,
        *,
        reference_snapshot_feed: dict[str, object],
        codex_insights: dict[str, object] | None,
        prior_recommendation_epochs: Sequence[Mapping[str, object]] = (),
    ) -> tuple[dict[str, object], dict[str, object]]:
        config = self.config_reader()
        state = self.state_reader()
        reference_snapshot_feed = project_reference_snapshot_feed(
            reference_snapshot_feed
        )
        dashboard = state.get("dashboard")
        raw_cards = dashboard.get("cards") if isinstance(dashboard, dict) else None
        dashboard_cards = (
            [dict(card) for card in raw_cards if isinstance(card, dict)]
            if isinstance(raw_cards, list)
            else []
        )
        state["settings_projection"] = self.settings_projector.project(
            config,
            dashboard_cards=dashboard_cards,
        )
        latest_reference_snapshot = reference_snapshot_feed.get("latest")
        official_snapshot = (
            reference_snapshot_to_advisor_source(latest_reference_snapshot)
            if isinstance(latest_reference_snapshot, dict)
            else None
        )
        reference_snapshot_feed = project_reference_snapshot_pairwise(
            reference_snapshot_feed
        )
        advisor_v2_bundle = build_advisor_v2_evidence_bundle(
            state,
            official_snapshot=official_snapshot,
        )
        advisor_v2_evidence = advisor_v2_bundle["primary_evidence"]
        recommendation_config = state.get("config", {}).get("recommendation", {})
        preference = (
            str(recommendation_config.get("preference") or "smart")
            if isinstance(recommendation_config, dict)
            else "smart"
        )
        state["advisor_v2_evidence"] = advisor_v2_evidence
        state["recommendation_portfolio_v2"] = (
            build_multi_recommendation_portfolio(
                advisor_v2_bundle["contexts"],
                activity=advisor_v2_bundle["activity"],
                fallback_evidence=advisor_v2_evidence,
                unmapped_active_session_count=int(
                    advisor_v2_bundle["unmapped_active_session_count"]
                ),
                preference=preference,
                prior_recommendation_epochs=prior_recommendation_epochs,
            )
        )
        state["reference_snapshot_feed"] = reference_snapshot_feed
        if codex_insights is not None:
            state["codex_insights"] = codex_insights
            advisor = build_advisor_decision(state, codex_insights)
            state["advisor"] = advisor
            state["diagnostics"] = build_diagnostic_summary(
                state,
                codex_insights,
                advisor,
                recommendation_portfolio=state["recommendation_portfolio_v2"],
            )
        return state, advisor_v2_bundle


@dataclass(frozen=True)
class SnapshotQuery:
    snapshot_projector: SnapshotProjector
    refresh_state_reader: StateReader
    data_dir: Path

    def build_snapshot(
        self,
        *,
        codex_insights: dict[str, object] | None = None,
    ) -> dict[str, object]:
        store = UsageStore(self.data_dir)
        state, _ = self.snapshot_projector.project(
            reference_snapshot_feed=read_reference_snapshot_feed_for_app(
                cache_root=self.data_dir / "reference_snapshots",
            ),
            codex_insights=codex_insights,
            prior_recommendation_epochs=_recommendation_epochs(store),
        )
        state["recommendation_use"] = read_recommendation_use_summary(
            store=store,
        )
        return project_app_snapshot_v2(state)

    def build_refresh_snapshot(
        self,
        *,
        codex_insights: dict[str, object] | None = None,
    ) -> dict[str, object]:
        state = self.refresh_state_reader()
        if codex_insights is not None:
            state["codex_insights"] = codex_insights
        state["recommendation_use"] = read_recommendation_use_summary(
            store=UsageStore(self.data_dir),
        )
        return project_refresh_snapshot_v1(state)


@dataclass(frozen=True)
class SnapshotCommand:
    snapshot_projector: SnapshotProjector
    data_dir: Path

    def build_snapshot(
        self,
        *,
        codex_insights_provider: CodexInsightsProvider | None = None,
        codex_insights: dict[str, object] | None = None,
        refresh_reference: bool = False,
    ) -> dict[str, object]:
        projected_insights = codex_insights
        if projected_insights is None and codex_insights_provider is not None:
            projected_insights = codex_insights_provider(self.data_dir)
        reference_snapshot_feed = (
            load_reference_snapshot_feed_for_app(
                cache_root=self.data_dir / "reference_snapshots",
            )
            if refresh_reference
            else read_reference_snapshot_feed_for_app(
                cache_root=self.data_dir / "reference_snapshots",
            )
        )
        store = UsageStore(self.data_dir)
        state, advisor_v2_bundle = self.snapshot_projector.project(
            reference_snapshot_feed=reference_snapshot_feed,
            codex_insights=projected_insights,
            prior_recommendation_epochs=_recommendation_epochs(store),
        )
        state["recommendation_use"] = update_recommendation_use_epochs(
            store=store,
            state=state,
            contexts=advisor_v2_bundle["contexts"],
            portfolio=state["recommendation_portfolio_v2"],
            workload=(
                state.get("codex_insights", {}).get("workload")
                if isinstance(state.get("codex_insights"), dict)
                else None
            ),
        )
        return project_app_snapshot_v2(state)


def _recommendation_epochs(store: UsageStore) -> list[Mapping[str, object]]:
    epochs = store.load_recommendation_use_state().get("epochs")
    if not isinstance(epochs, list):
        return []
    return [epoch for epoch in epochs if isinstance(epoch, Mapping)]
