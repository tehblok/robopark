from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from robopark_api.config import Settings, get_settings
from robopark_api.db import get_db
from robopark_api.deps import require_admin
from robopark_api.models import AccessStatus, AuthSession, Park, User, UserPark, UserRole
from robopark_api.schemas import MechanicCreate, MechanicOut, MechanicUpdate, ParkOut
from robopark_api.security import PasswordPolicyError, hash_password, validate_password

router = APIRouter(
    prefix="/admin/mechanics",
    tags=["admin-mechanics"],
    dependencies=[Depends(require_admin)],
)


def _park_out(park: Park) -> ParkOut:
    return ParkOut.model_validate(park)


def _mechanic_out(user: User, park: Park) -> MechanicOut:
    return MechanicOut(
        id=user.id,
        username=user.username,
        is_active=user.is_active,
        created_at=user.created_at,
        tracker_login=user.tracker_login,
        must_change_password=user.must_change_password,
        park=_park_out(park),
    )


def _get_mechanic(db: Session, mechanic_id: int) -> User | None:
    user = db.get(User, mechanic_id)
    if user is None or user.role != UserRole.mechanic.value:
        return None
    return user


def _single_park(db: Session, user_id: int) -> Park | None:
    parks = db.scalars(
        select(Park).join(UserPark).where(UserPark.user_id == user_id)
    ).all()
    if len(parks) != 1:
        return None
    return parks[0]


def _set_single_park(db: Session, user_id: int, park_id: int) -> None:
    db.execute(delete(UserPark).where(UserPark.user_id == user_id))
    db.add(UserPark(user_id=user_id, park_id=park_id))


def _active_park(db: Session, park_id: int) -> Park:
    park = db.get(Park, park_id)
    if park is None or not park.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)
    return park


def _check_password(password: str, settings: Settings, username: str | None) -> None:
    try:
        validate_password(
            password,
            min_length=settings.password_min_length,
            require_complexity=settings.password_require_complexity,
            username=username,
        )
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _revoke_sessions(db: Session, user_id: int) -> None:
    """Drop every active session of a user (password change / deactivation)."""
    db.execute(delete(AuthSession).where(AuthSession.user_id == user_id))


@router.get("", response_model=list[MechanicOut])
def list_mechanics(db: Session = Depends(get_db)) -> list[MechanicOut]:
    mechanics = db.scalars(
        select(User).where(User.role == UserRole.mechanic.value).order_by(User.id)
    ).all()
    result: list[MechanicOut] = []
    for user in mechanics:
        park = _single_park(db, user.id)
        if park is None:
            continue
        result.append(_mechanic_out(user, park))
    return result


@router.post("", response_model=MechanicOut, status_code=status.HTTP_201_CREATED)
def create_mechanic(
    payload: MechanicCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MechanicOut:
    if db.scalar(select(User.id).where(User.username == payload.username)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT)
    _check_password(payload.password, settings, payload.username)
    park = _active_park(db, payload.park_id)
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=UserRole.mechanic.value,
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db.add(user)
    db.flush()
    db.add(UserPark(user_id=user.id, park_id=park.id))
    db.commit()
    db.refresh(user)
    return _mechanic_out(user, park)


@router.patch("/{mechanic_id}", response_model=MechanicOut)
def update_mechanic(
    mechanic_id: int,
    payload: MechanicUpdate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MechanicOut:
    user = _get_mechanic(db, mechanic_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    changes = payload.model_dump(exclude_unset=True)
    if password := changes.get("password"):
        _check_password(password, settings, user.username)
        user.password_hash = hash_password(password)
        # A rotated password must invalidate sessions opened with the old one.
        _revoke_sessions(db, user.id)
    if (is_active := changes.get("is_active")) is not None:
        user.is_active = is_active
        if not is_active:
            _revoke_sessions(db, user.id)
    if "tracker_login" in changes:
        raw = changes.get("tracker_login")
        user.tracker_login = raw.strip() if isinstance(raw, str) and raw.strip() else None
    if (must_change := changes.get("must_change_password")) is not None:
        user.must_change_password = bool(must_change)
    park = _single_park(db, user.id)
    if park is None and changes.get("park_id") is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)
    if park_id := changes.get("park_id"):
        park = _active_park(db, park_id)
        _set_single_park(db, user.id, park.id)
    elif park is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)

    db.commit()
    db.refresh(user)
    park = _single_park(db, user.id)
    assert park is not None
    return _mechanic_out(user, park)
