import Combine
import Foundation

enum SettingsSaveFeedbackState: Equatable {
    case idle
    case saving
    case saved
    case failed
}

@MainActor
final class SelectionSettingsStore: ObservableObject {
    static let shared = SelectionSettingsStore(sessionStore: .shared)

    @Published private(set) var draftConfig: BridgeConfig?
    @Published var isSaving = false
    @Published var errorMessage: String?
    @Published private(set) var saveFeedbackState: SettingsSaveFeedbackState = .idle
    @Published private(set) var endpoint = EndpointOperationState()
    @Published private(set) var localImportMessage: String?
    @Published private(set) var localImportSelectionID: String?
    @Published private(set) var importingLocalProviderID: String?
    @Published private(set) var localImportFeedbackProviderID: String?
    @Published private(set) var localImportSucceeded: Bool?
    @Published private(set) var localModelDiscoveryCandidates: [BridgeLocalModelDiscoveryCandidate] = []
    @Published private(set) var localModelDiscoveryMessage: String?
    @Published private(set) var isLocalModelDiscoveryRunning = false
    @Published private(set) var dataOperationMessage: String?
    @Published private(set) var isDataOperationRunning = false

    private let sessionStore: AppSessionStore
    private let secretStore = AppSecretStore()
    private var pendingConfigPatches: [SettingsConfigPatch] = []
    private var isConfigPatchInFlight = false
    private var savedFeedbackResetTask: Task<Void, Never>?
    private var endpointDraftConnectionID: String?
    private var snapshotObservation: AnyCancellable?

    init(sessionStore: AppSessionStore) {
        self.sessionStore = sessionStore
        snapshotObservation = sessionStore.$snapshot.sink { [weak self] snapshot in
            self?.synchronizeDraft(with: snapshot?.config)
        }
        DebugLog.write("SelectionSettingsStore.init")
    }

    func reload() {
        DebugLog.write("SelectionSettingsStore.reload begin")
        synchronizeDraftWithAuthoritativeConfig()
    }

    func exportPersonalObservations(to url: URL) {
        guard !isDataOperationRunning else { return }
        isDataOperationRunning = true
        dataOperationMessage = nil
        Task(priority: .userInitiated) { [weak self] in
            guard let self else { return }
            do {
                try await sessionStore.exportSettingsPersonalObservations(to: url)
                isDataOperationRunning = false
                dataOperationMessage = "观察数据已导出"
            } catch {
                isDataOperationRunning = false
                dataOperationMessage = error.localizedDescription
            }
        }
    }

    func clearPersonalObservations() {
        guard !isDataOperationRunning else { return }
        isDataOperationRunning = true
        dataOperationMessage = nil
        Task(priority: .userInitiated) { [weak self] in
            guard let self else { return }
            do {
                let response = try await sessionStore.clearSettingsPersonalObservations()
                isDataOperationRunning = false
                dataOperationMessage = response.message
                synchronizeDraftWithAuthoritativeConfig()
            } catch {
                isDataOperationRunning = false
                dataOperationMessage = error.localizedDescription
            }
        }
    }

    func setModelCandidateEnabled(connectionID: String, candidateID: String, enabled: Bool) {
        apply(.modelCandidatesEnabled(
            connectionID: connectionID,
            candidateIDs: [candidateID],
            enabled: enabled
        ))
    }

    func setCurrentDefault(candidateID: String?) {
        apply(.currentDefault(candidateID: candidateID))
    }

    func useAutomaticCurrentModel() {
        apply(.automaticCurrentModel)
    }

    func setRecommendationPreference(_ preference: String) {
        guard let preference = SettingsConfigPatch.RecommendationPreference(
            rawValue: preference
        ) else { return }
        apply(.recommendationPreference(preference))
    }

    func setSourceMode(_ sourceMode: String, configurationID: String) {
        guard let sourceMode = SettingsConfigPatch.RecommendationSourceMode(
            rawValue: sourceMode
        ), !configurationID.isEmpty else { return }
        apply(.sourceMode(sourceMode, configurationID: configurationID))
    }

    func setProjectTaskProfile(name: String, taskMode: String) {
        apply(.projectTaskProfile(name: name, taskMode: taskMode))
    }

    func setScanBudget(
        enabled: Bool,
        maxDurationSeconds: Int,
        maxReferenceCostUsd: Double
    ) {
        apply(.scanBudget(
            enabled: enabled,
            maxDurationSeconds: maxDurationSeconds,
            maxReferenceCostUsd: maxReferenceCostUsd
        ))
    }

    func setScanExecution(
        maxConcurrentTargets: Int,
        executionTimeoutSeconds: Int,
        timeoutRetryCount: Int
    ) {
        apply(.scanExecution(
            maxConcurrentTargets: maxConcurrentTargets,
            executionTimeoutSeconds: executionTimeoutSeconds,
            timeoutRetryCount: timeoutRetryCount
        ))
    }

    func setModelCandidatesEnabled(connectionID: String, candidateIDs: [String], enabled: Bool) {
        apply(.modelCandidatesEnabled(
            connectionID: connectionID,
            candidateIDs: candidateIDs,
            enabled: enabled
        ))
    }

    func setTargetEnabled(id: String, enabled: Bool) {
        guard let target = draftConfig?.targets.first(where: { $0.id == id }) else { return }
        setModelCandidateEnabled(
            connectionID: target.connectionID,
            candidateID: target.candidateID,
            enabled: enabled
        )
    }

    @discardableResult
    func saveEndpointConnection(
        connectionID: String?,
        name: String,
        providerPreset: String,
        apiFormat: String,
        baseURL: String,
        apiKey: String,
        modelIDs: [String] = [],
        reasoningProfilesByModel: [String: [String]] = [:],
        defaultReasoningProfilesByModel: [String: String] = [:],
        connectionEnabled: Bool? = nil,
        candidateEnabled: Bool = true,
        lastTestStatus: String? = nil,
        lastTestAt: String? = nil,
        lastTestMessage: String? = nil,
        completion: ((Bool) -> Void)? = nil
    ) -> String? {
        guard !isSaving else {
            errorMessage = "请等待当前设置保存完成。"
            return nil
        }
        let resolvedID = connectionID ?? "endpoint-\(UUID().uuidString.lowercased())"
        let currentConfig = draftConfig ?? sessionStore.snapshot?.config
        let existing = currentConfig?.modelIngress.connections.first {
            $0.id == resolvedID
        }
        if connectionID != nil, existing == nil { return nil }
        let existingKeyRef = existing?.apiKeyRef
        let normalizedBaseURL = baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        let apiKeyRef: String
        let stagedApiKeyRef: String?
        do {
            if apiKey.isEmpty {
                guard let existingKeyRef else {
                    throw LocalEncryptedSecretStoreError.invalidSecret
                }
                if let migratedReference = try? secretStore.bridgeSecret(
                    connectionID: resolvedID,
                    apiKeyRef: existingKeyRef
                )?.updatedConfigReference {
                    apiKeyRef = migratedReference
                    stagedApiKeyRef = migratedReference == existingKeyRef ? nil : migratedReference
                } else {
                    apiKeyRef = existingKeyRef
                    stagedApiKeyRef = nil
                }
            } else {
                apiKeyRef = try secretStore.stage(apiKey, connectionID: resolvedID)
                stagedApiKeyRef = apiKeyRef
            }
        } catch {
            errorMessage = error.localizedDescription
            return nil
        }
        let intendedModelIDs = Array(Set(
            modelIDs
                + Array(reasoningProfilesByModel.keys)
                + Array(defaultReasoningProfilesByModel.keys)
        )).sorted()
        let intent = BridgeEndpointUpsertIntent(
            connectionID: resolvedID,
            name: name.trimmingCharacters(in: .whitespacesAndNewlines),
            providerPreset: providerPreset,
            apiFormat: apiFormat,
            baseURL: normalizedBaseURL,
            apiKeyReference: apiKeyRef,
            enabled: connectionEnabled ?? existing?.enabled ?? true,
            modelIDs: intendedModelIDs,
            reasoningProfilesByModel: reasoningProfilesByModel,
            defaultReasoningProfileByModel: defaultReasoningProfilesByModel,
            candidateEnabled: candidateEnabled,
            lastTestStatus: lastTestStatus,
            lastTestAt: lastTestAt,
            lastTestMessage: lastTestMessage
        )
        saveEndpointIntent(
            intent,
            connectionID: resolvedID,
            stagedApiKeyRef: stagedApiKeyRef,
            previousApiKeyRef: existingKeyRef,
            completion: completion
        )
        return resolvedID
    }

    func importLocalProvider(providerID: String) {
        guard importingLocalProviderID == nil else { return }
        importingLocalProviderID = providerID
        localImportFeedbackProviderID = providerID
        localImportMessage = nil
        localImportSucceeded = nil
        Task(priority: .userInitiated) { [weak self] in
            guard let self else { return }
            do {
                let response = try await sessionStore.importSettingsLocalProvider(
                    providerID: providerID
                )
                importingLocalProviderID = nil
                localImportMessage = response.message
                localImportSucceeded = response.ok
                if response.ok {
                    localImportSelectionID = response.connectionId
                    synchronizeDraftWithAuthoritativeConfig()
                }
            } catch {
                importingLocalProviderID = nil
                localImportMessage = error.localizedDescription
                localImportSucceeded = false
            }
        }
    }

    func discoverLocalModels(providerID: String) {
        isLocalModelDiscoveryRunning = true
        localModelDiscoveryMessage = "正在从 Codex 服务端获取可用模型…"
        Task(priority: .userInitiated) { [weak self] in
            guard let self else { return }
            do {
                let response = try await sessionStore.discoverSettingsLocalModels(
                    providerID: providerID
                )
                localModelDiscoveryCandidates = response.candidates
                localModelDiscoveryMessage = response.message
                isLocalModelDiscoveryRunning = false
            } catch {
                localModelDiscoveryCandidates = []
                localModelDiscoveryMessage = error.localizedDescription
                isLocalModelDiscoveryRunning = false
            }
        }
    }

    func addDiscoveredLocalCandidate(
        connectionID: String,
        candidate: BridgeLocalModelDiscoveryCandidate
    ) {
        guard apply(.addDiscoveredLocalCandidate(
            connectionID: connectionID,
            modelID: candidate.modelId,
            displayName: candidate.displayName,
            scanProfile: candidate.scanProfile
        )) else { return }
        localModelDiscoveryCandidates = localModelDiscoveryCandidates.map { item in
            guard item.id == candidate.id else { return item }
            return BridgeLocalModelDiscoveryCandidate(
                id: item.id,
                modelId: item.modelId,
                modelDisplayName: item.modelDisplayName,
                displayName: item.displayName,
                scanProfile: item.scanProfile,
                isDefault: item.isDefault,
                configured: true
            )
        }
        localModelDiscoveryMessage = "已加入 \(candidate.displayName)，默认关闭。"
    }

    func probeAndSaveEndpointConnection(
        name: String,
        providerPreset: String,
        apiFormat: String,
        baseURL: String,
        apiKey: String,
        modelIDs: [String],
        reasoningProfilesByModel: [String: [String]] = [:],
        defaultReasoningProfilesByModel: [String: String] = [:],
        completion: @escaping (Bool) -> Void
    ) {
        let probeTargets = Array(Set(modelIDs)).sorted().map { modelID in
            (
                modelID,
                defaultReasoningProfilesByModel[modelID]
                    ?? preferredDefaultProfile(in: reasoningProfilesByModel[modelID] ?? [])
                    ?? "default"
            )
        }
        guard !probeTargets.isEmpty else {
            endpoint.message = "请先选择至少一个模型"
            completion(false)
            return
        }
        endpoint.beginOperation(message: "正在验证连接…")
        Task(priority: .userInitiated) { [weak self] in
            guard let self else { return }
            do {
                var finalResponse: BridgeConnectionTestResponse?
                for (modelID, scanProfile) in probeTargets {
                    let response = try await sessionStore.probeSettingsEndpointConnection(
                        baseURL: baseURL,
                        apiFormat: apiFormat,
                        providerPreset: providerPreset,
                        modelID: modelID,
                        scanProfile: scanProfile,
                        apiKey: apiKey
                    )
                    finalResponse = response
                    if !response.ok { break }
                }
                guard let response = finalResponse else { return }
                endpoint.finishOperation()
                guard response.ok else {
                    endpoint.message = response.message
                    if response.errorCategory == "rate_limited" {
                        endpoint.message = "请求受到限流，正在保存连接草稿…"
                        let draftConnectionID = saveEndpointConnection(
                            connectionID: endpointDraftConnectionID,
                            name: name,
                            providerPreset: providerPreset,
                            apiFormat: apiFormat,
                            baseURL: baseURL,
                            apiKey: apiKey,
                            modelIDs: modelIDs,
                            reasoningProfilesByModel: reasoningProfilesByModel,
                            defaultReasoningProfilesByModel: defaultReasoningProfilesByModel,
                            connectionEnabled: false,
                            candidateEnabled: false,
                            lastTestStatus: "rate_limited",
                            lastTestAt: response.testedAt,
                            lastTestMessage: response.message,
                            completion: { saved in
                                self.endpoint.message = saved
                                    ? "请求受到限流，连接草稿已保存；可稍后继续验证。"
                                    : "请求受到限流，且连接草稿保存失败。"
                                completion(false)
                            }
                        )
                        guard let draftConnectionID else {
                            endpoint.message = "请求受到限流，且连接草稿保存失败。"
                            completion(false)
                            return
                        }
                        endpointDraftConnectionID = draftConnectionID
                        return
                    }
                    completion(false)
                    return
                }
                endpoint.message = "连接验证成功，正在保存…"
                let savedConnectionID = saveEndpointConnection(
                    connectionID: endpointDraftConnectionID,
                    name: name,
                    providerPreset: providerPreset,
                    apiFormat: apiFormat,
                    baseURL: baseURL,
                    apiKey: apiKey,
                    modelIDs: modelIDs,
                    reasoningProfilesByModel: reasoningProfilesByModel,
                    defaultReasoningProfilesByModel: defaultReasoningProfilesByModel,
                    connectionEnabled: true,
                    candidateEnabled: true,
                    lastTestStatus: "ok",
                    lastTestAt: response.testedAt,
                    lastTestMessage: response.message,
                    completion: { saved in
                        if saved {
                            self.endpoint.message = "连接并保存成功"
                            self.endpointDraftConnectionID = nil
                        } else {
                            self.endpoint.message = "连接验证成功，但保存失败。"
                        }
                        completion(saved)
                    }
                )
                guard let savedConnectionID else {
                    endpoint.message = "连接验证成功，但保存失败。"
                    completion(false)
                    return
                }
                endpointDraftConnectionID = savedConnectionID
            } catch {
                endpoint.finishOperation()
                endpoint.message = error.localizedDescription
                completion(false)
            }
        }
    }

    func probeEndpointModels(
        baseURL: String,
        apiFormat: String,
        apiKey: String,
        completion: @escaping ([String]) -> Void
    ) {
        endpoint.beginOperation(message: "正在发现模型…")
        Task(priority: .userInitiated) { [weak self] in
            guard let self else { return }
            do {
                let response = try await sessionStore.probeSettingsEndpointModels(
                    baseURL: baseURL,
                    apiFormat: apiFormat,
                    apiKey: apiKey
                )
                endpoint.replaceDiscovery(
                    models: response.models,
                    newModels: response.models,
                    configuredModels: [],
                    reasoningProfilesByModel: response.reasoningProfilesByModel,
                    defaultReasoningProfileByModel: response.defaultReasoningProfileByModel,
                    message: modelDiscoveryMessage(response)
                )
                endpoint.finishOperation()
                completion(response.models)
            } catch {
                endpoint.clearDiscovery(message: error.localizedDescription)
                endpoint.finishOperation()
                completion([])
            }
        }
    }

    func setConnectionEnabled(connectionID: String, enabled: Bool) {
        apply(.connectionEnabled(connectionID: connectionID, enabled: enabled))
    }

    func deleteConnection(
        connectionID: String,
        completion: @escaping (Bool) -> Void
    ) {
        guard !isSaving else {
            errorMessage = "请等待当前设置保存完成后再删除连接。"
            completion(false)
            return
        }
        if sessionStore.snapshot?.runtime.isRunning == true
            || sessionStore.snapshot?.runtime.hasResumableRun == true {
            errorMessage = "存在运行中或可续扫任务，暂时不能删除连接。"
            completion(false)
            return
        }
        guard let connection = (draftConfig ?? sessionStore.snapshot?.config)?
            .modelIngress.connections.first(where: { $0.id == connectionID }) else {
            completion(false)
            return
        }
        let apiKeyRef = connection.apiKeyRef

        beginSavingFeedback()
        Task(priority: .userInitiated) { [weak self] in
            guard let self else { return }
            do {
                let savedConfig = try await sessionStore.applySettingsPatch(
                    .deleteConnection(connectionID: connectionID)
                )
                let secretCleanupError: String? = await Task.detached(priority: .utility) { () -> String? in
                    do {
                        try AppSecretStore().delete(
                            connectionID: connectionID,
                            apiKeyRef: apiKeyRef
                        )
                        return nil
                    } catch {
                        return error.localizedDescription
                    }
                }.value
                synchronizeSavedConfig(savedConfig)
                errorMessage = secretCleanupError.map {
                    "连接已删除，但密钥清理失败：\($0)"
                }
                finishSavingFeedback(success: true)
                completion(true)
            } catch {
                errorMessage = error.localizedDescription
                finishSavingFeedback(success: false)
                completion(false)
            }
        }
    }

    func removeModelCandidates(
        connectionID: String,
        candidateIDs: [String],
        completion: @escaping (Bool) -> Void
    ) {
        guard !isSaving else {
            errorMessage = "请等待当前设置保存完成后再移除扫描档位。"
            completion(false)
            return
        }
        if sessionStore.snapshot?.runtime.isRunning == true
            || sessionStore.snapshot?.runtime.hasResumableRun == true {
            errorMessage = "存在运行中或可续扫任务，暂时不能移除扫描档位。"
            completion(false)
            return
        }
        let candidateIDs = Array(Set(candidateIDs)).sorted()
        guard !candidateIDs.isEmpty else {
            completion(false)
            return
        }

        beginSavingFeedback()
        Task(priority: .userInitiated) { [weak self] in
            guard let self else { return }
            do {
                let savedConfig = try await sessionStore.applySettingsPatch(
                    .removeModelCandidates(
                        connectionID: connectionID,
                        candidateIDs: candidateIDs
                    )
                )
                synchronizeSavedConfig(savedConfig)
                errorMessage = nil
                finishSavingFeedback(success: true)
                completion(true)
            } catch {
                errorMessage = error.localizedDescription
                finishSavingFeedback(success: false)
                completion(false)
            }
        }
    }

    func addEndpointModel(connectionID: String, modelID: String) {
        let normalizedModelID = modelID.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !isSaving, !normalizedModelID.isEmpty else { return }
        guard let connection = (draftConfig ?? sessionStore.snapshot?.config)?
            .modelIngress.connections.first(where: { $0.id == connectionID }),
              !connection.modelCandidates.contains(where: {
                  $0.modelId == normalizedModelID
              }) else { return }
        let intent = BridgeEndpointModelsIntent(
            connectionID: connectionID,
            modelIDs: [normalizedModelID],
            reasoningProfilesByModel: endpoint.discoveredReasoningProfilesByModel[normalizedModelID].map {
                [normalizedModelID: $0]
            } ?? [:],
            defaultReasoningProfileByModel: endpoint.discoveredDefaultReasoningProfileByModel[normalizedModelID].map {
                [normalizedModelID: $0]
            } ?? [:],
            candidateEnabled: false
        )
        beginSavingFeedback()
        Task(priority: .userInitiated) { [weak self] in
            guard let self else { return }
            do {
                let savedConfig = try await sessionStore.addSettingsEndpointModels(
                    intent
                )
                synchronizeSavedConfig(savedConfig)
                endpoint.newlyDiscoveredModelIDs.removeAll {
                    $0 == normalizedModelID
                }
                endpoint.configuredDiscoveredModelIDs = Array(
                    Set(endpoint.configuredDiscoveredModelIDs + [normalizedModelID])
                ).sorted()
                endpoint.message = "已添加，默认关闭；开启后可运行首次扫描。"
                errorMessage = nil
                finishSavingFeedback(success: true)
            } catch {
                errorMessage = error.localizedDescription
                finishSavingFeedback(success: false)
            }
        }
    }

    private func preferredDefaultProfile(in profiles: [String]) -> String? {
        if profiles.contains("high") { return "high" }
        return profiles.first
    }

    func discoverModels(connectionID: String) {
        endpoint.beginOperation()
        Task(priority: .userInitiated) { [weak self] in
            guard let self else { return }
            do {
                let response = try await sessionStore.discoverSettingsModels(
                    connectionID: connectionID
                )
                endpoint.replaceDiscovery(
                    models: response.models,
                    newModels: response.newModels,
                    configuredModels: response.configuredModels,
                    reasoningProfilesByModel: response.reasoningProfilesByModel,
                    defaultReasoningProfileByModel: response.defaultReasoningProfileByModel,
                    message: modelDiscoveryMessage(response)
                )
                endpoint.finishOperation()
            } catch {
                endpoint.clearDiscovery(message: error.localizedDescription)
                endpoint.finishOperation()
            }
        }
    }

    func discoverEndpointDraftModels(
        connectionID: String,
        baseURL: String,
        apiFormat: String,
        apiKey: String
    ) {
        let resolvedAPIKey: String
        do {
            resolvedAPIKey = try resolvedEndpointProbeAPIKey(
                connectionID: connectionID,
                draftAPIKey: apiKey
            )
        } catch {
            resetEndpointDraftFeedback()
            endpoint.message = error.localizedDescription
            return
        }
        let configuredModelIDs = Set(
            draftConfig?.modelIngress.connections
                .first(where: { $0.id == connectionID })?
                .modelCandidates
                .map(\.modelId)
                ?? []
        )
        let operationGeneration = endpoint.beginDraftOperation()
        endpoint.message = "正在发现模型…"
        endpoint.clearTestFeedback()
        Task(priority: .userInitiated) { [weak self] in
            guard let self else { return }
            do {
                let response = try await sessionStore.probeSettingsEndpointModels(
                    baseURL: baseURL,
                    apiFormat: apiFormat,
                    apiKey: resolvedAPIKey
                )
                guard endpoint.finishDraftOperation(operationGeneration) else { return }
                let models = Array(Set(response.models)).sorted()
                endpoint.replaceDiscovery(
                    models: models,
                    newModels: models.filter { !configuredModelIDs.contains($0) },
                    configuredModels: models.filter { configuredModelIDs.contains($0) },
                    reasoningProfilesByModel: response.reasoningProfilesByModel,
                    defaultReasoningProfileByModel: response.defaultReasoningProfileByModel,
                    message: modelDiscoveryMessage(response)
                )
            } catch {
                guard endpoint.finishDraftOperation(operationGeneration) else { return }
                endpoint.clearDiscovery(message: error.localizedDescription)
            }
        }
    }

    func resetModelDiscovery() {
        endpoint.resetModelDiscovery()
        endpointDraftConnectionID = nil
    }

    func resetEndpointDraftFeedback() {
        endpoint.resetDraftFeedback()
    }

    private func modelDiscoveryMessage(_ response: BridgeModelDiscoveryResponse) -> String {
        guard !response.ok,
              response.manualEntryAllowed,
              response.errorCategory == "network_error" else {
            return response.message
        }
        return "网络连接失败；仍可手工填写准确的 Model ID。"
    }

    private func resolvedEndpointProbeAPIKey(
        connectionID: String,
        draftAPIKey: String
    ) throws -> String {
        let normalizedDraftKey = draftAPIKey.trimmingCharacters(in: .whitespacesAndNewlines)
        guard normalizedDraftKey.isEmpty else { return normalizedDraftKey }
        let connection = draftConfig?.modelIngress.connections.first {
            $0.id == connectionID
        }
        guard let resolved = try secretStore.bridgeSecret(
            connectionID: connectionID,
            apiKeyRef: connection?.apiKeyRef
        ) else {
            throw LocalEncryptedSecretStoreError.invalidSecret
        }
        return resolved.secret
    }

    func testConnection(connectionID: String, modelID: String) {
        endpoint.beginConnectionTest(connectionID: connectionID, modelID: modelID)
        Task(priority: .userInitiated) { [weak self] in
            guard let self else { return }
            do {
                let response = try await sessionStore.testSettingsConnection(
                    connectionID: connectionID,
                    modelID: modelID
                )
                endpoint.finishConnectionTest(EndpointTestFeedback(
                    connectionID: connectionID,
                    modelID: modelID,
                    ok: response.ok,
                    message: response.message
                ))
                synchronizeDraftWithAuthoritativeConfig()
            } catch {
                let message = error.localizedDescription
                endpoint.finishConnectionTest(EndpointTestFeedback(
                    connectionID: connectionID,
                    modelID: modelID,
                    ok: false,
                    message: message
                ))
            }
        }
    }

    func testEndpointDraftConnection(
        connectionID: String,
        modelID: String,
        baseURL: String,
        apiFormat: String,
        providerPreset: String,
        apiKey: String
    ) {
        let resolvedAPIKey: String
        do {
            resolvedAPIKey = try resolvedEndpointProbeAPIKey(
                connectionID: connectionID,
                draftAPIKey: apiKey
            )
        } catch {
            let message = error.localizedDescription
            endpoint.recordTestFeedback(EndpointTestFeedback(
                connectionID: connectionID,
                modelID: modelID,
                ok: false,
                message: message
            ))
            return
        }
        let operationGeneration = endpoint.beginDraftOperation()
        endpoint.beginConnectionTest(connectionID: connectionID, modelID: modelID)
        Task(priority: .userInitiated) { [weak self] in
            guard let self else { return }
            do {
                let response = try await sessionStore.probeSettingsEndpointConnection(
                    baseURL: baseURL,
                    apiFormat: apiFormat,
                    providerPreset: providerPreset,
                    modelID: modelID,
                    apiKey: resolvedAPIKey
                )
                guard endpoint.finishDraftOperation(operationGeneration) else { return }
                endpoint.recordTestFeedback(EndpointTestFeedback(
                    connectionID: connectionID,
                    modelID: modelID,
                    ok: response.ok,
                    message: response.message
                ))
            } catch {
                guard endpoint.finishDraftOperation(operationGeneration) else { return }
                let message = error.localizedDescription
                endpoint.recordTestFeedback(EndpointTestFeedback(
                    connectionID: connectionID,
                    modelID: modelID,
                    ok: false,
                    message: message
                ))
            }
        }
    }

    func setScheduler(mode: String, intervalSeconds: Int) {
        guard let mode = SettingsConfigPatch.SchedulerMode(rawValue: mode) else { return }
        apply(.scheduler(mode: mode, intervalSeconds: intervalSeconds))
    }

    func setSchedulerEnabled(_ enabled: Bool) {
        apply(.schedulerEnabled(enabled))
    }

    func setSchedulerMode(_ mode: String) {
        guard let mode = SettingsConfigPatch.SchedulerMode(rawValue: mode) else { return }
        apply(.schedulerMode(mode))
    }

    func setDailySchedule(hour: Int, minute: Int) {
        apply(.dailySchedule(hour: hour, minute: minute))
    }

    func setWeeklySchedule(weekday: Int, hour: Int, minute: Int) {
        apply(.weeklySchedule(weekday: weekday, hour: hour, minute: minute))
    }

    func setScheduledEvaluationProfile(_ profileID: String) {
        apply(.scheduledEvaluationProfile(profileID))
    }

    private func saveEndpointIntent(
        _ intent: BridgeEndpointUpsertIntent,
        connectionID: String,
        stagedApiKeyRef: String?,
        previousApiKeyRef: String?,
        completion: ((Bool) -> Void)? = nil
    ) {
        beginSavingFeedback()
        Task(priority: .userInitiated) { [weak self] in
            guard let self else { return }
            do {
                let savedConfig = try await sessionStore.upsertSettingsEndpoint(intent)
                let secretCleanupError: String? = await Task.detached(priority: .utility) { () -> String? in
                    if let stagedApiKeyRef,
                       let previousApiKeyRef,
                       previousApiKeyRef != stagedApiKeyRef {
                        do {
                            try AppSecretStore().deleteReference(
                                previousApiKeyRef,
                                connectionID: connectionID
                            )
                        } catch {
                            return error.localizedDescription
                        }
                    }
                    return nil
                }.value
                let secretCleanupMessage = secretCleanupError.map {
                    "连接已保存，但旧密钥清理失败：\($0)"
                }
                synchronizeSavedConfig(savedConfig)
                errorMessage = secretCleanupMessage
                finishSavingFeedback(success: true)
                completion?(true)
            } catch {
                if let stagedApiKeyRef {
                    await Task.detached(priority: .utility) {
                        try? AppSecretStore().deleteReference(
                            stagedApiKeyRef,
                            connectionID: connectionID
                        )
                    }.value
                }
                errorMessage = error.localizedDescription
                finishSavingFeedback(success: false)
                completion?(false)
            }
        }
    }

    @discardableResult
    private func apply(_ patch: SettingsConfigPatch) -> Bool {
        save(patch)
        return true
    }

    private func save(_ patch: SettingsConfigPatch) {
        DebugLog.write("SelectionSettingsStore.patch begin")
        beginSavingFeedback()
        pendingConfigPatches.append(patch)
        startNextConfigPatchIfNeeded()
    }

    private func startNextConfigPatchIfNeeded() {
        guard !isConfigPatchInFlight,
              !pendingConfigPatches.isEmpty else { return }
        let patch = pendingConfigPatches.removeFirst()
        isConfigPatchInFlight = true
        Task(priority: .userInitiated) { [weak self] in
            guard let self else { return }
            do {
                let savedConfig = try await sessionStore.applySettingsPatch(patch)
                isConfigPatchInFlight = false
                synchronizeSavedConfig(savedConfig)
                errorMessage = nil
                if pendingConfigPatches.isEmpty {
                    finishSavingFeedback(success: true)
                } else {
                    startNextConfigPatchIfNeeded()
                }
            } catch {
                isConfigPatchInFlight = false
                pendingConfigPatches.removeAll()
                errorMessage = error.localizedDescription
                finishSavingFeedback(success: false)
            }
        }
    }

    private func synchronizeSavedConfig(_ config: BridgeConfig) {
        draftConfig = config
    }

    private func synchronizeDraftWithAuthoritativeConfig() {
        synchronizeDraft(with: sessionStore.snapshot?.config)
    }

    private func synchronizeDraft(with config: BridgeConfig?) {
        guard !isSaving, let config else { return }
        draftConfig = config
    }

    private func beginSavingFeedback() {
        savedFeedbackResetTask?.cancel()
        errorMessage = nil
        isSaving = true
        saveFeedbackState = .saving
    }

    private func finishSavingFeedback(success: Bool) {
        savedFeedbackResetTask?.cancel()
        isSaving = false
        saveFeedbackState = success ? .saved : .failed
        guard success else { return }
        savedFeedbackResetTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            guard !Task.isCancelled else { return }
            await MainActor.run {
                guard let self else { return }
                guard self.saveFeedbackState == .saved, !self.isSaving else { return }
                self.saveFeedbackState = .idle
            }
        }
    }
}
