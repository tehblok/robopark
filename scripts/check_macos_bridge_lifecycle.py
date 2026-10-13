#!/usr/bin/env python3
"""Hermetic lifecycle regression for the production Bridge enrollment flow."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MACOS = ROOT / "apps" / "macos"
PACKAGE = """// swift-tools-version: 5.9
import PackageDescription
let package = Package(name: "BridgeLifecycleChecks", platforms: [.macOS(.v13)], targets: [
 .target(name: "AgentCore"), .executableTarget(name: "BridgeLifecycleChecks", dependencies: ["AgentCore"])])
"""
SHIMS = r"""import Foundation
import CryptoKit
import AgentCore
enum NetworkFailure: Error, Equatable { case auth, network, invalid; var wireCode: String { "network" } }
enum SecureStore {
 static var registration: Registration?; static var data: [String: Data] = [:]; static var signed = false; static var failSave = false
 static func reset() { registration=nil; data=[:]; signed=false; failSave=false }
 static func load() throws -> Registration? { signed ? nil : registration }
 static func save(_ value: Registration) throws { if failSave { throw NSError(domain:"keychain", code:1) }; registration=value; signed=false }
 static func remove() throws { signed=true; registration=nil }
 static func readData(_ account:String) throws -> Data? { data[account] }
 static func writeData(_ value:Data, account:String) throws { data[account]=value }
 static func deleteData(_ account:String) throws { data.removeValue(forKey:account) }
}
enum EmbeddedTrust {
 static func verify(_ envelope: SignedCatalog?, origin: ServerOrigin, instanceID: String, environment: String) throws -> DeploymentProfile {
  guard envelope != nil else { throw AgentError.wrongServer }
  let x:[String:Any] = ["purpose":"robopark-deployment-v1","origin":origin.url.absoluteString,"instance_id":instanceID,"environment":environment,"channel":"rc","expires":Int64.max]
  return try JSONDecoder().decode(DeploymentProfile.self, from: JSONSerialization.data(withJSONObject:x))
 }
}
actor Claims {
 static let shared=Claims(); var calls:[[String:String]]=[]; var waiter:CheckedContinuation<Void,Never>?; var reply:CheckedContinuation<(Data,HTTPURLResponse),Error>?
 func reset(){ calls=[]; waiter=nil; reply=nil }
 func begin(_ object:[String:Any]) async throws -> (Data,HTTPURLResponse) {
  calls.append(object.compactMapValues { $0 as? String }); waiter?.resume(); waiter=nil
  return try await withCheckedThrowingContinuation { reply=$0 }
 }
 func wait(for expected:Int) async { if calls.count >= expected { return }; await withCheckedContinuation { waiter=$0 } }
 func succeed(_ id:String) { let body=try! JSONSerialization.data(withJSONObject:["device_id":id,"instance_id":"11111111-1111-4111-8111-111111111111","environment":"test"]); reply?.resume(returning:(body, response(201))); reply=nil }
 func fail(){ reply?.resume(throwing:NetworkFailure.network); reply=nil }
 func keys() -> [String] { calls.compactMap { $0["public_key"] } }
 func response(_ status:Int) -> HTTPURLResponse { HTTPURLResponse(url:URL(string:"https://service.example/api/agent-devices/claim")!,statusCode:status,httpVersion:"HTTP/1.1",headerFields:[:])! }
}
func response(_ status:Int)->HTTPURLResponse { HTTPURLResponse(url:URL(string:"https://service.example/api/agent-devices/info")!,statusCode:status,httpVersion:"HTTP/1.1",headerFields:[:])! }
enum Network {
 static var infoCalls=0
 static let session=URLSession(configuration:.ephemeral)
 static func request(_ url:URL, method:String="GET", object:[String:Any]?=nil, cookie:String?=nil, limit:Int=131072, timeout:Double=12) async throws -> (Data,HTTPURLResponse) {
  if url.path.hasSuffix("/claim") { return try await Claims.shared.begin(object ?? [:]) }
  infoCalls += 1
  guard infoCalls == 1 else { throw NetworkFailure.network }
  let info:[String:Any] = ["signed_profile":["payload":"x","signature":"x"],"environment":"test","instance_id":"11111111-1111-4111-8111-111111111111","protocol":1]
  return (try JSONSerialization.data(withJSONObject:info), response(200))
 }
 static func emergency(vin:String,cookie:String,timeout:Double=10) async throws -> [String:Any] { throw NetworkFailure.network }
}
"""
MAIN = r"""import Foundation
import AgentCore
var checks=0
func check(_ v:@autoclosure() throws -> Bool,_ n:String)throws { guard try v() else { fatalError("FAIL: \(n)") }; checks+=1 }
func reset() async { SecureStore.reset(); Network.infoCalls=0; await Claims.shared.reset() }
func enroll(_ bridge:Bridge) async throws { try await bridge.enroll(origin:"https://service.example",code:"code",label:"Mac") }
@main struct Run { static func main() async throws {
 let id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
 await reset(); let wake=Bridge(); let first=Task { try await enroll(wake) }; await Claims.shared.wait(for: 1); wake.start(); await Claims.shared.succeed(id); try await first.value
 try check(wake.registration?.deviceID == id,"wake during pending claim preserves enrollment run"); wake.stop()
 await reset(); let cancelled=Bridge(); let pending=Task { try await enroll(cancelled) }; await Claims.shared.wait(for: 1); try cancelled.disconnect(); await Claims.shared.succeed(id)
 do { try await pending.value; fatalError("FAIL: disconnect accepted pending claim") } catch { checks+=1 }
 try check(cancelled.registration == nil,"explicit disconnect cancels pending claim")
 await reset(); let retry=Bridge(); let lost=Task { try await enroll(retry) }; await Claims.shared.wait(for: 1); await Claims.shared.fail(); do { try await lost.value; fatalError("FAIL: lost claim accepted") } catch { checks+=1 }; Network.infoCalls=0
 let again=Task { try await enroll(retry) }; await Claims.shared.wait(for: 2); let keys=await Claims.shared.keys(); try check(keys.count == 2 && keys[0] == keys[1],"lost claim retry reuses pending key")
 SecureStore.failSave=true; await Claims.shared.succeed(id); do { try await again.value; fatalError("FAIL: persist failure accepted") } catch { checks+=1 }; try check(retry.registration == nil,"persist failure does not retain registration")
 SecureStore.failSave=false; Network.infoCalls=0; let recovered=Task { try await enroll(retry) }; await Claims.shared.wait(for: 3); let recoveredKeys=await Claims.shared.keys(); try check(recoveredKeys.last == keys[0],"persist failure retains retry key")
 await Claims.shared.succeed(id); try await recovered.value; retry.stop()
 print("PASS: \(checks) production Bridge enrollment wake, disconnect and retry lifecycle checks")
} }
"""


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="robopark-bridge-lifecycle-") as raw:
        root = Path(raw)
        (root / "Sources/BridgeLifecycleChecks").mkdir(parents=True)
        shutil.copytree(MACOS / "Sources/AgentCore", root / "Sources/AgentCore")
        shutil.copy2(
            MACOS / "Sources/RoboparkAgent/Bridge.swift",
            root / "Sources/BridgeLifecycleChecks/Bridge.swift",
        )
        (root / "Package.swift").write_text(PACKAGE)
        (root / "Sources/BridgeLifecycleChecks/Shims.swift").write_text(SHIMS)
        (root / "Sources/BridgeLifecycleChecks/main.swift").write_text(MAIN)
        cache = Path("/private/tmp/robopark-bridge-lifecycle-checks")
        cache.mkdir(parents=True, exist_ok=True)
        return subprocess.run(
            [
                "swift",
                "run",
                "--package-path",
                str(root),
                "--scratch-path",
                str(cache),
                "--cache-path",
                str(cache / "cache"),
                "--config-path",
                str(cache / "config"),
                "--security-path",
                str(cache / "security"),
                "--disable-sandbox",
                "BridgeLifecycleChecks",
            ],
            check=False,
            env={
                **os.environ,
                "CLANG_MODULE_CACHE_PATH": str(cache / "clang"),
                "SWIFTPM_MODULECACHE_OVERRIDE": str(cache / "modules"),
            },
        ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
