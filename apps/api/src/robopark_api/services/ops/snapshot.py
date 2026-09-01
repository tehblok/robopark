"""Build and restore the directory tree that goes into a snapshot ZIP."""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import suppress
from pathlib import Path

from sqlalchemy.engine.url import make_url

SNAPSHOT_DB_REL = Path("data") / "robopark.db"
SNAPSHOT_CONFIG_DIR = Path("config")
SNAPSHOT_DATA_DIR = Path("data")


class SnapshotError(ValueError):
    """Snapshot/restore failed. ``args[0]`` is a stable detail token."""


def sqlite_path_from_url(database_url: str) -> Path:
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite"):
        raise SnapshotError("sqlite_required")
    database = url.database
    if not database or database == ":memory:":
        raise SnapshotError("sqlite_required")
    return Path(database).resolve()


def copy_sqlite(src: Path, dest: Path) -> None:
    """Consistent copy without deleting the live DB's WAL/SHM files."""
    if not src.is_file():
        raise SnapshotError("database_missing")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    try:
        src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    except sqlite3.Error:
        shutil.copy2(src, dest)
        return
    try:
        dest_conn = sqlite3.connect(str(dest))
        try:
            with dest_conn:
                src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    except sqlite3.Error:
        shutil.copy2(src, dest)
    finally:
        src_conn.close()


def _is_under_or_equal(path: Path, root: Path) -> bool:
    try:
        resolved = path.resolve()
        base = root.resolve()
    except OSError:
        return False
    if resolved == base:
        return True
    try:
        return base.is_dir() and resolved.is_relative_to(base)
    except (OSError, ValueError):
        return False


def _copy_regular_file(src: Path, dest: Path) -> None:
    if src.is_symlink() or not src.is_file():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def build_snapshot_tree(
    dest: Path,
    *,
    database_url: str,
    config_files: dict[str, Path],
    data_dir: Path | None = None,
    skip_dirs: list[Path] | None = None,
) -> None:
    dest = dest.resolve()
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    db_path = sqlite_path_from_url(database_url)
    copy_sqlite(db_path, dest / SNAPSHOT_DB_REL)

    for name, path in config_files.items():
        if path.is_file() and not path.is_symlink():
            _copy_regular_file(path, dest / SNAPSHOT_CONFIG_DIR / name)

    skip: list[Path] = [dest, db_path]
    for sidecar in (Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")):
        skip.append(sidecar)
    skip.extend(skip_dirs or [])

    if data_dir is not None and data_dir.is_dir():
        data_root = data_dir.resolve()
        for path in data_root.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if not resolved.is_relative_to(data_root):
                continue
            if any(_is_under_or_equal(path, root) for root in skip):
                continue
            if path.name == "__pycache__" or path.suffix == ".pyc":
                continue
            rel = path.relative_to(data_root)
            _copy_regular_file(path, dest / SNAPSHOT_DATA_DIR / rel)


def restore_snapshot_tree(
    tree: Path,
    *,
    database_path: Path,
    config_targets: dict[str, Path],
    data_dir: Path | None = None,
    preserve_dirs: list[Path] | None = None,
) -> None:
    tree = tree.resolve()
    src_db = tree / SNAPSHOT_DB_REL
    if not src_db.is_file():
        raise SnapshotError("snapshot_incomplete")
    database_path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        stale = Path(str(database_path) + suffix) if suffix else database_path
        if stale.exists() or stale.is_symlink():
            stale.unlink()
    copy_sqlite(src_db, database_path)

    config_dir = tree / SNAPSHOT_CONFIG_DIR
    for name, target in config_targets.items():
        src = config_dir / name
        if src.is_file():
            _copy_regular_file(src, target)

    if data_dir is None:
        return

    data_dir.mkdir(parents=True, exist_ok=True)
    protected: list[Path] = [database_path]
    for sidecar in (Path(str(database_path) + "-wal"), Path(str(database_path) + "-shm")):
        protected.append(sidecar)
    protected.extend(preserve_dirs or [])

    for path in list(data_dir.rglob("*")):
        if not path.is_file() and not path.is_symlink():
            continue
        if any(_is_under_or_equal(path, root) for root in protected):
            continue
        if path.name == "robopark.db":
            continue
        with suppress(OSError):
            path.unlink()

    src_data = tree / SNAPSHOT_DATA_DIR
    if not src_data.is_dir():
        return
    for path in src_data.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        if path.name == "robopark.db":
            continue
        rel = path.relative_to(src_data)
        _copy_regular_file(path, data_dir / rel)
