#!/usr/bin/env python3
"""Exercise production accountVerified with a suspended registration transport.

No network, credentials, AppKit windows or user defaults are accessed. The
method and enrollment identity are extracted verbatim from Application.swift;
only its collaborators are deterministic test doubles.
"""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]

SHIMS = r'''
import Foundation
enum AgentError: Error { case ownerMismatch }
enum TestDeploymentAssignment {
    static func matches(origin: String, instanceID: String, environment: String) -> Bool { environment == "test" }
}
enum SecureStore {
    static var signedOut = false
    static func isSignedOut(_ name: String) -> Bool { signedOut }
}
final class UserDefaults {
    static let standard = UserDefaults()
    func set(_ value: Bool, forKey: String) {}
}
struct Profile {
    var origin = "https://test.invalid"
    var instanceID = "test-instance"
    var environment = "test"
    var accountID: Int? = 7
    var ownerUserID: Int? = 7
}
@MainActor final class Session {
    var profile: Profile? = Profile()
    func call(path: String, method: String, object: [String: Any]) async throws -> (Data, HTTPURLResponse) {
        (Data(), HTTPURLResponse(url: URL(string: "https://test.invalid")!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
    }
}
@MainActor final class Workspace { let session = Session() }
@MainActor final class Bridge {
    var registration: Profile?
    var pending: [CheckedContinuation<Void, Never>] = []
    var starts = 0
    func stop() {}
    func clearProbe() throws {}
    func start() { starts += 1 }
    func startIfNeeded() {}
    func claimAuthenticated(origin: String, label: String, expectedOwnerUserID: Int,
                            startEmergency: Bool,
                            claim: ([String: Any]) async throws -> (Data, HTTPURLResponse)) async throws {
        // Intentionally ignore cancellation like a late network completion.
        await withCheckedContinuation { pending.append($0) }
        registration = Profile()
        if startEmergency { start() }
    }
    func release(_ index: Int) { pending[index].resume() }
}
@MainActor final class Compute { var starts = 0; func start() { starts += 1 }; func stop() {} }
@MainActor final class Notifications {
    var starts = 0
    func start(accountID: Int, role: String, session: Session) { starts += 1 }
    func stop(removeDelivered: Bool) {}
}
@MainActor final class Updater { func check() async {} }
@MainActor final class Harness {
    var workspace: Workspace? = Workspace()
    let bridge = Bridge(); let compute = Compute(); let notifications = Notifications(); let updater = Updater()
    var enrollmentTask: Task<Void, Never>?
    var enrollmentRun = UUID()
    var accountLabel = ""; var accountRole: String?
    var accountActive = false; var emergencyAuthorized = false; var activeAccountID: Int?
    let accountPresentKey = "test-only"
    var lastError = ""
    func cancelProbeRefresh() {}
    func refreshProbeTarget() {}
    func render() {}
    // PRODUCTION_IDENTITY
    // PRODUCTION_METHOD
}
@main struct Checks {
    @MainActor static func main() async {
        func user(_ emergency: Bool = true, approved: Bool = true) -> [String: Any] {
            ["id": 7, "role": "royal", "access_status": approved ? "approved" : "revoked",
             "permissions": emergency ? ["nav.emergency"] : []]
        }
        func waitUntil(_ predicate: () -> Bool) async {
            for _ in 0..<1000 { if predicate() { return }; await Task.yield() }
            fatalError("FAIL: asynchronous registration did not reach checkpoint")
        }
        let normal = Harness()
        await normal.accountVerified(user())
        await waitUntil { normal.bridge.pending.count == 1 }
        precondition(!normal.accountActive, "identity response must return before registration completes")
        await normal.accountVerified(user())
        precondition(normal.bridge.pending.count == 1, "same identity must not duplicate registration")
        normal.bridge.release(0)
        await waitUntil { normal.enrollmentTask == nil }
        precondition(normal.accountActive && normal.bridge.starts == 1 && normal.compute.starts == 1)

        let revoked = Harness()
        await revoked.accountVerified(user())
        await waitUntil { revoked.bridge.pending.count == 1 }
        await revoked.accountVerified(user(false))
        await waitUntil { revoked.bridge.pending.count == 2 }
        revoked.bridge.release(0)
        for _ in 0..<10 { await Task.yield() }
        precondition(revoked.bridge.starts == 0 && revoked.compute.starts == 0,
                     "late stale registration must not activate workers")
        revoked.bridge.release(1)
        await waitUntil { revoked.enrollmentTask == nil }
        precondition(revoked.accountActive && !revoked.emergencyAuthorized && revoked.bridge.starts == 0)

        let denied = Harness()
        await denied.accountVerified(user())
        await waitUntil { denied.bridge.pending.count == 1 }
        await denied.accountVerified(user(approved: false))
        denied.bridge.release(0)
        for _ in 0..<10 { await Task.yield() }
        precondition(!denied.accountActive && denied.bridge.starts == 0 && denied.compute.starts == 0)

        let signedOut = Harness()
        await signedOut.accountVerified(user())
        await waitUntil { signedOut.bridge.pending.count == 1 }
        SecureStore.signedOut = true
        signedOut.workspace?.session.profile?.accountID = nil
        signedOut.bridge.release(0)
        await waitUntil { signedOut.enrollmentTask == nil }
        precondition(!signedOut.accountActive && signedOut.bridge.starts == 0 && signedOut.compute.starts == 0,
                     "late registration must not revive a signed-out account")
        print("PASS: account activation is nonblocking, coalesced and fenced after revocation/logout")
    }
}
'''


def main() -> int:
    source = (ROOT / 'apps/macos/Sources/RoboparkAgent/Application.swift').read_text()
    identity = source.split('    private struct EnrollmentIdentity', 1)[1].split('    private var probeRefreshTask', 1)[0]
    identity = '    private struct EnrollmentIdentity' + identity
    method = source.split('    private func accountVerified', 1)[1].split('    private func accountLogout', 1)[0]
    method = '    func accountVerified' + method
    code = SHIMS.replace('    // PRODUCTION_IDENTITY', identity).replace('    // PRODUCTION_METHOD', method)
    with tempfile.TemporaryDirectory(prefix='robopark-account-activation-') as raw:
        base = Path(raw)
        (base / 'Checks.swift').write_text(code)
        env = {**os.environ, 'CLANG_MODULE_CACHE_PATH': '/private/tmp/robopark-swift-module-cache'}
        subprocess.run(['swiftc', '-parse-as-library', str(base / 'Checks.swift'), '-o', str(base / 'checks')], env=env, check=True)
        return subprocess.run([str(base / 'checks')], timeout=15).returncode


if __name__ == '__main__':
    raise SystemExit(main())
