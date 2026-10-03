"""Short opt-in PostgreSQL checks using a disposable localhost-only database."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session

from robopark_api.models import Base, Park, TrackerIssueHistoryState, TrackerIssueStatusEvent
from robopark_api.services import platform_settings, tracker_cache, tracker_client, tracker_history


@pytest.fixture(scope="module")
def sla_postgres_engine():
    if os.environ.get("ROBOPARK_SLA_POSTGRES_TESTS") != "1":
        pytest.skip("opt-in short isolated PostgreSQL regression")
    name = f"robopark-sla-test-{uuid4().hex[:12]}"
    subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--pull",
            "never",
            "--name",
            name,
            "--env",
            "POSTGRES_HOST_AUTH_METHOD=trust",
            "--env",
            "POSTGRES_DB=robopark",
            "--tmpfs",
            "/var/lib/postgresql/data:rw,size=512m",
            "--publish",
            "127.0.0.1::5432",
            "postgres:17-alpine",
        ],
        check=True,
        capture_output=True,
    )
    engine = None
    try:
        port = (
            subprocess.run(
                ["docker", "port", name, "5432/tcp"], check=True, capture_output=True, text=True
            )
            .stdout.strip()
            .rsplit(":", 1)[-1]
        )
        engine = create_engine(
            f"postgresql+psycopg://postgres@127.0.0.1:{port}/robopark",
            connect_args={"options": "-c statement_timeout=3000 -c lock_timeout=2000"},
        )
        for attempt in range(40):
            try:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                break
            except Exception:
                if attempt == 39:
                    raise
                time.sleep(0.1)
        Base.metadata.create_all(
            engine,
            tables=[
                Park.__table__,
                TrackerIssueHistoryState.__table__,
                TrackerIssueStatusEvent.__table__,
            ],
        )
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        subprocess.run(["docker", "rm", "--force", name], check=False, capture_output=True)


def _park(engine):
    with Session(engine, expire_on_commit=False) as db:
        park = Park(
            name=uuid4().hex,
            tag=uuid4().hex,
            tracker_queue="RP",
            timezone="Europe/Moscow",
            is_active=True,
        )
        db.add(park)
        db.commit()
        return park


def _change(at):
    return {
        "updatedAt": at.isoformat(),
        "fields": [{"field": {"id": "status"}, "to": {"key": "queued", "display": "В очереди"}}],
    }


def test_opposite_ui_sort_orders_share_one_postgres_lock_order(sla_postgres_engine):
    engine = sla_postgres_engine
    park = _park(engine)
    prefix = uuid4().hex
    issues = [
        {"key": f"{prefix}-{suffix}", "queue": "RP", "tags": [park.tag]} for suffix in ["A", "B"]
    ]
    lock_orders = []

    def capture(connection, cursor, statement, parameters, context, many):
        if (
            statement.startswith("INSERT INTO tracker_issue_history_state")
            and "ON CONFLICT" in statement
        ):
            keys = [value for key, value in parameters.items() if key.startswith("issue_key_m")]
            if keys:
                lock_orders.append(keys)

    event.listen(engine, "before_cursor_execute", capture)
    barrier = threading.Barrier(2)

    def register(items):
        with Session(engine) as db:
            for _ in range(4):
                barrier.wait(timeout=2)
                tracker_history.register_pending_history(db, items, [park])

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(register, items) for items in [issues, list(reversed(issues))]]
            for future in futures:
                future.result(timeout=10)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(lock_orders) == 8
    assert all(order == sorted(item["key"] for item in issues) for order in lock_orders)


def test_parallel_postgres_ingestion_preserves_earliest_anchor_and_unique_events(
    sla_postgres_engine,
):
    engine = sla_postgres_engine
    park = _park(engine)
    key = uuid4().hex
    queued = datetime(2026, 9, 30, 6, tzinfo=UTC)
    first = [_change(queued)]
    later = first + [_change(queued + timedelta(days=1))]
    barrier = threading.Barrier(2)

    def ingest(history):
        with Session(engine) as db:
            barrier.wait(timeout=2)
            tracker_history.ingest_status_history(db, issue_key=key, park=park, history=history)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(ingest, history) for history in [first, later]]
        for future in futures:
            future.result(timeout=10)
    with Session(engine) as db:
        state = db.get(TrackerIssueHistoryState, key)
        assert state.first_queued_at == queued
        assert state.anchor_timezone == "Europe/Moscow"
        assert (
            len(
                db.scalars(
                    select(TrackerIssueStatusEvent).where(TrackerIssueStatusEvent.issue_key == key)
                ).all()
            )
            == 2
        )


def test_postgres_pending_drain_prioritizes_never_checked_tasks(sla_postgres_engine, monkeypatch):
    engine = sla_postgres_engine
    park = _park(engine)
    now = datetime(2026, 9, 30, 6, tzinfo=UTC)
    fresh_key = uuid4().hex
    with Session(engine) as db:
        # Keep the previous test's unverified discoveries out of this pass.
        db.execute(
            text(
                "UPDATE tracker_issue_history_state SET history_state='no_queue', history_checked_at=:now WHERE first_queued_at IS NULL"
            ),
            {"now": now},
        )
        db.add_all(
            [
                TrackerIssueHistoryState(
                    issue_key=uuid4().hex,
                    observed_park_id=park.id,
                    history_state="retry",
                    history_checked_at=now - timedelta(minutes=3),
                )
                for _ in range(17)
            ]
        )
        db.add(
            TrackerIssueHistoryState(
                issue_key=fresh_key, observed_park_id=park.id, history_state="unknown"
            )
        )
        db.commit()
        monkeypatch.setattr(platform_settings, "get_tracker_token", lambda _: "fixture-token")
        monkeypatch.setattr(
            tracker_cache,
            "get_issue",
            lambda **kw: {"key": kw["key"], "queue": "RP", "tags": [park.tag]},
        )
        monkeypatch.setattr(tracker_client, "get_issue_status_history", lambda **kw: [_change(now)])
        assert tracker_history.drain_pending_history(db, now=now) == 2
        assert db.get(TrackerIssueHistoryState, fresh_key).first_queued_at == now
