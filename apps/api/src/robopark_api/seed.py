from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.config import Settings
from robopark_api.models import User
from robopark_api.security import hash_password


def ensure_seed_user(db: Session, settings: Settings) -> None:
    if not settings.seed_username or not settings.seed_password:
        return

    existing = db.scalar(select(User).where(User.username == settings.seed_username))
    if existing:
        return

    db.add(
        User(
            username=settings.seed_username,
            password_hash=hash_password(settings.seed_password),
            role=settings.seed_role,
            is_active=True,
        )
    )
    db.commit()
