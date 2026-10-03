"""Bounded, identifier-free Linux performance observations."""

from __future__ import annotations

import math
import re
import time
from itertools import islice
from pathlib import Path

from .health_projection import update_public_health

_INTERFACE = re.compile(r"[a-zA-Z0-9_.-]{1,32}\Z")


def _read(path: Path, limit: int = 4096) -> str | None:
    try:
        with path.open("r", encoding="ascii", errors="replace") as stream:
            value = stream.read(limit + 1)
        return value if len(value) <= limit else None
    except OSError:
        return None


def _number(path: Path, low: float, high: float) -> float | None:
    raw = _read(path, 64)
    try:
        value = float(raw.strip()) if raw is not None else math.nan
    except ValueError:
        return None
    return value if math.isfinite(value) and low <= value <= high else None


def _children(path: Path, limit: int):
    try:
        yield from islice(path.iterdir(), limit)
    except OSError:
        return


def _online_cores(root: Path) -> int | None:
    raw = _read(root / "sys/devices/system/cpu/online", 4096)
    if raw is None:
        return None
    cores = set()
    try:
        for group in raw.strip().split(","):
            bounds = group.split("-", 1)
            first = int(bounds[0])
            last = int(bounds[-1])
            if first < 0 or last < first or last >= 4096:
                return None
            cores.update(range(first, last + 1))
            if len(cores) > 4096:
                return None
    except ValueError:
        return None
    return len(cores) or None


def _cpu(root: Path) -> dict:
    frequencies = []
    cpu_root = root / "sys/devices/system/cpu"
    for entry in _children(cpu_root, 512):
        if not re.fullmatch(r"cpu[0-9]{1,4}", entry.name):
            continue
        value = _number(entry / "cpufreq/scaling_cur_freq", 1, 10_000_000_000)
        if value is not None:
            frequencies.append(value / 1000)
        if len(frequencies) >= 256:
            break
    frequency = None
    if frequencies:
        frequency = {
            "min": round(min(frequencies), 1),
            "average": round(sum(frequencies) / len(frequencies), 1),
            "max": round(max(frequencies), 1),
        }
    return {"online_cores": _online_cores(root), "frequency_mhz": frequency}


def _pressure_file(path: Path) -> dict:
    result = {"some_avg10": None, "full_avg10": None}
    raw = _read(path, 4096)
    if raw is None:
        return result
    for line in raw.splitlines()[:2]:
        fields = line.split()
        if not fields or fields[0] not in {"some", "full"}:
            continue
        for field in fields[1:]:
            if not field.startswith("avg10="):
                continue
            try:
                value = float(field[6:])
            except ValueError:
                continue
            if math.isfinite(value) and 0 <= value <= 100:
                result[f"{fields[0]}_avg10"] = value
    return result


def _temperature_kind(label: str) -> str | None:
    normalized = label.lower()
    for kind, markers in (
        ("nvme", ("nvme",)),
        ("gpu", ("gpu",)),
        ("cpu", ("cpu",)),
        ("soc", ("soc", "tj-therm")),
    ):
        if any(marker in normalized for marker in markers):
            return kind
    return None


def _temperatures(root: Path) -> dict:
    result = {kind: None for kind in ("cpu", "gpu", "soc", "nvme")}
    sources = []
    for entry in _children(root / "sys/class/thermal", 64):
        if entry.name.startswith("thermal_zone"):
            sources.append((entry / "type", entry / "temp"))
    for entry in _children(root / "sys/class/hwmon", 64):
        if entry.name.startswith("hwmon"):
            sources.append((entry / "name", entry / "temp1_input"))
    for label_path, value_path in sources:
        label = _read(label_path, 128)
        kind = _temperature_kind(label.strip()) if label is not None else None
        value = _number(value_path, -100_000, 250_000)
        if kind is not None and value is not None:
            temperature = round(value / 1000, 1)
            previous = result[kind]
            result[kind] = (
                temperature if previous is None else max(previous, temperature)
            )
    return result


def _gpu(root: Path) -> dict:
    result = {"frequency_mhz": None, "load_percent": None}
    compatible = None
    for path in (
        root / "sys/firmware/devicetree/base/compatible",
        root / "proc/device-tree/compatible",
    ):
        compatible = _read(path, 4096)
        if compatible is not None:
            break
    tegra234 = compatible is not None and "nvidia,tegra234" in compatible.split("\0")
    if tegra234:
        platform = root / "sys/devices/platform/17000000.gpu"
        frequency = _number(
            platform / "devfreq/17000000.gpu/cur_freq", 1, 10_000_000_000
        )
        load = _number(platform / "load", 0, 1000)
        result["frequency_mhz"] = (
            round(frequency / 1_000_000, 1) if frequency is not None else None
        )
        result["load_percent"] = round(load / 10, 1) if load is not None else None
        if frequency is not None:
            return result
    for entry in _children(root / "sys/class/devfreq", 64):
        label = _read(entry / "name", 128)
        identity = (label or entry.name).strip().lower()
        if "gpu" not in identity:
            continue
        frequency = _number(entry / "cur_freq", 1, 10_000_000_000)
        result["frequency_mhz"] = round(frequency / 1_000_000, 1) if frequency else None
        break
    return result


def _wireless_rows(root: Path) -> list[tuple[str, float | None]]:
    raw = _read(root / "proc/net/wireless", 8192)
    rows = []
    for line in (raw or "").splitlines()[2:34]:
        if ":" not in line:
            continue
        name, values = line.split(":", 1)
        name = name.strip()
        if not _INTERFACE.fullmatch(name):
            continue
        fields = values.split()
        try:
            signal = float(fields[2].rstrip("."))
        except (IndexError, ValueError):
            signal = None
        if signal is not None and (
            not math.isfinite(signal) or not -200 <= signal <= 0
        ):
            signal = None
        rows.append((name, signal))
        if len(rows) >= 8:
            break
    return rows


def _wifi(root: Path) -> dict:
    rows = _wireless_rows(root)
    if not rows:
        for entry in _children(root / "sys/class/net", 64):
            if _INTERFACE.fullmatch(entry.name) and (
                (entry / "wireless").exists() or (entry / "phy80211").exists()
            ):
                rows.append((entry.name, None))
                if len(rows) >= 8:
                    break
    counters = {"rx_errors": [], "tx_errors": []}
    for name, _signal in rows:
        for key, values in counters.items():
            value = _number(
                root / "sys/class/net" / name / "statistics" / key, 0, 2**63 - 1
            )
            if value is not None:
                values.append(int(value))
    return {
        "interface_count": len(rows),
        "signal_dbm": rows[0][1] if len(rows) == 1 else None,
        "rx_errors": (
            sum(counters["rx_errors"])
            if rows and len(counters["rx_errors"]) == len(rows)
            else None
        ),
        "tx_errors": (
            sum(counters["tx_errors"])
            if rows and len(counters["tx_errors"]) == len(rows)
            else None
        ),
    }


def collect_performance(root: Path = Path("/"), *, now: float | None = None) -> dict:
    """Read a fixed, bounded set of procfs/sysfs metrics without subprocesses."""

    root = Path(root)
    return {
        "sampled_at": time.time() if now is None else now,
        "cpu": _cpu(root),
        "pressure": {
            "memory": _pressure_file(root / "proc/pressure/memory"),
            "io": _pressure_file(root / "proc/pressure/io"),
        },
        "temperatures_c": _temperatures(root),
        "gpu": _gpu(root),
        "wifi": _wifi(root),
    }


def publish_performance(paths) -> None:
    update_public_health(
        paths.var / "api-ops/host-health.json",
        performance=collect_performance(paths.root),
    )
