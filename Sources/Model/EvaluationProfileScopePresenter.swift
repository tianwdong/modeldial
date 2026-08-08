import Foundation

enum EvaluationProfileScopePresenter {
    struct CandidateDelta: Equatable {
        let originalCandidateIDs: Set<String>
        let currentCandidateIDs: Set<String>
        let addedCandidateIDs: Set<String>
        let removedCandidateIDs: Set<String>

        var originalCount: Int { originalCandidateIDs.count }
        var currentCount: Int { currentCandidateIDs.count }
        var addedCount: Int { addedCandidateIDs.count }
        var removedCount: Int { removedCandidateIDs.count }
    }

    struct Input: Equatable {
        let isProvisional: Bool
        let originalCandidateIDs: [String]
        let currentCandidateIDs: [String]
        let upgradeProfileLabel: String?
    }

    struct Presentation: Equatable {
        let delta: CandidateDelta?
        let requiresDecision: Bool
        let originalRoundUpgradeTitle: String
        let currentSelectionFullScanTitle: String
        let decisionMessage: String
    }

    static func present(_ input: Input) -> Presentation {
        let originalCandidateIDs = Set(input.originalCandidateIDs)
        let currentCandidateIDs = Set(input.currentCandidateIDs)
        let delta: CandidateDelta?
        if input.isProvisional,
           !originalCandidateIDs.isEmpty,
           originalCandidateIDs != currentCandidateIDs {
            delta = CandidateDelta(
                originalCandidateIDs: originalCandidateIDs,
                currentCandidateIDs: currentCandidateIDs,
                addedCandidateIDs: currentCandidateIDs.subtracting(originalCandidateIDs),
                removedCandidateIDs: originalCandidateIDs.subtracting(currentCandidateIDs)
            )
        } else {
            delta = nil
        }

        let currentCount = delta?.currentCount ?? currentCandidateIDs.count
        let profileLabel = input.upgradeProfileLabel ?? L10n.tr("完整评测")
        return Presentation(
            delta: delta,
            requiresDecision: delta != nil,
            originalRoundUpgradeTitle: delta.map {
                L10n.tr("只补全原 %lld 个", $0.originalCount)
            } ?? L10n.tr("补全原轮"),
            currentSelectionFullScanTitle: L10n.tr(
                "补全当前 %lld 个为%@",
                currentCount,
                profileLabel
            ),
            decisionMessage: decisionMessage(delta)
        )
    }

    private static func decisionMessage(_ delta: CandidateDelta?) -> String {
        guard let delta else {
            return L10n.tr("请选择本轮要评测的模型范围。")
        }
        if delta.currentCount == 0 {
            return L10n.tr(
                "原轮包含 %lld 个模型，当前没有启用模型。只能补全原轮并复用已有题目结果，或取消后先选择模型。",
                delta.originalCount
            )
        }
        if delta.addedCount > 0 && delta.removedCount == 0 {
            return L10n.tr(
                "原轮包含 %lld 个模型，当前新增 %lld 个。补全当前选择会复用已有题目结果：原模型只跑剩余题，新模型跑完整题包。",
                delta.originalCount,
                delta.addedCount
            )
        }
        if delta.addedCount == 0 && delta.removedCount > 0 {
            return L10n.tr(
                "原轮包含 %lld 个模型，当前移除 %lld 个。补全当前选择会复用已有题目结果；已移除模型仅保留在历史表现，不参与本轮。",
                delta.originalCount,
                delta.removedCount
            )
        }
        return L10n.tr(
            "模型范围已变化：新增 %lld 个、移除 %lld 个。补全当前选择会复用已有题目结果，仅执行当前 %lld 个模型缺失的题目；已移除模型仍保留在历史表现。",
            delta.addedCount,
            delta.removedCount,
            delta.currentCount
        )
    }
}
