"""Pinned HTTPS artifacts with bounded, resumable downloads."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from .release import ReleaseError

MAX_TOTAL = 12 * 1024**3
MAX_PART = 1024**3


def validate_manifest(value):
    try:
        if (
            not isinstance(value, dict)
            or set(value)
            != {
                "schema",
                "id",
                "format",
                "documents",
                "files",
                "expanded_bytes",
                "parts",
                "base_url",
            }
            or type(value["schema"]) is not int
            or value["schema"] != 1
            or value["format"] != "age-tar-gzip-v1"
            or not re.fullmatch(r"repair-[a-z0-9-]{1,64}", value["id"])
            or not re.fullmatch(
                r"https://github.com/tehblok/robopark/releases/download/knowledge-[a-z0-9-]{1,64}",
                value["base_url"],
            )
        ):
            raise ValueError("manifest")
        for key, maximum in [
            ("documents", 50000),
            ("files", 100000),
            ("expanded_bytes", MAX_TOTAL),
        ]:
            if type(value[key]) is not int or not 0 < value[key] <= maximum:
                raise ValueError(key)
        if not isinstance(value["parts"], list) or not 1 <= len(value["parts"]) <= 128:
            raise ValueError("parts")
        for index, part in enumerate(value["parts"], 1):
            if (
                not isinstance(part, dict)
                or set(part) != {"name", "bytes", "sha256", "files", "expanded_bytes"}
                or part["name"] != f"part-{index:04}.tar.gz.age"
                or type(part["bytes"]) is not int
                or not 0 < part["bytes"] <= MAX_PART
                or type(part["expanded_bytes"]) is not int
                or not 0 < part["expanded_bytes"] <= MAX_PART
                or type(part["files"]) is not int
                or not 0 < part["files"] <= 100000
                or not re.fullmatch(r"[a-f0-9]{64}", part["sha256"])
            ):
                raise ValueError("part")
        if (
            sum(x["bytes"] for x in value["parts"]) > MAX_TOTAL
            or sum(x["files"] for x in value["parts"]) != value["files"]
            or sum(x["expanded_bytes"] for x in value["parts"])
            != value["expanded_bytes"]
        ):
            raise ValueError("totals")
        return value
    except (ValueError, KeyError, TypeError) as error:
        raise ReleaseError("knowledge_manifest_invalid") from error


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def regular_size(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return 0
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.geteuid()
    ):
        raise ReleaseError("knowledge_cache_invalid")
    return info.st_size


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != "https":
            raise ReleaseError("knowledge_download_https_required")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def open_url(request, **kwargs):
    return urllib.request.build_opener(HTTPSRedirect()).open(request, **kwargs)


def download(url: str, target: Path, size: int, digest: str):
    if urlsplit(url).scheme != "https":
        raise ReleaseError("knowledge_download_https_required")
    if target.exists() or target.is_symlink():
        if regular_size(target) == size and sha256(target) == digest:
            return target
        target.unlink()
    partial = target.with_suffix(".partial")
    offset = regular_size(partial)
    if offset > size:
        raise ReleaseError("knowledge_download_size")
    if offset < size:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Robopark-Knowledge/1",
                "Accept-Encoding": "identity",
            },
        )
        if offset:
            request.add_header("Range", f"bytes={offset}-")
        try:
            with open_url(request, timeout=30) as response:
                if urlsplit(response.geturl()).scheme != "https":
                    raise ReleaseError("knowledge_download_https_required")
                if response.status == 200:
                    offset = 0
                elif (
                    response.status != 206
                    or response.headers.get("Content-Range")
                    != f"bytes {offset}-{size - 1}/{size}"
                ):
                    raise ReleaseError("knowledge_download_range")
                flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW
                flags |= os.O_APPEND if offset else os.O_TRUNC
                fd = os.open(partial, flags, 0o600)
                with os.fdopen(fd, "wb") as output:
                    while chunk := response.read(min(1024 * 1024, size - offset + 1)):
                        if offset + len(chunk) > size:
                            raise ReleaseError("knowledge_download_size")
                        output.write(chunk)
                        offset += len(chunk)
                    output.flush()
                    os.fsync(output.fileno())
        except (OSError, urllib.error.URLError) as error:
            raise ReleaseError("knowledge_download_interrupted") from error
    if regular_size(partial) != size:
        raise ReleaseError("knowledge_download_interrupted")
    if sha256(partial) != digest:
        partial.unlink()
        raise ReleaseError("knowledge_checksum_failed")
    partial.replace(target)
    return target
