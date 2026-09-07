"""Operator tooling must fail safely without printing configuration secrets."""

import importlib.util
import os
import sqlite3
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("host", ROOT / "deploy/host.py")
host = importlib.util.module_from_spec(spec)


@pytest.fixture(autouse=True)
def load_host():
    spec.loader.exec_module(host)


def config(tmp_path):
    env = tmp_path / "host.env"
    env.write_text(
        "CORS_ORIGINS=https://fleet.company.ru\nCOOKIE_SECURE=true\n"
        "COOKIE_SAMESITE=lax\nDEV_SEED=false\nUVICORN_WORKERS=2\n"
        "SECRET_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=\n"
    )
    env.chmod(0o600)
    return env


def test_config_accepts_production_and_rejects_demo_without_secret_output(tmp_path):
    env = config(tmp_path)
    assert host.validate_config(env) == []
    with env.open("a") as stream:
        stream.write("DEV_SEED=true\n")
    issues = host.validate_config(env)
    assert any("DEV_SEED" in issue for issue in issues)
    assert "AAAAAAAA" not in str(issues)


@pytest.mark.parametrize(
    "setting", ["COOKIE_SECURE=false", "CORS_ORIGINS=*", "SECRET_KEY=bad", "UVICORN_WORKERS=8"]
)
def test_unsafe_host_settings_rejected(tmp_path, setting):
    env = config(tmp_path)
    with env.open("a") as stream:
        stream.write(setting + "\n")
    assert host.validate_config(env)


def test_config_rejects_world_readable_secrets(tmp_path):
    env = config(tmp_path)
    env.chmod(0o644)
    assert any("600" in issue for issue in host.validate_config(env))


def test_tuna_requires_stable_domain_and_token_without_leaking(tmp_path):
    env = tmp_path / "tuna.env"
    env.write_text("TUNA_TOKEN=private-token\nTUNA_BIND=127.0.0.1:8080\n")
    env.chmod(0o600)
    issues = host.validate_tuna(env)
    assert issues and "private-token" not in str(issues)
    with env.open("a") as stream:
        stream.write("TUNA_DOMAIN=fleet.example.org\n")
    assert host.validate_tuna(env) == []


def test_failed_backup_does_not_rotate_previous_archives(tmp_path, monkeypatch):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(mode=0o700)
    previous = backup_dir / "robopark-20200101T000000.zip"
    previous.write_bytes(b"previous")

    def fake_compose(*args, **kwargs):
        if args[0] == "exec":
            return subprocess.CompletedProcess(args, 0, "/data/ops/artifacts/snapshot.zip\n")
        Path(args[-1]).write_bytes(b"broken archive")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(host, "compose", fake_compose)
    with pytest.raises(host.HostError):
        host.backup(backup_dir, keep=1)
    assert previous.read_bytes() == b"previous"
    assert not list(backup_dir.glob("*.partial"))


def test_entrypoint_rejects_invalid_worker_count_before_migration(tmp_path):
    # Execute the real shell logic, replacing only its fixed container cwd.
    script = (
        (ROOT / "apps/api/docker-entrypoint.sh").read_text().replace("cd /app", f'cd "{tmp_path}"')
    )
    result = subprocess.run(
        ["sh", "-c", script],
        env={**os.environ, "UVICORN_WORKERS": "invalid"},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "UVICORN_WORKERS" in result.stderr
    assert "alembic" not in result.stderr


def test_scheduled_snapshot_is_restorable_and_rotates_only_its_own_artifact(
    tmp_path, monkeypatch, capsys
):
    from robopark_api.services.ops.archives import inspect_archive
    from robopark_api.services.ops.runner import OpsContext

    helper_spec = importlib.util.spec_from_file_location(
        "snapshot_helper", ROOT / "deploy/backup-snapshot.py"
    )
    helper = importlib.util.module_from_spec(helper_spec)
    helper_spec.loader.exec_module(helper)
    database = tmp_path / "robopark.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE example (value TEXT)")
        connection.execute("INSERT INTO example VALUES ('preserved')")
    env = tmp_path / "host.env"
    env.write_text("SECRET_KEY=private-fixture-key\n")
    ctx = OpsContext(
        ops_dir=tmp_path / "ops",
        database_url=f"sqlite:///{database}",
        config_files={"host.env": env},
    )
    monkeypatch.setattr(helper, "get_settings", lambda: None)
    monkeypatch.setattr(helper, "build_ops_context", lambda _: ctx)
    monkeypatch.setattr(sys, "argv", ["backup-snapshot.py"])
    helper.main()
    output = capsys.readouterr().out
    assert "private-fixture-key" not in output
    first = Path(output.strip())
    inspect_archive(first.read_bytes(), expected_kind="snapshot")
    with zipfile.ZipFile(first) as archive:
        assert archive.read("config/host.env") == env.read_bytes()
        restored = tmp_path / "restored.db"
        restored.write_bytes(archive.read("data/robopark.db"))
    with sqlite3.connect(restored) as connection:
        assert connection.execute("SELECT value FROM example").fetchone() == ("preserved",)
    receipt = ctx.ops_dir / "scheduled-copy.json"
    assert not receipt.exists()
    monkeypatch.setattr(sys, "argv", ["backup-snapshot.py", "--ack", first.name])
    helper.main()
    from robopark_api.services.operational_health import backup_status

    assert backup_status(ctx.ops_dir)["verified_at"] is not None
    manual = first.parent / "manual.zip"
    manual.write_bytes(b"manual")
    monkeypatch.setattr(sys, "argv", ["backup-snapshot.py"])
    helper.main()
    second = Path(capsys.readouterr().out.strip())
    monkeypatch.setattr(sys, "argv", ["backup-snapshot.py", "--ack", second.name])
    helper.main()
    assert second.exists() and not first.exists()
    assert manual.read_bytes() == b"manual"


def test_successful_backup_keeps_newest_private_copies(tmp_path, monkeypatch):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(mode=0o700)
    previous = backup_dir / "robopark-20200101T000000.zip"
    previous.write_bytes(b"previous")

    directory_synced = False
    real_fsync = os.fsync

    def track_fsync(fd):
        nonlocal directory_synced
        real_fsync(fd)
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            directory_synced = True

    def fake_compose(*args, **kwargs):
        if args[0] == "exec":
            if "--ack" in args:
                assert directory_synced, "Do not rotate the server copy before durable host rename"
            return subprocess.CompletedProcess(args, 0, "/data/ops/artifacts/snapshot.zip\n")
        with zipfile.ZipFile(args[-1], "w") as archive:
            archive.writestr("manifest.json", '{"kind":"snapshot"}')
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(host, "compose", fake_compose)
    monkeypatch.setattr(host.os, "fsync", track_fsync)
    final = host.backup(backup_dir, keep=1)
    assert final.is_file() and not previous.exists()
    assert final.stat().st_mode & 0o077 == 0
