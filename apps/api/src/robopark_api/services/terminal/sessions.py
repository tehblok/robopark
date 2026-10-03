"""Transactional grants, immutable admission descriptors and owner-bound tickets."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import delete, func, select, update

from robopark_api.services import privileged_auth
from robopark_api.terminal_models import TerminalAttachTicket, TerminalSession, TerminalSessionEvent

from .authorization import TerminalError, authorize_terminal, aware

MAX_PENDING_ATTACH_TICKETS = 4


def event(db, row, name, reason=None):
    db.add(TerminalSessionEvent(session_id=row.id, event=name, reason=reason))


def reserve_session(
    db, *, actor_id, auth_session_hash, session_id, profile, capability, token, settings
):
    if profile not in ("maintenance", "root"):
        raise TerminalError("terminal_invalid_profile", 422)
    credential = privileged_auth._lock_credential(db, actor_id)
    actor = authorize_terminal(
        db, auth_session_hash=auth_session_hash, owner_id=actor_id, settings=settings
    )
    row = db.get(TerminalSession, session_id)
    if row is not None:
        if (
            row.owner_id != actor_id
            or row.auth_session_hash != auth_session_hash
            or row.profile != profile
            or row.descriptor["capability_revision"] != capability["capability_revision"]
        ):
            raise TerminalError("terminal_request_conflict")
        return row, False
    context = privileged_auth.AuditContext(
        ip=None,
        device=None,
        session_token_hash=auth_session_hash,
        operation_kind=f"terminal.open.{profile}",
        operation_id=session_id,
        capability_revision=capability["capability_revision"],
    )
    if not privileged_auth.consume_reauthorization_in_transaction(
        db, actor, raw_token=token, context=context, require_totp_only=True
    ):
        raise TerminalError("terminal_reauthorization_required", 403)
    now = datetime.now(UTC)
    duration = 900 if profile == "root" else 3600
    descriptor = {
        "session_id": session_id,
        "actor_id": actor_id,
        "auth_session_hash": auth_session_hash,
        "profile": profile,
        "credential_generation": credential.credential_generation,
        "capability_revision": capability["capability_revision"],
        "boot_id": capability["boot_id"],
        "broker_epoch": capability["broker_epoch"],
        "remaining_seconds": duration,
    }
    row = TerminalSession(
        id=session_id,
        owner_id=actor_id,
        auth_session_hash=auth_session_hash,
        profile=profile,
        state="starting",
        credential_generation=credential.credential_generation,
        broker_epoch=capability["broker_epoch"],
        descriptor=descriptor,
        expires_at=now + timedelta(seconds=duration),
    )
    db.add(row)
    db.flush()
    event(db, row, "reserved")
    return row, True


def own_session(db, session_id, auth_session_hash, owner_id):
    row = db.scalar(
        select(TerminalSession).where(
            TerminalSession.id == session_id,
            TerminalSession.owner_id == owner_id,
            TerminalSession.auth_session_hash == auth_session_hash,
        )
    )
    if row is None:
        raise TerminalError("terminal_session_missing", 404)
    return row


def end_session(db, row, reason):
    if row.state != "ended":
        row.state = "ended"
        row.ended_at = datetime.now(UTC)
        row.termination_reason = reason
        row.attachment_id = None
        row.attachment_expires_at = None
        event(db, row, "ended", reason)


def issue_attach_ticket(db, *, row):
    now = datetime.now(UTC)
    locked = db.scalar(
        select(TerminalSession)
        .where(TerminalSession.id == row.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None or locked.state == "ended" or aware(locked.expires_at) <= now:
        raise TerminalError("terminal_session_ended")
    db.execute(
        delete(TerminalAttachTicket).where(
            TerminalAttachTicket.session_id == locked.id,
            (TerminalAttachTicket.used_at.is_not(None)) | (TerminalAttachTicket.expires_at <= now),
        )
    )
    pending = db.scalar(
        select(func.count())
        .select_from(TerminalAttachTicket)
        .where(
            TerminalAttachTicket.session_id == locked.id,
            TerminalAttachTicket.used_at.is_(None),
            TerminalAttachTicket.expires_at > now,
        )
    )
    if pending >= MAX_PENDING_ATTACH_TICKETS:
        raise TerminalError("terminal_ticket_limit")
    raw = secrets.token_urlsafe(32)
    db.add(
        TerminalAttachTicket(
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            session_id=locked.id,
            auth_session_hash=locked.auth_session_hash,
            broker_epoch=locked.broker_epoch,
            expires_at=now + timedelta(seconds=15),
        )
    )
    db.flush()
    return raw


def consume_attach_ticket(db, *, raw_ticket, session_id, auth_session_hash, broker_epoch):
    now = datetime.now(UTC)
    # A database write acquires the same row lock on SQLite and PostgreSQL.
    db.execute(
        update(TerminalSession)
        .where(TerminalSession.id == session_id)
        .values(state=TerminalSession.state)
    )
    row = db.scalar(
        select(TerminalSession)
        .where(TerminalSession.id == session_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        row is None
        or row.auth_session_hash != auth_session_hash
        or row.broker_epoch != broker_epoch
        or row.state == "ended"
        or aware(row.expires_at) <= now
    ):
        raise TerminalError("terminal_session_ended")
    if row.attachment_id and row.attachment_expires_at and aware(row.attachment_expires_at) > now:
        raise TerminalError("terminal_already_attached")
    result = db.execute(
        update(TerminalAttachTicket)
        .where(
            TerminalAttachTicket.token_hash == hashlib.sha256(raw_ticket.encode()).hexdigest(),
            TerminalAttachTicket.session_id == session_id,
            TerminalAttachTicket.auth_session_hash == auth_session_hash,
            TerminalAttachTicket.broker_epoch == broker_epoch,
            TerminalAttachTicket.used_at.is_(None),
            TerminalAttachTicket.expires_at > now,
        )
        .values(used_at=now)
    )
    if result.rowcount != 1:
        raise TerminalError("terminal_invalid_ticket", 403)
    row.attachment_id = str(uuid4())
    row.attachment_expires_at = now + timedelta(seconds=20)
    row.state = "active"
    event(db, row, "attached")
    return row


def revoke_sessions(db, *, owner_id=None, auth_session_hash=None, reason):
    query = select(TerminalSession).where(TerminalSession.state != "ended")
    if owner_id is not None:
        query = query.where(TerminalSession.owner_id == owner_id)
    if auth_session_hash is not None:
        query = query.where(TerminalSession.auth_session_hash == auth_session_hash)
    rows = list(db.scalars(query.with_for_update()))
    for row in rows:
        end_session(db, row, reason)
    db.info.setdefault("terminal_revocations", set()).update(row.id for row in rows)
    return [row.id for row in rows]


def prune_terminal_history(db, *, now):
    db.execute(
        update(TerminalSession)
        .where(TerminalSession.state != "ended", TerminalSession.expires_at <= now)
        .values(state="ended", ended_at=now, termination_reason="expired")
    )
    db.execute(delete(TerminalAttachTicket).where(TerminalAttachTicket.expires_at <= now))
    old = select(TerminalSession.id).where(
        TerminalSession.state == "ended", TerminalSession.ended_at < now - timedelta(days=30)
    )
    db.execute(delete(TerminalSessionEvent).where(TerminalSessionEvent.session_id.in_(old)))
    db.execute(delete(TerminalAttachTicket).where(TerminalAttachTicket.session_id.in_(old)))
    return db.execute(delete(TerminalSession).where(TerminalSession.id.in_(old))).rowcount


# Register post-commit best-effort notifications; durable revocation remains authoritative.
from . import notifications as _notifications  # noqa: E402,F401
