"""Profiles must bound the actual installed/OTA containers, not only the CLI."""

import pytest
from robopark_host.runtime import production_config


@pytest.mark.parametrize("workers", ["2", "4"])
def test_profile_limits_follow_root_worker_setting(host_paths, workers):
    host_paths.etc.mkdir(parents=True)
    (host_paths.etc / "host.env").write_text(
        f"UVICORN_WORKERS='{workers}'\nSECRET_KEY=do-not-export\n"
    )
    document = production_config(
        {
            "services": {
                "api": {"environment": {"UVICORN_WORKERS": "99"}, "build": {}},
                "web": {"build": {}},
            }
        },
        host_paths,
        host_paths.releases / "test",
        "test",
    )
    api, web = document["services"]["api"], document["services"]["web"]
    assert "UVICORN_WORKERS" not in api["environment"]
    assert api["environment"]["DATABASE_URL"] == "postgresql+psycopg://robopark@db:5432/robopark"
    assert api["environment"]["REPORT_ATTACHMENTS_DIR"] == "/data/report-attachments"
    assert api["mem_limit"] == ("8g" if workers == "4" else "2g")
    assert web["mem_limit"] == "256m"
    assert api["pids_limit"] == 512
    for service in (api, web):
        assert service["ulimits"]["nofile"] == {"soft": 65536, "hard": 65536}
    assert "do-not-export" not in str(document)
