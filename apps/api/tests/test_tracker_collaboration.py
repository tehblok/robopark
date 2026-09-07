"""Collaboration must not repeat upstream writes or cross issue scope."""

import json
from urllib.parse import quote

import pytest

from conftest import login_as
from robopark_api.services import platform_settings, tracker_client


@pytest.fixture
def tracker_setup(client, db_session, seed_mechanic, monkeypatch):
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    issue = {
        "key": "ROBOPARK-1",
        "status": "Open",
        "status_key": "open",
        "queue": "ROBOPARK",
        "tags": ["Alpha"],
    }
    monkeypatch.setattr(tracker_client, "get_issue", lambda **kw: dict(issue))
    login_as(client, "mech1", "secret")
    return issue


def headers(key="submission-one"):
    return {
        "Idempotency-Key": key,
        "X-Tracker-State": quote(
            json.dumps({"status": "Open", "status_key": "open", "assignee": ""})
        ),
    }


def test_success_replayed_without_duplicate_comment(client, tracker_setup, monkeypatch):
    written = []
    monkeypatch.setattr(tracker_client, "add_comment", lambda **kw: written.append(kw))
    first = client.post(
        "/tracker/issues/ROBOPARK-1/comment", json={"text": "done"}, headers=headers()
    )
    second = client.post(
        "/tracker/issues/ROBOPARK-1/comment", json={"text": "done"}, headers=headers()
    )
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(written) == 1


def test_unknown_outcome_never_replayed(client, tracker_setup, monkeypatch):
    written = []

    def lost_response(**kw):
        written.append(kw)
        raise tracker_client.TrackerError("timeout after acceptance")

    monkeypatch.setattr(tracker_client, "add_comment", lost_response)
    first = client.post(
        "/tracker/issues/ROBOPARK-1/comment", json={"text": "done"}, headers=headers()
    )
    second = client.post(
        "/tracker/issues/ROBOPARK-1/comment", json={"text": "done"}, headers=headers()
    )
    assert first.status_code == 409
    assert second.json()["detail"] == "tracker_submission_uncertain"
    assert len(written) == 1


def test_conflict_checks_uncached_state_before_write(client, tracker_setup, monkeypatch):
    client.get("/tracker/issues/ROBOPARK-1")
    tracker_setup["status_key"] = "closed"
    written = []
    monkeypatch.setattr(tracker_client, "add_comment", lambda **kw: written.append(kw))
    response = client.post(
        "/tracker/issues/ROBOPARK-1/comment", json={"text": "done"}, headers=headers()
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "tracker_state_conflict"
    assert not written


def test_presence_and_handoff_are_scoped_and_revision_protected(client, tracker_setup):
    base = "/tracker/issues/ROBOPARK-1"
    presence = client.post(base + "/presence")
    assert presence.status_code == 200
    assert presence.json()["ttl_seconds"] == 90
    saved = client.put(
        base + "/handoff",
        json={"revision": 0, "done": "motor", "remaining": "test", "obstacles": ""},
    )
    assert saved.status_code == 200
    assert saved.json()["author"] == "mech1"
    stale = client.put(
        base + "/handoff", json={"revision": 0, "done": "stale", "remaining": "", "obstacles": ""}
    )
    assert stale.status_code == 409
    assert client.get(base + "/handoff").json()["done"] == "motor"
    tracker_setup["tags"] = ["Other park"]
    assert client.post(base + "/presence").status_code == 403
    assert client.get(base + "/handoff").status_code == 403
    assert (
        client.put(
            base + "/handoff",
            json={"revision": 1, "done": "out of scope", "remaining": "", "obstacles": ""},
        ).status_code
        == 403
    )


def test_payload_binding_and_revoked_user_never_receive_saved_result(
    client, tracker_setup, monkeypatch
):
    written = []
    monkeypatch.setattr(tracker_client, "add_comment", lambda **kw: written.append(kw))
    assert (
        client.post(
            "/tracker/issues/ROBOPARK-1/comment", json={"text": "one"}, headers=headers()
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/tracker/issues/ROBOPARK-1/comment", json={"text": "two"}, headers=headers()
        ).json()["detail"]
        == "tracker_submission_payload_conflict"
    )
    tracker_setup["tags"] = ["Other"]
    # A previously cached issue must not authorize access to an old result.
    assert (
        client.post(
            "/tracker/issues/ROBOPARK-1/comment", json={"text": "one"}, headers=headers()
        ).status_code
        == 403
    )
    assert len(written) == 1


def test_concurrent_reservation_has_only_one_writer(
    db_engine, db_session, seed_mechanic, monkeypatch
):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace

    from fastapi import HTTPException
    from sqlalchemy.orm import Session

    from robopark_api.models import User
    from robopark_api.services import tracker_submissions

    barrier = threading.Barrier(2)

    def issue(**kw):
        barrier.wait(timeout=5)
        return {
            "key": "ROBOPARK-1",
            "status": "Open",
            "status_key": "open",
            "queue": "ROBOPARK",
            "tags": ["Alpha"],
        }

    monkeypatch.setattr(tracker_client, "get_issue", issue)
    actor_id = seed_mechanic.id

    def reserve():
        with Session(db_engine) as db:
            user = db.get(User, actor_id)
            try:
                row, _ = tracker_submissions.begin(
                    db,
                    user,
                    "ROBOPARK-1",
                    "comment",
                    SimpleNamespace(headers=headers()),
                    {"text": "one"},
                    "test",
                )
                return row.state
            except HTTPException as error:
                return error.detail

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reserve(), range(2)))
    assert sorted(results) == ["pending", "tracker_submission_uncertain"]


def test_presence_expires_and_does_not_show_self(
    client, tracker_setup, db_session, seed_royal, monkeypatch
):
    import time

    from robopark_api.collaboration_models import TrackerPresence

    db_session.add(
        TrackerPresence(issue_key="ROBOPARK-1", actor_id=seed_royal.id, expires_at=time.time() - 1)
    )
    db_session.commit()
    assert client.post("/tracker/issues/ROBOPARK-1/presence").json()["people"] == []


def test_submission_identity_isolated_by_actor_task_and_action(
    client, tracker_setup, seed_royal, monkeypatch
):
    written = []
    monkeypatch.setattr(
        tracker_client, "add_comment", lambda **kw: written.append(("comment", kw["key"]))
    )
    monkeypatch.setattr(
        tracker_client, "unassign_issue", lambda **kw: written.append(("unassign", kw["key"]))
    )
    assert (
        client.post(
            "/tracker/issues/ROBOPARK-1/comment", json={"text": "same"}, headers=headers()
        ).status_code
        == 200
    )
    login_as(client, "royal", "secret")
    assert (
        client.post(
            "/tracker/issues/ROBOPARK-1/comment", json={"text": "same"}, headers=headers()
        ).status_code
        == 200
    )
    tracker_setup["key"] = "ROBOPARK-2"
    assert (
        client.post(
            "/tracker/issues/ROBOPARK-2/comment", json={"text": "same"}, headers=headers()
        ).status_code
        == 200
    )
    assert client.post("/tracker/issues/ROBOPARK-2/unassign", headers=headers()).status_code == 200
    assert written == [
        ("comment", "ROBOPARK-1"),
        ("comment", "ROBOPARK-1"),
        ("comment", "ROBOPARK-2"),
        ("unassign", "ROBOPARK-2"),
    ]


def test_other_actor_cannot_mutate_task_during_inflight_write(
    db_engine, db_session, seed_mechanic, seed_royal, monkeypatch
):
    """The lease encloses the fresh read and write across independent requests."""
    from fastapi import HTTPException
    from sqlalchemy.orm import Session

    from robopark_api.services.tracker_submissions import task_mutation_lease

    with Session(db_engine) as first_db, Session(db_engine) as second_db:
        with task_mutation_lease(first_db, "ROBOPARK-1"):
            with (
                pytest.raises(HTTPException) as caught,
                task_mutation_lease(second_db, "ROBOPARK-1"),
            ):
                pytest.fail("another actor entered a running task mutation")
            assert caught.value.status_code == 409
            assert caught.value.detail == "tracker_task_busy"
            with task_mutation_lease(second_db, "ROBOPARK-2"):
                pass
        with task_mutation_lease(second_db, "ROBOPARK-1"):
            pass


def test_tracker_write_timeout_not_retried_by_transport(monkeypatch):
    from types import SimpleNamespace

    written = []

    def create(**kwargs):
        written.append(kwargs)
        raise TimeoutError("timeout after acceptance")

    fake = SimpleNamespace(
        issues={"ROBOPARK-1": SimpleNamespace(comments=SimpleNamespace(create=create))}
    )
    monkeypatch.setattr(tracker_client, "_client", lambda token: fake)
    with pytest.raises(tracker_client.TrackerError):
        tracker_client.add_comment(token="test", key="ROBOPARK-1", text="one")
    assert len(written) == 1


def test_two_actors_assigning_same_snapshot_are_serialized_and_second_must_review(
    db_engine, db_session, seed_mechanic, seed_royal, monkeypatch
):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from fastapi import Depends, FastAPI, Header
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

    from robopark_api.db import get_db
    from robopark_api.deps import require_user
    from robopark_api.models import User
    from robopark_api.routers.tracker_actions import router

    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    issue = {
        "key": "ROBOPARK-1",
        "status": "Open",
        "status_key": "open",
        "queue": "ROBOPARK",
        "tags": ["Alpha"],
    }
    entered = threading.Event()
    release = threading.Event()
    writes = []
    monkeypatch.setattr(tracker_client, "get_issue", lambda **kwargs: dict(issue))

    def assign(**kwargs):
        writes.append(kwargs["assignee"])
        entered.set()
        assert release.wait(timeout=5)
        issue["assignee"] = {"login": kwargs["assignee"]}

    monkeypatch.setattr(tracker_client, "assign_issue", assign)
    app = FastAPI()
    app.include_router(router)

    def database():
        with Session(db_engine) as db:
            yield db

    def actor(x_actor: int = Header(), db: Session = Depends(get_db)):
        return db.get(User, x_actor)

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[require_user] = actor
    first_headers = {**headers("first-request"), "X-Actor": str(seed_mechanic.id)}
    second_headers = {**headers("second-request"), "X-Actor": str(seed_royal.id)}
    with (
        TestClient(app) as first,
        TestClient(app) as second,
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        pending = pool.submit(
            first.post,
            "/tracker/issues/ROBOPARK-1/assign",
            json={"assignee": "alice"},
            headers=first_headers,
        )
        try:
            assert entered.wait(timeout=5)
            busy = second.post(
                "/tracker/issues/ROBOPARK-1/assign",
                json={"assignee": "bob"},
                headers=second_headers,
            )
            assert busy.status_code == 409
            assert busy.json()["detail"] == "tracker_task_busy"
        finally:
            release.set()
        assert pending.result(timeout=5).status_code == 200
        stale = second.post(
            "/tracker/issues/ROBOPARK-1/assign", json={"assignee": "bob"}, headers=second_headers
        )
        assert stale.status_code == 409
        assert stale.json()["detail"] == "tracker_state_conflict"
    assert writes == ["alice"]


def test_sdk_retries_disabled_for_durable_mutations(monkeypatch):
    constructed = []
    monkeypatch.setattr(tracker_client, "_CLIENTS", {})
    monkeypatch.setattr(
        tracker_client, "_import_startrek", lambda: lambda **kwargs: constructed.append(kwargs)
    )
    tracker_client._client("test-only")
    assert constructed[0]["retries"] == 0
    assert constructed[0]["timeout"] == 10


@pytest.mark.parametrize("action", ["transition", "close"])
def test_successful_workflow_submission_replays_after_transition_disappears(
    client, tracker_setup, monkeypatch, action
):
    written = []
    transitions = [{"id": "close", "display": "Закрыть"}]
    monkeypatch.setattr(tracker_client, "list_transitions", lambda **kw: list(transitions))

    def perform(**kw):
        written.append(kw)
        tracker_setup["status"] = "Closed"
        tracker_setup["status_key"] = "closed"
        transitions.clear()

    monkeypatch.setattr(tracker_client, "transition_issue", perform)
    payload = {"transition": "close"} if action == "transition" else None
    first = client.post(f"/tracker/issues/ROBOPARK-1/{action}", json=payload, headers=headers())
    assert first.status_code == 200
    # Browser never receives the first response and retries its durable key.
    replayed = client.post(f"/tracker/issues/ROBOPARK-1/{action}", json=payload, headers=headers())
    assert replayed.status_code == 200
    assert replayed.json() == first.json()
    assert len(written) == 1
    tracker_setup["tags"] = ["Other"]
    assert (
        client.post(
            f"/tracker/issues/ROBOPARK-1/{action}", json=payload, headers=headers()
        ).status_code
        == 403
    )


def test_invalid_workflow_does_not_reserve_an_uncertain_submission(
    client, tracker_setup, monkeypatch
):
    transitions = []
    monkeypatch.setattr(tracker_client, "list_transitions", lambda **kw: list(transitions))
    written = []
    monkeypatch.setattr(tracker_client, "transition_issue", lambda **kw: written.append(kw))
    payload = {"transition": "close"}
    assert (
        client.post(
            "/tracker/issues/ROBOPARK-1/transition", json=payload, headers=headers()
        ).status_code
        == 400
    )
    transitions.append({"id": "close", "display": "Закрыть"})
    from robopark_api.services import tracker_cache

    tracker_cache.invalidate_issue("ROBOPARK-1")
    assert (
        client.post(
            "/tracker/issues/ROBOPARK-1/transition", json=payload, headers=headers()
        ).status_code
        == 200
    )
    assert len(written) == 1
