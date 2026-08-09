import Foundation

private var failureCount = 0

private func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() {
        failureCount += 1
        fputs("FAIL: \(message)\n", stderr)
    }
}

private func decoder() -> JSONDecoder {
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase
    return decoder
}

private func fixtureObject(_ name: String) throws -> [String: Any] {
    let url = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        .appendingPathComponent("tests/fixtures/\(name)")
    guard let payload = try JSONSerialization.jsonObject(
        with: Data(contentsOf: url)
    ) as? [String: Any] else {
        throw NSError(domain: "StartupLoadCoordinatorTests", code: 1)
    }
    return payload
}

private func snapshot() throws -> BridgeSnapshot {
    try decoder().decode(
        BridgeSnapshot.self,
        from: JSONSerialization.data(
            withJSONObject: try fixtureObject("architecture_app_snapshot_v2.json")
        )
    )
}

private func recovery(status: String = "no_active_run") throws -> BridgeRunRecoveryResponse {
    try decoder().decode(
        BridgeRunRecoveryResponse.self,
        from: JSONSerialization.data(
            withJSONObject: [
                "ok": true,
                "action": "recover_run",
                "recovered": false,
                "status": status,
                "message": "recovery \(status)",
            ]
        )
    )
}

private func observation() throws -> BridgeStateObservationResponse {
    try decoder().decode(
        BridgeStateObservationResponse.self,
        from: JSONSerialization.data(
            withJSONObject: [
                "schema_version": 1,
                "ok": true,
                "action": "observe_state",
                "status": "observed",
                "message": "observed",
                "state": try fixtureObject("architecture_refresh_snapshot_v1.json"),
            ]
        )
    )
}

private func reference(status: String = "refreshed") throws -> BridgeReferenceRefreshResponse {
    try decoder().decode(
        BridgeReferenceRefreshResponse.self,
        from: JSONSerialization.data(
            withJSONObject: [
                "schema_version": 1,
                "ok": true,
                "action": "refresh_reference",
                "status": status,
                "message": "reference \(status)",
                "state": try fixtureObject("architecture_app_snapshot_v2.json"),
            ]
        )
    )
}

private struct FixtureError: LocalizedError {
    let detail: String

    var errorDescription: String? { detail }
}

private func verifySuccessfulMaintenanceClaimIsSingleUse() {
    var coordinator = StartupLoadCoordinator()
    expect(
        coordinator.claimMaintenanceIfNeeded(),
        "the first load should claim startup maintenance"
    )
    coordinator.recordMaintenanceResult(successfully: true)
    expect(
        !coordinator.claimMaintenanceIfNeeded(),
        "successful maintenance should not be claimed again"
    )
}

private func verifyFailedMaintenanceCanBeClaimedAgain() throws {
    var coordinator = StartupLoadCoordinator()
    expect(
        coordinator.claimMaintenanceIfNeeded(),
        "the first load should claim startup maintenance"
    )
    let result = try StartupLoadCoordinator.load(
        recoverRun: {
            throw FixtureError(detail: "recover failed")
        },
        observeState: {
            throw FixtureError(detail: "observe failed")
        },
        snapshot: { try snapshot() }
    )
    expect(
        result.warningDetail?.contains("recover failed") == true
            && result.warningDetail?.contains("observe failed") == true,
        "recover and observe warnings should remain visible"
    )
    coordinator.recordMaintenanceResult(successfully: result.warningDetail == nil)
    expect(
        coordinator.claimMaintenanceIfNeeded(),
        "a failed startup maintenance attempt should be retryable"
    )
}

private func verifyMaintenanceStopsAfterMaximumAttempts() {
    var coordinator = StartupLoadCoordinator()
    for attempt in 0..<StartupLoadCoordinator.maximumMaintenanceAttempts {
        expect(
            coordinator.claimMaintenanceIfNeeded(),
            "maintenance attempt \(attempt + 1) should be available"
        )
        coordinator.recordMaintenanceResult(successfully: false)
    }
    expect(
        !coordinator.canRetryMaintenance,
        "maintenance should report no retry after its bounded attempts"
    )
    expect(
        !coordinator.claimMaintenanceIfNeeded(),
        "maintenance should stop after its bounded attempts"
    )
}

private func verifyStrictOrder() throws {
    var calls: [String] = []
    let result = try StartupLoadCoordinator.load(
        recoverRun: {
            calls.append("recover")
            return try recovery()
        },
        observeState: {
            calls.append("observe")
            return try observation()
        },
        snapshot: {
            calls.append("snapshot")
            return try snapshot()
        }
    )
    expect(
        calls == ["recover", "observe", "snapshot"],
        "startup maintenance should preserve command order"
    )
    expect(result.warningDetail == nil, "normal startup should not publish a warning")
    expect(result.referenceRefreshStatus == nil, "startup must not wait for remote data")
    expect(result.snapshot.runtime.lifecycleState == .idle, "startup should return snapshot")
}

private func verifyMaintenanceFailuresRemainNonBlocking() throws {
    var calls: [String] = []
    let result = try StartupLoadCoordinator.load(
        recoverRun: {
            calls.append("recover")
            throw FixtureError(detail: "recover failed")
        },
        observeState: {
            calls.append("observe")
            throw FixtureError(detail: "observe failed")
        },
        snapshot: {
            calls.append("snapshot")
            return try snapshot()
        }
    )
    let detail = result.warningDetail ?? ""
    expect(calls.last == "snapshot", "maintenance failures should not block snapshot")
    expect(detail.contains("recover failed"), "recovery failure should be reported")
    expect(detail.contains("observe failed"), "observation failure should be reported")
}

private func verifyIncompleteRecoveryRequiresAttention() throws {
    let result = try StartupLoadCoordinator.load(
        recoverRun: { try recovery(status: "incomplete") },
        observeState: { try observation() },
        snapshot: { try snapshot() }
    )
    expect(
        result.warningDetail?.contains("recovery incomplete") == true,
        "incomplete recovery should remain visible after snapshot"
    )
}

private func verifyReferenceRefreshRunsSeparately() throws {
    let refreshed = try StartupLoadCoordinator.refreshReference(
        refreshReference: { try reference(status: "not_modified") },
        snapshot: { try snapshot() }
    )
    expect(
        refreshed.referenceRefreshStatus == "not_modified",
        "a conditional refresh should expose its terminal status"
    )
    expect(refreshed.warningDetail == nil, "not-modified is a successful refresh")

    let failed = try StartupLoadCoordinator.refreshReference(
        refreshReference: { try reference(status: "failed") },
        snapshot: { try snapshot() }
    )
    expect(failed.referenceRefreshStatus == "failed", "failure status should be preserved")
    expect(
        failed.warningDetail?.contains("reference failed") == true,
        "reference failure should remain visible without blocking the snapshot"
    )
}

private func verifySnapshotFailurePropagates() {
    var coordinator = StartupLoadCoordinator()
    expect(
        coordinator.claimMaintenanceIfNeeded(),
        "a startup load with a snapshot failure should consume one attempt"
    )
    do {
        _ = try StartupLoadCoordinator.load(
            recoverRun: { try recovery() },
            observeState: { try observation() },
            snapshot: { throw FixtureError(detail: "snapshot failed") }
        )
        expect(false, "snapshot failure should propagate")
    } catch {
        expect(
            error.localizedDescription == "snapshot failed",
            "snapshot failure detail should be preserved"
        )
    }
    coordinator.recordMaintenanceResult(successfully: false)
    expect(
        coordinator.claimMaintenanceIfNeeded(),
        "a snapshot failure should leave a later startup retry opportunity"
    )
}

@main
private enum StartupLoadCoordinatorTestMain {
    static func main() {
        do {
            verifySuccessfulMaintenanceClaimIsSingleUse()
            try verifyFailedMaintenanceCanBeClaimedAgain()
            verifyMaintenanceStopsAfterMaximumAttempts()
            try verifyStrictOrder()
            try verifyMaintenanceFailuresRemainNonBlocking()
            try verifyIncompleteRecoveryRequiresAttention()
            try verifyReferenceRefreshRunsSeparately()
            verifySnapshotFailurePropagates()
        } catch {
            failureCount += 1
            fputs("FAIL: \(error)\n", stderr)
        }
        if failureCount > 0 {
            exit(1)
        }
        print("Startup load coordinator tests passed")
    }
}
