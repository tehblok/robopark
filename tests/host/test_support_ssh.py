import base64
import importlib.util
import os
import stat
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/support/ssh_access.py"


def load_module():
    spec = importlib.util.spec_from_file_location("robopark_ssh_access", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def ed25519_key(comment="operator", seed=0):
    kind = b"ssh-ed25519"
    raw = (
        struct.pack(">I", len(kind))
        + kind
        + struct.pack(">I", 32)
        + bytes((value + seed) % 256 for value in range(32))
    )
    return f"ssh-ed25519 {base64.b64encode(raw).decode()} {comment}\n"


class RecordingRunner:
    def __init__(self, *, fail_tuna=False, active=(), enabled=()):
        self.calls = []
        self.fail_tuna = fail_tuna
        self.active = set(active)
        self.enabled = set(enabled)

    def __call__(self, argv, *, check=True, capture_output=False, text=True):
        self.calls.append(list(argv))
        if argv[:3] == ["systemctl", "is-enabled", "--quiet"]:
            return subprocess.CompletedProcess(
                argv, 0 if argv[3] in self.enabled else 1, "", ""
            )
        if argv[:3] == ["systemctl", "is-active", "--quiet"]:
            return subprocess.CompletedProcess(
                argv, 0 if argv[3] in self.active else 1, "", ""
            )
        if argv[:3] == ["systemctl", "show", "--property=MainPID"]:
            return subprocess.CompletedProcess(
                argv, 0, "4242\n" if argv[-1] in self.active else "0\n", ""
            )
        if argv[:3] == ["systemctl", "show", "--property=NRestarts"]:
            return subprocess.CompletedProcess(argv, 0, "0\n", "")
        if self.fail_tuna and argv == [
            "systemctl",
            "enable",
            "--now",
            "robopark-support-tuna.service",
        ]:
            raise subprocess.CalledProcessError(1, argv)
        if argv[:3] == ["systemctl", "enable", "--now"]:
            self.enabled.add(argv[3])
            self.active.add(argv[3])
        if argv[:3] == ["systemctl", "disable", "--now"]:
            for unit in argv[3:]:
                self.enabled.discard(unit)
                self.active.discard(unit)
        if len(argv) == 3 and argv[:2] == ["systemctl", "enable"]:
            self.enabled.add(argv[2])
        if len(argv) == 3 and argv[:2] == ["systemctl", "disable"]:
            self.enabled.discard(argv[2])
        if argv[:2] == ["systemctl", "start"]:
            self.active.add(argv[2])
        if argv[:2] == ["systemctl", "stop"]:
            self.active.discard(argv[2])
        return subprocess.CompletedProcess(argv, 0, "", "")


@pytest.fixture
def ssh_access():
    return load_module()


def test_public_key_accepts_only_a_well_formed_ed25519_blob(ssh_access):
    assert ssh_access.validate_public_key(ed25519_key()) == ed25519_key().strip()

    malformed = base64.b64encode(b"ssh-ed25519" + bytes(32)).decode()
    rejected = [
        "ssh-rsa AAAA operator",
        f"ssh-ed25519 {malformed} operator",
        ed25519_key().strip() + "\ncommand=/bin/sh",
        "command=/bin/sh " + ed25519_key().strip(),
        ed25519_key(comment="operator\x00admin"),
    ]
    for value in rejected:
        with pytest.raises(ValueError, match="ed25519 public key"):
            ssh_access.validate_public_key(value)


def test_tuna_env_copies_only_a_plain_nonempty_token(ssh_access):
    source = """# existing web tunnel\nTUNA_TOKEN=abc_DEF-123.456\nTUNA_LOCATION=ru\nTUNA_SUBDOMAIN=web\nHTTP_PASSWORD=secret\n"""
    assert ssh_access.extract_tuna_token(source) == "abc_DEF-123.456"

    for source in (
        "TUNA_TOKEN=\n",
        "TUNA_TOKEN=$(id)\n",
        "TUNA_TOKEN=one\nTUNA_TOKEN=two\n",
        "export TUNA_TOKEN=secret\n",
        "TUNA_TOKEN='secret'\n",
    ):
        with pytest.raises(ValueError, match="TUNA_TOKEN"):
            ssh_access.extract_tuna_token(source)


def test_managed_path_rejects_symlink_and_unsafe_owned_ancestor(tmp_path, ssh_access):
    safe = tmp_path / "etc/robopark-ssh"
    safe.mkdir(parents=True)
    ssh_access.validate_managed_path(
        safe / "authorized_keys", root=tmp_path, expected_uid=os.getuid()
    )

    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o777)
    unsafe.chmod(0o777)
    with pytest.raises(ssh_access.SecurityError, match="writable"):
        ssh_access.validate_managed_path(
            unsafe / "file", root=tmp_path, expected_uid=os.getuid()
        )

    link = tmp_path / "linked"
    link.symlink_to(safe, target_is_directory=True)
    with pytest.raises(ssh_access.SecurityError, match="symlink"):
        ssh_access.validate_managed_path(
            link / "file", root=tmp_path, expected_uid=os.getuid()
        )


def test_install_prepares_private_persistent_state_without_enabling(
    tmp_path, ssh_access
):
    key_file = tmp_path / "operator.pub"
    key_file.write_text(ed25519_key())
    source_env = tmp_path / "source.env"
    source_env.write_text("TUNA_TOKEN=abc_DEF-123.456\nTUNA_LOCATION=eu\n")
    source_env.chmod(0o600)
    runner = RecordingRunner()
    manager = ssh_access.SshAccessManager(
        root=tmp_path,
        runner=runner,
        expected_uid=os.getuid(),
        sshd_binary="/usr/sbin/sshd",
        tuna_binary="/usr/bin/tuna",
        manage_account=False,
        generate_host_key=False,
        listener_probe=lambda: True,
        stability_interval=0,
    )
    manager.install(key_file, tuna_env=source_env, alias="ssh", location="ru")

    etc = tmp_path / "etc/robopark-ssh"
    state = tmp_path / "var/lib/robopark-ssh"
    assert (etc / "authorized_keys").read_text() == (
        "# ROBOPARK_SSH_ACCESS_MANAGED\n" + ed25519_key()
    )
    assert stat.S_IMODE(etc.stat().st_mode) == 0o755
    assert stat.S_IMODE((etc / "authorized_keys").stat().st_mode) == 0o644
    assert (etc / "tuna.env").read_text() == (
        "# ROBOPARK_SSH_ACCESS_MANAGED\nTUNA_TOKEN=abc_DEF-123.456\n"
    )
    assert stat.S_IMODE((etc / "tuna.env").stat().st_mode) == 0o600
    config = (etc / "sshd_config").read_text()
    assert "ListenAddress 127.0.0.1" in config
    assert "Port 2222" in config
    assert "PasswordAuthentication no" in config
    assert "PermitRootLogin no" in config
    assert "AuthenticationMethods publickey" in config
    assert "MaxAuthTries 3" in config
    assert "LoginGraceTime 30" in config
    assert "MaxStartups 3:30:6" in config
    assert "HostKey /etc/robopark-ssh/ssh_host_ed25519_key" in config
    assert state.is_dir()
    assert stat.S_IMODE((state / "home").stat().st_mode) == 0o700
    sshd_unit = (
        tmp_path / "etc/systemd/system/robopark-support-sshd.service"
    ).read_text()
    tuna_unit = (
        tmp_path / "etc/systemd/system/robopark-support-tuna.service"
    ).read_text()
    assert "RuntimeDirectoryPreserve=yes" in sshd_unit
    assert "User=root" not in sshd_unit
    assert "NoNewPrivileges" not in sshd_unit
    assert "DynamicUser=yes" in tuna_unit
    assert "User=robopark-support" not in tuna_unit
    assert "StateDirectory=robopark-support-tuna" in tuna_unit
    assert "Environment=HOME=/var/lib/robopark-support-tuna" in tuna_unit
    assert "tcp 127.0.0.1:2222 --port=ssh --location=ru" in tuna_unit
    assert not any(call[:2] == ["systemctl", "enable"] for call in runner.calls)
    assert ["systemctl", "daemon-reload"] in runner.calls
    assert not (tmp_path / "etc/robopark-ssh/root-password").exists()
    assert not (tmp_path / "etc/robopark-ssh/totp-secret").exists()


def test_install_preserves_existing_host_key_and_rejects_unrelated_unit(
    tmp_path, ssh_access
):
    key_file = tmp_path / "operator.pub"
    key_file.write_text(ed25519_key())
    source_env = tmp_path / "source.env"
    source_env.write_text("TUNA_TOKEN=abc_DEF-123.456\n")
    source_env.chmod(0o600)
    etc = tmp_path / "etc/robopark-ssh"
    etc.mkdir(parents=True)
    host_key = etc / "ssh_host_ed25519_key"
    host_key.write_text("existing-private-key")
    host_key.chmod(0o600)
    unit_dir = tmp_path / "etc/systemd/system"
    unit_dir.mkdir(parents=True)
    (unit_dir / "robopark-support-sshd.service").write_text("unrelated\n")
    manager = ssh_access.SshAccessManager(
        root=tmp_path,
        runner=RecordingRunner(),
        expected_uid=os.getuid(),
        sshd_binary="/usr/sbin/sshd",
        tuna_binary="/usr/bin/tuna",
        manage_account=False,
        generate_host_key=False,
        listener_probe=lambda: True,
        stability_interval=0,
    )

    with pytest.raises(ssh_access.SecurityError, match="unrelated managed file"):
        manager.install(key_file, tuna_env=source_env)
    assert host_key.read_text() == "existing-private-key"


def test_enable_starts_loopback_sshd_before_tuna_and_rolls_back_on_failure(
    tmp_path, ssh_access
):
    runner = RecordingRunner(fail_tuna=True)
    manager = ssh_access.SshAccessManager(
        root=tmp_path,
        runner=runner,
        expected_uid=os.getuid(),
        manage_account=False,
        listener_probe=lambda: True,
        stability_interval=0,
    )

    with pytest.raises(subprocess.CalledProcessError):
        manager.enable(validate=False)

    ssh_start = ["systemctl", "enable", "--now", "robopark-support-sshd.service"]
    tuna_start = ["systemctl", "enable", "--now", "robopark-support-tuna.service"]
    assert runner.calls.index(ssh_start) < runner.calls.index(tuna_start)
    assert (
        runner.calls.count(
            ["systemctl", "is-active", "--quiet", "robopark-support-sshd.service"]
        )
        >= 2
    )
    assert runner.enabled == set()
    assert runner.active == set()


def test_disable_touches_only_dedicated_units(tmp_path, ssh_access):
    runner = RecordingRunner()
    manager = ssh_access.SshAccessManager(
        root=tmp_path, runner=runner, expected_uid=os.getuid(), manage_account=False
    )
    manager.disable()
    mutations = [call for call in runner.calls if call[:3] == ["systemctl", "disable", "--now"]]
    assert mutations == [
        ["systemctl", "disable", "--now", "robopark-support-tuna.service"],
        ["systemctl", "disable", "--now", "robopark-support-sshd.service"],
    ]
    assert all(
        not call or not call[-1].startswith("robopark") or "support" in call[-1]
        for call in runner.calls
    )


def test_prepare_enable_disable_manage_only_support_transition_guards(
    tmp_path, ssh_access
):
    blocked = (
        "[Unit]\n"
        "ConditionPathExists=/etc/robopark/NEVER_ENABLE_PROD_AUTOMATION\n"
    )
    sshd_dropin = (
        tmp_path
        / "etc/systemd/system/robopark-support-sshd.service.d/90-test-transition.conf"
    )
    tuna_dropin = (
        tmp_path
        / "etc/systemd/system/robopark-support-tuna.service.d/90-test-transition.conf"
    )
    for path in (sshd_dropin, tuna_dropin):
        path.parent.mkdir(parents=True)
        path.write_text(blocked)

    runner = RecordingRunner()
    manager = ssh_access.SshAccessManager(
        root=tmp_path,
        runner=runner,
        expected_uid=os.getuid(),
        manage_account=False,
        listener_probe=lambda: True,
        stability_interval=0,
    )
    manager.validate_existing_install = lambda **_kwargs: None

    prepared = manager.prepare()

    assert prepared == {
        "phase": "prepared",
        "sshd_enabled": True,
        "sshd_active": True,
        "tuna_enabled": False,
        "tuna_active": False,
        "loopback_ready": True,
        "public_reachability": "not_checked",
        "host_fingerprint": None,
    }
    assert not sshd_dropin.exists()
    assert tuna_dropin.read_text() == blocked
    assert ["systemctl", "daemon-reload"] in runner.calls
    assert [
        "systemctl",
        "enable",
        "--now",
        "robopark-support-sshd.service",
    ] in runner.calls
    assert not any(
        call[:3] == ["systemctl", "enable", "--now"]
        and call[-1] == "robopark-support-tuna.service"
        for call in runner.calls
    )

    enabled = manager.enable()

    assert enabled["phase"] == "enabled"
    assert enabled["sshd_active"] is True
    assert enabled["tuna_active"] is True
    assert enabled["loopback_ready"] is True
    assert enabled["public_reachability"] == "not_checked"
    assert not tuna_dropin.exists()

    disabled = manager.disable()

    assert disabled == {
        "phase": "disabled",
        "sshd_enabled": False,
        "sshd_active": False,
        "tuna_enabled": False,
        "tuna_active": False,
        "loopback_ready": False,
        "public_reachability": "not_checked",
        "host_fingerprint": None,
    }
    assert sshd_dropin.read_text() == blocked
    assert tuna_dropin.read_text() == blocked
    assert not (
        tmp_path / "etc/robopark/NEVER_ENABLE_PROD_AUTOMATION"
    ).exists()


def test_prepare_rejects_unowned_transition_guard_before_systemd_mutation(
    tmp_path, ssh_access
):
    dropin = (
        tmp_path
        / "etc/systemd/system/robopark-support-sshd.service.d/90-test-transition.conf"
    )
    dropin.parent.mkdir(parents=True)
    dropin.write_text("[Unit]\nConditionPathExists=/tmp/unrelated\n")
    runner = RecordingRunner()
    manager = ssh_access.SshAccessManager(
        root=tmp_path,
        runner=runner,
        expected_uid=os.getuid(),
        manage_account=False,
        listener_probe=lambda: True,
        stability_interval=0,
    )
    manager.validate_existing_install = lambda **_kwargs: None

    with pytest.raises(ssh_access.SecurityError, match="transition guard"):
        manager.prepare()

    assert dropin.read_text() == "[Unit]\nConditionPathExists=/tmp/unrelated\n"
    assert not runner.calls


def test_prepare_persists_guard_intent_before_unlink(tmp_path, ssh_access):
    dropin = (
        tmp_path
        / "etc/systemd/system/robopark-support-sshd.service.d/90-test-transition.conf"
    )
    dropin.parent.mkdir(parents=True)
    dropin.write_text(ssh_access.TRANSITION_GUARD)
    manager = ssh_access.SshAccessManager(
        root=tmp_path,
        runner=RecordingRunner(),
        expected_uid=os.getuid(),
        manage_account=False,
    )
    manager.validate_existing_install = lambda **_kwargs: None
    manager._write_transition_state = lambda _state: (_ for _ in ()).throw(
        OSError("simulated state write failure")
    )

    with pytest.raises(RuntimeError, match="rollback incomplete"):
        manager.prepare()

    assert dropin.read_text() == ssh_access.TRANSITION_GUARD


def test_install_enable_remains_compatible_without_prepare(tmp_path, ssh_access):
    key_file = tmp_path / "operator.pub"
    key_file.write_text(ed25519_key())
    source_env = tmp_path / "source.env"
    source_env.write_text("TUNA_TOKEN=abc_DEF-123.456\n")
    source_env.chmod(0o600)
    runner = RecordingRunner()
    manager = ssh_access.SshAccessManager(
        root=tmp_path,
        runner=runner,
        expected_uid=os.getuid(),
        sshd_binary="/usr/sbin/sshd",
        tuna_binary="/usr/bin/tuna",
        manage_account=False,
        generate_host_key=False,
        listener_probe=lambda: True,
        stability_interval=0,
    )

    manager.install(key_file, tuna_env=source_env, enable=True)

    assert runner.enabled == {
        "robopark-support-sshd.service",
        "robopark-support-tuna.service",
    }
    assert runner.active == runner.enabled


def test_prepare_rejects_unknown_systemd_dropin(tmp_path, ssh_access):
    key_file = tmp_path / "operator.pub"
    key_file.write_text(ed25519_key())
    source_env = tmp_path / "source.env"
    source_env.write_text("TUNA_TOKEN=abc_DEF-123.456\n")
    source_env.chmod(0o600)
    manager = ssh_access.SshAccessManager(
        root=tmp_path,
        runner=RecordingRunner(),
        expected_uid=os.getuid(),
        sshd_binary="/usr/sbin/sshd",
        tuna_binary="/usr/bin/tuna",
        manage_account=False,
        generate_host_key=False,
        listener_probe=lambda: True,
        stability_interval=0,
    )
    manager.install(key_file, tuna_env=source_env)
    host_key = tmp_path / "etc/robopark-ssh/ssh_host_ed25519_key"
    host_key.write_text("private")
    host_key.chmod(0o600)
    Path(str(host_key) + ".pub").write_text(ed25519_key())
    manager._verify_host_key_pair = lambda *_args: ed25519_key().strip()
    override = (
        tmp_path
        / "etc/systemd/system/robopark-support-tuna.service.d/50-override.conf"
    )
    override.parent.mkdir(parents=True)
    override.write_text("[Service]\nExecStartPost=/bin/false\n")

    with pytest.raises(ssh_access.SecurityError, match="unexpected support SSH drop-in"):
        manager.prepare()


@pytest.mark.parametrize(
    ("base", "unit", "name", "content"),
    [
        (
            "etc/systemd/system",
            "robopark-support-sshd.service",
            "90-oneclick-restore.conf",
            "[Unit]\nConditionPathExists=/var/lib/robopark-oneclick/"
            "khadas-20261014T071500Z/allow-services\n",
        ),
        (
            "etc/systemd/system",
            "robopark-support-sshd.service",
            "95-runtime-session.conf",
            "[Service]\nProtectHome=tmpfs\nBindPaths=/run/user\n",
        ),
        (
            "run/systemd/system",
            "robopark-support-tuna.service",
            "90-oneclick-restore.conf",
            "[Unit]\nConditionPathExists=/var/lib/robopark-oneclick/"
            "khadas-20261014T071500Z/allow-services\n",
        ),
    ],
)
def test_validation_accepts_only_known_exact_support_dropins(
    tmp_path, ssh_access, base, unit, name, content
):
    manager = ssh_access.SshAccessManager(
        root=tmp_path,
        runner=RecordingRunner(),
        expected_uid=os.getuid(),
        manage_account=False,
    )
    dropin = tmp_path / base / f"{unit}.d" / name
    dropin.parent.mkdir(parents=True)
    dropin.write_text(content)

    manager._validate_unit_dropins(unit)


def test_validation_rejects_systemd_prefix_dropin(tmp_path, ssh_access):
    manager = ssh_access.SshAccessManager(
        root=tmp_path,
        runner=RecordingRunner(),
        expected_uid=os.getuid(),
        manage_account=False,
    )
    dropin = (
        tmp_path
        / "usr/lib/systemd/system/robopark-support-.service.d/50-override.conf"
    )
    dropin.parent.mkdir(parents=True)
    dropin.write_text("[Service]\nExecStartPost=/bin/false\n")

    with pytest.raises(ssh_access.SecurityError, match="unexpected support SSH drop-in"):
        manager._validate_unit_dropins("robopark-support-sshd.service")


def test_disable_stops_support_units_before_reporting_corrupt_transition_state(
    tmp_path, ssh_access
):
    units = {
        "robopark-support-sshd.service",
        "robopark-support-tuna.service",
    }
    runner = RecordingRunner(active=units, enabled=units)
    manager = ssh_access.SshAccessManager(
        root=tmp_path,
        runner=runner,
        expected_uid=os.getuid(),
        manage_account=False,
    )
    state = tmp_path / "var/lib/robopark-ssh/test-transition-guards.json"
    state.parent.mkdir(parents=True)
    state.write_text("truncated")
    state.chmod(0o600)

    with pytest.raises(ssh_access.SecurityError):
        manager.disable()

    assert runner.enabled == set()
    assert runner.active == set()


def test_failed_enable_retry_does_not_restore_already_removed_guard(
    tmp_path, ssh_access
):
    units = {
        "robopark-support-sshd.service",
        "robopark-support-tuna.service",
    }
    runner = RecordingRunner(active=units, enabled=units)
    manager = ssh_access.SshAccessManager(
        root=tmp_path,
        runner=runner,
        expected_uid=os.getuid(),
        manage_account=False,
        listener_probe=lambda: False,
        stability_interval=0,
    )
    manager._write_transition_state(
        {"sshd_guard_removed": True, "tuna_guard_removed": True}
    )
    guard = manager.layout.transition_guard("robopark-support-tuna.service")

    with pytest.raises(RuntimeError, match="did not become ready"):
        manager.enable(validate=False)

    assert not guard.exists()
    assert manager._load_transition_state()["tuna_guard_removed"] is True


def test_help_runs_on_non_linux_without_privileges():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert "install" in result.stdout and "status" in result.stdout


def test_install_cli_defaults_to_sibling_public_key(ssh_access):
    args = ssh_access.parser().parse_args(["install"])
    assert args.public_key_file is None


def test_installed_helper_rerun_reuses_managed_authorized_key(
    tmp_path, monkeypatch, ssh_access
):
    etc = tmp_path / "etc/robopark-ssh"
    etc.mkdir(parents=True)
    (etc / "authorized_keys").write_text(ed25519_key())
    (etc / "authorized_keys").chmod(0o644)
    (etc / "sshd_config").write_text("# ROBOPARK_SSH_ACCESS_MANAGED\nlegacy\n")
    units = tmp_path / "etc/systemd/system"
    units.mkdir(parents=True)
    for name in (
        "robopark-support-sshd.service",
        "robopark-support-tuna.service",
    ):
        (units / name).write_text("# ROBOPARK_SSH_ACCESS_MANAGED\nlegacy\n")
    source_env = tmp_path / "source.env"
    source_env.write_text("TUNA_TOKEN=abc_DEF-123.456\n")
    source_env.chmod(0o600)
    installed_helper = tmp_path / "usr/local/libexec/robopark-ssh-access.py"
    installed_helper.parent.mkdir(parents=True)
    installed_helper.write_bytes(SCRIPT.read_bytes())
    installed_helper.chmod(0o755)
    monkeypatch.setattr(ssh_access, "__file__", str(installed_helper))
    manager = ssh_access.SshAccessManager(
        root=tmp_path,
        runner=RecordingRunner(),
        expected_uid=os.getuid(),
        sshd_binary="/usr/sbin/sshd",
        tuna_binary="/usr/bin/tuna",
        manage_account=False,
        generate_host_key=False,
        listener_probe=lambda: True,
        stability_interval=0,
    )

    manager.install(None, tuna_env=source_env)
    manager.install(None, tuna_env=source_env)

    assert (etc / "authorized_keys").read_text() == (
        "# ROBOPARK_SSH_ACCESS_MANAGED\n" + ed25519_key()
    )


def test_existing_host_public_key_must_match_private_key(tmp_path, ssh_access):
    first = tmp_path / "first"
    second = tmp_path / "second"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(first)],
        check=True,
    )
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(second)],
        check=True,
    )
    manager = ssh_access.SshAccessManager(
        root=tmp_path,
        runner=subprocess.run,
        expected_uid=os.getuid(),
        manage_account=False,
        stability_interval=0,
    )

    with pytest.raises(ssh_access.SecurityError, match="host key pair"):
        manager._verify_host_key_pair(first, Path(str(second) + ".pub"))


def test_enable_failure_restores_both_units_exact_prior_state(tmp_path, ssh_access):
    sshd = "robopark-support-sshd.service"
    tuna = "robopark-support-tuna.service"
    runner = RecordingRunner(
        fail_tuna=True,
        active=(sshd,),
        enabled=(tuna,),
    )
    manager = ssh_access.SshAccessManager(
        root=tmp_path,
        runner=runner,
        expected_uid=os.getuid(),
        manage_account=False,
        listener_probe=lambda: True,
        stability_interval=0,
    )

    with pytest.raises(subprocess.CalledProcessError):
        manager.enable(validate=False)

    assert runner.active == {sshd}
    assert runner.enabled == {tuna}


def test_enable_rejects_tuna_that_exits_during_stability_window(tmp_path, ssh_access):
    tuna = "robopark-support-tuna.service"

    class EarlyExitRunner(RecordingRunner):
        def __init__(self):
            super().__init__()
            self.tuna_active_checks = 0

        def __call__(self, argv, **kwargs):
            if (
                argv[:3] == ["systemctl", "is-active", "--quiet"]
                and argv[3] == tuna
                and tuna in self.active
            ):
                self.tuna_active_checks += 1
                if self.tuna_active_checks == 2:
                    self.active.remove(tuna)
            return super().__call__(argv, **kwargs)

    runner = EarlyExitRunner()
    manager = ssh_access.SshAccessManager(
        root=tmp_path,
        runner=runner,
        expected_uid=os.getuid(),
        manage_account=False,
        listener_probe=lambda: True,
        stability_interval=0,
    )

    with pytest.raises(RuntimeError, match="external SSH check"):
        manager.enable(validate=False)

    assert runner.active == set()
    assert runner.enabled == set()


def test_disabled_install_rotates_managed_public_key_and_token(tmp_path, ssh_access):
    key_file = tmp_path / "operator.pub"
    source_env = tmp_path / "source.env"
    key_file.write_text(ed25519_key(seed=1))
    source_env.write_text("TUNA_TOKEN=first_token.123\n")
    source_env.chmod(0o600)
    runner = RecordingRunner()
    manager = ssh_access.SshAccessManager(
        root=tmp_path,
        runner=runner,
        expected_uid=os.getuid(),
        sshd_binary="/usr/sbin/sshd",
        tuna_binary="/usr/bin/tuna",
        manage_account=False,
        generate_host_key=False,
        listener_probe=lambda: True,
        stability_interval=0,
    )
    manager.install(key_file, tuna_env=source_env)

    key_file.write_text(ed25519_key(seed=2))
    source_env.write_text("TUNA_TOKEN=second_token.456\n")
    manager.install(key_file, tuna_env=source_env)

    assert (
        ed25519_key(seed=2).strip()
        in (tmp_path / "etc/robopark-ssh/authorized_keys").read_text()
    )
    assert (
        (tmp_path / "etc/robopark-ssh/tuna.env")
        .read_text()
        .endswith("TUNA_TOKEN=second_token.456\n")
    )


def test_active_install_refuses_credential_rotation(tmp_path, ssh_access):
    key_file = tmp_path / "operator.pub"
    source_env = tmp_path / "source.env"
    key_file.write_text(ed25519_key(seed=1))
    source_env.write_text("TUNA_TOKEN=first_token.123\n")
    source_env.chmod(0o600)
    runner = RecordingRunner()
    manager = ssh_access.SshAccessManager(
        root=tmp_path,
        runner=runner,
        expected_uid=os.getuid(),
        sshd_binary="/usr/sbin/sshd",
        tuna_binary="/usr/bin/tuna",
        manage_account=False,
        generate_host_key=False,
        listener_probe=lambda: True,
        stability_interval=0,
    )
    manager.install(key_file, tuna_env=source_env)
    runner.active.add("robopark-support-sshd.service")
    key_file.write_text(ed25519_key(seed=2))

    with pytest.raises(RuntimeError, match="disable support SSH"):
        manager.install(key_file, tuna_env=source_env)
