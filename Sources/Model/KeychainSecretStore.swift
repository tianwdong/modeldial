import Foundation
import Security

enum KeychainSecretStoreError: LocalizedError {
    case invalidSecret
    case operationFailed(OSStatus)

    var errorDescription: String? {
        switch self {
        case .invalidSecret:
            return "API Key 不能为空。"
        case .operationFailed(let status):
            return "无法访问系统钥匙串（\(status)）。"
        }
    }
}

struct KeychainSecretStore {
    static let service = "com.modeldial.api-key"
    private static let legacyService = "com.modelpilot.api-key"
    private static let cacheLock = NSLock()
    private static var processCache: [String: String] = [:]

    func reference(connectionID: String) -> String {
        "keychain:\(Self.service):\(connectionID)"
    }

    func save(_ secret: String, connectionID: String) throws -> String {
        guard !secret.isEmpty, let data = secret.data(using: .utf8) else {
            throw KeychainSecretStoreError.invalidSecret
        }
        let usedDataProtection: Bool
        do {
            try saveSecret(
                data,
                connectionID: connectionID,
                useDataProtection: true
            )
            usedDataProtection = true
        } catch KeychainSecretStoreError.operationFailed(let status)
            where status == errSecMissingEntitlement {
            try saveSecret(
                data,
                connectionID: connectionID,
                useDataProtection: false
            )
            usedDataProtection = false
        }
        if usedDataProtection {
            try? deleteItem(
                service: Self.service,
                connectionID: connectionID,
                useDataProtection: false
            )
        }
        Self.cache(secret: secret, service: Self.service, connectionID: connectionID)
        return reference(connectionID: connectionID)
    }

    private func saveSecret(
        _ data: Data,
        connectionID: String,
        useDataProtection: Bool
    ) throws {
        let query = baseQuery(
            service: Self.service,
            connectionID: connectionID,
            useDataProtection: useDataProtection
        )
        let updateStatus = SecItemUpdate(
            query as CFDictionary,
            [kSecValueData as String: data] as CFDictionary
        )
        if updateStatus == errSecItemNotFound {
            var addQuery = query
            addQuery[kSecValueData as String] = data
            if useDataProtection {
                addQuery[kSecAttrAccessible as String] =
                    kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            }
            let addStatus = SecItemAdd(addQuery as CFDictionary, nil)
            guard addStatus == errSecSuccess else {
                throw KeychainSecretStoreError.operationFailed(addStatus)
            }
        } else if updateStatus != errSecSuccess {
            throw KeychainSecretStoreError.operationFailed(updateStatus)
        }
    }

    func read(connectionID: String) throws -> String? {
        if let secret = try readSecret(service: Self.service, connectionID: connectionID) {
            return secret
        }
        guard let legacySecret = try readSecret(
            service: Self.legacyService,
            connectionID: connectionID
        ) else {
            return nil
        }
        _ = try? save(legacySecret, connectionID: connectionID)
        return legacySecret
    }

    func read(reference: String) throws -> String? {
        guard let item = referenceItem(reference) else { return nil }
        return try readSecret(service: item.service, connectionID: item.connectionID)
    }

    func migrateLegacyReference(_ reference: String, secret: String) throws -> String? {
        guard let item = referenceItem(reference), item.service == Self.legacyService else {
            return nil
        }
        return try save(secret, connectionID: item.connectionID)
    }

    private func readSecret(service: String, connectionID: String) throws -> String? {
        if let cached = Self.cachedSecret(service: service, connectionID: connectionID) {
            return cached
        }
        for useDataProtection in [true, false] {
            var query = baseQuery(
                service: service,
                connectionID: connectionID,
                useDataProtection: useDataProtection
            )
            query[kSecReturnData as String] = true
            query[kSecMatchLimit as String] = kSecMatchLimitOne
            var item: CFTypeRef?
            let status = SecItemCopyMatching(query as CFDictionary, &item)
            if status == errSecItemNotFound {
                continue
            }
            if useDataProtection && status == errSecMissingEntitlement {
                continue
            }
            guard status == errSecSuccess,
                  let data = item as? Data,
                  let value = String(data: data, encoding: .utf8)
            else {
                throw KeychainSecretStoreError.operationFailed(status)
            }
            Self.cache(secret: value, service: service, connectionID: connectionID)
            if !useDataProtection, service == Self.service {
                _ = try? save(value, connectionID: connectionID)
            }
            return value
        }
        return nil
    }

    func delete(connectionID: String) throws {
        for service in [Self.service, Self.legacyService] {
            for useDataProtection in [true, false] {
                try deleteItem(
                    service: service,
                    connectionID: connectionID,
                    useDataProtection: useDataProtection
                )
            }
            Self.removeCachedSecret(service: service, connectionID: connectionID)
        }
    }

    func delete(reference: String) throws {
        guard let item = referenceItem(reference) else { return }
        for useDataProtection in [true, false] {
            try deleteItem(
                service: item.service,
                connectionID: item.connectionID,
                useDataProtection: useDataProtection
            )
        }
        Self.removeCachedSecret(service: item.service, connectionID: item.connectionID)
    }

    private func deleteItem(
        service: String,
        connectionID: String,
        useDataProtection: Bool
    ) throws {
        let status = SecItemDelete(
            baseQuery(
                service: service,
                connectionID: connectionID,
                useDataProtection: useDataProtection
            ) as CFDictionary
        )
        if useDataProtection && status == errSecMissingEntitlement {
            return
        }
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw KeychainSecretStoreError.operationFailed(status)
        }
    }

    private static func cacheKey(service: String, connectionID: String) -> String {
        "\(service):\(connectionID)"
    }

    private static func cachedSecret(service: String, connectionID: String) -> String? {
        cacheLock.lock()
        defer { cacheLock.unlock() }
        return processCache[cacheKey(service: service, connectionID: connectionID)]
    }

    private static func cache(secret: String, service: String, connectionID: String) {
        cacheLock.lock()
        defer { cacheLock.unlock() }
        processCache[cacheKey(service: service, connectionID: connectionID)] = secret
    }

    private static func removeCachedSecret(service: String, connectionID: String) {
        cacheLock.lock()
        defer { cacheLock.unlock() }
        processCache.removeValue(forKey: cacheKey(service: service, connectionID: connectionID))
    }

    private func referenceItem(_ reference: String) -> (service: String, connectionID: String)? {
        let parts = reference.split(
            separator: ":",
            maxSplits: 2,
            omittingEmptySubsequences: false
        )
        guard parts.count == 3,
              parts[0] == "keychain",
              [Self.service, Self.legacyService].contains(String(parts[1])),
              !parts[2].isEmpty else {
            return nil
        }
        return (String(parts[1]), String(parts[2]))
    }

    private func baseQuery(
        service: String,
        connectionID: String,
        useDataProtection: Bool
    ) -> [String: Any] {
        var query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: connectionID,
        ]
        if useDataProtection {
            query[kSecUseDataProtectionKeychain as String] = true
        }
        return query
    }
}
