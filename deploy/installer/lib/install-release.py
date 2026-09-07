"""Verify with bootstrap code, then atomically install the immutable release."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile


def atomic_bytes(path, data, mode):
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_link(path, target):
    temporary = path.with_name('.' + path.name + '.install')
    if temporary.is_symlink():
        temporary.unlink()
    temporary.symlink_to(target)
    os.replace(temporary, path)


def validate_link(path, releases):
    if path.is_symlink():
        target = path.resolve(strict=True)
        if not target.is_relative_to(releases.resolve()) or not target.is_dir():
            raise ValueError('invalid_existing_link')
    elif path.exists():
        raise ValueError('invalid_existing_link')


def install(root, bundle):
    root, bundle = Path(root), Path(bundle).resolve()
    # Both alternatives belong to the trusted bootstrap distribution/checkout.
    # Never import a module from the ZIP or an extracted application release.
    bootstrap_verifier = bundle / 'verifier'
    if not bootstrap_verifier.is_dir():
        bootstrap_verifier = bundle.parents[1] / 'apps/api/src'
    sys.path.insert(0, str(bootstrap_verifier))
    from robopark_api.services.ops.archives import inspect_archive, unpack_archive, KIND_RELEASE, MAX_ARCHIVE_BYTES

    etc, opt = root / 'etc/robopark', root / 'opt/robopark'
    releases = opt / 'releases'
    for name in ('current', 'previous', 'host-tools'):
        validate_link(opt / name, releases)
    trusted_key = bundle / 'keys/release-public-key.pem'
    if not trusted_key.is_file():
        trusted_key = bundle.parent / 'keys/release-public-key.pem'
    key_data = trusted_key.read_bytes()
    target_key = etc / 'release-public-key.pem'
    if target_key.is_symlink():
        raise ValueError('invalid_key_path')
    if target_key.exists() and target_key.read_bytes() != key_data:
        raise ValueError('key_rotation_requires_signed_update')
    atomic_bytes(target_key, key_data, 0o644)
    payload = bundle / 'payload/robopark-release.zip'
    if payload.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError('archive_too_large')
    data = payload.read_bytes()
    meta = inspect_archive(data, expected_kind=KIND_RELEASE, public_key=target_key.read_bytes())
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,127}', meta.app_version):
        raise ValueError('invalid_release_version')
    target = releases / meta.app_version
    # Only authenticated metadata can cause a release directory to be created.
    releases.mkdir(parents=True, exist_ok=True, mode=0o755)
    if releases.is_symlink() or target.is_symlink():
        raise ValueError('invalid_release_path')
    if target.exists():
        actual_files = {p.relative_to(target).as_posix() for p in target.rglob('*') if p.is_file() or p.is_symlink()}
        if actual_files != set(meta.files):
            raise ValueError('existing_release_mismatch')
        for name, digest in meta.files.items():
            path = target / name
            if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError('existing_release_mismatch')
    else:
        staging = Path(tempfile.mkdtemp(prefix='.install-', dir=releases))
        try:
            unpack_archive(data, staging, expected_kind=KIND_RELEASE, public_key=key_data)
            for path in staging.rglob('*'):
                if path.is_dir():
                    path.chmod(0o755)
                else:
                    executable = path.suffix == '.sh' or path.relative_to(staging).as_posix() == 'deploy/host/robopark'
                    path.chmod(0o755 if executable else 0o644)
            staging.chmod(0o755)
            os.replace(staging, target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    current = opt / 'current'
    if current.is_symlink() and current.resolve() != target.resolve():
        # Re-running an old bootstrap is not an OTA or downgrade authorization.
        raise ValueError('existing_release_requires_updater')
    host_tools = target / 'deploy/host'
    if not host_tools.is_dir():
        raise ValueError('release_host_tools_missing')
    atomic_link(current, target)
    atomic_link(opt / 'host-tools', host_tools)


if __name__ == '__main__':
    try:
        install(*sys.argv[1:])
    except Exception:
        # An untrusted archive must never control operator log text.
        print('Релиз не прошёл проверку или существующая установка несовместима.', file=sys.stderr)
        sys.exit(1)
