from unittest.mock import patch

import pytest
from sqlalchemy import event

from conftest import login_as, role_id_for
from robopark_api.models import Park, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services import emergency_cache, rbac
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


@pytest.mark.parametrize("query", ["447", "A447", "a447", "[A447]", "[a447]", VIN.lower()])
def test_registry_aggregates_supported_robot_aliases_without_substring_matches(
    client, registry, query
):
    aliases = ["447", "0447", "A447", "a447", "[A447]", "[a447]", "[447]", VIN.lower()]
    unrelated = ["A1447", "other447", "A447B", "[A447] extra", "447/448", "[A447", "A447]"]
    items = [issue(f"ROBOPARK-{index}", alias) for index, alias in enumerate(aliases)]
    items.extend(issue(f"OTHER-{index}", raw) for index, raw in enumerate(unrelated))
    items.append(issue("FOREIGN-1", "A447", tag="Foreign"))
    with patch("robopark_api.services.tracker_cache.search_issues", return_value=items):
        response = client.get("/robots", params={"park_id": registry.id, "query": query})
    assert response.status_code == 200
    assert response.json()["total"] == 1
    row = response.json()["items"][0]
    assert row["vin"] == VIN
    assert row["short_number"] == "447"
    assert row["task_count"] == 8
    assert row["task_keys"] == [f"ROBOPARK-{index}" for index in range(8)]


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


@pytest.mark.parametrize("role", ["driver", "mechanic", "operator", "admin", "royal"])
def test_registry_all_parks_excludes_inactive_identities_tasks_and_cache_lookups(
    client, db_session, registry, seed_mechanic, role
):
    inactive = Park(name="Inactive", tag="Inactive", tracker_queue="ROBOPARK", is_active=False)
    db_session.add(inactive)
    db_session.flush()
    db_session.add(UserPark(user_id=seed_mechanic.id, park_id=inactive.id))
    seed_mechanic.role_id = role_id_for(db_session, role)
    db_session.commit()
    login_as(client, "mech1", "secret")
    with (
        patch(
            "robopark_api.services.tracker_cache.search_issues",
            return_value=[
                issue("ROBOPARK-1"),
                issue("ROBOPARK-2", "448", tag="Inactive"),
                issue("ROBOPARK-3", tag="Inactive"),
            ],
        ) as batch,
        patch("robopark_api.services.emergency_cache.peek_robot_payloads", return_value={}) as peek,
        patch("robopark_api.services.emergency_client.fetch_robot_payload") as emergency,
    ):
        response = client.get("/robots")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        row = data["items"][0]
        assert row["park_ids"] == [registry.id]
        assert row["issue_keys"] == row["task_keys"] == ["ROBOPARK-1"]
        assert row["task_count"] == 1
        assert peek.call_args.kwargs["vins"] == [VIN]
        assert client.get(f"/robots?park_id={inactive.id}").status_code == 403
        batch.assert_called_once()
        emergency.assert_not_called()


def test_registry_requires_active_queue_and_tag_pair_even_without_selected_park(
    client, db_session, registry, seed_mechanic
):
    second = Park(name="Beta", tag="Beta", tracker_queue="ROBOLAB", is_active=True)
    db_session.add(second)
    db_session.flush()
    db_session.add(UserPark(user_id=seed_mechanic.id, park_id=second.id))
    db_session.commit()
    with (
        patch(
            "robopark_api.services.tracker_cache.search_issues",
            return_value=[issue("ROBOPARK-1"), issue("ROBOPARK-2", "448", tag="Beta")],
        ),
        patch("robopark_api.services.emergency_cache.peek_robot_payloads", return_value={}) as peek,
    ):
        data = client.get("/robots").json()
    assert data["total"] == 1
    assert data["items"][0]["issue_keys"] == ["ROBOPARK-1"]
    assert peek.call_args.kwargs["vins"] == [VIN]


def test_registry_authorization_sql_is_constant_for_one_hundred_and_thousand_issues(
    client, db_engine, registry, record_property
):
    statements = []

    def record_sql(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(db_engine, "before_cursor_execute", record_sql)
    try:
        with patch("robopark_api.services.tracker_cache.search_issues") as batch:
            batch.return_value = [issue("ROBOPARK-1")]
            assert client.get("/robots").status_code == 200  # Warm ORM relationships consistently.
            counts = []
            for size in (1, 100, 1000):
                batch.return_value = [issue(f"ROBOPARK-{index}") for index in range(size)]
                batch.reset_mock()
                statements.clear()
                data = client.get("/robots").json()
                counts.append(len(statements))
                assert data["items"][0]["task_count"] == size
                batch.assert_called_once()
            record_property("sql_queries_for_1_100_1000", counts)
            assert counts[0] == counts[1] == counts[2], counts
            assert counts[0] <= 12, counts
    finally:
        event.remove(db_engine, "before_cursor_execute", record_sql)


@pytest.mark.parametrize("permission", ["nav.robot_search", "tracker.read", "nav.emergency"])
def test_registry_preserves_effective_permission_overrides(
    client, db_session, registry, seed_mechanic, permission
):
    permissions = rbac.permissions_for_user(db_session, seed_mechanic) - {permission}
    rbac.set_user_effective_permissions(db_session, seed_mechanic, list(permissions))
    db_session.commit()
    with (
        patch(
            "robopark_api.services.tracker_cache.search_issues", return_value=[issue("ROBOPARK-1")]
        ) as batch,
        patch("robopark_api.services.emergency_cache.peek_robot_payloads") as peek,
    ):
        response = client.get("/robots")
    peek.assert_not_called()
    if permission == "nav.emergency":
        assert response.status_code == 200
        assert response.json()["items"][0]["telemetry"] is None
        batch.assert_called_once()
    else:
        assert response.status_code == 403
        batch.assert_not_called()
