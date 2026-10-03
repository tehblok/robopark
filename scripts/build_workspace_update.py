"""Build OTA against verified archives, preserving every released migration."""

from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import io
import json
import os
import re
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "deploy/ota"))

from robopark_ota import verify_ota

from scripts.build_ota import BuildError, build_ota, validate_output_directory
from scripts.build_workspace_install import (
    _base_sha,
    _source_digest,
    _stage_snapshot,
    _tar_member,
    snapshot_version,
    workspace_source_files,
)
from scripts.robopark_version import ReleaseVersion

_MIGRATION_METADATA = {"revision", "down_revision", "branch_labels", "depends_on"}


def _declarative_migration_module(tree: ast.Module) -> bool:
    """New revisions may execute schema operations only inside migration functions."""
    functions: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, ast.Import) and all(alias.name == "sqlalchemy" for alias in node.names):
            continue
        if isinstance(node, ast.ImportFrom) and node.level == 0 and (
            (node.module == "alembic" and all(alias.name == "op" for alias in node.names))
            or (node.module == "__future__" and all(alias.name == "annotations" for alias in node.names))
        ):
            continue
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in _MIGRATION_METADATA:
                continue
        if (
            isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
            and node.target.id in _MIGRATION_METADATA
            and isinstance(node.annotation, ast.Name) and node.annotation.id == "str"
        ):
            continue
        if isinstance(node, ast.FunctionDef) and node.name in {"upgrade", "downgrade"}:
            args = node.args
            if (
                node.name not in functions
                and not node.decorator_list and not getattr(node, "type_params", [])
                and not (args.posonlyargs or args.args or args.kwonlyargs or args.vararg or args.kwarg or args.defaults or args.kw_defaults)
                and (node.returns is None or (isinstance(node.returns, ast.Constant) and node.returns.value is None))
            ):
                functions.add(node.name)
                continue
        return False
    return functions == {"upgrade", "downgrade"}


def _literal_migration_metadata(source: str | bytes, *, declarative: bool = False) -> dict[str, object] | None:
    """Read Alembic metadata without importing candidate migration code."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, TypeError):
        return None
    if declarative and not _declarative_migration_module(tree):
        return None
    values: dict[str, object] = {}
    permitted_targets: set[int] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue
        if not isinstance(target, ast.Name) or target.id not in _MIGRATION_METADATA:
            continue
        if target.id in values:
            return None
        try:
            values[target.id] = ast.literal_eval(value)
        except (ValueError, TypeError):
            return None
        permitted_targets.add(id(target))

    if set(values) != _MIGRATION_METADATA:
        return None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and node.id in _MIGRATION_METADATA
            and isinstance(node.ctx, (ast.Store, ast.Del))
            and id(node) not in permitted_targets
        ):
            return None
        if isinstance(node, ast.alias):
            bound = node.asname or node.name.split(".", 1)[0]
            if bound in _MIGRATION_METADATA:
                return None
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name in _MIGRATION_METADATA
        ):
            return None
        if isinstance(node, ast.arg) and node.arg in _MIGRATION_METADATA:
            return None
        if isinstance(node, ast.ExceptHandler) and node.name in _MIGRATION_METADATA:
            return None
        if isinstance(node, (ast.Global, ast.Nonlocal)) and (
            set(node.names) & _MIGRATION_METADATA
        ):
            return None
    return values


def _append_only_migrations(
    directory: Path,
    added: set[str],
    previous_head: str,
    target_head: str,
    *,
    previous_revisions: set[str],
) -> bool:
    """Allow one literal Alembic chain without importing candidate Python."""
    parents: dict[str, str] = {}
    try:
        for name in added:
            values = _literal_migration_metadata((directory / name).read_bytes(), declarative=True)
            if values is None:
                return False
            revision, parent = values.get("revision"), values.get("down_revision")
            if (
                not isinstance(revision, str) or not revision
                or not isinstance(parent, str) or not parent
                or revision in parents or revision in previous_revisions
                or values.get("branch_labels") is not None
                or values.get("depends_on") is not None
            ):
                return False
            parents[revision] = parent
    except (OSError, SyntaxError, ValueError, TypeError):
        return False
    visited: set[str] = set()
    current = target_head
    while current != previous_head:
        if current not in parents or current in visited:
            return False
        visited.add(current)
        current = parents[current]
    return bool(visited) and visited == set(parents)


def _compatibility(repository: Path, archives: list[Path]) -> tuple[list[str], bool]:
    if not archives:
        raise BuildError("update_previous_archive_required")
    directory = repository / "apps/api/alembic/versions"
    current = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in directory.glob("*.py")
    }
    metadata = json.loads((repository / "deploy/release-metadata.json").read_text())
    head = metadata["migration_head"]
    declared = metadata.get("migration_compatibility", {}).get("from_heads", [])
    versions = set()
    migration_changes = False
    with tempfile.TemporaryDirectory(prefix="robopark-update-compat-") as scratch:
        for archive in archives:
            with tarfile.open(archive, "r:gz") as package:
                members = [m for m in package.getmembers() if m.name.endswith(".ota")]
                if (
                    len(members) != 1
                    or not members[0].isfile()
                    or members[0].size > 2 * 1024**3
                ):
                    raise BuildError("invalid_previous_archive")
                previous = Path(scratch) / "previous.ota"
                previous.write_bytes(package.extractfile(members[0]).read())
            verified = verify_ota(previous)
            with zipfile.ZipFile(previous) as ota:
                migration_names = [
                    n for n in ota.namelist()
                    if n.startswith("release/apps/api/alembic/versions/")
                    and n.endswith(".py")
                ]
                migrations: dict[str, str] = {}
                previous_revisions: set[str] = set()
                for name in migration_names:
                    basename = Path(name).name
                    payload = ota.read(name)
                    values = _literal_migration_metadata(payload)
                    revision = None if values is None else values.get("revision")
                    if (
                        basename in migrations
                        or not isinstance(revision, str)
                        or not revision
                        or revision in previous_revisions
                    ):
                        raise BuildError("update_migrations_differ")
                    migrations[basename] = hashlib.sha256(payload).hexdigest()
                    previous_revisions.add(revision)
            previous_head = verified.manifest.migration_head
            if previous_head not in previous_revisions:
                raise BuildError("update_migrations_differ")
            if previous_head != head or migrations != current:
                if (
                    previous_head not in declared
                    or any(current.get(name) != digest for name, digest in migrations.items())
                    or not _append_only_migrations(
                        directory, set(current) - set(migrations), previous_head, head,
                        previous_revisions=previous_revisions,
                    )
                ):
                    raise BuildError("update_migrations_differ")
                migration_changes = True
            try:
                ReleaseVersion.parse(verified.manifest.app_version)
            except ValueError as exc:
                raise BuildError("unsupported_previous_version") from exc
            versions.add(verified.manifest.app_version)
    return sorted(versions), migration_changes


def compatible_versions(repository: Path, archives: list[Path]) -> list[str]:
    return _compatibility(repository, archives)[0]


def build_workspace_update_archive(
    repository: Path, output: Path, archives: list[Path], *, target_version: str | None = None,
) -> Path:
    repository = repository.resolve()
    output = validate_output_directory(repository, output.resolve())
    output.mkdir(parents=True, exist_ok=True)
    files = workspace_source_files(repository)
    source_digest = _source_digest(repository, files)
    base_sha = _base_sha(repository)
    extras = {
        "UPDATE.sh": (repository / "deploy/install-archive/UPDATE.sh").read_bytes(),
        "README-RU.md": (repository / "docs/runbooks/local-update.md").read_bytes(),
        "SYSTEM-OPERATIONS-RU.md": (
            repository / "docs/runbooks/system-operations.md"
        ).read_bytes(),
    }
    versions, migration_changes = _compatibility(repository, archives)
    latest = max((ReleaseVersion.parse(v) for v in versions), key=lambda v: v.precedence)
    if target_version is not None:
        try:
            requested = ReleaseVersion.parse(target_version)
        except ValueError as exc:
            raise BuildError("invalid_target_version") from exc
        if requested.precedence <= latest.precedence:
            raise BuildError("update_version_not_newer")
    identity = hashlib.sha256(
        json.dumps(
            {
                "kind": "local-update-v1",
                "source": source_digest,
                "base": base_sha,
                "compatible_from": versions,
                "target_version": target_version,
                "extra_sha256": {
                    n: hashlib.sha256(d).hexdigest() for n, d in extras.items()
                },
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    if target_version is None:
        base_version = f"{latest.major}.{latest.minor}.{latest.patch}"
        if latest.prerelease is not None:
            rc = re.fullmatch(r"rc\.([0-9]+)(?:\.dev[0-9]+)?", latest.prerelease)
            if rc is None:
                raise BuildError("explicit_target_version_required")
            base_version += f"-rc.{rc[1]}"
        version = snapshot_version(base_version, identity)
    else:
        version = target_version
    with tempfile.TemporaryDirectory(prefix="robopark-update-build-") as scratch_name:
        scratch = Path(scratch_name)
        stage = scratch / "source"
        stage.mkdir()
        _stage_snapshot(
            repository, stage, files, source_digest, identity, target_version=version
        )
        metadata_path = stage / "deploy/release-metadata.json"
        metadata = json.loads(metadata_path.read_text())
        metadata["compatible_from_versions"] = versions
        metadata["update_notes"] = (
            "Исправлено определение IP клиента за Tuna: лимиты входа получают адрес клиента "
            "вместо общего loopback-адреса. Список доверенных прокси не расширен. "
            "Сохранены данные, терминал с отдельным TOTP для root, формат OTA и миграция 0056. "
            f"Workspace source SHA-256: {source_digest}."
        )
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n"
        )
        ota = build_ota(stage, scratch / "ota", git_sha=base_sha)
        snapshot = {
            "kind": "local-update-worktree",
            "version": version,
            "base_git_sha": base_sha,
            "source_sha256": source_digest,
            "archive_identity_sha256": identity,
            "compatible_from": versions,
            "migration_head": metadata["migration_head"],
            "source_file_count": len(files),
            "migration_changes": migration_changes,
        }
        data = {
            **extras,
            ota.name: ota.read_bytes(),
            "SOURCE-SNAPSHOT.json": (
                json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
            ).encode(),
        }
        data["SHA256SUMS"] = "".join(
            f"{hashlib.sha256(d).hexdigest()}  {n}\n" for n, d in sorted(data.items())
        ).encode()
        target = output / f"robopark-{version}-update.tar.gz"
        buffer = io.BytesIO()
        with (
            gzip.GzipFile(
                filename="", fileobj=buffer, mode="wb", mtime=0
            ) as compressed,
            tarfile.open(fileobj=compressed, mode="w") as package,
        ):
            for n, d in sorted(data.items()):
                info, payload = _tar_member(n, d, 0o755 if n == "UPDATE.sh" else 0o644)
                package.addfile(info, io.BytesIO(payload))
        if (
            workspace_source_files(repository) != files
            or _source_digest(repository, files) != source_digest
        ):
            raise BuildError("workspace_changed_during_build")
        if _base_sha(repository) != base_sha or any(
            (repository / p).read_bytes() != extras[n]
            for n, p in (
                ("UPDATE.sh", "deploy/install-archive/UPDATE.sh"),
                ("README-RU.md", "docs/runbooks/local-update.md"),
                ("SYSTEM-OPERATIONS-RU.md", "docs/runbooks/system-operations.md"),
            )
        ):
            raise BuildError("workspace_changed_during_build")
        payload = buffer.getvalue()
        fd, name = tempfile.mkstemp(prefix=".local-update-", dir=output)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                if target.is_symlink() or target.read_bytes() != payload:
                    raise BuildError("archive_identity_collision") from None
        finally:
            temporary.unlink(missing_ok=True)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--from-archive", type=Path, action="append", required=True)
    parser.add_argument("--version", help="Explicit newer release version; otherwise generate an RC snapshot")
    args = parser.parse_args()
    target = build_workspace_update_archive(ROOT, args.output, args.from_archive, target_version=args.version)
    print(
        json.dumps(
            {
                "path": str(target),
                "size": target.stat().st_size,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
