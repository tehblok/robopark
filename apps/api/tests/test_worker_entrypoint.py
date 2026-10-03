"""The standalone worker must import without the API's router import order."""

import os
import subprocess
import sys
from pathlib import Path


def test_worker_entrypoint_imports_in_fresh_process(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "src"
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(source),
        "DATABASE_URL": "sqlite:///:memory:",
        "DEV_SEED": "false",
    }
    result = subprocess.run(
        [sys.executable, "-c", "import robopark_api.worker"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, result.stderr
