from datetime import UTC, datetime, timedelta

from conftest import login_as, role_id_for
from robopark_api.models import AccessStatus, Park, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services import schedules
from robopark_api.services.rbac import RoleSlug


def _add_user(db, *, username: str, role: str, park_id: int | None = None) -> User:
    user = User(
        username=username,
        password_hash=hash_password("secret"),
        role_id=role_id_for(db, role),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db.add(user)
    db.flush()
    if park_id is not None:
        db.add(UserPark(user_id=user.id, park_id=park_id))
    db.commit()
    db.refresh(user)
    return user


def _interval(kind: str = "shift") -> dict:
    return {
        "kind": kind,
        "start_at": "2026-09-21T09:00:00+03:00",
        "end_at": "2026-09-21T21:00:00+03:00",
    }


def test_employee_manages_own_schedule_and_gets_overlap_warning(
    client, db_session, seed_mechanic, seed_park_with_tracker
):
    login_as(client, seed_mechanic.username, "secret")
    created = client.post("/schedules", json={**_interval(), "park_id": seed_park_with_tracker.id})
    assert created.status_code == 201
    assert created.json()["owner_user_id"] == seed_mechanic.id
    assert created.json()["created_by_user_id"] == seed_mechanic.id

    overlap = client.post(
        "/schedules", json={**_interval("vacation"), "park_id": seed_park_with_tracker.id}
    )
    assert overlap.status_code == 201
    assert overlap.json()["warnings"] == ["overlap"]

    other = _add_user(
        db_session, username="mech2", role=RoleSlug.MECHANIC, park_id=seed_park_with_tracker.id
    )
    denied = client.post(
        "/schedules",
        json={**_interval(), "park_id": seed_park_with_tracker.id, "owner_user_id": other.id},
    )
    assert denied.status_code == 403


def test_admin_reads_only_accessible_parks_while_royal_bulk_assigns(
    client, db_session, seed_admin, seed_royal, seed_park_with_tracker
):
    second = Park(name="Beta", tag="Beta", is_active=True, tracker_queue="BETA")
    db_session.add(second)
    db_session.flush()
    db_session.add(UserPark(user_id=seed_admin.id, park_id=seed_park_with_tracker.id))
    mechanic = _add_user(
        db_session, username="bulk-mech", role=RoleSlug.MECHANIC, park_id=seed_park_with_tracker.id
    )
    foreign_mechanic = _add_user(
        db_session, username="foreign-mech", role=RoleSlug.MECHANIC, park_id=second.id
    )

    login_as(client, seed_royal.username, "secret")
    response = client.post(
        "/schedules/bulk",
        json={
            "owner_user_ids": [mechanic.id],
            "park_id": seed_park_with_tracker.id,
            **_interval(),
            "repeat_count": 2,
            "repeat_every_days": 7,
        },
    )
    assert response.status_code == 201
    assert len(response.json()) == 2
    assert response.json()[0]["source"] == "royal"
    assert response.json()[0]["series_id"] == response.json()[1]["series_id"]
    series_id = response.json()[0]["series_id"]
    copied = client.post(
        "/schedules/copy",
        json={
            "park_id": seed_park_with_tracker.id,
            "source_start": "2026-09-21T00:00:00+03:00",
            "source_end": "2026-10-01T00:00:00+03:00",
            "target_start": "2026-10-01T00:00:00+03:00",
            "owner_user_ids": [mechanic.id],
        },
    )
    assert copied.status_code == 201
    assert len(copied.json()) == 2
    assert client.delete(f"/schedules/series/{series_id}").json() == {"deleted": 2}
    assert (
        client.post(
            "/schedules",
            json={**_interval(), "park_id": seed_park_with_tracker.id, "owner_user_id": 999999},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/schedules/bulk",
            json={
                "owner_user_ids": [foreign_mechanic.id],
                "park_id": seed_park_with_tracker.id,
                **_interval(),
            },
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/schedules/copy",
            json={
                "park_id": seed_park_with_tracker.id,
                "source_start": "2026-09-21T00:00:00+03:00",
                "source_end": "2026-10-01T00:00:00+03:00",
                "target_start": "2026-10-01T00:00:00+03:00",
                "owner_user_ids": [foreign_mechanic.id],
            },
        ).status_code
        == 403
    )

    login_as(client, seed_admin.username, "secret")
    assert client.get(f"/schedules?park_id={seed_park_with_tracker.id}").status_code == 200
    assert client.get(f"/schedules?park_id={second.id}").status_code == 403
    assert (
        client.post(
            "/schedules", json={**_interval(), "park_id": seed_park_with_tracker.id}
        ).status_code
        == 403
    )

    rows = client.get(f"/schedules?park_id={seed_park_with_tracker.id}").json()
    assert rows[0]["updated_at"]
    datetime.fromisoformat(rows[0]["updated_at"])


def test_old_schedule_cleanup_is_bounded_and_keeps_current_entries(
    db_session, seed_mechanic, seed_park_with_tracker
):
    from robopark_api.schedule_models import ScheduleEntry

    old = ScheduleEntry(
        owner_user_id=seed_mechanic.id,
        park_id=seed_park_with_tracker.id,
        kind="shift",
        start_at=datetime.fromisoformat("2024-01-01T09:00:00+03:00"),
        end_at=datetime.fromisoformat("2024-01-01T21:00:00+03:00"),
        created_by_user_id=seed_mechanic.id,
        updated_by_user_id=seed_mechanic.id,
    )
    current = ScheduleEntry(
        owner_user_id=seed_mechanic.id,
        park_id=seed_park_with_tracker.id,
        kind="shift",
        start_at=datetime.fromisoformat("2026-09-20T09:00:00+03:00"),
        end_at=datetime.fromisoformat("2026-09-20T21:00:00+03:00"),
        created_by_user_id=seed_mechanic.id,
        updated_by_user_id=seed_mechanic.id,
    )
    db_session.add_all([old, current])
    db_session.commit()
    old_id, current_id = old.id, current.id
    assert (
        schedules.prune_old_entries(
            db_session, now=datetime.fromisoformat("2026-09-20T12:00:00+00:00")
        )
        == 1
    )
    assert db_session.get(ScheduleEntry, old_id) is None
    assert db_session.get(ScheduleEntry, current_id) is not None


def test_list_schedule_range_is_bounded_and_ordered(
    client, db_session, seed_admin, seed_mechanic, seed_park_with_tracker
):
    from robopark_api.schedule_models import ScheduleEntry

    db_session.add(UserPark(user_id=seed_admin.id, park_id=seed_park_with_tracker.id))
    range_start = datetime.fromisoformat("2026-09-01T00:00:00+03:00")
    rows = []
    for offset in reversed(range(2005)):
        start_at = range_start + timedelta(minutes=offset)
        rows.append(
            ScheduleEntry(
                owner_user_id=seed_mechanic.id,
                park_id=seed_park_with_tracker.id,
                kind="shift",
                start_at=start_at,
                end_at=start_at + timedelta(seconds=30),
                created_by_user_id=seed_mechanic.id,
                updated_by_user_id=seed_mechanic.id,
            )
        )
    db_session.add_all(rows)
    db_session.commit()
    login_as(client, seed_admin.username, "secret")

    response = client.get(
        f"/schedules?park_id={seed_park_with_tracker.id}"
        "&start_at=2026-09-01T00:00:00%2B03:00"
        "&end_at=2026-10-01T00:00:00%2B03:00"
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 2000
    assert [row["start_at"] for row in payload] == sorted(row["start_at"] for row in payload)
    assert payload[0]["start_at"] == "2026-09-01T00:00:00"
    assert payload[-1]["start_at"] == "2026-09-02T09:19:00"


def test_active_operator_prefers_current_shift_then_username(
    db_session, seed_park_with_tracker
):
    from robopark_api.schedule_models import ScheduleEntry

    now = datetime(2026, 9, 23, 9, 0, tzinfo=UTC)
    fallback = _add_user(
        db_session,
        username="aaa-fallback",
        role=RoleSlug.OPERATOR,
        park_id=seed_park_with_tracker.id,
    )
    on_shift = _add_user(
        db_session,
        username="zzz-on-shift",
        role=RoleSlug.OPERATOR,
        park_id=seed_park_with_tracker.id,
    )
    db_session.add(
        ScheduleEntry(
            owner_user_id=on_shift.id,
            park_id=seed_park_with_tracker.id,
            kind="shift",
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=1),
            created_by_user_id=on_shift.id,
            updated_by_user_id=on_shift.id,
        )
    )
    db_session.commit()

    chosen = schedules.resolve_active_operator(
        db_session, park_id=seed_park_with_tracker.id, at=now
    )

    assert chosen is not None
    assert chosen.id == on_shift.id
    db_session.delete(on_shift)
    db_session.commit()
    assert schedules.resolve_active_operator(
        db_session, park_id=seed_park_with_tracker.id, at=now
    ).id == fallback.id
