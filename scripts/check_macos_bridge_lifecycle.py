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
enum NetworkFailure: Error, Equatable {
 case auth, network, invalid
 var wireCode: String {
  switch self { case .auth: return "auth"; case .network: return "network"; case .invalid: return "invalid" }
 }
}
enum SecureStore {
    static func failureMessage(_ error: Error) -> String { "storage unavailable" }
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
actor CorporateIdentityGate {
 static let shared=CorporateIdentityGate()
 var blocked=false; var started=false
 var startWaiter:CheckedContinuation<Void,Never>?; var releaseWaiter:CheckedContinuation<Void,Never>?
 func reset(){ blocked=false; started=false; startWaiter?.resume(); startWaiter=nil; releaseWaiter?.resume(); releaseWaiter=nil }
 func block(){ blocked=true; started=false }
 func waitForRequest() async { if started { return }; await withCheckedContinuation { startWaiter=$0 } }
 func pauseIfNeeded() async {
  guard blocked else { return }
  started=true; startWaiter?.resume(); startWaiter=nil
  await withCheckedContinuation { releaseWaiter=$0 }
 }
 func release(){ blocked=false; releaseWaiter?.resume(); releaseWaiter=nil }
}
actor InventoryGate {
 static let shared=InventoryGate()
 var blocked=false; var started=false
 var startWaiter:CheckedContinuation<Void,Never>?; var releaseWaiter:CheckedContinuation<Void,Never>?
 func reset(){ blocked=false; started=false; startWaiter?.resume(); startWaiter=nil; releaseWaiter?.resume(); releaseWaiter=nil }
 func block(){ blocked=true; started=false }
 func waitForRequest() async { if started { return }; await withCheckedContinuation { startWaiter=$0 } }
 func pauseIfNeeded() async {
  guard blocked else { return }
  started=true; startWaiter?.resume(); startWaiter=nil
  await withCheckedContinuation { releaseWaiter=$0 }
 }
 func release(){ blocked=false; releaseWaiter?.resume(); releaseWaiter=nil }
}
func response(_ status:Int)->HTTPURLResponse { HTTPURLResponse(url:URL(string:"https://roboparktest.ru.tuna.am/api/agent-devices/info")!,statusCode:status,httpVersion:"HTTP/1.1",headerFields:[:])! }
enum Network {
 static var infoCalls=0
 static var corporateStatus=200
 static var corporateBody:[String:Any] = ["login":"owner.login","dismissed":false]
 static var corporateCalls=0
 static var corporateThrows=false
 static var inventoryStatus = CorporateInventoryStatus.ready
 static var inventoryCalls = 0
 static var emergencyOutcomes: [NetworkFailure?] = []
 static var emergencyCalls = 0
 static let session=URLSession(configuration:.ephemeral)
 static func serverTransport() throws -> URLSession { session }
 static func persistentWebSocketTransport() throws -> URLSession { session }
 static func request(_ url:URL, method:String="GET", object:[String:Any]?=nil, cookie:String?=nil, limit:Int=131072, timeout:Double=12) async throws -> (Data,HTTPURLResponse) {
  if url.path.hasSuffix("/claim") { return try await Claims.shared.begin(object ?? [:]) }
  infoCalls += 1
  guard infoCalls == 1 else { throw NetworkFailure.network }
  let info:[String:Any] = ["signed_profile":["payload":"x","signature":"x"],"environment":"test","instance_id":"d6ed3b43-c41b-4614-833e-c5ec30936ef3","protocol":1]
  return (try JSONSerialization.data(withJSONObject:info), response(200))
 }
 static func emergency(vin:String,cookie:String,timeout:Double=10) async throws -> [String:Any] {
  emergencyCalls += 1
  let outcome = emergencyOutcomes.isEmpty ? NetworkFailure.network : emergencyOutcomes.removeFirst()
  if let outcome { throw outcome }
  return ["vin":vin,"timestamp":Int(Date().timeIntervalSince1970 * 1000),"isOnline":true]
 }
 static func corporateIdentity(authorization:String,timeout:Double=10) async throws -> (Data,HTTPURLResponse) {
  corporateCalls += 1
  await CorporateIdentityGate.shared.pauseIfNeeded()
  if corporateThrows { throw NetworkFailure.network }
  let body=try JSONSerialization.data(withJSONObject:corporateBody)
  let url=URL(string:"https://st-api.yandex-team.ru/v3/myself")!
  return (body,HTTPURLResponse(url:url,statusCode:corporateStatus,httpVersion:"HTTP/1.1",headerFields:[:])!)
 }
 static func corporateInventoryReadiness(serverAuthorizedVIN:String,oauthToken:String,timeout:Double=10,
                                         sessionOverride:URLSession?=nil) async -> CorporateInventoryStatus {
  inventoryCalls += 1; await InventoryGate.shared.pauseIfNeeded(); return inventoryStatus
 }
}
"""
MAIN = r"""import Foundation
import AgentCore
var checks=0
func check(_ v:@autoclosure() throws -> Bool,_ n:String)throws { guard try v() else { fatalError("FAIL: \(n)") }; checks+=1 }
func reset() async { SecureStore.reset(); Network.infoCalls=0; Network.corporateStatus=200; Network.corporateBody=["login":"owner.login","dismissed":false]; Network.corporateCalls=0; Network.corporateThrows=false; Network.inventoryStatus = .ready; Network.inventoryCalls = 0; Network.emergencyOutcomes=[]; Network.emergencyCalls=0; await Claims.shared.reset(); await CorporateIdentityGate.shared.reset(); await InventoryGate.shared.reset() }
func waitFor(_ condition: @escaping () -> Bool) async throws {
 for _ in 0..<200 { if condition() { return }; try await Task.sleep(nanoseconds:5_000_000) }
 throw NetworkFailure.network
}
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
 authenticated.registration?.inventoryOAuthVerifiedAt = nil
 authenticated.registration?.inventoryOAuthStatus = CorporateInventoryStatus.unverifiedHostACL.rawValue
 try authenticated.persist(); Network.inventoryCalls = 0
 let recoveredInventory = try await authenticated.refreshInventoryReadiness(forProbe:"YASADR00000000664")
 try check(recoveredInventory == .ready && Network.inventoryCalls == 1,
           "an unchanged refreshed probe recovers Inventory readiness without a compute heartbeat")
 let cachedInventory = try await authenticated.refreshInventoryReadiness(forProbe:"YASADR00000000664")
 try check(cachedInventory == .ready && Network.inventoryCalls == 1,
           "periodic probe refresh reuses the Inventory readiness TTL")
 authenticated.registration?.inventoryOAuthVerifiedAt = nil
 Network.inventoryCalls = 0; await InventoryGate.shared.block()
 let probeRefresh = Task { try await authenticated.refreshInventoryReadiness(forProbe:"YASADR00000000664") }
 await InventoryGate.shared.waitForRequest()
 let computeRefresh = Task { await authenticated.verifyInventoryReadiness() }
 try await Task.sleep(nanoseconds:20_000_000)
 try check(Network.inventoryCalls == 1, "probe and compute refresh share one Inventory HTTP request")
 await InventoryGate.shared.release()
 let probeResult = try await probeRefresh.value
 let computeResult = await computeRefresh.value
 try check(probeResult == .ready && computeResult == .ready && authenticated.inventoryReadiness == .ready,
           "shared Inventory verification publishes one consistent result")
 authenticated.registration?.inventoryOAuthVerifiedAt = nil
 authenticated.registration?.inventoryOAuthStatus = CorporateInventoryStatus.unverifiedHostACL.rawValue
 try authenticated.persist(); Network.inventoryCalls = 0; await InventoryGate.shared.block()
 let cancellingWaiter = Task { await authenticated.verifyInventoryReadiness(force:true) }
 await InventoryGate.shared.waitForRequest()
 let joinedWaiter = Task { await authenticated.verifyInventoryReadiness(force:true) }
 try await Task.sleep(nanoseconds:20_000_000)
 try check(Network.inventoryCalls == 1, "two Inventory waiters share the gated request before cancellation")
 cancellingWaiter.cancel(); await InventoryGate.shared.release()
 let cancelledWaiterResult = await cancellingWaiter.value
 let joinedWaiterResult = await joinedWaiter.value
 try check(cancelledWaiterResult == .unverifiedHostACL
           && joinedWaiterResult == .unverifiedHostACL
           && authenticated.registration?.inventoryOAuthVerifiedAt == nil
           && authenticated.inventoryReadiness == .unverifiedHostACL,
           "cancelling one shared waiter prevents the late Inventory result from being persisted")
 let recoveredAfterCancellation = await authenticated.verifyInventoryReadiness(force:true)
 try check(recoveredAfterCancellation == .ready
           && authenticated.inventoryReadiness == .ready
           && authenticated.registration?.inventoryOAuthVerifiedAt != nil
           && Network.inventoryCalls == 2,
           "a fresh Inventory verification recovers after shared-flight cancellation")
 authenticated.registration?.inventoryOAuthVerifiedAt = nil
 authenticated.registration?.inventoryOAuthStatus = CorporateInventoryStatus.unverifiedHostACL.rawValue
 try authenticated.persist(); await InventoryGate.shared.block()
 let staleInventory = Task { await authenticated.verifyInventoryReadiness(force:true) }
 await InventoryGate.shared.waitForRequest()
 authenticated.registration?.deviceID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
 try authenticated.persist(); await InventoryGate.shared.release()
 let staleInventoryResult = await staleInventory.value
 try check(staleInventoryResult == .unverifiedHostACL
           && authenticated.registration?.inventoryOAuthVerifiedAt == nil,
           "late Inventory response cannot cross a changed device binding")
 authenticated.registration?.deviceID = id
 authenticated.registration?.inventoryOAuthVerifiedAt = nil
 authenticated.registration?.inventoryOAuthStatus = CorporateInventoryStatus.unverifiedHostACL.rawValue
 try authenticated.persist(); await InventoryGate.shared.block()
 let cancelledInventory = Task { await authenticated.verifyInventoryReadiness(force:true) }
 await InventoryGate.shared.waitForRequest(); cancelledInventory.cancel(); await InventoryGate.shared.release()
 let cancelledInventoryResult = await cancelledInventory.value
 try check(cancelledInventoryResult == .unverifiedHostACL
           && authenticated.registration?.inventoryOAuthVerifiedAt == nil,
           "cancelled Inventory refresh cannot persist a late readiness result")
 Network.inventoryStatus = .denied; Network.inventoryCalls = 0
 try authenticated.setProbe("YASADR00000000665")
 let inventoryInvalidated = authenticated.inventoryReadiness
 let inventoryDenied = await authenticated.verifyInventoryReadiness()
 try check(inventoryInvalidated == .unverifiedHostACL
           && inventoryDenied == .denied && Network.inventoryCalls == 1,
           "a changed server probe invalidates and refreshes Inventory ACL readiness")
 try authenticated.storeVerifiedSession("Session_id=present",login:"owner.login")
 try check(!authenticated.needsCorporateLogin,"verified owner cookie enables corporate services")
 authenticated.generation="11111111-1111-4111-8111-111111111111"
 authenticated.readyUntil=Date().addingTimeInterval(120)
 let jobChannel=Network.session.webSocketTask(with:URL(string:"wss://roboparktest.ru.tuna.am/emergency/bridge/ws")!)
 func job(_ vin:String,_ attempt:String) -> [String:Any] {
  ["type":"job","operation":"robot_payload","generation":authenticated.generation,
   "job_id":UUID().uuidString,"attempt_id":attempt,"vin":vin,"timeout_ms":1_000]
 }
 Network.emergencyOutcomes=[.network,.invalid,nil,.auth]
 try authenticated.acceptJob(job("YASADR00000001001","11111111-1111-4111-8111-111111111101"),channel:jobChannel)
 try await waitFor { Network.emergencyCalls == 1 && authenticated.jobs.isEmpty }
 try check(authenticated.readyUntil > Date(),"one robot network failure preserves validated Emergency readiness")
 try authenticated.acceptJob(job("YASADR00000001002","11111111-1111-4111-8111-111111111102"),channel:jobChannel)
 try await waitFor { Network.emergencyCalls == 2 && authenticated.jobs.isEmpty }
 try check(authenticated.readyUntil > Date(),"one invalid robot response preserves validated Emergency readiness")
 try authenticated.acceptJob(job("YASADR00000001003","11111111-1111-4111-8111-111111111103"),channel:jobChannel)
 try await waitFor { Network.emergencyCalls == 3 && authenticated.completed == 1 && authenticated.jobs.isEmpty }
 try check(authenticated.readyUntil > Date() && authenticated.upstreamState == "Корпоративный доступ проверен",
           "another robot succeeds immediately after a per-robot failure")
 try authenticated.acceptJob(job("YASADR00000001004","11111111-1111-4111-8111-111111111104"),channel:jobChannel)
 try await waitFor { Network.emergencyCalls == 4 && authenticated.readyUntil < Date() && authenticated.jobs.isEmpty }
 try check(authenticated.upstreamState == "Требуется вход в Emergency",
           "job authentication failure invalidates global Emergency readiness")
 authenticated.registration?.trackerOAuthExpiresAt=nil
 authenticated.registration?.trackerOAuthVerifiedAt=Date()
 try authenticated.persist()
 let undatedRestart=Bridge()
 try check(undatedRestart.registration?.trackerOAuthToken == "token"
           && undatedRestart.hasVerifiedCorporateOAuth && !undatedRestart.needsCorporateLogin,
           "verified undated OAuth survives restart and remains ready for fifteen minutes")
 undatedRestart.registration?.trackerOAuthVerifiedAt=Date(timeIntervalSinceNow:-901)
 try undatedRestart.persist()
 Network.corporateCalls=0
 let refreshedUndated=await undatedRestart.verifiedCorporateOAuth()
 try check(refreshedUndated?.token == "token" && Network.corporateCalls == 1
           && undatedRestart.hasVerifiedCorporateOAuth,
           "undated OAuth is rechecked after the fifteen minute identity TTL")
 for transientStatus in [500,429] {
  let lastOwnerVerification=Date(timeIntervalSinceNow:-901)
  undatedRestart.registration?.trackerOAuthVerifiedAt=lastOwnerVerification
  Network.corporateStatus=transientStatus
  let transient=await undatedRestart.verifiedCorporateOAuth(force:true)
  try check(transient?.token == "token" && undatedRestart.registration?.trackerOAuthToken == "token"
            && undatedRestart.registration?.trackerOAuthVerifiedAt == lastOwnerVerification
            && undatedRestart.hasVerifiedCorporateOAuth,
            "transient HTTP identity failure uses bounded owner grace without renewing it")
 }
 let lastOfflineVerification=Date(timeIntervalSinceNow:-901)
 undatedRestart.registration?.trackerOAuthVerifiedAt=lastOfflineVerification
 Network.corporateThrows=true
 let offline=await undatedRestart.verifiedCorporateOAuth(force:true)
 try check(offline?.token == "token" && undatedRestart.registration?.trackerOAuthToken == "token"
           && undatedRestart.registration?.trackerOAuthVerifiedAt == lastOfflineVerification
           && undatedRestart.hasVerifiedCorporateOAuth,
           "network identity failure uses bounded owner grace without renewing it")
 let callsBeforeBackoff=Network.corporateCalls
 let backedOff=await undatedRestart.verifiedCorporateOAuth()
 try check(backedOff?.token == "token" && Network.corporateCalls == callsBeforeBackoff,
           "availability grace backs off identity retries during an outage")
 Network.corporateThrows=false; Network.corporateStatus=500
 undatedRestart.registration?.trackerOAuthVerifiedAt=Date(timeIntervalSinceNow:-3601)
 let expiredGrace=await undatedRestart.verifiedCorporateOAuth(force:true)
 try check(expiredGrace == nil && undatedRestart.registration?.trackerOAuthToken == "token"
           && !undatedRestart.hasVerifiedCorporateOAuth,
           "owner grace expires after one hour even when identity remains unavailable")
 try undatedRestart.storeVerifiedOAuth(oauth,login:"owner.login")
 undatedRestart.registration?.trackerOAuthExpiresAt=nil
 undatedRestart.registration?.trackerOAuthVerifiedAt=Date(timeIntervalSinceNow:-901)
 try undatedRestart.persist(); Network.corporateStatus=500; await CorporateIdentityGate.shared.block()
 let oauthCallsBeforeShared=Network.corporateCalls
 let sharedOAuthFirst=Task { await undatedRestart.verifiedCorporateOAuth(force:true) }
 await CorporateIdentityGate.shared.waitForRequest()
 let sharedOAuthSecond=Task { await undatedRestart.verifiedCorporateOAuth(force:true) }
 try await Task.sleep(nanoseconds:20_000_000)
 try check(Network.corporateCalls == oauthCallsBeforeShared + 1,
           "same-binding OAuth verification is a single shared identity request")
 await CorporateIdentityGate.shared.release()
 let sharedOAuthFirstResult=await sharedOAuthFirst.value
 let sharedOAuthSecondResult=await sharedOAuthSecond.value
 try check(sharedOAuthFirstResult?.token == "token" && sharedOAuthSecondResult?.token == "token",
           "same-binding OAuth waiters share one availability-grace outcome")
 try undatedRestart.storeVerifiedOAuth(oauth,login:"owner.login")
 Network.corporateStatus=401; await CorporateIdentityGate.shared.block()
 let oldOAuthFirst=Task { await undatedRestart.verifiedCorporateOAuth(force:true) }
 await CorporateIdentityGate.shared.waitForRequest()
 let oldOAuthSecond=Task { await undatedRestart.verifiedCorporateOAuth(force:true) }
 try await Task.sleep(nanoseconds:20_000_000)
 undatedRestart.onChange = {
  if undatedRestart.registration?.trackerOAuthToken == nil {
   undatedRestart.onChange=nil
   try! undatedRestart.storeVerifiedOAuth(oauth,login:"owner.login")
  }
 }
 await CorporateIdentityGate.shared.release()
 _ = await oldOAuthFirst.value; _ = await oldOAuthSecond.value
 try check(undatedRestart.registration?.trackerOAuthToken == "token"
           && undatedRestart.hasVerifiedCorporateOAuth,
           "late shared denial cannot invalidate a newly stored identical OAuth token")
 try undatedRestart.storeVerifiedOAuth(oauth,login:"owner.login")
 undatedRestart.registration?.trackerOAuthExpiresAt=nil
 undatedRestart.registration?.trackerOAuthVerifiedAt=Date(timeIntervalSinceNow:-901)
 try undatedRestart.persist(); Network.corporateStatus=200; await CorporateIdentityGate.shared.block()
 let cancelledSharedOAuth=Task { await undatedRestart.verifiedCorporateOAuth(force:true) }
 await CorporateIdentityGate.shared.waitForRequest()
 let survivingSharedOAuth=Task { await undatedRestart.verifiedCorporateOAuth(force:true) }
 cancelledSharedOAuth.cancel(); await CorporateIdentityGate.shared.release()
 let cancelledSharedOAuthResult=await cancelledSharedOAuth.value
 let survivingSharedOAuthResult=await survivingSharedOAuth.value
 try check(cancelledSharedOAuthResult == nil && survivingSharedOAuthResult?.token == "token",
           "cancelling one OAuth waiter does not cancel the shared identity verification")
 try undatedRestart.storeVerifiedOAuth(oauth,login:"owner.login")
 undatedRestart.registration?.trackerOAuthExpiresAt=nil
 undatedRestart.registration?.trackerOAuthVerifiedAt=Date(timeIntervalSinceNow:-901)
 try undatedRestart.persist(); await CorporateIdentityGate.shared.block()
 let cancelledGrace=Task { await undatedRestart.verifiedCorporateOAuth() }
 await CorporateIdentityGate.shared.waitForRequest(); cancelledGrace.cancel(); await CorporateIdentityGate.shared.release()
 let cancelledGraceResult=await cancelledGrace.value
 try check(cancelledGraceResult == nil
           && undatedRestart.registration?.trackerOAuthVerifiedAt != nil,
           "cancelled identity refresh cannot consume owner grace")
 undatedRestart.registration?.trackerOAuthVerifiedAt=Date(timeIntervalSinceNow:-901)
 try undatedRestart.persist(); await CorporateIdentityGate.shared.block()
 let reboundGrace=Task { await undatedRestart.verifiedCorporateOAuth() }
 await CorporateIdentityGate.shared.waitForRequest()
 undatedRestart.registration?.deviceID="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
 await CorporateIdentityGate.shared.release()
 let reboundGraceResult=await reboundGrace.value
 try check(reboundGraceResult == nil
           && undatedRestart.registration?.trackerOAuthToken == "token",
           "late transient identity result cannot cross a changed device binding")
 undatedRestart.registration?.deviceID=id
 undatedRestart.registration?.trackerOAuthVerifiedAt=Date(timeIntervalSinceNow:-901)
 try undatedRestart.persist()
 Network.corporateThrows=false; Network.corporateStatus=200; Network.corporateBody=["dismissed":false]
 let malformedOAuthIdentity=await undatedRestart.verifiedCorporateOAuth(force:true)
 try check(malformedOAuthIdentity == nil
           && undatedRestart.registration?.trackerOAuthToken == "token"
           && undatedRestart.registration?.trackerOAuthVerifiedAt == nil
           && !undatedRestart.hasVerifiedCorporateOAuth,
           "malformed OAuth identity success fails closed without consuming the reusable token")
 Network.corporateStatus=500
 let noOAuthGraceAfterDenial=await undatedRestart.verifiedCorporateOAuth(force:true)
 try check(noOAuthGraceAfterDenial == nil,
           "a malformed OAuth identity response cannot be hidden by later availability grace")
 Network.corporateBody=["login":"owner.login","dismissed":false]
 try undatedRestart.storeVerifiedOAuth(oauth,login:"owner.login")
 undatedRestart.registration?.trackerOAuthExpiresAt=nil
 undatedRestart.registration?.trackerOAuthVerifiedAt=Date(timeIntervalSinceNow:-901)
 try undatedRestart.persist()
 Network.corporateThrows=false; Network.corporateStatus=401
 let revoked=await undatedRestart.verifiedCorporateOAuth(force:true)
 try check(revoked == nil && undatedRestart.registration?.trackerOAuthToken == nil,
           "401 identity response invalidates OAuth")
 try undatedRestart.storeVerifiedOAuth(oauth,login:"owner.login")
 undatedRestart.registration?.trackerOAuthExpiresAt=nil
 Network.corporateStatus=403
 let forbiddenOAuth=await undatedRestart.verifiedCorporateOAuth(force:true)
 try check(forbiddenOAuth == nil && undatedRestart.registration?.trackerOAuthToken == nil,
           "403 identity response invalidates OAuth")
 try undatedRestart.storeVerifiedOAuth(oauth,login:"owner.login")
 undatedRestart.registration?.trackerOAuthExpiresAt=nil
 Network.corporateStatus=200; Network.corporateBody=["login":"other.login","dismissed":false]
 let mismatchedOAuth=await undatedRestart.verifiedCorporateOAuth(force:true)
 try check(mismatchedOAuth == nil && undatedRestart.registration?.trackerOAuthToken == nil,
           "owner mismatch invalidates OAuth")
 Network.corporateBody=["login":"owner.login","dismissed":false]
 try undatedRestart.storeVerifiedOAuth(oauth,login:"owner.login")
 undatedRestart.registration?.trackerOAuthExpiresAt=Date(timeIntervalSinceNow:-1)
 try undatedRestart.persist()
 let expiredRestart=Bridge()
 let callsBeforeExpired=Network.corporateCalls
 let expired=await expiredRestart.verifiedCorporateOAuth(force:true)
 try check(expired == nil && expiredRestart.registration?.trackerOAuthToken == nil
           && Network.corporateCalls == callsBeforeExpired,
           "known expired OAuth is rejected before identity traffic")
 try expiredRestart.storeVerifiedOAuth(oauth,login:"owner.login")
 expiredRestart.registration?.trackerOAuthExpiresAt=nil
 expiredRestart.registration?.trackerOAuthVerifiedAt=Date(timeIntervalSinceNow:-901)
 try expiredRestart.persist()
 Network.corporateStatus=401
 await CorporateIdentityGate.shared.block()
 let staleTokenCheck=Task { await expiredRestart.verifiedCorporateOAuth() }
 await CorporateIdentityGate.shared.waitForRequest()
 expiredRestart.registration?.trackerOAuthToken="replacement-token"
 await CorporateIdentityGate.shared.release()
 let staleTokenResult=await staleTokenCheck.value
 try check(staleTokenResult == nil && expiredRestart.registration?.trackerOAuthToken == "replacement-token",
           "late revoked response cannot erase a changed OAuth token")
 Network.corporateStatus=401
 try expiredRestart.storeVerifiedOAuth(oauth,login:"owner.login")
 expiredRestart.registration?.trackerOAuthExpiresAt=nil
 expiredRestart.registration?.trackerOAuthVerifiedAt=Date(timeIntervalSinceNow:-901)
 try expiredRestart.persist()
 await CorporateIdentityGate.shared.block()
 let staleOwnerCheck=Task { await expiredRestart.verifiedCorporateOAuth() }
 await CorporateIdentityGate.shared.waitForRequest()
 expiredRestart.registration?.ownerTrackerLogin="new.owner"
 await CorporateIdentityGate.shared.release()
 let staleOwnerResult=await staleOwnerCheck.value
 try check(staleOwnerResult == nil && expiredRestart.registration?.trackerOAuthToken == "token"
           && expiredRestart.registration?.ownerTrackerLogin == "new.owner",
           "late revoked response cannot mutate a changed owner binding")
 expiredRestart.registration?.ownerTrackerLogin="owner.login"
 Network.corporateStatus=403
 try expiredRestart.storeVerifiedOAuth(oauth,login:"owner.login")
 expiredRestart.registration?.trackerOAuthExpiresAt=nil
 expiredRestart.registration?.trackerOAuthVerifiedAt=Date(timeIntervalSinceNow:-901)
 try expiredRestart.persist()
 await CorporateIdentityGate.shared.block()
 let staleRegistrationCheck=Task { await expiredRestart.verifiedCorporateOAuth() }
 await CorporateIdentityGate.shared.waitForRequest()
 expiredRestart.registration?.deviceID="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
 await CorporateIdentityGate.shared.release()
 let staleRegistrationResult=await staleRegistrationCheck.value
 try check(staleRegistrationResult == nil
           && expiredRestart.registration?.trackerOAuthToken == "token"
           && expiredRestart.registration?.deviceID == "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
           "late forbidden response cannot mutate a replaced registration")
 do { try authenticated.storeVerifiedSession("Session_id=other",login:"other.login"); fatalError("FAIL: wrong corporate owner accepted") } catch { checks+=1 }
 let lastCookieVerification=Date(timeIntervalSinceNow:-901)
 try authenticated.storeVerifiedSession("Session_id=present",login:"owner.login",verifiedAt:lastCookieVerification)
 Network.corporateStatus=500; Network.corporateCalls=0
 let transientCookie=await authenticated.verifyCorporateSession(force:true)
 try check(transientCookie == .ready
           && authenticated.registration?.corporateCookieVerifiedAt == lastCookieVerification
           && authenticated.registration?.corporateIdentityStatus == "ready",
           "transient HTTP identity failure uses bounded cookie owner grace without renewing it")
 let cookieCallsBeforeBackoff=Network.corporateCalls
 let backedOffCookie=await authenticated.verifyCorporateSession()
 try check(backedOffCookie == .ready && Network.corporateCalls == cookieCallsBeforeBackoff,
           "cookie owner grace backs off identity retries during an outage")
 let lastOfflineCookieVerification=Date(timeIntervalSinceNow:-901)
 try authenticated.storeVerifiedSession("Session_id=present",login:"owner.login",verifiedAt:lastOfflineCookieVerification)
 Network.corporateThrows=true
 let offlineCookie=await authenticated.verifyCorporateSession(force:true)
 try check(offlineCookie == .ready
           && authenticated.registration?.corporateCookieVerifiedAt == lastOfflineCookieVerification,
           "network identity failure uses bounded cookie owner grace without renewing it")
 let sharedCookieVerification=Date(timeIntervalSinceNow:-901)
 try authenticated.storeVerifiedSession("Session_id=present",login:"owner.login",
                                        verifiedAt:sharedCookieVerification)
 Network.corporateThrows=false; Network.corporateStatus=500; await CorporateIdentityGate.shared.block()
 let cookieCallsBeforeShared=Network.corporateCalls
 let sharedCookieFirst=Task { await authenticated.verifyCorporateSession(force:true) }
 await CorporateIdentityGate.shared.waitForRequest()
 let sharedCookieSecond=Task { await authenticated.verifyCorporateSession(force:true) }
 try await Task.sleep(nanoseconds:20_000_000)
 try check(Network.corporateCalls == cookieCallsBeforeShared + 1,
           "same-binding cookie verification is a single shared identity request")
 await CorporateIdentityGate.shared.release()
 let sharedCookieFirstResult=await sharedCookieFirst.value
 let sharedCookieSecondResult=await sharedCookieSecond.value
 try check(sharedCookieFirstResult == .ready && sharedCookieSecondResult == .ready
           && authenticated.registration?.corporateCookieVerifiedAt == sharedCookieVerification,
           "same-binding cookie waiters cannot overwrite readiness with a late result")
 Network.corporateStatus=401; await CorporateIdentityGate.shared.block()
 let oldCookieFirst=Task { await authenticated.verifyCorporateSession(force:true) }
 await CorporateIdentityGate.shared.waitForRequest()
 let oldCookieSecond=Task { await authenticated.verifyCorporateSession(force:true) }
 try await Task.sleep(nanoseconds:20_000_000)
 authenticated.onChange = {
  if authenticated.registration?.cookie == "" {
   authenticated.onChange=nil
   try! authenticated.storeVerifiedSession("Session_id=present",login:"owner.login")
  }
 }
 await CorporateIdentityGate.shared.release()
 _ = await oldCookieFirst.value; _ = await oldCookieSecond.value
 try check(authenticated.registration?.cookie == "Session_id=present"
           && authenticated.registration?.corporateIdentityStatus == "ready",
           "late shared denial cannot invalidate a newly stored identical cookie")
 Network.corporateThrows=false; Network.corporateStatus=500
 try authenticated.storeVerifiedSession("Session_id=present",login:"owner.login",
                                        verifiedAt:Date(timeIntervalSinceNow:-3601))
 let expiredCookieGrace=await authenticated.verifyCorporateSession(force:true)
 try check(expiredCookieGrace == .networkUnavailable
           && authenticated.registration?.corporateIdentityStatus == "network_unavailable"
           && authenticated.registration?.corporateCookieVerifiedAt != nil,
           "cookie owner grace expires after one hour without erasing the reusable session")
 try authenticated.storeVerifiedSession("Session_id=present",login:"owner.login",
                                        verifiedAt:Date(timeIntervalSinceNow:-901))
 Network.corporateStatus=200; Network.corporateBody=["dismissed":false]
 let malformedCookieIdentity=await authenticated.verifyCorporateSession(force:true)
 try check(malformedCookieIdentity == .accessDenied
           && authenticated.registration?.corporateIdentityStatus == "access_denied"
           && authenticated.registration?.corporateCookieVerifiedAt != nil,
           "malformed identity success fails closed while retaining the owner-bound session for recheck")
 Network.corporateStatus=500
 let noGraceAfterDenial=await authenticated.verifyCorporateSession(force:true)
 try check(noGraceAfterDenial == .networkUnavailable,
           "a malformed identity response cannot be hidden by later availability grace")
 Network.corporateBody=["login":"owner.login","dismissed":false]
 try authenticated.storeVerifiedSession("Session_id=present",login:"owner.login",
                                        verifiedAt:Date(timeIntervalSinceNow:-901))
 await CorporateIdentityGate.shared.block()
 let cancelledCookieGrace=Task { await authenticated.verifyCorporateSession() }
 await CorporateIdentityGate.shared.waitForRequest(); cancelledCookieGrace.cancel(); await CorporateIdentityGate.shared.release()
 let cancelledCookieResult=await cancelledCookieGrace.value
 try check(cancelledCookieResult == .networkUnavailable
           && authenticated.registration?.corporateIdentityStatus == "ready",
           "cancelled cookie identity refresh cannot consume or replace owner grace")
 try authenticated.storeVerifiedSession("Session_id=present",login:"owner.login",
                                        verifiedAt:Date(timeIntervalSinceNow:-901))
 await CorporateIdentityGate.shared.block()
 let reboundCookieGrace=Task { await authenticated.verifyCorporateSession() }
 await CorporateIdentityGate.shared.waitForRequest()
 authenticated.registration?.deviceID="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
 await CorporateIdentityGate.shared.release()
 let reboundCookieResult=await reboundCookieGrace.value
 try check(reboundCookieResult == .ownerMismatch
           && authenticated.registration?.corporateIdentityStatus == "ready",
           "late cookie identity result cannot cross a changed device binding")
 authenticated.registration?.deviceID=id
 try authenticated.storeVerifiedSession("Session_id=present",login:"owner.login")
 Network.corporateStatus=401
 let unauthorized=await authenticated.verifyCorporateSession(force:true)
 try check(unauthorized == .reauthRequired
           && authenticated.registration?.cookie.isEmpty == true,"401 expires and clears corporate session")
 try authenticated.storeVerifiedSession("Session_id=present",login:"owner.login")
 Network.corporateStatus=403
 let lastForbiddenCookieVerification=authenticated.registration?.corporateCookieVerifiedAt
 let forbidden=await authenticated.verifyCorporateSession(force:true)
 try check(forbidden == .accessDenied
           && authenticated.registration?.corporateIdentityStatus == "access_denied","403 remains distinct access denied")
 let forbiddenRestart=Bridge()
 try check(forbiddenRestart.registration?.cookie == "Session_id=present"
           && forbiddenRestart.registration?.corporateCookieVerifiedAt == lastForbiddenCookieVerification
           && forbiddenRestart.registration?.corporateIdentityStatus == "access_denied",
           "403 keeps the owner-bound Emergency session reusable across restart without advertising readiness")
 Network.corporateStatus=200
 let recoveredForbidden=await forbiddenRestart.verifyCorporateSession(force:true)
 try check(recoveredForbidden == .ready
           && forbiddenRestart.registration?.cookie == "Session_id=present"
           && forbiddenRestart.registration?.corporateIdentityStatus == "ready",
           "a retained denied Emergency session recovers after a later successful owner check")
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
    if "if emergencyAuthorized && canUseEmergency { bridge.startIfNeeded() }" not in fast_path:
        raise SystemExit("FAIL: repeated same-account identity refresh cannot recover a missing Emergency loop")
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
    if (
        "self.emergencyAuthorized" not in refresh_probe
        or "self.activeAccountID == accountID" not in refresh_probe
        or "self.enrollmentRun == enrollment" not in refresh_probe
        or "self.workspace?.session === session" not in refresh_probe
        or "if self.probeRefreshRun == run { self.probeRefreshTask = nil }" not in refresh_probe
    ):
        raise SystemExit("FAIL: probe refresh is not bound to its account/session generation")
    revoked_path = identity.split("if emergencyAuthorized != canUseEmergency", 1)[1].split(
        "notifications.start", 1
    )[0]
    if "else { cancelProbeRefresh(); bridge.stop(); try? bridge.clearProbe() }" not in revoked_path:
        raise SystemExit("FAIL: revoking Emergency access does not cancel an in-flight probe refresh")
    with tempfile.TemporaryDirectory(prefix="robopark-bridge-lifecycle-") as raw:
        root = Path(raw)
        (root / "Sources/BridgeLifecycleChecks").mkdir(parents=True)
        shutil.copytree(MACOS / "Sources/AgentCore", root / "Sources/AgentCore")
        exposed_bridge = bridge_source.replace(
            "private var readyUntil", "var readyUntil"
        ).replace(
            "private var generation", "var generation"
        ).replace(
            "private var jobs", "var jobs"
        ).replace(
            "private func acceptJob", "func acceptJob"
        ).replace(
            "try await self.send(answer, through: channel)",
            "try Task.checkCancellation(); _ = answer",
        )
        (root / "Sources/BridgeLifecycleChecks/Bridge.swift").write_text(exposed_bridge)
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
