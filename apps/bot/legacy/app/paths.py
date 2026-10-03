"""Project paths.

app/ is on PYTHONPATH; project root holds data/, logs/, venv/, deploy/.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
_configured_data_dir = os.environ.get("ROBOPARK_BOT_DATA_DIR", "").strip()
if _configured_data_dir and not Path(_configured_data_dir).is_absolute():
    raise ValueError("ROBOPARK_BOT_DATA_DIR must be absolute")
DATA_DIR = Path(_configured_data_dir) if _configured_data_dir else ROOT / "data"
LOGS_DIR = DATA_DIR / "logs" if _configured_data_dir else ROOT / "logs"
DEPLOY_DIR = ROOT / "deploy"
SCRIPTS_DIR = ROOT / "scripts"
CONFIG_DIR = ROOT / "config"
