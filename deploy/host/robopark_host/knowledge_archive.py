"""Bounded data-only extraction after age authentication, never tar.extractall."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import tarfile
from pathlib import Path, PurePosixPath

from .release import ReleaseError, unique_object

MAX_INVENTORY = 16 * 1024 * 1024
MAX_FILE = 1024**3


def safe_name(name):
    if not isinstance(name, str) or not name or len(name.encode()) > 4096:
        raise ValueError("path")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or str(path) != name
        or ".." in path.parts
        or "\\" in name
        or "\x00" in name
        or len(path.parts) < 2
        or path.parts[0] not in {"bundle", "originals", "prepared", "metadata"}
    ):
        raise ValueError("path")
    return name


def inventory(raw, *, max_bytes, max_files):
    value = json.loads(raw, object_pairs_hook=unique_object)
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "files"}
        or value["schema"] != 1
    ):
        raise ValueError("inventory")
    records = value["files"]
    if not isinstance(records, list) or not 0 < len(records) <= max_files:
        raise ValueError("files")
    result = {}
    for item in records:
        if not isinstance(item, dict) or set(item) != {"path", "bytes", "sha256"}:
            raise ValueError("file")
        name = safe_name(item["path"])
        if (
            name in result
            or type(item["bytes"]) is not int
            or not 0 <= item["bytes"] <= MAX_FILE
            or not isinstance(item["sha256"], str)
            or not re.fullmatch("[a-f0-9]{64}", item["sha256"])
        ):
            raise ValueError("file")
        result[name] = item
    if sum(x["bytes"] for x in records) > max_bytes:
        raise ValueError("size")
    return result


def _parents(root, target):
    current = root
    current.mkdir(parents=True, exist_ok=True, mode=0o700)
    if current.is_symlink():
        raise ValueError("symlink")
    for part in target.relative_to(root).parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise ValueError("symlink")
        current.mkdir(exist_ok=True, mode=0o700)


class _BoundedStream:
    def __init__(self, stream, limit):
        self.stream, self.remaining = stream, limit

    def read(self, size=-1):
        size = self.remaining + 1 if size < 0 else min(size, self.remaining + 1)
        data = self.stream.read(size)
        self.remaining -= len(data)
        if self.remaining < 0:
            raise ValueError("decompression_limit")
        return data


def extract_part(
    source: Path, root: Path, seen: set[str], *, max_bytes: int, max_files: int
):
    """Return extracted file count/bytes; caller removes staging on any failure."""
    try:
        with (
            gzip.open(source, "rb") as compressed,
            tarfile.open(
                fileobj=_BoundedStream(
                    compressed, max_bytes + max_files * 4096 + MAX_INVENTORY + 1024**2
                ),
                mode="r|",
            ) as tar,
        ):
            first = tar.next()
            if (
                first is None
                or first.name != "inventory.json"
                or not first.isfile()
                or not 0 < first.size <= MAX_INVENTORY
            ):
                raise ValueError("inventory")
            stream = tar.extractfile(first)
            records = inventory(
                stream.read(MAX_INVENTORY + 1), max_bytes=max_bytes, max_files=max_files
            )
            stream.close()
            if seen.intersection(records):
                raise ValueError("duplicate")
            pending = set(records)
            while (item := tar.next()) is not None:
                if (
                    not item.isfile()
                    or item.name not in pending
                    or item.size != records[item.name]["bytes"]
                ):
                    raise ValueError("member")
                target = root / item.name
                _parents(root, target)
                digest = hashlib.sha256()
                source_stream = tar.extractfile(item)
                fd = os.open(
                    target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
                )
                with os.fdopen(fd, "wb") as output, source_stream:
                    remaining = item.size
                    while remaining:
                        chunk = source_stream.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise ValueError("truncated")
                        output.write(chunk)
                        digest.update(chunk)
                        remaining -= len(chunk)
                if digest.hexdigest() != records[item.name]["sha256"]:
                    raise ValueError("checksum")
                pending.remove(item.name)
            if pending:
                raise ValueError("missing")
            seen.update(records)
            return len(records), sum(x["bytes"] for x in records.values())
    except (
        OSError,
        EOFError,
        ValueError,
        KeyError,
        TypeError,
        tarfile.TarError,
    ) as error:
        raise ReleaseError("knowledge_archive_invalid") from error
