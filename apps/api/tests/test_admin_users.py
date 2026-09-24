from datetime import UTC, datetime, timedelta

from conftest import login_as, role_id_for
from robopark_api.collaboration_models import TrackerClaim
from robopark_api.models import AuditLog, AuthSession, User
from robopark_api.schedule_models import ScheduleEntry
from robopark_api.security import hash_password


def test_delete_user_deactivates_access_but_keeps_historical_actor(client, db_session, seed_royal):
    user = User(
        username="former-worker",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status="approved",
        is_active=True,
        tracker_login="sensitive.login",
        last_ip="1.2.3.4",
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(
        AuditLog(
            action="test.history",
            actor_user_id=user.id,
            actor_username=user.username,
            actor_role="operator",
        )
    )
    db_session.add(AuthSession(user_id=user.id, token_hash="a" * 64, expires_at=datetime.now(UTC)))
    db_session.commit()
    user_id = user.id
    login_as(client, "royal", "secret")
    assert client.delete(f"/admin/users/{user_id}").status_code == 204
    db_session.expire_all()
    retained = db_session.get(User, user_id)
    assert retained is not None and not retained.is_active
    assert retained.access_status == "rejected"
    assert any(
        row["id"] == user_id and not row["is_active"] for row in client.get("/admin/users").json()
    )
    assert db_session.query(AuthSession).filter_by(user_id=user_id).count() == 0
    history = db_session.query(AuditLog).filter_by(action="test.history").one()
    assert history.actor_user_id == user_id
    assert history.actor_username == "former-worker"


def test_delete_user_with_active_claims_and_future_schedules_requires_reassignment(
    client, db_session, seed_royal, seed_mechanic, seed_park_with_tracker
):
    now = datetime.now(UTC)
    db_session.add(
        TrackerClaim(
            issue_key="ROBOPARK-777",
            park_id=seed_park_with_tracker.id,
            owner_user_id=seed_mechanic.id,
            updated_by_user_id=seed_mechanic.id,
            state="active",
            updated_at=now.timestamp(),
        )
    )
    entry = ScheduleEntry(
        owner_user_id=seed_mechanic.id,
        park_id=seed_park_with_tracker.id,
        kind="shift",
        start_at=now + timedelta(days=1),
        end_at=now + timedelta(days=2),
        created_by_user_id=seed_mechanic.id,
        updated_by_user_id=seed_mechanic.id,
    )
    db_session.add(entry)
    db_session.commit()
    login_as(client, "royal", "secret")
    response = client.delete(f"/admin/users/{seed_mechanic.id}")
    assert response.status_code == 409
    assert response.json()["detail"] == "active_ownership_requires_reassignment"
    db_session.expire_all()
    claim = db_session.get(TrackerClaim, "ROBOPARK-777")
    assert claim.owner_user_id == seed_mechanic.id
    assert db_session.get(ScheduleEntry, entry.id).owner_user_id == seed_mechanic.id
    assert db_session.get(User, seed_mechanic.id).is_active
