import json
from pathlib import Path
from unittest.mock import patch

import pytest

from conftest import login_as
from robopark_api.models import Park
from robopark_api.services import rbac
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
    for issue in issues:
        issue["queue"] = seed_park_with_tracker.tracker_queue
        issue["tags"] = [seed_park_with_tracker.tag]
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


def test_robot_search_rejects_foreign_park_tag_in_shared_queue(
    client, db_session, seed_mechanic, seed_royal, seed_park_with_tracker
):
    foreign = Park(
        name="Beta",
        tag="Beta",
        is_active=True,
        tracker_queue=seed_park_with_tracker.tracker_queue,
    )
    db_session.add(foreign)
    db_session.commit()
    login_as(client, "royal", "secret")
    client.put("/admin/settings/tracker-token", json={"token": "fake-token"})
    login_as(client, "mech1", "secret")

    base = {
        "summary": "[447] blocker",
        "status": "queued",
        "created": "2026-01-01T10:00:00+00:00",
        "hours_created": "1.0",
        "robot": "447",
        "in_relocation": "0",
        "status_key": "queued",
        "resolution": "",
        "queue": seed_park_with_tracker.tracker_queue,
    }
    issues = [
        {**base, "key": "R-OWN", "tags": [seed_park_with_tracker.tag]},
        {**base, "key": "R-FOREIGN", "tags": [foreign.tag]},
    ]
    with patch(
        "robopark_api.services.tracker_cache.search_robot_tickets",
        return_value=issues,
    ):
        response = client.get("/mechanic/robots/447/tickets")

    assert response.status_code == 200
    assert [item["key"] for item in response.json()["items"]] == ["R-OWN"]


@pytest.mark.parametrize(
    "permission",
    [rbac.PERMISSION_TRACKER_READ, rbac.PERMISSION_NAV_ROBOT_SEARCH],
)
def test_robot_search_respects_user_permission_denials(
    client, db_session, seed_mechanic, seed_royal, permission
):
    rbac.set_user_effective_permissions(
        db_session,
        seed_mechanic,
        sorted(rbac.permissions_for_user(db_session, seed_mechanic) - {permission}),
    )
    db_session.commit()
    login_as(client, "royal", "secret")
    client.put("/admin/settings/tracker-token", json={"token": "fake-token"})
    login_as(client, "mech1", "secret")

    with patch("robopark_api.services.tracker_cache.search_robot_tickets") as search:
        response = client.get("/mechanic/robots/447/tickets")

    assert response.status_code == 403
    search.assert_not_called()
