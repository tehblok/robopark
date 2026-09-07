"""Independent host trust boundary; never imports code from an uploaded release."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

UTC = timezone.utc  # noqa: UP017 -- host Python 3.10 compatibility

MAX_ARCHIVE = 512 * 1024 * 1024
MAX_EXPANDED = 2 * 1024 * 1024 * 1024
MAX_MANIFEST = 4 * 1024 * 1024
INSTALLER_VERSION = "1.0.0"
CAPABILITIES = {"atomic-ota-v1", "sqlite", "systemd", "docker-compose-v2"}
MANIFEST_KEYS = {
    "kind",
    "format",
    "app_version",
    "git_sha",
    "migration_head",
    "min_installer_version",
    "migration_compatibility",
    "required_capabilities",
    "created_at",
    "update_notes",
    "files",
}


class ReleaseError(ValueError):
    """A stable token, never an external command's output or a sensitive path."""


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("timestamp")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("timestamp")
    return stamp


SEMVER_PATTERN = (
    r"(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
)


def version(value):
    """Strict bounded SemVer 2 precedence; build metadata has no ordering weight."""
    match = (
        re.fullmatch(SEMVER_PATTERN, value)
        if isinstance(value, str) and len(value) <= 100
        else None
    )
    if not match:
        raise ReleaseError("invalid_version")
    prerelease = match[4]
    identifiers = []
    if prerelease:
        for part in prerelease.split("."):
            if part.isdigit():
                if len(part) > 1 and part.startswith("0"):
                    raise ReleaseError("invalid_version")
                identifiers.append((0, int(part)))
            else:
                identifiers.append((1, part))
    return tuple(map(int, match.group(1, 2, 3))) + (not prerelease, tuple(identifiers))


@dataclass(frozen=True)
class UpdateRequest:
    job_id: str
    kind: str
    artifact: str
    actor_user_id: int
    created_at: str

    @classmethod
    def from_dict(cls, value):
        try:
            if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
                raise ValueError()
            if str(UUID(value["job_id"])) != value["job_id"]:
                raise ValueError()
            if value["kind"] != "update":
                raise ValueError()
            if type(value["actor_user_id"]) is not int or not 0 < value["actor_user_id"] < 2**63:
                raise ValueError()
            if not isinstance(value["artifact"], str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]{0,150}\.zip", value["artifact"]
            ):
                raise ValueError()
            age = (datetime.now(UTC) - timestamp(value["created_at"])).total_seconds()
            if not -300 <= age <= 86400:
                raise ValueError()
            return cls(**value)
        except (ValueError, TypeError, AttributeError, OverflowError) as exc:
            raise ReleaseError("invalid_request") from exc

    @classmethod
    def from_file(cls, path):
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
                    raise ValueError()
                raw = stream.read(4097)
                if len(raw) > 4096:
                    raise ValueError()
            return cls.from_dict(json.loads(raw, object_pairs_hook=unique_object))
        except (OSError, ValueError, UnicodeError) as exc:
            raise ReleaseError("invalid_request") from exc

    def read_artifact(self, paths):
        # Revalidate even callers constructing the frozen dataclass directly.
        self.from_dict(vars(self))
        private = bool(re.fullmatch(r"github-release-[1-9][0-9]{0,18}\.zip", self.artifact))
        root = paths.state / "github-artifacts" if private else paths.ops / "artifacts"
        expected = (
            paths.state.resolve() / "github-artifacts"
            if private
            else paths.ops.resolve() / "artifacts"
        )
        target = root / self.artifact
        try:
            if root.is_symlink() or root.resolve() != expected:
                raise ValueError()
            fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ARCHIVE:
                    raise ValueError()
                raw = stream.read(MAX_ARCHIVE + 1)
                if len(raw) > MAX_ARCHIVE:
                    raise ValueError()
                if private:
                    from .github_releases import verify_downloaded_artifact

                    verify_downloaded_artifact(raw, paths, self)
                return raw
        except (OSError, ValueError) as exc:
            raise ReleaseError("unsafe_artifact") from exc


def safe_member(name):
    if not isinstance(name, str) or not name or "\\" in name or any(ord(c) < 32 for c in name):
        raise ReleaseError("unsafe_path")
    if any(part in {"", ".", ".."} or part.strip() != part for part in name.split("/")):
        raise ReleaseError("unsafe_path")
    return name


def verify_manifest(raw, signature, public_key):
    try:
        if len(raw) > MAX_MANIFEST or len(signature) != 64:
            raise ReleaseError("invalid_manifest")
        manifest = json.loads(raw, object_pairs_hook=unique_object)
        key = serialization.load_pem_public_key(public_key)
        if not isinstance(key, Ed25519PublicKey):
            raise ReleaseError("signature_invalid")
        canonical = json.dumps(
            manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        key.verify(signature, canonical)
    except (InvalidSignature, TypeError, ValueError, UnicodeError) as exc:
        raise ReleaseError("signature_invalid") from exc
    if not isinstance(manifest, dict) or set(manifest) != MANIFEST_KEYS:
        raise ReleaseError("invalid_manifest")
    if (
        manifest["kind"] != "release"
        or type(manifest["format"]) is not int
        or manifest["format"] != 2
    ):
        raise ReleaseError("unsupported_format")
    version(manifest["app_version"])
    version(
        "0.0.0" if manifest["min_installer_version"] == "0" else manifest["min_installer_version"]
    )
    if not isinstance(manifest["git_sha"], str) or not re.fullmatch(
        r"[a-fA-F0-9]{40}", manifest["git_sha"]
    ):
        raise ReleaseError("invalid_manifest")
    if not isinstance(manifest["migration_head"], str) or not re.fullmatch(
        r"[A-Za-z0-9_.-]{1,128}", manifest["migration_head"]
    ):
        raise ReleaseError("invalid_manifest")
    if not isinstance(manifest["migration_compatibility"], dict) or not isinstance(
        manifest["update_notes"], str
    ):
        raise ReleaseError("invalid_manifest")
    if not isinstance(manifest["required_capabilities"], list) or not all(
        isinstance(c, str) for c in manifest["required_capabilities"]
    ):
        raise ReleaseError("invalid_manifest")
    try:
        if (timestamp(manifest["created_at"]) - datetime.now(UTC)).total_seconds() > 300:
            raise ValueError()
    except (ValueError, TypeError, OverflowError) as exc:
        raise ReleaseError("invalid_manifest") from exc
    files = manifest["files"]
    if not isinstance(files, dict) or not files or len(files) > 20000:
        raise ReleaseError("invalid_manifest")
    total = 0
    for name, descriptor in files.items():
        safe_member(name)
        if (
            name in {"manifest.json", "manifest.sig"}
            or not isinstance(descriptor, dict)
            or set(descriptor) != {"sha256", "size"}
        ):
            raise ReleaseError("invalid_manifest")
        if not isinstance(descriptor["sha256"], str) or not re.fullmatch(
            r"[a-f0-9]{64}", descriptor["sha256"]
        ):
            raise ReleaseError("invalid_manifest")
        if type(descriptor["size"]) is not int or descriptor["size"] < 0:
            raise ReleaseError("invalid_manifest")
        total += descriptor["size"]
    if total > MAX_EXPANDED:
        raise ReleaseError("archive_too_large")
    return manifest


@dataclass(frozen=True)
class VerifiedRelease:
    raw: bytes
    manifest: dict

    def unpack(self, target):
        target.mkdir(mode=0o700)
        with zipfile.ZipFile(io.BytesIO(self.raw)) as archive:
            for name in archive.namelist():
                destination = target / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("xb") as stream:
                    stream.write(archive.read(name))
                    stream.flush()
                    os.fsync(stream.fileno())
                destination.chmod(
                    0o755 if name.endswith(".sh") or name == "deploy/host/robopark" else 0o644
                )
        # Directory entries must survive power loss, not just their contents.
        for directory in [p for p in target.rglob("*") if p.is_dir()] + [target, target.parent]:
            descriptor = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)


def verify_archive(raw, public_key):
    try:
        if len(raw) > MAX_ARCHIVE:
            raise ReleaseError("archive_too_large")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            names = archive.namelist()
            if len(names) > 20002 or len(names) != len(set(names)):
                raise ReleaseError("duplicate_member")
            total = 0
            for info in infos:
                safe_member(info.filename)
                if stat.S_IFMT(info.external_attr >> 16) not in {0, stat.S_IFREG}:
                    raise ReleaseError("unsafe_path")
                total += info.file_size
                if (
                    total > MAX_EXPANDED + MAX_MANIFEST
                    or info.file_size > max(info.compress_size, 1) * 200
                ):
                    raise ReleaseError("archive_too_large")
            if (
                archive.getinfo("manifest.json").file_size > MAX_MANIFEST
                or archive.getinfo("manifest.sig").file_size != 64
            ):
                raise ReleaseError("invalid_manifest")
            manifest = verify_manifest(
                archive.read("manifest.json"), archive.read("manifest.sig"), public_key
            )
            if set(names) != set(manifest["files"]) | {"manifest.json", "manifest.sig"}:
                raise ReleaseError("manifest_files_mismatch")
            for name, descriptor in manifest["files"].items():
                content = archive.read(name)
                if (
                    len(content) != descriptor["size"]
                    or hashlib.sha256(content).hexdigest() != descriptor["sha256"]
                ):
                    raise ReleaseError("checksum_mismatch")
            return VerifiedRelease(raw, manifest)
    except (zipfile.BadZipFile, KeyError, RuntimeError, OSError) as exc:
        raise ReleaseError("invalid_archive") from exc


def verify_directory(target, public_key):
    try:
        if target.is_symlink() or not target.is_dir():
            raise ReleaseError("unsafe_path")
        actual = set()
        for path in target.rglob("*"):
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise ReleaseError("unsafe_path")
            if path.is_file():
                actual.add(path.relative_to(target).as_posix())
        manifest = verify_manifest(
            (target / "manifest.json").read_bytes(),
            (target / "manifest.sig").read_bytes(),
            public_key,
        )
        if actual != set(manifest["files"]) | {"manifest.json", "manifest.sig"}:
            raise ReleaseError("manifest_files_mismatch")
        for name, descriptor in manifest["files"].items():
            path = target / name
            if path.stat().st_size != descriptor["size"]:
                raise ReleaseError("checksum_mismatch")
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != descriptor["sha256"]:
                raise ReleaseError("checksum_mismatch")
        return manifest
    except OSError as exc:
        raise ReleaseError("release_missing") from exc


def check_compatibility(candidate, current):
    gate_inputs = {
        "deploy/Dockerfile.api-tests",
        "apps/api/Dockerfile",
        "apps/api/uv.lock",
        "apps/api/pyproject.toml",
        "apps/web/Dockerfile",
        "apps/web/package-lock.json",
        "apps/web/package.json",
        "scripts/verify.sh",
    }
    if not gate_inputs <= set(candidate["files"]):
        raise ReleaseError("quality_gate_inputs_missing")
    if version(candidate["app_version"]) <= version(current["app_version"]):
        raise ReleaseError("downgrade_rejected")
    if version(
        "0.0.0" if candidate["min_installer_version"] == "0" else candidate["min_installer_version"]
    ) > version(INSTALLER_VERSION):
        raise ReleaseError("installer_incompatible")
    if set(candidate["required_capabilities"]) - CAPABILITIES:
        raise ReleaseError("capability_missing")
    if candidate["migration_head"] != current["migration_head"]:
        migration = candidate["migration_compatibility"]
        if (
            set(migration) != {"from_heads", "reversible"}
            or migration.get("reversible") is not True
            or not isinstance(migration.get("from_heads"), list)
            or current["migration_head"] not in migration["from_heads"]
        ):
            raise ReleaseError("migration_incompatible")
