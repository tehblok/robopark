#!/usr/bin/env python3
"""Build public stable AGX OTA and clean-install archives from one commit."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_ota import (
    BuildError,
    build_ota,
    ensure_clean_tracked_tree,
    validate_output_directory,
)
from scripts.build_workspace_install import (
    _INSTALL_FILES,
    _install_file_hashes,
    _install_files,
    _source_digest,
    _tar_member,
    workspace_source_files,
)
from scripts.robopark_version import ReleaseVersion


@dataclass(frozen=True, slots=True)
class AgxReleaseArtifacts:
    ota: Path
    install_archive: Path
    version: str
    git_sha: str
    source_sha256: str


def _git(repository: Path, *arguments: str, input_data: bytes | None = None) -> bytes:
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=repository,
            check=True,
            capture_output=True,
            input=input_data,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise BuildError("release_git_state_unavailable") from error


def _head(repository: Path) -> str:
    value = _git(repository, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise BuildError("invalid_release_head")
    return value


def _stable_version(repository: Path) -> str:
    try:
        value = (repository / "VERSION").read_text(encoding="utf-8").strip()
        version = ReleaseVersion.parse(value)
    except (OSError, UnicodeError, ValueError) as error:
        raise BuildError("invalid_release_version") from error
    if version.stage != "stable":
        raise BuildError("stable_release_required")
    return version.raw


def _require_committed_install_files(repository: Path) -> None:
    tracked = {
        Path(os.fsdecode(item))
        for item in _git(repository, "ls-files", "-z").split(b"\0")
        if item
    }
    if any(relative not in tracked for _name, relative in _INSTALL_FILES):
        raise BuildError("install_file_not_committed")


def _stage_sources(repository: Path, stage: Path, files: list[Path]) -> None:
    for relative in files:
        destination = stage / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repository / relative, destination)
    subprocess.run(["git", "init", "-q", str(stage)], check=True)
    for offset in range(0, len(files), 200):
        subprocess.run(
            ["git", "add", "--", *(str(path) for path in files[offset : offset + 200])],
            cwd=stage,
            check=True,
        )


def _source_document(
    *,
    git_sha: str,
    version: str,
    source_sha256: str,
    install_hashes: dict[str, str],
) -> bytes:
    document = {
        "schema": 1,
        "git_sha": git_sha,
        "version": version,
        "source_sha256": source_sha256,
        "install_file_sha256": install_hashes,
    }
    return (
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _install_archive(
    *,
    install_files: dict[str, bytes],
    source_data: bytes,
    ota_name: str,
    ota_data: bytes,
) -> bytes:
    checked = {**install_files, "SOURCE.json": source_data, ota_name: ota_data}
    checksums = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n"
        for name, data in sorted(checked.items())
    ).encode("utf-8")
    executable = {"INSTALL.sh", "INSTALL-AGX.sh", "robopark-agx.py"}
    members = [
        *(
            _tar_member(name, install_files[name], 0o755 if name in executable else 0o644)
            for name, _relative in _INSTALL_FILES
        ),
        _tar_member("SOURCE.json", source_data),
        _tar_member("SHA256SUMS", checksums),
        _tar_member(ota_name, ota_data),
    ]
    buffer = io.BytesIO()
    with (
        gzip.GzipFile(filename="", fileobj=buffer, mode="wb", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as package,
    ):
        for info, data in members:
            package.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _publish(target: Path, data: bytes) -> None:
    if target.exists():
        if target.is_symlink() or not target.is_file() or target.read_bytes() != data:
            raise BuildError("public_asset_collision")
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_bytes(data)
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.is_symlink() or not target.is_file() or target.read_bytes() != data:
                raise BuildError("public_asset_collision") from None
    finally:
        temporary.unlink(missing_ok=True)


def build_agx_release(repository: Path, output: Path) -> AgxReleaseArtifacts:
    repository = repository.resolve()
    output = validate_output_directory(repository, output.resolve(strict=False))
    ensure_clean_tracked_tree(repository)
    version = _stable_version(repository)
    git_sha = _head(repository)
    _require_committed_install_files(repository)
    files = workspace_source_files(repository)
    source_sha256 = _source_digest(repository, files)
    install_files = _install_files(repository)
    install_hashes = _install_file_hashes(install_files)

    with tempfile.TemporaryDirectory(prefix="robopark-agx-release-") as scratch_name:
        scratch = Path(scratch_name)
        stage = scratch / "source"
        stage.mkdir()
        _stage_sources(repository, stage, files)
        if _source_digest(stage, files) != source_sha256:
            raise BuildError("source_changed_during_build")
        ota = build_ota(stage, scratch / "ota", git_sha=git_sha)
        ota_data = ota.read_bytes()
        source_data = _source_document(
            git_sha=git_sha,
            version=version,
            source_sha256=source_sha256,
            install_hashes=install_hashes,
        )
        archive_data = _install_archive(
            install_files=install_files,
            source_data=source_data,
            ota_name=ota.name,
            ota_data=ota_data,
        )

        try:
            ensure_clean_tracked_tree(repository)
            unchanged = (
                _stable_version(repository) == version
                and _head(repository) == git_sha
                and workspace_source_files(repository) == files
                and _source_digest(repository, files) == source_sha256
                and _install_files(repository) == install_files
            )
        except (BuildError, OSError):
            unchanged = False
        if not unchanged:
            raise BuildError("source_changed_during_build")

    output.mkdir(parents=True, exist_ok=True)
    ota_target = output / ota.name
    archive_target = output / f"robopark-{version}-install.tar.gz"
    for target, data in ((ota_target, ota_data), (archive_target, archive_data)):
        if target.exists() and (
            target.is_symlink() or not target.is_file() or target.read_bytes() != data
        ):
            raise BuildError("public_asset_collision")
    _publish(ota_target, ota_data)
    _publish(archive_target, archive_data)
    return AgxReleaseArtifacts(
        ota=ota_target,
        install_archive=archive_target,
        version=version,
        git_sha=git_sha,
        source_sha256=source_sha256,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = build_agx_release(ROOT, args.output)
    except (BuildError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"{error}\n")
    print(
        json.dumps(
            {
                "git_sha": result.git_sha,
                "install_archive": str(result.install_archive),
                "install_sha256": hashlib.sha256(
                    result.install_archive.read_bytes()
                ).hexdigest(),
                "ota": str(result.ota),
                "ota_sha256": hashlib.sha256(result.ota.read_bytes()).hexdigest(),
                "source_sha256": result.source_sha256,
                "version": result.version,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
