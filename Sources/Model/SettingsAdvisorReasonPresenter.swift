struct SettingsAdvisorReasonPresentation: Equatable {
    let text: String
}

enum SettingsAdvisorReasonPresenter {
    static func presentation(for reason: String?) -> SettingsAdvisorReasonPresentation {
        SettingsAdvisorReasonPresentation(text: text(for: reason))
    }

    private static func text(for reason: String?) -> String {
        switch reason {
        case "current_identity_unmapped": return L10n.tr("当前模型身份未确认")
        case "current_route_not_current": return L10n.tr("当前 Endpoint 路由证据已变化")
        case "current_evaluation_incomplete": return L10n.tr("当前配置缺少同版完整评测")
        case "current_evaluation_not_fresh": return L10n.tr("当前配置评测已过期")
        case "quota_exhausted_no_alternative": return L10n.tr("额度耗尽且没有质量合格候选")
        case "candidate_route_not_current": return L10n.tr("候选 Endpoint 路由证据已变化")
        case "candidate_evaluation_missing": return L10n.tr("候选缺少同版完整评测")
        case "no_candidate_passed_guard": return L10n.tr("没有候选通过质量护栏")
        case "workload_preview_missing": return L10n.tr("真实工作样本未达到预览门槛")
        case "no_material_benefit": return L10n.tr("候选收益未达到实质门槛")
        case "workload_sample_preview_only": return L10n.tr("真实工作样本仅达到预览门槛")
        case "confidence_below_medium": return L10n.tr("证据置信度仍低于中等")
        case "material_benefit_with_qualified_evidence": return L10n.tr("候选收益和证据已达到试用门槛")
        case "material_time_gain": return L10n.tr("候选时间收益已达到建议门槛")
        case "material_reference_cost_gain": return L10n.tr("候选成本收益已达到建议门槛")
        case "material_efficiency_gain": return L10n.tr("候选效率收益已达到建议门槛")
        case "material_quality_gain": return L10n.tr("候选质量收益已达到建议门槛")
        case "quality_gain_with_tradeoff": return L10n.tr("候选质量提升，但伴随效率取舍")
        case "no_eligible_candidate": return L10n.tr("没有满足当前策略的可用候选")
        case "current_needs_test": return L10n.tr("当前配置仍需完成评测")
        case "current_stale": return L10n.tr("当前配置评测已过期")
        case "no_usage": return L10n.tr("尚未识别可用于建议的当前配置")
        case "needs_test": return L10n.tr("当前配置仍需完成评测")
        case "stale": return L10n.tr("当前配置评测已过期")
        case "recommend": return L10n.tr("候选收益已达到建议门槛")
        case "keep": return L10n.tr("当前配置仍是更稳妥选择")
        case .none, "unresolved": return L10n.tr("建议尚未形成")
        default: return L10n.tr("建议门禁未识别")
        }
    }
}
