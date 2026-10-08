import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from conftest import login_as, role_id_for
from robopark_api.models import (
    AccessStatus,
    AuditLog,
    AuthSession,
    NativeBotJob,
    Park,
    ParkRequest,
    TelegramAccount,
    User,
    UserPark,
)
from robopark_api.security import hash_password
from robopark_api.services import bot_shared_settings, native_telegram_migration, rbac

BOT_HEADERS = {"X-Robopark-Bot-Key": "test-bridge"}


def _allow_bot(monkeypatch):
    monkeypatch.setattr(
        "robopark_api.services.bot_tracker_gateway.bridge_key_matches",
        lambda value: value == "test-bridge",
    )
    monkeypatch.setattr(
        "robopark_api.services.native_telegram.bot_settings.desired_enabled",
        lambda _path: True,
    )


def _job(park_id: int, **overrides):
    payload = {
        "park_id": park_id,
        "kind": "text",
        "title": "Morning",
        "enabled": False,
        "schedule": "daily",
        "time": "09:00",
        "weekdays": [0, 1, 2, 3, 4, 5, 6],
        "start_hour": None,
        "end_hour": None,
        "text": "Hello",
        "url": None,
        "tracker_tag": None,
        "alternate": "all",
        "anchor_date": None,
    }
    payload.update(overrides)
    return payload


def test_telegram_onboarding_lists_only_active_public_park_fields(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    _allow_bot(monkeypatch)
    inactive = Park(name="Inactive", tag="inactive-onboarding", is_active=False)
    db_session.add(inactive)
    db_session.commit()

    assert client.get("/internal/bot/native/onboarding/parks").status_code == 401
    response = client.get("/internal/bot/native/onboarding/parks", headers=BOT_HEADERS)

    assert response.status_code == 200
    assert response.json() == {
        "parks": [{"id": seed_park_with_tracker.id, "name": seed_park_with_tracker.name}]
    }


def test_telegram_onboarding_creates_pending_identity_without_web_session(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    _allow_bot(monkeypatch)
    existing = User(
        username="telegram_nick",
        password_hash=hash_password("unused"),
        role_id=role_id_for(db_session, rbac.RoleSlug.OPERATOR),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(existing)
    db_session.commit()

    response = client.post(
        "/internal/bot/native/onboarding/request",
        headers=BOT_HEADERS,
        json={
            "telegram_user_id": 7001,
            "role": "mechanic",
            "park_id": seed_park_with_tracker.id,
            "display_name": "Иван Механик",
            "telegram_username": "telegram_nick",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "state": "pending",
        "user_id": body["user_id"],
        "request_id": body["request_id"],
        "role": "mechanic",
        "park_id": seed_park_with_tracker.id,
        "display_name": "Иван Механик",
        "notify_admin_ids": [],
    }
    account = db_session.scalar(
        select(TelegramAccount).where(TelegramAccount.telegram_user_id == 7001)
    )
    assert account is not None
    assert account.display_name == "Иван Механик"
    assert account.telegram_username == "telegram_nick"
    created = db_session.get(User, account.user_id)
    assert created is not None
    assert created.username.startswith("tg_telegram_nick_")
    assert created.username != existing.username
    assert created.role == rbac.RoleSlug.MECHANIC
    assert created.access_status == AccessStatus.pending.value
    assert db_session.query(AuthSession).filter_by(user_id=body["user_id"]).count() == 0
    request = db_session.get(ParkRequest, body["request_id"])
    assert request is not None
    assert (request.user_id, request.park_id, request.status) == (
        created.id,
        seed_park_with_tracker.id,
        AccessStatus.pending.value,
    )


def test_telegram_onboarding_repeat_uses_linked_identity_without_overwrite(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    _allow_bot(monkeypatch)
    payload = {
        "telegram_user_id": 7002,
        "role": "mechanic",
        "park_id": seed_park_with_tracker.id,
        "display_name": "First",
    }
    first = client.post(
        "/internal/bot/native/onboarding/request", headers=BOT_HEADERS, json=payload
    ).json()

    repeated = client.post(
        "/internal/bot/native/onboarding/request",
        headers=BOT_HEADERS,
        json={**payload, "role": "operator", "display_name": "Second"},
    )

    assert repeated.status_code == 200
    assert repeated.json() == {**first, "display_name": "Second"}
    user = db_session.get(User, first["user_id"])
    assert user is not None and user.role == rbac.RoleSlug.MECHANIC
    account = db_session.get(TelegramAccount, user.id)
    assert account is not None
    assert account.display_name == "First"
    assert account.telegram_username is None
    assert db_session.query(TelegramAccount).filter_by(telegram_user_id=7002).count() == 1
    assert db_session.query(ParkRequest).filter_by(user_id=user.id).count() == 1
    assert db_session.query(UserPark).filter_by(user_id=user.id).count() == 0


def test_new_onboarding_request_returns_scoped_admin_notification_ids_once(
    client,
    db_session,
    seed_admin,
    seed_royal,
    seed_park_with_tracker,
    monkeypatch,
):
    _allow_bot(monkeypatch)
    foreign_park = Park(name="Foreign notify", tag="foreign-notify", is_active=True)
    foreign_admin = User(
        username="foreign-notify-admin",
        password_hash=hash_password("unused"),
        role_id=role_id_for(db_session, rbac.RoleSlug.ADMIN),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add_all([foreign_park, foreign_admin])
    db_session.flush()
    db_session.add_all(
        [
            UserPark(user_id=seed_admin.id, park_id=seed_park_with_tracker.id),
            UserPark(user_id=foreign_admin.id, park_id=foreign_park.id),
            TelegramAccount(user_id=seed_admin.id, telegram_user_id=7101),
            TelegramAccount(user_id=seed_royal.id, telegram_user_id=7102),
            TelegramAccount(user_id=foreign_admin.id, telegram_user_id=7103),
        ]
    )
    db_session.commit()
    payload = {
        "telegram_user_id": 7104,
        "role": "operator",
        "park_id": seed_park_with_tracker.id,
        "display_name": "New operator",
    }

    created = client.post(
        "/internal/bot/native/onboarding/request", headers=BOT_HEADERS, json=payload
    )
    repeated = client.post(
        "/internal/bot/native/onboarding/request", headers=BOT_HEADERS, json=payload
    )

    assert created.status_code == 200
    assert created.json()["notify_admin_ids"] == [7101, 7102]
    assert repeated.status_code == 200
    assert repeated.json()["notify_admin_ids"] == []


def test_manager_lists_and_updates_only_scoped_telegram_user_memberships(
    client,
    db_session,
    seed_admin,
    seed_park_with_tracker,
    monkeypatch,
):
    _allow_bot(monkeypatch)
    foreign_park = Park(name="Foreign membership", tag="foreign-membership", is_active=True)
    mechanic = User(
        username="managed-telegram-mechanic",
        password_hash=hash_password("unused"),
        role_id=role_id_for(db_session, rbac.RoleSlug.MECHANIC),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    privileged = User(
        username="managed-telegram-admin",
        password_hash=hash_password("unused"),
        role_id=role_id_for(db_session, rbac.RoleSlug.ADMIN),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add_all([foreign_park, mechanic, privileged])
    db_session.flush()
    db_session.add_all(
        [
            UserPark(user_id=seed_admin.id, park_id=seed_park_with_tracker.id),
            UserPark(user_id=mechanic.id, park_id=seed_park_with_tracker.id),
            UserPark(user_id=mechanic.id, park_id=foreign_park.id),
            UserPark(user_id=privileged.id, park_id=seed_park_with_tracker.id),
            TelegramAccount(
                user_id=mechanic.id,
                telegram_user_id=7110,
                display_name="Managed Mechanic",
                telegram_username="managed_mechanic",
            ),
            TelegramAccount(user_id=privileged.id, telegram_user_id=7111),
            TelegramAccount(user_id=seed_admin.id, telegram_user_id=7112),
        ]
    )
    db_session.commit()

    listed = client.get(
        "/internal/bot/native/manage/users",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 7112},
    )

    assert listed.status_code == 200
    managed = next(item for item in listed.json() if item["user_id"] == mechanic.id)
    assert managed["display_name"] == "Managed Mechanic"
    assert managed["telegram_username"] == "managed_mechanic"
    assert managed["parks"] == [
        {"id": seed_park_with_tracker.id, "name": seed_park_with_tracker.name}
    ]

    forbidden_park = client.request(
        "DELETE",
        f"/internal/bot/native/manage/users/{mechanic.id}/parks/{foreign_park.id}",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 7112},
        json={"expected_park_ids": [seed_park_with_tracker.id]},
    )
    assert forbidden_park.status_code == 403
    forbidden_role = client.request(
        "DELETE",
        f"/internal/bot/native/manage/users/{privileged.id}/parks/{seed_park_with_tracker.id}",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 7112},
        json={"expected_park_ids": [seed_park_with_tracker.id]},
    )
    assert forbidden_role.status_code == 403

    removed = client.request(
        "DELETE",
        f"/internal/bot/native/manage/users/{mechanic.id}/parks/{seed_park_with_tracker.id}",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 7112},
        json={"expected_park_ids": [seed_park_with_tracker.id]},
    )
    assert removed.status_code == 200
    assert removed.json()["parks"] == []
    assert db_session.get(UserPark, (mechanic.id, foreign_park.id)) is not None

    stale = client.put(
        f"/internal/bot/native/manage/users/{mechanic.id}/parks/{seed_park_with_tracker.id}",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 7112},
        json={"expected_park_ids": [seed_park_with_tracker.id]},
    )
    assert stale.status_code == 409
    restored = client.put(
        f"/internal/bot/native/manage/users/{mechanic.id}/parks/{seed_park_with_tracker.id}",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 7112},
        json={"expected_park_ids": []},
    )
    assert restored.status_code == 200
    assert restored.json()["parks"] == [
        {"id": seed_park_with_tracker.id, "name": seed_park_with_tracker.name}
    ]


def test_telegram_onboarding_returns_active_for_existing_park_member(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    _allow_bot(monkeypatch)
    db_session.add(TelegramAccount(user_id=seed_mechanic.id, telegram_user_id=7006))
    db_session.commit()

    response = client.post(
        "/internal/bot/native/onboarding/request",
        headers=BOT_HEADERS,
        json={
            "telegram_user_id": 7006,
            "role": "operator",
            "park_id": seed_park_with_tracker.id,
        },
    )

    assert response.status_code == 200
    assert response.json()["state"] == "active"
    assert response.json()["role"] == "mechanic"
    assert response.json()["request_id"] is None
    db_session.refresh(seed_mechanic)
    assert seed_mechanic.role == rbac.RoleSlug.MECHANIC
    assert db_session.query(UserPark).filter_by(user_id=seed_mechanic.id).count() == 1


def test_telegram_onboarding_does_not_reopen_rejected_park_request(
    client, db_session, seed_mechanic, monkeypatch
):
    _allow_bot(monkeypatch)
    park = Park(name="Rejected onboarding", tag="rejected-onboarding", is_active=True)
    db_session.add(park)
    db_session.flush()
    rejected = ParkRequest(
        user_id=seed_mechanic.id,
        park_id=park.id,
        status=AccessStatus.rejected.value,
    )
    db_session.add_all([rejected, TelegramAccount(user_id=seed_mechanic.id, telegram_user_id=7007)])
    db_session.commit()

    response = client.post(
        "/internal/bot/native/onboarding/request",
        headers=BOT_HEADERS,
        json={
            "telegram_user_id": 7007,
            "role": "mechanic",
            "park_id": park.id,
        },
    )

    assert response.status_code == 200
    assert response.json()["state"] == "rejected"
    assert response.json()["request_id"] == rejected.id
    assert db_session.query(ParkRequest).filter_by(user_id=seed_mechanic.id).count() == 1


def test_telegram_onboarding_treats_approved_request_without_membership_as_blocked(
    client, db_session, seed_mechanic, monkeypatch
):
    _allow_bot(monkeypatch)
    park = Park(name="Revoked onboarding", tag="revoked-onboarding", is_active=True)
    db_session.add(park)
    db_session.flush()
    approved = ParkRequest(
        user_id=seed_mechanic.id,
        park_id=park.id,
        status=AccessStatus.approved.value,
    )
    db_session.add_all([approved, TelegramAccount(user_id=seed_mechanic.id, telegram_user_id=7010)])
    db_session.commit()

    response = client.post(
        "/internal/bot/native/onboarding/request",
        headers=BOT_HEADERS,
        json={
            "telegram_user_id": 7010,
            "role": "mechanic",
            "park_id": park.id,
        },
    )

    assert response.status_code == 200
    assert response.json()["state"] == "blocked"
    assert response.json()["request_id"] is None
    assert db_session.get(UserPark, (seed_mechanic.id, park.id)) is None
    assert db_session.query(ParkRequest).filter_by(user_id=seed_mechanic.id).count() == 1


def test_telegram_onboarding_uses_numeric_fallback_for_unusable_telegram_username(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    _allow_bot(monkeypatch)

    response = client.post(
        "/internal/bot/native/onboarding/request",
        headers=BOT_HEADERS,
        json={
            "telegram_user_id": 7011,
            "role": "operator",
            "park_id": seed_park_with_tracker.id,
            "telegram_username": "админ",
        },
    )

    assert response.status_code == 200
    user = db_session.get(User, response.json()["user_id"])
    assert user is not None
    assert user.username.startswith("tg_7011_")
    assert "админ" not in user.username


@pytest.mark.parametrize(
    ("access_status", "is_active", "expected"),
    [
        (AccessStatus.rejected.value, True, "rejected"),
        (AccessStatus.pending.value, False, "blocked"),
    ],
)
def test_telegram_onboarding_does_not_reopen_denied_identity(
    client,
    db_session,
    seed_park_with_tracker,
    monkeypatch,
    access_status,
    is_active,
    expected,
):
    _allow_bot(monkeypatch)
    user = User(
        username=f"onboarding-{expected}",
        password_hash=hash_password("unused"),
        role_id=role_id_for(db_session, rbac.RoleSlug.OPERATOR),
        access_status=access_status,
        is_active=is_active,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(TelegramAccount(user_id=user.id, telegram_user_id=7003 if is_active else 7004))
    db_session.commit()

    response = client.post(
        "/internal/bot/native/onboarding/request",
        headers=BOT_HEADERS,
        json={
            "telegram_user_id": 7003 if is_active else 7004,
            "role": "mechanic",
            "park_id": seed_park_with_tracker.id,
        },
    )

    assert response.status_code == 200
    assert response.json()["state"] == expected
    assert response.json()["request_id"] is None
    db_session.refresh(user)
    assert user.role == rbac.RoleSlug.OPERATOR
    assert db_session.query(ParkRequest).filter_by(user_id=user.id).count() == 0


@pytest.mark.parametrize("role", ["admin", "royal", "driver"])
def test_telegram_onboarding_rejects_non_applicant_roles(
    client, seed_park_with_tracker, monkeypatch, role
):
    _allow_bot(monkeypatch)
    response = client.post(
        "/internal/bot/native/onboarding/request",
        headers=BOT_HEADERS,
        json={"telegram_user_id": 7005, "role": role, "park_id": seed_park_with_tracker.id},
    )
    assert response.status_code == 422


def test_telegram_onboarding_never_downgrades_linked_admin(
    client, db_session, seed_admin, seed_park_with_tracker, monkeypatch
):
    _allow_bot(monkeypatch)
    db_session.add(TelegramAccount(user_id=seed_admin.id, telegram_user_id=7008))
    db_session.commit()

    response = client.post(
        "/internal/bot/native/onboarding/request",
        headers=BOT_HEADERS,
        json={
            "telegram_user_id": 7008,
            "role": "mechanic",
            "park_id": seed_park_with_tracker.id,
        },
    )

    assert response.status_code == 403
    db_session.refresh(seed_admin)
    assert seed_admin.role == rbac.RoleSlug.ADMIN
    assert db_session.query(ParkRequest).filter_by(user_id=seed_admin.id).count() == 0


def test_telegram_onboarding_rejects_inactive_park_without_partial_identity(
    client, db_session, monkeypatch
):
    _allow_bot(monkeypatch)
    inactive = Park(name="Unavailable", tag="unavailable-onboarding", is_active=False)
    db_session.add(inactive)
    db_session.commit()

    response = client.post(
        "/internal/bot/native/onboarding/request",
        headers=BOT_HEADERS,
        json={"telegram_user_id": 7009, "role": "operator", "park_id": inactive.id},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "park_unavailable"
    assert db_session.query(TelegramAccount).filter_by(telegram_user_id=7009).count() == 0


def test_admin_native_scope_and_bigint_chat_revision(
    client, db_session, seed_admin, seed_park_with_tracker
):
    foreign = Park(name="Foreign", tag="Foreign", timezone="Europe/Moscow", is_active=True)
    db_session.add(foreign)
    db_session.flush()
    db_session.add(UserPark(user_id=seed_admin.id, park_id=seed_park_with_tracker.id))
    db_session.commit()
    login_as(client, "admin", "secret")

    listing = client.get("/admin/bot/native")
    assert listing.status_code == 200
    assert [park["id"] for park in listing.json()["parks"]] == [seed_park_with_tracker.id]

    updated = client.put(
        f"/admin/bot/native/parks/{seed_park_with_tracker.id}",
        json={"chat_id": -1_001_234_567_890, "thread_id": 42, "revision": 1},
    )
    assert updated.status_code == 200
    assert updated.json()["chat_id"] == -1_001_234_567_890
    assert updated.json()["revision"] == 2
    bypass = client.patch(f"/parks/{seed_park_with_tracker.id}", json={"chat_id": -1009999999999})
    assert bypass.status_code == 409
    assert bypass.json()["detail"] == "telegram_destination_use_native_api"
    assert (
        client.put(
            f"/admin/bot/native/parks/{seed_park_with_tracker.id}",
            json={"chat_id": 1, "thread_id": None, "revision": 1},
        ).status_code
        == 409
    )
    assert (
        client.put(
            f"/admin/bot/native/parks/{foreign.id}",
            json={"chat_id": 1, "thread_id": None, "revision": 1},
        ).status_code
        == 403
    )


def test_native_job_crud_is_scoped_and_optimistic(
    client, db_session, seed_admin, seed_park_with_tracker
):
    db_session.add(UserPark(user_id=seed_admin.id, park_id=seed_park_with_tracker.id))
    db_session.commit()
    login_as(client, "admin", "secret")

    created = client.post("/admin/bot/native/jobs", json=_job(seed_park_with_tracker.id))
    assert created.status_code == 201
    body = created.json()
    assert body["enabled"] is False
    changed = client.put(
        f"/admin/bot/native/jobs/{body['id']}",
        json={**_job(seed_park_with_tracker.id, title="Changed"), "revision": body["revision"]},
    )
    assert changed.status_code == 200
    assert changed.json()["revision"] == body["revision"] + 1
    assert (
        client.delete(
            f"/admin/bot/native/jobs/{body['id']}", params={"revision": body["revision"]}
        ).status_code
        == 409
    )
    assert (
        client.delete(
            f"/admin/bot/native/jobs/{body['id']}",
            params={"revision": changed.json()["revision"]},
        ).status_code
        == 204
    )


def test_link_code_single_use_conflict_expiry_and_revoke(
    client, db_session, seed_mechanic, monkeypatch
):
    _allow_bot(monkeypatch)
    login_as(client, "mech1", "secret")
    issued = client.post("/bot/account/link-code")
    assert issued.status_code == 200
    code = issued.json()["code"]

    linked = client.post(
        "/internal/bot/native/link",
        headers=BOT_HEADERS,
        json={"code": code, "telegram_user_id": 9_223_372_036_854_000_000},
    )
    assert linked.status_code == 200
    assert (
        client.post(
            "/internal/bot/native/link",
            headers=BOT_HEADERS,
            json={"code": code, "telegram_user_id": 77},
        ).status_code
        == 409
    )
    account = client.get("/bot/account").json()
    assert account == {"linked": True, "telegram_user_id": 9_223_372_036_854_000_000}
    assert client.delete("/bot/account").status_code == 204
    assert client.get("/bot/account").json() == {"linked": False, "telegram_user_id": None}

    second = client.post("/bot/account/link-code").json()["code"]
    from robopark_api.models import TelegramLinkCode

    row = (
        db_session.query(TelegramLinkCode)
        .filter(TelegramLinkCode.user_id == seed_mechanic.id)
        .one()
    )
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    assert (
        client.post(
            "/internal/bot/native/link",
            headers=BOT_HEADERS,
            json={"code": second, "telegram_user_id": 88},
        ).status_code
        == 410
    )


def test_pending_account_can_link_request_access_and_be_approved_by_park_admin(
    client, db_session, seed_admin, seed_park_with_tracker, monkeypatch
):
    _allow_bot(monkeypatch)
    applicant = User(
        username="pending-mechanic",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, rbac.RoleSlug.MECHANIC),
        access_status=AccessStatus.pending.value,
        is_active=True,
    )
    db_session.add(applicant)
    db_session.flush()
    db_session.add(UserPark(user_id=seed_admin.id, park_id=seed_park_with_tracker.id))
    db_session.commit()

    login_as(client, applicant.username, "secret")
    initial = client.get("/access")
    assert initial.status_code == 200
    assert initial.json()["access_status"] == "pending"
    assert initial.json()["assigned_parks"] == []
    assert initial.json()["available_parks"] == [
        {"id": seed_park_with_tracker.id, "name": seed_park_with_tracker.name}
    ]
    assert set(initial.json()["available_parks"][0]) == {"id", "name"}

    created = client.post("/access/requests", json={"park_id": seed_park_with_tracker.id})
    assert created.status_code == 201
    request_body = created.json()
    assert request_body["revision"] == 1
    assert request_body["role"] == "mechanic"
    assert set(request_body["park"]) == {"id", "name"}

    code = client.post("/bot/account/link-code").json()["code"]
    assert (
        client.post(
            "/internal/bot/native/link",
            headers=BOT_HEADERS,
            json={"code": code, "telegram_user_id": 8001},
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/internal/bot/native/context",
            headers=BOT_HEADERS,
            params={"telegram_user_id": 8001},
        ).status_code
        == 403
    )
    pending_access = client.get(
        "/internal/bot/native/access",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 8001},
    )
    assert pending_access.status_code == 200
    assert pending_access.json()["requests"][0]["id"] == request_body["id"]

    telegram_account = db_session.get(TelegramAccount, applicant.id)
    assert telegram_account is not None
    telegram_account.display_name = "Pending Mechanic"
    telegram_account.telegram_username = "pending_mechanic"
    db_session.commit()

    login_as(client, seed_admin.username, "secret")
    admin_code = client.post("/bot/account/link-code").json()["code"]
    client.post(
        "/internal/bot/native/link",
        headers=BOT_HEADERS,
        json={"code": admin_code, "telegram_user_id": 8002},
    )
    inbox = client.get(
        "/internal/bot/native/access/requests",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 8002},
    )
    assert inbox.status_code == 200
    assert [item["id"] for item in inbox.json()] == [request_body["id"]]
    assert inbox.json()[0]["telegram_user_id"] == 8001
    assert inbox.json()[0]["display_name"] == "Pending Mechanic"
    assert inbox.json()[0]["telegram_username"] == "pending_mechanic"

    approved = client.post(
        f"/internal/bot/native/access/requests/{request_body['id']}/decision",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 8002},
        json={"approve": True, "revision": 1},
    )
    assert approved.status_code == 200
    assert approved.json()["request"]["revision"] == 2
    assert approved.json()["request"]["telegram_user_id"] == 8001
    assert approved.json()["request"]["display_name"] == "Pending Mechanic"
    assert approved.json()["user_access_status"] == "approved"
    db_session.refresh(applicant)
    assert applicant.role == rbac.RoleSlug.MECHANIC
    assert applicant.access_status == AccessStatus.approved.value
    assert db_session.get(UserPark, (applicant.id, seed_park_with_tracker.id)) is not None
    assert (
        client.post(
            f"/internal/bot/native/access/requests/{request_body['id']}/decision",
            headers=BOT_HEADERS,
            params={"telegram_user_id": 8002},
            json={"approve": True, "revision": 1},
        ).json()["detail"]
        == "revision_conflict"
    )
    actions = set(
        db_session.scalars(
            select(AuditLog.action).where(AuditLog.target_id == str(request_body["id"]))
        )
    )
    assert "access.park.requested" in actions
    assert "admin.access.park_request.approved" in actions


def test_access_decision_can_select_another_managed_park_without_changing_role(
    client, db_session, seed_admin, seed_park_with_tracker, monkeypatch
):
    _allow_bot(monkeypatch)
    selected = Park(name="Selected", tag="selected-access", is_active=True)
    foreign = Park(name="Foreign target", tag="foreign-access", is_active=True)
    applicant = User(
        username="target-park-mechanic",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, rbac.RoleSlug.MECHANIC),
        access_status=AccessStatus.pending.value,
        is_active=True,
    )
    db_session.add_all([selected, foreign, applicant])
    db_session.flush()
    db_session.add_all(
        [
            UserPark(user_id=seed_admin.id, park_id=seed_park_with_tracker.id),
            UserPark(user_id=seed_admin.id, park_id=selected.id),
        ]
    )
    request = ParkRequest(
        user_id=applicant.id,
        park_id=seed_park_with_tracker.id,
        status=AccessStatus.pending.value,
    )
    db_session.add(request)
    db_session.commit()
    login_as(client, seed_admin.username, "secret")
    code = client.post("/bot/account/link-code").json()["code"]
    client.post(
        "/internal/bot/native/link",
        headers=BOT_HEADERS,
        json={"code": code, "telegram_user_id": 8030},
    )

    forbidden = client.post(
        f"/internal/bot/native/access/requests/{request.id}/decision",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 8030},
        json={"approve": True, "revision": 1, "target_park_id": foreign.id},
    )
    assert forbidden.status_code == 403
    db_session.refresh(request)
    assert request.status == AccessStatus.pending.value

    invalid_rejection = client.post(
        f"/internal/bot/native/access/requests/{request.id}/decision",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 8030},
        json={"approve": False, "revision": 1, "target_park_id": selected.id},
    )
    assert invalid_rejection.status_code == 422

    approved = client.post(
        f"/internal/bot/native/access/requests/{request.id}/decision",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 8030},
        json={"approve": True, "revision": 1, "target_park_id": selected.id},
    )
    assert approved.status_code == 200
    assert approved.json()["request"]["park"]["id"] == selected.id
    db_session.refresh(applicant)
    assert applicant.role == rbac.RoleSlug.MECHANIC
    assert db_session.get(UserPark, (applicant.id, selected.id)) is not None
    assert db_session.get(UserPark, (applicant.id, seed_park_with_tracker.id)) is None


def test_global_access_requires_all_park_scope_and_only_promotes_pending_applicant(
    client, db_session, seed_admin, seed_park_with_tracker, monkeypatch
):
    _allow_bot(monkeypatch)
    other = Park(name="Other global", tag="other-global", is_active=True)
    applicant = User(
        username="global-mechanic",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, rbac.RoleSlug.MECHANIC),
        access_status=AccessStatus.pending.value,
        is_active=True,
    )
    db_session.add_all([other, applicant])
    db_session.flush()
    db_session.add(UserPark(user_id=seed_admin.id, park_id=seed_park_with_tracker.id))
    request = ParkRequest(
        user_id=applicant.id,
        park_id=seed_park_with_tracker.id,
        status=AccessStatus.pending.value,
    )
    db_session.add(request)
    db_session.commit()
    login_as(client, seed_admin.username, "secret")
    code = client.post("/bot/account/link-code").json()["code"]
    client.post(
        "/internal/bot/native/link",
        headers=BOT_HEADERS,
        json={"code": code, "telegram_user_id": 8031},
    )

    forbidden = client.post(
        f"/internal/bot/native/access/requests/{request.id}/decision",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 8031},
        json={"approve": True, "revision": 1, "global_access": True},
    )
    assert forbidden.status_code == 403
    db_session.refresh(request)
    assert request.status == AccessStatus.pending.value

    db_session.add(UserPark(user_id=seed_admin.id, park_id=other.id))
    db_session.commit()
    approved = client.post(
        f"/internal/bot/native/access/requests/{request.id}/decision",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 8031},
        json={"approve": True, "revision": 1, "global_access": True},
    )
    assert approved.status_code == 200
    db_session.refresh(applicant)
    assert applicant.role == rbac.RoleSlug.OPERATOR
    assert applicant.access_status == AccessStatus.approved.value
    active_ids = set(db_session.scalars(select(Park.id).where(Park.is_active.is_(True))))
    assigned_ids = set(
        db_session.scalars(select(UserPark.park_id).where(UserPark.user_id == applicant.id))
    )
    assert assigned_ids == active_ids


def test_global_access_cannot_change_an_already_approved_mechanic_role(
    client, db_session, seed_royal, seed_mechanic, monkeypatch
):
    _allow_bot(monkeypatch)
    park = Park(name="Global denied", tag="global-denied", is_active=True)
    db_session.add(park)
    db_session.flush()
    request = ParkRequest(
        user_id=seed_mechanic.id,
        park_id=park.id,
        status=AccessStatus.pending.value,
    )
    db_session.add(request)
    db_session.commit()
    login_as(client, seed_royal.username, "secret")
    code = client.post("/bot/account/link-code").json()["code"]
    client.post(
        "/internal/bot/native/link",
        headers=BOT_HEADERS,
        json={"code": code, "telegram_user_id": 8032},
    )

    response = client.post(
        f"/internal/bot/native/access/requests/{request.id}/decision",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 8032},
        json={"approve": True, "revision": 1, "global_access": True},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "global_role_change_forbidden"
    db_session.refresh(seed_mechanic)
    db_session.refresh(request)
    assert seed_mechanic.role == rbac.RoleSlug.MECHANIC
    assert request.status == AccessStatus.pending.value


def test_access_requests_are_scoped_and_cannot_approve_privileged_roles(
    client, db_session, seed_admin, seed_park_with_tracker, monkeypatch
):
    _allow_bot(monkeypatch)
    foreign_park = Park(name="Foreign", tag="Foreign", is_active=True)
    foreign_admin = User(
        username="foreign-admin",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, rbac.RoleSlug.ADMIN),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    applicant = User(
        username="pending-operator",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, rbac.RoleSlug.OPERATOR),
        access_status=AccessStatus.pending.value,
        is_active=True,
    )
    db_session.add_all([foreign_park, foreign_admin, applicant])
    db_session.flush()
    db_session.add_all(
        [
            UserPark(user_id=seed_admin.id, park_id=seed_park_with_tracker.id),
            UserPark(user_id=foreign_admin.id, park_id=foreign_park.id),
        ]
    )
    legitimate = ParkRequest(
        user_id=applicant.id,
        park_id=seed_park_with_tracker.id,
        status=AccessStatus.pending.value,
    )
    privileged = ParkRequest(
        user_id=foreign_admin.id,
        park_id=seed_park_with_tracker.id,
        status=AccessStatus.pending.value,
    )
    db_session.add_all([legitimate, privileged])
    db_session.commit()

    login_as(client, foreign_admin.username, "secret")
    site_manager_access = client.get("/access")
    assert site_manager_access.status_code == 200
    assert site_manager_access.json()["can_manage"] is True
    code = client.post("/bot/account/link-code").json()["code"]
    client.post(
        "/internal/bot/native/link",
        headers=BOT_HEADERS,
        json={"code": code, "telegram_user_id": 8010},
    )
    manager_access = client.get(
        "/internal/bot/native/access",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 8010},
    )
    assert manager_access.status_code == 200
    assert manager_access.json()["can_manage"] is True
    assert manager_access.json()["available_parks"] == []
    assert manager_access.json()["requests"] == []
    assert manager_access.json()["assigned_parks"] == [
        {"id": foreign_park.id, "name": foreign_park.name}
    ]
    assert (
        client.get(
            "/internal/bot/native/access/requests",
            headers=BOT_HEADERS,
            params={"telegram_user_id": 8010},
        ).json()
        == []
    )

    login_as(client, seed_admin.username, "secret")
    code = client.post("/bot/account/link-code").json()["code"]
    client.post(
        "/internal/bot/native/link",
        headers=BOT_HEADERS,
        json={"code": code, "telegram_user_id": 8011},
    )
    inbox = client.get(
        "/internal/bot/native/access/requests",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 8011},
    )
    assert [item["id"] for item in inbox.json()] == [legitimate.id]
    denied = client.post(
        f"/internal/bot/native/access/requests/{privileged.id}/decision",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 8011},
        json={"approve": True, "revision": 1},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "target_not_eligible"


def test_rejected_user_cannot_self_reset_or_be_restored_by_stale_park_request(
    client, db_session, seed_admin, seed_park_with_tracker, monkeypatch
):
    _allow_bot(monkeypatch)
    rejected = User(
        username="rejected-mechanic",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, rbac.RoleSlug.MECHANIC),
        access_status=AccessStatus.rejected.value,
        is_active=True,
    )
    db_session.add(rejected)
    db_session.flush()
    db_session.add(UserPark(user_id=seed_admin.id, park_id=seed_park_with_tracker.id))
    stale = ParkRequest(
        user_id=rejected.id,
        park_id=seed_park_with_tracker.id,
        status=AccessStatus.pending.value,
    )
    db_session.add(stale)
    db_session.commit()

    login_as(client, rejected.username, "secret")
    access = client.get("/access")
    assert access.status_code == 200
    assert access.json()["access_status"] == "rejected"
    assert access.json()["available_parks"] == []
    assert (
        client.post("/access/requests", json={"park_id": seed_park_with_tracker.id}).status_code
        == 403
    )
    code = client.post("/bot/account/link-code").json()["code"]
    client.post(
        "/internal/bot/native/link",
        headers=BOT_HEADERS,
        json={"code": code, "telegram_user_id": 8020},
    )
    assert (
        client.post(
            "/internal/bot/native/access/requests",
            headers=BOT_HEADERS,
            json={
                "telegram_user_id": 8020,
                "park_id": seed_park_with_tracker.id,
            },
        ).status_code
        == 403
    )

    login_as(client, seed_admin.username, "secret")
    assert client.get("/admin/park-requests").json() == []
    site_decision = client.post(
        f"/admin/park-requests/{stale.id}/approve",
        params={"revision": stale.revision},
    )
    assert site_decision.status_code == 403
    code = client.post("/bot/account/link-code").json()["code"]
    client.post(
        "/internal/bot/native/link",
        headers=BOT_HEADERS,
        json={"code": code, "telegram_user_id": 8021},
    )
    inbox = client.get(
        "/internal/bot/native/access/requests",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 8021},
    )
    assert inbox.json() == []
    decision = client.post(
        f"/internal/bot/native/access/requests/{stale.id}/decision",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 8021},
        json={"approve": True, "revision": stale.revision},
    )
    assert decision.status_code == 403
    assert decision.json()["detail"] == "target_not_eligible"
    db_session.refresh(rejected)
    assert rejected.access_status == AccessStatus.rejected.value
    assert db_session.get(UserPark, (rejected.id, seed_park_with_tracker.id)) is None


def test_link_rejects_telegram_id_already_bound(
    client, db_session, seed_mechanic, seed_admin, monkeypatch
):
    _allow_bot(monkeypatch)
    login_as(client, "mech1", "secret")
    first = client.post("/bot/account/link-code").json()["code"]
    assert (
        client.post(
            "/internal/bot/native/link",
            headers=BOT_HEADERS,
            json={"code": first, "telegram_user_id": 700},
        ).status_code
        == 200
    )
    login_as(client, "admin", "secret")
    second = client.post("/bot/account/link-code").json()["code"]
    assert (
        client.post(
            "/internal/bot/native/link",
            headers=BOT_HEADERS,
            json={"code": second, "telegram_user_id": 700},
        ).status_code
        == 409
    )


def test_context_and_private_management_repeat_role_and_park_checks(
    client, db_session, seed_admin, seed_park_with_tracker, monkeypatch
):
    _allow_bot(monkeypatch)
    db_session.add(UserPark(user_id=seed_admin.id, park_id=seed_park_with_tracker.id))
    db_session.commit()
    login_as(client, "admin", "secret")
    code = client.post("/bot/account/link-code").json()["code"]
    client.post(
        "/internal/bot/native/link",
        headers=BOT_HEADERS,
        json={"code": code, "telegram_user_id": 701},
    )

    context = client.get(
        "/internal/bot/native/context",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 701},
    )
    assert context.status_code == 200
    assert context.json()["can_manage"] is True
    assert [park["id"] for park in context.json()["parks"]] == [seed_park_with_tracker.id]
    created = client.post(
        "/internal/bot/native/manage/jobs",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 701},
        json=_job(seed_park_with_tracker.id),
    )
    assert created.status_code == 201


def test_claim_deduplicates_and_pause_before_begin(
    client, db_session, seed_royal, seed_park_with_tracker, monkeypatch
):
    _allow_bot(monkeypatch)
    seed_park_with_tracker.timezone = "UTC"
    db_session.commit()
    login_as(client, "royal", "secret")
    park = client.put(
        f"/admin/bot/native/parks/{seed_park_with_tracker.id}",
        json={"chat_id": -1_001_234_567_890, "thread_id": None, "revision": 1},
    )
    assert park.status_code == 200
    job_time = datetime.now(UTC).strftime("%H:%M")
    created = client.post(
        "/admin/bot/native/jobs",
        json=_job(seed_park_with_tracker.id, enabled=True, time=job_time),
    ).json()
    now = datetime.now(UTC)
    db_session.get(NativeBotJob, created["id"]).updated_at = now - timedelta(minutes=1)
    db_session.commit()
    monkeypatch.setattr("robopark_api.services.native_telegram.utcnow", lambda: now)

    claim = client.post("/internal/bot/native/claim", headers=BOT_HEADERS, json={"limit": 10})
    assert claim.status_code == 200
    deliveries = claim.json()["deliveries"]
    assert len(deliveries) == 1
    assert (
        client.post("/internal/bot/native/claim", headers=BOT_HEADERS, json={"limit": 10}).json()[
            "deliveries"
        ]
        == []
    )

    paused = client.put(
        f"/admin/bot/native/jobs/{created['id']}",
        json={
            **_job(seed_park_with_tracker.id, time=job_time),
            "revision": created["revision"],
        },
    )
    assert paused.status_code == 200
    delivery = deliveries[0]
    begin = client.post(
        f"/internal/bot/native/deliveries/{delivery['id']}/begin",
        headers=BOT_HEADERS,
        json={"lease_token": delivery["lease_token"]},
    )
    assert begin.status_code == 409
    failed = client.get("/admin/bot/native").json()["deliveries"][0]
    assert failed["state"] == "failed"
    assert failed["error_code"] == "configuration_changed"


def test_delivery_begin_finish_terminal_slot_is_not_reissued(
    client, db_session, seed_royal, seed_park_with_tracker, monkeypatch
):
    _allow_bot(monkeypatch)
    seed_park_with_tracker.timezone = "UTC"
    db_session.commit()
    login_as(client, "royal", "secret")
    client.put(
        f"/admin/bot/native/parks/{seed_park_with_tracker.id}",
        json={"chat_id": -1001001001001, "thread_id": 5, "revision": 1},
    )
    job_time = datetime.now(UTC).strftime("%H:%M")
    created = client.post(
        "/admin/bot/native/jobs",
        json=_job(seed_park_with_tracker.id, enabled=True, time=job_time),
    ).json()
    now = datetime.now(UTC)
    db_session.get(NativeBotJob, created["id"]).updated_at = now - timedelta(minutes=1)
    db_session.commit()
    monkeypatch.setattr("robopark_api.services.native_telegram.utcnow", lambda: now)
    delivery = client.post("/internal/bot/native/claim", headers=BOT_HEADERS, json={}).json()[
        "deliveries"
    ][0]
    assert client.post(
        f"/internal/bot/native/deliveries/{delivery['id']}/begin",
        headers=BOT_HEADERS,
        json={"lease_token": delivery["lease_token"]},
    ).json() == {"ready": True}
    assert client.post(
        f"/internal/bot/native/deliveries/{delivery['id']}/finish",
        headers=BOT_HEADERS,
        json={"lease_token": delivery["lease_token"], "state": "sent"},
    ).json() == {"recorded": True}
    assert (
        client.post("/internal/bot/native/claim", headers=BOT_HEADERS, json={}).json()["deliveries"]
        == []
    )


def test_delivery_can_fail_before_begin_and_finish_after_job_pause(
    client, db_session, seed_royal, seed_park_with_tracker, monkeypatch
):
    _allow_bot(monkeypatch)
    seed_park_with_tracker.timezone = "UTC"
    db_session.commit()
    login_as(client, "royal", "secret")
    client.put(
        f"/admin/bot/native/parks/{seed_park_with_tracker.id}",
        json={"chat_id": -1001001001001, "thread_id": None, "revision": 1},
    )
    job_time = datetime.now(UTC).strftime("%H:%M")
    created = client.post(
        "/admin/bot/native/jobs",
        json=_job(seed_park_with_tracker.id, enabled=True, time=job_time),
    ).json()
    now = datetime.now(UTC)
    db_session.get(NativeBotJob, created["id"]).updated_at = now - timedelta(minutes=1)
    db_session.commit()
    monkeypatch.setattr("robopark_api.services.native_telegram.utcnow", lambda: now)
    delivery = client.post("/internal/bot/native/claim", headers=BOT_HEADERS, json={}).json()[
        "deliveries"
    ][0]
    failed = client.post(
        f"/internal/bot/native/deliveries/{delivery['id']}/finish",
        headers=BOT_HEADERS,
        json={
            "lease_token": delivery["lease_token"],
            "state": "failed",
            "error_code": "report_preparation_failed",
        },
    )
    assert failed.status_code == 200

    created2 = client.post(
        "/admin/bot/native/jobs",
        json=_job(seed_park_with_tracker.id, title="Second", enabled=True, time=job_time),
    ).json()
    db_session.get(NativeBotJob, created2["id"]).updated_at = now - timedelta(minutes=1)
    db_session.commit()
    delivery2 = client.post("/internal/bot/native/claim", headers=BOT_HEADERS, json={}).json()[
        "deliveries"
    ][0]
    assert (
        client.post(
            f"/internal/bot/native/deliveries/{delivery2['id']}/begin",
            headers=BOT_HEADERS,
            json={"lease_token": delivery2["lease_token"]},
        ).status_code
        == 200
    )
    client.put(
        f"/admin/bot/native/jobs/{created2['id']}",
        json={
            **_job(seed_park_with_tracker.id, title="Second", time=job_time),
            "revision": created2["revision"],
        },
    )
    assert (
        client.post(
            f"/internal/bot/native/deliveries/{delivery2['id']}/finish",
            headers=BOT_HEADERS,
            json={"lease_token": delivery2["lease_token"], "state": "sent"},
        ).status_code
        == 200
    )


def test_global_disable_stops_claim_and_invalidates_preparing_lease(
    client, db_session, seed_royal, seed_park_with_tracker, monkeypatch
):
    _allow_bot(monkeypatch)
    seed_park_with_tracker.timezone = "UTC"
    db_session.commit()
    login_as(client, "royal", "secret")
    client.put(
        f"/admin/bot/native/parks/{seed_park_with_tracker.id}",
        json={"chat_id": -1001001001001, "thread_id": None, "revision": 1},
    )
    job_time = datetime.now(UTC).strftime("%H:%M")
    created = client.post(
        "/admin/bot/native/jobs",
        json=_job(seed_park_with_tracker.id, enabled=True, time=job_time),
    ).json()
    now = datetime.now(UTC)
    db_session.get(NativeBotJob, created["id"]).updated_at = now - timedelta(minutes=1)
    db_session.commit()
    monkeypatch.setattr("robopark_api.services.native_telegram.utcnow", lambda: now)
    enabled = {"value": True}
    monkeypatch.setattr(
        "robopark_api.services.native_telegram.bot_settings.desired_enabled",
        lambda _path: enabled["value"],
    )
    delivery = client.post("/internal/bot/native/claim", headers=BOT_HEADERS, json={}).json()[
        "deliveries"
    ][0]
    enabled["value"] = False
    assert (
        client.post("/internal/bot/native/claim", headers=BOT_HEADERS, json={}).json()["deliveries"]
        == []
    )
    assert (
        client.post(
            f"/internal/bot/native/deliveries/{delivery['id']}/begin",
            headers=BOT_HEADERS,
            json={"lease_token": delivery["lease_token"]},
        ).status_code
        == 409
    )


def test_native_schema_rejects_unsafe_destination_and_telegram_body(
    client, seed_royal, seed_park_with_tracker
):
    login_as(client, "royal", "secret")
    assert (
        client.put(
            f"/admin/bot/native/parks/{seed_park_with_tracker.id}",
            json={"chat_id": None, "thread_id": 1, "revision": 1},
        ).status_code
        == 422
    )
    assert (
        client.put(
            f"/admin/bot/native/parks/{seed_park_with_tracker.id}",
            json={"chat_id": 0, "thread_id": None, "revision": 1},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/admin/bot/native/jobs",
            json=_job(seed_park_with_tracker.id, title="   "),
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/admin/bot/native/jobs",
            json=_job(
                seed_park_with_tracker.id,
                kind="zoom",
                enabled=True,
                text="x" * 4090,
                url="https://user:secret@example.test/meeting",
            ),
        ).status_code
        == 422
    )


@pytest.mark.parametrize("issue_count", [7, 500])
def test_delivery_content_includes_donors_and_preserves_park_scope_and_cap(
    client, db_session, seed_royal, seed_park_with_tracker, monkeypatch, issue_count
):
    _allow_bot(monkeypatch)
    seed_park_with_tracker.timezone = "UTC"
    db_session.commit()
    login_as(client, "royal", "secret")
    client.put(
        f"/admin/bot/native/parks/{seed_park_with_tracker.id}",
        json={"chat_id": -1001001001001, "thread_id": None, "revision": 1},
    )
    job_time = datetime.now(UTC).strftime("%H:%M")
    created = client.post(
        "/admin/bot/native/jobs",
        json=_job(
            seed_park_with_tracker.id,
            kind="report",
            enabled=True,
            text=None,
            time=job_time,
        ),
    ).json()
    now = datetime.now(UTC)
    db_session.get(NativeBotJob, created["id"]).updated_at = now - timedelta(minutes=1)
    db_session.commit()
    monkeypatch.setattr("robopark_api.services.native_telegram.utcnow", lambda: now)
    delivery = client.post("/internal/bot/native/claim", headers=BOT_HEADERS, json={}).json()[
        "deliveries"
    ][0]
    captured = {}

    def search(**kwargs):
        captured.update(kwargs)
        rows = [
            {
                "key": f"ROBOPARK-{index}",
                "summary": f"[a{1000 + index}] repair",
                "tags": [seed_park_with_tracker.tag, *(["donor"] if index < 3 else [])],
            }
            for index in range(issue_count)
        ]
        if 'Tags: !"donor"' in kwargs["query"]:
            return [row for row in rows if "donor" not in row["tags"]]
        return rows

    monkeypatch.setattr(
        "robopark_api.services.native_telegram.bot_tracker_gateway.tracker_token",
        lambda _db: "token",
    )
    monkeypatch.setattr("robopark_api.services.native_telegram.bot_tracker_gateway.search", search)
    response = client.post(
        f"/internal/bot/native/deliveries/{delivery['id']}/content",
        headers=BOT_HEADERS,
        json={"lease_token": delivery["lease_token"]},
    )

    assert response.status_code == 200
    assert response.json()["truncated"] is (issue_count == 500)
    assert len(response.json()["issues"]) == issue_count
    assert sum("donor" in row["tags"] for row in response.json()["issues"]) == 3
    assert f"Tags: {seed_park_with_tracker.tag}" in captured["query"]
    assert 'Tags: !"donor"' not in captured["query"]
    assert "Type: repair, service, calibration" in captured["query"]
    assert "Priority: blocker" in captured["query"]
    assert "Resolution: empty()" in captured["query"]
    assert "Status: !closed" in captured["query"]
    assert captured["allowed_queues"] == (seed_park_with_tracker.tracker_queue,)


@pytest.mark.parametrize("view", ["open", "history"])
def test_robot_read_filters_foreign_tag_and_partial_number(
    client, seed_mechanic, seed_park_with_tracker, monkeypatch, view
):
    _allow_bot(monkeypatch)
    login_as(client, "mech1", "secret")
    code = client.post("/bot/account/link-code").json()["code"]
    client.post(
        "/internal/bot/native/link",
        headers=BOT_HEADERS,
        json={"code": code, "telegram_user_id": 811},
    )
    monkeypatch.setattr(
        "robopark_api.services.native_telegram.bot_tracker_gateway.tracker_token",
        lambda _db: "token",
    )
    queries = []

    def search(**kwargs):
        queries.append(kwargs["query"])
        return [
            {
                "key": "ROBOPARK-1",
                "summary": "[447] repair",
                "tags": [seed_park_with_tracker.tag],
                "type": {"key": "repair"},
                "resolution": {"key": "fixed"} if view == "history" else None,
            },
            {"key": "ROBOPARK-2", "summary": "[447] repair", "tags": ["Foreign"]},
            {
                "key": "ROBOPARK-3",
                "summary": "[1447] repair",
                "tags": [seed_park_with_tracker.tag],
            },
            {
                "key": "ROBOPARK-4",
                "summary": "[447] bug",
                "tags": [seed_park_with_tracker.tag],
                "type": {"key": "bug"},
            },
        ]

    monkeypatch.setattr("robopark_api.services.native_telegram.bot_tracker_gateway.search", search)
    response = client.get(
        "/internal/bot/native/robots/A0447",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 811, "view": view},
    )

    assert response.status_code == 200
    assert response.json()["robot"] == "447"
    assert [item["key"] for item in response.json()["issues"]] == ["ROBOPARK-1"]
    if view == "history":
        assert "Resolution: fixed" in queries[0]
    assert "Type: repair, service, calibration" in queries[0]
    assert f"Tags: {seed_park_with_tracker.tag}" in queries[0]


def test_robot_read_honors_tracker_permission_override(
    client, db_session, seed_mechanic, monkeypatch
):
    _allow_bot(monkeypatch)
    rbac.set_user_effective_permissions(
        db_session,
        seed_mechanic,
        sorted(
            rbac.permissions_for_user(db_session, seed_mechanic)
            - {rbac.PERMISSION_TRACKER_READ, rbac.PERMISSION_TRACKER_WRITE}
        ),
    )
    login_as(client, "mech1", "secret")
    code = client.post("/bot/account/link-code").json()["code"]
    client.post(
        "/internal/bot/native/link",
        headers=BOT_HEADERS,
        json={"code": code, "telegram_user_id": 813},
    )
    response = client.get(
        "/internal/bot/native/robots/447",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 813, "view": "open"},
    )
    assert response.status_code == 403


def test_auxiliary_robot_view_requires_park_anchor_and_configured_queue(
    client, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    _allow_bot(monkeypatch)
    login_as(client, "mech1", "secret")
    code = client.post("/bot/account/link-code").json()["code"]
    client.post(
        "/internal/bot/native/link",
        headers=BOT_HEADERS,
        json={"code": code, "telegram_user_id": 812},
    )
    monkeypatch.setattr(
        "robopark_api.routers.native_telegram.bot_shared_settings.auxiliary_tracker_queues",
        lambda _path: ("ROBOMAINT",),
    )
    monkeypatch.setattr(
        "robopark_api.services.native_telegram.bot_tracker_gateway.tracker_token",
        lambda _db: "token",
    )
    calls = []

    def search(**kwargs):
        calls.append(kwargs)
        if "Queue: ROBOPARK" in kwargs["query"]:
            return [
                {
                    "key": "ROBOPARK-1",
                    "summary": "[447] repair",
                    "tags": [seed_park_with_tracker.tag],
                }
            ]
        return [
            {"key": "ROBOMAINT-1", "summary": "move", "tags": [], "rover": "a447"},
            {"key": "ROBOMAINT-2", "summary": "move", "tags": [], "rover": "a1447"},
        ]

    monkeypatch.setattr("robopark_api.services.native_telegram.bot_tracker_gateway.search", search)
    response = client.get(
        "/internal/bot/native/robots/447",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 812, "view": "moves"},
    )

    assert response.status_code == 200
    assert [item["key"] for item in response.json()["issues"]] == ["ROBOMAINT-1"]
    assert calls[-1]["allowed_queues"] == ("ROBOMAINT",)
    assert "Status: inProgress, new, needEstimate, needInfo" in calls[-1]["query"]
    assert response.json()["can_qr"] is True
    assert response.json()["allowed_views"] == [
        "open",
        "history",
        "moves",
        "moves_history",
        "parts",
    ]


def test_global_operator_reads_auxiliary_robot_without_main_ticket_anchor(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    _allow_bot(monkeypatch)
    second_park = Park(name="Second active", tag="second-active", is_active=True)
    operator = User(
        username="global-operator",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, rbac.RoleSlug.OPERATOR),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add_all([second_park, operator])
    db_session.flush()
    active_park_ids = list(db_session.scalars(select(Park.id).where(Park.is_active.is_(True))))
    db_session.add_all(
        [UserPark(user_id=operator.id, park_id=park_id) for park_id in active_park_ids]
        + [TelegramAccount(user_id=operator.id, telegram_user_id=814)]
    )
    db_session.commit()
    monkeypatch.setattr(
        "robopark_api.routers.native_telegram.bot_shared_settings.auxiliary_tracker_queues",
        lambda _path: ("ROBOMAINT",),
    )
    monkeypatch.setattr(
        "robopark_api.services.native_telegram.bot_tracker_gateway.tracker_token",
        lambda _db: "token",
    )
    calls = []

    def search(**kwargs):
        calls.append(kwargs)
        return [{"key": "ROBOMAINT-1", "summary": "move", "tags": [], "rover": "a447"}]

    monkeypatch.setattr("robopark_api.services.native_telegram.bot_tracker_gateway.search", search)

    response = client.get(
        "/internal/bot/native/robots/447",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 814, "view": "moves"},
    )

    assert response.status_code == 200
    assert [item["key"] for item in response.json()["issues"]] == ["ROBOMAINT-1"]
    assert [call["allowed_queues"] for call in calls] == [("ROBOMAINT",)]
    assert response.json()["can_qr"] is True
    assert response.json()["allowed_views"] == [
        "open",
        "history",
        "moves",
        "moves_history",
        "parts",
    ]


def test_invalid_link_attempts_are_bounded(client, seed_mechanic, monkeypatch):
    _allow_bot(monkeypatch)
    for _ in range(5):
        assert (
            client.post(
                "/internal/bot/native/link",
                headers=BOT_HEADERS,
                json={"code": "00000000", "telegram_user_id": 912},
            ).status_code
            == 400
        )
    assert (
        client.post(
            "/internal/bot/native/link",
            headers=BOT_HEADERS,
            json={"code": "00000000", "telegram_user_id": 912},
        ).status_code
        == 429
    )


def test_runtime_health_is_bounded_and_visible(client, seed_royal, monkeypatch):
    _allow_bot(monkeypatch)
    login_as(client, "royal", "secret")
    assert client.get("/admin/bot/native").json()["health"]["state"] == "unknown"
    assert (
        client.post(
            "/internal/bot/native/health",
            headers=BOT_HEADERS,
            json={"telegram_ok": True, "scheduler_ok": False, "last_error": "scheduler_lag"},
        ).status_code
        == 204
    )
    health = client.get("/admin/bot/native").json()["health"]
    assert health["state"] == "degraded"
    assert health["telegram_ok"] is True
    assert health["scheduler_ok"] is False
    assert health["last_error"] == "scheduler_lag"
    assert (
        client.post(
            "/internal/bot/native/health",
            headers=BOT_HEADERS,
            json={"telegram_ok": False, "scheduler_ok": False, "last_error": "secret token"},
        ).status_code
        == 422
    )


def test_legacy_migration_is_royal_disabled_and_idempotent(
    client,
    db_session,
    test_settings,
    tmp_path,
    seed_admin,
    seed_royal,
    seed_park_with_tracker,
):
    test_settings.host_data_path = str(tmp_path)
    data = tmp_path / "telegram-bot" / "data"
    data.mkdir(parents=True)
    payloads = {
        "locations.json": {
            "version": 2,
            "locations": [
                {
                    "key": "North",
                    "display_name": "North",
                    "tracker_tag": seed_park_with_tracker.tag,
                    "slug": "north",
                    "chats": {
                        "prod": {"chat_id": -1001234567890, "thread_id": 7},
                        "test": None,
                    },
                    "chat_overrides": {"prod": True, "test": False},
                    "logistics": None,
                    "participation": {"hourly_png": True},
                    "report_window": {"start_hour": 10, "end_hour": 18},
                },
                {
                    "key": "North alias",
                    "display_name": "North alias",
                    "tracker_tag": seed_park_with_tracker.tag,
                    "slug": "north-alias",
                    "chats": {
                        "prod": {"chat_id": -1001234567890, "thread_id": 7},
                        "test": None,
                    },
                    "chat_overrides": {"prod": True, "test": False},
                    "logistics": None,
                    "participation": {"hourly_png": False},
                    "report_window": None,
                },
            ],
            "status_tags": [],
            "metadata": {
                "deleted_location_keys": ["Deleted park"],
                "deleted_location_slugs": ["deleted-park"],
            },
        },
        "schedules.json": {
            "version": 2,
            "deleted_job_ids": ["deleted_zoom"],
            "timezone": "Europe/Moscow",
            "planner_anchor": "2026-08-07",
            "send_window": {"start_hour": 9, "end_hour": 21, "enabled": True},
            "jobs": [
                {
                    "id": "zoom_custom",
                    "label": "Morning",
                    "enabled": True,
                    "fire_at": "09:00",
                    "weekdays": None,
                    "kind": "simple",
                    "alternate": "A",
                    "text": "Join {link}",
                    "locations": ["North", "North alias"],
                    "link": "https://example.test/zoom",
                }
            ],
        },
        "broadcasts.json": {"version": 1, "campaigns": [], "removed_ids": []},
        "sk_campaigns.json": {"version": 1, "campaigns": []},
    }
    for filename, payload in payloads.items():
        (data / filename).write_text(json.dumps(payload), encoding="utf-8")
        (data.parent / filename).write_text(json.dumps(payload), encoding="utf-8")

    login_as(client, "admin", "secret")
    assert client.get("/admin/bot/native/migration/preview").status_code == 403
    login_as(client, "royal", "secret")
    preview = client.get("/admin/bot/native/migration/preview")
    assert preview.status_code == 200, preview.text
    plan = preview.json()
    assert plan["counts"] == {
        "park_updates": 1,
        "jobs": 2,
        "conflicts": 0,
        "skipped": 3,
    }
    report = next(item for item in plan["jobs"] if item["kind"] == "report")
    assert (report["start_hour"], report["end_hour"]) == (10, 18)
    seed_park_with_tracker.chat_id = -1009999999999
    seed_park_with_tracker.bot_revision += 1
    db_session.commit()
    stale = client.post(
        "/admin/bot/native/migration/apply", json={"fingerprint": plan["fingerprint"]}
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "migration_fingerprint_changed"
    seed_park_with_tracker.chat_id = None
    seed_park_with_tracker.bot_revision += 1
    db_session.commit()
    plan = client.get("/admin/bot/native/migration/preview").json()
    applied = client.post(
        "/admin/bot/native/migration/apply", json={"fingerprint": plan["fingerprint"]}
    )
    assert applied.status_code == 200, applied.text
    imported = (
        db_session.query(NativeBotJob).filter_by(source_ref=plan["jobs"][0]["source_ref"]).one()
    )
    assert imported.enabled is False
    assert seed_park_with_tracker.chat_id == -1001234567890
    repeated = client.post(
        "/admin/bot/native/migration/apply", json={"fingerprint": plan["fingerprint"]}
    )
    assert repeated.status_code == 200
    assert repeated.json()["already_applied"] is True
    assert db_session.query(NativeBotJob).filter(NativeBotJob.source_ref.is_not(None)).count() == 2

    flat_schedules = {**payloads["schedules.json"], "planner_anchor": "2026-08-08"}
    (data.parent / "schedules.json").write_text(json.dumps(flat_schedules), encoding="utf-8")
    conflict = client.get("/admin/bot/native/migration/preview")
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "legacy_bot_sources_conflict"


def test_legacy_preview_validates_drafts_and_tombstones_win(
    client,
    test_settings,
    tmp_path,
    seed_royal,
    seed_park_with_tracker,
):
    test_settings.host_data_path = str(tmp_path)
    data = tmp_path / "telegram-bot"
    data.mkdir()

    def location(key, tag, hourly=False):
        return {
            "key": key,
            "display_name": key,
            "tracker_tag": tag,
            "slug": key.lower(),
            "chats": {"prod": None, "test": None},
            "chat_overrides": {"prod": False, "test": False},
            "logistics": None,
            "participation": {"hourly_png": hourly},
            "report_window": None,
        }

    locations = {
        "version": 2,
        "locations": [
            location("North", seed_park_with_tracker.tag, hourly=True),
            location("Deleted", "DeletedTag", hourly=True),
        ],
        "status_tags": [],
        "metadata": {
            "deleted_location_keys": ["Deleted"],
            "deleted_location_slugs": ["deleted"],
        },
    }
    base_job = {
        "label": "Legacy",
        "enabled": True,
        "fire_at": "09:00",
        "weekdays": [],
        "kind": "simple",
        "alternate": None,
        "text": "Hello",
        "locations": ["North"],
        "link": "",
    }
    schedules = {
        "version": 2,
        "deleted_job_ids": ["deleted_job"],
        "timezone": "Europe/Moscow",
        "planner_anchor": "2026-08-07",
        "send_window": {"start_hour": 9, "end_hour": 21, "enabled": True},
        "jobs": [
            {**base_job, "id": "empty_days"},
            {**base_job, "id": "bad_link", "link": "zoom.us/j/123"},
            {**base_job, "id": "deleted_job"},
            {**base_job, "id": "deleted_location", "locations": ["Deleted"]},
        ],
    }
    broadcasts = {
        "version": 1,
        "campaigns": [
            {
                "id": "removed_broadcast",
                "label": "Removed",
                "text": "Removed",
                "enabled": True,
                "fire_at": "10:00",
                "repeat": "daily",
                "location_mode": "keys",
                "location_keys": ["North"],
            }
        ],
        "removed_ids": ["removed_broadcast"],
    }
    for filename, payload in {
        "locations.json": locations,
        "schedules.json": schedules,
        "broadcasts.json": broadcasts,
        "sk_campaigns.json": {"version": 1, "campaigns": []},
    }.items():
        (data / filename).write_text(json.dumps(payload), encoding="utf-8")

    login_as(client, "royal", "secret")
    response = client.get("/admin/bot/native/migration/preview")
    assert response.status_code == 200, response.text
    plan = response.json()
    assert {job["source_id"] for job in plan["jobs"]} == {
        "empty_days",
        "hourly_report:North",
    }
    empty_days = next(job for job in plan["jobs"] if job["source_id"] == "empty_days")
    assert empty_days["weekdays"] == []
    assert plan["counts"] == {
        "park_updates": 0,
        "jobs": 2,
        "conflicts": 1,
        "skipped": 3,
    }
    assert plan["conflicts"][0]["source_id"] == "bad_link"
    assert plan["conflicts"][0]["reason"] == "unsupported_shape"


def test_legacy_preview_deduplicates_canonical_park_and_flush_conflict_is_409(
    db_session, seed_royal, seed_park_with_tracker, monkeypatch
):
    def location(key, chat_id):
        return {
            "key": key,
            "display_name": key,
            "tracker_tag": seed_park_with_tracker.tag,
            "slug": key.lower(),
            "chats": {"prod": {"chat_id": chat_id, "thread_id": None}, "test": None},
            "participation": {"hourly_png": False},
        }

    sections = {
        "locations": bot_shared_settings.Section(
            revision="locations",
            value={
                "version": 1,
                "locations": [location("North", -1001), location("Alias", -1002)],
                "status_tags": [],
            },
        ),
        "schedules": bot_shared_settings.Section(
            revision="schedules",
            value={
                "timezone": "Europe/Moscow",
                "planner_anchor": "2026-08-07",
                "send_window": {"start_hour": 9, "end_hour": 21, "enabled": True},
                "jobs": [
                    {
                        "id": "same_park",
                        "label": "Same park",
                        "enabled": True,
                        "fire_at": "09:00",
                        "weekdays": None,
                        "kind": "simple",
                        "alternate": None,
                        "text": "Hello",
                        "locations": ["North", "Alias"],
                        "link": "",
                    }
                ],
            },
        ),
        "broadcasts": bot_shared_settings.Section(
            revision="broadcasts",
            value={"version": 1, "campaigns": [], "removed_ids": []},
        ),
        "campaigns": bot_shared_settings.Section(
            revision="campaigns", value={"version": 1, "campaigns": []}
        ),
    }
    monkeypatch.setattr(
        native_telegram_migration.bot_shared_settings,
        "read_legacy_sources",
        lambda _path: [("flat", sections)],
    )
    plan = native_telegram_migration.preview(db_session, "/unused")
    assert plan["park_updates"] == []
    assert len(plan["jobs"]) == 1
    assert plan["counts"]["skipped"] == 1
    assert [item["reason"] for item in plan["conflicts"]] == ["destination_conflict"]

    assert seed_royal.id > 0
    monkeypatch.setattr(native_telegram_migration, "preview", lambda _db, _path: plan)
    original_flush = db_session.flush
    flush_calls = 0

    def conflicting_flush(*args, **kwargs):
        nonlocal flush_calls
        flush_calls += 1
        if flush_calls > 1:
            raise IntegrityError("insert", {}, Exception("duplicate"))
        return original_flush(*args, **kwargs)

    monkeypatch.setattr(db_session, "flush", conflicting_flush)
    with pytest.raises(HTTPException) as caught:
        native_telegram_migration.apply(db_session, seed_royal, "/unused", plan["fingerprint"])
    assert caught.value.status_code == 409
    assert caught.value.detail == "migration_state_changed"
