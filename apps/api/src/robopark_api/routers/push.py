import base64
import hashlib
import json
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import APIRouter, Depends, HTTPException, status
from pywebpush import WebPushException, webpush
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from robopark_api.config import get_settings
from robopark_api.crypto import decrypt_secret, encrypt_secret
from robopark_api.db import get_db
from robopark_api.deps import require_user
from robopark_api.models import Role, User, UserPark
from robopark_api.schedule_models import (
    NotificationEvent,
    NotificationPreference,
    PushSubscription,
    ScheduleEntry,
    SystemIncidentOccurrence,
)
from robopark_api.schedule_schemas import PushPreferenceIn, PushSubscriptionIn

router = APIRouter(prefix="/push", tags=["push"])
logger = logging.getLogger(__name__)

_P256_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551

EVENT_ROLES = {
    "new_task": ({"mechanic", "driver"}, True),
    "return": ({"mechanic", "driver"}, True),
    "operator_comment": ({"mechanic", "driver"}, True),
    "report": ({"operator", "admin", "royal"}, False),
    "review_task": ({"operator"}, False),
    "problem": ({"operator", "admin", "royal"}, False),
    "anomaly": ({"operator", "admin", "royal"}, False),
    "integration_down": ({"royal"}, False),
    "disk_low": ({"royal"}, False),
    "update_failure": ({"royal"}, False),
    "server_problem": ({"royal"}, False),
}


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
            roles, on_shift = EVENT_ROLES.get(event_type, (set(), False))
            if not roles:
                raise ValueError("unknown_event_type")
            users = list(db.scalars(select(User).join(Role).where(Role.slug.in_(roles))))
            now = datetime.now(UTC)
            if target_user_ids is not None:
                users = [user for user in users if user.id in target_user_ids]
            user_ids = {user.id for user in users}
            park_user_ids = (
                set(
                    db.scalars(
                        select(UserPark.user_id).where(
                            UserPark.park_id == park_id, UserPark.user_id.in_(user_ids)
                        )
                    )
                )
                if park_id is not None and user_ids
                else set()
            )
            on_shift_user_ids = (
                set(
                    db.scalars(
                        select(ScheduleEntry.owner_user_id).where(
                            ScheduleEntry.owner_user_id.in_(user_ids),
                            ScheduleEntry.kind == "shift",
                            ScheduleEntry.start_at <= now,
                            ScheduleEntry.end_at >= now,
                        )
                    )
                )
                if on_shift and user_ids
                else set()
            )
            preferences = {
                item.user_id: item
                for item in db.scalars(
                    select(NotificationPreference).where(
                        NotificationPreference.user_id.in_(user_ids)
                    )
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
            for user in users:
                if park_id is not None and user.role != "royal" and user.id not in park_user_ids:
                    continue
                if on_shift and user.id not in on_shift_user_ids:
                    continue
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
                internal_recipients.append(user.id)
                preference = preferences.get(user.id)
                enabled = preference is None or (
                    preference.system_enabled
                    and event_type in set(json.loads(preference.categories_json))
                )
                if enabled:
                    recipients.append(user.id)
            db.commit()
            if deliver and recipients:
                try:
                    self._deliver(db, event_id=event_id, recipient_ids=recipients)
                except Exception:
                    logger.exception("Web Push delivery failed after internal notification commit")
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

    def _deliver(self, db: Session, *, event_id: str, recipient_ids: list[int]) -> None:
        settings = get_settings()
        deadline = time.monotonic() + settings.push_delivery_deadline_seconds
        if not settings.secret_key:
            logger.warning("Web Push skipped: SECRET_KEY is not configured")
            return
        private_key, _ = _vapid_key_pair(settings.secret_key)
        rows = list(
            db.scalars(
                select(PushSubscription)
                .where(PushSubscription.user_id.in_(recipient_ids))
                .order_by(PushSubscription.created_at, PushSubscription.id)
                .limit(settings.push_delivery_batch_size)
            )
        )
        deliveries = [
            (
                row.endpoint_hash,
                decrypt_secret(row.endpoint_encrypted, settings.secret_key),
                decrypt_secret(row.p256dh_encrypted, settings.secret_key),
                decrypt_secret(row.auth_encrypted, settings.secret_key),
            )
            for row in rows
        ]
        def send(delivery: tuple[str, str, str, str]) -> str | None:
            endpoint_hash, endpoint, p256dh, auth = delivery
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                webpush(
                    subscription_info={
                        "endpoint": endpoint,
                        "keys": {"p256dh": p256dh, "auth": auth},
                    },
                    data=json.dumps({"event_id": event_id}),
                    vapid_private_key=private_key,
                    vapid_claims={"sub": "mailto:robopark@localhost"},
                    timeout=max(0.1, min(5.0, remaining)),
                )
            except WebPushException as exc:
                response = getattr(exc, "response", None)
                if response is not None and response.status_code in {404, 410}:
                    return endpoint_hash
                else:
                    logger.warning("Web Push delivery failed", exc_info=exc)
            except Exception:
                logger.exception("Web Push delivery failed")
            return None

        if not deliveries or time.monotonic() >= deadline:
            return
        executor = ThreadPoolExecutor(
            max_workers=settings.push_max_concurrency,
            thread_name_prefix="robopark-webpush",
        )
        futures = [executor.submit(send, delivery) for delivery in deliveries]
        done, pending = wait(
            futures,
            timeout=max(0.0, deadline - time.monotonic()),
        )
        for future in pending:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        rejected_hashes = {endpoint_hash for future in done if (endpoint_hash := future.result())}
        if rejected_hashes:
            db.execute(
                delete(PushSubscription).where(
                    PushSubscription.endpoint_hash.in_(rejected_hashes)
                )
            )
            db.commit()


def _vapid_key_pair(secret_key: str) -> tuple[str, str]:
    scalar = int.from_bytes(hashlib.sha256(f"vapid:{secret_key}".encode()).digest(), "big")
    private_key = ec.derive_private_key((scalar % (_P256_ORDER - 1)) + 1, ec.SECP256R1())
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    public_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    public_key = base64.urlsafe_b64encode(public_bytes).decode("ascii").rstrip("=")
    return private_pem, public_key


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
