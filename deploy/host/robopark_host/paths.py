"""Filesystem paths owned by the host-level Robopark utilities."""

import os
from dataclasses import dataclass
from pathlib import Path

_SYSTEM_ROOT = Path("/")


@dataclass(frozen=True)
class HostPaths:
    """The host-owned layout, kept outside immutable application releases."""

    root: Path
    opt: Path
    etc: Path
    var: Path
    releases: Path
    current: Path
    previous: Path
    recovery: Path
    ops: Path
    state: Path
    lock_dir: Path
    host_lock: Path

    @classmethod
    def from_root(cls, root: Path = _SYSTEM_ROOT) -> "HostPaths":
        root = Path(root)
        if root != _SYSTEM_ROOT and os.environ.get("ROBOPARK_TESTING") != "1":
            raise ValueError("ROBOPARK_ROOT is allowed only when ROBOPARK_TESTING=1")

        opt = root / "opt/robopark"
        var = root / "var/lib/robopark"
        ops = var / "ops"
        return cls(
            root=root,
            opt=opt,
            etc=root / "etc/robopark",
            var=var,
            releases=opt / "releases",
            current=opt / "current",
            previous=opt / "previous",
            recovery=opt / "recovery",
            ops=ops,
            state=ops / "state",
            lock_dir=root / "run/lock/robopark",
            host_lock=root / "run/lock/robopark/host.lock",
        )


def paths_from_environment() -> HostPaths:
    """Return system paths, or the explicitly enabled fake root used by tests."""

    root_override = os.environ.get("ROBOPARK_ROOT")
    if root_override is None:
        return HostPaths.from_root()
    return HostPaths.from_root(Path(root_override))
