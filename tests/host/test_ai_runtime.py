import hashlib
import json
import os
import struct
import sys
from copy import deepcopy
from pathlib import Path

import pytest


def _valid_smoke():
    return {"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "tool_calls": [{
        "id": "smoke-1", "type": "function", "function": {
            "name": "check_contract", "arguments": '{"ok":true}',
        },
    }]}}]}


def _gguf_value(value):
    if isinstance(value, str):
        encoded = value.encode()
        return struct.pack("<I Q", 8, len(encoded)) + encoded
    return struct.pack("<I I", 4, value)


def _gemma4_gguf(*, architecture="gemma4", template=None, metadata_overrides=None):
    template = template or (
        "{% if tools %}<|tool_call>{{ tools }}<|tool_response>{% endif %}"
    )
    metadata = {
        "general.architecture": architecture,
        "general.type": "model",
        "gemma4.block_count": 42,
        "gemma4.context_length": 131072,
        "gemma4.embedding_length": 2560,
        "gemma4.feed_forward_length": 10240,
        "gemma4.attention.head_count": 8,
        "gemma4.attention.head_count_kv": 2,
        "tokenizer.ggml.model": "gemma4",
        "tokenizer.chat_template": template,
    }
    metadata.update(metadata_overrides or {})
    parts = [b"GGUF", struct.pack("<I Q Q", 3, 666, len(metadata))]
    for key, value in metadata.items():
        encoded_key = key.encode()
        parts.extend((struct.pack("<Q", len(encoded_key)), encoded_key, _gguf_value(value)))
    return b"".join(parts)


@pytest.mark.parametrize("body", [None, [], {"choices": [{}]}, {"choices": [{"message": {"role": "assistant", "content": "OK"}}]}])
def test_readiness_rejects_plain_or_malformed_success_responses(body):
    from robopark_host.ai_runtime import _smoke_tool_response

    assert not _smoke_tool_response(body)


def test_readiness_requires_exact_synthetic_tool_and_arguments():
    from robopark_host.ai_runtime import _smoke_tool_response

    valid = _valid_smoke()
    assert _smoke_tool_response(valid)
    for arguments in ('{"ok":false}', '{"ok":1}', '{"ok":true,"extra":1}', "not json", "[]"):
        changed = deepcopy(valid)
        changed["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = arguments
        assert not _smoke_tool_response(changed)
    changed = deepcopy(valid)
    changed["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = "real_action"
    assert not _smoke_tool_response(changed)


def test_runtime_readiness_checks_system_and_tool_template_without_dispatch(host_paths, monkeypatch):
    from robopark_host import ai_runtime

    device = host_paths.root / "dev/nvidia0"
    device.parent.mkdir(parents=True)
    device.touch()
    monkeypatch.setattr(ai_runtime, "read_api_key", lambda _: "synthetic-test")
    monkeypatch.setattr(ai_runtime, "selected_model", lambda *args, **kwargs: ai_runtime._builtin_model())
    requests = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, limit):
            return json.dumps(_valid_smoke()).encode()[:limit]

    def request(value, *, timeout):
        requests.append(value)
        if value.full_url.endswith("/props"):
            response = Response()
            response.read = lambda limit: json.dumps({
                "total_slots": 4,
                "model_path": str(host_paths.var / "ai/models/active.gguf"),
                "default_generation_settings": {"n_ctx": 8192},
            }).encode()[:limit]
            return response
        return Response()

    monkeypatch.setattr(ai_runtime.urllib.request, "urlopen", request)
    assert ai_runtime.runtime_ready(host_paths) == (True, None)
    body = json.loads(requests[2].data)
    assert body["messages"][0]["role"] == "system"
    assert body["max_tokens"] == 64
    assert body["tool_choice"] == "required"
    assert len(body["tools"]) == 1 and body["parallel_tool_calls"] is False


def test_periodic_readiness_checks_health_and_identity_without_using_a_slot(
    host_paths, monkeypatch
):
    from robopark_host import ai_runtime

    device = host_paths.root / "dev/nvidia0"
    device.parent.mkdir(parents=True)
    device.touch()
    monkeypatch.setattr(ai_runtime, "read_api_key", lambda _: "synthetic-test")
    monkeypatch.setattr(ai_runtime, "selected_model", lambda *args, **kwargs: ai_runtime._builtin_model())
    monkeypatch.setattr(ai_runtime, "_runtime_process_identity", lambda paths: 42)
    ai_runtime._clear_startup_smoke_cache()
    requested = []

    class Response:
        status = 200

        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, limit):
            return json.dumps(self.payload).encode()[:limit]

    def open_request(request, timeout):
        requested.append(request.full_url)
        if request.full_url.endswith("/props"):
            return Response({
                "total_slots": 4,
                "model_path": str(host_paths.var / "ai/models/active.gguf"),
                "default_generation_settings": {"n_ctx": 8192},
            })
        if request.full_url.endswith("/v1/chat/completions"):
            return Response(_valid_smoke())
        return Response({"status": "ok"})

    monkeypatch.setattr(ai_runtime.urllib.request, "urlopen", open_request)

    assert ai_runtime.runtime_ready(host_paths) == (True, None)
    assert ai_runtime.runtime_ready(host_paths) == (True, None)
    assert requested.count("http://127.0.0.1:18081/v1/chat/completions") == 1
    assert requested.count("http://127.0.0.1:18081/health") == 2
    assert requested.count("http://127.0.0.1:18081/props") == 2


def test_runtime_readiness_distinguishes_server_startup_failure(host_paths, monkeypatch):
    from robopark_host import ai_runtime

    device = host_paths.root / "dev/nvidia0"
    device.parent.mkdir(parents=True)
    device.touch()
    monkeypatch.setattr(ai_runtime, "read_api_key", lambda _: "synthetic-test")
    monkeypatch.setattr(
        ai_runtime.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("refused")),
    )

    assert ai_runtime.runtime_ready(host_paths) == (False, "startup_unreachable")


def test_command_runner_retains_bounded_tail_without_failing_verbose_build():
    from robopark_host.ai_runtime import _run

    output = _run([
        sys.executable,
        "-c",
        "import sys; sys.stdout.write('x' * 100000 + 'tail-marker')",
    ])
    assert output.endswith("tail-marker")
    assert len(output.encode()) <= 65536


def _hardware(root: Path, *, model="NVIDIA Jetson AGX Orin Developer Kit", compatible="nvidia,p3701-0000\0nvidia,tegra234", memory_kib=32 * 1024**2):
    tree = root / "proc/device-tree"
    tree.mkdir(parents=True)
    (tree / "model").write_bytes(model.encode() + b"\0")
    (tree / "compatible").write_bytes(compatible.encode())
    proc = root / "proc"
    (proc / "meminfo").write_text(f"MemTotal:       {memory_kib} kB\n")


def test_runtime_pins_official_gemma_artifact_and_upstream_llama_cpp():
    from robopark_host import ai_runtime

    assert ai_runtime.MODEL_ID == "google/gemma-4-E4B-it-qat-q4_0-gguf"
    assert ai_runtime.MODEL_REVISION == "ce70163b5df4580cf3534f9f373f15c6c6a6c4e9"
    assert ai_runtime.MODEL_FILE == "gemma-4-E4B_q4_0-it.gguf"
    assert ai_runtime.MODEL_SIZE == 5_154_940_864
    assert ai_runtime.MODEL_SHA256 == (
        "09f6f2a1d9ff4a1b7db9cc1aad9c55a9df2f5ec133327a92eab593fcf4360ed0"
    )
    assert ai_runtime.LLAMA_REPOSITORY == "https://github.com/ggml-org/llama.cpp.git"
    assert ai_runtime.LLAMA_COMMIT == "c25030496079fdad724609d94f68b859f25774ce"


def test_status_reports_fixed_parallel_context_profile(host_paths):
    from robopark_host import ai_runtime

    status = ai_runtime.publish_status(host_paths, supported=True)

    assert status["model_family"] == "gemma-4-E4B"
    assert status["model"] == ai_runtime.MODEL_ID
    assert status["model_source"] == "builtin"
    assert status["runtime_installed"] is False
    assert status["model_available"] is False
    assert status["parallel_slots"] == 4
    assert status["context_tokens_per_slot"] == 8192
    assert status["total_context_tokens"] == 32768


def test_register_merged_gguf_is_verified_atomic_and_not_selected(
    host_paths, tmp_path, monkeypatch
):
    from robopark_host import ai_runtime

    source = tmp_path / "repair-tuned.gguf"
    source.write_bytes(_gemma4_gguf())
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(ai_runtime, "MIN_REGISTERED_MODEL_SIZE", 1)

    previous_umask = os.umask(0o077)
    try:
        receipt = ai_runtime.register_merged_gguf(
            host_paths,
            source.resolve(),
            sha256=digest,
            model_id="robopark/gemma-4-e4b-repair-v1",
        )
    finally:
        os.umask(previous_umask)

    assert receipt == {
        "schema": 1,
        "model": "robopark/gemma-4-e4b-repair-v1",
        "model_family": "gemma-4-E4B",
        "model_source": "registered",
        "model_file": f"registered/{digest}.gguf",
        "model_size": len(source.read_bytes()),
        "model_sha256": digest,
        "selected": False,
    }
    copied = host_paths.var / "ai/models" / receipt["model_file"]
    assert copied.read_bytes() == source.read_bytes()
    assert copied.stat().st_mode & 0o777 == 0o640
    with pytest.raises(ValueError, match="ai_model_not_selected"):
        ai_runtime.selected_model(host_paths)
    assert not (host_paths.var / "ai/models/active.gguf").exists()


@pytest.mark.parametrize(
    "contents,expected_sha,reason",
    [
        (b"nope", hashlib.sha256(b"nope").hexdigest(), "ai_model_not_gguf"),
        (_gemma4_gguf(), "0" * 64, "ai_model_checksum_mismatch"),
    ],
)
def test_register_merged_gguf_rejects_unverified_input(
    host_paths, tmp_path, monkeypatch, contents, expected_sha, reason
):
    from robopark_host import ai_runtime

    source = tmp_path / "candidate.gguf"
    source.write_bytes(contents)
    monkeypatch.setattr(ai_runtime, "MIN_REGISTERED_MODEL_SIZE", 1)

    with pytest.raises(ValueError, match=reason):
        ai_runtime.register_merged_gguf(
            host_paths,
            source.resolve(),
            sha256=expected_sha,
            model_id="robopark/gemma-4-e4b-repair-v1",
        )
    assert not list((host_paths.var / "ai/models/registered").glob("*.gguf"))


def test_register_rejects_wrong_gguf_architecture_before_publication(
    host_paths, tmp_path, monkeypatch
):
    from robopark_host import ai_runtime

    source = tmp_path / "other.gguf"
    source.write_bytes(_gemma4_gguf(architecture="llama"))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(ai_runtime, "MIN_REGISTERED_MODEL_SIZE", 1)

    with pytest.raises(ValueError, match="ai_model_architecture_invalid"):
        ai_runtime.register_merged_gguf(
            host_paths,
            source.resolve(),
            sha256=digest,
            model_id="robopark/not-gemma",
        )
    assert not (host_paths.var / "ai/models/registered" / f"{digest}.gguf").exists()


@pytest.mark.parametrize(
    "contents,reason",
    [
        (
            _gemma4_gguf(metadata_overrides={"gemma4.embedding_length": 2048}),
            "ai_model_architecture_invalid",
        ),
        (_gemma4_gguf(template="{% if tools %}plain text{% endif %}"),
         "ai_model_chat_template_invalid"),
    ],
)
def test_register_rejects_incompatible_gemma_shape_or_tool_template(
    host_paths, tmp_path, monkeypatch, contents, reason
):
    from robopark_host import ai_runtime

    source = tmp_path / "incompatible.gguf"
    source.write_bytes(contents)
    digest = hashlib.sha256(contents).hexdigest()
    monkeypatch.setattr(ai_runtime, "MIN_REGISTERED_MODEL_SIZE", 1)

    with pytest.raises(ValueError, match=reason):
        ai_runtime.register_merged_gguf(
            host_paths,
            source.resolve(),
            sha256=digest,
            model_id="robopark/incompatible-model",
        )


def test_select_revalidates_registered_gguf_before_stopping_service(
    host_paths, monkeypatch
):
    from robopark_host import ai_runtime

    monkeypatch.setattr(ai_runtime, "MIN_REGISTERED_MODEL_SIZE", 1)
    contents = _gemma4_gguf(architecture="llama")
    digest = hashlib.sha256(contents).hexdigest()
    model_id = "robopark/tampered-model"
    models = host_paths.var / "ai/models"
    target = models / "registered" / f"{digest}.gguf"
    target.parent.mkdir(parents=True)
    target.write_bytes(contents)
    registration = models / "registrations" / "candidate.json"
    registration.parent.mkdir()
    registration.write_text(json.dumps({
        "schema": 1,
        "model": model_id,
        "model_family": "gemma-4-E4B",
        "model_source": "registered",
        "model_file": f"registered/{digest}.gguf",
        "model_size": len(contents),
        "model_sha256": digest,
        "selected": False,
    }))
    calls = []
    runner = type("Runner", (), {"run": lambda self, argv, **kw: calls.append(argv)})()

    with pytest.raises(ValueError, match="ai_model_architecture_invalid"):
        ai_runtime.select_model(host_paths, model_id, runner)

    assert calls == []


def test_select_model_stops_switches_and_restarts_only_enabled_runtime(
    host_paths, tmp_path, monkeypatch
):
    from robopark_host import ai_runtime

    source = tmp_path / "repair-tuned.gguf"
    source.write_bytes(_gemma4_gguf())
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(ai_runtime, "MIN_REGISTERED_MODEL_SIZE", 1)
    ai_runtime.register_merged_gguf(
        host_paths,
        source.resolve(),
        sha256=digest,
        model_id="robopark/gemma-4-e4b-repair-v1",
    )
    ai_runtime.write_enabled_intent(host_paths, True)
    monkeypatch.setattr(ai_runtime, "_wait_runtime_ready", lambda paths: (True, None))
    monkeypatch.setattr(ai_runtime, "publish_status", lambda _paths, **values: values)
    calls = []
    runner = type("Runner", (), {"run": lambda self, argv, **kw: calls.append(argv)})()

    selected = ai_runtime.select_model(
        host_paths, "robopark/gemma-4-e4b-repair-v1", runner
    )

    assert calls == [
        ["systemctl", "stop", "robopark-ai.service"],
        ["systemctl", "start", "robopark-ai.service"],
    ]
    assert selected["selected"] is True
    assert selected["model_source"] == "registered"
    assert ai_runtime.selected_model(host_paths)["model_sha256"] == digest
    active = host_paths.var / "ai/models/active.gguf"
    assert active.is_symlink()
    assert active.resolve() == host_paths.var / "ai/models/registered" / f"{digest}.gguf"


@pytest.mark.parametrize("failure_mode", ["start", "readiness"])
def test_select_model_rolls_back_receipts_and_service_when_new_model_fails(
    host_paths, tmp_path, monkeypatch, failure_mode
):
    from robopark_host import ai_runtime

    monkeypatch.setattr(ai_runtime, "MIN_REGISTERED_MODEL_SIZE", 1)
    models = []
    for name in ("old", "new"):
        source = tmp_path / f"{name}.gguf"
        source.write_bytes(_gemma4_gguf(template=f"{name} <|tool_call> tools <|tool_response>"))
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        models.append(ai_runtime.register_merged_gguf(
            host_paths,
            source.resolve(),
            sha256=digest,
            model_id=f"robopark/gemma-4-e4b-{name}",
        ))
    previous = {**models[0], "selected": True}
    ai_runtime._activate_model(host_paths, previous)
    ai_runtime.write_enabled_intent(host_paths, True)
    readiness = iter(
        [(True, None)]
        if failure_mode == "start"
        else [(False, "smoke_failed"), (True, None)]
    )
    monkeypatch.setattr(ai_runtime, "_wait_runtime_ready", lambda paths: next(readiness))
    monkeypatch.setattr(ai_runtime, "installed", lambda paths, **kw: True)
    published = []
    monkeypatch.setattr(
        ai_runtime,
        "publish_status",
        lambda _paths, **values: published.append(values) or values,
    )
    calls = []

    class Runner:
        starts = 0

        def run(self, argv, **_kwargs):
            calls.append(argv)
            if argv[1] == "start":
                self.starts += 1
                if failure_mode == "start" and self.starts == 1:
                    raise RuntimeError("new model failed to start")

    runner = Runner()

    with pytest.raises(ValueError, match="ai_model_startup_failed"):
        ai_runtime.select_model(host_paths, models[1]["model"], runner)

    assert calls == [
        ["systemctl", "stop", "robopark-ai.service"],
        ["systemctl", "start", "robopark-ai.service"],
        ["systemctl", "stop", "robopark-ai.service"],
        ["systemctl", "start", "robopark-ai.service"],
    ]
    assert ai_runtime.selected_model(host_paths)["model"] == previous["model"]
    assert published == [{
        "supported": True,
        "installed": True,
        "enabled": True,
        "ready": True,
        "reason": None,
        "model_sha256": previous["model_sha256"],
        "backend": "cuda",
    }]


@pytest.mark.parametrize("failure_mode", ["partial_receipt", "disabled_publish"])
def test_disabled_model_selection_rolls_back_partial_publication(
    host_paths, tmp_path, monkeypatch, failure_mode
):
    from robopark_host import ai_runtime

    monkeypatch.setattr(ai_runtime, "MIN_REGISTERED_MODEL_SIZE", 1)
    models = []
    for name in ("old", "new"):
        contents = _gemma4_gguf(
            template=f"{name} <|tool_call> tools <|tool_response>"
        )
        source = tmp_path / f"{name}.gguf"
        source.write_bytes(contents)
        models.append(ai_runtime.register_merged_gguf(
            host_paths,
            source.resolve(),
            sha256=hashlib.sha256(contents).hexdigest(),
            model_id=f"robopark/transaction-{name}",
        ))
    previous = {**models[0], "selected": True}
    ai_runtime._activate_model(host_paths, previous)

    original_write = ai_runtime.atomic_write_json
    original_publish = ai_runtime.publish_status
    failures = 0

    def flaky_write(path, payload, *args, **kwargs):
        nonlocal failures
        if (
            failure_mode == "partial_receipt"
            and path.name == "runtime-model.json"
            and payload.get("model") == models[1]["model"]
            and failures == 0
        ):
            failures += 1
            raise OSError("simulated runtime receipt write failure")
        return original_write(path, payload, *args, **kwargs)

    def flaky_publish(paths, **values):
        nonlocal failures
        if failure_mode == "disabled_publish" and failures == 0:
            failures += 1
            raise OSError("simulated public status write failure")
        return original_publish(paths, **values)

    monkeypatch.setattr(ai_runtime, "atomic_write_json", flaky_write)
    monkeypatch.setattr(ai_runtime, "publish_status", flaky_publish)
    calls = []
    runner = type(
        "Runner", (), {"run": lambda self, argv, **kwargs: calls.append(argv)}
    )()

    with pytest.raises(ValueError, match="ai_model_selection_failed"):
        ai_runtime.select_model(host_paths, models[1]["model"], runner)

    assert failures == 1
    assert calls == [["systemctl", "stop", "robopark-ai.service"]]
    assert ai_runtime.selected_model(host_paths)["model"] == previous["model"]
    assert json.loads((host_paths.var / "ai/runtime-model.json").read_text())["model"] == previous["model"]
    active = host_paths.var / "ai/models/active.gguf"
    assert active.resolve() == (
        host_paths.var / "ai/models" / previous["model_file"]
    ).resolve()


def test_first_selection_publish_failure_restores_empty_state_and_runtime(
    host_paths, tmp_path, monkeypatch
):
    from robopark_host import ai_runtime

    base = host_paths.var / "ai"
    binary = base / "bin/llama-server"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"verified-runtime")
    binary_digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    (base / "install.json").write_text(json.dumps({
        "schema": 3,
        "llama_commit": ai_runtime.LLAMA_COMMIT,
        "binary_sha256": binary_digest,
    }))
    contents = _gemma4_gguf()
    source = tmp_path / "first-trained.gguf"
    source.write_bytes(contents)
    digest = hashlib.sha256(contents).hexdigest()
    monkeypatch.setattr(ai_runtime, "MIN_REGISTERED_MODEL_SIZE", 1)
    ai_runtime.register_merged_gguf(
        host_paths,
        source.resolve(),
        sha256=digest,
        model_id="robopark/first-trained",
    )
    original_publish = ai_runtime.publish_status
    failures = 0

    def fail_first_publish(paths, **values):
        nonlocal failures
        if failures == 0:
            failures += 1
            raise OSError("simulated status publication failure")
        return original_publish(paths, **values)

    monkeypatch.setattr(ai_runtime, "publish_status", fail_first_publish)
    calls = []
    runner = type(
        "Runner", (), {"run": lambda self, argv, **kwargs: calls.append(argv)}
    )()

    with pytest.raises(ValueError, match="ai_model_selection_failed"):
        ai_runtime.select_model(host_paths, "robopark/first-trained", runner)

    assert failures == 1
    assert calls == [["systemctl", "stop", "robopark-ai.service"]]
    for target in (
        base / "model-selection.json",
        base / "runtime-model.json",
        base / "models/active.gguf",
    ):
        assert not target.exists() and not target.is_symlink()
    assert ai_runtime.runtime_installed(host_paths, verify=True)
    assert not ai_runtime.installed(host_paths, verify=True)


@pytest.mark.parametrize("schema", [2, 3])
def test_runtime_receipt_schema_two_and_three_require_binary_digest(
    host_paths, schema
):
    from robopark_host import ai_runtime

    base = host_paths.var / "ai"
    binary = base / "bin/llama-server"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"pinned-runtime")
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    receipt = {
        "schema": schema,
        "llama_commit": ai_runtime.LLAMA_COMMIT,
        "binary_sha256": digest,
    }
    if schema == 2:
        receipt.update({
            "model_file": ai_runtime.MODEL_FILE,
            "model_size": ai_runtime.MODEL_SIZE,
            "model_sha256": ai_runtime.MODEL_SHA256,
        })
    (base / "install.json").write_text(json.dumps(receipt))

    assert ai_runtime.runtime_installed(host_paths, verify=True)
    binary.write_bytes(b"tampered-runtime")
    assert not ai_runtime.runtime_installed(host_paths, verify=True)


def test_support_requires_agx_tegra234_memory_and_ready_storage(host_paths, monkeypatch):
    from robopark_host import ai_runtime

    _hardware(host_paths.root)
    storage_calls = []
    monkeypatch.setattr(ai_runtime, "require_storage", lambda root, **kw: storage_calls.append((root, kw)) or {"state": "ready"})
    assert ai_runtime.probe_support(host_paths) == (True, None)
    assert storage_calls == [(host_paths.root, {"require_layout": True})]


@pytest.mark.parametrize(
    "model,compatible,memory,reason",
    [
        ("NVIDIA Jetson Orin Nano", "nvidia,p3767-0000\0nvidia,tegra234", 32 * 1024**2, "p3701_required"),
        ("NVIDIA Jetson AGX Orin", "nvidia,p3767-0000\0nvidia,tegra234", 32 * 1024**2, "p3701_required"),
        ("NVIDIA Jetson AGX Orin", "nvidia,p3701-0000\0nvidia,tegra194", 32 * 1024**2, "tegra234_required"),
        ("NVIDIA Jetson AGX Orin", "nvidia,p3701-0000\0nvidia,tegra234", 16 * 1024**2, "memory_below_24gib"),
    ],
)
def test_generic_orin_or_ram_profile_never_enables_ai(host_paths, monkeypatch, model, compatible, memory, reason):
    from robopark_host import ai_runtime

    _hardware(host_paths.root, model=model, compatible=compatible, memory_kib=memory)
    monkeypatch.setattr(ai_runtime, "require_storage", lambda *a, **k: {"state": "ready"})
    assert ai_runtime.probe_support(host_paths) == (False, reason)


def test_unsupported_reconcile_only_publishes_status(host_paths, monkeypatch):
    from robopark_host import ai_runtime

    monkeypatch.setattr(ai_runtime, "probe_support", lambda paths: (False, "agx_required"))
    monkeypatch.setattr(ai_runtime, "install_runtime", lambda *a, **k: pytest.fail("install reached"))
    status = ai_runtime.reconcile(host_paths, auto_install=True)
    assert status == {
        "schema": 1,
        "supported": False,
        "installed": False,
        "runtime_installed": False,
        "model_available": False,
        "enabled": False,
        "ready": False,
        "reason": "agx_required",
        "model": "google/gemma-4-E4B-it-qat-q4_0-gguf",
        "model_family": "gemma-4-E4B",
        "model_source": "builtin",
        "model_sha256": None,
        "backend": None,
        "parallel_slots": 4,
        "context_tokens_per_slot": 8192,
        "total_context_tokens": 32768,
    }
    assert json.loads((host_paths.ops / "public/ai-runtime.json").read_text()) == status


def test_reconcile_reports_service_start_failure_separately_from_install(host_paths, monkeypatch):
    from robopark_host import ai_runtime

    monkeypatch.setattr(ai_runtime, "probe_support", lambda paths: (True, None))
    monkeypatch.setattr(ai_runtime, "installed", lambda paths, **kwargs: True)
    monkeypatch.setattr(
        ai_runtime,
        "runtime_ready",
        lambda paths: pytest.fail("readiness reached after failed start"),
    )

    class Runner:
        def run(self, argv, **kwargs):
            raise RuntimeError("unit failed")

    status = ai_runtime.reconcile(host_paths, runner=Runner())
    assert status["installed"] is True
    assert status["enabled"] is False
    assert status["ready"] is False
    assert status["reason"] == "startup_failed"


def test_install_prepares_runtime_without_downloading_or_selecting_model(host_paths, monkeypatch):
    from robopark_host import ai_runtime

    monkeypatch.setattr(ai_runtime, "MODEL_SIZE", 4)
    monkeypatch.setattr(ai_runtime, "MODEL_SHA256", "054edec1d0211f624fed0cbca9d4f9400b0e491c43742af2c5b0abebf0c990d8")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[0] == "nvcc" and "--version" in argv:
            return "release 12.6"
        elif argv[0] == "cmake" and "--build" in argv:
            build = Path(argv[argv.index("--build") + 1])
            (build / "bin").mkdir(parents=True, exist_ok=True)
            (build / "bin/llama-server").write_text("binary")
        return ""

    monkeypatch.setattr(ai_runtime, "_run", run)
    ai_runtime.install_runtime(host_paths)
    assert ["nvcc", "--version"] in calls
    configure = next(call for call in calls if call[:2] == ["cmake", "-S"])
    assert "-DGGML_CUDA=ON" in configure
    assert "-DCMAKE_CUDA_ARCHITECTURES=87" in configure
    assert "-DLLAMA_BUILD_SERVER=ON" in configure
    assert "-DBUILD_SHARED_LIBS=OFF" in configure
    assert not any("LLAMA_CURL" in value for value in configure)
    assert [
        "git", "-C", str(host_paths.var / "ai/src/llama.cpp"), "remote", "set-url",
        "origin", "https://github.com/ggml-org/llama.cpp.git",
    ] in calls
    assert not any(call[0] == "curl" for call in calls)
    assert not (host_paths.var / "ai/models/gemma-4-E4B_q4_0-it.gguf").exists()
    assert not (host_paths.var / "ai/models/active.gguf").exists()
    assert ai_runtime.runtime_installed(host_paths, verify=True)
    assert not ai_runtime.installed(host_paths, verify=True)
    receipt = json.loads((host_paths.var / "ai/install.json").read_text())
    assert set(receipt) == {"schema", "llama_commit", "binary_sha256"}
    binary = host_paths.var / "ai/bin/llama-server"
    binary.write_text("modified binary")
    assert not ai_runtime.installed(host_paths, verify=True)
    monkeypatch.setattr(ai_runtime, "probe_support", lambda paths: (True, None))
    calls.clear()
    repaired = ai_runtime.reconcile(host_paths, auto_install=True)
    assert repaired["runtime_installed"] is True
    assert repaired["installed"] is False
    assert repaired["ready"] is False
    assert repaired["reason"] == "awaiting_model"
    assert any(call[:2] == ["cmake", "--build"] for call in calls)
    assert ai_runtime.runtime_installed(host_paths, verify=True)
    assert not ai_runtime.installed(host_paths, verify=True)
    # Even a byte-identical symlink cannot replace the installed executable.
    replacement = binary.with_suffix(".replacement")
    replacement.write_text("binary")
    binary.unlink()
    binary.symlink_to(replacement)
    assert not ai_runtime.installed(host_paths, verify=True)


def test_runtime_install_never_fetches_pinned_builtin_weights(host_paths, monkeypatch):
    from robopark_host import ai_runtime

    def run(argv, **kwargs):
        if argv[0] == "cmake" and "--build" in argv:
            build = Path(argv[argv.index("--build") + 1])
            (build / "bin").mkdir(parents=True, exist_ok=True)
            (build / "bin/llama-server").write_text("binary")
        elif argv[0] == "curl":
            pytest.fail("runtime preparation attempted to download model weights")
        return ""

    monkeypatch.setattr(ai_runtime, "_run", run)
    ai_runtime.install_runtime(host_paths)
    assert ai_runtime.runtime_installed(host_paths, verify=True)


def test_enable_requires_prepared_runtime_and_selected_model(host_paths, monkeypatch):
    from robopark_host import ai_runtime

    monkeypatch.setattr(ai_runtime, "probe_support", lambda paths: (True, None))
    monkeypatch.setattr(ai_runtime, "runtime_installed", lambda paths, **kwargs: True)
    monkeypatch.setattr(ai_runtime, "installed", lambda paths, **kwargs: False)

    with pytest.raises(ValueError, match="ai_model_not_selected"):
        ai_runtime.control(host_paths, "enable", object())


def test_first_registered_selection_does_not_require_builtin_model(
    host_paths, tmp_path, monkeypatch
):
    from robopark_host import ai_runtime

    contents = _gemma4_gguf()
    source = tmp_path / "trained.gguf"
    source.write_bytes(contents)
    digest = hashlib.sha256(contents).hexdigest()
    monkeypatch.setattr(ai_runtime, "MIN_REGISTERED_MODEL_SIZE", 1)
    monkeypatch.setattr(ai_runtime, "runtime_installed", lambda paths, **kwargs: True)
    ai_runtime.register_merged_gguf(
        host_paths, source.resolve(), sha256=digest, model_id="robopark/trained-v1"
    )
    calls = []
    runner = type("Runner", (), {"run": lambda self, argv, **kw: calls.append(argv)})()

    selected = ai_runtime.select_model(host_paths, "robopark/trained-v1", runner)

    assert selected["model"] == "robopark/trained-v1"
    assert calls == [["systemctl", "stop", "robopark-ai.service"]]
    assert ai_runtime.installed(host_paths, verify=True)


def test_retired_bonsai_artifacts_do_not_count_as_installed(host_paths):
    from robopark_host import ai_runtime

    base = host_paths.var / "ai"
    (base / "bin").mkdir(parents=True)
    (base / "bin/llama-server").write_text("old binary")
    legacy = base / "models/Ternary-Bonsai-2-27B-PQ2_0.gguf"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"retired")

    assert ai_runtime.installed(host_paths) is False


def test_remove_model_preserves_prepared_runtime_without_stopping_broker(host_paths, monkeypatch):
    from robopark_host import ai_runtime

    monkeypatch.setattr(ai_runtime, "probe_support", lambda paths: (True, None))
    base = host_paths.var / "ai"
    for path in (
        base / "models/gemma-4-E4B_q4_0-it.gguf",
        base / "models/gemma-4-E4B_q4_0-it.gguf.part",
        base / "models/Ternary-Bonsai-2-27B-PQ2_0.gguf",
        base / "models/Ternary-Bonsai-2-27B-PQ2_0.gguf.part",
        base / "install.json",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    calls = []

    class Runner:
        def run(self, argv, **kwargs):
            calls.append(argv)

    ai_runtime.control(host_paths, "remove_model", Runner())
    assert not any(path.exists() for path in (base / "models").iterdir())
    assert (base / "install.json").exists()
    assert all("robopark-ai-broker.service" not in call for call in calls)


def test_service_account_uses_absolute_useradd_and_api_secret_is_private(host_paths, monkeypatch):
    from types import SimpleNamespace

    from robopark_host import ai_runtime

    calls = []
    answers = iter([KeyError(), SimpleNamespace(pw_uid=991, pw_gid=991)])

    def account(_name):
        value = next(answers)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(ai_runtime.pwd, "getpwnam", account)
    monkeypatch.setattr(ai_runtime, "_run", lambda argv, **kw: calls.append(argv) or "")
    monkeypatch.setattr(ai_runtime.os, "chown", lambda *args: None)
    monkeypatch.setattr(ai_runtime.secrets, "token_urlsafe", lambda size: "s" * 48)
    system_paths = SimpleNamespace(root=Path("/"), var=host_paths.var)
    ai_runtime.ensure_service_account(system_paths)
    secret = ai_runtime.ensure_api_key(system_paths)
    assert calls[0][0] == "/usr/sbin/useradd"
    assert secret.read_text() == "s" * 48
    assert secret.stat().st_mode & 0o777 == 0o600


def test_control_intent_is_durable_and_separate_from_public_projection(host_paths):
    from robopark_host.ai_runtime import read_enabled_intent, write_enabled_intent

    write_enabled_intent(host_paths, True)
    assert read_enabled_intent(host_paths) is True
    intent = host_paths.var / "ai/control.json"
    assert intent.stat().st_mode & 0o777 == 0o600
    assert intent != host_paths.ops / "public/ai-runtime.json"


def test_enable_intent_survives_failed_service_start(host_paths, monkeypatch):
    from robopark_host import ai_runtime

    monkeypatch.setattr(ai_runtime, "probe_support", lambda paths: (True, None))
    monkeypatch.setattr(ai_runtime, "runtime_installed", lambda paths, **kwargs: True)
    monkeypatch.setattr(ai_runtime, "installed", lambda paths, **kwargs: True)
    monkeypatch.setattr(ai_runtime, "ensure_api_key", lambda paths: None)

    class Runner:
        def run(self, argv, **kwargs):
            raise RuntimeError("start failed")

    with pytest.raises(RuntimeError, match="start failed"):
        ai_runtime.control(host_paths, "enable", Runner())
    assert ai_runtime.read_enabled_intent(host_paths) is True


def test_control_install_is_background_and_does_not_hold_http_for_build(host_paths, monkeypatch):
    from robopark_host import ai_runtime
    monkeypatch.setattr(ai_runtime, "probe_support", lambda paths: (True, None))
    monkeypatch.setattr(ai_runtime, "ensure_service_account", lambda paths: None)
    monkeypatch.setattr(ai_runtime, "ensure_api_key", lambda paths: None)
    monkeypatch.setattr(ai_runtime, "installed", lambda *a, **kw: False)
    monkeypatch.setattr(ai_runtime, "install_runtime", lambda *a: pytest.fail("HTTP request ran installer synchronously"))
    monkeypatch.setattr(ai_runtime, "ai_lock", lambda *a, **kw: pytest.fail("background install waited for setup lock"))
    calls = []
    class Runner:
        def run(self, argv, **kw):
            calls.append(argv)
    result = ai_runtime.control(host_paths, "install", Runner())
    assert result["reason"] == "installing"
    assert ["systemctl", "start", "--no-block", "robopark-ai-setup.service"] in calls
    assert ai_runtime.read_enabled_intent(host_paths) is False


def test_refresh_preserves_installing_only_while_setup_is_active(host_paths, monkeypatch):
    from robopark_host import ai_runtime

    monkeypatch.setattr(ai_runtime, "probe_support", lambda paths: (True, None))
    monkeypatch.setattr(ai_runtime, "installed", lambda paths: False)
    installing = ai_runtime.refresh_runtime_status(host_paths, enabled=False, installing=True)
    assert installing["reason"] == "installing"
    monkeypatch.setattr(ai_runtime, "installed", lambda paths: True)
    promoted = ai_runtime.refresh_runtime_status(host_paths, enabled=False, installing=True)
    assert promoted["installed"] is True
    assert promoted["reason"] == "installing"
    monkeypatch.setattr(ai_runtime, "installed", lambda paths: False)
    stopped = ai_runtime.refresh_runtime_status(host_paths, enabled=False, installing=False)
    assert stopped["reason"] == "not_installed"


def test_model_checksum_cannot_certify_an_unknown_runtime_build(host_paths, monkeypatch):
    from robopark_host import ai_runtime

    base = host_paths.var / 'ai'
    (base / 'bin').mkdir(parents=True)
    (base / 'models').mkdir()
    (base / 'bin/llama-server').write_text('old binary')
    (base / 'models' / ai_runtime.MODEL_FILE).write_bytes(b'model')
    monkeypatch.setattr(ai_runtime, 'MODEL_SIZE', 5)
    monkeypatch.setattr(ai_runtime, '_sha256', lambda path: ai_runtime.MODEL_SHA256)
    assert not ai_runtime.installed(host_paths, verify=True)
    assert not (base / 'install.json').exists()


def test_readiness_rejects_incomplete_tool_generation():
    from robopark_host.ai_runtime import _smoke_tool_response

    value = _valid_smoke()
    value['choices'][0]['finish_reason'] = 'length'
    assert not _smoke_tool_response(value)
