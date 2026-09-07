#!/usr/bin/env python3
"""Decode CI signing material into a new private file only after trust validation."""

import argparse
import base64
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def prepare(output, public):
    encoded = os.environ.pop("ROBOPARK_RELEASE_SIGNING_KEY_B64", "")
    if not encoded or len(encoded) > 16384:
        raise ValueError()
    raw = base64.b64decode(encoded, validate=True)
    key = serialization.load_pem_private_key(raw, password=None)
    trusted = serialization.load_pem_public_key(public.read_bytes())
    if not isinstance(key, Ed25519PrivateKey) or not isinstance(trusted, Ed25519PublicKey):
        raise ValueError()
    if key.public_key() != trusted:
        raise ValueError()
    descriptor = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        output.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        prepare(args.output, args.public_key)
    except Exception:
        print(
            "Signing secret is missing, invalid, untrusted, or output is unsafe.", file=sys.stderr
        )
        sys.exit(1)
