import Foundation
import Security

private var failureCount = 0

private func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() {
        failureCount += 1
        fputs("FAIL: \(message)\n", stderr)
    }
}

private func makeRoot() throws -> URL {
    let root = URL(fileURLWithPath: NSTemporaryDirectory())
        .appendingPathComponent("modeldial-secret-migration-\(UUID().uuidString)")
    try FileManager.default.createDirectory(
        at: root.appendingPathComponent("scripts", isDirectory: true),
        withIntermediateDirectories: true
    )
    return root
}

private func writeConfig(
    root: URL,
    connectionID: String,
    candidateID: String,
    apiKeyReference: String
) throws {
    let fixture = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        .appendingPathComponent("tests/fixtures/architecture_app_snapshot_v2.json")
    let snapshot = try JSONSerialization.jsonObject(
        with: Data(contentsOf: fixture)
    ) as! [String: Any]
    var config = snapshot["config"] as! [String: Any]
    config["model_ingress"] = [
        "sources": [[
            "id": "test-endpoint-source",
            "kind": "custom_endpoint",
            "title": "Test endpoint",
            "description": "Secret migration fixture",
            "mode": "api",
            "enabled": true,
        ]],
        "connections": [[
            "id": connectionID,
            "source_id": "test-endpoint-source",
            "name": "Test endpoint",
            "enabled": true,
            "api_format": "openai_responses",
            "provider_preset": "generic",
            "base_url": "https://example.invalid/v1",
            "api_key_ref": apiKeyReference,
            "last_test_status": "ok",
            "model_candidates": [[
                "id": candidateID,
                "connection_id": connectionID,
                "model_id": "test-model",
                "display_name": "Test model",
                "enabled": true,
                "scan_profile": "medium",
                "capabilities": [],
            ]],
        ]],
    ]
    try JSONSerialization.data(withJSONObject: config).write(
        to: root.appendingPathComponent("fixture-config.json")
    )
}

private func writeBridgeScript(root: URL, rejectMigration: Bool) throws {
    if rejectMigration {
        try Data().write(to: root.appendingPathComponent("reject-migration"))
    }
    let script = """
    import json
    import pathlib
    import sys

    root = pathlib.Path(__file__).resolve().parent.parent
    command = sys.argv[1]
    with (root / "commands.txt").open("a", encoding="utf-8") as handle:
        handle.write(command + "\\n")

    if command == "read-config":
        print((root / "fixture-config.json").read_text(encoding="utf-8"))
        raise SystemExit(0)

    if command == "migrate-secret-references":
        payload_index = sys.argv.index("--payload") + 1
        payload = json.loads(sys.argv[payload_index])
        (root / "migration-payload.json").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )
        if (root / "reject-migration").exists():
            print("migration rejected", file=sys.stderr)
            raise SystemExit(9)
        print(json.dumps({
            "schema_version": 1,
            "ok": True,
            "action": "migrate_secret_references",
            "operation": "connection_secret_references",
        }))
        raise SystemExit(0)

    if command == "scan":
        secret_input = sys.stdin.read() if "--secret-stdin" in sys.argv else "{}"
        (root / "scan-secret-input.json").write_text(
            secret_input,
            encoding="utf-8",
        )
        raise SystemExit(0)

    print("unexpected command: " + command, file=sys.stderr)
    raise SystemExit(17)
    """
    try Data(script.utf8).write(
        to: root.appendingPathComponent("scripts/native_bridge.py")
    )
}

private func saveLegacySecret(_ secret: String, connectionID: String) throws -> String {
    let service = "com.modelpilot.api-key"
    let query: [String: Any] = [
        kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: service,
        kSecAttrAccount as String: connectionID,
    ]
    SecItemDelete(query as CFDictionary)
    var add = query
    add[kSecValueData as String] = Data(secret.utf8)
    let status = SecItemAdd(add as CFDictionary, nil)
    guard status == errSecSuccess else {
        throw NSError(
            domain: NSOSStatusErrorDomain,
            code: Int(status)
        )
    }
    return "keychain:\(service):\(connectionID)"
}

private func runScan(
    client: NativeBridgeClient,
    candidateID: String
) async throws {
    try await withCheckedThrowingContinuation {
        (continuation: CheckedContinuation<Void, Error>) in
        do {
            try client.startScan(
                intent: BridgeScanIntent(
                    candidateIDs: [candidateID],
                    selectionMode: .single
                ),
                onEvent: { _ in },
                onComplete: { continuation.resume() }
            )
        } catch {
            continuation.resume(throwing: error)
        }
    }
}

private func commands(in root: URL) throws -> [String] {
    try String(
        contentsOf: root.appendingPathComponent("commands.txt"),
        encoding: .utf8
    )
    .split(whereSeparator: \.isNewline)
    .map(String.init)
}

private func secretInput(in root: URL) throws -> [String: String] {
    try JSONSerialization.jsonObject(
        with: Data(
            contentsOf: root.appendingPathComponent("scan-secret-input.json")
        )
    ) as! [String: String]
}

private func verifyNormalScanDoesNotRequestSnapshot() async throws {
    let root = try makeRoot()
    let connectionID = "normal-\(UUID().uuidString.lowercased())"
    let candidateID = "\(connectionID):test-model:medium"
    let secret = "normal-secret-\(UUID().uuidString)"
    let store = KeychainSecretStore()
    defer {
        try? store.delete(connectionID: connectionID)
        try? FileManager.default.removeItem(at: root)
    }
    let reference = try store.save(secret, connectionID: connectionID)
    try writeConfig(
        root: root,
        connectionID: connectionID,
        candidateID: candidateID,
        apiKeyReference: reference
    )
    try writeBridgeScript(root: root, rejectMigration: false)

    try await runScan(
        client: NativeBridgeClient(repoRoot: root, dataDirectory: root),
        candidateID: candidateID
    )
    let recordedCommands = try commands(in: root)
    let recordedSecretInput = try secretInput(in: root)

    expect(
        recordedCommands == ["read-config", "scan"],
        "a normal scan must not request a snapshot or migration"
    )
    expect(
        recordedSecretInput == [reference: secret],
        "a normal scan should forward the current keychain reference"
    )
}

private func verifySuccessfulMigrationUsesAckWithoutSnapshot() async throws {
    let root = try makeRoot()
    let connectionID = "migration-\(UUID().uuidString.lowercased())"
    let candidateID = "\(connectionID):test-model:medium"
    let secret = "migration-secret-\(UUID().uuidString)"
    let store = KeychainSecretStore()
    defer {
        try? store.delete(connectionID: connectionID)
        try? FileManager.default.removeItem(at: root)
    }
    let oldReference = try saveLegacySecret(secret, connectionID: connectionID)
    let newReference = store.reference(connectionID: connectionID)
    try writeConfig(
        root: root,
        connectionID: connectionID,
        candidateID: candidateID,
        apiKeyReference: oldReference
    )
    try writeBridgeScript(root: root, rejectMigration: false)

    try await runScan(
        client: NativeBridgeClient(repoRoot: root, dataDirectory: root),
        candidateID: candidateID
    )
    let recordedCommands = try commands(in: root)
    let recordedSecretInput = try secretInput(in: root)
    let oldSecret = try store.read(reference: oldReference)

    expect(
        recordedCommands == ["read-config", "migrate-secret-references", "scan"],
        "migration should use only the lightweight command before scan"
    )
    expect(
        recordedSecretInput == [newReference: secret],
        "successful migration should forward the new reference"
    )
    let payload = try JSONSerialization.jsonObject(
        with: Data(contentsOf: root.appendingPathComponent("migration-payload.json"))
    ) as! [String: Any]
    expect(
        payload["operation"] as? String == "connection_secret_references",
        "migration should send only the secret-reference patch operation"
    )
    expect(
        oldSecret == nil,
        "the old reference should be removed only after acknowledgement"
    )
}

private func verifyFailedMigrationFallsBackToOldReference() async throws {
    let root = try makeRoot()
    let connectionID = "fallback-\(UUID().uuidString.lowercased())"
    let candidateID = "\(connectionID):test-model:medium"
    let secret = "fallback-secret-\(UUID().uuidString)"
    let store = KeychainSecretStore()
    defer {
        try? store.delete(connectionID: connectionID)
        try? FileManager.default.removeItem(at: root)
    }
    let oldReference = try saveLegacySecret(secret, connectionID: connectionID)
    try writeConfig(
        root: root,
        connectionID: connectionID,
        candidateID: candidateID,
        apiKeyReference: oldReference
    )
    try writeBridgeScript(root: root, rejectMigration: true)

    try await runScan(
        client: NativeBridgeClient(repoRoot: root, dataDirectory: root),
        candidateID: candidateID
    )
    let recordedCommands = try commands(in: root)
    let recordedSecretInput = try secretInput(in: root)
    let retainedSecret = try store.read(reference: oldReference)

    expect(
        recordedCommands == ["read-config", "migrate-secret-references", "scan"],
        "a rejected migration should continue directly to scan"
    )
    expect(
        recordedSecretInput == [oldReference: secret],
        "a rejected migration should fall back to the old reference"
    )
    expect(
        retainedSecret == secret,
        "a rejected migration must retain the old secret"
    )
}

@main
private enum NativeBridgeSecretMigrationTestMain {
    static func main() async {
        do {
            try await verifyNormalScanDoesNotRequestSnapshot()
            try await verifySuccessfulMigrationUsesAckWithoutSnapshot()
            try await verifyFailedMigrationFallsBackToOldReference()
        } catch {
            failureCount += 1
            fputs("FAIL: \(error)\n", stderr)
        }
        if failureCount > 0 {
            exit(1)
        }
        print("Native bridge secret migration tests passed")
    }
}
