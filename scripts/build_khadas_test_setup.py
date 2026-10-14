#!/usr/bin/env python3
"""Wrap the verified OTA in one local Khadas-to-test installer, without secrets."""

from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import json
import os
import sys
import time
import zipfile
from pathlib import Path
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

if __package__:
    from .robopark_version import ReleaseVersion
else:
    from robopark_version import ReleaseVersion

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://roboparktest.ru.tuna.am"
EXPECTED_UPDATE_ROOT = "KgfdWbJw01IJlIG4vFzNcTY7EiwWKSJhQakpzgFH58M="


def verify_profile(profile: bytes, public: bytes, instance_id: str) -> None:
    if str(UUID(instance_id)) != instance_id or UUID(instance_id).int == 0:
        raise ValueError("invalid_instance_id")
    envelope = json.loads(profile)
    if not isinstance(envelope, dict) or set(envelope) != {"payload", "signature"}:
        raise ValueError("profile_schema_invalid")
    payload = base64.b64decode(envelope["payload"], validate=True)
    signature = base64.b64decode(envelope["signature"], validate=True)
    root = json.loads(public)
    if root != {"ed25519": EXPECTED_UPDATE_ROOT}:
        raise ValueError("untrusted_update_root")
    key = base64.b64decode(root["ed25519"], validate=True)
    try:
        Ed25519PublicKey.from_public_bytes(key).verify(signature, payload)
    except (InvalidSignature, ValueError):
        raise ValueError("profile_signature_invalid") from None
    assignment = json.loads(payload)
    expected = {
        "purpose": "robopark-deployment-v1",
        "origin": ORIGIN,
        "instance_id": instance_id,
        "environment": "test",
        "channel": "rc",
    }
    if not isinstance(assignment, dict) or set(assignment) != {*expected, "expires"}:
        raise ValueError("profile_schema_invalid")
    if any(assignment.get(k) != v for k, v in expected.items()):
        raise ValueError("profile_assignment_mismatch")
    if (
        type(assignment.get("expires")) is not int
        or assignment["expires"] <= time.time()
    ):
        raise ValueError("profile_expired")


def pinned_engine(target: dict) -> bytes:
    """Render immutable release pins; runtime never trusts target.json for its pins."""
    source = (ROOT / "scripts/khadas_test_setup.py").read_text()
    pins = {
        "EXPECTED_VERSION": target["version"],
        "EXPECTED_OTA_NAME": target["ota_name"],
        "EXPECTED_OTA_SHA256": target["ota_sha256"],
        "EXPECTED_OTA_SIZE": target["ota_size"],
        "PINNED_UPDATE_ROOT_B64": EXPECTED_UPDATE_ROOT,
    }
    lines = source.splitlines(keepends=True)
    found = set()
    for node in ast.parse(source).body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        name = node.targets[0]
        if not isinstance(name, ast.Name) or name.id not in pins:
            continue
        if name.id in found or node.lineno != node.end_lineno:
            raise ValueError("engine_pin_layout_invalid")
        found.add(name.id)
        lines[node.lineno - 1] = f"{name.id} = {pins[name.id]!r}\n"
    if found != set(pins):
        raise ValueError("engine_pin_layout_invalid")
    rendered = "".join(lines)
    compile(rendered, "khadas_test_setup.py", "exec")
    return rendered.encode()


def build_bundle(
    output: Path,
    ota: Path,
    profile: Path,
    update_root: Path,
    instance_id: str,
    *,
    verify=None,
    version: str | None = None,
) -> dict:
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    if verify is None:
        sys.path.insert(0, str(ROOT / "deploy/ota"))
        from robopark_ota.verify import verify_ota

        verify = verify_ota
    selected_version = ReleaseVersion.parse(
        version or (ROOT / "VERSION").read_text().strip()
    )
    verified = verify(ota)
    if (
        selected_version.stage != "rc"
        or verified.manifest.app_version != selected_version.raw
        or ota.name != f"robopark-{selected_version.raw}.ota"
    ):
        raise ValueError("target_ota_invalid")
    profile_raw, public_raw = profile.read_bytes(), update_root.read_bytes()
    verify_profile(profile_raw, public_raw, instance_id)
    if not 0 < verified.size <= 512 * 1024 * 1024:
        raise ValueError("target_ota_invalid")
    with ota.open("rb") as stream:
        ota_raw = stream.read(verified.size + 1)
    if (
        len(ota_raw) != verified.size
        or hashlib.sha256(ota_raw).hexdigest() != verified.sha256
    ):
        raise ValueError("ota_changed_after_verification")
    target = {
        "version": verified.manifest.app_version,
        "ota_name": ota.name,
        "ota_sha256": hashlib.sha256(ota_raw).hexdigest(),
        "ota_size": len(ota_raw),
        "origin": ORIGIN,
        "subdomain": "roboparktest",
        "region": "ru",
        "instance_id": instance_id,
        "profile_sha256": hashlib.sha256(profile_raw).hexdigest(),
    }
    members = {
        "__main__.py": (
            b"from khadas_test_setup import main\n"
            b"from khadas_test_isolation import isolate\n"
            b"raise SystemExit(main(isolation_callback=isolate))\n"
        ),
        "khadas_test_setup.py": pinned_engine(target),
        "khadas_test_isolation.py": (
            ROOT / "scripts/khadas_test_isolation.py"
        ).read_bytes(),
        "target.json": (json.dumps(target, sort_keys=True, indent=2) + "\n").encode(),
        "profile.json": profile_raw,
        "update-root.json": public_raw,
        ota.name: ota_raw,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o755)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(b"#!/usr/bin/env python3\n")
            with zipfile.ZipFile(
                stream, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                for name, content in sorted(members.items()):
                    info = zipfile.ZipInfo(name, (2026, 10, 14, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o100644 << 16
                    archive.writestr(info, content)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--ota", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--update-root", type=Path, required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument(
        "--version", help="Exact RC version (defaults to repository VERSION)"
    )
    args = parser.parse_args()
    target = build_bundle(
        args.output,
        args.ota,
        args.profile,
        args.update_root,
        args.instance_id,
        version=args.version,
    )
    print(
        json.dumps(
            {
                "file": str(args.output),
                "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
                "target": target,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
