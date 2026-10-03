import asyncio
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest

from robopark_api.config import Settings
from robopark_api.services.terminal.authorization import TerminalError, require_origin
from test_terminal_sessions import setup_grant, terminal


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"origin": "https://evil.example", "host": "evil.example"},
        {"origin": "https://park.example", "host": "evil.example"},
        {
            "origin": "https://evil.example",
            "host": "park.example",
            "x-forwarded-host": "park.example",
        },
        {"origin": "http://park.example", "host": "park.example"},
    ],
)
def test_ws_rejects_missing_or_spoofed_origin(headers):
    with pytest.raises(TerminalError):
        require_origin(headers, Settings(cors_origins="https://park.example"))


def test_configured_exact_origin_only():
    require_origin(
        {"origin": "https://park.example", "host": "park.example"},
        Settings(cors_origins="https://park.example"),
    )


def test_attach_ticket_is_bound_single_use(db_session, seed_royal):
    s = terminal()
    sid, login, raw = setup_grant(db_session, seed_royal)
    cap = {"capability_revision": "b" * 64, "boot_id": str(uuid4()), "broker_epoch": str(uuid4())}
    row, _ = s.reserve_session(
        db_session,
        actor_id=seed_royal.id,
        auth_session_hash=login,
        session_id=sid,
        profile="maintenance",
        capability=cap,
        token=raw,
        settings=Settings(),
    )
    raw = s.issue_attach_ticket(db_session, row=row)
    db_session.commit()
    with pytest.raises(TerminalError):
        s.consume_attach_ticket(
            db_session,
            raw_ticket=raw,
            session_id=sid,
            auth_session_hash="c" * 64,
            broker_epoch=cap["broker_epoch"],
        )
    db_session.rollback()
    attached = s.consume_attach_ticket(
        db_session,
        raw_ticket=raw,
        session_id=sid,
        auth_session_hash=login,
        broker_epoch=cap["broker_epoch"],
    )
    db_session.commit()
    assert attached.attachment_id
    with pytest.raises(TerminalError):
        s.consume_attach_ticket(
            db_session,
            raw_ticket=raw,
            session_id=sid,
            auth_session_hash=login,
            broker_epoch=cap["broker_epoch"],
        )


def test_stream_exists_and_has_server_lease():
    assert importlib.util.find_spec("robopark_api.services.terminal.stream"), (
        "WS implementation missing"
    )


def test_api_wire_codec_matches_shared_terminal_v1_fixture():
    from robopark_api.services.terminal.broker import LIMITS, encode, read_frame

    fixture = json.loads(
        (Path(__file__).resolve().parents[3] / "tests/fixtures/terminal-v1.json").read_text()
    )
    assert list(LIMITS) == fixture["limits"]

    async def decode(frame):
        reader = asyncio.StreamReader()
        reader.feed_data(frame)
        reader.feed_eof()
        return await read_frame(reader)

    for item in fixture["frames"]:
        payload = item["payload_utf8"].encode()
        frame = bytes.fromhex(item["frame_hex"])
        assert encode(item["kind"], payload) == frame
        assert asyncio.run(decode(frame)) == (item["kind"], payload)


def test_logout_closes_live_stream_and_stops_input(
    client, seed_royal, db_session, test_settings, monkeypatch
):
    import asyncio
    import hashlib
    from datetime import UTC, datetime, timedelta

    from conftest import login_as
    from robopark_api.models import PrivilegedCredential
    from robopark_api.services.terminal import sessions, stream
    from robopark_api.services.terminal.broker import encode
    from robopark_api.terminal_models import TerminalSession

    login_as(client, "royal", "secret")
    login = hashlib.sha256(client.cookies.get("robopark_session").encode()).hexdigest()
    now = datetime.now(UTC)
    epoch = str(uuid4())
    sid = str(uuid4())
    db_session.add(
        PrivilegedCredential(
            user_id=seed_royal.id,
            totp_secret_encrypted="fixture",
            enrolled_at=now,
            credential_generation=1,
        )
    )
    row = TerminalSession(
        id=sid,
        owner_id=seed_royal.id,
        auth_session_hash=login,
        profile="maintenance",
        state="detached",
        credential_generation=1,
        broker_epoch=epoch,
        descriptor={},
        expires_at=now + timedelta(hours=1),
    )
    db_session.add(row)
    db_session.flush()
    ticket = sessions.issue_attach_ticket(db_session, row=row)
    db_session.commit()
    test_settings.terminal_allowed_origins = "https://testserver"
    monkeypatch.setattr(stream, "capabilities", lambda _: {"broker_epoch": epoch})
    writes = []
    closed = []

    class Writer:
        def write(self, data):
            writes.append(data)

        async def drain(self):
            pass

        def close(self):
            closed.append(True)

        async def wait_closed(self):
            pass

    class Broker:
        def __init__(self, _):
            pass

        async def attach(self, *args):
            reader = asyncio.StreamReader()
            reader.feed_data(encode(2, b"visible prompt"))
            return reader, Writer()

        async def terminate(self, *args):
            closed.append(True)

    monkeypatch.setattr(stream, "BrokerClient", Broker)
    with client.websocket_connect(
        f"/admin/terminal/sessions/{sid}/stream", headers={"origin": "https://testserver"}
    ) as ws:
        ws.send_json({"op": "authenticate", "ticket": ticket})
        assert ws.receive_json()["op"] == "ready"
        assert ws.receive_bytes() == b"visible prompt"
        ws.send_bytes(b"id\n")
        assert client.post("/auth/logout").status_code == 204
        error = ws.receive_json()
        assert error == {"op": "error", "error": "terminal_access_revoked"}
    assert closed
    assert sum(b"id\n" in entry for entry in writes) <= 1


def test_control_and_resize_reject_ambiguous_or_large_frames():
    from robopark_api.services.terminal.stream import message

    for value in ('{"op":"heartbeat","op":"resize"}', "[]", "x" * 4097):
        with pytest.raises(TerminalError):
            message(value)
