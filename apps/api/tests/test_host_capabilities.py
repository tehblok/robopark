"""A closed capability snapshot gates grants and queue publication."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from conftest import _test_totp, login_as
from robopark_api.models import PrivilegedReauthorization, PrivilegedRecoveryCode
from robopark_api.services.ops import host_bridge

BOOT_ID = "00000000-0000-4000-8000-000000000010"
SAFE_KINDS = {
    "package-inspect", "backup-verify", "cleanup-preview", "diagnostics",
    "usb-discover", "usb-select",
}
UNAVAILABLE_PAYLOADS = [
    {"kind": "release-update", "release_id": 7, "confirmation": "UPDATE ROBOPARK"},
    {"kind": "reinstall", "confirmation": "REINSTALL ROBOPARK"},
    {"kind": "rollback", "release": "old", "confirmation": "ROLLBACK ROBOPARK"},
    {"kind": "package-update", "package": "openssl", "confirmation": "UPDATE PACKAGE openssl"},
    {"kind": "service-restart", "service": "robopark-api.service", "confirmation": "RESTART SERVICE robopark-api.service"},
    {"kind": "reboot", "confirmation": "REBOOT ROBOPARK"},
    {"kind": "backup", "device_uuid": BOOT_ID},
    {"kind": "backup-restore", "backup_id": BOOT_ID, "confirmation": "RESTORE ROBOPARK BACKUP"},
    {"kind": "cleanup-execute", "plan_id": BOOT_ID, "confirmation": "CLEAN ROBOPARK"},
    {"kind": "usb-format", "device_uuid": BOOT_ID, "confirmation": f"FORMAT USB {BOOT_ID}", "confirmation_repeat": f"FORMAT USB {BOOT_ID}"},
]


@pytest.fixture
def capability_bridge(test_settings, tmp_path, monkeypatch):
    root = tmp_path / "host-ops"
    for name in ("public", "inbox", "artifacts"):
        (root / name).mkdir(parents=True)
    object.__setattr__(test_settings, "ops_host_root", str(root))
    monkeypatch.setattr(host_bridge, "_host_boot_id", lambda: BOOT_ID, raising=False)
    value = {
        "schema": 1, "boot_id": BOOT_ID,
        "generated_at": datetime.now(UTC).isoformat(), "valid_for_seconds": 300,
        "operations": {
            kind: {"available": kind in SAFE_KINDS,
                   "unavailable_reason": None if kind in SAFE_KINDS else "capability_unavailable"}
            for kind in SAFE_KINDS | {item["kind"] for item in UNAVAILABLE_PAYLOADS}
        },
    }
    (root / "public/operation-capabilities.json").write_text(json.dumps(value))
    (root / "public/command-claim.json").write_text(json.dumps({
        "job_id": str(uuid4()), "kind": "diagnostics", "actor_user_id": 1, "active": False,
    }))
    return root, value


def test_capabilities_api_reports_executable_subset(client, seed_royal, capability_bridge):
    login_as(client, "royal", "secret")
    response = client.get("/admin/ops/capabilities")
    assert response.status_code == 200
    value = response.json()
    assert value["state"] == "ready"
    assert {kind for kind, item in value["operations"].items() if item["available"]} == SAFE_KINDS
    for item in UNAVAILABLE_PAYLOADS:
        assert value["operations"][item["kind"]] == {
            "available": False, "unavailable_reason": "capability_unavailable",
        }


@pytest.mark.parametrize("damage", ["missing", "stale", "future", "boot", "oversize", "schema", "extra", "missing-kind", "unknown-kind", "inconsistent", "boolean", "lifetime", "symlink"])
def test_invalid_capability_cache_is_closed(capability_bridge, damage, tmp_path):
    root, value = capability_bridge
    path = root / "public/operation-capabilities.json"
    if damage == "missing":
        path.unlink()
    elif damage == "oversize":
        path.write_text(" " * 8193 + json.dumps(value))
    elif damage == "symlink":
        target = tmp_path / "outside.json"
        target.write_text(json.dumps(value))
        path.unlink()
        path.symlink_to(target)
    else:
        if damage == "stale":
            value["generated_at"] = (datetime.now(UTC) - timedelta(seconds=301)).isoformat()
        elif damage == "future":
            value["generated_at"] = (datetime.now(UTC) + timedelta(seconds=1)).isoformat()
        elif damage == "boot":
            value["boot_id"] = str(uuid4())
        elif damage == "schema":
            value["schema"] = True
        elif damage == "extra":
            value["secret"] = "must-not-project"
        elif damage == "missing-kind":
            value["operations"].pop("reboot")
        elif damage == "unknown-kind":
            value["operations"]["shell"] = {"available": True, "unavailable_reason": None}
        elif damage == "inconsistent":
            value["operations"]["reboot"]["available"] = True
        elif damage == "boolean":
            value["operations"]["package-inspect"]["available"] = "true"
        elif damage == "lifetime":
            value["valid_for_seconds"] = 86400
        path.write_text(json.dumps(value))
    report = host_bridge.operation_capabilities(root)
    assert report.state == "unavailable"
    assert not any(item.available for item in report.operations.values())
    with pytest.raises(host_bridge.BridgeError, match="capabilities_unavailable"):
        host_bridge.require_operation_capability(root, "package-inspect")


def test_unavailable_reauth_and_enqueue_preserve_second_factor_and_grants(
    client, seed_royal, db_session, capability_bridge, monkeypatch,
):
    login_as(client, "royal", "secret")
    monkeypatch.setattr("robopark_api.services.privileged_auth._unix_time", lambda: 3_000.0)
    secret = client.post("/admin/privileged-auth/enrollment").json()["secret"]
    codes = client.post("/admin/privileged-auth/enrollment/confirm", json={
        "password": "secret", "code": _test_totp(secret, 100),
    }).json()["recovery_codes"]
    root, _ = capability_bridge
    for payload in UNAVAILABLE_PAYLOADS:
        identity = str(uuid4())
        denied = client.post("/admin/privileged-auth/reauthorize", json={
            "password": "secret", "code": codes[0],
            "operation_kind": payload["kind"], "operation_id": identity,
        })
        assert denied.status_code == 409, denied.text
        assert denied.json()["detail"] == "capability_unavailable"
        response = client.post("/admin/ops/operations", json={
            **payload, "operation_id": identity,
        })
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "capability_unavailable"
        assert not (root / "inbox/approved.json").exists()
    db_session.expire_all()
    assert not db_session.scalars(select(PrivilegedReauthorization)).all()
    assert all(row.used_at is None for row in db_session.scalars(select(PrivilegedRecoveryCode)))


@pytest.mark.parametrize("drift", ["unsupported", "missing", "restart", "between-checks"])
def test_capability_drift_before_enqueue_does_not_consume_issued_grant(
    client, seed_royal, db_session, capability_bridge, monkeypatch, drift,
):
    login_as(client, "royal", "secret")
    monkeypatch.setattr("robopark_api.services.privileged_auth._unix_time", lambda: 6_000.0)
    secret = client.post("/admin/privileged-auth/enrollment").json()["secret"]
    client.post("/admin/privileged-auth/enrollment/confirm", json={
        "password": "secret", "code": _test_totp(secret, 200),
    })
    monkeypatch.setattr("robopark_api.services.privileged_auth._unix_time", lambda: 6_030.0)
    identity = str(uuid4())
    issued = client.post("/admin/privileged-auth/reauthorize", json={
        "password": "secret", "code": _test_totp(secret, 201),
        "operation_kind": "package-inspect", "operation_id": identity,
    })
    assert issued.status_code == 200, issued.text
    root, value = capability_bridge
    path = root / "public/operation-capabilities.json"
    if drift == "unsupported":
        value["operations"]["package-inspect"] = {"available": False, "unavailable_reason": "capability_unavailable"}
        path.write_text(json.dumps(value))
    elif drift == "missing":
        path.unlink()
    elif drift == "restart":
        monkeypatch.setattr(host_bridge, "_host_boot_id", lambda: str(uuid4()))
    else:
        enqueue = host_bridge.enqueue_typed_operation

        def revoke_before_enqueue(*args, **kwargs):
            value["operations"]["package-inspect"] = {
                "available": False, "unavailable_reason": "capability_unavailable",
            }
            path.write_text(json.dumps(value))
            return enqueue(*args, **kwargs)

        monkeypatch.setattr(host_bridge, "enqueue_typed_operation", revoke_before_enqueue)
    response = client.post("/admin/ops/operations", headers={
        "X-Privileged-Authorization": issued.json()["token"],
    }, json={"operation_id": identity, "kind": "package-inspect", "package": "openssl"})
    assert response.status_code in {409, 503}, response.text
    db_session.expire_all()
    assert db_session.scalars(select(PrivilegedReauthorization)).one().used_at is None
    assert not (root / "inbox/approved.json").exists()


def test_supported_api_operation_requires_and_consumes_one_bound_grant(
    client, seed_royal, db_session, capability_bridge, monkeypatch,
):
    login_as(client, "royal", "secret")
    identity = str(uuid4())
    payload = {"operation_id": identity, "kind": "package-inspect", "package": "openssl"}
    assert client.post("/admin/ops/operations", json=payload).status_code == 409
    monkeypatch.setattr("robopark_api.services.privileged_auth._unix_time", lambda: 9_000.0)
    secret = client.post("/admin/privileged-auth/enrollment").json()["secret"]
    client.post("/admin/privileged-auth/enrollment/confirm", json={
        "password": "secret", "code": _test_totp(secret, 300),
    })
    monkeypatch.setattr("robopark_api.services.privileged_auth._unix_time", lambda: 9_030.0)
    issued = client.post("/admin/privileged-auth/reauthorize", json={
        "password": "secret", "code": _test_totp(secret, 301),
        "operation_kind": "package-inspect", "operation_id": identity,
    })
    assert issued.status_code == 200
    headers = {"X-Privileged-Authorization": issued.json()["token"]}
    result = client.post("/admin/ops/operations", json=payload, headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["id"] == identity
    root, _ = capability_bridge
    request = json.loads((root / "inbox/approved.json").read_text())
    assert request["capability_revision"] == host_bridge.operation_capabilities(root).revision
    assert request["authorization"]["consumed"] is True
    db_session.expire_all()
    assert db_session.scalars(select(PrivilegedReauthorization)).one().used_at is not None
    assert client.post("/admin/ops/operations", json=payload, headers=headers).status_code == 401
