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
 func succeed(_ id:String) { let body=try! JSONSerialization.data(withJSONObject:["device_id":id,"owner_user_id":7,"owner_tracker_login":"owner.login","instance_id":"d6ed3b43-c41b-4614-833e-c5ec30936ef3","environment":"test","status":"approved"]); reply?.resume(returning:(body, response(201))); reply=nil }
 func fail(){ reply?.resume(throwing:NetworkFailure.network); reply=nil }
 func keys() -> [String] { calls.compactMap { $0["public_key"] } }
 func response(_ status:Int) -> HTTPURLResponse { HTTPURLResponse(url:URL(string:"https://roboparktest.ru.tuna.am/api/agent-devices/claim")!,statusCode:status,httpVersion:"HTTP/1.1",headerFields:[:])! }
}
func response(_ status:Int)->HTTPURLResponse { HTTPURLResponse(url:URL(string:"https://roboparktest.ru.tuna.am/api/agent-devices/info")!,statusCode:status,httpVersion:"HTTP/1.1",headerFields:[:])! }
enum Network {
 static var infoCalls=0
 static var corporateStatus=200
 static var corporateBody:[String:Any] = ["login":"owner.login","dismissed":false]
 static var inventoryStatus = CorporateInventoryStatus.ready
 static var inventoryCalls = 0
 static let session=URLSession(configuration:.ephemeral)
 static func request(_ url:URL, method:String="GET", object:[String:Any]?=nil, cookie:String?=nil, limit:Int=131072, timeout:Double=12) async throws -> (Data,HTTPURLResponse) {
  if url.path.hasSuffix("/claim") { return try await Claims.shared.begin(object ?? [:]) }
  infoCalls += 1
  guard infoCalls == 1 else { throw NetworkFailure.network }
  let info:[String:Any] = ["signed_profile":["payload":"x","signature":"x"],"environment":"test","instance_id":"d6ed3b43-c41b-4614-833e-c5ec30936ef3","protocol":1]
  return (try JSONSerialization.data(withJSONObject:info), response(200))
 }
 static func emergency(vin:String,cookie:String,timeout:Double=10) async throws -> [String:Any] { throw NetworkFailure.network }
 static func corporateIdentity(authorization:String,timeout:Double=10) async throws -> (Data,HTTPURLResponse) {
  let body=try JSONSerialization.data(withJSONObject:corporateBody)
  let url=URL(string:"https://st-api.yandex-team.ru/v3/myself")!
  return (body,HTTPURLResponse(url:url,statusCode:corporateStatus,httpVersion:"HTTP/1.1",headerFields:[:])!)
 }
 static func corporateInventoryReadiness(serverAuthorizedVIN:String,oauthToken:String,timeout:Double=10,
                                         sessionOverride:URLSession?=nil) async -> CorporateInventoryStatus {
  inventoryCalls += 1; return inventoryStatus
 }
}
"""
MAIN = r"""import Foundation
import AgentCore
var checks=0
func check(_ v:@autoclosure() throws -> Bool,_ n:String)throws { guard try v() else { fatalError("FAIL: \(n)") }; checks+=1 }
func reset() async { SecureStore.reset(); Network.infoCalls=0; Network.corporateStatus=200; Network.corporateBody=["login":"owner.login","dismissed":false]; Network.inventoryStatus = .ready; Network.inventoryCalls = 0; await Claims.shared.reset() }
func enroll(_ bridge:Bridge) async throws { try await bridge.enroll(origin:"https://roboparktest.ru.tuna.am",code:"code",label:"Mac") }
@main struct Run { static func main() async throws {
 let id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
 await reset(); let wake=Bridge(); let first=Task { try await enroll(wake) }; await Claims.shared.wait(for: 1); wake.start(); await Claims.shared.succeed(id); try await first.value
 try check(wake.registration?.deviceID == id && wake.registration?.ownerUserID == 7,
           "code enrollment persists the server-derived owner")
 try wake.disconnect(); let afterDisconnect=Bridge()
 try check(afterDisconnect.registration?.deviceID == id && afterDisconnect.registration?.ownerUserID == 7,
           "ordinary disconnect preserves immutable device owner and key")
 Network.infoCalls=0
 do {
  try await afterDisconnect.claimAuthenticated(origin:"https://roboparktest.ru.tuna.am",label:"Other",
      expectedOwnerUserID:8) { _ in
   let reply:[String:Any] = ["device_id":id,"owner_user_id":7,"environment":"test",
       "instance_id":"d6ed3b43-c41b-4614-833e-c5ec30936ef3","protocol":1,"status":"approved"]
   return (try JSONSerialization.data(withJSONObject:reply), response(200))
  }
  fatalError("FAIL: another account rebound a disconnected Mac")
 } catch { checks+=1 }
 try check(afterDisconnect.registration?.ownerUserID == 7 && afterDisconnect.registration?.deviceID == id,
           "another account cannot rebind a disconnected Mac")
 afterDisconnect.stop()
 await reset(); let cancelled=Bridge(); let pending=Task { try await enroll(cancelled) }; await Claims.shared.wait(for: 1); try cancelled.disconnect(); await Claims.shared.succeed(id)
 do { try await pending.value; fatalError("FAIL: disconnect accepted pending claim") } catch { checks+=1 }
 try check(cancelled.registration == nil,"explicit disconnect cancels pending claim")
 await reset(); let retry=Bridge(); let lost=Task { try await enroll(retry) }; await Claims.shared.wait(for: 1); await Claims.shared.fail(); do { try await lost.value; fatalError("FAIL: lost claim accepted") } catch { checks+=1 }; Network.infoCalls=0
 let again=Task { try await enroll(retry) }; await Claims.shared.wait(for: 2); let keys=await Claims.shared.keys(); try check(keys.count == 2 && keys[0] == keys[1],"lost claim retry reuses pending key")
 SecureStore.failSave=true; await Claims.shared.succeed(id); do { try await again.value; fatalError("FAIL: persist failure accepted") } catch { checks+=1 }; try check(retry.registration == nil,"persist failure does not retain registration")
 SecureStore.failSave=false; Network.infoCalls=0; let recovered=Task { try await enroll(retry) }; await Claims.shared.wait(for: 3); let recoveredKeys=await Claims.shared.keys(); try check(recoveredKeys.last == keys[0],"persist failure retains retry key")
 await Claims.shared.succeed(id); try await recovered.value; retry.stop()
 await reset(); SecureStore.registration = Registration(origin:"https://prod.example",deviceID:id,instanceID:"22222222-2222-4222-8222-222222222222",environment:"production",label:"old",privateKey:Data(repeating:1,count:32))
 let quarantined=Bridge(); quarantined.start(); try await Task.sleep(nanoseconds:20_000_000)
 try check(quarantined.registration == nil && Network.infoCalls == 0,"persisted production registration is quarantined before network use")
 await reset(); let authenticated=Bridge(); var claimKeys:[String]=[]
 func accountClaim(_ object:[String:Any]) async throws -> (Data,HTTPURLResponse) {
  claimKeys.append(object["public_key"] as? String ?? "")
  let reply:[String:Any] = ["device_id":id,"owner_user_id":7,"owner_tracker_login":"owner.login","environment":"test","instance_id":"d6ed3b43-c41b-4614-833e-c5ec30936ef3","protocol":1,"status":"approved"]
  return (try JSONSerialization.data(withJSONObject:reply), response(claimKeys.count == 1 ? 201 : 200))
 }
 try await authenticated.claimAuthenticated(origin:"https://roboparktest.ru.tuna.am",label:"Mac",expectedOwnerUserID:7,claim:accountClaim)
 try check(authenticated.registration?.deviceID == id && authenticated.registration?.ownerUserID == 7,
           "authenticated account claims and permanently owns the device")
 try check(authenticated.needsCorporateLogin,"first login exposes corporate SSO without waiting for an impossible cookie-free probe")
 let oauthState="0123456789abcdef0123456789abcdef"
 let oauth=try CorporateAuthPolicy.oauthCredential(from:URL(string:"https://oauth.yandex-team.ru/verification_code#access_token=token&expires_in=3600&state=\(oauthState)")!,expectedState:oauthState)
 try authenticated.storeVerifiedOAuth(oauth,login:"owner.login")
 try authenticated.setProbe("YASADR00000000664")
 let inventoryReady = await authenticated.verifyInventoryReadiness(force:true)
 try check(inventoryReady == .ready && Network.inventoryCalls == 1,
           "Inventory readiness verifies a server-authorized rover independently")
 Network.inventoryStatus = .denied
 try authenticated.setProbe("YASADR00000000665")
 let inventoryInvalidated = authenticated.inventoryReadiness
 let inventoryDenied = await authenticated.verifyInventoryReadiness()
 try check(inventoryInvalidated == .unverifiedHostACL
           && inventoryDenied == .denied && Network.inventoryCalls == 2,
           "a changed server probe invalidates and refreshes Inventory ACL readiness")
 try authenticated.storeVerifiedSession("Session_id=present",login:"owner.login")
 try check(!authenticated.needsCorporateLogin,"verified owner cookie enables corporate services")
 do { try authenticated.storeVerifiedSession("Session_id=other",login:"other.login"); fatalError("FAIL: wrong corporate owner accepted") } catch { checks+=1 }
 Network.corporateStatus=401
 let unauthorized=await authenticated.verifyCorporateSession(force:true)
 try check(unauthorized == .reauthRequired
           && authenticated.registration?.cookie.isEmpty == true,"401 expires and clears corporate session")
 try authenticated.storeVerifiedSession("Session_id=present",login:"owner.login")
 Network.corporateStatus=403
 let forbidden=await authenticated.verifyCorporateSession(force:true)
 try check(forbidden == .accessDenied
           && authenticated.registration?.corporateIdentityStatus == "access_denied","403 remains distinct access denied")
 Network.corporateStatus=500
 let unavailable=await authenticated.verifyCorporateSession(force:true)
 try check(unavailable == .networkUnavailable,
           "server/network failure remains retryable network unavailable")
 try check(authenticated.needsCorporateLogin,"non-ready corporate identity is never advertised ready")
 authenticated.stop(); authenticated.registration?.ownerUserID=nil; try authenticated.persist(); Network.infoCalls=0
 try await authenticated.claimAuthenticated(origin:"https://roboparktest.ru.tuna.am",label:"Mac",expectedOwnerUserID:7,claim:accountClaim)
 try check(claimKeys.count == 2 && claimKeys[0] == claimKeys[1]
           && authenticated.registration?.ownerUserID == 7,
           "legacy ownerless registration reuses its key and recovers authoritative owner")
 authenticated.stop(); Network.infoCalls=0
 do {
  try await authenticated.claimAuthenticated(origin:"https://roboparktest.ru.tuna.am",label:"Mac",expectedOwnerUserID:8,claim:accountClaim)
  fatalError("FAIL: mismatched authenticated owner replaced device binding")
 } catch { checks+=1 }
 try check(authenticated.registration?.ownerUserID == 7 && authenticated.registration?.deviceID == id,
           "mismatched account cannot rebind existing corporate credentials")
 authenticated.stop()
 await reset()
 var legacy=Registration(origin:"https://roboparktest.ru.tuna.am",deviceID:id,instanceID:"d6ed3b43-c41b-4614-833e-c5ec30936ef3",environment:"test",label:"Mac",privateKey:Data(repeating:1,count:32))
 legacy.ownerUserID=7; legacy.ownerTrackerLogin="owner.login"; legacy.corporateLogin="owner.login"; legacy.cookie="Session_id=legacy"
 SecureStore.registration=legacy
 let sanitized=Bridge()
 try check(sanitized.registration?.cookie.isEmpty == true && sanitized.needsCorporateLogin,
           "legacy cookie without runtime identity verification is invalidated")
 var missingOwner=legacy; missingOwner.ownerTrackerLogin=nil; missingOwner.cookie=""; missingOwner.corporateLogin=nil; missingOwner.corporateCookieVerifiedAt=nil
 SecureStore.registration=missingOwner
 let unmapped=Bridge()
 do { try unmapped.storeVerifiedSession("Session_id=present",login:"owner.login"); fatalError("FAIL: cookie accepted without server tracker owner") }
 catch { checks+=1 }
 print("PASS: \(checks) production Bridge enrollment wake, disconnect and retry lifecycle checks")
} }
"""


def main() -> int:
    application = (MACOS / "Sources/RoboparkAgent/Application.swift").read_text()
    launch = application.split("func applicationDidFinishLaunching", 1)[1].split(
        "private func initializeWorkspace", 1
    )[0]
    logout = application.split("private func accountLogout", 1)[1].split(
        "private func refreshProbeTarget", 1
    )[0]
    wake = application.split("@objc func wake", 1)[1].split("@objc func quit", 1)[0]
    sleep = application.split("@objc func sleep", 1)[1].split("@objc func quit", 1)[0]
    probe_timer = application.split("probeTimer = Timer.scheduledTimer", 1)[1].split(
        "private func initializeWorkspace", 1
    )[0]
    refresh_probe = application.split("private func refreshProbeTarget", 1)[1].split(
        "@objc func showPanel", 1
    )[0]
    identity = application.split("private func accountVerified", 1)[1].split(
        "private func accountLogout", 1
    )[0]
    bridge_source = (MACOS / "Sources/RoboparkAgent/Bridge.swift").read_text()
    if "bridge.start()" in launch:
        raise SystemExit(
            "FAIL: launch starts the pool before account identity is restored"
        )
    if (
        "UserDefaults.standard.set(false" not in logout
        or "corporateLoginRun = UUID()" not in logout
        or "corporateOAuthState = nil" not in logout
        or "loginWindow?.close()" not in logout
        or "bridge.stop()" not in logout
        or "compute.stop()" not in logout
        or "notifications.stop(removeDelivered: true)" not in logout
        or "if accountActive {" not in wake
        or "if emergencyAuthorized { bridge.start()" not in wake
        or "compute.start()" not in wake
        or "notifications.resume()" not in wake
        or "bridge.stop()" not in sleep
        or "compute.stop()" not in sleep
        or "notifications.suspend()" not in sleep
    ):
        raise SystemExit("FAIL: logout/wake account activation gate regressed")
    if "CorporateAuthPolicy.oauthCredential(from: url, expectedState: state)" not in application:
        raise SystemExit("FAIL: corporate OAuth callback is not bound to its one-use state")
    if "bridge.updateCookie" in application or "HTTPCookie.requestHeaderFields" in application:
        raise SystemExit("FAIL: unverified browser cookies can still enter the Emergency credential store")
    if 'presentWorkspace(route: "/schedule#notifications")' not in application:
        raise SystemExit("FAIL: native notification action is not fixed to the bundled notification center route")
    fast_path = identity.split("if accountActive, activeAccountID == userID", 1)[1].split("let run = UUID()", 1)[0]
    if "compute.start()" in fast_path or "claimAuthenticated" in fast_path or "notifications.start" not in fast_path:
        raise SystemExit("FAIL: repeated same-account identity refresh restarts the compute lease")
    if "if granted && managedProbes" in bridge_source:
        raise SystemExit("FAIL: managed readiness bypasses the real Emergency request")
    accept_probe = bridge_source.split("private func acceptProbe", 1)[1].split("private func probeIfNeeded", 1)[0]
    if "probeTask = Task" not in accept_probe or "onChange?()" not in accept_probe:
        raise SystemExit("FAIL: accepted Emergency probe does not immediately preempt compute")
    if "else { self.restoreAccountSession() }" not in probe_timer:
        raise SystemExit(
            "FAIL: a transient reboot outage cannot recover the account session"
        )
    if "else { try? self.bridge.clearProbe() }" not in refresh_probe:
        raise SystemExit("FAIL: a null managed probe target retains stale readiness")
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
