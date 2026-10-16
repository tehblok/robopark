#!/usr/bin/env python3
"""Check production heartbeat liveness and replacement-run safety with scaled time."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
MACOS = ROOT / "apps/macos"


def balanced_block(source: str, marker: str) -> str:
    start = source.index(marker)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise RuntimeError(f"unclosed Swift block after {marker!r}")


def production_fragments(path: Path) -> tuple[str, str]:
    source = path.read_text()
    heartbeat = balanced_block(source, "heartbeat = Task { [weak self] in")
    helpers_start = source.index("private func armLivenessTimeout")
    helpers_end = source.index("private func sendHeartbeat", helpers_start)
    helpers = source[helpers_start:helpers_end]
    return heartbeat, helpers


def scaled(fragment: str) -> str:
    result = (
        fragment.replace("URLSessionWebSocketTask", "ControlledChannel")
        .replace("channel.cancel(with: .goingAway, reason: nil)", "channel.cancel()")
        .replace(
            "UInt64(Double.random(in: 8.5...10.5) * 1_000_000_000)",
            "35_000_000",
        )
        .replace("1_000_000_000)", "15_000_000)")
        .replace("10_000_000_000)", "35_000_000)")
        .replace("25_000_000_000)", "120_000_000)")
    )
    for production_delay in ("1_000_000_000)", "10_000_000_000)", "25_000_000_000)"):
        if production_delay in result:
            raise RuntimeError(f"unscaled production delay remains: {production_delay}")
    return result


def old_return(heartbeat: str) -> str:
    mutated, count = re.subn(
        r"guard let pulse = self\.armLivenessTimeout\((.*?)\) else \{.*?\n\s*continue\n\s*\}",
        r"guard let pulse = self.armLivenessTimeout(\1) else { return }",
        heartbeat,
        count=1,
        flags=re.DOTALL,
    )
    if count != 1:
        raise RuntimeError("could not construct the historical heartbeat-return mutant")
    return mutated


def harness_class(name: str, heartbeat: str, helpers: str, emergency: bool) -> str:
    emergency_fields = """
    var managedProbes = true
    func probeIfNeeded() async {}
""" if emergency else """
    var corporateRefreshCount = 0
    func refreshCorporateReadiness(run: UUID, generation: String) { corporateRefreshCount += 1 }
"""
    return f"""
@MainActor final class {name} {{
    var heartbeat: Task<Void, Never>?
    var livenessTimeout: Task<Void, Never>?
    var connectionWatchdog = ConnectionWatchdog()
    var runID = UUID()
    var generation = ""
    var heartbeatAcknowledgements = true
    var heartbeatCount = 0
{emergency_fields}
    func start(run: UUID, nextGeneration: String, channel: ControlledChannel) {{
        runID = run
        generation = nextGeneration
        connectionWatchdog.begin(run: run, generation: nextGeneration)
        {heartbeat}
    }}

    func acknowledge(run: UUID, generation: String) {{
        acknowledgeLiveness(run: run, generation: generation)
    }}

    func stop() {{
        heartbeat?.cancel(); heartbeat = nil
        livenessTimeout?.cancel(); livenessTimeout = nil
        connectionWatchdog.cancel()
    }}

    func sendHeartbeat() async throws {{ heartbeatCount += 1 }}

    {helpers}
}}
"""


def swift_source(mutant: Optional[str]) -> str:
    emergency_heartbeat, emergency_helpers = production_fragments(
        MACOS / "Sources/RoboparkAgent/Bridge.swift"
    )
    compute_heartbeat, compute_helpers = production_fragments(
        MACOS / "Sources/RoboparkAgent/ComputeBridge.swift"
    )
    emergency_heartbeat = scaled(emergency_heartbeat)
    compute_heartbeat = scaled(compute_heartbeat)
    if mutant == "emergency":
        emergency_heartbeat = old_return(emergency_heartbeat)
    elif mutant == "compute":
        compute_heartbeat = old_return(compute_heartbeat)
    emergency_helpers = scaled(emergency_helpers)
    compute_helpers = scaled(compute_helpers)
    return f"""
import Foundation
import AgentCore

@MainActor final class ControlledChannel {{
    private(set) var cancellations = 0
    func cancel() {{ cancellations += 1 }}
    func sendPing(pongReceiveHandler: @escaping @Sendable (Error?) -> Void) {{
        pongReceiveHandler(nil)
    }}
}}

{harness_class("EmergencyHeartbeatHarness", emergency_heartbeat, emergency_helpers, True)}
{harness_class("ComputeHeartbeatHarness", compute_heartbeat, compute_helpers, False)}

@MainActor func waitUntil(_ description: String, timeout: TimeInterval = 0.6,
                          _ predicate: @escaping @MainActor () -> Bool) async throws {{
    let deadline = Date().addingTimeInterval(timeout)
    while !predicate(), Date() < deadline {{
        try await Task.sleep(nanoseconds: 2_000_000)
    }}
    guard predicate() else {{ throw NSError(domain: "HeartbeatRecovery", code: 1,
        userInfo: [NSLocalizedDescriptionKey: description]) }}
}}

@MainActor func exerciseEmergency() async throws {{
    let run = UUID(), generation = "emergency-generation"
    let bridge = EmergencyHeartbeatHarness(), channel = ControlledChannel()
    bridge.start(run: run, nextGeneration: generation, channel: channel)
    try await waitUntil("Emergency did not send its first heartbeat") {{ bridge.heartbeatCount == 1 }}
    try await Task.sleep(nanoseconds: 55_000_000)
    bridge.acknowledge(run: run, generation: generation)
    try await waitUntil("Emergency late ACK did not permit a second heartbeat") {{ bridge.heartbeatCount >= 2 }}
    bridge.stop()

    let noAckRun = UUID(), noAckGeneration = "emergency-no-ack"
    let noAck = EmergencyHeartbeatHarness(), dead = ControlledChannel()
    noAck.start(run: noAckRun, nextGeneration: noAckGeneration, channel: dead)
    try await waitUntil("Emergency did not send no-ACK heartbeat") {{ noAck.heartbeatCount == 1 }}
    try await waitUntil("Emergency no-ACK deadline did not cancel its socket") {{ dead.cancellations == 1 }}
    noAck.stop()
    // The outer connect loop is outside this extracted harness. Starting its
    // replacement run here verifies that stale heartbeat cleanup cannot block it.
    let replacement = ControlledChannel()
    noAck.start(run: UUID(), nextGeneration: "emergency-reconnected", channel: replacement)
    try await waitUntil("Emergency replacement run did not heartbeat after cancellation") {{ noAck.heartbeatCount >= 2 }}
    noAck.stop()
}}

@MainActor func exerciseCompute() async throws {{
    let run = UUID(), generation = "compute-generation"
    let bridge = ComputeHeartbeatHarness(), channel = ControlledChannel()
    bridge.start(run: run, nextGeneration: generation, channel: channel)
    try await waitUntil("Compute did not send its first heartbeat") {{ bridge.heartbeatCount == 1 }}
    guard bridge.corporateRefreshCount == 1 else {{ throw NSError(domain: "HeartbeatRecovery", code: 2,
        userInfo: [NSLocalizedDescriptionKey: "Compute did not schedule corporate readiness after transport heartbeat"]) }}
    try await Task.sleep(nanoseconds: 55_000_000)
    bridge.acknowledge(run: run, generation: generation)
    try await waitUntil("Compute late ACK did not permit a second heartbeat") {{ bridge.heartbeatCount >= 2 }}
    bridge.stop()

    let noAckRun = UUID(), noAckGeneration = "compute-no-ack"
    let noAck = ComputeHeartbeatHarness(), dead = ControlledChannel()
    noAck.start(run: noAckRun, nextGeneration: noAckGeneration, channel: dead)
    try await waitUntil("Compute did not send no-ACK heartbeat") {{ noAck.heartbeatCount == 1 }}
    try await waitUntil("Compute no-ACK deadline did not cancel its socket") {{ dead.cancellations == 1 }}
    noAck.stop()
    // The production outer loop owns automatic reconnect; this checks the
    // replacement heartbeat run after that loop creates a new connection.
    let replacement = ControlledChannel()
    noAck.start(run: UUID(), nextGeneration: "compute-reconnected", channel: replacement)
    try await waitUntil("Compute replacement run did not heartbeat after cancellation") {{ noAck.heartbeatCount >= 2 }}
    noAck.stop()
}}

do {{
    try await exerciseEmergency()
    try await exerciseCompute()
    print("PASS: production heartbeat loops survive late ACK, cancel no-ACK sockets, and accept replacement runs")
}} catch {{
    fputs("FAIL: \\(error.localizedDescription)\\n", stderr)
    exit(EXIT_FAILURE)
}}
"""


def run_variant(root: Path, mutant: Optional[str]) -> subprocess.CompletedProcess[str]:
    main = root / "Sources/HeartbeatRecoveryChecks/main.swift"
    main.write_text(swift_source(mutant))
    return subprocess.run(
        [
            "swift", "run", "--package-path", str(root),
            "--scratch-path", str(root / ".build"),
            "--cache-path", str(root / ".cache"),
            "--config-path", str(root / ".config"),
            "--security-path", str(root / ".security"),
            "--disable-sandbox", "HeartbeatRecoveryChecks",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="robopark-heartbeat-recovery-") as raw:
        root = Path(raw)
        shutil.copytree(MACOS / "Sources/AgentCore", root / "Sources/AgentCore")
        (root / "Sources/HeartbeatRecoveryChecks").mkdir(parents=True)
        (root / "Package.swift").write_text("""// swift-tools-version: 5.9
import PackageDescription
let package = Package(name: "HeartbeatRecoveryChecks", platforms: [.macOS(.v13)], targets: [
    .target(name: "AgentCore"),
    .executableTarget(name: "HeartbeatRecoveryChecks", dependencies: ["AgentCore"]),
])
""")
        for channel, expected in (
            ("emergency", "Emergency late ACK did not permit a second heartbeat"),
            ("compute", "Compute late ACK did not permit a second heartbeat"),
        ):
            red = run_variant(root, mutant=channel)
            if red.returncode == 0:
                print(red.stdout)
                raise SystemExit(f"FAIL: historical {channel} return mutant was not detected")
            if expected not in red.stdout:
                print(red.stdout)
                raise SystemExit(f"FAIL: historical {channel} mutant failed for an unrelated reason")
            print(f"RED PASS: historical {channel} return mutant loses late ACK recovery")

        green = run_variant(root, mutant=None)
        print(green.stdout, end="")
        return green.returncode


if __name__ == "__main__":
    raise SystemExit(main())
