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
let key = P256.Signing.PrivateKey()
let c = Challenge(type: "challenge", nonce: "nonce-123", environment: "test", instance_id: "instance", protocolVersion: 1)
let signature = try c.sign(key: key, environment: "test", instanceID: "instance")
try check(key.publicKey.isValidSignature(try P256.Signing.ECDSASignature(derRepresentation: signature),
            for: Data("robopark-bridge-v1\ninstance\ntest\nnonce-123".utf8)), "Python-compatible DER challenge")
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
try check(LoginPolicy.allows(URL(string: "https://passport.yandex-team.ru/auth?retpath=test")!), "corporate SSO allowed")
for address in ["https://attacker.example", "https://passport.yandex-team.ru.attacker.example", "http://passport.yandex-team.ru", "https://u:p@passport.yandex-team.ru", "https://passport.yandex-team.ru:8443"] {
    try check(!LoginPolicy.allows(URL(string: address)!), "login phishing navigation blocked")
}
print("PASS: \(checks) protocol, scope, update signature and artifact integrity checks")
