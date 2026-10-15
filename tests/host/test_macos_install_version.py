"""Run the shipped version guards against real temporary macOS plists.

Only the application path is redirected. Neither sudo, installer, launchd nor
the real installed application is invoked by this harness.
"""

import ast
import plistlib
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import build_macos_command, macos_keychain_broker

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="real macOS PlistBuddy"
)
VERSION = "0.2.3-rc.4"


def version_guard(tmp_path, layer):
    if layer == "command":
        package = tmp_path / "payload.pkg"
        package.write_bytes(b"xar!fixture")
        command = tmp_path / "install.command"
        build_macos_command.build(package, command, version=VERSION)
        source = command.read_text()
        guard = (
            "current="
            + source.split("current=", 1)[1].split('echo "Robopark $version', 1)[0]
        )
        return "version=" + shlex.quote(VERSION) + "\n" + guard
    # Render the actual f-string supplied to preinstall.write_text, without
    # building binaries or running the package's privileged lifecycle hooks.
    tree = ast.parse((ROOT / "scripts/build_macos_pkg.py").read_text())
    for call in ast.walk(tree):
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "write_text"
            and isinstance(call.func.value, ast.BinOp)
            and isinstance(call.func.value.right, ast.Constant)
            and call.func.value.right.value == "preinstall"
        ):
            source = eval(
                compile(ast.Expression(call.args[0]), "preinstall", "eval"),
                {"release_version": VERSION, "macos_keychain_broker": macos_keychain_broker,
                 "broker_manifest": {"sha256": "a" * 64}},
            )
            return "installed=" + source.split("installed=", 1)[1]
    raise AssertionError("package preinstall script not found")


@pytest.mark.parametrize("layer", ["command", "preinstall"])
@pytest.mark.parametrize(
    "scenario,allowed",
    [
        ("absent", True),
        ("removed_with_receipt", True),
        ("older", True),
        ("same", True),
        ("newer", False),
        ("stable", False),
        ("missing_plist", False),
        ("missing_key", False),
        ("empty_version", False),
        ("malformed_plist", False),
        ("unknown_version", False),
        ("broken_app_symlink", False),
        ("plist_symlink", False),
        ("contents_symlink", False),
        ("reader_failed", False),
    ],
)
def test_install_version_guard_handles_absent_app_and_preserves_antirollback(
    tmp_path, layer, scenario, allowed
):
    guard = version_guard(tmp_path, layer)
    app = tmp_path / "Applications/Robopark.app"
    plist = app / "Contents/Info.plist"
    if scenario == "broken_app_symlink":
        app.parent.mkdir()
        app.symlink_to(tmp_path / "missing-target")
    elif scenario not in {"absent", "removed_with_receipt"}:
        plist.parent.mkdir(parents=True)
        if scenario != "missing_plist":
            versions = {
                "older": "0.2.3-rc.3",
                "newer": "0.2.3-rc.5",
                "stable": "0.2.3",
                "empty_version": "",
                "unknown_version": "not-a-version",
            }
            data = {"RoboparkReleaseVersion": versions.get(scenario, VERSION)}
            if scenario == "missing_key":
                data = {"CFBundleName": "Robopark"}
            plist.write_bytes(plistlib.dumps(data))
            if scenario == "malformed_plist":
                plist.write_text("not a plist")
            if scenario == "plist_symlink":
                target = tmp_path / "outside.plist"
                plist.rename(target)
                plist.symlink_to(target)
            if scenario == "contents_symlink":
                target = tmp_path / "outside-contents"
                plist.parent.rename(target)
                plist.parent.symlink_to(target)
    if scenario == "removed_with_receipt":
        # Removal can preserve the package receipt and launch agent. The app
        # bundle, rather than either residue, determines the installed version.
        for name in (
            "var/db/receipts/ru.robopark.agent.plist",
            "Library/LaunchAgents/ru.robopark.agent.plist",
        ):
            residue = tmp_path / name
            residue.parent.mkdir(parents=True, exist_ok=True)
            residue.write_bytes(plistlib.dumps({"PackageVersion": "2478.20.27"}))
    guard = guard.replace("/Applications/Robopark.app", str(app))
    if scenario == "reader_failed":
        reader = tmp_path / "failed-reader"
        reader.write_text("#!/bin/sh\nprintf '0.2.3-rc.3\\n'\nexit 1\n")
        reader.chmod(0o755)
        guard = guard.replace("/usr/libexec/PlistBuddy", shlex.quote(str(reader)))
    before = plist.read_bytes() if plist.is_file() else None
    result = subprocess.run(
        ["/bin/sh", "-c", "set -eu\n" + guard],
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is allowed, (result.stdout, result.stderr)
    assert (plist.read_bytes() if plist.is_file() else None) == before
    if not allowed:
        assert result.stderr, "failed guard must explain why installation stopped"
