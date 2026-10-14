#!/usr/bin/env python3
"""Hermetic HTTP regression for the native corporate read executor."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MACOS = ROOT / "apps" / "macos"
PACKAGE = """// swift-tools-version: 5.9
import PackageDescription
let package = Package(name: "CorporateReadChecks", platforms: [.macOS(.v13)], targets: [
 .target(name: "AgentCore"), .executableTarget(name: "CorporateReadChecks", dependencies: ["AgentCore"])])
"""
MAIN = r'''import Foundation
import AgentCore

final class StubProtocol: URLProtocol {
    enum Mode { case reply(Int, Data, [String:String]); case redirect(URL); case delayed(Data); case fail(Error) }
    static let lock = NSLock()
    static var mode: Mode = .reply(200, Data("{}".utf8), [:])
    static var requests: [URLRequest] = []
    private var pending: DispatchWorkItem?
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.lock.lock(); let mode = Self.mode; Self.requests.append(request); Self.lock.unlock()
        switch mode {
        case let .reply(status, body, headers):
            let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: "HTTP/1.1", headerFields: headers)!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: body); client?.urlProtocolDidFinishLoading(self)
        case let .redirect(url):
            let response = HTTPURLResponse(url: request.url!, statusCode: 302, httpVersion: "HTTP/1.1", headerFields: ["Location":url.absoluteString])!
            client?.urlProtocol(self, wasRedirectedTo: URLRequest(url:url), redirectResponse:response)
            client?.urlProtocolDidFinishLoading(self)
        case let .delayed(body):
            let work = DispatchWorkItem { [weak self] in
                guard let self, !Task.isCancelled else { return }
                let response = HTTPURLResponse(url:self.request.url!,statusCode:200,httpVersion:"HTTP/1.1",headerFields:[:])!
                self.client?.urlProtocol(self,didReceive:response,cacheStoragePolicy:.notAllowed)
                self.client?.urlProtocol(self,didLoad:body); self.client?.urlProtocolDidFinishLoading(self)
            }
            pending=work; DispatchQueue.global().asyncAfter(deadline:.now()+1,execute:work)
        case let .fail(error): client?.urlProtocol(self, didFailWithError:error)
        }
    }
    override func stopLoading() { pending?.cancel(); pending=nil }
}

func envelope() throws -> Data { try JSONSerialization.data(withJSONObject:[
    "service":"tracker","action":"issue_read","resource":"TEST-1","params":[:],
    "owner_user_id":7,"owner_tracker_login":"owner.login","requester_user_id":7]) }
func writeEnvelope() throws -> Data { try JSONSerialization.data(withJSONObject:[
    "service":"tracker","action":"comment_create","resource":"TEST-1","params":["text":"hello"],
    "owner_user_id":7,"owner_tracker_login":"owner.login","requester_user_id":7,
    "command_id":"11111111-1111-4111-8111-111111111111","step_id":"comment:1"]) }
func check(_ value:@autoclosure() -> Bool,_ message:String) { if !value() { fatalError("FAIL: \(message)") } }

@main struct Run {
 static func main() async throws {
  let request=try CorporateReadPolicy.decode(try envelope(),expectedOwnerUserID:7,expectedOwnerTrackerLogin:"owner.login")
  let plan=try CorporateReadPolicy.plan(request)
  let config=URLSessionConfiguration.ephemeral; config.protocolClasses=[StubProtocol.self]
  let session=URLSession(configuration:config,delegate:Network.policy,delegateQueue:nil)
  defer { session.invalidateAndCancel() }
  StubProtocol.mode = .reply(200,Data("{\"key\":\"TEST-1\"}".utf8),["Content-Type":"application/json","X-Total-Count":"1","Set-Cookie":"secret"])
  let result=try await Network.corporateRead(plan:plan,oauthToken:"token",request:request,sessionOverride:session)
  check((result["payload"] as? [String:String])?["key"] == "TEST-1","stubbed 2xx JSON response")
  let sent=StubProtocol.requests.last!
  check(sent.url?.host == CorporateReadPolicy.trackerHost && sent.httpMethod == "GET","fixed host and method")
  check(sent.value(forHTTPHeaderField:"Authorization") == "OAuth token","OAuth header")
  check((result["headers"] as? [String:String]) == ["x-total-count":"1"],"pagination-only headers")

  StubProtocol.mode = .reply(403,Data("{}".utf8),["Content-Type":"application/json"])
  do { _=try await Network.corporateRead(plan:plan,oauthToken:"token",request:request,sessionOverride:session); fatalError("FAIL: 403 accepted") }
  catch let error as CorporateReadError { check(error == .accessDenied,"403 mapping") }
  StubProtocol.mode = .reply(200,Data(repeating:120,count:CorporateReadPolicy.maxResponseBytes+1),["Content-Type":"application/json"])
  do { _=try await Network.corporateRead(plan:plan,oauthToken:"token",request:request,sessionOverride:session); fatalError("FAIL: oversize accepted") }
  catch let error as CorporateReadError { check(error == .tooLarge,"stream response bound") }
  StubProtocol.mode = .redirect(URL(string:"https://evil.example/steal")!)
  do { _=try await Network.corporateRead(plan:plan,oauthToken:"token",request:request,sessionOverride:session); fatalError("FAIL: redirect accepted") }
  catch { check(StubProtocol.requests.last?.url?.host != "evil.example","credential redirect denied") }
  StubProtocol.mode = .delayed(Data("{}".utf8))
  let task=Task { try await Network.corporateRead(plan:plan,oauthToken:"token",request:request,sessionOverride:session) }
  try await Task.sleep(nanoseconds:50_000_000); task.cancel()
  do { _=try await task.value; fatalError("FAIL: cancelled request completed") } catch { }
  StubProtocol.mode = .reply(200,Data("{\"name\":\"a664\"}".utf8),["Content-Type":"application/json"])
  let inventoryReady = await Network.corporateInventoryReadiness(
      serverAuthorizedVIN:"YASADR00000000664",oauthToken:"token",sessionOverride:session)
  check(inventoryReady == .ready,"server-authorized Inventory rover proves host ACL")
  let inventorySent=StubProtocol.requests.last!
  check(inventorySent.url?.absoluteString == "https://inventory.sdc.yandex-team.ru/api/v1/rovers/a664/"
        && inventorySent.httpMethod == "GET"
        && inventorySent.value(forHTTPHeaderField:"Authorization") == "OAuth token",
        "Inventory readiness cannot select an arbitrary host or rover")
  StubProtocol.mode = .reply(403,Data("{}".utf8),["Content-Type":"application/json"])
  let inventoryDenied = await Network.corporateInventoryReadiness(serverAuthorizedVIN:"YASADR00000000664",
      oauthToken:"token",sessionOverride:session)
  check(inventoryDenied == .denied,"Inventory 403 is explicit access denial")
  StubProtocol.mode = .reply(500,Data("{}".utf8),["Content-Type":"application/json"])
  let inventoryUnreachable = await Network.corporateInventoryReadiness(serverAuthorizedVIN:"YASADR00000000664",
      oauthToken:"token",sessionOverride:session)
  check(inventoryUnreachable == .unreachable,"Inventory upstream failure is explicit")
  let writeRequest=try CorporateWritePolicy.decode(try writeEnvelope(),expectedOwnerUserID:7,expectedOwnerTrackerLogin:"owner.login")
  let writePlan=try CorporateWritePolicy.plan(writeRequest)
  StubProtocol.mode = .reply(201,Data("{\"id\":\"comment-1\"}".utf8),["Content-Type":"application/json"])
  let written=try await Network.corporateWrite(plan:writePlan,oauthToken:"token",request:writeRequest,sessionOverride:session)
  check((written["payload"] as? [String:String])?["id"] == "comment-1","write response projection")
  let writeSent=StubProtocol.requests.last!
  check(writeSent.url?.absoluteString == "https://st-api.yandex-team.ru/v2/issues/TEST-1/comments"
        && writeSent.httpMethod == "POST" && writeSent.value(forHTTPHeaderField:"Authorization") == "OAuth token",
        "write fixed endpoint and owner OAuth")
  StubProtocol.mode = .redirect(URL(string:"https://evil.example/write")!)
  do { _=try await Network.corporateWrite(plan:writePlan,oauthToken:"token",request:writeRequest,sessionOverride:session); fatalError("FAIL: write redirect accepted") }
  catch { check(StubProtocol.requests.last?.url?.host != "evil.example","write redirect denied") }
  StubProtocol.mode = .delayed(Data("{\"id\":\"late\"}".utf8))
  let writeTask=Task { try await Network.corporateWrite(plan:writePlan,oauthToken:"token",request:writeRequest,sessionOverride:session) }
  try await Task.sleep(nanoseconds:50_000_000); writeTask.cancel()
  do { _=try await writeTask.value; fatalError("FAIL: cancelled write completed") }
  catch let error as CorporateWriteError { check(error == .outcomeUnknown,"cancelled write is outcome_unknown") }
  StubProtocol.mode = .fail(URLError(.timedOut))
  do { _=try await Network.corporateWrite(plan:writePlan,oauthToken:"token",request:writeRequest,sessionOverride:session); fatalError("FAIL: timed out write completed") }
  catch let error as CorporateWriteError { check(error == .outcomeUnknown,"timed out write is outcome_unknown") }
  print("PASS: native corporate read/write fixed-host HTTP, auth, redirect, cancellation and response bounds")
 }
}
'''


def main() -> int:
    compute = (MACOS / "Sources/RoboparkAgent/ComputeBridge.swift").read_text()
    bridge = (MACOS / "Sources/RoboparkAgent/Bridge.swift").read_text()
    corporate = compute.split("private func acceptCorporateJob", 1)[1].split(
        "private func cancel", 1
    )[0]
    write = compute.split("private func acceptCorporateWriteJob", 1)[1].split(
        "private func cancel", 1
    )[0]
    cancel = compute.split("private func cancel", 1)[1].split(
        "private func telemetry", 1
    )[0]
    if (
        "CorporateReadPolicy.admits(inflight: corporateJobs.count)" not in corporate
        or "corporateJobs.removeValue(forKey: attempt)?.cancel()" not in cancel
        or 'message["payload_sha256"]' not in corporate
    ):
        raise SystemExit("FAIL: two-slot admission, digest, or cancellation gate regressed")
    if (
        "if corporateWriteAdmission.attemptID != nil" not in write
        or "CorporateWriteError.busy.wireCode" not in write
        or write.find("storeReceipt(executing") > write.find("Network.corporateWrite")
        or ".state = .unknown" not in write
        or "corporateWriteJob?.task.cancel()" not in cancel
    ):
        raise SystemExit("FAIL: one-slot receipt-before-HTTP or outcome-unknown write gate regressed")
    if (
        "await bridge.verifyInventoryReadiness()" not in compute
        or '"corporate_inventory_status": inventoryStatus.rawValue' not in compute
        or "registration?.inventoryOAuthVerifiedAt = nil" not in bridge.split("func setProbe", 1)[1].split("func pause", 1)[0]
    ):
        raise SystemExit("FAIL: Inventory ACL readiness heartbeat or probe invalidation regressed")
    heartbeat = compute.split("private func sendHeartbeat", 1)[1].split("private func acceptJob", 1)[0]
    if (
        '"active_jobs": active' not in heartbeat
        or '"corporate_read_capacity": corporateReady ? 2 : 0' not in heartbeat
        or '"corporate_write_capacity": corporateReady ? 1 : 0' not in heartbeat
        or '"corporate_active_jobs": corporateJobs.count' not in heartbeat
        or '"corporate_active_writes": corporateWriteJob == nil ? 0 : 1' not in heartbeat
        or '"paused": paused' not in heartbeat
        or "max(computeCapacity, 2)" in heartbeat
    ):
        raise SystemExit("FAIL: CPU and corporate I/O heartbeat capacities are conflated")
    with tempfile.TemporaryDirectory(prefix="robopark-corporate-read-") as raw:
        root = Path(raw)
        (root / "Sources/CorporateReadChecks").mkdir(parents=True)
        shutil.copytree(MACOS / "Sources/AgentCore", root / "Sources/AgentCore")
        shutil.copy2(MACOS / "Sources/RoboparkAgent/Networking.swift", root / "Sources/CorporateReadChecks/Networking.swift")
        (root / "Sources/CorporateReadChecks/main.swift").write_text(MAIN)
        (root / "Package.swift").write_text(PACKAGE)
        cache = Path("/private/tmp/robopark-corporate-read-checks")
        cache.mkdir(parents=True, exist_ok=True)
        return subprocess.run(["swift","run","--package-path",str(root),"--scratch-path",str(cache),
            "--cache-path",str(cache/"cache"),"--config-path",str(cache/"config"),
            "--security-path",str(cache/"security"),"--disable-sandbox","CorporateReadChecks"]).returncode


if __name__ == "__main__":
    raise SystemExit(main())
