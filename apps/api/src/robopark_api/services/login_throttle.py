"""Database-backed brute-force protection for authentication endpoints."""

from __future__ import annotations

import hashlib
import math
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy import insert as generic_insert
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from robopark_api.models import AuthThrottleState


class LoginThrottle:
    """Shared sliding-window failure counter with temporary lockout."""

    def __init__(
        self,
        *,
        max_attempts: int,
        window_seconds: float,
        lockout_seconds: float,
        session_factory: Callable[[], Session] | None = None,
    ) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds
        self._session_factory = session_factory

    @staticmethod
    def _key_hash(key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    @staticmethod
    def _now(now: datetime | None) -> datetime:
        return now or datetime.now(UTC)

    @contextmanager
    def _session(self, db: Session | None) -> Iterator[Session]:
        if db is not None:
            yield db
            return
        if self._session_factory is None:
            raise RuntimeError("A database session or session_factory is required")
        with self._session_factory() as owned:
            yield owned

    def retry_after(
        self, key: str, *, db: Session | None = None, now: datetime | None = None
    ) -> int:
        """Seconds the caller must wait, or 0 when the attempt is allowed."""
        moment = self._now(now)
        with self._session(db) as session:
            locked_until = session.scalar(
                select(AuthThrottleState.locked_until).where(
                    AuthThrottleState.key_hash == self._key_hash(key)
                )
            )
        if locked_until is None:
            return 0
        if locked_until.tzinfo is None:  # SQLite drops timezone information.
            locked_until = locked_until.replace(tzinfo=UTC)
        remaining = (locked_until - moment).total_seconds()
        return max(1, math.ceil(remaining)) if remaining > 0 else 0

    def register_failure(
        self,
        key: str,
        *,
        db: Session | None = None,
        now: datetime | None = None,
        commit: bool = True,
    ) -> None:
        """Atomically add a failure so every API worker sees the same count."""
        moment = self._now(now)
        key_hash = self._key_hash(key)
        cutoff = moment - timedelta(seconds=self.window_seconds)
        window_expiry = moment + timedelta(seconds=self.window_seconds)
        lockout_expiry = moment + timedelta(seconds=self.lockout_seconds)

        with self._session(db) as session:
            dialect = session.get_bind().dialect.name
            insert_fn = {
                "postgresql": postgresql_insert,
                "sqlite": sqlite_insert,
            }.get(dialect, generic_insert)
            seed = insert_fn(AuthThrottleState).values(
                key_hash=key_hash,
                failure_count=0,
                window_started_at=moment,
                locked_until=None,
                expires_at=window_expiry,
                created_at=moment,
                updated_at=moment,
            )
            if dialect in {"postgresql", "sqlite"}:
                seed = seed.on_conflict_do_nothing(index_elements=["key_hash"])
            session.execute(seed)

            active_lock = func.coalesce(AuthThrottleState.locked_until > moment, False)
            expired_window = or_(
                AuthThrottleState.window_started_at.is_(None),
                AuthThrottleState.window_started_at <= cutoff,
                AuthThrottleState.expires_at <= moment,
            )
            next_count = case(
                (active_lock, AuthThrottleState.failure_count),
                (expired_window, 1),
                else_=AuthThrottleState.failure_count + 1,
            )
            will_lock = (~active_lock) & (next_count >= self.max_attempts)
            session.execute(
                update(AuthThrottleState)
                .where(AuthThrottleState.key_hash == key_hash)
                .values(
                    failure_count=case((will_lock, 0), else_=next_count),
                    window_started_at=case(
                        (expired_window, moment),
                        else_=AuthThrottleState.window_started_at,
                    ),
                    locked_until=case(
                        (active_lock, AuthThrottleState.locked_until),
                        (will_lock, lockout_expiry),
                        else_=None,
                    ),
                    expires_at=case(
                        (active_lock, AuthThrottleState.expires_at),
                        (will_lock, lockout_expiry),
                        else_=window_expiry,
                    ),
                    updated_at=moment,
                )
            )
            if commit:
                session.commit()

    def reset(self, key: str, *, db: Session | None = None, commit: bool = True) -> None:
        """Clear state after a successful attempt."""
        with self._session(db) as session:
            session.execute(
                delete(AuthThrottleState).where(
                    AuthThrottleState.key_hash == self._key_hash(key)
                )
            )
            if commit:
                session.commit()


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
    """Drop cached configuration (used by tests)."""
    global _login_throttle, _register_throttle
    with _init_lock:
        _login_throttle = None
        _register_throttle = None


def client_ip(request) -> str:
    """Client address as seen by the reverse proxy."""
    real = (request.headers.get("x-real-ip") or "").strip()
    if real:
        return real
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"
