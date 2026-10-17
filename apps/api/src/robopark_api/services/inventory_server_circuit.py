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
    mode: str = "cold"
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
        self._condition = threading.Condition()
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

    def allow(
        self,
        key: tuple[str, str, bytes],
        *,
        wait_for_probe: bool = False,
        wait_timeout: float = 0.0,
    ) -> Admission | None:
        wait_until = time.monotonic() + max(0.0, float(wait_timeout))
        with self._condition:
            while True:
                state = self._states.get(key)
                if state is None:
                    state = _CircuitState(generation=self._generation())
                    self._states[key] = state
                    while len(self._states) > self._max_entries:
                        self._states.popitem(last=False)
                self._states.move_to_end(key)
                if state.mode == "cold":
                    state.mode = "cold_probe"
                    return Admission(state.generation, probe=True)
                if state.mode == "closed":
                    return Admission(state.generation, probe=False)
                if state.mode in {"cold_probe", "half_open"}:
                    remaining = wait_until - time.monotonic()
                    if not wait_for_probe or remaining <= 0:
                        return None
                    self._condition.wait(timeout=remaining)
                    continue
                if self._now() < state.cooldown_until:
                    return None
                state.mode = "half_open"
                return Admission(state.generation, probe=True)

    def unavailable(self, key: tuple[str, str, bytes], admission: Admission) -> None:
        with self._condition:
            state = self._states.get(key)
            if state is None or state.generation != admission.generation:
                return
            expected_modes = {"cold_probe", "half_open"} if admission.probe else {"closed"}
            if state.mode not in expected_modes:
                return
            state.generation = self._generation()
            state.mode = "open"
            state.cooldown_until = self._now() + self._cooldown_seconds
            self._states.move_to_end(key)
            self._condition.notify_all()

    def available(self, key: tuple[str, str, bytes], admission: Admission) -> None:
        with self._condition:
            state = self._states.get(key)
            if (
                state is None
                or not admission.probe
                or state.generation != admission.generation
                or state.mode not in {"cold_probe", "half_open"}
            ):
                return
            state.generation = self._generation()
            state.mode = "closed"
            state.cooldown_until = 0.0
            self._states.move_to_end(key)
            self._condition.notify_all()


inventory_server_circuit = InventoryServerCircuit()
