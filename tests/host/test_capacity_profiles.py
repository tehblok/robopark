"""Profiles must bound the actual installed/OTA containers, not only the CLI."""

import pytest
from robopark_host.runtime import production_config


@pytest.mark.parametrize("workers", ["2", "4"])
def test_profile_limits_follow_root_worker_setting(host_paths, workers):
    host_paths.etc.mkdir(parents=True)
    proc = host_paths.root / "proc"
    proc.mkdir()
    memory_kib = 32 * 1024 * 1024 if workers == "4" else 8 * 1024 * 1024
    cpu_count = 12 if workers == "4" else 4
    (proc / "meminfo").write_text(f"MemTotal: {memory_kib} kB\n")
    (proc / "cpuinfo").write_text("processor : 0\n" * cpu_count)
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


def test_large_profile_is_rejected_when_real_cpu_probe_has_fewer_than_eight_cores(
    host_paths,
):
    host_paths.etc.mkdir(parents=True)
    (host_paths.etc / "host.env").write_text("UVICORN_WORKERS=4\n")
    proc = host_paths.root / "proc"
    proc.mkdir()
    (proc / "meminfo").write_text(f"MemTotal: {32 * 1024 * 1024} kB\n")
    (proc / "cpuinfo").write_text("processor : 0\n" * 4)

    with pytest.raises(ValueError, match="invalid_host_profile"):
        production_config(
            {"services": {"api": {"environment": {}, "build": {}}, "web": {"build": {}}}},
            host_paths,
            host_paths.releases / "test",
            "test",
        )
