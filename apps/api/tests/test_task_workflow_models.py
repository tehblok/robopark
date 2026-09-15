import pytest
from sqlalchemy.exc import IntegrityError

from robopark_api.models import Role, User
from robopark_api.task_workflow_models import (
    HiddenTask,
    ReliableAction,
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
