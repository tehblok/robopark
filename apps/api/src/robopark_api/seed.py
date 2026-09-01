from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.config import Settings
from robopark_api.models import AccessStatus, User
from robopark_api.security import hash_password
from robopark_api.services import rbac
from robopark_api.services.rbac import RoleSlug
from robopark_api.services.rbac_seed import ensure_rbac_catalog


def ensure_seed_user(db: Session, settings: Settings) -> None:
    if not settings.seed_username or not settings.seed_password:
        return

    ensure_rbac_catalog(db)
    role_slug = _validated_role_slug(settings.seed_role)

    existing = db.scalar(select(User).where(User.username == settings.seed_username))
    if existing:
        return

    role = rbac.get_role_by_slug(db, role_slug)
    assert role is not None

    user = User(
        username=settings.seed_username,
        password_hash=hash_password(settings.seed_password),
        role_id=role.id,
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db.add(user)
    db.commit()


def _validated_role_slug(seed_role: str) -> str:
    """Reject a role the SPA has no cabinet for, instead of seeding a dead user."""
    slug = seed_role.strip().lower()
    if slug not in RoleSlug.SYSTEM:
        known = ", ".join(sorted(RoleSlug.SYSTEM))
        raise ValueError(f"SEED_ROLE={seed_role!r} is not a known role. Expected one of: {known}.")
    return slug
