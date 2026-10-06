#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OTA_SOURCE = ROOT / "deploy" / "ota"
if str(OTA_SOURCE) not in sys.path:
    sys.path.insert(0, str(OTA_SOURCE))

from robopark_ota import OtaError, verify_ota  # noqa: E402


class BuildError(ValueError):
    pass


_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,150}$")
_SHA = re.compile(r"^[a-f0-9]{40}$")
_SOURCE_PREFIXES = ("apps/", "deploy/", "scripts/")
_SOURCE_FILES = {"VERSION", "README.md"}
_EXCLUDED_EXACT: set[str] = set()
_EXCLUDED_PARTS = {
    ".git",
    ".pnpm-store",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "e2e",
    "e2e-production",
    "node_modules",
    "output",
}
_EXCLUDED_SUFFIXES = (".ota", ".pem", ".pyc", ".log")
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def _run(repository: Path, *command: str) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise BuildError("release_preflight_failed") from error
    return completed.stdout.strip()


def validate_output_directory(repository: Path, output: Path) -> Path:
    if not output.is_absolute():
        raise BuildError("output_directory_must_be_absolute")
    root = repository.resolve()
    resolved = output.resolve(strict=False)
    if resolved == root or root in resolved.parents:
        raise BuildError("output_directory_inside_repository")
    return resolved


def ensure_clean_tracked_tree(repository: Path) -> None:
    status = _run(repository, "git", "status", "--porcelain", "--untracked-files=no")
    if status:
        raise BuildError("tracked_tree_is_dirty")


def include_source_path(relative: Path) -> bool:
    name = relative.as_posix()
    return (
        (name in _SOURCE_FILES or name.startswith(_SOURCE_PREFIXES))
        and name not in _EXCLUDED_EXACT
        and not name.startswith(("apps/api/knowledge/", "deploy/knowledge/"))
        and relative.name != ".env"
        and not any(part in _EXCLUDED_PARTS for part in relative.parts)
        and not name.endswith(_EXCLUDED_SUFFIXES)
    )


def _tracked_files(repository: Path) -> list[Path]:
    output = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repository,
        check=True,
        capture_output=True,
    ).stdout
    paths = []
    for raw in output.split(b"\0"):
        if not raw:
            continue
        relative = Path(os.fsdecode(raw))
        if not include_source_path(relative):
            continue
        source = repository / relative
        if source.is_file() and not source.is_symlink():
            paths.append(relative)
    return sorted(paths, key=lambda item: item.as_posix())


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(document: object) -> bytes:
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, _ZIP_TIME)
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    # Installed clients read the bounded manifest directly before upload.
    # Keep it stored; release payloads remain compressed.
    info.compress_type = zipfile.ZIP_STORED if name == "manifest.json" else zipfile.ZIP_DEFLATED
    return info


def _metadata(repository: Path) -> dict[str, object]:
    try:
        value = json.loads((repository / "deploy/release-metadata.json").read_text())
        if not isinstance(value, dict):
            raise TypeError
        return value
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
        raise BuildError("invalid_release_metadata") from error


def _payload(repository: Path) -> dict[str, bytes]:
    payload: dict[str, bytes] = {
        "__main__.py": (repository / "deploy/ota/__main__.py").read_bytes(),
    }
    runtime = repository / "deploy/ota/robopark_ota"
    for source in sorted(runtime.glob("*.py")):
        payload[f"robopark_ota/{source.name}"] = source.read_bytes()
    # Shared bootstrap code must work before /opt/robopark exists. Keep it in
    # its own package so imports cannot shadow an installed robopark_host.
    payload["robopark_storage/__init__.py"] = b""
    for name in ("storage_layout.py", "storage_setup.py", "storage_watchdog.py"):
        payload[f"robopark_storage/{name}"] = (
            repository / "deploy/host/robopark_host" / name
        ).read_bytes()
    # The repository README pins the finished archive hash. The installed
    # guide is independent of that file and its presence in a workspace.
    readme = repository / "deploy/ota/README.installed.md"
    if readme.is_symlink() or not readme.is_file():
        raise BuildError("invalid_packaged_readme")
    payload["release/README.md"] = readme.read_bytes()
    for relative in _tracked_files(repository):
        if relative == Path("README.md"):
            continue
        payload[f"release/{relative.as_posix()}"] = (repository / relative).read_bytes()
    return payload


def build_ota(
    repository: Path,
    output: Path,
    *,
    git_sha: str | None = None,
    manifest_changes: list[str] | None = None,
) -> Path:
    repository = repository.resolve()
    output = output.resolve(strict=False)
    output.mkdir(parents=True, exist_ok=True)
    try:
        version = (repository / "VERSION").read_text().strip()
    except OSError as error:
        raise BuildError("invalid_release_version") from error
    if _VERSION.fullmatch(version) is None:
        raise BuildError("invalid_release_version")
    git_sha = git_sha or _run(repository, "git", "rev-parse", "HEAD")
    if _SHA.fullmatch(git_sha) is None:
        raise BuildError("invalid_git_sha")
    metadata = _metadata(repository)
    try:
        migration_head = metadata["migration_head"]
        compatible = metadata["compatible_from_versions"]
        requirements = metadata["requirements"]
        notes = metadata["update_notes"]
    except KeyError as error:
        raise BuildError("invalid_release_metadata") from error
    if not isinstance(notes, str) or not notes.strip():
        raise BuildError("invalid_release_metadata")
    changes = [notes.strip()] if manifest_changes is None else manifest_changes

    payload = _payload(repository)
    expanded = sum(len(data) for data in payload.values())
    manifest = {
        "format_version": 1,
        "app_version": version,
        "git_sha": git_sha,
        "migration_head": migration_head,
        "compatible_from": compatible,
        "required_free_bytes": max(2 * expanded, 512 * 1024 * 1024),
        "max_expanded_bytes": max(expanded, 1),
        "changes": changes,
        "requirements": requirements,
        "files": [
            {"path": name, "size": len(data), "sha256": _digest(data)}
            for name, data in sorted(payload.items())
        ],
    }
    members = {**payload, "manifest.json": _canonical(manifest)}
    target = output / f"robopark-{version}.ota"
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=output)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w") as archive:
            for name, data in sorted(members.items()):
                archive.writestr(_zip_info(name), data)
        verify_ota(temporary)
        os.replace(temporary, target)
    except (OSError, OtaError, zipfile.BadZipFile) as error:
        temporary.unlink(missing_ok=True)
        raise BuildError(str(error)) from error
    return target


def _run_gate(repository: Path) -> None:
    subprocess.run([str(repository / "scripts/verify.sh"), "fast"], cwd=repository, check=True)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": "deploy/ota"}
    subprocess.run(
        [
            "uv",
            "run",
            "--project",
            "apps/api",
            "--frozen",
            "--extra",
            "dev",
            "python",
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "tests/host/test_ota_verifier.py",
            "tests/host/test_ota_builder.py",
            "-q",
        ],
        cwd=repository,
        env=env,
        check=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build one deterministic Robopark OTA")
    parser.add_argument("output", type=Path)
    parser.add_argument("--skip-gate", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        output = validate_output_directory(ROOT, args.output)
        ensure_clean_tracked_tree(ROOT)
        if not args.skip_gate:
            _run_gate(ROOT)
        target = build_ota(ROOT, output)
        verified = verify_ota(target)
    except (BuildError, OtaError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "path": str(target),
                "version": verified.manifest.app_version,
                "size": verified.size,
                "sha256": verified.sha256,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
