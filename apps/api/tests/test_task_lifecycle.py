import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from conftest import login_as, role_id_for
from robopark_api.collaboration_models import TrackerClaim
from robopark_api.db import get_db
from robopark_api.models import AccessStatus, AuditLog, Park, User, UserPark
from robopark_api.schedule_models import ScheduleEntry
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


def _prepare_tracker(db_session, monkeypatch, *, with_operator=True, operator_on_shift=True):
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    from robopark_api.services import tracker_client

    monkeypatch.setattr(tracker_client, "get_issue", lambda **_kwargs: _issue())
    if with_operator:
        park = db_session.scalar(select(UserPark.park_id).where(UserPark.user_id.is_not(None)))
        assert park is not None
        operator = _operator(db_session, db_session.get(Park, park))
        if operator_on_shift:
            now = datetime.now(UTC)
            db_session.add(
                ScheduleEntry(
                    owner_user_id=operator.id,
                    park_id=park,
                    kind="shift",
                    start_at=now - timedelta(hours=1),
                    end_at=now + timedelta(hours=1),
                    created_by_user_id=operator.id,
                    updated_by_user_id=operator.id,
                )
            )
            db_session.commit()


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


def _activate_claim(db_session):
    claim = db_session.get(TrackerClaim, ISSUE_KEY)
    assert claim is not None
    claim.state = "active"
    db_session.commit()


def _submit(
    client,
    *,
    key="review-task-51",
    comment=None,
    code="BD-01",
    files=None,
    activate_claim=True,
):
    if activate_claim:
        # Most lifecycle tests model an already delivered start transition.
        dependency = client.app.dependency_overrides[get_db]
        session_generator = dependency()
        db = next(session_generator)
        claim = db.get(TrackerClaim, ISSUE_KEY)
        if claim is not None and claim.state == "pending":
            claim.state = "active"
            db.commit()
        session_generator.close()
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


def test_workflow_exposes_failed_operator_assignment_before_dependency_error(
    db_session, seed_mechanic
):
    from robopark_api.services import task_lifecycle
    from robopark_api.services.reliable_actions import begin_action, mark_needs_attention

    for action_name, error_code in (
        ("assign_operator", "tracker_error"),
        ("review", "prerequisite_failed"),
    ):
        action = begin_action(
            db_session,
            actor=seed_mechanic,
            resource_type="tracker_issue",
            resource_id=ISSUE_KEY,
            action=action_name,
            idempotency_key=f"failure-{action_name}",
            payload={},
        ).row
        mark_needs_attention(db_session, action, error_code=error_code)
    db_session.commit()

    result = task_lifecycle.workflow(
        db_session,
        issue_key=ISSUE_KEY,
        viewer=seed_mechanic,
        issue=_issue(),
    )

    assert result["sync_state"] == "needs_attention"
    assert result["sync_error_code"] == "tracker_operator_assignment_failed"


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


def test_tracker_external_close_resolves_pending_close_before_reopened_claim(
    client,
    db_session,
    seed_mechanic,
    seed_park_with_tracker,
    monkeypatch,
):
    _prepare_tracker(db_session, monkeypatch)
    operator = _operator(db_session, seed_park_with_tracker)
    assert _claim(client, seed_mechanic).status_code == 200
    assert _submit(client, comment="Исправлено").status_code == 200
    login_as(client, operator.username, "secret")
    assert (
        client.post(
            f"/tracker/issues/{ISSUE_KEY}/review/approve",
            headers={"Idempotency-Key": "close-external-51"},
        ).status_code
        == 200
    )
    close_action = db_session.scalar(
        select(ReliableAction).where(
            ReliableAction.resource_id == ISSUE_KEY,
            ReliableAction.action == "close",
        )
    )
    close_action.state = "needs_attention"
    db_session.commit()

    from robopark_api.services import tracker_cache, tracker_client

    monkeypatch.setattr(
        tracker_client,
        "get_issue",
        lambda **kw: {
            **_issue(),
            "status": "Закрыта",
            "status_key": "closed",
        },
    )
    tracker_cache.invalidate_issue(ISSUE_KEY)
    assert client.get(f"/tracker/issues/{ISSUE_KEY}").status_code == 200
    db_session.refresh(close_action)
    assert close_action.state == "succeeded"

    monkeypatch.setattr(tracker_client, "get_issue", lambda **kw: _issue())
    tracker_cache.invalidate_issue(ISSUE_KEY)
    login_as(client, seed_mechanic.username, "secret")
    reopened = client.post(
        f"/tracker/issues/{ISSUE_KEY}/claim",
        headers={"Idempotency-Key": "claim-reopened-51"},
    )
    assert reopened.status_code == 200
    assert db_session.get(TrackerClaim, ISSUE_KEY) is not None


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
        .filter(TaskMessage.external_id.like("tracker-external-close:%"))
        .count()
        == 1
    )


def test_external_tracker_close_after_reopen_finishes_each_repair_cycle(
    db_session,
    seed_mechanic,
    seed_park_with_tracker,
):
    from robopark_api.services import task_lifecycle
    from robopark_api.services.tracker_claims import claim_issue, get_claim

    for _ in range(2):
        claim_issue(
            db_session,
            actor=seed_mechanic,
            owner=seed_mechanic,
            issue_key=ISSUE_KEY,
            park_id=seed_park_with_tracker.id,
        )
        db_session.commit()
        task_lifecycle.reconcile_external_closure(
            db_session,
            {"key": ISSUE_KEY, "status_key": "closed"},
        )
        assert get_claim(db_session, ISSUE_KEY) is None

    messages = db_session.scalars(
        select(TaskMessage).where(
            TaskMessage.issue_key == ISSUE_KEY,
            TaskMessage.external_id.like("tracker-external-close:%"),
        )
    ).all()
    assert len(messages) == 2
    assert len({message.external_id for message in messages}) == 2
    assert (
        len(
            db_session.scalars(
                select(AuditLog).where(
                    AuditLog.target_id == ISSUE_KEY,
                    AuditLog.action == "task.external_close",
                )
            ).all()
        )
        == 2
    )


def test_external_close_marks_cycle_with_only_delivered_local_message(
    db_session,
    seed_mechanic,
):
    from robopark_api.services import task_lifecycle

    db_session.add(
        TaskMessage(
            id="delivered-before-close",
            issue_key=ISSUE_KEY,
            kind="user",
            author_user_id=seed_mechanic.id,
            author_name=seed_mechanic.username,
            text="Ремонт завершён",
            sync_state="synced",
            created_at=1,
            updated_at=1,
        )
    )
    db_session.commit()

    for _ in range(2):
        task_lifecycle.reconcile_external_closure(
            db_session,
            {"key": ISSUE_KEY, "status_key": "closed"},
        )

    markers = db_session.scalars(
        select(TaskMessage).where(
            TaskMessage.issue_key == ISSUE_KEY,
            TaskMessage.external_id.like("tracker-external-close:%"),
        )
    ).all()
    assert len(markers) == 1
    assert markers[0].created_at > 1


def test_reopened_workflow_only_reports_sync_from_current_repair_cycle(
    db_session,
    seed_mechanic,
    seed_park_with_tracker,
):
    from robopark_api.services import task_lifecycle
    from robopark_api.services.reliable_actions import begin_action
    from robopark_api.services.tracker_claims import claim_issue

    claim_issue(
        db_session,
        actor=seed_mechanic,
        owner=seed_mechanic,
        issue_key=ISSUE_KEY,
        park_id=seed_park_with_tracker.id,
    )
    old = begin_action(
        db_session,
        actor=seed_mechanic,
        resource_type="tracker_issue",
        resource_id=ISSUE_KEY,
        action="comment",
        idempotency_key="old-comment-51",
        payload={"text": "Старый комментарий"},
    ).row
    db_session.commit()
    task_lifecycle.reconcile_external_closure(
        db_session,
        {"key": ISSUE_KEY, "status_key": "closed"},
    )
    assert old.state == "pending"  # The local text remains available for review.
    reopened = task_lifecycle.workflow(
        db_session,
        issue_key=ISSUE_KEY,
        viewer=seed_mechanic,
        issue=_issue(),
    )
    assert reopened["sync_state"] == "saved"

    current = begin_action(
        db_session,
        actor=seed_mechanic,
        resource_type="tracker_issue",
        resource_id=ISSUE_KEY,
        action="comment",
        idempotency_key="new-comment-51",
        payload={"text": "Новый комментарий"},
    ).row
    db_session.commit()
    assert current.created_at > old.created_at
    reopened = task_lifecycle.workflow(
        db_session,
        issue_key=ISSUE_KEY,
        viewer=seed_mechanic,
        issue=_issue(),
    )
    assert reopened["sync_state"] == "pending"


def test_new_repair_transition_does_not_depend_on_failed_previous_cycle(
    db_session,
    seed_mechanic,
    seed_park_with_tracker,
):
    from robopark_api.services import task_lifecycle
    from robopark_api.services.reliable_actions import begin_action
    from robopark_api.services.tracker_claims import claim_issue

    claim_issue(
        db_session,
        actor=seed_mechanic,
        owner=seed_mechanic,
        issue_key=ISSUE_KEY,
        park_id=seed_park_with_tracker.id,
    )
    old = begin_action(
        db_session,
        actor=seed_mechanic,
        resource_type="tracker_issue",
        resource_id=ISSUE_KEY,
        action="review",
        idempotency_key="old-review-51",
        payload={},
    ).row
    old.state = "needs_attention"
    db_session.commit()
    task_lifecycle.reconcile_external_closure(
        db_session,
        {"key": ISSUE_KEY, "status_key": "closed"},
    )
    new = task_lifecycle._transition_action(
        db_session,
        actor=seed_mechanic,
        issue_key=ISSUE_KEY,
        action="review",
        idempotency_key="new-review-51",
        payload={},
    ).row
    assert old.id not in json.loads(new.payload_json).get("depends_on_action_ids", [])


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
    start_payload = json.loads(actions[3].payload_json)
    assert start_payload["depends_on_action_ids"] == [actions[2].id]
    assert "components_prepared" not in start_payload
    claim = db_session.get(TrackerClaim, ISSUE_KEY)
    assert claim.state == "pending"
    assert claim.start_action_id == actions[3].id
    assert claim.operator_user_id is not None
    assert "Задача взята в работу" in db_session.query(TaskMessage).one().text


def test_mechanic_can_claim_open_diagnostic_task_without_changing_role_rules(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    _prepare_tracker(db_session, monkeypatch, with_operator=False)
    from robopark_api.services import tracker_client

    monkeypatch.setattr(
        tracker_client,
        "get_issue",
        lambda **_kwargs: {**_issue(), "status": "Диагностика", "status_key": "diagnostics"},
    )

    response = _claim(client, seed_mechanic)

    assert response.status_code == 200
    assert response.json()["sync_state"] == "pending"
    assert db_session.get(TrackerClaim, ISSUE_KEY).owner_user_id == seed_mechanic.id


def test_mechanic_can_work_on_two_robots_at_the_same_time(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    _prepare_tracker(db_session, monkeypatch, with_operator=False)
    from robopark_api.services import tracker_client

    monkeypatch.setattr(
        tracker_client,
        "get_issue",
        lambda **kwargs: {
            **_issue(),
            "key": kwargs.get("key", ISSUE_KEY),
            "summary": f"blocker [{kwargs.get('key', ISSUE_KEY)}]",
        },
    )
    login_as(client, seed_mechanic.username, "secret")

    first = client.post(
        f"/tracker/issues/{ISSUE_KEY}/claim",
        headers={"Idempotency-Key": "two-robots-first"},
    )
    second_key = "ROBOPARK-52"
    second = client.post(
        f"/tracker/issues/{second_key}/claim",
        headers={"Idempotency-Key": "two-robots-second"},
    )

    assert first.status_code == second.status_code == 200
    assert {claim.issue_key for claim in db_session.query(TrackerClaim).all()} == {
        ISSUE_KEY,
        second_key,
    }


def test_claim_without_operator_defers_tracker_assignment_until_review(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    _prepare_tracker(db_session, monkeypatch, with_operator=False)

    claimed = _claim(client, seed_mechanic)
    assert claimed.status_code == 200
    claim = db_session.get(TrackerClaim, ISSUE_KEY)
    assert claim is not None
    assert claim.owner_user_id == seed_mechanic.id
    assert claim.operator_user_id is None
    assert [
        row.action
        for row in db_session.scalars(select(ReliableAction).order_by(ReliableAction.created_at))
    ] == ["ensure_tag", "ensure_components", "start"]

    unavailable = _submit(client, key="review-no-operator", comment="Ремонт завершён")
    assert unavailable.status_code == 409
    assert unavailable.json()["detail"] == "task_review_operator_unavailable"
    assert db_session.query(ReliableAction).filter(ReliableAction.action == "review").count() == 0

    _operator(db_session, seed_park_with_tracker)
    reviewed = _submit(client, comment="Ремонт завершён")
    assert reviewed.status_code == 200
    actions = list(db_session.scalars(select(ReliableAction).order_by(ReliableAction.created_at)))
    assignment = next(row for row in actions if row.action == "assign_operator")
    review = next(row for row in actions if row.action == "review")
    assert assignment.id in json.loads(review.payload_json)["depends_on_action_ids"]
    assert db_session.get(TrackerClaim, ISSUE_KEY).operator_user_id is not None

    replayed = _submit(client, comment="Ремонт завершён")
    assert replayed.status_code == 200
    assert (
        db_session.query(ReliableAction).filter(ReliableAction.action == "assign_operator").count()
        == 1
    )


def test_claim_with_off_shift_operator_waits_to_assign_until_review(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    _prepare_tracker(db_session, monkeypatch, operator_on_shift=False)

    claimed = _claim(client, seed_mechanic)
    assert claimed.status_code == 200
    claim = db_session.get(TrackerClaim, ISSUE_KEY)
    assert claim is not None
    assert claim.operator_user_id is None
    assert [
        row.action
        for row in db_session.scalars(select(ReliableAction).order_by(ReliableAction.created_at))
    ] == ["ensure_tag", "ensure_components", "start"]

    reviewed = _submit(client, comment="Ремонт завершён")
    assert reviewed.status_code == 200
    assert claim.operator_user_id is not None
    assert (
        db_session.query(ReliableAction).filter(ReliableAction.action == "assign_operator").count()
        == 1
    )


def test_review_reassigns_current_operator_after_shift_change_and_replays_same_reviewer(
    client,
    db_session,
    seed_mechanic,
    seed_park_with_tracker,
    monkeypatch,
):
    _prepare_tracker(db_session, monkeypatch)
    first_operator = _operator(db_session, seed_park_with_tracker)
    assert _claim(client, seed_mechanic).status_code == 200
    claim = db_session.get(TrackerClaim, ISSUE_KEY)
    assert claim is not None and claim.operator_user_id == first_operator.id

    first_shift = db_session.scalar(
        select(ScheduleEntry).where(
            ScheduleEntry.owner_user_id == first_operator.id,
        )
    )
    assert first_shift is not None
    now = datetime.now(UTC)
    first_shift.end_at = now - timedelta(minutes=1)
    second_operator = User(
        username="operator52",
        password_hash=first_operator.password_hash,
        role_id=first_operator.role_id,
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(second_operator)
    db_session.flush()
    db_session.add(UserPark(user_id=second_operator.id, park_id=seed_park_with_tracker.id))
    second_shift = ScheduleEntry(
        owner_user_id=second_operator.id,
        park_id=seed_park_with_tracker.id,
        kind="shift",
        start_at=now - timedelta(hours=1),
        end_at=now + timedelta(hours=1),
        created_by_user_id=second_operator.id,
        updated_by_user_id=second_operator.id,
    )
    db_session.add(second_shift)
    db_session.commit()

    reviewed = _submit(client, key="review-after-shift-change", comment="Исправлено")
    assert reviewed.status_code == 200
    db_session.refresh(claim)
    assert claim.operator_user_id == second_operator.id
    assignments = list(
        db_session.scalars(
            select(ReliableAction).where(
                ReliableAction.resource_id == ISSUE_KEY,
                ReliableAction.action == "assign_operator",
            )
        )
    )
    assert len(assignments) == 2
    reassignment = next(
        row
        for row in assignments
        if json.loads(row.payload_json)["operator_user_id"] == second_operator.id
    )
    review = db_session.scalar(
        select(ReliableAction).where(
            ReliableAction.resource_id == ISSUE_KEY,
            ReliableAction.action == "review",
        )
    )
    assert review is not None
    assert reassignment.id in json.loads(review.payload_json)["depends_on_action_ids"]

    second_shift.end_at = now - timedelta(minutes=1)
    first_shift.end_at = now + timedelta(hours=1)
    db_session.commit()
    replayed = _submit(client, key="review-after-shift-change", comment="Исправлено")
    assert replayed.status_code == 200
    assert replayed.json() == reviewed.json()
    assert db_session.query(TaskReview).one().reviewer_user_id == second_operator.id
    assert (
        db_session.query(ReliableAction)
        .filter(
            ReliableAction.resource_id == ISSUE_KEY,
            ReliableAction.action == "assign_operator",
        )
        .count()
        == 2
    )


def test_review_replay_survives_operator_becoming_unavailable(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    _prepare_tracker(db_session, monkeypatch)
    operator = db_session.scalar(select(User).where(User.username == "operator51"))
    assert operator is not None
    assert _claim(client, seed_mechanic).status_code == 200
    submitted = _submit(client, key="review-operator-gone", comment="Ремонт завершён")
    assert submitted.status_code == 200
    action_count = db_session.query(ReliableAction).count()

    operator.is_active = False
    db_session.commit()
    replay = _submit(client, key="review-operator-gone", comment="Ремонт завершён")

    assert replay.status_code == 200
    assert replay.json() == submitted.json()
    assert db_session.query(ReliableAction).count() == action_count
    changed = _submit(client, key="review-operator-gone", comment="Другой результат")
    assert changed.status_code == 409
    assert db_session.query(ReliableAction).count() == action_count


def test_claim_replays_persisted_unmarked_start_payload_without_conflict(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    from robopark_api.services.reliable_actions import canonical_payload

    _prepare_tracker(db_session, monkeypatch)
    assert _claim(client, seed_mechanic).status_code == 200
    start = db_session.scalar(select(ReliableAction).where(ReliableAction.action == "start"))
    payload = json.loads(start.payload_json)
    payload.pop("components_prepared", None)
    start.payload_json, start.payload_hash = canonical_payload(payload)
    db_session.commit()

    replay = _claim(client, seed_mechanic)

    assert replay.status_code == 200
    assert db_session.query(ReliableAction).count() == 4


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


def test_active_claim_cannot_be_replaced_by_another_mechanic(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    _prepare_tracker(db_session, monkeypatch)
    other = _mechanic(db_session, seed_park_with_tracker, username="active-claim-rival")
    assert _claim(client, seed_mechanic).status_code == 200
    _activate_claim(db_session)
    login_as(client, other.username, "secret")

    response = client.post(
        f"/tracker/issues/{ISSUE_KEY}/claim",
        headers={"Idempotency-Key": "active-claim-rival-key"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "tracker_issue_already_claimed"
    assert db_session.get(TrackerClaim, ISSUE_KEY).owner_user_id == seed_mechanic.id
    assert db_session.query(ReliableAction).count() == 4


def test_claim_same_owner_with_new_key_returns_existing_chain(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    _prepare_tracker(db_session, monkeypatch)
    first = _claim(client, seed_mechanic)
    _activate_claim(db_session)

    second = client.post(
        f"/tracker/issues/{ISSUE_KEY}/claim",
        headers={"Idempotency-Key": "same-owner-new-key"},
    )

    assert second.status_code == 200
    assert second.json()["performed_at"] == first.json()["performed_at"]
    assert second.json()["workflow"]["owner"]["login"] == seed_mechanic.username
    assert db_session.query(ReliableAction).count() == 4


def test_claim_skips_component_action_when_tracker_has_components(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    _prepare_tracker(db_session, monkeypatch)
    from robopark_api.services import tracker_client

    monkeypatch.setattr(
        tracker_client,
        "get_issue",
        lambda **_kwargs: {**_issue(), "components": ["EXISTING"]},
    )
    response = _claim(client, seed_mechanic)

    assert response.status_code == 200
    actions = list(db_session.scalars(select(ReliableAction).order_by(ReliableAction.created_at)))
    assert [row.action for row in actions] == ["assign_operator", "ensure_tag", "start"]
    assert json.loads(actions[-1].payload_json)["depends_on_action_ids"] == [actions[-2].id]


def test_handoff_requires_current_mechanic_owner(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    _prepare_tracker(db_session, monkeypatch)
    other = _mechanic(db_session, seed_park_with_tracker, username="handoff-target")
    operator = db_session.scalar(select(User).where(User.username == "operator51"))
    assert _claim(client, seed_mechanic).status_code == 200
    _activate_claim(db_session)
    login_as(client, operator.username, "secret")

    response = client.post(
        f"/tracker/issues/{ISSUE_KEY}/handoff",
        headers={"Idempotency-Key": "operator-handoff-key"},
        json={"assignee": other.username, "reason": "Новая смена"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "task_handoff_owner_required"
    assert db_session.get(TrackerClaim, ISSUE_KEY).owner_user_id == seed_mechanic.id


def test_concurrent_claims_serialize_at_issue_boundary(
    db_engine, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    from robopark_api.services import schedules, task_lifecycle

    _operator(db_session, seed_park_with_tracker)
    other = _mechanic(db_session, seed_park_with_tracker, username="racing-mechanic")
    park_id = seed_park_with_tracker.id
    real_resolve = schedules.resolve_active_operator
    start = threading.Barrier(3)
    release = threading.Event()
    first_entered = threading.Event()
    both_entered = threading.Event()
    counter_lock = threading.Lock()
    active = 0
    maximum_active = 0

    def controlled_resolve(db, *, park_id, at=None, allow_off_shift_fallback=True):
        nonlocal active, maximum_active
        with counter_lock:
            active += 1
            maximum_active = max(maximum_active, active)
            first_entered.set()
            if active == 2:
                both_entered.set()
        assert release.wait(timeout=2)
        try:
            return real_resolve(
                db,
                park_id=park_id,
                at=at,
                allow_off_shift_fallback=allow_off_shift_fallback,
            )
        finally:
            with counter_lock:
                active -= 1

    monkeypatch.setattr(schedules, "resolve_active_operator", controlled_resolve)

    def run_claim(user_id, idempotency_key):
        start.wait(timeout=2)
        with Session(db_engine) as db:
            try:
                task_lifecycle.claim(
                    db,
                    actor=db.get(User, user_id),
                    issue_key=ISSUE_KEY,
                    park=db.get(Park, park_id),
                    idempotency_key=idempotency_key,
                )
            except HTTPException as exc:
                return ("conflict", exc.status_code, exc.detail)
            return ("claimed", 200, None)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(run_claim, seed_mechanic.id, "race-claim-one")
        second = executor.submit(run_claim, other.id, "race-claim-two")
        start.wait(timeout=2)
        assert first_entered.wait(timeout=2)
        both_entered.wait(timeout=0.25)
        release.set()
        outcomes = [first.result(timeout=3), second.result(timeout=3)]

    assert maximum_active == 1
    assert sorted(outcome[0] for outcome in outcomes) == ["claimed", "conflict"], outcomes
    assert next(outcome for outcome in outcomes if outcome[0] == "conflict") == (
        "conflict",
        409,
        "tracker_issue_claim_pending",
    )


def test_claim_rejects_existing_active_owner_without_reassignment(
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

    assert response.status_code == 409
    assert response.json()["detail"] == "tracker_issue_already_claimed"
    claim = db_session.get(TrackerClaim, ISSUE_KEY)
    assert claim.owner_user_id == seed_royal.id
    assert db_session.query(TaskMessage).count() == 0


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
    _activate_claim(db_session)
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


def test_concurrent_handoffs_from_same_owner_commit_one_ownership_event(
    db_engine, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    from robopark_api.services import task_lifecycle
    from robopark_api.services.tracker_claims import claim_issue

    first_target = _mechanic(db_session, seed_park_with_tracker, username="handoff-race-b")
    second_target = _mechanic(db_session, seed_park_with_tracker, username="handoff-race-c")
    claim_issue(
        db_session,
        actor=seed_mechanic,
        owner=seed_mechanic,
        issue_key=ISSUE_KEY,
        park_id=seed_park_with_tracker.id,
    )
    db_session.commit()
    real_get_claim = task_lifecycle.get_claim
    first_read = threading.Event()
    both_read = threading.Event()
    release = threading.Event()
    reads = 0
    guard = threading.Lock()
    start = threading.Barrier(3)

    def paused_get_claim(db, issue_key):
        nonlocal reads
        claim = real_get_claim(db, issue_key)
        if db.info.get("handoff_probe") and not db.info.get("handoff_seen"):
            db.info["handoff_seen"] = True
            with guard:
                reads += 1
                first_read.set()
                if reads == 2:
                    both_read.set()
            assert release.wait(timeout=3)
        return claim

    monkeypatch.setattr(task_lifecycle, "get_claim", paused_get_claim)

    def transfer(target, key):
        start.wait(timeout=2)
        with Session(db_engine) as db:
            db.info["handoff_probe"] = True
            try:
                task_lifecycle.handoff(
                    db,
                    actor=db.get(User, seed_mechanic.id),
                    issue_key=ISSUE_KEY,
                    assignee=target,
                    reason="Новая смена",
                    idempotency_key=key,
                )
            except HTTPException as exc:
                return (exc.status_code, exc.detail)
            return (200, "ok")

    with ThreadPoolExecutor(max_workers=2) as executor:
        one = executor.submit(transfer, first_target.username, "handoff-race-one")
        two = executor.submit(transfer, second_target.username, "handoff-race-two")
        start.wait(timeout=2)
        assert first_read.wait(timeout=2)
        both_read.wait(timeout=0.25)
        release.set()
        outcomes = [one.result(timeout=4), two.result(timeout=4)]

    assert sorted(outcomes) == [(200, "ok"), (403, "task_handoff_owner_required")]
    assert db_session.query(ReliableAction).filter_by(action="comment").count() == 1
    assert db_session.query(TaskMessage).count() == 1
    assert db_session.get(TrackerClaim, ISSUE_KEY).owner_user_id in {
        first_target.id,
        second_target.id,
    }


def test_workflow_exposes_current_cycle_comment_eligibility(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    _prepare_tracker(db_session, monkeypatch)
    assert _claim(client, seed_mechanic).status_code == 200
    _activate_claim(db_session)
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


def test_submit_review_requires_active_claim_before_persisting_review_effects(
    client, db_session, seed_mechanic, seed_park_with_tracker, tmp_path, monkeypatch
):
    from robopark_api.services import task_timeline

    monkeypatch.setattr(task_timeline, "staged_attachments_root", lambda: tmp_path)
    _prepare_tracker(db_session, monkeypatch)
    assert _claim(client, seed_mechanic).status_code == 200
    claim = db_session.get(TrackerClaim, ISSUE_KEY)
    assert claim is not None and claim.state == "pending"
    before_actions = db_session.query(ReliableAction).count()

    pending = _submit(
        client,
        key="review-pending-claim",
        comment="Заменил деталь",
        activate_claim=False,
    )

    assert pending.status_code == 409
    assert pending.json()["detail"] == "tracker_issue_claim_not_active"
    assert db_session.query(TaskReview).count() == 0
    assert db_session.query(TaskAttachment).count() == 0
    assert db_session.query(ReliableAction).count() == before_actions

    claim.state = "active"
    db_session.commit()
    active = _submit(client, key="review-active-claim", comment="Заменил деталь")
    assert active.status_code == 200, active.text
    assert db_session.query(TaskReview).count() == 1


def test_second_review_key_is_rejected_before_staging_another_photo(
    client,
    db_session,
    seed_mechanic,
    seed_park_with_tracker,
    monkeypatch,
):
    from robopark_api.services import task_lifecycle

    _prepare_tracker(db_session, monkeypatch)
    assert _claim(client, seed_mechanic).status_code == 200
    assert _submit(client, key="review-first-key", comment="Исправлено").status_code == 200
    action_count = db_session.query(ReliableAction).count()

    def reject_duplicate_staging(*_args, **_kwargs):
        raise AssertionError("A second pending review must not stage a photo")

    monkeypatch.setattr(task_lifecycle, "_write_staged_blob", reject_duplicate_staging)
    duplicate = _submit(client, key="review-second-key", comment="Исправлено")

    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "task_review_already_pending"
    assert db_session.query(ReliableAction).count() == action_count


def test_submit_review_stages_one_photo_and_all_bot_actions_once(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    operator = _operator(db_session, seed_park_with_tracker)
    _prepare_tracker(db_session, monkeypatch)
    assert _claim(client, seed_mechanic).status_code == 200

    response = _submit(client, comment="Заменил бампер")
    replay = _submit(client, comment="Заменил бампер")

    assert response.status_code == replay.status_code == 200
    assert response.json() == replay.json()
    review = db_session.query(TaskReview).one()
    assert review.state == "pending"
    assert review.reviewer_user_id == operator.id
    assert db_session.query(TaskAttachment).count() == 1
    assert db_session.get(TrackerClaim, ISSUE_KEY).owner_user_id == seed_mechanic.id
    actions = db_session.query(ReliableAction).filter_by(resource_id=ISSUE_KEY).all()
    assert sorted(action.action for action in actions) == [
        "assign_operator",
        "attach",
        "comment",
        "ensure_components",
        "ensure_tag",
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
    assert "@operator51" not in automatic.text


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
    _activate_claim(db_session)
    posted = client.post(
        f"/tracker/issues/{ISSUE_KEY}/messages",
        headers={"Idempotency-Key": "current-comment-51"},
        json={"text": "Работа завершена"},
    )
    assert posted.status_code == 201

    response = _submit(client, key="review-existing-comment")

    assert response.status_code == 200
    assert db_session.query(ReliableAction).filter_by(action="comment").count() == 1


def test_review_waits_for_exact_prior_current_cycle_comment_action(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    from robopark_api.services.tracker_outbox import _dependency_state

    _prepare_tracker(db_session, monkeypatch)
    assert _claim(client, seed_mechanic).status_code == 200
    _activate_claim(db_session)
    posted = client.post(
        f"/tracker/issues/{ISSUE_KEY}/messages",
        headers={"Idempotency-Key": "completion-comment-51"},
        json={"text": "Работа завершена"},
    )
    assert posted.status_code == 201
    comment = db_session.scalar(select(ReliableAction).where(ReliableAction.action == "comment"))
    assert comment is not None and comment.state == "pending"

    response = _submit(client, key="review-prior-comment")

    assert response.status_code == 200
    review_action = db_session.scalar(
        select(ReliableAction).where(ReliableAction.action == "review")
    )
    dependencies = json.loads(review_action.payload_json)["depends_on_action_ids"]
    assert comment.id in dependencies
    assert _dependency_state(db_session, review_action) == "waiting"
    comment.state = "needs_attention"
    db_session.commit()
    assert _dependency_state(db_session, review_action) == "failed"


def test_review_replay_keeps_saved_comment_dependency_after_return_starts_new_cycle(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    _prepare_tracker(db_session, monkeypatch)
    operator = db_session.scalar(select(User).where(User.username == "operator51"))
    assert _claim(client, seed_mechanic).status_code == 200
    _activate_claim(db_session)
    assert (
        client.post(
            f"/tracker/issues/{ISSUE_KEY}/messages",
            headers={"Idempotency-Key": "replay-comment-51"},
            json={"text": "Работа завершена"},
        ).status_code
        == 201
    )
    assert _submit(client, key="replay-review-51").status_code == 200
    before_actions = db_session.query(ReliableAction).count()
    login_as(client, operator.username, "secret")
    returned = client.post(
        f"/tracker/issues/{ISSUE_KEY}/review/return",
        headers={"Idempotency-Key": "return-replay-review-51"},
        json={"reason": "Нужно проверить повторно"},
    )
    assert returned.status_code == 200
    login_as(client, seed_mechanic.username, "secret")

    replay = _submit(client, key="replay-review-51")

    assert replay.status_code == 200
    assert db_session.query(ReliableAction).count() == before_actions + 2


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
