import json
from pathlib import Path
from unittest.mock import patch

from conftest import login_as
from robopark_api.services.tracker_client import issue_to_dict
from robopark_api.task_workflow_models import HiddenTask

FIXTURES = Path(__file__).parent / "fixtures"


def test_robot_search(client, db_session, seed_mechanic, seed_royal, seed_park_with_tracker):
    login_as(client, "royal", "secret")
    client.put("/admin/settings/tracker-token", json={"token": "fake-token"})
    login_as(client, "mech1", "secret")

    issues = [
        issue_to_dict(item)
        for item in json.loads((FIXTURES / "tracker_issues.json").read_text(encoding="utf-8"))
    ]
    db_session.add(
        HiddenTask(
            id="hidden-mechanic-robot-ticket",
            issue_key="ROBOPARK-1",
            park_id=seed_park_with_tracker.id,
            reason="duplicate",
            actor_user_id=seed_royal.id,
            created_at=1,
            updated_at=1,
        )
    )
    db_session.commit()
    with patch(
        "robopark_api.services.tracker_client.search_robot_tickets",
        return_value=issues,
    ):
        response = client.get("/mechanic/robots/447/tickets")

    assert response.status_code == 200
    assert response.json()["query"] == "447"
    assert [item["key"] for item in response.json()["items"]] == ["ROBOPARK-2"]
