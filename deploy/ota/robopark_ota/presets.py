"""Authenticate the encrypted owner preset before changing the host.

Decryption is performed by the API container. The stdlib-only OTA bootstrap
checks the Fernet HMAC so a mistyped key fails before package installation.
"""

from __future__ import annotations

import base64
import binascii
import getpass
import hashlib
import hmac
import os
import tempfile
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

PRESET_MEMBER = "release/deploy/support/robopark-install-preset.enc"
MAX_PRESET_BYTES = 1024 * 1024


def _authenticate(bundle: Path, key: str) -> bytes:
    try:
        with zipfile.ZipFile(bundle) as archive:
            member = archive.getinfo(PRESET_MEMBER)
            if not 0 < member.file_size <= MAX_PRESET_BYTES:
                raise ValueError("preset_unavailable")
            cipher = archive.read(member).strip()
    except (OSError, KeyError, zipfile.BadZipFile) as error:
        raise ValueError("preset_unavailable") from error
    try:
        encoded_key = key.encode("ascii")
        signing_key = base64.b64decode(encoded_key, altchars=b"-_", validate=True)
        token = base64.b64decode(cipher, altchars=b"-_", validate=True)
        if len(signing_key) != 32 or len(token) < 73 or token[0] != 0x80:
            raise ValueError("preset_key_invalid")
        signature = hmac.new(signing_key[:16], token[:-32], hashlib.sha256).digest()
        if not hmac.compare_digest(signature, token[-32:]):
            raise ValueError("preset_key_invalid")
    except (UnicodeError, binascii.Error) as error:
        raise ValueError("preset_key_invalid") from error
    return encoded_key


@contextmanager
def preset_key_file(
    bundle: Path,
    directory: Path,
    *,
    getpass_fn: Callable[[str], str] = getpass.getpass,
) -> Iterator[Path]:
    key = _authenticate(
        bundle, getpass_fn("Ключ зашифрованных настроек Robopark: ").strip()
    )
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink():
        raise ValueError("unsafe_credential_directory")
    directory.chmod(0o700)
    descriptor, filename = tempfile.mkstemp(prefix="preset-", dir=directory)
    path = Path(filename)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(key)
            stream.flush()
            os.fsync(stream.fileno())
        yield path
    finally:
        path.unlink(missing_ok=True)
