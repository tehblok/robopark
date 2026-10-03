"""Terminal stop happens before ownership; payload mounts never leak to smoke."""

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from robopark_host import terminal_install, updater


def test_ota_quiesces_before_waiting_for_host_lock(host_paths, monkeypatch):
    calls = []
    request = SimpleNamespace(read_artifact=lambda _: b"fixture", job_id="fixture")
    for name in (
        "admission_key",
        "directory_key",
        "verify_directory",
        "check_compatibility",
        "_disk_preflight",
    ):
        monkeypatch.setattr(updater, name, lambda *args: None)
    monkeypatch.setattr(updater, "_release_target", lambda *args: Path("/current"))
    monkeypatch.setattr(updater, "_load_journal", lambda *args: None)
    monkeypatch.setattr(
        updater, "verify_archive", lambda *args: SimpleNamespace(manifest={})
    )
    monkeypatch.setattr(
        terminal_install,
        "quiesce_terminal",
        lambda *args, **kwargs: calls.append("stopped"),
    )

    @contextmanager
    def lock(*args):
        assert calls == ["stopped"]
        raise RuntimeError("reached_ownership_after_stop")
        yield

    monkeypatch.setattr(updater, "exclusive_lock", lock)
    with pytest.raises(RuntimeError, match="reached_ownership_after_stop"):
        updater.apply_release(request, host_paths, object())


def test_terminal_units_are_boot_safe():
    root = Path(__file__).resolve().parents[2] / "deploy/systemd"
    setup = (root / "robopark-terminal-setup.service").read_text()
    assert "RuntimeDirectoryPreserve=yes" in setup
    assert "RuntimeDirectory=robopark-terminal robopark-terminal-workers" in setup
    core = (root / "robopark.service").read_text()
    assert (
        "After=network-online.target docker.service robopark-terminal-setup.service"
        in core
    )
    worker = (root / "robopark-terminal-maintenance@.service").read_text()
    assert "User=robopark-maint" in worker and "CapabilityBoundingSet=\n" in worker
    for name in ("maintenance", "root"):
        content = (root / f"robopark-terminal-{name}@.service").read_text()
        for value in (
            "KillMode=control-group",
            "TimeoutStopSec=5",
            "TasksMax=128",
            "MemoryMax=512M",
            "LimitCORE=0",
        ):
            assert value in content
