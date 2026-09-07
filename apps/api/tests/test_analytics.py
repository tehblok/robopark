from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from conftest import login_as, role_id_for
from robopark_api.models import Park, ParkBlockerHistory, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services import platform_settings, rbac, tracker_client

NOW = datetime(2026, 9, 6, 12, 30, tzinfo=UTC)
END = NOW.replace(minute=0)


def account(db, park, *, linked=True, permissions=None, role="operator"):
    user = User(
        username="analyst",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db, role),
        access_status="approved",
        is_active=True,
    )
    db.add(user)
    db.flush()
    if linked:
        db.add(UserPark(user_id=user.id, park_id=park.id))
    if permissions is not None:
        rbac.set_user_effective_permissions(db, user, permissions)
    db.commit()
    return user


def issue(key="ROBOPARK-1", status="queued", age=30):
    return {
        "key": key,
        "queue": "ROBOPARK",
        "tags": ["Alpha"],
        "status_key": status,
        "status": status,
        "created": (END - timedelta(hours=age)).isoformat(),
    }


def observe(db, park, hours, items, target=24):
    from robopark_api.services.analytics_history import record_observation

    return record_observation(
        db, park=park, issues=items, observed_at=END - timedelta(hours=hours), target_hours=target
    )


def build(db, park, bucket="2h"):
    from robopark_api.services.analytics import build_analytics

    return build_analytics(db, park_id=park.id, days=1, bucket=bucket, now=NOW).model_dump()


def test_endpoint_is_historical_without_live_tracker(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    account(db_session, seed_park_with_tracker)
    monkeypatch.setattr(
        tracker_client,
        "fetch_park_blockers",
        lambda **kw: pytest.fail("analytics must read persisted history"),
    )
    login_as(client, "analyst", "secret")
    response = client.get(f"/analytics?park_id={seed_park_with_tracker.id}&days=1&bucket=2h")
    assert response.status_code == 200
    data = response.json()
    assert data["coverage"]["observations"]["expected_buckets"] == 12
    assert data["series"]["arrived"]["value"] is None
    assert data["drilldown_task_keys"] == []


@pytest.mark.parametrize(
    "linked,permissions",
    [
        (False, ["nav.analytics", "tracker.read"]),
        (True, ["nav.dashboard", "tracker.read"]),
        (True, ["nav.analytics"]),
    ],
)
def test_analytics_rejects_foreign_park_or_missing_permission(
    client,
    db_session,
    seed_park_with_tracker,
    linked,
    permissions,
):
    account(db_session, seed_park_with_tracker, linked=linked, permissions=permissions)
    login_as(client, "analyst", "secret")
    assert client.get(f"/analytics?park_id={seed_park_with_tracker.id}").status_code == 403


def test_comparison_cannot_read_an_unassigned_park(client, db_session, seed_park_with_tracker):
    account(db_session, seed_park_with_tracker)
    other = Park(name="Private", tag="Private", is_active=True)
    db_session.add(other)
    db_session.commit()
    login_as(client, "analyst", "secret")
    assert client.get(f"/analytics?park_id={seed_park_with_tracker.id}").status_code == 200
    assert client.get(f"/analytics?park_id={other.id}").status_code == 403


def test_bucket_totals_include_only_valid_closed_v2_history(db_session, seed_park_with_tracker):
    for hours, arrived, departed, version, scanned in [
        (6, 5, 2, 2, NOW),
        (2, 0, 1, 2, NOW),
        (4, 99, 99, 1, NOW),
        (8, 99, 99, 2, None),
        (0, 99, 99, 2, NOW),
    ]:
        db_session.add(
            ParkBlockerHistory(
                park_id=seed_park_with_tracker.id,
                bucket_start=END - timedelta(hours=hours),
                arrived_count=arrived,
                departed_count=departed,
                definition_version=version,
                scanned_at=scanned,
            )
        )
    db_session.commit()
    result = build(db_session, seed_park_with_tracker, "1d")
    arrived = result["series"]["arrived"]
    assert arrived["value"] == 5
    assert arrived["points"][0]["value"] == 5
    assert arrived["observed_buckets"] == 2
    assert arrived["expected_buckets"] == 12
    assert arrived["complete"] is False
    assert result["series"]["departed"]["value"] == 3
    fine = build(db_session, seed_park_with_tracker)["series"]["arrived"]["points"]
    assert len(fine) == 12
    assert fine[-1]["value"] == 0  # A measured zero remains a real zero.
    assert fine[-2]["value"] is None  # Legacy is not measured zero.


def test_no_observations_means_null_metrics_not_zero(db_session, seed_park_with_tracker):
    data = build(db_session, seed_park_with_tracker)
    for metric in [
        *data["series"].values(),
        *data["backlog_age_bands"],
        data["sla_trend"],
        *data["workload"],
        *data["stage_durations"],
    ]:
        assert metric["value"] is None
        assert metric["observed_buckets"] == 0
        assert metric["complete"] is False
        assert metric["unit"]
        assert metric["period"]["end"] == END


def test_empty_success_is_distinct_from_missing_and_snapshot_is_immutable(
    db_session,
    seed_park_with_tracker,
):
    from robopark_api.models import AnalyticsObservation, AnalyticsSnapshot

    observe(db_session, seed_park_with_tracker, 2, [])
    observe(db_session, seed_park_with_tracker, 2, [issue()])
    result = build(db_session, seed_park_with_tracker)
    assert result["series"]["backlog"]["value"] == 0
    assert result["series"]["backlog"]["points"][-1]["value"] == 0
    assert result["series"]["backlog"]["points"][-2]["value"] is None
    assert result["coverage"]["observations"]["observed_buckets"] == 1
    assert result["sla_trend"]["value"] is None  # No evaluated tasks, no percentage.
    assert len(db_session.scalars(select(AnalyticsSnapshot)).all()) == 1
    assert db_session.scalars(select(AnalyticsObservation)).all() == []


def test_age_bands_sla_and_workload_are_historical_means_with_real_keys(
    db_session,
    seed_park_with_tracker,
):
    observe(
        db_session, seed_park_with_tracker, 4, [issue(), issue("ROBOPARK-2", "diagnostics", age=10)]
    )
    observe(db_session, seed_park_with_tracker, 2, [issue()])
    data = build(db_session, seed_park_with_tracker, "1d")
    assert data["series"]["backlog"]["value"] == 1.5
    bands = {row["key"]: row for row in data["backlog_age_bands"]}
    assert bands["under_24h"]["value"] == 0.5
    assert bands["24_to_72h"]["value"] == 1
    assert data["sla_trend"]["value"] == 0
    assert data["sla_trend"]["sample_count"] == 2
    assert data["drilldown_task_keys"] == ["ROBOPARK-1", "ROBOPARK-2"]
    workload = {row["key"]: row for row in data["workload"]}
    assert workload["diagnostics"]["value"] == 0.5
    assert workload["diagnostics"]["task_keys"] == ["ROBOPARK-2"]


def test_sla_missing_policy_and_unknown_age_do_not_become_zero(db_session, seed_park_with_tracker):
    observe(db_session, seed_park_with_tracker, 4, [issue()], target=None)
    observe(db_session, seed_park_with_tracker, 2, [{**issue(), "created": "invalid"}])
    data = build(db_session, seed_park_with_tracker)
    assert data["sla_trend"]["value"] is None
    assert data["sla_trend"]["observed_buckets"] == 0
    assert data["backlog_age_bands"][-1]["value"] == 0.5


def test_stage_duration_requires_an_observed_change_and_uses_first_observed_status(
    db_session,
    seed_park_with_tracker,
):
    observe(db_session, seed_park_with_tracker, 6, [issue()])
    assert all(
        row["value"] is None for row in build(db_session, seed_park_with_tracker)["stage_durations"]
    )
    observe(db_session, seed_park_with_tracker, 4, [issue()])
    assert all(
        row["value"] is None for row in build(db_session, seed_park_with_tracker)["stage_durations"]
    )
    observe(db_session, seed_park_with_tracker, 2, [issue(status="diagnostics")])
    rows = {row["key"]: row for row in build(db_session, seed_park_with_tracker)["stage_durations"]}
    assert rows["queued"]["value"] == 4
    assert rows["queued"]["sample_count"] == 1
    assert rows["queued"]["task_keys"] == ["ROBOPARK-1"]
    assert rows["diagnostics"]["value"] is None


def test_observations_cannot_mix_parks_or_statuses_within_one_bucket(
    db_session, seed_park_with_tracker
):
    from robopark_api.models import AnalyticsObservation

    observe(
        db_session,
        seed_park_with_tracker,
        2,
        [
            issue(),
            issue(),
            {**issue("PRIVATE-1"), "queue": "PRIVATE"},
            {**issue("ROBOPARK-9"), "tags": ["Other"]},
        ],
    )
    rows = db_session.scalars(select(AnalyticsObservation)).all()
    assert [(row.issue_key, row.status) for row in rows] == [("ROBOPARK-1", "queued")]


def test_scan_records_successful_empty_buckets_but_not_failed_fetches(
    db_session,
    seed_park_with_tracker,
    monkeypatch,
):
    from robopark_api.services.analytics_history import scan_all_parks_once

    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda db: "test-token")
    monkeypatch.setattr(tracker_client, "fetch_park_blockers", lambda **kw: [])
    assert scan_all_parks_once(db_session, now=END - timedelta(hours=4)) == 1

    def fail(**kwargs):
        raise tracker_client.TrackerError("unavailable")

    monkeypatch.setattr(tracker_client, "fetch_park_blockers", fail)
    assert scan_all_parks_once(db_session, now=END - timedelta(hours=2)) == 0
    data = build(db_session, seed_park_with_tracker)
    assert data["coverage"]["observations"]["observed_buckets"] == 1
    assert data["series"]["backlog"]["points"][-1]["value"] is None


def test_full_measured_day_is_complete_and_validation_is_bounded(
    client,
    db_session,
    seed_park_with_tracker,
):
    for hours in range(2, 25, 2):
        observe(db_session, seed_park_with_tracker, hours, [])
    metric = build(db_session, seed_park_with_tracker)["series"]["backlog"]
    assert metric["complete"] is True
    assert metric["observed_buckets"] == metric["expected_buckets"] == 12
    account(db_session, seed_park_with_tracker)
    login_as(client, "analyst", "secret")
    for query in ["days=31", "days=0", "bucket=5m"]:
        assert (
            client.get(f"/analytics?park_id={seed_park_with_tracker.id}&{query}").status_code == 422
        )


def test_unknown_age_is_not_a_measured_zero_in_known_age_bands(db_session, seed_park_with_tracker):
    observe(db_session, seed_park_with_tracker, 2, [{**issue(), "created": None}])
    data = build(db_session, seed_park_with_tracker)
    assert all(band["value"] is None for band in data["backlog_age_bands"][:-1])
    assert data["backlog_age_bands"][-1]["value"] == 1


def test_driver_historical_keys_respect_status_scope(client, db_session, seed_park_with_tracker):
    account(
        db_session,
        seed_park_with_tracker,
        role="driver",
        permissions=["nav.analytics", "tracker.read"],
    )
    # Use the endpoint's current period, not the deterministic aggregation test clock.
    from robopark_api.services.analytics_history import record_observation

    record_observation(
        db_session,
        park=seed_park_with_tracker,
        issues=[issue("ROBOPARK-1", "moving"), issue("ROBOPARK-2", "queued")],
        observed_at=datetime.now(UTC) - timedelta(hours=3),
        target_hours=24,
    )
    login_as(client, "analyst", "secret")
    response = client.get(f"/analytics?park_id={seed_park_with_tracker.id}&days=1")
    assert response.status_code == 200
    assert response.json()["drilldown_task_keys"] == ["ROBOPARK-1"]
    assert response.json()["series"]["arrived"]["value"] is None


def test_stage_intervals_do_not_bridge_an_observed_absence(db_session, seed_park_with_tracker):
    observe(db_session, seed_park_with_tracker, 6, [issue()])
    observe(db_session, seed_park_with_tracker, 4, [])
    observe(db_session, seed_park_with_tracker, 2, [issue(status="diagnostics")])
    assert all(
        row["value"] is None for row in build(db_session, seed_park_with_tracker)["stage_durations"]
    )


@pytest.mark.parametrize(
    "status_key,status_display",
    [
        ("waitingforrelocation", "Ожидает перемещения"),
        ("queued", "Подготовка к перемещению"),
    ],
)
@pytest.mark.parametrize("role", ["driver", "operator", "admin", "royal"])
def test_analytics_authorization_ignores_relocation_and_display_hints_in_every_metric(
    client,
    db_session,
    seed_park_with_tracker,
    status_key,
    status_display,
    role,
):
    from robopark_api.models import AnalyticsObservation
    from robopark_api.services.analytics_history import record_observation
    from robopark_api.services.tracker_policy import is_issue_status_visible

    user = account(
        db_session, seed_park_with_tracker, role=role, permissions=["nav.analytics", "tracker.read"]
    )
    observed_at = datetime.now(UTC) - timedelta(hours=5)
    hidden = {
        **issue("ROBOPARK-HIDDEN", status_key),
        "status": status_display,
        "in_relocation": "1",
        "created": (observed_at - timedelta(hours=30)).isoformat(),
    }
    assert is_issue_status_visible(user, hidden) is (role != "driver")
    record_observation(
        db_session,
        park=seed_park_with_tracker,
        issues=[hidden],
        observed_at=observed_at,
        target_hours=24,
    )
    # A second raw status within the same hinted display group must not leak a duration.
    record_observation(
        db_session,
        park=seed_park_with_tracker,
        issues=[{**hidden, "status_key": "blocked", "status": "Ожидает перемещения"}],
        observed_at=observed_at + timedelta(hours=2),
        target_hours=24,
    )
    login_as(client, "analyst", "secret")
    response = client.get(f"/analytics?park_id={seed_park_with_tracker.id}&days=1&bucket=2h")
    assert response.status_code == 200
    data = response.json()
    assert data["series"]["backlog"]["value"] == (0 if role == "driver" else 1)
    assert data["drilldown_task_keys"] == ([] if role == "driver" else ["ROBOPARK-HIDDEN"])
    metric_groups = [
        *data["series"].values(),
        *data["backlog_age_bands"],
        data["sla_trend"],
        *data["workload"],
        *data["stage_durations"],
    ]
    if role == "driver":
        for metric in metric_groups:
            assert metric["task_keys"] == []
            assert metric["sample_count"] == 0
            assert metric["value"] is None or metric["value"] == 0
            for point in metric.get("points", []):
                assert point["task_keys"] == []
                assert point["sample_count"] == 0
                assert point["value"] is None or point["value"] == 0
    else:
        assert data["sla_trend"]["value"] is None
        assert next(row for row in data["workload"] if row["key"] == "moving")["value"] == 1
        duration = next(row for row in data["stage_durations"] if row["key"] == "moving")
        assert duration["value"] == 2
        assert duration["task_keys"] == ["ROBOPARK-HIDDEN"]
    first = db_session.scalar(
        select(AnalyticsObservation).order_by(AnalyticsObservation.bucket_start)
    )
    assert first.status_bucket == "moving"
    assert first.authorization_status == ("queued" if status_key == "queued" else None)


@pytest.mark.parametrize("source", ["no_token", "unavailable"])
def test_retention_is_committed_without_collection_and_preserves_current_history(
    db_session,
    db_engine,
    seed_park_with_tracker,
    monkeypatch,
    source,
):
    from sqlalchemy.orm import Session

    from robopark_api.models import AnalyticsObservation, AnalyticsSnapshot
    from robopark_api.services.analytics_history import scan_all_parks_once

    observe(db_session, seed_park_with_tracker, 31 * 24, [issue("ROBOPARK-OLD")])
    observe(db_session, seed_park_with_tracker, 30 * 24, [issue("ROBOPARK-BOUNDARY")])
    observe(db_session, seed_park_with_tracker, 2, [issue("ROBOPARK-CURRENT")])
    monkeypatch.setattr(
        platform_settings,
        "get_tracker_token",
        lambda db: None if source == "no_token" else "test-token",
    )

    def unavailable(**kwargs):
        if source == "no_token":
            pytest.fail("collection must not run without a token")
        raise tracker_client.TrackerError("unavailable")

    monkeypatch.setattr(tracker_client, "fetch_park_blockers", unavailable)
    assert scan_all_parks_once(db_session, now=NOW) == 0
    # A separate connection proves cleanup was committed even without a successful scan.
    with Session(db_engine) as reader:
        assert len(reader.scalars(select(AnalyticsSnapshot)).all()) == 2
        assert sorted(
            row.issue_key for row in reader.scalars(select(AnalyticsObservation)).all()
        ) == [
            "ROBOPARK-BOUNDARY",
            "ROBOPARK-CURRENT",
        ]


def test_permitted_workflow_keeps_its_display_stage_in_restricted_analytics(
    client,
    db_session,
    seed_park_with_tracker,
):
    from robopark_api.services.analytics_history import record_observation

    account(
        db_session,
        seed_park_with_tracker,
        role="mechanic",
        permissions=["nav.analytics", "tracker.read"],
    )
    observed_at = datetime.now(UTC) - timedelta(hours=3)
    record_observation(
        db_session,
        park=seed_park_with_tracker,
        issues=[
            {
                **issue(),
                "in_relocation": "1",
                "created": (observed_at - timedelta(hours=30)).isoformat(),
            }
        ],
        observed_at=observed_at,
        target_hours=24,
    )
    login_as(client, "analyst", "secret")
    response = client.get(f"/analytics?park_id={seed_park_with_tracker.id}&days=1")
    assert response.status_code == 200
    data = response.json()
    assert data["series"]["backlog"]["value"] == 1
    workload = {row["key"]: row for row in data["workload"]}
    assert "moving" in workload
    assert workload["moving"]["value"] == 1
    assert workload["moving"]["task_keys"] == ["ROBOPARK-1"]
    assert workload["queued"]["value"] == 0
