#!/usr/bin/env python3
"""Offline artifact verifier. Copy this file and install cryptography; no checkout needed."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import stat
import sys
import tarfile
import zipfile
from datetime import datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

MAX_ARCHIVE = 512 * 1024 * 1024
MAX_EXPANDED = 2 * 1024 * 1024 * 1024
MAX_MANIFEST = 4 * 1024 * 1024
MAX_MEMBER_COUNT = 20000
MAX_COMPRESSION_RATIO = 200
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
METADATA_KEYS = {
    "format",
    "kind",
    "filename",
    "size",
    "sha256",
    "app_version",
    "git_sha",
    "migration_head",
}


def require_semver(value):
    """Same bounded SemVer contract as the independently installed host verifier."""
    pattern = (
        r"(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})"
        r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
        r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    )
    match = re.fullmatch(pattern, value) if isinstance(value, str) and len(value) <= 100 else None
    require(match is not None)
    if match[4]:
        require(
            all(
                not part.isdigit() or len(part) == 1 or part[0] != "0"
                for part in match[4].split(".")
            )
        )


def require(condition):
    if not condition:
        raise ValueError("artifact_invalid")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def safe_name(name):
    require(isinstance(name, str) and bool(name) and "\\" not in name)
    require(all(ord(c) >= 32 and ord(c) != 127 for c in name))
    require(
        all(p not in {"", ".", ".."} and p == p.strip() and ":" not in p for p in name.split("/"))
    )
    return name


def read_regular(path, limit):
    # O_NONBLOCK avoids hanging on a malicious FIFO; no final symlink is followed.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_size <= limit)
        data = stream.read(limit + 1)
        require(len(data) <= limit)
        return data


def public_key(data):
    key = serialization.load_pem_public_key(data)
    require(isinstance(key, Ed25519PublicKey))
    return key


def verify_release(raw, key):
    require(len(raw) <= MAX_ARCHIVE)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        names = archive.namelist()
        require(len(names) <= MAX_MEMBER_COUNT and len(names) == len(set(names)))
        total = 0
        for info in infos:
            safe_name(info.filename)
            require(stat.S_IFMT(info.external_attr >> 16) in {0, stat.S_IFREG})
            total += info.file_size
            require(
                total <= MAX_EXPANDED
                and info.file_size <= max(info.compress_size, 1) * MAX_COMPRESSION_RATIO
            )
        require(archive.getinfo("manifest.json").file_size <= MAX_MANIFEST)
        require(archive.getinfo("manifest.sig").file_size == 64)
        manifest = json.loads(archive.read("manifest.json"), object_pairs_hook=unique_object)
        require(isinstance(manifest, dict) and set(manifest) == MANIFEST_KEYS)
        key.verify(
            archive.read("manifest.sig"),
            json.dumps(
                manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode(),
        )
        require(
            manifest["kind"] == "release"
            and type(manifest["format"]) is int
            and manifest["format"] == 2
        )
        validate_policy_metadata(manifest)
        require_semver(manifest["app_version"])
        require_semver(
            "0.0.0"
            if manifest["min_installer_version"] == "0"
            else manifest["min_installer_version"]
        )
        require(
            isinstance(manifest["git_sha"], str)
            and re.fullmatch(r"[a-fA-F0-9]{40}", manifest["git_sha"])
        )
        require(
            isinstance(manifest["migration_head"], str)
            and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", manifest["migration_head"])
        )
        require(isinstance(manifest["migration_compatibility"], dict))
        require(isinstance(manifest["update_notes"], str))
        require(
            isinstance(manifest["required_capabilities"], list)
            and all(isinstance(v, str) and v for v in manifest["required_capabilities"])
        )
        require(isinstance(manifest["created_at"], str) and len(manifest["created_at"]) <= 40)
        require(
            datetime.fromisoformat(manifest["created_at"].replace("Z", "+00:00")).utcoffset()
            is not None
        )
        files = manifest["files"]
        require(isinstance(files, dict) and bool(files))
        require(set(names) == set(files) | {"manifest.json", "manifest.sig"})
        for name, descriptor in files.items():
            safe_name(name)
            require(name not in {"manifest.json", "manifest.sig"})
            require(isinstance(descriptor, dict) and set(descriptor) == {"size", "sha256"})
            require(type(descriptor["size"]) is int and descriptor["size"] >= 0)
            require(
                isinstance(descriptor["sha256"], str)
                and re.fullmatch(r"[a-f0-9]{64}", descriptor["sha256"])
            )
            content = archive.read(name)
            require(
                len(content) == descriptor["size"]
                and hashlib.sha256(content).hexdigest() == descriptor["sha256"]
            )
        return manifest


def verify_installer(raw, key, trusted_key):
    required = {
        "install.sh",
        "README-RU.txt",
        "keys/release-public-key.pem",
        "payload/robopark-release.zip",
        "verifier/robopark_api/__init__.py",
        "verifier/robopark_api/services/__init__.py",
        "verifier/robopark_api/services/ops/__init__.py",
        "verifier/robopark_api/services/ops/archives.py",
        "verifier/robopark_api/services/ops/release_signing.py",
    }
    files = {}
    total = 0
    # Iterate headers with explicit limits; do not extract or import bundled code.
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r|gz") as archive:
        for member in archive:
            name = safe_name(member.name)
            require(member.isfile() and name not in files and len(files) < 20000)
            require(name in required or (name.startswith("lib/") and len(name.split("/")) == 2))
            total += member.size
            require(0 <= member.size <= MAX_ARCHIVE and total <= MAX_EXPANDED)
            files[name] = archive.extractfile(member).read()
    require(required <= set(files))
    require(files["keys/release-public-key.pem"] == trusted_key)
    return verify_release(files["payload/robopark-release.zip"], key)


def verify_artifact(path, trusted_key):
    path = Path(path)
    key = public_key(trusted_key)
    raw = read_regular(path, MAX_ARCHIVE)
    signature = read_regular(str(path) + ".sig", 64)
    require(len(signature) == 64)
    key.verify(signature, raw)
    digest = hashlib.sha256(raw).hexdigest()
    require(read_regular(str(path) + ".sha256", 1024) == f"{digest}  {path.name}\n".encode())
    metadata = json.loads(read_regular(str(path) + ".json", 4096), object_pairs_hook=unique_object)
    require(isinstance(metadata, dict) and set(metadata) == METADATA_KEYS)
    require(type(metadata["format"]) is int and metadata["format"] == 1)
    require(type(metadata["size"]) is int and metadata["size"] == len(raw))
    require(metadata["filename"] == path.name and metadata["sha256"] == digest)
    if path.name.endswith(".zip"):
        require(metadata["kind"] == "release")
        manifest = verify_release(raw, key)
    else:
        require(path.name.endswith(".tar.gz") and metadata["kind"] == "installer")
        manifest = verify_installer(raw, key, trusted_key)
    for name in ("app_version", "git_sha", "migration_head"):
        require(metadata[name] == manifest[name])
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    try:
        verify_artifact(args.artifact, read_regular(args.public_key, 16384))
    except Exception:
        print("Artifact verification failed.", file=sys.stderr)
        return 1
    print("Artifact verified: detached Ed25519 signature, metadata and release contents.")
    return 0


def validate_policy_metadata(manifest):
    """Strict signed policy fields; empty compatibility remains valid for old releases."""
    migration = manifest["migration_compatibility"]
    if not isinstance(migration, dict):
        raise ValueError("invalid_release_policy")
    if migration:
        if (
            set(migration) != {"from_heads", "reversible"}
            or type(migration["reversible"]) is not bool
        ):
            raise ValueError("invalid_release_policy")
        heads = migration["from_heads"]
        if (
            not isinstance(heads, list)
            or len(heads) > 64
            or not all(
                isinstance(head, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", head)
                for head in heads
            )
            or len(set(heads)) != len(heads)
        ):
            raise ValueError("invalid_release_policy")


if __name__ == "__main__":
    raise SystemExit(main())
