from collections.abc import Generator

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


def get_engine() -> Engine:
    return engine


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
