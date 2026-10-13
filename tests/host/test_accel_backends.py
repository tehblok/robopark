from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deploy/accelerator"))

from robopark_accel import media_backend, tensorrt_backend  # noqa: E402


class _Dimension:
    def __init__(self, value: int):
        self.dim_value = value
        self.dim_param = ""

    def HasField(self, field: str) -> bool:
        return field == "dim_value" and self.dim_value > 0


def _install_fake_onnx(monkeypatch, *, external: bool = False, elem_type: int = 1, qdq: bool = False):
    tensor = types.SimpleNamespace(
        name="image",
        type=types.SimpleNamespace(
            tensor_type=types.SimpleNamespace(
                elem_type=elem_type,
                shape=types.SimpleNamespace(dim=[_Dimension(value) for value in (1, 3, 8, 8)])
            )
        ),
    )
    initializer = types.SimpleNamespace(
        name="weights", data_location=1 if external else 0, external_data=[object()] if external else []
    )
    model = types.SimpleNamespace(
        graph=types.SimpleNamespace(
            input=[tensor],
            initializer=[initializer],
            node=(
                [types.SimpleNamespace(domain="", op_type="QuantizeLinear"),
                 types.SimpleNamespace(domain="", op_type="DequantizeLinear")]
                if qdq
                else []
            ),
        )
    )
    module = types.SimpleNamespace(
        load=lambda path, load_external_data=False: model,
        TensorProto=types.SimpleNamespace(EXTERNAL=1, FLOAT=1, INT8=3, FLOAT16=10),
    )
    monkeypatch.setitem(sys.modules, "onnx", module)


def test_build_engine_uses_static_gpu_fp16_command_and_hashes_real_plan(tmp_path, monkeypatch):
    model = tmp_path / "model.onnx"
    model.write_bytes(b"onnx")
    binary = tmp_path / "trtexec"
    binary.write_bytes(b"bin")
    _install_fake_onnx(monkeypatch)
    monkeypatch.setattr(tensorrt_backend, "_probe_dla_cores", lambda: (None, None))
    calls = []

    def run(argv, timeout_seconds):
        calls.append((argv, timeout_seconds))
        if "--help" in argv:
            return tensorrt_backend.CommandResult(0, "TensorRT.trtexec [TensorRT v101602]", "")
        save = next(value.split("=", 1)[1] for value in argv if value.startswith("--saveEngine="))
        Path(save).write_bytes(b"real-plan")
        return tensorrt_backend.CommandResult(0, "", "")

    monkeypatch.setattr(tensorrt_backend, "_run_command", run)
    result = tensorrt_backend.build_engine(
        model, tmp_path / "build", {"image": (1, 3, 8, 8)}, trtexec_path=binary
    )

    assert result.backend == "gpu"
    assert result.tensorrt_version == "10.16.2"
    assert result.engine_sha256 == hashlib.sha256(b"real-plan").hexdigest()
    assert result.input_dtypes == {"image": "float32"}
    assert calls[1][0] == [
        str(binary),
        f"--onnx={model.resolve()}",
        f"--saveEngine={(tmp_path / 'build/model.plan').resolve()}",
        "--fp16",
        "--skipInference",
    ]


def test_build_engine_never_accepts_missing_engine_after_success(tmp_path, monkeypatch):
    model = tmp_path / "model.onnx"
    model.write_bytes(b"onnx")
    binary = tmp_path / "trtexec"
    binary.write_bytes(b"bin")
    _install_fake_onnx(monkeypatch)
    monkeypatch.setattr(
        tensorrt_backend,
        "_run_command",
        lambda argv, timeout_seconds: tensorrt_backend.CommandResult(
            0, "TensorRT.trtexec [TensorRT v101602]" if "--help" in argv else "", ""
        ),
    )

    with pytest.raises(tensorrt_backend.BackendError, match="engine_output_missing"):
        tensorrt_backend.build_engine(
            model, tmp_path / "build", {"image": (1, 3, 8, 8)}, trtexec_path=binary
        )


def test_tensorrt11_builds_fp32_onnx_without_removed_fp16_flag(tmp_path, monkeypatch):
    model = tmp_path / "model.onnx"
    model.write_bytes(b"onnx")
    binary = tmp_path / "trtexec"
    binary.write_bytes(b"bin")
    calls = []
    monkeypatch.setattr(tensorrt_backend, "_probe_dla_cores", lambda: (None, None))

    def run(argv, timeout_seconds):
        calls.append(argv)
        if "--help" in argv:
            return tensorrt_backend.CommandResult(0, "TensorRT.trtexec [TensorRT v110300]", "")
        Path(next(value.split("=", 1)[1] for value in argv if value.startswith("--saveEngine="))).write_bytes(b"trt11")
        return tensorrt_backend.CommandResult(0, "", "")

    monkeypatch.setattr(tensorrt_backend, "_run_command", run)
    _install_fake_onnx(monkeypatch, elem_type=1)
    result = tensorrt_backend.build_engine(
        model, tmp_path / "fp32", {"image": (1, 3, 8, 8)}, trtexec_path=binary
    )
    assert result.tensorrt_version == "11.3.0"
    assert result.input_dtypes == {"image": "float32"}
    assert "--fp16" not in calls[-1]


@pytest.mark.parametrize("backend,core", [("dla0", 0), ("dla1", 1)])
def test_dla_build_is_strict_by_default_and_capability_driven(tmp_path, monkeypatch, backend, core):
    model = tmp_path / "model.onnx"
    model.write_bytes(b"onnx")
    binary = tmp_path / "trtexec"
    binary.write_bytes(b"bin")
    _install_fake_onnx(monkeypatch)
    monkeypatch.setattr(tensorrt_backend, "_probe_dla_cores", lambda: (2, "10.16.2"))
    calls = []

    def run(argv, timeout_seconds):
        calls.append(argv)
        if "--help" in argv:
            return tensorrt_backend.CommandResult(0, "TensorRT.trtexec [TensorRT v101602]", "")
        save = next(value.split("=", 1)[1] for value in argv if value.startswith("--saveEngine="))
        Path(save).write_bytes(b"dla-plan")
        return tensorrt_backend.CommandResult(0, "", "")

    monkeypatch.setattr(tensorrt_backend, "_run_command", run)
    result = tensorrt_backend.build_engine(
        model,
        tmp_path / "build",
        {"image": (1, 3, 8, 8)},
        backend=backend,
        trtexec_path=binary,
    )

    assert result.backend == backend
    assert f"--useDLACore={core}" in calls[1]
    assert "--allowGPUFallback" not in calls[1]


def test_dla_zero_cores_rejects_before_build_and_hybrid_is_explicit(tmp_path, monkeypatch):
    model = tmp_path / "model.onnx"
    model.write_bytes(b"onnx")
    binary = tmp_path / "trtexec"
    binary.write_bytes(b"bin")
    _install_fake_onnx(monkeypatch)
    monkeypatch.setattr(tensorrt_backend, "_probe_dla_cores", lambda: (0, "10.16.2"))
    monkeypatch.setattr(
        tensorrt_backend,
        "_run_command",
        lambda argv, timeout_seconds: tensorrt_backend.CommandResult(
            0, "TensorRT.trtexec [TensorRT v101602]", ""
        ),
    )
    with pytest.raises(tensorrt_backend.BackendError, match="dla_core_unavailable"):
        tensorrt_backend.build_engine(
            model, tmp_path / "blocked", {"image": (1, 3, 8, 8)}, backend="dla0", trtexec_path=binary
        )

    monkeypatch.setattr(tensorrt_backend, "_probe_dla_cores", lambda: (None, None))
    calls = []

    def run(argv, timeout_seconds):
        calls.append(argv)
        if "--help" in argv:
            return tensorrt_backend.CommandResult(0, "TensorRT.trtexec [TensorRT v101602]", "")
        Path(next(value.split("=", 1)[1] for value in argv if value.startswith("--saveEngine="))).write_bytes(b"hybrid")
        return tensorrt_backend.CommandResult(0, "", "")

    monkeypatch.setattr(tensorrt_backend, "_run_command", run)
    result = tensorrt_backend.build_engine(
        model,
        tmp_path / "hybrid",
        {"image": (1, 3, 8, 8)},
        backend="dla1",
        allow_gpu_fallback=True,
        trtexec_path=binary,
    )
    assert result.backend == "dla1-hybrid"
    assert "--allowGPUFallback" in calls[1]


def test_build_rejects_external_onnx_data_and_existing_output(tmp_path, monkeypatch):
    model = tmp_path / "model.onnx"
    model.write_bytes(b"onnx")
    binary = tmp_path / "trtexec"
    binary.write_bytes(b"bin")
    _install_fake_onnx(monkeypatch, external=True)

    with pytest.raises(tensorrt_backend.BackendError, match="onnx_external_data_unsupported"):
        tensorrt_backend.build_engine(
            model, tmp_path / "build", {"image": (1, 3, 8, 8)}, trtexec_path=binary
        )

    (tmp_path / "build").mkdir()
    with pytest.raises(FileExistsError):
        tensorrt_backend.build_engine(
            model, tmp_path / "build", {"image": (1, 3, 8, 8)}, trtexec_path=binary
        )


def test_infer_validates_float32_size_and_parses_bounded_json(tmp_path, monkeypatch):
    engine = tmp_path / "model.plan"
    engine.write_bytes(b"plan")
    raw = tmp_path / "image.bin"
    raw.write_bytes(b"\0" * (1 * 3 * 8 * 8 * 4))
    binary = tmp_path / "trtexec"
    binary.write_bytes(b"bin")
    calls = []

    def run(argv, timeout_seconds):
        calls.append(argv)
        export = next(value.split("=", 1)[1] for value in argv if value.startswith("--exportOutput="))
        Path(export).write_text(json.dumps({"outputs": [{"name": "score", "values": [0.75]}]}))
        return tensorrt_backend.CommandResult(0, "", "")

    monkeypatch.setattr(tensorrt_backend, "_run_command", run)
    result = tensorrt_backend.infer_engine(
        engine,
        {"image": raw},
        {"image": (1, 3, 8, 8)},
        tmp_path / "infer",
        trtexec_path=binary,
    )

    assert result.outputs["outputs"][0]["values"] == [0.75]
    assert calls[0] == [
        str(binary),
        f"--loadEngine={engine.resolve()}",
        f"--loadInputs=image:{raw.resolve()}",
        f"--exportOutput={(tmp_path / 'infer/outputs.json').resolve()}",
        "--iterations=1",
        "--warmUp=0",
        "--duration=0",
    ]

    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"short")
    with pytest.raises(tensorrt_backend.BackendError, match="input_size_mismatch:image"):
        tensorrt_backend.infer_engine(
            engine,
            {"image": bad},
            {"image": (1, 3, 8, 8)},
            tmp_path / "infer-bad",
            trtexec_path=binary,
        )


def test_infer_accepts_exact_float16_and_int8_raw_tensor_sizes(tmp_path, monkeypatch):
    engine = tmp_path / "model.plan"
    engine.write_bytes(b"plan")
    fp16 = tmp_path / "image.bin"
    fp16.write_bytes(b"\0" * 8)
    int8 = tmp_path / "mask.bin"
    int8.write_bytes(b"\0" * 4)
    binary = tmp_path / "trtexec"
    binary.write_bytes(b"bin")

    def run(argv, timeout_seconds):
        export = next(value.split("=", 1)[1] for value in argv if value.startswith("--exportOutput="))
        Path(export).write_text("[]")
        return tensorrt_backend.CommandResult(0, "", "")

    monkeypatch.setattr(tensorrt_backend, "_run_command", run)
    result = tensorrt_backend.infer_engine(
        engine,
        {"image": fp16, "mask": int8},
        {"image": (1, 4), "mask": (1, 4)},
        tmp_path / "infer-types",
        trtexec_path=binary,
        input_dtypes={"image": "float16", "mask": "int8"},
    )
    assert result.outputs == []


@pytest.mark.parametrize(
    "backend,expected",
    [("dla0", "--useDLACore=0"), ("dla1-hybrid", "--useDLACore=1")],
)
def test_infer_loads_dla_engine_on_its_serialized_core(tmp_path, monkeypatch, backend, expected):
    engine = tmp_path / "model.plan"
    engine.write_bytes(b"plan")
    raw = tmp_path / "image.bin"
    raw.write_bytes(b"\0" * 4)
    binary = tmp_path / "trtexec"
    binary.write_bytes(b"bin")
    calls = []

    def run(argv, timeout_seconds):
        calls.append(argv)
        export = next(value.split("=", 1)[1] for value in argv if value.startswith("--exportOutput="))
        Path(export).write_text("[]")
        return tensorrt_backend.CommandResult(0, "", "")

    monkeypatch.setattr(tensorrt_backend, "_run_command", run)
    result = tensorrt_backend.infer_engine(
        engine,
        {"image": raw},
        {"image": (1,)},
        tmp_path / "infer-dla",
        backend=backend,
        trtexec_path=binary,
    )
    assert expected in calls[0]
    assert result.backend == backend


def test_probe_reports_binary_version_and_distinguishes_unknown_dla(tmp_path, monkeypatch):
    binary = tmp_path / "trtexec"
    binary.write_bytes(b"bin")

    def run(argv, timeout_seconds):
        assert argv == [str(binary.resolve()), "--help"]
        assert "--version" not in argv
        return tensorrt_backend.CommandResult(
            0, "TensorRT.trtexec [TensorRT v101602]", ""
        )

    monkeypatch.setattr(tensorrt_backend, "_run_command", run)
    monkeypatch.setattr(tensorrt_backend, "_probe_dla_cores", lambda: (None, None))

    result = tensorrt_backend.probe(binary)

    assert result.available is True
    assert result.version == "10.16.2"
    assert result.dla_cores is None
    assert result.dla_detected is None
    assert result.dla_reason == "dla_capability_unknown"


def test_dla_probe_isolated_in_bounded_child(monkeypatch):
    calls = []

    def run(argv, timeout_seconds):
        calls.append((argv, timeout_seconds))
        return tensorrt_backend.CommandResult(
            0, '{"version":"10.16.2.6","cores":2}\n', ""
        )

    monkeypatch.setattr(tensorrt_backend, "_run_command", run)
    assert tensorrt_backend._probe_dla_cores() == (2, "10.16.2.6")
    assert calls[0][0][1:3] == ["-I", "-c"]
    assert calls[0][1] == 10


def test_probe_ignores_dla_count_from_mismatched_python_tensorrt(tmp_path, monkeypatch):
    binary = tmp_path / "trtexec"
    binary.write_bytes(b"bin")
    monkeypatch.setattr(
        tensorrt_backend,
        "_run_command",
        lambda argv, timeout_seconds: tensorrt_backend.CommandResult(
            0, "TensorRT.trtexec [TensorRT v110300]", ""
        ),
    )
    monkeypatch.setattr(
        tensorrt_backend, "_probe_dla_cores", lambda: (0, "10.16.2.6")
    )

    result = tensorrt_backend.probe(binary)

    assert result.version == "11.3.0"
    assert result.dla_runtime_version == "10.16.2.6"
    assert result.dla_cores is None
    assert result.dla_detected is None
    assert result.dla_reason == "python_runtime_mismatch"


def test_probe_treats_failed_version_process_as_unavailable(tmp_path, monkeypatch):
    binary = tmp_path / "trtexec"
    binary.write_bytes(b"bin")
    monkeypatch.setattr(tensorrt_backend, "_probe_dla_cores", lambda: (0, "10.16.2"))
    monkeypatch.setattr(
        tensorrt_backend,
        "_run_command",
        lambda argv, timeout_seconds: (_ for _ in ()).throw(
            tensorrt_backend.BackendError("command_start_failed")
        ),
    )
    result = tensorrt_backend.probe(binary)
    assert result.available is False
    assert result.version is None


def test_software_thumbnail_applies_exif_orientation_and_strips_metadata(tmp_path):
    image_module = pytest.importorskip("PIL.Image")
    src = tmp_path / "source.jpg"
    dst = tmp_path / "thumb.jpg"
    image = image_module.new("RGB", (40, 20), "red")
    exif = image.getexif()
    exif[274] = 6
    image.save(src, exif=exif)

    result = media_backend.thumbnail(src, dst, 10, 10, backend="software")

    assert result.backend == "software"
    assert result.size == (5, 10)
    with image_module.open(dst) as output:
        assert output.size == (5, 10)
        assert not output.getexif()


def test_hardware_thumbnail_uses_fixed_gstreamer_pipeline_and_verifies_output(tmp_path, monkeypatch):
    image_module = pytest.importorskip("PIL.Image")
    src = tmp_path / "source.jpg"
    dst = tmp_path / "thumb.jpg"
    image_module.new("RGB", (40, 20), "blue").save(src)
    gst = tmp_path / "gst-launch-1.0"
    gst.write_bytes(b"bin")
    calls = []

    def run(argv, timeout_seconds):
        calls.append(argv)
        target = Path([value.split("=", 1)[1] for value in argv if value.startswith("location=") and value.endswith(".jpg")][-1])
        image_module.new("RGB", (10, 5), "blue").save(target)
        return tensorrt_backend.CommandResult(0, "", "")

    monkeypatch.setattr(media_backend, "_run_command", run)
    result = media_backend.thumbnail(
        src, dst, 10, 10, backend="hardware", gst_launch_path=gst
    )

    assert result.backend == "hardware"
    assert result.size == (10, 5)
    assert calls[0][0] == str(gst)
    assert "nvjpegdec" in calls[0]
    assert "nvvidconv" in calls[0]
    assert "video/x-raw(memory:NVMM),format=I420,width=10,height=5" in calls[0]
    assert "nvjpegenc" in calls[0]


def test_auto_thumbnail_reports_hardware_failure_and_falls_back(tmp_path, monkeypatch):
    image_module = pytest.importorskip("PIL.Image")
    src = tmp_path / "source.jpg"
    dst = tmp_path / "thumb.jpg"
    image_module.new("RGB", (40, 20), "green").save(src)
    gst = tmp_path / "gst-launch-1.0"
    gst.write_bytes(b"bin")
    monkeypatch.setattr(
        media_backend,
        "_run_command",
        lambda argv, timeout_seconds: tensorrt_backend.CommandResult(
            1, "", "plugin failed"
        ),
    )

    result = media_backend.thumbnail(src, dst, 10, 10, backend="auto", gst_launch_path=gst)

    assert result.backend == "software"
    assert result.fallback_reason == "hardware_command_failed"
    assert result.size == (10, 5)


def test_auto_thumbnail_falls_back_when_hardware_process_cannot_start(tmp_path, monkeypatch):
    image_module = pytest.importorskip("PIL.Image")
    src = tmp_path / "source.jpg"
    dst = tmp_path / "thumb.jpg"
    image_module.new("RGB", (20, 10), "green").save(src)
    gst = tmp_path / "gst-launch-1.0"
    gst.write_bytes(b"bin")
    monkeypatch.setattr(
        media_backend,
        "_run_command",
        lambda argv, timeout_seconds: (_ for _ in ()).throw(
            tensorrt_backend.BackendError("command_start_failed")
        ),
    )
    result = media_backend.thumbnail(src, dst, 10, 10, backend="auto", gst_launch_path=gst)
    assert result.backend == "software"
    assert result.fallback_reason == "hardware_command_failed"


def test_media_probe_reports_plugins_without_claiming_operational_smoke(tmp_path, monkeypatch):
    launch = tmp_path / "gst-launch-1.0"
    inspect = tmp_path / "gst-inspect-1.0"
    launch.write_bytes(b"bin")
    inspect.write_bytes(b"bin")
    monkeypatch.setattr(
        media_backend,
        "_run_command",
        lambda argv, timeout_seconds: tensorrt_backend.CommandResult(
            1 if argv[-1] == "nvvidconv" else 0, "", ""
        ),
    )
    result = media_backend.probe(launch, inspect)
    assert result.available is False
    assert result.plugins == {"nvjpegdec": True, "nvvidconv": False, "nvjpegenc": True}
    assert result.operational is False


def test_thumbnail_rejects_non_jpeg_pixel_bomb_and_existing_destination(tmp_path):
    image_module = pytest.importorskip("PIL.Image")
    png = tmp_path / "source.png"
    image_module.new("RGB", (2, 2)).save(png)
    with pytest.raises(media_backend.MediaError, match="jpeg_required"):
        media_backend.thumbnail(png, tmp_path / "out.jpg", 10, 10)

    jpeg = tmp_path / "source.jpg"
    image_module.new("RGB", (2, 2)).save(jpeg)
    output = tmp_path / "exists.jpg"
    output.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        media_backend.thumbnail(jpeg, output, 10, 10)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(media_backend, "MAX_JPEG_PIXELS", 3)
    try:
        with pytest.raises(media_backend.MediaError, match="jpeg_pixel_limit"):
            media_backend.thumbnail(jpeg, tmp_path / "pixels.jpg", 10, 10)
    finally:
        monkeypatch.undo()


def test_thumbnail_rejects_huge_bounds_before_decode_and_never_upscales(tmp_path, monkeypatch):
    image_module = pytest.importorskip("PIL.Image")
    src = tmp_path / "tiny.jpg"
    image_module.new("RGB", (2, 2)).save(src)
    opened = False
    real_open = media_backend._open_jpeg

    def tracked_open(path):
        nonlocal opened
        opened = True
        return real_open(path)

    monkeypatch.setattr(media_backend, "_open_jpeg", tracked_open)
    with pytest.raises(ValueError, match="invalid_dimensions"):
        media_backend.thumbnail(src, tmp_path / "huge.jpg", 100_000, 2)
    assert opened is False

    result = media_backend.thumbnail(
        src, tmp_path / "not-upscaled.jpg", 100, 100, backend="software"
    )
    assert result.size == (2, 2)
