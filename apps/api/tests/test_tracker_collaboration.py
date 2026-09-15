"""Collaboration must not repeat upstream writes or cross issue scope."""

import hashlib
import json
import time
from urllib.parse import quote

import pytest

from conftest import login_as
from robopark_api.services import platform_settings, tracker_client
from robopark_api.task_workflow_models import ReliableAction


@pytest.fixture
def tracker_setup(client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch):
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    issue = {
        "key": "ROBOPARK-1",
        "status": "Open",
        "status_key": "open",
        "queue": "ROBOPARK",
        "tags": ["Alpha"],
        "assignee": {"login": "mech1", "display": "Mechanic"},
    }
    monkeypatch.setattr(tracker_client, "get_issue", lambda **kw: dict(issue))
    from robopark_api.services.tracker_claims import claim_issue

    claim_issue(
        db_session,
        actor=seed_mechanic,
        owner=seed_mechanic,
        issue_key="ROBOPARK-1",
        park_id=seed_park_with_tracker.id,
    )
    login_as(client, "mech1", "secret")
    return issue


def headers(key="submission-one", assignee="mech1"):
    return {
        "Idempotency-Key": key,
        "X-Tracker-State": quote(
            json.dumps({"status": "Open", "status_key": "open", "assignee": assignee})
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


def test_submission_state_uses_local_claim_when_tracker_has_no_assignee(
    client, tracker_setup, monkeypatch
):
    tracker_setup["assignee"] = None
    written = []
    monkeypatch.setattr(tracker_client, "add_comment", lambda **kw: written.append(kw))

    response = client.post(
        "/tracker/issues/ROBOPARK-1/comment",
        json={"text": "continue after takeover"},
        headers=headers("local-claim-state", assignee="mech1"),
    )

    assert response.status_code == 200
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


def test_old_unknown_outcome_stays_durable_and_is_never_replayed(
    client, db_session, tracker_setup, monkeypatch
):
    written = []

    def lost_response(**kw):
        written.append(kw)
        raise tracker_client.TrackerError("timeout after acceptance")

    monkeypatch.setattr(tracker_client, "add_comment", lost_response)
    first = client.post(
        "/tracker/issues/ROBOPARK-1/comment", json={"text": "done"}, headers=headers()
    )
    assert first.status_code == 409
    row = db_session.query(ReliableAction).one()
    row.created_at = time.time() - 61
    db_session.commit()

    second = client.post(
        "/tracker/issues/ROBOPARK-1/comment", json={"text": "done"}, headers=headers()
    )

    assert second.status_code == 409
    assert second.json()["detail"] == "tracker_submission_uncertain"
    assert len(written) == 1
    assert db_session.query(ReliableAction).count() == 1
    assert db_session.query(ReliableAction).one().state == "needs_attention"


def test_tracker_compatibility_uses_canonical_payload_hash(
    client, db_session, tracker_setup, monkeypatch
):
    monkeypatch.setattr(tracker_client, "add_comment", lambda **kw: None)

    response = client.post(
        "/tracker/issues/ROBOPARK-1/comment",
        json={"text": "готово"},
        headers=headers("canonical-payload"),
    )

    assert response.status_code == 200
    row = db_session.query(ReliableAction).one()
    canonical = json.dumps(
        {"text": "готово"}, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    assert row.payload_json == canonical
    assert row.payload_hash == hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_tracker_replays_migrated_succeeded_action_with_legacy_payload_hash(
    client, db_session, seed_mechanic, tracker_setup, monkeypatch
):
    payload = {"text": "готово"}
    legacy = json.dumps(payload, sort_keys=True)
    saved = {
        "key": "ROBOPARK-1",
        "action": "comment",
        "status": "Open",
        "actor": "mech1",
        "performed_at": "2026-09-15T12:00:00+00:00",
    }
    db_session.add(
        ReliableAction(
            actor_user_id=seed_mechanic.id,
            resource_type="tracker_issue",
            resource_id="ROBOPARK-1",
            action="comment",
            idempotency_key="migrated-action",
            payload_hash=hashlib.sha256(legacy.encode()).hexdigest(),
            payload_json="{}",
            state="succeeded",
            result_json=json.dumps(saved),
            created_at=time.time() - 100,
            updated_at=time.time() - 100,
        )
    )
    db_session.commit()
    monkeypatch.setattr(
        tracker_client,
        "add_comment",
        lambda **kw: pytest.fail("saved migrated action must not be sent again"),
    )

    response = client.post(
        "/tracker/issues/ROBOPARK-1/comment",
        json=payload,
        headers=headers("migrated-action"),
    )

    assert response.status_code == 200
    assert response.json() == saved


def test_submission_lookup_is_scoped_to_tracker_issue_resource_type(
    client, db_session, seed_mechanic, tracker_setup, monkeypatch
):
    payload = {"text": "done"}
    db_session.add(
        ReliableAction(
            actor_user_id=seed_mechanic.id,
            resource_type="inventory",
            resource_id="ROBOPARK-1",
            action="comment",
            idempotency_key="resource-scope",
            payload_hash=hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
            payload_json=json.dumps(payload),
            state="pending",
            created_at=time.time(),
            updated_at=time.time(),
        )
    )
    db_session.commit()
    written = []
    monkeypatch.setattr(tracker_client, "add_comment", lambda **kw: written.append(kw))

    response = client.post(
        "/tracker/issues/ROBOPARK-1/comment",
        json=payload,
        headers=headers("resource-scope"),
    )

    assert response.status_code == 200
    assert len(written) == 1
    assert {row.resource_type for row in db_session.query(ReliableAction)} == {
        "inventory",
        "tracker_issue",
    }


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
            "assignee": {"login": "mech1"},
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
        ("comment", "ROBOPARK-2"),
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


def test_assignments_are_local_and_staff_can_take_over(
    client, db_session, seed_mechanic, seed_royal, seed_park_with_tracker, monkeypatch
):
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    seed_mechanic.tracker_login = None
    db_session.commit()
    issue = {
        "key": "ROBOPARK-1",
        "status": "Open",
        "status_key": "open",
        "queue": "ROBOPARK",
        "tags": ["Alpha"],
    }
    monkeypatch.setattr(tracker_client, "get_issue", lambda **kwargs: dict(issue))
    monkeypatch.setattr(
        tracker_client,
        "assign_issue",
        lambda **kwargs: pytest.fail("local assignment must not call Tracker assignment"),
    )

    login_as(client, seed_mechanic.username, "secret")
    assert (
        client.post(
            "/tracker/issues/ROBOPARK-1/assign",
            json={"assignee": seed_mechanic.username},
        ).status_code
        == 200
    )
    login_as(client, seed_royal.username, "secret")
    assert (
        client.post(
            "/tracker/issues/ROBOPARK-1/assign",
            json={"assignee": seed_royal.username},
        ).status_code
        == 200
    )

    from robopark_api.services.tracker_claims import local_assignee

    assert local_assignee(db_session, issue)["login"] == seed_royal.username


def test_sdk_retries_disabled_for_durable_mutations(monkeypatch):
    constructed = []
    monkeypatch.setattr(tracker_client, "_CLIENTS", {})
    monkeypatch.setattr(
        tracker_client, "_import_startrek", lambda: lambda **kwargs: constructed.append(kwargs)
    )
    tracker_client._client("test-only")
    assert constructed[0]["retries"] == 0
    assert constructed[0]["timeout"] == 10
    assert constructed[0]["headers"] == {"User-Agent": tracker_client.USER_AGENT}
    assert "useragent" not in constructed[0]


def test_tracker_uses_the_declared_production_sdk():
    from yandex_tracker_client import TrackerClient

    assert tracker_client._import_startrek() is TrackerClient


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
