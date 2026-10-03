"""Idempotent application data initialization after writes are permitted."""

from contextlib import contextmanager

from sqlalchemy import text

from robopark_api.dev_seed import ensure_dev_seed
from robopark_api.seed import ensure_seed_user
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services.database_locks import database_idempotency_lock
from robopark_api.services.emergency_config import ensure_default_section_roles
from robopark_api.services.rbac_seed import ensure_rbac_catalog


@contextmanager
def _startup_seed_lock(db):
    """Serialize all startup seeds across API processes, including their commits."""
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        # A separate transaction holds this lock while seed helpers commit on
        # db. A lock taken on db itself would be released at their first commit.
        with bind.connect() as connection, connection.begin():
            connection.execute(text("SET LOCAL lock_timeout = '45s'"))
            connection.execute(text("SELECT pg_advisory_xact_lock(1695825275, 1)"))
            yield
    else:
        with database_idempotency_lock(db, "startup-seed-v1"):
            yield


def initialize_data(
    session_factory, settings, *, seed_user=ensure_seed_user, dev_seed=ensure_dev_seed
) -> None:
    with session_factory() as db, _startup_seed_lock(db):
        ensure_rbac_catalog(db)
        ensure_default_section_roles(db)
        seed_user(db, settings)
        dev_seed(db, settings)
        settings_svc.migrate_plaintext_secrets(db)
        settings_svc.migrate_registration_password_from_env(db)
