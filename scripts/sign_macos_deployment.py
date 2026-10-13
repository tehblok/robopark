#!/usr/bin/env python3
"""Authorize one server for Macs. Run on the release owner machine, never the server."""
from __future__ import annotations

import argparse
import base64
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def sign_profile(*, origin: str, instance_id: str, environment: str, channel: str,
                 expires: int, key: Path) -> dict[str, str]:
    parts = urlsplit(origin)
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
        or parts.query or parts.fragment or parts.path not in {"", "/"}):
        raise ValueError("invalid_origin")
    port = f":{parts.port}" if parts.port not in {None, 443} else ""
    origin = f"https://{parts.hostname.lower()}{port}"
    if str(UUID(instance_id)) != instance_id or not UUID(instance_id).int:
        raise ValueError("invalid_instance")
    if environment not in {"test", "production"} or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", channel):
        raise ValueError("invalid_assignment")
    if not int(time.time()) < expires <= int(time.time()) + 366 * 86400:
        raise ValueError("invalid_expiry")
    if key.is_symlink() or not key.is_file() or key.stat().st_mode & 0o077:
        raise ValueError("key_requires_private_regular_file")
    payload = json.dumps({"purpose": "robopark-deployment-v1", "origin": origin,
                          "instance_id": instance_id, "environment": environment,
                          "channel": channel, "expires": expires}, sort_keys=True, separators=(",", ":")).encode()
    signature = Ed25519PrivateKey.from_private_bytes(key.read_bytes()).sign(payload)
    return {"payload": base64.b64encode(payload).decode(), "signature": base64.b64encode(signature).decode()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--environment", choices=("test", "production"), required=True)
    parser.add_argument("--channel", default="rc")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--key", type=Path, default=Path(__file__).resolve().parents[1] / ".release-secrets/macos-update.key")
    args = parser.parse_args()
    envelope = sign_profile(origin=args.origin, instance_id=args.instance_id,
                            environment=args.environment, channel=args.channel,
                            expires=int(time.time()) + args.days * 86400, key=args.key)
    args.output.write_text(json.dumps(envelope, indent=2) + "\n")
    print(f"Signed deployment profile: {args.output}")


if __name__ == "__main__":
    main()
