import importlib.util
import json
from uuid import uuid4

import pytest


def protocol():
    assert importlib.util.find_spec("robopark_host.terminal_protocol"), (
        "terminal protocol missing"
    )
    from robopark_host import terminal_protocol

    return terminal_protocol


def descriptor(**changes):
    value = {
        "session_id": str(uuid4()),
        "actor_id": 1,
        "auth_session_hash": "a" * 64,
        "profile": "maintenance",
        "credential_generation": 1,
        "capability_revision": "b" * 64,
        "boot_id": str(uuid4()),
        "broker_epoch": str(uuid4()),
        "remaining_seconds": 3600,
    }
    value.update(changes)
    return value


@pytest.mark.parametrize(
    "change",
    [
        {"actor_id": True},
        {"profile": "sudo"},
        {"remaining_seconds": 3601},
        {"profile": "root", "remaining_seconds": 901},
        {"session_id": "../root"},
        {"auth_session_hash": ""},
        {"credential_generation": -1},
        {"argv": ["/bin/bash"]},
    ],
)
def test_create_rejects_ambiguous_or_escalating_fields(change):
    with pytest.raises(ValueError):
        protocol().TerminalCreate.from_dict(descriptor(**change))


def test_valid_root_descriptor_is_immutable():
    from dataclasses import FrozenInstanceError

    value = protocol().TerminalCreate.from_dict(
        descriptor(profile="root", remaining_seconds=900)
    )
    assert value.profile == "root"
    with pytest.raises(FrozenInstanceError):
        value.profile = "maintenance"


@pytest.mark.parametrize(
    "data",
    [
        b'{"op":"status","op":"create"}',
        b"[]",
        b'{"op":"exec"}',
        b"{" + b" " * 4096 + b"}",
    ],
)
def test_control_rejects_duplicate_keys_bad_types_and_oversize(data):
    with pytest.raises(ValueError):
        protocol().decode_control(data)


def test_frame_length_and_kind_limits():
    p = protocol()
    assert (
        p.decode_control(p.encode_control({"op": "status", "id": str(uuid4())}))["op"]
        == "status"
    )
    with pytest.raises(ValueError):
        p.pack_frame(1, b"x" * 16385)
    with pytest.raises(ValueError):
        p.pack_frame(9, b"x")


def test_shared_terminal_v1_wire_fixture():
    from pathlib import Path

    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures/terminal-v1.json").read_text()
    )
    for row in fixture["frames"]:
        payload = row["payload_utf8"].encode()
        assert protocol().pack_frame(row["kind"], payload).hex() == row["frame_hex"]
        if row["kind"] == 0:
            protocol().decode_control(payload)
