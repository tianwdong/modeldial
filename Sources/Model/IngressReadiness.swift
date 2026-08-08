import Foundation

enum IngressReadinessState: String {
    case disabled
    case needsConfiguration = "needs_configuration"
    case needsConnectionTest = "needs_connection_test"
    case needsModelSelection = "needs_model_selection"
    case needsBaseline = "needs_baseline"
    case ready
}

enum IngressReadinessAction {
    case none
    case manageConnection
    case testConnection
    case selectModels
    case scanBaseline
}

struct IngressReadiness {
    let state: IngressReadinessState
    let title: String
    let detail: String
    let action: IngressReadinessAction
    let completedStepCount: Int
    let enabledCandidateIDs: [String]
    let baselineCandidateIDs: [String]

    static func present(
        _ projection: BridgeSettingsConnectionProjection?
    ) -> IngressReadiness {
        guard let projection else {
            return make(
                .disabled,
                title: L10n.tr("状态同步中"),
                detail: L10n.tr("等待后端返回连接状态。"),
                action: .none,
                completedStepCount: 0,
                enabledCandidateIDs: [],
                baselineCandidateIDs: []
            )
        }

        guard let state = IngressReadinessState(
            rawValue: projection.recommendationStatus
        ) else {
            return make(
                .disabled,
                title: L10n.tr("状态不可用"),
                detail: L10n.tr("后端返回了未支持的推荐状态。"),
                action: .none,
                completedStepCount: 0,
                enabledCandidateIDs: projection.enabledCandidateIds,
                baselineCandidateIDs: projection.baselineCandidateIds
            )
        }

        let action = presentationAction(
            for: projection.recommendationAction,
            state: state
        )
        switch state {
        case .disabled:
            return make(
                state,
                title: L10n.tr("未启用"),
                detail: detail(for: state, reason: projection.recommendationReason),
                action: action,
                projection: projection
            )
        case .needsConfiguration:
            return make(
                state,
                title: L10n.tr("待配置"),
                detail: detail(for: state, reason: projection.recommendationReason),
                action: action,
                projection: projection
            )
        case .needsConnectionTest:
            return make(
                state,
                title: L10n.tr("待测试"),
                detail: detail(for: state, reason: projection.recommendationReason),
                action: action,
                projection: projection
            )
        case .needsModelSelection:
            return make(
                state,
                title: L10n.tr("待选择"),
                detail: detail(for: state, reason: projection.recommendationReason),
                action: action,
                projection: projection
            )
        case .needsBaseline:
            return make(
                state,
                title: L10n.tr("待扫描"),
                detail: L10n.tr("模型已经准备好；完成首次扫描后即可参与推荐。"),
                action: action,
                projection: projection
            )
        case .ready:
            return make(
                state,
                title: L10n.tr("可推荐"),
                detail: L10n.tr("已有本轮题目的扫描成绩，可以参与推荐比较。"),
                action: action,
                projection: projection
            )
        }
    }

    private static func detail(
        for state: IngressReadinessState,
        reason: String
    ) -> String {
        switch state {
        case .disabled:
            return L10n.tr("开启来源与连接后，才会进入扫描范围。")
        case .needsConfiguration:
            return L10n.tr("补齐 Base URL 与 API Key。")
        case .needsConnectionTest:
            if reason == "local_login_unverified" {
                return L10n.tr("先验证本机登录状态。")
            }
            return L10n.tr("先发送一次最小真实请求验证连接。")
        case .needsModelSelection:
            return reason == "no_candidates_configured"
                ? L10n.tr("先发现或手工添加模型。")
                : L10n.tr("至少开启一个扫描档位。")
        case .needsBaseline:
            return L10n.tr("模型已经准备好；完成首次扫描后即可参与推荐。")
        case .ready:
            return L10n.tr("已有本轮题目的扫描成绩，可以参与推荐比较。")
        }
    }

    private static func presentationAction(
        for action: String,
        state: IngressReadinessState
    ) -> IngressReadinessAction {
        if state == .disabled {
            return .none
        }
        switch action {
        case "configure_connection", "repair_configuration":
            return .manageConnection
        case "test_connection":
            return .testConnection
        case "enable_candidate", "add_candidate":
            return .selectModels
        case "scan_baseline":
            return .scanBaseline
        default:
            return .none
        }
    }

    private static func make(
        _ state: IngressReadinessState,
        title: String,
        detail: String,
        action: IngressReadinessAction,
        completedStepCount: Int,
        enabledCandidateIDs: [String],
        baselineCandidateIDs: [String]
    ) -> IngressReadiness {
        IngressReadiness(
            state: state,
            title: title,
            detail: detail,
            action: action,
            completedStepCount: completedStepCount,
            enabledCandidateIDs: enabledCandidateIDs,
            baselineCandidateIDs: baselineCandidateIDs
        )
    }

    private static func make(
        _ state: IngressReadinessState,
        title: String,
        detail: String,
        action: IngressReadinessAction,
        projection: BridgeSettingsConnectionProjection
    ) -> IngressReadiness {
        make(
            state,
            title: title,
            detail: detail,
            action: action,
            completedStepCount: projection.completedStepCount,
            enabledCandidateIDs: projection.enabledCandidateIds,
            baselineCandidateIDs: projection.baselineCandidateIds
        )
    }
}
