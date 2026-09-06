import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, current_thread
from time import monotonic

import httpx
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from conftest import login_as
from robopark_api import schemas
from robopark_api.db import (
    RequestSession,
    bind_request_session,
    release_request_session,
    reset_request_session,
)
from robopark_api.models import Base, DiagnosticRule
from robopark_api.routers.emergency import emergency_snapshot_for_user
from robopark_api.services import emergency_cache, emergency_client, emergency_scope
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services.emergency_snapshot import parse_emergency_snapshot
from robopark_api.services.emergency_vin import short_robot_number


def _snapshot_rule(db):
    rule = DiagnosticRule(
        source_path="errors",
        match_kind="exact",
        pattern="WHEEL_BLOCKED",
        example="WHEEL_BLOCKED",
        title="Wheel blocked",
        description="Inspect wheel",
        severity="critical",
        part="wheel",
        preferred_view="front",
        x=0.25,
        y=0.75,
        indicator="point",
        is_enabled=True,
        sort_order=7,
    )
    db.add(rule)
    db.commit()
    return rule


def test_short_robot_number_strips_leading_zeros():
    assert short_robot_number("YASADR00000000447") == "447"


def test_parse_snapshot_happy_path():
    snap = parse_emergency_snapshot(
        {
            "isOnline": True,
            "velocity": 1.25,
            "autoMode": True,
            "icp": {"ok": True, "status": "ICP"},
            "lte": "LTE",
            "disk": {"usedPercents": 53},
            "batteriesStatus": {
                "chargePercents": 64,
                "battery1": {"chargePercents": 97},
                "battery2": {"chargePercents": 96},
            },
            "position": {"lat": 55.7, "lon": 37.6, "yaw": 90},
            "wheelsBroken": [0, 5],
            "errors": ["[+0.5s] /RoverChassis/Systems/Control: Offline"],
        },
        vin="YASADR00000000447",
    )
    assert snap["short_number"] == "447"
    assert snap["online"] is True
    assert snap["speed"] == 1.25
    assert snap["charge_percent"] == 64
    assert snap["battery1_percent"] == 97
    assert snap["battery2_percent"] == 96
    assert snap["disk_percent"] == 53
    assert snap["mode"] == "AUTO"
    assert snap["icp_label"] == "ICP"
    assert snap["icp_ok"] is True
    assert snap["lte_label"] == "LTE"
    assert snap["connection"] == "lte"
    assert snap["error_banner"] == "ERROR: [+0.5s] /RoverChassis/Systems/Control: Offline"
    assert snap["lat"] == 55.7
    assert snap["lon"] == 37.6
    assert snap["heading_deg"] == 90
    assert snap["wheels_fault"] == ["fl", "rr"]


def test_parse_snapshot_nested_velocity_and_missing_fields():
    snap = parse_emergency_snapshot({"velocity": {"value": 2}}, vin="YASADR00000000001")
    assert snap["speed"] == 2.0
    assert snap["online"] is None
    assert snap["lat"] is None
    assert snap["mode"] is None
    assert snap["wheels_fault"] == []


def test_parse_snapshot_opaque_wheels_uses_body():
    snap = parse_emergency_snapshot({"wheelsBroken": "unknown"}, vin="YASADR00000000001")
    assert snap["wheels_fault"] == ["body"]


def test_parse_snapshot_compact_wheel_codes():
    snap = parse_emergency_snapshot({"wheelsBroken": ["fl", "rr"]}, vin="YASADR00000000001")
    assert snap["wheels_fault"] == ["fl", "rr"]


def test_parse_snapshot_online_coercion():
    online = parse_emergency_snapshot({"isOnline": "true"}, vin="YASADR00000000001")
    offline = parse_emergency_snapshot({"isOnline": 0}, vin="YASADR00000000001")
    unknown = parse_emergency_snapshot({"isOnline": "maybe"}, vin="YASADR00000000001")
    assert online["online"] is True
    assert offline["online"] is False
    assert unknown["online"] is None


def test_parse_snapshot_manual_mode_and_offline_link():
    snap = parse_emergency_snapshot(
        {"autoMode": False, "lte": False, "icp": "fail"},
        vin="YASADR00000000001",
    )
    assert snap["mode"] == "MANUAL"
    assert snap["lte_ok"] is False
    assert snap["icp_ok"] is False
    assert snap["connection"] is None


def test_parse_snapshot_live_field_shapes():
    snap = parse_emergency_snapshot(
        {
            "isOnline": True,
            "velocity": 0.0,
            "disk": {},
            "diskUsage": 17,
            "batteriesStatus": {
                "battery1": {"chargePercentage": 73, "isConnected": True},
                "battery2": {"chargePercentage": 74, "isConnected": True},
            },
            "lte": {"lte24": 7300, "lte50": 8900},
            "wheelsBroken": {
                "lf": False,
                "lm": False,
                "lr": False,
                "rf": False,
                "rm": False,
                "rr": False,
            },
            "position": {"yaw": 80},
        },
        vin="YASADR00000001975",
    )
    assert snap["battery1_percent"] == 73
    assert snap["battery2_percent"] == 74
    assert snap["disk_percent"] == 17
    assert snap["connection"] == "lte"
    assert snap["wheels_fault"] == []
    assert snap["heading_deg"] == 80


def test_parse_snapshot_disconnected_battery_wheel_dict_and_wire():
    snap = parse_emergency_snapshot(
        {
            "isOnline": True,
            "batteriesStatus": {
                "battery1": {"chargePercentage": 50, "isConnected": True},
                "battery2": {"chargePercentage": 0, "isConnected": False},
            },
            "wheelsBroken": {"lf": True, "lm": False, "rr": True},
            "lte": {"lte24": 0, "lte50": 0},
        },
        vin="YASADR00000000001",
    )
    assert snap["battery1_percent"] == 50
    assert snap["battery2_percent"] is None
    assert snap["wheels_fault"] == ["fl", "rr"]
    assert snap["connection"] == "wire"


def test_parse_snapshot_explicit_wire_beats_lte():
    snap = parse_emergency_snapshot(
        {"isOnline": True, "isWired": True, "lte": {"lte24": 8000}},
        vin="YASADR00000000001",
    )
    assert snap["connection"] == "wire"


def test_snapshot_schema_defaults_events_for_legacy_clients():
    snapshot = schemas.EmergencySnapshotOut(
        vin="YASADR00000000447",
        short_number="447",
        observed_at=datetime.now(UTC),
        error_banner="ERROR: old payload",
    )

    assert snapshot.diagnostic_events == []
    assert snapshot.error_banner == "ERROR: old payload"


def test_parser_normalizes_events_and_keeps_the_existing_error_banner(db_session):
    rule = _snapshot_rule(db_session)
    payload = {"errors": ["WHEEL_BLOCKED", "UNKNOWN"]}

    snapshot = parse_emergency_snapshot(payload, vin="YASADR00000000447", db=db_session)

    assert snapshot["error_banner"] == "ERROR: WHEEL_BLOCKED"
    assert [(item.rule_id, item.raw_value) for item in snapshot["diagnostic_events"]] == [
        (rule.id, "WHEEL_BLOCKED"),
        (None, "UNKNOWN"),
    ]
    assert payload == {"errors": ["WHEEL_BLOCKED", "UNKNOWN"]}


def test_snapshot_route_matches_fresh_rules_against_cached_payload(
    client,
    db_session,
    seed_royal,
    monkeypatch,
):
    rule = _snapshot_rule(db_session)
    settings_svc.set_setting(db_session, settings_svc.EMERGENCY_COOKIE_KEY, "test-cookie")
    calls = []

    def fetch(**kwargs):
        calls.append(kwargs)
        return {"errors": ["WHEEL_BLOCKED", "UNKNOWN"]}

    monkeypatch.setattr(emergency_client, "fetch_robot_payload", fetch)
    login_as(client, "royal", "secret")

    first = client.get("/emergency/447/snapshot")
    rule.title = "Updated explanation"
    db_session.commit()
    second = client.get("/emergency/447/snapshot")
    rule.is_enabled = False
    db_session.commit()
    disabled = client.get("/emergency/447/snapshot")

    assert first.status_code == second.status_code == disabled.status_code == 200
    assert first.json()["diagnostic_events"][0]["title"] == "Wheel blocked"
    assert second.json()["diagnostic_events"][0]["title"] == "Updated explanation"
    assert first.json()["diagnostic_events"][0]["id"] == second.json()["diagnostic_events"][0]["id"]
    assert all(item["rule_id"] is None for item in disabled.json()["diagnostic_events"])
    assert first.json()["error_banner"] == second.json()["error_banner"] == "ERROR: WHEEL_BLOCKED"
    assert calls == [{"cookie": "test-cookie", "vin": "YASADR00000000447"}]


@pytest.mark.parametrize(
    ("outcome", "expected_status", "detail"),
    [
        ("timeout", 502, "emergency_upstream_error"),
        ("auth", 403, "emergency_cookie_invalid"),
        ("malformed", 502, "emergency_upstream_error"),
    ],
)
def test_snapshot_upstream_failures_do_not_query_rules_and_next_success_recovers(
    client,
    db_session,
    db_engine,
    seed_royal,
    monkeypatch,
    outcome,
    expected_status,
    detail,
):
    _snapshot_rule(db_session)
    settings_svc.set_setting(db_session, settings_svc.EMERGENCY_COOKIE_KEY, "test-cookie")
    diagnostic_queries = []

    def track_query(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "diagnostic_rules" in statement:
            diagnostic_queries.append(statement)

    event.listen(db_engine, "before_cursor_execute", track_query)
    current = outcome

    def respond(request):
        if current == "timeout":
            raise httpx.ReadTimeout("test upstream timed out", request=request)
        if current == "auth":
            return httpx.Response(401, json={})
        if current == "malformed":
            return httpx.Response(200, json=["invalid payload shape"])
        return httpx.Response(200, json={"errors": ["WHEEL_BLOCKED"]})

    original_client = httpx.Client
    monkeypatch.setattr(
        emergency_client.httpx,
        "Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    login_as(client, "royal", "secret")
    try:
        failed = client.get("/emergency/447/snapshot")
        assert failed.status_code == expected_status
        assert failed.json() == {"detail": detail}
        assert diagnostic_queries == []

        current = "success"
        recovered = client.get("/emergency/447/snapshot")
        assert recovered.status_code == 200
        assert recovered.json()["diagnostic_events"][0]["title"] == "Wheel blocked"
        assert len(diagnostic_queries) == 1
    finally:
        event.remove(db_engine, "before_cursor_execute", track_query)


def test_matching_reopens_request_session_after_upstream_releases_connection(
    db_engine,
    db_session,
    seed_royal,
    monkeypatch,
):
    rule = _snapshot_rule(db_session)
    settings_svc.set_setting(db_session, settings_svc.EMERGENCY_COOKIE_KEY, "test-cookie")
    wrapper = RequestSession(sessionmaker(bind=db_engine, future=True))
    token = bind_request_session(wrapper)
    user = wrapper.get(type(seed_royal), seed_royal.id)
    monkeypatch.setattr(emergency_scope, "vin_allowed_for_user", lambda *_args: True)

    def fetch(**_kwargs):
        assert wrapper._inner is None, "Upstream must run without a held DB connection"
        return {"errors": ["WHEEL_BLOCKED"]}

    monkeypatch.setattr(emergency_client, "fetch_robot_payload", fetch)
    try:
        snapshot = emergency_snapshot_for_user("447", user, wrapper)
        assert snapshot.diagnostic_events[0].rule_id == rule.id
        assert wrapper.scalar(select(DiagnosticRule.id)) == rule.id
    finally:
        wrapper.close()
        reset_request_session(token)


def test_concurrent_snapshots_share_upstream_flight_but_match_in_their_own_sessions(
    db_engine,
    db_session,
    seed_royal,
    monkeypatch,
):
    rule = _snapshot_rule(db_session)
    settings_svc.set_setting(db_session, settings_svc.EMERGENCY_COOKIE_KEY, "test-cookie")
    user_id, rule_id = seed_royal.id, rule.id
    factory = sessionmaker(bind=db_engine, future=True)
    started, release, follower_waiting = Event(), Event(), Event()
    calls = []
    leader_thread = []

    def fetch(**kwargs):
        calls.append(kwargs)
        leader_thread.append(current_thread().ident)
        started.set()
        assert release.wait(3)
        return {"errors": ["WHEEL_BLOCKED", "UNKNOWN"]}

    def release_connection():
        release_request_session()
        if started.is_set() and current_thread().ident != leader_thread[0]:
            follower_waiting.set()

    def snapshot():
        wrapper = RequestSession(factory)
        token = bind_request_session(wrapper)
        try:
            user = wrapper.get(type(seed_royal), user_id)
            return emergency_snapshot_for_user("447", user, wrapper)
        finally:
            wrapper.close()
            reset_request_session(token)

    monkeypatch.setattr(emergency_client, "fetch_robot_payload", fetch)
    monkeypatch.setattr(emergency_cache, "release_request_session", release_connection)
    monkeypatch.setattr(emergency_scope, "vin_allowed_for_user", lambda *_args: True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        leader = pool.submit(snapshot)
        assert started.wait(3)
        follower = pool.submit(snapshot)
        try:
            assert follower_waiting.wait(3)
        finally:
            release.set()
        first, second = leader.result(timeout=3), follower.result(timeout=3)

    assert first.diagnostic_events == second.diagnostic_events
    assert [(item.rule_id, item.raw_value) for item in first.diagnostic_events] == [
        (rule_id, "WHEEL_BLOCKED"),
        (None, "UNKNOWN"),
    ]
    assert calls == [{"cookie": "test-cookie", "vin": "YASADR00000000447"}]


def _regex_budget_probe(database_path, pattern, length):
    """Run real requests in a disposable process so a broken engine cannot hang pytest."""
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        rule = _snapshot_rule(db)
        rule.match_kind = "regex"
        rule.pattern = pattern
        db.commit()
        rule_id = rule.id
        settings_svc.set_setting(db, settings_svc.EMERGENCY_COOKIE_KEY, "test-cookie")
    factory = sessionmaker(bind=engine)
    raw = "a" * length + "!"
    emergency_client.fetch_robot_payload = lambda **_kwargs: {"errors": [raw]}
    emergency_scope.vin_allowed_for_user = lambda *_args: True

    def request():
        wrapper = RequestSession(factory)
        token = bind_request_session(wrapper)
        try:
            return emergency_snapshot_for_user("447", None, wrapper)
        finally:
            wrapper.close()
            reset_request_session(token)

    started = monotonic()
    first = request()
    elapsed = monotonic() - started
    assert elapsed < 0.5, f"Diagnostic matching exceeded request budget: {elapsed}"
    assert [(item.rule_id, item.raw_value) for item in first.diagnostic_events] == [(None, raw)]
    assert first.diagnostic_events[0].part is None
    emergency_cache.clear_cache_for_tests()
    raw = "aaaa"
    second = request()
    assert [(item.rule_id, item.raw_value) for item in second.diagnostic_events] == [
        (rule_id, "aaaa")
    ]
    assert engine.pool.checkedout() == 0
    print(json.dumps({"elapsed": elapsed, "recovered": True}))


@pytest.mark.parametrize(
    ("pattern", "length"), [(r"^(a+)+$", 32), (r"^(a+)+$", 30000), (r"^(a|aa)+$", 1000)]
)
def test_regex_timeout_returns_unknown_and_next_snapshot_recovers(tmp_path, pattern, length):
    api_root = Path(__file__).resolve().parent.parent
    probe = (
        "import sys; sys.path[:0] = sys.argv[1:3]; "
        "from test_emergency_snapshot import _regex_budget_probe; "
        "_regex_budget_probe(sys.argv[3], sys.argv[4], int(sys.argv[5]))"
    )
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                probe,
                str(api_root / "src"),
                str(api_root / "tests"),
                str(tmp_path / "probe.db"),
                pattern,
                str(length),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("Regex blocked the snapshot worker beyond the 3-second process watchdog")
    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout)
    assert outcome["recovered"] is True
    assert outcome["elapsed"] < 0.5
