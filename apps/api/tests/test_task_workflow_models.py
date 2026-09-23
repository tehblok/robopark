import pytest
from sqlalchemy.exc import IntegrityError

from robopark_api.collaboration_models import TrackerClaim
from robopark_api.models import Park, Role, User
from robopark_api.task_workflow_models import (
    HiddenTask,
    ReliableAction,
    TaskAttachment,
    TaskMessage,
    TaskReview,
)


def _seed_user(db_session) -> User:
    role = Role(slug="workflow-test", name="Workflow test", description="")
    db_session.add(role)
    db_session.flush()
    user = User(
        username="workflow-test",
        password_hash="hash",
        role_id=role.id,
        access_status="approved",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _action(user_id: int, *, id: str = "action-1") -> ReliableAction:
    return ReliableAction(
        id=id,
        actor_user_id=user_id,
        resource_type="tracker_issue",
        resource_id="SDCFLEETOPS-1",
        action="claim",
        idempotency_key="request-0001",
        payload_hash="abc123",
        payload_json="{}",
        state="pending",
        attempts=0,
        next_attempt_at=0.0,
        created_at=1.0,
        updated_at=1.0,
    )


def test_reliable_action_rejects_duplicate_idempotency_scope(db_session):
    user = _seed_user(db_session)
    db_session.add(_action(user.id))
    db_session.commit()

    db_session.add(_action(user.id, id="action-2"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_reliable_action_keeps_legacy_compatible_action_length():
    assert ReliableAction.action.type.length == 32


@pytest.mark.parametrize(
    "blob_name",
    ["", "/tmp/photo.jpg", "../photo.jpg", "folder/photo.jpg", r"folder\photo.jpg", ".."],
)
def test_task_attachment_rejects_unsafe_blob_name(blob_name):
    with pytest.raises(ValueError, match="blob_name_invalid"):
        TaskAttachment(
            id="attachment-1",
            message_id="message-1",
            blob_name=blob_name,
            original_name="photo.jpg",
            mime_type="image/jpeg",
            size_bytes=10,
            sha256="a" * 64,
            created_at=1.0,
        )


@pytest.mark.parametrize("blob_name", ["../photo.jpg", r"folder\photo.jpg"])
def test_database_rejects_unsafe_blob_name_when_orm_validation_is_bypassed(db_session, blob_name):
    db_session.add(
        TaskMessage(
            id="message-1",
            issue_key="SDCFLEETOPS-1",
            kind="system",
            author_name="Robopark",
            text="Photo staged",
            sync_state="saved",
            created_at=1.0,
            updated_at=1.0,
        )
    )
    db_session.commit()

    with pytest.raises(IntegrityError):
        db_session.execute(
            TaskAttachment.__table__.insert(),
            {
                "id": "attachment-1",
                "message_id": "message-1",
                "blob_name": blob_name,
                "original_name": "photo.jpg",
                "mime_type": "image/jpeg",
                "size_bytes": 10,
                "sha256": "a" * 64,
                "created_at": 1.0,
            },
        )


@pytest.mark.parametrize(
    ("model", "kwargs", "invalid_state"),
    [
        (ReliableAction, {}, "unknown"),
        (
            TaskMessage,
            {
                "id": "message-1",
                "issue_key": "SDCFLEETOPS-1",
                "kind": "user",
                "author_name": "Worker",
                "text": "Started",
                "created_at": 1.0,
                "updated_at": 1.0,
            },
            "unknown",
        ),
    ],
)
def test_workflow_models_reject_unknown_synchronization_states(
    db_session, model, kwargs, invalid_state
):
    user = _seed_user(db_session)
    if model is ReliableAction:
        row = _action(user.id)
        row.state = invalid_state
    else:
        row = model(author_user_id=user.id, sync_state=invalid_state, **kwargs)
    db_session.add(row)

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_task_message_rejects_unknown_kind(db_session):
    user = _seed_user(db_session)
    db_session.add(
        TaskMessage(
            id="message-1",
            issue_key="SDCFLEETOPS-1",
            kind="email",
            author_user_id=user.id,
            author_name="Worker",
            text="Started",
            sync_state="saved",
            created_at=1.0,
            updated_at=1.0,
        )
    )

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_claim_and_message_visibility_defaults(db_session):
    user = _seed_user(db_session)
    park = Park(name="Workflow Park", tag="workflow-park", is_active=True)
    db_session.add(park)
    db_session.flush()
    claim = TrackerClaim(
        issue_key="SDCFLEETOPS-1",
        park_id=park.id,
        owner_user_id=user.id,
        updated_by_user_id=user.id,
        updated_at=1.0,
    )
    message = TaskMessage(
        id="message-1",
        issue_key="SDCFLEETOPS-1",
        kind="system",
        author_name="Robopark",
        text="Claim reserved",
        sync_state="saved",
        created_at=1.0,
        updated_at=1.0,
    )
    db_session.add_all([claim, message])
    db_session.flush()

    assert claim.state == "pending"
    assert claim.start_action_id is None
    assert claim.operator_user_id is None
    assert message.visibility == "participants"


@pytest.mark.parametrize(
    ("model", "invalid_value"),
    [(TrackerClaim, "finished"), (TaskMessage, "public")],
)
def test_claim_and_message_reject_unknown_visibility_states(db_session, model, invalid_value):
    user = _seed_user(db_session)
    if model is TrackerClaim:
        park = Park(name="Workflow Park", tag="workflow-park", is_active=True)
        db_session.add(park)
        db_session.flush()
        row = TrackerClaim(
            issue_key="SDCFLEETOPS-1",
            park_id=park.id,
            owner_user_id=user.id,
            updated_by_user_id=user.id,
            updated_at=1.0,
            state=invalid_value,
        )
    else:
        row = TaskMessage(
            id="message-1",
            issue_key="SDCFLEETOPS-1",
            kind="system",
            author_name="Robopark",
            text="Claim reserved",
            sync_state="saved",
            visibility=invalid_value,
            created_at=1.0,
            updated_at=1.0,
        )
    db_session.add(row)

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_only_one_open_review_and_active_hidden_row_exist_per_issue(db_session):
    user = _seed_user(db_session)
    db_session.add_all(
        [
            TaskReview(
                id="review-1",
                issue_key="SDCFLEETOPS-1",
                state="pending",
                actor_user_id=user.id,
                created_at=1.0,
                updated_at=1.0,
            ),
            HiddenTask(
                id="hidden-1",
                issue_key="SDCFLEETOPS-1",
                park_id=1,
                reason="Duplicate",
                actor_user_id=user.id,
                created_at=1.0,
                updated_at=1.0,
            ),
        ]
    )
    db_session.commit()

    db_session.add(
        TaskReview(
            id="review-2",
            issue_key="SDCFLEETOPS-1",
            state="returned",
            actor_user_id=user.id,
            created_at=2.0,
            updated_at=2.0,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    db_session.add(
        HiddenTask(
            id="hidden-2",
            issue_key="SDCFLEETOPS-1",
            park_id=1,
            reason="Still duplicate",
            actor_user_id=user.id,
            created_at=2.0,
            updated_at=2.0,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
