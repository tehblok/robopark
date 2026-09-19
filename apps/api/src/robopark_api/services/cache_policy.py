"""Shared bounded-cache policy primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CachePersistence = Literal["memory", "shared"]


@dataclass(frozen=True, slots=True)
class CachePolicy:
    ttl_seconds: float
    stale_seconds: float
    max_entries: int
    max_bytes: int
    persistence: CachePersistence = "memory"

    def __post_init__(self) -> None:
        if self.ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if self.stale_seconds < self.ttl_seconds:
            raise ValueError("stale_seconds must be at least ttl_seconds")
        if self.max_entries <= 0:
            raise ValueError("max_entries must be positive")
        if self.max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if self.persistence not in {"memory", "shared"}:
            raise ValueError("persistence must be memory or shared")
