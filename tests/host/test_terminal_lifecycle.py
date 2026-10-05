import importlib.util
from uuid import uuid4

import pytest
from test_terminal_protocol import descriptor


def registry():
    assert importlib.util.find_spec("robopark_host.terminal_state"), (
        "terminal lifecycle missing"
    )
    from robopark_host.terminal_protocol import TerminalCreate
    from robopark_host.terminal_state import TerminalRegistry

    now = [0.0]
    value = TerminalCreate.from_dict(descriptor())
    reg = TerminalRegistry(clock=lambda: now[0])
    return reg, value, now


def test_create_reply_loss_is_idempotent():
    reg, req, _ = registry()
    first, created = reg.admit(req)
    second, again = reg.admit(req)
    assert created is True and again is False and first is second
    assert len(reg.sessions) == 1


def test_modified_uuid_replay_conflicts():
    from dataclasses import replace

    reg, req, _ = registry()
    reg.admit(req)
    with pytest.raises(ValueError, match="conflict"):
        reg.admit(replace(req, profile="root", remaining_seconds=900))


def test_admission_limits_profiles_not_just_count():
    from dataclasses import replace

    reg, req, _ = registry()
    reg.admit(req)
    with pytest.raises(ValueError, match="limit"):
        reg.admit(replace(req, session_id=str(uuid4())))
    reg.admit(
        replace(req, session_id=str(uuid4()), profile="root", remaining_seconds=900)
    )
    assert len(reg.sessions) == 2


def test_disconnect_grace_does_not_extend_hard_deadline():
    reg, req, clock = registry()
    row, _ = reg.admit(req)
    reg.attach(req.session_id, "attach-1")
    reg.detach(req.session_id, "attach-1")
    clock[0] = 14.9
    assert reg.expired() == []
    clock[0] = 15
    assert reg.expired() == [(req.session_id, "disconnected")]
    assert row.hard_deadline == 3600


def test_lease_and_idle_are_not_renewed_by_output():
    reg, req, clock = registry()
    reg.admit(req)
    reg.attach(req.session_id, "a")
    clock[0] = 20
    assert reg.expired() == [(req.session_id, "lease_expired")]
    reg, req, clock = registry()
    reg.admit(req)
    reg.attach(req.session_id, "a")
    for tick in range(10, 600, 10):
        clock[0] = tick
        reg.renew(req.session_id, "a")
    clock[0] = 600
    assert reg.expired() == [(req.session_id, "idle_timeout")]


def test_new_attachment_fences_old_disconnect():
    reg, req, _ = registry()
    reg.admit(req)
    reg.attach(req.session_id, "old")
    reg.detach(req.session_id, "old")
    reg.attach(req.session_id, "new")
    reg.detach(req.session_id, "old")
    assert reg.sessions[req.session_id].attachment_id == "new"
    with pytest.raises(ValueError):
        reg.renew(req.session_id, "old")


def test_root_expiry_is_absolute_and_ended_uuid_cannot_restart():
    from dataclasses import replace

    reg, req, clock = registry()
    req = replace(req, profile="root", remaining_seconds=900)
    reg.admit(req)
    reg.attach(req.session_id, "a")
    clock[0] = 900
    assert reg.expired() == [(req.session_id, "expired")]
    reg.finish(req.session_id, "expired")
    row, created = reg.admit(req)
    assert created is False and row.reason == "expired"


def test_first_finish_sets_retention_order_but_repeated_finish_does_not_refresh_it():
    from dataclasses import replace

    reg, req, _clock = registry()
    first, _ = reg.admit(req)
    reg.finish(req.session_id, "closed")
    second_request = replace(req, session_id=str(uuid4()))
    reg.admit(second_request)
    reg.finish(second_request.session_id, "expired")

    reg.finish(req.session_id, "revoked")

    assert list(reg.sessions) == [req.session_id, second_request.session_id]
    assert first.reason == "closed"


@pytest.mark.parametrize("operation", ["renew", "input_seen", "attach"])
def test_expired_session_cannot_be_revived_before_watchdog(operation):
    reg, req, clock = registry()
    reg.admit(req)
    if operation != "attach":
        reg.attach(req.session_id, "a")
    clock[0] = 20
    with pytest.raises(ValueError, match="session_ended"):
        getattr(reg, operation)(req.session_id, "a")
