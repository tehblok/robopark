"""Root retention of named exchange artifacts; no traversal, volumes or code pruning.

Standalone caller owns host.lock then API begin.lock. File deletion is relative to
pinned directory descriptors. Root receipts compact into a permanent bounded Bloom
filter: false positives reject a command, never replay it. It is never reset.
"""

import fcntl
import hashlib
import heapq
import json
import os
import re
import shutil
import stat
import time
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .operational_state import read_object
from .release import unique_object
from .state import atomic_write_json

MAX_BYTES = 2 * 1024**3
MAX_AGE = 7 * 86400
INSPECTION_TTL = 86400
STAGING_TTL = 86400
RECEIPT_BYTES = 8 * 1024**2
BLOOM_BYTES = 1024**2
MAX_FILL = 0.4
MAX_ENTRIES = 20000
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
GIB = 1024**3
MIN_FREE_BYTES = 6 * GIB
MIN_FREE_RATIO = 0.15
MAX_STORAGE_DELETIONS = 128
STORAGE_CATEGORIES = ("diagnostics", "logs")
MANAGED_STORAGE_CATEGORIES = ("backups", "releases", "ota_cache")
STORAGE_PRIORITY = {name: index for index, name in enumerate(STORAGE_CATEGORIES)}
STORAGE_TTL = {
    "diagnostics": 7 * 86400,
    "logs": 14 * 86400,
}
STORAGE_MAX_BYTES = {"logs": 256 * 1024**2}


class CleanupPartialError(ValueError):
    """A consumed plan failed after one or more targets may have been removed."""

    def __init__(self, deleted: list[dict], uncertain_target: dict | None = None):
        super().__init__("cleanup_plan_changed")
        self.deleted = deleted
        self.uncertain_target = uncertain_target


def _allocated_bytes(info: os.stat_result) -> int:
    """Match disk inventory and the blocks a local unlink can actually free."""
    return info.st_blocks * 512


@dataclass(frozen=True, slots=True)
class StorageBudget:
    """One pressure calculation shared by every allowlisted cleanup category."""

    partition_bytes: int
    free_bytes: int
    minimum_free_bytes: int = MIN_FREE_BYTES
    minimum_free_ratio: float = MIN_FREE_RATIO

    @property
    def floor_bytes(self) -> int:
        return max(
            self.minimum_free_bytes, int(self.partition_bytes * self.minimum_free_ratio)
        )

    @property
    def bytes_to_reclaim(self) -> int:
        return max(0, self.floor_bytes - self.free_bytes)

    @classmethod
    def for_path(cls, path: Path) -> "StorageBudget":
        usage = shutil.disk_usage(path)
        return cls(partition_bytes=getattr(usage, "total", 0), free_bytes=usage.free)


def cleanup_storage_roots(
    roots: dict[str, Path],
    budget: StorageBudget,
    *,
    dry_run: bool,
    max_deletions: int = MAX_STORAGE_DELETIONS,
    now: float | None = None,
    expected_identities: list[dict[str, object]] | None = None,
) -> dict:
    """Delete direct regular files from explicit roots in the pressure order.

    Directory recursion is deliberately absent. A caller must name every owned root;
    symlinks, hard links, directories and unknown categories are reported but untouched.
    """

    now = time.time() if now is None else now
    unknown = sorted(set(roots) - set(STORAGE_CATEGORIES))
    blocked = bool(unknown)
    candidates: list[tuple[int, int, int, str, str, os.stat_result, int]] = []
    skipped_counts: dict[str, int] = {}
    opened: list[int] = []
    category_totals: dict[str, int] = {}
    expected_by_path = (
        {(item["category"], item["path"]): item for item in expected_identities}
        if expected_identities is not None else None
    )

    def retain_oldest(heap: list, item: tuple) -> None:
        if len(heap) < max(0, max_deletions):
            heapq.heappush(heap, item)
        elif heap and item > heap[0]:
            heapq.heapreplace(heap, item)

    def skip(reason: str) -> None:
        skipped_counts[reason] = skipped_counts.get(reason, 0) + 1

    def open_pinned(path: Path) -> int:
        descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        opened.append(descriptor)
        for part in path.absolute().parts[1:]:
            descriptor = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            opened.append(descriptor)
        return descriptor

    for category in STORAGE_CATEGORIES:
        root = roots.get(category)
        if root is None:
            continue
        try:
            descriptor = open_pinned(root)
            oldest: list[tuple[int, str, os.stat_result, int]] = []
            expired: list[tuple[int, str, os.stat_result, int]] = []
            with os.scandir(descriptor) as listing:
                for entry in listing:
                    info = entry.stat(follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        skip("not_owned_file")
                        continue
                    category_totals[category] = (
                        category_totals.get(category, 0) + _allocated_bytes(info)
                    )
                    item = (-info.st_mtime_ns, entry.name, info, descriptor)
                    retain_oldest(oldest, item)
                    if now - info.st_mtime >= STORAGE_TTL[category]:
                        retain_oldest(expired, item)
            over_cap = category_totals.get(category, 0) > STORAGE_MAX_BYTES.get(
                category, 2**63 - 1
            )
            selected = oldest if over_cap or budget.bytes_to_reclaim else expired
            reason_priority = 0 if over_cap else 2 if budget.bytes_to_reclaim else 1
            for mtime, name, info, owner_fd in selected:
                candidates.append(
                    (
                        reason_priority,
                        STORAGE_PRIORITY[category],
                        mtime,
                        category,
                        name,
                        info,
                        owner_fd,
                    )
                )
        except FileNotFoundError:
            continue
        except NotADirectoryError:
            blocked = True
            skip("unsafe_root")
        except OSError:
            blocked = True
            skip("scan_failed")
    candidates.sort(key=lambda item: (item[0], item[1], -item[2], item[4]))
    candidates = candidates[: max(0, max_deletions)]
    target = budget.bytes_to_reclaim
    reclaimed = 0
    planned: list[dict[str, object]] = []
    identities: list[dict[str, object]] = []
    deleted: list[dict[str, object]] = []
    for _, _, _, category, name, before, descriptor in candidates:
        expired = now - before.st_mtime >= STORAGE_TTL[category]
        over_category_budget = category_totals.get(category, 0) > STORAGE_MAX_BYTES.get(
            category, 2**63 - 1
        )
        under_pressure = reclaimed < target
        if len(planned) >= max(0, max_deletions):
            break
        if not (expired or over_category_budget or under_pressure):
            continue
        identity = {
            "category": category, "path": name,
            "dev": before.st_dev, "ino": before.st_ino,
            "mtime_ns": before.st_mtime_ns, "ctime_ns": before.st_ctime_ns,
        }
        if expected_by_path is not None and expected_by_path.get((category, name)) != identity:
            blocked = True
            skip("artifact_changed")
            break
        size = _allocated_bytes(before)
        item = {"category": category, "path": name, "bytes": size}
        planned.append(item)
        identities.append(identity)
        if dry_run:
            reclaimed += size
            category_totals[category] -= size
            continue
        try:
            current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if not stat.S_ISREG(current.st_mode) or (
                current.st_dev,
                current.st_ino,
                current.st_size,
                current.st_mtime_ns,
                current.st_ctime_ns,
            ) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns):
                raise RetentionBlocked("artifact_changed")
            os.unlink(name, dir_fd=descriptor)
            deleted.append(item)
            reclaimed += size
            category_totals[category] -= size
        except (OSError, ValueError):
            blocked = True
            skip("delete_failed")
            break
    for descriptor in reversed(opened):
        os.close(descriptor)
    category_bytes = category_totals
    pressure_category = (
        planned[-1]["category"]
        if planned and budget.free_bytes + reclaimed < budget.floor_bytes
        else None
    )
    return {
        "dry_run": dry_run,
        "bounded": len(planned) <= max(0, max_deletions),
        "floor_bytes": budget.floor_bytes,
        "bytes_to_reclaim": target,
        "reclaimed_bytes": reclaimed,
        "planned": planned,
        "identities": identities,
        "deleted": deleted,
        "deleted_count": len(deleted),
        "pressure": budget.free_bytes + reclaimed < budget.floor_bytes,
        "blocked": blocked,
        "unknown_categories": unknown,
        "skipped_counts": skipped_counts,
        "category_bytes": category_bytes,
        "pressure_category": pressure_category,
        "completed_at": time.time(),
    }


def _cleanup_plan_body(report):
    return {
        "schema": 1,
        "bounded": report["bounded"],
        "floor_bytes": report["floor_bytes"],
        "bytes_to_reclaim": report["bytes_to_reclaim"],
        "planned": report["planned"],
        "identities": report["identities"],
        "blocked": report["blocked"],
        "unknown_categories": report["unknown_categories"],
    }


def preview_cleanup_plan(roots, budget, *, max_deletions=MAX_STORAGE_DELETIONS, now=None):
    """Create a content-addressed plan; execution must present this exact preview."""

    report = cleanup_storage_roots(
        roots, budget, dry_run=True, max_deletions=max_deletions, now=now
    )
    body = _cleanup_plan_body(report)
    encoded = json.dumps(body, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return {**body, "plan_id": str(uuid.uuid5(uuid.NAMESPACE_URL, encoded))}


def execute_cleanup_plan(
    roots, budget, plan, *, max_deletions=MAX_STORAGE_DELETIONS, now=None
):
    """Execute only when a fresh scan still equals the immutable preview."""

    expected = preview_cleanup_plan(
        roots, budget, max_deletions=max_deletions, now=now
    )
    if plan != expected or expected["blocked"]:
        raise ValueError("cleanup_plan_changed")
    result = cleanup_storage_roots(
        roots, budget, dry_run=False, max_deletions=max_deletions, now=now,
        expected_identities=plan["identities"],
    )
    if result["blocked"] or result["deleted"] != plan["planned"]:
        if result["deleted"]:
            next_target = next(
                (item for item in plan["planned"] if item not in result["deleted"]), None
            )
            raise CleanupPartialError(result["deleted"], next_target)
        raise ValueError("cleanup_plan_changed")
    return {**result, "plan_id": expected["plan_id"]}


def _regular_sha256(path: Path, *, max_bytes: int = 4 * 1024**3) -> tuple[int, str]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    digest = hashlib.sha256()
    total = 0
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid not in {0, os.geteuid()}
        ):
            raise RetentionBlocked("unsafe_cleanup_artifact")
        while chunk := stream.read(1024 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise RetentionBlocked("cleanup_artifact_limit")
            digest.update(chunk)
    return _allocated_bytes(info), digest.hexdigest()


def _ota_cache_identity(path: Path) -> tuple[int, str]:
    if re.fullmatch(r"[a-f0-9]{64}\.ota", path.name) is None:
        raise RetentionBlocked("unsafe_ota_cache")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid not in {0, os.geteuid()}
            or not 0 < info.st_size <= MAX_BYTES
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise RetentionBlocked("unsafe_ota_cache")
    after = path.lstat()
    fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
    identity = tuple(getattr(info, key) for key in fields)
    if identity != tuple(getattr(after, key) for key in fields):
        raise RetentionBlocked("unsafe_ota_cache")
    fingerprint = hashlib.sha256(repr((identity, path.stem)).encode()).hexdigest()
    return _allocated_bytes(info), fingerprint


def _backup_receipts(paths):
    from .commands import SafeProductionTypedHostEffects
    from .release import ReleaseError

    receipts = paths.state / "backup-receipts"
    result = {}
    if not receipts.exists():
        return result
    if receipts.is_symlink() or not receipts.is_dir():
        raise RetentionBlocked("unsafe_backup_receipts")
    entries = list(receipts.iterdir())
    if len(entries) > MAX_ENTRIES:
        raise RetentionBlocked("backup_receipt_limit")
    effects = SafeProductionTypedHostEffects(paths)
    for receipt in entries:
        if receipt.is_symlink() or not re.fullmatch(UUID + r"\.json", receipt.name):
            raise RetentionBlocked("unsafe_backup_receipt")
        try:
            value = effects.verified_backup_receipt(receipt.stem)
        except (OSError, ValueError, TypeError, ReleaseError) as exc:
            raise RetentionBlocked("unsafe_backup_receipt") from exc
        result[receipt.stem] = value
    return result


def _managed_cleanup_snapshot(
    paths,
    categories,
    budget,
    *,
    now,
    max_deletions,
    large_cleanup_bytes,
    backup_guard_max_age,
):
    requested = tuple(categories)
    unknown = sorted(set(requested) - set(MANAGED_STORAGE_CATEGORIES))
    blocked = bool(unknown) or not requested or len(requested) != len(set(requested))
    planned = []
    guard = None
    try:
        needs_backup_scan = bool(set(requested) & {"backups", "releases"})
        receipts = _backup_receipts(paths) if needs_backup_scan else {}
        backup_root = paths.var / "backups"
        # A verified backup is the safety guard for destructive release cleanup,
        # independently of whether backup artifacts themselves were requested.
        if needs_backup_scan and backup_root.exists():
            if (
                backup_root.is_symlink()
                or not backup_root.is_dir()
                or backup_root.resolve().parent != paths.var.resolve()
            ):
                raise RetentionBlocked("unsafe_backup_root")
            entries = list(backup_root.iterdir())
            if len(entries) > MAX_ENTRIES:
                raise RetentionBlocked("backup_artifact_limit")
            for artifact in sorted(entries, key=lambda item: item.name):
                match = re.fullmatch(r"backup-(" + UUID + r")\.rpb", artifact.name)
                if match is None:
                    raise RetentionBlocked("unknown_backup_artifact")
                identity = match.group(1)
                size, digest = _regular_sha256(artifact)
                receipt = receipts.get(identity)
                if receipt is not None:
                    if digest != receipt["sha256"]:
                        raise RetentionBlocked("verified_backup_changed")
                    if now - receipt["verified_at"] <= backup_guard_max_age:
                        candidate = {
                            "backup_id": identity,
                            "device_uuid": receipt["device_uuid"],
                            "sha256": digest,
                            "verified_at": receipt["verified_at"],
                        }
                        if guard is None or candidate["verified_at"] > guard["verified_at"]:
                            guard = candidate
                    # Every verified or recovery-required artifact is protected.
                    continue
                if "backups" in requested:
                    planned.append(
                        {
                            "category": "backups",
                            "path": artifact.name,
                            "bytes": size,
                            "fingerprint": digest,
                        }
                    )
        if "releases" in requested:
            from .restore_retention import protected_release_names
            from .updater import _load_journal, _successful_release_receipts

            protected = protected_release_names(paths)
            journal = _load_journal(paths)
            if journal and journal["phase"] not in {"succeeded", "rolled_back", "failed"}:
                protected.update((journal["previous"], journal["candidate"]))
            for name, _, _ in _successful_release_receipts(paths):
                if name in protected:
                    continue
                target = paths.releases / name
                if target.parent != paths.releases or target.is_symlink() or not target.is_dir():
                    raise RetentionBlocked("unsafe_release_path")
                digest = hashlib.sha256()
                root_info = target.lstat()
                digest.update(str((
                    root_info.st_dev, root_info.st_ino, root_info.st_mode,
                    root_info.st_mtime_ns, root_info.st_ctime_ns,
                )).encode())
                size = _allocated_bytes(root_info)
                count = 0
                for root, directories, files in os.walk(target, followlinks=False):
                    directories.sort()
                    files.sort()
                    for entry_name in [*directories, *files]:
                        entry = Path(root) / entry_name
                        info = entry.lstat()
                        count += 1
                        if count > MAX_ENTRIES or entry.is_symlink():
                            raise RetentionBlocked("unsafe_release_path")
                        if stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode):
                            size += _allocated_bytes(info)
                        else:
                            raise RetentionBlocked("unsafe_release_path")
                        digest.update(entry.relative_to(target).as_posix().encode())
                        digest.update(str((
                            info.st_dev, info.st_ino, info.st_mode, info.st_size,
                            info.st_mtime_ns, info.st_ctime_ns,
                        )).encode())
                planned.append(
                    {
                        "category": "releases",
                        "path": name,
                        "bytes": size,
                        "fingerprint": digest.hexdigest(),
                    }
                )
        if "ota_cache" in requested:
            from .ota_store import OtaPackageStore

            store = OtaPackageStore(paths)
            journal = paths.state / "ota-update-journal.json"
            cache_root = store.packages
            receipt_root = paths.state / "ota-update-receipts"
            if (
                journal.exists() and not read_object(journal)
                or cache_root.is_symlink()
                or receipt_root.is_symlink()
                or cache_root.exists() and not cache_root.is_dir()
                or receipt_root.exists() and not receipt_root.is_dir()
            ):
                raise RetentionBlocked("unsafe_ota_cache")
            for target in store.terminal_cache_candidates():
                size, fingerprint = _ota_cache_identity(target)
                planned.append({
                    "category": "ota_cache",
                    "path": target.name,
                    "bytes": size,
                    "fingerprint": fingerprint,
                })
    except (OSError, ValueError, RetentionBlocked):
        blocked = True
    managed_priority = {"ota_cache": 0, "backups": 1, "releases": 2}
    planned.sort(key=lambda item: (managed_priority[item["category"]], item["path"]))
    planned = planned[:max(0, max_deletions)]
    target = budget.bytes_to_reclaim
    selected = []
    reclaimed = 0
    for item in planned:
        if item["category"] != "ota_cache" and reclaimed >= target:
            break
        selected.append(item)
        reclaimed += item["bytes"]
    guarded_bytes = sum(
        item["bytes"] for item in selected if item["category"] != "ota_cache"
    )
    if guarded_bytes >= large_cleanup_bytes and guard is None:
        blocked = True
    return {
        "schema": 1,
        "bounded": len(selected) <= max(0, max_deletions),
        "floor_bytes": budget.floor_bytes,
        "bytes_to_reclaim": target,
        "planned": selected,
        "blocked": blocked,
        "unknown_categories": unknown,
        "guard": guard,
    }


def preview_host_cleanup_plan(
    paths,
    categories,
    budget,
    *,
    max_deletions=MAX_STORAGE_DELETIONS,
    now=None,
    large_cleanup_bytes=512 * 1024**2,
    backup_guard_max_age=86400,
):
    """Plan backup/release cleanup from fixed roots and protected receipts."""

    now = time.time() if now is None else now
    body = _managed_cleanup_snapshot(
        paths,
        categories,
        budget,
        now=now,
        max_deletions=max_deletions,
        large_cleanup_bytes=large_cleanup_bytes,
        backup_guard_max_age=backup_guard_max_age,
    )
    encoded = json.dumps(body, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return {**body, "plan_id": str(uuid.uuid5(uuid.NAMESPACE_URL, encoded))}


def preview_system_cleanup_plan(
    paths,
    categories,
    budget,
    *,
    max_deletions=MAX_STORAGE_DELETIONS,
    now=None,
):
    """Bound the exact public targets for a separately authorized cleanup."""

    now = time.time() if now is None else now
    requested = tuple(categories)
    allowed = set(STORAGE_CATEGORIES) | set(MANAGED_STORAGE_CATEGORIES)
    unknown = sorted(set(requested) - allowed)
    duplicate = len(requested) != len(set(requested))
    ephemeral = {
        "diagnostics": paths.var / "diagnostics",
        "logs": paths.root / "var/log/robopark",
    }
    ephemeral = {key: value for key, value in ephemeral.items() if key in requested}
    regular = preview_cleanup_plan(
        ephemeral,
        budget,
        max_deletions=max_deletions,
        now=now,
    )
    managed_budget = StorageBudget(
        budget.partition_bytes,
        budget.free_bytes + sum(item["bytes"] for item in regular["planned"]),
        budget.minimum_free_bytes,
        budget.minimum_free_ratio,
    )
    remaining_slots = max(0, max_deletions - len(regular["planned"]))
    managed_categories = [
        category for category in requested if category in MANAGED_STORAGE_CATEGORIES
    ]
    managed = (
        preview_host_cleanup_plan(
            paths,
            managed_categories,
            managed_budget,
            max_deletions=remaining_slots,
            now=now,
        )
        if managed_categories
        else {
            "planned": [],
            "blocked": False,
            "guard": None,
            "unknown_categories": [],
        }
    )
    priority = {
        "diagnostics": 0,
        "logs": 1,
        "ota_cache": 2,
        "backups": 3,
        "releases": 4,
    }
    planned = sorted(
        [*regular["planned"], *managed["planned"]],
        key=lambda item: (priority[item["category"]], item["path"]),
    )[: max(0, max_deletions)]
    truncated = len(regular["planned"]) + len(managed["planned"]) > len(planned)
    body = {
        "schema": 1,
        "bounded": len(planned) <= max(0, max_deletions),
        "floor_bytes": budget.floor_bytes,
        "bytes_to_reclaim": budget.bytes_to_reclaim,
        "planned": planned,
        "identities": regular["identities"],
        "blocked": bool(
            unknown or duplicate or not requested or regular["blocked"] or managed["blocked"]
            or truncated
        ),
        "unknown_categories": unknown,
        "guard": managed.get("guard"),
    }
    encoded = json.dumps(body, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return {**body, "plan_id": str(uuid.uuid5(uuid.NAMESPACE_URL, encoded))}


def execute_system_cleanup_plan(
    paths,
    categories,
    budget,
    plan,
    *,
    max_deletions=MAX_STORAGE_DELETIONS,
    now=None,
):
    """Delete only the unchanged, fully enumerated system preview targets."""

    now = time.time() if now is None else now
    expected = preview_system_cleanup_plan(
        paths, categories, budget, max_deletions=max_deletions, now=now
    )
    if plan != expected or expected["blocked"]:
        raise ValueError("cleanup_plan_changed")
    ephemeral = {
        "diagnostics": paths.var / "diagnostics",
        "logs": paths.root / "var/log/robopark",
    }
    roots = {key: path for key, path in ephemeral.items() if key in categories}
    regular = preview_cleanup_plan(roots, budget, max_deletions=max_deletions, now=now)
    managed_budget = StorageBudget(
        budget.partition_bytes,
        budget.free_bytes + sum(item["bytes"] for item in regular["planned"]),
        budget.minimum_free_bytes,
        budget.minimum_free_ratio,
    )
    remaining_slots = max(0, max_deletions - len(regular["planned"]))
    managed_categories = [key for key in categories if key in MANAGED_STORAGE_CATEGORIES]
    managed = (
        preview_host_cleanup_plan(
            paths,
            managed_categories,
            managed_budget,
            max_deletions=remaining_slots,
            now=now,
        )
        if managed_categories else None
    )
    deleted = []
    try:
        if regular["planned"]:
            deleted.extend(
                execute_cleanup_plan(
                    roots, budget, regular, max_deletions=max_deletions, now=now
                )["deleted"]
            )
        if managed is not None and managed["planned"]:
            deleted.extend(
                execute_host_cleanup_plan(
                    paths, managed_categories, managed_budget, managed,
                    max_deletions=remaining_slots, now=now,
                )["deleted"]
            )
    except CleanupPartialError as exc:
        raise CleanupPartialError([*deleted, *exc.deleted], exc.uncertain_target) from exc
    except (OSError, ValueError) as exc:
        if deleted:
            raise CleanupPartialError(deleted) from exc
        raise
    return {**expected, "deleted": deleted, "deleted_count": len(deleted)}


def execute_host_cleanup_plan(
    paths,
    categories,
    budget,
    plan,
    *,
    max_deletions=MAX_STORAGE_DELETIONS,
    now=None,
    large_cleanup_bytes=512 * 1024**2,
    backup_guard_max_age=86400,
):
    """Revalidate the immutable managed plan and delete only its direct children."""

    expected = preview_host_cleanup_plan(
        paths,
        categories,
        budget,
        max_deletions=max_deletions,
        now=now,
        large_cleanup_bytes=large_cleanup_bytes,
        backup_guard_max_age=backup_guard_max_age,
    )
    if plan != expected or expected["blocked"]:
        raise ValueError("cleanup_plan_changed")
    deleted = []
    for item in expected["planned"]:
        try:
            if item["category"] == "backups":
                target = paths.var / "backups" / item["path"]
                _, digest = _regular_sha256(target)
                if digest != item["fingerprint"]:
                    raise ValueError("cleanup_plan_changed")
                target.unlink()
            elif item["category"] == "ota_cache":
                from .ota_store import OtaPackageStore
                from .rollback import sync_directory

                target = OtaPackageStore(paths).packages / item["path"]
                _, fingerprint = _ota_cache_identity(target)
                if fingerprint != item["fingerprint"]:
                    raise ValueError("cleanup_plan_changed")
                target.unlink()
                sync_directory(target.parent)
            else:
                from .restore_retention import protected_release_names

                if item["path"] in protected_release_names(paths):
                    raise ValueError("cleanup_plan_changed")
                target = paths.releases / item["path"]
                if target.is_symlink() or not target.is_dir():
                    raise ValueError("cleanup_plan_changed")
                if not getattr(shutil.rmtree, "avoids_symlink_attacks", False):
                    raise ValueError("cleanup_plan_changed")
                shutil.rmtree(target)
                (paths.state / "successful-releases" / f"{item['path']}.json").unlink()
            deleted.append({key: item[key] for key in ("category", "path", "bytes")})
        except (OSError, ValueError) as exc:
            if deleted or item["category"] == "releases":
                raise CleanupPartialError(deleted, {
                    key: item[key] for key in ("category", "path", "bytes")
                }) from exc
            raise
    return {**expected, "deleted": deleted, "deleted_count": len(deleted)}


def retain_storage(
    paths, *, dry_run: bool = False, max_deletions: int = MAX_STORAGE_DELETIONS
):
    """Scheduled cleanup of explicit ephemeral roots under the stable host lock."""
    from .state import HostBusy, host_operation

    roots = {
        "diagnostics": paths.var / "diagnostics",
        "logs": paths.root / "var/log/robopark",
    }
    try:
        paths.state.mkdir(parents=True, exist_ok=True)
        paths.lock_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        with host_operation(paths, check_space=False):
            report = cleanup_storage_roots(
                roots,
                StorageBudget.for_path(paths.var),
                dry_run=dry_run,
                max_deletions=max_deletions,
            )
    except HostBusy:
        report = {
            "dry_run": dry_run,
            "bounded": True,
            "blocked": True,
            "busy": True,
            "pressure": False,
            "deleted": [],
            "deleted_count": 0,
            "planned": [],
            "completed_at": time.time(),
        }
    if not dry_run and not paths.state.is_symlink():
        from .storage_inventory import collect_storage_inventory

        try:
            inventory = collect_storage_inventory(paths)
        except (OSError, ValueError):
            inventory = None
        category_bytes = dict(report.get("category_bytes") or {})
        if inventory is not None:
            category_bytes.update(inventory["category_bytes"])
        atomic_write_json(paths.state / "storage-retention.json", report)
        from .health_projection import update_public_health

        update_public_health(
            paths.var / "api-ops/host-health.json",
            storage={
                key: category_bytes if key == "category_bytes" else report.get(key)
                for key in (
                    "floor_bytes",
                    "bytes_to_reclaim",
                    "reclaimed_bytes",
                    "pressure",
                    "blocked",
                    "category_bytes",
                    "pressure_category",
                    "completed_at",
                )
            }
            | ({"busy": True} if report.get("busy") is True else {})
            | ({"inventory_sampled_at": inventory["sampled_at"]} if inventory is not None else {"inventory_failed": True}),
        )
    return report


class RetentionBlocked(ValueError):
    pass


@contextmanager
def _directory(paths, path):
    """Open every directory component without following replaceable symlinks."""
    descriptors = []
    try:
        descriptor = os.open(paths.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(descriptor)
        for part in path.relative_to(paths.root).parts:
            descriptor = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            descriptors.append(descriptor)
        yield descriptor
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


@contextmanager
def _lock(directory, name):
    descriptor = os.open(
        name,
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600,
        dir_fd=directory,
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid not in {0, 10001, os.geteuid()}
        ):
            raise RetentionBlocked("unsafe_lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # First scheduled cleanup may precede the first API operation. Its lock
        # must remain writable by the API directory owner, never root-only.
        directory_owner = os.fstat(directory)
        if (
            name == "begin.lock"
            and info.st_uid != directory_owner.st_uid
            and os.geteuid() == 0
        ):
            os.fchown(descriptor, directory_owner.st_uid, directory_owner.st_gid)
        yield
    finally:
        os.close(descriptor)


def _locations(paths):
    return [
        (
            paths.ops / "artifacts",
            rf"(?:(?:update|restore)-{UUID}\.zip|\.bridge-[a-z0-9_]{{8}})",
            "upload",
        ),
        (
            paths.var / "api-ops/inspections",
            rf"(?:{UUID}\.json|\.bridge-[a-z0-9_]{{8}})",
            "inspection",
        ),
        (paths.ops / "public/artifacts", rf"{UUID}\.zip", "diagnostic"),
        (paths.state / "command-receipts", rf"{UUID}\.json", "receipt"),
    ]


def _entries(paths, stack):
    entries = []
    for directory, pattern, kind in _locations(paths):
        try:
            fd = stack.enter_context(_directory(paths, directory))
        except FileNotFoundError:
            continue
        # scandir is streamed: foreign entries cannot force an unbounded list.
        with os.scandir(fd) as listing:
            for entry in listing:
                if len(entries) >= MAX_ENTRIES:
                    raise RetentionBlocked("too_many_artifacts")
                info = entry.stat(follow_symlinks=False)
                owned = (
                    bool(re.fullmatch(pattern, entry.name))
                    and stat.S_ISREG(info.st_mode)
                    and info.st_nlink == 1
                )
                owned = owned and info.st_uid in (
                    {0, 10001, os.geteuid()}
                    if kind in {"upload", "inspection"}
                    else {0, os.geteuid()}
                )
                entries.append((fd, entry.name, kind, info, owned))
    return entries


def artifact_usage(paths):
    try:
        with ExitStack() as stack:
            entries = _entries(paths, stack)
            from .restore_retention import entries as restore_entries

            restores = restore_entries(paths, stack)
            total = sum(item[3].st_size for item in entries) + sum(
                item["bytes"] for item in restores
            )
            return {
                "bytes": total,
                "files": len(entries) + sum(item["files"] for item in restores),
                "pressure": total >= MAX_BYTES,
                "blocked": False,
            }
    except (OSError, ValueError):
        return {"bytes": 0, "files": 0, "pressure": True, "blocked": True}


def _bloom(paths):
    target = paths.state / "retired-commands.json"
    try:
        info = target.lstat()
    except FileNotFoundError:
        marker = paths.state / "retired-commands-required.json"
        if marker.exists() or marker.is_symlink():
            raise RetentionBlocked("missing_replay_store") from None
        return bytearray(BLOOM_BYTES)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        raise RetentionBlocked("unsafe_replay_store")
    value = read_object(target, BLOOM_BYTES * 2 + 128)
    if (
        set(value) != {"format", "bits"}
        or type(value.get("format")) is not int
        or value["format"] != 1
    ):
        raise RetentionBlocked("invalid_replay_store")
    bits = value.get("bits")
    if (
        not isinstance(bits, str)
        or len(bits) != BLOOM_BYTES * 2
        or re.fullmatch("[0-9a-f]+", bits) is None
    ):
        raise RetentionBlocked("invalid_replay_store")
    return bytearray.fromhex(bits)


def _positions(identity):
    digest = hashlib.sha256(identity.encode("ascii")).digest()
    return [
        int.from_bytes(digest[index : index + 4], "big") % (BLOOM_BYTES * 8)
        for index in range(0, 28, 4)
    ]


def command_retired(paths, identity):
    """Caller holds host.lock. Corruption/full stores reject admission, never reset."""
    try:
        bits = _bloom(paths)
        if sum(byte.bit_count() for byte in bits) >= BLOOM_BYTES * 8 * MAX_FILL:
            return True
        return all(
            bits[position // 8] & (1 << (position % 8))
            for position in _positions(identity)
        )
    except (OSError, ValueError):
        return True


def _protected(paths):
    from .ota_update import OtaUpdateEngine, OtaUpdateRequest
    from .restore import _load

    names = set()
    identities = set()
    journal = _load(paths)
    if journal:
        identities.add(journal["request"]["job_id"])
        names.add(journal["request"]["artifact"])
    ota_journal_path = paths.state / "ota-update-journal.json"
    if ota_journal_path.is_symlink():
        raise RetentionBlocked("invalid_active_state")
    try:
        ota_journal = OtaUpdateEngine(paths, None)._read_journal()
    except RuntimeError as exc:
        raise RetentionBlocked("invalid_active_state") from exc
    if ota_journal and (
        ota_journal["phase"] not in {"published", "rolled_back", "failed"}
        or ota_journal["error"] == "ota_rollback_failed"
    ):
        request = OtaUpdateRequest.from_dict(ota_journal["request"])
        identities.add(str(request.operation_id))
    for path in (
        paths.ops / "inbox/approved.json",
        paths.state / "command-request.json",
        paths.state / "update-worker-request.json",
        paths.state / "updater-journal.json",
        paths.var / "api-ops/job.json",
        paths.ops / "public/command-result.json",
    ):
        raw = read_object(path)
        if path.exists() and not raw:
            raise RetentionBlocked("invalid_active_state")
        if (
            path.name in {"approved.json", "command-request.json"}
            and raw.get("kind") == "restore"
        ):
            from .commands import _validate

            _validate(raw, fresh=False)
        for key in ("artifact", "artifact_name"):
            if isinstance(raw.get(key), str):
                names.add(raw[key])
        for key in ("id", "job_id"):
            if isinstance(raw.get(key), str):
                identities.add(raw[key])
        extra = raw.get("extra")
        if isinstance(extra, dict):
            identity = extra.get("inspection_id")
            if isinstance(identity, str):
                names.update({identity + ".json", "update-" + identity + ".zip"})
            request = extra.get("host_request")
            if isinstance(request, dict):
                if isinstance(request.get("artifact"), str):
                    names.add(request["artifact"])
                if type(request.get("release_id")) is int:
                    names.add(f"github-release-{request['release_id']}.zip")
        release_id = raw.get("release_id")
        if type(release_id) is int:
            names.add(f"github-release-{release_id}.zip")
    return names, identities


def _receipt_identity(entry):
    fd, name, _, _, _ = entry
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
            raise RetentionBlocked("invalid_receipt")
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise RetentionBlocked("invalid_receipt")
    value = json.loads(raw, object_pairs_hook=unique_object)
    identity = name.removesuffix(".json")
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("request"), dict)
        or value["request"].get("job_id") != identity
        or not isinstance(value.get("result"), dict)
    ):
        raise RetentionBlocked("invalid_receipt")
    return identity


def _remove(entry):
    fd, name, _kind, before, _owned = entry
    now = os.stat(name, dir_fd=fd, follow_symlinks=False)
    if (now.st_dev, now.st_ino, now.st_size, now.st_mtime_ns) != (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ):
        raise RetentionBlocked("artifact_changed")
    os.unlink(name, dir_fd=fd)
    os.fsync(fd)


def _cleanup_staging(paths, identities, *, now):
    """Remove only expired updater work directories with known names."""
    try:
        with _directory(paths, paths.ops / "staging") as parent:
            deleted = 0
            seen = 0
            with os.scandir(parent) as listing:
                for entry in listing:
                    seen += 1
                    if seen > MAX_ENTRIES:
                        raise RetentionBlocked("too_many_artifacts")
                    match = re.fullmatch(
                        rf"(?:{UUID}|local-updater-({UUID}))", entry.name
                    )
                    identity = (
                        match.group(1) if match and match.group(1) else entry.name
                    )
                    info = entry.stat(follow_symlinks=False)
                    if (
                        match is None
                        or identity in identities
                        or not stat.S_ISDIR(info.st_mode)
                        or info.st_uid not in {0, os.geteuid()}
                        or now - info.st_mtime < STAGING_TTL
                    ):
                        continue
                    shutil.rmtree(entry.name, dir_fd=parent)
                    deleted += 1
            return deleted
    except FileNotFoundError:
        return 0


def _cleanup_operation_residue(paths, identities, *, now):
    """Reclaim only expired, exact-name technical residue under host-owned paths."""
    result = {"staging_deleted": _cleanup_staging(paths, identities, now=now)}
    locations = (
        (
            paths.state / "successful-releases",
            r"\.[A-Za-z0-9._+-]+\.json\.[a-z0-9_]{8}",
            "atomic_deleted",
        ),
        (
            paths.state / "image-owned",
            rf"\.(?:{UUID}|release-[a-f0-9]{{64}})\.json\.[a-z0-9_]{{8}}",
            "atomic_deleted",
        ),
        (
            paths.root / "var/log/robopark",
            r"\.doctor-[a-z0-9_]{8}",
            "diagnostic_deleted",
        ),
    )
    result.update(atomic_deleted=0, diagnostic_deleted=0)
    seen = 0
    for directory, pattern, category in locations:
        try:
            with _directory(paths, directory) as parent, os.scandir(parent) as listing:
                for entry in listing:
                    seen += 1
                    if seen > MAX_ENTRIES:
                        raise RetentionBlocked("too_many_artifacts")
                    info = entry.stat(follow_symlinks=False)
                    if (
                        re.fullmatch(pattern, entry.name) is None
                        or any(identity in entry.name for identity in identities)
                        or not stat.S_ISREG(info.st_mode)
                        or info.st_nlink != 1
                        or info.st_uid not in {0, os.geteuid()}
                        or now - info.st_mtime < STAGING_TTL
                    ):
                        continue
                    _remove((parent, entry.name, "temporary", info, True))
                    result[category] += 1
        except FileNotFoundError:
            continue
    result["temporary_deleted"] = sum(result.values())
    return result


def retain_artifacts(paths, *, now=None, max_bytes=MAX_BYTES):
    """Nonblocking scheduled cleanup; never runs inside an existing host operation."""
    result = {
        "deleted": 0,
        "staging_deleted": 0,
        "atomic_deleted": 0,
        "diagnostic_deleted": 0,
        "ota_uploads_deleted": 0,
        "ota_temporary_deleted": 0,
        "temporary_deleted": 0,
        "bytes": 0,
        "pressure": False,
        "blocked": False,
        "reclaimed_bytes": 0,
        "kept": 0,
        "removed": 0,
        "errors": [],
    }
    if paths.root.as_posix() == "/" and os.geteuid() != 0:
        return {**result, "blocked": True}
    now = time.time() if now is None else now
    try:
        paths.ops.mkdir(parents=True, exist_ok=True)
        paths.lock_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        with ExitStack() as stack:
            lock_dir = stack.enter_context(_directory(paths, paths.lock_dir))
            stack.enter_context(_lock(lock_dir, "host.lock"))
            try:
                api = stack.enter_context(_directory(paths, paths.var / "api-ops"))
            except FileNotFoundError:
                api = None
            if api is not None:
                stack.enter_context(_lock(api, "begin.lock"))
            for marker in (
                paths.state / "maintenance.json",
                paths.ops / "public/maintenance.json",
            ):
                if marker.exists() and read_object(marker).get("enabled") is not False:
                    raise RetentionBlocked("maintenance")
            names, identities = _protected(paths)
            result.update(_cleanup_operation_residue(paths, identities, now=now))
            from .ota_store import OtaPackageStore

            result["ota_uploads_deleted"] = len(
                OtaPackageStore(paths).cleanup_expired(now=now, ttl_seconds=STAGING_TTL)
            )
            result["ota_temporary_deleted"] = len(
                OtaPackageStore(paths).cleanup_abandoned_copies(
                    now=now, ttl_seconds=STAGING_TTL
                )
            )
            result["ota_packages_deleted"] = len(
                OtaPackageStore(paths).cleanup_terminal_packages()
            )
            bits = _bloom(paths)
            fill = sum(byte.bit_count() for byte in bits)
            if fill >= BLOOM_BYTES * 8 * MAX_FILL:
                raise RetentionBlocked("replay_store_full")
            entries = sorted(_entries(paths, stack), key=lambda item: item[3].st_mtime)
            total = sum(item[3].st_size for item in entries)
            from .restore_retention import entries as restore_entries
            from .restore_retention import prune

            restores = restore_entries(paths, stack)
            restore_bytes, restored_deleted = prune(
                restores, identities, now=now, max_bytes=max_bytes - total
            )
            atomic_write_json(
                paths.state / "restore-retention.json",
                {
                    "blocked": False,
                    "bytes": restore_bytes,
                    "deleted": restored_deleted,
                    "pressure": restore_bytes > max_bytes,
                },
            )
            result["deleted"] += restored_deleted
            total += restore_bytes
            receipt_total = sum(
                item[3].st_size for item in entries if item[2] == "receipt"
            )
            removals = []
            receipts = []
            for entry in entries:
                _fd, name, kind, info, owned = entry
                protected = (
                    name in names
                    or name.removesuffix(".json").removesuffix(".zip") in identities
                )
                if kind == "github":
                    protected = protected or name.split(".zip")[0] + ".zip" in names
                age = now - info.st_mtime
                if (
                    not owned
                    or protected
                    or kind in {"upload", "inspection"}
                    and age < INSPECTION_TTL
                ):
                    continue
                retention_age = (
                    INSPECTION_TTL if kind in {"upload", "inspection"} else MAX_AGE
                )
                if (
                    age < retention_age
                    and total <= max_bytes
                    and not (kind == "receipt" and receipt_total > RECEIPT_BYTES)
                ):
                    continue
                if kind == "receipt":
                    positions = _positions(_receipt_identity(entry))
                    additions = sum(
                        not bits[p // 8] & (1 << (p % 8)) for p in set(positions)
                    )
                    if fill + additions >= BLOOM_BYTES * 8 * MAX_FILL:
                        raise RetentionBlocked("replay_store_full")
                    for position in positions:
                        bits[position // 8] |= 1 << (position % 8)
                    fill += additions
                    receipts.append(entry)
                    receipt_total -= info.st_size
                else:
                    removals.append(entry)
                total -= info.st_size
            # Commit all replay identities BEFORE the first full receipt deletion.
            if receipts:
                atomic_write_json(
                    paths.state / "retired-commands-required.json", {"format": 1}
                )
                atomic_write_json(
                    paths.state / "retired-commands.json",
                    {"format": 1, "bits": bits.hex()},
                )
            for entry in removals + receipts:
                result["reclaimed_bytes"] += entry[3].st_size
                _remove(entry)
                result["deleted"] += 1
            result.update(
                bytes=total,
                pressure=total > max_bytes,
                kept=len(entries) - len(removals) - len(receipts),
                removed=len(removals) + len(receipts),
            )
    except BlockingIOError:
        result.update(blocked=True, busy=True)
    except (OSError, ValueError, RecursionError) as exc:
        if isinstance(exc, RetentionBlocked) and str(exc) == "maintenance":
            result.update(blocked=True, busy=True)
        else:
            result.update(blocked=True, pressure=True)
    result["errors"] = ["cleanup_blocked"] if result["blocked"] else []
    result["completed_at"] = datetime.now(timezone.utc).isoformat()
    # Bounded root-owned outcome lets doctor report blocked/saturated cleanup.
    if not paths.state.is_symlink():
        atomic_write_json(paths.state / "retention.json", result)
    public = paths.ops / "public"
    if not public.is_symlink():
        atomic_write_json(
            public / "retention-status.json",
            {
                key: result[key]
                for key in (
                    "kept",
                    "removed",
                    "reclaimed_bytes",
                    "completed_at",
                    "errors",
                )
            },
            mode=0o644,
        )
    return result


def require_capacity(paths, incoming):
    """Caller holds host.lock; fail before allocating another archive on disk."""
    import shutil

    usage = artifact_usage(paths)
    if (
        usage["blocked"]
        or usage["bytes"] + incoming > MAX_BYTES
        or shutil.disk_usage(paths.var).free < incoming + 2 * 1024**3
    ):
        raise RetentionBlocked("artifact_storage_full")
