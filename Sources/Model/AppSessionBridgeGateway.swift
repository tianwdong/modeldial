import Foundation

protocol AppSessionBridgeGatewayProtocol: Actor {
    func loadSnapshot(
        performStartupMaintenance: Bool,
        refreshReference: Bool
    ) throws -> StartupLoadResult
    func observeState(includeCodexInsights: Bool) throws -> BridgeStateObservationResponse
    func snapshot() throws -> BridgeSnapshot
    func requestScanControl(
        _ action: String,
        clientSessionID: String
    ) throws -> BridgeScanControlResponse
    func dismissResumableRun() throws -> BridgeScanControlResponse

    func patchConfig(_ patch: SettingsConfigPatch) throws -> BridgeSnapshot
    func upsertEndpoint(_ intent: BridgeEndpointUpsertIntent) throws -> BridgeSnapshot
    func addEndpointModels(_ intent: BridgeEndpointModelsIntent) throws -> BridgeSnapshot
    func previewCustomScanOptions(
        candidateIDs: [String],
        evaluationProfileID: String?
    ) throws -> BridgeCustomScanPlanOptions
    func previewScan(_ intent: BridgeScanIntent) throws -> BridgeScanPlanPreview
    func discoverModels(connectionID: String) throws -> BridgeModelDiscoveryResponse
    func testConnection(
        connectionID: String,
        modelID: String
    ) throws -> BridgeConnectionTestResponse
    func importLocalProvider(providerID: String) throws -> BridgeLocalImportResponse
    func discoverLocalModels(
        providerID: String
    ) throws -> BridgeLocalModelDiscoveryResponse
    func probeEndpointConnection(
        baseURL: String,
        apiFormat: String,
        providerPreset: String,
        modelID: String,
        scanProfile: String,
        apiKey: String
    ) throws -> BridgeConnectionTestResponse
    func probeEndpointModels(
        baseURL: String,
        apiFormat: String,
        apiKey: String
    ) throws -> BridgeModelDiscoveryResponse
    func exportPersonalObservations(to url: URL) throws
    func clearPersonalObservations() throws -> BridgeDataOperationResponse
}

actor AppSessionBridgeGateway: AppSessionBridgeGatewayProtocol {
    private lazy var bridge = NativeBridgeClient()

    func loadSnapshot(
        performStartupMaintenance: Bool,
        refreshReference: Bool
    ) throws -> StartupLoadResult {
        if performStartupMaintenance {
            return try StartupLoadCoordinator.load(
                recoverRun: { try bridge.recoverRun() },
                observeState: {
                    try bridge.observeState(includeCodexInsights: true)
                },
                snapshot: { try bridge.snapshot() }
            )
        }
        guard refreshReference else {
            return StartupLoadResult(
                snapshot: try bridge.snapshot(),
                warningDetail: nil,
                referenceRefreshStatus: nil
            )
        }
        return try StartupLoadCoordinator.refreshReference(
            refreshReference: { try bridge.refreshReferenceSnapshots() },
            snapshot: { try bridge.snapshot() }
        )
    }

    func observeState(
        includeCodexInsights: Bool
    ) throws -> BridgeStateObservationResponse {
        try bridge.observeState(includeCodexInsights: includeCodexInsights)
    }

    func snapshot() throws -> BridgeSnapshot {
        try bridge.snapshot()
    }

    func requestScanControl(
        _ action: String,
        clientSessionID: String
    ) throws -> BridgeScanControlResponse {
        try bridge.requestScanControl(action, clientSessionID: clientSessionID)
    }

    func dismissResumableRun() throws -> BridgeScanControlResponse {
        try bridge.dismissResumableRun()
    }

    func patchConfig(_ patch: SettingsConfigPatch) throws -> BridgeSnapshot {
        try bridge.patchConfig(patch)
    }

    func upsertEndpoint(_ intent: BridgeEndpointUpsertIntent) throws -> BridgeSnapshot {
        try bridge.upsertEndpoint(intent)
    }

    func addEndpointModels(_ intent: BridgeEndpointModelsIntent) throws -> BridgeSnapshot {
        try bridge.addEndpointModels(intent)
    }

    func previewCustomScanOptions(
        candidateIDs: [String],
        evaluationProfileID: String?
    ) throws -> BridgeCustomScanPlanOptions {
        try bridge.previewCustomScanOptions(
            candidateIDs: candidateIDs,
            evaluationProfileID: evaluationProfileID
        )
    }

    func previewScan(_ intent: BridgeScanIntent) throws -> BridgeScanPlanPreview {
        try bridge.previewScan(intent)
    }

    func discoverModels(connectionID: String) throws -> BridgeModelDiscoveryResponse {
        try bridge.discoverModels(connectionID: connectionID)
    }

    func testConnection(
        connectionID: String,
        modelID: String
    ) throws -> BridgeConnectionTestResponse {
        try bridge.testConnection(connectionID: connectionID, modelID: modelID)
    }

    func importLocalProvider(providerID: String) throws -> BridgeLocalImportResponse {
        try bridge.importLocalProvider(providerID: providerID)
    }

    func discoverLocalModels(
        providerID: String
    ) throws -> BridgeLocalModelDiscoveryResponse {
        try bridge.discoverLocalModels(providerID: providerID)
    }

    func probeEndpointConnection(
        baseURL: String,
        apiFormat: String,
        providerPreset: String,
        modelID: String,
        scanProfile: String,
        apiKey: String
    ) throws -> BridgeConnectionTestResponse {
        try bridge.probeEndpointConnection(
            baseURL: baseURL,
            apiFormat: apiFormat,
            providerPreset: providerPreset,
            modelID: modelID,
            scanProfile: scanProfile,
            apiKey: apiKey
        )
    }

    func probeEndpointModels(
        baseURL: String,
        apiFormat: String,
        apiKey: String
    ) throws -> BridgeModelDiscoveryResponse {
        try bridge.probeEndpointModels(
            baseURL: baseURL,
            apiFormat: apiFormat,
            apiKey: apiKey
        )
    }

    func exportPersonalObservations(to url: URL) throws {
        let data = try bridge.exportPersonalObservations()
        try data.write(to: url, options: .atomic)
    }

    func clearPersonalObservations() throws -> BridgeDataOperationResponse {
        try bridge.clearPersonalObservations()
    }
}
