import Foundation

struct BridgeSecretResolution {
    let originalReference: String
    let bridgeReference: String
    let secret: String
    let updatedConfigReference: String?
}

struct AppSecretStore {
    private let localEncrypted = LocalEncryptedSecretStore()
    private let keychain = KeychainSecretStore()

    func save(_ secret: String, connectionID: String) throws -> String {
        try keychain.save(secret, connectionID: connectionID)
    }

    func stage(_ secret: String, connectionID: String) throws -> String {
        try keychain.save(
            secret,
            connectionID: "\(connectionID)-pending-\(UUID().uuidString.lowercased())"
        )
    }

    func deleteReference(_ apiKeyRef: String, connectionID: String) throws {
        if apiKeyRef.hasPrefix(LocalEncryptedSecretStore.referencePrefix) {
            try localEncrypted.delete(reference: apiKeyRef)
        } else if apiKeyRef.hasPrefix("keychain:") {
            try keychain.delete(reference: apiKeyRef)
        }
    }

    func delete(connectionID: String, apiKeyRef: String?) throws {
        guard let apiKeyRef else { return }
        try deleteReference(apiKeyRef, connectionID: connectionID)
        try? keychain.delete(connectionID: connectionID)
        try? localEncrypted.delete(
            reference: localEncrypted.reference(connectionID: connectionID)
        )
    }

    func bridgeSecret(
        connectionID: String,
        apiKeyRef: String?
    ) throws -> BridgeSecretResolution? {
        guard let apiKeyRef else { return nil }
        if apiKeyRef.hasPrefix(LocalEncryptedSecretStore.referencePrefix) {
            if let secret = try localEncrypted.read(reference: apiKeyRef) {
                if let migratedReference = try? keychain.save(
                    secret,
                    connectionID: connectionID
                ) {
                    return BridgeSecretResolution(
                        originalReference: apiKeyRef,
                        bridgeReference: migratedReference,
                        secret: secret,
                        updatedConfigReference: migratedReference
                    )
                }
                return BridgeSecretResolution(
                    originalReference: apiKeyRef,
                    bridgeReference: apiKeyRef,
                    secret: secret,
                    updatedConfigReference: nil
                )
            }
            if let fallbackSecret = try keychain.read(connectionID: connectionID) {
                let fallbackReference = keychain.reference(connectionID: connectionID)
                return BridgeSecretResolution(
                    originalReference: apiKeyRef,
                    bridgeReference: fallbackReference,
                    secret: fallbackSecret,
                    updatedConfigReference: fallbackReference
                )
            }
            return nil
        }
        if apiKeyRef.hasPrefix("keychain:") {
            guard let secret = try keychain.read(reference: apiKeyRef) else {
                guard let fallbackSecret = try keychain.read(connectionID: connectionID) else {
                    return nil
                }
                let fallbackReference = keychain.reference(connectionID: connectionID)
                return BridgeSecretResolution(
                    originalReference: apiKeyRef,
                    bridgeReference: fallbackReference,
                    secret: fallbackSecret,
                    updatedConfigReference: fallbackReference
                )
            }
            if let migratedReference = try? keychain.migrateLegacyReference(
                apiKeyRef,
                secret: secret
            ) {
                return BridgeSecretResolution(
                    originalReference: apiKeyRef,
                    bridgeReference: migratedReference,
                    secret: secret,
                    updatedConfigReference: migratedReference
                )
            }
            return BridgeSecretResolution(
                originalReference: apiKeyRef,
                bridgeReference: apiKeyRef,
                secret: secret,
                updatedConfigReference: nil
            )
        }
        return nil
    }
}
