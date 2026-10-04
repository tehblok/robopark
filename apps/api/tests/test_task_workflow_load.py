from __future__ import annotations

import math
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from robopark_api import main
from robopark_api.config import Settings, get_settings
from robopark_api.db import get_db
from robopark_api.models import AuthSession, Base, Park, User, UserPark
from robopark_api.security import hash_session_token
from robopark_api.services import (
    platform_settings,
    rbac,
    tracker_cache,
    tracker_client,
)
from robopark_api.services.rbac_seed import ensure_rbac_catalog
from robopark_api.services.tracker_outbox import _process_batch
from robopark_api.task_workflow_models import ReliableAction

SESSION_COUNT = 200
LOCAL_P95_LIMIT_MS = 1_000

pytestmark = pytest.mark.load


def _issue(index: int) -> dict:
    return {
        "key": f"ROBOPARK-{index}",
        "summary": f"Capacity repair [{400 + index}]",
        "status": "В очереди",
        "status_key": "queued",
        "queue": "ROBOPARK",
        "tags": ["CapacityAlpha"],
        "created": "2026-09-15T08:00:00Z",
        "updated": "2026-09-15T08:00:00Z",
        "queued_at": "2026-09-15T08:00:00Z",
        "sla_deadline": "2026-09-15T13:00:00Z",
        "sla_source": "status_history",
        "hours_created": "1",
        "robot": str(400 + index),
        "description": "Synthetic local workflow load",
        "assignee": None,
        "reporter": None,
        "resolution": None,
        "priority": "normal",
        "type": "repair",
        "type_key": "repair",
        "components": [],
        "attachments": [],
    }


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def test_200_authenticated_sessions_keep_local_p95_and_duplicate_delivery_bounded(
    tmp_path, monkeypatch
):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'workflow-load.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    factory = sessionmaker(bind=engine, future=True)
    Base.metadata.create_all(engine)
    settings = Settings(
        _env_file=None,
        database_url=str(engine.url),
        secret_key="workflow-load-test-key",
        report_attachments_dir=str(tmp_path / "attachments"),
        ops_dir=str(tmp_path / "ops"),
    )
    now = datetime.now(UTC)
    operator_tokens: list[str] = []
    mechanic_tokens: list[tuple[str, int]] = []
    with factory() as db:
        ensure_rbac_catalog(db)
        park = Park(name="Capacity Alpha", tag="CapacityAlpha", tracker_queue="ROBOPARK")
        db.add(park)
        db.flush()
        operator_role = rbac.get_role_by_slug(db, "operator")
        mechanic_role = rbac.get_role_by_slug(db, "mechanic")
        for index in range(100):
            token = f"operator-session-{index}"
            user = User(
                username=f"operator-load-{index}",
                password_hash="unused",
                role_id=operator_role.id,
                access_status="approved",
                is_active=True,
            )
            db.add(user)
            db.flush()
            db.add(UserPark(user_id=user.id, park_id=park.id))
            db.add(
                AuthSession(
                    user_id=user.id,
                    token_hash=hash_session_token(token),
                    created_at=now,
                    expires_at=now + timedelta(days=1),
                )
            )
            operator_tokens.append(token)
        for index in range(50):
            user = User(
                username=f"mechanic-load-{index}",
                password_hash="unused",
                role_id=mechanic_role.id,
                access_status="approved",
                is_active=True,
            )
            db.add(user)
            db.flush()
            db.add(UserPark(user_id=user.id, park_id=park.id))
            for duplicate in range(2):
                token = f"mechanic-session-{index}-{duplicate}"
                db.add(
                    AuthSession(
                        user_id=user.id,
                        token_hash=hash_session_token(token),
                        created_at=now,
                        expires_at=now + timedelta(days=1),
                    )
                )
                mechanic_tokens.append((token, index + 1))
        platform_settings.set_setting(db, platform_settings.TRACKER_TOKEN_KEY, "load-token")
        db.commit()

    upstream = Counter()
    monkeypatch.setattr(
        tracker_client, "search_issues", lambda **_kwargs: [_issue(i) for i in range(1, 201)]
    )
    monkeypatch.setattr(
        tracker_client, "get_issue", lambda *, key, **_kwargs: _issue(int(key.rsplit("-", 1)[1]))
    )
    monkeypatch.setattr(tracker_client, "get_issue_status_history", lambda **_kwargs: [])
    monkeypatch.setattr(tracker_client, "_load_work_status_history", lambda **_kwargs: [])
    monkeypatch.setattr(tracker_client, "list_comments", lambda **_kwargs: [])
    monkeypatch.setattr(
        tracker_client,
        "list_queue_components",
        lambda **_kwargs: [{"id": "capacity-repair", "label": "Capacity repair"}],
    )
    monkeypatch.setattr(
        tracker_client,
        "list_transitions",
        lambda **_kwargs: [{"id": "start", "display": "В работу"}],
    )

    def transition(**_kwargs):
        upstream["transition"] += 1

    def set_components(**kwargs):
        assert kwargs["components"] == ["capacity-repair"]
        upstream["component"] += 1

    def set_tags(**kwargs):
        assert kwargs["tags"] == ["CapacityAlpha", "diag_complete"]
        upstream["tag"] += 1

    monkeypatch.setattr(tracker_client, "set_issue_tags", set_tags)
    monkeypatch.setattr(tracker_client, "set_issue_components", set_components)
    monkeypatch.setattr(tracker_client, "transition_issue", transition)
    monkeypatch.setattr(
        tracker_client,
        "_client",
        lambda *_args, **_kwargs: pytest.fail("load test attempted an unmocked Tracker client"),
    )
    monkeypatch.setattr(main, "SessionLocal", factory)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    tracker_cache.clear_all_for_tests()
    app = main.create_app()

    def database():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    primary: list[tuple[str, str, dict[str, str]]] = []
    duplicates: list[tuple[str, str, dict[str, str]]] = []
    read_routes = [
        "/tracker/issues?limit=20",
        "/tracker/issues/ROBOPARK-1",
        "/tracker/issues/ROBOPARK-1/timeline",
    ]
    for index, token in enumerate(operator_tokens):
        primary.append(
            ("GET", read_routes[index % len(read_routes)], {"Cookie": f"robopark_session={token}"})
        )
    for pair_index, (token, issue_index) in enumerate(mechanic_tokens):
        target = primary if pair_index % 2 == 0 else duplicates
        target.append(
            (
                "POST",
                f"/tracker/issues/ROBOPARK-{issue_index}/claim",
                {
                    "Cookie": f"robopark_session={token}",
                    "Idempotency-Key": f"claim-load-{issue_index:04d}",
                },
            )
        )
    assert len(primary) + len(duplicates) == SESSION_COUNT

    def invoke(item: tuple[str, str, dict[str, str]]) -> tuple[int, float]:
        method, path, headers = item
        started = time.perf_counter()
        response = client.request(method, path, headers=headers)
        return response.status_code, (time.perf_counter() - started) * 1_000

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(invoke, primary))
    with ThreadPoolExecutor(max_workers=16) as pool:
        results.extend(pool.map(invoke, duplicates))
    statuses = [status for status, _latency in results]
    latencies = [latency for _status, latency in results]
    p95_ms = _percentile(latencies, 0.95)
    assert statuses == [200] * SESSION_COUNT
    assert p95_ms < LOCAL_P95_LIMIT_MS

    processed = 0
    for _ in range(10):
        count = _process_batch(factory)
        processed += count
        if count == 0:
            break
    with factory() as db:
        actions = db.query(ReliableAction).all()
        assert len(actions) == 150
        assert Counter(action.action for action in actions) == {
            "ensure_tag": 50,
            "ensure_components": 50,
            "start": 50,
        }
        assert all(action.state == "succeeded" for action in actions)
    assert processed == 150
    assert upstream["tag"] == 50
    assert upstream["component"] == 50
    assert upstream["transition"] == 50
    print(
        {
            "sessions": SESSION_COUNT,
            "requests": len(results),
            "local_p95_ms": round(p95_ms, 2),
            "local_p95_limit_ms": LOCAL_P95_LIMIT_MS,
            "unique_action_ids": len(actions),
            "upstream_transition_calls": upstream["transition"],
            "pending_backlog": 0,
        }
    )
    client.close()
    engine.dispose()
