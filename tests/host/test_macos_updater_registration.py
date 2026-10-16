from __future__ import annotations

import os
import subprocess
from pathlib import Path

from scripts import macos_updater_registration


def _tool(path: Path, source: str) -> Path:
    path.write_text(source)
    path.chmod(0o755)
    return path


def test_registration_job_is_fixed_native_root_one_shot():
    daemon = macos_updater_registration.registration_daemon()
    assert daemon["Label"] == "ru.robopark.update-registration"
    assert daemon["UserName"] == "root"
    assert daemon["RunAtLoad"] is True
    assert daemon["ProgramArguments"] == [
        "/Library/PrivilegedHelperTools/RoboparkUpdater",
        "--register-updater",
    ]
    assert "KeepAlive" not in daemon
    assert "StartInterval" not in daemon


def test_native_registration_delegates_lock_handoff_to_tested_coordinator():
    source = Path("apps/macos/Sources/RoboparkUpdater/main.swift").read_text()
    registration = source[source.index("private func registerUpdater()") : source.index("@main private struct")]
    assert "UpdaterRegistrationCoordinator.run" in registration
    assert "maxAttempts: 600" in registration
    assert "storage.tryAcquireLock()" in registration
    assert "registrationIsBusy" in registration


def test_postinstall_schedules_only_independent_registration_and_does_not_hide_errors(tmp_path):
    calls = tmp_path / "calls"
    launchctl = _tool(
        tmp_path / "launchctl",
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$CALLS"\n'
        'if [ "$1" = print ]; then exit 0; fi\n'
        'if [ "$1" = bootstrap ] && [ "$FAIL" = 1 ]; then exit 9; fi\n'
        "exit 0\n",
    )
    plist = tmp_path / "ru.robopark.update-registration.plist"
    old_status = tmp_path / "registration.status"
    old_status.write_text("ready\n")
    script = macos_updater_registration.schedule_registration_script(
        launchctl=launchctl, registration_plist=plist, status_path=old_status
    )
    env = {**os.environ, "CALLS": str(calls), "FAIL": "0"}
    success = subprocess.run(
        ["/bin/sh", "-eu", "-c", script], env=env, text=True, capture_output=True, check=False
    )
    assert success.returncode == 0
    assert not old_status.exists()
    assert calls.read_text().splitlines() == [
        "print system/ru.robopark.update-registration",
        "bootout system/ru.robopark.update-registration",
        f"bootstrap system {plist}",
    ]
    assert not any("ru.robopark.update-helper" in call for call in calls.read_text().splitlines())

    calls.write_text("")
    env["FAIL"] = "1"
    failure = subprocess.run(
        ["/bin/sh", "-eu", "-c", script], env=env, text=True, capture_output=True, check=False
    )
    assert failure.returncode == 9


def test_uninstaller_retires_registration_job_before_updater_and_removes_it():
    source = Path("scripts/macos_uninstall.sh").read_text()
    registration_plist = "/Library/LaunchDaemons/ru.robopark.update-registration.plist"
    updater_plist = "/Library/LaunchDaemons/ru.robopark.update-helper.plist"

    plan = subprocess.run(
        ["/bin/sh", "scripts/macos_uninstall.sh", "--plan"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    assert registration_plist in plan
    assert source.index(f"bootout system {registration_plist}") < source.index(
        f"bootout system {updater_plist}"
    )
    assert registration_plist in source[source.index("/bin/rm -f") :]
