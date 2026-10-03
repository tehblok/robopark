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


def test_analytics_drilldown_is_bounded_and_paged_for_same_period(
    client,
    db_session,
    seed_park_with_tracker,
):
    from robopark_api.services.analytics_history import record_observation

    account(db_session, seed_park_with_tracker)
    record_observation(
        db_session,
        park=seed_park_with_tracker,
        issues=[issue(f"ROBOPARK-{number:03d}") for number in range(70)],
        observed_at=datetime.now(UTC) - timedelta(hours=3),
        target_hours=24,
    )
    login_as(client, "analyst", "secret")
    response = client.get(f"/analytics?park_id={seed_park_with_tracker.id}&days=1")
    assert response.status_code == 200
    data = response.json()
    assert data["drilldown_task_keys_count"] == 70
    assert len(data["drilldown_task_keys"]) == 50
    assert data["series"]["backlog"]["task_keys_count"] == 70
    assert len(data["series"]["backlog"]["task_keys"]) == 50
    page = client.get(
        "/analytics/task-keys",
        params={
            "park_id": seed_park_with_tracker.id,
            "days": 1,
            "period_end": data["period"]["end"],
            "group": "all",
            "offset": 50,
        },
    )
    assert page.status_code == 200
    assert page.json() == {
        "task_keys": [f"ROBOPARK-{number:03d}" for number in range(50, 70)],
        "total": 70,
        "has_more": False,
    }
    stage_page = client.get(
        "/analytics/task-keys",
        params={
            "park_id": seed_park_with_tracker.id,
            "days": 1,
            "period_end": data["period"]["end"],
            "group": "workload",
            "key": "queued",
            "after": "ROBOPARK-049",
        },
    )
    assert stage_page.status_code == 200
    assert stage_page.json()["task_keys"] == page.json()["task_keys"]
    record_observation(
        db_session,
        park=seed_park_with_tracker,
        issues=[issue("ROBOPARK-025A")],
        observed_at=datetime.now(UTC) - timedelta(hours=5),
        target_hours=24,
    )
    stable_page = client.get(
        "/analytics/task-keys",
        params={
            "park_id": seed_park_with_tracker.id,
            "days": 1,
            "period_end": data["period"]["end"],
            "group": "all",
            "offset": 50,
            "after": "ROBOPARK-049",
        },
    )
    assert stable_page.status_code == 200
    assert stable_page.json()["task_keys"] == [f"ROBOPARK-{number:03d}" for number in range(50, 70)]
    assert stable_page.json()["total"] == 71
    assert stable_page.json()["has_more"] is False


def test_analytics_task_keys_page_checks_park_and_permissions(
    client,
    db_session,
    seed_park_with_tracker,
):
    account(db_session, seed_park_with_tracker, linked=False)
    login_as(client, "analyst", "secret")
    response = client.get(
        "/analytics/task-keys",
        params={
            "park_id": seed_park_with_tracker.id,
            "days": 1,
            "period_end": datetime.now(UTC).replace(minute=0, second=0, microsecond=0).isoformat(),
            "group": "all",
            "offset": 0,
        },
    )
    assert response.status_code == 403


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
    assert data["verified_closures"]["count"] == 0
    assert data["verified_closures"]["sla_on_time_percent"] is None
    assert data["verified_closures"]["downtime_sample_count"] == 0
    assert data["verified_closures"]["median_downtime_hours"] is None
    assert data["verified_closures"]["p90_downtime_hours"] is None
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


def test_analytics_uses_park_timezone_for_period_labels(db_session, seed_park_with_tracker):
    seed_park_with_tracker.timezone = "Asia/Yekaterinburg"
    db_session.commit()
    assert build(db_session, seed_park_with_tracker)["timezone"] == "Asia/Yekaterinburg"


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
    from robopark_api.services.tracker_history import ingest_status_history

    for key, queued_at in [
        ("ROBOPARK-1", END - timedelta(hours=30)),
        ("ROBOPARK-2", END - timedelta(hours=10)),
    ]:
        ingest_status_history(
            db_session,
            issue_key=key,
            park=seed_park_with_tracker,
            history=[
                {
                    "updatedAt": queued_at.isoformat(),
                    "fields": [
                        {"field": {"id": "status"}, "to": {"key": "queued", "display": "В очереди"}}
                    ],
                }
            ],
        )
    observe(
        db_session, seed_park_with_tracker, 4, [issue(), issue("ROBOPARK-2", "diagnostics", age=10)]
    )
    observe(db_session, seed_park_with_tracker, 2, [issue()])
    data = build(db_session, seed_park_with_tracker, "1d")
    assert data["series"]["backlog"]["value"] == 1.5
    bands = {row["key"]: row for row in data["backlog_age_bands"]}
    assert bands["under_24h"]["value"] == 0.5
    assert bands["24_to_72h"]["value"] == 1
    assert data["sla_trend"]["value"] is not None
    assert data["sla_trend"]["sample_count"] == 3
    assert data["drilldown_task_keys"] == ["ROBOPARK-1", "ROBOPARK-2"]
    workload = {row["key"]: row for row in data["workload"]}
    assert workload["diagnostics"]["value"] == 0.5
    assert workload["diagnostics"]["task_keys"] == ["ROBOPARK-2"]


def test_series_points_do_not_repeat_period_drilldown_keys(
    db_session,
    seed_park_with_tracker,
):
    observe(db_session, seed_park_with_tracker, 4, [issue("ROBOPARK-1")])
    observe(db_session, seed_park_with_tracker, 2, [issue("ROBOPARK-1")])

    data = build(db_session, seed_park_with_tracker)
    backlog = data["series"]["backlog"]
    assert backlog["task_keys"] == ["ROBOPARK-1"]
    assert backlog["task_keys_count"] == 1
    assert all(point["task_keys"] == [] for point in backlog["points"])
    queued = next(row for row in data["workload"] if row["key"] == "queued")
    assert queued["task_keys"] == ["ROBOPARK-1"]
    assert all(point["task_keys"] == [] for point in queued["points"])
    assert data["drilldown_task_keys"] == ["ROBOPARK-1"]
    assert data["drilldown_task_keys_count"] == 1


def test_verified_closures_count_each_park_task_once_after_reopening(
    db_session,
    seed_park_with_tracker,
):
    from robopark_api.services.tracker_history import ingest_status_history

    def change(at, key, display):
        return {
            "updatedAt": at.isoformat(),
            "fields": [
                {
                    "field": {"id": "status"},
                    "to": {"key": key, "display": display},
                }
            ],
        }

    park = seed_park_with_tracker
    other = Park(name="Other", tag="Other", tracker_queue="ROBOPARK", is_active=True)
    db_session.add(other)
    db_session.commit()
    for issue_key, scope, statuses in [
        (
            "ROBOPARK-1",
            park,
            [
                (-30, "queued", "В очереди"),
                (-6, "closed", "Закрыт"),
                (-4, "inProgress", "В работе"),
                (-2, "closed", "Закрыт"),
            ],
        ),
        ("ROBOPARK-2", park, [(-7, "queued", "В очереди"), (-1, "cancelled", "Отменён")]),
        ("ROBOPARK-3", other, [(-7, "queued", "В очереди"), (-1, "closed", "Закрыт")]),
        ("ROBOPARK-4", park, [(-7, "queued", "В очереди"), (-1, "resolved", "Решён")]),
        ("ROBOPARK-5", park, [(-7, "queued", "В очереди"), (1, "closed", "Закрыт")]),
        (
            "ROBOPARK-6",
            park,
            [
                (-7, "queued", "В очереди"),
                (-5, "closed", "Закрыт"),
                (-2, "inProgress", "В работе"),
            ],
        ),
    ]:
        ingest_status_history(
            db_session,
            issue_key=issue_key,
            park=scope,
            history=[
                change(END + timedelta(hours=offset), key, display)
                for offset, key, display in statuses
            ],
        )
    result = build(db_session, park)
    assert result["verified_closures"] == {
        "count": 2,
        "task_keys": ["ROBOPARK-1", "ROBOPARK-4"],
        "source": "tracker_status_history",
        "complete": False,
        "sla_on_time_count": 1,
        "sla_late_count": 1,
        "sla_unknown_count": 0,
        "sla_on_time_percent": 50.0,
        "downtime_sample_count": 2,
        "median_downtime_hours": 17.0,
        "p90_downtime_hours": 28.0,
    }


def test_verified_closure_sla_treats_exact_park_time_deadline_as_on_time(
    db_session,
    seed_park_with_tracker,
):
    from robopark_api.services.tracker_history import ingest_status_history

    queued = datetime(2026, 9, 5, 17, tzinfo=UTC)  # 20:00 in Europe/Moscow.
    deadline = datetime(2026, 9, 6, 10, tzinfo=UTC)  # 13:00 next day.
    for key, closed in [
        ("ROBOPARK-DUE", deadline),
        ("ROBOPARK-LATE", deadline + timedelta(seconds=1)),
    ]:
        ingest_status_history(
            db_session,
            issue_key=key,
            park=seed_park_with_tracker,
            history=[
                {
                    "updatedAt": queued.isoformat(),
                    "fields": [{"field": {"id": "status"}, "to": {"key": "queued"}}],
                },
                {
                    "updatedAt": closed.isoformat(),
                    "fields": [{"field": {"id": "status"}, "to": {"key": "closed"}}],
                },
            ],
        )
    summary = build(db_session, seed_park_with_tracker)["verified_closures"]
    assert summary["sla_on_time_count"] == 1
    assert summary["sla_late_count"] == 1
    assert summary["sla_on_time_percent"] == 50.0


def test_sla_missing_queue_history_and_unknown_downtime_do_not_become_zero(
    db_session, seed_park_with_tracker
):
    observe(db_session, seed_park_with_tracker, 4, [issue()], target=None)
    observe(db_session, seed_park_with_tracker, 2, [{**issue(), "created": "invalid"}])
    data = build(db_session, seed_park_with_tracker)
    assert data["sla_trend"]["value"] is None
    assert data["sla_trend"]["observed_buckets"] == 0
    assert data["backlog_age_bands"][-1]["value"] == 1
    assert "queue_history_unavailable" in data["warnings"]


def test_invalid_historical_timezone_does_not_break_analytics(db_session, seed_park_with_tracker):
    from robopark_api.models import TrackerIssueHistoryState
    from robopark_api.services.tracker_history import ingest_status_history

    ingest_status_history(
        db_session,
        issue_key="ROBOPARK-1",
        park=seed_park_with_tracker,
        history=[
            {
                "updatedAt": (END - timedelta(hours=5)).isoformat(),
                "fields": [
                    {"field": {"id": "status"}, "to": {"key": "queued", "display": "В очереди"}}
                ],
            }
        ],
    )
    state = db_session.get(TrackerIssueHistoryState, "ROBOPARK-1")
    state.anchor_timezone = "Invalid/Timezone"
    db_session.commit()
    observe(db_session, seed_park_with_tracker, 2, [issue()])

    data = build(db_session, seed_park_with_tracker)
    assert data["sla_trend"]["value"] is None
    bands = {row["key"]: row for row in data["backlog_age_bands"]}
    assert bands["under_24h"]["value"] == 1
    assert bands["unknown"]["value"] == 0
    assert "queue_history_unavailable" in data["warnings"]
    assert "invalid_history_timezone" in data["warnings"]


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


def test_stage_duration_does_not_bridge_a_missing_snapshot(
    db_session,
    seed_park_with_tracker,
):
    observe(db_session, seed_park_with_tracker, 6, [issue()])
    # The four-hour bucket is missing: the transition could have happened then.
    observe(db_session, seed_park_with_tracker, 2, [issue(status="diagnostics")])
    rows = {row["key"]: row for row in build(db_session, seed_park_with_tracker)["stage_durations"]}
    assert rows["queued"]["value"] is None
    assert rows["queued"]["sample_count"] == 0


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
    monkeypatch.setattr(tracker_client, "search_closed_history_page", lambda **kw: [])
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
    from robopark_api.services.tracker_history import ingest_status_history

    queued_at = datetime.now(UTC) - timedelta(hours=4)
    ingest_status_history(
        db_session,
        issue_key="ROBOPARK-2",
        park=seed_park_with_tracker,
        history=[
            {
                "updatedAt": queued_at.isoformat(),
                "fields": [
                    {"field": {"id": "status"}, "to": {"key": "queued", "display": "В очереди"}}
                ],
            },
            {
                "updatedAt": (queued_at + timedelta(hours=2)).isoformat(),
                "fields": [
                    {"field": {"id": "status"}, "to": {"key": "closed", "display": "Закрыт"}}
                ],
            },
        ],
    )

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
    assert response.json()["verified_closures"]["count"] is None
    assert response.json()["verified_closures"]["task_keys"] == []
    page = client.get(
        "/analytics/task-keys",
        params={
            "park_id": seed_park_with_tracker.id,
            "days": 1,
            "period_end": response.json()["period"]["end"],
            "group": "all",
        },
    )
    assert page.status_code == 200
    assert page.json()["task_keys"] == ["ROBOPARK-1"]


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


def test_snapshot_retention_keeps_first_queue_anchor_for_late_reopening(
    db_session,
    seed_park_with_tracker,
    monkeypatch,
):
    from robopark_api.models import AnalyticsSnapshot, TrackerIssueHistoryState
    from robopark_api.services.analytics_history import scan_all_parks_once
    from robopark_api.services.tracker_history import ingest_status_history

    park = seed_park_with_tracker
    key = "ROBOPARK-REOPEN"
    queued_at = NOW - timedelta(days=45)

    def change(at, status):
        return {
            "updatedAt": at.isoformat(),
            "fields": [
                {
                    "field": {"id": "status"},
                    "to": {"key": status},
                }
            ],
        }

    history = [change(queued_at, "queued"), change(NOW - timedelta(days=35), "closed")]
    ingest_status_history(db_session, issue_key=key, park=park, history=history)
    observe(db_session, park, 31 * 24, [issue(key)])
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda db: None)
    assert scan_all_parks_once(db_session, now=NOW) == 0
    assert db_session.scalars(select(AnalyticsSnapshot)).all() == []

    ingest_status_history(
        db_session,
        issue_key=key,
        park=park,
        history=[*history, change(NOW - timedelta(hours=2), "inProgress")],
    )
    state = db_session.get(TrackerIssueHistoryState, key)
    assert state.first_queued_at.replace(tzinfo=UTC) == queued_at
    assert state.anchor_timezone == park.timezone
    assert state.terminal_at is None


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
