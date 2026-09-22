"""Read-only integrity check before an explicitly destructive reinstall."""

import os
import stat
import sys
from pathlib import Path


def read_regular(path: Path, limit: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("unsafe_bundle_file")
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("unsafe_bundle_file")
    return data


def main() -> None:
    bundle = Path(sys.argv[1]).resolve(strict=True)
    sys.path.insert(0, str(bundle / "verifier"))
    from robopark_api.services.ops.archives import (  # noqa: PLC0415
        KIND_RELEASE,
        MAX_ARCHIVE_BYTES,
        inspect_archive,
    )

    key = read_regular(bundle / "keys/release-public-key.pem", 16_384)
    payload = read_regular(bundle / "payload/robopark-release.zip", MAX_ARCHIVE_BYTES)
    inspect_archive(payload, expected_kind=KIND_RELEASE, public_key=key)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 -- no untrusted archive content in operator output
        print("release_verification_failed", file=sys.stderr)
        raise SystemExit(1) from None
