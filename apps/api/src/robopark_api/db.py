from collections.abc import AsyncGenerator
from contextvars import ContextVar, Token
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from robopark_api.config import get_settings
from robopark_api.services.ops.maintenance import (
    HostMaintenanceActive,
    host_maintenance_active,
    require_application_writes,
)


def apply_sqlite_pragmas(dbapi_connection, *, readonly: bool = False) -> None:
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
        # Journal mode is persistent. The other PRAGMAs only configure this
        # connection and must also apply to read-only candidate connections.
        if not readonly:
            cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    apply_sqlite_pragmas(dbapi_connection, readonly=host_maintenance_active())


def configure_engine(database_url: str) -> Engine:
    """Build the bounded application engine for SQLite or PostgreSQL."""
    url = make_url(database_url)
    is_sqlite = url.drivername.startswith("sqlite")
    sqlite_file = is_sqlite and url.database not in {None, "", ":memory:"}
    options: dict[str, Any] = {"future": True}
    if is_sqlite:
        options["connect_args"] = {"check_same_thread": False}
        if sqlite_file:
            # File SQLite connections are cheap; do not put a second capacity
            # queue in front of the finite request worker pool.
            options["poolclass"] = NullPool
    elif url.get_backend_name() == "postgresql":
        options.update(
            pool_size=5,
            max_overflow=5,
            pool_pre_ping=True,
            pool_recycle=300,
        )
    configured = create_engine(database_url, **options)
    if is_sqlite:
        event.listen(configured, "connect", _configure_sqlite)

    return configured


_settings = get_settings()
engine = configure_engine(_settings.database_url)


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


@event.listens_for(Engine, "begin")
def _begin_writer_tracking(connection) -> None:
    connection.info["robopark_application_write"] = False


@event.listens_for(Engine, "before_cursor_execute")
def _guard_application_statement(connection, cursor, statement, parameters, context, executemany):
    # A SELECT-only observer remains usable during cutover. DML, DDL and raw
    # statements (including writable PRAGMAs/CTEs) must pass the host barrier.
    if statement.lstrip().upper().startswith(("SELECT", "EXPLAIN")):
        return
    require_application_writes(getattr(connection.engine, "_robopark_ops_settings", None))
    connection.info["robopark_application_write"] = True


@event.listens_for(Engine, "commit")
def _guard_application_commit(connection) -> None:
    if connection.info.get("robopark_application_write"):
        try:
            require_application_writes(getattr(connection.engine, "_robopark_ops_settings", None))
        except HostMaintenanceActive:
            # A failed commit event ends SQLAlchemy's transaction bookkeeping,
            # but leaves SQLite's transaction open. Explicitly discard it so a
            # later connection reuse cannot commit the rejected business write.
            connection.connection.dbapi_connection.rollback()
            raise
