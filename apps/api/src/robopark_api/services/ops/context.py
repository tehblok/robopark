"""Build an OpsContext from process settings."""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path

from robopark_api.config import _API_ROOT, Settings, get_settings
from robopark_api.db import get_engine
from robopark_api.services.ops.runner import OpsContext
from robopark_api.services.ops.snapshot import sqlite_path_from_url

_REPO_ROOT = _API_ROOT.parent.parent
APP_VERSION = "0.1.29"


def resolved_ops_dir(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    if settings.ops_dir:
        return Path(settings.ops_dir).resolve()
    try:
        db_parent = sqlite_path_from_url(settings.database_url).parent
        return (db_parent / "ops").resolve()
    except Exception:  # noqa: BLE001
        return (_API_ROOT / "data" / "ops").resolve()


def resolved_apply_root(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    if settings.ops_apply_root:
        return Path(settings.ops_apply_root).resolve()
    host_repo = Path("/host-repo")
    if host_repo.is_dir():
        return host_repo
    return _REPO_ROOT


def _config_files(settings: Settings) -> dict[str, Path]:
    files: dict[str, Path] = {}
    candidates = []
    if settings.ops_host_env_path:
        candidates.append(("host.env", Path(settings.ops_host_env_path)))
    candidates.append(("host.env", _REPO_ROOT / "deploy" / "host.env"))
    candidates.append(("env", _API_ROOT / ".env"))
    seen: set[str] = set()
    for name, path in candidates:
        if name in seen:
            continue
        if path.is_file():
            files[name] = path.resolve()
            seen.add(name)
    return files


def _release_public_key(settings: Settings) -> bytes | None:
    try:
        return Path(settings.ops_release_public_key_path).read_bytes()
    except OSError:
        return None


def dispose_db_engines() -> None:
    get_engine().dispose()


def http_health_check(url: str = "http://127.0.0.1:8000/health") -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310
            return 200 <= getattr(response, "status", 0) < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def build_ops_context(settings: Settings | None = None) -> OpsContext:
    settings = settings or get_settings()
    from robopark_api.services.ops.host_bridge import host_root

    host = host_root(settings) if settings.ops_host_root else None
    db_path = sqlite_path_from_url(settings.database_url)
    return OpsContext(
        ops_dir=resolved_ops_dir(settings),
        database_url=settings.database_url,
        config_files=_config_files(settings),
        data_dir=db_path.parent,
        apply_root=resolved_apply_root(settings),
        app_version=APP_VERSION,
        release_public_key=_release_public_key(settings),
        use_host_updater=host is not None,
        host_ops_dir=host,
        before_db_replace=dispose_db_engines,
        health_check=http_health_check,
    )
