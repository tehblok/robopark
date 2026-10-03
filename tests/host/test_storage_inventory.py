"""Read-only, bounded storage accounting for the owner System screen."""

import json
import os
from contextlib import contextmanager
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace

from robopark_host.storage_inventory import (
    _directory_bytes,
    collect_storage_inventory,
    parse_docker_df,
    parse_owned_builder_du,
)


def test_owned_builder_inventory_accepts_realistic_203_record_report():
    rows = "\n".join(json.dumps({"ID": f"cache{i}", "Size": "8.192kB", "Shared": False,
        "Reclaimable": True, "Mutable": False, "Type": "regular", "Description": "build context " + "a" * 500}) for i in range(203))
    assert len(rows) > 64 * 1024
    assert parse_owned_builder_du(rows) == {"reported_bytes": 203 * 8192, "private_reclaimable_bytes": 203 * 8192}


def test_directory_inventory_includes_allocated_blocks_of_the_root(tmp_path, monkeypatch):
    root = tmp_path / "empty"
    root.mkdir()
    original_lstat = Path.lstat

    def lstat(path):
        measured = original_lstat(path)
        if path != root:
            return measured
        return SimpleNamespace(
            st_mode=measured.st_mode,
            st_dev=measured.st_dev,
            st_ino=measured.st_ino,
            st_blocks=8,
        )

    monkeypatch.setattr(Path, "lstat", lstat)
    assert _directory_bytes(root) == 8 * 512


def test_directory_inventory_includes_nested_directory_blocks(tmp_path, monkeypatch):
    root = tmp_path / "tree"
    (root / "nested").mkdir(parents=True)
    original_scandir = os.scandir

    class Entry:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.path = wrapped.path

        def stat(self, *, follow_symlinks):
            measured = self.wrapped.stat(follow_symlinks=follow_symlinks)
            return SimpleNamespace(
                st_mode=measured.st_mode,
                st_dev=measured.st_dev,
                st_ino=measured.st_ino,
                st_blocks=8,
            )

    @contextmanager
    def scandir(path):
        with original_scandir(path) as entries:
            yield (Entry(entry) for entry in entries)

    monkeypatch.setattr(os, "scandir", scandir)
    assert _directory_bytes(root) == root.lstat().st_blocks * 512 + 8 * 512


def test_docker_df_json_reports_distinct_categories_without_inventing_zeros():
    lines = [
        {"Type": "Images", "Size": "1.5GB"},
        {"Type": "Build Cache", "Size": "768MB"},
        {"Type": "Local Volumes", "Size": "2.0GB"},
    ]
    result = parse_docker_df("\n".join(json.dumps(row) for row in lines))
    assert result == {
        "docker_images": 1_500_000_000,
        "buildkit_cache": 768_000_000,
        "docker_volumes": 2_000_000_000,
    }
    assert parse_docker_df('{"Type":"Images","Size":"broken"}') is None


def test_owned_builder_du_counts_reported_and_private_reclaimable_bytes():
    rows = [
        {"ID": "a", "Size": "8192", "Reclaimable": True, "Shared": False},
        {"ID": "b", "Size": "4096", "Reclaimable": True, "Shared": True},
        {"ID": "c", "Size": "2048", "Reclaimable": False, "Shared": False},
    ]
    assert parse_owned_builder_du("\n".join(json.dumps(row) for row in rows)) == {
        "reported_bytes": 14_336,
        "private_reclaimable_bytes": 8192,
    }
    assert parse_owned_builder_du('{"ID":"a","Size":"not-a-number","Reclaimable":true,"Shared":false}') is None
    assert parse_owned_builder_du('{"ID":"a","Size":"8192","Reclaimable":true}') is None
    assert parse_owned_builder_du("\n".join(json.dumps(rows[0]) for _ in range(2))) is None
    assert parse_owned_builder_du("x" * (64 * 1024 + 1)) is None


def test_owned_builder_du_accepts_buildx_decimal_size_units():
    rows = [
        {"ID": "a", "Size": "8.192kB", "Reclaimable": True, "Shared": False},
        {"ID": "b", "Size": "1.5MiB", "Reclaimable": False, "Shared": False},
    ]
    assert parse_owned_builder_du("\n".join(json.dumps(row) for row in rows)) == {
        "reported_bytes": 8192 + 1_572_864,
        "private_reclaimable_bytes": 8192,
    }


def test_inventory_measures_only_the_receipted_robopark_builder(host_paths):
    from robopark_host.state import atomic_write_json

    owned = "robopark-buildkit-" + "a" * 32
    atomic_write_json(host_paths.state / "buildkit-builder.json", {
        "schema": 1, "name": owned, "driver": "docker-container",
    })
    commands = []

    def docker(argv, *, timeout):
        commands.append(argv)
        if argv[3:5] == ["system", "df"]:
            return CompletedProcess(argv, 1, stdout="", stderr="")
        if argv[3:5] == ["volume", "inspect"]:
            return CompletedProcess(argv, 1, stdout="", stderr="")
        if argv[3:] == ["buildx", "inspect", owned]:
            return CompletedProcess(argv, 0, stdout=f"Name: {owned}\nDriver: docker-container\n", stderr="")
        if argv[3:5] == ["inspect", "--type"]:
            return CompletedProcess(argv, 0, stdout=json.dumps(["ROBOPARK_BUILDER_OWNER=" + owned]), stderr="")
        assert argv[3:] == ["buildx", "du", "--builder", owned, "--format=json"]
        assert timeout <= 8
        return CompletedProcess(argv, 0, stdout=json.dumps({
            "ID": "a", "Size": "8192", "Reclaimable": True, "Shared": False,
        }), stderr="")

    measured = collect_storage_inventory(host_paths, runner=docker)["category_bytes"]
    assert measured["robopark_buildkit_reported"] == 8192
    assert measured["robopark_buildkit_private_reclaimable"] == 8192
    assert len([argv for argv in commands if argv[3:5] == ["buildx", "du"]]) == 1


def test_inventory_does_not_attribute_a_replaced_builder_to_robopark(host_paths):
    from robopark_host.state import atomic_write_json

    owned = "robopark-buildkit-" + "a" * 32
    atomic_write_json(host_paths.state / "buildkit-builder.json", {
        "schema": 1, "name": owned, "driver": "docker-container",
    })
    commands = []

    def docker(argv, *, timeout):
        del timeout
        commands.append(argv)
        if argv[3:] == ["buildx", "inspect", owned]:
            return CompletedProcess(argv, 0, stdout=f"Name: {owned}\nDriver: docker-container\n", stderr="")
        if argv[3:5] == ["inspect", "--type"]:
            return CompletedProcess(argv, 0, stdout="[]", stderr="")
        if argv[3:5] == ["buildx", "du"]:
            return CompletedProcess(argv, 0, stdout=json.dumps({
                "ID": "foreign", "Size": 8192, "Reclaimable": True, "Shared": False,
            }), stderr="")
        return CompletedProcess(argv, 1, stdout="", stderr="")

    categories = collect_storage_inventory(host_paths, runner=docker)["category_bytes"]
    assert categories["robopark_buildkit_reported"] is None
    assert not [argv for argv in commands if argv[3:5] == ["buildx", "du"]]


def test_inventory_does_not_query_an_unowned_or_invalid_builder(host_paths):
    calls = []

    def docker(argv, *, timeout):
        calls.append(argv)
        return CompletedProcess(argv, 1, stdout="", stderr="")

    assert collect_storage_inventory(host_paths, runner=docker)["category_bytes"][
        "robopark_buildkit_reported"
    ] is None
    assert all(argv[3:5] != ["buildx", "du"] for argv in calls)

    receipt = host_paths.state / "buildkit-builder.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text('{"name":"foreign-builder"}')
    calls.clear()
    assert collect_storage_inventory(host_paths, runner=docker)["category_bytes"][
        "robopark_buildkit_reported"
    ] is None
    assert all(argv[3:5] != ["buildx", "du"] for argv in calls)


def test_storage_inventory_measures_only_fixed_local_roots(host_paths, tmp_path: Path):
    upload = host_paths.ops / "ota-uploads" / "upload.ota"
    cache = host_paths.state / "ota-packages" / "cache.ota"
    release = host_paths.releases / "current" / "payload"
    journal = host_paths.root / "var/log/journal" / "machine" / "system.journal"
    for target in (upload, cache, release, journal):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x" * 1024)
    outsider = tmp_path / "outside"
    outsider.write_bytes(b"private")
    (upload.parent / "outside-link").symlink_to(outsider)

    def docker(argv, *, timeout):
        assert argv[:3] == ["docker", "--host", "unix:///var/run/docker.sock"]
        assert timeout <= 10
        if argv[3:5] == ["system", "df"]:
            return CompletedProcess(
                argv,
                0,
                stdout='{"Type":"Images","Size":"10MB"}\n{"Type":"Build Cache","Size":"20MB"}\n{"Type":"Local Volumes","Size":"30MB"}',
                stderr="",
            )
        assert argv[3:] == ["volume", "inspect", "robopark_robopark_postgres"]
        return CompletedProcess(argv, 1, stdout="", stderr="")

    report = collect_storage_inventory(host_paths, runner=docker)
    assert report["category_bytes"]["ota_uploads"] is None
    assert report["category_bytes"]["ota_cache"] > 0
    assert report["category_bytes"]["releases"] > 0
    assert report["category_bytes"]["journald"] > 0
    assert report["category_bytes"]["docker_images"] == 10_000_000
    assert report["category_bytes"]["buildkit_cache"] == 20_000_000
    assert report["category_bytes"]["docker_volumes"] == 30_000_000
    assert outsider.read_bytes() == b"private"


def test_storage_inventory_keeps_docker_unknown_when_command_fails(host_paths):
    def docker(argv, *, timeout):
        return CompletedProcess(argv, 1, stdout="", stderr="unavailable")

    report = collect_storage_inventory(host_paths, runner=docker)
    assert report["category_bytes"]["docker_images"] is None
    assert report["category_bytes"]["buildkit_cache"] is None
    assert report["category_bytes"]["docker_volumes"] is None


def test_daily_local_snapshots_are_measured_separately_from_usb_backups(
    host_paths, tmp_path: Path
):
    scheduled = host_paths.root / "var/backups/robopark"
    scheduled.mkdir(parents=True)
    (scheduled / "robopark-20260926T041500000000.zip").write_bytes(b"snapshot")
    usb = host_paths.var / "backups"
    usb.mkdir(parents=True)
    (usb / "backup-example.rpb").write_bytes(b"usb")

    categories = collect_storage_inventory(host_paths)["category_bytes"]
    assert categories["scheduled_backups"] > 0
    assert categories["backups"] > 0

    for entry in scheduled.iterdir():
        entry.unlink()
    scheduled.rmdir()
    scheduled.symlink_to(tmp_path, target_is_directory=True)
    assert (
        collect_storage_inventory(host_paths)["category_bytes"]["scheduled_backups"]
        is None
    )


def test_postgresql_volume_is_measured_only_for_exact_compose_identity(
    host_paths, tmp_path: Path
):
    data = tmp_path / "robopark_robopark_postgres" / "_data"
    data.mkdir(parents=True)
    (data / "base").write_bytes(b"database")
    expected = {
        "Name": "robopark_robopark_postgres",
        "Driver": "local",
        "Labels": {
            "com.docker.compose.project": "robopark",
            "com.docker.compose.volume": "robopark_postgres",
        },
        "Mountpoint": str(data),
    }

    def docker(argv, *, timeout):
        if argv[3:5] == ["system", "df"]:
            return CompletedProcess(argv, 1, stdout="", stderr="")
        assert argv[3:] == ["volume", "inspect", "robopark_robopark_postgres"]
        return CompletedProcess(argv, 0, stdout=json.dumps([expected]), stderr="")

    measured = collect_storage_inventory(host_paths, runner=docker)
    assert measured["category_bytes"]["postgresql_data"] >= len(b"database")
    expected["Labels"]["com.docker.compose.project"] = "other"
    refused = collect_storage_inventory(host_paths, runner=docker)
    assert refused["category_bytes"]["postgresql_data"] is None
    expected["Labels"]["com.docker.compose.project"] = "robopark"
    expected["Mountpoint"] = str(tmp_path)
    refused = collect_storage_inventory(host_paths, runner=docker)
    assert refused["category_bytes"]["postgresql_data"] is None


def test_fake_host_inventory_never_queries_the_real_local_docker(
    host_paths, monkeypatch
):
    from robopark_host import storage_inventory

    def forbidden(*args, **kwargs):
        raise AssertionError("fake_host_queried_real_docker")

    monkeypatch.setattr(storage_inventory.subprocess, "run", forbidden)
    report = collect_storage_inventory(host_paths)
    assert report["category_bytes"]["docker_images"] is None


def test_scheduled_host_snapshot_publishes_measured_categories(host_paths, monkeypatch):
    from robopark_host import retention, storage_inventory

    host_paths.var.mkdir(parents=True)
    monkeypatch.setattr(
        storage_inventory,
        "collect_storage_inventory",
        lambda paths: {
            "sampled_at": 42.0,
            "category_bytes": {
                "ota_uploads": 1024,
                "docker_images": 2000,
                "buildkit_cache": None,
            },
        },
    )
    retention.retain_storage(host_paths)
    public = json.loads((host_paths.var / "api-ops/host-health.json").read_text())
    assert public["storage"]["category_bytes"]["ota_uploads"] == 1024
    assert public["storage"]["category_bytes"]["docker_images"] == 2000
    assert public["storage"]["category_bytes"]["buildkit_cache"] is None
    assert public["storage"]["inventory_sampled_at"] == 42.0


def test_inventory_failure_does_not_hide_successful_cleanup(host_paths, monkeypatch):
    from robopark_host import retention, storage_inventory

    host_paths.var.mkdir(parents=True)
    monkeypatch.setattr(
        storage_inventory,
        "collect_storage_inventory",
        lambda paths: (_ for _ in ()).throw(OSError("inventory unavailable")),
    )
    report = retention.retain_storage(host_paths)
    public = json.loads((host_paths.var / "api-ops/host-health.json").read_text())
    assert report["blocked"] is False
    assert public["storage"]["completed_at"] == report["completed_at"]
    assert "inventory_sampled_at" not in public["storage"]


def test_default_inventory_uses_the_same_private_buildx_config_as_updater(host_paths, monkeypatch):
    from dataclasses import replace
    from robopark_host import storage_inventory

    paths = replace(host_paths, root=Path('/'))
    monkeypatch.setenv('DOCKER_CONTEXT', 'foreign-context')
    monkeypatch.setenv('DOCKER_HOST', 'tcp://foreign.invalid:2375')
    monkeypatch.setattr(storage_inventory, '_directory_bytes', lambda _: 0)
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs.get('env', {})))
        return SimpleNamespace(returncode=1, stdout='')

    monkeypatch.setattr(storage_inventory.subprocess, 'run', run)
    collect_storage_inventory(paths)
    assert calls
    for _, environment in calls:
        assert environment['DOCKER_CONFIG'] == str(paths.ops / 'docker-config')
        assert environment['DOCKER_HOST'] == 'unix:///var/run/docker.sock'
        assert 'DOCKER_CONTEXT' not in environment
