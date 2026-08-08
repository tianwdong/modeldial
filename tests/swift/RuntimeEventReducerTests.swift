import Foundation

private var failureCount = 0

private func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() {
        failureCount += 1
        fputs("FAIL: \(message)\n", stderr)
    }
}

private func decodeEvent(_ payload: [String: Any]) throws -> ScanEvent {
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase
    return try decoder.decode(
        ScanEvent.self,
        from: JSONSerialization.data(withJSONObject: payload)
    )
}

private func authoritativeFailurePayload() throws -> [String: Any] {
    let fixtureURL = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        .appendingPathComponent("tests/fixtures/architecture_batch_failure_events_v1.json")
    let fixture = try JSONSerialization.jsonObject(
        with: Data(contentsOf: fixtureURL)
    ) as! [[String: Any]]
    return fixture.first { $0["type"] as? String == "repair.failed" }!
}

private func startedPayload() throws -> [String: Any] {
    let fixtureURL = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        .appendingPathComponent("tests/fixtures/architecture_scan_events_v1.json")
    let fixture = try JSONSerialization.jsonObject(
        with: Data(contentsOf: fixtureURL)
    ) as! [[String: Any]]
    return fixture.first { $0["type"] as? String == "scan.started" }!
}

private func appSnapshotPayload() throws -> [String: Any] {
    let fixtureURL = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        .appendingPathComponent("tests/fixtures/architecture_app_snapshot_v2.json")
    return try JSONSerialization.jsonObject(
        with: Data(contentsOf: fixtureURL)
    ) as! [String: Any]
}

@main
private enum RuntimeEventReducerTestMain {
    static func main() throws {
        let authoritativeFailure = try decodeEvent(
            authoritativeFailurePayload()
        )
        if case .snapshot(let snapshot) = RuntimeEventReducer.stateUpdate(
            for: authoritativeFailure
        ) {
            expect(
                snapshot.runtime.lifecycleState == .failed,
                "failure must preserve the authoritative terminal snapshot"
            )
        } else {
            expect(false, "failure snapshot must be selected")
        }
        let bridgeFailure = ScanEvent.bridgeFailure(message: "bridge stopped")
        if case .none = RuntimeEventReducer.stateUpdate(for: bridgeFailure) {
            expect(true, "state-less bridge failure should stay state-less")
        } else {
            expect(false, "state-less bridge failure must not synthesize runtime")
        }

        let started = try decodeEvent(startedPayload())
        if case .runtime(let runtime) = RuntimeEventReducer.stateUpdate(for: started) {
            expect(runtime.lifecycleState == .activeScan, "started event should use backend active lifecycle")
            expect(runtime.progressCompleted == 0, "started event should preserve backend progress")
            expect(runtime.progressTotal == 1, "started event should preserve backend total")
        } else {
            expect(false, "started event must select its runtime delta")
        }

        let autoResumeMarker = try decodeEvent([
            "schema_version": 1,
            "state_kind": "snapshot",
            "type": "auto-resume.manual-attention",
            "reason": "attempt_limit_reached",
            "message": "manual check required",
            "attempt": 2,
            "state": try appSnapshotPayload(),
        ])
        expect(
            autoResumeMarker.reason == "attempt_limit_reached",
            "auto-resume marker should expose its backend reason"
        )
        expect(
            autoResumeMarker.message == "manual check required",
            "auto-resume marker should expose its backend message"
        )
        if case .snapshot = RuntimeEventReducer.stateUpdate(for: autoResumeMarker) {
            expect(true, "auto-resume terminal should apply its authoritative snapshot")
        } else {
            expect(false, "auto-resume terminal must carry authoritative state")
        }

        if failureCount > 0 {
            exit(1)
        }
        print("Runtime event reducer tests passed")
    }
}
