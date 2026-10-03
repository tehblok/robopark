import secrets
import time
from datetime import UTC, datetime, timedelta

import pytest

from conftest import login_as, role_id_for
from robopark_api.models import ParkBlockerHistory, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services import platform_settings, rbac, tracker_client, tracker_filters

NOW = datetime(2026, 9, 3, 12, 30, tzinfo=UTC)


def issue(key="ROBOPARK-1", status="new", age=12, login="operator.one"):
    created = (NOW - timedelta(hours=age)).isoformat()
    return {
        "key": key,
        "summary": key,
        "status": status,
        "status_key": status,
        "created": created,
        "status_history": (
            [
                {
                    "updatedAt": created,
                    "fields": [{"field": {"id": "status"}, "to": {"key": "queued"}}],
                }
            ]
            if status == "queued"
            else []
        ),
        "hours_created": str(age),
        "queue": "ROBOPARK",
        "tags": ["Alpha"],
        "robot": None,
        "resolution": "",
        "assignee": {"login": login, "display": "Operator One"} if login else None,
    }


def account(db, park, role="operator", username="subject", tracker_login=None, linked=True):
    user = User(
        username=username,
        password_hash=hash_password("secret"),
        role_id=role_id_for(db, role),
        access_status="approved",
        is_active=True,
        tracker_login=tracker_login,
    )
    db.add(user)
    db.flush()
    if linked:
        db.add(UserPark(user_id=user.id, park_id=park.id))
    if role == "driver":
        rbac.set_user_effective_permissions(
            db, user, [*rbac.permissions_for_user(db, user), "tracker.read"]
        )
    db.commit()
    return user


@pytest.fixture
def source(monkeypatch):
    rows = [
        issue(),
        issue("ROBOPARK-2", "queued"),
        issue("ROBOPARK-3", "diagnostics"),
        issue("ROBOPARK-4", "moving"),
        issue("ROBOPARK-5", "renewed"),
    ]
    calls = []
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda db: "test-token")

    def fetch(**kwargs):
        calls.append(kwargs)
        return rows

    monkeypatch.setattr(tracker_client, "fetch_park_blockers", fetch)
    return rows, calls


@pytest.mark.parametrize(
    "key,display,want",
    [
        ("new", "Новый", "new"),
        ("renewed", "Возобновлён", None),
        ("", "Новая", "new"),
        ("open", "", "new"),
        ("diagnosis", "", "diagnostics"),
        ("", " На диагностике ", "diagnostics"),
        ("inProgress", "В работе", None),
        ("queuedLater", "Будет в очереди", None),
        ("", "ЖДЁМ СМЕЖНИКОВ", "waiting_team"),
    ],
)
def test_status_classification_is_exact(key, display, want):
    assert tracker_filters.status_bucket(key, display) == want
    assert tracker_filters.issue_status_bucket({"status_key": key, "status": display}) == (
        want or "other"
    )


def test_sla_working_hours_boundaries_and_unknown_queue_times():
    from robopark_api.services.operations import calculate_sla

    rows = [
        issue("oldest", status="queued", age=20),
        issue("overdue", status="queued", age=10.01),
        issue("boundary", status="queued", age=10),
        issue("warning", status="queued", age=8),
        issue("safe", status="queued", age=7.99),
        issue("future", status="queued", age=-1),
        {**issue("invalid", status="queued"), "created": "nonsense", "status_history": []},
        {**issue("missing", status="queued"), "created": None, "status_history": []},
    ]
    result = calculate_sla(rows, target_hours=10, now=NOW, timezone="Europe/Moscow")
    assert result.evaluated_count == 5
    assert result.unknown_count == 3
    assert result.at_risk_count == 1
    assert result.overdue_count == 0
    assert result.overdue == []


def test_no_sla_target_is_unknown_not_zero():
    from robopark_api.services.operations import calculate_sla

    result = calculate_sla([issue()], target_hours=None, now=NOW, timezone="Europe/Moscow")
    assert result.target_hours is None
    assert result.evaluated_count == 0
    assert result.unknown_count == 1
    assert result.overdue_count is None
    assert result.at_risk_count is None
    assert result.overdue == []


def test_sla_continues_after_a_queued_task_moves_to_diagnostics():
    from robopark_api.services.operations import calculate_sla

    now = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)  # 15:00 Moscow
    queued = issue("queued", status="queued")
    queued["created"] = "2026-09-02T17:00:00+00:00"  # 20:00 Moscow: 1h + 6h
    queued["status_history"][0]["updatedAt"] = queued["created"]
    diagnostics = {
        **queued,
        "key": "diagnostics",
        "status": "diagnostics",
        "status_key": "diagnostics",
    }

    result = calculate_sla([queued, diagnostics], target_hours=4, now=now, timezone="Europe/Moscow")
    assert result.evaluated_count == 2
    assert result.unknown_count == 0
    assert result.overdue_count == 2
    assert {task.age_hours for task in result.overdue} == {7}


def test_sla_uses_the_selected_parks_timezone():
    from robopark_api.services.operations import calculate_sla, queued_working_hours

    queued = issue("queued", status="queued")
    queued["status_history"][0]["updatedAt"] = "2026-09-18T04:00:00Z"
    now = datetime(2026, 9, 18, 6, tzinfo=UTC)
    assert queued_working_hours(queued, now, timezone="Asia/Yekaterinburg") == 2
    result = calculate_sla(
        [queued],
        target_hours=5,
        now=now,
        timezone="Asia/Yekaterinburg",
    )
    assert result.evaluated_count == 1
    assert result.overdue_count == 0
    assert result.at_risk_count == 0
    assert result.unknown_count == 0


def test_open_task_becomes_overdue_after_a_deadline_at_closing_time():
    from robopark_api.services.operations import calculate_sla, calculate_workload, task_timing

    queued_at = datetime(2026, 9, 18, 13, tzinfo=UTC)  # 16:00 Moscow.
    due_at = datetime(2026, 9, 18, 18, tzinfo=UTC)  # 21:00 Moscow.
    queued = issue("night-edge", status="queued")
    queued["created"] = queued_at.isoformat()
    queued["status_history"][0]["updatedAt"] = queued_at.isoformat()

    at_deadline = calculate_sla([queued], target_hours=5, now=due_at, timezone="Europe/Moscow")
    assert at_deadline.overdue_count == 0

    after_deadline = due_at + timedelta(minutes=1)
    assert task_timing(queued, after_deadline, timezone="Europe/Moscow").sla_working_hours == 5
    overdue = calculate_sla([queued], target_hours=5, now=after_deadline, timezone="Europe/Moscow")
    assert overdue.overdue_count == 1
    assert overdue.overdue[0].key == "night-edge"
    assert (
        calculate_workload([queued], target_hours=5, now=after_deadline, timezone="Europe/Moscow")[
            0
        ].overdue_count
        == 1
    )


def test_invalid_historical_timezone_preserves_downtime_but_not_sla():
    from robopark_api.services.operations import calculate_sla, task_timing

    queued = issue("queued", status="queued", age=6)
    queued["sla_anchor_timezone"] = "Invalid/Timezone"
    timing = task_timing(queued, NOW, timezone="Europe/Moscow")
    assert timing.downtime_hours == 6
    assert timing.sla_deadline is None
    assert timing.sla_working_hours is None

    result = calculate_sla([queued], target_hours=5, now=NOW, timezone="Europe/Moscow")
    assert result.evaluated_count == 0
    assert result.unknown_count == 1
    assert result.overdue_count == 0


def test_task_timing_exposes_confirmed_anchor_timezone_for_device_countdown():
    from robopark_api.services.operations import task_timing

    queued = issue("queued", status="queued", age=1)
    queued["sla_anchor_timezone"] = "Europe/Moscow"
    timing = task_timing(queued, NOW, timezone="Asia/Yekaterinburg")
    assert timing.sla_timezone == "Europe/Moscow"
    queued["sla_anchor_timezone"] = "Invalid/Timezone"
    assert task_timing(queued, NOW, timezone="Europe/Moscow").sla_timezone is None


def test_unknown_queue_park_keeps_downtime_without_using_current_park_timezone():
    from robopark_api.services.operations import calculate_sla, task_timing

    queued = {
        **issue("queued", status="queued", age=6),
        "queued_at": (NOW - timedelta(hours=6)).isoformat(),
        "sla_source": "status_history",
        "sla_anchor_timezone": None,
    }
    timing = task_timing(queued, NOW, timezone="Europe/Moscow")
    assert timing.downtime_hours == 6
    assert timing.sla_deadline is None
    assert timing.sla_working_hours is None
    result = calculate_sla([queued], target_hours=5, now=NOW, timezone="Europe/Moscow")
    assert result.unknown_count == 1
    assert result.evaluated_count == 0


def test_sla_bounded_list_retains_exact_total():
    from robopark_api.services.operations import calculate_sla

    result = calculate_sla(
        [issue(str(i), status="queued", age=300 - i) for i in range(210)],
        target_hours=10,
        now=NOW,
        timezone="Europe/Moscow",
    )
    assert result.overdue_count == 210
    assert len(result.overdue) == 200
    assert result.overdue_truncated is True
    assert result.overdue[0].key == "0"


@pytest.mark.parametrize(
    "role,expected",
    [
        ("driver", ["ROBOPARK-1", "ROBOPARK-4"]),
        ("mechanic", [f"ROBOPARK-{i}" for i in range(1, 6)]),
        ("operator", [f"ROBOPARK-{i}" for i in range(1, 6)]),
        ("admin", [f"ROBOPARK-{i}" for i in range(1, 6)]),
        ("royal", [f"ROBOPARK-{i}" for i in range(1, 6)]),
    ],
)
def test_overview_role_defaults(client, db_session, seed_park_with_tracker, source, role, expected):
    account(db_session, seed_park_with_tracker, role)
    login_as(client, "subject", "secret")
    response = client.get(f"/operations/overview?park_id={seed_park_with_tracker.id}&status=all")
    assert response.status_code == 200
    data = response.json()
    assert [row["key"] for row in data["tasks"]] == expected
    assert data["selected_status"] == "all"
    assert data["counts"]["all"] == len(expected)
    assert data["tasks_total"] == len(expected)
    assert data["timezone"] == "Europe/Moscow"
    assert len(source[1]) == 1
    if role in {"driver", "mechanic"}:
        assert data["workload"] is None
    if role not in {"admin", "royal"}:
        assert data["operators"] is None


def test_overview_exposes_separate_working_sla_and_calendar_downtime(
    db_session,
    seed_park_with_tracker,
    source,
):
    from robopark_api.services.operations import build_overview

    park = seed_park_with_tracker
    user = account(db_session, park)
    result = build_overview(
        db_session,
        user,
        park,
        days=7,
        selected_status="all",
        now=NOW,
    )
    timing = {row.issue_key: row for row in result.task_timing}
    assert timing["ROBOPARK-2"].downtime_hours == 12
    assert timing["ROBOPARK-2"].sla_working_hours == 6.5
    assert timing["ROBOPARK-2"].sla_deadline == datetime(2026, 9, 3, 11, tzinfo=UTC)
    assert timing["ROBOPARK-1"].downtime_hours is None


def test_overview_open_requires_recent_tracker_data_without_silent_stale_fallback(
    monkeypatch,
    db_session,
    seed_park_with_tracker,
    source,
):
    from robopark_api.services import operations, tracker_cache

    park = seed_park_with_tracker
    user = account(db_session, park)
    original = tracker_cache.fetch_park_blockers
    options = []

    def observe(**kwargs):
        options.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(tracker_cache, "fetch_park_blockers", observe)
    operations.build_overview(db_session, user, park, days=7, selected_status="all", now=NOW)

    assert options[0]["max_age_seconds"] == 5.0
    assert options[0]["allow_stale"] is False
    assert len(source[1]) == 1


def test_overview_uses_local_task_owner_instead_of_tracker_assignee(
    client, db_session, seed_park_with_tracker, source
):
    operator = account(db_session, seed_park_with_tracker, "operator")
    mechanic = account(
        db_session,
        seed_park_with_tracker,
        "mechanic",
        username="local-mechanic",
    )
    from robopark_api.services.tracker_claims import claim_issue

    claim_issue(
        db_session,
        actor=operator,
        owner=mechanic,
        issue_key="ROBOPARK-1",
        park_id=seed_park_with_tracker.id,
    )
    login_as(client, "subject", "secret")

    data = client.get(f"/operations/overview?park_id={seed_park_with_tracker.id}&status=all").json()

    first = next(row for row in data["tasks"] if row["key"] == "ROBOPARK-1")
    assert first["assignee"]["login"] == "local-mechanic"
    assert any(row["login"] == "local-mechanic" for row in data["workload"])


def test_leadership_filter_counts_before_render_cap(
    client, db_session, seed_park_with_tracker, source
):
    account(db_session, seed_park_with_tracker)
    source[0][:] = [issue(str(i), age=400 - i) for i in range(210)] + [issue("queued", "queued")]
    login_as(client, "subject", "secret")
    data = client.get(f"/operations/overview?park_id={seed_park_with_tracker.id}&status=new").json()
    assert data["selected_status"] == "new"
    assert data["counts"]["all"] == 211
    assert data["counts"]["queued"] == 1
    assert data["tasks_total"] == 210
    assert data["tasks_truncated"] is True
    assert len(data["tasks"]) == 200
    assert data["tasks"][0]["key"] == "0"
    assert data["workload"][0]["open_count"] == 211
    assert data["workload"][0]["overdue_count"] == 1


@pytest.mark.parametrize("role,filter_value", [("driver", "queued")])
def test_overview_filter_cannot_expand_role_scope(
    client, db_session, seed_park_with_tracker, source, role, filter_value
):
    account(db_session, seed_park_with_tracker, role)
    login_as(client, "subject", "secret")
    assert (
        client.get(
            f"/operations/overview?park_id={seed_park_with_tracker.id}&status={filter_value}"
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "failure", ["foreign", "inactive", "pending", "tracker_denied", "nav_denied"]
)
def test_overview_fail_closed_before_source(
    client, db_session, seed_park_with_tracker, source, failure
):
    park = seed_park_with_tracker
    user = account(db_session, park, linked=failure != "foreign")
    if failure == "inactive":
        park.is_active = False
    elif failure == "pending":
        user.access_status = "pending"
    elif failure in {"tracker_denied", "nav_denied"}:
        denied = (
            {"tracker.read"} if failure == "tracker_denied" else {"nav.dashboard", "nav.analytics"}
        )
        rbac.set_user_effective_permissions(
            db_session, user, list(rbac.permissions_for_user(db_session, user) - denied)
        )
    db_session.commit()
    login_as(client, "subject", "secret")
    assert client.get(f"/operations/overview?park_id={park.id}").status_code == 403
    assert source[1] == []


def test_overview_analytics_permission_is_sufficient(
    client, db_session, seed_park_with_tracker, source
):
    user = account(db_session, seed_park_with_tracker)
    rbac.set_user_effective_permissions(db_session, user, ["nav.analytics", "tracker.read"])
    db_session.commit()
    login_as(client, "subject", "secret")
    assert (
        client.get(f"/operations/overview?park_id={seed_park_with_tracker.id}").status_code == 200
    )


def test_overview_upstream_failure_is_not_empty_success(
    client, db_session, seed_park_with_tracker, source, monkeypatch
):
    account(db_session, seed_park_with_tracker)

    def fail(**kwargs):
        raise tracker_client.TrackerError("test upstream unavailable")

    monkeypatch.setattr(tracker_client, "fetch_park_blockers", fail)
    login_as(client, "subject", "secret")
    response = client.get(f"/operations/overview?park_id={seed_park_with_tracker.id}")
    assert response.status_code == 502
    assert response.json()["detail"] == "tracker_upstream_error"


def test_overview_upstream_failure_does_not_reuse_expired_tracker_snapshot(
    client,
    db_session,
    seed_park_with_tracker,
    source,
    monkeypatch,
):
    from robopark_api.services import tracker_cache

    park = seed_park_with_tracker
    account(db_session, park)
    login_as(client, "subject", "secret")
    url = f"/operations/overview?park_id={park.id}"
    assert client.get(url).status_code == 200
    monkeypatch.setattr(tracker_cache._blockers_cache, "_ttl", 0.01)
    time.sleep(0.02)

    def fail(**_kwargs):
        raise tracker_client.TrackerError("test upstream unavailable")

    monkeypatch.setattr(tracker_client, "fetch_park_blockers", fail)
    response = client.get(url)
    assert response.status_code == 502
    assert response.json()["detail"] == "tracker_upstream_error"


def test_flow_is_closed_observed_v2_only(db_session, seed_park_with_tracker):
    from robopark_api.services.operations import flow_history

    park_id = seed_park_with_tracker.id
    for hour, version, arrived, scanned in [
        (4, 1, 99, NOW),
        (6, 2, 0, NOW),
        (8, 2, 3, NOW),
        (10, 2, 4, None),
        (12, 2, 8, NOW),
    ]:
        db_session.add(
            ParkBlockerHistory(
                park_id=park_id,
                bucket_start=NOW.replace(hour=hour, minute=0),
                definition_version=version,
                arrived_count=arrived,
                departed_count=0,
                scanned_at=scanned,
            )
        )
    db_session.commit()
    result = flow_history(db_session, park_id=park_id, days=1, now=NOW)
    assert result.definition_version == 2
    assert result.window_start == datetime(2026, 9, 2, 12, tzinfo=UTC)
    assert result.window_end == datetime(2026, 9, 3, 12, tzinfo=UTC)
    assert result.expected_buckets == 12
    assert result.observed_buckets == 2
    assert result.legacy_buckets == 1
    assert result.complete is False
    assert [point.arrived_count for point in result.points] == [0, 3]


def test_flow_without_history_has_no_manufactured_zeros(db_session, seed_park_with_tracker):
    from robopark_api.services.operations import flow_history

    result = flow_history(db_session, park_id=seed_park_with_tracker.id, days=7, now=NOW)
    assert result.points == []
    assert result.observed_buckets == 0
    assert result.expected_buckets == 84
    assert result.complete is False


def test_operator_stats_use_exact_login_and_only_current_park_accounts(
    client, db_session, seed_park_with_tracker, source
):
    park = seed_park_with_tracker
    account(db_session, park, "admin")
    account(db_session, park, username="matched", tracker_login="operator.one")
    account(db_session, park, username="missing")
    account(db_session, park, username="case", tracker_login="Operator.One")
    account(db_session, park, username="foreign", linked=False)
    pending = account(db_session, park, username="pending")
    pending.access_status = "pending"
    inactive = account(db_session, park, username="inactive")
    inactive.is_active = False
    db_session.commit()
    login_as(client, "subject", "secret")
    data = client.get(f"/operations/overview?park_id={park.id}").json()
    rows = {row["username"]: row for row in data["operators"]}
    assert set(rows) == {"matched", "missing", "case"}
    assert rows["matched"]["open_count"] == 5
    assert rows["missing"]["open_count"] is None
    assert rows["missing"]["oldest_hours"] is None
    assert rows["case"]["open_count"] == 0


def test_sla_is_fixed_at_five_hours_even_if_an_old_setting_exists(
    client, db_session, seed_park_with_tracker, source
):
    account(db_session, seed_park_with_tracker, "admin")
    login_as(client, "subject", "secret")
    url = f"/operations/sla-policy?park_id={seed_park_with_tracker.id}"
    platform_settings.set_setting(
        db_session, f"operations.sla.park.{seed_park_with_tracker.id}", "24"
    )
    assert client.get(url).status_code == 404
    assert client.put(url, json={"target_hours": 24}).status_code == 404
    overview = client.get(f"/operations/overview?park_id={seed_park_with_tracker.id}")
    assert overview.status_code == 200
    assert overview.json()["sla"]["target_hours"] == 5


@pytest.mark.parametrize(
    "path",
    [
        "/tracker/issues/ROBOPARK-1",
        "/tracker/issues/ROBOPARK-1/comments",
        "/tracker/transitions/ROBOPARK-1",
    ],
)
def test_driver_foreign_status_cannot_be_read(
    client, db_session, seed_park_with_tracker, source, monkeypatch, path
):
    account(db_session, seed_park_with_tracker, "driver")
    monkeypatch.setattr(tracker_client, "get_issue", lambda **kwargs: issue(status="queued"))
    login_as(client, "subject", "secret")
    assert client.get(path).status_code == 403


def test_driver_list_and_robot_tickets_filter_foreign_statuses(
    client, db_session, seed_park_with_tracker, source, monkeypatch
):
    account(db_session, seed_park_with_tracker, "driver")
    monkeypatch.setattr(tracker_client, "search_issues", lambda **kwargs: source[0])
    monkeypatch.setattr(tracker_client, "search_robot_tickets", lambda **kwargs: source[0])
    login_as(client, "subject", "secret")
    for path in ["/tracker/issues", "/tracker/robots/robot123/tickets"]:
        response = client.get(path)
        assert response.status_code == 200
        assert [row["key"] for row in response.json()["items"]] == ["ROBOPARK-1", "ROBOPARK-4"]


@pytest.mark.parametrize(
    "bucket,alias",
    [
        ("new", "open"),
        ("moving", "перемещение"),
        ("queued", "в очереди"),
        ("diagnostics", "diagnosis"),
        ("waiting_team", "waitingforanotherteam"),
        ("waiting_parts", "delieverywaiting"),
    ],
)
def test_tracker_canonical_status_filter_compiles_aliases(
    client, db_session, seed_park_with_tracker, source, monkeypatch, bucket, alias
):
    account(db_session, seed_park_with_tracker)
    queries = []

    def search(**kwargs):
        queries.append(kwargs["query"])
        return []

    monkeypatch.setattr(tracker_client, "search_issues", search)
    login_as(client, "subject", "secret")
    assert client.get(f"/tracker/issues?status={bucket}").status_code == 200
    assert " OR " in queries[0]
    assert alias in queries[0].lower()
    if bucket in {"waiting_team", "waiting_parts"}:
        assert f"Status: {bucket}" not in queries[0]


def test_driver_cannot_request_foreign_status_filter(
    client, db_session, seed_park_with_tracker, source, monkeypatch
):
    account(db_session, seed_park_with_tracker, "driver")
    monkeypatch.setattr(tracker_client, "search_issues", lambda **kwargs: [])
    login_as(client, "subject", "secret")
    assert client.get("/tracker/issues?status=queued").status_code == 403


def test_raw_terminal_status_does_not_get_filtered_as_open(
    client, db_session, seed_park_with_tracker, source, monkeypatch
):
    account(db_session, seed_park_with_tracker)
    queries = []

    def search(**kwargs):
        queries.append(kwargs)
        return [] if kwargs.get("filter_open", True) else [issue(status="closed")]

    monkeypatch.setattr(tracker_client, "search_issues", search)
    login_as(client, "subject", "secret")
    response = client.get("/tracker/issues?status=closed")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
    assert "Status: closed" in queries[0]["query"]


def test_untagged_status_filter_retains_requested_aliases(
    client, db_session, seed_park_with_tracker, source, monkeypatch
):
    account(db_session, seed_park_with_tracker)
    queries = []

    def search(**kwargs):
        queries.append(kwargs["query"])
        return []

    monkeypatch.setattr(tracker_client, "search_issues", search)
    login_as(client, "subject", "secret")
    assert client.get("/tracker/issues?status=diagnostics&untagged=true").status_code == 200
    assert "Status: diagnosis" in queries[0]
    assert 'Tags: !"Alpha"' in queries[0]


def test_driver_actions_check_status_even_with_explicit_write_grant(
    client, db_session, seed_park_with_tracker, source, monkeypatch
):
    user = account(db_session, seed_park_with_tracker, "driver")
    rbac.set_user_effective_permissions(db_session, user, ["tracker.read", "tracker.write"])
    db_session.commit()
    monkeypatch.setattr(tracker_client, "get_issue", lambda **kwargs: issue(status="queued"))
    login_as(client, "subject", "secret")
    assert client.post("/tracker/issues/ROBOPARK-1/close").status_code == 403


def test_mechanic_work_detail_is_not_limited_to_overview_statuses(
    client, db_session, seed_park_with_tracker, source, monkeypatch
):
    account(db_session, seed_park_with_tracker, "mechanic")
    monkeypatch.setattr(
        tracker_client,
        "get_issue",
        lambda **kwargs: issue(status="new", login="subject"),
    )
    login_as(client, "subject", "secret")
    assert client.get("/tracker/issues/ROBOPARK-1").status_code == 200


def test_workload_uses_assignee_identity_and_marks_unassigned():
    from robopark_api.services.operations import calculate_workload

    rows = [
        issue(age=20),
        {
            **issue("second", age=8),
            "assignee": {"login": "operator.one", "display": "Changed display"},
        },
        issue("unassigned", login=None),
        {**issue("invalid", login="invalid"), "created": "bad"},
    ]
    loads = {
        row.login: row
        for row in calculate_workload(rows, target_hours=10, now=NOW, timezone="Europe/Moscow")
    }
    assert loads["operator.one"].open_count == 2
    assert loads["operator.one"].overdue_count == 0
    assert loads["operator.one"].oldest_hours == 20
    assert loads[None].display == "Без ответственного"
    assert loads["invalid"].oldest_hours is None


def test_overview_rejects_cached_foreign_park_and_queue_rows(
    client, db_session, seed_park_with_tracker, source
):
    account(db_session, seed_park_with_tracker, "admin")
    source[0][:] = [
        issue(),
        {**issue("wrong-tag"), "tags": ["Other"]},
        {**issue("wrong-queue"), "queue": "OTHER"},
    ]
    login_as(client, "subject", "secret")
    data = client.get(f"/operations/overview?park_id={seed_park_with_tracker.id}").json()
    assert data["tasks_total"] == 1
    assert data["counts"]["all"] == 1


def test_driver_overview_relocation_hint_cannot_expand_status_scope(
    client, db_session, seed_park_with_tracker, source
):
    account(db_session, seed_park_with_tracker, "driver")
    source[0][:] = [{**issue(status="queued"), "in_relocation": "1"}]
    login_as(client, "subject", "secret")
    data = client.get(f"/operations/overview?park_id={seed_park_with_tracker.id}").json()
    assert data["tasks_total"] == 0
    assert data["counts"]["all"] == 0


@pytest.mark.parametrize("kind,code", [("token", 503), ("disabled", 409), ("missing", 404)])
def test_overview_unconfigured_source_is_explicit(
    client, db_session, seed_park_with_tracker, source, monkeypatch, kind, code
):
    park = seed_park_with_tracker
    account(db_session, park)
    if kind == "token":
        monkeypatch.setattr(platform_settings, "get_tracker_token", lambda db: None)
    elif kind == "disabled":
        park.feature_blockers = False
        db_session.commit()
    login_as(client, "subject", "secret")
    park_id = 99999 if kind == "missing" else park.id
    assert client.get(f"/operations/overview?park_id={park_id}").status_code == code
    assert source[1] == []


def test_first_tracker_token_unblocks_park_overview(client, seed_royal, test_settings, monkeypatch):
    monkeypatch.setattr(platform_settings, "get_settings", lambda: test_settings)
    received_tokens = []

    def fake_blockers(**kwargs):
        received_tokens.append(kwargs["token"])
        return [issue(status="queued")]

    monkeypatch.setattr(tracker_client, "fetch_park_blockers", fake_blockers)
    login_as(client, "royal", "secret")
    created = client.post(
        "/parks", json={"name": "Alpha", "tag": "Alpha", "tracker_queue": "ROBOPARK"}
    )
    assert created.status_code == 201
    assert created.json()["feature_blockers"] is True
    overview_url = f"/operations/overview?park_id={created.json()['id']}"
    missing = client.get(overview_url)
    assert missing.status_code == 503
    assert missing.json()["detail"] == "tracker_token_not_configured"
    assert received_tokens == []

    one_time_test_token = secrets.token_urlsafe(24)
    saved = client.put("/admin/settings/tracker-token", json={"token": one_time_test_token})
    assert saved.status_code == 200
    assert saved.json()["tracker_token_masked"]
    assert one_time_test_token not in str(saved.json())

    ready = client.get(overview_url)
    assert ready.status_code == 200
    assert ready.json()["tasks"][0]["key"] == "ROBOPARK-1"
    assert received_tokens == [one_time_test_token]


def test_flow_complete_only_when_all_closed_buckets_observed(db_session, seed_park_with_tracker):
    from robopark_api.services.operations import flow_history

    for offset in range(12):
        db_session.add(
            ParkBlockerHistory(
                park_id=seed_park_with_tracker.id,
                bucket_start=NOW.replace(minute=0) - timedelta(hours=2 * (offset + 1)),
                arrived_count=0,
                departed_count=0,
                scanned_at=NOW,
                definition_version=2,
            )
        )
    db_session.commit()
    result = flow_history(db_session, park_id=seed_park_with_tracker.id, days=1, now=NOW)
    assert result.complete is True
    assert result.observed_buckets == 12
    assert len(result.points) == 12


@pytest.mark.parametrize("role", ["mechanic", "operator", "admin", "royal"])
def test_overview_preserves_park_tasks_when_tracker_changes_tag_case(
    client, db_session, seed_park_with_tracker, source, role
):
    account(db_session, seed_park_with_tracker, role)
    for item in source[0]:
        item["tags"] = ["ALPHA"]
    login_as(client, "subject", "secret")
    response = client.get(f"/operations/overview?park_id={seed_park_with_tracker.id}&status=all")
    assert response.status_code == 200
    assert response.json()["tasks_total"] == 5


def test_overview_hydrates_and_persists_queue_anchor_without_opening_task(
    client, db_session, seed_park_with_tracker, source, monkeypatch
):
    from concurrent.futures import Future

    from robopark_api.models import TrackerIssueHistoryState

    account(db_session, seed_park_with_tracker, "mechanic")
    queued = "2026-09-30T06:00:00Z"
    source[0][:] = [{**issue("ROBOPARK-SLA", "queued"), "status_history": []}]
    starts = []

    def schedule(**kwargs):
        starts.append(kwargs["key"])
        future = Future()
        future.set_result(
            [
                {
                    "updatedAt": queued,
                    "fields": [{"field": {"id": "status"}, "to": {"key": "queued"}}],
                }
            ]
        )
        return future, True

    monkeypatch.setattr(tracker_client, "schedule_issue_status_history", schedule)
    login_as(client, "subject", "secret")
    first = client.get(f"/operations/overview?park_id={seed_park_with_tracker.id}&status=all")
    assert first.status_code == 200
    assert first.json()["task_timing"][0]["queue_started_at"] == queued
    state = db_session.get(TrackerIssueHistoryState, "ROBOPARK-SLA")
    assert state.anchor_timezone == "Europe/Moscow"
    second = client.get(f"/operations/overview?park_id={seed_park_with_tracker.id}&status=all")
    assert second.json()["task_timing"][0]["queue_started_at"] == queued
    assert starts == ["ROBOPARK-SLA"]
