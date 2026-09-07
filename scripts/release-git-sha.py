#!/usr/bin/env python3
"""Resolve release provenance for packing a checkout or authenticated payload.

The installer/updater must authenticate retained manifest metadata before running
candidate code. This helper validates its shape; it is not a signature verifier.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path


def resolve(root: Path) -> str:
    if "ROBOPARK_RELEASE_GIT_SHA" in os.environ:
        value = os.environ["ROBOPARK_RELEASE_GIT_SHA"]
    elif (root / "manifest.json").exists():
        manifest = root / "manifest.json"
        if manifest.is_symlink():
            raise ValueError("invalid_manifest")
        value = json.loads(manifest.read_text())["git_sha"]
    else:
        value = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", value):
        raise ValueError("invalid_release_git_sha")
    return value.lower()


if __name__ == "__main__":
    try:
        print(resolve(Path(sys.argv[1])))
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
        print("Release Git SHA is missing or invalid", file=sys.stderr)
        sys.exit(1)
