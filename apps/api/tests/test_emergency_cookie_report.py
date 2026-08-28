import threading

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from conftest import login_as
from robopark_api.models import Report, User
from robopark_api.services.rbac import RoleSlug
from robopark_api.services import emergency_cache, emergency_client
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services import reports as reports_svc

VIN = "YASADR00000000447"


def test_ensure_cookie_report_dedupes(db_session, seed_royal):
    first = reports_svc.ensure_open_emergency_cookie_report(db_session, author=seed_royal)
    second = reports_svc.ensure_open_emergency_cookie_report(db_session, author=seed_royal)
    assert first is not None and first.id == second.id
    assert first.kind == "emergency_cookie_stale"
    assert first.park_id is None
    assert first.target_role == RoleSlug.ADMIN
    open_rows = db_session.query(Report).filter(Report.status == "open").all()
    assert len(open_rows) == 1


def test_resolve_marks_cookie_report_done(db_session, seed_royal):
    reports_svc.ensure_open_emergency_cookie_report(db_session, author=seed_royal)
    assert reports_svc.resolve_open_emergency_cookie_reports(db_session) == 1
    row = db_session.query(Report).one()
    assert row.status == "done"


def test_admin_inbox_includes_null_park_cookie_report(
    client, db_session, seed_royal, seed_park_with_tracker
):
    reports_svc.ensure_open_emergency_cookie_report(db_session, author=seed_royal)
    login_as(client, "royal", "secret")
    inbox = client.get(f"/reports/inbox?park_id={seed_park_with_tracker.id}")
    assert inbox.status_code == 200
    kinds = [item["kind"] for item in inbox.json()]
    assert "emergency_cookie_stale" in kinds


def test_auth_error_opens_one_cookie_report(db_session, seed_royal, monkeypatch):
    emergency_cache.clear_cache_for_tests()
    settings_svc.set_setting(db_session, settings_svc.EMERGENCY_COOKIE_KEY, "cookie")

    def boom(**_kwargs):
        raise emergency_client.EmergencyAuthError("expired")

    monkeypatch.setattr(emergency_client, "fetch_robot_payload", boom)
    with pytest.raises(emergency_client.EmergencyAuthError):
        emergency_cache.get_robot_payload(db=db_session, vin=VIN)
    with pytest.raises(emergency_client.EmergencyAuthError):
        emergency_cache.get_robot_payload(db=db_session, vin=VIN)
    rows = db_session.query(Report).filter(Report.kind == "emergency_cookie_stale").all()
    assert len(rows) == 1
    assert rows[0].status == "open"


def test_success_resolves_cookie_report(db_session, seed_royal, monkeypatch):
    emergency_cache.clear_cache_for_tests()
    reports_svc.ensure_open_emergency_cookie_report(db_session, author=seed_royal)
    monkeypatch.setattr(
        emergency_client,
        "fetch_robot_payload",
        lambda **kwargs: {"vin": kwargs["vin"]},
    )
    settings_svc.set_setting(db_session, settings_svc.EMERGENCY_COOKIE_KEY, "cookie")
    emergency_cache.get_robot_payload(db=db_session, vin=VIN)
    row = db_session.query(Report).one()
    assert row.status == "done"


def test_cache_hit_does_not_resolve_cookie_report(db_session, monkeypatch):
    emergency_cache.clear_cache_for_tests()
    calls = {"resolve": 0}
    real_resolve = reports_svc.resolve_open_emergency_cookie_reports

    def counting_resolve(db):
        calls["resolve"] += 1
        return real_resolve(db)

    monkeypatch.setattr(
        emergency_cache.reports,
        "resolve_open_emergency_cookie_reports",
        counting_resolve,
    )
    monkeypatch.setattr(
        emergency_client,
        "fetch_robot_payload",
        lambda **kwargs: {"vin": kwargs["vin"]},
    )
    settings_svc.set_setting(db_session, settings_svc.EMERGENCY_COOKIE_KEY, "cookie")
    emergency_cache.get_robot_payload(db=db_session, vin=VIN)
    emergency_cache.get_robot_payload(db=db_session, vin=VIN)
    assert calls["resolve"] == 1


def test_put_emergency_cookie_resolves_open_report(client, db_session, seed_royal):
    reports_svc.ensure_open_emergency_cookie_report(db_session, author=seed_royal)
    login_as(client, "royal", "secret")
    put = client.put(
        "/admin/settings/emergency-cookie",
        json={"cookie": "Session_id=fresh"},
    )
    assert put.status_code == 200
    row = db_session.query(Report).one()
    db_session.refresh(row)
    assert row.status == "done"


def _gate_cookie_report_inserts(monkeypatch) -> None:
    barrier = threading.Barrier(2)
    real_add = Session.add

    def gated_add(self, instance):
        if isinstance(instance, Report) and instance.kind == "emergency_cookie_stale":
            barrier.wait(timeout=5)
        return real_add(self, instance)

    monkeypatch.setattr(Session, "add", gated_add)


def test_ensure_recovers_unique_violation_from_concurrent_inserts(
    db_engine, seed_royal, monkeypatch
):
    _gate_cookie_report_inserts(monkeypatch)
    results = []
    errors = []

    def worker():
        with Session(db_engine) as session:
            author = session.get(User, seed_royal.id)
            try:
                report = reports_svc.ensure_open_emergency_cookie_report(session, author=author)
                results.append(None if report is None else report.id)
            except Exception as exc:
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    assert len(results) == 2
    assert results[0] == results[1]
    with Session(db_engine) as session:
        rows = (
            session.query(Report)
            .filter(
                Report.kind == "emergency_cookie_stale",
                Report.status == "open",
            )
            .all()
        )
        assert len(rows) == 1


def test_get_robot_payload_raises_auth_error_when_ensure_hits_unique_index(
    db_engine, seed_royal, monkeypatch
):
    emergency_cache.clear_cache_for_tests()
    with Session(db_engine) as session:
        settings_svc.set_setting(session, settings_svc.EMERGENCY_COOKIE_KEY, "cookie")
        settings_svc.set_emergency_cookie_valid(session, True)

    def boom(**_kwargs):
        raise emergency_client.EmergencyAuthError("expired")

    monkeypatch.setattr(emergency_client, "fetch_robot_payload", boom)
    _gate_cookie_report_inserts(monkeypatch)
    errors = []

    def worker(vin):
        with Session(db_engine) as session:
            try:
                emergency_cache.get_robot_payload(db=session, vin=vin)
            except BaseException as exc:
                errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=("YASADR00000000001",)),
        threading.Thread(target=worker, args=("YASADR00000000002",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len(errors) == 2
    assert all(isinstance(exc, emergency_client.EmergencyAuthError) for exc in errors)
    assert not any(isinstance(exc, IntegrityError) for exc in errors)
    with Session(db_engine) as session:
        rows = (
            session.query(Report)
            .filter(
                Report.kind == "emergency_cookie_stale",
                Report.status == "open",
            )
            .all()
        )
        assert len(rows) == 1
