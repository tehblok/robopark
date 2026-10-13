#!/usr/bin/env python3
"""Plan or install the optional GEACX1 accelerator companion.

The default operation is read-only.  ``--apply`` is required before files or
systemd state are changed; dependencies are only reported and never installed.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import pwd
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

if __name__ == "__main__" and sys.version_info < (3, 12):
    print(json.dumps({"state": "blocked", "reason": "python_3_12_required"}))
    raise SystemExit(2)

try:
    from robopark_host.agx_preflight import inspect as inspect_platform
    from robopark_host.storage_layout import StorageError, require_storage
except ImportError:  # Direct execution from an unpacked Robopark release.
    _HOST_TOOLS = Path(__file__).resolve().parents[1] / "host"
    if str(_HOST_TOOLS) not in sys.path:
        sys.path.insert(0, str(_HOST_TOOLS))
    from robopark_host.agx_preflight import inspect as inspect_platform
    from robopark_host.storage_layout import StorageError, require_storage


UNIT_NAMES = ("robopark-accelerator.service", "robopark-edge-llm.service")
MODULES = ("PIL", "numpy", "onnx")
EXECUTABLES = ("gst-launch-1.0", "trtexec")


class InstallError(ValueError):
    """A controlled installation rejection suitable for operator output."""


def _regular_file(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError as error:
        raise InstallError("source_payload_missing") from error
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise InstallError("source_payload_unsafe")


def _payload(source: Path) -> list[tuple[Path, Path]]:
    source = Path(source)
    package = source / "robopark_accel"
    try:
        package_info = package.lstat()
        source_info = source.lstat()
    except OSError as error:
        raise InstallError("source_payload_missing") from error
    if (
        not stat.S_ISDIR(source_info.st_mode)
        or source.is_symlink()
        or not stat.S_ISDIR(package_info.st_mode)
        or package.is_symlink()
    ):
        raise InstallError("source_payload_unsafe")

    result: list[tuple[Path, Path]] = []
    launcher = source / "robopark-accel"
    _regular_file(launcher)
    result.append((launcher, Path("robopark-accel")))
    for path in sorted(package.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        _regular_file(path)
        result.append((path, Path("robopark_accel") / path.relative_to(package)))
    if not any(relative == Path("robopark_accel/__init__.py") for _, relative in result):
        raise InstallError("source_payload_missing")
    for name in UNIT_NAMES:
        unit = source / "systemd" / name
        _regular_file(unit)
        result.append((unit, Path("systemd") / name))
    for path, relative in result:
        if relative == Path("robopark-accel") or relative.suffix == ".py":
            try:
                compile(path.read_text(encoding="utf-8"), relative.as_posix(), "exec")
            except (OSError, UnicodeError, SyntaxError) as error:
                raise InstallError("source_python_invalid") from error
    return result


def _manifest(payload: list[tuple[Path, Path]]) -> tuple[str, dict[str, str]]:
    files: dict[str, str] = {}
    digest = hashlib.sha256()
    for source, relative in payload:
        data = source.read_bytes()
        file_hash = hashlib.sha256(data).hexdigest()
        name = relative.as_posix()
        files[name] = file_hash
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(file_hash.encode("ascii") + b"\n")
    return digest.hexdigest(), files


def _find_executable(name: str) -> str | None:
    if name == "trtexec":
        for candidate in (
            Path("/usr/src/tensorrt/bin/trtexec"),
            Path("/usr/local/tensorrt/bin/trtexec"),
        ):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return shutil.which(name)


def probe_dependencies() -> dict:
    modules = {name: importlib.util.find_spec(name) is not None for name in MODULES}
    executables = {name: _find_executable(name) is not None for name in EXECUTABLES}
    missing = [f"python:{name}" for name, present in modules.items() if not present]
    missing.extend(f"executable:{name}" for name, present in executables.items() if not present)
    capabilities = {
        "tensorrt": modules["numpy"] and modules["onnx"] and executables["trtexec"],
        # Pillow performs bounded decode, EXIF normalization and output
        # verification around the GStreamer hardware path.
        "hardware_media": modules["PIL"] and executables["gst-launch-1.0"],
        "software_media": modules["PIL"],
    }
    dependencies_present = all(modules.values()) and all(executables.values())
    return {
        "python_modules": modules,
        "executables": executables,
        "capabilities": capabilities,
        "missing": missing,
        "dependencies_present": dependencies_present,
        # File discovery cannot prove CUDA execution, DLA compilation or media
        # decoding.  Acceptance on the target promotes each capability later.
        "operational": False,
        "acceptance_required": True,
    }


def _run(argv: list[str]) -> None:
    subprocess.run(
        argv,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"},
        timeout=120,
    )


def _account_ids(runner: Callable[[list[str]], None]) -> tuple[int, int]:
    try:
        account = pwd.getpwnam("robopark-accel")
    except KeyError:
        runner(
            [
                "useradd",
                "--system",
                "--user-group",
                "--groups",
                "video,render",
                "--home-dir",
                "/nonexistent",
                "--shell",
                "/usr/sbin/nologin",
                "robopark-accel",
            ]
        )
        try:
            account = pwd.getpwnam("robopark-accel")
        except KeyError as error:
            raise InstallError("service_account_creation_failed") from error
    if account.pw_uid == 0:
        raise InstallError("service_account_must_be_nonroot")
    return account.pw_uid, 10001


def _guard_destinations(root: Path) -> None:
    """Reject links or non-directories before any privileged mutation."""
    directories = (
        Path("opt"),
        Path("opt/robopark-accelerator"),
        Path("opt/robopark-accelerator/releases"),
        Path("etc"),
        Path("etc/systemd"),
        Path("etc/systemd/system"),
        Path("var"),
        Path("var/lib"),
        Path("var/lib/robopark"),
        Path("var/lib/robopark/accelerator"),
        Path("var/lib/robopark/accelerator/models"),
        Path("var/lib/robopark/accelerator/work"),
    )
    candidates = (root, *(root / relative for relative in directories))
    for candidate in candidates:
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise InstallError("destination_ancestor_unsafe") from error
        if candidate.is_symlink() or not stat.S_ISDIR(info.st_mode):
            raise InstallError("destination_ancestor_unsafe")
    unit_directory = root / "etc/systemd/system"
    for name in UNIT_NAMES:
        target = unit_directory / name
        try:
            info = target.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise InstallError("unit_destination_unsafe") from error
        if not stat.S_ISREG(info.st_mode) or target.is_symlink():
            raise InstallError("unit_destination_unsafe")


def _write_release(
    root: Path,
    source: Path,
    payload: list[tuple[Path, Path]],
    release_id: str,
    files: dict[str, str],
) -> Path:
    releases = root / "opt/robopark-accelerator/releases"
    releases.mkdir(parents=True, exist_ok=True, mode=0o755)
    release = releases / release_id
    expected_manifest = (
        json.dumps(
            {"schema": 1, "release_id": release_id, "files": files},
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    if release.exists():
        manifest = release / "MANIFEST.json"
        if release.is_symlink() or not release.is_dir():
            raise InstallError("release_destination_unsafe")
        try:
            actual = manifest.read_text(encoding="utf-8")
        except OSError as error:
            raise InstallError("release_destination_unsafe") from error
        if actual != expected_manifest:
            raise InstallError("release_destination_mismatch")
        runtime_files = {
            name: digest for name, digest in files.items() if not name.startswith("systemd/")
        }
        expected_names = set(runtime_files) | {"MANIFEST.json"}
        actual_names = set()
        for path in release.rglob("*"):
            relative = path.relative_to(release).as_posix()
            if path.is_symlink():
                raise InstallError("release_destination_mismatch")
            if path.is_dir():
                continue
            if not path.is_file():
                raise InstallError("release_destination_mismatch")
            actual_names.add(relative)
            if relative != "MANIFEST.json" and hashlib.sha256(
                path.read_bytes()
            ).hexdigest() != runtime_files.get(relative):
                raise InstallError("release_destination_mismatch")
        if actual_names != expected_names:
            raise InstallError("release_destination_mismatch")
        return release

    staging = releases / f".{release_id}.{os.getpid()}.staging"
    try:
        staging.mkdir(mode=0o755)
        (staging / "robopark_accel").mkdir(mode=0o755)
        for source_path, relative in payload:
            if relative.parts[0] == "systemd":
                continue
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            shutil.copyfile(source_path, target)
            target.chmod(0o444)
        manifest = staging / "MANIFEST.json"
        manifest.write_text(expected_manifest, encoding="utf-8")
        manifest.chmod(0o444)
        for directory in sorted(
            (path for path in staging.rglob("*") if path.is_dir()), reverse=True
        ):
            directory.chmod(0o555)
        staging.chmod(0o555)
        os.replace(staging, release)
    except Exception:
        if staging.exists() and not staging.is_symlink():
            shutil.rmtree(staging)
        raise
    return release


def _atomic_symlink(link: Path, target: str) -> None:
    if link.exists() and not link.is_symlink():
        raise InstallError("current_link_unsafe")
    temporary = link.with_name(f".{link.name}.{os.getpid()}.tmp")
    try:
        temporary.symlink_to(target)
        os.replace(temporary, link)
    finally:
        if temporary.is_symlink():
            temporary.unlink()


def _install_units(root: Path, source: Path) -> None:
    destination = root / "etc/systemd/system"
    destination.mkdir(parents=True, exist_ok=True, mode=0o755)
    for name in UNIT_NAMES:
        target = destination / name
        if target.exists() and target.is_symlink():
            raise InstallError("unit_destination_unsafe")
        temporary = destination / f".{name}.{os.getpid()}.tmp"
        shutil.copyfile(source / "systemd" / name, temporary)
        temporary.chmod(0o644)
        os.replace(temporary, target)


def _prepare_state(root: Path, uid: int, gid: int) -> None:
    for relative in (
        "var/lib/robopark/accelerator",
        "var/lib/robopark/accelerator/models",
        "var/lib/robopark/accelerator/work",
    ):
        directory = root / relative
        directory.mkdir(parents=True, exist_ok=True, mode=0o750)
        directory.chmod(0o750)
        if root == Path("/"):
            os.chown(directory, uid, gid)


def prepare(
    *,
    root: Path = Path("/"),
    source: Path | None = None,
    apply: bool = False,
    start: bool = False,
    inspect_fn: Callable[[Path], dict] | None = None,
    storage_fn: Callable[..., dict] | None = None,
    dependency_fn: Callable[[], dict] | None = None,
    runner: Callable[[list[str]], None] | None = None,
) -> dict:
    root = Path(root)
    source = Path(source) if source is not None else Path(__file__).resolve().parent
    inspect_fn = inspect_fn or inspect_platform
    storage_fn = storage_fn or require_storage
    dependency_fn = dependency_fn or probe_dependencies
    runner = runner or _run
    if start and not apply:
        raise InstallError("start_requires_apply")
    platform = inspect_fn(root)
    if not platform.get("platform_ready") or not platform.get("identity", {}).get(
        "matches_recovery"
    ):
        blockers = platform.get("blockers") or ["recovery_identity_mismatch"]
        raise InstallError("platform_preflight_failed:" + ",".join(blockers))
    storage = storage_fn(root, require_layout=True)
    if storage.get("state") != "ready":
        raise InstallError("managed_storage_not_ready")
    payload = _payload(source)
    release_id, files = _manifest(payload)
    dependencies = dependency_fn()
    report = {
        "schema": 1,
        "state": "planned",
        "applied": False,
        "started": False,
        "start_requested": start,
        "release_id": release_id,
        "dependencies": dependencies,
        "models_included": False,
        "edge_llm_enabled": False,
    }
    if not apply:
        return report
    if os.geteuid() != 0:
        raise InstallError("root_required_for_apply")

    _guard_destinations(root)
    uid, gid = _account_ids(runner)
    release = _write_release(root, source, payload, release_id, files)
    _prepare_state(root, uid, gid)
    _install_units(root, source)
    current = root / "opt/robopark-accelerator/current"
    _atomic_symlink(current, f"releases/{release.name}")
    runner(["systemctl", "daemon-reload"])
    if start:
        runner(["systemctl", "enable", "--now", "robopark-accelerator.service"])
    report.update(state="installed", applied=True, started=start)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/"), help=argparse.SUPPRESS)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--start", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = prepare(
            root=args.root,
            source=args.source,
            apply=args.apply,
            start=args.start,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (InstallError, StorageError):
        print(json.dumps({"state": "blocked", "reason": "accelerator_install_blocked"}))
        return 2
    except (OSError, subprocess.SubprocessError):
        print(json.dumps({"state": "blocked", "reason": "system_operation_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
