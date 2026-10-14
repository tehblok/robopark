import Foundation
import Security
import AgentCore
import CryptoKit

enum SecureStore {
    private static let service = "ru.robopark.agent"
    private static func query(_ account: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service, kSecAttrAccount as String: account]
    }
    static func readData(_ account: String) throws -> Data? {
        var request = query(account)
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = item as? Data else {
            throw NSError(domain: "RoboparkKeychain", code: Int(status))
        }
        return data
    }
    static func writeData(_ data: Data, account: String) throws {
        let attributes: [String: Any] = [kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly]
        var status = SecItemUpdate(query(account) as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound { status = SecItemAdd(query(account).merging(attributes) { _, new in new } as CFDictionary, nil) }
        guard status == errSecSuccess else { throw NSError(domain: "RoboparkKeychain", code: Int(status)) }
    }
    static func deleteData(_ account: String) throws {
        let status = SecItemDelete(query(account) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw NSError(domain: "RoboparkKeychain", code: Int(status)) }
    }
    static func reserveCorporateWriteReceipt(_ account: String) throws {
        let indexAccount = "corporate-write-receipt-index-v1"
        var index: CorporateWriteReceiptIndex
        do {
            if let bytes = try readData(indexAccount) {
                index = try JSONDecoder().decode(CorporateWriteReceiptIndex.self, from: bytes)
            } else { index = CorporateWriteReceiptIndex() }
            try index.reserve(account)
            try writeData(JSONEncoder().encode(index), account: indexAccount)
        } catch let error as CorporateWriteError { throw error }
        catch { throw CorporateWriteError.receiptStorage }
    }
    static func acknowledgeCorporateWriteReceipt(_ account: String, ownerUserID: Int,
                                                 deviceID: String, commandID: String,
                                                 stepID: String) throws {
        let indexAccount = "corporate-write-receipt-index-v1"
        do {
            if let bytes = try readData(account) {
                let receipt = try JSONDecoder().decode(CorporateWriteReceipt.self, from: bytes)
                guard receipt.permitsAcknowledgement(ownerUserID: ownerUserID, deviceID: deviceID,
                    commandID: commandID, stepID: stepID) else { return }
                // The authenticated server has committed the result. Delete the
                // data before freeing its slot; a crash is recovered by the next ACK.
                try deleteData(account)
            }
            if let bytes = try readData(indexAccount) {
                var index = try JSONDecoder().decode(CorporateWriteReceiptIndex.self, from: bytes)
                index.releaseAcknowledged(account)
                try writeData(JSONEncoder().encode(index), account: indexAccount)
            }
        } catch { throw CorporateWriteError.receiptStorage }
    }
    static func load() throws -> Registration? {
        guard !isSignedOut("registration") else { return nil }
        var request = query("registration")
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = item as? Data else {
            throw NSError(domain: "RoboparkKeychain", code: Int(status), userInfo: [NSLocalizedDescriptionKey: "Хранилище Keychain недоступно. Разблокируйте сеанс macOS."])
        }
        return try JSONDecoder().decode(Registration.self, from: data)
    }
    static func save(_ value: Registration) throws {
        let data = try JSONEncoder().encode(value)
        let attributes: [String: Any] = [kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly]
        var status = SecItemUpdate(query("registration") as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            status = SecItemAdd(query("registration").merging(attributes) { _, new in new } as CFDictionary, nil)
        }
        guard status == errSecSuccess else {
            throw NSError(domain: "RoboparkKeychain", code: Int(status), userInfo: [NSLocalizedDescriptionKey: "Не удалось сохранить регистрацию в Keychain."])
        }
        markSignedOut("registration", false)
    }
    static func remove() throws {
        // A failed Keychain deletion must not restore the old registration on
        // wake or next launch. This non-secret tombstone survives a locked jar.
        markSignedOut("registration", true)
        let status = SecItemDelete(query("registration") as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw NSError(domain: "RoboparkKeychain", code: Int(status))
        }
    }
    static func isSignedOut(_ account: String) -> Bool {
        UserDefaults.standard.bool(forKey: "robopark.signed-out.\(account).v1")
    }
    static func markSignedOut(_ account: String, _ value: Bool) {
        UserDefaults.standard.set(value, forKey: "robopark.signed-out.\(account).v1")
        UserDefaults.standard.synchronize()
    }
    private static func revocationKey(origin: String, instanceID: String) -> String {
        "robopark.pending-revocations.v1." + SHA256.hash(data: Data((origin + "\n" + instanceID).utf8)).map { String(format: "%02x", $0) }.joined()
    }
    static func pendingRevocations(origin: String, instanceID: String) -> Set<String> {
        Set((UserDefaults.standard.stringArray(forKey: revocationKey(origin: origin, instanceID: instanceID)) ?? []).filter { UUID(uuidString: $0) != nil })
    }
    static func queueRevocation(_ registration: Registration) {
        let key = revocationKey(origin: registration.origin, instanceID: registration.instanceID)
        var ids = pendingRevocations(origin: registration.origin, instanceID: registration.instanceID)
        ids.insert(registration.deviceID)
        // Only public device IDs, no private keys, cookies or server addresses.
        UserDefaults.standard.set(Array(ids).sorted(), forKey: key)
        UserDefaults.standard.synchronize()
    }
    static func forgetRevocation(_ id: String, origin: String, instanceID: String) {
        var ids = pendingRevocations(origin: origin, instanceID: instanceID)
        ids.remove(id)
        UserDefaults.standard.set(Array(ids).sorted(), forKey: revocationKey(origin: origin, instanceID: instanceID))
        UserDefaults.standard.synchronize()
    }
}
