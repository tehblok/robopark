"""Canonical release version and build identity primitives."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_SEMVER = re.compile(
    r"(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
)
_GIT_SHA = re.compile(r"[0-9a-f]{40}")
_MIGRATION_HEAD = re.compile(r"[0-9A-Za-z_.-]{1,128}")


@dataclass(frozen=True, slots=True)
class ReleaseVersion:
    raw: str
    major: int
    minor: int
    patch: int
    prerelease: str | None = None

    @classmethod
    def parse(cls, value: str) -> ReleaseVersion:
        match = _SEMVER.fullmatch(value) if isinstance(value, str) and len(value) <= 100 else None
        if match is None:
            raise ValueError("invalid_version")
        prerelease = match[4]
        if prerelease and any(
            item.isdigit() and len(item) > 1 and item.startswith("0")
            for item in prerelease.split(".")
        ):
            raise ValueError("invalid_version")
        return cls(value, int(match[1]), int(match[2]), int(match[3]), prerelease)

    @property
    def stage(self) -> str:
        return "stable" if self.prerelease is None else "rc"

    @property
    def precedence(self) -> tuple[int, int, int, tuple[tuple[int, int | str], ...]]:
        if self.prerelease is None:
            suffix = ((2, ""),)
        else:
            suffix = tuple(
                (0, int(item)) if item.isdigit() else (1, item)
                for item in self.prerelease.split(".")
            )
        return (self.major, self.minor, self.patch, suffix)


@dataclass(frozen=True, slots=True)
class BuildIdentity:
    version: ReleaseVersion
    git_sha: str
    migration_head: str
    build_id: str

    @classmethod
    def create(cls, version: str, git_sha: str, migration_head: str) -> BuildIdentity:
        parsed = ReleaseVersion.parse(version)
        normalized_sha = git_sha.lower()
        if _GIT_SHA.fullmatch(normalized_sha) is None:
            raise ValueError("invalid_git_sha")
        if _MIGRATION_HEAD.fullmatch(migration_head) is None:
            raise ValueError("invalid_migration_head")
        source = f"{parsed.raw}\0{normalized_sha}\0{migration_head}".encode()
        build_id = hashlib.sha256(source).hexdigest()[:20]
        return cls(parsed, normalized_sha, migration_head, build_id)
