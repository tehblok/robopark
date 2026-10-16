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
    private static var deferredLegacyAccounts = Set<String>()
    static var hasDeferredLegacyData: Bool { synchronized { !deferredLegacyAccounts.isEmpty } }
    static func failureMessage(_ error: Error) -> String {
        if (error as NSError).domain == "RoboparkKeychain" {
            return "Не удалось перенести данные предыдущей версии. Они сохранены; повторный вход восстановит онлайн-работу. Старые офлайн-данные требуют отдельного переноса."
        }
        if error is PrivateSessionStoreError {
            return "Не удалось прочитать или сохранить локальные данные Robopark. Проверьте свободное место и права папки приложения."
        }
        return (error as? LocalizedError)?.errorDescription ?? "Операция не завершилась. Повторите после проверки связи с сервером."
    }

    private static func legacyMetadataExists(_ account: String, service: String) throws -> Bool {
        var request = query(account)
        request[kSecAttrService as String] = service
        request[kSecReturnAttributes as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = perform { SecItemCopyMatching(request as CFDictionary, &item) }
        if status == errSecItemNotFound { return false }
        guard status == errSecSuccess else { throw NSError(domain: "RoboparkKeychain", code: Int(status)) }
        return true
    }
    private static func legacyState(_ account: String) throws -> PrivateStoredState {
        // The old broker is only a read-only migration source. A fresh install
        // never needs its service; ordinary operation uses the private files.
        let brokerService = "ru.robopark.keychain-broker-v1"
        if try legacyMetadataExists(account, service: brokerService) {
            switch try KeychainBrokerClient.readState(account) {
            case .value(let bytes): return .value(bytes)
            case .deleted: return .deleted
            case .absent: break
            }
        }
        if isReceiptRow(account),
           try legacyMetadataExists("internal-receipt-migration-sealed-v1", service: brokerService),
           try KeychainBrokerClient.receiptMigrationSealed() { return .absent }
        if let bytes = try readRaw(query(account)) { return .value(bytes) }
        var old = query(account); old[kSecAttrService as String] = legacyService
        if let bytes = try readRaw(old) { return .value(bytes) }
        return .absent
    }
    private static func migrateLegacyValue(_ account: String) throws -> Data? {
        switch try legacyState(account) {
        case .value(let bytes): return try persistMigratedValue(bytes, account: account)
        case .deleted:
            switch try SessionStoreBackend.migrateDeletionIfAbsent(account) {
            case .value(let value): return value
            case .deleted, .absent: return nil
            }
        case .absent:
            switch try SessionStoreBackend.readState(account) {
            case .value(let value): return value
            case .absent, .deleted: return nil
            }
        }
    }
    private static func canRenewSession(_ account: String) -> Bool {
        // A new login can replace an expired/inaccessible session. Encryption
        // keys and uncertain write receipts cannot be replaced without data loss.
        ["registration", "workspace-profile-v1", "workspace-browser-session-v1"].contains(account)
            || account.hasPrefix("enrollment-key-v1-") || account.hasPrefix("account-claim-key-v1-")
    }
    private static func persistMigratedValue(_ value: Data, account: String) throws -> Data? {
        // The source item deliberately remains in place. A verified local
        // copy is authoritative; a later delete writes a local tombstone so
        // that an inaccessible legacy item can never be resurrected.
        let committed = try SessionStoreBackend.migrateIfAbsent(value, account: account)
        switch committed {
        case .deleted:
            return nil
        case .absent:
            throw migrationError()
        case let .value(candidate):
            switch try SessionStoreBackend.readState(account) {
            case .deleted: return nil
            case let .value(stored) where stored == candidate: return stored
            case .absent, .value: throw migrationError()
            }
        }
    }
    private static func migrateReceiptState(_ account: String) throws -> PrivateStoredState {
        let current = try SessionStoreBackend.readState(account)
        guard case .absent = current else { return current }
        let source: Data
        switch try legacyState(account) {
        case .value(let bytes): source = bytes
        case .deleted: return try SessionStoreBackend.migrateDeletionIfAbsent(account)
        case .absent: return try SessionStoreBackend.readState(account)
        }
        let committed = try SessionStoreBackend.migrateIfAbsent(source, account: account)
        switch committed {
        case .absent:
            // Another process may have sealed row migration after our first
            // read. Once sealed, an absent row intentionally ignores legacy.
            guard isReceiptRow(account), try SessionStoreBackend.receiptMigrationSealed() else {
                throw migrationError()
            }
            return .absent
        case .deleted:
            return .deleted
        case let .value(candidate):
            guard case let .value(stored) = try SessionStoreBackend.readState(account),
                  stored == candidate else { throw migrationError() }
            return .value(stored)
        }
    }
    private static func ensureReceiptMigration() throws {
        if try SessionStoreBackend.receiptMigrationSealed() { return }
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
        try SessionStoreBackend.sealReceiptMigration()
        guard try SessionStoreBackend.receiptMigrationSealed() else { throw migrationError() }
    }
    static func readData(_ account: String) throws -> Data? {
        try synchronized {
            let receiptStorage = requiresReceiptMigration(account)
            if receiptStorage { try ensureReceiptMigration() }
            switch try SessionStoreBackend.readState(account) {
            case let .value(value):
                deferredLegacyAccounts.remove(account)
                return value
            case .deleted:
                deferredLegacyAccounts.remove(account)
                return nil
            case .absent:
                if receiptStorage { return nil }
                // Background reads never show system dialogs. Existing offline
                // keys/receipts remain protected until a lossless import succeeds.
                do {
                    if deferredLegacyAccounts.contains(account), !interactive {
                        throw migrationError()
                    }
                    let value = try migrateLegacyValue(account)
                    deferredLegacyAccounts.remove(account)
                    return value
                } catch {
                    guard (error as NSError).domain == "RoboparkKeychain" else { throw error }
                    deferredLegacyAccounts.insert(account)
                    if canRenewSession(account), !interactive { return nil }
                    throw error
                }
            }
        }
    }
    static func writeData(_ data: Data, account: String) throws {
        try synchronized {
            if requiresReceiptMigration(account) { try ensureReceiptMigration() }
            try SessionStoreBackend.write(data, account: account)
            deferredLegacyAccounts.remove(account)
        }
    }
    static func deleteData(_ account: String) throws {
        // Fixed accounts use durable tombstones because old ACLs may prevent
        // source cleanup. Receipt rows are physically removed after their
        // one-time migration seal, so acknowledgements remain bounded.
        try synchronized {
            if requiresReceiptMigration(account) { try ensureReceiptMigration() }
            try SessionStoreBackend.delete(account)
            deferredLegacyAccounts.remove(account)
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
