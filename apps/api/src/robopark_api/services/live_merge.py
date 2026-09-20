"""Cross-process live merge: short POSIX locks plus a result blob.

KTD1: the exclusive lock is never held during Startrek or Emergency HTTP.
The leader claims the key, writes an inflight marker, drops the lock, fetches,
then writes the result blob. Waiters poll; a dead leader's lock and inflight
drop so the next waiter can claim once.

Lock files live under ``<data>/live-merge/`` — not under ops ``begin.lock``.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import stat
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

from robopark_api.config import get_settings
from robopark_api.services.cache_metrics import family

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Match Tracker slot-wait class (``tracker_api.DEFAULT_SLOT_WAIT_SEC``).
DEFAULT_WAITER_TIMEOUT_SEC = 25.0
_POLL_SEC = 0.05
_LOCK_RETRY_SEC = 0.01
_MAX_SUPERSEDED_ATTEMPTS = 16
_NAMESPACE_GUARD_KEY = "__live_merge_namespace_guard__"
_DISABLED = object()
_MISSING = object()

LIVE_MERGE_DIRNAME = "live-merge"
LIVE_MERGE_SCHEMA = 1
DEFAULT_NAMESPACE_MAX_ENTRIES = 2048
DEFAULT_NAMESPACE_MAX_BYTES = 64 * 1024 * 1024
DEFAULT_METADATA_MAX_ENTRIES = 4096
DEFAULT_METADATA_MAX_BYTES = 8 * 1024 * 1024
MIN_ABANDONED_TMP_AGE_SECONDS = 3600.0

_store_cache: object = _DISABLED


class LiveMergeTimeout(TimeoutError):
    """A waiter waited the bound without a result or a claim."""


class LiveMergeUpstreamError(RuntimeError):
    """Shared failure recorded by the merge leader so waiters do not refetch."""


class _LiveMergeSuperseded(RuntimeError):
    """Internal signal that invalidation retired the leader's claim."""


def _sqlite_parent() -> Path | None:
    url = get_settings().database_url
    if not url.startswith("sqlite:///"):
        return None
    raw = url.removeprefix("sqlite:///")
    if raw == ":memory:":
        return None
    return Path(raw).resolve().parent


def default_live_merge_root() -> Path:
    settings = get_settings()
    if settings.live_merge_dir:
        return Path(settings.live_merge_dir).resolve()
    parent = _sqlite_parent()
    if parent is not None:
        return parent / LIVE_MERGE_DIRNAME
    return Path.cwd() / "data" / LIVE_MERGE_DIRNAME


def live_merge_enabled() -> bool:
    flag = os.environ.get("ROBOPARK_LIVE_MERGE", "1").strip().lower()
    return flag not in {"0", "false", "no", "off"}


def get_live_merge_store() -> LiveMergeStore | None:
    global _store_cache
    if _store_cache is _DISABLED:
        if not live_merge_enabled():
            _store_cache = None
        else:
            _store_cache = LiveMergeStore(default_live_merge_root())
    return _store_cache  # type: ignore[return-value]


def reset_live_merge_store() -> None:
    global _store_cache
    _store_cache = _DISABLED


def _digest(namespace: str, key: str) -> str:
    return hashlib.sha256(f"{namespace}\0{key}".encode()).hexdigest()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class LiveMergeStore:
    """Per-key flock coordinator plus JSON result blobs on the data volume."""

    def __init__(
        self,
        root: Path,
        *,
        waiter_timeout: float = DEFAULT_WAITER_TIMEOUT_SEC,
    ) -> None:
        if waiter_timeout <= 0:
            raise ValueError("waiter_timeout must be positive")
        self.root = Path(root)
        self.waiter_timeout = waiter_timeout

    def namespace_dir(self, namespace: str) -> Path:
        safe = namespace.replace("/", "_").replace("\0", "")
        return self.root / safe

    def _paths(self, namespace: str, key: str) -> tuple[Path, Path, Path, Path]:
        digest = _digest(namespace, key)
        folder = self.namespace_dir(namespace)
        return (
            folder / f"{digest}.lock",
            folder / f"{digest}.json",
            folder / f"{digest}.inflight",
            folder / f"{digest}.error",
        )

    def lock_path(self, namespace: str, key: str) -> Path:
        return self._paths(namespace, key)[0]

    def result_path(self, namespace: str, key: str) -> Path:
        return self._paths(namespace, key)[1]

    def inflight_path(self, namespace: str, key: str) -> Path:
        return self._paths(namespace, key)[2]

    def error_path(self, namespace: str, key: str) -> Path:
        return self._paths(namespace, key)[3]

    def result_mtime(self, namespace: str, key: str) -> float | None:
        path = self.result_path(namespace, key)
        try:
            return path.stat().st_mtime
        except FileNotFoundError:
            return None

    @contextmanager
    def _exclusive(self, namespace: str, key: str, *, timeout: float) -> Iterator[None]:
        from robopark_api.services.ops.maintenance import require_application_writes

        require_application_writes()
        lock_path, _, _, _ = self._paths(namespace, key)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + max(timeout, 0.0)
        while True:
            with lock_path.open("a+", encoding="utf-8") as fh:
                while True:
                    try:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise LiveMergeTimeout(key) from None
                        time.sleep(_LOCK_RETRY_SEC)
                try:
                    # Pruning may unlink an inode after this waiter opened it.
                    # Only the inode currently named by the path coordinates peers.
                    descriptor = os.fstat(fh.fileno())
                    try:
                        current = lock_path.stat()
                    except FileNotFoundError:
                        current = None
                    if current is not None and (descriptor.st_dev, descriptor.st_ino) == (
                        current.st_dev,
                        current.st_ino,
                    ):
                        require_application_writes()
                        yield
                        return
                finally:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            if time.monotonic() >= deadline:
                raise LiveMergeTimeout(key)

    def _atomic_write(self, path: Path, payload: dict[str, Any]) -> None:
        from robopark_api.services.ops.maintenance import require_application_writes

        require_application_writes()
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                fh.write(encoded)
                fh.flush()
                require_application_writes()
                Path(tmp).replace(path)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise

    def _read_json(self, path: Path) -> dict[str, Any] | None:
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def _read_fresh(self, namespace: str, key: str, ttl: float) -> object:
        path = self.result_path(namespace, key)
        data = self._read_json(path)
        if data is None or data.get("ok") is not True:
            return _MISSING
        if data.get("schema") != LIVE_MERGE_SCHEMA:
            return _MISSING
        written = data.get("written_at")
        if not isinstance(written, (int, float)):
            return _MISSING
        if time.time() - written >= ttl:
            return _MISSING
        if data.get("key") != key:
            return _MISSING
        return data.get("payload")

    def _read_inflight(self, namespace: str, key: str) -> dict[str, Any] | None:
        data = self._read_json(self.inflight_path(namespace, key))
        if data is None:
            return None
        pid = data.get("pid")
        started = data.get("started_at")
        if not isinstance(pid, int) or not isinstance(started, (int, float)):
            return None
        if not _pid_alive(pid):
            return None
        if time.time() - started > self.waiter_timeout * 2:
            return None
        return data

    def _write_inflight(self, namespace: str, key: str, claim: str | None = None) -> None:
        payload: dict[str, Any] = {"pid": os.getpid(), "started_at": time.time()}
        if claim is not None:
            payload["claim"] = claim
        self._atomic_write(
            self.inflight_path(namespace, key),
            payload,
        )

    def _clear_inflight(self, namespace: str, key: str, claim: str | None = None) -> None:
        path = self.inflight_path(namespace, key)
        if claim is not None:
            current = self._read_json(path)
            if current is None or current.get("claim") != claim:
                return
        path.unlink(missing_ok=True)

    def _claim_is_current(self, namespace: str, key: str, claim: str) -> bool:
        current = self._read_json(self.inflight_path(namespace, key))
        return current is not None and current.get("claim") == claim

    def _write_result(self, namespace: str, key: str, payload: Any) -> None:
        self._atomic_write(
            self.result_path(namespace, key),
            {
                "key": key,
                "schema": LIVE_MERGE_SCHEMA,
                "written_at": time.time(),
                "ok": True,
                "payload": payload,
            },
        )
        removed = self._prune_namespace_results(namespace)
        removed += self._prune_namespace_metadata(
            namespace, protected=self.lock_path(namespace, key)
        )
        metrics = family(f"shared.{namespace}")
        if removed:
            metrics.increment("evictions", removed)
        self._update_namespace_metrics(namespace)

    def _prune_namespace_results(
        self,
        namespace: str,
        *,
        max_entries: int = DEFAULT_NAMESPACE_MAX_ENTRIES,
        max_bytes: int = DEFAULT_NAMESPACE_MAX_BYTES,
    ) -> int:
        folder = self.namespace_dir(namespace)
        try:
            rows = sorted(
                ((path, path.stat()) for path in folder.glob("*.json") if path.is_file()),
                key=lambda row: row[1].st_mtime,
            )
        except OSError:
            return 0
        total = sum(stat.st_size for _, stat in rows)
        removed = 0
        while rows and (len(rows) > max_entries or total > max_bytes):
            path, stat = rows.pop(0)
            try:
                path.unlink()
            except OSError:
                continue
            total -= stat.st_size
            removed += 1
        return removed

    def _read_stale_payload(self, namespace: str, key: str, max_age: float) -> object:
        data = self._read_json(self.result_path(namespace, key))
        if data is None or data.get("ok") is not True or data.get("key") != key:
            return _MISSING
        if data.get("schema") != LIVE_MERGE_SCHEMA:
            return _MISSING
        written = data.get("written_at")
        if not isinstance(written, (int, float)) or time.time() - written >= max_age:
            return _MISSING
        return data.get("payload")

    def _read_error(self, namespace: str, key: str, ttl: float) -> dict[str, Any] | None:
        data = self._read_json(self.error_path(namespace, key))
        if data is None:
            return None
        written = data.get("written_at")
        if not isinstance(written, (int, float)):
            return None
        if time.time() - written >= ttl:
            return None
        return data

    def _write_error(
        self,
        namespace: str,
        key: str,
        exc: BaseException,
        *,
        stale_allowed: bool,
    ) -> None:
        self._atomic_write(
            self.error_path(namespace, key),
            {
                "key": key,
                "written_at": time.time(),
                "ok": False,
                "exc_msg": str(exc) or type(exc).__name__,
                "stale_allowed": stale_allowed,
            },
        )
        removed = self._prune_namespace_metadata(
            namespace, protected=self.lock_path(namespace, key)
        )
        metrics = family(f"shared.{namespace}")
        if removed:
            metrics.increment("evictions", removed)
        self._update_namespace_metrics(namespace)

    def _update_namespace_metrics(self, namespace: str) -> None:
        folder = self.namespace_dir(namespace)
        try:
            stats = [path.stat() for path in folder.iterdir() if path.is_file()]
        except OSError:
            return
        family(f"shared.{namespace}").gauge(
            entries=len(stats), bytes_=sum(stat.st_size for stat in stats)
        )

    def _prune_namespace_metadata(
        self,
        namespace: str,
        *,
        max_entries: int = DEFAULT_METADATA_MAX_ENTRIES,
        max_bytes: int = DEFAULT_METADATA_MAX_BYTES,
        protected: Path | None = None,
    ) -> int:
        folder = self.namespace_dir(namespace)
        try:
            rows = sorted(
                (
                    (path, path.stat())
                    for path in folder.iterdir()
                    if path.is_file() and path != protected and path.suffix in {".lock", ".error"}
                ),
                key=lambda row: row[1].st_mtime,
            )
        except OSError:
            return 0
        total = sum(stat.st_size for _, stat in rows)
        removed = 0
        while rows and (len(rows) > max_entries or total > max_bytes):
            path, stat = rows.pop(0)
            deleted = False
            if path.suffix == ".lock":
                deleted = self._prune_lock(
                    path, initial_stat=stat, now=time.time(), max_age_seconds=0
                )
            else:
                try:
                    path.unlink()
                    deleted = True
                except OSError:
                    pass
            if deleted:
                total -= stat.st_size
                removed += 1
        return removed

    def _clear_error(self, namespace: str, key: str) -> None:
        self.error_path(namespace, key).unlink(missing_ok=True)

    def _raise_shared_error(self, data: dict[str, Any]) -> None:
        raise LiveMergeUpstreamError(str(data.get("exc_msg") or "upstream failed"))

    def try_fresh(self, namespace: str, key: str, ttl: float) -> tuple[bool, Any]:
        """Best-effort blob read without taking the merge lock.

        Returns ``(True, payload)`` including a JSON ``null`` payload, or
        ``(False, None)`` on miss / stale / corrupt.
        """
        value = self._read_fresh(namespace, key, ttl)
        if value is _MISSING:
            return False, None
        return True, value

    def merge_load(
        self,
        namespace: str,
        key: str,
        ttl: float,
        loader: Callable[[], T],
        is_current: Callable[[], bool] | None = None,
        *,
        max_stale_seconds: float = 60.0,
        stale_if: Callable[[BaseException], bool] | None = None,
        on_stale: Callable[[], None] | None = None,
        shared_payload: Callable[[T], Any] | None = None,
    ) -> T:
        deadline = time.monotonic() + self.waiter_timeout
        for _attempt in range(_MAX_SUPERSEDED_ATTEMPTS):
            try:
                return self._merge_load_attempt(
                    namespace,
                    key,
                    ttl,
                    loader,
                    is_current,
                    deadline=deadline,
                    max_stale_seconds=max_stale_seconds,
                    stale_if=stale_if,
                    on_stale=on_stale,
                    shared_payload=shared_payload,
                )
            except _LiveMergeSuperseded:
                if time.monotonic() >= deadline:
                    break
        logger.warning("live-merge invalidation retry timed out for %s %s", namespace, key)
        raise LiveMergeTimeout(key)

    def _merge_load_attempt(
        self,
        namespace: str,
        key: str,
        ttl: float,
        loader: Callable[[], T],
        is_current: Callable[[], bool] | None,
        *,
        deadline: float,
        max_stale_seconds: float,
        stale_if: Callable[[BaseException], bool] | None,
        on_stale: Callable[[], None] | None,
        shared_payload: Callable[[T], Any] | None,
    ) -> T:
        from robopark_api.db import release_request_session

        # Both file-lock contention and another process's upstream flight can
        # wait. Return the DB connection before either, including cache hits
        # discovered after waiting; releasing only in the loader misses them.
        release_request_session()
        claim = uuid.uuid4().hex
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning("live-merge wait timed out for %s %s", namespace, key)
                raise LiveMergeTimeout(key)
            claimed = False
            with self._exclusive(namespace, key, timeout=remaining):
                cached = self._read_fresh(namespace, key, ttl)
                if cached is not _MISSING:
                    return cached  # type: ignore[return-value]
                err = self._read_error(namespace, key, ttl)
                if err is not None:
                    stale = self._read_stale_payload(namespace, key, max_stale_seconds)
                    if err.get("stale_allowed") is not False and stale is not _MISSING:
                        if on_stale is not None:
                            on_stale()
                        return stale  # type: ignore[return-value]
                    self._raise_shared_error(err)
                inflight = self._read_inflight(namespace, key)
                if inflight is None:
                    self._write_inflight(namespace, key, claim)
                    claimed = True
            if claimed:
                break
            time.sleep(min(_POLL_SEC, max(remaining, 0)))

        try:
            value = loader()
            persisted_value = shared_payload(value) if shared_payload is not None else value
        except BaseException as exc:
            if not isinstance(exc, Exception):
                with self._exclusive(namespace, key, timeout=self.waiter_timeout):
                    self._clear_inflight(namespace, key, claim)
                raise
            stale_allowed = stale_if(exc) if stale_if is not None else True
            superseded = False
            with (
                self._exclusive(namespace, key, timeout=self.waiter_timeout),
                self._exclusive(
                    namespace,
                    _NAMESPACE_GUARD_KEY,
                    timeout=self.waiter_timeout,
                ),
            ):
                if not self._claim_is_current(namespace, key, claim):
                    superseded = True
                elif is_current is not None and not is_current():
                    self._clear_inflight(namespace, key, claim)
                    raise
                else:
                    self._write_error(
                        namespace,
                        key,
                        exc,
                        stale_allowed=stale_allowed,
                    )
                    self._clear_inflight(namespace, key, claim)
                    stale = self._read_stale_payload(namespace, key, max_stale_seconds)
            if superseded:
                raise _LiveMergeSuperseded from None
            if stale_allowed and stale is not _MISSING:
                if on_stale is not None:
                    on_stale()
                return stale  # type: ignore[return-value]
            raise
        superseded = False
        with (
            self._exclusive(namespace, key, timeout=self.waiter_timeout),
            self._exclusive(
                namespace,
                _NAMESPACE_GUARD_KEY,
                timeout=self.waiter_timeout,
            ),
        ):
            if not self._claim_is_current(namespace, key, claim):
                superseded = True
            elif is_current is not None and not is_current():
                self._clear_inflight(namespace, key, claim)
                return value
            else:
                self._write_result(namespace, key, persisted_value)
                self._clear_error(namespace, key)
                self._clear_inflight(namespace, key, claim)
        if superseded:
            raise _LiveMergeSuperseded
        return value

    def _prune_lock(
        self,
        path: Path,
        *,
        initial_stat: os.stat_result,
        now: float,
        max_age_seconds: float,
    ) -> bool:
        try:
            fh = path.open("r+", encoding="utf-8")
        except OSError:
            return False
        with fh:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError):
                return False
            try:
                try:
                    descriptor_stat = os.fstat(fh.fileno())
                    current_stat = path.stat()
                except OSError:
                    return False
                expected_inode = (initial_stat.st_dev, initial_stat.st_ino)
                if (
                    (descriptor_stat.st_dev, descriptor_stat.st_ino) != expected_inode
                    or (current_stat.st_dev, current_stat.st_ino) != expected_inode
                    or max(0.0, now - current_stat.st_mtime) < max_age_seconds
                ):
                    return False
                inflight = self._read_json(path.with_suffix(".inflight"))
                pid = inflight.get("pid") if inflight is not None else None
                if isinstance(pid, int) and _pid_alive(pid):
                    return False
                try:
                    path.unlink()
                except OSError:
                    return False
                return True
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def invalidate(self, namespace: str, key: str) -> None:
        from robopark_api.services.ops.maintenance import require_application_writes

        require_application_writes()
        with self._exclusive(namespace, key, timeout=self.waiter_timeout):
            _, result, inflight, error = self._paths(namespace, key)
            result.unlink(missing_ok=True)
            inflight.unlink(missing_ok=True)
            error.unlink(missing_ok=True)
        family(f"shared.{namespace}").invalidate("key")
        self._update_namespace_metrics(namespace)

    def invalidate_prefix(self, namespace: str, prefix: str) -> None:
        from robopark_api.services.ops.maintenance import require_application_writes

        require_application_writes()
        folder = self.namespace_dir(namespace)
        if not folder.is_dir():
            return
        for path in folder.glob("*.json"):
            data = self._read_json(path)
            if data is None:
                continue
            stored_key = data.get("key")
            if isinstance(stored_key, str) and stored_key.startswith(prefix):
                self.invalidate(namespace, stored_key)

    @staticmethod
    def _payload_contains_issue(payload: object, issue_key: str) -> bool:
        if isinstance(payload, dict):
            if payload.get("key") == issue_key:
                return True
            return any(
                LiveMergeStore._payload_contains_issue(value, issue_key)
                for value in payload.values()
            )
        if isinstance(payload, list):
            return any(
                LiveMergeStore._payload_contains_issue(value, issue_key) for value in payload
            )
        return False

    def invalidate_payload_member(self, namespace: str, issue_key: str) -> int:
        """Invalidate only shared list projections containing one Tracker issue."""
        folder = self.namespace_dir(namespace)
        if not folder.is_dir():
            return 0
        keys = []
        for path in folder.glob("*.json"):
            data = self._read_json(path)
            if (
                data is not None
                and data.get("schema") == LIVE_MERGE_SCHEMA
                and isinstance(data.get("key"), str)
                and self._payload_contains_issue(data.get("payload"), issue_key)
            ):
                keys.append(data["key"])
        for key in keys:
            self.invalidate(namespace, key)
        return len(keys)

    def clear_namespace(self, namespace: str, *, reason: str = "namespace") -> None:
        from robopark_api.services.ops.maintenance import require_application_writes

        require_application_writes()
        with self._exclusive(
            namespace,
            _NAMESPACE_GUARD_KEY,
            timeout=self.waiter_timeout,
        ):
            folder = self.namespace_dir(namespace)
            if not folder.is_dir():
                return
            for path in folder.iterdir():
                if path.is_file() and path.suffix in {".json", ".inflight", ".error", ".tmp"}:
                    path.unlink(missing_ok=True)
        family(f"shared.{namespace}").invalidate(reason)
        self._update_namespace_metrics(namespace)

    def prune(
        self,
        *,
        now: float,
        blob_max_age_seconds: float = 60.0,
        lock_max_age_seconds: float = 60.0,
        tmp_max_age_seconds: float = 3600.0,
        max_deletions: int | None = None,
        deadline_monotonic: float | None = None,
    ) -> int:
        """Best-effort bounded removal through pinned no-follow descriptors."""
        from robopark_api.services.ops.maintenance import require_application_writes
        from robopark_api.services.storage_retention import pinned_directory, unlink_unchanged

        require_application_writes()
        if max_deletions is not None and max_deletions <= 0:
            return 0
        limit = max_deletions if max_deletions is not None else 2**63 - 1
        tmp_age = max(MIN_ABANDONED_TMP_AGE_SECONDS, tmp_max_age_seconds)
        removed = 0

        def read_json_at(directory_fd: int, name: str) -> dict[str, Any] | None:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
                    return None
                raw = os.read(descriptor, info.st_size + 1)
                value = json.loads(raw)
                return value if isinstance(value, dict) else None
            except (OSError, ValueError, UnicodeError):
                return None
            finally:
                os.close(descriptor)

        def prune_unlocked_at(
            directory_fd: int,
            name: str,
            before: os.stat_result,
            age: float,
            minimum_age: float,
        ) -> bool:
            if age < minimum_age:
                return False
            try:
                descriptor = os.open(
                    name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd
                )
            except OSError:
                return False
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                opened = os.fstat(descriptor)
                identity = (before.st_dev, before.st_ino)
                if (current.st_dev, current.st_ino) != identity or (
                    opened.st_dev,
                    opened.st_ino,
                ) != identity:
                    return False
                os.unlink(name, dir_fd=directory_fd)
                return True
            except (BlockingIOError, OSError):
                return False
            finally:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
                os.close(descriptor)

        try:
            with pinned_directory(self.root) as root_fd, os.scandir(root_fd) as namespaces:
                for namespace in namespaces:
                    if removed >= limit or (
                        deadline_monotonic is not None and time.monotonic() >= deadline_monotonic
                    ):
                        return removed
                    try:
                        namespace_info = namespace.stat(follow_symlinks=False)
                        if namespace.name == "jobs" or not stat.S_ISDIR(namespace_info.st_mode):
                            continue
                        namespace_fd = os.open(
                            namespace.name,
                            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=root_fd,
                        )
                    except OSError:
                        continue
                    try:
                        with os.scandir(namespace_fd) as entries:
                            for entry in entries:
                                if removed >= limit or (
                                    deadline_monotonic is not None
                                    and time.monotonic() >= deadline_monotonic
                                ):
                                    return removed
                                try:
                                    before = entry.stat(follow_symlinks=False)
                                except OSError:
                                    continue
                                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                                    continue
                                suffix = Path(entry.name).suffix
                                age = max(0.0, now - before.st_mtime)
                                if suffix == ".lock":
                                    if prune_unlocked_at(
                                        namespace_fd,
                                        entry.name,
                                        before,
                                        age,
                                        lock_max_age_seconds,
                                    ):
                                        removed += 1
                                    continue
                                should_remove = (
                                    suffix in {".json", ".error"} and age >= blob_max_age_seconds
                                )
                                if suffix == ".tmp":
                                    if prune_unlocked_at(
                                        namespace_fd,
                                        entry.name,
                                        before,
                                        age,
                                        tmp_age,
                                    ):
                                        removed += 1
                                    continue
                                elif suffix == ".inflight":
                                    data = read_json_at(namespace_fd, entry.name)
                                    pid = data.get("pid") if data is not None else None
                                    should_remove = not isinstance(pid, int) or not _pid_alive(pid)
                                if not should_remove:
                                    continue
                                try:
                                    unlink_unchanged(namespace_fd, entry.name, before)
                                except OSError:
                                    continue
                                removed += 1
                    finally:
                        os.close(namespace_fd)
        except OSError:
            return removed
        return removed


class JobLease:
    """Cross-process exclusive lease for lifespan jobs (keepalive, history)."""

    def __init__(self, root: Path, name: str) -> None:
        safe = name.replace("/", "_").replace("\0", "")
        self.path = Path(root) / "jobs" / f"{safe}.lock"
        self._fh: Any = None
        self.held = False

    def try_acquire(self) -> bool:
        if self.held:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.close()
            return False
        self._fh = fh
        self.held = True
        return True

    def release(self) -> None:
        if self._fh is None:
            return
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        self._fh.close()
        self._fh = None
        self.held = False
