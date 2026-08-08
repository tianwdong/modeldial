import Foundation

enum ComparisonSelectionPresenter {
    struct DatasetSelection {
        let statistics: BridgeStatistics?
        let leaderboard: [BridgeLeaderboardEntry]
        let pairwiseComparisons: [BridgePairwiseComparison]
        let referenceSnapshot: BridgeReferenceSnapshot?
        let showsLocalRepairControls: Bool
    }

    struct Selection {
        let choices: [BridgeRecommendationDecisionV2]
        let decision: BridgeRecommendationDecisionV2?
        let currentItem: RadarLeaderboardItem?
        let automaticCandidateItem: RadarLeaderboardItem?
        let selectedManualCandidateID: String?
        let candidateItem: RadarLeaderboardItem?
        let selectableManualCandidates: [RadarLeaderboardItem]
        let choiceLabelsByCurrentID: [String: String]
        let itemIDByConfigurationID: [String: String]

        var isManualComparison: Bool {
            selectedManualCandidateID != nil
        }

        func choiceLabel(for decision: BridgeRecommendationDecisionV2) -> String {
            choiceLabelsByCurrentID[decision.currentModelConfigurationId]
                ?? decision.currentModelConfigurationId
        }

        func itemID(for configurationID: String?) -> String? {
            guard let configurationID else { return nil }
            return itemIDByConfigurationID[configurationID] ?? configurationID
        }
    }

    static func dataset(
        usesLocalDataset: Bool,
        usesOfficialSnapshot: Bool,
        localStatistics: BridgeStatistics?,
        localLeaderboard: [BridgeLeaderboardEntry],
        localPairwiseComparisons: [BridgePairwiseComparison],
        officialSnapshot: BridgeReferenceSnapshot?
    ) -> DatasetSelection {
        DatasetSelection(
            statistics: usesLocalDataset ? localStatistics : nil,
            leaderboard: usesLocalDataset ? localLeaderboard : [],
            pairwiseComparisons: usesOfficialSnapshot
                ? officialSnapshot?.pairwiseComparisons ?? []
                : usesLocalDataset ? localPairwiseComparisons : [],
            referenceSnapshot: usesOfficialSnapshot ? officialSnapshot : nil,
            showsLocalRepairControls: usesLocalDataset
        )
    }

    static func select(
        items: [RadarLeaderboardItem],
        representativeDecision: BridgeRecommendationDecisionV2?,
        decisions: [BridgeRecommendationDecisionV2],
        selectedCurrentConfigurationID: String?,
        manualCandidateByCurrentConfigurationID: [String: String],
        itemIDByConfigurationID: [String: String] = [:],
        displaySource: String? = nil,
        sourceModeByConfigurationID: [String: String] = [:]
    ) -> Selection {
        let itemByID = Dictionary(
            items.map { ($0.id, $0) },
            uniquingKeysWith: { first, _ in first }
        )
        func resolvedItemID(_ configurationID: String) -> String? {
            if itemByID[configurationID] != nil {
                return configurationID
            }
            guard let itemID = itemIDByConfigurationID[configurationID],
                  itemByID[itemID] != nil else {
                return nil
            }
            return itemID
        }
        func sourceCompatibleItemID(_ itemID: String) -> Bool {
            let configurationIDs = [itemID]
                + itemIDByConfigurationID.compactMap { configurationID, mappedItemID in
                    mappedItemID == itemID ? configurationID : nil
                }
            return Self.sourceCompatible(
                configurationIDs: configurationIDs,
                displaySource: displaySource,
                sourceModeByConfigurationID: sourceModeByConfigurationID
            )
        }
        let sourceChoices = decisions.isEmpty
            ? [representativeDecision].compactMap { $0 }
            : decisions
        let choices = sourceChoices.filter { choice in
            resolvedItemID(choice.currentModelConfigurationId) != nil
                && resolvedItemID(targetCandidateID(for: choice)) != nil
                && sourceCompatible(
                    configurationIDs: [
                        choice.currentModelConfigurationId,
                        targetCandidateID(for: choice),
                    ],
                    displaySource: displaySource,
                    sourceModeByConfigurationID: sourceModeByConfigurationID
                )
        }
        let decision: BridgeRecommendationDecisionV2?
        if let selectedCurrentConfigurationID,
           let selected = choices.first(where: {
               $0.currentModelConfigurationId == selectedCurrentConfigurationID
           }) {
            decision = selected
        } else if let representativeDecision,
                  choices.contains(where: {
                      $0.currentModelConfigurationId
                          == representativeDecision.currentModelConfigurationId
                  }) {
            decision = representativeDecision
        } else {
            decision = choices.first
        }

        let currentItemID = decision.flatMap {
            resolvedItemID($0.currentModelConfigurationId)
        }
            ?? items.first(where: { $0.isCurrent && sourceCompatibleItemID($0.id) })?.id
        let automaticCandidateItemID = decision.flatMap {
            resolvedItemID(targetCandidateID(for: $0))
        }
        let selectedManualCandidateID: String?
        if let currentDecisionID = decision?.currentModelConfigurationId,
           let candidateID = manualCandidateByCurrentConfigurationID[currentDecisionID],
           candidateID != currentItemID,
           candidateID != automaticCandidateItemID,
           sourceCompatibleItemID(candidateID),
           itemByID[candidateID] != nil {
            selectedManualCandidateID = candidateID
        } else {
            selectedManualCandidateID = nil
        }
        let selectableManualCandidateIDs: [String]
        if let currentItemID, let automaticCandidateItemID {
            selectableManualCandidateIDs = items.compactMap { item in
                item.id != currentItemID
                    && item.id != automaticCandidateItemID
                    && sourceCompatibleItemID(item.id)
                    ? item.id
                    : nil
            }
        } else {
            selectableManualCandidateIDs = []
        }

        return Selection(
            choices: choices,
            decision: decision,
            currentItem: currentItemID.flatMap { itemByID[$0] },
            automaticCandidateItem: automaticCandidateItemID.flatMap { itemByID[$0] },
            selectedManualCandidateID: selectedManualCandidateID,
            candidateItem: (selectedManualCandidateID ?? automaticCandidateItemID).flatMap {
                itemByID[$0]
            },
            selectableManualCandidates: selectableManualCandidateIDs.compactMap {
                itemByID[$0]
            },
            choiceLabelsByCurrentID: Dictionary(
                choices.map { choice in
                    let id = choice.currentModelConfigurationId
                    return (id, resolvedItemID(id).flatMap { itemByID[$0] }?.displayName ?? id)
                },
                uniquingKeysWith: { first, _ in first }
            ),
            itemIDByConfigurationID: itemIDByConfigurationID
        )
    }

    private static func targetCandidateID(
        for decision: BridgeRecommendationDecisionV2
    ) -> String {
        decision.candidateModelConfigurationId
            ?? decision.comparisonCandidateModelConfigurationId
            ?? decision.currentModelConfigurationId
    }

    private static func sourceCompatible(
        configurationIDs: [String],
        displaySource: String?,
        sourceModeByConfigurationID: [String: String]
    ) -> Bool {
        guard displaySource == "local_evaluation"
                || displaySource == "official_snapshot" else {
            return true
        }
        return configurationIDs.allSatisfy { configurationID in
            switch sourceModeByConfigurationID[configurationID] {
            case nil, "auto":
                return true
            case let sourceMode?:
                return sourceMode == displaySource
            }
        }
    }
}
