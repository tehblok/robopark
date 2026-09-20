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
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
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
STORAGE_PRIORITY = {name: index for index, name in enumerate(STORAGE_CATEGORIES)}
STORAGE_TTL = {
    "diagnostics": 7 * 86400,
    "logs": 14 * 86400,
}
STORAGE_MAX_BYTES = {"logs": 256 * 1024**2}


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
                        category_totals.get(category, 0) + info.st_size
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
        item = {"category": category, "path": name, "bytes": before.st_size}
        planned.append(item)
        if dry_run:
            reclaimed += before.st_size
            category_totals[category] -= before.st_size
            continue
        try:
            current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if not stat.S_ISREG(current.st_mode) or (
                current.st_dev,
                current.st_ino,
                current.st_size,
                current.st_mtime_ns,
            ) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
                raise RetentionBlocked("artifact_changed")
            os.unlink(name, dir_fd=descriptor)
            deleted.append(item)
            reclaimed += before.st_size
            category_totals[category] -= before.st_size
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
        with host_operation(paths):
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
        atomic_write_json(paths.state / "storage-retention.json", report)
        from .health_projection import update_public_health

        update_public_health(
            paths.var / "api-ops/host-health.json",
            storage={
                key: report.get(key)
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
            },
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
        (
            paths.state / "github-artifacts",
            r"github-release-[1-9][0-9]{0,18}\.zip(?:\.sig|\.json|\.approval\.json|\.partial|\.sig\.partial)?",
            "github",
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
    from .restore import _load

    names = set()
    identities = set()
    journal = _load(paths)
    if journal:
        identities.add(journal["request"]["job_id"])
        names.add(journal["request"]["artifact"])
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
    fd, name, kind, before, owned = entry
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
            with _directory(paths, directory) as parent:
                with os.scandir(parent) as listing:
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
        "temporary_deleted": 0,
        "bytes": 0,
        "pressure": False,
        "blocked": False,
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
                fd, name, kind, info, owned = entry
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
                _remove(entry)
                result["deleted"] += 1
            result.update(bytes=total, pressure=total > max_bytes)
    except BlockingIOError:
        result.update(blocked=True, busy=True)
    except (OSError, ValueError, RecursionError) as exc:
        if isinstance(exc, RetentionBlocked) and str(exc) == "maintenance":
            result.update(blocked=True, busy=True)
        else:
            result.update(blocked=True, pressure=True)
    # Bounded root-owned outcome lets doctor report blocked/saturated cleanup.
    if not paths.state.is_symlink():
        atomic_write_json(paths.state / "retention.json", result)
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
