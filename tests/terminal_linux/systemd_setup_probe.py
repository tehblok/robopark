"""Run the destructive systemd-255 terminal setup regression in a disposable VM."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

MARKER = Path("/run/robopark-terminal-test-vm")
UNIT = Path("/etc/systemd/system/robopark-terminal-setup.service")
DROP_IN = Path(
    "/etc/systemd/system/robopark-terminal-setup.service.d/10-capability-probe.conf"
)
CAPABILITY_OUTPUT = Path("/run/robopark-terminal-setup-capabilities")
PROBE = Path("/usr/local/lib/robopark-terminal-setup-capability-probe")
PROPERTIES = (
    "Result",
    "ExecMainStatus",
    "User",
    "NoNewPrivileges",
    "PrivateTmp",
    "ProtectHome",
    "RestrictAddressFamilies",
    "AmbientCapabilities",
    "UMask",
    "LimitCORE",
)
EXPECTED_HARDENING = {
    "NoNewPrivileges": "yes",
    "PrivateTmp": "yes",
    "ProtectHome": "yes",
    "RestrictAddressFamilies": "AF_UNIX",
    "AmbientCapabilities": "",
    "UMask": "0077",
    "LimitCORE": "0",
}


def run(*command: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, text=True, capture_output=True)


def require_disposable_vm() -> None:
    if sys.platform != "linux" or os.geteuid() != 0:
        raise RuntimeError("linux_root_required")
    if Path("/proc/1/comm").read_text().strip() != "systemd":
        raise RuntimeError("systemd_pid1_required")
    value = MARKER.lstat()
    if (
        not stat.S_ISREG(value.st_mode)
        or stat.S_IMODE(value.st_mode) != 0o600
        or value.st_uid != 0
    ):
        raise RuntimeError("disposable_vm_marker_unsafe")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def extract_release(ota: Path, destination: Path) -> Path:
    destination.mkdir(parents=True)
    with zipfile.ZipFile(ota) as archive:
        root = destination.resolve()
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if root not in target.parents and target != root:
                raise RuntimeError("ota_unsafe_path")
        archive.extractall(destination)
    release = destination / "release"
    if not (release / "deploy/host/robopark").is_file():
        raise RuntimeError("ota_host_helper_missing")
    if not (release / "deploy/systemd/robopark-terminal-setup.service").is_file():
        raise RuntimeError("ota_setup_unit_missing")
    return release


def replace_symlink(link: Path, target: Path) -> None:
    if link.is_symlink():
        link.unlink()
    elif link.exists():
        raise RuntimeError(f"fixture_path_exists:{link}")
    link.symlink_to(target)


def install_probe() -> None:
    PROBE.parent.mkdir(parents=True, exist_ok=True)
    PROBE.write_text(
        "#!/bin/sh\n"
        "{ id -u; awk '/^(CapEff|NoNewPrivs|Seccomp):/ {print}' /proc/self/status; } "
        f"> {CAPABILITY_OUTPUT}\n"
    )
    PROBE.chmod(0o755)
    DROP_IN.parent.mkdir(parents=True, exist_ok=True)
    DROP_IN.write_text(f"[Service]\nExecStartPre={PROBE}\n")


def parse_properties(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    return values


def read_capabilities() -> dict[str, object]:
    lines = CAPABILITY_OUTPUT.read_text().splitlines()
    values: dict[str, object] = {"uid": int(lines[0])}
    for line in lines[1:]:
        key, value = line.split(":", 1)
        values[key] = value.strip()
    cap_eff = int(str(values["CapEff"]), 16)
    values["cap_setuid"] = bool(cap_eff & (1 << 7))
    return values


def exercise(label: str, release: Path) -> dict[str, object]:
    run("systemctl", "stop", UNIT.name, check=False)
    host_tools = Path("/opt/robopark/host-tools")
    current = Path("/opt/robopark/current")
    releases = Path("/opt/robopark/releases")
    releases.mkdir(parents=True, exist_ok=True)
    release_link = releases / label
    replace_symlink(release_link, release)
    replace_symlink(current, release_link)
    replace_symlink(host_tools, release / "deploy/host")
    UNIT.write_bytes(
        (release / "deploy/systemd/robopark-terminal-setup.service").read_bytes()
    )
    UNIT.chmod(0o644)
    CAPABILITY_OUTPUT.unlink(missing_ok=True)
    run("systemctl", "daemon-reload")
    run("systemctl", "reset-failed", UNIT.name, check=False)
    start = run("systemctl", "start", UNIT.name, check=False)
    properties = parse_properties(
        run("systemctl", "show", UNIT.name, *(f"-p{name}" for name in PROPERTIES)).stdout
    )
    capabilities = read_capabilities()
    journal = run("journalctl", "-u", UNIT.name, "-n", "40", "--no-pager").stdout
    return {
        "label": label,
        "release": str(release),
        "unit_sha256": sha256(
            release / "deploy/systemd/robopark-terminal-setup.service"
        ),
        "helper_sha256": sha256(release / "deploy/host/robopark_host/cli.py"),
        "start_status": start.returncode,
        "start_stderr": start.stderr.strip(),
        "properties": properties,
        "capabilities": capabilities,
        "journal": journal,
    }


def assert_results(baseline: dict[str, object], candidate: dict[str, object]) -> None:
    baseline_properties = baseline["properties"]
    candidate_properties = candidate["properties"]
    baseline_caps = baseline["capabilities"]
    candidate_caps = candidate["capabilities"]
    assert isinstance(baseline_properties, dict)
    assert isinstance(candidate_properties, dict)
    assert isinstance(baseline_caps, dict)
    assert isinstance(candidate_caps, dict)
    assert {key: baseline_properties[key] for key in EXPECTED_HARDENING} == EXPECTED_HARDENING
    assert {key: candidate_properties[key] for key in EXPECTED_HARDENING} == EXPECTED_HARDENING
    assert baseline_properties["User"] == "root"
    assert baseline["start_status"] != 0
    assert baseline_properties["Result"] == "exit-code"
    assert baseline_caps["uid"] == 0
    assert baseline_caps["cap_setuid"] is False
    assert candidate_properties["User"] == ""
    assert candidate["start_status"] == 0
    assert candidate_properties["Result"] == "success"
    assert candidate_caps["uid"] == 0
    assert candidate_caps["cap_setuid"] is True
    account = pwd.getpwnam("robopark-maint")
    assert account.pw_uid not in (0, 10001)
    identity = run("runuser", "-u", "robopark-maint", "--", "id", "-u")
    assert int(identity.stdout.strip()) == account.pw_uid
    assert run(
        "runuser",
        "-u",
        "robopark-maint",
        "--",
        "test",
        "-w",
        "/var/lib/robopark/terminal-home",
    ).returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-ota", required=True, type=Path)
    parser.add_argument("--candidate-ota", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--fixture-root", type=Path, default=Path("/opt/robopark-systemd-setup-probe")
    )
    args = parser.parse_args()
    require_disposable_vm()
    if args.fixture_root.exists():
        raise RuntimeError("fixture_root_exists")
    install_probe()
    baseline_release = extract_release(args.baseline_ota, args.fixture_root / "baseline")
    candidate_release = extract_release(args.candidate_ota, args.fixture_root / "candidate")
    baseline = exercise("systemd-probe-baseline", baseline_release)
    candidate = exercise("systemd-probe-candidate", candidate_release)
    assert_results(baseline, candidate)
    report = {
        "format": 1,
        "platform": {
            "os_release": Path("/etc/os-release").read_text(),
            "systemd": run("systemd", "--version").stdout.splitlines()[0],
        },
        "artifacts": {
            "baseline_ota_sha256": sha256(args.baseline_ota),
            "candidate_ota_sha256": sha256(args.candidate_ota),
        },
        "baseline": baseline,
        "candidate": candidate,
        "outcome": "PASS_BASELINE_RED_CANDIDATE_GREEN",
    }
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    args.output.chmod(0o600)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
