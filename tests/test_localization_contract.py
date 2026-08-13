from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "Resources" / "Localizable.xcstrings"


class LocalizationContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        self.strings = self.catalog["strings"]

    def test_catalog_declares_chinese_source_and_english_translation(self) -> None:
        self.assertEqual(self.catalog["sourceLanguage"], "zh-Hans")
        self.assertGreater(len(self.strings), 0)
        for key, entry in self.strings.items():
            with self.subTest(key=key):
                localizations = entry["localizations"]
                self.assertEqual(set(localizations), {"en", "zh-Hans"})
                for language in ("en", "zh-Hans"):
                    unit = localizations[language]["stringUnit"]
                    self.assertEqual(unit["state"], "translated")
                    self.assertTrue(unit["value"])

    def test_localized_format_placeholders_match_between_languages(self) -> None:
        placeholder_pattern = re.compile(r"%(?:\d+\$)?[@df]")
        for key, entry in self.strings.items():
            with self.subTest(key=key):
                localizations = entry["localizations"]
                chinese = localizations["zh-Hans"]["stringUnit"]["value"]
                english = localizations["en"]["stringUnit"]["value"]
                self.assertEqual(
                    placeholder_pattern.findall(chinese),
                    placeholder_pattern.findall(english),
                )

    def test_every_explicit_localization_key_exists_in_catalog(self) -> None:
        sources = [
            ROOT / "Sources" / "Localization" / "L10n.swift",
            ROOT / "Sources" / "Model" / "GlanceState.swift",
        ]
        key_pattern = re.compile(r'(?:text|format)\(\s*"([a-z][a-z0-9_.]+)"')
        used_keys: set[str] = set()
        for source in sources:
            used_keys.update(key_pattern.findall(source.read_text(encoding="utf-8")))
        self.assertTrue(used_keys)
        self.assertEqual(used_keys - set(self.strings), set())

    def test_every_literal_localization_key_exists_in_catalog(self) -> None:
        key_pattern = re.compile(r'L10n\.tr\(\s*"((?:[^"\\]|\\.)*)"')
        used_keys: set[str] = set()
        for source in (ROOT / "Sources").rglob("*.swift"):
            used_keys.update(
                self._decode_swift_string(key)
                for key in key_pattern.findall(source.read_text(encoding="utf-8"))
            )
        self.assertTrue(used_keys)
        self.assertEqual(used_keys - set(self.strings), set())

    def test_every_static_chinese_view_literal_exists_in_catalog(self) -> None:
        literal_pattern = re.compile(r'"((?:[^"\\]|\\.)*)"')
        missing: list[str] = []
        for source_path in (ROOT / "Sources" / "Views").rglob("*.swift"):
            source = source_path.read_text(encoding="utf-8")
            for raw_literal in literal_pattern.findall(source):
                if "\\(" in raw_literal:
                    continue
                literal = self._decode_swift_string(raw_literal)
                if re.search(r"[\u3400-\u9fff]", literal) and literal not in self.strings:
                    missing.append(f"{source_path.relative_to(ROOT)}: {literal}")
        self.assertEqual(missing, [])

    def test_interpolated_chinese_ui_copy_uses_explicit_localization(self) -> None:
        ui_literal_pattern = re.compile(
            r'\b(?:Text|Button|Label|Toggle|Picker|DatePicker)'
            r'\(\s*"((?:[^"\\]|\\.)*)"'
        )
        direct_interpolations: list[str] = []
        for source_path in (ROOT / "Sources" / "Views").rglob("*.swift"):
            source = source_path.read_text(encoding="utf-8")
            for literal in ui_literal_pattern.findall(source):
                if "\\(" in literal and re.search(r"[\u3400-\u9fff]", literal):
                    direct_interpolations.append(
                        f"{source_path.relative_to(ROOT)}: {literal}"
                    )
        self.assertEqual(direct_interpolations, [])

    def test_menu_and_dynamic_ui_copy_is_complete_and_idiomatic(self) -> None:
        expected_english = {
            "自动选择（优先本机实测）": "Auto (prefer local results)",
            "综合平衡": "Balanced",
            "质量优先": "Quality first",
            "速度优先": "Speed first",
            "费用优先": "Cost first",
            "停用连接": "Disable connection",
            "启用连接": "Enable connection",
            "删除连接": "Delete connection",
            "移除档位": "Remove profile",
            "移除模型簇": "Remove model family",
            "已选择 %d/%d 个档位": "%d/%d profiles selected",
            "%@ · %d 个模型 · %d 道题": "%@ · %d models · %d questions",
            "%d 个模型 · %d 个档位": "%d models · %d profiles",
        }
        for key, expected in expected_english.items():
            with self.subTest(key=key):
                self.assertEqual(
                    self.strings[key]["localizations"]["en"]["stringUnit"]["value"],
                    expected,
                )

        expanded = (
            ROOT / "Sources" / "Views" / "ExpandedSelectionView.swift"
        ).read_text(encoding="utf-8")
        for key in ("自动选择（优先本机实测）", "综合平衡", "质量优先", "速度优先", "费用优先"):
            with self.subTest(menu_key=key):
                self.assertIn(f'Button(L10n.tr("{key}"))', expanded)

    def test_scheduler_runtime_copy_is_localized_and_locale_aware(self) -> None:
        expected_english = {
            "未安排": "Not scheduled",
            "自动扫描已关闭": "Automatic scans are off",
            "本轮结束后计算": "Calculated after this run",
            "本轮完成后重新计算": "Recalculated when this run finishes",
            "扫描正在运行": "Scan in progress",
            "没有已启用扫描档位": "No scan profiles are enabled",
            "扫描计划不可用": "Scan schedule unavailable",
            "定时任务使用%@": "Scheduled with %@",
        }
        for key, expected in expected_english.items():
            with self.subTest(key=key):
                self.assertEqual(
                    self.strings[key]["localizations"]["en"]["stringUnit"]["value"],
                    expected,
                )

        settings = (ROOT / "Sources" / "Views" / "SettingsView.swift").read_text(
            encoding="utf-8"
        )
        store = (ROOT / "Sources" / "Model" / "SelectionStore.swift").read_text(
            encoding="utf-8"
        )
        schedule_source = store[store.index("func nextScheduledRun"):]
        self.assertIn("scheduledRunAbsoluteText(nextRun)", settings)
        self.assertIn("scheduledRunRelativeText(nextRun)", settings)
        self.assertIn("scheduledRunReasonText(nextRun)", settings)
        self.assertIn("formatter.locale = L10n.locale", settings)
        self.assertIn('reason: "定时任务使用%@"', schedule_source)
        self.assertNotIn('Locale(identifier: "zh_CN")', schedule_source)

    def test_detached_surfaces_reapply_language_and_localize_actions(self) -> None:
        expanded = (
            ROOT / "Sources" / "Views" / "ExpandedSelectionView.swift"
        ).read_text(encoding="utf-8")
        settings = (ROOT / "Sources" / "Views" / "SettingsView.swift").read_text(
            encoding="utf-8"
        )
        modifiers = (
            ROOT / "Sources" / "Views" / "SettingsViewModifiers.swift"
        ).read_text(encoding="utf-8")

        self.assertIn("@ObservedObject private var appLanguage", expanded)
        self.assertGreaterEqual(
            expanded.count(".environment(\\.locale, appLanguage.locale)"),
            3,
        )
        self.assertGreaterEqual(
            settings.count(".environment(\\.locale, appLanguage.locale)"),
            3,
        )
        for call in (
            'Text(L10n.tr("扫描档位"))',
            'Text(L10n.tr("修改后自动保存，并同步到两页。"))',
            "Button(L10n.Common.done)",
            'Text(L10n.tr("评测范围"))',
        ):
            with self.subTest(call=call):
                self.assertIn(call, expanded)
        for call in (
            '.alert(L10n.tr("无法开始扫描")',
            'Button(L10n.tr("删除连接")',
            'Button(L10n.tr(request.actionTitle)',
        ):
            with self.subTest(call=call):
                self.assertIn(call, modifiers)

    @staticmethod
    def _decode_swift_string(value: str) -> str:
        return value.replace(r"\n", "\n").replace(r'\"', '"').replace(r"\\", "\\")

    def test_compact_metric_copy_fits_the_hover_columns(self) -> None:
        expected_english = {
            "参考费用": "Estimated cost",
            "快 %d%%": "%d%% faster",
            "慢 %d%%": "%d%% slower",
            "省 %d%%": "%d%% lower",
            "多 %d%%": "%d%% higher",
            "自动 · 官网榜单": "Auto · ModelDial",
            "自动 · 本机实测": "Auto · Local",
            "切换建议": "Recommended",
            "最接近候选": "Closest",
            "建议切换": "Switch",
            "质量护栏": "Max drop",
            "质量门槛": "Min gain",
            "性价比": "Best value",
            "速度优选": "Fastest",
            "轻量优选": "Lightweight",
        }
        for key, expected in expected_english.items():
            with self.subTest(key=key):
                self.assertEqual(
                    self.strings[key]["localizations"]["en"]["stringUnit"]["value"],
                    expected,
                )

    def test_official_cache_footer_preserves_the_modeldial_source_name(self) -> None:
        entry = self.strings["官网榜单%@"]["localizations"]
        self.assertEqual(
            entry["zh-Hans"]["stringUnit"]["value"],
            "ModelDial 榜单%@",
        )
        self.assertEqual(
            entry["en"]["stringUnit"]["value"],
            "ModelDial ranking%@",
        )

    def test_reference_refresh_feedback_is_localized(self) -> None:
        expected_english = {
            "正在更新榜单": "Updating ranking",
            "榜单已更新": "Leaderboard updated",
            "当前已是最新结果": "Already up to date",
        }
        for key, expected in expected_english.items():
            with self.subTest(key=key):
                self.assertEqual(
                    self.strings[key]["localizations"]["en"]["stringUnit"]["value"],
                    expected,
                )

    def test_localized_copy_is_not_cached_across_language_changes(self) -> None:
        source = (ROOT / "Sources" / "Localization" / "L10n.swift").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("static let", source)

    def test_manual_build_compiles_localization_before_signing(self) -> None:
        build = (ROOT / "build.sh").read_text(encoding="utf-8")
        info_plist = (ROOT / "Resources" / "Info.plist").read_text(encoding="utf-8")
        self.assertIn("CFBundleDevelopmentRegion", info_plist)
        self.assertIn("CFBundleLocalizations", info_plist)
        self.assertIn("Resources/Localizable.xcstrings", build)
        self.assertIn("xcrun xcstringstool compile", build)
        self.assertLess(
            build.index("xcrun xcstringstool compile"),
            build.index("sign_app_bundle"),
        )

    def test_language_preference_and_runtime_switch_contract(self) -> None:
        language_store = ROOT / "Sources" / "Model" / "AppLanguageStore.swift"
        self.assertTrue(language_store.is_file())
        source = language_store.read_text(encoding="utf-8")
        self.assertIn("case system", source)
        self.assertIn('case zhHans = "zh-Hans"', source)
        self.assertIn("case en", source)
        self.assertIn('static let key = "modeldial.app.language"', source)
        self.assertNotIn("restartApp", source)

        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "app-language-store-tests"
            subprocess.run(
                [
                    "swiftc",
                    str(language_store),
                    str(ROOT / "Sources" / "Localization" / "L10n.swift"),
                    str(ROOT / "tests" / "swift" / "AppLanguageStoreTests.swift"),
                    "-o",
                    str(executable),
                ],
                check=True,
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            completed = subprocess.run(
                [str(executable)],
                check=True,
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertIn("AppLanguageStore tests passed", completed.stdout)

    def test_both_native_roots_observe_the_language_store(self) -> None:
        settings = (ROOT / "Sources" / "App.swift").read_text(encoding="utf-8")
        island = (ROOT / "Sources" / "Views" / "IslandRootView.swift").read_text(
            encoding="utf-8"
        )
        self.assertIn("AppLanguageStore.shared", settings)
        self.assertIn(".environment(\\.locale, appLanguage.locale)", settings)
        self.assertIn("AppLanguageStore.shared", island)
        self.assertIn(".environment(\\.locale, appLanguage.locale)", island)

    def test_general_settings_expose_language_picker(self) -> None:
        settings = (ROOT / "Sources" / "Views" / "SettingsView.swift").read_text(
            encoding="utf-8"
        )
        self.assertIn("languageSection", settings)
        self.assertIn("ForEach(AppLanguage.allCases", settings)
        self.assertIn("appLanguage.select", settings)
        self.assertIn('Text("跟随系统").tag(language)', settings)
        self.assertNotIn("showLanguageRestartPrompt", settings)

    def test_primary_glance_and_session_surfaces_use_localization_layer(self) -> None:
        glance = (ROOT / "Sources" / "Model" / "GlanceState.swift").read_text(
            encoding="utf-8"
        )
        sessions = (
            ROOT / "Sources" / "Views" / "CompactSessionPanelView.swift"
        ).read_text(encoding="utf-8")
        overview = (
            ROOT / "Sources" / "Views" / "ExpandedSelectionView.swift"
        ).read_text(encoding="utf-8")

        self.assertIn("L10n.Glance", glance)
        self.assertNotIn('Locale(identifier: "zh_CN")', glance)
        self.assertIn("L10n.Sessions.count", sessions)
        self.assertIn("L10n.Sessions.activeAccessibility", sessions)
        self.assertNotIn("L10n.Sessions.emptyTitle", sessions)
        self.assertIn("L10n.Overview.recommendationDecision", overview)
        self.assertIn("L10n.Overview.comparisonTab", overview)

    def test_compare_surface_uses_runtime_localization_and_stable_question_ids(self) -> None:
        source = (
            ROOT / "Sources" / "Views" / "ExpandedSelectionView.swift"
        ).read_text(encoding="utf-8")
        comparison = source.split("private struct ComparisonPage: View {", 1)[1]
        l10n = (ROOT / "Sources" / "Localization" / "L10n.swift").read_text(
            encoding="utf-8"
        )
        selection_models = (
            ROOT / "Sources" / "Model" / "SelectionModels.swift"
        ).read_text(encoding="utf-8")

        required_runtime_calls = (
            'label: L10n.tr("当前")',
            'label: L10n.tr("候选")',
            'title: L10n.tr("整轮耗时")',
            'Text(L10n.tr("最近 %d 次", trendData.slots.count))',
            'Text(L10n.tr("%d 题明细", rows.count))',
            'tokenRow(L10n.tr("输入")',
            'evidenceRow(L10n.tr("来源")',
            'Text(L10n.tr("连接：%@", connection))',
        )
        for call in required_runtime_calls:
            with self.subTest(call=call):
                self.assertIn(call, comparison)

        self.assertIn("let capabilityId: String", selection_models)
        self.assertIn("L10n.Question.capability(", comparison)
        identity = comparison.split("private func comparisonIdentity(", 1)[1].split(
            "private func comparisonMetricList(", 1
        )[0]
        evidence_row = comparison.split("private func evidenceRow(", 1)[1].split(
            "private var emptyState", 1
        )[0]
        self.assertIn(".frame(width: 56, alignment: .leading)", identity)
        self.assertIn(".frame(width: 96, alignment: .leading)", evidence_row)
        self.assertIn(".lineLimit(1)", identity)
        self.assertIn(".lineLimit(1)", evidence_row)
        for capability_id in (
            "black_box_regression_testing",
            "debug_counterexample",
            "ci_plan_audit",
            "state_machine_testing",
            "regression_validation",
        ):
            with self.subTest(capability_id=capability_id):
                self.assertIn(f'case "{capability_id}"', l10n)

        expected_english = {
            "整轮耗时": "Total time",
            "%d 题明细": "Scores for %d questions",
            "最近 %d 次": "Last %d runs",
            "评测详情": "Evaluation details",
            "输入": "Input",
            "来源": "Source",
            "评分器": "Grader",
            "评测快照": "Evaluation version",
            "question.capability.black_box_regression_testing": "Contract testing",
            "question.capability.debug_counterexample": "Counterexample construction",
            "question.capability.ci_plan_audit": "Solution audit",
            "question.capability.state_machine_testing": "State machine",
            "question.capability.regression_validation": "Test design",
        }
        for key, expected in expected_english.items():
            with self.subTest(key=key):
                self.assertEqual(
                    self.strings[key]["localizations"]["en"]["stringUnit"]["value"],
                    expected,
                )

    def test_visible_english_copy_uses_complete_idiomatic_phrases(self) -> None:
        expected_english = {
            "近期归因": "Recent usage",
            "one-shot 证据积累中": "Building no-retry history",
            "累计约少 %@": "About %@ saved",
            "累计约省 %@": "About %@ saved",
            "累计约多 %@": "About %@ more",
            "模型综合榜单": "Model ranking",
            "未提供": "Not available",
            "第一梯队模型，参考费用最低。": (
                "Lowest estimated cost among the top-scoring models."
            ),
            "第一梯队模型，总耗时最短。": (
                "Fastest among the top-scoring models."
            ),
        }
        for key, expected in expected_english.items():
            with self.subTest(key=key):
                self.assertEqual(
                    self.strings[key]["localizations"]["en"]["stringUnit"]["value"],
                    expected,
                )

    def test_data_health_copy_is_complete_and_idiomatic(self) -> None:
        expected_english = {
            "数据链正常": "Data health looks good",
            "有数据需要检查": "Some data needs attention",
            "复制诊断": "Copy diagnostics",
            "内建视网膜显示器": "Built-in Retina Display",
            "导出观察数据": "Export usage data",
            "清除观察数据": "Clear usage data",
            "暂无已完成工作单元": "No completed tasks yet",
        }
        for key, expected in expected_english.items():
            with self.subTest(key=key):
                self.assertEqual(
                    self.strings[key]["localizations"]["en"]["stringUnit"]["value"],
                    expected,
                )

    def test_scan_profile_copy_does_not_call_profiles_efforts(self) -> None:
        expected_english = {
            "扫描档位": "Scan profiles",
            "常规扫描档位": "Regular scan profiles",
            "目录档位": "Catalog profiles",
            "已启用档位": "Enabled profiles",
            "已启用／目录档位": "Enabled / catalog profiles",
            "已选择 %@/%@ 个档位": "%@/%@ profiles selected",
            "档位": "Profiles",
        }
        for key, expected in expected_english.items():
            with self.subTest(key=key):
                self.assertEqual(
                    self.strings[key]["localizations"]["en"]["stringUnit"]["value"],
                    expected,
                )

        settings = (ROOT / "Sources" / "Views" / "SettingsView.swift").read_text(
            encoding="utf-8"
        )
        self.assertIn('L10n.tr("%d 个模型簇，%d/%d 已启用"', settings)
        self.assertIn('L10n.tr("%d 个模型，%d/%d 已启用"', settings)
        self.assertIn("let localizedItemLabel = L10n.tr(itemLabel)", settings)

    def test_model_ingress_dynamic_copy_is_localized_as_complete_phrases(self) -> None:
        expected_english = {
            "已接入": "Connected",
            "已接入 %d": "%d connected",
            "%@ · %d 个模型簇": "%@ · Families: %d",
            "%d 个模型簇，%d/%d 已启用": (
                "Families: %d · Enabled: %d/%d"
            ),
            "%d 个模型，%d/%d 已启用": "Models: %d · %d/%d enabled",
            "%@：%d · %d/%d 已开启 · %@": "%@: %d · %d/%d enabled · %@",
            "扫描一次": "Run a scan",
            "可参与推荐": "Ready for recommendations",
            "发现可用模型": "Discover models",
            "整组开启": "All enabled",
            "整组关闭": "All disabled",
            "部分开启": "Partially enabled",
            "网络连接失败；仍可手工填写准确的 Model ID。": (
                "Network connection failed. You can still enter the exact Model ID manually."
            ),
        }
        for key, expected in expected_english.items():
            with self.subTest(key=key):
                self.assertEqual(
                    self.strings[key]["localizations"]["en"]["stringUnit"]["value"],
                    expected,
                )

        settings = (ROOT / "Sources" / "Views" / "SettingsView.swift").read_text(
            encoding="utf-8"
        )
        for call in (
            'return L10n.tr("已接入")',
            'Text(L10n.tr("已接入 %d", connectionCount))',
            "Text(inventoryText)",
            "Text(L10n.tr(metric.value))",
            "Text(L10n.tr(metric.label))",
            'Button(L10n.tr("发现可用模型"))',
        ):
            with self.subTest(call=call):
                self.assertIn(call, settings)

    def test_product_copy_avoids_internal_jargon_in_both_languages(self) -> None:
        expected = {
            "ModelDial 记录到的变化": (
                "Observed after switching",
                "切换后的实际变化",
            ),
            "overview.header.recommendation_decision": ("Recommendation", "推荐"),
            "本机实测": ("Local results", "本机结果"),
            "官网榜单": ("ModelDial ranking", "ModelDial 榜单"),
            "来自后端权威对比投影；缺失字段保持不可用。": (
                "Based on comparable local results; unavailable data is left blank.",
                "基于同口径本机结果；缺失数据保持为空。",
            ),
            "个人观察数据": ("Local usage data", "本机使用记录"),
            "任务并发": ("Parallel tasks", "并行任务数"),
            "当前门禁": ("Current decision rule", "当前判定规则"),
            "选择全局推荐策略": (
                "Choose what ModelDial should optimize",
                "选择推荐时优先考虑的目标",
            ),
            "检测到覆盖空窗": ("History gap detected", "检测到记录缺口"),
            "等待后端推荐投影。": (
                "Waiting for recommendation results.",
                "正在等待建议结果。",
            ),
            "one-shot %d%%": ("No-retry rate %d%%", "一次完成 %d%%"),
            "api_key": ("API key", "API 密钥"),
            "候选 Endpoint 路由证据已变化": (
                "The candidate connection has changed",
                "候选连接已发生变化",
            ),
        }
        for key, (english, chinese) in expected.items():
            with self.subTest(key=key):
                localizations = self.strings[key]["localizations"]
                self.assertEqual(localizations["en"]["stringUnit"]["value"], english)
                self.assertEqual(
                    localizations["zh-Hans"]["stringUnit"]["value"], chinese
                )


if __name__ == "__main__":
    unittest.main()
