import json
import threading
from unittest.mock import MagicMock, Mock

import pytest
from sqlalchemy.orm import Session

from conftest import login_as
from robopark_api.models import Report, User
from robopark_api.routers import admin_settings
from robopark_api.schemas import EmergencyCookieUpdate
from robopark_api.services import emergency_cache, emergency_client, reports
from robopark_api.services import platform_settings as settings_svc


def test_get_integrations_empty(client, seed_royal):
    login_as(client, "royal", "secret")
    response = client.get("/admin/settings/integrations")
    assert response.status_code == 200
    body = response.json()
    assert body["tracker_token_masked"] is None
    assert body["emergency_cookie_masked"] is None


def test_set_tracker_token_masked(client, seed_royal):
    login_as(client, "royal", "secret")
    put = client.put(
        "/admin/settings/tracker-token",
        json={"token": "oauth-secret-token"},
    )
    assert put.status_code == 200
    body = put.json()
    assert body["tracker_token_masked"] == f"•••• ({len('oauth-secret-token')})"
    assert "oauth-secret-token" not in str(body)
    assert "oken" not in body["tracker_token_masked"]


def test_invalid_candidate_does_not_replace_working_cookie(
    client, db_session, seed_royal, monkeypatch
):
    login_as(client, "royal", "secret")
    settings_svc.activate_emergency_cookie(
        db_session, cookie="working", status="valid", checked_robot="447"
    )
    before = client.get("/admin/settings/integrations").json()
    monkeypatch.setattr(
        emergency_client,
        "fetch_robot_payload",
        Mock(side_effect=emergency_client.EmergencyAuthError()),
    )

    response = client.put(
        "/admin/settings/emergency-cookie",
        json={"cookie": "bad", "robot_number": "A2378"},
    )

    assert response.status_code == 401
    assert settings_svc.get_emergency_cookie(db_session) == "working"
    assert "bad" not in response.text
    assert client.get("/admin/settings/integrations").json() == before


def test_unavailable_candidate_does_not_replace_working_cookie(
    client, db_session, seed_royal, monkeypatch
):
    login_as(client, "royal", "secret")
    settings_svc.activate_emergency_cookie(
        db_session, cookie="working", status="valid", checked_robot="447"
    )
    before = client.get("/admin/settings/integrations").json()
    monkeypatch.setattr(
        emergency_client,
        "fetch_robot_payload",
        Mock(side_effect=emergency_client.EmergencyError()),
    )

    response = client.put(
        "/admin/settings/emergency-cookie",
        json={"cookie": "candidate-cookie", "robot_number": "A2378"},
    )

    assert response.status_code == 503
    assert settings_svc.get_emergency_cookie(db_session) == "working"
    assert "candidate-cookie" not in response.text
    assert client.get("/admin/settings/integrations").json() == before


def test_old_candidate_put_cannot_resolve_new_activation_report(db_engine, seed_royal, monkeypatch):
    activated = threading.Event()
    resume = threading.Event()
    errors = []
    actor_id = seed_royal.id

    def pause_after_activation():
        activated.set()
        assert resume.wait(timeout=5)

    monkeypatch.setattr(emergency_cache, "clear_cache", pause_after_activation)
    monkeypatch.setattr(
        emergency_client, "fetch_robot_payload", lambda **kwargs: {"vin": kwargs["vin"]}
    )

    def old_put():
        try:
            with Session(db_engine) as db:
                admin_settings.put_emergency_cookie(
                    EmergencyCookieUpdate(cookie="first", robot_number="447"),
                    db,
                    db.get(User, actor_id),
                )
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=old_put)
    worker.start()
    assert activated.wait(timeout=5)
    try:
        with Session(db_engine) as newer:
            identity = settings_svc.activate_emergency_cookie(
                newer, cookie="second", status="valid", checked_robot="448"
            )
            settings_svc.record_emergency_cookie_probe(
                newer, identity=identity, valid=False, status="invalid", checked_robot="448"
            )
            reports.ensure_open_emergency_cookie_report(
                newer, author=None, expected_identity=identity
            )
    finally:
        resume.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert not errors
    with Session(db_engine) as check:
        assert check.query(Report).one().status == "open"
        assert settings_svc.get_emergency_cookie_identity(check) == identity
        assert settings_svc.get_emergency_cookie_status(check) == "invalid"


def test_malformed_emergency_response_returns_503_for_candidate(client, seed_royal, monkeypatch):
    login_as(client, "royal", "secret")
    upstream_response = MagicMock(status_code=200, headers={"content-type": "application/json"})
    upstream_response.json.side_effect = json.JSONDecodeError("invalid JSON", "not-json", 0)
    http_client = MagicMock()
    http_client.__enter__.return_value = http_client
    http_client.get.return_value = upstream_response
    monkeypatch.setattr(emergency_client.httpx, "Client", lambda **_kwargs: http_client)

    response = client.put(
        "/admin/settings/emergency-cookie",
        json={"cookie": "candidate", "robot_number": "A2378"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "emergency_upstream_unavailable"


def test_valid_candidate_is_saved_after_probe(client, db_session, seed_royal, monkeypatch):
    login_as(client, "royal", "secret")
    monkeypatch.setattr(
        emergency_client,
        "fetch_robot_payload",
        Mock(return_value={"vin": "YASADR00000002378"}),
    )

    response = client.put(
        "/admin/settings/emergency-cookie",
        json={"cookie": "candidate", "robot_number": "A2378"},
    )

    assert response.status_code == 200
    assert settings_svc.get_emergency_cookie(db_session) == "candidate"
    assert response.json()["emergency_cookie_status"] == "valid"
    assert response.json()["emergency_cookie_checked_robot"] == "2378"
    assert response.json()["emergency_cookie_checked_at"] is not None


def test_valid_candidate_clears_cached_emergency_payload(
    client, db_session, seed_royal, monkeypatch
):
    login_as(client, "royal", "secret")
    vin = "YASADR00000002378"
    settings_svc.set_setting(db_session, settings_svc.EMERGENCY_COOKIE_KEY, "working")
    calls: list[str] = []

    def fetch_robot_payload(*, cookie: str, vin: str):
        calls.append(cookie)
        return {"vin": vin}

    monkeypatch.setattr(emergency_client, "fetch_robot_payload", fetch_robot_payload)
    emergency_cache.get_robot_payload(db=db_session, vin=vin)

    response = client.put(
        "/admin/settings/emergency-cookie",
        json={"cookie": "candidate", "robot_number": "A2378"},
    )
    emergency_cache.get_robot_payload(db=db_session, vin=vin)

    assert response.status_code == 200
    assert calls == ["working", "candidate", "candidate"]


def test_recheck_prefers_explicit_robot_over_keepalive_robot(
    client, db_session, seed_royal, monkeypatch
):
    login_as(client, "royal", "secret")
    settings_svc.set_setting(db_session, settings_svc.EMERGENCY_COOKIE_KEY, "working")
    settings_svc.touch_keepalive_ring(db_session, "YASADR00000000099")
    calls: list[dict[str, str]] = []
    monkeypatch.setattr(
        emergency_client,
        "fetch_robot_payload",
        lambda **kwargs: calls.append(kwargs) or {"vin": kwargs["vin"]},
    )
    emergency_cache.get_robot_payload(db=db_session, vin="YASADR00000000099")

    response = client.post(
        "/admin/settings/emergency-cookie/check",
        json={"robot_number": "A2378"},
    )

    assert response.status_code == 200
    assert calls[-1] == {"cookie": "working", "vin": "YASADR00000002378"}
    assert response.json()["emergency_cookie_status"] == "valid"
    assert response.json()["emergency_cookie_checked_robot"] == "2378"


def test_recheck_uses_last_keepalive_robot_when_explicit_robot_is_absent(
    client, db_session, seed_royal, monkeypatch
):
    login_as(client, "royal", "secret")
    settings_svc.set_setting(db_session, settings_svc.EMERGENCY_COOKIE_KEY, "working")
    settings_svc.touch_keepalive_ring(db_session, "YASADR00000002378")
    monkeypatch.setattr(
        emergency_client,
        "fetch_robot_payload",
        Mock(return_value={"vin": "YASADR00000002378"}),
    )

    response = client.post("/admin/settings/emergency-cookie/check", json={})

    assert response.status_code == 200
    assert response.json()["emergency_cookie_checked_robot"] == "2378"


def test_recheck_requires_explicit_or_keepalive_robot(client, seed_royal):
    login_as(client, "royal", "secret")

    response = client.post("/admin/settings/emergency-cookie/check", json={})

    assert response.status_code == 422
    assert response.json()["detail"] == "emergency_probe_required"


def test_missing_cookie_recheck_cannot_mark_a_concurrent_activation_unavailable(
    client, db_session, seed_royal, monkeypatch
):
    login_as(client, "royal", "secret")
    capture = settings_svc.get_emergency_cookie_probe

    def capture_before_activation(db):
        old_probe = capture(db)
        settings_svc.activate_emergency_cookie(
            db, cookie="new-cookie", status="valid", checked_robot="448"
        )
        return old_probe

    monkeypatch.setattr(settings_svc, "get_emergency_cookie_probe", capture_before_activation)
    response = client.post("/admin/settings/emergency-cookie/check", json={"robot_number": "447"})

    assert response.status_code == 503
    assert settings_svc.get_emergency_cookie_status(db_session) == "valid"
    assert settings_svc.get_emergency_cookie_checked_robot(db_session) == "448"


def test_recheck_auth_failure_marks_saved_cookie_invalid(
    client, db_session, seed_royal, monkeypatch
):
    login_as(client, "royal", "secret")
    settings_svc.set_setting(db_session, settings_svc.EMERGENCY_COOKIE_KEY, "working")
    monkeypatch.setattr(
        emergency_client,
        "fetch_robot_payload",
        Mock(side_effect=emergency_client.EmergencyAuthError()),
    )

    response = client.post(
        "/admin/settings/emergency-cookie/check",
        json={"robot_number": "A2378"},
    )

    assert response.status_code == 401
    assert settings_svc.get_emergency_cookie(db_session) == "working"
    body = client.get("/admin/settings/integrations").json()
    assert body["emergency_cookie_valid"] is False
    assert body["emergency_cookie_status"] == "invalid"
    assert body["emergency_cookie_checked_robot"] == "2378"


@pytest.mark.parametrize(
    "payload",
    [
        {"cookie": "candidate-secret"},
        {"cookie": {"legacy": "candidate-secret"}, "robot_number": "447"},
        {"cookie": "candidate-secret", "robot_number": []},
    ],
)
def test_emergency_cookie_validation_errors_do_not_leak_candidate_secret(
    client, seed_royal, payload
):
    login_as(client, "royal", "secret")

    response = client.put("/admin/settings/emergency-cookie", json=payload)

    assert response.status_code == 422
    assert "candidate-secret" not in response.text


def test_emergency_cookie_invalid_json_does_not_leak_candidate_secret(client, seed_royal):
    login_as(client, "royal", "secret")

    response = client.put(
        "/admin/settings/emergency-cookie",
        content=b'{"cookie":"candidate-secret"',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    assert "candidate-secret" not in response.text


def test_failed_emergency_cookie_activation_rolls_back_all_settings(
    db_session, seed_royal, monkeypatch
):
    settings_svc.set_setting(db_session, settings_svc.EMERGENCY_COOKIE_KEY, "working")
    monkeypatch.setattr(
        emergency_client,
        "fetch_robot_payload",
        Mock(return_value={"vin": "YASADR00000002378"}),
    )
    clear_cache = Mock()
    monkeypatch.setattr(emergency_cache, "clear_cache", clear_cache)

    def fail_commit():
        raise RuntimeError("commit failed")

    monkeypatch.setattr(db_session, "commit", fail_commit)

    with pytest.raises(RuntimeError, match="commit failed"):
        admin_settings.put_emergency_cookie(
            EmergencyCookieUpdate(cookie="candidate", robot_number="A2378"),
            db_session,
            seed_royal,
        )

    assert clear_cache.call_count == 0
    assert settings_svc.get_emergency_cookie(db_session) == "working"
    assert settings_svc.get_emergency_cookie_valid(db_session) is None
    assert settings_svc.get_emergency_cookie_status(db_session) == "unchecked"
    assert settings_svc.get_emergency_cookie_checked_at(db_session) is None
    assert settings_svc.get_emergency_cookie_checked_robot(db_session) is None


def test_emergency_cookie_activation_replaces_persistent_identity(db_session):
    settings_svc.activate_emergency_cookie(
        db_session,
        cookie="first",
        status="valid",
        checked_robot="2378",
    )
    first_identity = settings_svc.get_emergency_cookie_identity(db_session)

    settings_svc.activate_emergency_cookie(
        db_session,
        cookie="second",
        status="valid",
        checked_robot="2378",
    )

    assert first_identity is not None
    assert settings_svc.get_emergency_cookie_identity(db_session) not in {None, first_identity}


def test_emergency_cookie_activation_discards_prior_probe_ring(db_session):
    old_identity = settings_svc.activate_emergency_cookie(
        db_session,
        cookie="first",
        status="valid",
        checked_robot="2378",
    )
    assert settings_svc.record_emergency_cookie_probe(
        db_session,
        identity=old_identity,
        valid=False,
        vin="YASADR00000002378",
        status="invalid",
        checked_robot="2378",
    )

    settings_svc.activate_emergency_cookie(
        db_session,
        cookie="second",
        status="valid",
        checked_robot="447",
    )

    assert settings_svc.get_keepalive_ring(db_session) == []
    assert settings_svc.get_emergency_cookie_valid(db_session) is True
    assert settings_svc.get_emergency_cookie_status(db_session) == "valid"
    assert settings_svc.get_emergency_cookie_checked_robot(db_session) == "447"


def test_emergency_cookie_probe_refreshes_both_rows_after_other_session_activation(db_engine):
    """A retained ORM cookie row must not be paired with a fresh identity row."""
    with Session(db_engine) as initial:
        settings_svc.activate_emergency_cookie(
            initial,
            cookie="old-cookie",
            status="valid",
            checked_robot="2378",
        )

    with Session(db_engine) as stale_reader:
        assert settings_svc.get_emergency_cookie(stale_reader) == "old-cookie"
        with Session(db_engine) as replacement:
            new_identity = settings_svc.activate_emergency_cookie(
                replacement,
                cookie="new-cookie",
                status="valid",
                checked_robot="2378",
            )

        assert settings_svc.get_emergency_cookie_probe(stale_reader) == (
            "new-cookie",
            new_identity,
        )
