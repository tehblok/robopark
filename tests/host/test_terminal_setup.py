import importlib.util
import subprocess
import sys
import zipfile
from types import SimpleNamespace

import pytest


def setup_module_api():
    assert importlib.util.find_spec("robopark_host.terminal_setup"), (
        "terminal setup missing"
    )
    from robopark_host import terminal_setup

    return terminal_setup


def account(**changes):
    value = {
        "pw_uid": 995,
        "pw_gid": 995,
        "pw_dir": "/var/lib/robopark/terminal-home",
        "pw_shell": "/usr/sbin/nologin",
        "pw_name": "robopark-maint",
    }
    value.update(changes)
    return SimpleNamespace(**value)


@pytest.mark.parametrize(
    "user,groups",
    [
        (account(pw_uid=0), [995]),
        (account(pw_uid=10001), [995]),
        (account(), [995, 10001]),
        (account(), [995, 27]),
        (account(), [995, 6]),  # disk is privileged but not in the legacy denylist
        (account(), [995, 987]),  # unknown supplementary groups also fail closed
        (account(pw_shell="/bin/bash"), [995]),
    ],
)
def test_maintenance_account_cannot_inherit_privileged_identity(user, groups):
    with pytest.raises(ValueError):
        setup_module_api().validate_account(user, groups, forbidden_gids={0, 27, 10001})


def test_expected_maintenance_account_is_accepted():
    setup_module_api().validate_account(account(), [995], forbidden_gids={0, 27, 10001})


def test_setup_rejects_symlink_before_system_mutation(host_paths):
    host_paths.var.mkdir(parents=True)
    (host_paths.var / "terminal-home").symlink_to("/tmp")

    class Runner:
        def run(self, *a, **kw):
            pytest.fail("setup called system tools after unsafe path")

    with pytest.raises(ValueError, match="terminal_unsafe_path"):
        setup_module_api().validate_setup_paths(host_paths)


def test_setup_path_validation_is_idempotent(host_paths):
    p = setup_module_api()
    p.validate_setup_paths(host_paths)
    p.validate_setup_paths(host_paths)
    assert not (host_paths.var / "terminal-home").exists()


def test_worker_bundle_runs_without_access_to_release_tree(tmp_path):
    """Normal UID only needs the root-owned runtime file, never release access."""
    target = tmp_path / "worker.pyz"
    target.write_bytes(setup_module_api().worker_bundle())
    with zipfile.ZipFile(target) as archive:
        assert set(archive.namelist()) == {
            "__main__.py",
            "robopark_host/__init__.py",
            "robopark_host/terminal_worker.py",
            "robopark_host/terminal_protocol.py",
            "robopark_host/terminal_setup.py",
        }
    result = subprocess.run(
        [sys.executable, "-I", str(target), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "maintenance" in result.stdout


def test_worker_bundle_installs_atomically_with_read_only_ownership(
    tmp_path, monkeypatch
):
    setup = setup_module_api()
    directory = tmp_path / "runtime"
    directory.mkdir()
    target = directory / "worker.pyz"
    victim = tmp_path / "do-not-touch"
    victim.write_bytes(b"private")
    target.symlink_to(victim)
    ownership = []
    monkeypatch.setattr(
        setup.os, "fchown", lambda fd, uid, gid: ownership.append((uid, gid))
    )
    setup.install_worker_bundle(directory, 995)
    assert not target.is_symlink()
    assert victim.read_bytes() == b"private"
    assert target.stat().st_mode & 0o777 == 0o440
    assert ownership == [(0, 995)]
    assert target.read_bytes() == setup.worker_bundle()
    assert list(directory.iterdir()) == [target]


def test_setup_cli_retains_failing_child_output_in_private_host_log(
    host_paths, monkeypatch, capsys
):
    """A setup failure must remain diagnosable after its unit has exited."""
    from robopark_host import cli
    from robopark_host.release import ReleaseError

    def failed_host_probe(paths, runner):
        runner.run(
            [
                sys.executable,
                "-I",
                "-c",
                'import sys; sys.stderr.write("UID switch denied\\n"); sys.exit(1)',
            ],
            timeout=5,
        )

    # Keep the CLI and subprocess runner real; avoid account/ACL mutations on
    # the developer machine by substituting only the Linux setup operation.
    monkeypatch.setattr(setup_module_api(), "prepare_terminal_host", failed_host_probe)
    with pytest.raises(ReleaseError) as failure:
        cli.main(["terminal-setup"])

    log = host_paths.root / "var/log/robopark/terminal-setup.log"
    assert log.is_file(), "setup CLI discarded its failing child's diagnostic output"
    assert log.read_bytes() == b"UID switch denied\n"
    assert log.stat().st_mode & 0o777 == 0o600
    assert "UID switch denied" not in str(failure.value)
    assert capsys.readouterr() == ("", "")
