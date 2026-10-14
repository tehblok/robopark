import base64
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from conftest import role_id_for
from robopark_api.models import AccessStatus, CorporateWriteAuthority, CorporateWriteReceipt, User
from robopark_api.security import hash_password
from robopark_api.services import (
    corporate_server_write,
    corporate_write_client,
    corporate_write_contract,
    corporate_write_service,
)


def _user(db, username="corporate-writer"):
    user = User(
        username=username,
        password_hash=hash_password("secret"),
        role_id=role_id_for(db, "operator"),
        access_status=AccessStatus.approved.value,
        is_active=True,
        tracker_login=f"{username}.tracker",
    )
    db.add(user)
    db.flush()
    return user


def test_write_contract_is_closed_and_bounded():
    assert corporate_write_contract.validate_request(
        "comment_create",
        "ROBOPARK-42",
        {"text": "done", "attachment_ids": ["123"]},
    ) == {"text": "done", "attachment_ids": ["123"]}
    assert (
        corporate_write_contract.validate_request(
            "issue_patch",
            "ROBOPARK-42",
            {"fields": {"assignee": None, "tags": ["field"]}, "version": "7"},
        )["fields"]["assignee"]
        is None
    )
    assert (
        corporate_write_contract.validate_request(
            "transition_execute",
            "ROBOPARK-42",
            {"transition": "close_fixed", "resolution": "fixed"},
        )["transition"]
        == "close_fixed"
    )
    raw = b"small-image"
    upload = corporate_write_contract.validate_request(
        "attachment_upload",
        "ROBOPARK-42",
        {
            "filename": "photo.jpg",
            "content_type": "image/jpeg",
            "data_base64": base64.b64encode(raw).decode(),
        },
    )
    assert upload["data_base64"] == base64.b64encode(raw).decode()

    with pytest.raises(corporate_write_contract.ContractError, match="corporate_fields_invalid"):
        corporate_write_contract.validate_request(
            "issue_patch", "ROBOPARK-42", {"fields": {"summary": "arbitrary"}}
        )
    with pytest.raises(corporate_write_contract.ContractError, match="corporate_resource_invalid"):
        corporate_write_contract.validate_request("comment_create", "https://evil", {"text": "x"})
    with pytest.raises(
        corporate_write_contract.ContractError, match="corporate_attachment_too_large"
    ):
        corporate_write_contract.validate_request(
            "attachment_upload",
            "ROBOPARK-42",
            {
                "filename": "large.jpg",
                "content_type": "image/jpeg",
                "data_base64": base64.b64encode(b"x" * (4 * 1024 * 1024 + 1)).decode(),
            },
        )
    with pytest.raises(corporate_write_contract.ContractError, match="corporate_params_invalid"):
        corporate_write_contract.validate_request(
            "attachment_upload",
            "ROBOPARK-42",
            {
                "filename": "photo.jpg\r\nX-Evil: yes",
                "content_type": "image/jpeg",
                "data_base64": base64.b64encode(b"x").decode(),
            },
        )


def test_authority_is_immutable_and_existing_pin_wins(db_session):
    actor = _user(db_session)
    command_id = str(uuid4())
    first = corporate_write_service.pin_authority(
        db_session,
        command_id=command_id,
        requester_user_id=actor.id,
        resource="ROBOPARK-42",
        mode="mac",
        device_id="device-1",
        owner_user_id=actor.id,
        owner_tracker_login=actor.tracker_login,
    )
    db_session.commit()

    same = corporate_write_service.pin_authority(
        db_session,
        command_id=command_id,
        requester_user_id=actor.id,
        resource="ROBOPARK-42",
        mode="server",
    )
    assert same.id == first.id
    assert same.mode == "mac"
    assert same.device_id == "device-1"

    with pytest.raises(corporate_write_service.CorporateWriteError) as changed:
        corporate_write_service.pin_authority(
            db_session,
            command_id=command_id,
            requester_user_id=actor.id,
            resource="OTHER-1",
            mode="server",
        )
    assert (changed.value.status_code, changed.value.code) == (409, "corporate_write_conflict")


def test_receipt_fences_resend_and_replays_completed_result(db_session):
    actor = _user(db_session)
    authority = corporate_write_service.pin_authority(
        db_session,
        command_id=str(uuid4()),
        requester_user_id=actor.id,
        resource="ROBOPARK-42",
        mode="server",
    )
    db_session.commit()
    params = {"text": "once"}
    started = corporate_write_service.begin_step(
        db_session,
        authority=authority,
        action="comment_create",
        params=params,
    )
    assert started.execute is True
    db_session.commit()

    fenced = corporate_write_service.begin_step(
        db_session,
        authority=authority,
        action="comment_create",
        params=params,
    )
    assert fenced.execute is False
    assert fenced.error_code == "corporate_write_outcome_unknown"

    corporate_write_service.complete_step(
        db_session, started.receipt, status_code=201, payload={"id": "comment-1"}
    )
    db_session.commit()
    replay = corporate_write_service.begin_step(
        db_session,
        authority=authority,
        action="comment_create",
        params=params,
    )
    assert replay.execute is False
    assert replay.response.status_code == 201
    assert replay.response.payload == {"id": "comment-1"}

    with pytest.raises(corporate_write_service.CorporateWriteError) as changed:
        corporate_write_service.begin_step(
            db_session,
            authority=authority,
            action="comment_create",
            params={"text": "changed"},
        )
    assert (changed.value.status_code, changed.value.code) == (409, "corporate_write_conflict")
    assert db_session.query(CorporateWriteAuthority).count() == 1
    assert db_session.query(CorporateWriteReceipt).count() == 1


def test_server_authority_executes_once_and_replays(db_engine, db_session, monkeypatch):
    actor = _user(db_session, "server-writer")
    actor.role_id = role_id_for(db_session, "admin")
    authority = corporate_write_service.pin_authority(
        db_session,
        command_id=str(uuid4()),
        requester_user_id=actor.id,
        resource="ROBOPARK-42",
        mode="server",
    )
    db_session.commit()
    monkeypatch.setattr(corporate_write_client, "SessionLocal", sessionmaker(bind=db_engine))
    calls = []

    def send(**kwargs):
        calls.append(kwargs)
        return corporate_write_contract.CorporateWriteResponse(201, {"id": "comment-1"})

    monkeypatch.setattr(corporate_server_write, "execute", send)
    first = corporate_write_client.execute(
        command_id=authority.id,
        requester_user_id=actor.id,
        action="comment_create",
        resource="ROBOPARK-42",
        params={"text": "once"},
        server_token="server-token",
    )
    replay = corporate_write_client.execute(
        command_id=authority.id,
        requester_user_id=actor.id,
        action="comment_create",
        resource="ROBOPARK-42",
        params={"text": "once"},
        server_token="server-token",
    )
    assert (
        first == replay == corporate_write_contract.CorporateWriteResponse(201, {"id": "comment-1"})
    )
    assert len(calls) == 1


def test_server_writer_uses_if_match_and_treats_redirect_as_unknown(monkeypatch):
    calls = []
    status = {"value": 204}

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def patch(self, url, **kwargs):
            calls.append((url, kwargs))
            return httpx.Response(status["value"])

        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return httpx.Response(status["value"])

    monkeypatch.setattr(corporate_server_write.httpx, "Client", Client)
    result = corporate_server_write.execute(
        token="token",
        action="issue_patch",
        resource="ROBOPARK-42",
        params={"fields": {"tags": ["fixed"]}, "version": "17"},
    )
    assert result.status_code == 204
    assert calls[0][1]["headers"]["If-Match"] == '"17"'
    assert "params" not in calls[0][1]

    status["value"] = 302
    with pytest.raises(corporate_server_write.ServerWriteError) as redirect:
        corporate_server_write.execute(
            token="token",
            action="comment_create",
            resource="ROBOPARK-42",
            params={"text": "do not follow"},
        )
    assert redirect.value.outcome_unknown is True
