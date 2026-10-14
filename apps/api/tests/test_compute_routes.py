import base64
import hashlib
import struct
import threading
import time

from conftest import login_as
from robopark_api.services import compute_client


def _png(width: int, height: int, size: int = 256 * 1024) -> bytes:
    header = (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x02\x00\x00\x00"
    )
    return header + b"x" * (size - len(header))


def _jpeg(width: int, height: int) -> bytes:
    return (
        b"\xff\xd8\xff\xe0\x00\x02"
        + b"\xff\xc0\x00\x11\x08"
        + struct.pack(">HH", height, width)
        + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00\xff\xd9"
    )


def test_image_compute_requires_auth_validates_input_and_returns_verified_output(
    client, seed_admin, monkeypatch
):
    source = _png(1200, 800)
    request = {
        "data_base64": base64.b64encode(source).decode(),
        "mime_type": "image/png",
        "max_edge": 1024,
        "quality": 80,
    }
    assert client.post("/compute/image", json=request).status_code == 401
    login_as(client, seed_admin.username, "secret")
    output = _jpeg(1024, 683)
    captured = {}

    def dispatch(operation, payload, requester_user_id, deadline_ms=20_000):
        captured.update(
            operation=operation,
            payload=payload,
            requester_user_id=requester_user_id,
            deadline_ms=deadline_ms,
        )
        return {
            "data_base64": base64.b64encode(output).decode(),
            "sha256": hashlib.sha256(output).hexdigest(),
            "mime_type": "image/jpeg",
            "width": 1024,
            "height": 683,
            "device_id": "mac-1",
        }

    monkeypatch.setattr(compute_client, "dispatch_compute", dispatch)
    response = client.post("/compute/image", json=request)

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["device_id"] == "mac-1"
    assert response.json()["sha256"] == hashlib.sha256(output).hexdigest()
    assert captured == {
        "operation": "image_resize_v1",
        "payload": request,
        "requester_user_id": seed_admin.id,
        "deadline_ms": 20_000,
    }


def test_image_compute_rejects_small_oversized_or_inconsistent_images(
    client, seed_admin, monkeypatch
):
    login_as(client, seed_admin.username, "secret")
    small = _png(100, 100, 1024)
    response = client.post(
        "/compute/image",
        json={
            "data_base64": base64.b64encode(small).decode(),
            "mime_type": "image/png",
            "max_edge": 1024,
            "quality": 80,
        },
    )
    assert response.status_code == 422

    source = _png(1200, 800)
    monkeypatch.setattr(
        compute_client,
        "dispatch_compute",
        lambda *_args, **_kwargs: {
            "data_base64": base64.b64encode(_jpeg(2000, 1000)).decode(),
            "sha256": hashlib.sha256(_jpeg(2000, 1000)).hexdigest(),
            "mime_type": "image/jpeg",
            "width": 2000,
            "height": 1000,
            "device_id": "mac-1",
        },
    )
    response = client.post(
        "/compute/image",
        json={
            "data_base64": base64.b64encode(source).decode(),
            "mime_type": "image/png",
            "max_edge": 1024,
            "quality": 80,
        },
    )
    assert response.status_code == 503


def test_pool_is_authenticated_and_delegates_owner_scope(client, seed_admin, monkeypatch):
    assert client.get("/compute/pool").status_code == 401
    login_as(client, seed_admin.username, "secret")
    expected = {
        "enabled": True,
        "scope": "all",
        "summary": {"connected": 0, "ready": 0, "capacity": 0, "inflight": 0},
        "devices": [],
    }
    monkeypatch.setattr(
        compute_client,
        "fetch_compute_status",
        lambda *, requester_user_id, settings=None: expected,
    )
    response = client.get("/compute/pool")
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == expected


def test_direct_route_is_bounded_authenticated_and_cancel_is_owner_scoped(
    client, seed_admin, monkeypatch
):
    payload = {
        "operation": "image_resize_v1",
        "payload_sha256": "a" * 64,
        "payload_bytes": 500_000,
        "offer": {"type": "offer", "sdp": "v=0\r\n"},
    }
    assert client.post("/compute/direct", json=payload).status_code == 401
    login_as(client, seed_admin.username, "secret")
    monkeypatch.setattr(
        compute_client,
        "dispatch_direct",
        lambda **kwargs: {
            "generation": "g",
            "job_id": "11111111-1111-4111-8111-111111111111",
            "attempt_id": "22222222-2222-4222-8222-222222222222",
            "payload_sha256": kwargs["payload_sha256"],
            "token": "token",
            "answer": {"type": "answer", "sdp": "v=0\r\n"},
            "device_id": "mac-1",
            "timeout_ms": 19_000,
        },
    )
    response = client.post("/compute/direct", json=payload)
    assert response.status_code == 200, response.text
    job_id = response.json()["job_id"]
    cancelled = {}
    monkeypatch.setattr(
        compute_client,
        "cancel_direct",
        lambda *, job_id, requester_user_id, settings=None: cancelled.update(
            job_id=job_id, requester_user_id=requester_user_id
        ),
    )
    assert client.delete(f"/compute/direct/{job_id}").status_code == 204
    assert cancelled == {"job_id": job_id, "requester_user_id": seed_admin.id}


def test_direct_route_rejects_unknown_fields_and_streams_only_bounded_json(
    client, seed_admin, monkeypatch
):
    login_as(client, seed_admin.username, "secret")
    payload = {
        "operation": "image_resize_v1",
        "payload_sha256": "a" * 64,
        "payload_bytes": 500_000,
        "offer": {"type": "offer", "sdp": "v=0\r\n"},
        "unexpected": "must not be ignored",
    }
    monkeypatch.setattr(
        compute_client,
        "dispatch_direct",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not dispatch")),
    )

    assert client.post("/compute/direct", json=payload).status_code == 422
    oversized = b"{" + b" " * (80 * 1024) + b"}"
    response = client.post(
        "/compute/direct",
        content=oversized,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_direct_finish_is_owner_scoped_and_delegates_outcome(client, seed_admin, monkeypatch):
    login_as(client, seed_admin.username, "secret")
    job_id = "11111111-1111-4111-8111-111111111111"
    captured = {}
    monkeypatch.setattr(
        compute_client,
        "finish_direct",
        lambda **kwargs: captured.update(kwargs) or {"status": "accepted"},
    )

    response = client.post(f"/compute/direct/{job_id}/finish", json={"outcome": "completed"})

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    assert captured == {
        "job_id": job_id,
        "requester_user_id": seed_admin.id,
        "outcome": "completed",
    }
    assert (
        client.post(f"/compute/direct/{job_id}/finish", json={"outcome": "unknown"}).status_code
        == 422
    )


def test_direct_signaling_does_not_block_the_api_event_loop(client, seed_admin, monkeypatch):
    login_as(client, seed_admin.username, "secret")
    started = threading.Event()
    release = threading.Event()

    def dispatch(**kwargs):
        started.set()
        release.wait(1)
        return {
            "generation": "g",
            "job_id": "11111111-1111-4111-8111-111111111111",
            "attempt_id": "22222222-2222-4222-8222-222222222222",
            "payload_sha256": kwargs["payload_sha256"],
            "token": "token",
            "answer": {"type": "answer", "sdp": "v=0\r\n"},
            "device_id": "mac-1",
            "timeout_ms": 19_000,
        }

    monkeypatch.setattr(compute_client, "dispatch_direct", dispatch)
    payload = {
        "operation": "image_resize_v1",
        "payload_sha256": "a" * 64,
        "payload_bytes": 500_000,
        "offer": {"type": "offer", "sdp": "v=0\r\n"},
    }
    holder = {}
    request_thread = threading.Thread(
        target=lambda: holder.update(response=client.post("/compute/direct", json=payload))
    )
    request_thread.start()
    assert started.wait(1)
    before = time.monotonic()
    health = client.get("/health")
    elapsed = time.monotonic() - before
    release.set()
    request_thread.join(2)

    assert health.status_code == 200
    assert elapsed < 0.5
    assert holder["response"].status_code == 200
