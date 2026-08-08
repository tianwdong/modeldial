import Foundation

private var failureCount = 0

private func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() {
        failureCount += 1
        fputs("FAIL: \(message)\n", stderr)
    }
}

private func fixtureObject(_ name: String) throws -> [String: Any] {
    let url = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        .appendingPathComponent("tests/fixtures")
        .appendingPathComponent(name)
    return try JSONSerialization.jsonObject(
        with: Data(contentsOf: url)
    ) as! [String: Any]
}

private func decode<T: Decodable>(_ type: T.Type, _ payload: Any) throws -> T {
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase
    return try decoder.decode(
        type,
        from: JSONSerialization.data(withJSONObject: payload)
    )
}

private func expectDecodeFailure<T: Decodable>(
    _ type: T.Type,
    _ payload: Any,
    _ message: String
) {
    do {
        _ = try decode(type, payload)
        expect(false, message)
    } catch {
        expect(true, message)
    }
}

private func completeRunMetadata() -> [String: Any] {
    [
        "run_id": "run-fixture",
        "question_pack_id": "coding-fast",
        "question_pack_version": "v1",
        "started_at": "2026-07-29T10:00:00+08:00",
        "completed_at": "2026-07-29T10:01:00+08:00",
        "candidate_count": 1,
        "question_count": 1,
        "status": "completed",
        "selection_mode": "regular",
        "requested_candidate_ids": ["candidate-a"],
        "regular_candidate_ids": ["candidate-a"],
        "comparison_group_id": "group-a",
        "comparison_group_mode": "regular",
        "appended_candidate_ids": [],
        "skipped_candidate_ids": [],
        "aggregate_wall_clock_seconds": 60,
        "evaluation_profile_id": "quick",
        "evaluation_profile_label": "快速对比",
        "evaluation_result_level": "provisional",
        "evaluation_score_max": 20,
        "question_ids": ["01_session_bundle_repair"],
        "upgrade_from_run_id": NSNull(),
        "upgrade_target_profile_id": "full",
    ]
}

private func completeQuestionResult() -> [String: Any] {
    [
        "question_id": "01_session_bundle_repair",
        "question_title": "Backend Question",
        "capability_id": "backend-capability",
        "capability_label": "Backend Capability",
        "detail_label": "Backend Detail",
        "phase": "scan",
        "status": "pass",
        "expected_summary": "expected",
        "actual_summary": "actual",
        "answer_preview": "preview",
        "scorer_reason": "reason",
        "semantic_score": 20,
        "semantic_total": 20,
        "score_details": [],
        "failure_summary": "",
        "latency_s": 1.0,
        "input_tokens": 1,
        "cached_input_tokens": NSNull(),
        "cache_write_input_tokens": NSNull(),
        "output_tokens": 1,
        "reasoning_tokens": 1,
    ]
}

private func verifySnapshotRejectsLegacyIngressFallback() throws {
    let snapshot = try fixtureObject("architecture_app_snapshot_v2.json")
    let decoded = try decode(BridgeSnapshot.self, snapshot)
    let ingress = (snapshot["config"] as! [String: Any])["model_ingress"] as! [String: Any]
    let sourceCount = (ingress["sources"] as! [[String: Any]]).count
    expect(
        decoded.config.modelIngress.sources.count == sourceCount,
        "V2 model_ingress should decode without synthesized targets"
    )
    expect(
        decoded.settingsProjection.schemaVersion == 1,
        "AppSnapshotV2 should decode SettingsProjectionV1"
    )
    let readiness = decoded.settingsProjection.connections.first
    expect(readiness?.recommendationStatus == "ready", "settings connection should require recommendation status")
    expect(readiness?.recommendationReason == "ready", "settings connection should require recommendation reason")
    expect(readiness?.recommendationAction == "none", "settings connection should require recommendation action")
    expect(readiness?.completedStepCount == 4, "settings connection should require completed step count")
    expect(
        readiness?.baselineCandidateIds == ["fixture-candidate"],
        "settings connection should require baseline candidate ids"
    )
    let scanScope = decoded.settingsProjection.scanScope
    expect(
        scanScope.regularCandidateIds == ["fixture-candidate"],
        "settings projection should decode the authoritative regular scan scope"
    )
    expect(
        scanScope.customCandidateIds == ["fixture-candidate"],
        "settings projection should decode the authoritative custom scan scope"
    )
    expect(scanScope.sourceCount == 1, "settings projection should decode source count")
    expect(scanScope.modelCount == 1, "settings projection should decode model count")
    expect(scanScope.candidateCount == 1, "settings projection should decode candidate count")
    let projectedCandidate = decoded.settingsProjection.candidates.first
    expect(projectedCandidate?.candidateId == "fixture-candidate", "settings projection should decode candidates")
    expect(projectedCandidate?.enabled == true, "settings projection should decode enabled state")
    expect(projectedCandidate?.available == true, "settings projection should decode availability")
    expect(decoded.questionPack.questions.count == 1, "V2 question definitions should decode")

    var legacySnapshot = snapshot
    var config = legacySnapshot["config"] as! [String: Any]
    config.removeValue(forKey: "model_ingress")
    config["targets"] = [
        ["model": "gpt-fixture", "effort": "high", "enabled": true]
    ]
    legacySnapshot["config"] = config
    expectDecodeFailure(
        BridgeSnapshot.self,
        legacySnapshot,
        "Swift must not synthesize model_ingress from config.targets"
    )

    for field in [
        "config",
        "dashboard",
        "runtime",
        "question_pack",
        "settings_projection",
        "advisor_v2_evidence",
        "recommendation_portfolio_v2",
        "reference_snapshot_feed",
        "recommendation_use",
    ] {
        var missingField = snapshot
        missingField.removeValue(forKey: field)
        expectDecodeFailure(
            BridgeSnapshot.self,
            missingField,
            "AppSnapshotV2 must include \(field)"
        )

        var nullField = snapshot
        nullField[field] = NSNull()
        expectDecodeFailure(
            BridgeSnapshot.self,
            nullField,
            "AppSnapshotV2 must reject null \(field)"
        )
    }

    var unsupportedSettingsProjection = snapshot
    var settingsProjection = unsupportedSettingsProjection["settings_projection"] as! [String: Any]
    settingsProjection["schema_version"] = 2
    unsupportedSettingsProjection["settings_projection"] = settingsProjection
    expectDecodeFailure(
        BridgeSnapshot.self,
        unsupportedSettingsProjection,
        "Swift must reject an unsupported settings projection schema"
    )

    for field in [
        "recommendation_status",
        "recommendation_reason",
        "recommendation_action",
        "completed_step_count",
        "baseline_candidate_ids",
    ] {
        var incompleteSnapshot = snapshot
        var incompleteProjection = incompleteSnapshot["settings_projection"] as! [String: Any]
        var connections = incompleteProjection["connections"] as! [[String: Any]]
        connections[0].removeValue(forKey: field)
        incompleteProjection["connections"] = connections
        incompleteSnapshot["settings_projection"] = incompleteProjection
        expectDecodeFailure(
            BridgeSnapshot.self,
            incompleteSnapshot,
            "SettingsProjectionV1 connection must require \(field)"
        )
    }
}

private func verifyDashboardLegacySummaryFieldsAreOptional() throws {
    var snapshot = try fixtureObject("architecture_app_snapshot_v2.json")
    var dashboard = snapshot["dashboard"] as! [String: Any]
    for field in [
        "run_count",
        "hits_516",
        "hit_rate_516",
        "pass_rate",
        "reasoning_tokens_total",
        "budget_summary",
    ] {
        dashboard.removeValue(forKey: field)
    }
    snapshot["dashboard"] = dashboard

    let decoded = try decode(BridgeSnapshot.self, snapshot)
    expect(
        decoded.dashboard.runMetadata.runId == "fixture-dashboard-run",
        "AppSnapshotV2 should decode without legacy dashboard summary fields"
    )
}

private func verifyQuestionPackRequiresBackendSemantics() throws {
    var snapshot = try fixtureObject("architecture_app_snapshot_v2.json")
    var questionPack = snapshot["question_pack"] as! [String: Any]
    questionPack.removeValue(forKey: "questions")
    snapshot["question_pack"] = questionPack
    expectDecodeFailure(
        BridgeSnapshot.self,
        snapshot,
        "V2 question_pack must include backend question semantics"
    )
}

private func verifyRunMetadataHasNoLegacyDefault() throws {
    let metadata = try decode(BridgeRunMetadata.self, completeRunMetadata())
    expect(metadata.runId == "run-fixture", "complete run metadata should decode")

    var incomplete = completeRunMetadata()
    incomplete.removeValue(forKey: "run_id")
    expectDecodeFailure(
        BridgeRunMetadata.self,
        incomplete,
        "run metadata without run_id must not become a legacy default"
    )
}

private func verifyQuestionResultUsesBackendSemantics() throws {
    let result = try decode(BridgeQuestionResult.self, completeQuestionResult())
    expect(
        result.semanticDisplayName == "Backend Capability",
        "question display name must use backend capability_label"
    )
    expect(
        result.semanticDescription == "Backend Detail",
        "question description must use backend detail_label"
    )

    var incomplete = completeQuestionResult()
    incomplete.removeValue(forKey: "capability_label")
    expectDecodeFailure(
        BridgeQuestionResult.self,
        incomplete,
        "question result must not invent a capability label"
    )
}

private func verifyRuntimeRejectsLegacyLifecycleAndPhase() throws {
    let snapshot = try fixtureObject("architecture_app_snapshot_v2.json")
    let decoded = try decode(BridgeSnapshot.self, snapshot)
    expect(decoded.runtime.lifecycleState == .idle, "current lifecycle should decode")

    var legacyLifecycle = snapshot
    var lifecycleRuntime = legacyLifecycle["runtime"] as! [String: Any]
    lifecycleRuntime["lifecycle_state"] = "active_quick"
    legacyLifecycle["runtime"] = lifecycleRuntime
    expectDecodeFailure(
        BridgeSnapshot.self,
        legacyLifecycle,
        "Swift must reject legacy lifecycle aliases"
    )

    var legacyPhase = snapshot
    var phaseRuntime = legacyPhase["runtime"] as! [String: Any]
    phaseRuntime["current_phase"] = "review"
    legacyPhase["runtime"] = phaseRuntime
    expectDecodeFailure(
        BridgeSnapshot.self,
        legacyPhase,
        "Swift must reject legacy phase aliases"
    )
}

@main
private enum WireDTOCompatibilityTestMain {
    static func main() {
        do {
            try verifySnapshotRejectsLegacyIngressFallback()
            try verifyDashboardLegacySummaryFieldsAreOptional()
            try verifyQuestionPackRequiresBackendSemantics()
            try verifyRunMetadataHasNoLegacyDefault()
            try verifyQuestionResultUsesBackendSemantics()
            try verifyRuntimeRejectsLegacyLifecycleAndPhase()
        } catch {
            failureCount += 1
            fputs("FAIL: \(error)\n", stderr)
        }
        if failureCount > 0 {
            exit(1)
        }
        print("Wire DTO compatibility tests passed")
    }
}
