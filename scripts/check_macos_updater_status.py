#!/usr/bin/env python3
"""Compile Updater.swift and verify timestamp-ordered helper status reconciliation."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
MACOS = ROOT / "apps/macos"

PACKAGE = """// swift-tools-version: 5.9
import PackageDescription
let package = Package(name: "UpdaterStatusChecks", platforms: [.macOS(.v13)], targets: [
 .target(name: "AgentCore"),
 .executableTarget(name: "UpdaterStatusChecks", dependencies: ["AgentCore"]),
])
"""

AGENT_CORE = r"""import Foundation
public enum AgentError: Error { case invalidCatalog, wrongServer }
public enum NetworkFailure: Error { case network }
public struct ServerOrigin {
 public let url: URL
 public init(_ value: String) throws { url = URL(string: value)! }
 public func api(_ path: String) -> URL { url.appendingPathComponent(path) }
}
public struct ReleaseVersion: Comparable {
 let value: String
 public init(_ value: String) throws { self.value = value }
 public static func < (lhs: Self, rhs: Self) -> Bool { lhs.value < rhs.value }
}
public struct VerifiedUpdate { public let generation: Int64; public let target: UpdateTarget }
public struct UpdateTarget { public let version: String }
public struct UpdateVerifier {
 public init(publicKey: Data) throws {}
 public func verify(_ bytes: Data, now: Int64, minimumGeneration: Int64,
                    channel: String) throws -> VerifiedUpdate {
  VerifiedUpdate(generation: minimumGeneration, target: UpdateTarget(version: "development"))
 }
}
public enum EmbeddedTrust { public static func publicKey() throws -> Data { Data(repeating: 1, count: 32) } }
"""

SHIMS = r"""import Foundation
import AgentCore

struct Registration {
 var minimumGeneration: Int64 = 0
 var origin = "https://roboparktest.ru.tuna.am"
 var instanceID = "instance"
 var signedProfile = ""
 var channel = "rc"
}
struct Info { let environment = "test"; let instance_id = "instance"; let signed_profile = "signed" }
struct Assignment { let channel = "rc" }
@MainActor final class Bridge {
 var registration: Registration? = Registration()
 func verifiedInfo(_ origin: ServerOrigin) async throws -> (Info, Assignment) { (Info(), Assignment()) }
 func persist() throws {}
}
enum Network {
 static func request(_ url: URL, limit: Int) async throws -> (Data, HTTPURLResponse) {
  throw NetworkFailure.network
 }
}
"""

MAIN = r"""import Foundation

func check(_ value: @autoclosure () -> Bool, _ message: String) {
 guard value() else { fatalError("FAIL: \(message)") }
}
func writeStatus(_ url: URL, status: String, message: String, checkedAt: Int64,
                 updatedAtMs: Int64? = nil) throws {
 var value: [String: Any] = [
  "status": status, "message": message, "installed_version": "development",
  "target_version": "development", "checked_at": checkedAt,
 ]
 if let updatedAtMs { value["updated_at_ms"] = updatedAtMs }
 try JSONSerialization.data(withJSONObject: value).write(to: url, options: .atomic)
}

@main struct Run {
 @MainActor static func main() async throws {
  let root = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
   .appendingPathComponent(UUID().uuidString, isDirectory: true)
  try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
  defer { try? FileManager.default.removeItem(at: root) }
  let statusURL = root.appendingPathComponent("status.json")
  let now = Int64(Date().timeIntervalSince1970)
  try writeStatus(statusURL, status: "current", message: "old helper current", checkedAt: now - 10)
  let updater = Updater(bridge: Bridge(), statusURL: statusURL)
  await updater.check()
  check(updater.statusState == "error", "fresh app check failure is an error")
  let failureMessage = updater.message
  let localFailureAt = updater.lastStatusAt!
  try writeStatus(statusURL, status: "current", message: "equal-time stale helper",
                  checkedAt: now - 10, updatedAtMs: localFailureAt)
  updater.refreshStatus()
  check(updater.statusState == "error" && updater.message == failureMessage,
        "equal-time helper status cannot hide a fresh local check failure")
  try writeStatus(statusURL, status: "current", message: "Установлена актуальная версия",
                  checkedAt: now - 10, updatedAtMs: localFailureAt + 1)
  updater.refreshStatus()
  check(updater.statusState == "current" && updater.message == "Установлена актуальная версия",
        "newer successful helper status clears an older app check failure")
  try writeStatus(statusURL, status: "failed", message: "helper failed",
                  checkedAt: now - 10, updatedAtMs: localFailureAt + 2)
  updater.refreshStatus()
  check(updater.statusState == "error" && updater.message == "helper failed",
        "newer helper failure remains visible as an error")
  let progressURL = root.appendingPathComponent("progress-status.json")
  let cycleStartedAt = now - 20
  try writeStatus(progressURL, status: "downloading", message: "Загружается обновление",
                  checkedAt: cycleStartedAt)
  let progressing = Updater(bridge: Bridge(), statusURL: progressURL)
  progressing.refreshStatus()
  check(progressing.message == "Загружается обновление", "helper downloading stage is visible")
  await progressing.check()
  check(progressing.statusState == "error", "local failure after downloading is visible")
  let progressFailureAt = progressing.lastStatusAt!
  try writeStatus(progressURL, status: "installed", message: "Установлена актуальная версия",
                  checkedAt: cycleStartedAt, updatedAtMs: progressFailureAt + 1)
  progressing.refreshStatus()
  check(progressing.statusState == "current" && progressing.message == "Установлена актуальная версия",
        "fresh installed write advances after local failure despite old cycle checked_at")
  print("PASS: timestamp-ordered macOS updater status reconciliation")
 }
}
"""


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="robopark-updater-status-") as raw:
        root = Path(raw)
        (root / "Sources/AgentCore").mkdir(parents=True)
        (root / "Sources/UpdaterStatusChecks").mkdir(parents=True)
        (root / "Package.swift").write_text(PACKAGE)
        (root / "Sources/AgentCore/Core.swift").write_text(AGENT_CORE)
        updater_source = (MACOS / "Sources/RoboparkAgent/Updater.swift").read_text().replace(
            "private var lastStatusAt", "var lastStatusAt"
        )
        (root / "Sources/UpdaterStatusChecks/Updater.swift").write_text(updater_source)
        (root / "Sources/UpdaterStatusChecks/Shims.swift").write_text(SHIMS)
        (root / "Sources/UpdaterStatusChecks/main.swift").write_text(MAIN)
        cache = Path("/private/tmp/robopark-updater-status-checks")
        cache.mkdir(parents=True, exist_ok=True)
        return subprocess.run(
            [
                "swift", "run", "--package-path", str(root), "--scratch-path", str(cache),
                "--cache-path", str(cache / "cache"), "--config-path", str(cache / "config"),
                "--security-path", str(cache / "security"), "--disable-sandbox",
                "UpdaterStatusChecks",
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
