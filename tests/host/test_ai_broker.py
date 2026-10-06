import json
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace

import pytest


def test_sandbox_uses_fixed_image_and_strict_isolation(host_paths):
    from robopark_host.ai_broker import Sandbox

    seen = []

    def run(argv, *, input_bytes, timeout, max_output):
        seen.append((argv, input_bytes, timeout, max_output))
        return 0, b'printed\n__ROBOPARK_RESULT__={"ok":true}\n', b""

    result = Sandbox("sha256:" + "a" * 64, run=run).execute("def main(data): return {'ok': True}", {"x": 1})
    argv, stdin, timeout, max_output = seen[0]
    assert argv[:3] == ["docker", "run", "--rm"]
    for pair in (["--network", "none"], ["--cap-drop", "ALL"], ["--pids-limit", "32"], ["--memory", "256m"], ["--cpus", "1"], ["--user", "65534:65534"]):
        assert argv[argv.index(pair[0]):argv.index(pair[0]) + 2] == pair
    assert "--read-only" in argv and ["--security-opt", "no-new-privileges"] == argv[argv.index("--security-opt"):argv.index("--security-opt") + 2]
    assert not any(str(host_paths.root) in value for value in argv)
    assert timeout == 20 and max_output == 65536
    assert json.loads(stdin)["input"] == {"x": 1}
    assert result == {"output": {"ok": True}, "stdout": "printed\n"}


def test_sandbox_rejects_oversized_input_before_docker():
    from robopark_host.ai_broker import BrokerError, Sandbox

    with pytest.raises(BrokerError, match="request_too_large"):
        Sandbox("sha256:" + "a" * 64, run=lambda *a, **k: pytest.fail("docker reached")).execute("def main(data): return data", {"x": "z" * 65536})


def test_sandbox_top_level_import_constants_and_helpers_share_execution_namespace():
    from robopark_host.ai_broker import Sandbox

    def run(argv, *, input_bytes, timeout, max_output):
        completed = subprocess.run(
            [sys.executable, "-I", "-c", argv[-1]],
            input=input_bytes,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return (
            completed.returncode,
            completed.stdout[: max_output + 1],
            completed.stderr,
        )

    source = """
import math
OFFSET = 2

def adjusted(value):
    return math.floor(value) + OFFSET

def main(data):
    return {"value": adjusted(data["value"])}
"""
    result = Sandbox("sha256:" + "a" * 64, run=run).execute(source, {"value": 3.8})
    assert result == {"output": {"value": 5}, "stdout": ""}


@pytest.mark.parametrize("uid", [1, 65534, 10002])
def test_broker_rejects_non_app_peer(uid):
    from robopark_host.ai_broker import authorize_peer

    assert authorize_peer(uid) is False


def test_broker_accepts_only_root_and_app_peer():
    from robopark_host.ai_broker import authorize_peer

    assert authorize_peer(0) is True
    assert authorize_peer(10001) is True


def test_chat_admission_is_four_active_eight_queued_and_fail_fast_when_full():
    from robopark_host.ai_broker import Admission, BrokerOverloaded

    admission = Admission(active_limit=4, queued_limit=8, wait_timeout=1)
    active = [admission.request() for _ in range(4)]
    for request in active:
        request.__enter__()
    release = threading.Event()

    def wait_for_slot():
        try:
            with admission.request():
                release.wait(1)
        except BrokerOverloaded:
            pass

    threads = [threading.Thread(target=wait_for_slot) for _ in range(8)]
    for thread in threads:
        thread.start()
    for _ in range(100):
        if admission.snapshot()["queued_requests"] == 8:
            break
        threading.Event().wait(0.005)
    assert admission.snapshot() == {
        "parallel_slots": 4,
        "context_tokens_per_slot": 8192,
        "total_context_tokens": 32768,
        "active_requests": 4,
        "queued_requests": 8,
        "max_queued_requests": 8,
    }
    with pytest.raises(BrokerOverloaded) as error, admission.request():
        pass
    assert error.value.status == 429
    for request in active:
        request.__exit__(None, None, None)
    release.set()
    for thread in threads:
        thread.join(timeout=2)
    assert not any(thread.is_alive() for thread in threads)


def test_chat_admission_timeout_is_service_unavailable():
    from robopark_host.ai_broker import Admission, BrokerOverloaded

    admission = Admission(active_limit=1, queued_limit=1, wait_timeout=0.01)
    with (
        admission.request(),
        pytest.raises(BrokerOverloaded) as error,
        admission.request(),
    ):
        pass
    assert error.value.status == 503


def test_chat_forward_is_nonstreaming_bounded_and_disables_reasoning():
    from robopark_host.ai_broker import bounded_chat_body

    body = bounded_chat_body(json.dumps({
        "model": "client-choice",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 999999,
        "stream": True,
        "reasoning_effort": "high",
    }).encode())
    assert json.loads(body) == {
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 2048,
        "stream": False,
        "reasoning_effort": "none",
    }


def test_chat_forward_preserves_bounded_tools_and_paired_tool_result():
    from robopark_host.ai_broker import bounded_chat_body

    tools = [{
        "type": "function",
        "function": {
            "name": "get_robot",
            "description": "Read one robot",
            "parameters": {
                "type": "object",
                "properties": {"robot_id": {"type": "integer"}},
            },
        },
    }]
    messages = [
        {"role": "system", "content": "Use tools safely."},
        {"role": "user", "content": "Robot 7"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "get_robot", "arguments": '{"robot_id":7}'},
            }],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": '{"name":"R7"}'},
    ]

    body = bounded_chat_body(json.dumps({"messages": messages, "tools": tools}).encode())

    assert json.loads(body) == {
        "messages": [
            messages[0],
            messages[1],
            {**messages[2], "content": ""},
            messages[3],
        ],
        "tools": tools,
        "parallel_tool_calls": False,
        "max_tokens": 1024,
        "stream": False,
        "reasoning_effort": "none",
    }


@pytest.mark.parametrize(
    "messages",
    [
        [
            {"role": "user", "content": "Robot 7"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_robot", "arguments": "{}"},
                }],
            },
        ],
        [
            {"role": "user", "content": "Robot 7"},
            {"role": "tool", "tool_call_id": "call_1", "content": "{}"},
        ],
        [
            {"role": "system", "content": "one"},
            {"role": "system", "content": "two"},
            {"role": "user", "content": "Robot 7"},
        ],
        [
            {"role": "user", "content": "Robot 7"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_robot", "arguments": "[]"},
                }],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "{}"},
        ],
        [
            {"role": "user", "content": "Robot 7"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_robot", "arguments": "{}"},
                    },
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {"name": "get_robot", "arguments": "{}"},
                    },
                ],
            },
        ],
    ],
    ids=[
        "dangling-call",
        "orphan-result",
        "repeated-system",
        "non-object-arguments",
        "parallel-calls",
    ],
)
def test_chat_forward_rejects_invalid_tool_message_sequences(messages):
    from robopark_host.ai_broker import BrokerError, bounded_chat_body

    tools = [{
        "type": "function",
        "function": {"name": "get_robot", "parameters": {"type": "object"}},
    }]

    with pytest.raises(BrokerError, match="chat_request_invalid"):
        bounded_chat_body(json.dumps({"messages": messages, "tools": tools}).encode())


def test_chat_forward_rejects_more_than_sixteen_tools():
    from robopark_host.ai_broker import BrokerError, bounded_chat_body

    tools = [
        {
            "type": "function",
            "function": {"name": f"tool_{index}", "parameters": {"type": "object"}},
        }
        for index in range(17)
    ]

    with pytest.raises(BrokerError, match="chat_request_invalid"):
        bounded_chat_body(json.dumps({
            "messages": [{"role": "user", "content": "hello"}],
            "tools": tools,
        }).encode())


def test_chat_forward_rejects_reused_tool_call_id():
    from robopark_host.ai_broker import BrokerError, bounded_chat_body

    tools = [{
        "type": "function",
        "function": {"name": "get_robot", "parameters": {"type": "object"}},
    }]
    messages = [
        {"role": "user", "content": "Robot 7"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "get_robot", "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "{}"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "get_robot", "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "{}"},
    ]

    with pytest.raises(BrokerError, match="chat_request_invalid"):
        bounded_chat_body(json.dumps({"messages": messages, "tools": tools}).encode())


def test_chat_token_count_sends_tools_to_native_template(host_paths, monkeypatch):
    from robopark_host import ai_broker

    secret = host_paths.var / "ai/api-key"
    secret.parent.mkdir(parents=True)
    secret.write_text("private-token" * 3)
    secret.chmod(0o600)
    seen = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return json.dumps(self.payload).encode()

    def open_request(request, timeout):
        seen.append((request.full_url, json.loads(request.data), timeout))
        if request.full_url.endswith("/apply-template"):
            return Response({"prompt": "formatted"})
        return Response({"tokens": [1, 2]})

    monkeypatch.setattr(ai_broker.urllib.request, "urlopen", open_request)
    tools = [{
        "type": "function",
        "function": {"name": "get_robot", "parameters": {"type": "object"}},
    }]

    assert ai_broker._chat_token_count(
        host_paths,
        json.dumps({
            "messages": [{"role": "user", "content": "hello"}],
            "tools": tools,
        }).encode(),
    ) == {"prompt_tokens": 2, "context_tokens": 8192}
    assert seen[0][1]["tools"] == tools
    assert seen[0][1]["parallel_tool_calls"] is False


def test_api_complete_reaches_real_broker_handler_and_native_contract(host_paths, monkeypatch):
    from robopark_api.services.ai.runtime import complete
    from robopark_host import ai_broker

    secret = host_paths.var / "ai/api-key"
    secret.parent.mkdir(parents=True)
    secret.write_text("private-token" * 3)
    secret.chmod(0o600)
    socket_dir = tempfile.TemporaryDirectory(prefix="rp-ai-", dir="/tmp")
    socket_path = socket_dir.name + "/broker.sock"
    seen = []

    class Response:
        status = 200

        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return json.dumps(self.payload).encode()

    def open_request(request, timeout):
        seen.append((request, timeout, json.loads(request.data)))
        if request.full_url.endswith("/apply-template"):
            return Response({"prompt": "formatted prompt"})
        if request.full_url.endswith("/tokenize"):
            return Response({"tokens": [1, 2, 3]})
        return Response({
            "choices": [{
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "  ready  "},
            }]
        })

    monkeypatch.setattr(ai_broker.urllib.request, "urlopen", open_request)
    server = ai_broker.Server(socket_path, host_paths)
    server.verify_request = lambda _request, _address: True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = complete(
            SimpleNamespace(ai_broker_socket=socket_path),
            [{"role": "user", "content": "hello"}],
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        socket_dir.cleanup()

    assert result == "ready"
    assert [request.full_url.rsplit("/", 1)[-1] for request, _, _ in seen] == [
        "apply-template", "tokenize", "completions",
    ]
    request, timeout, payload = seen[-1]
    assert request.full_url == "http://127.0.0.1:18081/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer " + "private-token" * 3
    assert timeout == 120
    assert payload == {
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 1400,
        "stream": False,
        "reasoning_effort": "none",
        "temperature": 0.2,
    }


def test_native_context_preflight_rejects_prompt_plus_completion_over_8192(host_paths, monkeypatch):
    from robopark_host import ai_broker

    secret = host_paths.var / "ai/api-key"
    secret.parent.mkdir(parents=True)
    secret.write_text("private-token" * 3)
    secret.chmod(0o600)
    requested = []

    class Response:
        status = 200

        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return json.dumps(self.payload).encode()

    def open_request(request, timeout):
        requested.append(request.full_url)
        if request.full_url.endswith("/apply-template"):
            return Response({"prompt": "formatted prompt"})
        return Response({"tokens": list(range(8000))})

    monkeypatch.setattr(ai_broker.urllib.request, "urlopen", open_request)
    with pytest.raises(ai_broker.BrokerError, match="context_limit"):
        ai_broker._forward_chat(host_paths, json.dumps({
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 200,
        }).encode())
    assert requested == [
        "http://127.0.0.1:18081/apply-template",
        "http://127.0.0.1:18081/tokenize",
    ]


def test_chat_token_count_uses_native_template_and_tokenizer(host_paths, monkeypatch):
    from robopark_host import ai_broker

    secret = host_paths.var / "ai/api-key"
    secret.parent.mkdir(parents=True)
    secret.write_text("private-token" * 3)
    secret.chmod(0o600)
    requested = []

    class Response:
        status = 200

        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return json.dumps(self.payload).encode()

    def open_request(request, timeout):
        requested.append(request.full_url)
        if request.full_url.endswith("/apply-template"):
            return Response({"prompt": "formatted prompt"})
        return Response({"tokens": [1, 2, 3, 4]})

    monkeypatch.setattr(ai_broker.urllib.request, "urlopen", open_request)
    assert ai_broker._chat_token_count(
        host_paths,
        json.dumps({"messages": [{"role": "user", "content": "hello"}]}).encode(),
    ) == {"prompt_tokens": 4, "context_tokens": 8192}
    assert requested == [
        "http://127.0.0.1:18081/apply-template",
        "http://127.0.0.1:18081/tokenize",
    ]


def test_native_forward_uses_private_api_key(host_paths, monkeypatch):
    from robopark_host import ai_broker

    secret = host_paths.var / "ai/api-key"
    secret.parent.mkdir(parents=True)
    secret.write_text("private-token" * 3)
    secret.chmod(0o600)
    seen = []

    class Response:
        status = 200
        def __init__(self, payload): self.payload = payload
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def read(self, _limit): return json.dumps(self.payload).encode()

    def open_request(request, timeout):
        seen.append((request, timeout))
        if request.full_url.endswith("/apply-template"):
            return Response({"prompt": "prompt"})
        if request.full_url.endswith("/tokenize"):
            return Response({"tokens": [1]})
        return Response({})

    monkeypatch.setattr(ai_broker.urllib.request, "urlopen", open_request)
    ai_broker._forward_chat(host_paths, json.dumps({
        "messages": [{"role": "user", "content": "hello"}]
    }).encode())
    assert len(seen) == 3
    assert all(
        request.headers["Authorization"] == "Bearer " + "private-token" * 3
        for request, _ in seen
    )


@pytest.mark.parametrize("request_bytes", [
    b"",
    b"GET /health HTTP/1.1\r\nHost:",
    b"POST /control HTTP/1.1\r\nHost: broker\r\nContent-Length: 100\r\n\r\n{",
    b"GET /health HTTP/1.1\r\nHost: broker\r\n\r\n",
])
def test_idle_connections_release_broker_handler_slots(host_paths, monkeypatch, request_bytes):
    import socket

    from robopark_host import ai_broker

    monkeypatch.setattr(ai_broker, "SOCKET_IO_TIMEOUT_SECONDS", 0.1, raising=False)
    monkeypatch.setattr(ai_broker, "MAX_HANDLER_THREADS", 1)
    finished = threading.Event()
    process_request_thread = ai_broker.Server.process_request_thread

    def observe_finished(self, request, address):
        try:
            process_request_thread(self, request, address)
        finally:
            finished.set()

    monkeypatch.setattr(ai_broker.Server, "process_request_thread", observe_finished)
    with tempfile.TemporaryDirectory(prefix="rp-ai-", dir="/tmp") as directory:
        path = directory + "/broker.sock"
        server = ai_broker.Server(path, host_paths)
        server.verify_request = lambda _request, _address: True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = socket.socket(socket.AF_UNIX)
        try:
            client.connect(path)
            client.sendall(request_bytes)
            assert finished.wait(1), "An abandoned client retained the only handler slot"
            with socket.socket(socket.AF_UNIX) as health:
                health.settimeout(1)
                health.connect(path)
                health.sendall(b"GET /health HTTP/1.1\r\nHost: broker\r\nConnection: close\r\n\r\n")
                with health.makefile("rb") as response:
                    assert response.read().startswith(b"HTTP/1.1 200")
        finally:
            client.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
