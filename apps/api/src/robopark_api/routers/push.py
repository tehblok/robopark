import base64
import hashlib
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from robopark_api.config import get_settings
from robopark_api.crypto import encrypt_secret
from robopark_api.db import get_db
from robopark_api.deps import require_user
from robopark_api.models import User
from robopark_api.notification_delivery_models import NotificationDelivery
from robopark_api.schedule_models import (
    NotificationEvent,
    NotificationPreference,
    PushSubscription,
    SystemIncidentOccurrence,
)
from robopark_api.schedule_schemas import PushPreferenceIn, PushSubscriptionIn
from robopark_api.services.schedules import RoutingEvent, eligible_recipients

router = APIRouter(prefix="/push", tags=["push"])
logger = logging.getLogger(__name__)

_P256_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551


def prune_expired_subscriptions(db: Session, *, now: datetime | None = None) -> int:
    result = db.execute(
        delete(PushSubscription).where(
            PushSubscription.expires_at.is_not(None),
            PushSubscription.expires_at <= (now or datetime.now(UTC)),
        )
    )
    db.commit()
    return int(result.rowcount or 0)


def remove_rejected_subscription(db: Session, endpoint_hash: str) -> None:
    db.execute(delete(PushSubscription).where(PushSubscription.endpoint_hash == endpoint_hash))
    db.commit()


def prune_notification_data(
    db: Session, *, now: datetime | None = None, read_days: int = 30, unread_days: int = 90
) -> dict[str, int]:
    current = now or datetime.now(UTC)
    subscriptions = prune_expired_subscriptions(db, now=current)
    result = db.execute(
        delete(NotificationEvent).where(
            or_(
                NotificationEvent.read_at <= current - timedelta(days=read_days),
                (NotificationEvent.read_at.is_(None))
                & (NotificationEvent.created_at <= current - timedelta(days=unread_days)),
            )
        )
    )
    db.commit()
    return {"subscriptions": subscriptions, "notifications": int(result.rowcount or 0)}


class PushService:
    def __init__(self, session_factory: Callable[[], Session]):
        self._session_factory = session_factory

    def close(self) -> None:
        """Delivery is owned by WorkerRuntime and has no request-local resources."""

    def emit(
        self,
        *,
        event_type: str,
        park_id: int | None,
        protected_text: str,
        target_user_ids: set[int] | None = None,
        event_key: str | None = None,
        deliver: bool = True,
    ) -> dict:
        with self._session_factory() as db:
            result = self.emit_in_transaction(
                db,
                event_type=event_type,
                park_id=park_id,
                protected_text=protected_text,
                target_user_ids=target_user_ids,
                event_key=event_key,
                deliver=deliver,
            )
            db.commit()
            return result

    def emit_in_transaction(
        self,
        db: Session,
        *,
        event_type: str,
        park_id: int | None,
        protected_text: str,
        target_user_ids: set[int] | None = None,
        event_key: str | None = None,
        deliver: bool = True,
    ) -> dict:
        """Stage inbox and delivery rows in the caller's domain transaction."""
        now = datetime.now(UTC)
        users = eligible_recipients(RoutingEvent(db, event_type, park_id, target_user_ids), now)
        user_ids = {user.id for user in users}
        preferences = {
            item.user_id: item
            for item in db.scalars(
                select(NotificationPreference).where(NotificationPreference.user_id.in_(user_ids))
            )
        }
        recipients: list[int] = []
        internal_recipients: list[int] = []
        stable_event_key = event_key
        if event_key is not None and event_key.startswith("system:"):
            occurrence = db.scalar(
                select(SystemIncidentOccurrence).where(
                    SystemIncidentOccurrence.incident_key == event_key,
                    SystemIncidentOccurrence.resolved_at.is_(None),
                )
            )
            if occurrence is None:
                occurrence = SystemIncidentOccurrence(
                    incident_key=event_key,
                    event_type=event_type,
                    started_at=now,
                    last_seen_at=now,
                )
                try:
                    with db.begin_nested():
                        db.add(occurrence)
                        db.flush()
                except IntegrityError:
                    occurrence = db.scalar(
                        select(SystemIncidentOccurrence).where(
                            SystemIncidentOccurrence.incident_key == event_key,
                            SystemIncidentOccurrence.resolved_at.is_(None),
                        )
                    )
            if occurrence is not None:
                occurrence.last_seen_at = now
                stable_event_key = f"{event_key}:{occurrence.id}"
        event_id = hashlib.sha256(
            (stable_event_key or f"{event_type}:{now.timestamp()}:{protected_text}").encode()
        ).hexdigest()[:24]
        subscriptions = {}
        if deliver and user_ids:
            for row in db.scalars(
                select(PushSubscription)
                .where(PushSubscription.user_id.in_(user_ids))
                .order_by(PushSubscription.created_at, PushSubscription.id)
            ):
                subscriptions.setdefault(row.user_id, []).append(row)
        for user in users:
            notification_id = f"{event_id}-{user.id}"
            if db.get(NotificationEvent, notification_id) is not None:
                continue
            db.add(
                NotificationEvent(
                    id=notification_id,
                    user_id=user.id,
                    event_type=event_type,
                    park_id=park_id,
                    protected_text=protected_text,
                )
            )
            db.add(
                NotificationDelivery(
                    event_id=notification_id,
                    channel="in_app",
                    state="delivered",
                    next_attempt_at=now,
                    expires_at=now + timedelta(hours=24),
                    idempotency_key=f"{notification_id}:in_app",
                )
            )
            internal_recipients.append(user.id)
            preference = preferences.get(user.id)
            enabled = preference is None or (
                preference.system_enabled
                and event_type in set(json.loads(preference.categories_json))
            )
            if enabled:
                recipients.append(user.id)
                if deliver:
                    for subscription in subscriptions.get(user.id, []):
                        db.add(
                            NotificationDelivery(
                                event_id=notification_id,
                                channel="web_push",
                                state="pending",
                                next_attempt_at=now,
                                expires_at=now + timedelta(hours=24),
                                idempotency_key=f"{notification_id}:web_push:{subscription.endpoint_hash}",
                                endpoint_hash=subscription.endpoint_hash,
                            )
                        )
        return {
            "event_id": event_id,
            "recipient_ids": recipients,
            "internal_recipient_ids": internal_recipients,
            "push_payload": {"event_id": event_id},
        }

    def emit_for_tests(self, **kwargs) -> dict:
        return self.emit(**kwargs, deliver=False)

    def sync_system_incidents(self, active_keys: set[str]) -> None:
        """Refresh active health occurrences and resolve checks that recovered."""
        now = datetime.now(UTC)
        with self._session_factory() as db:
            occurrences = list(
                db.scalars(
                    select(SystemIncidentOccurrence).where(
                        SystemIncidentOccurrence.resolved_at.is_(None)
                    )
                )
            )
            for occurrence in occurrences:
                if occurrence.incident_key in active_keys:
                    occurrence.last_seen_at = now
                else:
                    occurrence.resolved_at = now
            db.commit()


def _vapid_key_pair(secret_key: str) -> tuple[str, str]:
    scalar = int.from_bytes(hashlib.sha256(f"vapid:{secret_key}".encode()).digest(), "big")
    scalar = (scalar % (_P256_ORDER - 1)) + 1
    private_key = ec.derive_private_key(scalar, ec.SECP256R1())
    private_encoded = (
        base64.urlsafe_b64encode(scalar.to_bytes(32, "big")).decode("ascii").rstrip("=")
    )
    public_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    public_key = base64.urlsafe_b64encode(public_bytes).decode("ascii").rstrip("=")
    return private_encoded, public_key


@router.get("/config")
def push_config(user: User = Depends(require_user)):
    del user
    settings = get_settings()
    if not settings.secret_key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "push_not_configured")
    _, public_key = _vapid_key_pair(settings.secret_key)
    return {"public_key": public_key}


@router.post("/subscriptions", status_code=status.HTTP_201_CREATED)
def subscribe(
    payload: PushSubscriptionIn, user: User = Depends(require_user), db: Session = Depends(get_db)
):
    settings = get_settings()
    endpoint_hash = hashlib.sha256(payload.endpoint.encode()).hexdigest()
    row = db.scalar(select(PushSubscription).where(PushSubscription.endpoint_hash == endpoint_hash))
    values = {
        "user_id": user.id,
        "endpoint_hash": endpoint_hash,
        "endpoint_encrypted": encrypt_secret(payload.endpoint, settings.secret_key),
        "p256dh_encrypted": encrypt_secret(payload.p256dh, settings.secret_key),
        "auth_encrypted": encrypt_secret(payload.auth, settings.secret_key),
        "expires_at": payload.expires_at,
    }
    if row is None:
        row = PushSubscription(**values)
        db.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "endpoint_hash": row.endpoint_hash, "expires_at": row.expires_at}


@router.delete("/subscriptions/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
def unsubscribe(
    subscription_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)
):
    row = db.get(PushSubscription, subscription_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "subscription_not_found")
    db.delete(row)
    db.commit()


@router.put("/preferences")
def preferences(
    payload: PushPreferenceIn, user: User = Depends(require_user), db: Session = Depends(get_db)
):
    row = db.get(NotificationPreference, user.id)
    if row is None:
        row = NotificationPreference(user_id=user.id)
        db.add(row)
    row.categories_json = json.dumps(sorted(set(payload.categories)))
    row.system_enabled = payload.system_enabled
    db.commit()
    return {"categories": json.loads(row.categories_json), "system_enabled": row.system_enabled}


@router.get("/inbox")
def inbox(user: User = Depends(require_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(NotificationEvent)
        .where(NotificationEvent.user_id == user.id)
        .order_by(NotificationEvent.created_at.desc(), NotificationEvent.id.desc())
        .limit(200)
    )
    return [
        {
            "id": row.id,
            "event_type": row.event_type,
            "park_id": row.park_id,
            "protected_text": row.protected_text,
            "read_at": row.read_at,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.post("/inbox/{event_id}/read")
def mark_read(event_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    row = db.get(NotificationEvent, event_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "notification_not_found")
    row.read_at = datetime.now(UTC)
    db.commit()
    return {"ok": True}
