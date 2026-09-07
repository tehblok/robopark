"""Verify with bootstrap code, then atomically install the immutable release."""
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import types
import zipfile
from pathlib import Path


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def sync_tree(root):
    # A durable link must never point at an unflushed release tree.
    entries = list(root.rglob('*'))
    for path in entries:
        if path.is_file():
            with path.open('rb') as stream:
                os.fsync(stream.fileno())
    for path in sorted((p for p in entries if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        sync_directory(path)
    sync_directory(root)


def atomic_bytes(path, data, mode):
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_link(path, target):
    temporary = path.with_name('.' + path.name + '.install')
    if temporary.is_symlink():
        temporary.unlink()
    temporary.symlink_to(target)
    os.replace(temporary, path)
    sync_directory(path.parent)


def validate_link(path, releases):
    if path.is_symlink():
        target = path.resolve(strict=True)
        if not target.is_relative_to(releases.resolve()) or not target.is_dir():
            raise ValueError('invalid_existing_link')
    elif path.exists():
        raise ValueError('invalid_existing_link')


def load_bootstrap_verifier(bundle):
    bootstrap = bundle / 'verifier'
    if not bootstrap.is_dir():
        bootstrap = bundle.parents[1] / 'apps/api/src'
    module_root = bootstrap / 'robopark_api/services/ops'
    # An earlier namespace portion does not outrank a later regular package.
    # Replace this namespace completely, including any previously loaded modules,
    # then execute exactly the two trusted files without package __init__ hooks.
    for name in list(sys.modules):
        if name == 'robopark_api' or name.startswith('robopark_api.'):
            del sys.modules[name]
    for name in ('robopark_api', 'robopark_api.services', 'robopark_api.services.ops'):
        package = types.ModuleType(name)
        package.__package__ = name
        package.__path__ = []
        sys.modules[name] = package
    for leaf in ('archives', 'release_signing'):
        source = module_root / (leaf + '.py')
        if not source.is_file() or source.is_symlink():
            raise ValueError('bootstrap_verifier_missing')
        name = 'robopark_api.services.ops.' + leaf
        spec = importlib.util.spec_from_file_location(name, source)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules['robopark_api.services.ops.archives']


def historical_resume(root, target, key_data, metadata):
    """Root's exact retained pin permits revalidation, never new admission.

    Do not import installed host code to obtain this authority. The bootstrap
    verifier has already authenticated the bundled manifest; validate its exact
    identity against the private host record before inspecting installed files.
    """
    state = root / 'var/lib/robopark/ops/state/signing-trust.json'
    if not state.exists() and not state.is_symlink():
        return False

    def unique(pairs):
        value = {}
        for name, item in pairs:
            if name in value:
                raise ValueError('invalid_trust_state')
            value[name] = item
        return value

    descriptor = os.open(state, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or info.st_size > 8 * 1024 * 1024
        ):
            raise ValueError('invalid_trust_state')
        raw = stream.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError('invalid_trust_state')
    value = json.loads(raw, object_pairs_hook=unique)
    if (
        not isinstance(value, dict)
        or set(value) != {'format', 'job_id', 'active_key', 'pins', 'certificate'}
        or type(value['format']) is not int
        or value['format'] != 1
    ):
        raise ValueError('invalid_trust_state')
    pins = value['pins']
    if not isinstance(pins, dict) or len(pins) > 2:
        raise ValueError('invalid_trust_state')
    current = root / 'opt/robopark/current'
    if (
        not current.is_symlink()
        or current.resolve(strict=True) != target.resolve(strict=True)
        or target.is_symlink()
    ):
        raise ValueError('existing_release_requires_updater')
    pin = pins.get(target.name)
    if (
        not isinstance(pin, dict)
        or set(pin) != {'key', 'sha256'}
        or pin['key'] != key_data.decode('ascii')
        or pin['sha256'] != hashlib.sha256(metadata['manifest.json']).hexdigest()
    ):
        raise ValueError('existing_release_mismatch')
    # The file-bound identity check below also checks every payload hash and
    # signature byte. Neither an old-key future release nor a replacement bridge
    # may be unpacked via this path, even if the PEM projection was interrupted.
    return True


def install(root, bundle):
    root, bundle = Path(root), Path(bundle).resolve()
    verifier = load_bootstrap_verifier(bundle)
    inspect_archive, unpack_archive = verifier.inspect_archive, verifier.unpack_archive
    KIND_RELEASE, MAX_ARCHIVE_BYTES = verifier.KIND_RELEASE, verifier.MAX_ARCHIVE_BYTES

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
    trust_state = root / 'var/lib/robopark/ops/state/signing-trust.json'
    if not trust_state.exists() and not trust_state.is_symlink():
        if target_key.exists() and target_key.read_bytes() != key_data:
            raise ValueError('key_rotation_requires_signed_update')
        # Preserve initial trusted-key provisioning, even if payload verification
        # fails. Once a transition record exists, its authority is never rewritten.
        atomic_bytes(target_key, key_data, 0o644)
    payload = bundle / 'payload/robopark-release.zip'
    if payload.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError('archive_too_large')
    data = payload.read_bytes()
    meta = inspect_archive(data, expected_kind=KIND_RELEASE, public_key=key_data)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,127}', meta.app_version):
        raise ValueError('invalid_release_version')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        authenticated_metadata = {name: archive.read(name) for name in ('manifest.json', 'manifest.sig')}
    target = releases / meta.app_version
    resumed_trust = historical_resume(root, target, key_data, authenticated_metadata)
    if not resumed_trust and target_key.exists() and target_key.read_bytes() != key_data:
        raise ValueError('key_rotation_requires_signed_update')
    current = opt / 'current'
    if current.is_symlink() and current.resolve() != target.resolve():
        raise ValueError('existing_release_requires_updater')
    # Only authenticated metadata can cause a release directory to be created.
    releases.mkdir(parents=True, exist_ok=True, mode=0o755)
    if releases.is_symlink() or target.is_symlink():
        raise ValueError('invalid_release_path')
    if target.exists():
        actual_files = {p.relative_to(target).as_posix() for p in target.rglob('*') if p.is_file() or p.is_symlink()}
        if actual_files != set(meta.files) | set(authenticated_metadata):
            raise ValueError('existing_release_mismatch')
        for name, expected in authenticated_metadata.items():
            path = target / name
            if path.is_symlink() or path.read_bytes() != expected:
                raise ValueError('existing_release_mismatch')
        for name, digest in meta.files.items():
            path = target / name
            if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError('existing_release_mismatch')
    else:
        staging = Path(tempfile.mkdtemp(prefix='.install-', dir=releases))
        try:
            unpack_archive(data, staging, expected_kind=KIND_RELEASE, public_key=key_data)
            for name, content in authenticated_metadata.items():
                atomic_bytes(staging / name, content, 0o644)
            for path in staging.rglob('*'):
                if path.is_dir():
                    path.chmod(0o755)
                else:
                    executable = path.suffix == '.sh' or path.relative_to(staging).as_posix() == 'deploy/host/robopark'
                    path.chmod(0o755 if executable else 0o644)
            staging.chmod(0o755)
            sync_tree(staging)
            os.replace(staging, target)
            sync_directory(releases)
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
    # A historical resume leaves authority/projections untouched. install-trust
    # finishes any interrupted projection after the usual local readiness gate.
    atomic_link(current, target)
    atomic_link(opt / 'host-tools', host_tools)


if __name__ == '__main__':
    try:
        install(*sys.argv[1:])
    except Exception:
        # An untrusted archive must never control operator log text.
        print('Релиз не прошёл проверку или существующая установка несовместима.', file=sys.stderr)
        sys.exit(1)
