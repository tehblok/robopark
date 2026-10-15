import Foundation
import Security
import AgentCore
import CryptoKit

enum SecureStore {
    private static let service = "ru.robopark.agent.stable-v1"
    private static let legacyService = "ru.robopark.agent"
    private static let receiptIndexAccount = "corporate-write-receipt-index-v1"
    private static let interactionLock = NSRecursiveLock()
    private static var interactive = false

    private static func synchronized<T>(_ body: () throws -> T) rethrows -> T {
        interactionLock.lock(); defer { interactionLock.unlock() }
        return try body()
    }

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
            let account = request[kSecAttrAccount as String] as? String ?? ""
            let known = ["registration", "workspace-profile-v1", "workspace-browser-session-v1", "workspace-storage-key-v1"]
            NSLog("Robopark Keychain read failed (area=%@, namespace=%@, code=%d)",
                known.contains(account) ? account : "other",
                request[kSecAttrService as String] as? String == service ? "stable" : "legacy", status)
            throw NSError(domain: "RoboparkKeychain", code: Int(status))
        }
        return data
    }
    private static func migrationError() -> NSError {
        NSError(domain: "RoboparkKeychain", code: Int(errSecAuthFailed))
    }
    private static func isReceiptRow(_ account: String) -> Bool {
        account.range(of: "^corporate-write-receipt-v1-[a-f0-9]{64}$",
                      options: .regularExpression) != nil
    }
    private static func requiresReceiptMigration(_ account: String) -> Bool {
        account == receiptIndexAccount || isReceiptRow(account)
    }
    private static func migrateLegacyValue(_ account: String) throws -> Data? {
        if let value = try readRaw(query(account)) {
            return try persistMigratedValue(value, account: account)
        }
        var legacy = query(account); legacy[kSecAttrService as String] = legacyService
        guard let value = try readRaw(legacy) else {
            // A concurrent broker write/delete after the initial absent read is
            // authoritative even though this process found no legacy source.
            switch try KeychainBrokerClient.readState(account) {
            case let .value(current): return current
            case .absent, .deleted: return nil
            }
        }
        return try persistMigratedValue(value, account: account)
    }
    private static func persistMigratedValue(_ value: Data, account: String) throws -> Data? {
        // The source item deliberately remains in place. A verified broker
        // copy is authoritative; a later delete writes a broker tombstone so
        // that an inaccessible legacy item can never be resurrected.
        let committed = try KeychainBrokerClient.migrateIfAbsent(value, account: account)
        switch committed {
        case .deleted:
            return nil
        case .absent:
            throw migrationError()
        case let .value(candidate):
            switch try KeychainBrokerClient.readState(account) {
            case .deleted: return nil
            case let .value(stored) where stored == candidate: return stored
            case .absent, .value: throw migrationError()
            }
        }
    }
    private static func migrateReceiptState(_ account: String) throws -> KeychainBrokerStoredState {
        let current = try KeychainBrokerClient.readState(account)
        guard case .absent = current else { return current }
        let source: Data?
        if let stable = try readRaw(query(account)) {
            source = stable
        } else {
            var legacy = query(account); legacy[kSecAttrService as String] = legacyService
            source = try readRaw(legacy)
        }
        guard let source else { return try KeychainBrokerClient.readState(account) }
        let committed = try KeychainBrokerClient.migrateIfAbsent(source, account: account)
        switch committed {
        case .absent:
            // Another process may have sealed row migration after our first
            // read. Once sealed, an absent row intentionally ignores legacy.
            guard isReceiptRow(account), try KeychainBrokerClient.receiptMigrationSealed() else {
                throw migrationError()
            }
            return .absent
        case .deleted:
            return .deleted
        case let .value(candidate):
            guard case let .value(stored) = try KeychainBrokerClient.readState(account),
                  stored == candidate else { throw migrationError() }
            return .value(stored)
        }
    }
    private static func ensureReceiptMigration() throws {
        if try KeychainBrokerClient.receiptMigrationSealed() { return }
        let indexState = try migrateReceiptState(receiptIndexAccount)
        switch indexState {
        case .absent, .deleted:
            break
        case let .value(bytes):
            let index = try JSONDecoder().decode(CorporateWriteReceiptIndex.self, from: bytes)
            guard index.accounts.count <= CorporateWriteReceiptIndex.capacity,
                  index.accounts.allSatisfy({ isReceiptRow($0) }) else {
                throw CorporateWriteError.receiptStorage
            }
            // Missing rows are legitimate if the previous process stopped
            // between reserving an index slot and persisting its receipt.
            for account in Set(index.accounts).sorted() { _ = try migrateReceiptState(account) }
        }
        try KeychainBrokerClient.sealReceiptMigration()
        guard try KeychainBrokerClient.receiptMigrationSealed() else { throw migrationError() }
    }
    static func readData(_ account: String) throws -> Data? {
        try synchronized {
            let receiptStorage = requiresReceiptMigration(account)
            if receiptStorage { try ensureReceiptMigration() }
            switch try KeychainBrokerClient.readState(account) {
            case let .value(value): return value
            case .deleted: return nil
            case .absent:
                if receiptStorage { return nil }
                // Direct Keychain access exists only for lazy copy-before-use
                // migration. At startup interaction is disabled, so a clean install
                // returns nil while an inaccessible old record fails closed. The
                // explicit foreground recovery action enables the one-time prompt.
                return try migrateLegacyValue(account)
            }
        }
    }
    static func writeData(_ data: Data, account: String) throws {
        try synchronized {
            if requiresReceiptMigration(account) { try ensureReceiptMigration() }
            try KeychainBrokerClient.write(data, account: account)
        }
    }
    static func deleteData(_ account: String) throws {
        // Fixed accounts use durable tombstones because old ACLs may prevent
        // source cleanup. Receipt rows are physically removed after their
        // one-time migration seal, so acknowledgements remain bounded.
        try synchronized {
            if requiresReceiptMigration(account) { try ensureReceiptMigration() }
            try KeychainBrokerClient.delete(account)
        }
    }
    static func migrateCorporateWriteReceipts() throws {
        try synchronized { try ensureReceiptMigration() }
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
