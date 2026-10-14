"""Focused contracts for the AGX archive entrypoint and boot firmware guard."""

import hashlib
import os
import subprocess
from pathlib import Path

import pytest
from robopark_ota.cli import _parser as ota_parser

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "deploy/install-archive/INSTALL-AGX.sh"
GUARD = ROOT / "deploy/install-archive/nvidia-bootfw-guard.sh"


def write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def test_operator_confirmation_reaches_read_only_entry(tmp_path: Path):
    package = tmp_path / "package"
    package.mkdir()
    for source in (INSTALLER, GUARD):
        target = package / source.name
        target.write_bytes(source.read_bytes())
        target.chmod(0o755)
    (package / "robopark-agx.py").write_text("entry")
    (package / "robopark-test.ota").write_text("ota")
    names = (
        "INSTALL-AGX.sh",
        "nvidia-bootfw-guard.sh",
        "robopark-agx.py",
        "robopark-test.ota",
    )
    (package / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256((package / name).read_bytes()).hexdigest()}  {name}\n"
            for name in names
        )
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "python.log"
    write_executable(
        fake_bin / "python3",
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$FAKE_LOG\"\n"
        "case \"$1:$2\" in\n"
        "  *robopark-test.ota:verify) printf '%s\\n' '{\"version\":\"test\"}' ;;\n"
        "  -c:*) printf '%s\\n' 'test' ;;\n"
        "esac\n",
    )
    result = subprocess.run(
        [str(package / "INSTALL-AGX.sh"), "--operator-confirmed-platform", "check"],
        cwd=package,
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "FAKE_LOG": str(log)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert any(
        call.endswith("robopark-agx.py check --operator-confirmed-platform")
        for call in log.read_text().splitlines()
    )


def test_installer_forwards_operator_confirmation_only_to_agx_entries():
    script = INSTALLER.read_text()

    assert 'python3 "$bundle" install' in script
    assert '"$bundle" install --operator-confirmed-platform' not in script
    assert "prepare --acceleration --apply" in script
    assert script.count("--operator-confirmed-platform") >= 5
    assert script.index("robopark_bootfw_guard_begin") > script.index('if [ "$action" = check ]')


def test_generic_ota_install_does_not_accept_agx_operator_confirmation():
    assert ota_parser().parse_args(["install"]).command == "install"
    with pytest.raises(SystemExit):
        ota_parser().parse_args(["install", "--operator-confirmed-platform"])


def guard_environment(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    postinst = tmp_path / "postinst"
    postinst.write_text("reviewed NVIDIA postinst fixture")
    status = tmp_path / "status"
    status.mkdir()
    (status / "nvidia-l4t-bootloader").write_text("install ok unpacked")
    (status / "nvidia-l4t-bsp").write_text("install ok half-configured")
    dpkg_log = tmp_path / "dpkg.log"
    write_executable(
        fake_bin / "dpkg-query",
        "#!/bin/sh\n"
        "if [ \"$1\" = --control-path ]; then printf '%s\\n' \"$FAKE_POSTINST\"; exit 0; fi\n"
        "for package do :; done\n"
        "test -f \"$FAKE_STATUS/$package\" && cat \"$FAKE_STATUS/$package\"\n",
    )
    write_executable(
        fake_bin / "sha256sum",
        "#!/bin/sh\n"
        "printf '%s  %s\\n' 26e63400bd5a7b6692917e1079f61561e42119bcd25d8bbbc2342051f0148099 \"$1\"\n",
    )
    write_executable(
        fake_bin / "dpkg",
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$FAKE_DPKG_LOG\"\n",
    )
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_POSTINST": str(postinst),
        "FAKE_STATUS": str(status),
        "FAKE_DPKG_LOG": str(dpkg_log),
    }
    return environment, dpkg_log, tmp_path / "marker"


def run_guard(environment: dict[str, str], marker: Path, body: str):
    return subprocess.run(
        [
            "/bin/sh",
            "-c",
            '. "$1"; RP_BOOTFW_MARKER=$2; ' + body,
            "guard-test",
            str(GUARD),
            str(marker),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_guard_configures_only_pending_packages_and_removes_its_marker(
    tmp_path: Path,
):
    environment, dpkg_log, marker = guard_environment(tmp_path)
    result = run_guard(
        environment,
        marker,
        "robopark_bootfw_guard_begin; test -e \"$RP_BOOTFW_MARKER\"; "
        "robopark_bootfw_guard_end; test ! -e \"$RP_BOOTFW_MARKER\"",
    )

    assert result.returncode == 0, result.stderr
    assert dpkg_log.read_text().strip() == (
        "--configure nvidia-l4t-bootloader nvidia-l4t-bsp"
    )


def test_guard_preserves_preexisting_marker(tmp_path: Path):
    environment, _dpkg_log, marker = guard_environment(tmp_path)
    marker.touch()
    result = run_guard(
        environment,
        marker,
        "robopark_bootfw_guard_begin; robopark_bootfw_guard_end; "
        "test -e \"$RP_BOOTFW_MARKER\"",
    )

    assert result.returncode == 0, result.stderr
    assert marker.exists()
