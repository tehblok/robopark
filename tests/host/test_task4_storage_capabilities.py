import hashlib
import json
import os
import time
from pathlib import Path
from uuid import uuid4

import pytest


def test_storage_budget_uses_larger_partition_floor():
    from robopark_host.retention import StorageBudget

    small = StorageBudget(partition_bytes=20 * 1024**3, free_bytes=7 * 1024**3)
    large = StorageBudget(partition_bytes=100 * 1024**3, free_bytes=14 * 1024**3)

    assert small.floor_bytes == 6 * 1024**3
    assert small.bytes_to_reclaim == 0
    assert large.floor_bytes == 15 * 1024**3
    assert large.bytes_to_reclaim == 1024**3


def test_bounded_cleanup_orders_known_categories_and_never_follows_symlinks(tmp_path):
    from robopark_host.retention import StorageBudget, cleanup_storage_roots

    diagnostics = tmp_path / "diagnostics"
    logs = tmp_path / "logs"
    outside = tmp_path / "outside"
    for root in (diagnostics, logs, outside):
        root.mkdir()
    (diagnostics / "old.log").write_bytes(b"d" * 10)
    (logs / "old.log").write_bytes(b"u" * 10)
    protected = outside / "primary.db"
    protected.write_bytes(b"primary")
    (diagnostics / "escape").symlink_to(protected)

    report = cleanup_storage_roots(
        {"diagnostics": diagnostics, "logs": logs},
        StorageBudget(
            partition_bytes=100,
            free_bytes=0,
            minimum_free_bytes=sum(
                (root / "old.log").stat().st_blocks * 512 for root in (diagnostics, logs)
            ),
        ),
        dry_run=False,
        max_deletions=2,
        now=10_000,
    )

    assert [item["category"] for item in report["deleted"]] == ["diagnostics", "logs"]
    assert not (diagnostics / "old.log").exists()
    assert not (logs / "old.log").exists()
    assert protected.read_bytes() == b"primary"
    assert report["bounded"] is True


def test_storage_cleanup_dry_run_has_no_side_effects(tmp_path):
    from robopark_host.retention import StorageBudget, cleanup_storage_roots

    diagnostics = tmp_path / "diagnostics"
    diagnostics.mkdir()
    victim = diagnostics / "entry"
    victim.write_bytes(b"123")

    report = cleanup_storage_roots(
        {"diagnostics": diagnostics},
        StorageBudget(partition_bytes=100, free_bytes=0, minimum_free_bytes=3),
        dry_run=True,
        max_deletions=1,
        now=10_000,
    )

    assert victim.exists()
    assert report["deleted"] == []
    assert report["planned"][0]["path"] == "entry"


def test_cleanup_execute_requires_the_exact_immutable_preview(tmp_path):
    from robopark_host.retention import (
        StorageBudget,
        execute_cleanup_plan,
        preview_cleanup_plan,
    )

    diagnostics = tmp_path / "diagnostics"
    diagnostics.mkdir()
    victim = diagnostics / "entry"
    victim.write_bytes(b"123")
    roots = {"diagnostics": diagnostics}
    plan = preview_cleanup_plan(
        roots,
        StorageBudget(100, 0, minimum_free_bytes=3),
        now=10_000,
    )
    assert victim.exists()
    with pytest.raises(ValueError, match="cleanup_plan_changed"):
        execute_cleanup_plan(
            roots,
            StorageBudget(100, 0, minimum_free_bytes=3),
            {**plan, "plan_id": "00000000-0000-4000-8000-000000000000"},
            now=10_000,
        )
    result = execute_cleanup_plan(
        roots,
        StorageBudget(100, 0, minimum_free_bytes=3),
        plan,
        now=10_000,
    )
    assert result["plan_id"] == plan["plan_id"]
    assert not victim.exists()


def test_cleanup_preview_reports_allocated_bytes_for_sparse_file(tmp_path):
    from robopark_host.retention import StorageBudget, preview_cleanup_plan

    diagnostics = tmp_path / "diagnostics"
    diagnostics.mkdir()
    sparse = diagnostics / "old.log"
    with sparse.open("wb") as stream:
        stream.truncate(8 * 1024 * 1024)
    os.utime(sparse, (1, 1))
    plan = preview_cleanup_plan(
        {"diagnostics": diagnostics},
        StorageBudget(10_000, 0, minimum_free_bytes=1),
        now=1_000_000,
    )
    assert plan["planned"][0]["bytes"] == sparse.stat().st_blocks * 512


def test_cleanup_execute_refuses_replacement_between_recheck_and_delete(tmp_path, monkeypatch):
    from robopark_host import retention

    root = tmp_path / "diagnostics"
    root.mkdir()
    victim = root / "old.log"
    victim.write_bytes(b"old")
    os.utime(victim, (1, 1))
    roots = {"diagnostics": root}
    budget = retention.StorageBudget(100, 0, minimum_free_bytes=3)
    plan = retention.preview_cleanup_plan(roots, budget, now=1_000_000)
    original = retention.cleanup_storage_roots

    def replace_during_execute(*args, **kwargs):
        if not kwargs["dry_run"]:
            victim.unlink()
            victim.write_bytes(b"new")
            os.utime(victim, (1, 1))
        return original(*args, **kwargs)

    monkeypatch.setattr(retention, "cleanup_storage_roots", replace_during_execute)
    with pytest.raises(ValueError, match="cleanup_plan_changed"):
        retention.execute_cleanup_plan(roots, budget, plan, now=1_000_000)
    assert victim.read_bytes() == b"new"


def test_cleanup_execute_reports_files_removed_before_a_later_unlink_failure(tmp_path, monkeypatch):
    from robopark_host import retention

    root = tmp_path / "diagnostics"
    root.mkdir()
    for name in ("first.log", "second.log"):
        victim = root / name
        victim.write_bytes(name.encode())
        os.utime(victim, (1, 1))
    roots = {"diagnostics": root}
    budget = retention.StorageBudget(100_000, 0, minimum_free_bytes=10_000)
    plan = retention.preview_cleanup_plan(roots, budget, now=1_000_000)
    real_unlink = retention.os.unlink

    def fail_second(path, *args, **kwargs):
        if path == "second.log":
            raise OSError("injected unlink failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(retention.os, "unlink", fail_second)
    with pytest.raises(retention.CleanupPartialError) as caught:
        retention.execute_cleanup_plan(roots, budget, plan, now=1_000_000)
    assert caught.value.deleted == [{
        "category": "diagnostics", "path": "first.log",
        "bytes": plan["planned"][0]["bytes"],
    }]
    assert not (root / "first.log").exists()
    assert (root / "second.log").exists()


def test_system_cleanup_executes_only_the_confirmed_preview(host_paths):
    from robopark_host import retention

    victim = host_paths.var / "diagnostics" / "old.log"
    victim.parent.mkdir(parents=True)
    victim.write_bytes(b"x" * 200)
    os.utime(victim, (1, 1))
    database = host_paths.var / "data" / "keep"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"postgres")
    budget = retention.StorageBudget(1000, 0, minimum_free_bytes=100)
    plan = retention.preview_system_cleanup_plan(
        host_paths, ["diagnostics"], budget, now=1_000_000,
    )
    assert [(item["category"], item["path"]) for item in plan["planned"]] == [
        ("diagnostics", "old.log")
    ]

    with pytest.raises(ValueError, match="cleanup_plan_changed"):
        retention.execute_system_cleanup_plan(
            host_paths, ["diagnostics"], budget,
            {**plan, "plan_id": "00000000-0000-4000-8000-000000000000"},
            now=1_000_000,
        )
    assert victim.exists()

    result = retention.execute_system_cleanup_plan(
        host_paths, ["diagnostics"], budget, plan, now=1_000_000,
    )
    assert result["deleted_count"] == 1
    assert not victim.exists()
    assert database.read_bytes() == b"postgres"


def test_ota_cache_preview_deletes_only_terminal_package_and_keeps_active_candidate(
    host_paths,
):
    from robopark_host.retention import (
        StorageBudget,
        execute_system_cleanup_plan,
        preview_system_cleanup_plan,
    )
    from robopark_host.state import atomic_write_json

    packages = host_paths.state / "ota-packages"
    receipts = host_paths.state / "ota-update-receipts"
    packages.mkdir(parents=True)
    receipts.mkdir(parents=True)
    terminal_bytes = b"finished" * 1024
    active_bytes = b"active" * 1024
    terminal_digest = hashlib.sha256(terminal_bytes).hexdigest()
    active_digest = hashlib.sha256(active_bytes).hexdigest()
    terminal = packages / f"{terminal_digest}.ota"
    active = packages / f"{active_digest}.ota"
    terminal.write_bytes(terminal_bytes)
    active.write_bytes(active_bytes)
    operation_id = uuid4()
    atomic_write_json(
        receipts / f"{operation_id}.json",
        {
            "operation_id": str(operation_id),
            "sha256": terminal_digest,
            "phase": "published",
        },
    )
    atomic_write_json(
        host_paths.state / "ota-update-journal.json",
        {"request": {"sha256": active_digest}, "phase": "staged"},
    )
    budget = StorageBudget(10**9, 0, minimum_free_bytes=10**7)

    plan = preview_system_cleanup_plan(host_paths, ["ota_cache"], budget, now=1_000_000)

    assert plan["blocked"] is False
    assert [(item["category"], item["path"]) for item in plan["planned"]] == [
        ("ota_cache", terminal.name)
    ]
    assert plan["planned"][0]["bytes"] == terminal.stat().st_blocks * 512
    assert terminal.exists() and active.exists()

    result = execute_system_cleanup_plan(
        host_paths, ["ota_cache"], budget, plan, now=1_000_000
    )

    assert result["deleted_count"] == 1
    assert not terminal.exists()
    assert active.exists()


def test_ota_cache_cleanup_blocks_when_journal_is_damaged(host_paths):
    from robopark_host.retention import StorageBudget, preview_system_cleanup_plan

    journal = host_paths.state / "ota-update-journal.json"
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text("{bad-json")

    plan = preview_system_cleanup_plan(
        host_paths, ["ota_cache"], StorageBudget(10**9, 0, minimum_free_bytes=1),
        now=1_000_000,
    )

    assert plan["blocked"] is True
    assert plan["unknown_categories"] == []
    assert plan["planned"] == []


def test_ota_cache_cleanup_blocks_unknown_journal_phase(host_paths):
    from robopark_host.retention import StorageBudget, preview_system_cleanup_plan
    from robopark_host.state import atomic_write_json

    atomic_write_json(
        host_paths.state / "ota-update-journal.json",
        {"request": {"sha256": "a" * 64}, "phase": "unexpected"},
    )
    packages = host_paths.state / "ota-packages"
    receipts = host_paths.state / "ota-update-receipts"
    packages.mkdir()
    receipts.mkdir()

    plan = preview_system_cleanup_plan(
        host_paths, ["ota_cache"], StorageBudget(10**9, 0, minimum_free_bytes=1),
        now=1_000_000,
    )

    assert plan["unknown_categories"] == []
    assert plan["blocked"] is True


def test_host_operation_accepts_exact_ota_cache_cleanup_category():
    from datetime import UTC, datetime

    from robopark_host.commands import validate_typed_operation

    operation_id = str(uuid4())
    now = datetime.now(UTC).isoformat()
    request = {
        "job_id": operation_id,
        "kind": "cleanup-preview",
        "actor_user_id": 1,
        "created_at": now,
        "capability_revision": "a" * 64,
        "confirmation": "ЗАПУСТИТЬ CLEANUP-PREVIEW",
        "categories": ["ota_cache"],
        "authorization": {
            "operation_id": operation_id,
            "operation_kind": "cleanup-preview",
            "actor_user_id": 1,
            "consumed": True,
            "validated_at": now,
        },
    }

    assert validate_typed_operation(request).payload["categories"] == ["ota_cache"]


def test_cleanup_prefers_terminal_ota_cache_to_unverified_backup(host_paths):
    from robopark_host.retention import StorageBudget, preview_system_cleanup_plan
    from robopark_host.state import atomic_write_json

    backup = host_paths.var / "backups" / f"backup-{uuid4()}.rpb"
    backup.parent.mkdir(parents=True)
    backup.write_bytes(b"backup" * 1024)
    package_bytes = b"cached" * 1024
    digest = hashlib.sha256(package_bytes).hexdigest()
    cache = host_paths.state / "ota-packages" / f"{digest}.ota"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(package_bytes)
    operation_id = uuid4()
    atomic_write_json(
        host_paths.state / "ota-update-receipts" / f"{operation_id}.json",
        {"operation_id": str(operation_id), "sha256": digest, "phase": "published"},
    )
    budget = StorageBudget(
        1000, 0, minimum_free_bytes=cache.stat().st_blocks * 512
    )

    plan = preview_system_cleanup_plan(
        host_paths, ["backups", "ota_cache"], budget, now=1_000_000
    )

    assert plan["blocked"] is False
    assert [(item["category"], item["path"]) for item in plan["planned"]] == [
        ("ota_cache", cache.name)
    ]
    assert backup.exists() and cache.exists()


def test_ota_cache_cleanup_refuses_package_replaced_after_preview(host_paths):
    from robopark_host.retention import (
        StorageBudget,
        execute_system_cleanup_plan,
        preview_system_cleanup_plan,
    )
    from robopark_host.state import atomic_write_json

    package_bytes = b"finished" * 1024
    digest = hashlib.sha256(package_bytes).hexdigest()
    cache = host_paths.state / "ota-packages" / f"{digest}.ota"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(package_bytes)
    operation_id = uuid4()
    atomic_write_json(
        host_paths.state / "ota-update-receipts" / f"{operation_id}.json",
        {"operation_id": str(operation_id), "sha256": digest, "phase": "published"},
    )
    budget = StorageBudget(1000, 0, minimum_free_bytes=cache.stat().st_blocks * 512)
    plan = preview_system_cleanup_plan(host_paths, ["ota_cache"], budget, now=1_000_000)
    assert len(plan["planned"]) == 1

    cache.unlink()
    cache.write_bytes(package_bytes)

    with pytest.raises(ValueError, match="cleanup_plan_changed"):
        execute_system_cleanup_plan(
            host_paths, ["ota_cache"], budget, plan, now=1_000_000
        )
    assert cache.exists()


def test_terminal_ota_cache_is_previewed_without_disk_pressure_or_backup(host_paths):
    from robopark_host.retention import (
        StorageBudget,
        preview_host_cleanup_plan,
        preview_system_cleanup_plan,
    )
    from robopark_host.state import atomic_write_json

    package_bytes = b"terminal" * 1024
    digest = hashlib.sha256(package_bytes).hexdigest()
    cache = host_paths.state / "ota-packages" / f"{digest}.ota"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(package_bytes)
    operation_id = uuid4()
    atomic_write_json(
        host_paths.state / "ota-update-receipts" / f"{operation_id}.json",
        {"operation_id": str(operation_id), "sha256": digest, "phase": "published"},
    )
    no_pressure = StorageBudget(1000, 1000, minimum_free_bytes=1)

    public = preview_system_cleanup_plan(
        host_paths, ["ota_cache"], no_pressure, now=1_000_000
    )
    strict_guard = preview_host_cleanup_plan(
        host_paths, ["ota_cache"], no_pressure, now=1_000_000,
        large_cleanup_bytes=1,
    )

    assert public["blocked"] is False
    assert [(item["category"], item["path"]) for item in public["planned"]] == [
        ("ota_cache", cache.name)
    ]
    assert strict_guard["blocked"] is False
    assert len(strict_guard["planned"]) == 1


def test_ota_cache_preview_does_not_rehash_verified_archive(host_paths, monkeypatch):
    from robopark_host import retention
    from robopark_host.state import atomic_write_json

    package_bytes = b"verified" * 1024
    digest = hashlib.sha256(package_bytes).hexdigest()
    cache = host_paths.state / "ota-packages" / f"{digest}.ota"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(package_bytes)
    operation_id = uuid4()
    atomic_write_json(
        host_paths.state / "ota-update-receipts" / f"{operation_id}.json",
        {"operation_id": str(operation_id), "sha256": digest, "phase": "published"},
    )
    original = retention._regular_sha256

    def no_archive_rehash(path, **kwargs):
        if path.parent == cache.parent:
            raise ValueError("archive_rehash_forbidden")
        return original(path, **kwargs)

    monkeypatch.setattr(retention, "_regular_sha256", no_archive_rehash)
    plan = retention.preview_system_cleanup_plan(
        host_paths, ["ota_cache"],
        retention.StorageBudget(1000, 1000, minimum_free_bytes=1),
        now=1_000_000,
    )

    assert plan["blocked"] is False
    assert len(plan["planned"]) == 1


def test_ota_cache_preview_reports_directory_read_failure(host_paths, monkeypatch):
    from robopark_host.retention import StorageBudget, preview_system_cleanup_plan

    packages = host_paths.state / "ota-packages"
    receipts = host_paths.state / "ota-update-receipts"
    packages.mkdir(parents=True)
    receipts.mkdir(parents=True)
    (packages / f"{'a' * 64}.ota").write_bytes(b"cached")
    original = os.scandir

    def unreadable(directory):
        if directory == receipts:
            raise OSError("disk read failed")
        return original(directory)

    monkeypatch.setattr(os, "scandir", unreadable)

    plan = preview_system_cleanup_plan(
        host_paths, ["ota_cache"], StorageBudget(1000, 1000, minimum_free_bytes=1),
        now=1_000_000,
    )

    assert plan["blocked"] is True
    assert plan["unknown_categories"] == []


def test_ota_only_preview_is_independent_of_unrelated_backup_artifacts(host_paths):
    from robopark_host.retention import StorageBudget, preview_system_cleanup_plan
    from robopark_host.state import atomic_write_json

    package_bytes = b"terminal" * 1024
    digest = hashlib.sha256(package_bytes).hexdigest()
    cache = host_paths.state / "ota-packages" / f"{digest}.ota"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(package_bytes)
    operation_id = uuid4()
    atomic_write_json(
        host_paths.state / "ota-update-receipts" / f"{operation_id}.json",
        {"operation_id": str(operation_id), "sha256": digest, "phase": "published"},
    )
    unrelated = host_paths.var / "backups" / "unexpected"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_bytes(b"keep")

    plan = preview_system_cleanup_plan(
        host_paths, ["ota_cache"], StorageBudget(1000, 1000, minimum_free_bytes=1),
        now=1_000_000,
    )

    assert plan["blocked"] is False
    assert [item["path"] for item in plan["planned"]] == [cache.name]
    assert unrelated.exists()


def test_system_cleanup_does_not_delete_backup_after_ephemeral_space_is_enough(
    host_paths,
):
    from robopark_host.retention import StorageBudget, preview_system_cleanup_plan

    diagnostic = host_paths.var / "diagnostics" / "old.log"
    diagnostic.parent.mkdir(parents=True)
    diagnostic.write_bytes(b"x" * 200)
    os.utime(diagnostic, (1, 1))
    backup = host_paths.var / "backups" / f"backup-{uuid4()}.rpb"
    backup.parent.mkdir(parents=True)
    backup.write_bytes(b"y" * 200)
    budget = StorageBudget(1000, 0, minimum_free_bytes=100)

    plan = preview_system_cleanup_plan(
        host_paths, ["diagnostics", "backups"], budget, now=1_000_000,
    )

    assert [(item["category"], item["path"]) for item in plan["planned"]] == [
        ("diagnostics", "old.log")
    ]
    assert diagnostic.exists() and backup.exists()


def test_system_cleanup_uses_backup_only_for_remaining_pressure(host_paths):
    from robopark_host.retention import (
        StorageBudget,
        execute_system_cleanup_plan,
        preview_system_cleanup_plan,
    )

    diagnostic = host_paths.var / "diagnostics" / "old.log"
    diagnostic.parent.mkdir(parents=True)
    diagnostic.write_bytes(b"x" * 50)
    os.utime(diagnostic, (1, 1))
    backup = host_paths.var / "backups" / f"backup-{uuid4()}.rpb"
    backup.parent.mkdir(parents=True)
    backup.write_bytes(b"y" * 200)
    budget = StorageBudget(
        1000, 0,
        minimum_free_bytes=(diagnostic.stat().st_blocks + backup.stat().st_blocks) * 512,
    )
    categories = ["diagnostics", "backups"]
    plan = preview_system_cleanup_plan(host_paths, categories, budget, now=1_000_000)
    assert [item["category"] for item in plan["planned"]] == ["diagnostics", "backups"]

    result = execute_system_cleanup_plan(host_paths, categories, budget, plan, now=1_000_000)

    assert result["deleted_count"] == 2
    assert not diagnostic.exists() and not backup.exists()


def test_host_cleanup_requires_persisted_preview_and_consumes_it_once(host_paths):
    from robopark_host.commands import SafeProductionTypedHostEffects
    from robopark_host.release import ReleaseError

    host_paths.state.mkdir(parents=True, exist_ok=True)
    victim = host_paths.var / "diagnostics" / "old.log"
    victim.parent.mkdir(parents=True)
    victim.write_bytes(b"old")
    os.utime(victim, (time.time() - 10 * 86400,) * 2)
    effects = SafeProductionTypedHostEffects(host_paths)

    with pytest.raises(ReleaseError, match="cleanup_plan_changed"):
        effects.cleanup_execute(str(uuid4()), str(uuid4()))
    assert victim.exists()

    preview = effects.cleanup_preview(str(uuid4()), ("diagnostics",))
    assert victim.exists()
    assert preview["planned"]
    with pytest.raises(ReleaseError, match="cleanup_plan_changed"):
        effects.cleanup_execute(str(uuid4()), str(uuid4()))
    assert victim.exists()

    result = effects.cleanup_execute(str(uuid4()), preview["plan_id"])
    assert result["deleted_count"] == 1
    assert not victim.exists()
    with pytest.raises(ReleaseError, match="cleanup_plan_changed"):
        effects.cleanup_execute(str(uuid4()), preview["plan_id"])


def test_host_cleanup_preserves_partial_result_after_consuming_plan(host_paths, monkeypatch):
    from robopark_host import retention
    from robopark_host.commands import SafeProductionTypedHostEffects

    host_paths.state.mkdir(parents=True, exist_ok=True)
    root = host_paths.var / "diagnostics"
    root.mkdir(parents=True)
    for name in ("first.log", "second.log"):
        victim = root / name
        victim.write_bytes(name.encode())
        os.utime(victim, (time.time() - 10 * 86400,) * 2)
    effects = SafeProductionTypedHostEffects(host_paths)
    preview = effects.cleanup_preview(str(uuid4()), ("diagnostics",))
    real_unlink = retention.os.unlink

    def fail_second(path, *args, **kwargs):
        if path == "second.log":
            raise OSError("injected unlink failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(retention.os, "unlink", fail_second)
    with pytest.raises(retention.CleanupPartialError) as caught:
        effects.cleanup_execute(str(uuid4()), preview["plan_id"])
    assert [item["path"] for item in caught.value.deleted] == ["first.log"]
    receipt = json.loads((host_paths.state / "cleanup-preview.json").read_text())
    assert receipt["consumed"] is True
    assert not (root / "first.log").exists()
    assert (root / "second.log").exists()


def test_host_cleanup_refuses_same_size_file_replaced_after_preview(host_paths):
    from robopark_host.commands import SafeProductionTypedHostEffects
    from robopark_host.release import ReleaseError

    host_paths.state.mkdir(parents=True, exist_ok=True)
    victim = host_paths.var / "diagnostics" / "old.log"
    victim.parent.mkdir(parents=True)
    victim.write_bytes(b"old")
    os.utime(victim, (time.time() - 10 * 86400,) * 2)
    effects = SafeProductionTypedHostEffects(host_paths)
    preview = effects.cleanup_preview(str(uuid4()), ("diagnostics",))
    assert preview["planned"]

    victim.unlink()
    victim.write_bytes(b"new")
    os.utime(victim, (time.time() - 10 * 86400,) * 2)
    with pytest.raises(ReleaseError, match="cleanup_plan_changed"):
        effects.cleanup_execute(str(uuid4()), preview["plan_id"])
    assert victim.read_bytes() == b"new"


def test_host_cleanup_refuses_expired_preview(host_paths):
    from robopark_host.commands import SafeProductionTypedHostEffects
    from robopark_host.release import ReleaseError

    host_paths.state.mkdir(parents=True, exist_ok=True)
    victim = host_paths.var / "diagnostics" / "old.log"
    victim.parent.mkdir(parents=True)
    victim.write_bytes(b"old")
    os.utime(victim, (time.time() - 10 * 86400,) * 2)
    effects = SafeProductionTypedHostEffects(host_paths)
    preview = effects.cleanup_preview(str(uuid4()), ("diagnostics",))
    receipt_path = host_paths.state / "cleanup-preview.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["created_at"] -= 601
    receipt_path.write_text(json.dumps(receipt))

    with pytest.raises(ReleaseError, match="cleanup_plan_changed"):
        effects.cleanup_execute(str(uuid4()), preview["plan_id"])
    assert victim.read_bytes() == b"old"


def _managed_cleanup_fixture(host_paths, *, guard_age=0):
    from robopark_host.state import atomic_write_json

    now = time.time()
    releases = {name: host_paths.releases / name for name in ("recovery-a", "obsolete-b", "previous-c", "current-d")}
    receipts = host_paths.state / "successful-releases"
    receipts.mkdir(parents=True, exist_ok=True)
    for index, (name, path) in enumerate(releases.items()):
        path.mkdir(parents=True)
        (path / "payload").write_bytes((name * 8).encode())
        receipt = receipts / f"{name}.json"
        receipt.write_text('{"successful":true}')
        os.utime(receipt, (index + 1, index + 1))
    host_paths.current.symlink_to(releases["current-d"])
    host_paths.previous.symlink_to(releases["previous-c"])
    host_paths.recovery.symlink_to(releases["recovery-a"])

    backup_root = host_paths.var / "backups"
    backup_root.mkdir(parents=True)
    guard_id = str(uuid4())
    guard = backup_root / f"backup-{guard_id}.rpb"
    guard.write_bytes(b"verified recovery backup")
    device_uuid = str(uuid4())
    mount = host_paths.root / "mnt/usb/robopark-backups"
    mount.mkdir(parents=True)
    usb_backup = mount / guard.name
    usb_backup.write_bytes(guard.read_bytes())
    usb_backup.chmod(0o600)
    device = host_paths.root / "dev/fake-usb"
    device.parent.mkdir(parents=True)
    device.touch()
    uuid_root = host_paths.root / "dev/disk/by-uuid"
    uuid_root.mkdir(parents=True)
    (uuid_root / device_uuid).symlink_to("../../fake-usb")
    removable = host_paths.root / "sys/class/block/fake-usb/removable"
    removable.parent.mkdir(parents=True)
    removable.write_text("1\n")
    mountinfo = host_paths.root / "proc/self/mountinfo"
    mountinfo.parent.mkdir(parents=True)
    mountinfo.write_text("1 0 8:1 / /mnt/usb rw - ext4 /dev/fake-usb rw\n")
    atomic_write_json(
        host_paths.state / "selected-usb.json",
        {"schema": 1, "device_uuid": device_uuid},
    )
    receipt = {
        "schema": 1,
        "backup_id": guard_id,
        "device_uuid": device_uuid,
        "verified": True,
        "sha256": hashlib.sha256(guard.read_bytes()).hexdigest(),
        "verified_at": now - guard_age,
        "recovery_required": True,
    }
    atomic_write_json(host_paths.state / "backup-receipts" / f"{guard_id}.json", receipt)
    old_id = str(uuid4())
    old = backup_root / f"backup-{old_id}.rpb"
    old.write_bytes(b"old unverified backup")
    os.utime(old, (1, 1))
    return now, guard, old, releases


def test_release_cleanup_preserves_candidate_in_nonterminal_update(host_paths):
    from robopark_host.retention import StorageBudget, preview_host_cleanup_plan
    from robopark_host.state import atomic_write_json

    now, _, _, releases = _managed_cleanup_fixture(host_paths)
    atomic_write_json(host_paths.state / "updater-journal.json", {
        "schema": 1,
        "job_id": str(uuid4()),
        "actor_user_id": 1,
        "candidate": "obsolete-b",
        "previous": "previous-c",
        "previous_config": "compose/previous.json",
        "original_previous": None,
        "phase": "building",
        "migration_started": False,
        "writes_resumed": False,
        "snapshot_done": False,
        "cutover_started": False,
        "publication_degraded": False,
        "error": None,
    })
    plan = preview_host_cleanup_plan(
        host_paths, ["releases"], StorageBudget(10_000, 0, minimum_free_bytes=10_000), now=now,
    )

    assert plan["blocked"] is False
    assert all(item["path"] != releases["obsolete-b"].name for item in plan["planned"])


def test_release_cleanup_blocks_when_updater_journal_is_invalid(host_paths):
    from robopark_host.retention import StorageBudget, preview_host_cleanup_plan

    now, _, _, _ = _managed_cleanup_fixture(host_paths)
    (host_paths.state / "updater-journal.json").write_text("{}")
    plan = preview_host_cleanup_plan(
        host_paths, ["releases"], StorageBudget(10_000, 0, minimum_free_bytes=10_000), now=now,
    )
    assert plan["blocked"] is True
    assert plan["planned"] == []


@pytest.mark.parametrize("change", ["selection", "legacy", "usb-artifact"])
def test_cleanup_guard_rejects_stale_usb_binding(host_paths, change):
    from robopark_host.operational_state import read_object
    from robopark_host.retention import (
        StorageBudget,
        execute_host_cleanup_plan,
        preview_host_cleanup_plan,
    )
    from robopark_host.state import atomic_write_json

    now, guard, old, releases = _managed_cleanup_fixture(host_paths)
    budget = StorageBudget(10_000, 0, minimum_free_bytes=10_000)
    plan = preview_host_cleanup_plan(host_paths, ["releases"], budget, now=now, large_cleanup_bytes=1)
    assert plan["blocked"] is False
    assert plan["guard"]["device_uuid"] == read_object(host_paths.state / "selected-usb.json")["device_uuid"]
    if change == "selection":
        atomic_write_json(host_paths.state / "selected-usb.json", {"schema": 1, "device_uuid": str(uuid4())})
    elif change == "legacy":
        path = next((host_paths.state / "backup-receipts").iterdir())
        receipt = read_object(path)
        receipt.pop("device_uuid")
        atomic_write_json(path, receipt)
    else:
        (host_paths.root / "mnt/usb/robopark-backups" / guard.name).write_bytes(b"changed")
    with pytest.raises(ValueError, match="cleanup_plan_changed"):
        execute_host_cleanup_plan(host_paths, ["releases"], budget, plan, now=now, large_cleanup_bytes=1)
    assert old.exists() and releases["obsolete-b"].exists()


def test_managed_cleanup_refuses_modified_release_with_same_size_and_mtime(host_paths):
    from robopark_host.retention import (
        StorageBudget,
        execute_host_cleanup_plan,
        preview_host_cleanup_plan,
    )

    now, _, _, releases = _managed_cleanup_fixture(host_paths)
    budget = StorageBudget(10_000, 0, minimum_free_bytes=10_000)
    plan = preview_host_cleanup_plan(host_paths, ["releases"], budget, now=now)
    assert any(item["path"] == "obsolete-b" for item in plan["planned"])
    target = releases["obsolete-b"] / "payload"
    before = target.stat()
    target.write_bytes(b"z" * before.st_size)
    os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))

    with pytest.raises(ValueError, match="cleanup_plan_changed"):
        execute_host_cleanup_plan(host_paths, ["releases"], budget, plan, now=now)
    assert target.exists()


def test_managed_cleanup_plans_actual_backup_and_release_candidates(host_paths):
    from robopark_host.retention import (
        StorageBudget,
        execute_host_cleanup_plan,
        preview_host_cleanup_plan,
    )

    now, guard, old, releases = _managed_cleanup_fixture(host_paths)
    plan = preview_host_cleanup_plan(
        host_paths,
        ["backups", "releases"],
        StorageBudget(10_000, 0, minimum_free_bytes=10_000),
        now=now,
        large_cleanup_bytes=1,
    )

    planned = {(item["category"], item["path"]) for item in plan["planned"]}
    assert ("backups", old.name) in planned
    assert ("releases", "obsolete-b") in planned
    assert all(guard.name != item["path"] for item in plan["planned"])
    assert not any(item["path"] in {"recovery-a", "previous-c", "current-d"} for item in plan["planned"])
    assert plan["blocked"] is False
    result = execute_host_cleanup_plan(
        host_paths,
        ["backups", "releases"],
        StorageBudget(10_000, 0, minimum_free_bytes=10_000),
        plan,
        now=now,
        large_cleanup_bytes=1,
    )
    assert result["plan_id"] == plan["plan_id"]
    assert not old.exists() and not releases["obsolete-b"].exists()
    assert guard.exists() and releases["recovery-a"].exists()


def test_managed_cleanup_preview_uses_allocated_backup_and_release_bytes(host_paths):
    from robopark_host.retention import (
        StorageBudget,
        preview_host_cleanup_plan,
    )

    now, _, old, releases = _managed_cleanup_fixture(host_paths)
    with old.open("r+b") as stream:
        stream.truncate(8 * 1024 * 1024)
    release = releases["obsolete-b"]
    with (release / "payload").open("r+b") as stream:
        stream.truncate(8 * 1024 * 1024)
    plan = preview_host_cleanup_plan(
        host_paths,
        ["backups", "releases"],
        StorageBudget(10_000, 0, minimum_free_bytes=10_000),
        now=now,
        large_cleanup_bytes=1,
    )
    planned = {(item["category"], item["path"]): item["bytes"] for item in plan["planned"]}
    assert planned[("backups", old.name)] == old.stat().st_blocks * 512
    assert planned[("releases", release.name)] == sum(
        path.stat().st_blocks * 512 for path in [release, *release.rglob("*")]
    )


@pytest.mark.parametrize("guard", ["missing", "stale", "changed"])
def test_managed_cleanup_requires_fresh_unchanged_verified_backup_guard(
    host_paths, guard
):
    from robopark_host.retention import (
        StorageBudget,
        execute_host_cleanup_plan,
        preview_host_cleanup_plan,
    )

    now, verified, old, releases = _managed_cleanup_fixture(
        host_paths, guard_age=90_000 if guard == "stale" else 0
    )
    if guard == "missing":
        next((host_paths.state / "backup-receipts").iterdir()).unlink()
    budget = StorageBudget(10_000, 0, minimum_free_bytes=10_000)
    plan = preview_host_cleanup_plan(
        host_paths,
        ["backups", "releases"],
        budget,
        now=now,
        large_cleanup_bytes=1,
        backup_guard_max_age=86_400,
    )
    if guard in {"missing", "stale"}:
        assert plan["blocked"] is True
        assert old.exists() and releases["obsolete-b"].exists()
        return

    assert plan["blocked"] is False
    verified.write_bytes(b"changed after preview")
    with pytest.raises(ValueError, match="cleanup_plan_changed"):
        execute_host_cleanup_plan(
            host_paths,
            ["backups", "releases"],
            budget,
            plan,
            now=now,
            large_cleanup_bytes=1,
            backup_guard_max_age=86_400,
        )
    assert old.exists() and releases["obsolete-b"].exists()


@pytest.mark.parametrize("guard", ["fresh", "missing", "stale", "changed"])
def test_release_only_cleanup_uses_independent_verified_backup_guard(host_paths, guard):
    from robopark_host.retention import (
        StorageBudget,
        execute_host_cleanup_plan,
        preview_host_cleanup_plan,
    )

    now, verified, old, releases = _managed_cleanup_fixture(
        host_paths, guard_age=90_000 if guard == "stale" else 0
    )
    if guard == "missing":
        next((host_paths.state / "backup-receipts").iterdir()).unlink()
    if guard == "changed":
        verified.write_bytes(b"changed before preview")
    budget = StorageBudget(10_000, 0, minimum_free_bytes=10_000)
    plan = preview_host_cleanup_plan(
        host_paths,
        ["releases"],
        budget,
        now=now,
        large_cleanup_bytes=1,
        backup_guard_max_age=86_400,
    )

    if guard != "fresh":
        assert plan["blocked"] is True
        assert releases["obsolete-b"].exists()
        return

    assert plan["blocked"] is False
    assert plan["guard"]["backup_id"] in verified.name
    result = execute_host_cleanup_plan(
        host_paths,
        ["releases"],
        budget,
        plan,
        now=now,
        large_cleanup_bytes=1,
        backup_guard_max_age=86_400,
    )
    assert result["deleted_count"] == 1
    assert not releases["obsolete-b"].exists()
    assert verified.exists() and old.exists()


def test_storage_cleanup_rejects_a_root_below_a_symlinked_parent(tmp_path):
    from robopark_host.retention import StorageBudget, cleanup_storage_roots

    outside = tmp_path / "outside"
    diagnostics = outside / "diagnostics"
    diagnostics.mkdir(parents=True)
    victim = diagnostics / "entry"
    victim.write_bytes(b"safe")
    alias = tmp_path / "alias"
    alias.symlink_to(outside, target_is_directory=True)

    report = cleanup_storage_roots(
        {"diagnostics": alias / "diagnostics"},
        StorageBudget(100, 0, minimum_free_bytes=3),
        dry_run=False,
    )

    assert report["blocked"] is True
    assert victim.read_bytes() == b"safe"


def test_storage_cleanup_parent_replacement_cannot_delete_outside(
    tmp_path, monkeypatch
):
    import os

    from robopark_host import retention

    owned = tmp_path / "owned"
    outside = tmp_path / "outside"
    owned.mkdir()
    outside.mkdir()
    (owned / "victim").write_bytes(b"owned")
    protected = outside / "victim"
    protected.write_bytes(b"outside")
    moved = tmp_path / "moved"
    real_unlink = os.unlink
    replaced = False

    def replace_parent_then_unlink(name, *, dir_fd=None):
        nonlocal replaced
        if not replaced:
            owned.rename(moved)
            owned.symlink_to(outside, target_is_directory=True)
            replaced = True
        return real_unlink(name, dir_fd=dir_fd)

    monkeypatch.setattr(retention.os, "unlink", replace_parent_then_unlink)
    report = retention.cleanup_storage_roots(
        {"diagnostics": owned},
        retention.StorageBudget(100, 0, minimum_free_bytes=1),
        dry_run=False,
    )

    assert report["deleted_count"] == 1
    assert protected.read_bytes() == b"outside"


def test_storage_cleanup_scan_and_report_are_bounded(tmp_path):
    from robopark_host.retention import StorageBudget, cleanup_storage_roots

    diagnostics = tmp_path / "diagnostics"
    diagnostics.mkdir()
    for index in range(1000):
        (diagnostics / f"entry-{index:04d}").write_bytes(b"x")
    for index in range(1000):
        (diagnostics / f"link-{index:04d}").symlink_to(diagnostics / "entry-0000")

    report = cleanup_storage_roots(
        {"diagnostics": diagnostics},
        StorageBudget(10_000, 0, minimum_free_bytes=1024**3),
        dry_run=True,
        max_deletions=128,
    )

    assert len(report["planned"]) == 128
    assert report["skipped_counts"] == {"not_owned_file": 1000}
    assert "skipped" not in report


def test_candidate_bound_does_not_hide_a_category_over_its_cap(tmp_path, monkeypatch):
    import os

    from robopark_host import retention
    from robopark_host.retention import StorageBudget, cleanup_storage_roots

    diagnostics = tmp_path / "diagnostics"
    logs = tmp_path / "logs"
    diagnostics.mkdir()
    logs.mkdir()
    expired = diagnostics / "expired"
    expired.write_bytes(b"old")
    os.utime(expired, (1, 1))
    (diagnostics / "fresh").write_bytes(b"fresh")
    oversized_log = logs / "fresh.log"
    oversized_log.write_bytes(b"x" * 100)
    monkeypatch.setitem(retention.STORAGE_MAX_BYTES, "logs", 1)

    report = cleanup_storage_roots(
        {"diagnostics": diagnostics, "logs": logs},
        StorageBudget(100, 100, minimum_free_bytes=0),
        dry_run=True,
        max_deletions=1,
        now=8 * 86400,
    )

    assert report["planned"] == [
        {"category": "logs", "path": "fresh.log", "bytes": oversized_log.stat().st_blocks * 512}
    ]


def _fixture(root: Path, *, model: str, compatible: str, devices=(), plugins=()):
    (root / "proc/device-tree").mkdir(parents=True)
    (root / "proc/device-tree/model").write_text(model)
    (root / "proc/device-tree/compatible").write_text(compatible)
    for device in devices:
        path = root / device.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    for plugin in plugins:
        path = root / plugin.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()


def test_capability_fixtures_require_a_healthy_jpeg_probe(tmp_path):
    from robopark_host.capabilities import probe_host_capabilities

    cases = [
        ("vim4", "Khadas VIM4", "khadas,vim4", (), (), "vim4", False),
        (
            "new-vim4",
            "Khadas New VIM4",
            "khadas,new-vim4",
            ("/dev/aml-npu",),
            ("/usr/lib/libgstaml.so",),
            "new-vim4",
            True,
        ),
        (
            "orin",
            "NVIDIA Jetson AGX Orin",
            "nvidia,tegra234",
            ("/dev/nvhost-nvdec",),
            ("/usr/lib/libnvjpeg.so",),
            "orin",
            False,
        ),
        ("generic", "Generic ARM", "linux,dummy-virt", (), (), "generic-arm", False),
    ]
    for name, model, compatible, devices, plugins, profile, npu in cases:
        root = tmp_path / name
        _fixture(
            root, model=model, compatible=compatible, devices=devices, plugins=plugins
        )
        capability = probe_host_capabilities(
            root, jpeg_health_probe=lambda backend: False
        )
        assert capability.profile == profile
        assert capability.jpeg_backend == "software"
        assert capability.npu_available is npu

    healthy_orin = probe_host_capabilities(
        tmp_path / "orin", jpeg_health_probe=lambda backend: backend == "nvjpeg"
    )
    assert healthy_orin.jpeg_backend == "software"
    assert healthy_orin.hardware_jpeg is False
    assert healthy_orin.cuda_available is True


def test_doctor_names_pressure_category_and_last_cleanup(host_paths):
    from robopark_host.doctor import _storage_retention_check
    from robopark_host.state import atomic_write_json

    atomic_write_json(
        host_paths.state / "storage-retention.json",
        {
            "blocked": True,
            "pressure": True,
            "pressure_category": "diagnostics",
            "completed_at": 123,
        },
    )
    check = _storage_retention_check(host_paths)
    assert check.status == "failed"
    assert "diagnostics" in check.message
    assert "123" in check.message


@pytest.mark.parametrize(
    ("busy", "pressure", "expected_status"),
    [
        (True, False, "warning"),
        (True, True, "failed"),
        ("true", False, "failed"),
    ],
)
def test_doctor_distinguishes_deferred_storage_cleanup_from_failure(
    host_paths, busy, pressure, expected_status
):
    from robopark_host.doctor import _storage_retention_check
    from robopark_host.state import atomic_write_json

    atomic_write_json(
        host_paths.state / "storage-retention.json",
        {
            "blocked": True,
            "busy": busy,
            "pressure": pressure,
            "completed_at": 123,
        },
    )

    check = _storage_retention_check(host_paths)

    assert check.status == expected_status
    if expected_status == "warning":
        assert "отложена" in check.message


def test_public_host_projection_is_sanitized(host_paths):
    import json
    import time

    from robopark_host.capabilities import HostCapabilities, write_capabilities

    public = host_paths.var / "api-ops/host-health.json"
    write_capabilities(
        host_paths.state / "capabilities.json",
        HostCapabilities(
            "orin", "secret serial model", "software", False, False, True, True
        ),
        public_path=public,
    )

    value = json.loads(public.read_text())
    assert value["capabilities"]["profile"] == "orin"
    assert abs(time.time() - value["capabilities"]["checked_at"]) < 60
    assert "model" not in value["capabilities"]
    assert str(host_paths.root) not in public.read_text()


def test_scheduled_storage_retention_uses_only_named_ephemeral_roots(
    host_paths, monkeypatch
):
    from robopark_host import retention

    diagnostics = host_paths.var / "diagnostics"
    diagnostics.mkdir(parents=True)
    (diagnostics / "old").write_bytes(b"diagnostic")
    primary = host_paths.var / "inventory.db"
    primary.write_bytes(b"primary")
    monkeypatch.setattr(
        retention.StorageBudget,
        "for_path",
        classmethod(lambda cls, path: cls(100, 0, minimum_free_bytes=5)),
    )

    report = retention.retain_storage(host_paths)

    assert report["deleted_count"] == 1
    assert primary.read_bytes() == b"primary"
    assert (host_paths.state / "storage-retention.json").is_file()


def test_scheduled_storage_retention_publishes_real_pending_operation_as_busy(
    host_paths, monkeypatch
):
    from robopark_host import retention, storage_inventory
    from robopark_host.state import atomic_write_json

    host_paths.state.mkdir(parents=True, exist_ok=True)
    atomic_write_json(host_paths.state / "command-request.json", {"operation_id": str(uuid4())})
    monkeypatch.setattr(
        storage_inventory,
        "collect_storage_inventory",
        lambda paths: {"sampled_at": 42.0, "category_bytes": {}},
    )

    report = retention.retain_storage(host_paths)
    public = json.loads((host_paths.var / "api-ops/host-health.json").read_text())

    assert report["busy"] is True
    assert report["blocked"] is True
    assert report["pressure"] is False
    assert public["storage"]["busy"] is True
