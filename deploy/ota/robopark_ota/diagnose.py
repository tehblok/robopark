from __future__ import annotations

import json
import os
import platform
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path


def collect_local_diagnostics(output: Path, *, root: Path = Path("/")) -> Path:
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(root)
    document = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "disk": {"total": usage.total, "used": usage.used, "free": usage.free},
        "installed": os.path.lexists(root / "opt/robopark/current"),
    }
    target = output / "robopark-diagnostics.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "diagnostics.json",
            json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2),
        )
    return target
