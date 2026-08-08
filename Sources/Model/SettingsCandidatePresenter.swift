import Foundation

struct SettingsCandidatePresentation: Equatable {
    let providerID: String?
    let familyID: String
    let variantID: String?
    let rawModelID: String
    let displayModel: String
    let displayScanProfile: String?

    var displayName: String {
        guard let displayScanProfile else { return displayModel }
        return "\(displayModel) [\(displayScanProfile)]"
    }

    var variantName: String {
        displayScanProfile ?? L10n.tr("默认档位")
    }
}

enum SettingsCandidateEvidenceTone: Equatable {
    case muted
    case warning
    case accent
}

struct SettingsCandidateEvidencePresentation: Equatable {
    let text: String
    let tone: SettingsCandidateEvidenceTone
}

enum SettingsCandidatePresenter {
    static func presentation(
        for candidate: BridgeIngressModelCandidate,
        projection: BridgeSettingsCandidateProjection?
    ) -> SettingsCandidatePresentation {
        guard let projection, projection.candidateId == candidate.id else {
            return SettingsCandidatePresentation(
                providerID: nil,
                familyID: candidate.modelId,
                variantID: nil,
                rawModelID: candidate.modelId,
                displayModel: candidate.modelId,
                displayScanProfile: nil
            )
        }
        let rawModelID = nonEmpty(projection.modelId) ?? candidate.modelId
        let displayProfile = nonEmpty(projection.displayScanProfile)
        return SettingsCandidatePresentation(
            providerID: nonEmpty(projection.providerId),
            familyID: nonEmpty(projection.familyId) ?? rawModelID,
            variantID: nonEmpty(projection.variantId),
            rawModelID: rawModelID,
            displayModel: nonEmpty(projection.displayModel) ?? rawModelID,
            displayScanProfile: ["default", "codex_default"].contains(
                displayProfile?.lowercased() ?? ""
            ) ? nil : displayProfile
        )
    }

    static func providerID(
        for candidates: [BridgeIngressModelCandidate],
        projectionsByCandidateID: [String: BridgeSettingsCandidateProjection],
        fallbackProviderID: String?
    ) -> String? {
        for candidate in candidates {
            let projectedProviderID = presentation(
                for: candidate,
                projection: projectionsByCandidateID[candidate.id]
            ).providerID
            if let projectedProviderID {
                return projectedProviderID
            }
        }
        return nonEmpty(fallbackProviderID)
    }

    static func evidencePresentation(
        for evidence: BridgeEvidenceCard?
    ) -> SettingsCandidateEvidencePresentation {
        guard let evidence,
              evidence.questionCompleted > 0 || evidence.latestValidAt != nil else {
            return SettingsCandidateEvidencePresentation(
                text: L10n.tr("暂无有效成绩"),
                tone: .muted
            )
        }
        if evidence.isUsingPreviousValidResult {
            return SettingsCandidateEvidencePresentation(
                text: L10n.tr("本次重扫失败 · 保留 %@", evidence.scoreText),
                tone: .warning
            )
        }
        if !evidence.isCurrentPackComparable {
            return SettingsCandidateEvidencePresentation(
                text: L10n.tr("题包已更新 · 需要重扫"),
                tone: .muted
            )
        }
        if let validAt = evidence.latestValidAt {
            return SettingsCandidateEvidencePresentation(
                text: L10n.tr(
                    "有效成绩 %@ · %@",
                    evidence.scoreText,
                    displayEvidenceTime(validAt)
                ),
                tone: .accent
            )
        }
        guard evidence.questionCount > 0 else {
            return SettingsCandidateEvidencePresentation(
                text: L10n.tr("暂无有效成绩"),
                tone: .muted
            )
        }
        return SettingsCandidateEvidencePresentation(
            text: L10n.tr("有效成绩 %@", evidence.scoreText),
            tone: .accent
        )
    }

    private static func displayEvidenceTime(_ value: String) -> String {
        String(value.prefix(16)).replacingOccurrences(of: "T", with: " ")
    }

    private static func nonEmpty(_ value: String?) -> String? {
        guard let normalized = value?.trimmingCharacters(in: .whitespacesAndNewlines),
              !normalized.isEmpty else {
            return nil
        }
        return normalized
    }
}
