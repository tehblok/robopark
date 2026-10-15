#!/usr/bin/env python3
"""Hermetic contract checks for the authenticated desktop agent status API."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "apps/macos/Sources/RoboparkAgent/AgentStatus.swift"
APPLICATION = ROOT / "apps/macos/Sources/RoboparkAgent/Application.swift"

MAIN = r'''import Foundation

var checks = 0
func check(_ condition: @autoclosure () -> Bool, _ message: String) {
    guard condition() else { fatalError("FAIL: \(message)") }
    checks += 1
}

let timestamp = Date(timeIntervalSince1970: 1_797_000_000)
let status = DesktopAgentStatus(snapshot: AgentStatusSnapshot(
    accountState: "signed_in", accountLabel: "Royal", accountRole: "royal", registered: true,
    serverState: "connected", serverLastSuccessAt: timestamp,
    ownerLoginConfigured: true, probeConfigured: true,
    tracker: "ready", inventory: "unverified", emergency: "ready",
    corporateChecking: false, corporateMessage: "Inventory ещё не проверен",
    corporateCanLogin: true, autostartSupported: true, autostartEnabled: true,
    paused: false, legacyWorkspaceAvailable: true,
    updateState: "available", currentVersion: "0.2.3-rc.4",
    availableVersion: "0.2.3-rc.5", updateMessage: "Доступно обновление"
))
let object = status.jsonObject
check(object["version"] as? Int == 1, "contract version is stable")
check(object["sync"] is NSNull, "native service never fabricates browser sync counts")
let account = object["account"] as! [String: Any]
check(account["state"] as? String == "signed_in" && account["role"] as? String == "royal",
      "account state and role are exposed without credentials")
let server = object["server"] as! [String: Any]
check(server["state"] as? String == "connected" && server["lastSuccessAt"] is String,
      "server transport and last success are separate")
let corporate = object["corporate"] as! [String: Any]
check(corporate["state"] as? String == "checking", "partially verified services remain checking")
check(corporate["inventory"] as? String == "unverified", "unknown Inventory readiness is explicit")
check(object["legacyWorkspaceAvailable"] as? Bool == true, "legacy WebKit data availability is explicit")
let encoded = String(data: try! JSONSerialization.data(withJSONObject: object), encoding: .utf8)!
for secret in ["cookie", "token", "privateKey", "oauth"] {
    check(!encoded.lowercased().contains(secret.lowercased()), "status contains no \(secret)")
}

check(try! DesktopAgentAction(body: ["action": "connect"]) == .connect,
      "connect action accepts no fabricated enabled value")
check(try! DesktopAgentAction(body: ["action": "autostart", "enabled": true]) == .autostart(true),
      "autostart requires and preserves enabled")
check(try! DesktopAgentAction(body: ["action": "pause", "enabled": false]) == .pause(false),
      "pause requires and preserves enabled")
check(try! DesktopAgentAction(body: ["action": "openLegacyWorkspace"]) == .openLegacyWorkspace,
      "legacy workspace action is explicit and parameter-free")
for invalid in [
    [:], ["action": "unknown"], ["action": "autostart"],
    ["action": "pause", "enabled": "yes"], ["action": "connect", "enabled": true]
] as [[String: Any]] {
    do { _ = try DesktopAgentAction(body: invalid); fatalError("FAIL: invalid action accepted") }
    catch { checks += 1 }
}

print("PASS: \(checks) desktop status and strict action contract checks")
'''


def main() -> int:
    application = APPLICATION.read_text()
    required = [
        "startWorkspaceService()",
        "NSWorkspace.shared.open(target)",
        "workspace.launchURL(route: route)",
        "workspace.launchURL(route: nil)",
        'presentWorkspace(route: "/device")',
        "server.agentStatus =",
        "server.agentAction =",
        "await task.value",
    ]
    for marker in required:
        if marker not in application:
            raise SystemExit(f"FAIL: native browser/status lifecycle missing {marker}")
    for forbidden in ["workspace.sessionCookie", "var workspaceWindow", "var workspaceView"]:
        if forbidden in application:
            raise SystemExit(f"FAIL: native WK workspace remains in main browser flow: {forbidden}")
    corporate_login = application.split("func openEmergencyLogin()", 1)[1].split(
        "private func verifyCorporateIdentity", 1
    )[0]
    if "emergencyAuthorized" in corporate_login:
        raise SystemExit("FAIL: operator/admin corporate setup is incorrectly gated by Emergency permission")
    with tempfile.TemporaryDirectory(prefix="robopark-agent-status-") as raw:
        root = Path(raw)
        shutil.copy2(SOURCE, root / "AgentStatus.swift")
        (root / "main.swift").write_text(MAIN)
        binary = root / "checks"
        build = subprocess.run(
            ["swiftc", str(root / "AgentStatus.swift"), str(root / "main.swift"), "-o", str(binary)],
            check=False,
            env={**os.environ, "CLANG_MODULE_CACHE_PATH": "/private/tmp/robopark-agent-status-clang"},
        )
        if build.returncode:
            return build.returncode
        return subprocess.run([str(binary)], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
