import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from conftest import login_as, role_id_for
from robopark_api.models import AccessStatus, Park, User, UserPark
from robopark_api.schedule_models import NotificationEvent
from robopark_api.security import hash_password
from robopark_api.task_workflow_models import MediaUploadSession, TaskReview


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


def test_offline_claim_rejects_submitted_park_that_differs_from_fresh_issue(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    from robopark_api.services import platform_settings, tracker_client

    other = Park(name="Other", tag="Other", tracker_queue="ROBOPARK", is_active=True)
    db_session.add(other)
    db_session.flush()
    db_session.add(UserPark(user_id=seed_mechanic.id, park_id=other.id))
    db_session.commit()
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    monkeypatch.setattr(
        tracker_client,
        "get_issue",
        lambda **_kwargs: {
            "key": "ROBOPARK-51",
            "status": "В очереди",
            "status_key": "queued",
            "queue": "ROBOPARK",
            "tags": [seed_park_with_tracker.tag],
        },
    )
    login_as(client, seed_mechanic.username, "secret")

    response = client.post(
        "/sync/batch",
        json=_batch(_action("wrong-park", action="claim", park_id=other.id, payload={})),
    )

    assert response.status_code == 200
    assert response.json()["results"][0]["state"] == "conflict"
    assert response.json()["results"][0]["code"] == "sync_park_mismatch"


def test_offline_claim_uses_fresh_closed_state_instead_of_cached_open_state(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    from robopark_api.services import platform_settings, tracker_cache, tracker_client

    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    monkeypatch.setattr(
        tracker_cache,
        "get_issue",
        lambda **_kwargs: {
            "key": "ROBOPARK-51",
            "summary": "blocker [447]",
            "status": "В очереди",
            "status_key": "queued",
            "queue": "ROBOPARK",
            "tags": ["Alpha"],
        },
    )
    monkeypatch.setattr(
        tracker_client,
        "get_issue",
        lambda **_kwargs: {
            "key": "ROBOPARK-51",
            "summary": "blocker [447]",
            "status": "Закрыта",
            "status_key": "closed",
            "queue": "ROBOPARK",
            "tags": ["Alpha"],
        },
    )
    login_as(client, seed_mechanic.username, "secret")

    response = client.post(
        "/sync/batch",
        json=_batch(
            _action("fresh-close", action="claim", park_id=seed_park_with_tracker.id, payload={})
        ),
    )

    assert response.status_code == 200
    assert response.json()["results"][0]["state"] == "conflict"
    assert response.json()["results"][0]["code"] == "task_already_closed"


def test_offline_review_persists_operator_notification_with_review(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch, tmp_path
):
    from robopark_api.services import media_uploads, platform_settings, tracker_client
    from robopark_api.services.tracker_claims import claim_issue

    operator = User(
        username="offline-review-operator",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(operator)
    db_session.flush()
    db_session.add(UserPark(user_id=operator.id, park_id=seed_park_with_tracker.id))
    claim_issue(
        db_session,
        actor=seed_mechanic,
        owner=seed_mechanic,
        issue_key="ROBOPARK-51",
        park_id=seed_park_with_tracker.id,
    )
    photo = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
    )
    photo_path = tmp_path / "review.png"
    photo_path.write_bytes(photo)
    db_session.add(
        MediaUploadSession(
            actor_user_id=seed_mechanic.id,
            media_id="offline-review-photo",
            issue_key="ROBOPARK-51",
            original_name="review.png",
            mime_type="image/png",
            size_bytes=len(photo),
            sha256=hashlib.sha256(photo).hexdigest(),
            received_offset=len(photo),
            blob_name="review-photo",
            completed=True,
            expires_at=time.time() + 3600,
        )
    )
    db_session.commit()
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    monkeypatch.setattr(media_uploads, "content_path", lambda _row: photo_path)
    monkeypatch.setattr(
        tracker_client,
        "get_issue",
        lambda **_kwargs: {
            "key": "ROBOPARK-51",
            "summary": "blocker [447]",
            "status": "В работе",
            "status_key": "in_progress",
            "queue": "ROBOPARK",
            "tags": [seed_park_with_tracker.tag],
        },
    )
    login_as(client, seed_mechanic.username, "secret")

    response = client.post(
        "/sync/batch",
        json=_batch(
            _action(
                "offline-review",
                action="submit_review",
                park_id=seed_park_with_tracker.id,
                payload={
                    "media_id": "offline-review-photo",
                    "defect_code": "BD-01",
                    "comment": "Исправлено",
                },
            )
        ),
    )

    assert response.status_code == 200
    assert response.json()["results"][0]["state"] == "confirmed"
    assert (
        db_session.scalar(select(TaskReview).where(TaskReview.issue_key == "ROBOPARK-51"))
        is not None
    )
    assert (
        db_session.scalar(
            select(NotificationEvent).where(
                NotificationEvent.user_id == operator.id,
                NotificationEvent.event_type == "review_task",
            )
        )
        is not None
    )


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
