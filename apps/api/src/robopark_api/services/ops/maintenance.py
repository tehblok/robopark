"""Authoritative installed-host writer barrier, independent of API job ownership."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from robopark_api.config import get_settings


class HostMaintenanceActive(RuntimeError):
    def __init__(self):
        super().__init__("maintenance")


def host_marker_active(root: Path) -> bool:
    """Only a definitely absent or valid disabled marker permits writes."""
    target = root / "public/maintenance.json"

    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError()
            value[key] = item
        return value

    try:
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
                return True
            value = json.loads(stream.read(4097), object_pairs_hook=unique)
        if not isinstance(value, dict) or set(value) not in ({"enabled"}, {"enabled", "reason"}):
            return True
        if type(value.get("enabled")) is not bool or value.get("reason", "update") != "update":
            return True
        return value["enabled"]
    except FileNotFoundError:
        # O_NOFOLLOW rejects dangling symlinks; missing parent/mount is unknown.
        return not target.parent.is_dir() or target.parent.is_symlink()
    except (OSError, ValueError, UnicodeError, RecursionError):
        return True


def host_maintenance_active(settings=None) -> bool:
    settings = settings or get_settings()
    if not settings.ops_host_root:
        return False
    from robopark_api.services.ops.host_bridge import BridgeError, host_root

    try:
        return host_marker_active(host_root(settings))
    except (BridgeError, OSError):
        return True


def require_application_writes(settings=None) -> None:
    if host_maintenance_active(settings):
        raise HostMaintenanceActive()
