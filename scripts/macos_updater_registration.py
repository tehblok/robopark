"""Generate the isolated native macOS updater re-registration job."""

from __future__ import annotations

import shlex
from pathlib import Path


UPDATER_LABEL = "ru.robopark.update-helper"
REGISTRATION_LABEL = "ru.robopark.update-registration"
SUPPORT_ROOT = Path("/Library/Application Support/RoboparkUpdater")
REGISTRATION_PLIST = Path(
    "/Library/LaunchDaemons/ru.robopark.update-registration.plist"
)
UPDATER_EXECUTABLE = Path("/Library/PrivilegedHelperTools/RoboparkUpdater")


def registration_daemon() -> dict:
    """A separate native one-shot that can wait on the updater kernel lock."""
    return {
        "Label": REGISTRATION_LABEL,
        "ProgramArguments": [str(UPDATER_EXECUTABLE), "--register-updater"],
        "UserName": "root",
        "RunAtLoad": True,
        "ProcessType": "Background",
        "StandardOutPath": str(SUPPORT_ROOT / "registration.log"),
        "StandardErrorPath": str(SUPPORT_ROOT / "registration.log"),
    }


def schedule_registration_script(
    *,
    launchctl: str | Path = "/bin/launchctl",
    registration_plist: str | Path = REGISTRATION_PLIST,
    status_path: str | Path = SUPPORT_ROOT / "registration.status",
) -> str:
    """Replace only the independent registration job; never its updater parent."""
    launch = shlex.quote(str(launchctl))
    plist = shlex.quote(str(registration_plist))
    status = shlex.quote(str(status_path))
    service = shlex.quote(f"system/{REGISTRATION_LABEL}")
    return f"""/bin/rm -f -- {status}
if {launch} print {service} >/dev/null 2>&1; then
  {launch} bootout {service}
fi
{launch} bootstrap system {plist}
"""
