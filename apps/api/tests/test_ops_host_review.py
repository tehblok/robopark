"""Review regressions for the installed host writer barrier and dispatch recovery."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from conftest import login_as
from robopark_api.config import reset_settings_cache
from robopark_api.models import AuthSession, ParkBlockerHistory
from robopark_api.services import (
    blocker_history,
    emergency_client,
    emergency_keepalive,
    session_cleanup,
)
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services.ops import host_bridge
from robopark_api.services.ops.jobs import load_job


@pytest.fixture
def installed(test_settings, tmp_path, monkeypatch):
    host = tmp_path / "host-ops"
    for name in ("inbox", "artifacts", "public"):
        (host / name).mkdir(parents=True)
    test_settings.ops_host_root = str(host)
    monkeypatch.setenv("OPS_HOST_ROOT", str(host))
    monkeypatch.setenv("OPS_DIR", test_settings.ops_dir)
    reset_settings_cache()
    return host


def enable(host, content='{"enabled":true,"reason":"update"}'):
    marker = host / "public/maintenance.json"
    marker.write_text(content)
    return marker


def test_root_marker_blocks_initiating_royal_post_but_allows_readonly_polling(
    client, seed_royal, installed, db_session
):
    login_as(client, "royal", "secret")
    assert client.post("/admin/ops/diagnostics").status_code == 200
    auth = db_session.scalar(select(AuthSession))
    deadline = datetime.now(UTC) + timedelta(seconds=30)
    auth.expires_at = deadline
    db_session.commit()
    writes = []

    @client.app.post("/review-write")
    def mutate():
        writes.append(True)
        return {"ok": True}

    enable(installed)
    assert client.post("/review-write").status_code == 503
    assert writes == []
    assert client.post("/admin/ops/repair").status_code == 503
    assert client.post("/admin/ops/abort").status_code == 503
    assert client.get("/admin/ops/job").status_code == 200
    assert client.get("/ops/maintenance").json()["active"] is True
    db_session.refresh(auth)
    assert auth.expires_at.replace(tzinfo=UTC) == deadline


@pytest.mark.parametrize(
    "content",
    [
        "{broken",
        "[]",
        "{}",
        '{"enabled":0}',
        '{"enabled":false,"unexpected":1}',
        '{"enabled":true,"enabled":false}',
        "x" * 5000,
    ],
)
def test_invalid_marker_blocks_writes_and_terminal_reconciliation(
    client, seed_royal, installed, test_settings, content
):
    login_as(client, "royal", "secret")
    job = client.post("/admin/ops/diagnostics").json()
    (installed / "public/command-result.json").write_text(
        json.dumps(
            {
                "job_id": job["id"],
                "kind": "diagnostics",
                "actor_user_id": seed_royal.id,
                "state": "succeeded",
            }
        )
    )
    enable(installed, content)
    assert client.get("/ops/maintenance").json()["active"] is True
    assert client.post("/auth/logout").status_code == 503
    assert client.get("/admin/ops/job").json()["state"] == "running"
    assert load_job(Path(test_settings.ops_dir)).extra["host_dispatch"] == "dispatched"


@pytest.mark.parametrize("mode", ["unreadable", "symlink"])
def test_unreadable_marker_is_not_equivalent_to_missing(
    client, seed_royal, installed, monkeypatch, mode
):
    import os

    login_as(client, "royal", "secret")
    marker = enable(installed)
    if mode == "symlink":
        marker.unlink()
        marker.symlink_to(installed / "public/missing.json")
    else:
        original = os.open

        def open_file(path, flags, *args, **kwargs):
            if Path(path) == marker:
                raise PermissionError("blocked")
            return original(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", open_file)
    assert client.get("/ops/maintenance").json()["active"] is True
    assert client.post("/auth/logout").status_code == 503


def test_session_cleanup_pauses_then_resumes_at_actual_delete(
    installed, db_engine, db_session, seed_royal, client, monkeypatch
):
    login_as(client, "royal", "secret")
    auth = db_session.scalar(select(AuthSession))
    auth.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    monkeypatch.setattr(session_cleanup, "SessionLocal", sessionmaker(bind=db_engine))
    marker = enable(installed)
    assert session_cleanup.purge_expired_sessions_once() == 0
    assert db_session.scalar(select(AuthSession)) is not None
    marker.unlink()
    assert session_cleanup.purge_expired_sessions_once() == 1


def test_keepalive_cannot_publish_db_writes_after_marker_arrives_during_upstream(
    installed, db_session, monkeypatch
):
    settings_svc.set_setting(db_session, settings_svc.EMERGENCY_COOKIE_KEY, "fixture")
    settings_svc.touch_keepalive_ring(db_session, "VIN-1")

    def fetch(**kwargs):
        enable(installed)
        return {"vin": "VIN-1"}

    monkeypatch.setattr(emergency_client, "fetch_robot_payload", fetch)
    emergency_keepalive.keepalive_once(db_session)
    db_session.expire_all()
    assert (
        settings_svc.get_setting(db_session, settings_svc.EMERGENCY_KEEPALIVE_LAST_OK_KEY) is None
    )
    assert settings_svc.get_emergency_cookie_valid(db_session) is not True


def test_inflight_blocker_scan_cannot_upsert_after_marker_arrives(
    installed, db_session, monkeypatch
):
    from robopark_api.models import Park

    park = Park(name="Test", tracker_queue="Q", tag="test", is_active=True)
    db_session.add(park)
    db_session.commit()

    def count(**kwargs):
        enable(installed)
        return 2

    monkeypatch.setattr(blocker_history, "count_issues", count)
    now = datetime.now(UTC)
    blocker_history.scan_park_bucket(
        db_session, park, now - timedelta(hours=2), now, token="fixture"
    )
    assert db_session.scalars(select(ParkBlockerHistory)).all() == []


def test_database_write_entrypoint_rechecks_marker_after_request_started(installed, db_engine):
    # Represents an in-flight handler that passed middleware before cutover.
    with db_engine.connect() as connection:
        connection.execute(text("CREATE TABLE review_barrier (value INTEGER)"))
        connection.commit()
        enable(installed)
        with pytest.raises(RuntimeError, match="maintenance"):
            connection.execute(text("INSERT INTO review_barrier VALUES (1)"))
        connection.rollback()
        assert connection.execute(text("SELECT COUNT(*) FROM review_barrier")).scalar() == 0


def test_database_commit_after_marker_rolls_back_pending_changes(installed, db_engine):
    with db_engine.connect() as connection:
        connection.execute(text("CREATE TABLE review_commit (value INTEGER)"))
        connection.commit()
        connection.execute(text("INSERT INTO review_commit VALUES (1)"))
        marker = enable(installed)
        with pytest.raises(RuntimeError, match="maintenance"):
            connection.commit()
        connection.rollback()
        marker.unlink()
        # Even reusing this connection after the marker clears must not publish
        # the transaction rejected at the cutover barrier.
        assert connection.execute(text("SELECT COUNT(*) FROM review_commit")).scalar() == 0
        connection.commit()


def test_readonly_startup_connection_keeps_foreign_keys_after_release(installed, tmp_path):
    import sqlite3

    from robopark_api.db import _configure_sqlite

    with sqlite3.connect(tmp_path / "candidate.db") as connection:
        connection.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        connection.execute("CREATE TABLE child (parent_id INTEGER REFERENCES parent(id))")
        connection.commit()
        journal = connection.execute("PRAGMA journal_mode").fetchone()[0]
        marker = enable(installed)
        _configure_sqlite(connection, None)
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == journal
        marker.unlink()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO child VALUES (999)")


def test_live_merge_completion_does_not_persist_after_cutover(installed, tmp_path):
    from robopark_api.services.live_merge import LiveMergeStore

    store = LiveMergeStore(tmp_path / "cache")

    def loader():
        enable(installed)
        return {"new": "data"}

    with pytest.raises(RuntimeError, match="maintenance"):
        store.merge_load("review", "key", 60, loader)
    assert not store.result_path("review", "key").exists()


@pytest.mark.parametrize("delayed_start", [True, False])
def test_delayed_restore_does_not_replace_data_during_cutover(installed, tmp_path, delayed_start):
    from robopark_api.services.ops import runner
    from robopark_api.services.ops.archives import KIND_SNAPSHOT, build_archive
    from robopark_api.services.ops.snapshot import build_snapshot_tree

    data = tmp_path / "restore-data"
    data.mkdir()
    database = data / "app.db"
    database.write_bytes(b"snapshot-data")
    tree = tmp_path / "snapshot-tree"
    build_snapshot_tree(tree, database_url=f"sqlite:///{database}", config_files={}, data_dir=data)
    archive = build_archive(kind=KIND_SNAPSHOT, source_root=tree, app_version="test")
    database.write_bytes(b"current-data")
    ctx = runner.OpsContext(
        ops_dir=tmp_path / "restore-ops",
        database_url=f"sqlite:///{database}",
        data_dir=data,
        use_host_updater=True,
        host_ops_dir=installed,
        before_db_replace=None if delayed_start else lambda: enable(installed),
    )
    job = runner.begin_job(ctx, "restore", exempt_token_hash="session")
    if delayed_start:
        enable(installed)
    result = runner.execute_job(ctx, job, archive=archive, confirm=runner.RESTORE_PHRASE)
    assert result.state == "failed"
    # Installed restore now fails before the process-local replacement callback.
    assert result.error == ("maintenance" if delayed_start else "host_restore_required")
    assert database.read_bytes() == b"current-data"


def test_attachment_publish_checks_marker_before_file_write(installed, tmp_path):
    from robopark_api.services.report_attachments import _atomic_write

    destination = tmp_path / "new-attachments" / "attachment"
    enable(installed)
    with pytest.raises(RuntimeError, match="maintenance"):
        _atomic_write(destination, b"new data")
    assert not destination.parent.exists()


def test_candidate_startup_is_readonly_until_host_releases_marker(
    installed,
    test_settings,
    db_engine,
    db_session,
    seed_royal,
    monkeypatch,
):
    import threading

    from fastapi.testclient import TestClient

    from robopark_api import main
    from robopark_api.db import get_db

    auth = AuthSession(
        user_id=seed_royal.id,
        token_hash="expired",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    db_session.add(auth)
    db_session.commit()
    seeded = threading.Event()
    purged = threading.Event()
    factory = sessionmaker(bind=db_engine)
    monkeypatch.setattr(main, "SessionLocal", factory)
    monkeypatch.setattr(main, "get_settings", lambda: test_settings)
    monkeypatch.setattr(session_cleanup, "SessionLocal", factory)
    original_seed = main.ensure_seed_user

    def seed(db, settings):
        original_seed(db, settings)
        seeded.set()

    monkeypatch.setattr(main, "ensure_seed_user", seed)
    original_purge = session_cleanup.purge_expired_sessions_once

    def purge():
        count = original_purge()
        if count:
            purged.set()
        return count

    monkeypatch.setattr(session_cleanup, "purge_expired_sessions_once", purge)

    async def idle(stop_event):
        await stop_event.wait()

    monkeypatch.setattr(main, "run_keepalive_loop", idle)
    monkeypatch.setattr(main, "run_blocker_history_loop", idle)
    test_settings.session_cleanup_interval_seconds = 0.05
    marker = enable(installed)
    app = main.create_app()

    def db_override():
        yield db_session

    app.dependency_overrides[get_db] = db_override
    with TestClient(app) as candidate:
        assert candidate.get("/health/ready").status_code == 200
        assert candidate.post("/auth/logout").status_code == 503
        assert not seeded.is_set()
        assert not purged.is_set()
        assert db_session.scalar(select(AuthSession)) is not None
        marker.unlink()
        assert seeded.wait(timeout=3), "startup seeds must resume after host releases maintenance"
        assert purged.wait(timeout=3), (
            "background cleanup must resume after host releases maintenance"
        )


@pytest.mark.parametrize("kind", ["diagnostics", "repair"])
def test_generic_publish_failure_retries_the_exact_job(
    client, seed_royal, installed, test_settings, monkeypatch, kind
):
    import os

    login_as(client, "royal", "secret")
    original = os.link
    monkeypatch.setattr(
        os, "link", lambda *args: (_ for _ in ()).throw(OSError("disk unavailable"))
    )
    first = client.post("/admin/ops/" + kind)
    assert first.status_code in {200, 503}
    job = load_job(Path(test_settings.ops_dir))
    saved = job.extra["host_request"].copy()
    assert job.extra["host_dispatch"] == "pending"
    assert not (installed / "inbox/approved.json").exists()
    monkeypatch.setattr(os, "link", original)
    retried = client.post("/admin/ops/" + kind)
    assert retried.status_code == 200, retried.text
    assert retried.json()["id"] == job.id
    assert json.loads((installed / "inbox/approved.json").read_text()) == saved
    (installed / "inbox/approved.json").unlink()
    (installed / "public/command-claim.json").write_text(
        json.dumps({"job_id": job.id, "active": True})
    )
    assert client.get("/admin/ops/job").json()["state"] == "running"
    assert not (installed / "inbox/approved.json").exists()


@pytest.mark.parametrize("kind", ["diagnostics", "repair"])
def test_generic_uncertain_publication_retains_ownership(
    installed,
    test_settings,
    monkeypatch,
    kind,
):
    def uncertain_sync(*args):
        raise OSError("publication durability unknown")

    monkeypatch.setattr(host_bridge, "_sync", uncertain_sync)
    ops = Path(test_settings.ops_dir)
    job = host_bridge.enqueue_operation(ops, installed, kind, 7, "session")
    assert load_job(ops).extra["host_dispatch"] == "dispatched"
    assert json.loads((installed / "inbox/approved.json").read_text()) == job.extra["host_request"]
    (installed / "public/command-claim.json").write_text(
        json.dumps({"job_id": job.id, "active": True})
    )
    (installed / "inbox/approved.json").unlink()
    host_bridge.reconcile_host_job(ops, installed)
    assert not (installed / "inbox/approved.json").exists()
    assert load_job(ops).extra["host_dispatch"] == "dispatched"


@pytest.mark.parametrize("kind", ["diagnostics", "repair"])
def test_generic_crash_after_reservation_is_recovered_by_polling(
    installed, test_settings, monkeypatch, kind
):
    import os

    def crash(*args):
        raise SystemExit("process crashed")

    original = os.link
    monkeypatch.setattr(os, "link", crash)
    ops = Path(test_settings.ops_dir)
    with pytest.raises(SystemExit):
        host_bridge.enqueue_operation(ops, installed, kind, 7, "session")
    saved = load_job(ops).extra["host_request"].copy()
    monkeypatch.setattr(os, "link", original)
    host_bridge.reconcile_host_job(ops, installed)
    assert json.loads((installed / "inbox/approved.json").read_text()) == saved
