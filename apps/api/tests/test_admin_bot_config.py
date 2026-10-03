from __future__ import annotations

import hashlib
import json
from pathlib import Path

from conftest import login_as


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _configure_data(test_settings, tmp_path, monkeypatch) -> Path:
    host_data = tmp_path / "host-data"
    data = host_data / "telegram-bot" / "data"
    data.mkdir(parents=True)
    monkeypatch.setattr(test_settings, "host_data_path", str(host_data))
    return data


def _roles() -> dict[str, object]:
    return {
        "version": 1,
        "admin_user_ids": [123],
        "role_definitions": {
            "mechanic": {
                "label": "Mechanic",
                "permissions": ["history", "location_tags", "qr"],
            }
        },
        "updated_at": "2026-09-28T12:00:00Z",
    }


def test_royal_reads_shared_sections_with_independent_revisions(
    client, seed_royal, test_settings, tmp_path, monkeypatch
):
    data = _configure_data(test_settings, tmp_path, monkeypatch)
    roles = _roles()
    raw = (json.dumps(roles, ensure_ascii=False) + "\n").encode()
    (data / "roles.json").write_bytes(raw)
    (data / "roles.json").chmod(0o600)
    login_as(client, "royal", "secret")

    response = client.get("/admin/bot/config")

    assert response.status_code == 200
    sections = response.json()["sections"]
    assert set(sections) == {
        "roles",
        "users",
        "locations",
        "schedules",
        "broadcasts",
        "campaigns",
        "auxiliary_tracker_queues",
        "profile",
        "dispatcher_pause",
        "send_pause",
    }
    assert sections["roles"] == {
        "revision": hashlib.sha256(raw).hexdigest(),
        "value": roles,
    }
    assert sections["profile"]["value"] == "prod"
    assert sections["dispatcher_pause"]["value"] is False
    assert sections["send_pause"]["value"] is False
    assert "token" not in response.text.lower()


def test_royal_reads_bot_user_with_custom_role(
    client, seed_royal, test_settings, tmp_path, monkeypatch
):
    data = _configure_data(test_settings, tmp_path, monkeypatch)
    _write_json(
        data / "roles.json",
        {
            "version": 1,
            "admin_user_ids": [123],
            "role_definitions": {
                "senior_mechanic": {"label": "Старший механик", "permissions": ["history", "qr"]},
            },
        },
    )
    _write_json(
        data / "dispatcher_users.json",
        {
            "456": {"role": "senior_mechanic", "access": "location", "allowed_tags": ["Next"]},
        },
    )
    login_as(client, "royal", "secret")

    response = client.get("/admin/bot/config")

    assert response.status_code == 200
    assert response.json()["sections"]["users"]["value"]["456"]["role"] == "senior_mechanic"


def test_shared_config_is_royal_only(client, seed_admin):
    login_as(client, "admin", "secret")

    response = client.get("/admin/bot/config")

    assert response.status_code == 403


def test_put_section_requires_revision_and_rejects_stale_writes(
    client, seed_royal, test_settings, tmp_path, monkeypatch
):
    data = _configure_data(test_settings, tmp_path, monkeypatch)
    _write_json(data / "roles.json", _roles())
    login_as(client, "royal", "secret")
    current = client.get("/admin/bot/config").json()["sections"]["roles"]
    updated = _roles()
    updated["admin_user_ids"] = [123, 456]

    missing = client.put("/admin/bot/config/roles", json={"value": updated})
    saved = client.put(
        "/admin/bot/config/roles",
        headers={"If-Match": f'"{current["revision"]}"'},
        json={"value": updated},
    )
    stale = client.put(
        "/admin/bot/config/roles",
        headers={"If-Match": current["revision"]},
        json={"value": _roles()},
    )

    assert missing.status_code == 428
    assert saved.status_code == 200
    assert saved.json()["value"]["admin_user_ids"] == [123, 456]
    assert saved.json()["revision"] != current["revision"]
    assert stale.status_code == 409
    assert stale.json() == {"detail": "bot_config_revision_conflict"}
    persisted = json.loads((data / "roles.json").read_text(encoding="utf-8"))
    sidecar = json.loads((data / "roles.sidecar.json").read_text(encoding="utf-8"))
    assert persisted == sidecar == saved.json()["value"]
    assert (data / "roles.json").stat().st_mode & 0o777 == 0o600


def test_structural_config_requires_disabled_bot_but_runtime_controls_remain_available(
    client, seed_royal, test_settings, tmp_path, monkeypatch
):
    data = _configure_data(test_settings, tmp_path, monkeypatch)
    _write_json(data / "roles.json", _roles())
    control = data.parent / "enabled.json"
    _write_json(control, {"schema": 1, "enabled": True})
    login_as(client, "royal", "secret")
    sections = client.get("/admin/bot/config").json()["sections"]

    blocked = client.put(
        "/admin/bot/config/roles",
        headers={"If-Match": sections["roles"]["revision"]},
        json={"value": _roles()},
    )
    paused = client.put(
        "/admin/bot/config/send_pause",
        headers={"If-Match": sections["send_pause"]["revision"]},
        json={"value": True},
    )

    assert blocked.status_code == 409
    assert blocked.json() == {"detail": "telegram_bot_must_be_disabled"}
    assert paused.status_code == 200
    assert paused.json()["value"] is True
    assert (data / ".send_paused").read_text(encoding="utf-8") == "1\n"


def test_config_rejects_symlinks_and_unknown_json_state(
    client, seed_royal, test_settings, tmp_path, monkeypatch
):
    data = _configure_data(test_settings, tmp_path, monkeypatch)
    outside = tmp_path / "outside.json"
    _write_json(outside, _roles())
    (data / "roles.json").symlink_to(outside)
    login_as(client, "royal", "secret")

    symlinked = client.get("/admin/bot/config")
    (data / "roles.json").unlink()
    invalid = _roles()
    invalid["unexpected"] = "must not be overwritten"
    _write_json(data / "roles.json", invalid)
    unknown = client.get("/admin/bot/config")

    assert symlinked.status_code == 422
    assert symlinked.json() == {"detail": "bot_config_unsafe_state:roles"}
    assert unknown.status_code == 422
    assert unknown.json() == {"detail": "bot_config_invalid_schema:roles"}


def test_config_accepts_legacy_bot_read_only_file_mode(
    client, seed_royal, test_settings, tmp_path, monkeypatch
):
    data = _configure_data(test_settings, tmp_path, monkeypatch)
    _write_json(data / "roles.json", _roles())
    (data / "roles.json").chmod(0o644)
    login_as(client, "royal", "secret")

    response = client.get("/admin/bot/config")

    assert response.status_code == 200
    assert response.json()["sections"]["roles"]["value"] == _roles()


def test_config_validates_profile_and_known_section_name(
    client, seed_royal, test_settings, tmp_path, monkeypatch
):
    _configure_data(test_settings, tmp_path, monkeypatch)
    login_as(client, "royal", "secret")
    sections = client.get("/admin/bot/config").json()["sections"]

    invalid_profile = client.put(
        "/admin/bot/config/profile",
        headers={"If-Match": sections["profile"]["revision"]},
        json={"value": "production"},
    )
    unknown = client.put(
        "/admin/bot/config/arbitrary",
        headers={"If-Match": "missing"},
        json={"value": {}},
    )

    assert invalid_profile.status_code == 422
    assert invalid_profile.json() == {"detail": "bot_config_invalid_schema:profile"}
    assert unknown.status_code == 404


def test_auxiliary_tracker_queues_are_explicit_bounded_allowlist(
    client, seed_royal, test_settings, tmp_path, monkeypatch
):
    data = _configure_data(test_settings, tmp_path, monkeypatch)
    login_as(client, "royal", "secret")
    section = client.get("/admin/bot/config").json()["sections"]["auxiliary_tracker_queues"]

    saved = client.put(
        "/admin/bot/config/auxiliary_tracker_queues",
        headers={"If-Match": section["revision"]},
        json={"value": ["ROBOMAINT", "SDCWH"]},
    )
    invalid = client.put(
        "/admin/bot/config/auxiliary_tracker_queues",
        headers={"If-Match": saved.json()["revision"]},
        json={"value": ["ROBOMAINT OR Queue: *"]},
    )
    malformed = client.put(
        "/admin/bot/config/auxiliary_tracker_queues",
        headers={"If-Match": saved.json()["revision"]},
        json={"value": [{"queue": "ROBOMAINT"}]},
    )

    assert saved.status_code == 200
    assert saved.json()["value"] == ["ROBOMAINT", "SDCWH"]
    assert json.loads((data / "robopark-settings.json").read_text(encoding="utf-8")) == {
        "version": 1,
        "auxiliary_tracker_queues": ["ROBOMAINT", "SDCWH"],
    }
    assert invalid.status_code == 422
    assert invalid.json() == {"detail": "bot_config_invalid_schema:auxiliary_tracker_queues"}
    assert malformed.status_code == 422
