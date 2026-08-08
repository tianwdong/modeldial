from __future__ import annotations

import ast
from pathlib import Path
import unittest
from unittest.mock import Mock

from scanner.codex_current_model import DetectedCodexModel, DetectedCodexSession
from scanner.current_model_context import CurrentModelContextQuery
from scanner.model_sessions import DetectedModelSession
from scanner.models import AppConfig


ROOT = Path(__file__).resolve().parents[1]


def _enable_claude_high(config: AppConfig) -> None:
    for source in config.model_ingress.sources:
        if source.id == "claude_local":
            source.enabled = True
    for connection in config.model_ingress.connections:
        if connection.source_id != "claude_local":
            continue
        connection.enabled = True
        connection.local_login_verified = True
        for candidate in connection.model_candidates:
            candidate.enabled = candidate.scan_profile == "high"


class CurrentModelContextQueryTest(unittest.TestCase):
    def test_build_returns_the_existing_payload_contract_without_monitor_service(
        self,
    ) -> None:
        config = AppConfig.default()
        _enable_claude_high(config)
        current_model_detector = Mock(
            return_value=DetectedCodexModel(
                model="gpt-5.5",
                effort="high",
                detected_at="2026-07-29T08:00:00Z",
                status="active_single",
                active_session_count=1,
                distinct_active_models=(("gpt-5.5", "high"),),
                active_sessions=(
                    DetectedCodexSession(
                        id="codex-session",
                        workspace_name="codex-project",
                        model="gpt-5.5",
                        effort="high",
                        thread_name="重构扫描器",
                        last_active_at="2026-07-29T07:59:00Z",
                        is_currently_producing=True,
                    ),
                ),
                display_sessions=(
                    DetectedCodexSession(
                        id="codex-session",
                        workspace_name="codex-project",
                        model="gpt-5.5",
                        effort="high",
                        thread_name="重构扫描器",
                        last_active_at="2026-07-29T07:59:00Z",
                        is_currently_producing=True,
                    ),
                    DetectedCodexSession(
                        id="modeldial-scan",
                        workspace_name="Backend",
                        model="gpt-5.4",
                        effort="xhigh",
                        is_modeldial_scan=True,
                    ),
                ),
            )
        )
        active_session_detector = Mock(
            return_value=(
                DetectedModelSession(
                    id="claude-session",
                    source="claude",
                    workspace_name="claude-project",
                    model="claude-sonnet-4-5",
                    effort="high",
                    thread_name="审查分支",
                    last_active_at="2026-07-29T07:58:00Z",
                    is_currently_producing=False,
                ),
            )
        )
        query = CurrentModelContextQuery(
            current_model_detector=current_model_detector,
            active_session_detector=active_session_detector,
        )

        self.assertEqual(
            query.build(config),
            {
                "effective_candidate_id": None,
                "source": "terminal_session",
                "detection_status": "active_mixed",
                "detected_at": "2026-07-29T08:00:00Z",
                "model": None,
                "effort": None,
                "active_session_count": 2,
                "active_models": [
                    {"model": "gpt-5.5", "effort": "high"},
                    {"model": "claude-sonnet-4-5", "effort": "high"},
                ],
                "active_sessions": [
                    {
                        "id": "codex-session",
                        "workspace_name": "codex-project",
                        "model": "gpt-5.5",
                        "effort": "high",
                        "thread_name": "重构扫描器",
                    },
                    {
                        "id": "claude-session",
                        "workspace_name": "claude-project",
                        "model": "claude-sonnet-4-5",
                        "effort": "high",
                        "thread_name": "审查分支",
                    },
                ],
                "active_configuration_sessions": [
                    {
                        "candidate_id": "codex-local-default:gpt-5.5:high",
                        "mapping_status": "matched",
                        "last_active_at": "2026-07-29T07:59:00Z",
                        "is_currently_producing": True,
                    },
                    {
                        "candidate_id": "claude-local-default:sonnet:high",
                        "mapping_status": "matched",
                        "last_active_at": "2026-07-29T07:58:00Z",
                        "is_currently_producing": False,
                    },
                ],
                "display_sessions": [
                    {
                        "id": "codex-session",
                        "source": "codex",
                        "workspace_name": "codex-project",
                        "model": "gpt-5.5",
                        "effort": "high",
                        "thread_name": "重构扫描器",
                        "is_evaluation_session": False,
                    },
                    {
                        "id": "modeldial-scan",
                        "source": "codex",
                        "workspace_name": "Backend",
                        "model": "gpt-5.4",
                        "effort": "xhigh",
                        "thread_name": None,
                        "is_evaluation_session": True,
                    },
                    {
                        "id": "claude-session",
                        "source": "claude",
                        "workspace_name": "claude-project",
                        "model": "claude-sonnet-4-5",
                        "effort": "high",
                        "thread_name": "审查分支",
                        "is_evaluation_session": False,
                    },
                ],
            },
        )
        current_model_detector.assert_called_once_with()
        active_session_detector.assert_called_once_with()

    def test_build_preserves_oserror_fallback_and_manual_override(self) -> None:
        config = AppConfig.default()
        config.recommendation.current_model_mode = "manual"
        config.recommendation.current_default_candidate_id = (
            "codex-local-default:gpt-5.5:high"
        )
        current_model_detector = Mock(side_effect=OSError("codex unavailable"))
        active_session_detector = Mock(side_effect=OSError("sessions unavailable"))
        query = CurrentModelContextQuery(
            current_model_detector=current_model_detector,
            active_session_detector=active_session_detector,
        )

        self.assertEqual(
            query.build(config),
            {
                "effective_candidate_id": "codex-local-default:gpt-5.5:high",
                "source": "manual",
                "detection_status": "unavailable",
                "detected_at": None,
                "model": None,
                "effort": None,
                "active_session_count": 0,
                "active_models": [],
                "active_sessions": [],
                "active_configuration_sessions": [],
                "display_sessions": [],
            },
        )
        current_model_detector.assert_called_once_with()
        active_session_detector.assert_called_once_with()

    def test_build_propagates_non_oserror_before_calling_session_detector(self) -> None:
        current_model_detector = Mock(side_effect=RuntimeError("broken detector"))
        active_session_detector = Mock(return_value=())
        query = CurrentModelContextQuery(
            current_model_detector=current_model_detector,
            active_session_detector=active_session_detector,
        )

        with self.assertRaisesRegex(RuntimeError, "broken detector"):
            query.build(AppConfig.default())

        current_model_detector.assert_called_once_with()
        active_session_detector.assert_not_called()

class CurrentModelContextArchitectureTest(unittest.TestCase):
    def test_query_has_no_service_or_persistence_dependency(self) -> None:
        path = ROOT / "scanner" / "current_model_context.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imported_modules.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )

        self.assertNotIn("service", imported_modules)
        self.assertNotIn(
            "MonitorService",
            {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)},
        )
        self.assertTrue(
            imported_modules.isdisjoint(
                {
                    "active_run_store",
                    "config_store",
                    "history_store",
                    "run_journal",
                    "usage_store",
                }
            )
        )
        called_attributes = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertTrue(
            called_attributes.isdisjoint(
                {"save", "clear", "write_text", "write_bytes", "unlink"}
            )
        )

if __name__ == "__main__":
    unittest.main()
