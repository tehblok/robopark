from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class OtaError(ValueError):
    """A stable, user-safe OTA validation failure."""


@dataclass(frozen=True)
class OtaFile:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class OtaManifest:
    format_version: int
    app_version: str
    git_sha: str
    migration_head: str
    compatible_from: tuple[str, ...]
    required_free_bytes: int
    max_expanded_bytes: int
    changes: tuple[str, ...]
    files: tuple[OtaFile, ...]


@dataclass(frozen=True)
class VerifiedOta:
    path: Path
    sha256: str
    size: int
    manifest: OtaManifest
