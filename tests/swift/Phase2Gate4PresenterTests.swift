import Foundation

private var failureCount = 0

private func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() {
        failureCount += 1
        fputs("FAIL: \(message)\n", stderr)
    }
}

private func candidate(
    id: String,
    connectionID: String,
    modelID: String,
    familyID: String,
    profile: String,
    enabled: Bool = true
) -> BridgeIngressModelCandidate {
    BridgeIngressModelCandidate(
        id: id,
        connectionId: connectionID,
        modelId: modelID,
        displayName: modelID,
        familyId: familyID,
        variantId: profile,
        enabled: enabled,
        scanProfile: profile,
        capabilities: []
    )
}

private func connection(
    id: String,
    sourceID: String,
    providerID: String?,
    candidates: [BridgeIngressModelCandidate]
) -> BridgeIngressConnection {
    BridgeIngressConnection(
        id: id,
        sourceId: sourceID,
        name: id,
        enabled: true,
        apiFormat: providerID == nil ? nil : "openai_responses",
        providerPreset: providerID ?? "local",
        providerId: providerID,
        providerDisplayName: nil,
        authMode: nil,
        catalogSource: nil,
        baseUrl: nil,
        apiKeyRef: nil,
        notes: nil,
        lastTestStatus: providerID == nil ? nil : "ok",
        lastTestAt: nil,
        lastTestMessage: nil,
        localLoginVerified: true,
        modelCandidates: candidates
    )
}

private func candidateProjection(
    candidate: BridgeIngressModelCandidate,
    sourceID: String,
    providerID: String?,
    displayModel: String
) -> BridgeSettingsCandidateProjection {
    BridgeSettingsCandidateProjection(
        candidateId: candidate.id,
        sourceId: sourceID,
        connectionId: candidate.connectionId,
        providerId: providerID,
        familyId: candidate.familyId,
        variantId: candidate.variantId,
        modelId: candidate.modelId,
        displayModel: displayModel,
        scanProfile: candidate.scanProfile,
        displayScanProfile: candidate.scanProfile,
        enabled: candidate.enabled,
        available: true
    )
}

private func provider(
    id: String,
    featured: Bool,
    connectionSupported: Bool?
) -> BridgeProviderCatalogProvider {
    BridgeProviderCatalogProvider(
        providerId: id,
        displayName: id.capitalized,
        providerPreset: id,
        defaultBaseUrl: nil,
        baseUrlHosts: [],
        featured: featured,
        defaultApiFormat: "openai_responses",
        defaultModelIds: [],
        websiteUrl: nil,
        apiKeyUrl: nil,
        connectionSupported: connectionSupported,
        availabilityNote: nil,
        families: []
    )
}

private func verifySettingsIngressPresenter() {
    let localCandidate = candidate(
        id: "local-high",
        connectionID: "local-connection",
        modelID: "gpt-local",
        familyID: "gpt-local",
        profile: "high"
    )
    let apiHigh = candidate(
        id: "api-high",
        connectionID: "api-connection",
        modelID: "deepseek-r1",
        familyID: "deepseek-r1",
        profile: "high"
    )
    let apiLow = candidate(
        id: "api-low",
        connectionID: "api-connection",
        modelID: "deepseek-r1",
        familyID: "deepseek-r1",
        profile: "low"
    )
    let ingress = BridgeModelIngress(
        sources: [
            BridgeIngressSource(
                id: "local-source",
                kind: "codex",
                title: "Codex",
                description: "local",
                mode: "local",
                enabled: true
            ),
            BridgeIngressSource(
                id: "api-source",
                kind: "api",
                title: "API",
                description: "api",
                mode: "api",
                enabled: true
            ),
        ],
        connections: [
            connection(
                id: "local-connection",
                sourceID: "local-source",
                providerID: nil,
                candidates: [localCandidate]
            ),
            connection(
                id: "api-connection",
                sourceID: "api-source",
                providerID: "deepseek",
                candidates: [apiHigh, apiLow]
            ),
        ]
    )
    let scope = BridgeSettingsScanScopeProjection(
        regularCandidateIds: ["local-high", "api-high"],
        customCandidateIds: ["local-high", "api-high", "api-low"],
        sourceCount: 2,
        modelCount: 2,
        candidateCount: 2,
        blockedReasons: [
            BridgeSettingsBlockedReason(
                connectionId: "api-connection",
                sourceId: "api-source",
                reason: "api_connection_unverified",
                action: "test_connection",
                candidateIds: ["api-high"]
            )
        ]
    )
    let presentation = SettingsIngressPresenter.present(
        ingress: ingress,
        providerCatalog: [
            provider(id: "deepseek", featured: true, connectionSupported: true),
            provider(id: "blocked", featured: true, connectionSupported: false),
            provider(id: "other", featured: false, connectionSupported: true),
        ],
        scanScope: scope,
        candidateProjections: [
            candidateProjection(
                candidate: localCandidate,
                sourceID: "local-source",
                providerID: nil,
                displayModel: "GPT Local"
            ),
            candidateProjection(
                candidate: apiHigh,
                sourceID: "api-source",
                providerID: "deepseek",
                displayModel: "DeepSeek R1"
            ),
            candidateProjection(
                candidate: apiLow,
                sourceID: "api-source",
                providerID: "deepseek",
                displayModel: "DeepSeek R1"
            ),
        ],
        hasResumableRun: false,
        customProviderID: "custom"
    )

    expect(presentation.workspaceItems.count == 2, "source and connection join should be unique")
    expect(presentation.localWorkspaceItems.count == 1, "local workspaces should be projected")
    expect(presentation.apiWorkspaceItems.count == 1, "API workspaces should be projected")
    expect(
        presentation.connectableFeaturedProviders.map(\.providerId) == ["deepseek"],
        "unsupported featured providers should be filtered once"
    )
    expect(
        presentation.connectableOverflowProviders.map(\.providerId) == ["other"],
        "overflow providers should expose a ready-to-render section"
    )
    expect(presentation.endpointProviderOptions.last?.id == "custom", "custom endpoint stays last")
    expect(presentation.sourceCount == 2, "source count should be deduplicated")
    expect(presentation.connectionCount == 2, "connection count should be projected")
    expect(presentation.modelEntryCount == 2, "model entries should deduplicate profiles")
    expect(presentation.enabledCandidateCount == 2, "scan-scope counts should stay authoritative")
    expect(
        presentation.regularScanIsBlockedByEndpointVerification,
        "an enabled unverified endpoint should block a new regular run"
    )
    expect(
        presentation.regularModelFamilyGroups(
            for: ingress.connections[1]
        ).first?.candidates.map(\.id) == ["api-high"],
        "regular family groups should already be eligibility-filtered"
    )
    expect(
        presentation.modelFamilyGroups(for: ingress.connections[1]).count == 1,
        "candidate profiles in one family should be grouped once"
    )
    expect(
        presentation.customSourceSections.flatMap(\.workspaces).flatMap(\.candidates).count == 3,
        "custom sections should contain only eligible candidates"
    )
    expect(
        SettingsIngressPresenter.uniqueModelNames(in: [apiHigh, apiLow]) == ["deepseek-r1"],
        "model name deduplication should preserve order"
    )
    expect(
        presentation.providerConnectionCount(for: "deepseek") == 1,
        "provider connection counts should use candidate projection identity"
    )
}

private func verifyEvaluationProfileScopePresenter() {
    let added = EvaluationProfileScopePresenter.present(
        EvaluationProfileScopePresenter.Input(
            isProvisional: true,
            originalCandidateIDs: ["a", "b"],
            currentCandidateIDs: ["a", "b", "c"],
            upgradeProfileLabel: "完整评测"
        )
    )
    expect(added.requiresDecision, "changed provisional scope should require a decision")
    expect(added.delta?.addedCandidateIDs == ["c"], "added candidates should be explicit")
    expect(added.originalRoundUpgradeTitle == "只补全原 2 个", "original-round title is stable")
    expect(
        added.currentSelectionFullScanTitle == "补全当前 3 个为完整评测",
        "current selection title should include authoritative count"
    )
    expect(added.decisionMessage.contains("当前新增 1 个"), "reuse copy should describe an added model")

    let unchanged = EvaluationProfileScopePresenter.present(
        EvaluationProfileScopePresenter.Input(
            isProvisional: true,
            originalCandidateIDs: ["a"],
            currentCandidateIDs: ["a"],
            upgradeProfileLabel: nil
        )
    )
    expect(!unchanged.requiresDecision, "unchanged scope should upgrade directly")

    let empty = EvaluationProfileScopePresenter.present(
        EvaluationProfileScopePresenter.Input(
            isProvisional: true,
            originalCandidateIDs: ["a"],
            currentCandidateIDs: [],
            upgradeProfileLabel: nil
        )
    )
    expect(empty.decisionMessage.contains("当前没有启用模型"), "empty current scope needs explicit copy")
}

private func verifyRadarEntryPresenter() {
    let interruptedRepair = BridgeRunEntry(
        candidateId: "candidate",
        model: "gpt-5.6",
        effort: "high",
        label: "GPT",
        phase: "repair",
        status: "interrupted",
        finalStatus: nil,
        reasoningTokens: nil,
        attemptsCompleted: 1,
        attemptsPerTarget: 4,
        flags: [],
        errorMessage: nil
    )
    let runtime = RadarEntryPresenter.runtime(
        runEntry: interruptedRepair,
        currentPhase: "repair",
        questionContracts: [
            RadarPresenter.QuestionContractInput(id: "q1", scoreMax: 20)
        ],
        questionResults: []
    )
    expect(runtime.progressText == "重试 1/4", "repair progress should be labeled once")
    expect(runtime.progressFraction == 0.25, "progress should be clamped and normalized")
    expect(runtime.tone == .warning, "interrupted entries should expose semantic warning tone")
    expect(runtime.isInCurrentOperation, "matching repair phases should be current")

    let completed = RadarEntryPresenter.runtime(
        runEntry: nil,
        currentPhase: nil,
        questionContracts: [
            RadarPresenter.QuestionContractInput(id: "q1", scoreMax: 20),
            RadarPresenter.QuestionContractInput(id: "q2", scoreMax: 20),
        ],
        questionResults: [
            RadarPresenter.QuestionResultContractInput(id: "q1", semanticTotal: 20)
        ]
    )
    expect(completed.progressText == "已完成 1/2", "evidence-only progress should use question contracts")
    expect(completed.tone == .neutral, "completed evidence should use a neutral semantic tone")
}

private func recommendationDecision(
    currentID: String,
    candidateID: String?
) -> BridgeRecommendationDecisionV2 {
    BridgeRecommendationDecisionV2(
        currentModelConfigurationId: currentID,
        candidateModelConfigurationId: candidateID,
        comparisonCandidateModelConfigurationId: nil,
        comparisonCandidateReasons: nil,
        decision: "recommend",
        reason: "test",
        qualityTradeoff: false,
        qualityWarningQuestionIds: [],
        qualityGuard: nil,
        quality: BridgeRecommendationQualityV2(
            currentScore: 80,
            candidateScore: 85,
            scoreDelta: 5
        ),
        time: BridgeRecommendationTimeV2(
            currentSeconds: 10,
            candidateSeconds: 8,
            reductionPercent: 20
        ),
        referenceCost: BridgeRecommendationCostV2(
            currentUsd: 1,
            candidateUsd: 0.8,
            reductionPercent: 20
        ),
        primaryBenefit: nil
    )
}

private func leaderboardItem(
    id: String,
    isCurrent: Bool = false
) -> RadarLeaderboardItem {
    RadarLeaderboardItem(
        id: id,
        displayName: id.uppercased(),
        modelName: id,
        providerId: nil,
        effort: "high",
        score: 80,
        maxScore: 100,
        elapsedSeconds: 10,
        referenceCostUsd: 1,
        costCoverage: "complete",
        questionScores: [:],
        isCurrent: isCurrent,
        isRecommended: false
    )
}

private func verifyComparisonSelectionPresenter() {
    let items = [
        leaderboardItem(id: "current", isCurrent: true),
        leaderboardItem(id: "automatic"),
        leaderboardItem(id: "manual"),
    ]
    let representative = recommendationDecision(currentID: "current", candidateID: "automatic")
    let invalid = recommendationDecision(currentID: "missing", candidateID: "automatic")
    let selection = ComparisonSelectionPresenter.select(
        items: items,
        representativeDecision: representative,
        decisions: [invalid, representative],
        selectedCurrentConfigurationID: "missing",
        manualCandidateByCurrentConfigurationID: ["current": "manual"]
    )
    expect(selection.choices.count == 1, "decisions without both items should be filtered")
    expect(
        selection.decision?.currentModelConfigurationId == "current",
        "representative decision should be the fallback after invalid selection"
    )
    expect(selection.automaticCandidateItem?.id == "automatic", "automatic candidate should be assembled")
    expect(selection.candidateItem?.id == "manual", "valid manual selection should replace the candidate")
    expect(selection.isManualComparison, "manual candidate should be explicit")
    expect(
        selection.selectableManualCandidates.map(\.id) == ["manual"],
        "manual choices should exclude current and automatic candidates"
    )
    expect(selection.choiceLabel(for: representative) == "CURRENT", "choice labels should be projected")

    let otherSourceDecision = recommendationDecision(
        currentID: "other-current",
        candidateID: "automatic"
    )
    let sourceScopedSelection = ComparisonSelectionPresenter.select(
        items: items + [
            leaderboardItem(id: "other-current"),
            leaderboardItem(id: "official-manual"),
        ],
        representativeDecision: representative,
        decisions: [representative, otherSourceDecision],
        selectedCurrentConfigurationID: nil,
        manualCandidateByCurrentConfigurationID: [:],
        displaySource: "local_evaluation",
        sourceModeByConfigurationID: [
            "current": "local_evaluation",
            "automatic": "local_evaluation",
            "manual": "local_evaluation",
            "other-current": "official_snapshot",
            "official-manual": "official_snapshot",
        ]
    )
    expect(
        sourceScopedSelection.choices.map(\.currentModelConfigurationId) == ["current"],
        "comparison choices should stay within the displayed evidence source"
    )
    expect(
        sourceScopedSelection.selectableManualCandidates.map(\.id) == ["manual"],
        "manual candidates from another evidence source should be hidden"
    )

    let remoteSourceSelection = ComparisonSelectionPresenter.select(
        items: [
            leaderboardItem(id: "remote:current", isCurrent: true),
            leaderboardItem(id: "remote:automatic"),
            leaderboardItem(id: "remote:manual"),
        ],
        representativeDecision: representative,
        decisions: [representative],
        selectedCurrentConfigurationID: nil,
        manualCandidateByCurrentConfigurationID: ["current": "remote:manual"],
        itemIDByConfigurationID: [
            "current": "remote:current",
            "automatic": "remote:automatic",
            "manual": "remote:manual",
        ],
        displaySource: "local_evaluation",
        sourceModeByConfigurationID: [
            "current": "local_evaluation",
            "automatic": "local_evaluation",
            "manual": "official_snapshot",
        ]
    )
    expect(
        remoteSourceSelection.currentItem?.id == "remote:current"
            && remoteSourceSelection.candidateItem?.id == "remote:automatic",
        "source filtering should resolve remote item IDs back to local configurations"
    )
    expect(
        remoteSourceSelection.selectableManualCandidates.isEmpty,
        "mapped manual candidates from another evidence source should be hidden"
    )

    let remoteCurrent = leaderboardItem(id: "cloudflare:current", isCurrent: true)
    let remoteCandidate = leaderboardItem(id: "cloudflare:automatic")
    let remoteSelection = ComparisonSelectionPresenter.select(
        items: [remoteCurrent, remoteCandidate],
        representativeDecision: representative,
        decisions: [representative],
        selectedCurrentConfigurationID: nil,
        manualCandidateByCurrentConfigurationID: [:],
        itemIDByConfigurationID: [
            "current": "cloudflare:current",
            "automatic": "cloudflare:automatic",
        ]
    )
    expect(
        remoteSelection.currentItem?.id == "cloudflare:current",
        "comparison should resolve the local current identity to the remote row"
    )
    expect(
        remoteSelection.candidateItem?.id == "cloudflare:automatic",
        "comparison should resolve the local candidate identity to the remote row"
    )
    expect(
        remoteSelection.itemID(for: representative.currentModelConfigurationId)
            == "cloudflare:current",
        "comparison evidence should use the resolved remote current id"
    )
    expect(
        remoteSelection.itemID(for: representative.candidateModelConfigurationId)
            == "cloudflare:automatic",
        "comparison evidence should use the resolved remote candidate id"
    )

    let localDataset = ComparisonSelectionPresenter.dataset(
        usesLocalDataset: true,
        usesOfficialSnapshot: false,
        localStatistics: nil,
        localLeaderboard: [],
        localPairwiseComparisons: [],
        officialSnapshot: nil
    )
    expect(localDataset.showsLocalRepairControls, "local datasets should expose local repair controls")
    expect(localDataset.referenceSnapshot == nil, "local datasets must not leak official snapshot data")
}

private func activeSession(
    id: String,
    source: String,
    workspaceName: String,
    model: String?,
    effort: String?,
    threadName: String?
) -> BridgeDetectedModelSession {
    BridgeDetectedModelSession(
        id: id,
        source: source,
        workspaceName: workspaceName,
        model: model,
        effort: effort,
        threadName: threadName,
        isEvaluationSession: false
    )
}

private func verifyActiveSessionPresenter() {
    let namedSession = activeSession(
        id: "first",
        source: "codex",
        workspaceName: "ModelDial",
        model: "gpt-5.6",
        effort: "high",
        threadName: "  Repair scan state  "
    )
    let workspaceSession = activeSession(
        id: "second",
        source: "claude",
        workspaceName: "Website",
        model: nil,
        effort: nil,
        threadName: " \n "
    )
    let thirdSession = activeSession(
        id: "third",
        source: "grok",
        workspaceName: "Reference",
        model: "grok-code",
        effort: "medium",
        threadName: nil
    )
    let fourthSession = activeSession(
        id: "fourth",
        source: "codex",
        workspaceName: "Docs",
        model: "gpt-5.6",
        effort: "medium",
        threadName: "Release notes"
    )

    let namedPresentation = ActiveSessionPresenter.present(namedSession)
    let namedIdentity = ModelIdentityPresentation.displayLabel(
        model: "gpt-5.6",
        effort: "high"
    )
    expect(namedPresentation.title == "Repair scan state", "thread titles should be trimmed")
    expect(
        namedPresentation.context == "Codex · ModelDial · \(namedIdentity)",
        "session context should retain source, workspace, and model identity order"
    )
    expect(namedPresentation.identity == namedIdentity, "model identity should be projected once")

    let workspacePresentation = ActiveSessionPresenter.present(workspaceSession)
    expect(workspacePresentation.title == "Website", "blank thread names should use the workspace")
    expect(workspacePresentation.context == "Claude Code", "workspace fallback should not repeat itself")
    expect(workspacePresentation.identity == "Claude Code", "missing models should use the source identity")

    let overview = ActiveSessionPresenter.overview([
        namedSession,
        workspaceSession,
        thirdSession,
        fourthSession,
    ])
    expect(overview.totalCount == 4, "overview total count should remain authoritative")
    expect(
        overview.visibleSessions.map(\.id) == ["first", "second"],
        "overview truncation should preserve source order"
    )
    expect(overview.overflowCount == 2, "overview should report the hidden session count")
}

private func verifyProviderLogoPresentation() {
    expect(
        ModelIdentityPresentation.canonicalProviderID(for: "codex") == "openai",
        "Codex should resolve through its explicit OpenAI channel alias"
    )
    expect(
        ModelIdentityPresentation.providerLogoResourceName(for: "claude-code")
            == "anthropic-lobe",
        "Claude Code should resolve to the bundled Anthropic mark"
    )
    expect(
        ModelIdentityPresentation.providerLogoResourceName(for: "custom_endpoint") == nil,
        "custom endpoints should use the unknown-provider fallback"
    )
    expect(
        ModelIdentityPresentation.providerBrandID(
            providerID: "custom_endpoint",
            model: "deepseek-v4-flash"
        ) == "deepseek",
        "known model families should supply the brand behind a generic route"
    )
    expect(
        ModelIdentityPresentation.providerBrandID(
            providerID: "custom_endpoint",
            model: "private-model"
        ) == "custom_endpoint",
        "unknown models should preserve the generic-route fallback"
    )
    expect(
        ModelIdentityPresentation.providerMonogram(for: "custom_endpoint") == "CE",
        "unknown providers should retain a stable monogram"
    )
}

@main
private enum Phase2Gate4PresenterTestMain {
    static func main() {
        verifySettingsIngressPresenter()
        verifyEvaluationProfileScopePresenter()
        verifyRadarEntryPresenter()
        verifyComparisonSelectionPresenter()
        verifyActiveSessionPresenter()
        verifyProviderLogoPresentation()
        if failureCount > 0 {
            exit(1)
        }
        print("Phase 2 gate 4 presenter tests passed")
    }
}
