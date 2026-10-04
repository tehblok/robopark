"""Claim chain contract shared by online and offline task entry points."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from conftest import login_as
from robopark_api.collaboration_models import TrackerClaim
from robopark_api.schedule_models import ScheduleEntry
from robopark_api.services import platform_settings, tracker_client
from robopark_api.task_workflow_models import ReliableAction
from test_task_lifecycle import ISSUE_KEY, _claim, _issue, _operator


@pytest.fixture(autouse=True)
def repair_component_catalog(monkeypatch):
    monkeypatch.setattr(
        tracker_client,
        "list_queue_components",
        lambda **_kwargs: [
            {"id": "robot-447", "label": "447"},
            {"id": "robot-448", "label": "448"},
        ],
    )


@pytest.mark.parametrize(
    ("components", "expected_actions"),
    [
        ([], ["assign_operator", "ensure_tag", "ensure_components", "start"]),
        (["EXISTING"], ["assign_operator", "ensure_tag", "start"]),
    ],
)
@pytest.mark.parametrize("operator_on_shift", [True, False])
def test_claim_queues_exact_dependency_chain_once(
    client,
    db_session,
    seed_mechanic,
    seed_park_with_tracker,
    monkeypatch,
    components,
    expected_actions,
    operator_on_shift,
):
    operator = _operator(db_session, seed_park_with_tracker)
    if operator_on_shift:
        now = datetime.now(UTC)
        db_session.add(
            ScheduleEntry(
                owner_user_id=operator.id,
                park_id=seed_park_with_tracker.id,
                kind="shift",
                start_at=now - timedelta(hours=1),
                end_at=now + timedelta(hours=1),
                created_by_user_id=operator.id,
                updated_by_user_id=operator.id,
            )
        )
        db_session.commit()
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    monkeypatch.setattr(
        tracker_client,
        "get_issue",
        lambda **_kwargs: {**_issue(), "components": components},
    )
    login_as(client, seed_mechanic.username, "secret")

    first = client.post(
        f"/tracker/issues/{ISSUE_KEY}/claim",
        headers={"Idempotency-Key": "claim-chain-51"},
    )
    replay = client.post(
        f"/tracker/issues/{ISSUE_KEY}/claim",
        headers={"Idempotency-Key": "claim-chain-51"},
    )

    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    actions = list(db_session.scalars(select(ReliableAction).order_by(ReliableAction.created_at)))
    assert [row.action for row in actions] == (
        expected_actions if operator_on_shift else expected_actions[1:]
    )
    for previous, current in zip(actions, actions[1:], strict=False):
        assert json.loads(current.payload_json)["depends_on_action_ids"] == [previous.id]


def test_existing_owner_still_needs_idempotency_key(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    _operator(db_session, seed_park_with_tracker)
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    monkeypatch.setattr(tracker_client, "get_issue", lambda **_kwargs: _issue())
    login_as(client, seed_mechanic.username, "secret")
    assert (
        client.post(
            f"/tracker/issues/{ISSUE_KEY}/claim",
            headers={"Idempotency-Key": "claim-first-51"},
        ).status_code
        == 200
    )

    missing = client.post(f"/tracker/issues/{ISSUE_KEY}/claim")

    assert missing.status_code == 400
    assert missing.json()["detail"] == "reliable_action_key_invalid"


def test_mechanic_can_claim_two_robots_and_see_both_in_owned_work(
    client,
    db_session,
    seed_mechanic,
    seed_park_with_tracker,
    monkeypatch,
):
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    issues = {
        "ROBOPARK-51": _issue(),
        "ROBOPARK-52": {**_issue(), "key": "ROBOPARK-52", "summary": "blocker [448]"},
    }
    monkeypatch.setattr(tracker_client, "get_issue", lambda **kwargs: issues[kwargs["key"]])
    monkeypatch.setattr(tracker_client, "search_issues", lambda **_kwargs: list(issues.values()))
    assert _claim(client, seed_mechanic).status_code == 200
    second = client.post(
        "/tracker/issues/ROBOPARK-52/claim",
        headers={"Idempotency-Key": "claim-robot-448"},
    )
    assert second.status_code == 200, second.json()
    for key in issues:
        assert db_session.get(TrackerClaim, key).owner_user_id == seed_mechanic.id

    owned = client.get("/tracker/issues?owned_by_me=true&sort=oldest")
    assert owned.status_code == 200, owned.json()
    assert owned.json()["total"] == 2
    assert {item["key"] for item in owned.json()["items"]} == set(issues)
    actions = db_session.scalars(select(ReliableAction)).all()
    assert {row.resource_id for row in actions} == set(issues)
    for row in actions:
        dependencies = json.loads(row.payload_json).get("depends_on_action_ids", [])
        assert all(
            db_session.get(ReliableAction, dependency).resource_id == row.resource_id
            for dependency in dependencies
        )
