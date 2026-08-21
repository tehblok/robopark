from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from robopark_api.config import get_settings


_settings = get_settings()
engine = create_engine(
    _settings.database_url,
    future=True,
    connect_args={"check_same_thread": False}
    if _settings.database_url.startswith("sqlite")
    else {},
)
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
