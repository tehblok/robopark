from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def synthetic_host_disk(monkeypatch):
    """Unit hosts have a deterministic disk; pressure tests override this probe."""
    import shutil
    from collections import namedtuple

    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: usage(100 * 1024**3, 20 * 1024**3, 80 * 1024**3))


@pytest.fixture
def host_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("ROBOPARK_ROOT", str(tmp_path))
    monkeypatch.setenv("ROBOPARK_TESTING", "1")

    from robopark_host.paths import paths_from_environment

    return paths_from_environment()
