"""Local broker + real packaged Swift worker. No accounts or Keychain outside test DB."""

import base64
import csv
import hashlib
import io
import json
import os
import struct
import subprocess
import sys
import threading
import zlib
from contextlib import ExitStack
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from robopark_api.config import Settings
from robopark_api.emergency_bridge import create_broker_app
from robopark_api.services.image_bounds import jpeg_dimensions
from test_compute_bridge import _device, _signed_headers

WORKER = Path(
    os.environ.get(
        "ROBOPARK_COMPUTE_TEST_WORKER",
        Path(__file__).parents[2] / "macos/.build/release/RoboparkComputeWorker",
    )
)
pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or not WORKER.is_file(), reason="built native macOS worker required"
)


def connect(stack, client, settings, private, device):
    ws = stack.enter_context(client.websocket_connect("/emergency/bridge/compute/ws"))
    challenge = ws.receive_json()
    signed = f"robopark-compute-v1\n{settings.robopark_instance_id}\n{settings.robopark_environment}\n{challenge['nonce']}".encode()
    ws.send_json(
        {
            "type": "auth",
            "device_id": device.id,
            "signature": base64.b64encode(private.sign(signed, ec.ECDSA(hashes.SHA256()))).decode(),
        }
    )
    ready = ws.receive_json()
    ws.send_json(
        {
            "type": "heartbeat",
            "generation": ready["generation"],
            "capacity": 1,
            "active_jobs": 0,
            "cpu_percent": 5,
            "memory_percent": 20,
            "thermal_state": "normal",
            "power": "ac",
            "supports": ["csv_render_v1", "image_resize_v1"],
            "version": "0.2.3-rc.2",
        }
    )
    assert ws.receive_json() == {
        "type": "heartbeat_ack",
        "generation": ready["generation"],
    }
    return ws


def dispatch(client, settings, owner, operation, payload):
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    body = json.dumps(
        {
            "operation": operation,
            "payload_base64": base64.b64encode(raw).decode(),
            "payload_sha256": hashlib.sha256(raw).hexdigest(),
            "requester_user_id": owner.id,
            "deadline_ms": 10_000,
        }
    ).encode()
    holder = {}

    def request():
        holder["response"] = client.post(
            "/internal/v1/compute", content=body, headers=_signed_headers(settings, body)
        )

    thread = threading.Thread(target=request)
    thread.start()
    return holder, thread


def execute(ws):
    job = ws.receive_json()
    assert job["type"] == "compute_job"
    process = subprocess.run(
        [str(WORKER)], input=json.dumps(job).encode() + b"\n", capture_output=True, timeout=10
    )
    assert process.returncode == 0
    result = json.loads(process.stdout)
    assert result["ok"] is True
    ws.send_json(result)
    return job


def test_two_registered_macs_failover_to_real_swift_csv_then_image(db_engine, db_session):
    settings = Settings(
        _env_file=None,
        database_url=str(db_engine.url),
        secret_key="isolated-native-test",
        emergency_bridge_enabled=True,
        robopark_instance_id="11111111-1111-1111-1111-111111111111",
    )
    private1, owner1, device1 = _device(db_session, settings)
    owner1.username = "first-compute-owner"
    device1.id = "11111111-1111-1111-1111-111111111111"
    db_session.commit()
    private2, owner2, device2 = _device(db_session, settings)
    device2.id = "22222222-2222-2222-2222-222222222222"
    db_session.commit()
    app = create_broker_app(sessionmaker(bind=db_engine, future=True), settings)
    with ExitStack() as stack:
        client = stack.enter_context(TestClient(app))
        first = connect(stack, client, settings, private1, device1)
        second = connect(stack, client, settings, private2, device2)
        payload = {
            "headers": ["Имя", "Заметка"],
            "rows": [["Алиса", 'запятая, "кавычки"\nстрока'], ["Боб", "'=already escaped"]],
        }
        holder, thread = dispatch(client, settings, owner2, "csv_render_v1", payload)
        failed_job = first.receive_json()
        assert failed_job["type"] == "compute_job"
        first.close()
        successful_job = execute(second)
        assert successful_job["attempt_id"] != failed_job["attempt_id"]
        thread.join(12)
        response = holder["response"]
        assert response.status_code == 200, response.text
        assert response.json()["attempts_used"] == 2
        assert response.json()["device_id"] == device2.id
        expected = io.StringIO(newline="")
        writer = csv.writer(expected)
        writer.writerow(payload["headers"])
        writer.writerows(payload["rows"])
        output = base64.b64decode(response.json()["result"]["data_base64"])
        assert output == b"\xef\xbb\xbf" + expected.getvalue().encode()

        def chunk(kind, value):
            return (
                struct.pack(">I", len(value))
                + kind
                + value
                + struct.pack(">I", zlib.crc32(kind + value) & 0xFFFFFFFF)
            )

        pixels = b"".join(
            b"\0" + bytes((x % 256, y % 256, 127)) * 128 for y, x in enumerate(range(64))
        )
        png = (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 128, 64, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(pixels, level=0))
            + chunk(b"IEND", b"")
        )
        holder, thread = dispatch(
            client,
            settings,
            owner2,
            "image_resize_v1",
            {
                "data_base64": base64.b64encode(png).decode(),
                "mime_type": "image/png",
                "max_edge": 64,
                "quality": 80,
            },
        )
        execute(second)
        thread.join(12)
        response = holder["response"]
        assert response.status_code == 200, response.text
        image = response.json()["result"]
        output = base64.b64decode(image["data_base64"])
        assert jpeg_dimensions(output) == (64, 32)
        assert hashlib.sha256(output).hexdigest() == image["sha256"]
