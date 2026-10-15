#!/usr/bin/env python3
"""Stable TEST-only macOS code signing without modifying user trust settings."""

from __future__ import annotations

import contextlib
import hashlib
import os
import pty
import re
import secrets
import select
import shlex
import signal
import stat
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

IDENTITY_NAME = "Robopark Test Application"
KEY_NAME = "macos-test-signing-key.pem"
CERTIFICATE_NAME = "macos-test-signing-cert.pem"


class TestSigningIdentity(NamedTuple):
    private_key: Path
    certificate: Path
    fingerprint: str
    codesign_hash: str


def _run(command: list[str], *, input_data: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            command,
            input=input_data,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("macos_signing_command_unavailable") from error
    if result.returncode != 0:
        raise RuntimeError("macos_signing_command_failed")
    return result


def _private_regular_file(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError("test_signing_key_requires_private_regular_file")


def _keychain_search_list() -> list[str]:
    raw = _run(["security", "list-keychains", "-d", "user"]).stdout.decode("utf-8", "replace")
    return shlex.split(raw)


def _set_keychain_search_list(paths: list[str]) -> None:
    _run(["security", "list-keychains", "-d", "user", "-s", *paths])


def restored_search_list(*, original: list[str], current: list[str], owned: str) -> list[str]:
    """Restore exactly when uncontested; otherwise remove only our temporary entry."""
    expected = [*original]
    if owned not in expected:
        expected.append(owned)
    if current == expected:
        return original
    return [path for path in current if path != owned]


def _import_pkcs12(keychain: Path, archive: Path, passphrase: str) -> None:
    """Import through one expected PTY prompt so the passphrase never enters argv."""
    pid, master_fd = pty.fork()
    if pid == 0:
        try:
            os.execv(
                "/usr/bin/security",
                [
                    "/usr/bin/security", "import", str(archive), "-k", str(keychain),
                    "-f", "pkcs12", "-T", "/usr/bin/codesign",
                ],
            )
        except OSError:
            os._exit(127)
    deadline = time.monotonic() + 20
    prompted = False
    status: int | None = None
    pending = b""
    try:
        while time.monotonic() < deadline:
            waited_pid, child_status = os.waitpid(pid, os.WNOHANG)
            if waited_pid == pid:
                status = child_status
                break
            readable, _, _ = select.select([master_fd], [], [], 0.2)
            if not readable:
                continue
            try:
                chunk = os.read(master_fd, 1024)
            except OSError:
                chunk = b""
            pending = (pending + chunk.lower())[-2048:]
            if not prompted and (b"password" in pending or b"passphrase" in pending):
                os.write(master_fd, passphrase.encode("utf-8") + b"\n")
                prompted = True
                pending = b""
        if status is None or not prompted or not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0:
            raise RuntimeError("test_signing_identity_import_failed")
    finally:
        os.close(master_fd)
        if status is None:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass


def _certificate_fingerprint(path: Path) -> str:
    der = _run(["openssl", "x509", "-in", str(path), "-outform", "DER"]).stdout
    return hashlib.sha256(der).hexdigest()


def _certificate_codesign_hash(path: Path) -> str:
    der = _run(["openssl", "x509", "-in", str(path), "-outform", "DER"]).stdout
    # Apple exposes the certificate's SHA-1 as the codesign identity selector.
    return hashlib.sha1(der).hexdigest().upper()


def _validate_identity(private_key: Path, certificate: Path) -> TestSigningIdentity:
    _private_regular_file(private_key)
    certificate_info = certificate.lstat()
    if not stat.S_ISREG(certificate_info.st_mode) or certificate_info.st_mode & 0o022:
        raise ValueError("test_signing_certificate_requires_regular_file")
    key_public = _run(["openssl", "pkey", "-in", str(private_key), "-pubout"]).stdout
    certificate_public = _run(
        ["openssl", "x509", "-in", str(certificate), "-pubkey", "-noout"]
    ).stdout
    if key_public != certificate_public:
        raise ValueError("test_signing_identity_mismatch")
    description = _run(
        ["openssl", "x509", "-in", str(certificate), "-noout", "-subject", "-text"]
    ).stdout.decode("utf-8", "replace")
    if not re.search(rf"CN\s*=\s*{re.escape(IDENTITY_NAME)}(?:\s*[,/]|$)", description) or "Code Signing" not in description:
        raise ValueError("invalid_test_signing_certificate")
    _run(["openssl", "x509", "-in", str(certificate), "-checkend", str(30 * 86400), "-noout"])
    return TestSigningIdentity(
        private_key, certificate, _certificate_fingerprint(certificate),
        _certificate_codesign_hash(certificate),
    )


def ensure_test_signing_identity(secret_dir: Path, *, initialize: bool = False, expected_fingerprint: str | None = None) -> TestSigningIdentity:
    """Release builds must reuse the pinned identity; initialization is explicit."""
    if not initialize and not secret_dir.is_dir():
        raise ValueError("test_signing_identity_missing_restore_from_backup")
    secret_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if secret_dir.is_symlink() or secret_dir.stat().st_mode & 0o077:
        raise ValueError("test_signing_directory_requires_private_permissions")
    private_key = secret_dir / KEY_NAME
    certificate = secret_dir / CERTIFICATE_NAME
    if private_key.exists() != certificate.exists():
        raise ValueError("incomplete_test_signing_identity")
    if not private_key.exists():
        if not initialize:
            raise ValueError("test_signing_identity_missing_restore_from_backup")
        old_umask = os.umask(0o077)
        try:
            with tempfile.TemporaryDirectory(prefix="identity-", dir=secret_dir) as raw_temporary:
                temporary = Path(raw_temporary)
                generated_key = temporary / KEY_NAME
                generated_certificate = temporary / CERTIFICATE_NAME
                _run(
                    [
                        "openssl", "req", "-new", "-newkey", "rsa:3072", "-nodes", "-x509",
                        "-days", "3650", "-subj", f"/CN={IDENTITY_NAME}/O=Robopark Test",
                        "-addext", "basicConstraints=critical,CA:FALSE",
                        "-addext", "keyUsage=critical,digitalSignature",
                        "-addext", "extendedKeyUsage=codeSigning",
                        "-keyout", str(generated_key), "-out", str(generated_certificate),
                    ]
                )
                generated_key.chmod(0o600)
                generated_certificate.chmod(0o600)
                _validate_identity(generated_key, generated_certificate)
                os.replace(generated_key, private_key)
                os.replace(generated_certificate, certificate)
        finally:
            os.umask(old_umask)
    identity = _validate_identity(private_key, certificate)
    if expected_fingerprint is not None and identity.fingerprint != expected_fingerprint:
        raise ValueError("test_signing_identity_differs_from_release_pin")
    return identity


class IsolatedTestSigner:
    def __init__(self, identity: TestSigningIdentity, keychain: Path, fingerprint: str):
        self.identity = identity
        self.keychain = keychain
        self.fingerprint = fingerprint

    def sign(self, target: Path, *, identifier: str | None = None) -> None:
        command = [
            "codesign", "--force", "--sign", self.fingerprint, "--keychain", str(self.keychain),
            "--options", "runtime",
        ]
        if identifier is not None:
            if not re.fullmatch(r"[A-Za-z0-9.-]{1,128}", identifier):
                raise ValueError("invalid_code_signing_identifier")
            command += ["--identifier", identifier]
        _run([*command, str(target)])
        _run(["codesign", "--verify", "--strict", str(target)])


@contextlib.contextmanager
def isolated_test_signer(identity: TestSigningIdentity) -> Iterator[IsolatedTestSigner]:
    """Import the persistent key into an isolated, empty-password build Keychain."""
    with tempfile.TemporaryDirectory(prefix="robopark-signing-") as raw_temporary:
        temporary = Path(raw_temporary)
        keychain = temporary / "signing.keychain-db"
        archive = temporary / "identity.p12"
        passphrase = secrets.token_urlsafe(32)
        _run(
            [
                "openssl", "pkcs12", "-export", "-inkey", str(identity.private_key),
                "-in", str(identity.certificate), "-out", str(archive), "-passout", "stdin",
                "-name", IDENTITY_NAME,
            ],
            input_data=(passphrase + "\n").encode("utf-8"),
        )
        archive.chmod(0o600)
        _run(["security", "create-keychain", "-p", "", str(keychain)])
        original_search_list: list[str] | None = None
        try:
            _run(["security", "unlock-keychain", "-p", "", str(keychain)])
            _import_pkcs12(keychain, archive, passphrase)
            original_search_list = _keychain_search_list()
            _set_keychain_search_list([*original_search_list, str(keychain)])
            certificate = _run(
                ["security", "find-certificate", "-a", "-Z", "-c", IDENTITY_NAME, str(keychain)]
            ).stdout.decode("utf-8", "replace")
            if identity.codesign_hash not in certificate:
                raise RuntimeError("test_signing_identity_not_available")
            yield IsolatedTestSigner(identity, keychain, identity.codesign_hash)
        finally:
            search_list_error: Exception | None = None
            if original_search_list is not None:
                try:
                    current = _keychain_search_list()
                    _set_keychain_search_list(
                        restored_search_list(
                            original=original_search_list, current=current, owned=str(keychain)
                        )
                    )
                except (RuntimeError, ValueError) as error:
                    search_list_error = error
            subprocess.run(
                ["security", "delete-keychain", str(keychain)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if search_list_error is not None:
                raise RuntimeError("test_signing_search_list_cleanup_failed") from search_list_error


def designated_requirement(target: Path) -> str:
    result = subprocess.run(
        ["codesign", "--display", "--requirements", "-", str(target)],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError("code_requirement_unavailable")
    match = re.search(r"designated =>(.+)", result.stdout + "\n" + result.stderr)
    if match is None:
        raise RuntimeError("code_requirement_unavailable")
    return match.group(1).strip()


def satisfies_designated_requirement(target: Path, requirement: str) -> bool:
    if not requirement or "\n" in requirement or "\r" in requirement:
        raise ValueError("invalid_designated_requirement")
    result = subprocess.run(
        ["codesign", "--verify", "--strict", "-R", f"={requirement}", str(target)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=15,
    )
    return result.returncode == 0


def create_fixture_keychain(path: Path, *, trusted_application: Path) -> None:
    """Create a test-only Keychain item whose ACL is derived from one signed fixture."""
    _run(["security", "create-keychain", "-p", "", str(path)])
    try:
        _run(["security", "unlock-keychain", "-p", "", str(path)])
        _run(
            [
                "security", "add-generic-password", "-a", "fixture", "-s",
                "ru.robopark.signing-fixture", "-w", "fixture-only-value", "-T",
                str(trusted_application), str(path),
            ]
        )
    except Exception:
        delete_fixture_keychain(path)
        raise


def delete_fixture_keychain(path: Path) -> None:
    subprocess.run(
        ["security", "delete-keychain", str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
