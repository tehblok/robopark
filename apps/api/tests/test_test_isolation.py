"""Running tests beside a local demo must not clear the demo's shared caches."""

import os
import subprocess
import sys
from pathlib import Path


def test_cache_cleanup_never_opens_the_configured_live_store(tmp_path):
    cache = tmp_path / "demo-cache" / "tracker.issues"
    cache.mkdir(parents=True)
    sentinel = cache / "live-result.json"
    sentinel.write_text('{"payload": "live-demo"}', encoding="utf-8")
    suite = tmp_path / "isolated-suite"
    suite.mkdir()
    (suite / "conftest.py").write_text(
        Path(__file__).with_name("conftest.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (suite / "test_probe.py").write_text("def test_probe():\n    pass\n", encoding="utf-8")
    env = {
        **os.environ,
        "ROBOPARK_LIVE_MERGE": "1",
        "LIVE_MERGE_DIR": str(cache.parent),
        "DATABASE_URL": f"sqlite:///{tmp_path / 'demo.db'}",
    }
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(suite)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert sentinel.exists(), "Test fixtures deleted a result from the configured demo cache"
