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
