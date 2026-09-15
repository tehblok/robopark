"""Reliable actions reserve work transactionally and retry it safely."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from robopark_api.collaboration_models import TrackerPresence
from robopark_api.models import User
from robopark_api.task_workflow_models import ReliableAction


def _begin(db, actor, *, key="claim-0001", payload=None):
    from robopark_api.services.reliable_actions import begin_action

    return begin_action(
        db,
        actor=actor,
        resource_type="tracker_issue",
        resource_id="SDCFLEETOPS-1",
        action="claim",
        idempotency_key=key,
        payload=payload if payload is not None else {"owner_user_id": actor.id},
    )


@pytest.mark.parametrize("key", [None, "short"])
def test_begin_action_rejects_missing_or_short_idempotency_key(db_session, seed_mechanic, key):
    with pytest.raises(HTTPException) as caught:
        _begin(db_session, seed_mechanic, key=key)

    assert caught.value.status_code == 400


def test_begin_action_replays_saved_result_for_identical_request(db_session, seed_mechanic):
    from robopark_api.services.reliable_actions import complete_action

    first = _begin(db_session, seed_mechanic)
    complete_action(db_session, first.row, {"claimed": True})
    db_session.commit()

    replay = _begin(db_session, seed_mechanic)

    assert replay.row.id == first.row.id
    assert replay.result == {"claimed": True}
    assert replay.created is False


def test_begin_action_rejects_same_key_with_changed_payload(db_session, seed_mechanic):
    _begin(db_session, seed_mechanic)

    with pytest.raises(HTTPException) as caught:
        _begin(db_session, seed_mechanic, payload={"owner_user_id": -1})

    assert caught.value.status_code == 409


def test_begin_action_only_flushes_so_caller_can_rollback(db_session, seed_mechanic):
    result = _begin(db_session, seed_mechanic)
    action_id = result.row.id

    db_session.rollback()

    assert db_session.get(ReliableAction, action_id) is None


def test_two_sessions_racing_on_same_key_create_one_action(db_engine, seed_mechanic):
    actor_id = seed_mechanic.id
    barrier = threading.Barrier(2)

    def reserve():
        with Session(db_engine) as db:
            actor = db.get(User, actor_id)
            barrier.wait(timeout=5)
            try:
                result = _begin(db, actor)
                db.commit()
                return result.row.id
            except HTTPException as error:
                db.rollback()
                return error.detail

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reserve(), range(2)))

    with Session(db_engine) as db:
        rows = db.query(ReliableAction).all()
    assert len(rows) == 1
    assert rows[0].id in results


def test_uniqueness_loser_preserves_callers_outer_transaction(db_engine, seed_mechanic):
    actor_id = seed_mechanic.id
    loser_selected = threading.Event()
    winner_committed = threading.Event()

    class PausingSession(Session):
        paused = False

        def scalar(self, statement, *args, **kwargs):
            result = super().scalar(statement, *args, **kwargs)
            if not self.paused and "reliable_actions" in str(statement):
                self.paused = True
                loser_selected.set()
                assert winner_committed.wait(timeout=5)
            return result

    def lose_race():
        with PausingSession(db_engine, autoflush=False) as db:
            actor = db.get(User, actor_id)
            db.add(TrackerPresence(issue_key="LOCAL-1", actor_id=actor_id, expires_at=999.0))
            with pytest.raises(HTTPException) as caught:
                _begin(db, actor, key="shared-race-key")
            assert caught.value.detail == "reliable_action_uncertain"
            db.commit()

    with ThreadPoolExecutor(max_workers=1) as pool:
        loser = pool.submit(lose_race)
        assert loser_selected.wait(timeout=5)
        with Session(db_engine) as db:
            actor = db.get(User, actor_id)
            _begin(db, actor, key="shared-race-key")
            db.commit()
        winner_committed.set()
        loser.result(timeout=5)

    with Session(db_engine) as db:
        assert db.get(TrackerPresence, 1).issue_key == "LOCAL-1"
        assert db.query(ReliableAction).count() == 1


def test_payload_is_stored_as_canonical_utf8_json(db_session, seed_mechanic):
    result = _begin(db_session, seed_mechanic, payload={"z": "мотор", "a": 1})

    assert result.row.payload_json == '{"a":1,"z":"мотор"}'


def test_claim_due_batch_orders_caps_and_reclaims_expired_leases(db_session, seed_mechanic):
    from robopark_api.services.reliable_actions import claim_due_batch

    rows = []
    for index in range(22):
        row = ReliableAction(
            actor_user_id=seed_mechanic.id,
            resource_type="tracker_issue",
            resource_id=f"TASK-{index}",
            action="comment",
            idempotency_key=f"comment-{index:04d}",
            payload_hash="0" * 64,
            payload_json="{}",
            state="sending" if index == 0 else "retry_wait",
            next_attempt_at=float(index),
            lease_until=99.0 if index == 0 else None,
            created_at=1.0,
            updated_at=1.0,
        )
        db_session.add(row)
        rows.append(row)
    db_session.commit()

    claimed = claim_due_batch(db_session, now=100.0, limit=50)

    assert claimed == rows[:20]
    assert all(row.state == "sending" and row.lease_until == 160.0 for row in claimed)


def test_claim_due_batch_returns_each_uuid_to_only_one_sqlite_session(
    db_engine, db_session, seed_mechanic
):
    from robopark_api.services.reliable_actions import claim_due_batch

    row = _begin(db_session, seed_mechanic, key="one-worker-only").row
    action_id = row.id
    db_session.commit()
    due_at = row.created_at + 1
    barrier = threading.Barrier(2)

    class SynchronizedSession(Session):
        synchronized = False

        def scalars(self, statement, *args, **kwargs):
            result = super().scalars(statement, *args, **kwargs)
            if not self.synchronized and "reliable_actions" in str(statement):
                self.synchronized = True
                barrier.wait(timeout=5)
            return result

    def claim():
        with SynchronizedSession(db_engine) as db:
            barrier.wait(timeout=5)
            return [claimed.id for claimed in claim_due_batch(db, now=due_at, limit=1)]

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed_ids = list(pool.map(lambda _: claim(), range(2)))

    assert sum(ids == [action_id] for ids in claimed_ids) == 1
    assert sum(not ids for ids in claimed_ids) == 1


@pytest.mark.parametrize(
    ("error_code", "expected_state"),
    [
        ("authentication", "needs_attention"),
        ("missing_transition", "needs_attention"),
        ("invalid_payload", "needs_attention"),
        ("network", "retry_wait"),
        ("timeout", "retry_wait"),
        ("429", "retry_wait"),
        ("503", "retry_wait"),
    ],
)
def test_schedule_retry_classifies_failures(db_session, seed_mechanic, error_code, expected_state):
    from robopark_api.services.reliable_actions import schedule_retry

    row = _begin(db_session, seed_mechanic, key=f"failure-{error_code}").row
    schedule_retry(db_session, row, error_code=error_code, now=100.0)

    assert row.state == expected_state
    assert row.error_code == error_code
    assert row.lease_until is None
    if expected_state == "retry_wait":
        assert row.attempts == 1
        assert 102.0 <= row.next_attempt_at < 103.0


def test_retry_jitter_is_deterministic(db_session, seed_mechanic):
    from robopark_api.services.reliable_actions import schedule_retry

    first = _begin(db_session, seed_mechanic, key="retry-one").row

    schedule_retry(db_session, first, error_code="timeout", now=100.0)
    expected = first.next_attempt_at
    first.attempts = 0
    schedule_retry(db_session, first, error_code="timeout", now=100.0)

    assert first.next_attempt_at == expected
