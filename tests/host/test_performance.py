import json


def _write(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)


def test_collector_reads_bounded_linux_metrics_without_publishing_identifiers(tmp_path):
    from robopark_host.performance import collect_performance

    _write(tmp_path, "sys/devices/system/cpu/online", "0-1\n")
    _write(
        tmp_path, "sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq", "1200000\n"
    )
    _write(
        tmp_path, "sys/devices/system/cpu/cpu1/cpufreq/scaling_cur_freq", "1800000\n"
    )
    _write(
        tmp_path,
        "proc/pressure/memory",
        "some avg10=1.25 avg60=0.00 avg300=0.00 total=1\nfull avg10=0.50 avg60=0.00 avg300=0.00 total=1\n",
    )
    _write(
        tmp_path,
        "proc/pressure/io",
        "some avg10=2.50 avg60=0.00 avg300=0.00 total=1\nfull avg10=0.25 avg60=0.00 avg300=0.00 total=1\n",
    )
    _write(tmp_path, "sys/class/thermal/thermal_zone0/type", "CPU-therm\n")
    _write(tmp_path, "sys/class/thermal/thermal_zone0/temp", "52500\n")
    _write(
        tmp_path,
        "sys/firmware/devicetree/base/compatible",
        "nvidia,tegra234\0vendor,board\0",
    )
    _write(
        tmp_path,
        "sys/devices/platform/17000000.gpu/devfreq/17000000.gpu/cur_freq",
        "612000000\n",
    )
    _write(tmp_path, "sys/devices/platform/17000000.gpu/load", "375\n")
    _write(
        tmp_path,
        "proc/net/wireless",
        "Inter-| sta\n face | data\n secret0: 0000 70.  -43.  -256\n",
    )
    _write(tmp_path, "sys/class/net/secret0/wireless/protocol", "wifi\n")
    _write(tmp_path, "sys/class/net/secret0/statistics/rx_errors", "2\n")
    _write(tmp_path, "sys/class/net/secret0/statistics/tx_errors", "3\n")

    result = collect_performance(tmp_path, now=1000.0)

    assert result == {
        "sampled_at": 1000.0,
        "cpu": {
            "online_cores": 2,
            "frequency_mhz": {"min": 1200.0, "average": 1500.0, "max": 1800.0},
        },
        "pressure": {
            "memory": {"some_avg10": 1.25, "full_avg10": 0.5},
            "io": {"some_avg10": 2.5, "full_avg10": 0.25},
        },
        "temperatures_c": {"cpu": 52.5, "gpu": None, "soc": None, "nvme": None},
        "gpu": {"frequency_mhz": 612.0, "load_percent": 37.5},
        "wifi": {
            "interface_count": 1,
            "signal_dbm": -43.0,
            "rx_errors": 2,
            "tx_errors": 3,
        },
    }
    serialized = json.dumps(result)
    assert "secret0" not in serialized
    assert "CPU-therm" not in serialized


def test_collector_keeps_malformed_or_ambiguous_readings_unknown(tmp_path):
    from robopark_host.performance import collect_performance

    _write(tmp_path, "sys/devices/system/cpu/online", "not-a-range\n")
    _write(tmp_path, "proc/pressure/memory", "some avg10=nan\n")
    _write(
        tmp_path,
        "proc/net/wireless",
        "Inter-| sta\n face | data\n wlan0: 0000 70. -40. -256\nwlan1: 0000 70. -60. -256\n",
    )
    for name in ("wlan0", "wlan1"):
        _write(tmp_path, f"sys/class/net/{name}/wireless/protocol", "wifi\n")

    result = collect_performance(tmp_path, now=1000.0)

    assert result["cpu"] == {"online_cores": None, "frequency_mhz": None}
    assert result["pressure"]["memory"] == {"some_avg10": None, "full_avg10": None}
    assert result["wifi"]["interface_count"] == 2
    assert result["wifi"]["signal_dbm"] is None
    assert result["wifi"]["rx_errors"] is None
    assert result["wifi"]["tx_errors"] is None


def test_collector_maps_nvme_hwmon_temperature_to_a_sanitized_kind(tmp_path):
    from robopark_host.performance import collect_performance

    _write(tmp_path, "sys/class/hwmon/hwmon0/name", "nvme\n")
    _write(tmp_path, "sys/class/hwmon/hwmon0/temp1_input", "41750\n")

    result = collect_performance(tmp_path, now=1000.0)

    assert result["temperatures_c"]["nvme"] == 41.8
    assert "hwmon0" not in json.dumps(result)


def test_collector_does_not_guess_unverified_gpu_load_or_partial_wifi_counters(
    tmp_path,
):
    from robopark_host.performance import collect_performance

    _write(tmp_path, "sys/class/devfreq/gpu/name", "generic-gpu\n")
    _write(tmp_path, "sys/class/devfreq/gpu/cur_freq", "500000000\n")
    _write(tmp_path, "sys/class/devfreq/gpu/load", "500\n")
    _write(
        tmp_path,
        "proc/net/wireless",
        "Inter-| sta\n face | data\n wlan0: 0000 70. -40. -256\nwlan1: 0000 70. -60. -256\n",
    )
    for name in ("wlan0", "wlan1"):
        _write(tmp_path, f"sys/class/net/{name}/wireless/protocol", "wifi\n")
    _write(tmp_path, "sys/class/net/wlan0/statistics/rx_errors", "2\n")
    _write(tmp_path, "sys/class/net/wlan0/statistics/tx_errors", "3\n")

    result = collect_performance(tmp_path, now=1000.0)

    assert result["gpu"] == {"frequency_mhz": 500.0, "load_percent": None}
    assert result["wifi"]["rx_errors"] is None
    assert result["wifi"]["tx_errors"] is None


def test_collector_does_not_treat_numeric_generic_devfreq_name_as_nvidia_load(tmp_path):
    from robopark_host.performance import collect_performance

    _write(tmp_path, "sys/class/devfreq/17000000.gpu/name", "17000000.gpu\n")
    _write(tmp_path, "sys/class/devfreq/17000000.gpu/cur_freq", "500000000\n")
    _write(tmp_path, "sys/class/devfreq/17000000.gpu/load", "500\n")

    result = collect_performance(tmp_path, now=1000.0)

    assert result["gpu"] == {"frequency_mhz": 500.0, "load_percent": None}


def test_collector_treats_jetson_junction_temperature_as_soc(tmp_path):
    from robopark_host.performance import collect_performance

    _write(tmp_path, "sys/class/thermal/thermal_zone0/type", "tj-therm\n")
    _write(tmp_path, "sys/class/thermal/thermal_zone0/temp", "62500\n")

    result = collect_performance(tmp_path, now=1000.0)

    assert result["temperatures_c"]["cpu"] is None
    assert result["temperatures_c"]["soc"] == 62.5
