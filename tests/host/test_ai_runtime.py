import json
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
        return Response()

    monkeypatch.setattr(ai_runtime.urllib.request, "urlopen", request)
    assert ai_runtime.runtime_ready(host_paths) == (True, None)
    body = json.loads(requests[1].data)
    assert body["messages"][0]["role"] == "system"
    assert body["max_tokens"] == 64
    assert body["tool_choice"] == "required"
    assert len(body["tools"]) == 1 and body["parallel_tool_calls"] is False


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
        "enabled": False,
        "ready": False,
        "reason": "agx_required",
        "model": "google/gemma-4-E4B-it-qat-q4_0-gguf",
        "model_sha256": None,
        "backend": None,
    }
    assert json.loads((host_paths.ops / "public/ai-runtime.json").read_text()) == status


def test_install_is_resumable_and_only_promotes_verified_model(host_paths, monkeypatch):
    from robopark_host import ai_runtime

    monkeypatch.setattr(ai_runtime, "MODEL_SIZE", 4)
    monkeypatch.setattr(ai_runtime, "MODEL_SHA256", "054edec1d0211f624fed0cbca9d4f9400b0e491c43742af2c5b0abebf0c990d8")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[0] == "curl":
            Path(argv[argv.index("--output") + 1]).write_bytes(bytes([0, 1, 2, 3]))
        elif argv[0] == "nvcc" and "--version" in argv:
            return "release 12.6"
        elif argv[0] == "cmake" and "--build" in argv:
            build = Path(argv[argv.index("--build") + 1])
            (build / "bin").mkdir(parents=True, exist_ok=True)
            (build / "bin/llama-server").write_text("binary")
        return ""

    monkeypatch.setattr(ai_runtime, "_run", run)
    ai_runtime.install_runtime(host_paths)
    assert (host_paths.var / "ai/models/gemma-4-E4B_q4_0-it.gguf").read_bytes() == bytes([0, 1, 2, 3])
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
    curl = next(call for call in calls if call[0] == "curl")
    assert "--continue-at" in curl and curl[curl.index("--continue-at") + 1] == "-"
    assert not (host_paths.var / "ai/models/gemma-4-E4B_q4_0-it.gguf.part").exists()
    assert ai_runtime.installed(host_paths, verify=True)
    binary = host_paths.var / "ai/bin/llama-server"
    binary.write_text("modified binary")
    assert not ai_runtime.installed(host_paths, verify=True)
    monkeypatch.setattr(ai_runtime, "probe_support", lambda paths: (True, None))
    monkeypatch.setattr(ai_runtime, "runtime_ready", lambda paths: (True, None))
    calls.clear()
    repaired = ai_runtime.reconcile(host_paths, auto_install=True)
    assert repaired["installed"] and repaired["ready"]
    assert any(call[:2] == ["cmake", "--build"] for call in calls)
    assert ai_runtime.installed(host_paths, verify=True)
    # Even a byte-identical symlink cannot replace the installed executable.
    replacement = binary.with_suffix(".replacement")
    replacement.write_text("binary")
    binary.unlink()
    binary.symlink_to(replacement)
    assert not ai_runtime.installed(host_paths, verify=True)


def test_checksum_failure_discards_poisoned_completed_partial(host_paths, monkeypatch):
    from robopark_host import ai_runtime

    monkeypatch.setattr(ai_runtime, "MODEL_SIZE", 4)
    monkeypatch.setattr(ai_runtime, "MODEL_SHA256", "0" * 64)

    def run(argv, **kwargs):
        if argv[0] == "cmake" and "--build" in argv:
            build = Path(argv[argv.index("--build") + 1])
            (build / "bin").mkdir(parents=True, exist_ok=True)
            (build / "bin/llama-server").write_text("binary")
        elif argv[0] == "curl":
            Path(argv[argv.index("--output") + 1]).write_bytes(b"bad!")
        return ""

    monkeypatch.setattr(ai_runtime, "_run", run)
    with pytest.raises(RuntimeError, match="ai_model_checksum_mismatch"):
        ai_runtime.install_runtime(host_paths)
    assert not (host_paths.var / "ai/models/gemma-4-E4B_q4_0-it.gguf.part").exists()


def test_retired_bonsai_artifacts_do_not_count_as_installed(host_paths):
    from robopark_host import ai_runtime

    base = host_paths.var / "ai"
    (base / "bin").mkdir(parents=True)
    (base / "bin/llama-server").write_text("old binary")
    legacy = base / "models/Ternary-Bonsai-2-27B-PQ2_0.gguf"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"retired")

    assert ai_runtime.installed(host_paths) is False


def test_remove_model_cleans_model_partial_and_receipt_without_stopping_broker(host_paths, monkeypatch):
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
    assert not (base / "install.json").exists()
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
    assert ai_runtime.read_enabled_intent(host_paths) is True


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
