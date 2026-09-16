"""Small cross-worker change counters, without response bodies or user data."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from robopark_api.services.live_merge import default_live_merge_root

_SCOPE = re.compile(r"[a-z][a-z0-9:_-]{0,63}\Z")


class ChangeRevisionStore:
    def __init__(self, root: Path) -> None:
        self.root = root / "change-revisions"

    def _path(self, scope: str) -> Path:
        if not _SCOPE.fullmatch(scope):
            raise ValueError("invalid change scope")
        digest = hashlib.sha256(scope.encode()).hexdigest()
        return self.root / f"{digest}.json"

    def current(self, scope: str) -> int:
        path = self._path(scope)
        try:
            value = json.loads(path.read_text())
            revision = value.get("revision")
            return revision if type(revision) is int and revision >= 0 else 0
        except (FileNotFoundError, OSError, ValueError, TypeError):
            return 0

    def mark_changed(self, scope: str) -> int:
        path = self._path(scope)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path = path.with_suffix(".lock")
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            revision = self.current(scope) + 1
            fd, temporary = tempfile.mkstemp(prefix="change-", suffix=".tmp", dir=self.root)
            try:
                with os.fdopen(fd, "w") as stream:
                    json.dump({"revision": revision}, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return revision
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)


def default_change_revision_store() -> ChangeRevisionStore:
    return ChangeRevisionStore(default_live_merge_root())


def scope_for_mutation(path: str) -> str | None:
    if path.startswith("/tracker/issues/") and path.rstrip("/").endswith("/presence"):
        return None
    if path.startswith("/tracker/") or path.startswith("/mechanic/tasks/"):
        return "work"
    if path.startswith("/inventory/parks/"):
        part = path.removeprefix("/inventory/parks/").split("/", 1)[0]
        if part.isdecimal():
            return f"inventory:{int(part)}"
    if path.startswith("/inventory/catalog/") or path.startswith("/inventory/parts"):
        return "inventory:catalog"
    return None
