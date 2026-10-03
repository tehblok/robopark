"""Idempotent, fixed-purpose host setup executed only by the root setup unit."""

from __future__ import annotations

import grp
import io
import os
import pwd
import stat
import sys
import tempfile
import zipfile
from pathlib import Path

ACCOUNT = "robopark-maint"


def worker_bundle():
    """Expose only worker code; release directories and host secrets stay private."""
    launcher = """import argparse
from pathlib import Path
from types import SimpleNamespace
from robopark_host.terminal_worker import run_worker
from robopark_host.terminal_setup import validate_account
parser = argparse.ArgumentParser(description="Robopark terminal worker")
parser.add_argument("id")
parser.add_argument("profile", choices=("maintenance", "root"))
args = parser.parse_args()
raise SystemExit(run_worker(SimpleNamespace(root=Path("/")), args.id, args.profile))
"""
    source = Path(__file__).resolve().parent
    content = {"__main__.py": launcher.encode(), "robopark_host/__init__.py": b""}
    for name in ("terminal_worker", "terminal_protocol", "terminal_setup"):
        content[f"robopark_host/{name}.py"] = (source / f"{name}.py").read_bytes()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, data in content.items():
            # Stable metadata makes the installed projection reproducible.
            archive.writestr(zipfile.ZipInfo(name), data)
    return output.getvalue()


def install_worker_bundle(directory, gid):
    data = worker_bundle()
    descriptor, temporary = tempfile.mkstemp(prefix=".worker-", dir=directory)
    try:
        os.fchown(descriptor, 0, gid)
        os.fchmod(descriptor, 0o440)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / "worker.pyz")
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_account(account, groups, *, forbidden_gids):
    if (
        account.pw_uid in (0, 10001)
        or account.pw_gid in forbidden_gids
        or set(groups) != {account.pw_gid}
        or account.pw_name != ACCOUNT
        or account.pw_dir != "/var/lib/robopark/terminal-home"
        or account.pw_shell != "/usr/sbin/nologin"
    ):
        raise ValueError("terminal_unsafe_account")


def validate_setup_paths(paths):
    targets = (
        paths.var / "terminal-home",
        paths.state / "terminal",
        paths.root / "run/robopark-terminal",
        paths.root / "run/robopark-terminal-workers",
    )
    for target in targets:
        for part in (target, *target.parents):
            if part == paths.root:
                break
            try:
                mode = part.lstat().st_mode
            except FileNotFoundError:
                continue
            if not stat.S_ISDIR(mode):
                raise ValueError("terminal_unsafe_path")


def prepare_terminal_host(paths, runner):
    validate_setup_paths(paths)
    if (
        paths.root != Path("/")
        or os.geteuid() != 0
        or not sys.platform.startswith("linux")
    ):
        raise ValueError("terminal_requires_linux_root")
    home = paths.var / "terminal-home"
    try:
        account = pwd.getpwnam(ACCOUNT)
    except KeyError:
        runner.run(
            [
                "useradd",
                "--system",
                "--user-group",
                "--home-dir",
                str(home),
                "--shell",
                "/usr/sbin/nologin",
                ACCOUNT,
            ],
            timeout=30,
        )
        account = pwd.getpwnam(ACCOUNT)
    forbidden = {0, 10001}
    for name in ("sudo", "wheel", "docker", "adm", "systemd-journal"):
        try:
            forbidden.add(grp.getgrnam(name).gr_gid)
        except KeyError:
            pass
    validate_account(
        account, os.getgrouplist(ACCOUNT, account.pw_gid), forbidden_gids=forbidden
    )
    if grp.getgrnam(ACCOUNT).gr_gid != account.pw_gid:
        raise ValueError("terminal_unsafe_account")
    with open("/etc/shadow", encoding="utf-8") as stream:
        locked = [
            line.split(":")[1] for line in stream if line.startswith(ACCOUNT + ":")
        ]
    if len(locked) != 1 or not locked[0].startswith(("!", "*")):
        raise ValueError("terminal_account_not_locked")
    for path, uid, gid, mode in (
        (home, account.pw_uid, account.pw_gid, 0o700),
        (paths.state / "terminal", 0, 0, 0o700),
        (paths.root / "run/robopark-terminal", 0, 10001, 0o750),
        (paths.root / "run/robopark-terminal-workers", 0, account.pw_gid, 0o750),
    ):
        path.mkdir(mode=mode, parents=True, exist_ok=True)
        os.chown(path, uid, gid)
        path.chmod(mode)
    policy = paths.state / "terminal" / "home-acl.conf"
    policy.write_text("a+ /var/lib/robopark - - - - u:robopark-maint:--x\n")
    policy.chmod(0o600)
    runner.run(["systemd-tmpfiles", "--create", str(policy)], timeout=15)
    runtime = paths.root / "run/robopark-terminal-workers"
    install_worker_bundle(runtime, account.pw_gid)
    runner.run(
        [
            "runuser",
            "-u",
            ACCOUNT,
            "--",
            "/usr/bin/python3",
            "-I",
            str(runtime / "worker.pyz"),
            "--help",
        ],
        timeout=10,
    )
    runner.run(["runuser", "-u", ACCOUNT, "--", "test", "-w", str(home)], timeout=10)
