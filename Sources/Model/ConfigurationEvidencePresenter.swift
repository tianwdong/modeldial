import Foundation

enum ConfigurationEvidencePresenter {
    struct SourceInput: Equatable {
        let rawModelID: String?
        let rawEffort: String?
        let connectionParts: [String?]
        let routeFingerprint: String?
        let completedAt: String?
    }

    struct ItemInput: Equatable {
        let id: String
        let displayName: String
        let modelName: String
        let effort: String
        let official: SourceInput
        let local: SourceInput
    }

    struct GraderResultInput: Equatable {
        let questionPackVersion: String?
        let graderVersion: String?
    }

    struct RoutingInput: Equatable {
        let displaySource: String?
        let officialSnapshotIsTrusted: Bool
        let officialQuestionPackVersion: String?
        let officialGraderVersion: String?
        let officialSnapshotID: String?
        let officialPricingSnapshotID: String?
        let localQuestionPackVersion: String?
        let localGraderResults: [GraderResultInput]
        let localSnapshotID: String?
        let recommendationPricingSnapshotID: String?
        let diagnosticPricingSnapshotID: String?

        init(
            displaySource: String?,
            officialSnapshotIsTrusted: Bool = false,
            officialQuestionPackVersion: String?,
            officialGraderVersion: String?,
            officialSnapshotID: String?,
            officialPricingSnapshotID: String?,
            localQuestionPackVersion: String?,
            localGraderResults: [GraderResultInput],
            localSnapshotID: String?,
            recommendationPricingSnapshotID: String?,
            diagnosticPricingSnapshotID: String?
        ) {
            self.displaySource = displaySource
            self.officialSnapshotIsTrusted = officialSnapshotIsTrusted
            self.officialQuestionPackVersion = officialQuestionPackVersion
            self.officialGraderVersion = officialGraderVersion
            self.officialSnapshotID = officialSnapshotID
            self.officialPricingSnapshotID = officialPricingSnapshotID
            self.localQuestionPackVersion = localQuestionPackVersion
            self.localGraderResults = localGraderResults
            self.localSnapshotID = localSnapshotID
            self.recommendationPricingSnapshotID = recommendationPricingSnapshotID
            self.diagnosticPricingSnapshotID = diagnosticPricingSnapshotID
        }
    }

    struct RoutingPresentation: Equatable {
        let usesLocalDataset: Bool
        let usesOfficialSnapshot: Bool
        let questionPackVersion: String?
        let graderVersion: String?
        let evaluationSnapshotID: String?
        let pricingSnapshotID: String?
    }

    struct RowPresentation: Equatable, Identifiable {
        let id: String
        let displayName: String
        let identityDifferenceText: String?
        let connectionText: String?
        let routeText: String?
        let completionText: String?
    }

    struct Presentation: Equatable {
        let rows: [RowPresentation]
        let sourceLabel: String
        let sharedConnectionText: String?
        let currentRouteFingerprint: String?
        let candidateRouteFingerprint: String?
        let routeEvidenceText: String
        let sharedCompletionText: String?

        var hasDetails: Bool { !rows.isEmpty }
    }

    static func presentation(
        displaySource: String?,
        current: ItemInput,
        candidate: ItemInput
    ) -> Presentation {
        let currentEvidence = evidence(displaySource: displaySource, item: current)
        let candidateEvidence = evidence(displaySource: displaySource, item: candidate)
        let sharedConnection = shared(currentEvidence.connection, candidateEvidence.connection)
        let sharedCompletion = shared(currentEvidence.completion, candidateEvidence.completion)
        let routesDiffer = currentEvidence.route != candidateEvidence.route

        let currentRow = row(
            item: current,
            evidence: currentEvidence,
            sharedConnection: sharedConnection,
            sharedCompletion: sharedCompletion,
            routesDiffer: routesDiffer
        )
        let candidateRow = row(
            item: candidate,
            evidence: candidateEvidence,
            sharedConnection: sharedConnection,
            sharedCompletion: sharedCompletion,
            routesDiffer: routesDiffer
        )
        var rows: [RowPresentation] = []
        if hasDetails(currentRow) { rows.append(currentRow) }
        if current.id != candidate.id, hasDetails(candidateRow) { rows.append(candidateRow) }

        return Presentation(
            rows: rows,
            sourceLabel: sourceLabel(displaySource),
            sharedConnectionText: sharedConnection,
            currentRouteFingerprint: currentEvidence.route,
            candidateRouteFingerprint: candidateEvidence.route,
            routeEvidenceText: ComparisonPresenter.routeEvidenceText(
                currentRoute: currentEvidence.route,
                candidateRoute: candidateEvidence.route
            ),
            sharedCompletionText: sharedCompletion
        )
    }

    static func graderVersion(
        officialGraderVersion: String?,
        questionPackVersion: String?,
        localResults: [GraderResultInput]
    ) -> String? {
        if let officialGraderVersion { return officialGraderVersion }
        return localResults.first {
            $0.questionPackVersion == questionPackVersion && $0.graderVersion != nil
        }?.graderVersion
    }

    static func routing(_ input: RoutingInput) -> RoutingPresentation {
        let usesOfficialSnapshot = input.displaySource == "official_snapshot"
            && input.officialSnapshotIsTrusted
        let usesLocalDataset = input.displaySource == "local_evaluation"
        let questionPackVersion = usesOfficialSnapshot
            ? input.officialQuestionPackVersion ?? input.localQuestionPackVersion
            : input.localQuestionPackVersion
        return RoutingPresentation(
            usesLocalDataset: usesLocalDataset,
            usesOfficialSnapshot: usesOfficialSnapshot,
            questionPackVersion: questionPackVersion,
            graderVersion: graderVersion(
                officialGraderVersion: usesOfficialSnapshot
                    ? input.officialGraderVersion
                    : nil,
                questionPackVersion: questionPackVersion,
                localResults: input.localGraderResults
            ),
            evaluationSnapshotID: usesOfficialSnapshot
                ? input.officialSnapshotID
                : input.localSnapshotID,
            pricingSnapshotID: (usesOfficialSnapshot
                ? input.officialPricingSnapshotID
                : nil)
                ?? input.recommendationPricingSnapshotID
                ?? input.diagnosticPricingSnapshotID
        )
    }

    private struct Evidence {
        let identityDifference: String?
        let connection: String?
        let route: String?
        let completion: String?
    }

    private static func evidence(
        displaySource: String?,
        item: ItemInput
    ) -> Evidence {
        let source = displaySource == "official_snapshot" ? item.official : item.local
        let rawModelID = nonempty(source.rawModelID) ?? item.modelName
        let rawEffort = nonempty(source.rawEffort) ?? item.effort
        var differences: [String] = []
        if rawModelID.caseInsensitiveCompare(item.modelName) != .orderedSame {
            differences.append(L10n.tr("原始 ID：%@", rawModelID))
        }
        if rawEffort.caseInsensitiveCompare(item.effort) != .orderedSame {
            differences.append(L10n.tr("思考档位：%@", rawEffort))
        }
        let connection = source.connectionParts.compactMap(nonempty).joined(separator: " · ")
        return Evidence(
            identityDifference: differences.isEmpty ? nil : differences.joined(separator: " · "),
            connection: connection.isEmpty ? nil : connection,
            route: nonempty(source.routeFingerprint),
            completion: shortTimestamp(nonempty(source.completedAt))
        )
    }

    private static func row(
        item: ItemInput,
        evidence: Evidence,
        sharedConnection: String?,
        sharedCompletion: String?,
        routesDiffer: Bool
    ) -> RowPresentation {
        RowPresentation(
            id: item.id,
            displayName: item.displayName,
            identityDifferenceText: evidence.identityDifference,
            connectionText: sharedConnection == nil ? evidence.connection : nil,
            routeText: routesDiffer ? shortIdentifier(evidence.route) : nil,
            completionText: sharedCompletion == nil ? evidence.completion : nil
        )
    }

    private static func hasDetails(_ row: RowPresentation) -> Bool {
        row.identityDifferenceText != nil
            || row.connectionText != nil
            || row.routeText != nil
            || row.completionText != nil
    }

    private static func shared(_ current: String?, _ candidate: String?) -> String? {
        guard let current, current == candidate else { return nil }
        return current
    }

    private static func sourceLabel(_ displaySource: String?) -> String {
        switch displaySource {
        case "official_snapshot": return L10n.tr("官网实测")
        case "local_evaluation": return L10n.tr("本机实测")
        default: return L10n.tr("不可用")
        }
    }

    private static func nonempty(_ value: String?) -> String? {
        guard let value, !value.isEmpty else { return nil }
        return value
    }

    private static func shortTimestamp(_ value: String?) -> String? {
        guard let value else { return nil }
        return String(value.prefix(16)).replacingOccurrences(of: "T", with: " ")
    }

    private static func shortIdentifier(_ value: String?) -> String {
        guard let value else { return L10n.tr("未知") }
        return value.count > 18 ? "\(value.prefix(8))…\(value.suffix(6))" : value
    }
}
