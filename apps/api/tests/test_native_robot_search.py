import pytest

from robopark_api.models import Park, UserPark
from robopark_api.services import (
    native_telegram,
    tracker_cache,
    tracker_client,
    tracker_robot_search,
)


def issue(key="ROBOPARK-1", tag="Alpha", robot="447", **extra):
    return tracker_client.issue_to_dict(
        {
            "queue": {"key": key.rsplit("-", 1)[0]},
            "key": key,
            "summary": f"[a{robot}] repair",
            "tags": [tag],
            "type": {"key": "repair"},
            "status": {"key": "open"},
            **extra,
        }
    )


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
    foreign = add_park(db_session, "Foreign")
    foreign.is_active = False
    db_session.commit()
    calls = []
    rows = [issue(), issue("ROBOPARK-2", "Park10"), issue("ROBOPARK-3", "Foreign")]

    def search(**kwargs):
        calls.append(kwargs)
        return rows

    monkeypatch.setattr(tracker_client, "search_issues", search)
    for _ in range(2):
        result = native_telegram.robot_issues(db_session, seed_royal, "A0447", "open")
        assert [row["key"] for row in result["issues"]] == ["ROBOPARK-1", "ROBOPARK-2"]
    assert len(calls) == 1
    assert "Tags:" not in calls[0]["query"]
    shared = tracker_robot_search.search(
        db_session, seed_royal, token="token", robot="447", queue="ROBOPARK", park="Alpha"
    )
    assert [row["key"] for row in shared] == ["ROBOPARK-1"]
    assert len(calls) == 1


def test_queue_and_tag_must_both_belong_to_same_allowed_park(
    db_session, seed_royal, seed_park_with_tracker, monkeypatch
):
    add_park(db_session, "Beta", "OTHER")
    monkeypatch.setattr(
        tracker_client,
        "search_issues",
        lambda **_kwargs: [issue(), issue("OTHER-2", "Beta"), issue("OTHER-3", "Alpha")],
    )
    result = native_telegram.robot_issues(db_session, seed_royal, "447", "open")
    assert {row["key"] for row in result["issues"]} == {"ROBOPARK-1", "OTHER-2"}


def test_queue_configuration_is_case_insensitive(
    db_session, seed_royal, seed_park_with_tracker, monkeypatch
):
    seed_park_with_tracker.tracker_queue = " robopark "
    db_session.commit()
    monkeypatch.setattr(tracker_client, "search_issues", lambda **_kwargs: [issue()])
    result = native_telegram.robot_issues(db_session, seed_royal, "447", "open")
    assert [row["key"] for row in result["issues"]] == ["ROBOPARK-1"]


def test_robot_includes_service_without_park_tag(db_session, seed_mechanic, monkeypatch):
    rows = [
        issue(),
        issue(
            "ROBOPARK-2", "SC", type={"key": "service"}, priority={"key": "normal"}, rover=["a447"]
        ),
    ]
    monkeypatch.setattr(tracker_cache, "peek_robot_source", lambda queues: rows)
    result = native_telegram.robot_issues(db_session, seed_mechanic, "447", "open")
    assert {row["key"] for row in result["issues"]} == {"ROBOPARK-1", "ROBOPARK-2"}


def test_complete_registry_cache_avoids_network_and_rechecks_membership(
    db_session, seed_royal, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    add_park(db_session, "Beta")
    monkeypatch.setattr(
        tracker_cache,
        "peek_robot_source",
        lambda queues: [issue(), issue("ROBOPARK-2", "Beta")],
    )

    def unexpected(**_kwargs):
        pytest.fail("Fresh complete task cache must avoid Tracker")

    monkeypatch.setattr(tracker_client, "search_issues", unexpected)
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
    monkeypatch.setattr(tracker_cache, "peek_robot_source", lambda queues: rows)
    monkeypatch.setattr(tracker_client, "search_issues", lambda **_kwargs: [])
    result = native_telegram.robot_issues(db_session, seed_mechanic, "YASADR00000000447", view)
    assert [row["key"] for row in result["issues"]] == expected


def test_complete_source_is_not_capped_before_exact_filter(
    db_session, seed_royal, seed_park_with_tracker, monkeypatch
):
    rows = [issue(f"ROBOPARK-{n}", robot="1447") for n in range(500)] + [issue("ROBOPARK-999")]
    monkeypatch.setattr(tracker_client, "search_issues", lambda **kwargs: rows)
    result = native_telegram.robot_issues(db_session, seed_royal, "447", "open")
    assert [row["key"] for row in result["issues"]] == ["ROBOPARK-999"]
    assert result["truncated"] is False


@pytest.mark.parametrize("raw", [["a447"], "a447", ["YASADR00000000447"], ["а0447"]])
def test_rover_list_without_summary_robot(db_session, seed_mechanic, monkeypatch, raw):
    rows = [
        issue(),
        issue("ROBOPARK-2", "SC", summary="Service campaign", rover=raw, type={"key": "service"}),
    ]
    monkeypatch.setattr(tracker_cache, "peek_robot_source", lambda queues: rows)
    result = native_telegram.robot_issues(db_session, seed_mechanic, "447", "open")
    assert {row["key"] for row in result["issues"]} == {"ROBOPARK-1", "ROBOPARK-2"}


def test_untagged_requires_live_same_queue_anchor(db_session, seed_mechanic, monkeypatch):
    rows = [issue(resolution={"key": "fixed"}), issue("ROBOPARK-2", "SC", type={"key": "service"})]
    monkeypatch.setattr(tracker_cache, "peek_robot_source", lambda queues: rows)
    assert native_telegram.robot_issues(db_session, seed_mechanic, "447", "open")["issues"] == []


@pytest.mark.parametrize("rover", ["YASADR00000000447", "а447", "A447", "a0447", "а0447"])
def test_cold_search_queries_rover_identity(db_session, seed_mechanic, monkeypatch, rover):
    def upstream(**kwargs):
        found = [issue()]
        if f'rover: "{rover}"' in kwargs["query"]:
            found.append(
                issue("ROBOPARK-2", "SC", summary="Service", rover=[rover], type={"key": "service"})
            )
        return found

    monkeypatch.setattr(tracker_client, "search_issues", upstream)
    result = native_telegram.robot_issues(db_session, seed_mechanic, "447", "open")
    assert {row["key"] for row in result["issues"]} == {"ROBOPARK-1", "ROBOPARK-2"}
