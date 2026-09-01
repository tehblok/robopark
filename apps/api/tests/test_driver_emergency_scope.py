from conftest import role_id_for
from robopark_api.models import AccessStatus, User
from robopark_api.security import hash_password
from robopark_api.services.rbac import RoleSlug


def test_driver_emergency_skips_blocker_scope(client, db_session, monkeypatch):
    from robopark_api.services import emergency_scope

    driver = User(
        username="driver_scope",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, RoleSlug.DRIVER),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(driver)
    db_session.commit()
    db_session.refresh(driver)

    def _fail_if_called(*_args, **_kwargs):
        raise AssertionError("tracker should not be queried for driver emergency scope")

    monkeypatch.setattr(emergency_scope.tracker_cache, "search_robot_tickets", _fail_if_called)
    assert emergency_scope.vin_allowed_for_user(db_session, driver, "YASADR00000000447") is True


def test_pending_driver_cannot_use_emergency(client, db_session):
    driver = User(
        username="driver_pending",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, RoleSlug.DRIVER),
        access_status=AccessStatus.pending.value,
        is_active=True,
    )
    db_session.add(driver)
    db_session.commit()

    assert (
        client.post(
            "/auth/login", json={"username": "driver_pending", "password": "secret"}
        ).status_code
        == 204
    )
    response = client.post("/emergency/resolve", json={"robot_number": "447"})
    assert response.status_code == 403
