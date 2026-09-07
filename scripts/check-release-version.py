#!/usr/bin/env python3
"""Require one numeric semantic version across shipped version sources and tag."""

import argparse
import ast
import json
import re
import sys
import tomllib
from pathlib import Path


def check(root, tag=None):
    version = (root / "VERSION").read_text().strip()
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version):
        raise ValueError()
    api = tomllib.loads((root / "apps/api/pyproject.toml").read_text())["project"]["version"]
    web = json.loads((root / "apps/web/package.json").read_text())["version"]
    lock = json.loads((root / "apps/web/package-lock.json").read_text())
    context = ast.parse((root / "apps/api/src/robopark_api/services/ops/context.py").read_text())
    host = [
        ast.literal_eval(node.value)
        for node in context.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "APP_VERSION" for target in node.targets
        )
    ]
    if host != [version] or {api, web, lock["version"], lock["packages"][""]["version"]} != {
        version
    }:
        raise ValueError()
    if tag is not None and tag != "v" + version:
        raise ValueError()
    return version


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag")
    args = parser.parse_args()
    try:
        print(check(Path(__file__).resolve().parent.parent, args.tag))
    except Exception:
        print("Release tag/version sources are inconsistent.", file=sys.stderr)
        sys.exit(1)
