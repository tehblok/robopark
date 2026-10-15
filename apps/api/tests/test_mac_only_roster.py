"""Roster and VIN scope keep working when only an owner's Mac has Tracker."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from conftest import login_as, role_id_for
from robopark_api.models import AccessStatus, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services import (
    emergency_scope,
    platform_settings,
    rbac,
    robot_registry,
    tracker_client,
    tracker_gateway_transport,
)
from robopark_api.services.tracker_gateway_transport import GatewayCredential


def _approved_operator(db_session, park):
    user = User(
        username="mac-owner",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, rbac.RoleSlug.OPERATOR),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(UserPark(user_id=user.id, park_id=park.id))
    db_session.commit()
    db_session.refresh(user)
    return user


def _enable_mac_only_tracker(monkeypatch):
    settings = SimpleNamespace(
        robopark_environment="test",
        mac_corporate_gateway_enabled=True,
        mac_corporate_server_fallback_enabled=True,
        emergency_bridge_enabled=True,
    )
    monkeypatch.setattr(tracker_gateway_transport, "get_settings", lambda: settings)
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda _db: None)


def test_registry_uses_requester_mac_when_server_has_no_tracker_token(
    db_session, seed_park_with_tracker, monkeypatch
):
    owner = _approved_operator(db_session, seed_park_with_tracker)
    _enable_mac_only_tracker(monkeypatch)
    seen = []

    def search_issues(*, token, **_kwargs):
        seen.append(token)
        return [
            {
                "key": "ROBOPARK-1",
                "queue": seed_park_with_tracker.tracker_queue,
                "tags": [seed_park_with_tracker.tag],
                "robot": "447",
            }
        ]

    monkeypatch.setattr(tracker_client, "search_issues", search_issues)

    result = robot_registry.registry_rows(
        db_session,
        owner,
        park_id=None,
        query="",
        state="all",
        active_errors=False,
        open_tasks=False,
        offset=0,
        limit=1,
    )

    assert result["items"][0]["vin"] == "YASADR00000000447"
    assert len(seen) == 1
    assert isinstance(seen[0], GatewayCredential)
    assert seen[0].requester_user_id == owner.id
    assert seen[0].server_token is None


def test_probe_target_uses_mac_only_authorized_registry(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    owner = _approved_operator(db_session, seed_park_with_tracker)
    _enable_mac_only_tracker(monkeypatch)
    monkeypatch.setattr(
        tracker_client,
        "search_issue_page",
        lambda **_kwargs: [
            {
                "key": "ROBOPARK-1",
                "queue": seed_park_with_tracker.tracker_queue,
                "tags": [seed_park_with_tracker.tag],
                "robot": "447",
            }
        ],
    )
    login_as(client, owner.username, "secret")

    response = client.get("/agent-devices/probe-target")

    assert response.status_code == 200
    assert response.json() == {"vin": "YASADR00000000447"}


def test_vin_scope_uses_requester_mac_when_server_has_no_tracker_token(
    db_session, seed_park_with_tracker, monkeypatch
):
    owner = _approved_operator(db_session, seed_park_with_tracker)
    _enable_mac_only_tracker(monkeypatch)
    seen = []
    issue = {
        "key": "ROBOPARK-1",
        "queue": seed_park_with_tracker.tracker_queue,
        "summary": "[447] brake",
        "tags": [seed_park_with_tracker.tag],
    }

    def search_robot_tickets(*, token, **_kwargs):
        seen.append(token)
        return [issue]

    monkeypatch.setattr(tracker_client, "search_robot_tickets", search_robot_tickets)

    assert emergency_scope.vin_allowed_for_user(db_session, owner, "YASADR00000000447")
    assert emergency_scope.vin_allowed_for_park(
        db_session, owner, "YASADR00000000447", seed_park_with_tracker.id
    )
    assert len(seen) == 2
    assert all(isinstance(token, GatewayCredential) for token in seen)
    assert all(token.requester_user_id == owner.id for token in seen)
    assert all(token.server_token is None for token in seen)


def test_vin_scope_denies_requester_without_tracker_gateway_permission_without_search(
    db_session, seed_park_with_tracker, monkeypatch
):
    owner = _approved_operator(db_session, seed_park_with_tracker)
    rbac.set_user_effective_permissions(
        db_session,
        owner,
        [rbac.PERMISSION_NAV_EMERGENCY],
    )
    db_session.commit()
    _enable_mac_only_tracker(monkeypatch)
    monkeypatch.setattr(
        tracker_client,
        "search_robot_tickets",
        lambda **_kwargs: pytest.fail("denied requester must not reach Tracker"),
    )

    with pytest.raises(HTTPException) as denied:
        emergency_scope.vin_allowed_for_user(db_session, owner, "YASADR00000000447")

    assert denied.value.status_code == 403
    assert denied.value.detail == "corporate_access_denied"


def test_feature_off_roster_keeps_plain_server_service_identity(
    db_session, seed_park_with_tracker, monkeypatch
):
    owner = _approved_operator(db_session, seed_park_with_tracker)
    monkeypatch.setattr(tracker_gateway_transport, "enabled", lambda: False)
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda _db: "bot-token")
    seen = []

    def search_issues(*, token, **_kwargs):
        seen.append(token)
        return []

    monkeypatch.setattr(tracker_client, "search_issues", search_issues)

    result = robot_registry.registry_rows(
        db_session,
        owner,
        park_id=None,
        query="",
        state="all",
        active_errors=False,
        open_tasks=False,
        offset=0,
        limit=1,
    )

    assert result["items"] == []
    assert seen == ["bot-token"]
