"""Часы «В ремонте»: только рабочее окно 9:00–21:00 (Europe/Moscow).

С 21:00 до 9:00 таймер на паузе — время не накапливается.
"""

from __future__ import annotations

import pandas as pd

WORK_TZ = "Europe/Moscow"
WORK_DAY_START_HOUR = 9
WORK_DAY_END_HOUR = 21


def parse_csv_utc(value):
    """Даты из cleaned_report.csv: naive строки — UTC (см. cleaner.py)."""
    return pd.to_datetime(value, utc=True)


def queue_working_hours(start, end) -> float:
    """Сумма часов между start и end внутри ежедневного окна 9:00–21:00 MSK."""
    if start is None or end is None or pd.isna(start) or pd.isna(end):
        return 0.0

    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    if start_ts.tzinfo is None:
        start_ts = start_ts.tz_localize("UTC")
    else:
        start_ts = start_ts.tz_convert("UTC")
    if end_ts.tzinfo is None:
        end_ts = end_ts.tz_localize("UTC")
    else:
        end_ts = end_ts.tz_convert("UTC")

    start_local = start_ts.tz_convert(WORK_TZ)
    end_local = end_ts.tz_convert(WORK_TZ)
    if end_local <= start_local:
        return 0.0

    total_seconds = 0.0
    day = start_local.normalize()
    last_day = end_local.normalize()
    while day <= last_day:
        window_start = day + pd.Timedelta(hours=WORK_DAY_START_HOUR)
        window_end = day + pd.Timedelta(hours=WORK_DAY_END_HOUR)
        seg_start = max(start_local, window_start)
        seg_end = min(end_local, window_end)
        if seg_end > seg_start:
            total_seconds += (seg_end - seg_start).total_seconds()
        day += pd.Timedelta(days=1)

    return total_seconds / 3600.0


def _uses_idle_timer(row: pd.Series) -> bool:
    """Бывший статус open — таймер из часов простоя (с «Создана»)."""
    val = row.get("Таймер_простоя")
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return False
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("true", "1", "yes")


def idle_hours_since_created(created) -> float:
    """Рабочие часы с «Создана» (9–21 MSK) — для бывших open в «В ремонте»."""
    if created is None or pd.isna(created) or not str(created).strip():
        return 0.0
    start_dt = parse_csv_utc(created)
    now_utc = pd.Timestamp.now(tz="UTC")
    return queue_working_hours(start_dt, now_utc)


def repair_hours_for_row(row: pd.Series, now_utc: pd.Timestamp) -> float:
    """Часы для «В ремонте» / SLA: очередь или простой (ex-open)."""
    if _uses_idle_timer(row):
        return idle_hours_since_created(row.get("Создана"))

    start = row.get("В очереди с")
    if pd.isna(start) or not str(start).strip():
        return 0.0
    end_raw = row.get("В очереди до")
    if pd.isna(end_raw) or not str(end_raw).strip():
        end_dt = now_utc
    else:
        end_dt = parse_csv_utc(end_raw)
    start_dt = parse_csv_utc(start)
    return queue_working_hours(start_dt, end_dt)
