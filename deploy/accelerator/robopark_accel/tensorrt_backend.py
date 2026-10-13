"""Constrained TensorRT 10 ``trtexec`` build and inference wrapper."""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import threading
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Backend = Literal["gpu", "dla0", "dla1"]
RuntimeBackend = Literal["gpu", "dla0", "dla1", "dla0-hybrid", "dla1-hybrid"]
TRTEXEC_PATHS = (Path("/usr/src/tensorrt/bin/trtexec"), Path("/usr/bin/trtexec"))
MAX_COMMAND_OUTPUT = 1024 * 1024
MAX_EXPORTED_JSON = 16 * 1024 * 1024


class BackendError(RuntimeError):
    """A safe, stable error from the accelerator boundary."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class TensorRTProbe:
    available: bool
    trtexec_path: Path | None
    version: str | None
    dla_runtime_version: str | None
    dla_cores: int | None
    dla_detected: bool | None
    dla_reason: str | None


@dataclass(frozen=True)
class EngineBuildResult:
    engine_path: Path
    engine_sha256: str
    engine_size: int
    backend: str
    tensorrt_version: str
    input_dtypes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class InferenceResult:
    output_path: Path
    outputs: Any
    backend: str


def _collect(stream: Any, chunks: list[bytes]) -> None:
    remaining = MAX_COMMAND_OUTPUT
    while True:
        data = stream.read(65536)
        if not data:
            break
        if remaining:
            kept = data[:remaining]
            chunks.append(kept)
            remaining -= len(kept)


def _run_command(argv: list[str], timeout_seconds: int) -> CommandResult:
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds_must_be_positive")
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            close_fds=True,
        )
    except OSError as exc:
        raise BackendError("command_start_failed") from exc
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    readers = [
        threading.Thread(target=_collect, args=(process.stdout, stdout_chunks), daemon=True),
        threading.Thread(target=_collect, args=(process.stderr, stderr_chunks), daemon=True),
    ]
    for reader in readers:
        reader.start()
    try:
        returncode = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        raise BackendError("command_timeout") from exc
    finally:
        for reader in readers:
            reader.join(timeout=2)
    return CommandResult(
        returncode,
        b"".join(stdout_chunks).decode("utf-8", "replace"),
        b"".join(stderr_chunks).decode("utf-8", "replace"),
    )


def _binary(explicit: Path | None) -> Path | None:
    candidates = (Path(explicit),) if explicit is not None else TRTEXEC_PATHS
    return next((path.resolve() for path in candidates if path.is_file()), None)


def _parse_version(text: str) -> str | None:
    ordinary = re.search(
        r"(?:TensorRT(?:\.trtexec)?(?:\s+version)?[^\d]{0,20})(\d+\.\d+(?:\.\d+)?)",
        text,
        re.I,
    )
    if ordinary:
        return ordinary.group(1)
    encoded = re.search(r"TensorRT\s+v(\d{6})", text, re.I)
    if encoded:
        digits = encoded.group(1)
        return f"{int(digits[:2])}.{int(digits[2:4])}.{int(digits[4:])}"
    return None


def _probe_dla_cores() -> tuple[int | None, str | None]:
    try:
        result = _run_command(
            [
                sys.executable,
                "-I",
                "-c",
                (
                    "import json\n"
                    "import tensorrt as t\n"
                    "l=t.Logger(t.Logger.ERROR)\n"
                    "b=t.Builder(l)\n"
                    "p={'version':t.__version__,'cores':int(b.num_DLA_cores)}\n"
                    "print('ROBOPARK_DLA_PROBE='+json.dumps(p,separators=(',',':')))\n"
                ),
            ],
            10,
        )
        if result.returncode:
            return None, None
        payload = None
        for line in reversed(result.stdout.splitlines()):
            candidate = line.removeprefix("ROBOPARK_DLA_PROBE=")
            if candidate.startswith("{"):
                payload = json.loads(candidate)
                break
        if (
            not isinstance(payload, dict)
            or set(payload) != {"version", "cores"}
            or not isinstance(payload["version"], str)
            or type(payload["cores"]) is not int
            or payload["cores"] < 0
        ):
            return None, None
        return payload["cores"], payload["version"]
    except (BackendError, json.JSONDecodeError, TypeError, ValueError):
        return None, None


def _version_triplet(version: str | None) -> tuple[int, int, int] | None:
    if version is None:
        return None
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:\D|$)", version)
    return tuple(map(int, match.groups())) if match else None


def _graphs(onnx: Any, root: Any):
    yield root
    graph_type = getattr(getattr(onnx, "AttributeProto", None), "GRAPH", 5)
    graphs_type = getattr(getattr(onnx, "AttributeProto", None), "GRAPHS", 10)
    for node in getattr(root, "node", ()):
        for attribute in getattr(node, "attribute", ()):
            if getattr(attribute, "type", None) == graph_type:
                yield from _graphs(onnx, attribute.g)
            elif getattr(attribute, "type", None) == graphs_type:
                for graph in attribute.graphs:
                    yield from _graphs(onnx, graph)


def probe(trtexec_path: Path | None = None) -> TensorRTProbe:
    binary = _binary(trtexec_path)
    version = None
    available = False
    if binary is not None:
        try:
            result = _run_command([str(binary), "--help"], 10)
            version = _parse_version(result.stdout + "\n" + result.stderr)
            available = result.returncode == 0 and version is not None
        except BackendError:
            pass
    probed_cores, dla_runtime_version = _probe_dla_cores()
    trtexec_triplet = _version_triplet(version)
    runtime_triplet = _version_triplet(dla_runtime_version)
    runtime_mismatch = bool(
        trtexec_triplet and runtime_triplet and trtexec_triplet != runtime_triplet
    )
    versions_match = bool(trtexec_triplet and trtexec_triplet == runtime_triplet)
    dla_cores = probed_cores if versions_match else None
    if runtime_mismatch:
        dla_reason = "python_runtime_mismatch"
    elif dla_cores is None:
        dla_reason = "dla_capability_unknown"
    elif dla_cores == 0:
        dla_reason = "dla_cores_unavailable"
    else:
        dla_reason = None
    return TensorRTProbe(
        available=available,
        trtexec_path=binary,
        version=version,
        dla_runtime_version=dla_runtime_version,
        dla_cores=dla_cores,
        dla_detected=None if dla_cores is None else dla_cores > 0,
        dla_reason=dla_reason,
    )


def _shapes(input_shapes: Mapping[str, Sequence[int]]) -> tuple[str, dict[str, tuple[int, ...]]]:
    if not input_shapes:
        raise BackendError("input_shapes_required")
    normalized: dict[str, tuple[int, ...]] = {}
    for name, dimensions in input_shapes.items():
        if not re.fullmatch(r"[A-Za-z0-9_.\-/]+", name):
            raise BackendError("invalid_input_name")
        shape = tuple(dimensions)
        if not shape or any(type(value) is not int or value < 1 for value in shape):
            raise BackendError(f"invalid_static_shape:{name}")
        normalized[name] = shape
    encoded = ",".join(f"{name}:{'x'.join(map(str, shape))}" for name, shape in normalized.items())
    return encoded, normalized


def _validate_onnx(path: Path, shapes: Mapping[str, tuple[int, ...]]) -> dict[str, str]:
    try:
        onnx = importlib.import_module("onnx")
    except ImportError as exc:
        raise BackendError("onnx_validation_dependency_missing") from exc
    try:
        model = onnx.load(str(path), load_external_data=False)
    except Exception as exc:
        raise BackendError("onnx_parse_failed") from exc
    graphs = list(_graphs(onnx, model.graph))
    for graph in graphs:
        for initializer in graph.initializer:
            if initializer.data_location == onnx.TensorProto.EXTERNAL or initializer.external_data:
                raise BackendError("onnx_external_data_unsupported")
    initializer_names = {item.name for item in model.graph.initializer}
    inputs = [item for item in model.graph.input if item.name not in initializer_names]
    actual: dict[str, tuple[int, ...]] = {}
    for item in inputs:
        dimensions = []
        for dimension in item.type.tensor_type.shape.dim:
            if not dimension.HasField("dim_value") or dimension.dim_value < 1:
                raise BackendError(f"onnx_dynamic_shape_unsupported:{item.name}")
            dimensions.append(int(dimension.dim_value))
        actual[item.name] = tuple(dimensions)
    if actual != dict(shapes):
        raise BackendError("onnx_input_shapes_mismatch")
    dtype_names = {
        onnx.TensorProto.FLOAT: "float32",
        onnx.TensorProto.FLOAT16: "float16",
        onnx.TensorProto.INT8: "int8",
    }
    try:
        input_dtypes = {item.name: dtype_names[item.type.tensor_type.elem_type] for item in inputs}
    except KeyError as exc:
        raise BackendError("onnx_input_dtype_unsupported") from exc
    for graph in graphs:
        for node in getattr(graph, "node", ()):
            if getattr(node, "domain", "") not in ("", "ai.onnx"):
                raise BackendError("onnx_custom_plugins_unsupported")
    return input_dtypes


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_engine(
    onnx_path: Path,
    output_dir: Path,
    input_shapes: Mapping[str, Sequence[int]],
    backend: Backend = "gpu",
    allow_gpu_fallback: bool = False,
    trtexec_path: Path | None = None,
    timeout_seconds: int = 1800,
) -> EngineBuildResult:
    onnx_path, output_dir = Path(onnx_path), Path(output_dir)
    if backend not in ("gpu", "dla0", "dla1"):
        raise ValueError("invalid_backend")
    if backend == "gpu" and allow_gpu_fallback:
        raise ValueError("gpu_fallback_only_applies_to_dla")
    if output_dir.exists():
        raise FileExistsError(output_dir)
    if not onnx_path.is_file():
        raise FileNotFoundError(onnx_path)
    _, shapes = _shapes(input_shapes)
    input_dtypes = _validate_onnx(onnx_path, shapes)
    state = probe(trtexec_path)
    if not state.available or state.trtexec_path is None or state.version is None:
        raise BackendError("trtexec_unavailable")
    try:
        trt_major = int(state.version.split(".", 1)[0])
    except ValueError as exc:
        raise BackendError("tensorrt_version_invalid") from exc
    dla_core = int(backend[-1]) if backend.startswith("dla") else None
    if dla_core is not None and state.dla_cores is not None and dla_core >= state.dla_cores:
        raise BackendError("dla_core_unavailable")
    output_dir.mkdir(parents=True, mode=0o750)
    engine_path = (output_dir / "model.plan").resolve()
    argv = [
        str(state.trtexec_path),
        f"--onnx={onnx_path.resolve()}",
        f"--saveEngine={engine_path}",
        "--skipInference",
    ]
    if trt_major < 11:
        argv.insert(3, "--fp16")
    if dla_core is not None:
        argv.append(f"--useDLACore={dla_core}")
        if allow_gpu_fallback:
            argv.append("--allowGPUFallback")
    result = _run_command(argv, timeout_seconds)
    if result.returncode:
        raise BackendError("engine_build_failed")
    if not engine_path.is_file() or engine_path.stat().st_size < 1:
        raise BackendError("engine_output_missing")
    return EngineBuildResult(
        engine_path=engine_path,
        engine_sha256=_sha256(engine_path),
        engine_size=engine_path.stat().st_size,
        backend=(f"{backend}-hybrid" if allow_gpu_fallback else backend),
        tensorrt_version=state.version,
        input_dtypes=input_dtypes,
    )


def infer_engine(
    engine_path: Path,
    inputs: Mapping[str, Path],
    input_shapes: Mapping[str, Sequence[int]],
    output_dir: Path,
    trtexec_path: Path | None = None,
    timeout_seconds: int = 300,
    input_dtypes: Mapping[str, Literal["float16", "float32", "int8"]] | None = None,
    backend: RuntimeBackend = "gpu",
) -> InferenceResult:
    engine_path, output_dir = Path(engine_path), Path(output_dir)
    if backend not in ("gpu", "dla0", "dla1", "dla0-hybrid", "dla1-hybrid"):
        raise ValueError("invalid_backend")
    if output_dir.exists():
        raise FileExistsError(output_dir)
    if not engine_path.is_file() or engine_path.stat().st_size < 1:
        raise FileNotFoundError(engine_path)
    _, shapes = _shapes(input_shapes)
    if set(inputs) != set(shapes):
        raise BackendError("input_files_mismatch")
    dtypes = dict(input_dtypes or dict.fromkeys(shapes, "float32"))
    if set(dtypes) != set(shapes) or any(
        value not in ("float16", "float32", "int8") for value in dtypes.values()
    ):
        raise BackendError("unsupported_input_dtype")
    resolved_inputs: dict[str, Path] = {}
    for name, path_value in inputs.items():
        path = Path(path_value)
        item_size = {"int8": 1, "float16": 2, "float32": 4}[dtypes[name]]
        expected_size = math.prod(shapes[name]) * item_size
        if not path.is_file() or path.stat().st_size != expected_size:
            raise BackendError(f"input_size_mismatch:{name}")
        resolved_inputs[name] = path.resolve()
    binary = _binary(trtexec_path)
    if binary is None:
        raise BackendError("trtexec_unavailable")
    output_dir.mkdir(parents=True, mode=0o750)
    output_path = (output_dir / "outputs.json").resolve()
    load_inputs = ",".join(f"{name}:{path}" for name, path in resolved_inputs.items())
    argv = [
        str(binary),
        f"--loadEngine={engine_path.resolve()}",
        f"--loadInputs={load_inputs}",
        f"--exportOutput={output_path}",
        "--iterations=1",
        "--warmUp=0",
        "--duration=0",
    ]
    if backend.startswith("dla"):
        argv.append(f"--useDLACore={backend[3]}")
    result = _run_command(argv, timeout_seconds)
    if result.returncode:
        raise BackendError("engine_inference_failed")
    if not output_path.is_file() or not (0 < output_path.stat().st_size <= MAX_EXPORTED_JSON):
        raise BackendError("inference_output_invalid")
    try:
        outputs = json.loads(output_path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BackendError("inference_output_invalid") from exc
    return InferenceResult(output_path=output_path, outputs=outputs, backend=backend)
