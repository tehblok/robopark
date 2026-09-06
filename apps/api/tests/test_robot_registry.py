from unittest.mock import patch

import pytest

from conftest import login_as, role_id_for
from robopark_api.models import Park, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services import emergency_cache
from robopark_api.services.tracker_client import TrackerError

VIN = "YASADR00000000447"


def issue(key, robot="447", *, tag="Alpha", status="new"):
    return {
        "key": key,
        "robot": robot,
        "summary": f"[{robot}] Repair",
        "queue": "ROBOPARK",
        "tags": [tag],
        "status_key": status,
        "status": status,
        "resolution": "",
    }


@pytest.fixture
def registry(client, db_session, seed_mechanic, seed_park_with_tracker):
    login_as(client, "mech1", "secret")
    with patch("robopark_api.services.platform_settings.get_tracker_token", return_value="fake"):
        yield seed_park_with_tracker


def test_registry_one_tracker_batch_no_emergency_and_unique_exact_robot_counts(client, registry):
    items = [
        issue("ROBOPARK-1"),
        issue("ROBOPARK-1"),
        issue("ROBOPARK-2"),
        issue("ROBOPARK-3", "1447"),
        issue("ROBOPARK-4", tag="Foreign"),
        issue("ROBOPARK-5", status="closed"),
        issue("ROBOPARK-6", "other447"),
    ]
    with (
        patch("robopark_api.services.tracker_client.search_issues", return_value=items) as batch,
        patch("robopark_api.services.emergency_client.fetch_robot_payload") as emergency,
    ):
        response = client.get(f"/robots?park_id={registry.id}&query=447")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["vin"] == VIN
    assert data["items"][0]["task_count"] == 2
    assert data["items"][0]["telemetry"] is None
    assert data["items"][0]["error_count"] is None
    assert data["partial"] is True
    assert data["source_complete"] is True
    batch.assert_called_once()
    assert batch.call_args.kwargs["filter_open"] is False
    assert '"ROBOPARK"' in batch.call_args.kwargs["query"]
    emergency.assert_not_called()


@pytest.mark.parametrize("query", [VIN.lower(), "ROBOPARK-1", "0447"])
def test_registry_search_vin_key_and_short_number(client, registry, query):
    with patch(
        "robopark_api.services.tracker_client.search_issues", return_value=[issue("ROBOPARK-1")]
    ):
        response = client.get("/robots", params={"query": query})
    assert response.status_code == 200
    assert [row["vin"] for row in response.json()["items"]] == [VIN]


def test_registry_cache_is_read_only_identity_bound_and_expires(client, db_session, registry):
    payload = {"isOnline": True, "batteriesStatus": {"chargePercents": 81}, "errors": ["motor"]}
    with (
        patch(
            "robopark_api.services.platform_settings.get_emergency_cookie_probe",
            return_value=("fake", "one"),
        ),
        patch(
            "robopark_api.services.platform_settings.record_emergency_cookie_probe",
            return_value=False,
        ),
        patch(
            "robopark_api.services.emergency_client.fetch_robot_payload", return_value=payload
        ) as emergency,
        patch(
            "robopark_api.services.tracker_client.search_issues", return_value=[issue("ROBOPARK-1")]
        ),
    ):
        emergency_cache.get_robot_payload(db=db_session, vin=VIN)
        emergency.reset_mock()
        response = client.get("/robots?state=online&active_errors=true&open_tasks=true")
        assert response.status_code == 200
        row = response.json()["items"][0]
        assert row["telemetry"]["charge_percent"] == 81
        assert row["telemetry"]["source"] == "emergency_cache"
        assert row["error_count"] == 1
        with patch(
            "robopark_api.services.platform_settings.get_emergency_cookie_probe",
            return_value=("fake", "two"),
        ):
            assert client.get("/robots").json()["items"][0]["telemetry"] is None
        with patch("robopark_api.services.emergency_cache.PAYLOAD_CACHE_TTL_SECONDS", 0):
            assert client.get("/robots").json()["items"][0]["telemetry"] is None
        emergency.assert_not_called()


def test_registry_pagination_after_aggregation_filters_without_count_truncation(client, registry):
    with patch(
        "robopark_api.services.tracker_client.search_issues",
        return_value=[
            issue("ROBOPARK-1"),
            issue("ROBOPARK-2"),
            issue("ROBOPARK-3", "448"),
            issue("ROBOPARK-4", "449", status="closed"),
        ],
    ) as batch:
        data = client.get("/robots?limit=1&offset=1&open_tasks=true&state=unknown").json()
        assert data["total"] == 2
        assert data["items"][0]["short_number"] == "448"
        assert data["items"][0]["task_count"] == 1
        assert data["has_more"] is False
        assert batch.call_count == 1


def test_registry_foreign_park_denied_before_network(client, db_session, registry):
    foreign = Park(name="Foreign", tag="Foreign", tracker_queue="ROBOPARK")
    db_session.add(foreign)
    db_session.commit()
    with patch("robopark_api.services.tracker_client.search_issues") as batch:
        assert client.get(f"/robots?park_id={foreign.id}").status_code == 403
        batch.assert_not_called()


def test_registry_driver_only_exact_new_and_moving_workflow_status(client, db_session, registry):
    user = User(
        username="driver-reg",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "driver"),
        access_status="approved",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(UserPark(user_id=user.id, park_id=registry.id))
    db_session.commit()
    login_as(client, "driver-reg", "secret")
    with patch(
        "robopark_api.services.tracker_client.search_issues",
        return_value=[
            issue("ROBOPARK-1", status="new"),
            issue("ROBOPARK-2", status="moving"),
            issue("ROBOPARK-3", "448", status="inProgress"),
        ],
    ):
        response = client.get("/robots")
    assert response.status_code == 200
    assert [(row["short_number"], row["task_count"]) for row in response.json()["items"]] == [
        ("447", 2)
    ]


def test_registry_upstream_failure_and_invalid_filters(client, registry):
    with patch(
        "robopark_api.services.tracker_client.search_issues", side_effect=TrackerError("offline")
    ):
        assert client.get("/robots").status_code == 502
    assert client.get("/robots?state=invalid").status_code == 422
    assert client.get("/robots?offset=-1").status_code == 422


def test_registry_requires_authentication(client):
    assert client.get("/robots").status_code == 401


def test_unknown_telemetry_remains_partial_even_when_error_filter_hides_rows(client, registry):
    with patch(
        "robopark_api.services.tracker_client.search_issues", return_value=[issue("ROBOPARK-1")]
    ):
        data = client.get("/robots?active_errors=true").json()
    assert data["items"] == []
    assert data["partial"] is True


def test_registry_all_assigned_parks_share_one_batch(client, db_session, registry, seed_mechanic):
    second = Park(name="Beta", tag="Beta", tracker_queue="ROBOLAB", is_active=True)
    db_session.add(second)
    db_session.flush()
    db_session.add(UserPark(user_id=seed_mechanic.id, park_id=second.id))
    db_session.commit()
    issues = [
        issue("ROBOPARK-1"),
        {**issue("ROBOLAB-2", tag="Beta"), "queue": "ROBOLAB"},
        {**issue("FOREIGN-3", "448"), "queue": "FOREIGN"},
    ]
    with patch("robopark_api.services.tracker_client.search_issues", return_value=issues) as batch:
        data = client.get("/robots").json()
    assert data["total"] == 1
    assert data["items"][0]["task_count"] == 2
    assert data["items"][0]["park_ids"] == [registry.id, second.id]
    batch.assert_called_once()
    assert '"ROBOLAB"' in batch.call_args.kwargs["query"]
    assert '"ROBOPARK"' in batch.call_args.kwargs["query"]


def test_registry_pending_user_denied_before_tracker(client, db_session, registry, seed_mechanic):
    seed_mechanic.access_status = "pending"
    db_session.commit()
    with patch("robopark_api.services.tracker_client.search_issues") as batch:
        assert client.get("/robots").status_code == 403
        batch.assert_not_called()
