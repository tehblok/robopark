#!/usr/bin/env python3
"""Require one canonical semantic version across shipped version sources and tag."""

import argparse
import ast
import importlib.util
import json
import sys
from pathlib import Path

import tomllib
from robopark_version import ReleaseVersion


def check(root, tag=None):
    version = (root / "VERSION").read_text().strip()
    parsed = ReleaseVersion.parse(version)
    api = tomllib.loads((root / "apps/api/pyproject.toml").read_text())["project"]["version"]
    api_lock = tomllib.loads((root / "apps/api/uv.lock").read_text())
    locked_api_versions = [
        package.get("version")
        for package in api_lock.get("package", [])
        if package.get("name") == "robopark-api"
        and package.get("source") == {"editable": "."}
    ]
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
    if (len(locked_api_versions) != 1 or locked_api_versions[0] not in {version, parsed.python_version}) or host != [version] or {
        api,
        web,
        lock["version"],
        lock["packages"][""]["version"],
    } != {version}:
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
        path = Path(__file__).with_name("check-release-migrations.py")
        spec = importlib.util.spec_from_file_location("check_release_migrations", path)
        if spec is None or spec.loader is None:
            raise ValueError("release_migration_checker_missing")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if module.check_release_migrations(Path(__file__).resolve().parent.parent):
            raise ValueError("release_migration_heads_inconsistent")
    except (KeyError, OSError, SyntaxError, TypeError, ValueError, tomllib.TOMLDecodeError):
        print("Release tag/version sources are inconsistent.", file=sys.stderr)
        sys.exit(1)
