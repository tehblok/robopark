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
from robopark_api.schedule_models import NotificationEvent, ScheduleEntry
from robopark_api.security import hash_password
from robopark_api.task_workflow_models import MediaUploadSession, ReliableAction, TaskReview


def test_sync_transport_failure_does_not_expose_exception_text():
    from robopark_api.services.offline_sync import _classification

    assert _classification(RuntimeError("private upstream diagnostic detail")) == (
        "attention",
        "temporary_failure",
    )
    assert _classification(HTTPException(503, "private upstream diagnostic detail")) == (
        "attention",
        "temporary_failure",
    )
    assert _classification(PermissionError("private authorization detail")) == (
        "rejected",
        "forbidden",
    )
    assert _classification(PermissionError("inventory_issue_not_owned")) == (
        "rejected",
        "inventory_issue_not_owned",
    )


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


def _schedule_action(
    action_id: str,
    *,
    action: str,
    resource_id: str,
    park_id: int,
    payload: dict,
    base_revision: str | None = None,
):
    return {
        "client_action_id": action_id,
        "resource_type": "schedule_entry",
        "resource_id": resource_id,
        "action": action,
        "idempotency_key": f"schedule-{action_id}-key",
        "base_revision": base_revision,
        "park_id": park_id,
        "dependencies": [],
        "payload": payload,
    }


def test_schedule_batch_replays_create_then_checks_edit_and_delete_revision(
    client, db_session, seed_mechanic, seed_park_with_tracker
):
    login_as(client, seed_mechanic.username, "secret")
    park_id = seed_park_with_tracker.id
    create = _schedule_action(
        "create-shift",
        action="schedule_create",
        resource_id="client-shift-1",
        park_id=park_id,
        payload={
            "park_id": park_id,
            "kind": "shift",
            "start_at": "2026-09-21T09:00:00+03:00",
            "end_at": "2026-09-21T21:00:00+03:00",
            "timezone": "Europe/Moscow",
        },
    )
    first = client.post("/sync/batch", json=_batch(create))
    replay = client.post("/sync/batch", json=_batch(create))
    assert first.status_code == replay.status_code == 200
    assert first.json()["results"] == replay.json()["results"]
    assert first.json()["results"][0]["state"] == "confirmed"
    assert f"schedule:park:{park_id}" in first.json()["revisions"]
    created = first.json()["results"][0]["result"]["entry"]
    assert (
        len(
            list(db_session.scalars(select(ScheduleEntry).where(ScheduleEntry.id == created["id"])))
        )
        == 1
    )

    edit = _schedule_action(
        "edit-shift",
        action="schedule_update",
        resource_id=created["id"],
        park_id=park_id,
        payload={"kind": "vacation"},
        base_revision=created["updated_at"],
    )
    changed = client.post("/sync/batch", json=_batch(edit))
    stale = client.post(
        "/sync/batch",
        json=_batch(
            _schedule_action(
                "stale-shift",
                action="schedule_update",
                resource_id=created["id"],
                park_id=park_id,
                payload={"kind": "sick"},
                base_revision=created["updated_at"],
            )
        ),
    )
    assert changed.json()["results"][0]["state"] == "confirmed"
    assert stale.json()["results"][0]["code"] == "schedule_revision_conflict"
    updated = changed.json()["results"][0]["result"]["entry"]

    remove = _schedule_action(
        "delete-shift",
        action="schedule_delete",
        resource_id=created["id"],
        park_id=park_id,
        payload={},
        base_revision=updated["updated_at"],
    )
    deleted = client.post("/sync/batch", json=_batch(remove))
    deleted_again = client.post("/sync/batch", json=_batch(remove))
    assert deleted.json()["results"] == deleted_again.json()["results"]
    assert deleted.json()["results"][0]["state"] == "confirmed"
    assert db_session.get(ScheduleEntry, created["id"]) is None


def test_schedule_batch_reports_missing_entry_without_failing_whole_batch(
    client, seed_mechanic, seed_park_with_tracker
):
    login_as(client, seed_mechanic.username, "secret")
    missing = _schedule_action(
        "missing-shift",
        action="schedule_update",
        resource_id="missing-entry",
        park_id=seed_park_with_tracker.id,
        payload={"kind": "vacation"},
        base_revision="2026-09-20T10:00:00Z",
    )

    response = client.post("/sync/batch", json=_batch(missing))

    assert response.status_code == 200
    assert response.json()["results"][0]["state"] == "rejected"
    assert response.json()["results"][0]["code"] == "schedule_not_found"


def test_schedule_batch_queues_pattern_and_copy_without_returning_a_large_result(
    client, db_session, seed_mechanic, seed_park_with_tracker
):
    login_as(client, seed_mechanic.username, "secret")
    park_id = seed_park_with_tracker.id
    pattern = _schedule_action(
        "pattern-one",
        action="schedule_pattern",
        resource_id="client-pattern-one",
        park_id=park_id,
        payload={
            "park_id": park_id,
            "owner_user_ids": [seed_mechanic.id],
            "kind": "shift",
            "pattern": "none",
            "start_date": "2026-09-21",
            "end_date": "2026-09-21",
            "start_time": "09:00",
            "end_time": "21:00",
            "timezone": "Europe/Moscow",
        },
    )
    first = client.post("/sync/batch", json=_batch(pattern))
    replay = client.post("/sync/batch", json=_batch(pattern))
    assert first.status_code == replay.status_code == 200
    assert first.json()["results"] == replay.json()["results"]
    assert first.json()["results"][0]["result"] == {"created_count": 1}

    copy = _schedule_action(
        "copy-one",
        action="schedule_copy",
        resource_id="client-copy-one",
        park_id=park_id,
        payload={
            "park_id": park_id,
            "owner_user_ids": [seed_mechanic.id],
            "source_start": "2026-09-21T00:00:00+03:00",
            "source_end": "2026-09-22T00:00:00+03:00",
            "target_start": "2026-09-28T00:00:00+03:00",
            "timezone": "Europe/Moscow",
        },
    )
    copied = client.post("/sync/batch", json=_batch(copy))
    copied_again = client.post("/sync/batch", json=_batch(copy))
    assert copied.status_code == copied_again.status_code == 200
    assert copied.json()["results"] == copied_again.json()["results"]
    assert copied.json()["results"][0]["result"] == {"created_count": 1}
    assert (
        len(list(db_session.scalars(select(ScheduleEntry).where(ScheduleEntry.park_id == park_id))))
        == 2
    )


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


def test_receipt_replay_does_not_expose_result_after_park_access_is_revoked(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    login_as(client, seed_mechanic.username, "secret")
    from robopark_api.services import offline_sync

    monkeypatch.setattr(offline_sync, "dispatch_action", lambda *_args: {"private": "park-result"})
    body = _batch(_action("receipt-revoked", park_id=seed_park_with_tracker.id))
    first = client.post("/sync/batch", json=body)
    assert first.json()["results"][0]["state"] == "confirmed"

    membership = db_session.scalar(
        select(UserPark).where(
            UserPark.user_id == seed_mechanic.id,
            UserPark.park_id == seed_park_with_tracker.id,
        )
    )
    db_session.delete(membership)
    db_session.commit()

    replay = client.post("/sync/batch", json=body)
    assert replay.status_code == 200
    assert replay.json()["results"][0] == {
        "client_action_id": "receipt-revoked",
        "state": "rejected",
        "code": "park_forbidden",
        "result": None,
    }
    assert f"work:park:{seed_park_with_tracker.id}" in replay.json()["revoked_scopes"]


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


def test_offline_claim_preserves_existing_component_without_catalog_lookup(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    from robopark_api.services import platform_settings, tracker_client

    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    monkeypatch.setattr(
        tracker_client,
        "get_issue",
        lambda **_kwargs: {
            "key": "ROBOPARK-51",
            "summary": "blocker [447]",
            "status": "В очереди",
            "status_key": "queued",
            "queue": "ROBOPARK",
            "tags": [seed_park_with_tracker.tag],
            "components": ["Существующая"],
            "component_ids": ["existing"],
        },
    )
    monkeypatch.setattr(
        tracker_client,
        "list_queue_components",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("Existing component must not require catalog")
        ),
    )
    login_as(client, seed_mechanic.username, "secret")

    response = client.post(
        "/sync/batch",
        json=_batch(
            _action(
                "existing-component-claim",
                action="claim",
                park_id=seed_park_with_tracker.id,
                payload={},
            )
        ),
    )

    assert response.status_code == 200
    assert response.json()["results"][0]["state"] == "confirmed", response.json()


def test_offline_empty_claim_queues_temporary_component_without_catalog_lookup(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    from robopark_api.services import platform_settings, tracker_client

    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    monkeypatch.setattr(
        tracker_client,
        "get_issue",
        lambda **_kwargs: {
            "key": "ROBOPARK-51",
            "summary": "blocker [447]",
            "status": "В очереди",
            "status_key": "queued",
            "queue": "ROBOPARK",
            "tags": [seed_park_with_tracker.tag],
            "components": [],
            "component_ids": [],
        },
    )
    monkeypatch.setattr(
        tracker_client,
        "list_queue_components",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("Offline claim must not require component catalog")
        ),
    )
    login_as(client, seed_mechanic.username, "secret")

    response = client.post(
        "/sync/batch",
        json=_batch(
            _action(
                "empty-component-claim",
                action="claim",
                park_id=seed_park_with_tracker.id,
                payload={},
            )
        ),
    )

    assert response.status_code == 200
    assert response.json()["results"][0]["state"] == "confirmed"
    component = db_session.scalar(
        select(ReliableAction).where(ReliableAction.action == "ensure_components")
    )
    payload = json.loads(component.payload_json)
    assert (payload["policy"], payload["name"]) == (
        "temporary_component",
        "ROBOT_UNSORTED",
    )


def test_offline_nonmechanic_claim_is_rejected_before_catalog_lookup(
    client, db_session, seed_royal, seed_park_with_tracker, monkeypatch
):
    from robopark_api.services import platform_settings, tracker_client

    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    monkeypatch.setattr(
        tracker_client,
        "get_issue",
        lambda **_kwargs: {
            "key": "ROBOPARK-51",
            "summary": "blocker [447]",
            "status": "В очереди",
            "status_key": "queued",
            "queue": "ROBOPARK",
            "tags": [seed_park_with_tracker.tag],
            "components": [],
            "component_ids": [],
        },
    )
    monkeypatch.setattr(
        tracker_client,
        "list_queue_components",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("Denied role must not require component catalog")
        ),
    )
    login_as(client, seed_royal.username, "secret")

    response = client.post(
        "/sync/batch",
        json=_batch(
            _action(
                "royal-claim",
                action="claim",
                park_id=seed_park_with_tracker.id,
                payload={},
            )
        ),
    )

    assert response.status_code == 200
    assert response.json()["results"][0] == {
        "client_action_id": "royal-claim",
        "state": "rejected",
        "code": "task_claim_mechanic_required",
        "result": None,
    }


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
    db_session.add(
        MediaUploadSession(
            actor_user_id=seed_mechanic.id,
            media_id="offline-review-invalid-photo",
            issue_key="ROBOPARK-51",
            original_name="review.png",
            mime_type="image/png",
            size_bytes=len(photo),
            sha256=hashlib.sha256(photo).hexdigest(),
            received_offset=len(photo),
            blob_name="review-invalid-photo",
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
            "components": ["ROBOT_UNSORTED"],
            "component_ids": ["162206"],
            "defect_code": None,
            "solution_method": None,
        },
    )
    monkeypatch.setattr(
        tracker_client,
        "list_queue_components",
        lambda **_kwargs: [
            {"id": "162206", "label": "ROBOT_UNSORTED"},
            {"id": "lidar", "label": "Лидар"},
        ],
    )
    login_as(client, seed_mechanic.username, "secret")

    invalid = client.post(
        "/sync/batch",
        json=_batch(
            _action(
                "offline-review-invalid-fields",
                action="submit_review",
                park_id=seed_park_with_tracker.id,
                payload={
                    "media_id": "offline-review-invalid-photo",
                    "defect_code": "BD-01",
                    "repair_fields": "not-an-object",
                },
            )
        ),
    )
    assert invalid.status_code == 200
    assert invalid.json()["results"][0] == {
        "client_action_id": "offline-review-invalid-fields",
        "state": "rejected",
        "code": "repair_fields_invalid",
        "result": None,
    }

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
                    "repair_fields": {
                        "component_ids": ["lidar"],
                        "solution_method": "REPAIR",
                        "expected": {
                            "component_ids": ["162206"],
                            "defect_code": None,
                            "solution_method": None,
                        },
                    },
                },
            )
        ),
    )

    assert response.status_code == 200
    assert response.json()["results"][0]["state"] == "confirmed", response.json()
    assert (
        db_session.scalar(select(TaskReview).where(TaskReview.issue_key == "ROBOPARK-51"))
        is not None
    )
    fields_action = db_session.scalar(
        select(ReliableAction).where(
            ReliableAction.resource_id == "ROBOPARK-51",
            ReliableAction.action == "set_repair_fields",
        )
    )
    assert json.loads(fields_action.payload_json)["solution_method"] == "REPAIR"
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
    assert receipts == {"ok"}


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


def test_missing_dependency_from_prior_batch_never_dispatches(
    db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    from robopark_api.services import offline_sync

    class Revisions:
        def mark_changed(self, _scope):
            pass

        def current(self, _scope):
            return 0

    calls = []
    monkeypatch.setattr(
        offline_sync,
        "dispatch_action",
        lambda _db, _user, item: calls.append(item.client_action_id) or {"ok": True},
    )
    batch = offline_sync.SyncBatchIn.model_validate(
        _batch(
            _action(
                "review-missing-comment",
                action="submit_review",
                park_id=seed_park_with_tracker.id,
                dependencies=["missing-comment"],
            )
        )
    )

    result = offline_sync.synchronize(db_session, seed_mechanic, batch, revision_store=Revisions())

    assert calls == []
    assert result.results[0].state == "attention"
    assert result.results[0].code == "dependency_missing"

    other_device = _batch(_action("missing-comment", park_id=seed_park_with_tracker.id))
    other_device["device_id"] = "another-phone"
    offline_sync.synchronize(
        db_session,
        seed_mechanic,
        offline_sync.SyncBatchIn.model_validate(other_device),
        revision_store=Revisions(),
    )
    calls.clear()
    wrong_device = offline_sync.synchronize(
        db_session, seed_mechanic, batch, revision_store=Revisions()
    )
    assert calls == []
    assert wrong_device.results[0].code == "dependency_missing"


def test_confirmed_dependency_from_prior_batch_allows_dispatch_and_replay(
    db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    from robopark_api.services import offline_sync

    class Revisions:
        def mark_changed(self, _scope):
            pass

        def current(self, _scope):
            return 0

    calls = []
    monkeypatch.setattr(
        offline_sync,
        "dispatch_action",
        lambda _db, _user, item: calls.append(item.client_action_id) or {"ok": True},
    )
    comment = offline_sync.SyncBatchIn.model_validate(
        _batch(_action("saved-comment", park_id=seed_park_with_tracker.id))
    )
    review = offline_sync.SyncBatchIn.model_validate(
        _batch(
            _action(
                "review-after-comment",
                action="submit_review",
                park_id=seed_park_with_tracker.id,
                dependencies=["saved-comment"],
            )
        )
    )

    first = offline_sync.synchronize(db_session, seed_mechanic, comment, revision_store=Revisions())
    second = offline_sync.synchronize(db_session, seed_mechanic, review, revision_store=Revisions())
    replay = offline_sync.synchronize(db_session, seed_mechanic, review, revision_store=Revisions())

    assert first.results[0].state == "confirmed"
    assert second.results[0].state == replay.results[0].state == "confirmed"
    assert calls == ["saved-comment", "review-after-comment"]

    from robopark_api.task_workflow_models import OfflineSyncReceipt

    prior_receipt = db_session.scalar(
        select(OfflineSyncReceipt).where(OfflineSyncReceipt.client_action_id == "saved-comment")
    )
    db_session.delete(prior_receipt)
    db_session.commit()
    replay_after_retention = offline_sync.synchronize(
        db_session, seed_mechanic, review, revision_store=Revisions()
    )
    assert replay_after_retention.results[0].state == "confirmed"
    assert calls == ["saved-comment", "review-after-comment"]


def test_review_media_survives_beyond_retention_until_retry_is_acknowledged(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch, tmp_path
):
    from robopark_api.services import media_uploads, offline_sync

    monkeypatch.setattr(offline_sync, "_authorize_review", lambda *_args: {})
    now = time.time()
    row = MediaUploadSession(
        actor_user_id=seed_mechanic.id,
        media_id="durable-review-photo",
        issue_key="ROBOPARK-51",
        original_name="review.jpg",
        mime_type="image/jpeg",
        size_bytes=4,
        sha256="e" * 64,
        received_offset=4,
        blob_name="durable.ready",
        completed=True,
        created_at=now - 10 * 86400,
        updated_at=now - 10 * 86400,
        completed_at=now - 10 * 86400,
        expires_at=now - 9 * 86400,
    )
    db_session.add(row)
    db_session.commit()
    (tmp_path / row.blob_name).write_bytes(b"data")
    monkeypatch.setattr(media_uploads, "uploads_root", lambda: tmp_path)
    monkeypatch.setattr(
        offline_sync,
        "dispatch_action",
        lambda *_args: (_ for _ in ()).throw(HTTPException(503, "tracker_upstream_error")),
    )
    login_as(client, seed_mechanic.username, "secret")
    body = _batch(
        _action(
            "durable-review-action",
            action="submit_review",
            park_id=seed_park_with_tracker.id,
            payload={"media_id": row.media_id, "defect_code": "BD-01"},
        )
    )

    first = client.post("/sync/batch", json=body)

    assert first.json()["results"][0]["state"] == "attention"
    db_session.expire_all()
    bound = db_session.get(MediaUploadSession, row.id)
    assert bound.dependent_device_id == "phone-1"
    assert bound.dependent_action_id == "durable-review-action"
    assert bound.dependency_terminal_at is None
    assert media_uploads.cleanup_expired(db_session, now=now + 8 * 86400) == 0
    assert (tmp_path / row.blob_name).exists()

    monkeypatch.setattr(offline_sync, "dispatch_action", lambda *_args: {"review_id": "done"})
    second = client.post("/sync/batch", json=body)
    assert second.json()["results"][0]["state"] == "confirmed"
    db_session.expire_all()
    terminal_at = db_session.get(MediaUploadSession, row.id).dependency_terminal_at
    assert terminal_at is not None
    assert (
        media_uploads.cleanup_expired(
            db_session, now=terminal_at + media_uploads.COMPLETED_RETENTION_SECONDS + 1
        )
        == 1
    )
    assert not (tmp_path / row.blob_name).exists()


def test_permanently_rejected_review_acknowledges_bound_media(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    from robopark_api.services import offline_sync

    now = time.time()
    row = MediaUploadSession(
        actor_user_id=seed_mechanic.id,
        media_id="rejected-review-photo",
        issue_key="ROBOPARK-51",
        original_name="review.jpg",
        mime_type="image/jpeg",
        size_bytes=4,
        sha256="a" * 64,
        received_offset=4,
        blob_name="rejected.ready",
        completed=True,
        created_at=now,
        updated_at=now,
        completed_at=now,
        expires_at=now + 86400,
        dependent_device_id="phone-1",
        dependent_action_id="rejected-review-action",
        dependency_bound_at=now,
    )
    db_session.add(row)
    db_session.commit()
    monkeypatch.setattr(
        offline_sync,
        "dispatch_action",
        lambda *_args: (_ for _ in ()).throw(HTTPException(403, "tracker_write_disabled")),
    )
    monkeypatch.setattr(offline_sync, "_authorize_review", lambda *_args: {})
    login_as(client, seed_mechanic.username, "secret")

    response = client.post(
        "/sync/batch",
        json=_batch(
            _action(
                "rejected-review-action",
                action="submit_review",
                park_id=seed_park_with_tracker.id,
                payload={"media_id": row.media_id, "defect_code": "BD-01"},
            )
        ),
    )

    assert response.json()["results"][0]["state"] == "rejected"
    db_session.expire_all()
    assert db_session.get(MediaUploadSession, row.id).dependency_terminal_at is not None


def test_park_revocation_rejection_acknowledges_already_bound_media(
    client, db_session, seed_mechanic, seed_park_with_tracker
):
    now = time.time()
    row = MediaUploadSession(
        actor_user_id=seed_mechanic.id,
        media_id="revoked-review-photo",
        issue_key="ROBOPARK-51",
        original_name="review.jpg",
        mime_type="image/jpeg",
        size_bytes=4,
        sha256="b" * 64,
        received_offset=4,
        blob_name="revoked.ready",
        completed=True,
        created_at=now,
        updated_at=now,
        completed_at=now,
        expires_at=now + 86400,
        dependent_device_id="phone-1",
        dependent_action_id="revoked-review-action",
        dependency_bound_at=now,
    )
    db_session.add(row)
    db_session.commit()
    login_as(client, seed_mechanic.username, "secret")
    membership = db_session.scalar(
        select(UserPark).where(
            UserPark.user_id == seed_mechanic.id,
            UserPark.park_id == seed_park_with_tracker.id,
        )
    )
    db_session.delete(membership)
    db_session.commit()

    response = client.post(
        "/sync/batch",
        json=_batch(
            _action(
                "revoked-review-action",
                action="submit_review",
                park_id=seed_park_with_tracker.id,
                payload={"media_id": row.media_id, "defect_code": "BD-01"},
            )
        ),
    )

    assert response.json()["results"][0] == {
        "client_action_id": "revoked-review-action",
        "state": "rejected",
        "code": "park_forbidden",
        "result": None,
    }
    db_session.expire_all()
    assert db_session.get(MediaUploadSession, row.id).dependency_terminal_at is not None


def test_review_rechecks_attach_permission_after_media_completed(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    from robopark_api.services import (
        platform_settings,
        rbac,
        schedules,
        tracker_cache,
    )

    now = time.time()
    row = MediaUploadSession(
        actor_user_id=seed_mechanic.id,
        park_id=seed_park_with_tracker.id,
        media_id="revoked-attach-photo",
        issue_key="ROBOPARK-51",
        original_name="review.jpg",
        mime_type="image/jpeg",
        size_bytes=4,
        sha256="c" * 64,
        received_offset=4,
        blob_name="revoked-attach.ready",
        completed=True,
        created_at=now,
        updated_at=now,
        completed_at=now,
        expires_at=now + 86400,
        dependent_device_id="phone-1",
        dependent_action_id="revoked-attach-action",
        dependency_bound_at=now,
    )
    db_session.add(row)
    db_session.commit()
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    monkeypatch.setattr(
        tracker_cache,
        "get_issue",
        lambda **kwargs: {
            "key": kwargs["key"],
            "queue": seed_park_with_tracker.tracker_queue,
            "tags": [seed_park_with_tracker.tag],
            "status": "In progress",
            "status_key": "in_progress",
        },
    )
    permissions = rbac.permissions_for_user(db_session, seed_mechanic)
    rbac.set_user_effective_permissions(
        db_session,
        seed_mechanic,
        sorted(permissions - {rbac.PERMISSION_TRACKER_ATTACH}),
    )
    db_session.commit()
    monkeypatch.setattr(
        schedules,
        "resolve_active_operator",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("permission must be checked before media dispatch")
        ),
    )
    login_as(client, seed_mechanic.username, "secret")

    response = client.post(
        "/sync/batch",
        json=_batch(
            _action(
                "revoked-attach-action",
                action="submit_review",
                park_id=seed_park_with_tracker.id,
                payload={"media_id": row.media_id, "defect_code": "BD-01"},
            )
        ),
    )

    assert response.json()["results"][0] == {
        "client_action_id": "revoked-attach-action",
        "state": "rejected",
        "code": "tracker_attach_disabled",
        "result": None,
    }
    db_session.expire_all()
    assert db_session.get(MediaUploadSession, row.id).dependency_terminal_at is not None


def test_review_media_conflict_is_nonterminal_and_survives_until_resolved(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch, tmp_path
):
    from robopark_api.services import media_uploads, offline_sync

    monkeypatch.setattr(offline_sync, "_authorize_review", lambda *_args: {})
    now = time.time()
    row = MediaUploadSession(
        actor_user_id=seed_mechanic.id,
        media_id="conflicting-review-photo",
        issue_key="ROBOPARK-51",
        original_name="review.jpg",
        mime_type="image/jpeg",
        size_bytes=4,
        sha256="f" * 64,
        received_offset=4,
        blob_name="conflict.ready",
        completed=True,
        created_at=now,
        updated_at=now,
        completed_at=now,
        expires_at=now + 86400,
        dependent_device_id="phone-1",
        dependent_action_id="conflicting-review-action",
        dependency_bound_at=now,
    )
    db_session.add(row)
    db_session.commit()
    (tmp_path / row.blob_name).write_bytes(b"data")
    monkeypatch.setattr(media_uploads, "uploads_root", lambda: tmp_path)
    monkeypatch.setattr(
        offline_sync,
        "dispatch_action",
        lambda *_args: (_ for _ in ()).throw(HTTPException(409, "review_revision_conflict")),
    )
    login_as(client, seed_mechanic.username, "secret")
    body = _batch(
        _action(
            "conflicting-review-action",
            action="submit_review",
            park_id=seed_park_with_tracker.id,
            payload={"media_id": row.media_id, "defect_code": "BD-01"},
        )
    )

    first = client.post("/sync/batch", json=body)

    assert first.json()["results"][0]["state"] == "conflict"
    db_session.expire_all()
    assert db_session.get(MediaUploadSession, row.id).dependency_terminal_at is None
    assert media_uploads.cleanup_expired(db_session, now=now + 8 * 86400) == 0
    assert (tmp_path / row.blob_name).exists()

    monkeypatch.setattr(offline_sync, "dispatch_action", lambda *_args: {"review_id": "done"})
    second = client.post("/sync/batch", json=body)
    assert second.json()["results"][0]["state"] == "confirmed"
    db_session.expire_all()
    terminal_at = db_session.get(MediaUploadSession, row.id).dependency_terminal_at
    assert terminal_at is not None


def test_missing_completed_review_media_reopens_and_replays_same_action_once(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch, tmp_path
):
    from robopark_api.services import media_uploads, offline_sync

    monkeypatch.setattr(offline_sync, "_authorize_review", lambda *_args: {})
    content = b"\xff\xd8\xffrecovered-review"
    now = time.time()
    row = MediaUploadSession(
        actor_user_id=seed_mechanic.id,
        park_id=seed_park_with_tracker.id,
        media_id="recovered-review-photo",
        issue_key="ROBOPARK-51",
        original_name="review.jpg",
        mime_type="image/jpeg",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        received_offset=len(content),
        blob_name="lost.ready",
        completed=True,
        created_at=now,
        updated_at=now,
        completed_at=now,
        expires_at=now + 86400,
        dependent_device_id="phone-1",
        dependent_action_id="recovered-review-action",
        dependency_bound_at=now,
    )
    db_session.add(row)
    db_session.commit()
    monkeypatch.setattr(media_uploads, "uploads_root", lambda: tmp_path)
    applied = 0

    def dispatch(db, user, item):
        nonlocal applied
        upload = db.scalar(
            select(MediaUploadSession).where(
                MediaUploadSession.actor_user_id == user.id,
                MediaUploadSession.media_id == item.payload["media_id"],
            )
        )
        media_uploads.content_path(upload)
        applied += 1
        return {"review_id": "applied-once"}

    monkeypatch.setattr(offline_sync, "dispatch_action", dispatch)
    login_as(client, seed_mechanic.username, "secret")
    body = _batch(
        _action(
            "recovered-review-action",
            action="submit_review",
            park_id=seed_park_with_tracker.id,
            payload={"media_id": row.media_id, "defect_code": "BD-01"},
        )
    )

    missing = client.post("/sync/batch", json=body)
    reopened = client.post(
        "/media/uploads",
        json={
            "media_id": row.media_id,
            "issue_key": row.issue_key,
            "dependent_action_id": row.dependent_action_id,
            "device_id": row.dependent_device_id,
            "name": row.original_name,
            "mime_type": row.mime_type,
            "size_bytes": row.size_bytes,
            "sha256": row.sha256,
        },
    )

    assert missing.json()["results"][0]["code"] == "media_upload_missing"
    assert reopened.json()["status"] == "reinitialized"
    assert reopened.json()["upload_id"] == row.id
    uploaded = client.put(
        f"/media/uploads/{row.id}/chunks/0",
        content=content,
        headers={"X-Chunk-SHA256": hashlib.sha256(content).hexdigest()},
    )
    completed = client.post(f"/media/uploads/{row.id}/complete")
    assert uploaded.status_code == completed.status_code == 200

    applied_response = client.post("/sync/batch", json=body)
    receipt_replay = client.post("/sync/batch", json=body)
    assert applied_response.json()["results"][0]["state"] == "confirmed"
    assert receipt_replay.json() == applied_response.json()
    assert applied == 1


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
