import asyncio
import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from conftest import login_as, role_id_for
from robopark_api.collaboration_models import TrackerClaim
from robopark_api.models import AccessStatus, Park, User, UserPark
from robopark_api.routers import media_uploads as media_uploads_router
from robopark_api.security import hash_password
from robopark_api.services import media_uploads, platform_settings, rbac, tracker_cache
from robopark_api.task_workflow_models import MediaUploadSession


def _start(
    client,
    content: bytes,
    *,
    media_id="media-12345678",
    issue_key="ROBOPARK-51",
    dependent_action_id=None,
    device_id=None,
):
    payload = {
        "media_id": media_id,
        "issue_key": issue_key,
        "name": "robot.jpg",
        "mime_type": "image/jpeg",
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    if dependent_action_id is not None:
        payload["dependent_action_id"] = dependent_action_id
    if device_id is not None:
        payload["device_id"] = device_id
    return client.post(
        "/media/uploads",
        json=payload,
    )


@pytest.fixture(autouse=True)
def configured_upload_issue(db_session, monkeypatch):
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    monkeypatch.setattr(
        tracker_cache,
        "get_issue",
        lambda **kwargs: {
            "key": kwargs["key"],
            "queue": "ROBOPARK",
            "tags": ["Alpha"],
            "status": "Open",
            "status_key": "open",
        },
    )


def _service_payload(media_id: str, size_bytes: int = 1) -> media_uploads.MediaUploadCreateIn:
    return media_uploads.MediaUploadCreateIn(
        media_id=media_id,
        issue_key="ROBOPARK-51",
        name="robot.jpg",
        mime_type="image/jpeg",
        size_bytes=size_bytes,
        sha256=hashlib.sha256(b"x" * size_bytes).hexdigest(),
    )


def test_upload_start_requires_attach_permission(client, db_session, seed_park_with_tracker):
    driver = User(
        username="media-driver",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "driver"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(driver)
    db_session.flush()
    db_session.add(UserPark(user_id=driver.id, park_id=seed_park_with_tracker.id))
    db_session.commit()
    login_as(client, driver.username, "secret")

    response = _start(client, b"\xff\xd8\xffdriver", media_id="driver-media-1234")

    assert response.status_code == 403
    assert response.json()["detail"] == "tracker_attach_disabled"
    assert db_session.scalar(select(func.count()).select_from(MediaUploadSession)) == 0


def test_upload_start_rejects_issue_outside_assigned_parks(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    restricted = User(
        username="media-restricted",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "mechanic"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(restricted)
    db_session.flush()
    db_session.add(UserPark(user_id=restricted.id, park_id=seed_park_with_tracker.id))
    rbac.set_user_effective_permissions(
        db_session,
        restricted,
        [rbac.PERMISSION_TRACKER_READ, rbac.PERMISSION_TRACKER_ATTACH],
    )
    db_session.add(
        Park(
            name="Beta",
            tag="Beta",
            is_active=True,
            tracker_queue="ROBOPARK",
            feature_blockers=True,
        )
    )
    db_session.commit()
    monkeypatch.setattr(
        tracker_cache,
        "get_issue",
        lambda **kwargs: {
            "key": kwargs["key"],
            "queue": "ROBOPARK",
            "tags": ["Beta"],
            "status": "Open",
            "status_key": "open",
        },
    )
    login_as(client, restricted.username, "secret")

    response = _start(client, b"\xff\xd8\xffforeign", media_id="foreign-media-1234")

    assert response.status_code == 403
    assert response.json()["detail"] == "tracker_issue_out_of_scope"
    assert db_session.scalar(select(func.count()).select_from(MediaUploadSession)) == 0


def test_upload_start_rejects_ambiguous_casefolded_park_tag(client, db_session, seed_mechanic):
    db_session.add(
        Park(
            name="Ambiguous Alpha",
            tag="alpha",
            is_active=True,
            tracker_queue="ROBOPARK",
            feature_blockers=True,
        )
    )
    db_session.commit()
    login_as(client, seed_mechanic.username, "secret")

    response = _start(client, b"\xff\xd8\xffambiguous", media_id="ambiguous-media-1")

    assert response.status_code == 409
    assert response.json()["detail"] == "tracker_issue_park_required"
    assert db_session.scalar(select(func.count()).select_from(MediaUploadSession)) == 0


def test_upload_operations_recheck_current_account_permission_and_park_scope(
    client, db_session, seed_mechanic, seed_park_with_tracker
):
    content = b"\xff\xd8\xffrevocation"
    login_as(client, seed_mechanic.username, "secret")
    started = _start(client, content, media_id="revocation-media-1")
    assert started.status_code == 201
    upload_id = started.json()["upload_id"]
    row = db_session.get(MediaUploadSession, upload_id)
    assert row.park_id == seed_park_with_tracker.id

    seed_mechanic.access_status = AccessStatus.pending.value
    db_session.commit()
    assert _start(client, content, media_id="revocation-media-1").status_code == 403

    seed_mechanic.access_status = AccessStatus.approved.value
    rbac.set_user_effective_permissions(
        db_session,
        seed_mechanic,
        [rbac.PERMISSION_TRACKER_READ],
    )
    db_session.commit()
    chunk = client.put(
        f"/media/uploads/{upload_id}/chunks/0",
        content=content,
        headers={"X-Chunk-SHA256": hashlib.sha256(content).hexdigest()},
    )
    assert chunk.status_code == 403
    assert chunk.json()["detail"] == "tracker_attach_disabled"

    rbac.set_user_effective_permissions(
        db_session,
        seed_mechanic,
        [rbac.PERMISSION_TRACKER_READ, rbac.PERMISSION_TRACKER_ATTACH],
    )
    db_session.commit()
    assert (
        client.put(
            f"/media/uploads/{upload_id}/chunks/0",
            content=content,
            headers={"X-Chunk-SHA256": hashlib.sha256(content).hexdigest()},
        ).status_code
        == 200
    )
    membership = db_session.scalar(
        select(UserPark).where(
            UserPark.user_id == seed_mechanic.id,
            UserPark.park_id == seed_park_with_tracker.id,
        )
    )
    db_session.delete(membership)
    db_session.commit()
    completed = client.post(f"/media/uploads/{upload_id}/complete")
    assert completed.status_code == 403
    assert completed.json()["detail"] == "tracker_issue_out_of_scope"


def test_legacy_upload_backfills_park_from_canonical_local_claim(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    content = b"\xff\xd8\xfflegacy-claim"
    now = time.time()
    row = MediaUploadSession(
        actor_user_id=seed_mechanic.id,
        park_id=None,
        media_id="legacy-claim-media",
        issue_key="ROBOPARK-51",
        original_name="robot.jpg",
        mime_type="image/jpeg",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        received_offset=0,
        blob_name="legacy-claim.part",
        completed=False,
        created_at=now,
        updated_at=now,
        expires_at=now + 86400,
    )
    db_session.add_all(
        [
            row,
            TrackerClaim(
                issue_key=row.issue_key,
                park_id=seed_park_with_tracker.id,
                owner_user_id=seed_mechanic.id,
                updated_by_user_id=seed_mechanic.id,
                state="active",
                updated_at=now,
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setattr(
        tracker_cache,
        "get_issue",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected Tracker request")),
    )
    login_as(client, seed_mechanic.username, "secret")

    replay = _start(client, content, media_id=row.media_id)

    assert replay.status_code == 201
    db_session.refresh(row)
    assert row.park_id == seed_park_with_tracker.id


def test_legacy_replay_cannot_rebind_scope_with_a_different_issue(
    client, db_session, seed_mechanic, monkeypatch
):
    content = b"\xff\xd8\xfflegacy-scope"
    now = time.time()
    row = MediaUploadSession(
        actor_user_id=seed_mechanic.id,
        park_id=None,
        media_id="legacy-scope-media",
        issue_key="ROBOPARK-51",
        original_name="robot.jpg",
        mime_type="image/jpeg",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        received_offset=0,
        blob_name="legacy-scope.part",
        completed=False,
        created_at=now,
        updated_at=now,
        expires_at=now + 86400,
    )
    db_session.add(row)
    db_session.commit()
    monkeypatch.setattr(
        tracker_cache,
        "get_issue",
        lambda **kwargs: {
            "key": kwargs["key"],
            "queue": "ROBOPARK",
            "tags": ["Alpha"],
            "status": "Open",
            "status_key": "open",
        },
    )
    login_as(client, seed_mechanic.username, "secret")

    replay = _start(
        client,
        content,
        media_id=row.media_id,
        issue_key="ROBOPARK-99",
    )

    assert replay.status_code == 409
    assert replay.json()["detail"] == "media_upload_payload_conflict"
    db_session.refresh(row)
    assert row.park_id is None


def test_legacy_completed_upload_survives_temporary_scope_resolution_failure(
    client, db_session, seed_mechanic, tmp_path, monkeypatch
):
    content = b"\xff\xd8\xfflegacy-ready"
    now = time.time()
    row = MediaUploadSession(
        actor_user_id=seed_mechanic.id,
        park_id=None,
        media_id="legacy-ready-media",
        issue_key="ROBOPARK-51",
        original_name="robot.jpg",
        mime_type="image/jpeg",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        received_offset=len(content),
        blob_name="legacy-ready.ready",
        completed=True,
        created_at=now,
        updated_at=now,
        expires_at=now + 86400,
        completed_at=now,
    )
    db_session.add(row)
    db_session.commit()
    path = tmp_path / row.blob_name
    path.write_bytes(content)
    monkeypatch.setattr(media_uploads, "uploads_root", lambda: tmp_path)
    monkeypatch.setattr(
        tracker_cache,
        "get_issue",
        lambda **_kwargs: (_ for _ in ()).throw(
            media_uploads.tracker_client.TrackerError("temporary")
        ),
    )
    login_as(client, seed_mechanic.username, "secret")

    replay = _start(client, content, media_id=row.media_id)

    assert replay.status_code == 502
    db_session.refresh(row)
    assert row.park_id is None
    assert row.completed is True
    assert path.read_bytes() == content


def test_legacy_chunk_without_local_claim_requires_scope_refresh(
    client, db_session, seed_mechanic, tmp_path, monkeypatch
):
    content = b"\xff\xd8\xfflegacy-part"
    now = time.time()
    row = MediaUploadSession(
        actor_user_id=seed_mechanic.id,
        park_id=None,
        media_id="legacy-direct-chunk",
        issue_key="ROBOPARK-51",
        original_name="robot.jpg",
        mime_type="image/jpeg",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        received_offset=0,
        blob_name="legacy-direct.part",
        completed=False,
        created_at=now,
        updated_at=now,
        expires_at=now + 86400,
    )
    db_session.add(row)
    db_session.commit()
    monkeypatch.setattr(media_uploads, "uploads_root", lambda: tmp_path)
    login_as(client, seed_mechanic.username, "secret")

    response = client.put(
        f"/media/uploads/{row.id}/chunks/0",
        content=content,
        headers={"X-Chunk-SHA256": hashlib.sha256(content).hexdigest()},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "media_upload_scope_refresh_required"
    assert not (tmp_path / row.blob_name).exists()


def test_concurrent_upload_starts_reserve_user_session_quota_atomically(
    db_engine, seed_mechanic, tmp_path, monkeypatch
):
    monkeypatch.setattr(media_uploads, "uploads_root", lambda: tmp_path)
    monkeypatch.setattr(media_uploads, "MAX_UNCONSUMED_SESSIONS_PER_USER", 1)
    start = threading.Barrier(3)

    def reserve(media_id: str):
        start.wait(timeout=1)
        with Session(db_engine) as db:
            user = db.get(User, seed_mechanic.id)
            try:
                return media_uploads.start(db, user, _service_payload(media_id)).status
            except HTTPException as exc:
                return exc.status_code, exc.detail

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(reserve, "quota-race-media-1")
        second = executor.submit(reserve, "quota-race-media-2")
        start.wait(timeout=1)
        results = [first.result(timeout=3), second.result(timeout=3)]

    assert sorted(results, key=str) == sorted(
        ["active", (429, "media_upload_user_quota_exceeded")], key=str
    )
    with Session(db_engine) as db:
        assert db.scalar(select(func.count()).select_from(MediaUploadSession)) == 1


def test_concurrent_users_reserve_global_session_quota_atomically(
    db_engine, seed_mechanic, seed_admin, tmp_path, monkeypatch
):
    monkeypatch.setattr(media_uploads, "uploads_root", lambda: tmp_path)
    monkeypatch.setattr(media_uploads, "MAX_UNCONSUMED_SESSIONS_GLOBAL", 1)
    start = threading.Barrier(3)

    def reserve(user_id: int, media_id: str):
        start.wait(timeout=1)
        with Session(db_engine) as db:
            user = db.get(User, user_id)
            try:
                return media_uploads.start(db, user, _service_payload(media_id)).status
            except HTTPException as exc:
                return exc.status_code, exc.detail

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(reserve, seed_mechanic.id, "global-race-media-1")
        second = executor.submit(reserve, seed_admin.id, "global-race-media-2")
        start.wait(timeout=1)
        results = [first.result(timeout=3), second.result(timeout=3)]

    assert sorted(results, key=str) == sorted(
        ["active", (429, "media_upload_global_quota_exceeded")], key=str
    )
    with Session(db_engine) as db:
        assert db.scalar(select(func.count()).select_from(MediaUploadSession)) == 1


def test_retained_completed_upload_still_consumes_quota(
    db_session, seed_mechanic, tmp_path, monkeypatch
):
    monkeypatch.setattr(media_uploads, "uploads_root", lambda: tmp_path)
    monkeypatch.setattr(media_uploads, "MAX_UNCONSUMED_SESSIONS_PER_USER", 1)
    content = b"\xff\xd8\xffretained"
    first_payload = media_uploads.MediaUploadCreateIn(
        media_id="retained-media-1",
        issue_key="ROBOPARK-51",
        name="robot.jpg",
        mime_type="image/jpeg",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    first = media_uploads.start(db_session, seed_mechanic, first_payload)
    media_uploads.append_chunk(
        db_session,
        seed_mechanic,
        first.row.id,
        0,
        content,
        hashlib.sha256(content).hexdigest(),
    )
    media_uploads.complete(db_session, seed_mechanic, first.row.id)

    replay = media_uploads.start(db_session, seed_mechanic, first_payload)
    assert replay.status == "completed"
    with pytest.raises(HTTPException) as caught:
        media_uploads.start(db_session, seed_mechanic, _service_payload("retained-media-2"))
    assert (caught.value.status_code, caught.value.detail) == (
        429,
        "media_upload_user_quota_exceeded",
    )
    assert caught.value.headers == {"Retry-After": str(media_uploads.QUOTA_RETRY_AFTER_SECONDS)}


def test_consumed_upload_releases_session_slot_but_remains_in_byte_usage(
    db_session, seed_mechanic, tmp_path, monkeypatch
):
    monkeypatch.setattr(media_uploads, "uploads_root", lambda: tmp_path)
    monkeypatch.setattr(media_uploads, "MAX_UNCONSUMED_SESSIONS_PER_USER", 1)
    content = b"\xff\xd8\xffconsumed"
    first_payload = media_uploads.MediaUploadCreateIn(
        media_id="consumed-media-1",
        issue_key="ROBOPARK-51",
        name="robot.jpg",
        mime_type="image/jpeg",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    first = media_uploads.start(db_session, seed_mechanic, first_payload)
    media_uploads.append_chunk(
        db_session,
        seed_mechanic,
        first.row.id,
        0,
        content,
        hashlib.sha256(content).hexdigest(),
    )
    media_uploads.complete(db_session, seed_mechanic, first.row.id)
    first.row.dependency_terminal_at = time.time()
    db_session.commit()

    assert media_uploads._retained_usage(db_session, actor_user_id=seed_mechanic.id) == (
        0,
        len(content),
    )
    assert (
        media_uploads.start(
            db_session,
            seed_mechanic,
            _service_payload("consumed-media-2"),
        ).status
        == "active"
    )


def test_expired_incomplete_reservation_restarts_same_media_without_leaking_partial_file(
    db_session, seed_mechanic, tmp_path, monkeypatch
):
    monkeypatch.setattr(media_uploads, "uploads_root", lambda: tmp_path)
    payload = _service_payload("expired-media-1234", 4)
    now = time.time()
    row = MediaUploadSession(
        actor_user_id=seed_mechanic.id,
        media_id=payload.media_id,
        issue_key=payload.issue_key,
        original_name=payload.name,
        mime_type=payload.mime_type,
        size_bytes=payload.size_bytes,
        sha256=payload.sha256,
        received_offset=2,
        blob_name="expired.part",
        completed=False,
        created_at=now - media_uploads.SESSION_TTL_SECONDS - 10,
        updated_at=now - 10,
        expires_at=now - 1,
    )
    db_session.add(row)
    db_session.commit()
    old_path = tmp_path / row.blob_name
    old_path.write_bytes(b"xx")

    restarted = media_uploads.start(db_session, seed_mechanic, payload)

    assert restarted.status == "reinitialized"
    assert restarted.row.id == row.id
    assert restarted.row.received_offset == 0
    assert restarted.row.expires_at - restarted.row.updated_at == media_uploads.SESSION_TTL_SECONDS
    assert restarted.row.blob_name != "expired.part"
    assert not old_path.exists()


def test_retained_upload_byte_quotas_apply_per_user_and_globally(
    db_session, seed_mechanic, seed_admin, tmp_path, monkeypatch
):
    monkeypatch.setattr(media_uploads, "uploads_root", lambda: tmp_path)
    monkeypatch.setattr(media_uploads, "MAX_RETAINED_BYTES_PER_USER", 5)
    monkeypatch.setattr(media_uploads, "MAX_RETAINED_BYTES_GLOBAL", 8)
    media_uploads.start(db_session, seed_mechanic, _service_payload("byte-quota-media-1", 5))

    with pytest.raises(HTTPException) as per_user:
        media_uploads.start(db_session, seed_mechanic, _service_payload("byte-quota-media-2", 1))
    assert (per_user.value.status_code, per_user.value.detail) == (
        429,
        "media_upload_user_quota_exceeded",
    )

    with pytest.raises(HTTPException) as global_quota:
        media_uploads.start(db_session, seed_admin, _service_payload("byte-quota-media-3", 4))
    assert (global_quota.value.status_code, global_quota.value.detail) == (
        429,
        "media_upload_global_quota_exceeded",
    )


def test_upload_reservation_preserves_disk_headroom(
    db_session, seed_mechanic, tmp_path, monkeypatch
):
    class Filesystem:
        f_bavail = media_uploads.MIN_FREE_BYTES_AFTER_RESERVATION
        f_frsize = 1

    monkeypatch.setattr(media_uploads, "uploads_root", lambda: tmp_path)
    monkeypatch.setattr(media_uploads.os, "statvfs", lambda _path: Filesystem())

    with pytest.raises(HTTPException) as caught:
        media_uploads.start(db_session, seed_mechanic, _service_payload("disk-headroom-1"))

    assert (caught.value.status_code, caught.value.detail) == (
        507,
        "media_storage_capacity_exceeded",
    )
    assert db_session.scalar(select(func.count()).select_from(MediaUploadSession)) == 0


def test_disk_headroom_includes_unwritten_bytes_from_existing_reservations(
    db_session, seed_mechanic, tmp_path, monkeypatch
):
    class Filesystem:
        f_bavail = media_uploads.MIN_FREE_BYTES_AFTER_RESERVATION + 5
        f_frsize = 1

    monkeypatch.setattr(media_uploads, "uploads_root", lambda: tmp_path)
    media_uploads.start(
        db_session,
        seed_mechanic,
        _service_payload("reserved-disk-headroom-1", 5),
    )
    monkeypatch.setattr(media_uploads.os, "statvfs", lambda _path: Filesystem())

    with pytest.raises(HTTPException) as caught:
        media_uploads.start(
            db_session,
            seed_mechanic,
            _service_payload("reserved-disk-headroom-2"),
        )

    assert (caught.value.status_code, caught.value.detail) == (
        507,
        "media_storage_capacity_exceeded",
    )


def test_upload_schema_rejects_more_than_fifteen_mebibytes(client, seed_mechanic):
    login_as(client, seed_mechanic.username, "secret")
    response = client.post(
        "/media/uploads",
        json={
            "media_id": "oversized-media-1",
            "issue_key": "ROBOPARK-51",
            "name": "robot.jpg",
            "mime_type": "image/jpeg",
            "size_bytes": 15 * 1024 * 1024 + 1,
            "sha256": "a" * 64,
        },
    )

    assert response.status_code == 422


def test_resumable_upload_validates_offsets_checksums_and_replays(
    client, seed_mechanic, test_settings
):
    content = b"\xff\xd8\xff" + b"robot-photo"
    login_as(client, seed_mechanic.username, "secret")
    started = _start(client, content)
    assert started.status_code == 201
    upload_id = started.json()["upload_id"]

    first = content[:5]
    headers = {
        "X-Chunk-SHA256": hashlib.sha256(first).hexdigest(),
        "Content-Type": "application/octet-stream",
    }
    sent = client.put(f"/media/uploads/{upload_id}/chunks/0", content=first, headers=headers)
    replay = client.put(f"/media/uploads/{upload_id}/chunks/0", content=first, headers=headers)
    assert sent.status_code == replay.status_code == 200
    assert sent.json()["received_offset"] == replay.json()["received_offset"] == len(first)

    wrong_offset = client.put(
        f"/media/uploads/{upload_id}/chunks/1",
        content=b"x",
        headers={"X-Chunk-SHA256": hashlib.sha256(b"x").hexdigest()},
    )
    wrong_hash = client.put(
        f"/media/uploads/{upload_id}/chunks/{len(first)}",
        content=b"x",
        headers={"X-Chunk-SHA256": "0" * 64},
    )
    assert wrong_offset.status_code == 409
    assert wrong_hash.status_code == 400

    rest = content[len(first) :]
    sent_rest = client.put(
        f"/media/uploads/{upload_id}/chunks/{len(first)}",
        content=rest,
        headers={"X-Chunk-SHA256": hashlib.sha256(rest).hexdigest()},
    )
    assert sent_rest.status_code == 200
    completed = client.post(f"/media/uploads/{upload_id}/complete")
    repeated = client.post(f"/media/uploads/{upload_id}/complete")
    assert completed.status_code == repeated.status_code == 200
    assert completed.json() == repeated.json()
    assert completed.json() == {
        "upload_id": upload_id,
        "media_id": "media-12345678",
        "completed": True,
    }


def test_chunk_upload_stops_reading_once_stream_exceeds_limit(
    db_session, seed_mechanic, monkeypatch
):
    chunks = [
        b"a" * (media_uploads.MAX_CHUNK_BYTES // 2 + 1),
        b"b" * (media_uploads.MAX_CHUNK_BYTES // 2),
        b"must-not-be-read",
    ]
    reads = 0
    events = []

    async def receive():
        nonlocal reads
        chunk = chunks[reads]
        reads += 1
        events.append(f"read-{reads}")
        return {"type": "http.request", "body": chunk, "more_body": reads < len(chunks)}

    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "PUT",
            "scheme": "http",
            "path": "/media/uploads/upload-1/chunks/0",
            "raw_path": b"/media/uploads/upload-1/chunks/0",
            "query_string": b"",
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        },
        receive,
    )

    monkeypatch.setattr(
        media_uploads,
        "authorize_chunk",
        lambda *_args: events.append("authorized"),
        raising=False,
    )

    def append_chunk(*_args):
        pytest.fail("oversized content reached append_chunk")

    monkeypatch.setattr(media_uploads, "append_chunk", append_chunk)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            media_uploads_router.put_chunk(
                "upload-1",
                0,
                request,
                "a" * 64,
                seed_mechanic,
                db_session,
            )
        )

    assert (caught.value.status_code, caught.value.detail) == (
        400,
        "media_chunk_size_invalid",
    )
    assert reads == 2
    assert events == ["authorized", "read-1", "read-2"]


def test_upload_creation_persists_exact_action_dependency_before_content(
    client, db_session, seed_mechanic
):
    login_as(client, seed_mechanic.username, "secret")
    started = _start(
        client,
        b"\xff\xd8\xffphoto",
        media_id="media-bound-1234",
        dependent_action_id="review-action-1234",
        device_id="account-42",
    )

    assert started.status_code == 201
    row = db_session.scalar(
        select(MediaUploadSession).where(MediaUploadSession.media_id == "media-bound-1234")
    )
    assert row.dependent_action_id == "review-action-1234"
    assert row.dependent_device_id == "account-42"
    assert row.dependency_bound_at is not None
    assert row.expires_at - row.created_at == media_uploads.SESSION_TTL_SECONDS

    replay = _start(
        client,
        b"\xff\xd8\xffphoto",
        media_id="media-bound-1234",
        dependent_action_id="review-action-1234",
        device_id="account-42",
    )
    conflict = _start(
        client,
        b"\xff\xd8\xffphoto",
        media_id="media-bound-1234",
        dependent_action_id="other-action-1234",
        device_id="account-42",
    )
    payload_conflict = _start(
        client,
        b"\xff\xd8\xffdifferent-photo",
        media_id="media-bound-1234",
        dependent_action_id="review-action-1234",
        device_id="account-42",
    )
    device_conflict = _start(
        client,
        b"\xff\xd8\xffphoto",
        media_id="media-bound-1234",
        dependent_action_id="review-action-1234",
        device_id="account-99",
    )
    assert replay.status_code == 201
    assert replay.json()["upload_id"] == started.json()["upload_id"]
    assert conflict.status_code == 409
    assert payload_conflict.status_code == 409
    assert device_conflict.status_code == 409

    incomplete = _start(
        client,
        b"\xff\xd8\xffother",
        media_id="media-bound-5678",
        dependent_action_id="review-action-5678",
    )
    unsafe = _start(
        client,
        b"\xff\xd8\xffother",
        media_id="media-bound-9012",
        dependent_action_id="review action 9012",
        device_id="account-42",
    )
    assert incomplete.status_code == 422
    assert unsafe.status_code == 422


def test_completed_missing_blob_reopens_same_upload_only_for_exact_identity(
    client, db_session, seed_mechanic, tmp_path, monkeypatch
):
    monkeypatch.setattr(media_uploads, "uploads_root", lambda: tmp_path)
    content = b"\xff\xd8\xffmissing-ready"
    now = time.time()
    row = MediaUploadSession(
        actor_user_id=seed_mechanic.id,
        media_id="media-reopen-1234",
        issue_key="ROBOPARK-51",
        original_name="robot.jpg",
        mime_type="image/jpeg",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        received_offset=len(content),
        blob_name="missing.ready",
        completed=True,
        created_at=now - 100,
        updated_at=now - 100,
        completed_at=now - 100,
        expires_at=now + 86400,
        dependent_device_id="account-42",
        dependent_action_id="review-action-reopen",
        dependency_bound_at=now - 100,
    )
    db_session.add(row)
    db_session.commit()
    login_as(client, seed_mechanic.username, "secret")

    mismatch = _start(
        client,
        content,
        media_id=row.media_id,
        dependent_action_id="different-action",
        device_id="account-42",
    )
    reopened = _start(
        client,
        content,
        media_id=row.media_id,
        dependent_action_id=row.dependent_action_id,
        device_id=row.dependent_device_id,
    )

    assert mismatch.status_code == 409
    assert reopened.status_code == 201
    assert reopened.json() == {
        "upload_id": row.id,
        "received_offset": 0,
        "completed": False,
        "media_id": None,
        "status": "reinitialized",
    }
    db_session.refresh(row)
    assert row.completed is False
    assert row.completed_at is None
    assert row.received_offset == 0
    assert row.blob_name.endswith(".part")
    assert row.dependent_action_id == "review-action-reopen"


def test_intact_completed_upload_remains_idempotent_and_is_not_reinitialized(
    client, db_session, seed_mechanic, tmp_path, monkeypatch
):
    monkeypatch.setattr(media_uploads, "uploads_root", lambda: tmp_path)
    content = b"\xff\xd8\xffintact-ready"
    now = time.time()
    row = MediaUploadSession(
        actor_user_id=seed_mechanic.id,
        media_id="media-intact-1234",
        issue_key="ROBOPARK-51",
        original_name="robot.jpg",
        mime_type="image/jpeg",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        received_offset=len(content),
        blob_name="intact.ready",
        completed=True,
        created_at=now - 100,
        updated_at=now - 100,
        completed_at=now - 100,
        expires_at=now + 86400,
        dependent_device_id="account-42",
        dependent_action_id="review-action-intact",
        dependency_bound_at=now - 100,
    )
    db_session.add(row)
    db_session.commit()
    (tmp_path / row.blob_name).write_bytes(content)
    login_as(client, seed_mechanic.username, "secret")

    replay = _start(
        client,
        content,
        media_id=row.media_id,
        dependent_action_id=row.dependent_action_id,
        device_id=row.dependent_device_id,
    )

    assert replay.status_code == 201
    assert replay.json()["status"] == "completed"
    assert replay.json()["completed"] is True
    assert replay.json()["upload_id"] == row.id
    assert (tmp_path / row.blob_name).read_bytes() == content


def test_sqlite_concurrent_media_completion_returns_same_completed_upload(
    db_engine, seed_mechanic, tmp_path, monkeypatch
):
    """Separate SQLite sessions serialize the staged-file rename."""
    monkeypatch.setattr(media_uploads, "uploads_root", lambda: tmp_path)
    content = b"\xff\xd8\xffsqlite-concurrent"
    with Session(db_engine) as db:
        user = db.get(User, seed_mechanic.id)
        upload = media_uploads.start(
            db,
            user,
            media_uploads.MediaUploadCreateIn(
                media_id="sqlite-concurrent-media",
                issue_key="ROBOPARK-51",
                name="robot.jpg",
                mime_type="image/jpeg",
                size_bytes=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
            ),
        )
        media_uploads.append_chunk(
            db, user, upload.row.id, 0, content, hashlib.sha256(content).hexdigest()
        )
        upload_id = upload.row.id

    original_replace = Path.replace
    replace_entered = threading.Event()
    allow_replace = threading.Event()
    start = threading.Barrier(3)

    def hold_replace(path: Path, target: Path):
        if path.suffix == ".part":
            replace_entered.set()
            assert allow_replace.wait(timeout=1)
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", hold_replace)

    def complete_once():
        start.wait(timeout=1)
        with Session(db_engine) as db:
            user = db.get(User, seed_mechanic.id)
            completed = media_uploads.complete(db, user, upload_id)
            return completed.id, completed.completed

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(complete_once)
        second = executor.submit(complete_once)
        start.wait(timeout=1)
        assert replace_entered.wait(timeout=1)
        allow_replace.set()
        first_result = first.result(timeout=2)
        second_result = second.result(timeout=2)

    assert first_result == second_result == (upload_id, True)
    assert (tmp_path / f"{upload_id}.ready").is_file()


def test_upload_session_is_private_to_its_owner(client, seed_mechanic, seed_admin):
    content = b"\xff\xd8\xffprivate"
    login_as(client, seed_mechanic.username, "secret")
    upload_id = _start(client, content).json()["upload_id"]
    client.post("/auth/logout")
    login_as(client, seed_admin.username, "secret")

    response = client.put(
        f"/media/uploads/{upload_id}/chunks/0",
        content=content,
        headers={"X-Chunk-SHA256": hashlib.sha256(content).hexdigest()},
    )
    assert response.status_code == 404


def test_complete_rejects_content_that_does_not_match_declared_mime(client, seed_mechanic):
    content = b"not-a-jpeg"
    login_as(client, seed_mechanic.username, "secret")
    upload_id = _start(client, content).json()["upload_id"]
    sent = client.put(
        f"/media/uploads/{upload_id}/chunks/0",
        content=content,
        headers={"X-Chunk-SHA256": hashlib.sha256(content).hexdigest()},
    )
    assert sent.status_code == 200
    assert client.post(f"/media/uploads/{upload_id}/complete").status_code == 400


def test_cleanup_removes_abandoned_and_old_completed_uploads(
    db_session, seed_mechanic, tmp_path, monkeypatch
):
    monkeypatch.setattr(media_uploads, "uploads_root", lambda: tmp_path)
    now = time.time()
    rows = [
        MediaUploadSession(
            actor_user_id=seed_mechanic.id,
            media_id="media-abandoned",
            issue_key="ROBOPARK-1",
            original_name="a.jpg",
            mime_type="image/jpeg",
            size_bytes=1,
            sha256="a" * 64,
            received_offset=0,
            blob_name="abandoned.part",
            completed=False,
            created_at=now - 100,
            updated_at=now - 100,
            expires_at=now - 1,
        ),
        MediaUploadSession(
            actor_user_id=seed_mechanic.id,
            media_id="media-completed",
            issue_key="ROBOPARK-1",
            original_name="b.jpg",
            mime_type="image/jpeg",
            size_bytes=3,
            sha256="b" * 64,
            received_offset=3,
            blob_name="completed.ready",
            completed=True,
            created_at=now - 900000,
            updated_at=now - 900000,
            completed_at=now - media_uploads.COMPLETED_RETENTION_SECONDS - 1,
            expires_at=now + 1,
        ),
    ]
    db_session.add_all(rows)
    db_session.commit()
    (tmp_path / "abandoned.part").write_bytes(b"x")
    (tmp_path / "completed.ready").write_bytes(b"x")

    assert media_uploads.cleanup_expired(db_session, now=now) == 2
    assert not (tmp_path / "abandoned.part").exists()
    assert not (tmp_path / "completed.ready").exists()


def test_failed_completion_commit_rolls_back_and_cleanup_removes_renamed_blob(
    db_engine, seed_mechanic, seed_park_with_tracker, tmp_path, monkeypatch
):
    now = time.time()
    content = b"\xff\xd8\xffcommit-failure"
    with Session(db_engine) as db:
        row = MediaUploadSession(
            actor_user_id=seed_mechanic.id,
            park_id=seed_park_with_tracker.id,
            media_id="failed-completion-commit",
            issue_key="ROBOPARK-51",
            original_name="robot.jpg",
            mime_type="image/jpeg",
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            received_offset=len(content),
            blob_name="failed-completion.part",
            completed=False,
            created_at=now,
            updated_at=now,
            expires_at=now + 60,
        )
        db.add(row)
        db.commit()
        upload_id = row.id
    source = tmp_path / "failed-completion.part"
    ready = tmp_path / f"{upload_id}.ready"
    source.write_bytes(content)
    monkeypatch.setattr(media_uploads, "uploads_root", lambda: tmp_path)

    with Session(db_engine) as db:
        actor = db.get(User, seed_mechanic.id)

        def fail_commit():
            raise RuntimeError("commit failed before durability")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="commit failed"):
            media_uploads.complete(db, actor, upload_id)
        assert db.in_transaction() is False

    assert not source.exists()
    assert ready.read_bytes() == content
    with Session(db_engine) as db:
        persisted = db.get(MediaUploadSession, upload_id)
        assert persisted.completed is False
        assert persisted.blob_name == "failed-completion.part"
        assert media_uploads.cleanup_expired(db, now=now + 61) == 1
    assert not ready.exists()


def test_completion_commit_ambiguous_success_is_replayable_from_fresh_session(
    db_engine, seed_mechanic, seed_park_with_tracker, tmp_path, monkeypatch
):
    now = time.time()
    content = b"\xff\xd8\xffambiguous-commit"
    with Session(db_engine) as db:
        row = MediaUploadSession(
            actor_user_id=seed_mechanic.id,
            park_id=seed_park_with_tracker.id,
            media_id="ambiguous-completion-commit",
            issue_key="ROBOPARK-51",
            original_name="robot.jpg",
            mime_type="image/jpeg",
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            received_offset=len(content),
            blob_name="ambiguous-completion.part",
            completed=False,
            created_at=now,
            updated_at=now,
            expires_at=now + 60,
        )
        db.add(row)
        db.commit()
        upload_id = row.id
    (tmp_path / "ambiguous-completion.part").write_bytes(content)
    monkeypatch.setattr(media_uploads, "uploads_root", lambda: tmp_path)

    with Session(db_engine) as db:
        actor = db.get(User, seed_mechanic.id)
        durable_commit = db.commit

        def commit_then_fail():
            durable_commit()
            raise RuntimeError("commit acknowledgement lost")

        monkeypatch.setattr(db, "commit", commit_then_fail)
        with pytest.raises(RuntimeError, match="acknowledgement lost"):
            media_uploads.complete(db, actor, upload_id)
        assert db.in_transaction() is False

    with Session(db_engine) as db:
        actor = db.get(User, seed_mechanic.id)
        replay = media_uploads.complete(db, actor, upload_id)
        assert replay.completed is True
        assert replay.blob_name == f"{upload_id}.ready"
        assert media_uploads.cleanup_expired(db, now=now + 61) == 0
    assert (tmp_path / f"{upload_id}.ready").read_bytes() == content


def test_cleanup_serializes_eligibility_with_expired_upload_reinitialization(
    db_engine, seed_mechanic, seed_park_with_tracker, tmp_path, monkeypatch
):
    now = time.time()
    content = b"\xff\xd8\xffrace"
    with Session(db_engine) as db:
        row = MediaUploadSession(
            actor_user_id=seed_mechanic.id,
            park_id=seed_park_with_tracker.id,
            media_id="cleanup-reinit-race",
            issue_key="ROBOPARK-51",
            original_name="robot.jpg",
            mime_type="image/jpeg",
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            received_offset=1,
            blob_name="cleanup-reinit.part",
            completed=False,
            created_at=now - 86401,
            updated_at=now - 10,
            expires_at=now - 1,
        )
        db.add(row)
        db.commit()
        row_id = row.id
    (tmp_path / "cleanup-reinit.part").write_bytes(b"x")
    monkeypatch.setattr(media_uploads, "uploads_root", lambda: tmp_path)
    original_capacity_check = media_uploads._ensure_disk_capacity
    reinit_in_lock = threading.Event()
    allow_reinit = threading.Event()
    cleanup_done = threading.Event()

    def hold_reinit(db, size_bytes, *, exclude_session_id=None):
        reinit_in_lock.set()
        assert allow_reinit.wait(timeout=2)
        return original_capacity_check(
            db,
            size_bytes,
            exclude_session_id=exclude_session_id,
        )

    monkeypatch.setattr(media_uploads, "_ensure_disk_capacity", hold_reinit)

    def restart():
        with Session(db_engine) as db:
            actor = db.get(User, seed_mechanic.id)
            return media_uploads.start(
                db,
                actor,
                media_uploads.MediaUploadCreateIn(
                    media_id="cleanup-reinit-race",
                    issue_key="ROBOPARK-51",
                    name="robot.jpg",
                    mime_type="image/jpeg",
                    size_bytes=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                ),
            ).status

    def cleanup():
        try:
            with Session(db_engine) as db:
                return media_uploads.cleanup_expired(db, now=now)
        finally:
            cleanup_done.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        restart_future = executor.submit(restart)
        assert reinit_in_lock.wait(timeout=1)
        cleanup_future = executor.submit(cleanup)
        cleanup_ran_during_reinit = cleanup_done.wait(timeout=0.2)
        allow_reinit.set()
        restart_result = restart_future.result(timeout=2)
        cleanup_result = cleanup_future.result(timeout=2)

    assert cleanup_ran_during_reinit is False
    assert restart_result == "reinitialized"
    assert cleanup_result == 0
    with Session(db_engine) as db:
        persisted = db.get(MediaUploadSession, row_id)
        assert persisted is not None
        assert persisted.expires_at > now


def test_expired_upload_reinitialization_removes_orphaned_ready_blob(
    db_session, seed_mechanic, seed_park_with_tracker, tmp_path, monkeypatch
):
    now = time.time()
    content = b"\xff\xd8\xfforphan"
    row = MediaUploadSession(
        actor_user_id=seed_mechanic.id,
        park_id=seed_park_with_tracker.id,
        media_id="reinitialize-orphan-ready",
        issue_key="ROBOPARK-51",
        original_name="robot.jpg",
        mime_type="image/jpeg",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        received_offset=len(content),
        blob_name="missing-after-rename.part",
        completed=False,
        created_at=now - media_uploads.SESSION_TTL_SECONDS - 1,
        updated_at=now - 1,
        expires_at=now - 1,
    )
    db_session.add(row)
    db_session.commit()
    orphan = tmp_path / f"{row.id}.ready"
    orphan.write_bytes(content)
    monkeypatch.setattr(media_uploads, "uploads_root", lambda: tmp_path)

    restarted = media_uploads.start(
        db_session,
        seed_mechanic,
        media_uploads.MediaUploadCreateIn(
            media_id=row.media_id,
            issue_key=row.issue_key,
            name=row.original_name,
            mime_type=row.mime_type,
            size_bytes=row.size_bytes,
            sha256=row.sha256,
        ),
    )

    assert restarted.status == "reinitialized"
    assert not orphan.exists()


def test_cleanup_cannot_delete_session_while_chunk_is_writing(
    db_engine, seed_mechanic, seed_park_with_tracker, tmp_path, monkeypatch
):
    now = time.time()
    content = b"chunk-race"
    with Session(db_engine) as db:
        row = MediaUploadSession(
            actor_user_id=seed_mechanic.id,
            park_id=seed_park_with_tracker.id,
            media_id="cleanup-chunk-race",
            issue_key="ROBOPARK-51",
            original_name="robot.jpg",
            mime_type="image/jpeg",
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            received_offset=0,
            blob_name="cleanup-chunk.part",
            completed=False,
            created_at=now,
            updated_at=now,
            expires_at=now + 60,
        )
        db.add(row)
        db.commit()
        upload_id = row.id
    monkeypatch.setattr(media_uploads, "uploads_root", lambda: tmp_path)
    original_open = media_uploads.os.open
    write_started = threading.Event()
    allow_write = threading.Event()
    cleanup_done = threading.Event()

    def hold_open(path, flags, mode=0o777):
        if Path(path).name == "cleanup-chunk.part":
            write_started.set()
            assert allow_write.wait(timeout=2)
        return original_open(path, flags, mode)

    monkeypatch.setattr(media_uploads.os, "open", hold_open)

    def append():
        with Session(db_engine) as db:
            actor = db.get(User, seed_mechanic.id)
            return media_uploads.append_chunk(
                db,
                actor,
                upload_id,
                0,
                content,
                hashlib.sha256(content).hexdigest(),
            )

    def cleanup():
        try:
            with Session(db_engine) as db:
                return media_uploads.cleanup_expired(db, now=now + 61)
        finally:
            cleanup_done.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        append_future = executor.submit(append)
        assert write_started.wait(timeout=1)
        cleanup_future = executor.submit(cleanup)
        cleanup_ran_during_write = cleanup_done.wait(timeout=0.2)
        allow_write.set()
        try:
            append_result = append_future.result(timeout=2)
        except Exception:
            append_result = None
        cleanup_result = cleanup_future.result(timeout=2)

    assert cleanup_ran_during_write is False
    assert append_result == len(content)
    assert cleanup_result == 1
    assert not (tmp_path / "cleanup-chunk.part").exists()
    with Session(db_engine) as db:
        assert db.get(MediaUploadSession, upload_id) is None


def test_cleanup_cannot_delete_session_while_upload_is_completing(
    db_engine, seed_mechanic, seed_park_with_tracker, tmp_path, monkeypatch
):
    now = time.time()
    content = b"\xff\xd8\xffcomplete-race"
    with Session(db_engine) as db:
        row = MediaUploadSession(
            actor_user_id=seed_mechanic.id,
            park_id=seed_park_with_tracker.id,
            media_id="cleanup-complete-race",
            issue_key="ROBOPARK-51",
            original_name="robot.jpg",
            mime_type="image/jpeg",
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            received_offset=len(content),
            blob_name="cleanup-complete.part",
            completed=False,
            created_at=now,
            updated_at=now,
            expires_at=now + 60,
        )
        db.add(row)
        db.commit()
        upload_id = row.id
    source = tmp_path / "cleanup-complete.part"
    source.write_bytes(content)
    monkeypatch.setattr(media_uploads, "uploads_root", lambda: tmp_path)
    original_replace = Path.replace
    rename_started = threading.Event()
    allow_rename = threading.Event()
    cleanup_done = threading.Event()

    def hold_replace(path: Path, target: Path):
        if path.name == "cleanup-complete.part":
            rename_started.set()
            assert allow_rename.wait(timeout=2)
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", hold_replace)

    def complete():
        with Session(db_engine) as db:
            actor = db.get(User, seed_mechanic.id)
            return media_uploads.complete(db, actor, upload_id).completed

    def cleanup():
        try:
            with Session(db_engine) as db:
                return media_uploads.cleanup_expired(db, now=now + 61)
        finally:
            cleanup_done.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        complete_future = executor.submit(complete)
        assert rename_started.wait(timeout=1)
        cleanup_future = executor.submit(cleanup)
        cleanup_ran_during_rename = cleanup_done.wait(timeout=0.2)
        allow_rename.set()
        try:
            complete_result = complete_future.result(timeout=2)
        except Exception:
            complete_result = None
        cleanup_result = cleanup_future.result(timeout=2)

    assert cleanup_ran_during_rename is False
    assert complete_result is True
    assert cleanup_result == 0
    assert not source.exists()
    assert (tmp_path / f"{upload_id}.ready").read_bytes() == content
    with Session(db_engine) as db:
        assert db.get(MediaUploadSession, upload_id).completed is True


def test_cleanup_retains_completed_upload_while_dependent_action_is_pending(
    db_session, seed_mechanic, tmp_path, monkeypatch
):
    monkeypatch.setattr(media_uploads, "uploads_root", lambda: tmp_path)
    now = time.time()
    row = MediaUploadSession(
        actor_user_id=seed_mechanic.id,
        media_id="media-pending-review",
        issue_key="ROBOPARK-1",
        original_name="review.jpg",
        mime_type="image/jpeg",
        size_bytes=4,
        sha256="c" * 64,
        received_offset=4,
        blob_name="pending.ready",
        completed=True,
        created_at=now - 900000,
        updated_at=now - 900000,
        completed_at=now - media_uploads.COMPLETED_RETENTION_SECONDS - 1,
        expires_at=now - 1,
        dependent_device_id="phone-1",
        dependent_action_id="review-action",
        dependency_bound_at=now - 900000,
        dependency_terminal_at=None,
    )
    db_session.add(row)
    db_session.commit()
    (tmp_path / row.blob_name).write_bytes(b"data")

    assert media_uploads.cleanup_expired(db_session, now=now) == 0
    assert db_session.get(MediaUploadSession, row.id) is not None
    assert (tmp_path / row.blob_name).exists()

    row.dependency_terminal_at = now
    db_session.commit()
    assert (
        media_uploads.cleanup_expired(
            db_session, now=now + media_uploads.COMPLETED_RETENTION_SECONDS + 1
        )
        == 1
    )
    assert db_session.get(MediaUploadSession, row.id) is None
    assert not (tmp_path / row.blob_name).exists()


def test_cleanup_bounds_nonterminal_dependency_retention(
    db_session, seed_mechanic, tmp_path, monkeypatch
):
    monkeypatch.setattr(media_uploads, "uploads_root", lambda: tmp_path)
    now = time.time()
    row = MediaUploadSession(
        actor_user_id=seed_mechanic.id,
        media_id="media-abandoned-dependent",
        issue_key="ROBOPARK-1",
        original_name="review.jpg",
        mime_type="image/jpeg",
        size_bytes=4,
        sha256="c" * 64,
        received_offset=4,
        blob_name="abandoned-dependent.ready",
        completed=True,
        created_at=now - media_uploads.PENDING_DEPENDENCY_RETENTION_SECONDS - 10,
        updated_at=now - media_uploads.PENDING_DEPENDENCY_RETENTION_SECONDS - 10,
        completed_at=now - media_uploads.PENDING_DEPENDENCY_RETENTION_SECONDS - 1,
        expires_at=now - 1,
        dependent_device_id="phone-1",
        dependent_action_id="abandoned-action",
        dependency_bound_at=now - media_uploads.PENDING_DEPENDENCY_RETENTION_SECONDS - 10,
        dependency_terminal_at=None,
    )
    db_session.add(row)
    db_session.commit()
    path = tmp_path / row.blob_name
    path.write_bytes(b"data")

    assert media_uploads.cleanup_expired(db_session, now=now) == 1
    assert db_session.get(MediaUploadSession, row.id) is None
    assert not path.exists()


def test_cleanup_waits_for_dependency_content_read(
    db_engine, seed_mechanic, seed_park_with_tracker, tmp_path, monkeypatch
):
    now = time.time()
    content = b"dependency-content"
    with Session(db_engine) as db:
        row = MediaUploadSession(
            actor_user_id=seed_mechanic.id,
            park_id=seed_park_with_tracker.id,
            media_id="media-read-cleanup-race",
            issue_key="ROBOPARK-51",
            original_name="review.jpg",
            mime_type="image/jpeg",
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            received_offset=len(content),
            blob_name="read-cleanup-race.ready",
            completed=True,
            created_at=now - media_uploads.PENDING_DEPENDENCY_RETENTION_SECONDS - 10,
            updated_at=now - media_uploads.PENDING_DEPENDENCY_RETENTION_SECONDS - 10,
            completed_at=now - media_uploads.PENDING_DEPENDENCY_RETENTION_SECONDS - 1,
            expires_at=now - 1,
            dependent_device_id="phone-1",
            dependent_action_id="read-cleanup-action",
            dependency_bound_at=now - media_uploads.PENDING_DEPENDENCY_RETENTION_SECONDS - 10,
        )
        db.add(row)
        db.commit()
        upload_id = row.id
    path = tmp_path / "read-cleanup-race.ready"
    path.write_bytes(content)
    monkeypatch.setattr(media_uploads, "uploads_root", lambda: tmp_path)
    original_read_bytes = Path.read_bytes
    read_started = threading.Event()
    allow_read = threading.Event()
    cleanup_done = threading.Event()

    def hold_read(candidate: Path):
        if candidate == path:
            read_started.set()
            assert allow_read.wait(timeout=2)
        return original_read_bytes(candidate)

    monkeypatch.setattr(Path, "read_bytes", hold_read)

    def consume():
        with Session(db_engine) as db:
            actor = db.get(User, seed_mechanic.id)
            return media_uploads.consume_action_dependency(
                db,
                actor,
                media_id="media-read-cleanup-race",
                issue_key="ROBOPARK-51",
                device_id="phone-1",
                action_id="read-cleanup-action",
            ).content

    def cleanup():
        try:
            with Session(db_engine) as db:
                return media_uploads.cleanup_expired(db, now=now)
        finally:
            cleanup_done.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        consume_future = executor.submit(consume)
        assert read_started.wait(timeout=1)
        cleanup_future = executor.submit(cleanup)
        cleanup_ran_during_read = cleanup_done.wait(timeout=0.2)
        allow_read.set()
        assert consume_future.result(timeout=2) == content
        assert cleanup_future.result(timeout=2) == 1

    assert cleanup_ran_during_read is False
    assert not path.exists()
    with Session(db_engine) as db:
        assert db.get(MediaUploadSession, upload_id) is None


def test_cleanup_retries_transient_upload_unlink_failure(
    db_session, seed_mechanic, tmp_path, monkeypatch
):
    monkeypatch.setattr(media_uploads, "uploads_root", lambda: tmp_path)
    now = time.time()
    row = MediaUploadSession(
        actor_user_id=seed_mechanic.id,
        media_id="media-unlink-retry",
        issue_key="ROBOPARK-1",
        original_name="review.jpg",
        mime_type="image/jpeg",
        size_bytes=4,
        sha256="d" * 64,
        received_offset=4,
        blob_name="retry.ready",
        completed=True,
        created_at=now - 900000,
        updated_at=now - 900000,
        completed_at=now - media_uploads.COMPLETED_RETENTION_SECONDS - 1,
        expires_at=now - 1,
    )
    db_session.add(row)
    db_session.commit()
    path = tmp_path / row.blob_name
    path.write_bytes(b"data")
    real_unlink = Path.unlink
    attempts = 0

    def flaky_unlink(candidate, *args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("temporary filesystem error")
        return real_unlink(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)
    assert media_uploads.cleanup_expired(db_session, now=now) == 0
    assert db_session.get(MediaUploadSession, row.id) is not None
    assert path.exists()

    assert media_uploads.cleanup_expired(db_session, now=now) == 1
    assert db_session.get(MediaUploadSession, row.id) is None
    assert not path.exists()


def test_deleted_legacy_unbound_upload_can_be_recreated_with_same_media_identity(
    client, db_session, seed_mechanic, tmp_path, monkeypatch
):
    monkeypatch.setattr(media_uploads, "uploads_root", lambda: tmp_path)
    now = time.time()
    content = b"\xff\xd8\xfflegacy"
    old = MediaUploadSession(
        actor_user_id=seed_mechanic.id,
        media_id="legacy-media-1234",
        issue_key="ROBOPARK-51",
        original_name="robot.jpg",
        mime_type="image/jpeg",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        received_offset=len(content),
        blob_name="legacy.ready",
        completed=True,
        created_at=now - 9 * 86400,
        updated_at=now - 9 * 86400,
        completed_at=now - 8 * 86400,
        expires_at=now - 7 * 86400,
    )
    db_session.add(old)
    db_session.commit()
    (tmp_path / old.blob_name).write_bytes(content)

    assert media_uploads.cleanup_expired(db_session, now=now) == 1
    assert db_session.get(MediaUploadSession, old.id) is None

    login_as(client, seed_mechanic.username, "secret")
    recreated = _start(
        client,
        content,
        media_id=old.media_id,
        dependent_action_id="review-action-legacy",
        device_id="account-42",
    )
    assert recreated.status_code == 201
    row = db_session.scalar(
        select(MediaUploadSession).where(MediaUploadSession.media_id == old.media_id)
    )
    assert row.id != old.id
    assert row.dependent_action_id == "review-action-legacy"
    assert row.dependent_device_id == "account-42"
