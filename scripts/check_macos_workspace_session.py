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
import CryptoKit

final class MockURLProtocol: URLProtocol {
    struct Reply { let status: Int; let headers: [String: String]; let body: Data; let delay: TimeInterval; var chunkDelay: TimeInterval = 0 }
    static var replies: [String: Reply] = [:]
    static var seen: [URLRequest] = []
    static var afterRenewIdentity: Reply?
    private let stopLock = NSLock()
    private var stopped = false
    private var active: Bool { stopLock.lock(); defer { stopLock.unlock() }; return !stopped }
    static func reset() { replies = [:]; seen = []; afterRenewIdentity = nil }
    override class func canInit(with request: URLRequest) -> Bool { request.url?.scheme == "https" }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        guard let url = request.url else { return }
        let key = "\(request.httpMethod ?? "GET") \(url.path)?\(url.query ?? "")"
        let reply = Self.replies[key] ?? Reply(status: 404, headers: [:], body: Data(), delay: 0)
        if url.path == "/api/auth/device-auth/renew", reply.status == 204, let after = Self.afterRenewIdentity {
            Self.replies["GET /api/auth/me?"] = after
        }
        Self.seen.append(request)
        Task { await RequestLatch.shared.signal() }
        let deliver = { [weak self] in
            guard let self, self.active, let client = self.client else { return }
            let response = HTTPURLResponse(url: url, statusCode: reply.status, httpVersion: "HTTP/1.1", headerFields: reply.headers)!
            client.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            if reply.chunkDelay > 0 {
                for (index, byte) in reply.body.enumerated() {
                    DispatchQueue.global().asyncAfter(deadline: .now() + reply.chunkDelay * Double(index + 1)) { [weak self] in
                        guard let self, self.active, let client = self.client else { return }
                        client.urlProtocol(self, didLoad: Data([byte]))
                        if index == reply.body.count - 1 { client.urlProtocolDidFinishLoading(self) }
                    }
                }
                return
            }
            if !reply.body.isEmpty { client.urlProtocol(self, didLoad: reply.body) }
            client.urlProtocolDidFinishLoading(self)
        }
        if reply.delay > 0 { DispatchQueue.global().asyncAfter(deadline: .now() + reply.delay, execute: deliver) }
        else { DispatchQueue.global().async(execute: deliver) }
    }
    override func stopLoading() { stopLock.lock(); stopped = true; stopLock.unlock() }
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
        let lastIdentitySuccess = restored.lastSuccessAt
        for status in [408, 425, 429, 503] {
            MockURLProtocol.replies["GET /api/auth/me?"] = .init(status: status, headers: [:], body: Data("{}".utf8), delay: 0)
            let recovered = await restored.restoreIdentity()
            try check(!recovered && restored.hasStoredIdentity && restored.connectionState == "error"
                      && restored.lastSuccessAt == lastIdentitySuccess,
                      "transient identity failure retains the account without reporting a healthy connection")
        }
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
        let loginRequest = MockURLProtocol.seen.last(where: { $0.url?.path == "/api/auth/login" })
        try check(loginRequest?.timeoutInterval == 10, "login request has a bounded ten-second timeout")
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

        // The server-derived device owner survives logout and process restart.
        // A login cookie is held until /auth/me proves that the same owner is
        // using this installation; no other API is reachable in that window.
        SecureStore.reset(); MockURLProtocol.reset()
        let ownerSession = try WorkspaceSession(transport: testTransport())
        try ownerSession.configure(origin: originA, info: infoA)
        var registration = Registration(origin: originA.url.absoluteString,
            deviceID: "11111111-1111-4111-8111-111111111111", instanceID: instanceA,
            environment: "test", label: "Owner Mac", privateKey: Data(repeating: 1, count: 32))
        registration.ownerUserID = 7
        registration.signedProfile = infoA.signed_profile
        try ownerSession.useRegistration(registration)
        var acceptedIdentities = 0
        ownerSession.onIdentity = { _ in acceptedIdentities += 1 }
        MockURLProtocol.replies["POST /api/auth/login?"] = .init(status: 200,
            headers: ["Set-Cookie": "native_session=owner; Path=/; Secure; HttpOnly"],
            body: Data("{}".utf8), delay: 0)
        let ownerLogin = try await ownerSession.proxy(local("POST", "/api/auth/login", body: "{}"))
        try check(ownerLogin.status == 200 && ownerSession.profile?.accountID == nil,
                  "successful login waits for authoritative identity")
        let requestsBeforeVerification = MockURLProtocol.seen.count
        let earlyData = try await ownerSession.proxy(local("GET", "/api/tasks"))
        try check(earlyData.status == 403 && MockURLProtocol.seen.count == requestsBeforeVerification,
                  "unverified login cannot access workspace APIs")

        MockURLProtocol.replies["GET /api/auth/me?"] = .init(status: 200, headers: [:],
            body: Data("{\"id\":8,\"access_status\":\"approved\"}".utf8), delay: 0)
        let wrongOwner = try await ownerSession.proxy(local("GET", "/api/auth/me"))
        try check(wrongOwner.status == 403 && acceptedIdentities == 0,
                  "different account is rejected before application activation")
        try check(ownerSession.profile?.ownerUserID == 7 && ownerSession.profile?.accountID == nil
                  && ownerSession.profile?.cookies.isEmpty == true,
                  "owner mismatch clears the foreign session without rebinding the Mac")

        MockURLProtocol.replies["POST /api/auth/login?"] = .init(status: 200,
            headers: ["Set-Cookie": "native_session=owner-again; Path=/; Secure; HttpOnly"],
            body: Data("{}".utf8), delay: 0)
        _ = try await ownerSession.proxy(local("POST", "/api/auth/login", body: "{}"))
        MockURLProtocol.replies["GET /api/auth/me?"] = .init(status: 200, headers: [:],
            body: Data("{\"id\":7,\"access_status\":\"approved\"}".utf8), delay: 0)
        let correctOwner = try await ownerSession.proxy(local("GET", "/api/auth/me"))
        try check(correctOwner.status == 200 && acceptedIdentities == 1
                  && ownerSession.profile?.ownerUserID == 7 && ownerSession.profile?.accountID == 7,
                  "registered owner can sign in after a rejected account switch")

        let ownerRestart = try WorkspaceSession(transport: testTransport())
        MockURLProtocol.replies["GET /api/auth/me?"] = .init(status: 200, headers: [:],
            body: Data("{\"id\":7,\"access_status\":\"approved\"}".utf8), delay: 0)
        let ownerRestored = await ownerRestart.restoreIdentity()
        try check(ownerRestored
                  && ownerRestart.profile?.ownerUserID == 7 && ownerRestart.profile?.accountID == 7,
                  "persisted owner reconnects after application restart")
        MockURLProtocol.replies["POST /api/auth/logout?"] = .init(status: 204, headers: [:],
            body: Data(), delay: 0)
        _ = try await ownerRestart.proxy(local("POST", "/api/auth/logout", body: "{}"))
        let ownerSignedOutRestart = try WorkspaceSession(transport: testTransport())
        try check(ownerSignedOutRestart.profile?.ownerUserID == 7
                  && ownerSignedOutRestart.profile?.accountID == nil,
                  "logout preserves owner binding across restart")

        SecureStore.reset(); MockURLProtocol.reset()
        let legacyOwner = try WorkspaceSession(transport: testTransport())
        try legacyOwner.configure(origin: originA, info: infoA)
        MockURLProtocol.replies["POST /api/auth/login?"] = .init(status: 200,
            headers: ["Set-Cookie": "native_session=legacy; Path=/; Secure; HttpOnly"],
            body: Data("{}".utf8), delay: 0)
        _ = try await legacyOwner.proxy(local("POST", "/api/auth/login", body: "{}"))
        MockURLProtocol.replies["GET /api/auth/me?"] = .init(status: 200, headers: [:],
            body: Data("{\"id\":7,\"access_status\":\"approved\"}".utf8), delay: 0)
        _ = try await legacyOwner.proxy(local("GET", "/api/auth/me"))
        let requestsBeforeOwnerClaim = MockURLProtocol.seen.count
        let legacyEarlyData = try await legacyOwner.proxy(local("GET", "/api/tasks"))
        try check(legacyEarlyData.status == 403 && MockURLProtocol.seen.count == requestsBeforeOwnerClaim,
                  "legacy installation cannot use workspace APIs before server claim resolves owner")
        try legacyOwner.useRegistration(registration)
        MockURLProtocol.replies["GET /api/tasks?"] = .init(status: 200, headers: [:],
            body: Data("[]".utf8), delay: 0)
        let legacyAfterClaim = try await legacyOwner.proxy(local("GET", "/api/tasks"))
        try check(legacyAfterClaim.status == 200,
                  "server-derived claim unlocks legacy installation for its owner")
        try check(legacyOwner.lastSuccessAt != nil && legacyOwner.connectionState == "connected",
                  "connection status is based on actual upstream response")
        var invalidations = 0
        legacyOwner.onSessionInvalidated = { invalidations += 1 }
        MockURLProtocol.replies["GET /api/auth/me?"] = .init(status: 401, headers: [:],
            body: Data("{}".utf8), delay: 0)
        let expired = try await legacyOwner.proxy(local("GET", "/api/auth/me"))
        try check(expired.status == 401 && invalidations == 1 && legacyOwner.profile?.accountID == nil
                  && legacyOwner.profile?.cookies.isEmpty == true && SecureStore.isSignedOut("workspace"),
                  "expired upstream identity revokes browser and native credentials")
        let lastHealthyAt = legacyOwner.lastSuccessAt
        MockURLProtocol.replies["GET /api/health/ready?"] = .init(status: 503, headers: [:],
            body: Data("{}".utf8), delay: 0)
        _ = try await legacyOwner.call(path: "/api/health/ready")
        try check(legacyOwner.connectionState == "error", "HTTP failure is not reported as healthy connection")
        try check(legacyOwner.lastSuccessAt == lastHealthyAt, "HTTP 503 does not overwrite the last successful connection timestamp")
        MockURLProtocol.replies["GET /api/robots/missing?"] = .init(status: 404,
            headers: ["Content-Type": "application/json"],
            body: Data("{\"detail\":\"not_found\"}".utf8), delay: 0)
        let apiMissing = try await legacyOwner.proxy(local("GET", "/api/robots/missing"))
        try check(apiMissing.status == 404 && legacyOwner.connectionState == "connected",
                  "JSON API 404 remains an authoritative not-found response")
        let jsonNotFoundAt = legacyOwner.lastSuccessAt
        MockURLProtocol.replies["GET /api/robots/tunnel-missing?"] = .init(status: 404,
            headers: ["Content-Type": "text/html; charset=utf-8"],
            body: Data("<html><body>Tuna Tunnel not found</body></html>".utf8), delay: 0)
        let tunnelMissing = try await legacyOwner.proxy(local("GET", "/api/robots/tunnel-missing"))
        try check(tunnelMissing.status == 503 && legacyOwner.connectionState == "offline",
                  "HTML reverse-proxy 404 is exposed as retryable transport unavailability")
        try check(legacyOwner.lastSuccessAt == jsonNotFoundAt,
                  "HTML reverse-proxy 404 does not refresh the last successful connection timestamp")

        // A registered device proves its own key to renew; no password is stored
        // or replayed by this test or the production client.
        func renewalFixture(challengeStatus: Int = 200, renewStatus: Int = 204,
                            renewedOwner: Int = 7, emptyJar: Bool = false) throws -> WorkspaceSession {
            SecureStore.reset(); MockURLProtocol.reset()
            let seed = WorkspaceProfile(origin: registration.origin, instanceID: instanceA, environment: "test",
                signedProfile: infoA.signed_profile,
                cookies: emptyJar ? [] : [WorkspaceCookie(name: "native_session", value: "expired-fixture", path: "/", expires: nil)],
                ownerUserID: 7, accountID: 7)
            SecureStore.values["workspace-profile-v1"] = try JSONEncoder().encode(seed)
            let current = try WorkspaceSession(transport: testTransport())
            try current.useRegistration(registration)
            let expires = DateFormatter(); expires.locale = Locale(identifier: "en_US_POSIX")
            expires.timeZone = TimeZone(secondsFromGMT: 0); expires.dateFormat = "yyyy-MM-dd'T'HH:mm:ss.SSSSSS'Z'"
            let challenge: [String: Any] = ["challenge_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "nonce": String(repeating: "A", count: 43), "instance_id": instanceA, "environment": "test",
                "expires_at": expires.string(from: Date().addingTimeInterval(60))]
            MockURLProtocol.replies["GET /api/auth/me?"] = .init(status: 401, headers: [:], body: Data(), delay: 0)
            MockURLProtocol.replies["POST /api/auth/device-auth/challenge?"] = .init(status: challengeStatus, headers: [:],
                body: try JSONSerialization.data(withJSONObject: challenge), delay: 0.02)
            MockURLProtocol.replies["POST /api/auth/device-auth/renew?"] = .init(status: renewStatus,
                headers: renewStatus == 204 ? ["Set-Cookie": "native_session=renewed-fixture; Path=/; Secure; HttpOnly"] : [:],
                body: Data(), delay: 0)
            MockURLProtocol.afterRenewIdentity = .init(status: 200, headers: [:],
                body: try JSONSerialization.data(withJSONObject: ["id": renewedOwner]), delay: 0)
            return current
        }
        let renewedSession = try renewalFixture()
        let restoredByKey = await renewedSession.restoreIdentity()
        try check(restoredByKey && renewedSession.profile?.accountID == 7, "expired account session renews by the bound Mac key")
        try check(MockURLProtocol.seen.filter { $0.url?.path == "/api/auth/device-auth/challenge" }.count == 1
                  && MockURLProtocol.seen.filter { $0.url?.path == "/api/auth/device-auth/renew" }.count == 1
                  && !MockURLProtocol.seen.contains { $0.url?.path == "/api/auth/login" },
                  "renewal uses one challenge and never sends a password login")
        let emptyJarSession = try renewalFixture(emptyJar: true)
        let emptyJarRestored = await emptyJarSession.restoreIdentity()
        try check(emptyJarRestored && emptyJarSession.profile?.accountID == 7,
                  "restart after expired cookie deletion still renews by the bound device key")
        let concurrentRenewal = try renewalFixture()
        async let firstRenewal = concurrentRenewal.restoreIdentity()
        async let secondRenewal = concurrentRenewal.restoreIdentity()
        let bothRenewals = await (firstRenewal, secondRenewal)
        try check(bothRenewals.0 && bothRenewals.1
                  && MockURLProtocol.seen.filter { $0.url?.path == "/api/auth/device-auth/renew" }.count == 1,
                  "parallel expired requests share a single signed renewal")
        let offlineRenewal = try renewalFixture(challengeStatus: 503)
        let offlineRenewed = await offlineRenewal.restoreIdentity()
        try check(!offlineRenewed && offlineRenewal.profile?.accountID == 7 && !SecureStore.isSignedOut("workspace"),
                  "temporary renewal failure preserves local identity for offline work")
        let revokedRenewal = try renewalFixture(renewStatus: 403)
        let revokedRenewed = await revokedRenewal.restoreIdentity()
        try check(!revokedRenewed && revokedRenewal.profile?.accountID == nil && SecureStore.isSignedOut("workspace"),
                  "revoked device cannot renew and clears its local session")
        let wrongOwnerRenewal = try renewalFixture(renewedOwner: 8)
        let wrongOwnerRenewed = await wrongOwnerRenewal.restoreIdentity()
        try check(!wrongOwnerRenewed && wrongOwnerRenewal.profile?.accountID == nil && SecureStore.isSignedOut("workspace"),
                  "renewal cannot change the immutable Mac owner")
        let manualLogoutRenewal = try renewalFixture()
        SecureStore.markSignedOut("workspace", true)
        let manualLogoutRestored = await manualLogoutRenewal.restoreIdentity()
        try check(!manualLogoutRestored && MockURLProtocol.seen.isEmpty,
                  "explicit logout is never silently undone by a device key")
        let pendingRenewal = try renewalFixture()
        let beforeRenewal = await RequestLatch.shared.current()
        let pendingRestore = Task { await pendingRenewal.restoreIdentity() }
        await RequestLatch.shared.wait(for: beforeRenewal + 2)
        try pendingRenewal.clear()
        let staleRenewed = await pendingRestore.value
        try check(!staleRenewed && pendingRenewal.profile == nil
                  && !MockURLProtocol.seen.contains { $0.url?.path == "/api/auth/device-auth/renew" },
                  "logout while challenge is pending prevents a late signed renewal")
        SecureStore.reset(); MockURLProtocol.reset()
        let deadlineSession = try WorkspaceSession(transport: testTransport())
        try deadlineSession.configure(origin: originA, info: infoA)
        MockURLProtocol.replies["GET /api/slow?"] = .init(status: 200, headers: [:],
            body: Data(repeating: 65, count: 20), delay: 0, chunkDelay: 0.2)
        let deadlineStarted = Date()
        do {
            _ = try await deadlineSession.request(target: "/api/slow", method: "GET", headers: [:], body: Data(), timeout: 1)
            fatalError("FAIL: stalled upstream exceeded the workspace deadline")
        } catch {
            try check(error is AsyncDeadlineError || (error as? URLError)?.code == .timedOut, "deadline reports a timeout, not an unrelated failure")
            try check(Date().timeIntervalSince(deadlineStarted) < 2, "workspace deadline also bounds continuously trickling bodies")
        }
        MockURLProtocol.replies["GET /api/fast?"] = .init(status: 200, headers: [:], body: Data("ok".utf8), delay: 0)
        let fastResult = try await deadlineSession.request(target: "/api/fast", method: "GET", headers: [:], body: Data())
        try check(fastResult.1.statusCode == 200 && fastResult.0 == Data("ok".utf8), "timeout does not invalidate shared HTTP transport")

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
