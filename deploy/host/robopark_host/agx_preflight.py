"""GEACX1 RC43 companion: inspect the target and prepare native build tools.

No flashing, storage provisioning, power changes, or model downloads. CUDA is
probed in a short-lived child, so a broken driver cannot hang the diagnostic.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

RECOVERY_COMMIT = "7611b523e3311ba08d821449236736590ec23e35"
EXPECTED_KERNEL = "6.8.12-1021-tegra"
CARRIER_MARKER = "geac91_510jx0_r3_0_JP7.2_ga_v1.0.0"
BUILD_PACKAGES = (
    "build-essential",
    "cmake",
    "git",
    "pkg-config",
    "libssl-dev",
    "libcurl4-openssl-dev",
    "ca-certificates",
    "curl",
)
ACCELERATION_PACKAGES = (
    "python3-venv",
    "python3-pil",
    "python3-numpy",
    "python3-onnx",
    "gstreamer1.0-tools",
    "gstreamer1.0-plugins-base",
    "libnvinfer-bin",
    "python3-libnvinfer",
)
PROTECTED = re.compile(
    r"^(?:nvidia-|libnvidia-|rb-|tztek-|linux-|systemd(?:-|$)|udev$|"
    r"libudev|libsystemd|initramfs-|flash-kernel$|grub|shim-|u-boot|"
    r"cuda-drivers|cuda-compat|firmware-)"
)
ENV = {
    "PATH": "/usr/local/cuda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin",
    "LANG": "C",
    "LC_ALL": "C",
    "DEBIAN_FRONTEND": "noninteractive",
}
APT_OPTIONS = ["--no-remove", "--no-install-recommends"]


def read(root: Path, name: str) -> str:
    try:
        with (root / name).open("rb") as stream:
            return stream.read(65536).decode("utf-8", "replace").strip()
    except OSError:
        return ""


def carrier_marker(root: Path) -> bool:
    """Bounded search in the live DT, matching the vendor POSTCHECK contract."""
    base = root / "proc/device-tree"
    examined = 0
    # /proc/device-tree itself is normally a symlink into /sys/firmware. Walk
    # that one known root, never follow symlink children outside the tree.
    for directory, directories, files in os.walk(base, followlinks=False):
        directories[:] = sorted(d for d in directories if not (Path(directory) / d).is_symlink())
        for name in sorted(files):
            examined += 1
            if examined > 4096:
                return False
            path = Path(directory) / name
            if not path.is_symlink() and CARRIER_MARKER in read(root, str(path.relative_to(root))):
                return True
    return False


def identity(root: Path, *, machine: str, kernel: str) -> dict:
    compatible = read(root, "proc/device-tree/compatible").split("\0")
    release = re.search(r"# R(\d+) .*REVISION:\s*([\d.]+)", read(root, "etc/nv_tegra_release"))
    l4t = ".".join(release.groups()) if release else None
    memory = re.search(r"^MemTotal:\s*(\d+) kB", read(root, "proc/meminfo"), re.M)
    gib = round(int(memory[1]) / 1024**2, 2) if memory else None
    os_values = dict(
        re.findall(r'^(ID|VERSION_ID)=["\']?([^"\'\n]+)', read(root, "etc/os-release"), re.M)
    )
    agx = "nvidia,p3701-0004" in compatible and "nvidia,tegra234" in compatible
    carrier = carrier_marker(root)
    return {
        "machine": machine,
        "agx_p3701_0004": agx,
        "carrier_510jx0_r3": carrier,
        "l4t": l4t,
        "kernel": kernel,
        "os": os_values,
        "memory_gib": gib,
        "matches_recovery": bool(
            agx
            and carrier
            and machine == "aarch64"
            and kernel == EXPECTED_KERNEL
            and l4t == "39.2.0"
            and gib
            and gib >= 24
            and os_values == {"ID": "ubuntu", "VERSION_ID": "24.04"}
        ),
    }


def command(argv: list[str], *, run=subprocess.run, timeout=20) -> tuple[bool, str]:
    try:
        result = run(
            argv, capture_output=True, text=True, timeout=timeout, env=ENV, stdin=subprocess.DEVNULL
        )
    except (OSError, subprocess.SubprocessError):
        return False, ""
    # Raw stderr can contain repository credentials or signed download URLs.
    if result.returncode or len(result.stdout) > 1024 * 1024:
        return False, ""
    return True, result.stdout.strip()


def parse_packages(output: str) -> dict[str, str]:
    packages = {}
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) == 3 and fields[1].strip() == "ii":
            packages[fields[0]] = fields[2]
    return packages


def package_state() -> dict[str, str]:
    ok, output = command(
        ["dpkg-query", "-W", "-f=${binary:Package}\t${db:Status-Abbrev}\t${Version}\n"]
    )
    if not ok:
        raise ValueError("dpkg_state_unavailable")
    return parse_packages(output)


def package_version(packages: dict[str, str], name: str) -> str | None:
    return packages.get(name) or packages.get(name + ":arm64")


def parse_apt_plan(output: str) -> list[str]:
    result = []
    configured = set()
    for line in output.splitlines():
        if line.startswith(("Remv ", "Purg ")):
            raise ValueError("apt_would_remove_packages")
        if not line.startswith(("Inst ", "Conf ")):
            continue
        match = re.match(
            r"^(?:Inst|Conf) ([a-z0-9][a-z0-9+.:_-]*) (?:\[[^\]]+\] )?\(([^\s()]+)", line
        )
        if not match:
            raise ValueError("apt_plan_unrecognized")
        name, version = match.groups()
        if PROTECTED.match(name.split(":", 1)[0]):
            raise ValueError(f"apt_protected_package:{name}")
        if line.startswith("Inst "):
            result.append(f"{name}={version}")
        else:
            configured.add(name)
    if configured - {item.split("=", 1)[0] for item in result}:
        raise ValueError("apt_pending_configuration_requires_repair")
    return result


def toolkit_spec(value: str) -> str:
    if not re.fullmatch(r"cuda-toolkit-\d+-\d+=[a-zA-Z0-9.+:~_-]+", value):
        raise ValueError("cuda_toolkit_requires_exact_package_and_version")
    return value


def cuda_probe() -> dict:
    try:
        library = ctypes.CDLL("libcuda.so.1")
        code = library.cuInit(0)
        if code:
            return {"ok": False, "reason": f"cuInit:{code}"}
        count = ctypes.c_int()
        code = library.cuDeviceGetCount(ctypes.byref(count))
        if code or count.value < 1:
            return {"ok": False, "reason": "no_cuda_device"}
        device, major, minor = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
        if library.cuDeviceGet(ctypes.byref(device), 0) or library.cuDeviceComputeCapability(
            ctypes.byref(major), ctypes.byref(minor), device
        ):
            return {"ok": False, "reason": "cuda_device_query_failed"}
        capability = f"{major.value}.{minor.value}"
        return {"ok": capability == "8.7", "compute_capability": capability}
    except (OSError, AttributeError):
        return {"ok": False, "reason": "cuda_driver_unavailable"}


def inspect(root: Path = Path("/")) -> dict:
    facts = identity(root, machine=platform.machine(), kernel=platform.release())
    report = {
        "schema": 1,
        "reference_recovery_commit": RECOVERY_COMMIT,
        "identity": facts,
        "platform_ready": False,
        "native_ai_ready": False,
    }
    if not facts["matches_recovery"]:
        report["blockers"] = ["recovery_identity_mismatch"]
        return report
    fan_active, _ = command(["systemctl", "is-active", "--quiet", "rb-jetson-service-fan.service"])
    fan_enabled, _ = command(
        ["systemctl", "is-enabled", "--quiet", "rb-jetson-service-fan.service"]
    )
    fan_nodes = bool(list((root / "sys/class/rb_gpio").glob("fan_*-power/value")))
    fan_ok = fan_active and fan_enabled and fan_nodes
    report["cooling"] = {"active": fan_active, "enabled": fan_enabled, "gpio_present": fan_nodes}
    ok, mount = command(["findmnt", "--json", "-o", "SOURCE,FSTYPE", "/"])
    try:
        report["root_mount"] = json.loads(mount)["filesystems"][0] if ok else {}
    except (ValueError, KeyError, IndexError):
        report["root_mount"] = {}
    nvme = any(
        re.fullmatch(r"nvme\d+n\d+", p.name) for p in (root / "sys/class/block").glob("nvme*")
    )
    free_gib = round(shutil.disk_usage(root).free / 1024**3, 2)
    report["storage"] = {
        "nvme_present": nvme,
        "root_free_gib": free_gib,
        "managed_layout_verified": False,
    }
    report["cpu_count"] = os.cpu_count()
    report["profile_hint"] = "orin" if (os.cpu_count() or 0) >= 8 else "vim4-safe"
    _, power = command(["nvpmodel", "-q"])
    report["power_mode"] = power or None
    report["monitoring"] = {
        tool: shutil.which(tool, path=ENV["PATH"]) is not None
        for tool in ("tegrastats", "nvpmodel", "jetson_clocks")
    }
    try:
        packages = package_state()
    except ValueError:
        packages = {}
    report["build_packages_missing"] = [
        name for name in BUILD_PACKAGES if not package_version(packages, name)
    ]
    report["compute_packages"] = {
        name: version
        for name, version in packages.items()
        if name.startswith(("cuda-toolkit-", "nvidia-l4t-cuda", "libcudnn", "libnvinfer"))
    }
    nvcc_ok, nvcc = command(["/usr/local/cuda/bin/nvcc", "--version"])
    version = re.search(r"release ([\d.]+)", nvcc)
    report["cuda_toolkit"] = {
        "nvcc": nvcc_ok and version is not None,
        "version": version[1] if version else None,
    }
    ok, cuda = command([sys.executable, "-I", str(Path(__file__).resolve()), "_cuda-probe"])
    try:
        report["cuda_driver"] = (
            json.loads(cuda) if ok else {"ok": False, "reason": "probe_failed_or_timed_out"}
        )
    except ValueError:
        report["cuda_driver"] = {"ok": False, "reason": "probe_invalid"}
    _, docker = command(["docker", "info", "--format", "{{json .DockerRootDir}}"])
    try:
        docker_root = json.loads(docker)
    except ValueError:
        docker_root = None
    compose_ok, _ = command(["docker", "compose", "version"])
    buildx_ok, _ = command(["docker", "buildx", "version"])
    report["docker"] = {
        "reachable": docker_root is not None,
        "data_root": docker_root,
        "compose": compose_ok,
        "buildx": buildx_ok,
    }
    blockers = []
    if not fan_ok:
        blockers.append("vendor_cooling_not_ready")
    if not nvme:
        blockers.append("nvme_not_found")
    if free_gib < 12:
        blockers.append("root_free_below_12gib")
    if not report["root_mount"]:
        blockers.append("root_mount_unknown")
    report["blockers"] = blockers
    report["platform_ready"] = not blockers
    report["native_ai_ready"] = bool(
        not blockers
        and report["cuda_toolkit"]["nvcc"]
        and report["cuda_driver"].get("ok")
        and not report["build_packages_missing"]
    )
    report["notes"] = [
        "Storage layout must separately pass RC43 storage check before installation.",
        "CUDA driver enumeration is not an inference or service-user smoke test.",
        "TensorRT/DLA and hardware JPEG/video have no consumer in RC43.",
        "Model registration and ai-check are required after installation.",
    ]
    return report


def validate_acceleration_versions(packages: dict[str, str]) -> None:
    for name, version in packages.items():
        base = name.split(":", 1)[0]
        if base.startswith(
            ("libnvinfer", "libnvonnxparsers", "python3-libnvinfer", "tensorrt")
        ) and (
            not re.match(r"^(?:\d+:)?10\.16\.2(?:[.+~-]|$)", version)
            or ("+cuda" in version and not re.search(r"\+cuda13\.2(?:[.~-]|$)", version))
        ):
            raise ValueError("acceleration_tensorrt_stack_mismatch")


def prepare(*, apply: bool, cuda_toolkit: str | None = None, acceleration: bool = False) -> dict:
    facts = inspect()
    if not facts["platform_ready"]:
        raise ValueError("platform_preflight_failed:" + ",".join(facts["blockers"]))
    if apply and os.geteuid() != 0:
        raise ValueError("root_required_for_apply")
    installed = package_state()
    if acceleration:
        validate_acceleration_versions(installed)
        if cuda_toolkit and not cuda_toolkit.startswith("cuda-toolkit-13-2="):
            raise ValueError("acceleration_cuda_stack_mismatch")
    wanted = BUILD_PACKAGES + (ACCELERATION_PACKAGES if acceleration else ())
    requested = [name for name in wanted if not package_version(installed, name)]
    if cuda_toolkit:
        requested.append(toolkit_spec(cuda_toolkit))
    # Explicit installed versions keep the carrier BSP unchanged even if APT
    # recomputes dependencies. A simulated change of a protected package fails.
    protected = sorted(
        f"{name}={version}"
        for name, version in installed.items()
        if PROTECTED.match(name.split(":", 1)[0])
    )
    if not requested:
        return {"state": "unchanged", "packages": [], "applied": False}
    ok, output = command(
        ["apt-get", "--simulate", *APT_OPTIONS, "install", *requested, *protected], timeout=120
    )
    if not ok:
        raise ValueError("apt_simulation_failed_check_local_apt_sources")
    planned = parse_apt_plan(output)
    if acceleration:
        validate_acceleration_versions(dict(item.split("=", 1) for item in planned))
    summary = re.search(r"(\d+) upgraded, (\d+) newly installed, (\d+) to remove", output)
    if not summary or int(summary[3]) or int(summary[1]) + int(summary[2]) != len(planned):
        raise ValueError("apt_plan_incomplete")
    result = {
        "state": "planned",
        "packages": planned,
        "applied": False,
        "cuda_toolkit": cuda_toolkit,
        "acceleration": acceleration,
        "note": "CUDA package must be verified against the installed vendor JetPack stack.",
    }
    if apply and planned:
        # Every changing dependency is version-pinned, not just the meta-package.
        ok, _ = command(
            ["apt-get", "-y", *APT_OPTIONS, "install", *planned, *protected], timeout=3600
        )
        if not ok:
            raise ValueError("apt_install_failed_inspect_var_log_apt_no_automatic_retry")
        after = package_state()
        if any(
            package_version(after, item.split("=")[0]) != item.split("=", 1)[1]
            for item in planned + protected
        ):
            raise ValueError("apt_postcheck_failed")
        result.update(state="installed", applied=True)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    check = commands.add_parser("check", help="Read-only GEACX1 platform and CUDA check")
    check.add_argument("--require-ai", action="store_true")
    preparation = commands.add_parser(
        "prepare", help="Plan native build dependencies; apply explicitly"
    )
    preparation.add_argument("--apply", action="store_true")
    preparation.add_argument(
        "--acceleration", action="store_true", help="Add JP7.2 TensorRT/Python/JPEG dependencies"
    )
    preparation.add_argument(
        "--cuda-toolkit", help="Verified cuda-toolkit-M-N=EXACT_VERSION from target APT"
    )
    commands.add_parser("_cuda-probe", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.action == "_cuda-probe":
            report = cuda_probe()
        elif args.action == "prepare":
            report = prepare(
                apply=args.apply, cuda_toolkit=args.cuda_toolkit, acceleration=args.acceleration
            )
        else:
            report = inspect()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if args.action == "check":
            return 0 if report["native_ai_ready" if args.require_ai else "platform_ready"] else 2
        return 0
    except (OSError, ValueError) as error:
        # Only controlled codes; never echo arbitrary OS errors (paths may be private).
        reason = str(error) if isinstance(error, ValueError) else "system_operation_failed"
        print(json.dumps({"state": "blocked", "reason": reason}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
