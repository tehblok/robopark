"""Install the private corpus on verified managed AGX hosts, outside OTA releases."""

from __future__ import annotations

import fcntl
import getpass
import hashlib
import json
import os
import secrets
import shutil
import tempfile
import warnings
from contextlib import contextmanager
from pathlib import Path

from .ai_runtime import probe_hardware, probe_support
from .knowledge_archive import extract_part
from .knowledge_assets import download, regular_size, sha256, validate_manifest
from .knowledge_crypto import decrypt_part, ensure_age
from .release import ReleaseError, unique_object
from .state import atomic_write_json


def load_manifest(paths):
    source = paths.current / "deploy/knowledge/manifest.json"
    try:
        if source.is_symlink() or source.stat().st_size > 128 * 1024:
            raise ReleaseError("knowledge_manifest_invalid")
        return validate_manifest(
            json.loads(source.read_bytes(), object_pairs_hook=unique_object)
        )
    except (ValueError, OSError) as error:
        raise ReleaseError("knowledge_manifest_invalid") from error


def _directory(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (
        path.is_symlink()
        or path.stat().st_uid != os.geteuid()
        or path.stat().st_mode & 0o077
    ):
        raise ReleaseError("knowledge_directory_invalid")
    return path


@contextmanager
def _lock(paths):
    paths.lock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(
        paths.lock_dir / "knowledge.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _bundle_ready(root):
    try:
        bundle = root / "bundle"
        metadata = bundle / "manifest.json"
        if (
            bundle.is_symlink()
            or metadata.is_symlink()
            or metadata.stat().st_size > 65536
        ):
            return False
        manifest = json.loads(metadata.read_bytes(), object_pairs_hook=unique_object)
        return (
            manifest.get("bundle_id") == "repair-private-v2"
            and manifest.get("schema") == 1
            and len(manifest["parts"]) == 1
            and manifest["parts"][0]["path"] == "seed.jsonl"
            and type(manifest["documents"]) is int
            and 0 < manifest["documents"] <= 50000
            and type(manifest["parts"][0]["bytes"]) is int
            and 0 < manifest["parts"][0]["bytes"] <= 64 * 1024**2
            and not (bundle / "seed.jsonl").is_symlink()
            and (bundle / "seed.jsonl").stat().st_size == manifest["parts"][0]["bytes"]
            and sha256(bundle / "seed.jsonl") == manifest["parts"][0]["sha256"]
        )
    except (OSError, ValueError, TypeError, KeyError):
        return False


def _publish(base, version):
    link = base / (".current-" + version.name)
    link.unlink(missing_ok=True)
    link.symlink_to(Path("versions") / version.name)
    link.replace(base / "current")


def required_space(manifest, cache):
    missing = 0
    for part in manifest["parts"]:
        target = cache / part["name"]
        cached = (
            regular_size(target)
            if target.exists() or target.is_symlink()
            else regular_size(target.with_suffix(".partial"))
        )
        missing += max(0, part["bytes"] - min(cached, part["bytes"]))
    # Extraction peak: expanded corpus, one authenticated compressed part,
    # downloader remainder and a bounded allowance for age/metadata.
    return (
        manifest["expanded_bytes"]
        + missing
        + max(p["bytes"] for p in manifest["parts"])
        + 512 * 1024**2
    )


def _clean_cache(cache, manifest):
    for part in manifest["parts"]:
        target = cache / part["name"]
        for path in (target, target.with_suffix(".partial")):
            regular_size(path)
            path.unlink(missing_ok=True)


def _installed(version, revision):
    if version.is_symlink():
        return False
    try:
        receipt = version / "receipt.json"
        if receipt.is_symlink() or receipt.stat().st_size > 65536:
            return False
        return json.loads(receipt.read_bytes(), object_pairs_hook=unique_object).get(
            "revision"
        ) == revision and _bundle_ready(version)
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def _clean_staging(versions):
    for path in versions.glob(".stage-*"):
        _directory(path)  # owned private directory, never a link
        shutil.rmtree(path)


def install_knowledge(paths, *, identity: str | None = None) -> bool:
    supported, _ = probe_support(paths)
    if not supported:
        # An AGX with missing/failed NVMe must not silently become a public-only install.
        if probe_hardware(paths)[0]:
            raise ReleaseError("knowledge_storage_unavailable")
        return False
    with _lock(paths):
        manifest = load_manifest(paths)
        revision = hashlib.sha256(
            json.dumps(manifest, sort_keys=True).encode()
        ).hexdigest()
        base = _directory(paths.var / "knowledge")
        versions = _directory(base / "versions")
        _clean_staging(versions)
        cache = _directory(_directory(base / "downloads") / revision)
        version = versions / (manifest["id"] + "-" + revision[:12])
        current = base / "current"
        selected = current.resolve() if current.is_symlink() else version
        if selected.parent != versions.resolve():
            raise ReleaseError("knowledge_directory_invalid")
        for existing in (selected, version):
            if _installed(existing, revision):
                _publish(base, existing)
                _clean_cache(cache, manifest)
                return True
        if version.exists() or version.is_symlink():
            # Preserve damaged evidence for inspection; publish a repaired copy
            # under a new immutable name without a window of dangling current.
            version = versions / (version.name + "-repair-" + secrets.token_hex(8))
        required = required_space(manifest, cache)
        if shutil.disk_usage(base).free < required:
            raise ReleaseError("knowledge_insufficient_space")
        print(
            "Загрузка полной базы знаний и исходников. Части проверяются; повторный запуск продолжит загрузку.",
            flush=True,
        )
        for index, part in enumerate(manifest["parts"], 1):
            print(f"База знаний: часть {index}/{len(manifest['parts'])}", flush=True)
            download(
                manifest["base_url"] + "/" + part["name"],
                cache / part["name"],
                part["bytes"],
                part["sha256"],
            )
        binary = ensure_age(cache)
        if identity is None:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", getpass.GetPassWarning)
                    identity = getpass.getpass(
                        "Ключ базы знаний (AGE-SECRET-KEY-…, ввод скрыт): "
                    ).strip()
            except (EOFError, KeyboardInterrupt, getpass.GetPassWarning) as error:
                raise ReleaseError("knowledge_identity_required") from error
        stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=versions))
        key_dir = paths.root / "run/robopark-knowledge-keys"
        seen = set()
        try:
            for part in manifest["parts"]:
                plain = stage / ".part.tar.gz"
                decrypt_part(binary, cache / part["name"], plain, identity, key_dir)
                count, size = extract_part(
                    plain,
                    stage,
                    seen,
                    max_bytes=part["expanded_bytes"],
                    max_files=part["files"],
                )
                plain.unlink()
                if count != part["files"] or size != part["expanded_bytes"]:
                    raise ReleaseError("knowledge_archive_invalid")
            if len(seen) != manifest["files"] or not _bundle_ready(stage):
                raise ReleaseError("knowledge_bundle_invalid")
            bundle_manifest = json.loads((stage / "bundle/manifest.json").read_bytes())
            if bundle_manifest["documents"] != manifest["documents"]:
                raise ReleaseError("knowledge_bundle_invalid")
            (stage / "bundle").chmod(0o755)
            for item in (stage / "bundle").iterdir():
                if not item.is_file() or item.is_symlink():
                    raise ReleaseError("knowledge_bundle_invalid")
                item.chmod(0o644)
            atomic_write_json(
                stage / "receipt.json",
                {
                    "schema": 1,
                    "revision": revision,
                    "files": len(seen),
                    "documents": manifest["documents"],
                },
            )
            stage.replace(version)
            _publish(base, version)
        finally:
            identity = None
            if stage.exists():
                shutil.rmtree(stage)
        # Keep installed originals, not a second disk-sized ciphertext copy.
        _clean_cache(cache, manifest)
        print(
            f"Полная база подготовлена: {manifest['documents']} записей. Импорт включится вместе с ИИ.",
            flush=True,
        )
        return True
