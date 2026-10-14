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
    ["id": 7, "event_type": "new_task", "protected_text": "Проверьте задачу", "read_at": NSNull()],
    ["id": "report-8", "event_type": "report", "protected_text": "Отчёт готов", "read_at": "2026-10-14T10:00:00Z"],
]))
try check(inbox.count == 2 && inbox[0].unread && !inbox[1].unread, "native inbox accepts all bounded server categories and read state")
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
    "id": "announcement-emoji", "event_type": "announcement", "protected_text": String(repeating: "🛠", count: 1000)
]]))
try check(unicodeInbox[0].protectedText.utf8.count == 4000, "native inbox accepts the server's 1000-Unicode-character text limit")
rejects("notification text bound") { _ = try NativeNotificationPolicy.parseInbox(JSONSerialization.data(withJSONObject: [["id": 1, "event_type": "task", "protected_text": String(repeating: "🛠", count: 1001)]])) }
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
print("PASS: \(checks) protocol, scope, update signature and artifact integrity checks")
