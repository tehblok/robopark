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
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

from robopark_api.config import get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Match Tracker slot-wait class (``tracker_api.DEFAULT_SLOT_WAIT_SEC``).
DEFAULT_WAITER_TIMEOUT_SEC = 25.0
_POLL_SEC = 0.05
_LOCK_RETRY_SEC = 0.01
_DISABLED = object()
_MISSING = object()

LIVE_MERGE_DIRNAME = "live-merge"

_store_cache: object = _DISABLED


class LiveMergeTimeout(TimeoutError):
    """A waiter waited the bound without a result or a claim."""


class LiveMergeUpstreamError(RuntimeError):
    """Shared failure recorded by the merge leader so waiters do not refetch."""


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
                require_application_writes()
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def _atomic_write(self, path: Path, payload: dict[str, Any]) -> None:
        from robopark_api.services.ops.maintenance import require_application_writes

        require_application_writes()
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(encoded)
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

    def _write_result(self, namespace: str, key: str, payload: Any) -> None:
        self._atomic_write(
            self.result_path(namespace, key),
            {
                "key": key,
                "written_at": time.time(),
                "ok": True,
                "payload": payload,
            },
        )

    def _read_stale_payload(self, namespace: str, key: str, max_age: float) -> object:
        data = self._read_json(self.result_path(namespace, key))
        if data is None or data.get("ok") is not True or data.get("key") != key:
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
    ) -> T:
        from robopark_api.db import release_request_session

        # Both file-lock contention and another process's upstream flight can
        # wait. Return the DB connection before either, including cache hits
        # discovered after waiting; releasing only in the loader misses them.
        release_request_session()
        deadline = time.monotonic() + self.waiter_timeout
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
        except BaseException as exc:
            stale_allowed = stale_if(exc) if stale_if is not None else True
            with self._exclusive(namespace, key, timeout=self.waiter_timeout):
                if is_current is not None and not is_current():
                    self._clear_inflight(namespace, key, claim)
                    raise
                self._write_error(
                    namespace,
                    key,
                    exc,
                    stale_allowed=stale_allowed,
                )
                self._clear_inflight(namespace, key, claim)
                stale = self._read_stale_payload(namespace, key, max_stale_seconds)
            if stale_allowed and stale is not _MISSING:
                if on_stale is not None:
                    on_stale()
                return stale  # type: ignore[return-value]
            raise
        with self._exclusive(namespace, key, timeout=self.waiter_timeout):
            if is_current is not None and not is_current():
                self._clear_inflight(namespace, key, claim)
                return value
            self._write_result(namespace, key, value)
            self._clear_error(namespace, key)
            self._clear_inflight(namespace, key, claim)
        return value

    def invalidate(self, namespace: str, key: str) -> None:
        from robopark_api.services.ops.maintenance import require_application_writes

        require_application_writes()
        _, result, inflight, error = self._paths(namespace, key)
        result.unlink(missing_ok=True)
        inflight.unlink(missing_ok=True)
        error.unlink(missing_ok=True)

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
                path.unlink(missing_ok=True)
                path.with_suffix(".inflight").unlink(missing_ok=True)
                path.with_name(path.stem + ".error").unlink(missing_ok=True)

    def clear_namespace(self, namespace: str) -> None:
        from robopark_api.services.ops.maintenance import require_application_writes

        require_application_writes()
        folder = self.namespace_dir(namespace)
        if not folder.is_dir():
            return
        for path in folder.iterdir():
            if path.is_file():
                path.unlink(missing_ok=True)

    def prune(
        self,
        *,
        now: float,
        blob_max_age_seconds: float = 60.0,
        lock_max_age_seconds: float = 60.0,
        tmp_max_age_seconds: float = 3600.0,
    ) -> int:
        """Best-effort removal of stale live-merge artifacts."""
        from robopark_api.services.ops.maintenance import require_application_writes

        require_application_writes()
        removed = 0
        try:
            folders = list(self.root.iterdir())
        except OSError:
            return 0
        for folder in folders:
            try:
                is_dir = folder.is_dir()
            except OSError:
                continue
            if not is_dir or folder.name == "jobs":
                continue
            try:
                paths = list(folder.iterdir())
            except OSError:
                continue
            for path in paths:
                try:
                    stat = path.stat()
                except OSError:
                    continue
                suffix = path.suffix
                age = max(0.0, now - stat.st_mtime)
                should_remove = False
                if suffix in {".json", ".error"}:
                    should_remove = age >= blob_max_age_seconds
                elif suffix == ".tmp":
                    should_remove = age >= tmp_max_age_seconds
                elif suffix == ".inflight":
                    data = self._read_json(path)
                    pid = data.get("pid") if data is not None else None
                    should_remove = not isinstance(pid, int) or not _pid_alive(pid)
                elif suffix == ".lock":
                    inflight = self._read_json(path.with_suffix(".inflight"))
                    pid = inflight.get("pid") if inflight is not None else None
                    should_remove = age >= lock_max_age_seconds and not (
                        isinstance(pid, int) and _pid_alive(pid)
                    )
                if not should_remove:
                    continue
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    continue
                removed += 1
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
