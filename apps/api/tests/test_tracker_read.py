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
    assert "Summary:" not in captured[0]["query"]

    closed = client.get(
        "/tracker/issues?queue=ROBOPARK&park=Alpha&status=closed&robot_exact=447&exclude_key=ROBOPARK-42"
    )
    assert closed.status_code == 200
    assert [item["key"] for item in closed.json()["items"]] == ["ROBOPARK-4"]
    assert captured[1]["filter_open"] is False
    assert "Status: closed" in captured[1]["query"]


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
        lambda **_kwargs: _scoped_issue("ROBOPARK-1", "2026-01-01T00:00:00Z"),
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
