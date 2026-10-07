import pytest

from robopark_api.models import Park, UserPark
from robopark_api.services import native_telegram, tracker_cache


def issue(key="ROBOPARK-1", tag="Alpha", robot="447", **extra):
    return {
        "key": key,
        "summary": f"[a{robot}] repair",
        "tags": [tag],
        "type": {"key": "repair"},
        "status": {"key": "open"},
        **extra,
    }


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setattr(native_telegram.bot_tracker_gateway, "tracker_token", lambda _db: "token")


def add_park(db, tag, queue="ROBOPARK"):
    park = Park(name=tag, tag=tag, tracker_queue=queue, is_active=True)
    db.add(park)
    db.commit()
    return park


def test_one_search_for_twelve_parks_and_repeat_uses_cache(
    db_session, seed_royal, seed_park_with_tracker, monkeypatch
):
    for n in range(11):
        add_park(db_session, f"Park{n}")
    calls = []
    rows = [issue(), issue("ROBOPARK-2", "Park10"), issue("ROBOPARK-3", "Foreign")]

    def search(**kwargs):
        calls.append(kwargs)
        return rows

    monkeypatch.setattr(native_telegram.bot_tracker_gateway, "search", search)
    for _ in range(2):
        result = native_telegram.robot_issues(db_session, seed_royal, "A0447", "open")
        assert [row["key"] for row in result["issues"]] == ["ROBOPARK-1", "ROBOPARK-2"]
    assert len(calls) == 1
    assert "Tags: Alpha OR Tags: Park0" in calls[0]["query"]


def test_queue_and_tag_must_both_belong_to_same_allowed_park(
    db_session, seed_royal, seed_park_with_tracker, monkeypatch
):
    add_park(db_session, "Beta", "OTHER")
    monkeypatch.setattr(
        native_telegram.bot_tracker_gateway,
        "search",
        lambda **_kwargs: [issue(), issue("OTHER-2", "Beta"), issue("OTHER-3", "Alpha")],
    )
    result = native_telegram.robot_issues(db_session, seed_royal, "447", "open")
    assert {row["key"] for row in result["issues"]} == {"ROBOPARK-1", "OTHER-2"}


def test_queue_configuration_is_case_insensitive(
    db_session, seed_royal, seed_park_with_tracker, monkeypatch
):
    seed_park_with_tracker.tracker_queue = " robopark "
    db_session.commit()
    monkeypatch.setattr(native_telegram.bot_tracker_gateway, "search", lambda **_kwargs: [issue()])
    result = native_telegram.robot_issues(db_session, seed_royal, "447", "open")
    assert [row["key"] for row in result["issues"]] == ["ROBOPARK-1"]


def test_complete_registry_cache_avoids_network_and_rechecks_membership(
    db_session, seed_royal, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    add_park(db_session, "Beta")
    monkeypatch.setattr(
        tracker_cache,
        "peek_native_robot_source",
        lambda queues: [issue(), issue("ROBOPARK-2", "Beta")],
    )

    def unexpected(**_kwargs):
        pytest.fail("Fresh complete task cache must avoid Tracker")

    monkeypatch.setattr(native_telegram.bot_tracker_gateway, "search", unexpected)
    assert len(native_telegram.robot_issues(db_session, seed_royal, "447", "open")["issues"]) == 2
    result = native_telegram.robot_issues(db_session, seed_mechanic, "447", "open")
    assert [row["key"] for row in result["issues"]] == ["ROBOPARK-1"]
    db_session.query(UserPark).filter(UserPark.user_id == seed_mechanic.id).delete()
    db_session.commit()
    assert native_telegram.robot_issues(db_session, seed_mechanic, "447", "open")["issues"] == []


@pytest.mark.parametrize("view,expected", [("open", ["ROBOPARK-1"]), ("history", ["ROBOPARK-2"])])
def test_cached_data_keeps_exact_robot_type_and_resolution_filters(
    db_session, seed_mechanic, monkeypatch, view, expected
):
    rows = [
        issue(),
        issue("ROBOPARK-2", resolution={"key": "fixed"}),
        issue("ROBOPARK-3", robot="1447"),
        issue("ROBOPARK-4", type={"key": "bug"}),
        issue("ROBOPARK-5", status={"key": "closed"}),
        issue("ROBOPARK-6", resolution={"key": "duplicate"}),
    ]
    monkeypatch.setattr(tracker_cache, "peek_native_robot_source", lambda queues: rows)
    monkeypatch.setattr(native_telegram.bot_tracker_gateway, "search", lambda **_kwargs: [])
    result = native_telegram.robot_issues(db_session, seed_mechanic, "YASADR00000000447", view)
    assert [row["key"] for row in result["issues"]] == expected


def test_full_combined_page_falls_back_to_park_queries_to_preserve_coverage(
    db_session, seed_royal, seed_park_with_tracker, monkeypatch
):
    add_park(db_session, "Beta")
    calls = []

    def search(**kwargs):
        query = kwargs["query"]
        calls.append(query)
        if "Tags: Alpha OR Tags: Beta" in query:
            return [issue(f"ROBOPARK-{n}", robot="1447") for n in range(500)]
        return [issue()] if "Tags: Alpha" in query else [issue("ROBOPARK-2", "Beta")]

    monkeypatch.setattr(native_telegram.bot_tracker_gateway, "search", search)
    result = native_telegram.robot_issues(db_session, seed_royal, "447", "open")
    assert [row["key"] for row in result["issues"]] == ["ROBOPARK-1", "ROBOPARK-2"]
    assert len(calls) == 3
