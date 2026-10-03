"""Validate the identifier-free host performance bridge."""

from __future__ import annotations

import math
import time

MAX_AGE_SECONDS = 300


def _blank(source_state: str) -> dict:
    return {
        "source_state": source_state,
        "sampled_at": None,
        "cpu": {"online_cores": None, "frequency_mhz": None},
        "pressure": {
            "memory": {"some_avg10": None, "full_avg10": None},
            "io": {"some_avg10": None, "full_avg10": None},
        },
        "temperatures_c": {"cpu": None, "gpu": None, "soc": None, "nvme": None},
        "gpu": {"frequency_mhz": None, "load_percent": None},
        "wifi": {
            "interface_count": None,
            "signal_dbm": None,
            "rx_errors": None,
            "tx_errors": None,
        },
    }


def _number(value, low: float, high: float, *, nullable: bool = True) -> float | None:
    if value is None and nullable:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError("invalid_performance_metric")
    return float(value)


def _integer(value, low: int, high: int, *, nullable: bool = True) -> int | None:
    if value is None and nullable:
        return None
    if type(value) is not int or not low <= value <= high:
        raise ValueError("invalid_performance_metric")
    return value


def _pressure(value) -> dict:
    if not isinstance(value, dict):
        raise ValueError("invalid_performance_metric")
    return {
        "some_avg10": _number(value.get("some_avg10"), 0, 100),
        "full_avg10": _number(value.get("full_avg10"), 0, 100),
    }


def _validated(value: dict, sampled_at: float) -> dict:
    cpu = value.get("cpu")
    pressure = value.get("pressure")
    temperatures = value.get("temperatures_c")
    gpu = value.get("gpu")
    wifi = value.get("wifi")
    if not all(isinstance(item, dict) for item in (cpu, pressure, temperatures, gpu, wifi)):
        raise ValueError("invalid_performance_metric")
    frequency = cpu.get("frequency_mhz")
    if frequency is not None:
        if not isinstance(frequency, dict):
            raise ValueError("invalid_performance_metric")
        frequency = {
            key: _number(frequency.get(key), 0, 1_000_000, nullable=False)
            for key in ("min", "average", "max")
        }
        if not frequency["min"] <= frequency["average"] <= frequency["max"]:
            raise ValueError("invalid_performance_metric")
    result = _blank("reported")
    result.update(
        {
            "sampled_at": sampled_at,
            "cpu": {
                "online_cores": _integer(cpu.get("online_cores"), 1, 4096),
                "frequency_mhz": frequency,
            },
            "pressure": {
                "memory": _pressure(pressure.get("memory")),
                "io": _pressure(pressure.get("io")),
            },
            "temperatures_c": {
                kind: _number(temperatures.get(kind), -100, 250)
                for kind in ("cpu", "gpu", "soc", "nvme")
            },
            "gpu": {
                "frequency_mhz": _number(gpu.get("frequency_mhz"), 0, 1_000_000),
                "load_percent": _number(gpu.get("load_percent"), 0, 100),
            },
            "wifi": {
                "interface_count": _integer(wifi.get("interface_count"), 0, 8, nullable=False),
                "signal_dbm": _number(wifi.get("signal_dbm"), -200, 0),
                "rx_errors": _integer(wifi.get("rx_errors"), 0, 2**63 - 1),
                "tx_errors": _integer(wifi.get("tx_errors"), 0, 2**63 - 1),
            },
        }
    )
    if result["wifi"]["interface_count"] != 1 and result["wifi"]["signal_dbm"] is not None:
        raise ValueError("invalid_performance_metric")
    return result


def performance_snapshot(public_health: dict, *, now: float | None = None) -> dict:
    current = time.time() if now is None else now
    if "performance" not in public_health:
        return _blank("missing")
    value = public_health.get("performance")
    if not isinstance(value, dict):
        return _blank("invalid")
    try:
        sampled_at = _number(value.get("sampled_at"), 0, current + 60, nullable=False)
    except ValueError:
        return _blank("invalid")
    if current - sampled_at > MAX_AGE_SECONDS:
        return _blank("stale")
    try:
        return _validated(value, sampled_at)
    except (TypeError, ValueError):
        return _blank("invalid")
