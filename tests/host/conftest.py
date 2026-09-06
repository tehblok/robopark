from pathlib import Path

import pytest


@pytest.fixture
def host_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("ROBOPARK_ROOT", str(tmp_path))
    monkeypatch.setenv("ROBOPARK_TESTING", "1")

    from robopark_host.paths import paths_from_environment

    return paths_from_environment()
