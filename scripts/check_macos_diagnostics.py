#!/usr/bin/env python3
"""Hermetic checks for the bounded, redacted macOS diagnostic journal."""
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "apps/macos/Sources/AgentCore/DiagnosticJournal.swift"
RUNNER = ROOT / "apps/macos/Sources/RoboparkAgent/DiagnosticRunner.swift"
PROTOCOL = ROOT / "apps/macos/Sources/AgentCore/Protocol.swift"
CORPORATE_READ = ROOT / "apps/macos/Sources/AgentCore/CorporateRead.swift"
CORPORATE_AUTH = ROOT / "apps/macos/Sources/AgentCore/CorporateAuth.swift"

MAIN = r'''
import Foundation

public struct SignedCatalog: Codable {}

func require(_ value: @autoclosure () -> Bool, _ message: String) {
    guard value() else { fatalError("FAIL: \(message)") }
}

@main struct Check {
    static func main() async throws {
        let journal = DiagnosticJournal(capacity: 100)
        for index in 0..<120 {
            journal.append(service: "server", stage: "tls", status: "ok",
                           code: "n\(index)", elapsedMs: index)
        }
        require(journal.snapshot().count == 100, "ring is capped at one hundred events")
        require(journal.snapshot().first?.id == 21, "ring evicts the oldest event")
        journal.append(service: "https://secret.invalid/?token=x", stage: "tls", status: "ok")
        require(journal.snapshot().count == 100, "unsafe free-form fields are rejected")
        let request = try DiagnosticRequest(["service": "emergency", "route": "direct", "robot": "1170"])
        require(request.robot == "1170", "read-only robot request is accepted")
        require((try? DiagnosticRequest(["service": "emergency", "route": "direct", "robot": "999"])) == nil,
                "robot below the diagnostic range is rejected")
        require((try? DiagnosticRequest(["service": "inventory", "route": "direct", "robot": "3001"])) == nil,
                "robot above the diagnostic range is rejected")
        require((try? DiagnosticRequest(["service": "shell", "route": "direct"])) == nil,
                "arbitrary services are rejected")
        let trackerRequest = try DiagnosticRequest(["service": "tracker", "route": "direct", "robot": "1170"])
        require(trackerRequest.robot == "1170",
                "tracker diagnostics preserve the requested robot")
        let serverRequest = try DiagnosticRequest(["service": "server", "route": "server", "robot": "1170"])
        require(serverRequest.route == "direct",
                "server diagnostics cannot be routed through an arbitrary endpoint")
        let emergency1170 = try EmergencyTarget.normalizeRobot("1170")
        let emergency2000 = try EmergencyTarget.normalizeRobot("2000")
        require(emergency1170 == "YASADR00000001170",
                "short robot 1170 is normalized for Emergency")
        require(emergency2000 == "YASADR00000002000",
                "short robot 2000 is normalized for Emergency")
        let trackerBytes = try JSONSerialization.data(withJSONObject: ["service": "tracker",
            "action": "issues_search", "resource": "search",
            "params": ["query": "(Summary: \"1170\" OR rover: \"a1170\") AND Resolution: empty()",
                       "per_page": 1, "order": ["createdAt"]],
            "owner_user_id": 7, "owner_tracker_login": "owner.test", "requester_user_id": 7])
        let trackerRead = try CorporateReadPolicy.decode(trackerBytes,
            expectedOwnerUserID: 7, expectedOwnerTrackerLogin: "owner.test")
        let trackerPlan = try CorporateReadPolicy.plan(trackerRead)
        require(trackerPlan.method == "POST" && trackerPlan.url.host == CorporateReadPolicy.trackerHost
                && trackerPlan.url.path == "/v2/issues/_search",
                "direct Tracker robot probe is accepted by the real read policy")
        let coordinator = DiagnosticRunCoordinator()
        let started = coordinator.start {
            try? await Task.sleep(nanoseconds: 80_000_000)
            return DiagnosticResult(service: "server", route: "direct", robot: nil,
                status: "ok", httpStatus: 200, code: nil, elapsedMs: 80)
        }
        require(started, "the first diagnostic run starts")
        let running = coordinator.running
        require(running, "POST can return a running snapshot immediately")
        let secondStarted = coordinator.start {
            DiagnosticResult(service: "server", route: "direct", robot: nil,
                status: "ok", httpStatus: 200, code: nil, elapsedMs: 0)
        }
        require(!secondStarted, "a concurrent diagnostic run is rejected")
        try await Task.sleep(nanoseconds: 120_000_000)
        let stillRunning = coordinator.running
        let resultCount = coordinator.results.count
        let finished = !stillRunning && resultCount == 1
        require(finished,
                "the background run publishes one bounded result")
        print("PASS: diagnostic ring, redaction and request validation")
    }
}
'''

def main() -> int:
    runner = RUNNER.read_text()
    required = ["Network.emergency", "corporateInventoryReadiness", "corporateIdentity",
                "session.call", "websocketChallenge"]
    if any(item not in runner for item in required):
        print("FAIL: diagnostic runner is missing a bounded read-only probe")
        return 1
    with tempfile.TemporaryDirectory(prefix="robopark-diagnostics-") as raw:
        folder = Path(raw)
        source = folder / "Sources/Check"
        source.mkdir(parents=True)
        shutil.copy2(SOURCE, source / SOURCE.name)
        shutil.copy2(PROTOCOL, source / PROTOCOL.name)
        shutil.copy2(CORPORATE_READ, source / CORPORATE_READ.name)
        shutil.copy2(CORPORATE_AUTH, source / CORPORATE_AUTH.name)
        (source / "main.swift").write_text(MAIN)
        (folder / "Package.swift").write_text(
            '// swift-tools-version: 5.9\nimport PackageDescription\n'
            'let package = Package(name: "Check", platforms: [.macOS(.v13)], '
            'targets: [.executableTarget(name: "Check")])\n'
        )
        cache = Path("/private/tmp/robopark-diagnostics-check")
        return subprocess.run(["swift", "run", "--package-path", str(folder),
            "--scratch-path", str(cache), "--cache-path", str(cache / "cache"),
            "--config-path", str(cache / "config"), "--security-path", str(cache / "security"),
            "--disable-sandbox", "Check"], check=False).returncode

if __name__ == "__main__":
    raise SystemExit(main())
