import hashlib
import subprocess
from pathlib import Path

from scripts import build_macos_command


def test_single_file_installer_verifies_embedded_package_without_installing(tmp_path):
    package = tmp_path / "Robopark-test.pkg"
    package.write_bytes(b"xar!test-payload" * 20)
    output = tmp_path / "Robopark Install (1).command"
    build_macos_command.build(package, output, version="0.2.3-rc.2")
    checked = subprocess.run(
        ["/bin/bash", str(output), "--verify"], capture_output=True, text=True, check=False
    )
    assert checked.returncode == 0, checked.stderr
    assert hashlib.sha256(package.read_bytes()).hexdigest() in checked.stdout
    assert output.stat().st_mode & 0o111
    assert not list(tmp_path.glob(".payload*"))


def test_corrupt_installer_fails_before_installation(tmp_path):
    package = tmp_path / "payload.pkg"
    package.write_bytes(b"xar!test")
    output = tmp_path / "install.command"
    build_macos_command.build(package, output, version="0.2.3-rc.2")
    text = output.read_text().replace(
        hashlib.sha256(package.read_bytes()).hexdigest(), "0" * 64
    )
    output.write_text(text)
    checked = subprocess.run(
        ["/bin/bash", str(output), "--verify"], capture_output=True, text=True, check=False
    )
    assert checked.returncode != 0
    assert "SHA-256" in checked.stderr


def test_uninstaller_has_a_read_only_plan_and_keeps_data_by_default():
    script = Path(__file__).resolve().parents[2] / "scripts/macos_uninstall.sh"
    checked = subprocess.run(
        ["/bin/sh", str(script), "--plan"], capture_output=True, text=True, check=False
    )
    assert checked.returncode == 0
    assert "app data is preserved by default" in checked.stdout
    assert "Browser site data for 127.0.0.1 is never removed" in checked.stdout
    assert "/Library/LaunchDaemons/ru.robopark.update-helper.plist" in checked.stdout
    assert "spctl" not in script.read_text()


def test_builder_rejects_nonpackage_and_unsafe_version(tmp_path):
    import pytest

    package = tmp_path / "payload.pkg"
    package.write_bytes(b"not a xar")
    with pytest.raises(ValueError):
        build_macos_command.build(package, tmp_path / "a.command", version="0.2.3-rc.2")
    package.write_bytes(b"xar!valid")
    with pytest.raises(ValueError):
        build_macos_command.build(package, tmp_path / "a.command", version="$(id)")
