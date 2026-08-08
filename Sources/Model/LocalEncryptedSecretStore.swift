import CryptoKit
import Foundation

enum LocalEncryptedSecretStoreError: LocalizedError {
    case invalidSecret
    case invalidReference
    case storageUnavailable
    case encryptionFailed
    case decryptionFailed

    var errorDescription: String? {
        switch self {
        case .invalidSecret:
            return "API Key 不能为空。"
        case .invalidReference:
            return "秘钥引用无效。"
        case .storageUnavailable:
            return "无法访问本地加密秘钥存储。"
        case .encryptionFailed:
            return "无法加密保存 API Key。"
        case .decryptionFailed:
            return "无法读取已保存的 API Key。"
        }
    }
}

struct LocalEncryptedSecretStore {
    static let referencePrefix = "local_encrypted:"

    private struct Envelope: Codable {
        var version = 1
        var secrets: [String: Record] = [:]
    }

    private struct Record: Codable {
        let combined: String
        let updatedAt: String
    }

    private let fileManager = FileManager.default
    private let iso8601 = ISO8601DateFormatter()

    func reference(connectionID: String) -> String {
        "\(Self.referencePrefix)\(connectionID)"
    }

    func save(_ secret: String, connectionID: String) throws -> String {
        guard !secret.isEmpty, let data = secret.data(using: .utf8) else {
            throw LocalEncryptedSecretStoreError.invalidSecret
        }
        let key = try loadOrCreateMasterKey()
        var envelope = try loadEnvelope()
        guard let combined = try AES.GCM.seal(data, using: key).combined else {
            throw LocalEncryptedSecretStoreError.encryptionFailed
        }
        envelope.secrets[connectionID] = Record(
            combined: combined.base64EncodedString(),
            updatedAt: iso8601.string(from: Date())
        )
        try writeEnvelope(envelope)
        return reference(connectionID: connectionID)
    }

    func read(reference: String) throws -> String? {
        let connectionID = try connectionID(from: reference)
        let envelope = try loadEnvelope()
        guard let record = envelope.secrets[connectionID] else {
            return nil
        }
        let key = try loadExistingMasterKey()
        guard let combined = Data(base64Encoded: record.combined) else {
            throw LocalEncryptedSecretStoreError.decryptionFailed
        }
        let sealedBox: AES.GCM.SealedBox
        do {
            sealedBox = try AES.GCM.SealedBox(combined: combined)
        } catch {
            throw LocalEncryptedSecretStoreError.decryptionFailed
        }
        let cleartext: Data
        do {
            cleartext = try AES.GCM.open(sealedBox, using: key)
        } catch {
            throw LocalEncryptedSecretStoreError.decryptionFailed
        }
        guard let secret = String(data: cleartext, encoding: .utf8) else {
            throw LocalEncryptedSecretStoreError.decryptionFailed
        }
        return secret
    }

    func delete(reference: String) throws {
        let connectionID = try connectionID(from: reference)
        var envelope = try loadEnvelope()
        envelope.secrets.removeValue(forKey: connectionID)
        try writeEnvelope(envelope)
    }

    private func connectionID(from reference: String) throws -> String {
        guard reference.hasPrefix(Self.referencePrefix) else {
            throw LocalEncryptedSecretStoreError.invalidReference
        }
        let connectionID = String(reference.dropFirst(Self.referencePrefix.count))
        guard !connectionID.isEmpty else {
            throw LocalEncryptedSecretStoreError.invalidReference
        }
        return connectionID
    }

    private func loadEnvelope() throws -> Envelope {
        let url = try storeFileURL()
        guard fileManager.fileExists(atPath: url.path) else {
            return Envelope()
        }
        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode(Envelope.self, from: data)
    }

    private func writeEnvelope(_ envelope: Envelope) throws {
        let url = try storeFileURL()
        let data = try JSONEncoder().encode(envelope)
        try data.write(to: url, options: .atomic)
        try setFilePermissions(url: url, mode: 0o600)
    }

    private func loadOrCreateMasterKey() throws -> SymmetricKey {
        let url = try masterKeyFileURL()
        if fileManager.fileExists(atPath: url.path) {
            return try loadKey(at: url)
        }
        let key = SymmetricKey(size: .bits256)
        let keyData = key.withUnsafeBytes { Data($0) }
        try keyData.write(to: url, options: .atomic)
        try setFilePermissions(url: url, mode: 0o600)
        return key
    }

    private func loadExistingMasterKey() throws -> SymmetricKey {
        let url = try masterKeyFileURL()
        guard fileManager.fileExists(atPath: url.path) else {
            throw LocalEncryptedSecretStoreError.storageUnavailable
        }
        return try loadKey(at: url)
    }

    private func loadKey(at url: URL) throws -> SymmetricKey {
        let keyData = try Data(contentsOf: url)
        guard keyData.count == 32 else {
            throw LocalEncryptedSecretStoreError.storageUnavailable
        }
        return SymmetricKey(data: keyData)
    }

    private func masterKeyFileURL() throws -> URL {
        try secretsDirectoryURL().appendingPathComponent("master.key", isDirectory: false)
    }

    private func storeFileURL() throws -> URL {
        try secretsDirectoryURL().appendingPathComponent("store.json", isDirectory: false)
    }

    private func secretsDirectoryURL() throws -> URL {
        let applicationSupport = fileManager.homeDirectoryForCurrentUser
            .appendingPathComponent("Library", isDirectory: true)
            .appendingPathComponent("Application Support", isDirectory: true)
        let base = applicationSupport
            .appendingPathComponent("modeldial", isDirectory: true)
            .appendingPathComponent("Secrets", isDirectory: true)
        let legacy = applicationSupport
            .appendingPathComponent("ModelPilot", isDirectory: true)
            .appendingPathComponent("Secrets", isDirectory: true)
        if !fileManager.fileExists(atPath: base.path),
           fileManager.fileExists(atPath: legacy.path) {
            try fileManager.createDirectory(
                at: base.deletingLastPathComponent(),
                withIntermediateDirectories: true,
                attributes: nil
            )
            try fileManager.copyItem(at: legacy, to: base)
        }
        try fileManager.createDirectory(
            at: base,
            withIntermediateDirectories: true,
            attributes: nil
        )
        try setFilePermissions(url: base, mode: 0o700)
        return base
    }

    private func setFilePermissions(url: URL, mode: Int16) throws {
        try fileManager.setAttributes(
            [.posixPermissions: NSNumber(value: mode)],
            ofItemAtPath: url.path
        )
    }
}
