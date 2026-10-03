"""Storage guard and release capability checks shared by host operations."""

from __future__ import annotations

from pathlib import Path

_LAYOUT_VERSION = b"1\n"
_REQUIRED_RELEASE_FILES = (
    "deploy/host/robopark_host/storage_layout.py",
    "deploy/host/robopark_host/storage_compatibility.py",
    "deploy/host/robopark_host/storage_setup.py",
    "deploy/host/robopark_host/storage_watchdog.py",
)


def require_storage_operations(paths, *, check_space: bool = True):
    """Fail closed before a host operation creates durable state."""

    from .storage_layout import require_storage

    return require_storage(paths.root, check_space=check_space)


def require_storage_release(paths, candidate: Path, *, check_space: bool = True):
    """Require storage-aware host tools only after a layout is explicitly managed."""

    from .release import ReleaseError
    from .storage_layout import StorageError

    try:
        storage = require_storage_operations(paths, check_space=check_space)
    except StorageError as error:
        raise ReleaseError(error.code) from None
    if storage.get("state") == "unmanaged":
        return storage
    root = Path(candidate)
    marker = root / "deploy/storage-layout-version"
    try:
        if root.is_symlink() or not root.is_dir():
            raise ValueError
        if marker.is_symlink() or not marker.is_file() or marker.read_bytes() != _LAYOUT_VERSION:
            raise ValueError
        for relative in _REQUIRED_RELEASE_FILES:
            target = root / relative
            if target.is_symlink() or not target.is_file():
                raise ValueError
    except (OSError, ValueError):
        raise ReleaseError("storage_release_incompatible") from None
    return storage


def require_storage_for_release_operation(paths, *, check_space: bool = True):
    """Translate the standalone guard contract to the host release error contract."""

    from .release import ReleaseError
    from .storage_layout import StorageError

    try:
        return require_storage_operations(paths, check_space=check_space)
    except StorageError as error:
        raise ReleaseError(error.code) from None


def refresh_storage_release_guard(paths, candidate: Path, *, check_space: bool = True):
    """Refresh the independent OS guard from an already validated managed release."""

    storage = require_storage_release(paths, candidate, check_space=check_space)
    if storage.get("state") == "unmanaged":
        return storage
    from .storage_setup import refresh_storage_guard

    refresh_storage_guard(paths.root, Path(candidate))
    return storage
