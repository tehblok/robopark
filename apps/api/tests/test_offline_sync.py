import json
import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from conftest import login_as
from robopark_api.models import Park, User


def _action(
    action_id: str,
    *,
    action: str = "comment",
    park_id: int | None = None,
    payload: dict | None = None,
    dependencies: list[str] | None = None,
):
    return {
        "client_action_id": action_id,
        "resource_type": "tracker_issue",
        "resource_id": "ROBOPARK-51",
        "action": action,
        "idempotency_key": f"sync-{action_id}-key",
        "base_revision": None,
        "park_id": park_id,
        "dependencies": dependencies or [],
        "payload": payload or {"text": action_id},
    }


def _batch(*actions):
    return {"device_id": "phone-1", "known_revisions": {"work": 0}, "actions": actions}


def test_batch_orders_comment_before_review_and_returns_revisions(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    login_as(client, seed_mechanic.username, "secret")
    calls = []

    def dispatch(_db, _user, item):
        calls.append(item.client_action_id)
        return {"kind": item.action, "id": item.client_action_id}

    from robopark_api.services import offline_sync

    monkeypatch.setattr(offline_sync, "dispatch_action", dispatch)
    response = client.post(
        "/sync/batch",
        json=_batch(
            _action(
                "review",
                action="submit_review",
                park_id=seed_park_with_tracker.id,
                dependencies=["comment"],
            ),
            _action("comment", park_id=seed_park_with_tracker.id),
        ),
    )

    assert response.status_code == 200
    assert calls == ["comment", "review"]
    assert [item["state"] for item in response.json()["results"]] == ["confirmed", "confirmed"]
    assert response.json()["revisions"]["work"] >= 0
    assert response.json()["deltas"] == {}


def test_replaying_batch_after_response_loss_returns_receipt_without_second_action(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    login_as(client, seed_mechanic.username, "secret")
    calls = 0

    def dispatch(_db, _user, item):
        nonlocal calls
        calls += 1
        return {"message_id": "message-1", "action": item.action}

    from robopark_api.services import offline_sync

    monkeypatch.setattr(offline_sync, "dispatch_action", dispatch)
    body = _batch(_action("comment", park_id=seed_park_with_tracker.id))

    first = client.post("/sync/batch", json=body)
    second = client.post("/sync/batch", json=body)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert calls == 1


def test_sqlite_concurrent_sync_replay_uses_one_dispatch_and_one_receipt(
    db_engine, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    """Separate SQLite sessions must not dispatch the same client action twice."""
    from robopark_api.services import offline_sync
    from robopark_api.task_workflow_models import OfflineSyncReceipt

    calls: list[str] = []
    dispatch_entered = threading.Event()
    allow_dispatch = threading.Event()
    start = threading.Barrier(3)

    class Revisions:
        def mark_changed(self, _scope):
            pass

        def current(self, _scope):
            return 0

    def dispatch(_db, _user, item):
        calls.append(item.client_action_id)
        dispatch_entered.set()
        assert allow_dispatch.wait(timeout=1)
        return {"message_id": "sqlite-1"}

    monkeypatch.setattr(offline_sync, "dispatch_action", dispatch)
    body = offline_sync.SyncBatchIn.model_validate(
        _batch(_action("concurrent", park_id=seed_park_with_tracker.id))
    )

    def synchronize_once():
        start.wait(timeout=1)
        with Session(db_engine) as db:
            user = db.get(User, seed_mechanic.id)
            return offline_sync.synchronize(db, user, body, revision_store=Revisions())

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(synchronize_once)
        second = executor.submit(synchronize_once)
        start.wait(timeout=1)
        assert dispatch_entered.wait(timeout=1)
        allow_dispatch.set()
        first_result = first.result(timeout=2)
        second_result = second.result(timeout=2)

    with Session(db_engine) as db:
        receipts = list(
            db.scalars(
                select(OfflineSyncReceipt).where(
                    OfflineSyncReceipt.client_action_id == "concurrent"
                )
            )
        )
    assert calls == ["concurrent"]
    assert first_result == second_result
    assert len(receipts) == 1


def test_same_client_action_with_different_payload_is_a_conflict(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    login_as(client, seed_mechanic.username, "secret")
    from robopark_api.services import offline_sync

    monkeypatch.setattr(offline_sync, "dispatch_action", lambda *_args: {"ok": True})
    first = client.post(
        "/sync/batch",
        json=_batch(_action("same", park_id=seed_park_with_tracker.id, payload={"text": "first"})),
    )
    second = client.post(
        "/sync/batch",
        json=_batch(
            _action("same", park_id=seed_park_with_tracker.id, payload={"text": "changed"})
        ),
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["results"] == [
        {
            "client_action_id": "same",
            "state": "conflict",
            "code": "sync_payload_conflict",
            "result": None,
        }
    ]


def test_unassigned_park_is_rejected_before_dispatch(
    client, db_session, seed_mechanic, monkeypatch
):
    other = Park(name="Other", tag="Other", tracker_queue="OTHER", is_active=True)
    db_session.add(other)
    db_session.commit()
    login_as(client, seed_mechanic.username, "secret")
    from robopark_api.services import offline_sync

    called = False

    def dispatch(*_args):
        nonlocal called
        called = True

    monkeypatch.setattr(offline_sync, "dispatch_action", dispatch)
    response = client.post("/sync/batch", json=_batch(_action("denied", park_id=other.id)))

    assert response.status_code == 200
    assert called is False
    assert response.json()["results"][0]["state"] == "rejected"
    assert response.json()["results"][0]["code"] == "park_forbidden"
    assert f"work:park:{other.id}" in response.json()["revoked_scopes"]


def test_mixed_batch_classifies_closed_task_stale_stock_and_server_failure(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    login_as(client, seed_mechanic.username, "secret")
    from robopark_api.services import offline_sync

    def dispatch(_db, _user, item):
        outcome = item.payload["outcome"]
        if outcome == "closed":
            raise HTTPException(409, "task_already_closed")
        if outcome == "stock":
            raise HTTPException(409, "inventory_revision_conflict")
        if outcome == "server":
            raise HTTPException(503, "tracker_upstream_error")
        return {"ok": True}

    monkeypatch.setattr(offline_sync, "dispatch_action", dispatch)
    response = client.post(
        "/sync/batch",
        json=_batch(
            _action("ok", park_id=seed_park_with_tracker.id, payload={"outcome": "ok"}),
            _action("closed", park_id=seed_park_with_tracker.id, payload={"outcome": "closed"}),
            _action("stock", park_id=seed_park_with_tracker.id, payload={"outcome": "stock"}),
            _action("server", park_id=seed_park_with_tracker.id, payload={"outcome": "server"}),
        ),
    )

    assert response.status_code == 200
    assert [
        (item["client_action_id"], item["state"], item["code"])
        for item in response.json()["results"]
    ] == [
        ("ok", "confirmed", None),
        ("closed", "conflict", "task_already_closed"),
        ("stock", "conflict", "inventory_revision_conflict"),
        ("server", "attention", "tracker_upstream_error"),
    ]
    from robopark_api.task_workflow_models import OfflineSyncReceipt

    receipts = set(db_session.scalars(select(OfflineSyncReceipt.client_action_id)))
    assert receipts == {"ok", "closed", "stock"}


def test_failed_dependency_is_not_dispatched(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    login_as(client, seed_mechanic.username, "secret")
    from robopark_api.services import offline_sync

    calls = []

    def dispatch(_db, _user, item):
        calls.append(item.client_action_id)
        raise HTTPException(409, "task_already_closed")

    monkeypatch.setattr(offline_sync, "dispatch_action", dispatch)
    response = client.post(
        "/sync/batch",
        json=_batch(
            _action("comment", park_id=seed_park_with_tracker.id),
            _action(
                "review",
                action="submit_review",
                park_id=seed_park_with_tracker.id,
                dependencies=["comment"],
            ),
        ),
    )

    assert calls == ["comment"]
    assert response.json()["results"][1]["state"] == "attention"
    assert response.json()["results"][1]["code"] == "dependency_failed"


def test_receipt_stores_canonical_result_json(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    login_as(client, seed_mechanic.username, "secret")
    from robopark_api.services import offline_sync
    from robopark_api.task_workflow_models import OfflineSyncReceipt

    monkeypatch.setattr(offline_sync, "dispatch_action", lambda *_args: {"message_id": "m-1"})
    response = client.post(
        "/sync/batch", json=_batch(_action("receipt", park_id=seed_park_with_tracker.id))
    )

    assert response.status_code == 200
    receipt = db_session.scalar(select(OfflineSyncReceipt))
    assert receipt is not None
    assert json.loads(receipt.result_json)["result"] == {"message_id": "m-1"}
