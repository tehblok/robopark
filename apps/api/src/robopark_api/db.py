from collections.abc import AsyncGenerator
from contextvars import ContextVar, Token
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from robopark_api.config import get_settings

_settings = get_settings()
_is_sqlite = _settings.database_url.startswith("sqlite")

engine = create_engine(
    _settings.database_url,
    future=True,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
)


def apply_sqlite_pragmas(dbapi_connection) -> None:
    """Apply the PRAGMAs SQLite needs for concurrent use.

    Two background jobs (Emergency keep-alive and the blocker history scan)
    write from worker threads while requests are served, so the default
    rollback journal produced `database is locked` errors under load.

    * ``journal_mode=WAL`` lets readers run while a writer is active.
    * ``busy_timeout`` makes a competing writer wait instead of failing.
    * ``foreign_keys=ON`` — SQLite ignores ``ondelete`` clauses without it, so
      the schema's CASCADE rules were never actually enforced.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


@event.listens_for(engine, "connect")
def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    if not _is_sqlite:
        return
    apply_sqlite_pragmas(dbapi_connection)


SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    future=True,
)


class RequestSession:
    """Request-scoped Session that can drop its pool connection during a wait.

    ORM objects already loaded stay usable as detached instances after
    ``release_connection`` (expunge, then close). The next attribute access
    opens a new Session.
    """

    def __init__(self, factory: sessionmaker[Session] | None = None) -> None:
        self._factory = factory or SessionLocal
        self._inner: Session | None = None

    def _live(self) -> Session:
        if self._inner is None:
            self._inner = self._factory()
        return self._inner

    def release_connection(self) -> None:
        if self._inner is None:
            return
        self._inner.expunge_all()
        self._inner.close()
        self._inner = None

    def close(self) -> None:
        if self._inner is not None:
            self._inner.close()
            self._inner = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._live(), name)


_request_session: ContextVar[RequestSession | None] = ContextVar(
    "robopark_request_session", default=None
)


def bind_request_session(wrapper: RequestSession) -> Token[RequestSession | None]:
    return _request_session.set(wrapper)


def reset_request_session(token: Token[RequestSession | None]) -> None:
    _request_session.reset(token)


def release_request_session() -> None:
    wrapper = _request_session.get()
    if wrapper is not None:
        wrapper.release_connection()


def get_engine() -> Engine:
    return engine


async def get_db() -> AsyncGenerator[Session, None]:
    wrapper = RequestSession()
    token = bind_request_session(wrapper)
    try:
        yield wrapper  # type: ignore[misc]
    finally:
        wrapper.close()
        reset_request_session(token)
