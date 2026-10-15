from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import sys

import pytest

from scripts import macos_keychain_broker as broker


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    root = tmp_path / "source"
    for folder in ("KeychainBrokerIPC", "RoboparkKeychainBroker"):
        path = root / "apps/macos/Sources" / folder
        path.mkdir(parents=True)
        (path / "fixture.c").write_text("/* public fixture */")
    pin = "a" * 64
    (root / "deploy").mkdir()
    (root / "deploy/macos-test-signing.json").write_text(json.dumps({"certificate_sha256": pin}))
    directory = tmp_path / "frozen"
    directory.mkdir()
    binary = directory / broker.EXECUTABLE
    binary.write_bytes(b"immutable signed fixture")
    manifest = {"schema": 1, "protocol": 1, "architecture": "arm64",
                "identifier": broker.IDENTIFIER, "certificate_sha256": pin,
                "source_sha256": broker.source_digest(root), "cdhash": "b" * 40,
                "sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
    (directory / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(broker.subprocess, "check_output", lambda *a, **kw: "arm64\n")
    monkeypatch.setattr(broker, "signature_metadata", lambda path: ("b" * 40, pin))
    return root, directory, manifest


def test_frozen_broker_verifies_exact_bytes_source_and_identity(frozen):
    root, directory, manifest = frozen
    assert broker.verify_frozen(directory, root=root) == manifest


@pytest.mark.parametrize("change", ["binary", "source", "certificate", "symlink", "protocol"])
def test_changed_broker_requires_explicit_migration(frozen, change):
    root, directory, manifest = frozen
    if change == "binary":
        (directory / broker.EXECUTABLE).write_bytes(b"replacement")
    elif change == "source":
        (root / "apps/macos/Sources/KeychainBrokerIPC/fixture.c").write_text("changed")
    elif change == "symlink":
        original = directory / broker.EXECUTABLE
        original.rename(directory / "target")
        original.symlink_to(directory / "target")
    else:
        manifest["certificate_sha256" if change == "certificate" else "protocol"] = "wrong"
        (directory / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        broker.verify_frozen(directory, root=root)


def test_two_releases_copy_byte_identical_broker_without_resigning(frozen, tmp_path, monkeypatch):
    _, directory, manifest = frozen
    monkeypatch.setattr(broker, "verify_frozen", lambda path: manifest)
    for name in ("release-a", "release-b"):
        payload = tmp_path / name
        assert broker.stage_frozen(payload, directory=directory) == manifest
        copied = payload / broker.INSTALL_DIR.lstrip("/") / broker.EXECUTABLE
        assert copied.read_bytes() == (directory / broker.EXECUTABLE).read_bytes()
        assert copied.stat().st_mode & 0o777 == 0o755


def test_broker_is_user_scoped_on_demand_and_scripts_parse(tmp_path):
    agent = broker.launch_agent()
    assert agent["MachServices"] == {broker.IDENTIFIER: True}
    assert "UserName" not in agent and "RunAtLoad" not in agent
    assert agent["ProgramArguments"] == [f"{broker.INSTALL_DIR}/{broker.EXECUTABLE}"]
    for body in (broker.preinstall_script("a" * 64), broker.postinstall_permissions(), broker.register_script()):
        subprocess.run(["/bin/sh", "-n"], input=body, text=True, check=True)


@pytest.mark.parametrize("state,allowed", [
    ("absent", True), ("identical", True), ("changed", False),
    ("symlink", False), ("writable", False),
])
def test_installed_broker_cannot_be_silently_replaced(tmp_path, state, allowed):
    root = tmp_path / "broker"
    version = root / "v1"
    digest = hashlib.sha256(b"original").hexdigest()
    if state != "absent":
        version.mkdir(parents=True)
        binary = version / broker.EXECUTABLE
        binary.write_bytes(b"changed" if state == "changed" else b"original")
        if state == "symlink":
            binary.rename(version / "elsewhere")
            binary.symlink_to(version / "elsewhere")
        if state == "writable":
            version.chmod(0o777)
    # Simulate root ownership, keeping real filesystem types/modes/hash checks.
    fake_stat = tmp_path / "stat"
    fake_stat.write_text(f"#!{sys.executable}\nimport os,sys,stat\n"
                        "s=os.stat(sys.argv[-1]); f=sys.argv[2]\n"
                        "print(0 if f=='%u' else oct(stat.S_IMODE(s.st_mode))[2:])\n")
    fake_stat.chmod(0o755)
    source = broker.preinstall_script(digest).replace(
        "/Library/Application Support/RoboparkKeychainBroker", str(root)
    ).replace("/usr/bin/stat", shlex.quote(str(fake_stat)))
    result = subprocess.run(["/bin/sh", "-eu", "-c", source],
                            capture_output=True, text=True, check=False)
    assert (result.returncode == 0) == allowed, result.stderr
