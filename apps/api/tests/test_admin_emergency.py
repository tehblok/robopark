import json

from conftest import login_as
from robopark_api.models import AccessStatus, User, UserRole
from robopark_api.security import hash_password


def _create_section(client, section_id: str = "status"):
    return client.post(
        "/admin/emergency/sections",
        json={
            "id": section_id,
            "title": "Статус",
            "is_enabled": True,
            "formatter": None,
            "meta": {"covered_top_level": ["vin"]},
            "roles": ["mechanic", "admin"],
            "fields": [{"path": "vin", "label": "VIN"}],
        },
    )


def test_admin_lists_creates_and_updates_sections(client, seed_royal):
    login_as(client, "royal", "secret")

    created = _create_section(client)
    assert created.status_code == 201
    assert created.json() == {
        "id": "status",
        "title": "Статус",
        "sort_order": 0,
        "is_enabled": True,
        "formatter": None,
        "meta": {"covered_top_level": ["vin"]},
        "roles": ["admin", "mechanic"],
        "fields": [
            {
                "id": created.json()["fields"][0]["id"],
                "path": "vin",
                "label": "VIN",
                "sort_order": 0,
            }
        ],
    }

    patched = client.patch(
        "/admin/emergency/sections/status",
        json={
            "title": "Состояние",
            "is_enabled": False,
            "formatter": "errors_classify",
            "meta": {"covered_top_level": ["errors"]},
            "roles": ["royal"],
        },
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "Состояние"
    assert patched.json()["is_enabled"] is False
    assert patched.json()["roles"] == ["royal"]

    listed = client.get("/admin/emergency/sections")
    assert listed.status_code == 200
    assert [section["id"] for section in listed.json()] == ["status"]


def test_admin_field_crud_and_section_delete(client, seed_royal):
    login_as(client, "royal", "secret")
    section = _create_section(client, "batteries").json()

    added = client.post(
        "/admin/emergency/sections/batteries/fields",
        json={"path": "battery.level", "label": "Заряд"},
    )
    assert added.status_code == 201
    assert added.json()["sort_order"] == 1

    changed = client.patch(
        f"/admin/emergency/fields/{added.json()['id']}",
        json={"path": "battery.percent", "label": "Процент"},
    )
    assert changed.status_code == 200
    assert changed.json()["path"] == "battery.percent"

    removed = client.delete(f"/admin/emergency/fields/{section['fields'][0]['id']}")
    assert removed.status_code == 204
    assert client.delete("/admin/emergency/sections/batteries").status_code == 204
    assert client.get("/admin/emergency/sections").json() == []


def test_admin_reorders_sections_and_exports_seed_shape(client, seed_royal):
    login_as(client, "royal", "secret")
    _create_section(client, "first")
    _create_section(client, "second")

    reordered = client.put(
        "/admin/emergency/sections/reorder",
        json={"ids": ["second", "first"]},
    )
    assert reordered.status_code == 200
    assert [section["id"] for section in reordered.json()] == ["second", "first"]

    exported = client.get("/admin/emergency/export")
    assert exported.status_code == 200
    assert list(exported.json()["sections"]) == ["second", "first"]
    assert exported.json()["sections"]["first"] == {
        "title": "Статус",
        "fields": [{"path": "vin", "label": "VIN"}],
        "covered_top_level": ["vin"],
    }
    json.dumps(exported.json(), ensure_ascii=False)


def test_operator_gets_403_for_admin_emergency(client, db_session):
    operator = User(
        username="operator-admin-test",
        password_hash=hash_password("secret"),
        role=UserRole.operator.value,
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(operator)
    db_session.commit()
    login_as(client, "operator-admin-test", "secret")

    assert client.get("/admin/emergency/sections").status_code == 403
    assert _create_section(client).status_code == 403


def test_export_canonical_keys_override_meta(client, seed_royal):
    login_as(client, "royal", "secret")
    client.post(
        "/admin/emergency/sections",
        json={
            "id": "status",
            "title": "Статус",
            "is_enabled": True,
            "formatter": "errors_classify",
            "meta": {
                "title": "Meta title",
                "fields": [{"path": "meta.path", "label": "Meta label"}],
                "formatter": "meta_formatter",
                "covered_top_level": ["vin"],
            },
            "roles": ["mechanic"],
            "fields": [{"path": "vin", "label": "VIN"}],
        },
    )

    exported = client.get("/admin/emergency/export")
    assert exported.status_code == 200
    assert exported.json()["sections"]["status"] == {
        "title": "Статус",
        "fields": [{"path": "vin", "label": "VIN"}],
        "formatter": "errors_classify",
        "covered_top_level": ["vin"],
    }


def test_patch_rejects_explicit_null_for_title_path_label(client, seed_royal):
    login_as(client, "royal", "secret")
    section = _create_section(client, "status").json()
    field_id = section["fields"][0]["id"]

    assert (
        client.patch("/admin/emergency/sections/status", json={"title": None}).status_code
        == 422
    )
    assert (
        client.patch("/admin/emergency/sections/status", json={"is_enabled": None}).status_code
        == 422
    )
    assert (
        client.patch(f"/admin/emergency/fields/{field_id}", json={"path": None}).status_code
        == 422
    )
    assert (
        client.patch(f"/admin/emergency/fields/{field_id}", json={"label": None}).status_code
        == 422
    )

    listed = client.get("/admin/emergency/sections").json()[0]
    assert listed["title"] == "Статус"
    assert listed["fields"][0]["path"] == "vin"
    assert listed["fields"][0]["label"] == "VIN"
