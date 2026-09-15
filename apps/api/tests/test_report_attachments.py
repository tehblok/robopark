import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from alembic import command as alembic_command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile as StarletteUploadFile

from conftest import role_id_for
from robopark_api.models import (
    AccessStatus,
    Park,
    Report,
    ReportAttachment,
    Role,
    User,
    UserPark,
)
from robopark_api.security import hash_password
from robopark_api.services import report_attachments as att_svc
from robopark_api.services import reports as reports_svc
from robopark_api.task_workflow_models import ReliableAction, TaskAttachment

TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


@pytest.mark.parametrize(
    ("filename", "content", "content_type", "error"),
    [
        ("../done.png", TINY_PNG, "image/png", "task_attachment_filename_invalid"),
        ("done.txt", b"plain", "text/plain", "task_attachment_invalid_type"),
        ("done.png", b"", "image/png", "task_attachment_empty"),
        (
            "done.png",
            b"x" * (att_svc.MAX_ATTACHMENT_BYTES + 1),
            "image/png",
            "task_attachment_too_large",
        ),
    ],
    ids=["traversal", "unsupported", "empty", "too-large"],
)
def test_staged_task_attachment_rejects_unsafe_or_unbounded_input(
    db_session, seed_royal, test_settings, monkeypatch, filename, content, content_type, error
):
    from robopark_api.services import task_timeline

    monkeypatch.setattr(task_timeline, "get_settings", lambda: test_settings)
    message = task_timeline.append_system_message(
        db_session, issue_key="ROBOPARK-1", actor=seed_royal, text="Photo"
    )
    db_session.commit()

    with pytest.raises(ValueError, match=error):
        task_timeline.stage_attachment(
            db_session,
            actor=seed_royal,
            issue_key="ROBOPARK-1",
            message=message,
            idempotency_key="attachment-0001",
            filename=filename,
            content=content,
            content_type=content_type,
        )


def test_staged_task_attachment_survives_request_completion(
    client, db_session, seed_royal, test_settings, monkeypatch
):
    from robopark_api.services import platform_settings, task_timeline, tracker_cache

    monkeypatch.setattr(task_timeline, "get_settings", lambda: test_settings)
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    monkeypatch.setattr(
        tracker_cache,
        "get_issue",
        lambda **_kwargs: {
            "key": "ROBOPARK-1",
            "queue": "ROBOPARK",
            "tags": ["Alpha"],
            "status": "Open",
            "status_key": "open",
        },
    )
    message = task_timeline.append_system_message(
        db_session, issue_key="ROBOPARK-1", actor=seed_royal, text="Photo"
    )
    db_session.commit()
    _login(client, "royal")

    response = client.post(
        "/tracker/issues/ROBOPARK-1/message-attachments",
        headers={"Idempotency-Key": "attachment-0001"},
        data={"message_id": message.id},
        files={"file": ("done.png", TINY_PNG, "image/png")},
    )

    assert response.status_code == 201
    attachment = db_session.get(TaskAttachment, response.json()["id"])
    action = db_session.get(ReliableAction, response.json()["action_id"])
    path = task_timeline.staged_attachments_root() / attachment.blob_name
    assert path.read_bytes() == TINY_PNG
    assert path.stat().st_mode & 0o777 == 0o600
    assert attachment.sha256 == hashlib.sha256(TINY_PNG).hexdigest()
    assert (action.action, action.state) == ("attach", "pending")


def test_successful_staged_attachment_retry_reuses_row_action_and_blob(
    db_session, seed_royal, test_settings, monkeypatch
):
    from robopark_api.services import task_timeline
    from robopark_api.services.reliable_actions import complete_action

    monkeypatch.setattr(task_timeline, "get_settings", lambda: test_settings)
    message = task_timeline.append_system_message(
        db_session, issue_key="ROBOPARK-1", actor=seed_royal, text="Photo"
    )
    db_session.commit()
    first, action = task_timeline.stage_attachment(
        db_session,
        actor=seed_royal,
        issue_key="ROBOPARK-1",
        message=message,
        idempotency_key="attachment-replay-0001",
        filename="done.png",
        content=TINY_PNG,
        content_type="image/png",
    )
    first_path = task_timeline.staged_attachments_root() / first.blob_name
    first_mtime = first_path.stat().st_mtime_ns
    complete_action(db_session, action, {"external_id": "tracker-file-1"})
    db_session.commit()

    replay, replay_action = task_timeline.stage_attachment(
        db_session,
        actor=seed_royal,
        issue_key="ROBOPARK-1",
        message=message,
        idempotency_key="attachment-replay-0001",
        filename="done.png",
        content=TINY_PNG,
        content_type="image/png",
    )

    assert replay.id == first.id
    assert replay_action.id == action.id
    assert first_path.stat().st_mtime_ns == first_mtime
    assert list(task_timeline.staged_attachments_root().iterdir()) == [first_path]
    assert json.loads(action.payload_json) == {
        "filename": "done.png",
        "mime_type": "image/png",
        "sha256": hashlib.sha256(TINY_PNG).hexdigest(),
        "size_bytes": len(TINY_PNG),
    }


@pytest.fixture
def seed_operator_with_park(db_session, seed_park_with_tracker):
    user = User(
        username="operator1",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(UserPark(user_id=user.id, park_id=seed_park_with_tracker.id))
    db_session.commit()
    db_session.refresh(user)
    return user


def _login(client: TestClient, username: str) -> None:
    response = client.post("/auth/login", json={"username": username, "password": "secret"})
    assert response.status_code == 204


def _open_report(db_session, author, park_id):
    return reports_svc.create_manual_report(
        db_session,
        author=author,
        park_id=park_id,
        kind=reports_svc.KIND_MECHANIC_PROBLEM,
        title="Broken screen",
        body="UI glitch",
        tracker_key=None,
        tracker_url=None,
    )


def _migrate_temporary_database(database_url: str, monkeypatch) -> Path:
    api_dir = Path(__file__).parents[1]
    monkeypatch.setenv("DATABASE_URL", database_url)
    alembic_command.upgrade(Config(api_dir / "alembic.ini"), "head")
    return api_dir


def _clean_cli_env(api_dir: Path, database_url: str, storage_root: Path) -> dict[str, str]:
    return {
        "DATABASE_URL": database_url,
        "PATH": os.environ.get("PATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(api_dir / "src"),
        "REPORT_ATTACHMENTS_DIR": str(storage_root),
        "SECRET_KEY": "clean-process-test-key",
    }


def _seed_attachment_metadata(database_url: str, *, storage_key: str) -> None:
    engine = create_engine(database_url, future=True)
    with Session(engine) as db:
        role = Role(
            slug="attachment-review-role",
            name="Attachment review role",
            description="",
            is_system=False,
            is_active=True,
        )
        park = Park(name="Attachment review park", tag="attachment-review", is_active=True)
        user = User(
            username="attachment-review-user",
            password_hash="not-used",
            role_ref=role,
            access_status=AccessStatus.approved.value,
            is_active=True,
        )
        db.add_all([park, user])
        db.flush()
        report = Report(
            kind="mechanic_problem",
            status="open",
            park_id=park.id,
            author_user_id=user.id,
            target_role="operator",
            title="Attachment review",
            body="",
        )
        db.add(report)
        db.flush()
        db.add(
            ReportAttachment(
                report_id=report.id,
                kind=att_svc.KIND_CLIENT_LOG,
                filename="client.txt",
                content_type="text/plain",
                size_bytes=1,
                storage_key=storage_key,
            )
        )
        db.commit()


def _assert_generic_storage_validation_failure(
    error: pytest.ExceptionInfo[att_svc.AttachmentStorageError],
    *,
    sensitive_values: tuple[str, ...],
) -> None:
    assert str(error.value) == "report attachment storage validation failed"
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__ is True
    context_message = str(error.value.__context__ or "")
    for sensitive_value in sensitive_values:
        assert sensitive_value not in str(error.value)
        assert sensitive_value not in context_message


def test_verify_command_runs_in_clean_process_with_migrated_database(
    sqlite_database_url, tmp_path, monkeypatch
):
    api_dir = _migrate_temporary_database(sqlite_database_url, monkeypatch)
    storage_root = tmp_path / "clean-process-attachments"

    result = subprocess.run(
        [sys.executable, "-m", "robopark_api.verify_report_attachments"],
        cwd=tmp_path,
        env=_clean_cli_env(api_dir, sqlite_database_url, storage_root),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert result.stdout == "report attachment storage verified\n"
    assert result.stderr == ""


def test_author_can_attach_ui_snapshot_and_log(
    db_session, seed_mechanic, seed_park_with_tracker, test_settings, monkeypatch
):
    monkeypatch.setattr(att_svc, "get_settings", lambda: test_settings)
    report = _open_report(db_session, seed_mechanic, seed_park_with_tracker.id)
    snap = att_svc.add_attachment(
        db_session,
        seed_mechanic,
        report.id,
        kind=att_svc.KIND_UI_SNAPSHOT,
        filename="ui.png",
        content=TINY_PNG,
        content_type="image/png",
    )
    log = att_svc.add_attachment(
        db_session,
        seed_mechanic,
        report.id,
        kind=att_svc.KIND_CLIENT_LOG,
        filename="client-log.txt",
        content=b"role=mechanic\npath=/emergency\n",
        content_type="text/plain",
    )
    assert snap.kind == "ui_snapshot"
    assert snap.content_type == "image/png"
    assert log.kind == "client_log"
    root = Path(test_settings.report_attachments_dir)
    assert (root / snap.storage_key).read_bytes() == TINY_PNG


def test_duplicate_kind_rejected(
    db_session, seed_mechanic, seed_park_with_tracker, test_settings, monkeypatch
):
    monkeypatch.setattr(att_svc, "get_settings", lambda: test_settings)
    report = _open_report(db_session, seed_mechanic, seed_park_with_tracker.id)
    att_svc.add_attachment(
        db_session,
        seed_mechanic,
        report.id,
        kind=att_svc.KIND_DEVICE_PHOTO,
        filename="cam.jpg",
        content=TINY_PNG,
        content_type="image/png",
    )
    with pytest.raises(ValueError, match="report_attachment_kind_taken"):
        att_svc.add_attachment(
            db_session,
            seed_mechanic,
            report.id,
            kind=att_svc.KIND_DEVICE_PHOTO,
            filename="cam2.png",
            content=TINY_PNG,
            content_type="image/png",
        )


def test_non_author_cannot_attach(
    db_session,
    seed_mechanic,
    seed_operator_with_park,
    seed_park_with_tracker,
    test_settings,
    monkeypatch,
):
    monkeypatch.setattr(att_svc, "get_settings", lambda: test_settings)
    report = _open_report(db_session, seed_mechanic, seed_park_with_tracker.id)
    with pytest.raises(PermissionError):
        att_svc.add_attachment(
            db_session,
            seed_operator_with_park,
            report.id,
            kind=att_svc.KIND_CLIENT_LOG,
            filename="log.txt",
            content=b"nope",
            content_type="text/plain",
        )


def test_http_attach_and_download(
    client: TestClient,
    db_session,
    seed_mechanic,
    seed_operator_with_park,
    seed_park_with_tracker,
    test_settings,
    monkeypatch,
):
    monkeypatch.setattr(att_svc, "get_settings", lambda: test_settings)
    report = _open_report(db_session, seed_mechanic, seed_park_with_tracker.id)
    _login(client, "mech1")
    uploaded = client.post(
        f"/reports/{report.id}/attachments",
        data={"kind": "ui_snapshot"},
        files={"file": ("ui.png", TINY_PNG, "image/png")},
    )
    assert uploaded.status_code == 201
    attachment_id = uploaded.json()["id"]
    fetched = client.get(f"/reports/{report.id}")
    assert fetched.status_code == 200
    assert [item["kind"] for item in fetched.json()["attachments"]] == ["ui_snapshot"]

    download = client.get(f"/reports/{report.id}/attachments/{attachment_id}")
    assert download.status_code == 200
    assert download.content == TINY_PNG

    client.post("/auth/logout")
    _login(client, "operator1")
    as_operator = client.get(f"/reports/{report.id}/attachments/{attachment_id}")
    assert as_operator.status_code == 200
    assert as_operator.content == TINY_PNG


def test_http_overlong_log_and_image_filenames_are_bounded_and_downloadable(
    client: TestClient,
    db_session,
    seed_mechanic,
    seed_park_with_tracker,
    test_settings,
    monkeypatch,
):
    monkeypatch.setattr(att_svc, "get_settings", lambda: test_settings)
    report = _open_report(db_session, seed_mechanic, seed_park_with_tracker.id)
    _login(client, "mech1")
    root = Path(test_settings.report_attachments_dir)
    uploads = [
        (att_svc.KIND_CLIENT_LOG, f"{'l' * 320}.log", b"client log", "text/plain", ".txt"),
        (att_svc.KIND_UI_SNAPSHOT, f"{'i' * 320}.png", TINY_PNG, "image/png", ".png"),
    ]

    for kind, filename, content, content_type, expected_suffix in uploads:
        uploaded = client.post(
            f"/reports/{report.id}/attachments",
            data={"kind": kind},
            files={"file": (filename, content, content_type)},
        )

        assert uploaded.status_code == 201
        payload = uploaded.json()
        display_filename = payload["filename"]
        assert len(display_filename.encode("utf-8")) <= 240
        assert display_filename.lower().endswith(expected_suffix)

        attachment = db_session.get(ReportAttachment, payload["id"])
        assert attachment is not None
        storage_component = Path(attachment.storage_key).name
        assert len(storage_component) == 32
        assert set(storage_component) <= set("0123456789abcdef")
        assert (root / attachment.storage_key).read_bytes() == content

        downloaded = client.get(f"/reports/{report.id}/attachments/{attachment.id}")
        assert downloaded.status_code == 200
        assert downloaded.content == content


def test_http_royal_cannot_attach(
    client: TestClient, db_session, seed_royal, seed_mechanic, seed_park_with_tracker
):
    report = _open_report(db_session, seed_mechanic, seed_park_with_tracker.id)
    _login(client, "royal")
    response = client.post(
        f"/reports/{report.id}/attachments",
        data={"kind": "client_log"},
        files={"file": ("log.txt", b"log", "text/plain")},
    )
    assert response.status_code == 403


def test_http_rejects_garbage_file(
    client: TestClient,
    db_session,
    seed_mechanic,
    seed_park_with_tracker,
    test_settings,
    monkeypatch,
):
    monkeypatch.setattr(att_svc, "get_settings", lambda: test_settings)
    report = _open_report(db_session, seed_mechanic, seed_park_with_tracker.id)
    _login(client, "mech1")
    response = client.post(
        f"/reports/{report.id}/attachments",
        data={"kind": "ui_snapshot"},
        files={"file": ("note.txt", b"not-an-image", "text/plain")},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "report_attachment_invalid_type"


def test_closed_report_cannot_accept_attachment(
    db_session, seed_mechanic, seed_park_with_tracker, test_settings, monkeypatch
):
    monkeypatch.setattr(att_svc, "get_settings", lambda: test_settings)
    report = _open_report(db_session, seed_mechanic, seed_park_with_tracker.id)
    report.status = reports_svc.STATUS_DONE
    db_session.commit()
    root = Path(test_settings.report_attachments_dir)

    with pytest.raises(PermissionError, match="forbidden"):
        att_svc.add_attachment(
            db_session,
            seed_mechanic,
            report.id,
            kind=att_svc.KIND_CLIENT_LOG,
            filename="closed.txt",
            content=b"closed",
            content_type="text/plain",
        )

    assert not root.exists() or not any(path.is_file() for path in root.rglob("*"))


def test_cross_park_operator_cannot_download_attachment(
    client: TestClient,
    db_session,
    seed_mechanic,
    seed_park_with_tracker,
    test_settings,
    monkeypatch,
):
    monkeypatch.setattr(att_svc, "get_settings", lambda: test_settings)
    other_park = Park(name="Beta", tag="Beta", is_active=True)
    db_session.add(other_park)
    db_session.flush()
    other_operator = User(
        username="operator-other-park",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(other_operator)
    db_session.flush()
    db_session.add(UserPark(user_id=other_operator.id, park_id=other_park.id))
    db_session.commit()

    report = _open_report(db_session, seed_mechanic, seed_park_with_tracker.id)
    attachment = att_svc.add_attachment(
        db_session,
        seed_mechanic,
        report.id,
        kind=att_svc.KIND_CLIENT_LOG,
        filename="client.txt",
        content=b"safe test content",
        content_type="text/plain",
    )
    _login(client, other_operator.username)

    response = client.get(f"/reports/{report.id}/attachments/{attachment.id}")

    assert response.status_code == 403
    assert response.json()["detail"] == "forbidden"


def test_storage_key_cannot_escape_attachment_root(
    db_session, seed_mechanic, seed_park_with_tracker, test_settings, monkeypatch
):
    monkeypatch.setattr(att_svc, "get_settings", lambda: test_settings)
    report = _open_report(db_session, seed_mechanic, seed_park_with_tracker.id)
    root = Path(test_settings.report_attachments_dir)
    root.mkdir(parents=True)
    outside = root.parent / "outside.bin"
    outside.write_bytes(b"outside")
    attachment = ReportAttachment(
        report_id=report.id,
        kind=att_svc.KIND_CLIENT_LOG,
        filename="private-name.txt",
        content_type="text/plain",
        size_bytes=7,
        storage_key="../outside.bin",
    )
    db_session.add(attachment)
    db_session.commit()

    with pytest.raises(LookupError, match="report_attachment_not_found"):
        att_svc.get_attachment(db_session, seed_mechanic, report.id, attachment.id)


def test_write_failure_rolls_back_metadata(
    db_session, seed_mechanic, seed_park_with_tracker, test_settings, monkeypatch
):
    monkeypatch.setattr(att_svc, "get_settings", lambda: test_settings)
    report = _open_report(db_session, seed_mechanic, seed_park_with_tracker.id)

    def fail_write(_destination: Path, _content: bytes) -> None:
        raise OSError("simulated write failure")

    monkeypatch.setattr(att_svc, "_atomic_write", fail_write)

    with pytest.raises(OSError, match="simulated write failure"):
        att_svc.add_attachment(
            db_session,
            seed_mechanic,
            report.id,
            kind=att_svc.KIND_CLIENT_LOG,
            filename="client.txt",
            content=b"safe test content",
            content_type="text/plain",
        )

    assert db_session.scalars(select(ReportAttachment)).all() == []


def test_commit_failure_removes_only_new_file_and_rolls_back_metadata(
    db_session, seed_mechanic, seed_park_with_tracker, test_settings, monkeypatch
):
    monkeypatch.setattr(att_svc, "get_settings", lambda: test_settings)
    report = _open_report(db_session, seed_mechanic, seed_park_with_tracker.id)
    root = Path(test_settings.report_attachments_dir)

    def fail_commit() -> None:
        raise OSError("simulated commit failure")

    monkeypatch.setattr(db_session, "commit", fail_commit)

    with pytest.raises(OSError, match="simulated commit failure"):
        att_svc.add_attachment(
            db_session,
            seed_mechanic,
            report.id,
            kind=att_svc.KIND_CLIENT_LOG,
            filename="client.txt",
            content=b"safe test content",
            content_type="text/plain",
        )

    assert db_session.scalars(select(ReportAttachment)).all() == []
    assert not root.exists() or not any(path.is_file() for path in root.rglob("*"))


def test_commit_and_rollback_failure_still_removes_new_file(
    db_session, seed_mechanic, seed_park_with_tracker, test_settings, monkeypatch
):
    monkeypatch.setattr(att_svc, "get_settings", lambda: test_settings)
    report = _open_report(db_session, seed_mechanic, seed_park_with_tracker.id)
    root = Path(test_settings.report_attachments_dir)

    def fail_commit() -> None:
        raise OSError("simulated commit failure")

    def fail_rollback() -> None:
        raise OSError("simulated rollback failure")

    monkeypatch.setattr(db_session, "commit", fail_commit)
    monkeypatch.setattr(db_session, "rollback", fail_rollback)

    with pytest.raises(OSError):
        att_svc.add_attachment(
            db_session,
            seed_mechanic,
            report.id,
            kind=att_svc.KIND_CLIENT_LOG,
            filename="client.txt",
            content=b"safe test content",
            content_type="text/plain",
        )

    assert not root.exists() or not any(path.is_file() for path in root.rglob("*"))


def test_atomic_write_never_replaces_existing_destination(tmp_path):
    destination = tmp_path / "attachment.bin"
    destination.write_bytes(b"stale")

    with pytest.raises(FileExistsError):
        att_svc._atomic_write(destination, b"replacement")

    assert destination.read_bytes() == b"stale"


def test_resolve_storage_key_allows_nonexistent_upload_destination(test_settings, monkeypatch):
    monkeypatch.setattr(att_svc, "get_settings", lambda: test_settings)
    root = Path(test_settings.report_attachments_dir)

    resolved = att_svc._resolve_storage_key("42/new-upload")

    assert resolved == root.resolve() / "42/new-upload"
    assert not resolved.exists()


def test_outside_root_symlink_is_not_downloadable(
    db_session, seed_mechanic, seed_park_with_tracker, test_settings, monkeypatch
):
    monkeypatch.setattr(att_svc, "get_settings", lambda: test_settings)
    report = _open_report(db_session, seed_mechanic, seed_park_with_tracker.id)
    root = Path(test_settings.report_attachments_dir)
    root.mkdir(parents=True)
    outside = root.parent / "outside-symlink-target.bin"
    outside.write_bytes(b"outside")
    (root / "outside-link").symlink_to(outside)
    attachment = ReportAttachment(
        report_id=report.id,
        kind=att_svc.KIND_CLIENT_LOG,
        filename="private-name.txt",
        content_type="text/plain",
        size_bytes=7,
        storage_key="outside-link",
    )
    db_session.add(attachment)
    db_session.commit()

    with pytest.raises(LookupError, match="report_attachment_not_found"):
        att_svc.get_attachment(db_session, seed_mechanic, report.id, attachment.id)


def test_symlink_loop_is_normalized_at_direct_and_validation_boundaries(
    db_session, seed_mechanic, seed_park_with_tracker, test_settings, monkeypatch
):
    monkeypatch.setattr(att_svc, "get_settings", lambda: test_settings)
    report = _open_report(db_session, seed_mechanic, seed_park_with_tracker.id)
    root = Path(test_settings.report_attachments_dir)
    root.mkdir(parents=True)
    (root / "private-loop").symlink_to("private-loop")
    attachment = ReportAttachment(
        report_id=report.id,
        kind=att_svc.KIND_CLIENT_LOG,
        filename="private-name.txt",
        content_type="text/plain",
        size_bytes=1,
        storage_key="private-loop",
    )
    db_session.add(attachment)
    db_session.commit()

    with pytest.raises(LookupError, match="report_attachment_not_found"):
        att_svc.get_attachment(db_session, seed_mechanic, report.id, attachment.id)

    with pytest.raises(att_svc.AttachmentStorageError) as error:
        att_svc.validate_attachment_storage(db_session)
    _assert_generic_storage_validation_failure(error, sensitive_values=("private-loop",))


def test_verify_command_symlink_loop_failure_is_generic_in_clean_process(
    sqlite_database_url, tmp_path, monkeypatch
):
    api_dir = _migrate_temporary_database(sqlite_database_url, monkeypatch)
    storage_root = tmp_path / "clean-process-loop-attachments"
    storage_root.mkdir()
    (storage_root / "private-loop").symlink_to("private-loop")
    _seed_attachment_metadata(sqlite_database_url, storage_key="private-loop")

    result = subprocess.run(
        [sys.executable, "-m", "robopark_api.verify_report_attachments"],
        cwd=tmp_path,
        env=_clean_cli_env(api_dir, sqlite_database_url, storage_root),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr == "report attachment storage validation failed\n"


@pytest.mark.parametrize(
    "persisted_content",
    [None, b"wrong-size"],
    ids=["missing-file", "size-mismatch"],
)
def test_validate_attachment_storage_reports_only_generic_failure_for_invalid_file(
    db_session,
    seed_mechanic,
    seed_park_with_tracker,
    test_settings,
    monkeypatch,
    persisted_content,
):
    monkeypatch.setattr(att_svc, "get_settings", lambda: test_settings)
    report = _open_report(db_session, seed_mechanic, seed_park_with_tracker.id)
    storage_key = "private-storage-key.txt"
    attachment = ReportAttachment(
        report_id=report.id,
        kind=att_svc.KIND_CLIENT_LOG,
        filename="private-filename.txt",
        content_type="text/plain",
        size_bytes=17,
        storage_key=storage_key,
    )
    db_session.add(attachment)
    db_session.commit()
    if persisted_content is not None:
        path = Path(test_settings.report_attachments_dir) / storage_key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(persisted_content)

    with pytest.raises(att_svc.AttachmentStorageError) as error:
        att_svc.validate_attachment_storage(db_session)

    _assert_generic_storage_validation_failure(
        error,
        sensitive_values=(attachment.filename, attachment.storage_key, str(attachment.id)),
    )


def test_validate_attachment_storage_suppresses_sensitive_oserror_cause(
    db_session, seed_mechanic, seed_park_with_tracker, test_settings, monkeypatch
):
    monkeypatch.setattr(att_svc, "get_settings", lambda: test_settings)
    report = _open_report(db_session, seed_mechanic, seed_park_with_tracker.id)
    db_session.add(
        ReportAttachment(
            report_id=report.id,
            kind=att_svc.KIND_CLIENT_LOG,
            filename="private-filename.txt",
            content_type="text/plain",
            size_bytes=17,
            storage_key="private-storage-key.txt",
        )
    )
    db_session.commit()

    def fail_without_disclosing_path(_storage_key: str) -> Path:
        raise OSError("private-storage-key.txt")

    monkeypatch.setattr(att_svc, "_resolve_storage_key", fail_without_disclosing_path)

    with pytest.raises(att_svc.AttachmentStorageError) as error:
        att_svc.validate_attachment_storage(db_session)

    _assert_generic_storage_validation_failure(
        error,
        sensitive_values=("private-filename.txt", "private-storage-key.txt"),
    )


def test_upload_stops_after_kind_limit_plus_one(
    client: TestClient,
    db_session,
    seed_mechanic,
    seed_park_with_tracker,
    test_settings,
    monkeypatch,
):
    monkeypatch.setattr(att_svc, "get_settings", lambda: test_settings)
    report = _open_report(db_session, seed_mechanic, seed_park_with_tracker.id)
    _login(client, "mech1")
    read_sizes: list[int] = []

    async def bounded_read(_upload: StarletteUploadFile, size: int = -1) -> bytes:
        read_sizes.append(size)
        return b"x" * (att_svc.MAX_LOG_BYTES + 1)

    monkeypatch.setattr(StarletteUploadFile, "read", bounded_read)

    response = client.post(
        f"/reports/{report.id}/attachments",
        data={"kind": att_svc.KIND_CLIENT_LOG},
        files={"file": ("client.txt", b"small request fixture", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "report_attachment_too_large"
    assert read_sizes == [att_svc.MAX_LOG_BYTES + 1]


def test_verify_command_prints_only_generic_success(monkeypatch, capsys):
    from robopark_api import verify_report_attachments as command

    class FakeSession:
        def close(self) -> None:
            pass

    session = FakeSession()
    monkeypatch.setattr(command, "SessionLocal", lambda: session)
    monkeypatch.setattr(command, "validate_attachment_storage", lambda db: 27)

    command.main()

    captured = capsys.readouterr()
    assert captured.out == "report attachment storage verified\n"
    assert captured.err == ""


def test_verify_command_failure_is_generic(monkeypatch, capsys):
    from robopark_api import verify_report_attachments as command

    class FakeSession:
        def close(self) -> None:
            pass

    def fail_validation(_db) -> int:
        raise att_svc.AttachmentStorageError("report attachment storage validation failed")

    monkeypatch.setattr(command, "SessionLocal", FakeSession)
    monkeypatch.setattr(command, "validate_attachment_storage", fail_validation)

    with pytest.raises(SystemExit) as error:
        command.main()

    assert str(error.value) == "report attachment storage validation failed"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
