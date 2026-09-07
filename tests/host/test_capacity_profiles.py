"""Profiles must bound the actual installed/OTA containers, not only the CLI."""

import pytest
from robopark_host.runtime import production_config


@pytest.mark.parametrize("workers,api_memory", [("2", "3g"), ("4", "12g")])
def test_profile_limits_follow_root_worker_setting(host_paths, workers, api_memory):
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
    assert api["environment"]["DATABASE_URL"] == "sqlite:////data/robopark.db"
    assert api["environment"]["REPORT_ATTACHMENTS_DIR"] == "/data/report-attachments"
    assert api["mem_limit"] == api_memory
    assert web["mem_limit"] == "256m"
    assert api["pids_limit"] == 512
    for service in (api, web):
        assert service["ulimits"]["nofile"] == {"soft": 65536, "hard": 65536}
    assert "do-not-export" not in str(document)
