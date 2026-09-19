"""Direct Compose bootstrap never relies on repository-local credential files."""

import importlib.util
import stat
from pathlib import Path


def _module():
    path = Path("deploy/compose_secrets.py")
    assert path.is_file(), "direct Compose credential bootstrap is missing"
    spec = importlib.util.spec_from_file_location("compose_secrets", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_direct_compose_secret_bootstrap_creates_private_external_files(
    tmp_path, monkeypatch
):
    module = _module()
    ownership = []
    file_ownership = []
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        module.os,
        "chown",
        lambda path, uid, gid: ownership.append((Path(path), uid, gid)),
    )
    monkeypatch.setattr(
        module.os, "fchown", lambda _fd, uid, gid: file_ownership.append((uid, gid))
    )

    env_file = module.bootstrap_compose_secrets(
        tmp_path / "etc-robopark", api_uid=10001
    )

    password = tmp_path / "etc-robopark/postgres-password"
    pgpass = tmp_path / "etc-robopark/pgpass"
    assert stat.S_IMODE(password.stat().st_mode) == 0o600
    assert stat.S_IMODE(pgpass.stat().st_mode) == 0o600
    assert (0, 0) in file_ownership
    assert (10001, 10001) in file_ownership
    env_text = env_file.read_text()
    assert str(password) in env_text and str(pgpass) in env_text
    assert password.read_text().strip() not in env_text


def test_compose_requires_explicit_external_secret_paths():
    text = Path("deploy/docker-compose.yml").read_text()

    assert "ROBOPARK_POSTGRES_PASSWORD_FILE:?" in text
    assert "ROBOPARK_PGPASS_FILE:?" in text
    assert "./postgres-password" not in text
    assert "./pgpass" not in text


def test_direct_compose_uses_root_wrapper_without_sourcing_private_env():
    wrapper = Path("deploy/compose-production.sh")
    text = wrapper.read_text()

    assert wrapper.stat().st_mode & 0o111
    assert '"$(id -u)" = 0' in text
    assert "--env-file /etc/robopark/compose-secrets.env" in text
    assert "source " not in text and ". /etc/robopark/compose-secrets.env" not in text
    for documentation in (Path("README.md"), Path("deploy/README.md")):
        body = documentation.read_text()
        assert "sudo ./compose-production.sh up" in body
        assert ". /etc/robopark/compose-secrets.env" not in body
