from pathlib import Path

from conftest import login_as


def test_retired_snapshot_requires_typed_gateway_without_host_effects(
    client, seed_royal, test_settings
):
    login_as(client, "royal", "secret")
    response = client.post("/admin/ops/snapshot")
    assert response.status_code == 410
    assert response.json() == {"detail": "typed_operation_required"}
    ops = Path(test_settings.ops_dir)
    assert not ops.exists() or not [
        path for path in ops.rglob("*") if path.is_file() and path.name != "begin.lock"
    ]


def test_retired_repair_ignores_legacy_token_and_creates_no_host_command(
    client, seed_royal, test_settings, tmp_path
):
    host = tmp_path / "host"
    for name in ("inbox", "artifacts", "public"):
        (host / name).mkdir(parents=True)
    object.__setattr__(test_settings, "ops_host_root", str(host))
    login_as(client, "royal", "secret")
    response = client.post(
        "/admin/ops/repair", headers={"X-Privileged-Authorization": "stale-or-wrong"}
    )
    assert response.status_code == 410
    assert response.json() == {"detail": "typed_operation_required"}
    assert list((host / "inbox").iterdir()) == []
