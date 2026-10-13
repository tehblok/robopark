"""Persistent engine provenance and repeatable on-target acceptance."""

from __future__ import annotations

import math
import platform
import struct
import tempfile
import uuid
from dataclasses import asdict
from pathlib import Path

from . import media_backend, registry, tensorrt_backend


def status(state: Path, *, trtexec: Path | None = None) -> dict:
    trt = tensorrt_backend.probe(trtexec)
    return {
        "schema": 1,
        "tensorrt": asdict(trt),
        "media": asdict(media_backend.probe()),
        "registered_models": sorted(p.stem for p in (state / "registry").glob("*.json")),
        "built_engines": sorted(
            p.name for p in (state / "engines").glob("*") if (p / "manifest.json").is_file()
        ),
        "edge_configured": (state / "edge-profile.json").is_file(),
        "ready": False,
        "reason": "run_model_specific_acceptance",
    }


def build(
    state: Path, name: str, shapes: dict, backend: str, fallback: bool, trtexec: Path | None = None
) -> dict:
    model = registry.verify(state, name)
    if model["kind"] != "onnx":
        raise ValueError("onnx_model_required")
    identifier = f"{name[:49]}-{uuid.uuid4().hex[:12]}"
    output = state / "engines" / identifier
    result = tensorrt_backend.build_engine(
        Path(model["path"]),
        output,
        shapes,
        backend=backend,
        allow_gpu_fallback=fallback,
        trtexec_path=trtexec,
    )
    binary = tensorrt_backend.probe(trtexec).trtexec_path
    if binary is None:
        raise ValueError("trtexec_unavailable")
    manifest = {
        "schema": 1,
        "id": identifier,
        "model_id": name,
        "model_sha256": model["sha256"],
        "engine_sha256": result.engine_sha256,
        "backend": result.backend,
        "tensorrt_version": result.tensorrt_version,
        "trtexec": str(binary),
        "trtexec_sha256": registry.digest(binary),
        "shapes": shapes,
        "input_dtypes": result.input_dtypes,
        "machine": platform.machine(),
        "state": "built_not_accepted",
    }
    registry.write_json(output / "manifest.json", manifest)
    return manifest


def infer(state: Path, identifier: str, inputs: dict[str, Path], output: Path) -> dict:
    identifier = registry.model_id(identifier)
    directory = state / "engines" / identifier
    manifest = registry.read_json(directory / "manifest.json")
    engine = directory / "model.plan"
    binary = Path(manifest["trtexec"])
    if manifest.get("schema") != 1 or manifest.get("id") != identifier or engine.is_symlink():
        raise ValueError("engine_manifest_invalid")
    if registry.digest(engine) != manifest["engine_sha256"]:
        raise ValueError("engine_changed")
    if registry.digest(binary) != manifest["trtexec_sha256"]:
        raise ValueError("runtime_changed_rebuild_engine")
    current = tensorrt_backend.probe(binary)
    if current.version != manifest["tensorrt_version"] or platform.machine() != manifest["machine"]:
        raise ValueError("runtime_changed_rebuild_engine")
    result = tensorrt_backend.infer_engine(
        engine,
        inputs,
        manifest["shapes"],
        output,
        trtexec_path=binary,
        input_dtypes=manifest["input_dtypes"],
        backend=manifest["backend"],
    )
    return {"engine_id": identifier, "backend": manifest["backend"], "outputs": result.outputs}


def _output_values(value):
    # trtexec --exportOutput emits an array of named outputs with flat values.
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise ValueError("smoke_output_invalid")
    output = value[0]
    if output.get("name") != "output" or not isinstance(output.get("values"), list):
        raise ValueError("smoke_output_invalid")
    values = output["values"]
    if len(values) != 256 or any(
        type(x) not in (int, float) or not math.isfinite(x) or abs(x - 4) > 0.01 for x in values
    ):
        raise ValueError("smoke_numerical_mismatch")


def smoke(state: Path, backend: str, *, trtexec: Path | None = None) -> dict:
    import onnx
    from onnx import TensorProto, helper

    # A real FP16 Conv that depends on its input (cannot fold to a constant).
    # Three unit channels, unit weights and bias=1 give exact output=4.
    # Never allow silent GPU fallback.
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="smoke-", dir=state) as temporary:
        work = Path(temporary)
        input_shape = [1, 3, 8, 8]
        weight = helper.make_tensor(
            "weight", TensorProto.FLOAT16, [4, 3, 1, 1], struct.pack("<12e", *([1] * 12)), raw=True
        )
        bias = helper.make_tensor(
            "bias", TensorProto.FLOAT16, [4], struct.pack("<4e", 1, 1, 1, 1), raw=True
        )
        graph = helper.make_graph(
            [
                helper.make_node(
                    "Conv",
                    ["input", "weight", "bias"],
                    ["output"],
                    kernel_shape=[1, 1],
                    strides=[1, 1],
                )
            ],
            "robopark_smoke",
            [helper.make_tensor_value_info("input", TensorProto.FLOAT16, input_shape)],
            [helper.make_tensor_value_info("output", TensorProto.FLOAT16, [1, 4, 8, 8])],
            [weight, bias],
        )
        model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)], ir_version=10)
        onnx.checker.check_model(model)
        onnx.save(model, work / "smoke.onnx")
        built = tensorrt_backend.build_engine(
            work / "smoke.onnx",
            work / "build",
            {"input": input_shape},
            backend=backend,
            trtexec_path=trtexec,
        )
        (work / "input.bin").write_bytes(struct.pack("<192e", *([1] * 192)))
        result = tensorrt_backend.infer_engine(
            built.engine_path,
            {"input": work / "input.bin"},
            {"input": input_shape},
            work / "run",
            trtexec_path=trtexec,
            input_dtypes={"input": "float16"},
            backend=backend,
        )
        _output_values(result.outputs)
        report = {
            "state": "smoke_passed",
            "backend": backend,
            "gpu_fallback": False,
            "tensorrt_version": built.tensorrt_version,
            "numerical_output_verified": True,
            "engine_sha256": built.engine_sha256,
        }
        registry.write_json(state / "acceptance" / f"{backend}-{uuid.uuid4().hex}.json", report)
        return report
