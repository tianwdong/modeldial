import Darwin
import Foundation

enum BridgeAutoResumeTrigger: String {
    case startup
    case interruption
}

enum BridgeClientError: LocalizedError {
    case missingRepoRoot
    case missingPythonRuntime
    case invalidOutput
    case processFailed(String)
    case processTimedOut(TimeInterval)
    case snapshotDecodeFailed(String)

    var errorDescription: String? {
        switch self {
        case .missingRepoRoot:
            return "ModelDial 本地运行组件不完整，请重新安装最新版本。"
        case .missingPythonRuntime:
            return "ModelDial 本地运行组件不完整，请重新安装最新版本。"
        case .invalidOutput:
            return "ModelDial 本地数据返回异常，请重试。"
        case .processFailed(let detail):
            if detail.contains("Failed to load Python shared library")
                || detail.contains("different Team IDs")
                || detail.contains("Library Validation") {
                return "ModelDial 本地运行组件无法启动，请重新安装最新版本。"
            }
            return "ModelDial 本地数据服务暂时不可用，正在自动重试。"
        case .processTimedOut:
            return "ModelDial 本地数据操作超时，请重试。"
        case .snapshotDecodeFailed:
            return "ModelDial 本地数据与当前版本不兼容，请更新或重新安装。"
        }
    }

    var diagnosticDescription: String {
        switch self {
        case .processFailed(let detail), .snapshotDecodeFailed(let detail):
            return detail
        case .processTimedOut(let timeout):
            return "Native bridge process timed out after \(timeout) seconds"
        case .missingRepoRoot, .missingPythonRuntime, .invalidOutput:
            return errorDescription ?? String(describing: self)
        }
    }
}

struct BridgeFailurePresentation: Equatable {
    let message: String
    let diagnosticDetail: String

    init(error: Error) {
        if let bridgeError = error as? BridgeClientError {
            message = bridgeError.errorDescription
                ?? "ModelDial 本地数据服务暂时不可用，正在自动重试。"
            diagnosticDetail = bridgeError.diagnosticDescription
        } else {
            message = "ModelDial 本地数据服务暂时不可用，正在自动重试。"
            diagnosticDetail = error.localizedDescription
        }
    }
}

struct BridgeEndpointUpsertIntent: Encodable {
    let connectionID: String
    let name: String
    let providerPreset: String
    let apiFormat: String
    let baseURL: String
    let apiKeyReference: String
    let enabled: Bool
    let modelIDs: [String]
    let reasoningProfilesByModel: [String: [String]]
    let defaultReasoningProfileByModel: [String: String]
    let candidateEnabled: Bool
    let lastTestStatus: String?
    let lastTestAt: String?
    let lastTestMessage: String?

    private enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case connectionID = "connection_id"
        case name
        case providerPreset = "provider_preset"
        case apiFormat = "api_format"
        case baseURL = "base_url"
        case apiKeyReference = "api_key_ref"
        case enabled
        case modelIDs = "model_ids"
        case reasoningProfilesByModel = "reasoning_profiles_by_model"
        case defaultReasoningProfileByModel = "default_reasoning_profile_by_model"
        case candidateEnabled = "candidate_enabled"
        case lastTestStatus = "last_test_status"
        case lastTestAt = "last_test_at"
        case lastTestMessage = "last_test_message"
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(1, forKey: .schemaVersion)
        try container.encode(connectionID, forKey: .connectionID)
        try container.encode(name, forKey: .name)
        try container.encode(providerPreset, forKey: .providerPreset)
        try container.encode(apiFormat, forKey: .apiFormat)
        try container.encode(baseURL, forKey: .baseURL)
        try container.encode(apiKeyReference, forKey: .apiKeyReference)
        try container.encode(enabled, forKey: .enabled)
        try container.encode(modelIDs, forKey: .modelIDs)
        try container.encode(reasoningProfilesByModel, forKey: .reasoningProfilesByModel)
        try container.encode(
            defaultReasoningProfileByModel,
            forKey: .defaultReasoningProfileByModel
        )
        try container.encode(candidateEnabled, forKey: .candidateEnabled)
        try encodeNullable(lastTestStatus, forKey: .lastTestStatus, into: &container)
        try encodeNullable(lastTestAt, forKey: .lastTestAt, into: &container)
        try encodeNullable(lastTestMessage, forKey: .lastTestMessage, into: &container)
    }

    private func encodeNullable(
        _ value: String?,
        forKey key: CodingKeys,
        into container: inout KeyedEncodingContainer<CodingKeys>
    ) throws {
        if let value {
            try container.encode(value, forKey: key)
        } else {
            try container.encodeNil(forKey: key)
        }
    }
}

struct BridgeEndpointModelsIntent: Encodable {
    let connectionID: String
    let modelIDs: [String]
    let reasoningProfilesByModel: [String: [String]]
    let defaultReasoningProfileByModel: [String: String]
    let candidateEnabled: Bool

    private enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case connectionID = "connection_id"
        case modelIDs = "model_ids"
        case reasoningProfilesByModel = "reasoning_profiles_by_model"
        case defaultReasoningProfileByModel = "default_reasoning_profile_by_model"
        case candidateEnabled = "candidate_enabled"
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(1, forKey: .schemaVersion)
        try container.encode(connectionID, forKey: .connectionID)
        try container.encode(modelIDs, forKey: .modelIDs)
        try container.encode(reasoningProfilesByModel, forKey: .reasoningProfilesByModel)
        try container.encode(
            defaultReasoningProfileByModel,
            forKey: .defaultReasoningProfileByModel
        )
        try container.encode(candidateEnabled, forKey: .candidateEnabled)
    }
}

final class NativeBridgeClient {
    private let repoRoot: URL
    private let dataDirectory: URL
    private let processTimeout: TimeInterval?
    private let secretStore = AppSecretStore()
    private var activeScanProcess: Process?
    private var activeScanWatchdog: DispatchWorkItem?

    init() {
        let fileManager = FileManager.default
        let bundleURL = Bundle.main.bundleURL
        let developmentRoot = bundleURL.deletingLastPathComponent().deletingLastPathComponent()
        let bundledRoot = Bundle.main.resourceURL?
            .appendingPathComponent("Backend", isDirectory: true)
        repoRoot = [bundledRoot, developmentRoot]
            .compactMap { $0 }
            .first {
                fileManager.isExecutableFile(
                    atPath: $0.appendingPathComponent("Runtime/modeldial-backend").path
                ) || fileManager.fileExists(
                    atPath: $0.appendingPathComponent("scripts/native_bridge.py").path
                )
            } ?? bundledRoot ?? developmentRoot
        #if DEBUG
        let acceptanceDataDirectory = (
            Bundle.main.object(forInfoDictionaryKey: "ModelDialAcceptanceDataDirectory")
                as? String
            ?? ProcessInfo.processInfo.environment["MODELDIAL_ACCEPTANCE_DATA_DIR"]
        )?.trimmingCharacters(in: .whitespacesAndNewlines)
        #else
        let acceptanceDataDirectory: String? = nil
        #endif
        dataDirectory = acceptanceDataDirectory.flatMap { path in
            path.isEmpty ? nil : URL(fileURLWithPath: path, isDirectory: true)
        } ?? fileManager.homeDirectoryForCurrentUser
            .appendingPathComponent("Library", isDirectory: true)
            .appendingPathComponent("Application Support", isDirectory: true)
            .appendingPathComponent("modeldial", isDirectory: true)
        processTimeout = nil
        Self.prepareDataDirectory(
            dataDirectory,
            legacyArtifactsDirectory: Self.legacyArtifactsDirectory(
                selectedRepoRoot: repoRoot,
                developmentRoot: developmentRoot,
                fileManager: fileManager
            ),
            fileManager: fileManager
        )
    }

    init(
        repoRoot: URL,
        dataDirectory: URL,
        processTimeout: TimeInterval? = nil
    ) {
        self.repoRoot = repoRoot
        self.dataDirectory = dataDirectory
        self.processTimeout = processTimeout
    }

    func snapshot() throws -> BridgeSnapshot {
        DebugLog.write("NativeBridgeClient.snapshot begin")
        let output = try run(arguments: ["snapshot", "--include-codex-insights"])
        let data = Data(output.utf8)
        do {
            let snapshot = try JSONDecoder.codexMonitor.decode(BridgeSnapshot.self, from: data)
            DebugLog.write("NativeBridgeClient.snapshot success")
            return snapshot
        } catch let error as DecodingError {
            let detail = Self.decodingErrorDetail(error)
            DebugLog.write("NativeBridgeClient.snapshot decode error=\(detail)")
            throw BridgeClientError.snapshotDecodeFailed(detail)
        }
    }

    func recoverRun() throws -> BridgeRunRecoveryResponse {
        let output = try run(arguments: ["recover-run"])
        let response = try JSONDecoder.codexMonitor.decode(
            BridgeRunRecoveryResponse.self,
            from: Data(output.utf8)
        )
        guard response.ok else {
            throw BridgeClientError.processFailed(response.message)
        }
        return response
    }

    func observeState(
        includeCodexInsights: Bool = false
    ) throws -> BridgeStateObservationResponse {
        let arguments = includeCodexInsights
            ? ["observe-state", "--include-codex-insights"]
            : ["observe-state"]
        let output = try run(arguments: arguments)
        let response = try JSONDecoder.codexMonitor.decode(
            BridgeStateObservationResponse.self,
            from: Data(output.utf8)
        )
        guard response.ok else {
            throw BridgeClientError.processFailed(response.message)
        }
        return response
    }

    func refreshReferenceSnapshots() throws -> BridgeReferenceRefreshResponse {
        let output = try run(arguments: ["refresh-reference"])
        let response = try JSONDecoder.codexMonitor.decode(
            BridgeReferenceRefreshResponse.self,
            from: Data(output.utf8)
        )
        guard response.ok else {
            throw BridgeClientError.processFailed(response.message)
        }
        return response
    }

    func readConfig() throws -> BridgeConfig {
        let output = try run(arguments: ["read-config"])
        return try JSONDecoder.codexMonitor.decode(
            BridgeConfig.self,
            from: Data(output.utf8)
        )
    }

    func refreshSnapshot(includeCodexInsights: Bool = false) throws -> BridgeRefreshSnapshot {
        let arguments = includeCodexInsights
            ? ["refresh-snapshot", "--include-codex-insights"]
            : ["refresh-snapshot"]
        let output = try run(arguments: arguments)
        return try JSONDecoder.codexMonitor.decode(
            BridgeRefreshSnapshot.self,
            from: Data(output.utf8)
        )
    }

    func patchConfig(_ patch: SettingsConfigPatch) throws -> BridgeSnapshot {
        let data = try JSONSerialization.data(
            withJSONObject: patch.commandPayload,
            options: []
        )
        guard let text = String(data: data, encoding: .utf8) else {
            throw BridgeClientError.invalidOutput
        }
        let output = try run(arguments: [
            "patch-config",
            "--include-codex-insights",
            "--payload",
            text,
        ])
        return try JSONDecoder.codexMonitor.decode(
            ConfigSaveResponse.self,
            from: Data(output.utf8)
        ).state
    }

    func upsertEndpoint(_ intent: BridgeEndpointUpsertIntent) throws -> BridgeSnapshot {
        try runEndpointCommand("upsert-endpoint", intent: intent)
    }

    func addEndpointModels(_ intent: BridgeEndpointModelsIntent) throws -> BridgeSnapshot {
        try runEndpointCommand("add-endpoint-models", intent: intent)
    }

    func previewCustomScanOptions(
        candidateIDs: [String],
        evaluationProfileID: String?
    ) throws -> BridgeCustomScanPlanOptions {
        var arguments = ["preview-scan", "--custom-options"]
        for candidateID in candidateIDs {
            arguments.append(contentsOf: ["--candidate-id", candidateID])
        }
        if let evaluationProfileID {
            arguments.append(contentsOf: [
                "--evaluation-profile-id",
                evaluationProfileID,
            ])
        }
        let output = try run(arguments: arguments)
        return try JSONDecoder.codexMonitor.decode(
            BridgeCustomScanPlanOptions.self,
            from: Data(output.utf8)
        )
    }

    func previewScan(_ intent: BridgeScanIntent) throws -> BridgeScanPlanPreview {
        let output = try run(arguments: scanArguments(
            command: "preview-scan",
            intent: intent
        ))
        return try JSONDecoder.codexMonitor.decode(
            BridgeScanPlanPreview.self,
            from: Data(output.utf8)
        )
    }

    func discoverModels(connectionID: String) throws -> BridgeModelDiscoveryResponse {
        let output = try run(arguments: [
            "discover-models",
            "--connection-id",
            connectionID,
        ], secretInput: try secretInput(connectionIDs: [connectionID]))
        return try JSONDecoder.codexMonitor.decode(
            BridgeModelDiscoveryResponse.self,
            from: Data(output.utf8)
        )
    }

    func testConnection(connectionID: String, modelID: String) throws -> BridgeConnectionTestResponse {
        let output = try run(arguments: [
            "test-connection",
            "--connection-id",
            connectionID,
            "--model-id",
            modelID,
        ], secretInput: try secretInput(connectionIDs: [connectionID]))
        return try JSONDecoder.codexMonitor.decode(
            BridgeConnectionTestResponse.self,
            from: Data(output.utf8)
        )
    }

    func importLocalProvider(providerID: String) throws -> BridgeLocalImportResponse {
        let output = try run(arguments: [
            "import-local-provider",
            "--provider-id",
            providerID,
        ])
        return try JSONDecoder.codexMonitor.decode(
            BridgeLocalImportResponse.self,
            from: Data(output.utf8)
        )
    }

    func discoverLocalModels(providerID: String) throws -> BridgeLocalModelDiscoveryResponse {
        let output = try run(arguments: [
            "discover-local-models",
            "--provider-id",
            providerID,
        ])
        return try JSONDecoder.codexMonitor.decode(
            BridgeLocalModelDiscoveryResponse.self,
            from: Data(output.utf8)
        )
    }

    func probeEndpointConnection(
        baseURL: String,
        apiFormat: String,
        providerPreset: String,
        modelID: String,
        scanProfile: String = "default",
        apiKey: String
    ) throws -> BridgeConnectionTestResponse {
        let secretReference = "preview:\(UUID().uuidString.lowercased())"
        let payload: [String: Any] = [
            "base_url": baseURL,
            "api_format": apiFormat,
            "provider_preset": providerPreset,
            "model_id": modelID,
            "scan_profile": scanProfile,
            "api_key_ref": secretReference,
        ]
        let payloadData = try JSONSerialization.data(withJSONObject: payload, options: [])
        let secretData = try JSONSerialization.data(
            withJSONObject: [secretReference: apiKey],
            options: []
        )
        guard let payloadText = String(data: payloadData, encoding: .utf8) else {
            throw BridgeClientError.invalidOutput
        }
        let output = try run(
            arguments: [
                "probe-endpoint",
                "--probe-action",
                "test",
                "--payload",
                payloadText,
            ],
            secretInput: secretData
        )
        return try JSONDecoder.codexMonitor.decode(
            BridgeConnectionTestResponse.self,
            from: Data(output.utf8)
        )
    }

    func probeEndpointModels(
        baseURL: String,
        apiFormat: String,
        apiKey: String
    ) throws -> BridgeModelDiscoveryResponse {
        let secretReference = "preview:\(UUID().uuidString.lowercased())"
        let payloadData = try JSONSerialization.data(withJSONObject: [
            "base_url": baseURL,
            "api_format": apiFormat,
            "api_key_ref": secretReference,
        ], options: [])
        let secretData = try JSONSerialization.data(
            withJSONObject: [secretReference: apiKey],
            options: []
        )
        guard let payloadText = String(data: payloadData, encoding: .utf8) else {
            throw BridgeClientError.invalidOutput
        }
        let output = try run(
            arguments: [
                "probe-endpoint",
                "--probe-action",
                "discover",
                "--payload",
                payloadText,
            ],
            secretInput: secretData
        )
        return try JSONDecoder.codexMonitor.decode(
            BridgeModelDiscoveryResponse.self,
            from: Data(output.utf8)
        )
    }

    func requestScanControl(
        _ action: String,
        clientSessionID: String
    ) throws -> BridgeScanControlResponse {
        let output = try run(arguments: [
            "control-scan",
            "--action",
            action,
            "--client-session-id",
            clientSessionID,
        ])
        return try JSONDecoder.codexMonitor.decode(
            BridgeScanControlResponse.self,
            from: Data(output.utf8)
        )
    }

    func dismissResumableRun() throws -> BridgeScanControlResponse {
        let output = try run(arguments: ["dismiss-resumable"])
        return try JSONDecoder.codexMonitor.decode(
            BridgeScanControlResponse.self,
            from: Data(output.utf8)
        )
    }

    func exportPersonalObservations() throws -> Data {
        let output = try run(arguments: ["export-personal-observations"])
        let data = Data(output.utf8)
        guard (try? JSONSerialization.jsonObject(with: data)) is [String: Any] else {
            throw BridgeClientError.invalidOutput
        }
        return data
    }

    func clearPersonalObservations() throws -> BridgeDataOperationResponse {
        let output = try run(arguments: ["clear-personal-observations"])
        return try JSONDecoder.codexMonitor.decode(
            BridgeDataOperationResponse.self,
            from: Data(output.utf8)
        )
    }

    private func runEndpointCommand<Intent: Encodable>(
        _ command: String,
        intent: Intent
    ) throws -> BridgeSnapshot {
        let data = try JSONEncoder().encode(intent)
        guard let text = String(data: data, encoding: .utf8) else {
            throw BridgeClientError.invalidOutput
        }
        let output = try run(arguments: [
            command,
            "--include-codex-insights",
            "--payload",
            text,
        ])
        return try JSONDecoder.codexMonitor.decode(
            ConfigSaveResponse.self,
            from: Data(output.utf8)
        ).state
    }

    private func scanArguments(
        command: String,
        intent: BridgeScanIntent,
        includeCodexInsights: Bool = false
    ) -> [String] {
        var arguments = [command]
        if includeCodexInsights {
            arguments.append("--include-codex-insights")
        }
        arguments.append(contentsOf: [
            "--selection-mode",
            intent.selectionMode.rawValue,
        ])
        if intent.selectionMode == .custom, let customRoundMode = intent.customRoundMode {
            arguments.append(contentsOf: [
                "--custom-round-mode",
                customRoundMode.rawValue,
            ])
        }
        if intent.forceRestart {
            arguments.append("--force-restart")
        }
        if let evaluationProfileID = intent.evaluationProfileID {
            arguments.append(contentsOf: [
                "--evaluation-profile-id",
                evaluationProfileID,
            ])
        }
        if let upgradeFromRunID = intent.upgradeFromRunID {
            arguments.append(contentsOf: [
                "--upgrade-from-run-id",
                upgradeFromRunID,
            ])
        }
        for candidateID in intent.candidateIDs ?? [] {
            arguments.append(contentsOf: ["--candidate-id", candidateID])
        }
        return arguments
    }

    func startScan(
        intent: BridgeScanIntent,
        onEvent: @escaping (ScanEvent) -> Void,
        onComplete: @escaping () -> Void
    ) throws {
        DebugLog.write("NativeBridgeClient.startScan begin")
        let secretInput = try secretInputForScan(candidateIDs: intent.candidateIDs)
        try startStreamingProcess(
            arguments: scanArguments(
                command: "scan",
                intent: intent,
                includeCodexInsights: true
            ),
            secretInput: secretInput,
            onEvent: onEvent,
            onComplete: onComplete
        )
    }

    func startAutoResume(
        trigger: BridgeAutoResumeTrigger,
        clientSessionID: String,
        onEvent: @escaping (ScanEvent) -> Void,
        onComplete: @escaping () -> Void
    ) throws {
        let secretInput = try secretInputForScan(candidateIDs: nil)
        try startStreamingProcess(
            arguments: [
                "auto-resume",
                "--include-codex-insights",
                "--trigger",
                trigger.rawValue,
                "--client-session-id",
                clientSessionID,
            ],
            secretInput: secretInput,
            onEvent: onEvent,
            onComplete: onComplete
        )
    }

    func startRepair(
        runID: String,
        candidateID: String,
        questionID: String? = nil,
        onEvent: @escaping (ScanEvent) -> Void,
        onComplete: @escaping () -> Void
    ) throws {
        let secretInput = try secretInputForScan(candidateIDs: [candidateID])
        var arguments = [
            "repair-candidate",
            "--include-codex-insights",
            "--run-id",
            runID,
            "--candidate-id",
            candidateID,
        ]
        if let questionID {
            arguments.append(contentsOf: ["--question-id", questionID])
        }
        try startStreamingProcess(
            arguments: arguments,
            secretInput: secretInput,
            onEvent: onEvent,
            onComplete: onComplete
        )
    }

    func startFailedRepair(
        runID: String,
        candidateIDs: [String],
        onEvent: @escaping (ScanEvent) -> Void,
        onComplete: @escaping () -> Void
    ) throws {
        let secretInput = try secretInputForScan(candidateIDs: candidateIDs)
        var arguments = [
            "repair-failures",
            "--include-codex-insights",
            "--run-id",
            runID,
        ]
        for candidateID in candidateIDs {
            arguments.append(contentsOf: ["--candidate-id", candidateID])
        }
        try startStreamingProcess(
            arguments: arguments,
            secretInput: secretInput,
            onEvent: onEvent,
            onComplete: onComplete
        )
    }

    func startTimedOutRepair(
        runID: String,
        candidateIDs: [String],
        onEvent: @escaping (ScanEvent) -> Void,
        onComplete: @escaping () -> Void
    ) throws {
        let secretInput = try secretInputForScan(candidateIDs: candidateIDs)
        var arguments = [
            "repair-timeouts",
            "--include-codex-insights",
            "--run-id",
            runID,
        ]
        for candidateID in candidateIDs {
            arguments.append(contentsOf: ["--candidate-id", candidateID])
        }
        try startStreamingProcess(
            arguments: arguments,
            secretInput: secretInput,
            onEvent: onEvent,
            onComplete: onComplete
        )
    }

    private func startStreamingProcess(
        arguments: [String],
        secretInput: Data?,
        onEvent: @escaping (ScanEvent) -> Void,
        onComplete: @escaping () -> Void
    ) throws {
        let process = Process()
        process.currentDirectoryURL = repoRoot
        var invocation = try bridgeInvocation(arguments)
        if secretInput != nil {
            invocation.arguments.append("--secret-stdin")
        }
        process.executableURL = invocation.executableURL
        process.arguments = invocation.arguments
        process.environment = enrichedEnvironment()
        activeScanProcess = process
        activeScanWatchdog?.cancel()

        let outputPipe = Pipe()
        let errorPipe = Pipe()
        let inputPipe = Pipe()
        process.standardOutput = outputPipe
        process.standardError = errorPipe
        if secretInput != nil {
            process.standardInput = inputPipe
        }
        process.terminationHandler = { process in
            DebugLog.write("NativeBridgeClient.startScan terminated pid=\(process.processIdentifier) status=\(process.terminationStatus)")
        }

        var bufferedOutputData = Data()
        outputPipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if data.isEmpty {
                handle.readabilityHandler = nil
                if !bufferedOutputData.isEmpty {
                    self.consumeScanOutput(bufferedOutputData, onEvent: onEvent)
                    bufferedOutputData.removeAll(keepingCapacity: false)
                }
                if process.isRunning {
                    process.waitUntilExit()
                }
                let terminationStatus = process.terminationStatus
                DebugLog.write("NativeBridgeClient.startScan complete")
                self.activeScanWatchdog?.cancel()
                self.activeScanProcess = nil
                DispatchQueue.main.async {
                    if process.terminationStatus != 0 {
                        onEvent(
                            ScanEvent.bridgeFailure(
                                message: "扫描桥接进程异常退出（状态码 \(terminationStatus)）"
                            )
                        )
                    }
                    onComplete()
                }
                return
            }
            bufferedOutputData.append(data)
            while let newlineIndex = bufferedOutputData.firstIndex(of: 0x0A) {
                let lineData = Data(bufferedOutputData[..<newlineIndex])
                bufferedOutputData.removeSubrange(...newlineIndex)
                self.consumeScanOutput(lineData, onEvent: onEvent)
            }
        }

        var bufferedErrorText = ""
        errorPipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if data.isEmpty {
                handle.readabilityHandler = nil
                if !bufferedErrorText.isEmpty {
                    self.consumeScanLog(bufferedErrorText)
                    bufferedErrorText = ""
                }
                return
            }
            guard let text = String(data: data, encoding: .utf8) else { return }
            bufferedErrorText.append(text)
            let lines = bufferedErrorText.components(separatedBy: "\n")
            bufferedErrorText = lines.last ?? ""
            for line in lines.dropLast() {
                self.consumeScanLog(line)
            }
        }

        try process.run()
        if let secretInput {
            try inputPipe.fileHandleForWriting.write(contentsOf: secretInput)
            try inputPipe.fileHandleForWriting.close()
        }
        DebugLog.write("NativeBridgeClient.startScan process launched pid=\(process.processIdentifier)")
        let watchdog = DispatchWorkItem { [weak process] in
            guard let process else { return }
            if process.isRunning {
                DebugLog.write("NativeBridgeClient.startScan watchdog still_running pid=\(process.processIdentifier)")
            } else {
                DebugLog.write("NativeBridgeClient.startScan watchdog exited pid=\(process.processIdentifier) status=\(process.terminationStatus)")
            }
        }
        activeScanWatchdog = watchdog
        DispatchQueue.global().asyncAfter(deadline: .now() + 15, execute: watchdog)
    }

    private func run(arguments: [String], secretInput: Data? = nil) throws -> String {
        let process = Process()
        process.currentDirectoryURL = repoRoot
        var invocation = try bridgeInvocation(arguments)
        if secretInput != nil {
            invocation.arguments.append("--secret-stdin")
        }
        DebugLog.write("NativeBridgeClient.run begin args=\(invocation.arguments)")
        process.executableURL = invocation.executableURL
        process.arguments = invocation.arguments
        process.environment = enrichedEnvironment()
        let outputPipe = Pipe()
        let errorPipe = Pipe()
        let inputPipe = Pipe()
        process.standardOutput = outputPipe
        process.standardError = errorPipe
        if secretInput != nil {
            process.standardInput = inputPipe
        }
        let terminationSemaphore = DispatchSemaphore(value: 0)
        process.terminationHandler = { _ in
            terminationSemaphore.signal()
        }
        try process.run()

        let outputCollector = BridgeProcessOutputCollector()
        let readerGroup = DispatchGroup()
        readerGroup.enter()
        DispatchQueue.global(qos: .userInitiated).async {
            outputCollector.captureOutput(from: outputPipe.fileHandleForReading)
            readerGroup.leave()
        }
        readerGroup.enter()
        DispatchQueue.global(qos: .userInitiated).async {
            outputCollector.captureError(from: errorPipe.fileHandleForReading)
            readerGroup.leave()
        }

        if let secretInput {
            try inputPipe.fileHandleForWriting.write(contentsOf: secretInput)
            try inputPipe.fileHandleForWriting.close()
        }

        let timeout = processTimeout(for: arguments)
        if terminationSemaphore.wait(timeout: .now() + timeout) == .timedOut {
            DebugLog.write(
                "NativeBridgeClient.run timed out pid=\(process.processIdentifier) timeout=\(timeout)"
            )
            process.terminate()
            if terminationSemaphore.wait(timeout: .now() + 2) == .timedOut {
                Darwin.kill(process.processIdentifier, SIGKILL)
                _ = terminationSemaphore.wait(timeout: .now() + 2)
            }
            try? outputPipe.fileHandleForReading.close()
            try? errorPipe.fileHandleForReading.close()
            _ = readerGroup.wait(timeout: .now() + 2)
            throw BridgeClientError.processTimedOut(timeout)
        }
        if readerGroup.wait(timeout: .now() + 5) == .timedOut {
            try? outputPipe.fileHandleForReading.close()
            try? errorPipe.fileHandleForReading.close()
            throw BridgeClientError.invalidOutput
        }
        let (outputData, errorData) = outputCollector.collectedData()
        DebugLog.write("NativeBridgeClient.run exit status=\(process.terminationStatus)")
        guard let outputText = String(data: outputData, encoding: .utf8),
              let rawErrorText = String(data: errorData, encoding: .utf8) else {
            throw BridgeClientError.invalidOutput
        }
        let errorText = rawErrorText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard process.terminationStatus == 0 else {
            DebugLog.write("NativeBridgeClient.run stderr=\(errorText)")
            throw BridgeClientError.processFailed(errorText)
        }
        return outputText
    }

    private func processTimeout(for arguments: [String]) -> TimeInterval {
        if let processTimeout {
            return max(0.05, processTimeout)
        }
        switch arguments.first {
        case "test-connection", "probe-endpoint":
            return 330
        default:
            return 120
        }
    }

    private static func decodingErrorDetail(_ error: DecodingError) -> String {
        switch error {
        case .keyNotFound(let key, let context):
            return "\(codingPath(context.codingPath + [key])) 缺少必需字段"
        case .valueNotFound(_, let context):
            return "\(codingPath(context.codingPath)) 缺少必需值"
        case .typeMismatch(let type, let context):
            return "\(codingPath(context.codingPath)) 类型不匹配，期望 \(type)"
        case .dataCorrupted(let context):
            return "\(codingPath(context.codingPath)) 数据格式无效"
        @unknown default:
            return String(describing: error)
        }
    }

    private static func codingPath(_ path: [CodingKey]) -> String {
        let value = path.map(\.stringValue).joined(separator: ".")
        return value.isEmpty ? "快照根节点" : value
    }

    private func secretInputForScan(candidateIDs: [String]?) throws -> Data? {
        let config = try readConfig()
        let connectionIDs = config.modelIngress.secretBackedConnectionIDs(
            for: candidateIDs
        )
        return try secretInput(
            config: config,
            connectionIDs: connectionIDs
        )
    }

    private func secretInput(connectionIDs: [String]) throws -> Data? {
        let config = try readConfig()
        return try secretInput(
            config: config,
            connectionIDs: connectionIDs
        )
    }

    private func secretInput(
        config: BridgeConfig,
        connectionIDs: [String]
    ) throws -> Data? {
        let connectionsByID = Dictionary(
            uniqueKeysWithValues: config.modelIngress.connections.map { ($0.id, $0) }
        )
        var secrets: [String: String] = [:]
        var pendingMigrations: [String: (oldReference: String, newReference: String, secret: String)] =
            [:]

        for connectionID in Set(connectionIDs) {
            guard let connection = connectionsByID[connectionID] else { continue }
            guard let resolved = try secretStore.bridgeSecret(
                connectionID: connectionID,
                apiKeyRef: connection.apiKeyRef
            ) else { continue }

            if let migratedReference = resolved.updatedConfigReference,
               migratedReference != resolved.originalReference {
                pendingMigrations[connectionID] = (
                    oldReference: resolved.originalReference,
                    newReference: migratedReference,
                    secret: resolved.secret
                )
            } else {
                secrets[resolved.bridgeReference] = resolved.secret
            }
        }

        if !pendingMigrations.isEmpty {
            if persistSecretReferenceMigrations(
                pendingMigrations.reduce(into: [String: String]()) { partialResult, item in
                    partialResult[item.key] = item.value.newReference
                }
            ) {
                for migration in pendingMigrations.values {
                    secrets[migration.newReference] = migration.secret
                }
                cleanupMigratedSecretReferences(pendingMigrations)
            } else {
                for migration in pendingMigrations.values {
                    secrets[migration.oldReference] = migration.secret
                }
            }
        }
        guard !connectionIDs.isEmpty else { return nil }
        return try JSONSerialization.data(withJSONObject: secrets, options: [])
    }

    private func cleanupMigratedSecretReferences(
        _ migrations: [String: (oldReference: String, newReference: String, secret: String)]
    ) {
        for (connectionID, migration) in migrations {
            try? secretStore.deleteReference(
                migration.oldReference,
                connectionID: connectionID
            )
        }
    }

    private func persistSecretReferenceMigrations(
        _ referencesByConnectionID: [String: String]
    ) -> Bool {
        guard !referencesByConnectionID.isEmpty else { return true }
        do {
            let patch = SettingsConfigPatch.connectionSecretReferences(referencesByConnectionID)
            let data = try JSONSerialization.data(
                withJSONObject: patch.commandPayload,
                options: []
            )
            guard let text = String(data: data, encoding: .utf8) else {
                throw BridgeClientError.invalidOutput
            }
            let output = try run(arguments: [
                "migrate-secret-references",
                "--payload",
                text,
            ])
            let acknowledgement = try JSONDecoder.codexMonitor.decode(
                SecretReferenceMigrationAcknowledgement.self,
                from: Data(output.utf8)
            )
            guard acknowledgement.isAccepted else {
                throw BridgeClientError.invalidOutput
            }
            DebugLog.write("NativeBridgeClient.persistSecretReferenceMigrations success")
            return true
        } catch {
            DebugLog.write(
                "NativeBridgeClient.persistSecretReferenceMigrations error=\(error.localizedDescription)"
            )
            return false
        }
    }

    private func consumeScanOutput(_ data: Data, onEvent: @escaping (ScanEvent) -> Void) {
        guard let line = String(data: data, encoding: .utf8) else {
            DispatchQueue.main.async {
                onEvent(ScanEvent.bridgeDecodeFailure(message: "扫描事件不是有效的 UTF-8 数据"))
            }
            return
        }
        consumeScanOutput(line, onEvent: onEvent)
    }

    private func consumeScanOutput(_ line: String, onEvent: @escaping (ScanEvent) -> Void) {
        let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        guard let eventData = trimmed.data(using: .utf8) else {
            DispatchQueue.main.async {
                onEvent(ScanEvent.bridgeDecodeFailure(message: "扫描事件不是有效的 UTF-8 数据"))
            }
            return
        }
        do {
            let event = try JSONDecoder.codexMonitor.decode(ScanEvent.self, from: eventData)
            DebugLog.write("NativeBridgeClient.startScan event=\(event.type)")
            DispatchQueue.main.async { onEvent(event) }
        } catch let error as DecodingError {
            let detail = Self.decodingErrorDetail(error)
            DebugLog.write("NativeBridgeClient.startScan decode error=\(detail)")
            DispatchQueue.main.async {
                onEvent(
                    ScanEvent.bridgeDecodeFailure(
                        message: "扫描结果数据与当前版本不兼容：\(detail)"
                    )
                )
            }
        } catch {
            DebugLog.write("NativeBridgeClient.startScan decode error=\(error.localizedDescription)")
            DispatchQueue.main.async {
                onEvent(
                    ScanEvent.bridgeDecodeFailure(
                        message: "扫描结果数据无法识别：\(error.localizedDescription)"
                    )
                )
            }
        }
    }

    private func consumeScanLog(_ line: String) {
        let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        DebugLog.write("NativeBridgeClient.startScan stderr=\(trimmed)")
    }

    private func enrichedEnvironment() -> [String: String] {
        var environment = ProcessInfo.processInfo.environment
        let fallbackPathComponents = [
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin",
        ]
        let existingPathComponents = (environment["PATH"] ?? "")
            .split(separator: ":")
            .map(String.init)
        var seenPathComponents = Set<String>()
        let pathComponents = (existingPathComponents + fallbackPathComponents).filter { component in
            guard !component.isEmpty, !seenPathComponents.contains(component) else { return false }
            seenPathComponents.insert(component)
            return true
        }
        environment["PATH"] = pathComponents.joined(separator: ":")
        environment["MODELDIAL_DATA_DIR"] = dataDirectory.path
        environment["MODELDIAL_BACKEND_ROOT"] = repoRoot.path
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        if environment["MODELDIAL_REFERENCE_SNAPSHOT_URL"]?
            .trimmingCharacters(in: .whitespacesAndNewlines).isEmpty != false,
           let bundledReferenceSnapshotURL = Bundle.main.object(
               forInfoDictionaryKey: "ModelDialReferenceSnapshotURL"
           ) as? String {
            let trimmedURL = bundledReferenceSnapshotURL.trimmingCharacters(
                in: .whitespacesAndNewlines
            )
            if !trimmedURL.isEmpty {
                environment["MODELDIAL_REFERENCE_SNAPSHOT_URL"] = trimmedURL
            }
        }

        let existing = environment["PYTHONPATH"]
        if let existing, !existing.isEmpty {
            environment["PYTHONPATH"] = "\(repoRoot.path):\(existing)"
        } else {
            environment["PYTHONPATH"] = repoRoot.path
        }
        return environment
    }

    private func bridgeInvocation(
        _ arguments: [String]
    ) throws -> (executableURL: URL, arguments: [String]) {
        let commandArguments = arguments + bridgeStorageArguments()
        let bundledRuntime = repoRoot.appendingPathComponent("Runtime/modeldial-backend")
        if FileManager.default.isExecutableFile(atPath: bundledRuntime.path) {
            return (bundledRuntime, commandArguments)
        }
        let script = repoRoot.appendingPathComponent("scripts/native_bridge.py")
        guard FileManager.default.fileExists(atPath: script.path) else {
            throw BridgeClientError.missingRepoRoot
        }
        guard let python = developmentPythonExecutable() else {
            throw BridgeClientError.missingPythonRuntime
        }
        return (python, [script.path] + commandArguments)
    }

    private func bridgeStorageArguments() -> [String] {
        [
            "--config-path", dataDirectory.appendingPathComponent("config.json").path,
            "--history-path", dataDirectory.appendingPathComponent("history.jsonl").path,
            "--active-run-path", dataDirectory.appendingPathComponent("active_run.json").path,
        ]
    }

    private func developmentPythonExecutable() -> URL? {
        let fileManager = FileManager.default
        let environment = enrichedEnvironment()
        if let override = environment["MODELDIAL_PYTHON"],
           fileManager.isExecutableFile(atPath: override) {
            return URL(fileURLWithPath: override)
        }
        for directory in (environment["PATH"] ?? "").split(separator: ":") {
            let candidate = URL(fileURLWithPath: String(directory))
                .appendingPathComponent("python3")
            if fileManager.isExecutableFile(atPath: candidate.path) {
                return candidate
            }
        }
        return nil
    }

    static func legacyArtifactsDirectory(
        selectedRepoRoot: URL,
        developmentRoot: URL,
        fileManager: FileManager
    ) -> URL? {
        guard selectedRepoRoot.standardizedFileURL.path
                == developmentRoot.standardizedFileURL.path,
              fileManager.fileExists(
                atPath: developmentRoot
                    .appendingPathComponent("scripts/native_bridge.py")
                    .path
              ) else {
            return nil
        }
        return developmentRoot.appendingPathComponent("artifacts")
    }

    static func prepareDataDirectory(
        _ directory: URL,
        legacyArtifactsDirectory: URL?,
        fileManager: FileManager
    ) {
        try? fileManager.createDirectory(
            at: directory,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        guard let legacyArtifactsDirectory else { return }
        for name in [
            "config.json",
            "history.jsonl",
            "history.run_metadata.json",
            "active_run.json",
            "codex_session_tracker.json",
        ] {
            let source = legacyArtifactsDirectory.appendingPathComponent(name)
            let destination = directory.appendingPathComponent(name)
            guard fileManager.fileExists(atPath: source.path),
                  !fileManager.fileExists(atPath: destination.path) else {
                continue
            }
            try? fileManager.copyItem(at: source, to: destination)
        }
    }
}

private final class BridgeProcessOutputCollector: @unchecked Sendable {
    private let lock = NSLock()
    private var outputData = Data()
    private var errorData = Data()

    func captureOutput(from handle: FileHandle) {
        let data = handle.readDataToEndOfFile()
        lock.lock()
        outputData = data
        lock.unlock()
    }

    func captureError(from handle: FileHandle) {
        let data = handle.readDataToEndOfFile()
        lock.lock()
        errorData = data
        lock.unlock()
    }

    func collectedData() -> (Data, Data) {
        lock.lock()
        defer { lock.unlock() }
        return (outputData, errorData)
    }
}

private extension JSONDecoder {
    static var codexMonitor: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }
}

private struct ConfigSaveResponse: Decodable {
    let state: BridgeSnapshot
}

private struct SecretReferenceMigrationAcknowledgement: Decodable {
    let schemaVersion: Int
    let ok: Bool
    let action: String
    let operation: String

    var isAccepted: Bool {
        schemaVersion == 1
            && ok
            && action == "migrate_secret_references"
            && operation == "connection_secret_references"
    }
}
