import Foundation

enum SettingsIngressPresenter {
    struct WorkspaceItem: Identifiable {
        let source: BridgeIngressSource
        let connection: BridgeIngressConnection

        var id: String { connection.id }
    }

    struct Metric: Identifiable {
        let id: String
        let value: String
        let label: String
    }

    struct ModelFamilyGroup: Identifiable {
        let id: String
        let connectionID: String
        let familyID: String
        let modelID: String
        let displayModel: String
        let candidates: [BridgeIngressModelCandidate]
    }

    struct EndpointProviderOption: Identifiable {
        let id: String
        let title: String
        let isCustom: Bool
    }

    struct CustomWorkspace: Identifiable {
        let connection: BridgeIngressConnection
        let candidates: [BridgeIngressModelCandidate]

        var id: String { connection.id }
    }

    struct CustomSourceSection: Identifiable {
        let source: BridgeIngressSource
        let workspaces: [CustomWorkspace]

        var id: String { source.id }
    }

    struct Presentation {
        let workspaceItems: [WorkspaceItem]
        let localWorkspaceItems: [WorkspaceItem]
        let apiWorkspaceItems: [WorkspaceItem]
        let featuredProviders: [BridgeProviderCatalogProvider]
        let connectableFeaturedProviders: [BridgeProviderCatalogProvider]
        let overflowProviders: [BridgeProviderCatalogProvider]
        let connectableOverflowProviders: [BridgeProviderCatalogProvider]
        let endpointProviderOptions: [EndpointProviderOption]
        let sourceCount: Int
        let connectionCount: Int
        let modelEntryCount: Int
        let totalCandidateCount: Int
        let enabledCandidateCount: Int
        let enabledSourceCount: Int
        let enabledModelEntryCount: Int
        let regularCandidateIDs: Set<String>
        let customEligibleCandidateIDs: Set<String>
        let enabledWorkspaceItems: [WorkspaceItem]
        let unverifiedEnabledEndpointWorkspaceItems: [WorkspaceItem]
        let regularScanIsBlockedByEndpointVerification: Bool
        let customWorkspaceItems: [WorkspaceItem]
        let customSourceSections: [CustomSourceSection]
        let regularScanScopeMetrics: [Metric]

        fileprivate let sourcesByID: [String: BridgeIngressSource]
        fileprivate let connectionsByID: [String: BridgeIngressConnection]
        fileprivate let connectionsBySourceID: [String: [BridgeIngressConnection]]
        fileprivate let providersByID: [String: BridgeProviderCatalogProvider]
        fileprivate let candidateProjectionsByID: [String: BridgeSettingsCandidateProjection]
        fileprivate let familyGroupsByConnectionID: [String: [ModelFamilyGroup]]
        fileprivate let regularFamilyGroupsByConnectionID: [String: [ModelFamilyGroup]]
        fileprivate let enabledCandidateCountsByConnectionID: [String: Int]
        fileprivate let providerConnectionCountsByID: [String: Int]
        fileprivate let providerIDsByConnectionID: [String: String]

        func selectedWorkspaceItem(id: String?) -> WorkspaceItem? {
            guard let id else { return workspaceItems.first }
            return workspaceItems.first(where: { $0.id == id }) ?? workspaceItems.first
        }

        func source(id: String) -> BridgeIngressSource? {
            sourcesByID[id]
        }

        func connections(sourceID: String) -> [BridgeIngressConnection] {
            connectionsBySourceID[sourceID] ?? []
        }

        func connection(id: String) -> BridgeIngressConnection? {
            connectionsByID[id]
        }

        func provider(id: String) -> BridgeProviderCatalogProvider? {
            providersByID[id]
        }

        func candidatePresentation(
            for candidate: BridgeIngressModelCandidate
        ) -> SettingsCandidatePresentation {
            SettingsCandidatePresenter.presentation(
                for: candidate,
                projection: candidateProjectionsByID[candidate.id]
            )
        }

        func modelFamilyGroups(
            for connection: BridgeIngressConnection
        ) -> [ModelFamilyGroup] {
            familyGroupsByConnectionID[connection.id]
                ?? SettingsIngressPresenter.modelFamilyGroups(
                    for: connection,
                    projectionsByCandidateID: candidateProjectionsByID
                )
        }

        func regularModelFamilyGroups(
            for connection: BridgeIngressConnection
        ) -> [ModelFamilyGroup] {
            regularFamilyGroupsByConnectionID[connection.id] ?? []
        }

        func enabledCandidateCount(for connection: BridgeIngressConnection) -> Int {
            enabledCandidateCountsByConnectionID[connection.id] ?? 0
        }

        func providerConnectionCount(for providerID: String) -> Int {
            providerConnectionCountsByID[providerID] ?? 0
        }

        func eligibleCustomSelection(from selectedCandidateIDs: Set<String>) -> Set<String> {
            selectedCandidateIDs.intersection(customEligibleCandidateIDs)
        }

        func endpointProviderID(
            for connection: BridgeIngressConnection,
            fallback customProviderID: String
        ) -> String {
            providerIDsByConnectionID[connection.id]
                ?? SettingsCandidatePresenter.providerID(
                    for: connection.modelCandidates,
                    projectionsByCandidateID: candidateProjectionsByID,
                    fallbackProviderID: connection.providerId
                )
                ?? customProviderID
        }
    }

    static func present(
        ingress: BridgeModelIngress?,
        providerCatalog: [BridgeProviderCatalogProvider],
        scanScope: BridgeSettingsScanScopeProjection?,
        candidateProjections: [BridgeSettingsCandidateProjection],
        hasResumableRun: Bool,
        customProviderID: String
    ) -> Presentation {
        let sources = ingress?.sources ?? []
        let connections = ingress?.connections ?? []
        let sourcesByID = dictionaryKeepingFirst(sources.map { ($0.id, $0) })
        let connectionsBySourceID = Dictionary(grouping: connections, by: \.sourceId)
        let candidateProjectionsByID = dictionaryKeepingFirst(
            candidateProjections.map { ($0.candidateId, $0) }
        )
        let workspaceItems = connections.compactMap { connection -> WorkspaceItem? in
            guard let source = sourcesByID[connection.sourceId] else { return nil }
            return WorkspaceItem(source: source, connection: connection)
        }
        let localWorkspaceItems = workspaceItems.filter { $0.source.mode == "local" }
        let apiWorkspaceItems = workspaceItems.filter { $0.source.mode == "api" }

        let familyGroupsByConnectionID = dictionaryKeepingFirst(
            connections.map {
                (
                    $0.id,
                    modelFamilyGroups(
                        for: $0,
                        projectionsByCandidateID: candidateProjectionsByID
                    )
                )
            }
        )
        let regularCandidateIDs = Set(scanScope?.regularCandidateIds ?? [])
        let customEligibleCandidateIDs = Set(scanScope?.customCandidateIds ?? [])
        let regularFamilyGroupsByConnectionID = familyGroupsByConnectionID.mapValues {
            groups in
            groups.compactMap { group -> ModelFamilyGroup? in
                let candidates = group.candidates.filter {
                    regularCandidateIDs.contains($0.id)
                }
                guard !candidates.isEmpty else { return nil }
                return ModelFamilyGroup(
                    id: group.id,
                    connectionID: group.connectionID,
                    familyID: group.familyID,
                    modelID: group.modelID,
                    displayModel: group.displayModel,
                    candidates: candidates
                )
            }
        }
        let enabledWorkspaceItems = workspaceItems.filter { item in
            item.connection.modelCandidates.contains {
                regularCandidateIDs.contains($0.id)
            }
        }
        let blockedConnectionIDs = Set(
            (scanScope?.blockedReasons ?? []).compactMap { reason in
                reason.reason == "api_connection_unverified" && !reason.candidateIds.isEmpty
                    ? reason.connectionId
                    : nil
            }
        )
        let unverifiedEnabledEndpointWorkspaceItems = apiWorkspaceItems.filter {
            blockedConnectionIDs.contains($0.connection.id)
        }
        let customWorkspaceItems = workspaceItems.filter { item in
            item.connection.modelCandidates.contains {
                customEligibleCandidateIDs.contains($0.id)
            }
        }
        let customWorkspaceBySourceID = Dictionary(
            grouping: customWorkspaceItems,
            by: { $0.source.id }
        )
        let customSourceSections = sources.compactMap { source -> CustomSourceSection? in
            let workspaces = (customWorkspaceBySourceID[source.id] ?? []).compactMap {
                item -> CustomWorkspace? in
                let candidates = item.connection.modelCandidates.filter {
                    customEligibleCandidateIDs.contains($0.id)
                }
                guard !candidates.isEmpty else { return nil }
                return CustomWorkspace(connection: item.connection, candidates: candidates)
            }
            guard !workspaces.isEmpty else { return nil }
            return CustomSourceSection(source: source, workspaces: workspaces)
        }

        let enabledCandidateCountsByConnectionID = dictionaryKeepingFirst(
            connections.map { connection in
                let sourceIsEnabled = sourcesByID[connection.sourceId]?.enabled == true
                let count = connection.modelCandidates.filter {
                    sourceIsEnabled && connection.enabled && $0.enabled
                }.count
                return (connection.id, count)
            }
        )
        let providerIDsByConnectionID = dictionaryKeepingFirst(
            apiWorkspaceItems.map { item in
                let providerID = SettingsCandidatePresenter.providerID(
                    for: item.connection.modelCandidates,
                    projectionsByCandidateID: candidateProjectionsByID,
                    fallbackProviderID: item.connection.providerId
                ) ?? customProviderID
                return (item.connection.id, providerID)
            }
        )
        let providerConnectionCountsByID = providerIDsByConnectionID.values.reduce(
            into: [String: Int]()
        ) { counts, providerID in
            counts[providerID, default: 0] += 1
        }

        let totalCandidateCount = candidateProjections.count
        let enabledCandidateCount = scanScope?.candidateCount ?? 0
        let enabledSourceCount = scanScope?.sourceCount ?? 0
        let enabledModelEntryCount = scanScope?.modelCount ?? 0
        let featuredProviders = providerCatalog.filter(\.featured)
        let overflowProviders = providerCatalog.filter { !$0.featured }

        return Presentation(
            workspaceItems: workspaceItems,
            localWorkspaceItems: localWorkspaceItems,
            apiWorkspaceItems: apiWorkspaceItems,
            featuredProviders: featuredProviders,
            connectableFeaturedProviders: featuredProviders.filter {
                $0.connectionSupported != false
            },
            overflowProviders: overflowProviders,
            connectableOverflowProviders: overflowProviders.filter {
                $0.connectionSupported != false
            },
            endpointProviderOptions: providerCatalog.compactMap { provider in
                guard provider.connectionSupported != false else { return nil }
                return EndpointProviderOption(
                    id: provider.providerId,
                    title: provider.displayName,
                    isCustom: false
                )
            } + [
                EndpointProviderOption(
                    id: customProviderID,
                    title: L10n.tr("自定义 endpoint"),
                    isCustom: true
                )
            ],
            sourceCount: Set(workspaceItems.map { $0.source.id }).count,
            connectionCount: workspaceItems.count,
            modelEntryCount: Set(workspaceItems.flatMap { item in
                item.connection.modelCandidates.map {
                    "\(item.connection.id):\($0.modelId)"
                }
            }).count,
            totalCandidateCount: totalCandidateCount,
            enabledCandidateCount: enabledCandidateCount,
            enabledSourceCount: enabledSourceCount,
            enabledModelEntryCount: enabledModelEntryCount,
            regularCandidateIDs: regularCandidateIDs,
            customEligibleCandidateIDs: customEligibleCandidateIDs,
            enabledWorkspaceItems: enabledWorkspaceItems,
            unverifiedEnabledEndpointWorkspaceItems: unverifiedEnabledEndpointWorkspaceItems,
            regularScanIsBlockedByEndpointVerification:
                !unverifiedEnabledEndpointWorkspaceItems.isEmpty && !hasResumableRun,
            customWorkspaceItems: customWorkspaceItems,
            customSourceSections: customSourceSections,
            regularScanScopeMetrics: [
                Metric(
                    id: "sources",
                    value: "\(enabledSourceCount)",
                    label: L10n.tr("已启用来源")
                ),
                Metric(
                    id: "model-entries",
                    value: "\(enabledModelEntryCount)",
                    label: L10n.tr("本轮模型")
                ),
                Metric(
                    id: "scan-configurations",
                    value: "\(enabledCandidateCount)/\(totalCandidateCount)",
                    label: L10n.tr("已启用／目录档位")
                ),
            ],
            sourcesByID: sourcesByID,
            connectionsByID: dictionaryKeepingFirst(
                connections.map { ($0.id, $0) }
            ),
            connectionsBySourceID: connectionsBySourceID,
            providersByID: dictionaryKeepingFirst(
                providerCatalog.map { ($0.providerId, $0) }
            ),
            candidateProjectionsByID: candidateProjectionsByID,
            familyGroupsByConnectionID: familyGroupsByConnectionID,
            regularFamilyGroupsByConnectionID: regularFamilyGroupsByConnectionID,
            enabledCandidateCountsByConnectionID: enabledCandidateCountsByConnectionID,
            providerConnectionCountsByID: providerConnectionCountsByID,
            providerIDsByConnectionID: providerIDsByConnectionID
        )
    }

    static func uniqueModelNames(
        in candidates: [BridgeIngressModelCandidate]
    ) -> [String] {
        var seen = Set<String>()
        return candidates.compactMap { candidate in
            seen.insert(candidate.modelId).inserted ? candidate.modelId : nil
        }
    }

    private static func modelFamilyGroups(
        for connection: BridgeIngressConnection,
        projectionsByCandidateID: [String: BridgeSettingsCandidateProjection]
    ) -> [ModelFamilyGroup] {
        var orderedFamilyIDs: [String] = []
        var candidatesByFamilyID: [String: [BridgeIngressModelCandidate]] = [:]
        for candidate in connection.modelCandidates {
            let familyID = SettingsCandidatePresenter.presentation(
                for: candidate,
                projection: projectionsByCandidateID[candidate.id]
            ).familyID
            if candidatesByFamilyID[familyID] == nil {
                orderedFamilyIDs.append(familyID)
            }
            candidatesByFamilyID[familyID, default: []].append(candidate)
        }
        return orderedFamilyIDs.map { familyID in
            let candidates = candidatesByFamilyID[familyID] ?? []
            return ModelFamilyGroup(
                id: "\(connection.id):\(familyID)",
                connectionID: connection.id,
                familyID: familyID,
                modelID: candidates.first?.modelId ?? familyID,
                displayModel: candidates.first.map {
                    SettingsCandidatePresenter.presentation(
                        for: $0,
                        projection: projectionsByCandidateID[$0.id]
                    ).displayModel
                } ?? familyID,
                candidates: candidates
            )
        }
    }

    private static func dictionaryKeepingFirst<Key: Hashable, Value>(
        _ pairs: [(Key, Value)]
    ) -> [Key: Value] {
        Dictionary(pairs, uniquingKeysWith: { first, _ in first })
    }
}
