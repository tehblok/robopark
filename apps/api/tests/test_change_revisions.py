from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request

from conftest import login_as
from robopark_api.models import Park
from robopark_api.routers import changes, tracker_actions
from robopark_api.services.change_revisions import ChangeRevisionStore, scope_for_mutation


def test_revisions_are_shared_between_instances_and_scoped(tmp_path):
    first = ChangeRevisionStore(tmp_path)
    second = ChangeRevisionStore(tmp_path)

    assert first.current("work") == 0
    assert first.mark_changed("work") == 1
    assert second.current("work") == 1
    assert second.current("inventory:7") == 0


def test_concurrent_writers_do_not_lose_revisions(tmp_path):
    store = ChangeRevisionStore(tmp_path)

    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _index: store.mark_changed("work"), range(32)))

    assert sorted(values) == list(range(1, 33))
    assert store.current("work") == 32


def test_change_endpoint_requires_auth_and_returns_only_revision(
    client, seed_mechanic, seed_park_with_tracker, tmp_path
):
    store = ChangeRevisionStore(tmp_path)
    client.app.dependency_overrides[changes.get_change_store] = lambda: store
    assert client.get("/changes?scope=work").status_code == 401
    assert login_as(client, seed_mechanic.username, "secret").status_code == 204

    version = client.get("/changes?scope=work:mine")
    assert version.json() == {"revision": 0}
    assert len(version.content) < 64
    assert version.headers["cache-control"] == "private, no-cache"
    unchanged = client.get(
        "/changes?scope=work:mine",
        headers={"If-None-Match": version.headers["etag"]},
    )
    assert unchanged.status_code == 304
    assert unchanged.content == b""
    store.mark_changed(f"work:park:{seed_park_with_tracker.id}")
    assert client.get("/changes?scope=work:mine").json() == {"revision": 1}
    assert client.get("/changes?scope=work").status_code == 403
    assert client.get("/changes?scope=reports").status_code == 404


def test_restricted_work_hint_does_not_reveal_other_parks(
    client, db_session, seed_mechanic, seed_park_with_tracker, tmp_path
):
    other = Park(name="Beta", tag="Beta", is_active=True, tracker_queue="BETA")
    db_session.add(other)
    db_session.commit()
    store = ChangeRevisionStore(tmp_path)
    client.app.dependency_overrides[changes.get_change_store] = lambda: store
    assert login_as(client, seed_mechanic.username, "secret").status_code == 204

    store.mark_changed(f"work:park:{other.id}")
    assert client.get("/changes?scope=work:mine").json() == {"revision": 0}
    store.mark_changed(f"work:park:{seed_park_with_tracker.id}")
    assert client.get("/changes?scope=work:mine").json() == {"revision": 1}


def test_change_endpoint_rejects_unassigned_park(client, seed_mechanic, tmp_path):
    store = ChangeRevisionStore(tmp_path)
    client.app.dependency_overrides[changes.get_change_store] = lambda: store
    assert login_as(client, seed_mechanic.username, "secret").status_code == 204

    response = client.get("/changes?scope=inventory:999")
    assert response.status_code in (403, 404)
    assert response.json() != {"revision": 0}


def test_inventory_park_version_includes_global_catalog(
    client, seed_mechanic, seed_park_with_tracker, tmp_path
):
    store = ChangeRevisionStore(tmp_path)
    client.app.dependency_overrides[changes.get_change_store] = lambda: store
    assert login_as(client, seed_mechanic.username, "secret").status_code == 204
    park_id = seed_park_with_tracker.id
    scope = f"inventory:{park_id}"
    assert client.get(f"/changes?scope={scope}").json() == {"revision": 0}
    store.mark_changed("inventory:catalog")
    assert client.get(f"/changes?scope={scope}").json() == {"revision": 1}


def test_successful_mutation_publishes_scope_but_failure_does_not(client, tmp_path):
    store = ChangeRevisionStore(tmp_path)
    client.app.state.change_revision_store = store

    @client.app.post("/tracker/_revision-success")
    def success():
        return {"ok": True}

    @client.app.post("/tracker/_revision-failure")
    def failure():
        raise HTTPException(status_code=409)

    @client.app.post("/inventory/parks/7/_revision-success")
    def stock_success():
        return {"ok": True}

    @client.app.post("/inventory/tasks/_revision-success")
    def task_stock_success(request: Request):
        request.state.change_scopes = ("inventory:7", "work:park:7", "work")
        return {"ok": True}

    assert client.post("/tracker/_revision-success").status_code == 200
    assert client.post("/tracker/_revision-failure").status_code == 409
    assert client.post("/inventory/parks/7/_revision-success").status_code == 200
    assert client.post("/inventory/tasks/_revision-success").status_code == 200
    assert store.current("work") == 2
    assert store.current("inventory:7") == 2
    assert store.current("work:park:7") == 1
    assert store.current("inventory:8") == 0


def test_presence_ping_never_invalidates_work():
    assert scope_for_mutation("/tracker/issues/TEST-1/presence") is None
    assert scope_for_mutation("/tracker/issues/TEST-1/comment") == "work"


def test_authorized_tracker_mutation_marks_its_park(db_session, seed_park_with_tracker):
    request = Request(
        {"type": "http", "method": "POST", "path": "/tracker/issues/TEST-1/claim", "headers": []}
    )
    tracker_actions._mark_park_change(request, db_session, {"key": "TEST-1", "queue": "ROBOPARK"})
    assert request.state.change_scopes == (f"work:park:{seed_park_with_tracker.id}",)


def test_tracker_mutation_marks_every_visible_park(db_session, seed_park_with_tracker):
    other = Park(name="Beta", tag="Beta", is_active=True, tracker_queue="ROBOPARK")
    db_session.add(other)
    db_session.commit()
    request = Request(
        {"type": "http", "method": "POST", "path": "/tracker/issues/TEST-1/comment", "headers": []}
    )

    tracker_actions._mark_park_change(
        request, db_session, {"key": "TEST-1", "tags": ["Alpha", "Beta"], "queue": "ROBOPARK"}
    )
    assert set(request.state.change_scopes) == {
        f"work:park:{seed_park_with_tracker.id}",
        f"work:park:{other.id}",
    }


def test_legacy_close_dependency_marks_park(monkeypatch, db_session, seed_park_with_tracker):
    issue = {"key": "TEST-1", "tags": [seed_park_with_tracker.tag], "queue": "ROBOPARK"}
    monkeypatch.setattr(tracker_actions, "_ensure_tracker_user", lambda *_: None)
    monkeypatch.setattr(tracker_actions.task_lifecycle, "is_hidden", lambda *_: False)
    monkeypatch.setattr(tracker_actions, "_require_token", lambda *_: "test-token")
    monkeypatch.setattr(tracker_actions, "_get_issue_or_404", lambda *_: issue)
    monkeypatch.setattr(tracker_actions, "ensure_action_allowed", lambda *_: None)
    monkeypatch.setattr(
        tracker_actions.submissions, "task_mutation_lease", lambda *_: nullcontext()
    )
    request = Request(
        {"type": "http", "method": "POST", "path": "/tracker/issues/TEST-1/close", "headers": []}
    )
    lease = tracker_actions._mutation_lease(
        "TEST-1", request, SimpleNamespace(role="operator"), db_session
    )
    next(lease)
    assert request.state.change_scopes == (f"work:park:{seed_park_with_tracker.id}",)
    with pytest.raises(StopIteration):
        next(lease)
