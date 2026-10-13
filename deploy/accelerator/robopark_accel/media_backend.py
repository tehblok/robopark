"""Bounded JPEG thumbnail generation with optional Jetson GStreamer hardware."""

from __future__ import annotations

import importlib
import math
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .tensorrt_backend import BackendError, _run_command

MediaBackend = Literal["auto", "hardware", "software"]
GST_LAUNCH_PATHS = (Path("/usr/bin/gst-launch-1.0"),)
GST_INSPECT_PATHS = (Path("/usr/bin/gst-inspect-1.0"),)
MAX_JPEG_BYTES = 32 * 1024 * 1024
MAX_JPEG_PIXELS = 40_000_000
MAX_OUTPUT_BYTES = 32 * 1024 * 1024
MAX_THUMBNAIL_DIMENSION = 4096
MAX_THUMBNAIL_PIXELS = 16_000_000


class MediaError(RuntimeError):
    """A safe, stable error from the media boundary."""


@dataclass(frozen=True)
class ThumbnailResult:
    output_path: Path
    backend: str
    size: tuple[int, int]
    fallback_reason: str | None


@dataclass(frozen=True)
class MediaProbe:
    available: bool
    gst_launch_path: Path | None
    plugins: dict[str, bool]
    operational: bool


def _pil():
    try:
        image = importlib.import_module("PIL.Image")
        image_ops = importlib.import_module("PIL.ImageOps")
        return image, image_ops
    except ImportError as exc:
        raise MediaError("pillow_dependency_missing") from exc


def _binary(explicit: Path | None, defaults: tuple[Path, ...]) -> Path | None:
    candidates = (Path(explicit),) if explicit is not None else defaults
    return next((path.resolve() for path in candidates if path.is_file()), None)


def _fit(source: tuple[int, int], bounds: tuple[int, int]) -> tuple[int, int]:
    source_width, source_height = source
    width, height = bounds
    scale = min(1.0, width / source_width, height / source_height)
    return max(1, round(source_width * scale)), max(1, round(source_height * scale))


def _open_jpeg(path: Path):
    image_module, image_ops = _pil()
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.stat().st_size < 1 or path.stat().st_size > MAX_JPEG_BYTES:
        raise MediaError("jpeg_size_limit")
    try:
        image = image_module.open(path)
        if image.format != "JPEG":
            image.close()
            raise MediaError("jpeg_required")
        if math.prod(image.size) > MAX_JPEG_PIXELS:
            image.close()
            raise MediaError("jpeg_pixel_limit")
        image.load()
        oriented = image_ops.exif_transpose(image)
        converted = oriented.convert("RGB")
        if oriented is not image:
            oriented.close()
        image.close()
        return converted
    except MediaError:
        raise
    except Exception as exc:
        raise MediaError("jpeg_decode_failed") from exc


def _verify(path: Path, expected: tuple[int, int]) -> None:
    image_module, _ = _pil()
    if not path.is_file() or not (0 < path.stat().st_size <= MAX_OUTPUT_BYTES):
        raise MediaError("thumbnail_output_invalid")
    try:
        with image_module.open(path) as image:
            image.load()
            if image.format != "JPEG" or image.size != expected or image.getexif():
                raise MediaError("thumbnail_output_invalid")
    except MediaError:
        raise
    except Exception as exc:
        raise MediaError("thumbnail_output_invalid") from exc


def _publish(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as incoming, destination.open("xb") as outgoing:
        shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)


def probe(gst_launch_path: Path | None = None, gst_inspect_path: Path | None = None) -> MediaProbe:
    launch = _binary(gst_launch_path, GST_LAUNCH_PATHS)
    inspect = _binary(gst_inspect_path, GST_INSPECT_PATHS)
    plugins = {}
    for plugin in ("nvjpegdec", "nvvidconv", "nvjpegenc"):
        if inspect is None:
            plugins[plugin] = False
        else:
            try:
                result = _run_command([str(inspect), plugin], 10)
                plugins[plugin] = result.returncode == 0
            except BackendError:
                plugins[plugin] = False
    return MediaProbe(bool(launch and all(plugins.values())), launch, plugins, False)


def thumbnail(
    src: Path,
    dst: Path,
    width: int,
    height: int,
    backend: MediaBackend = "auto",
    gst_launch_path: Path | None = None,
    timeout_seconds: int = 60,
) -> ThumbnailResult:
    src, dst = Path(src), Path(dst)
    if backend not in ("auto", "hardware", "software"):
        raise ValueError("invalid_backend")
    if (
        type(width) is not int
        or type(height) is not int
        or width < 1
        or height < 1
        or width > MAX_THUMBNAIL_DIMENSION
        or height > MAX_THUMBNAIL_DIMENSION
        or width * height > MAX_THUMBNAIL_PIXELS
    ):
        raise ValueError("invalid_dimensions")
    if dst.exists():
        raise FileExistsError(dst)
    image = _open_jpeg(src)
    fitted = _fit(image.size, (width, height))
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".robopark-thumb-", dir=dst.parent) as temporary:
        temporary_path = Path(temporary)
        normalized = temporary_path / "normalized.jpg"
        candidate = temporary_path / "output.jpg"
        image.save(normalized, "JPEG", quality=95)
        image.close()
        fallback_reason = None
        selected = backend
        if backend in ("auto", "hardware"):
            binary = _binary(gst_launch_path, GST_LAUNCH_PATHS)
            if binary is None:
                fallback_reason = "hardware_command_unavailable"
            else:
                caps = f"video/x-raw(memory:NVMM),format=I420,width={fitted[0]},height={fitted[1]}"
                argv = [
                    str(binary),
                    "-q",
                    "filesrc",
                    f"location={normalized}",
                    "!",
                    "nvjpegdec",
                    "!",
                    "nvvidconv",
                    "!",
                    caps,
                    "!",
                    "nvjpegenc",
                    "!",
                    "filesink",
                    f"location={candidate}",
                ]
                try:
                    result = _run_command(argv, timeout_seconds)
                except BackendError:
                    fallback_reason = "hardware_command_failed"
                else:
                    if result.returncode:
                        fallback_reason = "hardware_command_failed"
                if fallback_reason is None:
                    try:
                        _verify(candidate, fitted)
                    except MediaError:
                        fallback_reason = "hardware_output_invalid"
            if fallback_reason is None:
                selected = "hardware"
            elif backend == "hardware":
                raise MediaError(fallback_reason)
            else:
                selected = "software"
        if selected == "software":
            image_module, image_ops = _pil()
            with image_module.open(normalized) as normalized_image:
                resized = image_ops.contain(normalized_image, fitted)
                resized.convert("RGB").save(candidate, "JPEG", quality=90)
            _verify(candidate, fitted)
        _publish(candidate, dst)
    return ThumbnailResult(dst.resolve(), selected, fitted, fallback_reason)
