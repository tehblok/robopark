import time


def _snapshot(sampled_at):
    return {
        "sampled_at": sampled_at,
        "cpu": {"online_cores": 8, "frequency_mhz": {"min": 800, "average": 1200, "max": 1600}},
        "pressure": {
            "memory": {"some_avg10": 1.5, "full_avg10": None},
            "io": {"some_avg10": 2.5, "full_avg10": 0.25},
        },
        "temperatures_c": {"cpu": 52.5, "gpu": None, "soc": 48.0, "nvme": 41.0},
        "gpu": {"frequency_mhz": 612.0, "load_percent": 37.5},
        "wifi": {"interface_count": 1, "signal_dbm": -43.0, "rx_errors": 2, "tx_errors": 3},
        "private_label": "must-not-pass",
    }


def test_projection_whitelists_a_fresh_finite_snapshot():
    from robopark_api.services.host_performance import performance_snapshot

    result = performance_snapshot({"performance": _snapshot(1000)}, now=1100)

    assert result["source_state"] == "reported"
    assert result["sampled_at"] == 1000
    assert result["cpu"]["frequency_mhz"]["average"] == 1200.0
    assert result["wifi"] == {
        "interface_count": 1,
        "signal_dbm": -43.0,
        "rx_errors": 2,
        "tx_errors": 3,
    }
    assert "private_label" not in result


def test_projection_distinguishes_missing_invalid_and_stale_snapshots():
    from robopark_api.services.host_performance import performance_snapshot

    assert performance_snapshot({}, now=1100)["source_state"] == "missing"
    assert (
        performance_snapshot({"performance": _snapshot(700)}, now=1100)["source_state"] == "stale"
    )
    malformed = _snapshot(1000)
    malformed["gpu"] = {"frequency_mhz": float("inf"), "load_percent": 101}
    invalid = performance_snapshot({"performance": malformed}, now=1100)
    assert invalid["source_state"] == "invalid"
    assert invalid["sampled_at"] is None
    assert invalid["gpu"] == {"frequency_mhz": None, "load_percent": None}


def test_operational_snapshot_exposes_validated_performance(tmp_path, monkeypatch):
    from robopark_api.services import operational_health

    now = time.time()
    public = tmp_path / "host-health.json"
    public.write_text(__import__("json").dumps({"performance": _snapshot(now)}))
    operational_health._snapshot_cache.clear()

    result = operational_health.cached_host_snapshot(tmp_path, tmp_path, public)

    assert result["performance"]["source_state"] == "reported"
    assert result["performance"]["temperatures_c"]["cpu"] == 52.5
