"""Stored-sample evaluations are bounded, private and read-only."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, select

from conftest import login_as, role_id_for
from robopark_api.models import AuditLog, DiagnosticRule, DiagnosticUnknown, User
from robopark_api.security import hash_password
from test_admin_diagnostic_rules import RULE

BASE = "/admin/diagnostic-rules/test-samples"


@pytest.fixture
def royal_client(client, seed_royal):
    assert login_as(client, "royal", "secret").status_code == 204
    return client


def sample(db, identity="a", original="WHEEL_BLOCKED", **changes):
    now = datetime.now(UTC)
    row = DiagnosticUnknown(
        identity=identity,
        source_path="errors.navigation",
        source_segments_json='["errors","navigation",0]',
        raw_json='"RESIDUAL"',
        original_json=json.dumps(original),
        first_seen_at=now,
        last_seen_at=now,
        observations=1,
        last_robot="private-robot",
        state="new",
    )
    for key, value in changes.items():
        setattr(row, key, value)
    db.add(row)
    db.commit()
    return row


@pytest.mark.parametrize("role", ["admin", "royal", "operator", "mechanic", "driver"])
def test_sample_evaluation_role_boundary(client, db_session, role):
    assert client.post(BASE, json={"rule": RULE}).status_code == 401
    db_session.add(
        User(
            username="tester",
            password_hash=hash_password("secret"),
            role_id=role_id_for(db_session, role),
            access_status="approved",
            is_active=True,
        )
    )
    db_session.commit()
    login_as(client, "tester", "secret")
    assert client.post(BASE, json={"rule": RULE}).status_code == (
        200 if role in {"admin", "royal"} else 403
    )


def test_originals_overlap_and_read_only_privacy(royal_client, db_session, db_engine, monkeypatch):
    from robopark_api.routers import emergency

    monkeypatch.setattr(emergency, "_get_robot_payload", lambda *a: pytest.fail("upstream call"))
    hit = sample(db_session)
    miss = sample(db_session, "b", "OTHER", state="ignored")
    existing = DiagnosticRule(**{**RULE, "title": "private-rule-title"})
    disabled = DiagnosticRule(
        **{**RULE, "pattern": "^WHEEL", "match_kind": "regex", "is_enabled": False}
    )
    db_session.add_all([existing, disabled])
    db_session.commit()
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.strip().split()[0].upper())

    event.listen(db_engine, "before_cursor_execute", record)
    try:
        response = royal_client.post(BASE, json={"rule": RULE})
    finally:
        event.remove(db_engine, "before_cursor_execute", record)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert (data["matched"], data["missed"], data["skipped"], data["overlapping"]) == (1, 1, 0, 1)
    assert data["items"] == [
        {"id": miss.id, "outcome": "missed", "overlap_rule_ids": [], "reason": None},
        {"id": hit.id, "outcome": "matched", "overlap_rule_ids": [existing.id], "reason": None},
    ]
    assert not {"INSERT", "UPDATE", "DELETE"}.intersection(statements)
    assert (
        db_session.scalar(select(AuditLog).where(AuditLog.target_type == "diagnostic_rule")) is None
    )
    for private in [
        "WHEEL_BLOCKED",
        "RESIDUAL",
        "OTHER",
        "private-robot",
        "private-rule-title",
        "errors.navigation",
    ]:
        assert private not in response.text
    assert (
        royal_client.post(BASE, json={"rule": RULE, "exclude_rule_id": existing.id}).json()[
            "overlapping"
        ]
        == 0
    )
    assert (
        royal_client.post(BASE, json={"rule": {**RULE, "is_enabled": False}}).json()["matched"] == 0
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"original_json": None},
        {"original_json": "{bad"},
        {"original_json": "NaN"},
        {"source_segments_json": "{}"},
        {"source_segments_json": "[true]"},
        {"source_segments_json": '["errors",-1]'},
        {"source_segments_json": '["errors",1000000]'},
        {"source_segments_json": "[0]"},
        {"source_segments_json": "[]"},
        {"source_segments_json": '["errors", {}]'},
        {"original_json": '"' + "x" * 9000 + '"'},
        {"original_json": json.dumps({"token": "private-secret"})},
        {"original_json": json.dumps("Bearer private-secret")},
        {"source_segments_json": '["token"]'},
    ],
)
def test_skips_unusable_samples_without_leaking(royal_client, db_session, changes):
    row = sample(db_session, **changes)
    result = royal_client.post(BASE, json={"rule": RULE})
    assert result.status_code == 200
    assert result.json()["skipped"] == 1
    assert result.json()["matched"] == result.json()["missed"] == 0
    assert result.json()["items"][0]["id"] == row.id
    assert "private-secret" not in result.text


def test_original_scalar_is_not_reparsed_and_wrappers_cannot_match(royal_client, db_session):
    sample(db_session, original='{"code":"X"}')
    exact = {**RULE, "pattern": '{"code":"X"}'}
    assert royal_client.post(BASE, json={"rule": exact}).json()["matched"] == 1
    wrapper = {
        **RULE,
        "source_path": "errors",
        "pattern": '{"navigation":["{\\"code\\":\\"X\\"}"]}',
    }
    assert royal_client.post(BASE, json={"rule": wrapper}).json()["matched"] == 0


@pytest.mark.parametrize(
    "extra",
    [{"limit": 0}, {"limit": 101}, {"limit": True}, {"exclude_rule_id": -1}, {"limit": "2"}],
)
def test_request_limits_are_validated(royal_client, extra):
    assert royal_client.post(BASE, json={"rule": RULE, **extra}).status_code == 422


def test_latest_sample_limit_and_truncation(royal_client, db_session):
    sample(db_session, last_seen_at=datetime.now(UTC) - timedelta(days=1))
    recent = sample(db_session, "b")
    result = royal_client.post(BASE, json={"rule": RULE, "limit": 1}).json()
    assert [item["id"] for item in result["items"]] == [recent.id]
    assert result["has_more"] is True
    assert result["limit"] == 1


def test_budget_exhaustion_is_inconclusive_not_missed(royal_client, db_session, monkeypatch):
    from robopark_api.services import diagnostic_sample_tests as service

    sample(db_session)
    monkeypatch.setattr(service, "MAX_MATCH_OPERATIONS", 0)
    data = royal_client.post(BASE, json={"rule": RULE}).json()
    assert data["skipped"] == 1 and data["missed"] == 0
    assert data["items"][0]["reason"] == "budget"
    assert data["budget_exhausted"] is True


def test_deadline_and_inner_value_budget_stop_partial_matching(
    royal_client, db_session, monkeypatch
):
    from robopark_api.services import diagnostic_sample_tests as service

    sample(db_session, original=["a"] * 200, source_segments_json='["errors","navigation"]')
    monkeypatch.setattr(service, "MAX_MATCH_OPERATIONS", 10)
    result = royal_client.post(BASE, json={"rule": RULE}).json()
    assert result["budget_exhausted"] is True
    assert result["missed"] == 0
    monkeypatch.setattr(service, "MAX_MATCH_OPERATIONS", 2000)
    monkeypatch.setattr(service, "MAX_SECONDS", 0)
    result = royal_client.post(BASE, json={"rule": RULE}).json()
    assert result["budget_exhausted"] is True
    assert result["skipped"] == 1


def test_oversized_catalog_is_explicit_and_invalid_rules_are_reported(
    royal_client, db_session, monkeypatch
):
    from robopark_api.services import diagnostic_sample_tests as service

    sample(db_session)
    bad = DiagnosticRule(**{**RULE, "match_kind": "regex", "pattern": "[private-pattern"})
    db_session.add(bad)
    db_session.commit()
    response = royal_client.post(BASE, json={"rule": RULE})
    assert response.status_code == 200
    assert response.json()["invalid_rule_ids"] == [bad.id]
    assert "private-pattern" not in response.text
    monkeypatch.setattr(service, "MAX_CATALOG_RULES", 0)
    response = royal_client.post(BASE, json={"rule": RULE})
    assert response.status_code == 422
    assert response.json()["detail"] == "diagnostic_sample_catalog_too_large"


def test_deep_and_many_node_samples_are_inconclusive(royal_client, db_session):
    sample(db_session, original=["x"] * 257)
    deep = "x"
    for _ in range(26):
        deep = [deep]
    sample(db_session, "deep", original=deep)
    result = royal_client.post(BASE, json={"rule": RULE}).json()
    assert result["skipped"] == 2 and result["missed"] == 0


def test_invalid_candidate_does_not_echo_input(royal_client):
    response = royal_client.post(
        BASE, json={"rule": {**RULE, "match_kind": "regex", "pattern": "[private-input"}}
    )
    assert response.status_code == 422
    assert "private-input" not in response.text
    response = royal_client.post(BASE, json={"rule": {**RULE, "source_path": "private-input..x"}})
    assert response.status_code == 422
    assert "private-input" not in response.text
