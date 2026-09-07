"""Root-only GitHub discovery and explicitly approved, authenticated downloads."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import re
import selectors
import stat
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from http.client import HTTPException
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .release import MAX_ARCHIVE, UTC, ReleaseError, timestamp, unique_object, verify_archive
from .release import version as semver
from .rollback import sync_directory
from .state import atomic_write_json, exclusive_lock

MAX_API = 2 * 1024 * 1024
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


class GithubTransportError(ReleaseError):
    """A bounded transport rejection, distinct from an ineligible release."""


def require(condition):
    if not condition:
        raise ReleaseError("github_release_invalid")


def _read(path, limit=65536, *, private=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_size <= limit)
        if private:
            require(
                info.st_uid == (0 if os.geteuid() == 0 else os.geteuid())
                and not info.st_mode & 0o077
            )
        raw = stream.read(limit + 1)
        require(len(raw) <= limit)
        return raw


def _json(raw):
    return json.loads(raw, object_pairs_hook=unique_object)


@dataclass(frozen=True)
class GithubConfig:
    paths: object
    repository: str
    current_version: str
    channel: str = "stable"
    enabled: bool = True
    token: str = field(default="", repr=False)

    @classmethod
    def from_paths(cls, paths):
        try:
            require(paths.root != Path("/") or os.geteuid() == 0)
            values = {}
            for line in _read(paths.etc / "updater.env", 16384, private=True).decode().splitlines():
                if not line.strip() or line.startswith("#"):
                    continue
                key, sep, value = line.partition("=")
                require(
                    sep
                    and key
                    in {
                        "GITHUB_REPOSITORY",
                        "GITHUB_TOKEN",
                        "GITHUB_CHANNEL",
                        "GITHUB_ENABLED",
                    }
                    and key not in values
                )
                if len(value) >= 2 and value[0] == value[-1] == "'":
                    value = value[1:-1]
                require(all(32 <= ord(c) < 127 and c not in "'\\" for c in value))
                values[key] = value
            repository = values.get("GITHUB_REPOSITORY", "")
            enabled = values.get("GITHUB_ENABLED", "false")
            require(enabled in {"true", "false"})
            require(
                (not repository and enabled == "false")
                or bool(
                    re.fullmatch(
                        r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}",
                        repository,
                    )
                )
            )
            channel = values.get("GITHUB_CHANNEL", "stable")
            require(channel in {"stable", "prerelease"})
            current = _read(paths.current / "VERSION", 256).decode().strip()
            semver(current)
            return cls(
                paths,
                repository,
                current,
                channel,
                enabled == "true",
                values.get("GITHUB_TOKEN", ""),
            )
        except (OSError, ValueError, UnicodeError) as exc:
            raise ReleaseError("github_configuration_invalid") from exc

    @property
    def api(self):
        return "https://api.github.com/repos/" + self.repository


class AssetRedirects(urllib.request.HTTPRedirectHandler):
    """API redirects are refused. CDN redirects always drop bearer credentials."""

    max_redirections = 3

    def __init__(self, asset):
        self.asset = asset

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        require(
            self.asset
            and target.scheme == "https"
            and target.hostname
            in {"release-assets.githubusercontent.com", "objects.githubusercontent.com"}
            and target.port in {None, 443}
            and not target.username
            and not target.password
            and not target.fragment
        )
        return urllib.request.Request(
            newurl,
            headers={
                "Accept": "application/octet-stream",
                "User-Agent": "Robopark-Updater/1",
            },
        )


class GithubHttp:
    def __init__(self):
        self.deadline = time.monotonic() + 120

    def iter_bytes(self, url, *, token, asset, limit):
        """Parent enforces a hard deadline even while the child blocks in libc/HTTP."""
        deadline = min(self.deadline, time.monotonic() + (120 if asset else 30))
        require(time.monotonic() < deadline)
        reader, writer = os.pipe()
        worker = multiprocessing.get_context("fork").Process(
            target=_http_worker,
            args=(self, reader, writer, url, token, asset, limit),
            daemon=True,
        )
        started = False
        try:
            os.set_blocking(reader, False)
            worker.start()
            started = True
            os.close(writer)
            writer = None
            total = 0
            with selectors.DefaultSelector() as selector:
                selector.register(reader, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise GithubTransportError("github_transport_timeout")
                    chunk = os.read(reader, 65536)
                    if time.monotonic() >= deadline:
                        raise GithubTransportError("github_transport_timeout")
                    if not chunk:
                        break
                    total += len(chunk)
                    require(total <= limit)
                    yield chunk
            worker.join(timeout=max(0, deadline - time.monotonic()))
            if time.monotonic() >= deadline or worker.exitcode != 0:
                raise GithubTransportError("github_transport_failed")
        finally:
            os.close(reader)
            if writer is not None:
                os.close(writer)
            if started:
                if worker.is_alive():
                    worker.terminate()
                    worker.join(timeout=0.1)
                if worker.is_alive():
                    worker.kill()
                    worker.join(timeout=0.1)
                if not worker.is_alive():
                    worker.close()

    def _request_bytes(self, url, *, token, asset, limit):
        target = urlsplit(url)
        require(
            target.scheme == "https"
            and target.netloc == "api.github.com"
            and target.path.startswith("/repos/")
        )
        headers = {
            "Accept": "application/octet-stream" if asset else "application/vnd.github+json",
            "User-Agent": "Robopark-Updater/1",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(url, headers=headers)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), AssetRedirects(asset))
        started = time.monotonic()
        require(started < self.deadline)
        with opener.open(request, timeout=min(15, self.deadline - started)) as response:
            require(time.monotonic() < self.deadline)
            require(response.status == 200)
            declared = response.headers.get("Content-Length")
            if declared is not None:
                declared = int(declared)
                require(0 <= declared <= limit)
            total = 0
            while True:
                require(time.monotonic() < min(self.deadline, started + (120 if asset else 30)))
                chunk = response.read1(65536)
                require(time.monotonic() < min(self.deadline, started + (120 if asset else 30)))
                if not chunk:
                    break
                total += len(chunk)
                require(total <= limit)
                yield chunk
            require(declared is None or total == declared)


def _http_worker(client, reader, writer, url, token, asset, limit):
    """No credentials on argv, environment, disk, logs or error pipe."""
    os.close(reader)
    try:
        for chunk in client._request_bytes(url, token=token, asset=asset, limit=limit):
            view = memoryview(chunk)
            while view:
                view = view[os.write(writer, view) :]
    except Exception:
        os._exit(1)
    os._exit(0)


def _fetch(config, http, path, limit, *, asset=False):
    result = bytearray()
    try:
        for chunk in http.iter_bytes(
            config.api + path, token=config.token, asset=asset, limit=limit
        ):
            result.extend(chunk)
            require(len(result) <= limit)
        return bytes(result)
    except ReleaseError as exc:
        raise GithubTransportError("github_transport_failed") from exc


def _asset(config, http, descriptor):
    raw = _fetch(
        config,
        http,
        "/releases/assets/" + str(descriptor["id"]),
        descriptor["size"],
        asset=True,
    )
    require(len(raw) == descriptor["size"])
    if "digest" in descriptor:
        require(descriptor["digest"] == "sha256:" + hashlib.sha256(raw).hexdigest())
    return raw


@dataclass(frozen=True)
class AvailableRelease:
    repository: str
    release_id: int
    version: str
    git_sha: str
    migration_head: str
    filename: str
    size: int
    sha256: str
    assets: dict


def _release(config, http, value):
    require(isinstance(value, dict))
    identity = value.get("id")
    require(type(identity) is int and 0 < identity < 2**63)
    require(value.get("url") == config.api + "/releases/" + str(identity))
    tag = value.get("tag_name")
    require(isinstance(tag, str) and tag.startswith("v"))
    version = tag[1:]
    order = semver(version)
    # '+' is valid SemVer but excluded from GitHub artifact names and URL paths.
    require("+" not in version and order > semver(config.current_version))
    require(
        value.get("html_url") == "https://github.com/" + config.repository + "/releases/tag/" + tag
    )
    require(value.get("draft") is False and type(value.get("prerelease")) is bool)
    require(value["prerelease"] == ("-" in version))
    require(config.channel == "prerelease" or not value["prerelease"])
    name = "robopark-release-" + version + ".zip"
    wanted = {name + suffix for suffix in ("", ".sig", ".sha256", ".json")}
    entries = value.get("assets")
    require(isinstance(entries, list) and len(entries) <= 100)
    assets = {}
    ids = set()
    for entry in entries:
        require(isinstance(entry, dict) and isinstance(entry.get("name"), str))
        if entry["name"] not in wanted:
            continue  # Task10 also publishes the independently signed installer.
        identity = entry.get("id")
        size = entry.get("size")
        require(type(identity) is int and 0 < identity < 2**63 and identity not in ids)
        require(entry["name"] not in assets and type(size) is int and 0 < size <= MAX_ARCHIVE)
        require(entry.get("url") == config.api + "/releases/assets/" + str(identity))
        assets[entry["name"]] = {"id": identity, "size": size}
        if entry.get("digest") is not None:
            require(
                isinstance(entry["digest"], str)
                and bool(re.fullmatch(r"sha256:[a-f0-9]{64}", entry["digest"]))
            )
            assets[entry["name"]]["digest"] = entry["digest"]
        ids.add(identity)
    require(set(assets) == wanted)
    require(
        assets[name + ".sig"]["size"] == 64
        and assets[name + ".sha256"]["size"] <= 1024
        and assets[name + ".json"]["size"] <= 4096
    )
    metadata = _json(_asset(config, http, assets[name + ".json"]))
    require(isinstance(metadata, dict) and set(metadata) == METADATA_KEYS)
    require(
        type(metadata["format"]) is int
        and metadata["format"] == 1
        and metadata["kind"] == "release"
    )
    require(metadata["filename"] == name and metadata["app_version"] == version)
    require(type(metadata["size"]) is int and metadata["size"] == assets[name]["size"])
    require(
        isinstance(metadata["sha256"], str)
        and bool(re.fullmatch(r"[a-f0-9]{64}", metadata["sha256"]))
    )
    require(
        isinstance(metadata["git_sha"], str)
        and bool(re.fullmatch(r"[a-fA-F0-9]{40}", metadata["git_sha"]))
    )
    require(
        isinstance(metadata["migration_head"], str)
        and bool(re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", metadata["migration_head"]))
    )
    require(
        _asset(config, http, assets[name + ".sha256"]) == f"{metadata['sha256']}  {name}\n".encode()
    )
    if "digest" in assets[name]:
        require(assets[name]["digest"] == "sha256:" + metadata["sha256"])
    return AvailableRelease(
        config.repository,
        value["id"],
        version,
        metadata["git_sha"],
        metadata["migration_head"],
        name,
        metadata["size"],
        metadata["sha256"],
        assets,
    )


def _history_path(config):
    return config.paths.state / (
        "github-" + hashlib.sha256(config.repository.encode()).hexdigest() + ".json"
    )


def _saved_release(value):
    require(isinstance(value, dict) and set(value) == set(AvailableRelease.__dataclass_fields__))
    release = AvailableRelease(**value)
    require(
        isinstance(release.repository, str)
        and bool(
            re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}",
                release.repository,
            )
        )
    )
    require(type(release.release_id) is int and 0 < release.release_id < 2**63)
    semver(release.version)
    require(
        "+" not in release.version
        and release.filename == "robopark-release-" + release.version + ".zip"
    )
    require(type(release.size) is int and 0 < release.size <= MAX_ARCHIVE)
    for value, pattern in (
        (release.git_sha, r"[a-fA-F0-9]{40}"),
        (release.sha256, r"[a-f0-9]{64}"),
        (release.migration_head, r"[A-Za-z0-9_.-]{1,128}"),
    ):
        require(isinstance(value, str) and bool(re.fullmatch(pattern, value)))
    wanted = {release.filename + suffix for suffix in ("", ".sig", ".sha256", ".json")}
    require(isinstance(release.assets, dict) and set(release.assets) == wanted)
    ids = set()
    for descriptor in release.assets.values():
        require(
            isinstance(descriptor, dict)
            and set(descriptor) in ({"id", "size"}, {"id", "size", "digest"})
        )
        require(
            type(descriptor["id"]) is int
            and 0 < descriptor["id"] < 2**63
            and descriptor["id"] not in ids
        )
        require(type(descriptor["size"]) is int and 0 < descriptor["size"] <= MAX_ARCHIVE)
        ids.add(descriptor["id"])
        if "digest" in descriptor:
            require(
                isinstance(descriptor["digest"], str)
                and bool(re.fullmatch(r"sha256:[a-f0-9]{64}", descriptor["digest"]))
            )
    require(release.assets[release.filename]["size"] == release.size)
    require(release.assets[release.filename + ".sig"]["size"] == 64)
    require(release.assets[release.filename + ".json"]["size"] <= 4096)
    require(release.assets[release.filename + ".sha256"]["size"] <= 1024)
    return release


def _history(config):
    try:
        value = _json(_read(_history_path(config), private=True))
    except FileNotFoundError:
        return {}
    require(isinstance(value, dict) and set(value) == {"release", "consumed"})
    require(type(value["consumed"]) is bool)
    require(_saved_release(value["release"]).repository == config.repository)
    return value


def _publish(paths, state, release=None):
    value = {
        "state": state,
        "checked_at": datetime.now(UTC).isoformat(),
        "release": asdict(release) if release else None,
    }
    atomic_write_json(paths.state / "available-update.json", value)
    if release:
        value["release"] = {
            key: getattr(release, key)
            for key in ("release_id", "version", "git_sha", "size", "sha256")
        }
    public = paths.ops / "public"
    public.mkdir(mode=0o755, parents=True, exist_ok=True)
    public.chmod(0o755)
    atomic_write_json(public / "available-update.json", value, mode=0o644)


def check_latest_release(config, http):
    """Record availability only. Every network/configuration failure is sanitized."""
    with exclusive_lock(config.paths.ops / "host.lock"):
        try:
            if not config.enabled:
                _publish(config.paths, "disabled")
                return None
            history = _history(config)
            values = _json(_fetch(config, http, "/releases?per_page=100", MAX_API))
            require(isinstance(values, list) and len(values) <= 100)
            candidates = []
            for value in values:
                try:
                    candidate = _release(config, http, value)
                except GithubTransportError:
                    raise
                except (ReleaseError, KeyError, TypeError, ValueError):
                    continue
                prior = history.get("release")
                if prior and (
                    candidate.release_id < prior["release_id"]
                    or candidate.release_id == prior["release_id"]
                    and (history.get("consumed") or asdict(candidate) != prior)
                ):
                    continue
                candidates.append(candidate)
            if not candidates:
                _publish(config.paths, "up_to_date")
                return None
            candidate = max(candidates, key=lambda release: semver(release.version))
            atomic_write_json(
                _history_path(config), {"release": asdict(candidate), "consumed": False}
            )
            _publish(config.paths, "available", candidate)
            return candidate
        except (
            OSError,
            HTTPException,
            ValueError,
            TypeError,
            KeyError,
            RecursionError,
        ):
            _publish(config.paths, "discovery_stale")
            return None


def current_available(paths, release_id):
    value = _json(_read(paths.state / "available-update.json", private=True))
    require(isinstance(value, dict) and set(value) == {"state", "checked_at", "release"})
    require(
        value.get("state") == "available"
        and 0 <= (datetime.now(UTC) - timestamp(value.get("checked_at"))).total_seconds() <= 86400
    )
    release = _saved_release(value["release"])
    require(release.release_id == release_id)
    return release


def download_approved_release(release, paths, http):
    """Caller holds host.lock. Consume immutable availability after full verification."""
    partial = None
    try:
        from .retention import require_capacity

        require_capacity(paths, release.size + 65536)
        config = GithubConfig.from_paths(paths)
        require(config.enabled and release.repository == config.repository)
        require(current_available(paths, release.release_id) == release)
        history = _history(config)
        require(not history.get("consumed") and history.get("release") == asdict(release))
        latest = _json(_fetch(config, http, "/releases/" + str(release.release_id), MAX_API))
        require(_release(config, http, latest) == release)
        signature = _asset(config, http, release.assets[release.filename + ".sig"])
        directory = paths.state / "github-artifacts"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = directory / f"github-release-{release.release_id}.zip"
        partial = directory / (target.name + ".partial")
        partial.unlink(missing_ok=True)
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        digest = hashlib.sha256()
        total = 0
        with os.fdopen(fd, "wb") as stream:
            for chunk in http.iter_bytes(
                config.api + "/releases/assets/" + str(release.assets[release.filename]["id"]),
                token=config.token,
                asset=True,
                limit=release.size,
            ):
                total += len(chunk)
                require(total <= min(MAX_ARCHIVE, release.size))
                digest.update(chunk)
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        require(total == release.size and digest.hexdigest() == release.sha256)
        raw = _read(partial, MAX_ARCHIVE, private=True)
        from .trust import admission_key

        trusted = admission_key(paths)
        key = serialization.load_pem_public_key(trusted)
        require(isinstance(key, Ed25519PublicKey) and len(signature) == 64)
        key.verify(signature, raw)
        manifest = verify_archive(raw, trusted).manifest
        require(
            all(
                manifest[field] == getattr(release, attr)
                for field, attr in (
                    ("app_version", "version"),
                    ("git_sha", "git_sha"),
                    ("migration_head", "migration_head"),
                )
            )
        )
        metadata = {
            "format": 1,
            "kind": "release",
            "filename": release.filename,
            "app_version": release.version,
            "git_sha": release.git_sha,
            "migration_head": release.migration_head,
            "size": release.size,
            "sha256": release.sha256,
        }
        signature_path = target.with_suffix(".zip.sig")
        signature_partial = target.with_suffix(".zip.sig.partial")
        signature_partial.unlink(missing_ok=True)
        try:
            descriptor = os.open(
                signature_partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(signature)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(signature_partial, signature_path)
        finally:
            signature_partial.unlink(missing_ok=True)
        atomic_write_json(target.with_suffix(".zip.json"), metadata)
        # Durable consumption precedes publication: a crash cannot replay approval.
        atomic_write_json(_history_path(config), {"release": asdict(release), "consumed": True})
        os.replace(partial, target)
        sync_directory(directory)
        _publish(paths, "approved")
        return target
    except (
        OSError,
        HTTPException,
        ValueError,
        TypeError,
        KeyError,
        RecursionError,
        InvalidSignature,
    ) as exc:
        _publish(paths, "discovery_stale")
        raise ReleaseError("github_download_failed") from exc
    finally:
        if partial is not None:
            partial.unlink(missing_ok=True)


def verify_downloaded_artifact(raw, paths, request):
    """Reauthenticate private bytes and the root approval immediately before apply."""
    try:
        target = paths.state / "github-artifacts" / request.artifact
        approval = _json(_read(target.with_suffix(".zip.approval.json"), 4096, private=True))
        require(approval == vars(request))
        metadata = _json(_read(target.with_suffix(".zip.json"), 4096, private=True))
        require(isinstance(metadata, dict) and set(metadata) == METADATA_KEYS)
        require(
            type(metadata["format"]) is int
            and metadata["format"] == 1
            and metadata["kind"] == "release"
        )
        require(type(metadata["size"]) is int and metadata["size"] == len(raw))
        require(metadata["sha256"] == hashlib.sha256(raw).hexdigest())
        from .trust import admission_key

        trusted = admission_key(paths)
        key = serialization.load_pem_public_key(trusted)
        require(isinstance(key, Ed25519PublicKey))
        key.verify(_read(target.with_suffix(".zip.sig"), 64, private=True), raw)
        manifest = verify_archive(raw, trusted).manifest
        require(metadata["filename"] == "robopark-release-" + manifest["app_version"] + ".zip")
        require(
            all(
                metadata[field] == manifest[field]
                for field in ("app_version", "git_sha", "migration_head")
            )
        )
    except (OSError, ValueError, TypeError, KeyError, RecursionError, InvalidSignature) as exc:
        raise ReleaseError("unsafe_artifact") from exc


def run_check(paths, http=None):
    if paths.root == Path("/") and os.geteuid() != 0:
        return 1
    try:
        config = GithubConfig.from_paths(paths)
    except ReleaseError:
        with exclusive_lock(paths.ops / "host.lock"):
            _publish(paths, "discovery_stale")
        return 1
    check_latest_release(config, http or GithubHttp())
    return 0
