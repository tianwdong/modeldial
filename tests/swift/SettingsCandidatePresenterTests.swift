import Foundation

private var failureCount = 0

private func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() {
        failureCount += 1
        fputs("FAIL: \(message)\n", stderr)
    }
}

private func candidate(
    id: String = "candidate-1",
    modelID: String = "legacy-model-high"
) -> BridgeIngressModelCandidate {
    BridgeIngressModelCandidate(
        id: id,
        connectionId: "connection-1",
        modelId: modelID,
        displayName: "Legacy local label",
        familyId: "stale-local-family",
        variantId: "stale-local-variant",
        enabled: true,
        scanProfile: "stale-local-profile",
        capabilities: []
    )
}

private func projection(
    candidateID: String = "candidate-1",
    providerID: String? = "openai",
    displayScanProfile: String = "high"
) -> BridgeSettingsCandidateProjection {
    BridgeSettingsCandidateProjection(
        candidateId: candidateID,
        sourceId: "custom_endpoint",
        connectionId: "connection-1",
        providerId: providerID,
        familyId: "gpt-5.6",
        variantId: "high",
        modelId: "legacy-model-high",
        displayModel: "gpt-5.6",
        scanProfile: "default",
        displayScanProfile: displayScanProfile,
        enabled: true,
        available: true
    )
}

private func evidence(
    questionCount: Int = 5,
    questionCompleted: Int = 5,
    scoreText: String = "4/5",
    latestValidAt: String? = nil,
    isCurrentPackComparable: Bool = true,
    isUsingPreviousValidResult: Bool = false
) -> BridgeEvidenceCard {
    var payload: [String: Any] = [
        "id": "candidate-1",
        "label": "Candidate 1",
        "model": "gpt-5.6",
        "effort": "high",
        "recentCount": 1,
        "questionCount": questionCount,
        "correctCount": 4,
        "questionAttempted": questionCount,
        "questionCompleted": questionCompleted,
        "scoreText": scoreText,
        "hits516": 0,
        "hitRate516": 0,
        "passRate": 80,
        "avgReasoningTokens": 1200,
        "isCurrentPackComparable": isCurrentPackComparable,
        "isUsingPreviousValidResult": isUsingPreviousValidResult,
    ]
    if let latestValidAt {
        payload["latestValidAt"] = latestValidAt
    }
    let data = try! JSONSerialization.data(withJSONObject: payload)
    return try! JSONDecoder().decode(BridgeEvidenceCard.self, from: data)
}

private func verifyProjectionWins() {
    let presented = SettingsCandidatePresenter.presentation(
        for: candidate(),
        projection: projection()
    )
    expect(presented.providerID == "openai", "provider should come from projection")
    expect(presented.familyID == "gpt-5.6", "family should come from projection")
    expect(presented.variantID == "high", "variant should come from projection")
    expect(presented.displayModel == "gpt-5.6", "display model should come from projection")
    expect(presented.displayScanProfile == "high", "display profile should come from projection")
    expect(presented.displayName == "gpt-5.6 [high]", "display name should use projected identity")
}

private func verifyMissingProjectionUsesRawModelOnly() {
    let presented = SettingsCandidatePresenter.presentation(
        for: candidate(),
        projection: nil
    )
    expect(presented.providerID == nil, "missing projection should not infer provider")
    expect(presented.familyID == "legacy-model-high", "raw model should be the only family fallback")
    expect(presented.variantID == nil, "missing projection should not use local variant metadata")
    expect(presented.displayModel == "legacy-model-high", "missing projection should show raw model")
    expect(presented.displayScanProfile == nil, "missing projection should not infer a profile")
    expect(presented.displayName == "legacy-model-high", "fallback label should stay semantic-free")
    expect(presented.variantName == "默认档位", "missing projected variant should stay explicit")
}

private func verifyProviderSelectionOrder() {
    let first = candidate(id: "candidate-1")
    let second = candidate(id: "candidate-2", modelID: "another-model")
    let projectedSecond = projection(candidateID: "candidate-2", providerID: "moonshot")
    expect(
        SettingsCandidatePresenter.providerID(
            for: [first, second],
            projectionsByCandidateID: ["candidate-2": projectedSecond],
            fallbackProviderID: "explicit-fallback"
        ) == "moonshot",
        "candidate projection should win over connection fallback"
    )
    expect(
        SettingsCandidatePresenter.providerID(
            for: [first],
            projectionsByCandidateID: [:],
            fallbackProviderID: "explicit-fallback"
        ) == "explicit-fallback",
        "explicit connection provider should be the only fallback"
    )
}

private func verifyEvidencePresentation() {
    let missing = SettingsCandidatePresenter.evidencePresentation(for: nil)
    expect(missing.text == "暂无有效成绩", "missing evidence should not claim a score")
    expect(missing.tone == .muted, "missing evidence should use the muted tone")

    let emptyOutdated = SettingsCandidatePresenter.evidencePresentation(
        for: evidence(
            questionCount: 0,
            questionCompleted: 0,
            isCurrentPackComparable: false
        )
    )
    expect(emptyOutdated.text == "暂无有效成绩", "empty evidence should not claim a pack update")
    expect(emptyOutdated.tone == .muted, "empty evidence should use the muted tone")

    let retained = SettingsCandidatePresenter.evidencePresentation(
        for: evidence(scoreText: "3/5", isUsingPreviousValidResult: true)
    )
    expect(retained.text == "本次重扫失败 · 保留 3/5", "failed rescans should retain the valid score text")
    expect(retained.tone == .warning, "failed rescans should use the warning tone")

    let outdated = SettingsCandidatePresenter.evidencePresentation(
        for: evidence(isCurrentPackComparable: false)
    )
    expect(outdated.text == "题包已更新 · 需要重扫", "outdated packs should request a rescan")
    expect(outdated.tone == .muted, "outdated packs should use the muted tone")

    let dated = SettingsCandidatePresenter.evidencePresentation(
        for: evidence(
            scoreText: "4/5",
            latestValidAt: "2026-07-29T08:15:47+08:00"
        )
    )
    expect(dated.text == "有效成绩 4/5 · 2026-07-29 08:15", "valid evidence should include a stable display time")
    expect(dated.tone == .accent, "valid evidence should use the accent tone")

    let undated = SettingsCandidatePresenter.evidencePresentation(
        for: evidence(scoreText: "5/5")
    )
    expect(undated.text == "有效成绩 5/5", "valid undated evidence should preserve the score text")
    expect(undated.tone == .accent, "valid undated evidence should use the accent tone")
}

@main
private enum SettingsCandidatePresenterTestMain {
    static func main() {
        verifyProjectionWins()
        verifyMissingProjectionUsesRawModelOnly()
        verifyProviderSelectionOrder()
        verifyEvidencePresentation()
        if failureCount > 0 {
            exit(1)
        }
        print("Settings candidate presenter tests passed")
    }
}
