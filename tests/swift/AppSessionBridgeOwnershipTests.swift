import Combine
import Foundation

private var failureCount = 0

private func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() {
        failureCount += 1
        fputs("FAIL: \(message)\n", stderr)
    }
}

private enum StubGatewayError: LocalizedError {
    case expectedFailure
    case unexpectedCall

    var errorDescription: String? {
        switch self {
        case .expectedFailure:
            return "expected gateway failure"
        case .unexpectedCall:
            return "unexpected gateway call"
        }
    }
}

private final class BlockingSnapshotGate: @unchecked Sendable {
    private let condition = NSCondition()
    private var started = false
    private var released = false

    func blockUntilReleased() {
        condition.lock()
        started = true
        condition.broadcast()
        while !released {
            condition.wait()
        }
        condition.unlock()
    }

    func hasStarted() -> Bool {
        condition.lock()
        defer { condition.unlock() }
        return started
    }

    func release() {
        condition.lock()
        released = true
        condition.broadcast()
        condition.unlock()
    }
}

private actor StubBridgeGateway: AppSessionBridgeGatewayProtocol {
    private let defaultPatchResult: Result<BridgeSnapshot, StubGatewayError>
    private var patchResults: [Result<BridgeSnapshot, StubGatewayError>]
    private let endpointResult: Result<BridgeSnapshot, StubGatewayError>
    private let scanControlResult: Result<BridgeScanControlResponse, StubGatewayError>
    private let clearResult: BridgeDataOperationResponse?
    private let localImportResult: BridgeLocalImportResponse?
    private let connectionTestResult: BridgeConnectionTestResponse?
    private let observationError: StubGatewayError?
    private var snapshotResults: [BridgeSnapshot]
    private var loadSnapshots: [BridgeSnapshot]
    private let manualReferenceRefreshStatus: String
    private var loadSnapshotMaintenanceRequests: [Bool] = []
    private var loadSnapshotReferenceRefreshRequests: [Bool] = []
    private var observeStateCallCount = 0
    private var customScanPreviewRequest: (candidateIDs: [String], evaluationProfileID: String?)?
    private var scanPreviewRequests: [BridgeScanIntent] = []
    private var scanControlActions: [String] = []
    private var scanControlClientSessionIDs: [String] = []
    private var patchPayloads: [Data] = []
    private var snapshotCallCount = 0
    private var clearCallCount = 0
    private var localImportProviderIDs: [String] = []
    private var connectionTestRequests: [(connectionID: String, modelID: String)] = []
    private let firstLoadGate: BlockingSnapshotGate?
    private let firstPatchGate: BlockingSnapshotGate?
    private var didBlockFirstLoad = false
    private var didBlockFirstPatch = false

    init(
        patchResult: Result<BridgeSnapshot, StubGatewayError>,
        endpointResult: Result<BridgeSnapshot, StubGatewayError>? = nil,
        scanControlResult: Result<BridgeScanControlResponse, StubGatewayError> = .failure(
            .unexpectedCall
        ),
        loadSnapshots: [BridgeSnapshot] = [],
        manualReferenceRefreshStatus: String = "refreshed",
        firstLoadGate: BlockingSnapshotGate? = nil,
        patchResults: [Result<BridgeSnapshot, StubGatewayError>] = [],
        firstPatchGate: BlockingSnapshotGate? = nil,
        snapshotResults: [BridgeSnapshot] = [],
        clearResult: BridgeDataOperationResponse? = nil,
        localImportResult: BridgeLocalImportResponse? = nil,
        connectionTestResult: BridgeConnectionTestResponse? = nil,
        observationError: StubGatewayError? = nil
    ) {
        self.defaultPatchResult = patchResult
        self.patchResults = patchResults
        self.endpointResult = endpointResult ?? patchResult
        self.scanControlResult = scanControlResult
        self.loadSnapshots = loadSnapshots
        self.manualReferenceRefreshStatus = manualReferenceRefreshStatus
        self.firstLoadGate = firstLoadGate
        self.firstPatchGate = firstPatchGate
        self.snapshotResults = snapshotResults
        self.clearResult = clearResult
        self.localImportResult = localImportResult
        self.connectionTestResult = connectionTestResult
        self.observationError = observationError
    }

    func loadSnapshot(
        performStartupMaintenance: Bool,
        refreshReference: Bool
    ) throws -> StartupLoadResult {
        loadSnapshotMaintenanceRequests.append(performStartupMaintenance)
        loadSnapshotReferenceRefreshRequests.append(refreshReference)
        if !didBlockFirstLoad, let firstLoadGate {
            didBlockFirstLoad = true
            firstLoadGate.blockUntilReleased()
        }
        guard !loadSnapshots.isEmpty else {
            throw StubGatewayError.unexpectedCall
        }
        return StartupLoadResult(
            snapshot: loadSnapshots.removeFirst(),
            warningDetail: nil,
            referenceRefreshStatus: refreshReference ? manualReferenceRefreshStatus : nil
        )
    }

    func observeState(includeCodexInsights: Bool) throws -> BridgeStateObservationResponse {
        observeStateCallCount += 1
        if let observationError {
            throw observationError
        }
        return try decodedObservation()
    }

    func recordedLoadSnapshotMaintenanceRequests() -> [Bool] {
        loadSnapshotMaintenanceRequests
    }

    func recordedLoadSnapshotReferenceRefreshRequests() -> [Bool] {
        loadSnapshotReferenceRefreshRequests
    }

    func recordedObserveStateCallCount() -> Int {
        observeStateCallCount
    }

    func snapshot() throws -> BridgeSnapshot {
        snapshotCallCount += 1
        guard !snapshotResults.isEmpty else {
            throw StubGatewayError.unexpectedCall
        }
        return snapshotResults.removeFirst()
    }

    func requestScanControl(
        _ action: String,
        clientSessionID: String
    ) throws -> BridgeScanControlResponse {
        scanControlActions.append(action)
        scanControlClientSessionIDs.append(clientSessionID)
        switch scanControlResult {
        case .success(let response):
            return response
        case .failure(let error):
            throw error
        }
    }

    func recordedScanControlActions() -> [String] {
        scanControlActions
    }

    func recordedScanControlClientSessionIDs() -> [String] {
        scanControlClientSessionIDs
    }

    func dismissResumableRun() throws -> BridgeScanControlResponse {
        throw StubGatewayError.unexpectedCall
    }

    func patchConfig(_ patch: SettingsConfigPatch) throws -> BridgeSnapshot {
        patchPayloads.append(
            try JSONSerialization.data(
                withJSONObject: patch.commandPayload,
                options: [.sortedKeys]
            )
        )
        if !didBlockFirstPatch, let firstPatchGate {
            didBlockFirstPatch = true
            firstPatchGate.blockUntilReleased()
        }
        let result = patchResults.isEmpty
            ? defaultPatchResult
            : patchResults.removeFirst()
        switch result {
        case .success(let snapshot):
            return snapshot
        case .failure(let error):
            throw error
        }
    }

    func upsertEndpoint(_ intent: BridgeEndpointUpsertIntent) throws -> BridgeSnapshot {
        try endpointSnapshot()
    }

    func addEndpointModels(_ intent: BridgeEndpointModelsIntent) throws -> BridgeSnapshot {
        try endpointSnapshot()
    }

    func previewCustomScanOptions(
        candidateIDs: [String],
        evaluationProfileID: String?
    ) throws -> BridgeCustomScanPlanOptions {
        customScanPreviewRequest = (candidateIDs, evaluationProfileID)
        throw StubGatewayError.expectedFailure
    }

    func recordedCustomScanPreviewRequest() -> (
        candidateIDs: [String],
        evaluationProfileID: String?
    )? {
        customScanPreviewRequest
    }

    func previewScan(_ intent: BridgeScanIntent) throws -> BridgeScanPlanPreview {
        scanPreviewRequests.append(intent)
        throw StubGatewayError.expectedFailure
    }

    func recordedScanPreviewRequest() -> BridgeScanIntent? {
        scanPreviewRequests.last
    }

    func recordedScanPreviewRequests() -> [BridgeScanIntent] {
        scanPreviewRequests
    }

    func discoverModels(connectionID: String) throws -> BridgeModelDiscoveryResponse {
        throw StubGatewayError.unexpectedCall
    }

    func testConnection(
        connectionID: String,
        modelID: String
    ) throws -> BridgeConnectionTestResponse {
        connectionTestRequests.append((connectionID, modelID))
        guard let connectionTestResult else {
            throw StubGatewayError.unexpectedCall
        }
        return connectionTestResult
    }

    func importLocalProvider(providerID: String) throws -> BridgeLocalImportResponse {
        localImportProviderIDs.append(providerID)
        guard let localImportResult else {
            throw StubGatewayError.unexpectedCall
        }
        return localImportResult
    }

    func discoverLocalModels(
        providerID: String
    ) throws -> BridgeLocalModelDiscoveryResponse {
        throw StubGatewayError.unexpectedCall
    }

    func probeEndpointConnection(
        baseURL: String,
        apiFormat: String,
        providerPreset: String,
        modelID: String,
        scanProfile: String,
        apiKey: String
    ) throws -> BridgeConnectionTestResponse {
        throw StubGatewayError.unexpectedCall
    }

    func probeEndpointModels(
        baseURL: String,
        apiFormat: String,
        apiKey: String
    ) throws -> BridgeModelDiscoveryResponse {
        throw StubGatewayError.unexpectedCall
    }

    func exportPersonalObservations(to url: URL) throws {
        throw StubGatewayError.unexpectedCall
    }

    func clearPersonalObservations() throws -> BridgeDataOperationResponse {
        clearCallCount += 1
        guard let clearResult else {
            throw StubGatewayError.unexpectedCall
        }
        return clearResult
    }

    func recordedPatchPayloads() -> [Data] {
        patchPayloads
    }

    func recordedSnapshotCallCount() -> Int {
        snapshotCallCount
    }

    func recordedClearCallCount() -> Int {
        clearCallCount
    }

    func recordedLocalImportProviderIDs() -> [String] {
        localImportProviderIDs
    }

    func recordedConnectionTestRequests() -> [(connectionID: String, modelID: String)] {
        connectionTestRequests
    }

    private func endpointSnapshot() throws -> BridgeSnapshot {
        switch endpointResult {
        case .success(let snapshot):
            return snapshot
        case .failure(let error):
            throw error
        }
    }
}

private func endpointUpsertIntent() -> BridgeEndpointUpsertIntent {
    BridgeEndpointUpsertIntent(
        connectionID: "endpoint-a",
        name: "Endpoint A",
        providerPreset: "openai",
        apiFormat: "openai_responses",
        baseURL: "https://example.invalid/v1",
        apiKeyReference: "keychain:test:pending",
        enabled: true,
        modelIDs: ["model-a"],
        reasoningProfilesByModel: ["model-a": ["medium", "high"]],
        defaultReasoningProfileByModel: ["model-a": "high"],
        candidateEnabled: true,
        lastTestStatus: nil,
        lastTestAt: nil,
        lastTestMessage: nil
    )
}

private func endpointModelsIntent() -> BridgeEndpointModelsIntent {
    BridgeEndpointModelsIntent(
        connectionID: "endpoint-a",
        modelIDs: ["model-b"],
        reasoningProfilesByModel: ["model-b": ["low", "high"]],
        defaultReasoningProfileByModel: ["model-b": "high"],
        candidateEnabled: false
    )
}

private func encodedObject<T: Encodable>(_ value: T) throws -> [String: Any] {
    try JSONSerialization.jsonObject(with: JSONEncoder().encode(value)) as! [String: Any]
}

private func decodedValue<T: Decodable>(
    _ type: T.Type,
    from payload: [String: Any]
) throws -> T {
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase
    return try decoder.decode(
        type,
        from: JSONSerialization.data(withJSONObject: payload)
    )
}

private func patchOperations(_ payloads: [Data]) throws -> [String] {
    try payloads.map { data in
        let payload = try JSONSerialization.jsonObject(with: data) as! [String: Any]
        return payload["operation"] as! String
    }
}

private func verifyEndpointIntentsContainNoCandidateProjection() throws {
    let upsert = try encodedObject(endpointUpsertIntent())
    expect(upsert["schema_version"] as? Int == 1, "endpoint upsert should use schema v1")
    expect(upsert["model_ids"] as? [String] == ["model-a"], "endpoint upsert should send model intent")
    expect(
        upsert["reasoning_profiles_by_model"] as? [String: [String]]
            == ["model-a": ["medium", "high"]],
        "endpoint upsert should send reasoning profile intent"
    )
    expect(upsert["last_test_status"] is NSNull, "missing endpoint test status should encode as null")
    for forbiddenKey in [
        "model_candidates",
        "id",
        "family_id",
        "variant_id",
        "scan_profile",
        "capabilities",
    ] {
        expect(upsert[forbiddenKey] == nil, "endpoint upsert must not project \(forbiddenKey)")
    }

    let add = try encodedObject(endpointModelsIntent())
    expect(add["schema_version"] as? Int == 1, "endpoint model add should use schema v1")
    expect(add["model_ids"] as? [String] == ["model-b"], "endpoint model add should send model ids")
    expect(add["candidate_enabled"] as? Bool == false, "new endpoint models should preserve enabled intent")
    expect(add["model_candidates"] == nil, "endpoint model add must not send candidate projections")
}

private func snapshotPayload(
    historyCount: Int,
    isRunning: Bool = false,
    configure: (inout [String: Any]) -> Void = { _ in }
) throws -> [String: Any] {
    let fixtureURL = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        .appendingPathComponent("tests/fixtures/architecture_app_snapshot_v2.json")
    var payload = try JSONSerialization.jsonObject(
        with: Data(contentsOf: fixtureURL)
    ) as! [String: Any]
    var runtime = payload["runtime"] as! [String: Any]
    runtime["history_count"] = historyCount
    runtime["is_running"] = isRunning
    payload["runtime"] = runtime
    configure(&payload)
    return payload
}

private func decodedSnapshot(
    historyCount: Int,
    isRunning: Bool = false,
    configure: (inout [String: Any]) -> Void = { _ in }
) throws -> BridgeSnapshot {
    let payload = try snapshotPayload(
        historyCount: historyCount,
        isRunning: isRunning,
        configure: configure
    )
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase
    return try decoder.decode(
        BridgeSnapshot.self,
        from: JSONSerialization.data(withJSONObject: payload)
    )
}

private func decodedObservation() throws -> BridgeStateObservationResponse {
    let fixtureURL = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        .appendingPathComponent("tests/fixtures/architecture_refresh_snapshot_v1.json")
    let state = try JSONSerialization.jsonObject(
        with: Data(contentsOf: fixtureURL)
    ) as! [String: Any]
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase
    return try decoder.decode(
        BridgeStateObservationResponse.self,
        from: JSONSerialization.data(
            withJSONObject: [
                "schema_version": 1,
                "ok": true,
                "action": "observe_state",
                "status": "observed",
                "message": "observed",
                "state": state,
            ]
        )
    )
}

private func scanEventPayload(_ type: String) throws -> [String: Any] {
    let fixtureURL = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        .appendingPathComponent("tests/fixtures/architecture_scan_events_v1.json")
    let events = try JSONSerialization.jsonObject(
        with: Data(contentsOf: fixtureURL)
    ) as! [[String: Any]]
    return events.first { $0["type"] as? String == type }!
}

private func makeStreamingBridge(
    events: [[String: Any]],
    exitCode: Int = 0,
    trailingDelay: TimeInterval = 0
) throws -> (client: NativeBridgeClient, root: URL) {
    let root = URL(fileURLWithPath: NSTemporaryDirectory())
        .appendingPathComponent("modeldial-stream-contract-\(UUID().uuidString)")
    let scripts = root.appendingPathComponent("scripts", isDirectory: true)
    try FileManager.default.createDirectory(
        at: scripts,
        withIntermediateDirectories: true
    )
    let fixture = try snapshotPayload(historyCount: 0)
    let config = fixture["config"] as! [String: Any]
    try JSONSerialization.data(withJSONObject: config).write(
        to: root.appendingPathComponent("fixture-config.json")
    )
    try JSONSerialization.data(withJSONObject: events).write(
        to: root.appendingPathComponent("events.json")
    )
    let script = """
    import json
    import pathlib
    import sys
    import time

    root = pathlib.Path(__file__).resolve().parent.parent
    command = sys.argv[1]
    if command == "read-config":
        print((root / "fixture-config.json").read_text())
        raise SystemExit(0)
    if command == "scan":
        events = json.loads((root / "events.json").read_text())
        for event in events:
            delay = float(event.pop("_delay_after", 0))
            print(json.dumps(event), flush=True)
            if delay:
                time.sleep(delay)
        if \(trailingDelay) > 0:
            time.sleep(\(trailingDelay))
        raise SystemExit(\(exitCode))
    raise SystemExit(2)
    """
    try Data(script.utf8).write(
        to: scripts.appendingPathComponent("native_bridge.py")
    )
    return (
        NativeBridgeClient(repoRoot: root, dataDirectory: root),
        root
    )
}

private func configuredSnapshot(
    historyCount: Int,
    historyLimit: Int,
    schedulerEnabled: Bool
) throws -> BridgeSnapshot {
    try decodedSnapshot(historyCount: historyCount) { payload in
        var config = payload["config"] as! [String: Any]
        var system = config["system"] as! [String: Any]
        system["history_limit"] = historyLimit
        config["system"] = system
        var scheduler = config["scheduler"] as! [String: Any]
        scheduler["enabled"] = schedulerEnabled
        config["scheduler"] = scheduler
        payload["config"] = config
    }
}

private func scanPlanningSnapshot() throws -> BridgeSnapshot {
    try decodedSnapshot(historyCount: 1) { payload in
        var questionPack = payload["question_pack"] as! [String: Any]
        questionPack["default_evaluation_profile_id"] = "quick"
        questionPack["evaluation_profiles"] = [
            [
                "id": "quick",
                "label": "快速对比",
                "summary": "Fixture quick profile",
                "question_ids": ["fixture-question"],
                "question_count": 1,
                "result_level": "provisional",
                "score_presentation": "provisional",
                "score_max": 20,
                "upgrade_to": "full",
            ],
            [
                "id": "full",
                "label": "完整评测",
                "summary": "Fixture full profile",
                "question_ids": ["fixture-question"],
                "question_count": 1,
                "result_level": "complete",
                "score_presentation": "overall",
                "score_max": 20,
                "upgrade_to": NSNull(),
            ],
        ]
        payload["question_pack"] = questionPack

        var dashboard = payload["dashboard"] as! [String: Any]
        var metadata = dashboard["run_metadata"] as! [String: Any]
        metadata["run_id"] = "run-quick"
        metadata["evaluation_profile_id"] = "quick"
        metadata["evaluation_result_level"] = "provisional"
        metadata["upgrade_target_profile_id"] = "full"
        dashboard["run_metadata"] = metadata
        payload["dashboard"] = dashboard
    }
}

private func leaderboardEntryPayload(
    id: String,
    model: String,
    score: Int
) -> [String: Any] {
    [
        "candidate_id": id,
        "label": id,
        "model": model,
        "model_id": model,
        "effort": "high",
        "correct_count": 1,
        "total_count": 1,
        "question_count": 99,
        "semantic_score": score,
        "semantic_total": 100,
        "score_text": "\(score)/100",
        "mode_score": score,
        "mode_score_max": 100,
        "mode_score_text": "\(score)/100",
        "overall_score": score,
        "overall_score_text": "\(score)/100",
        "pass_rate": 100,
        "truncation_hits": 0,
        "elapsed_seconds": 10,
        "estimated_cost_usd": 0.1,
        "cost_coverage": "complete",
        "valid_completed_at": "2000-01-01T00:00:00Z",
        "question_pack_version": "deliberately-not-current",
        "is_current_pack_comparable": false,
        "is_current_run_eligible": false,
        "question_results": [],
    ]
}

private func recommendationDecisionPayload(
    currentID: String,
    candidateID: String
) -> [String: Any] {
    [
        "current_model_configuration_id": currentID,
        "candidate_model_configuration_id": candidateID,
        "comparison_candidate_model_configuration_id": candidateID,
        "comparison_candidate_reasons": [],
        "decision": "recommend",
        "reason": "fixture",
        "quality_tradeoff": false,
        "quality_warning_question_ids": [],
        "quality": [
            "current_score": 70,
            "candidate_score": 90,
            "score_delta": 20,
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
    ]
}

private func recommendationPortfolioPayload(
    currentID: String,
    candidateID: String,
    resolvedDataSource: String
) -> [String: Any] {
    [
        "schema_version": 2,
        "source_mode": "auto",
        "source_mode_by_configuration_id": [currentID: "auto"],
        "resolved_data_source": resolvedDataSource,
        "source_resolution_reason": "fixture",
        "preference": "smart",
        "representative_configuration_id": currentID,
        "representative_reason": "fixture",
        "status": "recommend",
        "decisions": [
            recommendationDecisionPayload(
                currentID: currentID,
                candidateID: candidateID
            ),
        ],
        "testable_candidate_ids": [],
        "unmapped_active_session_count": 0,
    ]
}

private func advisorEvidencePayload(
    currentID: String,
    eligibleCandidateIDs: [String],
    decisions: [[String: Any]],
    resolvedDataSource: String
) -> [String: Any] {
    [
        "schema_version": 2,
        "source_mode": "auto",
        "resolved_data_source": resolvedDataSource,
        "source_reason": "fixture",
        "source_snapshot_id": "fixture-snapshot",
        "current_model_configuration_id": currentID,
        "current_status": "ready",
        "eligible_candidate_ids": eligibleCandidateIDs,
        "testable_candidate_ids": decisions.compactMap { decision in
            decision["status"] as? String == "eligible"
                ? nil
                : decision["model_configuration_id"] as? String
        },
        "candidate_decisions": decisions,
        "resolved_result_rows": [],
    ]
}

private func referenceEntryPayload(
    id: String,
    model: String,
    score: Int,
    providerID: String = "fixture",
    effort: String = "high"
) -> [String: Any] {
    [
        "model_configuration_id": id,
        "model_configuration": [
            "provider_id": providerID,
            "raw_model_id": model,
            "canonical_model_id": model,
            "display_name": model,
            "reasoning_effort": effort,
            "service_tier": "default",
            "route_type": "official",
        ],
        "advisor_eligible": true,
        "score": score,
        "max_score": 100,
        "elapsed_ms": 1_000,
        "estimated_api_cost_usd": 0.1,
        "cost_coverage": "complete",
        "question_scores": [:],
        "completed_at": "2099-01-01T00:00:00Z",
    ]
}

private func referenceFeedPayload(
    entries: [[String: Any]],
    leaderboardOrder: [String] = [],
    recommendedID: String? = nil
) -> [String: Any] {
    var snapshot: [String: Any] = [
        "schema_version": 1,
        "kind": "first_party_snapshot",
        "batch_id": "fixture-official",
        "published_at": "2099-01-01T00:00:00Z",
        "question_pack_version": "fixture-v1",
        "grader_version": "fixture-grader",
        "entry_count": entries.count,
        "entries": entries,
        "provenance": [
            "kind": "first_party_snapshot",
            "public_official_snapshot": true,
        ],
    ]
    if !leaderboardOrder.isEmpty {
        snapshot["leaderboard_projection"] = [
            "schema_version": 1,
            "source": "publisher",
            "ranking_rule": "fixture",
            "trend_rule": "fixture",
            "questions": [],
            "rows": leaderboardOrder.enumerated().map { index, id in
                [
                    "model_configuration_id": id,
                    "rank": index + 1,
                    "target_labels": [[
                        "id": "highest_score",
                        "label": "Highest score",
                    ]],
                    "decision_tags": id == recommendedID ? [[
                        "kind": "recommended",
                        "label": "推荐",
                        "detail": "fixture",
                    ]] : [],
                    "question_scores": [],
                    "trend": [
                        "compatibility_key": "fixture",
                        "sample_count": 1,
                        "comparable": false,
                        "stable_ranking_eligible": false,
                        "points": [],
                    ],
                ]
            },
        ]
    }
    return [
        "schema_version": 1,
        "status": "ready",
        "kind": "first_party_snapshot",
        "latest": snapshot,
        "snapshots": [snapshot],
    ]
}

@MainActor
private func waitForHistoryCount(
    _ expectedCount: Int,
    in store: AppSessionStore
) async throws {
    for _ in 0..<100 {
        if store.snapshot?.runtime.historyCount == expectedCount { return }
        try await Task.sleep(nanoseconds: 5_000_000)
    }
    expect(false, "timed out waiting for history count \(expectedCount)")
}

@MainActor
private func waitUntil(
    _ message: String,
    attempts: Int = 200,
    condition: () -> Bool
) async throws {
    for _ in 0..<attempts {
        if condition() { return }
        try await Task.sleep(nanoseconds: 5_000_000)
    }
    expect(false, message)
}

@MainActor
private func waitForSettingsSave(
    _ settingsStore: SelectionSettingsStore
) async throws {
    for _ in 0..<100 {
        if !settingsStore.isSaving { return }
        try await Task.sleep(nanoseconds: 5_000_000)
    }
    expect(false, "timed out waiting for settings save")
}

@MainActor
private func makeStore(
    gateway: any AppSessionBridgeGatewayProtocol,
    bridge: NativeBridgeClient? = nil,
    initialSnapshot: BridgeSnapshot? = nil,
    referenceSnapshotRefreshPolicy: ReferenceSnapshotRefreshPolicy =
        ReferenceSnapshotRefreshPolicy(persistence: nil)
) -> AppSessionStore {
    let temporaryRoot = URL(fileURLWithPath: NSTemporaryDirectory())
        .appendingPathComponent("modeldial-bridge-ownership-\(UUID().uuidString)")
    return AppSessionStore(
        bridge: bridge ?? NativeBridgeClient(
            repoRoot: temporaryRoot,
            dataDirectory: temporaryRoot
        ),
        commandGateway: gateway,
        timersEnabled: false,
        clientSessionID: "test-client-session",
        initialSnapshot: initialSnapshot,
        referenceSnapshotRefreshPolicy: referenceSnapshotRefreshPolicy
    )
}

@MainActor
private func verifySettingsDraftFollowsAuthoritativeSnapshotsWithoutOwnGatewayQuery() async throws {
    let initial = try configuredSnapshot(
        historyCount: 0,
        historyLimit: 41,
        schedulerEnabled: false
    )
    let updated = try configuredSnapshot(
        historyCount: 1,
        historyLimit: 73,
        schedulerEnabled: true
    )
    let gateway = StubBridgeGateway(
        patchResult: .failure(.unexpectedCall),
        loadSnapshots: [updated]
    )
    let sessionStore = makeStore(gateway: gateway, initialSnapshot: initial)

    let settingsStore = SelectionSettingsStore(sessionStore: sessionStore)
    expect(
        settingsStore.draftConfig?.system.historyLimit == 41,
        "settings draft should initialize from the current AppSession snapshot"
    )

    sessionStore.reloadPeriodicSnapshotAsync()
    try await waitForHistoryCount(1, in: sessionStore)
    expect(
        settingsStore.draftConfig?.system.historyLimit == 73,
        "settings draft should follow later authoritative AppSession snapshots"
    )
    expect(
        settingsStore.draftConfig?.scheduler.enabled == true,
        "settings draft should consume the complete authoritative config"
    )

    settingsStore.reload()
    try await Task.sleep(nanoseconds: 20_000_000)
    expect(
        settingsStore.draftConfig?.system.historyLimit == 73,
        "settings reload should rebuild the draft from the current AppSession snapshot"
    )

    let snapshotRequests = await gateway.recordedLoadSnapshotMaintenanceRequests()
    let observationRequests = await gateway.recordedObserveStateCallCount()
    expect(
        snapshotRequests == [false] && observationRequests == 1,
        "settings draft reload should reuse the AppSession observation and snapshot refresh"
    )
}

@MainActor
private func verifySettingsSaveProtectsDraftAndConsumesCommandSnapshot() async throws {
    let initial = try configuredSnapshot(
        historyCount: 0,
        historyLimit: 50,
        schedulerEnabled: false
    )
    let unrelatedRefresh = try configuredSnapshot(
        historyCount: 1,
        historyLimit: 88,
        schedulerEnabled: false
    )
    let saved = try configuredSnapshot(
        historyCount: 2,
        historyLimit: 61,
        schedulerEnabled: true
    )
    let loadGate = BlockingSnapshotGate()
    let patchGate = BlockingSnapshotGate()
    defer {
        loadGate.release()
        patchGate.release()
    }
    let gateway = StubBridgeGateway(
        patchResult: .success(saved),
        loadSnapshots: [unrelatedRefresh],
        firstLoadGate: loadGate,
        firstPatchGate: patchGate
    )
    let sessionStore = makeStore(gateway: gateway, initialSnapshot: initial)
    let settingsStore = SelectionSettingsStore(sessionStore: sessionStore)

    sessionStore.reloadPeriodicSnapshotAsync()
    try await waitUntil("periodic refresh should reach the gateway") {
        loadGate.hasStarted()
    }
    settingsStore.setSchedulerEnabled(true)
    expect(settingsStore.isSaving, "settings save should enter the in-flight state synchronously")
    loadGate.release()
    try await waitUntil("settings patch should wait after the periodic refresh") {
        patchGate.hasStarted()
    }
    try await waitForHistoryCount(1, in: sessionStore)
    expect(
        settingsStore.draftConfig?.system.historyLimit == 50,
        "an in-flight draft must not be overwritten by a concurrent snapshot refresh"
    )

    patchGate.release()
    try await waitForSettingsSave(settingsStore)
    expect(
        settingsStore.draftConfig?.system.historyLimit == 61,
        "save completion should consume the command-returned authoritative config"
    )
    expect(
        settingsStore.draftConfig?.scheduler.enabled == true,
        "save completion should publish the persisted setting"
    )
}

@MainActor
private func verifyPeriodicRefreshObservesAndLoadsAnAuthoritativeSnapshot() async throws {
    let initial = try decodedSnapshot(historyCount: 0)
    let periodic = try decodedSnapshot(historyCount: 9)
    let immediateFollowUp = try decodedSnapshot(historyCount: 10)
    let gateway = StubBridgeGateway(
        patchResult: .failure(.unexpectedCall),
        loadSnapshots: [periodic, immediateFollowUp]
    )
    let store = makeStore(gateway: gateway, initialSnapshot: initial)

    store.reloadPeriodicSnapshotAsync()
    try await waitForHistoryCount(9, in: store)

    let maintenanceRequests = await gateway.recordedLoadSnapshotMaintenanceRequests()
    let observeStateCallCount = await gateway.recordedObserveStateCallCount()
    expect(
        maintenanceRequests == [false],
        "periodic refresh should request a complete snapshot without startup maintenance"
    )
    expect(
        observeStateCallCount == 1,
        "periodic refresh should update local observations before reading the full snapshot"
    )
    expect(
        store.snapshot?.runtime.historyCount == periodic.runtime.historyCount,
        "periodic refresh should publish the authoritative snapshot as one generation"
    )

    store.reloadPeriodicSnapshotAsync()
    try await waitForHistoryCount(10, in: store)
    let referenceRefreshRequests = await gateway.recordedLoadSnapshotReferenceRefreshRequests()
    let finalObserveStateCallCount = await gateway.recordedObserveStateCallCount()
    expect(
        referenceRefreshRequests == [true, false],
        "periodic polling should refresh the remote feed once, then wait for the next UTC slot"
    )
    expect(
        finalObserveStateCallCount == 2,
        "each idle periodic refresh should advance local usage observations"
    )
}

@MainActor
private func verifyPeriodicObservationFailureStillPublishesSavedSnapshot() async throws {
    let initial = try decodedSnapshot(historyCount: 0)
    let saved = try decodedSnapshot(historyCount: 7)
    let gateway = StubBridgeGateway(
        patchResult: .failure(.unexpectedCall),
        loadSnapshots: [saved],
        observationError: .expectedFailure
    )
    let store = makeStore(gateway: gateway, initialSnapshot: initial)

    store.reloadPeriodicSnapshotAsync()
    try await waitForHistoryCount(7, in: store)

    expect(
        store.snapshot?.runtime.historyCount == 7,
        "an observation failure should not block the saved authoritative snapshot"
    )
    expect(
        store.snapshotRefreshIssue?.message.contains("本机使用记录暂未更新") == true,
        "an observation failure should remain visible without discarding the snapshot"
    )
}

@MainActor
private func verifyManualReferenceRefreshBypassesTheFixedSchedule() async throws {
    let initial = try decodedSnapshot(historyCount: 0)
    let refreshed = try decodedSnapshot(historyCount: 11)
    var policy = ReferenceSnapshotRefreshPolicy(persistence: nil)
    let now = Date()
    expect(policy.claimIfDue(now: now), "the setup refresh should be due")
    policy.record(status: "not_modified", latestPublishedAt: now, now: now)
    expect(!policy.isDue(now: now), "the setup should wait for the next UTC slot")

    let gateway = StubBridgeGateway(
        patchResult: .failure(.unexpectedCall),
        loadSnapshots: [refreshed]
    )
    let store = makeStore(
        gateway: gateway,
        initialSnapshot: initial,
        referenceSnapshotRefreshPolicy: policy
    )

    store.refreshReferenceSnapshotNow()
    expect(
        store.isReferenceSnapshotRefreshInFlight,
        "manual remote refresh should publish its in-flight state immediately"
    )
    try await waitForHistoryCount(11, in: store)

    let maintenanceRequests = await gateway.recordedLoadSnapshotMaintenanceRequests()
    let referenceRefreshRequests = await gateway.recordedLoadSnapshotReferenceRefreshRequests()
    expect(
        maintenanceRequests == [false] && referenceRefreshRequests == [true],
        "manual remote refresh should bypass the fixed schedule without startup maintenance"
    )
    expect(
        !store.isReferenceSnapshotRefreshInFlight,
        "manual remote refresh should clear its in-flight state after completion"
    )
    expect(
        store.referenceSnapshotRefreshFeedbackStatus == "refreshed",
        "manual remote refresh should publish its terminal feedback status"
    )
}

@MainActor
private func verifyManualReferenceRefreshAcknowledgesUnchangedResults() async throws {
    let initial = try decodedSnapshot(historyCount: 0)
    let refreshed = try decodedSnapshot(historyCount: 12)
    let gateway = StubBridgeGateway(
        patchResult: .failure(.unexpectedCall),
        loadSnapshots: [refreshed],
        manualReferenceRefreshStatus: "not_modified"
    )
    let store = makeStore(gateway: gateway, initialSnapshot: initial)

    store.refreshReferenceSnapshotNow()
    try await waitForHistoryCount(12, in: store)
    expect(
        store.referenceSnapshotRefreshFeedbackStatus == "not_modified",
        "manual refresh should distinguish an already-current reference batch"
    )
}

@MainActor
private func verifyWakeRefreshUsesTheRemoteRefreshGate() async throws {
    let initial = try decodedSnapshot(historyCount: 0)
    let woke = try decodedSnapshot(historyCount: 6)
    let gateway = StubBridgeGateway(
        patchResult: .failure(.unexpectedCall),
        loadSnapshots: [woke]
    )
    let store = makeStore(gateway: gateway, initialSnapshot: initial)

    store.resumeGlanceBoundaryRefresh()
    try await waitForHistoryCount(6, in: store)

    let maintenanceRequests = await gateway.recordedLoadSnapshotMaintenanceRequests()
    let referenceRefreshRequests = await gateway.recordedLoadSnapshotReferenceRefreshRequests()
    expect(
        maintenanceRequests == [false] && referenceRefreshRequests == [true],
        "wake should request a due remote refresh without rerunning startup recovery"
    )
}

private func verifyReferenceRefreshPolicyTracksPublicationAndPersistsBackoff() {
    let suiteName = "ModelDial.ReferenceRefreshPolicyTests.\(UUID().uuidString)"
    guard let persistence = UserDefaults(suiteName: suiteName) else {
        expect(false, "a temporary defaults suite should be available")
        return
    }
    defer { persistence.removePersistentDomain(forName: suiteName) }
    let start = Date(timeIntervalSince1970: (5 * 60 * 60) + (54 * 60))
    var policy = ReferenceSnapshotRefreshPolicy(persistence: persistence)
    expect(policy.claimIfDue(now: start), "the first remote refresh should be due")
    policy.record(status: "failed", now: start)

    var afterFailure = ReferenceSnapshotRefreshPolicy(persistence: persistence)
    expect(
        !afterFailure.claimIfDue(now: start.addingTimeInterval(29)),
        "the first failed attempt should survive restart with a thirty-second backoff"
    )
    expect(
        afterFailure.claimIfDue(now: start.addingTimeInterval(30)),
        "the failed refresh should become due after thirty seconds"
    )
    afterFailure.record(
        status: "failed",
        now: start.addingTimeInterval(30)
    )

    var afterSecondFailure = ReferenceSnapshotRefreshPolicy(persistence: persistence)
    expect(
        !afterSecondFailure.claimIfDue(
            now: start.addingTimeInterval(30 + (5 * 60) - 1)
        ),
        "the second failed attempt should keep the five-minute backoff"
    )
    expect(
        afterSecondFailure.claimIfDue(
            now: start.addingTimeInterval(30 + (5 * 60))
        ),
        "the second failed attempt should become due after five minutes"
    )
    afterSecondFailure.record(
        status: "not_modified",
        latestPublishedAt: start,
        now: start.addingTimeInterval(30 + (5 * 60))
    )

    var afterSuccess = ReferenceSnapshotRefreshPolicy(persistence: persistence)
    expect(
        !afterSuccess.claimIfDue(
            now: Date(timeIntervalSince1970: (6 * 60 * 60) - 1)
        ),
        "a successful refresh should wait until the next fixed UTC slot"
    )
    expect(
        afterSuccess.claimIfDue(
            now: Date(timeIntervalSince1970: 6 * 60 * 60)
        ),
        "a successful refresh should become due at 06:00 UTC"
    )

    let exactNoon = Date(timeIntervalSince1970: 12 * 60 * 60)
    expect(
        ReferenceSnapshotRefreshPolicy.nextScheduledRefresh(after: exactNoon)
            == Date(timeIntervalSince1970: 18 * 60 * 60),
        "a refresh completed exactly on a slot should schedule the following slot"
    )

    let sixAM = Date(timeIntervalSince1970: 6 * 60 * 60)
    let previousSlot = Date(timeIntervalSince1970: 0)
    var pending = ReferenceSnapshotRefreshPolicy(persistence: nil)
    expect(pending.claimIfDue(now: sixAM), "the publication boundary should be due")
    pending.record(
        status: "not_modified",
        latestPublishedAt: previousSlot,
        now: sixAM
    )
    expect(
        !pending.isDue(now: sixAM.addingTimeInterval((2 * 60) - 1)),
        "an unchanged previous-slot batch should wait for the first publication retry"
    )
    expect(
        pending.isDue(now: sixAM.addingTimeInterval(2 * 60)),
        "the first publication retry should occur two minutes after the slot"
    )
    pending.record(
        status: "not_modified",
        latestPublishedAt: previousSlot,
        now: sixAM.addingTimeInterval(30 * 60)
    )
    expect(
        pending.isDue(now: sixAM.addingTimeInterval(90 * 60)),
        "a delayed publication should continue with hourly catch-up checks"
    )
    pending.record(
        status: "refreshed",
        latestPublishedAt: sixAM,
        now: sixAM.addingTimeInterval(90 * 60)
    )
    expect(
        !pending.isDue(now: Date(timeIntervalSince1970: (12 * 60 * 60) - 1)),
        "a current-slot batch should stop catch-up polling"
    )
    expect(
        pending.isDue(now: Date(timeIntervalSince1970: 12 * 60 * 60)),
        "a confirmed current batch should schedule the next fixed slot"
    )

    var cachedFallback = ReferenceSnapshotRefreshPolicy(persistence: nil)
    expect(cachedFallback.claimIfDue(now: sixAM), "the cached fallback setup should be due")
    cachedFallback.record(status: "cached", now: sixAM)
    expect(
        !cachedFallback.isDue(now: sixAM.addingTimeInterval(29)),
        "a cached network fallback should use the first failure backoff"
    )
    expect(
        cachedFallback.isDue(now: sixAM.addingTimeInterval(30)),
        "a cached network fallback should retry after thirty seconds"
    )

    let legacyPrefix = "ModelDial.ReferenceRefreshPolicyLegacy.\(UUID().uuidString)"
    persistence.set(
        Date(timeIntervalSince1970: 99_999).timeIntervalSince1970,
        forKey: "\(legacyPrefix).nextAttemptAt"
    )
    persistence.set(3, forKey: "\(legacyPrefix).scheduleVersion")
    let migrated = ReferenceSnapshotRefreshPolicy(
        persistence: persistence,
        persistencePrefix: legacyPrefix
    )
    expect(
        migrated.isDue(now: start),
        "an old relative schedule should be discarded so the fixed schedule can start immediately"
    )
}

@MainActor
private func verifyStartupPublishesCacheBeforeMaintenanceAndRemoteRefresh() async throws {
    let cached = try decodedSnapshot(historyCount: 1)
    let maintained = try decodedSnapshot(historyCount: 2)
    let refreshed = try decodedSnapshot(historyCount: 3)
    let loadGate = BlockingSnapshotGate()
    defer { loadGate.release() }
    let gateway = StubBridgeGateway(
        patchResult: .failure(.unexpectedCall),
        loadSnapshots: [maintained, refreshed],
        firstLoadGate: loadGate,
        snapshotResults: [cached]
    )
    let store = makeStore(gateway: gateway)

    store.refresh()
    try await waitForHistoryCount(1, in: store)
    expect(
        loadGate.hasStarted(),
        "startup maintenance should continue after the cached snapshot is published"
    )
    loadGate.release()
    try await waitForHistoryCount(3, in: store)

    let maintenanceRequests = await gateway.recordedLoadSnapshotMaintenanceRequests()
    let referenceRequests = await gateway.recordedLoadSnapshotReferenceRefreshRequests()
    expect(
        maintenanceRequests == [true, false] && referenceRequests == [false, true],
        "startup should publish cache, maintain local state, then refresh remote data"
    )
}

@MainActor
private func verifyInitialFixtureDoesNotSuppressNormalInitialLoad() async throws {
    let initial = try decodedSnapshot(historyCount: 0)
    let loaded = try decodedSnapshot(historyCount: 14)
    let refreshed = try decodedSnapshot(historyCount: 15)
    let gateway = StubBridgeGateway(
        patchResult: .failure(.unexpectedCall),
        loadSnapshots: [loaded, refreshed]
    )
    let store = makeStore(gateway: gateway, initialSnapshot: initial)

    expect(
        store.snapshot?.runtime.historyCount == 0,
        "the constructor fixture should be available before the first load"
    )
    store.refresh()
    try await waitForHistoryCount(15, in: store)

    let maintenanceRequests = await gateway.recordedLoadSnapshotMaintenanceRequests()
    let referenceRequests = await gateway.recordedLoadSnapshotReferenceRefreshRequests()
    expect(
        maintenanceRequests == [true, false] && referenceRequests == [false, true],
        "an initial fixture must not suppress startup maintenance or remote refresh"
    )
}

@MainActor
private func verifyLocalRadarConsumesBackendEligibilityDecisions() throws {
    let currentID = "current"
    let candidateID = "candidate"
    let routeMismatchID = "route-mismatch"
    let graderMismatchID = "grader-mismatch"
    let hardFailureID = "hard-failure"
    let decisions: [[String: Any]] = [
        [
            "model_configuration_id": candidateID,
            "status": "eligible",
            "reasons": [],
        ],
        [
            "model_configuration_id": routeMismatchID,
            "status": "testable",
            "reasons": ["route_mismatch"],
        ],
        [
            "model_configuration_id": graderMismatchID,
            "status": "testable",
            "reasons": ["grader_version_mismatch"],
        ],
        [
            "model_configuration_id": hardFailureID,
            "status": "testable",
            "reasons": ["hard_failure"],
        ],
    ]
    let allCandidateIDs = [
        candidateID,
        routeMismatchID,
        graderMismatchID,
        hardFailureID,
    ]
    let snapshot = try decodedSnapshot(historyCount: 1) { payload in
        payload["stable_dashboard"] = NSNull()
        var dashboard = payload["dashboard"] as! [String: Any]
        dashboard["leaderboard"] = ([currentID] + allCandidateIDs).enumerated().map {
            index, id in
            leaderboardEntryPayload(
                id: id,
                model: "local-\(id)",
                score: 70 + index
            )
        }
        payload["dashboard"] = dashboard
        payload["advisor_v2_evidence"] = advisorEvidencePayload(
            currentID: currentID,
            eligibleCandidateIDs: allCandidateIDs,
            decisions: decisions,
            resolvedDataSource: "local_evaluation"
        )
        payload["recommendation_portfolio_v2"] = recommendationPortfolioPayload(
            currentID: currentID,
            candidateID: candidateID,
            resolvedDataSource: "local_evaluation"
        )
    }
    let store = makeStore(
        gateway: StubBridgeGateway(patchResult: .failure(.unexpectedCall)),
        initialSnapshot: snapshot
    )

    let displayedIDs = Set(store.radarLeaderboardItems.map(\.id))
    expect(store.radarDisplaySource == "local_evaluation", "local evidence should own the radar source")
    expect(
        displayedIDs == Set([currentID, candidateID]),
        "route, grader, and hard-failure exclusions should come from backend candidate decisions"
    )
    expect(
        store.radarLeaderboardItems.first(where: { $0.id == candidateID }) != nil,
        "backend-eligible rows should display even when dashboard compatibility and freshness fields are stale"
    )
}

@MainActor
private func verifyAutoSourceUsesPortfolioResolvedOfficialIdentity() throws {
    let currentID = "current"
    let candidateID = "candidate"
    let snapshot = try decodedSnapshot(historyCount: 1) { payload in
        payload["stable_dashboard"] = NSNull()
        var runtime = payload["runtime"] as! [String: Any]
        runtime["enabled_target_count"] = 2
        payload["runtime"] = runtime
        var dashboard = payload["dashboard"] as! [String: Any]
        dashboard["leaderboard"] = [
            leaderboardEntryPayload(id: currentID, model: "local-current", score: 20),
            leaderboardEntryPayload(id: candidateID, model: "local-candidate", score: 30),
        ]
        payload["dashboard"] = dashboard
        payload["advisor_v2_evidence"] = advisorEvidencePayload(
            currentID: currentID,
            eligibleCandidateIDs: [candidateID],
            decisions: [[
                "model_configuration_id": candidateID,
                "status": "eligible",
                "reasons": [],
            ]],
            resolvedDataSource: "local_evaluation"
        )
        payload["recommendation_portfolio_v2"] = recommendationPortfolioPayload(
            currentID: currentID,
            candidateID: candidateID,
            resolvedDataSource: "official_snapshot"
        )
        payload["reference_snapshot_feed"] = referenceFeedPayload(entries: [
            referenceEntryPayload(id: currentID, model: "official-current", score: 80),
            referenceEntryPayload(id: candidateID, model: "official-candidate", score: 90),
        ])
    }
    let store = makeStore(
        gateway: StubBridgeGateway(patchResult: .failure(.unexpectedCall)),
        initialSnapshot: snapshot
    )

    expect(
        store.radarSelectedSourceMode == "auto",
        "the fixture should exercise automatic source selection"
    )
    expect(
        store.radarDisplaySource == "official_snapshot",
        "portfolio resolved source should remain authoritative when requested mode is auto"
    )
    expect(
        store.radarLeaderboardItems.first(where: { $0.id == candidateID })?.modelName
            == "official-candidate",
        "official rows should win over stale local rows under auto mode"
    )
    expect(
        store.glancePresentation.peekLeftPrimary == "official-candidate",
        "the glance recommendation identity should come from the portfolio resolved source"
    )
}

@MainActor
private func verifyAutoSourceFallsBackToRemoteProjectionAndMatchesCanonicalIdentity() throws {
    let currentID = "codex-local-default:gpt-5.6-sol:xhigh"
    let candidateID = "codex-local-default:gpt-5.6-sol:max"
    let remoteCurrentID = "cloudflare-reference:gpt-5.6-sol:xhigh"
    let remoteCandidateID = "cloudflare-reference:gpt-5.6-sol:max"
    let snapshot = try decodedSnapshot(historyCount: 1) { payload in
        var config = payload["config"] as! [String: Any]
        config["model_ingress"] = [
            "sources": [[
                "id": "codex_local",
                "kind": "codex",
                "title": "Codex",
                "description": "Fixture",
                "mode": "local",
                "enabled": true,
            ]],
            "connections": [[
                "id": "codex-local-default",
                "source_id": "codex_local",
                "name": "Codex",
                "enabled": true,
                "provider_preset": "generic",
                "provider_id": "codex",
                "model_candidates": [
                    [
                        "id": currentID,
                        "connection_id": "codex-local-default",
                        "model_id": "gpt-5.6-sol",
                        "display_name": "GPT-5.6 Sol XHigh",
                        "enabled": true,
                        "scan_profile": "xhigh",
                        "capabilities": [],
                    ],
                    [
                        "id": candidateID,
                        "connection_id": "codex-local-default",
                        "model_id": "gpt-5.6-sol",
                        "display_name": "GPT-5.6 Sol Max",
                        "enabled": true,
                        "scan_profile": "max",
                        "capabilities": [],
                    ],
                ],
            ]],
        ]
        payload["config"] = config
        var evidence = advisorEvidencePayload(
            currentID: currentID,
            eligibleCandidateIDs: [],
            decisions: [],
            resolvedDataSource: "official_snapshot"
        )
        evidence["resolved_data_source"] = NSNull()
        evidence["current_status"] = "needs_test"
        payload["advisor_v2_evidence"] = evidence
        var portfolio = recommendationPortfolioPayload(
            currentID: currentID,
            candidateID: candidateID,
            resolvedDataSource: "official_snapshot"
        )
        portfolio["resolved_data_source"] = NSNull()
        portfolio["status"] = "needs_test"
        payload["recommendation_portfolio_v2"] = portfolio
        var unverifiedEntry = referenceEntryPayload(
            id: "cloudflare-reference:unverified",
            model: "unverified-model",
            score: 40,
            providerID: "codex",
            effort: "high"
        )
        unverifiedEntry["advisor_eligible"] = false
        payload["reference_snapshot_feed"] = referenceFeedPayload(
            entries: [
                referenceEntryPayload(
                    id: remoteCandidateID,
                    model: "gpt-5.6-sol",
                    score: 85,
                    providerID: "codex",
                    effort: "max"
                ),
                referenceEntryPayload(
                    id: remoteCurrentID,
                    model: "gpt-5.6-sol",
                    score: 85,
                    providerID: "codex",
                    effort: "xhigh"
                ),
                unverifiedEntry,
            ],
            leaderboardOrder: [
                remoteCurrentID,
                remoteCandidateID,
                "cloudflare-reference:unverified",
            ],
            recommendedID: remoteCurrentID
        )
    }
    let store = makeStore(
        gateway: StubBridgeGateway(patchResult: .failure(.unexpectedCall)),
        initialSnapshot: snapshot
    )

    expect(
        store.radarDisplaySource == "official_snapshot",
        "auto mode should display a valid remote feed when no local evidence is actionable"
    )
    expect(
        store.radarLeaderboardItems.map(\.id) == [remoteCurrentID, remoteCandidateID],
        "remote rows must follow the publisher projection order"
    )
    expect(
        store.radarLeaderboardItems.first?.isCurrent == true,
        "remote identity should match the local current model and effort"
    )
    expect(
        store.radarLeaderboardItems.last?.isRecommended == true,
        "remote identity should match a locally keyed portfolio candidate without replacing its source id"
    )
    expect(
        store.glancePresentation.peekLeftPrimary == "远端榜单可用",
        "remote-only evidence should not force a local run in the capsule"
    )
}

@MainActor
private func verifyAmbiguousRemoteIdentityFailsClosed() throws {
    let currentID = "local:shared-high"
    let candidateID = "local:shared-high-alt"
    let snapshot = try decodedSnapshot(historyCount: 1) { payload in
        var config = payload["config"] as! [String: Any]
        config["model_ingress"] = [
            "sources": [[
                "id": "codex_local",
                "kind": "codex",
                "title": "Codex",
                "description": "Fixture",
                "mode": "local",
                "enabled": true,
            ]],
            "connections": [[
                "id": "codex-local-default",
                "source_id": "codex_local",
                "name": "Codex",
                "enabled": true,
                "provider_preset": "generic",
                "provider_id": "codex",
                "model_candidates": [
                    [
                        "id": currentID,
                        "connection_id": "codex-local-default",
                        "model_id": "shared-model",
                        "display_name": "Shared High",
                        "enabled": true,
                        "scan_profile": "high",
                        "capabilities": [],
                    ],
                    [
                        "id": candidateID,
                        "connection_id": "codex-local-default",
                        "model_id": "shared-model",
                        "display_name": "Shared High Alt",
                        "enabled": true,
                        "scan_profile": "high",
                        "capabilities": [],
                    ],
                ],
            ]],
        ]
        payload["config"] = config
        var evidence = advisorEvidencePayload(
            currentID: currentID,
            eligibleCandidateIDs: [],
            decisions: [],
            resolvedDataSource: "official_snapshot"
        )
        evidence["resolved_data_source"] = NSNull()
        evidence["current_status"] = "needs_test"
        payload["advisor_v2_evidence"] = evidence
        var portfolio = recommendationPortfolioPayload(
            currentID: currentID,
            candidateID: candidateID,
            resolvedDataSource: "official_snapshot"
        )
        portfolio["resolved_data_source"] = NSNull()
        portfolio["status"] = "needs_test"
        payload["recommendation_portfolio_v2"] = portfolio
        payload["reference_snapshot_feed"] = referenceFeedPayload(entries: [
            referenceEntryPayload(
                id: "remote-shared-a",
                model: "shared-model",
                score: 80,
                providerID: "codex",
                effort: "high"
            ),
            referenceEntryPayload(
                id: "remote-shared-b",
                model: "shared-model",
                score: 78,
                providerID: "codex",
                effort: "high"
            ),
        ], leaderboardOrder: ["remote-shared-a", "remote-shared-b"])
    }
    let store = makeStore(
        gateway: StubBridgeGateway(patchResult: .failure(.unexpectedCall)),
        initialSnapshot: snapshot
    )

    expect(
        store.radarLeaderboardItems.filter(\.isCurrent).isEmpty,
        "ambiguous remote identities must not mark multiple rows as current"
    )
    expect(
        store.radarLeaderboardItem(for: currentID) == nil,
        "ambiguous remote identity lookup must fail closed"
    )
}

@MainActor
private func verifySuccessfulPatchPublishesExactlyOnce() async throws {
    let initial = try decodedSnapshot(historyCount: 0)
    let saved = try decodedSnapshot(historyCount: 9)
    let gateway = StubBridgeGateway(patchResult: .success(saved))
    let store = makeStore(gateway: gateway, initialSnapshot: initial)

    var publicationCount = 0
    let observation = store.$snapshot.dropFirst().sink { _ in
        publicationCount += 1
    }
    defer { observation.cancel() }

    let savedConfig = try await store.applySettingsPatch(.schedulerEnabled(true))

    expect(publicationCount == 1, "a saved snapshot should be published exactly once")
    expect(store.snapshot?.runtime.historyCount == 9, "the authoritative saved snapshot should win")
    expect(
        savedConfig.scheduler.enabled == saved.config.scheduler.enabled,
        "the settings draft should receive config from the accepted snapshot"
    )
}

@MainActor
private func verifyFailedPatchDoesNotPolluteSnapshot() async throws {
    let initial = try decodedSnapshot(historyCount: 3)
    let gateway = StubBridgeGateway(
        patchResult: .failure(.expectedFailure)
    )
    let store = makeStore(gateway: gateway, initialSnapshot: initial)

    var publicationCount = 0
    let observation = store.$snapshot.dropFirst().sink { _ in
        publicationCount += 1
    }
    defer { observation.cancel() }

    do {
        _ = try await store.applySettingsPatch(.schedulerEnabled(true))
        expect(false, "failed bridge patches should throw")
    } catch StubGatewayError.expectedFailure {
        expect(true, "expected bridge failure should propagate")
    }

    expect(publicationCount == 0, "a failed patch must not publish a snapshot")
    expect(store.snapshot?.runtime.historyCount == 3, "a failed patch must preserve the current snapshot")
}

@MainActor
private func verifyEndpointCommandsPublishAuthoritativeSnapshotsOnce() async throws {
    let initial = try decodedSnapshot(historyCount: 4)
    let saved = try decodedSnapshot(historyCount: 12)
    let gateway = StubBridgeGateway(
        patchResult: .failure(.unexpectedCall),
        endpointResult: .success(saved)
    )
    let store = makeStore(gateway: gateway, initialSnapshot: initial)

    var publicationCount = 0
    let observation = store.$snapshot.dropFirst().sink { _ in
        publicationCount += 1
    }
    defer { observation.cancel() }

    let savedConfig = try await store.upsertSettingsEndpoint(endpointUpsertIntent())

    expect(publicationCount == 1, "endpoint upsert should publish its authoritative snapshot once")
    expect(store.snapshot?.runtime.historyCount == 12, "endpoint upsert should accept backend state")
    expect(
        savedConfig.modelIngress.connections.map(\.id)
            == saved.config.modelIngress.connections.map(\.id),
        "endpoint upsert should return config from accepted backend state"
    )
}

@MainActor
private func verifyFailedEndpointCommandDoesNotPolluteSnapshot() async throws {
    let initial = try decodedSnapshot(historyCount: 5)
    let gateway = StubBridgeGateway(
        patchResult: .failure(.unexpectedCall),
        endpointResult: .failure(.expectedFailure)
    )
    let store = makeStore(gateway: gateway, initialSnapshot: initial)

    var publicationCount = 0
    let observation = store.$snapshot.dropFirst().sink { _ in
        publicationCount += 1
    }
    defer { observation.cancel() }

    do {
        _ = try await store.addSettingsEndpointModels(endpointModelsIntent())
        expect(false, "failed endpoint model commands should throw")
    } catch StubGatewayError.expectedFailure {
        expect(true, "expected endpoint command failure should propagate")
    }

    expect(publicationCount == 0, "a failed endpoint command must not publish a snapshot")
    expect(store.snapshot?.runtime.historyCount == 5, "a failed endpoint command must preserve state")
}

@MainActor
private func verifyCustomScanPreviewParametersReachGateway() async throws {
    let gateway = StubBridgeGateway(
        patchResult: .failure(.unexpectedCall)
    )
    let store = makeStore(gateway: gateway)

    do {
        _ = try await store.previewCustomScanOptions(
            candidateIDs: ["candidate-b", "candidate-a"],
            evaluationProfileID: "quick"
        )
        expect(false, "the preview stub should surface its expected failure")
    } catch StubGatewayError.expectedFailure {
        expect(true, "the preview gateway failure should propagate")
    }

    let request = await gateway.recordedCustomScanPreviewRequest()
    expect(
        request?.candidateIDs == ["candidate-b", "candidate-a"],
        "custom scan candidate ids should reach the gateway without projection"
    )
    expect(
        request?.evaluationProfileID == "quick",
        "the evaluation profile id should reach the gateway"
    )
}

@MainActor
private func verifyTypedScanIntentReachesGatewayWithoutLocalPlanning() async throws {
    let gateway = StubBridgeGateway(
        patchResult: .failure(.unexpectedCall)
    )
    let store = makeStore(gateway: gateway)
    let intent = BridgeScanIntent(
        candidateIDs: ["candidate-b", "candidate-a"],
        selectionMode: .single
    )

    do {
        _ = try await store.previewScan(intent)
        expect(false, "the preview stub should surface its expected failure")
    } catch StubGatewayError.expectedFailure {
        expect(true, "expected scan preview failure should propagate")
    }

    let request = await gateway.recordedScanPreviewRequest()
    expect(request == intent, "scan intent should reach the gateway without local projection")
}

@MainActor
private func verifyScanEntryPointsPreviewBackendPlans() async throws {
    let quickGateway = StubBridgeGateway(patchResult: .failure(.unexpectedCall))
    let quickStore = makeStore(
        gateway: quickGateway,
        initialSnapshot: try scanPlanningSnapshot()
    )
    quickStore.selectEvaluationProfile("quick")
    quickStore.startRegularScan()
    for _ in 0..<100 {
        if !(await quickGateway.recordedScanPreviewRequests()).isEmpty { break }
        try await Task.sleep(nanoseconds: 5_000_000)
    }
    let quickIntent = await quickGateway.recordedScanPreviewRequest()
    expect(quickIntent?.selectionMode == .regular, "regular quick scan should preview regular mode")
    expect(quickIntent?.evaluationProfileID == "quick", "regular quick scan should send only the requested profile")
    expect(quickIntent?.candidateIDs == nil, "Swift must not choose the regular quick candidate pair")

    let upgradeGateway = StubBridgeGateway(patchResult: .failure(.unexpectedCall))
    let upgradeStore = makeStore(
        gateway: upgradeGateway,
        initialSnapshot: try scanPlanningSnapshot()
    )
    upgradeStore.upgradeCurrentEvaluationProfile()
    for _ in 0..<100 {
        if !(await upgradeGateway.recordedScanPreviewRequests()).isEmpty { break }
        try await Task.sleep(nanoseconds: 5_000_000)
    }
    let upgradeIntent = await upgradeGateway.recordedScanPreviewRequest()
    expect(upgradeIntent?.upgradeFromRunID == "run-quick", "profile upgrade should identify only its source run")
    expect(upgradeIntent?.candidateIDs == nil, "backend preview should choose upgrade candidates")
    expect(upgradeIntent?.evaluationProfileID == nil, "backend preview should choose the upgrade target profile")

    let selectedGateway = StubBridgeGateway(patchResult: .failure(.unexpectedCall))
    let selectedStore = makeStore(
        gateway: selectedGateway,
        initialSnapshot: try scanPlanningSnapshot()
    )
    selectedStore.upgradeCurrentSelectionEvaluationProfile(
        profileID: "full",
        candidateIDs: ["candidate-b"]
    )
    for _ in 0..<100 {
        if !(await selectedGateway.recordedScanPreviewRequests()).isEmpty { break }
        try await Task.sleep(nanoseconds: 5_000_000)
    }
    let selectedIntent = await selectedGateway.recordedScanPreviewRequest()
    expect(selectedIntent?.upgradeFromRunID == "run-quick", "selected upgrade should still preview its source run")
    expect(selectedIntent?.candidateIDs == ["candidate-b"], "explicit user candidate intent should reach the backend unchanged")
    expect(selectedIntent?.evaluationProfileID == "full", "explicit user profile intent should reach the backend unchanged")
}

@MainActor
private func verifyScanControlUsesGatewayActor() async throws {
    let gateway = StubBridgeGateway(
        patchResult: .failure(.unexpectedCall),
        scanControlResult: .failure(.expectedFailure)
    )
    let store = makeStore(
        gateway: gateway,
        initialSnapshot: try decodedSnapshot(historyCount: 6, isRunning: true)
    )

    store.pauseScan()
    for _ in 0..<100 {
        if !(await gateway.recordedScanControlActions()).isEmpty { break }
        try await Task.sleep(nanoseconds: 5_000_000)
    }

    let actions = await gateway.recordedScanControlActions()
    expect(
        actions == ["pause"],
        "pause should be routed through the command gateway actor"
    )
    let clientSessionIDs = await gateway.recordedScanControlClientSessionIDs()
    expect(
        clientSessionIDs == ["test-client-session"],
        "pause should carry the app client session id"
    )
    for _ in 0..<100 {
        if store.pendingScanControlAction == nil { break }
        try await Task.sleep(nanoseconds: 5_000_000)
    }
    expect(
        store.pendingScanControlAction == nil,
        "a failed control command should clear pending UI state"
    )
    expect(
        store.transientMessage == StubGatewayError.expectedFailure.localizedDescription,
        "a failed control command should expose the gateway error"
    )
}

@MainActor
private func verifyStopPendingSurvivesIdleRefreshUntilBridgeCompletes() async throws {
    let initial = try decodedSnapshot(historyCount: 6)
    let terminal = try decodedSnapshot(historyCount: 6)
    let streaming = try makeStreamingBridge(
        events: [],
        trailingDelay: 3
    )
    defer { try? FileManager.default.removeItem(at: streaming.root) }
    let gateway = StubBridgeGateway(
        patchResult: .failure(.unexpectedCall),
        scanControlResult: .success(
            BridgeScanControlResponse(
                ok: true,
                action: "stop",
                message: "正在停止"
            )
        ),
        loadSnapshots: [terminal, terminal]
    )
    let store = makeStore(
        gateway: gateway,
        bridge: streaming.client,
        initialSnapshot: initial
    )

    store.startRegularScan()
    expect(store.isScanOperationActive, "starting a bridge scan should enter the active operation state")
    store.stopScan()
    for _ in 0..<100 {
        if !(await gateway.recordedScanControlActions()).isEmpty { break }
        try await Task.sleep(nanoseconds: 5_000_000)
    }
    let recordedControlActions = await gateway.recordedScanControlActions()
    expect(
        !recordedControlActions.isEmpty,
        "stop control did not reach the gateway"
    )
    store.refresh()
    for _ in 0..<100 {
        if !(await gateway.recordedLoadSnapshotMaintenanceRequests()).isEmpty { break }
        try await Task.sleep(nanoseconds: 5_000_000)
    }
    let recordedRefreshRequests = await gateway.recordedLoadSnapshotMaintenanceRequests()
    expect(
        !recordedRefreshRequests.isEmpty,
        "idle refresh did not complete"
    )

    expect(
        store.pendingScanControlAction == "stop",
        "an idle refresh must not clear stop while the bridge process is still active"
    )
    expect(
        store.glancePresentation.state == .stopping,
        "the UI must remain stopping while the bridge process is still active"
    )

    try await waitUntil("bridge completion did not clear stop state", attempts: 800) {
        store.pendingScanControlAction == nil && !store.isScanOperationActive
    }
}

@MainActor
private func verifyPublicSettingsSettersEmitTypedGatewayPatches() async throws {
    let initial = try configuredSnapshot(
        historyCount: 1,
        historyLimit: 40,
        schedulerEnabled: false
    )
    let saved = try configuredSnapshot(
        historyCount: 2,
        historyLimit: 80,
        schedulerEnabled: true
    )
    let gateway = StubBridgeGateway(patchResult: .success(saved))
    let sessionStore = makeStore(gateway: gateway, initialSnapshot: initial)
    let settings = SelectionSettingsStore(sessionStore: sessionStore)

    settings.setModelCandidateEnabled(
        connectionID: "connection-a",
        candidateID: "candidate-a",
        enabled: true
    )
    settings.setCurrentDefault(candidateID: "candidate-a")
    settings.useAutomaticCurrentModel()
    settings.setRecommendationPreference("quality")
    settings.setSourceMode("local_evaluation", configurationID: "candidate-a")
    settings.setProjectTaskProfile(name: "Project", taskMode: "测试验证")
    settings.setScanBudget(
        enabled: true,
        maxDurationSeconds: 300,
        maxReferenceCostUsd: 1.5
    )
    settings.setScanExecution(
        maxConcurrentTargets: 2,
        executionTimeoutSeconds: 600,
        timeoutRetryCount: 1
    )
    settings.setModelCandidatesEnabled(
        connectionID: "connection-a",
        candidateIDs: ["candidate-a", "candidate-b"],
        enabled: false
    )
    settings.addDiscoveredLocalCandidate(
        connectionID: "local-a",
        candidate: BridgeLocalModelDiscoveryCandidate(
            id: "local-model",
            modelId: "model-local",
            modelDisplayName: "Model Local",
            displayName: "Model Local high",
            scanProfile: "high",
            isDefault: false,
            configured: false
        )
    )
    settings.setConnectionEnabled(connectionID: "connection-a", enabled: false)
    settings.setScheduler(mode: "interval", intervalSeconds: 3600)
    settings.setSchedulerEnabled(true)
    settings.setSchedulerMode("weekly")
    settings.setDailySchedule(hour: 8, minute: 30)
    settings.setWeeklySchedule(weekday: 2, hour: 9, minute: 45)
    settings.setScheduledEvaluationProfile("full")

    try await waitForSettingsSave(settings)
    let operations = try patchOperations(await gateway.recordedPatchPayloads())
    expect(
        operations == [
            "model_candidates_enabled",
            "current_default",
            "automatic_current_model",
            "recommendation_preference",
            "source_mode",
            "project_task_profile",
            "scan_budget",
            "scan_execution",
            "model_candidates_enabled",
            "add_discovered_local_candidate",
            "connection_enabled",
            "scheduler",
            "scheduler_enabled",
            "scheduler_mode",
            "daily_schedule",
            "weekly_schedule",
            "scheduled_evaluation_profile",
        ],
        "public settings setters should emit typed patch operations in request order"
    )
    expect(
        sessionStore.snapshot?.runtime.historyCount == 2,
        "typed settings patches should publish their authoritative command snapshot"
    )
    expect(
        settings.draftConfig?.system.historyLimit == 80,
        "settings draft should finish from the authoritative command snapshot"
    )
    let snapshotCallCount = await gateway.recordedSnapshotCallCount()
    let refreshRequests = await gateway.recordedLoadSnapshotMaintenanceRequests()
    expect(
        snapshotCallCount == 0,
        "typed patch commands must not trigger a second snapshot query"
    )
    expect(
        refreshRequests.isEmpty,
        "typed patch commands must not trigger a general refresh"
    )
}

@MainActor
private func verifySettingsPatchQueueIsSerialAndKeepsFinalAuthority() async throws {
    let initial = try configuredSnapshot(
        historyCount: 3,
        historyLimit: 30,
        schedulerEnabled: false
    )
    let first = try configuredSnapshot(
        historyCount: 4,
        historyLimit: 40,
        schedulerEnabled: false
    )
    let second = try configuredSnapshot(
        historyCount: 5,
        historyLimit: 50,
        schedulerEnabled: true
    )
    let gate = BlockingSnapshotGate()
    let gateway = StubBridgeGateway(
        patchResult: .failure(.unexpectedCall),
        patchResults: [.success(first), .success(second)],
        firstPatchGate: gate
    )
    let sessionStore = makeStore(gateway: gateway, initialSnapshot: initial)
    let settings = SelectionSettingsStore(sessionStore: sessionStore)

    settings.setCurrentDefault(candidateID: "candidate-a")
    try await waitUntil("first settings patch should reach the gateway") {
        gate.hasStarted()
    }
    settings.setSchedulerEnabled(true)
    expect(settings.isSaving, "queued settings patches should keep saving feedback active")
    expect(
        settings.saveFeedbackState == .saving,
        "queued settings patches should remain in saving state"
    )
    gate.release()

    try await waitForSettingsSave(settings)
    let operations = try patchOperations(await gateway.recordedPatchPayloads())
    expect(
        operations == ["current_default", "scheduler_enabled"],
        "queued settings patches should reach the gateway serially and in order"
    )
    expect(settings.saveFeedbackState == .saved, "the completed patch queue should report saved")
    expect(
        sessionStore.snapshot?.runtime.historyCount == 5,
        "the final command snapshot should remain authoritative"
    )
    expect(
        settings.draftConfig?.system.historyLimit == 50,
        "the final command config should own the draft"
    )
}

@MainActor
private func verifySettingsCommandsRefreshOnlyThroughAppSessionStore() async throws {
    let initial = try configuredSnapshot(
        historyCount: 6,
        historyLimit: 60,
        schedulerEnabled: false
    )

    let clearSnapshot = try configuredSnapshot(
        historyCount: 7,
        historyLimit: 70,
        schedulerEnabled: false
    )
    let clearResponse = try decodedValue(
        BridgeDataOperationResponse.self,
        from: [
            "ok": true,
            "action": "clear_personal_observations",
            "message": "cleared",
            "removed_file_count": 1,
        ]
    )
    let clearGateway = StubBridgeGateway(
        patchResult: .failure(.unexpectedCall),
        snapshotResults: [clearSnapshot],
        clearResult: clearResponse
    )
    let clearSession = makeStore(gateway: clearGateway, initialSnapshot: initial)
    let clearSettings = SelectionSettingsStore(sessionStore: clearSession)
    var clearPublications = 0
    let clearObservation = clearSession.$snapshot.dropFirst().sink { _ in
        clearPublications += 1
    }
    clearSettings.clearPersonalObservations()
    try await waitForHistoryCount(7, in: clearSession)
    let clearCallCount = await clearGateway.recordedClearCallCount()
    let clearSnapshotCallCount = await clearGateway.recordedSnapshotCallCount()
    expect(clearCallCount == 1, "clear should issue one gateway command")
    expect(clearSnapshotCallCount == 1, "clear should request one authoritative snapshot")
    expect(clearPublications == 1, "clear should publish the authoritative snapshot exactly once")
    expect(clearSettings.draftConfig?.system.historyLimit == 70, "clear should refresh the settings draft")
    clearObservation.cancel()

    let importSnapshot = try configuredSnapshot(
        historyCount: 8,
        historyLimit: 80,
        schedulerEnabled: false
    )
    let importResponse = try decodedValue(
        BridgeLocalImportResponse.self,
        from: [
            "ok": true,
            "provider_id": "codex",
            "source_id": "local-codex",
            "connection_id": "connection-codex",
            "message": "imported",
        ]
    )
    let importGateway = StubBridgeGateway(
        patchResult: .failure(.unexpectedCall),
        snapshotResults: [importSnapshot],
        localImportResult: importResponse
    )
    let importSession = makeStore(gateway: importGateway, initialSnapshot: initial)
    let importSettings = SelectionSettingsStore(sessionStore: importSession)
    var importPublications = 0
    let importObservation = importSession.$snapshot.dropFirst().sink { _ in
        importPublications += 1
    }
    importSettings.importLocalProvider(providerID: "codex")
    try await waitForHistoryCount(8, in: importSession)
    let importedProviderIDs = await importGateway.recordedLocalImportProviderIDs()
    let importSnapshotCallCount = await importGateway.recordedSnapshotCallCount()
    expect(
        importedProviderIDs == ["codex"],
        "local import should issue one typed gateway command"
    )
    expect(importSnapshotCallCount == 1, "local import should request one authoritative snapshot")
    expect(importPublications == 1, "local import should publish the authoritative snapshot exactly once")
    expect(importSettings.draftConfig?.system.historyLimit == 80, "local import should refresh the settings draft")
    importObservation.cancel()

    let testSnapshot = try configuredSnapshot(
        historyCount: 9,
        historyLimit: 90,
        schedulerEnabled: false
    )
    let testResponse = try decodedValue(
        BridgeConnectionTestResponse.self,
        from: [
            "ok": true,
            "status": "ok",
            "error_category": NSNull(),
            "message": "connected",
            "tested_at": "2026-07-29T12:00:00Z",
        ]
    )
    let testGateway = StubBridgeGateway(
        patchResult: .failure(.unexpectedCall),
        snapshotResults: [testSnapshot],
        connectionTestResult: testResponse
    )
    let testSession = makeStore(gateway: testGateway, initialSnapshot: initial)
    let testSettings = SelectionSettingsStore(sessionStore: testSession)
    var testPublications = 0
    let testObservation = testSession.$snapshot.dropFirst().sink { _ in
        testPublications += 1
    }
    testSettings.testConnection(connectionID: "connection-a", modelID: "model-a")
    try await waitForHistoryCount(9, in: testSession)
    let requests = await testGateway.recordedConnectionTestRequests()
    let testSnapshotCallCount = await testGateway.recordedSnapshotCallCount()
    expect(
        requests.count == 1
            && requests.first?.connectionID == "connection-a"
            && requests.first?.modelID == "model-a",
        "connection test should issue one typed gateway command"
    )
    expect(testSnapshotCallCount == 1, "connection test should request one authoritative snapshot")
    expect(testPublications == 1, "connection test should publish the authoritative snapshot exactly once")
    expect(testSettings.draftConfig?.system.historyLimit == 90, "connection test should refresh the settings draft")
    testObservation.cancel()
}

@MainActor
private func verifyRuntimeDeltaAndTerminalSnapshotOwnDistinctStoreUpdates() async throws {
    let initial = try configuredSnapshot(
        historyCount: 7,
        historyLimit: 41,
        schedulerEnabled: false
    )
    let terminalPayload = try snapshotPayload(historyCount: 11) { payload in
        var config = payload["config"] as! [String: Any]
        var system = config["system"] as! [String: Any]
        system["history_limit"] = 88
        config["system"] = system
        payload["config"] = config
        var dashboard = payload["dashboard"] as! [String: Any]
        var runMetadata = dashboard["run_metadata"] as! [String: Any]
        runMetadata["run_id"] = "terminal-dashboard-run"
        dashboard["run_metadata"] = runMetadata
        payload["dashboard"] = dashboard
    }
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase
    let terminalSnapshot = try decoder.decode(
        BridgeSnapshot.self,
        from: JSONSerialization.data(withJSONObject: terminalPayload)
    )
    var runtimeEvent = try scanEventPayload("scan.started")
    runtimeEvent["_delay_after"] = 0.15
    var terminalEvent = try scanEventPayload("scan.finished")
    terminalEvent["state"] = terminalPayload
    terminalEvent["_delay_after"] = 0.1
    let streaming = try makeStreamingBridge(
        events: [runtimeEvent, terminalEvent]
    )
    defer { try? FileManager.default.removeItem(at: streaming.root) }
    let gateway = StubBridgeGateway(
        patchResult: .failure(.unexpectedCall),
        loadSnapshots: [terminalSnapshot]
    )
    let store = makeStore(
        gateway: gateway,
        bridge: streaming.client,
        initialSnapshot: initial
    )

    store.startRegularScan()
    try await waitUntil("runtime delta was not applied") {
        store.snapshot?.runtime.isRunning == true
    }
    expect(
        store.snapshot?.config.system.historyLimit == 41,
        "runtime delta must preserve config"
    )
    expect(
        store.snapshot?.dashboard.runMetadata.runId
            == initial.dashboard.runMetadata.runId,
        "runtime delta must preserve dashboard"
    )
    expect(
        store.snapshot?.questionPack.version == initial.questionPack.version,
        "runtime delta must preserve question pack"
    )

    try await waitForHistoryCount(11, in: store)
    expect(
        store.snapshot?.config.system.historyLimit == 88,
        "terminal snapshot must replace config authoritatively"
    )
    expect(
        store.snapshot?.dashboard.runMetadata.runId == "terminal-dashboard-run",
        "terminal snapshot must replace dashboard authoritatively"
    )
    expect(
        store.snapshot?.runtime.lifecycleState == .idle,
        "terminal snapshot must replace runtime authoritatively"
    )
}

@MainActor
private func verifyBridgeFailureWithoutSnapshotPreservesCurrentState() async throws {
    let initial = try configuredSnapshot(
        historyCount: 5,
        historyLimit: 55,
        schedulerEnabled: false
    )
    let streaming = try makeStreamingBridge(events: [], exitCode: 7)
    defer { try? FileManager.default.removeItem(at: streaming.root) }
    let gateway = StubBridgeGateway(
        patchResult: .failure(.unexpectedCall),
        loadSnapshots: [initial]
    )
    let store = makeStore(
        gateway: gateway,
        bridge: streaming.client,
        initialSnapshot: initial
    )

    store.startRegularScan()
    try await waitUntil("bridge failure event was not observed") {
        store.transientMessage?.contains("状态码 7") == true
    }
    expect(
        store.snapshot?.runtime.historyCount == 5,
        "a bridge failure without state must preserve the current runtime"
    )
    expect(
        store.snapshot?.config.system.historyLimit == 55,
        "a bridge failure without state must preserve the current config"
    )
}

@MainActor
private func verifyStaleRefreshCannotReplaceNewerRuntimeEvent() async throws {
    let initial = try configuredSnapshot(
        historyCount: 3,
        historyLimit: 33,
        schedulerEnabled: false
    )
    let stale = try configuredSnapshot(
        historyCount: 999,
        historyLimit: 999,
        schedulerEnabled: true
    )
    let settled = try configuredSnapshot(
        historyCount: 12,
        historyLimit: 44,
        schedulerEnabled: false
    )
    var runtimeEvent = try scanEventPayload("scan.started")
    runtimeEvent["_delay_after"] = 0.4
    let streaming = try makeStreamingBridge(events: [runtimeEvent])
    defer { try? FileManager.default.removeItem(at: streaming.root) }
    let gate = BlockingSnapshotGate()
    let gateway = StubBridgeGateway(
        patchResult: .failure(.unexpectedCall),
        loadSnapshots: [stale, settled],
        firstLoadGate: gate
    )
    let store = makeStore(
        gateway: gateway,
        bridge: streaming.client,
        initialSnapshot: initial
    )
    var publishedHistoryCounts: [Int] = []
    let observation = store.$snapshot.dropFirst().sink { snapshot in
        if let historyCount = snapshot?.runtime.historyCount {
            publishedHistoryCounts.append(historyCount)
        }
    }
    defer { observation.cancel() }

    store.refresh()
    try await waitUntil("refresh should reach the controlled gateway") {
        gate.hasStarted()
    }
    store.startRegularScan()
    try await waitUntil("runtime event did not arrive while refresh was pending") {
        store.snapshot?.runtime.isRunning == true
    }
    gate.release()

    try await waitForHistoryCount(12, in: store)
    expect(
        !publishedHistoryCounts.contains(999),
        "a refresh started before a runtime event must never publish its stale snapshot"
    )
    expect(
        store.snapshot?.config.system.historyLimit == 44,
        "the post-operation authoritative refresh should still be accepted"
    )
}

@main
private enum AppSessionBridgeOwnershipTestMain {
    static func main() async {
        do {
            try verifyEndpointIntentsContainNoCandidateProjection()
            try await verifySettingsDraftFollowsAuthoritativeSnapshotsWithoutOwnGatewayQuery()
            try await verifySettingsSaveProtectsDraftAndConsumesCommandSnapshot()
            try await verifyPeriodicRefreshObservesAndLoadsAnAuthoritativeSnapshot()
            try await verifyPeriodicObservationFailureStillPublishesSavedSnapshot()
            try await verifyManualReferenceRefreshBypassesTheFixedSchedule()
            try await verifyManualReferenceRefreshAcknowledgesUnchangedResults()
            try await verifyWakeRefreshUsesTheRemoteRefreshGate()
            verifyReferenceRefreshPolicyTracksPublicationAndPersistsBackoff()
            try await verifyStartupPublishesCacheBeforeMaintenanceAndRemoteRefresh()
            try await verifyInitialFixtureDoesNotSuppressNormalInitialLoad()
            try verifyLocalRadarConsumesBackendEligibilityDecisions()
            try verifyAutoSourceUsesPortfolioResolvedOfficialIdentity()
            try verifyAutoSourceFallsBackToRemoteProjectionAndMatchesCanonicalIdentity()
            try verifyAmbiguousRemoteIdentityFailsClosed()
            try await verifySuccessfulPatchPublishesExactlyOnce()
            try await verifyFailedPatchDoesNotPolluteSnapshot()
            try await verifyEndpointCommandsPublishAuthoritativeSnapshotsOnce()
            try await verifyFailedEndpointCommandDoesNotPolluteSnapshot()
            try await verifyCustomScanPreviewParametersReachGateway()
            try await verifyTypedScanIntentReachesGatewayWithoutLocalPlanning()
            try await verifyScanEntryPointsPreviewBackendPlans()
            try await verifyScanControlUsesGatewayActor()
            try await verifyStopPendingSurvivesIdleRefreshUntilBridgeCompletes()
            try await verifyPublicSettingsSettersEmitTypedGatewayPatches()
            try await verifySettingsPatchQueueIsSerialAndKeepsFinalAuthority()
            try await verifySettingsCommandsRefreshOnlyThroughAppSessionStore()
            try await verifyRuntimeDeltaAndTerminalSnapshotOwnDistinctStoreUpdates()
            try await verifyBridgeFailureWithoutSnapshotPreservesCurrentState()
            try await verifyStaleRefreshCannotReplaceNewerRuntimeEvent()
        } catch {
            failureCount += 1
            fputs("FAIL: \(error)\n", stderr)
        }
        if failureCount > 0 {
            exit(1)
        }
        print("App session bridge ownership tests passed")
    }
}
