"""Root restore owns writers and recovers every durable filesystem transition."""

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from robopark_api.services.ops.archives import build_archive


@pytest.fixture
def e2e_host(monkeypatch):
    from e2e_support import InstalledHost

    with InstalledHost(monkeypatch) as host:
        yield host


def database(path, value, head="initial"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            "CREATE TABLE probe(value TEXT); CREATE TABLE alembic_version(version_num TEXT);"
        )
        connection.execute("INSERT INTO probe VALUES (?)", (value,))
        connection.execute("INSERT INTO alembic_version VALUES (?)", (head,))


def value(host):
    with sqlite3.connect(host.paths.var / "data/robopark.db") as connection:
        return connection.execute("SELECT value FROM probe").fetchone()[0]


def approve(host, *, head="initial", corrupt=False, extra=None, manifest_changes=None):
    database(host.paths.var / "data/robopark.db", "live")
    source = host.installer.base / "manual-snapshot"
    database(source / "data/robopark.db", "snapshot", head)
    (source / "data/attachment").write_text("saved attachment")
    (source / "config").mkdir()
    (source / "config/host.env").write_text("untrusted host configuration")
    for name, data in (extra or {}).items():
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    raw = build_archive(kind="snapshot", source_root=source, app_version="0.1.0")
    if manifest_changes:
        import io
        import zipfile

        source_zip = zipfile.ZipFile(io.BytesIO(raw))
        output = io.BytesIO()
        with source_zip, zipfile.ZipFile(output, "w") as destination:
            for name in source_zip.namelist():
                body = source_zip.read(name)
                if name == "manifest.json":
                    metadata = json.loads(body)
                    metadata.update(manifest_changes)
                    body = json.dumps(metadata).encode()
                destination.writestr(name, body)
        raw = output.getvalue()
    identity = str(uuid4())
    artifact = "restore-" + identity + ".zip"
    (host.paths.ops / "artifacts" / artifact).write_bytes(raw)
    request = {
        "kind": "restore",
        "job_id": identity,
        "artifact": artifact,
        "sha256": "0" * 64 if corrupt else hashlib.sha256(raw).hexdigest(),
        "actor_user_id": 1,
        "created_at": "2026-09-07T00:00:00+00:00",
    }
    # Use a fresh approval in any future test run.
    from datetime import UTC, datetime

    request["created_at"] = datetime.now(UTC).isoformat()
    (host.paths.ops / "inbox/approved.json").write_text(json.dumps(request))
    return host.command("consume")


def test_manual_restore_replaces_all_data_and_restarts_writers_under_root_barrier(
    e2e_host,
):
    host = e2e_host
    secrets = (host.paths.etc / "host.env").read_bytes()
    assert approve(host) == 0
    assert value(host) == "snapshot"
    assert (host.paths.var / "data/attachment").read_text() == "saved attachment"
    assert (host.paths.etc / "host.env").read_bytes() == secrets
    assert not host.maintenance()
    assert host.app_active and host.tuna_active
    result = json.loads((host.paths.ops / "public/command-result.json").read_text())
    assert result["kind"] == "restore" and result["state"] == "succeeded"


@pytest.mark.parametrize("options", [{"head": "wrong"}, {"corrupt": True}])
def test_manual_restore_rejects_invalid_input_without_stopping_app(e2e_host, options):
    host = e2e_host
    assert approve(host, **options) != 0
    assert value(host) == "live"
    assert not any(call[:2] == ["systemctl", "stop"] for call in host.calls)
    assert not host.maintenance()


@pytest.mark.parametrize(
    "phase",
    [
        "validating",
        "prepared",
        "maintenance",
        "stopping",
        "snapshotting",
        "snapshotted",
        "replacing",
        "replaced",
        "starting",
        "ready",
        "resuming",
        "succeeded",
        "rolling_back",
        "rollback_starting",
        "rollback_ready",
        "rollback_resuming",
        "rolled_back",
    ],
)
def test_manual_restore_power_loss_recovers_without_mixed_data(e2e_host, monkeypatch, phase):
    from e2e_support import PowerLoss

    host = e2e_host
    if phase.startswith("roll"):
        host.fail = "restore_health"
    original = os.replace
    tripped = False

    def interrupt(source, destination):
        nonlocal tripped
        original(source, destination)
        if (
            Path(destination).name == "restore-journal.json"
            and not tripped
            and json.loads(Path(destination).read_text())["phase"] == phase
        ):
            tripped = True
            raise PowerLoss()

    monkeypatch.setattr(os, "replace", interrupt)
    with pytest.raises(PowerLoss):
        approve(host)
    assert tripped
    monkeypatch.setattr(os, "replace", original)
    host.fail = None
    host.reboot()
    assert not host.maintenance()
    assert value(host) == ("snapshot" if phase in ("resuming", "succeeded") else "live")
    assert host.app_active and host.tuna_active
    host.reboot()
    assert not host.maintenance()


@pytest.mark.parametrize("edge", ["displace", "install"])
def test_boot_refuses_app_start_in_each_directory_rename_window(e2e_host, monkeypatch, edge):
    import fcntl

    from e2e_support import PowerLoss

    host = e2e_host
    original = os.replace
    tripped = False

    def interrupt(source, destination):
        nonlocal tripped
        selected = Path(source) if edge == "displace" else Path(destination)
        if selected == host.paths.var / "data" and not tripped:
            assert host.maintenance() and (host.paths.state / "maintenance.json").exists()
            assert not host.app_active
            with (
                (host.paths.ops / "host.lock").open("a") as lock,
                pytest.raises(BlockingIOError),
            ):
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            original(source, destination)
            tripped = True
            raise PowerLoss()
        original(source, destination)

    monkeypatch.setattr(os, "replace", interrupt)
    with pytest.raises(PowerLoss):
        approve(host)
    monkeypatch.setattr(os, "replace", original)
    assert tripped
    assert host.command("restore-check") == 1
    with pytest.raises(host.command_error):
        host.run(["systemctl", "start", "robopark.service"], timeout=60)
    assert not host.app_active
    host.reboot()
    assert value(host) == "live" and not host.maintenance()


def test_manual_restore_blocks_four_independent_database_writers(e2e_host):
    host = e2e_host
    host.check_multiworker = True
    assert approve(host) == 0
    assert host.writer_outcomes == ["maintenance"] * 4
    assert value(host) == "snapshot"


@pytest.mark.parametrize(
    "extra",
    [
        {"data/robopark.db-wal": b"stale WAL"},
        {"data/robopark.db-shm": b"stale SHM"},
        {"deploy/host/evil.py": b"executable"},
    ],
)
def test_manual_restore_rejects_sidecars_and_non_data_payloads(e2e_host, extra):
    host = e2e_host
    assert approve(host, extra=extra) != 0
    assert value(host) == "live" and host.app_active and not host.maintenance()


def test_corrupt_restore_journal_blocks_writes_and_app_boot(e2e_host):
    host = e2e_host
    (host.paths.state / "restore-journal.json").write_text("corrupt")
    assert host.command("restore-check") == 1
    assert host.command("update", "--recover") == 1
    assert host.maintenance()


def test_operator_recovery_bypasses_exhausted_automatic_retries(e2e_host):
    host = e2e_host
    host.fail = "both_health"
    assert approve(host) == 1
    assert host.maintenance()
    request = json.loads((host.paths.state / "command-request.json").read_text())
    (host.paths.state / "command-attempts.json").write_text(
        json.dumps({"job_id": request["job_id"], "attempts": 3})
    )
    host.fail = None
    assert host.command("restore", "--recover") == 0
    assert value(host) == "live"
    assert not host.maintenance()
    assert not (host.paths.state / "command-request.json").exists()


@pytest.mark.parametrize("changes", [{"app_version": None}, {"app_version": 123}])
def test_root_rejects_malformed_snapshot_metadata_before_writers_stop(e2e_host, changes):
    host = e2e_host
    assert approve(host, manifest_changes=changes) == 1
    assert value(host) == "live"
    assert not any(call[:2] == ["systemctl", "stop"] for call in host.calls)


def test_root_rejects_checksum_valid_non_sqlite_database(e2e_host):
    host = e2e_host
    assert approve(host, extra={"data/robopark.db": b"not a database"}) == 1
    assert value(host) == "live"


def test_standalone_update_cannot_take_over_interrupted_manual_restore(e2e_host, monkeypatch):
    from datetime import UTC, datetime

    from e2e_support import PowerLoss

    host = e2e_host
    original = os.replace

    def interrupt(source, destination):
        original(source, destination)
        if (
            Path(destination).name == "restore-journal.json"
            and json.loads(Path(destination).read_text())["phase"] == "maintenance"
        ):
            raise PowerLoss()

    monkeypatch.setattr(os, "replace", interrupt)
    with pytest.raises(PowerLoss):
        approve(host)
    monkeypatch.setattr(os, "replace", original)
    (host.paths.ops / "artifacts/update-e2e.zip").write_bytes(host.raw)
    request = host.paths.state / "standalone-update.json"
    request.write_text(
        json.dumps(
            {
                "kind": "update",
                "job_id": str(uuid4()),
                "actor_user_id": 1,
                "created_at": datetime.now(UTC).isoformat(),
                "artifact": "update-e2e.zip",
            }
        )
    )
    before = list(host.calls)
    assert host.command("update", "--request", str(request), "--worker") == 1
    assert host.calls == before
    assert value(host) == "live"
    host.reboot()
    assert not host.maintenance()


def test_snapshot_validation_never_creates_wal_or_shm_files(tmp_path):
    from contextlib import closing

    from robopark_host.restore import _validate_database

    path = tmp_path / "robopark.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE alembic_version(version_num TEXT)")
        connection.execute("INSERT INTO alembic_version VALUES ('initial')")
        connection.commit()
    before = path.read_bytes()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["robopark.db"]
    _validate_database(path, "initial")
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["robopark.db"]


def test_two_terminal_restores_prune_only_obsolete_material(e2e_host):
    import shutil
    import time

    from robopark_host.retention import artifact_usage, retain_artifacts

    host = e2e_host
    assert approve(host) == 0
    previous = json.loads((host.paths.state / "restore-journal.json").read_text())["request"][
        "job_id"
    ]
    shutil.rmtree(host.installer.base / "manual-snapshot")
    assert approve(host) == 0
    latest = json.loads((host.paths.state / "restore-journal.json").read_text())["request"][
        "job_id"
    ]
    before = artifact_usage(host.paths)["bytes"]
    result = retain_artifacts(host.paths, now=time.time() + 30 * 86400, max_bytes=0)
    assert not result["blocked"]
    for identity, exists in [(previous, False), (latest, True)]:
        assert (host.paths.state / "restores" / identity).exists() is exists
        assert (host.paths.var / (".manual-displaced-" + identity)).exists() is exists
        assert (host.paths.ops / "artifacts" / ("restore-" + identity + ".zip")).exists() is exists
    assert artifact_usage(host.paths)["bytes"] < before
    assert value(host) == "snapshot" and not host.maintenance()
