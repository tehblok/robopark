"""An installed worker may never replace the live multiprocess database itself."""

from robopark_api.services.ops.archives import build_archive
from robopark_api.services.ops.runner import OpsContext, begin_job, run_restore


def test_installed_restore_cannot_fall_back_to_process_local_replacement(tmp_path):
    live = tmp_path / "data"
    live.mkdir()
    database = live / "robopark.db"
    database.write_bytes(b"live database")
    source = tmp_path / "snapshot/data"
    source.mkdir(parents=True)
    (source / "robopark.db").write_bytes(b"replacement database")
    archive = build_archive(kind="snapshot", source_root=source.parent, app_version="0.1.0")
    host = tmp_path / "host"
    (host / "public").mkdir(parents=True)
    ctx = OpsContext(
        ops_dir=tmp_path / "ops",
        database_url="sqlite:///" + str(database),
        data_dir=live,
        use_host_updater=True,
        host_ops_dir=host,
    )
    job = begin_job(ctx, "restore", exempt_token_hash="initiator")
    result = run_restore(ctx, job, archive, confirm="ВОССТАНОВИТЬ")
    assert result.error == "host_restore_required"
    assert database.read_bytes() == b"live database"


def test_installed_legacy_restore_route_cannot_queue_root_approval(
    client, seed_royal, test_settings, tmp_path
):
    from conftest import login_as

    host = tmp_path / "host-ops"
    for name in ("inbox", "artifacts", "public"):
        (host / name).mkdir(parents=True)
    object.__setattr__(test_settings, "ops_host_root", str(host))
    source = tmp_path / "backup/data"
    source.mkdir(parents=True)
    (source / "robopark.db").write_bytes(b"snapshot validated again by root")
    archive = build_archive(kind="snapshot", source_root=source.parent, app_version="0.1.0")
    login_as(client, "royal", "secret")
    response = client.post(
        "/admin/ops/restore",
        data={"confirm": "ВОССТАНОВИТЬ"},
        files={"archive": ("external.zip", archive, "application/zip")},
    )
    assert response.status_code == 410
    assert response.json()["detail"] == "typed_operation_required"
    assert not (host / "inbox/approved.json").exists()
    assert not list((host / "artifacts").iterdir())
    assert client.get("/auth/me").status_code == 200


def test_restore_admission_reserves_storage_before_creating_job(tmp_path, monkeypatch):
    import pytest

    from robopark_api.services.ops import host_bridge
    from robopark_api.services.ops.jobs import JobConflict

    host = tmp_path / "host"
    for name in ("inbox", "artifacts", "public"):
        (host / name).mkdir(parents=True)
    (host / "artifacts/occupied.zip").write_bytes(b"existing")
    source = tmp_path / "snapshot/data"
    source.mkdir(parents=True)
    (source / "robopark.db").write_bytes(b"snapshot")
    blob = build_archive(kind="snapshot", source_root=source.parent, app_version="0.1.0")
    monkeypatch.setattr(host_bridge, "MAX_UPLOAD_STORAGE", len(blob))
    with pytest.raises(JobConflict, match="artifact_storage_full"):
        host_bridge.enqueue_restore(tmp_path / "ops", host, blob, 1, "session")
    assert not (tmp_path / "ops/job.json").exists()
    assert not (host / "inbox/approved.json").exists()
    assert len(list((host / "artifacts").iterdir())) == 1
