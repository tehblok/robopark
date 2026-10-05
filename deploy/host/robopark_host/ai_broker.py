"""Root Unix broker for bounded local inference, sandboxing, and typed control."""

from __future__ import annotations

import json
import math
import os
import selectors
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
MAX_TOOLS = 16
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
    started = time.monotonic()
    process = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        start_new_session=True,
    )
    stdout, stderr = bytearray(), bytearray()
    selector = None
    streams = {}
    open_fds = set()
    input_offset = 0

    def register(stream, events, kind):
        fd = stream.fileno()
        os.set_blocking(fd, False)
        streams[fd] = (stream, kind)
        open_fds.add(fd)
        selector.register(fd, events, kind)

    def close_fd(fd):
        if fd not in open_fds:
            return
        open_fds.remove(fd)
        with suppress(KeyError, ValueError):
            selector.unregister(fd)
        stream, _kind = streams[fd]
        with suppress(OSError, ValueError):
            stream.close()

    reason = None
    force_cleanup = False
    try:
        selector = selectors.DefaultSelector()
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        register(process.stdout, selectors.EVENT_READ, "stdout")
        register(process.stderr, selectors.EVENT_READ, "stderr")
        if input_bytes:
            register(process.stdin, selectors.EVENT_WRITE, "stdin")
        else:
            process.stdin.close()
        deadline = started + timeout
        while True:
            if len(stdout) > max_output or len(stderr) > max_output:
                reason = "output"
                break
            if process.poll() is not None and not open_fds:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                reason = "timeout"
                break
            try:
                events = selector.select(min(0.05, remaining))
            except OSError:
                reason = "io"
                break
            for key, _events in events:
                fd, kind = key.fd, key.data
                try:
                    if kind == "stdin":
                        written = os.write(fd, input_bytes[input_offset : input_offset + 65536])
                        if written <= 0:
                            reason = "io"
                            break
                        input_offset += written
                        if input_offset == len(input_bytes):
                            close_fd(fd)
                    else:
                        chunk = os.read(fd, 4096)
                        if not chunk:
                            close_fd(fd)
                            continue
                        retained = stdout if kind == "stdout" else stderr
                        available = max_output + 1 - len(retained)
                        if available > 0:
                            retained.extend(chunk[:available])
                except BlockingIOError:
                    continue
                except BrokenPipeError:
                    close_fd(fd)
                except OSError:
                    reason = "io"
                    break
            if reason is not None:
                break
        if reason is not None:
            force_cleanup = True
        if force_cleanup:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        if process.poll() is None:
            process.wait(timeout=2)
    except BaseException:
        force_cleanup = True
        raise
    finally:
        if force_cleanup or process.poll() is None or open_fds:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=2)
        for fd in tuple(open_fds):
            close_fd(fd)
        # Also close pipes whose registration was never reached after an error.
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                with suppress(OSError, ValueError):
                    stream.close()
        if selector is not None:
            selector.close()
        if force_cleanup:
            try:
                name = argv[argv.index("--name") + 1]
            except (ValueError, IndexError):
                name = None
            if name is not None:
                with suppress(OSError, subprocess.TimeoutExpired):
                    subprocess.run(
                        ["docker", "rm", "-f", name], stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=5, check=False,
                    )
    if reason == "timeout":
        raise BrokerError("sandbox_timeout")
    if reason == "output":
        raise BrokerError("sandbox_output_limit")
    if reason == "io":
        raise BrokerError("sandbox_failed")
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


def _bounded_json(value, *, depth=0):
    if depth > 8:
        raise ValueError("json_depth")
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str) and len(value.encode("utf-8")) > 16384:
            raise ValueError("json_string")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non_finite_json")
        return
    if isinstance(value, list):
        if len(value) > 128:
            raise ValueError("json_items")
        for item in value:
            _bounded_json(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 128 or any(not isinstance(key, str) for key in value):
            raise ValueError("json_object")
        for key, item in value.items():
            _bounded_json(key, depth=depth + 1)
            _bounded_json(item, depth=depth + 1)
        return
    raise ValueError("json_type")


def _function_call(call, tool_names):
    if not isinstance(call, dict) or set(call) != {"id", "type", "function"}:
        raise ValueError("tool_call")
    function = call["function"]
    if (
        call["type"] != "function"
        or not isinstance(call["id"], str)
        or not 1 <= len(call["id"]) <= 128
        or not isinstance(function, dict)
        or set(function) != {"name", "arguments"}
        or not isinstance(function["name"], str)
        or function["name"] not in tool_names
        or not isinstance(function["arguments"], str)
        or len(function["arguments"].encode("utf-8")) > 16384
    ):
        raise ValueError("tool_call")
    arguments = _strict_json(function["arguments"])
    if not isinstance(arguments, dict):
        raise TypeError("tool_arguments")
    _bounded_json(arguments)


def _bounded_tools(payload):
    if "tools" not in payload:
        return None, set()
    tools = payload["tools"]
    if not isinstance(tools, list) or not 1 <= len(tools) <= MAX_TOOLS:
        raise ValueError("tools")
    names = set()
    for tool in tools:
        if not isinstance(tool, dict) or set(tool) != {"type", "function"}:
            raise ValueError("tool")
        function = tool["function"]
        if (
            tool["type"] != "function"
            or not isinstance(function, dict)
            or not {"name", "parameters"} <= set(function) <= {
                "name",
                "description",
                "parameters",
            }
            or not isinstance(function["name"], str)
            or not 1 <= len(function["name"]) <= 64
            or any(
                character
                not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
                for character in function["name"]
            )
            or function["name"] in names
            or not isinstance(function.get("description", ""), str)
            or len(function.get("description", "").encode("utf-8")) > 4096
            or not isinstance(function["parameters"], dict)
        ):
            raise ValueError("tool")
        _bounded_json(function["parameters"])
        names.add(function["name"])
    return tools, names


def _bounded_messages(messages, tool_names):
    if not isinstance(messages, list) or not 1 <= len(messages) <= 64:
        raise ValueError("messages")
    normalized = []
    pending_call_id = None
    seen_call_ids = set()
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise TypeError("message")
        role = message.get("role")
        if role in {"system", "user"}:
            if set(message) != {"role", "content"} or not isinstance(
                message["content"], str
            ):
                raise ValueError("message")
            if role == "system" and index != 0:
                raise ValueError("system_message")
            if pending_call_id is not None:
                raise ValueError("missing_tool_result")
            normalized.append(message)
            continue
        if role == "assistant":
            if index == 0 or messages[index - 1].get("role") not in {"user", "tool"}:
                raise ValueError("assistant_sequence")
            if "tool_calls" not in message:
                if set(message) != {"role", "content"} or not isinstance(
                    message["content"], str
                ):
                    raise ValueError("message")
                normalized.append(message)
                continue
            if set(message) != {"role", "content", "tool_calls"}:
                raise ValueError("message")
            content = message["content"]
            calls = message["tool_calls"]
            if content is not None and not isinstance(content, str):
                raise ValueError("message")
            if not isinstance(calls, list) or len(calls) != 1:
                raise ValueError("tool_calls")
            _function_call(calls[0], tool_names)
            pending_call_id = calls[0]["id"]
            if pending_call_id in seen_call_ids:
                raise ValueError("reused_tool_call_id")
            seen_call_ids.add(pending_call_id)
            normalized.append({**message, "content": "" if content is None else content})
            continue
        if role == "tool":
            if (
                set(message) != {"role", "content", "tool_call_id"}
                or not isinstance(message["content"], str)
                or not isinstance(message["tool_call_id"], str)
                or message["tool_call_id"] != pending_call_id
            ):
                raise ValueError("tool_result")
            pending_call_id = None
            normalized.append(message)
            continue
        raise ValueError("message_role")
    if pending_call_id is not None or normalized[-1]["role"] not in {"user", "tool"}:
        raise ValueError("message_end")
    if sum(message["role"] == "system" for message in normalized) > 1:
        raise ValueError("system_message")
    return normalized


def bounded_chat_body(body: bytes) -> bytes:
    try:
        payload = _strict_json(body)
        if not isinstance(payload, dict):
            raise TypeError("payload")
        messages = payload["messages"]
        tools, tool_names = _bounded_tools(payload)
        messages = _bounded_messages(messages, tool_names)
        requested = payload.get("max_tokens", 1024)
        if type(requested) is not int or requested < 1:
            raise ValueError("max_tokens")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise BrokerError("chat_request_invalid") from error
    bounded = {
        "messages": messages,
        "max_tokens": min(requested, 2048),
        "stream": False,
        "reasoning_effort": "none",
    }
    if tools is not None:
        bounded["tools"] = tools
        bounded["parallel_tool_calls"] = False
    temperature = payload.get("temperature")
    if temperature is not None:
        if (
            isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or not math.isfinite(temperature)
            or not 0 <= temperature <= 2
        ):
            raise BrokerError("chat_request_invalid")
        bounded["temperature"] = temperature
    top_p = payload.get("top_p")
    if top_p is not None:
        if (
            isinstance(top_p, bool)
            or not isinstance(top_p, (int, float))
            or not math.isfinite(top_p)
            or not 0 < top_p <= 1
        ):
            raise BrokerError("chat_request_invalid")
        bounded["top_p"] = top_p
    stop = payload.get("stop")
    if stop is not None:
        if isinstance(stop, str):
            valid_stop = len(stop.encode("utf-8")) <= 128
        else:
            valid_stop = (
                isinstance(stop, list)
                and 1 <= len(stop) <= 4
                and all(
                    isinstance(item, str) and len(item.encode("utf-8")) <= 128
                    for item in stop
                )
            )
        if not valid_stop:
            raise BrokerError("chat_request_invalid")
        bounded["stop"] = stop
    if "seed" in payload:
        seed = payload["seed"]
        if type(seed) is not int or not -(2**31) <= seed < 2**31:
            raise BrokerError("chat_request_invalid")
        bounded["seed"] = seed
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
