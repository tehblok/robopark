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
}
enum KeychainBrokerStoredState {
    case absent
    case value(Data)
    case deleted
}
enum KeychainBrokerClient {
    static var states: [String: KeychainBrokerStoredState] = [:]
    static var failRead = false
    static var lockedRead = false
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
        states = [:]; failRead = false; lockedRead = false; failWrite = false; failMigrate = false
        failMigrateAccount = nil
        failDelete = false; corruptReadback = false; deleteOnMigrate = false; valueOnMigrate = nil
        receiptSealed = false; failSeal = false; sealCalls = 0
        reads = 0; writes = 0; deletes = 0
    }
    static func readState(_ account: String, timeout: TimeInterval = 2) throws -> KeychainBrokerStoredState {
        reads += 1
        if failRead { throw NSError(domain: "RoboparkKeychain", code: Int(errSecAuthFailed)) }
        if lockedRead { throw NSError(domain: "RoboparkKeychain", code: Int(errSecInteractionNotAllowed)) }
        if corruptReadback, case .value = states[account] { return .value(Data("wrong".utf8)) }
        return states[account] ?? .absent
    }
    static func write(_ data: Data, account: String, timeout: TimeInterval = 2) throws {
        writes += 1
        if failWrite { throw NSError(domain: "RoboparkKeychain", code: Int(errSecAuthFailed)) }
        if isReceiptRow(account), !receiptSealed {
            throw NSError(domain: "RoboparkKeychain", code: Int(errSecAuthFailed))
        }
        states[account] = .value(data)
    }
    static func migrateIfAbsent(_ data: Data, account: String,
                                timeout: TimeInterval = 2) throws -> KeychainBrokerStoredState {
        if failMigrate || failMigrateAccount == account {
            throw NSError(domain: "RoboparkKeychain", code: Int(errSecAuthFailed))
        }
        if isReceiptRow(account), receiptSealed,
           states[account] == nil { return .absent }
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
    static func delete(_ account: String, timeout: TimeInterval = 2) throws {
        deletes += 1
        if failDelete { throw NSError(domain: "RoboparkKeychain", code: Int(errSecAuthFailed)) }
        if isReceiptRow(account) {
            guard receiptSealed else { throw NSError(domain: "RoboparkKeychain", code: Int(errSecAuthFailed)) }
            states.removeValue(forKey: account)
        } else {
            states[account] = .deleted
        }
    }
    static func receiptMigrationSealed(timeout: TimeInterval = 2) throws -> Bool { receiptSealed }
    static func sealReceiptMigration(timeout: TimeInterval = 2) throws {
        sealCalls += 1
        if failSeal { throw NSError(domain: "RoboparkKeychain", code: Int(errSecAuthFailed)) }
        receiptSealed = true
    }
    static func isReceiptRow(_ account: String) -> Bool {
        account.range(of: "^corporate-write-receipt-v1-[a-f0-9]{64}$",
                      options: .regularExpression) != nil
    }
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
    KeychainBrokerClient.reset()
}
static func sourceValue(_ account: String, service: String = "ru.robopark.agent") -> Data? {
    var request = legacyQuery(account, service: service)
    request[kSecReturnData as String] = true
    var item: CFTypeRef?
    guard SecItemCopyMatching(request as CFDictionary, &item) == errSecSuccess else { return nil }
    return item as? Data
}
static func brokerValue(_ account: String) -> Data? {
    guard case let .value(data) = KeychainBrokerClient.states[account] else { return nil }
    return data
}
static func requireFailure(_ message: String, _ body: () throws -> Void) {
    do { try body(); fatalError(message) } catch { }
}
static func receipt(_ character: Character) -> String {
    "corporate-write-receipt-v1-" + String(repeating: String(character), count: 64)
}
static func main() throws {
    let path = CommandLine.arguments[1]
    // Empty password belongs only to this disposable Keychain, never login.keychain.
    guard SecKeychainOpen(path, &Fixture.keychain) == errSecSuccess,
          Fixture.keychain != nil else { fatalError("fixture creation failed") }
    resetFixture()
    guard try SecureStore.readData("workspace-profile-v1") == nil,
          KeychainBrokerClient.writes == 0 else { fatalError("clean install did not stay empty") }

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
              brokerValue(account) == bytes else { fatalError("required account not migrated: \(account)") }
    }
    let first = try SecureStore.readData("registration")
    guard first == oldValue,
          sourceValue("registration") == oldValue,
          brokerValue("registration") == oldValue else {
        fatalError("copy-before-use migration lost data")
    }

    resetFixture()
    let stableAccount = "registration"
    let stableValue = Data("stable-v1-source".utf8)
    insertLegacy(stableAccount, stableValue, service: "ru.robopark.agent.stable-v1")
    guard try SecureStore.readData(stableAccount) == stableValue,
          sourceValue(stableAccount, service: "ru.robopark.agent.stable-v1") == stableValue else {
        fatalError("stable-v1 source was not migrated")
    }

    resetFixture()
    let partialAccount = "registration"
    insertLegacy(partialAccount, Data("partial-source".utf8))
    KeychainBrokerClient.failMigrate = true
    requireFailure("failed broker write used legacy data") { _ = try SecureStore.readData(partialAccount) }
    KeychainBrokerClient.failMigrate = false
    guard sourceValue(partialAccount) == Data("partial-source".utf8) else { fatalError("partial migration removed source") }

    resetFixture()
    let mismatchAccount = "registration"
    insertLegacy(mismatchAccount, Data("mismatch-source".utf8))
    KeychainBrokerClient.corruptReadback = true
    requireFailure("unverified migration data was used") { _ = try SecureStore.readData(mismatchAccount) }
    KeychainBrokerClient.corruptReadback = false

    resetFixture()
    let racedDeleteAccount = "registration"
    insertLegacy(racedDeleteAccount, Data("deleted-source".utf8))
    KeychainBrokerClient.deleteOnMigrate = true
    guard try SecureStore.readData(racedDeleteAccount) == nil,
          sourceValue(racedDeleteAccount) == Data("deleted-source".utf8) else {
        fatalError("concurrent tombstone resurrected legacy data")
    }
    KeychainBrokerClient.deleteOnMigrate = false

    resetFixture()
    let racedWriteAccount = "registration"
    insertLegacy(racedWriteAccount, Data("old-source".utf8))
    KeychainBrokerClient.valueOnMigrate = Data("new-authoritative".utf8)
    guard try SecureStore.readData(racedWriteAccount) == Data("new-authoritative".utf8) else {
        fatalError("concurrent broker value was overwritten by migration")
    }
    KeychainBrokerClient.valueOnMigrate = nil

    resetFixture()
    let transportAccount = "registration"
    insertLegacy(transportAccount, Data("must-not-fallback".utf8))
    let writesBeforeFailure = KeychainBrokerClient.writes
    KeychainBrokerClient.failRead = true
    requireFailure("broker outage fell back to direct storage") { _ = try SecureStore.readData(transportAccount) }
    KeychainBrokerClient.failRead = false
    guard KeychainBrokerClient.writes == writesBeforeFailure else { fatalError("broker outage attempted migration") }

    resetFixture()
    let lockedAccount = "registration"
    insertLegacy(lockedAccount, Data("locked-source".utf8))
    var originalInteraction = DarwinBoolean(false)
    SecKeychainGetUserInteractionAllowed(&originalInteraction)
    KeychainBrokerClient.lockedRead = true
    requireFailure("locked keychain was treated as readable") { _ = try SecureStore.readData(lockedAccount) }
    KeychainBrokerClient.lockedRead = false
    var interactionAfter = DarwinBoolean(false)
    SecKeychainGetUserInteractionAllowed(&interactionAfter)
    guard interactionAfter.boolValue == originalInteraction.boolValue else { fatalError("interaction state leaked") }
    guard try SecureStore.withUserApproval({ try SecureStore.readData(lockedAccount) }) == Data("locked-source".utf8) else { fatalError("foreground recovery lost data") }

    resetFixture()
    insertLegacy("registration", oldValue)
    guard try SecureStore.readData("registration") == oldValue else { fatalError("delete setup migration failed") }
    try SecureStore.deleteData("registration")
    guard try SecureStore.readData("registration") == nil,
          sourceValue("registration") == oldValue,
          case .deleted = KeychainBrokerClient.states["registration"] else {
        fatalError("deleted legacy item resurrected")
    }
    try SecureStore.writeData(Data("recreated".utf8), account: "registration")
    guard try SecureStore.readData("registration") == Data("recreated".utf8) else {
        fatalError("write did not replace tombstone")
    }
    KeychainBrokerClient.failDelete = true
    requireFailure("failed delete was reported as successful") { try SecureStore.deleteData("registration") }
    KeychainBrokerClient.failDelete = false
    guard try SecureStore.readData("registration") == Data("recreated".utf8) else {
        fatalError("failed delete changed stored value")
    }

    // Receipt migration is sealed only after the complete legacy index and all
    // referenced rows have been copied and verified.
    resetFixture()
    let receiptA = receipt("a")
    let receiptB = receipt("b")
    insertLegacy(receiptA, Data("receipt-a".utf8))
    insertLegacy(receiptB, Data("receipt-b".utf8))
    insertLegacy("corporate-write-receipt-index-v1",
        try JSONEncoder().encode(CorporateWriteReceiptIndex(accounts: [receiptA, receiptB])))
    KeychainBrokerClient.failMigrateAccount = receiptB
    requireFailure("partial receipt migration sealed early") {
        try SecureStore.withUserApproval { try SecureStore.migrateCorporateWriteReceipts() }
    }
    guard !KeychainBrokerClient.receiptSealed,
          KeychainBrokerClient.sealCalls == 0,
          brokerValue(receiptA) == Data("receipt-a".utf8),
          brokerValue(receiptB) == nil else { fatalError("partial receipt migration was not retryable") }
    KeychainBrokerClient.failMigrateAccount = nil
    try SecureStore.withUserApproval { try SecureStore.migrateCorporateWriteReceipts() }
    guard KeychainBrokerClient.receiptSealed,
          KeychainBrokerClient.sealCalls == 1,
          brokerValue(receiptB) == Data("receipt-b".utf8) else { fatalError("receipt migration retry failed") }
    try SecureStore.migrateCorporateWriteReceipts()
    guard KeychainBrokerClient.sealCalls == 1 else { fatalError("receipt migration seal was repeated") }

    // Once sealed, old rows cannot return and acknowledgements physically
    // remove rows instead of accumulating one tombstone per command forever.
    try SecureStore.deleteData(receiptA)
    guard KeychainBrokerClient.states[receiptA] == nil,
          try SecureStore.readData(receiptA) == nil,
          sourceValue(receiptA) == Data("receipt-a".utf8) else {
        fatalError("sealed receipt deletion resurrected a legacy row")
    }
    let receiptC = receipt("c")
    try SecureStore.writeData(Data("new-receipt".utf8), account: receiptC)
    try SecureStore.deleteData(receiptC)
    guard KeychainBrokerClient.states[receiptC] == nil,
          try SecureStore.readData(receiptC) == nil else {
        fatalError("post-seal receipt acknowledgement retained a tombstone")
    }
    try SecureStore.deleteData("corporate-write-receipt-index-v1")
    guard try SecureStore.readData("corporate-write-receipt-index-v1") == nil,
          case .deleted = KeychainBrokerClient.states["corporate-write-receipt-index-v1"] else {
        fatalError("sealed index tombstone fell back to legacy")
    }

    resetFixture()
    insertLegacy("corporate-write-receipt-index-v1", Data("corrupt".utf8))
    requireFailure("corrupt receipt index was sealed") { try SecureStore.migrateCorporateWriteReceipts() }
    guard !KeychainBrokerClient.receiptSealed,
          KeychainBrokerClient.sealCalls == 0 else { fatalError("corrupt receipt index changed seal") }

    resetFixture()
    insertLegacy("corporate-write-receipt-index-v1",
        try JSONEncoder().encode(CorporateWriteReceiptIndex(accounts: ["invalid-row"])))
    requireFailure("invalid receipt account was sealed") { try SecureStore.migrateCorporateWriteReceipts() }
    guard !KeychainBrokerClient.receiptSealed,
          KeychainBrokerClient.sealCalls == 0 else { fatalError("invalid receipt index changed seal") }

    resetFixture()
    insertLegacy("corporate-write-receipt-index-v1",
        try JSONEncoder().encode(CorporateWriteReceiptIndex(accounts: [receipt("d")])))
    KeychainBrokerClient.lockedRead = true
    requireFailure("locked receipt storage was sealed") { try SecureStore.migrateCorporateWriteReceipts() }
    KeychainBrokerClient.lockedRead = false
    guard !KeychainBrokerClient.receiptSealed,
          KeychainBrokerClient.sealCalls == 0 else { fatalError("locked receipt storage changed seal") }

    resetFixture()
    try SecureStore.migrateCorporateWriteReceipts()
    guard KeychainBrokerClient.receiptSealed,
          KeychainBrokerClient.sealCalls == 1 else { fatalError("empty receipt migration did not seal once") }
    let legacyAfterSeal = receipt("e")
    insertLegacy(legacyAfterSeal, Data("must-stay-ignored".utf8))
    guard try SecureStore.readData(legacyAfterSeal) == nil,
          sourceValue(legacyAfterSeal) != nil else { fatalError("sealed missing row fell back to legacy") }

    print("PASS: SecureStore account migration, receipt seal, retry, lock and physical ACK checks (isolated Keychain)")
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
