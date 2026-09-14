import json

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from conftest import login_as, role_id_for
from robopark_api.models import AuditLog, EmergencyReading, EmergencySection, User
from robopark_api.schemas import EmergencyReadingCreate
from robopark_api.security import hash_password

BASE = "/admin/emergency-readings"
READING = {
    "section_id": "status",
    "path": "parktronics.lt",
    "label": "Left distance",
    "display_kind": "distance",
    "unit": "m",
    "precision": 2,
    "enabled_path": "parktronics.ltEnabled",
    "no_data_values": [2147483647],
    "warning_below": 0.4,
    "warning_above": None,
    "critical_below": 0.2,
    "critical_above": None,
    "view": "front",
    "x": 0.25,
    "y": 0.75,
    "label_direction": "left",
    "is_enabled": True,
    "sort_order": 4,
}


def add_user(db, role: str, *, status: str = "approved") -> User:
    user = User(
        username=f"{role}-{status}",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db, role),
        access_status=status,
        is_active=True,
    )
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def admin_client(client, db_session):
    add_user(db_session, "admin")
    assert login_as(client, "admin-approved", "secret").status_code == 204
    return client


@pytest.fixture(autouse=True)
def section(db_session):
    db_session.add(EmergencySection(id="status", title="Status", sort_order=0, is_enabled=True))
    db_session.commit()


def test_crud_uses_canonical_sentinels_etags_conflicts_and_structural_audit(
    admin_client, db_session
):
    created = admin_client.post(BASE, json=READING)
    assert created.status_code == 201
    assert created.headers["etag"].startswith('"')
    item = created.json()
    assert item == {"id": item["id"], **READING}
    stored = db_session.get(EmergencyReading, item["id"])
    assert stored.no_data_json == "[2147483647]"

    duplicate = admin_client.post(BASE, json=READING)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "emergency_reading_conflict"

    listed = admin_client.get(BASE)
    assert listed.status_code == 200
    assert listed.json() == [item]
    etag = listed.headers["etag"]
    assert listed.headers["cache-control"] == "no-store"

    secret_label = "private-payload-value"
    patched = admin_client.patch(
        f"{BASE}/{item['id']}",
        json={"label": secret_label, "no_data_values": [None, False, "missing"]},
    )
    assert patched.status_code == 200
    assert patched.json()["label"] == secret_label
    assert json.loads(db_session.get(EmergencyReading, item["id"]).no_data_json) == [
        None,
        False,
        "missing",
    ]
    assert patched.headers["etag"] != etag

    deleted = admin_client.delete(f"{BASE}/{item['id']}")
    assert deleted.status_code == 204
    assert deleted.headers["etag"].startswith('"')
    assert db_session.get(EmergencyReading, item["id"]) is None

    audits = list(
        db_session.scalars(
            select(AuditLog)
            .where(AuditLog.target_type == "emergency_reading")
            .order_by(AuditLog.id)
        )
    )
    assert [entry.action for entry in audits] == [
        "admin.emergency_reading.created",
        "admin.emergency_reading.updated",
        "admin.emergency_reading.deleted",
    ]
    assert all(secret_label not in (entry.detail or "") for entry in audits)
    assert "parktronics" not in " ".join(entry.detail or "" for entry in audits)
    assert json.loads(audits[1].detail) == {"fields": ["label", "no_data_values"]}


def test_reorder_requires_current_catalog_etag_and_every_id_once(admin_client, db_session):
    first = admin_client.post(BASE, json={**READING, "sort_order": 10}).json()
    second = admin_client.post(
        BASE,
        json={**READING, "path": "parktronics.rt", "sort_order": 20},
    ).json()
    etag = admin_client.get(BASE).headers["etag"]

    assert (
        admin_client.put(f"{BASE}/reorder", json={"ids": [second["id"], first["id"]]}).status_code
        == 428
    )
    stale = admin_client.put(
        f"{BASE}/reorder",
        headers={"If-Match": '"stale"'},
        json={"ids": [second["id"], first["id"]]},
    )
    assert stale.status_code == 409
    invalid = admin_client.put(
        f"{BASE}/reorder",
        headers={"If-Match": etag},
        json={"ids": [first["id"], first["id"]]},
    )
    assert invalid.status_code == 422

    reordered = admin_client.put(
        f"{BASE}/reorder",
        headers={"If-Match": etag},
        json={"ids": [second["id"], first["id"]]},
    )
    assert reordered.status_code == 200
    assert [item["id"] for item in reordered.json()] == [second["id"], first["id"]]
    assert [item["sort_order"] for item in reordered.json()] == [0, 1]
    assert reordered.headers["etag"] != etag
    audits = list(
        db_session.scalars(
            select(AuditLog)
            .where(AuditLog.action == "admin.emergency_reading.reordered")
            .order_by(AuditLog.target_id)
        )
    )
    assert {entry.target_id for entry in audits} == {str(first["id"]), str(second["id"])}
    assert all(
        set(json.loads(entry.detail)) == {"previous_sort_order", "sort_order"} for entry in audits
    )


def test_schemas_forbid_extra_fields_and_bound_catalog_values(admin_client):
    assert admin_client.post(BASE, json={**READING, "payload": {"secret": "x"}}).status_code == 422
    assert admin_client.post(BASE, json={**READING, "precision": 5}).status_code == 422
    with pytest.raises(ValidationError):
        EmergencyReadingCreate.model_validate({**READING, "x": float("inf")})
    assert (
        admin_client.post(
            BASE,
            json={**READING, "no_data_values": list(range(33))},
        ).status_code
        == 422
    )
    assert admin_client.patch(f"{BASE}/1", json={"sort_order": 99}).status_code == 422


@pytest.mark.parametrize(
    "role,status",
    [("royal", "approved"), ("admin", "pending"), ("mechanic", "approved")],
)
def test_every_endpoint_requires_approved_builtin_admin(client, db_session, role, status):
    add_user(db_session, role, status=status)
    assert login_as(client, f"{role}-{status}", "secret").status_code == 204

    responses = [
        client.get(BASE),
        client.get(BASE + "/1"),
        client.get(BASE + "/discovered", params={"vin": "1"}),
        client.post(BASE, json=READING),
        client.patch(BASE + "/1", json={"is_enabled": False}),
        client.delete(BASE + "/1"),
    ]
    assert [response.status_code for response in responses] == [403] * len(responses)


def test_discovery_normalizes_vin_uses_cache_and_returns_only_bounded_safe_scalars(
    admin_client, db_session, monkeypatch
):
    from robopark_api.services import emergency_cache, platform_settings

    calls = []
    monkeypatch.setattr(
        platform_settings,
        "get_emergency_cookie_probe",
        lambda _db: ("configured-cookie", "cookie-identity"),
    )

    nested = {"level13": 13}
    for depth in range(12, 0, -1):
        nested = {f"level{depth}": nested}

    def get_payload(*, db, vin, probe):
        calls.append((db, vin, probe))
        return {
            "speed": 0,
            "online": False,
            "name": "x" * 100,
            "tooLong": "x" * 129,
            "values": [1, None, {"current": 2.5}],
            "hud": {"secret": "hidden"},
            "sdcOptions": {"debug": True},
            "authToken": "hidden",
            "cookieJar": "hidden",
            **nested,
        }

    monkeypatch.setattr(emergency_cache, "get_robot_payload", get_payload)

    response = admin_client.get(BASE + "/discovered", params={"vin": " 42 "})

    assert response.status_code == 200
    assert calls == [(db_session, "YASADR00000000042", ("configured-cookie", "cookie-identity"))]
    records = {item["path"]: item for item in response.json()}
    assert records["speed"] == {"path": "speed", "value_type": "number", "example": "0"}
    assert records["online"]["value_type"] == "boolean"
    assert records["values.1"]["value_type"] == "null"
    assert records["values.2.current"]["example"] == "2.5"
    assert records["name"]["example"] == "x" * 80
    assert not any(
        forbidden.casefold() in path.casefold()
        for path in records
        for forbidden in ("hud", "sdcoptions", "token", "cookie", "toolong", "level13")
    )


def test_discovery_requires_valid_explicit_vin_and_caps_candidates(admin_client, monkeypatch):
    from robopark_api.services import emergency_cache, platform_settings

    monkeypatch.setattr(
        platform_settings,
        "get_emergency_cookie_probe",
        lambda _db: ("cookie", "identity"),
    )
    monkeypatch.setattr(
        emergency_cache,
        "get_robot_payload",
        lambda **_kwargs: {f"field{index}": index for index in range(1100)},
    )

    assert admin_client.get(BASE + "/discovered").status_code == 422
    assert admin_client.get(BASE + "/discovered", params={"vin": "invalid"}).status_code == 422
    response = admin_client.get(BASE + "/discovered", params={"vin": "1"})
    assert response.status_code == 200
    assert len(response.json()) == 1000
