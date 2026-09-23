import json
from itertools import pairwise

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from conftest import login_as, role_id_for
from robopark_api.collaboration_models import TrackerClaim
from robopark_api.models import AccessStatus, AuditLog, Park, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services import platform_settings, rbac
from robopark_api.task_workflow_models import (
    HiddenTask,
    ReliableAction,
    TaskAttachment,
    TaskMessage,
    TaskReview,
)

ISSUE_KEY = "ROBOPARK-51"
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def _issue():
    return {
        "key": ISSUE_KEY,
        "summary": "blocker [447]",
        "status": "В очереди",
        "status_key": "queued",
        "queue": "ROBOPARK",
        "tags": ["Alpha"],
        "created": "2026-09-15T08:00:00Z",
    }


def _prepare_tracker(db_session, monkeypatch, *, with_operator=True):
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    from robopark_api.services import tracker_client

    monkeypatch.setattr(tracker_client, "get_issue", lambda **_kwargs: _issue())
    if with_operator:
        park = db_session.scalar(select(UserPark.park_id).where(UserPark.user_id.is_not(None)))
        assert park is not None
        _operator(db_session, db_session.get(Park, park))


def _operator(db_session, park):
    current = db_session.scalar(select(User).where(User.username == "operator51"))
    if current is not None:
        return current
    user = User(
        username="operator51",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(UserPark(user_id=user.id, park_id=park.id))
    db_session.commit()
    return user


def _mechanic(db_session, park, username="mech2"):
    user = User(
        username=username,
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "mechanic"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(UserPark(user_id=user.id, park_id=park.id))
    db_session.commit()
    return user


def _claim(client, user):
    login_as(client, user.username, "secret")
    return client.post(
        f"/tracker/issues/{ISSUE_KEY}/claim",
        headers={"Idempotency-Key": "claim-task-51"},
    )


def _submit(client, *, key="review-task-51", comment=None, code="BD-01", files=None):
    data = {"defect_code": code}
    if comment is not None:
        data["comment"] = comment
    return client.post(
        f"/tracker/issues/{ISSUE_KEY}/submit-review",
        headers={"Idempotency-Key": key},
        data=data,
        files=files or [("photo", ("robot.png", PNG, "image/png"))],
    )


def test_closed_tracker_status_wins_over_stale_local_ownership(
    db_session, seed_mechanic, seed_park_with_tracker
):
    from robopark_api.services import task_lifecycle
    from robopark_api.services.tracker_claims import claim_issue

    claim_issue(
        db_session,
        actor=seed_mechanic,
        owner=seed_mechanic,
        issue_key=ISSUE_KEY,
        park_id=seed_park_with_tracker.id,
    )
    result = task_lifecycle.workflow(
        db_session,
        issue_key=ISSUE_KEY,
        viewer=seed_mechanic,
        issue={**_issue(), "status_key": "closed", "status": "Закрыта"},
    )
    assert result["display_status"] == "closed"


@pytest.mark.parametrize("queued_at", [None, "2026-09-15T09:30:00Z"])
def test_workflow_uses_actual_queue_time_not_creation_time(db_session, seed_mechanic, queued_at):
    from robopark_api.services import task_lifecycle

    result = task_lifecycle.workflow(
        db_session,
        issue_key=ISSUE_KEY,
        viewer=seed_mechanic,
        issue={**_issue(), "queued_at": queued_at},
    )
    assert result["queued_at"] == queued_at
    assert result["queued_at_source"] == ("tracker_history" if queued_at else None)


def test_cannot_claim_task_already_closed_in_tracker(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    _prepare_tracker(db_session, monkeypatch)
    from robopark_api.services import tracker_cache, tracker_client

    assert tracker_cache.get_issue(token="token", key=ISSUE_KEY)["status_key"] == "queued"

    monkeypatch.setattr(
        tracker_client,
        "get_issue",
        lambda **_kwargs: {
            **_issue(),
            "status_key": "closed",
            "status": "Закрыта",
        },
    )
    response = _claim(client, seed_mechanic)
    assert response.status_code == 409
    assert response.json()["detail"] == "task_already_closed"
    assert db_session.query(TrackerClaim).count() == 0
    assert db_session.query(ReliableAction).count() == 0


def test_failed_close_is_not_presented_as_confirmed_closure(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    _prepare_tracker(db_session, monkeypatch)
    operator = _operator(db_session, seed_park_with_tracker)
    assert _claim(client, seed_mechanic).status_code == 200
    assert _submit(client, comment="Исправлено").status_code == 200
    login_as(client, operator.username, "secret")
    result = client.post(
        f"/tracker/issues/{ISSUE_KEY}/review/approve",
        headers={"Idempotency-Key": "close-not-confirmed"},
    )
    assert result.status_code == 200
    assert result.json()["workflow"]["display_status"] == "closing"
    action = db_session.scalar(select(ReliableAction).where(ReliableAction.action == "close"))
    action.state = "needs_attention"
    action.error_code = "tracker_transition_missing"
    db_session.commit()
    result = client.get(f"/tracker/issues/{ISSUE_KEY}")
    assert result.json()["workflow"]["display_status"] == "closing"
    assert result.json()["workflow"]["sync_error_code"] == "tracker_transition_missing"
    login_as(client, seed_mechanic.username, "secret")
    blocked = client.post(
        f"/tracker/issues/{ISSUE_KEY}/claim",
        headers={"Idempotency-Key": "claim-before-close-delivered"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "task_closing_pending"
    assert db_session.query(TrackerClaim).count() == 0
    action.state = "succeeded"
    db_session.commit()
    # Queue automation can move a successfully transitioned ticket elsewhere.
    result = client.get(f"/tracker/issues/{ISSUE_KEY}")
    assert result.json()["workflow"]["display_status"] == "closing"


def test_external_tracker_close_finishes_local_review_and_claim_once(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    _prepare_tracker(db_session, monkeypatch)
    operator = _operator(db_session, seed_park_with_tracker)
    assert _claim(client, seed_mechanic).status_code == 200
    assert _submit(client, comment="Исправлено").status_code == 200
    from robopark_api.services import tracker_cache, tracker_client

    monkeypatch.setattr(
        tracker_client,
        "get_issue",
        lambda **_kwargs: {**_issue(), "status": "Закрыта", "status_key": "closed"},
    )
    tracker_cache.invalidate_issue(ISSUE_KEY)
    login_as(client, operator.username, "secret")
    first = client.get(f"/tracker/issues/{ISSUE_KEY}")
    second = client.get(f"/tracker/issues/{ISSUE_KEY}")

    assert first.status_code == second.status_code == 200
    assert first.json()["workflow"]["display_status"] == "closed"
    assert second.json()["workflow"]["display_status"] == "closed"
    assert db_session.query(TaskReview).one().state == "closed"
    assert db_session.query(TrackerClaim).count() == 0
    assert (
        db_session.query(TaskMessage)
        .filter(TaskMessage.external_id == "tracker-external-close")
        .count()
        == 1
    )


def test_claim_is_atomic_idempotent_and_never_calls_tracker_mutations(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    _prepare_tracker(db_session, monkeypatch)
    from robopark_api.services import tracker_client

    operator = db_session.scalar(select(User).where(User.username == "operator51"))
    operator.tracker_login = "operator.tracker"
    db_session.commit()

    monkeypatch.setattr(
        tracker_client,
        "transition_issue",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("request wrote to Tracker")),
    )

    first = _claim(client, seed_mechanic)
    second = _claim(client, seed_mechanic)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["sync_state"] == "pending"
    assert db_session.query(TrackerClaim).count() == 1
    assert db_session.query(TaskMessage).count() == 1
    actions = list(db_session.scalars(select(ReliableAction).order_by(ReliableAction.created_at)))
    assert [row.action for row in actions] == [
        "assign_operator",
        "ensure_tag",
        "ensure_components",
        "start",
    ]
    assert json.loads(actions[0].payload_json)["login"] == "operator.tracker"
    assert json.loads(actions[1].payload_json)["depends_on_action_ids"] == [actions[0].id]
    assert json.loads(actions[2].payload_json)["depends_on_action_ids"] == [actions[1].id]
    assert json.loads(actions[3].payload_json)["depends_on_action_ids"] == [actions[2].id]
    claim = db_session.get(TrackerClaim, ISSUE_KEY)
    assert claim.state == "pending"
    assert claim.start_action_id == actions[3].id
    assert claim.operator_user_id is not None
    assert "Задача взята в работу" in db_session.query(TaskMessage).one().text


def test_claim_rejects_before_mutation_when_park_has_no_operator(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    _prepare_tracker(db_session, monkeypatch, with_operator=False)

    response = _claim(client, seed_mechanic)

    assert response.status_code == 409
    assert response.json()["detail"] == "task_claim_operator_unavailable"
    assert db_session.query(TrackerClaim).count() == 0
    assert db_session.query(ReliableAction).count() == 0


def test_second_mechanic_cannot_replace_pending_claim_reservation(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    _prepare_tracker(db_session, monkeypatch)
    other = _mechanic(db_session, seed_park_with_tracker, username="competing-mechanic")
    assert _claim(client, seed_mechanic).status_code == 200

    login_as(client, other.username, "secret")
    response = client.post(
        f"/tracker/issues/{ISSUE_KEY}/claim",
        headers={"Idempotency-Key": "competing-claim"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "tracker_issue_claim_pending"
    assert db_session.get(TrackerClaim, ISSUE_KEY).owner_user_id == seed_mechanic.id
    assert db_session.query(ReliableAction).count() == 4


def test_claim_takeover_changes_owner_once_and_names_both_mechanics(
    client, db_session, seed_mechanic, seed_royal, seed_park_with_tracker, monkeypatch
):
    _prepare_tracker(db_session, monkeypatch)
    from robopark_api.services.tracker_claims import claim_issue

    claim_issue(
        db_session,
        actor=seed_royal,
        owner=seed_royal,
        issue_key=ISSUE_KEY,
        park_id=seed_park_with_tracker.id,
    )
    response = _claim(client, seed_mechanic)

    assert response.status_code == 200
    claim = db_session.get(TrackerClaim, ISSUE_KEY)
    assert claim.owner_user_id == seed_mechanic.id
    message = db_session.query(TaskMessage).one()
    assert seed_royal.username in message.text
    assert seed_mechanic.username in message.text


def test_claim_requires_tracker_write_permission(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    _prepare_tracker(db_session, monkeypatch)
    desired = rbac.role_permission_keys(db_session, seed_mechanic) - {rbac.PERMISSION_TRACKER_WRITE}
    rbac.set_user_effective_permissions(db_session, seed_mechanic, desired)

    response = _claim(client, seed_mechanic)

    assert response.status_code == 403
    assert db_session.get(TrackerClaim, ISSUE_KEY) is None


def test_handoff_replay_changes_owner_and_writes_one_message(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    next_mechanic = _mechanic(db_session, seed_park_with_tracker)
    _prepare_tracker(db_session, monkeypatch)
    assert _claim(client, seed_mechanic).status_code == 200
    before = db_session.query(TaskMessage).count()

    first = client.post(
        f"/tracker/issues/{ISSUE_KEY}/handoff",
        headers={"Idempotency-Key": "handoff-task-51"},
        json={
            "assignee": next_mechanic.username,
            "reason": "Смена закончилась",
            "done": "Заменён мотор",
            "remaining": "Проверить",
            "obstacles": "",
        },
    )
    replay = client.post(
        f"/tracker/issues/{ISSUE_KEY}/handoff",
        headers={"Idempotency-Key": "handoff-task-51"},
        json={
            "assignee": next_mechanic.username,
            "reason": "Смена закончилась",
            "done": "Заменён мотор",
            "remaining": "Проверить",
            "obstacles": "",
        },
    )

    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert db_session.get(TrackerClaim, ISSUE_KEY).owner_user_id == next_mechanic.id
    assert db_session.query(TaskMessage).count() == before + 1
    message = db_session.query(TaskMessage).order_by(TaskMessage.created_at.desc()).first()
    assert "Смена закончилась" in message.text
    assert "Заменён мотор" in message.text


def test_workflow_exposes_current_cycle_comment_eligibility(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    _prepare_tracker(db_session, monkeypatch)
    assert _claim(client, seed_mechanic).status_code == 200
    before = client.get(f"/tracker/issues/{ISSUE_KEY}")
    assert before.status_code == 200
    assert before.json()["workflow"]["has_current_cycle_comment"] is False
    posted = client.post(
        f"/tracker/issues/{ISSUE_KEY}/messages",
        headers={"Idempotency-Key": "cycle-comment-51"},
        json={"text": "Заменил датчик"},
    )
    assert posted.status_code == 201
    after = client.get(f"/tracker/issues/{ISSUE_KEY}")
    assert after.json()["workflow"]["has_current_cycle_comment"] is True


def test_submit_review_requires_current_cycle_comment_one_known_code_and_one_valid_image(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    _prepare_tracker(db_session, monkeypatch)
    assert _claim(client, seed_mechanic).status_code == 200

    missing_comment = _submit(client, key="review-no-comment")
    assert missing_comment.status_code == 400
    assert missing_comment.json()["detail"] == "task_completion_comment_required"

    old = TaskMessage(
        id="old-comment",
        issue_key=ISSUE_KEY,
        kind="user",
        author_user_id=seed_mechanic.id,
        author_name=seed_mechanic.username,
        text="Старая работа",
        sync_state="saved",
        created_at=db_session.get(TrackerClaim, ISSUE_KEY).updated_at - 1,
        updated_at=db_session.get(TrackerClaim, ISSUE_KEY).updated_at - 1,
    )
    db_session.add(old)
    db_session.commit()
    assert _submit(client, key="review-old-comment").status_code == 400

    assert (
        _submit(client, key="review-bad-code", comment="Заменил деталь", code="BAD").status_code
        == 422
    )
    duplicate = _submit(
        client,
        key="review-two-photos",
        comment="Заменил деталь",
        files=[
            ("photo", ("one.png", PNG, "image/png")),
            ("photo", ("two.png", PNG, "image/png")),
        ],
    )
    assert duplicate.status_code == 400
    assert duplicate.json()["detail"] == "task_review_exactly_one_photo"
    invalid = _submit(
        client,
        key="review-bad-photo",
        comment="Заменил деталь",
        files=[("photo", ("robot.gif", b"GIF89a", "image/gif"))],
    )
    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "task_attachment_invalid_type"
    fake_png = _submit(
        client,
        key="review-fake-png",
        comment="Заменил деталь",
        files=[("photo", ("robot.png", b"not an image", "image/png"))],
    )
    assert fake_png.status_code == 400
    assert fake_png.json()["detail"] == "task_attachment_invalid_type"
    assert db_session.query(TaskReview).count() == 0


def test_submit_review_stages_one_photo_and_all_bot_actions_once(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    _operator(db_session, seed_park_with_tracker)
    _prepare_tracker(db_session, monkeypatch)
    assert _claim(client, seed_mechanic).status_code == 200

    response = _submit(client, comment="Заменил бампер")
    replay = _submit(client, comment="Заменил бампер")

    assert response.status_code == replay.status_code == 200
    assert response.json() == replay.json()
    assert db_session.query(TaskReview).one().state == "pending"
    assert db_session.query(TaskAttachment).count() == 1
    assert db_session.get(TrackerClaim, ISSUE_KEY).owner_user_id == seed_mechanic.id
    actions = db_session.query(ReliableAction).filter_by(resource_id=ISSUE_KEY).all()
    assert sorted(action.action for action in actions) == [
        "attach",
        "comment",
        "review",
        "set_field",
        "start",
    ]
    field = next(action for action in actions if action.action == "set_field")
    assert json.loads(field.payload_json) == {"field": "theDefectCode", "value": "BD-01"}
    review_action = next(action for action in actions if action.action == "review")
    assert json.loads(review_action.payload_json)["depends_on_actions"] == [
        "comment",
        "attach",
        "set_field",
    ]
    messages = db_session.query(TaskMessage).filter_by(issue_key=ISSUE_KEY).all()
    assert sum(message.text == "Заменил бампер" for message in messages) == 1
    automatic = next(message for message in messages if "Передано на проверку" in message.text)
    assert "BD-01" in automatic.text
    assert "operator51" in automatic.text


def test_rapid_review_lifecycle_builds_one_causal_transition_chain(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    operator = _operator(db_session, seed_park_with_tracker)
    _prepare_tracker(db_session, monkeypatch)
    assert _claim(client, seed_mechanic).status_code == 200
    assert _submit(client, key="review-chain-1", comment="Починил").status_code == 200

    login_as(client, operator.username, "secret")
    returned = client.post(
        f"/tracker/issues/{ISSUE_KEY}/review/return",
        headers={"Idempotency-Key": "return-chain-1"},
        json={"reason": "Нужно переснять"},
    )
    assert returned.status_code == 200

    login_as(client, seed_mechanic.username, "secret")
    # Replaying an older command after a newer transition must retain its original chain edge.
    assert _submit(client, key="review-chain-1", comment="Починил").status_code == 200
    assert _submit(client, key="review-chain-2", comment="Переснял").status_code == 200

    login_as(client, operator.username, "secret")
    approved = client.post(
        f"/tracker/issues/{ISSUE_KEY}/review/approve",
        headers={"Idempotency-Key": "approve-chain-1"},
    )
    assert approved.status_code == 200

    transitions = (
        db_session.query(ReliableAction)
        .filter(
            ReliableAction.resource_id == ISSUE_KEY,
            ReliableAction.action.in_(("start", "review", "return", "close")),
        )
        .order_by(ReliableAction.created_at, ReliableAction.id)
        .all()
    )
    assert [row.action for row in transitions] == ["start", "review", "return", "review", "close"]
    for previous, current in pairwise(transitions):
        assert json.loads(current.payload_json)["depends_on_action_ids"] == [previous.id]
    first_review_payload = json.loads(transitions[1].payload_json)
    second_review_payload = json.loads(transitions[3].payload_json)
    assert first_review_payload["depends_on_actions"] == ["comment", "attach", "set_field"]
    assert second_review_payload["depends_on_actions"] == ["comment", "attach", "set_field"]


def test_submit_review_requires_attachment_permission_before_queuing_any_actions(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    _prepare_tracker(db_session, monkeypatch)
    assert _claim(client, seed_mechanic).status_code == 200
    desired = rbac.role_permission_keys(db_session, seed_mechanic) - {
        rbac.PERMISSION_TRACKER_ATTACH
    }
    rbac.set_user_effective_permissions(db_session, seed_mechanic, desired)
    before = db_session.query(ReliableAction).count()

    response = _submit(client, key="review-no-attach-permission", comment="Готово")

    assert response.status_code == 403
    assert db_session.query(ReliableAction).count() == before


def test_existing_current_cycle_comment_allows_omitting_optional_comment(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    _prepare_tracker(db_session, monkeypatch)
    assert _claim(client, seed_mechanic).status_code == 200
    boundary = db_session.get(TrackerClaim, ISSUE_KEY).updated_at
    db_session.add(
        TaskMessage(
            id="current-comment",
            issue_key=ISSUE_KEY,
            kind="user",
            author_user_id=seed_mechanic.id,
            author_name=seed_mechanic.username,
            text="Работа завершена",
            sync_state="saved",
            created_at=boundary,
            updated_at=boundary,
        )
    )
    db_session.commit()

    response = _submit(client, key="review-existing-comment")

    assert response.status_code == 200
    assert db_session.query(ReliableAction).filter_by(action="comment").count() == 0


def test_whitespace_only_current_cycle_comment_does_not_satisfy_review(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    _prepare_tracker(db_session, monkeypatch)
    assert _claim(client, seed_mechanic).status_code == 200
    boundary = db_session.get(TrackerClaim, ISSUE_KEY).updated_at
    db_session.add(
        TaskMessage(
            id="whitespace-comment",
            issue_key=ISSUE_KEY,
            kind="user",
            author_user_id=seed_mechanic.id,
            author_name=seed_mechanic.username,
            text=" \t\n ",
            sync_state="saved",
            created_at=boundary,
            updated_at=boundary,
        )
    )
    db_session.commit()

    response = _submit(client, key="review-whitespace-comment")

    assert response.status_code == 400
    assert response.json()["detail"] == "task_completion_comment_required"


def test_returned_review_rejects_duplicate_return_and_approval(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    operator = _operator(db_session, seed_park_with_tracker)
    next_mechanic = _mechanic(db_session, seed_park_with_tracker, username="replay-target")
    _prepare_tracker(db_session, monkeypatch)
    assert _claim(client, seed_mechanic).status_code == 200
    assert _submit(client, comment="Починил").status_code == 200

    denied = client.post(
        f"/tracker/issues/{ISSUE_KEY}/review/approve",
        headers={"Idempotency-Key": "approve-denied-51"},
    )
    assert denied.status_code == 403

    login_as(client, operator.username, "secret")
    returned = client.post(
        f"/tracker/issues/{ISSUE_KEY}/review/return",
        headers={"Idempotency-Key": "return-task-51"},
        json={"reason": "Нужно переснять", "assignee": next_mechanic.username},
    )
    assert returned.status_code == 200
    assert db_session.query(TaskReview).one().state == "returned"
    assert db_session.get(TrackerClaim, ISSUE_KEY).owner_user_id == next_mechanic.id

    duplicate_return = client.post(
        f"/tracker/issues/{ISSUE_KEY}/review/return",
        headers={"Idempotency-Key": "return-again-51"},
        json={"reason": "Ещё раз"},
    )
    approved = client.post(
        f"/tracker/issues/{ISSUE_KEY}/review/approve",
        headers={"Idempotency-Key": "approve-task-51"},
    )
    assert duplicate_return.status_code == 409
    assert approved.status_code == 409

    next_mechanic.is_active = False
    db_session.delete(db_session.query(UserPark).filter_by(user_id=next_mechanic.id).one())
    review = db_session.query(TaskReview).one()
    review.state = "pending"
    review.return_reason = None
    db_session.commit()

    replay = client.post(
        f"/tracker/issues/{ISSUE_KEY}/review/return",
        headers={"Idempotency-Key": "return-task-51"},
        json={"reason": "Нужно переснять", "assignee": next_mechanic.username},
    )
    conflict = client.post(
        f"/tracker/issues/{ISSUE_KEY}/review/return",
        headers={"Idempotency-Key": "return-task-51"},
        json={"reason": "Другая причина", "assignee": next_mechanic.username},
    )
    assert replay.status_code == 200
    assert replay.json() == returned.json()
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "reliable_action_payload_conflict"
    assert db_session.query(TaskReview).one().state == "pending"
    assert db_session.get(TrackerClaim, ISSUE_KEY) is not None
    assert db_session.query(ReliableAction).filter_by(action="close").count() == 0


def test_pending_review_approval_releases_claim(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    operator = _operator(db_session, seed_park_with_tracker)
    _prepare_tracker(db_session, monkeypatch)
    assert _claim(client, seed_mechanic).status_code == 200
    assert _submit(client, comment="Починил").status_code == 200
    login_as(client, operator.username, "secret")

    approved = client.post(
        f"/tracker/issues/{ISSUE_KEY}/review/approve",
        headers={"Idempotency-Key": "approve-pending-51"},
    )

    assert approved.status_code == 200
    assert db_session.query(TaskReview).one().state == "closed"
    assert db_session.get(TrackerClaim, ISSUE_KEY) is None


def test_approve_replay_returns_cycle_one_result_without_mutating_cycle_two(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    operator = _operator(db_session, seed_park_with_tracker)
    _prepare_tracker(db_session, monkeypatch)
    assert _claim(client, seed_mechanic).status_code == 200
    assert _submit(client, key="review-cycle-one", comment="Починил").status_code == 200

    login_as(client, operator.username, "secret")
    first = client.post(
        f"/tracker/issues/{ISSUE_KEY}/review/approve",
        headers={"Idempotency-Key": "approve-across-cycles"},
    )
    assert first.status_code == 200

    close_action = db_session.scalar(select(ReliableAction).where(ReliableAction.action == "close"))
    close_action.state = "succeeded"
    db_session.commit()
    login_as(client, seed_mechanic.username, "secret")
    claimed = client.post(
        f"/tracker/issues/{ISSUE_KEY}/claim",
        headers={"Idempotency-Key": "claim-cycle-two"},
    )
    assert claimed.status_code == 200
    assert claimed.json()["workflow"]["display_status"] == "in_progress"
    assert claimed.json()["workflow"]["review_state"] is None
    assert _submit(client, key="review-cycle-two", comment="Починил ещё").status_code == 200
    cycle_two = db_session.query(TaskReview).order_by(TaskReview.created_at.desc()).first()
    assert cycle_two.state == "pending"
    cycle_two_id = cycle_two.id

    login_as(client, operator.username, "secret")
    replay = client.post(
        f"/tracker/issues/{ISSUE_KEY}/review/approve",
        headers={"Idempotency-Key": "approve-across-cycles"},
    )

    assert replay.status_code == 200
    assert replay.json() == first.json()
    db_session.expire_all()
    assert db_session.get(TaskReview, cycle_two_id).state == "pending"
    assert db_session.get(TrackerClaim, ISSUE_KEY) is not None
    assert db_session.query(ReliableAction).filter_by(action="close").count() == 1


def test_concurrent_return_and_approve_create_only_the_winning_transition(
    client,
    db_engine,
    db_session,
    seed_mechanic,
    seed_royal,
    seed_park_with_tracker,
    monkeypatch,
):
    operator = _operator(db_session, seed_park_with_tracker)
    _prepare_tracker(db_session, monkeypatch)
    assert _claim(client, seed_mechanic).status_code == 200
    assert _submit(client, key="review-cas", comment="Починил").status_code == 200

    from robopark_api.services import task_lifecycle

    # Preserve the pending row read by the losing request before the winner commits.
    with Session(db_engine) as snapshot_db:
        stale_review = task_lifecycle._active_review(snapshot_db, ISSUE_KEY)
        snapshot_db.expunge(stale_review)

    with Session(db_engine) as winner_db:
        task_lifecycle.return_review(
            winner_db,
            actor=winner_db.get(User, operator.id),
            issue_key=ISSUE_KEY,
            reason="Переснять",
            assignee=None,
            idempotency_key="concurrent-return",
        )

    monkeypatch.setattr(task_lifecycle, "_active_review", lambda *_args, **_kwargs: stale_review)
    with Session(db_engine) as loser_db:
        try:
            task_lifecycle.approve_review(
                loser_db,
                actor=loser_db.get(User, seed_royal.id),
                issue_key=ISSUE_KEY,
                idempotency_key="concurrent-approve",
            )
        except HTTPException as exc:
            assert exc.status_code == 409
        else:
            raise AssertionError("stale concurrent approval won the pending-state CAS")

    with Session(db_engine) as db:
        transitions = db.scalars(
            select(ReliableAction).where(
                ReliableAction.resource_id == ISSUE_KEY,
                ReliableAction.action.in_(("return", "close")),
            )
        ).all()
        assert len(transitions) == 1
        review = db.query(TaskReview).one()
        assert (transitions[0].action, review.state) == ("return", "returned")


def test_operator_return_can_reassign_the_mechanic_and_starts_a_new_comment_cycle(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    operator = _operator(db_session, seed_park_with_tracker)
    next_mechanic = _mechanic(db_session, seed_park_with_tracker)
    _prepare_tracker(db_session, monkeypatch)
    assert _claim(client, seed_mechanic).status_code == 200
    assert _submit(client, comment="Починил").status_code == 200
    prior_boundary = db_session.get(TrackerClaim, ISSUE_KEY).updated_at

    login_as(client, operator.username, "secret")
    response = client.post(
        f"/tracker/issues/{ISSUE_KEY}/review/return",
        headers={"Idempotency-Key": "return-reassign-51"},
        json={"reason": "Нужно переснять", "assignee": next_mechanic.username},
    )

    assert response.status_code == 200
    claim = db_session.get(TrackerClaim, ISSUE_KEY)
    assert claim.owner_user_id == next_mechanic.id
    assert claim.updated_at > prior_boundary


def test_hide_restore_is_manager_only_filters_before_counts_and_writes_audit(
    client,
    db_session,
    seed_mechanic,
    seed_admin,
    seed_park_with_tracker,
    monkeypatch,
):
    _prepare_tracker(db_session, monkeypatch)
    from robopark_api.services import tracker_client

    monkeypatch.setattr(tracker_client, "search_issues", lambda **_kwargs: [_issue()])
    login_as(client, seed_mechanic.username, "secret")
    denied = client.post(
        f"/tracker/issues/{ISSUE_KEY}/hide",
        headers={"Idempotency-Key": "hide-denied-51"},
        json={"reason": "Дубль"},
    )
    assert denied.status_code == 403

    login_as(client, seed_admin.username, "secret")
    hidden = client.post(
        f"/tracker/issues/{ISSUE_KEY}/hide",
        headers={"Idempotency-Key": "hide-task-51"},
        json={"reason": "Дубль"},
    )
    assert hidden.status_code == 200
    assert client.get("/tracker/issues").json()["total"] == 0
    assert client.get(f"/tracker/issues/{ISSUE_KEY}").status_code == 404
    visible = client.get("/tracker/issues?include_hidden=true").json()
    assert visible["total"] == 1
    detail = client.get(f"/tracker/issues/{ISSUE_KEY}?include_hidden=true").json()
    assert detail["workflow"]["hidden"]["reason"] == "Дубль"
    local_actions = db_session.query(ReliableAction).filter_by(resource_id=ISSUE_KEY).all()
    assert [(row.action, row.state) for row in local_actions] == [("hide", "succeeded")]
    assert db_session.query(AuditLog).filter_by(action="tracker.hide").count() == 1

    hidden_requests = [
        ("get", f"/tracker/issues/{ISSUE_KEY}/timeline", {}),
        ("get", f"/tracker/issues/{ISSUE_KEY}/comments", {}),
        ("get", f"/tracker/transitions/{ISSUE_KEY}", {}),
        ("post", f"/tracker/issues/{ISSUE_KEY}/messages", {"json": {"text": "x"}}),
        (
            "post",
            f"/tracker/issues/{ISSUE_KEY}/message-attachments",
            {
                "data": {"message_id": "hidden-message"},
                "files": {"file": ("x.png", PNG, "image/png")},
            },
        ),
        ("post", f"/tracker/issues/{ISSUE_KEY}/comment", {"json": {"text": "x"}}),
        (
            "post",
            f"/tracker/issues/{ISSUE_KEY}/attachments",
            {"files": {"file": ("x.png", PNG, "image/png")}},
        ),
        ("post", f"/tracker/issues/{ISSUE_KEY}/claim", {}),
    ]
    for method, url, kwargs in hidden_requests:
        response = getattr(client, method)(url, **kwargs)
        assert response.status_code == 404, (method, url, response.text)

    restored = client.delete(
        f"/tracker/issues/{ISSUE_KEY}/hide",
        headers={"Idempotency-Key": "restore-task-51"},
    )
    assert restored.status_code == 200
    row = db_session.query(HiddenTask).one()
    assert row.reason == "Дубль"
    assert row.actor_user_id == seed_admin.id
    assert row.restored_by_user_id == seed_admin.id
    assert client.get("/tracker/issues").json()["total"] == 1


def test_manager_retry_now_is_idempotent_and_preserves_failed_action_history(
    client,
    db_session,
    seed_mechanic,
    seed_admin,
    seed_park_with_tracker,
    monkeypatch,
):
    _prepare_tracker(db_session, monkeypatch)
    failed = ReliableAction(
        actor_user_id=seed_mechanic.id,
        resource_type="tracker_issue",
        resource_id=ISSUE_KEY,
        action="start",
        idempotency_key="failed-start-51",
        payload_hash="0" * 64,
        payload_json="{}",
        state="needs_attention",
        error_code="missing_transition",
        attempts=3,
        next_attempt_at=999.0,
        created_at=1.0,
        updated_at=2.0,
    )
    db_session.add(failed)
    db_session.commit()

    login_as(client, seed_mechanic.username, "secret")
    denied = client.post(
        f"/tracker/issues/{ISSUE_KEY}/retry-now",
        headers={"Idempotency-Key": "retry-now-task-51"},
    )
    assert denied.status_code == 403

    login_as(client, seed_admin.username, "secret")
    first = client.post(
        f"/tracker/issues/{ISSUE_KEY}/retry-now",
        headers={"Idempotency-Key": "retry-now-task-51"},
    )
    replay = client.post(
        f"/tracker/issues/{ISSUE_KEY}/retry-now",
        headers={"Idempotency-Key": "retry-now-task-51"},
    )

    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    db_session.refresh(failed)
    assert (failed.state, failed.error_code, failed.attempts) == (
        "retry_wait",
        "missing_transition",
        3,
    )
    controls = (
        db_session.query(ReliableAction)
        .filter_by(resource_type="task_control", resource_id=ISSUE_KEY, action="retry_now")
        .all()
    )
    assert len(controls) == 1
    assert controls[0].state == "succeeded"
    assert db_session.query(AuditLog).filter_by(action="tracker.retry_now").count() == 1
