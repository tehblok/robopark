import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from robopark_api.config import get_settings
from robopark_api.crypto import encrypt_secret
from robopark_api.db import get_db
from robopark_api.deps import require_user
from robopark_api.models import Role, User, UserPark
from robopark_api.schedule_models import NotificationEvent, NotificationPreference, PushSubscription, ScheduleEntry
from robopark_api.schedule_schemas import PushPreferenceIn, PushSubscriptionIn

router = APIRouter(prefix="/push", tags=["push"])

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
    result = db.execute(delete(PushSubscription).where(PushSubscription.expires_at.is_not(None), PushSubscription.expires_at <= (now or datetime.now(UTC))))
    db.commit()
    return int(result.rowcount or 0)


def remove_rejected_subscription(db: Session, endpoint_hash: str) -> None:
    db.execute(delete(PushSubscription).where(PushSubscription.endpoint_hash == endpoint_hash))
    db.commit()


def prune_notification_data(db: Session, *, now: datetime | None = None, read_days: int = 30, unread_days: int = 90) -> dict[str, int]:
    current = now or datetime.now(UTC)
    subscriptions = prune_expired_subscriptions(db, now=current)
    result = db.execute(delete(NotificationEvent).where(or_(NotificationEvent.read_at <= current - timedelta(days=read_days), (NotificationEvent.read_at.is_(None)) & (NotificationEvent.created_at <= current - timedelta(days=unread_days)))))
    db.commit()
    return {"subscriptions": subscriptions, "notifications": int(result.rowcount or 0)}


class PushService:
    def __init__(self, session_factory: Callable[[], Session]):
        self._session_factory = session_factory

    def emit(self, *, event_type: str, park_id: int | None, protected_text: str) -> dict:
        with self._session_factory() as db:
            roles, on_shift = EVENT_ROLES.get(event_type, (set(), False))
            if not roles:
                raise ValueError("unknown_event_type")
            users = list(db.scalars(select(User).join(Role).where(Role.slug.in_(roles))))
            now = datetime.now(UTC)
            recipients: list[int] = []
            internal_recipients: list[int] = []
            event_id = hashlib.sha256(f"{event_type}:{now.timestamp()}:{protected_text}".encode()).hexdigest()[:24]
            for user in users:
                if park_id is not None and user.role != "royal" and db.scalar(select(UserPark).where(UserPark.user_id == user.id, UserPark.park_id == park_id)) is None:
                    continue
                if on_shift and db.scalar(select(ScheduleEntry.id).where(ScheduleEntry.owner_user_id == user.id, ScheduleEntry.kind == "shift", ScheduleEntry.start_at <= now, ScheduleEntry.end_at >= now).limit(1)) is None:
                    continue
                db.add(NotificationEvent(id=f"{event_id}-{user.id}", user_id=user.id, event_type=event_type, park_id=park_id, protected_text=protected_text))
                internal_recipients.append(user.id)
                preference = db.get(NotificationPreference, user.id)
                enabled = preference is None or (preference.system_enabled and event_type in set(json.loads(preference.categories_json)))
                if enabled:
                    recipients.append(user.id)
            db.commit()
            return {"event_id": event_id, "recipient_ids": recipients, "internal_recipient_ids": internal_recipients, "push_payload": {"event_id": event_id}}

    def emit_for_tests(self, **kwargs) -> dict:
        return self.emit(**kwargs)


@router.post("/subscriptions", status_code=status.HTTP_201_CREATED)
def subscribe(payload: PushSubscriptionIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
    settings = get_settings()
    endpoint_hash = hashlib.sha256(payload.endpoint.encode()).hexdigest()
    row = db.scalar(select(PushSubscription).where(PushSubscription.endpoint_hash == endpoint_hash))
    values = {"user_id": user.id, "endpoint_hash": endpoint_hash, "endpoint_encrypted": encrypt_secret(payload.endpoint, settings.secret_key), "p256dh_encrypted": encrypt_secret(payload.p256dh, settings.secret_key), "auth_encrypted": encrypt_secret(payload.auth, settings.secret_key), "expires_at": payload.expires_at}
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
def unsubscribe(subscription_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    row = db.get(PushSubscription, subscription_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "subscription_not_found")
    db.delete(row)
    db.commit()


@router.put("/preferences")
def preferences(payload: PushPreferenceIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
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
    rows = db.scalars(select(NotificationEvent).where(NotificationEvent.user_id == user.id).order_by(NotificationEvent.created_at.desc(), NotificationEvent.id.desc()).limit(200))
    return [{"id": row.id, "event_type": row.event_type, "park_id": row.park_id, "protected_text": row.protected_text, "read_at": row.read_at, "created_at": row.created_at} for row in rows]


@router.post("/inbox/{event_id}/read")
def mark_read(event_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    row = db.get(NotificationEvent, event_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "notification_not_found")
    row.read_at = datetime.now(UTC)
    db.commit()
    return {"ok": True}
