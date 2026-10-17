#!/usr/bin/env python3
"""Regression checks for non-blocking cached macOS autostart status."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "apps/macos/Sources/RoboparkAgent/Autostart.swift"
APPLICATION = ROOT / "apps/macos/Sources/RoboparkAgent/Application.swift"

MAIN = r'''import Foundation

actor FakeLaunchctl {
    private(set) var calls: [[String]] = []
    private var disabled = false

    func run(_ arguments: [String], timeout: TimeInterval) async throws -> String {
        calls.append(arguments)
        try await Task.sleep(nanoseconds: 80_000_000)
        if arguments.first == "disable" { disabled = true }
        if arguments.first == "enable" { disabled = false }
        return arguments.first == "print-disabled" && disabled
            ? "\"ru.robopark.agent\" => disabled" : ""
    }

    func count(_ command: String) -> Int { calls.filter { $0.first == command }.count }
}

@main struct Checks {
    @MainActor static func main() async throws {
        for (state, expected) in [("true", false), ("disabled", false), ("false", true), ("enabled", true)] {
            precondition(AutostartController.parseEnabled("\"ru.robopark.agent\" => " + state) == expected)
        }
        precondition(AutostartController.parseEnabled("\"ru.robopark.agent.other\" => disabled") == true)
        precondition(AutostartController.parseEnabled("\"ru.robopark.agent\" => unknown") == nil)
        let fake = FakeLaunchctl()
        let controller = AutostartController(commandTimeout: 0.2) { arguments, timeout in
            try await fake.run(arguments, timeout: timeout)
        }

        precondition(controller.enabled, "cached default preserves prior fail-open behavior")
        controller.refresh()
        controller.refresh()
        precondition(controller.enabled, "cached reads do not wait for launchctl")
        try await Task.sleep(nanoseconds: 180_000_000)
        let initialRefreshes = await fake.count("print-disabled")
        precondition(initialRefreshes == 1, "concurrent refreshes coalesce")

        try await controller.setEnabled(false)
        precondition(!controller.enabled, "successful explicit action updates cache immediately")
        try await Task.sleep(nanoseconds: 180_000_000)
        let disables = await fake.count("disable")
        let totalRefreshes = await fake.count("print-disabled")
        precondition(disables == 1, "explicit action executes once")
        precondition(totalRefreshes == 2, "explicit action refreshes cached truth")
        precondition(!controller.enabled, "refresh preserves disabled launchctl state")

        let failedToggle = AutostartController(commandTimeout: 0.2) { arguments, _ in
            if arguments.first == "disable" { throw NSError(domain: "fixture", code: 110) }
            return "\"ru.robopark.agent\" => disabled"
        }
        do { try await failedToggle.setEnabled(false); fatalError("expected timeout") } catch {}
        try await Task.sleep(nanoseconds: 20_000_000)
        precondition(!failedToggle.enabled, "failed mutation must refresh actual launchctl state")

        let started = Date()
        do {
            _ = try await AutostartController.runCommand(
                executableURL: URL(fileURLWithPath: "/bin/sleep"), arguments: ["2"], timeout: 0.05
            )
            fatalError("timeout accepted a hung command")
        } catch {}
        precondition(Date().timeIntervalSince(started) < 1, "launchctl runner has a bounded timeout")
        print("PASS: cached, coalesced and bounded autostart checks")
    }
}
'''


def main() -> int:
    application = APPLICATION.read_text()
    if "autostart.isEnabled()" in application:
        raise SystemExit("FAIL: render/status still execute launchctl synchronously")
    if "autostart.refresh()" not in application:
        raise SystemExit("FAIL: startup does not refresh cached autostart state")
    with tempfile.TemporaryDirectory(prefix="robopark-autostart-") as raw:
        root = Path(raw)
        shutil.copy2(SOURCE, root / "Autostart.swift")
        (root / "main.swift").write_text(MAIN)
        binary = root / "checks"
        build = subprocess.run(
            ["swiftc", "-parse-as-library", str(root / "Autostart.swift"), str(root / "main.swift"), "-o", str(binary)],
            check=False,
            env={**os.environ, "CLANG_MODULE_CACHE_PATH": "/private/tmp/robopark-autostart-clang"},
        )
        if build.returncode:
            return build.returncode
        return subprocess.run([str(binary)], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
