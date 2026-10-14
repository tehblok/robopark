"""Host recovery survives release switches, including a pre-policy updater."""
import runpy
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from robopark_host import resilience_install
from robopark_host.release import ReleaseError

REPO = Path(__file__).resolve().parents[2]


def payload(release):
    for relative in resilience_install.PAYLOAD:
        path = release / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n")


def test_old_release_is_noop_and_partial_new_release_fails_closed(tmp_path):
    assert not resilience_install.resilience_payload_present(tmp_path)
    payload(tmp_path)
    (tmp_path / resilience_install.PAYLOAD[-1]).unlink()
    with pytest.raises(ReleaseError, match="resilience_payload_invalid"):
        resilience_install.resilience_payload_present(tmp_path)


def test_symlink_payload_is_rejected(tmp_path):
    payload(tmp_path)
    source = tmp_path / resilience_install.PAYLOAD[-1]
    source.unlink()
    source.symlink_to(__file__)
    with pytest.raises(ReleaseError, match="resilience_payload_invalid"):
        resilience_install.resilience_payload_present(tmp_path)


def test_setup_runs_as_system_service_outside_updater_sandbox(tmp_path):
    payload(tmp_path)
    calls = []
    runner = SimpleNamespace(run=lambda command, **kwargs: calls.append(command))
    resilience_install.reconcile_host_resilience(tmp_path, runner)
    assert calls == [["systemctl", "start", "robopark-resilience-setup.service"]]


def test_installer_installs_complete_payload_but_rejects_partial_before_writes(host_paths):
    install = runpy.run_path(str(REPO / "deploy/installer/lib/install-services.py"))
    release = host_paths.releases / "candidate"
    payload(release)
    for name in install["UNITS"]:
        shutil.copyfile(REPO / "deploy/systemd" / name, release / "deploy/systemd" / name)
    tmpfiles = release / "deploy/tmpfiles.d/robopark.conf"
    tmpfiles.parent.mkdir(parents=True)
    shutil.copyfile(REPO / "deploy/tmpfiles.d/robopark.conf", tmpfiles)
    host_paths.current.symlink_to(release)
    missing = release / resilience_install.PAYLOAD[-1]
    missing.unlink()
    with pytest.raises(ValueError, match="resilience_payload_invalid"):
        install["install_units"](host_paths.root)
    assert not (host_paths.root / "etc/systemd/system/robopark.service").exists()
    missing.write_text("restored")
    install["install_units"](host_paths.root)
    for name in resilience_install.RESILIENCE_UNITS:
        assert (host_paths.root / "etc/systemd/system" / name).is_file()


def test_activation_installs_recovery_units_and_rollback_does_not_remove_them(host_paths, monkeypatch):
    from robopark_host import rollback, storage_compatibility, updater
    release = host_paths.releases / "candidate"
    payload(release)
    monkeypatch.setattr(storage_compatibility, "refresh_storage_release_guard", lambda *a, **kw: None)
    updater._activate_system_files(host_paths, release)
    for name in resilience_install.RESILIENCE_UNITS:
        assert (host_paths.root / "etc/systemd/system" / name).is_file()
    rollback.restore_units(host_paths, {"job_id": "old-release"})
    for name in resilience_install.RESILIENCE_UNITS:
        assert (host_paths.root / "etc/systemd/system" / name).is_file()


def test_local_update_bridges_old_runtime_using_published_release(host_paths):
    from robopark_ota.local_update import reconcile_published_resilience
    release = host_paths.releases / "candidate"
    payload(release)
    host_paths.current.symlink_to(release)
    calls = []
    runner = SimpleNamespace(run=lambda command, **kwargs: calls.append(command))
    assert reconcile_published_resilience(host_paths, runner) == "ready"
    assert str(release / "deploy/installer/lib/install-services.py") in calls[0]
    assert calls[1:] == [["systemctl", "daemon-reload"], ["systemctl", "start", "robopark-resilience-setup.service"]]
