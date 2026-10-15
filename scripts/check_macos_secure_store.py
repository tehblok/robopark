#!/usr/bin/env python3
"""Exercise production SecureStore in an isolated, disposable Keychain only."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = """// swift-tools-version: 5.9
import PackageDescription
let package = Package(name: "SecureStoreChecks", platforms: [.macOS(.v13)], targets: [
.target(name: "AgentCore"), .executableTarget(name: "SecureStoreChecks", dependencies: ["AgentCore"])])
"""
MAIN = r'''import Foundation
import Security
import AgentCore

enum Fixture { static var keychain: SecKeychain? }
@main struct Checks {
static func main() throws {
    let path = CommandLine.arguments[1]
    // Empty password belongs only to this disposable Keychain, never login.keychain.
    guard SecKeychainOpen(path, &Fixture.keychain) == errSecSuccess,
          let keychain = Fixture.keychain else { fatalError("fixture creation failed") }
    let legacy: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: "ru.robopark.agent", kSecAttrAccount as String: "fixture",
        kSecValueData as String: Data("fixture-only-value".utf8), kSecUseKeychain as String: keychain]
    guard SecItemAdd(legacy as CFDictionary, nil) == errSecSuccess else { fatalError("fixture insert failed") }
    let receiptAccount = "corporate-write-receipt-v1-" + String(repeating: "a", count: 64)
    var receiptQuery = legacy
    receiptQuery[kSecAttrAccount as String] = receiptAccount
    receiptQuery[kSecValueData as String] = Data("fixture-unknown-receipt".utf8)
    guard SecItemAdd(receiptQuery as CFDictionary, nil) == errSecSuccess else { fatalError("receipt fixture failed") }
    receiptQuery[kSecAttrAccount as String] = "corporate-write-receipt-index-v1"
    receiptQuery[kSecValueData as String] = try JSONEncoder().encode(CorporateWriteReceiptIndex(accounts: [receiptAccount]))
    guard SecItemAdd(receiptQuery as CFDictionary, nil) == errSecSuccess else { fatalError("index fixture failed") }
    try SecureStore.withUserApproval { try SecureStore.migrateCorporateWriteReceipts() }
    var stableReceipt = legacy
    stableReceipt[kSecAttrService as String] = "ru.robopark.agent.stable-v1"
    stableReceipt[kSecAttrAccount as String] = receiptAccount
    stableReceipt.removeValue(forKey: kSecValueData as String)
    stableReceipt[kSecReturnData as String] = true
    stableReceipt[kSecMatchSearchList as String] = [keychain]
    var migratedReceipt: CFTypeRef?
    guard SecItemCopyMatching(stableReceipt as CFDictionary, &migratedReceipt) == errSecSuccess,
          (migratedReceipt as? Data) == Data("fixture-unknown-receipt".utf8) else { fatalError("unknown receipt not migrated") }
    let first = try SecureStore.readData("fixture")
    guard first == Data("fixture-only-value".utf8) else { fatalError("legacy migration lost data") }
    try SecureStore.writeData(Data("fixture-new-value".utf8), account: "fixture")
    guard try SecureStore.readData("fixture") == Data("fixture-new-value".utf8) else { fatalError("stable item not authoritative") }
    var originalInteraction = DarwinBoolean(false)
    SecKeychainGetUserInteractionAllowed(&originalInteraction)
    SecKeychainLock(keychain)
    do { _ = try SecureStore.readData("fixture"); fatalError("locked keychain was treated as readable") }
    catch { /* Must fail quickly without displaying a dialog. */ }
    var interactionAfter = DarwinBoolean(false)
    SecKeychainGetUserInteractionAllowed(&interactionAfter)
    guard interactionAfter.boolValue == originalInteraction.boolValue else { fatalError("interaction state leaked") }
    guard "".withCString { SecKeychainUnlock(keychain, 0, $0, true) } == errSecSuccess else { fatalError("fixture unlock failed") }
    guard try SecureStore.withUserApproval({ try SecureStore.readData("fixture") }) == Data("fixture-new-value".utf8) else { fatalError("foreground recovery lost data") }
    try SecureStore.deleteData("fixture")
    guard try SecureStore.readData("fixture") == nil else { fatalError("deleted legacy item resurrected") }
    print("PASS: 7 SecureStore migration, lock, no-dialog and deletion checks (isolated Keychain)")
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
