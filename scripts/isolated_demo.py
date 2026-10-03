#!/usr/bin/env python3
"""Run a disposable PostgreSQL, API, worker, and web demo on local ports.

The existing checkout's .env, database, containers, and fixed dev ports are
never used. Python services can connect only to loopback while this demo runs.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import IO
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "apps" / "api"
WEB = ROOT / "apps" / "web"
POSTGRES_IMAGE = "postgres:17-alpine"


def isolated_app_env(
    inherited: dict[str, str],
    *,
    root: Path,
    api_src: Path,
    database_url: str,
    owner_username: str,
    owner_password: str,
    secret_key: str,
    web_origin: str,
    allow_integrations: bool = False,
) -> dict[str, str]:
    """Build a minimal child environment instead of copying working secrets."""
    root = root.resolve(strict=True)
    env = {
        key: inherited[key]
        for key in ("PATH", "LANG", "LC_ALL", "TZ")
        if key in inherited
    }
    env.update(
        PYTHONPATH=os.pathsep.join(
            (str(ROOT / "scripts" / "isolated_demo_guard"), str(api_src))
        ),
        PYTHONDONTWRITEBYTECODE="1",
        ROBOPARK_ISOLATED_DEMO="1",
        DATABASE_URL=database_url,
        SECRET_KEY=secret_key,
        SEED_USERNAME=owner_username,
        SEED_PASSWORD=owner_password,
        SEED_ROLE="royal",
        DEV_SEED="false",
        COOKIE_SECURE="false",
        CORS_ORIGINS=web_origin,
        SESSION_COOKIE_NAME=f"robopark_isolated_{root.name[-12:]}",
        OPS_DIR=str(root / "ops"),
        OPS_APPLY_ROOT=str(root / "apply"),
        OPS_HOST_ENV_PATH=str(root / "host.env"),
        LIVE_MERGE_DIR=str(root / "data" / "live-merge"),
        STAGED_ATTACHMENTS_DIR=str(root / "data" / "task-attachments"),
        REPORT_ATTACHMENTS_DIR=str(root / "data" / "report-attachments"),
        INVENTORY_PHOTOS_DIR=str(root / "data" / "inventory-photos"),
        HOST_DATA_PATH=str(root / "data"),
        HOST_HEALTH_PATH=str(root / "ops" / "host-health.json"),
        TMPDIR=str(root),
    )
    if allow_integrations:
        env["ROBOPARK_ISOLATED_DEMO_INTEGRATIONS"] = "1"
    return env


def _run(
    args: list[str], *, env: dict[str, str] | None = None, timeout: int = 60
) -> str:
    result = subprocess.run(
        args, env=env, capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"local demo command failed: {args[0]} (exit {result.returncode})"
        )
    return result.stdout.strip()


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for(predicate, *, timeout: float, label: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.25)
    raise RuntimeError(f"{label} did not become ready")


def _http_ready(url: str) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=1) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _start_service(
    args: list[str], *, cwd: Path, env: dict[str, str], log: IO[str]
) -> subprocess.Popen:
    return subprocess.Popen(
        args, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT
    )


def _write_login_file(path: Path, accounts: list[dict[str, str]]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump({"accounts": accounts}, stream)
        stream.write("\n")


def _login_file_notice(path: Path) -> str:
    return f"One-time login file for all five roles (mode 0600): {path}"


def _random_password() -> str:
    for _ in range(32):
        candidate = secrets.token_urlsafe(32)
        classes = (
            any(char.islower() for char in candidate),
            any(char.isupper() for char in candidate),
            any(char.isdigit() for char in candidate),
            any(not char.isalnum() for char in candidate),
        )
        if sum(classes) >= 3:
            return candidate
    raise RuntimeError("isolated password generation failed")


def _smoke_login(
    api_origin: str, username: str, password: str
) -> urllib.request.OpenerDirector:
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
    )
    request = urllib.request.Request(
        f"{api_origin}/auth/login",
        data=json.dumps({"username": username, "password": password}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with opener.open(request, timeout=5) as response:
        if response.status != 204:
            raise RuntimeError("isolated owner login failed")
    with opener.open(f"{api_origin}/auth/me", timeout=5) as response:
        if response.status != 200 or json.load(response).get("username") != username:
            raise RuntimeError("isolated owner session failed")
    return opener


def _smoke_system(opener: urllib.request.OpenerDirector, api_origin: str) -> None:
    try:
        with opener.open(f"{api_origin}/admin/system/summary", timeout=5) as response:
            if response.status != 200:
                raise RuntimeError("isolated system summary failed")
            summary = json.load(response)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise RuntimeError("isolated system summary failed") from exc
    metrics = summary.get("metrics") if isinstance(summary, dict) else None
    host = metrics.get("host") if isinstance(metrics, dict) else None
    disk = host.get("disk") if isinstance(host, dict) else None
    postgresql = host.get("postgresql") if isinstance(host, dict) else None
    if (
        not isinstance(host, dict)
        or not isinstance(disk, dict)
        or disk.get("source_state") != "measured"
        or type(disk.get("total_bytes")) is not int
        or type(disk.get("free_bytes")) is not int
        or not 0 <= disk["free_bytes"] <= disk["total_bytes"]
        or not isinstance(postgresql, dict)
        or postgresql.get("state") != "ok"
        or host.get("host_health_source_state") not in {"reported", "unavailable"}
        or summary.get("metrics_stale") is not False
        or summary.get("worker_health") != "worker_healthy"
    ):
        raise RuntimeError("isolated system telemetry unhealthy")


def _api_json(
    opener: urllib.request.OpenerDirector,
    api_origin: str,
    path: str,
    *,
    payload: dict[str, object] | None = None,
) -> object:
    request = urllib.request.Request(
        f"{api_origin}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"} if payload is not None else {},
        method="POST" if payload is not None else "GET",
    )
    with opener.open(request, timeout=5) as response:
        return json.load(response)


def _assert_role_scope(
    user: object, visible_parks: object, *, role: str, park_id: int
) -> None:
    if not isinstance(user, dict) or not isinstance(visible_parks, list):
        raise RuntimeError("isolated role park scope failed")  # noqa: TRY004
    membership = user.get("parks")
    if (
        user.get("role") != role
        or user.get("access_status") != "approved"
        or user.get("must_change_password") is not False
        or not isinstance(membership, list)
        or len(membership) != 1
        or len(visible_parks) != 1
        or not all(isinstance(park, dict) for park in (*membership, *visible_parks))
        or [park["id"] for park in membership] != [park_id]
        or [park["id"] for park in visible_parks] != [park_id]
    ):
        raise RuntimeError("isolated role park scope failed")


def _smoke_roles(
    owner: urllib.request.OpenerDirector, api_origin: str
) -> list[dict[str, str]]:
    """Exercise real PostgreSQL auth and park scope for every built-in role."""
    first = _api_json(owner, api_origin, "/parks", payload={
        "name": "Изолированный север", "tag": f"smoke-{uuid4().hex[:12]}",
    })
    second = _api_json(owner, api_origin, "/parks", payload={
        "name": "Изолированный юг", "tag": f"smoke-{uuid4().hex[:12]}",
    })
    if not isinstance(first, dict) or not isinstance(second, dict):
        raise RuntimeError("isolated parks setup failed")  # noqa: TRY004
    park_id, foreign_id = first.get("id"), second.get("id")
    if type(park_id) is not int or type(foreign_id) is not int or park_id == foreign_id:
        raise RuntimeError("isolated parks setup failed")
    accounts: list[dict[str, str]] = []
    for role in ("admin", "operator", "mechanic", "driver"):
        username = f"demo-{role}-{uuid4().hex[:8]}"
        password = _random_password()
        created = _api_json(owner, api_origin, "/admin/users", payload={
            "username": username, "password": password,
            "role_slug": role, "park_ids": [park_id],
        })
        if not isinstance(created, dict) or created.get("role") != role:
            raise RuntimeError("isolated role setup failed")
        client = _smoke_login(api_origin, username, password)
        user = _api_json(client, api_origin, "/auth/me")
        parks = _api_json(client, api_origin, "/parks")
        if role == "admin":
            if not isinstance(parks, list) or {
                item.get("id") for item in parks if isinstance(item, dict)
            } != {park_id, foreign_id}:
                raise RuntimeError("isolated admin park scope failed")
            _smoke_system(client, api_origin)
        else:
            _assert_role_scope(user, parks, role=role, park_id=park_id)
            try:
                _api_json(client, api_origin, "/admin/system/summary")
            except urllib.error.HTTPError as exc:
                if exc.code != 403:
                    raise RuntimeError("isolated system access failed") from exc
            else:
                raise RuntimeError("isolated system access failed")
        accounts.append({"role": role, "username": username, "password": password})
    return accounts


def _stop_owned(processes: list[subprocess.Popen], container: str | None) -> None:
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()
    for process in reversed(processes):
        if process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    if container:
        subprocess.run(
            ["docker", "rm", "--force", "--volumes", container],
            capture_output=True,
            check=False,
            timeout=15,
        )


def run_demo(*, smoke: bool, allow_integrations: bool = False) -> None:
    python = API / ".venv" / "bin" / "python"
    vite = WEB / "node_modules" / ".bin" / "vite"
    if not python.is_file() or not vite.is_file():
        raise RuntimeError("local API and web dependencies are required")
    _run(["docker", "image", "inspect", POSTGRES_IMAGE], timeout=10)

    processes: list[subprocess.Popen] = []
    container: str | None = None
    with tempfile.TemporaryDirectory(prefix="robopark-isolated-demo-") as temp:
        root = Path(temp)
        for name in ("ops", "apply", "data", "logs"):
            (root / name).mkdir()
        (root / "host.env").touch(mode=0o600)
        owner_username = f"demo-owner-{uuid4().hex[:10]}"
        owner_password = _random_password()
        secret_key = secrets.token_urlsafe(48)
        pg_password = secrets.token_hex(32)
        api_port, web_port = _free_port(), _free_port()
        api_origin = f"http://127.0.0.1:{api_port}"
        web_origin = f"http://127.0.0.1:{web_port}"
        candidate = f"robopark-isolated-demo-{uuid4().hex[:12]}"
        try:
            docker_env = dict(os.environ)
            docker_env["POSTGRES_PASSWORD"] = pg_password
            _run(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--rm",
                    "--name",
                    candidate,
                    "--env",
                    "POSTGRES_DB=robopark",
                    "--env",
                    "POSTGRES_USER=robopark",
                    "--env",
                    "POSTGRES_PASSWORD",
                    "--publish",
                    "127.0.0.1::5432",
                    "--tmpfs",
                    "/var/lib/postgresql/data:rw,size=512m",
                    POSTGRES_IMAGE,
                ],
                env=docker_env,
                timeout=30,
            )
            container = candidate
            _wait_for(
                lambda: (
                    subprocess.run(
                        [
                            "docker",
                            "exec",
                            container,
                            "pg_isready",
                            "-U",
                            "robopark",
                            "-d",
                            "robopark",
                        ],
                        capture_output=True,
                        check=False,
                        timeout=3,
                    ).returncode
                    == 0
                ),
                timeout=30,
                label="temporary PostgreSQL",
            )
            pg_port = int(
                _run(["docker", "port", container, "5432/tcp"]).rsplit(":", 1)[-1]
            )
            database_url = f"postgresql+psycopg://robopark:{pg_password}@127.0.0.1:{pg_port}/robopark"
            app_env = isolated_app_env(
                dict(os.environ),
                root=root,
                api_src=API / "src",
                database_url=database_url,
                owner_username=owner_username,
                owner_password=owner_password,
                secret_key=secret_key,
                web_origin=web_origin,
                allow_integrations=allow_integrations,
            )
            migration = (
                "import sys; from alembic import command; from alembic.config import Config; "
                "config = Config(sys.argv[1]); "
                "config.set_main_option('script_location', sys.argv[2]); "
                "command.upgrade(config, 'head')"
            )
            _run(
                [
                    str(python),
                    "-c",
                    migration,
                    str(API / "alembic.ini"),
                    str(API / "alembic"),
                ],
                env=app_env,
                timeout=60,
            )
            with (
                (root / "logs" / "api.log").open("w") as api_log,
                (root / "logs" / "worker.log").open("w") as worker_log,
                (root / "logs" / "web.log").open("w") as web_log,
            ):
                api_process = _start_service(
                    [
                        str(python),
                        "-m",
                        "uvicorn",
                        "robopark_api.main:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(api_port),
                    ],
                    cwd=root,
                    env=app_env,
                    log=api_log,
                )
                processes.append(api_process)
                _wait_for(
                    lambda: (
                        api_process.poll() is None
                        and _http_ready(f"{api_origin}/health/ready")
                    ),
                    timeout=30,
                    label="isolated API",
                )
                worker_process = _start_service(
                    [str(python), "-m", "robopark_api.worker"],
                    cwd=root,
                    env=app_env,
                    log=worker_log,
                )
                processes.append(worker_process)
                _wait_for(
                    lambda: (
                        worker_process.poll() is None
                        and subprocess.run(
                            [
                                str(python),
                                "-m",
                                "robopark_api.worker_healthcheck",
                                "--max-age-seconds",
                                "30",
                            ],
                            cwd=root,
                            env=app_env,
                            capture_output=True,
                            check=False,
                            timeout=3,
                        ).returncode
                        == 0
                    ),
                    timeout=30,
                    label="isolated worker",
                )
                web_env = {
                    key: app_env[key]
                    for key in ("PATH", "LANG", "LC_ALL", "TZ")
                    if key in app_env
                }
                web_env["ROBOPARK_DEV_API_TARGET"] = api_origin
                web_process = _start_service(
                    [
                        str(vite),
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(web_port),
                        "--strictPort",
                    ],
                    cwd=WEB,
                    env=web_env,
                    log=web_log,
                )
                processes.append(web_process)
                _wait_for(
                    lambda: web_process.poll() is None and _http_ready(web_origin),
                    timeout=30,
                    label="isolated web",
                )
                owner_client = _smoke_login(api_origin, owner_username, owner_password)
                _smoke_system(owner_client, api_origin)
                role_accounts = _smoke_roles(owner_client, api_origin)
                if smoke:
                    print(
                        "Isolated PostgreSQL, API, worker, web, and five roles: ready"
                    )
                    return
                login_file = root / "role-logins.json"
                _write_login_file(login_file, [
                    {"role": "royal", "username": owner_username, "password": owner_password},
                    *role_accounts,
                ])
                print(f"Isolated demo: {web_origin}")
                print(_login_file_notice(login_file))
                print(
                    "The demo is loopback-only and all data is removed when it stops. Press Ctrl+C."
                )
                if allow_integrations:
                    print("External HTTPS is limited to Tracker and Robot API hosts.")
                while all(process.poll() is None for process in processes):
                    time.sleep(1)
                raise RuntimeError("one of the isolated demo services exited")
        finally:
            _stop_owned(processes, container)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="start, verify, then remove the isolated demo",
    )
    parser.add_argument(
        "--allow-integrations",
        action="store_true",
        help="allow HTTPS to the exact Tracker and Robot API hosts in the disposable demo",
    )
    args = parser.parse_args()
    signal.signal(
        signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt)
    )
    try:
        run_demo(smoke=args.smoke, allow_integrations=args.allow_integrations)
    except KeyboardInterrupt:
        return 0
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"Isolated demo failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
