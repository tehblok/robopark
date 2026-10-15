#!/usr/bin/env python3
"""One-time v1 creation, followed by normal immutable verification on every release."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import macos_keychain_broker as broker
from build_macos_pkg import sign_application_target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initialize-v1", action="store_true")
    args = parser.parse_args()
    if not args.initialize_v1:
        broker.verify_frozen()
        print("Immutable broker verified")
        return
    if broker.FROZEN.exists():
        raise SystemExit("Refusing to replace broker v1; use an explicit versioned migration.")
    scratch = Path("/private/tmp/robopark-keychain-broker-release")
    options = ["--package-path", str(broker.ROOT / "apps/macos"), "--scratch-path", str(scratch),
               "--cache-path", str(scratch / "cache"), "--config-path", str(scratch / "config"),
               "--security-path", str(scratch / "security"), "--disable-sandbox",
               "--configuration", "release", "--arch", "arm64"]
    env = {**os.environ, "CLANG_MODULE_CACHE_PATH": str(scratch / "clang"),
           "SWIFTPM_MODULECACHE_OVERRIDE": str(scratch / "modules")}
    subprocess.run(["swift", "build", *options, "--product", broker.EXECUTABLE], check=True, env=env)
    binary_dir = Path(subprocess.check_output(["swift", "build", *options, "--show-bin-path"], text=True, env=env).strip())
    broker.FROZEN.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="broker-initial-", dir=broker.FROZEN.parent) as temporary:
        staging = Path(temporary) / "arm64"
        staging.mkdir()
        target = staging / broker.EXECUTABLE
        shutil.copy2(binary_dir / broker.EXECUTABLE, target)
        subprocess.run(["strip", "-S", str(target)], check=True)
        certificate = sign_application_target(target, identifier=broker.IDENTIFIER,
            developer_identity=None, test_secret_dir=broker.ROOT / ".release-secrets/macos-test-signing")
        cdhash, actual_certificate = broker.signature_metadata(target)
        if actual_certificate != certificate:
            raise ValueError("broker_certificate_mismatch")
        manifest = {"schema": 1, "protocol": 1, "architecture": "arm64", "identifier": broker.IDENTIFIER,
                    "certificate_sha256": certificate, "cdhash": cdhash,
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                    "source_sha256": broker.source_digest()}
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        broker.verify_frozen(staging)
        staging.rename(broker.FROZEN)
    print("Initial immutable broker created; review and commit public binary with its source and manifest.")


if __name__ == "__main__":
    main()
