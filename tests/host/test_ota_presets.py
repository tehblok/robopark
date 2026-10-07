from __future__ import annotations

import stat
import zipfile

import pytest
from cryptography.fernet import Fernet
from robopark_ota.presets import PRESET_MEMBER, preset_key_file


def _bundle(tmp_path, token):
    path = tmp_path / "preset.ota"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(PRESET_MEMBER, token)
    return path


def test_preset_key_is_authenticated_then_removed_even_on_failure(tmp_path):
    key = Fernet.generate_key()
    bundle = _bundle(tmp_path, Fernet(key).encrypt(b"{}"))
    with (
        pytest.raises(RuntimeError, match="installation_failed"),
        preset_key_file(
            bundle, tmp_path / "run", getpass_fn=lambda _: key.decode()
        ) as path,
    ):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert path.read_bytes() == key
        raise RuntimeError("installation_failed")
    assert not path.exists()


def test_wrong_key_does_not_create_a_secret_file(tmp_path):
    bundle = _bundle(tmp_path, Fernet(Fernet.generate_key()).encrypt(b"{}"))
    with (
        pytest.raises(ValueError, match="preset_key_invalid"),
        preset_key_file(
            bundle,
            tmp_path / "run",
            getpass_fn=lambda _: Fernet.generate_key().decode(),
        ),
    ):
        pytest.fail("wrong key accepted")
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize("cipher", [b"broken", b"gA==", b"x" * (1024 * 1024 + 1)])
def test_preset_refuses_malformed_or_oversized_ciphertext(tmp_path, cipher):
    bundle = _bundle(tmp_path, cipher)
    with (
        pytest.raises(ValueError, match="preset_(key_invalid|unavailable)"),
        preset_key_file(
            bundle,
            tmp_path / "run",
            getpass_fn=lambda _: Fernet.generate_key().decode(),
        ),
    ):
        pytest.fail("invalid preset accepted")


def test_preset_refuses_symlink_credential_directory(tmp_path):
    key = Fernet.generate_key()
    bundle = _bundle(tmp_path, Fernet(key).encrypt(b"{}"))
    (tmp_path / "target").mkdir()
    (tmp_path / "run").symlink_to(tmp_path / "target")
    with (
        pytest.raises(ValueError, match="unsafe_credential_directory"),
        preset_key_file(bundle, tmp_path / "run", getpass_fn=lambda _: key.decode()),
    ):
        pytest.fail("symlink accepted")


def test_invalid_preset_key_prevents_storage_or_package_changes(monkeypatch, tmp_path):
    from robopark_ota import cli

    class Runtime:
        def __init__(self, *args, **kwargs):
            pass

    def reject_key(*args, **kwargs):
        raise ValueError("preset_key_invalid")

    monkeypatch.setattr(cli, "HostInstallRuntime", Runtime)
    monkeypatch.setattr(cli, "preset_key_file", reject_key)
    monkeypatch.setattr(
        cli.storage,
        "choose_install_storage",
        lambda _: pytest.fail("storage changed before key verification"),
    )
    with pytest.raises(ValueError, match="preset_key_invalid"):
        cli._clean_install(tmp_path / "release.ota", tmp_path, preset="robopark")
