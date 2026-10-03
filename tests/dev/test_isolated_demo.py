"""The local integration demo cannot inherit a working installation's secrets."""

from __future__ import annotations

import json
import secrets
import subprocess
import sys
from io import BytesIO
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import isolated_demo
from isolated_demo import isolated_app_env


def test_app_env_uses_only_temporary_data_and_generated_credentials(
    tmp_path: Path,
) -> None:
    inherited = {
        "PATH": "/usr/bin",
        "DATABASE_URL": secrets.token_urlsafe(24),
        "SECRET_KEY": secrets.token_urlsafe(24),
        "SEED_PASSWORD": secrets.token_urlsafe(24),
        "TRACKER_API_BASE": "https://example.invalid",
        "HTTPS_PROXY": "https://example.invalid",
        "OPS_HOST_ROOT": "/var/lib/robopark",
        "DEV_SEED": "true",
    }
    database_url = (
        f"postgresql+psycopg://owner:{secrets.token_hex(24)}@127.0.0.1:5432/robopark"
    )
    owner_password = secrets.token_urlsafe(32)
    secret_key = secrets.token_urlsafe(48)
    api_src = ROOT / "apps" / "api" / "src"
    env = isolated_app_env(
        inherited,
        root=tmp_path,
        api_src=api_src,
        database_url=database_url,
        owner_username="isolated-owner",
        owner_password=owner_password,
        secret_key=secret_key,
        web_origin="http://127.0.0.1:49124",
    )

    assert env["DATABASE_URL"] == database_url
    assert env["SEED_PASSWORD"] == owner_password
    assert env["SECRET_KEY"] == secret_key
    assert env["DEV_SEED"] == "false"
    assert env["OPS_APPLY_ROOT"] == str(tmp_path / "apply")
    assert env["OPS_HOST_ENV_PATH"] == str(tmp_path / "host.env")
    assert str(api_src) in env["PYTHONPATH"].split(":")
    assert "TRACKER_API_BASE" not in env
    assert "HTTPS_PROXY" not in env
    assert "OPS_HOST_ROOT" not in env
    assert inherited["DATABASE_URL"] != env["DATABASE_URL"]
    assert inherited["SECRET_KEY"] != env["SECRET_KEY"]
    assert "ROBOPARK_ISOLATED_DEMO_INTEGRATIONS" not in env

    integrated = isolated_app_env(
        inherited,
        root=tmp_path,
        api_src=api_src,
        database_url=database_url,
        owner_username="isolated-owner",
        owner_password=owner_password,
        secret_key=secret_key,
        web_origin="http://127.0.0.1:49124",
        allow_integrations=True,
    )
    assert integrated["ROBOPARK_ISOLATED_DEMO_INTEGRATIONS"] == "1"


def test_app_env_canonicalizes_temporary_root_before_retention_paths(
    tmp_path: Path,
) -> None:
    actual = tmp_path / "actual" / "demo"
    actual.mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(actual.parent, target_is_directory=True)

    env = isolated_app_env(
        {"PATH": "/usr/bin"},
        root=alias / "demo",
        api_src=ROOT / "apps" / "api" / "src",
        database_url="postgresql+psycopg://demo@127.0.0.1/demo",
        owner_username="demo",
        owner_password=secrets.token_urlsafe(32),
        secret_key=secrets.token_urlsafe(48),
        web_origin="http://127.0.0.1:49124",
    )

    assert env["STAGED_ATTACHMENTS_DIR"] == str(actual / "data" / "task-attachments")
    assert env["HOST_DATA_PATH"] == str(actual / "data")
    Path(env["STAGED_ATTACHMENTS_DIR"]).mkdir(parents=True)
    api_src = str(ROOT / "apps" / "api" / "src")
    sys.path.insert(0, api_src)
    try:
        from robopark_api.services.storage_retention import pinned_directory

        with pinned_directory(Path(env["STAGED_ATTACHMENTS_DIR"])):
            pass
    finally:
        sys.path.remove(api_src)


def test_demo_network_guard_allows_only_loopback_hosts() -> None:
    guard_dir = ROOT / "scripts" / "isolated_demo_guard"
    sys.path.insert(0, str(guard_dir))
    try:
        from sitecustomize import is_loopback_host

        assert is_loopback_host("127.0.0.1")
        assert is_loopback_host("localhost")
        assert is_loopback_host("::1")
        assert not is_loopback_host("example.invalid")
        assert not is_loopback_host("127.0.0.2")
    finally:
        sys.path.remove(str(guard_dir))


def test_demo_child_blocks_remote_dns_and_tcp(tmp_path: Path) -> None:
    guard_dir = ROOT / "scripts" / "isolated_demo_guard"
    code = (
        "import socket; "
        "assert socket.socket.connect.__name__ == 'guarded_connect'; "
        "assert socket.getaddrinfo.__name__ == 'guarded_getaddrinfo'; "
        "\ntry: socket.getaddrinfo('example.invalid', 443)\n"
        "except PermissionError: pass\n"
        "else: raise AssertionError('remote DNS was allowed')\n"
        "\ntry: socket.socket().connect(('203.0.113.1', 443))\n"
        "except PermissionError: pass\n"
        "else: raise AssertionError('remote TCP was allowed')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env={"PYTHONPATH": str(guard_dir), "ROBOPARK_ISOLATED_DEMO": "1"},
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_integration_demo_allows_only_tracker_and_robot_https(tmp_path: Path) -> None:
    guard_dir = ROOT / "scripts" / "isolated_demo_guard"
    code = """
import socket
import sitecustomize

def resolve(host, port, *args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '',
             (('203.0.113.10' if host == 'st-api.yandex-team.ru' else '203.0.113.11'), port))]

sitecustomize._getaddrinfo = resolve
connected = []
sitecustomize._connect = lambda self, address: connected.append(address)
socket.getaddrinfo('st-api.yandex-team.ru', 443)
socket.getaddrinfo('emergency.sdc.yandex-team.ru', 443)
socket.socket().connect(('203.0.113.10', 443))
socket.socket().connect(('203.0.113.11', 443))
assert len(connected) == 2
for action in (
    lambda: socket.getaddrinfo('example.invalid', 443),
    lambda: socket.getaddrinfo('st-api.yandex-team.ru', 80),
    lambda: socket.socket().connect(('203.0.113.10', 80)),
    lambda: socket.socket().connect(('203.0.113.12', 443)),
):
    try:
        action()
    except PermissionError:
        pass
    else:
        raise AssertionError('unexpected external connection allowed')
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env={
            "PYTHONPATH": str(guard_dir),
            "ROBOPARK_ISOLATED_DEMO": "1",
            "ROBOPARK_ISOLATED_DEMO_INTEGRATIONS": "1",
        },
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_failed_container_creation_never_cleans_up_an_unowned_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stopped: list[tuple[list, str | None]] = []

    def fake_run(args: list[str], **_kwargs) -> str:
        if args[:3] == ["docker", "image", "inspect"]:
            return ""
        if args[:2] == ["docker", "run"]:
            raise RuntimeError("container name already exists")
        raise AssertionError("unexpected command")

    monkeypatch.setattr(isolated_demo, "_run", fake_run)
    ports = iter((49123, 49124))
    monkeypatch.setattr(isolated_demo, "_free_port", lambda: next(ports))
    monkeypatch.setattr(
        isolated_demo,
        "_stop_owned",
        lambda processes, container: stopped.append((processes, container)),
    )
    with pytest.raises(RuntimeError, match="container name already exists"):
        isolated_demo.run_demo(smoke=True)
    assert stopped == [([], None)]


@pytest.mark.parametrize(
    "summary",
    [
        {
            "worker_health": "worker_heartbeat_missing",
            "metrics_stale": True,
            "metrics": None,
        },
        {
            "worker_health": "worker_healthy",
            "metrics_stale": True,
            "metrics": {"host": {}},
        },
        {"worker_health": "worker_healthy", "metrics_stale": False, "metrics": {}},
        {
            "worker_health": "worker_healthy",
            "metrics_stale": False,
            "metrics": {"host": {}},
        },
    ],
)
def test_demo_system_smoke_rejects_missing_or_stale_telemetry(summary: dict) -> None:
    class Response(BytesIO):
        status = 200

    class Opener:
        def open(self, url: str, *, timeout: int):
            assert url == "http://127.0.0.1:49123/admin/system/summary"
            assert timeout == 5
            return Response(json.dumps(summary).encode())

    with pytest.raises(RuntimeError, match="isolated system telemetry unhealthy"):
        isolated_demo._smoke_system(Opener(), "http://127.0.0.1:49123")


def test_demo_system_smoke_accepts_a_fresh_worker_and_host_sample() -> None:
    class Response(BytesIO):
        status = 200

    class Opener:
        def open(self, _url: str, *, timeout: int):
            assert timeout == 5
            return Response(
                b'{"worker_health":"worker_healthy","metrics_stale":false,"metrics":{"host":{"disk":{"source_state":"measured","total_bytes":100,"free_bytes":50},"postgresql":{"state":"ok"},"host_health_source_state":"unavailable"}}}'
            )

    isolated_demo._smoke_system(Opener(), "http://127.0.0.1:49123")


def test_role_scope_smoke_rejects_foreign_park_and_accepts_one_assignment() -> None:
    user = {
        "role": "mechanic", "access_status": "approved", "must_change_password": False,
        "parks": [{"id": 7}],
    }
    isolated_demo._assert_role_scope(user, [{"id": 7}], role="mechanic", park_id=7)
    with pytest.raises(RuntimeError, match="isolated role park scope failed"):
        isolated_demo._assert_role_scope(
            user, [{"id": 7}, {"id": 8}], role="mechanic", park_id=7
        )


def test_interactive_demo_writes_all_generated_role_logins_to_private_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "login.json"
    accounts = [
        {"role": role, "username": f"demo-{role}", "password": secrets.token_urlsafe(32)}
        for role in ("royal", "admin", "operator", "mechanic", "driver")
    ]

    isolated_demo._write_login_file(path, accounts)

    assert path.stat().st_mode & 0o777 == 0o600
    saved = json.loads(path.read_text())
    assert [account["role"] for account in saved["accounts"]] == [
        "royal", "admin", "operator", "mechanic", "driver",
    ]
    assert len(saved["accounts"]) == 5


def test_interactive_demo_describes_login_file_for_all_roles_without_secrets(
    tmp_path: Path,
) -> None:
    path = tmp_path / "role-logins.json"

    notice = isolated_demo._login_file_notice(path)

    assert "all five roles" in notice
    assert str(path) in notice
    assert "owner login" not in notice
