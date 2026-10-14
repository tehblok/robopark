#!/usr/bin/env python3
"""Wrap the verified OTA in one local Khadas-to-test installer, without secrets."""

from __future__ import annotations

import argparse
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
    from . import khadas_test_setup as setup
else:
    import khadas_test_setup as setup

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://roboparktest.ru.tuna.am"
EXPECTED_UPDATE_ROOT = "KgfdWbJw01IJlIG4vFzNcTY7EiwWKSJhQakpzgFH58M="


def verify_profile(profile: bytes, public: bytes, instance_id: str) -> None:
    if str(UUID(instance_id)) != instance_id or UUID(instance_id).int == 0:
        raise ValueError("invalid_instance_id")
    envelope = json.loads(profile)
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
    if any(assignment.get(k) != v for k, v in expected.items()):
        raise ValueError("profile_assignment_mismatch")
    if (
        not isinstance(assignment.get("expires"), int)
        or assignment["expires"] <= time.time()
    ):
        raise ValueError("profile_expired")


def build_bundle(
    output: Path,
    ota: Path,
    profile: Path,
    update_root: Path,
    instance_id: str,
    *,
    verify=None,
) -> dict:
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    if verify is None:
        sys.path.insert(0, str(ROOT / "deploy/ota"))
        from robopark_ota.verify import verify_ota

        verify = verify_ota
    verified = verify(ota)
    if not ota.name.endswith(".ota") or ota.name in {".", ".."}:
        raise ValueError("invalid_ota_name")
    profile_raw, public_raw = profile.read_bytes(), update_root.read_bytes()
    verify_profile(profile_raw, public_raw, instance_id)
    ota_raw = ota.read_bytes()
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
    setup.validate_target(target)
    members = {
        "__main__.py": (
            b"from khadas_test_setup import main\n"
            b"from khadas_test_isolation import isolate\n"
            b"raise SystemExit(main(isolation_callback=isolate))\n"
        ),
        "khadas_test_setup.py": (ROOT / "scripts/khadas_test_setup.py").read_bytes(),
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
    args = parser.parse_args()
    target = build_bundle(
        args.output, args.ota, args.profile, args.update_root, args.instance_id
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
