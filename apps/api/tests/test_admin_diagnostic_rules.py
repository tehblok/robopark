"""Administration uses real persistence/matching without retaining preview input."""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import event, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from conftest import login_as, role_id_for
from robopark_api.db import get_db
from robopark_api.models import AuditLog, DiagnosticRule, Permission, Role, User
from robopark_api.security import hash_password
from robopark_api.services import rbac
from robopark_api.services.diagnostic_rules import match_diagnostic_events

BASE = "/admin/diagnostic-rules"
RULE = {
    "source_path": "errors.navigation",
    "match_kind": "exact",
    "pattern": "WHEEL_BLOCKED",
    "example": "WHEEL_BLOCKED",
    "title": "Wheel is blocked",
    "description": "Remove the obstacle and inspect the wheel.",
    "severity": "critical",
    "part": "front_left_wheel",
    "preferred_view": "front",
    "x": 0.25,
    "y": 0.75,
    "indicator": "point",
    "is_enabled": True,
    "sort_order": 0,
}
PRIVATE_INPUT = "private-diagnostic-validation-token-9382"


def insert_rule(db_session, **changes):
    rule = DiagnosticRule(**{**RULE, **changes})
    db_session.add(rule)
    db_session.commit()
    return rule


@pytest.fixture
def royal_client(client, seed_royal):
    assert login_as(client, "royal", "secret").status_code == 204
    return client


def rule_audits(db_session):
    return list(
        db_session.scalars(
            select(AuditLog).where(AuditLog.target_type == "diagnostic_rule").order_by(AuditLog.id)
        )
    )


def exercise_routes(client):
    return [
        client.get(BASE),
        client.post(BASE, json=RULE),
        client.patch(f"{BASE}/1", json={"title": "Updated"}),
        client.post(f"{BASE}/1/disable"),
        client.put(f"{BASE}/reorder", json={"ids": []}, headers={"If-Match": '"old"'}),
        client.post(f"{BASE}/preview", json={"rule": RULE}),
    ]


def test_unauthenticated_requests_are_denied(client):
    assert [response.status_code for response in exercise_routes(client)] == [401] * 6


@pytest.mark.parametrize("role", ["operator", "mechanic", "driver", "custom-admin"])
def test_other_roles_cannot_use_any_rule_endpoint(client, db_session, role):
    if role == "custom-admin":
        custom = Role(slug=role, name="Custom admin", is_system=False)
        db_session.add(custom)
        db_session.flush()
        custom.permissions = list(
            db_session.scalars(
                select(Permission).where(
                    Permission.key.in_([rbac.PERMISSION_NAV_ADMIN, rbac.PERMISSION_PARKS_MANAGE])
                )
            )
        )
        role_id = custom.id
    else:
        role_id = role_id_for(db_session, role)
    db_session.add(
        User(
            username="restricted",
            password_hash=hash_password("secret"),
            role_id=role_id,
            access_status="approved",
            is_active=True,
        )
    )
    db_session.commit()
    assert login_as(client, "restricted", "secret").status_code == 204
    assert [response.status_code for response in exercise_routes(client)] == [403] * 6
    assert db_session.scalar(select(DiagnosticRule)) is None


@pytest.mark.parametrize("role", ["admin", "royal"])
def test_built_in_admin_roles_can_manage_rules(client, db_session, role):
    user = User(
        username="allowed",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, role),
        access_status="approved",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    assert login_as(client, "allowed", "secret").status_code == 204
    created = client.post(BASE, json=RULE)
    assert created.status_code == 201
    rule_id = created.json()["id"]
    assert created.json() == {**RULE, "id": rule_id}
    changed = client.patch(f"{BASE}/{rule_id}", json={"title": "Updated", "x": 0.5})
    assert changed.status_code == 200
    assert changed.json() == {**RULE, "id": rule_id, "title": "Updated", "x": 0.5}
    assert client.post(f"{BASE}/preview", json={"rule": RULE}).json()["matched"] is True
    listed = client.get(BASE)
    assert listed.status_code == 200
    reordered = client.put(
        f"{BASE}/reorder",
        json={"ids": [rule_id]},
        headers={"If-Match": listed.headers["etag"]},
    )
    assert reordered.status_code == 200
    assert client.post(f"{BASE}/{rule_id}/disable").status_code == 200
    assert all(entry.actor_user_id == user.id for entry in rule_audits(db_session))


def test_list_includes_disabled_rules_in_stable_sort_order(royal_client, db_session):
    first = insert_rule(db_session, pattern="first", sort_order=2)
    second = insert_rule(db_session, pattern="second", sort_order=-1, is_enabled=False)
    third = insert_rule(db_session, pattern="third", sort_order=2)
    response = royal_client.get(BASE)
    assert response.status_code == 200
    assert [rule["id"] for rule in response.json()] == [second.id, first.id, third.id]
    assert response.json()[0]["is_enabled"] is False
    assert response.headers["etag"] == royal_client.get(BASE).headers["etag"]


@pytest.mark.parametrize(
    "changes",
    [
        {"source_path": "errors..code"},
        {"source_path": "errors.__class__"},
        {"source_path": "errors[0]"},
        {"match_kind": "regex", "pattern": "["},
        {"match_kind": "regex", "pattern": "(?R)"},
        {"match_kind": "regex", "pattern": "a{1001}"},
        {"match_kind": "regex", "pattern": "(?:a{100}){1000}"},
        {"x": 1.001},
        {"severity": "error"},
        {"preferred_view": "bottom"},
        {"indicator": "blink"},
        {"title": None},
    ],
)
def test_create_rejects_invalid_rules_without_writes(royal_client, db_session, changes):
    response = royal_client.post(BASE, json={**RULE, **changes})
    assert response.status_code == 422
    assert db_session.scalar(select(DiagnosticRule)) is None
    assert rule_audits(db_session) == []


@pytest.mark.parametrize("field", list(RULE))
def test_update_rejects_explicit_null_without_changing_rule(royal_client, db_session, field):
    rule = insert_rule(db_session)
    response = royal_client.patch(f"{BASE}/{rule.id}", json={field: None})
    assert response.status_code == 422
    db_session.refresh(rule)
    assert getattr(rule, field) == RULE[field]
    assert rule_audits(db_session) == []


def test_update_validates_resulting_regex_even_when_pattern_is_omitted(royal_client, db_session):
    rule = insert_rule(db_session, pattern="[")
    response = royal_client.patch(f"{BASE}/{rule.id}", json={"match_kind": "regex"})
    assert response.status_code == 422
    assert response.json()["detail"] == "invalid_diagnostic_regex"
    db_session.refresh(rule)
    assert rule.match_kind == "exact"
    assert rule_audits(db_session) == []
    assert royal_client.patch(f"{BASE}/{rule.id}", json={"example": "["}).status_code == 200


def test_exact_patterns_are_not_compiled(royal_client):
    created = royal_client.post(BASE, json={**RULE, "pattern": "[", "example": "["})
    assert created.status_code == 201
    preview = royal_client.post(
        f"{BASE}/preview", json={"rule": {**RULE, "pattern": "[", "example": "["}}
    )
    assert preview.status_code == 200
    assert preview.json()["matched"] is True


def test_unique_create_and_update_conflicts_are_409_and_leave_no_partial_write(
    royal_client, db_session
):
    first = insert_rule(db_session)
    second = insert_rule(db_session, pattern="SECOND")
    duplicate = royal_client.post(BASE, json=RULE)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "diagnostic_rule_conflict"
    changed = royal_client.patch(
        f"{BASE}/{second.id}", json={"pattern": first.pattern, "title": "Must roll back"}
    )
    assert changed.status_code == 409
    assert changed.json()["detail"] == "diagnostic_rule_conflict"
    db_session.refresh(second)
    assert second.pattern == "SECOND"
    assert second.title == RULE["title"]
    assert rule_audits(db_session) == []
    assert royal_client.patch(f"{BASE}/{second.id}", json={"title": "Recovered"}).status_code == 200


def test_missing_mutation_targets_are_404(royal_client):
    assert royal_client.patch(f"{BASE}/999", json={"title": "Missing"}).status_code == 404
    assert royal_client.post(f"{BASE}/999/disable").status_code == 404


def test_disable_is_idempotent_preserves_order_and_never_deletes(royal_client, db_session):
    rule = insert_rule(db_session, sort_order=7)
    for _ in range(2):
        response = royal_client.post(f"{BASE}/{rule.id}/disable")
        assert response.status_code == 200
        assert response.json() == {**RULE, "id": rule.id, "is_enabled": False, "sort_order": 7}
    assert royal_client.delete(f"{BASE}/{rule.id}").status_code == 405
    assert len(rule_audits(db_session)) == 1
    assert royal_client.patch(f"{BASE}/{rule.id}", json={"is_enabled": True}).status_code == 200
    assert db_session.get(DiagnosticRule, rule.id).is_enabled is True


def test_reorder_includes_disabled_rules_and_rejects_stale_order(royal_client, db_session):
    first = insert_rule(db_session, pattern="first", sort_order=10)
    second = insert_rule(db_session, pattern="second", sort_order=10, is_enabled=False)
    before = royal_client.get(BASE)
    assert before.status_code == 200
    headers = {"If-Match": before.headers["etag"]}
    response = royal_client.put(
        f"{BASE}/reorder", json={"ids": [second.id, first.id]}, headers=headers
    )
    assert response.status_code == 200
    assert [(rule["id"], rule["sort_order"]) for rule in response.json()] == [
        (second.id, 0),
        (first.id, 1),
    ]
    assert response.headers["etag"] != before.headers["etag"]
    stale = royal_client.put(
        f"{BASE}/reorder", json={"ids": [first.id, second.id]}, headers=headers
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "diagnostic_rules_changed"
    assert royal_client.get(BASE).json() == response.json()
    assert len(rule_audits(db_session)) == 2


@pytest.mark.parametrize("ids", [[], [1], [1, 1], [1, 999], [1, 2, 999], [True, 2]])
def test_invalid_reorder_is_atomic(royal_client, db_session, ids):
    insert_rule(db_session, pattern="first", sort_order=3)
    insert_rule(db_session, pattern="second", sort_order=9, is_enabled=False)
    before = royal_client.get(BASE)
    assert before.status_code == 200
    response = royal_client.put(
        f"{BASE}/reorder", json={"ids": ids}, headers={"If-Match": before.headers["etag"]}
    )
    assert response.status_code == 422
    assert royal_client.get(BASE).json() == before.json()
    assert rule_audits(db_session) == []


def test_reorder_requires_a_precondition_and_handles_empty_catalog(royal_client):
    response = royal_client.put(f"{BASE}/reorder", json={"ids": []})
    assert response.status_code == 428
    listed = royal_client.get(BASE)
    response = royal_client.put(
        f"{BASE}/reorder", json={"ids": []}, headers={"If-Match": listed.headers["etag"]}
    )
    assert response.status_code == 200
    assert response.json() == []


def test_catalog_mutations_invalidate_a_previous_reorder_precondition(royal_client, db_session):
    rule = insert_rule(db_session)
    before = royal_client.get(BASE)
    assert before.status_code == 200
    assert (
        royal_client.patch(f"{BASE}/{rule.id}", json={"title": "Other editor"}).status_code == 200
    )
    response = royal_client.put(
        f"{BASE}/reorder", json={"ids": [rule.id]}, headers={"If-Match": before.headers["etag"]}
    )
    assert response.status_code == 409


def test_competing_reorders_have_one_winner(royal_client, db_session, db_engine):
    first = insert_rule(db_session, pattern="first", sort_order=5)
    second = insert_rule(db_session, pattern="second", sort_order=8)
    ids = [first.id, second.id]
    listed = royal_client.get(BASE)
    assert listed.status_code == 200
    headers = {"If-Match": listed.headers["etag"]}

    def separate_session():
        with Session(db_engine) as session:
            yield session

    royal_client.app.dependency_overrides[get_db] = separate_session
    barrier = Barrier(2)

    def reorder(order):
        barrier.wait(timeout=5)
        return royal_client.put(f"{BASE}/reorder", json={"ids": order}, headers=headers)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(reorder, [ids, ids[::-1]]))
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(response for response in responses if response.status_code == 200)
    assert royal_client.get(BASE).json() == winner.json()


def test_reorder_database_failure_rolls_back_every_row(royal_client, db_session):
    first = insert_rule(db_session, pattern="first", sort_order=5)
    second = insert_rule(db_session, pattern="second", sort_order=8)
    listed = royal_client.get(BASE)
    assert listed.status_code == 200
    second_id = second.id

    def reject_second_update(mapper, connection, target):
        if target.id == second_id:
            raise IntegrityError("injected storage conflict", {}, None)

    event.listen(DiagnosticRule, "before_update", reject_second_update)
    try:
        response = royal_client.put(
            f"{BASE}/reorder",
            json={"ids": [second.id, first.id]},
            headers={"If-Match": listed.headers["etag"]},
        )
    finally:
        event.remove(DiagnosticRule, "before_update", reject_second_update)
    assert response.status_code == 409
    assert royal_client.get(BASE).json() == listed.json()
    assert rule_audits(db_session) == []


def test_database_write_contention_is_a_safe_conflict(royal_client, db_session, db_engine, caplog):
    rule = insert_rule(db_session)
    rule_id = rule.id
    db_session.execute(text("PRAGMA busy_timeout=1"))
    with db_engine.begin() as connection:
        connection.execute(update(DiagnosticRule).values(sort_order=9))
        response = royal_client.patch(f"{BASE}/{rule_id}", json={"title": "Do not lose update"})
    assert response.status_code == 409
    assert response.json()["detail"] == "diagnostic_rules_write_conflict"
    db_session.refresh(rule)
    assert rule.title == RULE["title"]
    assert rule.sort_order == 9
    assert rule_audits(db_session) == []
    assert "UPDATE diagnostic_rules" not in caplog.text


@pytest.mark.parametrize(
    ("source", "example", "pattern", "expected_raw"),
    [
        ("errors.navigation", "WHEEL_BLOCKED", "WHEEL_BLOCKED", "WHEEL_BLOCKED"),
        ("telemetry.2.code", "WHEEL_BLOCKED", "WHEEL_BLOCKED", "WHEEL_BLOCKED"),
        ("0.1.errors", "WHEEL_BLOCKED", "WHEEL_BLOCKED", "WHEEL_BLOCKED"),
        (
            "errors.navigation",
            '{"code":"X","message":"bad"}',
            '{"code":"X","message":"bad"}',
            {"code": "X", "message": "bad"},
        ),
    ],
)
def test_preview_builds_nested_payload_from_example_without_persisting(
    royal_client, db_session, source, example, pattern, expected_raw
):
    response = royal_client.post(
        f"{BASE}/preview",
        json={"rule": {**RULE, "source_path": source, "example": example, "pattern": pattern}},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["matched"] is True
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert {
        key: event[key]
        for key in (
            "raw_value",
            "source_path",
            "title",
            "severity",
            "part",
            "view",
            "x",
            "y",
            "indicator",
        )
    } == {
        "raw_value": expected_raw,
        "source_path": source,
        "title": RULE["title"],
        "severity": "critical",
        "part": "front_left_wheel",
        "view": "front",
        "x": 0.25,
        "y": 0.75,
        "indicator": "point",
    }
    assert db_session.scalar(select(DiagnosticRule)) is None
    assert rule_audits(db_session) == []


def test_preview_uses_live_matching_unknown_fallback_and_ordering(royal_client, db_session):
    candidate = {**RULE, "match_kind": "regex", "pattern": "^WHEEL_", "sort_order": 3}
    rule = insert_rule(db_session, **candidate)
    payload = {
        "errors": {
            "navigation": ["WHEEL_BLOCKED", "UNKNOWN", {"code": "OTHER", "detail": "untouched"}]
        }
    }
    live = [
        event.model_dump(exclude={"id", "rule_id"})
        for event in match_diagnostic_events(db_session, payload)
    ]
    response = royal_client.post(f"{BASE}/preview", json={"rule": candidate, "payload": payload})
    assert response.status_code == 200
    preview = response.json()
    assert preview["matched"] is True
    assert [
        {key: value for key, value in event.items() if key not in {"id", "rule_id"}}
        for event in preview["events"]
    ] == live
    assert [event["raw_value"] for event in preview["events"]] == [
        "WHEEL_BLOCKED",
        "UNKNOWN",
        {"code": "OTHER", "detail": "untouched"},
    ]
    assert len(list(db_session.scalars(select(DiagnosticRule)))) == 1
    assert db_session.get(DiagnosticRule, rule.id).pattern == "^WHEEL_"
    assert rule_audits(db_session) == []


@pytest.mark.parametrize("changes", [{"pattern": "NO_MATCH"}, {"is_enabled": False}])
def test_preview_no_match_keeps_raw_fault_unlocalized(royal_client, changes):
    response = royal_client.post(f"{BASE}/preview", json={"rule": {**RULE, **changes}})
    assert response.status_code == 200
    assert response.json()["matched"] is False
    event = response.json()["events"][0]
    assert event["raw_value"] == "WHEEL_BLOCKED"
    assert event["rule_id"] is None
    assert event["part"] is None
    assert event["view"] is None


def test_preview_regex_timeout_keeps_raw_fault_visible(royal_client):
    raw = "a" * 3000 + "!"
    response = royal_client.post(
        f"{BASE}/preview",
        json={
            "rule": {**RULE, "match_kind": "regex", "pattern": "(a+)+$"},
            "payload": {"errors": {"navigation": raw}},
        },
    )
    assert response.status_code == 200
    assert response.json()["matched"] is False
    assert response.json()["events"][0]["raw_value"] == raw


@pytest.mark.parametrize(
    "changes",
    [
        {"source_path": "errors.__class__"},
        {"source_path": "errors.999999999999999999"},
        {"match_kind": "regex", "pattern": "["},
        {"match_kind": "regex", "pattern": "(?R)"},
    ],
)
def test_preview_rejects_invalid_or_unconstructable_rules(royal_client, db_session, changes):
    response = royal_client.post(f"{BASE}/preview", json={"rule": {**RULE, **changes}})
    assert response.status_code == 422
    assert db_session.scalar(select(DiagnosticRule)) is None
    assert rule_audits(db_session) == []


def test_mutation_audits_contain_only_actor_rule_and_structural_metadata(
    royal_client, db_session, seed_royal, caplog
):
    caplog.set_level(logging.INFO)
    secret = "private-example-token-123"
    candidate = {
        **RULE,
        "example": secret,
        "pattern": secret,
        "description": secret,
        "title": secret,
        "part": secret,
        "source_path": f"errors.{secret}",
    }
    created = royal_client.post(BASE, json=candidate)
    assert created.status_code == 201
    rule_id = created.json()["id"]
    assert (
        royal_client.patch(
            f"{BASE}/{rule_id}", json={"example": secret + "-changed", "x": 0.8}
        ).status_code
        == 200
    )
    assert (
        royal_client.post(
            f"{BASE}/preview", json={"rule": candidate, "payload": {"errors": {secret: secret}}}
        ).status_code
        == 200
    )
    assert royal_client.post(f"{BASE}/{rule_id}/disable").status_code == 200
    listed = royal_client.get(BASE)
    assert (
        royal_client.put(
            f"{BASE}/reorder", json={"ids": [rule_id]}, headers={"If-Match": listed.headers["etag"]}
        ).status_code
        == 200
    )
    entries = rule_audits(db_session)
    assert [entry.action for entry in entries] == [
        "admin.diagnostic_rule.created",
        "admin.diagnostic_rule.updated",
        "admin.diagnostic_rule.disabled",
        "admin.diagnostic_rule.reordered",
    ]
    for entry in entries:
        assert entry.actor_user_id == seed_royal.id
        assert entry.actor_role == "royal"
        assert entry.target_id == str(rule_id)
        assert entry.outcome == "success"
        assert entry.park_id is None
        detail = json.loads(entry.detail)
        assert set(detail) <= {"fields", "sort_order", "previous_sort_order", "is_enabled"}
        assert secret not in entry.detail
    assert secret not in caplog.text


def test_invalid_regex_never_echoes_sensitive_pattern_or_logs_it(royal_client, caplog):
    secret = "sensitive-cookie-content"
    response = royal_client.post(
        BASE, json={**RULE, "match_kind": "regex", "pattern": secret + "["}
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "invalid_diagnostic_regex"
    assert secret not in response.text
    assert secret not in caplog.text


def test_browser_can_read_and_send_catalog_preconditions(royal_client, test_settings):
    origin = test_settings.cors_origins.split(",")[0]
    preflight = royal_client.options(
        f"{BASE}/reorder",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type,if-match",
        },
    )
    assert preflight.status_code == 200
    listed = royal_client.get(BASE, headers={"Origin": origin})
    assert listed.status_code == 200
    assert "etag" in listed.headers["access-control-expose-headers"].lower()
    assert listed.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("sort_order", [2**63, -(2**63) - 1])
def test_extreme_sort_orders_are_validation_errors(royal_client, db_session, sort_order):
    assert royal_client.post(BASE, json={**RULE, "sort_order": sort_order}).status_code == 422
    rule = insert_rule(db_session)
    assert (
        royal_client.patch(f"{BASE}/{rule.id}", json={"sort_order": sort_order}).status_code == 422
    )
    db_session.refresh(rule)
    assert rule.sort_order == 0


def test_extreme_mutation_id_is_validation_error(royal_client):
    assert royal_client.patch(f"{BASE}/{2**63}", json={"title": "Invalid"}).status_code == 422
    assert royal_client.post(f"{BASE}/{2**63}/disable").status_code == 422


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", BASE, [PRIVATE_INPUT]),
        ("POST", BASE, {**RULE, "x": PRIVATE_INPUT}),
        ("POST", BASE, {**RULE, "example": {"raw": PRIVATE_INPUT}}),
        ("POST", BASE, {**RULE, "example": None}),
        ("PATCH", f"{BASE}/1", {"title": None, "example": PRIVATE_INPUT, "pattern": PRIVATE_INPUT}),
        ("PATCH", f"{BASE}/1", {"x": PRIVATE_INPUT}),
        ("POST", f"{BASE}/preview", {"rule": {**RULE, "x": PRIVATE_INPUT}}),
        ("POST", f"{BASE}/preview", {"rule": RULE, "payload": [PRIVATE_INPUT]}),
        ("POST", f"{BASE}/preview", {"rule": RULE, PRIVATE_INPUT: "unknown field"}),
        ("PUT", f"{BASE}/reorder", {"ids": [PRIVATE_INPUT]}),
        ("PUT", f"{BASE}/reorder", {"ids": [], PRIVATE_INPUT: "unknown field"}),
        ("POST", BASE, {**RULE, "match_kind": "regex", "pattern": PRIVATE_INPUT * 20}),
        ("PATCH", f"{BASE}/1", {"match_kind": "regex", "pattern": PRIVATE_INPUT * 20}),
        (
            "POST",
            f"{BASE}/preview",
            {"rule": {**RULE, "match_kind": "regex", "pattern": PRIVATE_INPUT * 20}},
        ),
        (
            "POST",
            BASE,
            {**RULE, "match_kind": "regex", "pattern": f"(?# {PRIVATE_INPUT})a{{1001}}"},
        ),
        ("PATCH", f"{BASE}/1", {"match_kind": "regex", "pattern": f"(?# {PRIVATE_INPUT})(?R)"}),
        (
            "POST",
            f"{BASE}/preview",
            {"rule": {**RULE, "match_kind": "regex", "pattern": f"(?# {PRIVATE_INPUT})(?R)"}},
        ),
    ],
)
def test_validation_responses_and_logs_do_not_echo_diagnostic_input(
    royal_client, db_session, caplog, method, path, body
):
    insert_rule(db_session)
    caplog.set_level(logging.INFO)
    response = royal_client.request(method, path, json=body)
    assert response.status_code == 422
    assert PRIVATE_INPUT not in response.text
    detail = response.json()["detail"]
    if isinstance(detail, list):
        assert detail
        for error in detail:
            assert set(error) == {"loc", "msg", "type"}
            assert error["loc"][0] in {"body", "path", "query", "header"}
            assert error["msg"]
            assert error["type"]
    assert PRIVATE_INPUT not in caplog.text
    assert rule_audits(db_session) == []


def test_invalid_diagnostic_json_excludes_decoder_context(royal_client, caplog):
    response = royal_client.post(
        f"{BASE}/preview",
        content='{"rule": "' + PRIVATE_INPUT + '" trailing}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    error = response.json()["detail"][0]
    assert set(error) == {"loc", "msg", "type"}
    assert error["type"] == "json_invalid"
    assert PRIVATE_INPUT not in response.text
    assert PRIVATE_INPUT not in caplog.text


def test_diagnostic_error_sanitizing_keeps_other_route_contracts(royal_client):
    response = royal_client.post("/admin/emergency/sections", json=[PRIVATE_INPUT])
    assert response.status_code == 422
    assert response.json()["detail"][0]["input"] == [PRIVATE_INPUT]


@pytest.mark.parametrize("precondition", ["absent", "fresh", "stale"])
@pytest.mark.parametrize("sort_order", [0, 1, None, PRIVATE_INPUT])
def test_patch_explicit_sort_order_always_requires_reorder(
    royal_client, db_session, caplog, precondition, sort_order
):
    rule = insert_rule(db_session)
    listed = royal_client.get(BASE)
    headers = {}
    if precondition != "absent":
        headers["If-Match"] = listed.headers["etag"] if precondition == "fresh" else '"old"'
    response = royal_client.patch(
        f"{BASE}/{rule.id}",
        json={"sort_order": sort_order, "title": "Must not be saved", "example": PRIVATE_INPUT},
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["msg"] == "Value error, diagnostic_sort_order_use_reorder"
    assert PRIVATE_INPUT not in response.text
    assert PRIVATE_INPUT not in caplog.text
    assert royal_client.get(BASE).json() == listed.json()
    assert rule_audits(db_session) == []


def test_stale_editor_cannot_undo_atomic_reorder_via_patch(royal_client, db_session):
    first = insert_rule(db_session, pattern="A", sort_order=0)
    second = insert_rule(db_session, pattern="B", sort_order=1, is_enabled=False)
    editor_a_snapshot = royal_client.get(BASE)
    stale_header = {"If-Match": editor_a_snapshot.headers["etag"]}
    editor_b_reorder = royal_client.put(
        f"{BASE}/reorder", json={"ids": [second.id, first.id]}, headers=stale_header
    )
    assert editor_b_reorder.status_code == 200
    assert [(rule["id"], rule["sort_order"]) for rule in editor_b_reorder.json()] == [
        (second.id, 0),
        (first.id, 1),
    ]
    stale_save = royal_client.patch(
        f"{BASE}/{first.id}", json={"title": "Editor A", "sort_order": 0}, headers=stale_header
    )
    assert stale_save.status_code == 422
    assert royal_client.get(BASE).json() == editor_b_reorder.json()
    regular_save = royal_client.patch(
        f"{BASE}/{first.id}", json={"title": "Editor A"}, headers=stale_header
    )
    assert regular_save.status_code == 200
    assert regular_save.json()["title"] == "Editor A"
    assert regular_save.json()["sort_order"] == 1
    assert (
        royal_client.put(
            f"{BASE}/reorder", json={"ids": [first.id, second.id]}, headers=stale_header
        ).status_code
        == 409
    )
    latest = royal_client.get(BASE)
    reordered = royal_client.put(
        f"{BASE}/reorder",
        json={"ids": [first.id, second.id]},
        headers={"If-Match": latest.headers["etag"]},
    )
    assert reordered.status_code == 200
    assert [(rule["id"], rule["sort_order"]) for rule in reordered.json()] == [
        (first.id, 0),
        (second.id, 1),
    ]
    assert reordered.json()[1]["is_enabled"] is False
