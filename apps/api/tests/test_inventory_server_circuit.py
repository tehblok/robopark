from concurrent.futures import ThreadPoolExecutor
from threading import Event

from robopark_api.services.inventory_server_circuit import InventoryServerCircuit


def test_cold_start_allows_only_one_of_200_parallel_probes_then_closes():
    circuit = InventoryServerCircuit()
    key = circuit.key(endpoint="rover_card", base_url="https://inventory", credential="token")

    with ThreadPoolExecutor(max_workers=32) as workers:
        decisions = list(workers.map(lambda _index: circuit.allow(key), range(200)))

    assert sum(decision is not None for decision in decisions) == 1
    probe = next(decision for decision in decisions if decision is not None)
    assert probe.probe is True
    circuit.available(key, probe)

    with ThreadPoolExecutor(max_workers=32) as workers:
        healthy = list(workers.map(lambda _index: circuit.allow(key), range(200)))
    assert all(admission is not None and not admission.probe for admission in healthy)


def test_waiting_admission_continues_after_cold_probe_succeeds():
    circuit = InventoryServerCircuit()
    key = circuit.key(endpoint="rover_card", base_url="https://inventory", credential="token")
    probe = circuit.allow(key)
    waiting = Event()

    def allow_after_probe():
        waiting.set()
        return circuit.allow(key, wait_for_probe=True, wait_timeout=1)

    with ThreadPoolExecutor(max_workers=1) as workers:
        future = workers.submit(allow_after_probe)
        assert waiting.wait(timeout=1)
        circuit.available(key, probe)
        admission = future.result(timeout=1)

    assert admission is not None
    assert admission.probe is False


def test_half_open_allows_only_one_parallel_probe_and_success_closes_circuit():
    now = [100.0]
    circuit = InventoryServerCircuit(cooldown_seconds=10, now=lambda: now[0])
    key = circuit.key(endpoint="rover_card", base_url="https://inventory", credential="token")
    initial = circuit.allow(key)
    assert initial is not None
    circuit.unavailable(key, initial)
    assert circuit.allow(key) is None

    now[0] += 10
    with ThreadPoolExecutor(max_workers=16) as workers:
        decisions = list(workers.map(lambda _index: circuit.allow(key), range(32)))

    assert sum(decision is not None for decision in decisions) == 1
    probe = next(decision for decision in decisions if decision is not None)
    circuit.available(key, probe)
    assert circuit.allow(key) is not None


def test_circuit_isolated_by_credential_and_base_url_configuration():
    circuit = InventoryServerCircuit()
    old_key = circuit.key(
        endpoint="rover_card", base_url="https://inventory", credential="old-token"
    )
    new_key = circuit.key(
        endpoint="rover_card", base_url="https://inventory", credential="new-token"
    )
    moved_key = circuit.key(
        endpoint="rover_card", base_url="https://inventory-new", credential="old-token"
    )
    initial = circuit.allow(old_key)
    assert initial is not None
    circuit.unavailable(old_key, initial)

    assert circuit.allow(old_key) is None
    assert circuit.allow(new_key) is not None
    assert circuit.allow(moved_key) is not None


def test_stale_success_cannot_close_cooldown_opened_by_parallel_request():
    now = [100.0]
    circuit = InventoryServerCircuit(cooldown_seconds=10, now=lambda: now[0])
    key = circuit.key(endpoint="rover_card", base_url="https://inventory", credential="token")
    cold_probe = circuit.allow(key)
    assert cold_probe is not None
    circuit.available(key, cold_probe)
    failing = circuit.allow(key)
    stale_success = circuit.allow(key)
    assert failing is not None and stale_success is not None

    circuit.unavailable(key, failing)
    circuit.available(key, stale_success)

    assert circuit.allow(key) is None


def test_stale_failure_cannot_reopen_after_half_open_probe_recovers():
    now = [100.0]
    circuit = InventoryServerCircuit(cooldown_seconds=10, now=lambda: now[0])
    key = circuit.key(endpoint="rover_card", base_url="https://inventory", credential="token")
    cold_probe = circuit.allow(key)
    assert cold_probe is not None
    circuit.available(key, cold_probe)
    failing = circuit.allow(key)
    stale_failure = circuit.allow(key)
    assert failing is not None and stale_failure is not None
    circuit.unavailable(key, failing)

    now[0] += 10
    probe = circuit.allow(key)
    assert probe is not None
    circuit.available(key, probe)
    circuit.unavailable(key, stale_failure)

    assert circuit.allow(key) is not None
    assert circuit.allow(key) is not None
