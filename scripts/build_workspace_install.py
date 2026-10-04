"""Package the current worktree as a reviewable clean-install archive."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_ota import (
    BuildError,
    build_ota,
    include_source_path,
    validate_output_directory,
)
from scripts.robopark_version import ReleaseVersion

_RUNTIME_DATA = {"apps/api/data/emergency_sections.json"}
_RUNTIME_DATA_PREFIXES = ("apps/api/data/", "apps/bot/legacy/data/", "deploy/data/")
_UNTRACKED_PREFIXES = (
    "apps/api/src/", "apps/api/alembic/versions/", "apps/web/src/", "apps/web/terminal.html", "apps/web/public/", "apps/web/docker-entrypoint.d/", "apps/bot/",
    "deploy/host/robopark_host/", "deploy/ota/robopark_ota/",
    "deploy/installer/lib/", "deploy/systemd/", "deploy/host/robopark-bot-runtime",
    "scripts/audit-dependencies.sh",
    "apps/web/scripts/install-dependencies.sh",
)
_UNTRACKED_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".html", ".css", ".svg", ".png", ".webp",
    ".json", ".sh", ".service", ".timer",
}
_MAX_FILE_BYTES = 32 * 1024 * 1024
# New runtime files must be reviewed and staged in Git before packaging. This
# also covers files whose names were allowlisted during initial development.
_SECRET_FILE_NAMES = {
    "credentials.json", "secrets.json", "secret.json", "tokens.json",
    "token.json", "passwords.json", "private.json",
}
_INSTALL_FILES = (
    ("INSTALL.sh", Path("deploy/install-archive/INSTALL.sh")),
    ("README-RU.md", Path("docs/runbooks/usb-clean-install.md")),
)


def _git_paths(repository: Path, *command: str) -> set[Path]:
    try:
        output = subprocess.run(
            ["git", *(command or ("ls-files",)), "-z"], cwd=repository,
            check=True, capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise BuildError("workspace_git_files_unavailable") from error
    return {Path(os.fsdecode(raw)) for raw in output.split(b"\0") if raw}


def _allowed_source(relative: Path) -> bool:
    name = relative.as_posix()
    leaf = relative.name.lower()
    if not include_source_path(relative) or relative.is_absolute() or ".." in relative.parts:
        return False
    if leaf.startswith(".env") or leaf.endswith(".env") or leaf in _SECRET_FILE_NAMES:
        return False
    if relative.suffix.lower() in {".key", ".p12", ".pfx", ".jks", ".sqlite", ".db"}:
        return False
    if any(part in {"tests", "e2e", "e2e-production", "snapshots", "test-results", "playwright-report"} for part in relative.parts):
        return False
    if relative.name.endswith((".test.ts", ".test.tsx", ".test.mjs", ".spec.ts", ".spec.tsx", "_test.py")):
        return False
    if name.startswith(_RUNTIME_DATA_PREFIXES) and name not in _RUNTIME_DATA:
        return False
    return not name.startswith("deploy/install-archive/")


def workspace_source_files(repository: Path) -> list[Path]:
    """Select reviewed/indexed source and reject unstaged new runtime files."""
    tracked = {path for path in _git_paths(repository) if _allowed_source(path)}
    untracked_paths = _git_paths(repository, "ls-files", "--others", "--exclude-standard")
    # git add -N lists a path in ls-files without actually staging its content.
    # Do not confuse that marker with a legitimately staged empty module.
    intent_paths = _git_paths(
        repository, "diff", "--name-only", "--diff-filter=A", "--ita-invisible-in-index"
    )
    tracked -= intent_paths
    unexpected = {
        path for path in untracked_paths
        if _allowed_source(path)
        and path.as_posix().startswith(_UNTRACKED_PREFIXES)
        and (path.suffix in _UNTRACKED_EXTENSIONS or path.as_posix().startswith("apps/bot/") or path.suffix == ".path" or path.name == "robopark-bot-runtime")
    }
    unexpected.update(path for path in intent_paths if _allowed_source(path))
    if unexpected:
        raise BuildError("unreviewed_untracked_source")
    selected: list[Path] = []
    for relative in sorted(tracked, key=lambda path: path.as_posix()):
        source = repository / relative
        if source.is_symlink() or not source.is_file():
            continue
        if source.stat().st_size > _MAX_FILE_BYTES:
            raise BuildError(f"source_file_too_large:{relative.as_posix()}")
        selected.append(relative)
    return selected


def _source_digest(repository: Path, files: list[Path]) -> str:
    digest = hashlib.sha256()
    for relative in files:
        data = (repository / relative).read_bytes()
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _install_files(repository: Path) -> dict[str, bytes]:
    return {
        archive_name: (repository / relative).read_bytes()
        for archive_name, relative in _INSTALL_FILES
    }


def _install_file_hashes(files: dict[str, bytes]) -> dict[str, str]:
    return {
        archive_name: hashlib.sha256(files[archive_name]).hexdigest()
        for archive_name, _relative in _INSTALL_FILES
    }


def _archive_identity(source_digest: str, install_hashes: dict[str, str], base_sha: str) -> str:
    digest = hashlib.sha256(b"robopark-install-archive-v1\0")
    digest.update(bytes.fromhex(source_digest))
    digest.update(bytes.fromhex(base_sha))
    for archive_name, _relative in _INSTALL_FILES:
        digest.update(bytes.fromhex(install_hashes[archive_name]))
    return digest.hexdigest()


def _base_sha(repository: Path) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repository,
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise BuildError("base_git_sha_unavailable") from error


def snapshot_version(base_version: str, digest: str) -> str:
    """Give the source snapshot a unique forward-moving prerelease version."""
    match = re.fullmatch(
        r"(?P<major>[0-9]+)\.(?P<minor>[0-9]+)\.(?P<patch>[0-9]+)"
        r"(?:[-.]rc[.]?(?P<rc>[0-9]+)(?:\.dev[0-9]+)?)?(?:\+[0-9A-Za-z.-]+)?",
        base_version,
        re.IGNORECASE,
    )
    if match is None or re.fullmatch(r"[a-f0-9]{64}", digest) is None:
        raise BuildError("invalid_base_version")
    if match.group("rc") is None:
        core = f"{match.group('major')}.{match.group('minor')}.{int(match.group('patch')) + 1}"
        next_rc = 1
    else:
        core = f"{match.group('major')}.{match.group('minor')}.{match.group('patch')}"
        next_rc = int(match.group("rc")) + 1
    return f"{core}-rc.{next_rc}.dev{int(digest[:16], 16)}"


def _replace_once(path: Path, old: str, new: str) -> None:
    source = path.read_text()
    if source.count(old) != 1:
        raise BuildError(f"version_rewrite_failed:{path.name}")
    path.write_text(source.replace(old, new, 1))


def _stage_snapshot(
    repository: Path,
    stage: Path,
    files: list[Path],
    source_digest: str,
    archive_identity: str,
    *,
    target_version: str | None = None,
) -> str:
    for relative in files:
        destination = stage / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repository / relative, destination)
    if _source_digest(stage, files) != source_digest:
        raise BuildError("workspace_changed_during_build")
    base_version = (stage / "VERSION").read_text().strip()
    version = target_version or snapshot_version(base_version, archive_identity)
    (stage / "VERSION").write_text(version + "\n")
    _replace_once(
        stage / "apps/api/pyproject.toml",
        f'version = "{base_version}"', f'version = "{version}"',
    )
    lock_path = stage / "apps/api/uv.lock"
    locked_versions = [
        package.get("version")
        for package in tomllib.loads(lock_path.read_text()).get("package", [])
        if package.get("name") == "robopark-api" and package.get("source") == {"editable": "."}
    ]
    if len(locked_versions) != 1 or locked_versions[0] not in {
        base_version, ReleaseVersion.parse(base_version).python_version,
    }:
        raise BuildError("version_rewrite_failed:uv.lock")
    _replace_once(
        lock_path,
        f'name = "robopark-api"\nversion = "{locked_versions[0]}"',
        f'name = "robopark-api"\nversion = "{version}"',
    )
    _replace_once(
        stage / "apps/api/src/robopark_api/services/ops/context.py",
        f'APP_VERSION = "{base_version}"', f'APP_VERSION = "{version}"',
    )
    web_package_path = stage / "apps/web/package.json"
    web_package = json.loads(web_package_path.read_text())
    if web_package.get("version") != base_version:
        raise BuildError("version_rewrite_failed:package.json")
    web_package["version"] = version
    web_package_path.write_text(json.dumps(web_package, ensure_ascii=False, indent=2) + "\n")
    web_lock_path = stage / "apps/web/package-lock.json"
    web_lock = json.loads(web_lock_path.read_text())
    if (
        web_lock.get("version") != base_version
        or not isinstance(web_lock.get("packages"), dict)
        or web_lock["packages"].get("", {}).get("version") != base_version
    ):
        raise BuildError("version_rewrite_failed:package-lock.json")
    web_lock["version"] = version
    web_lock["packages"][""]["version"] = version
    web_lock_path.write_text(json.dumps(web_lock, ensure_ascii=False, indent=2) + "\n")
    metadata_path = stage / "deploy/release-metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["update_notes"] = (
        f"{metadata['update_notes'].strip().replace(base_version, version, 1)} "
        f"Workspace source SHA-256: {source_digest}. "
        "Uncommitted clean-install snapshot; inspect SOURCE-SNAPSHOT.json."
    )
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    subprocess.run(["git", "init", "-q", str(stage)], check=True)
    subprocess.run(["git", "add", "--all"], cwd=stage, check=True)
    return version


def _tar_member(name: str, data: bytes, mode: int = 0o644) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mtime = 0
    return info, data


def build_workspace_install_archive(repository: Path, output: Path) -> Path:
    repository = repository.resolve()
    output = validate_output_directory(repository, output.resolve(strict=False))
    output.mkdir(parents=True, exist_ok=True)
    files = workspace_source_files(repository)
    source_digest = _source_digest(repository, files)
    install_files = _install_files(repository)
    install_hashes = _install_file_hashes(install_files)
    base_sha = _base_sha(repository)
    archive_identity = _archive_identity(source_digest, install_hashes, base_sha)
    with tempfile.TemporaryDirectory(prefix="robopark-install-build-") as scratch_name:
        scratch = Path(scratch_name)
        stage = scratch / "source"
        stage.mkdir()
        version = _stage_snapshot(
            repository, stage, files, source_digest, archive_identity,
        )
        ota = build_ota(stage, scratch / "ota", git_sha=base_sha)
        ota_data = ota.read_bytes()
        snapshot = {
            "kind": "uncommitted-worktree", "base_git_sha": base_sha,
            "source_sha256": source_digest,
            "install_file_sha256": install_hashes,
            "archive_identity_sha256": archive_identity,
            "version": version,
            "source_file_count": len(files),
            "version_rewrites": [
                "VERSION", "apps/api/pyproject.toml", "apps/api/uv.lock",
                "apps/api/src/robopark_api/services/ops/context.py",
                "apps/web/package.json", "apps/web/package-lock.json",
                "deploy/release-metadata.json",
            ],
        }
        snapshot_data = (json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        checked = {
            **install_files,
            "SOURCE-SNAPSHOT.json": snapshot_data,
            ota.name: ota_data,
        }
        checksum = "".join(
            f"{hashlib.sha256(data).hexdigest()}  {name}\n"
            for name, data in sorted(checked.items())
        ).encode()
        members = [
            _tar_member("INSTALL.sh", install_files["INSTALL.sh"], 0o755),
            _tar_member("README-RU.md", install_files["README-RU.md"]),
            _tar_member("SOURCE-SNAPSHOT.json", snapshot_data),
            _tar_member("SHA256SUMS", checksum),
            _tar_member(ota.name, ota_data),
        ]
        target = output / f"robopark-{version}-install.tar.gz"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=output
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with (
                temporary.open("wb") as raw,
                gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed,
                tarfile.open(fileobj=compressed, mode="w") as package,
            ):
                for info, data in members:
                    package.addfile(info, io.BytesIO(data))
            if (
                workspace_source_files(repository) != files
                or _source_digest(repository, files) != source_digest
                or _install_files(repository) != install_files
                or _base_sha(repository) != base_sha
            ):
                raise BuildError("workspace_changed_during_build")
            try:
                os.link(temporary, target)
            except FileExistsError:
                if not target.is_file() or target.is_symlink() or target.read_bytes() != temporary.read_bytes():
                    raise BuildError("archive_identity_collision") from None
        finally:
            temporary.unlink(missing_ok=True)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Robopark clean-install archive from this worktree")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        target = build_workspace_install_archive(ROOT, args.output)
    except (BuildError, OSError, KeyError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps({"path": str(target), "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
