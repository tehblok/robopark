from __future__ import annotations

import importlib.util
from pathlib import Path


ZOOM_JOBS = Path(__file__).resolve().parents[2] / "apps/bot/legacy/app/store/zoom_jobs.py"


def test_default_meetings_do_not_embed_private_links_or_send_without_configuration():
    specification = importlib.util.spec_from_file_location("bot_zoom_defaults", ZOOM_JOBS)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)

    assert module.DEFAULT_ZOOM_JOBS
    assert all(job["link"] == "" and job["enabled"] is False for job in module.DEFAULT_ZOOM_JOBS)
