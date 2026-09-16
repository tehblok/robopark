"""Task-scoped presence and local shift handoff, authorized on every request."""

import time
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from robopark_api.collaboration_models import TrackerHandoff, TrackerPresence
from robopark_api.db import get_db
from robopark_api.deps import require_user
from robopark_api.models import User
from robopark_api.routers.tracker_actions import _mark_park_change, _require_token
from robopark_api.routers.tracker_read import _ensure_tracker_user
from robopark_api.services import tracker_client
from robopark_api.services.tracker_policy import enforce_issue_scope, ensure_action_allowed

router = APIRouter(prefix="/tracker/issues", tags=["tracker-collaboration"])
PRESENCE_TTL = 90


def authorized_issue(db, user, key):
    _ensure_tracker_user(user, db)
    # Local handoffs and presence must not outlive an upstream park/queue move.
    # This request is an authorization read, so a previous TTL response is unsafe.
    try:
        issue = tracker_client.get_issue(token=_require_token(db), key=key)
    except tracker_client.TrackerError as exc:
        raise HTTPException(502, "tracker_upstream_error") from exc
    if issue is None:
        raise HTTPException(404)
    enforce_issue_scope(db, user, issue)
    return issue


@router.post("/{key}/presence")
def presence(key: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    authorized_issue(db, user, key)
    now = time.time()
    db.execute(delete(TrackerPresence).where(TrackerPresence.expires_at <= now))
    db.execute(
        insert(TrackerPresence)
        .values(issue_key=key, actor_id=user.id, expires_at=now + PRESENCE_TTL)
        .on_conflict_do_update(
            index_elements=["issue_key", "actor_id"], set_={"expires_at": now + PRESENCE_TTL}
        )
    )
    db.commit()
    rows = db.execute(
        select(User.username, TrackerPresence.expires_at)
        .join(TrackerPresence, TrackerPresence.actor_id == User.id)
        .where(
            TrackerPresence.issue_key == key,
            TrackerPresence.actor_id != user.id,
            TrackerPresence.expires_at > now,
            User.is_active.is_(True),
        )
    ).all()
    return {
        "ttl_seconds": PRESENCE_TTL,
        "people": [{"username": name, "expires_at": expires} for name, expires in rows],
    }


def handoff_out(db, row):
    if not row:
        return {
            "revision": 0,
            "done": "",
            "remaining": "",
            "obstacles": "",
            "author": None,
            "updated_at": None,
        }
    author = db.get(User, row.author_id) if row.author_id else None
    return {
        "revision": row.revision,
        "done": row.done,
        "remaining": row.remaining,
        "obstacles": row.obstacles,
        "author": author.username if author else None,
        "updated_at": datetime.fromtimestamp(row.updated_at, UTC).isoformat(),
    }


@router.get("/{key}/handoff")
def get_handoff(key: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    authorized_issue(db, user, key)
    return handoff_out(db, db.get(TrackerHandoff, key))


class HandoffIn(BaseModel):
    revision: int = Field(ge=0)
    done: str = Field(max_length=4000)
    remaining: str = Field(max_length=4000)
    obstacles: str = Field(max_length=4000)


@router.put("/{key}/handoff")
def put_handoff(
    key: str,
    payload: HandoffIn,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    issue = authorized_issue(db, user, key)
    ensure_action_allowed(db, user, issue, "comment")
    _mark_park_change(request, db, issue)
    values = {
        "done": payload.done.strip(),
        "remaining": payload.remaining.strip(),
        "obstacles": payload.obstacles.strip(),
        "author_id": user.id,
        "updated_at": time.time(),
        "revision": payload.revision + 1,
    }
    if payload.revision == 0:
        db.add(TrackerHandoff(issue_key=key, **values))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "tracker_handoff_conflict") from None
    else:
        result = db.execute(
            update(TrackerHandoff)
            .where(TrackerHandoff.issue_key == key, TrackerHandoff.revision == payload.revision)
            .values(**values)
        )
        if result.rowcount != 1:
            db.rollback()
            raise HTTPException(409, "tracker_handoff_conflict")
        db.commit()
    return handoff_out(db, db.get(TrackerHandoff, key))
