#!/usr/bin/env python3
"""Check or atomically synchronize all shipped release version sources."""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import tomllib
from robopark_version import ReleaseVersion


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _replace_project_version(text: str, version: str) -> str:
    pattern = re.compile(r'(?m)^(version\s*=\s*)"[^"]+"\s*$')
    result, count = pattern.subn(rf'\g<1>"{version}"', text, count=1)
    if count != 1:
        raise ValueError("project_version_not_unique")
    return result


def _replace_uv_lock_version(text: str, version: str) -> str:
    document = tomllib.loads(text)
    packages = [
        item for item in document.get("package", [])
        if item.get("name") == "robopark-api" and item.get("source") == {"editable": "."}
    ]
    if len(packages) != 1:
        raise ValueError("locked_api_version_not_unique")
    pattern = re.compile(
        r'(?ms)(\[\[package\]\]\s*\nname\s*=\s*"robopark-api"\s*\nversion\s*=\s*)"[^"]+"'
    )
    result, count = pattern.subn(rf'\g<1>"{version}"', text, count=1)
    if count != 1:
        raise ValueError("locked_api_version_not_found")
    return result


def _replace_app_version(text: str, version: str) -> str:
    tree = ast.parse(text)
    assignments = [
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "APP_VERSION" for target in node.targets)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]
    if len(assignments) != 1:
        raise ValueError("app_version_not_unique")
    node = assignments[0]
    lines = text.splitlines(keepends=True)
    lines[node.lineno - 1] = re.sub(r'"[^"]+"', f'"{version}"', lines[node.lineno - 1], count=1)
    return "".join(lines)


def _load_checker():
    path = Path(__file__).with_name("check-release-version.py")
    spec = importlib.util.spec_from_file_location("check_release_version", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("release_checker_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.check


def _write(root: Path, version: str) -> None:
    pyproject = root / "apps/api/pyproject.toml"
    uv_lock = root / "apps/api/uv.lock"
    package_json = root / "apps/web/package.json"
    package_lock = root / "apps/web/package-lock.json"
    context = root / "apps/api/src/robopark_api/services/ops/context.py"

    package = json.loads(package_json.read_text())
    package["version"] = version
    lock = json.loads(package_lock.read_text())
    lock["version"] = version
    lock["packages"][""]["version"] = version

    updates = {
        root / "VERSION": f"{version}\n",
        pyproject: _replace_project_version(pyproject.read_text(), version),
        uv_lock: _replace_uv_lock_version(uv_lock.read_text(), version),
        package_json: json.dumps(package, ensure_ascii=False, indent=2) + "\n",
        package_lock: json.dumps(lock, ensure_ascii=False, indent=2) + "\n",
        context: _replace_app_version(context.read_text(), version),
    }
    for path, content in updates.items():
        _atomic_write(path, content)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    try:
        requested = ReleaseVersion.parse(args.version).raw
        if args.write:
            _write(args.root, requested)
        actual = _load_checker()(args.root)
        if actual != requested:
            raise ValueError("requested_version_mismatch")
    except (KeyError, OSError, RuntimeError, SyntaxError, TypeError, ValueError, tomllib.TOMLDecodeError) as error:
        print(f"inconsistent_version_sources: {error}", file=sys.stderr)
        return 1
    print(requested)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
