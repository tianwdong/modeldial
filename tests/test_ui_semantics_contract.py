from __future__ import annotations

from pathlib import Path
import unittest


class UISemanticsContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent.parent
        self.expanded = (self.root / "Sources/Views/ExpandedSelectionView.swift").read_text(
            encoding="utf-8"
        )
        self.models = (self.root / "Sources/Model/SelectionModels.swift").read_text(
            encoding="utf-8"
        )
        self.evidence = (
            self.root / "Sources/Views/CandidateEvidenceDetailView.swift"
        ).read_text(encoding="utf-8")
        self.settings = (self.root / "Sources/Views/SettingsView.swift").read_text(
            encoding="utf-8"
        )
        self.l10n = (self.root / "Sources/Localization/L10n.swift").read_text(
            encoding="utf-8"
        )

    def test_v2_overview_consumes_projected_ranking_without_rebuilding_question_columns(self) -> None:
        row = self.expanded.split("private struct RadarLeaderboardRow: View {", 1)[1].split(
            "private struct ComparisonScoreTrendChart", 1
        )[0]

        self.assertIn("let entry: RadarLeaderboardItem", row)
        self.assertIn("entry.score", row)
        self.assertIn("entry.elapsedSeconds", row)
        self.assertNotIn("QuestionSemantic", row)
        self.assertNotIn("currentQuestionResult", row)
        self.assertNotIn("QuestionScoreCell", self.expanded)

    def test_evidence_is_the_single_detailed_question_semantics_surface(self) -> None:
        self.assertIn('Text("逐题结果")', self.evidence)
        self.assertIn('evidenceRow("题目总分", scoreText)', self.evidence)
        self.assertIn("result.semanticDisplayName", self.evidence)
        self.assertNotIn('Text("探针结果")', self.evidence)
        self.assertNotIn("QuestionScoreDetailPopover", self.expanded)

    def test_adjacent_user_facing_terms_do_not_reuse_internal_jargon(self) -> None:
        self.assertNotIn('return "当前与建议"', self.expanded)
        self.assertIn('return L10n.tr("当前与候选")', self.expanded)
        self.assertIn('return L10n.tr("当前配置")', self.expanded)
        self.assertNotIn("旧成绩已保留在场景分析", self.expanded)
        self.assertNotIn('formRow("项目名称")', self.settings)
        self.assertNotIn('formRow("默认视角")', self.settings)
        self.assertIn('label: "扫描档位"', self.settings)
        self.assertNotIn("任务相关探针", self.settings)
        self.assertIn('fallback: "重试"', self.l10n)
        self.assertIn('fallback: "重试进度"', self.l10n)


if __name__ == "__main__":
    unittest.main()
