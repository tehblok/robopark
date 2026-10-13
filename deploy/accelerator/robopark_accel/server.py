"""Local Unix socket API; accepts bytes and registered IDs, never client paths."""

from __future__ import annotations

import base64
import json
import os
import socket
import socketserver
import stat
import struct
import tempfile
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from . import media_backend, operations

MAX_BODY = 12 * 1024 * 1024
MAX_RAW = 8 * 1024 * 1024


def decode(value: str) -> bytes:
    if not isinstance(value, str) or len(value) > MAX_BODY:
        raise ValueError("input_invalid")
    data = base64.b64decode(value, validate=True)
    if not 0 < len(data) <= MAX_RAW:
        raise ValueError("input_size_limit")
    return data


def dispatch(state: Path, route: str, payload: dict) -> dict:
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="request-", dir=state) as temporary:
        work = Path(temporary)
        if route == "/v1/thumbnail":
            if set(payload) != {"jpeg", "width", "height", "backend"}:
                raise ValueError("thumbnail_request_invalid")
            (work / "source.jpg").write_bytes(decode(payload["jpeg"]))
            result = media_backend.thumbnail(
                work / "source.jpg",
                work / "thumbnail.jpg",
                payload["width"],
                payload["height"],
                backend=payload["backend"],
            )
            raw = result.output_path.read_bytes()
            if len(raw) > MAX_RAW:
                raise ValueError("output_size_limit")
            return {
                "jpeg": base64.b64encode(raw).decode("ascii"),
                "backend": result.backend,
                "size": result.size,
                "fallback_reason": result.fallback_reason,
            }
        if route == "/v1/infer":
            if set(payload) != {"engine_id", "inputs"} or not isinstance(payload["inputs"], dict):
                raise ValueError("inference_request_invalid")
            if not 1 <= len(payload["inputs"]) <= 16:
                raise ValueError("input_count_limit")
            inputs = {}
            for index, (name, encoded) in enumerate(payload["inputs"].items()):
                path = work / f"input-{index}.bin"
                path.write_bytes(decode(encoded))
                inputs[name] = path
            return operations.infer(state, payload["engine_id"], inputs, work / "result")
        raise ValueError("route_not_found")


class Server(socketserver.UnixStreamServer):
    request_queue_size = 4

    def verify_request(self, request, client_address):
        try:
            _pid, uid, _gid = struct.unpack(
                "3i", request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            )
            request.settimeout(10)
            return uid in (0, 10001, os.geteuid())
        except (OSError, AttributeError):
            return False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self, code, payload):
        raw = json.dumps(payload, default=str, allow_nan=False).encode()
        if len(raw) > MAX_BODY:
            code, raw = 413, b'{"error":"response_size_limit"}'
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/health":
            self.reply(200, {"ok": True, "model_ready": False, "execution": "serial"})
        else:
            self.reply(404, {"error": "not_found"})

    def do_POST(self):
        try:
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or self.headers.get("Transfer-Encoding"):
                raise ValueError("request_framing_invalid")
            size = int(lengths[0])
            if not 0 < size <= MAX_BODY:
                raise ValueError("request_size_limit")
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise ValueError("request_incomplete")
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError("request_invalid")
            self.reply(200, dispatch(self.server.state, self.path, body))
        except (ValueError, TypeError, KeyError):
            self.reply(400, {"error": "request_invalid"})
        except (OSError, RuntimeError):
            self.reply(503, {"error": "accelerator_unavailable"})


def serve(state: Path, address: Path) -> None:
    if os.geteuid() == 0:
        raise ValueError("run_as_robopark_accel_not_root")
    address.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    if address.exists() or address.is_symlink():
        info = address.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid():
            raise ValueError("unsafe_socket_path")
        # A live listener must not be displaced by a second service process.
        with socket.socket(socket.AF_UNIX) as test:
            try:
                test.connect(str(address))
            except ConnectionRefusedError:
                address.unlink()
            else:
                raise ValueError("socket_in_use")
    with Server(str(address), Handler) as server:
        server.state = state
        os.chmod(address, 0o660)
        server.serve_forever()
