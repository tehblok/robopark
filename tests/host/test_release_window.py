"""The normal OTA release window retains current and rollback only."""

from robopark_host.updater import _retained_successful_releases


def test_successful_release_window_keeps_current_and_rollback(host_paths):
    host_paths.releases.mkdir(parents=True)
    for name in ("old", "rollback", "current"):
        (host_paths.releases / name).mkdir()
    host_paths.current.symlink_to(host_paths.releases / "current")
    host_paths.previous.symlink_to(host_paths.releases / "rollback")
    receipts = [
        ("old", host_paths.state / "old.json", 3),
        ("rollback", host_paths.state / "rollback.json", 2),
        ("current", host_paths.state / "current.json", 1),
    ]

    assert _retained_successful_releases(host_paths, receipts=receipts) == {
        "current", "rollback"
    }
