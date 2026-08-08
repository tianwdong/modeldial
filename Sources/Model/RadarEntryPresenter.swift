import Foundation

enum RadarEntryPresenter {
    enum Tone: Equatable {
        case active
        case warning
        case neutral
    }

    struct RuntimePresentation: Equatable {
        let status: String?
        let isRunning: Bool
        let isInterrupted: Bool
        let isInCurrentOperation: Bool
        let progressText: String
        let progressFraction: Double
        let tone: Tone
    }

    struct Entry: Identifiable {
        let id: String
        let label: String
        let model: String
        let effort: String
        let canonicalModelName: String
        let shortDisplayLabel: String
        let identityDisplayLabel: String
        let scoreText: String
        let evaluationResultLevel: String
        let modeScore: Int
        let modeScoreMax: Int
        let modeScoreText: String
        let overallScore: Int?
        let overallScoreText: String?
        let progressText: String
        let passRate: Int
        let isBest: Bool
        let isCurrentDefault: Bool
        let runtimeStatus: String?
        let isRunning: Bool
        let isInterrupted: Bool
        let isUsingPreviousValidResult: Bool
        let isCurrentRunEligible: Bool
        let repairableQuestionIds: [String]
        let repairRequiresFullScan: Bool
        let historicalScoreText: String?
        let historicalValidAt: String?
        let medianElapsedSeconds: Double?
        let elapsedSeconds: Double?
        let estimatedCostUsd: Double?
        let decisionTags: [BridgeDecisionTag]
        let canonicalRank: Int?
        let canonicalRankLabel: String
        let accentTone: Tone
        let progressFraction: Double
        let questionResults: [BridgeQuestionResult]
        let scoreFacets: [BridgeScoreFacet]
        let evidenceAvailability: RadarPresenter.EvidenceAvailabilityPresentation
    }

    static func entries(
        leaderboard: [BridgeLeaderboardEntry],
        runEntries: [BridgeRunEntry],
        bestCandidateID: String?,
        currentDefaultCandidateID: String?,
        currentPhase: BridgeRuntimePhase?,
        questionSemantics: [QuestionSemantic]
    ) -> [Entry] {
        let runEntryByCandidateID = Dictionary(
            runEntries.map { ($0.candidateId, $0) },
            uniquingKeysWith: { first, _ in first }
        )
        let questionContracts = questionSemantics.map {
            RadarPresenter.QuestionContractInput(id: $0.questionId, scoreMax: $0.scoreMax)
        }

        return leaderboard.map { leaderboardEntry in
            let runEntry = runEntryByCandidateID[leaderboardEntry.candidateId]
            let resultContracts = leaderboardEntry.questionResults.map {
                RadarPresenter.QuestionResultContractInput(
                    id: $0.questionId,
                    semanticTotal: $0.semanticTotal
                )
            }
            let runtime = runtime(
                runEntry: runEntry,
                currentPhase: currentPhase?.rawValue,
                questionContracts: questionContracts,
                questionResults: resultContracts
            )
            let identityDisplayLabel = ModelIdentityPresentation.displayLabel(
                model: leaderboardEntry.model,
                effort: leaderboardEntry.effort
            )
            return Entry(
                id: leaderboardEntry.candidateId.isEmpty
                    ? leaderboardEntry.id
                    : leaderboardEntry.candidateId,
                label: leaderboardEntry.label,
                model: leaderboardEntry.model,
                effort: leaderboardEntry.effort,
                canonicalModelName: ModelIdentityPresentation.canonicalName(
                    for: leaderboardEntry.model
                ),
                shortDisplayLabel: identityDisplayLabel,
                identityDisplayLabel: identityDisplayLabel,
                scoreText: leaderboardEntry.overallScoreText
                    ?? leaderboardEntry.modeScoreText,
                evaluationResultLevel: leaderboardEntry.evaluationResultLevel,
                modeScore: leaderboardEntry.modeScore,
                modeScoreMax: leaderboardEntry.modeScoreMax,
                modeScoreText: leaderboardEntry.modeScoreText,
                overallScore: leaderboardEntry.overallScore,
                overallScoreText: leaderboardEntry.overallScoreText,
                progressText: runtime.progressText,
                passRate: leaderboardEntry.passRate,
                isBest: bestCandidateID == leaderboardEntry.candidateId,
                isCurrentDefault: currentDefaultCandidateID == leaderboardEntry.candidateId,
                runtimeStatus: runtime.status,
                isRunning: runtime.isRunning,
                isInterrupted: runtime.isInterrupted,
                isUsingPreviousValidResult: leaderboardEntry.isUsingPreviousValidResult,
                isCurrentRunEligible: leaderboardEntry.isCurrentRunEligible,
                repairableQuestionIds: leaderboardEntry.repairableQuestionIds,
                repairRequiresFullScan: leaderboardEntry.repairRequiresFullScan,
                historicalScoreText: leaderboardEntry.historicalScoreText,
                historicalValidAt: leaderboardEntry.historicalValidAt,
                medianElapsedSeconds: leaderboardEntry.medianElapsedSeconds,
                elapsedSeconds: leaderboardEntry.elapsedSeconds,
                estimatedCostUsd: leaderboardEntry.estimatedCostUsd,
                decisionTags: leaderboardEntry.decisionTags,
                canonicalRank: leaderboardEntry.canonicalRank,
                canonicalRankLabel: leaderboardEntry.canonicalRankLabel,
                accentTone: runtime.tone,
                progressFraction: runtime.progressFraction,
                questionResults: leaderboardEntry.questionResults,
                scoreFacets: leaderboardEntry.scoreFacets,
                evidenceAvailability: RadarPresenter.evidenceAvailability(
                    RadarPresenter.EvidenceAvailabilityInput(
                        scoringMode: leaderboardEntry.scoringMode,
                        questionCompleted: leaderboardEntry.questionCompleted,
                        hasQuestionResults: !leaderboardEntry.questionResults.isEmpty,
                        hasLatestValidAt: leaderboardEntry.latestValidAt != nil,
                        hasLatestAttemptStatus: leaderboardEntry.latestAttemptStatus != nil,
                        isCurrentPackComparable: leaderboardEntry.isCurrentPackComparable,
                        isInCurrentOperation: runtime.isInCurrentOperation,
                        isCurrentRunEligible: leaderboardEntry.isCurrentRunEligible,
                        hasLatestAttemptError:
                            leaderboardEntry.latestAttemptErrorCategory != nil,
                        hasOverallScore: leaderboardEntry.overallScore != nil,
                        questionContracts: questionContracts,
                        questionResults: resultContracts
                    )
                )
            )
        }
    }

    static func runtime(
        runEntry: BridgeRunEntry?,
        currentPhase: String?,
        questionContracts: [RadarPresenter.QuestionContractInput],
        questionResults: [RadarPresenter.QuestionResultContractInput]
    ) -> RuntimePresentation {
        let progress = OperationalStatePresenter.progress(
            OperationalStatePresenter.ProgressInput(
                hasRunEntry: runEntry != nil,
                attemptsPerTarget: runEntry?.attemptsPerTarget,
                attemptsCompleted: runEntry?.attemptsCompleted,
                questions: questionContracts.map {
                    OperationalStatePresenter.ProgressQuestionInput(
                        id: $0.id,
                        scoreMax: $0.scoreMax
                    )
                },
                results: questionResults.map {
                    OperationalStatePresenter.ProgressResultInput(
                        questionID: $0.id,
                        semanticTotal: $0.semanticTotal
                    )
                }
            )
        )
        let isInCurrentOperation = runEntry.map {
            currentPhase == nil || currentPhase == "scan" || $0.phase == currentPhase
        } ?? false
        let status = runEntry?.status
        let isRunning = status == "running"
        let isInterrupted = status == "interrupted"
        let progressPrefix: String
        if runEntry?.phase == "repair" {
            progressPrefix = L10n.tr("重试")
        } else if runEntry != nil {
            progressPrefix = L10n.tr("扫描")
        } else {
            progressPrefix = L10n.tr("已完成")
        }
        return RuntimePresentation(
            status: status,
            isRunning: isRunning,
            isInterrupted: isInterrupted,
            isInCurrentOperation: isInCurrentOperation,
            progressText: L10n.tr(
                "%@ %d/%d",
                progressPrefix,
                progress.completed,
                progress.total
            ),
            progressFraction: min(
                max(Double(progress.completed) / Double(progress.total), 0),
                1
            ),
            tone: isRunning ? .active : isInterrupted ? .warning : .neutral
        )
    }
}
