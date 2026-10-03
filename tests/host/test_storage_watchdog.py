from pathlib import Path

import pytest


def test_healthy_or_unmanaged_storage_never_stops_containers(monkeypatch):
    from robopark_host import storage_watchdog as watchdog

    commands = []
    monkeypatch.setattr(
        watchdog, "require_storage", lambda _root, **_kw: {"state": "unmanaged"}
    )
    monkeypatch.setattr(watchdog, "_run", lambda argv: commands.append(argv))
    assert watchdog.check_and_stop(Path("/"))["state"] == "unmanaged"
    assert commands == []


def test_failed_storage_stops_containers_without_loading_nvme_compose(monkeypatch):
    from robopark_host import storage_watchdog as watchdog
    from robopark_host.storage_layout import StorageError

    commands = []
    monkeypatch.setattr(
        watchdog,
        "require_storage",
        lambda _root, **_kw: (_ for _ in ()).throw(
            StorageError("storage_mount_missing")
        ),
    )

    def run(argv):
        commands.append(argv)
        return "a" * 64 + "\n" if argv[:3] == ["docker", "ps", "-q"] else ""

    monkeypatch.setattr(watchdog, "_run", run)
    result = watchdog.check_and_stop(Path("/"))
    assert result["error"] == "storage_mount_missing"
    assert ["docker", "stop", "--time", "20", "a" * 64] in commands
    assert all(
        "compose" not in argv and not any("/opt/robopark" in arg for arg in argv)
        for argv in commands
    )
    assert any(
        argv[:3] == ["systemctl", "stop", "--no-block"] and "docker.socket" in argv
        for argv in commands
    )


def test_docker_failure_still_stops_daemons_and_reports_partial_shutdown(monkeypatch):
    from robopark_host import storage_watchdog as watchdog
    from robopark_host.storage_layout import StorageError

    commands = []
    monkeypatch.setattr(
        watchdog,
        "require_storage",
        lambda _root, **_kw: (_ for _ in ()).throw(StorageError("storage_read_only")),
    )

    def run(argv):
        commands.append(argv)
        if argv[0] == "docker":
            raise RuntimeError("unavailable")
        return ""

    monkeypatch.setattr(watchdog, "_run", run)
    result = watchdog.check_and_stop(Path("/"))
    assert result["shutdown"] == "incomplete"
    assert any(argv[0] == "systemctl" for argv in commands)


def test_full_disk_keeps_docker_available_for_cleanup(monkeypatch):
    from robopark_host import storage_watchdog as watchdog
    from robopark_host.storage_layout import StorageError

    def guard(_root, *, check_space=True):
        if check_space:
            raise StorageError("storage_space_low")
        return {"state": "ready"}

    monkeypatch.setattr(watchdog, "require_storage", guard)
    monkeypatch.setattr(
        watchdog, "_run", lambda _argv: pytest.fail("must allow cleanup")
    )
    assert watchdog.check_and_stop()["state"] == "ready"
