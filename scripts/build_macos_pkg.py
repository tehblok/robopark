#!/usr/bin/env python3
"""Build the Mac application and one pkg; never install it on the build host."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import plistlib
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

try:
    from scripts import macos_keychain_broker
    from scripts.macos_signing import ensure_test_signing_identity, isolated_test_signer
except ModuleNotFoundError:  # Direct execution: scripts/ is sys.path[0].
    import macos_keychain_broker
    from macos_signing import ensure_test_signing_identity, isolated_test_signer

ROOT = Path(__file__).resolve().parents[1]


def sign_application_target(
    target: Path,
    *,
    identifier: str,
    developer_identity: str | None,
    test_secret_dir: Path,
) -> str | None:
    """Sign one TEST artifact and return its public certificate fingerprint."""
    if developer_identity:
        subprocess.run(
            [
                "codesign", "--force", "--sign", developer_identity, "--options",
                "runtime", "--timestamp", "--identifier", identifier, str(target),
            ],
            check=True,
        )
        subprocess.run(["codesign", "--verify", "--strict", str(target)], check=True)
        return None
    pin = json.loads((ROOT / "deploy/macos-test-signing.json").read_text())["certificate_sha256"]
    if not isinstance(pin, str) or not re.fullmatch(r"[0-9a-f]{64}", pin):
        raise ValueError("invalid_test_signing_pin")
    identity = ensure_test_signing_identity(test_secret_dir, expected_fingerprint=pin)
    with isolated_test_signer(identity) as signer:
        signer.sign(target, identifier=identifier)
    return identity.fingerprint


def package_name(version: str) -> str:
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+.][A-Za-z0-9.]+)?", version):
        raise ValueError("invalid_version")
    return f"Robopark-{version}-arm64.pkg"


def _key(path: Path) -> Ed25519PrivateKey:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError("update_key_requires_private_regular_file")
    return Ed25519PrivateKey.from_private_bytes(path.read_bytes())


def ensure_update_key(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.exists():
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(
                Ed25519PrivateKey.generate().private_bytes(
                    Encoding.Raw, PrivateFormat.Raw, NoEncryption()
                )
            )
    public = _key(path).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(public).decode("ascii")


def sign_catalog(
    package: Path, version: str, key: Path, *, generation: int, now: int
) -> dict[str, str]:
    if package.name != package_name(version) or generation <= 0:
        raise ValueError("invalid_package_identity")
    data = package.read_bytes()
    catalog = {
        "schema": 1,
        "generation": generation,
        "expires": now + 14 * 86400,
        "channels": {
            "rc": {
                "version": version,
                "url": f"/api/distribution/v1/macos/{package.name}",
                "sha256": hashlib.sha256(data).hexdigest(),
                "length": len(data),
                "architecture": "arm64",
                "protocol": 1,
                "notes": "Офлайн-рабочее место, автоматическое подключение Mac, распределение Emergency и сжатие трафика.",
            }
        },
    }
    payload = json.dumps(
        catalog, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return {
        "payload": base64.b64encode(payload).decode("ascii"),
        "signature": base64.b64encode(_key(key).sign(payload)).decode("ascii"),
    }


def bundle_versions(version: str, now: int) -> dict[str, str]:
    package_name(version)
    date = datetime.fromtimestamp(now, timezone.utc)
    days = (
        date.date() - datetime(2020, 1, 1, tzinfo=timezone.utc).date()
    ).days + 1
    if not 0 < days <= 9999:
        raise ValueError("bundle_build_date_out_of_range")
    return {
        "CFBundleShortVersionString": version.split("-")[0].split("+")[0],
        "CFBundleVersion": f"{days}.{date.hour}.{date.minute}",
        "RoboparkReleaseVersion": version,
    }


def launch_agent() -> dict:
    return {
        "Label": "ru.robopark.agent",
        "ProgramArguments": [
            "/Applications/Robopark.app/Contents/MacOS/RoboparkAgent",
            "--background",
        ],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 30,
        "LimitLoadToSessionType": "Aqua",
        "ProcessType": "Background",
    }


def update_daemon() -> dict:
    return {
        "Label": "ru.robopark.update-helper",
        "ProgramArguments": ["/Library/PrivilegedHelperTools/RoboparkUpdater"],
        "UserName": "root",
        "RunAtLoad": True,
        "StartInterval": 15 * 60,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
    }


def automatic_update_config(envelope: dict, public_key: str) -> dict:
    try:
        if set(envelope) != {"payload", "signature"}:
            raise ValueError
        payload = base64.b64decode(envelope["payload"], validate=True)
        signature = base64.b64decode(envelope["signature"], validate=True)
        Ed25519PublicKey.from_public_bytes(
            base64.b64decode(public_key, validate=True)
        ).verify(signature, payload)
        profile = json.loads(payload)
        if (
            profile.get("purpose") != "robopark-deployment-v1"
            or profile.get("origin") != "https://roboparktest.ru.tuna.am"
            or profile.get("instance_id") != "d6ed3b43-c41b-4614-833e-c5ec30936ef3"
            or profile.get("environment") != "test"
            or profile.get("channel") != "rc"
            or not re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
                profile.get("instance_id", ""),
                re.IGNORECASE,
            )
            or type(profile.get("expires")) is not int
            or profile["expires"] <= int(time.time())
        ):
            raise ValueError
    except (
        ValueError,
        TypeError,
        KeyError,
        InvalidSignature,
        json.JSONDecodeError,
    ) as error:
        raise ValueError("invalid_deployment_profile") from error
    return {
        "schema": 1,
        "origin": profile["origin"],
        "instance_id": profile["instance_id"],
        "environment": profile["environment"],
        "channel": profile["channel"],
    }


def launch_agent_registration_script(
    *,
    launchctl: str | Path = "/bin/launchctl",
    plist: str | Path = "/Library/LaunchAgents/ru.robopark.agent.plist",
) -> str:
    command = shlex.quote(str(launchctl))
    plist_path = shlex.quote(str(plist))
    service = '"gui/$session_uid/ru.robopark.agent"'
    return f"""{command} bootout {service} 2>/dev/null || true
{command} bootstrap "gui/$session_uid" {plist_path}
{command} kickstart {service}
"""


def launch_agent_update_script(*, launchctl: str | Path = "/bin/launchctl",
                               plist: str | Path = "/Library/LaunchAgents/ru.robopark.agent.plist",
                               process_list: str | Path = "/bin/ps",
                               terminate: str | Path = "/bin/kill",
                               pause: str | Path = "/bin/sleep") -> str:
    command = shlex.quote(str(launchctl))
    ps, kill, sleep = (shlex.quote(str(p)) for p in (process_list, terminate, pause))
    service = '"gui/$session_uid/ru.robopark.agent"'
    restart = launch_agent_registration_script(launchctl=launchctl, plist=plist)
    stop_manual = f"""# Match UID and full executable path, never a process-name search.
for agent_pid in $agent_pids; do
  current=$({ps} -p "$agent_pid" -o uid=,comm= 2>/dev/null || true)
  if /usr/bin/printf '%s\\n' "$current" | /usr/bin/awk -v owner="$session_uid" '$1 == owner && $2 == "/Applications/Robopark.app/Contents/MacOS/RoboparkAgent" {{found=1}} END {{exit !found}}'; then
    {kill} -TERM "$agent_pid" 2>/dev/null || true
  fi
done
attempt=0
while :; do
  live=0
  for agent_pid in $agent_pids; do
    current=$({ps} -p "$agent_pid" -o uid=,comm= 2>/dev/null || true)
    if /usr/bin/printf '%s\\n' "$current" | /usr/bin/awk -v owner="$session_uid" '$1 == owner && $2 == "/Applications/Robopark.app/Contents/MacOS/RoboparkAgent" {{found=1}} END {{exit !found}}'; then live=1; fi
  done
  [ "$live" = 1 ] || break
  [ "$attempt" -lt 50 ] || {{ echo 'Robopark did not finish stopping; update restart stopped safely.' >&2; exit 1; }}
  {sleep} 0.1
  attempt=$((attempt + 1))
done
"""
    restart = restart.replace(f"{command} bootstrap", stop_manual + f"{command} bootstrap", 1)
    return f"""processes=$({ps} -axo pid=,uid=,comm=)
agent_pids=$(/usr/bin/printf '%s\\n' "$processes" | /usr/bin/awk -v owner="$session_uid" '$2 == owner && $3 == "/Applications/Robopark.app/Contents/MacOS/RoboparkAgent" {{print $1}}')
disabled=$({command} print-disabled "gui/$session_uid" 2>/dev/null || true)
if /usr/bin/grep -q '"ru.robopark.agent" => true' <<EOF
$disabled
EOF
then
  # Disabling login autostart intentionally leaves an already running app alive.
  # Restart that instance after replacement, then restore the saved preference.
  if [ -n "$agent_pids" ] || {command} print {service} 2>/dev/null | /usr/bin/grep -q 'pid = [0-9]'; then
    trap '{command} disable {service}' EXIT
    trap 'exit 130' HUP INT TERM
    {command} enable {service}
{restart.rstrip()}
    {command} disable {service}
    trap - EXIT HUP INT TERM
  else
    # Remove obsolete launch constraints without starting an inactive app.
    {command} bootout {service} 2>/dev/null || true
  fi
else
{restart.rstrip()}
fi
"""


def postinstall_script() -> str:
    script = """#!/bin/sh
set -eu
chown -R root:wheel /Applications/Robopark.app
chmod -R go-w /Applications/Robopark.app
chown root:wheel /Library/LaunchAgents/ru.robopark.agent.plist
chmod 644 /Library/LaunchAgents/ru.robopark.agent.plist
chown root:wheel /Library/LaunchDaemons/ru.robopark.update-helper.plist
chmod 644 /Library/LaunchDaemons/ru.robopark.update-helper.plist
chown root:wheel /Library/PrivilegedHelperTools/RoboparkUpdater
chmod 755 /Library/PrivilegedHelperTools/RoboparkUpdater
chown root:wheel '/Library/Application Support/RoboparkUpdater'
chmod 755 '/Library/Application Support/RoboparkUpdater'
chown root:wheel '/Library/Application Support/RoboparkUpdater/config.json' '/Library/Application Support/RoboparkUpdater/update-root.json'
chmod 644 '/Library/Application Support/RoboparkUpdater/config.json' '/Library/Application Support/RoboparkUpdater/update-root.json'
__ROBOPARK_BROKER_PERMISSIONS__
/bin/launchctl bootstrap system /Library/LaunchDaemons/ru.robopark.update-helper.plist 2>/dev/null || true
install_lock='/Library/Application Support/RoboparkUpdater/install.lock'
if [ -f "$install_lock/state" ] && [ ! -L "$install_lock/state" ] && [ "$(/usr/bin/stat -f %u "$install_lock/state")" = 0 ]; then
  mode=$(/usr/bin/awk -F= '$1 == "mode" { print $2 }' "$install_lock/state")
  case "$mode" in auto|package) /bin/rm -f "$install_lock/state"; /bin/rmdir "$install_lock" 2>/dev/null || true;; esac
fi
session_uid=$(/usr/bin/stat -f %u /dev/console)
case "$session_uid" in ''|*[!0-9]*) exit 0;; esac
if [ "$session_uid" -ge 501 ]; then
__ROBOPARK_BROKER_REGISTRATION__
__ROBOPARK_LAUNCH_AGENT_REGISTRATION__
fi
"""
    return script.replace("__ROBOPARK_BROKER_PERMISSIONS__", macos_keychain_broker.postinstall_permissions().rstrip()).replace(
        "__ROBOPARK_BROKER_REGISTRATION__", macos_keychain_broker.register_script().rstrip()
    ).replace(
        "__ROBOPARK_LAUNCH_AGENT_REGISTRATION__",
        launch_agent_update_script().rstrip(),
    )


def stage_workspace(dist: Path, target: Path, *, version: str, commit: str) -> dict:
    """Copy only the bounded public web build; private source/config never enters pkg."""
    if not dist.is_dir() or dist.is_symlink():
        raise ValueError("workspace_build_missing")
    files = sorted(dist.rglob("*"))
    if any(path.is_symlink() for path in files):
        raise ValueError("workspace_symlink")
    index = dist / "index.html"
    if not index.is_file():
        raise ValueError("workspace_index_missing")
    entries = re.findall(r'(?:src|href)=["\'](/assets/[^"\']+)["\']', index.read_text())
    for entry in entries:
        parts = Path(entry.removeprefix("/")).parts
        if ".." in parts or not (dist / entry.removeprefix("/")).is_file():
            raise ValueError("workspace_entry_asset_missing")
    allowed = {
        ".html",
        ".js",
        ".css",
        ".json",
        ".svg",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".ico",
        ".woff",
        ".woff2",
        ".webmanifest",
        ".wasm",
    }
    assets = [
        path
        for path in files
        if path.is_file()
        and path.suffix in allowed
        and not any(part.startswith(".") for part in path.relative_to(dist).parts)
    ]
    if (
        len(assets) > 4096
        or sum(path.stat().st_size for path in assets) > 128 * 1024 * 1024
    ):
        raise ValueError("workspace_build_too_large")
    manifest = {"version": version, "source_commit": commit, "files": {}}
    target.mkdir(parents=True)
    for path in assets:
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("workspace_asset_too_large")
        relative = path.relative_to(dist)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        data = path.read_bytes()
        destination.write_bytes(data)
        manifest["files"][relative.as_posix()] = {
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    (target / "workspace-manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n"
    )
    return manifest


def build(
    output: Path,
    *,
    key: Path,
    deployment_profile: Path,
    sign_application: str | None = None,
    sign_installer: str | None = None,
    notary_profile: str | None = None,
    test_signing_dir: Path = ROOT / ".release-secrets/macos-test-signing",
) -> dict:
    version = (ROOT / "VERSION").read_text().strip()
    filename = package_name(version)
    source_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    source_clean = not subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=ROOT,
        text=True,
    ).strip()
    if bool(sign_application) != bool(sign_installer) or (
        notary_profile and not sign_installer
    ):
        raise ValueError("provide_both_developer_id_identities")
    if sign_application:
        raise ValueError("developer_id_requires_explicit_keychain_broker_identity_migration")
    macos_keychain_broker.verify_frozen()
    output.mkdir(parents=True, exist_ok=True)
    public = ensure_update_key(key)
    try:
        profile_envelope = json.loads(deployment_profile.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("invalid_deployment_profile") from error
    updater_config = automatic_update_config(profile_envelope, public)
    web = ROOT / "apps/web"
    if json.loads((web / "package.json").read_text())["version"] != version:
        raise ValueError("workspace_version_mismatch")
    subprocess.run(["npm", "run", "build"], cwd=web, check=True)
    scratch = Path(tempfile.gettempdir()) / "robopark-macos-release-build"
    swift_options = [
        "--package-path",
        str(ROOT / "apps/macos"),
        "--scratch-path",
        str(scratch),
        "--cache-path",
        str(scratch / "cache"),
        "--config-path",
        str(scratch / "config"),
        "--security-path",
        str(scratch / "security"),
        "--disable-sandbox",
        "--configuration",
        "release",
        "--arch",
        "arm64",
    ]
    env = dict(
        os.environ,
        CLANG_MODULE_CACHE_PATH=str(scratch / "clang"),
        SWIFT_MODULECACHE_PATH=str(scratch / "swift"),
    )
    subprocess.run(
        ["swift", "build", *swift_options, "--product", "RoboparkAgent"],
        env=env,
        check=True,
    )
    subprocess.run(
        ["swift", "build", *swift_options, "--product", "RoboparkUpdater"],
        env=env,
        check=True,
    )
    subprocess.run(
        ["swift", "build", *swift_options, "--product", "RoboparkComputeWorker"],
        env=env,
        check=True,
    )
    bin_path = Path(
        subprocess.check_output(
            ["swift", "build", *swift_options, "--show-bin-path"], env=env, text=True
        ).strip()
    )
    with tempfile.TemporaryDirectory(prefix="robopark-pkg-") as temporary:
        work = Path(temporary)
        payload = work / "payload"
        app = payload / "Applications/Robopark.app"
        resources = app / "Contents/Resources"
        executable = app / "Contents/MacOS/RoboparkAgent"
        executable.parent.mkdir(parents=True)
        resources.mkdir()
        shutil.copy2(bin_path / "RoboparkAgent", executable)
        executable.chmod(0o755)
        architecture = subprocess.check_output(
            ["lipo", "-archs", str(executable)], text=True
        ).strip()
        if architecture != "arm64":
            raise ValueError("unexpected_binary_architecture")
        subprocess.run(["strip", "-S", str(executable)], check=True)
        shutil.copy2(ROOT / "apps/macos/Resources/panel.html", resources / "panel.html")
        shutil.copy2(ROOT / "apps/macos/Resources/compute-peer.html", resources / "compute-peer.html")
        shutil.copy2(ROOT / "apps/macos/Resources/corporate-oauth.js", resources / "corporate-oauth.js")
        shutil.copy2(ROOT / "scripts/macos_uninstall.sh", resources / "uninstall.command")
        (resources / "uninstall.command").chmod(0o755)
        compute_worker = executable.parent / "RoboparkComputeWorker"
        shutil.copy2(bin_path / "RoboparkComputeWorker", compute_worker)
        compute_worker.chmod(0o755)
        if subprocess.check_output(["lipo", "-archs", str(compute_worker)], text=True).strip() != "arm64":
            raise ValueError("unexpected_compute_worker_architecture")
        subprocess.run(["strip", "-S", str(compute_worker)], check=True)
        workspace = stage_workspace(
            web / "dist", resources / "workspace", version=version, commit=source_commit
        )
        (resources / "update-root.json").write_text(
            json.dumps({"ed25519": public}) + "\n"
        )
        subprocess.run(
            [
                "openssl",
                "x509",
                "-in",
                str(
                    ROOT
                    / "apps/api/src/robopark_api/certificates/YandexInternalRootCA.crt"
                ),
                "-outform",
                "DER",
                "-out",
                str(resources / "YandexInternalRootCA.der"),
            ],
            check=True,
        )
        info = {
            "CFBundleIdentifier": "ru.robopark.agent",
            "RoboparkSourceCommit": source_commit,
            "RoboparkSourceClean": source_clean,
            "CFBundleName": "Robopark",
            "CFBundleDisplayName": "Robopark",
            "CFBundleExecutable": "RoboparkAgent",
            "CFBundlePackageType": "APPL",
            "NSLocalNetworkUsageDescription": "Robopark использует локальную сеть для прямой обработки изображений на этом Mac.",
            **bundle_versions(version, int(time.time())),
            "LSMinimumSystemVersion": "13.0",
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
            "NSCameraUsageDescription": "Сканирование QR-кодов роботов и добавление фотографий к рабочим задачам.",
            "NSMicrophoneUsageDescription": "Запись голосовых вложений по вашему запросу.",
            "RoboparkWorkspaceVersion": version,
        }
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
        launch_dir = payload / "Library/LaunchAgents"
        launch_dir.mkdir(parents=True)
        (launch_dir / "ru.robopark.agent.plist").write_bytes(
            plistlib.dumps(launch_agent())
        )
        # This signed component owns login-Keychain entries. Never rebuild,
        # strip or re-sign it during an ordinary application release.
        broker_manifest = macos_keychain_broker.stage_frozen(payload)
        (launch_dir / f"{macos_keychain_broker.IDENTIFIER}.plist").write_bytes(
            plistlib.dumps(macos_keychain_broker.launch_agent())
        )
        test_signing_fingerprint = sign_application_target(
            compute_worker,
            identifier="ru.robopark.agent.compute",
            developer_identity=sign_application,
            test_secret_dir=test_signing_dir,
        )
        helper_dir = payload / "Library/PrivilegedHelperTools"
        helper_dir.mkdir(parents=True)
        helper = helper_dir / "RoboparkUpdater"
        shutil.copy2(bin_path / "RoboparkUpdater", helper)
        helper.chmod(0o755)
        subprocess.run(["strip", "-S", str(helper)], check=True)
        helper_test_fingerprint = sign_application_target(
            helper,
            identifier="ru.robopark.agent.updater",
            developer_identity=sign_application,
            test_secret_dir=test_signing_dir,
        )
        if helper_test_fingerprint != test_signing_fingerprint:
            raise ValueError("inconsistent_test_signing_identity")
        daemon_dir = payload / "Library/LaunchDaemons"
        daemon_dir.mkdir(parents=True)
        (daemon_dir / "ru.robopark.update-helper.plist").write_bytes(
            plistlib.dumps(update_daemon())
        )
        helper_support = payload / "Library/Application Support/RoboparkUpdater"
        helper_support.mkdir(parents=True)
        (helper_support / "config.json").write_text(
            json.dumps(updater_config, sort_keys=True, separators=(",", ":")) + "\n"
        )
        (helper_support / "update-root.json").write_text(
            json.dumps({"ed25519": public}) + "\n"
        )
        numeric = info["CFBundleVersion"]
        release_version = info["RoboparkReleaseVersion"]
        scripts = work / "scripts"
        scripts.mkdir()
        (scripts / "preinstall").write_text(f"""#!/bin/sh
set -eu
{macos_keychain_broker.preinstall_script(broker_manifest['sha256'])}
support='/Library/Application Support/RoboparkUpdater'
install_lock="$support/install.lock"
created_lock=0
cleanup_lock() {{
  status=$?
  trap - EXIT HUP INT TERM
  if [ "$status" -ne 0 ] && [ "$created_lock" -eq 1 ]; then
    /bin/rm -f "$install_lock/state"
    /bin/rmdir "$install_lock" 2>/dev/null || true
  fi
  exit "$status"
}}
trap cleanup_lock EXIT HUP INT TERM
if [ ! -e "$support" ]; then
  /bin/mkdir "$support"
  /usr/sbin/chown root:wheel "$support"
  /bin/chmod 755 "$support"
fi
if [ -L "$support" ] || [ "$(/usr/bin/stat -f %u "$support")" != 0 ] || [ $((8#$(/usr/bin/stat -f %Lp "$support") & 8#022)) -ne 0 ]; then
  echo 'Robopark updater support directory is unsafe.' >&2
  exit 1
fi
auto_lock=0
if [ -e "$install_lock" ]; then
  if [ -L "$install_lock" ] || [ "$(/usr/bin/stat -f %u "$install_lock")" != 0 ]; then
    echo 'Robopark installation lock is unsafe.' >&2
    exit 1
  fi
  lock_state="$install_lock/state"
  package_path=${{PACKAGE_PATH:-}}
  if [ "$package_path" = "$support/verified-update.pkg" ] && [ -f "$package_path" ] && [ ! -L "$package_path" ] && \
     [ "$(/usr/bin/stat -f %u "$package_path")" = 0 ] && [ "$(/usr/bin/stat -f %g "$package_path")" = 0 ] && \
     [ $((8#$(/usr/bin/stat -f %Lp "$package_path") & 8#077)) -eq 0 ] && \
     [ -f "$lock_state" ] && [ ! -L "$lock_state" ] && \
     [ "$(/usr/bin/stat -f %u "$lock_state")" = 0 ] && [ $((8#$(/usr/bin/stat -f %Lp "$lock_state") & 8#077)) -eq 0 ]; then
    lock_mode=$(/usr/bin/awk -F= '$1 == "mode" {{ print $2 }}' "$lock_state")
    helper_pid=$(/usr/bin/awk -F= '$1 == "helper_pid" {{ print $2 }}' "$lock_state")
    case "$helper_pid" in ''|*[!0-9]*) helper_pid=0;; esac
    if [ "$lock_mode" = auto ] && [ "$(/bin/ps -p "$helper_pid" -o comm= 2>/dev/null | /usr/bin/xargs)" = '/Library/PrivilegedHelperTools/RoboparkUpdater' ]; then
      auto_lock=1
    fi
  fi
  if [ "$auto_lock" -eq 1 ]; then :
  else
  now=$(/bin/date +%s); lock_time=$(/usr/bin/stat -f %m "$install_lock")
  installer_count=$(/bin/ps -axo comm= | /usr/bin/grep -Fxc '/usr/sbin/installer' || true)
  if [ $((now - lock_time)) -lt 600 ] || [ "$installer_count" -gt 1 ]; then
    echo 'Another Robopark installation is active; retry after it completes.' >&2
    exit 1
  fi
  /bin/rm -f "$install_lock/state"
  /bin/rmdir "$install_lock" 2>/dev/null || {{ echo 'Robopark installation lock cannot be recovered safely.' >&2; exit 1; }}
  fi
fi
if [ "$auto_lock" -eq 0 ]; then
  /bin/mkdir -m 700 "$install_lock"
  created_lock=1
  /usr/bin/printf '%s\n' 'mode=package' 'version={release_version}' > "$install_lock/state"
  /usr/sbin/chown root:wheel "$install_lock" "$install_lock/state"
  /bin/chmod 700 "$install_lock"
  /bin/chmod 600 "$install_lock/state"
fi
if [ -L /Applications/Robopark.app ]; then
  echo 'Refusing a symbolic-link application destination.' >&2
  exit 1
fi
if [ -e '/Library/Application Support/RoboparkUpdater/retiring' ]; then
  echo 'Robopark is being removed; retry installation after removal completes.' >&2
  exit 1
fi
if [ -e "$support/lifecycle.lock" ]; then
  if [ "$auto_lock" -ne 1 ]; then
    echo 'Another Robopark installation is active; retry after it completes.' >&2
    exit 1
  fi
  authorized=0; tries=0
  while [ "$tries" -lt 20 ]; do
    state="$support/lifecycle.lock/state.json"
    if [ -f "$state" ] && [ ! -L "$state" ] && [ "$(/usr/bin/stat -f %u "$state")" = 0 ] && [ $((8#$(/usr/bin/stat -f %Lp "$state") & 8#077)) -eq 0 ]; then
      installer_pid=$(/usr/bin/plutil -extract installer_pid raw -o - "$state" 2>/dev/null || true)
      case "$installer_pid" in
        ''|*[!0-9]*) ;;
        *) [ "$(/bin/ps -p "$installer_pid" -o comm= 2>/dev/null | /usr/bin/xargs)" = '/usr/sbin/installer' ] && authorized=1 && break ;;
      esac
    fi
    tries=$((tries + 1)); /bin/sleep 0.5
  done
  if [ "$authorized" -ne 1 ]; then
    echo 'Another Robopark installation is active; retry after it completes.' >&2
    exit 1
  fi
fi
installed=''
# A missing bundle is a fresh installation. PlistBuddy's missing-file message
# goes to stdout, so do not invoke it for an absent app or ignore read failures.
if [ -e /Applications/Robopark.app ] || [ -L /Applications/Robopark.app ]; then
  if [ -L /Applications/Robopark.app ] || [ ! -d /Applications/Robopark.app/Contents ] || [ -L /Applications/Robopark.app/Contents ] || [ ! -f /Applications/Robopark.app/Contents/Info.plist ] || [ -L /Applications/Robopark.app/Contents/Info.plist ]; then
    echo 'Existing Robopark bundle is invalid; refusing replacement.' >&2; exit 1
  fi
  if ! installed=$(/usr/libexec/PlistBuddy -c 'Print :RoboparkReleaseVersion' /Applications/Robopark.app/Contents/Info.plist 2>/dev/null) || [ -z "$installed" ]; then
    echo 'Existing Robopark version cannot be read; refusing replacement.' >&2; exit 1
  fi
fi
case "$installed" in
  '') ;;
  *)
    if ! /usr/bin/printf '%s\n' "$installed" | /usr/bin/grep -Eq '^[0-9]+\\.[0-9]+\\.[0-9]+(-rc\\.[0-9]+)?$'; then
      echo 'Existing Robopark version is invalid; refusing replacement.' >&2; exit 1
    fi
    old_base=${{installed%%-*}}; old_suffix=${{installed#"$old_base"}}
    new='{release_version}'; new_base=${{new%%-*}}; new_suffix=${{new#"$new_base"}}
    old_ifs=$IFS; IFS=.; set -- $old_base; IFS=$old_ifs; old_a=$1; old_b=$2; old_c=$3
    old_ifs=$IFS; IFS=.; set -- $new_base; IFS=$old_ifs; new_a=$1; new_b=$2; new_c=$3
    old_stage=1; old_rc=0; new_stage=1; new_rc=0
    if [ -n "$old_suffix" ]; then old_stage=0; old_rc=${{old_suffix#-rc.}}; fi
    if [ -n "$new_suffix" ]; then new_stage=0; new_rc=${{new_suffix#-rc.}}; fi
    if [ "$old_a" -gt "$new_a" ] || {{ [ "$old_a" -eq "$new_a" ] && [ "$old_b" -gt "$new_b" ]; }} || \
       {{ [ "$old_a" -eq "$new_a" ] && [ "$old_b" -eq "$new_b" ] && [ "$old_c" -gt "$new_c" ]; }} || \
       {{ [ "$old_a" -eq "$new_a" ] && [ "$old_b" -eq "$new_b" ] && [ "$old_c" -eq "$new_c" ] && [ "$old_stage" -gt "$new_stage" ]; }} || \
       {{ [ "$old_a" -eq "$new_a" ] && [ "$old_b" -eq "$new_b" ] && [ "$old_c" -eq "$new_c" ] && [ "$old_stage" -eq "$new_stage" ] && [ "$old_rc" -gt "$new_rc" ]; }}; then
      echo 'Refusing to downgrade Robopark.' >&2
      exit 1
    fi
    ;;
esac
""")
        (scripts / "postinstall").write_text(postinstall_script())
        for script in scripts.iterdir():
            script.chmod(0o755)
            subprocess.run(["sh", "-n", str(script)], check=True)
        app_test_fingerprint = sign_application_target(
            app,
            identifier="ru.robopark.agent",
            developer_identity=sign_application,
            test_secret_dir=test_signing_dir,
        )
        if app_test_fingerprint != test_signing_fingerprint:
            raise ValueError("inconsistent_test_signing_identity")
        if broker_manifest["certificate_sha256"] != app_test_fingerprint:
            raise ValueError("broker_application_identity_mismatch")
        subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", str(app)], check=True
        )
        # Check runtime resource resolution in an actual .app, not only the
        # asset inventory: Bundle.main URLs can retain relative base URLs.
        subprocess.run(
            [sys.executable, str(ROOT / "scripts/check_macos_workspace_server.py"), "--app", str(app)],
            check=True,
        )
        # .pkg version is numeric; the app and signed catalog retain full semver.
        package = output / filename
        command = [
            "pkgbuild",
            "--root",
            str(payload),
            "--identifier",
            "ru.robopark.agent",
            "--version",
            numeric,
            "--ownership",
            "recommended",
            "--scripts",
            str(scripts),
            "--install-location",
            "/",
        ]
        if sign_installer:
            command += ["--sign", sign_installer]
        subprocess.run([*command, str(package)], check=True)
        if notary_profile:
            subprocess.run(
                [
                    "xcrun",
                    "notarytool",
                    "submit",
                    str(package),
                    "--keychain-profile",
                    notary_profile,
                    "--wait",
                ],
                check=True,
            )
            subprocess.run(["xcrun", "stapler", "staple", str(package)], check=True)
        subprocess.run([str(executable), "--version"], check=True)
    now = int(time.time())
    catalog = sign_catalog(package, version, key, generation=time.time_ns(), now=now)
    (output / "catalog.json").write_text(json.dumps(catalog, indent=2) + "\n")
    (output / "update-root.json").write_text(json.dumps({"ed25519": public}) + "\n")
    report = {
        "workspace_runtime_checked": True,
        "version": version,
        "package": package.name,
        "sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
        "bytes": package.stat().st_size,
        "architecture": "arm64",
        "minimum_macos": "13.0",
        "developer_id_signed": bool(sign_application and sign_installer),
        "stable_test_identity_signed": bool(test_signing_fingerprint),
        "test_signing_certificate_sha256": test_signing_fingerprint,
        "notarized": bool(notary_profile),
        "metadata_signed": True,
        "physical_install_tested": False,
        "source_commit": source_commit,
        "source_clean": source_clean,
        "workspace_assets": len(workspace["files"]),
        "workspace_bytes": sum(row["size"] for row in workspace["files"].values()),
    }
    final_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    final_clean = not subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=ROOT,
        text=True,
    ).strip()
    if final_commit != source_commit or (source_clean and not final_clean):
        package.unlink(missing_ok=True)
        (output / "catalog.json").unlink(missing_ok=True)
        raise ValueError("source_changed_during_macos_build")
    (output / "macos-build.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--update-key", type=Path, default=ROOT / ".release-secrets/macos-update.key"
    )
    parser.add_argument("--deployment-profile", type=Path, required=True)
    parser.add_argument("--sign-application")
    parser.add_argument("--sign-installer")
    parser.add_argument("--notary-profile")
    parser.add_argument(
        "--test-signing-dir",
        type=Path,
        default=ROOT / ".release-secrets/macos-test-signing",
    )
    args = parser.parse_args()
    report = build(
        args.output.resolve(),
        key=args.update_key.resolve(),
        deployment_profile=args.deployment_profile.resolve(),
        sign_application=args.sign_application,
        sign_installer=args.sign_installer,
        notary_profile=args.notary_profile,
        test_signing_dir=args.test_signing_dir.resolve(),
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
