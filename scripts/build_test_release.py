#!/usr/bin/env python3
"""Build a reviewed test delivery without staging or publishing anything."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_macos_command import build as build_macos_command
from scripts.build_ota import (
    BuildError,
    build_ota,
    include_source_path,
    validate_output_directory,
)


class ReleaseBuildError(ValueError):
    pass


def _git(repository: Path, *arguments: str) -> bytes:
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=repository,
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise ReleaseBuildError("workspace_check_failed") from error


def require_reviewed_workspace(repository: Path) -> None:
    """Require every workspace payload change to be visible in the Git index."""

    if _git(repository, "diff", "--name-only", "--"):
        raise ReleaseBuildError("unstaged_changes")
    untracked = _git(
        repository, "ls-files", "--others", "--exclude-standard", "-z"
    ).split(b"\0")
    if any(
        raw and include_source_path(Path(raw.decode("utf-8", "surrogateescape")))
        for raw in untracked
    ):
        raise ReleaseBuildError("untracked_files")
    indexed = _git(repository, "ls-files", "-z").split(b"\0")
    if any(
        raw and ".release-secrets" in Path(raw.decode("utf-8", "surrogateescape")).parts
        for raw in indexed
    ):
        raise ReleaseBuildError("release_secrets_in_index")


def require_committed_source(repository: Path) -> None:
    require_reviewed_workspace(repository)
    if _git(repository, "diff", "--cached", "--name-only", "HEAD", "--"):
        raise ReleaseBuildError("commit_reviewed_release_before_build")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact(path: Path) -> dict[str, object]:
    return {"path": path.name, "size": path.stat().st_size, "sha256": _sha256(path)}


def _macos_update_root_bytes(path: Path | None) -> bytes:
    if path is None:
        raise ReleaseBuildError("macos_update_root_required")
    try:
        path = Path(path)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 16 * 1024:
                raise ValueError
            raw_document = source.read(16 * 1024 + 1)
        if len(raw_document) != info.st_size:
            raise ValueError
        document = json.loads(raw_document)
        if not isinstance(document, dict) or set(document) != {"ed25519"}:
            raise ValueError
        raw = base64.b64decode(document["ed25519"], validate=True)
        if len(raw) != 32:
            raise ValueError
        return raw
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        raise ReleaseBuildError("invalid_macos_update_root") from error


def _embedded_macos_update_root(package: Path) -> bytes:
    try:
        with tempfile.TemporaryDirectory(prefix="robopark-pkg-inspect-") as temporary:
            expanded = Path(temporary) / "expanded"
            subprocess.run(
                ["pkgutil", "--expand-full", os.fspath(package), os.fspath(expanded)],
                check=True,
                capture_output=True,
                timeout=120,
            )
            root = (
                expanded
                / "Payload/Applications/Robopark.app/Contents/Resources/update-root.json"
            )
            return _macos_update_root_bytes(root)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise ReleaseBuildError("macos_package_inspection_failed") from error


def validate_macos_source(package: Path, expected_commit: str) -> None:
    """Bind the separately compiled Mac app to this committed server release."""
    try:
        with tempfile.TemporaryDirectory(prefix="robopark-pkg-source-") as temporary:
            expanded = Path(temporary) / "expanded"
            subprocess.run(
                ["pkgutil", "--expand-full", os.fspath(package), os.fspath(expanded)],
                check=True,
                capture_output=True,
                timeout=120,
            )
            info = expanded / "Payload/Applications/Robopark.app/Contents/Info.plist"
            if info.is_symlink() or not info.is_file() or info.stat().st_size > 16384:
                raise ValueError
            document = plistlib.loads(info.read_bytes())
            if (
                not isinstance(document, dict)
                or document.get("RoboparkSourceCommit") != expected_commit
                or document.get("RoboparkSourceClean") is not True
            ):
                raise ValueError
    except (
        OSError,
        ValueError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as error:
        raise ReleaseBuildError(
            "macos_source_commit_mismatch_rebuild_package"
        ) from error


def _validate_macos_assets(
    package: Path,
    catalog: Path,
    *,
    version: str,
    macos_update_root: Path | None,
) -> None:
    expected_name = f"Robopark-{version}-arm64.pkg"
    try:
        package_info = package.lstat()
    except OSError as error:
        raise ReleaseBuildError("invalid_macos_package") from error
    if (
        package.name != expected_name
        or package.suffix != ".pkg"
        or not stat.S_ISREG(package_info.st_mode)
        or not 0 < package_info.st_size <= 512 * 1024 * 1024
    ):
        raise ReleaseBuildError("invalid_macos_package")
    update_root_bytes = _macos_update_root_bytes(macos_update_root)
    public_key = Ed25519PublicKey.from_public_bytes(update_root_bytes)
    try:
        raw = catalog.read_bytes()
        if not 0 < len(raw) <= 256 * 1024:
            raise ValueError
        envelope = json.loads(raw)
        if not isinstance(envelope, dict) or set(envelope) != {"payload", "signature"}:
            raise ValueError
        payload = base64.b64decode(envelope["payload"], validate=True)
        signature = base64.b64decode(envelope["signature"], validate=True)
        public_key.verify(signature, payload)
        document = json.loads(payload)
        if (
            not isinstance(document, dict)
            or set(document) != {"schema", "generation", "expires", "channels"}
            or type(document["schema"]) is not int
            or document["schema"] != 1
            or type(document["generation"]) is not int
            or document["generation"] <= 0
            or type(document["expires"]) is not int
            or document["expires"] <= int(time.time())
        ):
            raise ValueError
        channels = document["channels"]
        if not isinstance(channels, dict) or "rc" not in channels:
            raise ValueError
        target = channels["rc"]
        if (
            not isinstance(target, dict)
            or set(target)
            != {
                "version",
                "url",
                "sha256",
                "length",
                "architecture",
                "protocol",
                "notes",
            }
            or not all(
                isinstance(target[field], str)
                for field in ("version", "url", "sha256", "architecture", "notes")
            )
            or type(target["length"]) is not int
            or type(target["protocol"]) is not int
        ):
            raise ValueError
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        InvalidSignature,
        json.JSONDecodeError,
    ) as error:
        raise ReleaseBuildError("invalid_macos_catalog") from error
    digest = _sha256(package)
    expected_url = f"/api/distribution/v1/macos/{expected_name}"
    if not (
        target["version"] == version
        and target["url"] == expected_url
        and target["sha256"] == digest
        and target["length"] == package_info.st_size
        and target["architecture"] == "arm64"
        and target["protocol"] == 1
    ):
        raise ReleaseBuildError("macos_catalog_package_mismatch")
    if _embedded_macos_update_root(package) != update_root_bytes:
        raise ReleaseBuildError("macos_package_update_root_mismatch")


def stage_release_source(
    repository: Path,
    destination: Path,
    *,
    macos_pkg: Path | None,
    macos_update_root: Path | None = None,
    version: str,
    include_macos_command: bool = False,
) -> Path:
    """Export the reviewed index and add only the two generated Mac assets."""

    repository = Path(repository).resolve(strict=True)
    destination = Path(destination).resolve(strict=False)
    if destination.exists():
        raise ReleaseBuildError("snapshot_destination_exists")
    destination.mkdir(parents=True)
    prefix = os.fspath(destination) + os.sep
    try:
        indexed_paths = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=repository,
            check=True,
            capture_output=True,
        ).stdout
        subprocess.run(
            ["git", "checkout-index", "--all", f"--prefix={prefix}"],
            cwd=repository,
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "init", "-q"], cwd=destination, check=True)
        subprocess.run(
            [
                "git",
                "add",
                "-f",
                "--pathspec-from-file=-",
                "--pathspec-file-nul",
            ],
            cwd=destination,
            check=True,
            capture_output=True,
            input=indexed_paths,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ReleaseBuildError("snapshot_failed") from error
    if macos_pkg is None:
        return destination

    package = Path(macos_pkg).resolve(strict=True)
    catalog = package.parent / "catalog.json"
    _validate_macos_assets(
        package, catalog, version=version, macos_update_root=macos_update_root
    )
    assets = destination / "apps/api/src/robopark_api/agent_distribution_assets"
    assets.mkdir(parents=True, exist_ok=True)
    staged_package = assets / package.name
    staged_catalog = assets / "catalog.json"
    shutil.copyfile(package, staged_package)
    shutil.copyfile(catalog, staged_catalog)
    extra_assets = []
    if include_macos_command:
        command = assets / f"Robopark-{version}-arm64.command"
        build_macos_command(package, command, version=version)
        extra_assets.append(os.fspath(command.relative_to(destination)))
    relative_package = staged_package.relative_to(destination)
    relative_catalog = staged_catalog.relative_to(destination)
    try:
        subprocess.run(
            [
                "git",
                "add",
                "--",
                os.fspath(relative_package),
                os.fspath(relative_catalog),
                *extra_assets,
            ],
            cwd=destination,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ReleaseBuildError("snapshot_failed") from error
    return destination


def write_delivery_metadata(
    output: Path,
    *,
    version: str,
    server_ota: Path,
    macos_pkg: Path | None = None,
    macos_update_root: Path | None = None,
    source: dict[str, str] | None = None,
    include_macos_command: bool = False,
) -> Path:
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    server_ota = Path(server_ota).resolve(strict=True)
    if server_ota.parent != output.resolve():
        target = output / server_ota.name
        shutil.copyfile(server_ota, target)
        server_ota = target

    document: dict[str, object] = {
        "format_version": 1,
        "version": version,
        "channel": "test",
        "source": source,
        "server": {
            **_artifact(server_ota),
            "platforms": ["khadas-vim4", "nvidia-agx-orin"],
            "architectures": ["aarch64"],
        },
        "macos": None,
    }
    artifacts = [server_ota]
    if macos_pkg is not None:
        source = Path(macos_pkg).resolve(strict=True)
        catalog = source.parent / "catalog.json"
        _validate_macos_assets(
            source, catalog, version=version, macos_update_root=macos_update_root
        )
        update_root = Path(macos_update_root).resolve(strict=True)
        target = output / source.name
        if source != target.resolve(strict=False):
            shutil.copyfile(source, target)
        target_catalog = output / "catalog.json"
        if catalog.resolve() != target_catalog.resolve(strict=False):
            shutil.copyfile(catalog, target_catalog)
        target_update_root = output / "update-root.json"
        if update_root != target_update_root.resolve(strict=False):
            shutil.copyfile(update_root, target_update_root)
        document["macos"] = {
            **_artifact(target),
            "platforms": ["macos-arm64"],
            "catalog": _artifact(target_catalog),
            "update_root": _artifact(target_update_root),
        }
        artifacts.extend((target, target_catalog, target_update_root))
        if include_macos_command:
            command = output / f"Robopark-{version}-arm64.command"
            build_macos_command(target, command, version=version)
            document["macos"]["installer"] = _artifact(command)
            artifacts.append(command)

    manifest = output / "release-manifest.json"
    manifest.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output / "SHA256SUMS").write_text(
        "".join(
            f"{_sha256(path)}  {path.name}\n"
            for path in sorted(artifacts, key=lambda item: item.name)
        ),
        encoding="utf-8",
    )
    (output / "README-RU.txt").write_text(
        (
            f"Robopark test release {version}\n\n"
            "Сервер: один OTA подходит для Khadas VIM4 и NVIDIA AGX Orin.\n"
            "Для нового тестового сервера: sudo python3 <файл.ota> install --environment test\n"
            "Это тестовый prerelease: не устанавливайте его на действующий production.\n"
            "Бывший production Khadas RC43 переводится отдельным Robopark-Khadas-Test-<версия>.pyz; см. START-HERE.md.\n"
            "Каждому одновременно работающему серверу нужен отдельный HTTPS-адрес.\n"
            "Перед установкой проверьте SHA256SUMS. Этот пакет не выполняет SSH-развёртывание.\n"
            + (
                f"Mac: один файл Robopark-{version}-arm64.command; установка или локальное обновление сохраняет данные.\n"
                "Онлайн-обновление автоматически проверяет подписанный каталог тестового сервера.\n"
                "Удаление: меню Robopark → Удалить Robopark… или скрипт с --uninstall.\n"
                "--uninstall-remove-data также удаляет локальные данные текущего пользователя.\n"
                "Сборка без Apple notarization; скрипт не отключает защиту macOS.\n"
                if include_macos_command and macos_pkg is not None
                else ""
            )
        ),
        encoding="utf-8",
    )
    return manifest


def _run_gate(repository: Path) -> None:
    subprocess.run(
        [str(repository / "scripts/verify.sh"), "fast"], cwd=repository, check=True
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a reviewed Khadas/AGX test delivery"
    )
    parser.add_argument("output", type=Path)
    parser.add_argument("--macos-pkg", type=Path)
    parser.add_argument("--macos-update-root", type=Path)
    parser.add_argument("--skip-gate", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.macos_pkg is not None and args.macos_update_root is None:
            raise ReleaseBuildError("macos_update_root_required")
        output = validate_output_directory(ROOT, args.output)
        require_committed_source(ROOT)
        if not args.skip_gate:
            _run_gate(ROOT)
        git_sha = _git(ROOT, "rev-parse", "HEAD").decode("ascii").strip()
        reviewed_tree = _git(ROOT, "write-tree").decode("ascii").strip()
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        with tempfile.TemporaryDirectory(prefix="robopark-test-release-") as temporary:
            staged = stage_release_source(
                ROOT,
                Path(temporary) / "source",
                macos_pkg=args.macos_pkg,
                macos_update_root=args.macos_update_root,
                version=version,
                include_macos_command=True,
            )
            if args.macos_pkg is not None:
                validate_macos_source(args.macos_pkg, git_sha)
            server = build_ota(staged, output, git_sha=git_sha)
            manifest = write_delivery_metadata(
                output,
                version=version,
                server_ota=server,
                macos_pkg=args.macos_pkg,
                macos_update_root=args.macos_update_root,
                source={
                    "base_commit": git_sha,
                    "reviewed_tree": reviewed_tree,
                    "kind": "committed-release",
                },
                include_macos_command=True,
            )
    except (
        BuildError,
        ReleaseBuildError,
        OSError,
        subprocess.CalledProcessError,
    ) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps({"manifest": str(manifest), "version": version}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
