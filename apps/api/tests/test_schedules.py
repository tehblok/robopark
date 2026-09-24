from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from conftest import login_as, role_id_for
from robopark_api.models import AccessStatus, Park, User, UserPark
from robopark_api.schedule_schemas import SchedulePatternCreate
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


def _pattern_payload(park_id: int, owner_ids: list[int], **overrides) -> dict:
    payload = {
        "park_id": park_id,
        "owner_user_ids": owner_ids,
        "kind": "shift",
        "start_date": "2026-09-03",
        "end_date": "2026-09-14",
        "start_time": "09:00:00",
        "end_time": "21:00:00",
        "pattern": "4/4",
        "idempotency_key": "pattern-request-0001",
    }
    payload.update(overrides)
    return payload


def test_four_on_four_off_pattern_dates(
    client, db_session, seed_royal, seed_park_with_tracker
):
    mechanic = _add_user(
        db_session,
        username="pattern-mech",
        role=RoleSlug.MECHANIC,
        park_id=seed_park_with_tracker.id,
    )
    login_as(client, seed_royal.username, "secret")

    response = client.post(
        "/schedules/pattern",
        json=_pattern_payload(seed_park_with_tracker.id, [mechanic.id]),
    )

    assert response.status_code == 201
    rows = response.json()
    assert [row["start_at"][:10] for row in rows] == [
        "2026-09-03",
        "2026-09-04",
        "2026-09-05",
        "2026-09-06",
        "2026-09-11",
        "2026-09-12",
        "2026-09-13",
        "2026-09-14",
    ]
    assert len({row["series_id"] for row in rows}) == 1
    assert rows[0]["series_id"] is not None


@pytest.mark.parametrize(
    ("pattern", "expected_dates"),
    [
        ("5/2", ["2026-09-03", "2026-09-04", "2026-09-05", "2026-09-06", "2026-09-07", "2026-09-10", "2026-09-11"]),
        ("2/2", ["2026-09-03", "2026-09-04", "2026-09-07", "2026-09-08", "2026-09-11"]),
    ],
)
def test_pattern_exact_dates_and_moscow_wall_times(
    client, db_session, seed_royal, seed_park_with_tracker, pattern, expected_dates
):
    mechanic = _add_user(
        db_session,
        username=f"pattern-{pattern.replace('/', '-')}",
        role=RoleSlug.MECHANIC,
        park_id=seed_park_with_tracker.id,
    )
    login_as(client, seed_royal.username, "secret")

    response = client.post(
        "/schedules/pattern",
        json=_pattern_payload(
            seed_park_with_tracker.id,
            [mechanic.id],
            pattern=pattern,
            end_date="2026-09-11",
            start_time="00:30:00",
            end_time="08:30:00",
            idempotency_key=f"pattern-{pattern}-0001",
        ),
    )

    assert response.status_code == 201
    rows = response.json()
    assert [row["start_at"][:10] for row in rows] == expected_dates
    assert all(row["start_at"].endswith("00:30:00+03:00") for row in rows)
    assert all(row["end_at"].endswith("08:30:00+03:00") for row in rows)


def test_pattern_replay_is_idempotent_and_payload_bound(
    client, db_session, seed_royal, seed_park_with_tracker
):
    owner = _add_user(
        db_session,
        username="pattern-replay",
        role=RoleSlug.MECHANIC,
        park_id=seed_park_with_tracker.id,
    )
    payload = _pattern_payload(seed_park_with_tracker.id, [owner.id])
    login_as(client, seed_royal.username, "secret")

    first = client.post("/schedules/pattern", json=payload)
    owner.is_active = False
    db_session.commit()
    replay = client.post("/schedules/pattern", json=payload)
    conflict = client.post(
        "/schedules/pattern",
        json={**payload, "end_date": "2026-09-15"},
    )

    assert first.status_code == replay.status_code == 201
    assert replay.json() == first.json()
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "reliable_action_payload_conflict"

    from robopark_api.schedule_models import ScheduleEntry

    assert db_session.query(ScheduleEntry).count() == 8


def test_pattern_retry_after_ambiguous_commit_replays_without_duplicates(
    client, db_session, monkeypatch, seed_royal, seed_park_with_tracker
):
    owner = _add_user(
        db_session,
        username="pattern-ambiguous",
        role=RoleSlug.MECHANIC,
        park_id=seed_park_with_tracker.id,
    )
    payload = _pattern_payload(
        seed_park_with_tracker.id,
        [owner.id],
        idempotency_key="pattern-ambiguous-0001",
    )
    login_as(client, seed_royal.username, "secret")
    real_commit = db_session.commit
    raised = False

    def ambiguous_commit():
        nonlocal raised
        real_commit()
        if not raised:
            raised = True
            raise RuntimeError("response_lost_after_commit")

    monkeypatch.setattr(db_session, "commit", ambiguous_commit)
    with pytest.raises(RuntimeError, match="response_lost_after_commit"):
        client.post("/schedules/pattern", json=payload)
    monkeypatch.setattr(db_session, "commit", real_commit)

    replay = client.post("/schedules/pattern", json=payload)

    assert replay.status_code == 201
    assert len(replay.json()) == 8
    from robopark_api.schedule_models import ScheduleEntry

    assert db_session.query(ScheduleEntry).count() == 8


def test_concurrent_pattern_replay_inserts_one_plan(
    db_engine, db_session, seed_royal, seed_park_with_tracker
):
    owner = _add_user(
        db_session,
        username="pattern-concurrent",
        role=RoleSlug.OPERATOR,
        park_id=seed_park_with_tracker.id,
    )
    actor_id = seed_royal.id
    park_id = seed_park_with_tracker.id
    owner_id = owner.id
    db_session.expunge_all()
    barrier = Barrier(2)

    def create_once():
        with Session(db_engine) as session:
            actor = session.get(User, actor_id)
            assert actor is not None
            barrier.wait()
            return schedules.create_pattern(
                session,
                actor,
                SchedulePatternCreate.model_validate(
                    _pattern_payload(park_id, [owner_id], idempotency_key="pattern-race-0001")
                ),
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: create_once(), range(2)))

    assert [[row["id"] for row in result] for result in results] == [
        [row["id"] for row in results[0]],
        [row["id"] for row in results[0]],
    ]
    from robopark_api.schedule_models import ScheduleEntry

    with Session(db_engine) as session:
        assert session.query(ScheduleEntry).count() == 8


def test_pattern_is_royal_only_and_rejects_invalid_owner_or_range(
    client, db_session, seed_mechanic, seed_royal, seed_park_with_tracker
):
    owner = _add_user(
        db_session,
        username="pattern-owner",
        role=RoleSlug.OPERATOR,
        park_id=seed_park_with_tracker.id,
    )
    foreign_park = Park(name="Pattern foreign", tag="PATTERN-FOREIGN", is_active=True)
    db_session.add(foreign_park)
    db_session.flush()
    foreign_owner = _add_user(
        db_session,
        username="pattern-foreign",
        role=RoleSlug.MECHANIC,
        park_id=foreign_park.id,
    )

    login_as(client, seed_mechanic.username, "secret")
    assert client.post(
        "/schedules/pattern",
        json=_pattern_payload(seed_park_with_tracker.id, [owner.id]),
    ).status_code == 403

    login_as(client, seed_royal.username, "secret")
    assert client.post(
        "/schedules/pattern",
        json=_pattern_payload(seed_park_with_tracker.id, [owner.id, owner.id]),
    ).status_code == 422
    assert client.post(
        "/schedules/pattern",
        json=_pattern_payload(
            seed_park_with_tracker.id,
            [owner.id],
            start_date="2026-09-15",
            end_date="2026-09-14",
        ),
    ).status_code == 422
    assert client.post(
        "/schedules/pattern",
        json=_pattern_payload(
            seed_park_with_tracker.id,
            [owner.id],
            start_date="2026-01-01",
            end_date="2027-01-02",
        ),
    ).status_code == 422
    assert client.post(
        "/schedules/pattern",
        json=_pattern_payload(seed_park_with_tracker.id, [foreign_owner.id]),
    ).status_code == 403


def test_pattern_rejects_forged_inactive_pending_and_nonstaff_owners(
    client, db_session, seed_royal, seed_park_with_tracker
):
    inactive = _add_user(
        db_session,
        username="pattern-inactive",
        role=RoleSlug.MECHANIC,
        park_id=seed_park_with_tracker.id,
    )
    inactive.is_active = False
    pending = _add_user(
        db_session,
        username="pattern-pending",
        role=RoleSlug.OPERATOR,
        park_id=seed_park_with_tracker.id,
    )
    pending.access_status = AccessStatus.pending.value
    nonstaff = _add_user(
        db_session,
        username="pattern-admin",
        role=RoleSlug.ADMIN,
        park_id=seed_park_with_tracker.id,
    )
    db_session.commit()
    login_as(client, seed_royal.username, "secret")

    for index, owner in enumerate((inactive, pending, nonstaff)):
        response = client.post(
            "/schedules/pattern",
            json=_pattern_payload(
                seed_park_with_tracker.id,
                [owner.id],
                idempotency_key=f"forged-owner-{index:04d}",
            ),
        )
        assert response.status_code == 403


def test_pattern_rejects_owner_and_generated_entry_limits_before_insert(
    client, db_session, seed_royal, seed_park_with_tracker
):
    login_as(client, seed_royal.username, "secret")
    too_many_owners = list(range(1, 52))
    assert client.post(
        "/schedules/pattern",
        json=_pattern_payload(seed_park_with_tracker.id, too_many_owners),
    ).status_code == 422
    assert client.post(
        "/schedules/pattern",
        json=_pattern_payload(
            seed_park_with_tracker.id,
            list(range(10_000, 10_050)),
            start_date="2026-01-01",
            end_date="2026-12-31",
        ),
    ).status_code == 422

    from robopark_api.schedule_models import ScheduleEntry

    assert db_session.query(ScheduleEntry).count() == 0


def test_pattern_accepts_exactly_5000_entries(
    client, db_session, seed_royal, seed_park_with_tracker
):
    owners = [
        _add_user(
            db_session,
            username=f"pattern-limit-{index:02d}",
            role=RoleSlug.MECHANIC,
            park_id=seed_park_with_tracker.id,
        )
        for index in range(50)
    ]
    login_as(client, seed_royal.username, "secret")

    response = client.post(
        "/schedules/pattern",
        json=_pattern_payload(
            seed_park_with_tracker.id,
            [owner.id for owner in owners],
            pattern="5/2",
            start_date="2026-01-01",
            end_date="2026-05-20",
            idempotency_key="pattern-limit-5000",
        ),
    )

    assert response.status_code == 201
    assert len(response.json()) == 5000


def test_pattern_accepts_exactly_366_calendar_days(
    client, db_session, seed_royal, seed_park_with_tracker
):
    owner = _add_user(
        db_session,
        username="pattern-366-days",
        role=RoleSlug.OPERATOR,
        park_id=seed_park_with_tracker.id,
    )
    login_as(client, seed_royal.username, "secret")

    response = client.post(
        "/schedules/pattern",
        json=_pattern_payload(
            seed_park_with_tracker.id,
            [owner.id],
            pattern="2/2",
            start_date="2026-01-01",
            end_date="2027-01-01",
            idempotency_key="pattern-limit-366-days",
        ),
    )

    assert response.status_code == 201
    assert len(response.json()) == 184


def test_pattern_response_schedule_query_count_is_constant(
    client, db_session, db_engine, seed_royal, seed_park_with_tracker
):
    owner = _add_user(
        db_session,
        username="pattern-query-count",
        role=RoleSlug.MECHANIC,
        park_id=seed_park_with_tracker.id,
    )
    schedule_selects = []

    def count_schedule_selects(_connection, _cursor, statement, *_args):
        normalized = statement.upper()
        if normalized.lstrip().startswith("SELECT") and "FROM SCHEDULE_ENTRIES" in normalized:
            schedule_selects.append(statement)

    event.listen(db_engine, "before_cursor_execute", count_schedule_selects)
    try:
        login_as(client, seed_royal.username, "secret")
        response = client.post(
            "/schedules/pattern",
            json=_pattern_payload(seed_park_with_tracker.id, [owner.id]),
        )
    finally:
        event.remove(db_engine, "before_cursor_execute", count_schedule_selects)

    assert response.status_code == 201
    assert len(response.json()) == 8
    assert len(schedule_selects) <= 2


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


def test_copy_period_is_complete_ordered_and_idempotent(
    client, db_session, seed_royal, seed_mechanic, seed_park_with_tracker
):
    from robopark_api.schedule_models import ScheduleEntry

    source_start = datetime.fromisoformat("2026-09-01T00:00:00+03:00")
    source_rows = [
        ScheduleEntry(
            id=f"source-{index:03d}",
            owner_user_id=seed_mechanic.id,
            park_id=seed_park_with_tracker.id,
            kind="shift",
            start_at=source_start + timedelta(minutes=index),
            end_at=source_start + timedelta(minutes=index, seconds=30),
            created_by_user_id=seed_royal.id,
            updated_by_user_id=seed_royal.id,
        )
        for index in reversed(range(101))
    ]
    db_session.add_all(source_rows)
    db_session.commit()
    login_as(client, seed_royal.username, "secret")
    payload = {
        "idempotency_key": "copy-complete-0001",
        "park_id": seed_park_with_tracker.id,
        "source_start": "2026-09-01T00:00:00+03:00",
        "source_end": "2026-09-02T00:00:00+03:00",
        "target_start": "2026-10-01T00:00:00+03:00",
        "owner_user_ids": [seed_mechanic.id],
    }

    first = client.post("/schedules/copy", json=payload)
    replay = client.post("/schedules/copy", json=payload)

    assert first.status_code == replay.status_code == 201
    assert replay.json() == first.json()
    copied = first.json()
    assert len(copied) == 101
    assert [row["start_at"] for row in copied] == sorted(row["start_at"] for row in copied)
    assert copied[0]["start_at"] == "2026-10-01T00:00:00+03:00"
    assert copied[-1]["start_at"] == "2026-10-01T01:40:00+03:00"
    assert len({row["series_id"] for row in copied}) == 1
    assert copied[0]["series_id"] is not None
    assert db_session.query(ScheduleEntry).count() == 202


def test_copy_period_rejects_more_than_5000_rows_before_insert(
    client, db_session, seed_royal, seed_mechanic, seed_park_with_tracker
):
    from robopark_api.schedule_models import ScheduleEntry

    source_start = datetime.fromisoformat("2026-09-01T00:00:00+03:00")
    db_session.add_all([
        ScheduleEntry(
            owner_user_id=seed_mechanic.id,
            park_id=seed_park_with_tracker.id,
            kind="shift",
            start_at=source_start + timedelta(seconds=index),
            end_at=source_start + timedelta(seconds=index + 1),
            created_by_user_id=seed_royal.id,
            updated_by_user_id=seed_royal.id,
        )
        for index in range(5001)
    ])
    db_session.commit()
    login_as(client, seed_royal.username, "secret")

    response = client.post("/schedules/copy", json={
        "idempotency_key": "copy-over-limit-0001",
        "park_id": seed_park_with_tracker.id,
        "source_start": "2026-09-01T00:00:00+03:00",
        "source_end": "2026-09-02T00:00:00+03:00",
        "target_start": "2026-10-01T00:00:00+03:00",
        "owner_user_ids": [seed_mechanic.id],
    })

    assert response.status_code == 400
    assert response.json()["detail"] == "too_many_entries"
    assert db_session.query(ScheduleEntry).count() == 5001


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


def test_list_schedule_range_is_explicitly_paginated_without_gaps_or_duplicates(
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

    first = client.get(
        f"/schedules?park_id={seed_park_with_tracker.id}"
        "&start_at=2026-09-01T00:00:00%2B03:00"
        "&end_at=2026-10-01T00:00:00%2B03:00&limit=2000"
    )

    assert first.status_code == 200
    assert first.headers["x-schedule-has-more"] == "true"
    assert first.headers["x-schedule-next-start-at"] == "2026-09-02T09:19:00+03:00"
    assert first.headers["x-schedule-next-id"]
    second = client.get("/schedules", params={
        "park_id": seed_park_with_tracker.id,
        "start_at": "2026-09-01T00:00:00+03:00",
        "end_at": "2026-10-01T00:00:00+03:00",
        "limit": 2000,
        "after_start_at": first.headers["x-schedule-next-start-at"],
        "after_id": first.headers["x-schedule-next-id"],
    })

    assert second.status_code == 200
    assert second.headers["x-schedule-has-more"] == "false"
    payload = [*first.json(), *second.json()]
    assert len(payload) == 2005
    assert len({row["id"] for row in payload}) == 2005
    assert [row["start_at"] for row in payload] == sorted(row["start_at"] for row in payload)
    assert payload[0]["start_at"] == "2026-09-01T00:00:00+03:00"
    assert payload[-1]["start_at"] == "2026-09-02T09:24:00+03:00"


def test_list_schedule_range_uses_half_open_intersection(
    client, db_session, seed_admin, seed_mechanic, seed_park_with_tracker
):
    from robopark_api.schedule_models import ScheduleEntry

    db_session.add(UserPark(user_id=seed_admin.id, park_id=seed_park_with_tracker.id))
    window_start = datetime.fromisoformat("2026-09-01T00:00:00+03:00")
    window_end = datetime.fromisoformat("2026-10-01T00:00:00+03:00")
    ending_at_start = ScheduleEntry(
        owner_user_id=seed_mechanic.id, park_id=seed_park_with_tracker.id, kind="shift",
        start_at=window_start - timedelta(hours=1), end_at=window_start,
        created_by_user_id=seed_mechanic.id, updated_by_user_id=seed_mechanic.id,
    )
    crossing_start = ScheduleEntry(
        owner_user_id=seed_mechanic.id, park_id=seed_park_with_tracker.id, kind="shift",
        start_at=window_start - timedelta(minutes=1), end_at=window_start + timedelta(minutes=1),
        created_by_user_id=seed_mechanic.id, updated_by_user_id=seed_mechanic.id,
    )
    starting_at_end = ScheduleEntry(
        owner_user_id=seed_mechanic.id, park_id=seed_park_with_tracker.id, kind="shift",
        start_at=window_end, end_at=window_end + timedelta(hours=1),
        created_by_user_id=seed_mechanic.id, updated_by_user_id=seed_mechanic.id,
    )
    db_session.add_all([ending_at_start, crossing_start, starting_at_end])
    db_session.commit()
    login_as(client, seed_admin.username, "secret")

    response = client.get(
        f"/schedules?park_id={seed_park_with_tracker.id}"
        "&start_at=2026-09-01T00:00:00%2B03:00"
        "&end_at=2026-10-01T00:00:00%2B03:00"
    )

    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == [crossing_start.id]


def test_schedule_participants_are_minimal_park_scoped_and_privileged(
    client, db_session, seed_admin, seed_royal, seed_mechanic, seed_park_with_tracker
):
    second = Park(name="Participant foreign", tag="PART-FOREIGN", is_active=True)
    db_session.add(second)
    db_session.flush()
    db_session.add(UserPark(user_id=seed_admin.id, park_id=seed_park_with_tracker.id))
    operator = _add_user(
        db_session, username="participant-operator", role=RoleSlug.OPERATOR,
        park_id=seed_park_with_tracker.id,
    )
    operator.tracker_login = "sensitive-tracker"
    operator.last_ip = "192.0.2.1"
    inactive = _add_user(
        db_session, username="participant-inactive", role=RoleSlug.MECHANIC,
        park_id=seed_park_with_tracker.id,
    )
    inactive.is_active = False
    pending = _add_user(
        db_session, username="participant-pending", role=RoleSlug.MECHANIC,
        park_id=seed_park_with_tracker.id,
    )
    pending.access_status = AccessStatus.pending.value
    foreign = _add_user(
        db_session, username="participant-foreign", role=RoleSlug.OPERATOR, park_id=second.id,
    )
    db_session.commit()

    login_as(client, seed_admin.username, "secret")
    response = client.get(f"/schedules/participants?park_id={seed_park_with_tracker.id}")
    assert response.status_code == 200
    payload = response.json()
    assert operator.id in {row["id"] for row in payload}
    assert seed_mechanic.id in {row["id"] for row in payload}
    assert inactive.id not in {row["id"] for row in payload}
    assert pending.id not in {row["id"] for row in payload}
    assert foreign.id not in {row["id"] for row in payload}
    assert all(set(row) == {"id", "display_name", "role"} for row in payload)
    assert client.get(f"/schedules/participants?park_id={second.id}").status_code == 403

    login_as(client, seed_mechanic.username, "secret")
    assert client.get(f"/schedules/participants?park_id={seed_park_with_tracker.id}").status_code == 403

    login_as(client, seed_royal.username, "secret")
    royal_response = client.get(f"/schedules/participants?park_id={second.id}")
    assert royal_response.status_code == 200
    assert [row["id"] for row in royal_response.json()] == [foreign.id]


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
