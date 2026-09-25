"""Small local release metadata helpers.

Uploaded code is admitted only by :mod:`robopark_ota`; the retired ZIP/key
release protocol is intentionally unavailable here.
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

UTC = timezone.utc
INSTALLER_VERSION = "hash-only-v1"
CAPABILITIES = {"hash-only-ota-v1", "postgresql-17", "systemd", "docker-compose-v2"}


class ReleaseError(ValueError):
    """A stable public token, never external output or a sensitive path."""


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
    match = re.fullmatch(SEMVER_PATTERN, value) if isinstance(value, str) and len(value) <= 100 else None
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


def safe_member(name):
    if not isinstance(name, str) or not name or "\\" in name or any(ord(char) < 32 for char in name):
        raise ReleaseError("unsafe_path")
    if any(part in {"", ".", ".."} or part.strip() != part for part in name.split("/")):
        raise ReleaseError("unsafe_path")
    return name


@dataclass(frozen=True)
class UpdateRequest:
    job_id: str
    kind: str
    artifact: str
    actor_user_id: int
    created_at: str

    @classmethod
    def from_dict(cls, value):
        del value
        raise ReleaseError("legacy_release_update_removed")

    @classmethod
    def from_file(cls, path):
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
                    raise ValueError()
                value = json.loads(stream.read(4097), object_pairs_hook=unique_object)
        except (OSError, ValueError, UnicodeError) as exc:
            raise ReleaseError("invalid_request") from exc
        return cls.from_dict(value)

    def read_artifact(self, paths):
        del paths
        raise ReleaseError("legacy_release_update_removed")


@dataclass(frozen=True)
class VerifiedRelease:
    raw: bytes
    manifest: dict

    def unpack(self, target):
        del target
        raise ReleaseError("legacy_release_update_removed")


def verify_archive(raw, authority=None):
    del raw, authority
    raise ReleaseError("legacy_release_update_removed")


def verify_directory(target: Path, authority=None):
    """Read bounded metadata from an already-installed immutable release."""
    del authority
    try:
        target = Path(target)
        if target.is_symlink() or not target.is_dir():
            raise ReleaseError("unsafe_path")
        manifest_path = target / "manifest.json"
        if manifest_path.is_symlink() or manifest_path.stat().st_size > 4 * 1024 * 1024:
            raise ReleaseError("invalid_manifest")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
        if not isinstance(manifest, dict):
            raise ReleaseError("invalid_manifest")
        version(manifest.get("app_version"))
        if not isinstance(manifest.get("migration_head"), str) or not re.fullmatch(
            r"[A-Za-z0-9_.-]{1,128}", manifest["migration_head"]
        ):
            raise ReleaseError("invalid_manifest")
        return manifest
    except (OSError, ValueError, UnicodeError) as exc:
        if isinstance(exc, ReleaseError):
            raise
        raise ReleaseError("release_missing") from exc


def check_compatibility(candidate, current):
    if version(candidate.get("app_version")) <= version(current.get("app_version")):
        raise ReleaseError("downgrade_rejected")


def validate_policy_metadata(manifest):
    del manifest
    raise ReleaseError("legacy_release_update_removed")
