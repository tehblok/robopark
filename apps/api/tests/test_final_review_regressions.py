import hashlib
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from conftest import login_as, role_id_for
from robopark_api.models import DiagnosticUnknown, Permission, Role, User
from robopark_api.security import hash_password
from robopark_api.services.diagnostic_rules import (
    match_diagnostic_events,
    match_diagnostic_events_for_rules,
)
from robopark_api.services.emergency_readings import render_readings
from test_admin_diagnostic_rules import RULE, insert_rule
from test_admin_emergency_readings import READING
from test_emergency_readings import add_reading, add_section


@pytest.fixture
def admin(client, db_session):
    db_session.add(
        User(
            username="fix-admin",
            password_hash=hash_password("secret"),
            role_id=role_id_for(db_session, "admin"),
            access_status="approved",
            is_active=True,
        )
    )
    add_section(db_session, "status")
    db_session.commit()
    assert login_as(client, "fix-admin", "secret").status_code == 204
    return client


@pytest.mark.parametrize("field", ["path", "enabled_path"])
@pytest.mark.parametrize("path", ["hud.cookie", "sensors.authToken", "sdcOptions.value"])
def test_sensitive_paths_rejected_by_create_and_patch(admin, field, path):
    created = admin.post("/admin/emergency-readings", json=READING).json()
    assert admin.post("/admin/emergency-readings", json={**READING, field: path}).status_code == 422
    assert (
        admin.patch(f"/admin/emergency-readings/{created['id']}", json={field: path}).status_code
        == 422
    )


def test_persisted_sensitive_reading_is_not_projected(db_session):
    add_section(db_session, "status")
    add_reading(db_session, path="hud.cookie", display_kind="text")
    add_reading(db_session, path="safe", enabled_path="authToken")
    db_session.commit()
    assert (
        render_readings(
            db_session, {"hud": {"cookie": "secret"}, "safe": 1, "authToken": True}, "mechanic"
        )
        == []
    )


@pytest.mark.parametrize("check", ["keys", "cache"])
def test_discovery_has_no_store_and_rejects_dotted_keys(admin, monkeypatch, check):
    from robopark_api.services import emergency_cache, platform_settings

    monkeypatch.setattr(
        platform_settings, "get_emergency_cookie_probe", lambda db: ("cookie", "id")
    )
    monkeypatch.setattr(
        emergency_cache, "get_robot_payload", lambda **kw: {"a.b": 99, "a": {"b": 1}}
    )
    response = admin.get("/admin/emergency-readings/discovered?vin=1")
    if check == "keys":
        assert response.json() == [{"path": "a.b", "value_type": "number", "example": "1"}]
    else:
        assert response.headers.get("cache-control") == "no-store"


def test_preopened_lock_retries_after_pruning_unlinks_inode(tmp_path, monkeypatch):
    import fcntl
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from robopark_api.services.live_merge import LiveMergeStore, LiveMergeTimeout
    from robopark_api.services.ops import maintenance

    monkeypatch.setattr(maintenance, "require_application_writes", lambda: None)
    store = LiveMergeStore(tmp_path)
    path = store.lock_path("race", "key")
    path.parent.mkdir(parents=True)
    path.touch()
    opened, proceed = threading.Event(), threading.Event()
    real_flock = fcntl.flock

    def gated_flock(fd, operation):
        if threading.current_thread().name.startswith("waiter") and not opened.is_set():
            opened.set()
            assert proceed.wait(3)
        return real_flock(fd, operation)

    monkeypatch.setattr(fcntl, "flock", gated_flock)

    def waiter():
        try:
            with store._exclusive("race", "key", timeout=0.1):
                return "overlapped"
        except LiveMergeTimeout:
            return "timed-out"

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="waiter") as pool:
        future = pool.submit(waiter)
        assert opened.wait(3)
        with path.open("a+") as prune:
            real_flock(prune.fileno(), fcntl.LOCK_EX)
            path.unlink()
        with path.open("a+") as current:
            real_flock(current.fileno(), fcntl.LOCK_EX)
            proceed.set()
            assert future.result(timeout=3) == "timed-out"


@pytest.mark.parametrize("role", ["admin", "royal", "custom-admin", "mechanic"])
def test_configuration_write_permission_matrix(client, db_session, role):
    if role == "custom-admin":
        custom = Role(slug=role, name=role, is_system=False)
        custom.permissions = list(db_session.scalars(select(Permission)))
        db_session.add(custom)
        db_session.flush()
        role_id = custom.id
    else:
        role_id = role_id_for(db_session, role)
    db_session.add(
        User(
            username="matrix",
            password_hash=hash_password("secret"),
            role_id=role_id,
            access_status="approved",
            is_active=True,
        )
    )
    add_section(db_session, "status")
    db_session.commit()
    login_as(client, "matrix", "secret")
    writes = [
        ("post", "/admin/emergency-readings", READING),
        ("patch", "/admin/emergency-readings/999", {"label": "x"}),
        ("delete", "/admin/emergency-readings/999", None),
        ("put", "/admin/emergency-readings/reorder", {"ids": []}),
        ("post", "/admin/diagnostic-rules", RULE),
        ("patch", "/admin/diagnostic-rules/999", {"title": "x"}),
        ("post", "/admin/diagnostic-rules/999/disable", None),
        ("put", "/admin/diagnostic-rules/reorder", {"ids": []}),
        ("post", "/admin/diagnostic-unknowns/999/classify", {"rule": RULE}),
        ("post", "/admin/diagnostic-unknowns/999/ignore", None),
        ("post", "/admin/diagnostic-unknowns/999/reopen", None),
        (
            "post",
            "/admin/emergency/sections",
            {"id": "created", "title": "Created", "roles": [], "fields": []},
        ),
        ("patch", "/admin/emergency/sections/status", {"title": "Changed"}),
        ("delete", "/admin/emergency/sections/missing", None),
        ("put", "/admin/emergency/sections/reorder", {"ids": ["status"]}),
        ("post", "/admin/emergency/sections/status/fields", {"path": "speed", "label": "Speed"}),
        ("patch", "/admin/emergency/fields/999", {"label": "Changed"}),
        ("delete", "/admin/emergency/fields/999", None),
    ]
    statuses = [
        client.request(method, path, **({"json": body} if body is not None else {})).status_code
        for method, path, body in writes
    ]
    if role == "admin":
        assert all(code != 403 for code in statuses), statuses
        assert statuses[0] == statuses[4] == statuses[11] == 201
    else:
        assert statuses == [403] * len(writes)
    if role in {"admin", "royal", "custom-admin"}:
        assert client.get("/admin/emergency/sections").status_code == 200
    if role in {"admin", "royal"}:
        assert client.get("/admin/diagnostic-rules").status_code == 200
        assert client.get("/admin/diagnostic-unknowns").status_code == 200


def test_structured_notification_moves_warn_to_crit_with_stable_identity(db_session):
    from robopark_api.services.diagnostic_rules import diagnostic_rule_matches_sample

    rule = insert_rule(
        db_session,
        source_path="robotHudData.notifications.lastWarnNotification",
        pattern="Motor: blocked",
    )

    def payload(field):
        return {
            "robotHudData": {
                "notifications": {field: {"name": "Motor", "message": "blocked", "timestamp": 42}}
            }
        }

    warn = match_diagnostic_events_for_rules([rule], payload("lastWarnNotification"))
    crit = match_diagnostic_events_for_rules([rule], payload("lastCritNotification"))
    assert [event.rule_id for event in crit] == [rule.id]
    assert warn[0].id == crit[0].id
    assert (warn[0].severity, crit[0].severity) == ("warning", "critical")
    assert (
        match_diagnostic_events_for_rules([], payload("lastWarnNotification"))[0].id
        == match_diagnostic_events_for_rules([], payload("lastCritNotification"))[0].id
    )
    assert diagnostic_rule_matches_sample(
        rule,
        payload("lastCritNotification"),
        ("robotHudData", "notifications", "lastCritNotification"),
    )


def test_matching_notification_family_keeps_strongest_live_severity(db_session):
    rule = insert_rule(db_session, source_path="lastWarnNotification", pattern="Motor: blocked")
    payload = {
        field: {"name": "Motor", "message": "blocked"}
        for field in ("lastWarnNotification", "lastCritNotification")
    }
    assert [
        (event.rule_id, event.severity)
        for event in match_diagnostic_events_for_rules([rule], payload)
    ] == [(rule.id, "critical")]


def test_unknown_notification_family_keeps_strongest_live_severity():
    payload = {
        "lastWarnNotification": "WARN: [+1s] Motor: blocked",
        "robotHudData": {
            "notifications": {
                "lastCritNotification": "CRIT: [+2s] Motor: blocked",
            }
        },
    }

    events = match_diagnostic_events_for_rules([], payload)

    assert [(event.source_path, event.severity) for event in events] == [
        ("robotHudData.notifications.lastCritNotification", "critical"),
    ]


def test_legacy_ignored_residual_does_not_suppress_original_siblings(db_session):
    db_session.add(
        DiagnosticUnknown(
            identity="legacy",
            source_path="errors",
            source_segments_json='["errors",0]',
            raw_json='{"message":"unknown"}',
            original_json='{"message":"unknown","code":"KNOWN"}',
            first_seen_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
            observations=1,
            last_robot="1",
            state="ignored",
        )
    )
    db_session.commit()
    assert (
        len(
            match_diagnostic_events(
                db_session, {"errors": [{"message": "unknown", "code": "KNOWN"}]}
            )
        )
        == 1
    )


def test_legacy_ignored_hash_suppresses_canonical_event(db_session):
    raw = "WARN: [+1.5s] Motor: blocked"
    identity = hashlib.sha256(
        json.dumps(
            [None, ["errors", None], raw], ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()
    db_session.add(
        DiagnosticUnknown(
            identity=identity,
            source_path="errors",
            source_segments_json='["errors",0]',
            raw_json=json.dumps(raw),
            original_json=json.dumps(raw),
            first_seen_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
            observations=1,
            last_robot="1",
            state="ignored",
        )
    )
    db_session.commit()
    assert match_diagnostic_events(db_session, {"errors": ["CRIT: [+99s] Motor: blocked"]}) == []


def test_colliding_legacy_ignored_samples_require_each_ignore_to_be_reopened(db_session):
    rows = []
    for index, prefix in enumerate(["WARN: [+1s]", "CRIT: [+2s]"]):
        row = DiagnosticUnknown(
            identity=f"legacy-{index}",
            source_path="custom.alerts",
            source_segments_json='["custom","alerts",0]',
            raw_json=json.dumps(f"{prefix} Motor: blocked"),
            original_json=None,
            first_seen_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
            observations=1,
            last_robot="1",
            state="ignored",
        )
        rows.append(row)
        db_session.add(row)
    insert_rule(db_session, source_path="custom.alerts", pattern="other", is_enabled=False)
    payload = {"custom": {"alerts": ["ERROR: [+8s] Motor: blocked"]}}
    assert match_diagnostic_events(db_session, payload) == []
    rows[0].state = "new"
    db_session.commit()
    assert match_diagnostic_events(db_session, payload) == []
    rows[1].state = "new"
    db_session.commit()
    assert len(match_diagnostic_events(db_session, payload)) == 1


def test_exact_pattern_saved_canonically_and_regex_unchanged(admin):
    created = admin.post(
        "/admin/diagnostic-rules", json={**RULE, "pattern": "WARN: [+3s] Motor: blocked"}
    )
    assert created.status_code == 201
    assert created.json()["pattern"] == "Motor: blocked"
    changed = admin.patch(
        f"/admin/diagnostic-rules/{created.json()['id']}",
        json={"pattern": "CRIT: [+4s] Motor: hot"},
    )
    assert changed.json()["pattern"] == "Motor: hot"
    regex = admin.post(
        "/admin/diagnostic-rules", json={**RULE, "match_kind": "regex", "pattern": "WARN:.*Motor"}
    )
    assert regex.json()["pattern"] == "WARN:.*Motor"


def test_disabled_and_missing_readings_have_distinct_labels(db_session):
    add_section(db_session, "status")
    add_reading(db_session, path="distance", enabled_path="enabled", no_data_json="[999]")
    db_session.commit()
    assert (
        render_readings(db_session, {"distance": 1, "enabled": False}, "mechanic")[0].display
        == "Отключён"
    )
    assert (
        render_readings(db_session, {"distance": 999, "enabled": True}, "mechanic")[0].display
        == "Нет показания"
    )
    assert render_readings(db_session, {"enabled": True}, "mechanic")[0].display == "Нет показания"
