import importlib.util
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "deploy/installer/lib/lifecycle.py"


def load_module():
    spec = importlib.util.spec_from_file_location("installer_lifecycle", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_bundle(path: Path, version: str = "0.2.0-rc.1") -> Path:
    payload = path / "payload/robopark-release.zip"
    payload.parent.mkdir(parents=True)
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"app_version": version}))
    return path


def make_os(root: Path) -> None:
    (root / "etc").mkdir(parents=True)
    (root / "etc/os-release").write_text(
        'ID=ubuntu\nVERSION_ID="22.04"\nPRETTY_NAME="Ubuntu 22.04.5 LTS"\n'
    )


def make_install(root: Path, version: str = "0.1.44", *, complete: bool = True) -> None:
    release = root / "opt/robopark/releases" / version
    host = release / "deploy/host/robopark"
    host.parent.mkdir(parents=True)
    host.write_text("#!/bin/sh\n")
    host.chmod(0o755)
    (release / "VERSION").write_text(version + "\n")
    (root / "opt/robopark/current").symlink_to(release)
    (root / "opt/robopark/host-tools").symlink_to(release / "deploy/host")
    state = root / "var/lib/robopark/ops/state/install.json"
    state.parent.mkdir(parents=True)
    state.write_text(
        json.dumps(
            {
                "phase": "complete" if complete else "services",
                "status": "complete" if complete else "failed",
                "packages_complete": True,
            }
        )
    )


def test_detects_clean_host_and_bundle_version(tmp_path):
    module = load_module()
    root, bundle = tmp_path / "root", make_bundle(tmp_path / "bundle")
    make_os(root)

    result = module.detect(root, bundle, machine="aarch64")

    assert result == {
        "state": "absent",
        "installed_version": "none",
        "bundle_version": "0.2.0-rc.1",
        "relation": "none",
        "os": "Ubuntu 22.04.5 LTS",
        "arch": "arm64",
    }


def test_detects_installed_versions_and_relation(tmp_path):
    module = load_module()
    root, bundle = tmp_path / "root", make_bundle(tmp_path / "bundle")
    make_os(root)
    make_install(root)

    result = module.detect(root, bundle, machine="aarch64")

    assert result["state"] == "installed"
    assert result["installed_version"] == "0.1.44"
    assert result["relation"] == "older"


def test_detects_incomplete_damaged_and_data_only_states(tmp_path):
    module = load_module()
    bundle = make_bundle(tmp_path / "bundle")

    incomplete = tmp_path / "incomplete"
    make_os(incomplete)
    make_install(incomplete, complete=False)
    assert module.detect(incomplete, bundle)["state"] == "incomplete"

    damaged = tmp_path / "damaged"
    make_os(damaged)
    make_install(damaged)
    (damaged / "opt/robopark/host-tools").unlink()
    assert module.detect(damaged, bundle)["state"] == "damaged"

    data_only = tmp_path / "data-only"
    make_os(data_only)
    (data_only / "var/lib/robopark/data").mkdir(parents=True)
    assert module.detect(data_only, bundle)["state"] == "removed-data"

    removed_incomplete = tmp_path / "removed-incomplete"
    make_os(removed_incomplete)
    journal = removed_incomplete / "var/lib/robopark/ops/state/install.json"
    journal.parent.mkdir(parents=True)
    journal.write_text('{"phase":"services","status":"failed"}\n')
    assert module.detect(removed_incomplete, bundle)["state"] == "removed-data"


def test_broken_current_link_is_damaged_and_never_escapes_release_tree(tmp_path):
    module = load_module()
    root, bundle = tmp_path / "root", make_bundle(tmp_path / "bundle")
    make_os(root)
    current = root / "opt/robopark/current"
    current.parent.mkdir(parents=True)
    current.symlink_to(tmp_path / "outside")

    result = module.detect(root, bundle)

    assert result["state"] == "damaged"
    assert result["installed_version"] == "unknown"

    symlinked = tmp_path / "symlinked-releases"
    make_os(symlinked)
    outside_release = tmp_path / "outside-release"
    (outside_release / "deploy/host").mkdir(parents=True)
    (outside_release / "VERSION").write_text("0.1.44\n")
    outside_host = outside_release / "deploy/host/robopark"
    outside_host.write_text("#!/bin/sh\n")
    outside_host.chmod(0o755)
    opt = symlinked / "opt/robopark"
    opt.mkdir(parents=True)
    (opt / "releases").symlink_to(tmp_path)
    (opt / "current").symlink_to(outside_release)
    (opt / "host-tools").symlink_to(outside_release / "deploy/host")
    assert module.detect(symlinked, bundle)["state"] == "damaged"

    mismatched_tools = tmp_path / "mismatched-tools"
    make_os(mismatched_tools)
    make_install(mismatched_tools, "0.1.44")
    other = mismatched_tools / "opt/robopark/releases/0.1.43/deploy/host"
    other.mkdir(parents=True)
    other_host = other / "robopark"
    other_host.write_text("#!/bin/sh\n")
    other_host.chmod(0o755)
    (mismatched_tools / "opt/robopark/host-tools").unlink()
    (mismatched_tools / "opt/robopark/host-tools").symlink_to(other)
    assert module.detect(mismatched_tools, bundle)["state"] == "damaged"


def test_semantic_relation_refuses_downgrade_and_handles_prerelease():
    module = load_module()
    assert module.version_relation("0.1.44", "0.2.0-rc.1") == "older"
    assert module.version_relation("0.2.0-rc.1", "0.2.0-rc.1") == "same"
    assert module.version_relation("0.2.0", "0.2.0-rc.1") == "newer"
    assert module.version_relation("not-a-version", "0.2.0") == "unknown"


def test_cli_emits_shell_safe_fixed_fields(tmp_path):
    root, bundle = tmp_path / "root", make_bundle(tmp_path / "bundle")
    make_os(root)
    result = subprocess.run(
        [sys.executable, "-I", MODULE, root, bundle],
        text=True,
        capture_output=True,
        env={**os.environ, "ARCH": "aarch64"},
        check=True,
    )
    assert result.stdout.splitlines() == [
        "STATE=absent",
        "INSTALLED_VERSION=none",
        "BUNDLE_VERSION=0.2.0-rc.1",
        "RELATION=none",
        "OS=Ubuntu 22.04.5 LTS",
        "ARCH=arm64",
    ]
