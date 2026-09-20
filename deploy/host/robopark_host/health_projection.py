"""Sanitized host state explicitly projected into the API bridge."""

from pathlib import Path

from .operational_state import read_object
from .state import atomic_write_json


def update_public_health(path: Path, **sections: dict) -> None:
    current = read_object(path)
    if not isinstance(current, dict):
        current = {}
    current.update(sections)
    atomic_write_json(path, current, mode=0o644)
