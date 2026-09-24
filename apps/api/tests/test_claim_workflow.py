"""Claim chain contract shared by online and offline task entry points."""

import json

import pytest
from sqlalchemy import select

from conftest import login_as
from robopark_api.services import platform_settings, tracker_client
from robopark_api.task_workflow_models import ReliableAction
from test_task_lifecycle import ISSUE_KEY, _issue, _operator


@pytest.mark.parametrize(
    ("components", "expected_actions"),
    [
        ([], ["assign_operator", "ensure_tag", "ensure_components", "start"]),
        (["EXISTING"], ["assign_operator", "ensure_tag", "start"]),
    ],
)
def test_claim_queues_exact_dependency_chain_once(
    client,
    db_session,
    seed_mechanic,
    seed_park_with_tracker,
    monkeypatch,
    components,
    expected_actions,
):
    _operator(db_session, seed_park_with_tracker)
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
    assert [row.action for row in actions] == expected_actions
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
