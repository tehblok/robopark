#!/usr/bin/env python3
"""Reproducible, fail-closed packaging for release and installer artifacts."""

from __future__ import annotations

import argparse
import ast
import fnmatch
import gzip
import hashlib
import io
import json
import os
import re
import runpy
import stat
import sys
import tarfile
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

UTC = timezone.utc  # noqa: UP017 -- packaging also runs with system Python 3.10

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def safe_source_path(path, boundary):
    """Reject symlinks in every component below the trusted repository boundary."""
    path, boundary = Path(path).absolute(), Path(boundary).absolute()
    if not path.is_relative_to(boundary):
        raise ValueError("unsafe_source")
    current = boundary
    if current.is_symlink():
        raise ValueError("unsafe_source")
    for component in path.relative_to(boundary).parts:
        current = current / component
        if current.is_symlink():
            raise ValueError("unsafe_source")
    if not path.resolve().is_relative_to(boundary.resolve()):
        raise ValueError("unsafe_source")


verifier_source = Path(__file__).with_name("verify-artifact.py")
safe_source_path(verifier_source, REPOSITORY_ROOT)
VERIFIER = runpy.run_path(str(verifier_source))
EXCLUDED_DIRS = {
    ".git",
    ".release-secrets",
    ".cache",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "dist",
    "build",
    "coverage",
    "logs",
    ".worktrees",
    ".superpowers",
}
ROOT_FILES = ("README.md", "VERSION", ".dockerignore", ".gitignore", ".github/workflows/ci.yml")


def excluded(relative):
    parts = relative.parts
    name = relative.name.lower()
    if any(p.lower() in EXCLUDED_DIRS or p.endswith(".egg-info") for p in parts):
        return True
    if (
        parts[:3] == ("apps", "api", "data")
        and relative.as_posix() != "apps/api/data/emergency_sections.json"
    ):
        return True
    if parts[:1] in (("data",), ("diagnostics",)):
        return True
    if (name == ".env" or name.startswith(".env.") or name.endswith(".env") or ".env." in name) and not (
        name.endswith(".env.example") or name == ".env.example"
    ):
        return True
    patterns = (
        "*.pyc",
        "*.pyo",
        "*.log",
        "*.log.*",
        "*.db*",
        "*.sqlite*",
        "*-wal",
        "*-shm",
        "*.key",
        "*.p12",
        "*.pfx",
        "*.jks",
        "*private*key*",
        "*signing*.pem",
        "*secret*.pem",
        "id_rsa*",
        "id_dsa*",
        "id_ecdsa*",
        "id_ed25519*",
    )
    return any(fnmatch.fnmatch(name, p) for p in patterns) or (
        name.endswith(".pem") and name != "release-public-key.pem"
    )


def read_source(path, boundary, limit=512 * 1024 * 1024):
    safe_source_path(path, boundary)
    data = VERIFIER["read_regular"](path, limit)
    if re.search(rb"(?m)^-----BEGIN [A-Z ]*PRIVATE KEY-----\r?$", data):
        raise ValueError("private_key_in_source")
    return data


def source_files(root, repository=False, trusted_root=None):
    root = Path(root)
    boundary = root if trusted_root is None else Path(trusted_root)
    safe_source_path(root, boundary)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("unsafe_source")
    roots = (
        [root / p for p in ("apps/api", "apps/web", "deploy", "scripts")] if repository else [root]
    )
    files = {}

    def visit(directory):
        for entry in sorted(directory.iterdir()):
            relative = entry.relative_to(root)
            if excluded(relative):
                # The API seed is the only allowed runtime-data entry.
                if (
                    relative.as_posix() == "apps/api/data"
                    and entry.is_dir()
                    and not entry.is_symlink()
                ):
                    seed = entry / "emergency_sections.json"
                    if seed.exists():
                        add(seed)
                continue
            mode = entry.lstat().st_mode
            if stat.S_ISDIR(mode):
                visit(entry)
            elif stat.S_ISREG(mode):
                add(entry)
            else:
                raise ValueError("unsafe_source")

    def add(path):
        name = path.relative_to(root).as_posix()
        VERIFIER["safe_name"](name)
        files[name] = read_source(path, boundary)

    for directory in roots:
        safe_source_path(directory, boundary)
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("unsafe_source")
        visit(directory)
    if repository:
        for name in ROOT_FILES:
            path = root / name
            if path.is_symlink():
                raise ValueError("unsafe_source")
            if path.exists():
                add(path)
    return files


def epoch():
    value = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    if not 0 <= value <= 0xFFFFFFFF:
        raise ValueError("invalid_source_date_epoch")
    return value


def mode_for(name):
    if name == ".robopark-preset.env":
        return 0o600
    return 0o755 if name.endswith(".sh") or name == "deploy/host/robopark" else 0o644


def validate_output(output, forbidden):
    output = Path(output).absolute()
    VERIFIER["safe_name"](output.name)
    for suffix in ("", ".sig", ".sha256", ".json"):
        path = Path(str(output) + suffix)
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError("unsafe_output")
        resolved = path.resolve()
        for protected in forbidden:
            if resolved == protected.resolve() or resolved.is_relative_to(protected.resolve()):
                raise ValueError("output_overlaps_input")
            if path.exists() and protected.exists() and path.samefile(protected):
                raise ValueError("output_overlaps_input")
    return output


def signing_key(path):
    path = Path(path)
    data = VERIFIER["read_regular"](path, 16384)
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("private_key_permissions")
    key = serialization.load_pem_private_key(data, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("ed25519_required")
    return key, data


def write_artifacts(output, raw, key, manifest, kind):
    digest = hashlib.sha256(raw).hexdigest()
    metadata = {
        "format": 1,
        "kind": kind,
        "filename": output.name,
        "size": len(raw),
        "sha256": digest,
        **{name: manifest[name] for name in ("app_version", "git_sha", "migration_head")},
    }
    assets = {
        "": raw,
        ".sig": key.sign(raw),
        ".sha256": f"{digest}  {output.name}\n".encode(),
        ".json": (json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n").encode(),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    for suffix, data in assets.items():
        fd, temporary = tempfile.mkstemp(prefix=".pack-", dir=output.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
            os.chmod(temporary, 0o644)
            os.replace(temporary, str(output) + suffix)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def release_metadata(args, files):
    """Read reviewed data and compute the sole Alembic head without executing code."""
    path = getattr(args, "metadata", None) or args.root / "deploy/release-metadata.json"
    raw = VERIFIER["read_regular"](path, 16384)
    value = json.loads(raw, object_pairs_hook=VERIFIER["unique_object"])
    required = {"migration_head", "migration_compatibility"}
    allowed = required | {
        "update_notes",
        "min_installer_version",
        "required_capabilities",
        "signing_key_rotation",
    }
    if not isinstance(value, dict) or not required <= set(value) <= allowed:
        raise ValueError("invalid_release_metadata")
    compatibility = value["migration_compatibility"]
    if not isinstance(compatibility, dict) or set(compatibility) != {"from_heads", "reversible"}:
        raise ValueError("invalid_release_metadata")
    revisions = {}
    for name, content in files.items():
        if not name.startswith("apps/api/alembic/versions/") or not name.endswith(".py"):
            continue
        constants = {}
        for node in ast.parse(content).body:
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.target]
                if isinstance(node, ast.AnnAssign)
                else []
            )
            for target in targets:
                if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}:
                    constants[target.id] = ast.literal_eval(node.value)
        if not constants:
            continue
        revision = constants.get("revision")
        parent = constants.get("down_revision")
        if (
            not isinstance(revision, str)
            or revision in revisions
            or "down_revision" not in constants
        ):
            raise ValueError("invalid_migration_graph")
        parents = (
            []
            if parent is None
            else [parent]
            if isinstance(parent, str)
            else list(parent)
            if isinstance(parent, (tuple, list))
            else None
        )
        if parents is None or not all(isinstance(item, str) for item in parents):
            raise ValueError("invalid_migration_graph")
        revisions[revision] = parents
    referenced = {parent for parents in revisions.values() for parent in parents}
    heads = set(revisions) - referenced
    if not referenced <= set(revisions) or len(heads) != 1 or value["migration_head"] not in heads:
        raise ValueError("migration_head_mismatch")
    if getattr(args, "migration_head", None) not in (None, value["migration_head"]):
        raise ValueError("migration_head_mismatch")
    # Reject disconnected/cyclic ancestry even if it accidentally has one head.
    visited, visiting = set(), set()

    def visit(revision):
        if revision in visiting:
            raise ValueError("invalid_migration_graph")
        if revision in visited:
            return
        visiting.add(revision)
        for parent in revisions[revision]:
            visit(parent)
        visiting.remove(revision)
        visited.add(revision)

    visit(next(iter(heads)))
    if not isinstance(compatibility["from_heads"], list) or not all(
        isinstance(head, str) and head in revisions for head in compatibility["from_heads"]
    ):
        raise ValueError("invalid_migration_sources")
    if visited != set(revisions):
        raise ValueError("invalid_migration_graph")
    return value


def build_release(args):
    root = args.root.absolute()
    forbidden = [root / p for p in ("apps", "deploy", "scripts")] if args.repository else [root]
    metadata_path = getattr(args, "metadata", None) or root / "deploy/release-metadata.json"
    output = validate_output(args.output, [*forbidden, args.signing_key, metadata_path])
    key, private = signing_key(args.signing_key)
    files = source_files(root, args.repository)
    metadata = release_metadata(args, files)
    # Release construction imports only checked repository code. Installer
    # construction never needs to import API source modules at all.
    api = REPOSITORY_ROOT / "apps/api/src"
    for relative in (
        "robopark_api/__init__.py",
        "robopark_api/services/__init__.py",
        "robopark_api/services/ops/__init__.py",
        "robopark_api/services/ops/archives.py",
        "robopark_api/services/ops/release_signing.py",
    ):
        safe_source_path(api / relative, REPOSITORY_ROOT)
    sys.path.insert(0, str(api))
    from robopark_api.services.ops.archives import KIND_RELEASE, build_archive

    stamp = epoch()
    with tempfile.TemporaryDirectory(prefix="robopark-stage-") as temp:
        stage = Path(temp)
        for name, content in files.items():
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        raw = build_archive(
            kind=KIND_RELEASE,
            source_root=stage,
            app_version=args.version,
            release_meta={
                "git_sha": args.git_sha,
                **metadata,
                "created_at": datetime.fromtimestamp(stamp, UTC).isoformat().replace("+00:00", "Z"),
            },
            signing_key=private,
        )
    result = io.BytesIO()
    # Zip DOS times start in 1980. Stored members avoid compressor-version drift
    # and do not generate legitimate release files rejected as compression bombs.
    zip_time = datetime.fromtimestamp(max(stamp, 315532800), UTC).timetuple()[:6]
    with (
        zipfile.ZipFile(io.BytesIO(raw)) as source,
        zipfile.ZipFile(result, "w", compression=zipfile.ZIP_STORED) as target,
    ):
        for name in sorted(source.namelist()):
            info = zipfile.ZipInfo(name, zip_time)
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | mode_for(name)) << 16
            target.writestr(info, source.read(name))
    raw = result.getvalue()
    manifest = VERIFIER["verify_release"](raw, key.public_key())
    write_artifacts(output, raw, key, manifest, "release")


def build_installer(args):
    root = REPOSITORY_ROOT
    output = validate_output(
        args.output,
        [root / "deploy", root / "scripts", root / "apps", args.signing_key, args.public_key]
        + ([args.preset] if args.preset is not None else [])
        + [Path(str(args.release) + suffix) for suffix in ("", ".sig", ".sha256", ".json")],
    )
    trusted = VERIFIER["read_regular"](args.public_key, 16384)
    manifest = VERIFIER["verify_artifact"](args.release, trusted)
    key, _ = signing_key(args.signing_key)
    if (
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        != trusted
    ):
        raise ValueError("signing_key_mismatch")
    files = source_files(root / "deploy/installer", trusted_root=root)
    if args.preset is not None:
        preset = Path(args.preset)
        if stat.S_IMODE(preset.stat().st_mode) & 0o077:
            raise ValueError("preset_permissions")
        files[".robopark-preset.env"] = VERIFIER["read_regular"](preset, 16_384)
    files["payload/robopark-release.zip"] = VERIFIER["read_regular"](
        args.release, VERIFIER["MAX_ARCHIVE"]
    )
    files["keys/release-public-key.pem"] = trusted
    for package in ("robopark_api", "robopark_api/services", "robopark_api/services/ops"):
        files["verifier/" + package + "/__init__.py"] = b""
    for name in ("archives.py", "release_signing.py"):
        source = root / "apps/api/src/robopark_api/services/ops" / name
        files["verifier/robopark_api/services/ops/" + name] = read_source(source, root, 1024 * 1024)
    result = io.BytesIO()
    stamp = epoch()
    with gzip.GzipFile(  # noqa: SIM117 -- Python 3.9 parser compatibility
        fileobj=result, mode="wb", filename="", mtime=stamp, compresslevel=9
    ) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for name, content in sorted(files.items()):
                info = tarfile.TarInfo(name)
                info.size = len(content)
                info.mode = mode_for(name)
                info.mtime = stamp
                archive.addfile(info, io.BytesIO(content))
    raw = result.getvalue()
    VERIFIER["verify_installer"](raw, key.public_key(), trusted)
    write_artifacts(output, raw, key, manifest, "installer")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", action="store_true")
    parser.add_argument("--repository", action="store_true")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version")
    parser.add_argument("--git-sha")
    parser.add_argument("--migration-head")
    parser.add_argument(
        "--metadata",
        type=Path,
        help="reviewed release metadata JSON; defaults to deploy/release-metadata.json",
    )
    parser.add_argument("--signing-key", type=Path, required=True)
    parser.add_argument("--release", type=Path)
    parser.add_argument("--public-key", type=Path)
    parser.add_argument("--preset", type=Path)
    args = parser.parse_args()
    if args.installer:
        if args.release is None or args.public_key is None:
            parser.error("--release and --public-key are required")
    elif any(getattr(args, field) is None for field in ("root", "version", "git_sha")):
        parser.error("--root, --version and --git-sha are required")
    try:
        build_installer(args) if args.installer else build_release(args)
    except Exception:
        print("Packaging failed: unsafe input, output, metadata or signing key.", file=sys.stderr)
        return 1
    print(f"Created verified {args.output.name} and signature/checksum/metadata.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
