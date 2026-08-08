import Foundation

private var failureCount = 0

private func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() {
        failureCount += 1
        fputs("FAIL: \(message)\n", stderr)
    }
}

private func projection(
    recommendationStatus: String,
    recommendationReason: String,
    recommendationAction: String,
    completedStepCount: Int,
    baselineCandidateIds: [String] = [],
    operationalStatus: String = "operational",
    operationalReason: String = "ready",
    operationalAction: String = "none"
) -> BridgeSettingsConnectionProjection {
    BridgeSettingsConnectionProjection(
        connectionId: "connection-a",
        sourceId: "source-a",
        operationalStatus: operationalStatus,
        reason: operationalReason,
        action: operationalAction,
        enabledCandidateIds: ["candidate-a", "candidate-b"],
        availableCandidateIds: ["candidate-a", "candidate-b"],
        recommendationStatus: recommendationStatus,
        recommendationReason: recommendationReason,
        recommendationAction: recommendationAction,
        completedStepCount: completedStepCount,
        baselineCandidateIds: baselineCandidateIds
    )
}

private func verifyBackendStatesDrivePresentation() {
    let cases: [(String, IngressReadinessState, IngressReadinessAction, Int)] = [
        ("disabled", .disabled, .none, 0),
        ("needs_configuration", .needsConfiguration, .manageConnection, 0),
        ("needs_connection_test", .needsConnectionTest, .testConnection, 0),
        ("needs_model_selection", .needsModelSelection, .selectModels, 1),
        ("needs_baseline", .needsBaseline, .scanBaseline, 2),
        ("ready", .ready, .none, 4),
    ]
    let actionCodes = [
        "enable_source",
        "configure_connection",
        "test_connection",
        "enable_candidate",
        "scan_baseline",
        "none",
    ]

    for (index, item) in cases.enumerated() {
        let presented = IngressReadiness.present(
            projection(
                recommendationStatus: item.0,
                recommendationReason: item.0 == "needs_baseline" ? "no_valid_baseline" : "ready",
                recommendationAction: actionCodes[index],
                completedStepCount: item.3,
                baselineCandidateIds: item.0 == "ready" ? ["candidate-a"] : []
            )
        )
        expect(presented.state == item.1, "\(item.0) should use backend recommendation state")
        expect(presented.action == item.2, "\(item.0) should map backend recommendation action")
        expect(presented.completedStepCount == item.3, "\(item.0) should preserve backend completed steps")
    }
}

private func verifyBackendEvidenceFactsPassThrough() {
    let presented = IngressReadiness.present(
        projection(
            recommendationStatus: "ready",
            recommendationReason: "ready",
            recommendationAction: "none",
            completedStepCount: 4,
            baselineCandidateIds: ["candidate-b"],
            operationalStatus: "blocked",
            operationalReason: "connection_unavailable",
            operationalAction: "inspect_connection"
        )
    )
    expect(presented.state == .ready, "presenter must not recompute readiness from operational inputs")
    expect(
        presented.enabledCandidateIDs == ["candidate-a", "candidate-b"],
        "presenter should preserve backend enabled candidate ids"
    )
    expect(
        presented.baselineCandidateIDs == ["candidate-b"],
        "presenter should preserve backend baseline candidate ids"
    )
}

private func verifyReasonCodesOnlyChoosePresentationCopy() {
    let noCandidates = IngressReadiness.present(
        projection(
            recommendationStatus: "needs_model_selection",
            recommendationReason: "no_candidates_configured",
            recommendationAction: "add_candidate",
            completedStepCount: 1
        )
    )
    let noEnabledCandidate = IngressReadiness.present(
        projection(
            recommendationStatus: "needs_model_selection",
            recommendationReason: "no_enabled_candidates",
            recommendationAction: "enable_candidate",
            completedStepCount: 1
        )
    )
    expect(noCandidates.state == .needsModelSelection, "reason must not redefine backend state")
    expect(noEnabledCandidate.state == .needsModelSelection, "reason must not redefine backend state")
    expect(noCandidates.detail != noEnabledCandidate.detail, "reason may select presentation copy")
}

@main
private enum IngressReadinessTestMain {
    static func main() {
        verifyBackendStatesDrivePresentation()
        verifyBackendEvidenceFactsPassThrough()
        verifyReasonCodesOnlyChoosePresentationCopy()
        if failureCount > 0 {
            exit(1)
        }
        print("Ingress readiness presenter tests passed")
    }
}
