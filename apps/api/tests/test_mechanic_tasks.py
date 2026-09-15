import json
from pathlib import Path
from unittest.mock import patch

from conftest import login_as
from robopark_api.models import Park, UserPark
from robopark_api.services.tracker_client import issue_to_dict
from robopark_api.task_workflow_models import HiddenTask

FIXTURES = Path(__file__).parent / "fixtures"


def test_mechanic_tasks_requires_token(client, seed_mechanic):
    login_as(client, "mech1", "secret")
    response = client.get("/mechanic/tasks")
    assert response.status_code == 503
    assert response.json()["detail"] == "tracker_token_not_configured"


def test_mechanic_tasks_with_mocked_tracker(client, seed_mechanic, seed_royal, db_session):
    login_as(client, "royal", "secret")
    client.put("/admin/settings/tracker-token", json={"token": "fake-token"})
    login_as(client, "mech1", "secret")

    issues = [
        issue_to_dict(item)
        for item in json.loads((FIXTURES / "tracker_issues.json").read_text(encoding="utf-8"))
    ]
    park = db_session.query(Park).filter_by(tag="Alpha").one()
    db_session.add(
        HiddenTask(
            id="hidden-mechanic-task",
            issue_key="ROBOPARK-1",
            park_id=park.id,
            reason="duplicate",
            actor_user_id=seed_royal.id,
            created_at=1,
            updated_at=1,
        )
    )
    db_session.commit()
    with patch("robopark_api.services.tracker_client._search", return_value=issues):
        response = client.get("/mechanic/tasks?status=all")

    assert response.status_code == 200
    body = response.json()
    assert body["park_tag"] == "Alpha"
    assert [item["key"] for item in body["items"]] == ["ROBOPARK-2"]
    assert body["counts"]["all"] == 1


def test_mechanic_tasks_disabled_when_feature_off(
    client, seed_mechanic, seed_park_with_tracker, seed_royal, db_session
):
    seed_park_with_tracker.feature_blockers = False
    db_session.commit()
    login_as(client, "royal", "secret")
    client.put("/admin/settings/tracker-token", json={"token": "fake-token"})
    login_as(client, "mech1", "secret")
    response = client.get("/mechanic/tasks")
    assert response.status_code == 409
    assert response.json()["detail"] == "tasks_disabled_for_park"


def test_mechanic_tasks_second_park_by_id(client, seed_mechanic, seed_royal, db_session):
    extra = Park(
        name="Beta",
        tag="Beta",
        is_active=True,
        tracker_queue="OTHERQ",
        feature_blockers=True,
    )
    db_session.add(extra)
    db_session.flush()
    db_session.add(UserPark(user_id=seed_mechanic.id, park_id=extra.id))
    db_session.commit()

    login_as(client, "royal", "secret")
    client.put("/admin/settings/tracker-token", json={"token": "fake-token"})
    login_as(client, "mech1", "secret")

    issues = [
        issue_to_dict(item)
        for item in json.loads((FIXTURES / "tracker_issues.json").read_text(encoding="utf-8"))
    ]
    with patch("robopark_api.services.tracker_client._search", return_value=issues):
        response = client.get(f"/mechanic/tasks?status=all&park_id={extra.id}")

    assert response.status_code == 200
    assert response.json()["park_tag"] == "Beta"
