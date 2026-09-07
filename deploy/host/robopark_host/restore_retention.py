"""Root-recorded restore storage; active and latest recovery are never reclaimed.

Caller owns host.lock. Receipts precede journal transitions, and are deleted only
AFTER their trees. Interrupted cleanup therefore resumes without losing ownership.
"""

import json
import os
import re
import shutil
import stat
import time
from contextlib import ExitStack, suppress

from .state import atomic_write_json


def record(paths, journal):
    atomic_write_json(
        paths.state / "restore-owned" / (journal["request"]["job_id"] + ".json"),
        {
            "schema": 1,
            "job_id": journal["request"]["job_id"],
            "phase": journal["phase"],
            "updated_at": time.time(),
        },
    )


def _tree(fd, budget, depth=0):
    from .retention import RetentionBlocked

    if depth > 64:
        raise RetentionBlocked("restore_tree_limit")
    size = count = 0
    with os.scandir(fd) as listing:
        for entry in listing:
            budget[0] -= 1
            if budget[0] < 0:
                raise RetentionBlocked("restore_tree_limit")
            info = entry.stat(follow_symlinks=False)
            if info.st_uid not in {0, 10001, os.geteuid()}:
                raise RetentionBlocked("unsafe_restore_tree")
            if stat.S_ISDIR(info.st_mode):
                child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    subtotal, files = _tree(child, budget, depth + 1)
                finally:
                    os.close(child)
                size += subtotal
                count += files
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                size += info.st_size
                count += 1
            else:
                raise RetentionBlocked("unsafe_restore_tree")
    return size, count


def entries(paths, stack):
    from .release import unique_object
    from .restore import PHASES, _load
    from .retention import MAX_ENTRIES, UUID, RetentionBlocked, _directory

    # Malformed journals must never authorize cleanup, including between reboot
    # recovery and the first service start.
    _load(paths)
    try:
        receipts = stack.enter_context(_directory(paths, paths.state / "restore-owned"))
    except FileNotFoundError:
        return []
    result = []
    budget = [MAX_ENTRIES]
    with os.scandir(receipts) as listing:
        for entry in listing:
            budget[0] -= 1
            if budget[0] < 0:
                raise RetentionBlocked("restore_tree_limit")
            if re.fullmatch(r"\." + UUID + r"\.json\.[A-Za-z0-9_]+", entry.name):
                continue  # interrupted atomic write: never ownership authority
            if not re.fullmatch(UUID + r"\.json", entry.name):
                raise RetentionBlocked("unknown_restore_receipt")
            fd = os.open(entry.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=receipts)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_nlink != 1
                    or info.st_size > 4096
                ):
                    raise RetentionBlocked("unsafe_restore_receipt")
                value = json.loads(stream.read(4097), object_pairs_hook=unique_object)
            identity = entry.name[:-5]
            if (
                not isinstance(value, dict)
                or set(value) != {"schema", "job_id", "phase", "updated_at"}
                or type(value["schema"]) is not int
                or value["schema"] != 1
                or value["job_id"] != identity
                or not isinstance(value["phase"], str)
                or value["phase"] not in PHASES
                or type(value["updated_at"]) not in (int, float)
                or not 0 <= value["updated_at"] <= time.time() + 300
            ):
                raise RetentionBlocked("invalid_restore_receipt")
            trees = []
            size = info.st_size
            files = 1
            for target in (
                paths.state / "restores" / identity,
                paths.var / (".manual-restore-" + identity),
                paths.var / (".manual-displaced-" + identity),
            ):
                try:
                    parent = stack.enter_context(_directory(paths, target.parent))
                    tree = stack.enter_context(_directory(paths, target))
                except FileNotFoundError:
                    continue
                subtotal, count = _tree(tree, budget)
                trees.append((parent, target.name, os.fstat(tree)))
                size += subtotal
                files += count
            result.append(
                {
                    "id": identity,
                    "value": value,
                    "receipt": (receipts, entry.name, info),
                    "trees": trees,
                    "bytes": size,
                    "files": files,
                }
            )
    return result


def prune(items, protected, *, now, max_bytes):
    from .restore import TERMINAL
    from .retention import MAX_AGE, RetentionBlocked, _remove

    total = sum(item["bytes"] for item in items)
    deleted = 0
    for item in sorted(items, key=lambda item: item["value"]["updated_at"]):
        if item["id"] in protected or item["value"]["phase"] not in TERMINAL:
            continue
        if now - item["value"]["updated_at"] < MAX_AGE and total <= max_bytes:
            continue
        if not shutil.rmtree.avoids_symlink_attacks:
            raise RetentionBlocked("safe_tree_cleanup_unavailable")
        for parent, name, before in item["trees"]:
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
                raise RetentionBlocked("restore_tree_changed")
            shutil.rmtree(name, dir_fd=parent)
            os.fsync(parent)
        parent, name, info = item["receipt"]
        _remove((parent, name, "restore", info, True))
        total -= item["bytes"]
        deleted += 1
    return total, deleted


def cleanup_terminal(paths):
    """Best-effort housekeeping cannot turn accepted writes into data rollback."""
    from .retention import MAX_BYTES, _protected

    try:
        with ExitStack() as stack:
            items = entries(paths, stack)
            _, protected = _protected(paths)
            total, deleted = prune(items, protected, now=time.time(), max_bytes=MAX_BYTES)
        result = {
            "blocked": False,
            "pressure": total > MAX_BYTES,
            "bytes": total,
            "deleted": deleted,
        }
    except (OSError, ValueError, RecursionError):
        result = {"blocked": True, "pressure": True}
    # The scheduled doctor retries; housekeeping never rolls back resumed writes.
    with suppress(OSError):
        atomic_write_json(paths.state / "restore-retention.json", result)
