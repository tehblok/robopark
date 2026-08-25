"""Database pragmas, health probes and session hygiene."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, text

from robopark_api.models import AuthSession, Base, User
from robopark_api.security import hash_password


def _engine_with_pragmas(path):
    """Engine configured exactly like the production one."""
    from sqlalchemy import event

    from robopark_api.db import apply_sqlite_pragmas

    engine = create_engine(
        f"sqlite:///{path}",
        future=True,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, _record):
        apply_sqlite_pragmas(dbapi_connection)

    return engine


def test_sqlite_pragmas_applied(tmp_path):
    """WAL + busy_timeout + foreign_keys must be set on every connection."""
    engine = _engine_with_pragmas(tmp_path / "pragma.db")

    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 5000


def test_production_engine_uses_pragmas():
    """The real engine must register the connect hook, not just the helper."""
    from sqlalchemy import event

    from robopark_api.db import _configure_sqlite, engine

    assert event.contains(engine, "connect", _configure_sqlite)


def test_foreign_keys_cascade_on_delete(tmp_path):
    """Without PRAGMA foreign_keys=ON the schema's CASCADE was a no-op."""
    engine = _engine_with_pragmas(tmp_path / "fk.db")
    Base.metadata.create_all(engine)

    from sqlalchemy.orm import Session

    with Session(engine) as session:
        user = User(
            username="cascade",
            password_hash=hash_password("x"),
            role="operator",
            access_status="approved",
            is_active=True,
        )
        session.add(user)
        session.flush()
        session.add(
            AuthSession(
                user_id=user.id,
                token_hash="deadbeef",
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        session.commit()

        user_id = user.id

    # Delete at SQL level: the ORM relationship would otherwise try to NULL the
    # FK itself, which hides whether the database enforces the CASCADE rule.
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})

    with Session(engine) as session:
        assert session.query(AuthSession).count() == 0


def test_expired_sessions_are_purged(db_session, seed_royal):
    from robopark_api.routers.auth import purge_expired_sessions

    now = datetime.now(UTC)
    db_session.add_all(
        [
            AuthSession(
                user_id=seed_royal.id,
                token_hash="expired",
                expires_at=now - timedelta(hours=1),
            ),
            AuthSession(
                user_id=seed_royal.id,
                token_hash="valid",
                expires_at=now + timedelta(hours=1),
            ),
        ]
    )
    db_session.commit()

    removed = purge_expired_sessions(db_session)

    assert removed == 1
    remaining = db_session.query(AuthSession).all()
    assert [row.token_hash for row in remaining] == ["valid"]


def test_login_purges_expired_sessions(client, db_session, seed_royal):
    from conftest import login_as

    db_session.add(
        AuthSession(
            user_id=seed_royal.id,
            token_hash="stale",
            expires_at=datetime.now(UTC) - timedelta(days=1),
        )
    )
    db_session.commit()

    assert login_as(client, "royal", "secret").status_code == 204
    assert (
        db_session.query(AuthSession).filter_by(token_hash="stale").count() == 0
    )


def test_liveness_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_reports_database_and_integrations(client):
    response = client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"
    # Nothing configured in a fresh test database.
    assert body["checks"]["tracker_token"] == "missing"
    assert body["checks"]["emergency_cookie"] == "missing"


def test_readiness_reports_configured_integrations(client, db_session):
    from robopark_api.services import platform_settings

    platform_settings.set_setting(
        db_session, platform_settings.TRACKER_TOKEN_KEY, "token"
    )
    platform_settings.set_setting(
        db_session, platform_settings.EMERGENCY_COOKIE_KEY, "cookie"
    )

    checks = client.get("/health/ready").json()["checks"]
    assert checks["tracker_token"] == "configured"
    assert checks["emergency_cookie"] == "ok"


def test_readiness_degrades_when_database_fails(client):
    """A broken database must surface as 503, not a cheerful 'ok'."""
    from robopark_api.db import get_db

    class _BrokenSession:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("database is gone")

    def _broken_db():
        yield _BrokenSession()

    client.app.dependency_overrides[get_db] = _broken_db
    try:
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["status"] == "degraded"
        assert response.json()["checks"]["database"].startswith("error")
    finally:
        client.app.dependency_overrides.pop(get_db, None)
