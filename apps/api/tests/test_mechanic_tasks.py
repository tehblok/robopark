import json
from pathlib import Path
from unittest.mock import patch

from conftest import login_as
from robopark_api.services.tracker_client import issue_to_dict

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
    with patch("robopark_api.services.tracker_client._search", return_value=issues):
        response = client.get("/mechanic/tasks?status=all")

    assert response.status_code == 200
    body = response.json()
    assert body["park_tag"] == "Alpha"
    assert len(body["items"]) == 2
    assert body["counts"]["all"] == 2


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
