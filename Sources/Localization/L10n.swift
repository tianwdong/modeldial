import Foundation

enum L10n {
    static var locale: Locale {
        AppLanguageResolver.locale
    }

    static func text(_ key: String, fallback: String) -> String {
        AppLanguageResolver.bundle.localizedString(
            forKey: key,
            value: fallback,
            table: "Localizable"
        )
    }

    static func tr(_ source: String) -> String {
        text(source, fallback: source)
    }

    static func tr(_ source: String, _ arguments: CVarArg...) -> String {
        String(
            format: tr(source),
            locale: locale,
            arguments: arguments
        )
    }

    static func format(
        _ key: String,
        fallback: String,
        _ arguments: CVarArg...
    ) -> String {
        String(
            format: text(key, fallback: fallback),
            locale: locale,
            arguments: arguments
        )
    }

    enum Common {
        static var collapse: String { text("common.collapse", fallback: "收起") }
        static var view: String { text("common.view", fallback: "查看") }
        static var rescan: String { text("common.rescan", fallback: "重扫") }
        static var done: String { text("common.done", fallback: "完成") }
    }

    enum Language {
        static var sectionTitle: String {
            text("settings.general.language.section", fallback: "语言与地区")
        }

        static var language: String {
            text("settings.general.language.label", fallback: "语言")
        }

        static var followSystem: String {
            text("settings.general.language.system", fallback: "跟随系统")
        }

        static var immediateFooter: String {
            text(
                "settings.general.language.immediate_footer",
                fallback: "更改会立即应用到 modeldial 全部窗口。"
            )
        }
    }

    enum Update {
        static var sectionTitle: String {
            text("settings.general.update.section", fallback: "软件更新")
        }

        static var currentVersion: String {
            text("settings.general.update.current_version", fallback: "当前版本")
        }

        static func versionBuild(version: String, build: String) -> String {
            format(
                "settings.general.update.version_build",
                fallback: "%@（Build %@）",
                version,
                build
            )
        }

        static var checkNow: String {
            text("settings.general.update.check_now", fallback: "立即检查")
        }

        static var status: String {
            text("settings.general.update.status", fallback: "检查结果")
        }

        static var notChecked: String {
            text("settings.general.update.not_checked", fallback: "尚未检查更新")
        }

        static var checking: String {
            text("settings.general.update.checking", fallback: "正在检查更新")
        }

        static var upToDate: String {
            text("settings.general.update.up_to_date", fallback: "当前已是最新版本")
        }

        static var updateAvailable: String {
            text("settings.general.update.update_available", fallback: "发现新版本，正在打开更新…")
        }

        static var notConfigured: String {
            text("settings.general.update.not_configured", fallback: "当前构建未配置更新服务")
        }

        static var unsupportedSystem: String {
            text("settings.general.update.unsupported_system", fallback: "当前系统无法安装可用更新")
        }

        static var checkFailed: String {
            text("settings.general.update.check_failed", fallback: "暂时无法检查更新")
        }

        static var preferencesTitle: String {
            text("settings.general.update.preferences", fallback: "更新偏好")
        }

        static var automaticChecks: String {
            text("settings.general.update.automatic_checks", fallback: "自动检查更新")
        }

        static var automaticDownloads: String {
            text("settings.general.update.automatic_downloads", fallback: "自动下载更新")
        }

        static var configuredFooter: String {
            text(
                "settings.general.update.configured_footer",
                fallback: "更新检查仅访问官方更新源，不上传系统画像。"
            )
        }

        static var notConfiguredFooter: String {
            text(
                "settings.general.update.not_configured_footer",
                fallback: "当前构建尚未配置更新源和签名公钥，无法检查更新。"
            )
        }
    }

    enum Island {
        static var openRecommendationDetails: String {
            text(
                "island.accessibility.open_recommendation_details",
                fallback: "打开推荐详情"
            )
        }
    }

    enum Sessions {
        static var title: String { text("sessions.active.title", fallback: "活动会话") }
        static var emptyTitle: String {
            text("sessions.active.empty.title", fallback: "没有检测到活动会话")
        }
        static var emptySubtitle: String {
            text("sessions.active.empty.subtitle", fallback: "支持已接入的本地终端会话")
        }

        static func count(_ count: Int) -> String {
            format("sessions.active.count", fallback: "%d 个", count)
        }

        static func overflow(_ count: Int) -> String {
            format("sessions.active.overflow", fallback: "另有 %d 个", count)
        }

        static func activeAccessibility(title: String, context: String) -> String {
            format(
                "sessions.active.accessibility",
                fallback: "%@，%@，活动中",
                title,
                context
            )
        }
    }

    enum Overview {
        static var recommendationDecision: String {
            text("overview.header.recommendation_decision", fallback: "推荐决策")
        }
        static var activeSessions: String { Sessions.title }
        static var connectModel: String {
            text("overview.action.connect_model", fallback: "接入模型")
        }
        static var selectCurrentModel: String {
            text("overview.action.comparison_baseline", fallback: "指定当前模型")
        }
        static var currentInUse: String {
            text("overview.current_in_use", fallback: "当前在用")
        }
        static var radarTab: String { text("overview.tab.overview", fallback: "雷达") }
        static var comparisonTab: String {
            text("overview.tab.comparison", fallback: "对比")
        }
    }

    enum EvaluationProfile {
        static func label(id: String, fallback: String) -> String {
            switch id {
            case "quick":
                return text("evaluation_profile.quick.label", fallback: fallback)
            case "full":
                return text("evaluation_profile.full.label", fallback: fallback)
            default:
                return fallback
            }
        }

        static func summary(id: String, fallback: String) -> String {
            switch id {
            case "quick":
                return text("evaluation_profile.quick.summary", fallback: fallback)
            case "full":
                return text("evaluation_profile.full.summary", fallback: fallback)
            default:
                return fallback
            }
        }
    }

    enum Question {
        static func shortLabel(_ number: Int) -> String {
            tr("题%d", number)
        }

        static func capability(id: String, fallback: String) -> String {
            switch id {
            case "black_box_regression_testing":
                return text(
                    "question.capability.black_box_regression_testing",
                    fallback: fallback
                )
            case "debug_counterexample":
                return text(
                    "question.capability.debug_counterexample",
                    fallback: fallback
                )
            case "ci_plan_audit":
                return text("question.capability.ci_plan_audit", fallback: fallback)
            case "state_machine_testing":
                return text(
                    "question.capability.state_machine_testing",
                    fallback: fallback
                )
            case "regression_validation":
                return text(
                    "question.capability.regression_validation",
                    fallback: fallback
                )
            default:
                return fallback
            }
        }
    }

    enum ScanActivity {
        static var inProgress: String {
            text("scan_activity.in_progress", fallback: "扫描进行中")
        }
        static var noHistory: String {
            text("scan_activity.no_history", fallback: "暂无扫描记录")
        }
        static var justCompleted: String {
            text("scan_activity.just_completed", fallback: "上次扫描刚刚完成")
        }

        static func lastCompleted(relativeTime: String) -> String {
            format(
                "scan_activity.last_completed",
                fallback: "上次扫描于%@",
                relativeTime
            )
        }
    }

    enum Glance {
        static var dataError: String { text("glance.data_error", fallback: "数据异常") }
        static var localDataUnavailable: String {
            text("glance.local_data_unavailable", fallback: "暂时无法读取本地数据")
        }
        static var scan: String { text("glance.scan", fallback: "扫描") }
        static var scanning: String { text("glance.scanning", fallback: "扫描中") }
        static var scanProgress: String { text("glance.scan_progress", fallback: "扫描进度") }
        static var repair: String { text("glance.repair", fallback: "重试") }
        static var repairProgress: String { text("glance.repair_progress", fallback: "重试进度") }
        static var prepare: String { text("glance.prepare", fallback: "准备") }
        static var preparingScan: String { text("glance.preparing_scan", fallback: "准备扫描") }
        static var runtimeStatus: String { text("glance.runtime_status", fallback: "运行状态") }
        static var preparing: String { text("glance.preparing", fallback: "准备中") }
        static var preparingAccessibility: String {
            text("glance.preparing_accessibility", fallback: "正在准备扫描")
        }
        static var undecided: String { text("glance.undecided", fallback: "未决") }
        static var resultUndecided: String { text("glance.result_undecided", fallback: "结果未决") }
        static var failed: String { text("glance.failed", fallback: "失败") }
        static var scanFailed: String { text("glance.scan_failed", fallback: "扫描失败") }
        static var pendingScan: String { text("glance.pending_scan", fallback: "待扫描") }
        static var neverScanned: String { text("glance.never_scanned", fallback: "尚未扫描") }
        static var stopping: String { text("glance.stopping", fallback: "停止中") }
        static var stoppingScan: String { text("glance.stopping_scan", fallback: "正在停止扫描") }
        static var stoppingDetail: String {
            text("glance.stopping_detail", fallback: "正在终止在途模型请求，请稍候")
        }
        static var progressBeforeStop: String {
            text("glance.progress_before_stop", fallback: "停止前进度")
        }
        static var pausing: String { text("glance.pausing", fallback: "暂停中") }
        static var pausingScan: String { text("glance.pausing_scan", fallback: "正在暂停扫描") }
        static var pausingDetail: String {
            text("glance.pausing_detail", fallback: "正在终止在途模型请求并保存断点")
        }
        static var progressBeforePause: String {
            text("glance.progress_before_pause", fallback: "暂停前进度")
        }
        static var pendingResume: String { text("glance.pending_resume", fallback: "待继续") }
        static var scanPendingResume: String {
            text("glance.scan_pending_resume", fallback: "扫描待继续")
        }
        static var finalizing: String { text("glance.finalizing", fallback: "整理") }
        static var finalizingResults: String {
            text("glance.finalizing_results", fallback: "正在整理结果")
        }
        static var finalizingValue: String {
            text("glance.finalizing_value", fallback: "整理中")
        }
        static var expired: String { text("glance.expired", fallback: "推荐过期") }
        static var recommendationExpired: String {
            text("glance.recommendation_expired", fallback: "推荐已过期")
        }
        static var multipleSessions: String { text("glance.multiple_sessions", fallback: "多会话") }
        static var currentStatus: String { text("glance.current_status", fallback: "当前状态") }
        static var leadingEffort: String { text("glance.leading_effort", fallback: "榜首档位") }
        static var recommendedEffort: String {
            text("glance.recommended_effort", fallback: "建议档位")
        }
        static var needsBaseline: String { text("glance.needs_baseline", fallback: "需指定") }
        static var pendingComparison: String { text("glance.pending_comparison", fallback: "待比较") }
        static var previousEffort: String { text("glance.previous_effort", fallback: "上次档位") }
        static var currentLeader: String { text("glance.current_leader", fallback: "当前榜首") }
        static var cacheFallback: String {
            text("glance.cache_fallback", fallback: "数据暂未更新，显示上次可用推荐")
        }
        static var failureFallback: String {
            text("glance.failure_fallback", fallback: "本轮失败，沿用上次推荐")
        }
        static var degraded: String {
            text("glance.degraded", fallback: "本轮部分结果异常，榜首仅基于有效样本")
        }
        static var currentEffortUncompared: String {
            text("glance.current_effort_uncompared", fallback: "当前在用档位尚未参与比较")
        }

        static func testing(_ target: String) -> String {
            format("glance.testing_target", fallback: "正在测试 %@", target)
        }

        static func lastCompleted(_ value: String) -> String {
            format("glance.last_completed", fallback: "上次完成于 %@", value)
        }

        static func lastUpdated(_ value: String) -> String {
            format("glance.last_updated", fallback: "上次更新于 %@", value)
        }

        static func previousRecommendation(model: String, effort: String) -> String {
            format(
                "glance.previous_recommendation",
                fallback: "上次推荐 %@ %@",
                model,
                effort
            )
        }

        static func mixedSessions(_ count: Int) -> String {
            format(
                "glance.mixed_sessions_detail",
                fallback: "检测到 %d 个活动会话正在混用模型",
                count
            )
        }
    }
}

enum LocalizedFormatters {
    static func shortDateTime(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = L10n.locale
        formatter.dateStyle = .short
        formatter.timeStyle = .short
        return formatter.string(from: date)
    }

    static func relativeDateTime(from date: Date, relativeTo referenceDate: Date) -> String {
        let formatter = RelativeDateTimeFormatter()
        formatter.locale = L10n.locale
        formatter.unitsStyle = .short
        return formatter.localizedString(for: date, relativeTo: referenceDate)
    }
}
