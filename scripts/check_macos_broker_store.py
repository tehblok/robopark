#!/usr/bin/env python3
"""Exercise the production BrokerStore receipt seal in disposable Keychains.

The production source is copied verbatim except for its Keychain query: every
SecItem call is pinned to a fresh temporary Keychain.  This deliberately does
not contact the installed broker or the login Keychain.
"""
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = """// swift-tools-version: 5.9
import PackageDescription
let package = Package(name: "BrokerStoreChecks", platforms: [.macOS(.v13)], targets: [
    .executableTarget(name: "BrokerStoreChecks")
])
"""

CHECKS = r'''
private enum Fixture {
    static var keychain: SecKeychain!
}

private func payloadSizeAllowed(_ size: Int) -> Bool { size <= 1024 * 1024 }
private func accountAllowed(_ account: String) -> Bool {
    let exact = ["registration", "workspace-profile-v1", "workspace-browser-session-v1",
                 "workspace-storage-key-v1", "corporate-write-receipt-index-v1"]
    if exact.contains(account) { return true }
    for prefix in ["enrollment-key-v1-", "account-claim-key-v1-", "corporate-write-receipt-v1-"] {
        guard account.hasPrefix(prefix) else { continue }
        let digest = String(account.dropFirst(prefix.count))
        if digest.count == 64 && digest.allSatisfy({ $0.isNumber || ($0 >= "a" && $0 <= "f") }) { return true }
    }
    return false
}
private func receipt(_ letter: Character) -> String {
    "corporate-write-receipt-v1-" + String(repeating: String(letter), count: 64)
}
private let testService = "ru.robopark.keychain-broker-v1"
private let valueMarker = Data("value-v1".utf8)
private let deletedMarker = Data("deleted-v1".utf8)
private let sealAccount = "internal-receipt-migration-sealed-v1"

private func rawQuery(_ account: String) -> [String: Any] {
    [kSecClass as String: kSecClassGenericPassword,
     kSecAttrService as String: testService,
     kSecAttrAccount as String: account,
     kSecUseKeychain as String: Fixture.keychain!,
     kSecMatchSearchList as String: [Fixture.keychain!]]
}
private func rawPut(_ account: String, _ value: Data, _ marker: Data) {
    let attributes: [String: Any] = [kSecValueData as String: value,
                                     kSecAttrGeneric as String: marker,
                                     kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly]
    var status = SecItemUpdate(rawQuery(account) as CFDictionary, attributes as CFDictionary)
    if status == errSecItemNotFound {
        status = SecItemAdd(rawQuery(account).merging(attributes) { _, new in new } as CFDictionary, nil)
    }
    guard status == errSecSuccess else { fatalError("raw fixture put failed for \(account): \(status)") }
}
private func rawStatus(_ account: String) -> OSStatus {
    var request = rawQuery(account); request[kSecReturnData as String] = true
    return SecItemCopyMatching(request as CFDictionary, nil)
}
private func expectError(_ name: String, _ body: () throws -> Void) {
    do { try body(); fatalError("expected failure: \(name)") } catch { }
}
private func expect(_ value: Bool, _ message: String) { if !value { fatalError(message) } }
private func withKeychain(_ path: String, _ body: () throws -> Void) rethrows {
    var opened: SecKeychain?
    expect(SecKeychainOpen(path, &opened) == errSecSuccess && opened != nil, "cannot open fixture keychain")
    Fixture.keychain = opened
    try body()
    Fixture.keychain = nil
}

private func testMigrationAndSeal() throws {
    let retained = receipt("a"), oldTombstone = receipt("b"), newReceipt = receipt("c")
    rawPut(retained, Data("legacy-retained".utf8), valueMarker)
    rawPut(oldTombstone, Data(), deletedMarker)
    rawPut("corporate-write-receipt-index-v1", Data(), deletedMarker)

    // Pre-seal operations cannot create or remove dynamic receipt rows.  An
    // existing row is retained by atomic migration rather than overwritten.
    expectError("pre-seal write") { try BrokerStore.write(Data("new".utf8), account: retained) }
    expectError("pre-seal delete") { try BrokerStore.delete(retained) }
    if case let .value(data) = try BrokerStore.migrateIfAbsent(Data("replacement".utf8), account: retained) {
        expect(data == Data("legacy-retained".utf8), "pre-seal migration overwrote retained receipt")
    } else { fatalError("pre-seal migration did not retain receipt") }

    try BrokerStore.sealReceiptMigration()
    expect(try BrokerStore.receiptMigrationSealed(), "seal was not persisted")
    if case let .value(data) = try BrokerStore.read(retained) {
        expect(data == Data("legacy-retained".utf8), "seal lost retained receipt")
    } else { fatalError("retained receipt missing after seal") }
    expect(rawStatus(oldTombstone) == errSecItemNotFound, "seal retained dynamic receipt tombstone")
    if case .deleted = try BrokerStore.read("corporate-write-receipt-index-v1") {} else {
        fatalError("seal removed fixed receipt-index tombstone")
    }

    // A repeat seal is harmless, and post-seal dynamic acknowledgements use a
    // physical delete.  Old data cannot reappear through migrateIfAbsent.
    try BrokerStore.sealReceiptMigration()
    try BrokerStore.delete(retained)
    expect(rawStatus(retained) == errSecItemNotFound, "sealed delete retained dynamic tombstone")
    if case .absent = try BrokerStore.migrateIfAbsent(Data("must-not-return".utf8), account: retained) {} else {
        fatalError("sealed migration resurrected deleted receipt")
    }
    try BrokerStore.write(Data("post-seal".utf8), account: newReceipt)
    try BrokerStore.delete(newReceipt)
    expect(rawStatus(newReceipt) == errSecItemNotFound, "post-seal acknowledgement retained a tombstone")
}

private func testCorruptReceiptMarkerFailsClosed() throws {
    rawPut(receipt("d"), Data("corrupt".utf8), Data("unknown-marker".utf8))
    expectError("corrupt receipt marker") { try BrokerStore.sealReceiptMigration() }
    expect(rawStatus(sealAccount) == errSecItemNotFound, "corrupt receipt marker wrote a seal")
}

private func testCorruptSealMarkerFailsClosed() throws {
    rawPut(sealAccount, Data("not-a-seal".utf8), valueMarker)
    expectError("corrupt seal marker") { _ = try BrokerStore.receiptMigrationSealed() }
    expectError("corrupt seal marker reseal") { try BrokerStore.sealReceiptMigration() }
    expect(rawStatus(sealAccount) == errSecSuccess, "corrupt marker was silently removed")
}

@main private struct Main {
    static func main() throws {
        let paths = Array(CommandLine.arguments.dropFirst())
        expect(paths.count == 3, "expected three disposable Keychain paths")
        try withKeychain(paths[0]) { try testMigrationAndSeal() }
        try withKeychain(paths[1]) { try testCorruptReceiptMarkerFailsClosed() }
        try withKeychain(paths[2]) { try testCorruptSealMarkerFailsClosed() }
        print("PASS: BrokerStore receipt migration seal: real isolated SecItem checks")
    }
}
'''


def extract_store() -> str:
    source = (ROOT / "apps/macos/Sources/RoboparkKeychainBroker/main.swift").read_text()
    start = source.index("private enum StoredState")
    end = source.index("\nprivate final class BrokerServer", start)
    store = source[start:end]
    store = store.replace("import Foundation\nimport LocalAuthentication\nimport Security\nimport XPC\nimport KeychainBrokerIPC\n", "")
    store = store.replace("rp_keychain_broker_payload_size_allowed(data.count)", "payloadSizeAllowed(data.count)")
    store = store.replace("account.withCString({ rp_keychain_broker_account_allowed($0) })", "accountAllowed(account)")
    original = '''[kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account,
         kSecUseAuthenticationContext as String: authenticationContext]'''
    replacement = '''[kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account,
         kSecUseKeychain as String: Fixture.keychain!,
         kSecMatchSearchList as String: [Fixture.keychain!],
         kSecUseAuthenticationContext as String: authenticationContext]'''
    if original not in store:
        raise RuntimeError("BrokerStore query drifted; update isolated harness before running it")
    store = store.replace(original, replacement)
    seal_request = '''kSecAttrService as String: service,
            kSecReturnAttributes as String: true,'''
    seal_replacement = '''kSecAttrService as String: service,
            kSecUseKeychain as String: Fixture.keychain!,
            kSecMatchSearchList as String: [Fixture.keychain!],
            kSecReturnAttributes as String: true,'''
    if seal_request not in store:
        raise RuntimeError("BrokerStore seal query drifted; update isolated harness before running it")
    return store.replace(seal_request, seal_replacement)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="robopark-broker-store-") as tmp:
        root = Path(tmp)
        target = root / "Sources/BrokerStoreChecks"
        target.mkdir(parents=True)
        swift = "import Foundation\nimport LocalAuthentication\nimport Security\n\n" + CHECKS + "\n" + extract_store()
        (target / "main.swift").write_text(swift)
        (root / "Package.swift").write_text(PACKAGE)
        cache = Path("/private/tmp/robopark-broker-store-checks")
        options = ["--package-path", str(root), "--scratch-path", str(cache),
                   "--cache-path", str(cache / "cache"), "--config-path", str(cache / "config"),
                   "--security-path", str(cache / "security"), "--disable-sandbox"]
        env = {**os.environ, "CLANG_MODULE_CACHE_PATH": str(cache / "clang"),
               "SWIFTPM_MODULECACHE_OVERRIDE": str(cache / "modules")}
        subprocess.run(["swift", "build", *options], env=env, check=True)
        binary = subprocess.check_output(["swift", "build", *options, "--show-bin-path"],
                                         env=env, text=True).strip()
        keychains = [root / f"fixture-{index}.keychain-db" for index in range(3)]
        for keychain in keychains:
            subprocess.run(["security", "create-keychain", "-p", "", str(keychain)],
                           check=True, capture_output=True)
        try:
            subprocess.run([str(Path(binary) / "BrokerStoreChecks"), *map(str, keychains)],
                           check=True, timeout=20)
        finally:
            for keychain in keychains:
                subprocess.run(["security", "delete-keychain", str(keychain)],
                               check=True, capture_output=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
