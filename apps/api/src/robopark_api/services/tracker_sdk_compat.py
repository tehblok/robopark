"""Narrow compatibility registrations for the pinned Tracker SDK."""

from __future__ import annotations

from typing import Any

_OFFICE_PROBE_PATH = "/v2/offices/__robopark_compat__"


def ensure_offices_collection() -> Any:
    """Register the missing offices reference while preserving native handlers."""
    from yandex_tracker_client import collections

    matched = collections.CollectionMeta.matcher.match(_OFFICE_PROBE_PATH)
    if matched is not None:
        return matched

    class Offices(collections.Collection):
        path = "/{api_version}/offices/{id}"
        fields = {
            "self": None,
            "id": None,
            "display": None,
        }

    return Offices
