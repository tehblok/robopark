"""Owner-bound full terminal access; root requires a separate TOTP grant."""

from contextlib import suppress
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, WebSocket
from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.config import Settings, get_settings
from robopark_api.db import get_db
from robopark_api.deps import require_royal
from robopark_api.models import User
from robopark_api.security import hash_session_token
from robopark_api.services.terminal import sessions
from robopark_api.services.terminal.authorization import (
    TerminalError,
    authorize_terminal,
    capabilities,
    require_origin,
)
from robopark_api.services.terminal.broker import BrokerClient
from robopark_api.terminal_models import TerminalSession
from robopark_api.terminal_schemas import CreateSessionIn, SessionOut

router = APIRouter(prefix="/admin/terminal", tags=["terminal"])


def _hash(request, settings):
    raw = request.cookies.get(settings.session_cookie_name)
    if not raw:
        raise HTTPException(401)
    return hash_session_token(raw)


def _fail(error):
    raise HTTPException(error.status_code, detail=error.reason) from error


def _access(request, settings, db, royal, *, mutate=False):
    if mutate:
        require_origin(request.headers, settings)
    token_hash = _hash(request, settings)
    authorize_terminal(db, auth_session_hash=token_hash, owner_id=royal.id, settings=settings)
    return token_hash


async def _reconcile(db, row, broker):
    if row.state == "ended":
        return row
    try:
        value = await broker.status(row.id)
    except TerminalError as exc:
        if exc.reason == "terminal_session_missing":
            sessions.end_session(db, row, "broker_restart")
            db.commit()
            return row
        raise
    if value.get("state") == "ended":
        sessions.end_session(db, row, value.get("termination_reason") or "ended")
    elif value.get("state") in ("active", "detached"):
        row.state = value["state"]
    for field in ("input_bytes", "output_bytes"):
        value_count = value.get(field)
        if type(value_count) is int and value_count >= 0:
            setattr(row, field, min(value_count, 2**63 - 1))
    db.commit()
    return row


@router.get("/capabilities")
def get_capabilities(
    response: Response,
    royal: User = Depends(require_royal),
    settings: Settings = Depends(get_settings),
):
    response.headers["Cache-Control"] = "no-store"
    try:
        return {"available": True, **capabilities(settings)}
    except TerminalError as exc:
        return {"available": False, "reason": exc.reason}


@router.post("/sessions", response_model=SessionOut)
async def create(
    payload: CreateSessionIn,
    request: Request,
    response: Response,
    token: str = Header(default="", alias="X-Privileged-Authorization"),
    royal: User = Depends(require_royal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    response.headers["Cache-Control"] = "no-store"
    try:
        login = _access(request, settings, db, royal, mutate=True)
        cap = capabilities(settings)
        if cap["capability_revision"] != payload.capability_revision:
            raise TerminalError("terminal_capabilities_changed")
        row, fresh = sessions.reserve_session(
            db,
            actor_id=royal.id,
            auth_session_hash=login,
            session_id=str(payload.id),
            profile=payload.profile,
            capability=cap,
            token=token,
            settings=settings,
        )
        db.commit()  # Grant consumption and UUID reservation become durable together before IPC.
        broker = BrokerClient(settings)
        if fresh:
            try:
                await broker.create(row.descriptor)
            except TerminalError as exc:
                if exc.reason not in {"terminal_unavailable", "terminal_result_unknown"}:
                    sessions.end_session(db, row, exc.reason)
                    db.commit()
                raise
        authorize_terminal(
            db,
            auth_session_hash=login,
            owner_id=royal.id,
            settings=settings,
            generation=row.credential_generation,
        )
        return await _reconcile(db, row, broker)
    except TerminalError as exc:
        db.rollback()
        _fail(exc)


@router.get("/sessions", response_model=list[SessionOut])
async def listing(
    request: Request,
    response: Response,
    royal: User = Depends(require_royal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    response.headers["Cache-Control"] = "no-store"
    try:
        login = _access(request, settings, db, royal)
        rows = list(
            db.scalars(
                select(TerminalSession)
                .where(
                    TerminalSession.owner_id == royal.id, TerminalSession.auth_session_hash == login
                )
                .order_by(TerminalSession.created_at.desc())
                .limit(20)
            )
        )
        broker = BrokerClient(settings)
        for row in rows:
            # Keep unknown state visible when the broker is unavailable.
            with suppress(TerminalError):
                await _reconcile(db, row, broker)
        return rows
    except TerminalError as exc:
        _fail(exc)


@router.post("/sessions/{session_id}/attach-ticket")
def attach_ticket(
    session_id: UUID,
    request: Request,
    response: Response,
    royal: User = Depends(require_royal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    response.headers["Cache-Control"] = "no-store"
    try:
        login = _access(request, settings, db, royal, mutate=True)
        row = sessions.own_session(db, str(session_id), login, royal.id)
        authorize_terminal(
            db,
            auth_session_hash=login,
            owner_id=royal.id,
            settings=settings,
            generation=row.credential_generation,
        )
        if row.broker_epoch != capabilities(settings)["broker_epoch"]:
            raise TerminalError("terminal_capabilities_changed")
        ticket = sessions.issue_attach_ticket(db, row=row)
        db.commit()
        return {"ticket": ticket, "expires_in": 15}
    except TerminalError as exc:
        db.rollback()
        _fail(exc)


@router.delete("/sessions/{session_id}", response_model=SessionOut)
async def terminate(
    session_id: UUID,
    request: Request,
    response: Response,
    royal: User = Depends(require_royal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    response.headers["Cache-Control"] = "no-store"
    try:
        login = _access(request, settings, db, royal, mutate=True)
        row = sessions.own_session(db, str(session_id), login, royal.id)
        if row.state == "ended":
            return row
        sessions.end_session(db, row, "closed")
        db.commit()
        try:
            await BrokerClient(settings).terminate(row.id)
        except TerminalError as exc:
            if exc.reason != "terminal_session_missing":
                raise
        return row
    except TerminalError as exc:
        db.rollback()
        _fail(exc)


@router.websocket("/sessions/{session_id}/stream")
async def stream(
    websocket: WebSocket, session_id: UUID, settings: Settings = Depends(get_settings)
):
    from robopark_api.services.terminal.stream import serve_terminal_stream

    await serve_terminal_stream(websocket, str(session_id), settings)
