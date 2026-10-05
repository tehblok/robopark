import json
from contextlib import contextmanager

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from robopark_api.ai_models import AIScript
from robopark_api.services import platform_settings
from robopark_api.services.ai import tool_domain
from robopark_api.task_workflow_models import ReliableAction, TaskReview


def _names(items):
    return {item["function"]["name"] for item in items}


def test_catalog_is_role_filtered_and_rejects_open_argument_shapes(
    db_session, seed_admin, seed_mechanic
):
    admin = tool_domain.catalog(db_session, seed_admin)
    mechanic = tool_domain.catalog(db_session, seed_mechanic)

    assert _names(admin) == {
        "task_get",
        "task_comment",
        "task_close",
        "robot_check",
        "script_list",
        "script_get",
        "script_create",
        "script_update",
        "script_enable",
        "script_disable",
        "script_test",
        "script_run",
        "script_delete",
    }
    assert _names(mechanic) == {
        "task_get",
        "task_claim",
        "task_comment",
        "task_handoff",
        "robot_check",
    }
    assert all(
        item["function"]["parameters"]["additionalProperties"] is False for item in admin + mechanic
    )


def test_prepare_rejects_extra_arguments(db_session, seed_admin):
    with pytest.raises(HTTPException) as exc:
        tool_domain.prepare(
            db_session,
            seed_admin,
            None,
            "script_list",
            {"unexpected": "value"},
        )

    assert exc.value.status_code == 422
    assert exc.value.detail == "ai_tool_arguments_invalid"


def test_script_results_are_bounded_and_delete_always_requires_confirmation(
    db_session, seed_admin, seed_park_with_tracker, test_settings
):
    park_id = seed_park_with_tracker.id
    rows = [
        AIScript(name=f"Script {index:02}", source="def main(data):\n return data")
        for index in range(55)
    ]
    rows[0].source = "def main(data):\n return '" + ("x" * 12000) + "'"
    db_session.add_all(rows)
    db_session.commit()

    prepared = tool_domain.prepare(
        db_session,
        seed_admin,
        park_id,
        "script_delete",
        {"script_id": rows[0].id, "revision": rows[0].revision},
    )
    assert prepared["confirmation_required"] is True

    listing = tool_domain.execute(
        db_session,
        test_settings,
        seed_admin,
        park_id,
        "script_list",
        {},
        idempotency_key="list-scripts-1",
        expected={},
    )
    second_page = tool_domain.execute(
        db_session,
        test_settings,
        seed_admin,
        park_id,
        "script_list",
        {"offset": 25},
        idempotency_key="list-scripts-2",
        expected={},
    )
    detail = tool_domain.execute(
        db_session,
        test_settings,
        seed_admin,
        park_id,
        "script_get",
        {"script_id": rows[0].id},
        idempotency_key="get-script-1",
        expected=tool_domain.prepare(
            db_session, seed_admin, park_id, "script_get", {"script_id": rows[0].id}
        )["expected"],
    )

    assert len(listing["items"]) == 25
    assert listing["total"] == 55
    assert listing["next_offset"] == 25
    assert len(second_page["items"]) == 25
    assert second_page["items"][0]["name"] == "Script 25"
    assert second_page["next_offset"] == 50
    assert len(detail["source_preview"]) == 4000
    assert detail["source_complete"] is False
    assert len(json.dumps(detail, ensure_ascii=False).encode()) < 8000


def test_task_close_binds_latest_review_revision_before_approval(
    db_session,
    seed_admin,
    seed_mechanic,
    seed_park_with_tracker,
    test_settings,
    monkeypatch,
):
    issue = {
        "key": "ROBOPARK-81",
        "summary": "Camera",
        "status": "На проверке",
        "status_key": "review",
        "updated": "2026-10-05T10:00:00Z",
        "queue": seed_park_with_tracker.tracker_queue,
        "tags": [seed_park_with_tracker.tag],
        "assignee": {"login": "mechanic"},
    }
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    monkeypatch.setattr(tool_domain.tracker_client, "get_issue", lambda **_kwargs: dict(issue))
    review = TaskReview(
        id="review-bound-to-preview",
        issue_key=issue["key"],
        state="pending",
        actor_user_id=seed_mechanic.id,
        created_at=1,
        updated_at=1,
    )
    db_session.add(review)
    db_session.commit()

    prepared = tool_domain.prepare(
        db_session,
        seed_admin,
        seed_park_with_tracker.id,
        "task_close",
        {"key": issue["key"]},
    )
    assert prepared["confirmation_required"] is True
    assert prepared["expected"]["review"] == {
        "id": review.id,
        "state": "pending",
        "updated_at": 1,
    }

    review.updated_at = 2
    db_session.commit()
    with pytest.raises(HTTPException) as exc:
        tool_domain.execute(
            db_session,
            test_settings,
            seed_admin,
            seed_park_with_tracker.id,
            "task_close",
            {"key": issue["key"]},
            idempotency_key="close-review-bound-1",
            expected=prepared["expected"],
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "ai_action_changed"
    assert review.state == "pending"


def test_task_close_rechecks_review_after_acquiring_mutation_lease(
    db_session,
    seed_admin,
    seed_mechanic,
    seed_park_with_tracker,
    test_settings,
    monkeypatch,
):
    issue = {
        "key": "ROBOPARK-82",
        "summary": "Camera",
        "status": "На проверке",
        "status_key": "review",
        "updated": "2026-10-05T10:00:00Z",
        "queue": seed_park_with_tracker.tracker_queue,
        "tags": [seed_park_with_tracker.tag],
    }
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    monkeypatch.setattr(tool_domain.tracker_client, "get_issue", lambda **_kwargs: dict(issue))
    review = TaskReview(
        id="review-raced-at-lease",
        issue_key=issue["key"],
        state="pending",
        actor_user_id=seed_mechanic.id,
        created_at=1,
        updated_at=1,
    )
    db_session.add(review)
    db_session.commit()
    prepared = tool_domain.prepare(
        db_session,
        seed_admin,
        seed_park_with_tracker.id,
        "task_close",
        {"key": issue["key"]},
    )

    @contextmanager
    def raced_lease(_db, _key):
        review.updated_at = 2
        db_session.flush()
        yield

    monkeypatch.setattr(tool_domain.tracker_submissions, "task_mutation_lease", raced_lease)
    with pytest.raises(HTTPException) as exc:
        tool_domain.execute(
            db_session,
            test_settings,
            seed_admin,
            seed_park_with_tracker.id,
            "task_close",
            {"key": issue["key"]},
            idempotency_key="close-review-lease-race",
            expected=prepared["expected"],
        )

    assert exc.value.detail == "ai_action_changed"
    assert (
        db_session.scalar(
            select(ReliableAction).where(
                ReliableAction.resource_id == issue["key"], ReliableAction.action == "close"
            )
        )
        is None
    )
