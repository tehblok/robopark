#!/usr/bin/env python3
"""Create and validate fail-closed, source-bound release acceptance evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

REQUIRED_GATES = (
    "api_postgres",
    "host",
    "full_api",
    "full_web",
    "production_build",
    "route_role_evidence",
    "visual_ledger",
    "load_200",
    "soak_8h",
)
MAX_REPORT_BYTES = 64 * 1024 * 1024
RELEASE_PREFIXES = ("apps/api/", "apps/web/", "deploy/", "scripts/")
RELEASE_ROOT_FILES = {
    "README.md",
    "VERSION",
    ".dockerignore",
    ".gitignore",
    ".github/workflows/ci.yml",
}


def _safe_relative(value: str) -> Path:
    pure = PurePosixPath(value)
    if (
        not value
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise ValueError("acceptance_path")
    return Path(*pure.parts)


def _read_regular(root: Path, relative: str, limit: int = MAX_REPORT_BYTES) -> bytes:
    path = root / _safe_relative(relative)
    if path.is_symlink():
        raise ValueError("acceptance_report")
    try:
        info = path.stat()
    except OSError as exc:
        raise ValueError("acceptance_report") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
        raise ValueError("acceptance_report")
    resolved_root = root.resolve()
    if not path.resolve().is_relative_to(resolved_root):
        raise ValueError("acceptance_report")
    return path.read_bytes()


def source_tree_digest(root: Path, paths: list[str]) -> str:
    digest = hashlib.sha256()
    if paths != sorted(set(paths)) or not paths:
        raise ValueError("acceptance_source_paths")
    for relative in paths:
        data = _read_regular(root, relative, limit=512 * 1024 * 1024)
        encoded = relative.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _json_object(data: bytes, error: str) -> dict:
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(error) from exc
    if not isinstance(value, dict):
        raise ValueError(error)
    return value


def gate_status(name: str, report: dict) -> str:
    if (
        name == "soak_8h"
        and report.get("passed") is False
        and report.get("ruling") == "USER_CANCELLED"
    ):
        return "USER_CANCELLED"
    return "PASS"


def validate_acceptance(root: Path, evidence: dict) -> dict:
    if not isinstance(evidence, dict) or set(evidence) != {
        "format",
        "source_tree_sha256",
        "source_paths",
        "gates",
    }:
        raise ValueError("acceptance_format")
    if evidence["format"] != 1 or not isinstance(evidence["source_paths"], list):
        raise ValueError("acceptance_format")
    gates = evidence["gates"]
    if not isinstance(gates, dict) or set(gates) != set(REQUIRED_GATES):
        raise ValueError("acceptance_gates")
    actual = source_tree_digest(root, evidence["source_paths"])
    if evidence["source_tree_sha256"] != actual:
        raise ValueError("acceptance_source_tree")
    for name in REQUIRED_GATES:
        gate = gates[name]
        if not isinstance(gate, dict) or set(gate) != {"status", "report"}:
            raise ValueError("acceptance_gates")
        if not isinstance(gate["report"], str):
            raise ValueError("acceptance_gates")
        if name != "soak_8h" and gate["status"] != "PASS":
            raise ValueError("acceptance_gates")
        report = _json_object(_read_regular(root, gate["report"]), "acceptance_report")
        if (
            gate["status"] == "PASS"
            and report.get("source_tree_sha256") != evidence["source_tree_sha256"]
        ):
            raise ValueError("acceptance_report_source_tree")
        if gate["status"] == "PASS" and report.get("passed") is True:
            continue
        if (
            name == "soak_8h"
            and gate["status"] == "USER_CANCELLED"
            and report.get("passed") is False
            and report.get("ruling") == "USER_CANCELLED"
            and type(report.get("elapsed_seconds")) is int
            and 0 <= report["elapsed_seconds"] < 28_800
        ):
            continue
        else:
            raise ValueError("acceptance_report")
    return evidence


def is_release_source(relative: str) -> bool:
    """Limit the digest to product/release inputs, never worktree bookkeeping."""
    return relative in RELEASE_ROOT_FILES or relative.startswith(RELEASE_PREFIXES)


def tracked_source_paths(root: Path, excludes: set[str]) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    paths = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        relative = raw.decode("utf-8")
        if relative in excludes or not is_release_source(relative):
            continue
        paths.append(relative)
    return sorted(set(paths))


def atomic_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".acceptance-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, indent=2, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--validate", type=Path)
    parser.add_argument("--create", type=Path)
    parser.add_argument("--gate", action="append", default=[], metavar="NAME=REPORT")
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        if bool(args.validate) == bool(args.create):
            raise ValueError("choose_create_or_validate")
        if args.validate:
            relative = args.validate.resolve().relative_to(root).as_posix()
            evidence = _json_object(_read_regular(root, relative), "acceptance_format")
            validate_acceptance(root, evidence)
        else:
            gates = {}
            for item in args.gate:
                name, separator, report = item.partition("=")
                if not separator or name in gates:
                    raise ValueError("acceptance_gates")
                report_value = _json_object(
                    _read_regular(root, report), "acceptance_report"
                )
                gates[name] = {
                    "status": gate_status(name, report_value),
                    "report": report,
                }
            output_relative = args.create.resolve().relative_to(root).as_posix()
            source_paths = tracked_source_paths(
                root,
                {output_relative, *(gate["report"] for gate in gates.values())},
            )
            evidence = {
                "format": 1,
                "source_paths": source_paths,
                "source_tree_sha256": source_tree_digest(root, source_paths),
                "gates": gates,
            }
            validate_acceptance(root, evidence)
            atomic_write(args.create, evidence)
        print(json.dumps({"acceptance": "PASS"}))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError):
        print("release_acceptance_failed", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
