from conftest import login_as

from park_helpers import PARK_DEFAULTS


def test_create_park_with_tracker_fields(client, seed_royal):
    login_as(client, "royal", "secret")
    created = client.post(
        "/parks",
        json={
            "name": "Tracked",
            "tag": "Tracked",
            "tracker_queue": "ROBOPARK",
            "feature_blockers": False,
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["tracker_queue"] == "ROBOPARK"
    assert body["feature_blockers"] is False
    assert body["is_active"] is True


def test_patch_park_feature_flags(client, seed_royal):
    login_as(client, "royal", "secret")
    park_id = client.post("/parks", json={"name": "P", "tag": "P"}).json()["id"]
    updated = client.patch(
        f"/parks/{park_id}",
        json={"feature_blockers": False, "tracker_queue": "OPS"},
    )
    assert updated.status_code == 200
    assert updated.json()["feature_blockers"] is False
    assert updated.json()["tracker_queue"] == "OPS"
    assert updated.json()["is_active"] is True
    assert {k: updated.json()[k] for k in PARK_DEFAULTS if k not in {"feature_blockers", "tracker_queue"}} == {
        k: PARK_DEFAULTS[k] for k in PARK_DEFAULTS if k not in {"feature_blockers", "tracker_queue"}
    }
