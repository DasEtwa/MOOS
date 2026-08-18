import Foundation
import Security

protocol DeviceCredentialStoring: Sendable {
    func loadKey(deviceID: String) throws -> Data?
    func saveKey(_ key: Data, deviceID: String) throws
    func removeKey(deviceID: String) throws
}

enum DeviceCredentialStoreError: Error, LocalizedError {
    case keychainFailure

    var errorDescription: String? {
        "The paired device credential could not be accessed."
    }
}

final class KeychainDeviceCredentialStore: DeviceCredentialStoring, @unchecked Sendable {
    private let service: String

    init(service: String = "dev.moos.gateway.device-key.v1") {
        self.service = service
    }

    func loadKey(deviceID: String) throws -> Data? {
        var query = baseQuery(deviceID: deviceID)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound {
            return nil
        }
        guard status == errSecSuccess, let data = result as? Data else {
            throw DeviceCredentialStoreError.keychainFailure
        }
        return data
    }

    func saveKey(_ key: Data, deviceID: String) throws {
        guard key.count == MOOSGatewayV1.deviceKeyBytes else {
            throw DeviceCredentialStoreError.keychainFailure
        }
        var attributes = baseQuery(deviceID: deviceID)
        attributes[kSecValueData as String] = key
        attributes[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        let status = SecItemAdd(attributes as CFDictionary, nil)
        if status == errSecDuplicateItem {
            let update: [String: Any] = [
                kSecValueData as String: key,
                kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
            ]
            guard SecItemUpdate(
                baseQuery(deviceID: deviceID) as CFDictionary,
                update as CFDictionary
            ) == errSecSuccess else {
                throw DeviceCredentialStoreError.keychainFailure
            }
            return
        }
        guard status == errSecSuccess else {
            throw DeviceCredentialStoreError.keychainFailure
        }
    }

    func removeKey(deviceID: String) throws {
        let status = SecItemDelete(baseQuery(deviceID: deviceID) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw DeviceCredentialStoreError.keychainFailure
        }
    }

    private func baseQuery(deviceID: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: deviceID,
        ]
    }
}
