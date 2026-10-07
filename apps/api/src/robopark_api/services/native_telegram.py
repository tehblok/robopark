from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from robopark_api.models import (
    AccessStatus,
    NativeBotDelivery,
    NativeBotJob,
    Park,
    PlatformSetting,
    TelegramAccount,
    TelegramLinkAttempt,
    TelegramLinkCode,
    User,
    UserPark,
)
from robopark_api.native_telegram_schemas import (
    BotJobBase,
    BotJobOut,
    DeliveryOut,
    NativeBotHealthIn,
    NativeBotHealthOut,
    ParkBotOut,
)
from robopark_api.services import (
    audit,
    bot_settings,
    bot_tracker_gateway,
    native_telegram_reports,
    native_telegram_usage,
    rbac,
    tracker_client,
)

LINK_TTL = timedelta(minutes=10)
LINK_WINDOW = timedelta(minutes=10)
LINK_MAX_FAILURES = 5
LEASE_TTL = timedelta(minutes=5)
SCHEDULE_GRACE = timedelta(minutes=15)
RETENTION = timedelta(days=90)
CONTENT_LIMIT = 500
TERMINAL_STATES = frozenset({"sent", "failed", "unknown"})
HEALTH_KEY = "native_telegram_health"
HEALTH_STALE_AFTER = timedelta(seconds=120)


def utcnow() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def park_out(park: Park) -> ParkBotOut:
    return ParkBotOut(
        id=park.id,
        name=park.name,
        tag=park.tag,
        timezone=park.timezone,
        chat_id=park.chat_id,
        thread_id=park.thread_id,
        revision=park.bot_revision,
    )


def job_out(job: NativeBotJob) -> BotJobOut:
    return BotJobOut(
        id=job.id,
        park_id=job.park_id,
        kind=job.kind,
        title=job.title,
        enabled=job.enabled,
        schedule=job.schedule,
        timezone=job.timezone,
        time=job.time,
        run_at=_aware(job.run_at) if job.run_at is not None else None,
        weekdays=[int(value) for value in job.weekdays.split(",") if value],
        start_hour=job.start_hour,
        end_hour=job.end_hour,
        text=job.text,
        url=job.url,
        tracker_tag=job.tracker_tag,
        alternate=job.alternate,
        anchor_date=job.anchor_date,
        revision=job.revision,
    )


def delivery_out(row: NativeBotDelivery) -> DeliveryOut:
    return DeliveryOut(
        id=row.id,
        job_id=row.job_id,
        park_id=row.park_id,
        title=row.title,
        state=row.state,
        scheduled_at=row.scheduled_at,
        finished_at=row.finished_at,
        error_code=row.error_code,
        manual=row.manual,
        request_id=row.request_id,
    )


def record_health(db: Session, payload: NativeBotHealthIn) -> None:
    value = json.dumps(payload.model_dump(), separators=(",", ":"), sort_keys=True)
    row = db.get(PlatformSetting, HEALTH_KEY)
    if row is None:
        db.add(PlatformSetting(key=HEALTH_KEY, value=value))
    else:
        row.value = value
        row.updated_at = utcnow()
    db.commit()


def health(db: Session) -> NativeBotHealthOut:
    row = db.get(PlatformSetting, HEALTH_KEY)
    if row is None:
        return NativeBotHealthOut(
            state="unknown",
            telegram_ok=None,
            scheduler_ok=None,
            updated_at=None,
            last_error=None,
        )
    try:
        payload = NativeBotHealthIn.model_validate(json.loads(row.value))
    except (ValueError, TypeError, json.JSONDecodeError):
        return NativeBotHealthOut(
            state="unknown",
            telegram_ok=None,
            scheduler_ok=None,
            updated_at=row.updated_at,
            last_error="invalid_health_state",
        )
    updated_at = _aware(row.updated_at)
    if utcnow() - updated_at > HEALTH_STALE_AFTER:
        state = "offline"
        telegram_ok = False
        scheduler_ok = False
    elif payload.telegram_ok and payload.scheduler_ok:
        state = "ready"
        telegram_ok = payload.telegram_ok
        scheduler_ok = payload.scheduler_ok
    else:
        state = "degraded"
        telegram_ok = payload.telegram_ok
        scheduler_ok = payload.scheduler_ok
    return NativeBotHealthOut(
        state=state,
        telegram_ok=telegram_ok,
        scheduler_ok=scheduler_ok,
        updated_at=updated_at,
        last_error=payload.last_error,
    )


def manageable_parks(db: Session, user: User) -> list[Park]:
    if user.role == "royal":
        query = select(Park).where(Park.is_active.is_(True))
    elif user.role == "admin":
        query = (
            select(Park)
            .join(UserPark, UserPark.park_id == Park.id)
            .where(UserPark.user_id == user.id, Park.is_active.is_(True))
        )
    else:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return list(db.scalars(query.order_by(Park.id)).all())


def readable_parks(db: Session, user: User) -> list[Park]:
    if user.role == "royal":
        return list(db.scalars(select(Park).where(Park.is_active.is_(True)).order_by(Park.id)))
    return list(
        db.scalars(
            select(Park)
            .join(UserPark, UserPark.park_id == Park.id)
            .where(UserPark.user_id == user.id, Park.is_active.is_(True))
            .order_by(Park.id)
        )
    )


def require_manageable_park(db: Session, user: User, park_id: int) -> Park:
    if user.access_status != AccessStatus.approved.value or not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    if user.role == "royal":
        park = db.get(Park, park_id)
    elif user.role == "admin":
        park = db.scalar(
            select(Park)
            .join(UserPark, UserPark.park_id == Park.id)
            .where(Park.id == park_id, UserPark.user_id == user.id)
        )
    else:
        park = None
    if park is None or not park.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return park


def update_park(
    db: Session,
    user: User,
    park_id: int,
    *,
    chat_id: int | None,
    thread_id: int | None,
    revision: int,
) -> Park:
    require_manageable_park(db, user, park_id)
    park = db.scalar(
        select(Park)
        .where(Park.id == park_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert park is not None
    if park.bot_revision != revision:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="revision_conflict")
    park.chat_id = chat_id
    park.thread_id = thread_id
    park.bot_revision += 1
    db.commit()
    db.refresh(park)
    audit.record(
        db,
        action=audit.ACTION_NATIVE_BOT_PARK_UPDATED,
        actor=user,
        park_id=park.id,
        target_type="park",
        target_id=park.id,
    )
    return park


def _assign_job(job: NativeBotJob, payload: BotJobBase) -> None:
    for field in (
        "park_id",
        "kind",
        "title",
        "enabled",
        "schedule",
        "timezone",
        "time",
        "run_at",
        "start_hour",
        "end_hour",
        "text",
        "url",
        "tracker_tag",
        "alternate",
        "anchor_date",
    ):
        setattr(job, field, getattr(payload, field))
    job.weekdays = ",".join(str(day) for day in payload.weekdays)


def _validate_job_activation(payload: BotJobBase) -> None:
    if (
        payload.enabled
        and payload.schedule == "once"
        and payload.run_at is not None
        and _aware(payload.run_at) < utcnow() - SCHEDULE_GRACE
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="once_run_at_expired",
        )


def _ensure_once_delivery(db: Session, job: NativeBotJob) -> None:
    """Reserve an enabled one-shot delivery before its short send window."""
    if not job.enabled or job.schedule != "once" or job.run_at is None:
        return
    slot = _aware(job.run_at).astimezone(UTC)
    existing = db.scalar(
        select(NativeBotDelivery).where(
            NativeBotDelivery.job_id == job.id,
            NativeBotDelivery.scheduled_at == slot,
        )
    )
    if existing is not None:
        if (
            existing.state == "preparing"
            and existing.job_revision is None
            and existing.lease_token_hash is None
        ):
            existing.park_id = job.park_id
            existing.title = job.title
        return
    try:
        with db.begin_nested():
            db.add(
                NativeBotDelivery(
                    job_id=job.id,
                    park_id=job.park_id,
                    title=job.title,
                    state="preparing",
                    scheduled_at=slot,
                )
            )
            db.flush()
    except IntegrityError:
        pass


def create_job(db: Session, user: User, payload: BotJobBase) -> NativeBotJob:
    require_manageable_park(db, user, payload.park_id)
    _validate_job_activation(payload)
    row = NativeBotJob()
    _assign_job(row, payload)
    db.add(row)
    db.flush()
    _ensure_once_delivery(db, row)
    db.commit()
    db.refresh(row)
    audit.record(
        db,
        action=audit.ACTION_NATIVE_BOT_JOB_CREATED,
        actor=user,
        park_id=row.park_id,
        target_type="native_bot_job",
        target_id=row.id,
    )
    return row


def update_job(
    db: Session, user: User, job_id: str, payload: BotJobBase, revision: int
) -> NativeBotJob:
    row = db.scalar(
        select(NativeBotJob)
        .where(NativeBotJob.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    require_manageable_park(db, user, row.park_id)
    require_manageable_park(db, user, payload.park_id)
    if row.revision != revision:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="revision_conflict")
    _validate_job_activation(payload)
    _assign_job(row, payload)
    row.revision += 1
    db.flush()
    _ensure_once_delivery(db, row)
    db.commit()
    db.refresh(row)
    audit.record(
        db,
        action=audit.ACTION_NATIVE_BOT_JOB_UPDATED,
        actor=user,
        park_id=row.park_id,
        target_type="native_bot_job",
        target_id=row.id,
    )
    return row


def delete_job(db: Session, user: User, job_id: str, revision: int) -> None:
    row = db.scalar(
        select(NativeBotJob)
        .where(NativeBotJob.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    require_manageable_park(db, user, row.park_id)
    if row.revision != revision:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="revision_conflict")
    park_id = row.park_id
    db.delete(row)
    db.commit()
    audit.record(
        db,
        action=audit.ACTION_NATIVE_BOT_JOB_DELETED,
        actor=user,
        park_id=park_id,
        target_type="native_bot_job",
        target_id=job_id,
    )


def issue_link_code(db: Session, user: User) -> tuple[str, datetime]:
    now = utcnow()
    db.execute(delete(TelegramLinkCode).where(TelegramLinkCode.user_id == user.id))
    db.commit()
    for _ in range(8):
        code = f"{secrets.randbelow(100_000_000):08d}"
        if db.scalar(
            select(TelegramLinkCode.id).where(TelegramLinkCode.code_hash == _digest(code))
        ):
            continue
        expires = now + LINK_TTL
        db.add(TelegramLinkCode(user_id=user.id, code_hash=_digest(code), expires_at=expires))
        db.commit()
        audit.record(
            db,
            action=audit.ACTION_TELEGRAM_LINK_CODE_ISSUED,
            actor=user,
            target_type="user",
            target_id=user.id,
        )
        return code, expires
    db.rollback()
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="link_code_unavailable"
    )


def _record_failed_link(db: Session, telegram_user_id: int, now: datetime) -> None:
    attempt = db.get(TelegramLinkAttempt, telegram_user_id)
    if attempt is None or now - _aware(attempt.window_started_at) >= LINK_WINDOW:
        attempt = TelegramLinkAttempt(
            telegram_user_id=telegram_user_id,
            failure_count=1,
            window_started_at=now,
        )
        db.merge(attempt)
    else:
        attempt.failure_count += 1
    if attempt.failure_count >= LINK_MAX_FAILURES:
        attempt.locked_until = now + LINK_WINDOW
    db.commit()


def consume_link_code(db: Session, code: str, telegram_user_id: int) -> None:
    now = utcnow()
    db.execute(
        delete(TelegramLinkAttempt).where(TelegramLinkAttempt.window_started_at < now - RETENTION)
    )
    attempt = db.get(TelegramLinkAttempt, telegram_user_id)
    if (
        attempt is not None
        and attempt.locked_until is not None
        and _aware(attempt.locked_until) > now
    ):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="link_throttled")
    row = db.scalar(
        select(TelegramLinkCode)
        .where(TelegramLinkCode.code_hash == _digest(code))
        .with_for_update()
    )
    if row is None:
        _record_failed_link(db, telegram_user_id, now)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_link_code")
    if row.used_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="link_code_used")
    if _aware(row.expires_at) <= now:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="link_code_expired")
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    existing = db.scalar(
        select(TelegramAccount).where(TelegramAccount.telegram_user_id == telegram_user_id)
    )
    if existing is not None and existing.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="telegram_id_already_bound"
        )
    own = db.get(TelegramAccount, user.id)
    if own is not None and own.telegram_user_id != telegram_user_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="user_already_linked")
    row.used_at = now
    if own is None:
        db.add(TelegramAccount(user_id=user.id, telegram_user_id=telegram_user_id))
    if attempt is not None:
        db.delete(attempt)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="telegram_id_already_bound"
        ) from None
    audit.record(
        db,
        action=audit.ACTION_TELEGRAM_LINKED,
        actor=user,
        target_type="user",
        target_id=user.id,
    )


def linked_user(db: Session, telegram_user_id: int) -> User:
    user = linked_account_user(db, telegram_user_id)
    if user.access_status != AccessStatus.approved.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user


def linked_account_user(db: Session, telegram_user_id: int) -> User:
    user = db.scalar(
        select(User)
        .join(TelegramAccount, TelegramAccount.user_id == User.id)
        .where(TelegramAccount.telegram_user_id == telegram_user_id)
    )
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user


def _slot(job: NativeBotJob, park: Park, now: datetime) -> datetime | None:
    if job.schedule == "once":
        if job.run_at is None:
            return None
        slot = _aware(job.run_at).astimezone(UTC)
        if slot > now or now - slot > SCHEDULE_GRACE:
            return None
        if job.updated_at is not None and slot < _aware(job.updated_at):
            return None
        return slot
    try:
        local = now.astimezone(ZoneInfo(job.timezone or park.timezone))
    except ZoneInfoNotFoundError:
        return None
    weekdays = {int(value) for value in job.weekdays.split(",") if value}
    if local.weekday() not in weekdays:
        return None
    if job.alternate != "all":
        if job.anchor_date is None:
            return None
        parity = (local.date() - job.anchor_date).days % 2
        if (job.alternate == "odd" and parity != 1) or (job.alternate == "even" and parity != 0):
            return None
    if job.schedule == "daily":
        if job.time is None:
            return None
        hour, minute = (int(value) for value in job.time.split(":"))
        local_slot = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if local_slot > local:
            return None
    else:
        if job.start_hour is None or job.end_hour is None:
            return None
        if not job.start_hour <= local.hour <= job.end_hour:
            return None
        local_slot = local.replace(minute=0, second=0, microsecond=0)
    slot = local_slot.astimezone(UTC)
    if now - slot > SCHEDULE_GRACE:
        return None
    if job.updated_at is not None and slot < _aware(job.updated_at):
        return None
    return slot


def _valid_destination(
    job: NativeBotJob | None, park: Park | None, *, allow_disabled: bool = False
) -> bool:
    return bool(
        job
        and park
        and (job.enabled or allow_disabled)
        and park.is_active
        and park.chat_id is not None
    )


def enqueue_manual_run(
    db: Session,
    user: User,
    job_id: str,
    *,
    revision: int,
    request_id: str,
    allow_disabled: bool,
) -> tuple[NativeBotDelivery, bool]:
    existing = db.scalar(
        select(NativeBotDelivery).where(NativeBotDelivery.request_id == str(request_id))
    )
    if existing is not None:
        require_manageable_park(db, user, existing.park_id)
        if existing.job_id != job_id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="request_id_conflict")
        return existing, False
    job = db.scalar(
        select(NativeBotJob)
        .where(NativeBotJob.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    park = require_manageable_park(db, user, job.park_id)
    if job.revision != revision:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="revision_conflict")
    if not job.enabled and not allow_disabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="job_disabled")
    candidate = job_out(job).model_dump(exclude={"id", "revision"})
    candidate["enabled"] = True
    if candidate["schedule"] == "once":
        candidate["run_at"] = utcnow()
    try:
        BotJobBase.model_validate(candidate)
    except ValidationError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="job_invalid") from None
    if not _valid_destination(job, park, allow_disabled=allow_disabled):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="delivery_not_ready")
    now = utcnow()
    row = NativeBotDelivery(
        job_id=job.id,
        park_id=park.id,
        title=job.title,
        state="preparing",
        scheduled_at=now,
        manual=True,
        request_id=str(request_id),
        job_revision=job.revision,
        park_revision=park.bot_revision,
        destination_chat_id=park.chat_id,
        destination_thread_id=park.thread_id,
        park_tag=park.tag,
        tracker_queue=park.tracker_queue,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        repeated = db.scalar(
            select(NativeBotDelivery).where(NativeBotDelivery.request_id == str(request_id))
        )
        if repeated is not None:
            require_manageable_park(db, user, repeated.park_id)
            if repeated.job_id == job_id:
                return repeated, False
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="request_id_conflict"
        ) from None
    db.refresh(row)
    audit.record(
        db,
        action=audit.ACTION_NATIVE_BOT_JOB_RUN_QUEUED,
        actor=user,
        park_id=park.id,
        target_type="native_bot_delivery",
        target_id=row.id,
    )
    return row, True


def _globally_enabled(host_data_path: str | None) -> bool:
    if host_data_path is None:
        return True
    try:
        return bot_settings.desired_enabled(host_data_path)
    except (bot_settings.BotControlUnavailable, bot_settings.BotControlStateInvalid) as exc:
        raise HTTPException(status_code=503, detail="bot_control_unavailable") from exc


def _fail_delivery(row: NativeBotDelivery, now: datetime, error_code: str) -> None:
    row.state = "failed"
    row.error_code = error_code
    row.finished_at = now
    row.lease_token_hash = None
    row.lease_until = None


def claim(db: Session, limit: int, *, host_data_path: str | None = None) -> list[dict]:
    now = utcnow()
    if not _globally_enabled(host_data_path):
        return []
    paused = native_telegram_usage.deliveries_paused(db)
    db.execute(
        delete(NativeBotDelivery).where(
            NativeBotDelivery.state.in_(TERMINAL_STATES),
            NativeBotDelivery.finished_at < now - RETENTION,
        )
    )
    stale_sending = list(
        db.scalars(
            select(NativeBotDelivery).where(
                NativeBotDelivery.state == "sending",
                NativeBotDelivery.lease_until < now,
            )
        )
    )
    for row in stale_sending:
        row.state = "unknown"
        row.error_code = "sending_lease_expired"
        row.finished_at = now
        row.lease_token_hash = None
        row.lease_until = None

    stale_preparing = list(
        db.scalars(
            select(NativeBotDelivery)
            .where(
                NativeBotDelivery.state == "preparing",
                NativeBotDelivery.scheduled_at < now - SCHEDULE_GRACE,
                (NativeBotDelivery.lease_until.is_(None) | (NativeBotDelivery.lease_until < now)),
            )
            .limit(1000)
        )
    )
    for row in stale_preparing:
        job = db.get(NativeBotJob, row.job_id)
        if row.manual or (job is not None and job.schedule == "once"):
            continue
        _fail_delivery(row, now, "schedule_expired")

    pending = list(
        db.scalars(
            select(NativeBotDelivery)
            .where(
                NativeBotDelivery.state == "preparing",
                (NativeBotDelivery.lease_until.is_(None) | (NativeBotDelivery.lease_until < now)),
            )
            .order_by(NativeBotDelivery.scheduled_at, NativeBotDelivery.id)
            .limit(1000)
        )
    )
    for row in pending:
        job = db.get(NativeBotJob, row.job_id)
        park = db.get(Park, row.park_id)
        if not _valid_destination(job, park, allow_disabled=row.manual):
            _fail_delivery(row, now, "delivery_not_ready")
            continue
        if (
            not row.manual
            and job.schedule == "once"
            and job.run_at is not None
            and _aware(row.scheduled_at) != _aware(job.run_at).astimezone(UTC)
        ):
            _fail_delivery(row, now, "configuration_changed")
            continue
        if row.job_revision is not None and (
            row.job_revision != job.revision
            or row.park_revision != park.bot_revision
            or row.destination_chat_id != park.chat_id
            or row.destination_thread_id != park.thread_id
            or row.park_tag != park.tag
            or row.tracker_queue != park.tracker_queue
        ):
            _fail_delivery(row, now, "configuration_changed")

    jobs = db.scalars(
        select(NativeBotJob).where(NativeBotJob.enabled.is_(True)).order_by(NativeBotJob.id)
    ).all()
    for job in jobs:
        park = db.get(Park, job.park_id)
        if not _valid_destination(job, park):
            continue
        if job.schedule == "once":
            _ensure_once_delivery(db, job)
            continue
        if paused:
            continue
        slot = _slot(job, park, now)
        if slot is None:
            continue
        exists = db.scalar(
            select(NativeBotDelivery.id).where(
                NativeBotDelivery.job_id == job.id,
                NativeBotDelivery.scheduled_at == slot,
            )
        )
        if exists is None:
            try:
                with db.begin_nested():
                    db.add(
                        NativeBotDelivery(
                            job_id=job.id,
                            park_id=job.park_id,
                            title=job.title,
                            state="preparing",
                            scheduled_at=slot,
                        )
                    )
                    db.flush()
            except IntegrityError:
                pass
    db.commit()

    if paused:
        return []

    rows = list(
        db.scalars(
            select(NativeBotDelivery)
            .join(NativeBotJob, NativeBotJob.id == NativeBotDelivery.job_id)
            .join(Park, Park.id == NativeBotDelivery.park_id)
            .where(
                NativeBotDelivery.state == "preparing",
                NativeBotDelivery.scheduled_at <= now,
                or_(
                    NativeBotDelivery.scheduled_at >= now - SCHEDULE_GRACE,
                    NativeBotDelivery.manual.is_(True),
                    NativeBotJob.schedule == "once",
                ),
                (NativeBotDelivery.lease_until.is_(None) | (NativeBotDelivery.lease_until < now)),
                or_(NativeBotJob.enabled.is_(True), NativeBotDelivery.manual.is_(True)),
                Park.is_active.is_(True),
                Park.chat_id.is_not(None),
            )
            .order_by(NativeBotDelivery.scheduled_at, NativeBotDelivery.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    claimed: list[dict] = []
    for row in rows:
        job = db.get(NativeBotJob, row.job_id)
        park = db.get(Park, row.park_id)
        if not _valid_destination(job, park, allow_disabled=row.manual):
            _fail_delivery(row, now, "delivery_not_ready")
            continue
        token = secrets.token_urlsafe(32)
        row.lease_token_hash = _digest(token)
        row.lease_until = now + LEASE_TTL
        row.job_revision = job.revision
        row.park_revision = park.bot_revision
        row.destination_chat_id = park.chat_id
        row.destination_thread_id = park.thread_id
        row.park_tag = park.tag
        row.tracker_queue = park.tracker_queue
        claimed.append(
            {"id": row.id, "lease_token": token, "job": job_out(job), "park": park_out(park)}
        )
    db.commit()
    return claimed


def _leased_row(db: Session, delivery_id: int, token: str) -> NativeBotDelivery:
    row = db.scalar(
        select(NativeBotDelivery).where(NativeBotDelivery.id == delivery_id).with_for_update()
    )
    now = utcnow()
    if (
        row is None
        or row.state not in {"preparing", "sending"}
        or row.lease_token_hash is None
        or not hmac.compare_digest(row.lease_token_hash, _digest(token))
        or row.lease_until is None
        or _aware(row.lease_until) <= now
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="delivery_lease_invalid")
    return row


def require_lease(db: Session, delivery_id: int, token: str, *, preparing: bool) -> tuple:
    row = _leased_row(db, delivery_id, token)
    expected_state = "preparing" if preparing else "sending"
    if row.state != expected_state:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="delivery_lease_invalid")
    job = db.get(NativeBotJob, row.job_id)
    park = db.get(Park, row.park_id)
    if not _valid_destination(job, park, allow_disabled=row.manual) or (
        row.job_revision != job.revision
        or row.park_revision != park.bot_revision
        or row.destination_chat_id != park.chat_id
        or row.destination_thread_id != park.thread_id
        or row.park_tag != park.tag
        or row.tracker_queue != park.tracker_queue
    ):
        _fail_delivery(row, utcnow(), "configuration_changed")
        db.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="delivery_not_ready")
    return row, job, park


def begin(db: Session, delivery_id: int, token: str, *, host_data_path: str | None = None) -> None:
    native_telegram_usage.require_deliveries_active(db)
    if not _globally_enabled(host_data_path):
        row = _leased_row(db, delivery_id, token)
        if row.state == "preparing":
            _fail_delivery(row, utcnow(), "bot_disabled")
            db.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="delivery_not_ready")
    row, _job, _park = require_lease(db, delivery_id, token, preparing=True)
    row.state = "sending"
    row.lease_until = utcnow() + LEASE_TTL
    db.commit()


def finish(db: Session, delivery_id: int, token: str, state: str, error_code: str | None) -> None:
    row = _leased_row(db, delivery_id, token)
    if row.state == "preparing" and state != "failed":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="delivery_not_begun")
    if row.state not in {"preparing", "sending"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="delivery_lease_invalid")
    row.state = state
    row.error_code = error_code
    row.finished_at = utcnow()
    row.lease_token_hash = None
    row.lease_until = None
    db.commit()


def delivery_content(db: Session, delivery_id: int, token: str) -> dict:
    _row, job, park = require_lease(db, delivery_id, token, preparing=True)
    if job.kind not in {"report", "campaign"}:
        return {"issues": [], "truncated": False}
    queue = (park.tracker_queue or "").strip()
    if not queue:
        raise HTTPException(status_code=409, detail="tracker_queue_not_configured")
    if job.kind == "report":
        query = tracker_client.join_query(
            f"Queue: {tracker_client.ql_token(queue)}",
            "Type: repair, service, calibration",
            "Priority: blocker",
            tracker_client.open_issues_clause(),
            f"Tags: {tracker_client.ql_token(park.tag)}",
            tracker_client.exclude_tag("donor"),
        )
    else:
        query = tracker_client.join_query(
            f"Queue: {tracker_client.ql_token(queue)}",
            f"Tags: {tracker_client.ql_token(park.tag)}",
            f"Tags: {tracker_client.ql_token(job.tracker_tag or '')}",
        )
    try:
        token_value = bot_tracker_gateway.tracker_token(db)
        issues = bot_tracker_gateway.search(
            token=token_value,
            query=query,
            order=["+created"],
            per_page=50,
            max_pages=10,
            allowed_queues=(queue,),
        )
    except bot_tracker_gateway.TrackerNotConfigured as exc:
        raise HTTPException(status_code=503, detail="tracker_not_configured") from exc
    except (tracker_client.TrackerError, bot_tracker_gateway.TrackerResponseTooLarge) as exc:
        raise HTTPException(status_code=502, detail="tracker_upstream_error") from exc
    truncated = len(issues) >= CONTENT_LIMIT
    bounded = issues[:CONTENT_LIMIT]
    if job.kind == "report":
        native_telegram_reports.prepare_history(db, token=token_value, park=park, issues=bounded)
        report = native_telegram_reports.enrich(db, bounded, timezone=park.timezone)
        return {
            "issues": report["issues"],
            "truncated": truncated,
            "report_summary": report["summary"],
        }
    return {"issues": bounded, "truncated": truncated}


def normalize_robot(raw: str) -> str | None:
    text = raw.strip().upper()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    text = text[6:] if text.startswith("YASADR") else text.removeprefix("A")
    if not text or not text.isdecimal():
        return None
    return text.lstrip("0") or "0"


def _exact_park_issue(issue: dict, park: Park, normalized: str) -> bool:
    tags = {str(tag) for tag in issue.get("tags") or []}
    summary_robot = normalize_robot(
        tracker_client.parse_robot_from_summary(str(issue.get("summary") or "")) or ""
    )
    rover_robot = normalize_robot(str(issue.get("rover") or ""))
    return park.tag in tags and normalized in {summary_robot, rover_robot}


def robot_issues(
    db: Session,
    user: User,
    robot: str,
    view: str,
    *,
    auxiliary_queues: tuple[str, ...] = (),
) -> dict:
    if not (
        rbac.has_permission(db, user, rbac.PERMISSION_TRACKER_READ)
        or rbac.has_permission(db, user, rbac.PERMISSION_TRACKER_WRITE)
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    normalized = normalize_robot(robot)
    if normalized is None:
        raise HTTPException(status_code=422, detail="invalid_robot_number")
    parks = readable_parks(db, user)
    queues = tuple(sorted({park.tracker_queue for park in parks if park.tracker_queue}))
    if not queues:
        return {"robot": normalized, "issues": [], "truncated": False}
    try:
        token = bot_tracker_gateway.tracker_token(db)
    except bot_tracker_gateway.TrackerNotConfigured as exc:
        raise HTTPException(status_code=503, detail="tracker_not_configured") from exc
    collected: list[dict] = []
    seen: set[str] = set()
    auxiliary_view = view in {"moves", "moves_history", "parts"}
    anchored = False
    for park in parks:
        queue = (park.tracker_queue or "").strip()
        if not queue:
            continue
        state_clause = ""
        if not auxiliary_view:
            state_clause = (
                tracker_client.open_issues_clause()
                if view == "open"
                else "Resolution: fixed Type: repair, service, calibration"
            )
        query = tracker_client.join_query(
            f"Queue: {tracker_client.ql_token(queue)}",
            f"Tags: {tracker_client.ql_token(park.tag)}",
            (
                f"({tracker_client.robot_summary_clause(normalized)} "
                f"OR rover: {tracker_client.ql_quote(normalized)} "
                f"OR rover: {tracker_client.ql_quote(f'a{normalized}')})"
            ),
            state_clause,
        )
        try:
            issues = bot_tracker_gateway.search(
                token=token,
                query=query,
                order=["-created"],
                per_page=50,
                max_pages=10,
                allowed_queues=queues,
            )
        except (tracker_client.TrackerError, bot_tracker_gateway.TrackerResponseTooLarge) as exc:
            raise HTTPException(status_code=502, detail="tracker_upstream_error") from exc
        for issue in issues:
            key = str(issue.get("key") or "")
            if not _exact_park_issue(issue, park, normalized) or not key:
                continue
            anchored = True
            if auxiliary_view:
                break
            if key in seen:
                continue
            seen.add(key)
            collected.append(issue)
            if len(collected) >= CONTENT_LIMIT:
                return {"robot": normalized, "issues": collected, "truncated": True}
        if auxiliary_view and anchored:
            break
    if not auxiliary_view:
        return {"robot": normalized, "issues": collected, "truncated": False}
    if not anchored:
        return {"robot": normalized, "issues": [], "truncated": False}

    queue = "SDCWH" if view == "parts" else "ROBOMAINT"
    if queue not in auxiliary_queues:
        raise HTTPException(status_code=409, detail="native_auxiliary_queue_disabled")
    rover_clause = (
        f"(rover: {tracker_client.ql_quote(normalized)} "
        f"OR rover: {tracker_client.ql_quote(f'a{normalized}')})"
    )
    if view == "parts":
        state_clause = "Status: delieveryWaiting Resolution: empty()"
        order = ["+created"]
    elif view == "moves":
        state_clause = "Status: inProgress, new, needEstimate, needInfo"
        order = ["-updated"]
    else:
        state_clause = "Resolution: !empty()"
        order = ["-updated"]
    query = tracker_client.join_query(
        f"Queue: {tracker_client.ql_token(queue)}", rover_clause, state_clause
    )
    try:
        issues = bot_tracker_gateway.search(
            token=token,
            query=query,
            order=order,
            per_page=50,
            max_pages=10,
            allowed_queues=(queue,),
        )
    except (tracker_client.TrackerError, bot_tracker_gateway.TrackerResponseTooLarge) as exc:
        raise HTTPException(status_code=502, detail="tracker_upstream_error") from exc
    for issue in issues:
        if normalize_robot(str(issue.get("rover") or "")) != normalized:
            continue
        key = str(issue.get("key") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        collected.append(issue)
        if len(collected) >= CONTENT_LIMIT:
            return {"robot": normalized, "issues": collected, "truncated": True}
    return {"robot": normalized, "issues": collected, "truncated": False}
