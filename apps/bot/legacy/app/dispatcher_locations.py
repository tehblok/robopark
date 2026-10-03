"""Локации диспетчер-бота = store locations → теги Tracker."""

from __future__ import annotations

from dispatcher_roles import ACCESS_GLOBAL, ACCESS_LOCATION, ROLE_MECHANIC, ROLE_OPERATOR

# Seed defaults (also used when store is unavailable).
_DEFAULT_LOCATIONS: dict[str, dict[str, str]] = {
    "Next": {"label": "Next", "tag": "Next", "key": "next"},
    "РОКАЛАБ": {"label": "РОКАЛАБ", "tag": "РОКАЛАБ", "key": "rokalab"},
    "АРГАТЕХКАЗ": {"label": "АРГАТЕХКАЗ", "tag": "АРГАТЕХКАЗ", "key": "argatkaz"},
    "АРГАТЕХНН": {"label": "АРГАТЕХНН", "tag": "АРГАТЕХНН", "key": "argatnn"},
    "Юг": {"label": "Юг", "tag": "Юг", "key": "yug"},
    "Север (Москва)": {"label": "Север", "tag": "МскСевер", "key": "sever"},
    "КалиевАстана": {"label": "Астана", "tag": "КалиевАстана", "key": "astana"},
    "КалиевАлматы": {"label": "Алматы", "tag": "КалиевАлматы", "key": "almaty"},
    "Сигма": {"label": "Сигма", "tag": "Сигма", "key": "sigma"},
    "Континент": {"label": "Континент", "tag": "Континент", "key": "continent"},
    "АрмаМСК": {"label": "Арма", "tag": "АрмаМСК", "key": "arma"},
}


def _live_locations() -> dict[str, dict[str, str]]:
    try:
        from store.locations import LOCATIONS_FILE, dispatcher_locations_map

        # Avoid recursion during first seed (file not written yet).
        if not LOCATIONS_FILE.is_file():
            return dict(_DEFAULT_LOCATIONS)
        mapped = dispatcher_locations_map()
        if mapped:
            return mapped
    except Exception:
        pass
    return dict(_DEFAULT_LOCATIONS)


# Mutable alias kept for existing imports; call refresh or use getter for live data.
DISPATCHER_LOCATIONS: dict[str, dict[str, str]] = _live_locations()

LOCATION_BY_KEY: dict[str, str] = {
    info["key"]: name for name, info in DISPATCHER_LOCATIONS.items()
}


def refresh_dispatcher_locations() -> None:
    global DISPATCHER_LOCATIONS, LOCATION_BY_KEY
    DISPATCHER_LOCATIONS = _live_locations()
    LOCATION_BY_KEY = {info["key"]: name for name, info in DISPATCHER_LOCATIONS.items()}


def profile_for_location(location_name: str) -> tuple[str, str, str, list[str]]:
    """role, access, location label, allowed_tags."""
    info = DISPATCHER_LOCATIONS[location_name]
    return ROLE_MECHANIC, ACCESS_LOCATION, location_name, [info["tag"]]


def profile_for_global() -> tuple[str, str, str, str]:
    return ROLE_OPERATOR, ACCESS_GLOBAL, "Global", "*"
