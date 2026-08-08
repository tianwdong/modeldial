import Foundation

private var failureCount = 0

private func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() {
        failureCount += 1
        fputs("FAIL: \(message)\n", stderr)
    }
}

private func arguments(
    _ patch: SettingsConfigPatch,
    operation expectedOperation: String
) -> [String: Any] {
    let payload = patch.commandPayload
    expect(payload["schema_version"] as? Int == 1, "command should use schema v1")
    expect(
        payload["operation"] as? String == expectedOperation,
        "command should encode \(expectedOperation)"
    )
    expect(JSONSerialization.isValidJSONObject(payload), "command should be valid JSON")
    return payload["arguments"] as? [String: Any] ?? [:]
}

private func verifyEndpointCommands() {
    let delete = arguments(
        .deleteConnection(connectionID: "endpoint-a"),
        operation: "delete_connection"
    )
    expect(delete["connection_id"] as? String == "endpoint-a", "delete should encode connection identity")

    let remove = arguments(
        .removeModelCandidates(
            connectionID: "endpoint-a",
            candidateIDs: ["candidate-a", "candidate-b"]
        ),
        operation: "remove_model_candidates"
    )
    expect(remove["candidate_ids"] as? [String] == ["candidate-a", "candidate-b"], "remove should encode candidate identities")

    let references = arguments(
        .connectionSecretReferences(["endpoint-a": "keychain:test:migrated"]),
        operation: "connection_secret_references"
    )
    expect(
        references["references_by_connection_id"] as? [String: String]
            == ["endpoint-a": "keychain:test:migrated"],
        "secret migration should encode references by connection"
    )
}

private func verifyIngressCommands() {
    let enabled = arguments(
        .modelCandidatesEnabled(
            connectionID: "endpoint-a",
            candidateIDs: ["candidate-a"],
            enabled: true
        ),
        operation: "model_candidates_enabled"
    )
    expect(enabled["candidate_ids"] as? [String] == ["candidate-a"], "candidate toggle should encode scope")
    expect(enabled["enabled"] as? Bool == true, "candidate toggle should encode enabled state")

    let connection = arguments(
        .connectionEnabled(connectionID: "endpoint-a", enabled: false),
        operation: "connection_enabled"
    )
    expect(connection["enabled"] as? Bool == false, "connection toggle should encode enabled state")

    let discovered = arguments(
        .addDiscoveredLocalCandidate(
            connectionID: "local-a",
            modelID: "model-local",
            displayName: "Model Local",
            scanProfile: "high"
        ),
        operation: "add_discovered_local_candidate"
    )
    expect(discovered["model_id"] as? String == "model-local", "local candidate should encode model identity")
    expect(discovered["scan_profile"] as? String == "high", "local candidate should encode scan profile")
}

private func verifyRecommendationAndSchedulerCommands() {
    let current = arguments(
        .currentDefault(candidateID: "candidate-a"),
        operation: "current_default"
    )
    expect(current["candidate_id"] as? String == "candidate-a", "current default should encode candidate identity")
    let cleared = arguments(
        .currentDefault(candidateID: nil),
        operation: "current_default"
    )
    expect(cleared["candidate_id"] is NSNull, "cleared current default should encode null")

    _ = arguments(.automaticCurrentModel, operation: "automatic_current_model")
    let recommendation = arguments(
        .recommendationPreference(.quality),
        operation: "recommendation_preference"
    )
    expect(
        recommendation["preference"] as? String == "quality",
        "recommendation preference should preserve its typed value"
    )
    let sourceMode = arguments(
        .sourceMode(.localEvaluation, configurationID: "candidate-a"),
        operation: "source_mode"
    )
    expect(
        sourceMode["source_mode"] as? String == "local_evaluation",
        "source mode should preserve its typed value"
    )
    expect(
        sourceMode["configuration_id"] as? String == "candidate-a",
        "source mode should preserve its configuration identity"
    )
    _ = arguments(
        .projectTaskProfile(name: "Project", taskMode: "测试验证"),
        operation: "project_task_profile"
    )
    _ = arguments(
        .scanBudget(enabled: true, maxDurationSeconds: 30, maxReferenceCostUsd: 0),
        operation: "scan_budget"
    )
    _ = arguments(
        .scanExecution(
            maxConcurrentTargets: 2,
            executionTimeoutSeconds: 600,
            timeoutRetryCount: 1
        ),
        operation: "scan_execution"
    )
    _ = arguments(.scheduler(mode: .interval, intervalSeconds: 1800), operation: "scheduler")
    _ = arguments(.schedulerEnabled(true), operation: "scheduler_enabled")
    _ = arguments(.schedulerMode(.weekly), operation: "scheduler_mode")
    _ = arguments(.dailySchedule(hour: 7, minute: 45), operation: "daily_schedule")

    let weekly = arguments(
        .weeklySchedule(weekday: 5, hour: 18, minute: 30),
        operation: "weekly_schedule"
    )
    expect(weekly["weekday"] as? Int == 5, "weekly command should encode weekday")
    expect(weekly["hour"] as? Int == 18, "weekly command should encode hour")
    expect(weekly["minute"] as? Int == 30, "weekly command should encode minute")
    _ = arguments(
        .scheduledEvaluationProfile("quick"),
        operation: "scheduled_evaluation_profile"
    )
}

@main
private enum SettingsConfigPatchTestMain {
    static func main() {
        verifyEndpointCommands()
        verifyIngressCommands()
        verifyRecommendationAndSchedulerCommands()
        if failureCount > 0 {
            exit(1)
        }
        print("SettingsConfigPatch tests passed")
    }
}
