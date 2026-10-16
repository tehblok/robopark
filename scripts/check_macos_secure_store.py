#!/usr/bin/env python3
"""Exercise production SecureStore in an isolated, disposable Keychain only."""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = """// swift-tools-version: 5.9
import PackageDescription
let package = Package(name: "SecureStoreChecks", platforms: [.macOS(.v13)], targets: [
.target(name: "AgentCore"), .executableTarget(name: "SecureStoreChecks", dependencies: ["AgentCore"])])
"""
MAIN = r'''import Foundation
import Security
import AgentCore

enum Fixture {
    static var keychain: SecKeychain?
    static var inserted: [(account: String, service: String)] = []
    static var forcedReadAccounts = Set<String>()
}
enum PrivateStoredState {
    case absent
    case value(Data)
    case deleted
}
enum SessionStoreBackend {
    static var states: [String: PrivateStoredState] = [:]
    static var failRead = false
    static var failWrite = false
    static var failMigrate = false
    static var failMigrateAccount: String?
    static var failDelete = false
    static var corruptReadback = false
    static var deleteOnMigrate = false
    static var valueOnMigrate: Data?
    static var receiptSealed = false
    static var failSeal = false
    static var sealCalls = 0
    static var reads = 0
    static var writes = 0
    static var deletes = 0
    static func reset() {
        states = [:]; failRead = false; failWrite = false; failMigrate = false
        failMigrateAccount = nil
        failDelete = false; corruptReadback = false; deleteOnMigrate = false; valueOnMigrate = nil
        receiptSealed = false; failSeal = false; sealCalls = 0
        reads = 0; writes = 0; deletes = 0
    }
    static func readState(_ account: String) throws -> PrivateStoredState {
        reads += 1
        if failRead { throw NSError(domain: "RoboparkKeychain", code: Int(errSecAuthFailed)) }
        if corruptReadback, case .value = states[account] { return .value(Data("wrong".utf8)) }
        return states[account] ?? .absent
    }
    static func write(_ data: Data, account: String) throws {
        writes += 1
        if failWrite { throw NSError(domain: "RoboparkKeychain", code: Int(errSecAuthFailed)) }
        states[account] = .value(data)
    }
    static func migrateIfAbsent(_ data: Data, account: String,
                                timeout: TimeInterval = 2) throws -> PrivateStoredState {
        if failMigrate || failMigrateAccount == account {
            throw NSError(domain: "RoboparkKeychain", code: Int(errSecAuthFailed))
        }
        if deleteOnMigrate { states[account] = .deleted; return .deleted }
        if let valueOnMigrate { states[account] = .value(valueOnMigrate); return .value(valueOnMigrate) }
        switch states[account] {
        case nil, .some(.absent):
            states[account] = .value(data)
        default:
            break
        }
        return states[account] ?? .absent
    }
    static func migrateDeletionIfAbsent(_ account: String) throws -> PrivateStoredState {
        if failMigrate || failMigrateAccount == account {
            throw NSError(domain: "RoboparkKeychain", code: Int(errSecAuthFailed))
        }
        if let current = states[account] { return current }
        states[account] = .deleted
        return .deleted
    }
    static func delete(_ account: String) throws {
        deletes += 1
        if failDelete { throw NSError(domain: "RoboparkKeychain", code: Int(errSecAuthFailed)) }
        if isReceiptRow(account) { states.removeValue(forKey: account) }
        else { states[account] = .deleted }
    }
    static func receiptMigrationSealed() throws -> Bool { receiptSealed }
    static func sealReceiptMigration() throws {
        sealCalls += 1
        if failSeal { throw NSError(domain: "RoboparkKeychain", code: Int(errSecAuthFailed)) }
        receiptSealed = true
    }
    static func isReceiptRow(_ account: String) -> Bool {
        account.range(of: "^corporate-write-receipt-v1-[a-f0-9]{64}$",
                      options: .regularExpression) != nil
    }
}
// The old broker is a read-only migration source.  The harness never starts an
// XPC service and proves ordinary paths do not call it when no legacy metadata
// exists in the disposable Keychain.
enum KeychainBrokerStoredState { case absent; case value(Data); case deleted }
enum KeychainBrokerClient {
    static func readState(_ account: String) throws -> KeychainBrokerStoredState { .absent }
    static func receiptMigrationSealed() throws -> Bool { false }
}

@main struct Checks {
static func legacyQuery(_ account: String, service: String = "ru.robopark.agent") -> [String: Any] {
    [kSecClass as String: kSecClassGenericPassword,
     kSecAttrService as String: service, kSecAttrAccount as String: account,
     kSecUseKeychain as String: Fixture.keychain!,
     kSecMatchSearchList as String: [Fixture.keychain!]]
}
static func insertLegacy(_ account: String, _ data: Data, service: String = "ru.robopark.agent") {
    var request = legacyQuery(account, service: service)
    request[kSecValueData as String] = data
    var status = SecItemAdd(request as CFDictionary, nil)
    if status == errSecDuplicateItem {
        var updateQuery = legacyQuery(account, service: service)
        updateQuery.removeValue(forKey: kSecMatchSearchList as String)
        status = SecItemUpdate(updateQuery as CFDictionary,
            [kSecValueData as String: data] as CFDictionary)
    }
    guard status == errSecSuccess else { fatalError("fixture insert failed: \(account), \(status)") }
    if !Fixture.inserted.contains(where: { $0.account == account && $0.service == service }) {
        Fixture.inserted.append((account, service))
    }
}
static func resetFixture() {
    for item in Fixture.inserted {
        let status = SecItemDelete(legacyQuery(item.account, service: item.service) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            fatalError("fixture reset failed: \(item.account), \(status)")
        }
    }
    Fixture.inserted = []
    Fixture.forcedReadAccounts = []
    SessionStoreBackend.reset()
    SecureStore.testResetDeferredLegacyAccounts()
}
static func sourceValue(_ account: String, service: String = "ru.robopark.agent") -> Data? {
    var request = legacyQuery(account, service: service)
    request[kSecReturnData as String] = true
    var item: CFTypeRef?
    guard SecItemCopyMatching(request as CFDictionary, &item) == errSecSuccess else { return nil }
    return item as? Data
}
static func localValue(_ account: String) -> Data? {
    guard case let .value(data) = SessionStoreBackend.states[account] else { return nil }
    return data
}
static func requireFailure(_ message: String, _ body: () throws -> Void) {
    do { try body(); fatalError(message) } catch { }
}
static func receipt(_ character: Character) -> String {
    "corporate-write-receipt-v1-" + String(repeating: String(character), count: 64)
}
static func run() throws {
    let path = CommandLine.arguments[1]
    setbuf(stdout, nil)
    // Empty password belongs only to this disposable Keychain, never login.keychain.
    guard SecKeychainOpen(path, &Fixture.keychain) == errSecSuccess,
          Fixture.keychain != nil else { fatalError("fixture creation failed") }
    resetFixture()
    print("stage: clean-and-copy")
    guard try SecureStore.readData("workspace-profile-v1") == nil,
          SessionStoreBackend.writes == 0 else { fatalError("clean install did not stay empty") }

    let oldValue = Data("fixture-only-value".utf8)
    insertLegacy("registration", oldValue)
    let enrollmentAccount = "enrollment-key-v1-" + String(repeating: "b", count: 64)
    let claimAccount = "account-claim-key-v1-" + String(repeating: "c", count: 64)
    for (account, value) in [
        ("workspace-profile-v1", "profile"),
        ("workspace-browser-session-v1", "browser-capability"),
        ("workspace-storage-key-v1", "offline-key"),
        (enrollmentAccount, "enrollment-key"),
        (claimAccount, "claim-key"),
    ] {
        let bytes = Data(value.utf8)
        insertLegacy(account, bytes)
        guard try SecureStore.readData(account) == bytes,
              localValue(account) == bytes else { fatalError("required account not migrated: \(account)") }
    }
    let first = try SecureStore.readData("registration")
    guard first == oldValue,
          sourceValue("registration") == oldValue,
          localValue("registration") == oldValue else {
        fatalError("copy-before-use migration lost data")
    }

    resetFixture()
    print("stage: stable-source")
    let stableAccount = "registration"
    let stableValue = Data("stable-v1-source".utf8)
    insertLegacy(stableAccount, stableValue, service: "ru.robopark.agent.stable-v1")
    guard try SecureStore.readData(stableAccount) == stableValue,
          sourceValue(stableAccount, service: "ru.robopark.agent.stable-v1") == stableValue else {
        fatalError("stable-v1 source was not migrated")
    }

    resetFixture()
    print("stage: deferred-session-failures")
    let partialAccount = "registration"
    insertLegacy(partialAccount, Data("partial-source".utf8))
    SessionStoreBackend.failMigrate = true
    guard try SecureStore.readData(partialAccount) == nil,
          SessionStoreBackend.states[partialAccount] == nil,
          SecureStore.hasDeferredLegacyData else { fatalError("denied login migration did not defer") }
    SessionStoreBackend.failMigrate = false
    guard sourceValue(partialAccount) == Data("partial-source".utf8) else { fatalError("partial migration removed source") }

    resetFixture()
    print("stage: mismatch")
    let mismatchAccount = "registration"
    insertLegacy(mismatchAccount, Data("mismatch-source".utf8))
    SessionStoreBackend.corruptReadback = true
    guard try SecureStore.readData(mismatchAccount) == nil,
          SecureStore.hasDeferredLegacyData else { fatalError("unverified login migration did not defer") }
    SessionStoreBackend.corruptReadback = false

    resetFixture()
    print("stage: migration-races")
    let racedDeleteAccount = "registration"
    insertLegacy(racedDeleteAccount, Data("deleted-source".utf8))
    SessionStoreBackend.deleteOnMigrate = true
    guard try SecureStore.readData(racedDeleteAccount) == nil,
          sourceValue(racedDeleteAccount) == Data("deleted-source".utf8) else {
        fatalError("concurrent tombstone resurrected legacy data")
    }
    SessionStoreBackend.deleteOnMigrate = false

    resetFixture()
    print("stage: transport")
    let racedWriteAccount = "registration"
    insertLegacy(racedWriteAccount, Data("old-source".utf8))
    SessionStoreBackend.valueOnMigrate = Data("new-authoritative".utf8)
    guard try SecureStore.readData(racedWriteAccount) == Data("new-authoritative".utf8) else {
        fatalError("concurrent broker value was overwritten by migration")
    }
    SessionStoreBackend.valueOnMigrate = nil

    resetFixture()
    print("stage: delete")
    let transportAccount = "registration"
    insertLegacy(transportAccount, Data("must-not-fallback".utf8))
    let writesBeforeFailure = SessionStoreBackend.writes
    SessionStoreBackend.failRead = true
    requireFailure("private-store outage fell back to legacy storage") { _ = try SecureStore.readData(transportAccount) }
    SessionStoreBackend.failRead = false
    guard SessionStoreBackend.writes == writesBeforeFailure else { fatalError("private store outage attempted migration") }

    resetFixture()
    print("stage: receipts")
    insertLegacy("registration", oldValue)
    guard try SecureStore.readData("registration") == oldValue else { fatalError("delete setup migration failed") }
    try SecureStore.deleteData("registration")
    guard try SecureStore.readData("registration") == nil,
          sourceValue("registration") == oldValue,
          case .deleted = SessionStoreBackend.states["registration"] else {
        fatalError("deleted legacy item resurrected")
    }
    try SecureStore.writeData(Data("recreated".utf8), account: "registration")
    guard try SecureStore.readData("registration") == Data("recreated".utf8) else {
        fatalError("write did not replace tombstone")
    }
    SessionStoreBackend.failDelete = true
    requireFailure("failed delete was reported as successful") { try SecureStore.deleteData("registration") }
    SessionStoreBackend.failDelete = false
    guard try SecureStore.readData("registration") == Data("recreated".utf8) else {
        fatalError("failed delete changed stored value")
    }

    // Receipt migration is sealed only after the complete legacy index and all
    // referenced rows have been copied and verified.
    resetFixture()
    print("stage: corrupt-index")
    let receiptA = receipt("a")
    let receiptB = receipt("b")
    insertLegacy(receiptA, Data("receipt-a".utf8))
    insertLegacy(receiptB, Data("receipt-b".utf8))
    insertLegacy("corporate-write-receipt-index-v1",
        try JSONEncoder().encode(CorporateWriteReceiptIndex(accounts: [receiptA, receiptB])))
    SessionStoreBackend.failMigrateAccount = receiptB
    requireFailure("partial receipt migration sealed early") {
        try SecureStore.withUserApproval { try SecureStore.migrateCorporateWriteReceipts() }
    }
    guard !SessionStoreBackend.receiptSealed,
          SessionStoreBackend.sealCalls == 0,
          localValue(receiptA) == Data("receipt-a".utf8),
          localValue(receiptB) == nil else { fatalError("partial receipt migration was not retryable") }
    SessionStoreBackend.failMigrateAccount = nil
    try SecureStore.withUserApproval { try SecureStore.migrateCorporateWriteReceipts() }
    guard SessionStoreBackend.receiptSealed,
          SessionStoreBackend.sealCalls == 1,
          localValue(receiptB) == Data("receipt-b".utf8) else { fatalError("receipt migration retry failed") }
    try SecureStore.migrateCorporateWriteReceipts()
    guard SessionStoreBackend.sealCalls == 1 else { fatalError("receipt migration seal was repeated") }

    // Once sealed, old rows cannot return and acknowledgements physically
    // remove rows instead of accumulating one tombstone per command forever.
    try SecureStore.deleteData(receiptA)
    guard SessionStoreBackend.states[receiptA] == nil,
          try SecureStore.readData(receiptA) == nil,
          sourceValue(receiptA) == Data("receipt-a".utf8) else {
        fatalError("sealed receipt deletion resurrected a legacy row")
    }
    let receiptC = receipt("c")
    try SecureStore.writeData(Data("new-receipt".utf8), account: receiptC)
    try SecureStore.deleteData(receiptC)
    guard SessionStoreBackend.states[receiptC] == nil,
          try SecureStore.readData(receiptC) == nil else {
        fatalError("post-seal receipt acknowledgement retained a tombstone")
    }
    try SecureStore.deleteData("corporate-write-receipt-index-v1")
    guard try SecureStore.readData("corporate-write-receipt-index-v1") == nil,
          case .deleted = SessionStoreBackend.states["corporate-write-receipt-index-v1"] else {
        fatalError("sealed index tombstone fell back to legacy")
    }

    resetFixture()
    print("stage: invalid-index")
    insertLegacy("corporate-write-receipt-index-v1", Data("corrupt".utf8))
    requireFailure("corrupt receipt index was sealed") { try SecureStore.migrateCorporateWriteReceipts() }
    guard !SessionStoreBackend.receiptSealed,
          SessionStoreBackend.sealCalls == 0 else { fatalError("corrupt receipt index changed seal") }

    resetFixture()
    print("stage: empty-seal")
    insertLegacy("corporate-write-receipt-index-v1",
        try JSONEncoder().encode(CorporateWriteReceiptIndex(accounts: ["invalid-row"])))
    requireFailure("invalid receipt account was sealed") { try SecureStore.migrateCorporateWriteReceipts() }
    guard !SessionStoreBackend.receiptSealed,
          SessionStoreBackend.sealCalls == 0 else { fatalError("invalid receipt index changed seal") }

    resetFixture()
    print("stage: denied-login")
    try SecureStore.migrateCorporateWriteReceipts()
    guard SessionStoreBackend.receiptSealed,
          SessionStoreBackend.sealCalls == 1 else { fatalError("empty receipt migration did not seal once") }
    let legacyAfterSeal = receipt("e")
    insertLegacy(legacyAfterSeal, Data("must-stay-ignored".utf8))
    guard try SecureStore.readData(legacyAfterSeal) == nil,
          sourceValue(legacyAfterSeal) != nil else { fatalError("sealed missing row fell back to legacy") }

    // A denied legacy authentication session is deliberately renewable: it
    // must not block a new login or overwrite the unavailable source record.
    resetFixture()
    print("stage: denied-offline-key")
    let deniedSession = "registration"
    insertLegacy(deniedSession, Data("old-login-source".utf8))
    Fixture.forcedReadAccounts.insert(deniedSession)
    guard try SecureStore.readData(deniedSession) == nil,
          SessionStoreBackend.states[deniedSession] == nil,
          SecureStore.hasDeferredLegacyData,
          sourceValue(deniedSession) == Data("old-login-source".utf8) else {
        fatalError("denied login source did not defer cleanly")
    }
    try SecureStore.writeData(Data("fresh-login".utf8), account: deniedSession)
    guard !SecureStore.hasDeferredLegacyData,
          try SecureStore.readData(deniedSession) == Data("fresh-login".utf8),
          sourceValue(deniedSession) == Data("old-login-source".utf8) else {
        fatalError("fresh login left stale deferred state or overwrote denied source")
    }

    // Encryption keys and pending write receipts are never regenerated or
    // overwritten when their legacy source cannot be read losslessly.
    resetFixture()
    print("stage: denied-receipt")
    let offlineKey = "workspace-storage-key-v1"
    insertLegacy(offlineKey, Data(repeating: 7, count: 32))
    Fixture.forcedReadAccounts.insert(offlineKey)
    requireFailure("denied offline key was treated as renewable") { _ = try SecureStore.readData(offlineKey) }
    guard SessionStoreBackend.states[offlineKey] == nil,
          sourceValue(offlineKey) == Data(repeating: 7, count: 32) else {
        fatalError("denied offline key was generated or overwritten")
    }

    resetFixture()
    let deniedReceipt = receipt("d")
    insertLegacy(deniedReceipt, Data("pending-receipt".utf8))
    insertLegacy("corporate-write-receipt-index-v1",
        try JSONEncoder().encode(CorporateWriteReceiptIndex(accounts: [deniedReceipt])))
    Fixture.forcedReadAccounts.insert(deniedReceipt)
    requireFailure("denied receipt was treated as renewable") { _ = try SecureStore.readData(deniedReceipt) }
    guard SessionStoreBackend.states[deniedReceipt] == nil,
          !SessionStoreBackend.receiptSealed,
          sourceValue(deniedReceipt) == Data("pending-receipt".utf8) else {
        fatalError("denied receipt was generated, overwritten or sealed")
    }

    print("PASS: SecureStore private migration, receipt seal, deferred login and nonrenewable data checks (isolated Keychain)")
}
static func main() {
    do { try run() }
    catch { fatalError("unhandled SecureStore fixture error: \(error)") }
}
}
'''

def main():
    with tempfile.TemporaryDirectory(prefix="robopark-secure-store-") as tmp:
        root = Path(tmp)
        shutil.copytree(ROOT / "apps/macos/Sources/AgentCore", root / "Sources/AgentCore")
        target = root / "Sources/SecureStoreChecks"
        target.mkdir()
        source = (ROOT / "apps/macos/Sources/RoboparkAgent/SecureStore.swift").read_text()
        needle = "[kSecClass as String: kSecClassGenericPassword,"
        assert source.count(needle) == 1
        # Replace only storage destination; all migration/interaction code is production.
        source = source.replace(needle, "[kSecUseKeychain as String: Fixture.keychain!, kSecMatchSearchList as String: [Fixture.keychain!], kSecClass as String: kSecClassGenericPassword,")
        read_raw = '''    private static func readRaw(_ request: [String: Any]) throws -> Data? {
        var request = request
'''
        test_read_raw = '''    private static func readRaw(_ request: [String: Any]) throws -> Data? {
        var request = request
        if let account = request[kSecAttrAccount as String] as? String,
           Fixture.forcedReadAccounts.contains(account) {
            throw NSError(domain: "RoboparkKeychain", code: Int(errSecAuthFailed))
        }
'''
        assert read_raw in source
        # This narrow harness hook models a denied *legacy* SecItem read.  It
        # is not present in production and leaves all other source untouched.
        source = source.replace(read_raw, test_read_raw)
        deferred = '''    private static var deferredLegacyAccounts = Set<String>()
    static var hasDeferredLegacyData: Bool { synchronized { !deferredLegacyAccounts.isEmpty } }
'''
        test_deferred = '''    private static var deferredLegacyAccounts = Set<String>()
    static var hasDeferredLegacyData: Bool { synchronized { !deferredLegacyAccounts.isEmpty } }
    // Harness-only isolation: production deliberately retains deferred state.
    static func testResetDeferredLegacyAccounts() { synchronized { deferredLegacyAccounts = [] } }
'''
        assert deferred in source
        source = source.replace(deferred, test_deferred)
        (target / "SecureStore.swift").write_text(source)
        (target / "Checks.swift").write_text(MAIN)
        (root / "Package.swift").write_text(PACKAGE)
        cache = Path("/private/tmp/robopark-secure-store-checks")
        options = ["--package-path", str(root), "--scratch-path", str(cache), "--cache-path", str(cache / "cache"), "--config-path", str(cache / "config"), "--security-path", str(cache / "security"), "--disable-sandbox"]
        env = {**os.environ, "CLANG_MODULE_CACHE_PATH": str(cache / "clang"), "SWIFTPM_MODULECACHE_OVERRIDE": str(cache / "modules")}
        subprocess.run(["swift", "build", *options], env=env, check=True)
        binary = subprocess.check_output(["swift", "build", *options, "--show-bin-path"], env=env, text=True).strip()
        keychain = root / "fixture.keychain-db"
        subprocess.run(["security", "create-keychain", "-p", "", str(keychain)], check=True, capture_output=True)
        try:
            subprocess.run([str(Path(binary) / "SecureStoreChecks"), str(keychain)], check=True, timeout=15)
        finally:
            subprocess.run(["security", "delete-keychain", str(keychain)], check=True, capture_output=True)
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
