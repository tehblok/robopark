// These executable checks need only Apple's Command Line Tools, not Xcode/XCTest.
import Foundation
import CryptoKit
import AgentCore

var checks = 0
func check(_ value: @autoclosure () throws -> Bool, _ name: String) throws {
    guard try value() else { fatalError("FAIL: \(name)") }; checks += 1
}
func rejects(_ name: String, _ body: () throws -> Void) {
    do { try body() } catch { checks += 1; return }; fatalError("FAIL: \(name)")
}

for origin in ["http://example.com", "https://u:p@example.com", "https://example.com/api", "https://example.com?q=1", "https://example.com/#x"] {
    rejects("invalid origin") { _ = try ServerOrigin(origin) }
}
let origin = try ServerOrigin("https://example.com/")
try check(origin.api("agent-devices/info").absoluteString == "https://example.com/api/agent-devices/info", "API URL")
try check(origin.webSocket.absoluteString == "wss://example.com/api/emergency/bridge/ws", "WS URL")
try check(origin.computeWebSocket.absoluteString == "wss://example.com/api/emergency/bridge/compute/ws", "compute WS URL")
let key = P256.Signing.PrivateKey()
let c = Challenge(type: "challenge", nonce: "nonce-123", environment: "test", instance_id: "instance", protocolVersion: 1)
let signature = try c.sign(key: key, environment: "test", instanceID: "instance")
try check(key.publicKey.isValidSignature(try P256.Signing.ECDSASignature(derRepresentation: signature),
            for: Data("robopark-bridge-v1\ninstance\ntest\nnonce-123".utf8)), "Python-compatible DER challenge")
let computeSignature = try c.signCompute(key: key, environment: "test", instanceID: "instance")
try check(key.publicKey.isValidSignature(try P256.Signing.ECDSASignature(derRepresentation: computeSignature),
            for: Data("robopark-compute-v1\ninstance\ntest\nnonce-123".utf8)), "compute challenge has an independent signing domain")
rejects("wrong environment") { _ = try c.sign(key: key, environment: "production", instanceID: "instance") }
rejects("wrong instance") { _ = try c.sign(key: key, environment: "test", instanceID: "other") }
let unboundOwner = DeviceOwnerBinding(ownerUserID: nil, activeUserID: nil)
let legacyLogin = try unboundOwner.acceptingAuthenticatedUser(7)
try check(legacyLogin.ownerUserID == nil && legacyLogin.activeUserID == 7,
          "legacy Mac may authenticate only long enough to resolve its owner from claim-auth")
rejects("legacy session cannot switch account before authoritative owner claim") {
    _ = try legacyLogin.acceptingAuthenticatedUser(8)
}
let claimedOwner = try legacyLogin.bindingAuthoritativeOwner(7)
try check(claimedOwner.ownerUserID == 7 && claimedOwner.activeUserID == 7,
          "server claim permanently binds the Mac to its authenticated owner")
rejects("claim cannot bind a different owner than the authenticated account") {
    _ = try legacyLogin.bindingAuthoritativeOwner(8)
}
rejects("another account cannot sign into an owner-bound Mac") {
    _ = try claimedOwner.acceptingAuthenticatedUser(8)
}
let signedOutOwner = claimedOwner.signedOut()
try check(signedOutOwner.ownerUserID == 7 && signedOutOwner.activeUserID == nil,
          "logout preserves permanent owner binding")
let persistedOwner = try JSONDecoder().decode(DeviceOwnerBinding.self,
    from: JSONEncoder().encode(signedOutOwner))
try check(try persistedOwner.acceptingAuthenticatedUser(7) == claimedOwner,
          "persisted owner may reconnect after application restart")
let ownerClaim = try JSONDecoder().decode(AuthenticatedDeviceClaim.self, from: Data("""
    {"device_id":"11111111-1111-4111-8111-111111111111","owner_user_id":7,"status":"approved",
     "instance_id":"instance","environment":"test"}
    """.utf8))
try ownerClaim.validate(expectedOwnerUserID: 7, existingDeviceID: nil)
checks += 1
rejects("claim response owner must match the authenticated workspace owner") {
    try ownerClaim.validate(expectedOwnerUserID: 8, existingDeviceID: nil)
}
rejects("claim response cannot replace an installed device identity") {
    try ownerClaim.validate(expectedOwnerUserID: 7,
                            existingDeviceID: "22222222-2222-4222-8222-222222222222")
}
rejects("claim response without an authoritative owner is rejected") {
    _ = try JSONDecoder().decode(AuthenticatedDeviceClaim.self, from: Data("""
        {"device_id":"11111111-1111-4111-8111-111111111111","status":"approved",
         "instance_id":"instance","environment":"test"}
        """.utf8))
}
let oauthState = "0123456789abcdef0123456789abcdef"
var deviceSessionFixture = Registration(origin: "https://roboparktest.ru.tuna.am",
    deviceID: "11111111-1111-4111-8111-111111111111", instanceID: "22222222-2222-4222-8222-222222222222",
    environment: "test", label: "Fixture", privateKey: Data(repeating: 1, count: 32))
deviceSessionFixture.ownerUserID = 7
let deviceChallengeObject: [String: String] = ["challenge_id": "33333333-3333-4333-8333-333333333333",
    "nonce": String(repeating: "A", count: 43), "instance_id": deviceSessionFixture.instanceID,
    "environment": "test", "expires_at": "2026-10-16T00:00:30.000000Z"]
let deviceChallengeNow = ISO8601DateFormatter().date(from: "2026-10-16T00:00:00Z")!
let deviceChallenge = try JSONDecoder().decode(DeviceSessionChallenge.self,
    from: JSONSerialization.data(withJSONObject: deviceChallengeObject))
let deviceSignature = try deviceChallenge.signature(for: deviceSessionFixture, now: deviceChallengeNow)
let deviceSignedBytes = Data(["robopark-device-auth-v1", deviceSessionFixture.instanceID, "test", deviceSessionFixture.deviceID,
    deviceChallengeObject["challenge_id"]!, deviceChallengeObject["nonce"]!, deviceChallengeObject["expires_at"]!].joined(separator: "\n").utf8)
try check(try P256.Signing.PrivateKey(rawRepresentation: deviceSessionFixture.privateKey).publicKey.isValidSignature(
    P256.Signing.ECDSASignature(derRepresentation: Data(base64Encoded: deviceSignature)!), for: deviceSignedBytes),
    "device renewal signs the exact domain-separated owner/device/instance challenge")
for (key, value) in [("instance_id", "foreign"), ("environment", "production"), ("nonce", "invalid\nvalue"),
                     ("challenge_id", "bad"), ("expires_at", "2026-10-15T23:59:30.000000Z"),
                     ("expires_at", "2026-10-16T00:10:00.000000Z")] {
    var bad = deviceChallengeObject; bad[key] = value
    rejects("invalid or stale device challenge is never signed") {
        let parsed = try JSONDecoder().decode(DeviceSessionChallenge.self, from: JSONSerialization.data(withJSONObject: bad))
        _ = try parsed.signature(for: deviceSessionFixture, now: deviceChallengeNow)
    }
}
try check(CorporateLoginPresentation.failureMessage(NSError(domain: NSURLErrorDomain, code: NSURLErrorCancelled)) == nil,
          "expected navigation cancellation is not a failed sign in")
let timeoutMessage = CorporateLoginPresentation.failureMessage(NSError(domain: NSURLErrorDomain, code: NSURLErrorTimedOut,
    userInfo: [NSLocalizedDescriptionKey: "secret-token", NSURLErrorFailingURLStringErrorKey: "https://passport.yandex-team.ru/?token=secret-token"]))!
try check(timeoutMessage.contains("время") && !timeoutMessage.contains("secret-token"),
          "auth failure diagnosis excludes error descriptions and credential URLs")
try check(CorporateLoginPresentation.allowsBlankSubframe(URL(string: "about:blank")!, isMainFrame: false,
    sourceProtocol: "https", sourceHost: CorporateAuthPolicy.passportHost, sourcePort: 443),
          "approved login page can create its blank child frame")
try check(!CorporateLoginPresentation.allowsBlankSubframe(URL(string: "about:blank")!, isMainFrame: true,
    sourceProtocol: "https", sourceHost: CorporateAuthPolicy.passportHost, sourcePort: 443),
          "top-level blank navigation cannot replace sign in")
try check(!CorporateLoginPresentation.allowsBlankSubframe(URL(string: "about:blank")!, isMainFrame: false,
    sourceProtocol: "https", sourceHost: "attacker.example", sourcePort: 443),
          "foreign blank frames do not acquire corporate trust")
try check(CorporateLoginPresentation.allowsPopup(URL(string: "about:blank")!,
    sourceProtocol: "https", sourceHost: CorporateAuthPolicy.passportHost, sourcePort: 443),
          "approved Passport opener may create a real blank popup")
try check(!CorporateLoginPresentation.allowsPopup(URL(string: "about:blank")!,
    sourceProtocol: "http", sourceHost: CorporateAuthPolicy.passportHost, sourcePort: 80),
          "insecure opener cannot create a corporate popup")
try check(!CorporateLoginPresentation.allowsPopup(URL(string: "https://attacker.example")!,
    sourceProtocol: "https", sourceHost: CorporateAuthPolicy.passportHost, sourcePort: 443),
          "approved opener cannot send popup to unapproved host")
for host in ["guard.oauth.yandex-team.ru", "sso.passport.yandex-team.ru", "new-auth.yandex-team.ru", "yandex-team.ru"] {
    try check(LoginPolicy.allows(URL(string: "https://\(host)/auth")!),
        "corporate SSO may add HTTPS steps without a per-host application update")
}
for invalid in ["http://guard.oauth.yandex-team.ru/auth", "https://yandex-team.ru.attacker.example/",
                "https://fake-yandex-team.ru/", "https://user:password@guard.oauth.yandex-team.ru/",
                "https://guard.oauth.yandex-team.ru:8443/", "file:///tmp/login", "javascript:alert(1)"] {
    try check(!LoginPolicy.allows(URL(string: invalid)!), "corporate navigation boundary rejects lookalike and unsafe origins")
}
try check(CorporateLoginPresentation.allowsBlankSubframe(URL(string: "about:srcdoc")!, isMainFrame: false,
    sourceProtocol: "https", sourceHost: CorporateAuthPolicy.passportHost, sourcePort: 443),
    "Passport can render its inline challenge frame")
try check(!CorporateLoginPresentation.allowsBlankSubframe(URL(string: "about:srcdoc")!, isMainFrame: true,
    sourceProtocol: "https", sourceHost: CorporateAuthPolicy.passportHost, sourcePort: 443),
    "inline content may not replace the top-level authentication page")
try check(CorporateLoginPresentation.allowsPopup(URL(string: "https://guard.oauth.yandex-team.ru/auth")!,
    sourceProtocol: "https", sourceHost: "guard.oauth.yandex-team.ru", sourcePort: 443),
    "corporate guard can continue the approved SSO popup flow")
let diagnosticSecretURL = URL(string: "https://passport.yandex-team.ru/auth/user-validate?token=must-not-log#secret")!
let diagnostic = CorporateLoginDiagnostic.event(.blocked, url: diagnosticSecretURL, mainFrame: true)
try check(diagnostic["host"] as? String == "passport.yandex-team.ru"
    && diagnostic["route"] as? String == "challenge", "diagnostic identifies the provider stage")
let diagnosticJSON = String(data: try JSONSerialization.data(withJSONObject: diagnostic), encoding: .utf8)!
try check(!diagnosticJSON.contains("must-not-log") && !diagnosticJSON.contains("secret")
    && !diagnosticJSON.contains("token="), "diagnostic never retains query, fragment or arbitrary path")
try check(CorporateLoginDiagnostic.event(.blocked,
    url: URL(string: "https://user:secret@attacker.example/secret-token")!, mainFrame: true)["host"] as? String == "attacker.example",
    "blocked destination diagnostics retain only DNS host, never userinfo")
try check(CorporateLoginDiagnostic.event(.blocked,
    url: URL(string: "https://secret-token.yandex-team.ru/secret-token")!, mainFrame: true)["route"] as? String == "other",
    "unrecognized paths never appear in diagnostics")
try check(CorporateLoginDiagnostic.event(.blocked, url: URL(string: "about:blank")!, mainFrame: false)["route"] as? String == "blank",
    "blank frame failures can be distinguished from HTTP navigation")
let oauthCallback = URL(string: "https://oauth.yandex-team.ru/verification_code#access_token=token-123&expires_in=3600&state=\(oauthState)")!
let oauthCredential = try CorporateAuthPolicy.oauthCredential(from: oauthCallback,
                                                               expectedState: oauthState,
                                                               now: Date(timeIntervalSince1970: 1_000))
try check(oauthCredential.token == "token-123"
          && oauthCredential.expiresAt == Date(timeIntervalSince1970: 4_600),
          "exact OAuth callback yields a bounded expiring token")
for callback in [
    "https://attacker.example/verification_code#access_token=x&expires_in=3600&state=\(oauthState)",
    "https://oauth.yandex-team.ru/other#access_token=x&expires_in=3600&state=\(oauthState)",
    "https://oauth.yandex-team.ru/verification_code#access_token=x&expires_in=3600&state=wrong",
    "https://oauth.yandex-team.ru/verification_code#access_token=x&access_token=y&expires_in=3600&state=\(oauthState)",
] {
    rejects("forged or duplicate OAuth callback") {
        _ = try CorporateAuthPolicy.oauthCredential(from: URL(string: callback)!,
                                                     expectedState: oauthState,
                                                     now: Date(timeIntervalSince1970: 1_000))
    }
}
let sessionCandidate = CorporateCookieCandidate(name: "Session_id", value: "session-value",
    domain: ".yandex-team.ru", path: "/", secure: true,
    expires: Date(timeIntervalSince1970: 10_000))
try check(try CorporateAuthPolicy.sessionCookie(from: [sessionCandidate], now: Date(timeIntervalSince1970: 1_000))
          == "Session_id=session-value", "single parent-domain Session_id is accepted")
rejects("host-specific Session_id cannot shadow parent identity") {
    _ = try CorporateAuthPolicy.sessionCookie(from: [CorporateCookieCandidate(name: "Session_id", value: "x",
        domain: "emergency.sdc.yandex-team.ru", path: "/", secure: true, expires: nil)], now: Date())
}
rejects("duplicate Session_id cookies are rejected") {
    _ = try CorporateAuthPolicy.sessionCookie(from: [sessionCandidate, sessionCandidate], now: Date(timeIntervalSince1970: 1_000))
}
try check(CorporateAuthPolicy.identityStatus(httpStatus: 200,
    body: Data("{\"login\":\"Owner.Login\",\"dismissed\":false}".utf8), expectedLogin: "owner.login") == .ready,
    "verified corporate identity matches authoritative owner")
try check(CorporateAuthPolicy.identityStatus(httpStatus: 200,
    body: Data("{\"login\":\"other\",\"dismissed\":false}".utf8), expectedLogin: "owner.login") == .ownerMismatch,
    "wrong corporate owner fails closed")
try check(CorporateAuthPolicy.identityStatus(httpStatus: 200,
    body: Data("{\"login\":\"owner.login\",\"dismissed\":true}".utf8), expectedLogin: "owner.login") == .accessDenied,
    "dismissed corporate identity cannot become ready")
try check(CorporateAuthPolicy.identityStatus(httpStatus: 401, body: Data(), expectedLogin: "owner.login") == .reauthRequired,
    "401 requires reauthentication")
try check(CorporateAuthPolicy.identityStatus(httpStatus: 403, body: Data(), expectedLogin: "owner.login") == .accessDenied,
    "403 remains access denied")
try check(CorporateAuthPolicy.identityStatus(httpStatus: 500, body: Data(), expectedLogin: "owner.login") == .networkUnavailable,
    "upstream failure remains network unavailable")
try check(!oauthCredential.isUsable(now: Date(timeIntervalSince1970: 4_550))
          && oauthCredential.isUsable(now: Date(timeIntervalSince1970: 4_000)),
          "expired or nearly expired token is not reused")
func corporateEnvelope(service: String = "tracker", action: String = "issue_read",
                       resource: String = "TEST-1", params: [String: Any] = [:],
                       owner: Int = 7, login: String = "owner.login") throws -> Data {
    try JSONSerialization.data(withJSONObject: ["service": service, "action": action,
        "resource": resource, "params": params, "owner_user_id": owner,
        "owner_tracker_login": login, "requester_user_id": 7], options: [.sortedKeys])
}
let issueRequest = try CorporateReadPolicy.decode(try corporateEnvelope(), expectedOwnerUserID: 7,
                                                   expectedOwnerTrackerLogin: "Owner.Login")
let issuePlan = try CorporateReadPolicy.plan(issueRequest)
try check(issuePlan.method == "GET"
          && issuePlan.url.absoluteString == "https://st-api.yandex-team.ru/v2/issues/TEST-1",
          "typed Tracker issue request has a fixed HTTPS destination")
let userRequest = try CorporateReadPolicy.decode(try corporateEnvelope(action: "user_read", resource: "11291906"),
    expectedOwnerUserID: 7, expectedOwnerTrackerLogin: "owner.login")
let userPlan = try CorporateReadPolicy.plan(userRequest)
try check(userPlan.method == "GET"
          && userPlan.url.absoluteString == "https://st-api.yandex-team.ru/v2/users/11291906",
          "typed Tracker user reference has a fixed HTTPS destination")
let installations = try CorporateReadPolicy.decode(try corporateEnvelope(service: "inventory",
    action: "rover_installations", resource: "a664", params: ["offset": 2, "limit": 25, "slot": "/base/lidar"]),
    expectedOwnerUserID: 7, expectedOwnerTrackerLogin: "owner.login")
let installationsPlan = try CorporateReadPolicy.plan(installations)
let installationQuery = URLComponents(url: installationsPlan.url, resolvingAgainstBaseURL: false)?.queryItems ?? []
try check(installationsPlan.url.host == CorporateReadPolicy.inventoryHost
          && installationsPlan.url.absoluteString.hasPrefix("https://inventory.sdc.yandex-team.ru/api/v1/installations/")
          && installationQuery.contains(URLQueryItem(name: "rover", value: "a664")),
          "Inventory request cannot select a host or path")
for envelope in [
    try corporateEnvelope(owner: 8),
    try corporateEnvelope(login: "other.login"),
    try corporateEnvelope(action: "arbitrary_read"),
    try corporateEnvelope(action: "comments_read", resource: "https://evil.example/x"),
    try corporateEnvelope(action: "comments_read", resource: "TEST-1/comments"),
] {
    rejects("wrong owner, action, URL host or subresource is rejected") {
        _ = try CorporateReadPolicy.decode(envelope, expectedOwnerUserID: 7, expectedOwnerTrackerLogin: "owner.login")
    }
}
rejects("corporate request envelope is bounded") {
    _ = try CorporateReadPolicy.decode(Data(repeating: 0x20, count: CorporateReadPolicy.maxRequestBytes + 1),
        expectedOwnerUserID: 7, expectedOwnerTrackerLogin: "owner.login")
}
let countRequest = try CorporateReadPolicy.decode(try corporateEnvelope(action: "issues_count", resource: "count",
    params: ["query": "Queue: TEST"]), expectedOwnerUserID: 7, expectedOwnerTrackerLogin: "owner.login")
let normalizedCount = try CorporateReadPolicy.result(status: 200, data: Data("42".utf8), headers: [:], request: countRequest)
try check((normalizedCount["payload"] as? [String: Int])?["count"] == 42,
          "Tracker scalar count is normalized to a typed object")
let searchRequest = try CorporateReadPolicy.decode(try corporateEnvelope(action: "issues_search", resource: "search",
    params: ["query": "Queue: TEST", "order": ["-updatedAt"], "page": 2, "per_page": 25,
             "expand": "transitions", "fields": "key,summary"]),
    expectedOwnerUserID: 7, expectedOwnerTrackerLogin: "owner.login")
let searchPlan = try CorporateReadPolicy.plan(searchRequest)
let searchBody = try JSONSerialization.jsonObject(with: searchPlan.body!) as? [String: Any]
let searchQuery = URLComponents(url: searchPlan.url, resolvingAgainstBaseURL: false)?.queryItems ?? []
try check(Set(searchBody?.keys.map { $0 } ?? []) == Set(["query", "order"])
          && searchQuery.contains(URLQueryItem(name: "page", value: "2"))
          && searchQuery.contains(URLQueryItem(name: "perPage", value: "25"))
          && searchQuery.contains(URLQueryItem(name: "expand", value: "transitions"))
          && searchQuery.contains(URLQueryItem(name: "fields", value: "key,summary")),
          "Tracker search sends pagination and projections as URL query")
for flag in [false, true] {
    let request = try CorporateReadPolicy.decode(try corporateEnvelope(action: "issues_search", resource: "search",
        params: ["query": "Queue: TEST", "page": 2, "stale_ok": flag]),
        expectedOwnerUserID: 7, expectedOwnerTrackerLogin: "owner.login")
    let plan = try CorporateReadPolicy.plan(request)
    try check(URLComponents(url: plan.url, resolvingAgainstBaseURL: false)?.queryItems?.contains(
        URLQueryItem(name: "staleOk", value: flag ? "true" : "false")) == true,
        "Tracker next-page consistency hint preserves its Boolean wire value")
}
for invalidFlag: Any in [1, 0, "false", [true]] {
    rejects("Tracker stale_ok accepts only JSON Boolean") {
        _ = try CorporateReadPolicy.decode(try corporateEnvelope(action: "issues_search", resource: "search",
            params: ["query": "Queue: TEST", "stale_ok": invalidFlag]),
            expectedOwnerUserID: 7, expectedOwnerTrackerLogin: "owner.login")
    }
}
for (status, expected) in [(401, CorporateReadError.auth), (403, .accessDenied), (404, .notFound),
                           (429, .rateLimited), (500, .network)] {
    do { _ = try CorporateReadPolicy.result(status: status, data: Data("{}".utf8), headers: [:], request: issueRequest)
         fatalError("FAIL: corporate HTTP error \(status) accepted") }
    catch let error as CorporateReadError { try check(error == expected, "corporate HTTP status maps to stable wire error") }
}
rejects("corporate response is bounded") {
    _ = try CorporateReadPolicy.result(status: 200,
        data: Data(repeating: UInt8(ascii: "x"), count: CorporateReadPolicy.maxResponseBytes + 1),
        headers: [:], request: issueRequest)
}
try check(CorporateReadPolicy.admits(inflight: 0) && CorporateReadPolicy.admits(inflight: 1)
          && !CorporateReadPolicy.admits(inflight: 2),
          "corporate executor admits exactly two concurrent requests")
try check(try CorporateReadPolicy.inventoryRover(serverAuthorizedVIN: "YASADR00000000664") == "a664",
          "Inventory readiness uses only the server-authorized Emergency VIN")
func writeEnvelope(action: String = "comment_create", params: [String: Any] = ["text": "hello"],
                   owner: Int = 7, command: String = "11111111-1111-4111-8111-111111111111",
                   step: String = "comment:1") throws -> Data {
    try JSONSerialization.data(withJSONObject: ["service": "tracker", "action": action,
        "resource": "TEST-1", "params": params, "owner_user_id": owner,
        "owner_tracker_login": "owner.login", "requester_user_id": 7,
        "command_id": command, "step_id": step], options: [.sortedKeys])
}
let commentWrite = try CorporateWritePolicy.decode(try writeEnvelope(), expectedOwnerUserID: 7,
                                                     expectedOwnerTrackerLogin: "owner.login")
let commentPlan = try CorporateWritePolicy.plan(commentWrite)
try check(commentPlan.method == "POST" && commentPlan.url.absoluteString ==
          "https://st-api.yandex-team.ru/v2/issues/TEST-1/comments",
          "comment write has a fixed Tracker endpoint")
let patchWrite = try CorporateWritePolicy.decode(try writeEnvelope(action: "issue_patch",
    params: ["fields": ["assignee": NSNull(), "tags": ["robot"], "solutionMethod": "repair"], "version": "17"]),
    expectedOwnerUserID: 7, expectedOwnerTrackerLogin: "owner.login")
let patchPlan = try CorporateWritePolicy.plan(patchWrite)
try check(patchPlan.url.query == nil && patchPlan.ifMatch == "17",
          "issue patch sends version through the Tracker If-Match contract")
let attachmentWrite = try CorporateWritePolicy.decode(try writeEnvelope(action: "attachment_upload",
    params: ["filename": "photo.jpg", "content_type": "image/jpeg",
             "data_base64": Data("image".utf8).base64EncodedString()]),
    expectedOwnerUserID: 7, expectedOwnerTrackerLogin: "owner.login")
try check(try CorporateWritePolicy.plan(attachmentWrite).url.absoluteString ==
          "https://st-api.yandex-team.ru/v2/attachments/",
          "attachment upload uses Tracker temporary storage")
let numericAttachment = try CorporateWritePolicy.result(status: 201,
    data: Data("{\"id\":123}".utf8), request: attachmentWrite)
try check((numericAttachment["payload"] as? [String: Any])?["id"] as? String == "123",
          "numeric Tracker attachment ids are normalized before a durable success")
let longAttachment = try CorporateWritePolicy.result(status: 201,
    data: Data("{\"longId\":\"55\"}".utf8), request: attachmentWrite)
try check((longAttachment["payload"] as? [String: Any])?["id"] as? String == "55",
          "Tracker temporary attachment longId is normalized to the durable id contract")
let longComment = try CorporateWritePolicy.result(status: 201,
    data: Data("{\"longId\":\"56\"}".utf8), request: commentWrite)
try check((longComment["payload"] as? [String: Any])?["id"] as? String == "56",
          "Tracker comment longId is normalized after successful creation")
for bad in [
    try writeEnvelope(owner: 8), try writeEnvelope(action: "delete_issue"),
    try writeEnvelope(action: "issue_patch", params: ["fields": ["summary": "escape"]]),
    try writeEnvelope(step: "../other"),
] {
    rejects("write owner, action, fields and step are bounded") {
        _ = try CorporateWritePolicy.decode(bad, expectedOwnerUserID: 7, expectedOwnerTrackerLogin: "owner.login")
    }
}
let receipt = CorporateWriteReceipt(ownerUserID: 7, deviceID: "device", commandID: commentWrite.commandID,
    stepID: commentWrite.stepID, digest: String(repeating: "a", count: 64))
try receipt.validate(ownerUserID: 7, deviceID: "device", digest: String(repeating: "a", count: 64))
rejects("receipt cannot be reused with a different envelope") {
    try receipt.validate(ownerUserID: 7, deviceID: "device", digest: String(repeating: "b", count: 64))
}
var executingReceipt = receipt; executingReceipt.state = .executing
let reloadedExecuting = try JSONDecoder().decode(CorporateWriteReceipt.self,
    from: JSONEncoder().encode(executingReceipt))
try check(try reloadedExecuting.disposition(ownerUserID: 7, deviceID: "device",
    digest: String(repeating: "a", count: 64)) == .outcomeUnknown,
    "restart never resends an executing receipt")
let replayBytes = Data("{\"ok\":true}".utf8)
var completedReceipt = receipt; completedReceipt.state = .completed; completedReceipt.wireResult = replayBytes
try check(try completedReceipt.disposition(ownerUserID: 7, deviceID: "device",
    digest: String(repeating: "a", count: 64)) == .replay(replayBytes),
    "duplicate completed command replays its durable result")
var receiptIndex = CorporateWriteReceiptIndex()
for number in 0..<CorporateWriteReceiptIndex.capacity { try receiptIndex.reserve("receipt-\(number)") }
try receiptIndex.reserve("receipt-0")
rejects("receipt capacity uses backpressure and never evicts unknown outcomes") {
    try receiptIndex.reserve("receipt-over-capacity")
}
try check(completedReceipt.permitsAcknowledgement(ownerUserID: 7, deviceID: "device",
    commandID: receipt.commandID, stepID: receipt.stepID), "completed receipt accepts its exact durable ACK")
try check(!executingReceipt.permitsAcknowledgement(ownerUserID: 7, deviceID: "device",
    commandID: receipt.commandID, stepID: receipt.stepID), "executing receipt cannot be garbage collected")
var unknownReceipt = receipt; unknownReceipt.state = .unknown
try check(!unknownReceipt.permitsAcknowledgement(ownerUserID: 7, deviceID: "device",
    commandID: receipt.commandID, stepID: receipt.stepID), "unknown receipt survives server ACK")
try check(!completedReceipt.permitsAcknowledgement(ownerUserID: 8, deviceID: "device",
    commandID: receipt.commandID, stepID: receipt.stepID)
    && !completedReceipt.permitsAcknowledgement(ownerUserID: 7, deviceID: "other-device",
    commandID: receipt.commandID, stepID: receipt.stepID)
    && !completedReceipt.permitsAcknowledgement(ownerUserID: 7, deviceID: "device",
    commandID: UUID().uuidString.lowercased(), stepID: receipt.stepID), "foreign ACK cannot release a receipt")
for number in 0..<1000 {
    let account = "receipt-\(number)"
    receiptIndex.releaseAcknowledged(account)
    try receiptIndex.reserve("receipt-\(number + CorporateWriteReceiptIndex.capacity)")
}
try check(receiptIndex.accounts.count == CorporateWriteReceiptIndex.capacity,
          "ACK releases completed slots for more than 256 lifetime operations")
var writeAdmission = CorporateWriteAdmission()
try writeAdmission.acquire("attempt-one")
rejects("concurrent corporate write receives busy backpressure") { try writeAdmission.acquire("attempt-two") }
writeAdmission.release("attempt-one")
try writeAdmission.acquire("attempt-two")
try check(try EmergencyTarget.url(vin: "YASADR00000000664").absoluteString == "https://emergency.sdc.yandex-team.ru/api/v1/YASADR00000000664/", "fixed upstream")
for vin in ["664", "../admin", "https://evil.example", "YASADR00000000664?x", "YASADR１２３４５６７８９０１"] {
    rejects("noncanonical VIN") { _ = try EmergencyTarget.url(vin: vin) }
}
try check(try EmergencyTarget.normalizeRobot("A664") == "YASADR00000000664", "robot normalization")
let updateKey = Curve25519.Signing.PrivateKey()
let artifact = Data("test artifact".utf8)
let hash = SHA256.hash(data: artifact).map { String(format: "%02x", $0) }.joined()
let target: [String: Any] = ["version": "0.2.0-rc.44", "url": "https://releases.example/a.pkg", "sha256": hash,
    "length": artifact.count, "architecture": "arm64", "protocol": 1, "notes": "test"]
let catalog: [String: Any] = ["schema": 1, "generation": 10, "expires": 2000, "channels": ["pilot": target]]
let payload = try JSONSerialization.data(withJSONObject: catalog)
let signature64 = try updateKey.signature(for: payload).base64EncodedString()
let envelope = try JSONSerialization.data(withJSONObject: ["payload": payload.base64EncodedString(), "signature": signature64])
let verifier = UpdateVerifier(publicKey: updateKey.publicKey.rawRepresentation)
let verified = try verifier.verify(envelope, now: 1000, minimumGeneration: 10, channel: "pilot")
try check(verified.target.version == "0.2.0-rc.44", "signed channel assignment")
rejects("expired catalog") { _ = try verifier.verify(envelope, now: 2001, minimumGeneration: 0, channel: "pilot") }
rejects("rollback") { _ = try verifier.verify(envelope, now: 1000, minimumGeneration: 11, channel: "pilot") }
rejects("unknown channel") { _ = try verifier.verify(envelope, now: 1000, minimumGeneration: 0, channel: "missing") }
let forged = try JSONSerialization.data(withJSONObject: ["payload": Data("{}".utf8).base64EncodedString(), "signature": signature64])
rejects("tampered payload") { _ = try verifier.verify(forged, now: 1000, minimumGeneration: 0, channel: "pilot") }
try UpdateVerifier.checkArtifact(artifact, target: verified.target); checks += 1
rejects("tampered package") { try UpdateVerifier.checkArtifact(Data("other package".utf8), target: verified.target) }
rejects("truncated package") { try UpdateVerifier.checkArtifact(artifact.dropLast(), target: verified.target) }
try check(try ServerOrigin("https://EXAMPLE.com:443/") == origin, "canonical origin")
try check(try ReleaseVersion("0.2.0-rc.44.dev7114536505405942333") > ReleaseVersion("0.2.0-rc.44.dev7114536505405942332"), "monotonic large build")
try check(try ReleaseVersion("0.2.0") > ReleaseVersion("0.2.0-rc.44"), "stable after candidate")
try check(try ReleaseVersion("0.2.0-rc.44") > ReleaseVersion("0.2.0-rc.44.dev123"), "candidate after development")
rejects("unsupported release comparator") { _ = try ReleaseVersion("latest") }
let instance = "50b4ae12-c0d8-4c76-9639-2984f164408b"
let deployment: [String: Any] = ["purpose": "robopark-deployment-v1", "origin": "https://example.com",
    "instance_id": instance, "environment": "test", "channel": "rc", "expires": 2000]
let deploymentBytes = try JSONSerialization.data(withJSONObject: deployment)
let deploymentEnvelope = try JSONDecoder().decode(SignedCatalog.self, from: JSONSerialization.data(withJSONObject: [
    "payload": deploymentBytes.base64EncodedString(), "signature": updateKey.signature(for: deploymentBytes).base64EncodedString()]))
let trust = try DeploymentTrust.verify(deploymentEnvelope, publicKey: updateKey.publicKey.rawRepresentation,
    origin: origin, instanceID: instance, environment: "test", now: 1000)
try check(trust.channel == "rc", "signed server and channel")
rejects("attacker HTTPS origin") { _ = try DeploymentTrust.verify(deploymentEnvelope, publicKey: updateKey.publicKey.rawRepresentation,
    origin: ServerOrigin("https://attacker.example"), instanceID: instance, environment: "test", now: 1000) }
rejects("deployment environment mismatch") { _ = try DeploymentTrust.verify(deploymentEnvelope, publicKey: updateKey.publicKey.rawRepresentation,
    origin: origin, instanceID: instance, environment: "production", now: 1000) }
rejects("expired deployment") { _ = try DeploymentTrust.verify(deploymentEnvelope, publicKey: updateKey.publicKey.rawRepresentation,
    origin: origin, instanceID: instance, environment: "test", now: 2000) }
rejects("forged deployment root") { _ = try DeploymentTrust.verify(deploymentEnvelope, publicKey: Curve25519.Signing.PrivateKey().publicKey.rawRepresentation,
    origin: origin, instanceID: instance, environment: "test", now: 1000) }
var relativeTarget = target
relativeTarget["url"] = "/api/distribution/v1/macos/Robopark-0.2.0-rc.44-arm64.pkg"
let decodedTarget = try JSONDecoder().decode(ReleaseTarget.self, from: JSONSerialization.data(withJSONObject: relativeTarget))
try check(try decodedTarget.artifactURL(origin: origin).absoluteString == "https://example.com/api/distribution/v1/macos/Robopark-0.2.0-rc.44-arm64.pkg", "relative distribution path")
relativeTarget["url"] = "/api/distribution/v1/macos/../../evil.pkg"
let badTarget = try JSONDecoder().decode(ReleaseTarget.self, from: JSONSerialization.data(withJSONObject: relativeTarget))
rejects("artifact path traversal") { _ = try badTarget.artifactURL(origin: origin) }
var foreignTarget = target
foreignTarget["url"] = "https://attacker.example/Robopark.pkg"
let decodedForeignTarget = try JSONDecoder().decode(ReleaseTarget.self, from: JSONSerialization.data(withJSONObject: foreignTarget))
rejects("automatic update cannot leave fixed origin") { _ = try decodedForeignTarget.fixedArtifactURL(origin: origin) }
try check(try decodedTarget.fixedArtifactURL(origin: origin).host == "example.com", "automatic update remains on fixed origin")
let nominal = ResourceTelemetry(cpuPercent: 32, memoryPercent: 41, thermalState: "normal")
try check(nominal.capacity(base: 2, activeJobs: 0, ready: true, paused: false) == 2, "nominal Mac exposes bounded capacity")
try check(nominal.computeCapacity(activeJobs: 0, ready: true, paused: false, lowPower: false) == 1, "compute pool exposes one free-resource slot")
try check(ApplicationInstallPathPolicy.allowsApplicationsDirectory(owner: 0, group: 80, permissions: 0o775), "standard root:admin 0775 Applications directory is trusted")
try check(!ApplicationInstallPathPolicy.allowsApplicationsDirectory(owner: 0, group: 0, permissions: 0o775), "group-writable Applications requires the admin group")
try check(!ApplicationInstallPathPolicy.allowsApplicationsDirectory(owner: 0, group: 80, permissions: 0o777), "world-writable Applications is rejected")
let inbox = try NativeNotificationPolicy.parseInbox(JSONSerialization.data(withJSONObject: [
    ["id": 7, "event_type": "new_task", "protected_text": "Проверьте задачу", "read_at": NSNull(),
     "created_at": "2026-10-15T20:00:00Z"],
    ["id": "report-8", "event_type": "report", "protected_text": "Отчёт готов", "read_at": "2026-10-14T10:00:00Z",
     "created_at": "2026-10-15T19:00:00.123456+00:00"],
]))
try check(inbox.count == 2 && inbox[0].unread && !inbox[1].unread && inbox.allSatisfy { $0.createdAt != nil },
          "native inbox accepts all bounded server categories, read state, and creation time")
try check(NativeNotificationPolicy.title(for: "operator_comment") == "Комментарий оператора", "notification category has a fixed native title")
try check(NativeNotificationPolicy.title(for: "system_problem") == "Проблема системы" &&
          NativeNotificationPolicy.title(for: "network_problem") == "Проблема сети", "royal incident categories have distinct fixed titles")
let maintenance = try NativeNotificationPolicy.parseMaintenance(JSONSerialization.data(withJSONObject: ["active": true, "kind": "planned", "operator": "ops"]))
try check(maintenance.active && maintenance.kind == "planned", "bounded maintenance fallback is parsed")
var maintenanceEpisodes = NativeMaintenanceEpisodePolicy()
try check(maintenanceEpisodes.update(active: true, fingerprint: "same", makeEpisodeID: { "episode-1" }) == .started("episode-1"), "maintenance episode starts once")
try check(maintenanceEpisodes.update(active: true, fingerprint: "same", makeEpisodeID: { "unused" }) == .unchanged, "active maintenance polling is deduplicated")
try check(maintenanceEpisodes.update(active: false, fingerprint: "same") == .recovered("episode-1"), "maintenance recovery is emitted once")
try check(maintenanceEpisodes.update(active: true, fingerprint: "same", makeEpisodeID: { "episode-2" }) == .started("episode-2"), "same maintenance kind can start again inside the dedup TTL")
let unicodeInbox = try NativeNotificationPolicy.parseInbox(JSONSerialization.data(withJSONObject: [[
    "id": "announcement-emoji", "event_type": "announcement", "protected_text": String(repeating: "🛠", count: 1000),
    "created_at": "2026-10-15T20:00:00Z"
]]))
try check(unicodeInbox[0].protectedText.utf8.count == 4000, "native inbox accepts the server's 1000-Unicode-character text limit")
rejects("notification text bound") { _ = try NativeNotificationPolicy.parseInbox(JSONSerialization.data(withJSONObject: [["id": 1, "event_type": "task", "protected_text": String(repeating: "🛠", count: 1001)]])) }

let notificationNow = Date(timeIntervalSince1970: 1_760_556_600)
let notificationPlan = NativeNotificationPolicy.deliveryPlan(events: [
    NativeInboxEvent(id: "task-current", eventType: "new_task", protectedText: "Текущая задача", unread: true,
                     createdAt: notificationNow.addingTimeInterval(-60)),
    NativeInboxEvent(id: "report-current", eventType: "report", protectedText: "Текущий репорт", unread: true,
                     createdAt: notificationNow.addingTimeInterval(-120)),
    NativeInboxEvent(id: "health-current", eventType: "system_recovered", protectedText: "Восстановлено", unread: true,
                     createdAt: notificationNow.addingTimeInterval(-180)),
    NativeInboxEvent(id: "health-extra", eventType: "integration_down", protectedText: "Интеграция", unread: true,
                     createdAt: notificationNow.addingTimeInterval(-240)),
    NativeInboxEvent(id: "old-report-1", eventType: "report", protectedText: "Старый отчёт 1", unread: true,
                     createdAt: notificationNow.addingTimeInterval(-3_600)),
    NativeInboxEvent(id: "old-report-2", eventType: "operator_comment", protectedText: "Старый отчёт 2", unread: true,
                     createdAt: notificationNow.addingTimeInterval(-3_700)),
    NativeInboxEvent(id: "old-report-3", eventType: "return", protectedText: "Старый отчёт 3", unread: true,
                     createdAt: notificationNow.addingTimeInterval(-3_800)),
    NativeInboxEvent(id: "old-report-4", eventType: "review_task", protectedText: "Старый отчёт 4", unread: true,
                     createdAt: notificationNow.addingTimeInterval(-3_900)),
    NativeInboxEvent(id: "old-health", eventType: "server_problem", protectedText: "Старая авария", unread: true,
                     createdAt: notificationNow.addingTimeInterval(-4_000)),
], now: notificationNow)
try check(notificationPlan.deliver.map(\.id) == ["task-current", "report-current", "health-summary-health-current",
                                                   "old-report-1", "old-report-2", "old-report-3"],
          "native catch-up keeps every current work event but bounds current health and historical work alerts")
try check(Set(notificationPlan.suppress.map(\.id)) == Set(["health-current", "health-extra", "old-report-4", "old-health"]),
          "native catch-up suppresses the remaining backlog without removing it from the inbox")
try check(notificationPlan.deliver.contains { $0.eventType == "health_summary" && $0.protectedText.contains(": 2.") },
          "multiple current health changes produce a summary instead of hiding incidents behind recovery")
let maintenanceNow = NativeInboxEvent(id: "maintenance-current", eventType: "maintenance", protectedText: "Работы", unread: true,
                                    createdAt: notificationNow)
try check(NativeNotificationPolicy.deliveryPlan(events: [maintenanceNow], now: notificationNow).deliver == [maintenanceNow],
          "current maintenance remains visible with timestamped native fallback")

var notificationLedger = NativeNotificationReceiptLedger(data: nil, now: notificationNow)
try check(notificationLedger.reserve(accountID: 7, eventID: "event-1", reservationID: "run-a", now: notificationNow),
          "first native notification delivery reserves the account event")
try check(!notificationLedger.reserve(accountID: 7, eventID: "event-1", reservationID: "run-a", now: notificationNow),
          "same run cannot reserve an event twice while delivery is suspended")
notificationLedger.complete(accountID: 7, eventID: "event-1", reservationID: "run-a", now: notificationNow)
let restartedLedgerData = try notificationLedger.encoded()
var restartedLedger = NativeNotificationReceiptLedger(data: restartedLedgerData, now: notificationNow.addingTimeInterval(5))
try check(!restartedLedger.reserve(accountID: 7, eventID: "event-1", reservationID: "run-b",
                                     now: notificationNow.addingTimeInterval(5)),
          "completed native notification remains deduplicated after restart")
try check(restartedLedger.reserve(accountID: 8, eventID: "event-1", reservationID: "run-b",
                                    now: notificationNow.addingTimeInterval(5)),
          "native notification receipts remain isolated by account")
try check(restartedLedger.reserve(accountID: 7, eventID: "event-retry", reservationID: "run-a", now: notificationNow),
          "failed delivery test obtains a durable reservation")
let interruptedLedgerData = try restartedLedger.encoded()
var interruptedLedger = NativeNotificationReceiptLedger(data: interruptedLedgerData, now: notificationNow.addingTimeInterval(5))
try check(interruptedLedger.reserve(accountID: 7, eventID: "event-retry", reservationID: "run-b",
                                     now: notificationNow.addingTimeInterval(5)),
          "a reservation interrupted by restart is retryable under the new run")
interruptedLedger.release(accountID: 7, eventID: "event-retry", reservationID: "run-b")
try check(interruptedLedger.reserve(accountID: 7, eventID: "event-retry", reservationID: "run-b",
                                     now: notificationNow.addingTimeInterval(6)),
          "explicit notification delivery failure releases the reservation for retry")

let healthIssue = NativeInboxEvent(id: "health-issue-1", eventType: "server_problem",
    protectedText: "Сервер недоступен", unread: true, createdAt: notificationNow)
let firstHealthPlan = NativeNotificationPolicy.deliveryPlan(events: [healthIssue], accountID: 7,
    ledger: notificationLedger, now: notificationNow)
try check(firstHealthPlan.deliver.map(\.id) == ["health-window-issue-health-issue-1"],
          "first health issue creates one durable debounce-window notification")
let firstHealthNotice = firstHealthPlan.deliver[0]
try check(notificationLedger.reserve(accountID: 7, eventID: firstHealthNotice.id,
                                      reservationID: "health-run-1", now: notificationNow),
          "first health notification can be reserved")
notificationLedger.complete(accountID: 7, eventID: firstHealthNotice.id,
                            reservationID: "health-run-1", now: notificationNow)
notificationLedger = NativeNotificationReceiptLedger(data: try notificationLedger.encoded(),
    now: notificationNow.addingTimeInterval(60))

let healthIssue2 = NativeInboxEvent(id: "health-issue-2", eventType: "integration_down",
    protectedText: "Интеграция недоступна", unread: true,
    createdAt: notificationNow.addingTimeInterval(60))
let workDuringIncident = NativeInboxEvent(id: "task-during-health", eventType: "new_task",
    protectedText: "Новая задача", unread: true, createdAt: notificationNow.addingTimeInterval(60))
let repeatedHealthPlan = NativeNotificationPolicy.deliveryPlan(events: [healthIssue, healthIssue2, workDuringIncident],
    accountID: 7, ledger: notificationLedger, now: notificationNow.addingTimeInterval(60))
try check(repeatedHealthPlan.deliver == [workDuringIncident],
          "a new issue in the same health window is quiet while a fresh work event is still delivered")
try check(Set(repeatedHealthPlan.suppress.map(\.id)) == Set(["health-issue-1", "health-issue-2"]),
          "health debounce changes only native delivery and leaves source inbox events untouched")

let healthRecovery = NativeInboxEvent(id: "health-recovery-1", eventType: "system_recovered",
    protectedText: "Система восстановлена", unread: true,
    createdAt: notificationNow.addingTimeInterval(120))
let recoveryPlan = NativeNotificationPolicy.deliveryPlan(events: [healthIssue, healthIssue2, healthRecovery], accountID: 7,
    ledger: notificationLedger, now: notificationNow.addingTimeInterval(120))
try check(recoveryPlan.deliver.count == 1 && recoveryPlan.deliver[0].id == "health-window-recovery-health-recovery-1" &&
          recoveryPlan.deliver[0].eventType == "system_recovered",
          "health recovery crosses the phase boundary and is delivered once")
let recoveryNotice = recoveryPlan.deliver[0]
try check(notificationLedger.reserve(accountID: 7, eventID: recoveryNotice.id,
                                      reservationID: "health-run-2", now: notificationNow.addingTimeInterval(120)),
          "health recovery can be reserved independently from the issue")
notificationLedger.complete(accountID: 7, eventID: recoveryNotice.id,
                            reservationID: "health-run-2", now: notificationNow.addingTimeInterval(120))
notificationLedger = NativeNotificationReceiptLedger(data: try notificationLedger.encoded(),
    now: notificationNow.addingTimeInterval(180))
let healthRecovery2 = NativeInboxEvent(id: "health-recovery-2", eventType: "system_recovered",
    protectedText: "Система снова в норме", unread: true,
    createdAt: notificationNow.addingTimeInterval(180))
let repeatedRecoveryPlan = NativeNotificationPolicy.deliveryPlan(events: [healthRecovery2], accountID: 7,
    ledger: notificationLedger, now: notificationNow.addingTimeInterval(180))
try check(repeatedRecoveryPlan.deliver.isEmpty,
          "a later poll with a new single recovery does not create another banner in the same window")

let reopenedHealthIssue = NativeInboxEvent(id: "health-issue-reopened", eventType: "server_problem",
    protectedText: "Новая авария после восстановления", unread: true,
    createdAt: notificationNow.addingTimeInterval(240))
let reopenedIssuePlan = NativeNotificationPolicy.deliveryPlan(events: [reopenedHealthIssue], accountID: 7,
    ledger: notificationLedger, now: notificationNow.addingTimeInterval(240))
try check(reopenedIssuePlan.deliver.map(\.id) == ["health-window-issue-health-issue-reopened"],
          "a recovery closes the previous issue episode so a genuine new outage alerts inside ten minutes")

let nextWindowIssue = NativeInboxEvent(id: "health-issue-later", eventType: "server_problem",
    protectedText: "Новая авария", unread: true, createdAt: notificationNow.addingTimeInterval(660))
let nextWindowPlan = NativeNotificationPolicy.deliveryPlan(events: [nextWindowIssue], accountID: 7,
    ledger: notificationLedger, now: notificationNow.addingTimeInterval(660))
try check(nextWindowPlan.deliver.map(\.id) == ["health-window-issue-health-issue-later"],
          "a health issue outside the rolling debounce window can alert again")
var interruptedHealthLedger = NativeNotificationReceiptLedger(data: nil, now: notificationNow)
try check(interruptedHealthLedger.reserve(accountID: 9, eventID: "health-window-issue-health-before-crash",
    reservationID: "health-crashed-run", now: notificationNow), "health delivery is durably reserved before the banner")
let healthAfterCrash = NativeInboxEvent(id: "health-after-crash", eventType: "server_problem",
    protectedText: "Авария после перезапуска", unread: true,
    createdAt: notificationNow.addingTimeInterval(30))
let interruptedHealthPlan = NativeNotificationPolicy.deliveryPlan(events: [healthAfterCrash], accountID: 9,
    ledger: interruptedHealthLedger, now: notificationNow.addingTimeInterval(30))
try check(interruptedHealthPlan.deliver.map(\.id) == ["health-window-issue-health-before-crash"],
          "an interrupted health delivery reuses its durable window id instead of creating a second banner")
var incident = NativeIncidentPolicy()
try check(incident.failed(isRoyal: true, networkAvailable: true) == nil && incident.failed(isRoyal: true, networkAvailable: true) == nil, "royal incident waits for three failures")
try check(incident.failed(isRoyal: true, networkAvailable: true) == .serverIssue, "royal server incident starts after three online failures")
try check(incident.succeeded(isRoyal: true) == .recovered && incident.succeeded(isRoyal: true) == nil, "royal recovery is emitted once")
var offlineIncident = NativeIncidentPolicy()
_ = offlineIncident.failed(isRoyal: true, networkAvailable: false); _ = offlineIncident.failed(isRoyal: true, networkAvailable: false)
try check(offlineIncident.failed(isRoyal: true, networkAvailable: false) == .networkIssue, "offline client is never reported as a server outage")
try check(ResourceTelemetry(cpuPercent: 70, memoryPercent: 20, thermalState: "normal").computeCapacity(activeJobs: 0, ready: true, paused: false, lowPower: false) == 0, "compute drains at CPU threshold")
try check(ResourceTelemetry(cpuPercent: 20, memoryPercent: 80, thermalState: "normal").computeCapacity(activeJobs: 0, ready: true, paused: false, lowPower: false) == 0, "compute drains at memory threshold")
try check(nominal.computeCapacity(activeJobs: 0, ready: true, paused: false, lowPower: true) == 0, "compute drains in low power mode")
let warm = ResourceTelemetry(cpuPercent: 70, memoryPercent: 65, thermalState: "fair")
try check(warm.capacity(base: 2, activeJobs: 0, ready: true, paused: false) == 1, "fair thermal state throttles capacity")
for overloaded in [
    ResourceTelemetry(cpuPercent: 96, memoryPercent: 40, thermalState: "normal"),
    ResourceTelemetry(cpuPercent: 40, memoryPercent: 91, thermalState: "normal"),
    ResourceTelemetry(cpuPercent: 40, memoryPercent: 40, thermalState: "serious"),
] {
    try check(overloaded.capacity(base: 2, activeJobs: 0, ready: true, paused: false) == 0, "saturated Mac leaves scheduling pool")
}
let bounded = ResourceTelemetry(cpuPercent: -10, memoryPercent: 400, thermalState: "unknown")
try check(bounded.cpuPercent == 0 && bounded.memoryPercent == 100 && bounded.thermalState == "normal", "telemetry is bounded before heartbeat")
try check(nominal.capacity(base: 2, activeJobs: 2, ready: true, paused: false) == 2, "capacity reports total slots while inflight is separate")
try check(nominal.capacity(base: 2, activeJobs: 0, ready: false, paused: false) == 0, "unready Mac exposes no capacity")
let storageParent = FileManager.default.temporaryDirectory.appendingPathComponent("robopark-update-storage-\(UUID().uuidString)")
let storageURL = storageParent.appendingPathComponent("support")
try FileManager.default.createDirectory(at: storageURL, withIntermediateDirectories: true,
    attributes: [.posixPermissions: 0o700])
defer { try? FileManager.default.removeItem(at: storageParent) }
let storage = SecureUpdateStorage(root: storageURL, expectedOwner: getuid(), trustedAncestors: [storageParent])
try check(try storage.loadState() == nil, "absent private update state starts empty")
try Data("corrupt".utf8).write(to: storageURL.appendingPathComponent("state.json"))
rejects("corrupt antirollback state fails closed") { _ = try storage.loadState() }
try FileManager.default.removeItem(at: storageURL.appendingPathComponent("state.json"))
let staged = storageURL.appendingPathComponent("verified-update.pkg")
try FileManager.default.createSymbolicLink(at: staged, withDestinationURL: storageParent.appendingPathComponent("attacker.pkg"))
rejects("staging symlink is never followed") { try storage.stagePackage(Data("pkg".utf8)) }
try FileManager.default.removeItem(at: staged)
try Data("crash leftover".utf8).write(to: staged)
try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: staged.path)
try storage.stagePackage(Data("verified pkg".utf8))
try check(try Data(contentsOf: staged) == Data("verified pkg".utf8), "root-owned crash leftover is safely replaced")
let installer = try VerifiedInstaller.command(package: staged, supportRoot: storageURL)
try check(installer.executable == "/usr/sbin/installer" && installer.arguments == ["-pkg", staged.path, "-target", "/"], "installer command accepts only fixed staged package")
rejects("installer cannot execute arbitrary package path") {
    _ = try VerifiedInstaller.command(package: storageParent.appendingPathComponent("attacker.pkg"), supportRoot: storageURL)
}
let firstLock = try storage.acquireLock()
rejects("concurrent root updater is serialized") { _ = try storage.acquireLock() }
try firstLock.close()
let recoveredLock = try storage.acquireLock()
try recoveredLock.close()
try check(LoginPolicy.allows(URL(string: "https://passport.yandex-team.ru/auth?retpath=test")!), "corporate SSO allowed")
for address in ["https://attacker.example", "https://passport.yandex-team.ru.attacker.example", "http://passport.yandex-team.ru", "https://u:p@passport.yandex-team.ru", "https://passport.yandex-team.ru:8443"] {
    try check(!LoginPolicy.allows(URL(string: address)!), "login phishing navigation blocked")
}
let timeoutRunner = ComputeWorkerProcess()
let timeoutStarted = Date()
do {
    _ = try await timeoutRunner.run(frame: Data("{}".utf8), timeoutMilliseconds: 50,
                                    executable: URL(fileURLWithPath: "/bin/sleep"), arguments: ["5"])
    fatalError("FAIL: compute child timeout was ignored")
} catch { checks += 1 }
try check(Date().timeIntervalSince(timeoutStarted) < 1.5, "compute timeout terminates child promptly")
let cancelRunner = ComputeWorkerProcess()
let cancelStarted = Date()
let cancellable = Task { try await cancelRunner.run(frame: Data("{}".utf8), timeoutMilliseconds: 10_000,
                                                     executable: URL(fileURLWithPath: "/bin/sleep"), arguments: ["5"]) }
try await Task.sleep(nanoseconds: 50_000_000); cancellable.cancel()
do { _ = try await cancellable.value; fatalError("FAIL: cancelled compute child completed") } catch { checks += 1 }
try check(Date().timeIntervalSince(cancelStarted) < 1.5, "compute cancellation terminates child promptly")

let watchdogRun = UUID()
var watchdog = ConnectionWatchdog()
watchdog.begin(run: watchdogRun, generation: "generation-1")
guard let missedHeartbeat = watchdog.heartbeatSent(run: watchdogRun, generation: "generation-1") else {
    fatalError("FAIL: watchdog did not arm after heartbeat")
}
try check(watchdog.expire(missedHeartbeat), "missing heartbeat acknowledgement expires the current socket")

watchdog.begin(run: watchdogRun, generation: "generation-2")
guard let acknowledgedHeartbeat = watchdog.heartbeatSent(run: watchdogRun, generation: "generation-2") else {
    fatalError("FAIL: watchdog did not arm acknowledged heartbeat")
}
try check(watchdog.heartbeatSent(run: watchdogRun, generation: "generation-2") == nil,
          "a second tick waits for the pending heartbeat without replacing its deadline")
try check(watchdog.acknowledge(run: watchdogRun, generation: "generation-2"),
          "matching acknowledgement renews channel liveness")
try check(!watchdog.expire(acknowledgedHeartbeat), "acknowledged heartbeat cannot close its socket")
try check(watchdog.heartbeatSent(run: watchdogRun, generation: "generation-2") != nil,
          "a delayed acknowledgement allows the next heartbeat tick")

watchdog.begin(run: watchdogRun, generation: "generation-3")
guard let currentHeartbeat = watchdog.heartbeatSent(run: watchdogRun, generation: "generation-3") else {
    fatalError("FAIL: watchdog did not arm current generation")
}
try check(!watchdog.acknowledge(run: watchdogRun, generation: "stale-generation"),
          "stale generation acknowledgement is ignored")
try check(watchdog.expire(currentHeartbeat), "stale acknowledgement does not keep the current socket alive")

watchdog.begin(run: watchdogRun, generation: "generation-4")
guard let oldHeartbeat = watchdog.heartbeatSent(run: watchdogRun, generation: "generation-4") else {
    fatalError("FAIL: watchdog did not arm old connection")
}
watchdog.begin(run: watchdogRun, generation: "generation-5")
try check(!watchdog.expire(oldHeartbeat), "old timeout cannot close a replacement socket")
try check(!watchdog.end(run: watchdogRun, generation: "generation-4"),
          "old cleanup cannot clear a replacement socket")
guard let replacementHeartbeat = watchdog.heartbeatSent(run: watchdogRun, generation: "generation-5") else {
    fatalError("FAIL: old cleanup cleared replacement watchdog")
}
try check(watchdog.end(run: watchdogRun, generation: "generation-5"),
          "current cleanup clears its own socket")
try check(!watchdog.expire(replacementHeartbeat), "cleaned up socket cannot expire later")

let deadlineStarted = Date()
do {
    _ = try await AsyncDeadline.run(nanoseconds: 25_000_000) {
        let cancellationInsensitive = Task.detached {
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            return 1
        }
        return await cancellationInsensitive.value
    }
    fatalError("FAIL: handshake deadline accepted a stalled receive")
} catch is AsyncDeadlineError { checks += 1 }
try check(Date().timeIntervalSince(deadlineStarted) < 1.0, "handshake deadline fails promptly")
print("PASS: \(checks) protocol, scope, update signature and artifact integrity checks")
