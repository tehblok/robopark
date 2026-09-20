"""Unified bounded retention policy for API-owned ephemeral storage."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

GIB = 1024**3


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


def cleanup_storage(
    *,
    roots: dict[str, Path],
    budget: StorageBudget,
    dry_run: bool = True,
    max_deletions: int = 128,
) -> dict:
    """Clean only API-owned duplicates; callers cannot add primary-data roots."""
    allowed = ("cache", "tmp", "thumbnails", "diagnostics", "logs", "confirmed_tracker")
    unknown = sorted(set(roots) - set(allowed))
    blocked = bool(unknown)
    candidates = []
    skipped = []
    for priority, category in enumerate(allowed):
        root = roots.get(category)
        if root is None:
            continue
        try:
            if (
                root.is_symlink()
                or not root.is_dir()
                or root.resolve(strict=True) != root.absolute()
            ):
                blocked = True
                skipped.append({"category": category, "reason": "unsafe_root"})
                continue
            for entry in os.scandir(root):
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    candidates.append(
                        (priority, info.st_mtime_ns, category, Path(entry.path), info)
                    )
                else:
                    skipped.append(
                        {"category": category, "path": entry.name, "reason": "not_owned_file"}
                    )
        except OSError:
            blocked = True
            skipped.append({"category": category, "reason": "scan_failed"})
    candidates.sort(key=lambda item: (item[0], item[1], item[3].name))
    reclaimed = 0
    planned = []
    deleted = []
    for _, _, category, path, before in candidates:
        if len(planned) >= max(0, max_deletions) or reclaimed >= budget.bytes_to_reclaim:
            break
        item = {"category": category, "path": path.name, "bytes": before.st_size}
        planned.append(item)
        if dry_run:
            reclaimed += before.st_size
            continue
        try:
            current = path.stat(follow_symlinks=False)
            if (
                not stat.S_ISREG(current.st_mode)
                or current.st_nlink != 1
                or (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
                != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            ):
                raise OSError("changed")
            path.unlink()
            deleted.append(item)
            reclaimed += before.st_size
        except OSError:
            blocked = True
            skipped.append({"category": category, "path": path.name, "reason": "delete_failed"})
            break
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
        "skipped": skipped,
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
