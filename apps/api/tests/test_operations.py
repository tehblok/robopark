from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from conftest import login_as, role_id_for
from robopark_api.models import AuditLog, Park, ParkBlockerHistory, PlatformSetting, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services import platform_settings, rbac, tracker_client, tracker_filters

NOW = datetime(2026, 9, 3, 12, 30, tzinfo=UTC)


def issue(key="ROBOPARK-1", status="new", age=12, login="operator.one"):
    return {
        "key": key,
        "summary": key,
        "status": status,
        "status_key": status,
        "created": (NOW - timedelta(hours=age)).isoformat(),
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


def test_sla_calendar_hours_boundaries_and_unknown_dates():
    from robopark_api.services.operations import calculate_sla

    rows = [
        issue("oldest", status="queued", age=20),
        issue("overdue", status="queued", age=10.01),
        issue("boundary", status="queued", age=10),
        issue("warning", status="queued", age=8),
        issue("safe", status="queued", age=7.99),
        issue("future", status="queued", age=-1),
        {**issue("invalid", status="queued"), "created": "nonsense"},
        {**issue("missing", status="queued"), "created": None},
    ]
    result = calculate_sla(rows, target_hours=10, now=NOW)
    assert result.evaluated_count == 5
    assert result.unknown_count == 3
    assert result.at_risk_count == 1
    assert result.overdue_count == 0
    assert result.overdue == []


def test_no_sla_target_is_unknown_not_zero():
    from robopark_api.services.operations import calculate_sla

    result = calculate_sla([issue()], target_hours=None, now=NOW)
    assert result.target_hours is None
    assert result.evaluated_count == 0
    assert result.unknown_count == 1
    assert result.overdue_count is None
    assert result.at_risk_count is None
    assert result.overdue == []


def test_sla_counts_only_queued_working_hours_in_moscow():
    from robopark_api.services.operations import calculate_sla

    now = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)  # 15:00 Moscow
    queued = issue("queued", status="queued")
    queued["created"] = "2026-09-02T17:00:00+00:00"  # 20:00 Moscow: 1h + 6h
    diagnostics = {
        **queued,
        "key": "diagnostics",
        "status": "diagnostics",
        "status_key": "diagnostics",
    }

    result = calculate_sla([queued, diagnostics], target_hours=4, now=now)
    assert result.evaluated_count == 1
    assert result.unknown_count == 1
    assert result.overdue_count == 1
    assert result.overdue[0].age_hours == 7


def test_sla_bounded_list_retains_exact_total():
    from robopark_api.services.operations import calculate_sla

    result = calculate_sla(
        [issue(str(i), status="queued", age=300 - i) for i in range(210)], target_hours=10, now=NOW
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


def test_sla_policy_roundtrip_clear_and_audit(client, db_session, seed_park_with_tracker):
    account(db_session, seed_park_with_tracker, "admin")
    login_as(client, "subject", "secret")
    url = f"/operations/sla-policy?park_id={seed_park_with_tracker.id}"
    assert client.get(url).json() == {"park_id": seed_park_with_tracker.id, "target_hours": 4}
    response = client.put(url, json={"target_hours": 24})
    assert response.status_code == 200
    assert client.get(url).json()["target_hours"] == 24
    row = db_session.get(PlatformSetting, f"operations.sla.park.{seed_park_with_tracker.id}")
    assert row.value == "24"
    assert client.put(url, json={"target_hours": None}).status_code == 200
    assert client.get(url).json()["target_hours"] == 4
    edits = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "operations.sla_policy.updated")
    ).all()
    assert len(edits) == 2
    assert edits[0].park_id == seed_park_with_tracker.id
    assert edits[0].actor_username == "subject"


@pytest.mark.parametrize("target", [0, -1, 8761, 1.5, True, "24"])
def test_sla_policy_rejects_non_integer_or_out_of_range(
    client, db_session, seed_park_with_tracker, target
):
    account(db_session, seed_park_with_tracker, "admin")
    login_as(client, "subject", "secret")
    assert (
        client.put(
            f"/operations/sla-policy?park_id={seed_park_with_tracker.id}",
            json={"target_hours": target},
        ).status_code
        == 422
    )


def test_sla_policy_write_requires_permission_and_authorized_park(
    client, db_session, seed_park_with_tracker
):
    user = account(db_session, seed_park_with_tracker)
    login_as(client, "subject", "secret")
    url = f"/operations/sla-policy?park_id={seed_park_with_tracker.id}"
    assert client.get(url).status_code == 200
    assert client.put(url, json={"target_hours": 24}).status_code == 403
    rbac.set_user_effective_permissions(db_session, user, ["parks.manage"])
    db_session.add(Park(name="Other", tag="Other", is_active=True))
    db_session.commit()
    other = db_session.scalar(select(Park).where(Park.tag == "Other"))
    assert (
        client.put(
            f"/operations/sla-policy?park_id={other.id}", json={"target_hours": 24}
        ).status_code
        == 403
    )


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
    loads = {row.login: row for row in calculate_workload(rows, target_hours=10, now=NOW)}
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
