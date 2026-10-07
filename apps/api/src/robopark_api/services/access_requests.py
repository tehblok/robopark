from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, lazyload

from robopark_api.models import AccessStatus, Park, ParkRequest, Role, User, UserPark
from robopark_api.services import audit, rbac

APPLICANT_ROLES = frozenset({rbac.RoleSlug.MECHANIC, rbac.RoleSlug.OPERATOR})


def require_applicant(user: User) -> User:
    if (
        not user.is_active
        or user.role not in APPLICANT_ROLES
        or user.access_status == AccessStatus.rejected.value
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user


def assigned_parks(db: Session, user_id: int) -> list[Park]:
    return list(
        db.scalars(
            select(Park)
            .join(UserPark, UserPark.park_id == Park.id)
            .where(UserPark.user_id == user_id)
            .order_by(Park.id)
        )
    )


def own_requests(db: Session, user_id: int) -> list[ParkRequest]:
    return list(
        db.scalars(
            select(ParkRequest).where(ParkRequest.user_id == user_id).order_by(ParkRequest.id)
        )
    )


def available_parks(db: Session, user_id: int) -> list[Park]:
    assigned = select(UserPark.user_id).where(
        UserPark.user_id == user_id,
        UserPark.park_id == Park.id,
    )
    pending = select(ParkRequest.id).where(
        ParkRequest.user_id == user_id,
        ParkRequest.park_id == Park.id,
        ParkRequest.status == AccessStatus.pending.value,
    )
    return list(
        db.scalars(
            select(Park)
            .where(Park.is_active.is_(True), ~assigned.exists(), ~pending.exists())
            .order_by(Park.id)
        )
    )


def create_request(db: Session, user: User, park_id: int) -> ParkRequest:
    require_applicant(user)
    locked_user = db.scalar(
        select(User)
        .options(lazyload(User.role_ref))
        .where(User.id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked_user is None or not locked_user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    require_applicant(locked_user)
    park = db.get(Park, park_id)
    if park is None or not park.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="park_unavailable")
    if db.get(UserPark, (locked_user.id, park_id)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="park_already_assigned")
    pending = db.scalar(
        select(ParkRequest.id).where(
            ParkRequest.user_id == locked_user.id,
            ParkRequest.park_id == park_id,
            ParkRequest.status == AccessStatus.pending.value,
        )
    )
    if pending is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="request_already_pending")
    row = ParkRequest(
        user_id=locked_user.id,
        park_id=park_id,
        status=AccessStatus.pending.value,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    audit.record(
        db,
        action=audit.ACTION_ACCESS_REQUESTED,
        actor=locked_user,
        park_id=park_id,
        target_type="park_request",
        target_id=row.id,
    )
    return row


def manageable_park_ids(db: Session, actor: User) -> set[int] | None:
    if actor.access_status != AccessStatus.approved.value or not actor.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    if actor.role == rbac.RoleSlug.ROYAL:
        return None
    if actor.role != rbac.RoleSlug.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return set(db.scalars(select(UserPark.park_id).where(UserPark.user_id == actor.id)))


def for_manager(db: Session, actor: User, request_status: str) -> list[ParkRequest]:
    park_ids = manageable_park_ids(db, actor)
    query = (
        select(ParkRequest)
        .join(User, User.id == ParkRequest.user_id)
        .join(Role, Role.id == User.role_id)
        .where(
            ParkRequest.status == request_status,
            User.is_active.is_(True),
            User.access_status != AccessStatus.rejected.value,
            Role.slug.in_(APPLICANT_ROLES),
        )
    )
    if park_ids is not None:
        if not park_ids:
            return []
        query = query.where(ParkRequest.park_id.in_(park_ids))
    return list(db.scalars(query.order_by(ParkRequest.id)))


def pending_for_manager(db: Session, actor: User) -> list[ParkRequest]:
    return for_manager(db, actor, AccessStatus.pending.value)


def _assert_request_scope(db: Session, actor: User, row: ParkRequest) -> None:
    park_ids = manageable_park_ids(db, actor)
    if park_ids is not None and row.park_id not in park_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


def decide(
    db: Session,
    actor: User,
    request_id: int,
    *,
    approve: bool,
    revision: int | None,
    target_park_id: int | None = None,
    global_access: bool = False,
) -> tuple[ParkRequest, User]:
    row = db.scalar(
        select(ParkRequest)
        .where(ParkRequest.id == request_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    manageable_ids = manageable_park_ids(db, actor)
    if manageable_ids is not None and row.park_id not in manageable_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    if revision is not None and row.revision != revision:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="revision_conflict")
    if row.status != AccessStatus.pending.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="request_already_resolved")
    target = db.scalar(
        select(User)
        .options(lazyload(User.role_ref))
        .where(User.id == row.user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        target is None
        or not target.is_active
        or target.role not in APPLICANT_ROLES
        or target.access_status == AccessStatus.rejected.value
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="target_not_eligible")
    requested_park = db.get(Park, row.park_id)
    if requested_park is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="park_missing")
    if not approve and (target_park_id is not None or global_access):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_rejection_target"
        )
    if target_park_id is not None and global_access:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_access_target")

    park = requested_park
    grant_parks: list[Park] = []
    if approve and global_access:
        grant_parks = list(
            db.scalars(select(Park).where(Park.is_active.is_(True)).order_by(Park.id))
        )
        active_ids = {candidate.id for candidate in grant_parks}
        if manageable_ids is not None and not active_ids.issubset(manageable_ids):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        if (
            target.access_status != AccessStatus.pending.value
            and target.role != rbac.RoleSlug.OPERATOR
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="global_role_change_forbidden"
            )
        if target.role == rbac.RoleSlug.MECHANIC:
            operator_role = rbac.get_role_by_slug(db, rbac.RoleSlug.OPERATOR)
            if operator_role is None or not operator_role.is_active:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="operator_role_unavailable",
                )
            target.role_id = operator_role.id
        elif target.role != rbac.RoleSlug.OPERATOR:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="target_not_eligible")
    elif approve:
        selected_park_id = target_park_id or row.park_id
        if manageable_ids is not None and selected_park_id not in manageable_ids:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        park = db.get(Park, selected_park_id)
        if park is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="park_missing")
        grant_parks = [park]
    now = datetime.now(UTC)
    row.status = AccessStatus.approved.value if approve else AccessStatus.rejected.value
    row.resolved_at = now
    row.resolved_by = actor.id
    row.revision += 1
    if approve:
        if any(not candidate.is_active for candidate in grant_parks):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="park_inactive")
        if target_park_id is not None:
            row.park_id = park.id
        for granted_park in grant_parks:
            if db.get(UserPark, (target.id, granted_park.id)) is None:
                db.add(UserPark(user_id=target.id, park_id=granted_park.id))
        target.access_status = AccessStatus.approved.value
    elif target.access_status == AccessStatus.pending.value:
        other_pending = db.scalar(
            select(ParkRequest.id).where(
                ParkRequest.user_id == target.id,
                ParkRequest.id != row.id,
                ParkRequest.status == AccessStatus.pending.value,
            )
        )
        if other_pending is None:
            target.access_status = AccessStatus.rejected.value
    db.commit()
    db.refresh(row)
    db.refresh(target)
    audit.record(
        db,
        action=(
            audit.ACTION_ACCESS_REQUEST_APPROVED
            if approve
            else audit.ACTION_ACCESS_REQUEST_REJECTED
        ),
        actor=actor,
        park_id=park.id,
        target_type="park_request",
        target_id=row.id,
    )
    if approve or target.access_status == AccessStatus.rejected.value:
        audit.record(
            db,
            action=(audit.ACTION_ACCESS_APPROVED if approve else audit.ACTION_ACCESS_REJECTED),
            actor=actor,
            park_id=park.id,
            target_type="user",
            target_id=target.id,
        )
    return row, target
