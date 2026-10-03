"""A fresh installation should not send inherited bot templates automatically."""

from __future__ import annotations

import importlib
from pathlib import Path


APP = Path(__file__).resolve().parents[2] / "apps/bot/legacy/app"


def test_builtin_broadcast_templates_require_explicit_enable(monkeypatch):
    monkeypatch.syspath_prepend(str(APP))
    broadcasts = importlib.import_module("store.broadcasts")

    rows = [broadcasts._new_builtin_row(spec) for spec in broadcasts.BUILTIN_SPECS]

    assert rows
    assert all(row["enabled"] is False for row in rows)
