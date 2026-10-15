from __future__ import annotations

import base64
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

ROOT = Path(__file__).resolve().parents[2]


def builder():
    spec = importlib.util.spec_from_file_location(
        "build_macos_pkg", ROOT / "scripts/build_macos_pkg.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_catalog_is_signed_binds_exact_bytes_and_uses_persistent_root(tmp_path):
    module = builder()
    package = tmp_path / "Robopark-0.2.0-rc.44-arm64.pkg"
    package.write_bytes(b"test-only-pkg-bytes")
    private = tmp_path / "private.key"
    public = module.ensure_update_key(private)
    assert module.ensure_update_key(private) == public
    assert private.stat().st_mode & 0o777 == 0o600
    envelope = module.sign_catalog(
        package, "0.2.0-rc.44", private, generation=11, now=1000
    )
    payload = base64.b64decode(envelope["payload"])
    Ed25519PublicKey.from_public_bytes(base64.b64decode(public)).verify(
        base64.b64decode(envelope["signature"]), payload
    )
    catalog = json.loads(payload)
    assert catalog["generation"] == 11
    assert catalog["expires"] == 1000 + 14 * 86400
    assert catalog["channels"]["rc"]["length"] == len(package.read_bytes())
    assert (
        catalog["channels"]["rc"]["url"] == "/api/distribution/v1/macos/" + package.name
    )
    assert set(catalog["channels"]) == {"rc"}


def test_installer_runs_as_user_and_does_not_embed_private_key(tmp_path):
    module = builder()
    import plistlib

    profile = module.launch_agent()
    assert profile["RunAtLoad"] is True
    assert "UserName" not in profile
    assert profile["KeepAlive"] == {"SuccessfulExit": False}
    assert profile["ProgramArguments"][-1] == "--background"
    assert "private" not in plistlib.dumps(profile).decode()


def test_automatic_updater_is_fixed_root_daemon_without_user_arguments():
    module = builder()
    daemon = module.update_daemon()
    assert daemon["Label"] == "ru.robopark.update-helper"
    assert daemon["UserName"] == "root"
    assert daemon["RunAtLoad"] is True
    assert 900 <= daemon["StartInterval"] <= 6 * 60 * 60
    assert daemon["ProgramArguments"] == [
        "/Library/PrivilegedHelperTools/RoboparkUpdater"
    ]
    assert "KeepAlive" not in daemon


def test_package_bundles_and_individually_signs_compute_worker_and_uninstaller():
    source = (ROOT / "scripts/build_macos_pkg.py").read_text()
    assert '"--product", "RoboparkComputeWorker"' in source
    assert 'bin_path / "RoboparkComputeWorker"' in source
    assert "sign_application_target(\n            compute_worker," in source
    assert 'identifier="ru.robopark.agent.compute"' in source
    assert 'identifier="ru.robopark.agent.updater"' in source
    assert 'identifier="ru.robopark.agent"' in source
    assert 'sign_application or "-"' not in source
    assert 'ROOT / "apps/macos/Resources/compute-peer.html"' in source
    assert 'ROOT / "scripts/macos_uninstall.sh"' in source
    assert '(resources / "uninstall.command").chmod(0o755)' in source
    assert "Refusing to downgrade Robopark" in source
    assert "lifecycle.lock" in source and "verified-update.pkg" in source


def test_application_signing_uses_stable_test_identity_and_preserves_developer_id(
    tmp_path, monkeypatch
):
    import contextlib
    import subprocess

    module = builder()
    target = tmp_path / "RoboparkAgent"
    target.write_bytes(b"fixture")
    calls: list[tuple] = []

    class Identity:
        fingerprint = "public-test-certificate-fingerprint"

    class Signer:
        def sign(self, signed_target, *, identifier):
            calls.append(("test", signed_target, identifier))

    monkeypatch.setattr(module, "ensure_test_signing_identity", lambda path, **kwargs: Identity())

    @contextlib.contextmanager
    def isolated(identity):
        assert isinstance(identity, Identity)
        yield Signer()

    monkeypatch.setattr(module, "isolated_test_signer", isolated)
    fingerprint = module.sign_application_target(
        target,
        identifier="ru.robopark.agent",
        developer_identity=None,
        test_secret_dir=tmp_path / ".release-secrets",
    )
    assert fingerprint == Identity.fingerprint
    assert calls == [("test", target, "ru.robopark.agent")]

    def fake_run(command, *, check):
        calls.append(("developer", command, check))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module.sign_application_target(
        target,
        identifier="ru.robopark.agent",
        developer_identity="Developer ID Application: Example",
        test_secret_dir=tmp_path / "unused",
    ) is None
    assert calls[-2][1] == [
        "codesign", "--force", "--sign", "Developer ID Application: Example",
        "--options", "runtime", "--timestamp", "--identifier",
        "ru.robopark.agent", str(target),
    ]
    assert calls[-1][1] == ["codesign", "--verify", "--strict", str(target)]


@pytest.mark.parametrize("bootout_status", [0, 3], ids=["existing-job", "fresh-install"])
def test_installer_replaces_launch_agent_registration_before_starting(
    tmp_path, bootout_status
):
    module = builder()
    launchctl = tmp_path / "launchctl"
    calls = tmp_path / "calls"
    plist = tmp_path / "ru.robopark.agent.plist"
    launchctl.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$LAUNCHCTL_CALLS\"\n"
        "[ \"$1\" = bootout ] && exit \"$BOOTOUT_STATUS\"\n"
        "exit 0\n"
    )
    launchctl.chmod(0o755)

    result = subprocess.run(
        [
            "/bin/sh",
            "-eu",
            "-c",
            "session_uid=501\n"
            + module.launch_agent_registration_script(
                launchctl=launchctl, plist=plist
            ),
        ],
        env={
            **os.environ,
            "BOOTOUT_STATUS": str(bootout_status),
            "LAUNCHCTL_CALLS": str(calls),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert calls.read_text().splitlines() == [
        "bootout gui/501/ru.robopark.agent",
        f"bootstrap gui/501 {plist}",
        "kickstart gui/501/ru.robopark.agent",
    ]


def test_installer_preserves_disabled_autostart_without_process_name_kills():
    module = builder()
    script = module.postinstall_script()
    assert module.launch_agent_update_script().rstrip() in script
    assert "print-disabled" in script
    assert "ru.robopark.agent" in script
    assert "pkill" not in script
    assert "killall" not in script
    assert "update-helper" in script


@pytest.mark.parametrize("disabled,running,bootstrap_error", [
    (False, True, False), (True, True, False), (True, False, False), (True, True, True),
])
def test_installer_restarts_live_disabled_agent_and_restores_preference(
    tmp_path, disabled, running, bootstrap_error
):
    module = builder()
    command, calls = tmp_path / "launchctl", tmp_path / "calls"
    command.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\n'
        'case "$1" in\n'
        'print-disabled) [ "$DISABLED" = 1 ] && echo \'"ru.robopark.agent" => true\';;\n'
        'print) [ "$RUNNING" = 1 ] && echo "pid = 1234";;\n'
        'bootstrap) [ "$FAIL" = 1 ] && exit 7;;\n'
        'esac\nexit 0\n'
    )
    command.chmod(0o755)
    result = subprocess.run(["/bin/sh", "-eu", "-c", "session_uid=501\n" +
        module.launch_agent_update_script(launchctl=command, process_list="/usr/bin/true")], capture_output=True, text=True, check=False,
        env={**os.environ, "CALLS": str(calls), "DISABLED": str(int(disabled)),
             "RUNNING": str(int(running)), "FAIL": str(int(bootstrap_error))})
    observed = calls.read_text().splitlines()
    assert (result.returncode != 0) == bootstrap_error
    service = "gui/501/ru.robopark.agent"
    if disabled and not running:
        assert observed == ["print-disabled gui/501", f"print {service}", f"bootout {service}"]
    else:
        assert f"bootout {service}" in observed
        assert any(line.startswith("bootstrap gui/501 ") for line in observed)
        assert (f"kickstart {service}" in observed) != bootstrap_error
        if disabled:
            assert f"enable {service}" in observed
            assert observed[-1] == f"disable {service}"
        else:
            assert not any(line.startswith(("enable ", "disable ")) for line in observed)


@pytest.mark.parametrize("disabled,timeout", [(False, False), (True, False), (True, True)])
def test_manual_running_app_is_stopped_by_exact_uid_and_path(tmp_path, disabled, timeout):
    import sys

    module = builder()
    calls, alive = tmp_path / "calls", tmp_path / "alive"
    alive.write_text("alive")
    ps, kill, launchctl = [tmp_path / name for name in ("ps", "kill", "launchctl")]
    ps.write_text(f"#!{sys.executable}\nimport pathlib,sys\n"
        "app='/Applications/Robopark.app/Contents/MacOS/RoboparkAgent'\n"
        "if sys.argv[1]=='-axo':\n"
        " print('123 501 '+app);print('456 502 '+app);print('789 501 /tmp/RoboparkAgent')\n"
        f"elif pathlib.Path({str(alive)!r}).exists():\n"
        " print('501 '+app)\n")
    kill.write_text(f"#!{sys.executable}\nimport pathlib,sys\n"
        f"with open({str(calls)!r},'a') as f:f.write('kill '+' '.join(sys.argv[1:])+'\\n')\n"
        + ("" if timeout else f"pathlib.Path({str(alive)!r}).unlink()\n"))
    launchctl.write_text(f"#!{sys.executable}\nimport sys\n"
        f"with open({str(calls)!r},'a') as f:f.write(' '.join(sys.argv[1:])+'\\n')\n"
        + ("if sys.argv[1]=='print-disabled':print('\"ru.robopark.agent\" => true')\n" if disabled else ""))
    for path in (ps, kill, launchctl): path.chmod(0o755)
    script = module.launch_agent_update_script(launchctl=launchctl, process_list=ps,
                                               terminate=kill, pause="/usr/bin/true")
    result = subprocess.run(["/bin/sh", "-eu", "-c", "session_uid=501\n" + script],
                            capture_output=True, text=True, check=False)
    observed = calls.read_text().splitlines()
    assert (result.returncode != 0) == timeout, result.stderr
    assert [line for line in observed if line.startswith("kill ")] == ["kill -TERM 123"]
    assert ("kickstart gui/501/ru.robopark.agent" in observed) != timeout
    if disabled:
        assert observed[-1] == "disable gui/501/ru.robopark.agent"


def test_update_configuration_accepts_only_signed_test_assignment(tmp_path):
    import time

    import pytest

    from scripts.sign_macos_deployment import sign_profile

    module = builder()
    private = tmp_path / "private.key"
    public = module.ensure_update_key(private)
    instance = "d6ed3b43-c41b-4614-833e-c5ec30936ef3"
    envelope = sign_profile(
        origin="https://roboparktest.ru.tuna.am",
        instance_id=instance,
        environment="test",
        channel="rc",
        expires=int(time.time()) + 86400,
        key=private,
    )
    config = module.automatic_update_config(envelope, public)
    assert config == {
        "schema": 1,
        "origin": "https://roboparktest.ru.tuna.am",
        "instance_id": instance,
        "environment": "test",
        "channel": "rc",
    }
    wrong_origin = sign_profile(
        origin="https://production.example",
        instance_id=instance,
        environment="test",
        channel="rc",
        expires=int(time.time()) + 86400,
        key=private,
    )
    with pytest.raises(ValueError, match="invalid_deployment_profile"):
        module.automatic_update_config(wrong_origin, public)

    production = sign_profile(
        origin="https://roboparktest.ru.tuna.am",
        instance_id=instance,
        environment="production",
        channel="rc",
        expires=int(time.time()) + 86400,
        key=private,
    )
    with pytest.raises(ValueError, match="invalid_deployment_profile"):
        module.automatic_update_config(production, public)

    wrong_instance = sign_profile(
        origin="https://roboparktest.ru.tuna.am",
        instance_id="50b4ae12-c0d8-4c76-9639-2984f164408b",
        environment="test",
        channel="rc",
        expires=int(time.time()) + 86400,
        key=private,
    )
    with pytest.raises(ValueError, match="invalid_deployment_profile"):
        module.automatic_update_config(wrong_instance, public)


def test_invalid_versions_are_not_used_as_package_paths():
    import pytest

    module = builder()
    for version in ("../../bad", "1;echo bad", ""):
        with pytest.raises(ValueError):
            module.package_name(version)


def test_bundle_versions_are_apple_numeric_and_keep_release_identity():
    import re

    module = builder()
    first = module.bundle_versions("0.2.0-rc.44.dev7114536505405942333", 1791900000)
    later = module.bundle_versions("0.2.0-rc.44.dev7114536505405942334", 1791986400)
    assert first["CFBundleShortVersionString"] == "0.2.0"
    assert first["RoboparkReleaseVersion"].endswith("2333")
    assert re.fullmatch(
        r"[1-9][0-9]{0,3}\.[0-9]{1,2}\.[0-9]{1,2}", first["CFBundleVersion"]
    )
    assert tuple(map(int, later["CFBundleVersion"].split("."))) > tuple(
        map(int, first["CFBundleVersion"].split("."))
    )


def test_deployment_signature_binds_environment_instance_and_origin(tmp_path):
    import time

    from scripts.sign_macos_deployment import sign_profile

    module = builder()
    private = tmp_path / "private.key"
    public = module.ensure_update_key(private)
    envelope = sign_profile(
        origin="https://TEST.example:443/",
        instance_id="50b4ae12-c0d8-4c76-9639-2984f164408b",
        environment="test",
        channel="rc",
        expires=int(time.time()) + 86400,
        key=private,
    )
    data = base64.b64decode(envelope["payload"])
    Ed25519PublicKey.from_public_bytes(base64.b64decode(public)).verify(
        base64.b64decode(envelope["signature"]), data
    )
    profile = json.loads(data)
    assert profile["origin"] == "https://test.example"
    assert profile["environment"] == "test"
    assert profile["purpose"] == "robopark-deployment-v1"


def test_workspace_bundle_contains_verified_assets_and_source_identity(tmp_path):
    import hashlib

    module = builder()
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        '<head></head><script src="/assets/app.js"></script>'
    )
    (dist / "assets/app.js").write_text('console.log("offline")')
    target = tmp_path / "workspace"
    result = module.stage_workspace(
        dist, target, version="0.2.0-rc.44", commit="a" * 40
    )
    assert result["version"] == "0.2.0-rc.44"
    assert result["source_commit"] == "a" * 40
    assert (target / "index.html").read_bytes() == (dist / "index.html").read_bytes()
    assert (
        result["files"]["assets/app.js"]["sha256"]
        == hashlib.sha256((target / "assets/app.js").read_bytes()).hexdigest()
    )
    assert json.loads((target / "workspace-manifest.json").read_text()) == result


def test_workspace_bundle_rejects_missing_assets_and_symlink(tmp_path):
    import pytest

    module = builder()
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text('<script src="/assets/missing.js"></script>')
    with pytest.raises(ValueError, match="workspace_entry_asset_missing"):
        module.stage_workspace(
            dist, tmp_path / "first", version="1.0.0", commit="a" * 40
        )
    (dist / "index.html").write_text("<head></head>")
    (dist / "outside.js").symlink_to(tmp_path / "private.key")
    with pytest.raises(ValueError, match="workspace_symlink"):
        module.stage_workspace(
            dist, tmp_path / "second", version="1.0.0", commit="a" * 40
        )
