"""HTTP-to-receipt checks for the web-only, partially connected topology."""

import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from starlette.background import BackgroundTasks

from conftest import role_id_for
from robopark_api.models import (
    AccessStatus,
    CorporateWriteAuthority,
    CorporateWriteReceipt,
    User,
    UserPark,
)
from robopark_api.security import hash_password
from robopark_api.services import (
    corporate_gateway_client,
    corporate_server_write,
    corporate_write_client,
    corporate_write_contract,
    platform_settings,
    tracker_client,
    tracker_gateway_transport,
)
from robopark_api.services.emergency_bridge_client import BridgeError


@pytest.mark.parametrize("server_access", ["available", "missing", "denied", "unknown"])
def test_web_comment_without_macs_pins_available_server_and_never_replays_unknown(
    client, db_engine, db_session, test_settings, seed_park_with_tracker, monkeypatch, server_access
):
    actor = User(
        username="web-only-operator",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
        is_active=True,
        tracker_login="operator.login",
    )
    db_session.add(actor)
    db_session.flush()
    db_session.add(UserPark(user_id=actor.id, park_id=seed_park_with_tracker.id))
    db_session.commit()
    settings = test_settings.model_copy(
        update={
            "robopark_environment": "test",
            "mac_corporate_gateway_enabled": True,
            "mac_corporate_server_fallback_enabled": True,
            "emergency_bridge_enabled": True,
        }
    )
    for module in (tracker_gateway_transport, corporate_write_client, corporate_gateway_client):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(corporate_write_client, "SessionLocal", sessionmaker(bind=db_engine))
    monkeypatch.setattr(BackgroundTasks, "add_task", lambda *args, **kwargs: None)
    if server_access != "missing":
        platform_settings.set_setting(
            db_session, platform_settings.TRACKER_TOKEN_KEY, "test-service-token"
        )

    def no_macs(*args, **kwargs):
        raise BridgeError("bridge_unavailable")

    monkeypatch.setattr(corporate_write_client, "_signed_post", no_macs)
    monkeypatch.setattr(
        tracker_client,
        "get_issue",
        lambda **kwargs: {
            "key": "ROBOPARK-1",
            "summary": "blocker [447]",
            "queue": "ROBOPARK",
            "status": "Open",
            "status_key": "open",
            "tags": ["Alpha"],
            "assignee": {"login": "mech1"},
        },
    )
    sent = []

    def send(**kwargs):
        sent.append(kwargs)
        if server_access == "denied":
            raise corporate_server_write.ServerWriteError(403, "corporate_access_denied")
        if server_access == "unknown":
            raise corporate_server_write.ServerWriteError(
                409, "corporate_write_outcome_unknown", outcome_unknown=True
            )
        return corporate_write_contract.CorporateWriteResponse(201, {"id": "comment-1"})

    monkeypatch.setattr(corporate_server_write, "execute", send)
    assert (
        client.post(
            "/auth/login", json={"username": actor.username, "password": "secret"}
        ).status_code
        == 204
    )
    headers = {
        "Idempotency-Key": "web-only-comment",
        "X-Tracker-State": json.dumps(
            {"status": "Open", "status_key": "open", "assignee": "mech1"}
        ),
    }
    first = client.post(
        "/tracker/issues/ROBOPARK-1/comment", json={"text": "hello"}, headers=headers
    )
    second = client.post(
        "/tracker/issues/ROBOPARK-1/comment", json={"text": "hello"}, headers=headers
    )
    if server_access == "available":
        assert first.status_code == second.status_code == 200
    else:
        assert first.status_code >= 400
        assert second.status_code >= 400
    assert len(sent) == (0 if server_access == "missing" else 1)
    if sent:
        assert sent[0]["token"] == "test-service-token"
        assert sent[0]["action"] == "comment_create"
        db_session.expire_all()
        authority = db_session.scalar(select(CorporateWriteAuthority))
        receipt = db_session.scalar(select(CorporateWriteReceipt))
        assert authority.mode == "server"
        assert authority.requester_user_id == actor.id
        assert receipt.state == ("unknown" if server_access == "unknown" else "completed")


@pytest.mark.parametrize(
    "role,path",
    [
        ("operator", "/operator/blockers?park_id={park}"),
        ("operator", "/operator/robots/447/tickets"),
        ("operator", "/operator/now-report?park_id={park}"),
        ("operator", "/dashboard/summary?park_id={park}"),
        ("operator", "/tracker/issues/ROBOPARK-1/timeline"),
        ("mechanic", "/mechanic/tasks?park_id={park}"),
        ("mechanic", "/mechanic/robots/447/tickets"),
    ],
)
def test_interactive_views_use_owner_gateway_without_server_tracker_token(
    client,
    db_session,
    test_settings,
    seed_park_with_tracker,
    seed_mechanic,
    monkeypatch,
    role,
    path,
):
    from robopark_api.services import tracker_cache, tracker_metrics

    operator = User(
        username="mac-only-operator",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status="approved",
        is_active=True,
        tracker_login="operator.login",
    )
    db_session.add(operator)
    db_session.flush()
    park = seed_park_with_tracker
    park.feature_reports = True
    db_session.add(UserPark(user_id=operator.id, park_id=park.id))
    db_session.commit()
    actor = operator if role == "operator" else seed_mechanic
    settings = test_settings.model_copy(
        update={
            "robopark_environment": "test",
            "mac_corporate_gateway_enabled": True,
            "emergency_bridge_enabled": True,
        }
    )
    monkeypatch.setattr(tracker_gateway_transport, "get_settings", lambda: settings)
    calls = []

    def read(value):
        def invoke(*, token, **kwargs):
            assert isinstance(token, tracker_gateway_transport.GatewayCredential)
            assert token.requester_user_id == actor.id
            assert token.server_token is None
            calls.append(kwargs)
            return value

        return invoke

    for name in ("fetch_park_blockers", "search_robot_tickets", "list_comments"):
        monkeypatch.setattr(tracker_cache, name, read([]))
    monkeypatch.setattr(tracker_cache, "collect_park_metrics", read({}))
    monkeypatch.setattr(
        tracker_cache,
        "get_issue",
        read(
            {
                "key": "ROBOPARK-1",
                "summary": "blocker [447]",
                "queue": "ROBOPARK",
                "status": "Open",
                "status_key": "open",
                "tags": ["Alpha"],
                "assignee": {"login": "mech1"},
            }
        ),
    )
    # An old service-account aggregate must not bypass fresh owner access.
    monkeypatch.setattr(
        tracker_metrics,
        "get_cached_now_report",
        lambda *args: pytest.fail("service aggregate cache used for owner read"),
    )
    monkeypatch.setattr(
        tracker_metrics,
        "set_cached_now_report",
        lambda *args: pytest.fail("owner data stored in service aggregate cache"),
    )
    assert platform_settings.get_tracker_token(db_session) is None
    assert (
        client.post(
            "/auth/login", json={"username": actor.username, "password": "secret"}
        ).status_code
        == 204
    )
    response = client.get(path.format(park=park.id))
    assert response.status_code == 200, response.text
    assert calls
