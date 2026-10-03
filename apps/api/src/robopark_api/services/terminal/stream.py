"""Authenticated bounded WSS bridge with short database leases and no transcripts."""

import asyncio
import json
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from fastapi import WebSocketDisconnect
from sqlalchemy import select, update

from robopark_api.security import hash_session_token
from robopark_api.terminal_models import TerminalSession
from robopark_api.terminal_schemas import SessionOut

from . import sessions
from .authorization import TerminalError, authorize_terminal, aware, capabilities, require_origin
from .broker import BrokerClient, control, read_frame, send


def message(text):
    if not isinstance(text, str) or len(text.encode()) > 4096:
        raise TerminalError("terminal_invalid_control", 422)

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    try:
        value = json.loads(text, object_pairs_hook=unique)
    except (ValueError, RecursionError):
        raise TerminalError("terminal_invalid_control", 422) from None
    if not isinstance(value, dict):
        raise TerminalError("terminal_invalid_control", 422)
    return value


def check_access(factory, settings, session_id, login, *, ticket=None, attachment=None):
    with factory() as db:
        row = db.scalar(select(TerminalSession).where(TerminalSession.id == session_id))
        if row is None or row.auth_session_hash != login:
            raise TerminalError("terminal_session_missing", 404)
        authorize_terminal(
            db,
            auth_session_hash=login,
            owner_id=row.owner_id,
            settings=settings,
            generation=row.credential_generation,
        )
        now = datetime.now(UTC)
        if row.state == "ended" or aware(row.expires_at) <= now:
            raise TerminalError("terminal_session_ended")
        if capabilities(settings)["broker_epoch"] != row.broker_epoch:
            raise TerminalError("terminal_capabilities_changed")
        if ticket is not None:
            row = sessions.consume_attach_ticket(
                db,
                raw_ticket=ticket,
                session_id=session_id,
                auth_session_hash=login,
                broker_epoch=row.broker_epoch,
            )
        elif attachment is not None:
            # CAS fences a late lease from an old stream or a concurrent revoke.
            result = db.execute(
                update(TerminalSession)
                .where(
                    TerminalSession.id == session_id,
                    TerminalSession.attachment_id == attachment,
                    TerminalSession.state != "ended",
                )
                .values(attachment_expires_at=now + timedelta(seconds=20))
            )
            if result.rowcount != 1:
                raise TerminalError("terminal_attachment_changed")
        snapshot = SessionOut.model_validate(row).model_dump(mode="json")
        attachment_id = row.attachment_id
        db.commit()
        return snapshot, attachment_id


def detach(factory, session_id, attachment):
    with factory() as db:
        db.execute(
            update(TerminalSession)
            .where(
                TerminalSession.id == session_id,
                TerminalSession.attachment_id == attachment,
                TerminalSession.state != "ended",
            )
            .values(state="detached", attachment_id=None, attachment_expires_at=None)
        )
        db.commit()


async def serve_terminal_stream(websocket, session_id, settings):
    writer = None
    tasks = []
    attachment = None
    accepted = False
    revoke = False
    factory = websocket.app.state.terminal_session_factory
    broker = BrokerClient(settings)
    try:
        require_origin(websocket.headers, settings)
        if websocket.query_params:
            raise TerminalError("terminal_invalid_control", 422)
        raw = websocket.cookies.get(settings.session_cookie_name)
        if not raw:
            raise TerminalError("terminal_login_expired", 401)
        login = hash_session_token(raw)
        await asyncio.wait_for(
            asyncio.to_thread(check_access, factory, settings, session_id, login), 3
        )
        await websocket.accept()
        accepted = True
        first = await asyncio.wait_for(websocket.receive(), 5)
        auth = message(first.get("text"))
        if (
            set(auth) != {"op", "ticket"}
            or auth["op"] != "authenticate"
            or not isinstance(auth["ticket"], str)
            or not 20 <= len(auth["ticket"]) <= 128
        ):
            raise TerminalError("terminal_invalid_ticket", 403)
        snapshot, attachment = await asyncio.wait_for(
            asyncio.to_thread(
                check_access, factory, settings, session_id, login, ticket=auth["ticket"]
            ),
            3,
        )
        auth.clear()  # A ticket never enters logs, URLs or lifecycle records.
        reader, writer = await broker.attach(session_id, attachment)
        await websocket.send_json({"op": "ready", "session": snapshot})
        access_until = time.monotonic() + 5
        last_client = time.monotonic()
        write_lock = asyncio.Lock()

        async def checked_send(kind, data):
            if time.monotonic() >= access_until:
                raise TerminalError("terminal_access_expired", 403)
            async with write_lock:
                await send(writer, kind, data)

        async def inputs():
            nonlocal last_client
            while True:
                incoming = await asyncio.wait_for(websocket.receive(), 20)
                if incoming["type"] == "websocket.disconnect":
                    return
                if time.monotonic() >= access_until:
                    raise TerminalError("terminal_access_expired", 403)
                last_client = time.monotonic()
                if incoming.get("bytes") is not None:
                    data = incoming["bytes"]
                    if len(data) > 16384:
                        raise TerminalError("terminal_frame_too_large", 422)
                    if data:
                        await checked_send(1, data)
                else:
                    value = message(incoming.get("text"))
                    if value == {"op": "heartbeat"}:
                        continue
                    if set(value) != {"op", "cols", "rows"} or value["op"] != "resize":
                        raise TerminalError("terminal_invalid_control", 422)
                    if (
                        type(value["cols"]) is not int
                        or not 20 <= value["cols"] <= 300
                        or type(value["rows"]) is not int
                        or not 5 <= value["rows"] <= 150
                    ):
                        raise TerminalError("terminal_invalid_size", 422)
                    value.update(id=session_id, attachment_id=attachment)
                    await checked_send(0, json.dumps(value).encode())

        async def output():
            while True:
                kind, data = await read_frame(reader)
                if time.monotonic() >= access_until:
                    raise TerminalError("terminal_access_expired", 403)
                if kind == 2:
                    await asyncio.wait_for(websocket.send_bytes(data), 5)
                elif kind == 0:
                    value = message(data.decode())
                    if value.get("op") == "ended":
                        await asyncio.wait_for(
                            websocket.send_json(
                                {"op": "ended", "reason": value.get("reason", "ended")}
                            ),
                            5,
                        )
                        return
                    raise TerminalError("terminal_invalid_response")
                else:
                    raise TerminalError("terminal_invalid_response")

        async def lease():
            nonlocal access_until, revoke
            while True:
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(
                            check_access,
                            factory,
                            settings,
                            session_id,
                            login,
                            attachment=attachment,
                        ),
                        3,
                    )
                except Exception:
                    revoke = True
                    raise TerminalError("terminal_access_revoked", 403) from None
                if time.monotonic() - last_client >= 20:
                    return
                access_until = time.monotonic() + 5
                async with write_lock:
                    await control(
                        writer, {"op": "lease", "id": session_id, "attachment_id": attachment}
                    )
                await asyncio.sleep(4)

        tasks = [
            asyncio.create_task(inputs()),
            asyncio.create_task(output()),
            asyncio.create_task(lease()),
        ]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except (WebSocketDisconnect, asyncio.IncompleteReadError, OSError, TimeoutError):
        pass
    except TerminalError as exc:
        if accepted:
            with suppress(Exception):
                await asyncio.wait_for(websocket.send_json({"op": "error", "error": exc.reason}), 1)
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if writer:
            writer.close()
            with suppress(Exception):
                await asyncio.wait_for(writer.wait_closed(), 1)
        if attachment:
            with suppress(Exception):
                await asyncio.wait_for(
                    asyncio.to_thread(detach, factory, session_id, attachment), 3
                )
        if revoke:
            with suppress(Exception):
                await asyncio.wait_for(broker.terminate(session_id, "revoked"), 10)
        with suppress(Exception):
            await asyncio.wait_for(websocket.close(code=1000 if accepted else 1008), 1)
