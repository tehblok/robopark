#!/usr/bin/env python3
"""Build encrypted release assets; only ciphertext and an opaque manifest are public."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy/host"))
from robopark_host.knowledge_archive import safe_name
from robopark_host.knowledge_assets import sha256, validate_manifest

CHUNK_BYTES = 256 * 1024**2
DERIVED_EXPORTS = frozenset(
    "tracker_year_all_parks/" + name
    for name in ("corpus", "prepared", "dataset.jsonl", "training.jsonl")
)


def _write_json(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def build_bundle(seed, public_seed, key, output, *, replace_public=False):
    if len(key) < 32:
        raise ValueError("publication_identity_key_invalid")
    public = {
        row["source_ref"]: row
        for line in public_seed.open(encoding="utf-8")
        if (row := json.loads(line))
    }
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    seen, total = set(), 0
    with (output / "seed.jsonl").open("x", encoding="utf-8") as stream:
        for line in seed.open(encoding="utf-8"):
            row = json.loads(line)
            if set(row) != {"title", "content", "kind", "source_ref"}:
                raise ValueError("private_record_invalid")
            original_ref = row["source_ref"]
            public_ref = (
                "public:repair-v1:"
                + hmac.new(
                    key,
                    ("robopark-public-repair-v1\0" + original_ref).encode(),
                    hashlib.sha256,
                ).hexdigest()[:32]
            )
            ref = (
                public_ref
                if public_ref in public
                else "private:repair-v2:"
                + hmac.new(
                    key,
                    ("robopark-private-repair-v2\0" + original_ref).encode(),
                    hashlib.sha256,
                ).hexdigest()[:32]
            )
            if ref in seen:
                raise ValueError("private_record_duplicate")
            row["source_ref"] = ref
            stream.write(
                json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
            seen.add(ref)
            total += 1
        # A future corpus may omit an original that remains in the public seed.
        # Keep those identities so the switch never retires unrelated public data.
        for ref in sorted(public.keys() - seen) if not replace_public else ():
            stream.write(
                json.dumps(public[ref], ensure_ascii=False, separators=(",", ":"))
                + "\n"
            )
            total += 1
    seed_file = output / "seed.jsonl"
    value = {
        "schema": 1,
        "bundle_id": "repair-private-v2",
        "documents": total,
        "parts": [
            {
                "path": "seed.jsonl",
                "bytes": seed_file.stat().st_size,
                "documents": total,
                "sha256": sha256(seed_file),
            }
        ],
    }
    _write_json(output / "manifest.json", value)
    return value


def collect(root, prefix):
    files = []
    if not root.exists():
        raise ValueError("source_missing")
    if root.is_symlink():
        raise ValueError("source_symlink")
    candidates = [root] if root.is_file() else sorted(root.rglob("*"))
    for path in candidates:
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ValueError("source_not_regular")
        name = (
            prefix
            if root.is_file()
            else prefix + "/" + path.relative_to(root).as_posix()
        )
        safe_name(name)
        files.append((name, path))
    return files


def collect_originals(root, excluded=()):
    """Omit explicitly selected derivative exports, never sibling path prefixes."""
    prefixes = []
    for relative in excluded:
        if relative not in DERIVED_EXPORTS:
            raise ValueError("excluded_source_not_derivative")
        prefix = safe_name("originals/" + relative)
        path = root / relative
        if path.is_symlink() or not path.exists():
            raise ValueError("excluded_source_invalid")
        prefixes.append(prefix)
    return [
        entry
        for entry in collect(root, "originals")
        if not any(
            entry[0] == prefix or entry[0].startswith(prefix + "/")
            for prefix in prefixes
        )
    ]


def package_part(files, output, binary, recipient):
    raw = output.with_suffix(".plaintext.tmp")
    inventory = [
        {"path": name, "bytes": path.stat().st_size, "sha256": sha256(path)}
        for name, path in files
    ]
    try:
        fd = os.open(raw, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with (
            os.fdopen(fd, "wb") as raw_stream,
            tarfile.open(fileobj=raw_stream, mode="w:gz", compresslevel=3) as tar,
        ):
            data = json.dumps(
                {"schema": 1, "files": inventory},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
            info = tarfile.TarInfo("inventory.json")
            info.size = len(data)
            info.mode = 0o600
            tar.addfile(info, io.BytesIO(data))
            for (name, path), expected in zip(files, inventory, strict=True):
                before = path.stat()
                info = tarfile.TarInfo(name)
                info.size = before.st_size
                info.mode = 0o600
                if info.size != expected["bytes"]:
                    raise ValueError("source_changed")
                with path.open("rb") as stream:
                    tar.addfile(info, stream)
                after = path.stat()
                if (before.st_ino, before.st_mtime_ns, before.st_size) != (
                    after.st_ino,
                    after.st_mtime_ns,
                    after.st_size,
                ):
                    raise ValueError("source_changed")
        subprocess.run(
            [str(binary), "-r", recipient, "-o", str(output), str(raw)],
            check=True,
            stdin=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    finally:
        raw.unlink(missing_ok=True)
    return {
        "name": output.name,
        "bytes": output.stat().st_size,
        "sha256": sha256(output),
        "files": len(files),
        "expanded_bytes": sum(x["bytes"] for x in inventory),
    }


def build(args):
    os.umask(0o077)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    bundle = output / "local-bundle"
    value = build_bundle(
        args.seed,
        args.public_seed,
        args.publication_key.read_bytes(),
        bundle,
        replace_public=getattr(args, "replace_public", False),
    )
    originals = collect_originals(
        args.source_root, getattr(args, "exclude_original", ())
    )
    if not originals:
        raise ValueError("source_empty")
    files = collect(bundle, "bundle") + originals
    for extra in args.extra:
        prefix, separator, name = extra.partition("=")
        if not separator:
            raise ValueError("extra_requires_prefix_equals_path")
        files.extend(collect(Path(name), prefix))
    names = [name for name, _ in files]
    if len(set(names)) != len(names):
        raise ValueError("source_duplicate")
    assets = output / "assets"
    assets.mkdir(mode=0o700)
    parts = []
    group = []
    size = 0
    for entry in files:
        item_size = entry[1].stat().st_size
        if item_size > 1024**3:
            raise ValueError("source_too_large")
        if group and size + item_size > CHUNK_BYTES:
            part = package_part(
                group,
                assets / f"part-{len(parts) + 1:04}.tar.gz.age",
                args.age,
                args.recipient,
            )
            parts.append(part)
            print(
                f"Encrypted part {len(parts)}: {part['files']} files, {part['bytes']} bytes",
                flush=True,
            )
            group = []
            size = 0
        group.append(entry)
        size += item_size
    if group:
        parts.append(
            package_part(
                group,
                assets / f"part-{len(parts) + 1:04}.tar.gz.age",
                args.age,
                args.recipient,
            )
        )
    manifest = validate_manifest(
        {
            "schema": 1,
            "id": args.id,
            "format": "age-tar-gzip-v1",
            "documents": value["documents"],
            "files": len(files),
            "expanded_bytes": sum(p["expanded_bytes"] for p in parts),
            "parts": parts,
            "base_url": args.base_url,
        }
    )
    _write_json(assets / "manifest.json", manifest)
    print(
        json.dumps(
            {
                "parts": len(parts),
                "documents": value["documents"],
                "files": len(files),
                "encrypted_bytes": sum(p["bytes"] for p in parts),
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in [
        "seed",
        "public-seed",
        "publication-key",
        "source-root",
        "output",
        "age",
    ]:
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ["recipient", "id", "base-url"]:
        parser.add_argument("--" + name, required=True)
    parser.add_argument(
        "--replace-public",
        action="store_true",
        help="Curated replacement: omit missing public records so unchanged legacy tickets retire; preserve shared source IDs.",
    )
    parser.add_argument(
        "--exclude-original",
        action="append",
        default=[],
        help="Explicit path relative to source root; omit redundant derived exports only.",
    )
    parser.add_argument(
        "--extra",
        action="append",
        default=[],
        help="Encrypted destination prefix=local path",
    )
    build(parser.parse_args())


if __name__ == "__main__":
    main()
