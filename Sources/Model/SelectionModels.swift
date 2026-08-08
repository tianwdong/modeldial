import Foundation

func checkedRoundedDurationSeconds(_ seconds: Double?) -> Int? {
    guard let seconds, seconds.isFinite, seconds >= 0 else { return nil }
    let rounded = seconds.rounded()
    guard rounded < Double(Int.max) else { return nil }
    return Int(rounded)
}

struct BridgeSnapshot: Decodable {
    let schemaVersion: Int
    let config: BridgeConfig
    let dashboard: BridgeDashboard
    let stableDashboard: BridgeDashboard?
    let stableEvidenceDashboard: BridgeDashboard?
    var runtime: BridgeRuntime
    let questionPack: BridgeQuestionPack
    let settingsProjection: BridgeSettingsProjection
    let codexInsights: BridgeCodexInsights?
    let advisor: BridgeAdvisorDecision?
    let diagnostics: BridgeDiagnosticSummary?
    let advisorV2Evidence: BridgeAdvisorV2Evidence
    let recommendationPortfolioV2: BridgeRecommendationPortfolioV2
    let referenceSnapshotFeed: BridgeReferenceSnapshotFeed
    let recommendationUse: BridgeRecommendationUseSummary

    private enum CodingKeys: String, CodingKey {
        case schemaVersion
        case config
        case dashboard
        case stableDashboard
        case stableEvidenceDashboard
        case runtime
        case questionPack
        case settingsProjection
        case codexInsights
        case advisor
        case diagnostics
        case advisorV2Evidence
        case recommendationPortfolioV2
        case referenceSnapshotFeed
        case recommendationUse
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decode(Int.self, forKey: .schemaVersion)
        guard schemaVersion == 2 else {
            throw DecodingError.dataCorruptedError(
                forKey: .schemaVersion,
                in: container,
                debugDescription: "Unsupported app snapshot schema version: \(schemaVersion)"
            )
        }
        config = try container.decode(BridgeConfig.self, forKey: .config)
        dashboard = try container.decode(BridgeDashboard.self, forKey: .dashboard)
        stableDashboard = try container.decodeIfPresent(BridgeDashboard.self, forKey: .stableDashboard)
        stableEvidenceDashboard = try container.decodeIfPresent(
            BridgeDashboard.self,
            forKey: .stableEvidenceDashboard
        )
        runtime = try container.decode(BridgeRuntime.self, forKey: .runtime)
        questionPack = try container.decode(BridgeQuestionPack.self, forKey: .questionPack)
        settingsProjection = try container.decode(
            BridgeSettingsProjection.self,
            forKey: .settingsProjection
        )
        codexInsights = try container.decodeIfPresent(BridgeCodexInsights.self, forKey: .codexInsights)
        advisor = try container.decodeIfPresent(BridgeAdvisorDecision.self, forKey: .advisor)
        diagnostics = try container.decodeIfPresent(BridgeDiagnosticSummary.self, forKey: .diagnostics)
        advisorV2Evidence = try container.decode(BridgeAdvisorV2Evidence.self, forKey: .advisorV2Evidence)
        recommendationPortfolioV2 = try container.decode(
            BridgeRecommendationPortfolioV2.self,
            forKey: .recommendationPortfolioV2
        )
        referenceSnapshotFeed = try container.decode(
            BridgeReferenceSnapshotFeed.self,
            forKey: .referenceSnapshotFeed
        )
        recommendationUse = try container.decode(
            BridgeRecommendationUseSummary.self,
            forKey: .recommendationUse
        )
    }

}

struct BridgeRecommendationDecisionIdentity: Equatable {
    let status: String
    let decision: String
    let currentConfigurationID: String?
    let targetConfigurationID: String?

    var isActionableRecommendation: Bool {
        status == "recommend"
            && decision == "recommend"
            && targetConfigurationID != nil
            && targetConfigurationID != currentConfigurationID
    }

    var fingerprint: String {
        [
            status,
            decision,
            currentConfigurationID ?? "none",
            targetConfigurationID ?? "none",
        ].joined(separator: "|")
    }
}

extension BridgeSnapshot {
    var recommendationDecisionIdentity: BridgeRecommendationDecisionIdentity? {
        let portfolio = recommendationPortfolioV2
        let representative = portfolio.representativeDecision
        let currentID = representative?.currentModelConfigurationId
            ?? portfolio.representativeConfigurationId
        let decision = representative?.decision ?? portfolio.status
        let targetID = decision == "recommend"
            ? representative?.candidateModelConfigurationId
            : currentID
        return BridgeRecommendationDecisionIdentity(
            status: portfolio.status,
            decision: decision,
            currentConfigurationID: currentID,
            targetConfigurationID: targetID
        )
    }
}

struct BridgeRefreshSnapshot: Decodable {
    let schemaVersion: Int
    let config: BridgeConfig
    let runtime: BridgeRuntime
    let questionPack: BridgeQuestionPack?
    let codexInsights: BridgeCodexInsights?
    let advisor: BridgeAdvisorDecision?
    let diagnostics: BridgeDiagnosticSummary?
    let recommendationUse: BridgeRecommendationUseSummary?

    private enum CodingKeys: String, CodingKey {
        case schemaVersion
        case config
        case runtime
        case questionPack
        case codexInsights
        case advisor
        case diagnostics
        case recommendationUse
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decode(Int.self, forKey: .schemaVersion)
        guard schemaVersion == 1 else {
            throw DecodingError.dataCorruptedError(
                forKey: .schemaVersion,
                in: container,
                debugDescription: "Unsupported refresh snapshot schema version: \(schemaVersion)"
            )
        }
        config = try container.decode(BridgeConfig.self, forKey: .config)
        runtime = try container.decode(BridgeRuntime.self, forKey: .runtime)
        questionPack = try container.decodeIfPresent(BridgeQuestionPack.self, forKey: .questionPack)
        codexInsights = try container.decodeIfPresent(BridgeCodexInsights.self, forKey: .codexInsights)
        advisor = try container.decodeIfPresent(BridgeAdvisorDecision.self, forKey: .advisor)
        diagnostics = try container.decodeIfPresent(BridgeDiagnosticSummary.self, forKey: .diagnostics)
        recommendationUse = try container.decodeIfPresent(BridgeRecommendationUseSummary.self, forKey: .recommendationUse)
    }
}

struct BridgeRecommendationUseSummary: Decodable {
    let schemaVersion: Int
    let epochs: [BridgeRecommendationUseEpoch]
    let representativeEpoch: BridgeRecommendationUseEpoch?
    let benefitSummary: BridgeRecommendationBenefitSummary?
    let valueSummary: BridgeRecommendationValueSummary?
}

struct BridgeRecommendationValueSummary: Decodable {
    let schemaVersion: Int
    let mode: String
    let periodStart: String?
    let periodEnd: String?
    let periodDays: Int?
    let currentModelConfigurationId: String?
    let candidateModelConfigurationId: String?
    let completedWorkUnitCount: Int
    let referenceCostUsd: Double?
    let referenceCostStatus: String
    let modelWaitMs: Int?
    let modelWaitStatus: String
    let modelWaitWorkUnitCount: Int
    let referenceCostDeltaUsd: Double?
    let modelWaitDeltaMs: Int?
    let pricingSnapshotId: String?
    let coverageComplete: Bool?
    let basis: String
}

struct BridgeRecommendationBenefitSummary: Decodable {
    let schemaVersion: Int
    let status: String
    let observedWorkUnitCount: Int
    let referenceCostWorkUnitCount: Int?
    let modelWaitWorkUnitCount: Int?
    let referenceCostDeltaUsd: Double?
    let modelWaitDeltaMs: Int?
    let referenceCostEpochCount: Int
    let modelWaitEpochCount: Int
    let latestObservedAt: String?
    let estimateBasis: String
}

struct BridgeRecommendationUseEpoch: Decodable, Identifiable {
    let schemaVersion: Int
    let useEpochId: String
    let recommendationId: String
    let currentModelConfigurationId: String
    let recommendedModelConfigurationId: String
    let resolvedDataSource: String?
    let evaluationSnapshotId: String?
    let pricingSnapshotId: String
    let startedAt: String
    let endedAt: String?
    let endReason: String?
    let observedCandidateSessionCount: Int
    let observedCandidateWorkUnitCount: Int
    let observedCandidateReferenceCostUsd: Double
    let observedCandidateResponseWaitMs: Int?
    let estimatedReferenceCostDeltaUsd: Double?
    let estimatedModelWaitDeltaMs: Int?
    let lifecycleStatus: String
    let estimateStatus: String
    let estimateBasis: String
    let attributionRouteBasis: String?

    var id: String { useEpochId }
}

struct BridgeAdvisorV2Evidence: Decodable {
    let schemaVersion: Int
    let sourceMode: String
    let resolvedDataSource: String?
    let sourceReason: String
    let sourceSnapshotId: String?
    let pricingSnapshotId: String?
    let currentModelConfigurationId: String?
    let currentStatus: String
    let eligibleCandidateIds: [String]
    let testableCandidateIds: [String]
    let candidateDecisions: [BridgeAdvisorV2CandidateDecision]
    let resolvedResultRows: [BridgeAdvisorV2ResultRow]
}

struct BridgeAdvisorV2CandidateDecision: Decodable {
    let modelConfigurationId: String
    let status: String
    let reasons: [String]
}

struct BridgeAdvisorV2ResultRow: Decodable {
    let modelConfigurationId: String
    let completedAt: String?
    let complete: Bool?
    let hardFailure: Bool?
    let questionPackVersion: String?
    let graderVersion: String?
    let routeFingerprint: String?
    let displayRank: Int?
    let overallScore: Double?
    let elapsedSeconds: Double?
    let estimatedCostUsd: Double?
    let costCoverage: String?
}

struct BridgeRecommendationPortfolioV2: Decodable {
    let schemaVersion: Int
    let sourceMode: String?
    let sourceModeByConfigurationId: [String: String]
    let resolvedDataSource: String?
    let sourceResolutionReason: String?
    let preference: String
    let representativeConfigurationId: String?
    let representativeReason: String?
    let representativeEvidence: BridgeAdvisorV2Evidence?
    let status: String
    let recommendationLifecycle: BridgeRecommendationLifecycleV1
    let decisions: [BridgeRecommendationDecisionV2]
    let testableCandidateIds: [String]
    let unmappedActiveSessionCount: Int

    var representativeDecision: BridgeRecommendationDecisionV2? {
        guard let representativeConfigurationId else { return decisions.first }
        return decisions.first {
            $0.currentModelConfigurationId == representativeConfigurationId
        } ?? decisions.first
    }

    private enum CodingKeys: String, CodingKey {
        case schemaVersion
        case sourceMode
        case sourceModeByConfigurationId
        case resolvedDataSource
        case sourceResolutionReason
        case preference
        case representativeConfigurationId
        case representativeReason
        case representativeEvidence
        case status
        case recommendationLifecycle
        case decisions
        case testableCandidateIds
        case unmappedActiveSessionCount
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decode(Int.self, forKey: .schemaVersion)
        sourceMode = try container.decodeIfPresent(String.self, forKey: .sourceMode)
        sourceModeByConfigurationId = try container.decodeIfPresent(
            [String: String].self,
            forKey: .sourceModeByConfigurationId
        ) ?? [:]
        resolvedDataSource = try container.decodeIfPresent(String.self, forKey: .resolvedDataSource)
        sourceResolutionReason = try container.decodeIfPresent(String.self, forKey: .sourceResolutionReason)
        preference = try container.decodeIfPresent(String.self, forKey: .preference) ?? "smart"
        representativeConfigurationId = try container.decodeIfPresent(
            String.self,
            forKey: .representativeConfigurationId
        )
        representativeReason = try container.decodeIfPresent(String.self, forKey: .representativeReason)
        representativeEvidence = try container.decodeIfPresent(
            BridgeAdvisorV2Evidence.self,
            forKey: .representativeEvidence
        )
        status = try container.decodeIfPresent(String.self, forKey: .status) ?? "needs_test"
        recommendationLifecycle = try container.decodeIfPresent(
            BridgeRecommendationLifecycleV1.self,
            forKey: .recommendationLifecycle
        ) ?? .none
        decisions = try container.decodeIfPresent(
            [BridgeRecommendationDecisionV2].self,
            forKey: .decisions
        ) ?? []
        testableCandidateIds = try container.decodeIfPresent(
            [String].self,
            forKey: .testableCandidateIds
        ) ?? []
        unmappedActiveSessionCount = try container.decodeIfPresent(
            Int.self,
            forKey: .unmappedActiveSessionCount
        ) ?? 0
    }
}

struct BridgeRecommendationLifecycleV1: Decodable {
    let schemaVersion: Int
    let status: String
    let trigger: String?
    let anchorConfigurationId: String?
    let adoptedConfigurationId: String?

    static let none = BridgeRecommendationLifecycleV1(
        schemaVersion: 1,
        status: "none",
        trigger: nil,
        anchorConfigurationId: nil,
        adoptedConfigurationId: nil
    )

    var isAdopted: Bool {
        status == "adopted"
    }

    var isNewProposal: Bool {
        status == "proposed" && trigger == "new_evidence"
    }
}

struct BridgeRecommendationDecisionV2: Decodable {
    let currentModelConfigurationId: String
    let candidateModelConfigurationId: String?
    let comparisonCandidateModelConfigurationId: String?
    let comparisonCandidateReasons: [String]?
    let decision: String
    let reason: String
    let qualityTradeoff: Bool
    let qualityWarningQuestionIds: [String]
    let qualityGuard: BridgeRecommendationQualityGuardV1?
    let quality: BridgeRecommendationQualityV2
    let time: BridgeRecommendationTimeV2
    let referenceCost: BridgeRecommendationCostV2
    let primaryBenefit: BridgeRecommendationPrimaryBenefitV2?
}

struct BridgeRecommendationQualityGuardV1: Decodable {
    let schemaVersion: Int
    let status: String
    let rule: String
    let preference: String
    let decision: String
    let thresholdPoints: Double?
    let scoreDeltaPoints: Double?
    let passed: Bool?
    let anchorConfigurationId: String?
}

struct BridgeRecommendationQualityV2: Decodable {
    let currentScore: Double?
    let candidateScore: Double?
    let scoreDelta: Double?
}

struct BridgeRecommendationTimeV2: Decodable {
    let currentSeconds: Double?
    let candidateSeconds: Double?
    let reductionPercent: Double?
}

struct BridgeRecommendationCostV2: Decodable {
    let currentUsd: Double?
    let candidateUsd: Double?
    let reductionPercent: Double?
}

struct BridgeRecommendationPrimaryBenefitV2: Decodable {
    let kind: String
    let reductionPercent: Double?
    let gainPoints: Double?
}

struct BridgeReferenceSnapshotProvenance: Decodable {
    let kind: String?
    let publicOfficialSnapshot: Bool?

    private enum CodingKeys: String, CodingKey {
        case kind
        case publicOfficialSnapshot
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        kind = try container.decodeIfPresent(String.self, forKey: .kind)
        publicOfficialSnapshot = try container.decodeIfPresent(
            Bool.self,
            forKey: .publicOfficialSnapshot
        )
    }
}

struct BridgeReferenceSnapshotFeed: Decodable {
    let schemaVersion: Int
    let status: String
    let kind: String?
    let latest: BridgeReferenceSnapshot?
    let snapshots: [BridgeReferenceSnapshot]
    let delivery: BridgeReferenceSnapshotDelivery?
    let freshness: String?
    let ageHours: Int?

    /// Only a snapshot with matching first-party kinds and an affirmative
    /// public-official provenance flag may enter official App surfaces.
    var trustedLatest: BridgeReferenceSnapshot? {
        guard let latest, latest.isPublicOfficialSnapshot else { return nil }
        return latest
    }

    private enum CodingKeys: String, CodingKey {
        case schemaVersion
        case status
        case kind
        case latest
        case snapshots
        case delivery
        case freshness
        case ageHours
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decode(Int.self, forKey: .schemaVersion)
        status = try container.decode(String.self, forKey: .status)
        kind = try container.decodeIfPresent(String.self, forKey: .kind)
        latest = try container.decodeIfPresent(BridgeReferenceSnapshot.self, forKey: .latest)
        snapshots = try container.decodeIfPresent(
            [BridgeReferenceSnapshot].self,
            forKey: .snapshots
        ) ?? []
        delivery = try container.decodeIfPresent(
            BridgeReferenceSnapshotDelivery.self,
            forKey: .delivery
        )
        freshness = try container.decodeIfPresent(String.self, forKey: .freshness)
        ageHours = try container.decodeIfPresent(Int.self, forKey: .ageHours)
    }
}

struct BridgeReferenceSnapshotDelivery: Decodable {
    let source: String
    let refreshStatus: String
    let errorCode: String?
}

struct BridgeReferenceSnapshot: Decodable, Identifiable {
    let schemaVersion: Int
    let kind: String
    let batchId: String
    let publishedAt: String
    let questionPackVersion: String
    let graderVersion: String
    let pricingSnapshotId: String?
    let entryCount: Int
    let entries: [BridgeReferenceSnapshotEntry]
    let pairwiseComparisons: [BridgePairwiseComparison]
    let leaderboardProjection: BridgeReferenceLeaderboardProjection?
    let provenance: BridgeReferenceSnapshotProvenance?

    var id: String { batchId }

    var isPublicOfficialSnapshot: Bool {
        kind == "first_party_snapshot"
            && provenance?.kind == "first_party_snapshot"
            && provenance?.publicOfficialSnapshot == true
    }

    private enum CodingKeys: String, CodingKey {
        case schemaVersion
        case kind
        case batchId
        case publishedAt
        case questionPackVersion
        case graderVersion
        case pricingSnapshotId
        case entryCount
        case entries
        case pairwiseComparisons
        case leaderboardProjection
        case provenance
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decode(Int.self, forKey: .schemaVersion)
        kind = try container.decode(String.self, forKey: .kind)
        batchId = try container.decode(String.self, forKey: .batchId)
        publishedAt = try container.decode(String.self, forKey: .publishedAt)
        questionPackVersion = try container.decode(String.self, forKey: .questionPackVersion)
        graderVersion = try container.decode(String.self, forKey: .graderVersion)
        pricingSnapshotId = try container.decodeIfPresent(String.self, forKey: .pricingSnapshotId)
        entryCount = try container.decode(Int.self, forKey: .entryCount)
        entries = try container.decode([BridgeReferenceSnapshotEntry].self, forKey: .entries)
        pairwiseComparisons = try container.decodeIfPresent(
            [BridgePairwiseComparison].self,
            forKey: .pairwiseComparisons
        ) ?? []
        leaderboardProjection = try container.decodeIfPresent(
            BridgeReferenceLeaderboardProjection.self,
            forKey: .leaderboardProjection
        )
        provenance = try container.decodeIfPresent(
            BridgeReferenceSnapshotProvenance.self,
            forKey: .provenance
        )
    }
}

struct BridgeReferenceLeaderboardProjection: Decodable {
    let schemaVersion: Int
    let source: String
    let rankingRule: String
    let trendRule: String
    let questions: [BridgeReferenceLeaderboardQuestion]
    let rows: [BridgeReferenceLeaderboardRow]
}

struct BridgeReferenceLeaderboardQuestion: Decodable, Identifiable {
    let id: String
    let shortLabel: String
    let title: String
    let capabilityId: String
    let capabilityLabel: String
    let detailLabel: String
    let ordinal: Int
}

struct BridgeReferenceLeaderboardRow: Decodable, Identifiable {
    let modelConfigurationId: String
    let rank: Int
    let targetLabels: [BridgeReferenceTargetLabel]
    let decisionTags: [BridgeReferenceDecisionTag]
    let questionScores: [BridgeReferenceQuestionScore]
    let trend: BridgeReferenceTrend

    var id: String { modelConfigurationId }

    private enum CodingKeys: String, CodingKey {
        case modelConfigurationId
        case rank
        case targetLabels
        case decisionTags
        case questionScores
        case trend
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        modelConfigurationId = try container.decode(
            String.self,
            forKey: .modelConfigurationId
        )
        rank = try container.decode(Int.self, forKey: .rank)
        targetLabels = try container.decodeIfPresent(
            [BridgeReferenceTargetLabel].self,
            forKey: .targetLabels
        ) ?? []
        decisionTags = try container.decodeIfPresent(
            [BridgeReferenceDecisionTag].self,
            forKey: .decisionTags
        ) ?? []
        questionScores = try container.decodeIfPresent(
            [BridgeReferenceQuestionScore].self,
            forKey: .questionScores
        ) ?? []
        trend = try container.decode(BridgeReferenceTrend.self, forKey: .trend)
    }
}

struct BridgeReferenceDecisionTag: Decodable, Identifiable {
    let kind: String
    let label: String?
    let detail: String?

    var id: String { kind }
}

struct BridgeReferenceTargetLabel: Decodable, Identifiable {
    let id: String
    let label: String
}

struct BridgeReferenceQuestionScore: Decodable, Identifiable {
    let questionId: String
    let score: Double

    var id: String { questionId }
}

struct BridgeReferenceTrend: Decodable {
    let compatibilityKey: String
    let sampleCount: Int
    let comparable: Bool
    let stableRankingEligible: Bool
    let points: [BridgeReferenceTrendPoint]
}

struct BridgeReferenceTrendPoint: Decodable, Identifiable {
    let batchId: String
    let publishedAt: String
    let score: Double
    let elapsedMs: Double

    var id: String { batchId }
}

struct BridgeReferenceSnapshotEntry: Decodable, Identifiable {
    let modelConfigurationId: String
    let modelConfiguration: BridgeReferenceModelConfiguration
    let advisorEligible: Bool
    let score: Double
    let maxScore: Double
    let elapsedMs: Double
    let estimatedApiCostUsd: Double?
    let costCoverage: String
    let questionScores: [String: Double]
    let completedAt: String?
    let failureCount: Int
    let hardFailureCount: Int
    let routeFingerprint: String?
    let usage: BridgeReferenceUsage?

    var id: String { modelConfigurationId }

    private enum CodingKeys: String, CodingKey {
        case modelConfigurationId
        case modelConfiguration
        case advisorEligible
        case score
        case maxScore
        case elapsedMs
        case estimatedApiCostUsd
        case costCoverage
        case questionScores
        case completedAt
        case failureCount
        case hardFailureCount
        case routeFingerprint
        case usage
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        modelConfigurationId = try container.decode(String.self, forKey: .modelConfigurationId)
        modelConfiguration = try container.decode(
            BridgeReferenceModelConfiguration.self,
            forKey: .modelConfiguration
        )
        advisorEligible = try container.decode(Bool.self, forKey: .advisorEligible)
        score = try container.decode(Double.self, forKey: .score)
        maxScore = try container.decode(Double.self, forKey: .maxScore)
        elapsedMs = try container.decode(Double.self, forKey: .elapsedMs)
        estimatedApiCostUsd = try container.decodeIfPresent(
            Double.self,
            forKey: .estimatedApiCostUsd
        )
        costCoverage = try container.decode(String.self, forKey: .costCoverage)
        questionScores = try container.decodeIfPresent(
            [String: Double].self,
            forKey: .questionScores
        ) ?? [:]
        completedAt = try container.decodeIfPresent(String.self, forKey: .completedAt)
        failureCount = try container.decodeIfPresent(Int.self, forKey: .failureCount) ?? 0
        hardFailureCount = try container.decodeIfPresent(Int.self, forKey: .hardFailureCount) ?? 0
        routeFingerprint = try container.decodeIfPresent(String.self, forKey: .routeFingerprint)
        usage = try container.decodeIfPresent(BridgeReferenceUsage.self, forKey: .usage)
    }
}

struct BridgeReferenceUsage: Decodable {
    let inputTokens: Int?
    let cachedInputTokens: Int?
    let cacheWriteInputTokens: Int?
    let outputTokens: Int?
    let reasoningTokens: Int?
}

struct BridgeReferenceModelConfiguration: Decodable {
    let providerId: String
    let rawModelId: String
    let canonicalModelId: String
    let displayName: String
    let reasoningEffort: String
    let serviceTier: String
    let routeType: String
}

struct RadarLeaderboardItem: Identifiable {
    let id: String
    let displayName: String
    let modelName: String
    let providerId: String?
    let effort: String
    let score: Double?
    let maxScore: Double?
    let elapsedSeconds: Double?
    let referenceCostUsd: Double?
    let costCoverage: String?
    let questionScores: [String: Double]
    let isCurrent: Bool
    let isRecommended: Bool
}

struct BridgeDiagnosticSummary: Decodable {
    let schemaVersion: Int
    let generatedAt: String
    let overallStatus: String
    let appServer: BridgeDiagnosticAppServer
    let capabilities: BridgeDiagnosticCapabilities
    let sessionHistory: BridgeDiagnosticSessionHistory
    let behavior: BridgeDiagnosticBehavior
    let versions: BridgeDiagnosticVersions
    let advisorShortCircuitReason: String?
    let quotaStatus: String
    let quotaRejectedIntervals: [String: Int]
}

struct BridgeDiagnosticAppServer: Decodable {
    let status: String
    let lastReadAt: String?
    let readDurationMs: Int?
}

struct BridgeDiagnosticCapabilities: Decodable {
    let modelCatalog: String
    let account: String
    let rateLimits: String
}

struct BridgeDiagnosticSessionHistory: Decodable {
    let sourceCount: Int
    let discoveredFileCount: Int
    let sampledFileCount: Int
    let parsedFileCount: Int
    let failedFileCount: Int
    let unknownFileCount: Int
    let deduplicatedFileCount: Int
    let budgetLimitedFileCount: Int
    let visibleStartedAt: String?
    let continuousSince: String?
    let coverageComplete: Bool
    let gapDetected: Bool
    let upstreamRetentionRisk: String
}

struct BridgeDiagnosticBehavior: Decodable {
    let completedWorkUnits: Int
    let observedWorkUnits: Int
    let coveragePercent: Double?
    let editWorkUnits: Int
    let retryObservedEditWorkUnits: Int
    let retryIndeterminateEditWorkUnits: Int
}

struct BridgeDiagnosticVersions: Decodable {
    let questionPackId: String?
    let questionPackVersion: String?
    let advisorRulesetVersion: String?
    let pricingSnapshotId: String?
}

struct BridgeAdvisorDecision: Decodable {
    let schemaVersion: Int
    let rulesetVersion: String
    let decision: String
    let shortCircuitReason: String
    let currentModelConfigurationId: String?
    let candidateModelConfigurationId: String?
    let generatedAt: String
    let validUntil: String
    let quality: BridgeAdvisorQuality
    let benefits: BridgeAdvisorBenefits
    let confidence: Double
    let confidenceLevel: String
    let reasons: [String]
    let limitations: [String]
    let nextAction: String

    var presentationTitle: String {
        switch decision {
        case "keep":
            return "保持当前模型"
        case "trial_switch":
            return "可以有限试用候选"
        case "compare_first":
            return "先补齐比较证据"
        case "wait":
            return "继续积累真实使用"
        case "unmapped":
            return "先确认当前模型"
        case "quota_risk":
            return "当前额度存在风险"
        default:
            return "继续观察"
        }
    }

    var primaryReason: String {
        reasons.first(where: { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty })
            ?? nextAction
    }
}

struct BridgeAdvisorQuality: Decodable {
    let currentScore: Double?
    let candidateScore: Double?
    let scoreDelta: Double?
    let guardPassed: Bool
    let criticalRegressions: [BridgeAdvisorCriticalRegression]
    let hardFailures: [String]
}

struct BridgeAdvisorCriticalRegression: Decodable {
    let questionId: String
    let regression: Double
}

struct BridgeAdvisorBenefits: Decodable {
    let quotaReductionPercentRange: [Double]?
    let additionalSimilarTasksRange: [Double]?
    let quotaEvidence: String?
    let activeTimeReductionPercent: Double?
    let activeTimeEvidence: String?
    let standardCostReductionPercent: Double?
    let standardCostEvidence: String?
    let pricingSnapshotId: String
}

struct BridgeCodexInsights: Decodable {
    let schemaVersion: Int
    let account: BridgeCodexAccountSnapshot
    let workload: BridgeCodexWorkloadSnapshot
}

struct BridgeCodexAccountSnapshot: Decodable {
    let schemaVersion: Int
    let capturedAt: String
    let source: String
    let accountType: String
    let loginState: String
    let planType: String?
    let quotaStatus: String
    let quotaWindows: [BridgeCodexQuotaWindow]
    let usageStatus: String
    let usageSummary: BridgeCodexAccountUsageSummary?
    let dailyUsage: [BridgeCodexDailyUsage]
    let unavailableCapabilities: [String]
}

struct BridgeCodexQuotaWindow: Decodable, Identifiable {
    let windowId: String
    let label: String
    let limitId: String?
    let limitName: String?
    let usedPercent: Double?
    let windowSeconds: Int?
    let resetsAt: String?

    var id: String { windowId }
}

struct BridgeCodexAccountUsageSummary: Decodable {
    let lifetimeTokens: Int?
    let peakDailyTokens: Int?
    let longestRunningTurnSeconds: Int?
    let currentStreakDays: Int?
    let longestStreakDays: Int?
}

struct BridgeCodexDailyUsage: Decodable, Identifiable {
    let startDate: String
    let tokens: Int

    var id: String { startDate }
}

struct BridgeCodexWorkloadSnapshot: Decodable {
    let schemaVersion: Int
    let status: String
    let capturedAt: String
    let periodStart: String?
    let periodEnd: String?
    let coverageStartedAt: String?
    let coverageComplete: Bool
    let bootstrapTruncated: Bool?
    let observationCount: Int
    let excludedObservationCount: Int?
    let aggregates: [BridgeCodexUsageAggregate]
}

struct BridgeCodexUsageAggregate: Decodable, Identifiable {
    let modelConfigurationId: String
    let providerId: String
    let rawModelId: String
    let reasoningEffort: String
    let completedWorkUnits: Int
    let subagentCompletedWorkUnits: Int
    let activeDurationMs: Int
    let wallclockUnionMs: Int
    let inputTokens: Int
    let cachedInputTokens: Int
    let cacheWriteInputTokens: Int
    let outputTokens: Int
    let reasoningTokens: Int
    let referenceCostUsd: Double?
    let referenceCostStatus: String?
    let referenceCostPricingSnapshotId: String?
    let responseWaitMs: Int?
    let responseWaitWorkUnitCount: Int?
    let behaviorObservedWorkUnits: Int?
    let behaviorCoveragePercent: Double?
    let editWorkUnits: Int?
    let retryObservedEditWorkUnits: Int?
    let oneShotEditWorkUnits: Int?
    let oneShotRatePercent: Double?
    let retryCount: Int?
    let retriesPerEdit: Double?
    let failureCount: Int
    let sampleDays: Int
    let attributionConfidence: Double

    var id: String { modelConfigurationId }
}

struct BridgeQuestionPack: Decodable {
    let id: String
    let version: String
    let questionCount: Int
    let questions: [BridgeQuestionDefinition]
    let defaultEvaluationProfileId: String
    let evaluationProfiles: [BridgeEvaluationProfile]
}

struct BridgeSettingsProjection: Decodable {
    let schemaVersion: Int
    let connections: [BridgeSettingsConnectionProjection]
    let scanScope: BridgeSettingsScanScopeProjection
    let candidates: [BridgeSettingsCandidateProjection]

    private enum CodingKeys: String, CodingKey {
        case schemaVersion
        case connections
        case scanScope
        case candidates
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decode(Int.self, forKey: .schemaVersion)
        guard schemaVersion == 1 else {
            throw DecodingError.dataCorruptedError(
                forKey: .schemaVersion,
                in: container,
                debugDescription: "Unsupported settings projection schema version: \(schemaVersion)"
            )
        }
        connections = try container.decode(
            [BridgeSettingsConnectionProjection].self,
            forKey: .connections
        )
        scanScope = try container.decode(
            BridgeSettingsScanScopeProjection.self,
            forKey: .scanScope
        )
        candidates = try container.decode(
            [BridgeSettingsCandidateProjection].self,
            forKey: .candidates
        )
    }
}

struct BridgeSettingsConnectionProjection: Decodable {
    let connectionId: String
    let sourceId: String
    let operationalStatus: String
    let reason: String
    let action: String
    let enabledCandidateIds: [String]
    let availableCandidateIds: [String]
    let recommendationStatus: String
    let recommendationReason: String
    let recommendationAction: String
    let completedStepCount: Int
    let baselineCandidateIds: [String]
}

struct BridgeSettingsScanScopeProjection: Decodable {
    let regularCandidateIds: [String]
    let customCandidateIds: [String]
    let sourceCount: Int
    let modelCount: Int
    let candidateCount: Int
    let blockedReasons: [BridgeSettingsBlockedReason]
}

struct BridgeSettingsBlockedReason: Decodable {
    let connectionId: String
    let sourceId: String
    let reason: String
    let action: String
    let candidateIds: [String]
}

struct BridgeSettingsCandidateProjection: Decodable {
    let candidateId: String
    let sourceId: String
    let connectionId: String
    let providerId: String?
    let familyId: String?
    let variantId: String?
    let modelId: String
    let displayModel: String
    let scanProfile: String
    let displayScanProfile: String
    let enabled: Bool
    let available: Bool
}

struct BridgeQuestionDefinition: Decodable, Identifiable {
    let id: String
    let questionNumber: Int
    let title: String
    let capabilityId: String
    let capabilityLabel: String
    let detailLabel: String
    let scoreMax: Int
}

struct BridgeEvaluationProfile: Decodable, Identifiable {
    let id: String
    let label: String
    let summary: String
    let questionIds: [String]
    let questionCount: Int
    let resultLevel: String
    let scorePresentation: String
    let scoreMax: Int
    let upgradeTo: String?
}

struct BridgeConfig: Decodable {
    let modelIngress: BridgeModelIngress
    let providerCatalog: [BridgeProviderCatalogProvider]
    let detectedLocalProviders: [BridgeDetectedLocalProvider]
    let recommendation: BridgeRecommendationConfig
    let scheduler: BridgeSchedulerConfig
    let scanBudget: BridgeScanBudgetConfig
    let system: BridgeSystemConfig
    let rules: [String: BridgeRuleConfig]

    var targets: [BridgeTarget] {
        modelIngress.targets
    }

    private enum CodingKeys: String, CodingKey {
        case modelIngress
        case providerCatalog
        case detectedLocalProviders
        case recommendation
        case scheduler
        case scanBudget
        case system
        case rules
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        modelIngress = try container.decode(
            BridgeModelIngress.self,
            forKey: .modelIngress
        )
        providerCatalog = try container.decodeIfPresent(
            [BridgeProviderCatalogProvider].self,
            forKey: .providerCatalog
        ) ?? []
        detectedLocalProviders = try container.decodeIfPresent(
            [BridgeDetectedLocalProvider].self,
            forKey: .detectedLocalProviders
        ) ?? []
        recommendation =
            try container.decodeIfPresent(BridgeRecommendationConfig.self, forKey: .recommendation)
            ?? BridgeRecommendationConfig(
                currentDefaultCandidateId: nil,
                projectProfile: .default
            )
        scheduler = try container.decode(BridgeSchedulerConfig.self, forKey: .scheduler)
        scanBudget = try container.decodeIfPresent(
            BridgeScanBudgetConfig.self,
            forKey: .scanBudget
        ) ?? .default
        system = try container.decode(BridgeSystemConfig.self, forKey: .system)
        rules = try container.decode([String: BridgeRuleConfig].self, forKey: .rules)
    }
}

struct BridgeProjectProfile: Decodable {
    let projectName: String
    let taskMode: String

    static let `default` = BridgeProjectProfile(
        projectName: "当前项目",
        taskMode: "综合推荐"
    )
}

struct BridgeProviderCatalogProvider: Decodable, Identifiable {
    let providerId: String
    let displayName: String
    let providerPreset: String
    let defaultBaseUrl: String?
    let baseUrlHosts: [String]
    let featured: Bool
    let defaultApiFormat: String?
    let defaultModelIds: [String]
    let websiteUrl: String?
    let apiKeyUrl: String?
    let connectionSupported: Bool?
    let availabilityNote: String?
    let families: [BridgeProviderCatalogFamily]

    var id: String { providerId }
}

struct BridgeDetectedLocalProvider: Decodable, Identifiable {
    let providerId: String
    let displayName: String
    let sourceId: String
    let connectionId: String
    let detected: Bool
    let importable: Bool
    let status: String
    let statusMessage: String

    var id: String { providerId }
}

struct BridgeProviderCatalogFamily: Decodable, Identifiable {
    let familyId: String
    let displayName: String
    let variants: [BridgeProviderCatalogVariant]

    var id: String { familyId }
}

struct BridgeProviderCatalogVariant: Decodable, Identifiable {
    let variantId: String?
    let displayName: String
    let modelIds: [String]
    let reasoningEfforts: [String]?
    let defaultReasoningEffort: String?

    var id: String { variantId ?? modelIds.first ?? displayName }
}

struct BridgeRecommendationConfig: Decodable {
    let currentDefaultCandidateId: String?
    let currentModelMode: String
    let preference: String
    let sourceModeByConfigurationId: [String: String]
    let projectProfile: BridgeProjectProfile
    let effectiveCurrentCandidateId: String?
    let currentModelSource: String
    let currentModelDetectionStatus: String
    let currentModelDetectedAt: String?
    let detectedCurrentModel: String?
    let detectedCurrentEffort: String?
    let detectedActiveSessionCount: Int
    let detectedActiveModels: [BridgeDetectedCodexModel]
    let detectedActiveSessions: [BridgeDetectedCodexSession]
    let activeModelSessions: [BridgeDetectedModelSession]

    private enum CodingKeys: String, CodingKey {
        case currentDefaultCandidateId
        case currentModelMode
        case preference
        case sourceModeByConfigurationId
        case projectProfile
        case effectiveCurrentCandidateId
        case currentModelSource
        case currentModelDetectionStatus
        case currentModelDetectedAt
        case detectedCurrentModel
        case detectedCurrentEffort
        case detectedActiveSessionCount
        case detectedActiveModels
        case detectedActiveSessions
        case activeModelSessions
    }

    init(
        currentDefaultCandidateId: String?,
        projectProfile: BridgeProjectProfile,
        currentModelMode: String = "auto",
        preference: String = "smart",
        sourceModeByConfigurationId: [String: String] = [:],
        effectiveCurrentCandidateId: String? = nil,
        currentModelSource: String = "unavailable",
        currentModelDetectionStatus: String = "unavailable",
        currentModelDetectedAt: String? = nil,
        detectedCurrentModel: String? = nil,
        detectedCurrentEffort: String? = nil,
        detectedActiveSessionCount: Int = 0,
        detectedActiveModels: [BridgeDetectedCodexModel] = [],
        detectedActiveSessions: [BridgeDetectedCodexSession] = [],
        activeModelSessions: [BridgeDetectedModelSession] = []
    ) {
        self.currentDefaultCandidateId = currentDefaultCandidateId
        self.currentModelMode = currentModelMode
        self.preference = preference
        self.sourceModeByConfigurationId = sourceModeByConfigurationId
        self.projectProfile = projectProfile
        self.effectiveCurrentCandidateId = effectiveCurrentCandidateId
        self.currentModelSource = currentModelSource
        self.currentModelDetectionStatus = currentModelDetectionStatus
        self.currentModelDetectedAt = currentModelDetectedAt
        self.detectedCurrentModel = detectedCurrentModel
        self.detectedCurrentEffort = detectedCurrentEffort
        self.detectedActiveSessionCount = detectedActiveSessionCount
        self.detectedActiveModels = detectedActiveModels
        self.detectedActiveSessions = detectedActiveSessions
        self.activeModelSessions = activeModelSessions
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        currentDefaultCandidateId = try container.decodeIfPresent(
            String.self,
            forKey: .currentDefaultCandidateId
        )
        currentModelMode = try container.decodeIfPresent(
            String.self,
            forKey: .currentModelMode
        ) ?? "auto"
        preference = try container.decodeIfPresent(
            String.self,
            forKey: .preference
        ) ?? "smart"
        sourceModeByConfigurationId = try container.decodeIfPresent(
            [String: String].self,
            forKey: .sourceModeByConfigurationId
        ) ?? [:]
        effectiveCurrentCandidateId = try container.decodeIfPresent(
            String.self,
            forKey: .effectiveCurrentCandidateId
        )
        currentModelSource = try container.decodeIfPresent(
            String.self,
            forKey: .currentModelSource
        ) ?? "unavailable"
        currentModelDetectionStatus = try container.decodeIfPresent(
            String.self,
            forKey: .currentModelDetectionStatus
        ) ?? "unavailable"
        currentModelDetectedAt = try container.decodeIfPresent(
            String.self,
            forKey: .currentModelDetectedAt
        )
        detectedCurrentModel = try container.decodeIfPresent(
            String.self,
            forKey: .detectedCurrentModel
        )
        detectedCurrentEffort = try container.decodeIfPresent(
            String.self,
            forKey: .detectedCurrentEffort
        )
        detectedActiveSessionCount = try container.decodeIfPresent(
            Int.self,
            forKey: .detectedActiveSessionCount
        ) ?? 0
        detectedActiveModels = try container.decodeIfPresent(
            [BridgeDetectedCodexModel].self,
            forKey: .detectedActiveModels
        ) ?? []
        detectedActiveSessions = try container.decodeIfPresent(
            [BridgeDetectedCodexSession].self,
            forKey: .detectedActiveSessions
        ) ?? []
        activeModelSessions = try container.decodeIfPresent(
            [BridgeDetectedModelSession].self,
            forKey: .activeModelSessions
        ) ?? []
        projectProfile = try container.decodeIfPresent(
            BridgeProjectProfile.self,
            forKey: .projectProfile
        ) ?? .default
    }
}

struct BridgeDetectedCodexModel: Decodable, Hashable {
    let model: String
    let effort: String
}

struct BridgeDetectedCodexSession: Decodable, Hashable, Identifiable {
    let id: String
    let workspaceName: String
    let model: String
    let effort: String
    let threadName: String?
}

struct BridgeDetectedModelSession: Decodable, Hashable, Identifiable {
    let id: String
    let source: String
    let workspaceName: String
    let model: String?
    let effort: String?
    let threadName: String?
    let isEvaluationSession: Bool?

    var sourceDisplayName: String {
        switch source {
        case "codex":
            return "Codex"
        case "claude":
            return "Claude Code"
        case "grok":
            return "Grok Build"
        default:
            return source
        }
    }
}

struct BridgeModelIngress: Decodable {
    let sources: [BridgeIngressSource]
    let connections: [BridgeIngressConnection]

    var targets: [BridgeTarget] {
        let sourcesByID = Dictionary(uniqueKeysWithValues: sources.map { ($0.id, $0) })
        return connections.flatMap { connection -> [BridgeTarget] in
            let source = sourcesByID[connection.sourceId]
            let connectionAvailable = source?.enabled == true
                && connection.enabled
                && (source?.mode != "api" || connection.lastTestStatus == "ok")
                && (connection.sourceId != "claude_local" || connection.localLoginVerified == true)
            return connection.modelCandidates.map { candidate in
                BridgeTarget(
                    id: candidate.id,
                    candidateID: candidate.id,
                    connectionID: connection.id,
                    model: candidate.modelId,
                    effort: candidate.scanProfile,
                    enabled: connectionAvailable && candidate.enabled
                )
            }
        }
    }

    func secretBackedConnectionIDs(for candidateIDs: [String]?) -> [String] {
        let sourcesByID = Dictionary(uniqueKeysWithValues: sources.map { ($0.id, $0) })
        let requestedCandidateIDs = candidateIDs.map(Set.init)

        return connections.compactMap { connection in
            guard let source = sourcesByID[connection.sourceId],
                  source.mode != "api" || connection.lastTestStatus == "ok",
                  connection.sourceId != "claude_local" || connection.localLoginVerified == true else {
                return nil
            }
            guard let apiKeyRef = connection.apiKeyRef,
                  apiKeyRef.hasPrefix("keychain:")
                    || apiKeyRef.hasPrefix(LocalEncryptedSecretStore.referencePrefix) else {
                return nil
            }

            let hasSelectedCandidate = connection.modelCandidates.contains { candidate in
                if let requestedCandidateIDs {
                    return requestedCandidateIDs.contains(candidate.id)
                }
                return candidate.enabled
            }
            guard hasSelectedCandidate else { return nil }
            if requestedCandidateIDs != nil {
                return connection.id
            }
            guard source.enabled && connection.enabled else { return nil }
            return connection.id
        }
    }

    init(sources: [BridgeIngressSource], connections: [BridgeIngressConnection]) {
        self.sources = sources
        self.connections = connections
    }

}

struct BridgeIngressSource: Decodable, Identifiable {
    let id: String
    let kind: String
    let title: String
    let description: String
    let mode: String
    let enabled: Bool
}

struct BridgeIngressConnection: Decodable, Identifiable {
    let id: String
    let sourceId: String
    let name: String
    let enabled: Bool
    let apiFormat: String?
    let providerPreset: String
    let providerId: String?
    let providerDisplayName: String?
    let authMode: String?
    let catalogSource: String?
    let baseUrl: String?
    let apiKeyRef: String?
    let notes: String?
    let lastTestStatus: String?
    let lastTestAt: String?
    let lastTestMessage: String?
    let localLoginVerified: Bool?
    let modelCandidates: [BridgeIngressModelCandidate]

    var usesLocalEncryptedSecret: Bool {
        apiKeyRef?.hasPrefix(LocalEncryptedSecretStore.referencePrefix) == true
    }

    var usesKeychainSecret: Bool {
        apiKeyRef?.hasPrefix("keychain:") == true
    }

    var secretStorageSummaryText: String {
        guard apiKeyRef != nil else { return "未配置 Key" }
        if usesLocalEncryptedSecret {
            return "待迁移到 macOS 钥匙串"
        }
        if usesKeychainSecret {
            return "已绑定到 macOS 钥匙串"
        }
        return "已安全保存"
    }
}

struct BridgeModelDiscoveryResponse: Decodable {
    let ok: Bool
    let models: [String]
    let newModels: [String]
    let configuredModels: [String]
    let reasoningProfilesByModel: [String: [String]]
    let defaultReasoningProfileByModel: [String: String]
    let errorCategory: String?
    let message: String
    let manualEntryAllowed: Bool

    private enum CodingKeys: String, CodingKey {
        case ok
        case models
        case newModels
        case configuredModels
        case reasoningProfilesByModel
        case defaultReasoningProfileByModel
        case errorCategory
        case message
        case manualEntryAllowed
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        ok = try container.decode(Bool.self, forKey: .ok)
        models = try container.decodeIfPresent([String].self, forKey: .models) ?? []
        newModels = try container.decodeIfPresent([String].self, forKey: .newModels) ?? models
        configuredModels = try container.decodeIfPresent([String].self, forKey: .configuredModels) ?? []
        reasoningProfilesByModel = try container.decodeIfPresent(
            [String: [String]].self,
            forKey: .reasoningProfilesByModel
        ) ?? [:]
        defaultReasoningProfileByModel = try container.decodeIfPresent(
            [String: String].self,
            forKey: .defaultReasoningProfileByModel
        ) ?? [:]
        errorCategory = try container.decodeIfPresent(String.self, forKey: .errorCategory)
        message = try container.decode(String.self, forKey: .message)
        manualEntryAllowed = try container.decodeIfPresent(Bool.self, forKey: .manualEntryAllowed) ?? true
    }
}

struct BridgeConnectionTestResponse: Decodable {
    let ok: Bool
    let status: String
    let errorCategory: String?
    let message: String
    let testedAt: String
}

struct BridgeScanControlResponse: Decodable {
    let ok: Bool
    let action: String
    let message: String
}

struct BridgeRunRecoveryResponse: Decodable {
    let ok: Bool
    let action: String
    let recovered: Bool
    let status: String
    let runId: String?
    let message: String

    var requiresAttention: Bool {
        status == "incomplete"
    }
}

struct BridgeStateObservationResponse: Decodable {
    let schemaVersion: Int
    let ok: Bool
    let action: String
    let status: String
    let message: String
    let state: BridgeRefreshSnapshot
}

struct BridgeReferenceRefreshResponse: Decodable {
    let schemaVersion: Int
    let ok: Bool
    let action: String
    let status: String
    let message: String
    let state: BridgeSnapshot

    var requiresAttention: Bool {
        status == "failed"
    }
}

struct BridgeDataOperationResponse: Decodable {
    let ok: Bool
    let action: String
    let message: String
    let removedFileCount: Int?
}

struct BridgeLocalImportResponse: Decodable {
    let ok: Bool
    let providerId: String
    let sourceId: String?
    let connectionId: String?
    let message: String
}

struct BridgeLocalModelDiscoveryResponse: Decodable {
    let ok: Bool
    let providerId: String
    let connectionId: String?
    let candidates: [BridgeLocalModelDiscoveryCandidate]
    let message: String
}

struct BridgeLocalModelDiscoveryCandidate: Decodable, Identifiable {
    let id: String
    let modelId: String
    let modelDisplayName: String
    let displayName: String
    let scanProfile: String
    let isDefault: Bool
    let configured: Bool

    init(
        id: String,
        modelId: String,
        modelDisplayName: String,
        displayName: String,
        scanProfile: String,
        isDefault: Bool,
        configured: Bool
    ) {
        self.id = id
        self.modelId = modelId
        self.modelDisplayName = modelDisplayName
        self.displayName = displayName
        self.scanProfile = scanProfile
        self.isDefault = isDefault
        self.configured = configured
    }
}

struct BridgeIngressModelCandidate: Decodable, Identifiable {
    let id: String
    let connectionId: String
    let modelId: String
    let displayName: String
    let familyId: String?
    let variantId: String?
    let enabled: Bool
    let scanProfile: String
    let capabilities: [String]

    var model: String { modelId }
    var effort: String { scanProfile }

    private enum CodingKeys: String, CodingKey {
        case id
        case connectionId
        case modelId
        case displayName
        case familyId
        case variantId
        case enabled
        case scanProfile
        case capabilities
    }

    init(
        id: String,
        connectionId: String,
        modelId: String,
        displayName: String,
        familyId: String? = nil,
        variantId: String? = nil,
        enabled: Bool,
        scanProfile: String,
        capabilities: [String]
    ) {
        self.id = id
        self.connectionId = connectionId
        self.modelId = modelId
        self.displayName = displayName
        self.familyId = familyId
        self.variantId = variantId
        self.enabled = enabled
        self.scanProfile = scanProfile
        self.capabilities = capabilities
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        connectionId = try container.decode(String.self, forKey: .connectionId)
        modelId = try container.decode(String.self, forKey: .modelId)
        displayName = try container.decode(String.self, forKey: .displayName)
        familyId = try container.decodeIfPresent(String.self, forKey: .familyId)
        variantId = try container.decodeIfPresent(String.self, forKey: .variantId)
        enabled = try container.decodeIfPresent(Bool.self, forKey: .enabled) ?? true
        scanProfile = try container.decodeIfPresent(String.self, forKey: .scanProfile) ?? "codex_default"
        capabilities = try container.decodeIfPresent([String].self, forKey: .capabilities) ?? []
    }
}

struct BridgeTarget: Identifiable {
    let id: String
    let candidateID: String
    let connectionID: String
    let model: String
    let effort: String
    let enabled: Bool
}

struct BridgeSchedulerConfig: Decodable {
    let enabled: Bool
    let mode: String
    let intervalSeconds: Int
    let dailyHour: Int
    let dailyMinute: Int
    let weeklyWeekday: Int
    let weeklyHour: Int
    let weeklyMinute: Int
    let scheduledEvaluationProfileId: String?

    private enum CodingKeys: String, CodingKey {
        case enabled
        case mode
        case intervalSeconds
        case dailyHour
        case dailyMinute
        case weeklyWeekday
        case weeklyHour
        case weeklyMinute
        case scheduledEvaluationProfileId
    }

    init(
        enabled: Bool,
        mode: String,
        intervalSeconds: Int,
        dailyHour: Int,
        dailyMinute: Int,
        weeklyWeekday: Int,
        weeklyHour: Int,
        weeklyMinute: Int,
        scheduledEvaluationProfileId: String? = nil
    ) {
        self.enabled = enabled
        self.mode = mode
        self.intervalSeconds = max(1800, intervalSeconds)
        self.dailyHour = dailyHour
        self.dailyMinute = dailyMinute
        self.weeklyWeekday = weeklyWeekday
        self.weeklyHour = weeklyHour
        self.weeklyMinute = weeklyMinute
        self.scheduledEvaluationProfileId = scheduledEvaluationProfileId
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let rawMode = try container.decodeIfPresent(String.self, forKey: .mode) ?? "manual"
        enabled = try container.decodeIfPresent(Bool.self, forKey: .enabled) ?? (rawMode != "manual")
        mode = ["interval", "daily", "weekly"].contains(rawMode) ? rawMode : "daily"
        intervalSeconds = max(1800, try container.decodeIfPresent(Int.self, forKey: .intervalSeconds) ?? 1800)
        dailyHour = try container.decodeIfPresent(Int.self, forKey: .dailyHour) ?? 9
        dailyMinute = try container.decodeIfPresent(Int.self, forKey: .dailyMinute) ?? 0
        weeklyWeekday = try container.decodeIfPresent(Int.self, forKey: .weeklyWeekday) ?? 1
        weeklyHour = try container.decodeIfPresent(Int.self, forKey: .weeklyHour) ?? 9
        weeklyMinute = try container.decodeIfPresent(Int.self, forKey: .weeklyMinute) ?? 0
        scheduledEvaluationProfileId = try container.decodeIfPresent(
            String.self,
            forKey: .scheduledEvaluationProfileId
        )
    }
}

struct BridgeScanBudgetConfig: Decodable {
    let enabled: Bool
    let maxDurationSeconds: Int
    let maxReferenceCostUsd: Double

    static let `default` = BridgeScanBudgetConfig(
        enabled: false,
        maxDurationSeconds: 900,
        maxReferenceCostUsd: 1.0
    )
}

struct BridgeSystemConfig: Decodable {
    let useMockResults: Bool
    let autoOpenBrowser: Bool
    let historyLimit: Int
    let language: String
    let attemptsPerTarget: Int
    let maxConcurrentTargets: Int
    let executionTimeoutSeconds: Int
    let timeoutRetryCount: Int

    private enum CodingKeys: String, CodingKey {
        case useMockResults
        case autoOpenBrowser
        case historyLimit
        case language
        case attemptsPerTarget
        case maxConcurrentTargets
        case executionTimeoutSeconds
        case timeoutRetryCount
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        useMockResults = try container.decodeIfPresent(Bool.self, forKey: .useMockResults) ?? false
        autoOpenBrowser = try container.decodeIfPresent(Bool.self, forKey: .autoOpenBrowser) ?? true
        historyLimit = try container.decodeIfPresent(Int.self, forKey: .historyLimit) ?? 50
        language = try container.decodeIfPresent(String.self, forKey: .language) ?? "zh-CN"
        attemptsPerTarget = max(1, try container.decodeIfPresent(Int.self, forKey: .attemptsPerTarget) ?? 3)
        maxConcurrentTargets = max(1, try container.decodeIfPresent(Int.self, forKey: .maxConcurrentTargets) ?? 1)
        executionTimeoutSeconds = max(60, try container.decodeIfPresent(Int.self, forKey: .executionTimeoutSeconds) ?? 1200)
        timeoutRetryCount = max(0, try container.decodeIfPresent(Int.self, forKey: .timeoutRetryCount) ?? 0)
    }
}

struct BridgeRuleConfig: Decodable {
    let enabled: Bool
    let action: String
    let maxRetries: Int
    let cooldownSeconds: Int
}

enum BridgeRuntimeLifecycle: String, Decodable {
    case idle
    case preparing
    case activeScan = "active_scan"
    case pausedRecoverable = "paused_recoverable"
    case finalizing
    case failed
    case recommendationUnavailable = "recommendation_unavailable"
}

enum BridgeRuntimePhase: String, Decodable {
    case scan
    case repair
}

struct BridgeRuntime: Decodable {
    let enabledTargetCount: Int
    let historyCount: Int
    let isRunning: Bool
    let lastRunCount: Int
    let lastError: String?
    let lastRunMode: String
    let completedTargets: Int
    let totalTargets: Int
    let progressPercent: Int
    let currentTarget: String?
    let runEntries: [BridgeRunEntry]
    let currentRunId: String?
    let hasResumableRun: Bool
    let resumableRunId: String?
    let resumableOperationKind: String?
    let resumableOperationRunId: String?
    let resumableCandidateIds: [String]
    let resumableQuestionId: String?
    let currentPhase: BridgeRuntimePhase?
    let currentPhaseCompletedTargets: Int
    let currentPhaseTotalTargets: Int
    let progressCompleted: Int
    let progressTotal: Int?
    let progressUnit: String
    let activeEvaluationCount: Int
    let queuedEvaluationCount: Int
    let oldestActiveEvaluationStartedAt: String?
    let executionTimeoutSeconds: Int?
    let lifecycleState: BridgeRuntimeLifecycle
    let stateChangedAt: String?
    let finalizingStartedAt: String?
    let lastPhase: BridgeRuntimePhase?
    let lastPhaseCompleted: Int
    let lastPhaseTotal: Int?
    let updatedAt: String?
    let leaseExpiresAt: String?

    private enum CodingKeys: String, CodingKey {
        case enabledTargetCount, historyCount, isRunning, lastRunCount, lastError
        case lastRunMode, completedTargets, totalTargets, progressPercent
        case currentTarget, runEntries, currentRunId, hasResumableRun, resumableRunId
        case resumableOperationKind, resumableOperationRunId, resumableCandidateIds
        case resumableQuestionId
        case currentPhase, currentPhaseCompletedTargets, currentPhaseTotalTargets
        case progressCompleted, progressTotal, progressUnit, lifecycleState
        case activeEvaluationCount, queuedEvaluationCount
        case oldestActiveEvaluationStartedAt, executionTimeoutSeconds
        case stateChangedAt, finalizingStartedAt, lastPhase, lastPhaseCompleted
        case lastPhaseTotal, updatedAt, leaseExpiresAt
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        enabledTargetCount = try container.decode(Int.self, forKey: .enabledTargetCount)
        historyCount = try container.decode(Int.self, forKey: .historyCount)
        isRunning = try container.decodeIfPresent(Bool.self, forKey: .isRunning) ?? false
        lastRunCount = try container.decodeIfPresent(Int.self, forKey: .lastRunCount) ?? 0
        lastError = try container.decodeIfPresent(String.self, forKey: .lastError)
        lastRunMode = try container.decodeIfPresent(String.self, forKey: .lastRunMode) ?? "live"
        completedTargets = try container.decodeIfPresent(Int.self, forKey: .completedTargets) ?? 0
        totalTargets = try container.decodeIfPresent(Int.self, forKey: .totalTargets) ?? 0
        progressPercent = try container.decodeIfPresent(Int.self, forKey: .progressPercent) ?? 0
        currentTarget = try container.decodeIfPresent(String.self, forKey: .currentTarget)
        runEntries = try container.decodeIfPresent([BridgeRunEntry].self, forKey: .runEntries) ?? []
        currentRunId = try container.decodeIfPresent(String.self, forKey: .currentRunId)
        hasResumableRun = try container.decodeIfPresent(Bool.self, forKey: .hasResumableRun) ?? false
        resumableRunId = try container.decodeIfPresent(String.self, forKey: .resumableRunId)
        resumableOperationKind = try container.decodeIfPresent(String.self, forKey: .resumableOperationKind)
        resumableOperationRunId = try container.decodeIfPresent(String.self, forKey: .resumableOperationRunId)
        resumableCandidateIds = try container.decodeIfPresent([String].self, forKey: .resumableCandidateIds) ?? []
        resumableQuestionId = try container.decodeIfPresent(String.self, forKey: .resumableQuestionId)
        currentPhase = try container.decodeIfPresent(
            BridgeRuntimePhase.self,
            forKey: .currentPhase
        )
        currentPhaseCompletedTargets = try container.decodeIfPresent(Int.self, forKey: .currentPhaseCompletedTargets) ?? 0
        currentPhaseTotalTargets = try container.decodeIfPresent(Int.self, forKey: .currentPhaseTotalTargets) ?? 0
        progressCompleted = try container.decodeIfPresent(Int.self, forKey: .progressCompleted) ?? currentPhaseCompletedTargets
        progressTotal = try container.decodeIfPresent(Int.self, forKey: .progressTotal) ?? currentPhaseTotalTargets
        progressUnit = try container.decodeIfPresent(String.self, forKey: .progressUnit) ?? "evaluationUnit"
        activeEvaluationCount = try container.decodeIfPresent(Int.self, forKey: .activeEvaluationCount) ?? 0
        queuedEvaluationCount = try container.decodeIfPresent(Int.self, forKey: .queuedEvaluationCount) ?? 0
        oldestActiveEvaluationStartedAt = try container.decodeIfPresent(
            String.self,
            forKey: .oldestActiveEvaluationStartedAt
        )
        executionTimeoutSeconds = try container.decodeIfPresent(
            Int.self,
            forKey: .executionTimeoutSeconds
        )
        lifecycleState = try container.decode(
            BridgeRuntimeLifecycle.self,
            forKey: .lifecycleState
        )
        stateChangedAt = try container.decodeIfPresent(String.self, forKey: .stateChangedAt)
        finalizingStartedAt = try container.decodeIfPresent(String.self, forKey: .finalizingStartedAt)
        lastPhase = try container.decodeIfPresent(
            BridgeRuntimePhase.self,
            forKey: .lastPhase
        )
        lastPhaseCompleted = try container.decodeIfPresent(Int.self, forKey: .lastPhaseCompleted) ?? 0
        lastPhaseTotal = try container.decodeIfPresent(Int.self, forKey: .lastPhaseTotal)
        updatedAt = try container.decodeIfPresent(String.self, forKey: .updatedAt)
        leaseExpiresAt = try container.decodeIfPresent(String.self, forKey: .leaseExpiresAt)
    }
}

struct BridgeComparisonContract: Decodable {
    let schemaVersion: Int
    let questionPackVersion: String
    let graderVersion: String
    let evaluationSnapshotId: String
    let pricingSnapshotId: String
    let trendComparabilityKey: String
}

struct BridgeTokenTotals: Decodable {
    let inputTokens: Int?
    let cachedInputTokens: Int?
    let cacheWriteInputTokens: Int?
    let outputTokens: Int?
    let reasoningTokens: Int?
}

struct BridgePairwiseComparison: Decodable, Identifiable {
    var id: String { pairKey }
    let schemaVersion: Int
    let pairKey: String
    let baselineCandidateId: String
    let baselineLabel: String
    let candidateId: String
    let candidateLabel: String
    let comparisonStatus: String
    let isComparable: Bool
    let baselineQualityScore: Double?
    let candidateQualityScore: Double?
    let qualityDeltaPoints: Double?
    let baselineElapsedSeconds: Double?
    let candidateElapsedSeconds: Double?
    let timeDeltaPercent: Double?
    let baselineCostUsd: Double?
    let candidateCostUsd: Double?
    let costDeltaPercent: Double?
    let baselineCostCoverage: String?
    let candidateCostCoverage: String?
    let baselineTokenTotals: BridgeTokenTotals
    let candidateTokenTotals: BridgeTokenTotals
    let warningQuestionIds: [String]
}

struct BridgeDashboard: Decodable {
    let cards: [BridgeEvidenceCard]
    let leaderboard: [BridgeLeaderboardEntry]
    let comparisonContract: BridgeComparisonContract?
    let pairwiseComparisons: [BridgePairwiseComparison]
    let bestCombination: BridgeBestCombination?
    let provisionalLeader: BridgeProvisionalLeader?
    let statistics: BridgeStatistics?
    let runMetadata: BridgeRunMetadata

    private enum CodingKeys: String, CodingKey {
        case cards
        case leaderboard
        case comparisonContract
        case pairwiseComparisons
        case bestCombination
        case provisionalLeader
        case statistics
        case runMetadata
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        cards = try container.decode([BridgeEvidenceCard].self, forKey: .cards)
        leaderboard = try container.decode([BridgeLeaderboardEntry].self, forKey: .leaderboard)
        comparisonContract = try container.decodeIfPresent(
            BridgeComparisonContract.self,
            forKey: .comparisonContract
        )
        pairwiseComparisons = try container.decodeIfPresent(
            [BridgePairwiseComparison].self,
            forKey: .pairwiseComparisons
        ) ?? []
        bestCombination = try container.decodeIfPresent(BridgeBestCombination.self, forKey: .bestCombination)
        provisionalLeader = try container.decodeIfPresent(
            BridgeProvisionalLeader.self,
            forKey: .provisionalLeader
        )
        statistics = try container.decodeIfPresent(BridgeStatistics.self, forKey: .statistics)
        runMetadata = try container.decode(
            BridgeRunMetadata.self,
            forKey: .runMetadata
        )
    }
}

struct BridgeProvisionalLeader: Decodable {
    let candidateId: String?
    let label: String?
    let status: String
    let statusLabel: String
    let confidenceLabel: String
    let confidenceReason: String
    let modeScore: Int?
    let modeScoreMax: Int?
    let modeScoreText: String?
    let runnerUpGap: Int?

    private enum CodingKeys: String, CodingKey {
        case candidateId
        case label
        case status
        case statusLabel
        case confidenceLabel
        case confidenceReason
        case reason
        case modeScore
        case modeScoreMax
        case modeScoreText
        case runnerUpGap
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        candidateId = try container.decodeIfPresent(String.self, forKey: .candidateId)
        label = try container.decodeIfPresent(String.self, forKey: .label)
        status = try container.decode(String.self, forKey: .status)
        statusLabel = try container.decodeIfPresent(String.self, forKey: .statusLabel)
            ?? container.decodeIfPresent(String.self, forKey: .label)
            ?? "初步排序"
        confidenceLabel = try container.decodeIfPresent(String.self, forKey: .confidenceLabel)
            ?? "低"
        confidenceReason = try container.decodeIfPresent(String.self, forKey: .confidenceReason)
            ?? container.decodeIfPresent(String.self, forKey: .reason)
            ?? "当前仅完成部分题目，补全评测后再做正式切换决策。"
        modeScore = try container.decodeIfPresent(Int.self, forKey: .modeScore)
        modeScoreMax = try container.decodeIfPresent(Int.self, forKey: .modeScoreMax)
        modeScoreText = try container.decodeIfPresent(String.self, forKey: .modeScoreText)
        runnerUpGap = try container.decodeIfPresent(Int.self, forKey: .runnerUpGap)
    }
}

struct BridgeStatistics: Decodable {
    let trendSeries: [BridgeTrendSeries]
}

struct BridgeTrendSeries: Decodable, Identifiable {
    var id: String { candidateId }
    let candidateId: String
    let overallScoreRunIndices: [Int]
    let overallScoreValues: [Int]
}

struct BridgeRunMetadata: Decodable {
    let runId: String
    let questionPackId: String
    let questionPackVersion: String
    let startedAt: String?
    let completedAt: String?
    let candidateCount: Int
    let questionCount: Int
    let status: String
    let selectionMode: String
    let requestedCandidateIds: [String]
    let regularCandidateIds: [String]
    let comparisonGroupId: String?
    let comparisonGroupMode: String
    let appendedCandidateIds: [String]
    let skippedCandidateIds: [String]
    let aggregateWallClockSeconds: Int?
    let evaluationProfileId: String
    let evaluationProfileLabel: String
    let evaluationResultLevel: String
    let evaluationScoreMax: Int
    let questionIds: [String]
    let upgradeFromRunId: String?
    let upgradeTargetProfileId: String?

    private enum CodingKeys: String, CodingKey {
        case runId
        case questionPackId
        case questionPackVersion
        case startedAt
        case completedAt
        case candidateCount
        case questionCount
        case status
        case selectionMode
        case requestedCandidateIds
        case regularCandidateIds
        case comparisonGroupId
        case comparisonGroupMode
        case appendedCandidateIds
        case skippedCandidateIds
        case aggregateWallClockSeconds
        case evaluationProfileId
        case evaluationProfileLabel
        case evaluationResultLevel
        case evaluationScoreMax
        case questionIds
        case upgradeFromRunId
        case upgradeTargetProfileId
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        runId = try container.decode(String.self, forKey: .runId)
        questionPackId = try container.decode(String.self, forKey: .questionPackId)
        questionPackVersion = try container.decode(
            String.self,
            forKey: .questionPackVersion
        )
        startedAt = try container.decodeIfPresent(String.self, forKey: .startedAt)
        completedAt = try container.decodeIfPresent(String.self, forKey: .completedAt)
        candidateCount = try container.decode(Int.self, forKey: .candidateCount)
        questionCount = try container.decode(Int.self, forKey: .questionCount)
        status = try container.decode(String.self, forKey: .status)
        selectionMode = try container.decode(String.self, forKey: .selectionMode)
        requestedCandidateIds = try container.decode(
            [String].self,
            forKey: .requestedCandidateIds
        )
        regularCandidateIds = try container.decode(
            [String].self,
            forKey: .regularCandidateIds
        )
        comparisonGroupId = try container.decodeIfPresent(String.self, forKey: .comparisonGroupId)
        comparisonGroupMode = try container.decode(
            String.self,
            forKey: .comparisonGroupMode
        )
        appendedCandidateIds = try container.decode(
            [String].self,
            forKey: .appendedCandidateIds
        )
        skippedCandidateIds = try container.decode(
            [String].self,
            forKey: .skippedCandidateIds
        )
        aggregateWallClockSeconds = try container.decodeIfPresent(Int.self, forKey: .aggregateWallClockSeconds)
        evaluationProfileId = try container.decode(
            String.self,
            forKey: .evaluationProfileId
        )
        evaluationProfileLabel = try container.decode(
            String.self,
            forKey: .evaluationProfileLabel
        )
        evaluationResultLevel = try container.decode(
            String.self,
            forKey: .evaluationResultLevel
        )
        evaluationScoreMax = try container.decode(
            Int.self,
            forKey: .evaluationScoreMax
        )
        questionIds = try container.decode([String].self, forKey: .questionIds)
        upgradeFromRunId = try container.decodeIfPresent(String.self, forKey: .upgradeFromRunId)
        upgradeTargetProfileId = try container.decodeIfPresent(
            String.self,
            forKey: .upgradeTargetProfileId
        )
    }
}

struct BridgeEvidenceCard: Decodable, Identifiable {
    var id: String { candidateId }
    let candidateId: String
    let label: String
    let model: String
    let modelId: String
    let effort: String
    let sourceId: String?
    let connectionId: String?
    let familyId: String?
    let variantId: String?
    let recentCount: Int
    let questionCount: Int
    let correctCount: Int
    let questionAttempted: Int
    let questionCompleted: Int
    let scoreText: String
    let evaluationProfileId: String
    let evaluationResultLevel: String
    let modeScore: Int
    let modeScoreMax: Int
    let modeScoreText: String
    let overallScore: Int?
    let overallScoreText: String?
    let hits516: Int
    let hitRate516: Int
    let passRate: Int
    let avgReasoningTokens: Int
    let latestReasoningTokens: Int?
    let latestStatus: String?
    let latestValidRunId: String?
    let latestValidAt: String?
    let validRunId: String?
    let validCompletedAt: String?
    let questionPackVersion: String
    let latestAttemptAt: String?
    let latestAttemptStatus: String?
    let latestAttemptErrorCategory: String?
    let latestAttemptErrorSummary: String?
    let isCurrentPackComparable: Bool
    let isUsingPreviousValidResult: Bool
    let isCurrentRunEligible: Bool
    let repairableQuestionIds: [String]
    let repairRequiresFullScan: Bool
    let historicalScoreText: String?
    let historicalValidAt: String?
    let questionResults: [BridgeQuestionResult]

    private enum CodingKeys: String, CodingKey {
        case candidateId = "id"
        case label
        case model
        case modelId
        case effort
        case sourceId
        case connectionId
        case familyId
        case variantId
        case recentCount
        case questionCount
        case correctCount
        case questionAttempted
        case questionCompleted
        case scoreText
        case evaluationProfileId
        case evaluationResultLevel
        case modeScore
        case modeScoreMax
        case modeScoreText
        case overallScore
        case overallScoreText
        case hits516
        case hitRate516
        case passRate
        case avgReasoningTokens
        case latestReasoningTokens
        case latestStatus
        case latestValidRunId
        case latestValidAt
        case validRunId
        case validCompletedAt
        case questionPackVersion
        case latestAttemptAt
        case latestAttemptStatus
        case latestAttemptErrorCategory
        case latestAttemptErrorSummary
        case isCurrentPackComparable
        case isUsingPreviousValidResult
        case isCurrentRunEligible
        case repairableQuestionIds
        case repairRequiresFullScan
        case historicalScoreText
        case historicalValidAt
        case questionResults
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        label = try container.decode(String.self, forKey: .label)
        candidateId = try container.decodeIfPresent(String.self, forKey: .candidateId) ?? label
        model = try container.decode(String.self, forKey: .model)
        modelId = try container.decodeIfPresent(String.self, forKey: .modelId) ?? model
        effort = try container.decode(String.self, forKey: .effort)
        sourceId = try container.decodeIfPresent(String.self, forKey: .sourceId)
        connectionId = try container.decodeIfPresent(String.self, forKey: .connectionId)
        familyId = try container.decodeIfPresent(String.self, forKey: .familyId)
        variantId = try container.decodeIfPresent(String.self, forKey: .variantId)
        recentCount = try container.decode(Int.self, forKey: .recentCount)
        questionCount = try container.decode(Int.self, forKey: .questionCount)
        correctCount = try container.decode(Int.self, forKey: .correctCount)
        questionAttempted = try container.decodeIfPresent(Int.self, forKey: .questionAttempted) ?? questionCount
        questionCompleted = try container.decodeIfPresent(Int.self, forKey: .questionCompleted) ?? questionCount
        scoreText = try container.decode(String.self, forKey: .scoreText)
        evaluationProfileId = try container.decodeIfPresent(
            String.self,
            forKey: .evaluationProfileId
        ) ?? "legacy_full"
        evaluationResultLevel = try container.decodeIfPresent(
            String.self,
            forKey: .evaluationResultLevel
        ) ?? "unknown"
        modeScore = try container.decodeIfPresent(Int.self, forKey: .modeScore) ?? correctCount
        modeScoreMax = try container.decodeIfPresent(Int.self, forKey: .modeScoreMax) ?? questionCount
        modeScoreText = try container.decodeIfPresent(
            String.self,
            forKey: .modeScoreText
        ) ?? scoreText
        overallScore = try container.decodeIfPresent(Int.self, forKey: .overallScore)
        overallScoreText = try container.decodeIfPresent(String.self, forKey: .overallScoreText)
        hits516 = try container.decode(Int.self, forKey: .hits516)
        hitRate516 = try container.decode(Int.self, forKey: .hitRate516)
        passRate = try container.decode(Int.self, forKey: .passRate)
        avgReasoningTokens = try container.decode(Int.self, forKey: .avgReasoningTokens)
        latestReasoningTokens = try container.decodeIfPresent(Int.self, forKey: .latestReasoningTokens)
        latestStatus = try container.decodeIfPresent(String.self, forKey: .latestStatus)
        latestValidRunId = try container.decodeIfPresent(String.self, forKey: .latestValidRunId)
        latestValidAt = try container.decodeIfPresent(String.self, forKey: .latestValidAt)
        validRunId = try container.decodeIfPresent(String.self, forKey: .validRunId) ?? latestValidRunId
        validCompletedAt = try container.decodeIfPresent(String.self, forKey: .validCompletedAt) ?? latestValidAt
        questionPackVersion = try container.decodeIfPresent(String.self, forKey: .questionPackVersion) ?? "unknown"
        latestAttemptAt = try container.decodeIfPresent(String.self, forKey: .latestAttemptAt)
        latestAttemptStatus = try container.decodeIfPresent(String.self, forKey: .latestAttemptStatus)
        latestAttemptErrorCategory = try container.decodeIfPresent(String.self, forKey: .latestAttemptErrorCategory)
        latestAttemptErrorSummary = try container.decodeIfPresent(String.self, forKey: .latestAttemptErrorSummary)
        isCurrentPackComparable = try container.decodeIfPresent(Bool.self, forKey: .isCurrentPackComparable) ?? true
        isUsingPreviousValidResult = try container.decodeIfPresent(Bool.self, forKey: .isUsingPreviousValidResult) ?? false
        isCurrentRunEligible = try container.decodeIfPresent(Bool.self, forKey: .isCurrentRunEligible) ?? true
        repairableQuestionIds = try container.decodeIfPresent([String].self, forKey: .repairableQuestionIds) ?? []
        repairRequiresFullScan = try container.decodeIfPresent(Bool.self, forKey: .repairRequiresFullScan) ?? false
        historicalScoreText = try container.decodeIfPresent(String.self, forKey: .historicalScoreText)
        historicalValidAt = try container.decodeIfPresent(String.self, forKey: .historicalValidAt)
        questionResults = try container.decodeIfPresent([BridgeQuestionResult].self, forKey: .questionResults) ?? []
    }
}

struct QuestionSemantic: Identifiable, Equatable {
    var id: String { questionId }
    let questionNumber: Int
    let questionId: String
    let capabilityId: String
    let capabilityLabel: String
    let detailLabel: String
    let scoreMax: Int

    var shortLabel: String { "题\(questionNumber)" }
    var displayName: String { "\(shortLabel) · \(capabilityLabel)" }
    var description: String { "\(detailLabel)，满分 \(scoreMax) 分" }
    var helpText: String { "\(displayName)：\(description)" }

    static func from(_ definition: BridgeQuestionDefinition) -> QuestionSemantic {
        QuestionSemantic(
            questionNumber: definition.questionNumber,
            questionId: definition.id,
            capabilityId: definition.capabilityId,
            capabilityLabel: definition.capabilityLabel,
            detailLabel: definition.detailLabel,
            scoreMax: definition.scoreMax
        )
    }

}

struct BridgeQuestionResult: Decodable, Identifiable {
    var id: String { "\(phase)-\(questionId)" }
    let questionId: String
    let questionTitle: String
    let capabilityId: String
    let capabilityLabel: String
    let detailLabel: String
    let phase: String
    let status: String
    let expectedSummary: String
    let actualSummary: String
    let answerPreview: String
    let scorerReason: String
    let semanticScore: Int?
    let semanticTotal: Int?
    let scoreDetails: [BridgeQuestionScoreDetail]
    let failureSummary: String
    let latencyS: Double
    let inputTokens: Int?
    let cachedInputTokens: Int?
    let cacheWriteInputTokens: Int?
    let outputTokens: Int?
    let reasoningTokens: Int?

    var semanticScoreText: String? {
        guard let semanticScore, let semanticTotal, semanticTotal > 0 else {
            return nil
        }
        return "\(semanticScore)/\(semanticTotal)"
    }

    var semanticDisplayName: String { capabilityLabel }

    var semanticDescription: String { detailLabel }

    private enum CodingKeys: String, CodingKey {
        case questionId
        case questionTitle
        case capabilityId
        case capabilityLabel
        case detailLabel
        case phase
        case status
        case expectedSummary
        case actualSummary
        case answerPreview
        case scorerReason
        case semanticScore
        case semanticTotal
        case scoreDetails
        case failureSummary
        case latencyS
        case inputTokens
        case cachedInputTokens
        case cacheWriteInputTokens
        case outputTokens
        case reasoningTokens
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        questionId = try container.decode(String.self, forKey: .questionId)
        questionTitle = try container.decode(String.self, forKey: .questionTitle)
        capabilityId = try container.decode(String.self, forKey: .capabilityId)
        capabilityLabel = try container.decode(
            String.self,
            forKey: .capabilityLabel
        )
        detailLabel = try container.decode(String.self, forKey: .detailLabel)
        phase = try container.decode(String.self, forKey: .phase)
        status = try container.decode(String.self, forKey: .status)
        expectedSummary = try container.decodeIfPresent(String.self, forKey: .expectedSummary) ?? ""
        actualSummary = try container.decodeIfPresent(String.self, forKey: .actualSummary) ?? ""
        answerPreview = try container.decodeIfPresent(String.self, forKey: .answerPreview) ?? actualSummary
        scorerReason = try container.decodeIfPresent(String.self, forKey: .scorerReason) ?? ""
        semanticScore = try container.decodeIfPresent(Int.self, forKey: .semanticScore)
        semanticTotal = try container.decodeIfPresent(Int.self, forKey: .semanticTotal)
        scoreDetails = try container.decodeIfPresent([BridgeQuestionScoreDetail].self, forKey: .scoreDetails) ?? []
        failureSummary = try container.decodeIfPresent(String.self, forKey: .failureSummary) ?? ""
        latencyS = try container.decodeIfPresent(Double.self, forKey: .latencyS) ?? 0
        inputTokens = try container.decodeIfPresent(Int.self, forKey: .inputTokens)
        cachedInputTokens = try container.decodeIfPresent(Int.self, forKey: .cachedInputTokens)
        cacheWriteInputTokens = try container.decodeIfPresent(Int.self, forKey: .cacheWriteInputTokens)
        outputTokens = try container.decodeIfPresent(Int.self, forKey: .outputTokens)
        reasoningTokens = try container.decodeIfPresent(Int.self, forKey: .reasoningTokens)
    }

}

struct BridgeQuestionScoreDetail: Decodable, Identifiable {
    let id: String
    let label: String
    let points: Int
    let maxPoints: Int
    let passed: Bool
}

enum BridgeQuestionOutcome: Equatable {
    case passed
    case incorrect
    case timedOut
    case executionFailed
    case truncated
    case unknown

    var displayName: String {
        switch self {
        case .passed:
            return "通过"
        case .incorrect:
            return "答错"
        case .timedOut:
            return "超时"
        case .executionFailed:
            return "执行错误"
        case .truncated:
            return "输出截断"
        case .unknown:
            return "未知"
        }
    }

    var symbolName: String {
        switch self {
        case .passed:
            return "checkmark"
        case .incorrect:
            return "xmark"
        case .timedOut:
            return "clock.fill"
        case .executionFailed:
            return "exclamationmark.triangle.fill"
        case .truncated:
            return "ellipsis.rectangle"
        case .unknown:
            return "questionmark"
        }
    }
}

extension BridgeQuestionResult {
    var outcome: BridgeQuestionOutcome {
        switch status.lowercased() {
        case "pass":
            return .passed
        case "fail":
            return .incorrect
        case "timeout":
            return .timedOut
        case "error":
            return .executionFailed
        case "truncated":
            return .truncated
        default:
            return .unknown
        }
    }
}

struct BridgeScoreFacet: Decodable, Identifiable {
    let id: String
    let label: String
    let passed: Int
    let total: Int
}

struct BridgeDecisionTag: Decodable, Identifiable {
    var id: String { kind }
    let kind: String
    let label: String
    let detail: String
}

struct BridgeLeaderboardEntry: Decodable, Identifiable {
    var id: String { label }
    let candidateId: String
    let label: String
    let model: String
    let modelId: String
    let effort: String
    let sourceId: String?
    let connectionId: String?
    let familyId: String?
    let variantId: String?
    let correctCount: Int
    let totalCount: Int
    let questionCount: Int
    let questionAttempted: Int
    let questionCompleted: Int
    let semanticScore: Int
    let semanticTotal: Int
    let scoreText: String
    let scoringMode: String
    let evaluationProfileId: String
    let evaluationResultLevel: String
    let modeScore: Int
    let modeScoreMax: Int
    let modeScoreText: String
    let overallScore: Int?
    let overallScoreText: String?
    let scoreFacets: [BridgeScoreFacet]
    let passRate: Int
    let truncationHits: Int
    let medianElapsedSeconds: Double?
    let elapsedSeconds: Double?
    let estimatedCostUsd: Double?
    let costCoverage: String?
    let decisionTags: [BridgeDecisionTag]
    let isBest: Bool
    let canonicalRank: Int?
    let canonicalRankLabel: String
    let canonicalRankStatus: String
    let canonicalRankSemantics: String
    let canonicalRankScoreBasis: String?
    let isCanonicalRankTied: Bool
    let canonicalRankTieCount: Int
    let canonicalLabels: [String]
    let latestValidRunId: String?
    let latestValidAt: String?
    let validRunId: String?
    let validCompletedAt: String?
    let questionPackVersion: String
    let latestAttemptAt: String?
    let latestAttemptStatus: String?
    let latestAttemptErrorCategory: String?
    let latestAttemptErrorSummary: String?
    let isCurrentPackComparable: Bool
    let isUsingPreviousValidResult: Bool
    let isCurrentRunEligible: Bool
    let repairableQuestionIds: [String]
    let repairRequiresFullScan: Bool
    let historicalScoreText: String?
    let historicalValidAt: String?
    let questionResults: [BridgeQuestionResult]

    private enum CodingKeys: String, CodingKey {
        case candidateId
        case label
        case model
        case modelId
        case effort
        case sourceId
        case connectionId
        case familyId
        case variantId
        case correctCount
        case totalCount
        case questionCount
        case questionAttempted
        case questionCompleted
        case semanticScore
        case semanticTotal
        case scoreText
        case scoringMode
        case evaluationProfileId
        case evaluationResultLevel
        case modeScore
        case modeScoreMax
        case modeScoreText
        case overallScore
        case overallScoreText
        case scoreFacets
        case passRate
        case truncationHits
        case medianElapsedSeconds
        case elapsedSeconds
        case estimatedCostUsd
        case costCoverage
        case decisionTags
        case isBest
        case canonicalRank
        case canonicalRankLabel
        case canonicalRankStatus
        case canonicalRankSemantics
        case canonicalRankScoreBasis
        case isCanonicalRankTied
        case canonicalRankTieCount
        case canonicalLabels
        case latestValidRunId
        case latestValidAt
        case validRunId
        case validCompletedAt
        case questionPackVersion
        case latestAttemptAt
        case latestAttemptStatus
        case latestAttemptErrorCategory
        case latestAttemptErrorSummary
        case isCurrentPackComparable
        case isUsingPreviousValidResult
        case isCurrentRunEligible
        case repairableQuestionIds
        case repairRequiresFullScan
        case historicalScoreText
        case historicalValidAt
        case questionResults
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        candidateId = try container.decodeIfPresent(String.self, forKey: .candidateId) ?? ""
        label = try container.decode(String.self, forKey: .label)
        model = try container.decode(String.self, forKey: .model)
        modelId = try container.decodeIfPresent(String.self, forKey: .modelId) ?? model
        effort = try container.decode(String.self, forKey: .effort)
        sourceId = try container.decodeIfPresent(String.self, forKey: .sourceId)
        connectionId = try container.decodeIfPresent(String.self, forKey: .connectionId)
        familyId = try container.decodeIfPresent(String.self, forKey: .familyId)
        variantId = try container.decodeIfPresent(String.self, forKey: .variantId)
        correctCount = try container.decode(Int.self, forKey: .correctCount)
        totalCount = try container.decode(Int.self, forKey: .totalCount)
        questionCount = try container.decode(Int.self, forKey: .questionCount)
        questionAttempted = try container.decodeIfPresent(Int.self, forKey: .questionAttempted) ?? questionCount
        questionCompleted = try container.decodeIfPresent(Int.self, forKey: .questionCompleted) ?? questionCount
        semanticScore = try container.decode(Int.self, forKey: .semanticScore)
        semanticTotal = try container.decode(Int.self, forKey: .semanticTotal)
        scoreText = try container.decode(String.self, forKey: .scoreText)
        scoringMode = try container.decodeIfPresent(String.self, forKey: .scoringMode) ?? "semantic_q1_q5_equal_v2"
        evaluationProfileId = try container.decodeIfPresent(
            String.self,
            forKey: .evaluationProfileId
        ) ?? "legacy_full"
        evaluationResultLevel = try container.decodeIfPresent(
            String.self,
            forKey: .evaluationResultLevel
        ) ?? "unknown"
        modeScore = try container.decodeIfPresent(Int.self, forKey: .modeScore) ?? semanticScore
        modeScoreMax = try container.decodeIfPresent(Int.self, forKey: .modeScoreMax) ?? semanticTotal
        modeScoreText = try container.decodeIfPresent(
            String.self,
            forKey: .modeScoreText
        ) ?? scoreText
        overallScore = try container.decodeIfPresent(Int.self, forKey: .overallScore)
        overallScoreText = try container.decodeIfPresent(String.self, forKey: .overallScoreText)
        scoreFacets = try container.decodeIfPresent([BridgeScoreFacet].self, forKey: .scoreFacets) ?? []
        passRate = try container.decode(Int.self, forKey: .passRate)
        truncationHits = try container.decode(Int.self, forKey: .truncationHits)
        medianElapsedSeconds = try container.decodeIfPresent(Double.self, forKey: .medianElapsedSeconds)
        elapsedSeconds = try container.decodeIfPresent(Double.self, forKey: .elapsedSeconds)
        estimatedCostUsd = try container.decodeIfPresent(Double.self, forKey: .estimatedCostUsd)
        costCoverage = try container.decodeIfPresent(String.self, forKey: .costCoverage)
        decisionTags = try container.decodeIfPresent([BridgeDecisionTag].self, forKey: .decisionTags) ?? []
        isBest = try container.decodeIfPresent(Bool.self, forKey: .isBest) ?? false
        canonicalRank = try container.decodeIfPresent(Int.self, forKey: .canonicalRank)
        canonicalRankLabel = try container.decodeIfPresent(
            String.self,
            forKey: .canonicalRankLabel
        ) ?? "暂不排名"
        canonicalRankStatus = try container.decodeIfPresent(
            String.self,
            forKey: .canonicalRankStatus
        ) ?? "unranked"
        canonicalRankSemantics = try container.decodeIfPresent(
            String.self,
            forKey: .canonicalRankSemantics
        ) ?? "competition"
        canonicalRankScoreBasis = try container.decodeIfPresent(
            String.self,
            forKey: .canonicalRankScoreBasis
        )
        isCanonicalRankTied = try container.decodeIfPresent(
            Bool.self,
            forKey: .isCanonicalRankTied
        ) ?? false
        canonicalRankTieCount = try container.decodeIfPresent(
            Int.self,
            forKey: .canonicalRankTieCount
        ) ?? 0
        canonicalLabels = try container.decodeIfPresent(
            [String].self,
            forKey: .canonicalLabels
        ) ?? []
        latestValidRunId = try container.decodeIfPresent(String.self, forKey: .latestValidRunId)
        latestValidAt = try container.decodeIfPresent(String.self, forKey: .latestValidAt)
        validRunId = try container.decodeIfPresent(String.self, forKey: .validRunId) ?? latestValidRunId
        validCompletedAt = try container.decodeIfPresent(String.self, forKey: .validCompletedAt) ?? latestValidAt
        questionPackVersion = try container.decodeIfPresent(String.self, forKey: .questionPackVersion) ?? "unknown"
        latestAttemptAt = try container.decodeIfPresent(String.self, forKey: .latestAttemptAt)
        latestAttemptStatus = try container.decodeIfPresent(String.self, forKey: .latestAttemptStatus)
        latestAttemptErrorCategory = try container.decodeIfPresent(String.self, forKey: .latestAttemptErrorCategory)
        latestAttemptErrorSummary = try container.decodeIfPresent(String.self, forKey: .latestAttemptErrorSummary)
        isCurrentPackComparable = try container.decodeIfPresent(Bool.self, forKey: .isCurrentPackComparable) ?? true
        isUsingPreviousValidResult = try container.decodeIfPresent(Bool.self, forKey: .isUsingPreviousValidResult) ?? false
        isCurrentRunEligible = try container.decodeIfPresent(Bool.self, forKey: .isCurrentRunEligible) ?? true
        repairableQuestionIds = try container.decodeIfPresent([String].self, forKey: .repairableQuestionIds) ?? []
        repairRequiresFullScan = try container.decodeIfPresent(Bool.self, forKey: .repairRequiresFullScan) ?? false
        historicalScoreText = try container.decodeIfPresent(String.self, forKey: .historicalScoreText)
        historicalValidAt = try container.decodeIfPresent(String.self, forKey: .historicalValidAt)
        questionResults = try container.decodeIfPresent([BridgeQuestionResult].self, forKey: .questionResults) ?? []
    }
}

struct BridgeBestCombination: Decodable {
    let label: String
    let shortDisplayName: String
    let model: String
    let effort: String
    let effortLabel: String
    let stabilityText: String
    let scoreText: String
    let semanticScore: Int
    let semanticTotal: Int
    let scoringMode: String
    let overallScore: Int?
    let overallScoreText: String?
    let avgReasoningTokens: Int
    let truncationHits: Int
    let passRate: Int
    let recommendationBasis: String
    let confidenceLabel: String
    let confidenceReason: String
    let confidenceReasons: [String]
    let recommendationOutcome: String
    let evidenceState: String
    let decisionReason: String
    let currentDefaultCandidateId: String?
    let decisionState: String
    let decisionTitle: String
    let decisionActionLabel: String
    let runnerUpGapText: String
    let overallGap: Int?
    let questionPackVersion: String
    let questionPackDisplayText: String
    let questionPackContextText: String
    let displayLabel: String
    let copyValue: String
    let candidateId: String
    let recommendationCreatedAt: String?
    let runCompletedAt: String?
    let staleAt: String?
    let expiresAt: String?

    private enum CodingKeys: String, CodingKey {
        case label
        case shortDisplayName
        case model
        case effort
        case effortLabel
        case stabilityText
        case scoreText
        case semanticScore
        case semanticTotal
        case scoringMode
        case overallScore
        case overallScoreText
        case avgReasoningTokens
        case truncationHits
        case passRate
        case recommendationBasis
        case confidenceLabel
        case confidenceReason
        case confidenceReasons
        case recommendationOutcome
        case evidenceState
        case decisionReason
        case currentDefaultCandidateId
        case decisionState
        case decisionTitle
        case decisionActionLabel
        case runnerUpGapText
        case overallGap
        case questionPackVersion
        case questionPackDisplayText
        case questionPackContextText
        case displayLabel
        case copyValue
        case candidateId
        case recommendationCreatedAt
        case runCompletedAt
        case staleAt
        case expiresAt
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        label = try container.decode(String.self, forKey: .label)
        model = try container.decode(String.self, forKey: .model)
        effort = try container.decode(String.self, forKey: .effort)
        effortLabel = try container.decode(String.self, forKey: .effortLabel)
        stabilityText = try container.decode(String.self, forKey: .stabilityText)
        scoreText = try container.decode(String.self, forKey: .scoreText)
        semanticScore = try container.decode(Int.self, forKey: .semanticScore)
        semanticTotal = try container.decode(Int.self, forKey: .semanticTotal)
        scoringMode = try container.decodeIfPresent(String.self, forKey: .scoringMode) ?? "semantic_q1_q5_equal_v2"
        overallScore = try container.decodeIfPresent(Int.self, forKey: .overallScore)
        overallScoreText = try container.decodeIfPresent(String.self, forKey: .overallScoreText)
        avgReasoningTokens = try container.decode(Int.self, forKey: .avgReasoningTokens)
        truncationHits = try container.decode(Int.self, forKey: .truncationHits)
        passRate = try container.decode(Int.self, forKey: .passRate)
        recommendationBasis = try container.decodeIfPresent(String.self, forKey: .recommendationBasis) ?? "overall_score_pending"
        confidenceLabel = try container.decodeIfPresent(String.self, forKey: .confidenceLabel) ?? "低"
        confidenceReason = try container.decodeIfPresent(String.self, forKey: .confidenceReason) ?? "等待下一轮扫描"
        confidenceReasons = try container.decodeIfPresent([String].self, forKey: .confidenceReasons) ?? []
        let rawDecisionState = try container.decodeIfPresent(String.self, forKey: .decisionState)
        let fallbackDecisionState: String
        switch rawDecisionState {
        case "keep", "switch", "recommend", "wait", "retain_after_failure":
            fallbackDecisionState = rawDecisionState ?? "wait"
        case "retry_required":
            fallbackDecisionState = "retain_after_failure"
        default:
            fallbackDecisionState = "wait"
        }
        let fallbackDecisionTitle: String
        let fallbackDecisionActionLabel: String
        if fallbackDecisionState == "retain_after_failure" {
            fallbackDecisionTitle = "本次失败，保留旧成绩"
            fallbackDecisionActionLabel = "查看失败详情"
        } else {
            fallbackDecisionTitle = "等待更多可比较证据"
            fallbackDecisionActionLabel = "再扫描一轮"
        }
        decisionState = fallbackDecisionState
        recommendationOutcome = try container.decodeIfPresent(String.self, forKey: .recommendationOutcome)
            ?? ((fallbackDecisionState == "keep" || fallbackDecisionState == "switch" || fallbackDecisionState == "recommend") ? fallbackDecisionState : "wait")
        evidenceState = try container.decodeIfPresent(String.self, forKey: .evidenceState)
            ?? (fallbackDecisionState == "retain_after_failure" ? "retained_after_failure" : "fresh")
        decisionReason = try container.decodeIfPresent(String.self, forKey: .decisionReason) ?? confidenceReason
        currentDefaultCandidateId = try container.decodeIfPresent(String.self, forKey: .currentDefaultCandidateId)
        decisionTitle = try container.decodeIfPresent(String.self, forKey: .decisionTitle) ?? fallbackDecisionTitle
        decisionActionLabel = try container.decodeIfPresent(String.self, forKey: .decisionActionLabel) ?? fallbackDecisionActionLabel
        runnerUpGapText = try container.decodeIfPresent(String.self, forKey: .runnerUpGapText) ?? "等待下一轮扫描"
        overallGap = try container.decodeIfPresent(Int.self, forKey: .overallGap)
        questionPackVersion = try container.decodeIfPresent(String.self, forKey: .questionPackVersion) ?? "unknown"
        questionPackDisplayText = try container.decodeIfPresent(String.self, forKey: .questionPackDisplayText)
            ?? (questionPackVersion == "unknown" ? "未记录（旧数据）" : questionPackVersion)
        questionPackContextText = try container.decodeIfPresent(String.self, forKey: .questionPackContextText) ?? "当前 run"
        displayLabel = try container.decodeIfPresent(String.self, forKey: .displayLabel) ?? label
        copyValue = try container.decodeIfPresent(String.self, forKey: .copyValue) ?? displayLabel
        candidateId = try container.decodeIfPresent(String.self, forKey: .candidateId) ?? label
        shortDisplayName = try container.decodeIfPresent(String.self, forKey: .shortDisplayName) ?? displayLabel
        recommendationCreatedAt = try container.decodeIfPresent(String.self, forKey: .recommendationCreatedAt)
        runCompletedAt = try container.decodeIfPresent(String.self, forKey: .runCompletedAt)
        staleAt = try container.decodeIfPresent(String.self, forKey: .staleAt)
        expiresAt = try container.decodeIfPresent(String.self, forKey: .expiresAt)
    }
}

struct BridgeRunEntry: Decodable, Identifiable {
    var id: String { candidateId }
    let candidateId: String
    let model: String
    let effort: String
    let label: String
    let phase: String?
    let status: String
    let finalStatus: String?
    let reasoningTokens: Int?
    let attemptsCompleted: Int?
    let attemptsPerTarget: Int?
    let flags: [String]
    let errorMessage: String?

    private enum CodingKeys: String, CodingKey {
        case candidateId
        case model
        case effort
        case label
        case phase
        case status
        case finalStatus
        case reasoningTokens
        case attemptsCompleted
        case attemptsPerTarget
        case flags
        case errorMessage
    }

    init(
        candidateId: String,
        model: String,
        effort: String,
        label: String,
        phase: String?,
        status: String,
        finalStatus: String?,
        reasoningTokens: Int?,
        attemptsCompleted: Int?,
        attemptsPerTarget: Int?,
        flags: [String],
        errorMessage: String?
    ) {
        self.candidateId = candidateId
        self.model = model
        self.effort = effort
        self.label = label
        self.phase = phase
        self.status = status
        self.finalStatus = finalStatus
        self.reasoningTokens = reasoningTokens
        self.attemptsCompleted = attemptsCompleted
        self.attemptsPerTarget = attemptsPerTarget
        self.flags = flags
        self.errorMessage = errorMessage
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        model = try container.decode(String.self, forKey: .model)
        effort = try container.decode(String.self, forKey: .effort)
        label = try container.decode(String.self, forKey: .label)
        candidateId = try container.decodeIfPresent(String.self, forKey: .candidateId) ?? label
        phase = try container.decodeIfPresent(String.self, forKey: .phase)
        status = try container.decode(String.self, forKey: .status)
        finalStatus = try container.decodeIfPresent(String.self, forKey: .finalStatus)
        reasoningTokens = try container.decodeIfPresent(Int.self, forKey: .reasoningTokens)
        attemptsCompleted = try container.decodeIfPresent(Int.self, forKey: .attemptsCompleted)
        attemptsPerTarget = try container.decodeIfPresent(Int.self, forKey: .attemptsPerTarget)
        flags = try container.decodeIfPresent([String].self, forKey: .flags) ?? []
        errorMessage = try container.decodeIfPresent(String.self, forKey: .errorMessage)
    }
}

struct BridgeRuntimeEventStateV1: Decodable {
    let schemaVersion: Int
    let runtime: BridgeRuntime

    private enum CodingKeys: String, CodingKey {
        case schemaVersion
        case runtime
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decode(Int.self, forKey: .schemaVersion)
        guard schemaVersion == 1 else {
            throw DecodingError.dataCorruptedError(
                forKey: .schemaVersion,
                in: container,
                debugDescription: "Unsupported runtime event state schema version: \(schemaVersion)"
            )
        }
        runtime = try container.decode(BridgeRuntime.self, forKey: .runtime)
    }
}

enum RuntimeEventStateKindV1: String, Decodable {
    case none
    case runtimeDelta = "runtime_delta"
    case snapshot
}

struct ScanEvent {
    let schemaVersion: Int
    let stateKind: RuntimeEventStateKindV1
    let type: String
    let totalTargets: Int?
    let completedTargets: Int?
    let index: Int?
    let label: String?
    let runtimeState: BridgeRuntimeEventStateV1?
    let snapshot: BridgeSnapshot?
    let lastPhase: String?
    let lastPhaseCompleted: Int?
    let lastPhaseTotal: Int?
    let finalizingStartedAt: String?
    let updatedAt: String?
    let leaseExpiresAt: String?
    let failureCategory: String?
    let failureMessage: String?
    let reason: String?
    let message: String?
    let attempt: Int?

    static func bridgeFailure(message: String) -> ScanEvent {
        ScanEvent(
            schemaVersion: 1,
            stateKind: .none,
            type: "scan.failed",
            totalTargets: nil,
            completedTargets: nil,
            index: nil,
            label: nil,
            runtimeState: nil,
            snapshot: nil,
            lastPhase: nil,
            lastPhaseCompleted: nil,
            lastPhaseTotal: nil,
            finalizingStartedAt: nil,
            updatedAt: nil,
            leaseExpiresAt: nil,
            failureCategory: "bridge_process_failed",
            failureMessage: message,
            reason: nil,
            message: nil,
            attempt: nil
        )
    }

    static func bridgeDecodeFailure(message: String) -> ScanEvent {
        ScanEvent(
            schemaVersion: 1,
            stateKind: .none,
            type: "scan.failed",
            totalTargets: nil,
            completedTargets: nil,
            index: nil,
            label: nil,
            runtimeState: nil,
            snapshot: nil,
            lastPhase: nil,
            lastPhaseCompleted: nil,
            lastPhaseTotal: nil,
            finalizingStartedAt: nil,
            updatedAt: nil,
            leaseExpiresAt: nil,
            failureCategory: "bridge_event_decode_failed",
            failureMessage: message,
            reason: nil,
            message: nil,
            attempt: nil
        )
    }
}

extension ScanEvent: Decodable {
    private enum CodingKeys: String, CodingKey {
        case schemaVersion
        case stateKind
        case type
        case totalTargets
        case completedTargets
        case index
        case label
        case state
        case lastPhase
        case lastPhaseCompleted
        case lastPhaseTotal
        case finalizingStartedAt
        case updatedAt
        case leaseExpiresAt
        case failureCategory
        case failureMessage
        case reason
        case message
        case attempt
    }

    private static func expectedStateKind(
        for eventType: String
    ) -> RuntimeEventStateKindV1? {
        switch eventType {
        case "scan.started",
             "target.started",
             "scan.progress",
             "scan.finalizing",
             "repair.started",
             "repair.question.started",
             "repair.question.finished",
             "repair.finalizing",
             "timeout-repair.started",
             "timeout-repair.question.started",
             "timeout-repair.question.finished",
             "timeout-repair.finalizing":
            return .runtimeDelta
        case "auto-resume.started":
            return RuntimeEventStateKindV1.none
        case "scan.finished",
             "scan.paused",
             "scan.stopped",
             "scan.already_running",
             "scan.failed",
             "repair.finished",
             "repair.paused",
             "repair.stopped",
             "repair.already_running",
             "repair.failed",
             "timeout-repair.finished",
             "timeout-repair.paused",
             "timeout-repair.stopped",
             "timeout-repair.already_running",
             "timeout-repair.failed",
             "auto-resume.noop",
             "auto-resume.manual-attention":
            return .snapshot
        default:
            return nil
        }
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        type = try container.decode(String.self, forKey: .type)
        schemaVersion = try container.decode(
            Int.self,
            forKey: .schemaVersion
        )
        guard schemaVersion == 1 else {
            throw DecodingError.dataCorruptedError(
                forKey: .schemaVersion,
                in: container,
                debugDescription: "Unsupported runtime event schema version: \(schemaVersion)"
            )
        }
        stateKind = try container.decode(
            RuntimeEventStateKindV1.self,
            forKey: .stateKind
        )
        guard let expectedStateKind = Self.expectedStateKind(for: type) else {
            throw DecodingError.dataCorruptedError(
                forKey: .type,
                in: container,
                debugDescription: "Unsupported RuntimeEventV1 type: \(type)"
            )
        }
        guard stateKind == expectedStateKind else {
            throw DecodingError.dataCorruptedError(
                forKey: .stateKind,
                in: container,
                debugDescription: "RuntimeEventV1 state_kind does not match \(type)"
            )
        }
        totalTargets = try container.decodeIfPresent(Int.self, forKey: .totalTargets)
        completedTargets = try container.decodeIfPresent(Int.self, forKey: .completedTargets)
        index = try container.decodeIfPresent(Int.self, forKey: .index)
        label = try container.decodeIfPresent(String.self, forKey: .label)
        switch stateKind {
        case .snapshot:
            runtimeState = nil
            snapshot = try container.decode(BridgeSnapshot.self, forKey: .state)
        case .runtimeDelta:
            runtimeState = try container.decode(
                BridgeRuntimeEventStateV1.self,
                forKey: .state
            )
            snapshot = nil
        case .none:
            if container.contains(.state) {
                throw DecodingError.dataCorruptedError(
                    forKey: .stateKind,
                    in: container,
                    debugDescription: "RuntimeEventV1 state_kind none cannot carry state"
                )
            }
            runtimeState = nil
            snapshot = nil
        }
        lastPhase = try container.decodeIfPresent(String.self, forKey: .lastPhase)
        lastPhaseCompleted = try container.decodeIfPresent(Int.self, forKey: .lastPhaseCompleted)
        lastPhaseTotal = try container.decodeIfPresent(Int.self, forKey: .lastPhaseTotal)
        finalizingStartedAt = try container.decodeIfPresent(String.self, forKey: .finalizingStartedAt)
        updatedAt = try container.decodeIfPresent(String.self, forKey: .updatedAt)
        leaseExpiresAt = try container.decodeIfPresent(String.self, forKey: .leaseExpiresAt)
        failureCategory = try container.decodeIfPresent(String.self, forKey: .failureCategory)
        failureMessage = try container.decodeIfPresent(String.self, forKey: .failureMessage)
        reason = try container.decodeIfPresent(String.self, forKey: .reason)
        message = try container.decodeIfPresent(String.self, forKey: .message)
        attempt = try container.decodeIfPresent(Int.self, forKey: .attempt)
    }
}
