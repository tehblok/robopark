"""Activate optional host policy without inheriting the OTA process sandbox."""
from pathlib import Path

from .release import ReleaseError

RESILIENCE_UNITS = (
    "robopark-resilience-setup.service",
    "robopark-network-recovery.service",
    "robopark-network-recovery.timer",
)
PAYLOAD = tuple("deploy/systemd/" + name for name in RESILIENCE_UNITS) + (
    "deploy/host/robopark-host-resilience",
    "deploy/host/robopark_host/host_resilience.py",
    "deploy/host/robopark_host/network_recovery.py",
)


def resilience_payload_present(release: Path) -> bool:
    sources = [release / relative for relative in PAYLOAD]
    if not any(path.exists() or path.is_symlink() for path in sources):
        return False
    if any(path.is_symlink() or not path.is_file() for path in sources):
        raise ReleaseError("resilience_payload_invalid")
    return True


def reconcile_host_resilience(release: Path, runner) -> bool:
    if not resilience_payload_present(release):
        return False
    runner.run(["systemctl", "start", "robopark-resilience-setup.service"], timeout=180)
    return True
