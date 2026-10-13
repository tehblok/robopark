from pathlib import Path

import pytest

CUDA_DRIVER_LIBRARIES = (
    "/usr/lib/aarch64-linux-gnu/libcuda.so.1",
    "/usr/lib/aarch64-linux-gnu/nvidia/libcuda.so.1",
    "/usr/lib/aarch64-linux-gnu/tegra/libcuda.so.1",
)


def _orin_root(root: Path) -> Path:
    device_tree = root / "proc/device-tree"
    device_tree.mkdir(parents=True)
    (device_tree / "model").write_text("NVIDIA Jetson AGX Orin")
    (device_tree / "compatible").write_text("nvidia,tegra234")
    return root


def _touch(root: Path, name: str) -> None:
    path = root / name.lstrip("/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


@pytest.mark.parametrize(
    "gpu_device",
    ("/dev/nvidia0", "/dev/nvhost-ctrl-gpu"),
)
def test_orin_cuda_accepts_gpu_device(tmp_path, gpu_device):
    from robopark_host.capabilities import probe_host_capabilities

    root = _orin_root(tmp_path)
    _touch(root, gpu_device)

    capabilities = probe_host_capabilities(root)

    assert capabilities.cuda_available is True
    assert capabilities.jpeg_backend == "software"
    assert capabilities.hardware_jpeg is False


@pytest.mark.parametrize("driver_library", CUDA_DRIVER_LIBRARIES)
def test_orin_cuda_requires_gpu_device_with_driver_library(tmp_path, driver_library):
    from robopark_host.capabilities import probe_host_capabilities

    root = _orin_root(tmp_path)
    _touch(root, driver_library)

    assert probe_host_capabilities(root).cuda_available is False

    _touch(root, "/dev/nvidia0")

    assert probe_host_capabilities(root).cuda_available is True


def test_orin_cuda_ignores_nvdec_device(tmp_path):
    from robopark_host.capabilities import probe_host_capabilities

    root = _orin_root(tmp_path)
    _touch(root, "/dev/nvhost-nvdec")

    assert probe_host_capabilities(root).cuda_available is False


@pytest.mark.parametrize(
    "incidental_evidence",
    (("/usr/lib/libnvjpeg.so",), ("/dev/nvhost-nvdec", "/usr/lib/libnvjpeg.so")),
)
def test_orin_cuda_ignores_jpeg_evidence(tmp_path, incidental_evidence):
    from robopark_host.capabilities import probe_host_capabilities

    root = _orin_root(tmp_path)
    for evidence in incidental_evidence:
        _touch(root, evidence)

    assert probe_host_capabilities(root).cuda_available is False


def test_generic_host_does_not_advertise_cuda_from_gpu_device(tmp_path):
    from robopark_host.capabilities import probe_host_capabilities

    device_tree = tmp_path / "proc/device-tree"
    device_tree.mkdir(parents=True)
    (device_tree / "model").write_text("Generic ARM")
    (device_tree / "compatible").write_text("linux,dummy-virt")
    _touch(tmp_path, "/dev/nvidia0")

    assert probe_host_capabilities(tmp_path).cuda_available is False
