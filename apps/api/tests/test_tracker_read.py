import threading
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from conftest import login_as, role_id_for
from robopark_api.collaboration_models import TrackerClaim
from robopark_api.models import AccessStatus, Park, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services import platform_settings
from robopark_api.task_workflow_models import HiddenTask


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


def test_tracker_list_preserves_upstream_order_for_equal_queue_timestamps(
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

    assert [item["key"] for item in oldest] == ["ROBOPARK-2", "ROBOPARK-1"]
    assert [item["key"] for item in newest] == ["ROBOPARK-2", "ROBOPARK-1"]


def test_tracker_owned_list_uses_local_claims_before_pagination_and_scope(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    other_park = Park(
        name="Beta", tag="Beta", is_active=True, tracker_queue="ROBOPARK", feature_blockers=True
    )
    other_owner = User(
        username="mech2",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "mechanic"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add_all([other_park, other_owner])
    db_session.flush()
    claims = [
        ("ROBOPARK-OWN-OLD", seed_park_with_tracker.id, seed_mechanic.id),
        ("ROBOPARK-OTHER-OWNER", seed_park_with_tracker.id, other_owner.id),
        ("ROBOPARK-OTHER-PARK", other_park.id, seed_mechanic.id),
        ("ROBOPARK-HIDDEN", seed_park_with_tracker.id, seed_mechanic.id),
        ("ROBOPARK-OWN-NEW", seed_park_with_tracker.id, seed_mechanic.id),
    ]
    db_session.add_all(
        [
            TrackerClaim(
                issue_key=key,
                park_id=park_id,
                owner_user_id=owner_id,
                updated_by_user_id=owner_id,
                updated_at=1,
            )
            for key, park_id, owner_id in claims
        ]
    )
    db_session.add(
        HiddenTask(
            id="hidden-owned",
            issue_key="ROBOPARK-HIDDEN",
            park_id=seed_park_with_tracker.id,
            reason="duplicate",
            actor_user_id=other_owner.id,
            created_at=1,
            updated_at=1,
        )
    )
    db_session.commit()

    issues = [
        {**_scoped_issue("ROBOPARK-OWN-NEW", "2026-01-05T00:00:00Z"), "assignee": None},
        # Upstream tags are stale; the local claim park remains authoritative.
        _scoped_issue("ROBOPARK-OTHER-PARK", "2026-01-03T00:00:00Z"),
        {**_scoped_issue("ROBOPARK-OTHER-OWNER", "2026-01-02T00:00:00Z")},
        {**_scoped_issue("ROBOPARK-HIDDEN", "2026-01-04T00:00:00Z")},
        {
            **_scoped_issue("ROBOPARK-OWN-OLD", "2026-01-01T00:00:00Z"),
            "assignee": {"login": "stale.tracker", "display": "stale.tracker"},
        },
    ]
    captured = {}

    from robopark_api.services import tracker_client

    def fake_search(**kwargs):
        captured.update(kwargs)
        return issues

    monkeypatch.setattr(tracker_client, "search_issues", fake_search)
    login_as(client, seed_mechanic.username, "secret")

    response = client.get("/tracker/issues?owned_by_me=true&sort=oldest&limit=1&offset=1")

    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert [item["key"] for item in response.json()["items"]] == ["ROBOPARK-OWN-NEW"]
    assert response.json()["items"][0]["assignee"]["login"] == seed_mechanic.username
    assert "Assignee:" not in captured["query"]
    assert "Tags:" not in captured["query"]
    assert captured["filter_open"] is True


def test_tracker_list_prefers_queue_history_and_exposes_exact_five_hour_sla(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    _seed_operator(db_session, seed_park_with_tracker)
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    from robopark_api.services import tracker_client

    historical = {
        **_scoped_issue("ROBOPARK-1", "2026-01-01T00:00:00Z"),
        "status_history": [
            {
                "updatedAt": "2026-01-03T09:15:00Z",
                "fields": [
                    {"field": {"id": "status"}, "to": {"key": "queued", "display": "В очереди"}}
                ],
            }
        ],
    }
    fallback = _scoped_issue("ROBOPARK-2", "2026-01-02T10:00:00Z")
    missing = {**_scoped_issue("ROBOPARK-3", ""), "hours_created": None}
    monkeypatch.setattr(
        tracker_client,
        "search_issues",
        lambda **_kwargs: [historical, fallback, missing],
    )
    login_as(client, "op2", "secret")

    items = client.get("/tracker/issues?sort=oldest").json()["items"]

    assert [item["key"] for item in items] == ["ROBOPARK-2", "ROBOPARK-1", "ROBOPARK-3"]
    assert items[0]["queued_at"] == "2026-01-02T10:00:00Z"
    assert items[0]["sla_deadline"] == "2026-01-02T15:00:00Z"
    assert items[0]["sla_source"] == "estimated"
    assert items[1]["queued_at"] == "2026-01-03T09:15:00Z"
    assert items[1]["sla_deadline"] == "2026-01-03T14:15:00Z"
    assert items[1]["sla_source"] == "status_history"
    assert items[2]["queued_at"] is None
    assert items[2]["sla_deadline"] is None
    assert items[2]["sla_source"] is None


def test_tracker_work_page_hydrates_only_returned_items_and_orders_exact_queue_times(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    _seed_operator(db_session, seed_park_with_tracker)
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    from robopark_api.services import tracker_client

    first = _scoped_issue("ROBOPARK-9", "2026-01-02T09:00:00Z")
    second = _scoped_issue("ROBOPARK-10", "2026-01-02T10:00:00Z")
    outside_page = _scoped_issue("ROBOPARK-11", "2026-01-02T11:00:00Z")
    monkeypatch.setattr(
        tracker_client,
        "search_issues",
        lambda **_kwargs: [first, second, outside_page],
    )
    calls = []

    def history(*, token, key, issue):
        del issue
        calls.append((token, key))
        timestamp = {
            "ROBOPARK-9": "2026-01-02T12:00:00Z",
            "ROBOPARK-10": "2026-01-02T11:00:00Z",
        }[key]
        return [
            {
                "updatedAt": timestamp,
                "fields": [{"field": {"id": "status"}, "to": {"key": "queued"}}],
            }
        ]

    monkeypatch.setattr(tracker_client, "_load_work_status_history", history)
    login_as(client, "op2", "secret")

    items = client.get("/tracker/issues?sort=oldest&limit=2").json()["items"]

    assert len(calls) == 2
    assert set(calls) == {("token", "ROBOPARK-9"), ("token", "ROBOPARK-10")}
    assert [item["key"] for item in items] == ["ROBOPARK-10", "ROBOPARK-9"]
    assert items[0]["queued_at"] == "2026-01-02T11:00:00Z"
    assert items[0]["sla_deadline"] == "2026-01-02T16:00:00Z"
    assert items[0]["sla_source"] == "status_history"


def test_tracker_work_history_failure_falls_back_to_estimated_creation(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    _seed_operator(db_session, seed_park_with_tracker)
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    from robopark_api.services import tracker_client

    issue = _scoped_issue("ROBOPARK-12", "2026-01-02T10:00:00Z")
    monkeypatch.setattr(tracker_client, "search_issues", lambda **_kwargs: [issue])
    calls = []

    def failed_history(*, token, key, issue):
        del issue
        calls.append((token, key))
        raise tracker_client.TrackerError("history unavailable")

    monkeypatch.setattr(tracker_client, "_load_work_status_history", failed_history)
    login_as(client, "op2", "secret")

    item = client.get("/tracker/issues").json()["items"][0]

    assert calls == [("token", "ROBOPARK-12")]
    assert item["queued_at"] == "2026-01-02T10:00:00Z"
    assert item["sla_deadline"] == "2026-01-02T15:00:00Z"
    assert item["sla_source"] == "estimated"


def test_tracker_work_cold_page_has_one_short_history_budget_and_bounded_calls(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    _seed_operator(db_session, seed_park_with_tracker)
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    from robopark_api.services import tracker_client

    issues = [
        _scoped_issue(f"ROBOPARK-{index}", f"2026-01-{index % 28 + 1:02d}T10:00:00Z")
        for index in range(200)
    ]
    monkeypatch.setattr(tracker_client, "search_issues", lambda **_kwargs: issues)
    release = threading.Event()
    calls: list[str] = []
    calls_lock = threading.Lock()

    def stalled_history(*, token, key, issue):
        del token, issue
        with calls_lock:
            calls.append(key)
        release.wait(5)
        raise tracker_client.TrackerError("history unavailable")

    monkeypatch.setattr(tracker_client, "_load_work_status_history", stalled_history)
    login_as(client, "op2", "secret")

    started = time.monotonic()
    try:
        response = client.get("/tracker/issues?limit=200")
    finally:
        release.set()
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert elapsed < 1.0
    assert len(calls) <= 2
    assert len(response.json()["items"]) == 200
    assert {item["sla_source"] for item in response.json()["items"]} == {"estimated"}


def test_tracker_work_history_does_not_enqueue_the_inner_tracker_executor(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    _seed_operator(db_session, seed_park_with_tracker)
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    from robopark_api.services import tracker_api, tracker_client

    def forbidden_general_slot(*_args, **_kwargs):
        raise AssertionError("Work history must not use the general Tracker slot")

    monkeypatch.setattr(tracker_api, "tracker_slot", forbidden_general_slot)

    inner_release = threading.Event()
    inner_started = threading.Barrier(tracker_api.MAX_INFLIGHT + 1)

    def occupy_inner_worker():
        inner_started.wait(timeout=2)
        inner_release.wait(5)

    inner_futures = [
        tracker_api._executor.submit(occupy_inner_worker)  # noqa: SLF001
        for _ in range(tracker_api.MAX_INFLIGHT)
    ]
    inner_started.wait(timeout=2)

    history_release = threading.Event()
    history_calls: list[str] = []
    history_lock = threading.Lock()

    class StalledChangelog:
        def __init__(self, key: str):
            self.key = key

        def get_all(self):
            with history_lock:
                history_calls.append(self.key)
            history_release.wait(5)
            return []

    generation = 0

    def search(**_kwargs):
        nonlocal generation
        generation += 1
        return [
            {
                **_scoped_issue(f"ROBOPARK-{generation}-{index}", "2026-01-02T10:00:00Z"),
                "_tracker_resource": SimpleNamespace(
                    changelog=StalledChangelog(f"ROBOPARK-{generation}-{index}")
                ),
            }
            for index in range(200)
        ]

    monkeypatch.setattr(tracker_client, "search_issues", search)
    login_as(client, "op2", "secret")

    try:
        first = client.get("/tracker/issues?limit=200")
        second = client.get("/tracker/issues?limit=200")

        assert first.status_code == 200
        assert second.status_code == 200
        assert len(history_calls) == 2
        assert tracker_client._work_history_executor._work_queue.qsize() == 0  # noqa: SLF001
        assert tracker_api._executor._work_queue.qsize() == 0  # noqa: SLF001
        assert {item["sla_source"] for item in first.json()["items"]} == {"estimated"}
        assert {item["sla_source"] for item in second.json()["items"]} == {"estimated"}
    finally:
        history_release.set()
        inner_release.set()
        for future in inner_futures:
            future.result(timeout=2)


def test_tracker_work_history_hydration_reuses_a_fixed_worker_pool(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    _seed_operator(db_session, seed_park_with_tracker)
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    from robopark_api.services import tracker_client

    monkeypatch.setattr(
        tracker_client,
        "search_issues",
        lambda **_kwargs: [_scoped_issue("ROBOPARK-POOL", "2026-01-02T10:00:00Z")],
    )
    release = threading.Event()
    monkeypatch.setattr(
        tracker_client,
        "_load_work_status_history",
        lambda **_kwargs: release.wait(5) or [],
    )
    login_as(client, "op2", "secret")

    try:
        for _ in range(5):
            assert client.get("/tracker/issues").status_code == 200
        workers = [
            thread
            for thread in threading.enumerate()
            if thread.name.startswith("tracker-work-history")
        ]
        assert len(workers) <= 2
    finally:
        release.set()


def test_tracker_work_detail_hydrates_exact_queue_history(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    _seed_operator(db_session, seed_park_with_tracker)
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    from robopark_api.services import tracker_client

    issue = _scoped_issue("ROBOPARK-13", "2026-01-03T10:00:00Z")
    monkeypatch.setattr(tracker_client, "get_issue", lambda **_kwargs: issue)
    calls = []

    def history(*, token, key, issue):
        del issue
        calls.append((token, key))
        return [
            {
                "updatedAt": "2026-01-02T08:30:00Z",
                "fields": [{"field": {"id": "status"}, "to": {"key": "queued"}}],
            }
        ]

    monkeypatch.setattr(tracker_client, "get_issue_status_history", history, raising=False)
    login_as(client, "op2", "secret")

    item = client.get("/tracker/issues/ROBOPARK-13").json()

    assert calls == [("token", "ROBOPARK-13")]
    assert item["queued_at"] == "2026-01-02T08:30:00Z"
    assert item["sla_deadline"] == "2026-01-02T13:30:00Z"
    assert item["sla_source"] == "status_history"


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


def test_mechanic_can_open_unclaimed_issue_and_another_users_claim_to_take_over(
    client, db_session, seed_mechanic, seed_royal, seed_park_with_tracker, monkeypatch
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

    unclaimed = client.get("/tracker/issues/ROBOPARK-1")
    assert unclaimed.status_code == 200
    assert unclaimed.json()["claim"] is None

    from robopark_api.services.tracker_claims import claim_issue

    claim_issue(
        db_session,
        actor=seed_royal,
        owner=seed_royal,
        issue_key="ROBOPARK-1",
        park_id=seed_park_with_tracker.id,
    )
    claimed = client.get("/tracker/issues/ROBOPARK-1")
    assert claimed.status_code == 200
    assert claimed.json()["assignee"]["login"] == seed_royal.username
    assert claimed.json()["claim"] == {"park_id": seed_park_with_tracker.id}


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
