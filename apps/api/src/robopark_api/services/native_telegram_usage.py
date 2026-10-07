from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from robopark_api.models import (
    AccessStatus,
    NativeBotControl,
    NativeBotUsageDaily,
    NativeBotUsageTotal,
    TelegramAccount,
    User,
    UserPark,
)
from robopark_api.native_telegram_usage_schemas import (
    NativeBotControlOut,
    NativeUsageQueryOut,
    NativeUsageStatsOut,
    NativeUsageUserOut,
)
from robopark_api.services import audit

USAGE_TIMEZONE = ZoneInfo("Europe/Moscow")
DAILY_RETENTION_DAYS = 90
CONTROL_ID = 1


def utcnow() -> datetime:
    return datetime.now(UTC)


def _local_now() -> datetime:
    return utcnow().astimezone(USAGE_TIMEZONE)


def _usage_insert(db: Session, model, values: dict):
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        return postgresql_insert(model).values(**values)
    if dialect == "sqlite":
        return sqlite_insert(model).values(**values)
    raise RuntimeError(f"unsupported usage counter dialect: {dialect}")


def _increment_total(db: Session, user_id: int) -> None:
    statement = _usage_insert(db, NativeBotUsageTotal, {"user_id": user_id, "total": 1})
    db.execute(
        statement.on_conflict_do_update(
            index_elements=[NativeBotUsageTotal.user_id],
            set_={
                "total": NativeBotUsageTotal.total + 1,
                "updated_at": func.now(),
            },
        )
    )


def _increment_day(db: Session, user_id: int, day: date) -> None:
    statement = _usage_insert(
        db,
        NativeBotUsageDaily,
        {"user_id": user_id, "day": day, "count": 1},
    )
    db.execute(
        statement.on_conflict_do_update(
            index_elements=[NativeBotUsageDaily.user_id, NativeBotUsageDaily.day],
            set_={"count": NativeBotUsageDaily.count + 1},
        )
    )


def _stats_for_day(db: Session, user_id: int, today: date) -> NativeUsageStatsOut:
    month_start = today.replace(day=1)
    total = db.scalar(
        select(NativeBotUsageTotal.total).where(NativeBotUsageTotal.user_id == user_id)
    )
    today_count = db.scalar(
        select(NativeBotUsageDaily.count).where(
            NativeBotUsageDaily.user_id == user_id,
            NativeBotUsageDaily.day == today,
        )
    )
    month_count = db.scalar(
        select(func.coalesce(func.sum(NativeBotUsageDaily.count), 0)).where(
            NativeBotUsageDaily.user_id == user_id,
            NativeBotUsageDaily.day >= month_start,
            NativeBotUsageDaily.day <= today,
        )
    )
    return NativeUsageStatsOut(
        today=int(today_count or 0),
        month=int(month_count or 0),
        total=int(total or 0),
    )


def user_stats(db: Session, user_id: int) -> NativeUsageStatsOut:
    return _stats_for_day(db, user_id, _local_now().date())


def self_usage(db: Session, user: User) -> NativeUsageUserOut:
    account = db.get(TelegramAccount, user.id)
    return NativeUsageUserOut(
        user_id=user.id,
        username=user.username,
        telegram_user_id=account.telegram_user_id if account is not None else None,
        stats=user_stats(db, user.id),
    )


def _greeting(username: str, hour: int) -> str:
    name = escape(username.strip())
    if 5 <= hour < 12:
        return f"Доброе утро, {name}!" if name else "Доброе утро!"
    if 18 <= hour < 23:
        return f"Добрый вечер, {name}!" if name else "Добрый вечер!"
    return f"Привет, {name}!" if name else "Привет!"


def record_success(db: Session, user: User) -> NativeUsageQueryOut:
    local = _local_now()
    today = local.date()
    _increment_total(db, user.id)
    _increment_day(db, user.id, today)
    greeted = db.scalar(
        update(NativeBotUsageTotal)
        .where(
            NativeBotUsageTotal.user_id == user.id,
            or_(
                NativeBotUsageTotal.greeted_on.is_(None),
                NativeBotUsageTotal.greeted_on != today,
            ),
        )
        .values(greeted_on=today)
        .returning(NativeBotUsageTotal.user_id)
    )
    cutoff = today - timedelta(days=DAILY_RETENTION_DAYS - 1)
    db.execute(delete(NativeBotUsageDaily).where(NativeBotUsageDaily.day < cutoff))
    db.commit()
    account = db.get(TelegramAccount, user.id) if greeted is not None else None
    display_name = account.display_name if account and account.display_name else user.username
    return NativeUsageQueryOut(
        stats=_stats_for_day(db, user.id, today),
        greeting=_greeting(display_name, local.hour) if greeted is not None else None,
    )


def all_usage(db: Session, manager: User) -> list[NativeUsageUserOut]:
    if (
        manager.role not in {"royal", "admin"}
        or not manager.is_active
        or manager.access_status != AccessStatus.approved.value
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    today = _local_now().date()
    month_start = today.replace(day=1)
    daily = (
        select(
            NativeBotUsageDaily.user_id.label("user_id"),
            func.sum(
                case((NativeBotUsageDaily.day == today, NativeBotUsageDaily.count), else_=0)
            ).label("today_count"),
            func.sum(
                case(
                    (
                        NativeBotUsageDaily.day.between(month_start, today),
                        NativeBotUsageDaily.count,
                    ),
                    else_=0,
                )
            ).label("month_count"),
        )
        .group_by(NativeBotUsageDaily.user_id)
        .subquery()
    )
    query = (
        select(
            NativeBotUsageTotal.user_id,
            User.username,
            TelegramAccount.telegram_user_id,
            NativeBotUsageTotal.total,
            func.coalesce(daily.c.today_count, 0),
            func.coalesce(daily.c.month_count, 0),
        )
        .join(User, User.id == NativeBotUsageTotal.user_id)
        .outerjoin(TelegramAccount, TelegramAccount.user_id == User.id)
        .outerjoin(daily, daily.c.user_id == NativeBotUsageTotal.user_id)
    )
    if manager.role == "admin":
        managed_park_ids = select(UserPark.park_id).where(UserPark.user_id == manager.id)
        visible_user_ids = select(UserPark.user_id).where(UserPark.park_id.in_(managed_park_ids))
        query = query.where(NativeBotUsageTotal.user_id.in_(visible_user_ids))
    rows = db.execute(
        query.order_by(NativeBotUsageTotal.total.desc(), User.username, User.id)
    ).all()
    return [
        NativeUsageUserOut(
            user_id=user_id,
            username=username,
            telegram_user_id=telegram_user_id,
            stats=NativeUsageStatsOut(
                today=int(today_count),
                month=int(month_count),
                total=int(total),
            ),
        )
        for user_id, username, telegram_user_id, total, today_count, month_count in rows
    ]


def control(db: Session) -> NativeBotControlOut:
    row = db.scalar(
        select(NativeBotControl)
        .where(NativeBotControl.id == CONTROL_ID)
        .execution_options(populate_existing=True)
    )
    if row is None:
        return NativeBotControlOut(
            queries_paused=False,
            deliveries_paused=False,
            revision=1,
        )
    return NativeBotControlOut.model_validate(row, from_attributes=True)


def update_control(
    db: Session,
    user: User,
    *,
    queries_paused: bool,
    deliveries_paused: bool,
    revision: int,
) -> NativeBotControlOut:
    if (
        user.role != "royal"
        or not user.is_active
        or user.access_status != AccessStatus.approved.value
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    row = db.scalar(
        select(NativeBotControl)
        .where(NativeBotControl.id == CONTROL_ID)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        row = NativeBotControl(id=CONTROL_ID)
        db.add(row)
        db.flush()
    if row.revision != revision:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="revision_conflict")
    row.queries_paused = queries_paused
    row.deliveries_paused = deliveries_paused
    row.revision += 1
    db.commit()
    db.refresh(row)
    audit.record(
        db,
        action=audit.ACTION_NATIVE_BOT_CONTROL_UPDATED,
        actor=user,
        target_type="native_bot_control",
        target_id="native_telegram_control",
        detail=json.dumps(
            {
                "queries_paused": row.queries_paused,
                "deliveries_paused": row.deliveries_paused,
                "revision": row.revision,
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
    )
    return NativeBotControlOut.model_validate(row, from_attributes=True)


def require_queries_active(db: Session, user: User) -> None:
    if user.role in {"royal", "admin"}:
        return
    if control(db).queries_paused:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="native_queries_paused",
        )


def deliveries_paused(db: Session) -> bool:
    return control(db).deliveries_paused


def require_deliveries_active(db: Session) -> None:
    if deliveries_paused(db):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="native_deliveries_paused",
        )
