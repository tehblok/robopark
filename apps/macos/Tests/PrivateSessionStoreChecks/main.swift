import Darwin
import Foundation
import AgentCore

private var checks = 0

private func check(_ condition: @autoclosure () throws -> Bool, _ message: String) rethrows {
    checks += 1
    guard try condition() else {
        fputs("FAIL: \(message)\n", stderr)
        exit(1)
    }
}

private func rejects(_ message: String, _ body: () throws -> Void) {
    do {
        try body()
        fputs("FAIL: accepted \(message)\n", stderr)
        exit(1)
    } catch {
        checks += 1
    }
}

private func mode(_ path: String) throws -> mode_t {
    var status = stat()
    guard lstat(path, &status) == 0 else { throw POSIXError(.init(rawValue: errno) ?? .EIO) }
    return status.st_mode & 0o777
}

private func makeRoot(_ label: String) throws -> URL {
    let url = FileManager.default.temporaryDirectory
        .appendingPathComponent("robopark-private-store-\(label)-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: url, withIntermediateDirectories: false,
                                            attributes: [.posixPermissions: 0o755])
    return url
}

private func launchChild(_ arguments: [String]) throws -> Process {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: CommandLine.arguments[0])
    process.arguments = arguments
    process.standardOutput = FileHandle.nullDevice
    try process.run()
    return process
}

if CommandLine.arguments.count == 6, CommandLine.arguments[1] == "--child" {
    let operation = CommandLine.arguments[2]
    let root = URL(fileURLWithPath: CommandLine.arguments[3], isDirectory: true)
    let account = CommandLine.arguments[4]
    guard let payload = Data(base64Encoded: CommandLine.arguments[5]) else { exit(64) }
    do {
        let store = try PrivateSessionStore(directory: root)
        if operation == "write" {
            try store.write(payload, account: account)
        } else if operation == "migrate" {
            _ = try store.migrateIfAbsent(payload, account: account)
        } else if operation == "migrate-delete" {
            _ = try store.migrateDeletionIfAbsent(account)
        } else {
            exit(64)
        }
        exit(0)
    } catch {
        fputs("child error: \(error)\n", stderr)
        exit(1)
    }
}

let root = try makeRoot("roundtrip")
defer { try? FileManager.default.removeItem(at: root) }
let store = try PrivateSessionStore(directory: root)
try check(try mode(root.path) == 0o700, "store directory is private")
try check(store.readState("registration") == .absent, "new account is absent")

let binary = Data([0x00, 0xff, 0x80, 0x0a, 0x00])
try store.write(binary, account: "registration")
try check(store.readState("registration") == .value(binary), "binary value round trips")
let restarted = try PrivateSessionStore(directory: root)
try check(restarted.readState("registration") == .value(binary), "value survives store restart")

try restarted.write(Data(), account: "workspace-profile-v1")
try check(restarted.readState("workspace-profile-v1") == .value(Data()),
          "empty value is distinct from absence")
try restarted.delete("workspace-profile-v1")
try check(restarted.readState("workspace-profile-v1") == .deleted,
          "normal delete persists a tombstone")
try restarted.delete("workspace-profile-v1", tombstone: false)
try check(restarted.readState("workspace-profile-v1") == .absent,
          "explicit physical delete removes the record")

let first = Data("first".utf8)
let second = Data("second".utf8)
try check(try restarted.migrateIfAbsent(first, account: "workspace-browser-session-v1") == .value(first),
          "migration stores the first value")
try check(try restarted.migrateIfAbsent(second, account: "workspace-browser-session-v1") == .value(first),
          "migration is idempotent")
try restarted.delete("workspace-browser-session-v1")
try check(try restarted.migrateIfAbsent(second, account: "workspace-browser-session-v1") == .deleted,
          "migration cannot resurrect a tombstoned account")
try check(try restarted.migrateDeletionIfAbsent("workspace-storage-key-v1") == .deleted,
          "legacy deletion migration creates a tombstone when absent")
try restarted.write(first, account: "workspace-storage-key-v1")
try check(try restarted.migrateDeletionIfAbsent("workspace-storage-key-v1") == .value(first),
          "legacy deletion migration cannot overwrite a new value")

let digest = String(repeating: "a", count: 64)
for account in [
    "workspace-storage-key-v1", "corporate-write-receipt-index-v1",
    "internal-receipt-migration-sealed-v1", "enrollment-key-v1-\(digest)",
    "account-claim-key-v1-\(digest)", "corporate-write-receipt-v1-\(digest)",
] {
    try restarted.write(Data(account.utf8), account: account)
    try check(restarted.readState(account) == .value(Data(account.utf8)),
              "approved account \(account) round trips")
}
for account in ["", "../registration", "Registration", "workspace-profile-v2",
                "enrollment-key-v1-" + String(repeating: "a", count: 63),
                "account-claim-key-v1-" + String(repeating: "g", count: 64),
                "corporate-write-receipt-v1-\(digest)-extra"] {
    rejects("unapproved account \(account)") { _ = try restarted.readState(account) }
}
rejects("payload over one MiB") {
    try restarted.write(Data(repeating: 1, count: 1_048_577), account: "registration")
}

let recordURL = root.appendingPathComponent("registration.rps")
try check(try mode(recordURL.path) == 0o600, "record is readable only by its owner")
try check(try mode(root.appendingPathComponent("store.lock").path) == 0o600,
          "cross-process lock is readable only by its owner")
let validBytes = try Data(contentsOf: recordURL)
let corruptBytes = Data("not-a-private-session-record".utf8)
try corruptBytes.write(to: recordURL)
try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: recordURL.path)
rejects("corrupt record") { _ = try restarted.readState("registration") }
rejects("write over corrupt record") { try restarted.write(binary, account: "registration") }
try check(try Data(contentsOf: recordURL) == corruptBytes, "corruption is never silently replaced")
try validBytes.write(to: recordURL)
try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: recordURL.path)

let symlinkRoot = try makeRoot("symlink")
defer { try? FileManager.default.removeItem(at: symlinkRoot) }
let symlinkStore = try PrivateSessionStore(directory: symlinkRoot)
let outside = symlinkRoot.deletingLastPathComponent().appendingPathComponent("outside-\(UUID().uuidString)")
try Data("outside".utf8).write(to: outside)
defer { try? FileManager.default.removeItem(at: outside) }
try FileManager.default.createSymbolicLink(at: symlinkRoot.appendingPathComponent("registration.rps"),
                                           withDestinationURL: outside)
rejects("record symlink") { _ = try symlinkStore.readState("registration") }
try check(try Data(contentsOf: outside) == Data("outside".utf8), "record symlink target remains untouched")

let directoryTarget = try makeRoot("directory-target")
defer { try? FileManager.default.removeItem(at: directoryTarget) }
let directoryLink = directoryTarget.deletingLastPathComponent()
    .appendingPathComponent("robopark-private-store-directory-link-\(UUID().uuidString)")
try FileManager.default.createSymbolicLink(at: directoryLink, withDestinationURL: directoryTarget)
defer { try? FileManager.default.removeItem(at: directoryLink) }
rejects("store directory symlink") { _ = try PrivateSessionStore(directory: directoryLink) }

let raceRoot = try makeRoot("migration-race")
defer { try? FileManager.default.removeItem(at: raceRoot) }
let racePayloads = (0..<8).map { Data("migrate-\($0)".utf8) }
let migrations = try racePayloads.map {
    try launchChild(["--child", "migrate", raceRoot.path, "registration", $0.base64EncodedString()])
}
for child in migrations { child.waitUntilExit(); check(child.terminationStatus == 0, "migration child succeeds") }
let raced = try PrivateSessionStore(directory: raceRoot).readState("registration")
guard case let .value(raceWinner) = raced else { fatalError("FAIL: migration race left no value") }
check(racePayloads.contains(raceWinner), "migration race commits exactly one candidate")

let deletionRaceAccount = "account-claim-key-v1-" + String(repeating: "b", count: 64)
let deletionRaceValue = Data("new-local-value".utf8)
let deletionMigrator = try launchChild(["--child", "migrate-delete", raceRoot.path,
                                        deletionRaceAccount, Data().base64EncodedString()])
let deletionWriter = try launchChild(["--child", "write", raceRoot.path,
                                      deletionRaceAccount, deletionRaceValue.base64EncodedString()])
for child in [deletionMigrator, deletionWriter] {
    child.waitUntilExit()
    check(child.terminationStatus == 0, "deletion migration race child succeeds")
}
try check(try PrivateSessionStore(directory: raceRoot).readState(deletionRaceAccount) == .value(deletionRaceValue),
          "legacy deletion migration never overwrites a racing new value")

let writerPayloads = (0..<12).map { Data(repeating: UInt8($0), count: 64 * 1024 + $0) }
let writers = try writerPayloads.map {
    try launchChild(["--child", "write", raceRoot.path, "workspace-profile-v1", $0.base64EncodedString()])
}
for child in writers { child.waitUntilExit(); check(child.terminationStatus == 0, "writer child succeeds") }
guard case let .value(writerWinner) = try PrivateSessionStore(directory: raceRoot)
    .readState("workspace-profile-v1") else { fatalError("FAIL: concurrent writers left no value") }
check(writerPayloads.contains(writerWinner), "concurrent writers leave one complete value")

print("PrivateSessionStoreChecks: \(checks) passed")
