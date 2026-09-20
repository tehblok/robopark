"""Fail-soft hardware discovery; acceleration is optional and health-gated."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from .health_projection import update_public_health
from .state import atomic_write_json


@dataclass(frozen=True, slots=True)
class HostCapabilities:
    profile: str
    model: str
    jpeg_backend: str
    hardware_jpeg: bool
    npu_available: bool
    cuda_available: bool
    nvme_available: bool

    def as_dict(self) -> dict[str, str | bool]:
        return asdict(self)


def _text(path: Path) -> str:
    try:
        return path.read_bytes().replace(b"\0", b",").decode("utf-8", "replace").strip()
    except OSError:
        return ""


def _exists(root: Path, *names: str) -> bool:
    return any((root / name.lstrip("/")).exists() for name in names)


def probe_jpeg_backend(backend: str) -> bool:
    """No hardware thumbnail consumer is installed, so never advertise one.

    Library/plugin discovery is not an encode/decode health check.  A future
    thumbnail owner must replace this with a bounded round trip and consume the
    selected backend before returning true.
    """
    return False


def probe_host_capabilities(
    root: Path = Path("/"),
    *,
    jpeg_health_probe: Callable[[str], bool] | None = None,
) -> HostCapabilities:
    model = _text(root / "proc/device-tree/model")
    compatible = _text(root / "proc/device-tree/compatible")
    identity = f"{model} {compatible}".casefold()
    is_orin = "orin" in identity or "tegra234" in identity
    is_new_vim4 = any(
        name in identity for name in ("new vim4", "new-vim4", "vim4n", "vim4-new")
    )
    if "vim4" in identity and _exists(root, "/dev/aml-npu"):
        is_new_vim4 = True
    is_vim4 = is_new_vim4 or "vim4" in identity
    profile = (
        "orin"
        if is_orin
        else "new-vim4"
        if is_new_vim4
        else "vim4"
        if is_vim4
        else "generic-arm"
    )
    npu = is_new_vim4 and _exists(root, "/dev/aml-npu", "/dev/galcore")
    cuda = is_orin and _exists(
        root,
        "/dev/nvhost-nvdec",
        "/dev/nvhost-ctrl-gpu",
        "/usr/lib/libcuda.so",
        "/usr/lib/libnvjpeg.so",
    )
    nvme = any(
        (root / "sys/class/block" / name).exists() for name in ("nvme0n1", "nvme1n1")
    )
    # Hardware discovery remains informational (CUDA/NPU) and can never become
    # a startup dependency. There is currently no hardware thumbnail consumer,
    # so selecting a backend would be a false capability advertisement.
    backend = "software"
    return HostCapabilities(
        profile, model or "unknown", backend, backend != "software", npu, cuda, nvme
    )


def write_capabilities(
    path: Path, capabilities: HostCapabilities, *, public_path: Path | None = None
) -> None:
    atomic_write_json(path, capabilities.as_dict())
    if public_path is not None:
        public = capabilities.as_dict()
        public.pop("model", None)
        update_public_health(public_path, capabilities=public)
