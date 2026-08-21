from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.config import Settings
from robopark_api.models import User, UserRole
from robopark_api.security import hash_password


def ensure_seed_user(db: Session, settings: Settings) -> None:
    if not settings.seed_username or not settings.seed_password:
        return

    role = _validated_role(settings.seed_role)

    existing = db.scalar(select(User).where(User.username == settings.seed_username))
    if existing:
        return

    db.add(
        User(
            username=settings.seed_username,
            password_hash=hash_password(settings.seed_password),
            role=role.value,
            is_active=True,
        )
    )
    db.commit()


def _validated_role(seed_role: str) -> UserRole:
    """Reject a role the SPA has no cabinet for, instead of seeding a dead user."""
    try:
        return UserRole(seed_role)
    except ValueError as error:
        known = ", ".join(role.value for role in UserRole)
        raise ValueError(
            f"SEED_ROLE={seed_role!r} is not a known role. Expected one of: {known}."
        ) from error
