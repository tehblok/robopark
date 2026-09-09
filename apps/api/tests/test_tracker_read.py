from datetime import UTC, datetime, timedelta

import pytest

from conftest import login_as, role_id_for
from robopark_api.models import AccessStatus, Park, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services import platform_settings


def _seed_operator(db_session, park):
    operator = User(
        username="op2",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(operator)
    db_session.flush()
    db_session.add(UserPark(user_id=operator.id, park_id=park.id))
    db_session.commit()
    return operator


def _scoped_issue(key: str, created: str) -> dict:
    return {
        "key": key,
        "summary": f"blocker [{key[-1]}]",
        "status": "Open",
        "status_key": "open",
        "queue": "ROBOPARK",
        "created": created,
        "hours_created": "1",
        "tags": ["Alpha"],
        "robot": key[-1],
    }


def test_tracker_read_list_issues(client, db_session, seed_park_with_tracker, monkeypatch):
    _seed_operator(db_session, seed_park_with_tracker)
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")

    from robopark_api.services import tracker_client

    monkeypatch.setattr(
        tracker_client,
        "search_issues",
        lambda **_kwargs: [
            {
                "key": "ROBOPARK-1",
                "summary": "blocker [447]",
                "status": "Open",
                "status_key": "open",
                "queue": "ROBOPARK",
                "created": "2026-01-01T00:00:00Z",
                "hours_created": "2.0",
                "robot": "447",
                "resolution": "",
            }
        ],
    )

    assert (
        client.post("/auth/login", json={"username": "op2", "password": "secret"}).status_code
        == 204
    )
    response = client.get("/tracker/issues")
    assert response.status_code == 200
    assert response.json()["items"][0]["key"] == "ROBOPARK-1"


def test_tracker_list_honors_oldest_and_newest_sort(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    _seed_operator(db_session, seed_park_with_tracker)
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    from robopark_api.services import tracker_client

    monkeypatch.setattr(
        tracker_client,
        "search_issues",
        lambda **_kwargs: [
            _scoped_issue("ROBOPARK-2", "2026-01-02T00:00:00Z"),
            _scoped_issue("ROBOPARK-1", "2026-01-01T00:00:00Z"),
        ],
    )
    login_as(client, "op2", "secret")

    oldest = client.get("/tracker/issues?sort=oldest").json()["items"]
    newest = client.get("/tracker/issues?sort=newest").json()["items"]

    assert [item["key"] for item in oldest] == ["ROBOPARK-1", "ROBOPARK-2"]
    assert [item["key"] for item in newest] == ["ROBOPARK-2", "ROBOPARK-1"]


def test_tracker_list_uses_issue_key_as_a_deterministic_sort_tiebreaker(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    _seed_operator(db_session, seed_park_with_tracker)
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    from robopark_api.services import tracker_client

    monkeypatch.setattr(
        tracker_client,
        "search_issues",
        lambda **_kwargs: [
            _scoped_issue("ROBOPARK-2", "2026-01-01T00:00:00Z"),
            _scoped_issue("ROBOPARK-1", "2026-01-01T00:00:00Z"),
        ],
    )
    login_as(client, "op2", "secret")

    oldest = client.get("/tracker/issues?sort=oldest").json()["items"]
    newest = client.get("/tracker/issues?sort=newest").json()["items"]

    assert [item["key"] for item in oldest] == ["ROBOPARK-1", "ROBOPARK-2"]
    assert [item["key"] for item in newest] == ["ROBOPARK-2", "ROBOPARK-1"]


def test_tracker_list_exact_robot_filters_before_deduplication_and_pagination(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    _seed_operator(db_session, seed_park_with_tracker)
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    from robopark_api.services import tracker_client

    captured: list[dict] = []
    open_items = [
        {**_scoped_issue("ROBOPARK-42", "2026-01-01T00:00:00Z"), "robot": "447"},
        {
            **_scoped_issue("ROBOPARK-1447", "2026-01-01T01:00:00Z"),
            "robot": "1447",
            "summary": "Робот 447 в тексте",
        },
        {
            **_scoped_issue("ROBOPARK-1", "2026-01-01T02:00:00Z"),
            "robot": "447",
            "summary": "Без номера в summary",
        },
        {**_scoped_issue("ROBOPARK-2", "2026-01-01T03:00:00Z"), "robot": "YASADR00000000447"},
        {
            **_scoped_issue("ROBOPARK-2", "2026-01-02T00:00:00Z"),
            "robot": "447",
            "summary": "Дубликат ключа",
        },
        {**_scoped_issue("ROBOPARK-3", "2026-01-01T04:00:00Z"), "robot": "447"},
    ]
    closed_items = [
        {**_scoped_issue("ROBOPARK-4", "2026-01-02T00:00:00Z"), "robot": "447", "status": "Closed"}
    ]

    def search(**kwargs):
        captured.append(kwargs)
        return open_items if kwargs["filter_open"] else closed_items

    monkeypatch.setattr(tracker_client, "search_issues", search)
    login_as(client, "op2", "secret")

    response = client.get(
        "/tracker/issues?queue=ROBOPARK&park=Alpha&robot_exact=447&exclude_key=ROBOPARK-42&limit=2"
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["key"] for item in body["items"]] == ["ROBOPARK-1", "ROBOPARK-2"]
    assert body["total"] == 3
    assert body["has_more"] is True
    assert "ROBOPARK-42" not in [item["key"] for item in body["items"]]
    assert "ROBOPARK-1447" not in [item["key"] for item in body["items"]]
    assert captured[0]["filter_open"] is True
    assert "Queue: ROBOPARK" in captured[0]["query"]
    assert "Tags: Alpha" in captured[0]["query"]
    assert 'Summary: "447"' in captured[0]["query"]
    assert 'Summary: "a447"' in captured[0]["query"]
    assert 'Summary: "0447"' in captured[0]["query"]
    assert 'Summary: "YASADR00000000447"' in captured[0]["query"]
    assert f'Summary: "{"447".zfill(64)}"' in captured[0]["query"]

    closed = client.get(
        "/tracker/issues?queue=ROBOPARK&park=Alpha&status=closed&robot_exact=447&exclude_key=ROBOPARK-42"
    )
    assert closed.status_code == 200
    assert [item["key"] for item in closed.json()["items"]] == ["ROBOPARK-4"]
    assert captured[1]["filter_open"] is False
    assert "Status: closed" in captured[1]["query"]
    assert 'Summary: "a447"' in captured[1]["query"]


@pytest.mark.parametrize(
    "query",
    ["447", "A447", "a447", "[A447]", "[a447]", "yasadr00000000447", "YASADR447", "00000000000447"],
)
def test_exact_robot_aliases_match_only_authorized_complete_identifiers(
    client, db_session, seed_park_with_tracker, monkeypatch, query
):
    _seed_operator(db_session, seed_park_with_tracker)
    db_session.add(Park(name="Foreign", tag="Foreign", tracker_queue="ROBOPARK"))
    db_session.commit()
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    from robopark_api.services import tracker_client

    aliases = ["447", "0447", "A447", "a447", "[A447]", "[a447]", "[447]", "yasadr00000000447"]
    unrelated = ["A1447", "other447", "A447B", "[A447] extra", "447/448", "[A447", "A447]"]
    items = [
        {**_scoped_issue(f"ROBOPARK-{index}", "2026-01-01T00:00:00Z"), "robot": raw}
        for index, raw in enumerate([*aliases, *unrelated])
    ]
    items.append(
        {
            **_scoped_issue("ROBOPARK-99", "2026-01-01T00:00:00Z"),
            "robot": "A447",
            "tags": ["Foreign"],
        }
    )
    monkeypatch.setattr(tracker_client, "search_issues", lambda **_kwargs: items)
    login_as(client, "op2", "secret")
    response = client.get(
        "/tracker/issues", params={"queue": "ROBOPARK", "park": "Alpha", "robot_exact": query}
    )
    assert response.status_code == 200
    assert [item["key"] for item in response.json()["items"]] == [
        f"ROBOPARK-{index}" for index in range(8)
    ]


def test_mechanic_issue_capabilities_respect_write_policy_but_keep_attachment(
    client, db_session, seed_mechanic, monkeypatch
):
    from robopark_api.services import tracker_client

    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    platform_settings.set_bool_setting(
        db_session,
        platform_settings.TRACKER_MECHANIC_WRITE_KEY,
        False,
    )
    monkeypatch.setattr(
        tracker_client,
        "get_issue",
        lambda **_kwargs: {
            **_scoped_issue("ROBOPARK-1", "2026-01-01T00:00:00Z"),
            "assignee": {"login": "mech1", "display": "Mechanic"},
        },
    )
    login_as(client, "mech1", "secret")

    response = client.get("/tracker/issues/ROBOPARK-1")

    assert response.status_code == 200
    assert response.json()["capabilities"] == {
        "comment": False,
        "assign": False,
        "unassign": False,
        "transition": False,
        "close": False,
        "attach": True,
    }


@pytest.mark.parametrize("queue", ["ROBOPARK", "SDCFLEETOPS"])
@pytest.mark.parametrize("closed", [False, True])
def test_related_repairs_preserve_scope_and_page_only_exact_robot_repairs_of_any_priority(
    client, db_session, seed_park_with_tracker, monkeypatch, queue, closed
):
    seed_park_with_tracker.tracker_queue = queue
    _seed_operator(db_session, seed_park_with_tracker)
    db_session.add(Park(name="Foreign", tag="Foreign", tracker_queue=queue))
    db_session.commit()
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    from robopark_api.services import tracker_client

    def repair(number, **changes):
        return {
            **_scoped_issue(f"{queue}-{number}", "2026-01-01T00:00:00Z"),
            "queue": queue,
            "robot": "A447",
            "status": "Закрыт" if closed else "Открыт",
            "status_key": "closed" if closed else "open",
            "type": "Ремонт",
            "type_key": "repair",
            "priority": "Обычный",
            "resolved": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            **changes,
        }

    captured = []

    def search(**kwargs):
        captured.append(kwargs)
        return [
            repair(0),  # Selected parent task is excluded.
            repair(1, robot="1447"),
            repair(2, type="Ремонт", type_key="service"),
            repair(3, tags=["Foreign"]),
            repair(4, queue="FORBIDDEN"),
            repair(5, priority="Низкий"),
            repair(6, priority="Критический", robot="YASADR00000000447"),
            repair(6),  # Duplicates cannot alter pagination.
            repair(7, priority="Блокер"),
        ]

    monkeypatch.setattr(tracker_client, "search_issues", search)
    login_as(client, "op2", "secret")
    params = {
        "related_repairs": "true",
        "robot_exact": "[A447]",
        "queue": queue,
        "park": "Alpha",
        "exclude_key": f"{queue}-0",
        "limit": 1,
        "offset": 1,
        **({"status": "closed"} if closed else {"open_only": "true"}),
    }

    response = client.get("/tracker/issues", params=params)
    assert response.status_code == 200
    body = response.json()
    assert [item["key"] for item in body["items"]] == [f"{queue}-6"]
    assert body["items"][0]["priority"] == "Критический"
    assert body["total"] == 3
    assert body["limit"] == 1
    assert body["offset"] == 1
    assert body["has_more"] is True

    newest = client.get("/tracker/issues", params={**params, "sort": "newest", "offset": 0})
    assert [item["key"] for item in newest.json()["items"]] == [f"{queue}-7"]
    last = client.get("/tracker/issues", params={**params, "offset": 2})
    assert [item["key"] for item in last.json()["items"]] == [f"{queue}-7"]
    assert last.json()["has_more"] is False
    assert len(captured) == 1  # Identical scoped searches reuse the cache across pages.
    query = captured[0]["query"]
    assert "Priority:" not in query
    assert "Type: repair" in query
    assert "Type: service" not in query
    assert f"Queue: {queue}" in query
    assert "Tags: Alpha" in query
    assert 'Summary: "447"' in query
    assert captured[0]["filter_open"] is (not closed)
    if closed:
        assert "Status: closed" in query


@pytest.mark.parametrize("robot", [None, "", "other447", "447/448"])
def test_related_repairs_require_valid_exact_robot_before_search(
    client, db_session, seed_park_with_tracker, monkeypatch, robot
):
    _seed_operator(db_session, seed_park_with_tracker)
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    from robopark_api.services import tracker_client

    calls = []
    monkeypatch.setattr(
        tracker_client, "search_issues", lambda **kwargs: calls.append(kwargs) or []
    )
    login_as(client, "op2", "secret")
    params = {"related_repairs": "true", "status": "closed"}
    if robot is not None:
        params["robot_exact"] = robot
    response = client.get("/tracker/issues", params=params)
    assert response.status_code == 400
    assert response.json()["detail"] == "tracker_robot_exact_required"
    assert calls == []


@pytest.mark.parametrize("params", [{"queue": "FORBIDDEN"}, {"park": "Foreign"}])
def test_related_repairs_reject_forbidden_selected_scope(
    client, db_session, seed_park_with_tracker, monkeypatch, params
):
    _seed_operator(db_session, seed_park_with_tracker)
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    from robopark_api.services import tracker_client

    calls = []
    monkeypatch.setattr(
        tracker_client, "search_issues", lambda **kwargs: calls.append(kwargs) or []
    )
    login_as(client, "op2", "secret")
    response = client.get(
        "/tracker/issues", params={"related_repairs": "true", "robot_exact": "447", **params}
    )
    assert response.status_code == 403
    assert calls == []


@pytest.mark.parametrize(
    ("issue_scope", "expected_status"),
    [({}, 200), ({"queue": "FORBIDDEN"}, 403), ({"tags": ["Foreign"]}, 403)],
)
def test_non_blocker_repair_detail_and_comment_keep_existing_scope(
    client, db_session, seed_park_with_tracker, monkeypatch, issue_scope, expected_status
):
    _seed_operator(db_session, seed_park_with_tracker)
    db_session.add(Park(name="Foreign", tag="Foreign", tracker_queue="ROBOPARK"))
    db_session.commit()
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    from robopark_api.services import tracker_client

    issue = {
        **_scoped_issue("ROBOPARK-1", "2026-01-01T00:00:00Z"),
        "robot": "447",
        "type": "Ремонт",
        "type_key": "repair",
        "priority": "Низкий",
        **issue_scope,
    }
    monkeypatch.setattr(tracker_client, "get_issue", lambda **_kwargs: issue)
    comments = []

    def add_comment(**kwargs):
        comments.append(kwargs)
        return {"id": "1", "text": kwargs["text"]}

    monkeypatch.setattr(tracker_client, "add_comment", add_comment)
    login_as(client, "op2", "secret")
    detail = client.get("/tracker/issues/ROBOPARK-1")
    assert detail.status_code == expected_status
    response = client.post("/tracker/issues/ROBOPARK-1/comment", json={"text": "Проверила ремонт"})
    assert response.status_code == expected_status
    if expected_status == 200:
        assert detail.json()["priority"] == "Низкий"
        assert all(detail.json()["capabilities"].values())
        assert response.json()["action"] == "comment"
        assert len(comments) == 1
        assert comments[0]["key"] == "ROBOPARK-1"
        assert "Проверила ремонт" in comments[0]["text"]
    else:
        assert comments == []


def test_tracker_read_assignee_filter(client, db_session, seed_park_with_tracker, monkeypatch):
    _seed_operator(db_session, seed_park_with_tracker)
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")

    from robopark_api.services import tracker_client

    captured: dict[str, str] = {}

    def fake_search(**kwargs):
        captured["query"] = kwargs["query"]
        return []

    monkeypatch.setattr(tracker_client, "search_issues", fake_search)

    assert (
        client.post("/auth/login", json={"username": "op2", "password": "secret"}).status_code
        == 204
    )

    response = client.get("/tracker/issues?assignee=ivan.petrov")
    assert response.status_code == 200
    assert "Assignee: ivan.petrov" in captured["query"]

    response = client.get("/tracker/issues?assignee=empty")
    assert response.status_code == 200
    assert "Assignee: empty()" in captured["query"]


def test_mechanic_comments_filtered_to_platform_and_staff(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    from robopark_api.models import AccessStatus, User, UserPark
    from robopark_api.security import hash_password

    operator = User(
        username="op_staff",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(operator)
    db_session.flush()
    db_session.add(UserPark(user_id=operator.id, park_id=seed_park_with_tracker.id))
    db_session.commit()

    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")

    from robopark_api.services import tracker_client

    monkeypatch.setattr(
        tracker_client,
        "get_issue",
        lambda **_kwargs: {
            "key": "ROBOPARK-1",
            "summary": "blocker [447]",
            "status": "Open",
            "status_key": "open",
            "queue": "ROBOPARK",
            "resolution": "",
            "tags": ["Alpha"],
            "assignee": {"login": "mech1", "display": "Mechanic"},
        },
    )
    monkeypatch.setattr(
        tracker_client,
        "list_comments",
        lambda **_kwargs: [
            {
                "id": "1",
                "text": "ok\nAlpha / mech1 / operator1",
                "author": "bot",
                "author_login": "bot",
            },
            {
                "id": "2",
                "text": "plain comment",
                "author": "Operator",
                "author_login": "op_staff",
            },
            {"id": "3", "text": "random chatter", "author": "human", "author_login": "stranger"},
        ],
    )

    login_as(client, "mech1", "secret")
    response = client.get("/tracker/issues/ROBOPARK-1/comments")
    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload] == ["1", "2"]


def test_mechanic_must_claim_issue_before_reading_detail(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    seed_mechanic.tracker_login = "mech.login"
    db_session.commit()
    issue = {
        "key": "ROBOPARK-1",
        "summary": "blocker [447]",
        "status": "Open",
        "status_key": "open",
        "queue": "ROBOPARK",
        "tags": ["Alpha"],
    }
    from robopark_api.services import tracker_client

    monkeypatch.setattr(tracker_client, "get_issue", lambda **_kwargs: dict(issue))
    login_as(client, "mech1", "secret")

    denied = client.get("/tracker/issues/ROBOPARK-1")
    assert denied.status_code == 409
    assert denied.json()["detail"] == "tracker_issue_claim_required"

    issue["assignee"] = {"login": "mech.login", "display": "Mechanic"}
    from robopark_api.services import tracker_cache

    tracker_cache.invalidate_issue("ROBOPARK-1")
    assert client.get("/tracker/issues/ROBOPARK-1").status_code == 200


def test_tracker_robot_search_royal(
    client, db_session, seed_royal, seed_park_with_tracker, monkeypatch
):
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")

    from robopark_api.services import tracker_cache

    monkeypatch.setattr(
        tracker_cache,
        "search_robot_tickets",
        lambda **kwargs: [
            {
                "key": "ROBOPARK-9",
                "summary": "blocker [447]",
                "status": "Open",
                "status_key": "open",
                "queue": kwargs["queue"],
                "created": "2026-01-01T00:00:00Z",
                "hours_created": "1.0",
                "robot": "447",
            }
        ],
    )

    login_as(client, "royal", "secret")
    response = client.get("/tracker/robots/447/tickets")
    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "447"
    assert body["items"][0]["key"] == "ROBOPARK-9"


def test_selected_status_can_be_restricted_to_open_blockers(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    _seed_operator(db_session, seed_park_with_tracker)
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    from robopark_api.services import tracker_client

    captured = []

    def search(**kwargs):
        captured.append(kwargs)
        items = [
            _scoped_issue("ROBOPARK-1", "2026-01-01T00:00:00Z"),
            {**_scoped_issue("ROBOPARK-2", "2026-01-02T00:00:00Z"), "resolution": "fixed"},
        ]
        return [
            item
            for item in items
            if not kwargs["filter_open"] or tracker_client.is_issue_open_item(item)
        ]

    monkeypatch.setattr(tracker_client, "search_issues", search)
    login_as(client, "op2", "secret")
    response = client.get("/tracker/issues?status=open&open_only=true")
    assert response.status_code == 200
    assert [item["key"] for item in response.json()["items"]] == ["ROBOPARK-1"]
    assert response.json()["total"] == 1
    assert captured[0]["filter_open"] is True
    assert tracker_client.open_issues_clause() in captured[0]["query"]
    assert "Priority: blocker" in captured[0]["query"]
    assert "Status: open" in captured[0]["query"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("waiting_parts", "Status: delieveryWaiting"),
        ("waiting_team", "Status: waitingForAnotherTeam"),
        ("queued", 'Status: "В очереди"'),
        ("diagnostics", 'Status: "Диагностика"'),
    ],
)
def test_status_query_preserves_case_sensitive_tracker_workflow_names(
    client, db_session, seed_park_with_tracker, monkeypatch, status, expected
):
    _seed_operator(db_session, seed_park_with_tracker)
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    from robopark_api.services import tracker_client

    queries = []

    def search(**kwargs):
        queries.append(kwargs["query"])
        return []

    monkeypatch.setattr(tracker_client, "search_issues", search)
    login_as(client, "op2", "secret")
    assert client.get(f"/tracker/issues?status={status}&open_only=true").status_code == 200
    assert expected in queries[0]


def test_closed_related_repairs_use_resolution_date_and_filter_before_pagination(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    from robopark_api.routers import tracker_read
    from robopark_api.services import tracker_client

    class Clock(datetime):
        elapsed_seconds = 0

        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 6, 12, 30, tzinfo=UTC) + timedelta(seconds=cls.elapsed_seconds)

    monkeypatch.setattr(tracker_read, "datetime", Clock)
    _seed_operator(db_session, seed_park_with_tracker)
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    dates = [
        "2026-08-23T12:29:59Z",  # One second too old, despite a recent update.
        "2026-08-23T15:30:00+0300",  # Inclusive boundary with a timezone offset.
        "2026-09-05T12:00:00Z",  # An old task closed recently is included.
        "",
        "invalid",
        None,
        "2026-09-07T12:00:00Z",  # Future timestamps cannot enter the period.
    ]
    rows = [
        {
            **_scoped_issue(f"ROBOPARK-{i}", "2020-01-01T00:00:00Z"),
            "robot": "447",
            "type_key": "repair",
            "status_key": "closed",
            "resolved": value,
            "updated": "2026-09-06T12:00:00Z",
        }
        for i, value in enumerate(dates)
    ]
    queries = []

    def search(**kwargs):
        queries.append(kwargs["query"])
        return rows

    monkeypatch.setattr(tracker_client, "search_issues", search)
    login_as(client, "op2", "secret")
    params = {
        "related_repairs": "true",
        "robot_exact": "447",
        "status": "closed",
        "queue": "ROBOPARK",
        "park": "Alpha",
        "limit": 1,
    }
    first = client.get("/tracker/issues", params=params).json()
    assert [item["key"] for item in first["items"]] == ["ROBOPARK-1"]
    assert first["total"] == 2
    assert first["has_more"] is True
    last = client.get("/tracker/issues", params={**params, "offset": 1}).json()
    assert [item["key"] for item in last["items"]] == ["ROBOPARK-2"]
    assert last["has_more"] is False
    assert len(queries) == 1
    assert 'Resolved: >= "2026-08-23 12:00:00"' in queries[0]

    Clock.elapsed_seconds = 1
    aged = client.get("/tracker/issues", params=params).json()
    assert [item["key"] for item in aged["items"]] == ["ROBOPARK-2"]
    assert aged["total"] == 1
    assert aged["has_more"] is False
    assert len(queries) == 1  # Reapply the cutoff even when upstream results are cached.

    # Other Tracker history callers retain their existing unrestricted behavior.
    ordinary = client.get("/tracker/issues", params={**params, "related_repairs": "false"}).json()
    assert ordinary["total"] == len(rows)
    assert "Resolved:" not in queries[-1]
