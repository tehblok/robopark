#!/usr/bin/env python3
"""Generate the one-off Ed25519 keypair used to sign release archives."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _new_file(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
    os.chmod(path, mode)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private", type=Path, required=True)
    parser.add_argument("--public", type=Path, required=True)
    args = parser.parse_args()
    private_path = args.private.resolve()
    public_path = args.public.resolve()
    if private_path == public_path:
        parser.error("--private and --public must be different paths")
    if private_path.exists() or public_path.exists():
        parser.error("refusing to overwrite an existing release key")

    private = Ed25519PrivateKey.generate()
    private_bytes = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_bytes = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    _new_file(private_path, private_bytes, 0o600)
    _new_file(public_path, public_bytes, 0o644)
    print(f"wrote public key: {public_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
