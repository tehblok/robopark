"""Unified bounded retention policy for API-owned ephemeral storage."""

from __future__ import annotations

import heapq
import os
import shutil
import stat
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

GIB = 1024**3
ALLOWED_CATEGORIES = ("confirmed_tracker",)


@contextmanager
def pinned_directory(path: Path):
    """Pin an owned directory through no-follow openat traversal."""
    descriptors: list[int] = []
    try:
        descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        descriptors.append(descriptor)
        for part in path.absolute().parts[1:]:
            descriptor = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            descriptors.append(descriptor)
        yield descriptor
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def unlink_unchanged(descriptor: int, name: str, before: os.stat_result) -> None:
    current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_nlink != 1
        or (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
        != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    ):
        raise OSError("changed")
    os.unlink(name, dir_fd=descriptor)


@dataclass(frozen=True, slots=True)
class StorageBudget:
    partition_bytes: int
    free_bytes: int
    minimum_free_bytes: int = 6 * GIB
    minimum_free_ratio: float = 0.15

    @property
    def floor_bytes(self) -> int:
        return max(self.minimum_free_bytes, int(self.partition_bytes * self.minimum_free_ratio))

    @property
    def bytes_to_reclaim(self) -> int:
        return max(0, self.floor_bytes - self.free_bytes)

    @classmethod
    def for_path(cls, path: Path) -> StorageBudget:
        usage = shutil.disk_usage(path)
        return cls(partition_bytes=usage.total, free_bytes=usage.free)


def cleanup_storage(
    *,
    roots: dict[str, Path],
    budget: StorageBudget,
    dry_run: bool = True,
    max_deletions: int = 128,
    eligible_names: dict[str, set[str]] | None = None,
) -> dict:
    """Clean only API-owned duplicates; callers cannot add primary-data roots."""
    unknown = sorted(set(roots) - set(ALLOWED_CATEGORIES))
    blocked = bool(unknown)
    candidates = []
    skipped_counts: dict[str, int] = {}
    opened: list[int] = []
    seen_eligible: dict[str, set[str]] = {}

    def skip(reason: str) -> None:
        skipped_counts[reason] = skipped_counts.get(reason, 0) + 1

    for category in ALLOWED_CATEGORIES:
        root = roots.get(category)
        if root is None:
            continue
        try:
            manager = pinned_directory(root)
            descriptor = manager.__enter__()
            opened.append((manager, descriptor))
            for entry in os.scandir(descriptor):
                if eligible_names is not None and entry.name not in eligible_names.get(
                    category, set()
                ):
                    skip("protected")
                    continue
                seen_eligible.setdefault(category, set()).add(entry.name)
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    item = (-info.st_mtime_ns, entry.name, category, info, descriptor)
                    if len(candidates) < max(0, max_deletions):
                        heapq.heappush(candidates, item)
                    elif candidates and item > candidates[0]:
                        heapq.heapreplace(candidates, item)
                else:
                    skip("not_owned_file")
        except OSError:
            blocked = True
            skip("scan_failed")
    candidates.sort(key=lambda item: (-item[0], item[1]))
    reclaimed = 0
    planned = []
    deleted = []
    for _, name, category, before, descriptor in candidates:
        if len(planned) >= max(0, max_deletions) or reclaimed >= budget.bytes_to_reclaim:
            break
        item = {"category": category, "path": name, "bytes": before.st_size}
        planned.append(item)
        if dry_run:
            reclaimed += before.st_size
            continue
        try:
            unlink_unchanged(descriptor, name, before)
            deleted.append(item)
            reclaimed += before.st_size
        except OSError:
            blocked = True
            skip("delete_failed")
            break
    for manager, _ in reversed(opened):
        manager.__exit__(None, None, None)
    eligible_missing = {
        category: sorted(names - seen_eligible.get(category, set()))
        for category, names in (eligible_names or {}).items()
    }
    return {
        "dry_run": dry_run,
        "floor_bytes": budget.floor_bytes,
        "bytes_to_reclaim": budget.bytes_to_reclaim,
        "reclaimed_bytes": reclaimed,
        "planned": planned,
        "deleted": deleted,
        "deleted_count": len(deleted),
        "pressure": budget.free_bytes + reclaimed < budget.floor_bytes,
        "blocked": blocked,
        "bounded": len(planned) <= max(0, max_deletions),
        "unknown_categories": unknown,
        "skipped_counts": skipped_counts,
        "eligible_missing": eligible_missing,
    }


class MemoryPressureController:
    """Evict once after sustained pressure; persistent pressure is surfaced."""

    def __init__(
        self,
        *,
        required_samples: int = 3,
        threshold: float = 0.9,
        evict: Callable[[], object],
    ) -> None:
        self.required_samples = max(1, required_samples)
        self.threshold = threshold
        self.evict = evict
        self._consecutive = 0
        self._evicted = False

    def observe(self, utilization: float) -> dict[str, bool]:
        if utilization < self.threshold:
            self._consecutive = 0
            self._evicted = False
            return {"sustained": False, "evicted": False, "failed": False}
        self._consecutive += 1
        sustained = self._consecutive >= self.required_samples
        if not sustained:
            return {"sustained": False, "evicted": False, "failed": False}
        if not self._evicted:
            self._evicted = True
            try:
                self.evict()
            except Exception:  # noqa: BLE001 - arbitrary cache backends must fail closed.
                return {"sustained": True, "evicted": False, "failed": True}
            return {"sustained": True, "evicted": True, "failed": False}
        return {"sustained": True, "evicted": False, "failed": True}


class CleanupRetry:
    """Bounded exponential retry and error-log throttling for scheduled cleanup."""

    def __init__(self, *, minimum: float = 30.0, maximum: float = 900.0) -> None:
        self.minimum = minimum
        self.maximum = max(minimum, maximum)
        self._delay = 0.0
        self._last_log = float("-inf")

    def failed(self) -> float:
        self._delay = min(self.maximum, max(self.minimum, self._delay * 2))
        return self._delay

    def succeeded(self) -> None:
        self._delay = 0.0

    def should_log(self, now: float) -> bool:
        if now - self._last_log < self.maximum:
            return False
        self._last_log = now
        return True
