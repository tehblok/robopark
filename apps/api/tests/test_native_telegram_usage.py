from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi import HTTPException

from conftest import login_as
from robopark_api.models import (
    AuditLog,
    NativeBotDelivery,
    NativeBotJob,
    NativeBotUsageDaily,
    NativeBotUsageTotal,
    TelegramAccount,
    UserPark,
)
from robopark_api.services import native_telegram, native_telegram_usage

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


def test_successful_queries_count_in_moscow_and_greet_once_per_day(
    db_session, seed_mechanic, monkeypatch
):
    now = {"value": datetime(2026, 10, 7, 3, 0, tzinfo=UTC)}
    monkeypatch.setattr(native_telegram_usage, "utcnow", lambda: now["value"])

    first = native_telegram_usage.record_success(db_session, seed_mechanic)
    second = native_telegram_usage.record_success(db_session, seed_mechanic)
    now["value"] = datetime(2026, 10, 8, 21, 30, tzinfo=UTC)
    next_day = native_telegram_usage.record_success(db_session, seed_mechanic)

    assert first.stats.model_dump() == {"today": 1, "month": 1, "total": 1}
    assert first.greeting == "Доброе утро, mech1!"
    assert second.stats.model_dump() == {"today": 2, "month": 2, "total": 2}
    assert second.greeting is None
    assert next_day.stats.model_dump() == {"today": 1, "month": 3, "total": 3}
    assert next_day.greeting == "Привет, mech1!"


def test_daily_retention_prunes_rows_but_preserves_total(db_session, seed_mechanic, monkeypatch):
    today = date(2026, 10, 7)
    db_session.add(NativeBotUsageTotal(user_id=seed_mechanic.id, total=40, greeted_on=today))
    db_session.add_all(
        [
            NativeBotUsageDaily(
                user_id=seed_mechanic.id,
                day=today - timedelta(days=90),
                count=30,
            ),
            NativeBotUsageDaily(
                user_id=seed_mechanic.id,
                day=today - timedelta(days=89),
                count=10,
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setattr(
        native_telegram_usage,
        "utcnow",
        lambda: datetime(2026, 10, 7, 9, 0, tzinfo=UTC),
    )

    result = native_telegram_usage.record_success(db_session, seed_mechanic)

    days = db_session.query(NativeBotUsageDaily).order_by(NativeBotUsageDaily.day).all()
    assert [(row.day, row.count) for row in days] == [
        (today - timedelta(days=89), 10),
        (today, 1),
    ]
    assert result.stats.total == 41


def test_all_usage_limits_admin_to_users_in_assigned_parks(
    db_session, seed_admin, seed_royal, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    db_session.add(UserPark(user_id=seed_admin.id, park_id=seed_park_with_tracker.id))
    db_session.commit()
    monkeypatch.setattr(
        native_telegram_usage,
        "utcnow",
        lambda: datetime(2026, 10, 7, 9, 0, tzinfo=UTC),
    )
    native_telegram_usage.record_success(db_session, seed_mechanic)
    native_telegram_usage.record_success(db_session, seed_royal)

    admin_rows = native_telegram_usage.all_usage(db_session, seed_admin)
    royal_rows = native_telegram_usage.all_usage(db_session, seed_royal)

    assert [row.user_id for row in admin_rows] == [seed_mechanic.id]
    assert {row.user_id for row in royal_rows} == {seed_mechanic.id, seed_royal.id}
    assert royal_rows[0].stats.total == 1


def test_control_is_optimistic_royal_only_and_audited(db_session, seed_admin, seed_royal):
    initial = native_telegram_usage.control(db_session)
    assert initial.model_dump() == {
        "queries_paused": False,
        "deliveries_paused": False,
        "revision": 1,
    }

    with pytest.raises(HTTPException) as denied:
        native_telegram_usage.update_control(
            db_session,
            seed_admin,
            queries_paused=True,
            deliveries_paused=False,
            revision=1,
        )
    assert denied.value.status_code == 403

    changed = native_telegram_usage.update_control(
        db_session,
        seed_royal,
        queries_paused=True,
        deliveries_paused=True,
        revision=1,
    )
    assert changed.model_dump() == {
        "queries_paused": True,
        "deliveries_paused": True,
        "revision": 2,
    }
    with pytest.raises(HTTPException) as stale:
        native_telegram_usage.update_control(
            db_session,
            seed_royal,
            queries_paused=False,
            deliveries_paused=False,
            revision=1,
        )
    assert stale.value.status_code == 409
    audit_row = (
        db_session.query(AuditLog).filter_by(action="admin.native_bot.control.updated").one()
    )
    assert (audit_row.actor_user_id, audit_row.target_id) == (
        seed_royal.id,
        "native_telegram_control",
    )
    assert "queries_paused" in (audit_row.detail or "")


def test_query_pause_allows_admins_and_blocks_normal_users(
    db_session, seed_admin, seed_royal, seed_mechanic
):
    native_telegram_usage.update_control(
        db_session,
        seed_royal,
        queries_paused=True,
        deliveries_paused=False,
        revision=1,
    )

    native_telegram_usage.require_queries_active(db_session, seed_admin)
    native_telegram_usage.require_queries_active(db_session, seed_royal)
    with pytest.raises(HTTPException) as paused:
        native_telegram_usage.require_queries_active(db_session, seed_mechanic)
    assert paused.value.status_code == 409
    assert paused.value.detail == "native_queries_paused"
    assert native_telegram_usage.deliveries_paused(db_session) is False


def test_delivery_pause_guard_does_not_mutate_state(db_session, seed_royal):
    native_telegram_usage.update_control(
        db_session,
        seed_royal,
        queries_paused=False,
        deliveries_paused=True,
        revision=1,
    )

    with pytest.raises(HTTPException) as paused:
        native_telegram_usage.require_deliveries_active(db_session)
    assert paused.value.status_code == 409
    assert paused.value.detail == "native_deliveries_paused"
    assert native_telegram_usage.deliveries_paused(db_session) is True


def test_usage_routes_scope_self_and_manager_rows(
    client,
    db_session,
    seed_admin,
    seed_royal,
    seed_mechanic,
    seed_park_with_tracker,
    monkeypatch,
):
    _allow_bot(monkeypatch)
    db_session.add(UserPark(user_id=seed_admin.id, park_id=seed_park_with_tracker.id))
    db_session.add_all(
        [
            TelegramAccount(user_id=seed_mechanic.id, telegram_user_id=9101),
            TelegramAccount(user_id=seed_admin.id, telegram_user_id=9102),
            TelegramAccount(user_id=seed_royal.id, telegram_user_id=9103),
        ]
    )
    db_session.commit()
    native_telegram_usage.record_success(db_session, seed_mechanic)
    native_telegram_usage.record_success(db_session, seed_royal)

    own = client.get(
        "/internal/bot/native/usage",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 9101},
    )
    admin_all = client.get(
        "/internal/bot/native/usage/all",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 9102},
    )
    denied = client.get(
        "/internal/bot/native/usage/all",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 9101},
    )
    login_as(client, "admin", "secret")
    public_admin = client.get("/admin/bot/native/usage")

    assert own.status_code == 200
    assert own.json()["stats"]["total"] == 1
    assert [row["user_id"] for row in admin_all.json()] == [seed_mechanic.id]
    assert public_admin.json() == admin_all.json()
    assert denied.status_code == 403


def test_robot_route_counts_only_success_and_returns_daily_greeting(
    client, db_session, seed_royal, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    _allow_bot(monkeypatch)
    db_session.add(TelegramAccount(user_id=seed_mechanic.id, telegram_user_id=9201))
    db_session.commit()
    monkeypatch.setattr(
        "robopark_api.services.native_telegram.bot_tracker_gateway.tracker_token",
        lambda _db: "token",
    )
    search_state = {"fail": False}

    def search(**_kwargs):
        if search_state["fail"]:
            raise native_telegram.tracker_client.TrackerError("offline")
        return [
            {
                "key": "ROBOPARK-1",
                "summary": "[447] repair",
                "tags": [seed_park_with_tracker.tag],
            }
        ]

    monkeypatch.setattr("robopark_api.services.native_telegram.bot_tracker_gateway.search", search)
    first = client.get(
        "/internal/bot/native/robots/447",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 9201, "view": "open"},
    )
    second = client.get(
        "/internal/bot/native/robots/447",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 9201, "view": "open"},
    )
    search_state["fail"] = True
    failed = client.get(
        "/internal/bot/native/robots/447",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 9201, "view": "open"},
    )
    native_telegram_usage.update_control(
        db_session,
        seed_royal,
        queries_paused=True,
        deliveries_paused=False,
        revision=1,
    )
    query_paused = client.get(
        "/internal/bot/native/robots/447",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 9201, "view": "open"},
    )

    assert first.status_code == 200
    assert first.json()["usage"]["total"] == 1
    assert seed_mechanic.username in first.json()["greeting"]
    assert second.json()["usage"]["total"] == 2
    assert second.json()["greeting"] is None
    assert failed.status_code == 502
    assert query_paused.status_code == 409
    assert query_paused.json()["detail"] == "native_queries_paused"
    assert native_telegram_usage.user_stats(db_session, seed_mechanic.id).total == 2


def test_control_routes_and_delivery_pause_preserve_preparing_lease(
    client, db_session, seed_royal, seed_park_with_tracker, monkeypatch
):
    _allow_bot(monkeypatch)
    db_session.add(TelegramAccount(user_id=seed_royal.id, telegram_user_id=9301))
    seed_park_with_tracker.timezone = "UTC"
    seed_park_with_tracker.chat_id = -1001234567890
    job = NativeBotJob(
        park_id=seed_park_with_tracker.id,
        kind="text",
        title="Now",
        enabled=True,
        schedule="daily",
        time=datetime.now(UTC).strftime("%H:%M"),
        weekdays="0,1,2,3,4,5,6",
        text="Hello",
        alternate="all",
        revision=1,
        updated_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    db_session.add(job)
    db_session.commit()
    login_as(client, "royal", "secret")

    paused = client.put(
        "/admin/bot/native/control",
        json={"queries_paused": False, "deliveries_paused": True, "revision": 1},
    )
    assert paused.status_code == 200
    assert client.post(
        "/internal/bot/native/claim", headers=BOT_HEADERS, json={"limit": 10}
    ).json() == {"deliveries": []}
    assert db_session.query(NativeBotDelivery).count() == 0

    resumed = client.put(
        "/internal/bot/native/control",
        headers=BOT_HEADERS,
        params={"telegram_user_id": 9301},
        json={"queries_paused": False, "deliveries_paused": False, "revision": 2},
    )
    assert resumed.status_code == 200
    delivery = client.post(
        "/internal/bot/native/claim", headers=BOT_HEADERS, json={"limit": 10}
    ).json()["deliveries"][0]
    client.put(
        "/admin/bot/native/control",
        json={"queries_paused": False, "deliveries_paused": True, "revision": 3},
    )
    begin = client.post(
        f"/internal/bot/native/deliveries/{delivery['id']}/begin",
        headers=BOT_HEADERS,
        json={"lease_token": delivery["lease_token"]},
    )
    assert begin.status_code == 409
    assert begin.json()["detail"] == "native_deliveries_paused"
    db_session.expire_all()
    assert db_session.get(NativeBotDelivery, delivery["id"]).state == "preparing"
