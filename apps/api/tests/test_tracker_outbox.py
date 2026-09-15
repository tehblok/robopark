import asyncio
import hashlib
import json

from sqlalchemy.orm import Session, sessionmaker

from robopark_api.task_workflow_models import ReliableAction, TaskAttachment, TaskMessage

TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def _action(db, actor, *, action="comment", payload=None, state="pending", lease_until=None):
    encoded = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    row = ReliableAction(
        actor_user_id=actor.id,
        resource_type="tracker_issue",
        resource_id="ROBOPARK-1",
        action=action,
        idempotency_key=f"{action}-delivery-0001",
        payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
        payload_json=encoded,
        state=state,
        next_attempt_at=0.0,
        lease_until=lease_until,
        created_at=1.0,
        updated_at=1.0,
    )
    db.add(row)
    db.commit()
    return row


def _run_one_cycle(factory, stop_event, monkeypatch):
    from robopark_api.services import tracker_outbox

    real_claim = tracker_outbox.claim_due_batch

    def claim_once(db, **kwargs):
        rows = real_claim(db, **kwargs)
        stop_event.set()
        return rows

    monkeypatch.setattr(tracker_outbox, "claim_due_batch", claim_once)
    asyncio.run(tracker_outbox.run_tracker_outbox_loop(factory, stop_event, interval_seconds=3600))


def test_worker_delivers_signed_comment_completes_action_and_invalidates_cache(
    db_engine, db_session, seed_mechanic, monkeypatch
):
    from robopark_api.services import tracker_outbox

    action = _action(db_session, seed_mechanic, payload={"text": "Готово"})
    message = TaskMessage(
        id="comment-message",
        issue_key="ROBOPARK-1",
        kind="user",
        author_user_id=seed_mechanic.id,
        author_name=seed_mechanic.username,
        text="Готово",
        action_id=action.id,
        sync_state="pending",
        created_at=1.0,
        updated_at=1.0,
    )
    db_session.add(message)
    db_session.commit()
    action_id = action.id
    delivered = []
    invalidated = []
    monkeypatch.setattr(tracker_outbox.settings_svc, "get_tracker_token", lambda db: "token")
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "get_issue",
        lambda **kwargs: {"key": "ROBOPARK-1", "tags": ["Alpha"], "status": "Open"},
    )
    monkeypatch.setattr(tracker_outbox.tracker_client, "list_comments", lambda **kwargs: [])
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "add_comment",
        lambda **kwargs: delivered.append(kwargs) or {"id": "comment-42", "text": kwargs["text"]},
    )
    monkeypatch.setattr(tracker_outbox.tracker_cache, "invalidate_issue", invalidated.append)
    stop = asyncio.Event()

    _run_one_cycle(sessionmaker(bind=db_engine, future=True), stop, monkeypatch)

    with Session(db_engine) as db:
        saved = db.get(ReliableAction, action_id)
        assert saved.state == "succeeded"
        assert json.loads(saved.result_json) == {"external_id": "comment-42"}
        assert db.get(TaskMessage, message.id).external_id == "comment-42"
    assert delivered[0]["key"] == "ROBOPARK-1"
    assert delivered[0]["text"].startswith("Готово\n\n—\n")
    assert f"surp-action:{action_id}" in delivered[0]["text"]
    assert "Время: 01.01.1970 03:00 МСК" in delivered[0]["text"]
    assert invalidated == ["ROBOPARK-1"]


def test_worker_schedules_transient_retry_and_marks_permanent_error_for_attention(
    db_engine, db_session, seed_mechanic, monkeypatch
):
    from robopark_api.services import tracker_outbox

    retry = _action(db_session, seed_mechanic, payload={"text": "one"})
    retry.idempotency_key = "retry-delivery-0001"
    permanent = _action(db_session, seed_mechanic, payload={"text": "two"})
    permanent.idempotency_key = "permanent-delivery-0001"
    permanent.next_attempt_at = 1.0
    db_session.commit()
    ids = retry.id, permanent.id
    monkeypatch.setattr(tracker_outbox.settings_svc, "get_tracker_token", lambda db: "token")
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "get_issue",
        lambda **kwargs: {"key": "ROBOPARK-1", "status": "Open"},
    )
    monkeypatch.setattr(tracker_outbox.tracker_client, "list_comments", lambda **kwargs: [])
    errors = iter(
        [
            tracker_outbox.tracker_client.TrackerError("request timeout"),
            tracker_outbox.tracker_client.TrackerError("comment create failed: 400"),
        ]
    )
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "add_comment",
        lambda **kwargs: (_ for _ in ()).throw(next(errors)),
    )
    stop = asyncio.Event()

    _run_one_cycle(sessionmaker(bind=db_engine, future=True), stop, monkeypatch)

    with Session(db_engine) as db:
        assert db.get(ReliableAction, ids[0]).state == "retry_wait"
        assert db.get(ReliableAction, ids[0]).attempts == 1
        assert db.get(ReliableAction, ids[1]).state == "needs_attention"


def test_worker_recovers_expired_transition_lease_without_duplicate_delivery(
    db_engine, db_session, seed_mechanic, monkeypatch
):
    from robopark_api.services import tracker_outbox

    action = _action(
        db_session,
        seed_mechanic,
        action="start",
        payload={},
        state="sending",
        lease_until=0.0,
    )
    action_id = action.id
    transitions = []
    monkeypatch.setattr(tracker_outbox.settings_svc, "get_tracker_token", lambda db: "token")
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "get_issue",
        lambda **kwargs: {"key": "ROBOPARK-1", "status": "В работе", "status_key": "inProgress"},
    )
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "transition_issue",
        lambda **kwargs: transitions.append(kwargs),
    )
    stop = asyncio.Event()

    _run_one_cycle(sessionmaker(bind=db_engine, future=True), stop, monkeypatch)

    with Session(db_engine) as db:
        assert db.get(ReliableAction, action_id).state == "succeeded"
    assert transitions == []


def test_worker_recognizes_return_transition_target_status_after_restart(
    db_engine, db_session, seed_mechanic, monkeypatch
):
    from robopark_api.services import tracker_outbox

    action = _action(
        db_session,
        seed_mechanic,
        action="return",
        payload={},
        state="sending",
        lease_until=0.0,
    )
    action_id = action.id
    transitions = []
    monkeypatch.setattr(tracker_outbox.settings_svc, "get_tracker_token", lambda db: "token")
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "get_issue",
        lambda **kwargs: {"key": "ROBOPARK-1", "status": "In Progress"},
    )
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "transition_issue",
        lambda **kwargs: transitions.append(kwargs),
    )
    stop = asyncio.Event()

    _run_one_cycle(sessionmaker(bind=db_engine, future=True), stop, monkeypatch)

    with Session(db_engine) as db:
        assert db.get(ReliableAction, action_id).state == "succeeded"
    assert transitions == []


def test_worker_stores_transition_missing_instead_of_choosing_arbitrary_transition(
    db_engine, db_session, seed_mechanic, monkeypatch
):
    from robopark_api.services import tracker_outbox

    action = _action(db_session, seed_mechanic, action="review", payload={})
    action_id = action.id
    monkeypatch.setattr(tracker_outbox.settings_svc, "get_tracker_token", lambda db: "token")
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "get_issue",
        lambda **kwargs: {"key": "ROBOPARK-1", "status": "In Progress"},
    )
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "list_transitions",
        lambda **kwargs: [{"id": "pause", "display": "Pause"}],
    )
    stop = asyncio.Event()

    _run_one_cycle(sessionmaker(bind=db_engine, future=True), stop, monkeypatch)

    with Session(db_engine) as db:
        saved = db.get(ReliableAction, action_id)
        assert (saved.state, saved.error_code) == (
            "needs_attention",
            "tracker_transition_missing",
        )


def test_worker_uploads_staged_attachment_by_action_id_and_adds_one_signed_comment(
    db_engine, db_session, seed_mechanic, tmp_path, monkeypatch
):
    from robopark_api.services import tracker_outbox

    message = TaskMessage(
        id="message-1",
        issue_key="ROBOPARK-1",
        kind="user",
        author_user_id=seed_mechanic.id,
        author_name=seed_mechanic.username,
        text="Фото готово",
        sync_state="pending",
        created_at=1.0,
        updated_at=1.0,
    )
    db_session.add(message)
    action = _action(
        db_session,
        seed_mechanic,
        action="attach",
        payload={"filename": "photo.png", "message_id": message.id, "mime_type": "image/png"},
    )
    blob = tmp_path / "blob"
    blob.write_bytes(b"png-bytes")
    db_session.add(
        TaskAttachment(
            id=action.id,
            message_id=message.id,
            blob_name=blob.name,
            original_name="photo.png",
            mime_type="image/png",
            size_bytes=9,
            sha256=hashlib.sha256(b"png-bytes").hexdigest(),
            created_at=1.0,
        )
    )
    db_session.commit()
    action_id = action.id
    uploaded = []
    comments = []
    monkeypatch.setattr(tracker_outbox.settings_svc, "get_tracker_token", lambda db: "token")
    monkeypatch.setattr(tracker_outbox, "staged_attachments_root", lambda: tmp_path)
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "get_issue",
        lambda **kwargs: {"key": "ROBOPARK-1", "status": "Open"},
    )
    monkeypatch.setattr(tracker_outbox.tracker_client, "list_comments", lambda **kwargs: [])
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "upload_temp_attachment",
        lambda **kwargs: uploaded.append(kwargs) or "temp-7",
    )
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "add_comment",
        lambda **kwargs: comments.append(kwargs) or {"id": "comment-7"},
    )
    stop = asyncio.Event()

    _run_one_cycle(sessionmaker(bind=db_engine, future=True), stop, monkeypatch)

    with Session(db_engine) as db:
        saved = db.get(TaskAttachment, action_id)
        assert saved.uploaded_at is not None
        assert db.get(ReliableAction, action_id).state == "succeeded"
        assert db.get(TaskMessage, message.id).sync_state == "synced"
    assert uploaded[0]["content"] == b"png-bytes"
    assert comments[0]["attachment_ids"] == ["temp-7"]
    assert comments[0]["text"].startswith("Фото готово\n\n—\n")
    assert f"surp-action:{action_id}" in comments[0]["text"]


def test_worker_reconciles_comment_accepted_before_timeout_without_posting_twice(
    db_engine, db_session, seed_mechanic, monkeypatch
):
    from robopark_api.services import tracker_outbox

    action = _action(db_session, seed_mechanic, payload={"text": "Готово"})
    message = TaskMessage(
        id="accepted-comment-message",
        issue_key=action.resource_id,
        kind="user",
        author_user_id=seed_mechanic.id,
        author_name=seed_mechanic.username,
        text="Готово",
        action_id=action.id,
        sync_state="pending",
        created_at=action.created_at,
        updated_at=action.created_at,
    )
    db_session.add(message)
    db_session.commit()
    action_id = action.id
    remote_comments = []
    post_count = 0
    monkeypatch.setattr(tracker_outbox.settings_svc, "get_tracker_token", lambda db: "token")
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "get_issue",
        lambda **kwargs: {"key": "ROBOPARK-1", "status": "Open"},
    )
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "list_comments",
        lambda **kwargs: list(remote_comments),
    )

    def accepted_then_timeout(**kwargs):
        nonlocal post_count
        post_count += 1
        remote_comments.append({"id": "accepted-42", "text": kwargs["text"]})
        raise tracker_outbox.tracker_client.TrackerError("request timeout")

    monkeypatch.setattr(tracker_outbox.tracker_client, "add_comment", accepted_then_timeout)
    factory = sessionmaker(bind=db_engine, future=True)

    tracker_outbox._process_batch(factory)
    with Session(db_engine) as db:
        retry = db.get(ReliableAction, action_id)
        assert retry.state == "retry_wait"
        retry.next_attempt_at = 0
        db.commit()

    # A timeline refresh may import the accepted Tracker comment while the
    # local action is waiting for retry/reconciliation.
    from robopark_api.services.task_timeline import merge_timeline

    with Session(db_engine) as db:
        merge_timeline(db, issue_key="ROBOPARK-1", comments=remote_comments)
        assert db.query(TaskMessage).filter_by(issue_key="ROBOPARK-1").count() == 2

    tracker_outbox._process_batch(factory)

    with Session(db_engine) as db:
        saved = db.get(ReliableAction, action_id)
        assert saved.state == "succeeded"
        assert json.loads(saved.result_json) == {"external_id": "accepted-42"}
        assert db.get(TaskMessage, message.id).external_id == "accepted-42"
        remaining = db.query(TaskMessage).filter_by(issue_key="ROBOPARK-1").all()
        assert [(item.id, item.external_id) for item in remaining] == [(message.id, "accepted-42")]
    assert post_count == 1


def test_reconciliation_reparents_staged_attachment_from_imported_twin_and_delivers_it(
    db_engine, db_session, seed_mechanic, test_settings, monkeypatch
):
    from robopark_api.services import task_timeline, tracker_outbox

    comment = _action(db_session, seed_mechanic, payload={"text": "Готово"})
    canonical = TaskMessage(
        id="canonical-comment",
        issue_key=comment.resource_id,
        kind="user",
        author_user_id=seed_mechanic.id,
        author_name=seed_mechanic.username,
        text="Готово",
        action_id=comment.id,
        sync_state="pending",
        created_at=comment.created_at,
        updated_at=comment.created_at,
    )
    db_session.add(canonical)
    db_session.commit()
    comment_id = comment.id
    canonical_id = canonical.id
    remote_comments = []
    post_count = 0
    upload_count = 0
    monkeypatch.setattr(task_timeline, "get_settings", lambda: test_settings)
    monkeypatch.setattr(tracker_outbox.settings_svc, "get_tracker_token", lambda db: "token")
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "get_issue",
        lambda **kwargs: {"key": "ROBOPARK-1", "status": "Open"},
    )
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "list_comments",
        lambda **kwargs: list(remote_comments),
    )

    def add_comment(**kwargs):
        nonlocal post_count
        post_count += 1
        external_id = "accepted-parent" if post_count == 1 else "delivered-attachment"
        remote_comments.append({"id": external_id, "text": kwargs["text"]})
        if post_count == 1:
            raise tracker_outbox.tracker_client.TrackerError("request timeout")
        return {"id": external_id, "text": kwargs["text"]}

    def upload(**kwargs):
        nonlocal upload_count
        upload_count += 1
        return "temp-reparented"

    monkeypatch.setattr(tracker_outbox.tracker_client, "add_comment", add_comment)
    monkeypatch.setattr(tracker_outbox.tracker_client, "upload_temp_attachment", upload)
    factory = sessionmaker(bind=db_engine, future=True)

    tracker_outbox._process_batch(factory)
    with Session(db_engine) as db:
        retry = db.get(ReliableAction, comment_id)
        retry.next_attempt_at = 0
        task_timeline.merge_timeline(db, issue_key="ROBOPARK-1", comments=remote_comments)
        twin = db.get(TaskMessage, "tracker:ROBOPARK-1:accepted-parent")
        actor = db.get(type(seed_mechanic), seed_mechanic.id)
        attachment, attach_action = task_timeline.stage_attachment(
            db,
            actor=actor,
            issue_key="ROBOPARK-1",
            message=twin,
            idempotency_key="reparent-attachment-0001",
            filename="evidence.png",
            content=TINY_PNG,
            content_type="image/png",
        )
        attachment_id = attachment.id
        attach_action_id = attach_action.id
        blob_path = task_timeline.staged_attachments_root() / attachment.blob_name

    tracker_outbox._process_batch(factory)

    with Session(db_engine) as db:
        assert db.get(TaskMessage, "tracker:ROBOPARK-1:accepted-parent") is None
        assert db.get(TaskMessage, canonical_id).external_id == "accepted-parent"
        attachment = db.get(TaskAttachment, attachment_id)
        assert attachment.message_id == canonical_id
        assert db.get(ReliableAction, attach_action_id).state == "succeeded"
    assert blob_path.exists()
    assert (post_count, upload_count) == (2, 1)


def test_reconciliation_keeps_all_evidence_when_external_id_conflicts(
    db_engine, db_session, seed_mechanic, monkeypatch
):
    from robopark_api.services import tracker_outbox

    action = _action(db_session, seed_mechanic, payload={"text": "canonical"})
    canonical = TaskMessage(
        id="conflict-canonical",
        issue_key=action.resource_id,
        kind="user",
        author_user_id=seed_mechanic.id,
        author_name=seed_mechanic.username,
        text="canonical",
        action_id=action.id,
        sync_state="pending",
        created_at=1.0,
        updated_at=1.0,
    )
    conflicting_local = TaskMessage(
        id="conflicting-local",
        issue_key=action.resource_id,
        kind="user",
        author_user_id=seed_mechanic.id,
        author_name=seed_mechanic.username,
        text="other evidence",
        external_id="same-remote-id",
        sync_state="synced",
        created_at=2.0,
        updated_at=2.0,
    )
    tracker_twin = TaskMessage(
        id="tracker:ROBOPARK-1:same-remote-id",
        issue_key=action.resource_id,
        kind="tracker",
        author_name="Tracker",
        text="remote evidence",
        external_id="same-remote-id",
        sync_state="synced",
        created_at=3.0,
        updated_at=3.0,
    )
    db_session.add_all([canonical, conflicting_local, tracker_twin])
    db_session.commit()
    action_id = action.id
    marker = f"surp-action:{action_id}"
    monkeypatch.setattr(tracker_outbox.settings_svc, "get_tracker_token", lambda db: "token")
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "list_comments",
        lambda **kwargs: [{"id": "same-remote-id", "text": marker}],
    )

    tracker_outbox._process_batch(sessionmaker(bind=db_engine, future=True))

    with Session(db_engine) as db:
        saved = db.get(ReliableAction, action_id)
        assert (saved.state, saved.error_code) == (
            "needs_attention",
            "timeline_external_id_conflict",
        )
        assert db.get(TaskMessage, canonical.id).external_id is None
        assert db.get(TaskMessage, conflicting_local.id) is not None
        assert db.get(TaskMessage, tracker_twin.id) is not None


def test_worker_reconciles_attachment_comment_accepted_before_timeout_without_reupload(
    db_engine, db_session, seed_mechanic, tmp_path, monkeypatch
):
    from robopark_api.services import tracker_outbox

    message = TaskMessage(
        id="accepted-attachment-message",
        issue_key="ROBOPARK-1",
        kind="system",
        author_user_id=seed_mechanic.id,
        author_name=seed_mechanic.username,
        text="Передано на проверку",
        external_id="primary-comment-7",
        sync_state="pending",
        created_at=1.0,
        updated_at=1.0,
    )
    db_session.add(message)
    action = _action(
        db_session,
        seed_mechanic,
        action="attach",
        payload={"filename": "photo.png", "message_id": message.id, "mime_type": "image/png"},
    )
    blob = tmp_path / "accepted-blob"
    blob.write_bytes(b"photo")
    db_session.add(
        TaskAttachment(
            id=action.id,
            message_id=message.id,
            blob_name=blob.name,
            original_name="photo.png",
            mime_type="image/png",
            size_bytes=5,
            sha256=hashlib.sha256(b"photo").hexdigest(),
            created_at=1.0,
        )
    )
    db_session.commit()
    action_id = action.id
    remote_comments = []
    uploads = 0
    posts = 0
    monkeypatch.setattr(tracker_outbox.settings_svc, "get_tracker_token", lambda db: "token")
    monkeypatch.setattr(tracker_outbox, "staged_attachments_root", lambda: tmp_path)
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "get_issue",
        lambda **kwargs: {"key": "ROBOPARK-1", "status": "Open"},
    )
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "list_comments",
        lambda **kwargs: list(remote_comments),
    )

    def upload(**kwargs):
        nonlocal uploads
        uploads += 1
        return "temp-accepted"

    def accepted_then_timeout(**kwargs):
        nonlocal posts
        posts += 1
        remote_comments.append({"id": "attachment-comment-42", "text": kwargs["text"]})
        raise tracker_outbox.tracker_client.TrackerError("request timeout")

    monkeypatch.setattr(tracker_outbox.tracker_client, "upload_temp_attachment", upload)
    monkeypatch.setattr(tracker_outbox.tracker_client, "add_comment", accepted_then_timeout)
    factory = sessionmaker(bind=db_engine, future=True)

    tracker_outbox._process_batch(factory)
    with Session(db_engine) as db:
        retry = db.get(ReliableAction, action_id)
        assert retry.state == "retry_wait"
        retry.next_attempt_at = 0
        db.commit()
    tracker_outbox._process_batch(factory)

    with Session(db_engine) as db:
        saved = db.get(ReliableAction, action_id)
        assert saved.state == "succeeded"
        assert json.loads(saved.result_json) == {
            "attachment_id": "temp-accepted",
            "external_id": "attachment-comment-42",
        }
        assert db.get(TaskMessage, message.id).external_id == "primary-comment-7"
    assert (uploads, posts) == (1, 1)


def test_worker_delivers_only_allowlisted_tracker_field(
    db_engine, db_session, seed_mechanic, monkeypatch
):
    from robopark_api.services import tracker_outbox

    allowed = _action(
        db_session,
        seed_mechanic,
        action="set_field",
        payload={"field": "theDefectCode", "value": "motor"},
    )
    allowed.idempotency_key = "allowed-field-0001"
    denied = _action(
        db_session,
        seed_mechanic,
        action="set_field",
        payload={"field": "assignee", "value": "someone"},
    )
    denied.idempotency_key = "denied-field-0001"
    db_session.commit()
    ids = allowed.id, denied.id
    updates = []
    monkeypatch.setattr(tracker_outbox.settings_svc, "get_tracker_token", lambda db: "token")
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "get_issue",
        lambda **kwargs: {"key": "ROBOPARK-1", "status": "Open"},
    )
    monkeypatch.setattr(tracker_outbox, "_set_issue_field", lambda **kwargs: updates.append(kwargs))
    stop = asyncio.Event()

    _run_one_cycle(sessionmaker(bind=db_engine, future=True), stop, monkeypatch)

    with Session(db_engine) as db:
        assert db.get(ReliableAction, ids[0]).state == "succeeded"
        assert db.get(ReliableAction, ids[1]).state == "needs_attention"
    assert updates == [
        {
            "token": "token",
            "key": "ROBOPARK-1",
            "field_id": "60df26695151a36df681d67b--theDefectCode",
            "value": "motor",
        }
    ]


def test_worker_returns_immediately_when_shutdown_is_already_requested():
    from robopark_api.services.tracker_outbox import run_tracker_outbox_loop

    stop = asyncio.Event()
    stop.set()

    asyncio.run(run_tracker_outbox_loop(lambda: (_ for _ in ()).throw(AssertionError()), stop))
