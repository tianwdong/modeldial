import Foundation
import Security

private func fileKeychainSecret(connectionID: String) throws -> String? {
    let query: [String: Any] = [
        kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: KeychainSecretStore.service,
        kSecAttrAccount as String: connectionID,
        kSecReturnData as String: true,
        kSecMatchLimit as String: kSecMatchLimitOne,
    ]
    var item: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &item)
    if status == errSecItemNotFound {
        return nil
    }
    guard status == errSecSuccess,
          let data = item as? Data,
          let value = String(data: data, encoding: .utf8) else {
        throw KeychainSecretStoreError.operationFailed(status)
    }
    return value
}

@main
private enum KeychainSecretStoreTestMain {
    static func main() {
        let store = KeychainSecretStore()
        let connectionID = "modeldial-keychain-test-\(UUID().uuidString.lowercased())"
        let secret = UUID().uuidString

        defer {
            try? store.delete(connectionID: connectionID)
        }

        do {
            let reference = try store.save(secret, connectionID: connectionID)
            guard try fileKeychainSecret(connectionID: connectionID) == secret else {
                fputs("FAIL: fallback did not persist to the file keychain\n", stderr)
                exit(1)
            }
            try store.delete(reference: reference)
            guard try store.read(reference: reference) == nil else {
                fputs("FAIL: deleted keychain secret is still readable\n", stderr)
                exit(1)
            }
        } catch {
            fputs("FAIL: \(error.localizedDescription)\n", stderr)
            exit(1)
        }

        print("KeychainSecretStore tests passed")
    }
}
