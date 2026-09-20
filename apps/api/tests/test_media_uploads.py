import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy.orm import Session

from conftest import login_as
from robopark_api.models import User
from robopark_api.services import media_uploads
from robopark_api.task_workflow_models import MediaUploadSession


def _start(client, content: bytes, *, media_id="media-12345678", issue_key="ROBOPARK-51"):
    return client.post(
        "/media/uploads",
        json={
            "media_id": media_id,
            "issue_key": issue_key,
            "name": "robot.jpg",
            "mime_type": "image/jpeg",
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        },
    )


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
            db, user, upload.id, 0, content, hashlib.sha256(content).hexdigest()
        )
        upload_id = upload.id

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
