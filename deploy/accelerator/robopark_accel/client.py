"""Small stdlib client for a local host or a container with the socket mount."""

from __future__ import annotations

import http.client
import json
import socket


class Connection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: int = 310):
        super().__init__("localhost", timeout=timeout)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def request(path: str, route: str, payload: dict | None = None) -> dict:
    if route not in ("/health", "/v1/thumbnail", "/v1/infer"):
        raise ValueError("route_not_allowed")
    body = None if payload is None else json.dumps(payload, allow_nan=False).encode()
    if body is not None and len(body) > 12 * 1024 * 1024:
        raise ValueError("request_size_limit")
    connection = Connection(path)
    try:
        connection.request(
            "GET" if body is None else "POST", route, body, {"Content-Type": "application/json"}
        )
        response = connection.getresponse()
        data = response.read(12 * 1024 * 1024 + 1)
        if len(data) > 12 * 1024 * 1024:
            raise ValueError("response_size_limit")
        if response.status != 200:
            raise RuntimeError("accelerator_request_failed")
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError("response_invalid")
        return value
    finally:
        connection.close()
