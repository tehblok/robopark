"""In-process brute-force protection for authentication endpoints.

`POST /auth/login` and `POST /auth/register` were previously unlimited, so both
a user's password and the shared registration password could be guessed at
network speed.

Counters live in the API process. With ``uvicorn --workers`` each worker has
its own map, so the limit is best-effort per process (follow-up: host-wide
file/SQLite throttle). Restarting the API still clears the counters.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class _Bucket:
    failures: list[float] = field(default_factory=list)
    locked_until: float = 0.0


class LoginThrottle:
    """Sliding-window failure counter with temporary lockout."""

    def __init__(
        self,
        *,
        max_attempts: int,
        window_seconds: float,
        lockout_seconds: float,
    ) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def _prune(self, bucket: _Bucket, now: float) -> None:
        cutoff = now - self.window_seconds
        bucket.failures = [ts for ts in bucket.failures if ts > cutoff]

    def retry_after(self, key: str, *, now: float | None = None) -> int:
        """Seconds the caller must wait, or 0 when the attempt is allowed."""
        moment = now if now is not None else time.monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                return 0
            if bucket.locked_until > moment:
                return max(1, int(bucket.locked_until - moment))
            if bucket.locked_until and bucket.locked_until <= moment:
                # Lockout expired: start from a clean slate.
                self._buckets.pop(key, None)
                return 0
            self._prune(bucket, moment)
            if not bucket.failures:
                self._buckets.pop(key, None)
            return 0

    def register_failure(self, key: str, *, now: float | None = None) -> None:
        moment = now if now is not None else time.monotonic()
        with self._lock:
            bucket = self._buckets.setdefault(key, _Bucket())
            self._prune(bucket, moment)
            bucket.failures.append(moment)
            if len(bucket.failures) >= self.max_attempts:
                bucket.locked_until = moment + self.lockout_seconds
                bucket.failures.clear()

    def reset(self, key: str) -> None:
        """Clear state after a successful attempt."""
        with self._lock:
            self._buckets.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._buckets.clear()


_login_throttle: LoginThrottle | None = None
_register_throttle: LoginThrottle | None = None
_init_lock = threading.Lock()


def get_login_throttle(settings) -> LoginThrottle:
    global _login_throttle
    with _init_lock:
        if _login_throttle is None:
            _login_throttle = LoginThrottle(
                max_attempts=settings.login_max_attempts,
                window_seconds=settings.login_attempt_window_seconds,
                lockout_seconds=settings.login_lockout_seconds,
            )
        return _login_throttle


def get_register_throttle(settings) -> LoginThrottle:
    global _register_throttle
    with _init_lock:
        if _register_throttle is None:
            _register_throttle = LoginThrottle(
                max_attempts=settings.register_max_attempts,
                window_seconds=settings.login_attempt_window_seconds,
                lockout_seconds=settings.login_lockout_seconds,
            )
        return _register_throttle


def reset_throttles() -> None:
    """Drop all throttle state (used by tests)."""
    global _login_throttle, _register_throttle
    with _init_lock:
        _login_throttle = None
        _register_throttle = None


def client_ip(request) -> str:
    """Client address as seen by the reverse proxy.

    Prefer ``X-Real-IP`` (set by nginx from the corrected ``$remote_addr``).
    Do **not** trust the first ``X-Forwarded-For`` hop — any client that can
    reach the API socket directly could rotate forged values and bypass
    login/register lockouts.
    """
    real = (request.headers.get("x-real-ip") or "").strip()
    if real:
        return real
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"
