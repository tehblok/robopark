"""Exercise the shipped removal fence against temporary locks, never system paths."""

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="macOS plutil lifecycle contract"
)


@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("starting", 73),
        ("orphan_installer", 73),
        ("empty_recent", 73),
        ("manual_recent", 73),
        ("stale", 0),
        ("malformed", 1),
        ("extra_entry", 1),
    ],
)
def test_uninstall_waits_for_both_install_paths_and_recovers_only_stale_locks(
    tmp_path, scenario, expected
):
    script = Path(__file__).resolve().parents[2] / "scripts/macos_uninstall.sh"
    source = script.read_text()
    fence = source.split("any_installer_running() {", 1)[1].split(
        "/bin/launchctl bootout system", 1
    )[0]
    fence = "any_installer_running() {" + fence
    support = tmp_path / "updater"
    support.mkdir()
    lock = support / (
        "install.lock" if scenario == "manual_recent" else "lifecycle.lock"
    )
    lock.mkdir(mode=0o700)
    now = int(time.time())
    if scenario != "empty_recent":
        if scenario == "manual_recent":
            state = lock / "state"
            state.write_text("mode=package\nversion=0.2.3-rc.2\n")
        else:
            state = lock / "state.json"
            state.write_text(
                json.dumps(
                    {
                        "helper_pid": 99999999,
                        "installer_pid": -1,
                        "installer_started_at": now
                        if scenario == "starting"
                        else now - 700,
                    }
                )
            )
        state.chmod(0o600)
        if scenario == "malformed":
            state.chmod(0o666)
        if scenario == "extra_entry":
            (lock / "unknown").write_text("keep")
    if scenario not in {"starting", "empty_recent", "manual_recent"}:
        os.utime(lock, (now - 700, now - 700))
    # Simulate root ownership only; modes, sizes, mtimes and actual file mutations
    # come from the temporary filesystem. No installed app or service is touched.
    stat = tmp_path / "stat"
    stat.write_text(
        f"#!{sys.executable}\n"
        "import os, stat, sys\n"
        "s=os.stat(sys.argv[-1]); form=sys.argv[2]\n"
        "print({'%u:%Lp': '0:'+oct(stat.S_IMODE(s.st_mode))[2:], '%z': s.st_size, '%m': int(s.st_mtime)}[form])\n"
    )
    stat.chmod(0o755)
    ps = tmp_path / "ps"
    ps.write_text(
        "#!/bin/sh\n"
        + (
            "echo /usr/sbin/installer\n"
            if scenario == "orphan_installer"
            else "exit 0\n"
        )
    )
    ps.chmod(0o755)
    fence = fence.replace("/usr/bin/stat", shlex.quote(str(stat)))
    fence = fence.replace("/bin/ps", shlex.quote(str(ps)))
    fence = fence.replace("/bin/sleep 1", "exit 73")
    harness = "set -eu\nupdater_root=" + shlex.quote(str(support)) + "\n" + fence
    result = subprocess.run(["/bin/sh", "-c", harness], capture_output=True, text=True, check=False)
    assert result.returncode == expected, result.stderr
    assert lock.exists() is (expected != 0)
    if scenario == "extra_entry":
        assert (lock / "unknown").read_text() == "keep"
    if scenario == "malformed":
        assert (lock / "state.json").exists()
