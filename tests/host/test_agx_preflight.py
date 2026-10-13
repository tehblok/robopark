"""Admission checks for the supplied GEACX1 recovery image and apt plans."""

import subprocess

import pytest
from robopark_host import agx_preflight as agx


def write(root, name, value):
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(value)


def target(root):
    write(root, "proc/device-tree/nvidia,dtsfilename", "geac91_510jx0_r3_0_JP7.2_ga_v1.0.0")
    write(root, "proc/device-tree/compatible", "nvidia,p3701-0004\0nvidia,tegra234\0")
    write(root, "proc/meminfo", "MemTotal: 32000000 kB\n")
    write(root, "etc/nv_tegra_release", "# R39 (release), REVISION: 2.0, GCID: 1\n")
    write(root, "etc/os-release", 'ID=ubuntu\nVERSION_ID="24.04"\n')


def test_exact_recovery_stack_is_recognized(tmp_path):
    target(tmp_path)
    result = agx.identity(tmp_path, machine="aarch64", kernel="6.8.12-1021-tegra")
    assert result["matches_recovery"] is True
    assert result["memory_gib"] > 24
    assert result["l4t"] == "39.2.0"


@pytest.mark.parametrize("change", ["x86", "nx", "kernel", "release", "ram"])
def test_wrong_device_or_stack_is_not_prepared(tmp_path, change):
    target(tmp_path)
    machine, kernel = "aarch64", "6.8.12-1021-tegra"
    if change == "x86":
        machine = "x86_64"
    elif change == "nx":
        write(tmp_path, "proc/device-tree/compatible", "nvidia,p3767-0000\0nvidia,tegra234\0")
    elif change == "kernel":
        kernel = "6.8.0-generic"
    elif change == "release":
        write(tmp_path, "etc/nv_tegra_release", "# R39 (release), REVISION: 2.1, GCID: 1\n")
    else:
        write(tmp_path, "proc/meminfo", "MemTotal: 16000000 kB\n")
    assert agx.identity(tmp_path, machine=machine, kernel=kernel)["matches_recovery"] is False


def test_missing_facts_are_unknown_not_ready(tmp_path):
    result = agx.identity(tmp_path, machine="aarch64", kernel="unknown")
    assert result["matches_recovery"] is False
    assert result["l4t"] is None


@pytest.mark.parametrize(
    "line",
    [
        "Remv libc6 [2.39]",
        "Inst nvidia-l4t-core [39.2.0] (39.2.1 NVIDIA:stable [arm64])",
        "Inst nvidia-l4t-cuda (39.2.1 NVIDIA:stable [arm64])",
        "Inst rb-jetson-service-fan (1.2 vendor [arm64])",
        "Inst linux-image-6.8.0 (6.8 ubuntu [arm64])",
        "Inst nvidia-driver-580 (580 NVIDIA [arm64])",
        "Inst cuda-drivers (580 NVIDIA [arm64])",
        "Inst systemd [255.4] (255.5 Ubuntu [arm64])",
        "Inst udev [255.4] (255.5 Ubuntu [arm64])",
    ],
)
def test_apt_plan_cannot_modify_platform(line):
    with pytest.raises(ValueError, match="protected|remove"):
        agx.parse_apt_plan(line)


def test_apt_plan_pins_every_userspace_dependency():
    plan = agx.parse_apt_plan(
        "Reading package lists...\n"
        "Inst cmake (3.28.3-1build7 Ubuntu:24.04/noble [arm64])\n"
        "Inst libcurl4t64 [8.5.0-2] (8.5.0-2ubuntu1 Ubuntu [arm64])\n"
        "Conf cmake (3.28.3-1build7 Ubuntu [arm64])\n"
    )
    assert plan == ["cmake=3.28.3-1build7", "libcurl4t64=8.5.0-2ubuntu1"]


def test_unknown_apt_action_fails_closed():
    with pytest.raises(ValueError, match="unrecognized"):
        agx.parse_apt_plan("Inst cmake unexpected output")


def test_package_state_must_be_configured():
    assert agx.parse_packages("cmake\tii\t3.28.3\ngit\tiU\t2.43\n") == {"cmake": "3.28.3"}


def test_command_failures_are_summarized_without_raw_output():
    def run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 1, "secret-env-value", "secret-error")

    result = agx.command(["some-command"], run=run)
    assert result == (False, "")


def test_cuda_toolkit_requires_explicit_version_and_no_shell():
    assert agx.toolkit_spec("cuda-toolkit-13-2=13.2.0-1") == "cuda-toolkit-13-2=13.2.0-1"
    for value in [
        "cuda",
        "nvidia-jetpack",
        "cuda-toolkit",
        "cuda-toolkit-13-2",
        "cuda-toolkit-13-2=1;id",
    ]:
        with pytest.raises(ValueError):
            agx.toolkit_spec(value)


def test_truncated_command_output_is_rejected():
    def run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, "x" * (1024 * 1024 + 1), "")

    assert agx.command(["apt-get", "--simulate"], run=run) == (False, "")


def test_prepare_plan_does_not_install_or_refresh_apt(monkeypatch):
    monkeypatch.setattr(agx, "inspect", lambda: {"platform_ready": True})
    monkeypatch.setattr(agx, "package_state", lambda: {"nvidia-l4t-core": "39.2.0"})
    calls = []

    def command(argv, **kwargs):
        calls.append(argv)
        return True, (
            "0 upgraded, 1 newly installed, 0 to remove and 0 not upgraded.\n"
            "Inst cmake (3.28.3 Ubuntu [arm64])\n"
        )

    monkeypatch.setattr(agx, "command", command)
    result = agx.prepare(apply=False)
    assert result["applied"] is False
    assert len(calls) == 1
    assert "--simulate" in calls[0]
    assert "nvidia-l4t-core=39.2.0" in calls[0]


def test_prepare_rejects_partial_apt_plan_before_install(monkeypatch):
    monkeypatch.setattr(agx, "inspect", lambda: {"platform_ready": True})
    monkeypatch.setattr(agx, "package_state", lambda: {})
    monkeypatch.setattr(agx.os, "geteuid", lambda: 0)
    calls = []

    def command(argv, **kwargs):
        calls.append(argv)
        return True, (
            "0 upgraded, 2 newly installed, 0 to remove and 0 not upgraded.\n"
            "Inst cmake (3.28.3 Ubuntu [arm64])\n"
        )

    monkeypatch.setattr(agx, "command", command)
    with pytest.raises(ValueError, match="incomplete"):
        agx.prepare(apply=True)
    assert len(calls) == 1


def test_prepare_refuses_platform_mismatch_before_apt(monkeypatch):
    monkeypatch.setattr(
        agx, "inspect", lambda: {"platform_ready": False, "blockers": ["vendor_cooling_not_ready"]}
    )
    monkeypatch.setattr(agx, "package_state", lambda: pytest.fail("must not query APT"))
    with pytest.raises(ValueError, match="vendor_cooling_not_ready"):
        agx.prepare(apply=True)


def test_prepare_apply_pins_dependencies_and_preserves_bsp(monkeypatch):
    monkeypatch.setattr(agx, "inspect", lambda: {"platform_ready": True})
    monkeypatch.setattr(agx.os, "geteuid", lambda: 0)
    states = iter(
        [
            {"nvidia-l4t-core": "39.2.0"},
            {"nvidia-l4t-core": "39.2.0", "cmake": "3.28.3", "libcurl4t64:arm64": "8.5.0"},
        ]
    )
    monkeypatch.setattr(agx, "package_state", lambda: next(states))
    calls = []

    def command(argv, **kwargs):
        calls.append(argv)
        return True, (
            "0 upgraded, 2 newly installed, 0 to remove and 0 not upgraded.\n"
            "Inst cmake (3.28.3 Ubuntu [arm64])\n"
            "Inst libcurl4t64 (8.5.0 Ubuntu [arm64])\n"
        )

    monkeypatch.setattr(agx, "command", command)
    assert agx.prepare(apply=True)["applied"] is True
    assert len(calls) == 2
    assert "--simulate" not in calls[1]
    assert "cmake=3.28.3" in calls[1]
    assert "libcurl4t64=8.5.0" in calls[1]
    assert "nvidia-l4t-core=39.2.0" in calls[1]
    assert "--no-remove" in calls[1]


def test_host_command_works_before_storage_is_prepared(monkeypatch, capsys):
    from robopark_host import storage_compatibility
    from robopark_host.cli import main

    monkeypatch.setattr(
        agx, "inspect", lambda root: {"platform_ready": True, "native_ai_ready": False}
    )
    monkeypatch.setattr(
        storage_compatibility,
        "require_storage_operations",
        lambda *a, **kw: pytest.fail("read-only check must work before install"),
    )
    assert main(["agx-check"]) == 0
    assert main(["agx-check", "--require-ai"]) == 2
    assert "native_ai_ready" in capsys.readouterr().out


@pytest.mark.parametrize("version", ["11.3.0.1-1+cuda13.4", "10.16.1-1", "10.3.0-1"])
def test_acceleration_does_not_replace_jp72_compute_stack(version):
    with pytest.raises(ValueError, match="acceleration_tensorrt_stack_mismatch"):
        agx.validate_acceleration_versions({"libnvinfer10:arm64": version})


def test_acceleration_plan_adds_runtime_dependencies_and_rejects_newer_candidate(monkeypatch):
    monkeypatch.setattr(agx, "inspect", lambda: {"platform_ready": True})
    monkeypatch.setattr(agx, "package_state", lambda: dict.fromkeys(agx.BUILD_PACKAGES, "1"))
    calls = []

    def command(argv, **kwargs):
        calls.append(argv)
        return (
            True,
            "0 upgraded, 1 newly installed, 0 to remove\nInst libnvinfer11 (11.3.0-1+cuda13.4 NVIDIA [arm64])\n",
        )

    monkeypatch.setattr(agx, "command", command)
    with pytest.raises(ValueError, match="acceleration_tensorrt_stack_mismatch"):
        agx.prepare(apply=False, acceleration=True)
    assert len(calls) == 1
    assert "python3-onnx" in calls[0]
    assert "python3-libnvinfer" in calls[0]
    assert "--simulate" in calls[0]


def test_matching_module_without_carrier_marker_is_rejected(tmp_path):
    target(tmp_path)
    (tmp_path / "proc/device-tree/nvidia,dtsfilename").unlink()
    result = agx.identity(tmp_path, machine="aarch64", kernel=agx.EXPECTED_KERNEL)
    assert result["matches_recovery"] is False
    assert result["carrier_510jx0_r3"] is False


def test_vendor_marker_search_does_not_follow_symlink(tmp_path):
    target(tmp_path)
    marker = tmp_path / "proc/device-tree/nvidia,dtsfilename"
    marker.unlink()
    outside = tmp_path / "outside"
    outside.write_text(agx.CARRIER_MARKER)
    marker.symlink_to(outside)
    assert not agx.carrier_marker(tmp_path)


def test_configuration_only_apt_plan_is_not_silently_accepted():
    with pytest.raises(ValueError, match="pending_configuration"):
        agx.parse_apt_plan("Conf cmake (3.28.3 Ubuntu [arm64])\n")


@pytest.mark.parametrize("name", ["systemd:arm64", "udev:arm64", "flash-kernel:arm64"])
def test_multiarch_cannot_bypass_platform_protection(name):
    with pytest.raises(ValueError, match="protected"):
        agx.parse_apt_plan(f"Inst {name} (1 Ubuntu [arm64])")


@pytest.mark.parametrize(
    "cuda_ok,fan_ok,nvcc_output",
    [
        (True, True, "Cuda compilation tools, release 13.2, V13.2.1"),
        (False, True, "Cuda compilation tools, release 13.2, V13.2.1"),
        (True, False, "Cuda compilation tools, release 13.2, V13.2.1"),
        (True, True, ""),
    ],
)
def test_readiness_requires_driver_and_vendor_cooling(
    tmp_path, monkeypatch, cuda_ok, fan_ok, nvcc_output
):
    import json

    target(tmp_path)
    write(tmp_path, "sys/class/rb_gpio/fan_0-power/value", "1")
    (tmp_path / "sys/class/block/nvme0n1").mkdir(parents=True)
    monkeypatch.setattr(agx.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(agx.platform, "release", lambda: agx.EXPECTED_KERNEL)
    monkeypatch.setattr(agx, "package_state", lambda: dict.fromkeys(agx.BUILD_PACKAGES, "1"))

    def command(argv, **kwargs):
        if argv[0] == "systemctl":
            return fan_ok, ""
        if argv[0] == "findmnt":
            return True, json.dumps(
                {"filesystems": [{"source": "/dev/nvme0n1p1", "fstype": "ext4"}]}
            )
        if argv[-1] == "_cuda-probe":
            return True, json.dumps({"ok": cuda_ok})
        if argv[0] == "/usr/local/cuda/bin/nvcc":
            return True, nvcc_output
        return False, ""

    monkeypatch.setattr(agx, "command", command)
    report = agx.inspect(tmp_path)
    assert report["native_ai_ready"] is bool(cuda_ok and fan_ok and nvcc_output)
    assert report["platform_ready"] is fan_ok
    assert report["storage"]["managed_layout_verified"] is False
    assert report["reference_recovery_commit"] == agx.RECOVERY_COMMIT
