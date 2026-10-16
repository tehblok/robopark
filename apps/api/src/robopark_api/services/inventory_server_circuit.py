"""Small per-route circuit breaker for the server-side Inventory reader."""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class _CircuitState:
    generation: int
    mode: str = "closed"
    cooldown_until: float = 0.0


@dataclass(frozen=True)
class Admission:
    generation: int
    probe: bool


class InventoryServerCircuit:
    """Skip a recently unavailable server route while preserving server-first recovery."""

    def __init__(
        self,
        *,
        cooldown_seconds: float = 15.0,
        max_entries: int = 256,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cooldown_seconds = max(1.0, min(float(cooldown_seconds), 60.0))
        self._max_entries = max(1, int(max_entries))
        self._now = now
        self._lock = threading.Lock()
        self._states: OrderedDict[tuple[str, str, bytes], _CircuitState] = OrderedDict()
        self._next_generation = 1

    @staticmethod
    def key(*, endpoint: str, base_url: str, credential: str) -> tuple[str, str, bytes]:
        # Keep credentials out of process diagnostics and state snapshots.
        fingerprint = hashlib.blake2s(credential.encode("utf-8"), digest_size=16).digest()
        return endpoint, base_url, fingerprint

    def _generation(self) -> int:
        generation = self._next_generation
        self._next_generation += 1
        return generation

    def allow(self, key: tuple[str, str, bytes]) -> Admission | None:
        with self._lock:
            state = self._states.get(key)
            if state is None:
                state = _CircuitState(generation=self._generation())
                self._states[key] = state
                while len(self._states) > self._max_entries:
                    self._states.popitem(last=False)
            self._states.move_to_end(key)
            if state.mode == "closed":
                return Admission(state.generation, probe=False)
            if state.mode == "half_open" or self._now() < state.cooldown_until:
                return None
            state.mode = "half_open"
            return Admission(state.generation, probe=True)

    def unavailable(self, key: tuple[str, str, bytes], admission: Admission) -> None:
        with self._lock:
            state = self._states.get(key)
            if state is None or state.generation != admission.generation:
                return
            expected_mode = "half_open" if admission.probe else "closed"
            if state.mode != expected_mode:
                return
            state.generation = self._generation()
            state.mode = "open"
            state.cooldown_until = self._now() + self._cooldown_seconds
            self._states.move_to_end(key)

    def available(self, key: tuple[str, str, bytes], admission: Admission) -> None:
        with self._lock:
            state = self._states.get(key)
            if (
                state is None
                or not admission.probe
                or state.generation != admission.generation
                or state.mode != "half_open"
            ):
                return
            state.generation = self._generation()
            state.mode = "closed"
            state.cooldown_until = 0.0
            self._states.move_to_end(key)


inventory_server_circuit = InventoryServerCircuit()
