"""Unknown inbox is fed by authorized snapshots and classified atomically."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError

from conftest import login_as, role_id_for
from robopark_api.models import DiagnosticRule, User
from robopark_api.routers import emergency
from robopark_api.security import hash_password
from test_admin_diagnostic_rules import RULE

BASE = "/admin/diagnostic-unknowns"


@pytest.fixture
def royal_client(client, seed_royal):
    assert login_as(client, "royal", "secret").status_code == 204
    return client


@pytest.fixture
def snapshot(monkeypatch):
    payload = {
        "errors": {"navigation": ["WHEEL_BLOCKED", "WHEEL_BLOCKED"]},
        "cookie": "never-store",
    }
    monkeypatch.setattr(emergency, "_get_robot_payload", lambda db, vin: payload)
    return payload


def test_snapshot_to_inbox_to_rule(royal_client, snapshot):
    before = deepcopy(snapshot)
    response = royal_client.get("/emergency/001/snapshot")
    assert response.status_code == 200
    listed = royal_client.get(BASE)
    assert listed.status_code == 200
    result = listed.json()
    assert result["total"] == 1
    item = result["items"][0]
    assert item["source_path"] == "errors.navigation"
    assert item["pattern"] == item["raw_value"] == "WHEEL_BLOCKED"
    assert item["observations"] == 1
    assert item["state"] == "new"
    assert snapshot == before
    assert "never-store" not in listed.text
    created = royal_client.post(f"{BASE}/{item['id']}/classify", json={"rule": RULE})
    assert created.status_code == 201
    assert royal_client.get(BASE).json()["total"] == 0
    assert (
        royal_client.get(BASE, params={"state": "mapped"}).json()["items"][0]["rule_id"]
        == created.json()["id"]
    )
    next_snapshot = royal_client.get("/emergency/001/snapshot").json()
    assert next_snapshot["diagnostic_events"][0]["rule_id"] == created.json()["id"]


def test_ignore_hides_the_raw_signal_until_reopen(royal_client, snapshot, monkeypatch):
    from robopark_api.services import emergency_cache

    invalidated: list[tuple[str, str | None]] = []

    def invalidate(vin: str, *, identity: str | None = None) -> None:
        invalidated.append((vin, identity))

    monkeypatch.setattr(emergency_cache, "invalidate_vin", invalidate)
    royal_client.get("/emergency/001/snapshot")
    item = royal_client.get(BASE).json()["items"][0]
    robot = item["last_robot"]
    snapshot["errors"]["navigation"].insert(0, "OTHER")
    royal_client.get("/emergency/001/snapshot")
    current = royal_client.get(f"{BASE}/{item['id']}").json()
    assert current["observations"] == 1
    ignored = royal_client.post(f"{BASE}/{item['id']}/ignore")
    assert ignored.status_code == 200
    assert ignored.json()["state"] == "ignored"
    assert "last_robot" not in ignored.json()
    assert "raw_value" not in ignored.json()
    assert invalidated == [(robot, None)]
    events = royal_client.get("/emergency/001/snapshot").json()["diagnostic_events"]
    assert not any(e["raw_value"] == "WHEEL_BLOCKED" and e["rule_id"] is None for e in events)
    assert royal_client.post(f"{BASE}/{item['id']}/reopen").json()["state"] == "new"
    assert invalidated == [(robot, None), (robot, None)]
    events = royal_client.get("/emergency/001/snapshot").json()["diagnostic_events"]
    assert any(e["raw_value"] == "WHEEL_BLOCKED" and e["rule_id"] is None for e in events)
    royal_client.get("/emergency/002/snapshot")
    assert royal_client.get(f"{BASE}/{item['id']}").json()["observations"] == 2


def test_ignore_and_reopen_invalidate_the_active_cookie_live_merge_entry(
    royal_client, snapshot, db_session, tmp_path, monkeypatch
):
    from robopark_api.services import emergency_cache
    from robopark_api.services import platform_settings as settings_svc
    from robopark_api.services.live_merge import LiveMergeStore
    from robopark_api.services.ops import maintenance

    identity = settings_svc.activate_emergency_cookie(
        db_session, cookie="active-cookie", status="valid", checked_robot="1"
    )
    store = LiveMergeStore(tmp_path)
    monkeypatch.setattr(emergency_cache, "get_live_merge_store", lambda: store)
    monkeypatch.setattr(maintenance, "require_application_writes", lambda: None)
    royal_client.get("/emergency/001/snapshot")
    item = royal_client.get(BASE).json()["items"][0]
    key = emergency_cache._shared_key(identity, item["last_robot"])

    for action in ("ignore", "reopen"):
        store.merge_load(
            emergency_cache._MERGE_NS, key, 60, lambda action=action: {"cached": action}
        )
        assert store.try_fresh(emergency_cache._MERGE_NS, key, 60)[0]
        assert royal_client.post(f"{BASE}/{item['id']}/{action}").status_code == 200
        assert not store.try_fresh(emergency_cache._MERGE_NS, key, 60)[0]


@pytest.mark.parametrize(
    "changes", [{"pattern": "OTHER"}, {"source_path": "panics"}, {"is_enabled": False}]
)
def test_classify_must_match_sample(royal_client, db_session, snapshot, changes):
    royal_client.get("/emergency/001/snapshot")
    item = royal_client.get(BASE).json()["items"][0]
    response = royal_client.post(
        f"{BASE}/{item['id']}/classify", json={"rule": {**RULE, **changes}}
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "unknown_rule_does_not_match"
    assert db_session.scalar(select(DiagnosticRule)) is None
    assert royal_client.get(f"{BASE}/{item['id']}").json()["state"] == "new"


@pytest.mark.parametrize("role", ["operator", "mechanic", "driver"])
def test_rbac(client, db_session, role):
    user = User(
        username="restricted",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, role),
        access_status="approved",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    assert login_as(client, "restricted", "secret").status_code == 204
    responses = [
        client.get(BASE),
        client.get(BASE + "/1"),
        client.post(BASE + "/1/classify", json={"rule": RULE}),
        client.post(BASE + "/1/ignore"),
        client.post(BASE + "/1/reopen"),
    ]
    assert [r.status_code for r in responses] == [403] * 5


def test_preview_and_denied_scope_do_not_capture(royal_client, snapshot, monkeypatch):
    from fastapi import HTTPException

    assert (
        royal_client.post(
            "/admin/diagnostic-rules/preview", json={"rule": RULE, "payload": snapshot}
        ).status_code
        == 200
    )
    assert royal_client.get(BASE).json()["total"] == 0

    def denied(*args):
        raise HTTPException(status_code=403)

    monkeypatch.setattr(emergency, "_enforce_vin_scope", denied)
    assert royal_client.get("/emergency/001/snapshot").status_code == 403
    assert royal_client.get(BASE).json()["total"] == 0


def test_atomic_classification_rolls_back_rule(royal_client, db_session, snapshot):
    from robopark_api.models import DiagnosticUnknown

    royal_client.get("/emergency/001/snapshot")
    item = royal_client.get(BASE).json()["items"][0]

    def reject_update(mapper, connection, target):
        if target.state == "mapped":
            raise IntegrityError("injected", {}, None)

    event.listen(DiagnosticUnknown, "before_update", reject_update)
    try:
        response = royal_client.post(f"{BASE}/{item['id']}/classify", json={"rule": RULE})
    finally:
        event.remove(DiagnosticUnknown, "before_update", reject_update)
    assert response.status_code == 409
    assert db_session.scalar(select(DiagnosticRule)) is None
    assert royal_client.get(f"{BASE}/{item['id']}").json()["state"] == "new"


def test_capture_database_failure_keeps_snapshot(royal_client, snapshot, caplog):
    from robopark_api.models import DiagnosticUnknown

    def reject_insert(*args):
        raise IntegrityError("secret-must-not-leak", {}, None)

    event.listen(DiagnosticUnknown, "before_insert", reject_insert)
    try:
        response = royal_client.get("/emergency/001/snapshot")
    finally:
        event.remove(DiagnosticUnknown, "before_insert", reject_insert)
    assert response.status_code == 200
    assert response.json()["diagnostic_events"][0]["raw_value"] == "WHEEL_BLOCKED"
    assert royal_client.get(BASE).json()["total"] == 0
    assert "secret-must-not-leak" not in caplog.text


def test_capture_sample_bounds_credentials_and_json_string(royal_client, snapshot):
    snapshot["errors"]["navigation"] = [
        "x" * 8193,
        {"code": "A", "token": "secret"},
        "Bearer secret",
        '"literal"',
    ]
    royal_client.get("/emergency/001/snapshot")
    listed = royal_client.get(BASE).json()
    assert listed["total"] == 1
    item = listed["items"][0]
    assert item["raw_value"] == '"literal"'
    rule = {**RULE, "pattern": '"literal"'}
    assert (
        royal_client.post(f"{BASE}/{item['id']}/classify", json={"rule": rule}).status_code == 201
    )


def test_parallel_capture_deduplicates_and_samples(db_engine):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy.orm import Session

    from robopark_api.models import DiagnosticUnknown
    from robopark_api.services.diagnostic_rules import match_diagnostic_events_for_rules
    from robopark_api.services.diagnostic_unknowns import capture_unknowns

    events = match_diagnostic_events_for_rules([], {"errors": ["SAME", "SAME"]})
    barrier = Barrier(4)
    now = datetime.now(UTC)

    def capture():
        with Session(db_engine) as db:
            barrier.wait()
            capture_unknowns(
                db, events, "robot", payload={"errors": [events[0].raw_value]}, now=now
            )

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: capture(), range(4)))
    with Session(db_engine) as db:
        rows = list(db.scalars(select(DiagnosticUnknown)))
        assert len(rows) == 1
        assert rows[0].observations == 1
        capture_unknowns(
            db,
            events,
            "robot",
            payload={"errors": [events[0].raw_value]},
            now=now + timedelta(seconds=60),
        )
        db.refresh(rows[0])
        assert rows[0].observations == 2
        capture_unknowns(
            db,
            events,
            "robot2",
            payload={"errors": [events[0].raw_value]},
            now=now + timedelta(seconds=61),
        )
        capture_unknowns(
            db,
            events,
            "robot",
            payload={"errors": [events[0].raw_value]},
            now=now + timedelta(seconds=62),
        )
        db.refresh(rows[0])
        assert rows[0].observations == 3


@pytest.mark.parametrize("state", ["new", "ignored", "mapped"])
def test_pagination(royal_client, snapshot, state):
    snapshot["errors"]["navigation"] = ["A", "B", "C"]
    royal_client.get("/emergency/001/snapshot")
    items = royal_client.get(BASE).json()["items"]
    if state == "ignored":
        for item in items:
            royal_client.post(f"{BASE}/{item['id']}/ignore")
    if state == "mapped":
        for item in items:
            assert (
                royal_client.post(
                    f"{BASE}/{item['id']}/classify",
                    json={"rule": {**RULE, "pattern": item["pattern"]}},
                ).status_code
                == 201
            )
    page = royal_client.get(BASE, params={"state": state, "limit": 2}).json()
    assert page["total"] == 3
    assert len(page["items"]) == 2
    assert page["has_more"]
    final = royal_client.get(BASE, params={"state": state, "limit": 2, "offset": 2}).json()
    assert len(final["items"]) == 1
    assert not final["has_more"]
    assert {item["id"] for item in page["items"]}.isdisjoint(item["id"] for item in final["items"])


def test_validation_is_sanitized(royal_client, snapshot):
    secret = "do-not-echo-this-private-example"
    response = royal_client.post(
        BASE + "/1/classify", json={"rule": {**RULE, "x": secret}, secret: "extra"}
    )
    assert response.status_code == 422
    assert secret not in response.text
    assert royal_client.get(BASE, params={"state": secret}).status_code == 422
    assert secret not in royal_client.get(BASE, params={"state": secret}).text
    assert royal_client.get(BASE + "/999").status_code == 404
    assert royal_client.get(BASE, params={"limit": 0}).status_code == 422


def test_capture_preparation_failure_keeps_snapshot(royal_client, snapshot, monkeypatch):
    from robopark_api.services import diagnostic_unknowns

    def broken(value):
        raise ValueError("private-sample")

    monkeypatch.setattr(diagnostic_unknowns, "canonical", broken)
    assert royal_client.get("/emergency/001/snapshot").status_code == 200


@pytest.mark.parametrize(
    "role,status",
    [
        ("admin", "approved"),
        ("custom-admin", "approved"),
        ("admin", "pending"),
        ("royal", "pending"),
    ],
)
def test_strict_builtin_approved_admin_gate(client, db_session, role, status):
    from robopark_api.models import Permission, Role

    if role == "custom-admin":
        custom = Role(slug=role, name="Custom", is_system=False)
        db_session.add(custom)
        db_session.flush()
        custom.permissions = list(db_session.scalars(select(Permission)))
        role_id = custom.id
    else:
        role_id = role_id_for(db_session, role)
    user = User(
        username="gate",
        password_hash=hash_password("secret"),
        role_id=role_id,
        access_status=status,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    assert login_as(client, "gate", "secret").status_code == 204
    responses = [
        client.get(BASE),
        client.get(BASE + "/1"),
        client.post(BASE + "/1/classify", json={"rule": RULE}),
        client.post(BASE + "/1/ignore"),
        client.post(BASE + "/1/reopen"),
    ]
    assert [r.status_code for r in responses] == (
        [200, 404, 404, 404, 404] if role == "admin" and status == "approved" else [403] * 5
    )


def test_unauthenticated_inbox(client):
    assert client.get(BASE).status_code == 401
    assert client.get(BASE + "/1").status_code == 401
    assert client.post(BASE + "/1/classify", json={"rule": RULE}).status_code == 401
    assert client.post(BASE + "/1/ignore").status_code == 401
    assert client.post(BASE + "/1/reopen").status_code == 401


def test_inbox_audit_contains_structure_only(royal_client, snapshot, db_session):
    from robopark_api.models import AuditLog

    royal_client.get("/emergency/001/snapshot")
    item = royal_client.get(BASE).json()["items"][0]
    royal_client.post(f"{BASE}/{item['id']}/ignore")
    royal_client.post(f"{BASE}/{item['id']}/reopen")
    royal_client.post(f"{BASE}/{item['id']}/classify", json={"rule": RULE})
    audits = list(
        db_session.scalars(
            select(AuditLog)
            .where(AuditLog.target_type == "diagnostic_unknown")
            .order_by(AuditLog.id)
        )
    )
    assert [entry.action for entry in audits] == [
        "admin.diagnostic_unknown.ignored",
        "admin.diagnostic_unknown.reopened",
        "admin.diagnostic_unknown.classified",
    ]
    for entry in audits:
        assert "WHEEL_BLOCKED" not in entry.detail
        assert entry.actor_role == "royal"
        assert entry.target_id == str(item["id"])


def test_concurrent_classification_creates_one_rule(royal_client, snapshot, db_engine, seed_royal):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from fastapi import HTTPException, Response
    from sqlalchemy.orm import Session

    from robopark_api.models import DiagnosticUnknown
    from robopark_api.routers.admin_diagnostic_unknowns import UnknownClassify, classify_unknown

    royal_client.get("/emergency/001/snapshot")
    item = royal_client.get(BASE).json()["items"][0]
    actor_id = seed_royal.id
    barrier = Barrier(2)

    def classify(kind):
        with Session(db_engine) as db:
            actor = db.get(User, actor_id)
            barrier.wait()
            try:
                classify_unknown(
                    item["id"],
                    UnknownClassify(rule={**RULE, "match_kind": kind}),
                    Response(),
                    db,
                    actor,
                )
                return 201
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(classify, ["exact", "regex"]))
    assert sorted(outcomes) == [201, 409]
    with Session(db_engine) as db:
        rules = list(db.scalars(select(DiagnosticRule)))
        assert len(rules) == 1
        assert db.get(DiagnosticUnknown, item["id"]).rule_id == rules[0].id


def test_fresh_sighting_poll_is_read_only(db_engine):
    from sqlalchemy.orm import Session

    from robopark_api.services.diagnostic_rules import match_diagnostic_events_for_rules
    from robopark_api.services.diagnostic_unknowns import capture_unknowns

    events = match_diagnostic_events_for_rules([], {"errors": ["A"]})
    now = datetime.now(UTC)
    with Session(db_engine) as db:
        capture_unknowns(db, events, "robot", payload={"errors": [events[0].raw_value]}, now=now)
        statements = []

        def record_statement(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(db_engine, "before_cursor_execute", record_statement)
        try:
            capture_unknowns(
                db,
                events,
                "robot",
                payload={"errors": [events[0].raw_value]},
                now=now + timedelta(seconds=2),
            )
        finally:
            event.remove(db_engine, "before_cursor_execute", record_statement)
        assert statements
        assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements)


def test_classify_rejects_match_on_synthetic_ancestor(royal_client, snapshot, db_session):
    snapshot["errors"]["navigation"] = []
    snapshot["telemetry"] = {"code": ["OTHER", "OTHER", "TARGET"], "real_sibling": "present"}
    db_session.add(
        DiagnosticRule(
            **{**RULE, "source_path": "telemetry.code.2", "pattern": "KNOWN", "is_enabled": False}
        )
    )
    db_session.commit()
    royal_client.get("/emergency/001/snapshot")
    item = royal_client.get(BASE).json()["items"][0]
    assert item["raw_value"] == "TARGET"
    candidate = {**RULE, "source_path": "telemetry", "pattern": '{"code":[null,null,"TARGET"]}'}
    response = royal_client.post(f"{BASE}/{item['id']}/classify", json={"rule": candidate})
    assert response.status_code == 422
    assert response.json()["detail"] == "unknown_rule_does_not_match"
    valid = {**RULE, "source_path": "telemetry.code", "pattern": "TARGET"}
    assert (
        royal_client.post(f"{BASE}/{item['id']}/classify", json={"rule": valid}).status_code == 201
    )


def test_classify_projected_unknown_uses_original_unit(royal_client, snapshot, db_session):
    import json

    snapshot["errors"] = {"code": ["KNOWN", "TARGET"], "message": "Summary"}
    db_session.add(DiagnosticRule(**{**RULE, "source_path": "errors.code", "pattern": "KNOWN"}))
    db_session.commit()
    royal_client.get("/emergency/001/snapshot")
    item = royal_client.get(BASE).json()["items"][0]
    residual = {"code": ["TARGET"], "message": "Summary"}
    candidate = {
        **RULE,
        "source_path": "errors",
        "pattern": json.dumps(residual, sort_keys=True, separators=(",", ":")),
    }
    rejected = royal_client.post(f"{BASE}/{item['id']}/classify", json={"rule": candidate})
    assert rejected.status_code == 422
    assert item["raw_value"] == residual
    assert item["original_value"] == snapshot["errors"]
    candidate["pattern"] = item["pattern"]
    mapped = royal_client.post(f"{BASE}/{item['id']}/classify", json={"rule": candidate})
    assert mapped.status_code == 201
    next_events = royal_client.get("/emergency/001/snapshot").json()["diagnostic_events"]
    assert all(event["rule_id"] is not None for event in next_events)
    assert any(event["rule_id"] == mapped.json()["id"] for event in next_events)


@pytest.mark.parametrize("operation", ["ignore", "reopen"])
def test_stale_actions_cannot_unmap_classified_unknown(royal_client, snapshot, operation):
    royal_client.get("/emergency/001/snapshot")
    item = royal_client.get(BASE).json()["items"][0]
    mapped = royal_client.post(f"{BASE}/{item['id']}/classify", json={"rule": RULE})
    assert mapped.status_code == 201
    stale = royal_client.post(f"{BASE}/{item['id']}/{operation}")
    assert stale.status_code == 409
    assert stale.json()["detail"] == "diagnostic_unknown_already_mapped"
    current = royal_client.get(f"{BASE}/{item['id']}").json()
    assert current["state"] == "mapped"
    assert current["rule_id"] == mapped.json()["id"]


def test_legacy_unknown_requires_real_recapture(royal_client, snapshot, db_session):
    from robopark_api.models import DiagnosticUnknown

    royal_client.get("/emergency/001/snapshot")
    item = royal_client.get(BASE).json()["items"][0]
    row = db_session.get(DiagnosticUnknown, item["id"])
    row.original_json = None
    db_session.commit()
    legacy = royal_client.get(f"{BASE}/{item['id']}").json()
    assert legacy["original_value"] is None
    rejected = royal_client.post(f"{BASE}/{item['id']}/classify", json={"rule": RULE})
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "unknown_sample_requires_observation"
    # The 60s sighting throttle must not prevent a legacy original from backfill.
    royal_client.get("/emergency/001/snapshot")
    fresh = royal_client.get(f"{BASE}/{item['id']}").json()
    assert fresh["original_value"] == "WHEEL_BLOCKED"
    assert fresh["observations"] == 1
    assert (
        royal_client.post(f"{BASE}/{item['id']}/classify", json={"rule": RULE}).status_code == 201
    )


def test_classify_cannot_match_only_previously_known_sibling(royal_client, snapshot, db_session):
    snapshot["errors"] = {"code": ["KNOWN", "TARGET"], "message": "Summary"}
    db_session.add(DiagnosticRule(**{**RULE, "source_path": "errors.code", "pattern": "KNOWN"}))
    db_session.commit()
    royal_client.get("/emergency/001/snapshot")
    item = royal_client.get(BASE).json()["items"][0]
    candidate = {**RULE, "source_path": "errors.code", "match_kind": "regex", "pattern": "^KNOWN$"}
    rejected = royal_client.post(f"{BASE}/{item['id']}/classify", json={"rule": candidate})
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "unknown_rule_does_not_match"
    candidate["pattern"] = "^TARGET$"
    mapped = royal_client.post(f"{BASE}/{item['id']}/classify", json={"rule": candidate})
    assert mapped.status_code == 201
    assert all(
        e["rule_id"] is not None
        for e in royal_client.get("/emergency/001/snapshot").json()["diagnostic_events"]
    )


def test_cleanup_deletes_only_old_or_overflowing_new_unknowns(db_session):
    from robopark_api.models import DiagnosticUnknown
    from robopark_api.services.diagnostic_unknowns import prune_diagnostic_unknowns

    now = datetime(2026, 9, 14, tzinfo=UTC)

    def unknown(identity: str, *, state: str, seen: datetime) -> DiagnosticUnknown:
        return DiagnosticUnknown(
            identity=identity,
            source_path="errors",
            source_segments_json='["errors"]',
            raw_json='"sample"',
            original_json='"sample"',
            first_seen_at=seen,
            last_seen_at=seen,
            observations=1,
            last_robot="robot",
            state=state,
        )

    db_session.add(unknown("old-new", state="new", seen=now - timedelta(days=30)))
    db_session.add(unknown("old-mapped", state="mapped", seen=now - timedelta(days=365)))
    db_session.add(unknown("old-ignored", state="ignored", seen=now - timedelta(days=365)))
    for index in range(1002):
        db_session.add(unknown(f"new-{index}", state="new", seen=now - timedelta(seconds=index)))
    db_session.commit()

    removed = prune_diagnostic_unknowns(db_session, now=now)

    remaining_new = list(
        db_session.scalars(select(DiagnosticUnknown).where(DiagnosticUnknown.state == "new"))
    )
    assert removed == 3
    assert len(remaining_new) == 1000
    assert db_session.scalar(
        select(DiagnosticUnknown).where(DiagnosticUnknown.identity == "new-999")
    )
    assert (
        db_session.scalar(select(DiagnosticUnknown).where(DiagnosticUnknown.identity == "new-1000"))
        is None
    )
    assert (
        db_session.scalar(select(DiagnosticUnknown).where(DiagnosticUnknown.identity == "new-1001"))
        is None
    )
    assert db_session.scalar(
        select(DiagnosticUnknown).where(DiagnosticUnknown.identity == "old-mapped")
    )
    assert db_session.scalar(
        select(DiagnosticUnknown).where(DiagnosticUnknown.identity == "old-ignored")
    )
