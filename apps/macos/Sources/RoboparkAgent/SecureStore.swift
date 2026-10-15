import Foundation
import Security
import AgentCore
import CryptoKit

enum SecureStore {
    private static let service = "ru.robopark.agent.stable-v1"
    private static let legacyService = "ru.robopark.agent"
    private static let interactionLock = NSRecursiveLock()
    private static var interactive = false

    // Legacy file-based Keychain operations otherwise display dialogs even at
    // background launch, before the accessory application has a usable window.
    // Only the explicit foreground recovery action opts into system UI.
    static func withUserApproval<T>(_ body: () throws -> T) rethrows -> T {
        interactionLock.lock(); defer { interactionLock.unlock() }
        let previous = interactive; interactive = true
        defer { interactive = previous }
        return try body()
    }
    private static func perform(_ operation: () -> OSStatus) -> OSStatus {
        interactionLock.lock(); defer { interactionLock.unlock() }
        var previous = DarwinBoolean(false)
        let readStatus = SecKeychainGetUserInteractionAllowed(&previous)
        guard readStatus == errSecSuccess else { return readStatus }
        let setStatus = SecKeychainSetUserInteractionAllowed(interactive)
        guard setStatus == errSecSuccess else { return setStatus }
        defer { SecKeychainSetUserInteractionAllowed(previous.boolValue) }
        return operation()
    }
    private static func query(_ account: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service, kSecAttrAccount as String: account]
    }
    private static func readRaw(_ request: [String: Any]) throws -> Data? {
        var request = request
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = perform { SecItemCopyMatching(request as CFDictionary, &item) }
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = item as? Data else {
            throw NSError(domain: "RoboparkKeychain", code: Int(status))
        }
        return data
    }
    static func readData(_ account: String) throws -> Data? {
        if let value = try readRaw(query(account)) { return value }
        var legacy = query(account); legacy[kSecAttrService as String] = legacyService
        guard let value = try readRaw(legacy) else { return nil }
        // Copy-before-use preserves old drafts and credentials if migration is
        // interrupted. New items inherit the stable RC9 designated requirement.
        // A locked legacy item throws; it is never treated as an empty account.
        try writeData(value, account: account)
        return value
    }
    static func writeData(_ data: Data, account: String) throws {
        let attributes: [String: Any] = [kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly]
        var status = perform { SecItemUpdate(query(account) as CFDictionary, attributes as CFDictionary) }
        if status == errSecItemNotFound { status = perform { SecItemAdd(query(account).merging(attributes) { _, new in new } as CFDictionary, nil) } }
        guard status == errSecSuccess else { throw NSError(domain: "RoboparkKeychain", code: Int(status)) }
    }
    static func deleteData(_ account: String) throws {
        // Remove the old namespace first so a crash cannot fall back to it.
        var legacy = query(account); legacy[kSecAttrService as String] = legacyService
        for request in [legacy, query(account)] {
            let status = perform { SecItemDelete(request as CFDictionary) }
            guard status == errSecSuccess || status == errSecItemNotFound else {
                throw NSError(domain: "RoboparkKeychain", code: Int(status))
            }
        }
    }
    static func migrateCorporateWriteReceipts() throws {
        guard let bytes = try readData("corporate-write-receipt-index-v1") else { return }
        let index = try JSONDecoder().decode(CorporateWriteReceiptIndex.self, from: bytes)
        guard index.accounts.count <= CorporateWriteReceiptIndex.capacity,
              index.accounts.allSatisfy({ $0.range(of: "^corporate-write-receipt-v1-[a-f0-9]{64}$", options: .regularExpression) != nil }) else {
            throw CorporateWriteError.receiptStorage
        }
        // Prepared/executing/unknown receipts are copied unchanged. Missing
        // rows are legitimate when a crash occurred between reserve and write.
        for account in Set(index.accounts) { _ = try readData(account) }
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
        guard !isSignedOut("registration"), let data = try readData("registration") else { return nil }
        return try JSONDecoder().decode(Registration.self, from: data)
    }
    static func save(_ value: Registration) throws {
        try writeData(JSONEncoder().encode(value), account: "registration")
        markSignedOut("registration", false)
    }
    static func remove() throws {
        // Deletion failure never resurrects a signed-out owner on next launch.
        markSignedOut("registration", true)
        try deleteData("registration")
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
