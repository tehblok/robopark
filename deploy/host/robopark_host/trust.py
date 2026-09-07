"""Root-owned signing authority with exact retained-release pins and replayable promotion.

A bridge is signed by the current key; activation occurs only after local health.
The atomic state file is authoritative. PEM/public files are replayable projections.
Historical keys never authorize new archives, only the exact retained tree manifest.
"""

import hashlib
import json
import os
import re
import stat
import tempfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .release import ReleaseError, unique_object, verify_directory
from .state import atomic_write_json

LIMIT = 8 * 1024 * 1024


def _read(path, limit=LIMIT):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_size > limit:
            raise ReleaseError("signature_invalid")
        raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ReleaseError("signature_invalid")
        return raw


def _pem(value):
    if not isinstance(value, str) or len(value) > 256:
        raise ReleaseError("signature_invalid")
    key = serialization.load_pem_public_key(value.encode("ascii"))
    if not isinstance(key, Ed25519PublicKey):
        raise ReleaseError("signature_invalid")
    return value.encode("ascii")


def load(paths):
    target = paths.state / "signing-trust.json"
    if not target.exists() and not target.is_symlink():
        return {
            "format": 1,
            "job_id": None,
            "active_key": _read(paths.etc / "release-public-key.pem", 16384).decode("ascii"),
            "pins": {},
            "certificate": None,
        }
    try:
        value = json.loads(_read(target), object_pairs_hook=unique_object)
        if (
            not isinstance(value, dict)
            or set(value) != {"format", "job_id", "active_key", "pins", "certificate"}
            or value["format"] != 1
        ):
            raise ValueError()
        _pem(value["active_key"])
        if not isinstance(value["pins"], dict) or len(value["pins"]) > 2:
            raise ValueError()
        for name, pin in value["pins"].items():
            if (
                not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,150}", name)
                or set(pin) != {"sha256", "key"}
                or not re.fullmatch(r"[a-f0-9]{64}", pin["sha256"])
            ):
                raise ValueError()
            _pem(pin["key"])
        return value
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise ReleaseError("signature_invalid") from exc


def admission_key(paths):
    return _pem(load(paths)["active_key"])


def directory_key(paths, directory):
    """No caller may use retained authority to admit uploaded/future releases."""
    if directory.parent.resolve() != paths.releases.resolve() or directory.is_symlink():
        raise ReleaseError("unsafe_release_path")
    state = load(paths)
    pin = state["pins"].get(directory.name)
    if pin and hashlib.sha256(_read(directory / "manifest.json")).hexdigest() == pin["sha256"]:
        return _pem(pin["key"])
    return _pem(state["active_key"])


def _publish(paths, state):
    target = paths.etc / "release-public-key.pem"
    fd, temporary = tempfile.mkstemp(dir=paths.etc, prefix=".signing-key-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(_pem(state["active_key"]))
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        from .rollback import sync_directory

        sync_directory(paths.etc)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    atomic_write_json(
        paths.ops / "public/signing-trust.json",
        {"format": 1, "active_key": state["active_key"], "certificate": state["certificate"]},
        mode=0o644,
    )


def activate(paths, journal):
    """Called only after candidate health/head checks, under host ownership.

    Save rollback authority, then commit authority once. Replays finish projections.
    """
    state = load(paths)
    if state["job_id"] == journal["job_id"]:
        _publish(paths, state)
        return
    candidate = paths.releases / journal["candidate"]
    previous = paths.releases / journal["previous"]
    candidate_key = directory_key(paths, candidate)
    manifest = verify_directory(candidate, candidate_key)
    pins = {}
    for directory in (candidate, previous):
        key = directory_key(paths, directory)
        verify_directory(directory, key)
        pins[directory.name] = {
            "sha256": hashlib.sha256(_read(directory / "manifest.json")).hexdigest(),
            "key": key.decode("ascii"),
        }
    updated = {**state, "job_id": journal["job_id"], "pins": pins}
    rotation = manifest.get("signing_key_rotation")
    if rotation:
        if (
            candidate_key != admission_key(paths)
            or rotation["next_public_key"].encode() == candidate_key
        ):
            raise ReleaseError("signature_invalid")
        updated["active_key"] = rotation["next_public_key"]
        updated["certificate"] = {
            "manifest": manifest,
            "signature": _read(candidate / "manifest.sig", 64).hex(),
        }
    backup = paths.ops / "rollbacks" / journal["job_id"] / "signing-trust-before.json"
    if not backup.exists():
        atomic_write_json(backup, state)
    atomic_write_json(paths.state / "signing-trust.json", updated)
    _publish(paths, updated)


def restore(paths, journal):
    """Undo this transaction only before writes resume; never widen admission pins."""
    state = load(paths)
    if state["job_id"] != journal["job_id"]:
        _publish(paths, state)
        return
    if journal["writes_resumed"]:
        raise ReleaseError("manual_recovery_required")
    backup = paths.ops / "rollbacks" / journal["job_id"] / "signing-trust-before.json"
    before = json.loads(_read(backup), object_pairs_hook=unique_object)
    atomic_write_json(paths.state / "signing-trust.json", before)
    _publish(paths, load(paths))


def bootstrap(paths, runner):
    """Install-time bridge activation only after independently checked readiness."""
    current = paths.current.resolve(strict=True)
    manifest = verify_directory(current, directory_key(paths, current))
    if not manifest.get("signing_key_rotation"):
        return
    job = "bootstrap-" + hashlib.sha256(_read(current / "manifest.json")).hexdigest()
    state = load(paths)
    if state["job_id"] not in (None, job):
        raise ReleaseError("signature_invalid")
    if not runner.wait_ready(
        project="robopark", config=paths.state / "current-compose.json", timeout=180
    ):
        raise ReleaseError("cutover_unhealthy")
    activate(paths, {"job_id": job, "candidate": current.name, "previous": current.name})
