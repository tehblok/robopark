"""Idempotent application data initialization after writes are permitted."""

from robopark_api.dev_seed import ensure_dev_seed
from robopark_api.seed import ensure_seed_user
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services.emergency_config import ensure_default_section_roles
from robopark_api.services.rbac_seed import ensure_rbac_catalog


def initialize_data(
    session_factory, settings, *, seed_user=ensure_seed_user, dev_seed=ensure_dev_seed
) -> None:
    with session_factory() as db:
        ensure_rbac_catalog(db)
        ensure_default_section_roles(db)
        seed_user(db, settings)
        dev_seed(db, settings)
        settings_svc.migrate_plaintext_secrets(db)
        settings_svc.migrate_registration_password_from_env(db)
