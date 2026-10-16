import Foundation
import AgentCore

/// App-owned persistence. Code signing and the login Keychain are not involved
/// in ordinary reads, updates or restarts.
enum SessionStoreBackend {
    private static let sealAccount = "internal-receipt-migration-sealed-v1"
    private static func store() throws -> PrivateSessionStore {
        let parent = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("RoboparkAgent", isDirectory: true)
        return try PrivateSessionStore(directory: parent.appendingPathComponent("session-store-v1", isDirectory: true))
    }
    static func readState(_ account: String) throws -> PrivateStoredState {
        try store().readState(account)
    }
    static func write(_ value: Data, account: String) throws {
        try store().write(value, account: account)
    }
    static func migrateIfAbsent(_ value: Data, account: String) throws -> PrivateStoredState {
        try store().migrateIfAbsent(value, account: account)
    }
    static func migrateDeletionIfAbsent(_ account: String) throws -> PrivateStoredState {
        try store().migrateDeletionIfAbsent(account)
    }
    static func delete(_ account: String) throws {
        let receipt = account.hasPrefix("corporate-write-receipt-v1-")
        if receipt, try !receiptMigrationSealed() { throw CorporateWriteError.receiptStorage }
        try store().delete(account, tombstone: !receipt)
    }
    static func receiptMigrationSealed() throws -> Bool {
        switch try store().readState(sealAccount) {
        case .absent: return false
        case .value(let data) where data == Data("sealed-v1".utf8): return true
        default: throw CorporateWriteError.receiptStorage
        }
    }
    static func sealReceiptMigration() throws {
        try store().write(Data("sealed-v1".utf8), account: sealAccount)
    }
}
