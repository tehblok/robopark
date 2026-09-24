"""Worker-owned, lease-protected notification channel delivery."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pywebpush import WebPushException, webpush
from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from robopark_api.config import get_settings
from robopark_api.crypto import decrypt_secret
from robopark_api.notification_delivery_models import NotificationDelivery
from robopark_api.schedule_models import NotificationEvent, PushSubscription

logger = logging.getLogger(__name__)
LEASE_SECONDS = 60
RETRY_SECONDS = 60


class LeaseLost(RuntimeError):
    """A delivery may no longer call its external provider."""


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _claim(db: Session, row_id: str, owner_id: str, now: datetime) -> bool:
    result = db.execute(
        update(NotificationDelivery)
        .where(
            NotificationDelivery.id == row_id,
            NotificationDelivery.state == "pending",
            NotificationDelivery.next_attempt_at <= now,
            NotificationDelivery.expires_at > now,
            or_(
                NotificationDelivery.lease_until.is_(None), NotificationDelivery.lease_until <= now
            ),
        )
        .values(lease_owner=owner_id, lease_until=now + timedelta(seconds=LEASE_SECONDS))
    )
    db.commit()
    return bool(result.rowcount)


def _send_web_push(
    db: Session,
    row: NotificationDelivery,
    event: NotificationEvent,
    send: Callable[..., object],
) -> str:
    subscription = db.scalar(
        select(PushSubscription).where(
            PushSubscription.endpoint_hash == row.endpoint_hash,
            PushSubscription.user_id == event.user_id,
        )
    )
    if subscription is None:
        return "cancelled"
    settings = get_settings()
    if not settings.secret_key:
        raise RuntimeError("push_secret_key_missing")
    from robopark_api.routers.push import _vapid_key_pair

    private_key, _ = _vapid_key_pair(settings.secret_key)
    send(
        subscription_info={
            "endpoint": decrypt_secret(subscription.endpoint_encrypted, settings.secret_key),
            "keys": {
                "p256dh": decrypt_secret(subscription.p256dh_encrypted, settings.secret_key),
                "auth": decrypt_secret(subscription.auth_encrypted, settings.secret_key),
            },
        },
        data=json.dumps({"event_id": event.id.split("-", 1)[0]}),
        vapid_private_key=private_key,
        vapid_claims={"sub": "mailto:robopark@localhost"},
        timeout=min(5.0, settings.push_delivery_deadline_seconds),
    )
    return "delivered"


def _deliver_in_app(
    _db: Session,
    _row: NotificationDelivery,
    _event: NotificationEvent,
    _send: Callable[..., object],
) -> str:
    # The inbox row was stored in the same transaction as this delivery row.
    return "delivered"


CHANNEL_ADAPTERS = {"in_app": _deliver_in_app, "web_push": _send_web_push}


def process_due(
    session_factory: Callable[[], Session],
    *,
    owner_id: str,
    now: datetime | None = None,
    limit: int | None = None,
    send_web_push: Callable[..., object] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> int:
    """Claim and attempt due rows; a dead worker's lease can be replayed."""
    read_clock = clock or (lambda: now or datetime.now(UTC))
    moment = _utc(read_clock())
    batch_size = limit or get_settings().push_delivery_batch_size
    with session_factory() as db:
        db.execute(
            update(NotificationDelivery)
            .where(
                NotificationDelivery.state == "pending",
                NotificationDelivery.expires_at <= moment,
                or_(
                    NotificationDelivery.lease_until.is_(None),
                    NotificationDelivery.lease_until <= moment,
                ),
            )
            .values(state="expired", lease_owner=None, lease_until=None)
        )
        db.commit()
        ids = list(
            db.scalars(
                select(NotificationDelivery.id)
                .where(
                    NotificationDelivery.state == "pending",
                    NotificationDelivery.next_attempt_at <= moment,
                    NotificationDelivery.expires_at > moment,
                    or_(
                        NotificationDelivery.lease_until.is_(None),
                        NotificationDelivery.lease_until <= moment,
                    ),
                )
                .order_by(NotificationDelivery.next_attempt_at, NotificationDelivery.id)
                .limit(batch_size)
            )
        )
    processed = 0
    for row_id in ids:
        claim_at = _utc(read_clock())
        with session_factory() as db:
            if not _claim(db, row_id, owner_id, claim_at):
                continue
        processed += 1
        outcome = "pending"
        gone = False

        def send_if_owned(*, row_id=row_id, **payload):
            with session_factory() as lease_db:
                owns_lease = lease_db.scalar(
                    select(NotificationDelivery.id).where(
                        NotificationDelivery.id == row_id,
                        NotificationDelivery.lease_owner == owner_id,
                        NotificationDelivery.lease_until > _utc(read_clock()),
                    )
                )
            if owns_lease is None:
                raise LeaseLost
            return (send_web_push or webpush)(**payload)

        try:
            with session_factory() as db:
                before_send = _utc(read_clock())
                row = db.scalar(
                    select(NotificationDelivery).where(
                        NotificationDelivery.id == row_id,
                        NotificationDelivery.lease_owner == owner_id,
                        NotificationDelivery.lease_until > before_send,
                    )
                )
                if row is None:
                    continue
                event = db.get(NotificationEvent, row.event_id) if row is not None else None
                if row is None or event is None:
                    outcome = "cancelled"
                else:
                    adapter = CHANNEL_ADAPTERS.get(row.channel)
                    if adapter is None:
                        raise ValueError("unsupported_notification_channel")
                    outcome = adapter(db, row, event, send_if_owned)
        except LeaseLost:
            continue
        except WebPushException as exc:
            response = getattr(exc, "response", None)
            if response is not None and response.status_code in {404, 410}:
                outcome, gone = "cancelled", True
            else:
                logger.warning("Web Push transient failure", exc_info=exc)
        except Exception:
            logger.exception("Notification delivery attempt failed")
        with session_factory() as db:
            row = db.scalar(
                select(NotificationDelivery).where(
                    NotificationDelivery.id == row_id,
                    NotificationDelivery.lease_owner == owner_id,
                )
            )
            if row is None:
                continue
            row.attempts += 1
            row.lease_owner = None
            row.lease_until = None
            if gone:
                db.execute(
                    delete(PushSubscription).where(
                        PushSubscription.endpoint_hash == row.endpoint_hash
                    )
                )
            if outcome == "pending":
                retry_at = _utc(read_clock()) + timedelta(
                    seconds=min(3600, RETRY_SECONDS * 2 ** min(row.attempts - 1, 6))
                )
                if retry_at >= _utc(row.expires_at):
                    row.state = "expired"
                else:
                    row.next_attempt_at = retry_at
            else:
                row.state = outcome
            db.commit()
    return processed


async def run_notification_delivery_loop(
    session_factory: Callable[[], Session],
    stop: asyncio.Event,
    *,
    owner_id: str,
) -> None:
    """Drain persisted deliveries under WorkerRuntime until cooperative shutdown."""
    while not stop.is_set():
        try:
            processed = await asyncio.to_thread(process_due, session_factory, owner_id=owner_id)
        except Exception:
            logger.exception("Notification delivery poll failed")
            processed = 0
        if processed:
            continue
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=1.0)
