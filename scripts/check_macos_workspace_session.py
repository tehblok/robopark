#!/usr/bin/env python3
"""Compile and run production WorkspaceSession against a hermetic HTTPS transport.

This deliberately copies the production Swift source into a temporary package.
The only replacements are macOS Keychain, deployment-signature lookup and TLS
policy, which have in-memory deterministic implementations.  It never contacts
the network and it never prints cookie values or server URLs from a profile.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MACOS = ROOT / "apps" / "macos"

PACKAGE = """// swift-tools-version: 5.9
import PackageDescription
let package = Package(
    name: "WorkspaceSessionChecks",
    platforms: [.macOS(.v13)],
    targets: [
        .target(name: "AgentCore"),
        .executableTarget(name: "WorkspaceSessionChecks", dependencies: ["AgentCore"]),
    ]
)
"""

SHIMS = r"""
import Foundation
import AgentCore

enum NetworkFailure: Error { case auth, network, invalid }

final class RedirectDenyPolicy: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}
enum Network { static let policy = RedirectDenyPolicy() }

// Test-only Keychain replacement.  The tombstone intentionally remains when a
// simulated deletion fails, matching SecureStore.clear()'s production order.
enum SecureStore {
    static var values: [String: Data] = [:]
    static var signedOut: [String: Bool] = [:]
    static var failWrite = false
    static var failDelete = false
    static func reset() { values = [:]; signedOut = [:]; failWrite = false; failDelete = false }
    static func readData(_ account: String) throws -> Data? { values[account] }
    static func writeData(_ data: Data, account: String) throws {
        if failWrite { throw NSError(domain: "test.keychain", code: 1) }
        values[account] = data
    }
    static func deleteData(_ account: String) throws {
        if failDelete { throw NSError(domain: "test.keychain", code: 2) }
        values.removeValue(forKey: account)
    }
    static func isSignedOut(_ account: String) -> Bool { signedOut[account] ?? false }
    static func markSignedOut(_ account: String, _ value: Bool) { signedOut[account] = value }
}

enum EmbeddedTrust {
    static func verify(_ envelope: SignedCatalog?, origin: ServerOrigin, instanceID: String,
                       environment: String) throws -> DeploymentProfile {
        guard envelope != nil else { throw AgentError.wrongServer }
        let value: [String: Any] = ["purpose": "robopark-deployment-v1", "origin": origin.url.absoluteString,
            "instance_id": instanceID, "environment": environment, "channel": "rc", "expires": Int64.max]
        return try JSONDecoder().decode(DeploymentProfile.self, from: JSONSerialization.data(withJSONObject: value))
    }
}
"""

MAIN = r"""
import Foundation
import AgentCore

final class MockURLProtocol: URLProtocol {
    struct Reply { let status: Int; let headers: [String: String]; let body: Data; let delay: TimeInterval }
    static var replies: [String: Reply] = [:]
    static var seen: [URLRequest] = []
    static func reset() { replies = [:]; seen = [] }
    override class func canInit(with request: URLRequest) -> Bool { request.url?.scheme == "https" }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        guard let url = request.url else { return }
        let key = "\(request.httpMethod ?? "GET") \(url.path)?\(url.query ?? "")"
        let reply = Self.replies[key] ?? Reply(status: 404, headers: [:], body: Data(), delay: 0)
        Self.seen.append(request)
        Task { await RequestLatch.shared.signal() }
        let deliver = { [weak self] in
            guard let self, let client = self.client else { return }
            let response = HTTPURLResponse(url: url, statusCode: reply.status, httpVersion: "HTTP/1.1", headerFields: reply.headers)!
            client.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            if !reply.body.isEmpty { client.urlProtocol(self, didLoad: reply.body) }
            client.urlProtocolDidFinishLoading(self)
        }
        if reply.delay > 0 { DispatchQueue.global().asyncAfter(deadline: .now() + reply.delay, execute: deliver) }
        else { DispatchQueue.global().async(execute: deliver) }
    }
    override func stopLoading() {}
}

actor RequestLatch {
    static let shared = RequestLatch()
    private var count = 0
    private var waiters: [(Int, CheckedContinuation<Void, Never>)] = []
    func current() -> Int { count }
    func signal() {
        count += 1
        let ready = waiters.filter { $0.0 <= count }
        waiters.removeAll { $0.0 <= count }
        ready.forEach { $0.1.resume() }
    }
    func wait(for minimum: Int) async {
        if count >= minimum { return }
        await withCheckedContinuation { continuation in waiters.append((minimum, continuation)) }
    }
}

var checks = 0
func check(_ value: @autoclosure () throws -> Bool, _ name: String) throws {
    guard try value() else { fatalError("FAIL: \(name)") }
    checks += 1
}
func info(_ instance: String, origin: String = "https://roboparktest.ru.tuna.am") throws -> (ServerOrigin, ServerInfo) {
    let server = try ServerOrigin(origin)
    let value: [String: Any] = ["signed_profile": ["payload": "test", "signature": "test"],
        "environment": "test", "instance_id": instance, "protocol": 1]
    return (server, try JSONDecoder().decode(ServerInfo.self, from: JSONSerialization.data(withJSONObject: value)))
}
func local(_ method: String, _ target: String, body: String = "") throws -> LocalHTTPRequest {
    let wire = "\(method) \(target) HTTP/1.1\r\nHost: 127.0.0.1:18444\r\nContent-Length: \(body.utf8.count)\r\n\r\n\(body)"
    return try LocalHTTPRequest.parse(Data(wire.utf8))!
}
func testTransport() -> URLSession {
    let config = URLSessionConfiguration.ephemeral
    config.httpCookieStorage = nil; config.httpShouldSetCookies = false; config.urlCache = nil
    config.protocolClasses = [MockURLProtocol.self]
    return URLSession(configuration: config, delegate: Network.policy, delegateQueue: nil)
}

@main struct Run {
    static func main() async throws {
        let instanceA = "d6ed3b43-c41b-4614-833e-c5ec30936ef3"
        let instanceB = "22222222-2222-4222-8222-222222222222"
        let (originA, infoA) = try info(instanceA)
        let (_, infoB) = try info(instanceB, origin: "https://second.example")

        SecureStore.reset(); MockURLProtocol.reset()
        let session = try WorkspaceSession(transport: testTransport())
        try session.configure(origin: originA, info: infoA)
        MockURLProtocol.replies["GET /api/auth/me?scope=desktop"] = .init(status: 200,
            headers: ["Set-Cookie": "native_session=alpha; Path=/; Secure; HttpOnly", "Content-Type": "application/json"],
            body: Data("{}".utf8), delay: 0)
        _ = try await session.request(target: "/api/auth/me?scope=desktop", method: "GET",
                                      headers: ["authorization": "attacker", "cookie": "attacker=1"], body: Data())
        try check(session.profile?.cookies.count == 1 && session.profile?.cookies.first?.name == "native_session", "secure server cookie retained only in native profile")
        try check(MockURLProtocol.seen.last?.url?.absoluteString == "https://roboparktest.ru.tuna.am/api/auth/me?scope=desktop", "relative API path and query preserved")
        try check(MockURLProtocol.seen.last?.value(forHTTPHeaderField: "Authorization") == nil && MockURLProtocol.seen.last?.value(forHTTPHeaderField: "Cookie") == nil, "browser supplied credentials are rejected")
        try check(MockURLProtocol.seen.last?.value(forHTTPHeaderField: "Origin") == "https://roboparktest.ru.tuna.am", "native origin is set")
        try check(SecureStore.values["workspace-profile-v1"] != nil, "cookie profile persisted")

        let restored = try WorkspaceSession(transport: testTransport())
        var restoredIdentity = 0
        restored.onIdentity = { user in if user["id"] as? Int == 7 { restoredIdentity += 1 } }
        MockURLProtocol.replies["GET /api/auth/me?"] = .init(status: 200, headers: [:],
            body: Data("{\"id\":7,\"access_status\":\"approved\",\"permissions\":[\"nav.emergency\"]}".utf8), delay: 0)
        let didRestoreIdentity = await restored.restoreIdentity()
        try check(didRestoreIdentity && restoredIdentity == 1, "authenticated restart restores native identity before pool activation")
        MockURLProtocol.replies["GET /api/tasks?"] = .init(status: 200, headers: [:], body: Data("[]".utf8), delay: 0)
        _ = try await restored.request(target: "/api/tasks", method: "GET", headers: [:], body: Data())
        try check(MockURLProtocol.seen.last?.value(forHTTPHeaderField: "Cookie")?.hasPrefix("native_session=") == true, "persisted cookie restored natively")

        var logoutCalls = 0
        session.onLogout = { logoutCalls += 1 }
        MockURLProtocol.replies["POST /api/auth/login?"] = .init(status: 200,
            headers: ["Set-Cookie": "native_session=beta; Path=/; Secure; HttpOnly", "Content-Type": "application/json"], body: Data("{}".utf8), delay: 0)
        let login = try await session.proxy(local("POST", "/api/auth/%6cogin", body: "{}"))
        try check(login.status == 200 && login.headers["Set-Cookie"] == nil, "encoded login has native lifecycle and never leaks cookie")
        try check(logoutCalls == 1 && session.profile?.cookies.count == 1, "login clears old session before native replacement")
        MockURLProtocol.replies["POST /api/auth/logout?"] = .init(status: 204, headers: [:], body: Data(), delay: 0)
        let logout = try await session.proxy(local("POST", "/api/auth/%6cogout", body: "{}"))
        try check(logout.status == 204 && session.profile?.cookies.isEmpty == true && SecureStore.isSignedOut("workspace"), "logout clears native session")
        let signedOutRestart = try WorkspaceSession(transport: testTransport())
        let didRestoreSignedOut = await signedOutRestart.restoreIdentity()
        try check(!didRestoreSignedOut, "logout restart remains outside the worker pool")

        // A delayed pre-logout result must never write a cookie after clear().
        MockURLProtocol.replies["GET /api/slow?"] = .init(status: 200,
            headers: ["Set-Cookie": "late=ignored; Path=/; Secure; HttpOnly"], body: Data("{}".utf8), delay: 0.15)
        let beforeSlowRequest = await RequestLatch.shared.current()
        let old = Task { try await restored.request(target: "/api/slow", method: "GET", headers: [:], body: Data()) }
        await RequestLatch.shared.wait(for: beforeSlowRequest + 1); try restored.clear()
        do { _ = try await old.value; fatalError("FAIL: stale response was accepted") } catch { checks += 1 }
        try check(restored.profile == nil && SecureStore.values["workspace-profile-v1"] == nil, "generation fence prevents late cookie resurrection")

        // A failed Keychain deletion leaves the explicit tombstone, so the next
        // process cannot send the old corporate session even if bytes remain.
        let afterClear = try WorkspaceSession(transport: testTransport()); try afterClear.configure(origin: originA, info: infoA)
        MockURLProtocol.replies["GET /api/seed?"] = .init(status: 200,
            headers: ["Set-Cookie": "seed=value; Path=/; Secure; HttpOnly"], body: Data(), delay: 0)
        _ = try await afterClear.request(target: "/api/seed", method: "GET", headers: [:], body: Data())
        SecureStore.failDelete = true
        do { try afterClear.clear(); fatalError("FAIL: simulated delete failure did not surface") } catch { checks += 1 }
        SecureStore.failDelete = false
        let recovered = try WorkspaceSession(transport: testTransport())
        try check(recovered.profile?.cookies.isEmpty == true && recovered.profile?.accountID == nil, "tombstone suppresses stale Keychain session after deletion failure")

        // This test-only build must never reactivate a persisted production profile.
        do { try recovered.configure(origin: try ServerOrigin("https://second.example"), info: infoB); fatalError("FAIL: production assignment accepted") }
        catch { checks += 1 }
        try check(recovered.profile?.origin != "https://second.example", "production assignment is quarantined before network use")
        let persistedProduction = WorkspaceProfile(origin: "https://prod.example", instanceID: instanceB,
            environment: "production", signedProfile: infoB.signed_profile,
            cookies: [WorkspaceCookie(name: "session", value: "secret", path: "/", expires: nil)], accountID: 7)
        SecureStore.values["workspace-profile-v1"] = try JSONEncoder().encode(persistedProduction)
        SecureStore.markSignedOut("workspace", false)
        let beforeQuarantine = MockURLProtocol.seen.count
        let quarantined = try WorkspaceSession(transport: testTransport())
        let restoredProduction = await quarantined.restoreIdentity()
        try check(!restoredProduction && quarantined.profile == nil && MockURLProtocol.seen.count == beforeQuarantine,
                  "persisted production profile cannot make a request in the test app")
        MockURLProtocol.replies["GET /api/oversize?"] = .init(status: 200,
            headers: ["Content-Length": "33554433"], body: Data(), delay: 0)
        do { _ = try await recovered.request(target: "/api/oversize", method: "GET", headers: [:], body: Data()); fatalError("FAIL: oversized response accepted") } catch { checks += 1 }
        try check(recovered.profile?.cookies.isEmpty == true, "oversized response cannot mutate session")
        print("PASS: \(checks) production WorkspaceSession lifecycle, cookie isolation and generation checks")
    }
}
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--keep", action="store_true", help="keep the generated package for debugging"
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="robopark-workspace-session-") as temporary:
        base = Path(temporary)
        (base / "Sources" / "AgentCore").mkdir(parents=True)
        (base / "Sources" / "WorkspaceSessionChecks").mkdir(parents=True)
        shutil.copytree(
            MACOS / "Sources" / "AgentCore",
            base / "Sources" / "AgentCore",
            dirs_exist_ok=True,
        )
        shutil.copy2(
            MACOS / "Sources" / "RoboparkAgent" / "WorkspaceSession.swift",
            base / "Sources" / "WorkspaceSessionChecks",
        )
        (base / "Package.swift").write_text(PACKAGE)
        (base / "Sources" / "WorkspaceSessionChecks" / "Shims.swift").write_text(SHIMS)
        (base / "Sources" / "WorkspaceSessionChecks" / "main.swift").write_text(MAIN)
        cache = Path("/private/tmp/robopark-workspace-session-checks")
        cache.mkdir(parents=True, exist_ok=True)
        command = [
            "swift",
            "run",
            "--package-path",
            str(base),
            "--scratch-path",
            str(cache),
            "--cache-path",
            str(cache / "cache"),
            "--config-path",
            str(cache / "config"),
            "--security-path",
            str(cache / "security"),
            "--disable-sandbox",
            "WorkspaceSessionChecks",
        ]
        result = subprocess.run(
            command,
            env={
                **__import__("os").environ,
                "CLANG_MODULE_CACHE_PATH": str(cache / "clang"),
                "SWIFTPM_MODULECACHE_OVERRIDE": str(cache / "modules"),
            },
            check=False,
        )
        if args.keep:
            destination = Path("/private/tmp/robopark-workspace-session-kept")
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(base, destination)
            print(f"kept generated test package at {destination}")
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
