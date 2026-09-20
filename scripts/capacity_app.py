"""Isolated production-app adapter used only by capacity_benchmark.py.

The harness starts this module in a new temporary working directory with a
minimal explicit environment. Settings are installed BEFORE importing db/main.
Only the external client boundary is replaced; auth, ACLs, caches, PostgreSQL,
serialization and production lifespan remain real.
"""

# Production imports must follow the loopback guard and isolated settings:
# importing db/main earlier could initialize the developer's live database.
# ruff: noqa: E402

from __future__ import annotations

import faulthandler
import json
import os
import resource
import signal
import socket
import sys
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

faulthandler.register(signal.SIGUSR1, all_threads=True)

CONFIG = json.loads((Path.cwd() / "capacity-config.json").read_text())
ROOT = Path(CONFIG["root"]).resolve()
if Path.cwd().resolve() != ROOT or not (ROOT / "capacity-isolated").is_file():
    raise RuntimeError("Capacity app requires a marked isolated temporary directory")

# Fail closed if any unmocked integration tries to connect outside loopback.
_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex


def _guard_address(address):
    if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1"}:
        raise RuntimeError("Capacity harness blocked a non-loopback connection")


def _local_connect(self, address):
    _guard_address(address)
    return _connect(self, address)


def _local_connect_ex(self, address):
    _guard_address(address)
    return _connect_ex(self, address)


socket.socket.connect = _local_connect
socket.socket.connect_ex = _local_connect_ex

from robopark_api import config

SETTINGS = config.Settings(
    _env_file=None,
    database_url=CONFIG["database_url"],
    secret_key="capacity-synthetic-key-never-use-in-production",
    seed_username=None,
    seed_password=None,
    dev_seed=False,
    live_merge_dir=str(ROOT / "live-merge"),
    report_attachments_dir=str(ROOT / "attachments"),
    ops_dir=str(ROOT / "ops"),
    ops_apply_root=str(ROOT / "apply"),
    ops_host_env_path=str(ROOT / "nonexistent-host.env"),
)
config.get_settings = lambda: SETTINGS

from robopark_api import (
    collaboration_models,  # noqa: F401 — include task metadata in isolated schema
)
from robopark_api.db import SessionLocal, engine
from robopark_api.models import (
    AuthSession,
    Base,
    Park,
    Report,
    User,
    UserPark,
)
from robopark_api.security import hash_session_token
from robopark_api.services import (
    emergency_client,
    platform_settings,
    tracker_client,
)
from robopark_api.services.cache_metrics import snapshot_all
from robopark_api.services.emergency_config import (
    DEFAULT_JSON_PATH,
    seed_emergency_config,
)
from robopark_api.services.rbac import get_role_by_slug
from robopark_api.services.rbac_seed import ensure_rbac_catalog


def seed():
    Base.metadata.create_all(engine)
    now = datetime.now(UTC)
    with SessionLocal() as db:
        ensure_rbac_catalog(db)
        seed_emergency_config(db, DEFAULT_JSON_PATH)
        park = Park(
            name="Capacity Alpha", tag="CapacityAlpha", tracker_queue="ROBOPARK"
        )
        foreign = Park(
            name="Capacity Foreign", tag="CapacityForeign", tracker_queue="ROBOPARK"
        )
        db.add_all([park, foreign])
        db.flush()
        role = get_role_by_slug(db, "operator")
        for index in range(CONFIG["users"]):
            user = User(
                username=f"capacity-{index}",
                password_hash="not-a-login-password",
                role_id=role.id,
                access_status="approved",
                is_active=True,
            )
            db.add(user)
            db.flush()
            db.add(UserPark(user_id=user.id, park_id=park.id))
            # A cold reconnect must include some real sliding-session writes.
            idle = SETTINGS.session_idle_seconds - (7200 if index % 20 == 0 else 0)
            db.add(
                AuthSession(
                    user_id=user.id,
                    token_hash=hash_session_token(f"capacity-session-{index}"),
                    created_at=now - timedelta(hours=3),
                    expires_at=now + timedelta(seconds=idle),
                )
            )
            db.add(
                Report(
                    author_user_id=user.id,
                    park_id=park.id,
                    kind="mechanic_problem",
                    status="open",
                    target_role="operator",
                    title=f"Synthetic report {index}",
                    body="Synthetic local capacity fixture",
                )
            )
        db.commit()
        platform_settings.set_setting(
            db, platform_settings.TRACKER_TOKEN_KEY, "synthetic-token"
        )
        platform_settings.set_setting(
            db, platform_settings.EMERGENCY_COOKIE_KEY, "Session_id=synthetic"
        )
    print(
        json.dumps(
            {
                "seeded_users": CONFIG["users"],
                "database": "disposable PostgreSQL 17",
            }
        )
    )


def _stub(operation, **kwargs):
    # Deliberately omit synthetic credentials; only operation arguments reach
    # the stub, which counts actual requests received from both worker PIDs.
    data = json.dumps(
        {"operation": operation, "args": kwargs, "worker": os.getpid()}
    ).encode()
    request = urllib.request.Request(
        CONFIG["stub_url"], data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def _install_stubs():
    class StubIssues:
        def __getitem__(self, key):
            return SimpleNamespace(
                changelog=SimpleNamespace(
                    get_all=lambda: _stub("issue_history", key=key)
                )
            )

    tracker_client.search_issues = lambda *, token, **kw: _stub("search_issues", **kw)
    tracker_client.get_issue = lambda *, token, **kw: _stub("get_issue", **kw)
    tracker_client.list_comments = lambda *, token, **kw: _stub("list_comments", **kw)
    tracker_client.search_robot_tickets = lambda *, token, **kw: _stub(
        "search_robot_tickets", **kw
    )
    tracker_client.fetch_park_blockers = lambda *, token, **kw: _stub(
        "fetch_park_blockers", **kw
    )
    tracker_client.count_issues = lambda *, token, **kw: _stub("count_issues", **kw)
    tracker_client._client = lambda _token: SimpleNamespace(issues=StubIssues())
    emergency_client.fetch_robot_payload = lambda *, cookie, **kw: _stub(
        "emergency", **kw
    )


if __name__ == "__main__":
    if sys.argv[1:] != ["--seed"]:
        raise SystemExit("Only --seed is supported")
    seed()
else:
    _install_stubs()
    from robopark_api.main import app

    _started = time.monotonic()

    @app.middleware("http")
    async def identify_capacity_worker(request, call_next):
        response = await call_next(request)
        response.headers["X-Capacity-Worker"] = str(os.getpid())
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        response.headers["X-Capacity-Peak-Rss-Bytes"] = str(
            rss if sys.platform == "darwin" else rss * 1024
        )
        usage = resource.getrusage(resource.RUSAGE_SELF)
        elapsed = max(0.001, time.monotonic() - _started)
        response.headers["X-Capacity-Cpu-Percent"] = str(
            round(100 * (usage.ru_utime + usage.ru_stime) / elapsed, 3)
        )
        checked_out = getattr(engine.pool, "checkedout", lambda: 0)()
        response.headers["X-Capacity-Db-Pool-Checked-Out"] = str(checked_out)
        metrics = snapshot_all().values()
        response.headers["X-Capacity-Cache-Hits"] = str(
            sum(int(item["hits"]) for item in metrics)
        )
        metrics = snapshot_all().values()
        response.headers["X-Capacity-Cache-Misses"] = str(
            sum(int(item["misses"]) for item in metrics)
        )
        return response
