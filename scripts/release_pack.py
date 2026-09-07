#!/usr/bin/env python3
"""Build a signed format-2 Robopark release archive from a staging tree."""

from __future__ import annotations

import argparse
import stat
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY_ROOT / "apps" / "api" / "src"))

from robopark_api.services.ops.archives import KIND_RELEASE, build_archive  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--migration-head", required=True)
    parser.add_argument("--signing-key", type=Path, required=True)
    args = parser.parse_args()

    source_root = args.root.resolve()
    key_path = args.signing_key.resolve()
    if not source_root.is_dir():
        parser.error("--root must be a directory")
    if not key_path.is_file() or not key_path.stat().st_mode & stat.S_IRUSR:
        parser.error("--signing-key must be a readable regular file")
    if stat.S_IMODE(key_path.stat().st_mode) & 0o077:
        parser.error("--signing-key must not be group- or world-readable")

    archive = build_archive(
        kind=KIND_RELEASE,
        source_root=source_root,
        app_version=args.version,
        release_meta={"git_sha": args.git_sha, "migration_head": args.migration_head},
        signing_key=key_path.read_bytes(),
    )
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(archive)
    print(f"wrote {output} ({len(archive)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
