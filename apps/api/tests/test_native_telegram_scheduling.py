from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from conftest import login_as
from robopark_api.models import AuditLog, NativeBotDelivery, NativeBotJob, Park
from robopark_api.native_telegram_schemas import BotJobCreate, BotJobOut
from robopark_api.services import (
    bot_shared_settings,
    native_telegram,
    native_telegram_migration,
    native_telegram_usage,
)


def _job(park: Park, **overrides) -> NativeBotJob:
    values = {
        "park_id": park.id,
        "kind": "text",
        "title": "Scheduled text",
        "enabled": True,
        "schedule": "daily",
        "timezone": None,
        "time": "09:00",
        "run_at": None,
        "weekdays": "0,1,2,3,4,5,6",
        "start_hour": None,
        "end_hour": None,
        "text": "Hello",
        "url": None,
        "tracker_tag": None,
        "alternate": "all",
        "anchor_date": None,
        "revision": 1,
        "updated_at": datetime(2025, 1, 1, tzinfo=UTC),
    }
    values.update(overrides)
    return NativeBotJob(**values)


def _job_payload(park_id: int, **overrides) -> dict:
    values = {
        "park_id": park_id,
        "kind": "text",
        "title": "Scheduled text",
        "enabled": False,
        "schedule": "daily",
        "timezone": None,
        "time": "09:00",
        "run_at": None,
        "weekdays": [0, 1, 2, 3, 4, 5, 6],
        "start_hour": None,
        "end_hour": None,
        "text": "Hello",
        "url": None,
        "tracker_tag": None,
        "alternate": "all",
        "anchor_date": None,
    }
    values.update(overrides)
    return values


def test_job_schema_validates_iana_timezone_and_once_shape():
    with pytest.raises(ValidationError):
        BotJobCreate.model_validate(_job_payload(1, timezone="Mars/Olympus_Mons"))
    with pytest.raises(ValidationError):
        BotJobCreate.model_validate(
            _job_payload(
                1,
                schedule="once",
                time=None,
                weekdays=[],
                run_at=datetime(2026, 1, 1, 9, 0),
            )
        )

    payload = BotJobCreate.model_validate(
        _job_payload(
            1,
            schedule="once",
            timezone="Asia/Almaty",
            time=None,
            weekdays=[],
            run_at=datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
        )
    )

    assert payload.schedule == "once"
    assert payload.timezone == "Asia/Almaty"

    old_once = BotJobOut.model_validate(
        {
            **_job_payload(
                1,
                enabled=True,
                schedule="once",
                time=None,
                weekdays=[],
                run_at=datetime.now(UTC) - timedelta(days=1),
            ),
            "id": "old-once",
            "revision": 1,
        }
    )
    assert old_once.id == "old-once"


def test_slot_uses_job_timezone_override_and_falls_back_to_park_timezone(
    seed_park_with_tracker,
):
    seed_park_with_tracker.timezone = "America/Los_Angeles"
    now = datetime(2026, 1, 5, 9, 5, tzinfo=UTC)

    assert native_telegram._slot(
        _job(seed_park_with_tracker, timezone="UTC"), seed_park_with_tracker, now
    ) == datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
    assert native_telegram._slot(_job(seed_park_with_tracker), seed_park_with_tracker, now) is None


def test_alternating_slot_keeps_anchor_parity_across_year_boundary(seed_park_with_tracker):
    seed_park_with_tracker.timezone = "UTC"
    job = _job(
        seed_park_with_tracker,
        alternate="even",
        anchor_date=date(2025, 12, 31),
    )

    assert (
        native_telegram._slot(job, seed_park_with_tracker, datetime(2026, 1, 1, 9, 5, tzinfo=UTC))
        is None
    )
    assert native_telegram._slot(
        job, seed_park_with_tracker, datetime(2026, 1, 2, 9, 5, tzinfo=UTC)
    ) == datetime(2026, 1, 2, 9, 0, tzinfo=UTC)


def test_once_slot_has_bounded_grace_and_never_changes_to_another_slot(seed_park_with_tracker):
    run_at = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)
    job = _job(
        seed_park_with_tracker,
        schedule="once",
        time=None,
        weekdays="",
        run_at=run_at,
    )

    assert native_telegram._slot(job, seed_park_with_tracker, run_at - timedelta(seconds=1)) is None
    assert (
        native_telegram._slot(job, seed_park_with_tracker, run_at + timedelta(minutes=15)) == run_at
    )
    assert (
        native_telegram._slot(
            job, seed_park_with_tracker, run_at + timedelta(minutes=15, seconds=1)
        )
        is None
    )


def test_once_claim_creates_only_one_delivery(db_session, seed_park_with_tracker, monkeypatch):
    seed_park_with_tracker.timezone = "UTC"
    seed_park_with_tracker.chat_id = -1001234567890
    run_at = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)
    job = _job(
        seed_park_with_tracker,
        schedule="once",
        time=None,
        weekdays="",
        run_at=run_at,
    )
    db_session.add(job)
    db_session.commit()
    monkeypatch.setattr(native_telegram, "utcnow", lambda: run_at + timedelta(minutes=1))

    first = native_telegram.claim(db_session, 10)
    second = native_telegram.claim(db_session, 10)

    assert len(first) == 1
    assert second == []


def test_once_becoming_due_during_long_delivery_pause_is_claimed_after_resume(
    db_session, seed_royal, seed_park_with_tracker, monkeypatch
):
    seed_park_with_tracker.timezone = "UTC"
    seed_park_with_tracker.chat_id = -1001234567890
    now = {"value": datetime(2026, 2, 1, 12, 0, tzinfo=UTC)}
    monkeypatch.setattr(native_telegram, "utcnow", lambda: now["value"])
    run_at = now["value"] + timedelta(minutes=5)
    job = native_telegram.create_job(
        db_session,
        seed_royal,
        BotJobCreate.model_validate(
            _job_payload(
                seed_park_with_tracker.id,
                enabled=True,
                schedule="once",
                time=None,
                weekdays=[],
                run_at=run_at,
            )
        ),
    )
    assert db_session.query(NativeBotDelivery).filter_by(job_id=job.id).count() == 1
    native_telegram_usage.update_control(
        db_session,
        seed_royal,
        queries_paused=False,
        deliveries_paused=True,
        revision=1,
    )

    now["value"] = run_at + timedelta(minutes=30)
    assert native_telegram.claim(db_session, 10) == []
    native_telegram_usage.update_control(
        db_session,
        seed_royal,
        queries_paused=False,
        deliveries_paused=False,
        revision=2,
    )

    claimed = native_telegram.claim(db_session, 10)
    assert len(claimed) == 1
    assert claimed[0]["job"].id == job.id


@pytest.mark.parametrize("manual", [False, True], ids=["once", "manual"])
def test_long_pause_preserves_already_claimed_durable_delivery(
    db_session, seed_royal, seed_park_with_tracker, monkeypatch, manual
):
    seed_park_with_tracker.timezone = "UTC"
    seed_park_with_tracker.chat_id = -1001234567890
    now = {"value": datetime(2026, 2, 1, 12, 0, tzinfo=UTC)}
    monkeypatch.setattr(native_telegram, "utcnow", lambda: now["value"])
    if manual:
        job = _job(seed_park_with_tracker, enabled=False)
        db_session.add(job)
        db_session.commit()
        row, _created = native_telegram.enqueue_manual_run(
            db_session,
            seed_royal,
            job.id,
            revision=1,
            request_id="77777777-7777-4777-8777-777777777777",
            allow_disabled=True,
        )
    else:
        job = _job(
            seed_park_with_tracker,
            schedule="once",
            time=None,
            weekdays="",
            run_at=now["value"],
            updated_at=now["value"] - timedelta(minutes=1),
        )
        db_session.add(job)
        db_session.commit()
        row = None

    first = native_telegram.claim(db_session, 10)
    assert len(first) == 1
    delivery_id = first[0]["id"]
    if row is not None:
        assert delivery_id == row.id
    native_telegram_usage.update_control(
        db_session,
        seed_royal,
        queries_paused=False,
        deliveries_paused=True,
        revision=1,
    )

    now["value"] += timedelta(minutes=30)
    assert native_telegram.claim(db_session, 10) == []
    native_telegram_usage.update_control(
        db_session,
        seed_royal,
        queries_paused=False,
        deliveries_paused=False,
        revision=2,
    )

    resumed = native_telegram.claim(db_session, 10)
    assert [item["id"] for item in resumed] == [delivery_id]
    db_session.expire_all()
    assert db_session.get(NativeBotDelivery, delivery_id).state == "preparing"


def test_long_pause_still_expires_claimed_recurring_slot(
    db_session, seed_royal, seed_park_with_tracker, monkeypatch
):
    seed_park_with_tracker.timezone = "UTC"
    seed_park_with_tracker.chat_id = -1001234567890
    now = {"value": datetime(2026, 2, 1, 12, 5, tzinfo=UTC)}
    monkeypatch.setattr(native_telegram, "utcnow", lambda: now["value"])
    job = _job(seed_park_with_tracker, time="12:00")
    db_session.add(job)
    db_session.commit()
    delivery_id = native_telegram.claim(db_session, 10)[0]["id"]
    native_telegram_usage.update_control(
        db_session,
        seed_royal,
        queries_paused=False,
        deliveries_paused=True,
        revision=1,
    )

    now["value"] += timedelta(minutes=30)
    assert native_telegram.claim(db_session, 10) == []
    native_telegram_usage.update_control(
        db_session,
        seed_royal,
        queries_paused=False,
        deliveries_paused=False,
        revision=2,
    )

    assert native_telegram.claim(db_session, 10) == []
    db_session.expire_all()
    delivery = db_session.get(NativeBotDelivery, delivery_id)
    assert (delivery.state, delivery.error_code) == ("failed", "schedule_expired")


def test_paused_once_rechecks_disabled_configuration_before_resume(
    db_session, seed_royal, seed_park_with_tracker, monkeypatch
):
    seed_park_with_tracker.timezone = "UTC"
    seed_park_with_tracker.chat_id = -1001234567890
    now = {"value": datetime(2026, 2, 1, 12, 0, tzinfo=UTC)}
    monkeypatch.setattr(native_telegram, "utcnow", lambda: now["value"])
    run_at = now["value"] + timedelta(minutes=5)
    job = native_telegram.create_job(
        db_session,
        seed_royal,
        BotJobCreate.model_validate(
            _job_payload(
                seed_park_with_tracker.id,
                enabled=True,
                schedule="once",
                time=None,
                weekdays=[],
                run_at=run_at,
            )
        ),
    )
    native_telegram_usage.update_control(
        db_session,
        seed_royal,
        queries_paused=False,
        deliveries_paused=True,
        revision=1,
    )
    native_telegram.update_job(
        db_session,
        seed_royal,
        job.id,
        BotJobCreate.model_validate(
            _job_payload(
                seed_park_with_tracker.id,
                enabled=False,
                schedule="once",
                time=None,
                weekdays=[],
                run_at=run_at,
            )
        ),
        revision=job.revision,
    )

    now["value"] = run_at + timedelta(minutes=30)
    assert native_telegram.claim(db_session, 10) == []
    native_telegram_usage.update_control(
        db_session,
        seed_royal,
        queries_paused=False,
        deliveries_paused=False,
        revision=2,
    )

    assert native_telegram.claim(db_session, 10) == []
    delivery = db_session.query(NativeBotDelivery).filter_by(job_id=job.id).one()
    assert (delivery.state, delivery.error_code) == ("failed", "delivery_not_ready")


def test_manual_run_is_idempotent_and_edit_invalidates_claim(
    db_session, seed_royal, seed_park_with_tracker, monkeypatch
):
    seed_park_with_tracker.chat_id = -1001234567890
    job = _job(seed_park_with_tracker, enabled=False)
    db_session.add(job)
    db_session.commit()
    now = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
    monkeypatch.setattr(native_telegram, "utcnow", lambda: now)

    first, created = native_telegram.enqueue_manual_run(
        db_session,
        seed_royal,
        job.id,
        revision=1,
        request_id="11111111-1111-4111-8111-111111111111",
        allow_disabled=True,
    )
    repeated, repeated_created = native_telegram.enqueue_manual_run(
        db_session,
        seed_royal,
        job.id,
        revision=1,
        request_id="11111111-1111-4111-8111-111111111111",
        allow_disabled=True,
    )
    assert (repeated.id, repeated_created) == (first.id, False)
    assert created is True
    audit_rows = (
        db_session.query(AuditLog).filter_by(action="admin.native_bot.job.run_queued").all()
    )
    assert [(row.actor_user_id, row.park_id, row.target_id) for row in audit_rows] == [
        (seed_royal.id, seed_park_with_tracker.id, str(first.id))
    ]

    job.title = "Edited"
    job.revision += 1
    db_session.commit()
    assert native_telegram.claim(db_session, 10) == []
    db_session.refresh(first)
    assert (first.state, first.error_code) == ("failed", "configuration_changed")


def test_admin_run_endpoint_returns_durable_manual_delivery(
    client, db_session, seed_royal, seed_park_with_tracker
):
    seed_park_with_tracker.chat_id = -1001234567890
    job = _job(seed_park_with_tracker, enabled=False)
    db_session.add(job)
    db_session.commit()
    login_as(client, "royal", "secret")
    payload = {
        "revision": 1,
        "request_id": "44444444-4444-4444-8444-444444444444",
        "allow_disabled": True,
    }

    first = client.post(f"/admin/bot/native/jobs/{job.id}/run", json=payload)
    repeated = client.post(f"/admin/bot/native/jobs/{job.id}/run", json=payload)

    assert first.status_code == 200
    assert first.json()["created"] is True
    assert first.json()["delivery"]["manual"] is True
    assert first.json()["delivery"]["request_id"] == payload["request_id"]
    assert repeated.json() == {**first.json(), "created": False}


def test_create_rejects_enabled_once_outside_grace(client, seed_royal, seed_park_with_tracker):
    login_as(client, "royal", "secret")
    response = client.post(
        "/admin/bot/native/jobs",
        json=_job_payload(
            seed_park_with_tracker.id,
            enabled=True,
            schedule="once",
            time=None,
            weekdays=[],
            run_at=(datetime.now(UTC) - timedelta(minutes=16)).isoformat(),
        ),
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "once_run_at_expired"


def test_manual_run_replay_rechecks_current_park_scope(
    db_session, seed_royal, seed_admin, seed_park_with_tracker
):
    seed_park_with_tracker.chat_id = -1001234567890
    job = _job(seed_park_with_tracker, enabled=False)
    db_session.add(job)
    db_session.commit()
    request_id = "55555555-5555-4555-8555-555555555555"
    native_telegram.enqueue_manual_run(
        db_session,
        seed_royal,
        job.id,
        revision=1,
        request_id=request_id,
        allow_disabled=True,
    )

    with pytest.raises(HTTPException) as caught:
        native_telegram.enqueue_manual_run(
            db_session,
            seed_admin,
            job.id,
            revision=1,
            request_id=request_id,
            allow_disabled=True,
        )

    assert caught.value.status_code == 403


def test_manual_run_requires_explicit_disabled_override(
    db_session, seed_royal, seed_park_with_tracker
):
    seed_park_with_tracker.chat_id = -1001234567890
    job = _job(seed_park_with_tracker, enabled=False)
    db_session.add(job)
    db_session.commit()

    with pytest.raises(HTTPException) as caught:
        native_telegram.enqueue_manual_run(
            db_session,
            seed_royal,
            job.id,
            revision=1,
            request_id="22222222-2222-4222-8222-222222222222",
            allow_disabled=False,
        )

    assert caught.value.status_code == 409
    assert caught.value.detail == "job_disabled"


def test_manual_run_validates_disabled_draft_as_enabled(
    db_session, seed_royal, seed_park_with_tracker
):
    seed_park_with_tracker.chat_id = -1001234567890
    job = _job(seed_park_with_tracker, enabled=False, text=None)
    db_session.add(job)
    db_session.commit()

    with pytest.raises(HTTPException) as caught:
        native_telegram.enqueue_manual_run(
            db_session,
            seed_royal,
            job.id,
            revision=1,
            request_id="33333333-3333-4333-8333-333333333333",
            allow_disabled=True,
        )

    assert caught.value.status_code == 409
    assert caught.value.detail == "job_invalid"


def test_legacy_planner_groups_and_once_broadcast_preserve_source_timezone(
    db_session, seed_royal, seed_park_with_tracker, monkeypatch
):
    location = {
        "key": "Alpha",
        "display_name": "Alpha",
        "tracker_tag": seed_park_with_tracker.tag,
        "chats": {"prod": None, "test": None},
        "participation": {"hourly_png": False},
    }
    sections = {
        "locations": bot_shared_settings.Section(
            revision="locations-v1",
            value={"locations": [location], "metadata": {}},
        ),
        "schedules": bot_shared_settings.Section(
            revision="schedules-v1",
            value={
                "timezone": "Europe/Moscow",
                "planner_anchor": "2026-08-07",
                "send_window": {"start_hour": 9, "end_hour": 21},
                "jobs": [
                    {
                        "id": "custom_ab",
                        "label": "Custom A/B",
                        "enabled": True,
                        "fire_at": "09:00",
                        "weekdays": None,
                        "kind": "planner_ab",
                        "text": "Join {link}",
                        "groups": {
                            "A": {
                                "locations": ["Alpha"],
                                "link": "https://example.test/a",
                            },
                            "B": {
                                "locations": ["Alpha"],
                                "link": "https://example.test/b",
                            },
                        },
                    }
                ],
            },
        ),
        "broadcasts": bot_shared_settings.Section(
            revision="broadcasts-v1",
            value={
                "campaigns": [
                    {
                        "id": "custom_once",
                        "label": "One time",
                        "text": "Only once",
                        "enabled": True,
                        "fire_at": "10:00",
                        "repeat": "once",
                        "created_at": "2026-10-07T06:00:00Z",
                        "location_mode": "keys",
                        "location_keys": ["Alpha"],
                    }
                ],
                "removed_ids": [],
            },
        ),
        "campaigns": bot_shared_settings.Section(revision="campaigns-v1", value={"campaigns": []}),
    }
    monkeypatch.setattr(
        native_telegram_migration.bot_shared_settings,
        "read_legacy_sources",
        lambda _path: [("flat", sections)],
    )

    plan = native_telegram_migration.preview(db_session, "/unused")

    assert plan["conflicts"] == []
    assert len(plan["jobs"]) == 3
    planner = sorted(
        (item for item in plan["jobs"] if item["source_id"] == "custom_ab"),
        key=lambda item: item["alternate"],
    )
    assert [(item["alternate"], item["url"]) for item in planner] == [
        ("even", "https://example.test/a"),
        ("odd", "https://example.test/b"),
    ]
    assert len({item["source_ref"] for item in planner}) == 2
    once = next(item for item in plan["jobs"] if item["source_id"] == "custom_once")
    assert once["timezone"] == "Europe/Moscow"
    assert once["schedule"] == "once"
    assert once["run_at"] == datetime(2026, 10, 7, 7, 0, tzinfo=UTC)
    assert all(item["timezone"] == "Europe/Moscow" for item in plan["jobs"])

    native_telegram_migration.apply(db_session, seed_royal, "/unused", plan["fingerprint"])
    imported = db_session.query(NativeBotJob).order_by(NativeBotJob.source_ref).all()
    assert len(imported) == 3
    assert all(item.enabled is False for item in imported)
    assert {item.timezone for item in imported} == {"Europe/Moscow"}
    imported_once = next(item for item in imported if item.schedule == "once")
    assert native_telegram._aware(imported_once.run_at) == datetime(2026, 10, 7, 7, 0, tzinfo=UTC)
