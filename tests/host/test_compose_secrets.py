"""Direct Compose bootstrap never relies on repository-local credential files."""

import importlib.util
import os
import shutil
import stat
import subprocess
from pathlib import Path

import yaml


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

    host_env = tmp_path / "host.env"
    host_env.write_text(
        "CORS_ORIGINS=https://robopark.example\n"
        "UVICORN_WORKERS=2\n"
        "SEED_PASSWORD=seed-secret\n"
        "OPERATOR_SHARED_PASSWORD=operator-secret\n"
        "SECRET_KEY=fernet-secret\n"
        "DATABASE_URL=postgresql://db-secret\n"
    )
    host_env.chmod(0o600)
    env_file = module.bootstrap_compose_secrets(
        tmp_path / "etc-robopark",
        host_env=host_env,
        host_env_owner_uid=host_env.stat().st_uid,
        api_uid=10001,
    )

    password = tmp_path / "etc-robopark/postgres-password"
    pgpass = tmp_path / "etc-robopark/pgpass"
    bot_bridge_key = tmp_path / "etc-robopark/bot-bridge-key"
    assert stat.S_IMODE(password.stat().st_mode) == 0o600
    assert stat.S_IMODE(pgpass.stat().st_mode) == 0o600
    assert stat.S_IMODE(bot_bridge_key.stat().st_mode) == 0o600
    assert (0, 0) in file_ownership
    assert (10001, 10001) in file_ownership
    env_text = env_file.read_text()
    assert str(password) in env_text and str(pgpass) in env_text
    assert password.read_text().strip() not in env_text
    assert bot_bridge_key.read_text().strip() not in env_text
    assert f"ROBOPARK_BOT_BRIDGE_KEY_FILE={bot_bridge_key}" in env_text
    snapshot_config = tmp_path / "etc-robopark/snapshot.env"
    assert stat.S_IMODE(snapshot_config.stat().st_mode) == 0o600
    assert (10001, 10001) in file_ownership
    snapshot_text = snapshot_config.read_text()
    assert snapshot_text == (
        "CORS_ORIGINS=https://robopark.example\nUVICORN_WORKERS=2\n"
    )
    for secret in (
        "seed-secret",
        "operator-secret",
        "fernet-secret",
        "db-secret",
        "SEED_PASSWORD",
        "OPERATOR_SHARED_PASSWORD",
        "SECRET_KEY",
        "DATABASE_URL",
    ):
        assert secret not in snapshot_text
    assert str(snapshot_config) in env_text


def test_compose_requires_explicit_external_secret_paths():
    text = Path("deploy/docker-compose.yml").read_text()
    compose = yaml.safe_load(text)

    assert "ROBOPARK_POSTGRES_PASSWORD_FILE:?" in text
    assert "ROBOPARK_PGPASS_FILE:?" in text
    assert "ROBOPARK_SNAPSHOT_CONFIG_FILE:?" in text
    assert "./postgres-password" not in text
    assert "./pgpass" not in text
    assert "/run/robopark/snapshot.env" in text
    api_volumes = compose["services"]["api"]["volumes"]
    host_env_mount = next(
        volume
        for volume in api_volumes
        if volume.endswith(":/host-repo/deploy/host.env:ro")
    )
    assert host_env_mount.startswith("${ROBOPARK_SNAPSHOT_CONFIG_FILE:")
    agent = compose["services"]["ops-agent"]
    assert agent["environment"] == {
        "HOST_ENV_FILE": "/host-repo/deploy/host.env",
        "ROBOPARK_POSTGRES_PASSWORD_FILE": "/etc/robopark/postgres-password",
        "ROBOPARK_PGPASS_FILE": "/etc/robopark/pgpass",
        "ROBOPARK_SNAPSHOT_CONFIG_FILE": "/etc/robopark/snapshot.env",
    }
    assert "/etc/robopark:/etc/robopark:ro" in agent["volumes"]


def test_direct_compose_uses_root_wrapper_without_sourcing_private_env():
    wrapper = Path("deploy/compose-production.sh")
    text = wrapper.read_text()

    assert wrapper.stat().st_mode & 0o111
    assert '"$(id -u)" = 0' in text
    assert '--host-env "$DEPLOY_DIR/host.env"' in text
    assert "--env-file /etc/robopark/compose-secrets.env" in text
    assert "source " not in text and ". /etc/robopark/compose-secrets.env" not in text
    for documentation in (Path("README.md"), Path("deploy/README.md")):
        body = documentation.read_text()
        assert "sudo ./compose-production.sh up" in body
        assert ". /etc/robopark/compose-secrets.env" not in body


def test_direct_compose_rejects_group_readable_host_env(tmp_path):
    module = _module()
    host_env = tmp_path / "host.env"
    host_env.write_text("SECRET_KEY=private\n")
    host_env.chmod(0o644)

    try:
        module.validate_host_env(host_env, owner_uid=os.geteuid())
    except ValueError as error:
        assert str(error) == "unsafe_host_env"
    else:
        raise AssertionError("secret-bearing host.env with mode 0644 was accepted")

    host_env.chmod(0o600)
    module.validate_host_env(host_env, owner_uid=os.geteuid())


def test_direct_compose_validates_host_env_before_creating_credentials(
    tmp_path, monkeypatch
):
    module = _module()
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    host_env = tmp_path / "host.env"
    host_env.write_text("SECRET_KEY=private\n")
    host_env.chmod(0o644)
    directory = tmp_path / "etc-robopark"

    try:
        module.bootstrap_compose_secrets(
            directory,
            host_env=host_env,
            host_env_owner_uid=host_env.stat().st_uid,
        )
    except ValueError as error:
        assert str(error) == "unsafe_host_env"
    else:
        raise AssertionError("unsafe host.env was accepted")

    assert not directory.exists()


def test_direct_compose_documentation_uses_only_private_wrapper_commands():
    for path, prefix in ((Path("README.md"), "./"), (Path("deploy/README.md"), "./")):
        readme = path.read_text()
        assert "install -o root -g root -m 0600 host.env.example host.env" in readme
        assert "sudoedit host.env" in readme
        assert "docker compose" not in readme
        assert f"sudo {prefix}compose-production.sh up" in readme


def test_real_compose_config_accepts_the_generated_sanitized_contract(tmp_path, monkeypatch):
    module = _module()
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module.os, "chown", lambda *_: None)
    monkeypatch.setattr(module.os, "fchown", lambda *_: None)
    host_env = tmp_path / "host.env"
    host_env.write_text("UVICORN_WORKERS=2\nSECRET_KEY=do-not-project\n")
    host_env.chmod(0o600)
    env_file = module.bootstrap_compose_secrets(
        tmp_path / "etc", host_env=host_env, host_env_owner_uid=host_env.stat().st_uid
    )
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "--project-directory",
            "deploy",
            "--file",
            "deploy/docker-compose.yml",
            "config",
            "--no-env-resolution",
            "--format",
            "json",
        ],
        env={**os.environ, "HOST_ENV_FILE": str(host_env)},
        check=True,
        capture_output=True,
        text=True,
    )
    assert "/run/robopark/snapshot.env" in result.stdout
    assert "do-not-project" not in (tmp_path / "etc/snapshot.env").read_text()


def test_legacy_host_and_backup_compose_path_renders_with_sanitized_config(
    tmp_path, monkeypatch
):
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    shutil.copyfile("deploy/docker-compose.yml", deploy / "docker-compose.yml")
    shutil.copyfile("deploy/compose_secrets.py", deploy / "compose_secrets.py")
    (deploy / "host.env").write_text("UVICORN_WORKERS=2\nSECRET_KEY=do-not-project\n")
    (deploy / "host.env").chmod(0o600)
    root = tmp_path / "root"
    secret_dir = root / "etc/robopark"
    monkeypatch.setenv("ROBOPARK_TESTING", "1")
    monkeypatch.setenv("ROBOPARK_ROOT", str(root))

    path = Path("deploy/host.py")
    spec = importlib.util.spec_from_file_location("legacy_host", path)
    assert spec and spec.loader
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    monkeypatch.setattr(host, "DEPLOY", deploy)
    monkeypatch.setattr(host, "COMPOSE_SECRET_DIR", secret_dir)

    result = host.compose("config", "--no-env-resolution", "--format", "json", capture_output=True)

    assert "/run/robopark/snapshot.env" in result.stdout
    assert (secret_dir / "snapshot.env").read_text() == "UVICORN_WORKERS=2\n"
