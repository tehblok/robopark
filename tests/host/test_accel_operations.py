from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy/accelerator"))
from robopark_accel import edge, operations, registry, server


def test_inference_refuses_changed_runtime_before_deserialization(tmp_path, monkeypatch):
    binary = tmp_path / "trtexec"
    binary.write_bytes(b"old")
    directory = tmp_path / "engines/test"
    directory.mkdir(parents=True)
    (directory / "model.plan").write_bytes(b"engine")
    registry.write_json(
        directory / "manifest.json",
        {
            "schema": 1,
            "id": "test",
            "trtexec": str(binary),
            "trtexec_sha256": registry.digest(binary),
            "engine_sha256": registry.digest(directory / "model.plan"),
        },
    )
    binary.write_bytes(b"new")
    monkeypatch.setattr(
        operations.tensorrt_backend,
        "infer_engine",
        lambda *a, **k: pytest.fail("must not deserialize"),
    )
    with pytest.raises(ValueError, match="runtime_changed"):
        operations.infer(tmp_path, "test", {}, tmp_path / "out")


@pytest.mark.parametrize(
    "value",
    [
        [],
        [{"name": "output", "values": [0] * 256}],
        [{"name": "output", "values": [float("nan")] * 256}],
    ],
)
def test_smoke_requires_finite_numerically_correct_output(value):
    with pytest.raises(ValueError, match="smoke_"):
        operations._output_values(value)


def test_smoke_reference_accepts_only_expected_tensor():
    operations._output_values([{"name": "output", "values": [4.0] * 256}])


def test_smoke_onnx_graph_depends_on_input_and_matches_cpu_reference(tmp_path, monkeypatch):
    onnx = pytest.importorskip("onnx")
    from types import SimpleNamespace

    import numpy as np
    from onnx.reference import ReferenceEvaluator

    captured = {}

    def build(path, output, shapes, **kwargs):
        model = onnx.load(path)
        onnx.checker.check_model(model)
        captured["model"] = model
        assert shapes == {"input": [1, 3, 8, 8]}
        assert kwargs["backend"] == "dla0"
        assert not kwargs.get("allow_gpu_fallback", False)
        return SimpleNamespace(
            engine_path=output / "model.plan", engine_sha256="test", tensorrt_version="11.3.0"
        )

    def infer(engine, inputs, shapes, output, **kwargs):
        assert kwargs["backend"] == "dla0"
        assert kwargs["input_dtypes"] == {"input": "float16"}
        source = np.frombuffer(inputs["input"].read_bytes(), dtype="<f2").reshape(1, 3, 8, 8)
        evaluator = ReferenceEvaluator(captured["model"])
        values = evaluator.run(None, {"input": source})[0]
        changed = evaluator.run(None, {"input": source * 2})[0]
        assert np.all(values == 4)
        assert np.all(changed == 7)
        return SimpleNamespace(outputs=[{"name": "output", "values": values.flatten().tolist()}])

    monkeypatch.setattr(operations.tensorrt_backend, "build_engine", build)
    monkeypatch.setattr(operations.tensorrt_backend, "infer_engine", infer)
    assert operations.smoke(tmp_path, "dla0")["numerical_output_verified"] is True


def test_service_rejects_client_paths_and_cleans_request_files(tmp_path):
    with pytest.raises(ValueError, match="thumbnail_request_invalid"):
        server.dispatch(tmp_path, "/v1/thumbnail", {"path": "/etc/shadow"})
    assert list(tmp_path.iterdir()) == []


def test_service_cannot_write_tensor_name_as_path(tmp_path, monkeypatch):
    def infer(state, engine_id, inputs, output):
        path = inputs["../../outside"]
        assert path.name == "input-0.bin"
        assert path.is_relative_to(tmp_path)
        assert path.read_bytes() == b"tensor"
        return {"outputs": []}

    monkeypatch.setattr(operations, "infer", infer)
    assert server.dispatch(
        tmp_path,
        "/v1/infer",
        {
            "engine_id": "test",
            "inputs": {"../../outside": base64.b64encode(b"tensor").decode()},
        },
    ) == {"outputs": []}
    assert list(tmp_path.iterdir()) == []


def _profile(tmp_path):
    model = tmp_path / "models/alternative"
    model.mkdir(parents=True)
    (model / "config.json").write_text('{"model_type":"llama"}')
    (model / "tokenizer.json").write_text("{}")
    (model / "model.safetensors").write_bytes(b"weights")
    registry.register(tmp_path, "alternative-model", "checkpoint", model)
    return registry.configure_edge(tmp_path, "alternative-model")


def test_edge_launch_is_local_model_neutral_and_has_no_secret_argv(tmp_path):
    _profile(tmp_path)
    args = edge.launch_arguments(tmp_path)
    assert str(tmp_path / "models/alternative") == args[0]
    assert args[args.index("--host") + 1] == "127.0.0.1"
    assert "--api-key" not in args
    assert "--allowed-local-media-path" not in args
    assert "alternative-model" in args


def test_edge_rejects_exposed_listener_unmerged_adapter_or_unimplemented_rag(tmp_path):
    profile = _profile(tmp_path)
    for change in ({"host": "0.0.0.0"}, {"adapter": "unmerged"}, {"rag": {"enabled": True}}):
        registry.write_json(tmp_path / "edge-profile.json", {**profile, **change}, replace=True)
        with pytest.raises(ValueError):
            edge.launch_arguments(tmp_path)


def test_edge_smoke_does_not_accept_text_that_looks_like_a_tool_call(tmp_path, monkeypatch):
    _profile(tmp_path)

    def request(state, route, payload=None):
        if route == "/v1/models":
            return {"data": [{"id": "alternative-model"}]}
        return {"choices": [{"message": {"content": '{"tool":"check_contract","ok":true}'}}]}

    monkeypatch.setattr(edge, "_request", request)
    with pytest.raises(ValueError, match="edge_tool_contract_failed"):
        edge.check(tmp_path)


def test_edge_smoke_accepts_structured_tool_only(tmp_path, monkeypatch):
    _profile(tmp_path)

    def request(state, route, payload=None):
        if route == "/v1/models":
            return {"data": [{"id": "alternative-model"}]}
        return {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {"name": "check_contract", "arguments": '{"ok":true}'},
                            }
                        ]
                    }
                }
            ]
        }

    monkeypatch.setattr(edge, "_request", request)
    assert edge.check(tmp_path)["production_selected"] is False


def test_real_software_jpeg_endpoint_preserves_original_and_returns_image(tmp_path):
    image = pytest.importorskip("PIL.Image")
    import io

    source = io.BytesIO()
    image.new("RGB", (80, 40), (20, 30, 40)).save(source, "JPEG")
    raw = source.getvalue()
    response = server.dispatch(
        tmp_path,
        "/v1/thumbnail",
        {"jpeg": base64.b64encode(raw).decode(), "width": 20, "height": 20, "backend": "software"},
    )
    assert response["backend"] == "software"
    with image.open(io.BytesIO(base64.b64decode(response["jpeg"]))) as output:
        assert output.size == (20, 10)
    assert source.getvalue() == raw
    assert list(tmp_path.iterdir()) == []
