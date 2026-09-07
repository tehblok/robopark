import asyncio
import json
from pathlib import Path

from conftest import login_as


def test_observations_merge_workers_expire_and_never_store_request_details(tmp_path):
    from robopark_api.services.operational_health import RequestObservations, read_observations

    first = RequestObservations(tmp_path, worker="a")
    second = RequestObservations(tmp_path, worker="b")
    first.record("tracker", 100, 200, now=1000)
    first.record("tracker", 300, 503, now=1001)
    second.record("tracker", 200, 429, now=1002)
    first.flush(now=1002)
    second.flush(now=1002)
    summary = read_observations(tmp_path, now=1003)
    assert summary["tracker"]["requests"] == 3
    assert summary["tracker"]["errors"] == 2
    assert summary["tracker"]["average_ms"] == 200
    assert read_observations(tmp_path, now=1400)["tracker"]["requests"] == 0
    assert all("token" not in p.read_text() for p in tmp_path.glob("*.json"))


def test_backup_status_requires_confirmed_copy_and_survives_later_failed_job(tmp_path):
    from robopark_api.services.operational_health import backup_status

    assert backup_status(tmp_path, now=1000)["verified_at"] is None
    (tmp_path / "scheduled-copy.json").write_text(json.dumps({"verified_at": 900}))
    (tmp_path / "job.json").write_text(
        json.dumps({"kind": "snapshot", "state": "failed", "updated_at": "2026-09-06T10:00:00Z"})
    )
    status = backup_status(tmp_path, now=1000)
    assert status["verified_at"] == 900
    assert status["last_attempt_failed"] is True
    assert status["overdue"] is False
    assert backup_status(tmp_path, now=200000)["overdue"] is True


def test_health_api_restricts_access_and_returns_no_sensitive_paths(
    client, seed_mechanic, seed_royal, monkeypatch
):
    from robopark_api.routers import admin_health

    monkeypatch.setattr(
        admin_health,
        "cached_host_snapshot",
        lambda *_: {
            "sampled_at": 1000,
            "database": "ok",
            "disk": {"free_bytes": 100},
            "memory": {},
            "backup": {},
            "requests": {},
        },
    )
    assert client.get("/admin/health").status_code == 401
    login_as(client, "mech1", "secret")
    assert client.get("/admin/health").status_code == 403
    login_as(client, "royal", "secret")
    response = client.get("/admin/health")
    assert response.status_code == 200
    assert response.json()["database"] == "ok"
    assert response.headers["cache-control"] == "no-store"


def test_unavailable_platform_memory_is_unknown(monkeypatch):
    from robopark_api.services import operational_health as health

    def unavailable(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(Path, "read_text", unavailable)
    assert all(value is None for value in health.read_memory().values())


def test_last_failed_request_is_flushed_even_when_no_more_requests_arrive(tmp_path, monkeypatch):
    from robopark_api.middleware import observations
    from robopark_api.services.operational_health import read_observations

    monkeypatch.setattr(observations, "FLUSH_DELAY_SECONDS", 0.01, raising=False)
    status = 200

    async def scenario():
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": status})
            await send({"type": "http.response.body", "body": b""})

        async def send(message):
            pass

        middleware = observations.ObservationMiddleware(app, tmp_path)
        await middleware({"type": "http", "path": "/tracker/issues"}, None, send)
        nonlocal status
        status = 503
        await middleware({"type": "http", "path": "/tracker/issues"}, None, send)
        await asyncio.sleep(0.1)
        assert read_observations(tmp_path)["tracker"]["errors"] == 1

    asyncio.run(scenario())


def test_request_during_background_flush_is_not_lost(tmp_path, monkeypatch):
    from robopark_api.middleware import observations
    from robopark_api.services.operational_health import read_observations

    monkeypatch.setattr(observations, "FLUSH_DELAY_SECONDS", 0.01)

    async def scenario():
        captured = asyncio.Event()
        release = asyncio.Event()
        should_pause = False

        async def controlled_pool(fn):
            nonlocal should_pause
            fn()
            if should_pause:
                should_pause = False
                captured.set()
                await release.wait()

        monkeypatch.setattr(observations, "run_in_threadpool", controlled_pool)

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": scope["status"]})

        async def send(message):
            pass

        middleware = observations.ObservationMiddleware(app, tmp_path)
        scope = {"type": "http", "path": "/tracker/issues", "status": 200}
        await middleware(scope, None, send)
        await middleware(scope, None, send)
        should_pause = True
        await asyncio.wait_for(captured.wait(), 1)
        await middleware({**scope, "status": 503}, None, send)
        release.set()
        await asyncio.sleep(0.1)
        assert read_observations(tmp_path)["tracker"]["errors"] == 1

    asyncio.run(scenario())
