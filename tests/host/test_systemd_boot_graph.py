"""Exercise systemd's implicit dependencies, not a hand-written graph model.

Run on Linux with systemd-analyze; no system services are started.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from robopark_host.storage_setup import storage_units

REPO = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("systemd-analyze") is None,
    reason="requires Linux systemd-analyze",
)


def verify_graph(root, *, original_bug=False):
    root.mkdir()
    for name in ("robopark.service", "robopark-terminal-setup.service", "robopark-bot.path", "robopark-commands.path", "robopark-bot.service", "robopark-commands.service"):
        data = (REPO / "deploy/systemd" / name).read_text()
        # Isolate dependency resolution from installation/executable availability.
        data = re.sub(r"^Exec[^=]*=.*$", "", data, flags=re.MULTILINE)
        if name.endswith(".service"):
            data += "\n[Service]\nExecStart=/bin/true\n"
        if original_bug and name.endswith(".path"):
            data = data.replace("DefaultDependencies=no\n", "")
        (root / name).write_text(data)
    for name, data in storage_units({"mode": "nvme-root"}).items():
        if name not in {"robopark-storage.target", "robopark-storage-check.service", "docker.socket.d/50-robopark-storage.conf", "docker.service.d/50-robopark-storage.conf"}:
            continue
        data = re.sub(r"^Exec[^=]*=.*$", "", data, flags=re.MULTILINE)
        if name.endswith(".service"):
            data += "\n[Service]\nExecStart=/bin/true\n"
        if original_bug:
            data = data.replace("DefaultDependencies=no\n", "")
        target = root / name
        target.parent.mkdir(exist_ok=True)
        target.write_text(data)
    (root / "docker.service").write_text("[Unit]\nRequires=docker.socket\nAfter=docker.socket\n[Service]\nExecStart=/bin/true\n")
    (root / "docker.socket").write_text("[Socket]\nListenStream=/run/robopark-test-docker.sock\n")
    (root / "default.target").write_text("[Unit]\nRequires=basic.target\nAfter=basic.target\nWants=robopark.service robopark-commands.path\n")
    return subprocess.run(
        ["systemd-analyze", "verify", "--man=no", "default.target"],
        env={**os.environ, "SYSTEMD_UNIT_PATH": f"{root}:/usr/lib/systemd/system:/lib/systemd/system"},
        capture_output=True, text=True, timeout=30, check=False,
    )


def test_production_boot_graph_is_acyclic_and_fixture_catches_original_bug(tmp_path):
    repaired = verify_graph(tmp_path / "fixed")
    assert repaired.returncode == 0, repaired.stderr
    assert "cycle" not in repaired.stderr.lower(), repaired.stderr
    original = verify_graph(tmp_path / "original", original_bug=True)
    # Some systemd versions return 0 after deleting a job to break the cycle.
    assert "cycle" in original.stderr.lower(), original.stderr
