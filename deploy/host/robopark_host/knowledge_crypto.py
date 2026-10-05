"""Official pinned age; identities never appear in argv, environment or logs."""

from __future__ import annotations

import os
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path

from .knowledge_assets import download
from .release import ReleaseError

AGE_URL = "https://github.com/FiloSottile/age/releases/download/v1.3.2/age-v1.3.2-linux-arm64.tar.gz"
AGE_SIZE = 17771923
AGE_SHA = "6b8dc4333c53a5a57c9e5834e3a48f92605d7154014cd07269ff3327db5d37f4"


def ensure_age(cache: Path):
    package = download(AGE_URL, cache / "age-linux-arm64.tar.gz", AGE_SIZE, AGE_SHA)
    binary = cache / "age"
    # Re-extract from verified distribution: never execute an unchecked cache binary.
    with tarfile.open(package, "r:gz") as tar:
        item = tar.getmember("age/age")
        if not item.isfile() or not 0 < item.size <= 64 * 1024**2:
            raise ReleaseError("knowledge_age_invalid")
        with tar.extractfile(item) as stream:
            fd = os.open(
                binary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o700
            )
            os.fchmod(fd, 0o700)
            with os.fdopen(fd, "wb") as output:
                while chunk := stream.read(1024 * 1024):
                    output.write(chunk)
    return binary


def decrypt_part(
    binary: Path, source: Path, target: Path, identity: str, key_dir: Path
):
    if not isinstance(identity, str) or not re.fullmatch(
        r"AGE-SECRET-KEY-1[A-Z0-9]{58}", identity
    ):
        raise ReleaseError("knowledge_identity_invalid")
    key_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (
        key_dir.is_symlink()
        or key_dir.stat().st_uid != os.geteuid()
        or key_dir.stat().st_mode & 0o077
    ):
        raise ReleaseError("knowledge_identity_directory_invalid")
    descriptor, name = tempfile.mkstemp(prefix="identity-", dir=key_dir)
    try:
        os.write(descriptor, (identity + "\n").encode("ascii"))
        os.lseek(descriptor, 0, os.SEEK_SET)
        # Unlink before launching: even SIGKILL cannot leave an identity file.
        Path(name).unlink()
        fd = os.open(
            target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(fd, "wb") as output:
            result = subprocess.run(
                [
                    str(binary),
                    "--decrypt",
                    "--identity",
                    f"/dev/fd/{descriptor}",
                    str(source),
                ],
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.DEVNULL,
                env={"PATH": "/usr/bin:/bin", "LANG": "C"},
                timeout=1800,
                check=False,
                pass_fds=(descriptor,),
            )
        if result.returncode:
            raise ReleaseError("knowledge_decryption_failed")
    except (OSError, subprocess.SubprocessError, ReleaseError) as error:
        target.unlink(missing_ok=True)
        raise ReleaseError("knowledge_decryption_failed") from error
    finally:
        os.close(descriptor)
        Path(name).unlink(missing_ok=True)
