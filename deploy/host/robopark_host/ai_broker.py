"""Root Unix broker for bounded local inference, sandboxing, and typed control."""

from __future__ import annotations

import json
import os
import signal
import socket
import socketserver
import stat
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.request
from contextlib import suppress
from http.server import BaseHTTPRequestHandler

MAX_BODY = 65536
CONTEXT_TOKENS = 8192
RESULT_MARKER = b"__ROBOPARK_RESULT__="
class BrokerError(ValueError):
    pass


def _strict_json(value):
    def invalid(_value):
        raise ValueError("non_finite_json")

    return json.loads(value, parse_constant=invalid)


def authorize_peer(uid: int) -> bool:
    return uid in {0, 10001}


def _run_container(argv, *, input_bytes, timeout, max_output):
    process = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    stdout, stderr = bytearray(), bytearray()

    def drain(stream, retained):
        while chunk := stream.read(4096):
            remaining = max_output + 1 - len(retained)
            if remaining > 0:
                retained.extend(chunk[:remaining])

    readers = [
        threading.Thread(target=drain, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, stderr), daemon=True),
    ]
    try:
        assert process.stdin is not None
        process.stdin.write(input_bytes)
        process.stdin.close()
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + timeout
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)
            name = argv[argv.index("--name") + 1]
            subprocess.run(
                ["docker", "rm", "-f", name], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=5, check=False,
            )
            raise BrokerError("sandbox_timeout")
        for reader in readers:
            reader.join(timeout=2)
    finally:
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
    if len(stdout) > max_output or len(stderr) > max_output:
        raise BrokerError("sandbox_output_limit")
    return process.returncode, bytes(stdout), bytes(stderr)


class Sandbox:
    def __init__(self, image: str, *, run=_run_container):
        if not image.startswith("sha256:") or len(image) != 71:
            raise BrokerError("sandbox_image_invalid")
        self.image, self.run = image, run

    def execute(self, source: str, input_value):
        from uuid import uuid4

        if not isinstance(source, str):
            raise BrokerError("sandbox_source_invalid")
        request = json.dumps({"source": source, "input": input_value}, ensure_ascii=False).encode()
        if len(request) > MAX_BODY:
            raise BrokerError("request_too_large")
        wrapper = (
            "import json,sys\n"
            "p=json.load(sys.stdin); ns={'__builtins__':__builtins__}\n"
            "exec(compile(p['source'],'<automation>','exec'),ns,ns)\n"
            "fn=ns.get('main'); out=fn(p['input']) if callable(fn) else (_ for _ in ()).throw(ValueError('main_required'))\n"
            "print('__ROBOPARK_RESULT__='+json.dumps(out,separators=(',',':'),ensure_ascii=False,allow_nan=False))\n"
        )
        argv = [
            "docker", "run", "--rm", "--name", "robopark-ai-sandbox-" + uuid4().hex,
            "-i", "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "32",
            "--memory", "256m", "--cpus", "1", "--user", "65534:65534",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m", "--entrypoint", "python",
            self.image, "-I", "-c", wrapper,
        ]
        code, stdout, _stderr = self.run(argv, input_bytes=request, timeout=20, max_output=MAX_BODY)
        if code:
            raise BrokerError("sandbox_failed")
        lines = stdout.splitlines(keepends=True)
        matches = [line for line in lines if line.startswith(RESULT_MARKER)]
        if len(matches) != 1:
            raise BrokerError("sandbox_result_invalid")
        try:
            output = _strict_json(matches[0][len(RESULT_MARKER):])
        except (UnicodeError, ValueError) as error:
            raise BrokerError("sandbox_result_invalid") from error
        printed = b"".join(line for line in lines if not line.startswith(RESULT_MARKER))
        return {"output": output, "stdout": printed.decode("utf-8", "replace")}


def resolve_app_image(paths) -> str:
    document = json.loads((paths.state / "current-compose.json").read_text())
    image = document["services"]["api"]["image"]
    if not isinstance(image, str) or not image.startswith("sha256:") or len(image) != 71:
        raise BrokerError("sandbox_image_invalid")
    return image


def bounded_chat_body(body: bytes) -> bytes:
    try:
        payload = _strict_json(body)
        messages = payload["messages"]
        if (
            not isinstance(payload, dict)
            or not isinstance(messages, list)
            or not 1 <= len(messages) <= 64
            or any(
                not isinstance(message, dict)
                or message.get("role") not in {"system", "user", "assistant"}
                or not isinstance(message.get("content"), str)
                for message in messages
            )
        ):
            raise ValueError()
        requested = payload.get("max_tokens", 1024)
        if type(requested) is not int or requested < 1:
            raise ValueError()
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise BrokerError("chat_request_invalid") from error
    bounded = {
        "messages": messages,
        "max_tokens": min(requested, 2048),
        "stream": False,
        "reasoning_effort": "none",
    }
    for key in ("temperature", "top_p", "stop", "seed"):
        if key in payload:
            bounded[key] = payload[key]
    result = json.dumps(bounded, ensure_ascii=False, separators=(",", ":")).encode()
    if len(result) > MAX_BODY:
        raise BrokerError("request_too_large")
    return result


def _native_json(paths, path: str, payload: dict, *, timeout: int = 15) -> dict:
    from .ai_runtime import read_api_key

    request = urllib.request.Request(
        "http://127.0.0.1:18081" + path,
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + read_api_key(paths),
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read(MAX_BODY + 1)
    if len(data) > MAX_BODY:
        raise BrokerError("context_limit")
    try:
        value = _strict_json(data)
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise BrokerError("native_response_invalid") from error
    if not isinstance(value, dict):
        raise BrokerError("native_response_invalid")
    return value


def _context_token_count(paths, payload: dict) -> int:
    template = _native_json(paths, "/apply-template", payload)
    prompt = template.get("prompt")
    if not isinstance(prompt, str):
        raise BrokerError("native_response_invalid")
    tokenized = _native_json(paths, "/tokenize", {"content": prompt})
    tokens = tokenized.get("tokens")
    if not isinstance(tokens, list):
        raise BrokerError("native_response_invalid")
    return len(tokens)


def _ensure_context_fits(paths, payload: dict) -> None:
    if _context_token_count(paths, payload) + payload["max_tokens"] > CONTEXT_TOKENS:
        raise BrokerError("context_limit")


def _chat_token_count(paths, body: bytes):
    payload = _strict_json(bounded_chat_body(body))
    return {
        "prompt_tokens": _context_token_count(paths, payload),
        "context_tokens": CONTEXT_TOKENS,
    }


def _forward_chat(paths, body: bytes):
    from .ai_runtime import read_api_key

    bounded = bounded_chat_body(body)
    _ensure_context_fits(paths, _strict_json(bounded))
    request = urllib.request.Request(
        "http://127.0.0.1:18081/v1/chat/completions",
        data=bounded,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + read_api_key(paths),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read(MAX_BODY + 1)
            if len(data) > MAX_BODY:
                raise BrokerError("response_too_large")
            return response.status, data
    except urllib.error.HTTPError as error:
        data = error.read(MAX_BODY + 1)
        return error.code, data[:MAX_BODY]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _reply(self, status, payload):
        data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        try:
            size = int(self.headers.get("Content-Length", "-1"))
        except ValueError as error:
            raise BrokerError("request_invalid") from error
        if not 0 <= size <= MAX_BODY:
            raise BrokerError("request_too_large")
        return self.rfile.read(size)

    def do_GET(self):
        if self.path != "/health":
            self._reply(404, {"error": "not_found"})
        else:
            self._reply(200, {"ok": True})

    def do_POST(self):
        try:
            body = self._body()
            if self.path == "/v1/chat/completions":
                status, data = _forward_chat(self.server.paths, body)
                self._reply(status, data)
                return
            if self.path == "/v1/chat/tokens":
                self._reply(200, _chat_token_count(self.server.paths, body))
                return
            payload = _strict_json(body)
            if self.path == "/sandbox":
                if set(payload) != {"source", "input"}:
                    raise BrokerError("request_invalid")
                self._reply(200, Sandbox(resolve_app_image(self.server.paths)).execute(payload["source"], payload["input"]))
            elif self.path == "/control":
                if set(payload) != {"action"} or payload["action"] not in {"enable", "disable", "install", "remove_model"}:
                    raise BrokerError("request_invalid")
                from .ai_runtime import control
                from .updater import SystemRunner
                self._reply(200, control(self.server.paths, payload["action"], SystemRunner()))
            else:
                self._reply(404, {"error": "not_found"})
        except (BrokerError, ValueError, KeyError, json.JSONDecodeError) as error:
            self._reply(400, {"error": str(error) if str(error) else "request_invalid"})
        except (OSError, RuntimeError):
            self._reply(503, {"error": "ai_unavailable"})

    def log_message(self, *_args):
        return


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, path, paths):
        self.paths = paths
        super().__init__(path, Handler)

    def verify_request(self, request, _client_address):
        _pid, uid, _gid = struct.unpack("3i", request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
        return authorize_peer(uid)


def run_broker(paths):
    if os.geteuid() != 0 or not hasattr(socket, "SO_PEERCRED"):
        raise ValueError("ai_broker_requires_linux_root")
    directory = paths.root / "run/robopark-ai"
    directory.mkdir(parents=True, exist_ok=True, mode=0o750)
    socket_path = directory / "broker.sock"
    if socket_path.exists() or socket_path.is_symlink():
        if not stat.S_ISSOCK(socket_path.lstat().st_mode):
            raise ValueError("ai_broker_unsafe_socket")
        socket_path.unlink()
    server = Server(str(socket_path), paths)
    os.chown(socket_path, 0, 10001)
    socket_path.chmod(0o660)
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(
            signum,
            lambda *_args: threading.Thread(target=server.shutdown, daemon=True).start(),
        )
    stopped = threading.Event()

    def publish_runtime():
        from .ai_runtime import refresh_runtime_status

        while not stopped.is_set():
            with suppress(OSError, RuntimeError, ValueError):
                def unit_active(name):
                    return subprocess.run(
                        ["systemctl", "is-active", "--quiet", name],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, timeout=5, check=False,
                    ).returncode == 0

                refresh_runtime_status(
                    paths,
                    enabled=unit_active("robopark-ai.service"),
                    installing=unit_active("robopark-ai-setup.service"),
                )
            stopped.wait(30)

    status_thread = threading.Thread(target=publish_runtime, daemon=True)
    status_thread.start()
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        stopped.set()
        status_thread.join(timeout=35)
        server.server_close()
        with suppress(FileNotFoundError):
            socket_path.unlink()
    return 0
