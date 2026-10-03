"""Host tools must import with the datetime API available on Python 3.10."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_host_modules_import_with_python310_standard_library():
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(ROOT / "deploy/host"), str(ROOT / "deploy/ota"))
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import datetime\n"
                "del datetime.UTC\n"
                "import enum\n"
                "del enum.StrEnum\n"
                "import robopark_host.retention\n"
                "import robopark_host.scheduled_backup\n"
                "import robopark_host.operation_capabilities\n"
                "import robopark_host.commands\n"
                "import robopark_host.terminal_install\n"
                "import robopark_host.terminal_protocol\n"
                "import robopark_host.terminal_setup\n"
                "import robopark_host.terminal_state\n"
                "import robopark_host.terminal_runtime\n"
                "import robopark_host.terminal_broker\n"
                "import robopark_host.terminal_worker\n"

                "assert str(robopark_host.commands.OperationKind.OTA_UPDATE) == 'ota-update'\n"
            ),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
