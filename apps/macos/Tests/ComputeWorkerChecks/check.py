#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import json
import struct
import subprocess
import sys
import uuid
import zlib
from pathlib import Path


def png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw.extend((x % 256, y % 256, 127))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b"")


def frame(operation: str, payload: dict, digest: str | None = None) -> dict:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    return {
        "type": "compute_job",
        "generation": "generation-1",
        "job_id": str(uuid.uuid4()),
        "attempt_id": str(uuid.uuid4()),
        "operation": operation,
        "payload_base64": base64.b64encode(encoded).decode(),
        "payload_sha256": digest or hashlib.sha256(encoded).hexdigest(),
        "timeout_ms": 20_000,
    }


def run(worker: Path, value: dict) -> dict:
    result = subprocess.run([worker], input=json.dumps(value).encode() + b"\n", stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=False)
    assert len(result.stdout) <= 12 * 1024 * 1024
    response = json.loads(result.stdout)
    assert response["job_id"] == value["job_id"] and response["attempt_id"] == value["attempt_id"]
    return response


def jpeg_size(data: bytes) -> tuple[int, int]:
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in {0xC0, 0xC1, 0xC2}:
            return struct.unpack(">HH", data[index + 5:index + 9])[::-1]
        if marker in {0xD8, 0xD9}:
            index += 2
        else:
            size = struct.unpack(">H", data[index + 2:index + 4])[0]
            index += 2 + size
    raise AssertionError("JPEG dimensions missing")


def orient_right(jpeg: bytes) -> bytes:
    tiff = b"II\x2a\x00\x08\x00\x00\x00\x01\x00\x12\x01\x03\x00\x01\x00\x00\x00\x06\x00\x00\x00\x00\x00\x00\x00"
    app1 = b"Exif\x00\x00" + tiff
    return jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1 + jpeg[2:]


def main() -> None:
    worker = Path(sys.argv[1])
    source = png(128, 64)
    image = run(worker, frame("image_resize_v1", {
        "data_base64": base64.b64encode(source).decode(), "mime_type": "image/png", "max_edge": 64, "quality": 80,
    }))
    assert image["ok"] is True and image["result"]["mime_type"] == "image/jpeg"
    rendered = base64.b64decode(image["result"]["data_base64"], validate=True)
    assert jpeg_size(rendered) == (64, 32)
    assert hashlib.sha256(rendered).hexdigest() == image["result"]["sha256"]
    assert b"Exif" not in rendered

    large = run(worker, frame("image_resize_v1", {
        "data_base64": base64.b64encode(png(256, 128)).decode(), "mime_type": "image/png", "max_edge": 128, "quality": 90,
    }))
    rotated_input = orient_right(base64.b64decode(large["result"]["data_base64"], validate=True))
    rotated = run(worker, frame("image_resize_v1", {
        "data_base64": base64.b64encode(rotated_input).decode(), "mime_type": "image/jpeg", "max_edge": 64, "quality": 80,
    }))
    assert jpeg_size(base64.b64decode(rotated["result"]["data_base64"], validate=True)) == (32, 64)

    csv = run(worker, frame("csv_render_v1", {"headers": ["name", "note"], "rows": [["Alice", "hello, world"], ["Bob", 'a "quote"'], ["Eve", "=kept"]]}))
    data = base64.b64decode(csv["result"]["data_base64"], validate=True)
    assert data == b'\xef\xbb\xbfname,note\r\nAlice,"hello, world"\r\nBob,"a ""quote"""\r\nEve,=kept\r\n'
    assert csv["result"]["mime_type"] == "text/csv; charset=utf-8"

    boundary = frame("csv_render_v1", {"headers": ["x" * (6 * 1024 * 1024 - 128)], "rows": []})
    boundary_wire = json.dumps(boundary).encode()
    assert 8 * 1024 * 1024 < len(boundary_wire) <= 9 * 1024 * 1024
    boundary_result = run(worker, boundary)
    assert boundary_result["ok"] is True

    bad_hash = run(worker, frame("csv_render_v1", {"headers": ["x"], "rows": [["y"]]}, "0" * 64))
    assert bad_hash["ok"] is False and bad_hash["error"] == "payload_hash"
    bad_bound = run(worker, frame("image_resize_v1", {"data_base64": base64.b64encode(source).decode(), "mime_type": "image/png", "max_edge": 63, "quality": 80}))
    assert bad_bound["ok"] is False and bad_bound["error"] == "invalid_payload"
    print("PASS: real ImageIO resize, exact CSV and worker validation")


if __name__ == "__main__":
    main()
