import Foundation

enum BridgeScanSelectionMode: String, Equatable, Hashable {
    case regular
    case custom
    case single
    case incrementalFull = "incremental_full"
}

enum BridgeCustomRoundMode: String, Equatable, Hashable {
    case append
    case newRound = "new_round"
}

struct BridgeScanIntent: Equatable, Hashable {
    let forceRestart: Bool
    let candidateIDs: [String]?
    let selectionMode: BridgeScanSelectionMode
    let customRoundMode: BridgeCustomRoundMode?
    let evaluationProfileID: String?
    let upgradeFromRunID: String?

    init(
        forceRestart: Bool = false,
        candidateIDs: [String]? = nil,
        selectionMode: BridgeScanSelectionMode = .regular,
        customRoundMode: BridgeCustomRoundMode? = nil,
        evaluationProfileID: String? = nil,
        upgradeFromRunID: String? = nil
    ) {
        self.forceRestart = forceRestart
        self.candidateIDs = candidateIDs
        self.selectionMode = selectionMode
        self.customRoundMode = customRoundMode
        self.evaluationProfileID = evaluationProfileID
        self.upgradeFromRunID = upgradeFromRunID
    }

    func applying(_ preview: BridgeScanPlanPreview) -> BridgeScanIntent? {
        guard preview.valid,
              let selectionMode = BridgeScanSelectionMode(
                  rawValue: preview.requestedSelectionMode
              ),
              selectionMode == self.selectionMode else {
            return nil
        }
        let customRoundMode: BridgeCustomRoundMode?
        if selectionMode == .custom {
            customRoundMode = BridgeCustomRoundMode(
                rawValue: preview.requestedCustomRoundMode
            )
            guard customRoundMode != nil, customRoundMode == self.customRoundMode else {
                return nil
            }
        } else {
            customRoundMode = nil
        }
        let plannedCandidateIDs: [String]?
        if upgradeFromRunID != nil, candidateIDs == nil {
            plannedCandidateIDs = nil
        } else {
            plannedCandidateIDs = preview.requestedCandidateIds.isEmpty
                ? nil
                : preview.requestedCandidateIds
        }
        return BridgeScanIntent(
            forceRestart: forceRestart,
            candidateIDs: plannedCandidateIDs,
            selectionMode: selectionMode,
            customRoundMode: customRoundMode,
            evaluationProfileID: preview.profile.id,
            upgradeFromRunID: upgradeFromRunID
        )
    }
}

struct BridgeScanPlanPreviewProfile: Decodable, Equatable {
    let id: String?
    let label: String?
    let questionCount: Int?
}

struct BridgeScanPlanPreviewComparisonGroup: Decodable, Equatable {
    let id: String?
    let mode: String?
    let parentRunId: String?
    let appendTargetGroupId: String?
}

struct BridgeScanPlanPreview: Decodable, Equatable {
    let schemaVersion: Int
    let valid: Bool
    let reason: String?
    let message: String?
    let requestedSelectionMode: String
    let requestedCustomRoundMode: String
    let executionSelectionMode: String?
    let executionCustomRoundMode: String?
    let profile: BridgeScanPlanPreviewProfile
    let requestedCandidateIds: [String]
    let effectiveCandidateIds: [String]
    let executionCandidateIds: [String]
    let regularCandidateIds: [String]
    let appendedCandidateIds: [String]
    let skippedCandidateIds: [String]
    let comparisonGroup: BridgeScanPlanPreviewComparisonGroup
    let totalEvaluations: Int
    let completedEvaluations: Int

    private enum CodingKeys: String, CodingKey {
        case schemaVersion
        case valid
        case reason
        case message
        case requestedSelectionMode
        case requestedCustomRoundMode
        case executionSelectionMode
        case executionCustomRoundMode
        case profile
        case requestedCandidateIds
        case effectiveCandidateIds
        case executionCandidateIds
        case regularCandidateIds
        case appendedCandidateIds
        case skippedCandidateIds
        case comparisonGroup
        case totalEvaluations
        case completedEvaluations
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decode(Int.self, forKey: .schemaVersion)
        guard schemaVersion == 1 else {
            throw DecodingError.dataCorruptedError(
                forKey: .schemaVersion,
                in: container,
                debugDescription: "Unsupported scan plan preview schema version: \(schemaVersion)"
            )
        }
        valid = try container.decode(Bool.self, forKey: .valid)
        reason = try container.decodeIfPresent(String.self, forKey: .reason)
        message = try container.decodeIfPresent(String.self, forKey: .message)
        requestedSelectionMode = try container.decode(
            String.self,
            forKey: .requestedSelectionMode
        )
        requestedCustomRoundMode = try container.decode(
            String.self,
            forKey: .requestedCustomRoundMode
        )
        executionSelectionMode = try container.decodeIfPresent(
            String.self,
            forKey: .executionSelectionMode
        )
        executionCustomRoundMode = try container.decodeIfPresent(
            String.self,
            forKey: .executionCustomRoundMode
        )
        profile = try container.decode(
            BridgeScanPlanPreviewProfile.self,
            forKey: .profile
        )
        requestedCandidateIds = try container.decode(
            [String].self,
            forKey: .requestedCandidateIds
        )
        effectiveCandidateIds = try container.decode(
            [String].self,
            forKey: .effectiveCandidateIds
        )
        executionCandidateIds = try container.decode(
            [String].self,
            forKey: .executionCandidateIds
        )
        regularCandidateIds = try container.decode(
            [String].self,
            forKey: .regularCandidateIds
        )
        appendedCandidateIds = try container.decode(
            [String].self,
            forKey: .appendedCandidateIds
        )
        skippedCandidateIds = try container.decode(
            [String].self,
            forKey: .skippedCandidateIds
        )
        comparisonGroup = try container.decode(
            BridgeScanPlanPreviewComparisonGroup.self,
            forKey: .comparisonGroup
        )
        totalEvaluations = try container.decode(Int.self, forKey: .totalEvaluations)
        completedEvaluations = try container.decode(
            Int.self,
            forKey: .completedEvaluations
        )
    }
}

struct BridgeCustomScanPlanOptions: Decodable, Equatable {
    let schemaVersion: Int
    let newRound: BridgeScanPlanPreview
    let append: BridgeScanPlanPreview

    private enum CodingKeys: String, CodingKey {
        case schemaVersion
        case newRound
        case append
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decode(Int.self, forKey: .schemaVersion)
        guard schemaVersion == 1 else {
            throw DecodingError.dataCorruptedError(
                forKey: .schemaVersion,
                in: container,
                debugDescription: "Unsupported custom scan options schema version: \(schemaVersion)"
            )
        }
        newRound = try container.decode(BridgeScanPlanPreview.self, forKey: .newRound)
        append = try container.decode(BridgeScanPlanPreview.self, forKey: .append)
    }
}

struct ScanPlanOptionPresentation: Equatable {
    let isEnabled: Bool
    let subtitle: String
}

enum ScanPlanPreviewPresenter {
    static func option(
        for preview: BridgeScanPlanPreview,
        isAppend: Bool
    ) -> ScanPlanOptionPresentation {
        guard preview.valid else {
            return ScanPlanOptionPresentation(
                isEnabled: false,
                subtitle: failureText(reason: preview.reason)
            )
        }
        if isAppend {
            return ScanPlanOptionPresentation(
                isEnabled: true,
                subtitle: "新增 \(preview.appendedCandidateIds.count) 个配置 · \(preview.totalEvaluations) 次待评测"
            )
        }
        return ScanPlanOptionPresentation(
            isEnabled: true,
            subtitle: "新建一轮 · \(preview.effectiveCandidateIds.count) 个配置 · \(preview.totalEvaluations) 次评测"
        )
    }

    static func failureText(reason: String?) -> String {
        switch reason {
        case "quick_candidate_count":
            return "快速对比需要选择 2 个配置"
        case "quick_recommendation_pair_unavailable":
            return "暂无唯一可用的建议配置，请在“自定义本轮”中选择两个配置"
        case "append_profile_mismatch":
            return "当前扫描档位与上一轮不一致"
        case "append_no_new_candidates":
            return "所选配置都已包含在当前轮"
        case "append_no_current_round":
            return "当前没有可追加的评测轮次"
        case "incremental_no_reusable_evidence":
            return "没有可复用的兼容结果"
        case "incremental_already_complete":
            return "全部配置已有兼容结果"
        case "incremental_scope_mismatch":
            return "增量补齐必须覆盖全部已启用配置"
        case "incremental_profile_required":
            return "增量补齐必须使用完整评测"
        default:
            return "当前选择无法生成扫描计划"
        }
    }
}
