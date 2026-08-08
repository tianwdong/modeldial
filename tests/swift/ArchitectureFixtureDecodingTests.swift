import Foundation

private var failureCount = 0

private func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() {
        failureCount += 1
        fputs("FAIL: \(message)\n", stderr)
    }
}

private func fixtureData(_ name: String) throws -> Data {
    let url = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        .appendingPathComponent("tests/fixtures/\(name)")
    return try Data(contentsOf: url)
}

private func decoder() -> JSONDecoder {
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase
    return decoder
}

private func verifySnapshotFixtures() throws {
    let appSnapshot = try decoder().decode(
        BridgeSnapshot.self,
        from: fixtureData("architecture_app_snapshot_v2.json")
    )
    expect(appSnapshot.schemaVersion == 2, "app snapshot should decode AppSnapshotV2")
    expect(appSnapshot.runtime.lifecycleState == .idle, "app snapshot should decode idle runtime")
    expect(appSnapshot.questionPack.version == "fixture-v1", "app snapshot should decode question pack")
    expect(appSnapshot.recommendationUse.schemaVersion == 1, "app snapshot should decode recommendation use")
    let trend = appSnapshot.dashboard.statistics?.trendSeries.first
    expect(trend?.candidateId == "fixture-candidate", "statistics should decode candidate identity")
    expect(trend?.overallScoreRunIndices == [0, 2], "statistics should decode sparse run slots")
    expect(trend?.overallScoreValues == [80, 90], "statistics should decode overall scores")
    let readiness = appSnapshot.settingsProjection.connections.first
    expect(readiness?.recommendationStatus == "ready", "app snapshot should decode recommendation readiness")
    expect(readiness?.recommendationReason == "ready", "app snapshot should decode recommendation reason")
    expect(readiness?.recommendationAction == "none", "app snapshot should decode recommendation action")
    expect(readiness?.completedStepCount == 4, "app snapshot should decode completed readiness steps")
    expect(
        readiness?.baselineCandidateIds == ["fixture-candidate"],
        "app snapshot should decode baseline candidate ids"
    )

    let refreshSnapshot = try decoder().decode(
        BridgeRefreshSnapshot.self,
        from: fixtureData("architecture_refresh_snapshot_v1.json")
    )
    expect(refreshSnapshot.schemaVersion == 1, "refresh snapshot should decode RefreshSnapshotV1")
    expect(refreshSnapshot.runtime.lifecycleState == .idle, "refresh snapshot should decode idle runtime")
    expect(refreshSnapshot.questionPack?.questionCount == 1, "refresh snapshot should decode question count")

    var unsupportedAppPayload = try fixtureObject("architecture_app_snapshot_v2.json")
    unsupportedAppPayload["schema_version"] = 3
    do {
        _ = try decoder().decode(
            BridgeSnapshot.self,
            from: JSONSerialization.data(withJSONObject: unsupportedAppPayload)
        )
        expect(false, "unsupported app snapshot version should fail")
    } catch {
        expect(true, "unsupported app snapshot version should fail")
    }
}

private func fixtureObject(_ name: String) throws -> [String: Any] {
    guard let object = try JSONSerialization.jsonObject(
        with: fixtureData(name)
    ) as? [String: Any] else {
        throw NSError(domain: "ArchitectureFixtureDecodingTests", code: 7)
    }
    return object
}

private func decodeSnapshotPayload(
    _ payload: [String: Any]
) throws -> BridgeSnapshot {
    try decoder().decode(
        BridgeSnapshot.self,
        from: JSONSerialization.data(withJSONObject: payload)
    )
}

private func expectSnapshotDecodeFailure(
    _ payload: [String: Any],
    _ message: String
) {
    do {
        _ = try decodeSnapshotPayload(payload)
        expect(false, message)
    } catch {
        expect(true, message)
    }
}

private func extendedSnapshotPayload() throws -> [String: Any] {
    var payload = try fixtureObject("architecture_app_snapshot_v2.json")
    let epoch: [String: Any] = [
        "schema_version": 1,
        "use_epoch_id": "epoch-fixture",
        "recommendation_id": "recommendation-fixture",
        "current_model_configuration_id": "current-fixture",
        "recommended_model_configuration_id": "candidate-fixture",
        "resolved_data_source": "local_evaluation",
        "evaluation_snapshot_id": "evaluation-fixture",
        "pricing_snapshot_id": "pricing-fixture",
        "started_at": "2026-07-29T10:00:00Z",
        "ended_at": "2026-07-29T11:00:00Z",
        "end_reason": "completed",
        "observed_candidate_session_count": 2,
        "observed_candidate_work_unit_count": 3,
        "observed_candidate_reference_cost_usd": 0.25,
        "observed_candidate_response_wait_ms": 1200,
        "estimated_reference_cost_delta_usd": -0.1,
        "estimated_model_wait_delta_ms": -300,
        "lifecycle_status": "completed",
        "estimate_status": "ready",
        "estimate_basis": "fixture",
        "attribution_route_basis": "same-route",
    ]
    payload["recommendation_use"] = [
        "schema_version": 1,
        "epochs": [epoch],
        "representative_epoch": epoch,
        "benefit_summary": NSNull(),
        "value_summary": NSNull(),
    ]

    let decision: [String: Any] = [
        "current_model_configuration_id": "current-fixture",
        "candidate_model_configuration_id": "candidate-fixture",
        "comparison_candidate_model_configuration_id": "comparison-fixture",
        "comparison_candidate_reasons": ["best-compatible-evidence"],
        "decision": "recommend",
        "reason": "fixture",
        "quality_tradeoff": false,
        "quality_warning_question_ids": [],
        "quality_guard": [
            "schema_version": 1,
            "status": "passed",
            "rule": "no-quality-regression",
            "preference": "smart",
            "decision": "recommend",
            "threshold_points": 5,
            "score_delta_points": 2,
            "passed": true,
        ],
        "quality": [
            "current_score": 80,
            "candidate_score": 82,
            "score_delta": 2,
        ],
        "time": [
            "current_seconds": 20,
            "candidate_seconds": 10,
            "reduction_percent": 50,
        ],
        "reference_cost": [
            "current_usd": 0.2,
            "candidate_usd": 0.1,
            "reduction_percent": 50,
        ],
        "primary_benefit": NSNull(),
    ]
    var portfolio = payload["recommendation_portfolio_v2"] as! [String: Any]
    portfolio["representative_configuration_id"] = "current-fixture"
    portfolio["decisions"] = [decision]
    payload["recommendation_portfolio_v2"] = portfolio

    let usage: [String: Any] = [
        "input_tokens": 101,
        "cached_input_tokens": 102,
        "cache_write_input_tokens": 103,
        "output_tokens": 104,
        "reasoning_tokens": 105,
    ]
    let entry: [String: Any] = [
        "model_configuration_id": "candidate-fixture",
        "model_configuration": [
            "provider_id": "fixture",
            "raw_model_id": "model-fixture",
            "canonical_model_id": "model-fixture",
            "display_name": "Model Fixture",
            "reasoning_effort": "high",
            "service_tier": "default",
            "route_type": "official",
        ],
        "advisor_eligible": true,
        "score": 18,
        "max_score": 20,
        "elapsed_ms": 1234,
        "estimated_api_cost_usd": 0.1,
        "cost_coverage": "complete",
        "question_scores": ["fixture-question": 18],
        "completed_at": "2026-07-29T11:00:00Z",
        "failure_count": 2,
        "hard_failure_count": 1,
        "route_fingerprint": "route-fixture",
        "usage": usage,
    ]
    let leaderboardProjection: [String: Any] = [
        "schema_version": 1,
        "source": "publisher",
        "ranking_rule": "score-desc",
        "trend_rule": "compatible-only",
        "questions": [],
        "rows": [[
            "model_configuration_id": "candidate-fixture",
            "rank": 1,
            "target_labels": [],
            "question_scores": [[
                "question_id": "fixture-question",
                "score": 18,
            ]],
            "trend": [
                "compatibility_key": "fixture-v1:grader-v1",
                "sample_count": 1,
                "comparable": true,
                "stable_ranking_eligible": true,
                "points": [],
            ],
        ]],
    ]
    let referenceSnapshot: [String: Any] = [
        "schema_version": 1,
        "kind": "first_party",
        "batch_id": "batch-fixture",
        // The runner may start at the previous UTC slot, but the App contract
        // must expose the subsequent public publication time.
        "started_at": "2026-08-05T23:00:00Z",
        "published_at": "2026-08-06T00:00:00Z",
        "question_pack_version": "fixture-v1",
        "grader_version": "grader-v1",
        "pricing_snapshot_id": "pricing-fixture",
        "entry_count": 1,
        "entries": [entry],
        "leaderboard_projection": leaderboardProjection,
    ]
    payload["reference_snapshot_feed"] = [
        "schema_version": 1,
        "status": "ready",
        "kind": "first_party",
        "latest": referenceSnapshot,
        "snapshots": [referenceSnapshot],
        "delivery": NSNull(),
    ]
    return payload
}

private func verifyExtendedSnapshotDTOContracts() throws {
    let payload = try extendedSnapshotPayload()
    let snapshot = try decodeSnapshotPayload(payload)
    let epoch = snapshot.recommendationUse.representativeEpoch
    expect(
        epoch?.currentModelConfigurationId == "current-fixture",
        "recommendation-use epoch should decode current configuration identity"
    )
    expect(
        epoch?.recommendedModelConfigurationId == "candidate-fixture",
        "recommendation-use epoch should decode recommended configuration identity"
    )
    let decision = snapshot.recommendationPortfolioV2.decisions.first
    expect(
        decision?.comparisonCandidateModelConfigurationId == "comparison-fixture",
        "recommendation decision should decode the comparison candidate"
    )
    expect(
        decision?.comparisonCandidateReasons == ["best-compatible-evidence"],
        "recommendation decision should decode comparison reasons"
    )
    expect(
        decision?.qualityGuard?.passed == true,
        "recommendation decision should decode its versioned quality guard"
    )
    let reference = snapshot.referenceSnapshotFeed.latest
    expect(
        reference?.publishedAt == "2026-08-06T00:00:00Z",
        "reference snapshot should expose publication time, not the prior slot start"
    )
    let entry = reference?.entries.first
    expect(entry?.questionScores["fixture-question"] == 18, "reference entry should decode question scores")
    expect(entry?.completedAt == "2026-07-29T11:00:00Z", "reference entry should decode completion time")
    expect(entry?.failureCount == 2, "reference entry should decode failure count")
    expect(entry?.usage?.inputTokens == 101, "reference usage should decode input tokens")
    expect(entry?.usage?.cachedInputTokens == 102, "reference usage should decode cached input tokens")
    expect(entry?.usage?.cacheWriteInputTokens == 103, "reference usage should decode cache-write tokens")
    expect(entry?.usage?.outputTokens == 104, "reference usage should decode output tokens")
    expect(entry?.usage?.reasoningTokens == 105, "reference usage should decode reasoning tokens")
    expect(
        reference?.leaderboardProjection?.rows.first?.trend.compatibilityKey
            == "fixture-v1:grader-v1",
        "leaderboard projection should decode its required compatibility key"
    )

    var optionalPayload = payload
    var optionalPortfolio = optionalPayload["recommendation_portfolio_v2"] as! [String: Any]
    var optionalDecision = (optionalPortfolio["decisions"] as! [[String: Any]])[0]
    optionalDecision["comparison_candidate_model_configuration_id"] = NSNull()
    optionalDecision.removeValue(forKey: "comparison_candidate_reasons")
    optionalDecision["quality_guard"] = NSNull()
    optionalPortfolio["decisions"] = [optionalDecision]
    optionalPayload["recommendation_portfolio_v2"] = optionalPortfolio
    var optionalFeed = optionalPayload["reference_snapshot_feed"] as! [String: Any]
    var optionalReference = optionalFeed["latest"] as! [String: Any]
    var optionalEntry = (optionalReference["entries"] as! [[String: Any]])[0]
    optionalEntry["completed_at"] = NSNull()
    optionalEntry.removeValue(forKey: "failure_count")
    optionalEntry["usage"] = NSNull()
    optionalReference["entries"] = [optionalEntry]
    optionalReference["leaderboard_projection"] = NSNull()
    optionalFeed["latest"] = optionalReference
    optionalFeed["snapshots"] = [optionalReference]
    optionalPayload["reference_snapshot_feed"] = optionalFeed
    let optionalSnapshot = try decodeSnapshotPayload(optionalPayload)
    let optionalDecodedDecision = optionalSnapshot.recommendationPortfolioV2.decisions.first
    expect(optionalDecodedDecision?.comparisonCandidateModelConfigurationId == nil, "comparison candidate should accept null")
    expect(optionalDecodedDecision?.comparisonCandidateReasons == nil, "comparison reasons should accept omission")
    expect(optionalDecodedDecision?.qualityGuard == nil, "quality guard should accept null")
    let optionalDecodedReference = optionalSnapshot.referenceSnapshotFeed.latest
    expect(optionalDecodedReference?.entries.first?.completedAt == nil, "reference completion time should accept null")
    expect(optionalDecodedReference?.entries.first?.failureCount == 0, "omitted failure count should preserve its V1 default")
    expect(optionalDecodedReference?.entries.first?.usage == nil, "reference usage should accept null")
    expect(optionalDecodedReference?.leaderboardProjection == nil, "leaderboard projection should accept null")

    var missingEpochField = payload
    var recommendationUse = missingEpochField["recommendation_use"] as! [String: Any]
    var incompleteEpoch = (recommendationUse["epochs"] as! [[String: Any]])[0]
    incompleteEpoch.removeValue(forKey: "current_model_configuration_id")
    recommendationUse["epochs"] = [incompleteEpoch]
    recommendationUse["representative_epoch"] = NSNull()
    missingEpochField["recommendation_use"] = recommendationUse
    expectSnapshotDecodeFailure(
        missingEpochField,
        "recommendation-use epoch must require current_model_configuration_id"
    )

    var missingQualityGuardSchema = payload
    var schemaPortfolio = missingQualityGuardSchema["recommendation_portfolio_v2"] as! [String: Any]
    var schemaDecision = (schemaPortfolio["decisions"] as! [[String: Any]])[0]
    var qualityGuard = schemaDecision["quality_guard"] as! [String: Any]
    qualityGuard.removeValue(forKey: "schema_version")
    schemaDecision["quality_guard"] = qualityGuard
    schemaPortfolio["decisions"] = [schemaDecision]
    missingQualityGuardSchema["recommendation_portfolio_v2"] = schemaPortfolio
    expectSnapshotDecodeFailure(
        missingQualityGuardSchema,
        "quality guard V1 must require schema_version"
    )

    var missingCompatibilityKey = payload
    var feed = missingCompatibilityKey["reference_snapshot_feed"] as! [String: Any]
    var latest = feed["latest"] as! [String: Any]
    var projection = latest["leaderboard_projection"] as! [String: Any]
    var rows = projection["rows"] as! [[String: Any]]
    var trend = rows[0]["trend"] as! [String: Any]
    trend.removeValue(forKey: "compatibility_key")
    rows[0]["trend"] = trend
    projection["rows"] = rows
    latest["leaderboard_projection"] = projection
    feed["latest"] = latest
    feed["snapshots"] = [latest]
    missingCompatibilityKey["reference_snapshot_feed"] = feed
    expectSnapshotDecodeFailure(
        missingCompatibilityKey,
        "leaderboard trend must require compatibility_key"
    )
}

private func verifyRefreshSnapshotDecoding() throws {
    var payload = try fixtureObject("architecture_refresh_snapshot_v1.json")
    var config = payload["config"] as! [String: Any]
    var system = config["system"] as! [String: Any]
    system["history_limit"] = 77
    config["system"] = system
    payload["config"] = config

    var runtime = payload["runtime"] as! [String: Any]
    runtime["history_count"] = 9
    payload["runtime"] = runtime

    var questionPack = payload["question_pack"] as! [String: Any]
    questionPack["version"] = "refresh-v2"
    payload["question_pack"] = questionPack

    var recommendationUse = payload["recommendation_use"] as! [String: Any]
    recommendationUse["schema_version"] = 2
    payload["recommendation_use"] = recommendationUse

    let refresh = try decoder().decode(
        BridgeRefreshSnapshot.self,
        from: JSONSerialization.data(withJSONObject: payload)
    )
    expect(refresh.config.system.historyLimit == 77, "refresh snapshot should decode config")
    expect(refresh.runtime.historyCount == 9, "refresh snapshot should decode runtime")
    expect(refresh.questionPack?.version == "refresh-v2", "refresh snapshot should decode question pack")
    expect(refresh.recommendationUse?.schemaVersion == 2, "refresh snapshot should decode recommendation use")

    payload["schema_version"] = 2
    do {
        _ = try decoder().decode(
            BridgeRefreshSnapshot.self,
            from: JSONSerialization.data(withJSONObject: payload)
        )
        expect(false, "unsupported refresh snapshot version should fail")
    } catch is DecodingError {
        // Expected: RefreshSnapshotV1 remains an independently versioned query DTO.
    }
}

private func verifyEventFixture(
    _ name: String,
    expectedTypes: [String],
    activeEventType: String,
    terminalEventType: String
) throws {
    let events = try decoder().decode([ScanEvent].self, from: fixtureData(name))
    expect(events.map(\.type) == expectedTypes, "\(name) should preserve event order")
    expect(events.allSatisfy { $0.schemaVersion == 1 }, "\(name) should decode RuntimeEventV1")
    let activeEvent = events.first { $0.type == activeEventType }
    expect(activeEvent?.stateKind == .runtimeDelta, "\(name) should mark active state as a delta")
    expect(activeEvent?.runtimeState?.schemaVersion == 1, "\(name) should decode runtime state schema v1")
    expect(activeEvent?.runtimeState?.runtime.isRunning == true, "\(name) should carry an active runtime delta")
    expect(activeEvent?.runtimeState?.runtime.lifecycleState == .activeScan, "\(name) started event should carry backend active lifecycle")
    expect(activeEvent?.runtimeState?.runtime.progressCompleted == activeEvent?.completedTargets, "\(name) started event should preserve planned progress")
    expect(activeEvent?.runtimeState?.runtime.progressTotal == activeEvent?.totalTargets, "\(name) started event should preserve planned total")
    expect(activeEvent?.snapshot == nil, "\(name) active events should not decode a full snapshot")
    let finalizingEvent = events.first { $0.type.hasSuffix(".finalizing") }
    expect(finalizingEvent?.stateKind == .runtimeDelta, "\(name) should carry a finalizing runtime delta")
    expect(finalizingEvent?.runtimeState?.runtime.lifecycleState == .finalizing, "\(name) should decode backend finalizing state")
    let terminalEvent = events.first { $0.type == terminalEventType }
    expect(terminalEvent?.stateKind == .snapshot, "\(name) should mark terminal state as authoritative")
    expect(terminalEvent?.runtimeState == nil, "\(name) terminal events should not decode a runtime delta")
    expect(terminalEvent?.snapshot?.runtime.lifecycleState == .idle, "\(name) should finish with an authoritative idle snapshot")
}

private func verifyBatchFailureFixtures() throws {
    let events = try decoder().decode(
        [ScanEvent].self,
        from: fixtureData("architecture_batch_failure_events_v1.json")
    )
    expect(
        events.map(\.type) == ["repair.failed", "timeout-repair.failed"],
        "batch failure fixture should cover both repair event families"
    )
    guard events.count == 2 else { return }
    expect(events[0].stateKind == .snapshot, "repair.failed should carry an authoritative snapshot")
    expect(events[0].snapshot?.runtime.lifecycleState == .failed, "repair failure snapshot should preserve the backend lifecycle")
    expect(events[0].runtimeState == nil, "repair failure must not decode as a runtime delta")
    expect(events[1].stateKind == .snapshot, "timeout-repair.failed should carry an authoritative snapshot")
    expect(events[1].snapshot?.runtime.lifecycleState == .idle, "plan failure snapshot should preserve the backend lifecycle")
    expect(events[1].runtimeState == nil, "timeout repair failure must not decode as a runtime delta")
}

private func scanProgressPayload() throws -> [String: Any] {
    let payload = try JSONSerialization.jsonObject(
        with: fixtureData("architecture_scan_events_v1.json")
    )
    guard let events = payload as? [[String: Any]],
          let progress = events.first(where: { $0["type"] as? String == "scan.progress" }) else {
        throw NSError(domain: "ArchitectureFixtureDecodingTests", code: 1)
    }
    return progress
}

private func verifyCanonicalRuntimeStateEnvelope() throws {
    var payload = try scanProgressPayload()
    guard let legacyState = payload["state"] as? [String: Any],
          let runtime = legacyState["runtime"] else {
        throw NSError(domain: "ArchitectureFixtureDecodingTests", code: 2)
    }
    payload["state"] = [
        "schema_version": 1,
        "runtime": runtime,
    ]
    let event = try decoder().decode(
        ScanEvent.self,
        from: JSONSerialization.data(withJSONObject: payload)
    )
    expect(event.runtimeState?.schemaVersion == 1, "canonical runtime state should decode schema v1")
    expect(event.runtimeState?.runtime.isRunning == true, "canonical runtime state should decode runtime")
    expect(event.snapshot == nil, "canonical runtime state should not decode a snapshot")
}

private func verifyRuntimeStateRequiresSchemaVersion() throws {
    var payload = try scanProgressPayload()
    guard var state = payload["state"] as? [String: Any] else {
        throw NSError(domain: "ArchitectureFixtureDecodingTests", code: 3)
    }
    state.removeValue(forKey: "schema_version")
    payload["state"] = state
    do {
        _ = try decoder().decode(
            ScanEvent.self,
            from: JSONSerialization.data(withJSONObject: payload)
        )
        expect(false, "runtime delta should require its schema version")
    } catch is DecodingError {
        // Expected: backend owns legacy adaptation before Swift decoding.
    }
}

private func verifyUnsupportedRuntimeStateVersionFails() throws {
    var payload = try scanProgressPayload()
    guard var state = payload["state"] as? [String: Any] else {
        throw NSError(domain: "ArchitectureFixtureDecodingTests", code: 5)
    }
    state["schema_version"] = 2
    payload["state"] = state
    do {
        _ = try decoder().decode(
            ScanEvent.self,
            from: JSONSerialization.data(withJSONObject: payload)
        )
        expect(false, "unsupported runtime state schema should fail decoding")
    } catch is DecodingError {
        // Expected: a future schema must not be silently interpreted as V1.
    }
}

private func verifyVersionedRuntimeEventEnvelopeIsStrict() throws {
    var payload = try scanProgressPayload()
    payload.removeValue(forKey: "schema_version")
    do {
        _ = try decoder().decode(
            ScanEvent.self,
            from: JSONSerialization.data(withJSONObject: payload)
        )
        expect(false, "runtime event should require schema_version")
    } catch is DecodingError {
        // Expected.
    }

    payload["schema_version"] = 2
    do {
        _ = try decoder().decode(
            ScanEvent.self,
            from: JSONSerialization.data(withJSONObject: payload)
        )
        expect(false, "unsupported runtime event schema should fail decoding")
    } catch is DecodingError {
        // Expected.
    }

    payload["schema_version"] = 1
    payload.removeValue(forKey: "state_kind")
    do {
        _ = try decoder().decode(
            ScanEvent.self,
            from: JSONSerialization.data(withJSONObject: payload)
        )
        expect(false, "versioned runtime event should require state_kind")
    } catch is DecodingError {
        // Expected.
    }

    payload["state_kind"] = "none"
    do {
        _ = try decoder().decode(
            ScanEvent.self,
            from: JSONSerialization.data(withJSONObject: payload)
        )
        expect(false, "state_kind none should reject a state payload")
    } catch is DecodingError {
        // Expected.
    }
}

private func verifyRuntimeDeltaPreservesSnapshotProjection() throws {
    var snapshot = try decoder().decode(
        BridgeSnapshot.self,
        from: fixtureData("architecture_app_snapshot_v2.json")
    )
    let events = try decoder().decode(
        [ScanEvent].self,
        from: fixtureData("architecture_scan_events_v1.json")
    )
    guard let runtime = events.first(where: { $0.type == "scan.progress" })?
        .runtimeState?.runtime else {
        throw NSError(domain: "ArchitectureFixtureDecodingTests", code: 6)
    }
    let questionPackVersion = snapshot.questionPack.version
    let recommendationUseVersion = snapshot.recommendationUse.schemaVersion
    let diagnosticsStatus = snapshot.diagnostics?.overallStatus
    snapshot.runtime = runtime
    expect(snapshot.runtime.isRunning, "runtime delta should update the snapshot runtime")
    expect(snapshot.questionPack.version == questionPackVersion, "runtime delta should preserve question pack")
    expect(snapshot.recommendationUse.schemaVersion == recommendationUseVersion, "runtime delta should preserve recommendation use")
    expect(snapshot.diagnostics?.overallStatus == diagnosticsStatus, "runtime delta should preserve diagnostics")
}

private func verifyAuthoritativeSnapshotEventTypes() throws {
    let payload = try JSONSerialization.jsonObject(
        with: fixtureData("architecture_scan_events_v1.json")
    )
    guard let events = payload as? [[String: Any]],
          let state = events.last?["state"] else {
        throw NSError(domain: "ArchitectureFixtureDecodingTests", code: 4)
    }
    for type in [
        "scan.finished",
        "scan.paused",
        "scan.stopped",
        "scan.already_running",
        "repair.finished",
        "repair.paused",
        "repair.stopped",
        "repair.already_running",
        "timeout-repair.finished",
        "timeout-repair.paused",
        "timeout-repair.stopped",
        "timeout-repair.already_running",
        "auto-resume.noop",
        "auto-resume.manual-attention",
    ] {
        let event = try decoder().decode(
            ScanEvent.self,
            from: JSONSerialization.data(withJSONObject: [
                "schema_version": 1,
                "state_kind": "snapshot",
                "type": type,
                "state": state,
            ])
        )
        expect(event.runtimeState == nil, "\(type) should not decode a runtime delta")
        expect(event.snapshot?.runtime.lifecycleState == .idle, "\(type) should decode an authoritative snapshot")
    }
}

private func verifyRecoveryResponseAttentionPolicy() throws {
    func response(status: String) throws -> BridgeRunRecoveryResponse {
        try decoder().decode(
            BridgeRunRecoveryResponse.self,
            from: JSONSerialization.data(
                withJSONObject: [
                    "ok": true,
                    "action": "recover_run",
                    "recovered": false,
                    "status": status,
                    "run_id": "run-fixture",
                    "message": "fixture",
                ]
            )
        )
    }

    let incomplete = try response(status: "incomplete")
    expect(
        incomplete.requiresAttention,
        "incomplete recovery should require non-blocking attention"
    )
    for status in ["no_active_run", "not_finalizing", "scan_active", "recovered"] {
        let normal = try response(status: status)
        expect(
            !normal.requiresAttention,
            "\(status) recovery should remain a normal startup result"
        )
    }
}

private func verifyMaintenanceCommandResponses() throws {
    let observation = try decoder().decode(
        BridgeStateObservationResponse.self,
        from: JSONSerialization.data(
            withJSONObject: [
                "schema_version": 1,
                "ok": true,
                "action": "observe_state",
                "status": "observed",
                "message": "fixture",
                "state": try fixtureObject("architecture_refresh_snapshot_v1.json"),
            ]
        )
    )
    expect(observation.ok, "state observation response should decode ok")
    expect(
        observation.state.runtime.lifecycleState == .idle,
        "state observation response should decode refresh state"
    )

    let reference = try decoder().decode(
        BridgeReferenceRefreshResponse.self,
        from: JSONSerialization.data(
            withJSONObject: [
                "schema_version": 1,
                "ok": true,
                "action": "refresh_reference",
                "status": "failed",
                "message": "fixture",
                "state": try fixtureObject("architecture_app_snapshot_v2.json"),
            ]
        )
    )
    expect(
        reference.requiresAttention,
        "failed reference refresh should require non-blocking attention"
    )
    expect(
        reference.state.runtime.lifecycleState == .idle,
        "reference refresh response should decode authoritative snapshot"
    )
}

private func verifyPythonObservationResponse(at path: String) throws {
    let response = try decoder().decode(
        BridgeStateObservationResponse.self,
        from: Data(contentsOf: URL(fileURLWithPath: path))
    )
    expect(response.schemaVersion == 1, "Python observe-state response should use command schema V1")
    expect(response.state.schemaVersion == 1, "Python observe-state state should use RefreshSnapshotV1")
    expect(
        response.state.runtime.lifecycleState == .idle,
        "Python observe-state RefreshSnapshotV1 should decode current runtime"
    )
}

@main
private enum ArchitectureFixtureDecodingTestMain {
    static func main() {
        do {
            try verifySnapshotFixtures()
            try verifyExtendedSnapshotDTOContracts()
            try verifyRefreshSnapshotDecoding()
            try verifyEventFixture(
                "architecture_scan_events_v1.json",
                expectedTypes: [
                    "scan.started",
                    "target.started",
                    "scan.progress",
                    "scan.finalizing",
                    "scan.finished",
                ],
                activeEventType: "scan.started",
                terminalEventType: "scan.finished"
            )
            try verifyEventFixture(
                "architecture_repair_events_v1.json",
                expectedTypes: [
                    "repair.started",
                    "repair.question.started",
                    "repair.question.finished",
                    "repair.finalizing",
                    "repair.finished",
                ],
                activeEventType: "repair.started",
                terminalEventType: "repair.finished"
            )
            try verifyEventFixture(
                "architecture_failed_batch_events_v1.json",
                expectedTypes: [
                    "repair.started",
                    "repair.question.started",
                    "repair.question.finished",
                    "repair.finalizing",
                    "repair.finished",
                ],
                activeEventType: "repair.started",
                terminalEventType: "repair.finished"
            )
            try verifyEventFixture(
                "architecture_timeout_batch_events_v1.json",
                expectedTypes: [
                    "timeout-repair.started",
                    "timeout-repair.question.started",
                    "timeout-repair.question.finished",
                    "timeout-repair.finalizing",
                    "timeout-repair.finished",
                ],
                activeEventType: "timeout-repair.started",
                terminalEventType: "timeout-repair.finished"
            )
            try verifyBatchFailureFixtures()
            try verifyCanonicalRuntimeStateEnvelope()
            try verifyRuntimeStateRequiresSchemaVersion()
            try verifyUnsupportedRuntimeStateVersionFails()
            try verifyVersionedRuntimeEventEnvelopeIsStrict()
            try verifyRuntimeDeltaPreservesSnapshotProjection()
            try verifyAuthoritativeSnapshotEventTypes()
            try verifyRecoveryResponseAttentionPolicy()
            try verifyMaintenanceCommandResponses()
            if CommandLine.arguments.count > 1 {
                try verifyPythonObservationResponse(at: CommandLine.arguments[1])
            }
        } catch {
            failureCount += 1
            fputs("FAIL: \(error)\n", stderr)
        }
        if failureCount > 0 {
            exit(1)
        }
        print("Architecture fixture decoding tests passed")
    }
}
