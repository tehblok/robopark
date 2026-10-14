import pytest

from robopark_api.services.compute_routing_health import RoutingHealth


def test_failure_of_inventory_does_not_remove_tracker_or_other_devices():
    now = [100.0]
    health = RoutingHealth(now=lambda: now[0])
    health.observe("mac-a", "inventory:rover_card", ok=False, elapsed=3, error="network")
    assert not health.available("mac-a", "inventory:rover_card")
    assert health.available("mac-a", "tracker:issue_read")
    assert health.available("mac-a", "image_resize_v1")
    assert health.available("mac-b", "inventory:rover_card")
    now[0] += 10
    assert health.available("mac-a", "inventory:rover_card")


def test_cooldown_is_bounded_and_success_recovers_without_global_reset():
    now = [0.0]
    health = RoutingHealth(now=lambda: now[0])
    for _ in range(20):
        health.observe("mac", "tracker:issue_read", ok=False, elapsed=2, error="timeout")
        assert health.snapshot("mac")["tracker:issue_read"]["retry_after_seconds"] <= 60
        now[0] += 60
    health.observe("mac", "tracker:issue_read", ok=True, elapsed=0.2, payload_bytes=10000)
    assert health.available("mac", "tracker:issue_read")
    assert health.latency("mac", "tracker:issue_read") == 0.2
    assert health.snapshot("mac")["tracker:issue_read"]["throughput_bytes_per_second"] == 50000


@pytest.mark.parametrize("error", ["auth", "access_denied", "not_found", "rate_limited", "invalid"])
def test_business_errors_are_not_transport_failures(error):
    health = RoutingHealth()
    health.observe("mac", "tracker:issue_read", ok=False, elapsed=0.1, error=error)
    assert health.available("mac", "tracker:issue_read")
    assert health.snapshot("mac") == {}


def test_latency_uses_operation_history_and_bounded_ewma():
    health = RoutingHealth()
    health.observe("mac", "tracker:issue_read", ok=True, elapsed=4)
    health.observe("mac", "tracker:issue_read", ok=True, elapsed=0.4)
    assert health.latency("mac", "tracker:issue_read") == pytest.approx(3.1)
    assert health.latency("mac", "inventory:rover_card") == 1


def test_metrics_do_not_grow_with_departed_devices():
    now = [100.0]
    health = RoutingHealth(now=lambda: now[0], max_entries=3, ttl=60)
    for number in range(100):
        health.observe(f"mac-{number}", "image_resize_v1", ok=True, elapsed=1)
    assert health.snapshot("mac-0") == {}
    assert health.snapshot("mac-99")
    now[0] += 60
    assert health.snapshot("mac-99") == {}
